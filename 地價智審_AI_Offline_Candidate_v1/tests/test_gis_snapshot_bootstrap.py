# -*- coding: utf-8 -*-
"""Tests for backend/handlers/gis_snapshot_bootstrap.py -- the S3-to-/tmp
bootstrap for the two large NTPC GIS snapshots (新北市使用分區 /
新北市都市計畫範圍), mirroring backend/handlers/cadastral_snapshot_
bootstrap.py's proven safety invariants (temp-file-then-atomic-replace,
checksum-verified, never stale/partial). See that module's own test file
(tests/test_cadastral_snapshot_bootstrap.py) for the precedent this
mirrors; see docs/backlog.md's URBAN_PLAN_BOUNDARY_SNAPSHOT_NOT_PACKAGED
entry for why this module exists at all.

None of these tests need real AWS credentials -- a small `_FakeS3Client`
test double stands in for S3 throughout, same convention as the cadastral
test file.
"""
import hashlib
import os
import sqlite3
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

import gis_snapshot_bootstrap as bootstrap  # noqa: E402
from gis_snapshot_bootstrap import (  # noqa: E402
    ensure_ntpc_zoning_snapshot, ensure_ntpc_plan_boundary_snapshot, ensure_gis_snapshots,
    SnapshotBootstrapStatus, ZONING_CONFIG, PLAN_BOUNDARY_CONFIG, ENV_RUNTIME_REGISTRY_PATH,
)
from dataset_registry import DatasetRegistry  # noqa: E402


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _FakeS3Client:
    """Test double for boto3's S3 client -- only implements the one method
    this module actually calls. Content/errors are keyed by (bucket, key)
    so a single client instance can serve BOTH datasets differently in the
    partial-failure tests."""

    def __init__(self):
        self.contents = {}       # (bucket, key) -> bytes
        self.errors = {}         # (bucket, key) -> Exception
        self.calls = []

    def set_content(self, bucket, key, content: bytes):
        self.contents[(bucket, key)] = content

    def set_error(self, bucket, key, error: Exception):
        self.errors[(bucket, key)] = error

    def download_file(self, bucket, key, local_path):
        self.calls.append((bucket, key, local_path))
        if (bucket, key) in self.errors:
            raise self.errors[(bucket, key)]
        with open(local_path, "wb") as f:
            f.write(self.contents[(bucket, key)])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    env_vars = [
        ZONING_CONFIG.env_bucket, ZONING_CONFIG.env_key, ZONING_CONFIG.env_sha256, ZONING_CONFIG.env_runtime_path,
        PLAN_BOUNDARY_CONFIG.env_bucket, PLAN_BOUNDARY_CONFIG.env_key,
        PLAN_BOUNDARY_CONFIG.env_sha256, PLAN_BOUNDARY_CONFIG.env_runtime_path,
        ENV_RUNTIME_REGISTRY_PATH, "LAMBDA_TASK_ROOT",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)
    bootstrap._WARM_VERIFIED.clear()
    yield
    bootstrap._WARM_VERIFIED.clear()


def _set_zoning_env(monkeypatch, runtime_path, *, bucket="test-bucket",
                     key="datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", sha256=None):
    monkeypatch.setenv(ZONING_CONFIG.env_bucket, bucket)
    monkeypatch.setenv(ZONING_CONFIG.env_key, key)
    monkeypatch.setenv(ZONING_CONFIG.env_runtime_path, runtime_path)
    if sha256 is not None:
        monkeypatch.setenv(ZONING_CONFIG.env_sha256, sha256)


def _set_boundary_env(monkeypatch, runtime_path, *, bucket="test-bucket",
                       key="datasets/ntpc-gis/v2026-09-08/ntpc_plan_boundary.sqlite3", sha256=None):
    monkeypatch.setenv(PLAN_BOUNDARY_CONFIG.env_bucket, bucket)
    monkeypatch.setenv(PLAN_BOUNDARY_CONFIG.env_key, key)
    monkeypatch.setenv(PLAN_BOUNDARY_CONFIG.env_runtime_path, runtime_path)
    if sha256 is not None:
        monkeypatch.setenv(PLAN_BOUNDARY_CONFIG.env_sha256, sha256)


# -- 1: cold S3 bootstrap success -------------------------------------------

def test_cold_bootstrap_success_downloads_and_verifies(tmp_path, monkeypatch):
    content = b"fake-zoning-sqlite-bytes"
    runtime_path = str(tmp_path / "ntpc_zoning.sqlite3")
    _set_zoning_env(monkeypatch, runtime_path, sha256=_sha256(content))
    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", content)

    result = ensure_ntpc_zoning_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DOWNLOADED
    assert result.local_path == runtime_path
    assert result.available is True
    assert result.download_ms is not None and result.checksum_ms is not None
    assert result.snapshot_bytes == len(content)
    with open(runtime_path, "rb") as f:
        assert f.read() == content


