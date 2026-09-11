# -*- coding: utf-8 -*-
"""
Minimal deterministic Rule Engine for AI-Assisted Real Estate Valuation Case Review.

Design principles (per project constitution):
- Deterministic only. No LLM / heuristic guessing anywhere in this module.
- Every function raises explicit, typed exceptions rather than silently
  guessing or defaulting when a rule cannot be found.
- Normalize -> Rule Selection -> Grade Result -> Adjustment Matrix Lookup
  are kept as separate, independently testable steps.
"""
from dataclasses import dataclass
from typing import Optional, Union, List, Dict, Any


class RuleNotFoundError(Exception):
    """Raised when no rule band matches the given (city, district, land_use_type,
    factor, value) combination. The engine NEVER falls back to a guessed grade."""
    pass


class WrongUnitError(Exception):
    """Raised when the unit of the supplied value does not match the rule's
    declared unit. The engine NEVER silently converts units (e.g. km->m)."""
    pass


class GradeNotComparableError(Exception):
    """Raised when an adjustment_matrix lookup is attempted with a grade_code
    that does not exist in that factor's matrix (e.g. asking for grade_code=3
    on a 2-level boolean factor)."""
    pass


class AmbiguousFactorError(Exception):
    """Raised when a factor name resolves to rule records from more than one
    rule_set (e.g. '建蔽率' exists in both the regional and individual
    evaluation tables with DIFFERENT thresholds) and the caller did not
    specify which rule_set to use. The engine never silently picks one --
    that would risk applying the wrong table's thresholds (see
    docs/phase3/source_anomalies.md ANOMALY-06 and the Phase 4 NO-GO
    Recovery BLK-01 root cause)."""
    pass


@dataclass
class NormalizedInput:
    city: str
    district: str
    land_use_type: str
    factor: str
    value: Union[float, str, bool]
    unit: Optional[str] = None
    rule_set: Optional[str] = None


@dataclass
class GradeResult:
    rule_id: str
    factor: str
    grade: str
    grade_code: int
    grade_label: Optional[str]
    matched_rule: Dict[str, Any]


