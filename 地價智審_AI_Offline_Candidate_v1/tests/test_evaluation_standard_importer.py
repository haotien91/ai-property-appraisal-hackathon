# -*- coding: utf-8 -*-
"""
Tests for engine/evaluation_standard_importer.py (docs/audit/
EVALUATION_STANDARD_IMPORTER_PHASE3A_REPORT.md) -- deterministic-first
extraction of 評價基準明細表 PDFs into Rule Candidates that can be handed
to backend.handlers.case_rule_repository.CaseRuleRepository.save_candidate().

Uses the REAL sample PDF (data/sources/competition/評價基準明細表範例.pdf),
not a synthetic fixture -- the parsing rules in the importer were reverse
-engineered from this exact file's layout (see the Phase 3A survey), so
testing against a hand-built fake PDF would not actually exercise the
layout quirks (stream-order matrix/label interleaving, note-sentence
geometry) this module exists to handle.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, REPO_ROOT)

try:
    import pymupdf  # noqa: F401
except ImportError:
    pytest.skip("pymupdf not available", allow_module_level=True)

import evaluation_standard_importer as esi  # noqa: E402
from domain.models import CaseRulePackageStatus  # noqa: E402

PDF_PATH = os.path.join(REPO_ROOT, "data", "sources", "competition", "評價基準明細表範例.pdf")
ROAD_WIDTH_FACTOR = "主要道路寬度"


def _known_factor_names():
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return sorted({r["factor"] for r in reg}), sorted({r["factor"] for r in ind})


@pytest.fixture(scope="module")
def known_factors():
    return _known_factor_names()


@pytest.fixture(scope="module")
def report(known_factors):
    known_reg, known_ind = known_factors
    return esi.extract_from_pdf(PDF_PATH, known_reg, known_ind)


@pytest.fixture(scope="module")
def road_width_candidate(report):
    matches = [c for c in report.candidates if c.canonical_factor_id == ROAD_WIDTH_FACTOR]
    assert matches, "主要道路寬度 candidate not found -- fixture setup itself is broken"
    return matches[0]


# ---------------------------------------------------------------------
# TEST 1 -- PDF opens successfully
# ---------------------------------------------------------------------

def test_pdf_opens_successfully():
    doc = pymupdf.open(PDF_PATH)
    assert doc.page_count == 9
    assert doc[0].get_text().strip() != ""


# ---------------------------------------------------------------------
# TEST 2 -- Regional section detected
# ---------------------------------------------------------------------

def test_regional_section_detected(report):
    assert report.regional_table_detected is True
    assert any(c.scope == "regional" for c in report.candidates)


# ---------------------------------------------------------------------
# TEST 3 -- Individual section detected
# ---------------------------------------------------------------------

def test_individual_section_detected(report):
    assert report.individual_table_detected is True
    assert any(c.scope == "individual" for c in report.candidates)


# ---------------------------------------------------------------------
# TEST 4 -- Factor names extracted
# ---------------------------------------------------------------------

def test_factor_names_extracted(report):
    resolved = {c.canonical_factor_id for c in report.candidates if c.canonical_factor_id}
    # At least the Golden-verified factor, plus several others across both
    # scopes -- proves this isn't a single lucky match.
    assert ROAD_WIDTH_FACTOR in resolved
    assert "建蔽率" in resolved
    assert "容積率" in resolved
    assert "面積" in resolved
    assert len(resolved) >= 10


# ---------------------------------------------------------------------
# TEST 5 -- Grade labels extracted
# ---------------------------------------------------------------------

def test_grade_labels_extracted(road_width_candidate):
    grades = [b.grade for b in road_width_candidate.bands]
    assert grades == ["優", "稍優", "普通", "稍劣", "劣"]
    codes = [b.grade_code for b in road_width_candidate.bands]
    assert codes == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------
# TEST 6 -- Numeric range parsed
# ---------------------------------------------------------------------

def test_numeric_range_parsed_matches_golden_case(road_width_candidate):
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        existing = [r for r in json.load(f)["rules"] if r["factor"] == ROAD_WIDTH_FACTOR]
    existing_by_code = {r["grade_code"]: r for r in existing}

    for band in road_width_candidate.bands:
        e = existing_by_code[band.grade_code]
        assert band.value_type == "numeric_range"
        assert band.lower_bound == e["lower_bound"]
        assert band.upper_bound == e["upper_bound"]
        assert band.unit == e["unit"]


# ---------------------------------------------------------------------
# TEST 7 -- Boolean/categorical condition parsed
# ---------------------------------------------------------------------

def test_boolean_condition_parsed(report):
    candidates_with_you_wu = [
        c for c in report.candidates
        if {b.condition_text for b in c.bands} == {"有", "無"}
    ]
    assert candidates_with_you_wu, "expected at least one 有/無 boolean-condition block in the sample PDF"
    for c in candidates_with_you_wu:
        assert all(b.value_type == "boolean" for b in c.bands)
        assert all(b.lower_bound is None and b.upper_bound is None for b in c.bands)


def test_categorical_condition_parsed(report):
    # 形狀 (individual): 優=方形 / 劣=不規則形 -- neither is boolean or a
    # numeric range, must fall back to categorical (exact-label match).
    shape_candidates = [
        c for c in report.candidates
        if any(b.condition_text == "方形" for b in c.bands)
    ]
    assert shape_candidates
    assert all(b.value_type == "categorical" for b in shape_candidates[0].bands)


# ---------------------------------------------------------------------
# TEST 8 -- Explicit matrix parsed
# ---------------------------------------------------------------------

def test_explicit_matrix_parsed_matches_golden_case(road_width_candidate):
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        existing = [r for r in json.load(f)["rules"] if r["factor"] == ROAD_WIDTH_FACTOR]
    expected_matrix = existing[0]["adjustment_matrix"]
    assert road_width_candidate._matrix() == expected_matrix


# ---------------------------------------------------------------------
# TEST 9 -- Max adjustment parsed
# ---------------------------------------------------------------------

def test_max_adjustment_parsed(road_width_candidate):
    assert road_width_candidate.max_adjustment_declared == 15.0
    actual_max = max(abs(v) for band in road_width_candidate.bands for v in band.matrix_row)
    assert road_width_candidate.max_adjustment_declared == actual_max


# ---------------------------------------------------------------------
# TEST 10 -- Source page provenance preserved
# ---------------------------------------------------------------------

def test_source_page_provenance_preserved(road_width_candidate):
    assert road_width_candidate.source_document == PDF_PATH
    assert road_width_candidate.source_page == "2"  # 1-indexed; 主要道路寬度 is on page_index 1

    records = esi.candidate_to_rule_records(road_width_candidate, "PROV-CHECK")
    assert records
    for r in records:
        assert r["source_document"] == PDF_PATH
        assert r["source_page"] == "2"


# ---------------------------------------------------------------------
# TEST 11 -- Unknown factor does not guess
# ---------------------------------------------------------------------

def test_unknown_factor_does_not_guess(report):
    unmapped = [c for c in report.candidates if c.canonical_factor_id is None]
    assert unmapped, "expected at least one genuinely unmapped factor in the sample PDF"
    for c in unmapped:
        assert "UNKNOWN_FACTOR" in c.issues
        assert c.status in ("PARTIAL", "AMBIGUOUS")
        # An unmapped candidate must NEVER be silently turned into a rule
        # record -- there is no factor name to key it by.
        assert esi.candidate_to_rule_records(c, "PKG") == []


# ---------------------------------------------------------------------
# TEST 12 -- Ambiguous condition does not guess
# ---------------------------------------------------------------------

def test_ambiguous_compound_condition_does_not_guess(report):
    # 個別因素「寬度」的劣級："未滿10m或100m以上" -- two disjoint numeric
    # conditions, never force-parsed into one [lower, upper) band.
    compound = [
        c for c in report.candidates
        if any(b.condition_text == "未滿10m或100m以上" for b in c.bands)
    ]
    assert compound, "expected the compound-OR width band to be present in the sample PDF"
    band = next(b for b in compound[0].bands if b.condition_text == "未滿10m或100m以上")
    assert band.value_type == "categorical"
    assert band.lower_bound is None and band.upper_bound is None


# ---------------------------------------------------------------------
# TEST 13 -- Incomplete matrix detected
# ---------------------------------------------------------------------

def test_incomplete_matrix_detected():
    # Truncate the token stream right after the header + first numeric
    # row, before any grade label -- simulates a page that got cut off /
    # OCR dropout mid-table. Must be flagged, never silently accepted as
    # a smaller-than-declared matrix.
    lines = ["比凖地", "(比較標的)", "宗地", "  (比準地)", "0", "3.75", "7.5", "11.25", "15"]
    rows, max_adjustment, end = esi._parse_matrix_block(lines, 4)
    assert rows == []
    candidate = esi.RuleCandidate(
        scope="regional", city="新北市", district="金山區", land_use_type="商業用地",
        source_document="synthetic", source_page="1", max_adjustment_declared=max_adjustment,
    )
    esi._validate_candidate(candidate, expected_grade_count=5, actual_row_count=0)
    assert "RULE_EXTRACTION_INCOMPLETE" in candidate.issues
    assert candidate.status == "PARTIAL"
    assert esi.candidate_to_rule_records(candidate, "PKG") == []


# ---------------------------------------------------------------------
# TEST 14 -- Candidate cannot become CONFIRMED automatically
# ---------------------------------------------------------------------

def test_candidate_cannot_become_confirmed_automatically(known_factors):
    known_reg, known_ind = known_factors
    package, _ = esi.build_case_rule_package_from_pdf(
        PDF_PATH, case_id="PHASE3A-TEST-CASE", package_id="PHASE3A-TEST-PKG", rule_version="survey-v1",
        known_regional_factors=known_reg, known_individual_factors=known_ind,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    assert package.status != CaseRulePackageStatus.CONFIRMED
    assert package.status in (
        CaseRulePackageStatus.EXTRACTED, CaseRulePackageStatus.PARTIAL, CaseRulePackageStatus.AMBIGUOUS,
    )
    assert package.confirmed_at is None
    assert package.confirmed_by is None


# ---------------------------------------------------------------------
# TEST 15 -- Existing GradeEngine untouched
# ---------------------------------------------------------------------

def test_grade_engine_module_never_imported_or_referenced():
    importer_src = open(os.path.join(REPO_ROOT, "engine", "evaluation_standard_importer.py"), encoding="utf-8").read()
    import_lines = [l for l in importer_src.splitlines() if l.strip().startswith(("import ", "from "))]
    assert not any("grade_engine" in l for l in import_lines)


def test_grade_engine_behavior_unchanged_end_to_end(known_factors):
    """Functional proof, not just a source-grep: the SAME unmodified
    GradeEngine, fed rule records this importer extracted, reproduces the
    known Golden Case grade for 主要道路寬度=18m ("普通") -- i.e. the
    importer's output is a plain rule_schema.json-shaped list GradeEngine
    already knows how to consume, nothing about GradeEngine itself changed."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
    from grade_engine import GradeEngine
    from rule_engine import RuleEngine
    from domain.models import FactorInput, Evidence, SourceType, PartyRole

    known_reg, known_ind = known_factors
    report_ = esi.extract_from_pdf(PDF_PATH, known_reg, known_ind)
    candidate = next(c for c in report_.candidates if c.canonical_factor_id == ROAD_WIDTH_FACTOR)
    records = esi.candidate_to_rule_records(candidate, "GE-CHECK")

    rule_engine = RuleEngine(records)
    grade_engine = GradeEngine(rule_engine)
    fi = FactorInput(field_id="regional_main_road_width", factor=ROAD_WIDTH_FACTOR, raw_value=18, unit="M",
                      evidence=Evidence(source="test", source_type=SourceType.AI_ASSISTED_FILL))
    result = grade_engine.grade_factor("新北市", "金山區", "商業用地", "regional_main_road_width", fi,
                                        PartyRole.BASE_PARCEL, "base", rule_set="regional")
    assert result.rule_result.grade == "普通"


