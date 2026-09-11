# -*- coding: utf-8 -*-
"""
Failure-injection tests for scripts/sync_land_price_dataset.py's Phase
API-1.8 Atomic Staging Contract (Official API -> STAGING -> validation ->
[ALL PASS] -> atomic promote -> CURRENT). These exercise the SCRIPT's own
orchestration (network-failure/validation-failure handling, staging vs
CURRENT visibility, Last-Known-Good survival) with a fake `_fetch_page` --
never the real network -- so the suite stays offline/fast per this
codebase's convention (see scripts/smoke_test_real_providers.py for the
live counterpart).
"""
import os
import sys

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

import scripts.sync_land_price_dataset as sync_mod  # noqa: E402
from cadastral_dataset_cache import (  # noqa: E402
    CadastralDatasetCache, CadastralDatasetCacheError, STAGING_STATUS_FAILED, STAGING_STATUS_PROMOTED,
)


def _row(district, segment, lid, value, country="新北市"):
    return {"country": country, "district": district, "segment": segment, "lid": lid,
            "official_value_busiprval": value}


def _patch_common(monkeypatch, tmp_path, pages, total_hint=2, threshold=0.5):
    """`pages` is a list where each item is either a list of row-dicts
    (returned as that page) or an Exception instance (raised when that
    page index is reached). The page AFTER the last list-item that is
    shorter than PAGE_SIZE naturally ends the crawl (reached_end=True),
    matching the real script's short-page-means-end-of-dataset logic."""
    db_path = str(tmp_path / "cache.sqlite3")
    # main() parses argparse from real sys.argv by default -- pin it to a
    # clean baseline so pytest's own invocation args never leak in
    # (individual tests may override this afterward, e.g. --max-pages).
    monkeypatch.setattr(sys, "argv", ["sync_land_price_dataset.py"])
    monkeypatch.setattr(sync_mod, "CadastralDatasetCache", lambda: CadastralDatasetCache(db_path=db_path))
    monkeypatch.setattr(sync_mod, "PAGE_SIZE", 3)
    monkeypatch.setattr(sync_mod, "INTER_PAGE_DELAY_S", 0)
    monkeypatch.setattr(sync_mod, "EXPECTED_TOTAL_RECORD_COUNT_HINT", total_hint)
    monkeypatch.setattr(sync_mod, "_TOTAL_COUNT_DROP_ABORT_THRESHOLD", threshold)

    def fake_fetch_page(page):
        if page >= len(pages):
            return []
        item = pages[page]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(sync_mod, "_fetch_page", fake_fetch_page)
    return CadastralDatasetCache(db_path=db_path)


def _seed_current(cache, district, lid, value, dataset_name="VersionA"):
    return cache.write_land_price_snapshot(
        dataset_id=sync_mod.DATASET_ID, dataset_name=dataset_name, dataset_year="113",
        source_url="u", source_authority="a", scope_key=district,
        records=[_row(district, "s", lid, value)],
    )


# -- 1/2/3: network/malformed-response failure at various points ---------


def test_failure_after_page0(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "A1", "1"), _row("板橋區", "s", "A2", "2"), _row("板橋區", "s", "A3", "3")],
        ConnectionResetError("simulated WinError 10054"),
    ])
    seed = _seed_current(cache, "板橋區", "OLD", "999")
    with pytest.raises(ConnectionResetError):
        sync_mod.main()

    meta = cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")
    assert meta["checksum_sha256"] == seed["checksum_sha256"]
    assert cache.get_staging_status(sync_mod.DATASET_ID)["status"] == STAGING_STATUS_FAILED
    # Page0's fetched rows made it to STAGING (checkpointed) but not CURRENT.
    assert len(cache.get_staged_land_price_records(sync_mod.DATASET_ID, "板橋區")) == 3
    assert cache.lookup_land_price(sync_mod.DATASET_ID, "板橋區", "板橋區", "s", "OLD") is not None
    assert cache.lookup_land_price(sync_mod.DATASET_ID, "板橋區", "板橋區", "s", "A1") is None


def test_failure_after_page1(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "A1", "1"), _row("板橋區", "s", "A2", "2"), _row("板橋區", "s", "A3", "3")],
        [_row("金山區", "s", "B1", "1"), _row("金山區", "s", "B2", "2"), _row("金山區", "s", "B3", "3")],
        TimeoutError("simulated timeout"),
    ])
    seed_a = _seed_current(cache, "板橋區", "OLD_A", "999")
    seed_b = _seed_current(cache, "金山區", "OLD_B", "888")
    with pytest.raises(TimeoutError):
        sync_mod.main()

    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")["checksum_sha256"] == seed_a["checksum_sha256"]
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "金山區")["checksum_sha256"] == seed_b["checksum_sha256"]
    assert len(cache.get_staged_land_price_records(sync_mod.DATASET_ID, "金山區")) == 3


def test_malformed_page2(monkeypatch, tmp_path):
    """A malformed response (non-list JSON shape) surfaces from the real
    `_fetch_page` as a RuntimeError -- simulated directly here since
    `_fetch_page` itself is replaced for these orchestration-level tests."""
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "A1", "1"), _row("板橋區", "s", "A2", "2"), _row("板橋區", "s", "A3", "3")],
        [_row("金山區", "s", "B1", "1"), _row("金山區", "s", "B2", "2"), _row("金山區", "s", "B3", "3")],
        RuntimeError("Unexpected non-list response shape at page 2"),
    ])
    seed = _seed_current(cache, "板橋區", "OLD", "999")
    with pytest.raises(RuntimeError):
        sync_mod.main()
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")["checksum_sha256"] == seed["checksum_sha256"]


# -- 4/5: post-crawl validation failures ----------------------------------


