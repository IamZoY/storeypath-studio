"""Item types' models in the formats furniture is published in (model_files.py): glTF
packed whole, the rest converted by Assimp (skipped where it is not installed), what
Assimp leaves out kept (an FBX file's units, a texture's own colour), a .zip unpacked
safely, and what is not read refused, saying how to export it."""

import io
import json
import struct
import zipfile

import numpy as np
import pytest

from storeypath.model_files import as_glb, assimp_command, fbx_unit, glb_of, kind_of
from storeypath.models import ModelError, doubts, make_model, read_glb, read_model

needs_assimp = pytest.mark.skipif(assimp_command() is None,
                                  reason="Assimp is not installed (brew install assimp; apt install assimp-utils)")

# ---- small model files written by hand, as other programs write them ----------------------


def box_faces(x0, y0, z0, x1, y1, z1):
    """A box's 8 corners and its 6 quads (corner indexes), its front (+Z) the last."""
    p = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    quads = [(0, 2, 6, 4), (0, 4, 5, 1), (0, 1, 3, 2), (4, 6, 7, 5), (2, 3, 7, 6), (1, 5, 7, 3)]
    return p, quads


def obj(size=(200, 75, 90), mtl="sofa.mtl"):
    """A sofa as an OBJ (Y up, in centimetres by default) and its .mtl: red, its front blue."""
    w, h, d = size
    p, quads = box_faces(-w / 2, 0, -d / 2, w / 2, h, d / 2)
    lines = [f"mtllib {mtl}"] if mtl else []
    lines += [f"v {x} {y} {z}" for x, y, z in p]
    lines += ["usemtl body"] + [f"f {' '.join(str(i + 1) for i in q)}" for q in quads[:5]]
    lines += ["usemtl front", f"f {' '.join(str(i + 1) for i in quads[5])}"]
    material = "newmtl body\nKd 0.8 0.05 0.05\n\nnewmtl front\nKd 0.05 0.05 0.8\n"
    return "\n".join(lines) + "\n", material


def fbx(size=(2.0, 0.75, 0.9), unit_cm=100.0):
    """A sofa as an ASCII FBX 7.4 file, Y up, ``unit_cm`` centimetres a unit (UnitScaleFactor),
    its body red and its front blue (two materials, by polygon)."""
    k = 100.0 / unit_cm  # metres to the file's units
    w, h, d = (s * k for s in size)
    p, quads = box_faces(-w / 2, 0, -d / 2, w / 2, h, d / 2)
    verts = ",".join(f"{c:g}" for v in p for c in v)
    idx = ",".join(",".join(str(i) if j < 3 else str(-i - 1) for j, i in enumerate(q)) for q in quads)
    mats = ",".join("0" if i < 5 else "1" for i in range(6))
    return f"""; FBX 7.4.0 project file
FBXHeaderExtension:  {{
\tFBXHeaderVersion: 1003
\tFBXVersion: 7400
\tCreator: "StoreyPath test"
}}
GlobalSettings:  {{
\tVersion: 1000
\tProperties70:  {{
\t\tP: "UpAxis", "int", "Integer", "",1
\t\tP: "UpAxisSign", "int", "Integer", "",1
\t\tP: "FrontAxis", "int", "Integer", "",2
\t\tP: "FrontAxisSign", "int", "Integer", "",1
\t\tP: "CoordAxis", "int", "Integer", "",0
\t\tP: "CoordAxisSign", "int", "Integer", "",1
\t\tP: "UnitScaleFactor", "double", "Number", "",{unit_cm:g}
\t\tP: "OriginalUnitScaleFactor", "double", "Number", "",{unit_cm:g}
\t}}
}}
Objects:  {{
\tGeometry: 1000, "Geometry::sofa", "Mesh" {{
\t\tVertices: *{len(p) * 3} {{
\t\t\ta: {verts}
\t\t}}
\t\tPolygonVertexIndex: *{len(quads) * 4} {{
\t\t\ta: {idx}
\t\t}}
\t\tGeometryVersion: 124
\t\tLayerElementMaterial: 0 {{
\t\t\tVersion: 101
\t\t\tName: ""
\t\t\tMappingInformationType: "ByPolygon"
\t\t\tReferenceInformationType: "IndexToDirect"
\t\t\tMaterials: *6 {{
\t\t\t\ta: {mats}
\t\t\t}}
\t\t}}
\t\tLayer: 0 {{
\t\t\tVersion: 100
\t\t\tLayerElement:  {{
\t\t\t\tType: "LayerElementMaterial"
\t\t\t\tTypedIndex: 0
\t\t\t}}
\t\t}}
\t}}
\tModel: 2000, "Model::sofa", "Mesh" {{
\t\tVersion: 232
\t\tProperties70:  {{
\t\t}}
\t}}
\tMaterial: 3000, "Material::body", "" {{
\t\tVersion: 102
\t\tShadingModel: "phong"
\t\tProperties70:  {{
\t\t\tP: "DiffuseColor", "Color", "", "A",0.8,0.05,0.05
\t\t}}
\t}}
\tMaterial: 3001, "Material::front", "" {{
\t\tVersion: 102
\t\tShadingModel: "phong"
\t\tProperties70:  {{
\t\t\tP: "DiffuseColor", "Color", "", "A",0.05,0.05,0.8
\t\t}}
\t}}
}}
Connections:  {{
\tC: "OO",2000,0
\tC: "OO",1000,2000
\tC: "OO",3000,2000
\tC: "OO",3001,2000
}}
"""


