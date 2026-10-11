"""Item types' 3D models (models.py): a .glb read whole, in metres, turned, simplified,
and written as the small file a package carries (spec/FORMAT.md, Models)."""

import json
import struct

import numpy as np
import pytest

from storeypath.models import MODEL_TRIANGLES, ModelError, make_model, picture, read_glb, read_model


def glb(primitives, materials=(), node=None, extensions_required=(), uri=None) -> bytes:
    """A binary glTF file of ``primitives`` ([(positions (n, 3), triangles (m, 3), material
    index or None)]) in one mesh, on one node (``node``: its transform)."""
    binary, views, accessors, prims = b"", [], [], []
    for positions, faces, material in primitives:
        p = np.asarray(positions, dtype="<f4")
        f = np.asarray(faces, dtype="<u4").reshape(-1)
        for data, kind, comp, count in ((p, "VEC3", 5126, len(p)), (f, "SCALAR", 5125, len(f))):
            raw = data.tobytes()
            views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(raw)})
            accessors.append({"bufferView": len(views) - 1, "componentType": comp, "count": count, "type": kind,
                              **({"min": p.min(axis=0).tolist(), "max": p.max(axis=0).tolist()} if kind == "VEC3" else {})})
            binary += raw + b"\0" * (-len(raw) % 4)
        prims.append({"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1,
                      **({"material": material} if material is not None else {})})
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, **(node or {})}], "meshes": [{"primitives": prims}],
            "materials": list(materials), "buffers": [{"byteLength": len(binary), **({"uri": uri} if uri else {})}],
            "bufferViews": views, "accessors": accessors}
    if extensions_required:
        gltf["extensionsRequired"] = list(extensions_required)
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def box(x0, y0, z0, x1, y1, z1):
    """A box's corners and its twelve triangles, its front (+Z) the last two."""
    p = [[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    faces = [[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3],
             [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]]
    return np.array(p, dtype=float), np.array(faces)


def colour(rgb):
    return {"pbrMetallicRoughness": {"baseColorFactor": [*rgb, 1], "metallicFactor": 0, "roughnessFactor": 0.6}}


RED, BLUE = (0.8, 0.05, 0.05), (0.05, 0.05, 0.8)


def sofa_in_cm() -> bytes:
    """A sofa 200 x 90 x 75 cm, drawn in centimetres, placed off its node's origin: its
    body red, its front face (+Z) blue."""
    p, f = box(-100, 0, -45, 100, 75, 45)
    return glb([(p, f[:10], 0), (p, f[10:], 1)], [colour(RED), colour(BLUE)],
               node={"translation": [500, 20, 300]})


def test_a_model_is_read_in_metres_its_middle_at_the_origin_its_bottom_on_the_floor():
    m = make_model(sofa_in_cm(), "SOFA", "sofa.glb")
    assert m.units == "cm" and m.size == (2.0, 0.9, 0.75)  # width, depth, height
    positions, colours, index = read_model(m.glb)
    assert np.allclose(positions.min(axis=0), [-1.0, 0.0, -0.45]) and np.allclose(positions.max(axis=0), [1.0, 0.75, 0.45])
    assert m.triangles == len(index) == 12
    reds = {tuple(c) for c in colours if c[0] > c[2]}
    blues = {tuple(c) for c in colours if c[2] > c[0]}
    assert reds and blues  # each part its colour (linear, as glTF's are)
    gltf, _ = read_glb(m.glb)
    assert gltf["asset"]["extras"]["storeypath"]["code"] == "SOFA"
    assert {v.get("byteStride") for v in gltf["bufferViews"][1:3]} == {4}  # (glTF aligns each vertex attribute so)


def test_a_model_turned_a_quarter_faces_the_other_way_its_width_and_depth_swapped():
    m = make_model(sofa_in_cm(), "SOFA", turn=90)
    assert m.size == (0.9, 2.0, 0.75)
    positions, colours, index = read_model(m.glb)
    front = positions[[i for i in range(len(positions)) if colours[i][2] > colours[i][0]]]
    assert np.allclose(front[:, 0], positions[:, 0].max())  # its front faces +X now


def _sphere(n: int):
    u, v = np.meshgrid(np.linspace(0, np.pi, n), np.linspace(0, 2 * np.pi, 2 * n))
    p = np.stack([np.sin(u) * np.cos(v), np.cos(u) + 1, np.sin(u) * np.sin(v)], axis=-1).reshape(-1, 3) * 0.4
    rows, cols = u.shape
    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            a, b, d, e = r * cols + c, r * cols + c + 1, (r + 1) * cols + c, (r + 1) * cols + c + 1
            faces += [[a, d, b], [b, d, e]]
    return p, np.array(faces)


def test_a_detailed_model_is_simplified_to_what_a_floor_of_them_can_hold():
    p, f = _sphere(120)
    assert len(f) > 4 * MODEL_TRIANGLES
    m = make_model(glb([(p, f, 0)], [colour(RED)]), "STOOL")
    assert m.source_triangles == len(f) and MODEL_TRIANGLES * 0.5 <= m.triangles <= MODEL_TRIANGLES
    assert m.units == "m" and all(abs(a - b) < 0.02 for a, b in zip(m.size, (0.8, 0.8, 0.8)))


def test_files_that_are_no_model_this_reads_are_refused_saying_why():
    p, f = box(0, 0, 0, 1, 1, 1)
    with pytest.raises(ModelError, match="not a binary glTF"):
        make_model(b"solid cube\nendsolid", "X")
    with pytest.raises(ModelError, match="compressed"):
        make_model(glb([(p, f, None)], extensions_required=["KHR_draco_mesh_compression"]), "X")
    with pytest.raises(ModelError, match="file of its own"):
        make_model(glb([(p, f, None)], uri="sofa.bin"), "X")
    with pytest.raises(ModelError, match="no triangles"):
        make_model(glb([]), "X")
    with pytest.raises(ModelError, match="turned"):
        make_model(sofa_in_cm(), "SOFA", turn=45)


def test_a_model_is_pictured_from_its_front_above_and_its_side():
    m = make_model(sofa_in_cm(), "SOFA")
    front, top = picture(m.glb, "front"), picture(m.glb, "top")
    assert front.startswith("<svg") and front.count("<polygon") >= 2
    assert 'viewBox="0 0 240 ' in front and 'viewBox="0 0 240 ' in top  # as wide as the picture, both
    with pytest.raises(ValueError):
        picture(m.glb, "below")
