# -*- coding: utf-8 -*-
"""
Phase API-2.3F §13: handler-level integration tests for collect_data.py's
Coordinate Evidence redesign (_resolve_coordinate_evidence_bundle /
_select_analysis_coordinate / _compute_coordinate_comparison). These call
the handler's own module-level functions directly with plain meta/body
dicts -- no DynamoDB case_store involvement needed, since none of these
functions touch case_store.

Central correction under test (Phase API-2.3F's redesign of Phase
API-2.3H's design): a submitted coordinate must NEVER block the official
NLSC lookup from being attempted -- SUBMITTED_COORDINATE_EVIDENCE and
OFFICIAL_PARCEL_COORDINATE_EVIDENCE are independent, both preserved, and
official (when SUCCESS) outranks submitted only for the SINGLE
`analysis_coordinate` that feeds facility-distance calculations.

collect_data.py reads DATA_PROVIDER_MODE and builds module-level state at
import time, so every test here reloads the module after setting env vars,
matching the established convention in tests/test_backend_handlers_e2e.py's
TestDataProviderModeSwitch.
"""
import importlib
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

from nlsc_code_cache import NlscCodeCache  # noqa: E402
from official_parcel_coordinate_provider import ENV_ENABLED, ENV_AUTH_CONTRACT_VERIFIED  # noqa: E402
from domain.models import (  # noqa: E402
    CoordinateSourceType, CoordinateAuthoritativeStatus, CoordinateComparisonStatus,
    OfficialParcelCoordinateEvidence, ParcelCoordinateStatus, ParcelCoordinateSemantics, Coordinate,
)


