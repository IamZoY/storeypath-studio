"""Mezzanines (format 0.11): a floor standing in another floor of its building, over part of
it. It keeps that floor's ordinal and stands its own height above it; its code is its own
(M00 over F00); floors come in order of ordinal, then elevation, everywhere (Ground,
Mezzanine, First); stairs that serve it link Ground to Mezzanine to First, lifts that pass
it Ground to First. All of it made up: a warehouse with a mezzanine (samples)."""

import json
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest
from shapely.geometry import box, shape

from storeypath.assets import asset_dir
from storeypath.export import export_package
from storeypath.ids import default_mezzanine_code, parse_id
from storeypath.navigation import Graph, route
from storeypath.samples import (MEZZANINE_AT, add_footprint, mezzanine_floor, mezzanine_warehouse, office_floor,
                                write_floor_dxf, write_sheet_dxf)
from storeypath.sheets import mezzanine_of, read_title
from storeypath.stacks import stacks
from storeypath.validate import validate_package
from storeypath.workspace import SourceDrawing, Workspace

WAREHOUSE = asset_dir("spec") / "conformance" / "packages" / "mezzanine-warehouse.storeypath"


def _building(*floors):
    """A project of one building with floors of these ordinals (ground floor 7 m high)."""
    ws = Workspace.new("Warehouse")
    b_id = ws.add_building(ws.add_location("MAIN", "Main site"), "WH", "Warehouse")
    for n in floors:
        ws.add_floor(b_id, n, height=7.0 if n == 0 else 3.5)
    ws.restack(b_id)
    return ws, b_id


# ---- codes, and the floors of a building ------------------------------------------------

def test_a_mezzanines_code_is_by_the_floor_it_stands_in():
    assert default_mezzanine_code(0) == "M00" and default_mezzanine_code(1) == "M01"
    assert default_mezzanine_code(-1) == "MB01" and default_mezzanine_code(12) == "M12"
    assert default_mezzanine_code(0, {"F00", "M00"}) == "M00B"
    assert default_mezzanine_code(0, {"M00", "M00B"}) == "M00C"
    for code in ("M00", "M00B", "MB01"):  # an ID's segment as any other floor's
        assert parse_id(f"K7Q2XM-MAIN-WH-{code}-0001").segments[3] == code


def test_a_mezzanine_stands_in_a_floor_of_its_building():
    ws, b_id = _building(0, 1)
    m = ws.add_floor(b_id, 0, mezzanine=True, above_host=4.0)
    f = ws.floor(m)
    assert m == f"{b_id}-M00" and (f.name, f.ordinal, f.host, f.above_host, f.elevation) == ("Mezzanine", 0, "F00", 4.0, 4.0)
    assert f.mezzanine and not ws.floor(f"{b_id}-F00").mezzanine and ws.host_of(m).code == "F00"
    # in order: the ground floor, the mezzanine on it, the first floor
    assert [fid.rsplit("-", 1)[1] for *_, fid in ws.iter_floors()] == ["F00", "M00", "F01"]
    # a second on the same floor: its own code; by default half its floor's height above it
    second = ws.floor(ws.add_floor(b_id, 0, mezzanine=True, name="Gallery"))
    assert (second.code, second.name, second.above_host, second.elevation) == ("M00B", "Gallery", 3.5, 3.5)
    # one over a basement
    ws.add_floor(b_id, -1, height=4.0)
    ws.restack(b_id)
    below = ws.floor(ws.add_floor(b_id, -1, mezzanine=True, above_host=2.5))
    assert (below.code, below.elevation) == ("MB01", -1.5)
    assert [fid.rsplit("-", 1)[1] for *_, fid in ws.iter_floors()] == ["B01", "MB01", "F00", "M00B", "M00", "F01"]


