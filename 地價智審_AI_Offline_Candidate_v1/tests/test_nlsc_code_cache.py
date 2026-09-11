# -*- coding: utf-8 -*-
"""
Direct unit tests for providers/nlsc_code_cache.py (Phase API-2.3). Uses a
real sqlite3 file per test (tmp_path-scoped), same convention as
tests/test_cadastral_dataset_cache.py -- this module IS the storage layer.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

import pytest  # noqa: E402

from nlsc_code_cache import NlscCodeCache, NlscCodeCacheError  # noqa: E402


def _cache(tmp_path):
    return NlscCodeCache(db_path=str(tmp_path / "nlsc_codes.sqlite3"))


def test_lookup_county_before_sync_raises(tmp_path):
    cache = _cache(tmp_path)
    with pytest.raises(NlscCodeCacheError):
        cache.lookup_county("新北市")


def test_get_current_counties_before_sync_raises(tmp_path):
    cache = _cache(tmp_path)
    with pytest.raises(NlscCodeCacheError):
        cache.get_current_counties()


def test_stage_and_promote_counties_round_trip(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "F", "county_name": "新北市"}])
    result = cache.promote_counties(source_service="ListCounty", source_url="http://x")
    assert result["record_count"] == 1
    rows = cache.lookup_county("新北市")
    assert len(rows) == 1
    assert rows[0]["county_code"] == "F"


def test_promote_with_empty_staging_raises_and_leaves_current_unchanged(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "F", "county_name": "新北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")

    cache.begin_staging_run("counties")  # clears staging, CURRENT untouched
    with pytest.raises(NlscCodeCacheError):
        cache.promote_counties(source_service="ListCounty", source_url="http://x")

    rows = cache.lookup_county("新北市")
    assert len(rows) == 1  # still there -- promote never touched CURRENT


def test_lookup_county_not_found_returns_empty_list(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "F", "county_name": "新北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")

    assert cache.lookup_county("不存在市") == []


def test_lookup_town_ambiguous_returns_all_candidates(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("towns")
    cache.stage_towns("F", [
        {"town_code": "F25", "town_name": "重複區"},
        {"town_code": "F99", "town_name": "重複區"},
    ])
    cache.promote_towns(source_service="ListTown", source_url="http://x")

    rows = cache.lookup_town("F", "重複區")
    assert len(rows) == 2
    codes = {r["town_code"] for r in rows}
    assert codes == {"F25", "F99"}


def test_lookup_section_scoped_by_county_and_town(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("sections")
    cache.stage_sections("F", "F25", [
        {"office": "FD", "section_code": "1027", "section_name": "金美段"},
    ])
    cache.promote_sections(source_service="ListLandSection", source_url="http://x")

    rows = cache.lookup_section("F", "F25", "金美段")
    assert len(rows) == 1
    assert rows[0]["section_code"] == "1027"
    assert rows[0]["office"] == "FD"

    # Same section name under a different town must not match.
    assert cache.lookup_section("F", "F99", "金美段") == []


def test_mark_staging_failed_sets_status_and_preserves_current(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("counties")
    cache.stage_counties([{"county_code": "F", "county_name": "新北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")

    cache.begin_staging_run("counties", note="second run")
    cache.mark_staging_failed("counties", note="network timeout")
    status = cache.get_staging_status("counties")
    assert status["status"] == "FAILED"
    assert status["note"] == "network timeout"
    assert cache.lookup_county("新北市")[0]["county_code"] == "F"


def test_checksum_deterministic_regardless_of_row_order(tmp_path):
    cache_a = _cache(tmp_path / "a")
    cache_a.begin_staging_run("counties")
    cache_a.stage_counties([
        {"county_code": "F", "county_name": "新北市"},
        {"county_code": "A", "county_name": "臺北市"},
    ])
    result_a = cache_a.promote_counties(source_service="ListCounty", source_url="http://x")

    cache_b = _cache(tmp_path / "b")
    cache_b.begin_staging_run("counties")
    cache_b.stage_counties([
        {"county_code": "A", "county_name": "臺北市"},
        {"county_code": "F", "county_name": "新北市"},
    ])
    result_b = cache_b.promote_counties(source_service="ListCounty", source_url="http://x")

    assert result_a["checksum_sha256"] == result_b["checksum_sha256"]