def _reload_real(monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER_MODE", "real")
    import collect_data
    importlib.reload(collect_data)
    return collect_data


def _reload_mock(monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    import collect_data
    importlib.reload(collect_data)
    return collect_data


def _seed_nlsc_cache(tmp_path, monkeypatch, *, county_code="F", town_code="F25"):
    db_path = str(tmp_path / "nlsc_codes.sqlite3")
    monkeypatch.setenv("NLSC_CODE_CACHE_DB_PATH", db_path)
    cache = NlscCodeCache(db_path=db_path)
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": county_code, "county_name": "新北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")
    cache.begin_staging_run("towns")
    cache.stage_towns(county_code, [{"town_code": town_code, "town_name": "金山區"}])
    cache.promote_towns(source_service="ListTown", source_url="http://x")
    cache.begin_staging_run("sections")
    cache.stage_sections(county_code, town_code, [
        {"office": "FD", "section_code": "1027", "section_name": "金美段"},
    ])
    cache.promote_sections(source_service="ListLandSection", source_url="http://x")
    return db_path


def _meta():
    return {"city": "新北市", "district": "金山區", "segment_code": "P002-00", "base_parcel_id": "金美段489地號"}


def _stub_official_success(monkeypatch, collect_data, *, latitude=24.142313, longitude=120.683552):
    """Stubs _resolve_nlsc_official_coordinate_evidence to behave as if
    CAD_001 genuinely returned SUCCESS, without needing a real credential
    or monkeypatching the auth-contract constant -- this test file's
    concern is the coordinate-selection/comparison logic downstream of
    that result, not the Auth Gate itself (covered in
    tests/test_official_parcel_coordinate_provider.py)."""
    success_evidence = OfficialParcelCoordinateEvidence(
        status=ParcelCoordinateStatus.SUCCESS, latitude=latitude, longitude=longitude,
        coordinate_semantics=ParcelCoordinateSemantics.OFFICIAL_PARCEL_REPRESENTATIVE_POINT,
        source_authority="內政部國土測繪中心",
        source_url="https://api.nlsc.gov.tw/dmaps/CadasMapPosition/F/1027/04890000/4326",
        confidence="高", requires_manual_review=True,
    )
    monkeypatch.setattr(
        collect_data, "_resolve_nlsc_official_coordinate_evidence",
        lambda case_no, meta: collect_data.to_target_coordinate_evidence(success_evidence),
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DATA_PROVIDER_MODE", "NLSC_CODE_CACHE_DB_PATH", ENV_ENABLED, ENV_AUTH_CONTRACT_VERIFIED,
                "NLSC_CAD_API_USERNAME", "NLSC_CAD_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    yield
    # Restore collect_data to a known-good state for later test modules in
    # this process (module reload mutates shared module-level state).
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    import collect_data
    importlib.reload(collect_data)


# -- 1: submitted + official are both saved as independent evidences. ----

def test_submitted_and_official_both_saved_independently(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _stub_official_success(monkeypatch, collect_data)

    body = {"center_coordinate": {"latitude": 24.15, "longitude": 120.70}}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)

    assert bundle["submitted"] is not None
    assert bundle["submitted"].latitude == 24.15
    assert bundle["official_parcel"] is not None
    assert bundle["official_parcel"].latitude == pytest.approx(24.142313)
    # Neither overwrote the other.
    assert bundle["submitted"].latitude != bundle["official_parcel"].latitude


# -- 2: official SUCCESS is selected as the analysis coordinate. ---------

def test_official_success_selected_as_analysis_coordinate(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _stub_official_success(monkeypatch, collect_data)

    body = {"center_coordinate": {"latitude": 24.15, "longitude": 120.70}}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)

    analysis = bundle["analysis_coordinate"]
    assert analysis.source_type == CoordinateSourceType.OFFICIAL_GIS
    assert analysis.authoritative_status == CoordinateAuthoritativeStatus.OFFICIAL
    assert analysis.latitude == pytest.approx(24.142313)


# -- 3: a submitted coordinate never prevents official verification. -----

def test_submitted_does_not_block_official_lookup(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)

    calls = []

    def _spy(case_no, meta):
        calls.append((case_no, meta))
        return None  # AUTH_REQUIRED in this round's real environment
    monkeypatch.setattr(collect_data, "_resolve_nlsc_official_coordinate_evidence", _spy)

    body = {"center_coordinate": {"latitude": 24.15, "longitude": 120.70}}
    collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    assert len(calls) == 1  # official lookup WAS attempted despite submitted existing


# -- 4: official unavailable -> submitted fallback for analysis. ---------

def test_official_unavailable_falls_back_to_submitted(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _seed_nlsc_cache(tmp_path, monkeypatch)
    # NLSC_CAD_API_ENABLED left unset -- official always returns None.

    body = {"center_coordinate": {"latitude": 24.15, "longitude": 120.70}}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)

    assert bundle["official_parcel"] is None
    assert bundle["analysis_coordinate"].source_type == CoordinateSourceType.SUBMITTED_BY_CALLER
    assert bundle["analysis_coordinate"].latitude == 24.15


# -- 5: neither official nor submitted -> Nominatim, REFERENCE_ONLY. -----

def test_neither_official_nor_submitted_uses_nominatim_reference_only(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _seed_nlsc_cache(tmp_path, monkeypatch)

    monkeypatch.setattr(collect_data, "geocode", lambda place: Coordinate(latitude=25.3, longitude=121.7))

    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})
    assert bundle["official_parcel"] is None
    assert bundle["submitted"] is None
    assert bundle["nominatim_reference"] is not None
    assert bundle["nominatim_reference"].authoritative_status == CoordinateAuthoritativeStatus.EXTERNAL_UNVERIFIED
    assert bundle["analysis_coordinate"] is bundle["nominatim_reference"]
    assert bundle["analysis_coordinate"].authoritative_status != CoordinateAuthoritativeStatus.OFFICIAL


def test_nominatim_never_consulted_when_official_or_submitted_present(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _stub_official_success(monkeypatch, collect_data)

    def _boom(place):
        raise AssertionError("Nominatim must not be consulted when an official coordinate is available")
    monkeypatch.setattr(collect_data, "geocode", _boom)

    collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})  # must not raise


# -- 6: coordinate_delta_m is computed deterministically. -----------------

def test_coordinate_delta_deterministic(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _stub_official_success(monkeypatch, collect_data, latitude=24.142313, longitude=120.683552)

    body = {"center_coordinate": {"latitude": 24.142313, "longitude": 120.683552}}
    bundle1 = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    bundle2 = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    assert bundle1["comparison"].coordinate_delta_m == bundle2["comparison"].coordinate_delta_m
    assert bundle1["comparison"].coordinate_delta_m == pytest.approx(0.0, abs=1e-6)
    assert bundle1["comparison"].status == CoordinateComparisonStatus.MATCH


def test_coordinate_delta_different_when_far_apart(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _stub_official_success(monkeypatch, collect_data, latitude=24.142313, longitude=120.683552)

    body = {"center_coordinate": {"latitude": 25.0, "longitude": 121.5}}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    assert bundle["comparison"].status == CoordinateComparisonStatus.DIFFERENT
    assert bundle["comparison"].coordinate_delta_m > 1000


def test_no_comparison_when_only_one_side_present(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _seed_nlsc_cache(tmp_path, monkeypatch)  # official stays AUTH_REQUIRED (flag off)

    body = {"center_coordinate": {"latitude": 24.15, "longitude": 120.70}}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    assert bundle["comparison"] is None


# -- 7: no fabricated legal threshold -- status is purely descriptive. ---

def test_comparison_never_claims_which_coordinate_is_correct(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _stub_official_success(monkeypatch, collect_data, latitude=24.142313, longitude=120.683552)

    body = {"center_coordinate": {"latitude": 25.0, "longitude": 121.5}}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    comparison = bundle["comparison"]
    # The evidence model has no field that could assert "correct" -- this
    # is a structural guarantee, not just a notes-string check.
    assert not hasattr(comparison, "correct_coordinate")
    assert not hasattr(comparison, "winner")
    assert comparison.status in (CoordinateComparisonStatus.MATCH, CoordinateComparisonStatus.DIFFERENT,
                                  CoordinateComparisonStatus.MANUAL_REVIEW_REQUIRED)


# -- Facility Integration: official_distance_ready only true for genuine
# OFFICIAL provenance (existing RealOfficialFacilityProvider logic,
# unmodified -- these tests confirm the UPSTREAM evidence it receives is
# now correctly tagged in every scenario). ---------------------------------

def test_analysis_coordinate_authoritative_status_matches_source(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)

    # (a) Official SUCCESS -> OFFICIAL.
    _stub_official_success(monkeypatch, collect_data)
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})
    assert bundle["analysis_coordinate"].authoritative_status == CoordinateAuthoritativeStatus.OFFICIAL

    # (b) Submitted only -> EXTERNAL_UNVERIFIED, never OFFICIAL.
    monkeypatch.setattr(collect_data, "_resolve_nlsc_official_coordinate_evidence", lambda case_no, meta: None)
    body = {"center_coordinate": {"latitude": 24.15, "longitude": 120.70}}
    bundle2 = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body)
    assert bundle2["analysis_coordinate"].authoritative_status == CoordinateAuthoritativeStatus.EXTERNAL_UNVERIFIED

    # (c) Nominatim only -> EXTERNAL_UNVERIFIED, never OFFICIAL.
    monkeypatch.setattr(collect_data, "geocode", lambda place: Coordinate(latitude=25.3, longitude=121.7))
    bundle3 = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})
    assert bundle3["analysis_coordinate"].authoritative_status == CoordinateAuthoritativeStatus.EXTERNAL_UNVERIFIED


