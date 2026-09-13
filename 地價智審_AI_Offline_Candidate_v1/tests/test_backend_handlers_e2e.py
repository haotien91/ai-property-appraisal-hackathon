# -*- coding: utf-8 -*-
"""
Phase 8A regression tests: end-to-end Lambda handler chain via moto-mocked
DynamoDB. Locks in two CRITICAL/HIGH fixes found during the Phase 8A
Acceptance Review (not just isolated engine tests -- these exercise the
actual Request -> Handler -> Domain Logic -> Response path per that
review's explicit instruction not to accept "class instantiates" as proof
of readiness):

1. collect_data.py previously crashed on EVERY invocation (TypeError:
   list + dict) -- this is the exact request shape documented in
   docs/phase4/frontend_api_contract.md.
2. review.py previously ALWAYS returned a hardcoded empty issues list and
   never called AuditEngine.review() at all.
"""
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

from domain.models import RoadWidthEvidence, RoadWidthEvidenceType  # noqa: E402


@pytest.fixture()
def ddb_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    # Redirects any default-constructed DatasetRegistry (e.g. real-mode
    # RealLandUseProvider -> RealNtpcZoningProvider) away from the actual
    # project's data/ directory -- see providers/dataset_registry.py.
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # STEP5 FINAL GATE Part A (docs/audit/STEP5_FINAL_GATE_REPORT.md):
        # see tests/_aws_mock_reset.py's module docstring for the full
        # root-cause writeup -- module-level table/bucket name constants
        # must be reset here so an earlier test file's env vars never
        # leak into this one.
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_case(cases_module, case_no):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    })}
    return cases_module.create_case(event, None)


class TestCollectDataCrashFix:
    def test_collect_data_does_not_crash_with_typical_request_body(self, ddb_env):
        """Before the fix, this exact request shape (matching
        docs/phase4/frontend_api_contract.md's documented example) raised
        TypeError: can only concatenate list (not "dict") to list, on
        EVERY invocation -- not an edge case."""
        import cases
        import collect_data

        case_no = "REGRESSION-001"
        _make_case(cases, case_no)
        event = {"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}
        resp = collect_data.collect_data(event, None)
        assert resp["statusCode"] == 200

    def test_collect_data_does_not_crash_with_empty_body(self, ddb_env):
        """Even the default/empty-body case crashed before the fix."""
        import cases
        import collect_data

        case_no = "REGRESSION-002"
        _make_case(cases, case_no)
        event = {"pathParameters": {"id": case_no}, "body": "{}"}
        resp = collect_data.collect_data(event, None)
        assert resp["statusCode"] == 200

    def test_stored_factors_are_dict_shaped_for_downstream_handlers(self, ddb_env):
        import cases
        import collect_data
        import case_store

        case_no = "REGRESSION-003"
        _make_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "x", "factor": "y", "raw_value": 1}],
            "comparable_factors": {"c1": [{"field_id": "x", "factor": "y", "raw_value": 2}]},
        })}, None)
        stored = case_store.get_record(case_no, "FACTORS")
        assert isinstance(stored["user_submitted_factors"], dict)
        assert "base_parcel_factors" in stored["user_submitted_factors"]
        assert "comparable_factors" in stored["user_submitted_factors"]