# -- 2: warm start does not re-download or re-hash --------------------------

def test_warm_start_reuses_without_rehashing(tmp_path, monkeypatch):
    content = b"fake-zoning-sqlite-bytes-warm"
    runtime_path = str(tmp_path / "ntpc_zoning.sqlite3")
    _set_zoning_env(monkeypatch, runtime_path, sha256=_sha256(content))
    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", content)

    r1 = ensure_ntpc_zoning_snapshot(s3_client=client)
    assert r1.status == SnapshotBootstrapStatus.DOWNLOADED
    assert len(client.calls) == 1

    r2 = ensure_ntpc_zoning_snapshot(s3_client=client)
    r3 = ensure_ntpc_zoning_snapshot(s3_client=client)

    assert r2.status == SnapshotBootstrapStatus.ALREADY_PRESENT
    assert r3.status == SnapshotBootstrapStatus.ALREADY_PRESENT
    assert len(client.calls) == 1  # no re-download across warm calls
    assert r2.local_path == runtime_path


# -- 3: SHA mismatch ----------------------------------------------------------

def test_checksum_mismatch_rejected_not_published(tmp_path, monkeypatch):
    downloaded = b"wrong-content"
    runtime_path = str(tmp_path / "ntpc_zoning.sqlite3")
    _set_zoning_env(monkeypatch, runtime_path, sha256=_sha256(b"completely-different-expected"))
    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", downloaded)

    result = ensure_ntpc_zoning_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.CHECKSUM_MISMATCH
    assert result.available is False
    assert not os.path.exists(runtime_path)
    assert os.listdir(tmp_path) == []  # temp file cleaned up


# -- 4: S3 missing object / unreachable --------------------------------------

def test_s3_error_is_unavailable_not_raised(tmp_path, monkeypatch):
    runtime_path = str(tmp_path / "ntpc_zoning.sqlite3")
    _set_zoning_env(monkeypatch, runtime_path, sha256=_sha256(b"expected-content-never-arrives"))
    client = _FakeS3Client()
    client.set_error("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3",
                      ConnectionError("simulated: object not found / S3 unreachable"))

    result = ensure_ntpc_zoning_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.S3_UNAVAILABLE
    assert result.available is False
    assert not os.path.exists(runtime_path)
    assert os.listdir(tmp_path) == []


# -- 5: atomic replacement of a stale existing file --------------------------

def test_stale_existing_snapshot_atomically_replaced(tmp_path, monkeypatch):
    stale_content = b"old-stale-zoning-content"
    new_content = b"new-verified-zoning-content"
    runtime_path = str(tmp_path / "ntpc_zoning.sqlite3")
    with open(runtime_path, "wb") as f:
        f.write(stale_content)
    _set_zoning_env(monkeypatch, runtime_path, sha256=_sha256(new_content))
    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", new_content)

    result = ensure_ntpc_zoning_snapshot(s3_client=client)

    assert result.status == SnapshotBootstrapStatus.DOWNLOADED
    assert len(client.calls) == 1
    with open(runtime_path, "rb") as f:
        assert f.read() == new_content


# -- 6: runtime registry local_path is a verbatim passthrough of the
# configured runtime path -- NEVER derived from, or re-parsed out of, some
# OTHER (potentially dev-machine, potentially Windows-backslash) recorded
# path the way the earlier (superseded) infra/layers/engine/rewrite_
# dataset_registry_paths.py approach had to (see that module's own
# docstring/tests for the bug this design sidesteps entirely: this module
# never reads a pre-existing "packaged registry" with someone else's
# recorded local_path at all, so there is nothing to mis-parse). In real
# AWS deployment NTPC_ZONING_RUNTIME_PATH is always a POSIX /tmp/... value
# (see infra/template.yaml) purely because that is what is configured
# there -- not because this module does any OS-specific handling.

def test_runtime_registry_local_path_is_verbatim_configured_runtime_path(tmp_path, monkeypatch):
    content = b"fake-zoning-bytes-registry-check"
    runtime_path = str(tmp_path / "ntpc_zoning.sqlite3")
    registry_path = str(tmp_path / "runtime_dataset_registry.sqlite3")
    monkeypatch.setenv("LAMBDA_TASK_ROOT", str(tmp_path))  # simulate Lambda
    monkeypatch.setenv(ENV_RUNTIME_REGISTRY_PATH, registry_path)
    _set_zoning_env(monkeypatch, runtime_path, sha256=_sha256(content))
    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", content)

    ensure_gis_snapshots(s3_client=client)

    registry = DatasetRegistry(db_path=registry_path)
    snapshot = registry.get_current_snapshot("ntpc_zoning")
    assert snapshot is not None
    # Exactly what NTPC_ZONING_RUNTIME_PATH was set to -- byte-for-byte,
    # no basename-extraction/reconstruction of any kind.
    assert snapshot.local_path == runtime_path
    assert os.path.exists(snapshot.local_path)  # and it's genuinely readable at that path
    assert snapshot.local_snapshot_version == "v2026-09-09"


