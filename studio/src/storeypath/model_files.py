"""A model as people find it, made one binary glTF for models.make_model (spec/FORMAT.md,
Models): furniture is published as glTF, FBX, OBJ with its .mtl, Collada and a few more,
alone or in a .zip with its textures.

glTF is read here, its buffers and images packed in from beside it. The others are
converted by Assimp, the Open Asset Import Library's command (``assimp``: Debian's
assimp-utils, Homebrew's assimp; STOREYPATH_ASSIMP names another), run as a process of
its own, for two minutes and 4 GB at most: a file from anyone may be made to crash what
reads it, and it must not take Studio with it. What Assimp leaves out is kept: the units
an FBX file says it is in (Collada's it applies itself), and a texture's colour alone
where an FBX file also gives a colour (Blender writes 0.8 grey beside every texture,
which Assimp would darken it by). Formats only their own program reads (SketchUp, Revit,
3ds Max, …) are refused, saying how to export them.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .models import ModelError

# what is read, by its extension: glTF here, the rest by Assimp; in a .zip holding several
# kinds, the first of this order is the model (the others its sources, or copies)
KINDS = {".glb": "glTF", ".gltf": "glTF", ".fbx": "FBX", ".dae": "Collada", ".obj": "OBJ", ".3ds": "3DS",
         ".3mf": "3MF", ".x3d": "X3D", ".ply": "PLY", ".stl": "STL", ".off": "OFF"}
# what no free reader reads well: how to make one that is
REFUSED = {
    ".skp": "a SketchUp model: export it from SketchUp Pro (File › Export › 3D Model) as FBX, OBJ or Collada (.dae)",
    ".rfa": "a Revit family: place it in a project and export the view as FBX, or take the maker's FBX, OBJ or SketchUp file",
    ".rvt": "a Revit project: export the view as FBX, or take the maker's FBX or OBJ file of the object",
    ".max": "a 3ds Max scene: export it from 3ds Max as FBX or OBJ",
    ".blend": "a Blender file: export it from Blender (File › Export) as glTF 2.0 (.glb)",
    ".c4d": "a Cinema 4D file: export it as FBX or glTF",
    ".3dm": "a Rhino model: export it as FBX or OBJ",
    ".ma": "a Maya scene: export it as FBX", ".mb": "a Maya scene: export it as FBX",
    ".dwg": "an AutoCAD drawing: export the 3D object as FBX or OBJ",
    ".dxf": "an AutoCAD drawing: export the 3D object as FBX or OBJ",
    ".ifc": "an IFC (BIM) file: export the object as FBX or OBJ from the BIM program",
    ".usd": "a USD file: export it as glTF (.glb)", ".usda": "a USD file: export it as glTF (.glb)",
    ".usdc": "a USD file: export it as glTF (.glb)", ".usdz": "a USDZ file: export it as glTF (.glb)",
}
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
          ".bmp": "image/bmp", ".tga": "image/x-tga", ".tif": "image/tiff", ".tiff": "image/tiff", ".gif": "image/gif"}
MAX_ZIP_FILES = 2000
MAX_UNZIPPED = 1 << 30  # bytes a .zip may unpack to
TIMEOUT = 120  # seconds Assimp may take
MEMORY = 4 << 30  # bytes it may use (where the system limits it)


@dataclass
class Found:
    """What a file brought: ``glb`` its model as binary glTF; ``kind`` the format it came in;
    ``stated`` the metres a unit of it is, as the file says (None: it does not, or its
    numbers are metres already); ``notes`` what was missing or left out."""

    glb: bytes
    kind: str
    stated: float | None = None
    notes: list[str] = field(default_factory=list)


def kind_of(name: str) -> str | None:
    """The format a file's name says (FBX, OBJ…), "zip", or None."""
    ext = Path(str(name or "")).suffix.lower()
    return "zip" if ext == ".zip" else KINDS.get(ext)


