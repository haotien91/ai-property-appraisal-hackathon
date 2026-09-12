# -*- coding: utf-8 -*-
"""
STEP5 §10 (Cross-Form Tamper Detection A-E) and §24 (Failure Tests F1-F10).

§10 reuses the EXISTING, already-wired AuditEngine/CrossFormValidationEngine/
CalculationValidator cross-form checks (engine/audit_engine.py) -- no new
detection engine is built here, per STEP5 §0's Core Freeze. Each tamper
scenario mutates an already-computed FORM_COMPLETION record directly (the
same "post-computation corruption" shape a real tampered/corrupted stored
record would take) and proves review.py's review() -- re-deriving the
EXPECTED side fresh from the rule engine every call -- still catches it.

§24 proves each named failure mode fails SAFELY: no fabricated result, no
Mock/Golden fallback, an honest error code or MISSING/INCONSISTENT issue
instead. F6 (RULESET_UNAVAILABLE) is already covered precisely by
tests/test_competition_dual_input_e2e.py::TestResidentialRulesetUnavailable
and is not duplicated here.
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

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from domain.models import CaseRulePackage, CaseRulePackageStatus  # noqa: E402

ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"
BLIND_FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "competition", "blind_case_changed_standard",
    "blind_evaluation_standard.json",
)


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    yield
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)


def _ensure_mock_collect_data(monkeypatch):
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
        # Module-level table/bucket name staleness defense -- see
        # tests/_aws_mock_reset.py's module docstring for the full,
        # generalized root-cause writeup (docs/audit/STEP5_FINAL_GATE_
        # REPORT.md).
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_case(cases_module, case_no, land_use_type="商業用地"):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": land_use_type, "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    })}
    return cases_module.create_case(event, None)


def _seed_full_case(monkeypatch, case_no, base_value=18, comp_value=3):
    """Mirrors test_competition_blind_case.py's own _seed_full_case (same
    comp_value=3 convention -- see that file's docstring for why)."""
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


def _run_complete_form(case_no):
    import complete_form
    resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
    assert resp["statusCode"] == 200, resp["body"]
    return json.loads(resp["body"])


def _run_review(case_no):
    import review
    resp = review.review({"pathParameters": {"id": case_no}}, None)
    assert resp["statusCode"] == 200, resp["body"]
    return json.loads(resp["body"])


def _tamper_form_completion_field(case_no, field_id, new_final_value):
    """Mutates a stored FORM_COMPLETION field's final_value/calculation
    (read by build_submitted_from_form_completion() for most field_id
    patterns), AND its `adjustment` key specifically for `..._differential_
    rate_...` fields -- that pattern is read via f["adjustment"], not
    f["final_value"] (see review.py's build_submitted_from_form_completion:
    `if "_differential_rate_" in field_id and f.get("adjustment") is not
    None: ... = f["adjustment"]`), a DELIBERATELY separate stored key from
    final_value even though they hold the same conceptual number for this
    field type."""
    import case_store
    form_completion = case_store.get_record(case_no, "FORM_COMPLETION")
    found = False
    for f in form_completion["fields"]:
        if f["field_id"] == field_id:
            f["final_value"] = new_final_value
            f["calculation"] = str(new_final_value)
            if "_differential_rate_" in field_id and f.get("adjustment") is not None:
                f["adjustment"] = new_final_value
            found = True
    assert found, f"field_id={field_id!r} not found in stored FORM_COMPLETION"
    case_store.put_record(case_no, "FORM_COMPLETION", form_completion)