def dae(size=(2.0, 0.75, 0.9), meter=1.0, z_up=False):
    """A sofa as Collada, its units ``meter`` metres each, Y up (or Z up: its front then -Y),
    its body red and its front blue."""
    w, h, d = (s / meter for s in size)
    p, quads = box_faces(-w / 2, 0, -d / 2, w / 2, h, d / 2)
    if z_up:  # (x, y, z) Y up → (x, -z, y): the front, +Z, is -Y
        p = [(x, -z, y) for x, y, z in p]
    pos = " ".join(f"{c:g}" for v in p for c in v)
    def tris(qs):
        return " ".join(f"{q[0]} {q[1]} {q[2]} {q[0]} {q[2]} {q[3]}" for q in qs)
    up = "Z_UP" if z_up else "Y_UP"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit name="unit" meter="{meter:g}"/><up_axis>{up}</up_axis></asset>
  <library_effects>
    <effect id="body-fx"><profile_COMMON><technique sid="t"><lambert><diffuse><color>0.8 0.05 0.05 1</color></diffuse></lambert></technique></profile_COMMON></effect>
    <effect id="front-fx"><profile_COMMON><technique sid="t"><lambert><diffuse><color>0.05 0.05 0.8 1</color></diffuse></lambert></technique></profile_COMMON></effect>
  </library_effects>
  <library_materials>
    <material id="body"><instance_effect url="#body-fx"/></material>
    <material id="front"><instance_effect url="#front-fx"/></material>
  </library_materials>
  <library_geometries>
    <geometry id="sofa"><mesh>
      <source id="sofa-pos"><float_array id="sofa-pos-a" count="{len(p) * 3}">{pos}</float_array>
        <technique_common><accessor source="#sofa-pos-a" count="{len(p)}" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>
      <vertices id="sofa-v"><input semantic="POSITION" source="#sofa-pos"/></vertices>
      <triangles material="body" count="10"><input semantic="VERTEX" source="#sofa-v" offset="0"/><p>{tris(quads[:5])}</p></triangles>
      <triangles material="front" count="2"><input semantic="VERTEX" source="#sofa-v" offset="0"/><p>{tris(quads[5:])}</p></triangles>
    </mesh></geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id="scene"><node id="sofa"><instance_geometry url="#sofa"><bind_material><technique_common>
      <instance_material symbol="body" target="#body"/><instance_material symbol="front" target="#front"/>
    </technique_common></bind_material></instance_geometry></node></visual_scene>
  </library_visual_scenes>
  <scene><instance_visual_scene url="#scene"/></scene>
</COLLADA>
"""


def _zip(files: dict) -> bytes:
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        for name, data in files.items():
            f.writestr(name, data)
    return z.getvalue()


def _png(rgb) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), rgb).save(out, "PNG")
    return out.getvalue()


def _colours(m):
    """A model's colours (bytes, linear): its reddest and its bluest."""
    _, colours, _ = read_model(m.glb)
    return colours[colours[:, 0].argmax()], colours[colours[:, 2].argmax()]


# ---- glTF, read here --------------------------------------------------------------------------

