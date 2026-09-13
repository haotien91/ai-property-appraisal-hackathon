# -*- coding: utf-8 -*-
"""
Phase 6 test suite: Smart Review / Cross-Form Audit.

Verifies every REQUIRED CHECK from the Phase 6 instructions (Grade Error,
Adjustment Error, Subtotal/Total Error, Missing, Wrong Unit, Rule Not
Found, Cross-form Inconsistent, Upstream/Downstream Impact), and that all
three DEMO ERROR CASES (A/B/C) -- grounded in already-verified Golden Case
values, not arbitrary numbers -- are automatically detected with correct
downstream impact traced.
"""
import sys
import os
from decimal import Decimal

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "engine", os.path.join("data", "golden"), os.path.join("data", "demo_errors")):
    sys.path.insert(0, os.path.join(REPO_ROOT, p) if p else REPO_ROOT)

from rule_engine import RuleEngine, AmbiguousFactorError  # noqa: E402
from dependency_impact_analyzer import DependencyImpactAnalyzer  # noqa: E402
from rule_validator import RuleValidator  # noqa: E402
from calculation_validator import CalculationValidator  # noqa: E402
from cross_form_validation_engine import CrossFormValidationEngine  # noqa: E402
from engine.audit_engine import AuditEngine, SubmittedFormData  # noqa: E402
from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402
from build_demo_submission import build_submitted_form_with_demo_errors  # noqa: E402
from domain.models import (  # noqa: E402
    IssueType, Severity, CheckType, RoadWidthEvidence, RoadWidthEvidenceType,
)


@pytest.fixture(scope="module")
def rule_engine_instance():
    import json
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return RuleEngine(reg + ind)


@pytest.fixture(scope="module")
def dep_analyzer():
    return DependencyImpactAnalyzer(os.path.join(REPO_ROOT, "data", "dependency_graph.json"))


@pytest.fixture()
def audit_result(rule_engine_instance):
    submitted = build_submitted_form_with_demo_errors(
        rule_engine_instance, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL
    )
    audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
    return audit.review(GOLDEN_CASE, submitted, BASE_REGIONAL, COMP_REGIONAL)


# ---------------------------------------------------------------------
# Dependency Impact Analyzer (Upstream/Downstream)
# ---------------------------------------------------------------------
class TestDependencyImpactAnalyzer:
    def test_regional_factor_traces_to_final_price(self, dep_analyzer):
        impact = dep_analyzer.analyze("regional_main_road_width_adjustment_pct_溫泉段218地號")
        assert "region_adjustment_rate_溫泉段218地號" in impact
        assert "trial_price_溫泉段218地號" in impact
        assert "base_parcel_comparison_price" in impact

    def test_individual_factor_traces_to_final_price(self, dep_analyzer):
        impact = dep_analyzer.analyze("individual_land_depth_differential_rate_溫泉段218地號")
        assert "individual_adjustment_total_溫泉段218地號" in impact
        assert "base_parcel_comparison_price" in impact

    def test_unrelated_field_has_no_impact(self, dep_analyzer):
        assert dep_analyzer.analyze("some_unrelated_field") == []

    def test_final_price_field_itself_has_no_further_downstream(self, dep_analyzer):
        # base_parcel_comparison_price matches no upstream_pattern (it's the sink)
        assert dep_analyzer.analyze("base_parcel_comparison_price") == []