def test_one_floor_of_an_ordinal_and_any_number_of_mezzanines_on_it():
    ws, b_id = _building(0, 1)
    with pytest.raises(ValueError, match="floor ordinal 0 already used"):
        ws.add_floor(b_id, 0)
    with pytest.raises(ValueError, match="has no floor 2"):
        ws.add_floor(b_id, 2, mezzanine=True)
    with pytest.raises(ValueError, match="floor code F01 already used"):
        ws.add_floor(b_id, 0, mezzanine=True, code="F01")
    with pytest.raises(ValueError, match="more than 0"):
        ws.add_floor(b_id, 0, mezzanine=True, above_host=0)
    with pytest.raises(ValueError, match="above_host is a mezzanine's"):
        ws.add_floor(b_id, 2, above_host=3.0)
    with pytest.raises(ValueError, match="elevation is its host floor's"):
        ws.add_floor(b_id, 0, mezzanine=True, elevation=4.0)
    for _ in range(3):
        ws.add_floor(b_id, 0, mezzanine=True)
    ws.add_floor(b_id, 1, mezzanine=True, code="GALLERY")
    assert sorted(f.code for f in ws.building(b_id).floors) == ["F00", "F01", "GALLERY", "M00", "M00B", "M00C"]
    ws.add_floor(b_id, 2)  # the second floor: its ordinal still free
    ws.restack(b_id)
    assert ws.floor(f"{b_id}-F02").elevation == 10.5


def test_a_mezzanine_moves_with_the_floor_it_stands_in():
    ws, b_id = _building(0, 1, 2)
    m = ws.add_floor(b_id, 1, mezzanine=True, above_host=2.5)
    assert ws.floor(m).elevation == 9.5
    ws.floor(f"{b_id}-F00").height = 6.0  # the floors above it come down
    ws.restack(b_id)
    assert [(f.code, f.elevation) for *_, f, _ in ws.iter_floors()] == [("F00", 0.0), ("F01", 6.0), ("M01", 8.5),
                                                                         ("F02", 9.5)]


def test_a_mezzanine_is_saved_and_read_back(tmp_path, databases):
    from storeypath import db as studio_db
    from storeypath.db.store import ProjectStore

    ws, b_id = _building(0, 1)
    ws.add_floor(b_id, 0, mezzanine=True, above_host=4.0, height=3.0)
    ws.save(tmp_path / "w.spproj")
    again = Workspace.load(tmp_path / "w.spproj")
    assert again.floor(f"{b_id}-M00").model_dump() == ws.floor(f"{b_id}-M00").model_dump()
    store = ProjectStore(studio_db.connect(data=tmp_path / "data"))
    store.create(ws)
    store.forget()  # read from the database
    back = store.current(ws.id).floor(f"{b_id}-M00")
    assert (back.host, back.above_host, back.elevation, back.height) == ("F00", 4.0, 4.0, 3.0)
    assert store.current(ws.id).floor(f"{b_id}-F00").host is None


def test_a_mezzanines_plan_is_known_by_its_title():
    assert read_title("MEZZANINE FLOOR PLAN") == ("floor_plan", 0) and mezzanine_of("MEZZANINE FLOOR PLAN") == 0
    assert mezzanine_of("FIRST FLOOR MEZZANINE PLAN") == 1 and mezzanine_of("MEZZ. FLOOR PLAN") == 0
    assert mezzanine_of("GROUND FLOOR PLAN") is None and mezzanine_of(None) is None


# ---- read, exported, routed --------------------------------------------------------------

@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("warehouse")
    ws = mezzanine_warehouse(folder)
    out = folder / "warehouse.storeypath"
    export_package(ws, out, bake=False)
    return ws, out


def test_a_mezzanines_outline_is_its_own_rooms_not_the_building_round_it(warehouse):
    # Its drawing holds the building's outline too, alone on a layer of its own: neither its
    # floor's outline nor its walls, which are its block of rooms'.
    ws, _ = warehouse
    m = next(f for *_, f, _ in ws.iter_floors() if f.mezzanine)
    ground = next(f for *_, f, _ in ws.iter_floors() if f.code == "F00")
    block = box(20, 21.5, 40, 30)  # the mezzanine's rooms, 20 × 8.5 m, in the drawing's metres
    outline = shape(m.outline)
    assert outline.symmetric_difference(block).area < 0.1 * block.area
    assert shape(m.walls).bounds[2] < 40.5 and shape(m.walls).bounds[1] > 21
    assert shape(ground.outline).area > 5 * outline.area