# -- 7/8: partial failure -- each dataset independent ------------------------

def test_zoning_success_boundary_failure_is_partial_not_all_or_nothing(tmp_path, monkeypatch):
    zoning_content = b"good-zoning-content"
    monkeypatch.setenv("LAMBDA_TASK_ROOT", str(tmp_path))
    monkeypatch.setenv(ENV_RUNTIME_REGISTRY_PATH, str(tmp_path / "registry.sqlite3"))
    _set_zoning_env(monkeypatch, str(tmp_path / "ntpc_zoning.sqlite3"), sha256=_sha256(zoning_content))
    _set_boundary_env(monkeypatch, str(tmp_path / "ntpc_plan_boundary.sqlite3"))  # no sha256 -> MISSING_CONFIG

    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", zoning_content)

    results = ensure_gis_snapshots(s3_client=client)

    assert results["ntpc_zoning"].status == SnapshotBootstrapStatus.DOWNLOADED
    assert results["ntpc_plan_boundary"].status == SnapshotBootstrapStatus.MISSING_CONFIG

    registry = DatasetRegistry(db_path=str(tmp_path / "registry.sqlite3"))
    assert registry.get_current_snapshot("ntpc_zoning") is not None
    assert registry.get_current_snapshot("ntpc_plan_boundary") is None  # never registered


def test_boundary_success_zoning_failure_is_partial_not_all_or_nothing(tmp_path, monkeypatch):
    boundary_content = b"good-boundary-content"
    monkeypatch.setenv("LAMBDA_TASK_ROOT", str(tmp_path))
    monkeypatch.setenv(ENV_RUNTIME_REGISTRY_PATH, str(tmp_path / "registry.sqlite3"))
    _set_zoning_env(monkeypatch, str(tmp_path / "ntpc_zoning.sqlite3"))  # no sha256 -> MISSING_CONFIG
    _set_boundary_env(monkeypatch, str(tmp_path / "ntpc_plan_boundary.sqlite3"), sha256=_sha256(boundary_content))

    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-08/ntpc_plan_boundary.sqlite3", boundary_content)

    results = ensure_gis_snapshots(s3_client=client)

    assert results["ntpc_plan_boundary"].status == SnapshotBootstrapStatus.DOWNLOADED
    assert results["ntpc_zoning"].status == SnapshotBootstrapStatus.MISSING_CONFIG

    registry = DatasetRegistry(db_path=str(tmp_path / "registry.sqlite3"))
    assert registry.get_current_snapshot("ntpc_plan_boundary") is not None
    assert registry.get_current_snapshot("ntpc_zoning") is None


# -- 9: local/offline mode never touches S3 ----------------------------------

def test_local_mode_never_calls_s3_even_if_fully_configured(tmp_path, monkeypatch):
    # LAMBDA_TASK_ROOT deliberately NOT set -- this is the local-dev signal.
    content = b"should-never-be-downloaded-in-local-mode"
    _set_zoning_env(monkeypatch, str(tmp_path / "ntpc_zoning.sqlite3"), sha256=_sha256(content))
    _set_boundary_env(monkeypatch, str(tmp_path / "ntpc_plan_boundary.sqlite3"), sha256=_sha256(content))
    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", content)
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-08/ntpc_plan_boundary.sqlite3", content)

    results = ensure_gis_snapshots(s3_client=client)

    assert results == {}
    assert client.calls == []


# -- 10/11: no Mock/Golden fallback concept, structurally --------------------

def test_missing_configuration_is_missing_config_never_raises():
    result = ensure_ntpc_zoning_snapshot(s3_client=_FakeS3Client())
    assert result.status == SnapshotBootstrapStatus.MISSING_CONFIG
    assert result.local_path is None
    assert result.available is False


def test_module_has_no_mock_or_golden_fallback_concept():
    import inspect
    source = inspect.getsource(bootstrap)
    scrubbed = (
        source.replace("Real -> Mock", "").replace("Real -> Golden", "")
        .replace("fabricates data", "").replace("never fabricates", "")
        .replace("fake/\nplaceholder", "").replace("placeholder snapshot", "")
    )
    assert "Mock" not in scrubbed
    assert "Golden" not in scrubbed


# -- 12: Golden runtime E2E -- plan_id/zone_name genuinely computed ---------