class TestDataProviderModeSwitch:
    """DATA_PROVIDER_MODE governs whether collect_data.py uses the Mock
    providers (default -- fast, offline, what every other test here
    assumes) or the Real (OSM-backed) providers added to support a
    competition-day segment that was never seen before (Phase 1 REQ-006/
    007). collect_data.py reads this env var and builds ALL_PROVIDERS once
    at import time, so these tests reload the module after setting the env
    var via monkeypatch."""

    def _reload_collect_data(self, monkeypatch, mode=None):
        import importlib
        if mode is not None:
            monkeypatch.setenv("DATA_PROVIDER_MODE", mode)
        else:
            monkeypatch.delenv("DATA_PROVIDER_MODE", raising=False)
        import collect_data
        importlib.reload(collect_data)
        return collect_data

    def test_default_mode_uses_mock_providers(self, ddb_env, monkeypatch):
        collect_data = self._reload_collect_data(monkeypatch, mode=None)
        names = {p.provider_name for p in (cls() for cls in collect_data.ALL_PROVIDERS)}
        assert "MockTransportationProvider" in names
        assert "RealTransportationProvider" not in names

    def test_real_mode_uses_real_providers_for_facility_types(self, ddb_env, monkeypatch):
        collect_data = self._reload_collect_data(monkeypatch, mode="real")
        names = {p.provider_name for p in (cls() for cls in collect_data.ALL_PROVIDERS)}
        assert "RealTransportationProvider" in names
        assert "RealCommercialActivityProvider" in names
        # land_use_provider now has a real data source for land_use_zone
        # (RealLandUseProvider -> RealNtpcZoningProvider, a local snapshot
        # query, no network at request time).
        assert "RealLandUseProvider" in names
        assert "MockLandUseProvider" not in names
        # road_provider (Phase 8 Road Width Multi-Evidence Model) now runs
        # RealRoadProvider in real mode too -- it has no confirmed reliable
        # source wired in by default, so it genuinely reports UNKNOWN
        # rather than falling back to Mock's Golden Case numbers.
        assert "RealRoadProvider" in names
        assert "MockRoadProvider" not in names

    def test_mock_mode_uses_mock_land_use_provider(self, ddb_env, monkeypatch):
        collect_data = self._reload_collect_data(monkeypatch, mode="mock")
        names = {p.provider_name for p in (cls() for cls in collect_data.ALL_PROVIDERS)}
        assert "MockLandUseProvider" in names
        assert "RealLandUseProvider" not in names

    def test_unset_mode_is_an_explicit_default_not_a_fallback(self, ddb_env, monkeypatch):
        """Unset DATA_PROVIDER_MODE resolves to "mock" -- a deliberate
        development default, distinct from the fail-fast behavior an
        explicitly-set-but-invalid value must trigger (see the
        TestDataProviderModeFailsFastOnInvalidValue class below)."""
        collect_data = self._reload_collect_data(monkeypatch, mode=None)
        assert collect_data.DATA_PROVIDER_MODE == "mock"

    def test_real_mode_never_geocodes_when_body_supplies_a_coordinate(self, ddb_env, monkeypatch):
        import cases
        import real_facility_provider_base
        import transportation_provider
        collect_data = self._reload_collect_data(monkeypatch, mode="real")

        from osm_facility_lookup import OsmLookupResult
        stub_result = OsmLookupResult(found=False, facility=None, match_count=0, reason="test stub, no network")

        geocode_calls = []
        monkeypatch.setattr(collect_data, "geocode", lambda place: geocode_calls.append(place) or None)
        # Real providers WOULD make live Overpass calls with a real
        # coordinate present -- not what this test is checking, and not
        # something the offline suite should depend on. Only the geocode
        # short-circuit is under test here.
        monkeypatch.setattr(real_facility_provider_base, "find_nearest_facility", lambda *a, **k: stub_result)
        monkeypatch.setattr(transportation_provider, "find_nearest_facility", lambda *a, **k: stub_result)
        monkeypatch.setattr(real_facility_provider_base.time, "sleep", lambda s: None)

        case_no = "REGRESSION-COORD-001"
        _make_case(cases, case_no)
        resp = collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "center_coordinate": {"latitude": 25.0138, "longitude": 121.4629},
        })}, None)
        assert resp["statusCode"] == 200
        assert geocode_calls == []

    def test_real_mode_geocodes_from_case_meta_when_no_coordinate_supplied(self, ddb_env, monkeypatch):
        import cases
        collect_data = self._reload_collect_data(monkeypatch, mode="real")

        geocode_calls = []

        def fake_geocode(place):
            geocode_calls.append(place)
            return None  # simulate lookup failure -- must not crash the pipeline

        monkeypatch.setattr(collect_data, "geocode", fake_geocode)

        case_no = "REGRESSION-COORD-002"
        _make_case(cases, case_no)  # _make_case sets city=新北市 district=金山區 segment_scope=測試區段
        resp = collect_data.collect_data({"pathParameters": {"id": case_no}, "body": "{}"}, None)
        assert resp["statusCode"] == 200
        assert len(geocode_calls) == 1
        assert "新北市" in geocode_calls[0] and "金山區" in geocode_calls[0]

    def test_mock_mode_never_calls_geocode_even_with_no_coordinate(self, ddb_env, monkeypatch):
        import cases
        collect_data = self._reload_collect_data(monkeypatch, mode=None)

        geocode_calls = []
        monkeypatch.setattr(collect_data, "geocode", lambda place: geocode_calls.append(place) or None)

        case_no = "REGRESSION-COORD-003"
        _make_case(cases, case_no)
        resp = collect_data.collect_data({"pathParameters": {"id": case_no}, "body": "{}"}, None)
        assert resp["statusCode"] == 200
        assert geocode_calls == []


class TestDataProviderModeFailsFastOnInvalidValue:
    """DATA_PROVIDER_MODE must never silently fall back to Mock for an
    unrecognized value -- that would violate this project's "real path
    must never fall back to Mock" invariant by making a misconfigured
    real-mode deployment look like it's working while quietly serving
    Golden Case numbers. Only "mock" and "real" exist; unset defaults to
    "mock" (see TestDataProviderModeSwitch.test_unset_mode_is_an_explicit_
    default_not_a_fallback above), but anything else -- typo, "offline",
    "hybrid", an empty string -- must raise immediately at module-load
    time (the Lambda cold-start / test-reload boundary), not be silently
    coerced into Mock at first request."""

    def _reload(self, monkeypatch, mode):
        import importlib
        monkeypatch.setenv("DATA_PROVIDER_MODE", mode)
        import collect_data
        importlib.reload(collect_data)
        return collect_data

    @pytest.mark.parametrize("bad_mode", ["typo", "offline", "hybrid", "", "REALX", "raел"])
    def test_invalid_mode_raises_instead_of_falling_back_to_mock(self, ddb_env, monkeypatch, bad_mode):
        import importlib
        monkeypatch.setenv("DATA_PROVIDER_MODE", bad_mode)
        import collect_data
        with pytest.raises(RuntimeError, match="INVALID_DATA_PROVIDER_MODE"):
            importlib.reload(collect_data)
        # Leave the module in a known-good state so later tests in this
        # process (module reload mutates shared module-level state) aren't
        # affected by this deliberately-broken reload attempt.
        monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
        importlib.reload(collect_data)

    def test_invalid_mode_never_produces_a_working_all_providers_list(self, ddb_env, monkeypatch):
        """Direct proof (not just "an exception was raised somewhere"):
        after an invalid-mode reload fails, ALL_PROVIDERS was never
        (re)assigned to Mock's provider list as a silent fallback -- the
        exception fires before that assignment line ever runs, so no
        request could ever be served (let alone quietly served Golden Mock
        values) under a misconfigured real-mode deployment."""
        import importlib
        collect_data = self._reload(monkeypatch, "real")
        real_providers_before = list(collect_data.ALL_PROVIDERS)

        monkeypatch.setenv("DATA_PROVIDER_MODE", "hybrid")
        with pytest.raises(RuntimeError, match="INVALID_DATA_PROVIDER_MODE"):
            importlib.reload(collect_data)
        # ALL_PROVIDERS is untouched by the failed reload -- specifically
        # NOT silently reassigned to the Mock list.
        assert collect_data.ALL_PROVIDERS == real_providers_before

        monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
        importlib.reload(collect_data)


