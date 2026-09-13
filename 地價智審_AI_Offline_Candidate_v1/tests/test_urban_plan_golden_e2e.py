# -*- coding: utf-8 -*-
"""Phase 7 requirement: prove the coordinate -> 都市計畫 -> 使用分區 ->
LandUseRatioEngine chain genuinely COMPUTES plan_id/zone_name/BCR/FAR from
a coordinate at request time, rather than any of this codebase's Golden
Case machinery (data/golden/golden_case_input.py, which carries no
coordinate at all) silently supplying the answer.

Runs against the REAL, already-synced local snapshots (see
scripts/sync_ntpc_zoning_dataset.py's sync()/sync_plan_boundary(), both
built from the actual official 新北市使用分區/新北市都市計畫範圍 downloads,
not fixtures) -- skipped if either hasn't been synced in this environment.

GOLDEN_COORDINATE below was NOT taken from any Golden Case source file. It
was found by querying the real synced 新北市使用分區 snapshot for a polygon
with zone_name="第二種商業區" AND plan_name="金山都市計畫" (the largest such
polygon among 12 candidates) and taking its centroid -- i.e. it is a real
point inside an actual government-published 第二種商業區 parcel within
金山都市計畫, independently discovered from the GIS data itself, not
reverse-engineered from the expected 70%/240% answer.
"""
import os
import sys

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

from domain.models import Coordinate, UrbanPlanStatus  # noqa: E402
from base import ProviderContext  # noqa: E402
from dataset_registry import DatasetRegistry  # noqa: E402
from ntpc_zoning_provider import DATASET_ID as ZONING_DATASET_ID, RealNtpcZoningProvider  # noqa: E402
from urban_plan_boundary_provider import (  # noqa: E402
    DATASET_ID as PLAN_BOUNDARY_DATASET_ID, RealUrbanPlanBoundaryProvider,
)
from land_use_provider import RealLandUseProvider  # noqa: E402
from engine.land_use_ratio_engine import LandUseRatioEngine  # noqa: E402

GOLDEN_COORDINATE = Coordinate(latitude=25.222069586064077, longitude=121.63762266410481)


def _both_real_snapshots_available() -> bool:
    registry = DatasetRegistry()
    return (
        registry.get_current_snapshot(ZONING_DATASET_ID) is not None
        and registry.get_current_snapshot(PLAN_BOUNDARY_DATASET_ID) is not None
    )


pytestmark = pytest.mark.skipif(
    not _both_real_snapshots_available(),
    reason="ntpc_zoning and/or ntpc_plan_boundary snapshot not synced in this environment -- "
           "run scripts/sync_ntpc_zoning_dataset.py's sync()/sync_plan_boundary()",
)


class TestGoldenCaseUrbanPlanE2E:
    def test_urban_plan_boundary_provider_alone_resolves_jinshan(self):
        """Step 1 of the chain, isolated: coordinate -> plan_id. Not 240,
        not "jinshan" as a literal in this test's assertion input -- this
        IS the assertion, proving the provider (not this test) produced it."""
        result = RealUrbanPlanBoundaryProvider().resolve_urban_plan(GOLDEN_COORDINATE)
        assert result.urban_plan_status == UrbanPlanStatus.INSIDE
        assert result.plan_id == "jinshan"
        assert result.plan_name == "金山都市計畫"

    def test_zoning_provider_alone_resolves_second_commercial_zone(self):
        """Step 2, isolated and INDEPENDENT of step 1 (see providers/
        urban_plan_boundary_provider.py's module docstring: these two
        queries never share code path or fall back on each other)."""
        result = RealNtpcZoningProvider().query(GOLDEN_COORDINATE)
        assert result.zone_name == "第二種商業區"

    def test_far_240_requires_the_resolved_plan_id_not_hardcoded(self):
        """Phase 7's core proof. Querying the SAME zone_name with plan_id=
        None (no coordinate-based resolution at all) must NOT silently
        yield 240 -- only supplying the plan_id that step 1 above actually
        computed does. If 240 were hardcoded anywhere in the engine or a
        Golden Case fallback, the plan_id=None call below would also
        return 240; it does not (see also tests/test_land_use_ratio_engine.
        py's TestFloorAreaRatioLayer2PlanSpecific, which locks in the same
        invariant against the frozen ratio engine directly)."""
        engine = LandUseRatioEngine()

        without_plan_id = engine.resolve_floor_area_ratio("第二種商業區", plan_id=None)
        assert without_plan_id.resolved_value_pct != 240
        assert without_plan_id.resolution_layer != "PLAN_SPECIFIC"

        resolved_plan_id = RealUrbanPlanBoundaryProvider().resolve_urban_plan(GOLDEN_COORDINATE).plan_id
        with_resolved_plan_id = engine.resolve_floor_area_ratio("第二種商業區", plan_id=resolved_plan_id)
        assert with_resolved_plan_id.resolved_value_pct == 240
        assert with_resolved_plan_id.resolution_layer == "PLAN_SPECIFIC"
        assert with_resolved_plan_id.rule_status == "CONFIRMED"

    def test_real_land_use_provider_full_chain_without_any_human_plan_id(self):
        """The complete Phase 6 flow, replicating exactly what backend/
        handlers/collect_data.py's _resolve_urban_plan_result + effective_
        plan_id computation does -- ProviderContext is constructed with
        plan_id ALREADY resolved from the coordinate (mirroring the
        handler), never left for RealLandUseProvider to guess. No plan_id
        is typed anywhere in this test."""
        urban_plan_result = RealUrbanPlanBoundaryProvider().resolve_urban_plan(GOLDEN_COORDINATE)
        assert urban_plan_result.urban_plan_status == UrbanPlanStatus.INSIDE
        auto_plan_id = urban_plan_result.plan_id

        ctx = ProviderContext(
            case_no="URBAN-PLAN-E2E", city="新北市", district="金山區", segment_code="E2E",
            center_coordinate=GOLDEN_COORDINATE, plan_id=auto_plan_id,
        )
        points = {p.field: p for p in RealLandUseProvider().fetch(ctx)}

        assert points["land_use_zone"].value == "第二種商業區"
        assert points["building_coverage_ratio"].value == 70.0
        assert points["floor_area_ratio"].value == 240.0
        assert "PLAN_SPECIFIC" in points["floor_area_ratio"].notes
        assert f"plan_id={auto_plan_id}" in points["floor_area_ratio"].notes
