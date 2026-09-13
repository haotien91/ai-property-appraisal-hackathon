# -*- coding: utf-8 -*-
"""
table4_analysis_engine.py — TABLE4-THREE-COMPARABLE-D1.

Builds a domain.models.Table4Analysis (表4 比較法調查估價表) for one
Competition case with N segment-scoped comparables, entirely by CALLING
the existing, unmodified GradeEngine/AdjustmentEngine/CalculationEngine/
ComparableSelectionEngine -- exactly the same reuse discipline
engine/table51_analysis_engine.py already established for 表5-1. This
module adds NO grading or calculation logic of its own.

Per-comparable, independent pipeline (never averaged, never a
comparable_ids[0] shortcut -- callers pass an explicit ordered list):

  1. 20 individual factor slots (19 standard-matrix + FAR special policy,
     via engine/individual_factor_catalog.py) -- same generic missing/
     manual-review handling as Table51AnalysisEngine.build_comparison():
     a factor absent from either side, or one GradeEngine/AdjustmentEngine
     cannot resolve, becomes an honest MANUAL_REVIEW_REQUIRED row, never a
     fabricated grade or a silently-dropped slot. FAR (individual_
     floor_area_ratio) has NO rule record at all (Shulin A2's deliberate
     special policy) -- grading it always raises RuleNotFoundError ->
     GradeEngineError -> MANUAL_REVIEW_REQUIRED, which is the CORRECT
     outcome, never patched over with a fake matrix or a fabricated 0%.
  2. individual_adjustment_total_pct via CalculationEngine.
     individual_adjustment_total() -- fails closed (None) if ANY of the 20
     slots requires manual review, never a partial sum that silently
     excludes the unresolved ones.
  3. regional_adjustment_pct is BRIDGED from a caller-supplied
     Table51Comparison for this SAME comparable_segment_code -- this
     module never recomputes regional adjustment itself, and never
     accepts a comparison for the wrong comparable (see build_comparison's
     own assertion).
  4. trial_price/adjustment_abs_sum via CalculationEngine, only when every
     required input resolved -- otherwise MANUAL_REVIEW_REQUIRED, no
     fabricated partial calculation.
  5. weight is ALWAYS either MANUAL_REVIEW_REQUIRED (no confirmed value
     supplied) or the caller-confirmed value -- this module NEVER computes
     a final weighted base_parcel_comparison_price on its own (不動產估價
     技術規則§27 has no formula for that -- see engine/comparable_
     selection_engine.py's own docstring); a SYSTEM_AUXILIARY weight
     SUGGESTION (via ComparableSelectionEngine.suggest_weights(), reused
     unmodified) may be attached for reference, always tagged non-
     statutory and requiring human confirmation.
"""
from __future__ import annotations

import sys
import os
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from grade_engine import GradeEngine, GradeEngineError  # noqa: E402
from adjustment_engine import AdjustmentEngine, AdjustmentEngineError  # noqa: E402
from calculation_engine import CalculationEngine, CalculationEngineError  # noqa: E402
from individual_factor_catalog import build_individual_factor_catalog, FAR_FIELD_ID  # noqa: E402
from regional_factor_value_normalization import normalize_regional_factor_value  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    FactorInput, PartyRole, FieldStatus, AdjustmentResult,
    Table4Analysis, Table4Comparison, Table4FactorResult, Table4WeightStatus,
    Table51Comparison,
)


def _decimalize_transaction(transaction: dict) -> dict:
    """`transaction` arrives as a plain JSON-transported dict (see
    data/competition_cases/shulin_residential_2026/segment_table4_
    fixtures.py's own docstring on why it deliberately avoids Decimal) --
    converts the two numeric fields CalculationEngine actually needs to
    Decimal via str() first, matching engine/calculation_engine.py's own
    _d() convention (never via float(), to avoid binary floating-point
    error)."""
    from decimal import Decimal
    out = dict(transaction)
    for key in ("land_normal_price_raw", "price_date_adjustment_pct_raw", "adjusted_price_raw"):
        if out.get(key) is not None and not isinstance(out[key], Decimal):
            out[key] = Decimal(str(out[key]))
    return out


def extract_individual_rule_records(rule_engine) -> List[dict]:
    """Individual-scope counterpart of engine/table51_analysis_engine.py's
    extract_regional_rule_records() -- same internal-index flattening
    convention (matching engine/adjustment_engine.py's own
    _lookup_matched_rule() pattern), scoped to "IND-*" rule_id prefixes."""
    seen = {}
    for records in rule_engine._index.values():
        for r in records:
            if r.get("rule_id", "").startswith("IND-"):
                seen[r["rule_id"]] = r
    return list(seen.values())


