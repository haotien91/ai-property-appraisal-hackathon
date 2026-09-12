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
import zipfile

import pytest
import shapefile
from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon

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


def _write_plan_boundary_fixture_shapefile(base_path, records, prj_wkt=REAL_PRJ_WKT, cpg="UTF-8"):
    """records: list of (key:int, sdf_id:int, (minx,miny,maxx,maxy)). Name/
    LblName are written as the literal U+FFFD replacement character on
    purpose -- mirrors the real dataset's corruption, and locks in that
    load_plan_boundary_polygons() never reads these two fields."""
    with shapefile.Writer(base_path, shapeType=shapefile.POLYGON, encoding="utf-8") as w:
        w.field("key", "N", size=10)
        w.field("Name", "C", size=254)
        w.field("Url", "C", size=254)
        w.field("SDF_ID", "N", size=10)
        w.field("key1", "N", size=10)
        w.field("LblName", "C", size=254)
        for key, sdf_id, (minx, miny, maxx, maxy) in records:
            w.poly([[(minx, miny), (minx, maxy), (maxx, maxy), (maxx, miny), (minx, miny)]])
            w.record(key=key, Name="��", Url="", SDF_ID=sdf_id, key1=key, LblName="��")
    if prj_wkt is not None:
        with open(base_path + ".prj", "w", encoding="utf-8") as f:
            f.write(prj_wkt)
    if cpg is not None:
        with open(base_path + ".cpg", "w", encoding="ascii") as f:
            f.write(cpg)


def _zip_shapefile(base_path):
    """Zips up a fixture's .shp/.shx/.dbf/.prj/.cpg siblings -- mirrors the
    real archive format load_plan_boundary_polygons() expects (a ZIP
    containing exactly one .shp), so tests exercise the same
    extract_zip()/verify_crs() path as the real sync."""
    zip_path = base_path + ".zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = base_path + ext
            if os.path.exists(p):
                zf.write(p, arcname=os.path.basename(p))
    return zip_path


# A boundary polygon in the same EPSG:3826 neighborhood as SQUARE_A/SQUARE_B
# above, large enough to contain both.
PLAN_BOUNDARY_COVERING_SQUARES = (297800.0, 2763600.0, 298200.0, 2763900.0)


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


class TestLoadPlanNameLookup:
    def test_parses_lookup_array_keyed_by_key_and_sdf_id(self, tmp_path):
        path = tmp_path / "lookup.json"
        path.write_text(
            json.dumps({"lookup": [{"key": "64", "key1": "64", "sdf_id": 28, "name": "金山都市計畫"}]}),
            encoding="utf-8",
        )
        result = sync_mod.load_plan_name_lookup(str(path))
        assert result == {("64", 28): "金山都市計畫"}

    def test_duplicate_key_different_sdf_id_both_kept(self, tmp_path):
        # Mirrors the real dataset's known quirk: key=999 repeats with two
        # different sdf_id values -- the compound (key, sdf_id) pair must
        # disambiguate them, not silently keep only one.
        path = tmp_path / "lookup.json"
        path.write_text(
            json.dumps({"lookup": [
                {"key": "999", "sdf_id": 0, "name": "非都市計畫區"},
                {"key": "999", "sdf_id": 39, "name": "非都市計畫區"},
            ]}),
            encoding="utf-8",
        )
        result = sync_mod.load_plan_name_lookup(str(path))
        assert result == {("999", 0): "非都市計畫區", ("999", 39): "非都市計畫區"}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_name_lookup(str(tmp_path / "missing.json"))

    def test_missing_lookup_array_raises(self, tmp_path):
        path = tmp_path / "lookup.json"
        path.write_text(json.dumps({"_meta": {}}), encoding="utf-8")
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_name_lookup(str(path))

    def test_malformed_entry_raises_not_skips(self, tmp_path):
        path = tmp_path / "lookup.json"
        path.write_text(json.dumps({"lookup": [{"key": "1"}]}), encoding="utf-8")
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_name_lookup(str(path))

    def test_empty_name_raises(self, tmp_path):
        path = tmp_path / "lookup.json"
        path.write_text(json.dumps({"lookup": [{"key": "1", "sdf_id": 1, "name": ""}]}), encoding="utf-8")
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_name_lookup(str(path))


