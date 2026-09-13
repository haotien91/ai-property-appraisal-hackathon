# -*- coding: utf-8 -*-
"""
table51_analysis_engine.py — TABLE51-THREE-COMPARABLE-C1.

Builds a domain.models.Table51Analysis (表5-1 影響地價區域因素分析明細表)
for one Competition case with N segment-scoped comparables, entirely by
CALLING the existing, unmodified GradeEngine.grade_factor() and
AdjustmentEngine.compute_adjustment() -- exactly the same pattern
FormCompletionEngine already uses for its own 表5-2 regional-factor
completion. This module adds NO grading logic of its own (no matrix
lookups, no threshold comparisons); it only:

  1. Derives the canonical 29-factor catalog (field_id -> factor_name/
     category) DIRECTLY from whatever RuleEngine instance the caller
     already resolved (via backend/handlers/rule_engine_factory.py's
     build_rule_engine_for_case(rule_profile_id=...)) -- never a
     hardcoded factor list, so this is NOT specific to the Shulin 29
     factors in particular; it is whatever "REG-*" rule records that
     RuleEngine happens to have been built from.
  2. For each of those factors, for each (base_segment, comparable_segment)
     pair, calls GradeEngine.grade_factor() twice (once per party) +
     AdjustmentEngine.compute_adjustment() once -- OR, if the factor is
     genuinely absent from either side, emits an honest MANUAL_REVIEW_
     REQUIRED result instead of silently dropping it (the gap
     FormCompletionEngine.complete_table5_2_regional_factors() itself has:
     it only processes the base/comparable field_id INTERSECTION and
     silently skips anything outside it).
  3. Groups results into the 8 official categories and computes category
     subtotals + one grand total, per the OFFICIAL_BLANK_FORM_FORMULA
     found in 表5影響地價區域因素分析明細表(住宅用地).xlsx's own
     "表5-1區域因素明細表(住)" sheet: B42 = "=(1)+(2)+...+(8)" (a plain
     TEXT annotation in the official blank form, not a live Excel formula
     -- data_type='s', verified via openpyxl -- so this is reproduced as
     the form's own documented intent, never claimed as an independently
     invented or statutory formula). Fails closed (subtotal/grand total =
     None, never a value that silently excludes an unresolved factor as if
     it were zero) whenever any member factor needs manual review.

Never averages P002/P003/P004 into one comparable, never assumes
comparable_ids[0] is "the" comparable -- callers pass an explicit,
ordered list of (comparable_segment_code, comparison_index,
comparable_regional_factors) tuples and get back one Table51Comparison per
entry, independently.
"""
from __future__ import annotations

import re
import sys
import os
from collections import OrderedDict
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from grade_engine import GradeEngine, GradeEngineError  # noqa: E402
from adjustment_engine import AdjustmentEngine, AdjustmentEngineError  # noqa: E402
from regional_factor_value_normalization import normalize_regional_factor_value  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    FactorInput, PartyRole, FieldStatus,
    Table51Analysis, Table51Comparison, Table51FactorResult, Table51CategorySubtotal,
)

_CATEGORY_INDEX_RE = re.compile(r"\((\d+)\)\s*$")


def _category_index(category: str) -> int:
    m = _CATEGORY_INDEX_RE.search(category or "")
    return int(m.group(1)) if m else 0


def build_regional_factor_catalog(regional_rule_records: List[dict]) -> "OrderedDict[str, Tuple[str, str]]":
    """field_id -> (factor_name, category), one entry per DISTINCT factor
    (deduplicated by rule_id prefix, i.e. one entry regardless of how many
    grade rows that factor has). field_id is derived generically from the
    rule_id prefix convention this project's rule packs already use
    ("REG-BUILDING_COVERAGE_RATIO-3" -> "regional_building_coverage_ratio")
    -- the SAME transformation scripts/build_shulin_competition_rule_pack.py
    and data/competition_cases/.../segment_table3_fixtures.py already rely
    on implicitly; never a hand-maintained list that could silently drift
    from the actual rule pack. Ordered by first appearance (which, for
    every rule pack built by this project's own generator scripts, is
    already the official category order 1-8)."""
    catalog: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
    for r in regional_rule_records:
        rule_id = r.get("rule_id", "")
        if not rule_id.startswith("REG-"):
            continue
        prefix = rule_id.rsplit("-", 1)[0]
        field_id = "regional_" + prefix[len("REG-"):].lower()
        if field_id not in catalog:
            catalog[field_id] = (r["factor"], r["category"])
    return catalog


