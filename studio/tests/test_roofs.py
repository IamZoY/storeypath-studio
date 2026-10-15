"""Roofs (format 0.11): what level of its building a floor is (its type: floor, mezzanine
or roof), and the roof as a level. A roof has its own ordinal at the top of its building
(a floor added above it puts it higher), its code RF (RF2 for a second); its open ground
is its deck (spaces of type roof, open to the sky, walked through), the rooms standing on
it indoor, its parapets its edge; stairs that reach it link to it, and a lift's machine
room on it is no stop. A penthouse is a floor like any (a smaller top floor), named so.
All of it made up: the warehouse of samples, with a mezzanine and a roof, and small roofs
drawn here."""

import json
import struct
import threading
import zipfile

import pytest
from shapely.geometry import box, shape

from storeypath.bake import baker, node
from storeypath.convert import convert_floor
from storeypath.export import build_features, export_package
from storeypath.extract import MACHINE_ROOM_NOTE
from storeypath.ids import default_roof_code, parse_id
from storeypath.navigation import Graph, NoRoute, route
from storeypath.samples import (MEZZANINE_AT, Cell, Door, mezzanine_floor, mezzanine_warehouse, office_floor,
                                roof_floor, write_floor_dxf, write_sheet_dxf)
from storeypath.sheets import title_level
from storeypath.stacks import stacks
from storeypath.types import FloorType
from storeypath.validate import validate_package
from storeypath.workspace import SourceDrawing, Workspace

from test_mezzanines import call, studio, wait  # noqa: F401 (the fixture)

with_node = pytest.mark.skipif(node()[0] is None or baker() is None, reason="needs Node.js and the viewer's baker")


def _building(*floors):
    """A project of one building with floors of these ordinals (ground floor 7 m high)."""
    ws = Workspace.new("Warehouse")
    b_id = ws.add_building(ws.add_location("MAIN", "Main site"), "WH", "Warehouse")
    for n in floors:
        ws.add_floor(b_id, n, height=7.0 if n == 0 else 3.5)
    ws.restack(b_id)
    return ws, b_id


def _codes(ws):
    return [(f.code, f.type.value, f.ordinal) for *_, f, _ in ws.iter_floors()]


# ---- codes, and the levels of a building -------------------------------------------------

def test_a_roofs_code_is_RF_and_a_second_RF2():
    assert default_roof_code() == "RF" and default_roof_code({"F00", "RF"}) == "RF2"
    assert default_roof_code({"RF", "RF2"}) == "RF3"
    for code in ("RF", "RF2"):  # an ID's segment as any other floor's
        assert parse_id(f"K7Q2XM-MAIN-WH-{code}-0001").segments[3] == code


def test_a_roof_is_added_at_the_top_of_its_building():
    ws, b_id = _building(0, 1)
    r = ws.add_floor(b_id, None, type="roof", height=3.0)
    f = ws.floor(r)
    assert r == f"{b_id}-RF" and (f.name, f.type, f.ordinal, f.host, f.above_host) == ("Roof", FloorType.ROOF, 2, None, None)
    assert f.roof and not f.mezzanine and not ws.floor(f"{b_id}-F01").roof
    ws.restack(b_id)
    assert [(c, ws.floor(f"{b_id}-{c}").elevation) for c, *_ in _codes(ws)] == [("F00", 0.0), ("F01", 7.0), ("RF", 10.5)]
    # a floor added at the roof's number, or above it: the roof goes up, to stay the top
    ws.add_floor(b_id, 2)
    ws.add_floor(b_id, 4)
    ws.restack(b_id)
    assert _codes(ws) == [("F00", "floor", 0), ("F01", "floor", 1), ("F02", "floor", 2), ("F04", "floor", 4),
                          ("RF", "roof", 5)]
    assert ws.floor(r).elevation == 21.0  # (a missing floor 3 as high as floor 2)
    # a second roof, over what stands on the first: RF2, above it
    r2 = ws.floor(ws.add_floor(b_id, None, type="roof", height=2.5))
    assert (r2.code, r2.ordinal) == ("RF2", 6)


