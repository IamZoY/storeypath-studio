"""Item types' 3D models (format 0.9.2; spec/FORMAT.md, Models): a supplier's model of
a sofa or a desk, shown in 3D in place of the shape its type is drawn as.

A model comes as binary glTF (.glb) and is read here whole: its nodes where they place
its meshes, its materials' colours (a texture by its average colour), its vertices'
colours. It is read in metres (one whose size says centimetres or millimetres, as read
so), turned a quarter at a time when its front faces another way than glTF's +Z,
simplified to MODEL_TRIANGLES at most, and written again as the small file a package
carries: one mesh in the type's own frame (its middle at x = z = 0, its bottom at y = 0,
its front +Z), each vertex its colour and its finish, no textures. The file as it came is
kept, so the model can be made again (turned another way).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import struct
from dataclasses import dataclass

import numpy as np

MAX_FILE = 64 << 20  # a model as it comes: at most 64 MB
MAX_SOURCE_TRIANGLES = 4_000_000
MODEL_TRIANGLES = 5000  # the model Studio makes: at most this many triangles
MAX_TRIANGLES = 20_000  # a model a package carries: at most this many (FORMAT.md, Models)
TURNS = (0, 90, 180, 270)  # counter-clockwise, seen from above
TOO_BIG_M = 30.0  # no piece of furniture is this large: a model this size is in centimetres (or millimetres)
UNITS = (("m", 1.0), ("cm", 0.01), ("mm", 0.001))

_MAGIC, _JSON, _BIN = b"glTF", 0x4E4F534A, 0x004E4942
_COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
# extensions a file may require that change nothing read here (its geometry, its colours)
_HARMLESS = {"KHR_mesh_quantization", "KHR_texture_transform", "KHR_texture_basisu", "EXT_texture_webp",
             "KHR_materials_unlit", "KHR_materials_emissive_strength", "KHR_materials_specular", "KHR_materials_ior",
             "KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_clearcoat", "KHR_materials_sheen",
             "KHR_materials_variants", "KHR_materials_pbrSpecularGlossiness"}
_COMPRESSED = {"KHR_draco_mesh_compression": "Draco", "EXT_meshopt_compression": "meshopt",
               "KHR_meshopt_compression": "meshopt"}
PLAIN = (0.9, 0.0)  # roughness, metalness of a part with no material


class ModelError(ValueError):
    """A file that is not a model this reads, saying why (and what to do)."""


@dataclass
class Model:
    """A model made from a file: ``glb`` the file a package carries; ``size`` its width
    (x), depth (z) and height (y) in metres; how many triangles it has and had; the
    units it was read in."""

    glb: bytes
    size: tuple[float, float, float]
    triangles: int
    source_triangles: int
    units: str


def make_model(data: bytes, code: str, name: str = "", turn: int = 0, generator: str = "StoreyPath Studio") -> Model:
    """The model of the type ``code`` from a .glb's bytes (``name``: the file's),
    ``turn``-ed (TURNS). Raises ModelError for a file it cannot read."""
    if turn not in TURNS:
        raise ModelError(f"a model is turned {', '.join(map(str, TURNS))} degrees")
    if len(data) > MAX_FILE:
        raise ModelError(f"the file is {len(data) >> 20} MB: a model is at most {MAX_FILE >> 20} MB")
    gltf, binary = read_glb(data)
    try:
        tri, colour, finish = _triangles(gltf, binary)
    except ModelError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
        raise ModelError(f"the file is not glTF as glTF 2.0 says ({type(e).__name__}: {e})") from None
    if not len(tri):
        raise ModelError("the file has no triangles: no mesh, or only lines and points")
    tri, units = _in_metres(tri)
    tri = _turned(tri, turn)
    lo, hi = tri.reshape(-1, 3).min(axis=0), tri.reshape(-1, 3).max(axis=0)
    tri = tri - np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    if min(hi - lo) < 0.005:
        raise ModelError("the model is flat (under 5 mm one way): a sheet, not a thing that stands in a room")
    groups, group_of = _groups(colour, finish)
    positions, vgroup, index = simplify(tri, group_of, MODEL_TRIANGLES)
    extras = {"code": code, "source": name, "source_sha256": hashlib.sha256(data).hexdigest(),
              "source_triangles": int(len(tri)), "turn": turn}
    glb = write_glb(positions, groups[vgroup, :3], groups[vgroup, 3:], index, generator, extras)
    w, h, d = (hi - lo).tolist()
    return Model(glb=glb, size=(round(w, 3), round(d, 3), round(h, 3)), triangles=int(len(index)),
                 source_triangles=int(len(tri)), units=units)


# ---- reading glTF ----------------------------------------------------------------------

def read_glb(data: bytes) -> tuple[dict, bytes]:
    """A binary glTF file's JSON and its binary chunk (b"" when it has none)."""
    if len(data) < 20 or data[:4] != _MAGIC:
        raise ModelError("not a binary glTF file (.glb): export the model as .glb (glTF binary)")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ModelError(f"glTF {version}: a model is glTF 2.0")
    if length > len(data):
        raise ModelError("the file is cut short")
    gltf, binary, at = None, b"", 12
    while at + 8 <= length:
        size, kind = struct.unpack_from("<II", data, at)
        chunk = data[at + 8:at + 8 + size]
        if len(chunk) < size:
            raise ModelError("the file is cut short")
        if kind == _JSON and gltf is None:
            try:
                gltf = json.loads(chunk.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                raise ModelError(f"the file's glTF is not JSON: {e}") from None
        elif kind == _BIN and not binary:
            binary = chunk
        at += 8 + size
    if not isinstance(gltf, dict):
        raise ModelError("the file has no glTF in it")
    for ext in gltf.get("extensionsRequired") or []:
        if ext in _COMPRESSED:
            raise ModelError(f"the model is compressed ({_COMPRESSED[ext]}): export it again without compression")
        if ext not in _HARMLESS:
            raise ModelError(f"the model needs {ext}, which is not read here: export it again without it")
    return gltf, binary


class _Reader:
    """A glTF's buffers, views and accessors, as numpy arrays."""

    def __init__(self, gltf: dict, binary: bytes):
        self.gltf = gltf
        self.buffers = []
        for i, b in enumerate(gltf.get("buffers") or []):
            uri = b.get("uri")
            if uri is None:
                self.buffers.append(binary if i == 0 else b"")
            elif uri.startswith("data:") and ";base64," in uri:
                self.buffers.append(base64.b64decode(uri.split(";base64,", 1)[1]))
            else:
                raise ModelError("the model's data is in a file of its own: export it as one .glb, everything inside")

    def view(self, k: int) -> tuple[bytes, int | None]:
        v = self.gltf["bufferViews"][k]
        buf = self.buffers[v["buffer"]]
        start = v.get("byteOffset", 0)
        end = start + v["byteLength"]
        if end > len(buf):
            raise ModelError("a part of the model is outside its data")
        return buf[start:end], v.get("byteStride")

    def accessor(self, k: int) -> np.ndarray:
        """An accessor's values, (count, width), as floats when normalized or float."""
        a = self.gltf["accessors"][k]
        if a.get("sparse"):
            raise ModelError("the model has sparse accessors: export it again without them")
        dtype = np.dtype(_COMPONENTS[a["componentType"]]).newbyteorder("<")
        width, count = _WIDTH[a["type"]], a["count"]
        if "bufferView" not in a:
            return np.zeros((count, width))
        data, stride = self.view(a["bufferView"])
        offset, item = a.get("byteOffset", 0), dtype.itemsize * width
        stride = stride or item
        if count and offset + stride * (count - 1) + item > len(data):
            raise ModelError("a part of the model is outside its data")
        values = np.ndarray((count, width), dtype=dtype, buffer=data, offset=offset, strides=(stride, dtype.itemsize))
        if a.get("normalized") and dtype.kind in "iu":
            top = float(np.iinfo(dtype).max)
            return np.maximum(values.astype(np.float64) / top, -1.0)
        return values.astype(np.float64)


def _matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T  # (glTF's is column by column)
    t = np.eye(4)
    t[:3, 3] = node.get("translation", (0, 0, 0))
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    r = np.eye(4)
    r[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    s = np.diag([*node.get("scale", (1, 1, 1)), 1.0])
    return t @ r @ s


def _triangles(gltf: dict, binary: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every triangle the default scene draws, placed by its nodes ((n, 3, 3) metres as
    the file has them), with its colour (linear rgb) and finish (roughness, metalness)."""
    r = _Reader(gltf, binary)
    nodes = gltf.get("nodes") or []
    scenes = gltf.get("scenes") or []
    if scenes:
        roots = scenes[gltf.get("scene", 0)].get("nodes", [])
    else:  # no scene: every node no other holds
        held = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in held]
    textures = _TextureColours(gltf, r)
    tris, colours, finishes, total = [], [], [], 0
    todo, seen = [(i, np.eye(4)) for i in roots], 0
    while todo:
        k, parent = todo.pop()
        seen += 1
        if seen > 100_000:
            raise ModelError("the model's nodes go round in a loop")
        node = nodes[k]
        m = parent @ _matrix(node)
        todo.extend((c, m) for c in node.get("children", []))
        if "mesh" not in node:
            continue
        for prim in gltf["meshes"][node["mesh"]].get("primitives", []):
            got = _primitive(r, prim, m, textures)
            if got is None:
                continue
            t, c, f = got
            total += len(t)
            if total > MAX_SOURCE_TRIANGLES:
                raise ModelError(f"the model has over {MAX_SOURCE_TRIANGLES:,} triangles: simplify it first")
            tris.append(t)
            colours.append(c)
            finishes.append(f)
    if not tris:
        return np.zeros((0, 3, 3)), np.zeros((0, 3)), np.zeros((0, 2))
    return np.concatenate(tris), np.concatenate(colours), np.concatenate(finishes)


def _primitive(r: _Reader, prim: dict, m: np.ndarray, textures) -> tuple | None:
    mode = prim.get("mode", 4)
    if mode not in (4, 5, 6) or "POSITION" not in prim.get("attributes", {}):
        return None  # points and lines are no surface
    attrs = prim["attributes"]
    p = r.accessor(attrs["POSITION"])[:, :3]
    idx = r.accessor(prim["indices"])[:, 0].astype(np.int64) if "indices" in prim else np.arange(len(p))
    if len(idx) and (idx.min() < 0 or idx.max() >= len(p)):
        raise ModelError("a part of the model points at vertices it does not have")
    if mode == 4:
        faces = idx[: len(idx) // 3 * 3].reshape(-1, 3)
    elif mode == 5:  # a strip: every other one turned back
        faces = np.array([(idx[i], idx[i + 1], idx[i + 2]) if i % 2 == 0 else (idx[i + 1], idx[i], idx[i + 2])
                          for i in range(len(idx) - 2)], dtype=np.int64).reshape(-1, 3)
    else:  # a fan
        faces = np.array([(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)], dtype=np.int64).reshape(-1, 3)
    if not len(faces):
        return None
    placed = p @ m[:3, :3].T + m[:3, 3]
    if np.linalg.det(m[:3, :3]) < 0:  # a mirrored node: its triangles' order is turned back
        faces = faces[:, ::-1]
    tri = placed[faces]
    base, finish = _material(r.gltf, prim.get("material"), textures)
    colour = np.tile(base, (len(faces), 1))
    if "COLOR_0" in attrs:
        vc = r.accessor(attrs["COLOR_0"])[:, :3]
        colour = colour * vc[faces].mean(axis=1)
    return tri, colour, np.tile(finish, (len(faces), 1))


def _material(gltf: dict, k: int | None, textures) -> tuple[np.ndarray, tuple[float, float]]:
    """A material's colour (linear rgb: its factor times its texture's average) and finish."""
    if k is None:
        return np.array([0.8, 0.8, 0.8]), PLAIN
    mat = (gltf.get("materials") or [])[k]
    if "pbrMetallicRoughness" not in mat and "KHR_materials_pbrSpecularGlossiness" not in mat.get("extensions", {}):
        return np.array([1.0, 1.0, 1.0]), PLAIN  # (glTF's defaults: white, and wholly metal, which no one meant)
    pbr = mat.get("pbrMetallicRoughness", {})
    rgb = np.array(pbr.get("baseColorFactor", (1, 1, 1, 1))[:3], dtype=np.float64)
    tex = pbr.get("baseColorTexture")
    finish = (float(pbr.get("roughnessFactor", 1.0)), float(pbr.get("metallicFactor", 1.0)))
    sg = mat.get("extensions", {}).get("KHR_materials_pbrSpecularGlossiness")
    if sg is not None and not mat.get("pbrMetallicRoughness"):  # an older file's specular-glossiness
        rgb = np.array(sg.get("diffuseFactor", (1, 1, 1, 1))[:3], dtype=np.float64)
        tex, finish = sg.get("diffuseTexture"), PLAIN
    if tex is not None:
        rgb = rgb * textures.average(tex.get("index"))
    return np.clip(rgb, 0, 1), finish


class _TextureColours:
    """A texture's average colour (linear), read once; white when it cannot be read."""

    def __init__(self, gltf: dict, r: _Reader):
        self.gltf, self.r, self.kept = gltf, r, {}

    def average(self, k) -> np.ndarray:
        if k not in self.kept:
            self.kept[k] = self._read(k)
        return self.kept[k]

    def _read(self, k) -> np.ndarray:
        white = np.ones(3)
        try:
            from PIL import Image
        except ImportError:
            return white
        try:
            texture = self.gltf["textures"][k]
            source = texture.get("source")
            if source is None:  # (webp or basis only): its own extension names the image
                source = next((e.get("source") for e in texture.get("extensions", {}).values() if "source" in e), None)
            image = self.gltf["images"][source]
            if "bufferView" in image:
                data, _ = self.r.view(image["bufferView"])
            elif str(image.get("uri", "")).startswith("data:") and ";base64," in image["uri"]:
                data = base64.b64decode(image["uri"].split(";base64,", 1)[1])
            else:
                return white
            with Image.open(io.BytesIO(data)) as im:
                small = np.asarray(im.convert("RGB").resize((16, 16)), dtype=np.float64) / 255
            return _linear(small.reshape(-1, 3)).mean(axis=0)
        except Exception:  # a texture it cannot read: the factor's colour alone
            return white


def _linear(srgb: np.ndarray) -> np.ndarray:
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


# ---- in metres, turned, simplified -------------------------------------------------------

def _in_metres(tri: np.ndarray) -> tuple[np.ndarray, str]:
    """A model's triangles in metres, and the units its size says it was in."""
    pts = tri.reshape(-1, 3)
    largest = float((pts.max(axis=0) - pts.min(axis=0)).max())
    for name, k in UNITS:
        if largest * k <= TOO_BIG_M:
            return tri * k, name
    raise ModelError(f"the model is {largest:,.0f} units across: too large for furniture in metres, "
                     "centimetres or millimetres")


def _turned(tri: np.ndarray, turn: int) -> np.ndarray:
    """Turned counter-clockwise seen from above (+Y): at 90 its front (+Z) faces +X."""
    if not turn:
        return tri
    a = math.radians(turn)
    c, s = round(math.cos(a)), round(math.sin(a))
    rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    return tri @ rot.T


def _groups(colour: np.ndarray, finish: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The colours and finishes the triangles have (as bytes, (g, 5): r, g, b, roughness,
    metalness), and each triangle's."""
    rows = np.concatenate([np.clip(np.rint(colour * 255), 0, 255), np.clip(np.rint(finish * 255), 0, 255)], axis=1)
    groups, of = np.unique(rows.astype(np.uint8), axis=0, return_inverse=True)
    return groups, of.reshape(-1)


def simplify(tri: np.ndarray, group: np.ndarray, budget: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Triangles (n, 3, 3) as at most ``budget`` (vertices, each vertex's group, and the
    triangles' (m, 3) vertices), by clustering: vertices within a cell of a grid are
    one, at their middle, and triangles left with two corners in one cell are gone. The
    cell is the smallest that leaves at most ``budget``. A vertex is its group's (its
    colour's): where two colours meet, each keeps its own edge."""
    pts = tri.reshape(-1, 3)
    vertices, of = np.unique(np.round(pts, 6), axis=0, return_inverse=True)  # the same point is one vertex
    of = of.reshape(-1, 3)
    if len(tri) <= budget:
        return _indexed(vertices, of, np.arange(len(vertices)), group)
    span = float((vertices.max(axis=0) - vertices.min(axis=0)).max())
    lo, hi = math.log(span / 20000), math.log(span / 2)
    best = None
    for _ in range(22):  # the smallest cell leaving at most budget, a few % off
        cell = math.exp((lo + hi) / 2)
        cluster = _clusters(vertices, cell)
        if _kept(cluster[of]).sum() <= budget:
            best, hi = cluster, math.log(cell)
        else:
            lo = math.log(cell)
        if hi - lo < 0.02:
            break
    if best is None:
        best = _clusters(vertices, span)
    return _indexed(vertices, of, best, group)


def _clusters(vertices: np.ndarray, cell: float) -> np.ndarray:
    keys = np.floor((vertices - vertices.min(axis=0)) / cell).astype(np.int64)
    packed = (keys[:, 0] << 42) | (keys[:, 1] << 21) | keys[:, 2]  # (each under 2^21: span / cell ≤ 20000)
    return np.unique(packed, return_inverse=True)[1].reshape(-1)


def _kept(faces: np.ndarray) -> np.ndarray:
    a, b, c = faces[:, 0], faces[:, 1], faces[:, 2]
    return (a != b) & (b != c) & (a != c)


def _indexed(vertices, of, cluster, group) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The triangles left, each corner a vertex of its cluster and its triangle's group."""
    faces = cluster[of]
    keep = _kept(faces)
    faces, group = faces[keep], group[keep]
    # one triangle drawn twice (in one group, its corners in the same order turned) is drawn once
    first = np.argmin(faces, axis=1)
    rolled = np.stack([faces[np.arange(len(faces)), (first + k) % 3] for k in range(3)], axis=1)
    _, unique = np.unique(np.column_stack([rolled, group]), axis=0, return_index=True)
    faces, group = faces[np.sort(unique)], group[np.sort(unique)]
    middle = np.zeros((cluster.max() + 1, 3))
    np.add.at(middle, cluster, vertices)
    middle /= np.bincount(cluster, minlength=len(middle))[:, None]
    corners = np.stack([faces.reshape(-1), np.repeat(group, 3)], axis=1)
    pairs, index = np.unique(corners, axis=0, return_inverse=True)
    return middle[pairs[:, 0]], pairs[:, 1], index.reshape(-1, 3)


# ---- writing the model -------------------------------------------------------------------

def write_glb(positions: np.ndarray, colours: np.ndarray, finishes: np.ndarray, index: np.ndarray,
              generator: str, extras: dict) -> bytes:
    """The file a package carries (spec/FORMAT.md, Models): one mesh, its vertices'
    colours (linear, as glTF's are) and finishes (roughness, metalness) as bytes, each
    padded to four (glTF aligns a vertex's every attribute so)."""
    n = len(positions)
    pos = positions.astype("<f4")
    padded = np.zeros((n, 4), dtype=np.uint8)
    padded[:, :3] = colours
    fin = np.zeros((n, 4), dtype=np.uint8)
    fin[:, :2] = finishes
    small = n < 65536
    idx = index.reshape(-1).astype("<u2" if small else "<u4")
    parts = [pos.tobytes(), padded.tobytes(), fin.tobytes(), idx.tobytes()]
    views, at = [], 0
    for k, (part, stride) in enumerate(zip(parts, (None, 4, 4, None))):
        view = {"buffer": 0, "byteOffset": at, "byteLength": len(part), "target": 34963 if k == 3 else 34962}
        if stride:
            view["byteStride"] = stride
        views.append(view)
        at += len(part) + (-len(part) % 4)
    binary = b"".join(part + b"\0" * (-len(part) % 4) for part in parts)
    gltf = {
        "asset": {"version": "2.0", "generator": generator, "extras": {"storeypath": extras}},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"mode": 4, "material": 0, "indices": 3,
                                    "attributes": {"POSITION": 0, "COLOR_0": 1, "_FINISH": 2}}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0,
                                                "roughnessFactor": 1}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
             "min": [float(v) for v in pos.min(axis=0)], "max": [float(v) for v in pos.max(axis=0)]},
            {"bufferView": 1, "componentType": 5121, "normalized": True, "count": n, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5121, "normalized": True, "count": n, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123 if small else 5125, "count": len(idx), "type": "SCALAR"},
        ],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    return (_MAGIC + struct.pack("<II", 2, total) + struct.pack("<II", len(text), _JSON) + text
            + struct.pack("<II", len(binary), _BIN) + binary)


def read_model(glb: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A model as write_glb wrote it: its vertices, their colours (bytes), and its
    triangles' vertices (for its picture, and for tests)."""
    gltf, binary = read_glb(glb)
    r = _Reader(gltf, binary)
    prim = gltf["meshes"][0]["primitives"][0]
    positions = r.accessor(prim["attributes"]["POSITION"])
    if "COLOR_0" in prim["attributes"]:
        colours = np.rint(r.accessor(prim["attributes"]["COLOR_0"])[:, :3] * 255).astype(np.uint8)
    else:
        colours = np.full((len(positions), 3), 255, dtype=np.uint8)
    index = (r.accessor(prim["indices"])[:, 0].astype(np.int64) if "indices" in prim
             else np.arange(len(positions))).reshape(-1, 3)
    return positions, colours, index


def model_problem(glb: bytes) -> str | None:
    """What is wrong with a package's model file (FORMAT.md, Models), or None."""
    try:
        gltf, _ = read_glb(glb)
        prims = [p for mesh in gltf.get("meshes") or [] for p in mesh.get("primitives", [])]
        if len(prims) != 1 or prims[0].get("mode", 4) != 4 or "POSITION" not in prims[0].get("attributes", {}):
            return "not one mesh of triangles"
        positions, _, index = read_model(glb)
    except ModelError as e:
        return str(e)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return f"not glTF as glTF 2.0 says ({type(e).__name__}: {e})"
    if len(index) > MAX_TRIANGLES:
        return f"{len(index):,} triangles: at most {MAX_TRIANGLES:,}"
    if len(index) and (index.min() < 0 or index.max() >= len(positions)):
        return "its triangles point at vertices it does not have"
    lo, hi = positions.min(axis=0), positions.max(axis=0)
    if abs(lo[1]) > 1e-3 or abs(lo[0] + hi[0]) > 1e-3 or abs(lo[2] + hi[2]) > 1e-3:
        return "not in its type's frame (its middle at x = z = 0, its bottom at y = 0)"
    return None


# ---- its picture ------------------------------------------------------------------------

VIEWS = ("front", "top", "side")
_LIGHT = np.array([0.35, 0.8, 0.5]) / np.linalg.norm([0.35, 0.8, 0.5])


def picture(glb: bytes, view: str = "front", px: int = 240) -> str:
    """The model drawn flat as SVG, seen from its ``front`` (+Z), from above (``top``,
    its front down) or from its right (``side``): each triangle in its colour, lit from
    above, the nearer over the farther."""
    if view not in VIEWS:
        raise ValueError(f"a model is seen from its {', '.join(VIEWS)}")
    positions, colours, index = read_model(glb)
    # (right, up, towards the eye) of each view, in the model's frame
    axes = {"front": ((1, 0, 0), (0, 1, 0), (0, 0, 1)), "top": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
            "side": ((0, 0, 1), (0, 1, 0), (-1, 0, 0))}[view]  # (its right is -X: glTF's +X is its left)
    basis = np.array(axes, dtype=np.float64).T
    seen = positions @ basis
    tri = seen[index]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    keep = length > 1e-12
    tri, index, normal = tri[keep], index[keep], normal[keep] / length[keep, None]
    world_normal = normal @ basis.T  # the triangle's normal in the model's frame, for its light
    shade = 0.45 + 0.55 * np.abs(world_normal @ _LIGHT)
    order = np.argsort(tri[:, :, 2].mean(axis=1))  # the farthest first
    lo, hi = seen[:, :2].min(axis=0), seen[:, :2].max(axis=0)
    k = (px - 8) / max(float((hi - lo).max()), 1e-9)
    w, h = (hi - lo) * k + 8
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}">']
    for t in order:
        c = colours[index[t]].mean(axis=0) / 255
        srgb = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055) * shade[t]
        fill = "#%02x%02x%02x" % tuple(int(v) for v in np.clip(np.rint(srgb * 255), 0, 255))
        pts = " ".join(f"{(x - lo[0]) * k + 4:.1f},{(hi[1] - y) * k + 4:.1f}" for x, y, _ in tri[t])
        out.append(f'<polygon points="{pts}" fill="{fill}" stroke="{fill}" stroke-width="0.4"/>')
    out.append("</svg>")
    return "\n".join(out)
