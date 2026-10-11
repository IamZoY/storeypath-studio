"""The drawing printed for vision (printing.Batches, extract.drawn_in, vision.FloorPrint):
the same print as ezdxf's own matplotlib backend makes, handed to matplotlib in batches;
what crosses a view printed, and nothing far from it; the views of the largest rooms
cut from one print of the whole floor instead of printing most of the floor again for
each; and answers kept about shapes that cross themselves not stopping a read."""

import io

import ezdxf
import numpy as np
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image
from shapely import wkt
from shapely.geometry import Polygon, box

import storeypath.vision as vision
from storeypath.extract import drawn_in
from storeypath.printing import Batches


def _a_drawn_floor():
    """Rooms with what a plan holds: walls, a hatched wall, a filled column, a door's
    swing, a text, a block placed twice."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    for x in (0, 6, 12):
        msp.add_lwpolyline([(x, 0), (x + 5, 0), (x + 5, 4), (x, 4)], close=True)
        msp.add_text("OFFICE", dxfattribs={"height": 0.3}).set_placement((x + 2, 2))
    wall = msp.add_hatch()
    wall.set_pattern_fill("ANSI31", scale=0.02)
    wall.paths.add_polyline_path([(0, -0.3), (17, -0.3), (17, 0), (0, 0)], is_closed=True)
    column = msp.add_hatch()
    column.paths.add_polyline_path([(5.2, 4.2), (5.8, 4.2), (5.8, 4.8), (5.2, 4.8)], is_closed=True)
    msp.add_arc((3, 4), 0.9, 0, 90)
    msp.add_circle((9, 2), 0.4)
    desk = doc.blocks.new("DESK")
    desk.add_lwpolyline([(0, 0), (1.2, 0), (1.2, 0.6), (0, 0.6)], close=True)
    msp.add_blockref("DESK", (1, 1))
    msp.add_blockref("DESK", (13, 1))
    return doc


def _pixels(doc, view, backend, px=400):
    fig = Figure(figsize=(px / 100, px / 100), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    out = backend(ax)
    Frontend(RenderContext(doc), out, config=vision._print_config()).draw_entities(list(doc.modelspace()))
    if isinstance(out, Batches):
        out.flush()
    ax.set_xlim(view[0], view[2])
    ax.set_ylim(view[1], view[3])
    ax.set_aspect("equal")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor="white")
    return np.asarray(Image.open(buf).convert("L"), dtype=int)


def test_a_print_in_batches_is_the_print_ezdxf_makes():
    doc, view = _a_drawn_floor(), (-1, -8, 18, 11)  # (square, as every print is: the figure is)
    theirs = _pixels(doc, view, MatplotlibBackend)
    ours = _pixels(doc, view, lambda ax: Batches(ax, view=view))
    assert (theirs < 128).sum() > 300  # (something drawn)
    assert (np.abs(theirs - ours) > 64).mean() < 0.001
    # a view of part of it: what lies outside is not handed over, and the print is the same
    part = (5, -1, 12, 6)
    assert (np.abs(_pixels(doc, part, MatplotlibBackend) - _pixels(doc, part, lambda ax: Batches(ax, view=part))) > 64).mean() < 0.001


def test_what_crosses_a_view_is_printed_and_nothing_far_from_it():
    doc = ezdxf.new()
    msp = doc.modelspace()
    across = msp.add_line((-100, 5), (40, 5))  # its middle far to the left, it runs across the view
    near = msp.add_circle((10.6, 5), 0.3)  # just outside, within the margin
    far = msp.add_circle((60, 60), 1)
    sheet = doc.blocks.new("SHEET")  # a whole sheet placed as one block, larger than the view
    sheet.add_line((0, 0), (1, 1))
    sheet.add_line((500, 500), (501, 501))
    msp.add_blockref("SHEET", (0, 0))
    shown = list(drawn_in(doc, (0, 0, 10, 10)))
    assert across in shown and near in shown and far not in shown
    pieces = [e for e in shown if e.dxftype() == "LINE" and e is not across]
    assert [tuple(p.dxf.start)[:2] for p in pieces] == [(0, 0)]  # its piece in the view, not the one far off


def test_the_largest_rooms_are_cut_from_one_print_of_the_whole_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(vision.FloorPrint, "WHOLE_PX", 2048)

    def floor(far_off=False):
        doc = ezdxf.new()
        msp = doc.modelspace()
        for x in range(0, 120, 10):  # a floor 118 m long, in metres
            msp.add_lwpolyline([(x, 0), (x + 8, 0), (x + 8, 30), (x, 30)], close=True)
        if far_off:
            msp.add_line((5000, 5000), (5001, 5001))
        return doc

    doc = floor()
    printed = []
    real = vision._print
    monkeypatch.setattr(vision, "_print", lambda *a, **k: printed.append(a[1]) or real(*a, **k))
    keep = tmp_path / "tiles"
    sheet = vision.FloorPrint(doc, 1.0, keep=keep, sha="c" * 64)
    hall = box(10, 2, 70, 28)
    first = sheet.view(hall.bounds, [hall])
    assert len(printed) == 1 and printed[0][2] - printed[0][0] == 118  # the whole floor, once
    sheet.view((40, -20, 100, 40), [box(45, 2, 95, 28)])  # another: cut from it too
    assert len(printed) == 1
    image = Image.open(io.BytesIO(sheet.view((-80, -80, 0, 0), [])))  # beyond the drawing: paper, not black
    assert np.asarray(image.convert("L")).min() > 200
    again = vision.FloorPrint(doc, 1.0, keep=keep, sha="c" * 64).view(hall.bounds, [hall])
    assert len(printed) == 1 and again == first  # read again: the whole floor's print kept
    # a print of the whole too coarse for a view (a drawing far larger than the floor): drawn on its own
    vision.FloorPrint(floor(far_off=True), 1.0).view(hall.bounds, [hall])
    assert len(printed) == 3 and printed[-1] == hall.bounds


def test_answers_kept_about_a_shape_crossing_itself_do_not_stop_a_read():
    # A shape is kept rounded to the centimetre, and rounding can leave one crossing
    # itself: comparing a room with it must not fail the floor's read
    crossing = "POLYGON ((0 0, 4 4, 4 0, 0 4, 0 0))"
    answers = {"a": {"drawing": "d", "outline": vision.ONE_ROOM, "type": "office", "shape": crossing},
               "b": {"drawing": "d", "outline": vision.ONE_ROOM, "type": "store", "shape": "POLYGON ((10 0, 14 0, 14 4, 10 4, 10 0))"}}
    alike = vision._Alike(answers, "d", None)
    assert alike.to(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])) is None  # (it failed here: "side location conflict")
    assert alike.to(wkt.loads(crossing))["type"] == "office"  # the room's own shape crossing itself too
    assert alike.to(Polygon([(10, 0), (14, 0), (14, 4.1), (10, 4.1)]))["type"] == "store"
