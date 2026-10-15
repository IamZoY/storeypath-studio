"""A drawing as its own program shows it, for Studio's drawing viewer (review_app/drawview.js):
the whole of its model space, every layer of it (lines, curves, hatches, solid fills, texts,
blocks, dimensions) worked out by ezdxf's drawing frontend as Studio's prints are
(printing.py), in the drawing's own colours (ACI 7 white, on the dark ground CAD programs
draw model space on), and packed small for a browser to draw with WebGL.

What is drawn is recorded, not painted: lines and curves as straight pieces (a curve made
straight to within FLATTEN of the drawing's main body), fills as triangles (made here: a
hole stays a hole), each kept with its layer and colour; a text as its words, where it
stands, how it turns and how wide ezdxf sets it, for the page to write in the browser's
own letters, sharp at any zoom. A layer turned off or frozen in the drawing is drawn too,
said so: the viewer hides it until it is shown.

A big floor's drawing is millions of pieces, and most of what makes it big is what nobody
sees: a linetype defined for other units, its dashes a fraction of a millimetre (drawn as
the line it looks like, DASHES_FINE), a hatch of fine dots over a whole site (tens of
millions of them: a tint of its colour instead, MAX_HATCH_PIECES), and the same line drawn
twice by overlapping hatches (drawn once). Points are kept to a grid finer than the
curves' straightening (SNAP), so the same line drawn a hair apart is one, and the numbers
compress well.

A view is one gzip file (write_view; its make-up under FORMAT below), kept in Studio's
cache by the drawing's content and VIEW_VERSION (server.Studio.view)."""

from __future__ import annotations

import gzip
import json
import math
import os
import struct
import time
from array import array
from collections import Counter
from pathlib import Path
from typing import Callable

import numpy as np
from ezdxf.addons.drawing.backend import BackendInterface
from ezdxf.addons.drawing.config import Configuration, HatchPolicy, TextPolicy
from ezdxf.addons.drawing.frontend import UniversalFrontend
from ezdxf.addons.drawing.pipeline import (BackendStage2d, ClippingStage2d, LinetypeStage2d, RenderPipeline2d,
                                           prepare_string_for_rendering)
from ezdxf.math import Vec2

# FORMAT: gzipped, MAGIC, the head's length (uint32), the head (JSON, UTF-8, padded with
# spaces to a multiple of 4 bytes), then the arrays, each where the head's "arrays" says
# (its offset and length in bytes, counted from the end of the head), little-endian, and
# a byte at a time: every number's first byte, then every number's second… (numbers alike
# compress well so):
#   lines   float32: x, y, two points a line (a curve: its straight pieces)
#   fills   float32: x, y, three points a triangle
#   points  float32: x, y
#   texts   float32, eight a text: x, y where its first line's baseline starts; a, b, c, d
#           its turn and stretch (a point u along the line, v up from it, of letters one
#           unit tall, is at x + a·u + c·v, y + b·u + d·v); its cap height; its width as
#           ezdxf sets it (0: not known)
#   styles  uint16, two a text: its layer and its colour (in the head's lists)
# Points are in drawing units from the head's "origin" (the middle of the main body): a
# float32 holds a building's millimetres there to well under one. The head's "batches"
# say which lines, fills and points are of which layer and colour: kind, layer, colour,
# the first point and how many points (lines, fills and points each in an array of their
# own, a batch after another).
MAGIC = b"SPDV"
VIEW_VERSION = 1  # a view made by an older Studio is made again
FLATTEN = 1e-5  # a curve's straight pieces stray from it by at most this share of the main body's size
DASHES_FINE = 1e-4  # a line's dashes repeating within this share of the main body's size: drawn as a line
MAX_HATCH_PIECES = 100_000  # a hatch whose pattern would be more dashes and dots than this: a tint
TINT = "40"  # its opacity (of ff): a quarter
SNAP = 4  # points kept to a grid of a power of two drawing units, at most the straightening's tolerance / SNAP
BODY_GAP = 1.0  # a part farther from the main body than this share of its size is a stray
BODY_SAMPLE = 200_000  # points enough to find the main body by
PROGRESS_S = 3.0  # how often drawing says how far it has got
FILL_AT_ONCE = 5000  # fills triangulated together
BACKGROUND = "#212830"  # model space's ground in CAD programs (ezdxf's too): ACI 7 is white on it


