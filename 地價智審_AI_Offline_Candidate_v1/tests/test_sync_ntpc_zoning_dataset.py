# -*- coding: utf-8 -*-
"""Tests for scripts/sync_ntpc_zoning_dataset.py's pure functions. Uses a
tiny synthetic shapefile (two squares) rather than the real ~75MB NTPC
download, per this codebase's convention that the pytest suite stays
offline/fast (see scripts/smoke_test_real_providers.py for the live,
manually-triggered counterpart). The full download->reproject->query
pipeline was verified once, live, against the real production shapefile
in this session (34,179 real polygons parsed, reprojected, and correctly
point-in-polygon queried end to end) -- these tests lock in that the
underlying logic keeps working, without re-downloading 75MB on every run.
"""
import json
import os
import sqlite3
import sys

import pytest
import shapefile

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

import scripts.sync_ntpc_zoning_dataset as sync_mod  # noqa: E402

# Real .prj content, verified live against the actual NTPC download in this
# session (TWD_1997_TM_Taiwan == EPSG:3826).
REAL_PRJ_WKT = (
    'PROJCS["TWD_1997_TM_Taiwan",GEOGCS["GCS_TWD_1997",'
    'DATUM["D_TWD_1997",SPHEROID["GRS_1980",6378137.0,298.257222101]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",250000.0],'
    'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",121.0],'
    'PARAMETER["Scale_Factor",0.9999],PARAMETER["Latitude_Of_Origin",0.0],'
    'UNIT["Meter",1.0]'
)

# Coordinates in the same neighborhood (EPSG:3826 meters) as a real sample
# polygon observed in the actual download, so reprojection lands inside
# Taiwan's real extent rather than an arbitrary/meaningless location.
SQUARE_A = ("住宅區", (297900.0, 2763700.0, 298000.0, 2763800.0))
SQUARE_B = ("商業區", (298000.0, 2763700.0, 298100.0, 2763800.0))


def _write_fixture_shapefile(base_path, squares, prj_wkt=REAL_PRJ_WKT, cpg="UTF-8"):
    with shapefile.Writer(base_path, shapeType=shapefile.POLYGON, encoding="utf-8") as w:
        w.field("ZONE", "C", size=254)
        for zone_name, (minx, miny, maxx, maxy) in squares:
            # Clockwise ring == exterior ring per the shapefile spec.
            w.poly([[(minx, miny), (minx, maxy), (maxx, maxy), (maxx, miny), (minx, miny)]])
            w.record(ZONE=zone_name)
    if prj_wkt is not None:
        with open(base_path + ".prj", "w", encoding="utf-8") as f:
            f.write(prj_wkt)
    if cpg is not None:
        with open(base_path + ".cpg", "w", encoding="ascii") as f:
            f.write(cpg)


@pytest.fixture()
def fixture_shp(tmp_path):
    base = str(tmp_path / "fixture")
    _write_fixture_shapefile(base, [SQUARE_A, SQUARE_B])
    return base + ".shp"


class TestVerifyCrs:
    def test_matching_marker_passes(self, fixture_shp):
        sync_mod.verify_crs(fixture_shp)  # does not raise

    def test_missing_prj_raises(self, tmp_path):
        base = str(tmp_path / "noprj")
        _write_fixture_shapefile(base, [SQUARE_A], prj_wkt=None)
        with pytest.raises(sync_mod.SyncError):
            sync_mod.verify_crs(base + ".shp")

    def test_wrong_crs_raises_not_silently_assumes_wgs84(self, tmp_path):
        base = str(tmp_path / "wrongcrs")
        _write_fixture_shapefile(base, [SQUARE_A], prj_wkt='GEOGCS["GCS_WGS_1984"]')
        with pytest.raises(sync_mod.SyncError):
            sync_mod.verify_crs(base + ".shp")


class TestDetectEncoding:
    def test_reads_cpg_value(self, fixture_shp):
        assert sync_mod._detect_encoding(fixture_shp) == "UTF-8"

    def test_missing_cpg_raises_not_guesses(self, tmp_path):
        base = str(tmp_path / "nocpg")
        _write_fixture_shapefile(base, [SQUARE_A], cpg=None)
        with pytest.raises(sync_mod.SyncError):
            sync_mod._detect_encoding(base + ".shp")

    def test_empty_cpg_raises_not_guesses(self, tmp_path):
        base = str(tmp_path / "emptycpg")
        _write_fixture_shapefile(base, [SQUARE_A], cpg="")
        with pytest.raises(sync_mod.SyncError):
            sync_mod._detect_encoding(base + ".shp")


