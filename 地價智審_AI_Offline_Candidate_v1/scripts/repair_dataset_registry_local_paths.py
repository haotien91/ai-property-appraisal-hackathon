# -*- coding: utf-8 -*-
"""
repair_dataset_registry_local_paths.py — STEP5 FINAL GATE 2 §A/§C/§D.

ROOT CAUSE (DATASET_REGISTRY_PATH_WRONG, not RUNTIME_DATASET_MISSING):
data/dataset_registry.sqlite3 stores each dataset's `local_path` as an
ABSOLUTE path, baked in at the moment scripts/sync_ntpc_zoning_dataset.py
last ran (on a different machine/directory layout -- e.g.
"D:\\黑客松影片\\地價智審_AI_Offline_Candidate\\...\\data\\snapshots\\
ntpc_plan_boundary_2026-09-08.sqlite3"). When this project directory is
copied to a new location (a new drive letter, a new parent folder name --
exactly what happened for this "_v12" copy), the real snapshot files ARE
present, unmodified, at the CORRECT location relative to the new repo
root (verified: SHA-256 checksum matches the registry's own recorded
value exactly, and `PRAGMA integrity_check` returns "ok" for both) --
only the registry's stored absolute path is stale, so
DatasetRegistry.check_staleness()'s `os.path.exists(snapshot.local_path)`
check correctly (and safely -- see providers/urban_plan_boundary_
provider.py's own "never fabricate INSIDE" contract) reports UNAVAILABLE,
which the Provider correctly turns into urban_plan_status=UNKNOWN rather
than guessing.

This is a data-repair operation, not a Provider/engine code change and
not a re-sync: it does NOT touch providers/urban_plan_boundary_provider.py,
providers/ntpc_zoning_provider.py, providers/dataset_registry.py, or any
engine/ file. It reuses DatasetRegistry.register_snapshot() (the SAME
upsert API scripts/sync_ntpc_zoning_dataset.py itself uses) to rewrite
ONLY the `local_path` field for whichever registered dataset's checksum-
verified snapshot file has moved relative to what the registry currently
records -- every other field (checksum, record_count, last_synced_at,
notes, ...) is carried over UNCHANGED from the existing row.

Safety: for each dataset_id, this script ONLY updates local_path if
(a) a file exists at the NEW candidate path under data/snapshots/, AND
(b) that file's SHA-256 checksum matches the registry's ALREADY-recorded
checksum for that dataset_id exactly. If either check fails, that
dataset_id is left untouched and reported as unresolved -- this script
never fabricates a checksum, never accepts a mismatched file, and never
points local_path at a file that doesn't exist.

Usage: py scripts/repair_dataset_registry_local_paths.py [--dry-run]
"""
from __future__ import annotations

import hashlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from dataset_registry import DatasetRegistry  # noqa: E402


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _candidate_path_for(local_snapshot_version: str, dataset_id: str) -> str:
    """The snapshot naming convention scripts/sync_ntpc_zoning_dataset.py
    uses: data/snapshots/<dataset_id>_<local_snapshot_version>.sqlite3."""
    return os.path.join(REPO_ROOT, "data", "snapshots", f"{dataset_id}_{local_snapshot_version}.sqlite3")


def main(dry_run: bool = False) -> int:
    registry = DatasetRegistry()
    with registry._connect() as conn:  # noqa: SLF001 -- read-only listing, not a new public API surface
        rows = conn.execute("SELECT dataset_id, local_path, local_snapshot_version, checksum FROM dataset_snapshots").fetchall()

    exit_code = 0
    for row in rows:
        dataset_id = row["dataset_id"]
        old_path = row["local_path"]
        registered_checksum = row["checksum"]

        if os.path.exists(old_path):
            print(f"[OK] {dataset_id}: local_path already valid on this machine ({old_path})")
            continue

        candidate = _candidate_path_for(row["local_snapshot_version"], dataset_id)
        if not os.path.exists(candidate):
            print(f"[UNRESOLVED] {dataset_id}: registered local_path missing "
                  f"({old_path!r}), and no file found at the expected naming-"
                  f"convention path either ({candidate!r}). NOT repaired.")
            exit_code = 1
            continue

        actual_checksum = _sha256(candidate)
        if actual_checksum != registered_checksum:
            print(f"[CHECKSUM_MISMATCH] {dataset_id}: candidate file {candidate!r} "
                  f"exists but its SHA-256 ({actual_checksum}) does NOT match the "
                  f"registry's recorded checksum ({registered_checksum}). NOT repaired "
                  f"-- refusing to accept an unverified file.")
            exit_code = 1
            continue

        print(f"[REPAIR] {dataset_id}: local_path {old_path!r} -> {candidate!r} "
              f"(checksum verified: {actual_checksum})")
        if not dry_run:
            snapshot = registry.get_current_snapshot(dataset_id)
            snapshot.local_path = candidate
            registry.register_snapshot(snapshot)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(dry_run="--dry-run" in sys.argv))
