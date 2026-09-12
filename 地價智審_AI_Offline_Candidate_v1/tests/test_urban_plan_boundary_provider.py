# -*- coding: utf-8 -*-
"""Tests for providers/urban_plan_boundary_provider.py's
RealUrbanPlanBoundaryProvider -- the point-in-polygon judge of which 新北市
都市計畫 (if any) a coordinate falls in, deliberately independent from
providers/ntpc_zoning_provider.py's zone_name (使用分區) lookup (see that
module's docstring for why). Covers this round's required negative/safety
matrix: OUTSIDE, invalid coordinate -> UNKNOWN, a polygon hit with no
plan_id-registry mapping -> PLAN_MAPPING_UNAVAILABLE, and overlapping
polygons -> AMBIGUOUS (never picking the first candidate).
"""
import os
import sqlite3
import sys
from datetime import datetime

import pytest
from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

from domain.models import Coordinate, DatasetSnapshotInfo, UrbanPlanStatus  # noqa: E402
from dataset_registry import DatasetRegistry  # noqa: E402
from urban_plan_boundary_provider import DATASET_ID, RealUrbanPlanBoundaryProvider  # noqa: E402
from ntpc_zoning_provider import DATASET_ID as ZONING_DATASET_ID, RealNtpcZoningProvider  # noqa: E402

# A small square in WGS84 lon/lat, well away from the tolerance buffer at
# its center, so "inside"/"outside" tests are unambiguous by construction.
_PLAN_RING = [(121.0, 25.0), (121.0, 25.1), (121.1, 25.1), (121.1, 25.0)]
_INSIDE = Coordinate(latitude=25.05, longitude=121.05)  # dead center
_FAR_OUTSIDE = Coordinate(latitude=10.0, longitude=100.0)


def _polygon_wkb(coords):
    return shapely_wkb.dumps(Polygon(coords))


def _make_snapshot_db(db_path, rows):
    """rows: list of (source_key, source_sdf_id, plan_id, plan_name,
    mapping_found, wkb_bytes, min_lon, max_lon, min_lat, max_lat) -- matches
    scripts/sync_ntpc_zoning_dataset.py's write_plan_boundary_sqlite_
    snapshot() schema."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""CREATE TABLE plan_boundary_polygons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            source_sdf_id INTEGER NOT NULL,
            plan_id TEXT,
            plan_name TEXT,
            mapping_found INTEGER NOT NULL,
            geometry_wkb BLOB NOT NULL,
            min_lon REAL NOT NULL, max_lon REAL NOT NULL,
            min_lat REAL NOT NULL, max_lat REAL NOT NULL
        )""")
        conn.executemany(
            "INSERT INTO plan_boundary_polygons "
            "(source_key, source_sdf_id, plan_id, plan_name, mapping_found, geometry_wkb, "
            " min_lon, max_lon, min_lat, max_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _make_registry(tmp_path, db_path, record_count, dataset_id=DATASET_ID, version="test"):
    registry = DatasetRegistry(db_path=str(tmp_path / f"registry_{version}.sqlite3"))
    registry.register_snapshot(DatasetSnapshotInfo(
        dataset_id=dataset_id, source_name="測試來源", source_agency="測試機關",
        source_url="https://example.com/test", local_snapshot_version=version,
        local_path=db_path, checksum="dummy", license=None, refresh_policy="quarterly",
        last_synced_at=datetime.now(), source_last_modified=None,
        record_count=record_count, notes=None,
    ))
    return registry


class TestUnknownAndUnavailable:
    """TEST 3 (invalid coordinate) + the "no snapshot at all" degradation."""

    def test_none_coordinate_returns_unknown_not_guesses(self, tmp_path):
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 0))
        result = provider.resolve_urban_plan(None)
        assert result.urban_plan_status == UrbanPlanStatus.UNKNOWN
        assert result.plan_id is None

    def test_coordinate_with_missing_lat_lon_returns_unknown(self, tmp_path):
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 0))
        result = provider.resolve_urban_plan(Coordinate(latitude=None, longitude=None))
        assert result.urban_plan_status == UrbanPlanStatus.UNKNOWN

    def test_no_snapshot_ever_synced_returns_unknown_not_crash(self, tmp_path):
        registry = DatasetRegistry(db_path=str(tmp_path / "empty_registry.sqlite3"))
        provider = RealUrbanPlanBoundaryProvider(registry=registry)
        result = provider.resolve_urban_plan(_INSIDE)
        assert result.urban_plan_status == UrbanPlanStatus.UNKNOWN
        assert "sync_plan_boundary" in result.notes