def test_duplicate_key_validation_failure(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        # Short page (2 < PAGE_SIZE=3) -> reached_end immediately, but
        # contains a duplicate (district,segment,lid) key.
        [_row("板橋區", "s", "DUP", "1"), _row("板橋區", "s", "DUP", "2")],
    ], total_hint=1, threshold=0.0)
    seed = _seed_current(cache, "板橋區", "OLD", "999")
    with pytest.raises(sync_mod.SyncIntegrityError):
        sync_mod.main()
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")["checksum_sha256"] == seed["checksum_sha256"]
    assert cache.get_staging_status(sync_mod.DATASET_ID)["status"] == STAGING_STATUS_FAILED


def test_total_count_validation_failure(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "A1", "1")],  # short page, reached_end, only 1 row total
    ], total_hint=1000, threshold=0.5)  # need >= 500 to pass; only got 1
    seed = _seed_current(cache, "板橋區", "OLD", "999")
    with pytest.raises(sync_mod.SyncIntegrityError):
        sync_mod.main()
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")["checksum_sha256"] == seed["checksum_sha256"]


# -- 6/7/9: staging invisibility + Last-Known-Good survival --------------


def test_staging_never_visible_to_provider_after_failed_sync(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("金山區", "金美段", "04890000", "57305"), _row("金山區", "金美段", "X", "1"),
         _row("金山區", "金美段", "Y", "2")],
        ConnectionResetError("simulated"),
    ])
    with pytest.raises(ConnectionResetError):
        sync_mod.main()
    # 金山區 was never in CURRENT before this run and the run failed -- it
    # must still read as "never synced", not as "synced with page0's data".
    try:
        cache.lookup_land_price(sync_mod.DATASET_ID, "金山區", "金山區", "金美段", "04890000")
        assert False, "should have raised -- staging must never satisfy a Provider lookup"
    except CadastralDatasetCacheError:
        pass
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "金山區") is None


def test_checksum_and_record_count_survive_failed_sync(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "A1", "1"), _row("板橋區", "s", "A2", "2"), _row("板橋區", "s", "A3", "3")],
        ConnectionResetError("simulated"),
    ])
    seed = _seed_current(cache, "板橋區", "OLD", "999")
    with pytest.raises(ConnectionResetError):
        sync_mod.main()
    meta = cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")
    assert meta["checksum_sha256"] == seed["checksum_sha256"]
    assert meta["record_count"] == 1
    assert meta["dataset_name"] == "VersionA"


def test_golden_case_survives_failed_sync(monkeypatch, tmp_path):
    """Golden Case shape: 金山區/金美段/lid=04890000 (== land_no_main=489,
    land_no_sub=0 per LandNumberNormalizer.to_land_price_lid) must keep
    returning its last-known-good value even after a subsequent sync
    attempt fails partway through."""
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("金山區", "金美段", "04890000", "99999"),  # a DIFFERENT (wrong) value mid-crawl
         _row("金山區", "金美段", "X", "1"), _row("金山區", "金美段", "Y", "2")],
        ConnectionResetError("simulated"),
    ])
    seed = cache.write_land_price_snapshot(
        dataset_id=sync_mod.DATASET_ID, dataset_name="VersionA", dataset_year="113",
        source_url="u", source_authority="a", scope_key="金山區",
        records=[_row("金山區", "金美段", "04890000", "57305")],
    )
    with pytest.raises(ConnectionResetError):
        sync_mod.main()
    found = cache.lookup_land_price(sync_mod.DATASET_ID, "金山區", "金山區", "金美段", "04890000")
    assert found["official_value_busiprval"] == "57305"
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "金山區")["checksum_sha256"] == seed["checksum_sha256"]


# -- 8: successful sync atomically replaces CURRENT -----------------------


def test_successful_sync_atomically_replaces_current(monkeypatch, tmp_path):
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "NEW1", "1"), _row("板橋區", "s", "NEW2", "2")],  # short page, reached_end
    ], total_hint=2, threshold=0.5)
    _seed_current(cache, "板橋區", "OLD", "999")

    exit_code = sync_mod.main()
    assert exit_code == 0

    assert cache.lookup_land_price(sync_mod.DATASET_ID, "板橋區", "板橋區", "s", "OLD") is None
    found = cache.lookup_land_price(sync_mod.DATASET_ID, "板橋區", "板橋區", "s", "NEW1")
    assert found is not None
    meta = cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區")
    assert meta["dataset_name"] == sync_mod.DATASET_NAME
    assert meta["record_count"] == 2
    assert cache.get_staging_status(sync_mod.DATASET_ID)["status"] == STAGING_STATUS_PROMOTED
    assert cache.get_staged_land_price_records(sync_mod.DATASET_ID, "板橋區") == []


def test_partial_max_pages_run_never_promotes(monkeypatch, tmp_path):
    """--max-pages is an intentional PARTIAL/test-only run -- it must
    stage but never promote, and CURRENT must be left exactly as it was
    (even if there was no prior CURRENT at all)."""
    cache = _patch_common(monkeypatch, tmp_path, pages=[
        [_row("板橋區", "s", "A1", "1"), _row("板橋區", "s", "A2", "2"), _row("板橋區", "s", "A3", "3")],
        [_row("板橋區", "s", "A4", "4"), _row("板橋區", "s", "A5", "5"), _row("板橋區", "s", "A6", "6")],
    ])
    monkeypatch.setattr(sys, "argv", ["sync_land_price_dataset.py", "--max-pages", "1"])
    exit_code = sync_mod.main()
    assert exit_code == 0
    assert cache.get_snapshot_meta(sync_mod.DATASET_ID, "板橋區") is None
    assert len(cache.get_staged_land_price_records(sync_mod.DATASET_ID, "板橋區")) == 3