class TestLoadPlanBoundaryPolygons:
    def test_joins_key_sdf_id_to_lookup_name_not_corrupted_name_field(self, tmp_path):
        base = str(tmp_path / "boundary")
        _write_plan_boundary_fixture_shapefile(base, [(10, 1, PLAN_BOUNDARY_COVERING_SQUARES)])
        zip_path = _zip_shapefile(base)
        lookup = {("10", 1): "測試都市計畫"}

        polygons = sync_mod.load_plan_boundary_polygons(zip_path, lookup, str(tmp_path / "work"))

        assert len(polygons) == 1
        name, geom = polygons[0]
        assert name == "測試都市計畫"
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        assert 120.0 < min_lon < max_lon < 122.0
        assert 21.0 < min_lat < max_lat < 26.0

    def test_missing_zip_raises(self, tmp_path):
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_boundary_polygons(
                str(tmp_path / "missing.zip"), {}, str(tmp_path / "work")
            )

    def test_unmatched_key_sdf_id_raises_not_skips(self, tmp_path):
        base = str(tmp_path / "boundary")
        _write_plan_boundary_fixture_shapefile(base, [(999, 0, PLAN_BOUNDARY_COVERING_SQUARES)])
        zip_path = _zip_shapefile(base)
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_boundary_polygons(
                zip_path, {("1", 1): "無關資料"}, str(tmp_path / "work")
            )

    def test_missing_key_field_raises(self, tmp_path):
        base = str(tmp_path / "noboundaryfield")
        with shapefile.Writer(base, shapeType=shapefile.POLYGON, encoding="utf-8") as w:
            w.field("NOT_KEY", "N", size=10)
            w.poly([[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]])
            w.record(NOT_KEY=1)
        with open(base + ".prj", "w", encoding="utf-8") as f:
            f.write(REAL_PRJ_WKT)
        with open(base + ".cpg", "w", encoding="ascii") as f:
            f.write("UTF-8")
        zip_path = _zip_shapefile(base)
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_boundary_polygons(zip_path, {}, str(tmp_path / "work"))


# A small zone polygon in WGS84 lon/lat -- resolve_plan_name doesn't care
# about the source CRS, only that zone/boundary geometries share one, so
# plain lon/lat-shaped numbers keep these tests simple and independent of
# the shapefile/reprojection machinery exercised elsewhere in this file.
_ZONE_RING = [(121.0, 25.0), (121.0, 25.1), (121.1, 25.1), (121.1, 25.0)]


class TestResolvePlanName:
    def _zone_wkb(self):
        return shapely_wkb.dumps(Polygon(_ZONE_RING))

    def test_unique_match_returns_name(self):
        boundary = [("金山都市計畫", Polygon([(120.9, 24.9), (120.9, 25.2), (121.2, 25.2), (121.2, 24.9)]))]
        name, note = sync_mod.resolve_plan_name(self._zone_wkb(), boundary)
        assert name == "金山都市計畫"
        assert note is None

    def test_non_plan_sentinel_returns_none_not_literal_string(self):
        boundary = [(sync_mod.NON_PLAN_SENTINEL,
                     Polygon([(120.9, 24.9), (120.9, 25.2), (121.2, 25.2), (121.2, 24.9)]))]
        name, note = sync_mod.resolve_plan_name(self._zone_wkb(), boundary)
        assert name is None
        assert "非都市計畫區" in note

    def test_zero_match_returns_none_with_note_not_guesses(self):
        boundary = [("其他都市計畫", Polygon([(150.0, 40.0), (150.0, 40.1), (150.1, 40.1), (150.1, 40.0)]))]
        name, note = sync_mod.resolve_plan_name(self._zone_wkb(), boundary)
        assert name is None
        assert "未落在任何都市計畫範圍" in note

    def test_multi_match_returns_none_listing_candidates(self):
        boundary = [
            ("計畫甲", Polygon([(120.9, 24.9), (120.9, 25.2), (121.2, 25.2), (121.2, 24.9)])),
            ("計畫乙", Polygon([(120.95, 24.95), (120.95, 25.15), (121.15, 25.15), (121.15, 24.95)])),
        ]
        name, note = sync_mod.resolve_plan_name(self._zone_wkb(), boundary)
        assert name is None
        assert "計畫甲" in note and "計畫乙" in note
        assert "無法唯一判定" in note


