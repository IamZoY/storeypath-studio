"""A location's layout (format 0.12): its land read from a site plan (siteplan.py), the
location's buildings matched to the footprints on it and placed on them (layout.py),
reading it again, Review on it, its package, the validators and opening it back. Every
site plan here is made up (samples.sample_site_plan)."""

import io
import json
import math
import zipfile

import pytest
from shapely.geometry import Polygon, mapping, shape

from people import Team
from storeypath import accounts as acc
from storeypath import siteplan
from storeypath.bundle import read_file
from storeypath.cad import read_drawing
from storeypath.export import export_layout_package, export_package, placements, site_footprint
from storeypath.georef import Georeferencer
from storeypath.ids import LAND_CODE, is_land
from storeypath.layout import (convert_layout, fit_shape, label_score, match_buildings, new_building, place,
                               place_all)
from storeypath.samples import HOUSE, GUEST, STORE, _placed, sample_site_plan, site_plan_region, write_site_plan_dxf
from storeypath.validate import validate_package
from storeypath.workspace import Placement, SourceDrawing, Workspace

ORIGIN = (300.0, 200.0)  # where the made-up site plan is drawn, metres
MIDDLE = (325.0, 218.0)  # its plot's middle: the site's centre


def _plan_file(tmp_path, plan=None, units: str = "m", name: str = "site.dxf"):
    plan = plan or sample_site_plan()
    write_site_plan_dxf(tmp_path / name, plan, units=units)
    return plan


def _read(tmp_path, plan=None, units: str = "m"):
    plan = _plan_file(tmp_path, plan, units)
    return siteplan.read_site_plan(read_drawing(tmp_path / "site.dxf"), site_plan_region(plan, units=units), units)


def _project(tmp_path, buildings=(), plan=None):
    """A project whose location has buildings of these outlines (code, name, ring in the
    building's own frame: its ground floor's outline) and the made-up site plan as its
    layout, read."""
    plan = _plan_file(tmp_path, plan)
    ws = Workspace.new("Made-up plot")
    loc = ws.add_location("SITE", "Plot")
    ids = {}
    for code, name, ring in buildings:
        ids[code] = ws.add_building(loc, code, name)
        f_id = ws.add_floor(ids[code], 0)
        ws.floor(f_id).outline = mapping(Polygon(ring))
    layout = ws.add_layout(loc, SourceDrawing(path="site.dxf", units="m", region=site_plan_region(plan)))
    convert_layout(ws, layout, tmp_path)
    return ws, loc, layout, ids


def _objects(ws, layout, kind):
    return sorted((r for r in ws.floor_objects(layout) if r.kind == kind), key=lambda r: r.id)


def _footprint(ws, layout, name):
    return next(r for r in _objects(ws, layout, "footprint") if r.name == name)


# ---- reading a site plan ------------------------------------------------------------------

def test_a_site_plan_reads_as_its_plot_walls_gates_areas_footprints_and_north(tmp_path):
    r = _read(tmp_path)
    assert r.boundary_from == "property lines"
    assert r.boundary.area == pytest.approx(50 * 36, abs=0.5)
    assert r.boundary.bounds == pytest.approx((300, 200, 350, 236), abs=0.01)
    assert r.wall_thickness == pytest.approx(0.25, abs=0.02)
    assert r.walls is not None and r.walls.area > 0.25 * 150
    names = sorted(siteplan.building_name(f.texts) for f in r.footprints)
    assert names == ["Guest House A", "Guest House B", "Main House", "Store"]
    house = next(f for f in r.footprints if siteplan.building_name(f.texts) == "Main House")
    assert house.polygon.area == pytest.approx(Polygon(HOUSE).area, rel=0.01)
    kinds = {a.type.value: a for a in r.areas}
    assert set(kinds) == {"yard", "parking", "garden", "road"}
    assert kinds["parking"].polygon.area == pytest.approx(72, abs=1) and kinds["parking"].name == "PARKING"
    assert kinds["garden"].polygon.area == pytest.approx(90, abs=1)
    assert kinds["road"].issues and not kinds["road"].polygon.intersection(r.boundary).area > 1
    # the yard: the plot but its buildings, areas and walls
    assert kinds["yard"].polygon.area == pytest.approx(1800 - 72 - 90 - 186 - 2 * 48 - 20, rel=0.06)
    assert sorted(round(g.span.length, 2) for g in r.gates) == [1.2, 5.0]
    yard, road = r.areas.index(kinds["yard"]), r.areas.index(kinds["road"])
    assert all(g.connects == [yard, road] for g in r.gates)
    assert next(g for g in r.gates if g.span.length > 4).swings == 2
    assert r.north.bearing == pytest.approx(30.0) and r.north.sure >= 0.9


