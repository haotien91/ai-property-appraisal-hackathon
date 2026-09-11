# -*- coding: utf-8 -*-
"""
GeoDistanceEngine — deterministic distance calculation between two
coordinates.

Per Phase 2 docs/phase2/distance_rules.md (grounded in 土地徵收補償市價查估
作業手冊.pdf p.22-32) and Phase 3 docs/phase3/source_anomalies.md, TWO
distinct pieces of distance methodology are officially confirmed:

1. Measurement ORIGIN: 表1 regional facilities are measured from "本區段
   中心點" (segment center point); 表4 individual factors are measured from
   "宗地中心點" (the specific parcel's own center point). CONFIRMED, high
   confidence (作業手冊 p.22, p.28-32).
2. Measurement ALGORITHM: for 殯葬設施/嫌惡設施-type nuisance facilities,
   straight-line distance is confirmed (逐字稿 x2 + 作業手冊 p.24's parallel
   "焚化爐" example). For "通達"-type facilities (schools, markets), the
   manual recommends route/path distance -- REQ-010's second confirmation
   uses the specific words "最近的步行距離" (nearest WALKING distance), so
   route_distance() computes a pedestrian route, not a driving route.

route_distance() calls the public OSRM demo routing server (foot profile;
verified reachable, HTTP 200, from this development sandbox -- an earlier
version of this docstring incorrectly assumed no network access was
possible here, before that assumption was actually tested). Like every
other network call in this codebase (providers/osm_facility_lookup.py),
it degrades to MANUAL_REVIEW_REQUIRED rather than silently falling back to
straight-line distance on failure/timeout/no coordinates -- straight-line
is a DIFFERENT, unconfirmed methodology for this facility category, and
substituting it silently would misrepresent which algorithm actually
produced the number.
"""
from __future__ import annotations

import json
import math
import sys
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from enum import Enum
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import Coordinate  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402


EARTH_RADIUS_M = 6371000.0  # mean Earth radius in meters (WGS84 sphere approximation)

OSRM_FOOT_ROUTE_URL = "https://router.project-osrm.org/route/v1/foot"
OSRM_USER_AGENT = (
    "ai-valuation-review-competition-tool/1.0 "
    "(New Taipei City AI Hackathon 2026, Land Administration track; "
    "non-commercial competition use, offline dev candidate)"
)


class DistanceMethod(str, Enum):
    STRAIGHT_LINE = "straight_line"    # Haversine, CONFIRMED for nuisance facilities
    ROUTE = "route"                    # road-network path distance, NOT implementable here
    UNKNOWN = "unknown"


class DistanceOrigin(str, Enum):
    SEGMENT_CENTER = "本區段中心點"      # 表1 regional-factor origin (作業手冊 p.28-32)
    PARCEL_CENTER = "宗地中心點"         # 表4 individual-factor origin (作業手冊 p.22)


class DistanceResult(BaseModel):
    """Full traceability record for one distance calculation, per Phase 5
    TRACEABILITY spec: origin, destination, method, distance, unit, source."""
    model_config = ConfigDict(extra="forbid")

    origin: Coordinate
    origin_description: str
    destination: Coordinate
    destination_description: str
    method: DistanceMethod
    distance_m: Optional[float] = None
    unit: str = "M"
    source: str
    status: str  # "COMPLETED" | "MANUAL_REVIEW_REQUIRED"
    notes: Optional[str] = None
    computed_at: datetime


