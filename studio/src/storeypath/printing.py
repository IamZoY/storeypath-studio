"""A drawing printed for vision and Review: ezdxf works out what to draw, matplotlib
paints it. ezdxf's own matplotlib backend hands matplotlib an object for every line,
and a floor holds hundreds of thousands of them (a hatch's pattern is a line a stroke):
making those objects is most of a print's time. Batches hands them over a style at a
time, one object a batch, and leaves out what lies wholly outside the view: the same
print, to the pixel, several times sooner.

Under the print's colour policy everything is drawn black on white, so the order
between batches does not change the print; an image (a raster in the drawing) is
drawn in its place, the batches before it first."""

from __future__ import annotations

import numpy as np
from ezdxf.addons.drawing.matplotlib import SCATTER_POINT_SIZE, MatplotlibBackend
from ezdxf.npshapes import to_matplotlib_path
from matplotlib.collections import LineCollection, PathCollection
from matplotlib.path import Path


class Batches(MatplotlibBackend):
    """ezdxf's matplotlib backend, handing matplotlib a batch of each style at once;
    ``view`` (x0, y0, x1, y1, drawing units): what lies wholly outside it is left out.
    ``flush()`` (or ``finalize()``) once the entities are drawn puts the batches on
    the axes."""

    def __init__(self, ax, view: tuple[float, float, float, float] | None = None):
        super().__init__(ax, adjust_figure=False)
        self.view = view
        self._batches: dict[tuple, list] = {}  # (kind, colour, width) → what is drawn so, in order

    def _batch(self, kind: str, color, width: float = 0.0) -> list:
        return self._batches.setdefault((kind, color, width), [])

    def _outside(self, xy: np.ndarray) -> bool:
        if self.view is None or not len(xy):
            return False
        x0, y0, x1, y1 = self.view
        return bool(xy[:, 0].max() < x0 or xy[:, 0].min() > x1 or xy[:, 1].max() < y0 or xy[:, 1].min() > y1)

    def draw_point(self, pos, properties) -> None:
        self._batch("points", properties.color).append((pos.x, pos.y))

    def draw_line(self, start, end, properties) -> None:
        self.draw_solid_lines([(start, end)], properties)

    def draw_solid_lines(self, lines, properties) -> None:
        segments = np.array([(s.x, s.y, e.x, e.y) for s, e in lines], dtype=float).reshape(-1, 4)
        if self.view is not None and len(segments):
            x0, y0, x1, y1 = self.view
            xs, ys = segments[:, 0::2], segments[:, 1::2]
            segments = segments[(xs.max(axis=1) >= x0) & (xs.min(axis=1) <= x1)
                                & (ys.max(axis=1) >= y0) & (ys.min(axis=1) <= y1)]
        dots = (segments[:, 0] == segments[:, 2]) & (segments[:, 1] == segments[:, 3])
        if dots.any():  # a line of no length: a point, as ezdxf's backend draws it
            self._batch("points", properties.color).extend(map(tuple, segments[dots, :2]))
        if not dots.all():
            self._batch("lines", properties.color, self.get_lineweight(properties)).append(segments[~dots])

    def draw_path(self, path, properties) -> None:
        drawn = to_matplotlib_path([path])
        if not self._outside(drawn.vertices):
            self._batch("paths", properties.color, self.get_lineweight(properties)).append(drawn)

    def draw_filled_paths(self, paths, properties) -> None:
        drawn = to_matplotlib_path(paths, detect_holes=True)
        if not self._outside(drawn.vertices):
            self._batch("fills", properties.color).append(drawn)

    def draw_filled_polygon(self, points, properties) -> None:
        xy = np.array([(p.x, p.y) for p in points.vertices()], dtype=float).reshape(-1, 2)
        if len(xy) >= 3 and not self._outside(xy):
            self._batch("fills", properties.color).append(Path(xy))

    def draw_image(self, image_data, properties) -> None:
        self.flush()
        super().draw_image(image_data, properties)

    def flush(self) -> None:
        ax = self.ax
        for (kind, color, width), drawn in self._batches.items():
            z = self._get_z()
            if kind == "lines":
                ax.add_collection(LineCollection(np.concatenate(drawn).reshape(-1, 2, 2), linewidths=width, colors=color,
                                                 capstyle="butt", zorder=z), autolim=False)
            elif kind == "points":
                xs, ys = zip(*drawn)
                ax.scatter(xs, ys, s=SCATTER_POINT_SIZE, c=color, zorder=z)
            elif kind == "paths":
                ax.add_collection(PathCollection(drawn, facecolors="none", edgecolors=color, linewidths=width, zorder=z),
                                  autolim=False)
            else:
                ax.add_collection(PathCollection(drawn, facecolors=color, edgecolors="none", linewidths=0, zorder=z),
                                  autolim=False)
        self._batches = {}

    def finalize(self) -> None:
        self.flush()