# -- Carried-over Phase API-2.3H invariants (still required this round) --

def test_auth_contract_unconfirmed_means_no_cad001_network_call(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _seed_nlsc_cache(tmp_path, monkeypatch)
    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv("NLSC_CAD_API_USERNAME", "test-user")
    monkeypatch.setenv("NLSC_CAD_API_TOKEN", "test-token")

    def _boom(*a, **kw):
        raise AssertionError("CAD001_AUTH_CONTRACT_STATUS=UNCONFIRMED must block every network call")
    monkeypatch.setattr("urllib.request.urlopen", _boom)

    evidence = collect_data._resolve_nlsc_official_coordinate_evidence("CASE1", _meta())
    assert evidence is None


def test_nlsc_cache_missing_does_not_crash_handler(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    monkeypatch.setenv("NLSC_CODE_CACHE_DB_PATH", str(tmp_path / "does_not_exist.sqlite3"))
    monkeypatch.setenv(ENV_ENABLED, "true")

    monkeypatch.setattr(collect_data, "geocode", lambda place: Coordinate(latitude=25.3, longitude=121.7))

    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})  # must not raise
    assert bundle["analysis_coordinate"].source_type == CoordinateSourceType.NOMINATIM_EXTERNAL


def test_non_new_taipei_case_is_out_of_coverage_falls_through(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    db_path = str(tmp_path / "nlsc_codes.sqlite3")
    monkeypatch.setenv("NLSC_CODE_CACHE_DB_PATH", db_path)
    cache = NlscCodeCache(db_path=db_path)
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "A", "county_name": "臺北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")
    cache.begin_staging_run("towns")
    cache.stage_towns("A", [{"town_code": "A01", "town_name": "中正區"}])
    cache.promote_towns(source_service="ListTown", source_url="http://x")
    cache.begin_staging_run("sections")
    cache.stage_sections("F", "F25", [{"office": "FD", "section_code": "1027", "section_name": "金美段"}])
    cache.promote_sections(source_service="ListLandSection", source_url="http://x")
    monkeypatch.setenv(ENV_ENABLED, "true")

    monkeypatch.setattr(collect_data, "geocode", lambda place: Coordinate(latitude=25.0, longitude=121.5))

    meta = {"city": "臺北市", "district": "中正區", "segment_code": "P099-00", "base_parcel_id": "某段1地號"}
    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", meta, body={})
    assert bundle["official_parcel"] is None
    assert bundle["analysis_coordinate"].source_type == CoordinateSourceType.NOMINATIM_EXTERNAL  # no crash


