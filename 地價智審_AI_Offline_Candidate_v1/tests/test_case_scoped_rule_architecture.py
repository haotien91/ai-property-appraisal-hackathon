# -*- coding: utf-8 -*-
"""
Regression tests for the Case-Scoped Rule Architecture
(docs/audit/CASE_SCOPED_RULE_ARCHITECTURE_REPORT.md).

Exercises the REAL production path -- analyze.py / complete_form.py /
review.py -> rule_engine_factory.build_rule_engine_for_case() ->
case_rule_repository.DynamoCaseRuleRepository -- against moto-mocked
DynamoDB, the exact same pattern tests/test_backend_handlers_e2e.py already
uses for case_store.py itself (no parallel test-only implementation that
could drift from what actually runs in Lambda).

Fixture factor: 主要道路寬度 (regional_main_road_width), because its real
static rules (data/rules/regional_rules.json) already grade 18m as 普通
(grade_code=3) -- exactly the "Case B: 道路 18m -> 普通" example from the
architecture spec. _case_a_road_width_override() below widens 稍優
(grade_code=2)'s band so the SAME 18m resolves to 稍優 (grade_code=2)
instead -- a deliberately different, independently-verifiable outcome used
throughout this file as "the case rule is actually in effect" proof.
"""
from __future__ import annotations

import copy
import datetime
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

from domain.models import CaseRulePackage, CaseRulePackageStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    """collect_data.py reads DATA_PROVIDER_MODE and builds module-level
    ALL_PROVIDERS state at import time. An EARLIER test module in the same
    pytest session (test_backend_handlers_e2e.py::TestDataProviderModeSwitch
    /TestDataProviderModeFailsFastOnInvalidValue) may leave collect_data
    reloaded into "real" mode -- monkeypatch reverts the ENV VAR after those
    tests but NOT the already-reloaded module object, so a later file that
    just does `import collect_data` inherits REAL (network-calling)
    providers. This file's own tests defend themselves explicitly (see
    _ensure_mock_collect_data() below, called from every seed helper right
    before use) rather than relying on fixture setup-ordering relative to
    ddb_env; this autouse fixture's job is only the teardown half -- restore
    mock mode for whatever test module runs after this one -- mirroring the
    established convention in tests/test_collect_data_nlsc_integration.py's
    `_clean_env` fixture, which states explicitly: "Restore collect_data to
    a known-good state for later test modules in this process.\""""
    yield
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)


def _ensure_mock_collect_data(monkeypatch):
    """Called at the START of every seed helper, not relied on via fixture
    ordering: guarantees collect_data.py is in default Mock-provider mode
    at the exact point this file is about to call collect_data.collect_data()
    -- regardless of what an earlier test module in the same pytest session
    left it as (see _restore_data_provider_mode_for_later_modules above).
    Without this, this file's very first test hung for many minutes making
    real outbound HTTP calls when run as part of the full suite -- a
    reproducible test-isolation hazard, not flakiness."""
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)
    return collect_data


@pytest.fixture()
def ddb_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
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
        # root-cause writeup.
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"


def _make_case(cases_module, case_no):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    })}
    return cases_module.create_case(event, None)


def _seed_road_width_case(monkeypatch, case_no, base_value=18, comp_value=18):
    """Minimal seed sufficient for analyze.py (reads user_submitted_factors
    only)."""
    import cases

    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_case(cases, case_no)
    collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
        "base_parcel_factors": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR,
                                  "raw_value": base_value, "unit": "M"}],
        "comparable_factors": {"comp1": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR,
                                           "raw_value": comp_value, "unit": "M"}]},
    })}, None)


def _seed_full_case(monkeypatch, case_no, base_value=18, comp_value=18):
    """Full seed (mirrors tests/test_backend_handlers_e2e.py's
    TestCrossFormIndependentFieldIdentity._seed_full_case) -- sufficient for
    complete_form.py/review.py, which need regional_base_factors/
    regional_comparable_factors plus the comparison-price scaffolding
    fields, not just user_submitted_factors."""
    import cases
    import case_store

    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_case(cases, case_no)
    collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
        "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
        "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
    })}, None)
    factors = case_store.get_record(case_no, "FACTORS")
    factors["regional_base_factors"] = [
        {"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": base_value, "unit": "M"},
    ]
    factors["regional_comparable_factors"] = {
        "comp1": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": comp_value, "unit": "M"}],
    }
    factors["land_normal_price"] = {"comp1": "184763"}
    factors["price_date_rate"] = {"comp1": "2.00"}
    factors["weight"] = {"comp1": "100"}
    case_store.put_record(case_no, "FACTORS", factors)


