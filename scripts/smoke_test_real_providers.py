# -*- coding: utf-8 -*-
"""
Live smoke test for the Real*Provider classes (providers/{transportation,
public_facility,special_facility,environmental,commercial_activity}_
provider.py). Deliberately NOT collected by pytest -- it makes real network
calls to public OpenStreetMap services (Overpass/Nominatim), which is slow,
depends on a third party's uptime, and can be transiently rate-limited (see
providers/osm_facility_lookup.py's docstring). The offline, deterministic
proof that the parsing/field-mapping logic is correct lives in
tests/test_osm_facility_lookup.py and tests/test_real_facility_providers.py
instead (both mock the network call).

What THIS script proves that the mocked tests cannot: that the whole chain
genuinely answers for a real, previously-unseen location -- not just that
it doesn't crash. Run it manually, read the output, and sanity-check that
the facility names look plausible for the chosen coordinate; there is no
programmatic pass/fail here because there is no ground truth to assert
against (that's the entire point -- this coordinate is NOT the Golden
Case).

Usage:
    py scripts/smoke_test_real_providers.py [latitude] [longitude]

Defaults to a coordinate near Banqiao Station, New Taipei City --
deliberately NOT 金山區 (Phase 1 REQ-006/007: the competition segment is
guaranteed to be different from the Golden Case; this script exists to
demonstrate the system already works somewhere else).
"""
from __future__ import annotations

import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from domain.models import Coordinate  # noqa: E402
from transportation_provider import RealTransportationProvider  # noqa: E402
from public_facility_provider import RealPublicFacilityProvider  # noqa: E402
from special_facility_provider import RealSpecialFacilityProvider  # noqa: E402
from environmental_provider import RealEnvironmentalProvider  # noqa: E402
from commercial_activity_provider import RealCommercialActivityProvider  # noqa: E402

DEFAULT_LAT = 25.0138
DEFAULT_LON = 121.4629  # near Banqiao Station, New Taipei City


def main() -> int:
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LAT
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LON

    ctx = ProviderContext(
        case_no="SMOKE-TEST", city="新北市", district="(smoke test)", segment_code="SMOKE-TEST",
        center_coordinate=Coordinate(latitude=lat, longitude=lon),
    )

    print(f"座標: {lat}, {lon}")
    print("=" * 70)

    found_count = 0
    total_count = 0
    for ProviderCls in (
        RealTransportationProvider, RealPublicFacilityProvider, RealSpecialFacilityProvider,
        RealEnvironmentalProvider, RealCommercialActivityProvider,
    ):
        print(f"\n--- {ProviderCls.__name__} ---")
        for point in ProviderCls().fetch(ctx):
            total_count += 1
            if point.value is not None:
                found_count += 1
                unit = f" {point.unit}" if point.unit else ""
                print(f"  [FOUND]   {point.field} = {point.value}{unit}  (confidence={point.confidence})")
            else:
                pass  # UNKNOWN fields are expected and numerous; not printed to keep output readable

    print("\n" + "=" * 70)
    print(f"共 {total_count} 個欄位，{found_count} 個查得真實資料，"
          f"{total_count - found_count} 個為MANUAL_REVIEW_REQUIRED（含刻意不猜測之主觀欄位）")
    print("請人工檢查上方[FOUND]之設施名稱與距離，是否是該座標附近合理存在的真實地點。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