def test_an_outline_alone_on_its_layer_round_a_plan_is_not_its_floor(tmp_path):
    # the same with a plan read whole from its drawing (no region): the building's outline
    # round the block of rooms, on a layer of its own, is not read as walls or as the floor
    from storeypath.convert import convert_floor

    write_floor_dxf(tmp_path / "upper.dxf", mezzanine_floor(), origin=(5.0, 5.0), title="MEZZANINE FLOOR PLAN")
    add_footprint(tmp_path / "upper.dxf", [(-30, -10), (60, -10), (60, 40), (-30, 40)], origin=(5.0, 5.0),
                  layer="OUTLINE")
    ws = Workspace.new("Outline")
    b_id = ws.add_building(ws.add_location("MAIN", "Main site"), "WH", "Warehouse")
    f_id = ws.add_floor(b_id, 0, source=SourceDrawing(path="upper.dxf"))
    convert_floor(ws, f_id, tmp_path)
    f = ws.floor(f_id)
    assert shape(f.outline).bounds == pytest.approx((5.0, 5.0, 25.0, 13.5), abs=0.2)
    assert len([r for r in ws.floor_objects(f_id) if r.kind == "space"]) == 4


def test_the_package_lists_the_floors_in_order_with_the_mezzanines_mark(warehouse):
    ws, out = warehouse
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        floors = json.loads(z.read("floors.geojson"))["features"]
        nav = json.loads(z.read("navigation.json"))
        rows = list(z.read("objects.csv").decode().splitlines())
    b_id = floors[0]["properties"]["building_id"]
    assert [(f["properties"]["code"], f["properties"]["ordinal"], f["properties"]["elevation"]) for f in floors] == \
        [("F00", 0, 0.0), ("M00", 0, 3.5), ("F01", 1, 7.0)]
    m = floors[1]["properties"]
    assert (m["mezzanine"], m["host_floor_id"], m["above_host_m"], m["height"]) == (True, f"{b_id}-F00", 3.5, 3.5)
    for f in (floors[0], floors[2]):
        assert (f["properties"]["mezzanine"], f["properties"]["host_floor_id"], f["properties"]["above_host_m"]) == \
            (False, None, None)
    assert [f["id"] for f in nav["floors"]] == [f["id"] for f in floors]  # (the order ways count floors by)
    assert any(r.startswith(f"{b_id}-M00-") for r in rows)  # its rooms by its code: …-M00-0001


def test_the_stairs_serve_the_mezzanine_and_the_lifts_pass_it(warehouse):
    ws, out = warehouse
    b_id = next(iter(ws.locations[0].buildings)).code
    found = {s.type: s for s in stacks(ws, f"{ws.id}-MAIN-{b_id}")}
    assert sorted(m.split("-")[3] for m in found["stairs"].members) == ["F00", "F01", "M00"]
    lifts = [s for s in stacks(ws, f"{ws.id}-MAIN-{b_id}") if s.type == "elevator"]
    assert lifts and all(sorted(m.split("-")[3] for m in s.members) == ["F00", "F01"] for s in lifts)
    with zipfile.ZipFile(out) as z:
        nav = json.loads(z.read("navigation.json"))
    rides = {(e["kind"], *sorted(n.rsplit("-", 1)[1] for n in (e["from"], e["to"]))) for e in nav["edges"]
             if e["kind"] in ("stairs", "lift")}
    assert rides == {("stairs", "F00", "M00"), ("stairs", "F01", "M00"), ("lift", "F00", "F01")}
    lift = next(e for e in nav["edges"] if e["kind"] == "lift")
    assert (lift["seconds"], lift["length_m"]) == (38.0, 7.0)  # waiting, and two floors passed: the mezzanine is one