def _static_road_width_rules():
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        rules = json.load(f)["rules"]
    return [r for r in rules if r["factor"] == ROAD_WIDTH_FACTOR]


def _case_a_road_width_override():
    """A structurally-valid (RuleTableValidator: zero ERRORs) clone of the
    real 主要道路寬度 regional rules, with 稍優(grade_code=2)'s band widened
    to 18<=x<30 (and 普通/grade_code=3's band correspondingly narrowed to
    15<=x<18, staying non-empty and contiguous -- SHULIN-COMPETITION-RULE-
    PACK-A2's new range-overlap/unreachable-range validator checks would
    correctly reject the previous 15/15 boundary here, which left
    grade_code=3 as an empty (lower_bound==upper_bound) band) so 18m now
    resolves to 稍優 instead of static's 普通 (grade_code=3)."""
    rules = copy.deepcopy(_static_road_width_rules())
    for r in rules:
        if r["grade_code"] == 2:
            r["lower_bound"] = 18
        elif r["grade_code"] == 3:
            r["upper_bound"] = 18
    return rules


def _draft_package(case_id, package_id, regional_rules=None, individual_rules=None):
    return CaseRulePackage(
        case_id=case_id, package_id=package_id, rule_version="test-v1",
        source_document="test_fixture.csv", source_type="MANUAL_CSV_INGEST",
        regional_rules=regional_rules or [], individual_rules=individual_rules or [],
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )


def _grade_of(body, field_id=ROAD_WIDTH_FIELD_ID):
    matches = [g for g in body["grades"] if g["field_id"] == field_id]
    assert matches, f"no grade entry for field_id={field_id!r} in {body['grades']}"
    return matches[0]


# ---------------------------------------------------------------------------
# TEST 1 -- No Case Rule -> existing static behavior unchanged
# ---------------------------------------------------------------------------

class TestNoCaseRuleStaticBaselineUnchanged:
    def test_no_package_at_all_uses_static_baseline(self, ddb_env, monkeypatch):
        import analyze

        case_no = "CASERULE-T1-001"
        _seed_road_width_case(monkeypatch, case_no)
        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert body["rule_package_id"] is None
        assert body["rule_resolution_warnings"] == []
        g = _grade_of(body)
        assert "comp=普通" in g["grade"]
        assert g["rule_source_type"] == "STATIC_LOCAL"


# ---------------------------------------------------------------------------
# TEST 2 -- Confirmed Case Rule -> Analyze uses Case Rule
# ---------------------------------------------------------------------------

class TestConfirmedCaseRuleAnalyze:
    def test_confirmed_package_overrides_grade_in_analyze(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import analyze

        case_no = "CASERULE-T2-001"
        _seed_road_width_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-T2-001", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-T2-001", confirmed_by="tester")

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == "PKG-T2-001"
        g = _grade_of(body)
        assert "comp=稍優" in g["grade"]
        assert g["rule_source_type"] == "CASE_IMPORTED_CONFIRMED"


# ---------------------------------------------------------------------------
# TEST 3 -- Confirmed Case Rule -> CompleteForm uses same Case Rule
# ---------------------------------------------------------------------------

