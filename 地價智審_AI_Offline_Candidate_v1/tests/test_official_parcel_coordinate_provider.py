# -*- coding: utf-8 -*-
"""
Direct unit tests for providers/official_parcel_coordinate_provider.py
(Phase API-2.3). Central invariant under test throughout this file (this
round's own opening instruction): "沒有NLSC key -> 偷偷Nominatim -> 假裝
官方" must never happen -- a missing feature flag or missing credential
must produce AUTH_REQUIRED, never a silently-substituted Mock/Nominatim/
demo coordinate reported as official.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
FIXTURES_DIR = os.path.join(THIS_DIR, "fixtures", "nlsc")

import pytest  # noqa: E402

from base import ProviderContext  # noqa: E402
from nlsc_code_cache import NlscCodeCache  # noqa: E402
from nlsc_cadastral_code_resolver import NlscCadastralCodeResolver  # noqa: E402
from domain.models import ParcelCoordinateStatus, ParcelCoordinateSemantics  # noqa: E402
from domain.models import CoordinateSourceType, CoordinateAuthoritativeStatus  # noqa: E402

import official_parcel_coordinate_provider as m  # noqa: E402
from official_parcel_coordinate_provider import (  # noqa: E402
    build_cadas_map_position_request, parse_cadas_map_position_response,
    MockOfficialParcelCoordinateProvider, RealOfficialParcelCoordinateProvider,
    to_target_coordinate_evidence, ENV_ENABLED, ENV_USERNAME, ENV_TOKEN, ENV_AUTH_CONTRACT_VERIFIED,
)


def _force_auth_contract_verified(monkeypatch):
    """Test-only helper: the real Auth Gate requires BOTH the
    CAD001_AUTH_CONTRACT_STATUS source-code constant AND the
    NLSC_CAD_AUTH_CONTRACT_VERIFIED env var (Phase API-2.3H §3-4) -- this
    round's actual value of that constant is "UNCONFIRMED" and stays that
    way (see module docstring), so reaching the network-call branch at all
    requires monkeypatching the constant too, exactly as a real future
    round would need an actual reviewed code change to flip it."""
    monkeypatch.setattr(m, "CAD001_AUTH_CONTRACT_STATUS", "VERIFIED")
    monkeypatch.setenv(ENV_AUTH_CONTRACT_VERIFIED, "true")


def _read_fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES_DIR, name), "rb") as f:
        return f.read()


def _seeded_cache(tmp_path):
    cache = NlscCodeCache(db_path=str(tmp_path / "nlsc_codes.sqlite3"))
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "F", "county_name": "新北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")
    cache.begin_staging_run("towns")
    cache.stage_towns("F", [{"town_code": "F25", "town_name": "金山區"}])
    cache.promote_towns(source_service="ListTown", source_url="http://x")
    cache.begin_staging_run("sections")
    cache.stage_sections("F", "F25", [{"office": "FD", "section_code": "1027", "section_name": "金美段"}])
    cache.promote_sections(source_service="ListLandSection", source_url="http://x")
    return cache


def _golden_ctx():
    return ProviderContext(
        case_no="1140901-99-001", city="新北市", district="金山區",
        segment_code="P002-00", parcel_id="金美段489地號",
    )


@pytest.fixture(autouse=True)
def _clear_nlsc_env(monkeypatch):
    # Every test starts from a clean slate on the auth-gate env vars --
    # never inherit a real credential from the ambient shell environment.
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    monkeypatch.delenv(ENV_USERNAME, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv(ENV_AUTH_CONTRACT_VERIFIED, raising=False)


# -- build_cadas_map_position_request() ----------------------------------

def test_build_request_golden_case_endpoint():
    req = build_cadas_map_position_request("F", "1027", "04890000")
    assert req["endpoint"] == "https://api.nlsc.gov.tw/dmaps/CadasMapPosition/F/1027/04890000/4326"
    assert req["method"] == "GET"
    assert req["output_crs"] == "4326"


def test_build_request_supports_3826():
    req = build_cadas_map_position_request("F", "1027", "04890000", output_crs="3826")
    assert req["endpoint"].endswith("/3826")


def test_build_request_rejects_invalid_crs():
    with pytest.raises(ValueError):
        build_cadas_map_position_request("F", "1027", "04890000", output_crs="9999")


# -- parse_cadas_map_position_response() ----------------------------------

def test_parse_success_fixture():
    body = _read_fixture("cad001_success_response.xml")
    result = parse_cadas_map_position_response(http_status=200, body=body, output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.SUCCESS
    assert result["rep_x"] == pytest.approx(120.683552)
    assert result["rep_y"] == pytest.approx(24.142313)
    assert result["ld_x"] == pytest.approx(120.683471)
    assert result["rt_y"] == pytest.approx(24.142389)


def test_parse_missing_repxy_is_unverified_response_never_not_found():
    # Phase API-2.3H §5-6: NLSC never documents this response shape, so it
    # must NOT be presented as a confirmed "官方查無宗地" (NOT_FOUND).
    body = _read_fixture("cad001_missing_repxy_response.xml")
    result = parse_cadas_map_position_response(http_status=200, body=body, output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.UNVERIFIED_RESPONSE
    assert result["status"] != ParcelCoordinateStatus.NOT_FOUND


def test_parse_malformed_xml_is_parse_failed_not_crash():
    body = _read_fixture("cad001_malformed.xml")
    result = parse_cadas_map_position_response(http_status=200, body=body, output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.PARSE_FAILED


def test_parse_empty_body_is_empty_response_never_not_found():
    result = parse_cadas_map_position_response(http_status=200, body=b"", output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.EMPTY_RESPONSE
    assert result["status"] != ParcelCoordinateStatus.NOT_FOUND


@pytest.mark.parametrize("status", [401, 403])
def test_parse_auth_failure_status_codes(status):
    result = parse_cadas_map_position_response(http_status=status, body=b"", output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.AUTH_REQUIRED


def test_parse_400_is_invalid_request():
    result = parse_cadas_map_position_response(http_status=400, body=b"", output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.INVALID_REQUEST


def test_parse_404_is_unverified_response_never_not_found():
    # Phase API-2.3H §6: CAD001_DOCUMENTED_NOT_FOUND_RESPONSE=UNCONFIRMED,
    # so 404 must not be presented as "404必然=查無地號".
    result = parse_cadas_map_position_response(http_status=404, body=b"", output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.UNVERIFIED_RESPONSE
    assert result["status"] != ParcelCoordinateStatus.NOT_FOUND


def test_parse_500_is_service_unavailable():
    result = parse_cadas_map_position_response(http_status=503, body=b"", output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.SERVICE_UNAVAILABLE


def test_parse_unexpected_status_is_unknown():
    result = parse_cadas_map_position_response(http_status=302, body=b"", output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.UNKNOWN


def test_parse_network_error_is_service_unavailable_never_fallback():
    result = parse_cadas_map_position_response(
        http_status=None, body=None, output_crs="4326", network_error="timeout"
    )
    assert result["status"] == ParcelCoordinateStatus.SERVICE_UNAVAILABLE


def test_parse_none_status_is_unknown():
    result = parse_cadas_map_position_response(http_status=None, body=None, output_crs="4326")
    assert result["status"] == ParcelCoordinateStatus.UNKNOWN


# -- Mock provider: fully offline, honest UNKNOWN -------------------------

def test_mock_provider_never_touches_network(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("MockOfficialParcelCoordinateProvider must never call urllib.request.urlopen")
    monkeypatch.setattr("urllib.request.urlopen", _boom)

    provider = MockOfficialParcelCoordinateProvider()
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.UNKNOWN
    assert evidence.coordinate_semantics == ParcelCoordinateSemantics.UNKNOWN

    points = provider.fetch(_golden_ctx())
    assert points[0].value == ParcelCoordinateStatus.UNKNOWN.value


# -- Real provider: Auth Gate ----------------------------------------------

def test_real_provider_feature_flag_disabled_is_auth_required_but_resolves_identifiers(tmp_path):
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.AUTH_REQUIRED
    # Identifier resolution layer succeeded -- proves failure is at the
    # AUTHORIZATION layer, not the IDENTIFIER RESOLUTION layer.
    assert evidence.county_code == "F"
    assert evidence.town_code == "F25"
    assert evidence.section_code == "1027"
    assert evidence.nlsc_land_no == "04890000"
    assert evidence.requires_manual_review is True


def test_real_provider_feature_enabled_auth_contract_unverified_by_default(tmp_path, monkeypatch):
    # Phase API-2.3H §3: this round's actual, unmodified environment --
    # feature flag on, but CAD001_AUTH_CONTRACT_STATUS is still
    # "UNCONFIRMED" (the real, un-monkeypatched module constant) -- must
    # produce AUTH_CONTRACT_UNVERIFIED, distinct from AUTH_REQUIRED.
    monkeypatch.setenv(ENV_ENABLED, "true")
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.AUTH_CONTRACT_UNVERIFIED
    assert evidence.section_code == "1027"


def test_real_provider_auth_contract_unverified_even_with_credentials_present(tmp_path, monkeypatch):
    # Phase API-2.3H §3 explicit wording: "即使 NLSC_CAD_API_ENABLED=true,
    # credentials present, 也必須 AUTH_CONTRACT_UNVERIFIED...NO NETWORK
    # CALL" -- credentials being present must NOT be enough on their own.
    def _boom(*a, **kw):
        raise AssertionError("auth_contract_unverified must never reach the network layer")
    monkeypatch.setattr("urllib.request.urlopen", _boom)

    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv(ENV_USERNAME, "test-user")
    monkeypatch.setenv(ENV_TOKEN, "test-token")
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.AUTH_CONTRACT_UNVERIFIED
    assert evidence.requires_manual_review is True


def test_real_provider_feature_enabled_contract_verified_but_no_credentials_is_auth_required(tmp_path, monkeypatch):
    # Once (and only once) the auth contract is verified, a missing
    # credential still correctly falls through to AUTH_REQUIRED.
    monkeypatch.setenv(ENV_ENABLED, "true")
    _force_auth_contract_verified(monkeypatch)
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.AUTH_REQUIRED
    assert evidence.section_code == "1027"


def test_real_provider_no_guessed_auth_headers_sent_when_contract_verified(tmp_path, monkeypatch):
    # Phase API-2.3H §3: even in the (this-round-unreachable-in-practice)
    # fully-open branch, no guessed X-NLSC-Username/X-NLSC-Token header may
    # be sent -- the documented CAD_001 request carries no auth field at all.
    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv(ENV_USERNAME, "test-user")
    monkeypatch.setenv(ENV_TOKEN, "test-token")
    _force_auth_contract_verified(monkeypatch)

    captured = {}

    class _FakeResp:
        status = 200
        def read(self):
            return _read_fixture("cad001_success_response.xml")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    provider.query_parcel_coordinate(_golden_ctx())
    assert not any(h.lower().startswith("x-nlsc") for h in captured["headers"])


def test_real_provider_disabled_flag_never_calls_network(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("Feature flag off must never reach the network layer")
    monkeypatch.setattr("urllib.request.urlopen", _boom)
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    provider.query_parcel_coordinate(_golden_ctx())  # must not raise via _boom


def test_real_provider_data_provider_mode_real_alone_does_not_imply_call(tmp_path, monkeypatch):
    # This round's explicit instruction: DATA_PROVIDER_MODE=real alone must
    # never imply calling CAD_001 -- only feature flag + credentials do.
    monkeypatch.setenv("DATA_PROVIDER_MODE", "real")
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.AUTH_REQUIRED
    monkeypatch.delenv("DATA_PROVIDER_MODE", raising=False)


def test_real_provider_never_falls_back_to_mock_or_nominatim_label(tmp_path):
    # Whatever AUTH_REQUIRED evidence looks like, it must never claim
    # OFFICIAL authoritative_status or a non-UNKNOWN coordinate semantics.
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.authoritative_status != "OFFICIAL"
    assert evidence.latitude is None and evidence.longitude is None
    assert evidence.coordinate_semantics == ParcelCoordinateSemantics.UNKNOWN


def test_real_provider_parse_uncertain_identifier(tmp_path):
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    ctx = ProviderContext(case_no="X", city="新北市", district="金山區", segment_code="P002-00", parcel_id="P002-00")
    evidence = provider.query_parcel_coordinate(ctx)
    assert evidence.status == ParcelCoordinateStatus.UNKNOWN
    assert evidence.requires_manual_review is True


def test_real_provider_section_not_found_preserves_partial_resolution(tmp_path):
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    ctx = ProviderContext(
        case_no="X", city="新北市", district="金山區", segment_code="P002-00", parcel_id="不存在段489地號",
    )
    evidence = provider.query_parcel_coordinate(ctx)
    assert evidence.status == ParcelCoordinateStatus.UNKNOWN
    assert evidence.county_code == "F"
    assert evidence.town_code == "F25"


# -- Real provider: actual network call path (feature enabled + creds) ---

def test_real_provider_success_path_with_mocked_network(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv(ENV_USERNAME, "test-user")
    monkeypatch.setenv(ENV_TOKEN, "test-token")
    _force_auth_contract_verified(monkeypatch)

    class _FakeResp:
        status = 200
        def read(self):
            return _read_fixture("cad001_success_response.xml")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())

    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.status == ParcelCoordinateStatus.SUCCESS
    assert evidence.latitude == pytest.approx(24.142313)
    assert evidence.longitude == pytest.approx(120.683552)
    assert evidence.source_coordinate_x == pytest.approx(120.683552)
    assert evidence.source_crs == "EPSG:4326"
    assert evidence.coordinate_semantics == ParcelCoordinateSemantics.OFFICIAL_PARCEL_REPRESENTATIVE_POINT
    assert evidence.authoritative_status == "OFFICIAL"


# -- to_target_coordinate_evidence() glue ---------------------------------

def test_to_target_coordinate_evidence_none_for_auth_required(tmp_path):
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert to_target_coordinate_evidence(evidence) is None


def test_to_target_coordinate_evidence_for_success(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv(ENV_USERNAME, "test-user")
    monkeypatch.setenv(ENV_TOKEN, "test-token")
    _force_auth_contract_verified(monkeypatch)

    class _FakeResp:
        status = 200
        def read(self):
            return _read_fixture("cad001_success_response.xml")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())

    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    target = to_target_coordinate_evidence(evidence)
    assert target is not None
    assert target.source_type == CoordinateSourceType.OFFICIAL_GIS
    assert target.authoritative_status == CoordinateAuthoritativeStatus.OFFICIAL
    assert target.precision_level == "PARCEL"
    assert target.latitude == pytest.approx(24.142313)
    assert target.longitude == pytest.approx(120.683552)


# -- Section snapshot coverage (Phase API-2.3H §7) -------------------------

def test_real_provider_non_new_taipei_county_is_out_of_coverage_not_not_found(tmp_path):
    cache = NlscCodeCache(db_path=str(tmp_path / "nlsc_codes.sqlite3"))
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "A", "county_name": "臺北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")
    cache.begin_staging_run("towns")
    cache.stage_towns("A", [{"town_code": "A01", "town_name": "中正區"}])
    cache.promote_towns(source_service="ListTown", source_url="http://x")
    # sections resource IS synced (for New Taipei only) -- 臺北市 was simply
    # never in scope, which is exactly the OUT_OF_COVERAGE distinction.
    cache.begin_staging_run("sections")
    cache.stage_sections("F", "F25", [{"office": "FD", "section_code": "1027", "section_name": "金美段"}])
    cache.promote_sections(source_service="ListLandSection", source_url="http://x")

    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    ctx = ProviderContext(
        case_no="X", city="臺北市", district="中正區", segment_code="P002-00", parcel_id="某段1地號",
    )
    evidence = provider.query_parcel_coordinate(ctx)
    assert evidence.status == ParcelCoordinateStatus.OUT_OF_COVERAGE
    assert evidence.status != ParcelCoordinateStatus.UNKNOWN


# -- Golden Case, auth-gated, this round's expected PASS condition -------

def test_golden_case_auth_gated_result_matches_release_gate_expectation(tmp_path):
    cache = _seeded_cache(tmp_path)
    provider = RealOfficialParcelCoordinateProvider(cache=cache, code_resolver=NlscCadastralCodeResolver(cache=cache))
    evidence = provider.query_parcel_coordinate(_golden_ctx())
    assert evidence.section_code == "1027"
    assert evidence.nlsc_land_no == "04890000"
    assert evidence.status == ParcelCoordinateStatus.AUTH_REQUIRED
    assert evidence.latitude is None
    assert evidence.longitude is None
    assert to_target_coordinate_evidence(evidence) is None