# ---------------------------------------------------------------------
# TEST 16 -- Existing AdjustmentEngine untouched
# ---------------------------------------------------------------------

def test_adjustment_engine_module_never_imported_or_referenced():
    importer_src = open(os.path.join(REPO_ROOT, "engine", "evaluation_standard_importer.py"), encoding="utf-8").read()
    import_lines = [l for l in importer_src.splitlines() if l.strip().startswith(("import ", "from "))]
    assert not any("adjustment_engine" in l for l in import_lines)


def test_adjustment_engine_behavior_unchanged_end_to_end(known_factors):
    sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
    from grade_engine import GradeEngine
    from adjustment_engine import AdjustmentEngine
    from rule_engine import RuleEngine
    from domain.models import FactorInput, Evidence, SourceType, PartyRole

    known_reg, known_ind = known_factors
    report_ = esi.extract_from_pdf(PDF_PATH, known_reg, known_ind)
    candidate = next(c for c in report_.candidates if c.canonical_factor_id == ROAD_WIDTH_FACTOR)
    records = esi.candidate_to_rule_records(candidate, "AE-CHECK")

    rule_engine = RuleEngine(records)
    grade_engine = GradeEngine(rule_engine)
    adjustment_engine = AdjustmentEngine(rule_engine)
    ev = Evidence(source="test", source_type=SourceType.AI_ASSISTED_FILL)
    base_fi = FactorInput(field_id="regional_main_road_width", factor=ROAD_WIDTH_FACTOR, raw_value=18, unit="M", evidence=ev)
    comp_fi = FactorInput(field_id="regional_main_road_width", factor=ROAD_WIDTH_FACTOR, raw_value=30, unit="M", evidence=ev)
    base_g = grade_engine.grade_factor("新北市", "金山區", "商業用地", "regional_main_road_width", base_fi,
                                        PartyRole.BASE_PARCEL, "base", rule_set="regional")
    comp_g = grade_engine.grade_factor("新北市", "金山區", "商業用地", "regional_main_road_width", comp_fi,
                                        PartyRole.COMPARABLE, "comp1", rule_set="regional")
    adj = adjustment_engine.compute_adjustment(base_g, comp_g)
    # base=普通(3), comp=優(1) -> matrix["3"]["1"] == -7.5 per the Golden matrix.
    assert float(adj.adjustment_pct) == -7.5


