# -*- coding: utf-8 -*-
"""
AuditEngine — top-level Smart Review orchestrator. Given a CompetitionCase
(the true structured input) and a "submitted form" (what a filled-in PDF
actually contains -- via Mock Extracted Data, see docs/phase6/
document_extraction_spec.md for why Textract was not used), runs
RuleValidator + CalculationValidator + CrossFormValidationEngine across
every factor and produces a complete ReviewResult.

No LLM anywhere in this module.
"""
from __future__ import annotations
import sys
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    CompetitionCase, AuditIssue, ReviewResult, CheckType, Severity, IssueType,
    ExplanationData, RecommendationData,
)


def _d0(value) -> "Decimal | None":
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
from engine.rule_validator import RuleValidator  # noqa: E402
from engine.calculation_validator import CalculationValidator  # noqa: E402
from engine.cross_form_validation_engine import CrossFormValidationEngine  # noqa: E402
from engine.dependency_impact_analyzer import DependencyImpactAnalyzer  # noqa: E402
from engine.land_use_ratio_validator import LandUseRatioValidator  # noqa: E402
from engine.road_width_resolver import RoadWidthResolver  # noqa: E402
from engine.road_width_validator import RoadWidthValidator  # noqa: E402


class SubmittedFormData:
    """Represents what was actually extracted from a filled-in form
    (Mock Extracted Data, per Phase 6 instructions -- see
    docs/phase6/document_extraction_spec.md). Structurally mirrors the
    CompetitionCase's factors but carries whatever grades/adjustments/totals
    were ACTUALLY WRITTEN on the form, which may differ from what the
    engines would compute (that discrepancy is exactly what Smart Review
    detects)."""

    def __init__(self, case_no: str):
        self.case_no = case_no
        # submitted_grades[(field_id, comparable_id)] = grade TEXT, for 表5-2/表4 factors
        self.submitted_grades: Dict[tuple, str] = {}
        # submitted_grade_codes[(field_id, comparable_id)] = grade CODE ("1".."5"),
        # 表5-2's BASE 優劣等級 column code cell -- independent of submitted_grades
        # (text); see Grade Representation Contract (Phase D). Never derived FROM
        # submitted_grades or vice versa -- both come straight from extraction.
        self.submitted_grade_codes: Dict[tuple, str] = {}
        # submitted_adjustments[(field_id, comparable_id)] = adjustment pct (str/number)
        self.submitted_adjustments: Dict[tuple, Any] = {}
        # submitted_totals[key] = value, for aggregate fields
        self.submitted_totals: Dict[str, Any] = {}

        # ---- 表1 land-use-ratio fields (optional, backward-compatible) ----
        # What the submitted form states, independent of whichever zone/plan
        # the system itself resolves from official sources.
        self.submitted_land_use_zone: Any = None
        self.submitted_building_coverage_rate: Any = None
        self.submitted_floor_area_ratio: Any = None
        # confirmed_plan_name/internal_plan_id/plan_identification_source are
        # always human-supplied (see providers/base.py ProviderContext.plan_id
        # docstring: the NTPC plan-boundary shapefile's name field is
        # corrupted at the source, so plan_id can never be auto-inferred from
        # coordinates). plan_identification_source records how the value was
        # obtained, e.g. "MANUAL_INPUT".
        self.confirmed_plan_name: Any = None
        self.internal_plan_id: Any = None
        self.plan_identification_source: Any = None

        # 表1 main_road_width (optional, backward-compatible) -- the
        # SUBMITTED value, compared by RoadWidthValidator against whatever
        # RoadWidthResolver resolves from the road_width_evidence passed
        # into AuditEngine.review(), never against this field itself.
        self.submitted_main_road_width: Any = None

        # ---- STEP 5 §10 cross-form identity/subtotal fields (optional,
        # backward-compatible -- all default to None/empty so existing
        # callers that never set them simply get IssueType.MISSING for
        # these specific checks, same convention as every other optional
        # field above) ----
        # case identity: two INDEPENDENTLY-populated case_no reads --
        # form_case_no comes from the already-stored FORM_COMPLETION
        # record's own stamped case_no (written by FormCompletionEngine at
        # complete_form time); AuditEngine.review()'s `case.case_no`
        # argument is reconstructed fresh from the case META record at
        # review time. A mismatch means the two records disagree about
        # which case they belong to.
        self.submitted_case_no: Any = None
        # comparable identity: the comparable_id SET actually present in
        # 表5-2's own field namespace (regional_total_adjustment_{cid})
        # versus 表4's own field namespace (region_adjustment_rate_{cid});
        # these are two independently-emitted sets of field_ids, not one
        # list read twice -- a comparable dropped from one table's fields
        # while still present in the other's is a real integration defect.
        self.submitted_comparable_ids_table5_2: Any = None  # sorted comma-joined str, or None
        self.submitted_comparable_ids_table4: Any = None
        # price/subtotal/final trial price: per-comparable 表4 trial_price
        # + comparable_weight fields (each its own independently-stored
        # FieldCompletion) versus the final base_parcel_comparison_price
        # field's own stored RAW (pre-rounding) result -- checks that the
        # final weighted total is actually internally consistent with the
        # subtotals it claims to be built from.
        self.submitted_trial_prices: Dict[str, Any] = {}
        self.submitted_comparable_weights: Dict[str, Any] = {}
        self.submitted_base_parcel_comparison_price_raw: Any = None


