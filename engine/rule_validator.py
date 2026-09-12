# -*- coding: utf-8 -*-
"""
RuleValidator — re-derives the EXPECTED grade for a factor from its raw
value (via the Phase 3 RuleEngine, the same deterministic source used to
originally fill the form), and compares against the SUBMITTED grade found
on a (possibly erroneous) filled-in form. Produces AuditIssue records.

No LLM. Every judgment is a deterministic re-computation compared against
a submitted value -- this is the direct implementation of the master
project's "LLM負責理解與解釋，Deterministic Engine負責正確性" principle
applied to auditing rather than original form-filling.
"""
from __future__ import annotations
import sys
import os
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from rule_engine import RuleEngine, RuleNotFoundError, WrongUnitError, AmbiguousFactorError  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    AuditIssue, Severity, IssueType, CheckType, ExplanationData, RecommendationData,
)


class RuleValidator:
    # Evidence-limited whitelist (Grade Representation Contract, Phase D
    # guardrail 1): 評價基準明細表範例 explicitly defines these 2 factors'
    # grade_label ("無"/"有") as the SAME canonical judgment as their
    # grade ("優"/"劣") -- confirmed via direct user-supplied citation of
    # the source table (優：無 / 劣：有 for both 有無禁止建築 and 有無限制
    #建築), not inferred from the 2 Golden Case rows alone. NO other
    # factor has this evidence in this repo -- grade_label MUST NOT be
    # treated as an accepted submitted-text representation for them; only
    # expected.grade is canonical for the remaining 26 factors. Extending
    # this set requires the SAME kind of direct source-table citation, not
    # a pattern inferred from value_type or from how many rows happen to
    # match in one document.
    _GRADE_LABEL_ALSO_VALID_TEXT_FOR = frozenset({
        "regional_construction_prohibited",
        "regional_construction_restricted",
    })

    def __init__(self, rule_engine: RuleEngine, dependency_analyzer=None):
        self._engine = rule_engine
        self._dep = dependency_analyzer

    def validate_grade(
        self, issue_id: str, source_form: str, field_id: str, label: str,
        city: str, district: str, land_use_type: str, factor: str,
        raw_value, unit, rule_set: str, submitted_grade: Optional[str],
    ) -> AuditIssue:
        """Compares submitted_grade against the grade the RuleEngine
        deterministically derives from raw_value. Handles: correct match
        (Passed), mismatch (Error/GRADE_ERROR), missing submission
        (Missing), wrong unit (Error/WRONG_UNIT), rule not found
        (Error/RULE_NOT_FOUND)."""
        downstream = self._dep.analyze(field_id) if self._dep else []

        if submitted_grade is None:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form=source_form, field=field_id, label=label,
                submitted_value=None, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label} 未填寫優劣等級", check_type=CheckType.MISSING,
                ),
                recommendation_data=RecommendationData(
                    action="請依評價基準明細表填寫優劣等級", requires_human_review=True,
                ),
                downstream_impact=downstream,
            )

        try:
            expected = self._engine.grade(city, district, land_use_type, factor, raw_value, unit=unit, rule_set=rule_set)
        except WrongUnitError as e:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label} 單位不符，無法核算等級：{e}", check_type=CheckType.WRONG_UNIT,
                ),
                recommendation_data=RecommendationData(action="請確認原始資料單位是否正確", requires_human_review=True),
                downstream_impact=downstream,
            )
        except (RuleNotFoundError, AmbiguousFactorError) as e:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label} 找不到對應規則：{e}", check_type=CheckType.RULE_NOT_FOUND,
                ),
                recommendation_data=RecommendationData(action="請人工確認評價基準明細表是否涵蓋此數值", requires_human_review=True),
                downstream_impact=downstream,
            )

        if submitted_grade == expected.grade:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade, expected_value=expected.grade, rule_id=expected.rule_id,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label} 優劣等級正確", check_type=CheckType.PASSED_CHECK,
                    rule_citation=f"{expected.matched_rule['source_document']} {expected.matched_rule['source_page']}",
                ),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )

        return AuditIssue(
            issue_id=issue_id, severity=Severity.CRITICAL if downstream else Severity.HIGH,
            issue_type=IssueType.ERROR,
            source_form=source_form, field=field_id, label=label,
            submitted_value=submitted_grade, expected_value=expected.grade, rule_id=expected.rule_id,
            source="RuleValidator",
            explanation_data=ExplanationData(
                summary=f"{label} 優劣等級錯誤：原始值{raw_value}{unit or ''}應判定為「{expected.grade}」，"
                        f"表單填載為「{submitted_grade}」",
                check_type=CheckType.GRADE_ERROR,
                rule_citation=f"{expected.matched_rule['source_document']} {expected.matched_rule['source_page']}",
                computed_steps=[
                    f"raw_value={raw_value}{unit or ''}",
                    f"依評價基準明細表級距判定 -> {expected.grade}（{expected.grade_label}）",
                    f"表單實際填載 -> {submitted_grade}（不符）",
                ],
            ),
            recommendation_data=RecommendationData(
                action="訂正優劣等級為系統核算結果", suggested_value=expected.grade, requires_human_review=True,
            ),
            downstream_impact=downstream,
        )

    # ------------------------------------------------------------------
    # Grade Representation Contract (Phase D) -- Check A (grade identity,
    # code vs code) and Check B (grade representation, code+text self-
    # consistency). Both are ADDITIVE to validate_grade() above, which is
    # left completely unmodified (still used exactly as before by any
    # caller that wants text-vs-canonical-grade comparison; its own 6
    # direct unit tests are unaffected). _review_regional_factors() in
    # engine/audit_engine.py is the only caller of these two new methods.
    # ------------------------------------------------------------------

    def validate_grade_identity(
        self, issue_id: str, source_form: str, field_id: str, label: str,
        city: str, district: str, land_use_type: str, factor: str,
        raw_value, unit, rule_set: str, submitted_grade_code: Optional[str],
    ) -> AuditIssue:
        """Check A: compares submitted_grade_code against the grade_code
        the RuleEngine deterministically derives from raw_value -- the
        PRIMARY, valuation-critical correctness check (supersedes text-
        based comparison for regional factors: comparing codes sidesteps
        the representation-vocabulary question entirely, see the Grade
        Representation Contract audit). submitted_grade_code is NEVER
        normalized/derived from submitted_grade_text here -- if the code
        cell was not extracted, this is MISSING, not a fallback guess from
        text."""
        downstream = self._dep.analyze(field_id) if self._dep else []

        if submitted_grade_code is None:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form=source_form, field=field_id, label=label,
                submitted_value=None, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}未填寫優劣等級代碼", check_type=CheckType.MISSING,
                ),
                recommendation_data=RecommendationData(
                    action="請依評價基準明細表填寫優劣等級代碼", requires_human_review=True,
                ),
                downstream_impact=downstream,
            )

        try:
            expected = self._engine.grade(city, district, land_use_type, factor, raw_value, unit=unit, rule_set=rule_set)
        except WrongUnitError as e:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade_code, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}單位不符，無法核算等級：{e}", check_type=CheckType.WRONG_UNIT,
                ),
                recommendation_data=RecommendationData(action="請確認原始資料單位是否正確", requires_human_review=True),
                downstream_impact=downstream,
            )
        except (RuleNotFoundError, AmbiguousFactorError) as e:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade_code, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}找不到對應規則：{e}", check_type=CheckType.RULE_NOT_FOUND,
                ),
                recommendation_data=RecommendationData(action="請人工確認評價基準明細表是否涵蓋此數值", requires_human_review=True),
                downstream_impact=downstream,
            )

        if str(submitted_grade_code) == str(expected.grade_code):
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form=source_form, field=field_id, label=label,
                submitted_value=str(submitted_grade_code), expected_value=str(expected.grade_code),
                rule_id=expected.rule_id, source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}正確", check_type=CheckType.PASSED_CHECK,
                    rule_citation=f"{expected.matched_rule['source_document']} {expected.matched_rule['source_page']}",
                ),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )

        return AuditIssue(
            issue_id=issue_id, severity=Severity.CRITICAL if downstream else Severity.HIGH,
            issue_type=IssueType.ERROR,
            source_form=source_form, field=field_id, label=label,
            submitted_value=str(submitted_grade_code), expected_value=str(expected.grade_code),
            rule_id=expected.rule_id, source="RuleValidator",
            explanation_data=ExplanationData(
                summary=f"{label}錯誤：原始值{raw_value}{unit or ''}應判定為代碼「{expected.grade_code}」"
                        f"（{expected.grade}），表單填載代碼為「{submitted_grade_code}」",
                check_type=CheckType.GRADE_ERROR,
                rule_citation=f"{expected.matched_rule['source_document']} {expected.matched_rule['source_page']}",
                computed_steps=[
                    f"raw_value={raw_value}{unit or ''}",
                    f"依評價基準明細表級距判定 -> grade_code={expected.grade_code}（{expected.grade}）",
                    f"表單實際填載代碼 -> {submitted_grade_code}（不符）",
                ],
            ),
            recommendation_data=RecommendationData(
                action="訂正優劣等級代碼為系統核算結果", suggested_value=str(expected.grade_code),
                requires_human_review=True,
            ),
            downstream_impact=downstream,
        )

    def validate_grade_representation(
        self, issue_id: str, source_form: str, field_id: str, label: str, canonical_field_id: str,
        city: str, district: str, land_use_type: str, factor: str, rule_set: str,
        submitted_grade_code: Optional[str], submitted_grade_text: Optional[str],
    ) -> AuditIssue:
        """Check B: is submitted_grade_text a valid representation of
        submitted_grade_code (per THIS factor's own rule rows), never
        whether it matches the EXPECTED (raw-value-derived) code -- these
        are orthogonal by design: a wrong code with a self-consistent
        text is CONSISTENT here (the representation is fine even though
        the underlying grade is wrong, per Check A); a correct code with
        a text that doesn't match it is GRADE_REPRESENTATION_INCONSISTENT
        here even though Check A would PASS. Never rewrites submitted_
        grade_text -- only reports whether it belongs to the allowed set."""
        downstream = self._dep.analyze(field_id) if self._dep else []

        if submitted_grade_code is None or submitted_grade_text is None:
            missing = "優劣等級代碼" if submitted_grade_code is None else "優劣等級文字"
            return AuditIssue(
                issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.MISSING,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade_text, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}無法檢查：未填寫{missing}", check_type=CheckType.MISSING,
                ),
                recommendation_data=RecommendationData(
                    action="請依評價基準明細表同時填寫優劣等級代碼與文字", requires_human_review=True,
                ),
                downstream_impact=downstream,
            )

        try:
            ni = self._engine.normalize(city, district, land_use_type, factor, None, unit=None, rule_set=rule_set)
            candidates = self._engine.select_rules(ni)
        except (RuleNotFoundError, AmbiguousFactorError) as e:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade_text, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}找不到對應規則：{e}", check_type=CheckType.RULE_NOT_FOUND,
                ),
                recommendation_data=RecommendationData(action="請人工確認評價基準明細表是否涵蓋此數值", requires_human_review=True),
                downstream_impact=downstream,
            )

        matched_row = next((r for r in candidates if str(r["grade_code"]) == str(submitted_grade_code)), None)
        if matched_row is None:
            # Distinct from MISSING (guardrail: invalid code != missing
            # code) -- the code WAS submitted, it just does not correspond
            # to any grade band this factor's rules define. Never falls
            # back to guessing a grade from submitted_grade_text.
            return AuditIssue(
                issue_id=issue_id, severity=Severity.HIGH, issue_type=IssueType.ERROR,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade_code, expected_value=None, rule_id=None,
                source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}提交之優劣等級代碼「{submitted_grade_code}」非本因素合法代碼",
                    check_type=CheckType.GRADE_CODE_INVALID,
                ),
                recommendation_data=RecommendationData(action="請確認優劣等級代碼是否填寫錯誤", requires_human_review=True),
                downstream_impact=downstream,
            )

        allowed = {matched_row["grade"]}
        if canonical_field_id in self._GRADE_LABEL_ALSO_VALID_TEXT_FOR:
            allowed.add(matched_row["grade_label"])

        if submitted_grade_text in allowed:
            return AuditIssue(
                issue_id=issue_id, severity=Severity.INFO, issue_type=IssueType.PASSED,
                source_form=source_form, field=field_id, label=label,
                submitted_value=submitted_grade_text, expected_value=matched_row["grade"],
                rule_id=matched_row["rule_id"], source="RuleValidator",
                explanation_data=ExplanationData(
                    summary=f"{label}文字表述與代碼一致", check_type=CheckType.PASSED_CHECK,
                    rule_citation=f"{matched_row['source_document']} {matched_row['source_page']}",
                ),
                recommendation_data=RecommendationData(action="無需處理", requires_human_review=False),
                downstream_impact=downstream,
            )

        return AuditIssue(
            issue_id=issue_id, severity=Severity.MEDIUM, issue_type=IssueType.WARNING,
            source_form=source_form, field=field_id, label=label,
            submitted_value=submitted_grade_text, expected_value=matched_row["grade"],
            rule_id=matched_row["rule_id"], source="RuleValidator",
            explanation_data=ExplanationData(
                summary=f"{label}文字表述與代碼不一致：代碼「{submitted_grade_code}」允許之文字為"
                        f"{sorted(allowed)}，表單填載文字為「{submitted_grade_text}」",
                check_type=CheckType.GRADE_REPRESENTATION_INCONSISTENT,
                rule_citation=f"{matched_row['source_document']} {matched_row['source_page']}",
                computed_steps=[
                    f"submitted_grade_code={submitted_grade_code}",
                    f"該代碼允許之文字表述 -> {sorted(allowed)}",
                    f"表單實際填載文字 -> {submitted_grade_text}（不在允許範圍）",
                ],
            ),
            recommendation_data=RecommendationData(
                action="請人工確認優劣等級代碼與文字何者為appraiser真正判定，不由系統自動判斷",
                requires_human_review=True,
            ),
            downstream_impact=downstream,
        )