def as_glb(data: bytes, name: str = "") -> Found:
    """A model as binary glTF, from a file of any format read (KINDS) or a .zip holding
    one with its textures. Raises ModelError saying why not (and what to do)."""
    ext = Path(str(name or "")).suffix.lower()
    if data[:4] == b"glTF":
        return Found(data, "glTF")
    if ext == ".glb" and data[:4] != b"PK\x03\x04":
        from .models import read_glb

        read_glb(data)  # (not one: says so)
    if ext in REFUSED:
        raise ModelError(f"the file is {REFUSED[ext]}")
    with tempfile.TemporaryDirectory(prefix="sp-model-") as tmp:
        root = Path(tmp) / "in"
        root.mkdir()
        if data[:4] == b"PK\x03\x04":
            main = _unzip(data, root)
        elif ext in KINDS:
            main = root / f"model{ext}"
            main.write_bytes(data)
        else:
            raise ModelError(f"not a model file read here ({ext or 'no extension'}): send glTF (.glb), FBX, OBJ, "
                             "Collada (.dae), 3DS, 3MF, X3D, PLY or STL, or a .zip of one with its textures")
        e = main.suffix.lower()
        notes: list[str] = []
        if e in (".glb", ".gltf"):
            glb = _packed(main.read_bytes(), main.parent, root, notes)
            return Found(glb, "glTF", None, notes)
        out = Path(tmp) / "out.glb"
        _assimp(main, out, KINDS[e])
        glb = _packed(out.read_bytes(), main.parent, root, notes)
        stated = None
        if e == ".fbx":
            stated = fbx_unit(main.read_bytes())
            glb = _texture_alone(glb)
        if e == ".obj":
            notes += _obj_notes(main.read_bytes(), main.parent, root)
        return Found(glb, KINDS[e], stated, notes)


# ---- a .zip -------------------------------------------------------------------------------

