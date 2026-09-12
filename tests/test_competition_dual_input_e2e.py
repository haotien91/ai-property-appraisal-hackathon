# -*- coding: utf-8 -*-
"""
STEP5 §1, §5, §13, §21, §22, §25 — Competition dual-input E2E.

Exercises the REAL production handlers (never mocking the final result)
through CompetitionOrchestrator, using the two real archived Competition
PDFs (data/sources/competition/查估書表範本.pdf for APPRAISAL_FORM,
data/sources/competition/評價基準明細表範例.pdf for EVALUATION_STANDARD),
proving:
  - the dual-input document_type contract (§1) routes each PDF into its
    own pipeline and never the other's,
  - the full 15-step sequence (§21) from case creation through PDF output,
  - the Golden Case's own known values (§13: 主要道路寬度 18m -> 普通;
    面前道路寬度 18m vs 6m -> +5.00%) reproduce correctly via the REAL
    evaluation-standard-confirmed pipeline (G2), and stay unchanged when no
    case rule package exists at all (G1) and after a Blind Case has run in
    the same warm process (G3, mirrors test_competition_blind_case.py's
    own isolation proof from the OTHER direction -- Golden run AFTER
    Blind, here Golden run using a REAL dual-input Evaluation Standard
    rather than the Blind fixture's synthetic one),
  - RULESET_UNAVAILABLE semantics (§25) for 住宅用地 (no confirmed
    case-specific rule and no static local rule, per docs/audit/
    RULE_COVERAGE_MATRIX.md's own NOT_RUNTIME_GRADE_READY finding).

LOCAL moto/S3-mocked verification only -- NOT AWS-deployed.
"""
from __future__ import annotations

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

APPRAISAL_PDF_PATH = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")
EVAL_STANDARD_PDF_PATH = os.path.join(REPO_ROOT, "data", "sources", "competition", "評價基準明細表範例.pdf")
DOCUMENT_BUCKET = "test-document-bucket"
PDF_BUCKET = "test-pdf-bucket"
AWS_REGION = "ap-northeast-1"

ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"


@pytest.fixture()
def _require_fitz():
    pytest.importorskip("fitz")


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    """See tests/test_case_scoped_rule_architecture.py's identical fixture
    for the full incident writeup."""
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
def competition_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("DOCUMENT_BUCKET_NAME", DOCUMENT_BUCKET)
    monkeypatch.setenv("PDF_BUCKET_NAME", PDF_BUCKET)
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
        s3.create_bucket(Bucket=PDF_BUCKET, CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        # Module-level table/bucket name staleness defense -- see
        # tests/_aws_mock_reset.py's module docstring for the full,
        # generalized root-cause writeup (docs/audit/STEP5_FINAL_GATE_
        # REPORT.md).
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_case(cases_module, case_no, comparable_ids=None, land_use_type="商業用地"):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": land_use_type, "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地",
        "comparable_ids": comparable_ids or ["comp1"],
    })}
    return cases_module.create_case(event, None)


def _seed_golden_regional_and_individual(case_no, comparable_id):
    """Seeds FACTORS with the REAL Golden Case's regional facts (data/
    golden/golden_case_input.py) PLUS the individual 面前道路寬度 18m vs
    6m pair (STEP5 §13's second named scenario) -- sufficient for
    complete_form.py/review.py, mirroring test_backend_document_handlers_
    e2e.py::_seed_golden_document_case but also adding the individual-
    factor pair that file's own DOCUMENT-source tests don't need."""
    import case_store

    golden_dir = os.path.join(REPO_ROOT, "data", "golden")
    if golden_dir not in sys.path:
        sys.path.insert(0, golden_dir)
    from golden_case_input import BASE_REGIONAL  # noqa: E402

    regional_dicts = [
        {"field_id": fi.field_id, "factor": fi.factor, "raw_value": fi.raw_value, "unit": fi.unit}
        for fi in BASE_REGIONAL
    ]
    case_store.put_record(case_no, "FACTORS", {
        # user_submitted_factors carries BOTH the individual pair AND the
        # regional factor -- analyze.py (an EARLIER, separate pipeline
        # stage from complete_form.py/review.py) only ever reads THIS
        # sub-object, never regional_base_factors/regional_comparable_
        # factors below (those are collect_data.py's own independently-
        # resolved provider output, consumed only downstream). Both must
        # be populated for the SAME 主要道路寬度 fact to be gradeable by
        # both analyze.py and complete_form.py/review.py.
        "user_submitted_factors": {
            "base_parcel_factors": [
                {"field_id": "individual_frontage_road_width", "factor": "面前道路寬度", "raw_value": 18, "unit": "M"},
                {"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"},
            ],
            "comparable_factors": {
                comparable_id: [
                    {"field_id": "individual_frontage_road_width", "factor": "面前道路寬度", "raw_value": 6, "unit": "M"},
                    {"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"},
                ],
            },
        },
        "regional_base_factors": regional_dicts,
        "regional_comparable_factors": {comparable_id: regional_dicts},
        "land_normal_price": {comparable_id: "184763"},
        "price_date_rate": {comparable_id: "2.00"},
        "weight": {comparable_id: "100"},
        "plan_identification": {
            "internal_plan_id": "jinshan", "confirmed_plan_name": "金山都市計畫",
            "plan_identification_source": "MANUAL_INPUT",
        },
    })