def write_view(drawing: str | Path, out: str | Path, say: Callable[[str], None] | None = None, lock=None) -> dict:
    """The view of a drawing file (DXF or DWG, read as Studio reads it, with cad's repairs)
    written to ``out`` (a file beside it first, then put in place). What it holds, in
    numbers (lines, triangles, points, texts, entities, bytes, seconds). ``lock``: as
    render's. The drawing is not kept in memory once drawn (cad.read_drawing keeps the
    last few: a big one is a GB or two, and the file is a copy made for this)."""
    from .cad import read_drawing_to_change

    say = say or (lambda line: None)
    started = time.monotonic()
    say(f"reading {Path(drawing).name}")
    doc = read_drawing_to_change(drawing)
    view = render(doc, say, lock)
    say(f"writing it: {view.summary()}")
    data = view.packed()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.name}.{os.getpid()}.part")
    part.write_bytes(data)
    os.replace(part, out)
    return {**view.numbers(), "bytes": len(data), "seconds": round(time.monotonic() - started, 1)}


def render(doc, say: Callable[[str], None] | None = None, lock=None) -> "View":
    """The drawing's model space as it is drawn (View). ``lock``: held while ezdxf draws
    (its caches are shared by the threads of one process: vision._DRAWING)."""
    from contextlib import nullcontext

    from ezdxf.addons.drawing import RenderContext

    say = say or (lambda line: None)
    msp = doc.modelspace()
    total = len(msp)
    body = main_body(_anchors(msp))
    size = math.hypot(body[2] - body[0], body[3] - body[1]) if body else 0.0
    recorder = _Recorder(FLATTEN * size if size > 0 else 1e-3)
    fine = DASHES_FINE * size
    ctx = RenderContext(doc)
    hidden: set[str] = set()

    def every_layer(layers) -> None:  # (drawn all the same; said to be off)
        for layer in layers:
            if not layer.is_visible:
                hidden.add(layer.layer)
                layer.is_visible = True

    ctx.set_layer_properties_override(every_layer)
    recorder.spelled = {doc_layer.dxf.name.lower(): doc_layer.dxf.name for doc_layer in doc.layers}
    config = Configuration(text_policy=TextPolicy.FILLING, max_flattening_distance=recorder.tolerance)
    seen = [0, time.monotonic()]

    def counted(entity) -> bool:
        seen[0] += 1
        if time.monotonic() - seen[1] > PROGRESS_S:
            seen[1] = time.monotonic()
            say(f"drawing it: {seen[0]:,} of {total:,} entities")
        return True

    say(f"drawing it: {total:,} entities")
    with lock if lock is not None else nullcontext():
        frontend = _Frontend(ctx, _Pipeline(recorder, fine), config)
        frontend.draw_layout(msp, finalize=True, filter_func=counted)
    layers = {lp.layer: lp.color for lp in ctx.layers.values()}
    say(f"filling {sum(len(f) for f in recorder.fills.values()):,} hatches and solids")
    return View.of(recorder, layers, hidden, len(doc.layers), _units(doc), total)


def _units(doc) -> str | None:
    from .cad import header_units

    return header_units(doc)


# ---- where most of a drawing is ------------------------------------------------------------

def main_body(xy: np.ndarray | None) -> tuple[float, float, float, float] | None:
    """Where most of a drawing is, (x0, y0, x1, y1): the middle half of its points, each
    way, grown outwards over what lies near it, a gap at a time no wider than BODY_GAP of
    the body so far. A stray far off (a line left at the origin, a legend a kilometre away)
    is not reached, and does not shrink the drawing to a speck when it is fitted to the
    window. None when there are no points."""
    if xy is None or not len(xy):
        return None
    xy = xy[np.isfinite(xy).all(axis=1)]
    if not len(xy):
        return None
    if len(xy) > BODY_SAMPLE:
        xy = xy[:: math.ceil(len(xy) / BODY_SAMPLE)]
    (x0, x1), (y0, y1) = _reach(xy[:, 0]), _reach(xy[:, 1])
    return (x0, y0, x1, y1)


