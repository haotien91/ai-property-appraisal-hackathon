# -*- coding: utf-8 -*-
"""complete_form.py — POST /api/cases/{id}/complete-form. Runs the full
Phase 4 FormCompletionEngine pipeline (Grade -> Adjustment -> Calculation)."""
from __future__ import annotations

import sys
import os
import json

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
from case_reconstruction import build_case_and_regional_factors  # noqa: E402

from rule_engine import RuleEngine  # noqa: E402
from grade_engine import GradeEngine  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from calculation_engine import CalculationEngine  # noqa: E402
from form_completion_engine import FormCompletionEngine  # noqa: E402


def _rule_engine():
    data_dir = runtime_paths.data_dir()
    with open(os.path.join(data_dir, "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(data_dir, "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return RuleEngine(reg + ind)


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

        rule_engine = _rule_engine()
        fce = FormCompletionEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine())
        result = fce.complete_form(case, regional_base, regional_comp)

        result_json = json.loads(result.model_dump_json())
        case_store.put_record(case_no, "FORM_COMPLETION", result_json)
        return response(200, result_json)
