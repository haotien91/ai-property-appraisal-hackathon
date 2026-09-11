# -*- coding: utf-8 -*-
"""
Phase API-2.3F §7/§9/§10/§13: EngineLayer Packaging Acceptance.

`make`/`sam` are not available in every dev environment this repo runs
in, so this test replicates infra/layers/engine/Makefile's `build-
EngineLayer` target's COPY STEPS in pure Python (same source paths, same
optional-sqlite guard) into a tmp_path, then runs a real smoke test
against THAT packaged artifact -- never against the raw repo `data/`
path -- to prove:

1. `python/data/nlsc_code_cache.sqlite3` is actually present in the
   artifact when the repo's own data/nlsc_code_cache.sqlite3 exists
   (skipped, not faked, if a fresh checkout hasn't run
   scripts/sync_nlsc_codes.py yet -- see the module-level skip below).
2. A read-only sqlite3 connection against the PACKAGED file can resolve
   the Golden Case (新北市/金山區/金美段 -> F/F25/1027/office=FD,
   489 -> 04890000) end-to-end through NlscCadastralCodeResolver, using
   DEFAULT_DB_PATH's own path arithmetic (not a hand-picked path) so this
   test would fail if that arithmetic ever stopped resolving into the
   Layer's expected /opt/python/data/ location.
3. A write attempt against the packaged file fails (both via this
   codebase's own NlscCodeCache read_only=True guard AND via a raw
   sqlite3 `mode=ro` connection, proving SQLite itself enforces it, not
   merely this class's convention).

If this test is run from a source tree copied/zipped elsewhere (so
`sys.path` tricks are needed to import providers/ as if it were
`/opt/python/providers/`), that's intentional -- it's exactly what
happens inside a real deployed Lambda.
"""
import importlib
import os
import shutil
import sqlite3
import sys

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
REPO_SQLITE_PATH = os.path.join(REPO_ROOT, "data", "nlsc_code_cache.sqlite3")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(REPO_SQLITE_PATH),
    reason="data/nlsc_code_cache.sqlite3 尚未由 scripts/sync_nlsc_codes.py 產生，無法驗證packaging",
)


def _build_engine_layer_artifact(tmp_path) -> str:
    """Mirrors infra/layers/engine/Makefile's build-EngineLayer target's
    copy steps exactly (domain/engine/providers/schemas + data/rules +
    data/dependency_graph.json + the OPTIONAL data/nlsc_code_cache.sqlite3
    -- same `if exists` guard, same source paths). Does NOT invoke
    `make`/`sam` -- this environment may not have them installed; the
    copy semantics themselves are what's under test, replicated in pure
    Python so the test is portable."""
    artifacts_dir = tmp_path / "artifacts"
    python_dir = artifacts_dir / "python"
    python_dir.mkdir(parents=True)

    for name in ("domain", "engine", "providers", "schemas"):
        shutil.copytree(os.path.join(REPO_ROOT, name), str(python_dir / name),
                         ignore=shutil.ignore_patterns("__pycache__"))

    data_dir = python_dir / "data"
    data_dir.mkdir()
    shutil.copytree(os.path.join(REPO_ROOT, "data", "rules"), str(data_dir / "rules"))
    shutil.copy(os.path.join(REPO_ROOT, "data", "dependency_graph.json"), str(data_dir / "dependency_graph.json"))
    if os.path.isfile(REPO_SQLITE_PATH):
        shutil.copy(REPO_SQLITE_PATH, str(data_dir / "nlsc_code_cache.sqlite3"))

    return str(python_dir)


def test_build_artifact_contains_sqlite_cache(tmp_path):
    python_dir = _build_engine_layer_artifact(tmp_path)
    packaged_sqlite = os.path.join(python_dir, "data", "nlsc_code_cache.sqlite3")
    assert os.path.isfile(packaged_sqlite)
    assert os.path.getsize(packaged_sqlite) > 0


_ISOLATED_MODULE_NAMES = ("nlsc_code_cache", "nlsc_cadastral_code_resolver", "nlsc_land_number_encoder")


