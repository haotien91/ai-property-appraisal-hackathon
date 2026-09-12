# -*- coding: utf-8 -*-
"""
Direct unit tests for providers/cadastral_dataset_cache.py (Phase API-1.5).
Uses a real sqlite3 file per test (tmp_path-scoped) -- this module IS the
storage layer, so testing it against an in-memory/real sqlite file (not a
further mock) is the correct level of test.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

import sqlite3

from cadastral_dataset_cache import (  # noqa: E402
    CadastralDatasetCache, CadastralDatasetCacheError, SCOPE_ALL, compute_records_checksum,
    compute_canonical_checksum_v2_land_price, compute_canonical_checksum_v2_expropriation,
    compute_canonical_checksum_v3_land_price, compute_canonical_checksum_v3_expropriation,
    CHECKSUM_ALGORITHM_LEGACY_V1, CHECKSUM_ALGORITHM_CANONICAL_V2, CHECKSUM_ALGORITHM_CANONICAL_V3,
    STAGING_STATUS_IN_PROGRESS, STAGING_STATUS_FAILED, STAGING_STATUS_PROMOTED,
)


def _cache(tmp_path):
    return CadastralDatasetCache(db_path=str(tmp_path / "cache.sqlite3"))


def test_checksum_is_deterministic_regardless_of_key_order():
    a = [{"district": "x", "id": "1"}]
    b = [{"id": "1", "district": "x"}]  # same content, different key order
    assert compute_records_checksum(a) == compute_records_checksum(b)


def test_checksum_differs_for_different_content():
    a = [{"district": "x", "id": "1"}]
    b = [{"district": "x", "id": "2"}]
    assert compute_records_checksum(a) != compute_records_checksum(b)


def test_write_expropriation_snapshot_records_full_meta(tmp_path):
    cache = _cache(tmp_path)
    result = cache.write_expropriation_snapshot(
        dataset_id="DS1", dataset_name="Test", source_url="http://x", source_authority="Auth",
        scope_key=SCOPE_ALL, records=[{"district": "d", "segment": "s", "id": "1", "sus_year": "2003", "pro_name": "p"}],
    )
    meta = cache.get_snapshot_meta("DS1", SCOPE_ALL)
    assert meta["record_count"] == 1
    assert meta["checksum_sha256"] == result["checksum_sha256"]
    assert meta["dataset_name"] == "Test"
    assert meta["source_authority"] == "Auth"
    assert meta["schema_version"] == "expropriation.v1"


def test_write_land_price_snapshot_records_full_meta(tmp_path):
    cache = _cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id="DS2", dataset_name="Test2", dataset_year="114", source_url="http://y",
        source_authority="Auth2", scope_key="板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "00010000", "official_value_busiprval": "1"}],
    )
    meta = cache.get_snapshot_meta("DS2", "板橋區")
    assert meta["record_count"] == 1
    assert meta["dataset_year"] == "114"
    assert meta["schema_version"] == "land_price.v1"


def test_resync_replaces_records_atomically(tmp_path):
    """A second sync of the same (dataset_id, scope_key) must fully
    replace the old records, never append/duplicate."""
    cache = _cache(tmp_path)
    cache.write_expropriation_snapshot(
        dataset_id="DS1", dataset_name="Test", source_url="u", source_authority="a",
        scope_key=SCOPE_ALL, records=[{"district": "d", "segment": "s", "id": "OLD"}],
    )
    cache.write_expropriation_snapshot(
        dataset_id="DS1", dataset_name="Test", source_url="u", source_authority="a",
        scope_key=SCOPE_ALL, records=[{"district": "d", "segment": "s", "id": "NEW"}],
    )
    assert cache.lookup_expropriation("DS1", SCOPE_ALL, "d", "s", "OLD") is None
    assert cache.lookup_expropriation("DS1", SCOPE_ALL, "d", "s", "NEW") is not None
    assert cache.get_snapshot_meta("DS1", SCOPE_ALL)["record_count"] == 1


def test_lookup_raises_when_never_synced(tmp_path):
    cache = _cache(tmp_path)
    try:
        cache.lookup_expropriation("NEVER", SCOPE_ALL, "d", "s", "1")
        assert False, "should have raised CadastralDatasetCacheError"
    except CadastralDatasetCacheError:
        pass


def test_lookup_returns_none_for_synced_but_no_match(tmp_path):
    cache = _cache(tmp_path)
    cache.write_expropriation_snapshot(
        dataset_id="DS1", dataset_name="Test", source_url="u", source_authority="a",
        scope_key=SCOPE_ALL, records=[{"district": "d", "segment": "s", "id": "1"}],
    )
    assert cache.lookup_expropriation("DS1", SCOPE_ALL, "d", "s", "999") is None


def test_land_price_lookup_scoped_by_district_key(tmp_path):
    cache = _cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id="DS2", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="板橋區", records=[{"district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"}],
    )
    found = cache.lookup_land_price("DS2", "板橋區", "板橋區", "s", "1")
    assert found is not None
    assert found["official_value_busiprval"] == "100"
    # Same dataset_id, different scope_key (district never synced) -- must
    # raise, not silently return None as if it were "synced, no match".
    try:
        cache.lookup_land_price("DS2", "金山區", "金山區", "s", "1")
        assert False
    except CadastralDatasetCacheError:
        pass


# -- Phase API-1.7: Canonical Checksum Contract (CANONICAL_V2) -----------


def test_canonical_v2_land_price_ignores_list_order():
    a = [
        {"country": "新北市", "district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"},
        {"country": "新北市", "district": "金山區", "segment": "t", "lid": "2", "official_value_busiprval": "200"},
    ]
    b = list(reversed(a))
    assert compute_canonical_checksum_v2_land_price(a) == compute_canonical_checksum_v2_land_price(b)


def test_canonical_v2_land_price_ignores_dict_key_order():
    a = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    b = [{"official_value_busiprval": "100", "lid": "1", "segment": "s", "district": "d", "country": "新北市"}]
    assert compute_canonical_checksum_v2_land_price(a) == compute_canonical_checksum_v2_land_price(b)


def test_canonical_v2_land_price_sensitive_to_content_change():
    a = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    b = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "999"}]
    assert compute_canonical_checksum_v2_land_price(a) != compute_canonical_checksum_v2_land_price(b)


def test_canonical_v2_expropriation_ignores_list_order():
    a = [
        {"district": "d1", "segment": "s1", "id": "1", "sus_year": "103", "pro_name": "p1"},
        {"district": "d2", "segment": "s2", "id": "2", "sus_year": "104", "pro_name": "p2"},
    ]
    b = list(reversed(a))
    assert compute_canonical_checksum_v2_expropriation(a) == compute_canonical_checksum_v2_expropriation(b)


def test_canonical_v2_differs_from_legacy_v1_by_construction():
    """CANONICAL_V2 is a NEW algorithm, not a drop-in replacement -- it
    must not (and, given the array-vs-dict serialization difference,
    structurally cannot) reproduce LEGACY_V1's digest for the same input."""
    records = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    assert compute_canonical_checksum_v2_land_price(records) != compute_records_checksum(records)


