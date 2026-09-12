# -*- coding: utf-8 -*-
"""
cadastral_snapshot_bootstrap.py — Phase DEPLOY-1E: fetches
data/cadastral_dataset_cache.sqlite3 (~407MB) from S3 into a Lambda
execution environment's /tmp on cold start, so it never needs to be
packaged into EngineLayer (structurally impossible anyway -- 407MB alone
exceeds Lambda's 250MB combined Layer+deployment-package limit; see
docs/phase7's Phase DEPLOY-1 packaging audit).

CORE PRINCIPLE (this round's explicit correction): this module ONLY
moves a file. It never opens the file as a database, never runs SQL,
never performs a cadastral lookup, never decides a valuation result, and
never falls back to Mock or Golden data. The FROZEN `providers/
cadastral_dataset_cache.py::CadastralDatasetCache` is NOT a read-only-mode
class (unlike providers/nlsc_code_cache.py's NlscCodeCache) -- its
`__init__` unconditionally calls `os.makedirs(...)` + `sqlite3.connect(...)`
+ `_init_schema()` (CREATE TABLE IF NOT EXISTS) every time it is
constructed, including against a path that does not yet exist. This
module does not claim otherwise, and does not construct
`CadastralDatasetCache` at all -- Real providers (`RealExpropriationCase
Provider`/`RealLandPriceProvider`, both frozen, both unmodified) continue
to be the ONLY code that ever opens or queries the cadastral snapshot;
this module's only job is making sure a byte-for-byte VERIFIED copy is at
`CADASTRAL_DATASET_CACHE_DB_PATH` before they run -- "Runtime Real
providers only ever query the cadastral snapshot; bootstrap never touches
or modifies the frozen query semantics."

THE CORE SAFETY INVARIANT (Phase DEPLOY-1E design correction, §2 of this
round's approval): at the end of `ensure_cadastral_snapshot()`, the file
at `CADASTRAL_DATASET_CACHE_DB_PATH` is in EXACTLY one of two states --
(a) verified correct (its SHA-256 matches `CADASTRAL_SNAPSHOT_SHA256`), or
(b) ABSENT. There is no third state where a stale or checksum-mismatched
file sits at that path. This is enforced structurally, not by a status
flag the caller might forget to check: the moment an existing file at the
target path is found to have the WRONG checksum, it is deleted
immediately -- before any download is even attempted -- and a
subsequently failed download (network error, S3 error, or a mismatched
checksum on the NEW download) never writes anything to the target path at
all (downloads always land in a same-directory temp file first, promoted
via `os.replace()` only after its own checksum is verified). Consequently,
if this module ever returns DATASET_UNAVAILABLE, `CADASTRAL_DATASET_CACHE_
DB_PATH` is guaranteed to be absent -- so when the (frozen, unmodified)
`CadastralDatasetCache()` is constructed afterward, it deterministically
takes the SAME "never synced" path this round's audit already confirmed
(STEP 2: `except CadastralDatasetCacheError` in both `RealExpropriationCase
Provider.query_case()` and `RealLandPriceProvider.query_land_price()`,
producing `status=...UNKNOWN`/`announced_land_current_value_status=
LandPriceFieldStatus.UNKNOWN`, both `requires_manual_review=True`) --
never a stale FOUND/NOT_FOUND result derived from data already proven (by
its own checksum) to not match the published release.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Optional

ENV_S3_BUCKET = "CADASTRAL_SNAPSHOT_S3_BUCKET"
ENV_S3_KEY = "CADASTRAL_SNAPSHOT_S3_KEY"
ENV_SHA256 = "CADASTRAL_SNAPSHOT_SHA256"
ENV_DB_PATH = "CADASTRAL_DATASET_CACHE_DB_PATH"

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB streamed reads -- never loads 407MB into memory at once


class SnapshotBootstrapStatus(str, Enum):
    ALREADY_PRESENT = "ALREADY_PRESENT"          # warm reuse: existing file's checksum already matched
    DOWNLOADED = "DOWNLOADED"                     # cold start: fresh download, verified, atomically published
    DATASET_UNAVAILABLE = "DATASET_UNAVAILABLE"   # config missing / download failed / checksum never verified


@dataclass
class SnapshotBootstrapResult:
    status: SnapshotBootstrapStatus
    local_path: Optional[str] = None  # set ONLY for ALREADY_PRESENT / DOWNLOADED
    notes: Optional[str] = None


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


def ensure_cadastral_snapshot(*, s3_client=None) -> SnapshotBootstrapResult:
    """Idempotent; safe to call on every invocation (warm containers skip
    the download entirely once a verified copy already sits at the target
    path -- see module docstring's core safety invariant). `s3_client` is
    DI-injectable for tests (never constructs a real `boto3.client("s3")`
    itself unless one isn't supplied, matching this codebase's established
    Real-provider DI convention).

    Returns DATASET_UNAVAILABLE (never raises) for: missing configuration,
    any S3/network error, or a checksum mismatch on the downloaded file --
    matching this codebase's "no provider crashes the pipeline" convention
    for the ONE thing this module is actually responsible for (moving a
    file), while leaving all valuation-relevant degraded-state semantics
    (UNKNOWN/requires_manual_review) entirely to the frozen Real providers
    downstream, which this module never touches."""
    bucket = os.environ.get(ENV_S3_BUCKET)
    key = os.environ.get(ENV_S3_KEY)
    expected_sha256 = os.environ.get(ENV_SHA256)
    final_path = os.environ.get(ENV_DB_PATH)

    if not (bucket and key and expected_sha256 and final_path):
        return SnapshotBootstrapResult(
            status=SnapshotBootstrapStatus.DATASET_UNAVAILABLE,
            notes=(
                f"缺少必要環境變數（{ENV_S3_BUCKET}/{ENV_S3_KEY}/{ENV_SHA256}/{ENV_DB_PATH}其中之一或多個未設定），"
                "無法判斷snapshot來源，不嘗試下載。"
            ),
        )

    # --- Warm reuse / stale-detection (Phase DEPLOY-1E §2 correction) ---
    # An existing file at final_path is used ONLY if its checksum matches
    # RIGHT NOW -- never assumed valid just because it exists. A mismatch
    # means this exact file has already been proven wrong; it is deleted
    # immediately, before any download is attempted, so no code path can
    # ever fall through to "network failed, so just leave the old file in
    # place" (Phase DEPLOY-1E's explicitly forbidden stale-snapshot
    # fallback).
    if os.path.isfile(final_path):
        try:
            if _sha256_of_file(final_path) == expected_sha256:
                return SnapshotBootstrapResult(
                    status=SnapshotBootstrapStatus.ALREADY_PRESENT, local_path=final_path,
                    notes="既有本地snapshot checksum相符，直接reuse，未觸發下載。",
                )
        except OSError:
            pass  # unreadable existing file -- treated the same as a checksum mismatch below
        _remove_if_exists(final_path)

    # --- Download to a same-directory temp file, verify, THEN publish ---
    # Same directory as final_path is required for os.replace() to be
    # atomic (atomic rename is only guaranteed within one filesystem).
    target_dir = os.path.dirname(final_path) or "."
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        return SnapshotBootstrapResult(
            status=SnapshotBootstrapStatus.DATASET_UNAVAILABLE,
            notes=f"無法建立snapshot目標目錄{target_dir!r}：{e}",
        )

    temp_fd, temp_path = tempfile.mkstemp(prefix=".cadastral_snapshot_download_", dir=target_dir)
    os.close(temp_fd)
    try:
        if s3_client is None:
            import boto3
            s3_client = boto3.client("s3")
        try:
            s3_client.download_file(bucket, key, temp_path)
        except Exception as e:  # noqa: BLE001 -- any S3/network failure degrades the same way
            return SnapshotBootstrapResult(
                status=SnapshotBootstrapStatus.DATASET_UNAVAILABLE,
                notes=f"從S3下載snapshot失敗（bucket={bucket!r}, key={key!r}）：{e}",
            )

        try:
            actual_sha256 = _sha256_of_file(temp_path)
        except OSError as e:
            return SnapshotBootstrapResult(
                status=SnapshotBootstrapStatus.DATASET_UNAVAILABLE,
                notes=f"下載完成後之checksum驗證讀取失敗：{e}",
            )

        if actual_sha256 != expected_sha256:
            return SnapshotBootstrapResult(
                status=SnapshotBootstrapStatus.DATASET_UNAVAILABLE,
                notes=(
                    f"下載完成但SHA-256不符（expected={expected_sha256}, actual={actual_sha256}），"
                    "拒絕發布，canonical路徑維持不存在，絕不使用此檔案。"
                ),
            )

        # Verified -- publish atomically. final_path is guaranteed absent
        # at this point (removed above if it was stale, or never existed).
        os.replace(temp_path, final_path)
        return SnapshotBootstrapResult(
            status=SnapshotBootstrapStatus.DOWNLOADED, local_path=final_path,
            notes=f"下載並驗證成功（SHA-256={actual_sha256}），已atomic發布至{final_path}。",
        )
    finally:
        # Never leaves a partial/orphaned temp file behind, whether the
        # function returned via the success path (already renamed away,
        # so this is a harmless no-op) or any failure path above.
        _remove_if_exists(temp_path)