class TestOutside:
    """TEST 2: coordinate outside every urban-plan polygon."""

    def test_zero_polygon_match_is_unknown_not_outside(self, tmp_path):
        # A coordinate the boundary dataset simply has no polygon covering
        # at all (e.g. outside New Taipei City entirely) must NOT be
        # reported as OUTSIDE -- OUTSIDE is reserved for an actual matched
        # "非都市計畫區" sentinel polygon (see test below); zero coverage is
        # a genuinely different, honestly-UNKNOWN situation.
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("64", 28, "jinshan", "金山都市計畫", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 1))
        result = provider.resolve_urban_plan(_FAR_OUTSIDE)
        assert result.urban_plan_status == UrbanPlanStatus.UNKNOWN
        assert result.plan_id is None

    def test_matched_non_plan_sentinel_is_outside(self, tmp_path):
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("0", 0, None, "非都市計畫區", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 1))
        result = provider.resolve_urban_plan(_INSIDE)
        assert result.urban_plan_status == UrbanPlanStatus.OUTSIDE
        assert result.plan_id is None


class TestPlanMappingUnavailable:
    """TEST 4: polygon hit, but the plan_id registry has no mapping for it."""

    def test_unmapped_polygon_hit_reports_plan_mapping_unavailable(self, tmp_path):
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("77", 41, None, None, 0, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 1))
        result = provider.resolve_urban_plan(_INSIDE)
        assert result.urban_plan_status == UrbanPlanStatus.PLAN_MAPPING_UNAVAILABLE
        assert result.plan_id is None
        assert result.requires_manual_review is True
        assert "77" in result.notes and "41" in result.notes


class TestAmbiguous:
    """TEST 5: overlapping polygons -- must never pick first()."""

    def test_two_overlapping_plans_is_ambiguous_not_first_pick(self, tmp_path):
        overlap_ring = [(120.95, 24.95), (120.95, 25.15), (121.15, 25.15), (121.15, 24.95)]
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("64", 28, "jinshan", "金山都市計畫", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
            ("37", 3, "ntpc_plan_37_3", "板橋都市計畫", 1, _polygon_wkb(overlap_ring), 120.95, 121.15, 24.95, 25.15),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 2))
        result = provider.resolve_urban_plan(_INSIDE)
        assert result.urban_plan_status == UrbanPlanStatus.AMBIGUOUS
        assert result.plan_id is None
        assert result.requires_manual_review is True
        assert "金山都市計畫" in result.notes and "板橋都市計畫" in result.notes