def _reach(values: np.ndarray) -> tuple[float, float]:
    """The main body along one axis (main_body)."""
    v = np.sort(values)
    n = len(v)
    lo, hi = float(v[n // 4]), float(v[min(n - 1, (3 * n) // 4)])
    least = 1e-9 * max(abs(float(v[0])), abs(float(v[-1])), 1.0)  # (a body of one point: only what is on it)
    up = v[v > hi]
    if len(up):
        before = np.concatenate(([hi], up[:-1]))
        near = (up - before) <= BODY_GAP * np.maximum(before - lo, least)
        k = len(up) if near.all() else int(np.argmin(near))
        if k:
            hi = float(up[k - 1])
    down = v[v < lo][::-1]
    if len(down):
        before = np.concatenate(([lo], down[:-1]))
        near = (before - down) <= BODY_GAP * np.maximum(hi - before, least)
        k = len(down) if near.all() else int(np.argmin(near))
        if k:
            lo = float(down[k - 1])
    return lo, hi


def _anchors(msp) -> np.ndarray:
    """A point of each entity of the model space shown (where it is put, starts, or its
    middle), found without drawing it: to make curves straight to the drawing's size
    before it is drawn."""
    off = {layer.dxf.name.lower() for layer in msp.doc.layers if layer.is_off() or layer.is_frozen()}
    points = []
    for e in msp:
        if e.dxf.layer.lower() in off:
            continue
        try:
            p = _anchor(e)
        except Exception:  # noqa: BLE001 (a damaged entity: left out)
            continue
        if p is not None:
            points.append(p)
    return np.array(points, dtype=float).reshape(-1, 2)


def _anchor(e):
    kind = e.dxftype()
    if kind == "LWPOLYLINE":
        return (e[0][0], e[0][1]) if len(e) else None
    if kind == "POLYLINE":
        return tuple(e.vertices[0].dxf.location)[:2] if len(e.vertices) else None
    if kind == "SPLINE":
        pts = e.control_points if len(e.control_points) else e.fit_points
        return (pts[0][0], pts[0][1]) if len(pts) else None
    if kind == "HATCH" or kind == "MPOLYGON":
        for path in e.paths:
            if hasattr(path, "vertices") and len(path.vertices):
                return path.vertices[0][0], path.vertices[0][1]
            if hasattr(path, "edges") and path.edges:
                edge = path.edges[0]
                at = edge.start if hasattr(edge, "start") else getattr(edge, "center", None)
                return (at[0], at[1]) if at is not None else None
        return None
    for name in ("insert", "start", "center", "location", "defpoint", "vtx0"):
        if e.dxf.hasattr(name):
            at = e.dxf.get(name)
            return at[0], at[1]
    return None


# ---- what ezdxf draws, recorded ------------------------------------------------------------

class _Frontend(UniversalFrontend):
    """ezdxf's drawing frontend, a hatch whose pattern would be more than MAX_HATCH_PIECES
    dashes and dots (fine sand over a whole site) filled with a tint of its colour, as it
    reads from afar: drawn a dot at a time it is tens of millions of pieces too small to
    see, minutes and gigabytes to draw (and ezdxf gives up on one after its timeout)."""

    def draw_hatch_entity(self, entity, properties, *, loops=None) -> None:
        from ezdxf.addons.drawing.properties import Filling

        filling = properties.filling
        if filling is not None and filling.type == Filling.PATTERN and self.config.hatch_policy == HatchPolicy.NORMAL \
                and _pieces(entity) > MAX_HATCH_PIECES:
            properties.filling = Filling()
            properties.color = properties.color[:7] + TINT
        super().draw_hatch_entity(entity, properties, loops=loops)


def _pieces(polygon) -> float:
    """About how many dashes and dots a hatch's pattern draws: its lines across its
    extents, and the marks along them (more than it draws, for a shape that is not a
    rectangle)."""
    from ezdxf.render import hatching

    try:
        corners = [(v.x, v.y) for p in hatching.hatch_boundary_paths(polygon, filter_text_boxes=True)
                   for v in p.control_vertices()]
        lines = polygon.pattern.lines if polygon.pattern is not None else []
    except Exception:  # noqa: BLE001 (drawn as ezdxf draws it)
        return 0.0
    if not corners:
        return 0.0
    (x0, y0), (x1, y1) = np.min(corners, axis=0), np.max(corners, axis=0)
    reach, area = math.hypot(x1 - x0, y1 - y0), (x1 - x0) * (y1 - y0)
    pieces = 0.0
    for line in lines:
        apart = abs(Vec2.from_deg_angle(line.angle).det(Vec2(line.offset)))  # (between the pattern's lines)
        if apart <= 0:
            return math.inf
        marks = line.dash_length_items
        period = math.fsum(abs(m) for m in marks)
        pieces += reach / apart + (area / (apart * period) * len(marks) / 2 if marks and period > 0 else 0)
    return pieces

class _Pipeline(RenderPipeline2d):
    """ezdxf's 2D drawing pipeline, a text handed to the recorder as words where it
    stands, instead of the outlines of its letters; a line whose dashes repeat within
    ``fine`` (drawing units) drawn whole (_Dashes)."""

    def __init__(self, backend, fine: float = 0.0):
        self.fine = fine
        super().__init__(backend)

    def build_render_pipeline(self):
        backend = BackendStage2d(self.backend, converter=self.get_backend_properties)
        dashes = _Dashes(self.config, get_ltype_scale=self.get_vp_ltype_scale, next_stage=backend)
        dashes.fine = self.fine
        return ClippingStage2d(self.config, self.clipping_portal, next_stage=dashes)

    def draw_text(self, text, transform, properties, cap_height, dxftype="TEXT") -> None:
        if not text.strip() or self.config.text_policy == TextPolicy.IGNORE:
            return
        text = prepare_string_for_rendering(text, dxftype)
        portal = self.clipping_portal
        if portal.is_active and portal.clip_point(Vec2(transform.transform((0, 0, 0)))) is None:
            return  # (a text of a clipped block, outside the clip)
        font = properties.font if properties.font is not None else self.default_font_face
        try:
            width = self.text_engine.get_text_line_width(text, font, cap_height)
        except Exception:  # noqa: BLE001 (a font that cannot be read: the browser's letters, as wide as they are)
            width = 0.0
        self.backend.text(text, transform, cap_height, width, self.get_backend_properties(properties))


class _Dashes(LinetypeStage2d):
    """ezdxf's linetypes, but a pattern of dashes repeating within ``fine`` (a linetype
    defined for other units, or scaled down) drawn as a line, as a CAD program shows it:
    as dashes it is millions of pieces too small to see, most of a drawing's size."""

    fine = 0.0

    def _whole(self, properties) -> bool:
        pattern = self.pattern(properties) if len(properties.linetype_pattern) >= 2 else ()
        return len(pattern) >= 2 and sum(pattern) < self.fine

    def draw_line(self, start, end, properties):
        if self._whole(properties):
            self.next_stage.draw_line(Vec2(start), Vec2(end), properties)
        else:
            super().draw_line(start, end, properties)

    def draw_path(self, path, properties):
        if self._whole(properties):
            self.next_stage.draw_path(path, properties)
        else:
            super().draw_path(path, properties)


class _Recorder(BackendInterface):
    """ezdxf's drawing, recorded (see the module): lines, points and fills by (layer,
    colour), texts, how many things each layer has (an entity counts when it draws
    something itself: a block's pieces count, the block does not), and where each entity
    of the model space is (the first point it draws: its anchor, to find the main body by,
    a hatch of a thousand strokes counting once)."""

    def __init__(self, tolerance: float):
        self.tolerance = tolerance
        self.keys: dict[tuple[str, str], int] = {}  # (layer, colour) → its number
        self.lines: dict[int, array] = {}  # x0, y0, x1, y1, …
        self.points: dict[int, array] = {}  # x, y, …
        self.fills: dict[int, list[list[np.ndarray]]] = {}  # each fill: its rings, (n, 2)
        self.texts: list[tuple] = []  # (words, x, y, a, b, c, d, cap height, width, key)
        self.counts: Counter = Counter()  # layer → things drawn on it
        self.background = BACKGROUND
        self.spelled: dict[str, str] = {}  # a layer's name as its entities have it, any case → as the drawing has it
        self.anchors = array("d")  # x, y of each entity of the model space…
        self.anchor_keys = array("i")  # …and what it was drawn as
        self._entered: list[list] = []  # [drew something, its layer] of each entity being drawn
        self._anchored = False  # the entity of the model space being drawn has its anchor

    def _layer(self, name: str) -> str:
        return self.spelled.get(name.lower(), name)

    def _key(self, properties, x: float, y: float) -> int:
        """The batch of what is drawn so, from (x, y) on."""
        key = (self._layer(properties.layer), properties.color)
        if key not in self.keys:
            self.keys[key] = len(self.keys)
        k = self.keys[key]
        if self._entered:
            self._entered[-1][0] = True
            if not self._anchored and math.isfinite(x) and math.isfinite(y):
                self.anchors.extend((x, y))
                self.anchor_keys.append(k)
                self._anchored = True
        return k

    def _flat(self, path) -> list[np.ndarray]:
        """A path's straight pieces: each part of it (a path may hold several) as its points."""
        parts = path.sub_paths() if path.has_sub_paths else [path]
        out = []
        for p in parts:
            if p.has_curves:
                xy = np.array([(v.x, v.y) for v in p.flattening(self.tolerance, segments=2)], dtype=float)
            else:
                xy = np.asarray(p.np_vertices(), dtype=float)[:, :2]
            if len(xy):
                out.append(xy.reshape(-1, 2))
        return out

    # ---- BackendInterface --------------------------------------------------------------

    def configure(self, config) -> None:
        self.config = config

    def enter_entity(self, entity, properties) -> None:
        if not self._entered:
            self._anchored = False
        self._entered.append([False, self._layer(properties.layer)])

    def exit_entity(self, entity) -> None:
        drew, layer = self._entered.pop()
        if drew:
            self.counts[layer] += 1

    def set_background(self, color) -> None:
        self.background = color

    def draw_point(self, pos, properties) -> None:
        self.points.setdefault(self._key(properties, pos.x, pos.y), array("d")).extend((pos.x, pos.y))

    def draw_line(self, start, end, properties) -> None:
        self.lines.setdefault(self._key(properties, start.x, start.y), array("d")).extend(
            (start.x, start.y, end.x, end.y))

    def draw_solid_lines(self, lines, properties) -> None:
        drawn = np.array([(s.x, s.y, e.x, e.y) for s, e in lines], dtype=float)
        if len(drawn):
            self.lines.setdefault(self._key(properties, drawn[0, 0], drawn[0, 1]), array("d")).frombytes(
                drawn.tobytes())

    def draw_path(self, path, properties) -> None:
        for xy in self._flat(path):
            if len(xy) >= 2:
                self.lines.setdefault(self._key(properties, xy[0, 0], xy[0, 1]), array("d")).frombytes(
                    np.hstack([xy[:-1], xy[1:]]).tobytes())

    def draw_filled_paths(self, paths, properties) -> None:
        rings = [xy for p in paths for xy in self._flat(p) if len(xy) >= 3]
        if rings:
            self.fills.setdefault(self._key(properties, rings[0][0, 0], rings[0][0, 1]), []).append(rings)

    def draw_filled_polygon(self, points, properties) -> None:
        if properties.color[:7] == self.background[:7]:
            return  # a wipeout: the ground over what is beneath (the viewer draws fills under the lines)
        xy = np.asarray(points.np_vertices(), dtype=float).reshape(-1, 2)
        if len(xy) >= 3:
            self.fills.setdefault(self._key(properties, xy[0, 0], xy[0, 1]), []).append([xy])

    def draw_image(self, image_data, properties) -> None:
        """An image (its file is not kept with the drawing): its frame."""
        try:
            w, h = image_data.image_size()
            m = image_data.flip_matrix() * image_data.transform
            corners = [m.transform((x, y, 0)) for x, y in ((0, 0), (w, 0), (w, h), (0, h), (0, 0))]
        except Exception:  # noqa: BLE001
            return
        self.draw_solid_lines(list(zip(corners, corners[1:])), properties)

    def clear(self) -> None:
        pass

    def finalize(self) -> None:
        pass

    # ---- from _Pipeline ----------------------------------------------------------------

    def text(self, words: str, m, cap: float, width: float, properties) -> None:
        if not (math.isfinite(m[3, 0]) and math.isfinite(m[3, 1]) and cap > 0):
            return
        self.texts.append((words, m[3, 0], m[3, 1], m[0, 0], m[0, 1], m[1, 0], m[1, 1], cap, width,
                           self._key(properties, m[3, 0], m[3, 1])))


# ---- the view --------------------------------------------------------------------------

class View:
    """A drawing, drawn and packed (see FORMAT): the head, and its arrays."""

    def __init__(self, head: dict, arrays: dict[str, np.ndarray]):
        self.head, self.arrays = head, arrays

    @classmethod
    def of(cls, rec: _Recorder, layer_colours: dict, hidden: set, layers_in_drawing: int, units, entities: int):
        lines = {k: _finite(np.frombuffer(a, dtype=float).reshape(-1, 4)) for k, a in rec.lines.items()}
        points = {k: _finite(np.frombuffer(a, dtype=float).reshape(-1, 2)) for k, a in rec.points.items()}
        texts = [t for t in rec.texts if all(math.isfinite(v) for v in t[1:9])]
        # the main body of what is shown (a layer off in the drawing is not, until a person shows it)
        anchors = np.frombuffer(rec.anchors, dtype=float).reshape(-1, 2)
        shown = np.array([layer not in hidden for (layer, _), _k in sorted(rec.keys.items(), key=lambda kv: kv[1])],
                         dtype=bool)
        on = shown[np.frombuffer(rec.anchor_keys, dtype=np.int32)] if len(anchors) else np.zeros(0, dtype=bool)
        body = main_body(anchors[on] if on.any() else anchors)
        bounds = _bounds(lines, points, rec.fills, texts)
        if body is not None:  # (and as far as what is drawn there reaches: a long line's other end)
            x0, y0, x1, y1 = body
            gx, gy = BODY_GAP * (x1 - x0), BODY_GAP * (y1 - y0)
            body = _bounds(lines, points, rec.fills, texts, (x0 - gx, y0 - gy, x1 + gx, y1 + gy)) or body
        if body is None or bounds is None:
            body = bounds = (0.0, 0.0, 1.0, 1.0)
        grid = 2.0 ** math.floor(math.log2(rec.tolerance / SNAP))
        ox, oy = (round((body[i] + body[i + 2]) / 2 / grid) * grid for i in (0, 1))
        at = np.array([ox, oy], dtype=float)

        names = sorted({layer for layer, _ in rec.keys}, key=str.casefold)
        layer_no = {name: i for i, name in enumerate(names)}
        colours = sorted({colour for _, colour in rec.keys})
        colour_no = {c: i for i, c in enumerate(colours)}
        style = {k: (layer_no[layer], colour_no[colour]) for (layer, colour), k in rec.keys.items()}

        batches, out = [], {"lines": [], "fills": [], "points": []}
        made = {"lines": 0, "fills": 0, "points": 0}

        def batch(kind: str, key: int, xy: np.ndarray) -> None:
            if len(xy):
                layer, colour = style[key]
                batches.append({"kind": kind, "layer": layer, "color": colour, "first": made[kind], "count": len(xy)})
                out[kind].append(xy)
                made[kind] += len(xy)

        for key in sorted(rec.keys.values(), key=lambda k: style[k]):
            segments, dots = _lines(_snap(lines.get(key, np.zeros((0, 4))) - np.tile(at, 2), grid))
            batch("lines", key, segments.reshape(-1, 2))
            more = _snap(points.get(key, np.zeros((0, 2))) - at, grid)
            batch("points", key, _unique_rows(np.concatenate([more, dots])))
            batch("fills", key, _snap(_triangles(rec.fills.get(key, []), at), grid).reshape(-1, 2))

        text_xy = np.array([t[1:9] for t in texts], dtype=float).reshape(-1, 8)
        text_xy[:, :2] -= at
        arrays = {kind: (np.concatenate(out[kind]) if out[kind] else np.zeros((0, 2))).astype("<f4")
                  for kind in ("lines", "fills", "points")}
        arrays["texts"] = text_xy.astype("<f4")
        arrays["styles"] = np.array([style[t[9]] for t in texts], dtype="<u2").reshape(-1, 2)
        layers = [{"name": n, "color": _hex(layer_colours.get(n)), "count": rec.counts.get(n, 0), "off": n in hidden}
                  for n in names]
        head = {"format": VIEW_VERSION, "origin": [ox, oy], "grid": grid, "bounds": [round(v, 3) for v in bounds],
                "body": [round(v, 3) for v in body], "background": rec.background[:7], "units": units,
                "layers": layers, "empty_layers": max(0, layers_in_drawing - len(names)), "colors": colours,
                "batches": batches, "words": [t[0] for t in texts],
                "counts": {"lines": len(arrays["lines"]) // 2, "triangles": len(arrays["fills"]) // 3,
                           "points": len(arrays["points"]), "texts": len(texts), "entities": entities}}
        return cls(head, arrays)

    def numbers(self) -> dict:
        return dict(self.head["counts"])

    def summary(self) -> str:
        c = self.head["counts"]
        return f"{c['lines']:,} lines, {c['triangles']:,} triangles, {c['points']:,} points, {c['texts']:,} texts"

    def packed(self) -> bytes:
        """The view as the page reads it (FORMAT), gzipped."""
        blobs, at, places = [], 0, {}
        for name in ("lines", "fills", "points", "texts", "styles"):
            a = np.ascontiguousarray(self.arrays[name])
            blob = np.frombuffer(a.tobytes(), dtype=np.uint8).reshape(-1, a.itemsize).T.tobytes()  # (a byte at a time)
            places[name] = [at, len(blob)]
            blobs.append(blob + b"\0" * (-len(blob) % 4))
            at += len(blobs[-1])
        head = json.dumps({**self.head, "arrays": places}, ensure_ascii=False, separators=(",", ":")).encode()
        head += b" " * (-len(head) % 4)
        return gzip.compress(MAGIC + struct.pack("<I", len(head)) + head + b"".join(blobs), compresslevel=6)


def read_view(data: bytes) -> tuple[dict, dict[str, np.ndarray]]:
    """A view as written (packed): its head and its arrays (lines, fills and points as
    (n, 2), texts (n, 8), styles (n, 2)). For the tests, and anyone checking one."""
    raw = gzip.decompress(data)
    if raw[:4] != MAGIC:
        raise ValueError("not a drawing's view")
    (n,) = struct.unpack("<I", raw[4:8])
    head = json.loads(raw[8:8 + n])
    rest = memoryview(raw)[8 + n:]
    shapes = {"lines": ("<f4", 2), "fills": ("<f4", 2), "points": ("<f4", 2), "texts": ("<f4", 8), "styles": ("<u2", 2)}
    arrays = {}
    for name, (kind, width) in shapes.items():
        at, size = head["arrays"][name]
        item = np.dtype(kind).itemsize
        planes = np.frombuffer(rest[at:at + size], dtype=np.uint8).reshape(item, -1)
        arrays[name] = np.frombuffer(planes.T.tobytes(), dtype=kind).reshape(-1, width)
    return head, arrays


# ---- packing ---------------------------------------------------------------------------

def _finite(a: np.ndarray) -> np.ndarray:
    return a[np.isfinite(a).all(axis=1)] if len(a) else a


def _bounds(lines: dict, points: dict, fills: dict, texts: list, within=None):
    """Where anything at all is drawn (x0, y0, x1, y1; None: nothing is), the main body and
    every stray; ``within`` (x0, y0, x1, y1): of what is drawn in it."""
    lo, hi = np.full(2, np.inf), np.full(2, -np.inf)
    for xy in [a.reshape(-1, 2) for a in lines.values()] + list(points.values()) + \
              [r for f in fills.values() for rings in f for r in rings[:1]] + \
              [np.array([(t[1], t[2]) for t in texts]).reshape(-1, 2)]:
        xy = xy[np.isfinite(xy).all(axis=1)] if len(xy) else xy
        if len(xy) and within is not None:
            x0, y0, x1, y1 = within
            xy = xy[(xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)]
        if len(xy):
            lo, hi = np.minimum(lo, xy.min(axis=0)), np.maximum(hi, xy.max(axis=0))
    return (float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])) if lo[0] <= hi[0] else None