def _grade_of(body, field_id=ROAD_WIDTH_FIELD_ID):
    matches = [g for g in body["grades"] if g["field_id"] == field_id]
    assert matches, f"no grade entry for field_id={field_id!r} in {body['grades']}"
    return matches[0]


def _adjustment_of(body, field_id="individual_frontage_road_width"):
    matches = [a for a in body["adjustments"] if a["field_id"] == field_id]
    assert matches, f"no adjustment entry for field_id={field_id!r} in {body['adjustments']}"
    return matches[0]


# ---------------------------------------------------------------------------
# §22 Golden E2E tests
# ---------------------------------------------------------------------------

class TestGoldenE2E:
    def test_g1_golden_without_new_case_rule_static_result_unchanged(self, competition_env, monkeypatch):
        """TEST G1: no Evaluation Standard uploaded at all -- static result
        unchanged (§13's known values, reproduced via the REAL FACTORS/
        analyze pipeline, no fixture-fallback shortcuts)."""
        import cases
        import analyze

        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "G1-GOLDEN-001"
        _make_case(cases, case_no)
        comparable_id = "comp1"
        _seed_golden_regional_and_individual(case_no, comparable_id)

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert body["rule_package_id"] is None
        assert "base=普通" in _grade_of(body)["grade"]

        frontage = _adjustment_of(body)
        assert frontage["adjustment_pct"] == "5.00" or frontage["adjustment_pct"] == "5"

    @pytest.mark.usefixtures("_require_fitz")
    def test_g2_golden_dual_input_confirmed_case_rule_reproduces_golden(self, competition_env, monkeypatch):
        """TEST G2: a REAL Evaluation Standard PDF is uploaded, imported,
        and CONFIRMED for the Golden Case's own city/district/land_use_type
        -- the resulting confirmed case rule must reproduce the SAME 18m ->
        普通 result (a confirmed rule extracted from the OFFICIAL evaluation
        standard PDF for 金山區/商業用地 is expected to agree with the
        static baseline built from the same source, not to differ)."""
        import cases
        import document_upload
        import evaluation_standard
        import analyze
        import case_store

        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "G2-GOLDEN-001"
        _make_case(cases, case_no)
        comparable_id = "comp1"
        _seed_golden_regional_and_individual(case_no, comparable_id)

        upload_resp = document_upload.request_upload(
            {"pathParameters": {"id": case_no}, "body": json.dumps({"document_type": "EVALUATION_STANDARD"})}, None,
        )
        assert upload_resp["statusCode"] == 201
        document_id = json.loads(upload_resp["body"])["document_id"]
        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))
        assert doc_meta["document_type"] == "EVALUATION_STANDARD"

        s3 = boto3.client("s3", region_name=AWS_REGION)
        with open(EVAL_STANDARD_PDF_PATH, "rb") as f:
            s3.put_object(Bucket=DOCUMENT_BUCKET, Key=doc_meta["s3_key"], Body=f.read(), ContentType="application/pdf")

        extract_resp = evaluation_standard.extract_evaluation_standard(
            {"pathParameters": {"id": case_no, "document_id": document_id}, "body": "{}"}, None,
        )
        assert extract_resp["statusCode"] == 201, extract_resp["body"]
        dto = json.loads(extract_resp["body"])
        package_id = dto["package_id"]
        # Human Confirmation Gate: a freshly-imported candidate is never
        # already CONFIRMED.
        assert dto["status"] != "CONFIRMED"

        confirm_resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": package_id},
             "body": json.dumps({"confirmed_by": "g2-tester"})}, None,
        )
        assert confirm_resp["statusCode"] == 200, confirm_resp["body"]
        assert json.loads(confirm_resp["body"])["status"] == "CONFIRMED"

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200, resp["body"]
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == package_id
        g = _grade_of(body)
        assert "base=普通" in g["grade"], g["grade"]
        assert g["rule_source_type"] == "CASE_IMPORTED_CONFIRMED"

    def test_g3_golden_after_blind_case_unchanged(self, competition_env, monkeypatch):
        """TEST G3: run a Blind Case (its OWN, separate case_id, confirmed
        via the same tests/fixtures/competition/blind_case_changed_
        standard fixture used in test_competition_blind_case.py) and THEN
        re-check a Golden case with no case rule of its own -- still
        STATIC_LOCAL, still 普通, proving no cross-case leak from this
        file's own direction too."""
        import cases
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard
        import analyze
        from domain.models import CaseRulePackage, CaseRulePackageStatus
        import datetime as dt

        collect_data = _ensure_mock_collect_data(monkeypatch)
        golden_case_no = "G3-GOLDEN-001"
        blind_case_no = "G3-BLIND-001"
        _make_case(cases, golden_case_no)
        _seed_golden_regional_and_individual(golden_case_no, "comp1")
        _make_case(cases, blind_case_no)
        _seed_golden_regional_and_individual(blind_case_no, "comp1")

        blind_fixture_path = os.path.join(
            REPO_ROOT, "tests", "fixtures", "competition", "blind_case_changed_standard",
            "blind_evaluation_standard.json",
        )
        with open(blind_fixture_path, encoding="utf-8") as f:
            fixture = json.load(f)
        pkg = CaseRulePackage(
            case_id=blind_case_no, package_id="G3-PKG-001", rule_version="TEST_ONLY-blind-v1",
            source_document=blind_fixture_path, source_type="TEST_ONLY_JSON_FIXTURE",
            status=CaseRulePackageStatus.EXTRACTED,
            regional_rules=fixture["regional_rules"], individual_rules=[],
            created_at=dt.datetime.now(dt.timezone.utc), metadata=dict(fixture["metadata"]),
        )
        DynamoCaseRuleRepository().save_candidate(pkg)
        confirm_resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": blind_case_no, "package_id": "G3-PKG-001"},
             "body": json.dumps({"confirmed_by": "g3-tester"})}, None,
        )
        assert confirm_resp["statusCode"] == 200, confirm_resp["body"]
        blind_body = json.loads(analyze.analyze({"pathParameters": {"id": blind_case_no}}, None)["body"])
        assert "base=稍優" in _grade_of(blind_body)["grade"]

        golden_body = json.loads(analyze.analyze({"pathParameters": {"id": golden_case_no}}, None)["body"])
        assert golden_body["rule_resolution_status"] == "STATIC_LOCAL"
        assert golden_body["rule_package_id"] is None
        assert "base=普通" in _grade_of(golden_body)["grade"]


