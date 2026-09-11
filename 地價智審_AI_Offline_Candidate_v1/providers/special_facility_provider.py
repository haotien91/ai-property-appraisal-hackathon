# -*- coding: utf-8 -*-
"""SpecialFacilityProvider — 電業氣體燃料／殯葬設施等."""
from __future__ import annotations
from datetime import datetime
from typing import List

from base import DataProvider, ProviderContext
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


class MockSpecialFacilityProvider(DataProvider):
    provider_name = "MockSpecialFacilityProvider"

    _KNOWN_VALUES = {
        "substation_name": ("金山變電所", None),
        "substation_within_segment": (False, None),
        "substation_distance_m": (700, "M"),
        "gas_tank_name": ("中油金山站", None),
        "gas_tank_within_segment": (False, None),
        "gas_tank_distance_m": (440, "M"),
        "cemetery_name": ("金山第1公墓", None),
        "cemetery_within_segment": (False, None),
        "cemetery_distance_m": (80, "M"),
        "funeral_home_name": ("無", None),
        "crematorium_name": ("無", None),
        "columbarium_name": ("金山區公所福緣納骨堂", None),
        "columbarium_within_segment": (False, None),
        "columbarium_distance_m": (750, "M"),
    }

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        points = []
        for field, (value, unit) in self._KNOWN_VALUES.items():
            notes = (
                "Mock資料，取自Golden Case；距離量測方法為直線距離，"
                "已於8/26工作坊口頭覆述2次確認（docs/phase2/distance_rules.md）"
                if "distance_m" in field else "Mock資料，取自Golden Case"
            )
            points.append(NormalizedDataPoint(
                field=field, value=value, unit=unit, source=SRC, source_type="Mock",
                coordinate=None, confidence="高", retrieved_at=now, notes=notes,
            ))
        for field in ("funeral_home_within_segment", "funeral_home_distance_m",
                      "crematorium_within_segment", "crematorium_distance_m"):
            points.append(self._unknown(field, "查估書表範本.pdf表1 Golden Case該設施記載為「無」，距離欄位空白"))
        return points


from real_facility_provider_base import RealFacilityProviderBase, FacilitySpec  # noqa: E402


class RealSpecialFacilityProvider(RealFacilityProviderBase):
    """Real (OSM Overpass) implementation. These are all 嫌惡設施-type
    facilities -- 作業手冊/逐字稿 both confirm straight-line distance is the
    CONFIRMED method for this category (see engine/geo_distance_engine.py),
    which is exactly what find_nearest_facility/GeoDistanceEngine compute,
    so this is on solid methodological ground (unlike the transportation
    provider's 一般設施 fields, where the confirmed method is walking
    distance, not straight-line -- see docs/phase2/distance_rules.md)."""

    provider_name = "RealSpecialFacilityProvider"

    # Radii match data/rules/regional_rules.json's actual max-relevant
    # distance: 電業/氣體燃料設施=3000M, 殯葬設施(涵蓋cemetery/funeral_home/
    # crematorium/columbarium)=3000M. Confirmed empirically too: a real
    # cemetery found near a live-tested Banqiao coordinate sat at 2024M --
    # a 2000M radius (an earlier, un-sourced guess) would have missed it.
    FACILITY_SPECS = [
        FacilitySpec("substation", ["power=substation"], radius_m=3000),
        FacilitySpec("gas_tank", ["amenity=fuel", "man_made=gasometer"], radius_m=3000),
        FacilitySpec("cemetery", ["landuse=cemetery", "amenity=grave_yard"], radius_m=3000),
        FacilitySpec("funeral_home", ["amenity=funeral_hall"], radius_m=3000),
        FacilitySpec("crematorium", ["amenity=crematorium"], radius_m=3000),
        # No widely-adopted OSM tag for columbarium specifically; this is a
        # best-effort attempt that will often legitimately find nothing --
        # that degrades to the same honest MANUAL_REVIEW_REQUIRED as any
        # other not-found case, never a guess.
        FacilitySpec("columbarium", ["building=columbarium", "amenity=columbarium"], radius_m=3000),
    ]