class TestCrossFormIndependentFieldIdentity:
    """Regression tests for the Phase 8A cross-form data model fix:
    表5-2's regional_total_adjustment_{cid} and 表4's
    region_adjustment_rate_{cid} are now two INDEPENDENT
    FieldCompletion records (see engine/form_completion_engine.py), not
    one field read twice into two dict keys. Before this fix, tampering
    with either side's stored value was undetectable because both
    "submitted" values were derived from the same single source field --
    a tautological A==A comparison, not a genuine two-sided check."""

    def _seed_full_case(self, case_no):
        import cases
        import collect_data
        import complete_form
        import case_store

        _make_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)
        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_base_factors"] = [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}]
        factors["regional_comparable_factors"] = {"comp1": [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}]}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

    def test_two_independent_field_ids_are_actually_stored(self, ddb_env):
        """The core data-model assertion: 表5-2 and 表4 must be two
        distinct field_id records, not one field aliased under two names."""
        import case_store

        case_no = "CROSSFORM-IDENTITY-001"
        self._seed_full_case(case_no)
        fc = case_store.get_record(case_no, "FORM_COMPLETION")
        f52 = [f for f in fc["fields"] if f["field_id"] == "regional_total_adjustment_comp1"]
        f4 = [f for f in fc["fields"] if f["field_id"] == "region_adjustment_rate_comp1"]
        assert len(f52) == 1 and f52[0]["form"] == "表5-2"
        assert len(f4) == 1 and f4[0]["form"] == "表4"

    def test_normal_case_produces_no_cross_form_false_positive(self, ddb_env):
        import review

        case_no = "CROSSFORM-NORMAL-001"
        self._seed_full_case(case_no)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["error_count"] == 0
        assert body["inconsistent_count"] == 0

    def test_tampering_form5_2_only_is_detected(self, ddb_env):
        """Modifies ONLY the 表5-2 stored field; 表4 is left untouched."""
        import review
        import case_store

        case_no = "CROSSFORM-TAMPER-52-001"
        self._seed_full_case(case_no)
        fc = case_store.get_record(case_no, "FORM_COMPLETION")
        for f in fc["fields"]:
            if f["field_id"] == "regional_total_adjustment_comp1":
                assert f["final_value"] == "0"  # sanity: true value before tampering
                f["final_value"] = "3.00"
        case_store.put_record(case_no, "FORM_COMPLETION", fc)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["inconsistent_count"] >= 1
        inconsistent = [i for i in body["issues"] if i["issue_type"] == "Inconsistent"]
        assert len(inconsistent) == 1
        assert inconsistent[0]["submitted_value"] != inconsistent[0]["expected_value"]
        assert inconsistent[0]["severity"] == "CRITICAL"
        assert "base_parcel_comparison_price" in inconsistent[0]["downstream_impact"]

    def test_tampering_form4_only_is_detected(self, ddb_env):
        """Reverse direction: modifies ONLY 表4's stored field; 表5-2 is
        left untouched. Proves the check is symmetric, not special-cased
        to only catch tampering on one particular side."""
        import review
        import case_store

        case_no = "CROSSFORM-TAMPER-4-001"
        self._seed_full_case(case_no)
        fc = case_store.get_record(case_no, "FORM_COMPLETION")
        for f in fc["fields"]:
            if f["field_id"] == "region_adjustment_rate_comp1":
                assert f["final_value"] == "0"
                f["final_value"] = "5.00"
        case_store.put_record(case_no, "FORM_COMPLETION", fc)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["inconsistent_count"] >= 1
        inconsistent = [i for i in body["issues"] if i["issue_type"] == "Inconsistent"]
        assert len(inconsistent) == 1
        assert inconsistent[0]["submitted_value"] != inconsistent[0]["expected_value"]

    def test_expected_vs_submitted_remain_distinct_concepts(self, ddb_env):
        """STEP 9: confirms the cross-form check (submitted-vs-submitted)
        and grade correctness checks (submitted-vs-RuleEngine-expected)
        remain separate, non-conflated mechanisms -- reusing the existing
        AuditEngine architecture rather than building a second validator."""
        import review
        import case_store

        case_no = "CROSSFORM-EXPECTED-DISTINCT-001"
        self._seed_full_case(case_no)
        # Tamper the individual factor's grade-derived value (RuleEngine-vs-
        # submitted concern) -- must surface as a separate Error, not get
        # mixed into the cross-form Inconsistent check.
        fc = case_store.get_record(case_no, "FORM_COMPLETION")
        for f in fc["fields"]:
            if f["field_id"] == "individual_land_depth_differential_rate_comp1":
                f["adjustment"] = "42.00"
        case_store.put_record(case_no, "FORM_COMPLETION", fc)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["error_count"] >= 1  # grade/adjustment-vs-Expected mismatch
        assert body["inconsistent_count"] == 0  # cross-form sides still agree, untouched
        error_issues = [i for i in body["issues"] if i["issue_type"] == "Error"]
        assert any(i["field"] == "individual_land_depth_differential_rate_comp1" for i in error_issues)


