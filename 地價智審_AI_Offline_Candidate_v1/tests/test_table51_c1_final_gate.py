# -*- coding: utf-8 -*-
"""
TABLE51-THREE-COMPARABLE-C1-FINAL-GATE-1.

Closes two data-trustworthiness gaps found in TABLE51-THREE-COMPARABLE-C1's
first pass:

1. That round's own fixture fix (segment_table3_fixtures.py) rewrote 4
   COMPETITION_PROVIDED_FIXED raw_values from 題目.pdf's literal text
   ("第一種住宅區", "無", the literal ticked-item list) directly into the
   rule pack's own categorical band labels ("住宅區、市場用地",
   "無禁止建築", "無限制建築", "四項以上") -- losing the original source
   value entirely. Fixed: raw_value is restored to the verbatim 題目.pdf
   text; a NEW, separate normalization layer (engine/regional_factor_
   value_normalization.py) maps raw_value -> evaluation_value ONLY for the
   GradeEngine call, never mutating raw_value itself.
2. Table51FactorResult only ever exposed ONE "source" string (the rule's
   own source_document/source_page) -- collapsing "where did this NUMBER
   come from" (data provenance: COMPETITION_PROVIDED_FIXED / Provider /
   AI / etc.) and "which regulation matched it" (rule provenance) into one
   field. Fixed: domain.models.Table51FactorResult now carries
   base_source_type/base_source/comparable_source_type/comparable_source
   (data provenance) SEPARATELY from rule_id/rule_source_document/
   rule_source_page (rule provenance), plus base_evaluation_value/
   comparable_evaluation_value/normalization_reason/mapping_source.
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
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-t51fg")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table-t51fg",
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


def _collect_segment(collect_data_module, case_no, segment_code, factors=None):
    return collect_data_module.collect_data(
        {"pathParameters": {"id": case_no, "segment_code": segment_code},
         "body": json.dumps({"competition_provided_factors": factors or fx.SEGMENT_TABLE3_FACTORS[segment_code]})},
        None,
    )


def _confirm_shulin_package(case_no, package_id="PKG-T51FG"):
    from case_rule_repository import DynamoCaseRuleRepository
    repo = DynamoCaseRuleRepository()
    pkg = CaseRulePackage(
        case_id=case_no, package_id=package_id, rule_version="t51fg-test-v1",
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


def _full_setup(monkeypatch, case_no):
    import cases
    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_shulin_case(cases, case_no)
    for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
        resp = _collect_segment(collect_data, case_no, code)
        assert resp["statusCode"] == 200, resp["body"]
    _confirm_shulin_package(case_no)


def _get_analysis(case_no):
    import table51_analysis
    return table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)


_FIXED_FIELDS_WITH_NORMALIZATION = (
    "regional_land_use_zone", "regional_construction_prohibited",
    "regional_construction_restricted", "regional_land_improvement",
)


# ---------------------------------------------------------------------------
# Task 1 A-C: raw_value preserved, evaluation_value separated.
# ---------------------------------------------------------------------------

class TestRawValuePreservedFromCompetitionSourceTruth:
    def test_a_land_use_zone_raw_preserved_but_grades_correctly(self, ddb_env, monkeypatch):
        case_no = "T51FG-A-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        zone = next(r for r in p002["factor_results"] if r["field_id"] == "regional_land_use_zone")

        assert zone["base_raw_value"] == "第一種住宅區"  # verbatim 題目.pdf text, never rewritten
        assert zone["base_evaluation_value"] == "住宅區、市場用地"  # separate, mapped value used for grading
        assert zone["status"] == "COMPLETED"  # grading still succeeds via the mapping
        assert zone["base_grade"] is not None
        assert zone["mapping_source"] is not None and "評價基準明細表" in zone["mapping_source"]

    def test_b_construction_prohibited_raw_preserved_but_grades_correctly(self, ddb_env, monkeypatch):
        case_no = "T51FG-B-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        prohibited = next(r for r in p002["factor_results"] if r["field_id"] == "regional_construction_prohibited")

        assert prohibited["base_raw_value"] == "無"  # verbatim checkbox reading, NEVER "無禁止建築"
        assert prohibited["base_evaluation_value"] == "無禁止建築"
        assert prohibited["status"] == "COMPLETED"
        assert prohibited["comparable_raw_value"] == "無"
        assert prohibited["comparable_evaluation_value"] == "無禁止建築"

    def test_c_stored_factors_match_fixture_source_truth_verbatim(self, ddb_env, monkeypatch):
        """collect_data 後 FACTORS#P001/P002/P003/P004 之
        competition_provided_factors raw values 必須與 segment_table3_
        fixtures.py（題目.pdf 之逐字轉錄）完全一致 -- 逐一比對，不只抽查。"""
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "T51FG-C-001"
        _make_shulin_case(cases, case_no)
        for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
            _collect_segment(collect_data, case_no, code)

        for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
            rec = case_store.get_record(case_no, competition_segments.factors_sk(code))
            stored_by_field = {f["field_id"]: f["raw_value"] for f in rec["competition_provided_factors"]}
            expected_by_field = {f["field_id"]: f["raw_value"] for f in fx.SEGMENT_TABLE3_FACTORS[code]}
            assert stored_by_field == expected_by_field, f"{code}: stored raw values diverge from fixture source truth"
            # Explicitly re-affirm the 4 previously-at-risk fields are the
            # LITERAL 題目.pdf text, never a rule-pack band label.
            for field_id in _FIXED_FIELDS_WITH_NORMALIZATION:
                assert stored_by_field[field_id] not in (
                    "住宅區、市場用地", "商業區、捷運用地(聯開)", "無禁止建築", "有禁止建築",
                    "無限制建築", "部分限制建築(如高度限制或面積限制)", "限制整體開發",
                    "四項以上", "三項", "二項", "一項",
                ), f"{code}.{field_id} raw_value was rewritten into a rule-pack band label: {stored_by_field[field_id]!r}"


# ---------------------------------------------------------------------------
# Task 2 D-G: complete input + rule provenance, correctly separated.
# ---------------------------------------------------------------------------

class TestCompleteInputAndRuleProvenance:
    def test_d_p001_competition_provided_provenance_preserved(self, ddb_env, monkeypatch):
        case_no = "T51FG-D-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        road = next(r for r in p002["factor_results"] if r["field_id"] == "regional_main_road_width")

        assert road["base_source_type"] == "競賽題目提供固定值"
        assert "題目.pdf" in road["base_source"]

    def test_e_p002_competition_provided_provenance_preserved(self, ddb_env, monkeypatch):
        case_no = "T51FG-E-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        road = next(r for r in p002["factor_results"] if r["field_id"] == "regional_main_road_width")

        assert road["comparable_source_type"] == "競賽題目提供固定值"
        assert "題目.pdf" in road["comparable_source"]

    def test_f_provider_evidence_and_rule_source_coexist_and_distinguishable(self, ddb_env, monkeypatch):
        """Data provenance (base_source_type/base_source -- WHERE the
        number came from) must be a genuinely different field from rule
        provenance (rule_id/rule_source_document/rule_source_page -- WHICH
        regulation matched it) -- never collapsed into one ambiguous
        'source' string."""
        case_no = "T51FG-F-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        road = next(r for r in p002["factor_results"] if r["field_id"] == "regional_main_road_width")

        assert road["base_source_type"] == "競賽題目提供固定值"  # DATA provenance
        assert road["rule_source_document"] == "評價基準明細表.pdf"  # RULE provenance -- a DIFFERENT field
        assert road["rule_id"] is not None
        # The two must not be the same string (proves they are genuinely
        # separate concepts, not just two names for the same value).
        assert road["base_source"] != road["rule_source_document"]

    def test_g_manual_review_factor_retains_input_provenance(self, ddb_env, monkeypatch):
        """A factor whose raw_value IS present but cannot be graded (e.g.
        an unrecognized 使用分區 zone name outside the known mapping table
        -- never guessed) must still show base_source_type/base_source,
        never null them out just because grading failed."""
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "T51FG-G-001"
        _make_shulin_case(cases, case_no)
        for code in ("P002-00", "P003-00", "P004-00"):
            _collect_segment(collect_data, case_no, code)

        p001_factors = [dict(f) for f in fx.P001_TABLE3_FACTORS]
        zone_index = next(i for i, f in enumerate(p001_factors) if f["field_id"] == "regional_land_use_zone")
        p001_factors[zone_index] = dict(p001_factors[zone_index], raw_value="未知特殊分區")  # not in the normalization map -- must stay MANUAL_REVIEW_REQUIRED
        _collect_segment(collect_data, case_no, "P001-00", factors=p001_factors)
        _confirm_shulin_package(case_no)

        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        p002 = next(c for c in body["analysis"]["comparisons"] if c["comparable_segment_code"] == "P002-00")
        zone = next(r for r in p002["factor_results"] if r["field_id"] == "regional_land_use_zone")

        assert zone["status"] == "MANUAL_REVIEW_REQUIRED"
        assert zone["base_raw_value"] == "未知特殊分區"
        assert zone["base_evaluation_value"] is None  # no mapping found -- never guessed
        assert zone["base_source_type"] == "競賽題目提供固定值"  # input provenance still present
        assert zone["base_source"] is not None
        assert zone["reason"]


# ---------------------------------------------------------------------------
# Task 3: aggregation regression -- still fail-closed, still labeled
# OFFICIAL_BLANK_FORM_FORMULA (never STATUTORY/LEGAL).
# ---------------------------------------------------------------------------

class TestAggregationStillFailClosed:
    def test_partial_category_never_produces_a_fabricated_subtotal(self, ddb_env, monkeypatch):
        case_no = "T51FG-AGG-001"
        _full_setup(monkeypatch, case_no)  # real fixtures: only 15/29 factors filled
        resp = _get_analysis(case_no)
        body = json.loads(resp["body"])
        for comparison in body["analysis"]["comparisons"]:
            for subtotal in comparison["category_subtotals"]:
                if subtotal["requires_manual_review"]:
                    assert subtotal["subtotal_pct"] is None
            if comparison["requires_manual_review"]:
                assert comparison["total_adjustment_pct"] is None

    def test_documentation_never_claims_statutory_or_legal_formula(self):
        """The doc is allowed to MENTION these forbidden labels in prose
        (e.g. explaining that a test guards against them, in backticks,
        as this very docstring does) -- what it must never do is actually
        LABEL the aggregation formula with one, i.e. an assignment-style
        occurrence like "=STATUTORY_FORMULA" or "OFFICIAL_XLSX_FORMULA_
        FABRICATED=YES". A bare substring check would false-positive on
        the doc's own meta-commentary describing this very safeguard."""
        doc_path = os.path.join(REPO_ROOT, "docs", "phase7", "table51_three_comparable_c1.md")
        text = open(doc_path, encoding="utf-8").read()
        assert "=STATUTORY_FORMULA" not in text
        assert "=LEGAL_FORMULA" not in text
        assert "OFFICIAL_XLSX_FORMULA_FABRICATED=YES" not in text
        assert "OFFICIAL_BLANK_FORM_FORMULA" in text

        engine_src = open(os.path.join(REPO_ROOT, "engine", "table51_analysis_engine.py"), encoding="utf-8").read()
        assert "STATUTORY_FORMULA" not in engine_src
        assert "LEGAL_FORMULA" not in engine_src


# ---------------------------------------------------------------------------
# Task 4: API route registration audit + smoke test.
# ---------------------------------------------------------------------------

class TestApiRouteRegistration:
    def test_table51_route_registered_in_template_yaml(self):
        template_path = os.path.join(REPO_ROOT, "infra", "template.yaml")
        text = open(template_path, encoding="utf-8").read()
        assert "table51_analysis.get_table51_analysis" in text
        assert "/api/cases/{id}/table5-1" in text

    def test_handler_referenced_by_route_actually_exists_and_is_callable(self):
        import table51_analysis
        assert callable(table51_analysis.get_table51_analysis)
