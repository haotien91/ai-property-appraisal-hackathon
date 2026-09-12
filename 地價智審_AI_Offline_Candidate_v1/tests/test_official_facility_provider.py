# -*- coding: utf-8 -*-
"""
Tests for providers/official_facility_provider.py (Phase API-2). All
network-free -- the PRIMARY (and only) real path reads a local, tmp_path-
scoped FacilityDatasetCache, never data.ntpc.gov.tw directly (mirrors
tests/test_expropriation_case_provider.py's pattern).
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from domain.models import (  # noqa: E402
    FacilityType, FacilityDistanceMethod, FacilityDistanceSemantics, FacilityCoordinateStatus, Coordinate,
    TargetCoordinateEvidence, CoordinateSourceType, CoordinateAuthoritativeStatus,
)
import official_facility_provider as ofp  # noqa: E402
from facility_dataset_cache import FacilityDatasetCache, SCOPE_ALL  # noqa: E402

DATASET_ID = "TEST-DS"


def _row(facility_type, name, district, lat=None, lon=None, coordinate_status="WGS84", facility_id=None,
         address=None):
    return {
        "facility_type": facility_type, "facility_id": facility_id or name, "district": district,
        "name": name, "address": address or f"{district}某路（{name}）", "latitude": lat, "longitude": lon,
        "coordinate_status": coordinate_status, "source_dataset_id": DATASET_ID, "source_authority": "測試機關",
    }


def _synced_cache(tmp_path, records, dataset_id=DATASET_ID):
    cache = FacilityDatasetCache(db_path=str(tmp_path / "cache.sqlite3"))
    cache.begin_staging_run(dataset_id)
    cache.stage_facility_records(dataset_id, SCOPE_ALL, records)
    cache.promote_facility_staging_to_current(
        dataset_id=dataset_id, dataset_name="測試資料集", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL],
    )
    return cache


def _provider_with_datasets(cache, dataset_id=DATASET_ID):
    """Monkeypatch-free way to point every FacilityType at the same test
    dataset_id, since the module hardcodes real production dataset IDs per
    type."""
    provider = ofp.RealOfficialFacilityProvider(cache=cache)
    return provider


def _patch_all_types_to(monkeypatch, dataset_id):
    for ft in FacilityType:
        monkeypatch.setitem(
            ofp._FACILITY_DATASET_INFO, ft, (dataset_id, "測試資料集", "u", "測試機關"),
        )


CTX_NO_COORD = ProviderContext(case_no="T", city="新北市", district="金山區", segment_code="P002-00", parcel_id="金美段489地號")
CTX_WITH_COORD = ProviderContext(
    case_no="T", city="新北市", district="金山區", segment_code="P002-00", parcel_id="金美段489地號",
    center_coordinate=Coordinate(latitude=25.22104, longitude=121.63547),  # near 市立金美國小, real coordinate
)


# -- 1/4: nearest lookup (school/station, coordinate-backed) --------------

def test_school_nearest_lookup(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [
        _row("SCHOOL", "近校", "金山區", 25.2211, 121.6356),   # ~90m from CTX_WITH_COORD
        _row("SCHOOL", "遠校", "金山區", 25.30, 121.70),        # far
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    provider = _provider_with_datasets(cache)
    evidence = provider.query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)

    assert evidence.match_count == 2
    assert evidence.nearest is not None
    assert evidence.nearest.name == "近校"
    assert evidence.distance_method == FacilityDistanceMethod.HAVERSINE_WGS84


def test_station_nearest_lookup(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [
        _row("STATION", "近站", "金山區", 25.2215, 121.6360),
        _row("STATION", "遠站", "金山區", 25.35, 121.75),
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    provider = _provider_with_datasets(cache)
    evidence = provider.query_facility(CTX_WITH_COORD, FacilityType.STATION)

    assert evidence.match_count == 2
    assert evidence.nearest.name == "近站"


# -- 2/3/5: market/park -- ADDRESS_ONLY, no coordinate dataset ------------

def test_market_nearest_lookup_address_only(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [
        _row("MARKET", "金山中繼公有市場", "金山區", coordinate_status="ADDRESS_ONLY"),
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    provider = _provider_with_datasets(cache)
    evidence = provider.query_facility(CTX_WITH_COORD, FacilityType.MARKET)

    assert evidence.match_count == 1
    assert evidence.nearest is None  # no computable distance
    assert evidence.matches[0].distance_m is None
    assert evidence.distance_method == FacilityDistanceMethod.UNKNOWN
    assert "ADDRESS_ONLY" in evidence.notes


def test_park_nearest_lookup_address_only(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("PARK", "某公園", "金山區", coordinate_status="ADDRESS_ONLY")])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    provider = _provider_with_datasets(cache)
    evidence = provider.query_facility(CTX_WITH_COORD, FacilityType.PARK)

    assert evidence.match_count == 1
    assert evidence.nearest is None
    assert all(m.distance_m is None for m in evidence.matches)


def test_no_coordinate_dataset_never_fabricates_distance(tmp_path, monkeypatch):
    """Direct proof against fabrication: even with a perfectly valid query
    coordinate, a facility whose SOURCE has no coordinate must never get a
    distance value."""
    cache = _synced_cache(tmp_path, [_row("MARKET", "M", "金山區", coordinate_status="ADDRESS_ONLY")])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.MARKET)
    assert evidence.matches[0].distance_m is None
    assert evidence.matches[0].coordinate_status == FacilityCoordinateStatus.ADDRESS_ONLY


# -- 6: unknown CRS ---------------------------------------------------------

def test_unknown_crs_never_computes_distance(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [
        _row("SCHOOL", "座標系不明學校", "金山區", lat=25.1, lon=121.1, coordinate_status="COORDINATE_CRS_UNKNOWN"),
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    assert evidence.match_count == 1
    assert evidence.matches[0].distance_m is None
    assert evidence.matches[0].coordinate_status == FacilityCoordinateStatus.CRS_UNKNOWN
    assert evidence.nearest is None


# -- 7: empty dataset -------------------------------------------------------

def test_empty_dataset_zero_matches(tmp_path, monkeypatch):
    """A synced-but-genuinely-empty type (STATION has 0 rows here) must
    read as a real "0 matches", not an error. NOTE: this is deliberately
    NOT "a school exists but in a different district" -- since Phase
    API-2.1 §6, SCHOOL/STATION are cross-district searches once a query
    coordinate is available, so a different-district record for a
    coordinate-bearing type is (correctly) now a MATCH, not an exclusion --
    see test_cross_district_nearest_search for that behavior."""
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "板橋區", 25.0, 121.5)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.STATION)
    assert evidence.match_count == 0
    assert evidence.matches == []
    assert evidence.nearest is None


# -- 8: target coordinate missing -> TARGET_COORDINATE_UNAVAILABLE --------

def test_target_coordinate_missing(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.2, 121.6)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_NO_COORD, FacilityType.SCHOOL)
    assert evidence.query_coordinate is None
    assert evidence.match_count == 1  # facility still listed
    assert evidence.matches[0].distance_m is None
    assert evidence.nearest is None
    assert "TARGET_COORDINATE_UNAVAILABLE" in evidence.notes


# -- 9: exact same coordinate -> distance=0 --------------------------------

def test_exact_same_coordinate_distance_zero(tmp_path, monkeypatch):
    same = Coordinate(latitude=25.22104, longitude=121.63547)
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "同座標校", "金山區", same.latitude, same.longitude)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = ProviderContext(case_no="T", city="新北市", district="金山區", segment_code="P002-00",
                           parcel_id="金美段489地號", center_coordinate=same)
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)
    assert evidence.nearest is not None
    assert evidence.nearest.distance_m == 0.0


# -- 10/11: deterministic distance + deterministic nearest selection ------

def test_deterministic_distance_repeatable(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.25, 121.65)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    provider = _provider_with_datasets(cache)
    d1 = provider.query_facility(CTX_WITH_COORD, FacilityType.SCHOOL).matches[0].distance_m
    d2 = provider.query_facility(CTX_WITH_COORD, FacilityType.SCHOOL).matches[0].distance_m
    assert d1 == d2


def test_nearest_selection_deterministic_regardless_of_lookup_order(tmp_path, monkeypatch):
    cache_a = FacilityDatasetCache(db_path=str(tmp_path / "a.sqlite3"))
    cache_a.begin_staging_run(DATASET_ID)
    cache_a.stage_facility_records(DATASET_ID, SCOPE_ALL, [
        _row("SCHOOL", "近", "金山區", 25.2211, 121.6356), _row("SCHOOL", "遠", "金山區", 25.30, 121.70),
    ])
    cache_a.promote_facility_staging_to_current(
        dataset_id=DATASET_ID, dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    cache_b = FacilityDatasetCache(db_path=str(tmp_path / "b.sqlite3"))
    cache_b.begin_staging_run(DATASET_ID)
    cache_b.stage_facility_records(DATASET_ID, SCOPE_ALL, [
        _row("SCHOOL", "遠", "金山區", 25.30, 121.70), _row("SCHOOL", "近", "金山區", 25.2211, 121.6356),
    ])
    cache_b.promote_facility_staging_to_current(
        dataset_id=DATASET_ID, dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ev_a = _provider_with_datasets(cache_a).query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    ev_b = _provider_with_datasets(cache_b).query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    assert ev_a.nearest.name == ev_b.nearest.name == "近"


# -- 12: tie distance ordering ----------------------------------------------

def test_tie_distance_deterministic_tiebreak(tmp_path, monkeypatch):
    """Two facilities at the mathematically identical distance from the
    query point (placed symmetrically) -- `nearest` must deterministically
    resolve to the same one every time, not an arbitrary/random pick."""
    center = CTX_WITH_COORD.center_coordinate
    cache = _synced_cache(tmp_path, [
        _row("SCHOOL", "丙校", "金山區", center.latitude + 0.001, center.longitude),
        _row("SCHOOL", "甲校", "金山區", center.latitude - 0.001, center.longitude),
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    provider = _provider_with_datasets(cache)
    picks = {provider.query_facility(CTX_WITH_COORD, FacilityType.SCHOOL).nearest.name for _ in range(5)}
    assert len(picks) == 1  # always the same one, never flips between runs


# -- 13: dataset provenance --------------------------------------------------

def test_dataset_provenance_present(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.2, 121.6)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    assert evidence.dataset_id == DATASET_ID
    assert evidence.dataset_name == "測試資料集"
    assert evidence.source_authority == "測試機關"
    assert evidence.checksum is not None
    assert evidence.record_count == 1
    assert evidence.matches[0].source_dataset_id == DATASET_ID


# -- 19: API filter actually ignored (structural -- NTPC_SERVER_FILTER_TRUST_
#         POLICY = UNTRUSTED_UNLESS_VERIFIED_PER_DATASET; the live filter=
#         no-op finding itself was verified empirically against the real
#         API during this round's audit -- see docs/phase9/facility_
#         evidence_pipeline.md -- this test instead locks in that the sync
#         script's CODE never relies on it, which stays checkable offline). --

def test_sync_script_never_sends_filter_param(monkeypatch):
    import scripts.sync_facility_dataset as sync_mod
    captured_urls = []

    class FakeResponse:
        status = 200
        def read(self):
            return b"[]"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        return FakeResponse()

    monkeypatch.setattr(sync_mod.urllib.request, "urlopen", fake_urlopen)
    try:
        sync_mod._fetch_json("SOME-DATASET-ID")
    except RuntimeError:
        pass  # empty list -> "0 rows" downstream errors are irrelevant here
    assert captured_urls, "expected at least one HTTP request"
    for url in captured_urls:
        assert "filter=" not in url, f"sync script must never send filter= (URL: {url})"


# -- 14: Mock offline ---------------------------------------------------------

def test_mock_never_touches_network_or_cache(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("MockOfficialFacilityProvider must never construct a real cache/network call")
    monkeypatch.setattr(ofp, "FacilityDatasetCache", _boom)
    mock = ofp.MockOfficialFacilityProvider()
    evidence = mock.query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    assert evidence.match_count == 0
    assert evidence.matches == []
    points = mock.fetch(CTX_WITH_COORD)
    assert all(p.source_type == "Mock" for p in points)
    assert all(p.value is None for p in points)


# -- 15: Real never silently falls back to Mock ------------------------------

def test_real_never_falls_back_to_mock(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.2, 121.6)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    real_evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    mock_evidence = ofp.MockOfficialFacilityProvider().query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    assert real_evidence.dataset_id == DATASET_ID
    assert mock_evidence.dataset_id is None
    assert real_evidence.notes != mock_evidence.notes


# -- 16/17: malformed record / invalid coordinate (sync-script level) -----

def test_sync_script_skips_unmapped_landmark_type_and_bad_coordinate(tmp_path, monkeypatch):
    import scripts.sync_facility_dataset as sync_mod

    fake_rows = [
        {"objectid": "1", "地標類型": "國民小學", "行政區": "金山區", "地標名稱": "甲國小",
         "地址": "addr", "twd97_x": "300033.0", "twd97_y": "2793883.0"},
        {"objectid": "2", "地標類型": "警察機關", "行政區": "金山區", "地標名稱": "某派出所",  # unmapped type
         "地址": "addr", "twd97_x": "300000.0", "twd97_y": "2793000.0"},
        {"objectid": "3", "地標類型": "國民中學", "行政區": "金山區", "地標名稱": "壞座標國中",  # malformed coordinate
         "地址": "addr", "twd97_x": "not-a-number", "twd97_y": "2793000.0"},
    ]
    monkeypatch.setattr(sync_mod, "_fetch_json", lambda dataset_id: fake_rows)

    cache = FacilityDatasetCache(db_path=str(tmp_path / "cache.sqlite3"))
    sync_mod._sync_landmark(cache)

    records = cache.lookup_facilities(sync_mod.LANDMARK_DATASET_ID, SCOPE_ALL, "SCHOOL", district="金山區")
    assert len(records) == 1
    assert records[0]["name"] == "甲國小"


# -- 18: snapshot unavailable -------------------------------------------------

def test_snapshot_unavailable_returns_unknown_with_sync_instruction(tmp_path, monkeypatch):
    cache = FacilityDatasetCache(db_path=str(tmp_path / "never_synced.sqlite3"))
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.SCHOOL)
    assert evidence.match_count == 0
    assert "sync_facility_dataset.py" in evidence.notes


# -- 20: official-first (never OSM/Nominatim) --------------------------------

def test_module_never_imports_osm_or_geocoding():
    """Structural check on actual import/call statements (not docstring
    prose, which legitimately explains WHY these are avoided)."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ofp))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.add(node.module or "")
            imported_names.update(a.name for a in node.names)
    forbidden = {"osm_facility_lookup", "geocode", "Nominatim"}
    assert not (imported_names & forbidden), f"unexpected import(s): {imported_names & forbidden}"