class TestReviewActuallyRunsAuditLogic:
    def _seed_full_case(self, case_no):
        import cases
        import collect_data
        import complete_form
        import case_store

        _make_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)
        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_base_factors"] = [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}]
        factors["regional_comparable_factors"] = {"comp1": [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}]}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

    def test_review_returns_non_empty_issues_not_hardcoded_stub(self, ddb_env):
        """Before the fix, review.py ALWAYS returned {"issues": []}
        regardless of input -- AuditEngine was instantiated but its
        .review() method was never called."""
        import review

        case_no = "REGRESSION-004"
        self._seed_full_case(case_no)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert len(body["issues"]) > 0
        assert "note" not in body  # the old stub's telltale hardcoded field

    def test_review_detects_a_deliberately_corrupted_stored_value(self, ddb_env):
        """Proves the check is non-vacuous: tampers with the stored
        FORM_COMPLETION result and confirms review.py's re-derivation
        actually disagrees with the tampered value."""
        import review
        import case_store

        case_no = "REGRESSION-005"
        self._seed_full_case(case_no)

        fc = case_store.get_record(case_no, "FORM_COMPLETION")
        for f in fc["fields"]:
            if f["field_id"] == "individual_land_depth_differential_rate_comp1":
                assert f["adjustment"] == "1"  # sanity: true Golden-verified value before tampering
                f["adjustment"] = "99.00"
        case_store.put_record(case_no, "FORM_COMPLETION", fc)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["error_count"] >= 1
        detected = [i for i in body["issues"] if i["field"] == "individual_land_depth_differential_rate_comp1"]
        assert len(detected) == 1
        assert detected[0]["issue_type"] == "Error"
        assert detected[0]["submitted_value"] == "99.00"
        assert detected[0]["expected_value"] == "1"

    def test_review_result_is_persisted(self, ddb_env):
        import review
        import case_store

        case_no = "REGRESSION-006"
        self._seed_full_case(case_no)
        review.review({"pathParameters": {"id": case_no}}, None)
        stored = case_store.get_record(case_no, "REVIEW_RESULT")
        assert stored is not None
        assert len(stored["issues"]) > 0


class TestLandUseRatioBackendE2E:
    """Proves the full API payload -> case_reconstruction.py ->
    SubmittedFormData -> AuditEngine.review() data flow for 表1's
    使用分區/建蔽率/容積率/plan identification (added in the LandUseRatio
    backend-wiring round). Golden Case's concrete values (70/240/jinshan/
    第二種商業區) appear ONLY in this test file's request bodies -- never
    hardcoded in collect_data.py/case_reconstruction.py/review.py
    themselves, which only know field_id/dict-key names."""

    def _seed(self, case_no, building_coverage_rate, floor_area_ratio,
              plan_id="jinshan", confirmed_plan_name="金山都市計畫"):
        import cases
        import collect_data
        import complete_form
        import case_store

        _make_case(cases, case_no)
        collect_data_body = {
            "confirmed_plan_name": confirmed_plan_name,
            "base_parcel_factors": [
                {"field_id": "individual_zoning_designation", "factor": "使用分區", "raw_value": "第二種商業區"},
                {"field_id": "individual_building_coverage_ratio", "factor": "建蔽率",
                 "raw_value": building_coverage_rate, "unit": "%"},
                {"field_id": "individual_floor_area_ratio", "factor": "容積率",
                 "raw_value": floor_area_ratio, "unit": "%"},
            ],
            "comparable_factors": {"comp1": []},
        }
        if plan_id is not None:
            collect_data_body["plan_id"] = plan_id
        collect_data.collect_data(
            {"pathParameters": {"id": case_no}, "body": json.dumps(collect_data_body)}, None,
        )
        factors = case_store.get_record(case_no, "FACTORS")
        # regional_base_factors is a separate, pre-existing gap (collect_data.py
        # does not yet persist Provider-resolved regional points into it --
        # see docs/backlog.md) -- seeded directly here the same way every
        # other cross-form E2E test above already does, so this test isolates
        # the land-use-ratio data flow this round is actually responsible for.
        factors["regional_base_factors"] = [
            {"field_id": "regional_land_use_zone", "factor": "使用分區(使用地類別)", "raw_value": "第二種商業區"},
        ]
        factors["regional_comparable_factors"] = {"comp1": []}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        return factors

    def test_plan_identification_persisted_by_collect_data(self, ddb_env):
        """collect_data.py must persist plan_id (previously computed into
        ProviderContext and then silently dropped once the request
        returned) so a later, separate review() request can read it back."""
        import case_store

        case_no = "LANDUSE-PERSIST-001"
        self._seed(case_no, building_coverage_rate=70, floor_area_ratio=240)
        stored = case_store.get_record(case_no, "FACTORS")
        assert stored["plan_identification"]["internal_plan_id"] == "jinshan"
        assert stored["plan_identification"]["confirmed_plan_name"] == "金山都市計畫"
        assert stored["plan_identification"]["plan_identification_source"] == "MANUAL_INPUT"

    def test_golden_payload_flows_through_to_passed_building_coverage_and_far(self, ddb_env):
        """request payload -> reconstruction -> AuditEngine ->
        Building Coverage / FAR Issue: Golden 70/240 -> PASS."""
        import review

        case_no = "LANDUSE-GOLDEN-001"
        self._seed(case_no, building_coverage_rate=70, floor_area_ratio=240)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        bcr = [i for i in body["issues"] if i["field"] == "building_coverage_ratio"]
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"]
        assert len(bcr) == 1 and bcr[0]["issue_type"] == "Passed"
        assert len(far) == 1 and far[0]["issue_type"] == "Passed"
        assert far[0]["explanation_data"]["check_type"] == "PASSED_CHECK"

    def test_error_payload_60_300_flows_through_to_inconsistent(self, ddb_env):
        """request payload -> reconstruction -> AuditEngine ->
        Building Coverage / FAR Issue: Error 60/300 -> INCONSISTENT."""
        import review

        case_no = "LANDUSE-ERROR-001"
        self._seed(case_no, building_coverage_rate=60, floor_area_ratio=300)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        bcr = [i for i in body["issues"] if i["field"] == "building_coverage_ratio"][0]
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"][0]
        assert bcr["issue_type"] == "Inconsistent"
        assert bcr["explanation_data"]["check_type"] == "BUILDING_COVERAGE_RATE_INCONSISTENT"
        assert far["issue_type"] == "Inconsistent"
        assert far["explanation_data"]["check_type"] == "FLOOR_AREA_RATIO_INCONSISTENT"

    def test_missing_plan_id_reports_unresolved_not_inconsistent_via_backend(self, ddb_env):
        """A real case with no plan_id supplied at collect-data time must
        flow through as internal_plan_id=None end-to-end, surfacing
        ZONING_PLAN_UNRESOLVED -- never guessed, never mislabeled as
        FLOOR_AREA_RATIO_INCONSISTENT."""
        import review

        case_no = "LANDUSE-NOPLAN-001"
        self._seed(case_no, building_coverage_rate=70, floor_area_ratio=240,
                   plan_id=None, confirmed_plan_name=None)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"][0]
        assert far["explanation_data"]["check_type"] == "ZONING_PLAN_UNRESOLVED"
        assert far["issue_type"] == "Warning"


