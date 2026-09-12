# -*- coding: utf-8 -*-
"""Tests for engine/road_width_resolver.py -- the core distinction this
module exists to enforce: multiple independently-sourced road-width
figures must never be silently averaged or picked-by-assumption when they
disagree. Only exact agreement across all considered evidence produces a
RESOLVED result; any disagreement produces CONFLICT with every entry
preserved."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.road_width_resolver import RoadWidthResolver  # noqa: E402
from domain.models import RoadWidthEvidence, RoadWidthEvidenceType, RoadWidthResolutionStatus  # noqa: E402


def _ev(evidence_type, width_m, **overrides):
    fields = dict(road_name="中山路", width_m=width_m, evidence_type=evidence_type,
                  source_name=f"{evidence_type.value} source", confidence="高")
    fields.update(overrides)
    return RoadWidthEvidence(**fields)


class TestUnavailable:
    def test_no_evidence_is_unavailable(self):
        result = RoadWidthResolver().resolve([])
        assert result.status == RoadWidthResolutionStatus.UNAVAILABLE
        assert result.resolved_width_m is None
        assert result.requires_manual_review is True

    def test_only_submitted_value_evidence_is_still_unavailable(self):
        """SUBMITTED_VALUE must never be treated as a resolution candidate
        -- otherwise submitted-vs-official becomes a tautological A==A
        comparison."""
        ev = [_ev(RoadWidthEvidenceType.SUBMITTED_VALUE, 18)]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.UNAVAILABLE

    def test_evidence_with_no_width_value_does_not_count(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, None, notes="查無資料")]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.UNAVAILABLE


class TestResolvedSingleSource:
    def test_single_official_attribute_resolves(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.RESOLVED
        assert result.resolved_width_m == 18
        assert result.resolved_evidence.evidence_type == RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE
        assert result.requires_manual_review is False

    def test_stale_evidence_propagates_requires_manual_review(self):
        ev = [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18, requires_manual_review=True)]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.RESOLVED
        assert result.requires_manual_review is True


class TestResolvedAgreement:
    def test_all_sources_agreeing_resolves_and_prefers_highest_priority(self):
        ev = [
            _ev(RoadWidthEvidenceType.GEOMETRIC_ESTIMATE, 18),
            _ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18),
        ]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.RESOLVED
        assert result.resolved_width_m == 18
        assert result.resolved_evidence.evidence_type == RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE
        assert len(result.evidence) == 3

    def test_urban_plan_and_map_agreeing_prefers_urban_plan_over_map(self):
        ev = [
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 20),
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20),
        ]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.RESOLVED
        assert result.resolved_evidence.evidence_type == RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH


class TestConflict:
    def test_three_way_disagreement_is_conflict_not_averaged(self):
        """The exact scenario from spec: urban plan=20m, map=18m,
        geometric estimate=17.5m -- must never be silently resolved to one
        of these, must never be averaged to ~18.5m."""
        ev = [
            _ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20),
            _ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18),
            _ev(RoadWidthEvidenceType.GEOMETRIC_ESTIMATE, "17.5"),
        ]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.CONFLICT
        assert result.resolved_width_m is None
        assert result.resolved_evidence is None
        assert result.requires_manual_review is True
        assert len(result.evidence) == 3  # all preserved, none dropped

    def test_two_way_disagreement_within_same_tier_is_still_conflict(self):
        ev = [
            _ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18, source_name="dataset A"),
            _ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 19, source_name="dataset B"),
        ]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.CONFLICT

    def test_high_priority_disagreeing_with_low_priority_is_still_conflict(self):
        """Priority order breaks ties among AGREEING evidence only -- it
        must never be used to override a genuine disagreement (i.e. this
        must NOT just trust OFFICIAL_ATTRIBUTE and discard the rest)."""
        ev = [
            _ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18),
            _ev(RoadWidthEvidenceType.GEOMETRIC_ESTIMATE, 15),
        ]
        result = RoadWidthResolver().resolve(ev)
        assert result.status == RoadWidthResolutionStatus.CONFLICT
