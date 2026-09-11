# -*- coding: utf-8 -*-
"""
cadastral_dataset_cache.py — Phase API-1.5's local indexed snapshot store
for 新北市已公告徵收案件地籍資料 / 新北市公告土地現值.

Why a NEW, separate sqlite-backed cache instead of extending
providers/dataset_registry.py: DatasetRegistry's schema is `dataset_id
TEXT PRIMARY KEY` -- one CURRENT row per whole dataset, no per-jurisdiction
scoping. This round's sync strategy is deliberately per-district (see
module docstring of scripts/sync_land_price_dataset.py for why: the land-
price dataset has ~1.15M rows city-wide, far too large to sync in one pass
this round), which needs a (dataset_id, scope_key) composite key
DatasetRegistry does not support. Rather than retrofit an already-shipped
Provider's dependency (providers/ntpc_zoning_provider.py's
RealNtpcZoningProvider) to a new schema shape, this module is a small,
purpose-built, analogous store using the exact same Snapshot Contract
vocabulary (dataset_id/dataset_name/dataset_year/source_url/source_
authority/downloaded_at/source_last_modified/checksum_sha256/record_count/
schema_version/local_path) -- same principle (sqlite3 stdlib, one CURRENT
row per key, checksum-verified), different key shape.

`scope_key` is `"__ALL__"` for a full-dataset sync (used for the small
~9,437-row expropriation dataset) or a district name (used for the
land-price dataset's per-district syncs).

This module only ever WRITES via `write_*_snapshot()` / `stage_*` /
`promote_*_staging_to_current()` (called by the scripts/sync_*.py scripts)
and READS via `lookup_*()`/`get_snapshot_meta()` (called by the Provider
classes at request time) -- Provider classes never trigger a sync
themselves, mirroring RealNtpcZoningProvider's existing "read whatever the
registry currently points to, never fetch over the network inside a
request" contract.

---

## Versioned Checksum Contract (Phase API-1.7 / API-1.8)

Phase API-1.5's original `compute_records_checksum()` (kept below,
UNCHANGED, as `LEGACY_V1`) was discovered to have two reproducibility
fragilities (see docs/backlog.md::CHECKSUM_REPRODUCIBILITY_FRAGILITY):
it hashes a `json.dumps` of a **list** of dicts, so (a) it is sensitive to
the LIST's own order, and (b) the original record dicts included a
`country` field that was never persisted into the SQL tables.

`CANONICAL_V2` (Phase API-1.7) fixed both: a fixed field SET/ORDER
(array, not dict), sorted by a `(district, segment, lid-or-id)` prefix,
UTF-8, deterministic JSON. `country` is now persisted so the checksum is
reproducible from storage alone.

`CANONICAL_V3` (Phase API-1.8) fixes ONE remaining latent gap in V2:
Python's `list.sort()` is stable, so two records that happen to share the
exact same `(district, segment, lid-or-id)` prefix but differ in a
trailing field (e.g. `official_value_busiprval`) would keep whatever
RELATIVE order they arrived in from the input list -- meaning V2's
checksum is NOT fully input-order-independent for that (currently
hypothetical -- the live dataset has zero such collisions, verified) edge
case. This is exactly the class of bug this round's checksum contract
promises to rule out by construction, so it must be closed rather than
left as a documented caveat. V3 sorts by the FULL canonical field tuple
(every field, not just the leading prefix) -- for genuinely identical full
rows, order is irrelevant by definition (the serialized bytes are
identical either way); for any two DIFFERING rows, the full-tuple sort key
differs, so the result is fully deterministic regardless of input order.

Per this round's explicit instruction, this is **not** a silent
redefinition of `CANONICAL_V2` -- V2's function and stored meaning are
completely unchanged; `CANONICAL_V3` is a new, independently-versioned
algorithm, and both `write_*_snapshot()`'s and `promote_*_staging_to_
current()`'s DEFAULT `checksum_algorithm` argument moves to `CANONICAL_V3`
for all NEW writes from this round forward (a call-site default, not a
redefinition of what V1/V2 compute -- both remain available as explicit,
fully-supported values).

---

## Atomic Staging Contract (Phase API-1.8)

Phase API-1.7's sync script called `write_land_price_snapshot()` directly
at every checkpoint -- i.e. mid-crawl, PARTIAL data (whatever had been
fetched so far) was written straight into the authoritative
`land_price_records`/`snapshot_meta` tables that `lookup_land_price()`/
`get_snapshot_meta()` read from. A crash or network interruption between
checkpoints therefore left a PARTIAL result looking exactly like a
COMPLETE one to any Provider querying it -- there was no way to tell "this
snapshot table has 47,000 rows because that's really all of 板橋區" from
"this snapshot table has 47,000 rows because the crawl died at page 94 of
what should have been 400". That is a correctness risk this round exists
to close.

Fix: a two-space contract river-crossed by exactly one gate:

    Official API -> download -> STAGING -> validation -> [ALL PASS] ->
    atomic promote -> CURRENT

- **STAGING** (`*_records_staging` tables + `staging_run_status`): written
  by `stage_land_price_records()`/`stage_expropriation_records()` at every
  checkpoint. Freely overwritten mid-crawl -- this is expected and safe,
  because nothing reads staging except diagnostics tooling.
- **CURRENT** (`land_price_records`/`expropriation_records`/
  `snapshot_meta` -- unchanged tables from Phase API-1.5/1.7): the ONLY
  tables `lookup_*()`/`get_snapshot_meta()` ever read. The ONLY way data
  reaches CURRENT is `promote_land_price_staging_to_current()`/
  `promote_expropriation_staging_to_current()`, and each promote call is a
  SINGLE sqlite3 transaction covering every scope_key passed to it -- if
  ANY scope_key fails validation partway through (missing staged data,
  a duplicate key), the whole transaction is rolled back by Python's
  `sqlite3.Connection.__exit__` (raise -> rollback, not commit), so
  CURRENT is left holding the entire previous (last-known-good) snapshot
  for EVERY scope_key, not a mix of old-for-some/new-for-others.

There is deliberately **no** `snapshot_status`/`is_complete` column added
to `snapshot_meta` itself: since a row can only ever land there via a
successful, fully-validated `promote_*`, a row's mere EXISTENCE in
`snapshot_meta` already means COMPLETE/VALIDATED -- adding a redundant
flag there would just be a second place the same invariant could
(accidentally) be violated. `staging_run_status` (IN_PROGRESS / FAILED /
PROMOTED) exists purely as diagnostic breadcrumbs for the staging side,
and is never consulted by any lookup path.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cadastral_dataset_cache.sqlite3"
)

SCOPE_ALL = "__ALL__"

SCHEMA_VERSION_EXPROPRIATION = "expropriation.v1"
SCHEMA_VERSION_LAND_PRICE = "land_price.v1"

CHECKSUM_ALGORITHM_LEGACY_V1 = "LEGACY_V1"
CHECKSUM_ALGORITHM_CANONICAL_V2 = "CANONICAL_V2"
CHECKSUM_ALGORITHM_CANONICAL_V3 = "CANONICAL_V3"
DEFAULT_CHECKSUM_ALGORITHM = CHECKSUM_ALGORITHM_CANONICAL_V3

# CANONICAL_V2/V3 fixed field sets/order (see module docstring). V2 sorts
# by a (district, segment, land-no) PREFIX only; V3 sorts by the FULL
# tuple below -- see module docstring for why V3 exists.
_CANONICAL_FIELDS_LAND_PRICE = ("country", "district", "segment", "lid", "official_value_busiprval")
_CANONICAL_FIELDS_EXPROPRIATION = ("district", "segment", "id", "sus_year", "pro_name")

CHECKSUM_SCHEMA_VERSION_LAND_PRICE_V2 = "land_price.checksum.v2"
CHECKSUM_SCHEMA_VERSION_EXPROPRIATION_V2 = "expropriation.checksum.v2"
CHECKSUM_SCHEMA_VERSION_LAND_PRICE_V3 = "land_price.checksum.v3"
CHECKSUM_SCHEMA_VERSION_EXPROPRIATION_V3 = "expropriation.checksum.v3"

STAGING_STATUS_IN_PROGRESS = "IN_PROGRESS"
STAGING_STATUS_FAILED = "FAILED"
STAGING_STATUS_PROMOTED = "PROMOTED"


class CadastralDatasetCacheError(Exception):
    pass


def compute_records_checksum(records: List[dict]) -> str:
    """LEGACY_V1 -- UNCHANGED from Phase API-1.5 (see module docstring for
    its known order/field-persistence fragilities). Kept verbatim so any
    already-written LEGACY_V1 snapshot's stored checksum_sha256 remains
    interpretable exactly as it always was."""
    canonical = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize_v2(records: List[dict], fields: tuple, sort_key_fields: tuple) -> str:
    """CANONICAL_V2 -- UNCHANGED from Phase API-1.7. Sorts by a PREFIX of
    `fields` only (see module docstring for the tie-ordering gap this
    leaves, closed by CANONICAL_V3 below, NOT by silently changing this
    function)."""
    arrays = [[r.get(f) for f in fields] for r in records]
    field_index = {f: i for i, f in enumerate(fields)}
    arrays.sort(key=lambda arr: tuple(arr[field_index[k]] or "" for k in sort_key_fields))
    canonical = json.dumps(arrays, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_canonical_checksum_v2_land_price(records: List[dict]) -> str:
    return _canonicalize_v2(records, _CANONICAL_FIELDS_LAND_PRICE, ("district", "segment", "lid"))


def compute_canonical_checksum_v2_expropriation(records: List[dict]) -> str:
    return _canonicalize_v2(records, _CANONICAL_FIELDS_EXPROPRIATION, ("district", "segment", "id"))


def _canonicalize_v3(records: List[dict], fields: tuple) -> str:
    """CANONICAL_V3 (Phase API-1.8) -- sorts by the FULL canonical field
    tuple (every field, not just a district/segment/land-no prefix), so
    the result is fully independent of input list order even for two
    records sharing the same leading key prefix (see module docstring).
    `None` is normalized to `""` for sort-key comparability only (never
    written into the serialized/hashed output itself, which still uses
    the raw `r.get(f)` value including `None` where present)."""
    arrays = [[r.get(f) for f in fields] for r in records]
    arrays.sort(key=lambda arr: tuple("" if v is None else v for v in arr))
    canonical = json.dumps(arrays, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_canonical_checksum_v3_land_price(records: List[dict]) -> str:
    return _canonicalize_v3(records, _CANONICAL_FIELDS_LAND_PRICE)


def compute_canonical_checksum_v3_expropriation(records: List[dict]) -> str:
    return _canonicalize_v3(records, _CANONICAL_FIELDS_EXPROPRIATION)


def _compute_checksum_land_price(records: List[dict], algorithm: str) -> "tuple[str, Optional[str]]":
    if algorithm == CHECKSUM_ALGORITHM_CANONICAL_V3:
        return compute_canonical_checksum_v3_land_price(records), CHECKSUM_SCHEMA_VERSION_LAND_PRICE_V3
    if algorithm == CHECKSUM_ALGORITHM_CANONICAL_V2:
        return compute_canonical_checksum_v2_land_price(records), CHECKSUM_SCHEMA_VERSION_LAND_PRICE_V2
    if algorithm == CHECKSUM_ALGORITHM_LEGACY_V1:
        return compute_records_checksum(records), None
    raise CadastralDatasetCacheError(f"未知checksum_algorithm={algorithm!r}")


def _compute_checksum_expropriation(records: List[dict], algorithm: str) -> "tuple[str, Optional[str]]":
    if algorithm == CHECKSUM_ALGORITHM_CANONICAL_V3:
        return compute_canonical_checksum_v3_expropriation(records), CHECKSUM_SCHEMA_VERSION_EXPROPRIATION_V3
    if algorithm == CHECKSUM_ALGORITHM_CANONICAL_V2:
        return compute_canonical_checksum_v2_expropriation(records), CHECKSUM_SCHEMA_VERSION_EXPROPRIATION_V2
    if algorithm == CHECKSUM_ALGORITHM_LEGACY_V1:
        return compute_records_checksum(records), None
    raise CadastralDatasetCacheError(f"未知checksum_algorithm={algorithm!r}")


def _count_duplicate_keys(records: List[dict], key_fields: tuple) -> int:
    seen = set()
    dup_count = 0
    for r in records:
        key = tuple(r.get(f) for f in key_fields)
        if key in seen:
            dup_count += 1
        else:
            seen.add(key)
    return dup_count


def _dedupe_check(records: List[dict], key_fields: tuple, label: str, enforce: bool = True) -> int:
    """Returns the duplicate-key count. Raises iff `enforce` is True AND
    at least one duplicate exists.

    `enforce=True` is appropriate for a dataset where the key is EMPIRICALLY
    verified unique (e.g. land price's (district,segment,lid) -- 0
    duplicates across all 1,153,450 real rows, see Phase API-1.7/1.8
    verification) -- there, any duplicate is a corruption signal (e.g. a
    large-page pagination overlap bug) and must block promotion.

    `enforce=False` is required for the expropriation dataset: Phase
    API-1.8 discovered LIVE that (district,segment,land_no) is NOT a
    unique key there -- 441 real (district,segment,id) collisions exist
    in the live ~9,437-row dataset, each representing the SAME cadastral
    parcel expropriated under MULTIPLE DISTINCT projects in different
    years (e.g. 板橋區/江子翠段第一崁小段/10067 appears under three
    separate 2005-2006 road-widening projects). Treating this as a
    corruption signal would make the expropriation dataset permanently
    un-syncable via the atomic staging path -- these are real, legitimate,
    distinct government records, not a pagination bug. See
    docs/backlog.md::EXPROPRIATION_KEY_NOT_UNIQUE_MULTI_PROJECT_PARCELS for
    the corresponding pre-existing Provider-level gap this discovery
    surfaces (RealExpropriationCaseProvider.query_case()'s lookup returns
    only ONE of potentially several real matches -- out of THIS round's
    scope to fix)."""
    dup_count = _count_duplicate_keys(records, key_fields)
    if dup_count > 0 and enforce:
        raise CadastralDatasetCacheError(
            f"promote失敗：{label}中發現{dup_count}筆重複key，拒絕promote（CURRENT未變更）"
        )
    return dup_count


class CadastralDatasetCache:
    def __init__(self, db_path: Optional[str] = None):
        # Mirrors providers/dataset_registry.py's DATASET_REGISTRY_DB_PATH
        # env var convention, so tests never write into the real project
        # data/ directory.
        self.db_path = db_path or os.environ.get("CADASTRAL_DATASET_CACHE_DB_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in rows)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshot_meta (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    dataset_year TEXT,
                    source_url TEXT NOT NULL,
                    source_authority TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    source_last_modified TEXT,
                    checksum_sha256 TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    local_path TEXT,
                    PRIMARY KEY (dataset_id, scope_key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expropriation_records (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    district TEXT, segment TEXT, land_no_raw TEXT,
                    sus_year TEXT, pro_name TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expropriation_lookup
                ON expropriation_records(dataset_id, district, segment, land_no_raw)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS land_price_records (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    dataset_year TEXT,
                    district TEXT, segment TEXT, lid TEXT,
                    official_value_busiprval TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_land_price_lookup
                ON land_price_records(dataset_id, district, segment, lid)
            """)

            # Additive, backward-compatible schema migrations (Phase
            # API-1.7). Existing LEGACY_V1 rows are left exactly as they
            # are -- checksum_algorithm defaults to LEGACY_V1 for any row
            # written before this column existed, never silently
            # reinterpreted as CANONICAL_V2/V3.
            if not self._column_exists(conn, "snapshot_meta", "checksum_algorithm"):
                conn.execute(
                    f"ALTER TABLE snapshot_meta ADD COLUMN checksum_algorithm TEXT "
                    f"DEFAULT '{CHECKSUM_ALGORITHM_LEGACY_V1}'"
                )
            if not self._column_exists(conn, "snapshot_meta", "checksum_schema_version"):
                conn.execute("ALTER TABLE snapshot_meta ADD COLUMN checksum_schema_version TEXT")
            if not self._column_exists(conn, "land_price_records", "country"):
                conn.execute("ALTER TABLE land_price_records ADD COLUMN country TEXT")

            # Phase API-1.8: Atomic Staging Contract tables. Same column
            # shapes as their CURRENT counterparts (no schema_version/
            # checksum bookkeeping needed on the staging side -- staging
            # is a scratch area, never a queryable source of truth).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expropriation_records_staging (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    district TEXT, segment TEXT, land_no_raw TEXT,
                    sus_year TEXT, pro_name TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expropriation_staging
                ON expropriation_records_staging(dataset_id, scope_key)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS land_price_records_staging (
                    dataset_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    dataset_year TEXT,
                    district TEXT, segment TEXT, lid TEXT,
                    official_value_busiprval TEXT,
                    country TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_land_price_staging
                ON land_price_records_staging(dataset_id, scope_key)
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

    # -- writing: direct-to-CURRENT (small/simple callers, tests) --------
    #
    # Still available as a single-scope, self-contained atomic write (each
    # call is its own transaction) -- but scripts/sync_*.py no longer call
    # these directly for multi-checkpoint crawls; see stage_*/promote_*
    # below for the Atomic Staging Contract those scripts now use.

    def write_expropriation_snapshot(
        self, *, dataset_id: str, dataset_name: str, source_url: str, source_authority: str,
        scope_key: str, records: List[dict], source_last_modified: Optional[str] = None,
        local_path: Optional[str] = None,
        checksum_algorithm: str = DEFAULT_CHECKSUM_ALGORITHM,
    ) -> Dict:
        checksum, checksum_schema_version = _compute_checksum_expropriation(records, checksum_algorithm)
        downloaded_at = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM expropriation_records WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            )
            conn.executemany(
                "INSERT INTO expropriation_records "
                "(dataset_id, scope_key, district, segment, land_no_raw, sus_year, pro_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (dataset_id, scope_key, r.get("district"), r.get("segment"), r.get("id"),
                     r.get("sus_year"), r.get("pro_name"))
                    for r in records
                ],
            )
            self._upsert_snapshot_meta(
                conn, dataset_id=dataset_id, scope_key=scope_key, dataset_name=dataset_name,
                dataset_year=None, source_url=source_url, source_authority=source_authority,
                downloaded_at=downloaded_at, source_last_modified=source_last_modified,
                checksum=checksum, record_count=len(records), schema_version=SCHEMA_VERSION_EXPROPRIATION,
                local_path=local_path, checksum_algorithm=checksum_algorithm,
                checksum_schema_version=checksum_schema_version,
            )
        return {"checksum_sha256": checksum, "record_count": len(records), "downloaded_at": downloaded_at,
                "checksum_algorithm": checksum_algorithm}

    def write_land_price_snapshot(
        self, *, dataset_id: str, dataset_name: str, dataset_year: str, source_url: str,
        source_authority: str, scope_key: str, records: List[dict],
        source_last_modified: Optional[str] = None, local_path: Optional[str] = None,
        checksum_algorithm: str = DEFAULT_CHECKSUM_ALGORITHM,
    ) -> Dict:
        checksum, checksum_schema_version = _compute_checksum_land_price(records, checksum_algorithm)
        downloaded_at = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM land_price_records WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            )
            conn.executemany(
                "INSERT INTO land_price_records "
                "(dataset_id, scope_key, dataset_year, district, segment, lid, official_value_busiprval, country) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (dataset_id, scope_key, dataset_year, r.get("district"), r.get("segment"),
                     r.get("lid"), r.get("official_value_busiprval"), r.get("country"))
                    for r in records
                ],
            )
            self._upsert_snapshot_meta(
                conn, dataset_id=dataset_id, scope_key=scope_key, dataset_name=dataset_name,
                dataset_year=dataset_year, source_url=source_url, source_authority=source_authority,
                downloaded_at=downloaded_at, source_last_modified=source_last_modified,
                checksum=checksum, record_count=len(records), schema_version=SCHEMA_VERSION_LAND_PRICE,
                local_path=local_path, checksum_algorithm=checksum_algorithm,
                checksum_schema_version=checksum_schema_version,
            )
        return {"checksum_sha256": checksum, "record_count": len(records), "downloaded_at": downloaded_at,
                "checksum_algorithm": checksum_algorithm}

    def _upsert_snapshot_meta(self, conn, *, dataset_id, scope_key, dataset_name, dataset_year,
                               source_url, source_authority, downloaded_at, source_last_modified,
                               checksum, record_count, schema_version, local_path,
                               checksum_algorithm, checksum_schema_version) -> None:
        conn.execute(
            "INSERT INTO snapshot_meta "
            "(dataset_id, scope_key, dataset_name, dataset_year, source_url, source_authority, "
            " downloaded_at, source_last_modified, checksum_sha256, record_count, schema_version, "
            " local_path, checksum_algorithm, checksum_schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dataset_id, scope_key) DO UPDATE SET "
            "dataset_name=excluded.dataset_name, dataset_year=excluded.dataset_year, "
            "source_url=excluded.source_url, source_authority=excluded.source_authority, "
            "downloaded_at=excluded.downloaded_at, source_last_modified=excluded.source_last_modified, "
            "checksum_sha256=excluded.checksum_sha256, record_count=excluded.record_count, "
            "schema_version=excluded.schema_version, local_path=excluded.local_path, "
            "checksum_algorithm=excluded.checksum_algorithm, "
            "checksum_schema_version=excluded.checksum_schema_version",
            (dataset_id, scope_key, dataset_name, dataset_year, source_url, source_authority,
             downloaded_at, source_last_modified, checksum, record_count, schema_version, local_path,
             checksum_algorithm, checksum_schema_version),
        )

    # -- Atomic Staging Contract (Phase API-1.8) -------------------------
    #
    # scripts/sync_*.py use this trio for any multi-checkpoint crawl:
    #   1. begin_staging_run()        -- once, at sync start
    #   2. stage_*_records()          -- once per checkpoint (freely
    #                                     re-callable; replaces prior
    #                                     staged content for that scope_key)
    #   3a. promote_*_staging_to_current()  -- once, ONLY after full
    #                                          validation passes
    #   3b. mark_staging_failed()     -- instead of 3a, on any failure.
    #                                     CURRENT is left completely
    #                                     untouched either way.

    def begin_staging_run(self, dataset_id: str, note: Optional[str] = None) -> None:
        """Clears any staging leftovers from a PREVIOUS run for this
        dataset_id (a previous run's staged-but-never-promoted data must
        not silently blend into a new run) and marks a fresh run
        IN_PROGRESS. Does not touch CURRENT tables at all."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM land_price_records_staging WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM expropriation_records_staging WHERE dataset_id=?", (dataset_id,))
            conn.execute(
                "INSERT INTO staging_run_status (dataset_id, status, started_at, updated_at, note) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(dataset_id) DO UPDATE SET status=excluded.status, "
                "started_at=excluded.started_at, updated_at=excluded.updated_at, note=excluded.note",
                (dataset_id, STAGING_STATUS_IN_PROGRESS, now, now, note),
            )

    def stage_land_price_records(self, dataset_id: str, scope_key: str, records: List[dict],
                                  dataset_year: Optional[str] = None) -> None:
        """Replaces STAGING (never CURRENT) content for (dataset_id,
        scope_key). Safe to call repeatedly (e.g. once per checkpoint) --
        each call fully replaces the previous staged content for this
        scope_key, it never appends/accumulates duplicates across calls."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM land_price_records_staging WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            )
            conn.executemany(
                "INSERT INTO land_price_records_staging "
                "(dataset_id, scope_key, dataset_year, district, segment, lid, official_value_busiprval, country) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (dataset_id, scope_key, dataset_year, r.get("district"), r.get("segment"),
                     r.get("lid"), r.get("official_value_busiprval"), r.get("country"))
                    for r in records
                ],
            )
            conn.execute(
                "UPDATE staging_run_status SET updated_at=? WHERE dataset_id=?",
                (datetime.now().isoformat(), dataset_id),
            )

    def stage_expropriation_records(self, dataset_id: str, scope_key: str, records: List[dict]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM expropriation_records_staging WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            )
            conn.executemany(
                "INSERT INTO expropriation_records_staging "
                "(dataset_id, scope_key, district, segment, land_no_raw, sus_year, pro_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (dataset_id, scope_key, r.get("district"), r.get("segment"), r.get("id"),
                     r.get("sus_year"), r.get("pro_name"))
                    for r in records
                ],
            )
            conn.execute(
                "UPDATE staging_run_status SET updated_at=? WHERE dataset_id=?",
                (datetime.now().isoformat(), dataset_id),
            )

    def mark_staging_failed(self, dataset_id: str, note: str) -> None:
        """Called instead of promote_*, on ANY failure (network
        interruption, validation failure, unexpected response). Touches
        ONLY staging_run_status -- CURRENT tables and staging record
        tables themselves are left exactly as they are, so a failed run's
        partial staged data remains available for diagnostics rather than
        being discarded."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=?, note=? WHERE dataset_id=?",
                (STAGING_STATUS_FAILED, datetime.now().isoformat(), note, dataset_id),
            )

    def get_staging_status(self, dataset_id: str) -> Optional[Dict]:
        """DIAGNOSTIC ONLY -- never called by any Provider/lookup path."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM staging_run_status WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_staged_land_price_records(self, dataset_id: str, scope_key: str) -> List[Dict]:
        """DIAGNOSTIC ONLY -- never called by any Provider/lookup path."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM land_price_records_staging WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            ).fetchall()
        return [dict(r) for r in rows]

    def promote_land_price_staging_to_current(
        self, *, dataset_id: str, dataset_name: str, dataset_year: str, source_url: str,
        source_authority: str, scope_keys: List[str],
        checksum_algorithm: str = DEFAULT_CHECKSUM_ALGORITHM,
        enforce_duplicate_check: bool = True,
    ) -> Dict:
        """Atomically promotes STAGING -> CURRENT for every scope_key in
        `scope_keys`, as ONE sqlite3 transaction: if any scope_key has no
        staged data, or its staged rows contain a duplicate
        (district,segment,lid), the whole call raises BEFORE the
        transaction commits -- Python's `with self._connect() as conn:`
        rolls back everything executed so far in this call, so CURRENT
        retains the ENTIRE previous snapshot for every scope_key
        untouched (not a partial mix of old/new). Reads staged content
        from the staging tables (not from caller-supplied records), so
        this call is independently re-runnable against whatever was last
        successfully checkpointed even if the caller's own in-memory
        state was lost."""
        downloaded_at = datetime.now().isoformat()
        promoted = []
        with self._connect() as conn:
            for scope_key in sorted(scope_keys):
                rows = conn.execute(
                    "SELECT district, segment, lid, official_value_busiprval, country "
                    "FROM land_price_records_staging WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                ).fetchall()
                if not rows:
                    raise CadastralDatasetCacheError(
                        f"promote失敗：scope_key={scope_key} 在staging中無資料，拒絕promote（CURRENT未變更）"
                    )
                records = [dict(r) for r in rows]
                dup_count = _dedupe_check(records, ("district", "segment", "lid"), f"scope_key={scope_key}之staging",
                                           enforce=enforce_duplicate_check)

                checksum, checksum_schema_version = _compute_checksum_land_price(records, checksum_algorithm)

                conn.execute(
                    "DELETE FROM land_price_records WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                )
                conn.executemany(
                    "INSERT INTO land_price_records "
                    "(dataset_id, scope_key, dataset_year, district, segment, lid, official_value_busiprval, country) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (dataset_id, scope_key, dataset_year, r.get("district"), r.get("segment"),
                         r.get("lid"), r.get("official_value_busiprval"), r.get("country"))
                        for r in records
                    ],
                )
                self._upsert_snapshot_meta(
                    conn, dataset_id=dataset_id, scope_key=scope_key, dataset_name=dataset_name,
                    dataset_year=dataset_year, source_url=source_url, source_authority=source_authority,
                    downloaded_at=downloaded_at, source_last_modified=None, checksum=checksum,
                    record_count=len(records), schema_version=SCHEMA_VERSION_LAND_PRICE, local_path=None,
                    checksum_algorithm=checksum_algorithm, checksum_schema_version=checksum_schema_version,
                )
                promoted.append({"scope_key": scope_key, "record_count": len(records), "checksum_sha256": checksum,
                                  "duplicate_key_count": dup_count})
            # Only reached if EVERY scope_key above validated cleanly --
            # the `with` block now exits normally and commits everything
            # together. Any exception above skips this and rolls back.

        # Best-effort staging cleanup AFTER a successful commit (CURRENT
        # is already durably promoted at this point regardless of what
        # happens below) -- a failure here would only affect staging
        # housekeeping, never correctness of CURRENT.
        with self._connect() as conn:
            conn.execute("DELETE FROM land_price_records_staging WHERE dataset_id=?", (dataset_id,))
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=? WHERE dataset_id=?",
                (STAGING_STATUS_PROMOTED, datetime.now().isoformat(), dataset_id),
            )
        return {"promoted": promoted, "checksum_algorithm": checksum_algorithm,
                "total_record_count": sum(p["record_count"] for p in promoted)}

    def promote_expropriation_staging_to_current(
        self, *, dataset_id: str, dataset_name: str, source_url: str, source_authority: str,
        scope_keys: List[str], checksum_algorithm: str = DEFAULT_CHECKSUM_ALGORITHM,
        enforce_duplicate_check: bool = True,
    ) -> Dict:
        """`enforce_duplicate_check` defaults to True for safety, but
        scripts/sync_expropriation_dataset.py explicitly passes False --
        (district,segment,land_no) is NOT a unique key in the live
        expropriation dataset (441 real collisions verified live, each a
        genuine distinct case: the same parcel expropriated under multiple
        projects in different years). See `_dedupe_check`'s docstring and
        docs/backlog.md::EXPROPRIATION_KEY_NOT_UNIQUE_MULTI_PROJECT_PARCELS.
        The duplicate count is still computed and returned either way (see
        `promoted[i]["duplicate_key_count"]`) for visibility even when not
        enforced."""
        downloaded_at = datetime.now().isoformat()
        promoted = []
        with self._connect() as conn:
            for scope_key in sorted(scope_keys):
                rows = conn.execute(
                    "SELECT district, segment, land_no_raw AS id, sus_year, pro_name "
                    "FROM expropriation_records_staging WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                ).fetchall()
                if not rows:
                    raise CadastralDatasetCacheError(
                        f"promote失敗：scope_key={scope_key} 在staging中無資料，拒絕promote（CURRENT未變更）"
                    )
                records = [dict(r) for r in rows]
                dup_count = _dedupe_check(records, ("district", "segment", "id"), f"scope_key={scope_key}之staging",
                                           enforce=enforce_duplicate_check)

                checksum, checksum_schema_version = _compute_checksum_expropriation(records, checksum_algorithm)

                conn.execute(
                    "DELETE FROM expropriation_records WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                )
                conn.executemany(
                    "INSERT INTO expropriation_records "
                    "(dataset_id, scope_key, district, segment, land_no_raw, sus_year, pro_name) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (dataset_id, scope_key, r.get("district"), r.get("segment"), r.get("id"),
                         r.get("sus_year"), r.get("pro_name"))
                        for r in records
                    ],
                )
                self._upsert_snapshot_meta(
                    conn, dataset_id=dataset_id, scope_key=scope_key, dataset_name=dataset_name,
                    dataset_year=None, source_url=source_url, source_authority=source_authority,
                    downloaded_at=downloaded_at, source_last_modified=None, checksum=checksum,
                    record_count=len(records), schema_version=SCHEMA_VERSION_EXPROPRIATION, local_path=None,
                    checksum_algorithm=checksum_algorithm, checksum_schema_version=checksum_schema_version,
                )
                promoted.append({"scope_key": scope_key, "record_count": len(records), "checksum_sha256": checksum,
                                  "duplicate_key_count": dup_count})

        with self._connect() as conn:
            conn.execute("DELETE FROM expropriation_records_staging WHERE dataset_id=?", (dataset_id,))
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=? WHERE dataset_id=?",
                (STAGING_STATUS_PROMOTED, datetime.now().isoformat(), dataset_id),
            )
        return {"promoted": promoted, "checksum_algorithm": checksum_algorithm,
                "total_record_count": sum(p["record_count"] for p in promoted)}

    # -- reading (Provider classes only) ---------------------------------
    #
    # These NEVER touch *_staging or staging_run_status -- structurally
    # incapable of returning partial/in-progress data, regardless of what
    # is currently happening on the staging side.

    def get_snapshot_meta(self, dataset_id: str, scope_key: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshot_meta WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            ).fetchone()
        return dict(row) if row else None

    def lookup_expropriation(self, dataset_id: str, scope_key: str, district: str,
                              segment: str, land_no: str) -> Optional[Dict]:
        """LEGACY single-result convenience wrapper -- kept for existing
        callers/tests of this exact contract. Returns exactly ONE matching
        record dict (the first in `lookup_expropriation_matches()`'s
        deterministic order), or None if the scope has been synced but no
        match exists at all. Raises CadastralDatasetCacheError if the scope
        was never synced.

        Phase API-1.9 live full-dataset audit found (district,segment,
        land_no) is NOT a unique key in the real expropriation dataset --
        441 real parcels have more than one matching row (see
        providers/expropriation_case_provider.py's module docstring for the
        full breakdown). This method therefore CANNOT represent the true
        multiplicity of such a parcel -- it silently returns only the first
        of possibly several real records. `RealExpropriationCaseProvider`
        (the actual business consumer) uses `lookup_expropriation_matches()`
        instead specifically to avoid this loss; this method remains only
        for callers that have already accepted (or predate) the single-
        result limitation."""
        matches = self.lookup_expropriation_matches(dataset_id, scope_key, district, segment, land_no)
        return matches[0] if matches else None

    def lookup_expropriation_matches(self, dataset_id: str, scope_key: str, district: str,
                                      segment: str, land_no: str) -> List[Dict]:
        """Returns EVERY raw source row matching (district,segment,land_no)
        -- never just the first, never deduplicated (an EXACT_SOURCE_
        DUPLICATE parcel's repeated identical rows are ALL returned, since
        the raw snapshot must be preserved faithfully; see
        providers/expropriation_case_provider.py's module docstring for how
        the Provider layer separately collapses these to distinct events
        for the `matches` evidence field while still reporting the raw
        count). Returns `[]` (not an exception) if the scope is synced but
        genuinely has no match -- raises CadastralDatasetCacheError only if
        the scope was never synced at all, same distinction as
        `lookup_expropriation()`.

        Deterministic ordering: sorted by the FULL row tuple (district,
        segment, land_no_raw, sus_year, pro_name) -- not just a district/
        segment/land_no prefix -- so the returned order depends only on row
        CONTENT, never on SQLite's return order (which is not guaranteed
        stable across inserts/vacuums). This mirrors the exact reasoning
        behind CANONICAL_V3's full-tuple sort key (Phase API-1.8) applied
        here to query results rather than checksums."""
        if self.get_snapshot_meta(dataset_id, scope_key) is None:
            raise CadastralDatasetCacheError(
                f"尚未同步 dataset_id={dataset_id} scope_key={scope_key} 之snapshot"
            )
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM expropriation_records WHERE dataset_id=? AND scope_key=? "
                "AND district=? AND segment=? AND land_no_raw=?",
                (dataset_id, scope_key, district, segment, land_no),
            ).fetchall()
        records = [dict(r) for r in rows]
        records.sort(key=lambda r: tuple(
            "" if r.get(f) is None else r.get(f)
            for f in ("district", "segment", "land_no_raw", "sus_year", "pro_name")
        ))
        return records

    def lookup_land_price(self, dataset_id: str, scope_key: str, district: str,
                           segment: str, lid: str) -> Optional[Dict]:
        if self.get_snapshot_meta(dataset_id, scope_key) is None:
            raise CadastralDatasetCacheError(
                f"尚未同步 dataset_id={dataset_id} scope_key={scope_key} 之snapshot"
            )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM land_price_records WHERE dataset_id=? AND scope_key=? "
                "AND district=? AND segment=? AND lid=?",
                (dataset_id, scope_key, district, segment, lid),
            ).fetchone()
        return dict(row) if row else None

    # -- verification (audit / release-gate tooling) --------------------

    def recompute_canonical_checksum(self, dataset_id: str, scope_key: str, kind: str) -> Optional[str]:
        """Re-reads the currently-stored CURRENT rows for (dataset_id,
        scope_key) and recomputes a checksum FROM STORAGE ALONE, using
        whichever algorithm `snapshot_meta.checksum_algorithm` says this
        snapshot was written under (CANONICAL_V2 or CANONICAL_V3 -- both
        fully storage-reproducible by construction). `kind` is
        "land_price" or "expropriation". Returns None if nothing is
        synced for this scope, OR if the stored snapshot is LEGACY_V1
        (LEGACY_V1 is, by design, NOT naively reproducible from storage
        alone -- see docs/backlog.md::CHECKSUM_REPRODUCIBILITY_FRAGILITY;
        that is expected, not a bug in this method)."""
        meta = self.get_snapshot_meta(dataset_id, scope_key)
        if meta is None:
            return None
        algorithm = meta.get("checksum_algorithm") or CHECKSUM_ALGORITHM_LEGACY_V1
        if algorithm == CHECKSUM_ALGORITHM_LEGACY_V1:
            return None
        with self._connect() as conn:
            if kind == "land_price":
                rows = conn.execute(
                    "SELECT country, district, segment, lid, official_value_busiprval "
                    "FROM land_price_records WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                ).fetchall()
                records = [dict(r) for r in rows]
                if algorithm == CHECKSUM_ALGORITHM_CANONICAL_V3:
                    return compute_canonical_checksum_v3_land_price(records)
                return compute_canonical_checksum_v2_land_price(records)
            elif kind == "expropriation":
                rows = conn.execute(
                    "SELECT district, segment, land_no_raw AS id, sus_year, pro_name "
                    "FROM expropriation_records WHERE dataset_id=? AND scope_key=?",
                    (dataset_id, scope_key),
                ).fetchall()
                records = [dict(r) for r in rows]
                if algorithm == CHECKSUM_ALGORITHM_CANONICAL_V3:
                    return compute_canonical_checksum_v3_expropriation(records)
                return compute_canonical_checksum_v2_expropriation(records)
            else:
                raise CadastralDatasetCacheError(f"未知kind={kind!r}")

    def audit_expropriation_parcel_multiplicity(self, dataset_id: str, scope_key: str) -> Dict:
        """Phase API-1.9: formal PARCEL_MULTI_EVENT vs EXACT_SOURCE_
        DUPLICATE classification (see providers/expropriation_case_
        provider.py's module docstring for the full definitions and the
        live audit this codifies). Reads CURRENT rows only (never staging).

        - PARCEL_MULTI_EVENT: same (district,segment,land_no), but at
          least two DIFFERENT (sus_year,pro_name) signatures among its
          rows -- a real, legitimate multi-event parcel.
        - EXACT_SOURCE_DUPLICATE (only): same (district,segment,land_no),
          and EVERY row shares the identical (sus_year,pro_name) -- a raw
          source redundancy signal, not a second event. Counted here as a
          PARCEL key (how many distinct parcels are affected), separate
          from `exact_duplicate_full_rows` (how many raw ROWS are
          redundant -- a parcel with 3 identical rows contributes 1 to the
          parcel count but 2 to the row count).

        This is a read-only diagnostic/audit helper -- it never mutates
        CURRENT or staging, and is never called from any Provider lookup
        path (Providers call `lookup_expropriation_matches()` directly)."""
        if self.get_snapshot_meta(dataset_id, scope_key) is None:
            raise CadastralDatasetCacheError(
                f"尚未同步 dataset_id={dataset_id} scope_key={scope_key} 之snapshot"
            )
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT district, segment, land_no_raw, sus_year, pro_name "
                "FROM expropriation_records WHERE dataset_id=? AND scope_key=?",
                (dataset_id, scope_key),
            ).fetchall()
        records = [dict(r) for r in rows]

        total_rows = len(records)
        by_parcel: Dict[tuple, List[dict]] = {}
        for r in records:
            key = (r["district"], r["segment"], r["land_no_raw"])
            by_parcel.setdefault(key, []).append(r)

        unique_parcel_keys = len(by_parcel)
        multi_record_parcel_keys = 0
        max_records_per_parcel = 0
        parcel_multi_event_keys = 0
        exact_source_duplicate_only_keys = 0
        exact_duplicate_full_rows = 0

        full_row_counts: Dict[tuple, int] = {}
        for r in records:
            sig = (r["district"], r["segment"], r["land_no_raw"], r["sus_year"], r["pro_name"])
            full_row_counts[sig] = full_row_counts.get(sig, 0) + 1
        exact_duplicate_full_rows = sum(c - 1 for c in full_row_counts.values() if c > 1)

        for key, parcel_rows in by_parcel.items():
            n = len(parcel_rows)
            max_records_per_parcel = max(max_records_per_parcel, n)
            if n <= 1:
                continue
            multi_record_parcel_keys += 1
            distinct_events = {(r["sus_year"], r["pro_name"]) for r in parcel_rows}
            if len(distinct_events) > 1:
                parcel_multi_event_keys += 1
            else:
                exact_source_duplicate_only_keys += 1

        return {
            "total_rows": total_rows,
            "unique_parcel_keys": unique_parcel_keys,
            "multi_record_parcel_keys": multi_record_parcel_keys,
            "max_records_per_parcel": max_records_per_parcel,
            "exact_duplicate_full_rows": exact_duplicate_full_rows,
            "parcel_multi_event_keys": parcel_multi_event_keys,
            "exact_source_duplicate_only_keys": exact_source_duplicate_only_keys,
        }
