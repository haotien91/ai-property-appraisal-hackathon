# -*- coding: utf-8 -*-
"""collect_data.py — POST /api/cases/{id}/collect-data. Wires the Phase 5
Data Providers. Two modes, selected by the DATA_PROVIDER_MODE env var:

- "mock" (default): every provider returns the Golden Case's hardcoded
  values regardless of which segment/case is asked about. Safe, fast,
  offline -- what every existing test relies on, so this stays the default.
- "real": 6 of the 7 providers query a real data source for whatever
  coordinate the case is actually at -- this is what makes the system
  answer for a genuinely new, previously unseen competition-day segment
  (Phase 1 REQ-006/007) instead of always echoing 金山區 Golden Case
  numbers:
    - transportation/public_facility/special_facility/environmental/
      commercial_activity query OpenStreetMap live (Overpass/Nominatim).
      Set DATA_PROVIDER_MODE=real (e.g. in infra/template.yaml's Lambda
      environment variables) once AWS network egress to overpass-api.de /
      nominatim.openstreetmap.org is confirmed reachable during deployment
      rehearsal.
    - land_use_provider (RealLandUseProvider) answers `land_use_zone` from
      a LOCAL SNAPSHOT of NTPC's zoning shapefile (see providers/
      ntpc_zoning_provider.py + scripts/sync_ntpc_zoning_dataset.py) --
      this one does NOT touch the network at request time, only reads
      whatever DatasetRegistry currently points to. Every other
      land_use_provider field (building_coverage_ratio, floor_area_ratio,
      construction_prohibited/restricted, drainage_quality, terrain, ...)
      still has no real-data source and stays UNKNOWN in real mode.
    - road_provider (RealRoadProvider, Phase 8's Road Width Multi-Evidence
      Model -- see providers/road_provider.py's module docstring for the
      full A-E source survey) answers `main_road_width` via
      RoadWidthResolver, ONLY if evidence_sources are injected into it; the
      DEFAULT construction used here has none wired (no confirmed reliable
      real source exists yet), so it genuinely returns UNKNOWN for
      main_road_width in real mode -- never Golden Case's 18m/12m.
      segment_avg_road_width/road_development_level have no resolution
      infrastructure at all yet and always stay UNKNOWN in real mode too.

Only "mock" and "real" exist -- there is no "offline" or "hybrid" mode in
this codebase. DATA_PROVIDER_MODE is validated at module-load time:
unset -> "mock" (an explicit development default, not a fallback); set to
"mock" or "real" -> used as given; set to anything else (a typo, "offline",
"hybrid", an empty string, ...) -> raises immediately rather than silently
using Mock. "Real path must never fall back to Mock" is a project-wide
invariant -- an unrecognized mode is a deployment misconfiguration, not a
degraded-but-safe state, so it must fail loudly (Lambda cold start / import
failure) rather than quietly serving Golden Case numbers for what the
caller believed was a real-mode request.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402
import competition_segments  # noqa: E402
from competition_segments import InvalidSegmentCodeError  # noqa: E402

from base import ProviderContext  # noqa: E402
from land_use_provider import MockLandUseProvider, RealLandUseProvider  # noqa: E402
from urban_plan_boundary_provider import RealUrbanPlanBoundaryProvider  # noqa: E402
from road_provider import MockRoadProvider, RealRoadProvider  # noqa: E402
from transportation_provider import MockTransportationProvider, RealTransportationProvider  # noqa: E402
from public_facility_provider import MockPublicFacilityProvider, RealPublicFacilityProvider  # noqa: E402
from special_facility_provider import MockSpecialFacilityProvider, RealSpecialFacilityProvider  # noqa: E402
from environmental_provider import MockEnvironmentalProvider, RealEnvironmentalProvider  # noqa: E402
from commercial_activity_provider import (  # noqa: E402
    MockCommercialActivityProvider, RealCommercialActivityProvider,
)
from osm_facility_lookup import geocode  # noqa: E402
from domain.models import (  # noqa: E402
    Coordinate, FacilityType, TargetCoordinateEvidence, CoordinateSourceType, CoordinateAuthoritativeStatus,
    CoordinateComparisonEvidence, CoordinateComparisonStatus, UrbanPlanStatus,
)
from engine.geo_distance_engine import GeoDistanceEngine  # noqa: E402

# Phase API-1 (Multi-Jurisdiction Official Data + API Integration Audit's
# approved first slice): 新北市已公告徵收案件地籍資料 + 新北市公告土地現值,
# both data.ntpc.gov.tw OpenAPI, both VERIFIED_AVAILABLE with no auth. Their
# fetch() output is EXTERNAL CORROBORATING EVIDENCE ONLY (see each module's
# own docstring) -- it flows into FACTORS.points exactly like every other
# provider's NormalizedDataPoint, but these two field names
# (expropriation_case_status/announced_land_current_value/announced_land_
# price) have no entry in _REGIONAL_LAND_USE_FIELD_MAP below and are never
# consumed by RuleEngine/AuditEngine this round.
from expropriation_case_provider import (  # noqa: E402
    MockExpropriationCaseProvider, RealExpropriationCaseProvider,
)
from land_price_provider import MockLandPriceProvider, RealLandPriceProvider  # noqa: E402

# Phase DEPLOY-1E: data/cadastral_dataset_cache.sqlite3 (~407MB) cannot be
# packaged into EngineLayer (structurally exceeds Lambda's 250MB combined
# Layer+package limit -- see Phase DEPLOY-1's packaging audit), so it is
# fetched from S3 into /tmp on cold start instead. This module ONLY moves
# a file (download + SHA-256 verify + atomic publish); it never opens the
# file as a database, never queries it, and never falls back to Mock/
# Golden data on failure -- see its own module docstring for the full
# safety invariant. RealExpropriationCaseProvider/RealLandPriceProvider
# (both frozen, both unmodified) remain the ONLY code that ever
# constructs CadastralDatasetCache and reads CADASTRAL_DATASET_CACHE_
# DB_PATH; this import/call is purely additive plumbing in front of them.
import cadastral_snapshot_bootstrap  # noqa: E402

# 2026-09-09 (URBAN_PLAN_BOUNDARY_SNAPSHOT_NOT_PACKAGED, see docs/backlog.md):
# same rationale as cadastral_snapshot_bootstrap.py above, independently
# re-applied to a different, disjoint dataset family (新北市使用分區 /
# 新北市都市計畫範圍 -- see gis_snapshot_bootstrap.py's own module
# docstring for why these could not simply be baked into EngineLayer like
# data/nlsc_code_cache.sqlite3 was). No-op outside Lambda -- see that
# module's ensure_gis_snapshots().
import gis_snapshot_bootstrap  # noqa: E402

# Phase API-2 (Official Facility Evidence, additive per §11 of that round's
# audit): OfficialFacilityProvider's fetch() answers official_facility_
# {school,market,park,station}_match_count from a LOCAL SNAPSHOT of NTPC
# OpenData (providers/facility_dataset_cache.py + scripts/sync_facility_
# dataset.py) -- like RealExpropriationCaseProvider/RealLandPriceProvider,
# it never queries the network at request time, and market/park matches
# never carry a distance (their source datasets have no coordinate field --
# see providers/official_facility_provider.py's module docstring's NTPC
# OpenData audit). Full FacilityEvidence (matches[]/nearest/provenance) is
# persisted separately below, same pattern as _resolve_expropriation_case_
# evidence.
from official_facility_provider import (  # noqa: E402
    MockOfficialFacilityProvider, RealOfficialFacilityProvider,
)

# Phase API-2.3 (Official Parcel Coordinate Provider, Auth-Gated), Fallback
# Priority corrected Phase API-2.3F (§3-4): queries NLSC's CadasMapPosition
# (CAD_001) official 宗地代表點 INDEPENDENTLY of any submitted coordinate
# (never blocked by one -- see _resolve_coordinate_evidence_bundle below).
# Not registered in _MODE_PROVIDERS -- it does not produce a FACTORS.points
# NormalizedDataPoint this round, only a TargetCoordinateEvidence for
# coordinate resolution (see that module's `to_target_coordinate_
# evidence()`). Importing RealOfficialParcelCoordinateProvider
# unconditionally here is safe even in Mock mode / with the feature flag
# off: constructing it does no I/O (it opens the local NlscCodeCache
# sqlite file RUNTIME_READ_ONLY, same as every other Real*Provider's
# __init__), and query_parcel_coordinate() itself is only ever invoked
# below when DATA_PROVIDER_MODE == "real" (see
# _resolve_coordinate_evidence_bundle) -- this mirrors how RealRoadProvider
# is imported unconditionally but only exercised in real mode.
from official_parcel_coordinate_provider import (  # noqa: E402
    RealOfficialParcelCoordinateProvider, ParcelCoordinateStatus, to_target_coordinate_evidence,
)

_ALWAYS_MOCK = []

_MODE_PROVIDERS = {
    "mock": [
        MockLandUseProvider, MockRoadProvider,
        MockTransportationProvider, MockPublicFacilityProvider,
        MockSpecialFacilityProvider, MockEnvironmentalProvider, MockCommercialActivityProvider,
        MockExpropriationCaseProvider, MockLandPriceProvider, MockOfficialFacilityProvider,
    ],
    "real": [
        # RealRoadProvider() with no injected evidence_sources -- no
        # confirmed reliable real road-width source exists yet (see
        # providers/road_provider.py's module docstring), so this
        # genuinely returns UNKNOWN for main_road_width, never Golden
        # Case's 18m. Still registered here (not _ALWAYS_MOCK) because
        # "real path must never fall back to Mock" applies to this
        # provider too, once RealRoadProvider exists at all.
        RealLandUseProvider, RealRoadProvider,
        RealTransportationProvider, RealPublicFacilityProvider,
        RealSpecialFacilityProvider, RealEnvironmentalProvider, RealCommercialActivityProvider,
        RealExpropriationCaseProvider, RealLandPriceProvider, RealOfficialFacilityProvider,
    ],
}


def _resolve_data_provider_mode() -> str:
    """Distinguishes "unset" (a deliberate development default) from "set
    to an invalid value" (a configuration error that must fail loudly, not
    silently degrade to Mock -- see module docstring). os.environ.get's own
    default arg cannot make this distinction since it returns the same
    thing for both an absent key and one explicitly set to that default."""
    raw = os.environ.get("DATA_PROVIDER_MODE")
    if raw is None:
        return "mock"
    mode = raw.strip().lower()
    if mode not in _MODE_PROVIDERS:
        raise RuntimeError(
            f"INVALID_DATA_PROVIDER_MODE: DATA_PROVIDER_MODE={raw!r} is not one of "
            f"{sorted(_MODE_PROVIDERS)}. The real data path must never silently fall back "
            "to Mock -- fix the environment variable rather than relying on a default."
        )
    return mode


DATA_PROVIDER_MODE = _resolve_data_provider_mode()
ALL_PROVIDERS = _ALWAYS_MOCK + _MODE_PROVIDERS[DATA_PROVIDER_MODE]


def _resolve_submitted_coordinate_evidence(body: dict) -> "TargetCoordinateEvidence | None":
    """Phase API-2.3F §3: SUBMITTED_COORDINATE_EVIDENCE -- an explicit
    coordinate the frontend/caller already supplied (`body["center_
    coordinate"]`), tagged SUBMITTED_BY_CALLER. This server has NO way to
    verify what THAT coordinate's own origin was (the shipped frontend
    never actually sends this field today -- see docs/phase9/facility_
    evidence_pipeline.md §2 -- so this path is reachable only via a direct
    API call, not the shipped UI), so its authoritative_status is
    EXTERNAL_UNVERIFIED, never OFFICIAL, absent some future contract that
    lets a caller assert its own provenance.

    Deliberately independent of, and never blocking, official-coordinate
    resolution (Phase API-2.3F's explicit correction of Phase API-2.3H's
    "submitted coordinate存在→完全不查NLSC" design -- inappropriate for a
    review system, which should surface a submitted/official discrepancy
    rather than hide it by skipping the official lookup whenever a
    submitted value exists -- see _resolve_coordinate_evidence_bundle)."""
    raw = body.get("center_coordinate")
    if not (raw and raw.get("latitude") is not None and raw.get("longitude") is not None):
        return None
    return TargetCoordinateEvidence(
        latitude=raw["latitude"], longitude=raw["longitude"],
        source_type=CoordinateSourceType.SUBMITTED_BY_CALLER,
        source_authority=None, source_url=None,
        authoritative_status=CoordinateAuthoritativeStatus.EXTERNAL_UNVERIFIED,
        precision_level="UNKNOWN",
        retrieved_at=datetime.now(),
        notes=(
            "呼叫端提交之座標，伺服器無法驗證其原始來源是否官方；"
            "目前線上前端從未實際送出此欄位（見docs/phase9/facility_evidence_pipeline.md §2），"
            "此為理論上可達但非實際production路徑。與OFFICIAL_PARCEL_COORDINATE_EVIDENCE"
            "為兩份獨立evidence，彼此不互相覆蓋。"
        ),
    )


def _resolve_nlsc_official_coordinate_evidence(
    case_no: str, meta: dict, segment=None,
) -> "TargetCoordinateEvidence | None":
    """Phase API-2.3F §3: OFFICIAL_PARCEL_COORDINATE_EVIDENCE -- ALWAYS
    attempted in real mode (see _resolve_coordinate_evidence_bundle),
    regardless of whether a submitted coordinate also exists for this
    case (Phase API-2.3H's "submitted blocks official lookup" behavior is
    corrected this round: SUBMITTED_BLOCKS_OFFICIAL_LOOKUP = NO). Returns
    a TargetCoordinateEvidence ONLY on NLSC CAD_001 SUCCESS (OFFICIAL,
    parcel-precise); returns None for every other status (AUTH_REQUIRED,
    AUTH_CONTRACT_UNVERIFIED, OUT_OF_COVERAGE, SERVICE_UNAVAILABLE,
    UNVERIFIED_RESPONSE, EMPTY_RESPONSE, PARSE_FAILED, INVALID_REQUEST,
    UNKNOWN) -- NEVER promotes an unofficial fallback to look official,
    and NEVER raises (RealOfficialParcelCoordinateProvider's own contract
    already guarantees every branch returns a status rather than
    throwing).

    Feature-flag/auth-contract/credential gated inside
    RealOfficialParcelCoordinateProvider itself (NLSC_CAD_API_ENABLED and
    CAD001_AUTH_CONTRACT_STATUS both default closed) -- so with this
    round's actual environment (no real NLSC credential, auth contract
    still UNCONFIRMED), this function always returns None regardless of
    the ctx it queries with.

    COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1 Task 3/5: `segment`
    is an EXPLICIT, OPTIONAL CompetitionSegment. None (every legacy caller)
    -> byte-identical to before (case-level meta's own district/segment_
    code/base_parcel_id). A real segment -> THIS segment's own district
    and its own parcel_ids[0] (never the case-level base_parcel_id) feed
    the CAD_001 query's ctx, so P001-00/P002-00/P003-00/P004-00 each query
    NLSC under their OWN parcel identity, never all sharing the base
    parcel's -- see NLSC_REQUEST_USES_SEGMENT_IDENTITY in this round's
    docs/tests (an architecture proof via a monkeypatched provider, not a
    live NLSC call -- NLSC_CAD_API_ENABLED stays closed)."""
    provider = RealOfficialParcelCoordinateProvider()
    district = segment.district if segment else meta["district"]
    segment_code = segment.segment_code if segment else meta["segment_code"]
    parcel_id = (segment.parcel_ids[0] if segment and segment.parcel_ids else meta.get("base_parcel_id"))
    ctx = ProviderContext(
        case_no=case_no, city=meta.get("city", ""), district=district,
        segment_code=segment_code, parcel_id=parcel_id,
    )
    evidence = provider.query_parcel_coordinate(ctx)
    if evidence.status != ParcelCoordinateStatus.SUCCESS:
        return None
    return to_target_coordinate_evidence(evidence)


def _resolve_nominatim_coordinate_evidence(meta: dict, segment=None) -> "TargetCoordinateEvidence | None":
    """REFERENCE_ONLY geocode fallback -- tagged NOMINATIM_EXTERNAL,
    authoritative_status EXTERNAL_UNVERIFIED (this round's explicit
    prohibition, §4: "禁止 Nominatim -> OFFICIAL"), precision_level
    DISTRICT (a city+district+segment-name text geocode is never
    parcel-precise). Real mode only; only ever consulted by
    _resolve_coordinate_evidence_bundle when NEITHER an official NLSC
    coordinate NOR a submitted coordinate is available, so a case that
    already has either never triggers this network call.

    `segment` (Task 3): EXPLICIT, OPTIONAL. None -> case-level meta's own
    district/segment_scope, unchanged. A real segment -> its OWN district
    replaces the case-level one in the geocoded place string (segment_
    scope has no per-segment equivalent in CompetitionSegment yet, so that
    part of the string still comes from meta -- a documented, narrow known
    limitation, not a silent regression)."""
    district = segment.district if segment else meta.get("district", "")
    place = " ".join(filter(None, [meta.get("city", ""), district, meta.get("segment_scope", "")]))
    if not place.strip():
        return None
    coord = geocode(place)
    if coord is None or coord.latitude is None or coord.longitude is None:
        return None
    return TargetCoordinateEvidence(
        latitude=coord.latitude, longitude=coord.longitude,
        source_type=CoordinateSourceType.NOMINATIM_EXTERNAL,
        source_authority="OpenStreetMap Nominatim", source_url="https://nominatim.openstreetmap.org/",
        authoritative_status=CoordinateAuthoritativeStatus.EXTERNAL_UNVERIFIED,
        precision_level="DISTRICT",
        retrieved_at=datetime.now(),
        notes=(
            f"REFERENCE_ONLY：對文字地名「{place}」之geocode結果，非地號層級官方座標，"
            "僅達區/段層級精細度，僅供參考，絕不得視為OFFICIAL。"
        ),
    )


def _select_analysis_coordinate(
    submitted: "TargetCoordinateEvidence | None", official: "TargetCoordinateEvidence | None",
    nominatim: "TargetCoordinateEvidence | None",
) -> "TargetCoordinateEvidence | None":
    """Phase API-2.3F §4 Analysis Coordinate Selection -- the ONE evidence
    that actually feeds `ProviderContext.center_coordinate`/
    `center_coordinate_evidence` (and therefore OfficialFacilityProvider's
    distance calculations), chosen independently of which evidences were
    separately preserved (§3):

    1. Official NLSC SUCCESS -> OFFICIAL_PARCEL_REPRESENTATIVE_POINT
       (highest priority: an OFFICIAL, parcel-precise government
       coordinate outranks a caller-submitted or geocoded one for the
       purpose of downstream analysis, even though the submitted value is
       still independently preserved and compared -- see
       _compute_coordinate_comparison).
    2. Official unavailable, submitted present -> SUBMITTED_COORDINATE.
    3. Neither available -> Nominatim (REFERENCE_ONLY/EXTERNAL_UNVERIFIED)
       or None. Nominatim -> OFFICIAL is never possible: `official` here
       is always either None or an evidence already tagged OFFICIAL by
       _resolve_nlsc_official_coordinate_evidence, never Nominatim's own
       EXTERNAL_UNVERIFIED evidence promoted upward."""
    if official is not None:
        return official
    if submitted is not None:
        return submitted
    return nominatim


# A tiny numerical-identity tolerance for GPS/CRS rounding noise -- NOT a
# legal/valuation materiality threshold (this round's explicit "本輪不要
# 自行建立法律threshold" instruction). See CoordinateComparisonStatus's
# docstring in domain/models.py.
COORDINATE_IDENTITY_EPSILON_M = 1.0


def _compute_coordinate_comparison(
    submitted: "TargetCoordinateEvidence | None", official: "TargetCoordinateEvidence | None",
) -> "CoordinateComparisonEvidence | None":
    """Phase API-2.3F §5: produced ONLY when BOTH a submitted AND an
    official coordinate exist for this case -- preserves both raw lat/lon
    values plus a deterministic coordinate_delta_m (Haversine, via the
    FROZEN engine/geo_distance_engine.py, reused unmodified) and a purely
    descriptive MATCH/DIFFERENT/MANUAL_REVIEW_REQUIRED status. This
    function NEVER decides which coordinate is "correct" for the
    appraisal -- that judgment is explicitly out of scope this round."""
    if submitted is None or official is None:
        return None
    now = datetime.now()
    if submitted.latitude is None or submitted.longitude is None or \
            official.latitude is None or official.longitude is None:
        return CoordinateComparisonEvidence(
            submitted_latitude=submitted.latitude, submitted_longitude=submitted.longitude,
            official_latitude=official.latitude, official_longitude=official.longitude,
            coordinate_delta_m=None, status=CoordinateComparisonStatus.MANUAL_REVIEW_REQUIRED,
            requires_manual_review=True,
            notes="submitted/official任一座標缺少緯度或經度，無法計算距離差，需人工確認。",
            computed_at=now,
        )

    result = GeoDistanceEngine().straight_line_distance(
        Coordinate(latitude=submitted.latitude, longitude=submitted.longitude), "呼叫端提交座標",
        Coordinate(latitude=official.latitude, longitude=official.longitude), "NLSC官方宗地代表點",
        source="GeoDistanceEngine（Haversine great-circle公式，WGS84）",
    )
    delta_m = result.distance_m
    if delta_m is None:
        status = CoordinateComparisonStatus.MANUAL_REVIEW_REQUIRED
        notes = f"GeoDistanceEngine無法計算距離：{result.notes}"
    elif delta_m <= COORDINATE_IDENTITY_EPSILON_M:
        status = CoordinateComparisonStatus.MATCH
        notes = f"submitted與official座標相距{delta_m}公尺，於數值誤差容許範圍內視為同一點（非估價重大性門檻判斷）。"
    else:
        status = CoordinateComparisonStatus.DIFFERENT
        notes = (
            f"submitted與official座標相距{delta_m}公尺，兩者為不同地理位置"
            "（純幾何事實，非何者正確之判斷，亦非本專案自行認定之估價重大性門檻）。"
        )
    return CoordinateComparisonEvidence(
        submitted_latitude=submitted.latitude, submitted_longitude=submitted.longitude,
        official_latitude=official.latitude, official_longitude=official.longitude,
        coordinate_delta_m=delta_m, status=status, requires_manual_review=True,
        notes=notes, computed_at=now,
    )


def _resolve_coordinate_evidence_bundle(case_no: str, meta: dict, body: dict, segment=None) -> dict:
    """Phase API-2.3F §3/§4/§5 orchestrator -- the single place all
    coordinate-related evidence is assembled. Returns:
    {
        "submitted": TargetCoordinateEvidence | None,
        "official_parcel": TargetCoordinateEvidence | None,  # SUCCESS only
        "nominatim_reference": TargetCoordinateEvidence | None,
        "analysis_coordinate": TargetCoordinateEvidence | None,  # feeds ctx
        "comparison": CoordinateComparisonEvidence | None,
    }
    Mock mode: `submitted` may still be populated (reading body needs no
    network/DB), but `official_parcel`/`nominatim_reference` are always
    None (Mock mode never touches NLSC or Nominatim -- unchanged
    invariant from every prior round).

    `segment` (COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1 Task 3):
    EXPLICIT, OPTIONAL CompetitionSegment, threaded through to both the
    NLSC and Nominatim resolvers below. `submitted` itself needs no
    threading at all -- it already reads only THIS call's own `body`,
    so P001-00/P002-00/P003-00/P004-00 each submitting a different
    center_coordinate in their own collect_data() call already produces
    a genuinely different `submitted` evidence per segment, with no
    change needed here."""
    submitted = _resolve_submitted_coordinate_evidence(body)

    official = None
    if DATA_PROVIDER_MODE == "real":
        official = _resolve_nlsc_official_coordinate_evidence(case_no, meta, segment=segment)

    nominatim = None
    if DATA_PROVIDER_MODE == "real" and official is None and submitted is None:
        nominatim = _resolve_nominatim_coordinate_evidence(meta, segment=segment)

    analysis_coordinate = _select_analysis_coordinate(submitted, official, nominatim)
    comparison = _compute_coordinate_comparison(submitted, official)

    return {
        "submitted": submitted, "official_parcel": official, "nominatim_reference": nominatim,
        "analysis_coordinate": analysis_coordinate, "comparison": comparison,
    }


def _resolve_center_coordinate_evidence(
    case_no: str, meta: dict, body: dict, segment=None,
) -> "TargetCoordinateEvidence | None":
    """Thin backward-compatible accessor: the single ANALYSIS coordinate
    evidence (see _select_analysis_coordinate) that feeds `ProviderContext.
    center_coordinate`/`center_coordinate_evidence`. Callers needing the
    full set of independently-preserved evidences (submitted/official/
    nominatim/comparison) should call _resolve_coordinate_evidence_bundle
    directly instead (see collect_data() below, which does exactly that)."""
    return _resolve_coordinate_evidence_bundle(case_no, meta, body, segment=segment)["analysis_coordinate"]


def _resolve_center_coordinate(case_no: str, meta: dict, body: dict, segment=None) -> "Coordinate | None":
    """Backward-compatible Coordinate-only accessor, now implemented in
    terms of `_resolve_center_coordinate_evidence()` so both stay in sync
    (same resolution order, same single source of truth) rather than
    re-implementing the branching twice."""
    evidence = _resolve_center_coordinate_evidence(case_no, meta, body, segment=segment)
    if evidence is None:
        return None
    return Coordinate(latitude=evidence.latitude, longitude=evidence.longitude)


# land_use_provider's NormalizedDataPoint.field names -> the regional-
# factor field_id/factor-label convention AuditEngine/case_reconstruction.py
# already expect (data/golden/golden_case_input.py's regional_land_use_
# zone/regional_building_coverage_ratio/regional_floor_area_ratio -- see
# AuditEngine._official_regional_raw_value). building_coverage_ratio/
# floor_area_ratio are persisted here too (Provider already resolves them),
# purely as independently-sourced REFERENCE evidence -- AuditEngine's
# LandUseRatioValidator re-derives its own reference from
# regional_land_use_zone rather than trusting these two numbers directly,
# and nothing here ever touches SubmittedFormData.submitted_building_
# coverage_rate/submitted_floor_area_ratio (see case_reconstruction.
# extract_submitted_land_use_ratio_fields, which reads ONLY from
# user_submitted_factors/case.base_parcel_factors -- a completely separate
# path from this Provider-sourced one).
_REGIONAL_LAND_USE_FIELD_MAP = {
    "land_use_zone": ("regional_land_use_zone", "使用分區(使用地類別)"),
    "building_coverage_ratio": ("regional_building_coverage_ratio", "建蔽率"),
    "floor_area_ratio": ("regional_floor_area_ratio", "容積率"),
}


def _to_regional_factor(point: dict) -> "dict | None":
    """Maps one land_use_provider NormalizedDataPoint (already JSON-shaped
    via model_dump(mode='json')) into the regional_base_factors FactorInput-
    dict shape case_reconstruction.py's to_factor_inputs() expects, carrying
    the point's own Evidence (source/source_type/confidence/retrieved_at/
    notes -- source_url/source_agency/dataset_version/legal_status are
    already composed into source/notes by RealLandUseProvider itself, the
    existing convention for fields Evidence has no first-class slot for;
    see providers/land_use_provider.py) forward instead of discarding it.
    Returns None for a field this mapping doesn't cover, or an UNKNOWN
    point (value=None) -- an unresolved factor must not appear as a
    fabricated FactorInput; AuditEngine already treats an absent
    regional_land_use_zone as official_raw_zone_name=None (honest
    MANUAL_REVIEW_REQUIRED-style outcome), never a guess."""
    mapping = _REGIONAL_LAND_USE_FIELD_MAP.get(point.get("field"))
    if mapping is None or point.get("value") is None:
        return None
    field_id, factor = mapping
    return {
        "field_id": field_id, "factor": factor, "raw_value": point["value"], "unit": point.get("unit"),
        "evidence": {
            "source": point.get("source"), "source_type": point.get("source_type"),
            "confidence": point.get("confidence"), "retrieved_at": point.get("retrieved_at"),
            "notes": point.get("notes"),
        },
    }


def _resolve_plan_id(body: dict) -> "str | None":
    """Reads ONLY the caller's explicit override of plan_id
    (data/rules/plan_zone_floor_area_ratios.json's internal identifier,
    e.g. "jinshan") from the request body -- never derived from
    center_coordinate, district, or segment_scope inside this function.

    Update: automatic coordinate->都市計畫 resolution, once investigated and
    found infeasible (the "新北市都市計畫範圍" dataset's Name/LblName fields
    are corrupted at the source -- see providers/base.py's ProviderContext
    docstring), is now possible via a separate manually-derived key/SDF_ID
    fix-up table (providers/urban_plan_boundary_provider.py,
    data/rules/urban_plan_id_registry.json). See `_resolve_urban_plan_
    result` below and its call site in `collect_data()`: a human-supplied
    plan_id from THIS function always takes precedence over that
    auto-resolution when both are present."""
    plan_id = body.get("plan_id")
    return plan_id.strip() if isinstance(plan_id, str) and plan_id.strip() else None


def _resolve_urban_plan_result(center_coordinate: "Coordinate | None"):
    """Auto-derives which 新北市都市計畫 (if any) `center_coordinate` falls
    in, via providers/urban_plan_boundary_provider.py's point-in-polygon
    query -- so plan_id need not always be typed by hand. Only attempted in
    real mode with a coordinate present (Mock mode's Golden Case has no
    coordinate at all, and there is nothing to query without one). Never
    raises -- RealUrbanPlanBoundaryProvider.resolve_urban_plan() itself
    degrades to an honest UNKNOWN/PLAN_MAPPING_UNAVAILABLE/AMBIGUOUS result
    rather than throwing, matching every other Real provider's contract."""
    if DATA_PROVIDER_MODE != "real" or center_coordinate is None:
        return None
    return RealUrbanPlanBoundaryProvider().resolve_urban_plan(center_coordinate)


