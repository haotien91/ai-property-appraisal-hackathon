# -*- coding: utf-8 -*-
"""
Builds a SubmittedFormData representing a filled-in form that is CORRECT
everywhere except three deliberately-injected errors, per Phase 6's DEMO
ERROR CASES requirement. The "correct everywhere else" baseline is computed
by actually running the real Rule/Adjustment engines against the Golden
Case's true raw values (not hand-typed), so this file cannot silently drift
from the actual rule data.

Case A: 主要道路寬度(regional) 真實原始值18M，正確等級為代碼3「普通」（獨立
        驗證見docs/phase3/rule_engine_spec.md）。故意提交錯誤但內部自洽的
        代碼1／文字「優」（code與text互相一致，只有grade本身判斷錯誤 --
        Grade Representation Contract Check A應為GRADE_ERROR，Check B應為
        CONSISTENT/PASSED，兩者正交，不得混為一談）。
Case B: 深度(individual) 真實比準地23m/比較標的16m，正確差異率為1.00%（獨立
        驗證見docs/phase3/rule_engine_spec.md §3.1，5組非平凡案例之一）。
        故意提交錯誤差異率5.00%。
Case C: 表5-2影響地價區域因素總修正數，Golden Case真實值為0.00%（因比準地與
        比較標的1同屬地價區段P002-00，見docs/phase2/business_process.md
        Step3「Golden Case觀察」）。故意在表4提交不一致值5.00%。

具體測試值全部以已於Phase 2/3反覆驗證之Golden Case Source為準，非憑空捏造。
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.audit_engine import SubmittedFormData  # noqa: E402


def build_submitted_form_with_demo_errors(rule_engine, case, base_regional_factors, comparable_regional_factors):
    """Returns a SubmittedFormData that is correct everywhere except the
    three demo error cases. `case` is the golden CompetitionCase,
    `base_regional_factors`/`comparable_regional_factors` are the regional
    FactorInput lists (same shape as data/golden/golden_case_input.py's
    BASE_REGIONAL/COMP_REGIONAL)."""
    submitted = SubmittedFormData(case_no=case.case_no)

    # ---- 表1 land-use-ratio fields: correct-by-construction (Golden Case's
    # true values -- 金山都市計畫、第二種商業區、建蔽率70%、容積率240%,
    # see docs/phase3/rule_engine_spec.md and data/rules/
    # plan_zone_floor_area_ratios.json's sole "jinshan" entry). Case-level,
    # not per-comparable, so this is not one of the three demo error cases.
    submitted.submitted_land_use_zone = "第二種商業區"
    submitted.submitted_building_coverage_rate = "70"
    submitted.submitted_floor_area_ratio = "240"
    submitted.confirmed_plan_name = "金山都市計畫"
    submitted.internal_plan_id = "jinshan"
    submitted.plan_identification_source = "MANUAL_INPUT"

    # ---- 表1 main_road_width: 18M is what 查估書表範本.pdf's worked
    # example actually states (表1/表5-2, 中山路). It is NOT independently
    # confirmable against any official road-width source this codebase has
    # access to (see providers/road_provider.py's module docstring survey)
    # -- it is the appraiser's own field-observed submission, not a
    # government dataset value. Populated here as SUBMITTED data only; no
    # road_width_evidence is injected by this fixture, so
    # RoadWidthResolver honestly reports UNAVAILABLE rather than a
    # fabricated PASS (see tests/test_smart_review.py's
    # TestRoadWidthWiringInAuditEngine for the SOURCE_UNCONFIRMED
    # documentation and Mock-vs-Real-validation separation).
    submitted.submitted_main_road_width = "18"

    for comparable_id in case.comparable_ids:
        # ---- Regional factors: fill every grade CORRECTLY via the real engine ----
        base_by_field = {f.field_id: f for f in base_regional_factors}
        comp_by_field = {f.field_id: f for f in comparable_regional_factors[comparable_id]}
        regional_total = 0
        for field_id in sorted(set(base_by_field) & set(comp_by_field)):
            fi = base_by_field[field_id]
            grade_result = rule_engine.grade(
                case.city, case.district, case.land_use_type, fi.factor,
                fi.raw_value, unit=fi.unit, rule_set="regional",
            )
            submitted.submitted_grades[(field_id, comparable_id)] = grade_result.grade
            submitted.submitted_grade_codes[(field_id, comparable_id)] = str(grade_result.grade_code)
            regional_total += 0  # Golden Case: all regional differentials are 0 (same segment)

        # CASE A: deliberately corrupt 主要道路寬度's submitted grade --
        # code and text are corrupted TOGETHER, self-consistently (as a
        # real appraiser writing a wrong-but-internally-coherent judgment
        # would), so this fixture exercises Check A=GRADE_ERROR /
        # Check B=CONSISTENT, not a representation inconsistency.
        submitted.submitted_grades[("regional_main_road_width", comparable_id)] = "優"  # true=普通
        submitted.submitted_grade_codes[("regional_main_road_width", comparable_id)] = "1"  # true=3

        # ---- Individual factors: fill every adjustment CORRECTLY via the real engine ----
        base_ind = {f.field_id: f for f in case.base_parcel_factors}
        comp_ind = {f.field_id: f for f in case.comparable_factors[comparable_id]}
        for field_id in sorted(set(base_ind) & set(comp_ind)):
            bi, ci = base_ind[field_id], comp_ind[field_id]
            base_g = rule_engine.grade(case.city, case.district, case.land_use_type, bi.factor,
                                        bi.raw_value, unit=bi.unit, rule_set="individual")
            comp_g = rule_engine.grade(case.city, case.district, case.land_use_type, ci.factor,
                                        ci.raw_value, unit=ci.unit, rule_set="individual")
            matrix = base_g.matched_rule["adjustment_matrix"]
            correct_adj = matrix[str(base_g.grade_code)][str(comp_g.grade_code)]
            submitted.submitted_adjustments[(field_id, comparable_id)] = str(correct_adj)

        # CASE B: deliberately corrupt 深度's submitted differential rate
        submitted.submitted_adjustments[("individual_land_depth", comparable_id)] = "5.00"  # true=1.00

        # CASE C: 表5-2 correct (0.00), 表4 deliberately inconsistent (5.00)
        submitted.submitted_totals[f"regional_total_{comparable_id}"] = "0.00"
        submitted.submitted_totals[f"region_adjustment_rate_{comparable_id}"] = "5.00"  # true=0.00

    return submitted
