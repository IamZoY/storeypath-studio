"""One floor drawn as parts where they stand (a building's two wings, the open ground between
them its own), found as plans of their own: Find plans says which plans can be read together
(nothing else drawn between them) and offers as one floor those whose room labels carry on one
numbering; a floor added from them reads the area spanning them (sheets.floor_parts)."""

import ezdxf
from shapely.geometry import shape

from storeypath.cad import read_drawing
from storeypath.samples import Cell, Door, write_sheet_dxf
from storeypath.sheets import find_views, floor_parts
from test_analyse import RULES, rules_only
from test_mezzanines import call, studio, wait  # noqa: F401 (a fixture)

GAP = 30.0  # metres of open ground between the wings


def wing(stem: str, first: int) -> list:
    """A wing of four offices off a corridor, labelled ``stem`` and a number from ``first``."""
    cells = [Cell(0, 0, 3, 24, ["CORRIDOR"], "corridor")]
    for k in range(4):
        cells.append(Cell(3, k * 6, 11, k * 6 + 6, ["OFFICE", f"{stem}{first + k:02d}"], "office",
                          Door(3, k * 6 + 3, "v", +1)))
    return cells


def sheet(path, west, east, between=0):
    write_sheet_dxf(path, [(west[0], (100.0, 50.0), west[1]), (east[0], (100.0 + 11 + GAP, 50.0), east[1])],
                    area_outlines=False)
    if between:  # something else drawn in the open ground between them: a legend's lines
        doc = ezdxf.readfile(path)
        for k in range(between):
            y = 50_000 + 1_000 + k * 500
            doc.modelspace().add_line((100_000 + 11_000 + 5_000, y), (100_000 + 11_000 + 9_000, y), dxfattribs={"layer": "LEGEND"})
        doc.saveas(path)
    views = find_views(read_drawing(path), RULES, 0.001, auto=True, is_room_name=rules_only)
    views.sort(key=lambda v: v.region[0])
    return views


def test_a_floors_two_wings_are_offered_as_one_floor(tmp_path):
    views = sheet(tmp_path / "wings.dxf", (wing("Q.2.", 1), "FIRST FLOOR PLAN"), (wing("Q.2.", 5), "FIRST FLOOR PLAN"))
    assert len(views) == 2
    joins, together, stems = floor_parts(read_drawing(tmp_path / "wings.dxf"), [v.region for v in views], [1, 1])
    assert joins == [[1], [0]] and together == [[0, 1], [0, 1]] and stems == ["Q.2.", "Q.2."]


def test_something_drawn_between_them_keeps_them_apart(tmp_path):
    views = sheet(tmp_path / "legend.dxf", (wing("Q.2.", 1), "FIRST FLOOR PLAN"), (wing("Q.2.", 5), "FIRST FLOOR PLAN"),
                  between=40)
    joins, together, _ = floor_parts(read_drawing(tmp_path / "legend.dxf"), [v.region for v in views], [1, 1])
    assert joins == [[], []] and together == [None, None]


def test_two_floors_side_by_side_are_not_offered_as_one(tmp_path):
    # nothing between them, but their labels' numberings differ (and so do their titles)
    views = sheet(tmp_path / "floors.dxf", (wing("Q.1.", 1), "GROUND FLOOR PLAN"), (wing("Q.2.", 1), "FIRST FLOOR PLAN"))
    joins, together, stems = floor_parts(read_drawing(tmp_path / "floors.dxf"), [v.region for v in views], [0, 1])
    assert joins == [[1], [0]] and together == [None, None] and stems == ["Q.1.", "Q.2."]
    # the same numbering, but titles saying different floors: not offered either
    views = sheet(tmp_path / "titled.dxf", (wing("Q.2.", 1), "GROUND FLOOR PLAN"), (wing("Q.2.", 5), "FIRST FLOOR PLAN"))
    joins, together, _ = floor_parts(read_drawing(tmp_path / "titled.dxf"), [v.region for v in views], [0, 1])
    assert joins == [[1], [0]] and together == [None, None]