def test_a_way_up_goes_by_the_mezzanine_and_past_it(warehouse):
    ws, out = warehouse
    with zipfile.ZipFile(out) as z:
        nav = json.loads(z.read("navigation.json"))
        spaces = {f["id"]: f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]}

    def room(floor, number=None):
        return next(i for i, p in sorted(spaces.items()) if p["floor_id"].endswith(floor) and p["type"] == "office"
                    and number in (None, p["number"]))

    g = Graph(nav)
    up = route(g, room("-F00", "001"), room("-F01", "112"))
    (ride,) = up["changes"]
    assert (ride["by"], ride["floors"], ride["direction"]) == ("stairs", 2, "up")
    assert any(n.startswith("stairs:") and n.endswith("-M00") for n in up["nodes"])  # up through the mezzanine
    assert "Take the stairs up to First floor" in [s["text"] for s in up["steps"]]
    lift = route(g, room("-F00", "001"), room("-F01", "112"), accessible=True)
    assert [(c["by"], c["floors"]) for c in lift["changes"]] == [("lift", 2)]
    assert not any(n.endswith("-M00") for n in lift["nodes"])
    there = route(g, room("-F00", "001"), room("-M00"))
    assert [(c["by"], c["to_floor_id"].rsplit("-", 1)[1], c["floors"]) for c in there["changes"]] == [("stairs", "M00", 1)]
    assert "Take the stairs up to Mezzanine" in [s["text"] for s in there["steps"]]
    from storeypath.navigation import NoRoute

    with pytest.raises(NoRoute):  # no lift stops there
        route(g, room("-F00", "001"), room("-M00"), accessible=True)


def test_a_package_with_a_mezzanine_opens_as_a_project_with_it(warehouse, tmp_path):
    from storeypath.bundle import read_file

    ws, out = warehouse
    how, opened, code = read_file(out)
    assert how == "package" and code == ws.id
    m = next(f for *_, f, _ in opened.iter_floors() if f.mezzanine)
    assert (m.code, m.ordinal, m.host, m.above_host, m.elevation) == ("M00", 0, "F00", 3.5, 3.5)
    assert [f.code for *_, f, _ in opened.iter_floors()] == ["F00", "M00", "F01"]


# ---- the validator --------------------------------------------------------------------------

def _rewritten(tmp_path, change):
    """The conformance warehouse with its floors changed by ``change`` (their features)."""
    out = tmp_path / f"case-{len(list(tmp_path.glob('case-*')))}.storeypath"
    with zipfile.ZipFile(WAREHOUSE) as z, zipfile.ZipFile(out, "w") as w:
        for n in z.namelist():
            data = z.read(n)
            if n == "floors.geojson":
                doc = json.loads(data)
                change({f["properties"]["code"]: f for f in doc["features"]})
                data = json.dumps(doc).encode()
            w.writestr(n, data)
    return out


def test_the_conformance_warehouse_is_valid_and_in_order():
    assert validate_package(WAREHOUSE) == []
    with zipfile.ZipFile(WAREHOUSE) as z:
        floors = json.loads(z.read("floors.geojson"))["features"]
    assert [f["properties"]["code"] for f in floors] == ["F00", "M00", "F01"]


@pytest.mark.parametrize("change, said", [
    (lambda f: f["F01"]["properties"].update(ordinal=0), "is floor 0 of its building already"),
    (lambda f: f["M00"]["properties"].update(mezzanine=False, host_floor_id=None, above_host_m=None),
     "is floor 0 of its building already"),
    (lambda f: f["M00"]["properties"].update(code="F00"), "code F00 is"),
    (lambda f: f["M00"]["properties"].update(host_floor_id=f["M00"]["id"].replace("M00", "F09")),
     "which is not a floor of its building"),
    (lambda f: f["M00"]["properties"].update(host_floor_id=f["F01"]["id"]), "a mezzanine of floor 1"),
    (lambda f: f["M00"]["properties"].update(host_floor_id=f["M00"]["id"]), "itself a mezzanine"),
    (lambda f: f["M00"]["properties"].update(elevation=4.0), "not its host floor's"),
    (lambda f: f["M00"]["properties"].update(above_host_m=None), "without its height above its host floor"),
    (lambda f: f["F01"]["properties"].update(host_floor_id=f["F00"]["id"]), "not a mezzanine, but with a host"),
    (lambda f: f["F01"]["properties"].update(above_host_m=2.0), "not a mezzanine, but with a host"),
])
def test_a_floor_out_of_its_buildings_order_is_refused(tmp_path, change, said):
    errors = validate_package(_rewritten(tmp_path, change))
    assert errors and all("floors.geojson" not in e or said in e for e in errors) and any(said in e for e in errors), errors