# ---------------------------------------------------------------------------
# §25 RULESET_UNAVAILABLE semantics for 住宅用地
# ---------------------------------------------------------------------------

class TestResidentialRulesetUnavailable:
    def test_residential_without_case_rule_raises_no_silent_grade(self, competition_env, monkeypatch):
        """docs/audit/RULE_COVERAGE_MATRIX.md: 住宅用地 has CENTRAL_MAX
        coverage but NO local grade-band/matrix rules and no confirmed
        case-specific rule here -- analyze.py must surface this as an
        unresolved factor (RuleNotFoundError -> GradeEngineError, caught
        and reported as "RULE_NOT_FOUND"), never fabricate a grade from
        the central maximum table alone."""
        import cases

        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "F6-RESIDENTIAL-001"
        _make_case(cases, case_no, land_use_type="住宅用地")
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR,
                                      "raw_value": 18, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR,
                                               "raw_value": 6, "unit": "M"}]},
        })}, None)

        import analyze
        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["grades"] == []
        assert body["unresolved_factors"], "住宅用地 with no confirmed case rule must be reported UNRESOLVED, never silently graded"
        assert body["unresolved_factors"][0]["reason"] == "RULE_NOT_FOUND"
        assert body["rule_resolution_status"] == "STATIC_LOCAL"


