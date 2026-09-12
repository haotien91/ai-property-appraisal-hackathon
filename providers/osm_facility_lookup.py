# -*- coding: utf-8 -*-
"""
OsmFacilityLookup — thin wrapper around two free, no-API-key-required public
geo data services:
- Overpass API (OpenStreetMap) for "nearest facility of category X" queries
  (bus stops, schools, cemeteries, substations, ...).
- Nominatim (OpenStreetMap) for geocoding a place description into a
  coordinate, when the caller does not already have one.

Why this exists: Phase 1 REQ-005/006/007 confirm the competition organizers
will NOT provide GIS layer files, and the segment given at competition time
is guaranteed to be different from the 金山區 Golden Case. `providers/*.py`
were, until now, all Mock implementations returning hardcoded Golden Case
numbers regardless of which segment/case they were asked about -- this
module is the real data source that lets a handful of those providers
answer for an arbitrary, previously-unseen New Taipei City location.

Design constraints (same philosophy as GeoDistanceEngine and every
DataProvider in this codebase -- see providers/base.py and
docs/backlog.md's "不得自行捏造資料" principle):
- NEVER raises for "couldn't find data" or "network unreachable". Every
  function here returns a result object with found=False / None and a
  human-readable reason instead, so the calling provider can surface
  MANUAL_REVIEW_REQUIRED exactly like it already does for missing Mock
  data. A network failure at competition time (e.g. the real AWS Lambda's
  outbound network policy blocks this) must degrade gracefully, never
  crash the whole pipeline.
- Standard library only (urllib) -- deliberately not adding `requests` as a
  new project dependency for two HTTP calls.
- Reachability of nominatim.openstreetmap.org / overpass-api.de was
  verified (HTTP 200) from the development sandbox this module was written
  in. Reachability from the REAL competition AWS Lambda environment is an
  open question (same class of unknown as Phase 1 REQ-015's AWS network
  policy) -- this should be the first thing checked during AWS deployment
  rehearsal, see docs/phase8a/competition_checklist.md.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import Coordinate  # noqa: E402
from engine.geo_distance_engine import GeoDistanceEngine, DistanceMethod  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires a descriptive, identifying User-Agent
# (an unauthenticated / generic UA can be blocked outright). HTTP header
# values must be Latin-1-encodable, so this stays ASCII-only even though
# the project itself is otherwise all Traditional Chinese.
USER_AGENT = (
    "ai-valuation-review-competition-tool/1.0 "
    "(New Taipei City AI Hackathon 2026, Land Administration track; "
    "non-commercial competition use, offline dev candidate)"
)

_geo_engine = GeoDistanceEngine()


@dataclass
class OsmFacility:
    name: str
    latitude: float
    longitude: float
    distance_m: Optional[float]  # None if the requested distance_method's calculation itself failed
    distance_method: DistanceMethod = DistanceMethod.STRAIGHT_LINE
    distance_note: Optional[str] = None  # populated only when distance_m is None
    osm_tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OsmLookupResult:
    found: bool
    facility: Optional[OsmFacility]
    match_count: int
    reason: Optional[str] = None  # populated when found=False


def _http_get(url: str, timeout_s: float, retries: int = 1, backoff_s: float = 3.0) -> Optional[bytes]:
    """The public Overpass/Nominatim instances rate-limit bursts of
    requests (observed directly: a request immediately following a heavier
    query failed, then succeeded after a few seconds' wait) -- a single
    short-backoff retry meaningfully reduces spurious MANUAL_REVIEW_REQUIRED
    results from transient throttling, without turning a real outage into a
    long hang (bounded by `retries`)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status != 200:
                    return None
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt >= retries:
                return None
            attempt += 1
            time.sleep(backoff_s)


def _build_overpass_query(
    selectors: Sequence[str], center: Coordinate, radius_m: int, timeout_s: float,
) -> str:
    clauses = []
    for sel in selectors:
        key, _, val = sel.partition("=")
        tag_filter = f'["{key}"="{val}"]' if val else f'["{key}"]'
        clauses.append(f'node{tag_filter}(around:{radius_m},{center.latitude},{center.longitude});')
        clauses.append(f'way{tag_filter}(around:{radius_m},{center.latitude},{center.longitude});')
    return f'[out:json][timeout:{int(timeout_s)}];({"".join(clauses)});out center tags;'


def find_nearest_facility(
    center: Optional[Coordinate],
    overpass_selectors: Sequence[str],
    radius_m: int,
    distance_method: DistanceMethod = DistanceMethod.STRAIGHT_LINE,
    timeout_s: float = 8.0,
) -> OsmLookupResult:
    """overpass_selectors: OSM tag filters, e.g. ["amenity=school"] or
    ["shop=marketplace", "amenity=marketplace"] (multiple selectors are
    OR'd -- the nearest match across all of them is returned).

    distance_method picks which of GeoDistanceEngine's two confirmed
    algorithms produces the REPORTED distance (Phase 1 REQ-010: convenience-
    type 一般設施 use walking ROUTE distance; nuisance-type 特殊設施 use
    STRAIGHT_LINE). Ranking candidates to find the nearest one always uses
    straight-line distance regardless of distance_method -- that only costs
    one Overpass call already made, whereas ranking by route distance would
    require one extra routing call PER candidate. Once the nearest candidate
    is identified, if distance_method=ROUTE was requested, exactly one
    additional route_distance() call refines that single candidate's
    reported distance; if that call fails, distance_m becomes None (with
    distance_note explaining why) rather than silently reporting the
    straight-line number under the ROUTE label -- see module docstring."""
    if center is None or center.latitude is None or center.longitude is None:
        return OsmLookupResult(False, None, 0, "無中心點座標，無法查詢（不捏造座標）")

    query = _build_overpass_query(overpass_selectors, center, radius_m, timeout_s)
    url = OVERPASS_URL + "?" + urllib.parse.urlencode({"data": query})

    raw = _http_get(url, timeout_s)
    if raw is None:
        return OsmLookupResult(
            False, None, 0,
            "Overpass API 無法連線或逾時（可能是本次執行環境之網路限制，需人工查證）",
        )

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return OsmLookupResult(False, None, 0, "Overpass API 回傳非預期格式")

    elements = payload.get("elements", [])
    if not elements:
        return OsmLookupResult(
            False, None, 0,
            f"半徑{radius_m}M內查無符合 {list(overpass_selectors)} 之OSM資料"
            "（可能真的沒有，也可能是OSM資料本身缺漏，非官方登記資料來源，建議人工核實）",
        )

    best: Optional[OsmFacility] = None
    match_count = 0
    for el in elements:
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            center_pt = el.get("center") or {}
            lat, lon = center_pt.get("lat"), center_pt.get("lon")
        if lat is None or lon is None:
            continue
        match_count += 1
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:zh") or tags.get("name:zh-Hant") or "(未命名)"
        dist_result = _geo_engine.straight_line_distance(
            origin=center, origin_description="查詢中心點",
            destination=Coordinate(latitude=lat, longitude=lon), destination_description=name,
        )
        dist_m = dist_result.distance_m
        if best is None or dist_m < best.distance_m:
            best = OsmFacility(
                name=name, latitude=lat, longitude=lon, distance_m=dist_m,
                distance_method=DistanceMethod.STRAIGHT_LINE, osm_tags=tags,
            )

    if best is None:
        return OsmLookupResult(False, None, 0, "查得元素但皆缺少可用座標，無法計算距離")

    if distance_method == DistanceMethod.ROUTE:
        route_result = _geo_engine.route_distance(
            origin=center, origin_description="查詢中心點",
            destination=Coordinate(latitude=best.latitude, longitude=best.longitude),
            destination_description=best.name, timeout_s=timeout_s,
        )
        best.distance_method = DistanceMethod.ROUTE
        if route_result.status == "COMPLETED":
            best.distance_m = route_result.distance_m
        else:
            best.distance_m = None
            best.distance_note = route_result.notes

    return OsmLookupResult(True, best, match_count)


def geocode(place_description: str, timeout_s: float = 8.0) -> Optional[Coordinate]:
    """Best-effort geocoding via Nominatim. Returns None (never a guessed
    coordinate) if the lookup fails, times out, or returns no result."""
    if not place_description or not place_description.strip():
        return None
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode({
        "q": place_description, "format": "json", "limit": 1,
    })
    raw = _http_get(url, timeout_s)
    if raw is None:
        return None
    try:
        results = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not results:
        return None
    try:
        return Coordinate(latitude=float(results[0]["lat"]), longitude=float(results[0]["lon"]))
    except (KeyError, ValueError, TypeError):
        return None
