"""Local drawing meters → longitude/latitude (WGS84)."""

from __future__ import annotations

import math

from pyproj import Transformer
from pyproj.enums import TransformDirection
from shapely.ops import transform

from .workspace import Placement


class Georeferencer:
    """Rotates the drawing by the placement bearing around the anchor, then
    projects from an azimuthal-equidistant plane centred on the anchor."""

    def __init__(self, placement: Placement):
        self.placement = placement
        b = math.radians(placement.bearing)
        self._cos, self._sin = math.cos(b), math.sin(b)
        self._to_geo = Transformer.from_crs(
            f"+proj=aeqd +lat_0={placement.lat} +lon_0={placement.lon} +x_0=0 +y_0=0 +ellps=WGS84 +units=m",
            "EPSG:4326",
            always_xy=True,
        )

    def lonlat(self, x, y):
        dx, dy = x - self.placement.x, y - self.placement.y
        east = dx * self._cos + dy * self._sin
        north = -dx * self._sin + dy * self._cos
        return self._to_geo.transform(east, north)

    def local(self, lon, lat):
        """A point on the map in the drawing's metres: lonlat undone."""
        east, north = self._to_geo.transform(lon, lat, direction=TransformDirection.INVERSE)
        dx = east * self._cos - north * self._sin
        dy = east * self._sin + north * self._cos
        return self.placement.x + dx, self.placement.y + dy

    def geometry(self, geom):
        return transform(self.lonlat, geom)