# ---------------------------------------------------------------------
# §12 Golden-like Import Verification (Phase 3A spec, distinct from the
# numbered TEST 1-16 list above) -- full package build reproduces a
# zero-structural-error, RuleEngine-consumable package.
# ---------------------------------------------------------------------

def test_golden_like_import_verification_full_package(known_factors, monkeypatch):
    # case_store.py captures CASES_TABLE_NAME as a module-level constant at
    # FIRST import in the process (correct for real Lambda, where env vars
    # are set before the handler code runs at all) -- set it before the
    # transitive `import case_rule_repository -> import case_store` below,
    # so this test behaves the same regardless of which other test file's
    # ddb_env fixture (or lack thereof) happened to import case_store first
    # in a given pytest invocation.
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
    from case_rule_repository import validate_package_rules_scoped, RuleTableValidator
    from rule_engine import RuleEngine

    known_reg, known_ind = known_factors
    package, _ = esi.build_case_rule_package_from_pdf(
        PDF_PATH, case_id="GOLDEN-VERIFY-CASE", package_id="GOLDEN-VERIFY-PKG", rule_version="v1",
        known_regional_factors=known_reg, known_individual_factors=known_ind,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    issues = validate_package_rules_scoped(package.regional_rules, package.individual_rules)
    assert not RuleTableValidator.has_errors(issues)

    rule_engine = RuleEngine(package.regional_rules + package.individual_rules)
    g = rule_engine.grade("新北市", "金山區", "商業用地", ROAD_WIDTH_FACTOR, 18, unit="M", rule_set="regional")
    assert g.grade == "普通"