# -- 21: distance evidence does not directly set grade -----------------------

def test_evidence_model_has_no_grade_field():
    from domain.models import FacilityEvidence, FacilityMatch
    assert "grade" not in FacilityEvidence.model_fields
    assert "adjustment_rate" not in FacilityEvidence.model_fields
    assert "grade" not in FacilityMatch.model_fields


def test_provider_never_imports_rule_or_grade_engine():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ofp))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.add(node.module or "")
            imported_names.update(a.name for a in node.names)
    forbidden = {"rule_engine", "grade_engine", "adjustment_engine", "calculation_engine", "RuleEngine", "GradeEngine"}
    assert not (imported_names & forbidden), f"unexpected import(s): {imported_names & forbidden}"


# -- 22: Golden Case behavior (synthetic, Golden-Case-shaped) ---------------

def test_golden_case_target_coordinate_unavailable(tmp_path, monkeypatch):
    """The real Golden Case (金山區/金美段/489地號) has no official/trusted
    parcel coordinate anywhere in this repo (see module docstring's audit)
    -- this reproduces that exact situation: ctx has no center_coordinate,
    so even though real schools exist in 金山區 (synthetic here, matching
    the real district), distance must not be fabricated."""
    cache = _synced_cache(tmp_path, [
        _row("SCHOOL", "市立金美國小", "金山區", 25.22104, 121.63547),
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    golden_ctx = ProviderContext(
        case_no="1140901-99-001", city="新北市", district="金山區", segment_code="P002-00",
        parcel_id="金美段489地號",
    )
    evidence = _provider_with_datasets(cache).query_facility(golden_ctx, FacilityType.SCHOOL)
    assert evidence.query_coordinate is None
    assert evidence.match_count == 1
    assert evidence.nearest is None
    assert "TARGET_COORDINATE_UNAVAILABLE" in evidence.notes


# ===========================================================================
# Phase API-2.1: Facility Coordinate Provenance & Spatial Scope Hardening
# ===========================================================================

def _ctx_with_evidence(target_coordinate_evidence, district="金山區"):
    coord = None
    if target_coordinate_evidence is not None:
        coord = Coordinate(latitude=target_coordinate_evidence.latitude, longitude=target_coordinate_evidence.longitude)
    return ProviderContext(
        case_no="T", city="新北市", district=district, segment_code="P002-00", parcel_id="金美段489地號",
        center_coordinate=coord, center_coordinate_evidence=target_coordinate_evidence,
    )


def _official_evidence(lat=25.22104, lon=121.63547):
    return TargetCoordinateEvidence(
        latitude=lat, longitude=lon, source_type=CoordinateSourceType.OFFICIAL_GIS,
        source_authority="測試官方座標登記", authoritative_status=CoordinateAuthoritativeStatus.OFFICIAL,
        precision_level="PARCEL",
    )


def _nominatim_evidence(lat=25.22104, lon=121.63547):
    return TargetCoordinateEvidence(
        latitude=lat, longitude=lon, source_type=CoordinateSourceType.NOMINATIM_EXTERNAL,
        source_authority="OpenStreetMap Nominatim", authoritative_status=CoordinateAuthoritativeStatus.EXTERNAL_UNVERIFIED,
        precision_level="DISTRICT",
    )


def _demo_evidence(lat=25.23611, lon=121.61750):
    return TargetCoordinateEvidence(
        latitude=lat, longitude=lon, source_type=CoordinateSourceType.DEMO_ONLY,
        source_authority="Wikipedia demo anchor", authoritative_status=CoordinateAuthoritativeStatus.DEMO_ONLY,
        precision_level="DISTRICT",
    )


# -- 1: Nominatim-origin target coordinate not labeled official -----------

def test_nominatim_origin_not_labeled_official(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "市立金美國小", "金山區", 25.22104, 121.63547)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_nominatim_evidence())
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)

    assert evidence.match_count == 1
    assert evidence.nearest is not None  # a distance IS computed
    assert evidence.distance_authoritative_status == "MIXED_SOURCE"
    assert evidence.official_distance_ready is False