def test_golden_case_e2e_through_bootstrapped_runtime_registry(tmp_path, monkeypatch):
    """Proves the FULL chain (S3 bootstrap -> runtime registry -> real
    provider query) using synthetic-but-realistic sqlite snapshots shaped
    exactly like scripts/sync_ntpc_zoning_dataset.py's real output --
    plan_id/zone_name/240% are never hardcoded anywhere in this test's
    assertions' inputs, only in what the bootstrapped chain produces."""
    from shapely import wkb as shapely_wkb
    from shapely.geometry import Polygon

    zone_ring = [(121.0, 25.0), (121.0, 25.1), (121.1, 25.1), (121.1, 25.0)]

    zoning_db_bytes_path = tmp_path / "_build_zoning.sqlite3"
    conn = sqlite3.connect(str(zoning_db_bytes_path))
    conn.execute("""CREATE TABLE zoning_polygons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, zone_name TEXT NOT NULL, plan_name TEXT,
        plan_name_note TEXT, geometry_wkb BLOB NOT NULL,
        min_lon REAL NOT NULL, max_lon REAL NOT NULL, min_lat REAL NOT NULL, max_lat REAL NOT NULL
    )""")
    conn.execute(
        "INSERT INTO zoning_polygons (zone_name, plan_name, plan_name_note, geometry_wkb, "
        "min_lon, max_lon, min_lat, max_lat) VALUES (?, ?, NULL, ?, 121.0, 121.1, 25.0, 25.1)",
        ("第二種商業區", "金山都市計畫", shapely_wkb.dumps(Polygon(zone_ring))),
    )
    conn.commit()
    conn.close()
    zoning_content = zoning_db_bytes_path.read_bytes()

    boundary_db_bytes_path = tmp_path / "_build_boundary.sqlite3"
    conn = sqlite3.connect(str(boundary_db_bytes_path))
    conn.execute("""CREATE TABLE plan_boundary_polygons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL, source_sdf_id INTEGER NOT NULL,
        plan_id TEXT, plan_name TEXT, mapping_found INTEGER NOT NULL, geometry_wkb BLOB NOT NULL,
        min_lon REAL NOT NULL, max_lon REAL NOT NULL, min_lat REAL NOT NULL, max_lat REAL NOT NULL
    )""")
    conn.execute(
        "INSERT INTO plan_boundary_polygons (source_key, source_sdf_id, plan_id, plan_name, mapping_found, "
        "geometry_wkb, min_lon, max_lon, min_lat, max_lat) VALUES ('64', 28, 'jinshan', '金山都市計畫', 1, ?, "
        "121.0, 121.1, 25.0, 25.1)",
        (shapely_wkb.dumps(Polygon(zone_ring)),),
    )
    conn.commit()
    conn.close()
    boundary_content = boundary_db_bytes_path.read_bytes()

    monkeypatch.setenv("LAMBDA_TASK_ROOT", str(tmp_path))
    monkeypatch.setenv(ENV_RUNTIME_REGISTRY_PATH, str(tmp_path / "runtime_registry.sqlite3"))
    _set_zoning_env(monkeypatch, str(tmp_path / "rt_ntpc_zoning.sqlite3"), sha256=_sha256(zoning_content))
    _set_boundary_env(monkeypatch, str(tmp_path / "rt_ntpc_plan_boundary.sqlite3"), sha256=_sha256(boundary_content))

    client = _FakeS3Client()
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3", zoning_content)
    client.set_content("test-bucket", "datasets/ntpc-gis/v2026-09-08/ntpc_plan_boundary.sqlite3", boundary_content)

    results = ensure_gis_snapshots(s3_client=client)
    assert results["ntpc_zoning"].available and results["ntpc_plan_boundary"].available

    from domain.models import Coordinate, UrbanPlanStatus
    from ntpc_zoning_provider import RealNtpcZoningProvider
    from urban_plan_boundary_provider import RealUrbanPlanBoundaryProvider

    runtime_registry = DatasetRegistry(db_path=str(tmp_path / "runtime_registry.sqlite3"))
    coord = Coordinate(latitude=25.05, longitude=121.05)

    zoning_result = RealNtpcZoningProvider(registry=runtime_registry).query(coord)
    urban_plan_result = RealUrbanPlanBoundaryProvider(registry=runtime_registry).resolve_urban_plan(coord)

    assert zoning_result.zone_name == "第二種商業區"
    assert urban_plan_result.urban_plan_status == UrbanPlanStatus.INSIDE
    assert urban_plan_result.plan_id == "jinshan"

    from engine.land_use_ratio_engine import LandUseRatioEngine
    far = LandUseRatioEngine().resolve_floor_area_ratio(zoning_result.zone_name, plan_id=urban_plan_result.plan_id)
    assert far.resolved_value_pct == 240
    assert far.resolution_layer == "PLAN_SPECIFIC"
