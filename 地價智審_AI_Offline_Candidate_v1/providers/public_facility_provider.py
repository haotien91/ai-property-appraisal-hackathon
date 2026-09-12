# -*- coding: utf-8 -*-
"""PublicFacilityProvider — 學校/市場/公園/觀光遊憩設施/停車場地等."""
from __future__ import annotations
from datetime import datetime
from typing import List

from base import DataProvider, ProviderContext
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


class MockPublicFacilityProvider(DataProvider):
    provider_name = "MockPublicFacilityProvider"

    _BLANK_FACILITY_GROUPS = [
        "school_elementary", "school_junior_high", "school_senior_high", "school_college",
    ]

    _KNOWN_VALUES = {
        "market_name": ("金山區第一零售傳統市場", None),
        "market_within_segment": (True, None),
        "park_name": ("中山溫泉里鄰公園", None),
        "park_within_segment": (True, None),
        "tourism_facility_name": ("金包里老街", None),
        "tourism_facility_within_segment": (True, None),
        "parking_lot_name": ("金包里老街停車場", None),
        "parking_lot_within_segment": (False, None),
        "parking_lot_distance_m": (120, "M"),
        "wastewater_facility_name": ("無", None),
    }

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        points = []
        for field, (value, unit) in self._KNOWN_VALUES.items():
            points.append(NormalizedDataPoint(
                field=field, value=value, unit=unit, source=SRC, source_type="Mock",
                coordinate=None, confidence="高", retrieved_at=now,
                notes="Mock資料，取自Golden Case",
            ))
        for group in self._BLANK_FACILITY_GROUPS:
            for suffix in ("_name", "_within_segment", "_distance_m"):
                points.append(self._unknown(
                    group + suffix, "查估書表範本.pdf表1 Golden Case此欄位留空（無填寫學校名稱）"
                ))
        for field in ("service_facility_proximity", "power_resource", "industrial_water_facility",
                      "wastewater_facility_within_segment", "wastewater_facility_distance_m",
                      "market_distance_m", "park_distance_m", "tourism_facility_distance_m"):
            points.append(self._unknown(field, "查估書表範本.pdf表1 Golden Case此欄位留空或設施在區段內無須填距離"))
        return points


from real_facility_provider_base import RealFacilityProviderBase, FacilitySpec  # noqa: E402
from engine.geo_distance_engine import DistanceMethod  # noqa: E402


class RealPublicFacilityProvider(RealFacilityProviderBase):
    """Real (OSM Overpass) implementation for market/park/tourism/parking/
    wastewater plant. School-tier fields (elementary/junior/senior/college)
    are deliberately NOT queried: OSM's isced:level tagging is too sparse
    for Taiwan to reliably distinguish school tiers, and the Mock already
    leaves these blank for the same reason -- this is not a regression, it
    stays exactly as honest as before. power_resource/industrial_water_
    facility/service_facility_proximity have no reliable OSM tag mapping
    either and stay MANUAL_REVIEW_REQUIRED."""

    provider_name = "RealPublicFacilityProvider"

    # Radii match data/rules/regional_rules.json's actual max-relevant
    # distance per factor, not round numbers picked by feel: 接近市場=1800M,
    # 接近公園=1800M, 接近觀光遊憩設施=1500M, 停車場地=700M. wastewater_facility
    # has no distinct regional factor of its own in the source table; 3000M
    # matches the closest analogous category (廢棄物處理設施).
    #
    # Distance method (REQ-010): market/park/tourism/parking are all
    # value_type=distance_positive (convenience-type) in the source rule
    # data -> walking ROUTE distance. wastewater_facility is the one
    # nuisance-type (distance_negative) factor in this file -- it keeps the
    # default STRAIGHT_LINE, same reasoning as special_facility_provider.py.
    FACILITY_SPECS = [
        FacilitySpec("market", ["shop=marketplace", "amenity=marketplace"], radius_m=1800,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("park", ["leisure=park"], radius_m=1800,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("tourism_facility", ["tourism=attraction"], radius_m=1500,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("parking_lot", ["amenity=parking"], radius_m=700,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("wastewater_facility", ["man_made=wastewater_plant"], radius_m=3000),
    ]

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        points = super().fetch(ctx)
        for group in ("school_elementary", "school_junior_high", "school_senior_high", "school_college"):
            for suffix in ("_name", "_within_segment", "_distance_m"):
                points.append(self._unknown(
                    group + suffix,
                    "OSM學校標籤（isced:level）在台灣涵蓋率過低，無法可靠區分國小/國中/高中/大學，不猜測",
                ))
        for field in ("service_facility_proximity", "power_resource", "industrial_water_facility"):
            points.append(self._unknown(field, "無可靠對應之OSM設施類別，需人工確認"))
        return points