# -- 2: DEMO_ONLY coordinate not labeled official ---------------------------

def test_demo_only_origin_not_labeled_official(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.236, 121.617)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_demo_evidence())
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)

    assert evidence.distance_authoritative_status == "MIXED_SOURCE"
    assert evidence.official_distance_ready is False


# -- 3: Official target coordinate allows official distance -----------------

def test_official_target_coordinate_allows_official_distance(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "市立金美國小", "金山區", 25.22104, 121.63547)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_official_evidence())
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)

    assert evidence.distance_authoritative_status == "OFFICIAL"
    assert evidence.official_distance_ready is True
    assert evidence.target_coordinate_evidence.authoritative_status == CoordinateAuthoritativeStatus.OFFICIAL


# -- 4: unknown target coordinate provenance ---------------------------------

def test_unknown_target_coordinate_provenance_defensive_default(tmp_path, monkeypatch):
    """A ProviderContext constructed with only `center_coordinate` (no
    evidence attached -- e.g. an older/direct caller) must NOT be silently
    treated as official; the provider must defensively synthesize an
    UNKNOWN-provenance evidence and report MIXED_SOURCE, never OFFICIAL."""
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "市立金美國小", "金山區", 25.22104, 121.63547)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    legacy_ctx = ProviderContext(
        case_no="T", city="新北市", district="金山區", segment_code="P002-00", parcel_id="金美段489地號",
        center_coordinate=Coordinate(latitude=25.22104, longitude=121.63547),
        # no center_coordinate_evidence -- simulates a pre-2.1 caller
    )
    evidence = _provider_with_datasets(cache).query_facility(legacy_ctx, FacilityType.SCHOOL)

    assert evidence.target_coordinate_evidence is not None
    assert evidence.target_coordinate_evidence.source_type == CoordinateSourceType.UNKNOWN
    assert evidence.target_coordinate_evidence.authoritative_status == CoordinateAuthoritativeStatus.UNKNOWN
    assert evidence.distance_authoritative_status == "MIXED_SOURCE"
    assert evidence.official_distance_ready is False