class Table4AnalysisEngine:
    def __init__(self, grade_engine: GradeEngine, adjustment_engine: AdjustmentEngine,
                 calculation_engine: CalculationEngine, individual_rule_records: List[dict],
                 allow_partial: bool = False):
        self._grade = grade_engine
        self._adjustment = adjustment_engine
        self._calc = calculation_engine
        self._catalog = build_individual_factor_catalog(individual_rule_records)
        # Opt-in draft mode for the public-data auto-fill pipeline. FAR has no
        # rule record, so a strict comparison can never produce a total.
        self._allow_partial = allow_partial

    @property
    def factor_count(self) -> int:
        return len(self._catalog)

    def _build_individual_factor_results(
        self, city: str, base_district: str, base_land_use_type: str, base_segment_code: str,
        comparable_district: str, comparable_land_use_type: str, comparable_segment_code: str,
        base_individual_factors: List[FactorInput], comparable_individual_factors: List[FactorInput],
    ) -> Tuple[List[Table4FactorResult], List[AdjustmentResult]]:
        base_by_field = {f.field_id: f for f in base_individual_factors}
        comp_by_field = {f.field_id: f for f in comparable_individual_factors}

        results: List[Table4FactorResult] = []
        adjustments: List[AdjustmentResult] = []
        for field_id, factor_name in self._catalog.items():
            is_far = field_id == FAR_FIELD_ID
            base_input = base_by_field.get(field_id)
            comp_input = comp_by_field.get(field_id)

            base_source_type = base_input.evidence.source_type.value if base_input else None
            base_source = base_input.evidence.source if base_input else None
            comp_source_type = comp_input.evidence.source_type.value if comp_input else None
            comp_source = comp_input.evidence.source if comp_input else None

            if base_input is None or comp_input is None:
                missing = []
                if base_input is None:
                    missing.append(f"比準地({base_segment_code})")
                if comp_input is None:
                    missing.append(f"比較標的({comparable_segment_code})")
                reason = (
                    f"容積率差異須以土地開發分析法進行試算調整（評價基準明細表.pdf p.8），"
                    f"本系統無標準矩陣，一律 MANUAL_REVIEW_REQUIRED"
                    if is_far else f"缺值：{'/'.join(missing)} 尚無此因素資料，無法判定等級"
                )
                results.append(Table4FactorResult(
                    field_id=field_id, factor_name=factor_name, is_far_special_policy=is_far,
                    base_segment_code=base_segment_code, comparable_segment_code=comparable_segment_code,
                    base_raw_value=base_input.raw_value if base_input else None,
                    base_source_type=base_source_type, base_source=base_source,
                    comparable_raw_value=comp_input.raw_value if comp_input else None,
                    comparable_source_type=comp_source_type, comparable_source=comp_source,
                    status=FieldStatus.MANUAL_REVIEW_REQUIRED, requires_manual_review=True,
                    reason=reason,
                ))
                continue

            base_eval_value, base_norm_reason, base_mapping_source = normalize_regional_factor_value(
                field_id, base_input.raw_value)
            comp_eval_value, comp_norm_reason, comp_mapping_source = normalize_regional_factor_value(
                field_id, comp_input.raw_value)
            base_for_grading = (
                base_input.model_copy(update={"raw_value": base_eval_value}) if base_eval_value is not None else base_input
            )
            comp_for_grading = (
                comp_input.model_copy(update={"raw_value": comp_eval_value}) if comp_eval_value is not None else comp_input
            )

            try:
                base_grade = self._grade.grade_factor(
                    city, base_district, base_land_use_type, field_id, base_for_grading,
                    PartyRole.BASE_PARCEL, base_segment_code, rule_set="individual",
                )
                comp_grade = self._grade.grade_factor(
                    city, comparable_district, comparable_land_use_type, field_id, comp_for_grading,
                    PartyRole.COMPARABLE, comparable_segment_code, rule_set="individual",
                )
                adj = self._adjustment.compute_adjustment(base_grade, comp_grade)
                adjustments.append(adj)
                results.append(Table4FactorResult(
                    field_id=field_id, factor_name=factor_name, is_far_special_policy=is_far,
                    base_segment_code=base_segment_code, comparable_segment_code=comparable_segment_code,
                    base_raw_value=base_input.raw_value, base_evaluation_value=base_eval_value,
                    base_source_type=base_source_type, base_source=base_source,
                    comparable_raw_value=comp_input.raw_value, comparable_evaluation_value=comp_eval_value,
                    comparable_source_type=comp_source_type, comparable_source=comp_source,
                    normalization_reason=base_norm_reason or comp_norm_reason,
                    mapping_source=base_mapping_source or comp_mapping_source,
                    base_grade=base_grade.rule_result.grade, comparable_grade=comp_grade.rule_result.grade,
                    adjustment_pct=adj.adjustment_pct, rule_id=adj.rule_id,
                    rule_source_document=base_grade.rule_result.source_document,
                    rule_source_page=base_grade.rule_result.source_page,
                    status=FieldStatus.COMPLETED, requires_manual_review=False,
                ))
            except (GradeEngineError, AdjustmentEngineError) as e:
                reason = (
                    f"容積率無標準評分矩陣（評價基準明細表.pdf p.8：容積率差異須以土地開發分析法"
                    f"進行試算調整，並與區域因素容積率併同考量），一律 MANUAL_REVIEW_REQUIRED，"
                    f"不得改用區域因素容積率矩陣、不得預設0%: {e}"
                    if is_far else str(e)
                )
                results.append(Table4FactorResult(
                    field_id=field_id, factor_name=factor_name, is_far_special_policy=is_far,
                    base_segment_code=base_segment_code, comparable_segment_code=comparable_segment_code,
                    base_raw_value=base_input.raw_value, base_evaluation_value=base_eval_value,
                    base_source_type=base_source_type, base_source=base_source,
                    comparable_raw_value=comp_input.raw_value, comparable_evaluation_value=comp_eval_value,
                    comparable_source_type=comp_source_type, comparable_source=comp_source,
                    normalization_reason=base_norm_reason or comp_norm_reason,
                    mapping_source=base_mapping_source or comp_mapping_source,
                    status=FieldStatus.MANUAL_REVIEW_REQUIRED, requires_manual_review=True,
                    reason=reason,
                ))
        return results, adjustments

    def build_comparison(
        self, city: str,
        base_district: str, base_land_use_type: str, base_segment_code: str,
        comparable_district: str, comparable_land_use_type: str,
        comparable_segment_code: str, comparison_index: int,
        base_individual_factors: List[FactorInput], comparable_individual_factors: List[FactorInput],
        transaction: dict,
        regional_comparison: Table51Comparison,
    ) -> Table4Comparison:
        """`transaction`: {"transaction_date_raw", "land_normal_price_raw",
        "price_date_adjustment_pct_raw", "adjusted_price_raw",
        "source_type", "source"} -- all COMPETITION_PROVIDED_FIXED values
        read verbatim from 題目.pdf, never recomputed here (Task 2).

        `regional_comparison`: the Table51Comparison for THIS EXACT
        comparable_segment_code (Task 4's bridge) -- asserted to match, so
        a caller can never accidentally pass a different comparable's
        Table5-1 result."""
        if regional_comparison.comparable_segment_code != comparable_segment_code:
            raise ValueError(
                f"Table5-1 bridge mismatch: build_comparison() for {comparable_segment_code!r} "
                f"was given a Table51Comparison for {regional_comparison.comparable_segment_code!r} instead -- "
                f"this must always be THIS comparable's own Table5-1 result, never another's."
            )
        transaction = _decimalize_transaction(transaction)

        factor_results, adjustments = self._build_individual_factor_results(
            city, base_district, base_land_use_type, base_segment_code,
            comparable_district, comparable_land_use_type, comparable_segment_code,
            base_individual_factors, comparable_individual_factors,
        )
        any_individual_manual_review = any(r.requires_manual_review for r in factor_results)
        excluded = [r.field_id for r in factor_results if r.requires_manual_review] if self._allow_partial else []
        if any_individual_manual_review and not (self._allow_partial and adjustments):
            individual_total = None
        else:
            individual_total = self._calc.individual_adjustment_total(adjustments).raw_result

        regional_pct = regional_comparison.total_adjustment_pct
        regional_manual_review = regional_comparison.requires_manual_review

        adjusted_price = transaction.get("adjusted_price_raw")
        can_compute_trial_price = (
            adjusted_price is not None and regional_pct is not None and individual_total is not None
        )
        trial_price = None
        if can_compute_trial_price:
            trial_price = self._calc.trial_price(adjusted_price, regional_pct, individual_total).raw_result

        adjustment_abs_sum = None
        price_date_rate = transaction.get("price_date_adjustment_pct_raw")
        if price_date_rate is not None and regional_pct is not None and individual_total is not None:
            adjustment_abs_sum = self._calc.adjustment_abs_sum(price_date_rate, regional_pct, individual_total).raw_result

        overall_manual_review = (
            any_individual_manual_review or regional_manual_review
            or adjusted_price is None or trial_price is None
        )

        reason = None
        if overall_manual_review:
            reasons = []
            if any_individual_manual_review:
                reasons.append("個別因素存在未解析項目")
            if regional_manual_review:
                reasons.append("Table5-1 區域因素修正未完全解析")
            if adjusted_price is None:
                reasons.append("缺少調整至估價基準日單價（題目固定值）")
            if excluded:
                names = [r.factor_name for r in factor_results if r.field_id in excluded]
                reasons.append("PARTIAL_DRAFT 試算未納入之個別因素：" + "、".join(names))
            reason = "；".join(reasons) if reasons else None
        partial = self._allow_partial and bool(excluded or regional_comparison.calculation_mode == "PARTIAL_DRAFT")

        return Table4Comparison(
            comparable_segment_code=comparable_segment_code, comparison_index=comparison_index,
            transaction_date_raw=transaction.get("transaction_date_raw"),
            land_normal_price_raw=transaction.get("land_normal_price_raw"),
            price_date_adjustment_pct_raw=transaction.get("price_date_adjustment_pct_raw"),
            adjusted_price_raw=adjusted_price,
            transaction_source_type=transaction.get("source_type"),
            transaction_source=transaction.get("source"),
            regional_adjustment_pct=regional_pct,
            regional_adjustment_requires_manual_review=regional_manual_review,
            regional_adjustment_source_comparison_index=regional_comparison.comparison_index,
            individual_factor_results=factor_results,
            individual_adjustment_total_pct=individual_total,
            trial_price=trial_price,
            adjustment_abs_sum=adjustment_abs_sum,
            weight_pct=None, weight_status=Table4WeightStatus.MANUAL_REVIEW_REQUIRED,
            weight_requires_human_confirmation=True,
            weight_basis=(
                "不動產估價技術規則§27未定義比較標的權重計算公式（見 engine/"
                "comparable_selection_engine.py 模組文件），本系統不自動產生最終決定權重，"
                "僅可提供 SYSTEM_AUXILIARY 建議值供人工確認"
            ),
            status=FieldStatus.MANUAL_REVIEW_REQUIRED if overall_manual_review else FieldStatus.COMPLETED,
            requires_manual_review=overall_manual_review,
            reason=reason,
            calculation_mode="PARTIAL_DRAFT" if partial else None,
            excluded_factor_ids=excluded,
        )

    def build_analysis(
        self, case_id: str, rule_profile_id: Optional[str], base_segment_code: str,
        comparisons: List[Table4Comparison],
    ) -> Table4Analysis:
        return Table4Analysis(
            case_id=case_id, base_segment_code=base_segment_code,
            rule_profile_id=rule_profile_id, comparisons=comparisons,
        )

    def apply_suggested_weights(self, analysis: Table4Analysis) -> Table4Analysis:
        """PARTIAL_DRAFT only: attaches ComparableSelectionEngine's disclosed,
        non-statutory inverse-adjustment weights (still flagged for human
        confirmation) and the resulting draft base comparison price. Returns
        the analysis unchanged unless every comparable has a trial price."""
        from comparable_selection_engine import ComparableSelectionEngine
        comparisons = analysis.comparisons
        if not self._allow_partial or not comparisons or any(
                c.trial_price is None or c.adjustment_abs_sum is None for c in comparisons):
            return analysis
        suggestions = ComparableSelectionEngine().suggest_weights(
            {c.comparable_segment_code: c.adjustment_abs_sum for c in comparisons})
        basis = ("SYSTEM_AUXILIARY：依各比較標的調整百分率絕對值加總之反比例（1/(|調整|+1)）正規化至100%，"
                 "非不動產估價技術規則§27法定公式，需人工確認")
        weighted = [c.model_copy(update={
            "weight_pct": suggestions[c.comparable_segment_code].suggested_weight_pct,
            "weight_status": Table4WeightStatus.SYSTEM_AUXILIARY_SUGGESTION,
            "weight_requires_human_confirmation": True, "weight_basis": basis,
        }) for c in comparisons]
        price = self._calc.base_parcel_comparison_price(
            {c.comparable_segment_code: c.trial_price for c in weighted},
            {c.comparable_segment_code: c.weight_pct for c in weighted})
        return analysis.model_copy(update={
            "comparisons": weighted, "calculation_mode": "PARTIAL_DRAFT",
            "base_comparison_price": price.rounded_result,
            "base_comparison_price_basis": "PARTIAL_DRAFT：試算價格依系統建議權重加權平均，四捨五入至個位數；需人工複核",
        })