@pytest.mark.parametrize("marked", ["layer", "none"])
def test_the_plot_from_its_property_layer_else_from_its_walls(tmp_path, marked):
    plan = sample_site_plan()
    plan.property = marked
    r = _read(tmp_path, plan, units="mm" if marked == "layer" else "m")
    assert r.boundary_from == ("property layer" if marked == "layer" else "walls")
    assert r.boundary.area == pytest.approx(1800, abs=1)
    assert len(r.footprints) == 4 and len(r.gates) == 2


@pytest.mark.parametrize("turn", [0.0, 90.0, 215.0])
def test_north_from_the_arrow_turned(tmp_path, turn):
    plan = sample_site_plan()
    plan.north = turn
    r = _read(tmp_path, plan)
    assert r.north.bearing == pytest.approx(turn % 360)
    plan.north = None
    assert _read(tmp_path, plan).north is None


def test_what_a_site_plans_texts_say():
    say = siteplan.what_text_says
    assert say("+0.45 FFL") == (siteplan.BUILDING, None) and say("±0.00 FGL") == (siteplan.GROUND, None)
    assert say("CAR PARK")[1].value == "parking" and say("SWIMMING POOL")[1].value == "pool"
    assert say("R  O  A  D")[1].value == "road" and say("N E I G H B O U R")[0] == siteplan.OUTSIDE
    assert say("GUEST HOUSE A")[0] == siteplan.BUILDING and say("STEP UP")[0] == siteplan.NOTHING
    assert say("1200.00 sq. m.")[0] == siteplan.NOTHING and say("LVL. +2.80")[0] == siteplan.NOTHING
    assert siteplan.building_name(["PROPOSED G+2 ANNEX", "+2.85 FFL"]) == "Annex"
    assert siteplan.building_name(["NEW GUEST H0USE"]) == "Guest House"  # (a zero typed for an O)


def test_the_language_model_reads_what_the_rules_do_not_know(tmp_path):
    plan = sample_site_plan()
    plan.buildings[3].labels = ["SEC. HUT"]
    plan.areas[1] = (plan.areas[1][0], "LAWN AREA X")
    asked = []

    def reader(texts):
        asked.extend(texts)
        return {"SEC. HUT": (siteplan.BUILDING, None)} if "SEC. HUT" in texts else {}

    _plan_file(tmp_path, plan)
    r = siteplan.read_site_plan(read_drawing(tmp_path / "site.dxf"), site_plan_region(plan), "m", read_texts=reader)
    assert "SEC. HUT" in asked
    assert any(f.texts == ["SEC. HUT"] and f.named for f in r.footprints)


# ---- matching and placing ------------------------------------------------------------------

