"""A floor read again after a person drew on it (convert.py, _extract): the rooms a
wall drawn or taken away comes near are found again around it (find_spaces_around),
the rest are as the floor's last read found them, and what the floor holds then is
what reading the whole floor again gives."""

import pytest
from shapely.geometry import shape

from storeypath import convert
from storeypath.convert import convert_floor
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.workspace import DrawnOpening, Placement, SourceDrawing, Workspace


@pytest.fixture
def floor(tmp_path, monkeypatch):
    """An office floor drawn with walls (no room outlines), read once; and which way
    each read after it found its spaces."""
    write_floor_dxf(tmp_path / "level-2.dxf", office_floor(2), area_outlines=False, walls="lines")
    ws = Workspace.new("Drawn On")
    b_id = ws.add_building(ws.add_location("SITE", "Site"), "B", "Building")
    ws.building(b_id).placement = Placement(lon=10.0, lat=50.0, x=125, y=48, bearing=0)
    f_id = ws.add_floor(b_id, 2, source=SourceDrawing(path="level-2.dxf"))
    convert_floor(ws, f_id, tmp_path)
    ways = []
    whole, around = convert.find_spaces, convert.find_spaces_around

    def found_whole(*args, **kw):
        ways.append("whole")
        return whole(*args, **kw)

    def found_around(*args, **kw):
        out = around(*args, **kw)
        ways.append("around" if out is not None else "around: whole needed")
        return out

    monkeypatch.setattr(convert, "find_spaces", found_whole)
    monkeypatch.setattr(convert, "find_spaces_around", found_around)
    return ws, tmp_path, f_id, ways


def _held(ws, f_id):
    """What the floor holds, as compared: each object's kind, ID, name, number, type,
    the spaces it joins and its shape (area and extent, to the centimetre)."""
    out = []
    for r in ws.floor_objects(f_id):
        g = shape(r.geometry)
        e = ws.effective(r)
        out.append((r.kind, r.id, e["name"], e["number"], e["type"], tuple(sorted(r.connects or ())),
                    round(g.area, 1), tuple(round(v, 1) for v in g.bounds)))
    return sorted(out)


def _read_again(ws, d, f_id):
    """The floor read again after a person drew on it, as it is; and the same floor read
    whole (nothing kept of its last read), to compare it with."""
    whole = ws.model_copy(deep=True)
    report = convert_floor(ws, f_id, d, drawn=True)
    kept = dict(convert._reads)
    convert._reads.clear()
    convert_floor(whole, f_id, d, drawn=True)
    convert._reads.update(kept)
    return report, whole


def _rooms(ws, f_id):
    return [r for r in ws.floor_objects(f_id) if r.kind == "space"]


def _room(ws, f_id, number):
    return next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.number == number)


def test_a_wall_across_an_office_finds_the_rooms_around_it_again_as_the_whole_floors_read_does(floor):
    ws, d, f_id, ways = floor
    rooms = len(_rooms(ws, f_id))
    x0, y0, x1, y1 = shape(_room(ws, f_id, "201").geometry).bounds
    ws.floor(f_id).edits.walls.append([[x0 + 1.2, y0 - 0.1], [x0 + 1.2, y1 + 0.1]])  # (beside its label)
    report, whole = _read_again(ws, d, f_id)
    assert ways[0] == "around"
    assert len(_rooms(ws, f_id)) == rooms + 1  # the office, two rooms now
    assert _held(ws, f_id) == _held(whole, f_id)

    # and taken away again: one office, as before
    ways.clear()
    ws.floor(f_id).edits.walls.clear()
    report, whole = _read_again(ws, d, f_id)
    assert ways[0] == "around" and len(_rooms(ws, f_id)) == rooms
    assert _held(ws, f_id) == _held(whole, f_id)


def test_a_door_or_a_dividing_line_drawn_keeps_the_spaces_as_they_were_found(floor):
    ws, d, f_id, ways = floor
    office = shape(_room(ws, f_id, "202").geometry)
    x0, y0, x1, y1 = office.bounds
    edits = ws.floor(f_id).edits
    edits.dividers.append([[(x0 + x1) / 2, y0 - 0.1], [(x0 + x1) / 2, y1 + 0.1]])
    edits.openings.append(DrawnOpening(type="door", span=[[x0 + 0.5, y0], [x0 + 1.4, y0]]))
    report, whole = _read_again(ws, d, f_id)
    assert ways == ["whole"]  # (the read to compare with's alone: the floor's found none)
    assert _held(ws, f_id) == _held(whole, f_id)


def test_walls_out_into_the_outside_have_the_whole_floor_read(floor):
    # three walls closing off a piece of the outside against the facade: a room now,
    # that only the whole floor's walls can tell
    ws, d, f_id, ways = floor
    rooms = len(_rooms(ws, f_id))
    x0, y0, x1, y1 = shape(_room(ws, f_id, "201").geometry).bounds
    ws.floor(f_id).edits.walls.extend([[[x0 + 1, y0 + 0.2], [x0 + 1, y0 - 3]], [[x0 + 1, y0 - 3], [x1 - 1, y0 - 3]],
                                       [[x1 - 1, y0 - 3], [x1 - 1, y0 + 0.2]]])
    report, whole = _read_again(ws, d, f_id)
    assert ways[:2] == ["around: whole needed", "whole"]
    assert len(_rooms(ws, f_id)) == rooms + 1
    assert _held(ws, f_id) == _held(whole, f_id)


def test_a_floor_read_after_its_drawing_changed_is_read_whole(floor):
    ws, d, f_id, ways = floor
    write_floor_dxf(d / "level-2-rev.dxf", office_floor(2), area_outlines=False, walls="lines", title="REVISED")
    ws.floor(f_id).source = SourceDrawing(path="level-2-rev.dxf")
    x0, y0, x1, y1 = shape(_room(ws, f_id, "201").geometry).bounds
    ws.floor(f_id).edits.walls.append([[x0 + 1.2, y0 - 0.1], [x0 + 1.2, y1 + 0.1]])
    convert_floor(ws, f_id, d, drawn=True)
    assert ways == ["whole"]


@pytest.mark.parametrize("where", ["across the corridor", "along a wall", "a stub closing nothing",
                                   "across the open office", "between two offices' doors"])
def test_walls_drawn_anywhere_leave_the_floor_as_reading_it_whole_does(floor, where):
    ws, d, f_id, ways = floor
    corridor = shape(next(r for r in _rooms(ws, f_id) if r.name == "CORRIDOR").geometry).bounds
    a = shape(_room(ws, f_id, "201").geometry).bounds
    b = shape(_room(ws, f_id, "202").geometry).bounds
    open_office = shape(next(r for r in _rooms(ws, f_id) if r.name == "OPEN OFFICE").geometry).bounds
    wall = {"across the corridor": [[corridor[0] + 10, corridor[1] - 0.1], [corridor[0] + 10, corridor[3] + 0.1]],
            "along a wall": [[a[2] + 0.05, a[1] + 1], [a[2] + 0.05, a[3] - 1]],
            "a stub closing nothing": [[a[0] + 1, a[1] + 2], [a[0] + 2.5, a[1] + 2]],
            "across the open office": [[open_office[0] + 5, open_office[1] - 0.1],
                                       [open_office[0] + 5, open_office[3] + 0.1]],
            "between two offices' doors": [[a[2], a[3] + 0.3], [b[2], b[3] + 0.3]]}[where]
    ws.floor(f_id).edits.walls.append(wall)
    report, whole = _read_again(ws, d, f_id)
    assert ways[0] == "around"
    assert _held(ws, f_id) == _held(whole, f_id)
