# -*- coding: utf-8 -*-
"""
STEP5 §14-§19, §22, §25 — Blind Case tests.

Builds a genuinely different Evaluation Standard (tests/fixtures/
competition/blind_case_changed_standard/blind_evaluation_standard.json --
different 主要道路寬度 grade bands AND a different adjustment matrix step
from the real static baseline) and proves it changes BOTH Grade and
Adjustment Rate for the SAME 18m raw value, entirely through the REAL
production pipeline: CaseRuleRepository.save_candidate() (a JSON fixture
standing in for a PDF-extracted candidate, per STEP5 §20 -- "若以JSON
fixture表達，仍必須經過CaseRulePackage/confirmation path") -> the real
evaluation_standard.py Human Confirmation Gate HANDLERS (get_evaluation_
standard_candidate / confirm_evaluation_standard) -> analyze.py /
complete_form.py / review.py. NEVER a direct RuleEngine instantiation +
assert (STEP5 §15's explicit ban), and NEVER a case_id/road_width branch
in production code (§19) -- every Blind-specific number below lives only
in this tests/ file and its tests/fixtures/ JSON, never in engine/ or
backend/handlers/.

Reuses the EXACT moto/DynamoDB fixture pattern and seed helpers already
proven in tests/test_case_scoped_rule_architecture.py (STEP2), including
its documented DATA_PROVIDER_MODE cross-test-file leak defense.
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

# Every other STEP5-adjacent test file's very first case_rule_repository
# import is preceded by seeding a case (which transitively imports a
# handler that calls runtime_paths.bootstrap(), adding engine/providers to
# sys.path as a side effect) -- test_b1 below deliberately does NOT seed a
# case first (it only needs CaseRulePackage persistence, no case data), so
# bootstrap() is called explicitly here instead of relying on import order.
import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from domain.models import CaseRulePackage, CaseRulePackageStatus  # noqa: E402

BLIND_FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "competition", "blind_case_changed_standard",
    "blind_evaluation_standard.json",
)

ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    """See tests/test_case_scoped_rule_architecture.py's identical fixture
    for the full incident writeup -- this file's teardown half of the same
    defense."""
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
        # case_store.TABLE_NAME is bound ONCE, at case_store's first-ever
        # import in this process, from os.environ.get("CASES_TABLE_NAME",
        # "AIValuationCases") -- a module-level constant, never re-read
        # per-call. TestBlindMatrixDiffersFromStatic above runs with no
        # ddb_env active, but its OWN teardown (the autouse _restore_data_
        # provider_mode_for_later_modules fixture) imports collect_data,
        # which imports case_store for the very first time in THIS
        # process -- BEFORE CASES_TABLE_NAME is ever set to "test-table" --
        # permanently binding TABLE_NAME to the "AIValuationCases" default
        # in sys.modules. Reset here (see tests/_aws_mock_reset.py's
        # module docstring for the full, generalized root-cause writeup --
        # this same class of bug also hits document_upload.DOCUMENT_
        # BUCKET/evaluation_standard.DOCUMENT_BUCKET/pdf_handler.PDF_
        # BUCKET, not just case_store.TABLE_NAME) so every test in this
        # file sees the correct table/bucket names regardless of what ran
        # earlier in the same pytest process.
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


def _seed_road_width_case(monkeypatch, case_no, base_value=18, comp_value=3):
    """Minimal seed sufficient for analyze.py. comp_value=3 (not Golden's
    6) is a deliberate STEP5 choice: under BOTH the static bands (<10 ->
    劣/grade5) and the Blind bands (<5 -> 劣/grade5) it resolves to the
    SAME comparable grade, isolating that any adjustment-rate difference
    seen below comes from the BASE grade change (普通->稍優) and the
    matrix step change (3.75->4), not from the comparable's own grade
    also moving."""
    import cases

    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_case(cases, case_no)
    collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
        "base_parcel_factors": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR,
                                  "raw_value": base_value, "unit": "M"}],
        "comparable_factors": {"comp1": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR,
                                           "raw_value": comp_value, "unit": "M"}]},
    })}, None)


def _seed_full_case(monkeypatch, case_no, base_value=18, comp_value=3):
    """Full seed (mirrors test_case_scoped_rule_architecture.py's own
    _seed_full_case) -- sufficient for complete_form.py/review.py."""
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


def _load_blind_fixture():
    """The ONLY place this Blind JSON fixture is read -- a plain
    json.load(), NOT a new PDF parser (STEP5 §20 explicitly forbids
    writing a second Importer just for a test fixture)."""
    with open(BLIND_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _draft_blind_package(case_id, package_id, suffix=""):
    """Builds a CaseRulePackage DIRECTLY from the Blind JSON fixture --
    standing in for what evaluation_standard.py's extract_evaluation_
    standard() would have produced from a real uploaded PDF (STEP5 §20
    permits a JSON fixture here specifically to avoid a second Parser).
    rule_id values get `suffix` appended when non-empty so two Blind
    packages used in the SAME test (Blind A / Blind B, see
    TestGoldenBlindIsolation below) never collide on rule_id."""
    fixture = _load_blind_fixture()
    regional_rules = copy.deepcopy(fixture["regional_rules"])
    if suffix:
        for r in regional_rules:
            r["rule_id"] = f"{r['rule_id']}-{suffix}"
    return CaseRulePackage(
        case_id=case_id, package_id=package_id, rule_version=f"TEST_ONLY-blind-v1{suffix}",
        source_document=BLIND_FIXTURE_PATH, source_type="TEST_ONLY_JSON_FIXTURE",
        status=CaseRulePackageStatus.EXTRACTED,
        regional_rules=regional_rules, individual_rules=[],
        created_at=datetime.datetime.now(datetime.timezone.utc),
        metadata=dict(fixture["metadata"]),
    )


def _grade_of(body, field_id=ROAD_WIDTH_FIELD_ID):
    matches = [g for g in body["grades"] if g["field_id"] == field_id]
    assert matches, f"no grade entry for field_id={field_id!r} in {body['grades']}"
    return matches[0]


def _adjustment_of(body, field_id=ROAD_WIDTH_FIELD_ID):
    matches = [a for a in body["adjustments"] if a["field_id"] == field_id]
    assert matches, f"no adjustment entry for field_id={field_id!r} in {body['adjustments']}"
    return matches[0]


# ---------------------------------------------------------------------------
# §17 -- data-level proof that the Blind matrix genuinely differs from the
# static matrix for the SAME base/comparable grade pair (grade_code 2 vs 5),
# independent of whichever grades a specific 18m/3m scenario resolves to.
# ---------------------------------------------------------------------------

class TestBlindMatrixDiffersFromStatic:
    def test_same_grade_pair_different_rate(self):
        static_rules = _static_road_width_rules()
        blind_rules = _load_blind_fixture()["regional_rules"]
        static_matrix = static_rules[0]["adjustment_matrix"]
        blind_matrix = blind_rules[0]["adjustment_matrix"]
        assert static_matrix["2"]["5"] != blind_matrix["2"]["5"]
        assert static_matrix["2"]["5"] == 11.25
        assert blind_matrix["2"]["5"] == 12

    def test_fixture_metadata_marked_test_only(self):
        meta = _load_blind_fixture()["metadata"]
        assert meta["TEST_ONLY"] is True
        assert meta["NON_OFFICIAL"] is True
        assert meta["BLIND_CASE_RULE"] is True


# ---------------------------------------------------------------------------
# B1-B5 -- rule import / confirmation gate / Analyze
# ---------------------------------------------------------------------------

class TestBlindCaseRulePipeline:
    def test_b1_package_starts_non_confirmed(self, ddb_env):
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        pkg = _draft_blind_package("BLIND-B1-001", "PKG-B1-001")
        saved = repo.save_candidate(pkg)
        assert saved.status != CaseRulePackageStatus.CONFIRMED

    def test_b2_before_confirmation_cannot_affect_result(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import analyze

        case_no = "BLIND-B2-001"
        _seed_road_width_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_blind_package(case_no, "PKG-B2-001"))

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        # An unconfirmed package still uses the STATIC baseline
        # (rule_engine_factory.build_rule_engine_for_case only ever
        # switches resolution_status away from STATIC_LOCAL for a package
        # that has actually reached CONFIRMED) -- but the fact that an
        # unconfirmed package EXISTS for this case is still surfaced as an
        # explicit warning, never silently dropped.
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert body["rule_resolution_warnings"] == ["CASE_RULE_NOT_CONFIRMED"]
        g = _grade_of(body)
        assert "base=普通" in g["grade"], "unconfirmed Blind package must NOT change the base grade"

    def test_b3_b4_after_confirmation_analyze_uses_blind_rule_and_grade_differs(self, ddb_env, monkeypatch):
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard
        import analyze

        case_no = "BLIND-B34-001"
        _seed_road_width_case(monkeypatch, case_no)
        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_blind_package(case_no, "PKG-B34-001"))

        # Human Confirmation Gate exercised via the REAL handler, not
        # repo.confirm() directly (STEP5 §15).
        dto = json.loads(evaluation_standard.get_evaluation_standard_candidate(
            {"pathParameters": {"id": case_no, "package_id": "PKG-B34-001"}}, None,
        )["body"])
        assert dto["status"] == "EXTRACTED"

        confirm_resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": "PKG-B34-001"},
             "body": json.dumps({"confirmed_by": "blind-tester"})}, None,
        )
        assert confirm_resp["statusCode"] == 200, confirm_resp["body"]
        confirmed_dto = json.loads(confirm_resp["body"])
        assert confirmed_dto["status"] == "CONFIRMED"

        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        assert body["rule_package_id"] == "PKG-B34-001"
        g = _grade_of(body)
        # B4: 18m grade differs from static baseline (普通/grade3 -> 稍優/grade2)
        assert "base=稍優" in g["grade"], g["grade"]
        assert g["rule_source_type"] == "CASE_IMPORTED_CONFIRMED"

    def test_b5_adjustment_rate_differs_from_static(self, ddb_env, monkeypatch):
        """Full comparison: the SAME 18m/3m scenario run once against the
        static baseline (no case rule) and once against the CONFIRMED
        Blind rule -- the resulting adjustment_pct must differ, proving
        ADJUSTMENT_CHANGED_BY_NEW_STANDARD=YES, not just the grade label."""
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard
        import analyze

        static_case_no = "BLIND-B5-STATIC-001"
        _seed_road_width_case(monkeypatch, static_case_no)
        static_resp = analyze.analyze({"pathParameters": {"id": static_case_no}}, None)
        static_body = json.loads(static_resp["body"])
        static_adjustment = _adjustment_of(static_body)["adjustment_pct"]

        blind_case_no = "BLIND-B5-BLIND-001"
        _seed_road_width_case(monkeypatch, blind_case_no)
        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_blind_package(blind_case_no, "PKG-B5-001"))
        evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": blind_case_no, "package_id": "PKG-B5-001"},
             "body": json.dumps({"confirmed_by": "blind-tester"})}, None,
        )
        blind_resp = analyze.analyze({"pathParameters": {"id": blind_case_no}}, None)
        blind_body = json.loads(blind_resp["body"])
        blind_adjustment = _adjustment_of(blind_body)["adjustment_pct"]

        assert static_adjustment != blind_adjustment, (static_adjustment, blind_adjustment)
        # Known values from the fixture design (see module docstring):
        # static base=普通(3)/comp=劣(5) -> matrix[3][5]=7.5 ; blind
        # base=稍優(2)/comp=劣(5) -> matrix[2][5]=12.
        assert static_adjustment == "7.5"
        assert blind_adjustment == "12"


# ---------------------------------------------------------------------------
# B6-B8 -- CompleteForm / Review / Cross-form
# ---------------------------------------------------------------------------

class TestBlindCaseFormsAndReview:
    def _confirm_blind(self, case_no, package_id="PKG-B678-001"):
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard

        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_blind_package(case_no, package_id))
        resp = evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": case_no, "package_id": package_id},
             "body": json.dumps({"confirmed_by": "blind-tester"})}, None,
        )
        assert resp["statusCode"] == 200, resp["body"]

    def test_b6_complete_form_reflects_blind_rule(self, ddb_env, monkeypatch):
        import complete_form

        case_no = "BLIND-B6-001"
        _seed_full_case(monkeypatch, case_no)
        self._confirm_blind(case_no)

        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200, resp["body"]
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"

        field = next(f for f in body["fields"] if f["field_id"] == f"{ROAD_WIDTH_FIELD_ID}_adjustment_pct_comp1")
        assert "base=稍優" in field["grade"]
        assert field["rule_source_type"] == "CASE_IMPORTED_CONFIRMED"

        region_rate_field = next(f for f in body["fields"] if f["field_id"] == "region_adjustment_rate_comp1")
        assert region_rate_field["final_value"] == "12"

    def test_b7_review_understands_blind_rule(self, ddb_env, monkeypatch):
        import complete_form
        import review

        case_no = "BLIND-B7-001"
        _seed_full_case(monkeypatch, case_no)
        self._confirm_blind(case_no)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200, resp["body"]
        body = json.loads(resp["body"])

        grade_issue = next(
            i for i in body["issues"]
            if i["field"] == f"{ROAD_WIDTH_FIELD_ID}_adjustment_pct_comp1"
        )
        # Review re-derives the expected grade via the SAME CONFIRMED Blind
        # rule (build_rule_engine_for_case is case-scoped, not a fresh
        # static lookup), so this must PASS, not fabricate an error against
        # a rule it doesn't know about.
        assert grade_issue["issue_type"] == "Passed", grade_issue
        assert grade_issue["expected_value"] == "2"
        assert grade_issue["rule_source_type"] == "CASE_IMPORTED_CONFIRMED"

    def test_b8_cross_form_uses_blind_values(self, ddb_env, monkeypatch):
        import complete_form
        import review

        case_no = "BLIND-B8-001"
        _seed_full_case(monkeypatch, case_no)
        self._confirm_blind(case_no)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        cross_form_issue = next(
            i for i in body["issues"] if i["field"] == "region_adjustment_rate_comp1"
        )
        assert cross_form_issue["issue_type"] == "Passed"
        assert cross_form_issue["expected_value"] == "12"
        assert cross_form_issue["submitted_value"] == "12"


# ---------------------------------------------------------------------------
# B9/B10, G1-G3 -- Golden/Blind isolation, warm-runtime cross-case leak
# ---------------------------------------------------------------------------

class TestGoldenBlindIsolation:
    def test_g1_golden_static_baseline_18m_is_普通(self, ddb_env, monkeypatch):
        import analyze

        case_no = "GOLDEN-G1-001"
        _seed_road_width_case(monkeypatch, case_no)
        resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body["rule_resolution_status"] == "STATIC_LOCAL"
        assert "base=普通" in _grade_of(body)["grade"]

    def test_g2_g3_golden_blind_golden_sequence_no_cross_case_leak(self, ddb_env, monkeypatch):
        """STEP5 §16: Golden -> Blind -> Golden again, in the SAME warm
        process, verifying the Blind Case's CONFIRMED rule never leaks
        into the Golden case (G3) -- and that Golden itself was never
        touched by importing a dual-input Evaluation Standard for a
        DIFFERENT case (G2)."""
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard
        import analyze

        golden_case_no = "GOLDEN-G23-001"
        blind_case_no = "BLIND-G23-001"
        _seed_road_width_case(monkeypatch, golden_case_no)
        _seed_road_width_case(monkeypatch, blind_case_no)

        # First Golden read: 普通.
        first = json.loads(analyze.analyze({"pathParameters": {"id": golden_case_no}}, None)["body"])
        assert "base=普通" in _grade_of(first)["grade"]

        # Blind Case confirmed for a DIFFERENT case_id.
        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_blind_package(blind_case_no, "PKG-G23-001"))
        evaluation_standard.confirm_evaluation_standard(
            {"pathParameters": {"id": blind_case_no, "package_id": "PKG-G23-001"},
             "body": json.dumps({"confirmed_by": "blind-tester"})}, None,
        )
        blind_result = json.loads(analyze.analyze({"pathParameters": {"id": blind_case_no}}, None)["body"])
        assert "base=稍優" in _grade_of(blind_result)["grade"]

        # Second Golden read (G3): STILL 普通, STILL STATIC_LOCAL -- the
        # Blind Case's CONFIRMED package must not have leaked.
        second = json.loads(analyze.analyze({"pathParameters": {"id": golden_case_no}}, None)["body"])
        assert second["rule_resolution_status"] == "STATIC_LOCAL"
        assert second["rule_package_id"] is None
        assert "base=普通" in _grade_of(second)["grade"]

    def test_b10_blind_a_blind_b_blind_a_warm_runtime_no_leak(self, ddb_env, monkeypatch):
        """STEP5 §16's second sequence: Blind A -> Blind B -> Blind A,
        where A and B are two DIFFERENT cases each with their own
        CONFIRMED Blind package (built with distinct rule_id suffixes so
        they never collide in the moto table), all within the same warm
        process/module -- the module-level RuleEngine cache bug STEP2
        fixed (docs/audit/CASE_SCOPED_RULE_ARCHITECTURE_REPORT.md) is
        exactly the class of regression this would catch if reintroduced."""
        from case_rule_repository import DynamoCaseRuleRepository
        import evaluation_standard
        import analyze

        case_a, case_b = "BLIND-B10-A", "BLIND-B10-B"
        _seed_road_width_case(monkeypatch, case_a)
        _seed_road_width_case(monkeypatch, case_b)

        repo = DynamoCaseRuleRepository()
        repo.save_candidate(_draft_blind_package(case_a, "PKG-B10-A", suffix="A"))
        repo.save_candidate(_draft_blind_package(case_b, "PKG-B10-B", suffix="B"))
        for case_no, package_id in ((case_a, "PKG-B10-A"), (case_b, "PKG-B10-B")):
            resp = evaluation_standard.confirm_evaluation_standard(
                {"pathParameters": {"id": case_no, "package_id": package_id},
                 "body": json.dumps({"confirmed_by": "blind-tester"})}, None,
            )
            assert resp["statusCode"] == 200, resp["body"]

        def _check(case_no, expected_package_id):
            body = json.loads(analyze.analyze({"pathParameters": {"id": case_no}}, None)["body"])
            assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
            assert body["rule_package_id"] == expected_package_id
            assert "base=稍優" in _grade_of(body)["grade"]

        _check(case_a, "PKG-B10-A")
        _check(case_b, "PKG-B10-B")
        _check(case_a, "PKG-B10-A")
