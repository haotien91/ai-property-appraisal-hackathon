# -*- coding: utf-8 -*-
"""
Direct unit tests for providers/facility_dataset_cache.py (Phase API-2).
Same testing philosophy as tests/test_cadastral_dataset_cache.py: real
sqlite3 file per test (tmp_path-scoped), since this module IS the storage
layer.
"""
import os
import sqlite3
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

from facility_dataset_cache import (  # noqa: E402
    FacilityDatasetCache, FacilityDatasetCacheError, SCOPE_ALL,
    compute_canonical_checksum_v1, STAGING_STATUS_FAILED, STAGING_STATUS_PROMOTED,
)


def _cache(tmp_path):
    return FacilityDatasetCache(db_path=str(tmp_path / "facility_cache.sqlite3"))


def _row(facility_type, name, district, lat=None, lon=None, coordinate_status="WGS84", facility_id=None):
    return {
        "facility_type": facility_type, "facility_id": facility_id or name, "district": district,
        "name": name, "address": f"{district}某路某號（{name}）", "latitude": lat, "longitude": lon,
        "coordinate_status": coordinate_status, "source_dataset_id": "DS", "source_authority": "測試機關",
    }


# -- checksum -----------------------------------------------------------

def test_checksum_order_independent():
    a = [_row("SCHOOL", "甲國小", "金山區", 25.1, 121.1), _row("SCHOOL", "乙國小", "金山區", 25.2, 121.2)]
    b = list(reversed(a))
    assert compute_canonical_checksum_v1(a) == compute_canonical_checksum_v1(b)


def test_checksum_sensitive_to_content():
    a = [_row("SCHOOL", "甲國小", "金山區", 25.1, 121.1)]
    b = [_row("SCHOOL", "甲國小", "金山區", 25.9, 121.9)]
    assert compute_canonical_checksum_v1(a) != compute_canonical_checksum_v1(b)


# -- staging/promote atomicity -------------------------------------------

def test_staging_never_visible_to_lookup(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS1")
    cache.stage_facility_records("DS1", SCOPE_ALL, [_row("SCHOOL", "甲國小", "金山區", 25.1, 121.1)])
    assert cache.get_snapshot_meta("DS1", SCOPE_ALL) is None
    try:
        cache.lookup_facilities("DS1", SCOPE_ALL, "SCHOOL")
        assert False, "should have raised -- never synced (staging is not CURRENT)"
    except FacilityDatasetCacheError:
        pass


def test_successful_promote_replaces_current_atomically(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS1")
    cache.stage_facility_records("DS1", SCOPE_ALL, [_row("SCHOOL", "甲國小", "金山區", 25.1, 121.1)])
    result = cache.promote_facility_staging_to_current(
        dataset_id="DS1", dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    assert result["total_record_count"] == 1
    found = cache.lookup_facilities("DS1", SCOPE_ALL, "SCHOOL")
    assert len(found) == 1
    assert cache.get_staging_status("DS1")["status"] == STAGING_STATUS_PROMOTED
    # staging cleared after successful promote
    conn = sqlite3.connect(cache.db_path)
    rows = conn.execute("SELECT * FROM facility_records_staging WHERE dataset_id=?", ("DS1",)).fetchall()
    conn.close()
    assert rows == []


def test_promote_rejects_scope_with_no_staged_data(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS1")
    cache.stage_facility_records("DS1", "板橋區", [_row("SCHOOL", "甲國小", "板橋區", 25.1, 121.1)])
    try:
        cache.promote_facility_staging_to_current(
            dataset_id="DS1", dataset_name="T", source_url="u", source_authority="a",
            scope_keys=["板橋區", "金山區"],  # 金山區 never staged
        )
        assert False, "should have raised"
    except FacilityDatasetCacheError:
        pass
    assert cache.get_snapshot_meta("DS1", "板橋區") is None


def test_last_known_good_survives_failed_resync(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS1")
    cache.stage_facility_records("DS1", SCOPE_ALL, [_row("SCHOOL", "舊國小", "金山區", 25.1, 121.1)])
    seed = cache.promote_facility_staging_to_current(
        dataset_id="DS1", dataset_name="VersionA", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    cache.begin_staging_run("DS1")
    cache.stage_facility_records("DS1", "板橋區", [_row("SCHOOL", "新國小", "板橋區", 25.2, 121.2)])
    try:
        cache.promote_facility_staging_to_current(
            dataset_id="DS1", dataset_name="VersionB", source_url="u", source_authority="a",
            scope_keys=[SCOPE_ALL, "板橋區"],  # SCOPE_ALL was overwritten by begin_staging_run's clear
        )
        assert False
    except FacilityDatasetCacheError:
        cache.mark_staging_failed("DS1", note="simulated failure")

    meta = cache.get_snapshot_meta("DS1", SCOPE_ALL)
    assert meta["checksum_sha256"] == seed["promoted"][0]["checksum_sha256"]
    assert meta["dataset_name"] == "VersionA"
    assert cache.get_staging_status("DS1")["status"] == STAGING_STATUS_FAILED


def test_snapshot_unavailable_raises(tmp_path):
    cache = _cache(tmp_path)
    try:
        cache.lookup_facilities("NEVER", SCOPE_ALL, "SCHOOL")
        assert False
    except FacilityDatasetCacheError:
        pass


def test_empty_type_returns_empty_list_not_error(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS1")
    cache.stage_facility_records("DS1", SCOPE_ALL, [_row("SCHOOL", "甲國小", "金山區", 25.1, 121.1)])
    cache.promote_facility_staging_to_current(
        dataset_id="DS1", dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    # Synced, but no STATION rows at all -- a real, valid "0 matches", not an error.
    stations = cache.lookup_facilities("DS1", SCOPE_ALL, "STATION")
    assert stations == []


def test_lookup_deterministic_order_independent_of_insertion(tmp_path):
    records_forward = [
        _row("SCHOOL", "丙國小", "金山區", 25.3, 121.3), _row("SCHOOL", "甲國小", "金山區", 25.1, 121.1),
        _row("SCHOOL", "乙國小", "金山區", 25.2, 121.2),
    ]
    cache_a = _cache(tmp_path / "a")
    cache_a.begin_staging_run("DS1")
    cache_a.stage_facility_records("DS1", SCOPE_ALL, records_forward)
    cache_a.promote_facility_staging_to_current(
        dataset_id="DS1", dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    cache_b = _cache(tmp_path / "b")
    cache_b.begin_staging_run("DS1")
    cache_b.stage_facility_records("DS1", SCOPE_ALL, list(reversed(records_forward)))
    cache_b.promote_facility_staging_to_current(
        dataset_id="DS1", dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    order_a = [r["name"] for r in cache_a.lookup_facilities("DS1", SCOPE_ALL, "SCHOOL")]
    order_b = [r["name"] for r in cache_b.lookup_facilities("DS1", SCOPE_ALL, "SCHOOL")]
    # The exact order is whatever sorted((district,name,facility_id)) produces
    # (Unicode codepoint order for these Chinese names, not "intuitive" 甲乙丙
    # order) -- what matters is that it is THE SAME regardless of insertion
    # order, not what the order happens to be.
    assert order_a == order_b
    assert set(order_a) == {"甲國小", "乙國小", "丙國小"}
