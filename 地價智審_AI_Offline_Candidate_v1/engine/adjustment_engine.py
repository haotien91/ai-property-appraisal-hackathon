# -*- coding: utf-8 -*-
"""
Adjustment Engine — deterministic matrix lookup between a base-parcel grade
and a comparable's grade for the same factor. Wraps
engine.rule_engine.RuleEngine.adjustment() and converts to the traceable
domain model AdjustmentResult.
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from rule_engine import RuleEngine, GradeNotComparableError, RuleNotFoundError  # noqa: E402
from rule_engine import GradeResult as EngineGradeResult  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import AdjustmentResult, GradeResult  # noqa: E402


class AdjustmentEngineError(Exception):
    pass


class AdjustmentEngine:
    def __init__(self, rule_engine: RuleEngine):
        self._engine = rule_engine

    def compute_adjustment(self, base: GradeResult, comparable: GradeResult) -> AdjustmentResult:
        """base/comparable are domain.models.GradeResult (already produced by
        GradeEngine). This method re-derives the underlying engine-level
        GradeResult objects needed by RuleEngine.adjustment() from the
        RuleResult carried on each domain GradeResult, so the matrix lookup
        itself still goes through the single source of truth in
        engine/rule_engine.py (no duplicated matrix logic here)."""
        if base.factor != comparable.factor:
            raise AdjustmentEngineError(
                f"Cannot compute adjustment across different factors: "
                f"{base.factor!r} vs {comparable.factor!r}"
            )

        # Re-look-up the matched rule records to get the full matrix payload
        # (GradeResult.rule_result does not itself carry the matrix, by
        # design -- the matrix lives only in the rule record / engine layer,
        # never duplicated into the domain model, to avoid two sources of
        # truth for the same numbers).
        base_matched = self._lookup_matched_rule(base)
        comp_matched = self._lookup_matched_rule(comparable)

        engine_base = EngineGradeResult(
            rule_id=base.rule_result.rule_id, factor=base.factor,
            grade=base.rule_result.grade, grade_code=base.rule_result.grade_code,
            grade_label=base.rule_result.grade_label, matched_rule=base_matched,
        )
        engine_comp = EngineGradeResult(
            rule_id=comparable.rule_result.rule_id, factor=comparable.factor,
            grade=comparable.rule_result.grade, grade_code=comparable.rule_result.grade_code,
            grade_label=comparable.rule_result.grade_label, matched_rule=comp_matched,
        )

        try:
            pct = self._engine.adjustment(engine_base, engine_comp)
        except (GradeNotComparableError, RuleNotFoundError) as e:
            raise AdjustmentEngineError(
                f"Adjustment Engine failed for factor {base.factor!r}: {e}"
            ) from e

        return AdjustmentResult(
            field_id=base.field_id,
            factor=base.factor,
            comparable_id=comparable.party_id,
            base_grade=base,
            comparable_grade=comparable,
            adjustment_pct=Decimal(str(pct)),
            rule_id=base.rule_result.rule_id,
            matrix_row_grade_code=base.rule_result.grade_code,
            matrix_col_grade_code=comparable.rule_result.grade_code,
        )

    def _lookup_matched_rule(self, gr: GradeResult) -> dict:
        """Finds the full rule record (including adjustment_matrix) that
        produced this GradeResult, by rule_id, from the engine's internal
        index. This keeps the matrix as single-sourced from
        data/rules/*.json rather than re-declared in the domain layer."""
        for rules in self._engine._index.values():
            for r in rules:
                if r["rule_id"] == gr.rule_result.rule_id:
                    return r
        raise AdjustmentEngineError(
            f"Internal error: rule_id {gr.rule_result.rule_id!r} not found in engine index "
            f"(this should be impossible if the GradeResult came from this same engine instance)"
        )