def test_matching_by_shape_turned_and_moved_and_by_label(tmp_path):
    # the house drawn in a drawing of its own, turned a right angle, far off
    ws, loc, layout, ids = _project(tmp_path, [("RES", "Residence", _placed(HOUSE, (500.0, 80.0), 90.0)),
                                               ("GA", "Guest house A", _placed(GUEST, (10.0, 10.0))),
                                               ("GB", "Guest house B", _placed(GUEST, (40.0, 10.0)))])
    house = _footprint(ws, layout, "Main House")
    m = house.match  # (no word of its name or code on the site plan: its shape alone)
    assert m["building"] == ids["RES"] and m["by"] == "shape" and m["shape"] > 0.98 and m["confidence"] > 0.8
    assert m["site"]["rotation"] == pytest.approx(90.0)
    # two buildings alike: told apart by their names
    assert _footprint(ws, layout, "Guest House A").match["building"] == ids["GA"]
    assert _footprint(ws, layout, "Guest House B").match["building"] == ids["GB"]
    assert _footprint(ws, layout, "Guest House A").match["by"] == "both"
    # a footprint no building of the project is
    assert _footprint(ws, layout, "Store").match["building"] is None
    # nothing moved until a person says
    assert all(ws.building(b).site is None for b in ids.values())


def test_placing_puts_the_ground_floor_on_its_footprint_and_moves_nothing_in_it(tmp_path):
    ws, loc, layout, ids = _project(tmp_path, [("HOUSE", "Residence", _placed(HOUSE, (500.0, 80.0), 90.0))])
    b = ws.building(ids["HOUSE"])
    floors_before = [f.model_dump() for f in b.floors]
    placed = place_all(ws, ws.locations[0] and f"{ws.id}-SITE", confirm=True)
    assert placed == [(_footprint(ws, layout, "Main House").id, ids["HOUSE"])]
    on_site = site_footprint(b, b.site)
    fp = shape(_footprint(ws, layout, "Main House").geometry)
    assert on_site.intersection(fp).area / on_site.union(fp).area > 0.98
    assert b.site.footprint == _footprint(ws, layout, "Main House").id and b.site.rotation == pytest.approx(90.0)
    assert [f.model_dump() for f in b.floors] == floors_before  # its floors as they were: no room moves in it
    assert ws.overrides[_footprint(ws, layout, "Main House").id].building == ids["HOUSE"]  # a person's choice
    # on the map, the house's middle is where its footprint's is
    ws.locations[0].placement = Placement(lon=46.68, lat=24.71, bearing=30.0)
    pl, real = placements(ws)[ids["HOUSE"]]
    c = Polygon(_placed(HOUSE, (500.0, 80.0), 90.0)).centroid
    site = Georeferencer(Placement(lon=46.68, lat=24.71, x=0, y=0, bearing=30.0))
    want = site.lonlat(fp.centroid.x, fp.centroid.y)
    got = Georeferencer(pl).lonlat(c.x, c.y)
    assert real and math.dist(want, got) * 111_000 < 0.05


def test_a_building_drawn_in_the_site_plans_own_coordinates_fits_unturned(tmp_path):
    # the house's drawing shares the site plan's coordinates: drawn where the site plan has it
    ring = [(ORIGIN[0] + x, ORIGIN[1] + y) for x, y in _placed(HOUSE, (16.0, 12.0))]
    ws, loc, layout, ids = _project(tmp_path, [("HOUSE", "Main house", ring)])
    fit = fit_shape(Polygon(ring), shape(_footprint(ws, layout, "Main House").geometry))
    assert fit.rotation == pytest.approx(0.0) and fit.iou > 0.99
    # the identity in the drawing's coordinates: the layout's frame is the drawing's less its offset
    assert ws.floor(layout).source.offset == pytest.approx(MIDDLE)
    assert (fit.dx, fit.dy) == pytest.approx((-MIDDLE[0], -MIDDLE[1]), abs=0.01)
    place(ws, _footprint(ws, layout, "Main House").id, ids["HOUSE"])
    site = ws.building(ids["HOUSE"]).site
    from storeypath.export import on_site

    for x, y in ring[:3]:
        assert on_site(site, x, y) == pytest.approx((x - MIDDLE[0], y - MIDDLE[1]), abs=0.01)


