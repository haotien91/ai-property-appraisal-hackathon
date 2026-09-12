# -*- coding: utf-8 -*-
"""
CalculationValidator — re-derives EXPECTED adjustment/subtotal/total/price
values via Phase 3/4's RuleEngine+CalculationEngine, compares against
SUBMITTED values found on a filled-in form. Covers Adjustment Error,
Subtotal Error, and Total Error from the Phase 6 REQUIRED CHECKS list.
"""
from __future__ import annotations
import sys
import os
from decimal import Decimal, InvalidOperation
from typing import Optional, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from rule_engine import RuleEngine, GradeNotComparableError, RuleNotFoundError  # noqa: E402
from rule_engine import GradeResult as EngineGradeResult  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    AuditIssue, Severity, IssueType, CheckType, ExplanationData, RecommendationData,
)


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class CalculationValidator:
    def __init__(self, rule_engine: RuleEngine, dependency_analyzer=None):
        self._engine = rule_engine
        self._dep = dependency_analyzer

    # ------------------------------------------------------------------
    # Adjustment Error: matrix[base_grade][comp_grade] vs submitted
    # ------------------------------------------------------------------
    def validate_adjustment(
        self, issue_id: str, source_form: str, field_id: str, label: str,
        factor: str, base_grade_code: int, base_grade: str,
        comp_grade_code: int, comp_grade: str, matrix_rule_id: str, matrix: dict,
        submitted_adjustment,
    ) -> AuditIssue:
        downstream = self._dep.analyze(field_id) if self._dep else []
        row_key, col_key = str(base_grade_code), str(comp_grade_code)
        if row_key not in matrix or col_key not in matrix[row_key]:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=str(submitted_adjustment) if submitted_adjustment is not None else None,
                expected_value=None, rule_id=matrix_rule_id, source="CalculationValidator",
                explanation_data=ExplanationData(
                    summary=f"{label} 無法於修正率矩陣中找到 grade_code {row_key}/{col_key}",
                    check_type=CheckType.RULE_NOT_FOUND,
                ),
                recommendation_data=RecommendationData(action="人工確認等級代碼", requires_human_review=True),
                downstream_impact=downstream,
            )
        expected_pct = Decimal(str(matrix[row_key][col_key]))

        if submitted_adjustment is None:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form=source_form, field=field_id, label=label,
                submitted_value=None, expected_value=str(expected_pct), rule_id=matrix_rule_id,
                source="CalculationValidator",
                explanation_data=ExplanationData(summary=f"{label} 未填寫修正百分比", check_type=CheckType.MISSING),
                recommendation_data=RecommendationData(
                    action="請填入系統核算值", suggested_value=str(expected_pct), requires_human_review=True,
                ),
                downstream_impact=downstream,
            )

        try:
            submitted_pct = _d(submitted_adjustment)
        except InvalidOperation:
            submitted_pct = None

        if submitted_pct == expected_pct:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form=source_form, field=field_id, label=label,
                submitted_value=str(submitted_adjustment), expected_value=str(expected_pct), rule_id=matrix_rule_id,
                source="CalculationValidator",
                explanation_data=ExplanationData(summary=f"{label} 修正百分比正確", check_type=CheckType.PASSED_CHECK),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )

        return AuditIssue(
            issue_id=issue_id, severity=Severity.CRITICAL if downstream else Severity.HIGH,
            issue_type=IssueType.ERROR,
            source_form=source_form, field=field_id, label=label,
            submitted_value=str(submitted_adjustment), expected_value=str(expected_pct), rule_id=matrix_rule_id,
            source="CalculationValidator",
            explanation_data=ExplanationData(
                summary=f"{label} 修正百分比錯誤：比準地={base_grade}、比較標的={comp_grade}，"
                        f"矩陣查表應為{expected_pct}%，表單填載為{submitted_adjustment}%",
                check_type=CheckType.ADJUSTMENT_ERROR,
                computed_steps=[
                    f"matrix[{row_key}][{col_key}] = {expected_pct}",
                    f"表單實際填載 = {submitted_adjustment}（不符）",
                ],
            ),
            recommendation_data=RecommendationData(
                action="訂正修正百分比為系統核算結果", suggested_value=str(expected_pct), requires_human_review=True,
            ),
            downstream_impact=downstream,
        )

    # ------------------------------------------------------------------
    # Subtotal / Total Error: sum(components) vs submitted
    # ------------------------------------------------------------------
    def validate_sum(
        self, issue_id: str, source_form: str, field_id: str, label: str,
        component_values: List, submitted_total, check_type: CheckType,
    ) -> AuditIssue:
        downstream = self._dep.analyze(field_id) if self._dep else []
        expected_total = sum((_d(v) for v in component_values), Decimal("0"))

        if submitted_total is None:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form=source_form, field=field_id, label=label,
                submitted_value=None, expected_value=str(expected_total), rule_id=None,
                source="CalculationValidator",
                explanation_data=ExplanationData(summary=f"{label} 未填寫", check_type=CheckType.MISSING),
                recommendation_data=RecommendationData(
                    action="請填入系統核算值", suggested_value=str(expected_total), requires_human_review=True,
                ),
                downstream_impact=downstream,
            )

        submitted_d = _d(submitted_total)
        if submitted_d == expected_total:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form=source_form, field=field_id, label=label,
                submitted_value=str(submitted_total), expected_value=str(expected_total), rule_id=None,
                source="CalculationValidator",
                explanation_data=ExplanationData(summary=f"{label} 加總正確", check_type=CheckType.PASSED_CHECK),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )

        return AuditIssue(
            issue_id=issue_id, severity=Severity.CRITICAL if downstream else Severity.HIGH,
            issue_type=IssueType.ERROR,
            source_form=source_form, field=field_id, label=label,
            submitted_value=str(submitted_total), expected_value=str(expected_total), rule_id=None,
            source="CalculationValidator",
            explanation_data=ExplanationData(
                summary=f"{label} 加總錯誤：各分項合計應為{expected_total}，表單填載為{submitted_total}",
                check_type=check_type,
                computed_steps=[f"sum({[str(_d(v)) for v in component_values]}) = {expected_total}",
                                f"表單實際填載 = {submitted_total}（不符）"],
            ),
            recommendation_data=RecommendationData(
                action="訂正為系統核算之加總結果", suggested_value=str(expected_total), requires_human_review=True,
            ),
            downstream_impact=downstream,
        )