# -- 5/6: cross-district spatial search + boundary test ---------------------

def test_cross_district_nearest_search(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [
        _row("SCHOOL", "同區遠校", "金山區", 25.30, 121.70),      # same district as query, but far
        _row("SCHOOL", "鄰區近校", "萬里區", 25.2215, 121.6360),  # DIFFERENT district, much closer
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_official_evidence(), district="金山區")
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)

    assert evidence.match_count == 2  # both districts represented, none excluded
    assert evidence.nearest is not None
    assert evidence.nearest.name == "鄰區近校"
    assert evidence.nearest.district == "萬里區"


def test_boundary_same_district_farther_loses_to_adjacent_district_closer(tmp_path, monkeypatch):
    """Exact scenario from Phase API-2.1 §7: Facility A (same district,
    ~2500m) vs Facility B (different district, ~180m) -- nearest MUST be
    B, never excluded just because its district differs from the query's."""
    query_coord = (25.22104, 121.63547)
    # ~2500m north (roughly 0.0225 deg lat ~ 2500m)
    far_same_district = _row("SCHOOL", "Facility A", "金山區", query_coord[0] + 0.0225, query_coord[1])
    # ~180m north (roughly 0.0016 deg lat ~ 180m), different district
    near_other_district = _row("SCHOOL", "Facility B", "萬里區", query_coord[0] + 0.0016, query_coord[1])
    cache = _synced_cache(tmp_path, [far_same_district, near_other_district])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_official_evidence(*query_coord), district="金山區")
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)

    assert evidence.nearest.name == "Facility B"
    # sanity-check the distances are roughly in the claimed ranges
    a = next(m for m in evidence.matches if m.name == "Facility A")
    b = next(m for m in evidence.matches if m.name == "Facility B")
    assert 2000 < a.distance_m < 3000
    assert 100 < b.distance_m < 300