def test_a_mezzanines_height_above_its_floor_is_more_than_nothing(tmp_path):
    errors = validate_package(_rewritten(tmp_path, lambda f: f["M00"]["properties"].update(above_host_m=0)))
    assert any("above_host_m" in e and "greater than 0" in e for e in errors), errors


# ---- added from a drawing, over HTTP (Find plans) ----------------------------------------------

class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture
def studio(tmp_path):
    from sessions import admin_server
    from storeypath.server import Studio

    s = Studio(tmp_path / "data", model=NoModel())
    srv = admin_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", s
    srv.shutdown()
    srv.server_close()


def call(url, body=None, raw=None):
    if raw is not None:
        req = urllib.request.Request(url, data=raw, method="PUT", headers={"X-StoreyPath": "1"})
    else:
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-StoreyPath": "1"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(base, job):
    for _ in range(900):
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.1)
        _, job = call(f"{base}/api/jobs/{job['id']}")
    assert job["state"] == "done", job
    return job["result"]


def test_a_mezzanine_is_added_from_its_plan_and_told_apart_from_its_floor(studio, tmp_path):
    base, app = studio
    code = call(f"{base}/api/projects", {"name": "Warehouse"})[1]["code"]
    # one sheet: the ground floor, the mezzanine over its west end, the first floor, side by side
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (10.0, 10.0), "GROUND FLOOR PLAN"),
                                             (mezzanine_floor(), (80.0, 10.0 + MEZZANINE_AT[1]), "MEZZANINE FLOOR PLAN"),
                                             (office_floor(1), (130.0, 10.0), "FIRST FLOOR PLAN")])
    call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=(tmp_path / "sheet.dxf").read_bytes())
    found = wait(base, call(f"{base}/api/projects/{code}/drawings/sheet.dxf/plans", {})[1])
    plans = {p["title"]: p for p in found["plans"]}
    assert (plans["MEZZANINE FLOOR PLAN"]["mezzanine"], plans["MEZZANINE FLOOR PLAN"]["floor"]) == (True, 0)
    assert not plans["GROUND FLOOR PLAN"]["mezzanine"] and plans["FIRST FLOOR PLAN"]["floor"] == 1

    def plan(title, **more):
        p = plans[title]
        return {"index": p["index"], "title": title, "region": p["region"], "building": "Warehouse",
                "ordinal": p["floor"], **more}

    mezzanine = plan("MEZZANINE FLOOR PLAN", mezzanine=True, code="M00", name="Mezzanine", above_host=3.5, height=3.5)
    # a mezzanine of a floor the building does not have, and two of one code: refused, nothing changed
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [{**mezzanine, "ordinal": 2}]})
    assert status == 400 and "a mezzanine of floor 2, which Warehouse does not have" in said["error"]
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("GROUND FLOOR PLAN", height=7.0), mezzanine, {**mezzanine, "title": "MEZZANINE FLOOR PLAN"}]})
    assert status == 400 and "the code M00 is MEZZANINE FLOOR PLAN's" in said["error"]
    assert not any(loc.buildings for loc in app.workspace(code).locations)  # nothing added
    # the three at once: the mezzanine stands in the ground floor added with it
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        mezzanine, plan("GROUND FLOOR PLAN", height=7.0, name="Ground floor"),
        plan("FIRST FLOOR PLAN", name="First floor")]})[1])
    ws = app.workspace(code)
    b = ws.locations[0].buildings[0]
    b_id = f"{ws.id}-{ws.locations[0].code}-{b.code}"
    assert [(f.code, f.ordinal, f.elevation, f.host) for *_, f, _ in ws.iter_floors()] == \
        [("F00", 0, 0.0, None), ("M00", 0, 3.5, "F00"), ("F01", 1, 7.0, None)]
    m = ws.floor(f"{b_id}-M00")
    assert m.converted_at is not None and len([r for r in ws.floor_objects(f"{b_id}-M00") if r.kind == "space"]) == 4
    assert m.source.offset == pytest.approx((70000, 0), abs=100)  # lined up on the ground floor, 70 m off on the sheet
    gx0, gy0, _, gy1 = shape(ws.floor(f"{b_id}-F00").outline).bounds
    assert shape(m.outline).bounds == pytest.approx((gx0, gy0 + MEZZANINE_AT[1], gx0 + 19.8, gy1), abs=0.3)  # its west end
    _, page = call(f"{base}/api/projects/{code}")
    listed = page["locations"][0]["buildings"][0]["floors"]
    assert [(f["code"], f["mezzanine"]) for f in listed] == [("F00", False), ("M00", True), ("F01", False)]
    assert (listed[1]["host"], listed[1]["host_name"], listed[1]["above_host"]) == (f"{b_id}-F00", "Ground floor", 3.5)

    # again: a floor and a mezzanine on it are told apart
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("MEZZANINE FLOOR PLAN", building_id=b_id, mezzanine=True, code="M00")]})
    assert status == 400 and "Warehouse already has Mezzanine (M00) on floor 0" in said["error"]
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("MEZZANINE FLOOR PLAN", building_id=b_id, mezzanine=True, code="F01")]})
    assert status == 400 and "the code F01 is First floor (F01)'s" in said["error"]
    status, said = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("GROUND FLOOR PLAN", building_id=b_id)]})
    assert status == 400 and "Warehouse already has floor 0 (Ground floor)" in said["error"]
    # the mezzanine's drawing replaced, higher up now: its rooms keep their IDs
    rooms = {r.id for r in ws.floor_objects(f"{b_id}-M00")}
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("MEZZANINE FLOOR PLAN", building_id=b_id, mezzanine=True, code="M00", above_host=4.0, replace=True)]})[1])
    ws = app.workspace(code)
    assert ws.floor(f"{b_id}-M00").elevation == 4.0 and {r.id for r in ws.floor_objects(f"{b_id}-M00")} == rooms
    assert len(ws.building(b_id).floors) == 3
    # a second mezzanine on the ground floor: its own code by default (M00B)
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        plan("MEZZANINE FLOOR PLAN", building_id=b_id, mezzanine=True, name="Gallery", above_host=3.0)]})[1])
    assert [f.code for *_, f, _ in app.workspace(code).iter_floors()] == ["F00", "M00B", "M00", "F01"]
    # the floor a mezzanine stands in is not deleted from under it
    status, said = call(f"{base}/api/projects/{code}/floors/{b_id}-F00/delete", {"confirm": "Ground floor"})
    assert status == 400 and "Ground floor holds Gallery (M00B), Mezzanine (M00)" in said["error"]
    status, said = call(f"{base}/api/projects/{code}/floors/{b_id}-M00B/delete", {"confirm": "Gallery"})
    assert status == 200, said


