# -*- coding: utf-8 -*-
"""
TransportationProvider — 大型車站／站牌／交流道／接近聚落程度等.

Demonstrates two provenance patterns side by side:
1. Distance already stated directly in the official source document
   (source_type='官方提供資料', confidence='高') -- most fields here.
2. Distance derivable via GeoDistanceEngine from coordinates, when
   coordinates ARE available (source_type='Mock+GeoDistanceEngine') --
   shown for major_station_distance_m as a worked example, using the real
   Jinshan District anchor coordinate established in Phase 5 testing.
"""
from __future__ import annotations
from datetime import datetime
from typing import List

from base import DataProvider, ProviderContext
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint, Coordinate  # noqa: E402
from engine.geo_distance_engine import GeoDistanceEngine, DistanceMethod  # noqa: E402
from real_facility_provider_base import RealFacilityProviderBase, FacilitySpec  # noqa: E402
from osm_facility_lookup import find_nearest_facility  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"

# Real, Wikipedia-sourced anchor (see docs/phase5/geo_engine_spec.md); used
# only to DEMONSTRATE the GeoDistanceEngine integration pattern, since the
# Golden Case source PDF already states this distance directly (300M) and
# does not provide a destination coordinate for 國光客運金山站 itself.
JINSHAN_DISTRICT_CENTROID = Coordinate(latitude=25.23611, longitude=121.61750)


class MockTransportationProvider(DataProvider):
    provider_name = "MockTransportationProvider"

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        points = [
            NormalizedDataPoint(field="major_station_name", value="國光客運金山站", unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now, notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="major_station_within_segment", value=False, unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now, notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="major_station_distance_m", value=300, unit="M",
                                 source=SRC, source_type="官方提供資料", coordinate=None,
                                 confidence="高", retrieved_at=now,
                                 notes=(
                                     "此距離值直接取自官方查估書表範本，非本Provider即時計算。"
                                     "若未來需由座標即時計算，GeoDistanceEngine可支援（見同檔案內"
                                     "示範用法），惟需先取得該站牌之實際座標，Sources未提供。"
                                 )),
            NormalizedDataPoint(field="bus_stop_name", value="金山區公所站", unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now, notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="bus_stop_within_segment", value=True, unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now, notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="bus_stop_density", value="密集", unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now, notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="interchange_name", value="無交流道", unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now, notes="Mock資料，取自Golden Case"),
            self._unknown("interchange_distance_m",
                           "Golden Case該區段無交流道，距離欄位空白；見docs/phase3/source_anomalies.md ANOMALY-04"),
            self._unknown("village_proximity", "查估書表範本.pdf表1 Golden Case此欄位留空"),
            self._unknown("distribution_center_proximity", "查估書表範本.pdf表1 Golden Case此欄位留空"),
            self._unknown("consumer_market_proximity", "查估書表範本.pdf表1 Golden Case此欄位留空"),
        ]
        return points

    def demo_geo_engine_usage(self, destination: Coordinate, destination_description: str):
        """Worked example showing how this provider WOULD use
        GeoDistanceEngine if a real destination coordinate were available
        (e.g. from a future government Open Data facility registry).
        Not called by fetch() -- Golden Case already has an official
        distance value, so recomputation is unnecessary and would risk
        disagreeing with the source document."""
        engine = GeoDistanceEngine()
        return engine.straight_line_distance(
            JINSHAN_DISTRICT_CENTROID, "新北市金山區中心點（本區段中心點近似值）",
            destination, destination_description,
        )


class RealTransportationProvider(RealFacilityProviderBase):
    """Real (OSM Overpass) implementation. `major_station` and `interchange`
    both follow the standard name/within_segment/distance_m trio -- see
    schemas/field_dictionary.json, which defines interchange_within_segment
    and interchange_distance_m even though the Golden Case Mock leaves them
    blank (that segment simply has no interchange, not a schema omission).
    `bus_stop_density` and the *_proximity fields are NOT in FACILITY_SPECS:
    they are subjective classifications ("密集/普通/稀少", "接近聚落程度")
    with no Source-defined rule for deriving them from a raw OSM count, so
    this provider does not guess them -- see fetch() override below."""

    provider_name = "RealTransportationProvider"

    # Radii match data/rules/regional_rules.json's actual max-relevant
    # distance (upper_bound of that factor's worst/farthest grade band) --
    # not round numbers picked by feel. major_station=3000M, interchange
    # =4500M, bus_stop (below, in fetch())=700M. Both factors are
    # value_type=distance_positive (closer=better, convenience-type) in the
    # source rule data, so REQ-010 calls for walking ROUTE distance, not
    # straight-line (that's reserved for distance_negative nuisance
    # factors -- see special_facility_provider.py).
    FACILITY_SPECS = [
        FacilitySpec("major_station", ["amenity=bus_station"], radius_m=3000,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("interchange", ["highway=motorway_junction"], radius_m=4500,
                      distance_method=DistanceMethod.ROUTE),
    ]

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        points = super().fetch(ctx)

        bus_stop_result_points = []
        result = find_nearest_facility(ctx.center_coordinate, ["highway=bus_stop"], radius_m=700)
        now = datetime.now()
        bus_stop_result_points.append(self._unknown(
            "bus_stop_within_segment",
            "「是否在本區段內」需要地價區段GIS邊界圖資，官方不提供，本系統不猜測邊界",
        ))
        if result.found:
            bus_stop_result_points.append(NormalizedDataPoint(
                field="bus_stop_name", value=result.facility.name, unit=None,
                source="OpenStreetMap Overpass API（即時查詢）", source_type="API",
                coordinate=Coordinate(latitude=result.facility.latitude, longitude=result.facility.longitude),
                confidence="中", retrieved_at=now,
                notes="OSM為社群協作資料，非官方登記資料，建議人工快速核對名稱",
            ))
        else:
            bus_stop_result_points.append(self._unknown("bus_stop_name", result.reason))
        bus_stop_result_points.append(self._unknown(
            "bus_stop_density",
            "「密集/普通/稀少」為主觀分級判斷，Sources未提供由設施數量換算此分級之公式，"
            "本系統不自行訂定門檻猜測，需人工依現況判斷",
        ))
        points.extend(bus_stop_result_points)

        for field in ("village_proximity", "distribution_center_proximity", "consumer_market_proximity"):
            points.append(self._unknown(field, "「接近程度」為綜合性主觀描述，非單一可查詢之OSM設施類別，需人工判斷"))

        return points