# -- 8: all candidates preserved (cross-district) ----------------------------

def test_all_candidates_preserved_across_districts(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [
        _row("SCHOOL", "A", "金山區", 25.25, 121.65),
        _row("SCHOOL", "B", "萬里區", 25.20, 121.60),
        _row("SCHOOL", "C", "板橋區", 25.01, 121.46),
    ])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_official_evidence(), district="金山區")
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)
    assert evidence.match_count == 3
    assert {m.name for m in evidence.matches} == {"A", "B", "C"}


# -- 9: nearest not treated as selected appraisal facility -------------------

def test_nearest_field_is_not_named_or_documented_as_selection():
    from domain.models import FacilityEvidence
    assert "selected_appraisal_facility" not in FacilityEvidence.model_fields
    assert "selected" not in FacilityEvidence.model_fields
    doc = FacilityEvidence.__doc__ or ""
    assert "DISTANCE CONVENIENCE POINTER" in doc


# -- 10: station subtype preserved -------------------------------------------

def test_station_subtype_preserved_through_sync(tmp_path, monkeypatch):
    import scripts.sync_facility_dataset as sync_mod

    fake_rows = [
        {"objectid": "1", "地標類型": "捷運站", "行政區": "板橋區", "地標名稱": "板橋站",
         "地址": "addr", "twd97_x": "300033.0", "twd97_y": "2793883.0"},
        {"objectid": "2", "地標類型": "火車站", "行政區": "板橋區", "地標名稱": "板橋車站",
         "地址": "addr", "twd97_x": "300100.0", "twd97_y": "2793900.0"},
    ]
    monkeypatch.setattr(sync_mod, "_fetch_json", lambda dataset_id: fake_rows)
    cache = FacilityDatasetCache(db_path=str(tmp_path / "cache.sqlite3"))
    sync_mod._sync_landmark(cache)

    records = cache.lookup_facilities(sync_mod.LANDMARK_DATASET_ID, SCOPE_ALL, "STATION")
    by_name = {r["name"]: r for r in records}
    assert by_name["板橋站"]["facility_subtype"] == "MRT"
    assert by_name["板橋站"]["source_category"] == "捷運站"
    assert by_name["板橋車站"]["facility_subtype"] == "TRA"
    assert by_name["板橋車站"]["source_category"] == "火車站"


