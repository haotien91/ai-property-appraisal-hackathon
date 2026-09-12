# -*- coding: utf-8 -*-
"""Tests for providers/ntpc_zoning_provider.py's RealNtpcZoningProvider --
previously had no dedicated coverage (only exercised indirectly through
scripts/sync_ntpc_zoning_dataset.py's snapshot-writing tests). Added
alongside the plan_name spatial-join backfill (see
scripts/sync_ntpc_zoning_dataset.py's `resolve_plan_name`) to lock in how
`plan_name`/`plan_name_note` (new columns) flow into ZoningQueryResult.
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

from domain.models import Coordinate, DatasetSnapshotInfo  # noqa: E402
from dataset_registry import DatasetRegistry  # noqa: E402
from ntpc_zoning_provider import DATASET_ID, RealNtpcZoningProvider  # noqa: E402

# A zone polygon in WGS84 lon/lat covering roughly (121.0-121.1, 25.0-25.1).
_ZONE_RING = [(121.0, 25.0), (121.0, 25.1), (121.1, 25.1), (121.1, 25.0)]
_INSIDE = Coordinate(latitude=25.05, longitude=121.05)
_OUTSIDE = Coordinate(latitude=10.0, longitude=100.0)


def _make_snapshot_db(db_path, rows):
    """rows: list of (zone_name, plan_name, plan_name_note, geometry_wkb,
    min_lon, max_lon, min_lat, max_lat) -- matches the schema
    scripts/sync_ntpc_zoning_dataset.py's write_sqlite_snapshot() creates."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""CREATE TABLE zoning_polygons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_name TEXT NOT NULL,
            plan_name TEXT,
            plan_name_note TEXT,
            geometry_wkb BLOB NOT NULL,
            min_lon REAL NOT NULL,
            max_lon REAL NOT NULL,
            min_lat REAL NOT NULL,
            max_lat REAL NOT NULL
        )""")
        conn.executemany(
            "INSERT INTO zoning_polygons "
            "(zone_name, plan_name, plan_name_note, geometry_wkb, min_lon, max_lon, min_lat, max_lat) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _make_registry(tmp_path, db_path, record_count):
    registry = DatasetRegistry(db_path=str(tmp_path / "registry.sqlite3"))
    registry.register_snapshot(DatasetSnapshotInfo(
        dataset_id=DATASET_ID, source_name="測試來源", source_agency="測試機關",
        source_url="https://example.com/test", local_snapshot_version="test",
        local_path=db_path, checksum="dummy", license=None, refresh_policy="quarterly",
        last_synced_at=datetime.now(), source_last_modified=None,
        record_count=record_count, notes=None,
    ))
    return registry


class TestRealNtpcZoningProviderQuery:
    def test_unique_match_surfaces_plan_name(self, tmp_path):
        db_path = str(tmp_path / "zoning.sqlite3")
        wkb = shapely_wkb.dumps(Polygon(_ZONE_RING))
        _make_snapshot_db(db_path, [
            ("第二種商業區", "金山都市計畫", None, wkb, 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealNtpcZoningProvider(registry=_make_registry(tmp_path, db_path, 1))

        result = provider.query(_INSIDE)

        assert result.zone_name == "第二種商業區"
        assert result.plan_name == "金山都市計畫"
        assert result.requires_manual_review is False

    def test_plan_name_note_forces_manual_review_and_clears_plan_name(self, tmp_path):
        db_path = str(tmp_path / "zoning.sqlite3")
        wkb = shapely_wkb.dumps(Polygon(_ZONE_RING))
        note = "centroid未落在任何都市計畫範圍多邊形內，需人工確認"
        _make_snapshot_db(db_path, [
            ("第二種商業區", None, note, wkb, 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealNtpcZoningProvider(registry=_make_registry(tmp_path, db_path, 1))

        result = provider.query(_INSIDE)

        assert result.zone_name == "第二種商業區"
        assert result.plan_name is None
        assert result.requires_manual_review is True
        assert note in result.notes

    def test_no_polygon_match_returns_unknown_not_guesses(self, tmp_path):
        db_path = str(tmp_path / "zoning.sqlite3")
        wkb = shapely_wkb.dumps(Polygon(_ZONE_RING))
        _make_snapshot_db(db_path, [
            ("第二種商業區", "金山都市計畫", None, wkb, 121.0, 121.1, 25.0, 25.1),
        ])
        provider = RealNtpcZoningProvider(registry=_make_registry(tmp_path, db_path, 1))

        result = provider.query(_OUTSIDE)

        assert result.zone_name is None
        assert result.plan_name is None
        assert result.matched_polygon_count == 0

    def test_no_coordinate_returns_unknown_not_guesses(self, tmp_path):
        db_path = str(tmp_path / "zoning.sqlite3")
        provider = RealNtpcZoningProvider(registry=_make_registry(tmp_path, db_path, 0))

        result = provider.query(None)

        assert result.zone_name is None
        assert result.plan_name is None
