# -*- coding: utf-8 -*-
"""
Form Completion Engine — the top-level orchestrator implementing the Phase 4
Goal A pipeline:

    Structured Case Data -> Rule -> Grade -> Adjustment -> Calculation
    -> Completed Fields

This module contains NO grading/adjustment/calculation logic of its own; it
only sequences calls to GradeEngine / AdjustmentEngine / CalculationEngine
and assembles the results into FieldCompletion / FormCompletionResult
domain objects, attaching a full traceability chain to every field.

No Bedrock, no Textract, no real GIS, no AWS database -- this engine
operates purely on a CompetitionCase built from Mock/Golden JSON input, per
Phase 4 instructions.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from domain.models import (  # noqa: E402
    CompetitionCase, FieldCompletion, FormCompletionResult, FieldStatus,
    PartyRole, Evidence, SourceType, GradeResult, AdjustmentResult,
    ExclusionDeterminationStatus,
)
# SHULIN-COMPETITION-RULE-PACK-A2-FINAL-GATE-1 Task 4 fix: these MUST be bare
# (not "engine.xxx") imports. complete_form.py/review.py -- the only real
# production callers of this class -- construct GradeEngine/AdjustmentEngine/
# CalculationEngine via the SAME bare `from grade_engine import ...` style
# (engine/ is on sys.path directly, see runtime_paths.bootstrap()). Python
# treats "grade_engine" and "engine.grade_engine" as two DIFFERENT modules
# with two DIFFERENT GradeEngineError classes, even though it's the same
# source file -- so `except (GradeEngineError, AdjustmentEngineError)` below
# was silently never matching the actual exception instances raised by a
# GradeEngine built from the bare import, and every genuinely-missing-rule
# individual/regional factor (e.g. Shulin's floor_area_ratio_individual,
# which deliberately has no rule record at all) crashed complete_form.py/
# review.py with an UNHANDLED exception instead of the intended graceful
# FieldStatus.MANUAL_REVIEW_REQUIRED degradation -- found via a REAL
# complete_form.py handler-path test, not a FormCompletionEngine unit test
# (which always constructs its own GradeEngine locally and never surfaces
# this cross-module identity mismatch).
from grade_engine import GradeEngine, GradeEngineError  # noqa: E402
from adjustment_engine import AdjustmentEngine, AdjustmentEngineError  # noqa: E402
from calculation_engine import CalculationEngine, CalculationEngineError  # noqa: E402
from comparable_selection_engine import ComparableSelectionEngine  # noqa: E402


class FormCompletionEngine:
    def __init__(self, grade_engine: GradeEngine, adjustment_engine: AdjustmentEngine,
                 calculation_engine: CalculationEngine,
                 comparable_selection_engine: Optional[ComparableSelectionEngine] = None):
        self._grade = grade_engine
        self._adjustment = adjustment_engine
        self._calc = calculation_engine
        self._selection = comparable_selection_engine or ComparableSelectionEngine()

    # ------------------------------------------------------------------
    # 表4 individual-factor completion for one comparable
    # ------------------------------------------------------------------
    def complete_table4_individual_factors(
        self, case: CompetitionCase, comparable_id: str
    ) -> (List[FieldCompletion], List[AdjustmentResult]):
        """Grades + adjusts every individual factor present on both the base
        parcel and the given comparable. Returns (field_completions,
        adjustment_results) so the caller can feed adjustment_results into
        the Calculation Engine's individual_adjustment_total step."""
        fields: List[FieldCompletion] = []
        adjustments: List[AdjustmentResult] = []

        base_by_field = {f.field_id: f for f in case.base_parcel_factors}
        comp_by_field = {f.field_id: f for f in case.comparable_factors.get(comparable_id, [])}

        shared_field_ids = sorted(set(base_by_field) & set(comp_by_field))
        missing_on_comp = sorted(set(base_by_field) - set(comp_by_field))
        missing_on_base = sorted(set(comp_by_field) - set(base_by_field))

        for field_id in shared_field_ids:
            base_input = base_by_field[field_id]
            comp_input = comp_by_field[field_id]
            try:
                base_grade = self._grade.grade_factor(
                    case.city, case.district, case.land_use_type, field_id, base_input,
                    PartyRole.BASE_PARCEL, case.base_parcel_id, rule_set="individual",
                )
                comp_grade = self._grade.grade_factor(
                    case.city, case.district, case.land_use_type, field_id, comp_input,
                    PartyRole.COMPARABLE, comparable_id, rule_set="individual",
                )
                adj = self._adjustment.compute_adjustment(base_grade, comp_grade)
                adjustments.append(adj)

                fields.append(FieldCompletion(
                    field_id=f"{field_id}_differential_rate_{comparable_id}",
                    chinese_label=f"{base_input.factor}差異率",
                    form="表4",
                    raw_value=f"base={base_input.raw_value}, comp={comp_input.raw_value}",
                    normalized_value=f"base={base_grade.normalized_value}, comp={comp_grade.normalized_value}",
                    source=f"Rule Engine（{base_grade.rule_result.source_document} {base_grade.rule_result.source_page}）",
                    rule_id=adj.rule_id,
                    grade=f"base={base_grade.rule_result.grade}, comp={comp_grade.rule_result.grade}",
                    adjustment=adj.adjustment_pct,
                    formula="adjustment_matrix[base_grade_code][comparable_grade_code]",
                    calculation=f"matrix[{adj.matrix_row_grade_code}][{adj.matrix_col_grade_code}] = {adj.adjustment_pct}",
                    final_value=adj.adjustment_pct,
                    status=FieldStatus.COMPLETED,
                    evidence=base_grade.evidence,
                    anomaly_flag=base_grade.rule_result.anomaly_flag,
                ))
            except (GradeEngineError, AdjustmentEngineError) as e:
                fields.append(FieldCompletion(
                    field_id=f"{field_id}_differential_rate_{comparable_id}",
                    chinese_label=f"{base_input.factor}差異率",
                    form="表4",
                    raw_value=f"base={base_input.raw_value}, comp={comp_input.raw_value}",
                    source="Rule Engine（失敗）",
                    status=FieldStatus.MANUAL_REVIEW_REQUIRED,
                    warnings=[str(e)],
                ))

        for field_id in missing_on_comp + missing_on_base:
            fields.append(FieldCompletion(
                field_id=f"{field_id}_differential_rate_{comparable_id}",
                chinese_label=field_id,
                form="表4",
                source="Data Acquisition Layer",
                status=FieldStatus.UNKNOWN,
                warnings=[
                    f"factor present on only one of (base parcel, comparable {comparable_id}); "
                    f"cannot compute a differential rate without both sides"
                ],
            ))

        return fields, adjustments

    # ------------------------------------------------------------------
    # 表5-2 regional-factor completion for one comparable
    # ------------------------------------------------------------------
    def complete_table5_2_regional_factors(
        self, case: CompetitionCase, comparable_id: str,
        base_regional_factors: List, comparable_regional_factors: List,
    ) -> (List[FieldCompletion], List[AdjustmentResult]):
        """Same pattern as complete_table4_individual_factors but for the 28
        regional factors (表5-2). Regional factor inputs are passed
        separately from case.base_parcel_factors/comparable_factors because
        they describe the SEGMENT, not the parcel itself (see
        docs/phase2/form_mapping.md 表1→表4個別因素對照之限制說明)."""
        fields: List[FieldCompletion] = []
        adjustments: List[AdjustmentResult] = []

        base_by_field = {f.field_id: f for f in base_regional_factors}
        comp_by_field = {f.field_id: f for f in comparable_regional_factors}
        shared = sorted(set(base_by_field) & set(comp_by_field))

        for field_id in shared:
            base_input = base_by_field[field_id]
            comp_input = comp_by_field[field_id]
            try:
                base_grade = self._grade.grade_factor(
                    case.city, case.district, case.land_use_type, field_id, base_input,
                    PartyRole.BASE_PARCEL, case.base_parcel_id, rule_set="regional",
                )
                comp_grade = self._grade.grade_factor(
                    case.city, case.district, case.land_use_type, field_id, comp_input,
                    PartyRole.COMPARABLE, comparable_id, rule_set="regional",
                )
                adj = self._adjustment.compute_adjustment(base_grade, comp_grade)
                adjustments.append(adj)
                fields.append(FieldCompletion(
                    field_id=f"{field_id}_adjustment_pct_{comparable_id}",
                    chinese_label=f"{base_input.factor}修正百分比",
                    form="表5-2",
                    raw_value=f"base={base_input.raw_value}, comp={comp_input.raw_value}",
                    normalized_value=f"base={base_grade.normalized_value}, comp={comp_grade.normalized_value}",
                    source=f"Rule Engine（{base_grade.rule_result.source_document} {base_grade.rule_result.source_page}）",
                    rule_id=adj.rule_id,
                    grade=f"base={base_grade.rule_result.grade}, comp={comp_grade.rule_result.grade}",
                    adjustment=adj.adjustment_pct,
                    formula="adjustment_matrix[base_grade_code][comparable_grade_code]",
                    calculation=f"matrix[{adj.matrix_row_grade_code}][{adj.matrix_col_grade_code}] = {adj.adjustment_pct}",
                    final_value=adj.adjustment_pct,
                    status=FieldStatus.COMPLETED,
                    evidence=base_grade.evidence,
                    anomaly_flag=base_grade.rule_result.anomaly_flag,
                ))
            except (GradeEngineError, AdjustmentEngineError) as e:
                fields.append(FieldCompletion(
                    field_id=f"{field_id}_adjustment_pct_{comparable_id}",
                    chinese_label=field_id, form="表5-2", source="Rule Engine（失敗）",
                    status=FieldStatus.MANUAL_REVIEW_REQUIRED, warnings=[str(e)],
                ))

        return fields, adjustments

    # ------------------------------------------------------------------
    # Full 表4 calculation chain for one comparable
    # ------------------------------------------------------------------
    def complete_comparable_price(
        self, case: CompetitionCase, comparable_id: str,
        individual_adjustments: List[AdjustmentResult],
        regional_adjustments: List[AdjustmentResult],
    ) -> (List[FieldCompletion], Optional[Decimal]):
        """Returns (fields, adjustment_abs_sum_raw). The second value is
        None when the chain could not even start (missing land_price/
        date_rate) -- callers use it to build the adjustment_abs_sums dict
        that complete_comparable_weight_fields()'s §27 suggestion needs, so
        a comparable that never produced an abs_sum must not silently show
        up as 0 there (0 would misrepresent "couldn't compute" as "computed
        to zero, i.e. no adjustment needed at all")."""
        fields: List[FieldCompletion] = []

        land_price = case.comparable_land_normal_price.get(comparable_id)
        date_rate = case.comparable_price_date_adjustment_rate.get(comparable_id)
        if land_price is None or date_rate is None:
            fields.append(FieldCompletion(
                field_id=f"trial_price_{comparable_id}", chinese_label="試算價格", form="表4",
                source="Calculation Engine", status=FieldStatus.MANUAL_REVIEW_REQUIRED,
                warnings=[f"missing land_normal_price or price_date_adjustment_rate for {comparable_id}"],
            ))
            return fields, None

        step1 = self._calc.price_date_adjustment(land_price, date_rate)
        fields.append(FieldCompletion(
            field_id=f"adjusted_price_{comparable_id}", chinese_label="調整至估價基準日單價", form="表4",
            raw_value=str(land_price), source="Calculation Engine",
            formula=step1.formula, calculation=f"{step1.raw_result}（完整精度，顯示時可另行四捨五入僅供UI參考）",
            final_value=step1.raw_result, status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE),
        ))

        regional_total = self._calc.regional_total_adjustment(regional_adjustments)
        # Two INDEPENDENT FieldCompletion records for what were previously a
        # single field read twice. Both derive from the same regional_total
        # calculation in the normal/correct case (so they are equal by
        # construction here), but they are now two distinct, independently
        # stored/retrievable/tamperable identities -- field_ids match the
        # existing schemas/field_dictionary.json naming
        # (regional_total_adjustment for 表5-2, region_adjustment_rate_n for
        # 表4) rather than inventing new names. This is the fix for the
        # Phase 8A "Cross-form handler wiring gap" finding: previously only
        # the 表4 field existed, and review.py had to copy its single value
        # into two dict keys to fake a two-sided comparison.
        fields.append(FieldCompletion(
            field_id=f"regional_total_adjustment_{comparable_id}", chinese_label="影響地價區域因素總修正數", form="表5-2",
            source="Calculation Engine", formula=regional_total.formula,
            calculation=str(regional_total.raw_result), final_value=regional_total.raw_result,
            status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE),
        ))
        fields.append(FieldCompletion(
            field_id=f"region_adjustment_rate_{comparable_id}", chinese_label="區域因素調整百分率", form="表4",
            source="Calculation Engine（承接表5-2）", formula=regional_total.formula,
            calculation=str(regional_total.raw_result), final_value=regional_total.raw_result,
            status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE),
        ))

        individual_total = self._calc.individual_adjustment_total(individual_adjustments)
        fields.append(FieldCompletion(
            field_id=f"individual_adjustment_total_{comparable_id}", chinese_label="個別因素調整合計", form="表4",
            source="Calculation Engine", formula=individual_total.formula,
            calculation=str(individual_total.raw_result), final_value=individual_total.raw_result,
            status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE),
        ))

        step2 = self._calc.trial_price(step1.raw_result, regional_total.raw_result, individual_total.raw_result)
        fields.append(FieldCompletion(
            field_id=f"trial_price_{comparable_id}", chinese_label="試算價格", form="表4",
            source="Calculation Engine", formula=step2.formula,
            calculation=f"{step1.raw_result} * (1 + {regional_total.raw_result}% + {individual_total.raw_result}%) = {step2.raw_result}",
            final_value=step2.raw_result, status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE),
        ))

        abs_sum = self._calc.adjustment_abs_sum(date_rate, regional_total.raw_result, individual_total.raw_result)
        fields.append(FieldCompletion(
            field_id=f"adjustment_abs_sum_{comparable_id}", chinese_label="調整百分率絕對值加總", form="表4",
            source="Calculation Engine", formula=abs_sum.formula,
            calculation=str(abs_sum.raw_result), final_value=abs_sum.raw_result,
            status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE),
        ))

        fields.append(self._comparable_exclusion_field(
            comparable_id, regional_adjustments, individual_adjustments, date_rate,
        ))

        fields.append(FieldCompletion(
            field_id=f"price_formation_similarity_{comparable_id}", chinese_label="價格形成因素之相近程度", form="表4",
            source="AI輔助建議＋人工核定（官方僅提供舉例級距，非固定公式，見open_questions.md B-2/C-5）",
            final_value=None, status=FieldStatus.MANUAL_REVIEW_REQUIRED,
            warnings=["此欄位非deterministic規則，須估價師專業判斷後人工填入"],
        ))

        return fields, abs_sum.raw_result

    def _comparable_exclusion_field(
        self, comparable_id: str,
        regional_adjustments: List[AdjustmentResult], individual_adjustments: List[AdjustmentResult],
        date_rate,
    ) -> FieldCompletion:
        """不動產估價技術規則§25 exclusion check. situation_adjustment_pct is
        hardcoded to 0 -- this codebase has no 情況調整（distressed-sale/
        related-party transaction adjustment）factor anywhere in its domain
        model (every comparable is implicitly assumed to be a normal arm's-
        length transaction, 正常買賣); this is a real, disclosed scope gap
        (see docs/backlog.md), not a silent guess of "0 means no situation
        adjustment applies here". exception_note is always None: this
        codebase has no case field carrying an estimator's documented §25
        proviso justification ("性質特殊或區位特殊缺乏市場交易資料"), so this
        check can only ever surface the raw threshold result, never assert
        that an exception has already been claimed."""
        exclusion = self._selection.check_exclusion(
            comparable_id=comparable_id,
            regional_adjustments=regional_adjustments,
            individual_adjustments=individual_adjustments,
            situation_adjustment_pct=Decimal("0"),
            price_date_adjustment_pct=date_rate,
            exception_note=None,
        )

        # §25's 15% single-item threshold has an unambiguous official
        # formula -- this is the ONLY thing allowed to drive `excluded`.
        confirmed_reasons = []
        if exclusion.over_15pct_items:
            confirmed_reasons.append(f"單項調整絕對值超過15%：{'、'.join(exclusion.over_15pct_items)}")

        # §25's 30% "總調整率" clause has NO confirmed formula anywhere this
        # codebase could find (see ComparableExclusionCheck's docstring) --
        # both candidate readings are surfaced for human judgment only, and
        # NEITHER contributes to `excluded` or to confirmed_reasons above.
        unconfirmed_notes = []
        if exclusion.total_over_30pct_signed_sum:
            unconfirmed_notes.append(
                f"讀法A（先加總後取絕對值）={exclusion.total_adjustment_signed_sum_pct}%，超過30%"
            )
        if exclusion.total_over_30pct_abs_component_sum:
            unconfirmed_notes.append(
                f"讀法B（各項先取絕對值後加總）={exclusion.total_adjustment_abs_component_sum_pct}%，超過30%"
            )

        calculation_parts = []
        calculation_parts.append("；".join(confirmed_reasons) if confirmed_reasons else "單項門檻：均未超過15%")
        calculation_parts.append(
            f"總調整率（LEGAL_BASIS_UNCONFIRMED，僅供參考）：{'；'.join(unconfirmed_notes)}"
            if unconfirmed_notes
            else f"總調整率（LEGAL_BASIS_UNCONFIRMED，僅供參考）：兩種候選讀法皆未超過30%"
            f"（讀法A={exclusion.total_adjustment_signed_sum_pct}%，讀法B={exclusion.total_adjustment_abs_component_sum_pct}%）"
        )

        warnings = []
        if exclusion.excluded:
            warnings.append(
                "不動產估價技術規則§25：" + "；".join(confirmed_reasons) + "。除非估價報告書已敘明「性質特殊或"
                "區位特殊缺乏市場交易資料」之例外情形，否則本比較標的應不予採用——請人工確認是否"
                "符合例外規定，若否應排除此比較標的重新選樣"
            )
        if unconfirmed_notes:
            warnings.append(
                "不動產估價技術規則§25「總調整率」大於30%之精確計算公式，於法規本文與"
                "土地徵收補償市價查估作業手冊中均查無明文定義（詳見程式內citation），"
                "系統不自動依此排除比較標的。僅供參考："
                + "；".join(unconfirmed_notes) + "。請估價師依專業判斷決定是否比照§25排除。"
            )

        return FieldCompletion(
            field_id=f"comparable_exclusion_check_{comparable_id}", chinese_label="比較標的排除檢核（§25）", form="表4",
            source="Comparable Selection Engine",
            formula="任一單項調整絕對值>15%（法規明文，唯一排除依據）；"
            "總調整率>30%（LEGAL_BASIS_UNCONFIRMED，僅供參考，不驅動排除）",
            calculation="｜".join(calculation_parts),
            # Keyed off exclusion_determination_status (not the plain
            # `excluded` boolean) so UNDETERMINED never gets worded as if it
            # were a confirmed "無需排除" -- see ExclusionDeterminationStatus's
            # docstring for why that distinction matters downstream.
            final_value={
                ExclusionDeterminationStatus.EXCLUDED: "建議排除，需人工確認",
                ExclusionDeterminationStatus.NOT_EXCLUDED: "無需排除（§15%門檻，30%總門檻兩種候選讀法皆未觸發）",
                ExclusionDeterminationStatus.UNDETERMINED: "待確認（30%總門檻公式未確認，不能斷定是否應排除）",
            }[exclusion.exclusion_determination_status],
            status=(
                FieldStatus.COMPLETED
                if exclusion.exclusion_determination_status == ExclusionDeterminationStatus.NOT_EXCLUDED
                else FieldStatus.MANUAL_REVIEW_REQUIRED
            ),
            warnings=warnings,
            evidence=Evidence(
                source="不動產估價技術規則§25", source_type=SourceType.CALCULATION_ENGINE,
                source_document="不動產估價技術規則", source_page="第25條",
            ),
        )

    def complete_base_parcel_comparison_price(
        self, case: CompetitionCase, trial_prices: Dict[str, Decimal],
    ) -> FieldCompletion:
        weights = case.comparable_weight
        if not weights or set(weights) != set(trial_prices):
            return FieldCompletion(
                field_id="base_parcel_comparison_price", chinese_label="比準地比較價格", form="表4",
                source="Calculation Engine", status=FieldStatus.MANUAL_REVIEW_REQUIRED,
                warnings=["comparable weights missing or incomplete; cannot compute final weighted price"],
            )
        try:
            step = self._calc.base_parcel_comparison_price(trial_prices, weights)
        except CalculationEngineError as e:
            return FieldCompletion(
                field_id="base_parcel_comparison_price", chinese_label="比準地比較價格", form="表4",
                source="Calculation Engine", status=FieldStatus.MANUAL_REVIEW_REQUIRED, warnings=[str(e)],
            )
        return FieldCompletion(
            field_id="base_parcel_comparison_price", chinese_label="比準地比較價格", form="表4",
            source="Calculation Engine", formula=step.formula,
            calculation=f"{step.raw_result} -> 四捨五入(REQ-022) -> {step.rounded_result}",
            final_value=step.rounded_result, status=FieldStatus.COMPLETED,
            evidence=Evidence(source="Calculation Engine", source_type=SourceType.CALCULATION_ENGINE,
                               source_document="土地徵收補償市價查估作業手冊.pdf", source_page="p.52-53"),
        )

    # ------------------------------------------------------------------
    # §27 suggested weight (all comparables at once -- suggest_weights()
    # normalizes across the full set, so it cannot run per-comparable)
    # ------------------------------------------------------------------
    def complete_comparable_weight_fields(
        self, case: CompetitionCase, adjustment_abs_sums: Dict[str, Decimal],
    ) -> List[FieldCompletion]:
        suggestions = (
            self._selection.suggest_weights(adjustment_abs_sums)
            if len(adjustment_abs_sums) > 1 else {}
        )
        fields: List[FieldCompletion] = []
        for comparable_id in adjustment_abs_sums:
            weight = case.comparable_weight.get(comparable_id)
            if weight is not None:
                note = (
                    "官方明文：僅1筆比較標的時，價格形成因素之相近程度填普通，權重=100%（作業手冊p.52-53）"
                    if weight == 100 and len(case.comparable_ids) == 1
                    else None
                )
                fields.append(FieldCompletion(
                    field_id=f"comparable_weight_{comparable_id}", chinese_label="比較標的權重", form="表4",
                    source="使用者輸入", calculation=note,
                    final_value=weight, status=FieldStatus.COMPLETED,
                ))
                continue

            suggestion = suggestions.get(comparable_id)
            if suggestion is not None:
                note = (
                    f"系統建議（{suggestion.method}）：{suggestion.suggested_weight_pct}%——"
                    "此為決定性演算法輸出之建議值，非AI生成、非不動產估價技術規則明文規定之法定公式，"
                    "須估價師確認後才可採用"
                )
                warning = f"§27無官方權重公式，此為系統建議值，須人工確認才能採用：{suggestion.suggested_weight_pct}%"
            elif len(case.comparable_ids) == 1:
                note = "官方明文：僅1筆比較標的時，價格形成因素之相近程度填普通，權重應為100%（作業手冊p.52-53），但案件未提供權重數值"
                warning = "案件未提供權重數值，依作業手冊應為100%，須人工確認並填入"
            else:
                note = "多筆比較標的時之權重公式未經官方確認，見open_questions.md B-2/C-5"
                warning = "案件未提供權重數值，且無法計算系統建議值，須人工填入"

            fields.append(FieldCompletion(
                field_id=f"comparable_weight_{comparable_id}", chinese_label="比較標的權重", form="表4",
                source="系統建議＋人工核定（非AI／非法定公式）", calculation=note,
                final_value=None, status=FieldStatus.MANUAL_REVIEW_REQUIRED,
                warnings=[warning],
            ))
        return fields

    # ------------------------------------------------------------------
    # §26 trial-price gap check (all comparables at once)
    # ------------------------------------------------------------------
    def complete_trial_price_gap_field(self, trial_prices: Dict[str, Decimal]) -> Optional[FieldCompletion]:
        if len(trial_prices) < 2:
            return None  # §26's "highest vs lowest" comparison is undefined for <2 trial prices
        gap = self._selection.check_trial_price_gap(trial_prices)
        return FieldCompletion(
            field_id="trial_price_gap_check", chinese_label="試算價格最高最低差距檢核（§26）", form="表4",
            source="Comparable Selection Engine",
            formula="(最高試算價格-最低試算價格)/((最高+最低)/2)",
            calculation=(
                f"最高={gap.max_price}（{gap.max_comparable_id}），最低={gap.min_price}"
                f"（{gap.min_comparable_id}），差距={gap.gap_pct.quantize(Decimal('0.01'))}%"
            ),
            final_value=("超過20%門檻，需人工確認" if gap.triggered else "未超過20%門檻"),
            status=(FieldStatus.MANUAL_REVIEW_REQUIRED if gap.triggered else FieldStatus.COMPLETED),
            warnings=(
                [
                    f"不動產估價技術規則§26：試算價格最高（{gap.max_comparable_id}）與最低"
                    f"（{gap.min_comparable_id}）差距達{gap.gap_pct.quantize(Decimal('0.01'))}%，"
                    "超過20%門檻，請人工複核各比較標的之調整過程是否有誤"
                ]
                if gap.triggered else []
            ),
            evidence=Evidence(
                source="不動產估價技術規則§26", source_type=SourceType.CALCULATION_ENGINE,
                source_document="不動產估價技術規則", source_page="第26條",
            ),
        )

    # ------------------------------------------------------------------
    # Top-level entry point
    # ------------------------------------------------------------------
    def complete_form(
        self, case: CompetitionCase,
        base_regional_factors: List, comparable_regional_factors: Dict[str, List],
    ) -> FormCompletionResult:
        all_fields: List[FieldCompletion] = []
        trial_prices: Dict[str, Decimal] = {}
        adjustment_abs_sums: Dict[str, Decimal] = {}

        for comparable_id in case.comparable_ids:
            ind_fields, ind_adjustments = self.complete_table4_individual_factors(case, comparable_id)
            reg_fields, reg_adjustments = self.complete_table5_2_regional_factors(
                case, comparable_id, base_regional_factors,
                comparable_regional_factors.get(comparable_id, []),
            )
            price_fields, abs_sum_raw = self.complete_comparable_price(
                case, comparable_id, ind_adjustments, reg_adjustments,
            )
            all_fields.extend(ind_fields)
            all_fields.extend(reg_fields)
            all_fields.extend(price_fields)

            trial_price_field = next(
                (f for f in price_fields if f.field_id == f"trial_price_{comparable_id}"), None
            )
            if trial_price_field and trial_price_field.status == FieldStatus.COMPLETED:
                trial_prices[comparable_id] = trial_price_field.final_value
            if abs_sum_raw is not None:
                adjustment_abs_sums[comparable_id] = abs_sum_raw

        all_fields.extend(self.complete_comparable_weight_fields(case, adjustment_abs_sums))

        gap_field = self.complete_trial_price_gap_field(trial_prices)
        if gap_field is not None:
            all_fields.append(gap_field)

        final_field = self.complete_base_parcel_comparison_price(case, trial_prices)
        all_fields.append(final_field)

        warnings = [w for f in all_fields for w in f.warnings]
        return FormCompletionResult(
            case_no=case.case_no, form="表4+表5-2（整合輸出）", fields=all_fields,
            generated_at=datetime.now(),
            engine_versions={"rule_schema": "1.0", "grade_engine": "1.0",
                              "adjustment_engine": "1.0", "calculation_engine": "1.0"},
            warnings=warnings,
        )
