# -*- coding: utf-8 -*-
"""Tests for providers/road_provider.py's RealRoadProvider -- must never
fall back to MockRoadProvider's Golden Case numbers (18m/12m) regardless
of whether any evidence source is wired in."""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from road_provider import MockRoadProvider, RealRoadProvider  # noqa: E402
from domain.models import Coordinate, RoadWidthEvidence, RoadWidthEvidenceType  # noqa: E402
from engine.road_width_resolver import RoadWidthResolutionStatus  # noqa: E402

CTX = ProviderContext(
    case_no="TEST-001", city="新北市", district="金山區", segment_code="P002-00",
    center_coordinate=Coordinate(latitude=25.0, longitude=121.5),
)


def _ev(evidence_type, width_m, road_name="中山路", **overrides):
    fields = dict(road_name=road_name, width_m=width_m, evidence_type=evidence_type,
                  source_name=f"{evidence_type.value} source", confidence="高")
    fields.update(overrides)
    return RoadWidthEvidence(**fields)


class TestMockRoadProviderUnchanged:
    def test_still_returns_golden_case_values(self):
        points = MockRoadProvider().fetch(CTX)
        by_field = {p.field: p for p in points}
        assert by_field["main_road_width"].value == 18
        assert by_field["segment_avg_road_width"].value == 12
        assert by_field["main_road_width"].source_type == "Mock"


class TestRealRoadProviderNeverFallsBackToMock:
    def test_default_construction_no_sources_is_unknown_not_golden_case(self):
        provider = RealRoadProvider()
        points = provider.fetch(CTX)
        by_field = {p.field: p for p in points}
        assert by_field["main_road_width"].value is None
        assert by_field["main_road_width"].value != 18  # never Golden Case's number
        assert by_field["main_road_width"].confidence == "UNKNOWN"
        assert by_field["main_road_width"].source_type == "UNKNOWN"

    def test_no_evidence_source_never_produces_segment_avg_or_development_level(self):
        """Phase 8 item 5 MVP scope: only main_road_width this round --
        the other two fields have no resolution infrastructure at all and
        must stay honestly UNKNOWN, not silently populated."""
        points = RealRoadProvider().fetch(CTX)
        by_field = {p.field: p for p in points}
        assert by_field["segment_avg_road_width"].value is None
        assert by_field["road_development_level"].value is None

    def test_empty_evidence_source_list_is_unavailable(self):
        provider = RealRoadProvider(evidence_sources=[lambda ctx: []])
        resolution = provider.resolve_main_road_width(CTX)
        assert resolution.status == RoadWidthResolutionStatus.UNAVAILABLE


class TestRealRoadProviderWithInjectedEvidence:
    """DI-testable -- mirrors RealLandUseProvider's zoning_provider
    injection pattern -- since no confirmed real source exists yet to
    exercise for real (see module docstring's A-E survey)."""

    def test_single_agreeing_source_resolves_and_surfaces_via_fetch(self):
        source = lambda ctx: [_ev(RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE, 18)]
        provider = RealRoadProvider(evidence_sources=[source])
        points = provider.fetch(CTX)
        by_field = {p.field: p for p in points}
        assert by_field["main_road_width"].value == 18.0
        assert by_field["main_road_width"].confidence == "高"
        assert by_field["main_road_width"].source_type == "OFFICIAL_ATTRIBUTE"
        assert by_field["main_road_name"].value == "中山路"

    def test_multiple_agreeing_sources_resolve(self):
        s1 = lambda ctx: [_ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 18)]
        s2 = lambda ctx: [_ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18)]
        provider = RealRoadProvider(evidence_sources=[s1, s2])
        resolution = provider.resolve_main_road_width(CTX)
        assert resolution.status == RoadWidthResolutionStatus.RESOLVED
        assert resolution.resolved_width_m == 18
        assert len(resolution.evidence) == 2

    def test_conflicting_sources_never_surface_a_fabricated_single_value(self):
        s1 = lambda ctx: [_ev(RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH, 20)]
        s2 = lambda ctx: [_ev(RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE, 18)]
        provider = RealRoadProvider(evidence_sources=[s1, s2])
        resolution = provider.resolve_main_road_width(CTX)
        assert resolution.status == RoadWidthResolutionStatus.CONFLICT
        assert resolution.resolved_width_m is None
        # fetch()'s NormalizedDataPoint shape has no dedicated CONFLICT
        # state -- it must still never fabricate a single number here.
        points = provider.fetch(CTX)
        by_field = {p.field: p for p in points}
        assert by_field["main_road_width"].value is None

    def test_submitted_value_evidence_type_never_leaks_into_resolution(self):
        """A misconfigured evidence source that (incorrectly) tags its
        output as SUBMITTED_VALUE must not be treated as an official
        reference -- RoadWidthResolver excludes it categorically."""
        source = lambda ctx: [_ev(RoadWidthEvidenceType.SUBMITTED_VALUE, 18)]
        provider = RealRoadProvider(evidence_sources=[source])
        resolution = provider.resolve_main_road_width(CTX)
        assert resolution.status == RoadWidthResolutionStatus.UNAVAILABLE
