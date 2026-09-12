from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from .crs import anchor_3826, roi_square
from .models import BoundaryCase, load_case
from .normalize import first_explicit_road
from .output import write_debug_geojson, write_result_geojson
from .partition import build_barriers, partition_roi, select_candidate
from .render import render_boundary_map, render_preview
from .roads import RoadFeature, RoadNotFoundError, fetch_roads, resolve_road
from .validate import audit_zone_constraint, intersect_target_zone, validate_result
from .zoning import discover_ntpc_zoning_download, load_zoning, select_zone

RoadProvider = Callable[[BoundaryCase, object], dict[str, RoadFeature]]
ZoningProvider = Callable[[BoundaryCase, object, object], object]


def _constraint_descriptions(case: BoundaryCase) -> dict[str, str]:
    c = case.constraints
    return {
        "north_of": c.north_of,
        "west_of": c.west_of,
        "south_of": c.south_of,
        "east_of": c.east_of,
    }


def _first_boundary_names(case: BoundaryCase) -> tuple[dict[str, str], list[str]]:
    names: dict[str, str] = {}
    warnings: list[str] = []
    for relation, raw in _constraint_descriptions(case).items():
        road, w = first_explicit_road(raw)
        names[relation] = road
        warnings.extend(f"{relation}: {message}" for message in w)
    return names, warnings


def _live_road_provider(case: BoundaryCase, anchor) -> dict[str, RoadFeature]:
    names, _ = _first_boundary_names(case)
    last_error: Exception | None = None
    for radius in (450, 750, 1200, 1800):
        try:
            features = fetch_roads(case.lat, case.lon, radius, list(dict.fromkeys(names.values())))
            return {relation: resolve_road(features, name) for relation, name in names.items()}
        except Exception as exc:  # retry bounded radii, then fail explicitly
            last_error = exc
    raise RoadNotFoundError(f"Unable to resolve all required roads after bounded ROI expansion: {last_error}")


def _live_zoning_provider(case: BoundaryCase, roi, anchor):
    source = discover_ntpc_zoning_download()
    all_zones = load_zoning(source, roi)
    return select_zone(all_zones, case.constraints.zone)


def _write_failure(output_dir: Path, stage: str, exc: Exception) -> dict:
    payload = {
        "status": "error",
        "stage": stage,
        "message": str(exc),
        "exception": exc.__class__.__name__,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "failure.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def run_case(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    road_provider: RoadProvider | None = None,
    zoning_provider: ZoningProvider | None = None,
    roi_size_m: float = 600.0,
    barrier_width_m: float = 2.0,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    road_provider = road_provider or _live_road_provider
    zoning_provider = zoning_provider or _live_zoning_provider

    stage = "input"
    try:
        case = load_case(input_path)
        boundary_names, warnings = _first_boundary_names(case)

        stage = "crs_roi"
        anchor = anchor_3826(case.lat, case.lon)
        roi = roi_square(anchor, roi_size_m)

        stage = "roads"
        road_features = road_provider(case, anchor)
        missing_relations = set(boundary_names) - set(road_features)
        if missing_relations:
            raise RoadNotFoundError(f"Road provider omitted relations: {sorted(missing_relations)}")
        directional_roads = {relation: rf.geometry for relation, rf in road_features.items()}
        roads_by_name = {rf.name: rf.geometry for rf in road_features.values()}

        stage = "partition"
        barriers = build_barriers(directional_roads, barrier_width_m)
        cells = partition_roi(roi, barriers)
        candidate = select_candidate(cells, anchor, directional_roads)

        stage = "zoning"
        zoning = zoning_provider(case, roi, anchor)
        if getattr(zoning, "empty", False):
            raise ValueError("Target zoning provider returned no geometry")

        stage = "intersection"
        final_geom = intersect_target_zone(candidate, zoning, anchor, min_area_m2=5.0)

        stage = "validation"
        report = validate_result(final_geom, min_area_m2=5.0)
        if not report.valid:
            raise ValueError("; ".join(report.reasons))
        zone_report = audit_zone_constraint(final_geom, zoning, tolerance_m2=0.01)
        if not zone_report.valid:
            raise ValueError(
                f"Final geometry violates target zoning constraint by "
                f"{zone_report.outside_area_m2:.3f} m²"
            )

        metadata = {
            "section_name": case.section.name,
            "section_code": case.section.code,
            "parcel": case.parcel,
            "anchor_lat": case.lat,
            "anchor_lon": case.lon,
            "target_zone": case.constraints.zone,
            "boundary_roads": boundary_names,
            "matched_roads": {k: v.name for k, v in road_features.items()},
            "warnings": warnings,
            "road_source": "OpenStreetMap/Overpass or injected provider",
            "zoning_source": "New Taipei City Open Data or injected provider",
            "validation": {
                "valid": report.valid,
                "area_m2": report.area_m2,
                "geometry_type": report.geometry_type,
                "reasons": list(report.reasons),
            },
            "zone_constraint": {
                "valid": zone_report.valid,
                "target_zone": case.constraints.zone,
                "result_area_m2": zone_report.result_area_m2,
                "inside_target_zone_area_m2": zone_report.inside_area_m2,
                "outside_target_zone_area_m2": zone_report.outside_area_m2,
                "inside_ratio": zone_report.inside_ratio,
            },
        }

        stage = "output"
        write_result_geojson(output_dir / "result.geojson", final_geom, metadata)

        debug_features: list[tuple[str, object, dict]] = [
            ("roi", roi, {}),
            ("anchor", anchor, {"section_code": case.section.code, "parcel": case.parcel}),
            ("barriers", barriers, {}),
            ("candidate", candidate, {}),
            ("final", final_geom, {"zone": case.constraints.zone}),
        ]
        for relation, rf in road_features.items():
            debug_features.append(("road", rf.geometry, {"relation": relation, "name": rf.name}))
        for i, cell in enumerate(cells):
            debug_features.append(("cell", cell, {"index": i, "area_m2": cell.area}))
        for i, row in zoning.reset_index(drop=True).iterrows():
            debug_features.append(("zoning", row.geometry, {"index": int(i), "zone": case.constraints.zone}))
        write_debug_geojson(output_dir / "debug.geojson", debug_features)
        render_preview(output_dir / "preview.png", roi, roads_by_name, zoning, final_geom, anchor, case)
        render_boundary_map(
            output_dir / "boundary_map.png", roi, roads_by_name, zoning, final_geom, anchor, case
        )
        audit_payload = {
            "target_zone": case.constraints.zone,
            "zone_constraint_passed": zone_report.valid,
            "result_area_m2": zone_report.result_area_m2,
            "inside_target_zone_area_m2": zone_report.inside_area_m2,
            "outside_target_zone_area_m2": zone_report.outside_area_m2,
            "inside_ratio": zone_report.inside_ratio,
            "directional_constraints": _constraint_descriptions(case),
            "matched_roads": {k: v.name for k, v in road_features.items()},
            "warnings": warnings,
        }
        (output_dir / "constraint_audit.json").write_text(
            json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if (output_dir / "failure.json").exists():
            (output_dir / "failure.json").unlink()
        return {
            "status": "ok",
            "output_dir": str(output_dir),
            "area_m2": report.area_m2,
            "warnings": warnings,
        }
    except Exception as exc:
        return _write_failure(output_dir, stage, exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NTPC road/zoning boundary POC")
    parser.add_argument("--input", required=True, help="Structured boundary JSON")
    parser.add_argument("--output", required=True, help="Output directory")
    args = parser.parse_args(argv)
    result = run_case(args.input, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