def extract_regional_rule_records(rule_engine) -> List[dict]:
    """Flattens whatever RuleEngine._index the caller already built (via
    build_rule_engine_for_case()) into a de-duplicated (by rule_id) list of
    "REG-*" rule records -- the SAME internal-index access pattern
    engine/adjustment_engine.py's own _lookup_matched_rule() already uses,
    not a new/parallel data path. Never re-reads regional_rules.json
    directly, so this always reflects EXACTLY the rule set actually in
    effect for this case (CONFIRMED Shulin package, static Jinshan, or
    whatever else build_rule_engine_for_case() resolved)."""
    seen: Dict[str, dict] = {}
    for records in rule_engine._index.values():
        for r in records:
            if r.get("rule_id", "").startswith("REG-"):
                seen[r["rule_id"]] = r
    return list(seen.values())


class Table51AnalysisEngine:
    def __init__(self, grade_engine: GradeEngine, adjustment_engine: AdjustmentEngine,
                 regional_rule_records: List[dict], allow_partial: bool = False):
        self._grade = grade_engine
        self._adjustment = adjustment_engine
        self._catalog = build_regional_factor_catalog(regional_rule_records)
        # Opt-in draft mode for the public-data auto-fill pipeline: totals are
        # summed over resolved factors and every excluded factor is listed.
        self._allow_partial = allow_partial

    @property
    def factor_count(self) -> int:
        return len(self._catalog)

    def build_comparison(
        self, city: str,
        base_district: str, base_land_use_type: str, base_segment_code: str,
        comparable_district: str, comparable_land_use_type: str,
        comparable_segment_code: str, comparison_index: int,
        base_regional_factors: List[FactorInput], comparable_regional_factors: List[FactorInput],
    ) -> Table51Comparison:
        base_by_field = {f.field_id: f for f in base_regional_factors}
        comp_by_field = {f.field_id: f for f in comparable_regional_factors}

        factor_results: List[Table51FactorResult] = []
        for field_id, (factor_name, category) in self._catalog.items():
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
                factor_results.append(Table51FactorResult(
                    field_id=field_id, factor_name=factor_name, category=category,
                    base_segment_code=base_segment_code, comparable_segment_code=comparable_segment_code,
                    base_raw_value=base_input.raw_value if base_input else None,
                    base_source_type=base_source_type, base_source=base_source,
                    comparable_raw_value=comp_input.raw_value if comp_input else None,
                    comparable_source_type=comp_source_type, comparable_source=comp_source,
                    status=FieldStatus.MANUAL_REVIEW_REQUIRED, requires_manual_review=True,
                    reason=f"缺值：{'/'.join(missing)} 尚無此因素資料，無法判定等級",
                ))
                continue

            # Task 1: raw_value is NEVER mutated -- normalization produces a
            # SEPARATE evaluation_value (None when no mapping is needed),
            # fed into GradeEngine via a grading-only FactorInput clone.
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
            normalization_reason = base_norm_reason or comp_norm_reason
            mapping_source = base_mapping_source or comp_mapping_source

            try:
                base_grade = self._grade.grade_factor(
                    city, base_district, base_land_use_type, field_id, base_for_grading,
                    PartyRole.BASE_PARCEL, base_segment_code, rule_set="regional",
                )
                comp_grade = self._grade.grade_factor(
                    city, comparable_district, comparable_land_use_type, field_id, comp_for_grading,
                    PartyRole.COMPARABLE, comparable_segment_code, rule_set="regional",
                )
                adj = self._adjustment.compute_adjustment(base_grade, comp_grade)
                factor_results.append(Table51FactorResult(
                    field_id=field_id, factor_name=factor_name, category=category,
                    base_segment_code=base_segment_code, comparable_segment_code=comparable_segment_code,
                    base_raw_value=base_input.raw_value, base_evaluation_value=base_eval_value,
                    base_source_type=base_source_type, base_source=base_source,
                    comparable_raw_value=comp_input.raw_value, comparable_evaluation_value=comp_eval_value,
                    comparable_source_type=comp_source_type, comparable_source=comp_source,
                    normalization_reason=normalization_reason, mapping_source=mapping_source,
                    base_grade=base_grade.rule_result.grade, comparable_grade=comp_grade.rule_result.grade,
                    adjustment_pct=adj.adjustment_pct, rule_id=adj.rule_id,
                    rule_source_document=base_grade.rule_result.source_document,
                    rule_source_page=base_grade.rule_result.source_page,
                    status=FieldStatus.COMPLETED, requires_manual_review=False,
                ))
            except (GradeEngineError, AdjustmentEngineError) as e:
                factor_results.append(Table51FactorResult(
                    field_id=field_id, factor_name=factor_name, category=category,
                    base_segment_code=base_segment_code, comparable_segment_code=comparable_segment_code,
                    base_raw_value=base_input.raw_value, base_evaluation_value=base_eval_value,
                    base_source_type=base_source_type, base_source=base_source,
                    comparable_raw_value=comp_input.raw_value, comparable_evaluation_value=comp_eval_value,
                    comparable_source_type=comp_source_type, comparable_source=comp_source,
                    normalization_reason=normalization_reason, mapping_source=mapping_source,
                    status=FieldStatus.MANUAL_REVIEW_REQUIRED, requires_manual_review=True,
                    reason=str(e),
                ))

        category_subtotals = self._build_category_subtotals(factor_results, self._allow_partial)
        total = self._build_grand_total(category_subtotals, self._allow_partial)
        any_manual_review = any(r.requires_manual_review for r in factor_results)
        excluded = [r.field_id for r in factor_results if r.requires_manual_review] if self._allow_partial else []
        return Table51Comparison(
            comparable_segment_code=comparable_segment_code, comparison_index=comparison_index,
            factor_results=factor_results, category_subtotals=category_subtotals,
            total_adjustment_pct=total,
            status=FieldStatus.MANUAL_REVIEW_REQUIRED if any_manual_review else FieldStatus.COMPLETED,
            requires_manual_review=any_manual_review,
            calculation_mode="PARTIAL_DRAFT" if excluded else None,
            excluded_factor_ids=excluded,
        )

    @staticmethod
    def _build_category_subtotals(factor_results: List[Table51FactorResult],
                                  allow_partial: bool = False) -> List[Table51CategorySubtotal]:
        by_category: "OrderedDict[str, List[Table51FactorResult]]" = OrderedDict()
        for r in factor_results:
            by_category.setdefault(r.category, []).append(r)
        subtotals = []
        for category, results in by_category.items():
            resolved = [r for r in results if not r.requires_manual_review]
            has_unresolved = len(resolved) != len(results)
            if has_unresolved and not (allow_partial and resolved):
                subtotals.append(Table51CategorySubtotal(
                    category=category, category_index=_category_index(category),
                    subtotal_pct=None, requires_manual_review=True,
                ))
            else:
                total = sum((r.adjustment_pct for r in resolved), Decimal("0"))
                subtotals.append(Table51CategorySubtotal(
                    category=category, category_index=_category_index(category),
                    subtotal_pct=total, requires_manual_review=has_unresolved,
                ))
        return subtotals

    @staticmethod
    def _build_grand_total(category_subtotals: List[Table51CategorySubtotal],
                           allow_partial: bool = False) -> Optional[Decimal]:
        if allow_partial:
            values = [s.subtotal_pct for s in category_subtotals if s.subtotal_pct is not None]
            return sum(values, Decimal("0")) if values else None
        if any(s.requires_manual_review for s in category_subtotals):
            return None
        return sum((s.subtotal_pct for s in category_subtotals), Decimal("0"))

    def build_analysis(
        self, case_id: str, rule_profile_id: Optional[str], city: str,
        base_district: str, base_land_use_type: str, base_segment_code: str,
        base_regional_factors: List[FactorInput],
        comparables: List[Tuple[str, int, str, str, List[FactorInput]]],
    ) -> Table51Analysis:
        """`comparables`: ordered list of (segment_code, comparison_index,
        district, land_use_type, regional_factors) tuples -- one entry per
        comparable segment, each producing one INDEPENDENT
        Table51Comparison. Never merges or averages entries together."""
        comparisons = [
            self.build_comparison(
                city, base_district, base_land_use_type, base_segment_code,
                comp_district, comp_land_use_type, comp_segment_code, comparison_index,
                base_regional_factors, comp_factors,
            )
            for comp_segment_code, comparison_index, comp_district, comp_land_use_type, comp_factors in comparables
        ]
        return Table51Analysis(
            case_id=case_id, base_segment_code=base_segment_code,
            rule_profile_id=rule_profile_id, comparisons=comparisons,
        )