def _resolve_road_width_evidence(ctx: ProviderContext) -> list:
    """RoadWidthEvidence (Phase 8's Road Width Multi-Evidence Model) is
    richer than a NormalizedDataPoint (evidence_type/dataset_id/
    legal_status/derivation_method as first-class fields -- see
    domain.models.RoadWidthEvidence) and only RealRoadProvider produces
    it, via its dedicated resolve_main_road_width() method, not the
    generic fetch() loop above. Only meaningful in real mode: Mock mode
    has no evidence concept at all (MockRoadProvider stays a simple fixed
    18m/12m fixture, deliberately never conflated with this model -- see
    providers/road_provider.py's module docstring and item 6 of this
    round's instructions: Golden Case's 18m must never be presented as an
    official reference in real-mode backend E2E). Every entry considered
    is persisted (even on CONFLICT/UNAVAILABLE) so a human reviewer can
    see exactly what was weighed, not just the final resolved number."""
    if DATA_PROVIDER_MODE != "real":
        return []
    for ProviderCls in ALL_PROVIDERS:
        if ProviderCls is RealRoadProvider:
            resolution = ProviderCls().resolve_main_road_width(ctx)
            return [e.model_dump(mode="json") for e in resolution.evidence]
    return []


def _resolve_expropriation_case_evidence(ctx: ProviderContext) -> "dict | None":
    """ExpropriationCaseEvidence (Phase API-1) is richer than a
    NormalizedDataPoint (dataset_id/source_authority/query_parameters/
    authoritative_status as first-class fields), same rationale as
    _resolve_road_width_evidence above -- persisted separately so a human
    reviewer sees the full provenance, not just the collapsed FOUND/
    NOT_FOUND/UNKNOWN string already sitting in FACTORS.points. Only
    meaningful in real mode; Mock mode has no evidence concept (see
    providers/expropriation_case_provider.py's module docstring)."""
    if DATA_PROVIDER_MODE != "real":
        return None
    for ProviderCls in ALL_PROVIDERS:
        if ProviderCls is RealExpropriationCaseProvider:
            return ProviderCls().query_case(ctx).model_dump(mode="json")
    return None