def test_find_plans_offers_the_wings_and_one_floor_is_added_from_both(studio, tmp_path):  # noqa: F811
    base, app = studio
    code = call(f"{base}/api/projects", {"name": "Depot"})[1]["code"]
    sheet(tmp_path / "wings.dxf", (wing("Q.2.", 1), "FIRST FLOOR PLAN"), (wing("Q.2.", 5), "FIRST FLOOR PLAN"))
    call(f"{base}/api/projects/{code}/drawings/wings.dxf?private=0", raw=(tmp_path / "wings.dxf").read_bytes())
    found = wait(base, call(f"{base}/api/projects/{code}/drawings/wings.dxf/plans", {})[1])
    plans = sorted(found["plans"], key=lambda p: p["region"][0])
    assert [p["joins"] for p in plans] == [[plans[1]["index"]], [plans[0]["index"]]]
    assert plans[0]["together"] == plans[1]["together"] and sorted(plans[0]["together"]) == sorted(p["index"] for p in plans)
    # added as the page adds parts of one floor: one plan, its area spanning them
    r = [p["region"] for p in plans]
    span = [min(x[0] for x in r), min(x[1] for x in r), max(x[2] for x in r), max(x[3] for x in r)]
    wait(base, call(f"{base}/api/projects/{code}/floors", {"drawing": "wings.dxf", "plans": [
        {"index": plans[0]["index"], "title": "FIRST FLOOR PLAN", "region": span, "building": "Depot", "ordinal": 1,
         "name": "First floor", "parts": [p["index"] for p in plans]}]})[1])
    ws = app.workspace(code)
    (*_, f, f_id), = list(ws.iter_floors())
    offices = [o for o in ws.floor_objects(f_id) if o.kind == "space" and o.type == "office"]
    assert len(offices) == 8  # both wings' offices
    outline = shape(f.outline)
    assert outline.geom_type == "MultiPolygon" and len(outline.geoms) == 2  # two blocks, nothing in the open ground


def test_two_blocks_of_one_floor_numbered_by_block_are_offered_as_one(tmp_path):
    # each block its own letters, the floor's part the same: one floor's two blocks
    from storeypath.sheets import one_numbering

    views = sheet(tmp_path / "blocks.dxf", (wing("K.2.", 1), "FIRST FLOOR PLAN"), (wing("L.2.", 1), "FIRST FLOOR PLAN"))
    _, together, _ = floor_parts(read_drawing(tmp_path / "blocks.dxf"), [v.region for v in views], [None, None])
    assert together == [[0, 1], [0, 1]]
    assert one_numbering("K-GF-", "L-GF-") and not one_numbering("K.1.", "K.2.") and not one_numbering("A1-GF-", "A1-1F-")


def test_a_facade_standing_apart_across_an_open_hall_is_its_plans(tmp_path):
    # A wing whose east side is an open hall: its outside wall is a row of short piers 10 m off
    # the nearest wall (too short to be read as walls), windows between them. Its plan takes it
    # in, as far as the piers; a deep drawing beside a plan (an elevation's lines) is not taken
    write_sheet_dxf(tmp_path / "hall.dxf", [(wing("Q.2.", 1), (100.0, 50.0), "FIRST FLOOR PLAN")], area_outlines=False)
    doc = ezdxf.readfile(tmp_path / "hall.dxf")
    walls = next(e.dxf.layer for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE")
    for k in range(13):  # piers 0.35 x 1.0 m, every 2 m, at x = 11 + 10 m
        x, y = 100_000 + 21_000, 50_000 + k * 2_000
        doc.modelspace().add_lwpolyline([(x, y), (x + 350, y), (x + 350, y + 1000), (x, y + 1000)], close=True,
                                        dxfattribs={"layer": walls})
    for k in range(30):  # an elevation's lines 6 m deep, south of the plan
        doc.modelspace().add_line((100_000, 50_000 - 8_000 - k * 200), (100_000 + 11_000, 50_000 - 8_000 - k * 200),
                                  dxfattribs={"layer": walls})
    doc.saveas(tmp_path / "hall.dxf")
    views = find_views(read_drawing(tmp_path / "hall.dxf"), RULES, 0.001, auto=True, is_room_name=rules_only)
    (x0, y0, x1, y1), = [tuple(c * 0.001 for c in v.region) for v in views]
    assert x1 >= 121.35 and y0 >= 50.0 - 1.5  # the piers taken in; the elevation south of it not