def test_a_gltf_with_its_bin_and_texture_in_a_zip_is_packed_whole_and_coloured_by_its_texture():
    from test_models import box

    p, f = box(-1, 0, -0.45, 1, 0.75, 0.45)
    pos, idx = np.asarray(p, "<f4"), np.asarray(f, "<u4").reshape(-1)
    binary = pos.tobytes() + idx.tobytes()
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
            "textures": [{"source": 0}], "images": [{"uri": "textures%5Cred%20fabric.png"}],  # (a Windows path, escaped)
            "buffers": [{"uri": "sofa.bin", "byteLength": len(binary)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": pos.nbytes},
                            {"buffer": 0, "byteOffset": pos.nbytes, "byteLength": idx.nbytes}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                           "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                          {"bufferView": 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"}]}
    sent = _zip({"sofa/sofa.gltf": json.dumps(gltf), "sofa/sofa.bin": binary, "sofa/textures/red fabric.png": _png((200, 20, 20)),
                 "__MACOSX/sofa/._sofa.gltf": b"\0"})
    found = as_glb(sent, "sofa.zip")
    m = make_model(found.glb, "SOFA")
    red, _ = _colours(m)
    assert found.kind == "glTF" and not found.notes and m.size == (2.0, 0.9, 0.75) and red[0] > 100 > red[2]
    # its .bin not sent: refused, saying what to send; its texture not sent: plain, and said
    with pytest.raises(ModelError, match=r"data file \(sofa.bin\) was not sent"):
        as_glb(json.dumps(gltf).encode(), "sofa.gltf")
    found = as_glb(_zip({"sofa.gltf": json.dumps(gltf), "sofa.bin": binary}), "sofa.zip")
    assert "not sent (red fabric.png)" in found.notes[0] and make_model(found.glb, "SOFA").size == (2.0, 0.9, 0.75)


def test_what_is_not_read_is_refused_saying_how_to_export_it():
    with pytest.raises(ModelError, match=r"SketchUp model: export it from SketchUp Pro .* FBX, OBJ or Collada"):
        as_glb(b"\xff\xfe\xff\x0eSketchUp Model", "chair.skp")
    with pytest.raises(ModelError, match="Revit family"):
        as_glb(_zip({"chair/chair.rfa": b"x", "chair/readme.txt": b"y"}), "chair.zip")
    with pytest.raises(ModelError, match=r"not a model file read here \(.txt\)"):
        as_glb(b"hello", "notes.txt")
    with pytest.raises(ModelError, match="not a binary glTF"):
        as_glb(b"solid sofa", "sofa.glb")
    with pytest.raises(ModelError, match="the .zip cannot be read"):
        as_glb(b"PK\x03\x04 not really", "sofa.zip")
    assert (kind_of("Sofa.FBX"), kind_of("a.zip"), kind_of("a.skp")) == ("FBX", "zip", None)


def test_a_zip_is_unpacked_inside_its_folder_alone_and_must_hold_one_model():
    from test_models import sofa_in_cm

    # a name climbing out of the folder stays in it; a link is not followed
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../../escaped/sofa.glb", sofa_in_cm())
        link = zipfile.ZipInfo("textures/link.png")
        link.external_attr = (0o120777 << 16)
        f.writestr(link, "/etc/passwd")
    found = as_glb(z.getvalue(), "sofa.zip")
    assert make_model(found.glb, "SOFA").size == (2.0, 0.9, 0.75)
    with pytest.raises(ModelError, match=r"holds 2 models \(a/sofa.glb, b/sofa.glb\): send one at a time"):
        as_glb(_zip({"a/sofa.glb": sofa_in_cm(), "b/sofa.glb": sofa_in_cm()}), "two.zip")
    with pytest.raises(ModelError, match="holds no model read here"):
        as_glb(_zip({"readme.txt": b"hello"}), "empty.zip")


def test_an_fbx_files_units_are_read_as_it_says_them_binary_or_text():
    assert fbx_unit(fbx(unit_cm=2.54).encode()) == pytest.approx(0.0254)
    assert fbx_unit(fbx(unit_cm=100).encode()) == pytest.approx(1.0)
    s = lambda t: b"S" + struct.pack("<I", len(t)) + t  # noqa: E731 (a binary FBX string property)
    binary = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400) + b"\0" * 40 + s(b"UnitScaleFactor") \
        + s(b"double") + s(b"Number") + s(b"") + b"D" + struct.pack("<d", 0.1)
    assert fbx_unit(binary) == pytest.approx(0.001) and fbx_unit(b"Kaydara FBX Binary  \x00") is None


