# -*- coding: utf-8 -*-
"""
nlsc_code_cache.py — Phase API-2.3's local indexed snapshot store for
NLSC's three CONFIRMED-OPEN, no-auth code services (Phase API-2.2R2 live
audit): ListCounty (COM_003), ListTown (COM_004), ListLandSection (COM_006).

Deliberately a SEPARATE store from providers/cadastral_dataset_cache.py
(FROZEN, unrelated NTPC dataset family) and from providers/facility_
dataset_cache.py (unrelated NTPC facility dataset family) -- same
architectural principle (Atomic Staging Contract: STAGING -> validation ->
atomic promote -> CURRENT, established Phase API-1.8, reapplied per Phase
API-2 module's own docstring convention), new tables, because this data
(county/town/section administrative codes) is a third, disjoint domain
from either.

Why cache at all rather than call NLSC per case: county/town/section codes
change extremely rarely (administrative boundaries, not daily data), and
this round's explicit instruction is "不要讓每一筆案件都runtime call
NLSC" -- a one-time (or periodic) sync via scripts/sync_nlsc_codes.py
populates this store; NlscCadastralCodeResolver (providers/nlsc_cadastral_
code_resolver.py) reads ONLY from here at request time, never the network.

Scope of what gets synced (see scripts/sync_nlsc_codes.py's own docstring
for the live request counts): ListCounty is synced in full (all counties,
1 request) since it is tiny; ListTown is synced for every county (~22
requests, still tiny) since a case could reference any city; ListLandSection
is synced only for 新北市's 29 towns this round (matching this entire
project's established New Taipei City scope) -- NOT all of Taiwan's
sections, which would be tens of thousands of requests for no benefit this
round's Golden Case and known use cases need. This is a documented,
deliberate scope decision, not a silent omission.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "nlsc_code_cache.sqlite3"
)

SCOPE_ALL = "__ALL__"

SCHEMA_VERSION = "nlsc_code.v1"
CHECKSUM_ALGORITHM = "CANONICAL_V1"

STAGING_STATUS_IN_PROGRESS = "IN_PROGRESS"
STAGING_STATUS_FAILED = "FAILED"
STAGING_STATUS_PROMOTED = "PROMOTED"

# Phase API-2.3H §7/§8: ListLandSection has only ever been synced for one
# county (新北市/"F") -- see scripts/sync_nlsc_codes.py's own module
# docstring for why (a deliberate scope decision, not a silent omission).
# `counties`/`towns` ARE synced for every county in Taiwan (see that same
# script), so ONLY the `sections` resource has this narrower coverage.
# NlscCadastralCodeResolver consults this constant BEFORE treating a 0-row
# section lookup as a negative result, so a county this codebase never
# synced sections for reports OUT_OF_COVERAGE, never NOT_FOUND.
SECTION_SNAPSHOT_COVERAGE = "NEW_TAIPEI_CITY"
SECTION_SNAPSHOT_COVERAGE_COUNTY_CODES = frozenset({"F"})

# Phase API-2.3H §8: per-resource coverage label persisted into
# snapshot_meta alongside the existing fields -- "ALL_TAIWAN" for
# counties/towns, SECTION_SNAPSHOT_COVERAGE for sections.
COVERAGE_ALL_TAIWAN = "ALL_TAIWAN"
_RESOURCE_COVERAGE = {
    "counties": COVERAGE_ALL_TAIWAN,
    "towns": COVERAGE_ALL_TAIWAN,
    "sections": SECTION_SNAPSHOT_COVERAGE,
}


class NlscCodeCacheError(Exception):
    pass


def _canonical_checksum(records: List[dict], fields: tuple) -> str:
    """Same principle as cadastral_dataset_cache.py's CANONICAL_V3 / Phase
    API-2's CANONICAL_V1: fixed field order (array, not dict), sorted by
    the FULL row tuple -- order-independent by construction, no dependency
    on SQLite rowid or insertion order."""
    arrays = [[r.get(f) for f in fields] for r in records]
    arrays.sort(key=lambda arr: tuple("" if v is None else v for v in arr))
    canonical = json.dumps(arrays, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class NlscCodeCache:
    """Two operating modes (Phase API-2.3H §9 -- AWS Lambda Read-Only
    Audit): `read_only=False` (default, unchanged from Phase API-2.3) is
    SYNC_READ_WRITE mode -- used only by scripts/sync_nlsc_codes.py and
    this module's own tests, freely creates the directory/schema and
    accepts staging/promote writes. `read_only=True` is RUNTIME_READ_ONLY
    mode -- used by NlscCadastralCodeResolver/RealOfficialParcelCoordinate
    Provider (the actual Lambda request path): never calls os.makedirs,
    never runs CREATE TABLE, opens the sqlite file via a `mode=ro` URI
    connection (so SQLite itself refuses any write/journal-file attempt at
    the OS level, not merely by this class's own convention), and every
    staging/promote method raises NlscCodeCacheError immediately rather
    than silently no-op'ing. A Lambda Layer's mounted filesystem
    (`/opt/python/...`) is commonly read-only, and even a pure SELECT can
    otherwise trigger sqlite3 to attempt a rollback-journal file next to
    the DB file -- `mode=ro` prevents that class of failure outright."""

    def __init__(self, db_path: Optional[str] = None, *, read_only: bool = False):
        self.db_path = db_path or os.environ.get("NLSC_CODE_CACHE_DB_PATH") or DEFAULT_DB_PATH
        self.read_only = read_only
        if self.db_path == ":memory:":
            # Phase API-2.3H §13: every `sqlite3.connect(":memory:")` call
            # opens a BRAND NEW, independent in-memory database -- and this
            # class opens a fresh connection per method call (`with self.
            # _connect() as conn:`). Staging and promote would each see
            # their own empty database, silently breaking the Atomic
            # Staging Contract in a way no test happened to catch (the
            # earlier draft of this module's own test suite briefly used
            # ":memory:" and only "passed" because it never exercised more
            # than one connection in a single test). Rather than leave
            # that as a latent trap (or worse, quietly "fix" it by holding
            # one long-lived connection open, which nothing in this
            # codebase actually needs), this is an explicit, documented
            # refusal: ":memory:" is NOT a supported db_path for this
            # class. Use a real tmp_path-scoped file in tests instead (see
            # tests/test_nlsc_code_cache.py).
            raise NlscCodeCacheError(
                "NlscCodeCache不支援db_path=':memory:'："
                "每次_connect()皆會開啟全新、彼此獨立的in-memory資料庫，"
                "staging/promote跨連線寫入將互不可見，非本類別實際支援之用法。"
                "測試請改用tmp_path實體檔案。"
            )
        if not self.read_only:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            # `mode=ro` is enforced by SQLite itself, not just this class's
            # convention -- any accidental write attempt (including an
            # implicit journal file) raises sqlite3.OperationalError rather
            # than silently succeeding against a read-only Lambda Layer
            # filesystem. A missing DB file also raises OperationalError
            # here (SQLite cannot open a nonexistent file in ro mode),
            # translated below into a clear NlscCodeCacheError.
            uri = f"file:{self.db_path}?mode=ro"
            try:
                conn = sqlite3.connect(uri, uri=True)
            except sqlite3.OperationalError as e:
                raise NlscCodeCacheError(
                    f"NlscCodeCache（RUNTIME_READ_ONLY模式）無法開啟db_path={self.db_path!r}："
                    f"{e}。請先於SYNC_READ_WRITE模式（read_only=False）執行"
                    "scripts/sync_nlsc_codes.py完成同步。"
                ) from e
        else:
            conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _require_writable(self, action: str) -> None:
        if self.read_only:
            raise NlscCodeCacheError(
                f"NlscCodeCache處於RUNTIME_READ_ONLY模式，拒絕執行寫入操作：{action}。"
                "此模式僅供Lambda request path唯讀查詢使用，"
                "同步/寫入請使用read_only=False（SYNC_READ_WRITE模式）之NlscCodeCache實例。"
            )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshot_meta (
                    resource TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    source_service TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    PRIMARY KEY (resource, scope_key)
                )
            """)
            # Phase API-2.3H §8: `coverage`/`checksum_algorithm` are new
            # columns added on top of Phase API-2.3's original snapshot_
            # meta schema. `ALTER TABLE ... ADD COLUMN` is NOT idempotent
            # via "IF NOT EXISTS" in all SQLite versions this project might
            # run against, so existing installations (including this
            # round's already-synced data/nlsc_code_cache.sqlite3) are
            # migrated explicitly via PRAGMA table_info -- never by
            # dropping/recreating the table, which would discard real
            # synced data.
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshot_meta)").fetchall()}
            if "coverage" not in existing_cols:
                conn.execute("ALTER TABLE snapshot_meta ADD COLUMN coverage TEXT")
            if "checksum_algorithm" not in existing_cols:
                conn.execute("ALTER TABLE snapshot_meta ADD COLUMN checksum_algorithm TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS counties (
                    scope_key TEXT NOT NULL, county_code TEXT NOT NULL, county_name TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_counties_name ON counties(scope_key, county_name)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS towns (
                    scope_key TEXT NOT NULL, county_code TEXT NOT NULL,
                    town_code TEXT NOT NULL, town_name TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_towns_lookup ON towns(scope_key, county_code, town_name)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    scope_key TEXT NOT NULL, county_code TEXT NOT NULL, town_code TEXT NOT NULL,
                    office TEXT, section_code TEXT NOT NULL, section_name TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sections_lookup ON sections(scope_key, county_code, town_code, section_name)"
            )
            for table in ("counties", "towns", "sections"):
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table}_staging AS SELECT * FROM {table} WHERE 0
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS staging_run_status (
                    resource TEXT NOT NULL PRIMARY KEY,
                    status TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, note TEXT
                )
            """)

    # -- Atomic Staging Contract ------------------------------------------

    def begin_staging_run(self, resource: str, note: Optional[str] = None) -> None:
        self._require_writable(f"begin_staging_run({resource!r})")
        now = datetime.now().isoformat()
        table = f"{resource}_staging"
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "INSERT INTO staging_run_status (resource, status, started_at, updated_at, note) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(resource) DO UPDATE SET status=excluded.status, "
                "started_at=excluded.started_at, updated_at=excluded.updated_at, note=excluded.note",
                (resource, STAGING_STATUS_IN_PROGRESS, now, now, note),
            )

    def stage_counties(self, records: List[dict]) -> None:
        self._require_writable("stage_counties")
        with self._connect() as conn:
            conn.execute("DELETE FROM counties_staging")
            conn.executemany(
                "INSERT INTO counties_staging (scope_key, county_code, county_name) VALUES (?, ?, ?)",
                [(SCOPE_ALL, r["county_code"], r["county_name"]) for r in records],
            )
            conn.execute("UPDATE staging_run_status SET updated_at=? WHERE resource=?",
                         (datetime.now().isoformat(), "counties"))

    def stage_towns(self, county_code: str, records: List[dict]) -> None:
        self._require_writable("stage_towns")
        with self._connect() as conn:
            conn.execute("DELETE FROM towns_staging WHERE county_code=?", (county_code,))
            conn.executemany(
                "INSERT INTO towns_staging (scope_key, county_code, town_code, town_name) VALUES (?, ?, ?, ?)",
                [(SCOPE_ALL, county_code, r["town_code"], r["town_name"]) for r in records],
            )
            conn.execute("UPDATE staging_run_status SET updated_at=? WHERE resource=?",
                         (datetime.now().isoformat(), "towns"))

    def stage_sections(self, county_code: str, town_code: str, records: List[dict]) -> None:
        self._require_writable("stage_sections")
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sections_staging WHERE county_code=? AND town_code=?", (county_code, town_code)
            )
            conn.executemany(
                "INSERT INTO sections_staging (scope_key, county_code, town_code, office, section_code, section_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(SCOPE_ALL, county_code, town_code, r.get("office"), r["section_code"], r["section_name"])
                 for r in records],
            )
            conn.execute("UPDATE staging_run_status SET updated_at=? WHERE resource=?",
                         (datetime.now().isoformat(), "sections"))

    def mark_staging_failed(self, resource: str, note: str) -> None:
        self._require_writable("mark_staging_failed")
        with self._connect() as conn:
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=?, note=? WHERE resource=?",
                (STAGING_STATUS_FAILED, datetime.now().isoformat(), note, resource),
            )

    def get_staging_status(self, resource: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM staging_run_status WHERE resource=?", (resource,)).fetchone()
        return dict(row) if row else None

    def promote_counties(self, *, source_service: str, source_url: str) -> Dict:
        return self._promote(
            resource="counties", table="counties", fields=("county_code", "county_name"),
            source_service=source_service, source_url=source_url,
        )

    def promote_towns(self, *, source_service: str, source_url: str) -> Dict:
        return self._promote(
            resource="towns", table="towns", fields=("county_code", "town_code", "town_name"),
            source_service=source_service, source_url=source_url,
        )

    def promote_sections(self, *, source_service: str, source_url: str) -> Dict:
        return self._promote(
            resource="sections", table="sections",
            fields=("county_code", "town_code", "office", "section_code", "section_name"),
            source_service=source_service, source_url=source_url,
        )

    def _promote(self, *, resource: str, table: str, fields: tuple, source_service: str, source_url: str) -> Dict:
        """Atomically replaces the ENTIRE CURRENT table with the ENTIRE
        staged table, as one transaction -- rollback-on-raise via `with
        self._connect() as conn:`, same reasoning as cadastral_dataset_
        cache.py's promote_*. `counties`/`towns`/`sections` are synced as
        one whole resource (not per-scope_key like the frozen cadastral
        cache) since their total size is small (Phase API-2.3's scope --
        see module docstring -- is at most ~22 counties + ~22*~30 towns +
        29 New Taipei sections lists, a few hundred rows total)."""
        self._require_writable(f"_promote({resource!r})")
        staging_table = f"{table}_staging"
        downloaded_at = datetime.now().isoformat()
        coverage = _RESOURCE_COVERAGE.get(resource)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM {staging_table}").fetchall()
            if not rows:
                raise NlscCodeCacheError(f"promote失敗：{resource}在staging中無資料，拒絕promote（CURRENT未變更）")
            records = [dict(r) for r in rows]
            checksum = _canonical_checksum(records, fields)

            conn.execute(f"DELETE FROM {table}")
            placeholders = ", ".join("?" for _ in ("scope_key",) + fields)
            columns = ", ".join(("scope_key",) + fields)
            conn.executemany(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                [(SCOPE_ALL,) + tuple(r.get(f) for f in fields) for r in records],
            )
            conn.execute(
                "INSERT INTO snapshot_meta (resource, scope_key, source_service, source_url, downloaded_at, "
                "checksum_sha256, record_count, schema_version, coverage, checksum_algorithm) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(resource, scope_key) DO UPDATE SET source_service=excluded.source_service, "
                "source_url=excluded.source_url, downloaded_at=excluded.downloaded_at, "
                "checksum_sha256=excluded.checksum_sha256, record_count=excluded.record_count, "
                "schema_version=excluded.schema_version, coverage=excluded.coverage, "
                "checksum_algorithm=excluded.checksum_algorithm",
                (resource, SCOPE_ALL, source_service, source_url, downloaded_at, checksum, len(records),
                 SCHEMA_VERSION, coverage, CHECKSUM_ALGORITHM),
            )
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {staging_table}")
            conn.execute(
                "UPDATE staging_run_status SET status=?, updated_at=? WHERE resource=?",
                (STAGING_STATUS_PROMOTED, datetime.now().isoformat(), resource),
            )
        return {"record_count": len(records), "checksum_sha256": checksum}

    # -- reading (NlscCadastralCodeResolver only) -------------------------

    def get_snapshot_meta(self, resource: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshot_meta WHERE resource=? AND scope_key=?", (resource, SCOPE_ALL)
            ).fetchone()
        return dict(row) if row else None

    def get_current_counties(self) -> List[Dict]:
        """All CURRENT counties, for callers (e.g. scripts/sync_nlsc_
        codes.py's `towns`-only resync path) that need the full list
        without re-fetching ListCounty. Raises NlscCodeCacheError if
        counties were never synced (same "never synced" vs "synced but
        empty" distinction as the other lookup methods)."""
        if self.get_snapshot_meta("counties") is None:
            raise NlscCodeCacheError("尚未同步counties之snapshot")
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM counties ORDER BY county_code").fetchall()
        return [dict(r) for r in rows]

    def lookup_county(self, county_name: str) -> List[Dict]:
        if self.get_snapshot_meta("counties") is None:
            raise NlscCodeCacheError("尚未同步counties之snapshot")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM counties WHERE county_name=? ORDER BY county_code", (county_name,)
            ).fetchall()
        return [dict(r) for r in rows]

    def lookup_town(self, county_code: str, town_name: str) -> List[Dict]:
        if self.get_snapshot_meta("towns") is None:
            raise NlscCodeCacheError("尚未同步towns之snapshot")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM towns WHERE county_code=? AND town_name=? ORDER BY town_code",
                (county_code, town_name),
            ).fetchall()
        return [dict(r) for r in rows]

    def is_county_in_section_coverage(self, county_code: str) -> bool:
        """Phase API-2.3H §7: a pure, static membership check against
        SECTION_SNAPSHOT_COVERAGE_COUNTY_CODES (never a DB query) -- lets
        NlscCadastralCodeResolver distinguish "this county's sections were
        never in scope for sync_nlsc_codes.py at all" (OUT_OF_COVERAGE)
        from "this county's sections WERE synced and genuinely have no
        matching section name" (NOT_FOUND), BEFORE running the section
        lookup query itself."""
        return county_code in SECTION_SNAPSHOT_COVERAGE_COUNTY_CODES

    def lookup_section(self, county_code: str, town_code: str, section_name: str) -> List[Dict]:
        if self.get_snapshot_meta("sections") is None:
            raise NlscCodeCacheError("尚未同步sections之snapshot")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sections WHERE county_code=? AND town_code=? AND section_name=? "
                "ORDER BY office, section_code",
                (county_code, town_code, section_name),
            ).fetchall()
        return [dict(r) for r in rows]