def test_a_roof_is_its_buildings_top_and_one_level_of_its_number():
    ws, b_id = _building(0, 1)
    with pytest.raises(ValueError, match="a roof is its building's top: .* at floor 1, so its roof is 2 or more"):
        ws.add_floor(b_id, 1, type="roof")
    ws.add_floor(b_id, 3, type="roof")
    with pytest.raises(ValueError, match="floor ordinal 3 already used"):
        ws.add_floor(b_id, 3, type="roof", code="TOP")
    with pytest.raises(ValueError, match="floor code RF already used"):
        ws.add_floor(b_id, 4, type="roof", code="RF")
    with pytest.raises(ValueError, match="above_host is a mezzanine's"):
        ws.add_floor(b_id, 5, type="roof", above_host=2.0)
    with pytest.raises(ValueError, match="has no floor 3"):  # a mezzanine stands in a floor, not in a roof
        ws.add_floor(b_id, 3, type="mezzanine")
    with pytest.raises(ValueError, match="a floor's ordinal"):
        ws.add_floor(b_id, None)
    with pytest.raises(ValueError):
        ws.add_floor(b_id, 6, type="attic")


def test_a_levels_type_is_saved_and_read_back(tmp_path, databases):
    from storeypath import db as studio_db
    from storeypath.db.store import ProjectStore

    ws, b_id = _building(0, 1)
    ws.add_floor(b_id, 0, type="mezzanine", above_host=4.0)
    ws.add_floor(b_id, None, type="roof", height=3.0, parapet_height=1.2)
    ws.save(tmp_path / "w.spproj")
    again = Workspace.load(tmp_path / "w.spproj")
    assert _codes(again) == _codes(ws) == [("F00", "floor", 0), ("M00", "mezzanine", 0), ("F01", "floor", 1),
                                           ("RF", "roof", 2)]
    store = ProjectStore(studio_db.connect(data=tmp_path / "data"))
    store.create(ws)
    store.forget()  # read from the database
    back = store.current(ws.id)
    assert _codes(back) == _codes(ws)
    assert (back.floor(f"{b_id}-RF").parapet_height, back.floor(f"{b_id}-M00").host) == (1.2, "F00")


def test_what_level_a_plans_title_says():
    assert title_level("ROOF PLAN") == "roof" and title_level("ROOF TERRACE PLAN") == "roof"
    assert title_level("MEZZANINE FLOOR PLAN") == "mezzanine"
    assert title_level("PENTHOUSE FLOOR PLAN") == "penthouse"  # (a floor like any, named so)
    # the roof of what stands on the roof (levels.TOP): not the building's roof
    for title in ("STAIR ROOF PLAN", "LIFT ROOM ROOF PLAN", "PENTHOUSE ROOF PLAN", "UPPER ROOF PLAN"):
        assert title_level(title) == "upper roof", title
    assert title_level("FIRST FLOOR PLAN") is None and title_level(None) is None


def test_a_roof_plan_is_found_whole_with_its_parapet(tmp_path):
    # a roof's parapet round the few rooms on it (at its edge, or standing free) is mostly its
    # border, as a sheet's frame is: the plan's title under it, outside it, tells it is the
    # plan's own outer wall (a frame holds its plans' titles)
    from storeypath.cad import read_drawing
    from storeypath.profile import AUTO, load_profile
    from storeypath.sheets import find_views

    rules = load_profile(AUTO)
    for roof in (roof_floor(), _roof_with_rooms()):
        write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (10.0, 10.0), "GROUND FLOOR PLAN"),
                                                 (roof, (80.0, 10.0), "ROOF PLAN")], area_outlines=False)
        views = find_views(read_drawing(tmp_path / "sheet.dxf"), rules, 0.001, auto=True,
                           is_room_name=lambda t: rules.classify(t, [], "")[0] != "unspecified" or None)
        assert [(v.title, v.size, v.ordinal) for v in views] == [("GROUND FLOOR PLAN", (48.2, 20.2), 0),
                                                                 ("ROOF PLAN", (48.2, 20.2), 1)]


