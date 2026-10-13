"""Doors as a person reads them: by their swing, their tag and where they stand, not
by the layer something was put on."""

from dataclasses import replace

import ezdxf
from shapely.geometry import Point

from storeypath.analyse import analyse
from storeypath.cad import read_drawing
from storeypath.extract import add_lift_doors, extract_floor
from storeypath.samples import WALL, Door, office_floor, write_floor_dxf

ORIGIN = (125.0, 48.0)


def _mm(x, y):
    return (ORIGIN[0] + x) * 1000, (ORIGIN[1] + y) * 1000


def _plan(tmp_path, cells, edit=None):
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    if edit is not None:
        doc = ezdxf.readfile(tmp_path / "plan.dxf")
        edit(doc)
        doc.saveas(tmp_path / "plan.dxf")
    doc = read_drawing(tmp_path / "plan.dxf")
    return extract_floor(doc, analyse(doc, 0.001, None, None).profile)  # layers read from what is drawn


def test_fixtures_on_a_door_layer_are_not_doors(tmp_path):
    def basins(doc):
        block = doc.blocks.new("A$C597558D8")  # an anonymous basin, as AutoCAD names them
        block.add_lwpolyline([(0, 0), (600, 0), (600, 450), (0, 450)], close=True)
        block.add_circle((300, 225), 150)
        for x in (9.0, 13.0, 17.0):
            doc.modelspace().add_blockref("A$C597558D8", _mm(x, 3.0), dxfattribs={"layer": "A-DOOR"})

    plain, with_basins = _plan(tmp_path, office_floor(1)), _plan(tmp_path, office_floor(1), basins)
    assert len(with_basins.doors) == len(plain.doors)


def _glazed_doorway(tmp_path, tag=None):
    """Office 101's doorway with glazing drawn across it, as a sliding door is often
    drawn, and maybe a tag beside it."""
    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.number == "101")
    cells[i] = replace(cells[i], door=replace(cells[i].door, block=False))
    d = cells[i].door

    def glaze(doc):
        for dy in (-WALL / 2, 0, WALL / 2):
            doc.modelspace().add_line(_mm(d.x - d.width / 2, d.y + dy), _mm(d.x + d.width / 2, d.y + dy),
                                      dxfattribs={"layer": "A-GLAZ"})
        if tag:
            doc.modelspace().add_text(tag, height=150, dxfattribs={"layer": "A-ANNO-SYMB"}).set_placement(
                _mm(d.x, d.y - 0.5))

    ex = _plan(tmp_path, cells, glaze)
    at = Point(ORIGIN[0] + d.x, ORIGIN[1] + d.y)  # extraction works in meters
    return ex, min(ex.doors, key=lambda o: o.point.distance(at))


def test_glazing_that_is_the_only_way_into_a_room_is_a_sliding_door(tmp_path):
    _, opening = _glazed_doorway(tmp_path)
    assert opening.source == "glazing" and len(opening.connects) == 2
    assert opening.issues == ["drawn as glazing, the only way into a room, taken as a sliding door; check it"]


def _glazed_panes(tmp_path, panes, door):
    """Office 101's wall onto the corridor glazed in panes (0.8 m, centred at
    ``panes``), beside its door or with no door at all."""
    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.number == "101")
    y = cells[i].door.y
    cells[i] = replace(cells[i], door=cells[i].door if door else None,
                       doors=[Door(x, y, "h", -1, 0.8, block=False) for x in panes])

    def glaze(doc):
        for x in panes:
            for dy in (-WALL / 2, 0, WALL / 2):
                doc.modelspace().add_line(_mm(x - 0.4, y + dy), _mm(x + 0.4, y + dy), dxfattribs={"layer": "A-GLAZ"})

    ex = _plan(tmp_path, cells, glaze)
    office = next(k for k, s in enumerate(ex.spaces) if s.number == "101")
    return [o for o in ex.doors if office in o.connects and len(o.connects) == 2 and o.source in ("window", "glazing")]


def test_glazing_beside_a_door_is_a_window(tmp_path):
    # an office's glass front onto its corridor, its door in it: the panes are windows
    glass = _glazed_panes(tmp_path, [8.8, 11.2], door=True)
    assert len(glass) == 2 and {o.source for o in glass} == {"window"}


def test_a_row_of_panes_round_a_room_with_no_door_is_windows(tmp_path):
    # which pane would be the door is not drawn (a courtyard's windows, between columns)
    glass = _glazed_panes(tmp_path, [8.8, 10.0, 11.2], door=False)
    assert len(glass) == 3 and {o.source for o in glass} == {"window"}


