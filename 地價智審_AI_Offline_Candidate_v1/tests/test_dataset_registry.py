# -*- coding: utf-8 -*-
"""Tests for providers/dataset_registry.py. Fully offline (sqlite file in
tmp_path); no network, no dependency on the real ~75MB NTPC zoning download."""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers.dataset_registry import DatasetRegistry, DatasetRegistryError, compute_file_checksum
from domain.models import DatasetSnapshotInfo, DatasetSnapshotStatus


@pytest.fixture()
def registry(tmp_path):
    return DatasetRegistry(db_path=str(tmp_path / "registry.sqlite3"))


@pytest.fixture()
def local_file(tmp_path):
    p = tmp_path / "ntpc_zoning_snapshot.gpkg"
    p.write_bytes(b"fake geopackage content for testing")
    return str(p)


def _snapshot(local_path, last_synced_at=None, refresh_policy="quarterly", dataset_id="ntpc_zoning"):
    return DatasetSnapshotInfo(
        dataset_id=dataset_id,
        source_name="新北市都市計畫土地使用分區及範圍圖",
        source_agency="新北市政府城鄉發展局",
        source_url="https://data.ntpc.gov.tw/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5",
        local_snapshot_version="2026-02-26",
        local_path=local_path,
        checksum=compute_file_checksum(local_path),
        refresh_policy=refresh_policy,
        last_synced_at=last_synced_at or datetime.now(),
    )


class TestRegisterAndRetrieve:
    def test_register_then_get_returns_same_data(self, registry, local_file):
        info = _snapshot(local_file)
        registry.register_snapshot(info)
        got = registry.get_current_snapshot("ntpc_zoning")
        assert got is not None
        assert got.dataset_id == "ntpc_zoning"
        assert got.local_snapshot_version == "2026-02-26"
        assert got.checksum == info.checksum

    def test_unregistered_dataset_returns_none(self, registry):
        assert registry.get_current_snapshot("does_not_exist") is None

    def test_register_twice_upserts_not_duplicates(self, registry, local_file):
        registry.register_snapshot(_snapshot(local_file, refresh_policy="monthly"))
        registry.register_snapshot(_snapshot(local_file, refresh_policy="quarterly"))
        got = registry.get_current_snapshot("ntpc_zoning")
        assert got.refresh_policy == "quarterly"  # second registration wins


class TestStaleness:
    def test_never_synced_is_unavailable(self, registry):
        assert registry.check_staleness("never_synced") == DatasetSnapshotStatus.UNAVAILABLE

    def test_recent_sync_within_policy_is_current(self, registry, local_file):
        registry.register_snapshot(_snapshot(local_file, last_synced_at=datetime.now(), refresh_policy="quarterly"))
        assert registry.check_staleness("ntpc_zoning") == DatasetSnapshotStatus.CURRENT

    def test_sync_older_than_policy_is_stale(self, registry, local_file):
        old = datetime.now() - timedelta(days=100)
        registry.register_snapshot(_snapshot(local_file, last_synced_at=old, refresh_policy="quarterly"))
        assert registry.check_staleness("ntpc_zoning") == DatasetSnapshotStatus.STALE

    def test_boundary_just_under_policy_is_current(self, registry, local_file):
        just_under = datetime.now() - timedelta(days=89)
        registry.register_snapshot(_snapshot(local_file, last_synced_at=just_under, refresh_policy="quarterly"))
        assert registry.check_staleness("ntpc_zoning") == DatasetSnapshotStatus.CURRENT

    def test_named_policies(self, registry, local_file):
        for policy, days_over in [("daily", 2), ("weekly", 8), ("monthly", 31)]:
            old = datetime.now() - timedelta(days=days_over)
            registry.register_snapshot(_snapshot(local_file, last_synced_at=old, refresh_policy=policy,
                                                    dataset_id=f"ds_{policy}"))
            assert registry.check_staleness(f"ds_{policy}") == DatasetSnapshotStatus.STALE

    def test_iso_duration_policy(self, registry, local_file):
        old = datetime.now() - timedelta(days=91)
        registry.register_snapshot(_snapshot(local_file, last_synced_at=old, refresh_policy="P90D"))
        assert registry.check_staleness("ntpc_zoning") == DatasetSnapshotStatus.STALE

    def test_unknown_policy_raises_not_silently_defaults(self, registry, local_file):
        registry.register_snapshot(_snapshot(local_file, refresh_policy="whenever_i_feel_like_it"))
        with pytest.raises(DatasetRegistryError):
            registry.check_staleness("ntpc_zoning")

    def test_local_file_deleted_after_registration_is_unavailable(self, registry, local_file):
        registry.register_snapshot(_snapshot(local_file))
        os.remove(local_file)
        assert registry.check_staleness("ntpc_zoning") == DatasetSnapshotStatus.UNAVAILABLE


class TestChecksumVerification:
    def test_matching_checksum_verifies_true(self, registry, local_file):
        registry.register_snapshot(_snapshot(local_file))
        assert registry.verify_checksum("ntpc_zoning") is True

    def test_corrupted_file_verifies_false(self, registry, local_file):
        registry.register_snapshot(_snapshot(local_file))
        with open(local_file, "ab") as f:
            f.write(b"corruption!!!")
        assert registry.verify_checksum("ntpc_zoning") is False

    def test_missing_dataset_verifies_false(self, registry):
        assert registry.verify_checksum("never_registered") is False


class TestComputeFileChecksum:
    def test_deterministic(self, local_file):
        assert compute_file_checksum(local_file) == compute_file_checksum(local_file)

    def test_different_content_different_checksum(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"content A")
        b.write_bytes(b"content B")
        assert compute_file_checksum(str(a)) != compute_file_checksum(str(b))
