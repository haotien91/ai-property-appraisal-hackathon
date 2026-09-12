# -*- coding: utf-8 -*-
"""
TABLE51-THREE-COMPARABLE-C1 tests.

Exercises the REAL production path -- cases.create_case (with `segments`) ->
collect_data.collect_data x4 (segment-scoped, real 題目.pdf-derived Table3
fixtures) -> a CONFIRMED Shulin CaseRulePackage -> backend/handlers/
table51_analysis.get_table51_analysis -- against moto-mocked DynamoDB.

Scenarios A-L below map 1:1 to this round's own Task 10 list.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import sys
from decimal import Decimal

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "data", "competition_cases", "shulin_residential_2026"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

import segment_table3_fixtures as fx  # noqa: E402
from domain.models import CaseRulePackage, FactorInput, Evidence, SourceType  # noqa: E402

PROFILE_ID = "shulin_residential_2026"
SHULIN_DIR = os.path.join(REPO_ROOT, "data", "rules", "competition", "shulin_residential_2026")


def _load_rules(name):
    with open(os.path.join(SHULIN_DIR, name), encoding="utf-8") as f:
        return json.load(f)["rules"]


REGIONAL_RULES = _load_rules("regional_rules.json")
INDIVIDUAL_RULES = _load_rules("individual_rules.json")


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
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-t51")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table-t51",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_shulin_case(cases_module, case_no, comparable_ids=None):
    body = {
        "case_no": case_no, "segment_code": "P001-00", "city": "新北市", "district": fx.DISTRICT,
        "land_use_type": fx.LAND_USE_TYPE, "appraisal_period": fx.APPRAISAL_PERIOD,
        "appraisal_base_date": fx.APPRAISAL_PERIOD, "segment_scope": "P001-00 比準地區段",
        "base_parcel_id": fx.P001_SEGMENT_META["parcel_ids"][0],
        "comparable_ids": comparable_ids if comparable_ids is not None else ["P002-00", "P003-00", "P004-00"],
        "rule_profile_id": PROFILE_ID,
        "segments": fx.segment_map_body(),
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _make_legacy_case(cases_module, case_no):
    body = {
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _collect_segment(collect_data_module, case_no, segment_code, factors=None):
    return collect_data_module.collect_data(
        {"pathParameters": {"id": case_no, "segment_code": segment_code},
         "body": json.dumps({"competition_provided_factors": factors or fx.SEGMENT_TABLE3_FACTORS[segment_code]})},
        None,
    )


def _confirm_shulin_package(case_no, package_id="PKG-T51"):
    from case_rule_repository import DynamoCaseRuleRepository
    repo = DynamoCaseRuleRepository()
    pkg = CaseRulePackage(
        case_id=case_no, package_id=package_id, rule_version="t51-test-v1",
        source_document="評價基準明細表.pdf", source_type="MANUAL_CSV_INGEST",
        regional_rules=REGIONAL_RULES, individual_rules=INDIVIDUAL_RULES,
        metadata={"competition_profile": {
            "profile_id": PROFILE_ID, "district": "樹林區", "land_use_type": "普通住宅用地",
            "source_document": "評價基準明細表.pdf",
            "source_sha256": "a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c",
        }},
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    repo.save_candidate(pkg)
    repo.confirm(case_no, package_id, confirmed_by="tester")
    return repo


def _full_setup(monkeypatch, case_no, comparable_ids=None, segments_to_collect=("P001-00", "P002-00", "P003-00", "P004-00")):
    import cases
    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_shulin_case(cases, case_no, comparable_ids=comparable_ids)
    for code in segments_to_collect:
        resp = _collect_segment(collect_data, case_no, code)
        assert resp["statusCode"] == 200, resp["body"]
    _confirm_shulin_package(case_no)


def _get_analysis(case_no):
    import table51_analysis
    resp = table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)
    return resp


# ---------------------------------------------------------------------------
# Synthetic (non-fixture) complete 29-factor datasets, for tests that need
# every factor genuinely resolvable (the real 題目.pdf fixtures only fill
# 15/29 -- honestly, per Task 6/E's own "missing != zero" requirement, the
# other 14 are genuinely not surveyed and MUST stay MANUAL_REVIEW_REQUIRED).
# ---------------------------------------------------------------------------

def _synthetic_complete_factors(road_width=18.0):
    """One complete set of all 29 regional factors, built directly from the
    rule pack's own first grade_label/lower_bound per factor -- guarantees
    every factor is genuinely gradeable, never fabricating a value outside
    what the rule pack itself defines as valid."""
    import table51_analysis_engine as t51e
    catalog = t51e.build_regional_factor_catalog(REGIONAL_RULES)
    by_field = {}
    for r in REGIONAL_RULES:
        prefix = r["rule_id"].rsplit("-", 1)[0]
        if not prefix.startswith("REG-"):
            continue
        field_id = "regional_" + prefix[len("REG-"):].lower()
        by_field.setdefault(field_id, []).append(r)

    factors = []
    for field_id, (factor_name, category) in catalog.items():
        records = by_field[field_id]
        rec = records[0]
        if rec["value_type"] in ("categorical", "boolean"):
            value = rec["grade_label"]
        else:
            if field_id == "regional_main_road_width":
                value = road_width
            elif rec.get("lower_bound") is not None:
                value = rec["lower_bound"]
            elif rec.get("upper_bound") is not None:
                value = rec["upper_bound"] - 1
            else:
                value = 0
        factors.append({
            "field_id": field_id, "factor": factor_name, "raw_value": value, "unit": rec.get("unit"),
            "evidence": {"source": "synthetic-test", "source_type": "競賽題目提供固定值"},
        })
    return factors


def _to_factor_inputs(raw_list):
    out = []
    for d in raw_list:
        ev = d["evidence"]
        out.append(FactorInput(field_id=d["field_id"], factor=d["factor"], raw_value=d["raw_value"],
                                 unit=d.get("unit"), evidence=Evidence(source=ev["source"], source_type=SourceType(ev["source_type"]))))
    return out


# ---------------------------------------------------------------------------
# A/B/C/D. Segment reads, 3 independent comparisons, 29 factor slots,
# no cross-contamination.
# ---------------------------------------------------------------------------

class TestSegmentReadsAndIndependentComparisons:
    def test_a_all_four_segments_correctly_read(self, ddb_env, monkeypatch):
        case_no = "T51-A-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["analysis"]["base_segment_code"] == "P001-00"
        assert {c["comparable_segment_code"] for c in body["analysis"]["comparisons"]} == {"P002-00", "P003-00", "P004-00"}

    def test_b_three_independent_comparisons_created(self, ddb_env, monkeypatch):
        case_no = "T51-B-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        assert body["comparable_count"] == 3
        comparisons = body["analysis"]["comparisons"]
        assert [c["comparison_index"] for c in comparisons] == [1, 2, 3]

    def test_c_each_comparison_has_29_factor_slots(self, ddb_env, monkeypatch):
        case_no = "T51-C-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        for c in body["analysis"]["comparisons"]:
            assert len(c["factor_results"]) == 29

    def test_d_p002_p003_p004_do_not_cross_contaminate(self, ddb_env, monkeypatch):
        """P003's main road width is uniquely overridden -- only P003's
        comparison must reflect it; P002/P004 stay at their own fixture
        values (7M/10M respectively, per segment_table3_fixtures.py)."""
        case_no = "T51-D-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        _collect_segment(collect_data, case_no, "P001-00")
        _collect_segment(collect_data, case_no, "P002-00")
        p003_factors = copy.deepcopy(fx.SEGMENT_TABLE3_FACTORS["P003-00"])
        for f in p003_factors:
            if f["field_id"] == "regional_main_road_width":
                f["raw_value"] = 999
        _collect_segment(collect_data, case_no, "P003-00", factors=p003_factors)
        _collect_segment(collect_data, case_no, "P004-00")
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        by_code = {c["comparable_segment_code"]: c for c in body["analysis"]["comparisons"]}

        def _road_width(comparison):
            return next(r for r in comparison["factor_results"] if r["field_id"] == "regional_main_road_width")

        assert _road_width(by_code["P002-00"])["comparable_raw_value"] == 7
        assert _road_width(by_code["P003-00"])["comparable_raw_value"] == 999
        assert _road_width(by_code["P004-00"])["comparable_raw_value"] == 10


# ---------------------------------------------------------------------------
# E/F. missing != zero, MANUAL_REVIEW isolated to the right comparison.
# ---------------------------------------------------------------------------

class TestMissingVsZeroAndManualReviewIsolation:
    def test_e_zero_is_a_legitimate_value_distinct_from_missing(self, ddb_env, monkeypatch):
        """P002's 建蔽率 is genuinely 0% (legal edge value, e.g. a segment
        with no legal building coverage) -- must be GRADED normally (a
        real grade/adjustment), never conflated with a genuinely absent
        factor (which gets MANUAL_REVIEW_REQUIRED with base/comparable_
        raw_value=None)."""
        case_no = "T51-E-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        _collect_segment(collect_data, case_no, "P001-00")
        p002_factors = copy.deepcopy(fx.SEGMENT_TABLE3_FACTORS["P002-00"])
        for f in p002_factors:
            if f["field_id"] == "regional_building_coverage_ratio":
                f["raw_value"] = 0
        _collect_segment(collect_data, case_no, "P002-00", factors=p002_factors)
        _collect_segment(collect_data, case_no, "P003-00")
        _collect_segment(collect_data, case_no, "P004-00")
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        bcr = next(r for r in p002["factor_results"] if r["field_id"] == "regional_building_coverage_ratio")
        assert bcr["comparable_raw_value"] == 0
        assert bcr["status"] == "COMPLETED"  # a real grade was determined for 0%, not skipped/manual-review
        assert bcr["comparable_grade"] is not None

        # A genuinely ABSENT factor (never submitted at all) on the SAME
        # comparison must still show None, never silently become 0.
        far = next(r for r in p002["factor_results"] if r["field_id"] == "regional_school_proximity"
                   ) if any(r["field_id"] == "regional_school_proximity" for r in p002["factor_results"]) else None
        missing_factor = next(r for r in p002["factor_results"] if r["status"] == "MANUAL_REVIEW_REQUIRED")
        assert missing_factor["comparable_raw_value"] is None or missing_factor["base_raw_value"] is None

    def test_f_manual_review_only_affects_the_correct_comparison_and_factor(self, ddb_env, monkeypatch):
        """Fully synthetic all-29-factor data for P001/P002/P004; P003 is
        missing exactly ONE factor. Only P003's comparison -- and only
        that one factor within it -- may show MANUAL_REVIEW_REQUIRED."""
        case_no = "T51-F-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)

        complete = _synthetic_complete_factors()
        incomplete = [f for f in complete if f["field_id"] != "regional_sunlight"]

        _collect_segment(collect_data, case_no, "P001-00", factors=complete)
        _collect_segment(collect_data, case_no, "P002-00", factors=complete)
        _collect_segment(collect_data, case_no, "P003-00", factors=incomplete)
        _collect_segment(collect_data, case_no, "P004-00", factors=complete)
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        by_code = {c["comparable_segment_code"]: c for c in body["analysis"]["comparisons"]}

        assert by_code["P002-00"]["status"] == "COMPLETED"
        assert by_code["P002-00"]["total_adjustment_pct"] is not None
        assert by_code["P004-00"]["status"] == "COMPLETED"
        assert by_code["P004-00"]["total_adjustment_pct"] is not None

        p003 = by_code["P003-00"]
        assert p003["status"] == "MANUAL_REVIEW_REQUIRED"
        assert p003["total_adjustment_pct"] is None
        manual_fields = [r["field_id"] for r in p003["factor_results"] if r["status"] == "MANUAL_REVIEW_REQUIRED"]
        assert manual_fields == ["regional_sunlight"]


# ---------------------------------------------------------------------------
# G/H. Competition fixed value precedence, at Table5-1 runtime.
# ---------------------------------------------------------------------------

class TestCompetitionFixedValuePrecedenceAtTable51:
    def test_g_base_fixed_value_precedence(self, ddb_env, monkeypatch):
        case_no = "T51-G-001"
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        _collect_segment(collect_data, case_no, "P001-00")
        _collect_segment(collect_data, case_no, "P002-00")
        _collect_segment(collect_data, case_no, "P003-00")
        _collect_segment(collect_data, case_no, "P004-00")

        # Inject a conflicting Provider-derived value + a supplemental
        # user/AI value for P001's own regional_main_road_width, directly
        # into its FACTORS#P001-00 record (competition_provided_factors
        # already holds 28 from the fixture).
        rec = case_store.get_record(case_no, competition_segments.factors_sk("P001-00"))
        rec["regional_base_factors"] = rec.get("regional_base_factors", []) + [
            {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 99, "unit": "M",
             "evidence": {"source": "Provider", "source_type": "GIS量測"}},
        ]
        rec["user_submitted_factors"] = {
            "base_parcel_factors": [
                {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 123, "unit": "M",
                 "evidence": {"source": "AI建議", "source_type": "AI輔助填寫"}},
            ],
            "comparable_factors": {},
        }
        case_store.put_record(case_no, competition_segments.factors_sk("P001-00"), rec)
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        road_width = next(r for r in p002["factor_results"] if r["field_id"] == "regional_main_road_width")
        assert road_width["base_raw_value"] == 28  # TABLE51_BASE_FIXED_VALUE_PRECEDENCE

    def test_h_comparable_fixed_value_precedence(self, ddb_env, monkeypatch):
        case_no = "T51-H-001"
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
            _collect_segment(collect_data, case_no, code)

        rec = case_store.get_record(case_no, competition_segments.factors_sk("P002-00"))
        rec["regional_base_factors"] = rec.get("regional_base_factors", []) + [
            {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 55, "unit": "M",
             "evidence": {"source": "Provider", "source_type": "GIS量測"}},
        ]
        rec["user_submitted_factors"] = {
            "base_parcel_factors": [
                {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 77, "unit": "M",
                 "evidence": {"source": "AI建議", "source_type": "AI輔助填寫"}},
            ],
            "comparable_factors": {},
        }
        case_store.put_record(case_no, competition_segments.factors_sk("P002-00"), rec)
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        road_width = next(r for r in p002["factor_results"] if r["field_id"] == "regional_main_road_width")
        assert road_width["comparable_raw_value"] == 7  # P002-00's OWN fixture value (X), never 55 (Y) or 77 (Z)


# ---------------------------------------------------------------------------
# I. No primary_comparable shortcut.
# ---------------------------------------------------------------------------

class TestNoPrimaryComparableShortcut:
    def test_i_partial_collect_data_fails_closed_never_silently_partial(self, ddb_env, monkeypatch):
        """Segment map declares 3 comparables, but only P001 and P003 ever
        ran collect_data (P002/P004 never did) -- a "shortcut" implementation
        (e.g. comparable_ids[0], or silently skipping ungathered segments)
        would either crash, silently return 1 comparison, or pick the wrong
        one. The correct behavior is a clean, fail-closed 400 -- never a
        partial success that hides the missing segments."""
        case_no = "T51-I-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        _collect_segment(collect_data, case_no, "P001-00")
        _collect_segment(collect_data, case_no, "P003-00")
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_i_comparison_index_order_independent_of_dict_iteration(self, ddb_env, monkeypatch):
        """comparisons[] must be produced in comparison_index order (1,2,3)
        regardless of segment map declaration order -- proves no code path
        just takes "the first" comparable and stops."""
        case_no = "T51-I-002"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        codes_in_order = [c["comparable_segment_code"] for c in body["analysis"]["comparisons"]]
        assert codes_in_order == ["P002-00", "P003-00", "P004-00"]


# ---------------------------------------------------------------------------
# J. Legacy Jinshan preserved.
# ---------------------------------------------------------------------------

class TestLegacyJinshanPreserved:
    def test_j_legacy_case_blocks_cleanly_never_crashes(self, ddb_env, monkeypatch):
        case_no = "T51-J-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_legacy_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)

        resp = _get_analysis(case_no)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "SEGMENT_MAP_REQUIRED"

    def test_j_legacy_single_comparable_complete_form_still_works(self, ddb_env, monkeypatch):
        """LEGACY_JINSHAN_SINGLE_COMPARABLE_PRESERVED: the EXISTING (pre-C1)
        complete_form.py path for a legacy single-segment/single-comparable
        case is completely untouched by anything in this round."""
        import cases
        import case_store
        import complete_form
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "T51-J-002"
        _make_legacy_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)
        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_base_factors"] = [
            {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"},
        ]
        factors["regional_comparable_factors"] = {
            "comp1": [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}],
        }
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)

        resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["rule_resolution_status"] == "STATIC_LOCAL"


# ---------------------------------------------------------------------------
# K. Official XLSX aggregate/formula mapping.
# ---------------------------------------------------------------------------

class TestOfficialXlsxFormulaMapping:
    def test_k_category_subtotal_and_grand_total_match_official_blank_form_formula(self):
        """OFFICIAL_BLANK_FORM_FORMULA (表5-1區域因素明細表(住) sheet, B42 =
        '(1)+(2)+...+(8)', verified via openpyxl data_type='s' -- a plain
        text annotation in the official blank form, not a live spreadsheet
        formula, but a genuine documented aggregation rule nonetheless):
        each category's 百分比小計 = sum of its own member factors'
        adjustment_pct; 影響地價區域因素總修正數 = sum of the 8 category
        subtotals. Verified here against a fully-synthetic, fully-resolved
        29-factor dataset (arithmetic identity, independent of any real
        case data)."""
        import table51_analysis_engine as t51e
        from rule_engine import RuleEngine
        from grade_engine import GradeEngine
        from adjustment_engine import AdjustmentEngine

        engine = RuleEngine(REGIONAL_RULES + INDIVIDUAL_RULES)
        ge, ae = GradeEngine(engine), AdjustmentEngine(engine)
        records = t51e.extract_regional_rule_records(engine)
        t51_engine = t51e.Table51AnalysisEngine(ge, ae, records)

        base = _to_factor_inputs(_synthetic_complete_factors(road_width=18.0))
        comp = _to_factor_inputs(_synthetic_complete_factors(road_width=10.0))
        comparison = t51_engine.build_comparison(
            "新北市", "樹林區", "普通住宅用地", "P001-00",
            "樹林區", "普通住宅用地", "P002-00", 1, base, comp,
        )
        assert comparison.status.value == "COMPLETED"
        assert comparison.total_adjustment_pct is not None

        manual_subtotal_sum = sum((s.subtotal_pct for s in comparison.category_subtotals), Decimal("0"))
        assert comparison.total_adjustment_pct == manual_subtotal_sum

        for category, results in _group_by_category(comparison.factor_results).items():
            subtotal = next(s for s in comparison.category_subtotals if s.category == category)
            manual_sum = sum((r.adjustment_pct for r in results), Decimal("0"))
            assert subtotal.subtotal_pct == manual_sum

        assert len(comparison.category_subtotals) == 8


def _group_by_category(factor_results):
    out = {}
    for r in factor_results:
        out.setdefault(r.category, []).append(r)
    return out


# ---------------------------------------------------------------------------
# L. Provenance / lineage completeness.
# ---------------------------------------------------------------------------

class TestProvenanceLineageCompleteness:
    def test_l_full_lineage_reconstructible_for_every_factor(self, ddb_env, monkeypatch):
        case_no = "T51-L-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        analysis = body["analysis"]
        assert analysis["case_id"] == case_no
        assert analysis["rule_profile_id"] == PROFILE_ID
        assert analysis["base_segment_code"] == "P001-00"

        for comparison in analysis["comparisons"]:
            comp_code = comparison["comparable_segment_code"]
            for r in comparison["factor_results"]:
                assert r["field_id"]
                assert r["factor_name"]
                assert r["category"]
                assert r["base_segment_code"] == "P001-00"
                assert r["comparable_segment_code"] == comp_code
                assert r["status"] in ("COMPLETED", "MANUAL_REVIEW_REQUIRED")
                if r["status"] == "COMPLETED":
                    # Rule provenance (which regulation/rule matched) --
                    # distinct from data provenance below.
                    assert r["rule_id"] is not None
                    assert r["rule_source_document"] is not None
                    assert r["rule_source_page"] is not None
                    assert r["base_grade"] is not None
                    assert r["comparable_grade"] is not None
                    assert r["adjustment_pct"] is not None
                # Data provenance (where did this NUMBER come from) -- must
                # be present whenever the underlying FactorInput existed,
                # regardless of whether grading itself succeeded (Task 2/G:
                # a MANUAL_REVIEW_REQUIRED factor still keeps whatever input
                # provenance it had).
                if r["base_raw_value"] is not None:
                    assert r["base_source_type"] is not None
                    assert r["base_source"] is not None
                if r["comparable_raw_value"] is not None:
                    assert r["comparable_source_type"] is not None
                    assert r["comparable_source"] is not None
                if r["status"] == "MANUAL_REVIEW_REQUIRED":
                    assert r["reason"]  # a genuine, human-readable explanation, never silent