def _load_blind_fixture():
    with open(BLIND_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# STEP5 §10 A-E -- Cross-Form Tamper Detection
# ---------------------------------------------------------------------------

class TestCrossFormTamperDetection:
    def test_a_normal_golden_all_cross_form_checks_pass(self, ddb_env, monkeypatch):
        """A. 正常 Golden -- an untampered run must show every cross-form
        check PASSED, never a false positive."""
        case_no = "TAMPER-A-001"
        _seed_full_case(monkeypatch, case_no)
        _run_complete_form(case_no)
        review_body = _run_review(case_no)

        region_rate_issue = next(i for i in review_body["issues"] if i["field"] == "region_adjustment_rate_comp1")
        assert region_rate_issue["issue_type"] == "Passed"
        case_identity_issue = next(i for i in review_body["issues"] if i["field"] == "case_no_identity")
        assert case_identity_issue["issue_type"] == "Passed"
        comparable_identity_issue = next(
            i for i in review_body["issues"] if i["field"] == "comparable_id_set_identity"
        )
        assert comparable_identity_issue["issue_type"] == "Passed"

    def test_b_table52_regional_total_tampered(self, ddb_env, monkeypatch):
        """B. 表5-2 regional total 被竄改 -- mutate 表5-2's OWN stored field
        (regional_total_adjustment_comp1) after complete_form ran; 表4's
        region_adjustment_rate_comp1 field is untouched, so the two
        independently-stored fields now disagree."""
        case_no = "TAMPER-B-001"
        _seed_full_case(monkeypatch, case_no)
        form_body = _run_complete_form(case_no)
        correct_value = next(
            f["final_value"] for f in form_body["fields"] if f["field_id"] == "regional_total_adjustment_comp1"
        )
        tampered_value = str(float(correct_value) + 99)
        _tamper_form_completion_field(case_no, "regional_total_adjustment_comp1", tampered_value)

        review_body = _run_review(case_no)
        issue = next(i for i in review_body["issues"] if i["field"] == "region_adjustment_rate_comp1")
        assert issue["issue_type"] == "Inconsistent", issue
        assert issue["expected_value"] == tampered_value  # 表5-2 side (tampered) is "expected" per this check's own field ordering
        assert issue["submitted_value"] != tampered_value

    def test_c_table4_uses_wrong_regional_value(self, ddb_env, monkeypatch):
        """C. 表4 使用錯誤 regional value -- the OPPOSITE side tampered
        instead (表4's region_adjustment_rate_comp1, 表5-2 left untouched)
        -- proves the check independently catches EITHER side, not just a
        hardcoded direction (land-appraisal-audit skill's "Cross-Form
        Validation Independence" rule)."""
        case_no = "TAMPER-C-001"
        _seed_full_case(monkeypatch, case_no)
        form_body = _run_complete_form(case_no)
        correct_value = next(
            f["final_value"] for f in form_body["fields"] if f["field_id"] == "region_adjustment_rate_comp1"
        )
        tampered_value = str(float(correct_value) - 50)
        _tamper_form_completion_field(case_no, "region_adjustment_rate_comp1", tampered_value)

        review_body = _run_review(case_no)
        issue = next(i for i in review_body["issues"] if i["field"] == "region_adjustment_rate_comp1")
        assert issue["issue_type"] == "Inconsistent", issue
        assert issue["submitted_value"] == tampered_value
        assert issue["expected_value"] != tampered_value

    def test_d_expected_vs_submitted_never_tautological(self, ddb_env, monkeypatch):
        """D. Expected / Submitted 分離 -- tampering an INDIVIDUAL
        adjustment field's submitted value must produce an issue whose
        expected_value is the REAL matrix-derived rate (independently
        recomputed by CalculationValidator from grade/matrix, never a
        copy of the tampered submitted_value itself)."""
        case_no = "TAMPER-D-001"
        _seed_full_case(monkeypatch, case_no)
        _run_complete_form(case_no)
        _tamper_form_completion_field(case_no, "individual_land_depth_differential_rate_comp1", "999.99")

        review_body = _run_review(case_no)
        issue = next(
            i for i in review_body["issues"]
            if i["field"] == "individual_land_depth_differential_rate_comp1"
        )
        assert issue["issue_type"] == "Error", issue
        assert issue["submitted_value"] == "999.99"
        assert issue["expected_value"] != "999.99"
        assert issue["expected_value"] != issue["submitted_value"]

    def test_e_case_rule_changed_but_downstream_form_stays_old(self, ddb_env, monkeypatch):
        """E. Case rule changed but downstream form remains old value --
        complete_form runs under the STATIC baseline (18m -> 普通), the
        case is THEN switched to a CONFIRMED Blind rule (18m -> 稍優)
        WITHOUT re-running complete_form -- the stale FORM_COMPLETION still
        says 普通; review() re-derives the EXPECTED grade fresh from
        whichever rule is CURRENTLY confirmed (rule_engine_factory.build_
        rule_engine_for_case is never cached, called fresh every request),
        so it must now report an inconsistency, not silently agree with
        the stale form."""
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard

        case_no = "TAMPER-E-001"
        _seed_full_case(monkeypatch, case_no)
        form_body = _run_complete_form(case_no)
        static_field = next(
            f for f in form_body["fields"] if f["field_id"] == f"{ROAD_WIDTH_FIELD_ID}_adjustment_pct_comp1"
        )
        assert "base=普通" in static_field["grade"]

        fixture = _load_blind_fixture()
        pkg = CaseRulePackage(
            case_id=case_no, package_id="TAMPER-E-PKG", rule_version="TEST_ONLY-blind-v1",
            source_document=BLIND_FIXTURE_PATH, source_type="TEST_ONLY_JSON_FIXTURE",
            status=CaseRulePackageStatus.EXTRACTED,
            regional_rules=copy.deepcopy(fixture["regional_rules"]), individual_rules=[],
            created_at=datetime.datetime.now(datetime.timezone.utc), metadata=dict(fixture["metadata"]),
        )
        DynamoCaseRuleRepository().save_candidate(pkg)
        confirm_resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": "TAMPER-E-PKG"},
             "body": json.dumps({"confirmed_by": "tamper-e-tester"})}, None,
        )
        assert confirm_resp["statusCode"] == 200, confirm_resp["body"]
        # complete_form is DELIBERATELY NOT re-run here -- that is the
        # entire point of this scenario.

        review_body = _run_review(case_no)
        grade_issue = next(
            i for i in review_body["issues"]
            if i["field"] == f"{ROAD_WIDTH_FIELD_ID}_adjustment_pct_comp1"
        )
        assert grade_issue["issue_type"] in ("Error", "Inconsistent"), grade_issue
        assert grade_issue["expected_value"] == "2"  # 稍優 under the NEWLY confirmed Blind rule
        assert grade_issue["submitted_value"] == "3"  # 普通 -- the STALE static-baseline grade code still on the form
        assert grade_issue["rule_source_type"] == "CASE_IMPORTED_CONFIRMED"