def test_a_mezzanine_is_added_on_the_command_line(tmp_path):
    from typer.testing import CliRunner

    from storeypath.cli import app

    def run(*args):
        result = CliRunner().invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        return result.output.strip()

    ws_file = tmp_path / "w.spproj"
    write_floor_dxf(tmp_path / "ground.dxf", office_floor(0))
    write_floor_dxf(tmp_path / "upper.dxf", mezzanine_floor(), origin=(125.0, 48.0 + MEZZANINE_AT[1]))
    run("new", ws_file, "--name", "Warehouse")
    b = run("add-building", ws_file, run("add-location", ws_file, "MAIN", "--name", "Main site"), "WH", "--name", "Warehouse")
    run("add-floor", ws_file, b, tmp_path / "ground.dxf", "--ordinal", "0", "--height", "7", "--units", "mm", "--no-model")
    m = run("add-floor", ws_file, b, tmp_path / "upper.dxf", "--ordinal", "0", "--mezzanine", "--above", "3.5",
            "--height", "3.5", "--units", "mm", "--no-model")
    assert m == f"{b}-M00"
    f = Workspace.load(ws_file).floor(m)
    assert (f.name, f.ordinal, f.host, f.above_host, f.elevation) == ("Mezzanine", 0, "F00", 3.5, 3.5)
    assert f"{m}: 0 kept, 8 new" in run("convert", ws_file)  # its 4 rooms, 3 doors and a window
    assert "export #1" in run("export", ws_file, "-o", tmp_path / "w.storeypath")
    assert "valid" in run("validate", tmp_path / "w.storeypath")