class TestIterZonePolygons:
    def test_yields_one_row_per_polygon_with_wgs84_coords(self, fixture_shp):
        rows = list(sync_mod.iter_zone_polygons(fixture_shp))
        assert len(rows) == 2
        names = {r[0] for r in rows}
        assert names == {"住宅區", "商業區"}
        for zone_name, geom_wkb, (min_lon, max_lon, min_lat, max_lat) in rows:
            assert isinstance(geom_wkb, (bytes, bytearray))
            assert 120.0 < min_lon < max_lon < 122.0  # sanity: Taiwan longitude range
            assert 21.0 < min_lat < max_lat < 26.0  # sanity: Taiwan latitude range

    def test_skips_blank_zone_name_not_fabricates_one(self, tmp_path):
        base = str(tmp_path / "blank")
        _write_fixture_shapefile(base, [("", SQUARE_A[1]), SQUARE_B])
        rows = list(sync_mod.iter_zone_polygons(base + ".shp"))
        assert len(rows) == 1
        assert rows[0][0] == "商業區"

    def test_missing_zone_field_raises(self, tmp_path):
        base = str(tmp_path / "nozonefield")
        with shapefile.Writer(base, shapeType=shapefile.POLYGON, encoding="utf-8") as w:
            w.field("NOT_ZONE", "C", size=10)
            w.poly([[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]])
            w.record(NOT_ZONE="x")
        with open(base + ".prj", "w", encoding="utf-8") as f:
            f.write(REAL_PRJ_WKT)
        with open(base + ".cpg", "w", encoding="ascii") as f:
            f.write("UTF-8")
        with pytest.raises(sync_mod.SyncError):
            list(sync_mod.iter_zone_polygons(base + ".shp"))


class TestWriteSqliteSnapshot:
    def test_writes_expected_row_count_and_schema(self, fixture_shp, tmp_path):
        db_path = str(tmp_path / "out.sqlite3")
        count = sync_mod.write_sqlite_snapshot(sync_mod.iter_zone_polygons(fixture_shp), db_path)
        assert count == 2

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT zone_name, plan_name, min_lon, max_lon, min_lat, max_lat FROM zoning_polygons"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        for zone_name, plan_name, min_lon, max_lon, min_lat, max_lat in rows:
            assert plan_name is None  # this dataset has no plan_name field -- must not be fabricated
            assert min_lon < max_lon and min_lat < max_lat

    def test_rerun_replaces_not_appends(self, fixture_shp, tmp_path):
        db_path = str(tmp_path / "out.sqlite3")
        sync_mod.write_sqlite_snapshot(sync_mod.iter_zone_polygons(fixture_shp), db_path)
        sync_mod.write_sqlite_snapshot(sync_mod.iter_zone_polygons(fixture_shp), db_path)
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT COUNT(*) FROM zoning_polygons").fetchone()[0]
        conn.close()
        assert total == 2  # not 4 -- a rerun must not silently accumulate duplicates


class _FakeResp:
    def __init__(self, payload_bytes, status=200):
        self._payload = payload_bytes
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestFetchDownloadLink:
    def test_finds_matching_item(self, monkeypatch):
        payload = json.dumps([
            {"item": "1", "name": "新北市使用分區", "link": "https://example.com/foo.zip"},
            {"item": "2", "name": "新北市都市計畫範圍", "link": "https://example.com/bar.zip"},
        ]).encode("utf-8")
        monkeypatch.setattr(
            sync_mod.urllib.request, "urlopen", lambda req, timeout: _FakeResp(payload)
        )
        assert sync_mod.fetch_download_link() == "https://example.com/foo.zip"

    def test_missing_item_raises_not_guesses_url(self, monkeypatch):
        payload = json.dumps([
            {"item": "2", "name": "新北市都市計畫範圍", "link": "https://example.com/bar.zip"},
        ]).encode("utf-8")
        monkeypatch.setattr(
            sync_mod.urllib.request, "urlopen", lambda req, timeout: _FakeResp(payload)
        )
        with pytest.raises(sync_mod.SyncError):
            sync_mod.fetch_download_link()

    def test_item_without_link_raises(self, monkeypatch):
        payload = json.dumps([{"item": "1", "name": "新北市使用分區"}]).encode("utf-8")
        monkeypatch.setattr(
            sync_mod.urllib.request, "urlopen", lambda req, timeout: _FakeResp(payload)
        )
        with pytest.raises(sync_mod.SyncError):
            sync_mod.fetch_download_link()
