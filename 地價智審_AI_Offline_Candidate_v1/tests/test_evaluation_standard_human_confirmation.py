# -*- coding: utf-8 -*-
"""
Phase 3B tests: Evaluation Standard Importer -> Human Confirmation ->
CaseRuleRepository (docs/audit/EVALUATION_STANDARD_HUMAN_CONFIRMATION_
PHASE3B_REPORT.md). Exercises the REAL production path -- backend/
handlers/evaluation_standard.py's five handlers, backed by moto-mocked
DynamoDB + S3 (document upload reuses document_upload.py AS-IS, exactly
like tests/test_backend_document_handlers_e2e.py already does for case
submission PDFs) -- against the REAL sample PDF (data/sources/competition/
評價基準明細表範例.pdf), the same one Phase 3A's Golden-like verification
used.

Fixture strategy: 主要道路寬度 (regional_main_road_width) is the ONE
candidate the Importer already resolves cleanly end-to-end (see Phase 3A),
but its extracted grade for 18m (普通) is IDENTICAL to the static
baseline -- confirming it unedited would prove nothing about whether the
confirmed package is actually being used. So TEST 13/14/15 below submit a
human EDIT widening 稍優's band first (mirroring Step 2's own
_case_a_road_width_override()), producing a deliberately different,
independently-verifiable outcome (稍優 instead of 普通) used throughout
this file as "the confirmed evaluation-standard package is actually in
effect" proof.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

try:
    import pymupdf  # noqa: F401
except ImportError:
    pytest.skip("pymupdf not available", allow_module_level=True)

PDF_PATH = os.path.join(REPO_ROOT, "data", "sources", "competition", "評價基準明細表範例.pdf")
DOCUMENT_BUCKET = "test-document-bucket"
AWS_REGION = "ap-northeast-1"
ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    """See tests/test_case_scoped_rule_architecture.py's identical fixture
    for the full explanation: collect_data.py's DATA_PROVIDER_MODE is
    module-level state that an earlier test module in the same pytest
    session may leave mutated; this only restores it for whatever runs
    after this file (this file's own tests defend themselves explicitly
    via _ensure_mock_collect_data())."""
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
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("DOCUMENT_BUCKET_NAME", DOCUMENT_BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", AWS_REGION)
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=AWS_REGION)
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.create_bucket(Bucket=DOCUMENT_BUCKET, CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        # STEP5 FINAL GATE Part A (docs/audit/STEP5_FINAL_GATE_REPORT.md):
        # ROOT CAUSE of a real, reproduced cross-file test-order failure --
        # evaluation_standard.py / document_upload.py / document_extract.
        # py's DOCUMENT_BUCKET is a module-level constant bound once at
        # first import; whichever test file happens to import one of
        # those modules FIRST in the process (with no DOCUMENT_BUCKET_NAME
        # env var set, e.g. a STEP5 test file whose OWN fixture never
        # needed a document bucket) permanently poisons it to the OS
        # default ("ai-valuation-documents") for every later file,
        # including this one. See tests/_aws_mock_reset.py's module
        # docstring for the full writeup.
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


def _seed_road_width_case(monkeypatch, case_no, base_value=18, comp_value=18):
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


def _upload_and_extract(case_no, package_id=None):
    """Full client-shaped flow: request_upload -> client PUT -> extract.
    Returns (document_id, package_id, review_dto)."""
    import document_upload
    import evaluation_standard
    import case_store

    resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
    assert resp["statusCode"] == 201
    document_id = json.loads(resp["body"])["document_id"]
    doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))

    s3 = boto3.client("s3", region_name=AWS_REGION)
    with open(PDF_PATH, "rb") as f:
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=doc_meta["s3_key"], Body=f.read(), ContentType="application/pdf")

    body = {"package_id": package_id} if package_id else {}
    resp = evaluation_standard.extract_evaluation_standard(
        {"pathParameters": {"id": case_no, "document_id": document_id}, "body": json.dumps(body)}, None,
    )
    assert resp["statusCode"] == 201, resp["body"]
    dto = json.loads(resp["body"])
    return document_id, dto["package_id"], dto


def _road_width_rule_id(review_dto):
    factor = next(f for f in review_dto["factors"] if f["canonical_factor_id"] == ROAD_WIDTH_FACTOR)
    return factor["factor_id"], factor


def _widen_road_width_via_human_edit(case_no, package_id, edited_by="reviewer"):
    """Submits a human edit widening 稍優's band the SAME way Step 2's
    _case_a_road_width_override() does, on the REAL Importer-extracted
    主要道路寬度 rule records, via the REAL submit_evaluation_standard_
    edits handler -- so 18m resolves to 稍優 instead of static's 普通, a
    deliberate, independently-verifiable difference.

    Edits BOTH grade_code=2's lower_bound and grade_code=3's upper_bound
    down to 18 (not just grade_code=2's lower_bound down to 15): the
    original bounds are 稍優=[20,30) / 普通=[15,20). Moving only
    grade_code=2's lower_bound to 15 would make 稍優=[15,30) overlap
    普通=[15,20) for the whole [15,20) range -- SHULIN-COMPETITION-RULE-
    PACK-A2's generalized RuleTableValidator._check_range_segments()
    correctly rejects that as an ERROR (genuine overlap), which
    confirm_evaluation_standard() then correctly refuses with 409. Editing
    both bounds to 18 keeps the two grades contiguous and non-overlapping
    (稍優=[18,30), 普通=[15,18)) while still resolving 18m as 稍優."""
    import evaluation_standard
    from case_rule_repository import default_case_rule_repository

    repo = default_case_rule_repository()
    package = repo.get_package(case_no, package_id)
    grade2_rule_id = next(
        r["rule_id"] for r in package.regional_rules
        if r["factor"] == ROAD_WIDTH_FACTOR and r["grade_code"] == 2
    )
    grade3_rule_id = next(
        r["rule_id"] for r in package.regional_rules
        if r["factor"] == ROAD_WIDTH_FACTOR and r["grade_code"] == 3
    )
    edits = [
        {"rule_id": grade2_rule_id, "field": "lower_bound", "new_value": 18},
        {"rule_id": grade3_rule_id, "field": "upper_bound", "new_value": 18},
    ]
    resp = evaluation_standard.submit_evaluation_standard_edits(
        {"pathParameters": {"id": case_no, "package_id": package_id},
         "body": json.dumps({"edits": edits, "edited_by": edited_by})}, None,
    )
    assert resp["statusCode"] == 200, resp["body"]
    return json.loads(resp["body"])


def _strip_individual_rules_for_regional_only_scenario(case_no, package_id):
    """Test-setup helper, not a Phase 3B feature under test: the REAL PDF
    also yields several genuinely-EXTRACTED individual rules (面積/寬度/
    深度/...), and per Case Rule Override Semantics a non-empty
    individual_rules list REPLACES the static individual scope entirely.
    One of those real individual factors (深度) legitimately has a
    compound "未滿10m或100m以上" condition on its worst grade (see Phase
    3A's test_ambiguous_compound_condition_does_not_guess) that correctly
    forces its value_type to categorical -- incompatible with this test
    file's simplified _seed_full_case, which submits a plain numeric 深度
    value. Clearing individual_rules here keeps these tests focused on
    proving the CONFIRMED regional override is used (already exercised
    end-to-end for both scopes together by Phase 3A/Step 2's own tests),
    without needing this file to also model every real individual factor's
    submission shape."""
    from case_rule_repository import default_case_rule_repository
    repo = default_case_rule_repository()
    package = repo.get_package(case_no, package_id)
    package.individual_rules = []
    repo.save_candidate(package)


def _confirm(case_no, package_id, confirmed_by="reviewer"):
    import evaluation_standard
    resp = evaluation_standard.confirm_evaluation_standard(
        {"pathParameters": {"id": case_no, "package_id": package_id},
         "body": json.dumps({"confirmed_by": confirmed_by})}, None,
    )
    return resp


def _grade_of(body, field_id=ROAD_WIDTH_FIELD_ID):
    matches = [g for g in body["grades"] if g["field_id"] == field_id]
    assert matches, f"no grade entry for field_id={field_id!r} in {body['grades']}"
    return matches[0]


# ---------------------------------------------------------------------
# TEST 1 -- Importer result successfully saved as EXTRACTED
# ---------------------------------------------------------------------

class TestCandidatePersistence:
    def test_importer_result_saved_and_rereadable_by_case_and_package_id(self, env, monkeypatch):
        case_no = "EVALSTD-T1-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, dto = _upload_and_extract(case_no)

        import evaluation_standard
        resp = evaluation_standard.get_evaluation_standard_candidate(
            {"pathParameters": {"id": case_no, "package_id": package_id}}, None,
        )
        assert resp["statusCode"] == 200
        reread = json.loads(resp["body"])
        assert reread["package_id"] == package_id
        assert reread["case_id"] == case_no
        assert reread["summary"]["total_factors"] == 47
        rule_id, factor = _road_width_rule_id(reread)
        assert factor["status"] == "EXTRACTED"
        assert reread["status"] in ("EXTRACTED", "PARTIAL", "AMBIGUOUS")


# ---------------------------------------------------------------------
# TEST 2/3/4 -- EXTRACTED/PARTIAL/AMBIGUOUS cannot affect production
# ---------------------------------------------------------------------

class TestUnconfirmedCannotAffectResult:
    def test_extracted_status_package_does_not_affect_analyze(self, env, monkeypatch):
        import analyze
        case_no = "EVALSTD-T2-001"
        _seed_road_width_case(monkeypatch, case_no)
        _upload_and_extract(case_no)  # never confirmed

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "普通" in _grade_of(body)["grade"]

    def test_partial_status_package_does_not_affect_analyze(self, env, monkeypatch):
        import analyze
        from domain.models import CaseRulePackageStatus
        from case_rule_repository import default_case_rule_repository

        case_no = "EVALSTD-T3-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, dto = _upload_and_extract(case_no)
        assert dto["status"] == "PARTIAL"  # 19/47 extracted, 28 unmapped -> mixed

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "普通" in _grade_of(body)["grade"]

    def test_ambiguous_status_package_does_not_affect_analyze(self, env, monkeypatch):
        import analyze
        import datetime as dt
        from domain.models import CaseRulePackage, CaseRulePackageStatus
        from case_rule_repository import default_case_rule_repository

        case_no = "EVALSTD-T4-001"
        _seed_road_width_case(monkeypatch, case_no)
        # A genuinely AMBIGUOUS package (zero usable rule records at all).
        package = CaseRulePackage(
            case_id=case_no, package_id="EVALSTD-T4-PKG", rule_version="v1",
            source_document="synthetic", source_sha256=None, source_type="PDF_DETERMINISTIC_EXTRACTION",
            status=CaseRulePackageStatus.AMBIGUOUS, regional_rules=[], individual_rules=[],
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        default_case_rule_repository().save_candidate(package)

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "CASE_RULE_NOT_CONFIRMED" in body["rule_resolution_warnings"]


# ---------------------------------------------------------------------
# TEST 5/6 -- Confirmation flow + validation gate
# ---------------------------------------------------------------------

class TestConfirmationFlow:
    def test_valid_extracted_confirms_successfully(self, env, monkeypatch):
        case_no = "EVALSTD-T5-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)

        resp = _confirm(case_no, package_id)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["status"] == "CONFIRMED"

    def test_validation_error_prevents_confirmation(self, env, monkeypatch):
        from case_rule_repository import default_case_rule_repository

        case_no = "EVALSTD-T6-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)

        repo = default_case_rule_repository()
        package = repo.get_package(case_no, package_id)
        del package.regional_rules[0]["max_adjustment"]  # corrupt -> structural ERROR
        import case_store as _cs
        _cs.put_record(case_no, f"CASE_RULE_PACKAGE#{package_id}", package.model_dump(mode="json"))

        resp = _confirm(case_no, package_id)
        assert resp["statusCode"] == 409
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "CASE_RULE_VALIDATION_FAILED"
        assert body["validation_issues"]

        repo2 = default_case_rule_repository()
        assert repo2.get_package(case_no, package_id).status != "CONFIRMED"


# ---------------------------------------------------------------------
# TEST 7/8 -- Human edit provenance
# ---------------------------------------------------------------------

class TestHumanEditProvenance:
    def test_human_edit_preserved_in_provenance(self, env, monkeypatch):
        from case_rule_repository import default_case_rule_repository

        case_no = "EVALSTD-T7-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)

        _widen_road_width_via_human_edit(case_no, package_id, edited_by="reviewer-A")

        package = default_case_rule_repository().get_package(case_no, package_id)
        assert package.edit_history
        # _widen_road_width_via_human_edit() now submits TWO edits in one
        # call (grade2.lower_bound AND grade3.upper_bound, to avoid a
        # genuine range overlap -- see that helper's docstring), so find
        # the lower_bound one explicitly rather than assuming it is last.
        edit = next(e for e in package.edit_history if e.field == "lower_bound")
        assert edit.confirmed_value == 18
        assert edit.edited is True
        assert edit.confirmed_by == "reviewer-A"
        assert edit.confirmed_at is not None

    def test_original_extracted_value_preserved(self, env, monkeypatch):
        from case_rule_repository import default_case_rule_repository

        case_no = "EVALSTD-T8-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)

        repo = default_case_rule_repository()
        before = repo.get_package(case_no, package_id)
        target_rule_id = next(
            r["rule_id"] for r in before.regional_rules
            if r["factor"] == ROAD_WIDTH_FACTOR and r["grade_code"] == 2
        )
        original_value_in_record = next(
            r["lower_bound"] for r in before.regional_rules if r["rule_id"] == target_rule_id
        )
        assert original_value_in_record == 20.0  # Importer's real extracted value

        _widen_road_width_via_human_edit(case_no, package_id)

        after = repo.get_package(case_no, package_id)
        edit = next(e for e in after.edit_history if e.rule_id == target_rule_id and e.field == "lower_bound")
        assert edit.original_extracted_value == original_value_in_record
        # And the ORIGINAL is never mutated in place -- only the audit
        # trail changes, the live record moves to the NEW value.
        live_value = next(r["lower_bound"] for r in after.regional_rules if r["rule_id"] == target_rule_id)
        assert live_value == 18


# ---------------------------------------------------------------------
# TEST 9 -- Case A cannot confirm Case B package
# ---------------------------------------------------------------------

class TestCaseIsolation:
    def test_case_a_cannot_confirm_case_b_package(self, env, monkeypatch):
        from case_rule_repository import CaseRulePackageNotFoundError, default_case_rule_repository

        case_a, case_b = "EVALSTD-T9-A", "EVALSTD-T9-B"
        _seed_road_width_case(monkeypatch, case_a)
        _seed_road_width_case(monkeypatch, case_b)
        _document_id, package_id, _dto = _upload_and_extract(case_a)

        resp = _confirm(case_b, package_id)
        assert resp["statusCode"] == 404

        repo = default_case_rule_repository()
        with pytest.raises(CaseRulePackageNotFoundError):
            repo.confirm(case_b, package_id, confirmed_by="attacker")
        # The package under its REAL owner is untouched and still confirmable.
        assert repo.get_package(case_a, package_id).status != "CONFIRMED"


# ---------------------------------------------------------------------
# TEST 10/11 -- Reject + re-upload flow
# ---------------------------------------------------------------------

class TestRejectAndReupload:
    def test_rejected_package_never_affects_result(self, env, monkeypatch):
        import analyze
        import evaluation_standard

        case_no = "EVALSTD-T10-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)
        _widen_road_width_via_human_edit(case_no, package_id)
        assert _confirm(case_no, package_id)["statusCode"] == 200

        resp = evaluation_standard.reject_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": package_id},
             "body": json.dumps({"reason": "改用其他版本", "rejected_by": "reviewer"})}, None,
        )
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["status"] == "REJECTED"

        body = json.loads(analyze.analyze({"pathParameters": {"id": case_no}}, None)["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "普通" in _grade_of(body)["grade"]

    def test_second_upload_after_rejection_works(self, env, monkeypatch):
        case_no = "EVALSTD-T11-001"
        _seed_road_width_case(monkeypatch, case_no)

        _doc1, package_a, _dto = _upload_and_extract(case_no, package_id="EVALSTD-T11-PKG-A")
        assert _confirm(case_no, package_a)["statusCode"] == 200

        import evaluation_standard
        evaluation_standard.reject_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": package_a},
             "body": json.dumps({"reason": "上錯檔案", "rejected_by": "reviewer"})}, None,
        )

        _doc2, package_b, _dto2 = _upload_and_extract(case_no, package_id="EVALSTD-T11-PKG-B")
        resp = _confirm(case_no, package_b)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["package_id"] == package_b


# ---------------------------------------------------------------------
# TEST 12 -- Only one CONFIRMED package per case
# ---------------------------------------------------------------------

class TestMultipleConfirmedPackagesForbidden:
    def test_only_one_confirmed_package_per_case(self, env, monkeypatch):
        case_no = "EVALSTD-T12-001"
        _seed_road_width_case(monkeypatch, case_no)
        _doc1, package_a, _ = _upload_and_extract(case_no, package_id="EVALSTD-T12-PKG-A")
        _doc2, package_b, _ = _upload_and_extract(case_no, package_id="EVALSTD-T12-PKG-B")

        assert _confirm(case_no, package_a)["statusCode"] == 200
        resp = _confirm(case_no, package_b)
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["error"]["code"] == "CASE_RULE_ALREADY_CONFIRMED"


# ---------------------------------------------------------------------
# TEST 13/14/15 -- Confirmed package used by Analyze/CompleteForm/Review
# ---------------------------------------------------------------------

class TestConfirmedPackageUsedByAllThreeHandlers:
    def test_analyze_uses_confirmed_evaluation_standard_package(self, env, monkeypatch):
        import analyze
        case_no = "EVALSTD-T13-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)
        _widen_road_width_via_human_edit(case_no, package_id)
        assert _confirm(case_no, package_id)["statusCode"] == 200

        body = json.loads(analyze.analyze({"pathParameters": {"id": case_no}}, None)["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert "稍優" in _grade_of(body)["grade"]

    def test_complete_form_uses_confirmed_evaluation_standard_package(self, env, monkeypatch):
        import complete_form
        case_no = "EVALSTD-T14-001"
        _seed_full_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)
        _widen_road_width_via_human_edit(case_no, package_id)
        _strip_individual_rules_for_regional_only_scenario(case_no, package_id)
        assert _confirm(case_no, package_id)["statusCode"] == 200

        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == package_id

    def test_review_uses_confirmed_evaluation_standard_package(self, env, monkeypatch):
        import complete_form
        import review
        case_no = "EVALSTD-T15-001"
        _seed_full_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)
        _widen_road_width_via_human_edit(case_no, package_id)
        _strip_individual_rules_for_regional_only_scenario(case_no, package_id)
        assert _confirm(case_no, package_id)["statusCode"] == 200

        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == package_id


# ---------------------------------------------------------------------
# TEST 16 -- CASE_RULE_NOT_CONFIRMED warning surfaced
# ---------------------------------------------------------------------

class TestNotConfirmedWarningSurfaced:
    def test_unconfirmed_upload_surfaces_warning_not_silent_static(self, env, monkeypatch):
        import analyze
        case_no = "EVALSTD-T16-001"
        _seed_road_width_case(monkeypatch, case_no)
        _upload_and_extract(case_no)  # extracted, never confirmed

        body = json.loads(analyze.analyze({"pathParameters": {"id": case_no}}, None)["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "CASE_RULE_NOT_CONFIRMED" in body["rule_resolution_warnings"]


# ---------------------------------------------------------------------
# TEST 17 -- No Mock fallback
# ---------------------------------------------------------------------

class TestNoMockFallback:
    def test_invalid_confirmed_package_persists_nothing(self, env, monkeypatch):
        import analyze
        import case_store
        import case_rule_repository as crr
        from case_rule_repository import default_case_rule_repository

        case_no = "EVALSTD-T17-001"
        _seed_road_width_case(monkeypatch, case_no)
        _document_id, package_id, _dto = _upload_and_extract(case_no)
        _widen_road_width_via_human_edit(case_no, package_id)
        assert _confirm(case_no, package_id)["statusCode"] == 200

        repo = default_case_rule_repository()
        drifted = repo.get_package(case_no, package_id)
        del drifted.regional_rules[0]["max_adjustment"]
        case_store.put_record(case_no, crr._package_sk(package_id), drifted.model_dump(mode="json"))

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["error"]["code"] == "CASE_RULE_INVALID"
        assert case_store.get_record(case_no, "ANALYSIS") is None


# ---------------------------------------------------------------------
# TEST 18 -- No Golden fallback
# ---------------------------------------------------------------------

class TestNoGoldenFallback:
    def test_unrelated_case_never_inherits_another_cases_confirmed_edit(self, env, monkeypatch):
        import analyze
        case_a, case_b = "EVALSTD-T18-A", "EVALSTD-T18-B"
        _seed_road_width_case(monkeypatch, case_a)
        _seed_road_width_case(monkeypatch, case_b)

        _document_id, package_id, _dto = _upload_and_extract(case_a)
        _widen_road_width_via_human_edit(case_a, package_id)
        assert _confirm(case_a, package_id)["statusCode"] == 200

        body_a = json.loads(analyze.analyze({"pathParameters": {"id": case_a}}, None)["body"])
        body_b = json.loads(analyze.analyze({"pathParameters": {"id": case_b}}, None)["body"])
        assert "稍優" in _grade_of(body_a)["grade"]
        assert "普通" in _grade_of(body_b)["grade"]
        assert body_b["rule_resolution_status"] == "STATIC_LOCAL"