class TestIterZonePolygonsWithPlanName:
    def test_joins_plan_name_onto_every_zone_polygon(self, fixture_shp, tmp_path):
        boundary_base = str(tmp_path / "boundaryfixture")
        _write_plan_boundary_fixture_shapefile(boundary_base, [(10, 1, PLAN_BOUNDARY_COVERING_SQUARES)])
        boundary_zip = _zip_shapefile(boundary_base)
        lookup = {("10", 1): "測試都市計畫"}
        boundary_polygons = sync_mod.load_plan_boundary_polygons(boundary_zip, lookup, str(tmp_path / "work"))

        rows = list(sync_mod.iter_zone_polygons_with_plan_name(fixture_shp, boundary_polygons))
        assert len(rows) == 2
        for zone_name, geom_wkb, bbox, plan_name, plan_name_note in rows:
            assert plan_name == "測試都市計畫"
            assert plan_name_note is None


class TestWriteSqliteSnapshotPlanName:
    def test_round_trips_plan_name_and_note_from_five_tuples(self, tmp_path):
        db_path = str(tmp_path / "out.sqlite3")
        records = [
            ("住宅區", b"\x00", (121.0, 121.1, 25.0, 25.1), "測試都市計畫", None),
            ("商業區", b"\x00", (121.1, 121.2, 25.0, 25.1), None, "落在範圍外，需人工確認"),
        ]
        count = sync_mod.write_sqlite_snapshot(iter(records), db_path)
        assert count == 2

        conn = sqlite3.connect(db_path)
        try:
            row_a = conn.execute(
                "SELECT plan_name, plan_name_note FROM zoning_polygons WHERE zone_name = ?", ("住宅區",)
            ).fetchone()
            row_b = conn.execute(
                "SELECT plan_name, plan_name_note FROM zoning_polygons WHERE zone_name = ?", ("商業區",)
            ).fetchone()
        finally:
            conn.close()
        assert row_a == ("測試都市計畫", None)
        assert row_b == (None, "落在範圍外，需人工確認")


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


class TestLoadPlanIdRegistry:
    def test_parses_entries_keyed_by_source_key_and_sdf_id(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"entries": [
            {"source_key": "64", "source_sdf_id": 28, "plan_id": "jinshan", "plan_name": "金山都市計畫"},
            {"source_key": "0", "source_sdf_id": 0, "plan_id": None, "plan_name": "非都市計畫區"},
        ]}), encoding="utf-8")
        result = sync_mod.load_plan_id_registry(str(path))
        assert result == {
            ("64", 28): ("jinshan", "金山都市計畫"),
            ("0", 0): (None, "非都市計畫區"),
        }

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_id_registry(str(tmp_path / "missing.json"))

    def test_missing_entries_array_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"_meta": {}}), encoding="utf-8")
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_id_registry(str(path))

    def test_malformed_entry_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"entries": [{"source_key": "1"}]}), encoding="utf-8")
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_id_registry(str(path))


class TestLoadPlanBoundaryFeaturesForProvider:
    def test_mapped_and_unmapped_records_both_kept(self, tmp_path):
        base = str(tmp_path / "boundary")
        _write_plan_boundary_fixture_shapefile(base, [
            (10, 1, PLAN_BOUNDARY_COVERING_SQUARES),
            (999, 0, (500000.0, 2763600.0, 500100.0, 2763900.0)),
        ])
        zip_path = _zip_shapefile(base)
        id_registry = {("10", 1): ("test_plan", "測試都市計畫")}  # (999, 0) deliberately absent

        records = sync_mod.load_plan_boundary_features_for_provider(zip_path, id_registry, str(tmp_path / "work"))

        assert len(records) == 2
        by_key = {(r[0], r[1]): r for r in records}
        mapped = by_key[("10", 1)]
        assert mapped[2:5] == ("test_plan", "測試都市計畫", True)
        unmapped = by_key[("999", 0)]
        assert unmapped[2:5] == (None, None, False)

    def test_never_raises_on_unmapped_record(self, tmp_path):
        # Contrast with load_plan_boundary_polygons, which fails loudly on
        # exactly this input -- this function must tolerate it instead.
        base = str(tmp_path / "boundary")
        _write_plan_boundary_fixture_shapefile(base, [(999, 0, PLAN_BOUNDARY_COVERING_SQUARES)])
        zip_path = _zip_shapefile(base)
        records = sync_mod.load_plan_boundary_features_for_provider(zip_path, {}, str(tmp_path / "work"))
        assert len(records) == 1
        assert records[0][4] is False

    def test_missing_zip_raises(self, tmp_path):
        with pytest.raises(sync_mod.SyncError):
            sync_mod.load_plan_boundary_features_for_provider(
                str(tmp_path / "missing.zip"), {}, str(tmp_path / "work")
            )


