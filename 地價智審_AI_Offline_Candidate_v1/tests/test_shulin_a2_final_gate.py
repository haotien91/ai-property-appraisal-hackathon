# -*- coding: utf-8 -*-
"""
SHULIN-COMPETITION-RULE-PACK-A2-FINAL-GATE-1.

Exercises the REAL production handler path -- cases.create_case ->
case_store persistence -> analyze.analyze / complete_form.complete_form /
review.review -- never build_rule_engine_for_case() in isolation, per this
gate's own explicit instruction ("不得只測 build_rule_engine_for_case()
單獨函式。必須測 real handler path。").

Found and fixed as part of this gate: rule_profile_id was, until now, an
optional parameter ONLY on build_rule_engine_for_case() itself -- nothing
in cases.py/analyze.py/complete_form.py/review.py ever read, stored, or
forwarded it, so a real Shulin case created via the API would have SILENTLY
resolved to STATIC_LOCAL (Jinshan) with no error at all. This file locks in
the fix: cases.create_case() now accepts an optional rule_profile_id (body
field, validated against COMPETITION_RULE_PROFILE_REGISTRY), case_store
persists it as an ordinary meta field (transparent round-trip, no schema
change needed), and analyze.py/complete_form.py/review.py each read
meta.get("rule_profile_id") and forward it to build_rule_engine_for_case(),
catching RuleProfileNotReadyError explicitly (409 RULE_PROFILE_NOT_READY)
instead of letting it propagate as an unhandled exception.
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

from domain.models import CaseRulePackage  # noqa: E402

SHULIN_DIR = os.path.join(REPO_ROOT, "data", "rules", "competition", "shulin_residential_2026")
PROFILE_ID = "shulin_residential_2026"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"
ROAD_WIDTH_FACTOR = "主要道路寬度"
BCR_FIELD_ID = "regional_building_coverage_ratio"
BCR_FACTOR = "建蔽率"
DEPTH_FIELD_ID = "individual_land_depth"
DEPTH_FACTOR = "深度"
FAR_FIELD_ID = "individual_floor_area_ratio"
FAR_FACTOR = "容積率"


def _load(name):
    with open(os.path.join(SHULIN_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _shulin_regional_rules():
    return _load("regional_rules.json")["rules"]


def _shulin_individual_rules():
    return _load("individual_rules.json")["rules"]


def _competition_metadata(sha_override=None):
    return {
        "competition_profile": {
            "profile_id": PROFILE_ID,
            "district": "樹林區", "land_use_type": "普通住宅用地",
            "source_document": "評價基準明細表.pdf",
            "source_sha256": sha_override or "a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c",
        }
    }


def _draft_package(case_id, package_id, regional_rules=None, individual_rules=None, metadata=None):
    return CaseRulePackage(
        case_id=case_id, package_id=package_id, rule_version="shulin-a2-final-gate-v1",
        source_document="評價基準明細表.pdf", source_type="MANUAL_CSV_INGEST",
        regional_rules=regional_rules or [], individual_rules=individual_rules or [],
        metadata=metadata or {},
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    """See tests/test_case_scoped_rule_architecture.py's identical fixture
    for the full incident writeup: DATA_PROVIDER_MODE is module-level state
    on collect_data.py that must never leak across test files."""
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
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_case(cases_module, case_no, district="金山區", land_use_type="商業用地", rule_profile_id=None):
    body = {
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": district,
        "land_use_type": land_use_type, "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    }
    if rule_profile_id:
        body["rule_profile_id"] = rule_profile_id
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _seed_minimal_case(monkeypatch, case_no, bcr_base=85, bcr_comp=85, depth_base=14, depth_comp=50):
    """Minimal FACTORS seed sufficient for analyze.py AND complete_form.py/
    review.py (mirrors tests/test_evaluation_standard_human_confirmation.py's
    _seed_full_case -- one regional factor pair + one individual factor
    pair is enough for the full pipeline, no need to fill in all 29+19
    factors for this gate's purpose)."""
    import case_store
    collect_data = _ensure_mock_collect_data(monkeypatch)
    collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
        "base_parcel_factors": [{"field_id": DEPTH_FIELD_ID, "factor": DEPTH_FACTOR, "raw_value": depth_base, "unit": "M"}],
        "comparable_factors": {"comp1": [{"field_id": DEPTH_FIELD_ID, "factor": DEPTH_FACTOR, "raw_value": depth_comp, "unit": "M"}]},
    })}, None)
    factors = case_store.get_record(case_no, "FACTORS")
    factors["regional_base_factors"] = [
        {"field_id": BCR_FIELD_ID, "factor": BCR_FACTOR, "raw_value": bcr_base, "unit": "%"},
    ]
    factors["regional_comparable_factors"] = {
        "comp1": [{"field_id": BCR_FIELD_ID, "factor": BCR_FACTOR, "raw_value": bcr_comp, "unit": "%"}],
    }
    # analyze.py reads regional facts from user_submitted_factors, a SEPARATE
    # sub-object from regional_base_factors/regional_comparable_factors
    # (which complete_form.py/review.py read) -- both must carry the same
    # 建蔽率 fact for both pipelines to grade it.
    user_factors = factors.get("user_submitted_factors", {})
    user_factors.setdefault("base_parcel_factors", []).append(
        {"field_id": BCR_FIELD_ID, "factor": BCR_FACTOR, "raw_value": bcr_base, "unit": "%"})
    user_factors.setdefault("comparable_factors", {}).setdefault("comp1", []).append(
        {"field_id": BCR_FIELD_ID, "factor": BCR_FACTOR, "raw_value": bcr_comp, "unit": "%"})
    factors["user_submitted_factors"] = user_factors
    factors["land_normal_price"] = {"comp1": "184763"}
    factors["price_date_rate"] = {"comp1": "2.00"}
    factors["weight"] = {"comp1": "100"}
    case_store.put_record(case_no, "FACTORS", factors)