def test_label_scores():
    assert label_score(["MAIN HOUSE"], ["Main house", "HOUSE"]) == 1.0
    assert label_score(["PROPOSED GUEST HOUSE A"], ["Guest house A"]) == 1.0
    assert label_score(["GUEST HOUSE A"], ["Guest house B"]) < label_score(["GUEST HOUSE A"], ["Guest house A"])
    assert label_score(["G. HOUSE"], ["Guest house"]) > 0.5  # an abbreviation
    assert label_score(["WAREHOUSE"], ["Main building"]) == 0.0


def test_the_language_model_names_a_building_the_words_do_not(tmp_path):
    from storeypath import llm

    ws, loc, layout, ids = _project(tmp_path, [("SH", "Security hut", _placed(STORE, (0.0, 0.0))),
                                               ("XX", "Pump room", _placed(STORE, (20.0, 0.0)))])

    class Model:
        name = "made-up"
        parallel = 1

        def available(self):
            return True

        def ask(self, system, user, schema, max_tokens=0):
            assert system == llm.WHICH_SYSTEM
            return {"n": 1}  # the first: the security hut

    store = _footprint(ws, layout, "Store")
    match_buildings(ws, f"{ws.id}-SITE", model=Model())
    store = _footprint(ws, layout, "Store")
    assert store.match["building"] == ids["SH"] and store.match["by"] in ("model", "both")


def test_a_footprint_of_no_building_is_offered_as_a_new_building(tmp_path):
    ws, loc, layout, ids = _project(tmp_path)
    store = _footprint(ws, layout, "Store")
    b_id = new_building(ws, store.id)
    b = ws.building(b_id)
    assert b.name == "Store" and b.floors == [] and b.code == "STORE"
    assert b.site.footprint == store.id and ws.overrides[store.id].building == b_id
    # a building with no floors may be exported: its package says where it stands
    m = export_package(ws, tmp_path / "store.storeypath", building=b_id, bake=False)
    assert validate_package(tmp_path / "store.storeypath") == []
    assert m.placements[b_id].site is not None
    with pytest.raises(ValueError, match="LAND"):
        ws.add_building(f"{ws.id}-SITE", LAND_CODE, "Land Rover garage")


def test_reading_again_keeps_ids_and_the_placements_people_made(tmp_path):
    ws, loc, layout, ids = _project(tmp_path, [("HOUSE", "Residence", _placed(HOUSE, (500.0, 80.0), 90.0))])
    before = {r.id: (r.kind, r.type) for r in ws.floor_objects(layout)}
    place_all(ws, f"{ws.id}-SITE", confirm=True)
    site = ws.building(ids["HOUSE"]).site.model_copy()
    # the site plan revised: the parking a little larger, a new gate
    plan = sample_site_plan()
    plan.areas[0] = ([(2.0, 2.0), (15.0, 2.0), (15.0, 8.0), (2.0, 8.0)], "PARKING")
    plan.gates.append(((10.0, 11.5), 1))
    _plan_file(tmp_path, plan)
    report = convert_layout(ws, layout, tmp_path)
    after = {r.id: (r.kind, r.type) for r in ws.floor_objects(layout)}
    assert set(before) <= set(after) and not report.retired
    assert len(after) == len(before) + 1  # the new gate
    assert ws.building(ids["HOUSE"]).site == site  # kept: a person placed it
    assert place_all(ws, f"{ws.id}-SITE", again=True) == [(_footprint(ws, layout, "Main House").id, ids["HOUSE"])]