class AuditEngine:
    def __init__(self, rule_engine, dependency_graph_path: str,
                 land_use_ratio_validator: "LandUseRatioValidator | None" = None,
                 road_width_resolver: "RoadWidthResolver | None" = None,
                 road_width_validator: "RoadWidthValidator | None" = None):
        self._rule_engine = rule_engine
        self._dep = DependencyImpactAnalyzer(dependency_graph_path)
        self._rule_validator = RuleValidator(rule_engine, self._dep)
        self._calc_validator = CalculationValidator(rule_engine, self._dep)
        self._cross_form = CrossFormValidationEngine(self._dep)
        self._land_use_ratio_validator = land_use_ratio_validator or LandUseRatioValidator()
        self._road_width_resolver = road_width_resolver or RoadWidthResolver()
        self._road_width_validator = road_width_validator or RoadWidthValidator()
        self._issue_counter = 0

    def _next_issue_id(self) -> str:
        self._issue_counter += 1
        return f"ISS-{self._issue_counter:04d}"

    def review(
        self, case: CompetitionCase, submitted: SubmittedFormData,
        base_regional_factors: List, comparable_regional_factors: Dict[str, List],
        road_width_evidence: "List | None" = None,
    ) -> ReviewResult:
        issues: List[AuditIssue] = []

        for comparable_id in case.comparable_ids:
            issues.extend(self._review_individual_factors(case, comparable_id, submitted))
            issues.extend(self._review_regional_factors(
                case, comparable_id, submitted, base_regional_factors,
                comparable_regional_factors.get(comparable_id, []),
            ))
            issues.append(self._review_cross_form_regional_total(case, comparable_id, submitted))

        # 表1 land-use-ratio checks are case-level (one submitted 建蔽率/容積率
        # per case), not per-comparable, so they run once here.
        official_zone = self._official_regional_raw_value(base_regional_factors, "regional_land_use_zone")
        issues.append(self._land_use_ratio_validator.validate_building_coverage_rate(
            self._next_issue_id(), official_zone, submitted.submitted_building_coverage_rate,
        ))
        issues.append(self._land_use_ratio_validator.validate_floor_area_ratio(
            self._next_issue_id(), official_zone, submitted.internal_plan_id,
            submitted.submitted_floor_area_ratio,
            confirmed_plan_name=submitted.confirmed_plan_name,
            plan_identification_source=submitted.plan_identification_source,
        ))

        # 主要道路寬度 (also case-level, 表1 field): resolved from
        # independently-sourced road_width_evidence (RealRoadProvider ->
        # RoadWidthResolver), never from submitted.submitted_main_road_width
        # itself -- see engine/road_width_resolver.py.
        road_width_resolution = self._road_width_resolver.resolve(road_width_evidence or [])
        issues.append(self._road_width_validator.validate_main_road_width(
            self._next_issue_id(), road_width_resolution, submitted.submitted_main_road_width,
        ))

        # ---- STEP 5 §10 case-level cross-form checks (case identity,
        # comparable identity, price/subtotal/final trial price). All three
        # are OPTIONAL SubmittedFormData fields (default None/empty) so a
        # caller that predates STEP5 (or the DOCUMENT submission path,
        # which does not yet populate them) gets an honest MISSING issue
        # here, never a fabricated INCONSISTENT from comparing a real value
        # against an absent one (Three-State Verification Outcome
        # Semantics -- "cannot verify" must never look like "confirmed
        # mismatch").
        issues.append(self._field_pair_or_missing(
            source_form="表4+表5-2", field_id="case_no_identity", label="案件編號（跨表一致性）",
            value_a=case.case_no, value_b=submitted.submitted_case_no,
            rule_citation="STEP5 §10 case identity",
        ))
        issues.append(self._field_pair_or_missing(
            source_form="表4", field_id="comparable_id_set_identity",
            label="比較標的清單（表5-2 vs 表4 一致性）",
            value_a=submitted.submitted_comparable_ids_table5_2,
            value_b=submitted.submitted_comparable_ids_table4,
            rule_citation="STEP5 §10 comparable identity",
        ))
        issues.append(self._review_final_trial_price_subtotal(submitted))

        return ReviewResult(
            case_no=case.case_no, issues=issues, generated_at=datetime.now(),
            engine_versions={"audit_engine": "1.0", "rule_validator": "1.0",
                              "calculation_validator": "1.0", "cross_form_validation_engine": "1.0"},
        )

    def _review_individual_factors(self, case, comparable_id, submitted) -> List[AuditIssue]:
        issues = []
        base_by_field = {f.field_id: f for f in case.base_parcel_factors}
        comp_by_field = {f.field_id: f for f in case.comparable_factors.get(comparable_id, [])}
        for field_id in sorted(set(base_by_field) & set(comp_by_field)):
            base_input = base_by_field[field_id]
            comp_input = comp_by_field[field_id]
            full_field_id = f"{field_id}_differential_rate_{comparable_id}"

            try:
                base_grade_result = self._rule_engine.grade(
                    case.city, case.district, case.land_use_type, base_input.factor,
                    base_input.raw_value, unit=base_input.unit, rule_set="individual",
                )
                comp_grade_result = self._rule_engine.grade(
                    case.city, case.district, case.land_use_type, comp_input.factor,
                    comp_input.raw_value, unit=comp_input.unit, rule_set="individual",
                )
            except Exception:
                continue  # grade-level errors already surfaced via validate_grade elsewhere; skip adjustment check if grading itself fails

            submitted_adj = submitted.submitted_adjustments.get((field_id, comparable_id))
            issues.append(self._calc_validator.validate_adjustment(
                issue_id=self._next_issue_id(), source_form="表4", field_id=full_field_id,
                label=f"{base_input.factor}差異率", factor=base_input.factor,
                base_grade_code=base_grade_result.grade_code, base_grade=base_grade_result.grade,
                comp_grade_code=comp_grade_result.grade_code, comp_grade=comp_grade_result.grade,
                matrix_rule_id=base_grade_result.rule_id,
                matrix=base_grade_result.matched_rule["adjustment_matrix"],
                submitted_adjustment=submitted_adj,
            ))
        return issues

    def _review_regional_factors(self, case, comparable_id, submitted,
                                  base_regional_factors, comparable_regional_factors) -> List[AuditIssue]:
        """Two orthogonal checks per factor (Grade Representation Contract,
        Phase D) -- Check A (grade identity, code vs code) drives the
        valuation-critical GRADE_ERROR/PASSED verdict; Check B (grade
        representation, code+text self-consistency) is a SEPARATE, lower-
        severity WARNING-class signal. Neither normalizes submitted into
        expected before comparing; both read submitted_grade_codes/
        submitted_grades independently."""
        issues = []
        base_by_field = {f.field_id: f for f in base_regional_factors}
        comp_by_field = {f.field_id: f for f in comparable_regional_factors}
        for field_id in sorted(set(base_by_field) & set(comp_by_field)):
            base_input = base_by_field[field_id]
            identity_field_id = f"{field_id}_adjustment_pct_{comparable_id}"
            representation_field_id = f"{field_id}_grade_representation_{comparable_id}"

            submitted_grade_code = submitted.submitted_grade_codes.get((field_id, comparable_id))
            submitted_grade_text = submitted.submitted_grades.get((field_id, comparable_id))

            issues.append(self._rule_validator.validate_grade_identity(
                issue_id=self._next_issue_id(), source_form="表5-2", field_id=identity_field_id,
                label=f"{base_input.factor}優劣等級", city=case.city, district=case.district,
                land_use_type=case.land_use_type, factor=base_input.factor,
                raw_value=base_input.raw_value, unit=base_input.unit, rule_set="regional",
                submitted_grade_code=submitted_grade_code,
            ))
            issues.append(self._rule_validator.validate_grade_representation(
                issue_id=self._next_issue_id(), source_form="表5-2", field_id=representation_field_id,
                label=f"{base_input.factor}優劣等級文字表述", canonical_field_id=field_id,
                city=case.city, district=case.district, land_use_type=case.land_use_type,
                factor=base_input.factor, rule_set="regional",
                submitted_grade_code=submitted_grade_code, submitted_grade_text=submitted_grade_text,
            ))
        return issues

    @staticmethod
    def _official_regional_raw_value(base_regional_factors, field_id: str):
        """Looks up the TRUE (not submitted) raw_value for a case-level
        regional factor, e.g. the officially-resolved zone name backing the
        建蔽率/容積率 checks -- must come from the independently-sourced
        base_regional_factors, never from the submitted form itself, or a
        submission that lies about its own zone would trivially self-validate."""
        for fi in base_regional_factors:
            if fi.field_id == field_id:
                return fi.raw_value
        return None

    def _field_pair_or_missing(
        self, source_form: str, field_id: str, label: str, value_a, value_b, rule_citation: str,
    ) -> AuditIssue:
        """Wraps CrossFormValidationEngine.validate_field_pair() with a
        MISSING short-circuit when either side is None -- that generic
        comparator has no concept of "not yet populated" (it stringifies
        whatever it's given), so an optional field neither side has filled
        in yet must never reach it directly, or "None" == "None" (both
        sides absent) would read as a spurious PASSED, and one side absent
        would read as a spurious INCONSISTENT."""
        if value_a is None or value_b is None:
            return AuditIssue(
                issue_id=self._next_issue_id(), severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form=source_form, field=field_id, label=label,
                submitted_value=value_b, expected_value=value_a, rule_id=None,
                source="CrossFormValidationEngine",
                explanation_data=ExplanationData(
                    summary=f"{label} 缺少可比對之兩方資料，無法執行跨表一致性檢查", check_type=CheckType.MISSING,
                ),
                recommendation_data=RecommendationData(action="請確認雙方欄位皆已填寫", requires_human_review=True),
                downstream_impact=[],
            )
        return self._cross_form.validate_field_pair(
            issue_id=self._next_issue_id(), source_form=source_form, field_id=field_id, label=label,
            value_a=value_a, value_b=value_b, rule_citation=rule_citation,
        )

    def _review_final_trial_price_subtotal(self, submitted) -> AuditIssue:
        """STEP5 §10 price/subtotal/final trial price check: the final
        比準地比較價格 (base_parcel_comparison_price) must equal
        sum(trial_price[cid] * weight[cid] / 100) over the SAME per-
        comparable fields FormCompletionEngine separately stored earlier
        in its own pipeline (trial_price_{cid} / comparable_weight_{cid}) --
        reusing CalculationValidator.validate_sum() (already used for the
        regional/individual subtotal checks), never a second calculator."""
        cids = sorted(set(submitted.submitted_trial_prices) & set(submitted.submitted_comparable_weights))
        components = []
        for cid in cids:
            tp = _d0(submitted.submitted_trial_prices.get(cid))
            wt = _d0(submitted.submitted_comparable_weights.get(cid))
            if tp is not None and wt is not None:
                components.append(tp * wt / Decimal("100"))
        return self._calc_validator.validate_sum(
            issue_id=self._next_issue_id(), source_form="表4", field_id="base_parcel_comparison_price_subtotal",
            label="比準地比較價格（加權合計，跨表一致性）", component_values=components,
            submitted_total=submitted.submitted_base_parcel_comparison_price_raw,
            check_type=CheckType.CROSS_FORM_INCONSISTENT,
        )

    def _review_cross_form_regional_total(self, case, comparable_id, submitted) -> AuditIssue:
        table5_2_total = submitted.submitted_totals.get(f"regional_total_{comparable_id}")
        table4_rate = submitted.submitted_totals.get(f"region_adjustment_rate_{comparable_id}")
        return self._cross_form.validate_regional_total_matches_table4(
            issue_id=self._next_issue_id(), comparable_id=comparable_id,
            table5_2_regional_total=table5_2_total, table4_region_adjustment_rate=table4_rate,
        )
