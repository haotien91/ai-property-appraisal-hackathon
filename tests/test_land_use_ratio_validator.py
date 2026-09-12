# -*- coding: utf-8 -*-
"""
Tests for engine/land_use_ratio_validator.py -- the core distinction this
module exists to enforce: "查無官方規則" (can't verify) must never be
reported the same way as "申報值與官方值不符" (confirmed mismatch). The
former is IssueType.MISSING/WARNING; only the latter is IssueType.
INCONSISTENT.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import IssueType, Severity, CheckType  # noqa: E402
from engine.land_use_ratio_validator import LandUseRatioValidator  # noqa: E402


@pytest.fixture(scope="module")
def validator():
    return LandUseRatioValidator()


class TestBuildingCoverageRate:
    def test_matching_submission_is_passed(self, validator):
        issue = validator.validate_building_coverage_rate("ISS-1", "第二種商業區", 70)
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK
        assert issue.recommendation_data.requires_human_review is False

    def test_mismatched_submission_is_inconsistent_not_error(self, validator):
        issue = validator.validate_building_coverage_rate("ISS-2", "第二種商業區", 65)
        assert issue.issue_type == IssueType.INCONSISTENT
        assert issue.severity == Severity.HIGH
        assert issue.explanation_data.check_type == CheckType.BUILDING_COVERAGE_RATE_INCONSISTENT
        assert issue.submitted_value == 65
        assert issue.expected_value == "70"
        assert issue.recommendation_data.requires_human_review is True

    def test_unclassifiable_zone_is_warning_not_inconsistent(self, validator):
        """道路用地無法歸類——查無規則不是申報錯誤，不得標為法規違反。"""
        issue = validator.validate_building_coverage_rate("ISS-3", "道路用地", 70)
        assert issue.issue_type == IssueType.WARNING
        assert issue.issue_type != IssueType.INCONSISTENT
        assert issue.explanation_data.check_type == CheckType.ZONE_CATEGORY_NORMALIZATION_UNCERTAIN

    def test_no_zone_name_is_missing_not_inconsistent(self, validator):
        issue = validator.validate_building_coverage_rate("ISS-4", None, 70)
        assert issue.issue_type == IssueType.MISSING
        assert issue.issue_type != IssueType.INCONSISTENT
        assert issue.explanation_data.check_type == CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE

    def test_no_submitted_value_is_missing(self, validator):
        issue = validator.validate_building_coverage_rate("ISS-5", "第二種商業區", None)
        assert issue.issue_type == IssueType.MISSING


class TestFloorAreaRatio:
    def test_jinshan_matching_submission_is_passed(self, validator):
        issue = validator.validate_floor_area_ratio("ISS-6", "第二種商業區", "jinshan", 240)
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK

    def test_jinshan_mismatched_submission_is_inconsistent(self, validator):
        issue = validator.validate_floor_area_ratio("ISS-7", "第二種商業區", "jinshan", 200)
        assert issue.issue_type == IssueType.INCONSISTENT
        assert issue.severity == Severity.HIGH
        assert issue.explanation_data.check_type == CheckType.FLOOR_AREA_RATIO_INCONSISTENT
        assert issue.submitted_value == 200
        assert issue.expected_value == "240"

    def test_missing_plan_id_is_warning_not_inconsistent(self, validator):
        """未提供plan_id——查無規則不是申報錯誤。"""
        issue = validator.validate_floor_area_ratio("ISS-8", "第二種商業區", None, 240)
        assert issue.issue_type == IssueType.WARNING
        assert issue.issue_type != IssueType.INCONSISTENT
        assert issue.explanation_data.check_type == CheckType.ZONING_PLAN_UNRESOLVED

    def test_different_plan_does_not_inherit_jinshan_and_is_missing_not_inconsistent(self, validator):
        """另一個都市計畫的第二種商業區——不得沿用金山240%，也不得標為
        INCONSISTENT（因為根本沒有該計畫的官方數值可比對）。"""
        issue = validator.validate_floor_area_ratio("ISS-9", "第二種商業區", "banqiao", 999)
        assert issue.issue_type == IssueType.MISSING
        assert issue.issue_type != IssueType.INCONSISTENT
        assert issue.explanation_data.check_type == CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE
        assert issue.expected_value is None  # never silently populated with 240 or any other plan's number

    def test_no_zone_name_is_missing(self, validator):
        issue = validator.validate_floor_area_ratio("ISS-10", None, "jinshan", 240)
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.LAND_USE_RATIO_RULE_UNAVAILABLE

    def test_industrial_zone_resolves_from_common_layer_even_with_plan_id(self, validator):
        """工業區在附表一本身就有固定容積率（210%），Layer 2查無登記時
        應正確落回Layer 1，而非直接報MISSING。"""
        issue = validator.validate_floor_area_ratio("ISS-11", "工業區", "jinshan", 210)
        assert issue.issue_type == IssueType.PASSED
        assert issue.expected_value == "210"


class TestIssueFieldsWellFormed:
    def test_every_issue_carries_required_traceability_fields(self, validator):
        issue = validator.validate_building_coverage_rate("ISS-12", "第二種商業區", 65)
        assert issue.source == "LandUseRatioValidator"
        assert issue.explanation_data.summary
        assert issue.explanation_data.rule_citation
        assert issue.recommendation_data.action
        assert issue.field == "building_coverage_ratio"
        assert issue.label == "建蔽率"
        assert issue.source_form == "表1"
