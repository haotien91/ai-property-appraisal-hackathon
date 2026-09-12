# -*- coding: utf-8 -*-
"""EnvironmentalProvider — 環境污染／廢棄物處理設施."""
from __future__ import annotations
from datetime import datetime
from typing import List

from base import DataProvider, ProviderContext
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


class MockEnvironmentalProvider(DataProvider):
    provider_name = "MockEnvironmentalProvider"

    _NAME_FIELDS = [
        "sewage_plant_name", "landfill_name", "incinerator_name",
        "water_pollution_name", "noise_pollution_name", "air_pollution_name",
        "waste_pollution_name", "other_pollution_name",
    ]

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        points = []
        for field in self._NAME_FIELDS:
            points.append(NormalizedDataPoint(
                field=field, value="無", unit=None, source=SRC, source_type="Mock",
                coordinate=None, confidence="高", retrieved_at=now,
                notes="Mock資料，取自Golden Case（該類設施/污染源記載為「無」）",
            ))
            base = field.rsplit("_name", 1)[0]
            points.append(self._unknown(f"{base}_within_segment", "該設施記載為「無」，本區段內外欄位空白"))
            points.append(self._unknown(f"{base}_distance_m", "該設施記載為「無」，距離欄位空白"))
        return points


from real_facility_provider_base import RealFacilityProviderBase, FacilitySpec  # noqa: E402


class RealEnvironmentalProvider(RealFacilityProviderBase):
    """Real (OSM Overpass) implementation for the 3 fields with a plausible
    OSM tag mapping (sewage_plant/landfill/incinerator). The other 5 fields
    (water/noise/air/waste/other_pollution) are pollution-SOURCE
    assessments -- schemas/field_dictionary.json does define name/
    within_segment/distance_m for them, implying a specific site could in
    principle be named, but there is no OSM tag meaning 'this site is a
    noise/air/water pollution source' (pollution is an assessed effect, not
    a mappable feature), so attempting a query would only waste an API call
    and always fail. These stay MANUAL_REVIEW_REQUIRED, same as Mock."""

    provider_name = "RealEnvironmentalProvider"

    # 3000M matches data/rules/regional_rules.json's max-relevant distance
    # for 廢棄物處理設施之有無及接近程度 (the closest analogous official factor
    # for all three of these).
    FACILITY_SPECS = [
        FacilitySpec("sewage_plant", ["man_made=wastewater_plant"], radius_m=3000),
        FacilitySpec("landfill", ["landuse=landfill"], radius_m=3000),
        FacilitySpec("incinerator", ["man_made=incinerator", "power=generator"], radius_m=3000),
    ]

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        points = super().fetch(ctx)
        for base in ("water_pollution", "noise_pollution", "air_pollution",
                     "waste_pollution", "other_pollution"):
            for suffix in ("_name", "_within_segment", "_distance_m"):
                points.append(self._unknown(
                    base + suffix,
                    "汙染源為評估性質，無對應之OSM可查詢設施標籤，非本系統可自動判定，需人工確認",
                ))
        return points
