# -*- coding: utf-8 -*-
"""
table51_analysis.py — TABLE51-THREE-COMPARABLE-C1.

    GET /api/cases/{id}/table5-1

Builds and returns a domain.models.Table51Analysis (表5-1 影響地價區域因素
分析明細表) for a Competition case with N segment-scoped comparables --
base=P001-00, comparables=P002-00/P003-00/P004-00 for shulin_residential_
2026, but nothing here is hardcoded to that specific case: comparables are
read from the case's own CompetitionSegmentMap (backend/handlers/
competition_segments.py), in whatever order/count it declares.

Reuses, unmodified:
  - rule_engine_factory.build_rule_engine_for_case(rule_profile_id=...) for
    fail-closed rule resolution (SHULIN-COMPETITION-RULE-PACK-A2/
    COMPETITION-DOMAIN-MULTI-SEGMENT-B1's own established gate -- a
    Competition case with no CONFIRMED rule package still correctly blocks
    here with RULE_PROFILE_NOT_READY, never silently grades against
    Jinshan static rules).
  - case_reconstruction.py's `_apply_competition_provided_precedence()` +
    `to_factor_inputs()` (COMPETITION_PROVIDED_FIXED precedence -- the SAME
    path B1-FINAL-GATE-1 established for complete_form.py/review.py, not a
    second implementation).
  - engine/table51_analysis_engine.py's Table51AnalysisEngine (which itself
    only calls the existing GradeEngine/AdjustmentEngine -- no grading
    logic lives in this handler or that engine module).

Each of P001-00/P002-00/P003-00/P004-00's regional factors are read from
their OWN "FACTORS#<segment_code>" DynamoDB item (competition_segments.
factors_sk()) -- never a shared case-level record -- so P002/P003/P004
can never read each other's (or P001's) data.

build_table51_analysis_for_case() (TABLE4-THREE-COMPARABLE-D1 Task 4) is
the SAME logic re-exposed as a plain function -- backend/handlers/
table4_analysis.py calls it directly to obtain the Table5-1 bridge for
each comparable, rather than recomputing regional adjustment itself.
"""
from __future__ import annotations

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from typing import List, Optional, Tuple

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import competition_segments  # noqa: E402
from competition_segments import InvalidSegmentCodeError  # noqa: E402
import case_reconstruction  # noqa: E402
from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError  # noqa: E402
from case_rule_repository import CaseRulePackageInvalidError  # noqa: E402

from grade_engine import GradeEngine  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from table51_analysis_engine import Table51AnalysisEngine, extract_regional_rule_records  # noqa: E402
from domain.models import FactorInput, Table51Analysis  # noqa: E402


class SegmentMapRequiredError(Exception):
    """Raised when a case has no CompetitionSegmentMap at all -- Table5-1/
    Table4's multi-comparable model has no well-defined lineage for such a
    (legacy Jinshan-style) case. Callers translate this to 400
    SEGMENT_MAP_REQUIRED."""


class SegmentFactorsNotFoundError(Exception):
    """Raised when a segment declared in the case's CompetitionSegmentMap
    has never had collect_data() run for it (no FACTORS#<segment_code>
    record at all yet). Callers translate this to 400 VALIDATION_ERROR."""

    def __init__(self, segment_code: str):
        super().__init__(f"segment {segment_code!r} 尚未執行 collect_data，無 FACTORS 資料")
        self.segment_code = segment_code


def _load_segment_regional_factors(case_no: str, segment_code: Optional[str]) -> Optional[List[FactorInput]]:
    """One segment's own regional factor list, COMPETITION_PROVIDED_FIXED
    precedence already applied (Task 2/7) -- reuses case_reconstruction.py's
    existing helpers verbatim, no new precedence logic. Returns None if
    this segment has no FACTORS record at all yet (caller decides how to
    surface that)."""
    factors_record = case_store.get_record(case_no, competition_segments.factors_sk(segment_code))
    if factors_record is None:
        return None
    regional_base = case_reconstruction._apply_competition_provided_precedence(
        factors_record.get("regional_base_factors", []), factors_record,
    )
    return case_reconstruction.to_factor_inputs(regional_base)


def build_table51_analysis_for_case(case_no: str, meta: dict) -> Tuple[Table51Analysis, object]:
    """Returns (Table51Analysis, CaseRuleResolution). Raises
    RuleProfileNotReadyError / CaseRulePackageInvalidError (rule_engine_
    factory.py's own fail-closed exceptions, unchanged) / SegmentMapRequiredError
    / SegmentFactorsNotFoundError -- callers (get_table51_analysis below,
    and backend/handlers/table4_analysis.py) translate these to HTTP
    responses themselves."""
    segment_map = competition_segments.get_segment_map(case_no)
    if segment_map is None:
        raise SegmentMapRequiredError(case_no)

    rule_profile_id = meta.get("rule_profile_id")
    rule_engine, rule_resolution = build_rule_engine_for_case(case_no, rule_profile_id=rule_profile_id)

    base_segment = segment_map.base_segment
    base_factors = _load_segment_regional_factors(case_no, base_segment.segment_code)
    if base_factors is None:
        raise SegmentFactorsNotFoundError(base_segment.segment_code)

    comparables = []
    for comp in sorted(segment_map.comparables, key=lambda s: s.comparison_index or 0):
        comp_factors = _load_segment_regional_factors(case_no, comp.segment_code)
        if comp_factors is None:
            raise SegmentFactorsNotFoundError(comp.segment_code)
        comparables.append((comp.segment_code, comp.comparison_index, comp.district, comp.land_use_type, comp_factors))

    regional_rule_records = extract_regional_rule_records(rule_engine)
    table51_engine = Table51AnalysisEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), regional_rule_records)

    analysis = table51_engine.build_analysis(
        case_id=case_no, rule_profile_id=rule_profile_id, city=meta.get("city", ""),
        base_district=base_segment.district, base_land_use_type=base_segment.land_use_type,
        base_segment_code=base_segment.segment_code, base_regional_factors=base_factors,
        comparables=comparables,
    )
    return analysis, rule_resolution


def get_table51_analysis(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_table51_analysis"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        try:
            analysis, rule_resolution = build_table51_analysis_for_case(case_no, meta)
        except SegmentMapRequiredError:
            # Table5-1's three-comparable domain model is inherently
            # segment-based -- a case with no segment map at all (every
            # legacy Jinshan case) has no well-defined base_segment/
            # comparable_segment lineage for this handler to build. This
            # is a deliberate scope boundary (Task 9): legacy cases keep
            # using the existing complete_form.py/review.py 表5-2 path
            # unchanged, and simply do not call this NEW endpoint.
            return error_response(
                400, "SEGMENT_MAP_REQUIRED",
                f"案件 {case_no} 尚未定義 CompetitionSegmentMap，Table5-1 僅適用於 multi-segment Competition case",
            )
        except SegmentFactorsNotFoundError as e:
            return error_response(400, "VALIDATION_ERROR", str(e))
        except RuleProfileNotReadyError as e:
            return error_response(409, "RULE_PROFILE_NOT_READY", str(e))
        except CaseRulePackageInvalidError as e:
            return error_response(409, "CASE_RULE_INVALID", f"{e}. MANUAL_REVIEW_REQUIRED")

        return response(200, {
            "analysis": analysis.model_dump(mode="json"),
            "rule_resolution_status": rule_resolution.resolution_status,
            "rule_package_id": rule_resolution.package_id,
            "comparable_count": len(analysis.comparisons),
        })
