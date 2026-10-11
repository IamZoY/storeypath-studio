"""The drawing printed for the vision model (vision.FloorPrint): the tiles its views are
cut from, kept on disk by the drawing's sha, so a floor read again after a wall is drawn
in review takes them from there instead of printing them again; the tiles of the
drawings read last alone are kept."""

import os

import ezdxf

import storeypath.vision as vision


def _a_drawn_room():
    doc = ezdxf.new()
    msp = doc.modelspace()
    for a, b in (((0, 0), (5, 0)), ((5, 0), (5, 4)), ((5, 4), (0, 4)), ((0, 4), (0, 0))):
        msp.add_line(a, b)
    msp.add_text("OFFICE", dxfattribs={"height": 0.3}).set_placement((2, 2))
    return doc


def test_a_floor_read_again_takes_its_prints_from_the_tiles_kept(tmp_path, monkeypatch):
    # A wall drawn in review: the floor read again, its drawing unchanged. The tiles the
    # vision model's views are cut from are kept on disk, and taken from there.
    printed = []
    real = vision._print
    monkeypatch.setattr(vision, "_print", lambda *a, **k: printed.append(a[1]) or real(*a, **k))
    room, keep = (0.5, 0.5, 4.5, 3.5), tmp_path / "tiles"
    first = vision.FloorPrint(_a_drawn_room(), 1.0, keep=keep, sha="a" * 64).view(room, [])
    assert len(printed) == 1 and list((keep / ("a" * 64)).rglob("*.png"))
    again = vision.FloorPrint(_a_drawn_room(), 1.0, keep=keep, sha="a" * 64).view(room, [])
    assert len(printed) == 1 and again == first  # not printed again: the same view
    vision.FloorPrint(_a_drawn_room(), 1.0, keep=keep, sha="b" * 64).view(room, [])  # another drawing
    assert len(printed) == 2
    vision.FloorPrint(_a_drawn_room(), 1.0).view(room, [])  # none kept: printed
    assert len(printed) == 3


def test_the_tiles_of_the_drawings_read_last_are_kept_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(vision.FloorPrint, "KEEP_DRAWINGS", 2)
    keep = tmp_path / "tiles"
    for i, sha in enumerate(("1" * 64, "2" * 64, "3" * 64)):
        vision.FloorPrint(_a_drawn_room(), 1.0, keep=keep, sha=sha).view((0.5, 0.5, 4.5, 3.5), [])
        os.utime(keep / sha, (1_000_000 + i, 1_000_000 + i))  # (read one after another)
    vision.FloorPrint(_a_drawn_room(), 1.0, keep=keep, sha="3" * 64)
    assert sorted(d.name[0] for d in keep.iterdir()) == ["2", "3"]