# -- 11: incomplete station coverage produces PARTIAL (structural) ----------

def test_station_subtype_map_has_no_hsr_or_bus_terminal_bucket():
    """Phase API-2.1 §9 audit finding: NTPC OpenData has no dedicated HSR or
    bus-terminal station dataset -- this locks in that the sync script's
    own subtype map does not silently claim coverage it doesn't have (no
    fabricated HSR/BUS_TERMINAL bucket). Light rail (輕軌) IS present in the
    data but only under the source's own "捷運站" category (no separate
    LIGHT_RAIL bucket exists because the SOURCE itself does not distinguish
    it from heavy-rail MRT -- see sync_facility_dataset.py's comment)."""
    import scripts.sync_facility_dataset as sync_mod
    subtypes = set(sync_mod._LANDMARK_TYPE_TO_SUBTYPE.values())
    assert "HSR" not in subtypes
    assert "BUS_TERMINAL" not in subtypes
    assert "LIGHT_RAIL" not in subtypes
    assert subtypes == {"ELEMENTARY", "JUNIOR_HIGH", "COMPLETE_SCHOOL", "SENIOR_HIGH", "COLLEGE", "MRT", "TRA"}


# -- 13: straight-line never labeled route distance --------------------------

def test_distance_semantics_never_labeled_route_or_walking():
    values = {v.value for v in FacilityDistanceSemantics}
    assert "ROUTE_DISTANCE" not in values
    assert "WALKING_DISTANCE" not in values
    assert FacilityDistanceSemantics.STRAIGHT_LINE_REFERENCE.value == "STRAIGHT_LINE_REFERENCE"


