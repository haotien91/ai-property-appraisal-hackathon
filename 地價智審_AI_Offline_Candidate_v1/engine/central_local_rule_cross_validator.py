# -*- coding: utf-8 -*-
"""
central_local_rule_cross_validator.py — STEP 4 §5 (docs/audit/
TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md): checks a LOCAL rule (from
data/rules/regional_rules.json / individual_rules.json, or a case-scoped
CaseRulePackage's regional_rules/individual_rules) never claims a larger
`max_adjustment` than the applicable CENTRAL ceiling
(data/rules/central_max_adjustment_range.json) allows.

Deliberately NOT a grade-determination component -- this module never
calls RuleEngine.grade()/AdjustmentEngine, never assigns a grade or an
adjustment result to a case. It answers exactly one question: "does this
RULE DATA (not any specific case's outcome) respect the legal ceiling?" A
violation is a RULE DATA ERROR (this round's own finding -- see §5's
explicit framing), never described as "案件估價錯誤" (a case valuation
error) -- no case computation is inspected here at all.

Subgrade mapping status: `data/rules/central_max_range_local_mapping.json`
records the EXISTING (already-Golden-Case) local rule files as UNMAPPED
against the central table's specific subgrades (高度/中度/普通/村里鄰
商業用地, etc.) -- deliberately NOT resolved by this module either (see
that file's own history: docs/phase8a/competition_checklist.md explicitly
deferred this, and STEP 4's own empirical check, see docs/audit/
TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md §5, found the Golden Case's
Jinshan commercial rules do not match any SINGLE central subgrade closely
enough to justify inferring one -- best match was only 12/26 factors).
Guessing high/medium/ordinary/neighborhood is explicitly forbidden by
this round's scope.

Given that, `max_ceiling_for_factor()` below uses the MOST PERMISSIVE
(maximum) value across every subgrade of the given land_use_type/
table_type as the ceiling bound -- a conservative, non-guessing choice:
if a local rule doesn't exceed even the most generous applicable central
subgrade for that factor, it certainly doesn't exceed whichever specific
subgrade actually applies; if it DOES exceed the most generous one, that
is an unambiguous violation regardless of which subgrade is correct.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class CentralMaximumExceededIssue:
    factor: str
    table_type: str
    land_use_type: str
    local_max_adjustment: float
    central_ceiling: float
    local_rule_id: Optional[str] = None


def _norm_item_name(name: Optional[str]) -> str:
    if name is None:
        return ""
    return "".join(name.split())


def max_ceiling_for_factor(central_entries: List[Dict[str, Any]], table_type: str,
                            land_use_type: str, factor: str) -> Optional[float]:
    """The most permissive (max) central `max_range_pct` across every
    subgrade of (table_type, land_use_type, factor). None if the factor
    has no matching central entry at all (DASH_NOT_APPLICABLE/BLANK_NO_
    DATA cells, or a factor the central table simply doesn't cover for
    this land_use_type -- see §3, '-' stays NOT_APPLICABLE, never 0%,
    so those cells are correctly excluded here rather than treated as a
    ceiling of 0)."""
    target = _norm_item_name(factor)
    values = []
    for e in central_entries:
        if e.get("table_type") != table_type or e.get("land_use_type") != land_use_type:
            continue
        if _norm_item_name(e.get("item_name")) != target:
            continue
        v = e.get("max_range_pct")
        if v is None:
            continue
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def check_central_maximum_not_exceeded(local_rules: List[Dict[str, Any]], central_entries: List[Dict[str, Any]],
                                        table_type: str, land_use_type: str) -> List[CentralMaximumExceededIssue]:
    """local_rules: a flat rule_schema.json-shaped list (one record per
    grade, `max_adjustment` duplicated identically across a factor's
    rows -- same convention RuleTableValidator already assumes). Checks
    each DISTINCT factor once. Returns [] when every local factor's
    max_adjustment is within its applicable central ceiling (or has no
    central entry to compare against at all, per NO_CENTRAL_CEILING
    handling -- callers that want to know about factors with NO
    corresponding central data should inspect that separately, e.g. via
    docs/audit/RULE_COVERAGE_MATRIX.md; this function reports ONLY actual
    ceiling violations, never "no data to compare" as if it were one)."""
    issues: List[CentralMaximumExceededIssue] = []
    seen_factors = set()
    for r in local_rules:
        factor = r.get("factor")
        if factor in seen_factors:
            continue
        seen_factors.add(factor)
        local_max = r.get("max_adjustment")
        if local_max is None:
            continue
        ceiling = max_ceiling_for_factor(central_entries, table_type, land_use_type, factor)
        if ceiling is None:
            continue
        if float(local_max) > ceiling + 1e-9:
            issues.append(CentralMaximumExceededIssue(
                factor=factor, table_type=table_type, land_use_type=land_use_type,
                local_max_adjustment=float(local_max), central_ceiling=ceiling, local_rule_id=r.get("rule_id"),
            ))
    return issues