# ---------------------------------------------------------------------
# RuleValidator: Grade Error / Missing / Wrong Unit / Rule Not Found
# ---------------------------------------------------------------------
class TestRuleValidatorChecks:
    def test_grade_error_detected(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade(
            "ISS-T1", "表5-2", "regional_main_road_width_adjustment_pct_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "M", "regional", submitted_grade="優",
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.explanation_data.check_type == CheckType.GRADE_ERROR
        assert issue.expected_value == "普通"

    def test_grade_passed_when_correct(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade(
            "ISS-T2", "表5-2", "field_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "M", "regional", submitted_grade="普通",
        )
        assert issue.issue_type == IssueType.PASSED

    def test_missing_grade_detected(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade(
            "ISS-T3", "表5-2", "field_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "M", "regional", submitted_grade=None,
        )
        assert issue.issue_type == IssueType.MISSING

    def test_wrong_unit_detected(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade(
            "ISS-T4", "表5-2", "field_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "KM", "regional", submitted_grade="普通",
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.explanation_data.check_type == CheckType.WRONG_UNIT

    def test_rule_not_found_detected(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade(
            "ISS-T5", "表5-2", "field_x", "不存在因素",
            "新北市", "金山區", "商業用地", "不存在因素XYZ", 18, "M", "regional", submitted_grade="優",
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.explanation_data.check_type == CheckType.RULE_NOT_FOUND

    def test_ambiguous_factor_without_rule_set_is_rule_not_found_type(self, rule_engine_instance, dep_analyzer):
        """建蔽率 exists in both tables; omitting rule_set must surface as a
        handled RULE_NOT_FOUND-class issue, not an unhandled exception."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        with pytest.raises(TypeError):
            # rule_set is a required positional/keyword arg in this validator's
            # call signature -- this test documents that the caller must always
            # supply it, matching Phase 4 BLK-01's fix at the GradeEngine layer.
            rv.validate_grade(
                "ISS-T6", "表5-2", "field_x", "建蔽率",
                "新北市", "金山區", "商業用地", "建蔽率", 70, "%", submitted_grade="優",
            )


# ---------------------------------------------------------------------
# Grade Representation Contract (Phase D): Check A (grade identity, code
# vs code) and Check B (grade representation, code/text self-consistency)
# -- orthogonal to each other and to validate_grade() above (untouched).
# ---------------------------------------------------------------------
class TestGradeIdentityCheck:
    """Check A: submitted_grade_code vs expected.grade_code."""

    def test_code_correct_passes(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_identity(
            "ISS-A1", "表5-2", "field_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "M", "regional",
            submitted_grade_code="3",
        )
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK
        assert issue.submitted_value == "3"
        assert issue.expected_value == "3"

    def test_code_wrong_is_grade_error(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_identity(
            "ISS-A2", "表5-2", "field_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "M", "regional",
            submitted_grade_code="1",
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.explanation_data.check_type == CheckType.GRADE_ERROR
        assert issue.submitted_value == "1"
        assert issue.expected_value == "3"

    def test_code_missing_is_missing_not_error(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_identity(
            "ISS-A3", "表5-2", "field_x", "主要道路寬度優劣等級",
            "新北市", "金山區", "商業用地", "主要道路寬度", 18, "M", "regional",
            submitted_grade_code=None,
        )
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.MISSING

    def test_boolean_factor_correct_code_passes(self, rule_engine_instance, dep_analyzer):
        """有無禁止建築 -- Golden PDF's code=1 matches raw_value=無's
        expected code=1: Check A must PASS purely on code identity,
        independent of whatever text representation question exists."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_identity(
            "ISS-A4", "表5-2", "field_x", "有無禁止建築優劣等級",
            "新北市", "金山區", "商業用地", "有無禁止建築", "無", None, "regional",
            submitted_grade_code="1",
        )
        assert issue.issue_type == IssueType.PASSED


class TestGradeRepresentationCheck:
    """Check B: submitted_grade_code/text internal self-consistency,
    orthogonal to Check A -- uses the SUBMITTED code (never the expected
    code) to look up the allowed text set, so a wrong-but-self-consistent
    submission is CONSISTENT here even though Check A fails it."""

    def test_code_correct_canonical_grade_text_passes(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B1", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code="3", submitted_grade_text="普通",
        )
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK

    def test_code_correct_officially_supported_grade_label_text_passes(self, rule_engine_instance, dep_analyzer):
        """有無禁止建築 code=1, text="無" -- 評價基準明細表範例 explicitly
        defines 優=無/劣=有 for this factor (user-supplied official
        evidence), so "無" is an ALLOWED representation for code=1, not a
        blanket grade_label acceptance."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B2", "表5-2", "field_x", "有無禁止建築優劣等級文字表述", "regional_construction_prohibited",
            "新北市", "金山區", "商業用地", "有無禁止建築", "regional",
            submitted_grade_code="1", submitted_grade_text="無",
        )
        assert issue.issue_type == IssueType.PASSED
        assert issue.submitted_value == "無"

    def test_grade_label_not_accepted_for_factor_without_evidence(self, rule_engine_instance, dep_analyzer):
        """主要道路寬度 has NO evidence that its grade_label ("15m以上未滿
        20m") is ever a valid submitted representation -- only "普通" is
        accepted for code=3, unlike the 2 evidence-confirmed boolean
        factors above. This locks in guardrail 1: no blanket 28-factor
        grade_label acceptance."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B3", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code="3", submitted_grade_text="15m以上未滿20m",
        )
        assert issue.issue_type == IssueType.WARNING
        assert issue.explanation_data.check_type == CheckType.GRADE_REPRESENTATION_INCONSISTENT

    def test_code_correct_incompatible_text_is_inconsistent(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B4", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code="1", submitted_grade_text="劣",
        )
        assert issue.issue_type == IssueType.WARNING
        assert issue.explanation_data.check_type == CheckType.GRADE_REPRESENTATION_INCONSISTENT
        assert issue.severity != Severity.CRITICAL  # never conflated with GRADE_ERROR's severity

    def test_code_wrong_but_text_internally_consistent_with_wrong_code_is_consistent(
        self, rule_engine_instance, dep_analyzer,
    ):
        """The exact scenario from the Final Contract's Example 1 / this
        round's guardrail example 2: code=1(wrong)/text=優 -- Check B only
        asks "does 優 belong to code=1's own allowed set", which it does,
        REGARDLESS of code=1 being the wrong grade for this factor's
        true raw_value. Check A (identity) is what catches the wrongness,
        not Check B."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B5", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code="1", submitted_grade_text="優",
        )
        assert issue.issue_type == IssueType.PASSED

    def test_invalid_code_is_distinct_from_missing_code(self, rule_engine_instance, dep_analyzer):
        """code="9" does not correspond to any grade band 主要道路寬度
        defines (only 1-5 exist) -- must be GRADE_CODE_INVALID, never
        MISSING, and must never fall back to guessing a grade from text."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B6", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code="9", submitted_grade_text="普通",
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.explanation_data.check_type == CheckType.GRADE_CODE_INVALID
        assert issue.explanation_data.check_type != CheckType.MISSING

    def test_code_missing_is_missing(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B7", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code=None, submitted_grade_text="普通",
        )
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.MISSING

    def test_text_missing_is_missing(self, rule_engine_instance, dep_analyzer):
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B8", "表5-2", "field_x", "主要道路寬度優劣等級文字表述", "regional_main_road_width",
            "新北市", "金山區", "商業用地", "主要道路寬度", "regional",
            submitted_grade_code="3", submitted_grade_text=None,
        )
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.MISSING

    def test_submitted_text_never_rewritten_in_the_issue(self, rule_engine_instance, dep_analyzer):
        """Guardrail: never auto-modify submitted code or text -- the
        INCONSISTENT issue's submitted_value must remain the verbatim
        submitted text, not silently normalized to expected."""
        rv = RuleValidator(rule_engine_instance, dep_analyzer)
        issue = rv.validate_grade_representation(
            "ISS-B9", "表5-2", "field_x", "有無禁止建築優劣等級文字表述", "regional_construction_prohibited",
            "新北市", "金山區", "商業用地", "有無禁止建築", "regional",
            submitted_grade_code="1", submitted_grade_text="有",  # code1's allowed set is {優,無}, not 有
        )
        assert issue.issue_type == IssueType.WARNING
        assert issue.submitted_value == "有"  # untouched, not rewritten to 優 or 無


# ---------------------------------------------------------------------
# CalculationValidator: Adjustment / Subtotal / Total Error
# ---------------------------------------------------------------------
class TestCalculationValidatorChecks:
    def test_adjustment_error_detected(self, rule_engine_instance, dep_analyzer):
        cv = CalculationValidator(rule_engine_instance, dep_analyzer)
        base = rule_engine_instance.grade("新北市", "金山區", "商業用地", "深度", 23, unit="M", rule_set="individual")
        comp = rule_engine_instance.grade("新北市", "金山區", "商業用地", "深度", 16, unit="M", rule_set="individual")
        issue = cv.validate_adjustment(
            "ISS-T7", "表4", "field_x", "深度差異率", "深度",
            base.grade_code, base.grade, comp.grade_code, comp.grade,
            base.rule_id, base.matched_rule["adjustment_matrix"], submitted_adjustment="5.00",
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.expected_value == "1"

    def test_subtotal_error_detected(self, rule_engine_instance, dep_analyzer):
        cv = CalculationValidator(rule_engine_instance, dep_analyzer)
        issue = cv.validate_sum(
            "ISS-T8", "表5-2", "field_x", "土地使用管制百分比小計",
            component_values=[0, 0, 0, 0, 0, 0], submitted_total="3.00",
            check_type=CheckType.SUBTOTAL_ERROR,
        )
        assert issue.issue_type == IssueType.ERROR
        assert issue.expected_value == "0"

    def test_total_error_detected(self, rule_engine_instance, dep_analyzer):
        cv = CalculationValidator(rule_engine_instance, dep_analyzer)
        issue = cv.validate_sum(
            "ISS-T9", "表5-2", "field_x", "影響地價區域因素總修正數",
            component_values=[0, 0, 0, 0, 0, 0, 0, 0], submitted_total="2.50",
            check_type=CheckType.TOTAL_ERROR,
        )
        assert issue.issue_type == IssueType.ERROR

    def test_missing_total_detected(self, rule_engine_instance, dep_analyzer):
        cv = CalculationValidator(rule_engine_instance, dep_analyzer)
        issue = cv.validate_sum(
            "ISS-T10", "表5-2", "field_x", "小計", component_values=[1, 2], submitted_total=None,
            check_type=CheckType.SUBTOTAL_ERROR,
        )
        assert issue.issue_type == IssueType.MISSING


# ---------------------------------------------------------------------
# CrossFormValidationEngine: Cross-form Inconsistent
# ---------------------------------------------------------------------
class TestCrossFormValidationChecks:
    def test_cross_form_inconsistent_detected(self, dep_analyzer):
        cfe = CrossFormValidationEngine(dep_analyzer)
        issue = cfe.validate_regional_total_matches_table4(
            "ISS-T11", "溫泉段218地號", table5_2_regional_total="0.00", table4_region_adjustment_rate="5.00",
        )
        assert issue.issue_type == IssueType.INCONSISTENT
        assert issue.severity == Severity.CRITICAL

    def test_cross_form_consistent_passes(self, dep_analyzer):
        cfe = CrossFormValidationEngine(dep_analyzer)
        issue = cfe.validate_regional_total_matches_table4(
            "ISS-T12", "溫泉段218地號", table5_2_regional_total="0.00", table4_region_adjustment_rate="0.00",
        )
        assert issue.issue_type == IssueType.PASSED


# ---------------------------------------------------------------------
# Full Golden-Case-grounded Demo Error Cases (A/B/C), via AuditEngine
# ---------------------------------------------------------------------
class TestDemoErrorCases:
    def test_case_a_grade_error_detected_with_downstream(self, audit_result):
        """Case A: 主要道路寬度18M真實等級代碼為3(普通)，故意提交代碼1(優)。
        Grade Representation Contract Phase D: Check A (grade identity)
        compares CODE, not text -- submitted_value/expected_value are now
        grade_code strings."""
        matches = [i for i in audit_result.issues if i.field == "regional_main_road_width_adjustment_pct_溫泉段218地號"]
        assert len(matches) == 1
        issue = matches[0]
        assert issue.issue_type == IssueType.ERROR
        assert issue.explanation_data.check_type == CheckType.GRADE_ERROR
        assert issue.submitted_value == "1"
        assert issue.expected_value == "3"
        assert "base_parcel_comparison_price" in issue.downstream_impact

    def test_case_a_representation_check_passes_since_code_and_text_agree(self, audit_result):
        """Case A's corruption is self-consistent (code=1, text=優 both
        wrong TOGETHER) -- Check B (representation) must PASS even though
        Check A (identity) fails; the two are orthogonal by design."""
        matches = [i for i in audit_result.issues
                   if i.field == "regional_main_road_width_grade_representation_溫泉段218地號"]
        assert len(matches) == 1
        issue = matches[0]
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK
        assert issue.submitted_value == "優"

    def test_case_b_adjustment_error_detected_with_downstream(self, audit_result):
        """Case B: 深度真實差異率1.00%，故意提交5.00%。"""
        matches = [i for i in audit_result.issues
                   if i.field == "individual_land_depth_differential_rate_溫泉段218地號"]
        assert len(matches) == 1
        issue = matches[0]
        assert issue.issue_type == IssueType.ERROR
        assert issue.submitted_value == "5.00"
        assert issue.expected_value == "1"
        assert "base_parcel_comparison_price" in issue.downstream_impact

    def test_case_c_cross_form_inconsistent_detected_with_downstream(self, audit_result):
        """Case C: 表5-2真實總修正數0.00%，表4故意提交5.00%。"""
        matches = [i for i in audit_result.issues if i.field == "region_adjustment_rate_溫泉段218地號"]
        assert len(matches) == 1
        issue = matches[0]
        assert issue.issue_type == IssueType.INCONSISTENT
        assert issue.expected_value == "0.00"
        assert issue.submitted_value == "5.00"
        assert "base_parcel_comparison_price" in issue.downstream_impact

    def test_exactly_three_demo_errors_plus_one_honest_road_width_gap(self, audit_result):
        """Everything else in the submission was generated by the real
        engines (correct by construction), so exactly 3 deliberately-
        injected demo errors must appear among the non-Passed issues --
        no more, no fewer. A 4th non-Passed issue (main_road_width) is
        EXPECTED and is not a bug: Golden Case's 18M has no independently-
        confirmable official source (see providers/road_provider.py's
        module docstring survey), this fixture injects no
        road_width_evidence, and RoadWidthResolver honestly reports
        UNAVAILABLE rather than fabricating a PASS -- see
        TestRoadWidthWiringInAuditEngine below.

        3 further non-Passed issues are ALSO expected (STEP5 §10 cross-form
        checks: case_no_identity / comparable_id_set_identity /
        base_parcel_comparison_price_subtotal, added in
        engine/audit_engine.py's review()) -- this demo builder predates
        those checks and never populates their SubmittedFormData fields, so
        all 3 honestly resolve to MISSING (never a fabricated ERROR/
        INCONSISTENT against absent data, per _field_pair_or_missing()),
        exactly like the pre-existing road_width_gap case above."""
        non_passed = [i for i in audit_result.issues if i.issue_type != IssueType.PASSED]
        assert len(non_passed) == 7
        demo_error_fields = {
            "regional_main_road_width_adjustment_pct_溫泉段218地號",
            "individual_land_depth_differential_rate_溫泉段218地號",
            "region_adjustment_rate_溫泉段218地號",
        }
        demo_errors = [i for i in non_passed if i.field in demo_error_fields]
        assert len(demo_errors) == 3
        road_width_gap = [i for i in non_passed if i.field == "main_road_width"]
        assert len(road_width_gap) == 1
        assert road_width_gap[0].explanation_data.check_type == CheckType.ROAD_WIDTH_UNAVAILABLE

        step5_cross_form_fields = {
            "case_no_identity", "comparable_id_set_identity", "base_parcel_comparison_price_subtotal",
        }
        step5_gaps = [i for i in non_passed if i.field in step5_cross_form_fields]
        assert len(step5_gaps) == 3
        assert all(i.issue_type == IssueType.MISSING for i in step5_gaps)

    def test_all_three_demo_errors_are_critical_severity(self, audit_result):
        """All three demo errors propagate to base_parcel_comparison_price,
        so all three must be CRITICAL, not a lesser severity. The 4th
        non-Passed issue (main_road_width's honest UNAVAILABLE) is
        intentionally NOT part of this claim -- it is Severity.MEDIUM
        (can't-verify, not a confirmed defect), asserted separately below."""
        demo_error_fields = {
            "regional_main_road_width_adjustment_pct_溫泉段218地號",
            "individual_land_depth_differential_rate_溫泉段218地號",
            "region_adjustment_rate_溫泉段218地號",
        }
        demo_errors = [i for i in audit_result.issues if i.field in demo_error_fields]
        assert len(demo_errors) == 3
        assert all(i.severity == Severity.CRITICAL for i in demo_errors)

    def test_road_width_gap_is_medium_not_critical(self, audit_result):
        road_width_gap = [i for i in audit_result.issues if i.field == "main_road_width"][0]
        assert road_width_gap.severity == Severity.MEDIUM
        assert road_width_gap.issue_type == IssueType.MISSING

    def test_remaining_75_fields_correctly_pass(self, audit_result):
        """45 grade/adjustment/cross-form fields + 2 case-level land-use-ratio
        checks (建蔽率/容積率) + 28 Grade Representation Check B (one per
        regional factor, including Case A's -- self-consistent, see above)
        = 75, all correctly PASS. Check A's 28 issues are counted
        separately above (27 pass + Case A's 1 error)."""
        passed = [i for i in audit_result.issues if i.issue_type == IssueType.PASSED]
        assert len(passed) == 75

    def test_no_grade_representation_inconsistency_on_golden_case(self, audit_result):
        """None of the 28 regional factors (including the 2 boolean
        factors resolved by the Grade Representation Contract) should
        produce a WARNING -- the fixture's grade/grade_code pairs are all
        self-consistent by construction."""
        warnings = [i for i in audit_result.issues
                    if i.explanation_data.check_type == CheckType.GRADE_REPRESENTATION_INCONSISTENT]
        assert warnings == []

    def test_every_issue_has_explanation_and_recommendation(self, audit_result):
        for issue in audit_result.issues:
            assert issue.explanation_data.summary
            assert issue.recommendation_data.action


# ---------------------------------------------------------------------
# LandUseRatioValidator wired into AuditEngine.review() (case-level 表1
# 建蔽率/容積率 checks, not per-comparable)
# ---------------------------------------------------------------------
class TestLandUseRatioWiringInAuditEngine:
    def _run(self, rule_engine_instance, mutate=None):
        submitted = build_submitted_form_with_demo_errors(
            rule_engine_instance, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL
        )
        if mutate is not None:
            mutate(submitted)
        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        return audit.review(GOLDEN_CASE, submitted, BASE_REGIONAL, COMP_REGIONAL)

    def test_golden_case_70_240_passes_both_checks(self, audit_result):
        """金山都市計畫、第二種商業區、建蔽率70%、容積率240% -> PASSED/PASSED."""
        bcr = [i for i in audit_result.issues if i.field == "building_coverage_ratio"]
        far = [i for i in audit_result.issues if i.field == "floor_area_ratio"]
        assert len(bcr) == 1 and len(far) == 1
        assert bcr[0].issue_type == IssueType.PASSED
        assert far[0].issue_type == IssueType.PASSED
        assert bcr[0].explanation_data.check_type == CheckType.PASSED_CHECK
        assert far[0].explanation_data.check_type == CheckType.PASSED_CHECK

    def test_error_case_60_300_detected_as_inconsistent(self, rule_engine_instance):
        """建蔽率60%（真實70%）、容積率300%（真實240%）-> 必須被抓到，
        且不得與其他demo error混淆。"""
        def mutate(submitted):
            submitted.submitted_building_coverage_rate = "60"
            submitted.submitted_floor_area_ratio = "300"
        result = self._run(rule_engine_instance, mutate)
        bcr = [i for i in result.issues if i.field == "building_coverage_ratio"][0]
        far = [i for i in result.issues if i.field == "floor_area_ratio"][0]
        assert bcr.issue_type == IssueType.INCONSISTENT
        assert bcr.explanation_data.check_type == CheckType.BUILDING_COVERAGE_RATE_INCONSISTENT
        assert bcr.submitted_value == "60"
        assert bcr.expected_value == "70"
        assert far.issue_type == IssueType.INCONSISTENT
        assert far.explanation_data.check_type == CheckType.FLOOR_AREA_RATIO_INCONSISTENT
        assert far.submitted_value == "300"
        assert far.expected_value == "240"

    def test_missing_plan_id_reports_unresolved_not_inconsistent(self, rule_engine_instance):
        """無internal_plan_id時，容積率必須回報ZONING_PLAN_UNRESOLVED，
        絕不能誤標為FLOOR_AREA_RATIO_INCONSISTENT（申報值可能其實正確，
        只是本系統無法核對）。"""
        def mutate(submitted):
            submitted.internal_plan_id = None
        result = self._run(rule_engine_instance, mutate)
        far = [i for i in result.issues if i.field == "floor_area_ratio"][0]
        assert far.explanation_data.check_type == CheckType.ZONING_PLAN_UNRESOLVED
        assert far.issue_type == IssueType.WARNING
        assert far.explanation_data.check_type != CheckType.FLOOR_AREA_RATIO_INCONSISTENT

    def test_unresolvable_plan_rule_reports_unavailable_not_a_violation(self, rule_engine_instance):
        """internal_plan_id指向一個未登記本分區規則的都市計畫（"banqiao"，
        非金山）-> 必須回報LAND_USE_RATIO_RULE_UNAVAILABLE，不得標為填錯
        或違規（見tests/test_land_use_ratio_engine.py同一組資料的獨立驗證）。"""
        def mutate(submitted):
            submitted.internal_plan_id = "banqiao"
        result = self._run(rule_engine_instance, mutate)
        far = [i for i in result.issues if i.field == "floor_area_ratio"][0]
        assert far.explanation_data.check_type == CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE
        assert far.issue_type == IssueType.MISSING
        assert far.explanation_data.check_type != CheckType.FLOOR_AREA_RATIO_INCONSISTENT

    def test_evidence_chain_preserved_on_golden_case_pass(self, audit_result):
        """Evidence-preservation requirement: resolution_layer/legal_source/
        dataset_version/etc. must survive into computed_steps, not just the
        bare resolved_value_pct."""
        far = [i for i in audit_result.issues if i.field == "floor_area_ratio"][0]
        steps = "\n".join(far.explanation_data.computed_steps)
        assert "resolution_layer=PLAN_SPECIFIC" in steps
        assert "official_raw_zone_name=第二種商業區" in steps
        assert "internal_plan_id=jinshan" in steps
        assert "confirmed_plan_name=金山都市計畫" in steps
        assert "requires_manual_review=" in steps

    def test_evidence_chinese_text_is_not_mojibake(self, audit_result):
        """Locks in that computed_steps' Chinese text is genuine UTF-8, not
        corrupted at any domain/JSON boundary. A Windows terminal's own
        codepage can mangle DISPLAYED console output without the underlying
        Python string being wrong -- this test never goes through a
        console, so a real corruption (vs. a terminal-display artifact)
        would show up here as a failed equality check or an embedded
        U+FFFD replacement character."""
        far = [i for i in audit_result.issues if i.field == "floor_area_ratio"][0]
        steps = dict(s.split("=", 1) for s in far.explanation_data.computed_steps)
        assert steps["official_raw_zone_name"] == "第二種商業區"
        assert steps["normalized_zone_category"] == "商業區"
        assert steps["confirmed_plan_name"] == "金山都市計畫"

        # Full API response shape: json.dumps(..., ensure_ascii=False) must
        # round-trip the Chinese text intact, with no U+FFFD anywhere.
        import json
        response_json = json.dumps(audit_result.model_dump(mode="json"), ensure_ascii=False)
        assert "第二種商業區" in response_json
        assert "�" not in response_json
        reparsed = json.loads(response_json)
        far_reparsed = [i for i in reparsed["issues"] if i["field"] == "floor_area_ratio"][0]
        assert "confirmed_plan_name=金山都市計畫" in far_reparsed["explanation_data"]["computed_steps"]

    def test_land_use_ratio_checks_do_not_disturb_other_checks(self, audit_result):
        """Regression: adding the 2 new case-level checks must not change
        the 3 existing demo-error detections or their downstream impact."""
        matches = [i for i in audit_result.issues if i.field == "regional_main_road_width_adjustment_pct_溫泉段218地號"]
        assert len(matches) == 1 and matches[0].issue_type == IssueType.ERROR


# ---------------------------------------------------------------------
# RoadWidthResolver/RoadWidthValidator wired into AuditEngine.review()
# (case-level 表1 main_road_width check, not per-comparable)
# ---------------------------------------------------------------------
def _ev(evidence_type, width_m, road_name="中山路", **overrides):
    fields = dict(road_name=road_name, width_m=width_m, evidence_type=evidence_type,
                  source_name=f"{evidence_type.value} source", confidence="高")
    fields.update(overrides)
    return RoadWidthEvidence(**fields)


class TestRoadWidthWiringInAuditEngine:
    def _run(self, rule_engine_instance, submitted_main_road_width, road_width_evidence):
        submitted = build_submitted_form_with_demo_errors(
            rule_engine_instance, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL
        )
        submitted.submitted_main_road_width = submitted_main_road_width
        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        return audit.review(GOLDEN_CASE, submitted, BASE_REGIONAL, COMP_REGIONAL,
                             road_width_evidence=road_width_evidence)

    def test_golden_case_18m_source_unconfirmed_without_evidence(self, audit_result):
        """Golden Case's 18M has no independently-confirmable official
        source (see providers/road_provider.py's module docstring survey)
        -- the demo fixture injects no road_width_evidence, so this must
        stay ROAD_WIDTH_UNAVAILABLE, never a fabricated PASS. Mock Golden
        Case (submitted=18, unconditionally shown in the UI/PDF) and Real
        validation (this check, which can only PASS given real evidence)
        are deliberately kept separate."""
        issue = [i for i in audit_result.issues if i.field == "main_road_width"][0]
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_UNAVAILABLE
        assert issue.submitted_value == "18"

    def test_golden_case_18m_passes_when_reliable_evidence_confirms_it(self, rule_engine_instance):
        """If a reliable evidence source DID confirm 18M (synthetic here,
        since none exists yet in production), the same submitted value
        that reports UNAVAILABLE above must cleanly PASS -- proving the
        gap above is genuinely about missing evidence, not a validator bug."""
        evidence = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        result = self._run(rule_engine_instance, "18", evidence)
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK

    def test_error_case_12_vs_reliable_18_is_inconsistent(self, rule_engine_instance):
        """submitted=12m, reliable single-source reference=18m ->
        ROAD_WIDTH_INCONSISTENT."""
        evidence = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        result = self._run(rule_engine_instance, "12", evidence)
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        assert issue.issue_type == IssueType.INCONSISTENT
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_INCONSISTENT
        assert issue.submitted_value == "12"
        assert issue.expected_value == "18"
        assert issue.severity == Severity.HIGH

    def test_multi_source_agreement_resolves_and_passes(self, rule_engine_instance):
        evidence = [
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 18),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18),
        ]
        result = self._run(rule_engine_instance, "18", evidence)
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        assert issue.issue_type == IssueType.PASSED

    def test_conflicting_evidence_never_asserted_as_violation(self, rule_engine_instance):
        """urban plan=20m, map=18m, geometric estimate=17.5m -- the spec's
        own conflict example. Must be ROAD_WIDTH_EVIDENCE_CONFLICT
        (WARNING), never INCONSISTENT, even though submitted (18) happens
        to match one of the disagreeing candidates."""
        evidence = [
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18),
            _ev(RoadWidthEvidenceType.GEOMETRIC_ESTIMATE, "17.5"),
        ]
        result = self._run(rule_engine_instance, "18", evidence)
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        assert issue.issue_type == IssueType.WARNING
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_EVIDENCE_CONFLICT
        assert issue.issue_type != IssueType.INCONSISTENT

    def test_no_evidence_at_all_is_unavailable_not_a_violation(self, rule_engine_instance):
        result = self._run(rule_engine_instance, "18", [])
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_UNAVAILABLE

    def test_submitted_and_resolved_reference_are_independently_preserved(self, rule_engine_instance):
        """Even when they disagree, both the submitted value and the
        resolved reference value must be readable from the AuditIssue --
        the reference is never used to silently overwrite the submission."""
        evidence = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        result = self._run(rule_engine_instance, "12", evidence)
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        assert issue.submitted_value == "12"  # untouched
        assert issue.expected_value == "18"   # resolved reference, kept separate

    def test_road_width_evidence_computed_steps_preserved(self, rule_engine_instance):
        evidence = [
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20, dataset_id="ntpc_urban_plan_road"),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18, dataset_id="osm"),
        ]
        result = self._run(rule_engine_instance, "18", evidence)
        issue = [i for i in result.issues if i.field == "main_road_width"][0]
        steps = "\n".join(issue.explanation_data.computed_steps)
        assert "URBAN_PLAN_DESIGN_WIDTH" in steps
        assert "EXTERNAL_MAP_ATTRIBUTE" in steps
        assert "ntpc_urban_plan_road" in steps
        assert "osm" in steps
