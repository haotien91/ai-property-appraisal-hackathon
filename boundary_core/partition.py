from __future__ import annotations

from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union


_DIRECTION_CHECKS = {
    "north_of": lambda p, q: p.y > q.y,
    "south_of": lambda p, q: p.y < q.y,
    "east_of": lambda p, q: p.x > q.x,
    "west_of": lambda p, q: p.x < q.x,
}


def build_barriers(roads: dict[str, object], width_m: float):
    if width_m <= 0:
        raise ValueError("width_m must be positive")
    return unary_union([geom.buffer(width_m, cap_style=2, join_style=2) for geom in roads.values()])


def _polygons(geom) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygons(g))
        return out
    return []


def partition_roi(roi: Polygon, barriers) -> list[Polygon]:
    return [p for p in _polygons(roi.difference(barriers)) if not p.is_empty and p.area > 0]


def _satisfies_direction(cell: Polygon, relation: str, road) -> bool:
    if relation not in _DIRECTION_CHECKS:
        raise ValueError(f"Unsupported directional relation: {relation}")
    p = cell.representative_point()
    _, q = nearest_points(p, road)
    return bool(_DIRECTION_CHECKS[relation](p, q))


def select_candidate(cells: list[Polygon], anchor: Point, directional_roads: dict[str, object]) -> Polygon:
    containing = [c for c in cells if c.contains(anchor) or c.touches(anchor)]
    if not containing:
        raise ValueError("No partition cell contains the anchor")
    valid = [
        c
        for c in containing
        if all(_satisfies_direction(c, relation, road) for relation, road in directional_roads.items())
    ]
    if not valid:
        raise ValueError("Anchor-containing cell violates one or more directional constraints")
    # Usually unique; if not, take the largest valid cell rather than a sliver.
    return max(valid, key=lambda g: g.area)