class TestLandUseRatioProviderDrivenBackendE2E:
    """Proves regional_base_factors["regional_land_use_zone"] is actually
    PRODUCED by collect_data.py -> RealLandUseProvider -> NtpcZoningProvider
    -> FACTORS.regional_base_factors, not manually injected into the stored
    record the way TestLandUseRatioBackendE2E above (and every other E2E
    class's _seed_full_case) does. Only the zoning lookup's own I/O (the
    local shapefile snapshot) is stubbed -- the same pattern already
    established and documented in tests/test_land_use_provider.py's module
    docstring ("RealLandUseProvider's zoning lookup is stubbed (offline, no
    filesystem/DatasetRegistry touch)"). Everything downstream of that one
    I/O boundary -- zone normalization, LandUseRatioEngine resolution,
    NormalizedDataPoint construction, and this round's new
    collect_data._to_regional_factor() mapping -- runs for real."""

    def _reload_real_land_use_only(self, monkeypatch):
        import importlib
        monkeypatch.setenv("DATA_PROVIDER_MODE", "real")
        import collect_data
        importlib.reload(collect_data)
        # Real mode also activates 5 OSM-backed facility/transportation
        # providers unrelated to this round's concern (land-use zoning
        # persistence) -- excluded here (rather than stubbed across each of
        # their modules) to keep this test fast, offline, and deterministic
        # without dragging in unrelated live-network stubbing plumbing.
        monkeypatch.setattr(collect_data, "ALL_PROVIDERS", [collect_data.RealLandUseProvider])
        return collect_data

    def _stub_zoning(self, monkeypatch, zone_name):
        import land_use_provider
        from domain.models import ZoningQueryResult

        class _StubZoningProvider:
            def query(self, center):
                return ZoningQueryResult(
                    zone_name=zone_name, plan_name=None, matched_polygon_count=1,
                    dataset_version="2026-09-03", requires_manual_review=False,
                )

        monkeypatch.setattr(land_use_provider, "RealNtpcZoningProvider", _StubZoningProvider)

    def _seed(self, monkeypatch, case_no, submitted_bcr, submitted_far, plan_id="jinshan"):
        import cases
        import complete_form
        import case_store

        collect_data_module = self._reload_real_land_use_only(monkeypatch)
        self._stub_zoning(monkeypatch, "第二種商業區")

        _make_case(cases, case_no)
        body = {
            "center_coordinate": {"latitude": 25.0138, "longitude": 121.4629},
            "plan_id": plan_id, "confirmed_plan_name": "金山都市計畫",
            "base_parcel_factors": [
                {"field_id": "individual_building_coverage_ratio", "factor": "建蔽率",
                 "raw_value": submitted_bcr, "unit": "%"},
                {"field_id": "individual_floor_area_ratio", "factor": "容積率",
                 "raw_value": submitted_far, "unit": "%"},
            ],
            "comparable_factors": {"comp1": []},
        }
        resp = collect_data_module.collect_data(
            {"pathParameters": {"id": case_no}, "body": json.dumps(body)}, None,
        )
        assert resp["statusCode"] == 200

        stored = case_store.get_record(case_no, "FACTORS")
        # The core proof this test class exists for: regional_land_use_zone
        # came from RealLandUseProvider's own fetch(), through collect_data's
        # real persistence path -- not a manually-seeded test fixture value.
        regional = {f["field_id"]: f for f in stored["regional_base_factors"]}
        assert regional["regional_land_use_zone"]["raw_value"] == "第二種商業區"
        assert regional["regional_land_use_zone"]["evidence"]["source_type"] == "GovernmentOpenData"
        assert "新北市政府城鄉發展局" in regional["regional_land_use_zone"]["evidence"]["source"]

        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_comparable_factors"] = {"comp1": []}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        return stored

    def test_golden_provider_driven_flows_to_passed(self, ddb_env, monkeypatch):
        """Provider resolves 第二種商業區; submitted BCR=70/FAR=240 (both
        match the Provider's own independently-resolved reference) ->
        PASSED/PASSED."""
        import review

        case_no = "LANDUSE-PROVIDER-GOLDEN-001"
        self._seed(monkeypatch, case_no, submitted_bcr=70, submitted_far=240)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        bcr = [i for i in body["issues"] if i["field"] == "building_coverage_ratio"][0]
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"][0]
        assert bcr["issue_type"] == "Passed"
        assert far["issue_type"] == "Passed"

    def test_error_provider_driven_flows_to_inconsistent(self, ddb_env, monkeypatch):
        """Provider resolves 第二種商業區; submitted BCR=60/FAR=300 (both
        disagree with the Provider's reference) -> INCONSISTENT/INCONSISTENT."""
        import review

        case_no = "LANDUSE-PROVIDER-ERROR-001"
        self._seed(monkeypatch, case_no, submitted_bcr=60, submitted_far=300)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        bcr = [i for i in body["issues"] if i["field"] == "building_coverage_ratio"][0]
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"][0]
        assert bcr["issue_type"] == "Inconsistent"
        assert far["issue_type"] == "Inconsistent"

    def test_submitted_value_never_overwritten_by_provider_reference(self, ddb_env, monkeypatch):
        """The critical regression this round exists to lock in: submitted
        FAR=300 must survive collect_data -> complete_form -> review
        UNCHANGED, even though the Provider's own independently-resolved
        reference for the same zone is 240. If Provider output ever
        silently clobbers the user's submission, submitted_value would come
        back as "240" instead of "300" and this test must fail."""
        import review

        case_no = "LANDUSE-PROVIDER-NOOVERWRITE-001"
        self._seed(monkeypatch, case_no, submitted_bcr=70, submitted_far=300)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"][0]
        assert far["submitted_value"] == "300"
        assert far["expected_value"] == "240"
        assert far["issue_type"] == "Inconsistent"

    def test_zoning_not_found_is_unknown_not_fallback_to_mock(self, ddb_env, monkeypatch):
        """Real mode's zoning lookup genuinely fails (zone_name=None,
        matched_polygon_count=0 -- the same "no polygon contains this
        point" outcome RealNtpcZoningProvider itself returns for a
        coordinate outside its coverage). This must surface as
        LAND_USE_RATIO_RULE_UNAVAILABLE/MANUAL_REVIEW_REQUIRED-style honest
        unresolvable state -- NEVER silently fall back to MockLandUseProvider's
        Golden Case zone (第二種商業區), even though that value happens to
        be sitting right there in the same module."""
        import cases
        import complete_form
        import case_store
        import review
        from domain.models import ZoningQueryResult

        collect_data_module = self._reload_real_land_use_only(monkeypatch)
        import land_use_provider

        class _StubZoningProviderNotFound:
            def query(self, center):
                return ZoningQueryResult(zone_name=None, plan_name=None, matched_polygon_count=0,
                                          notes="查無涵蓋此座標之都市計畫分區圖資")

        monkeypatch.setattr(land_use_provider, "RealNtpcZoningProvider", _StubZoningProviderNotFound)

        case_no = "LANDUSE-PROVIDER-NOTFOUND-001"
        _make_case(cases, case_no)
        body = {
            "center_coordinate": {"latitude": 21.9, "longitude": 121.9},  # outside any 新北市 polygon
            "plan_id": "jinshan",
            "base_parcel_factors": [
                {"field_id": "individual_building_coverage_ratio", "factor": "建蔽率", "raw_value": 70, "unit": "%"},
                {"field_id": "individual_floor_area_ratio", "factor": "容積率", "raw_value": 240, "unit": "%"},
            ],
            "comparable_factors": {"comp1": []},
        }
        resp = collect_data_module.collect_data(
            {"pathParameters": {"id": case_no}, "body": json.dumps(body)}, None,
        )
        assert resp["statusCode"] == 200
        stored = case_store.get_record(case_no, "FACTORS")
        # No fabricated regional_land_use_zone entry at all -- not even an
        # UNKNOWN placeholder, and definitely not Mock's 第二種商業區.
        field_ids = {f["field_id"] for f in stored["regional_base_factors"]}
        assert "regional_land_use_zone" not in field_ids

        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_comparable_factors"] = {"comp1": []}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        bcr = [i for i in body["issues"] if i["field"] == "building_coverage_ratio"][0]
        far = [i for i in body["issues"] if i["field"] == "floor_area_ratio"][0]
        assert bcr["explanation_data"]["check_type"] == "LAND_USE_RATIO_RULE_UNAVAILABLE"
        assert far["explanation_data"]["check_type"] == "LAND_USE_RATIO_RULE_UNAVAILABLE"
        assert bcr["issue_type"] == "Missing"


