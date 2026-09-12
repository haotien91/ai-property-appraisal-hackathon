from __future__ import annotations

from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.ops import transform

_TO_3826 = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
_TO_4326 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)


def anchor_3826(lat: float, lon: float) -> Point:
    x, y = _TO_3826.transform(lon, lat)
    return Point(x, y)


def roi_square(anchor: Point, size_m: float = 600.0):
    half = size_m / 2.0
    return box(anchor.x - half, anchor.y - half, anchor.x + half, anchor.y + half)


def to_4326(geometry):
    return transform(_TO_4326.transform, geometry)
