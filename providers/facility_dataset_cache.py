# -*- coding: utf-8 -*-
"""
facility_dataset_cache.py — Phase API-2's local indexed snapshot store for
official 新北市 facility datasets (schools/stations/markets/parks).

Deliberately a SEPARATE store from providers/cadastral_dataset_cache.py --
that module (and its Atomic Staging Contract / CANONICAL_V3 checksum
contract) is FROZEN as of Phase API-1.9's OFFICIAL_CADASTRAL_EVIDENCE_
PIPELINE declaration and must not be modified for an unrelated dataset
family (see docs/phase9/api_integration_spec.md §49). This module
independently re-applies the SAME architectural principles Phase API-1.8
established (STAGING -> validation -> atomic promote -> CURRENT; Provider
lookups never see partial/in-progress data) to a new, disjoint set of
tables, rather than either (a) risking the frozen pipeline by extending
its schema, or (b) reinventing a weaker contract for facility data.

## Coordinate Reference System (CRS)

新北市重要地標資訊 (the dataset backing school/station facilities, dataset_id
6DCFF24A-838C-40FB-A9DF-F1160AFAFE84) publishes coordinates as `twd97_x`/
`twd97_y` -- TWD97 TM2 (EPSG:3826), NOT WGS84. This is stated explicitly by
the source field names themselves (not inferred/guessed), and independently
sanity-checked during Phase API-2's audit: reprojecting 三峽區's 國立臺北
大學三峽校區 (twd97_x=287682, twd97_y=2759752) via `pyproj.Transformer.
from_crs("EPSG:3826", "EPSG:4326")` yields (lon=121.373, lat=24.945) --
correct for that campus's real, well-known location, cross-checked against
general geographic knowledge of the area, not merely trusted procedurally.

This module stores ONLY WGS84 lat/lon in `latitude`/`longitude` (matching
domain.models.Coordinate's documented convention) -- the TWD97 source
values are reprojected ONCE at sync time (scripts/sync_facility_dataset.py,
using the SAME `pyproj` library and EPSG:3826/4326 codes already verified
in scripts/sync_ntpc_zoning_dataset.py, never a hand-rolled formula) and
never persisted in TWD97 form here, so no downstream reader can accidentally
treat a TWD97 meter value as a WGS84 degree value.

For 市場/公園 datasets (785BE91A.../5FE3A136...), the source has NO
coordinate field at all (name/address/district/phone only, verified via
direct API inspection during this round's audit) -- rows from these
datasets are stored with `latitude`/`longitude` = NULL, `coordinate_status`
= 'ADDRESS_ONLY'. This is never a guess-then-fill-in-later gap: it is a
structural fact about the source dataset, persisted explicitly so
`OfficialFacilityProvider` can honestly report "no distance computable"
rather than silently omitting the facility or fabricating a coordinate.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "facility_dataset_cache.sqlite3"
)

SCOPE_ALL = "__ALL__"

SCHEMA_VERSION_FACILITY = "facility.v1"

CHECKSUM_ALGORITHM_CANONICAL_V1 = "CANONICAL_V1"
DEFAULT_CHECKSUM_ALGORITHM = CHECKSUM_ALGORITHM_CANONICAL_V1

COORDINATE_STATUS_WGS84 = "WGS84"
COORDINATE_STATUS_ADDRESS_ONLY = "ADDRESS_ONLY"
COORDINATE_STATUS_CRS_UNKNOWN = "COORDINATE_CRS_UNKNOWN"

STAGING_STATUS_IN_PROGRESS = "IN_PROGRESS"
STAGING_STATUS_FAILED = "FAILED"
STAGING_STATUS_PROMOTED = "PROMOTED"

# Fixed field set/order for the canonical checksum -- an array (not dict),
# sorted by the FULL tuple (not a partial prefix), same reasoning as
# cadastral_dataset_cache.py's CANONICAL_V3 (Phase API-1.8): eliminates any
# stable-sort tie-ordering dependency on input order, even for two rows
# that happen to share a facility_id.
_CANONICAL_FIELDS = (
    "facility_type", "district", "name", "address",
    "latitude", "longitude", "coordinate_status", "facility_id",
)


class FacilityDatasetCacheError(Exception):
    pass


def compute_canonical_checksum_v1(records: List[dict]) -> str:
    arrays = [[r.get(f) for f in _CANONICAL_FIELDS] for r in records]
    arrays.sort(key=lambda arr: tuple("" if v is None else v for v in arr))
    canonical = json.dumps(arrays, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FacilityDatasetCache:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("FACILITY_DATASET_CACHE_DB_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshot_meta (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_authority TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    checksum_algorithm TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    PRIMARY KEY (dataset_id, scope_key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facility_records (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    facility_type TEXT NOT NULL,
                    facility_id TEXT,
                    district TEXT,
                    name TEXT,
                    address TEXT,
                    latitude REAL,
                    longitude REAL,
                    coordinate_status TEXT NOT NULL,
                    source_dataset_id TEXT,
                    source_authority TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facility_lookup
                ON facility_records(dataset_id, scope_key, facility_type, district)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facility_records_staging (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    facility_type TEXT NOT NULL,
                    facility_id TEXT,
                    district TEXT,
                    name TEXT,
                    address TEXT,
                    latitude REAL,
                    longitude REAL,
                    coordinate_status TEXT NOT NULL,
                    source_dataset_id TEXT,
                    source_authority TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_facility_staging
                ON facility_records_staging(dataset_id, scope_key)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS staging_run_status (
                    dataset_id TEXT NOT NULL PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    note TEXT
                )
            """)

            # Phase API-2.1 additive schema migrations (idempotent -- same
            # pattern as cadastral_dataset_cache.py's PRAGMA table_info
            # checks). `facility_subtype`/`source_category` preserve the
            # source dataset's own original category (§10) without being
            # part of the CANONICAL_V1 checksum's fixed field set -- that
            # set was defined (Phase API-2) to cover the facts that
            # identify/locate a record (type/district/name/address/
            # coordinates/id); subtype/category are provenance ENRICHMENT
            # of an already-identified record, not new identity, so adding
            # them as columns does not require a new checksum algorithm
            # version (contrast with cadastral_dataset_cache.py's V1->V2->V3
            # evolutions, which each changed what the checksum's fixed
            # field set actually covers).
            for table in ("facility_records", "facility_records_staging"):
                if not self._column_exists(conn, table, "facility_subtype"):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN facility_subtype TEXT")
                if not self._column_exists(conn, table, "source_category"):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN source_category TEXT")
            # source_record_count/mapped_record_count/skipped_record_count
            # (§14): `record_count` alone (renamed in spirit to
            # stored_record_count at the Provider/Evidence layer) could be
            # misread as "the source dataset only has this many rows" when
            # it is actually the count AFTER normalization/type-filtering
            # (e.g. 614 SCHOOL+STATION rows normalized out of 2,056 raw
            # landmark rows) -- these three columns let snapshot_meta state
            # the full breakdown explicitly rather than leaving it only in
            # a sync-script log line.
            for column in ("source_record_count", "mapped_record_count", "skipped_record_count"):
                if not self._column_exists(conn, "snapshot_meta", column):
                    conn.execute(f"ALTER TABLE snapshot_meta ADD COLUMN {column} INTEGER")

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in rows)

    # -- Atomic Staging Contract (same principle as Phase API-1.8) ------

    def begin_staging_run(self, dataset_id: str, note: Optional[str] = None) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM facility_records_staging WHERE dataset_id=?", (dataset_id,))
            conn.execute(
                "INSERT INTO staging_run_status (dataset_id, status, started_at, updated_at, note) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(dataset_id) DO UPDATE SET status=excluded.status, "
                "started_at=excluded.started_at, updated_at=excluded.updated_at, note=excluded.note",
                (dataset_id, STAGING_STATUS_IN_PROGRESS, now, now, note),
            )

    def stage_facility_records(self, dataset_id: str, scope_key: str, records: List[dict]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM facility_records_staging WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            )
            conn.executemany(
                "INSERT INTO facility_records_staging "
                "(dataset_id, scope_key, facility_type, facility_id, district, name, address, "
                " latitude, longitude, coordinate_status, source_dataset_id, source_authority, "
                " facility_subtype, source_category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (dataset_id, scope_key, r.get("facility_type"), r.get("facility_id"), r.get("district"),
                     r.get("name"), r.get("address"), r.get("latitude"), r.get("longitude"),
                     r.get("coordinate_status"), r.get("source_dataset_id"), r.get("source_authority"),
                     r.get("facility_subtype"), r.get("source_category"))
                    for r in records
                ],
            )
            conn.execute(
                "UPDATE staging_run_status SET updated_at=? WHERE dataset_id=?",
                (datetime.now().isoformat(), dataset_id),
            )

    def mark_staging_failed(self, dataset_id: str, note: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=?, note=? WHERE dataset_id=?",
                (STAGING_STATUS_FAILED, datetime.now().isoformat(), note, dataset_id),
            )

    def get_staging_status(self, dataset_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM staging_run_status WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        return dict(row) if row else None

    def promote_facility_staging_to_current(
        self, *, dataset_id: str, dataset_name: str, source_url: str, source_authority: str,
        scope_keys: List[str], checksum_algorithm: str = DEFAULT_CHECKSUM_ALGORITHM,
        source_record_count: Optional[int] = None, mapped_record_count: Optional[int] = None,
        skipped_record_count: Optional[int] = None,
    ) -> Dict:
        """Atomically promotes STAGING -> CURRENT for every scope_key, as
        ONE sqlite3 transaction (identical rollback-on-raise reasoning as
        cadastral_dataset_cache.py's promote_*_staging_to_current --
        Python's `with self._connect() as conn:` rolls back everything
        executed in this call if any scope_key raises, so CURRENT never
        ends up as a partial mix of old/new).

        `source_record_count`/`mapped_record_count`/`skipped_record_count`
        (Phase API-2.1 §14, optional/additive) let the caller (scripts/
        sync_facility_dataset.py) record the full source->stored breakdown
        alongside the per-scope_key stored count (`record_count` in the
        returned dict, unchanged meaning) -- e.g. landmark: source=2056,
        mapped=614, skipped=1442. Left NULL for callers that only have one
        number (e.g. the address-only market/park syncs, where source count
        == stored count and there is nothing to skip)."""
        if checksum_algorithm != CHECKSUM_ALGORITHM_CANONICAL_V1:
            raise FacilityDatasetCacheError(f"未知checksum_algorithm={checksum_algorithm!r}")
        downloaded_at = datetime.now().isoformat()
        promoted = []
        with self._connect() as conn:
            for scope_key in sorted(scope_keys):
                rows = conn.execute(
                    "SELECT facility_type, facility_id, district, name, address, latitude, longitude, "
                    "coordinate_status, source_dataset_id, source_authority, facility_subtype, source_category "
                    "FROM facility_records_staging WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                ).fetchall()
                if not rows:
                    raise FacilityDatasetCacheError(
                        f"promote失敗：scope_key={scope_key} 在staging中無資料，拒絕promote（CURRENT未變更）"
                    )
                records = [dict(r) for r in rows]
                checksum = compute_canonical_checksum_v1(records)

                conn.execute(
                    "DELETE FROM facility_records WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                )
                conn.executemany(
                    "INSERT INTO facility_records "
                    "(dataset_id, scope_key, facility_type, facility_id, district, name, address, "
                    " latitude, longitude, coordinate_status, source_dataset_id, source_authority, "
                    " facility_subtype, source_category) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (dataset_id, scope_key, r.get("facility_type"), r.get("facility_id"), r.get("district"),
                         r.get("name"), r.get("address"), r.get("latitude"), r.get("longitude"),
                         r.get("coordinate_status"), r.get("source_dataset_id"), r.get("source_authority"),
                         r.get("facility_subtype"), r.get("source_category"))
                        for r in records
                    ],
                )
                conn.execute(
                    "INSERT INTO snapshot_meta "
                    "(dataset_id, scope_key, dataset_name, source_url, source_authority, downloaded_at, "
                    " checksum_sha256, checksum_algorithm, record_count, schema_version, "
                    " source_record_count, mapped_record_count, skipped_record_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(dataset_id, scope_key) DO UPDATE SET "
                    "dataset_name=excluded.dataset_name, source_url=excluded.source_url, "
                    "source_authority=excluded.source_authority, downloaded_at=excluded.downloaded_at, "
                    "checksum_sha256=excluded.checksum_sha256, checksum_algorithm=excluded.checksum_algorithm, "
                    "record_count=excluded.record_count, schema_version=excluded.schema_version, "
                    "source_record_count=excluded.source_record_count, "
                    "mapped_record_count=excluded.mapped_record_count, "
                    "skipped_record_count=excluded.skipped_record_count",
                    (dataset_id, scope_key, dataset_name, source_url, source_authority, downloaded_at,
                     checksum, checksum_algorithm, len(records), SCHEMA_VERSION_FACILITY,
                     source_record_count, mapped_record_count, skipped_record_count),
                )
                promoted.append({"scope_key": scope_key, "record_count": len(records), "checksum_sha256": checksum})

        with self._connect() as conn:
            conn.execute("DELETE FROM facility_records_staging WHERE dataset_id=?", (dataset_id,))
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=? WHERE dataset_id=?",
                (STAGING_STATUS_PROMOTED, datetime.now().isoformat(), dataset_id),
            )
        return {"promoted": promoted, "checksum_algorithm": checksum_algorithm,
                "total_record_count": sum(p["record_count"] for p in promoted)}

    # -- reading (Provider classes only) ---------------------------------

    def get_snapshot_meta(self, dataset_id: str, scope_key: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshot_meta WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            ).fetchone()
        return dict(row) if row else None

    def lookup_facilities(self, dataset_id: str, scope_key: str, facility_type: str,
                           district: Optional[str] = None) -> List[Dict]:
        """Returns EVERY CURRENT record matching facility_type (and
        district, if given) -- never just the nearest/first, matching
        Phase API-1.9's "never fetchone/first-match" principle applied
        here too (deciding "nearest" or "most impactful" is the caller's
        job -- see OfficialFacilityProvider -- not this read layer's).
        Raises FacilityDatasetCacheError if the scope was never synced;
        returns [] if synced but genuinely no match (same distinction as
        cadastral_dataset_cache.py's lookup methods)."""
        if self.get_snapshot_meta(dataset_id, scope_key) is None:
            raise FacilityDatasetCacheError(
                f"尚未同步 dataset_id={dataset_id} scope_key={scope_key} 之snapshot"
            )
        with self._connect() as conn:
            if district:
                rows = conn.execute(
                    "SELECT * FROM facility_records WHERE dataset_id=? AND scope_key=? "
                    "AND facility_type=? AND district=?",
                    (dataset_id, scope_key, facility_type, district),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM facility_records WHERE dataset_id=? AND scope_key=? AND facility_type=?",
                    (dataset_id, scope_key, facility_type),
                ).fetchall()
        records = [dict(r) for r in rows]
        records.sort(key=lambda r: tuple(
            "" if r.get(f) is None else r.get(f) for f in ("district", "name", "facility_id")
        ))
        return records