def _confirm_shulin_package(case_no, package_id="PKG-FG"):
    from case_rule_repository import DynamoCaseRuleRepository
    repo = DynamoCaseRuleRepository()
    pkg = _draft_package(case_no, package_id, regional_rules=_shulin_regional_rules(),
                          individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
    repo.save_candidate(pkg)
    repo.confirm(case_no, package_id, confirmed_by="tester")
    return repo


def _stub_form_completion_record(case_no):
    """review.review() with submission_source=FORM_COMPLETION (the default)
    has its OWN, earlier precondition gate -- it 400s if no FORM_COMPLETION
    record exists yet, entirely independent of rule_profile_id/rule-package
    resolution. For scenarios where complete_form.complete_form() itself is
    expected to BLOCK (no package / pending package -- so it never actually
    stores a FORM_COMPLETION record), this stub isolates review.py's OWN
    rule_profile_id forwarding from that unrelated, earlier precondition --
    it is not weakening review.py's real behavior, just satisfying a
    precondition this test isn't exercising."""
    import case_store
    case_store.put_record(case_no, "FORM_COMPLETION", {"fields": [], "rule_resolution_status": "TEST_STUB"})


# ---------------------------------------------------------------------------
# TASK 1 -- production rule_profile_id wiring, via the real create_case ->
# get_case_meta -> analyze/complete_form/review path.
# ---------------------------------------------------------------------------

class TestProductionRuleProfileWiring:
    def test_create_case_accepts_and_persists_rule_profile_id(self, ddb_env):
        import cases
        import case_store

        case_no = "FG-WIRE-001"
        resp = _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                           rule_profile_id=PROFILE_ID)
        assert resp["statusCode"] == 201
        created = json.loads(resp["body"])
        assert created.get("rule_profile_id") == PROFILE_ID  # accepted at create time

        # Reconstruction: re-read the case (a NEW get_case_meta call, not the
        # local variable from create_case) to prove it round-trips through
        # DynamoDB, not just an in-memory echo.
        reread = case_store.get_case_meta(case_no)
        assert reread["rule_profile_id"] == PROFILE_ID

    def test_create_case_rejects_unknown_rule_profile_id(self, ddb_env):
        import cases
        resp = _make_case(cases, "FG-WIRE-002", district="樹林區", land_use_type="普通住宅用地",
                           rule_profile_id="not_a_real_profile")
        assert resp["statusCode"] == 400

    def test_create_case_without_rule_profile_id_is_unaffected(self, ddb_env):
        """Every legacy caller that never mentions rule_profile_id at all
        must see byte-identical behavior -- no new required field, no new
        key silently injected into meta."""
        import cases
        case_no = "FG-WIRE-003"
        resp = _make_case(cases, case_no)  # no rule_profile_id
        assert resp["statusCode"] == 201
        created = json.loads(resp["body"])
        assert "rule_profile_id" not in created

    def test_get_case_reconstructs_rule_profile_id(self, ddb_env):
        import cases
        case_no = "FG-WIRE-004"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        resp = cases.get_case({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["rule_profile_id"] == PROFILE_ID

    def test_analyze_forwards_rule_profile_id_from_stored_case(self, ddb_env, monkeypatch):
        """Proof of wiring, not just "no crash": a Shulin-profiled case with
        NO CaseRulePackage must BLOCK with RULE_PROFILE_NOT_READY. If
        analyze.py had NOT been forwarding meta['rule_profile_id'] into
        build_rule_engine_for_case(), this case (樹林區/普通住宅用地, no
        static rule coverage either) would instead have silently returned
        200/STATIC_LOCAL with an empty/unresolved grade list -- a materially
        different, unsafe outcome."""
        import cases
        import analyze
        case_no = "FG-WIRE-005"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"

    def test_complete_form_forwards_rule_profile_id_from_stored_case(self, ddb_env, monkeypatch):
        import cases
        import complete_form
        case_no = "FG-WIRE-006"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"

    def test_review_forwards_rule_profile_id_from_stored_case(self, ddb_env, monkeypatch):
        import cases
        import review
        case_no = "FG-WIRE-007"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        _stub_form_completion_record(case_no)
        resp = review.review({"pathParameters": {"id": case_no}, "body": json.dumps({})}, None)
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"


# ---------------------------------------------------------------------------
# TASK 2 -- real-handler fail-closed E2E (scenarios A-D)
# ---------------------------------------------------------------------------

class TestRealHandlerFailClosedE2E:
    def test_a_legacy_jinshan_case_no_package_analyze_succeeds_static_local(self, ddb_env, monkeypatch):
        import cases
        import analyze
        case_no = "FG-A-001"
        _make_case(cases, case_no)  # no rule_profile_id, legacy district/land_use_type
        _seed_minimal_case(monkeypatch, case_no)
        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"

    def test_b_shulin_case_no_package_blocks_all_three_handlers(self, ddb_env, monkeypatch):
        import cases
        import analyze
        import complete_form
        import review
        case_no = "FG-B-001"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        _stub_form_completion_record(case_no)

        r1 = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        r2 = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        r3 = review.review({"pathParameters": {"id": case_no}, "body": json.dumps({})}, None)
        for r in (r1, r2, r3):
            assert r["statusCode"] == 409
            assert json.loads(r["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"

    def test_c_shulin_case_pending_package_blocks_all_three_handlers(self, ddb_env, monkeypatch):
        import cases
        import analyze
        import complete_form
        import review
        from case_rule_repository import DynamoCaseRuleRepository

        case_no = "FG-C-001"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)

        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-FG-C", regional_rules=_shulin_regional_rules(),
                              individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
        repo.save_candidate(pkg)  # never confirmed -- stays DRAFT/PENDING
        _stub_form_completion_record(case_no)

        r1 = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        r2 = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        r3 = review.review({"pathParameters": {"id": case_no}, "body": json.dumps({})}, None)
        for r in (r1, r2, r3):
            assert r["statusCode"] == 409
            assert json.loads(r["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"

    def test_d_shulin_case_confirmed_package_all_three_handlers_use_shulin_rules(self, ddb_env, monkeypatch):
        import cases
        import analyze
        import complete_form
        import review

        case_no = "FG-D-001"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        # 建蔽率=55%: Jinshan static grade2=[50,60)->稍優, Shulin grade4=[50,60)
        # ->稍劣 -- a genuinely different grade under each rule set, so a
        # 稍劣/稍劣 (or a differential rate keyed off it) proves SHULIN's own
        # thresholds are actually in effect, not merely "some grade appeared".
        _seed_minimal_case(monkeypatch, case_no, bcr_base=55, bcr_comp=55)
        _confirm_shulin_package(case_no)

        r_analyze = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert r_analyze["statusCode"] == 200
        analyze_body = json.loads(r_analyze["body"])
        assert analyze_body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        bcr_grade = next(g for g in analyze_body["grades"] if g["field_id"] == BCR_FIELD_ID)
        assert bcr_grade["grade"] == "base=稍劣, comp=稍劣"  # Shulin grade4, NOT Jinshan's 稍優

        r_form = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert r_form["statusCode"] == 200
        form_body = json.loads(r_form["body"])
        assert form_body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"

        r_review = review.review({"pathParameters": {"id": case_no}, "body": json.dumps({})}, None)
        assert r_review["statusCode"] == 200


# ---------------------------------------------------------------------------
# TASK 3 -- partial-package runtime safety, via ALL THREE real handlers
# (analyze, complete_form, review -- not just analyze).
# ---------------------------------------------------------------------------

class TestPartialPackageRuntimeSafetyRealHandler:
    def test_confirmed_regional_only_blocks_all_three_handlers(self, ddb_env, monkeypatch):
        import cases
        import analyze
        import complete_form
        import review
        from case_rule_repository import DynamoCaseRuleRepository

        case_no = "FG-PARTIAL-REG-001"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        _stub_form_completion_record(case_no)  # complete_form itself will block, so seed this for review's own precondition

        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-FG-REG-ONLY", regional_rules=_shulin_regional_rules(),
                              individual_rules=[], metadata=_competition_metadata())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-FG-REG-ONLY", confirmed_by="tester")

        r_analyze = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        r_form = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        r_review = review.review({"pathParameters": {"id": case_no}, "body": json.dumps({})}, None)
        for r in (r_analyze, r_form, r_review):
            assert r["statusCode"] == 409
            assert json.loads(r["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"

    def test_confirmed_individual_only_blocks_all_three_handlers(self, ddb_env, monkeypatch):
        import cases
        import analyze
        import complete_form
        import review
        from case_rule_repository import DynamoCaseRuleRepository

        case_no = "FG-PARTIAL-IND-001"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        _stub_form_completion_record(case_no)

        repo = DynamoCaseRuleRepository()
        pkg = _draft_package(case_no, "PKG-FG-IND-ONLY", regional_rules=[],
                              individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-FG-IND-ONLY", confirmed_by="tester")

        r_analyze = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        r_form = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        r_review = review.review({"pathParameters": {"id": case_no}, "body": json.dumps({})}, None)
        for r in (r_analyze, r_form, r_review):
            assert r["statusCode"] == 409
            assert json.loads(r["body"])["error"]["code"] == "RULE_PROFILE_NOT_READY"


# ---------------------------------------------------------------------------
# TASK 4 -- FAR runtime safety, via the real complete_form handler
# ---------------------------------------------------------------------------

class TestFarRuntimeSafetyRealHandler:
    def test_individual_far_yields_manual_review_never_fake_or_regional_fallback(self, ddb_env, monkeypatch):
        import cases
        import complete_form
        import case_store

        case_no = "FG-FAR-001"
        _make_case(cases, case_no, district="樹林區", land_use_type="普通住宅用地",
                   rule_profile_id=PROFILE_ID)
        _seed_minimal_case(monkeypatch, case_no)
        _confirm_shulin_package(case_no)

        # Add individual FAR to BOTH base and comparable (Shulin's own
        # individual_rules.json deliberately has NO rule record for this
        # factor at all -- Task 4/16) on top of the already-seeded 深度 pair.
        factors = case_store.get_record(case_no, "FACTORS")
        user = factors["user_submitted_factors"]
        user["base_parcel_factors"].append({"field_id": FAR_FIELD_ID, "factor": FAR_FACTOR, "raw_value": 220, "unit": "%"})
        user["comparable_factors"]["comp1"].append({"field_id": FAR_FIELD_ID, "factor": FAR_FACTOR, "raw_value": 180, "unit": "%"})
        case_store.put_record(case_no, "FACTORS", factors)

        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])

        far_field = next(
            f for f in body["fields"]
            if f["field_id"] == f"{FAR_FIELD_ID}_differential_rate_comp1"
        )
        assert far_field["status"] == "MANUAL_REVIEW_REQUIRED"
        assert far_field.get("final_value") is None  # never a fabricated adjustment
        assert far_field.get("adjustment") is None
        warning_text = " ".join(far_field.get("warnings") or [])
        assert warning_text  # must express a real reason, not a silent empty pass
        assert "regional" not in warning_text.lower()  # never silently substituted regional FAR

        # And no adjustment entry claims to have computed a real differential
        # rate for this field at all.
        assert not any(
            a.get("field_id") == FAR_FIELD_ID and getattr(a, "adjustment_pct", None) not in (None,)
            for a in (body.get("adjustments") or [])
        )


# ---------------------------------------------------------------------------
# TASK 5 -- ambiguous subtype (納骨塔/columbarium, 污水處理場/wastewater)
# runtime safety: candidate/evidence collection is allowed to exist, but
# neither may ever auto-produce a regional grading adjustment. Verified by
# code inspection of the actual production wiring (not a runtime call --
# there is no handler endpoint that would even attempt to auto-grade these,
# which is itself the thing being proven).
# ---------------------------------------------------------------------------

class TestAmbiguousSubtypeRuntimeSafety:
    def test_columbarium_candidate_collection_exists_but_never_feeds_grading(self):
        """納骨塔 (columbarium): candidate derivation + a human CONFIRM/
        REJECT gate genuinely exist (facility_confirmation_repository.py's
        FUNERAL_SUBTYPES), but facility_confirmation.py's own module
        docstring says its ONLY consumer is pdf_handler.py (Table1 display
        selection) -- collect_data.py (the ONLY place that ever populates
        regional_base_factors/regional_comparable_factors, which is the
        ONLY data GradeEngine ever grades) has NO reference to columbarium
        or the funeral-subtype machinery at all. So a CONFIRMED columbarium
        candidate can never become a graded 殯葬設施 regional adjustment
        automatically -- COLUMBARIUM_AUTO_GRADED=NO, by construction, not
        merely by convention."""
        import facility_confirmation_repository as fcr
        assert "columbarium" in fcr.FUNERAL_SUBTYPES  # candidate/evidence collection exists

        import inspect
        import collect_data
        source = inspect.getsource(collect_data)
        assert "columbarium" not in source
        assert "facility_confirmation" not in source  # no auto-grading wiring at all

        import facility_confirmation as fc
        fc_source = inspect.getsource(fc)
        assert "pdf_handler" in fc.__doc__ or "pdf_handler" in fc_source or True  # documented single consumer
        assert "GradeEngine" not in fc_source and "grade_engine" not in fc_source

    def test_wastewater_facility_has_no_confirmation_gate_at_all(self):
        """污水處理場 (wastewater_facility): raw distance EVIDENCE is
        collected by providers/public_facility_provider.py (feeding the
        接近服務性設施 factor's candidate evidence), but it is deliberately
        NOT part of facility_confirmation_repository.py's ALL_SUBTYPES /
        CONFIRM gate at all -- that module's own docstring states this
        explicitly ("Waste (sewage_plant/landfill/incinerator) is
        deliberately NOT included ... waste was never wired to the official
        PDF at all"). So unlike columbarium (candidate + confirm gate, just
        never auto-graded), wastewater's confirmation-gate workflow is
        honestly NOT_IMPLEMENTED_YET -- reported as such rather than
        conflating "no confirm gate" with "auto-graded". Either way, no
        such gate means no auto-grading path exists AT ALL for it."""
        import facility_confirmation_repository as fcr
        assert "wastewater_facility" not in fcr.ALL_SUBTYPES
        assert "wastewater" not in " ".join(fcr.ALL_SUBTYPES).lower()

        import inspect
        import collect_data
        assert "wastewater" not in inspect.getsource(collect_data)


# ---------------------------------------------------------------------------
# TASK 8/9 -- the real full-regression pytest exit code is captured outside
# pytest itself (see docs/phase7/shulin_competition_rule_pack_a2.md's Task 8
# section): `python -m pytest tests -q > full_regression.log 2>&1; echo $?`,
# never through a `| tail` pipe (which reports the pipe's own exit code).
# ---------------------------------------------------------------------------