class TestConfirmedCaseRuleCompleteForm:
    def test_confirmed_package_traceable_in_complete_form(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import complete_form

        case_no = "CASERULE-T3-001"
        _seed_full_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        override = _case_a_road_width_override()
        pkg = _draft_package(case_no, "PKG-T3-001", regional_rules=override)
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-T3-001", confirmed_by="tester")

        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == "PKG-T3-001"

        case_rule_ids = {r["rule_id"] for r in override}
        matching = [f for f in body["fields"] if f.get("rule_id") in case_rule_ids]
        assert matching, "expected at least one field graded via the confirmed case rule"
        assert all(f["rule_source_type"] == "CASE_IMPORTED_CONFIRMED" for f in matching)


# ---------------------------------------------------------------------------
# TEST 4 -- Confirmed Case Rule -> Review uses same Case Rule
# ---------------------------------------------------------------------------

class TestConfirmedCaseRuleReview:
    def test_confirmed_package_traceable_in_review(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import complete_form
        import review

        case_no = "CASERULE-T4-001"
        _seed_full_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-T4-001", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-T4-001", confirmed_by="tester")

        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == "PKG-T4-001"


# ---------------------------------------------------------------------------
# TEST 5 -- Case A / Case B isolation
# ---------------------------------------------------------------------------

class TestCaseIsolation:
    def test_case_a_and_case_b_produce_different_grades(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import analyze

        case_a, case_b = "CASERULE-T5-A", "CASERULE-T5-B"
        _seed_road_width_case(monkeypatch, case_a)
        _seed_road_width_case(monkeypatch, case_b)

        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_a, "PKG-T5-A", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_a, "PKG-T5-A", confirmed_by="tester")

        body_a = json.loads(analyze.analyze({"pathParameters": {"id": case_a}}, None)["body"])
        body_b = json.loads(analyze.analyze({"pathParameters": {"id": case_b}}, None)["body"])

        g_a, g_b = _grade_of(body_a), _grade_of(body_b)
        assert "稍優" in g_a["grade"]
        assert "普通" in g_b["grade"]
        assert g_a["grade"] != g_b["grade"]
        assert body_a["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body_b["rule_resolution_status"] == "STATIC_LOCAL"


# ---------------------------------------------------------------------------
# TEST 6 -- Warm-process repeated calls do not leak Case Rule
# ---------------------------------------------------------------------------

class TestWarmProcessNoLeak:
    def test_no_module_level_rule_engine_cache_in_any_handler(self):
        import analyze
        import complete_form
        import review

        for mod in (analyze, complete_form, review):
            assert not hasattr(mod, "_RULE_ENGINE"), (
                f"{mod.__name__} still has a module-level RuleEngine cache -- "
                f"this would leak Case A's confirmed rules into Case B's "
                f"request on a warm Lambda container"
            )

    def test_sequential_case_a_then_case_b_no_leak(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import analyze

        case_a, case_b = "CASERULE-T6-A", "CASERULE-T6-B"
        _seed_road_width_case(monkeypatch, case_a)
        _seed_road_width_case(monkeypatch, case_b)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_a, "PKG-T6-A", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_a, "PKG-T6-A", confirmed_by="tester")

        # Simulates a warm container serving repeated requests: A, A, then B.
        analyze.analyze({"pathParameters": {"id": case_a}}, None)
        analyze.analyze({"pathParameters": {"id": case_a}}, None)
        body_b = json.loads(analyze.analyze({"pathParameters": {"id": case_b}}, None)["body"])

        assert body_b["rule_resolution_status"] == "STATIC_LOCAL"
        assert "普通" in _grade_of(body_b)["grade"]


# ---------------------------------------------------------------------------
# TEST 7 -- Unconfirmed Case Rule cannot affect result
# ---------------------------------------------------------------------------

class TestUnconfirmedCannotAffectResult:
    @pytest.mark.parametrize("status", [
        CaseRulePackageStatus.DRAFT, CaseRulePackageStatus.EXTRACTED, CaseRulePackageStatus.PARTIAL,
    ])
    def test_unconfirmed_package_never_affects_grade(self, ddb_env, monkeypatch, status):
        from case_rule_repository import DynamoCaseRuleRepository
        import analyze

        case_no = f"CASERULE-T7-{status.value}"
        _seed_road_width_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, f"PKG-T7-{status.value}", regional_rules=_case_a_road_width_override())
        pkg.status = status
        repo.save_candidate(pkg)  # deliberately never confirmed

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "CASE_RULE_NOT_CONFIRMED" in body["rule_resolution_warnings"]
        assert "普通" in _grade_of(body)["grade"]


# ---------------------------------------------------------------------------
# TEST 8 -- Rejected Case Rule cannot affect result
# ---------------------------------------------------------------------------

class TestRejectedCannotAffectResult:
    def test_rejected_after_confirm_falls_back_to_static(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import analyze

        case_no = "CASERULE-T8-001"
        _seed_road_width_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-T8-001", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-T8-001", confirmed_by="tester")

        body_before = json.loads(analyze.analyze({"pathParameters": {"id": case_no}}, None)["body"])
        assert body_before["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"

        repo.reject(case_no, "PKG-T8-001", reason="test reject", rejected_by="tester")
        body_after = json.loads(analyze.analyze({"pathParameters": {"id": case_no}}, None)["body"])
        assert body_after["rule_resolution_status"] == "STATIC_LOCAL"
        assert "普通" in _grade_of(body_after)["grade"]


# ---------------------------------------------------------------------------
# TEST 9 -- Invalid confirmed package -> CASE_RULE_INVALID, no silent fallback
# ---------------------------------------------------------------------------

class TestInvalidConfirmedPackage:
    def test_confirm_refuses_structurally_invalid_rules(self, ddb_env):
        from case_rule_repository import DynamoCaseRuleRepository, CaseRulePackageInvalidError

        case_no = "CASERULE-T9-001"
        repo = DynamoCaseRuleRepository()
        bad_rules = _case_a_road_width_override()
        del bad_rules[0]["max_adjustment"]  # missing required field -> ERROR
        pkg = _draft_package(case_no, "PKG-T9-001", regional_rules=bad_rules)
        repo.save_candidate(pkg)

        with pytest.raises(CaseRulePackageInvalidError):
            repo.confirm(case_no, "PKG-T9-001", confirmed_by="tester")

        # The CONFIRMED Gate must have refused BEFORE mutating status.
        still_draft = repo.get_package(case_no, "PKG-T9-001")
        assert still_draft.status == CaseRulePackageStatus.DRAFT
        assert repo.get_confirmed_package(case_no) is None

    def test_resolver_refuses_drifted_confirmed_package_no_silent_fallback(self, ddb_env, monkeypatch):
        """Simulates post-CONFIRMED data drift (bypassing confirm()'s own
        gate -- e.g. a hand-edited DynamoDB item): build_rule_engine_for_case()
        must raise, and analyze.py must return CASE_RULE_INVALID, never
        silently grading against the static baseline instead."""
        from case_rule_repository import DynamoCaseRuleRepository
        import case_rule_repository as crr
        import case_store
        import analyze

        case_no = "CASERULE-T9-002"
        _seed_road_width_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-T9-002", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-T9-002", confirmed_by="tester")

        drifted = repo.get_package(case_no, "PKG-T9-002")
        del drifted.regional_rules[0]["max_adjustment"]
        case_store.put_record(case_no, crr._package_sk("PKG-T9-002"), drifted.model_dump(mode="json"))

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 409
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "CASE_RULE_INVALID"
        assert "MANUAL_REVIEW_REQUIRED" in body["error"]["message"]
        # No silent fallback: no ANALYSIS record was ever persisted for
        # this invocation.
        assert case_store.get_record(case_no, "ANALYSIS") is None


# ---------------------------------------------------------------------------
# TEST 10 -- Golden existing behavior unchanged when no Case Rule exists
# ---------------------------------------------------------------------------

class TestGoldenLikeFlowUnaffected:
    def test_full_pipeline_with_no_case_rule_matches_pre_existing_behavior(self, ddb_env, monkeypatch):
        """End-to-end collect_data -> complete_form -> review with NO case
        rule package ever created for this case -- mirrors
        tests/test_backend_handlers_e2e.py::TestCrossFormIndependentFieldIdentity
        ::test_normal_case_produces_no_cross_form_false_positive, proving
        this architecture introduced no behavior change for the untouched
        path."""
        import review

        case_no = "CASERULE-T10-001"
        _seed_full_case(monkeypatch, case_no)
        import complete_form
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["error_count"] == 0
        assert body["inconsistent_count"] == 0
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert body["rule_package_id"] is None


# ---------------------------------------------------------------------------
# TEST 11 -- No Mock fallback: an invalid CONFIRMED package must never
# silently produce (and persist) SOME result via a substitute rule source.
# ---------------------------------------------------------------------------

class TestNoMockFallbackOnInvalidRule:
    def test_invalid_confirmed_package_persists_nothing_for_any_handler(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import case_rule_repository as crr
        import case_store
        import complete_form

        case_no = "CASERULE-T11-001"
        _seed_full_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-T11-001", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-T11-001", confirmed_by="tester")

        drifted = repo.get_package(case_no, "PKG-T11-001")
        drifted.regional_rules[0].pop("max_adjustment", None)
        case_store.put_record(case_no, crr._package_sk("PKG-T11-001"), drifted.model_dump(mode="json"))

        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 409
        assert case_store.get_record(case_no, "FORM_COMPLETION") is None


# ---------------------------------------------------------------------------
# TEST 12 -- No Golden fallback: a brand-new, unrelated case must never
# resolve to a cached/hardcoded Golden Case result.
# ---------------------------------------------------------------------------

class TestNoGoldenFallback:
    def test_unrelated_case_resolves_purely_from_its_own_submitted_value(self, ddb_env, monkeypatch):
        import analyze

        golden_like = "1140901-99-001"  # mirrors the real Golden Case number format
        other = "CASERULE-T12-OTHER"
        _seed_road_width_case(monkeypatch, golden_like, base_value=18, comp_value=18)
        _seed_road_width_case(monkeypatch, other, base_value=12, comp_value=12)  # falls in 稍劣(4)'s band, never 18m's band

        body_golden_like = json.loads(analyze.analyze({"pathParameters": {"id": golden_like}}, None)["body"])
        body_other = json.loads(analyze.analyze({"pathParameters": {"id": other}}, None)["body"])

        g1, g2 = _grade_of(body_golden_like), _grade_of(body_other)
        assert "普通" in g1["grade"]
        assert "稍劣" in g2["grade"]
        assert g1["grade"] != g2["grade"]
        assert body_golden_like["rule_resolution_status"] == "STATIC_LOCAL"
        assert body_other["rule_resolution_status"] == "STATIC_LOCAL"


# ---------------------------------------------------------------------------
# Repository-level unit tests (CONFIRMED Gate invariants not otherwise
# exercised end-to-end above).
# ---------------------------------------------------------------------------

class TestRepositoryInvariants:
    def test_confirm_refuses_a_second_package_while_one_is_already_confirmed(self, ddb_env):
        from case_rule_repository import DynamoCaseRuleRepository, CaseRulePackageAlreadyConfirmedError

        case_no = "CASERULE-REPO-001"
        repo = DynamoCaseRuleRepository()
        pkg1 = _draft_package(case_no, "PKG-REPO-001-A", regional_rules=_case_a_road_width_override())
        pkg2 = _draft_package(case_no, "PKG-REPO-001-B", regional_rules=_case_a_road_width_override())
        repo.save_candidate(pkg1)
        repo.save_candidate(pkg2)
        repo.confirm(case_no, "PKG-REPO-001-A", confirmed_by="tester")

        with pytest.raises(CaseRulePackageAlreadyConfirmedError):
            repo.confirm(case_no, "PKG-REPO-001-B", confirmed_by="tester")

        # Rejecting the first frees the way for the second.
        repo.reject(case_no, "PKG-REPO-001-A", reason="superseded", rejected_by="tester")
        repo.confirm(case_no, "PKG-REPO-001-B", confirmed_by="tester")
        assert repo.get_confirmed_package(case_no).package_id == "PKG-REPO-001-B"

    def test_confirm_unknown_package_raises_not_found(self, ddb_env):
        from case_rule_repository import DynamoCaseRuleRepository, CaseRulePackageNotFoundError

        repo = DynamoCaseRuleRepository()
        with pytest.raises(CaseRulePackageNotFoundError):
            repo.confirm("CASERULE-REPO-002", "NO-SUCH-PACKAGE", confirmed_by="tester")

    def test_list_packages_scoped_to_one_case_only(self, ddb_env):
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_package("CASERULE-REPO-003-A", "PKG-X"))
        repo.save_candidate(_draft_package("CASERULE-REPO-003-B", "PKG-Y"))

        assert {p.package_id for p in repo.list_packages("CASERULE-REPO-003-A")} == {"PKG-X"}
        assert {p.package_id for p in repo.list_packages("CASERULE-REPO-003-B")} == {"PKG-Y"}
