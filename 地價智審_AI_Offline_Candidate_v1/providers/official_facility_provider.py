# -*- coding: utf-8 -*-
"""
OfficialFacilityProvider — Phase API-2's Official Facility Evidence
pipeline (schools/stations/markets/parks), sourced from 新北市政府 OpenData
ONLY (no TGOS/NLSC/OSM/Nominatim -- see module docstring's "Official-first"
section for why, and docs/phase9/facility_evidence_pipeline.md for the full
audit this module implements).

---

## Repo Audit Finding (Phase API-2 §1)

Before writing this module, the repo was audited for existing facility/POI
capability (per this round's explicit "不要重複造已存在功能" instruction).
Found: providers/real_facility_provider_base.py + providers/osm_facility_
lookup.py already implement a DIFFERENT real-mode pipeline for a largely
overlapping set of factors (market/park/major_station/school -- see
providers/public_facility_provider.py, providers/transportation_provider.py)
via OpenStreetMap's Overpass API. That pipeline is NOT replaced or modified
by this module -- it continues to serve FACTORS.points exactly as before.
This module is a SEPARATE, ADDITIVE evidence trail (mirroring how Phase
API-1's ExpropriationCaseEvidence/LandPriceEvidence sit alongside, not
instead of, existing NormalizedDataPoint fields), specifically because this
round's mandate is OFFICIAL (government-published) data, which the existing
OSM-based pipeline explicitly is not (its own code/docs already say so --
see real_facility_provider_base.py's docstrings: "OSM為社群協作資料，非官方
登記資料"). No CoordinateProvider/GeocodingProvider exists beyond the
Nominatim-based `geocode()` used elsewhere (out of scope here -- this round
forbids geocoding fallback, see below).

## NTPC OpenData Audit (Phase API-2 §3) -- summary; full detail in
## docs/phase9/facility_evidence_pipeline.md

Catalog-wide search (via https://data.ntpc.gov.tw/api/datasets/info/csv,
1,747 datasets total) for school/market/park/station-relevant entries found:

- **新北市重業地標資訊** (dataset_id `6DCFF24A-838C-40FB-A9DF-F1160AFAFE84`,
  資訊中心/網際服務科, daily update, sourced from 新北iMAP): 2,056 rows
  across 29 landmark categories, INCLUDING every school tier (國民小學=242,
  國民中學=110, 完全中學=50, 高中職=44, 大專院校=26 -- 472 total) AND both
  rail-station categories (捷運站=115, 火車站=27 -- 142 total), each with
  `twd97_x`/`twd97_y` coordinates (see facility_dataset_cache.py's module
  docstring for the TWD97->WGS84 reprojection this requires). VERIFIED via
  direct field inspection, not assumed from the dataset's title. This is
  the SOLE source for SCHOOL and STATION in this round.
- **新北市公有市場及超市清冊** (`785BE91A-CAAF-4E1C-91D6-F7D616D31A45`,
  經濟發展局/市場處, annual): official market/supermarket registry, fields
  = item/name/county/countycode/town/areacode/address/phone/types --
  VERIFIED via direct field inspection to have **NO coordinate field at
  all**. Name/address/district only.
- **新北市公園** (`5FE3A136-29CC-4695-A17E-6636A32C3342`, 農業局/綠美化
  環境景觀處, annual): fields = seqno/name/area/address/management/
  localcallservice/areacode -- VERIFIED, also **NO coordinate field**.
- No dedicated MRT/TRA-only dataset, and no separate 公車站 (bus stop)
  dataset was used for STATION -- 公車站位資訊 (`34B402A8-...`) exists but
  was deliberately excluded: a bus stop is NOT what the appraisal manual's
  "大型車站"/接近條件"車站" means (this round's explicit instruction), and
  the landmark dataset's own 捷運站/火車站 categories already give a clean,
  officially-labeled STATION set without needing that judgment call at all.

`filter` query-parameter behavior (NTPC_SERVER_FILTER_TRUST_POLICY =
UNTRUSTED_UNLESS_VERIFIED_PER_DATASET, carried over from Phase API-1):
tested on the landmark dataset with (a) `filter=地標類型 eq 國民小學`,
(b) no filter, (c) `filter=地標類型 eq <impossible value>`, (d) `filter=
行政區 eq 金山區` -- ALL FOUR returned the exact same 2,056 rows, so
`filter` is a CONFIRMED COMPLETE NO-OP on this dataset too (third NTPC
dataset in this project found to ignore `filter` entirely, after the
expropriation and land-price datasets in Phase API-1). scripts/sync_
facility_dataset.py therefore never sends `filter` and does full-table
fetch + client-side type/district matching, exactly like the frozen
cadastral pipeline's sync scripts.

## Golden Case Coordinate Availability (Phase API-2 §11)

Checked `data/golden/golden_case_input.py` (no coordinate field at all) and
every other Golden-Case-adjacent fixture in this repo: NONE carries an
official, parcel-specific coordinate for 金山區/金美段/489地號. The ONLY
coordinate-producing paths that exist today are (a) `providers/
transportation_provider.py`'s `JINSHAN_DISTRICT_CENTROID` -- explicitly
documented there as a Wikipedia-sourced DEMO-ONLY anchor, never claimed as
authoritative for this parcel, and (b) `backend/handlers/collect_data.py`'s
`_resolve_center_coordinate()` Nominatim fallback -- a coarse city/district/
segment-name TEXT geocode, not a verified official parcel coordinate, and
explicitly out of scope for THIS round's evidence pipeline (§5/§10: "不要
偷偷Nominatim geocode...本輪不做fallback"). Neither qualifies as the
"官方/可信座標" this round requires before computing real distance evidence
for the Golden Case -- see GOLDEN_CASE_COORDINATE_READY=NO in
docs/phase9/facility_evidence_pipeline.md for the full reasoning and what
would be needed to close this gap (an official parcel-level coordinate
registry, which would itself require TGOS/NLSC or a cadastral map digitizing
effort -- both explicitly out of this round's scope).

## Distance ≠ Grade (Phase API-2 §8)

`query_facility()` below NEVER returns a grade or adjustment rate. It
returns `FacilityEvidence` (facts: what/where/how far). Grade assignment
remains entirely the job of the existing, unmodified deterministic Rule/
Grade Engine reading data/rules/individual_rules.json ("接近學校之程度")
and data/rules/regional_rules.json ("接近市場/公園/大型車站之程度"), fed by
a human-reviewed form value -- this provider is not wired into either engine
and does not attempt to be.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import List, Optional

from base import DataProvider, ProviderContext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    NormalizedDataPoint, Coordinate, FacilityType, FacilityMatch, FacilityEvidence,
    FacilityDistanceMethod, FacilityCoordinateStatus, FacilityDistanceSemantics,
    TargetCoordinateEvidence, CoordinateSourceType, CoordinateAuthoritativeStatus,
)
from engine.geo_distance_engine import GeoDistanceEngine  # noqa: E402

from facility_dataset_cache import FacilityDatasetCache, FacilityDatasetCacheError, SCOPE_ALL  # noqa: E402

LANDMARK_DATASET_ID = "6DCFF24A-838C-40FB-A9DF-F1160AFAFE84"
LANDMARK_DATASET_NAME = "新北市重要地標資訊"
LANDMARK_SOURCE_URL = f"https://data.ntpc.gov.tw/datasets/{LANDMARK_DATASET_ID.lower()}"

MARKET_DATASET_ID = "785BE91A-CAAF-4E1C-91D6-F7D616D31A45"
MARKET_DATASET_NAME = "新北市公有市場及超市清冊"
MARKET_SOURCE_URL = f"https://data.ntpc.gov.tw/datasets/{MARKET_DATASET_ID.lower()}"

PARK_DATASET_ID = "5FE3A136-29CC-4695-A17E-6636A32C3342"
PARK_DATASET_NAME = "新北市公園"
PARK_SOURCE_URL = f"https://data.ntpc.gov.tw/datasets/{PARK_DATASET_ID.lower()}"

SOURCE_AUTHORITY_LANDMARK = "新北市政府研究發展考核委員會（新北iMAP）"
SOURCE_AUTHORITY_MARKET = "新北市政府經濟發展局/新北市政府市場處"
SOURCE_AUTHORITY_PARK = "新北市政府農業局/新北市政府綠美化環境景觀處"

# Maps FacilityType -> (dataset_id, dataset_name, source_url, source_authority)
_FACILITY_DATASET_INFO = {
    FacilityType.SCHOOL: (LANDMARK_DATASET_ID, LANDMARK_DATASET_NAME, LANDMARK_SOURCE_URL, SOURCE_AUTHORITY_LANDMARK),
    FacilityType.STATION: (LANDMARK_DATASET_ID, LANDMARK_DATASET_NAME, LANDMARK_SOURCE_URL, SOURCE_AUTHORITY_LANDMARK),
    FacilityType.MARKET: (MARKET_DATASET_ID, MARKET_DATASET_NAME, MARKET_SOURCE_URL, SOURCE_AUTHORITY_MARKET),
    FacilityType.PARK: (PARK_DATASET_ID, PARK_DATASET_NAME, PARK_SOURCE_URL, SOURCE_AUTHORITY_PARK),
}

MOCK_SOURCE = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"

# Phase API-2.1 §6: facility types whose source dataset carries a real
# WGS84 coordinate (SCHOOL/STATION, via 新北市重要地標資訊) -- these are the
# only types for which `ctx.district` is dropped as a hard filter once a
# query coordinate is available (cross-district nearest search). MARKET/
# PARK never have coordinates at all (ADDRESS_ONLY), so there is no
# distance ranking for a district filter to distort -- they stay
# district-scoped as a pure display/optimization narrowing in every case.
_CROSS_DISTRICT_TYPES = {FacilityType.SCHOOL, FacilityType.STATION}


class MockOfficialFacilityProvider(DataProvider):
    """Mock Mode: never touches the network or any local snapshot --
    matches MockExpropriationCaseProvider/MockLandPriceProvider's existing
    convention (Phase API-1) of honestly returning UNKNOWN/empty rather
    than fabricating a plausible-looking facility match."""
    provider_name = "MockOfficialFacilityProvider"

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        return [
            NormalizedDataPoint(
                field=f"official_facility_{ft.value.lower()}_match_count", value=None, unit=None,
                source=MOCK_SOURCE, source_type="Mock", coordinate=None,
                confidence="UNKNOWN", retrieved_at=now,
                notes="Mock Mode：本Provider於Mock Mode不呼叫任何網路服務或本地snapshot，誠實回傳UNKNOWN。",
            )
            for ft in FacilityType
        ]

    def query_facility(self, ctx: ProviderContext, facility_type: FacilityType) -> FacilityEvidence:
        return FacilityEvidence(
            facility_type=facility_type, query_coordinate=None, query_district=ctx.district,
            target_coordinate_evidence=None,
            matches=[], nearest=None, match_count=0,
            distance_method=FacilityDistanceMethod.UNKNOWN,
            distance_semantics=FacilityDistanceSemantics.NOT_APPLICABLE,
            distance_authoritative_status="UNKNOWN", official_distance_ready=False,
            confidence="UNKNOWN", requires_manual_review=True,
            notes="Mock Mode：未呼叫真實資料，無官方查證結果可供示範。",
        )


class RealOfficialFacilityProvider(DataProvider):
    """Real path never falls back to Mock. `cache` is DI-injectable for
    tests, mirroring RealExpropriationCaseProvider/RealLandPriceProvider's
    existing pattern."""
    provider_name = "RealOfficialFacilityProvider"

    def __init__(self, cache: Optional[FacilityDatasetCache] = None):
        self._cache = cache or FacilityDatasetCache()
        self._distance_engine = GeoDistanceEngine()

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        points = []
        for ft in FacilityType:
            evidence = self.query_facility(ctx, ft)
            points.append(NormalizedDataPoint(
                field=f"official_facility_{ft.value.lower()}_match_count", value=evidence.match_count,
                unit=None, source=evidence.source_url or (evidence.dataset_name or self.provider_name),
                source_type="GovernmentOpenData", coordinate=None,
                confidence=evidence.confidence, retrieved_at=now, notes=evidence.notes,
            ))
        return points

    def query_facility(self, ctx: ProviderContext, facility_type: FacilityType) -> FacilityEvidence:
        """PRIMARY (and only) path -- reads the local synced snapshot.
        Never queries data.ntpc.gov.tw directly (matches the frozen
        cadastral pipeline's "sync out-of-band, query the snapshot"
        contract).

        Phase API-2.1 §6 Cross-District Spatial Search: for coordinate-
        bearing types (SCHOOL/STATION -- see `_CROSS_DISTRICT_TYPES`),
        `ctx.district` is used as a hard filter ONLY when no query
        coordinate is available (there, it is purely a display/
        optimization narrowing, never a spatial-correctness concern since
        no distance ranking happens in that branch). Once a query
        coordinate IS available, the lookup spans ALL districts so a
        genuinely nearer facility just across a district boundary is never
        excluded (see this module's `test_boundary_...` coverage in
        tests/test_official_facility_provider.py). MARKET/PARK (no
        coordinates at all, ever) stay district-scoped in both branches --
        there is no distance ranking to get wrong for them regardless."""
        retrieved_at = datetime.now()
        dataset_id, dataset_name, source_url, source_authority = _FACILITY_DATASET_INFO[facility_type]
        query_coordinate = ctx.center_coordinate
        target_coordinate_evidence = self._resolve_target_coordinate_evidence(ctx)
        has_query_coordinate = query_coordinate is not None and query_coordinate.latitude is not None \
            and query_coordinate.longitude is not None
        lookup_district = None if (has_query_coordinate and facility_type in _CROSS_DISTRICT_TYPES) else ctx.district

        try:
            raw_matches = self._cache.lookup_facilities(
                dataset_id, SCOPE_ALL, facility_type.value, district=lookup_district
            )
        except FacilityDatasetCacheError:
            return FacilityEvidence(
                facility_type=facility_type, query_coordinate=query_coordinate, query_district=ctx.district,
                target_coordinate_evidence=target_coordinate_evidence,
                matches=[], nearest=None, match_count=0,
                distance_method=FacilityDistanceMethod.UNKNOWN,
                distance_semantics=FacilityDistanceSemantics.NOT_APPLICABLE,
                distance_authoritative_status="UNKNOWN", official_distance_ready=False,
                dataset_id=dataset_id, dataset_name=dataset_name,
                source_authority=source_authority, source_url=source_url, retrieved_at=retrieved_at,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=f"官方{dataset_name}資料尚未同步至本地snapshot，無法查詢。請先執行 `py scripts/sync_facility_dataset.py`。",
            )

        meta = self._cache.get_snapshot_meta(dataset_id, SCOPE_ALL)
        stored_record_count = meta.get("record_count") if meta else None
        source_record_count = meta.get("source_record_count") if meta else None

        if not has_query_coordinate:
            # TARGET_COORDINATE_UNAVAILABLE (Phase API-2 §11): never
            # fabricate a coordinate to compute a plausible-looking
            # distance. Facilities themselves are still listed (they are
            # real, independent of whether THIS query has a coordinate),
            # just with distance_m=None for every one of them.
            matches = [self._to_match(r, distance_m=None) for r in raw_matches]
            return FacilityEvidence(
                facility_type=facility_type, query_coordinate=None, query_district=ctx.district,
                target_coordinate_evidence=target_coordinate_evidence,
                matches=matches, nearest=None, match_count=len(matches),
                distance_method=FacilityDistanceMethod.UNKNOWN,
                distance_semantics=FacilityDistanceSemantics.NOT_APPLICABLE,
                distance_authoritative_status="UNKNOWN", official_distance_ready=False,
                dataset_id=dataset_id, dataset_name=dataset_name, dataset_version=meta.get("downloaded_at") if meta else None,
                source_authority=source_authority, source_url=source_url, retrieved_at=retrieved_at,
                checksum=meta.get("checksum_sha256") if meta else None,
                source_record_count=source_record_count, stored_record_count=stored_record_count,
                record_count=stored_record_count,
                authoritative_status="OFFICIAL_OPEN_DATA", confidence="中" if matches else "UNKNOWN",
                requires_manual_review=True,
                notes="TARGET_COORDINATE_UNAVAILABLE：查詢座標未提供，無法計算距離；已列出符合類型/行政區之全部官方設施供人工比對。",
            )

        matches = []
        for r in raw_matches:
            distance_m = None
            if r.get("coordinate_status") == FacilityCoordinateStatus.WGS84.value and \
                    r.get("latitude") is not None and r.get("longitude") is not None:
                dest = Coordinate(latitude=r["latitude"], longitude=r["longitude"])
                result = self._distance_engine.straight_line_distance(
                    query_coordinate, "查詢座標", dest, r.get("name") or "官方設施",
                    source="GeoDistanceEngine（Haversine great-circle公式，WGS84）",
                )
                distance_m = result.distance_m
            matches.append(self._to_match(r, distance_m=distance_m))

        computable = [m for m in matches if m.distance_m is not None]
        # Deterministic tie-break: `matches` (and therefore `computable`,
        # which preserves that order via the list comprehension above) is
        # already sorted by lookup_facilities() on (district,name,
        # facility_id) -- Python's min() returns the FIRST minimal element
        # in iteration order on a tie, so two facilities at an identical
        # distance always resolve to the same one, never an arbitrary pick
        # (see tests/test_official_facility_provider.py::
        # test_tie_distance_deterministic_tiebreak).
        nearest = min(computable, key=lambda m: m.distance_m) if computable else None

        if computable:
            distance_method = FacilityDistanceMethod.HAVERSINE_WGS84
            distance_semantics = FacilityDistanceSemantics.STRAIGHT_LINE_REFERENCE
            if target_coordinate_evidence is not None and \
                    target_coordinate_evidence.authoritative_status == CoordinateAuthoritativeStatus.OFFICIAL:
                distance_authoritative_status = "OFFICIAL"
                official_distance_ready = True
            else:
                # Facility coordinate is official (NTPC OpenData); target
                # coordinate is not (or its provenance is unknown) -- Phase
                # API-2.1 §3: MUST NOT be reported as OFFICIAL just because
                # a non-None coordinate happened to be present.
                distance_authoritative_status = "MIXED_SOURCE"
                official_distance_ready = False
        else:
            distance_method = FacilityDistanceMethod.UNKNOWN
            distance_semantics = FacilityDistanceSemantics.NOT_APPLICABLE
            distance_authoritative_status = "UNKNOWN"
            official_distance_ready = False

        address_only_note = ""
        if raw_matches and not computable:
            address_only_note = "官方資料集僅提供名稱/地址，無座標欄位（ADDRESS_ONLY），無法計算距離，僅供人工比對地址。"
        mixed_source_note = ""
        if distance_authoritative_status == "MIXED_SOURCE":
            src = target_coordinate_evidence.source_type.value if target_coordinate_evidence else "UNKNOWN"
            mixed_source_note = (
                f"distance_authoritative_status=MIXED_SOURCE：設施座標為官方資料"
                f"（NTPC OpenData），但查詢座標來源為{src}（非OFFICIAL），"
                "此距離不得標示為正式官方距離證據，僅供參考。"
            )

        return FacilityEvidence(
            facility_type=facility_type, query_coordinate=query_coordinate, query_district=ctx.district,
            target_coordinate_evidence=target_coordinate_evidence,
            matches=matches, nearest=nearest, match_count=len(matches),
            distance_method=distance_method, distance_semantics=distance_semantics,
            distance_authoritative_status=distance_authoritative_status, official_distance_ready=official_distance_ready,
            dataset_id=dataset_id, dataset_name=dataset_name, dataset_version=meta.get("downloaded_at") if meta else None,
            source_authority=source_authority, source_url=source_url, retrieved_at=retrieved_at,
            checksum=meta.get("checksum_sha256") if meta else None,
            source_record_count=source_record_count, stored_record_count=stored_record_count,
            record_count=stored_record_count,
            authoritative_status="OFFICIAL_OPEN_DATA", confidence="中" if matches else "UNKNOWN",
            requires_manual_review=True,
            notes=(
                address_only_note or mixed_source_note or (
                    f"共查得{len(matches)}筆符合官方設施（搜尋範圍：新北市），"
                    "僅供距離事實佐證（nearest僅為此資料集涵蓋範圍內之最近設施——"
                    "nearest_within_dataset_coverage，非nearest_global，亦非正式查估設施選定）；"
                    "依查估作業手冊p.24，若有多筆同類設施，應由查估人員判斷「對當地地價影響最大者」，"
                    "非本Provider自動之nearest可直接代表。"
                    if matches else "官方資料集中查無符合類型之設施紀錄。"
                )
            ),
        )

    def _resolve_target_coordinate_evidence(self, ctx: ProviderContext) -> Optional[TargetCoordinateEvidence]:
        """Phase API-2.1 §2-4: reads `ctx.center_coordinate_evidence` when
        the caller populated it (backend/handlers/collect_data.py always
        does, since this round). For a caller that constructed
        ProviderContext with only `center_coordinate` (no evidence -- e.g.
        an older test, or a future direct API caller that skips the new
        field), defensively synthesizes an UNKNOWN-provenance evidence
        rather than treating an absent evidence object as "no coordinate at
        all" or, worse, silently as official -- this is exactly the
        contamination risk this round's audit exists to close."""
        if ctx.center_coordinate_evidence is not None:
            return ctx.center_coordinate_evidence
        if ctx.center_coordinate is not None and ctx.center_coordinate.latitude is not None:
            return TargetCoordinateEvidence(
                latitude=ctx.center_coordinate.latitude, longitude=ctx.center_coordinate.longitude,
                source_type=CoordinateSourceType.UNKNOWN,
                authoritative_status=CoordinateAuthoritativeStatus.UNKNOWN,
                precision_level="UNKNOWN", retrieved_at=datetime.now(),
                notes="ProviderContext未附center_coordinate_evidence，來源無法追溯，保守視為UNKNOWN，不得視為OFFICIAL。",
            )
        return None

    @staticmethod
    def _to_match(row: dict, distance_m: Optional[float]) -> FacilityMatch:
        return FacilityMatch(
            facility_id=row.get("facility_id"), facility_type=FacilityType(row["facility_type"]),
            facility_subtype=row.get("facility_subtype"), source_category=row.get("source_category"),
            name=row.get("name") or "（無名稱）", address=row.get("address"), district=row.get("district"),
            latitude=row.get("latitude"), longitude=row.get("longitude"),
            coordinate_status=FacilityCoordinateStatus(row.get("coordinate_status")),
            distance_m=distance_m,
            source_dataset_id=row.get("source_dataset_id"), source_authority=row.get("source_authority"),
        )
