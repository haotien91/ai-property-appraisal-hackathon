from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import mapping

from .crs import to_4326


def _feature(geometry, properties: dict) -> dict:
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(geometry) if geometry is not None else None,
    }


def write_result_geojson(path: str | Path, geometry_3826, metadata: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "type": "FeatureCollection",
        "name": "ntpc_boundary_result",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [_feature(to_4326(geometry_3826), dict(metadata))],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_debug_geojson(path: str | Path, features_3826: list[tuple[str, object, dict]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    features = []
    for layer, geom, props in features_3826:
        p = {"layer": layer, **dict(props)}
        features.append(_feature(to_4326(geom), p))
    data = {
        "type": "FeatureCollection",
        "name": "ntpc_boundary_debug",
        "features": features,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
