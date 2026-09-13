from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, unary_union

from .normalize import normalize_name

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_TO_3826 = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)


class RoadNotFoundError(RuntimeError):
    pass


class RoadAmbiguousError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoadFeature:
    osm_id: int
    name: str
    geometry: LineString | MultiLineString


def _escape_overpass_regex(text: str) -> str:
    return re.escape(text).replace('"', '\\"')


def build_overpass_query(lat: float, lon: float, radius_m: int, names: list[str]) -> str:
    regex = "|".join(_escape_overpass_regex(normalize_name(n)) for n in names)
    return (
        "[out:json][timeout:30];\n"
        f'way(around:{int(radius_m)},{lat},{lon})["highway"]["name"~"^({regex})$"];\n'
        "out geom;"
    )


def _project_lonlat(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [_TO_3826.transform(lon, lat) for lon, lat in coords]


def parse_overpass_ways(payload: dict) -> list[RoadFeature]:
    features: list[RoadFeature] = []
    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        name = tags.get("name")
        geometry = element.get("geometry") or []
        if not name or len(geometry) < 2:
            continue
        lonlat = [(float(p["lon"]), float(p["lat"])) for p in geometry]
        line = LineString(_project_lonlat(lonlat))
        features.append(RoadFeature(int(element.get("id", 0)), str(name), line))
    return features


def resolve_road(features: list[RoadFeature], requested: str) -> RoadFeature:
    req = normalize_name(requested)
    matched = [f for f in features if normalize_name(f.name) == req]
    if not matched:
        available = sorted({f.name for f in features})
        raise RoadNotFoundError(f"Road '{requested}' not found; available={available}")
    if len(matched) == 1:
        return matched[0]

    merged = linemerge(unary_union([f.geometry for f in matched]))
    if isinstance(merged, MultiLineString) and len(merged.geoms) > 1:
        # Small endpoint gaps are common in OSM. If components are clearly
        # separated (>10 m) we refuse to guess which road segment is intended.
        parts = list(merged.geoms)
        min_gap = min(parts[i].distance(parts[j]) for i in range(len(parts)) for j in range(i + 1, len(parts)))
        if min_gap > 10.0:
            raise RoadAmbiguousError(
                f"Road '{requested}' has {len(parts)} disconnected groups (minimum gap {min_gap:.1f} m)"
            )
    return RoadFeature(matched[0].osm_id, matched[0].name, merged)


def fetch_roads(lat: float, lon: float, radius_m: int, names: list[str], session=requests) -> list[RoadFeature]:
    query = build_overpass_query(lat, lon, radius_m, names)
    response = session.post(_OVERPASS_URL, data={"data": query}, timeout=45)
    response.raise_for_status()
    return parse_overpass_ways(response.json())
