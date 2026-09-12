# -*- coding: utf-8 -*-
"""
CrossFormValidationEngine — checks that values which must be IDENTICAL
across two forms (per the official審查checklist, docs/phase1/evidence_matrix.md
REQ-023 and docs/phase2/form_dependency.md DEP-03) actually agree. The
canonical example is 表5-2's 影響地價區域因素總修正數 vs 表4's 區域因素
調整百分率 -- these are the SAME number, filled twice (once derived, once
carried over), and a mismatch is a direct signal of a transcription error
or tampering, independent of whether either individual number is itself
"correct" in isolation.
"""
from __future__ import annotations
import sys
import os
from decimal import Decimal, InvalidOperation
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    AuditIssue, Severity, IssueType, CheckType, ExplanationData, RecommendationData,
)


def _d(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


class CrossFormValidationEngine:
    def __init__(self, dependency_analyzer=None):
        self._dep = dependency_analyzer

    def validate_regional_total_matches_table4(
        self, issue_id: str, comparable_id: str,
        table5_2_regional_total, table4_region_adjustment_rate,
    ) -> AuditIssue:
        """The canonical Phase 6 Demo Error Case C check: 表5-2影響地價區域
        因素總修正數 必須逐字等於 表4區域因素調整百分率。"""
        field_id = f"region_adjustment_rate_{comparable_id}"
        downstream = self._dep.analyze(field_id) if self._dep else []

        v52 = _d(table5_2_regional_total)
        v4 = _d(table4_region_adjustment_rate)

        if v52 is None or v4 is None:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form="表4", field=field_id, label="區域因素調整百分率（跨表一致性）",
                submitted_value=table4_region_adjustment_rate, expected_value=table5_2_regional_total,
                rule_id=None, source="CrossFormValidationEngine",
                explanation_data=ExplanationData(
                    summary="表5-2或表4之區域因素修正數值缺失，無法比對", check_type=CheckType.MISSING,
                ),
                recommendation_data=RecommendationData(action="請確認兩表皆已填寫", requires_human_review=True),
                downstream_impact=downstream,
            )

        if v52 == v4:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form="表4", field=field_id, label="區域因素調整百分率（跨表一致性）",
                submitted_value=str(v4), expected_value=str(v52), rule_id=None,
                source="CrossFormValidationEngine",
                explanation_data=ExplanationData(
                    summary="表5-2影響地價區域因素總修正數與表4區域因素調整百分率一致",
                    check_type=CheckType.PASSED_CHECK,
                    rule_citation="作業手冊 p.48「(4)」",
                ),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )

        return AuditIssue(
            issue_id=issue_id, severity=Severity.CRITICAL, issue_type=IssueType.INCONSISTENT,
            source_form="表4", field=field_id, label="區域因素調整百分率（跨表一致性）",
            submitted_value=str(v4), expected_value=str(v52), rule_id=None,
            source="CrossFormValidationEngine",
            explanation_data=ExplanationData(
                summary=f"跨表不一致：表5-2「影響地價區域因素總修正數」={v52}%，"
                        f"表4「區域因素調整百分率」={v4}%，兩者應逐字相符但不一致",
                check_type=CheckType.CROSS_FORM_INCONSISTENT,
                rule_citation="作業手冊 p.48「(4)」；docs/phase1/evidence_matrix.md REQ-023（官方審查checklist核心檢查點）",
                computed_steps=[f"表5-2記載值 = {v52}%", f"表4記載值 = {v4}%", "兩值不相等 -> Cross-form Inconsistent"],
            ),
            recommendation_data=RecommendationData(
                action="請確認表4是否正確承接表5-2之總修正數，並訂正其中一方", requires_human_review=True,
            ),
            downstream_impact=downstream,
        )

    def validate_field_pair(
        self, issue_id: str, source_form: str, field_id: str, label: str,
        value_a, value_b, rule_citation: str,
    ) -> AuditIssue:
        """Generic reusable pair-consistency check for any other pair of
        fields that must agree across forms (e.g. 案號/區段編號/使用分區
        appearing identically on multiple forms' headers)."""
        downstream = self._dep.analyze(field_id) if self._dep else []
        if str(value_a) == str(value_b):
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form=source_form, field=field_id, label=label,
                submitted_value=str(value_b), expected_value=str(value_a), rule_id=None,
                source="CrossFormValidationEngine",
                explanation_data=ExplanationData(summary=f"{label} 跨表一致", check_type=CheckType.PASSED_CHECK),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )
        return AuditIssue(
            issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.INCONSISTENT,
            source_form=source_form, field=field_id, label=label,
            submitted_value=str(value_b), expected_value=str(value_a), rule_id=None,
            source="CrossFormValidationEngine",
            explanation_data=ExplanationData(
                summary=f"{label} 跨表不一致：{value_a} vs {value_b}",
                check_type=CheckType.CROSS_FORM_INCONSISTENT, rule_citation=rule_citation,
            ),
            recommendation_data=RecommendationData(action="請確認兩表資料是否一致", requires_human_review=True),
            downstream_impact=downstream,
        )
