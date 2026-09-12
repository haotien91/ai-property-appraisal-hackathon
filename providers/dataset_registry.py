# -*- coding: utf-8 -*-
"""
DatasetRegistry — tracks locally-cached snapshots of government open-data
downloads (e.g. the ~75MB NTPC zoning shapefile ZIP), so that:

1. Offline/hybrid-mode Providers can answer "do I have a usable local copy,
   and is it CURRENT/STALE/UNAVAILABLE" without re-downloading on every
   case query -- large GIS datasets like the zoning shapefile (verified
   78,815,382 bytes as of this session) must NOT be fetched inside a
   Lambda's request/response path; sync is a separate, out-of-band job
   (scripts/sync_ntpc_zoning_dataset.py), and this registry is the
   handoff point between "sync job wrote a new snapshot" and "provider
   reads whatever the registry currently points to".
2. Every NormalizedDataPoint derived from a local snapshot can honestly
   report `dataset_version` instead of silently implying it is live data
   -- this is the concrete mechanism behind the project-wide "官方原始
   資料與系統推論不可混在一起" principle applied to dataset freshness.

Pure Python stdlib (sqlite3) -- no new project dependency for what is,
functionally, one small metadata table.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import DatasetSnapshotInfo, DatasetSnapshotStatus  # noqa: E402

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dataset_registry.sqlite3"
)

# Named policies map to a fixed day count; anything else must match the
# simple ISO8601-ish "P<n>D" duration form (e.g. "P90D") -- an unrecognized
# policy string is a configuration error, not silently treated as "never
# stale" or "always stale".
_NAMED_POLICIES = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}
_ISO_DURATION_RE = re.compile(r"^P(\d+)D$")


class DatasetRegistryError(Exception):
    pass


def _refresh_interval_days(refresh_policy: str) -> int:
    if refresh_policy in _NAMED_POLICIES:
        return _NAMED_POLICIES[refresh_policy]
    m = _ISO_DURATION_RE.match(refresh_policy)
    if m:
        return int(m.group(1))
    raise DatasetRegistryError(
        f"未知的refresh_policy={refresh_policy!r}，必須是{sorted(_NAMED_POLICIES)}其中之一，"
        f"或'P<天數>D'格式（如'P90D'）。本函式不猜測合理預設值。"
    )


def compute_file_checksum(path: str) -> str:
    """SHA-256 of a local file, streamed (safe for the ~75MB zoning ZIP
    without loading the whole file into memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class DatasetRegistry:
    def __init__(self, db_path: Optional[str] = None):
        # DATASET_REGISTRY_DB_PATH lets tests (and any other short-lived
        # environment) redirect every default-constructed DatasetRegistry
        # to a throwaway location, the same way CASES_TABLE_NAME redirects
        # DynamoDB access in tests/test_backend_handlers_e2e.py -- without
        # it, any test that instantiates a Real*Provider in "real" mode
        # (e.g. RealLandUseProvider -> RealNtpcZoningProvider ->
        # DatasetRegistry()) would create a stray sqlite file inside the
        # actual project's data/ directory.
        self.db_path = db_path or os.environ.get("DATASET_REGISTRY_DB_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dataset_snapshots (
                    dataset_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_agency TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    local_snapshot_version TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    license TEXT,
                    refresh_policy TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL,
                    source_last_modified TEXT,
                    record_count INTEGER,
                    notes TEXT
                )
            """)

    def register_snapshot(self, info: DatasetSnapshotInfo) -> None:
        """Upserts one dataset's current snapshot record. Called by a sync
        job (e.g. scripts/sync_ntpc_zoning_dataset.py) after it has
        verified the downloaded file's checksum -- never called with an
        unverified/partial download."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO dataset_snapshots
                    (dataset_id, source_name, source_agency, source_url,
                     local_snapshot_version, local_path, checksum, license,
                     refresh_policy, last_synced_at, source_last_modified,
                     record_count, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id) DO UPDATE SET
                    source_name=excluded.source_name,
                    source_agency=excluded.source_agency,
                    source_url=excluded.source_url,
                    local_snapshot_version=excluded.local_snapshot_version,
                    local_path=excluded.local_path,
                    checksum=excluded.checksum,
                    license=excluded.license,
                    refresh_policy=excluded.refresh_policy,
                    last_synced_at=excluded.last_synced_at,
                    source_last_modified=excluded.source_last_modified,
                    record_count=excluded.record_count,
                    notes=excluded.notes
            """, (
                info.dataset_id, info.source_name, info.source_agency, info.source_url,
                info.local_snapshot_version, info.local_path, info.checksum, info.license,
                info.refresh_policy, info.last_synced_at.isoformat(),
                info.source_last_modified.isoformat() if info.source_last_modified else None,
                info.record_count, info.notes,
            ))

    def get_current_snapshot(self, dataset_id: str) -> Optional[DatasetSnapshotInfo]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_snapshots WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        if row is None:
            return None
        return DatasetSnapshotInfo(
            dataset_id=row["dataset_id"], source_name=row["source_name"],
            source_agency=row["source_agency"], source_url=row["source_url"],
            local_snapshot_version=row["local_snapshot_version"], local_path=row["local_path"],
            checksum=row["checksum"], license=row["license"], refresh_policy=row["refresh_policy"],
            last_synced_at=datetime.fromisoformat(row["last_synced_at"]),
            source_last_modified=datetime.fromisoformat(row["source_last_modified"])
            if row["source_last_modified"] else None,
            record_count=row["record_count"], notes=row["notes"],
        )

    def check_staleness(self, dataset_id: str, now: Optional[datetime] = None) -> DatasetSnapshotStatus:
        """Never synced (or the local file has vanished since) ->
        UNAVAILABLE. Synced but past its refresh_policy interval -> STALE
        (still usable -- see providers/ntpc_zoning_provider.py, which
        downgrades confidence rather than refusing to answer). Otherwise
        CURRENT."""
        snapshot = self.get_current_snapshot(dataset_id)
        if snapshot is None or not os.path.exists(snapshot.local_path):
            return DatasetSnapshotStatus.UNAVAILABLE
        now = now or datetime.now()
        interval = timedelta(days=_refresh_interval_days(snapshot.refresh_policy))
        if now - snapshot.last_synced_at > interval:
            return DatasetSnapshotStatus.STALE
        return DatasetSnapshotStatus.CURRENT

    def verify_checksum(self, dataset_id: str) -> bool:
        """Re-hashes the local file and compares against the registered
        checksum -- catches silent corruption/truncation between sync runs,
        not just at sync time."""
        snapshot = self.get_current_snapshot(dataset_id)
        if snapshot is None or not os.path.exists(snapshot.local_path):
            return False
        return compute_file_checksum(snapshot.local_path) == snapshot.checksum