def _resolve_land_price_evidence(ctx: ProviderContext) -> "dict | None":
    """LandPriceEvidence (Phase API-1) -- same rationale as the two helpers
    above. Only meaningful in real mode."""
    if DATA_PROVIDER_MODE != "real":
        return None
    for ProviderCls in ALL_PROVIDERS:
        if ProviderCls is RealLandPriceProvider:
            return ProviderCls().query_land_price(ctx).model_dump(mode="json")
    return None


def _resolve_official_facility_evidence(ctx: ProviderContext) -> "dict | None":
    """FacilityEvidence per FacilityType (Phase API-2) -- same rationale as
    the three helpers above. Returns a dict keyed by FacilityType.value
    (e.g. {"SCHOOL": {...}, "STATION": {...}, ...}), one full FacilityEvidence
    (matches[]/nearest/provenance) per type, not just the match_count already
    in FACTORS.points. Only meaningful in real mode."""
    if DATA_PROVIDER_MODE != "real":
        return None
    for ProviderCls in ALL_PROVIDERS:
        if ProviderCls is RealOfficialFacilityProvider:
            provider = ProviderCls()
            return {ft.value: provider.query_facility(ctx, ft).model_dump(mode="json") for ft in FacilityType}
    return None


def collect_data(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    # COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 3/12/16: EXPLICIT, OPTIONAL
    # path parameter. Omitted (every existing legacy Jinshan caller, and
    # every frontend that has not yet been updated -- Task 16) -> byte-
    # identical to pre-B1 behavior: meta's own case-level district/
    # segment_code and the bare "FACTORS" SK, exactly as before. Supplied
    # -> this ONE segment's own district (from its CompetitionSegmentMap
    # entry, never the case-level meta) feeds ProviderContext, and FACTORS
    # is stored under its OWN "FACTORS#<segment_code>" item -- so P001-00/
    # P002-00/P003-00/P004-00 can never overwrite each other (Task 10).
    segment_code = event.get("pathParameters", {}).get("segment_code")
    with timed_step(case_no, "collect_data"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        segment = None
        if segment_code:
            try:
                segment = competition_segments.resolve_segment(case_no, segment_code)
            except InvalidSegmentCodeError as e:
                return error_response(400, "INVALID_SEGMENT_CODE", str(e))
        effective_district = segment.district if segment else meta["district"]
        effective_segment_code = segment.segment_code if segment else meta["segment_code"]
        # Task 3/5: this segment's OWN parcel identity (never the case-
        # level base_parcel_id) once a real segment is given -- feeds both
        # the main ProviderContext below AND the NLSC/Nominatim resolvers
        # (via _resolve_coordinate_evidence_bundle(segment=segment)).
        effective_parcel_id = (
            segment.parcel_ids[0] if segment and segment.parcel_ids else meta.get("base_parcel_id")
        )
        factors_sk = competition_segments.factors_sk(segment_code)

        body = parse_body(event)
        if isinstance(body, dict) and body.get("acquisition_strategy") == "public":
            from providers.public_data_collector import PublicDataCollector
            from providers.public_http import PublicHttpClient
            import tempfile
            previous = case_store.get_record(case_no, factors_sk) or {}
            request = {**meta, **body, "case_no": case_no,
                       "district": effective_district, "segment_code": effective_segment_code,
                       "base_parcel_id": effective_parcel_id}
            request["competition_provided_factors"] = body.get(
                "competition_provided_factors", previous.get("competition_provided_factors", []),
            )
            try:
                result = PublicDataCollector(PublicHttpClient(
                    cache_dir=os.path.join(tempfile.gettempdir(), "valuation-public-data"))).collect(request)
            except (ValueError, TypeError) as exc:
                return error_response(400, "VALIDATION_ERROR", str(exc))
            # Preserve prior user/fixed/transaction evidence. Reference-only spatial
            # estimates are exported as draft fields, never promoted to grades.
            record = {**previous, "segment_code": segment_code, "public_data_draft": result,
                      "points": result["points"]}
            for key in ("competition_provided_factors", "competition_provided_individual_factors",
                        "competition_provided_transaction", "user_submitted_factors"):
                if key in body:
                    record[key] = body[key]
            case_store.put_record(case_no, factors_sk, record)
            return response(200, result)
        # Phase API-2.3F §3: submitted/official/nominatim are three
        # INDEPENDENT evidences (none overwrites another) computed once
        # here and persisted in full below; `analysis_coordinate` is the
        # single one of them that actually feeds ctx.center_coordinate.
        coordinate_bundle = _resolve_coordinate_evidence_bundle(case_no, meta, body, segment=segment)
        center_coordinate_evidence = coordinate_bundle["analysis_coordinate"]
        analysis_coordinate = (
            Coordinate(latitude=center_coordinate_evidence.latitude, longitude=center_coordinate_evidence.longitude)
            if center_coordinate_evidence else None
        )

        # Must run BEFORE RealUrbanPlanBoundaryProvider/RealNtpcZoningProvider
        # are ever queried below (via _resolve_urban_plan_result and the
        # ALL_PROVIDERS loop) -- mirrors cadastral_snapshot_bootstrap's own
        # call site pattern above. No-op in Mock mode or outside Lambda; a
        # partial/total bootstrap failure here simply means those two
        # providers find no registered snapshot and take their own already-
        # established UNKNOWN degradation path, exactly as if neither
        # dataset had ever been synced.
        if DATA_PROVIDER_MODE == "real":
            gis_snapshot_bootstrap.ensure_gis_snapshots()

        # Urban-plan-boundary auto-resolution (see _resolve_urban_plan_result
        # and _resolve_plan_id's docstrings): a human-supplied plan_id always
        # wins; auto-resolution only fills the gap when the caller didn't
        # supply one AND the coordinate resolves unambiguously to exactly
        # one 都市計畫 (urban_plan_status == INSIDE). AMBIGUOUS/OUTSIDE/
        # UNKNOWN/PLAN_MAPPING_UNAVAILABLE never produce a plan_id -- never
        # a guess, per this round's "查不到時必須UNKNOWN/MANUAL_REVIEW_
        # REQUIRED，不得猜" instruction.
        human_plan_id = _resolve_plan_id(body)
        urban_plan_result = _resolve_urban_plan_result(analysis_coordinate)
        auto_plan_id = (
            urban_plan_result.plan_id
            if urban_plan_result and urban_plan_result.urban_plan_status == UrbanPlanStatus.INSIDE
            else None
        )
        effective_plan_id = human_plan_id or auto_plan_id
        # A human-supplied plan_id still always WINS as the value actually
        # used for calculation (see above) -- but if it disagrees with what
        # the submitted coordinate itself resolves to, that discrepancy must
        # stay visible in plan_identification below, never be silently
        # swallowed by the override. Same "never silently pick one" principle
        # already applied to submitted-vs-official coordinates (see
        # _compute_coordinate_comparison).
        plan_id_source_mismatch = bool(
            human_plan_id and auto_plan_id and human_plan_id != auto_plan_id
        )

        ctx = ProviderContext(
            case_no=case_no, city=meta.get("city", ""), district=effective_district,
            segment_code=effective_segment_code,
            # parcel_id (比準地地號/land_no) -- previously never passed here
            # even though ProviderContext has carried this slot since Phase
            # 5 (see providers/base.py); ExpropriationCaseProvider/
            # LandPriceProvider (Phase API-1) are the first real-mode
            # providers that actually need it. meta["base_parcel_id"]
            # defaults to the literal string "TBD" at case creation
            # (backend/handlers/cases.py) when not supplied -- both new
            # providers treat "TBD" the same as missing, never as a real
            # land number to query with. A real `segment` (Task 3/5) uses
            # THAT segment's own parcel_ids[0] instead -- never the case-
            # level base_parcel_id once segment-scoped.
            parcel_id=effective_parcel_id,
            center_coordinate=analysis_coordinate,
            plan_id=effective_plan_id,
            # Phase API-2.1: carries WHERE center_coordinate came from
            # (SUBMITTED_BY_CALLER/NOMINATIM_EXTERNAL/None), so
            # OfficialFacilityProvider can tell an official coordinate apart
            # from an external/unverified one instead of treating "not
            # None" as proof of official provenance -- see domain.models.
            # TargetCoordinateEvidence's docstring.
            center_coordinate_evidence=center_coordinate_evidence,
        )

        # Phase DEPLOY-1E: must run BEFORE any provider in ALL_PROVIDERS
        # is constructed below -- RealExpropriationCaseProvider/
        # RealLandPriceProvider's __init__ each construct a
        # CadastralDatasetCache() (frozen, unmodified) that immediately
        # opens/creates whatever file sits at CADASTRAL_DATASET_CACHE_
        # DB_PATH, so the verified snapshot must already be in place (or
        # definitively absent) by the time that happens. Mock mode never
        # touches the cadastral cache at all, so this is a no-op there.
        # Never raises, never blocks the rest of this handler on failure
        # -- a DATASET_UNAVAILABLE result here simply means the frozen
        # providers below will find no file (or no synced snapshot_meta
        # row) and take their own already-established UNKNOWN/requires_
        # manual_review degradation path, exactly as if this dataset had
        # never been synced at all.
        if DATA_PROVIDER_MODE == "real":
            cadastral_snapshot_bootstrap.ensure_cadastral_snapshot()

        all_points = []
        for ProviderCls in ALL_PROVIDERS:
            all_points.extend(p.model_dump(mode="json") for p in ProviderCls().fetch(ctx))

        missing = [p["field"] for p in all_points if p["value"] is None]
        collected = len(all_points) - len(missing)

        # regional_base_factors -- previously never written by this handler
        # at all (case_reconstruction.py always read an empty [], see
        # docs/backlog.md's "collect_data.py 未持久化 regional_base_factors"
        # entry). land_use_provider's own regional-scope points (zone/BCR/
        # FAR, whichever mode -- Mock or Real -- is active) are mapped and
        # persisted here so official_raw_zone_name actually flows through to
        # AuditEngine instead of always resolving to None.
        regional_base_factors = [
            rf for rf in (_to_regional_factor(p) for p in all_points) if rf is not None
        ]

        # road_width_evidence: NOT a bare "main_road_width = 18" number --
        # the full list of independently-sourced RoadWidthEvidence entries
        # RealRoadProvider considered (possibly empty), so RoadWidthResolver
        # can re-run the same conflict/agreement logic downstream in
        # review.py instead of this handler collapsing it to one figure.
        road_width_evidence = _resolve_road_width_evidence(ctx)

        # Phase API-1 rich evidence (full provenance -- dataset_id/
        # source_authority/query_parameters/authoritative_status), stored
        # alongside but separate from FACTORS.points' collapsed
        # NormalizedDataPoint view of the same query. External
        # corroborating evidence only -- never read by RuleEngine/
        # CalculationEngine/AuditEngine this round.
        expropriation_case_evidence = _resolve_expropriation_case_evidence(ctx)
        land_price_evidence = _resolve_land_price_evidence(ctx)
        official_facility_evidence = _resolve_official_facility_evidence(ctx)

        case_store.put_record(case_no, factors_sk, {
            # COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 11: explicit on the
            # record itself (None for every legacy/non-segmented case), so
            # a reader of this ONE FACTORS/FACTORS#<segment_code> item can
            # always tell which segment (if any) it belongs to without
            # having to infer it from the SK string.
            "segment_code": segment_code,
            "points": all_points,
            "user_submitted_factors": {
                "base_parcel_factors": body.get("base_parcel_factors", []),
                "comparable_factors": body.get("comparable_factors", {}),
            },
            # COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 4/11: the 表3 地價
            # 區段勘查表 facts 題目.pdf already fixed for THIS segment (e.g.
            # P001-00's 建蔽率=50%/容積率=260%/主要道路寬度=28M) -- an
            # explicit, separate bucket from `regional_base_factors` below
            # (which is exclusively Provider-derived). A caller is expected
            # to tag each entry's evidence.source_type as
            # COMPETITION_PROVIDED_FIXED; this handler never overwrites or
            # merges this bucket with anything Provider-sourced -- a
            # differing Provider point for the same field_id simply lands
            # in regional_base_factors/points as ordinary REFERENCE
            # evidence, side by side, never replacing this one.
            "competition_provided_factors": body.get("competition_provided_factors", []),
            # TABLE4-THREE-COMPARABLE-D1 Task 2/10: the INDIVIDUAL-factor
            # (表4 個別因素) counterpart of the bucket above -- same
            # precedence discipline (case_reconstruction.py::
            # apply_competition_provided_individual_precedence()), applied
            # on top of user_submitted_factors.base_parcel_factors /
            # comparable_factors instead of regional_base_factors.
            "competition_provided_individual_factors": body.get("competition_provided_individual_factors", []),
            # TABLE4-THREE-COMPARABLE-D1 Task 2: this segment's own 表4
            # "0基本資料" transaction block (交易日期/土地正常單價/調整百分率/
            # 調整至估價基準日單價), if this segment IS a comparable with a
            # real transaction (never present for a base/比準地 segment,
            # which has no transaction of its own) -- COMPETITION_PROVIDED_
            # FIXED, read verbatim from 題目.pdf, never recomputed here.
            "competition_provided_transaction": body.get("competition_provided_transaction"),
            "regional_base_factors": regional_base_factors,
            "road_width_evidence": road_width_evidence,
            "expropriation_case_evidence": expropriation_case_evidence,
            "land_price_evidence": land_price_evidence,
            "official_facility_evidence": official_facility_evidence,
            # Phase API-2.3F §3/§5: submitted/official/nominatim persisted
            # as three INDEPENDENT evidences (never overwriting each
            # other), plus `analysis_coordinate` (the one actually fed
            # into ctx.center_coordinate above) and `comparison` (only
            # non-null when BOTH submitted and official exist -- see
            # _compute_coordinate_comparison; never a legal/valuation
            # threshold judgment, purely descriptive MATCH/DIFFERENT/
            # MANUAL_REVIEW_REQUIRED).
            "coordinate_evidence": {
                "submitted": coordinate_bundle["submitted"].model_dump(mode="json")
                if coordinate_bundle["submitted"] else None,
                "official_parcel": coordinate_bundle["official_parcel"].model_dump(mode="json")
                if coordinate_bundle["official_parcel"] else None,
                "nominatim_reference": coordinate_bundle["nominatim_reference"].model_dump(mode="json")
                if coordinate_bundle["nominatim_reference"] else None,
                "analysis_coordinate": coordinate_bundle["analysis_coordinate"].model_dump(mode="json")
                if coordinate_bundle["analysis_coordinate"] else None,
                "comparison": coordinate_bundle["comparison"].model_dump(mode="json")
                if coordinate_bundle["comparison"] else None,
            },
            # submitted_main_road_width is what the appraiser wrote on 表1
            # (main_road_width has no "individual_" factor-list counterpart
            # in this data model -- 主要道路寬度 is graded as a REGIONAL
            # factor, not compared per-comparable the way individual
            # factors are), so it is read from its own top-level request-
            # body key, the same convention plan_id/confirmed_plan_name
            # already use, rather than being force-fit into
            # base_parcel_factors.
            "road_width_submission": {
                "submitted_main_road_width": body.get("submitted_main_road_width"),
            },
            # ctx.plan_id is human-supplied (_resolve_plan_id) OR, when
            # absent, auto-resolved from the coordinate via
            # _resolve_urban_plan_result -- persisted here so review.py --
            # a later, separate request -- can read it back. Previously
            # computed into `ctx` and then silently dropped once this
            # handler returned. urban_plan_* fields surface the FULL
            # auto-resolution result (status/name/source/manual-review flag)
            # even when a human-supplied plan_id ultimately won, so a
            # reviewer can see what the coordinate alone would have implied.
            "plan_identification": {
                "internal_plan_id": ctx.plan_id,
                "confirmed_plan_name": body.get("confirmed_plan_name"),
                "plan_identification_source": (
                    "MANUAL_INPUT" if human_plan_id
                    else ("AUTO_COORDINATE_RESOLVED" if ctx.plan_id else None)
                ),
                "urban_plan_status": urban_plan_result.urban_plan_status.value if urban_plan_result else None,
                "urban_plan_name": urban_plan_result.plan_name if urban_plan_result else None,
                "urban_plan_id": urban_plan_result.plan_id if urban_plan_result else None,
                "urban_plan_source": urban_plan_result.source_dataset if urban_plan_result else None,
                "urban_plan_requires_manual_review": (
                    urban_plan_result.requires_manual_review if urban_plan_result else None
                ),
                # True when a human-supplied plan_id disagrees with what the
                # submitted coordinate itself auto-resolves to -- the human
                # value still wins for calculation (see effective_plan_id
                # above), but this flag makes that override visible for
                # audit rather than silently accepted.
                "plan_id_source_mismatch": plan_id_source_mismatch,
            },
        })

        return response(200, {
            "case_no": case_no, "segment_code": segment_code, "collected_field_count": collected,
            "missing_field_ids": missing,
            "status": "PARTIAL" if missing else "COMPLETE",
        })