def test_feature_flag_false_preserves_prior_nominatim_behavior(tmp_path, monkeypatch):
    collect_data = _reload_real(monkeypatch)
    _seed_nlsc_cache(tmp_path, monkeypatch)
    # ENV_ENABLED left unset -- this round's actual default.

    monkeypatch.setattr(collect_data, "geocode", lambda place: Coordinate(latitude=25.3, longitude=121.7))

    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})
    assert bundle["analysis_coordinate"].source_type == CoordinateSourceType.NOMINATIM_EXTERNAL
    assert bundle["analysis_coordinate"].precision_level == "DISTRICT"


def test_mock_mode_never_touches_nlsc_or_nominatim(monkeypatch):
    collect_data = _reload_mock(monkeypatch)

    def _boom_nlsc(*a, **kw):
        raise AssertionError("Mock mode must never attempt NLSC resolution")
    def _boom_geocode(*a, **kw):
        raise AssertionError("Mock mode must never attempt Nominatim resolution")
    monkeypatch.setattr(collect_data, "_resolve_nlsc_official_coordinate_evidence", _boom_nlsc)
    monkeypatch.setattr(collect_data, "geocode", _boom_geocode)

    bundle = collect_data._resolve_coordinate_evidence_bundle("CASE1", _meta(), body={})
    assert bundle["official_parcel"] is None
    assert bundle["nominatim_reference"] is None
    assert bundle["analysis_coordinate"] is None


def test_no_guessed_http_credentials_reach_the_network():
    # Structural guarantee that the guessed headers stay gone (Phase
    # API-2.3H/§2.3F): the actual HTTP-header-dict assignment syntax must
    # never reappear in source, even though the module docstring/comments
    # legitimately still MENTION the old (removed) header names as
    # historical context.
    import official_parcel_coordinate_provider as m
    import inspect
    source = inspect.getsource(m)
    assert '"X-NLSC-Username":' not in source
    assert '"X-NLSC-Token":' not in source