def test_what_people_draw_on_a_layout_is_read_with_it(tmp_path):
    from storeypath.workspace import DrawnOpening, ResizedOpening

    ws, loc, layout, ids = _project(tmp_path)
    f = ws.floor(layout)
    wide = max(_objects(ws, layout, "opening"), key=lambda r: r.width)
    # a gate in the north wall the drawing leaves out, the vehicle gate narrowed, a wall across the yard
    f.edits.openings.append(DrawnOpening(type="door", span=[[0.0, 17.9], [3.0, 17.9]]))
    f.edits.resized.append(ResizedOpening(at=wide.geometry["coordinates"], width=4.0))
    f.edits.walls.append([[-25.0, 0.0], [-14.0, 0.0]])
    report = convert_layout(ws, layout, tmp_path)
    gates = {r.id: r for r in _objects(ws, layout, "opening")}
    assert wide.id in gates and gates[wide.id].width == pytest.approx(4.0)
    drawn = [r for r in gates.values() if r.type_source == "drawn gate"]
    assert len(drawn) == 1 and not drawn[0].issues and drawn[0].width == pytest.approx(3.0)
    assert len([r for r in _objects(ws, layout, "space") if r.type == "yard"]) >= 1 and not report.held


# ---- the package, the validators, and back -------------------------------------------------

def _furnished(ws, layout):
    gates = sorted(_objects(ws, layout, "opening"), key=lambda r: r.width)
    pole = ws.add_item("CCTV-POLE", layout, 3.0, -15.0, rotation=180)
    reader = ws.add_item("CARD-READER", layout, 8.0, -17.6, rotation=180)
    reader.door_id = gates[0].id
    return pole, reader, gates


def test_the_layouts_package_validates_and_opens_back(tmp_path):
    ws, loc, layout, ids = _project(tmp_path, [("HOUSE", "Residence", _placed(HOUSE, (500.0, 80.0), 90.0))])
    place_all(ws, f"{ws.id}-SITE", confirm=True)
    pole, reader, gates = _furnished(ws, layout)
    ws.locations[0].placement = Placement(lon=46.68, lat=24.71, bearing=30.0)
    out = tmp_path / "plot.storeypath"
    m = export_layout_package(ws, out, layout)
    assert validate_package(out) == []
    assert m.scope.buildings == [] and m.scope.layout == layout and list(m.placements) == [layout]
    assert m.files["layout"] == "layout.geojson" and "navigation" not in m.files
    with zipfile.ZipFile(out) as z:
        lay = json.loads(z.read("layout.geojson"))["features"][0]
        prints = {f["properties"]["name"]: f for f in json.loads(z.read("footprints.geojson"))["features"]}
        items = {f["id"]: f["properties"] for f in json.loads(z.read("items.geojson"))["features"]}
        rows = list(__import__("csv").DictReader(io.StringIO(z.read("objects.csv").decode())))
    assert lay["properties"]["north"] == {"bearing": 30.0, "from": "map"}
    assert prints["Main House"]["properties"]["building_id"] == ids["HOUSE"]
    assert prints["Store"]["properties"]["building_id"] is None
    assert items[reader.id]["door_id"] == gates[0].id and items[reader.id]["building_id"] is None
    assert items[pole.id]["view_deg"] == 70 and items[pole.id]["range_m"] == 30
    assert {r["kind"] for r in rows} >= {"layout", "footprint", "space", "opening", "item"}
    assert all(not r["building_id"] for r in rows if is_land(r["id"]))
    # the building's package says where it stands on the site, as the layout's does
    export_package(ws, tmp_path / "house.storeypath", building=ids["HOUSE"], bake=False)
    assert validate_package(tmp_path / "house.storeypath") == []
    with zipfile.ZipFile(tmp_path / "house.storeypath") as z:
        site = json.loads(z.read("manifest.json"))["placements"][ids["HOUSE"]]["site"]
    assert site == prints["Main House"]["properties"]["site"]
    # opened back: the layout rebuilt, its objects and items where they were
    back = read_file(out).ws
    land = back.locations[0].layout
    assert land is not None and land.floors[0].layout and back.floor(layout).outline is not None
    assert {r.id for r in back.floor_objects(layout)} == {r.id for r in ws.floor_objects(layout)}
    assert back.items[reader.id].door_id is None or back.items[reader.id].floor_id == layout
    assert back.items[pole.id].x == pytest.approx(pole.x, abs=0.01)
    assert back.overrides[_footprint(ws, layout, "Main House").id].building == ids["HOUSE"]
    assert back.locations[0].placement.bearing == pytest.approx(30.0)
    # the next export lists nothing changed
    m2 = export_layout_package(back, tmp_path / "again.storeypath", layout)
    with zipfile.ZipFile(tmp_path / "again.storeypath") as z:
        changes = json.loads(z.read("changes.json"))
    assert changes["previous_sequence"] == m.export.sequence and not changes["added"] and not changes["retired"]