class TestUrbanPlanIdSourceMismatchAudit:
    """TEST 8 (this round's urban-plan-boundary integration): a human-
    supplied plan_id that DISAGREES with what the submitted coordinate
    itself auto-resolves to must still win (human override), but the
    discrepancy must be visible in the persisted plan_identification block
    -- never silently accepted as if both agreed. Stubs
    RealUrbanPlanBoundaryProvider the same way TestLandUseRatioProvider
    DrivenBackendE2E above stubs RealNtpcZoningProvider (offline, no
    DatasetRegistry/filesystem touch)."""

    def _reload_with_stubbed_urban_plan(self, monkeypatch, auto_status, auto_plan_id, auto_plan_name):
        import importlib
        monkeypatch.setenv("DATA_PROVIDER_MODE", "real")
        import collect_data
        importlib.reload(collect_data)
        monkeypatch.setattr(collect_data, "ALL_PROVIDERS", [collect_data.RealLandUseProvider])

        from domain.models import UrbanPlanBoundaryResult, UrbanPlanStatus

        class _StubUrbanPlanBoundaryProvider:
            def resolve_urban_plan(self, center):
                return UrbanPlanBoundaryResult(
                    urban_plan_status=UrbanPlanStatus(auto_status),
                    plan_id=auto_plan_id, plan_name=auto_plan_name,
                    source_code="64/28", source_version="test",
                )

        monkeypatch.setattr(collect_data, "RealUrbanPlanBoundaryProvider", _StubUrbanPlanBoundaryProvider)
        return collect_data

    def _seed(self, monkeypatch, case_no, human_plan_id, auto_status="INSIDE",
              auto_plan_id="jinshan", auto_plan_name="金山都市計畫"):
        import cases
        import case_store

        collect_data_module = self._reload_with_stubbed_urban_plan(
            monkeypatch, auto_status, auto_plan_id, auto_plan_name
        )
        _make_case(cases, case_no)
        body = {
            "center_coordinate": {"latitude": 25.2214, "longitude": 121.6360},
            "plan_id": human_plan_id,
            "base_parcel_factors": [],
            "comparable_factors": {"comp1": []},
        }
        resp = collect_data_module.collect_data(
            {"pathParameters": {"id": case_no}, "body": json.dumps(body)}, None,
        )
        assert resp["statusCode"] == 200
        return case_store.get_record(case_no, "FACTORS")["plan_identification"]

    def test_conflicting_human_plan_id_flagged_not_silently_accepted(self, ddb_env, monkeypatch):
        # human_plan_id is a DIFFERENT plan than what the coordinate itself
        # resolves to (jinshan, per _seed's default auto_plan_id).
        stored = self._seed(monkeypatch, "URBANPLAN-MISMATCH-001", human_plan_id="ntpc_plan_37_3")
        # Human override still wins for the value actually used downstream...
        assert stored["internal_plan_id"] == "ntpc_plan_37_3"
        assert stored["plan_identification_source"] == "MANUAL_INPUT"
        # ...but the disagreement with the coordinate's own resolution
        # (jinshan) must be visible, not swallowed.
        assert stored["plan_id_source_mismatch"] is True
        assert stored["urban_plan_id"] == "jinshan"
        assert stored["urban_plan_status"] == "INSIDE"

    def test_agreeing_human_plan_id_is_not_flagged(self, ddb_env, monkeypatch):
        stored = self._seed(monkeypatch, "URBANPLAN-AGREE-001", human_plan_id="jinshan")
        assert stored["internal_plan_id"] == "jinshan"
        assert stored["plan_id_source_mismatch"] is False

    def test_no_human_plan_id_uses_auto_resolution_and_is_not_flagged_mismatch(self, ddb_env, monkeypatch):
        stored = self._seed(monkeypatch, "URBANPLAN-AUTO-001", human_plan_id=None)
        assert stored["internal_plan_id"] == "jinshan"
        assert stored["plan_identification_source"] == "AUTO_COORDINATE_RESOLVED"
        assert stored["plan_id_source_mismatch"] is False

    def test_ambiguous_auto_resolution_never_supplies_a_plan_id(self, ddb_env, monkeypatch):
        """When the coordinate itself is ambiguous/outside/unknown, no
        plan_id may be silently guessed -- internal_plan_id stays None if
        the human also supplied none."""
        stored = self._seed(
            monkeypatch, "URBANPLAN-AMBIGUOUS-001", human_plan_id=None,
            auto_status="AMBIGUOUS", auto_plan_id=None, auto_plan_name=None,
        )
        assert stored["internal_plan_id"] is None
        assert stored["plan_identification_source"] is None
        assert stored["urban_plan_status"] == "AMBIGUOUS"