# ---- read ------------------------------------------------------------------------------------

def _roof_with_rooms():
    """A roof 48 × 20 m inside its parapet with rooms standing free on it: a stair room, a
    lift's top (LIFT, with its car drawn) and a penthouse of three rooms (a living room, a
    bedroom, a bathroom); the rest is open, no name."""
    return [
        Cell(0, 0, 48, 20, None, "roof"),
        Cell(4, 8, 10, 14, ["STAIR"], "stairs", Door(10, 11, "v", +1)),
        Cell(16, 5, 26, 15, ["LIVING ROOM"], "living_room", Door(21, 5, "h", -1), doors=[Door(26, 12, "v", -1)]),
        Cell(26, 9, 32, 15, ["BEDROOM"], "bedroom"),
        Cell(26, 5, 32, 9, ["BATHROOM"], "bathroom", Door(29, 9, "h", -1)),
        Cell(38, 6, 44, 12, ["LIFT"], "elevator", Door(38, 9, "v", -1), blocks=["ELEVATOR_CAR"]),
    ]


def _read(tmp_path, cells, level, name="roof.dxf"):
    write_floor_dxf(tmp_path / name, cells, title="ROOF PLAN", area_outlines=False)
    ws, b_id = _building(0)
    f_id = ws.add_floor(b_id, 1 if level == "floor" else None, type=level, source=SourceDrawing(path=name))
    convert_floor(ws, f_id, tmp_path)
    return ws, f_id


def test_a_roofs_open_ground_is_its_deck_and_the_rooms_on_it_are_indoor(tmp_path):
    # read as a floor, the open ground round the rooms standing on it is the outside round a
    # building (a garden), left out; read as the roof, it is the roof's deck
    ws, f_id = _read(tmp_path, _roof_with_rooms(), "floor")
    spaces = [r for r in ws.floor_objects(f_id) if r.kind == "space"]
    assert len(spaces) == 5 and shape(ws.floor(f_id).outline).area < 300
    ws, f_id = _read(tmp_path, _roof_with_rooms(), "roof")
    spaces = {r.name: r for r in ws.floor_objects(f_id) if r.kind == "space"}
    deck = spaces.pop(None)
    assert (deck.type, deck.type_source) == ("roof", "roof") and shape(deck.geometry).area > 650
    assert shape(ws.floor(f_id).outline).area == pytest.approx(48.2 * 20.2, rel=0.01)  # the whole roof
    # the penthouse's rooms and the stair room keep their own types; the lift is its top (no stop)
    assert {n: r.type for n, r in spaces.items()} == {"STAIR": "stairs", "LIVING ROOM": "unspecified",
                                                      "BEDROOM": "unspecified", "BATHROOM": "restroom",
                                                      "LIFT": "utility"}
    assert MACHINE_ROOM_NOTE in spaces["LIFT"].issues
    # exported: the deck open to the sky, behind its parapets; the rooms under a ceiling
    out = tmp_path / "roof.storeypath"
    export_package(ws, out, bake=False)
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        floor = next(f for f in json.loads(z.read("floors.geojson"))["features"] if f["id"] == f_id)["properties"]
        exported = {f["properties"]["name"]: f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]}
    assert floor["type"] == "roof" and floor["parapets"] is not None and floor["parapet_height_m"] == 1.1
    assert exported[None]["outdoor"] and not any(p["outdoor"] for n, p in exported.items() if n)
    feature = next(f for f in build_features(ws)["floors"] if f["id"] == f_id)
    px0, py0, px1, py1 = shape(feature["properties"]["parapets"]).bounds
    wx0, _, wx1, _ = shape(feature["properties"]["walls"]).bounds
    across = px1 - px0  # (degrees, the building unturned: 48.2 m)
    assert shape(feature["geometry"]).bounds == pytest.approx((px0, py0, px1, py1), abs=across / 400)  # its edge
    # the walls full height: the rooms' own, standing free 4 m in from it
    assert (wx0 - px0) / across == pytest.approx(4 / 48.2, abs=0.005) and (px1 - wx1) / across > 0.07


