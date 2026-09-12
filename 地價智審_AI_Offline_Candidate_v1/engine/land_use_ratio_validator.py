# -*- coding: utf-8 -*-
"""
LandUseRatioValidator — compares a case's SUBMITTED 建蔽率／容積率 (what an
appraisal report actually states, e.g. 表1's building_coverage_ratio/
floor_area_ratio fields) against the reference value LandUseRatioEngine
resolves from official sources, producing AuditIssue findings.

Critical distinction this module exists to enforce: "查不到官方規則" is NOT
the same finding as "申報值與官方值不符". The former means this system
cannot verify the submission at all (CheckType.LAND_USE_RATIO_RULE_
UNAVAILABLE / ZONING_PLAN_UNRESOLVED / ZONE_CATEGORY_NORMALIZATION_
UNCERTAIN, issue_type=MISSING or WARNING) -- it must never be reported as
issue_type=INCONSISTENT or otherwise implied to be a regulatory violation.
Only a genuine, resolved reference value that numerically differs from the
submission becomes CheckType.BUILDING_COVERAGE_RATE_INCONSISTENT /
FLOOR_AREA_RATIO_INCONSISTENT (issue_type=INCONSISTENT).

This module is a standalone, independently testable validator (same
constructor-injection pattern as engine/rule_validator.py and engine/
calculation_validator.py), wired into AuditEngine.review() as a case-level
(not per-comparable) check -- see engine/audit_engine.py.

Every branch that has resolved a LandUseRatioResolutionResult (or a
ZoneNameNormalizationResult) records the full evidence chain into
explanation_data.computed_steps -- resolution_layer, legal_source,
article_or_section, source_url, source_document_checksum, dataset_version,
official_plan_name, requires_manual_review, normalized_zone_category -- so a
human reviewer never has to re-derive where a number came from.
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal, InvalidOperation
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    AuditIssue, IssueType, Severity, CheckType, ExplanationData, RecommendationData,
)
from engine.land_use_ratio_engine import LandUseRatioEngine  # noqa: E402
from engine.zone_name_normalizer import normalize_zone_name  # noqa: E402


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _evidence_steps(
    submitted_value=None, official_raw_zone_name: Optional[str] = None,
    normalized_zone_category: Optional[str] = None, confirmed_plan_name: Optional[str] = None,
    internal_plan_id: Optional[str] = None, plan_identification_source: Optional[str] = None,
    result=None,
) -> list:
    """Builds the human-readable evidence trail required by every branch
    that has resolved at least a zone name or a LandUseRatioResolutionResult.
    `result` is an Optional[LandUseRatioResolutionResult]."""
    steps = [f"submitted_value={submitted_value}"]
    if official_raw_zone_name is not None:
        steps.append(f"official_raw_zone_name={official_raw_zone_name}")
    if normalized_zone_category is not None:
        steps.append(f"normalized_zone_category={normalized_zone_category}")
    if confirmed_plan_name is not None:
        steps.append(f"confirmed_plan_name={confirmed_plan_name}")
    if internal_plan_id is not None:
        steps.append(f"internal_plan_id={internal_plan_id}")
    if plan_identification_source is not None:
        steps.append(f"plan_identification_source={plan_identification_source}")
    if result is not None:
        steps.append(f"resolved_value={result.resolved_value_pct}")
        steps.append(f"resolution_layer={result.resolution_layer}")
        if result.official_plan_name is not None:
            steps.append(f"official_plan_name={result.official_plan_name}")
        if result.legal_source is not None:
            steps.append(f"legal_source={result.legal_source}")
        if result.article_or_section is not None:
            steps.append(f"article_or_section={result.article_or_section}")
        if result.source_url is not None:
            steps.append(f"source_url={result.source_url}")
        if result.source_document_checksum is not None:
            steps.append(f"source_document_checksum={result.source_document_checksum}")
        if result.dataset_version is not None:
            steps.append(f"dataset_version={result.dataset_version}")
        steps.append(f"requires_manual_review={result.requires_manual_review}")
    return steps


class LandUseRatioValidator:
    def __init__(self, ratio_engine: Optional[LandUseRatioEngine] = None):
        self._engine = ratio_engine or LandUseRatioEngine()

    def validate_building_coverage_rate(
        self, issue_id: str, official_raw_zone_name: Optional[str], submitted_value,
        source_form: str = "表1", field: str = "building_coverage_ratio", label: str = "建蔽率",
    ) -> AuditIssue:
        if official_raw_zone_name is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, None, CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE,
                summary="查無都市計畫使用分區資料，無法核對建蔽率是否與官方規定相符",
                rule_citation=None, action="需人工確認本案土地使用分區並自行核對建蔽率",
                computed_steps=_evidence_steps(submitted_value=submitted_value),
            )

        norm = normalize_zone_name(official_raw_zone_name)
        if norm.normalized_zone_category is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.WARNING, source_form, field, label,
                submitted_value, None, CheckType.ZONE_CATEGORY_NORMALIZATION_UNCERTAIN,
                summary=f"官方分區名稱「{official_raw_zone_name}」無法可靠歸類至都市計畫法新北市"
                "施行細則附表一之標準分區類別，無法核對建蔽率",
                rule_citation="都市計畫法新北市施行細則附表一",
                action="需人工確認本分區之正確歸類（可能屬附表三公共設施用地，本系統未涵蓋）",
                computed_steps=_evidence_steps(
                    submitted_value=submitted_value, official_raw_zone_name=official_raw_zone_name,
                ),
            )

        result = self._engine.resolve_building_coverage_rate(norm.normalized_zone_category)
        evidence = _evidence_steps(
            submitted_value=submitted_value, official_raw_zone_name=official_raw_zone_name,
            normalized_zone_category=norm.normalized_zone_category, result=result,
        )
        if result.resolved_value_pct is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, None, CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE,
                summary=f"分區「{norm.normalized_zone_category}」查無官方固定建蔽率數值，"
                "非申報錯誤，僅代表本系統無法自動核對",
                rule_citation="都市計畫法新北市施行細則附表一", action="需人工查證建蔽率是否符合規定",
                computed_steps=evidence,
            )

        return self._compare(
            issue_id, source_form, field, label, submitted_value, result,
            CheckType.BUILDING_COVERAGE_RATE_INCONSISTENT,
            rule_citation="都市計畫法新北市施行細則第三十六條、附表一",
            passed_summary=f"申報建蔽率與附表一分區「{norm.normalized_zone_category}」規定之上限相符",
            mismatch_summary_prefix="申報建蔽率", evidence=evidence,
        )

    def validate_floor_area_ratio(
        self, issue_id: str, official_raw_zone_name: Optional[str], plan_id: Optional[str],
        submitted_value, source_form: str = "表1", field: str = "floor_area_ratio", label: str = "容積率",
        confirmed_plan_name: Optional[str] = None, plan_identification_source: Optional[str] = None,
    ) -> AuditIssue:
        if official_raw_zone_name is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, None, CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE,
                summary="查無都市計畫使用分區資料，無法核對容積率是否與官方規定相符",
                rule_citation=None, action="需人工確認本案土地使用分區並自行核對容積率",
                computed_steps=_evidence_steps(submitted_value=submitted_value),
            )

        if plan_id is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.WARNING, source_form, field, label,
                submitted_value, None, CheckType.ZONING_PLAN_UNRESOLVED,
                summary="未提供本案所屬都市計畫（plan_id），無法核對容積率——新北市都市計畫範圍圖資"
                "之計畫名稱欄位於來源端已損毀，本系統無法自動由座標判定所屬都市計畫",
                rule_citation=None,
                action="需人工提供本案實際所屬都市計畫，或逕行查詢該都市計畫土地使用分區管制要點",
                computed_steps=_evidence_steps(
                    submitted_value=submitted_value, official_raw_zone_name=official_raw_zone_name,
                ),
            )

        norm = normalize_zone_name(official_raw_zone_name)
        result = self._engine.resolve_floor_area_ratio(
            official_raw_zone_name, plan_id=plan_id,
            normalized_zone_category=norm.normalized_zone_category,
        )
        evidence = _evidence_steps(
            submitted_value=submitted_value, official_raw_zone_name=official_raw_zone_name,
            normalized_zone_category=norm.normalized_zone_category,
            confirmed_plan_name=confirmed_plan_name, internal_plan_id=plan_id,
            plan_identification_source=plan_identification_source, result=result,
        )
        if result.resolved_value_pct is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, None, CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE,
                summary=f"都市計畫「{plan_id}」之分區「{official_raw_zone_name}」查無官方容積率"
                "規則，非申報錯誤，僅代表本系統無法自動核對",
                rule_citation="個別都市計畫土地使用分區管制要點", action="需人工查證容積率是否符合規定",
                computed_steps=evidence,
            )

        rule_citation = (
            "個別都市計畫土地使用分區管制要點" if result.resolution_layer == "PLAN_SPECIFIC"
            else "都市計畫法新北市施行細則第三十六條、附表一"
        )
        return self._compare(
            issue_id, source_form, field, label, submitted_value, result,
            CheckType.FLOOR_AREA_RATIO_INCONSISTENT, rule_citation=rule_citation,
            passed_summary=f"申報容積率與{rule_citation}所載數值相符",
            mismatch_summary_prefix="申報容積率", evidence=evidence,
        )

    def _compare(
        self, issue_id, source_form, field, label, submitted_value, result,
        check_type: CheckType, rule_citation: str, passed_summary: str, mismatch_summary_prefix: str,
        evidence: list,
    ) -> AuditIssue:
        resolved_value_pct: Decimal = result.resolved_value_pct
        submitted_dec = _to_decimal(submitted_value)
        if submitted_dec is None:
            return self._issue(
                issue_id, Severity.MEDIUM, IssueType.MISSING, source_form, field, label,
                submitted_value, str(resolved_value_pct), CheckType.MISSING,
                summary=f"{label}未申報數值，無法比對", rule_citation=rule_citation,
                action=f"請填入{label}後重新審查", computed_steps=evidence,
            )
        if submitted_dec != resolved_value_pct:
            return self._issue(
                issue_id, Severity.HIGH, IssueType.INCONSISTENT, source_form, field, label,
                submitted_value, str(resolved_value_pct), check_type,
                summary=f"{mismatch_summary_prefix}{submitted_value}%與官方參考值"
                f"{resolved_value_pct}%不符", rule_citation=rule_citation,
                action="請核對申報數值或確認是否有官方核准之特殊情形（如都市設計審議增額容積）",
                computed_steps=evidence,
            )
        return self._issue(
            issue_id, Severity.INFO, IssueType.PASSED, source_form, field, label,
            submitted_value, str(resolved_value_pct), CheckType.PASSED_CHECK,
            summary=passed_summary, rule_citation=rule_citation, action="無需處理",
            computed_steps=evidence,
        )

    def _issue(
        self, issue_id, severity, issue_type, source_form, field, label,
        submitted_value, expected_value, check_type, summary, rule_citation, action,
        computed_steps: Optional[list] = None,
    ) -> AuditIssue:
        return AuditIssue(
            issue_id=issue_id, severity=severity, issue_type=issue_type,
            source_form=source_form, field=field, label=label,
            submitted_value=submitted_value, expected_value=expected_value,
            source="LandUseRatioValidator",
            explanation_data=ExplanationData(
                summary=summary, check_type=check_type, rule_citation=rule_citation,
                computed_steps=computed_steps or [],
            ),
            recommendation_data=RecommendationData(
                action=action, suggested_value=expected_value,
                requires_human_review=issue_type != IssueType.PASSED,
            ),
        )