def _window_blocks(tmp_path, panes, door):
    """As _glazed_panes, each pane drawn as a window block (its glass on layer 0, as a
    block's lines are drawn: they take the layer the block is placed on), placed on a
    window layer; as thin as a door leaf."""
    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.number == "101")
    y = cells[i].door.y
    cells[i] = replace(cells[i], door=cells[i].door if door else None,
                       doors=[Door(x, y, "h", -1, 0.8, block=False) for x in panes])

    def glaze(doc):
        doc.layers.add("WINDOW PANES")
        pane = doc.blocks.new("PANE 800")
        for dy in (-WALL / 2, 0, WALL / 2):
            pane.add_line((-400, dy * 1000), (400, dy * 1000))
        for x in panes:
            doc.modelspace().add_blockref("PANE 800", _mm(x, y), dxfattribs={"layer": "WINDOW PANES"})

    ex = _plan(tmp_path, cells, glaze)
    office = next(k for k, s in enumerate(ex.spaces) if s.number == "101")
    return [o for o in ex.doors if office in o.connects and len(o.connects) == 2], ex


def test_windows_drawn_as_blocks_close_their_gaps_as_windows(tmp_path):
    # Windows drawn as blocks on a window layer: their glass is read, each gap a window,
    # never a doorway (between columns onto a courtyard, a room would join it) nor a door
    # (a window block as thin as a door leaf is no door on a layer named for windows)
    for door, at in ((True, [8.8, 11.2]), (False, [8.8, 10.0, 11.2])):
        ways, ex = _window_blocks(tmp_path, at, door=door)
        panes = [o for o in ways if o.source != "door"]
        assert len(panes) == len(at) and {o.source for o in panes} == {"window"}, [(o.source, o.point) for o in ways]
        assert len([o for o in ways if o.source == "door"]) == (1 if door else 0)


def test_a_side_light_drawn_as_a_frame_between_a_pier_and_a_door_closes_its_gap():
    # An entrance in a glass front: a pier, a side light 1.3 m long (more than a doorway, so
    # nothing closes it but its glass), then a double door's leaves shut. The glass is drawn
    # as a thin closed frame, not a line: its middle line carries the wall from the pier to
    # the door; a closed shape that is no pane (a box) carries nothing
    from shapely.geometry import LineString, box
    from shapely.ops import unary_union

    from storeypath.walls import _connectors

    pier = box(0.0, 1.35, 1.1, 2.55)
    leaf = box(0.13, -1.15, 0.34, 0.02)  # a leaf shut, as the door's bar has it
    pane = LineString([(0.13, 0.0), (0.18, 0.0), (0.18, 1.3), (0.13, 1.3), (0.13, 0.0)])
    crate = LineString([(0.3, 0.2), (1.0, 0.2), (1.0, 0.9), (0.3, 0.9), (0.3, 0.2)])
    got = _connectors(unary_union([pier, leaf]), [pane, crate], 4.0)
    assert len(got) == 1 and abs(got[0].length - 1.3) < 0.01 and abs(got[0].centroid.x - 0.155) < 0.01


def test_a_tag_says_whether_an_opening_is_a_door_or_a_window(tmp_path):
    _, door = _glazed_doorway(tmp_path, "D2")
    assert (door.source, door.tag, door.issues) == ("door", "D2", [])
    (tmp_path / "w").mkdir()
    _, window = _glazed_doorway(tmp_path / "w", "W-3")
    assert (window.source, window.tag) == ("window", "W3")


def test_a_lift_drawn_without_a_door_opens_onto_the_lobby(tmp_path):
    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.label == ["LIFT"])
    cells[i] = replace(cells[i], door=None)
    ex = _plan(tmp_path, cells)
    lift = next(k for k, s in enumerate(ex.spaces) if s.name == "LIFT")
    assert not any(lift in d.connects for d in ex.doors)
    add_lift_doors(ex)
    door = next(d for d in ex.doors if lift in d.connects)
    other = ex.spaces[next(k for k in door.connects if k != lift)]
    assert door.source == "assumed" and other.name == "ELEV. LOBBY"  # not the WC next to it
    assert door.issues and "check it" in door.issues[0]
    assert abs(door.span.length - 0.9) < 1e-6 and abs(door.point.x - (ORIGIN[0] + 3)) < 0.2  # in their shared wall


def test_a_layer_named_for_doors_holding_the_walls_is_walls(tmp_path):
    # Drafters sometimes draw walls on whatever layer is current: here every wall is
    # on "jundoor". Its name says doors, but it holds the whole wall frame.
    def move(doc):
        doc.layers.add("jundoor")
        for e in doc.modelspace().query('LINE[layer=="A-WALL"]'):
            e.dxf.layer = "jundoor"

    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1), area_outlines=False, walls="lines")
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    move(doc)
    doc.saveas(tmp_path / "plan.dxf")
    roles = {r.layer: r.roles for r in analyse(read_drawing(tmp_path / "plan.dxf"), 0.001, None, None).roles}
    assert "walls" in roles["jundoor"]