def test_a_roof_whose_rooms_stand_at_its_edge_reads_the_same(tmp_path):
    # the warehouse's roof: its rooms against the parapet; a plant room and a lift's
    # machine room named so (utility), STAIR A's room; the rest its deck
    ws, f_id = _read(tmp_path, roof_floor(), "roof")
    spaces = {r.name: (r.type, round(shape(r.geometry).area)) for r in ws.floor_objects(f_id) if r.kind == "space"}
    assert {n: t for n, (t, _) in spaces.items()} == {None: "roof", "STAIR A": "stairs", "PLANT ROOM": "utility",
                                                      "LIFT MACHINE ROOM": "utility"}
    assert spaces[None][1] > 750


def test_a_smaller_top_floor_over_a_bigger_one_invents_no_walls_or_slab(tmp_path):
    # a penthouse: a floor like any (named so), over the west end of the floor under it; its
    # outline and walls are its own block's, the floor under it open round it
    write_floor_dxf(tmp_path / "ground.dxf", office_floor(0), origin=(20.0, 10.0))
    write_floor_dxf(tmp_path / "top.dxf", mezzanine_floor(), origin=(20.0, 10.0 + MEZZANINE_AT[1]),
                    title="PENTHOUSE FLOOR PLAN")
    ws, b_id = _building()
    ground = ws.add_floor(b_id, 0, source=SourceDrawing(path="ground.dxf"))
    top = ws.add_floor(b_id, 1, name="Penthouse", source=SourceDrawing(path="top.dxf"))
    for f_id in (ground, top):
        convert_floor(ws, f_id, tmp_path)
    block = box(20, 21.5, 40, 30)
    assert top == f"{b_id}-F01" and ws.floor(top).type == FloorType.FLOOR
    assert shape(ws.floor(top).outline).symmetric_difference(block).area < 0.05 * block.area
    assert box(19.8, 21.3, 40.2, 30.2).contains(shape(ws.floor(top).walls))
    assert shape(ws.floor(ground).outline).area > 4 * block.area
    out = tmp_path / "penthouse.storeypath"
    export_package(ws, out, bake=False)
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        floors = {f["id"]: f for f in json.loads(z.read("floors.geojson"))["features"]}
    assert floors[top]["properties"]["parapets"] is None  # no terrace on it: nothing low invented round it


def _gltf(data: bytes) -> dict:
    length, = struct.unpack_from("<I", data, 12)
    return json.loads(data[20:20 + length])


