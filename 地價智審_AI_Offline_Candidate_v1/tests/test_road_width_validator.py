# -*- coding: utf-8 -*-
"""Tests for engine/road_width_validator.py -- three non-PASSED outcomes
that must never be conflated: ROAD_WIDTH_UNAVAILABLE (can't verify) vs
ROAD_WIDTH_EVIDENCE_CONFLICT (references disagree) vs
ROAD_WIDTH_INCONSISTENT (confirmed, high-confidence mismatch)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.road_width_resolver import RoadWidthResolver  # noqa: E402
from engine.road_width_validator import RoadWidthValidator  # noqa: E402
from domain.models import IssueType, Severity, CheckType, RoadWidthEvidence, RoadWidthEvidenceType  # noqa: E402


def _ev(evidence_type, width_m, **overrides):
    fields = dict(road_name="中山路", width_m=width_m, evidence_type=evidence_type,
                  source_name=f"{evidence_type.value} source", confidence="高")
    fields.update(overrides)
    return RoadWidthEvidence(**fields)


class TestUnavailable:
    def test_no_evidence_is_missing_not_inconsistent(self):
        resolution = RoadWidthResolver().resolve([])
        issue = RoadWidthValidator().validate_main_road_width("ISS-1", resolution, 18)
        assert issue.issue_type == IssueType.MISSING
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_UNAVAILABLE
        assert issue.issue_type != IssueType.INCONSISTENT


class TestConflict:
    def test_conflicting_evidence_is_warning_not_inconsistent(self):
        ev = [
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18),
            _ev(RoadWidthEvidenceType.GEOMETRIC_ESTIMATE, "17.5"),
        ]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-2", resolution, 18)
        assert issue.issue_type == IssueType.WARNING
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_EVIDENCE_CONFLICT
        assert issue.issue_type != IssueType.INCONSISTENT
        # never claims a violation even though submitted (18) happens to
        # match one of the conflicting candidates
        assert "不一致" in issue.explanation_data.summary


class TestResolvedComparison:
    def test_matching_submission_passes(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-3", resolution, 18)
        assert issue.issue_type == IssueType.PASSED
        assert issue.explanation_data.check_type == CheckType.PASSED_CHECK
        assert issue.recommendation_data.requires_human_review is False

    def test_mismatched_submission_is_inconsistent(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-4", resolution, 12)
        assert issue.issue_type == IssueType.INCONSISTENT
        assert issue.explanation_data.check_type == CheckType.ROAD_WIDTH_INCONSISTENT
        assert issue.severity == Severity.HIGH
        assert issue.submitted_value == 12
        assert issue.expected_value == "18"

    def test_no_submitted_value_is_missing(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-5", resolution, None)
        assert issue.issue_type == IssueType.MISSING

    def test_stale_resolved_evidence_never_asserts_inconsistent(self):
        """A single-source resolution whose evidence itself flags
        requires_manual_review is too low-confidence to call INCONSISTENT
        outright, even on a genuine numeric mismatch -- 'only high
        confidence and semantically consistent evidence may produce
        ROAD_WIDTH_INCONSISTENT' per spec."""
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18, requires_manual_review=True)]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-6", resolution, 12)
        assert issue.issue_type != IssueType.INCONSISTENT
        assert issue.issue_type == IssueType.WARNING

    def test_stale_resolved_evidence_never_asserts_passed_either(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18, requires_manual_review=True)]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-7", resolution, 18)
        assert issue.issue_type != IssueType.PASSED
        assert issue.issue_type == IssueType.WARNING


class TestEvidencePreserved:
    def test_computed_steps_carry_every_evidence_entry(self):
        ev = [
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20, dataset_id="ntpc_urban_plan_road"),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18, dataset_id="osm"),
        ]
        resolution = RoadWidthResolver().resolve(ev)
        issue = RoadWidthValidator().validate_main_road_width("ISS-8", resolution, 18)
        steps = "\n".join(issue.explanation_data.computed_steps)
        assert "URBAN_PLAN_DESIGN_WIDTH" in steps
        assert "EXTERNAL_MAP_ATTRIBUTE" in steps
        assert "ntpc_urban_plan_road" in steps
        assert "osm" in steps