class TestRoadWidthBackendE2E:
    """Proves request -> collect_data -> RealRoadProvider -> FACTORS ->
    complete_form -> review -> AuditEngine -> RoadWidthValidator is
    genuinely wired end-to-end. Golden Case's 18m (查估書表範本.pdf's
    worked example, no independently-confirmable official source -- see
    docs/backlog.md's "Road Width Multi-Evidence Model" survey) is NEVER
    presented as an official reference here -- every "reliable evidence"
    scenario below injects synthetic evidence via evidence_sources DI and
    is explicitly labeled TEST_INJECTED_REFERENCE, not treated as Golden
    Case fact."""

    # Explicit marker per this round's instruction item 6: any width value
    # used below as a "reliable reference" is synthetic test fixture data,
    # not a claim about 中山路's actual/official width.
    TEST_INJECTED_REFERENCE = "TEST_INJECTED_REFERENCE"

    def _reload_real_road_only(self, monkeypatch, evidence_sources):
        import importlib
        monkeypatch.setenv("DATA_PROVIDER_MODE", "real")
        import collect_data
        importlib.reload(collect_data)
        import road_provider

        class _InjectedRealRoadProvider:
            provider_name = "RealRoadProvider"

            def __new__(cls):
                return road_provider.RealRoadProvider(evidence_sources=evidence_sources)

        # Only RealRoadProvider is registered (not the 5 OSM-backed
        # facility/transportation providers real mode also activates) --
        # keeps this test fast/offline/deterministic, same rationale as
        # TestLandUseRatioProviderDrivenBackendE2E above. Patched on both
        # names collect_data.py's own _resolve_road_width_evidence() checks
        # (`ProviderCls is RealRoadProvider`) and iterates
        # (ALL_PROVIDERS) so the identity check still matches.
        monkeypatch.setattr(collect_data, "RealRoadProvider", _InjectedRealRoadProvider)
        monkeypatch.setattr(collect_data, "ALL_PROVIDERS", [_InjectedRealRoadProvider])
        return collect_data

    def _seed(self, monkeypatch, case_no, submitted_main_road_width, evidence_sources):
        import cases
        import complete_form
        import case_store

        collect_data_module = self._reload_real_road_only(monkeypatch, evidence_sources)

        _make_case(cases, case_no)
        body = {
            "submitted_main_road_width": submitted_main_road_width,
            "base_parcel_factors": [], "comparable_factors": {"comp1": []},
        }
        resp = collect_data_module.collect_data(
            {"pathParameters": {"id": case_no}, "body": json.dumps(body)}, None,
        )
        assert resp["statusCode"] == 200

        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_comparable_factors"] = {"comp1": []}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        return factors

    def _review_road_width_issue(self, case_no):
        import review
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        return [i for i in body["issues"] if i["field"] == "main_road_width"][0]

    def test_a_no_evidence_is_unavailable_not_fallback_mock(self, ddb_env, monkeypatch):
        case_no = "ROADWIDTH-NOEVIDENCE-001"
        factors = self._seed(monkeypatch, case_no, submitted_main_road_width=18, evidence_sources=[])
        # No fabricated evidence entry at all -- collect_data persisted an
        # empty list, not a placeholder.
        assert factors["road_width_evidence"] == []

        issue = self._review_road_width_issue(case_no)
        assert issue["explanation_data"]["check_type"] == "ROAD_WIDTH_UNAVAILABLE"
        assert issue["issue_type"] == "Missing"
        # Never Golden Case's Mock numbers leaking in as a fabricated
        # resolved reference.
        assert issue["expected_value"] is None

    def test_b_reliable_evidence_matches_submission_passes(self, ddb_env, monkeypatch):
        """submitted=18, TEST_INJECTED_REFERENCE=18 -> PASSED."""
        source = lambda ctx: [RoadWidthEvidence(
            road_name="測試道路", width_m=18, evidence_type=RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE,
            source_name=self.TEST_INJECTED_REFERENCE, confidence="高",
        )]
        case_no = "ROADWIDTH-PASS-001"
        factors = self._seed(monkeypatch, case_no, submitted_main_road_width=18, evidence_sources=[source])
        assert len(factors["road_width_evidence"]) == 1
        assert factors["road_width_evidence"][0]["source_name"] == self.TEST_INJECTED_REFERENCE

        issue = self._review_road_width_issue(case_no)
        assert issue["issue_type"] == "Passed"
        assert issue["explanation_data"]["check_type"] == "PASSED_CHECK"

    def test_c_reliable_evidence_mismatch_is_inconsistent(self, ddb_env, monkeypatch):
        """submitted=12, TEST_INJECTED_REFERENCE=18 -> ROAD_WIDTH_INCONSISTENT,
        with submitted_value=12 and expected/resolved_value=18 both
        independently preserved (never overwritten by the reference)."""
        source = lambda ctx: [RoadWidthEvidence(
            road_name="測試道路", width_m=18, evidence_type=RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE,
            source_name=self.TEST_INJECTED_REFERENCE, confidence="高",
        )]
        case_no = "ROADWIDTH-MISMATCH-001"
        self._seed(monkeypatch, case_no, submitted_main_road_width=12, evidence_sources=[source])

        issue = self._review_road_width_issue(case_no)
        assert issue["issue_type"] == "Inconsistent"
        assert issue["explanation_data"]["check_type"] == "ROAD_WIDTH_INCONSISTENT"
        assert issue["submitted_value"] == "12"
        assert issue["expected_value"] == "18"

    def test_d_evidence_conflict_is_never_reported_as_inconsistent(self, ddb_env, monkeypatch):
        """urban plan=20, external map=18, geometric estimate=17.5 ->
        ROAD_WIDTH_EVIDENCE_CONFLICT / MANUAL_REVIEW_REQUIRED, never
        ROAD_WIDTH_INCONSISTENT."""
        source = lambda ctx: [
            RoadWidthEvidence(road_name="測試道路", width_m=20,
                               evidence_type=RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH,
                               source_name=self.TEST_INJECTED_REFERENCE, confidence="高"),
            RoadWidthEvidence(road_name="測試道路", width_m=18,
                               evidence_type=RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE,
                               source_name=self.TEST_INJECTED_REFERENCE, confidence="中"),
            RoadWidthEvidence(road_name="測試道路", width_m="17.5",
                               evidence_type=RoadWidthEvidenceType.GEOMETRIC_ESTIMATE,
                               source_name=self.TEST_INJECTED_REFERENCE, confidence="低"),
        ]
        case_no = "ROADWIDTH-CONFLICT-001"
        factors = self._seed(monkeypatch, case_no, submitted_main_road_width=18, evidence_sources=[source])
        assert len(factors["road_width_evidence"]) == 3

        issue = self._review_road_width_issue(case_no)
        assert issue["issue_type"] == "Warning"
        assert issue["explanation_data"]["check_type"] == "ROAD_WIDTH_EVIDENCE_CONFLICT"
        assert issue["issue_type"] != "Inconsistent"
        assert issue["recommendation_data"]["requires_human_review"] is True

    def test_real_mode_never_produces_golden_case_numbers_without_evidence(self, ddb_env, monkeypatch):
        """Locks in item 5: DATA_PROVIDER_MODE=real with RealRoadProvider
        genuinely having no data must never surface 18m/12m anywhere in
        the persisted FACTORS or the review result, unless evidence
        itself actually provided it."""
        case_no = "ROADWIDTH-NOFALLBACK-001"
        self._seed(monkeypatch, case_no, submitted_main_road_width=None, evidence_sources=[])
        issue = self._review_road_width_issue(case_no)
        assert issue["expected_value"] is None
        assert issue["explanation_data"]["check_type"] == "ROAD_WIDTH_UNAVAILABLE"
        assert issue["issue_type"] == "Missing"