@with_node
def test_in_3d_a_smaller_top_floors_slab_is_its_own_and_a_roofs_rooms_stand_on_its_deck(tmp_path):
    ws = mezzanine_warehouse(tmp_path)
    out = tmp_path / "w.storeypath"
    export_package(ws, out)
    floors = {f.code: fid for *_, f, fid in ws.iter_floors()}
    with zipfile.ZipFile(out) as z:
        built = {code: _gltf(z.read(f"world/{fid}.glb")) for code, fid in floors.items()}

    def extent(gltf, name):  # the x and z a node's mesh spans (its accessor's bounds)
        node = next(n for n in gltf["nodes"] if n["name"] == name)
        acc = gltf["accessors"][gltf["meshes"][node["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
        return acc["max"][0] - acc["min"][0], acc["max"][2] - acc["min"][2]

    # (on the map, the warehouse turned 15°: what they span, not their sides)
    ground, mezzanine, roof = (extent(built[c], "slab") for c in ("F00", "M00", "RF"))
    assert mezzanine[0] < 0.6 * ground[0] and mezzanine[1] < 0.6 * ground[1]  # the mezzanine's slab: its block alone
    assert roof == pytest.approx(ground, abs=0.5)  # the roof's slab: the roof, as wide as the floors under it
    names = {n["name"] for n in built["RF"]["nodes"]}
    assert {"parapet", "wall"} <= names  # its parapets low, its rooms' walls full


# ---- exported, routed, opened, validated --------------------------------------------------------

@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("warehouse")
    ws = mezzanine_warehouse(folder)
    out = folder / "warehouse.storeypath"
    export_package(ws, out, bake=False)
    return ws, out


def test_the_package_says_what_level_each_floor_is(warehouse):
    ws, out = warehouse
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        floors = json.loads(z.read("floors.geojson"))["features"]
        spaces = [f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]]
        manifest = json.loads(z.read("manifest.json"))
        rows = z.read("objects.csv").decode().splitlines()
    assert [(f["properties"]["code"], f["properties"]["type"]) for f in floors] == \
        [("F00", "floor"), ("M00", "mezzanine"), ("F01", "floor"), ("RF", "roof")]
    roof = floors[3]["properties"]
    assert (roof["name"], roof["ordinal"], roof["elevation"], roof["height"], roof["parapet_height_m"]) == \
        ("Roof", 2, 10.5, 3.0, 1.1)
    assert manifest["types"]["floor"] == ["floor", "mezzanine", "roof"] and "roof" in manifest["types"]["space"]
    on_roof = {(p["type"], p["outdoor"]) for p in spaces if p["floor_id"].endswith("-RF")}
    assert on_roof == {("roof", True), ("stairs", False), ("utility", False)}
    b_id = floors[0]["properties"]["building_id"]
    assert any(r.startswith(f"{b_id}-RF-") for r in rows)  # its rooms by its code: …-RF-0001


def test_stairs_reach_the_roof_and_the_lifts_do_not_stop_there(warehouse):
    ws, out = warehouse
    b_id = f"{ws.id}-MAIN-WH"
    found = stacks(ws, b_id)
    stairs = next(s for s in found if s.type == "stairs")
    assert sorted(m.split("-")[3] for m in stairs.members) == ["F00", "F01", "M00", "RF"]
    assert all(not any(m.split("-")[3] == "RF" for m in s.members) for s in found if s.type == "elevator")
    with zipfile.ZipFile(out) as z:
        nav = json.loads(z.read("navigation.json"))
        spaces = {f["id"]: f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]}
    office = next(i for i, p in sorted(spaces.items()) if p["floor_id"].endswith("-F00") and p["number"] == "001")
    deck = next(i for i, p in spaces.items() if p["type"] == "roof")
    g = Graph(nav)
    up = route(g, office, deck)
    assert [(c["by"], c["to_floor_id"].rsplit("-", 1)[1], c["floors"]) for c in up["changes"]] == [("stairs", "RF", 3)]
    assert "Take the stairs up to Roof" in [s["text"] for s in up["steps"]]
    assert up["steps"][-1]["text"].startswith("The roof")  # (its deck: no name, its type)
    with pytest.raises(NoRoute):  # no lift stops there: its machine room is no stop
        route(g, office, deck, accessible=True)


def test_a_package_with_a_roof_opens_as_a_project_with_it(warehouse):
    from storeypath.bundle import read_file

    ws, out = warehouse
    how, opened, code = read_file(out)
    assert how == "package" and code == ws.id
    assert _codes(opened) == [("F00", "floor", 0), ("M00", "mezzanine", 0), ("F01", "floor", 1), ("RF", "roof", 2)]
    roof = next(f for *_, f, _ in opened.iter_floors() if f.roof)
    assert (roof.host, roof.above_host, roof.parapet_height) == (None, None, 1.1)


def _rewritten(tmp_path, source, change):
    out = tmp_path / f"case-{len(list(tmp_path.glob('case-*')))}.storeypath"
    with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w") as w:
        for n in z.namelist():
            data = z.read(n)
            if n == "floors.geojson":
                doc = json.loads(data)
                change({f["properties"]["code"]: f for f in doc["features"]})
                data = json.dumps(doc).encode()
            w.writestr(n, data)
    return out


@pytest.mark.parametrize("change, said", [
    (lambda f: f["RF"]["properties"].update(ordinal=1), "is floor 1 of its building already"),
    (lambda f: f["F01"]["properties"].update(ordinal=3), "a roof, with"),
    (lambda f: f["RF"]["properties"].update(host_floor_id=f["F01"]["id"]), "a roof, not a mezzanine, but with a host"),
    (lambda f: f["M00"]["properties"].update(host_floor_id=f["RF"]["id"], ordinal=2),
     "a roof (a mezzanine stands in a floor)"),
    (lambda f: f["RF"]["properties"].update(type="attic"), "type"),
])
def test_a_roof_out_of_its_place_is_refused(warehouse, tmp_path, change, said):
    _, out = warehouse
    errors = validate_package(_rewritten(tmp_path, out, change))
    assert any(said in e for e in errors), errors


def test_a_second_roof_over_the_first_is_valid(warehouse, tmp_path):
    _, out = warehouse

    def upper(f):  # the first floor made a roof under the roof: two roofs, nothing else above
        f["F01"]["properties"].update(type="roof")

    assert validate_package(_rewritten(tmp_path, out, upper)) == []


# ---- added from a drawing (Find plans), and on the command line ---------------------------------

def test_a_roof_and_a_penthouse_are_added_from_their_plans(studio, tmp_path):  # noqa: F811
    base, app = studio
    code = call(f"{base}/api/projects", {"name": "Warehouse"})[1]["code"]
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (10.0, 10.0), "GROUND FLOOR PLAN"),
                                             (mezzanine_floor(), (80.0, 10.0 + MEZZANINE_AT[1]), "PENTHOUSE FLOOR PLAN"),
                                             (roof_floor(), (130.0, 10.0), "ROOF PLAN")], area_outlines=False)
    call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=(tmp_path / "sheet.dxf").read_bytes())
    found = wait(base, call(f"{base}/api/projects/{code}/drawings/sheet.dxf/plans", {})[1])
    plans = {p["title"]: p for p in found["plans"]}
    # the penthouse a floor like any, named so, the next one up; the roof above it
    assert {t: (p["type"], p["floor"], p["name"], p["upper"]) for t, p in plans.items()} == {
        "GROUND FLOOR PLAN": ("floor", 0, None, False), "PENTHOUSE FLOOR PLAN": ("floor", 1, "Penthouse", False),
        "ROOF PLAN": ("roof", 2, None, False)}

    def plan(title, **more):
        p = plans[title]
        return {"index": p["index"], "title": title, "region": p["region"], "building": "Warehouse",
                "ordinal": p["floor"], "type": p["type"], **more}

    roof = plan("ROOF PLAN", code="RF", name="Roof", height=3.0, parapet=1.1)
    # a roof not above every floor: refused, nothing changed
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("GROUND FLOOR PLAN"), {**roof, "ordinal": 0}]})
    assert status == 400 and "is a roof: give it a floor above Warehouse's top floor (0), 1 or more" in said["error"]
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("GROUND FLOOR PLAN"), {**roof, "type": "attic"}]})
    assert status == 400 and "its type is one of floor, mezzanine, roof" in said["error"]
    assert not any(loc.buildings for loc in app.workspace(code).locations)
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        roof, plan("GROUND FLOOR PLAN", name="Ground floor"), plan("PENTHOUSE FLOOR PLAN", name="Penthouse")]})[1])
    ws = app.workspace(code)
    b = ws.locations[0].buildings[0]
    b_id = f"{ws.id}-{ws.locations[0].code}-{b.code}"
    assert _codes(ws) == [("F00", "floor", 0), ("F01", "floor", 1), ("RF", "roof", 2)]
    assert ws.floor(f"{b_id}-F01").name == "Penthouse"
    rf = ws.floor(f"{b_id}-RF")
    assert (rf.name, rf.height, rf.parapet_height, rf.elevation) == ("Roof", 3.0, 1.1, 7.0)
    assert any(r.type == "roof" for r in ws.floor_objects(f"{b_id}-RF"))  # its deck
    _, page = call(f"{base}/api/projects/{code}")
    listed = page["locations"][0]["buildings"][0]["floors"]
    assert [(f["code"], f["type"]) for f in listed] == [("F00", "floor"), ("F01", "floor"), ("RF", "roof")]
    # again: the roof's drawing replaced by its code, its rooms keep their IDs; a second roof
    # of that code refused
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        {**roof, "building_id": b_id, "ordinal": 5}]})
    assert status == 400 and "Warehouse already has Roof (RF): give ROOF PLAN another code" in said["error"]
    rooms = {r.id for r in ws.floor_objects(f"{b_id}-RF")}
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        {**roof, "building_id": b_id, "replace": True, "parapet": 1.2}]})[1])
    ws = app.workspace(code)
    assert {r.id for r in ws.floor_objects(f"{b_id}-RF")} == rooms and ws.floor(f"{b_id}-RF").parapet_height == 1.2
    # a floor added above the penthouse: the roof goes up, to stay the top
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("GROUND FLOOR PLAN", building_id=b_id, ordinal=2, name="Second floor")]})[1])
    ws = app.workspace(code)
    assert _codes(ws) == [("F00", "floor", 0), ("F01", "floor", 1), ("F02", "floor", 2), ("RF", "roof", 3)]
    assert ws.floor(f"{b_id}-RF").elevation == 10.5