def test_distance_method_values_are_haversine_not_geodesic_or_route():
    values = {v.value for v in FacilityDistanceMethod}
    assert values == {"HAVERSINE_WGS84", "UNKNOWN"}
    assert "GEODESIC_WGS84" not in values
    assert "ROUTE" not in values


def test_computed_distance_carries_straight_line_semantics(tmp_path, monkeypatch):
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.22104, 121.63547)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_official_evidence())
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)
    assert evidence.distance_semantics == FacilityDistanceSemantics.STRAIGHT_LINE_REFERENCE


# -- 17: Golden Case remains no official distance (even with a hypothetical
#        Nominatim-sourced coordinate, per §5's explicit requirement) ------

def test_golden_case_never_official_even_with_nominatim_coordinate(tmp_path, monkeypatch):
    """Even if collect_data.py's Nominatim fallback DID produce a non-None
    coordinate for the Golden Case (it can, in real mode, per the traced
    runtime paths in docs/phase9/facility_evidence_pipeline.md), the
    resulting distance must still never be reported OFFICIAL -- Nominatim
    provenance is EXTERNAL_UNVERIFIED, not OFFICIAL, full stop."""
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "市立金美國小", "金山區", 25.22104, 121.63547)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    golden_ctx_with_nominatim = ProviderContext(
        case_no="1140901-99-001", city="新北市", district="金山區", segment_code="P002-00",
        parcel_id="金美段489地號",
        center_coordinate=Coordinate(latitude=25.22, longitude=121.63),
        center_coordinate_evidence=_nominatim_evidence(lat=25.22, lon=121.63),
    )
    evidence = _provider_with_datasets(cache).query_facility(golden_ctx_with_nominatim, FacilityType.SCHOOL)

    assert evidence.official_distance_ready is False
    assert evidence.distance_authoritative_status != "OFFICIAL"


# ===========================================================================
# Phase API-2.2 §3: Spatial Search Coverage Contract
# ===========================================================================

def test_search_coverage_field_states_new_taipei_city_only(tmp_path, monkeypatch):
    """`nearest` must never be read as an absolute/global nearest -- the
    NTPC dataset only covers New Taipei City. `search_coverage` makes this
    explicit on every FacilityEvidence (backward compatible: existing
    `nearest`/`matches` field names/shapes are unchanged)."""
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.25, 121.65)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    ctx = _ctx_with_evidence(_official_evidence())
    evidence = _provider_with_datasets(cache).query_facility(ctx, FacilityType.SCHOOL)
    assert evidence.search_coverage == "NEW_TAIPEI_CITY"


def test_search_coverage_default_present_even_without_matches(tmp_path, monkeypatch):
    # STATION has zero rows here (only a SCHOOL row is staged) -- a real
    # "0 matches" case, not an unsynced-scope error (see test_empty_
    # dataset_zero_matches for why this must stage at least one row).
    cache = _synced_cache(tmp_path, [_row("SCHOOL", "甲校", "金山區", 25.25, 121.65)])
    _patch_all_types_to(monkeypatch, DATASET_ID)
    evidence = _provider_with_datasets(cache).query_facility(CTX_WITH_COORD, FacilityType.STATION)
    assert evidence.match_count == 0
    assert evidence.search_coverage == "NEW_TAIPEI_CITY"


def test_nearest_docstring_states_within_coverage_not_global():
    from domain.models import FacilityEvidence
    doc = FacilityEvidence.__doc__ or ""
    assert "nearest_within_dataset_coverage" in doc
    assert "nearest_global" in doc  # mentioned explicitly as what it is NOT
