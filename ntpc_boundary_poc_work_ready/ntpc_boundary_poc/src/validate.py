from __future__ import annotations

from dataclasses import dataclass

from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union




@dataclass(frozen=True)
class ZoneConstraintReport:
    valid: bool
    result_area_m2: float
    inside_area_m2: float
    outside_area_m2: float
    inside_ratio: float


def audit_zone_constraint(result, zone_gdf, tolerance_m2: float = 0.01) -> ZoneConstraintReport:
    """Verify the final result is materially contained in the selected zoning geometry.

    The main pipeline already clips the candidate by the target zone. This audit makes
    that constraint explicit and machine-checkable in outputs.
    """
    if result is None or result.is_empty:
        return ZoneConstraintReport(False, 0.0, 0.0, 0.0, 0.0)
    zone_union = make_valid(unary_union(list(zone_gdf.geometry)))
    geom = make_valid(result)
    result_area = float(geom.area)
    inside_area = float(geom.intersection(zone_union).area)
    outside_area = max(0.0, result_area - inside_area)
    ratio = inside_area / result_area if result_area > 0 else 0.0
    return ZoneConstraintReport(
        outside_area <= tolerance_m2,
        result_area,
        inside_area,
        outside_area,
        ratio,
    )

@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    area_m2: float
    geometry_type: str
    reasons: tuple[str, ...]


def _polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygon_parts(g))
        return out
    return []


def intersect_target_zone(candidate, zone_gdf, anchor, min_area_m2: float = 5.0):
    zone_union = unary_union(list(zone_gdf.geometry))
    raw = make_valid(candidate.intersection(zone_union))
    parts = [p for p in _polygon_parts(raw) if p.area >= min_area_m2]
    if not parts:
        raise ValueError("Candidate does not intersect the target zoning geometry above minimum area")
    anchor_parts = [p for p in parts if p.contains(anchor) or p.touches(anchor)]
    chosen = max(anchor_parts or parts, key=lambda g: g.area)
    return make_valid(chosen)


def validate_result(result, min_area_m2: float = 5.0) -> ValidationReport:
    reasons: list[str] = []
    if result is None or result.is_empty:
        reasons.append("empty geometry")
        return ValidationReport(False, 0.0, "None", tuple(reasons))
    geom = make_valid(result)
    area = float(geom.area)
    if area < min_area_m2:
        reasons.append(f"area below minimum threshold ({area:.2f} < {min_area_m2:.2f} m²)")
    if not geom.is_valid:
        reasons.append("geometry is invalid")
    if geom.geom_type not in {"Polygon", "MultiPolygon"}:
        reasons.append(f"unexpected geometry type: {geom.geom_type}")
    return ValidationReport(not reasons, area, geom.geom_type, tuple(reasons))