def _import_fresh_from_packaged(python_dir, monkeypatch):
    """Imports the three path-sensitive NLSC modules FRESH from the
    packaged artifact, never from whichever copy an earlier test file in
    this same pytest session already imported under the same module name.

    `importlib.reload()` would NOT achieve this: reload() re-executes a
    module from its OWN existing `__spec__.origin` (wherever it was FIRST
    imported from), ignoring sys.path changes made afterwards -- so a
    naive `sys.path.insert(...); importlib.reload(nlsc_code_cache)` here
    would silently keep re-running the ORIGINAL repo copy, while ALSO
    leaving `sys.modules["nlsc_code_cache"]` mutated for the rest of the
    test session (every later test file's `from nlsc_code_cache import
    ...` would then just return this already-cached module object,
    corrupting unrelated tests -- exactly the bug this helper avoids).

    Fix: delete any existing sys.modules entries for these names via
    `monkeypatch.delitem` (auto-restored to their PRE-test value when this
    test ends, whatever that was, even though the import machinery
    reassigns the key mid-test) so the subsequent `import_module` call is
    forced to re-search sys.path (now pointing at the packaged copy)
    instead of returning a cached module."""
    for name in _ISOLATED_MODULE_NAMES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(python_dir)
    monkeypatch.syspath_prepend(os.path.join(python_dir, "providers"))
    return {name: importlib.import_module(name) for name in _ISOLATED_MODULE_NAMES}


def test_packaged_sqlite_read_only_golden_resolution(tmp_path, monkeypatch):
    python_dir = _build_engine_layer_artifact(tmp_path)

    # Simulate the Lambda import path: providers/ imported from
    # <layer_root>/providers/, exactly as /opt/python/providers/ would be.
    mods = _import_fresh_from_packaged(python_dir, monkeypatch)
    nlsc_code_cache = mods["nlsc_code_cache"]
    nlsc_cadastral_code_resolver = mods["nlsc_cadastral_code_resolver"]
    nlsc_land_number_encoder = mods["nlsc_land_number_encoder"]

    # DEFAULT_DB_PATH's own arithmetic must resolve INTO the packaged tree
    # (not the original repo path) -- this is the actual proof that
    # packaging didn't just happen to work because the test cheated with a
    # hand-picked path.
    expected = os.path.join(python_dir, "data", "nlsc_code_cache.sqlite3")
    assert os.path.abspath(nlsc_code_cache.DEFAULT_DB_PATH) == os.path.abspath(expected)

    cache = nlsc_code_cache.NlscCodeCache(read_only=True)
    resolver = nlsc_cadastral_code_resolver.NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "金山區", "金美段")
    assert ev.city_code == "F"
    assert ev.town_code == "F25"
    assert ev.section_code == "1027"
    assert ev.office == "FD"
    assert nlsc_land_number_encoder.NlscLandNumberEncoder.encode(489, 0) == "04890000"


def test_packaged_sqlite_rejects_write_both_layers(tmp_path, monkeypatch):
    python_dir = _build_engine_layer_artifact(tmp_path)
    mods = _import_fresh_from_packaged(python_dir, monkeypatch)
    nlsc_code_cache = mods["nlsc_code_cache"]

    cache = nlsc_code_cache.NlscCodeCache(read_only=True)
    with pytest.raises(nlsc_code_cache.NlscCodeCacheError):
        cache.begin_staging_run("counties")

    # Raw SQLite level, independent of this codebase's own guard.
    packaged_sqlite = os.path.join(python_dir, "data", "nlsc_code_cache.sqlite3")
    conn = sqlite3.connect(f"file:{packaged_sqlite}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO counties (scope_key, county_code, county_name) VALUES ('x','ZZ','test')")
            conn.commit()
        # SELECT still works.
        row = conn.execute("SELECT county_name FROM counties WHERE county_code='F'").fetchone()
        assert row is not None
    finally:
        conn.close()
