# -*- coding: utf-8 -*-
"""CommercialActivityProvider — 百貨公司/金融機構/娛樂設施/大型展示中心/顧客通行量/店舖毗連狀態."""
from __future__ import annotations
from datetime import datetime
from typing import List

from base import DataProvider, ProviderContext
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


class MockCommercialActivityProvider(DataProvider):
    provider_name = "MockCommercialActivityProvider"

    _KNOWN_VALUES = {
        "department_store_name": ("無", None),
        "financial_institution_name": ("新北市金山地區農會", None),
        "financial_institution_qty": (1, None),
        "financial_institution_within_segment": (False, None),
        "financial_institution_distance_m": (210, "M"),
        "entertainment_facility_name": ("無", None),
        "exhibition_hotel_name": ("新北北海溫泉洲際酒店", None),
        "exhibition_hotel_qty": (1, None),
        "exhibition_hotel_within_segment": (False, None),
        "exhibition_hotel_distance_m": (850, "M"),
        "customer_traffic_volume": ("顧客通行量多", None),
        "shop_contiguity": (90, "%"),
    }

    _UNKNOWN_FIELDS = [
        "department_store_qty", "department_store_within_segment", "department_store_distance_m",
        "entertainment_facility_qty", "entertainment_facility_within_segment", "entertainment_facility_distance_m",
    ]

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        points = [
            NormalizedDataPoint(
                field=field, value=value, unit=unit, source=SRC, source_type="Mock",
                coordinate=None, confidence="高", retrieved_at=now,
                notes="Mock資料，取自Golden Case",
            ) for field, (value, unit) in self._KNOWN_VALUES.items()
        ]
        for field in self._UNKNOWN_FIELDS:
            points.append(self._unknown(field, "查估書表範本.pdf表1 Golden Case該設施記載為「無」，數量/距離欄位空白"))
        return points


from real_facility_provider_base import RealFacilityProviderBase, FacilitySpec  # noqa: E402
from engine.geo_distance_engine import DistanceMethod  # noqa: E402


class RealCommercialActivityProvider(RealFacilityProviderBase):
    """Real (OSM Overpass) implementation. `_qty` fields (see
    schemas/field_dictionary.json) use FacilitySpec.include_qty=True, which
    reports the raw count of matching OSM elements within radius -- an
    approximate, non-official count, clearly labeled as such in each
    NormalizedDataPoint's notes. customer_traffic_volume and
    shop_contiguity are subjective/assessed (顧客通行量多寡, 店舖毗連百分比)
    with no OSM equivalent and stay MANUAL_REVIEW_REQUIRED, same as Mock."""

    provider_name = "RealCommercialActivityProvider"

    # 1500M matches data/rules/regional_rules.json's max-relevant distance
    # for all four of 百貨公司/金融機構/娛樂設施/大型展示中心或觀光飯店 之
    # 有無數量接近程度 -- all four factors share the same 1500M ceiling.
    # All four are value_type=distance_positive (convenience-type) in the
    # source rule data -> REQ-010 walking ROUTE distance.
    FACILITY_SPECS = [
        FacilitySpec("department_store", ["shop=department_store"], radius_m=1500, include_qty=True,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("financial_institution", ["amenity=bank"], radius_m=1500, include_qty=True,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("entertainment_facility",
                      ["leisure=amusement_arcade", "amenity=cinema"], radius_m=1500, include_qty=True,
                      distance_method=DistanceMethod.ROUTE),
        FacilitySpec("exhibition_hotel",
                      ["tourism=hotel", "amenity=conference_centre"], radius_m=1500, include_qty=True,
                      distance_method=DistanceMethod.ROUTE),
    ]

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        points = super().fetch(ctx)
        for field in ("customer_traffic_volume", "shop_contiguity"):
            points.append(self._unknown(field, "為主觀評估性質欄位，非OSM可查詢之設施類別，需人工判斷"))
        return points
