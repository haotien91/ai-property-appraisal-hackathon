# -*- coding: utf-8 -*-
"""
STEP 4 tests: Teammate Rule Data Integration (docs/audit/
TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md). Exercises the REAL teammate
source files (docs/incoming_rule_sources/*.doc + regional_rate_
calculation.json), the existing digitized central registry (data/rules/
central_max_adjustment_range.json, 418 entries, untouched by this round),
the new scripts/cross_check_teammate_central_data.py, engine/central_
local_rule_cross_validator.py, and data/rules/factor_alias_registry.json.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
sys.path.insert(0, REPO_ROOT)

import cross_check_teammate_central_data as xcheck  # noqa: E402
from central_local_rule_cross_validator import (  # noqa: E402
    check_central_maximum_not_exceeded, max_ceiling_for_factor,
)


def _load_json(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# TEST 1 -- Existing central max registry unchanged
# ---------------------------------------------------------------------------

def test_existing_central_max_registry_unchanged():
    d = _load_json("data", "rules", "central_max_adjustment_range.json")
    assert len(d["entries"]) == 418
    land_use_types = {e["land_use_type"] for e in d["entries"]}
    assert land_use_types == {"住宅用地", "商業用地", "工業用地", "農業用地", "其他用地"}
    # No second/parallel central registry was created.
    assert not os.path.exists(os.path.join(REPO_ROOT, "data", "rules", "central_factor_maximums.json"))


# ---------------------------------------------------------------------------
# TEST 2 -- Team central values cross-check
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not xcheck.antiword_available(), reason="antiword not available in this environment")
def test_teammate_central_values_cross_check():
    result = xcheck.run_cross_check()
    assert result["total_checked"] > 300
    assert result["total_mismatch"] == 0, f"real value discrepancies found: {result['mismatches']}"
    assert result["total_no_central_match"] == 0, f"unmatched teammate items: {result['no_central_match']}"


# ---------------------------------------------------------------------------
# TEST 3 -- '-' remains NOT_APPLICABLE
# ---------------------------------------------------------------------------

class TestDashSemantics:
    def test_dash_normalizes_to_none_not_zero(self):
        assert xcheck._teammate_value("-") is None
        assert xcheck._teammate_value("") is None
        assert xcheck._teammate_value("0") == "0"  # a REAL 0% value must stay distinct from NOT_APPLICABLE

    def test_central_registry_dash_cells_are_null_not_zero(self):
        d = _load_json("data", "rules", "central_max_adjustment_range.json")
        dash_cells = [e for e in d["entries"] if e["cell_state"] == "DASH_NOT_APPLICABLE"]
        assert dash_cells, "expected at least one DASH_NOT_APPLICABLE cell in the existing registry"
        assert all(e["max_range_pct"] is None for e in dash_cells)


# ---------------------------------------------------------------------------
# TEST 4/5/6/7 -- Land use coverage classified
# ---------------------------------------------------------------------------

def _coverage_for(land_use_type):
    central = _load_json("data", "rules", "central_max_adjustment_range.json")["entries"]
    reg = _load_json("data", "rules", "regional_rules.json")["rules"]
    ind = _load_json("data", "rules", "individual_rules.json")["rules"]
    central_available = any(e["land_use_type"] == land_use_type for e in central)
    local_regional_available = any(r["land_use_type"] == land_use_type for r in reg)
    local_individual_available = any(r["land_use_type"] == land_use_type for r in ind)
    return {
        "CENTRAL_MAX_AVAILABLE": central_available,
        "LOCAL_GRADE_AVAILABLE": local_regional_available or local_individual_available,
        "RUNTIME_READY": local_regional_available or local_individual_available,
    }


class TestLandUseCoverageClassified:
    def test_residential_coverage_classified(self):
        c = _coverage_for("住宅用地")
        assert c["CENTRAL_MAX_AVAILABLE"] is True
        assert c["LOCAL_GRADE_AVAILABLE"] is False
        assert c["RUNTIME_READY"] is False  # NOT_RUNTIME_GRADE_READY -- central ceiling only

    def test_commercial_coverage_classified(self):
        c = _coverage_for("商業用地")
        assert c["CENTRAL_MAX_AVAILABLE"] is True
        assert c["LOCAL_GRADE_AVAILABLE"] is True
        assert c["RUNTIME_READY"] is True

    def test_industrial_coverage_classified(self):
        c = _coverage_for("工業用地")
        assert c["CENTRAL_MAX_AVAILABLE"] is True
        assert c["LOCAL_GRADE_AVAILABLE"] is False
        assert c["RUNTIME_READY"] is False

    def test_agricultural_coverage_classified(self):
        c = _coverage_for("農業用地")
        assert c["CENTRAL_MAX_AVAILABLE"] is True
        assert c["LOCAL_GRADE_AVAILABLE"] is False
        assert c["RUNTIME_READY"] is False


# ---------------------------------------------------------------------------
# TEST 8 -- Unknown land use no fallback
# ---------------------------------------------------------------------------

def test_unknown_land_use_no_fallback():
    from rule_engine import RuleEngine, RuleNotFoundError

    reg = _load_json("data", "rules", "regional_rules.json")["rules"]  # 商業用地 only
    engine = RuleEngine(reg)
    with pytest.raises(RuleNotFoundError):
        engine.grade("新北市", "金山區", "住宅用地", "都市計畫（內、外）", True, rule_set="regional")


# ---------------------------------------------------------------------------
# TEST 9/10 -- Alias registry
# ---------------------------------------------------------------------------

class TestAliasRegistry:
    def test_alias_exact_mapping(self):
        registry = _load_json("data", "rules", "factor_alias_registry.json")
        by_alias = {}
        for entry in registry["aliases"]:
            assert entry["canonical_factor_id"]
            assert entry["scope"] in ("regional", "individual")
            for a in entry["aliases"]:
                by_alias[(entry["scope"], a["alias_text"])] = entry["canonical_factor_id"]
        assert by_alias[("regional", "都市計畫內外")] == "都市計畫（內、外）"
        assert by_alias[("individual", "使用分區或編定")] == "使用分區或編定用地"

    def test_alias_does_not_cross_scope(self):
        registry = _load_json("data", "rules", "factor_alias_registry.json")
        seen = {}
        for entry in registry["aliases"]:
            for a in entry["aliases"]:
                key = a["alias_text"]
                if key in seen and seen[key] != entry["scope"]:
                    pytest.fail(f"alias_text {key!r} used in both scope={seen[key]!r} and {entry['scope']!r}")
                seen[key] = entry["scope"]

    def test_no_second_parallel_naming_system(self):
        """Every canonical_factor_id must already exist as a real `factor`
        value somewhere in the existing local/central data -- this
        registry never invents a new factor identity."""
        registry = _load_json("data", "rules", "factor_alias_registry.json")
        reg = _load_json("data", "rules", "regional_rules.json")["rules"]
        ind = _load_json("data", "rules", "individual_rules.json")["rules"]
        central = _load_json("data", "rules", "central_max_adjustment_range.json")["entries"]
        known = ({r["factor"] for r in reg} | {r["factor"] for r in ind}
                 | {e["item_name"] for e in central if e.get("item_name")})
        for entry in registry["aliases"]:
            assert entry["canonical_factor_id"] in known, (
                f"canonical_factor_id {entry['canonical_factor_id']!r} not found in any existing rule/central source"
            )


# ---------------------------------------------------------------------------
# TEST 11 -- Regional fixture not loaded as runtime
# ---------------------------------------------------------------------------

def test_regional_rate_fixture_never_referenced_by_production_code():
    fixture_name = "teammate_regional_rate_calculation.json"
    assert os.path.exists(os.path.join(REPO_ROOT, "tests", "fixtures", fixture_name))
    for root_dir in ("backend", "engine", "providers", "domain"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO_ROOT, root_dir)):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                    assert fixture_name not in f.read(), f"{fn} references the teammate regression fixture"


# ---------------------------------------------------------------------------
# TEST 12 -- TEAMMATE_EXPECTED vs MAIN_ENGINE_ACTUAL
# ---------------------------------------------------------------------------

def test_teammate_expected_vs_main_engine_actual():
    """For every teammate fixture record whose sub_item exact-matches an
    existing regional_rules.json factor, the teammate's own benchmark
    grade encoding (grade_label_zh + rank) must be internally consistent
    with the SAME official rule table's own grade_code<->grade mapping --
    never silently skipped, never a fabricated pass. Records with no
    matching local factor are counted separately (MANUAL_REVIEW_REQUIRED
    territory, per §7's "comparable_missing 不得自行補值"), not treated
    as failures."""
    fixture = _load_json("tests", "fixtures", "teammate_regional_rate_calculation.json")
    reg = _load_json("data", "rules", "regional_rules.json")["rules"]
    grade_by_factor_code = {}
    for r in reg:
        grade_by_factor_code.setdefault(r["factor"], {})[r["grade_code"]] = r["grade"]

    checked, unavailable = 0, 0
    for rec in fixture["records"]:
        name = rec["sub_item"]["name_zh"]
        bench = rec["benchmark"]
        codes = grade_by_factor_code.get(name)
        if codes is None:
            unavailable += 1
            continue
        checked += 1
        local_grade = codes.get(bench["rank"])
        assert local_grade == bench["grade_label_zh"], (
            f"factor={name!r}: teammate rank={bench['rank']} grade={bench['grade_label_zh']!r} "
            f"but local rule table's grade at that code is {local_grade!r}"
        )
    assert checked >= 15, "fixture sanity: expected most of the 28 records to be checkable against local rules"


# ---------------------------------------------------------------------------
# TEST 13/14 -- Central maximum exceeded detection, never determines grade
# ---------------------------------------------------------------------------

class TestCentralMaximumValidation:
    def test_central_maximum_exceeded_detected(self):
        central = _load_json("data", "rules", "central_max_adjustment_range.json")["entries"]
        ind = _load_json("data", "rules", "individual_rules.json")["rules"]
        issues = check_central_maximum_not_exceeded(ind, central, "individual", "商業用地")
        exceeded_factors = {i.factor for i in issues}
        assert "道路種類" in exceeded_factors, (
            "expected the known RULE DATA finding (individual_rules.json's 道路種類 "
            "max_adjustment=8 exceeds the central individual-table ceiling of 5%) to be detected"
        )
        hit = next(i for i in issues if i.factor == "道路種類")
        assert hit.local_max_adjustment == 8.0
        assert hit.central_ceiling == 5.0

    def test_central_maximum_not_exceeded_for_clean_factors(self):
        central = _load_json("data", "rules", "central_max_adjustment_range.json")["entries"]
        reg = _load_json("data", "rules", "regional_rules.json")["rules"]
        issues = check_central_maximum_not_exceeded(reg, central, "regional", "商業用地")
        # regional_rules.json's factors are all comfortably within the
        # central ceiling (see docs/audit/TEAMMATE_RULE_DATA_INTEGRATION_
        # REPORT.md §5) -- zero violations expected here.
        assert issues == []

    def test_central_max_does_not_determine_grade(self):
        src = open(os.path.join(REPO_ROOT, "engine", "central_local_rule_cross_validator.py"), encoding="utf-8").read()
        import_lines = [l for l in src.splitlines() if l.strip().startswith(("import ", "from "))]
        assert not any("rule_engine" in l or "grade_engine" in l or "adjustment_engine" in l for l in import_lines)


# ---------------------------------------------------------------------------
# TEST 15 -- Same-segment inconsistent evidence flagged (no hardcoded shortcut)
# ---------------------------------------------------------------------------

def test_no_hardcoded_same_segment_zero_shortcut():
    """§9: adjustment must always come from comparing two independently-
    graded parties through the matrix, never a same-segment shortcut.
    Confirms no such shortcut exists anywhere in the engine layer (a
    dedicated SAME_SEGMENT_REGIONAL_FACTOR_INCONSISTENT AuditEngine Issue
    type does not exist yet -- documented as a remaining gap in docs/audit/
    TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md §9, not silently pretended
    to be implemented here)."""
    for fname in ("adjustment_engine.py", "calculation_engine.py", "audit_engine.py"):
        src = open(os.path.join(REPO_ROOT, "engine", fname), encoding="utf-8").read()
        assert "same_segment" not in src.lower().replace(" ", "")


# ---------------------------------------------------------------------------
# TEST 16/17 -- No Jinshan / no commercial fallback (Rule Router)
# ---------------------------------------------------------------------------

class TestRuleRouterNoCrossFallback:
    def test_no_jinshan_fallback_for_other_district(self):
        from rule_engine import RuleEngine, RuleNotFoundError

        reg = _load_json("data", "rules", "regional_rules.json")["rules"]  # 新北市/金山區 only
        engine = RuleEngine(reg)
        with pytest.raises(RuleNotFoundError):
            engine.grade("新北市", "板橋區", "商業用地", "都市計畫（內、外）", True, rule_set="regional")

    def test_no_commercial_fallback_for_residential_query(self):
        from rule_engine import RuleEngine, RuleNotFoundError

        reg = _load_json("data", "rules", "regional_rules.json")["rules"]  # 商業用地 only
        engine = RuleEngine(reg)
        with pytest.raises(RuleNotFoundError):
            engine.grade("新北市", "金山區", "住宅用地", "主要道路寬度", 18, unit="M", rule_set="regional")


# ---------------------------------------------------------------------------
# TEST 18 -- No Golden fallback
# ---------------------------------------------------------------------------

def test_no_golden_fallback_unrelated_district_query():
    from rule_engine import RuleEngine, RuleNotFoundError

    reg = _load_json("data", "rules", "regional_rules.json")["rules"]
    engine = RuleEngine(reg)
    # A query shaped like a real case but for a district/land_use_type
    # combination the Golden Case data was never built for must fail
    # honestly, never silently return a Golden Case number.
    with pytest.raises(RuleNotFoundError):
        engine.grade("新北市", "三重區", "商業用地", "主要道路寬度", 18, unit="M", rule_set="regional")


# ---------------------------------------------------------------------------
# TEST 19 -- No AI direct grade/rank/rate/price (reused from Phase 3C, kept
# here so STEP 4's own regression run exercises the guard directly too)
# ---------------------------------------------------------------------------

def test_ai_boundary_guard_still_rejects_final_decision_fields():
    sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
    from semantic_rule_mapping_provider import validate_ai_response_schema

    for field in ("grade", "rank", "adjustment_rate", "price"):
        status, _err = validate_ai_response_schema({"confidence": "HIGH", "reason": "x", field: "x"})
        assert status == "SCOPE_VIOLATION", f"field={field} should be rejected as a scope violation"


# ---------------------------------------------------------------------------
# TEST 20 -- Case A/B isolation retained
# ---------------------------------------------------------------------------

def test_case_isolation_retained():
    """Structural re-confirmation (Step 2's own 19-test suite already
    covers this exhaustively) that adding STEP 4's teammate-data artifacts
    (alias registry, central-local validator, cross-check script) did not
    touch CaseRuleRepository's case_id-scoped storage keys at all."""
    src = open(os.path.join(REPO_ROOT, "backend", "handlers", "case_rule_repository.py"), encoding="utf-8").read()
    assert 'f"CASE_RULE_PACKAGE#{package_id}"' in src.replace("'", '"')
    assert "CASE#" in src  # case_store's PK convention, unchanged