def _snap(a: np.ndarray, grid: float) -> np.ndarray:
    """Coordinates (from the origin) on the grid, as float32: a power of two's multiples
    end in noughts, which compress."""
    return (np.round(a / grid) * grid).astype(np.float32)


def _lines(s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lines (x0, y0, x1, y1, float32 from the origin) each once (the same line drawn twice,
    either way round, is drawn once: overlapping hatches draw many twice), and the lines of
    no length, as the points they are."""
    if not len(s):
        return s, np.zeros((0, 2), dtype=np.float32)
    swap = (s[:, 0] > s[:, 2]) | ((s[:, 0] == s[:, 2]) & (s[:, 1] > s[:, 3]))
    s[swap] = s[swap][:, [2, 3, 0, 1]]
    dot = (s[:, 0] == s[:, 2]) & (s[:, 1] == s[:, 3])
    return _unique_rows(s[~dot]), s[dot, :2]


def _unique_rows(a: np.ndarray) -> np.ndarray:
    """Each row once (in no order that matters: a batch is one colour on one layer)."""
    if len(a) < 2:
        return a
    a = np.ascontiguousarray(a)
    _, first = np.unique(a.view(np.dtype((np.void, a.dtype.itemsize * a.shape[1]))).ravel(), return_index=True)
    return a[first]


def _triangles(fills: list[list[np.ndarray]], at: np.ndarray) -> np.ndarray:
    """Fills (each its rings: an outline, its holes, islands in them; filled even-odd, as a
    hatch is) as triangles, (n, 3, 2) float32 from ``at``. A three- or four-sided one that
    is convex is cut by hand; the rest by shapely (constrained Delaunay: the holes stay
    holes)."""
    import shapely

    made, rest = [], []
    for rings in fills:
        if len(rings) == 1:
            xy = rings[0]
            if len(xy) > 3 and np.allclose(xy[0], xy[-1]):
                xy = xy[:-1]
            if len(xy) == 3:
                made.append(xy[None, :, :])
                continue
            if len(xy) == 4 and _convex(xy):
                made.append(xy[[0, 1, 2, 0, 2, 3]].reshape(2, 3, 2))
                continue
        rest.append(rings)
    for k in range(0, len(rest), FILL_AT_ONCE):
        shapes = [_area(rings) for rings in rest[k:k + FILL_AT_ONCE]]
        shapes = [s for s in shapes if s is not None and not s.is_empty]
        if not shapes:
            continue
        try:
            cut = shapely.constrained_delaunay_triangles(np.array(shapes, dtype=object))
            xy = shapely.get_coordinates(shapely.get_parts(cut))
        except Exception:  # noqa: BLE001 (one at a time: one that fails is left out)
            xy = []
            for s in shapes:
                try:
                    xy.append(shapely.get_coordinates(shapely.get_parts(shapely.constrained_delaunay_triangles(s))))
                except Exception:  # noqa: BLE001
                    continue
            xy = np.concatenate(xy) if xy else np.zeros((0, 2))
        if len(xy):
            made.append(xy.reshape(-1, 4, 2)[:, :3])
    if not made:
        return np.zeros((0, 3, 2), dtype=np.float32)
    tri = np.concatenate(made)
    tri = tri[np.isfinite(tri).all(axis=(1, 2))]
    return (tri - at).astype(np.float32)


def _convex(xy: np.ndarray) -> bool:
    d = np.roll(xy, -1, axis=0) - xy
    cross = d[:, 0] * np.roll(d, -1, axis=0)[:, 1] - d[:, 1] * np.roll(d, -1, axis=0)[:, 0]
    return bool((cross > 0).all() or (cross < 0).all())


def _area(rings: list[np.ndarray]):
    """The area rings fill, even-odd: an outline, less its holes, with the islands in them."""
    import shapely
    from shapely.geometry import MultiPolygon, Polygon

    def polygonal(g):
        if g.geom_type in ("Polygon", "MultiPolygon"):
            return g
        parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
        return shapely.union_all(parts) if parts else None

    area = None
    for xy in rings:
        if len(xy) < 3:
            continue
        try:
            ring = polygonal(shapely.make_valid(Polygon(xy)))
        except Exception:  # noqa: BLE001
            continue
        if ring is None or ring.is_empty:
            continue
        try:
            area = ring if area is None else polygonal(area.symmetric_difference(ring))
        except Exception:  # noqa: BLE001 (rings that cannot be put together: the first stands)
            continue
    if area is None:
        return None
    return area if area.geom_type in ("Polygon", "MultiPolygon") else MultiPolygon()


def _hex(colour) -> str | None:
    return colour[:7] if isinstance(colour, str) and colour.startswith("#") else None