def _unzip(data: bytes, root: Path) -> Path:
    """A .zip unpacked under ``root`` (its names made plain: none outside it, no links),
    and the model in it."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = [i for i in z.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, ValueError) as e:
        raise ModelError(f"the .zip cannot be read: {e}") from None
    if len(infos) > MAX_ZIP_FILES:
        raise ModelError(f"the .zip holds {len(infos)} files: a model's .zip holds {MAX_ZIP_FILES} at most")
    total = 0
    with z:
        for info in infos:
            parts = [p for p in PurePosixPath(info.filename.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
            if not parts or parts[0] == "__MACOSX" or parts[-1].startswith("._"):
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # a link: not followed
                continue
            dest = root.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                while chunk := src.read(1 << 20):
                    total += len(chunk)
                    if total > MAX_UNZIPPED:
                        raise ModelError(f"the .zip unpacks to over {MAX_UNZIPPED >> 30} GB")
                    out.write(chunk)
    files = [p for p in root.rglob("*") if p.is_file()]
    models = [p for p in files if p.suffix.lower() in KINDS]
    if not models:
        refused = next((p for p in files if p.suffix.lower() in REFUSED), None)
        if refused is not None:
            raise ModelError(f"the .zip holds {REFUSED[refused.suffix.lower()]}")
        raise ModelError("the .zip holds no model read here: glTF (.glb), FBX, OBJ, Collada (.dae), 3DS, 3MF, X3D, PLY or STL")
    order = list(KINDS)
    first = order[min(order.index(p.suffix.lower()) for p in models)]
    same = sorted(p for p in models if p.suffix.lower() == first)
    if len(same) > 1:
        names = ", ".join(p.relative_to(root).as_posix() for p in same[:6]) + (", …" if len(same) > 6 else "")
        raise ModelError(f"the .zip holds {len(same)} models ({names}): send one at a time")
    return same[0]


# ---- glTF packed whole ----------------------------------------------------------------------

def _find(base: Path, root: Path, uri: str) -> Path | None:
    """A file a model names (a texture, a buffer): where it says, from beside it (its
    slashes either way), else by its name anywhere in what was sent; never outside it."""
    rel = unquote(uri).replace("\\", "/")
    name = PurePosixPath(rel).name
    for p in (base / rel, base / name):
        try:
            p = p.resolve()
        except OSError:
            continue
        if p.is_file() and p.is_relative_to(root.resolve()):
            return p
    low = name.lower()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name.lower() == low:
            return p
    return None


def _packed(raw: bytes, base: Path, root: Path, notes: list[str]) -> bytes:
    """A glTF (.gltf or .glb) with the files it names beside it (buffers, images) packed
    into one binary glTF; images not found left out (``notes`` says which)."""
    from .models import read_glb

    if raw[:4] == b"glTF":
        gltf, binary = read_glb(raw)
    else:
        try:
            gltf, binary = json.loads(raw.decode("utf-8-sig")), b""
        except (UnicodeDecodeError, ValueError) as e:
            raise ModelError(f"the .gltf is not JSON: {e}") from None
    if not isinstance(gltf, dict):
        raise ModelError("the .gltf is not glTF")
    out, offsets = bytearray(), []
    for buf in gltf.get("buffers") or []:
        uri = buf.get("uri")
        if uri is None:
            data = binary
        elif uri.startswith("data:"):
            data = _data_uri(uri)
        else:
            p = _find(base, root, uri)
            if p is None:
                raise ModelError(f"its data file ({PurePosixPath(unquote(uri)).name}) was not sent: "
                                 "send the .gltf in a .zip with its .bin and textures")
            data = p.read_bytes()
        offsets.append(len(out))
        out += data + b"\0" * (-len(data) % 4)
    for view in gltf.get("bufferViews") or []:
        view["byteOffset"] = view.get("byteOffset", 0) + offsets[view.get("buffer", 0)]
        view["buffer"] = 0
    missing = []
    for image in gltf.get("images") or []:
        uri = image.get("uri")
        if not uri or uri.startswith("data:"):
            continue
        p = _find(base, root, uri)
        if p is None:
            missing.append(PurePosixPath(unquote(uri).replace("\\", "/")).name)
            del image["uri"]
            continue
        data = p.read_bytes()
        gltf.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": len(out), "byteLength": len(data)})
        out += data + b"\0" * (-len(data) % 4)
        del image["uri"]
        image["bufferView"] = len(gltf["bufferViews"]) - 1
        image["mimeType"] = IMAGES.get(p.suffix.lower(), "application/octet-stream")
    if missing:
        notes.append(f"{len(missing)} of its textures {'was' if len(missing) == 1 else 'were'} not sent "
                     f"({', '.join(sorted(set(missing))[:4])}{', …' if len(set(missing)) > 4 else ''}): "
                     "its parts are in plain colours; send it in a .zip with its textures")
    gltf["buffers"] = [{"byteLength": len(out)}] if out else []
    return glb_of(gltf, bytes(out))


def _data_uri(uri: str) -> bytes:
    import base64

    head, _, body = uri.partition(",")
    return base64.b64decode(body) if head.endswith(";base64") else unquote(body).encode("latin-1")


def glb_of(gltf: dict, binary: bytes) -> bytes:
    """A binary glTF file of a glTF's JSON and its one buffer."""
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        binary += b"\0" * (-len(binary) % 4)
        body += struct.pack("<II", len(binary), 0x004E4942) + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def _texture_alone(glb: bytes) -> bytes:
    """An FBX file's materials as its program shows them: a texture is the colour, not
    one multiplied by the colour the file also gives (Assimp multiplies them)."""
    from .models import read_glb

    gltf, binary = read_glb(glb)
    changed = False
    for m in gltf.get("materials") or []:
        pbr = m.get("pbrMetallicRoughness") or {}
        if "baseColorTexture" in pbr and "baseColorFactor" in pbr:
            pbr["baseColorFactor"] = [1, 1, 1, pbr["baseColorFactor"][3] if len(pbr["baseColorFactor"]) > 3 else 1]
            changed = True
    return glb_of(gltf, binary) if changed else glb


# ---- what Assimp leaves out -----------------------------------------------------------------

