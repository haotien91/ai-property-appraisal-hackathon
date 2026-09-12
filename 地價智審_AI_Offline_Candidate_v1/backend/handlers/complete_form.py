# -*- coding: utf-8 -*-
"""complete_form.py — POST /api/cases/{id}/complete-form. Runs the full
Phase 4 FormCompletionEngine pipeline (Grade -> Adjustment -> Calculation)."""
from __future__ import annotations

import sys
import json

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
from case_reconstruction import build_case_and_regional_factors  # noqa: E402

from grade_engine import GradeEngine  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from calculation_engine import CalculationEngine  # noqa: E402
from form_completion_engine import FormCompletionEngine  # noqa: E402
from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError  # noqa: E402
from case_rule_repository import CaseRulePackageInvalidError  # noqa: E402


def complete_form(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "complete_form"):
        meta = case_store.get_case_meta(case_no)
        factors_record = case_store.get_record(case_no, "FACTORS")
        if meta is None or factors_record is None:
            return error_response(400, "VALIDATION_ERROR", "案件資料不完整，無法執行pipeline")

        try:
            case, regional_base, regional_comp = build_case_and_regional_factors(case_no, meta, factors_record)
        except Exception as e:
            return error_response(400, "VALIDATION_ERROR", f"案件資料格式錯誤：{e}")

        try:
            rule_engine, rule_resolution = build_rule_engine_for_case(
                case_no, rule_profile_id=meta.get("rule_profile_id"),
            )
        except RuleProfileNotReadyError as e:
            return error_response(409, "RULE_PROFILE_NOT_READY", str(e))
        except CaseRulePackageInvalidError as e:
            return error_response(409, "CASE_RULE_INVALID", f"{e}. MANUAL_REVIEW_REQUIRED")

        fce = FormCompletionEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine())
        result = fce.complete_form(case, regional_base, regional_comp)

        result_json = json.loads(result.model_dump_json())
        # FormCompletionEngine itself is untouched (already field/factor-
        # driven, no land-use-type-specific hardcoding) -- rule_source_type
        # is attached here, purely from each field's already-populated
        # rule_id, so a case-scoped CONFIRMED package's provenance is
        # traceable without modifying that engine.
        for field in result_json.get("fields", []):
            trace = rule_resolution.trace_by_rule_id.get(field.get("rule_id"))
            field["rule_source_type"] = trace.rule_source_type.value if trace else None
        result_json["rule_resolution_status"] = rule_resolution.resolution_status
        result_json["rule_package_id"] = rule_resolution.package_id
        result_json["rule_resolution_warnings"] = rule_resolution.warnings

        case_store.put_record(case_no, "FORM_COMPLETION", result_json)
        return response(200, result_json)