# ---------------------------------------------------------------------------
# STEP5 §24 F1-F10 -- Failure Tests (safe failure, no fabricated result)
# ---------------------------------------------------------------------------

class TestFailureModes:
    def test_f1_appraisal_document_missing(self, ddb_env, monkeypatch):
        import cases
        import document_extract

        _ensure_mock_collect_data(monkeypatch)
        case_no = "F1-001"
        _make_case(cases, case_no)
        resp = document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": "nonexistent-doc"}}, None,
        )
        assert resp["statusCode"] == 404
        assert json.loads(resp["body"])["error"]["code"] == "DOCUMENT_NOT_FOUND"

    def test_f2_evaluation_standard_missing_when_required(self, ddb_env, monkeypatch):
        """When a case has no Evaluation Standard package at all, analyze
        must honestly fall back to STATIC_LOCAL -- never fabricate a
        CONFIRMED-looking result -- and get_evaluation_standard_candidate
        for a nonexistent package_id must 404, never a fabricated DTO."""
        import cases
        import evaluation_standard
        import analyze

        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "F2-001"
        _make_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [], "comparable_factors": {"comp1": []},
        })}, None)

        missing_resp = evaluation_standard.get_evaluation_standard_candidate(
            {"pathParameters": {"id": case_no, "package_id": "NEVER-UPLOADED"}}, None,
        )
        assert missing_resp["statusCode"] == 404

        analyze_resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        body = json.loads(analyze_resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert body["rule_package_id"] is None

    def test_f3_evaluation_standard_partial_still_confirmable_but_traced(self, ddb_env, monkeypatch):
        """A PARTIAL package (some rows resolved, others not) can still be
        confirmed with whatever rules it DOES have (STEP5 §4's partial-
        scope allowance) -- but its status is never silently upgraded to
        look like a full EXTRACTED/CONFIRMED success before a human acts."""
        from case_rule_repository import DynamoCaseRuleRepository

        case_no = "F3-001"
        pkg = CaseRulePackage(
            case_id=case_no, package_id="F3-PKG", rule_version="v1",
            source_document="test", source_type="TEST_ONLY_JSON_FIXTURE",
            status=CaseRulePackageStatus.PARTIAL,
            regional_rules=[], individual_rules=[],
            created_at=datetime.datetime.now(datetime.timezone.utc),
            warnings=["部分因素解析失敗，需人工確認"],
        )
        saved = DynamoCaseRuleRepository().save_candidate(pkg)
        assert saved.status == CaseRulePackageStatus.PARTIAL
        assert saved.status != CaseRulePackageStatus.CONFIRMED

    def test_f4_evaluation_standard_ambiguous_blocks_silent_progress(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository

        case_no = "F4-001"
        pkg = CaseRulePackage(
            case_id=case_no, package_id="F4-PKG", rule_version="v1",
            source_document="test", source_type="TEST_ONLY_JSON_FIXTURE",
            status=CaseRulePackageStatus.AMBIGUOUS,
            regional_rules=[], individual_rules=[],
            created_at=datetime.datetime.now(datetime.timezone.utc),
            warnings=["因素名稱無法唯一對應，需人工確認"],
        )
        saved = DynamoCaseRuleRepository().save_candidate(pkg)
        assert saved.status == CaseRulePackageStatus.AMBIGUOUS
        assert saved.status != CaseRulePackageStatus.CONFIRMED

    def test_f5_invalid_confirmed_attempt_rejected(self, ddb_env, monkeypatch):
        """A package whose regional_rules fail RuleTableValidator (an
        internally-contradictory grade band) must be REFUSED at confirm()
        time, with validation_issues returned -- never silently confirmed."""
        import evaluation_standard

        case_no = "F5-001"
        from case_rule_repository import DynamoCaseRuleRepository
        broken_rules = copy.deepcopy(_load_blind_fixture()["regional_rules"])
        # Corrupt: duplicate rule_id across two rows -- RuleTableValidator
        # flags this as ERROR-severity ("rule_id 重複出現") unconditionally.
        broken_rules[1]["rule_id"] = broken_rules[0]["rule_id"]
        pkg = CaseRulePackage(
            case_id=case_no, package_id="F5-PKG", rule_version="v1",
            source_document="test", source_type="TEST_ONLY_JSON_FIXTURE",
            status=CaseRulePackageStatus.EXTRACTED,
            regional_rules=broken_rules, individual_rules=[],
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        DynamoCaseRuleRepository().save_candidate(pkg)

        resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": "F5-PKG"},
             "body": json.dumps({"confirmed_by": "f5-tester"})}, None,
        )
        assert resp["statusCode"] == 409
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "CASE_RULE_VALIDATION_FAILED"
        assert body["validation_issues"]

    def test_f7_case_a_package_not_usable_by_case_b(self, ddb_env, monkeypatch):
        """A package confirmed for Case A must be structurally invisible
        to Case B -- get_evaluation_standard_candidate for Case B with
        Case A's package_id must 404 (case_id is part of the DynamoDB
        partition key), and Case B's own analyze() must stay STATIC_LOCAL."""
        import cases
        import evaluation_standard
        import analyze
        from case_rule_repository import DynamoCaseRuleRepository

        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_a, case_b = "F7-A-001", "F7-B-001"
        _make_case(cases, case_a)
        _make_case(cases, case_b)
        for c in (case_a, case_b):
            collect_data.collect_data({"pathParameters": {"id": c}, "body": json.dumps({
                "base_parcel_factors": [], "comparable_factors": {"comp1": []},
            })}, None)

        fixture = _load_blind_fixture()
        pkg = CaseRulePackage(
            case_id=case_a, package_id="F7-PKG", rule_version="TEST_ONLY-v1",
            source_document=BLIND_FIXTURE_PATH, source_type="TEST_ONLY_JSON_FIXTURE",
            status=CaseRulePackageStatus.EXTRACTED,
            regional_rules=copy.deepcopy(fixture["regional_rules"]), individual_rules=[],
            created_at=datetime.datetime.now(datetime.timezone.utc), metadata=dict(fixture["metadata"]),
        )
        DynamoCaseRuleRepository().save_candidate(pkg)
        confirm_resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": case_a, "package_id": "F7-PKG"},
             "body": json.dumps({"confirmed_by": "f7-tester"})}, None,
        )
        assert confirm_resp["statusCode"] == 200

        cross_read = evaluation_standard.get_evaluation_standard_candidate(
            {"pathParameters": {"id": case_b, "package_id": "F7-PKG"}}, None,
        )
        assert cross_read["statusCode"] == 404

        b_body = json.loads(analyze.analyze({"pathParameters": {"id": case_b}}, None)["body"])
        assert b_body["rule_resolution_status"] == "STATIC_LOCAL"
        assert b_body["rule_package_id"] is None

    def test_f8_pdf_generation_failure_before_form_completion(self, ddb_env, monkeypatch):
        """PDF generation on a case that never completed FORM_COMPLETION
        must fail with a clear VALIDATION_ERROR, never return a blank/
        fabricated PDF. pdf_handler.py itself imports weasyprint at module
        level (needed for its OTHER, successful-path branches) -- guarded
        here the same way as tests/test_competition_dual_input_e2e.py's
        15-step test, since this Windows dev host lacks WeasyPrint's
        native Cairo/Pango libs (STEP5 §12 known environment limitation,
        not a code failure)."""
        import cases

        monkeypatch.setenv("PDF_BUCKET_NAME", "test-pdf-bucket-f8")
        case_no = "F8-001"
        _make_case(cases, case_no)
        try:
            import pdf_handler
        except OSError as e:
            pytest.skip(f"WeasyPrint native library unavailable on this host (STEP5 §12 known environment "
                        f"limitation, not a code failure): {e}")
        resp = pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "VALIDATION_ERROR"

    def test_f9_cross_form_tamper_caught_via_full_pipeline(self, ddb_env, monkeypatch):
        """Same tamper shape as Test B above, restated as an explicit F9
        failure-mode test per STEP5 §24's own naming."""
        case_no = "F9-001"
        _seed_full_case(monkeypatch, case_no)
        _run_complete_form(case_no)
        _tamper_form_completion_field(case_no, "regional_total_adjustment_comp1", "-12345")
        review_body = _run_review(case_no)
        issue = next(i for i in review_body["issues"] if i["field"] == "region_adjustment_rate_comp1")
        assert issue["issue_type"] == "Inconsistent"

    def test_f10_required_official_data_unknown(self, ddb_env, monkeypatch):
        """A case with no road_width_evidence at all (no Real*Provider
        result, no submission) must resolve to an honest MISSING/
        UNAVAILABLE finding for 主要道路寬度 (case-level 表1 field), never
        a guessed value."""
        case_no = "F10-001"
        _seed_full_case(monkeypatch, case_no)
        _run_complete_form(case_no)
        review_body = _run_review(case_no)
        road_width_issue = next(i for i in review_body["issues"] if i["field"] == "main_road_width")
        assert road_width_issue["issue_type"] == "Missing"
        assert road_width_issue["explanation_data"]["check_type"] == "ROAD_WIDTH_UNAVAILABLE"
