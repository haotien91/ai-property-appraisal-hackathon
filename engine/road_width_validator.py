# -*- coding: utf-8 -*-
"""
RoadWidthValidator — compares a case's SUBMITTED 主要道路寬度 (what an
appraisal report actually states, 表1's main_road_width field) against
RoadWidthResolver's output, producing AuditIssue findings.

Three distinct non-PASSED outcomes, never conflated (mirrors engine/
land_use_ratio_validator.py's MISSING-vs-WARNING-vs-INCONSISTENT
discipline):
  - ROAD_WIDTH_UNAVAILABLE (issue_type=MISSING): no non-submitted evidence
    at all -- this system cannot verify the submission, not a finding
    against it.
  - ROAD_WIDTH_EVIDENCE_CONFLICT (issue_type=WARNING): 2+ independently-
    sourced evidence entries disagree with each other -- the appraiser's
    submission is NOT judged against an unreliable, self-contradicting
    reference. Never reported as INCONSISTENT.
  - ROAD_WIDTH_INCONSISTENT (issue_type=INCONSISTENT): only when
    RoadWidthResolver reached a single resolved value AND that resolution
    itself does not require manual review (e.g. not from a stale dataset)
    -- "high confidence and semantically consistent", per instruction.
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal, InvalidOperation
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    AuditIssue, IssueType, Severity, CheckType, ExplanationData, RecommendationData,
    RoadWidthResolutionResult, RoadWidthResolutionStatus,
)


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class RoadWidthValidator:
    def validate_main_road_width(
        self, issue_id: str, resolution: RoadWidthResolutionResult, submitted_value,
        source_form: str = "表1", field: str = "main_road_width", label: str = "主要道路寬度",
    ) -> AuditIssue:
        if resolution.status == RoadWidthResolutionStatus.UNAVAILABLE:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, None, CheckType.ROAD_WIDTH_UNAVAILABLE,
                summary="查無可信之非申報道路寬度Evidence（官方屬性／都市計畫／外部地圖／幾何估算皆無），"
                "無法核對申報之主要道路寬度，非申報錯誤",
                rule_citation=None, action="需人工現場勘查或查詢道路寬度官方資料後確認",
                computed_steps=self._evidence_steps(submitted_value, resolution),
            )

        if resolution.status == RoadWidthResolutionStatus.CONFLICT:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.WARNING, source_form, field, label,
                submitted_value, None, CheckType.ROAD_WIDTH_EVIDENCE_CONFLICT,
                summary=resolution.notes or "多來源道路寬度Evidence互相衝突，未自動選定參考值",
                rule_citation=None,
                action="需人工比對各Evidence來源（可能為都市計畫寬度與現況實際寬度本即不同，"
                "非資料錯誤），確認何者適用後再行核對",
                computed_steps=self._evidence_steps(submitted_value, resolution),
            )

        # RESOLVED
        submitted_dec = _to_decimal(submitted_value)
        resolved = resolution.resolved_width_m
        evidence = self._evidence_steps(submitted_value, resolution)

        if submitted_dec is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, str(resolved), CheckType.MISSING,
                summary=f"{label}未申報數值，無法比對", rule_citation=None,
                action=f"請填入{label}後重新審查", computed_steps=evidence,
            )

        if not resolution.requires_manual_review and submitted_dec == resolved:
            return self._issue(
                issue_id, Severity.INFO, IssueType.PASSED, source_form, field, label,
                submitted_value, str(resolved), CheckType.PASSED_CHECK,
                summary=f"申報{label}與{resolution.resolved_evidence.evidence_type.value}"
                "所載數值相符", rule_citation=None, action="無需處理", computed_steps=evidence,
            )

        if not resolution.requires_manual_review and submitted_dec != resolved:
            return self._issue(
                issue_id, Severity.HIGH, IssueType.INCONSISTENT, source_form, field, label,
                submitted_value, str(resolved), CheckType.ROAD_WIDTH_INCONSISTENT,
                summary=f"申報{label}{submitted_value}M與"
                f"{resolution.resolved_evidence.evidence_type.value}參考值{resolved}M不符",
                rule_citation=None,
                action="請核對申報數值或確認是否有道路拓寬等後續變更未即時反映於參考資料",
                computed_steps=evidence,
            )

        # Resolved but the resolution itself flags requires_manual_review
        # (e.g. a stale dataset) -- too low-confidence to assert either
        # PASSED or INCONSISTENT outright.
        return self._issue(
            issue_id, Severity.MEDIUM, IssueType.WARNING, source_form, field, label,
            submitted_value, str(resolved), CheckType.ROAD_WIDTH_EVIDENCE_CONFLICT,
            summary=f"雖已解析出單一道路寬度參考值（{resolved}M），但該筆Evidence本身標記需人工複核"
            "，暫不據以判定申報值是否相符",
            rule_citation=None, action="需人工複核參考Evidence之可信度後再行核對",
            computed_steps=evidence,
        )

    @staticmethod
    def _evidence_steps(submitted_value, resolution: RoadWidthResolutionResult) -> list:
        steps = [f"submitted_value={submitted_value}", f"resolution_status={resolution.status.value}"]
        if resolution.resolved_width_m is not None:
            steps.append(f"resolved_width_m={resolution.resolved_width_m}")
        for e in resolution.evidence:
            steps.append(
                f"evidence: type={e.evidence_type.value}, width_m={e.width_m}, "
                f"source={e.source_name}, dataset_id={e.dataset_id}, dataset_version={e.dataset_version}, "
                f"confidence={e.confidence}, requires_manual_review={e.requires_manual_review}"
            )
        steps.append(f"requires_manual_review={resolution.requires_manual_review}")
        return steps

    def _issue(
        self, issue_id, severity, issue_type, source_form, field, label,
        submitted_value, expected_value, check_type, summary, rule_citation, action,
        computed_steps: Optional[list] = None,
    ) -> AuditIssue:
        return AuditIssue(
            issue_id=issue_id, severity=severity, issue_type=issue_type,
            source_form=source_form, field=field, label=label,
            submitted_value=submitted_value, expected_value=expected_value,
            source="RoadWidthValidator",
            explanation_data=ExplanationData(
                summary=summary, check_type=check_type, rule_citation=rule_citation,
                computed_steps=computed_steps or [],
            ),
            recommendation_data=RecommendationData(
                action=action, suggested_value=expected_value,
                requires_human_review=issue_type != IssueType.PASSED,
            ),
        )