class TestWritePlanBoundarySqliteSnapshot:
    def test_round_trips_all_columns(self, tmp_path):
        db_path = str(tmp_path / "out.sqlite3")
        wkb = shapely_wkb.dumps(Polygon([(121.0, 25.0), (121.0, 25.1), (121.1, 25.1), (121.1, 25.0)]))
        records = [
            ("64", 28, "jinshan", "金山都市計畫", True, shapely_wkb.loads(wkb)),
            ("999", 0, None, None, False, shapely_wkb.loads(wkb)),
        ]
        count = sync_mod.write_plan_boundary_sqlite_snapshot(records, db_path)
        assert count == 2

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT source_key, source_sdf_id, plan_id, plan_name, mapping_found "
                "FROM plan_boundary_polygons ORDER BY source_key"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [
            ("64", 28, "jinshan", "金山都市計畫", 1),
            ("999", 0, None, None, 0),
        ]

    def test_rerun_replaces_not_appends(self, tmp_path):
        db_path = str(tmp_path / "out.sqlite3")
        wkb = shapely_wkb.loads(shapely_wkb.dumps(Polygon([(121.0, 25.0), (121.0, 25.1), (121.1, 25.1), (121.1, 25.0)])))
        records = [("64", 28, "jinshan", "金山都市計畫", True, wkb)]
        sync_mod.write_plan_boundary_sqlite_snapshot(records, db_path)
        sync_mod.write_plan_boundary_sqlite_snapshot(records, db_path)
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT COUNT(*) FROM plan_boundary_polygons").fetchone()[0]
        conn.close()
        assert total == 1  # not 2 -- a rerun must not silently accumulate duplicates


class TestSyncPlanBoundary:
    def test_builds_queryable_snapshot_from_local_fixtures(self, tmp_path, monkeypatch):
        """End-to-end (minus the network, which this function never touches
        anyway): synthetic boundary shapefile + registry -> sync_plan_
        boundary() -> DatasetRegistry row a fresh RealUrbanPlanBoundary
        Provider can query -- proves the wiring, not just each piece in
        isolation."""
        boundary_base = str(tmp_path / "boundaryfixture")
        _write_plan_boundary_fixture_shapefile(boundary_base, [(10, 1, PLAN_BOUNDARY_COVERING_SQUARES)])
        boundary_zip = _zip_shapefile(boundary_base)

        registry_path = tmp_path / "id_registry.json"
        registry_path.write_text(json.dumps({"entries": [
            {"source_key": "10", "source_sdf_id": 1, "plan_id": "test_plan", "plan_name": "測試都市計畫"},
        ]}), encoding="utf-8")

        monkeypatch.setattr(sync_mod, "PLAN_BOUNDARY_ZIP_PATH", boundary_zip)
        monkeypatch.setattr(sync_mod, "PLAN_BOUNDARY_ID_REGISTRY_PATH", str(registry_path))
        monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "registry.sqlite3"))

        out_dir = str(tmp_path / "snapshots")
        info = sync_mod.sync_plan_boundary(out_dir=out_dir)

        assert info.record_count == 1
        assert info.dataset_id == sync_mod.PLAN_BOUNDARY_DATASET_ID
        assert os.path.exists(info.local_path)

        # A rerun must overwrite the DatasetRegistry row, not fail/duplicate.
        info2 = sync_mod.sync_plan_boundary(out_dir=out_dir)
        assert info2.record_count == 1


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