def test_a_textures_own_colour_where_an_fbx_also_gives_one():
    from storeypath.model_files import _texture_alone

    gltf = {"asset": {"version": "2.0"}, "materials": [
        {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "baseColorFactor": [0.8, 0.8, 0.8, 0.5]}},
        {"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.1, 0.1, 1]}}]}
    out, _ = read_glb(_texture_alone(glb_of(gltf, b"")))
    assert [m["pbrMetallicRoughness"]["baseColorFactor"] for m in out["materials"]] == [[1, 1, 1, 0.5], [0.8, 0.1, 0.1, 1]]


# ---- converted by Assimp ----------------------------------------------------------------------

@needs_assimp
def test_an_obj_with_its_mtl_in_a_zip_comes_in_its_colours_read_in_the_units_its_size_says():
    o, mtl = obj()  # 200 x 75 x 90: centimetres
    found = as_glb(_zip({"sofa/sofa.obj": o, "sofa/sofa.mtl": mtl}), "sofa.zip")
    m = make_model(found.glb, "SOFA", stated=found.stated)
    red, blue = _colours(m)
    assert (found.kind, found.notes, m.size, m.units, m.units_from) == ("OBJ", [], (2.0, 0.9, 0.75), "cm", "size")
    assert red[0] > 150 > red[2] and blue[2] > 150 > blue[0]
    alone = as_glb(o.encode(), "sofa.obj")  # its .mtl not sent: grey, and said
    assert "its materials (sofa.mtl) were not sent" in alone.notes[0]


@needs_assimp
def test_an_fbx_in_inches_and_a_collada_file_in_centimetres_come_in_metres_as_they_say():
    inches = as_glb(fbx(unit_cm=2.54).encode(), "sofa.fbx")
    m = make_model(inches.glb, "SOFA", stated=inches.stated)
    assert (inches.kind, m.size, m.units, m.units_from) == ("FBX", (2.0, 0.9, 0.75), "in", "file")
    red, blue = _colours(m)
    assert red[0] > 150 > red[2] and blue[2] > 150 > blue[0]
    for meter, z_up in ((0.01, False), (1.0, True)):  # Collada's units and up axis, applied by Assimp
        found = as_glb(dae(meter=meter, z_up=z_up).encode(), "sofa.dae")
        m = make_model(found.glb, "SOFA", stated=found.stated)
        positions, colours, _ = read_model(m.glb)
        front = positions[colours[:, 2] > colours[:, 0]]
        assert found.kind == "Collada" and m.size == (2.0, 0.9, 0.75) and np.allclose(front[:, 2], positions[:, 2].max())


@needs_assimp
def test_a_file_assimp_cannot_read_is_refused_saying_so():
    with pytest.raises(ModelError, match=r"the FBX file could not be read \(.+\): export it again"):
        as_glb(b"; FBX 7.4.0 project file\nnothing else", "broken.fbx")


# ---- read in units chosen, stood up, and its size doubted --------------------------------------

def test_a_model_is_read_in_the_units_chosen_or_stood_up_and_a_size_no_furniture_has_is_doubted():
    from test_models import sofa_in_cm

    assert make_model(sofa_in_cm(), "SOFA", units="mm").size == (0.2, 0.09, 0.075)
    assert make_model(sofa_in_cm(), "SOFA", units="in").units_from == "chosen"
    stood = make_model(sofa_in_cm(), "SOFA", up="z")  # drawn Z up: its depth was its height
    assert stood.size == (2.0, 0.75, 0.9)
    with pytest.raises(ModelError, match="units"):
        make_model(sofa_in_cm(), "SOFA", units="furlong")
    assert doubts((2.0, 0.9, 0.75)) == [] and doubts((2.0, 0.9, 0.75), (1.8, 0.9, 0.8)) == []
    assert doubts((1.1, 0.5, 21.4))[0].startswith("it is 21.40 m tall")
    assert doubts((6.6, 4.0, 2.3), (1.2, 0.6, 0.45)) == [
        "it is 6.60 m across: larger than furniture is", "it is 5.5 times the size the type was (1.20 × 0.60 × 0.45 m)"]
