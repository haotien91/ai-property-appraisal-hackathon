# -*- coding: utf-8 -*-
"""
gis_snapshot_bootstrap.py — S3-to-/tmp cold-start bootstrap for the two
large NTPC GIS point-in-polygon snapshots (新北市使用分區 / 新北市都市計畫
範圍) that `providers/ntpc_zoning_provider.py` / `providers/urban_plan_
boundary_provider.py` read via DatasetRegistry.

WHY THIS EXISTS (2026-09-09 packaging audit, see docs/backlog.md's
URBAN_PLAN_BOUNDARY_SNAPSHOT_NOT_PACKAGED entry): baking these snapshots
into EngineLayer (like data/nlsc_code_cache.sqlite3, a few hundred KB) was
attempted first, but (a) the 新北市使用分區 snapshot alone is ~190MB,
uncomfortably close to Lambda's 250MB combined Layer+package limit even
before accounting for pydantic/shapely/numpy already in EngineLayer, and
(b) `sam build --use-container` on this dev machine was observed to not
reliably pick up new Makefile copy rules for these specific files despite
extensive investigation (recorded in the same backlog entry). Both reasons
independently point to the SAME fix already proven for the ~407MB
cadastral dataset: `backend/handlers/cadastral_snapshot_bootstrap.py`'s
S3-at-cold-start pattern. This module deliberately mirrors that one's
core safety invariant (temp-file-then-atomic-replace, checksum-verified,
never a stale/partial file exposed as canonical) rather than inventing a
different architecture -- see that module's docstring for the full
rationale, which applies here unchanged. `cadastral_snapshot_bootstrap.py`
itself is FROZEN and NOT modified or imported by this module; this is an
independent, disjoint module for a different dataset family, same
principles re-applied (same reasoning `facility_dataset_cache.py` gives
for not extending the frozen `cadastral_dataset_cache.py`).

HOW THIS DIFFERS FROM cadastral_snapshot_bootstrap.py:
1. Two independent datasets, not one -- see PARTIAL FAILURE below.
2. cadastral_snapshot_bootstrap.py's target (CadastralDatasetCache) reads
   a fixed path directly, no registry involved. These two datasets are
   read via providers/dataset_registry.py's DatasetRegistry (which is how
   `RealNtpcZoningProvider`/`RealUrbanPlanBoundaryProvider` already know
   where to look, and how STALE-vs-CURRENT is already computed) -- so a
   successful bootstrap here ALSO registers the downloaded file into a
   RUNTIME (not the developer's own) DatasetRegistry at
   GIS_RUNTIME_REGISTRY_DB_PATH (default /tmp/runtime_dataset_registry.
   sqlite3), never touching the packaged data/dataset_registry.sqlite3.
   See `_register_runtime_snapshot`.
3. Local/offline development is UNCHANGED and untouched by this module:
   `ensure_gis_snapshots()` is a no-op unless LAMBDA_TASK_ROOT is set (the
   same explicit Lambda-vs-local signal backend/handlers/runtime_paths.py
   already uses -- reused here rather than inventing a second detection
   mechanism) -- local dev keeps reading data/dataset_registry.sqlite3 /
   data/snapshots/ exactly as scripts/sync_ntpc_zoning_dataset.py already
   produces them, via DatasetRegistry's existing DEFAULT_DB_PATH.

PARTIAL FAILURE IS EXPECTED, NOT AN ERROR: the two datasets are bootstrapped
and registered independently. Zoning succeeding while plan-boundary fails
(or vice versa) is a normal, safe outcome -- `ensure_gis_snapshots()`
returns a per-dataset result dict rather than an all-or-nothing status, and
only SUCCESSFULLY bootstrapped datasets get registered. A provider whose
dataset was never registered takes its own already-established UNAVAILABLE
degradation path (RealNtpcZoningProvider.query() /
RealUrbanPlanBoundaryProvider.resolve_urban_plan() are UNMODIFIED by this
module), exactly as if that dataset had never been synced.

WARM REUSE: within one Lambda execution environment (container reuse
across invocations), a dataset already verified by an earlier invocation
in THIS process is trusted without re-hashing a (potentially 190MB) file
on every request -- see `_WARM_VERIFIED`. A NEW cold container always
re-verifies via SHA-256 before trusting anything already on its own /tmp.

NEVER: Real -> Mock, Real -> Golden, or "S3 unreachable -> embed a fake/
placeholder snapshot". Every failure mode (missing config, S3 error,
checksum mismatch) converges on the SAME outcome as a dataset that was
never registered at all: the frozen provider query returns UNKNOWN /
requires_manual_review=True. This module never fabricates data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger()

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


class SnapshotBootstrapStatus(str, Enum):
    ALREADY_PRESENT = "ALREADY_PRESENT"    # warm /tmp reuse (this or an earlier invocation), checksum matched
    DOWNLOADED = "DOWNLOADED"              # fresh S3 download this call, checksum matched, atomically published
    MISSING_CONFIG = "MISSING_CONFIG"      # one or more required env vars absent -- not attempted
    S3_UNAVAILABLE = "S3_UNAVAILABLE"      # network/S3 error, or local I/O error around the download
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"  # downloaded object's SHA-256 did not match the expected value


_UNAVAILABLE_STATUSES = frozenset({
    SnapshotBootstrapStatus.MISSING_CONFIG,
    SnapshotBootstrapStatus.S3_UNAVAILABLE,
    SnapshotBootstrapStatus.CHECKSUM_MISMATCH,
})


@dataclass
class GisSnapshotBootstrapResult:
    dataset_id: str
    status: SnapshotBootstrapStatus
    local_path: Optional[str] = None
    notes: Optional[str] = None
    download_ms: Optional[float] = None
    checksum_ms: Optional[float] = None
    warm_ms: Optional[float] = None
    snapshot_bytes: Optional[int] = None

    @property
    def available(self) -> bool:
        return self.status not in _UNAVAILABLE_STATUSES


@dataclass(frozen=True)
class _DatasetConfig:
    dataset_id: str
    env_bucket: str
    env_key: str
    env_sha256: str
    env_runtime_path: str
    # Kept in sync BY HAND with scripts/sync_ntpc_zoning_dataset.py's own
    # SOURCE_NAME/SOURCE_AGENCY constants -- that script is a dev-only tool
    # under scripts/, never packaged into EngineLayer, so it cannot be
    # imported at Lambda runtime; duplicating these two descriptive
    # strings is the accepted cost of that packaging boundary.
    source_name: str
    source_agency: str


ZONING_CONFIG = _DatasetConfig(
    dataset_id="ntpc_zoning",
    env_bucket="NTPC_ZONING_SNAPSHOT_S3_BUCKET",
    env_key="NTPC_ZONING_SNAPSHOT_S3_KEY",
    env_sha256="NTPC_ZONING_SNAPSHOT_SHA256",
    env_runtime_path="NTPC_ZONING_RUNTIME_PATH",
    source_name="新北市都市計畫土地使用分區及範圍圖",
    source_agency="新北市政府城鄉發展局",
)

PLAN_BOUNDARY_CONFIG = _DatasetConfig(
    dataset_id="ntpc_plan_boundary",
    env_bucket="NTPC_PLAN_BOUNDARY_SNAPSHOT_S3_BUCKET",
    env_key="NTPC_PLAN_BOUNDARY_SNAPSHOT_S3_KEY",
    env_sha256="NTPC_PLAN_BOUNDARY_SNAPSHOT_SHA256",
    env_runtime_path="NTPC_PLAN_BOUNDARY_RUNTIME_PATH",
    source_name="新北市都市計畫範圍",
    source_agency="新北市政府城鄉發展局",
)

ENV_RUNTIME_REGISTRY_PATH = "GIS_RUNTIME_REGISTRY_DB_PATH"
DEFAULT_RUNTIME_REGISTRY_PATH = "/tmp/runtime_dataset_registry.sqlite3"

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB streamed reads -- never loads a ~190MB file into memory at once

# Process-level warm cache: dataset_id -> local_path already SHA-256-
# verified by THIS process (Lambda execution environment). A NEW cold
# container starts with an empty dict, so it always re-verifies at least
# once -- this only ever skips re-hashing on the SAME warm container's
# later invocations, per this module's docstring.
_WARM_VERIFIED: Dict[str, str] = {}


def _is_lambda_runtime() -> bool:
    """Same explicit signal backend/handlers/runtime_paths.py already uses
    to distinguish real/emulated Lambda from local development -- reused
    here rather than a second detection mechanism (AWS_LAMBDA_FUNCTION_NAME
    is also set by the real runtime, but LAMBDA_TASK_ROOT is this
    codebase's own established convention, confirmed correct for both real
    and `sam local invoke`-emulated execution by runtime_paths.py's own
    docstring)."""
    return bool(os.environ.get("LAMBDA_TASK_ROOT"))


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_if_exists(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _log(dataset_id: str, status: str, **fields) -> None:
    """Structured JSON log line, same spirit as backend/handlers/common.py's
    log_step (CloudWatch-friendly, no raw case content) but for this
    module's own cold/warm bootstrap metrics -- COLD_BOOTSTRAP_DOWNLOAD_MS/
    COLD_BOOTSTRAP_SHA256_MS/WARM_BOOTSTRAP_MS/SNAPSHOT_BYTES."""
    record = {"component": "gis_snapshot_bootstrap", "dataset_id": dataset_id, "status": status}
    for key, value in fields.items():
        if value is not None:
            record[key] = round(value, 2) if isinstance(value, float) else value
    logger.info(json.dumps(record, ensure_ascii=False))


def _ensure_snapshot(config: _DatasetConfig, *, s3_client=None) -> GisSnapshotBootstrapResult:
    """Idempotent; safe to call on every invocation. Mirrors
    cadastral_snapshot_bootstrap.ensure_cadastral_snapshot()'s core safety
    invariant: once this returns, the file at config.env_runtime_path is in
    EXACTLY one of two states -- a byte-for-byte verified snapshot, or
    absent. Never a stale/checksum-mismatched file. `s3_client` is
    DI-injectable for tests (never constructs a real `boto3.client("s3")`
    itself unless one isn't supplied)."""
    cached_path = _WARM_VERIFIED.get(config.dataset_id)
    if cached_path and os.path.exists(cached_path):
        t0 = time.time()
        warm_ms = (time.time() - t0) * 1000
        result = GisSnapshotBootstrapResult(
            dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.ALREADY_PRESENT,
            local_path=cached_path, warm_ms=warm_ms,
            notes="process-level warm cache reuse，本次呼叫未重新驗證checksum",
        )
        _log(config.dataset_id, result.status.value, WARM_BOOTSTRAP_MS=warm_ms)
        return result

    bucket = os.environ.get(config.env_bucket)
    key = os.environ.get(config.env_key)
    expected_sha256 = os.environ.get(config.env_sha256)
    final_path = os.environ.get(config.env_runtime_path)

    if not (bucket and key and expected_sha256 and final_path):
        result = GisSnapshotBootstrapResult(
            dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.MISSING_CONFIG,
            notes=(
                f"缺少必要環境變數（{config.env_bucket}/{config.env_key}/"
                f"{config.env_sha256}/{config.env_runtime_path}其中之一或多個未設定），"
                "無法判斷snapshot來源，不嘗試下載，不使用任何預設/假資料"
            ),
        )
        _log(config.dataset_id, result.status.value)
        return result

    # --- Warm reuse / stale-detection (same design as cadastral_snapshot_
    # bootstrap.py) -- an existing file at final_path is used ONLY if its
    # checksum matches RIGHT NOW, never assumed valid just because it
    # exists. A mismatch means this exact file has already been proven
    # wrong; it is deleted immediately, before any download is attempted,
    # so no path can fall through to "download failed, leave the old file
    # in place".
    if os.path.isfile(final_path):
        t0 = time.time()
        try:
            actual = _sha256_of_file(final_path)
        except OSError:
            actual = None
        checksum_ms = (time.time() - t0) * 1000
        if actual == expected_sha256:
            _WARM_VERIFIED[config.dataset_id] = final_path
            result = GisSnapshotBootstrapResult(
                dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.ALREADY_PRESENT,
                local_path=final_path, checksum_ms=checksum_ms,
                snapshot_bytes=os.path.getsize(final_path),
                notes="既有本地snapshot checksum相符，直接reuse，未觸發下載",
            )
            _log(config.dataset_id, result.status.value, COLD_BOOTSTRAP_SHA256_MS=checksum_ms,
                 SNAPSHOT_BYTES=result.snapshot_bytes)
            return result
        _remove_if_exists(final_path)

    target_dir = os.path.dirname(final_path) or "."
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        result = GisSnapshotBootstrapResult(
            dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.S3_UNAVAILABLE,
            notes=f"無法建立snapshot目標目錄{target_dir!r}：{e}",
        )
        _log(config.dataset_id, result.status.value)
        return result

    temp_fd, temp_path = tempfile.mkstemp(prefix=f".{config.dataset_id}_download_", dir=target_dir)
    os.close(temp_fd)
    try:
        if s3_client is None:
            import boto3
            s3_client = boto3.client("s3")

        t0 = time.time()
        try:
            s3_client.download_file(bucket, key, temp_path)
        except Exception as e:  # noqa: BLE001 -- any S3/network failure degrades the same way
            result = GisSnapshotBootstrapResult(
                dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.S3_UNAVAILABLE,
                notes=f"從S3下載snapshot失敗（bucket={bucket!r}, key={key!r}）：{e}",
            )
            _log(config.dataset_id, result.status.value)
            return result
        download_ms = (time.time() - t0) * 1000

        t0 = time.time()
        try:
            actual_sha256 = _sha256_of_file(temp_path)
        except OSError as e:
            result = GisSnapshotBootstrapResult(
                dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.S3_UNAVAILABLE,
                download_ms=download_ms, notes=f"下載完成後之checksum驗證讀取失敗：{e}",
            )
            _log(config.dataset_id, result.status.value, COLD_BOOTSTRAP_DOWNLOAD_MS=download_ms)
            return result
        checksum_ms = (time.time() - t0) * 1000

        if actual_sha256 != expected_sha256:
            result = GisSnapshotBootstrapResult(
                dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.CHECKSUM_MISMATCH,
                download_ms=download_ms, checksum_ms=checksum_ms,
                notes=(
                    f"下載完成但SHA-256不符（expected={expected_sha256}, actual={actual_sha256}），"
                    "拒絕發布，canonical路徑維持不存在，絕不使用此檔案"
                ),
            )
            _log(config.dataset_id, result.status.value, COLD_BOOTSTRAP_DOWNLOAD_MS=download_ms,
                 COLD_BOOTSTRAP_SHA256_MS=checksum_ms)
            return result

        snapshot_bytes = os.path.getsize(temp_path)
        # final_path is guaranteed absent at this point (removed above if
        # stale, or never existed) -- os.replace is atomic within one
        # filesystem, which /tmp always is here (same dir as temp_path).
        os.replace(temp_path, final_path)
        _WARM_VERIFIED[config.dataset_id] = final_path
        result = GisSnapshotBootstrapResult(
            dataset_id=config.dataset_id, status=SnapshotBootstrapStatus.DOWNLOADED,
            local_path=final_path, download_ms=download_ms, checksum_ms=checksum_ms,
            snapshot_bytes=snapshot_bytes,
            notes=f"下載並驗證成功（SHA-256={actual_sha256}），已atomic發布至{final_path}",
        )
        _log(config.dataset_id, result.status.value, COLD_BOOTSTRAP_DOWNLOAD_MS=download_ms,
             COLD_BOOTSTRAP_SHA256_MS=checksum_ms, SNAPSHOT_BYTES=snapshot_bytes)
        return result
    finally:
        # Never leaves a partial/orphaned temp file behind, whether this
        # function returned via success (already renamed away, so this is
        # a harmless no-op) or any failure path above.
        _remove_if_exists(temp_path)


def ensure_ntpc_zoning_snapshot(s3_client=None) -> GisSnapshotBootstrapResult:
    return _ensure_snapshot(ZONING_CONFIG, s3_client=s3_client)


def ensure_ntpc_plan_boundary_snapshot(s3_client=None) -> GisSnapshotBootstrapResult:
    return _ensure_snapshot(PLAN_BOUNDARY_CONFIG, s3_client=s3_client)


def _version_from_s3_key(key: str) -> str:
    """Extracts the immutable version segment from a key shaped like
    'datasets/ntpc-gis/<version>/ntpc_zoning.sqlite3' (see infra/
    template.yaml's parameter descriptions for this convention -- 'latest'-
    style keys are explicitly disallowed there). Falls back to the full key
    if it doesn't have at least two segments, rather than raising -- this
    only ever feeds a human-readable audit-trail string
    (local_snapshot_version), never a control-flow decision."""
    parts = [p for p in key.split("/") if p]
    return parts[-2] if len(parts) >= 2 else key


def _register_runtime_snapshot(config: _DatasetConfig, result: GisSnapshotBootstrapResult) -> None:
    """Registers a successfully-bootstrapped dataset into the RUNTIME
    registry at GIS_RUNTIME_REGISTRY_DB_PATH (default /tmp/runtime_
    dataset_registry.sqlite3) -- built fresh in Lambda /tmp, NEVER by
    copying/patching the packaged data/dataset_registry.sqlite3 (that file
    is not shipped to Lambda at all under this design -- see module
    docstring's "WHY THIS EXISTS"). RealNtpcZoningProvider/
    RealUrbanPlanBoundaryProvider's own DatasetRegistry() picks up the
    SAME path via the DATASET_REGISTRY_DB_PATH env var set in infra/
    template.yaml, so this registration is the only bridge needed between
    this module and those frozen/unmodified provider classes."""
    import runtime_paths
    runtime_paths.bootstrap()
    from dataset_registry import DatasetRegistry  # noqa: E402
    from domain.models import DatasetSnapshotInfo  # noqa: E402

    bucket = os.environ.get(config.env_bucket, "")
    key = os.environ.get(config.env_key, "")
    expected_sha256 = os.environ.get(config.env_sha256, "")

    registry = DatasetRegistry(db_path=os.environ.get(ENV_RUNTIME_REGISTRY_PATH, DEFAULT_RUNTIME_REGISTRY_PATH))
    registry.register_snapshot(DatasetSnapshotInfo(
        dataset_id=config.dataset_id,
        source_name=config.source_name,
        source_agency=config.source_agency,
        source_url=f"s3://{bucket}/{key}",
        local_snapshot_version=_version_from_s3_key(key),
        local_path=result.local_path,
        checksum=expected_sha256,
        license=None,
        refresh_policy="quarterly",
        # Represents "this Lambda execution environment confirmed a
        # checksum-verified copy as of now", not the ORIGINAL sync time on
        # whatever machine first produced the S3 object -- this dataset's
        # freshness is controlled by redeploying with a new (immutable)
        # S3 key + SHA256 (see infra/template.yaml's parameter
        # descriptions), not by periodic re-sync staleness detection, so
        # this is an honest, documented simplification rather than a
        # silent inaccuracy.
        last_synced_at=datetime.now(),
        source_last_modified=None,
        record_count=None,
        notes=(
            f"AWS Lambda S3 runtime bootstrap（gis_snapshot_bootstrap.py）；"
            f"bucket={bucket}；key={key}；status={result.status.value}"
        ),
    ))


def ensure_gis_snapshots(s3_client=None) -> Dict[str, GisSnapshotBootstrapResult]:
    """Orchestrates both datasets independently (partial failure allowed --
    see module docstring). No-op (returns {}) outside Lambda -- local/
    offline development keeps reading data/dataset_registry.sqlite3 /
    data/snapshots/ exactly as before, completely unaffected by this
    module. Call once per request, before constructing RealNtpcZoning
    Provider/RealUrbanPlanBoundaryProvider (mirrors backend/handlers/
    collect_data.py's existing cadastral_snapshot_bootstrap.
    ensure_cadastral_snapshot() call site)."""
    if not _is_lambda_runtime():
        return {}

    results: Dict[str, GisSnapshotBootstrapResult] = {}
    for config in (ZONING_CONFIG, PLAN_BOUNDARY_CONFIG):
        result = _ensure_snapshot(config, s3_client=s3_client)
        results[config.dataset_id] = result
        if result.available:
            _register_runtime_snapshot(config, result)
    return results