class TestInsideAndBoundaryTolerance:
    def test_unique_match_is_inside_with_plan_id(self, tmp_path):
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("64", 28, "jinshan", "金山都市計畫", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 1))
        result = provider.resolve_urban_plan(_INSIDE)
        assert result.urban_plan_status == UrbanPlanStatus.INSIDE
        assert result.plan_id == "jinshan"
        assert result.plan_name == "金山都市計畫"
        assert result.source_code == "64/28"
        assert result.requires_manual_review is False

    def test_coordinate_near_boundary_edge_forces_manual_review(self, tmp_path):
        """A coordinate just inside the polygon, within the digitization-
        tolerance buffer of its edge, must not be hard-classified -- still
        reports the matched plan (best-effort), but flags for human
        confirmation rather than silently treating it as a clean INSIDE."""
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("64", 28, "jinshan", "金山都市計畫", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 1))
        # 0.00005 degrees (~5.5m) inside the 121.0 edge -- well within
        # BOUNDARY_TOLERANCE_DEGREES (0.0002).
        near_edge = Coordinate(latitude=25.05, longitude=121.00005)
        result = provider.resolve_urban_plan(near_edge)
        assert result.urban_plan_status == UrbanPlanStatus.INSIDE
        assert result.plan_id == "jinshan"
        assert result.requires_manual_review is True

    def test_process_cache_returns_consistent_result_on_repeat_query(self, tmp_path):
        """Phase 13 performance requirement: a second query against the
        same snapshot version must not need to re-open/re-parse the
        SQLite file -- verified here via correctness (same answer twice),
        with the actual cold/warm timing proof recorded in the smoke test
        against the real dataset (see URBAN_PLAN_RUNTIME_INTEGRATION_
        REPORT.md's Performance section)."""
        db_path = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(db_path, [
            ("64", 28, "jinshan", "金山都市計畫", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealUrbanPlanBoundaryProvider(registry=_make_registry(tmp_path, db_path, 1))
        first = provider.resolve_urban_plan(_INSIDE)
        second = provider.resolve_urban_plan(_INSIDE)
        assert first.plan_id == second.plan_id == "jinshan"


class TestRealArchivedGoldenData:
    """Runs against the REAL, already-synced local snapshot (see
    scripts/sync_ntpc_zoning_dataset.py's sync_plan_boundary(), built from
    data/sources/gis/新北市都市計畫範圍.zip -- not a fixture) to prove the
    Golden Case's plan_id is genuinely computed from the coordinate, not
    hardcoded anywhere in this codebase. Skipped if that snapshot hasn't
    been synced in this environment yet."""

    def test_jinshan_coordinate_resolves_to_jinshan_via_real_dataset(self):
        registry = DatasetRegistry()  # default path -- the real project registry
        if registry.get_current_snapshot(DATASET_ID) is None:
            pytest.skip("ntpc_plan_boundary snapshot not synced in this environment "
                        "-- run scripts/sync_ntpc_zoning_dataset.py's sync_plan_boundary()")
        provider = RealUrbanPlanBoundaryProvider(registry=registry)
        # 金山區 approximate center -- NOT sourced from data/golden/
        # golden_case_input.py (which carries no coordinate at all, only
        # the zone_name text) or from data/rules/plan_zone_floor_area_
        # ratios.json.
        result = provider.resolve_urban_plan(Coordinate(latitude=25.2214, longitude=121.6360))
        assert result.urban_plan_status == UrbanPlanStatus.INSIDE
        assert result.plan_id == "jinshan"
        assert result.plan_name == "金山都市計畫"


class TestIndependenceFromZoningLookup:
    """TEST 6: 使用分區找不到，但都市計畫範圍找到 -- urban_plan_status must
    stay INSIDE (never forced to OUTSIDE or UNKNOWN just because the
    SEPARATE 使用分區 lookup came up empty at the same coordinate). Uses two
    independently-constructed synthetic snapshots (one per dataset_id) to
    make the "gap in one dataset only" scenario deterministic rather than
    hunting for a lucky real-world coordinate."""

    def test_zoning_gap_does_not_downgrade_urban_plan_status(self, tmp_path):
        boundary_db = str(tmp_path / "boundary.sqlite3")
        _make_snapshot_db(boundary_db, [
            ("64", 28, "jinshan", "金山都市計畫", 1, _polygon_wkb(_PLAN_RING), 121.0, 121.1, 25.0, 25.1),
        ])
        boundary_registry = _make_registry(tmp_path, boundary_db, 1, dataset_id=DATASET_ID, version="boundary")

        zoning_db = str(tmp_path / "zoning.sqlite3")
        conn = sqlite3.connect(zoning_db)
        conn.execute("""CREATE TABLE zoning_polygons (
            id INTEGER PRIMARY KEY AUTOINCREMENT, zone_name TEXT NOT NULL, plan_name TEXT,
            plan_name_note TEXT, geometry_wkb BLOB NOT NULL,
            min_lon REAL NOT NULL, max_lon REAL NOT NULL, min_lat REAL NOT NULL, max_lat REAL NOT NULL
        )""")  # deliberately empty -- no zoning coverage at all for this point
        conn.commit()
        conn.close()
        zoning_registry = _make_registry(tmp_path, zoning_db, 0, dataset_id=ZONING_DATASET_ID, version="zoning")

        urban_plan_result = RealUrbanPlanBoundaryProvider(registry=boundary_registry).resolve_urban_plan(_INSIDE)
        zoning_result = RealNtpcZoningProvider(registry=zoning_registry).query(_INSIDE)

        assert urban_plan_result.urban_plan_status == UrbanPlanStatus.INSIDE
        assert urban_plan_result.plan_id == "jinshan"
        assert zoning_result.zone_name is None  # honestly UNKNOWN, not fabricated
