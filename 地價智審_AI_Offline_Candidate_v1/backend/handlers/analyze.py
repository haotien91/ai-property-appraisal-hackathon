# -*- coding: utf-8 -*-
"""analyze.py — POST /api/cases/{id}/analyze. Runs GradeEngine +
AdjustmentEngine only (no Calculation/PDF yet) -- kept as its own Step
Functions state per Phase 7 Part E's stage list (Rule step, separate from
Calculation step)."""
from __future__ import annotations

import sys
import os
import json

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402

from rule_engine import RuleEngine  # noqa: E402
from grade_engine import GradeEngine, GradeEngineError  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from domain.models import FactorInput, Evidence, SourceType, PartyRole  # noqa: E402

_RULE_ENGINE = None


def _load_rule_engine():
    global _RULE_ENGINE
    if _RULE_ENGINE is None:
        data_dir = runtime_paths.data_dir()
        with open(os.path.join(data_dir, "rules", "regional_rules.json"), encoding="utf-8") as f:
            reg = json.load(f)["rules"]
        with open(os.path.join(data_dir, "rules", "individual_rules.json"), encoding="utf-8") as f:
            ind = json.load(f)["rules"]
        _RULE_ENGINE = RuleEngine(reg + ind)
    return _RULE_ENGINE


def analyze(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "analyze"):
        meta = case_store.get_case_meta(case_no)
        factors_record = case_store.get_record(case_no, "FACTORS")
        if meta is None or factors_record is None:
            return error_response(404, "CASE_NOT_FOUND", f"案件 {case_no} 尚未收集資料")

        rule_engine = _load_rule_engine()
        grade_engine = GradeEngine(rule_engine)
        adjustment_engine = AdjustmentEngine(rule_engine)

        grades, adjustments, unresolved = [], [], []
        user_factors = factors_record.get("user_submitted_factors", {})
        # user_factors shape mirrors POST .../collect-data's request body:
        # {"base_parcel_factors": [...], "comparable_factors": {cid: [...]}}
        base_list = user_factors.get("base_parcel_factors", []) if isinstance(user_factors, dict) else []
        comp_map = user_factors.get("comparable_factors", {}) if isinstance(user_factors, dict) else {}

        for comparable_id, comp_list in comp_map.items():
            base_by_field = {f["field_id"]: f for f in base_list}
            comp_by_field = {f["field_id"]: f for f in comp_list}
            for field_id in sorted(set(base_by_field) & set(comp_by_field)):
                b, c = base_by_field[field_id], comp_by_field[field_id]
                ev = Evidence(source=b.get("evidence", {}).get("source", "使用者輸入"),
                               source_type=SourceType.AI_ASSISTED_FILL)
                try:
                    base_fi = FactorInput(field_id=field_id, factor=b["factor"], raw_value=b["raw_value"],
                                           unit=b.get("unit"), evidence=ev)
                    comp_fi = FactorInput(field_id=field_id, factor=c["factor"], raw_value=c["raw_value"],
                                           unit=c.get("unit"), evidence=ev)
                    rule_set = "individual" if field_id.startswith("individual_") else "regional"
                    base_g = grade_engine.grade_factor(meta["city"] if "city" in meta else "新北市",
                                                         meta["district"], meta.get("land_use_type", "商業用地"),
                                                         field_id, base_fi, PartyRole.BASE_PARCEL, "base",
                                                         rule_set=rule_set)
                    comp_g = grade_engine.grade_factor(meta["city"] if "city" in meta else "新北市",
                                                         meta["district"], meta.get("land_use_type", "商業用地"),
                                                         field_id, comp_fi, PartyRole.COMPARABLE, comparable_id,
                                                         rule_set=rule_set)
                    adj = adjustment_engine.compute_adjustment(base_g, comp_g)
                    grades.append({"field_id": field_id, "factor": b["factor"],
                                    "grade": f"base={base_g.rule_result.grade}, comp={comp_g.rule_result.grade}",
                                    "rule_id": base_g.rule_result.rule_id})
                    adjustments.append({"field_id": field_id, "factor": b["factor"],
                                         "adjustment_pct": str(adj.adjustment_pct), "rule_id": adj.rule_id})
                except GradeEngineError as e:
                    unresolved.append({"field_id": field_id, "reason": "RULE_NOT_FOUND", "factor": b.get("factor", "")})

        case_store.put_record(case_no, "ANALYSIS", {"grades": grades, "adjustments": adjustments})
        return response(200, {"case_no": case_no, "grades": grades, "adjustments": adjustments,
                               "unresolved_factors": unresolved})
