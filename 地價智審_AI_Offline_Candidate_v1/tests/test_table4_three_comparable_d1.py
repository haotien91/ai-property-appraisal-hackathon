# -*- coding: utf-8 -*-
"""
TABLE4-THREE-COMPARABLE-D1 tests.

Exercises the REAL production path -- cases.create_case (with `segments`) ->
collect_data.collect_data x4 (segment-scoped, real 題目.pdf-derived Table3/
Table4 fixtures) -> a CONFIRMED Shulin CaseRulePackage -> backend/handlers/
table4_analysis.get_table4_analysis (which itself calls table51_analysis.py's
build_table51_analysis_for_case() for the Table5-1 bridge) -- against
moto-mocked DynamoDB.

Numbered scenarios below map 1:1 to this round's own Task 15 list (1-20).
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
import segment_table4_fixtures as fx4  # noqa: E402
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
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-t4d1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table-t4d1",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_shulin_case(cases_module, case_no):
    body = {
        "case_no": case_no, "segment_code": "P001-00", "city": "新北市", "district": fx.DISTRICT,
        "land_use_type": fx.LAND_USE_TYPE, "appraisal_period": fx.APPRAISAL_PERIOD,
        "appraisal_base_date": fx.APPRAISAL_PERIOD, "segment_scope": "P001-00 比準地區段",
        "base_parcel_id": fx.P001_SEGMENT_META["parcel_ids"][0],
        "comparable_ids": ["P002-00", "P003-00", "P004-00"],
        "rule_profile_id": PROFILE_ID, "segments": fx.segment_map_body(),
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _make_legacy_case(cases_module, case_no):
    body = {
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _collect_segment(collect_data_module, case_no, segment_code, table3_factors=None, individual_factors=None):
    cd_body = {"competition_provided_factors": table3_factors or fx.SEGMENT_TABLE3_FACTORS[segment_code]}
    if individual_factors is not None:
        cd_body["competition_provided_individual_factors"] = individual_factors
    if segment_code in fx4.SEGMENT_TABLE4_TRANSACTION:
        cd_body["competition_provided_transaction"] = fx4.SEGMENT_TABLE4_TRANSACTION[segment_code]
    return collect_data_module.collect_data(
        {"pathParameters": {"id": case_no, "segment_code": segment_code}, "body": json.dumps(cd_body)}, None,
    )


def _confirm_shulin_package(case_no, package_id="PKG-T4D1"):
    from case_rule_repository import DynamoCaseRuleRepository
    repo = DynamoCaseRuleRepository()
    pkg = CaseRulePackage(
        case_id=case_no, package_id=package_id, rule_version="t4d1-test-v1",
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


def _full_setup(monkeypatch, case_no, individual_factors_by_segment=None):
    import cases
    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_shulin_case(cases, case_no)
    individual_factors_by_segment = individual_factors_by_segment or {}
    for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
        resp = _collect_segment(collect_data, case_no, code, individual_factors=individual_factors_by_segment.get(code))
        assert resp["statusCode"] == 200, resp["body"]
    _confirm_shulin_package(case_no)


def _get_table4(case_no):
    import table4_analysis
    return table4_analysis.get_table4_analysis({"pathParameters": {"id": case_no}}, None)


def _get_table51(case_no):
    import table51_analysis
    return table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)


# ---------------------------------------------------------------------------
# Synthetic complete 20-slot individual-factor datasets (real 題目.pdf data
# is genuinely blank for all individual factors -- see docs/phase7/
# table4_three_comparable_d1.md Task 1/2 audit).
# ---------------------------------------------------------------------------

def _synthetic_complete_individual_factors():
    import individual_factor_catalog as cat
    by_field = {}
    for r in INDIVIDUAL_RULES:
        rule_id = r.get("rule_id", "")
        if not rule_id.startswith("IND-"):
            continue
        prefix = rule_id.rsplit("-", 1)[0]
        field_id = cat.INDIVIDUAL_FIELD_ID_MAP.get(prefix)
        if field_id:
            by_field.setdefault(field_id, []).append(r)

    factors = []
    for field_id, records in by_field.items():
        rec = records[0]
        if rec["value_type"] in ("categorical", "boolean"):
            value = rec["grade_label"]
        elif rec.get("lower_bound") is not None:
            value = rec["lower_bound"]
        elif rec.get("upper_bound") is not None:
            value = rec["upper_bound"] - 1
        else:
            value = 0
        factors.append({
            "field_id": field_id, "factor": rec["factor"], "raw_value": value, "unit": rec.get("unit"),
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
# 1/15/16. Three independent Table4 comparisons, no shortcut, no averaging.
# ---------------------------------------------------------------------------

class TestThreeIndependentComparisons:
    def test_1_three_independent_comparisons_created(self, ddb_env, monkeypatch):
        case_no = "T4D1-1-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_table4(case_no)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["comparable_count"] == 3
        comparisons = body["analysis"]["comparisons"]
        assert [c["comparison_index"] for c in comparisons] == [1, 2, 3]
        assert [c["comparable_segment_code"] for c in comparisons] == ["P002-00", "P003-00", "P004-00"]

    def test_15_no_primary_comparable_shortcut(self, ddb_env, monkeypatch):
        """Only P001 and P003 ever ran collect_data (P002/P004 never did)
        -- a comparable_ids[0]/shortcut implementation would either crash,
        silently return 1 comparison, or pick the wrong one. Correct
        behavior: clean fail-closed 400, never a partial success."""
        case_no = "T4D1-15-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        _collect_segment(collect_data, case_no, "P001-00")
        _collect_segment(collect_data, case_no, "P003-00")
        _confirm_shulin_package(case_no)

        resp = _get_table4(case_no)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "VALIDATION_ERROR"

    def test_16_no_early_averaging_source_scan(self):
        """Static source scan: neither the engine nor the handler contains
        an obvious averaging/shortcut pattern over the 3 comparables AS
        ACTUAL CODE. Only non-comment, non-docstring lines are scanned --
        this module's own docstrings legitimately MENTION these forbidden
        patterns (in prose, explaining that a shortcut must never appear),
        which a bare substring check would false-positive on."""
        import ast

        def _code_lines_only(path):
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src)
            docstring_lines = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc_node = node.body[0] if node.body else None
                    if (isinstance(doc_node, ast.Expr) and isinstance(getattr(doc_node, "value", None), ast.Constant)
                            and isinstance(doc_node.value.value, str)):
                        docstring_lines.update(range(doc_node.lineno, doc_node.end_lineno + 1))
            lines = src.splitlines()
            return "\n".join(
                line for i, line in enumerate(lines, start=1)
                if i not in docstring_lines and not line.strip().startswith("#")
            )

        engine_code = _code_lines_only(os.path.join(REPO_ROOT, "engine", "table4_analysis_engine.py"))
        handler_code = _code_lines_only(os.path.join(REPO_ROOT, "backend", "handlers", "table4_analysis.py"))
        for src in (engine_code, handler_code):
            assert "average(" not in src
            assert "comparable_ids[0]" not in src
            assert "comparables[0]" not in src
            assert 'row["comparables"][0]' not in src


# ---------------------------------------------------------------------------
# 2/3/4/5. Exact Table5-1 bridge per comparable, no cross-contamination.
# ---------------------------------------------------------------------------

class TestTable51Bridge:
    def test_2_3_4_exact_table51_bridge_per_comparable(self, ddb_env, monkeypatch):
        case_no = "T4D1-234-001"
        _full_setup(monkeypatch, case_no)

        t51_resp = _get_table51(case_no)
        assert t51_resp["statusCode"] == 200
        t51_body = json.loads(t51_resp["body"])
        t51_by_code = {c["comparable_segment_code"]: c for c in t51_body["analysis"]["comparisons"]}

        t4_resp = _get_table4(case_no)
        assert t4_resp["statusCode"] == 200
        t4_body = json.loads(t4_resp["body"])
        t4_by_code = {c["comparable_segment_code"]: c for c in t4_body["analysis"]["comparisons"]}

        for code in ("P002-00", "P003-00", "P004-00"):
            assert t4_by_code[code]["regional_adjustment_pct"] == t51_by_code[code]["total_adjustment_pct"]
            assert t4_by_code[code]["regional_adjustment_requires_manual_review"] == t51_by_code[code]["requires_manual_review"]
            assert t4_by_code[code]["regional_adjustment_source_comparison_index"] == t51_by_code[code]["comparison_index"]

    def test_5_no_table51_table4_cross_contamination(self):
        """Direct engine-level proof: build_comparison() REFUSES a
        Table51Comparison for the wrong comparable_segment_code."""
        import table4_analysis_engine as t4e
        from rule_engine import RuleEngine
        from grade_engine import GradeEngine
        from adjustment_engine import AdjustmentEngine
        from calculation_engine import CalculationEngine
        from domain.models import Table51Comparison, FieldStatus

        engine = RuleEngine(REGIONAL_RULES + INDIVIDUAL_RULES)
        t4 = t4e.Table4AnalysisEngine(GradeEngine(engine), AdjustmentEngine(engine), CalculationEngine(), INDIVIDUAL_RULES)
        wrong_regional = Table51Comparison(
            comparable_segment_code="P003-00", comparison_index=2,
            factor_results=[], category_subtotals=[], total_adjustment_pct=Decimal("1"),
            status=FieldStatus.COMPLETED, requires_manual_review=False,
        )
        with pytest.raises(ValueError):
            t4.build_comparison(
                "新北市", "樹林區", "普通住宅用地", "P001-00", "樹林區", "普通住宅用地", "P002-00", 1,
                [], [], {}, wrong_regional,
            )


# ---------------------------------------------------------------------------
# 6/7/8. Competition fixed-value precedence for individual factors.
# ---------------------------------------------------------------------------

class TestCompetitionFixedValuePrecedence:
    def _run_precedence_case(self, ddb_env, monkeypatch, case_no, target_segment):
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
            _collect_segment(collect_data, case_no, code)

        rec = case_store.get_record(case_no, competition_segments.factors_sk(target_segment))
        user_factors = rec.get("user_submitted_factors") or {"base_parcel_factors": [], "comparable_factors": {}}
        key = "base_parcel_factors" if target_segment == "P001-00" else "comparable_factors"
        provider_entry = {"field_id": "individual_land_depth", "factor": "深度", "raw_value": 99, "unit": "M",
                           "evidence": {"source": "Provider", "source_type": "GIS量測"}}
        supplemental_entry = {"field_id": "individual_land_depth", "factor": "深度", "raw_value": 123, "unit": "M",
                               "evidence": {"source": "AI建議", "source_type": "AI輔助填寫"}}
        if key == "base_parcel_factors":
            user_factors["base_parcel_factors"] = [provider_entry, supplemental_entry]
        else:
            user_factors.setdefault("comparable_factors", {})[target_segment] = [provider_entry, supplemental_entry]
        rec["user_submitted_factors"] = user_factors
        rec["competition_provided_individual_factors"] = [
            {"field_id": "individual_land_depth", "factor": "深度", "raw_value": 28, "unit": "M",
             "evidence": {"source": "題目.pdf", "source_type": "競賽題目提供固定值"}},
        ]
        case_store.put_record(case_no, competition_segments.factors_sk(target_segment), rec)
        _confirm_shulin_package(case_no)

        resp = _get_table4(case_no)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        return body

    def test_6_fixed_value_precedence_p002(self, ddb_env, monkeypatch):
        body = self._run_precedence_case(ddb_env, monkeypatch, "T4D1-6-001", "P002-00")
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        depth = next(r for r in p002["individual_factor_results"] if r["field_id"] == "individual_land_depth")
        assert depth["comparable_raw_value"] == 28

    def test_7_fixed_value_precedence_p003(self, ddb_env, monkeypatch):
        body = self._run_precedence_case(ddb_env, monkeypatch, "T4D1-7-001", "P003-00")
        p003 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P003-00")
        depth = next(r for r in p003["individual_factor_results"] if r["field_id"] == "individual_land_depth")
        assert depth["comparable_raw_value"] == 28

    def test_8_fixed_value_precedence_p004(self, ddb_env, monkeypatch):
        body = self._run_precedence_case(ddb_env, monkeypatch, "T4D1-8-001", "P004-00")
        p004 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P004-00")
        depth = next(r for r in p004["individual_factor_results"] if r["field_id"] == "individual_land_depth")
        assert depth["comparable_raw_value"] == 28

    def test_base_fixed_value_precedence(self, ddb_env, monkeypatch):
        """base(P001)-side precedence, mirroring the comparable-side tests."""
        body = self._run_precedence_case(ddb_env, monkeypatch, "T4D1-BASE-001", "P001-00")
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        depth = next(r for r in p002["individual_factor_results"] if r["field_id"] == "individual_land_depth")
        assert depth["base_raw_value"] == 28


# ---------------------------------------------------------------------------
# 9/10/11. 20 individual factor slots (19 standard + FAR special), FAR
# fail-closed.
# ---------------------------------------------------------------------------

class TestIndividualFactorSlotsAndFar:
    def test_9_10_twenty_slots_nineteen_standard_one_far(self, ddb_env, monkeypatch):
        synthetic = _synthetic_complete_individual_factors()
        case_no = "T4D1-910-001"
        _full_setup(monkeypatch, case_no, individual_factors_by_segment={
            "P001-00": synthetic, "P002-00": synthetic, "P003-00": synthetic, "P004-00": synthetic,
        })
        resp = _get_table4(case_no)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        results = p002["individual_factor_results"]
        assert len(results) == 20
        far_rows = [r for r in results if r["is_far_special_policy"]]
        assert len(far_rows) == 1
        assert far_rows[0]["field_id"] == "individual_floor_area_ratio"
        standard_rows = [r for r in results if not r["is_far_special_policy"]]
        assert len(standard_rows) == 19
        # All 19 standard factors resolved with the synthetic complete data.
        assert all(r["status"] == "COMPLETED" for r in standard_rows)

    def test_11_far_never_standard_matrix_never_fake_zero(self, ddb_env, monkeypatch):
        synthetic = _synthetic_complete_individual_factors()
        case_no = "T4D1-11-001"
        _full_setup(monkeypatch, case_no, individual_factors_by_segment={
            "P001-00": synthetic, "P002-00": synthetic, "P003-00": synthetic, "P004-00": synthetic,
        })
        resp = _get_table4(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        far = next(r for r in p002["individual_factor_results"] if r["field_id"] == "individual_floor_area_ratio")

        assert far["status"] == "MANUAL_REVIEW_REQUIRED"  # TABLE4_INDIVIDUAL_FAR_FAIL_CLOSED
        assert far["adjustment_pct"] is None  # never fabricated
        assert far["base_grade"] is None and far["comparable_grade"] is None
        assert "regional" not in (far["reason"] or "").lower()  # never silently used regional FAR matrix
        assert far["reason"]  # genuine explanation, not silent

        # And because FAR always requires manual review for this rule
        # profile, the WHOLE comparison must also fail closed -- never
        # silently compute a partial total that excludes it.
        assert p002["individual_adjustment_total_pct"] is None
        assert p002["status"] == "MANUAL_REVIEW_REQUIRED"

    def test_far_static_source_scan_no_fabrication(self):
        # The literal field_id lives in individual_factor_catalog.py (the
        # single source of truth for it, per FAR_FIELD_ID); table4_
        # analysis_engine.py references it via that imported constant,
        # never re-declaring the string itself.
        catalog_src = open(os.path.join(REPO_ROOT, "engine", "individual_factor_catalog.py"), encoding="utf-8").read()
        assert "individual_floor_area_ratio" in catalog_src
        engine_src = open(os.path.join(REPO_ROOT, "engine", "table4_analysis_engine.py"), encoding="utf-8").read()
        assert "FAR_FIELD_ID" in engine_src
        # No standard adjustment-matrix construction anywhere near FAR handling.
        assert "far_matrix" not in engine_src.lower()


# ---------------------------------------------------------------------------
# 12/13. missing != zero, manual review isolated per comparable.
# ---------------------------------------------------------------------------

class TestMissingVsZeroAndIsolation:
    def test_12_a_legitimate_zero_is_real(self, ddb_env, monkeypatch):
        synthetic = _synthetic_complete_individual_factors()
        p002_synth = copy.deepcopy(synthetic)
        for f in p002_synth:
            if f["field_id"] == "individual_building_coverage_ratio":
                f["raw_value"] = 0
        case_no = "T4D1-12-001"
        _full_setup(monkeypatch, case_no, individual_factors_by_segment={
            "P001-00": synthetic, "P002-00": p002_synth, "P003-00": synthetic, "P004-00": synthetic,
        })
        resp = _get_table4(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        bcr = next(r for r in p002["individual_factor_results"] if r["field_id"] == "individual_building_coverage_ratio")
        assert bcr["comparable_raw_value"] == 0
        assert bcr["status"] == "COMPLETED"  # a real grade for 0%, not manual review

    def test_12_b_missing_is_none_not_zero(self, ddb_env, monkeypatch):
        synthetic = _synthetic_complete_individual_factors()
        p002_missing = [f for f in synthetic if f["field_id"] != "individual_land_area"]
        case_no = "T4D1-12B-001"
        _full_setup(monkeypatch, case_no, individual_factors_by_segment={
            "P001-00": synthetic, "P002-00": p002_missing, "P003-00": synthetic, "P004-00": synthetic,
        })
        resp = _get_table4(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        area = next(r for r in p002["individual_factor_results"] if r["field_id"] == "individual_land_area")
        assert area["comparable_raw_value"] is None
        assert area["status"] == "MANUAL_REVIEW_REQUIRED"

    def test_13_c_d_manual_review_isolated_to_correct_comparable(self, ddb_env, monkeypatch):
        synthetic = _synthetic_complete_individual_factors()
        p003_missing = [f for f in synthetic if f["field_id"] != "individual_land_width"]
        case_no = "T4D1-13-001"
        _full_setup(monkeypatch, case_no, individual_factors_by_segment={
            "P001-00": synthetic, "P002-00": synthetic, "P003-00": p003_missing, "P004-00": synthetic,
        })
        resp = _get_table4(case_no)
        body = json.loads(resp["body"])
        by_code = {c["comparable_segment_code"]: c for c in body["analysis"]["comparisons"]}

        p003_width = next(r for r in by_code["P003-00"]["individual_factor_results"] if r["field_id"] == "individual_land_width")
        assert p003_width["status"] == "MANUAL_REVIEW_REQUIRED"

        for code in ("P002-00", "P004-00"):
            width = next(r for r in by_code[code]["individual_factor_results"] if r["field_id"] == "individual_land_width")
            assert width["status"] == "COMPLETED"  # P002/P004 not polluted by P003's missing factor


# ---------------------------------------------------------------------------
# 14. Provenance completeness.
# ---------------------------------------------------------------------------

class TestProvenanceCompleteness:
    def test_14_full_provenance_reconstructible(self, ddb_env, monkeypatch):
        case_no = "T4D1-14-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_table4(case_no)
        body = json.loads(resp["body"])
        analysis = body["analysis"]
        assert analysis["case_id"] == case_no
        assert analysis["rule_profile_id"] == PROFILE_ID
        assert analysis["base_segment_code"] == "P001-00"

        for comparison in analysis["comparisons"]:
            # Transaction (data) provenance.
            if comparison["adjusted_price_raw"] is not None:
                assert comparison["transaction_source_type"] == "競賽題目提供固定值"
                assert "題目.pdf" in comparison["transaction_source"]
            # Table5-1 bridge lineage.
            assert comparison["regional_adjustment_source_comparison_index"] == comparison["comparison_index"]
            for r in comparison["individual_factor_results"]:
                assert r["field_id"] and r["factor_name"]
                assert r["base_segment_code"] == "P001-00"
                assert r["comparable_segment_code"] == comparison["comparable_segment_code"]
                if r["status"] == "COMPLETED":
                    assert r["rule_id"] is not None
                    assert r["rule_source_document"] is not None
                else:
                    assert r["reason"]


# ---------------------------------------------------------------------------
# 17. No statutory weight fabrication.
# ---------------------------------------------------------------------------

class TestNoStatutoryWeightFabrication:
    def test_17_weight_always_manual_review_required_by_default(self, ddb_env, monkeypatch):
        case_no = "T4D1-17-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_table4(case_no)
        body = json.loads(resp["body"])
        for comparison in body["analysis"]["comparisons"]:
            assert comparison["weight_status"] == "MANUAL_REVIEW_REQUIRED"
            assert comparison["weight_pct"] is None
            assert comparison["weight_requires_human_confirmation"] is True
            assert "非" in comparison["weight_basis"] or "NON_STATUTORY" in comparison["weight_basis"].upper() or "未定義" in comparison["weight_basis"]

    def test_17_no_statutory_or_legal_weight_formula_claimed_anywhere(self):
        for path in ("engine/table4_analysis_engine.py", "backend/handlers/table4_analysis.py"):
            src = open(os.path.join(REPO_ROOT, path), encoding="utf-8").read()
            assert "STATUTORY_WEIGHT_FORMULA" not in src
            assert "=LEGAL_FORMULA" not in src


# ---------------------------------------------------------------------------
# 18. Legacy Jinshan preserved.
# ---------------------------------------------------------------------------

class TestLegacyJinshanPreserved:
    def test_18_legacy_case_blocks_cleanly_never_crashes(self, ddb_env, monkeypatch):
        case_no = "T4D1-18-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_legacy_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)

        resp = _get_table4(case_no)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "SEGMENT_MAP_REQUIRED"

    def test_18_legacy_complete_form_flow_still_works(self, ddb_env, monkeypatch):
        import cases
        import case_store
        import complete_form
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "T4D1-18B-001"
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
# 19. Real handler E2E (also covered inline by most tests above, which all
# go through the real cases/collect_data/table51_analysis/table4_analysis
# handlers -- this test asserts the full chain end to end explicitly).
# ---------------------------------------------------------------------------

class TestRealHandlerE2E:
    def test_19_full_chain_create_case_to_table4(self, ddb_env, monkeypatch):
        case_no = "T4D1-19-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_table4(case_no)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["comparable_count"] == 3
        assert body["rule_resolution_status"] == "CASE_IMPORTED_CONFIRMED"
        for c in body["analysis"]["comparisons"]:
            assert c["comparable_segment_code"] in ("P002-00", "P003-00", "P004-00")
            # Real fixture 題目.pdf transaction values, verified against
            # data/competition_cases/shulin_residential_2026/
            # segment_table4_fixtures.py.
            expected = fx4.SEGMENT_TABLE4_TRANSACTION[c["comparable_segment_code"]]
            assert float(c["adjusted_price_raw"]) == expected["adjusted_price_raw"]


# ---------------------------------------------------------------------------
# 20. Route registration.
# ---------------------------------------------------------------------------

class TestApiRouteRegistration:
    def test_20_table4_route_registered_in_template_yaml(self):
        template_path = os.path.join(REPO_ROOT, "infra", "template.yaml")
        text = open(template_path, encoding="utf-8").read()
        assert "table4_analysis.get_table4_analysis" in text
        assert "/api/cases/{id}/table4" in text
        # No duplicate/conflicting route.
        assert text.count("Path: '/api/cases/{id}/table4'") == 1

    def test_20_handler_referenced_by_route_actually_exists_and_is_callable(self):
        import table4_analysis
        assert callable(table4_analysis.get_table4_analysis)