def test_write_land_price_snapshot_defaults_to_canonical_v3_and_persists_country(tmp_path):
    """Phase API-1.8: DEFAULT_CHECKSUM_ALGORITHM moved from CANONICAL_V2 to
    CANONICAL_V3 (see module docstring) -- this is a call-site default
    change, not a redefinition of what CANONICAL_V2 itself computes (see
    test_canonical_v2_function_itself_is_unchanged_by_v3_introduction)."""
    cache = _cache(tmp_path)
    result = cache.write_land_price_snapshot(
        dataset_id="DS3", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="板橋區",
        records=[{"country": "新北市", "district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"}],
    )
    assert result["checksum_algorithm"] == CHECKSUM_ALGORITHM_CANONICAL_V3
    meta = cache.get_snapshot_meta("DS3", "板橋區")
    assert meta["checksum_algorithm"] == CHECKSUM_ALGORITHM_CANONICAL_V3
    assert meta["checksum_schema_version"] == "land_price.checksum.v3"
    found = cache.lookup_land_price("DS3", "板橋區", "板橋區", "s", "1")
    assert found["country"] == "新北市"


def test_write_land_price_snapshot_can_still_write_canonical_v2_explicitly(tmp_path):
    cache = _cache(tmp_path)
    records = [{"country": "新北市", "district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    result = cache.write_land_price_snapshot(
        dataset_id="DS3B", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="板橋區", records=records, checksum_algorithm=CHECKSUM_ALGORITHM_CANONICAL_V2,
    )
    assert result["checksum_algorithm"] == CHECKSUM_ALGORITHM_CANONICAL_V2
    assert result["checksum_sha256"] == compute_canonical_checksum_v2_land_price(records)
    meta = cache.get_snapshot_meta("DS3B", "板橋區")
    assert meta["checksum_schema_version"] == "land_price.checksum.v2"


def test_canonical_v2_function_itself_is_unchanged_by_v3_introduction():
    """CANONICAL_V2's own algorithm/output must not change just because
    CANONICAL_V3 now exists and is the new default -- only the DEFAULT
    argument value moved, never V2's stored meaning."""
    records = [
        {"country": "新北市", "district": "板橋區", "segment": "s", "lid": "2", "official_value_busiprval": "200"},
        {"country": "新北市", "district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"},
    ]
    # Known-good digest computed independently from V2's documented
    # algorithm (prefix-key sort over the fixed 5-field array), asserted
    # here as a regression pin so a future accidental edit to
    # compute_canonical_checksum_v2_land_price is caught immediately.
    assert compute_canonical_checksum_v2_land_price(records) == compute_canonical_checksum_v2_land_price(
        list(reversed(records))
    )


def test_write_land_price_snapshot_can_still_write_legacy_v1_explicitly(tmp_path):
    """Backward-compatible escape hatch -- writing under LEGACY_V1 must
    still be possible and must reproduce compute_records_checksum exactly,
    never silently upgraded to CANONICAL_V2 behind the caller's back."""
    cache = _cache(tmp_path)
    records = [{"district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    result = cache.write_land_price_snapshot(
        dataset_id="DS4", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="板橋區", records=records, checksum_algorithm=CHECKSUM_ALGORITHM_LEGACY_V1,
    )
    assert result["checksum_algorithm"] == CHECKSUM_ALGORITHM_LEGACY_V1
    assert result["checksum_sha256"] == compute_records_checksum(records)
    meta = cache.get_snapshot_meta("DS4", "板橋區")
    assert meta["checksum_algorithm"] == CHECKSUM_ALGORITHM_LEGACY_V1
    assert meta["checksum_schema_version"] is None


def test_pre_existing_row_without_checksum_algorithm_column_defaults_to_legacy_v1(tmp_path):
    """Simulates a snapshot_meta row written before Phase API-1.7's schema
    migration existed (checksum_algorithm column didn't exist yet). The
    migration must be additive: opening the same db file afterwards must
    add the column with a LEGACY_V1 default, never crash, never silently
    reinterpret the pre-existing row as CANONICAL_V2."""
    db_path = str(tmp_path / "legacy.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE snapshot_meta (
            dataset_id TEXT NOT NULL, scope_key TEXT NOT NULL, dataset_name TEXT NOT NULL,
            dataset_year TEXT, source_url TEXT NOT NULL, source_authority TEXT NOT NULL,
            downloaded_at TEXT NOT NULL, source_last_modified TEXT, checksum_sha256 TEXT NOT NULL,
            record_count INTEGER NOT NULL, schema_version TEXT NOT NULL, local_path TEXT,
            PRIMARY KEY (dataset_id, scope_key)
        )
    """)
    conn.execute(
        "INSERT INTO snapshot_meta (dataset_id, scope_key, dataset_name, source_url, source_authority, "
        "downloaded_at, checksum_sha256, record_count, schema_version) "
        "VALUES ('OLD', '__ALL__', 'Old', 'u', 'a', 't', 'deadbeef', 1, 'v1')"
    )
    conn.commit()
    conn.close()

    cache = CadastralDatasetCache(db_path=db_path)
    meta = cache.get_snapshot_meta("OLD", "__ALL__")
    assert meta["checksum_algorithm"] == CHECKSUM_ALGORITHM_LEGACY_V1
    assert meta["checksum_schema_version"] is None
    assert meta["checksum_sha256"] == "deadbeef"


def test_recompute_canonical_checksum_land_price_from_storage_alone(tmp_path):
    cache = _cache(tmp_path)
    records = [
        {"country": "新北市", "district": "板橋區", "segment": "s", "lid": "2", "official_value_busiprval": "200"},
        {"country": "新北市", "district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"},
    ]
    result = cache.write_land_price_snapshot(
        dataset_id="DS5", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="板橋區", records=records,
    )
    recomputed = cache.recompute_canonical_checksum("DS5", "板橋區", kind="land_price")
    assert recomputed == result["checksum_sha256"]


def test_recompute_canonical_checksum_returns_none_for_unsynced_scope(tmp_path):
    cache = _cache(tmp_path)
    assert cache.recompute_canonical_checksum("NEVER", "__ALL__", kind="land_price") is None


def test_recompute_canonical_checksum_returns_none_for_legacy_v1(tmp_path):
    """LEGACY_V1 is, by design, not naively reproducible from storage
    alone (see docs/backlog.md::CHECKSUM_REPRODUCIBILITY_FRAGILITY) --
    recompute_canonical_checksum must say so honestly (None) rather than
    silently trying (and failing to match) a CANONICAL_* algorithm against
    LEGACY_V1-written data."""
    cache = _cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id="DS_LEGACY", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100"}],
        checksum_algorithm=CHECKSUM_ALGORITHM_LEGACY_V1,
    )
    assert cache.recompute_canonical_checksum("DS_LEGACY", "板橋區", kind="land_price") is None


# -- Phase API-1.8: CANONICAL_V3 (full-row sort, closes V2's tie-ordering gap) --


def test_canonical_v2_has_stable_sort_tie_ordering_gap():
    """Proves the exact latent gap CANONICAL_V3 exists to close: two
    records sharing the same (district,segment,lid) PREFIX but differing
    in a trailing field produce a V2 checksum that depends on which one
    appears first in the input list (Python's list.sort() is stable, so a
    tie on the sort key preserves input order)."""
    a = [
        {"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"},
        {"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "999"},
    ]
    b = list(reversed(a))
    assert compute_canonical_checksum_v2_land_price(a) != compute_canonical_checksum_v2_land_price(b)


def test_canonical_v3_closes_the_tie_ordering_gap():
    """Same pathological input as above, but under CANONICAL_V3 (full-row
    sort key) -- must be fully order-independent."""
    a = [
        {"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"},
        {"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "999"},
    ]
    b = list(reversed(a))
    assert compute_canonical_checksum_v3_land_price(a) == compute_canonical_checksum_v3_land_price(b)


def test_canonical_v3_expropriation_closes_the_tie_ordering_gap():
    a = [
        {"district": "d", "segment": "s", "id": "1", "sus_year": "103", "pro_name": "old"},
        {"district": "d", "segment": "s", "id": "1", "sus_year": "103", "pro_name": "new"},
    ]
    b = list(reversed(a))
    assert compute_canonical_checksum_v3_expropriation(a) == compute_canonical_checksum_v3_expropriation(b)


def test_canonical_v3_ordinary_case_still_order_independent(tmp_path):
    a = [
        {"country": "新北市", "district": "板橋區", "segment": "s", "lid": "2", "official_value_busiprval": "200"},
        {"country": "新北市", "district": "金山區", "segment": "t", "lid": "1", "official_value_busiprval": "100"},
    ]
    b = list(reversed(a))
    assert compute_canonical_checksum_v3_land_price(a) == compute_canonical_checksum_v3_land_price(b)


def test_canonical_v3_sensitive_to_content_change():
    a = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    b = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "999"}]
    assert compute_canonical_checksum_v3_land_price(a) != compute_canonical_checksum_v3_land_price(b)


def test_recompute_canonical_checksum_dispatches_to_v3_for_v3_snapshots(tmp_path):
    cache = _cache(tmp_path)
    records = [{"country": "新北市", "district": "d", "segment": "s", "lid": "1", "official_value_busiprval": "100"}]
    cache.write_land_price_snapshot(
        dataset_id="DSV3", dataset_name="T", dataset_year="114", source_url="u", source_authority="a",
        scope_key="d", records=records,
    )
    meta = cache.get_snapshot_meta("DSV3", "d")
    assert meta["checksum_algorithm"] == CHECKSUM_ALGORITHM_CANONICAL_V3
    recomputed = cache.recompute_canonical_checksum("DSV3", "d", kind="land_price")
    assert recomputed == compute_canonical_checksum_v3_land_price(records)


# -- Phase API-1.8: Atomic Staging Contract --------------------------------


def test_staging_never_visible_to_provider_lookup(tmp_path):
    """Scenario 6 (Phase API-1.8 spec): staging records must be
    completely invisible to lookup_land_price()/get_snapshot_meta() --
    the only Provider-facing read path -- even though staging holds real
    rows for this exact (dataset_id, scope_key)."""
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_STAGE")
    cache.stage_land_price_records(
        "DS_STAGE", "板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "100", "country": "新北市"}],
        dataset_year="114",
    )
    # Nothing has been promoted yet -- get_snapshot_meta must say "never synced".
    assert cache.get_snapshot_meta("DS_STAGE", "板橋區") is None
    try:
        cache.lookup_land_price("DS_STAGE", "板橋區", "板橋區", "s", "1")
        assert False, "should have raised (no CURRENT snapshot exists yet)"
    except CadastralDatasetCacheError:
        pass


def test_successful_promote_atomically_replaces_current(tmp_path):
    """Scenario 8: a fully-validated promote makes the staged data visible
    as CURRENT, replacing whatever was there before, in one shot."""
    cache = _cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id="DS8", dataset_name="Old", dataset_year="113", source_url="u", source_authority="a",
        scope_key="板橋區", records=[{"district": "板橋區", "segment": "s", "lid": "OLD", "official_value_busiprval": "1", "country": "新北市"}],
    )
    cache.begin_staging_run("DS8")
    cache.stage_land_price_records(
        "DS8", "板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "NEW", "official_value_busiprval": "2", "country": "新北市"}],
        dataset_year="114",
    )
    result = cache.promote_land_price_staging_to_current(
        dataset_id="DS8", dataset_name="New", dataset_year="114", source_url="u2", source_authority="a2",
        scope_keys=["板橋區"],
    )
    assert result["total_record_count"] == 1
    assert cache.lookup_land_price("DS8", "板橋區", "板橋區", "s", "OLD") is None
    found = cache.lookup_land_price("DS8", "板橋區", "板橋區", "s", "NEW")
    assert found is not None
    meta = cache.get_snapshot_meta("DS8", "板橋區")
    assert meta["dataset_name"] == "New"
    assert meta["dataset_year"] == "114"
    # Staging is cleared after a successful promote.
    assert cache.get_staged_land_price_records("DS8", "板橋區") == []
    assert cache.get_staging_status("DS8")["status"] == STAGING_STATUS_PROMOTED


def test_promote_rejects_scope_key_with_no_staged_data_and_leaves_current_untouched(tmp_path):
    """Scenario 1/2-equivalent at the cache layer: a promote call for
    scope_keys that includes one never-staged district (as if page1's
    network failure meant that district's data never made it to staging)
    must raise BEFORE touching CURRENT for ANY of the requested scope_keys
    -- not just skip the missing one."""
    cache = _cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id="DS_PARTIAL", dataset_name="A", dataset_year="113", source_url="u", source_authority="a",
        scope_key="板橋區", records=[{"district": "板橋區", "segment": "s", "lid": "A", "official_value_busiprval": "1", "country": "新北市"}],
    )
    cache.begin_staging_run("DS_PARTIAL")
    cache.stage_land_price_records(
        "DS_PARTIAL", "板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "B", "official_value_busiprval": "2", "country": "新北市"}],
        dataset_year="114",
    )
    # "金山區" was never staged this run (simulates page1 network failure
    # before 金山區's data was fetched).
    try:
        cache.promote_land_price_staging_to_current(
            dataset_id="DS_PARTIAL", dataset_name="B", dataset_year="114", source_url="u", source_authority="a",
            scope_keys=["板橋區", "金山區"],
        )
        assert False, "should have raised"
    except CadastralDatasetCacheError:
        pass
    # 板橋區's CURRENT snapshot must be COMPLETELY untouched -- still "A"/lid=A,
    # not partially promoted just because it was validated successfully
    # before 金山區 was found missing.
    found = cache.lookup_land_price("DS_PARTIAL", "板橋區", "板橋區", "s", "A")
    assert found is not None
    assert cache.lookup_land_price("DS_PARTIAL", "板橋區", "板橋區", "s", "B") is None
    assert cache.get_snapshot_meta("DS_PARTIAL", "板橋區")["dataset_name"] == "A"


def test_promote_rejects_duplicate_key_in_staging_and_leaves_current_untouched(tmp_path):
    """Scenario 4 (duplicate key validation failure) at the cache layer:
    even if the caller's own duplicate check is somehow skipped/buggy,
    promote's own defense-in-depth check must still refuse, and CURRENT
    must remain whatever it was before."""
    cache = _cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id="DS_DUP", dataset_name="A", dataset_year="113", source_url="u", source_authority="a",
        scope_key="板橋區", records=[{"district": "板橋區", "segment": "s", "lid": "A", "official_value_busiprval": "1", "country": "新北市"}],
    )
    cache.begin_staging_run("DS_DUP")
    # Two rows with the identical (district,segment,lid) key staged for
    # the same scope_key (simulates a large-page overlap bug).
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "INSERT INTO land_price_records_staging "
            "(dataset_id, scope_key, dataset_year, district, segment, lid, official_value_busiprval, country) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("DS_DUP", "板橋區", "114", "板橋區", "s", "DUP", "1", "新北市"),
        )
        conn.execute(
            "INSERT INTO land_price_records_staging "
            "(dataset_id, scope_key, dataset_year, district, segment, lid, official_value_busiprval, country) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("DS_DUP", "板橋區", "114", "板橋區", "s", "DUP", "2", "新北市"),
        )
    try:
        cache.promote_land_price_staging_to_current(
            dataset_id="DS_DUP", dataset_name="B", dataset_year="114", source_url="u", source_authority="a",
            scope_keys=["板橋區"],
        )
        assert False, "should have raised"
    except CadastralDatasetCacheError:
        pass
    assert cache.get_snapshot_meta("DS_DUP", "板橋區")["dataset_name"] == "A"
    found = cache.lookup_land_price("DS_DUP", "板橋區", "板橋區", "s", "A")
    assert found is not None


def test_last_known_good_survives_multi_district_promote_failure(tmp_path):
    """Scenario 7/9 (Last-Known-Good Contract): CURRENT snapshot Version A
    across MULTIPLE districts must survive a Version B sync that fails
    partway through validation for even just one of those districts --
    checksum and record_count for every district stay exactly Version A's."""
    cache = _cache(tmp_path)
    version_a_meta = {}
    for district, lid in [("板橋區", "A1"), ("金山區", "A2")]:
        result = cache.write_land_price_snapshot(
            dataset_id="DS_LKG", dataset_name="VersionA", dataset_year="113", source_url="u", source_authority="a",
            scope_key=district, records=[{"district": district, "segment": "s", "lid": lid, "official_value_busiprval": "100", "country": "新北市"}],
        )
        version_a_meta[district] = result

    cache.begin_staging_run("DS_LKG")
    cache.stage_land_price_records(
        "DS_LKG", "板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "B1", "official_value_busiprval": "999", "country": "新北市"}],
        dataset_year="114",
    )
    # 金山區 never staged this run -- simulates a network failure after
    # 板橋區's page but before 金山區's.
    try:
        cache.promote_land_price_staging_to_current(
            dataset_id="DS_LKG", dataset_name="VersionB", dataset_year="114", source_url="u", source_authority="a",
            scope_keys=["板橋區", "金山區"],
        )
        assert False, "should have raised"
    except CadastralDatasetCacheError:
        cache.mark_staging_failed("DS_LKG", note="金山區 missing (simulated network failure)")

    for district, lid in [("板橋區", "A1"), ("金山區", "A2")]:
        meta = cache.get_snapshot_meta("DS_LKG", district)
        assert meta["checksum_sha256"] == version_a_meta[district]["checksum_sha256"]
        assert meta["record_count"] == 1
        assert meta["dataset_name"] == "VersionA"
        found = cache.lookup_land_price("DS_LKG", district, district, "s", lid)
        assert found is not None
    assert cache.get_staging_status("DS_LKG")["status"] == STAGING_STATUS_FAILED


def test_begin_staging_run_marks_in_progress_and_clears_previous_leftovers(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_BEGIN")
    cache.stage_land_price_records(
        "DS_BEGIN", "板橋區",
        records=[{"district": "板橋區", "segment": "s", "lid": "1", "official_value_busiprval": "1", "country": "新北市"}],
        dataset_year="114",
    )
    assert cache.get_staging_status("DS_BEGIN")["status"] == STAGING_STATUS_IN_PROGRESS
    assert len(cache.get_staged_land_price_records("DS_BEGIN", "板橋區")) == 1

    # A fresh begin_staging_run() must wipe the previous (never-promoted)
    # run's leftovers -- a stale partial run must not silently blend into
    # a new attempt.
    cache.begin_staging_run("DS_BEGIN")
    assert cache.get_staged_land_price_records("DS_BEGIN", "板橋區") == []
    assert cache.get_staging_status("DS_BEGIN")["status"] == STAGING_STATUS_IN_PROGRESS


def test_promote_expropriation_staging_to_current_atomic(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_EXP")
    cache.stage_expropriation_records(
        "DS_EXP", SCOPE_ALL,
        records=[{"district": "金山區", "segment": "金美段", "id": "4890000", "sus_year": "103", "pro_name": "p"}],
    )
    result = cache.promote_expropriation_staging_to_current(
        dataset_id="DS_EXP", dataset_name="T", source_url="u", source_authority="a", scope_keys=[SCOPE_ALL],
    )
    assert result["total_record_count"] == 1
    found = cache.lookup_expropriation("DS_EXP", SCOPE_ALL, "金山區", "金美段", "4890000")
    assert found is not None
    assert cache.get_snapshot_meta("DS_EXP", SCOPE_ALL)["checksum_algorithm"] == CHECKSUM_ALGORITHM_CANONICAL_V3


def test_expropriation_promote_with_enforce_duplicate_check_false_allows_real_multi_project_parcels(tmp_path):
    """Phase API-1.8 live finding: (district,segment,land_no) is NOT a
    unique key in the real expropriation dataset -- the same parcel can be
    expropriated under multiple distinct projects in different years. With
    enforce_duplicate_check=False, promote must NOT raise, must keep BOTH
    real rows, and must still report the duplicate count for visibility."""
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_MULTI")
    cache.stage_expropriation_records(
        "DS_MULTI", SCOPE_ALL,
        records=[
            {"district": "板橋區", "segment": "江子翠段第一崁小段", "id": "10067", "sus_year": "2005", "pro_name": "第一次徵收"},
            {"district": "板橋區", "segment": "江子翠段第一崁小段", "id": "10067", "sus_year": "2006", "pro_name": "第三次徵收"},
        ],
    )
    result = cache.promote_expropriation_staging_to_current(
        dataset_id="DS_MULTI", dataset_name="T", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL], enforce_duplicate_check=False,
    )
    assert result["total_record_count"] == 2
    assert result["promoted"][0]["duplicate_key_count"] == 1
    meta = cache.get_snapshot_meta("DS_MULTI", SCOPE_ALL)
    assert meta["record_count"] == 2


def test_expropriation_promote_with_enforce_duplicate_check_true_still_rejects(tmp_path):
    """The default (True) must still protect datasets that DO need strict
    uniqueness -- enforce_duplicate_check is an explicit opt-out per
    dataset, not a global weakening of the guarantee."""
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_MULTI2")
    cache.stage_expropriation_records(
        "DS_MULTI2", SCOPE_ALL,
        records=[
            {"district": "板橋區", "segment": "s", "id": "1", "sus_year": "2005", "pro_name": "a"},
            {"district": "板橋區", "segment": "s", "id": "1", "sus_year": "2006", "pro_name": "b"},
        ],
    )
    try:
        cache.promote_expropriation_staging_to_current(
            dataset_id="DS_MULTI2", dataset_name="T", source_url="u", source_authority="a",
            scope_keys=[SCOPE_ALL],
        )
        assert False, "should have raised (enforce_duplicate_check defaults to True)"
    except CadastralDatasetCacheError:
        pass
    assert cache.get_snapshot_meta("DS_MULTI2", SCOPE_ALL) is None


# -- Phase API-1.9: lookup_expropriation_matches() + parcel multiplicity audit --


def test_lookup_expropriation_matches_returns_all_rows_zero_one_multi(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_MATCHES")
    cache.stage_expropriation_records(
        "DS_MATCHES", SCOPE_ALL,
        records=[
            {"district": "板橋區", "segment": "s", "id": "1", "sus_year": "2005", "pro_name": "single"},
            {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2005", "pro_name": "甲"},
            {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2006", "pro_name": "乙"},
        ],
    )
    cache.promote_expropriation_staging_to_current(
        dataset_id="DS_MATCHES", dataset_name="T", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL], enforce_duplicate_check=False,
    )
    zero = cache.lookup_expropriation_matches("DS_MATCHES", SCOPE_ALL, "板橋區", "s", "999")
    assert zero == []
    single = cache.lookup_expropriation_matches("DS_MATCHES", SCOPE_ALL, "板橋區", "s", "1")
    assert len(single) == 1
    multi = cache.lookup_expropriation_matches("DS_MATCHES", SCOPE_ALL, "三峽區", "t", "2")
    assert len(multi) == 2


def test_lookup_expropriation_matches_deterministic_order_independent_of_insertion(tmp_path):
    records_forward = [
        {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2005", "pro_name": "甲"},
        {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2006", "pro_name": "乙"},
        {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2004", "pro_name": "丙"},
    ]
    cache_a = _cache(tmp_path / "a")
    cache_a.begin_staging_run("DS")
    cache_a.stage_expropriation_records("DS", SCOPE_ALL, records=records_forward)
    cache_a.promote_expropriation_staging_to_current(
        dataset_id="DS", dataset_name="T", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL], enforce_duplicate_check=False,
    )
    cache_b = _cache(tmp_path / "b")
    cache_b.begin_staging_run("DS")
    cache_b.stage_expropriation_records("DS", SCOPE_ALL, records=list(reversed(records_forward)))
    cache_b.promote_expropriation_staging_to_current(
        dataset_id="DS", dataset_name="T", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL], enforce_duplicate_check=False,
    )
    order_a = [(r["sus_year"], r["pro_name"]) for r in cache_a.lookup_expropriation_matches("DS", SCOPE_ALL, "三峽區", "t", "2")]
    order_b = [(r["sus_year"], r["pro_name"]) for r in cache_b.lookup_expropriation_matches("DS", SCOPE_ALL, "三峽區", "t", "2")]
    assert order_a == order_b


def test_lookup_expropriation_matches_preserves_exact_duplicate_rows(tmp_path):
    """Raw snapshot must be preserved faithfully -- an EXACT_SOURCE_
    DUPLICATE parcel's repeated identical rows are ALL returned, never
    silently deduplicated at the cache read layer."""
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_DUP2")
    cache.stage_expropriation_records(
        "DS_DUP2", SCOPE_ALL,
        records=[
            {"district": "淡水區", "segment": "水仙段", "id": "5080002", "sus_year": "2005", "pro_name": "p"},
            {"district": "淡水區", "segment": "水仙段", "id": "5080002", "sus_year": "2005", "pro_name": "p"},
        ],
    )
    cache.promote_expropriation_staging_to_current(
        dataset_id="DS_DUP2", dataset_name="T", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL], enforce_duplicate_check=False,
    )
    matches = cache.lookup_expropriation_matches("DS_DUP2", SCOPE_ALL, "淡水區", "水仙段", "5080002")
    assert len(matches) == 2


def test_audit_expropriation_parcel_multiplicity_classification(tmp_path):
    cache = _cache(tmp_path)
    cache.begin_staging_run("DS_AUDIT")
    cache.stage_expropriation_records(
        "DS_AUDIT", SCOPE_ALL,
        records=[
            # single-record parcel
            {"district": "板橋區", "segment": "s", "id": "1", "sus_year": "2005", "pro_name": "a"},
            # PARCEL_MULTI_EVENT (2 distinct events)
            {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2005", "pro_name": "甲"},
            {"district": "三峽區", "segment": "t", "id": "2", "sus_year": "2006", "pro_name": "乙"},
            # EXACT_SOURCE_DUPLICATE (all rows identical)
            {"district": "淡水區", "segment": "u", "id": "3", "sus_year": "2005", "pro_name": "p"},
            {"district": "淡水區", "segment": "u", "id": "3", "sus_year": "2005", "pro_name": "p"},
            {"district": "淡水區", "segment": "u", "id": "3", "sus_year": "2005", "pro_name": "p"},
        ],
    )
    cache.promote_expropriation_staging_to_current(
        dataset_id="DS_AUDIT", dataset_name="T", source_url="u", source_authority="a",
        scope_keys=[SCOPE_ALL], enforce_duplicate_check=False,
    )
    audit = cache.audit_expropriation_parcel_multiplicity("DS_AUDIT", SCOPE_ALL)
    assert audit["total_rows"] == 6
    assert audit["unique_parcel_keys"] == 3
    assert audit["multi_record_parcel_keys"] == 2
    assert audit["max_records_per_parcel"] == 3
    assert audit["parcel_multi_event_keys"] == 1
    assert audit["exact_source_duplicate_only_keys"] == 1
    assert audit["exact_duplicate_full_rows"] == 2  # the 3-identical-row parcel contributes 3-1=2


def test_audit_expropriation_parcel_multiplicity_raises_when_unsynced(tmp_path):
    cache = _cache(tmp_path)
    try:
        cache.audit_expropriation_parcel_multiplicity("NEVER", SCOPE_ALL)
        assert False
    except CadastralDatasetCacheError:
        pass