class RuleEngine:
    def __init__(self, rule_records: List[Dict[str, Any]]):
        """rule_records: flat list of rule dicts conforming to rule_schema.json
        (as found in data/rules/regional_rules.json['rules'] or
        data/rules/individual_rules.json['rules'])."""
        self._rules = rule_records
        # Primary index: fully-scoped key including rule_set, derived from the
        # rule_id prefix ("REG-" -> "regional", "IND-" -> "individual") so no
        # change to the rule data files themselves is required.
        self._index: Dict[tuple, List[Dict[str, Any]]] = {}
        # Secondary index: unscoped key, used ONLY when the caller does not
        # specify rule_set. Kept for full backward compatibility with the
        # ~93% of factors whose name is not shared across rule sets.
        self._unscoped_index: Dict[tuple, List[Dict[str, Any]]] = {}
        for r in rule_records:
            rule_set = self._infer_rule_set(r)
            scoped_key = (r["city"], r["district"], r["land_use_type"], r["factor"], rule_set)
            self._index.setdefault(scoped_key, []).append(r)
            unscoped_key = (r["city"], r["district"], r["land_use_type"], r["factor"])
            self._unscoped_index.setdefault(unscoped_key, []).append(r)

    @staticmethod
    def _infer_rule_set(rule: Dict[str, Any]) -> str:
        rid = rule.get("rule_id", "")
        if rid.startswith("REG-"):
            return "regional"
        if rid.startswith("IND-"):
            return "individual"
        return "unknown"

    # ------------------------------------------------------------------
    # Step 1: Normalize
    # ------------------------------------------------------------------
    def normalize(self, city: str, district: str, land_use_type: str,
                  factor: str, value: Union[float, str, bool],
                  unit: Optional[str] = None,
                  rule_set: Optional[str] = None) -> NormalizedInput:
        """Normalizes raw input. Deliberately does NOT convert units (e.g. km->m):
        unit conversion is a business-rule decision, not a generic engine
        responsibility, and silent conversion could mask upstream data errors
        (see docs/phase3/source_anomalies.md for documented m/km anomalies in
        the source evaluation tables themselves)."""
        if isinstance(value, str):
            value_norm = value.strip()
        else:
            value_norm = value
        return NormalizedInput(
            city=city.strip(), district=district.strip(),
            land_use_type=land_use_type.strip(), factor=factor.strip(),
            value=value_norm, unit=unit.strip() if isinstance(unit, str) else unit,
            rule_set=rule_set.strip() if isinstance(rule_set, str) else rule_set,
        )

    # ------------------------------------------------------------------
    # Step 2: Rule Selection (find candidate rule bands for a factor)
    # ------------------------------------------------------------------
    def select_rules(self, ni: NormalizedInput) -> List[Dict[str, Any]]:
        if ni.rule_set is not None:
            key = (ni.city, ni.district, ni.land_use_type, ni.factor, ni.rule_set)
            candidates = self._index.get(key)
            if not candidates:
                raise RuleNotFoundError(
                    f"No rules found for city={ni.city!r} district={ni.district!r} "
                    f"land_use_type={ni.land_use_type!r} factor={ni.factor!r} "
                    f"rule_set={ni.rule_set!r}. This system never guesses a grade "
                    f"in the absence of a rule."
                )
            return candidates

        # No rule_set specified: fall back to the unscoped index, but first
        # check whether this factor name is ambiguous across rule sets. This
        # keeps every non-colliding factor call working exactly as before.
        unscoped_key = (ni.city, ni.district, ni.land_use_type, ni.factor)
        candidates = self._unscoped_index.get(unscoped_key)
        if not candidates:
            raise RuleNotFoundError(
                f"No rules found for city={ni.city!r} district={ni.district!r} "
                f"land_use_type={ni.land_use_type!r} factor={ni.factor!r}. "
                f"This system never guesses a grade in the absence of a rule."
            )
        distinct_rule_sets = {self._infer_rule_set(r) for r in candidates}
        if len(distinct_rule_sets) > 1:
            raise AmbiguousFactorError(
                f"Factor {ni.factor!r} exists in more than one rule set "
                f"({sorted(distinct_rule_sets)}) with potentially different "
                f"thresholds (e.g. 建蔽率/容積率 differ between the regional and "
                f"individual evaluation tables). Caller must specify rule_set="
                f"'regional' or rule_set='individual' explicitly -- this engine "
                f"never silently picks one table over the other."
            )
        return candidates

    # ------------------------------------------------------------------
    # Step 3: Range Match / Grade Result
    # ------------------------------------------------------------------
    def grade(self, city: str, district: str, land_use_type: str,
              factor: str, value: Union[float, str, bool],
              unit: Optional[str] = None,
              rule_set: Optional[str] = None) -> GradeResult:
        ni = self.normalize(city, district, land_use_type, factor, value, unit, rule_set)
        candidates = self.select_rules(ni)

        value_type = candidates[0]["value_type"]

        # Unit check applies only to genuine numeric measurements. A string
        # label routed to categorical matching (including the "區段內有"
        # sentinel for distance_positive/negative factors) carries no
        # distance value by definition, so no unit is expected for it.
        is_numeric_input = isinstance(ni.value, (int, float))
        expected_unit = candidates[0]["unit"]
        if expected_unit is not None and is_numeric_input:
            if ni.unit is None:
                raise WrongUnitError(
                    f"Factor {factor!r} expects unit {expected_unit!r} but no unit was supplied."
                )
            if ni.unit != expected_unit:
                raise WrongUnitError(
                    f"Factor {factor!r} expects unit {expected_unit!r} but got {ni.unit!r}. "
                    f"This engine never silently converts units."
                )

        if value_type in ("numeric_range", "distance_positive", "distance_negative"):
            if isinstance(ni.value, str):
                # Handles the "區段內有" (facility located inside the segment)
                # sentinel grade band, which the source tables express as a
                # label with no numeric bounds rather than a distance value.
                matched = self._match_label(candidates, ni.value, factor)
            else:
                matched = self._match_numeric(candidates, ni.value, factor)
        elif value_type in ("boolean", "categorical"):
            matched = self._match_label(candidates, ni.value, factor)
        else:
            raise RuleNotFoundError(f"Unsupported value_type {value_type!r} for factor {factor!r}")

        return GradeResult(
            rule_id=matched["rule_id"], factor=factor, grade=matched["grade"],
            grade_code=matched["grade_code"], grade_label=matched.get("grade_label"),
            matched_rule=matched
        )

    def _match_numeric(self, candidates, value, factor):
        if not isinstance(value, (int, float)):
            raise RuleNotFoundError(
                f"Factor {factor!r} requires a numeric value for range matching, got {type(value)}"
            )
        for rule in candidates:
            lb, ub = rule["lower_bound"], rule["upper_bound"]
            if lb is None and ub is None:
                # Fully-unbounded band = a categorical sentinel state (e.g.
                # "區段內有") that must be selected via its label, never by
                # a numeric distance value. Skip it here so a plain numeric
                # distance can never accidentally match "already inside".
                continue
            lb_inc, ub_inc = rule["lower_inclusive"], rule["upper_inclusive"]
            lower_ok = True
            upper_ok = True
            if lb is not None:
                lower_ok = (value >= lb) if lb_inc else (value > lb)
            if ub is not None:
                upper_ok = (value < ub) if not ub_inc else (value <= ub)
            if lower_ok and upper_ok:
                return rule
        raise RuleNotFoundError(
            f"Value {value} for factor {factor!r} did not match any band "
            f"(checked {len(candidates)} bands). No grade guessed."
        )

    def _match_label(self, candidates, value, factor):
        if isinstance(value, bool):
            value = "有" if value else "無"
        for rule in candidates:
            if rule.get("grade_label") == value:
                return rule
        raise RuleNotFoundError(
            f"Label {value!r} for factor {factor!r} did not match any known category/boolean band. "
            f"No grade guessed."
        )

    # ------------------------------------------------------------------
    # Step 4: Adjustment Matrix Lookup
    # ------------------------------------------------------------------
    def adjustment(self, base_grade_result: GradeResult,
                   comparable_grade_result: GradeResult) -> float:
        """Returns the differential/adjustment percentage for comparing a
        base-parcel (比準地) grade against a comparable (比較標的) grade for
        the SAME factor. row=比準地 grade_code, column=比較標的 grade_code
        (see docs/phase3/rule_engine_spec.md for the matrix-orientation proof)."""
        if base_grade_result.factor != comparable_grade_result.factor:
            raise RuleNotFoundError(
                "adjustment() requires both grade results to be for the same factor: "
                f"{base_grade_result.factor!r} vs {comparable_grade_result.factor!r}"
            )
        matrix = base_grade_result.matched_rule["adjustment_matrix"]
        row_key = str(base_grade_result.grade_code)
        col_key = str(comparable_grade_result.grade_code)
        if row_key not in matrix:
            raise GradeNotComparableError(
                f"grade_code {row_key} not present in adjustment_matrix rows for "
                f"factor {base_grade_result.factor!r}"
            )
        if col_key not in matrix[row_key]:
            raise GradeNotComparableError(
                f"grade_code {col_key} not present in adjustment_matrix columns for "
                f"factor {base_grade_result.factor!r}"
            )
        return matrix[row_key][col_key]