def test_a_roof_is_added_on_the_command_line(tmp_path):
    from typer.testing import CliRunner

    from storeypath.cli import app

    def run(*args, ok=True):
        result = CliRunner().invoke(app, [str(a) for a in args])
        assert (result.exit_code == 0) == ok, result.output
        return result.output.strip()

    ws_file = tmp_path / "w.spproj"
    write_floor_dxf(tmp_path / "ground.dxf", office_floor(0))
    write_floor_dxf(tmp_path / "roof.dxf", roof_floor(), area_outlines=False)
    run("new", ws_file, "--name", "Warehouse")
    b = run("add-building", ws_file, run("add-location", ws_file, "MAIN", "--name", "Main site"), "WH", "--name", "Warehouse")
    run("add-floor", ws_file, b, tmp_path / "ground.dxf", "--ordinal", "0", "--units", "mm", "--no-model")
    assert "--type is one of floor, mezzanine, roof" in run("add-floor", ws_file, b, tmp_path / "roof.dxf", "--type",
                                                           "attic", "--units", "mm", "--no-model", ok=False)
    assert "a floor needs --ordinal" in run("add-floor", ws_file, b, tmp_path / "roof.dxf", "--units", "mm",
                                            "--no-model", ok=False)
    r = run("add-floor", ws_file, b, tmp_path / "roof.dxf", "--type", "roof", "--height", "3", "--units", "mm",
            "--no-model")
    assert r == f"{b}-RF"
    f = Workspace.load(ws_file).floor(r)
    assert (f.name, f.type, f.ordinal) == ("Roof", FloorType.ROOF, 1)
    run("convert", ws_file)
    assert "export #1" in run("export", ws_file, "-o", tmp_path / "w.storeypath")
    assert "valid" in run("validate", tmp_path / "w.storeypath")
