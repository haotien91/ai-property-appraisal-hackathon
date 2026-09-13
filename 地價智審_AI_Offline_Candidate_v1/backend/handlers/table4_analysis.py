# -*- coding: utf-8 -*-
"""
table4_analysis.py — TABLE4-THREE-COMPARABLE-D1.

    GET /api/cases/{id}/table4

Builds and returns a domain.models.Table4Analysis (表4 比較法調查估價表)
for a Competition case with N segment-scoped comparables. Comparables are
read from the case's own CompetitionSegmentMap (backend/handlers/
competition_segments.py), in whatever order/count it declares -- never
hardcoded to 3, never a comparable_ids[0] shortcut.

Reuses, unmodified:
  - table51_analysis.py::build_table51_analysis_for_case() for the
    Table5-1 bridge (Task 4) -- this handler NEVER recomputes regional
    adjustment itself; it reads each comparable's OWN Table51Comparison.
    total_adjustment_pct from that SAME call's result.
  - rule_engine_factory.build_rule_engine_for_case(rule_profile_id=...)
    for fail-closed rule resolution.
  - case_reconstruction.py's apply_competition_provided_individual_
    precedence() + to_factor_inputs() for segment-scoped individual-factor
    loading with COMPETITION_PROVIDED_FIXED precedence (the individual-
    factor counterpart of the regional precedence path B1-FINAL-GATE-1
    established).
  - engine/table4_analysis_engine.py's Table4AnalysisEngine (itself only
    calls the existing GradeEngine/AdjustmentEngine/CalculationEngine/
    ComparableSelectionEngine -- no grading or calculation logic lives in
    this handler or that engine module).

Each of P001-00/P002-00/P003-00/P004-00's individual factors are read from
their OWN "FACTORS#<segment_code>" DynamoDB item -- never a shared
case-level record -- so P002/P003/P004 can never read each other's data.
"""
from __future__ import annotations

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from typing import List, Optional

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import competition_segments  # noqa: E402
import case_reconstruction  # noqa: E402
from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError  # noqa: E402
from case_rule_repository import CaseRulePackageInvalidError  # noqa: E402
from table51_analysis import (  # noqa: E402
    build_table51_analysis_for_case, SegmentMapRequiredError, SegmentFactorsNotFoundError,
)

from grade_engine import GradeEngine  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from calculation_engine import CalculationEngine  # noqa: E402
from table4_analysis_engine import Table4AnalysisEngine, extract_individual_rule_records  # noqa: E402
from domain.models import FactorInput  # noqa: E402


def _load_segment_individual_factors(
    case_no: str, segment_code: str, party_key: str, comparable_id: Optional[str] = None,
) -> Optional[List[FactorInput]]:
    """One segment's own individual factor list (表4 個別因素), reading
    THAT segment's own "FACTORS#<segment_code>" record's
    user_submitted_factors.{base_parcel_factors|comparable_factors[cid]},
    with COMPETITION_PROVIDED_FIXED precedence applied via case_
    reconstruction.apply_competition_provided_individual_precedence()
    (Task 2/10 -- the individual-factor counterpart of the regional
    precedence path). `party_key` is "base_parcel_factors" or
    "comparable_factors"; `comparable_id` is required for the latter.
    Returns None if this segment has no FACTORS record at all yet."""
    factors_record = case_store.get_record(case_no, competition_segments.factors_sk(segment_code))
    if factors_record is None:
        return None
    user_factors = factors_record.get("user_submitted_factors") or {}
    if party_key == "base_parcel_factors":
        raw_list = user_factors.get("base_parcel_factors", [])
    else:
        raw_list = (user_factors.get("comparable_factors") or {}).get(comparable_id, [])
    merged = case_reconstruction.apply_competition_provided_individual_precedence(raw_list, factors_record)
    return case_reconstruction.to_factor_inputs(merged)


def get_table4_analysis(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_table4_analysis"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        try:
            table51_analysis, _table51_resolution = build_table51_analysis_for_case(case_no, meta)
        except SegmentMapRequiredError:
            return error_response(
                400, "SEGMENT_MAP_REQUIRED",
                f"案件 {case_no} 尚未定義 CompetitionSegmentMap，Table4 僅適用於 multi-segment Competition case",
            )
        except SegmentFactorsNotFoundError as e:
            return error_response(400, "VALIDATION_ERROR", str(e))
        except RuleProfileNotReadyError as e:
            return error_response(409, "RULE_PROFILE_NOT_READY", str(e))
        except CaseRulePackageInvalidError as e:
            return error_response(409, "CASE_RULE_INVALID", f"{e}. MANUAL_REVIEW_REQUIRED")

        segment_map = competition_segments.get_segment_map(case_no)
        base_segment = segment_map.base_segment
        rule_profile_id = meta.get("rule_profile_id")

        try:
            rule_engine, rule_resolution = build_rule_engine_for_case(case_no, rule_profile_id=rule_profile_id)
        except RuleProfileNotReadyError as e:
            return error_response(409, "RULE_PROFILE_NOT_READY", str(e))
        except CaseRulePackageInvalidError as e:
            return error_response(409, "CASE_RULE_INVALID", f"{e}. MANUAL_REVIEW_REQUIRED")

        base_individual_factors = _load_segment_individual_factors(case_no, base_segment.segment_code, "base_parcel_factors")
        if base_individual_factors is None:
            return error_response(
                400, "VALIDATION_ERROR",
                f"比準地 segment {base_segment.segment_code!r} 尚未執行 collect_data，無 FACTORS 資料",
            )

        individual_rule_records = extract_individual_rule_records(rule_engine)

        table4_engine = Table4AnalysisEngine(
            GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine(), individual_rule_records,
        )

        comparisons = []
        table51_by_segment = {c.comparable_segment_code: c for c in table51_analysis.comparisons}
        for comp in sorted(segment_map.comparables, key=lambda s: s.comparison_index or 0):
            comp_individual_factors = _load_segment_individual_factors(
                case_no, comp.segment_code, "comparable_factors", comparable_id=comp.segment_code,
            )
            if comp_individual_factors is None:
                return error_response(
                    400, "VALIDATION_ERROR",
                    f"比較標的 segment {comp.segment_code!r} 尚未執行 collect_data，無 FACTORS 資料",
                )
            comp_factors_record = case_store.get_record(case_no, competition_segments.factors_sk(comp.segment_code)) or {}
            transaction = comp_factors_record.get("competition_provided_transaction") or {}
            regional_comparison = table51_by_segment.get(comp.segment_code)
            if regional_comparison is None:
                return error_response(
                    500, "INTERNAL_ERROR",
                    f"Table5-1 comparisons 缺少 comparable {comp.segment_code!r} 對應結果，無法建立 Table5-1 bridge",
                )
            comparisons.append(table4_engine.build_comparison(
                meta.get("city", ""), base_segment.district, base_segment.land_use_type, base_segment.segment_code,
                comp.district, comp.land_use_type, comp.segment_code, comp.comparison_index,
                base_individual_factors, comp_individual_factors, transaction, regional_comparison,
            ))

        analysis = table4_engine.build_analysis(
            case_id=case_no, rule_profile_id=rule_profile_id,
            base_segment_code=base_segment.segment_code, comparisons=comparisons,
        )

        return response(200, {
            "analysis": analysis.model_dump(mode="json"),
            "rule_resolution_status": rule_resolution.resolution_status,
            "rule_package_id": rule_resolution.package_id,
            "comparable_count": len(analysis.comparisons),
        })