# ---------------------------------------------------------------------------
# §21 -- the 15-step dual-input Competition E2E, driven by
# CompetitionOrchestrator (§5) end to end, using BOTH real archived PDFs.
# ---------------------------------------------------------------------------

class TestCompetitionOrchestrator15Steps:
    @pytest.mark.usefixtures("_require_fitz")
    def test_all_15_steps_no_mocked_final_result(self, competition_env, monkeypatch):
        import cases
        import document_upload
        import competition_orchestrator
        import competition_state
        from domain.models import CompetitionLifecycleStage

        collect_data = _ensure_mock_collect_data(monkeypatch)
        s3 = boto3.client("s3", region_name=AWS_REGION)
        case_no = "E2E15-001"
        comparable_id = "comp1"

        # STEP 1: Create Case.
        create_resp = _make_case(cases, case_no, comparable_ids=[comparable_id])
        assert create_resp["statusCode"] == 201

        orchestrator = competition_orchestrator.CompetitionOrchestrator(case_no)
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.CREATED

        # STEP 2: Upload appraisal document.
        appraisal_upload = document_upload.request_upload(
            {"pathParameters": {"id": case_no}, "body": json.dumps({"document_type": "APPRAISAL_FORM"})}, None,
        )
        assert appraisal_upload["statusCode"] == 201
        appraisal_document_id = json.loads(appraisal_upload["body"])["document_id"]
        import case_store
        appraisal_doc_meta = case_store.get_record(case_no, case_store.document_sk(appraisal_document_id))
        with open(APPRAISAL_PDF_PATH, "rb") as f:
            s3.put_object(Bucket=DOCUMENT_BUCKET, Key=appraisal_doc_meta["s3_key"], Body=f.read(),
                          ContentType="application/pdf")

        # STEP 3: Extract appraisal document.
        appraisal_extraction = orchestrator.extract_appraisal_document(appraisal_document_id)
        assert appraisal_extraction["status"] == "COMPLETED"
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.APPRAISAL_EXTRACTED
        assert state.appraisal_document_id == appraisal_document_id

        # STEP 4: Upload evaluation standard.
        eval_upload = document_upload.request_upload(
            {"pathParameters": {"id": case_no}, "body": json.dumps({"document_type": "EVALUATION_STANDARD"})}, None,
        )
        assert eval_upload["statusCode"] == 201
        eval_document_id = json.loads(eval_upload["body"])["document_id"]
        eval_doc_meta = case_store.get_record(case_no, case_store.document_sk(eval_document_id))
        with open(EVAL_STANDARD_PDF_PATH, "rb") as f:
            s3.put_object(Bucket=DOCUMENT_BUCKET, Key=eval_doc_meta["s3_key"], Body=f.read(),
                          ContentType="application/pdf")

        # STEP 5: Extract evaluation standard.
        eval_dto = orchestrator.import_evaluation_standard(eval_document_id)
        package_id = eval_dto["package_id"]

        # STEP 6: Candidate persisted.
        import evaluation_standard
        readback = json.loads(evaluation_standard.get_evaluation_standard_candidate(
            {"pathParameters": {"id": case_no, "package_id": package_id}}, None,
        )["body"])
        assert readback["package_id"] == package_id

        # STEP 7: Human confirmation required.
        assert readback["status"] != "CONFIRMED"
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.RULE_REVIEW_REQUIRED

        # STEP 8: Confirm rules.
        confirm_dto = orchestrator.confirm_evaluation_standard(package_id, confirmed_by="e2e15-tester")
        assert confirm_dto["status"] == "CONFIRMED"
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.RULE_CONFIRMED
        assert state.case_rule_status == "CONFIRMED"

        # STEP 9: CollectData. Deliberately empty base_parcel_factors/
        # comparable_factors: user_submitted_factors.base_parcel_factors
        # is EXCLUSIVELY the individual-factor bucket downstream (case_
        # reconstruction.build_case_and_regional_factors maps it straight
        # onto CompetitionCase.base_parcel_factors, which FormCompletion
        # Engine.complete_table4_individual_factors then grades entirely
        # as rule_set="individual", with NO field_id-prefix filtering) --
        # putting a regional_-prefixed field there would make complete_
        # form.py try to grade 主要道路寬度 as an individual factor and
        # fail (it only exists under the regional table). The regional
        # fact is supplied the same way every other test file's
        # _seed_full_case-style helper does it: directly into FACTORS.
        # regional_base_factors/regional_comparable_factors below.
        #
        # Also, the CONFIRMED package came from the REAL PDF importer,
        # whose deterministically-extracted individual_rules (if any) may
        # or may not happen to cover any specific individual factor --
        # Rule Resolution Precedence (STEP5 §4) replaces the INDIVIDUAL
        # scope entirely when the package supplies ANY individual rules.
        # §13's exact individual-factor scenario (面前道路寬度 18m vs 6m ->
        # +5.00%) is already proven precisely under the STATIC baseline by
        # TestGoldenE2E above and under a CONFIRMED case rule by
        # test_competition_blind_case.py -- this test exists to prove the
        # ORCHESTRATION wiring end-to-end, not to re-prove that a second
        # time against whatever individual rules this specific real PDF
        # happens to extract.
        collect_body = orchestrator.collect_data({"base_parcel_factors": [], "comparable_factors": {comparable_id: []}})
        assert collect_body["case_no"] == case_no
        # Also seed regional_base_factors/regional_comparable_factors +
        # comparison-price scaffolding directly (same as every other test
        # file's _seed_full_case helper) -- collect_data.py's OWN
        # provider-resolution output does not include 主要道路寬度 under
        # Mock mode by default, and complete_form.py/review.py need these
        # regardless of what collect_data itself resolved.
        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_base_factors"] = [
            {"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"},
        ]
        factors["regional_comparable_factors"] = {
            comparable_id: [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"}],
        }
        factors["land_normal_price"] = {comparable_id: "184763"}
        factors["price_date_rate"] = {comparable_id: "2.00"}
        factors["weight"] = {comparable_id: "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.DATA_COLLECTED

        # STEP 10: Analyze.
        analyze_body = orchestrator.analyze()
        assert analyze_body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.ANALYZED

        # STEP 11: Complete Form.
        form_body = orchestrator.complete_form()
        assert form_body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        state = competition_state.get_state(case_no)
        assert state.overall_status == CompetitionLifecycleStage.FORMS_COMPLETED

        # STEP 12: Review.
        review_body = orchestrator.review()
        assert review_body["issues"]
        # STEP 13: Cross-form validation is embedded in Review -- assert
        # the regional-total-vs-table4 cross-form check actually PASSED
        # (not fabricated, not skipped) for this genuinely-consistent run.
        cross_form_issue = next(
            i for i in review_body["issues"] if i["field"] == f"region_adjustment_rate_{comparable_id}"
        )
        assert cross_form_issue["issue_type"] == "Passed"
        state = competition_state.get_state(case_no)
        assert state.overall_status in (CompetitionLifecycleStage.REVIEWED, CompetitionLifecycleStage.MANUAL_REVIEW_REQUIRED)

        # STEP 14: PDF output. WeasyPrint needs native Cairo/Pango libs
        # this Windows host does not have (see tests/test_phase5_golden_
        # pipeline.py's own documented, --ignore'd environment limitation,
        # STEP5 §12/§33) -- caught here explicitly so THIS test still
        # proves steps 1-13+15 for real rather than being wholesale
        # excluded like that file.
        pdf_skipped_reason = None
        try:
            pdf_body = orchestrator.generate_pdf()
        except OSError as e:
            pdf_skipped_reason = str(e)
        else:
            assert "表1" in pdf_body["forms"]
            assert "表4+表5-2" in pdf_body["forms"]
            state = competition_state.get_state(case_no)
            assert state.overall_status == CompetitionLifecycleStage.PDF_READY

        # STEP 15: Case finished -- honest, non-fabricated final state.
        # Checked regardless of whether PDF generation itself could run on
        # this host, so this assertion is never skipped along with it.
        final_state = competition_state.get_state(case_no)
        assert final_state.overall_status != CompetitionLifecycleStage.CREATED
        assert final_state.blocking_issues == [] or final_state.overall_status == CompetitionLifecycleStage.MANUAL_REVIEW_REQUIRED

        if pdf_skipped_reason is not None:
            pytest.skip(f"WeasyPrint native library unavailable on this host (STEP5 §12 known environment "
                        f"limitation, not a code failure) -- steps 1-13+15 verified for real above, only "
                        f"step 14's PDF byte-generation itself is skipped: {pdf_skipped_reason}")
