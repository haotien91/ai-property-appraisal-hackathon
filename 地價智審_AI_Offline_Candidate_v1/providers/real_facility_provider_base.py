# -*- coding: utf-8 -*-
"""
RealFacilityProviderBase — shared fetch() logic for every provider whose
official fields follow the {prefix}_name / {prefix}_within_segment /
{prefix}_distance_m shape (5 of the 7 providers: transportation,
public_facility, special_facility, environmental, commercial_activity all
share this exact shape -- see their MockXxxProvider._KNOWN_VALUES). A
concrete Real provider only has to declare a list of FacilitySpec entries;
this base class does the OSM query + NormalizedDataPoint construction once.

Two fields this deliberately NEVER fills from OSM, on purpose:
- `{prefix}_within_segment`: this means "is the facility located inside the
  official 地價區段 boundary polygon", which requires the segment's actual
  GIS boundary. Phase 1 REQ-005 confirms organizers will NOT provide GIS
  layer files. Approximating this with a distance threshold would be
  inventing an unofficial rule this codebase has no Source for -- exactly
  the kind of silent guess every other engine here refuses to make (see
  GeoDistanceEngine's refusal to guess a route-distance algorithm). Always
  MANUAL_REVIEW_REQUIRED.
- Any field that is a subjective/assessed judgment rather than a locatable
  point (e.g. 顧客通行量, 店舖毗連狀態, pollution-source assessments): OSM has
  no tag for these and fabricating one would misrepresent an opinion as
  fact. Concrete providers simply do not include these in FACILITY_SPECS,
  and their fetch() override falls back to self._unknown() for them.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint, Coordinate  # noqa: E402
from base import DataProvider, ProviderContext  # noqa: E402
from osm_facility_lookup import find_nearest_facility  # noqa: E402
from engine.geo_distance_engine import DistanceMethod  # noqa: E402

WITHIN_SEGMENT_NOTE = (
    "「是否在本區段內」需要地價區段之GIS邊界圖資，官方明確表示不提供圖層原始檔"
    "（Phase 1 REQ-005），本系統不以距離門檻猜測邊界，一律標記待人工確認"
)

# Polite pacing between successive Overpass queries within one provider's
# fetch() -- the public instance rate-limits request bursts (observed
# directly during development: a query immediately following another
# failed, then succeeded after a few seconds). This does not eliminate
# throttling risk, only reduces it.
_INTER_QUERY_DELAY_S = 1.0


@dataclass
class FacilitySpec:
    field_prefix: str
    overpass_selectors: Sequence[str]
    radius_m: int
    include_qty: bool = False
    not_found_note: Optional[str] = None
    # Phase 1 REQ-010: convenience-type 一般設施 (transit, shopping, parks)
    # use walking ROUTE distance; nuisance-type 特殊設施 (cemeteries,
    # substations, waste facilities) use STRAIGHT_LINE -- see each concrete
    # provider's FACILITY_SPECS for which applies to which factor.
    distance_method: DistanceMethod = DistanceMethod.STRAIGHT_LINE


class RealFacilityProviderBase(DataProvider):
    FACILITY_SPECS: List[FacilitySpec] = []

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        points: List[NormalizedDataPoint] = []
        has_coordinate = ctx.center_coordinate is not None and ctx.center_coordinate.latitude is not None
        for i, spec in enumerate(self.FACILITY_SPECS):
            # No point pacing between calls that never touch the network --
            # find_nearest_facility short-circuits instantly with no
            # coordinate, so sleeping here would only slow that case down
            # for no benefit (and makes offline/no-coordinate testing slow).
            if i > 0 and has_coordinate:
                time.sleep(_INTER_QUERY_DELAY_S)
            points.extend(self._facility_datapoints(ctx, spec))
        return points

    def _facility_datapoints(self, ctx: ProviderContext, spec: FacilitySpec) -> List[NormalizedDataPoint]:
        now = datetime.now()
        prefix = spec.field_prefix
        points = [self._unknown(f"{prefix}_within_segment", WITHIN_SEGMENT_NOTE)]

        result = find_nearest_facility(
            ctx.center_coordinate, spec.overpass_selectors, spec.radius_m,
            distance_method=spec.distance_method,
        )

        if not result.found:
            reason = result.reason or spec.not_found_note or "查詢無結果"
            points.append(self._unknown(f"{prefix}_name", reason))
            points.append(self._unknown(f"{prefix}_distance_m", reason))
            if spec.include_qty:
                points.append(self._unknown(f"{prefix}_qty", reason))
            return points

        facility = result.facility
        points.append(NormalizedDataPoint(
            field=f"{prefix}_name", value=facility.name, unit=None,
            source="OpenStreetMap Overpass API（即時查詢）", source_type="API",
            coordinate=Coordinate(latitude=facility.latitude, longitude=facility.longitude),
            confidence="中", retrieved_at=now,
            notes=(
                f"OSM tags={facility.osm_tags}；OSM為社群協作地圖資料，非官方登記資料，"
                "可能有缺漏或命名與官方登記不同，建議人工快速核對名稱"
            ),
        ))
        # facility.distance_m can be None even though the facility itself
        # was found: e.g. distance_method=ROUTE was requested but the OSRM
        # routing call failed/timed out. Never silently report the
        # straight-line number under a ROUTE label in that case -- see
        # osm_facility_lookup.find_nearest_facility's docstring.
        if facility.distance_m is not None:
            method_label = "路網步行距離（OSRM foot profile，即時查詢）" if \
                facility.distance_method == DistanceMethod.ROUTE else \
                "直線距離（Haversine，以OSM查得最近設施座標計算）"
            points.append(NormalizedDataPoint(
                field=f"{prefix}_distance_m", value=facility.distance_m, unit="M",
                source=f"GeoDistanceEngine（{method_label}）", source_type="API",
                coordinate=None, confidence="中", retrieved_at=now,
                notes=f"即時查詢所得，非Mock資料；REQ-010量測方法＝{method_label}",
            ))
        else:
            points.append(self._unknown(
                f"{prefix}_distance_m",
                facility.distance_note or "距離計算失敗，原因不明",
            ))
        if spec.include_qty:
            points.append(NormalizedDataPoint(
                field=f"{prefix}_qty", value=result.match_count, unit=None,
                source="OpenStreetMap Overpass API（即時查詢，半徑內符合筆數）", source_type="API",
                coordinate=None, confidence="中", retrieved_at=now,
                notes=f"半徑{spec.radius_m}M內查得{result.match_count}筆，非官方普查數字",
            ))
        return points