class GeoDistanceEngine:
    """Stateless; every method is a pure function of its inputs."""

    def straight_line_distance(
        self, origin: Coordinate, origin_description: str,
        destination: Coordinate, destination_description: str,
        source: str = "GeoDistanceEngine（Haversine公式）",
    ) -> DistanceResult:
        """Haversine great-circle distance. Deterministic, no external
        service required. This is the CONFIRMED method for nuisance-type
        facilities (作業手冊 p.24；逐字稿 [00:22:54-00:23:03][00:44:08-00:44:13])."""
        if origin.latitude is None or origin.longitude is None or \
           destination.latitude is None or destination.longitude is None:
            return DistanceResult(
                origin=origin, origin_description=origin_description,
                destination=destination, destination_description=destination_description,
                method=DistanceMethod.STRAIGHT_LINE, distance_m=None,
                source=source, status="MANUAL_REVIEW_REQUIRED",
                notes="origin或destination座標缺失，無法計算直線距離，需人工提供座標或改用官方文件已記載之距離數值",
                computed_at=datetime.now(),
            )

        lat1, lon1 = math.radians(origin.latitude), math.radians(origin.longitude)
        lat2, lon2 = math.radians(destination.latitude), math.radians(destination.longitude)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(min(1.0, math.sqrt(a)))
        distance_m = EARTH_RADIUS_M * c

        return DistanceResult(
            origin=origin, origin_description=origin_description,
            destination=destination, destination_description=destination_description,
            method=DistanceMethod.STRAIGHT_LINE, distance_m=round(distance_m, 2),
            source=source, status="COMPLETED",
            notes=None, computed_at=datetime.now(),
        )

    def route_distance(
        self, origin: Coordinate, origin_description: str,
        destination: Coordinate, destination_description: str,
        timeout_s: float = 8.0,
    ) -> DistanceResult:
        """Pedestrian route distance via OSRM's public foot-profile routing
        API (REQ-010's confirmed method for 一般設施 -- 逐字稿 second
        confirmation: "最近的步行距離"). Never raises: a network failure,
        timeout, missing coordinates, or an OSRM 'NoRoute' response all
        degrade to MANUAL_REVIEW_REQUIRED with an explanatory note, exactly
        like every other network-backed lookup in this codebase -- never
        silently substitutes straight-line distance for a failed route
        lookup, since that would misrepresent which algorithm produced the
        number."""
        if origin.latitude is None or origin.longitude is None or \
           destination.latitude is None or destination.longitude is None:
            return DistanceResult(
                origin=origin, origin_description=origin_description,
                destination=destination, destination_description=destination_description,
                method=DistanceMethod.ROUTE, distance_m=None,
                source="GeoDistanceEngine（OSRM步行路線）", status="MANUAL_REVIEW_REQUIRED",
                notes="origin或destination座標缺失，無法計算路線距離，需人工提供座標",
                computed_at=datetime.now(),
            )

        coords = f"{origin.longitude},{origin.latitude};{destination.longitude},{destination.latitude}"
        url = f"{OSRM_FOOT_ROUTE_URL}/{coords}?" + urllib.parse.urlencode({"overview": "false"})
        req = urllib.request.Request(url, headers={"User-Agent": OSRM_USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status != 200:
                    raise urllib.error.URLError(f"HTTP {resp.status}")
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            return DistanceResult(
                origin=origin, origin_description=origin_description,
                destination=destination, destination_description=destination_description,
                method=DistanceMethod.ROUTE, distance_m=None,
                source="GeoDistanceEngine（OSRM步行路線，連線失敗）", status="MANUAL_REVIEW_REQUIRED",
                notes=f"OSRM路線查詢無法連線或逾時（{e}），可能是本次執行環境之網路限制，需人工查證",
                computed_at=datetime.now(),
            )

        if payload.get("code") != "Ok" or not payload.get("routes"):
            return DistanceResult(
                origin=origin, origin_description=origin_description,
                destination=destination, destination_description=destination_description,
                method=DistanceMethod.ROUTE, distance_m=None,
                source="GeoDistanceEngine（OSRM步行路線）", status="MANUAL_REVIEW_REQUIRED",
                notes=f"OSRM查無可行步行路線（code={payload.get('code')}），需人工查證",
                computed_at=datetime.now(),
            )

        distance_m = payload["routes"][0]["distance"]
        return DistanceResult(
            origin=origin, origin_description=origin_description,
            destination=destination, destination_description=destination_description,
            method=DistanceMethod.ROUTE, distance_m=round(distance_m, 2),
            source="GeoDistanceEngine（OSRM foot profile，即時路線查詢）", status="COMPLETED",
            notes="即時查詢所得步行路網距離，非Mock資料；OSRM為社群維運之開源路由服務，非官方權威來源",
            computed_at=datetime.now(),
        )

    def compute(
        self, origin: Coordinate, origin_description: str,
        destination: Coordinate, destination_description: str,
        method: DistanceMethod,
    ) -> DistanceResult:
        """Single entry point that routes to the correct calculation (or
        MANUAL_REVIEW_REQUIRED) based on the requested method. Never
        silently substitutes one method for another."""
        if method == DistanceMethod.STRAIGHT_LINE:
            return self.straight_line_distance(origin, origin_description, destination, destination_description)
        if method == DistanceMethod.ROUTE:
            return self.route_distance(origin, origin_description, destination, destination_description)
        return DistanceResult(
            origin=origin, origin_description=origin_description,
            destination=destination, destination_description=destination_description,
            method=DistanceMethod.UNKNOWN, distance_m=None,
            source="GeoDistanceEngine", status="MANUAL_REVIEW_REQUIRED",
            notes=f"未知或未確認之量測方法 {method!r}，依規定不猜測演算法，標記待人工確認",
            computed_at=datetime.now(),
        )
