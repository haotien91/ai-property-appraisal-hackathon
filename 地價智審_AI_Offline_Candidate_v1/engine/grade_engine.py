# -*- coding: utf-8 -*-
"""
Grade Engine — wraps Phase 3's deterministic engine.rule_engine.RuleEngine and
converts its output into fully-traceable domain models (domain.models.GradeResult).

This module adds NO new grading logic of its own: all grade determination
remains inside engine/rule_engine.py (Phase 3, already tested with 69 pytest
cases). This module's job is purely to attach provenance (Evidence) and
convert to the Pydantic domain model shape required for FieldCompletion
traceability.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime
from typing import Union

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from rule_engine import RuleEngine, RuleNotFoundError, WrongUnitError, AmbiguousFactorError  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    GradeResult, RuleResult, Evidence, SourceType, PartyRole, FactorInput,
)


class GradeEngineError(Exception):
    """Raised when the Grade Engine cannot produce a grade. Wraps the
    underlying RuleNotFoundError/WrongUnitError/AmbiguousFactorError from
    engine.rule_engine so callers only need to catch one exception type at
    this layer, without hiding which underlying error occurred (see
    .__cause__)."""
    pass


class GradeEngine:
    def __init__(self, rule_engine: RuleEngine):
        self._engine = rule_engine

    def grade_factor(self, city: str, district: str, land_use_type: str,
                      field_id: str, factor_input: FactorInput,
                      party_role: PartyRole, party_id: str,
                      rule_set: str) -> GradeResult:
        """Grades one factor for one party (base parcel or a comparable).
        rule_set is REQUIRED (not optional) at this layer: callers must
        always know whether a field belongs to the regional (表5-2) or
        individual (表4) evaluation table, since some factor names (建蔽率/
        容積率) exist in both tables with different thresholds -- see
        docs/phase3/source_anomalies.md ANOMALY-06. This is the call-site
        fix for the Phase 4 NO-GO Recovery BLK-01 root cause. Never guesses:
        any failure from the underlying RuleEngine is re-raised as
        GradeEngineError, never silently defaulted."""
        if rule_set not in ("regional", "individual"):
            raise GradeEngineError(
                f"rule_set must be 'regional' or 'individual', got {rule_set!r} "
                f"for field_id={field_id!r}. This is a caller bug, not a data issue."
            )
        try:
            raw = factor_input.raw_value
            result = self._engine.grade(
                city, district, land_use_type, factor_input.factor,
                raw, unit=factor_input.unit, rule_set=rule_set,
            )
        except (RuleNotFoundError, WrongUnitError, AmbiguousFactorError) as e:
            raise GradeEngineError(
                f"Grade Engine could not determine a grade for field_id={field_id!r} "
                f"factor={factor_input.factor!r} value={factor_input.raw_value!r} "
                f"rule_set={rule_set!r}: {e}"
            ) from e

        rule_result = RuleResult(
            rule_id=result.rule_id,
            factor=result.factor,
            matched=True,
            grade=result.grade,
            grade_code=result.grade_code,
            grade_label=result.grade_label,
            source_document=result.matched_rule["source_document"],
            source_page=result.matched_rule["source_page"],
            anomaly_flag=result.matched_rule.get("anomaly_flag"),
        )

        evidence = Evidence(
            source=f"Rule Engine（{result.matched_rule['source_document']} {result.matched_rule['source_page']}）",
            source_type=SourceType.RULE_ENGINE,
            source_document=result.matched_rule["source_document"],
            source_page=result.matched_rule["source_page"],
            confidence="高" if not rule_result.anomaly_flag else "中（原始資料存在標記異常，見source_anomalies.md）",
            retrieved_at=datetime.now(),
        )

        return GradeResult(
            field_id=field_id,
            factor=factor_input.factor,
            party_role=party_role,
            party_id=party_id,
            raw_value=raw,
            normalized_value=raw.strip() if isinstance(raw, str) else raw,
            rule_result=rule_result,
            evidence=evidence,
        )