def test_the_validators_refuse_what_a_layouts_package_may_not_hold(tmp_path):
    ws, loc, layout, ids = _project(tmp_path, [("HOUSE", "Residence", _placed(HOUSE, (500.0, 80.0), 90.0))])
    _furnished(ws, layout)
    out = tmp_path / "plot.storeypath"
    export_layout_package(ws, out, layout)

    def broken(name, change):
        with zipfile.ZipFile(out) as z:
            files = {n: z.read(n) for n in z.namelist()}
        doc = json.loads(files[name])
        change(doc)
        files[name] = json.dumps(doc).encode()
        bad = tmp_path / "bad.storeypath"
        with zipfile.ZipFile(bad, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return validate_package(bad)

    def two_placements(m):
        m["placements"]["X-Y"] = next(iter(m["placements"].values()))

    assert any("one placement" in e for e in broken("manifest.json", two_placements))

    def item_in_a_building(fc):
        fc["features"][0]["properties"]["building_id"] = ids["HOUSE"]

    assert any("on layout" in e for e in broken("items.geojson", item_in_a_building))

    def foreign_footprint(fc):
        fc["features"][0]["properties"]["building_id"] = f"{ws.id}-ELSEWHERE-HQ"

    assert any("not a building of its layout's location" in e for e in broken("footprints.geojson", foreign_footprint))
    # a building's package with a building coded LAND
    export_package(ws, tmp_path / "house.storeypath", building=ids["HOUSE"], bake=False)
    out = tmp_path / "house.storeypath"

    def land_building(fc):
        f = fc["features"][0]
        f["id"] = f"{ws.id}-SITE-LAND"

    assert any("LAND is a location's land" in e for e in broken("buildings.geojson", land_building))


# ---- in Studio: Find plans, the layout's job, the project's page, Review, export ----------------

@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def _upload(team, tmp_path) -> str:
    plan = _plan_file(tmp_path)
    data = (tmp_path / "site.dxf").read_bytes()
    from sessions import call

    status, answer, _ = call(team.port, "PUT", f"/api/projects/{team.code}/drawings/site.dxf?private=0", raw=data,
                             token=team.tokens["khalid"])
    assert status == 200, answer
    return answer["drawing"], plan


def test_a_site_plan_found_and_used_as_a_locations_layout_in_studio(team, tmp_path):
    drawing, plan = _upload(team, tmp_path)
    status, job = team("khalid", "POST", f"drawings/{drawing}/plans", {})
    found = team.finished("khalid", job)["result"]
    site = next(p for p in found["plans"] if p["kind"] == "site_plan")
    assert site["title"] == "SITE PLAN"
    loc = f"{team.code}-DEMO"
    status, job = team("khalid", "POST", "layouts", {"drawing": drawing, "units": found["units"],
                                                    "plan": {"region": site["region"], "title": site["title"]},
                                                    "location_id": loc})
    assert status == 200, job
    team.finished("khalid", job)
    layout = f"{loc}-{LAND_CODE}-G00"
    page = team("khalid", "GET", f"/api/projects/{team.code}")[1]
    lay = next(x for x in page["locations"] if x["id"] == loc)["layout"]
    assert lay["id"] == layout and lay["converted"] and len(lay["footprints"]) == 4 and len(lay["gates"]) == 2
    assert {a["type"] for a in lay["areas"]} == {"yard", "parking", "garden", "road"}
    assert lay["north"]["bearing"] == 30.0
    # vera (HQ's ground floor alone) may not say what a footprint is; khalid may
    house = next(f for f in lay["footprints"] if f["name"] == "Main House")
    assert team("vera", "POST", f"footprints/{house['id']}", {"building": team.hq})[0] in (403, 404)
    status, said = team("khalid", "POST", f"footprints/{house['id']}", {"building": team.hq})
    assert status == 200 and said["site"]["footprint"] == house["id"]
    store = next(f for f in lay["footprints"] if f["name"] == "Store")
    status, made = team("khalid", "POST", f"footprints/{store['id']}", {"new": True})
    assert status == 200 and made["building"] == f"{loc}-STORE"
    page = team("khalid", "GET", f"/api/projects/{team.code}")[1]
    b = next(b for x in page["locations"] if x["id"] == loc for b in x["buildings"] if b["code"] == "STORE")
    assert b["on_footprint"] == store["id"] and b["footprint"]  # drawn as its footprint
    # north set by a person, and the history says so
    assert team("khalid", "POST", f"locations/{loc}/north", {"bearing": 12.5})[1]["north"]["source"] == "person"
    lines = [h["line"] for h in team("khalid", "GET", "history?n=10")[1]["entries"]]
    assert any("north" in line for line in lines) and any("footprint" in line for line in lines)
    # Review lists the layout and opens it, its footprints with it
    review = team("khalid", "GET", "review")[1]
    entry = next(f for f in review["floors"] if f["id"] == layout)
    assert entry["layout"] and "garden" in review["land_types"]
    floor = team("khalid", "GET", f"floors/{layout}")[1]
    assert floor["layout"] and {f["name"] for f in floor["footprints"]} >= {"Main House", "Store"}
    assert next(f for f in floor["footprints"] if f["name"] == "Main House")["building"] == team.hq
    # a camera on a pole and a card reader at a gate, placed as on a floor: the reader serves the gate
    gate = min((d for d in floor["doors"]), key=lambda d: d.get("width") or 0)
    (x0, y0), (x1, y1) = gate["span"]
    status, it = team("khalid", "POST", f"floors/{layout}/items", {"type": "CARD-READER", "x": max(x0, x1) + 0.3,
                                                                 "y": y0 + 0.3, "rotation": 180})
    assert status == 200, it
    assert it["door_id"] == gate["id"]
    status, pole = team("khalid", "POST", f"floors/{layout}/items", {"type": "CCTV-POLE", "x": 2.0, "y": -14.0})
    assert status == 200, pole
    # an area typed in review
    yard = next(s for s in floor["spaces"] if s["type"] == "yard")
    assert team("khalid", "POST", f"objects/{yard['id']}", {"correction": {"type": "courtyard"}})[0] == 200
    # read again: its IDs kept, the person's placement kept
    status, job = team("khalid", "POST", f"floors/{layout}/convert", {})
    assert status == 200, job
    team.finished("khalid", job)
    floor2 = team("khalid", "GET", f"floors/{layout}")[1]
    assert {s["id"] for s in floor2["spaces"]} == {s["id"] for s in floor["spaces"]}
    ws = team.studio.workspace(team.code)
    assert ws.building(team.hq).site.footprint == house["id"]
    # its package, made as a building's is, and the 3D view's
    status, job = team("khalid", "POST", "export", {"layout": layout})
    assert status == 200, job
    done = team.finished("khalid", job)
    assert done["result"]["file"].endswith("-DEMO-LAND.storeypath")
    from sessions import call

    status, data, _ = call(team.port, "GET", f"/api/projects/{team.code}/exports/{done['result']['file']}",
                           token=team.tokens["khalid"])
    assert status == 200
    kept = tmp_path / "kept.storeypath"
    kept.write_bytes(data)
    assert validate_package(kept) == []
    status, data, _ = call(team.port, "GET", f"/api/projects/{team.code}/preview.storeypath?building={loc}-LAND",
                           token=team.tokens["khalid"])
    assert status == 200 and zipfile.ZipFile(io.BytesIO(data)).read("layout.geojson")