def fbx_unit(data: bytes) -> float | None:
    """The metres a unit of an FBX file is, as it says (UnitScaleFactor: centimetres a
    unit), binary or text; None when it does not say."""
    value = None
    if data.startswith(b"Kaydara FBX Binary"):
        at = data.find(b"UnitScaleFactor")
        while at >= 0 and value is None:
            j = at + len(b"UnitScaleFactor")
            try:
                for _ in range(4):  # its type's names ("double", "Number", ""), then its value
                    if data[j:j + 1] != b"S":
                        break
                    j += 5 + struct.unpack_from("<I", data, j + 1)[0]
                if data[j:j + 1] == b"D":
                    value = struct.unpack_from("<d", data, j + 1)[0]
                elif data[j:j + 1] == b"F":
                    value = struct.unpack_from("<f", data, j + 1)[0]
            except struct.error:
                pass
            at = data.find(b"UnitScaleFactor", at + 1)
    else:
        m = re.search(rb'P:\s*"UnitScaleFactor"\s*,[^\n]*?,\s*([-+0-9.eE]+)\s*\r?\n', data[:1 << 20])
        if m:
            try:
                value = float(m.group(1))
            except ValueError:
                value = None
    if value is None or not (1e-4 < value < 1e5):
        return None
    return value / 100.0


def _obj_notes(data: bytes, base: Path, root: Path) -> list[str]:
    """What an OBJ file names that was not sent: its materials (.mtl)."""
    names = re.findall(rb"^\s*mtllib\s+(.+?)\s*$", data[:1 << 22], re.M)
    gone = [n.decode("utf-8", "replace") for n in names if _find(base, root, n.decode("utf-8", "replace")) is None]
    if gone:
        return [f"its materials ({', '.join(gone)}) were not sent: it is in plain grey; send the .obj in a .zip "
                "with its .mtl (and textures)"]
    if not names:
        return ["it names no materials (.mtl): it is in plain grey"]
    return []


def assimp_command() -> str | None:
    """The Assimp command used here (STOREYPATH_ASSIMP, else ``assimp`` on the PATH), or None."""
    return os.environ.get("STOREYPATH_ASSIMP") or shutil.which("assimp")


# (run before Assimp, in its process: the limits set, then Assimp in its place)
_LIMITED = ("import os, resource, sys\n"
            "for limit, value in ((getattr(resource, 'RLIMIT_AS', None), int(sys.argv[1])), "
            "(resource.RLIMIT_CPU, int(sys.argv[2]))):\n"
            "    try:\n"
            "        limit is not None and resource.setrlimit(limit, (value, value))\n"
            "    except (ValueError, OSError):\n"
            "        pass\n"
            "os.execv(sys.argv[3], sys.argv[3:])\n")


def _assimp(src: Path, out: Path, kind: str) -> None:
    exe = assimp_command()
    if not exe:
        raise ModelError(f"{kind} files are not read here: Studio's converter (Assimp) is not installed "
                         "(Homebrew: brew install assimp; Debian: apt install assimp-utils). A .glb is read without it")
    exe = shutil.which(exe) or exe
    cmd = [sys.executable, "-I", "-S", "-c", _LIMITED, str(MEMORY), str(TIMEOUT + 5),
           exe, "export", str(src), str(out), "-fglb2", "-tri"]
    try:
        run = subprocess.run(cmd, cwd=src.parent, stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT,
                             env={"PATH": os.environ.get("PATH", ""), "LANG": "C", "HOME": str(out.parent)})
    except subprocess.TimeoutExpired:
        raise ModelError(f"reading the {kind} file took over {TIMEOUT // 60} minutes: it was stopped") from None
    if run.returncode != 0 or not out.is_file() or out.stat().st_size < 20:
        said = (run.stderr + run.stdout).decode("utf-8", "replace").replace(str(src.parent), "").replace(str(out.parent), "")
        lines = [ln.strip() for ln in said.splitlines() if re.search(r"error|fail|unsupported|invalid", ln, re.I)]
        why = (lines[-1] if lines else said.strip().splitlines()[-1] if said.strip() else f"exit {run.returncode}")[:300]
        raise ModelError(f"the {kind} file could not be read ({why}): export it again, or as glTF (.glb)")
