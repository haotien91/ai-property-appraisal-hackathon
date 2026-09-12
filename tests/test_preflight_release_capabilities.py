# -*- coding: utf-8 -*-
"""
Phase API-2.3F §8/§13: tests for scripts/preflight_release_capabilities.py
-- a release build that omits data/nlsc_code_cache.sqlite3 while claiming
NLSC_CODE_RESOLVER_AVAILABLE=YES must fail preflight, never silently ship.
"""
import json
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from preflight_release_capabilities import compute_capabilities, main  # noqa: E402


def _make_artifact_tree(tmp_path, *, with_sqlite: bool):
    root = tmp_path / "artifact"
    (root / "python" / "data").mkdir(parents=True)
    (root / "python" / "data" / "dependency_graph.json").write_text("{}", encoding="utf-8")
    if with_sqlite:
        (root / "python" / "data" / "nlsc_code_cache.sqlite3").write_bytes(b"fake-sqlite-bytes")
    return str(root)


def test_capability_yes_when_sqlite_present(tmp_path):
    root = _make_artifact_tree(tmp_path, with_sqlite=True)
    caps = compute_capabilities(root)
    assert caps["NLSC_CODE_RESOLVER_AVAILABLE"] == "YES"


def test_capability_no_when_sqlite_absent(tmp_path):
    root = _make_artifact_tree(tmp_path, with_sqlite=False)
    caps = compute_capabilities(root)
    assert caps["NLSC_CODE_RESOLVER_AVAILABLE"] == "NO"


def test_manifest_file_written_explicitly(tmp_path, monkeypatch, capsys):
    root = _make_artifact_tree(tmp_path, with_sqlite=False)
    monkeypatch.setattr(sys, "argv", ["preflight_release_capabilities.py", root])
    exit_code = main()
    assert exit_code == 0  # informational mode never fails
    manifest_path = os.path.join(root, "release_capability_manifest.json")
    assert os.path.isfile(manifest_path)
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    # The manifest EXPLICITLY says NO -- never a silently-omitted key.
    assert manifest["NLSC_CODE_RESOLVER_AVAILABLE"] == "NO"

    captured = capsys.readouterr()
    assert "NLSC_CODE_RESOLVER_AVAILABLE = NO" in captured.out


def test_missing_snapshot_fails_preflight_when_required(tmp_path, monkeypatch):
    root = _make_artifact_tree(tmp_path, with_sqlite=False)
    monkeypatch.setattr(sys, "argv", [
        "preflight_release_capabilities.py", root, "--require", "NLSC_CODE_RESOLVER_AVAILABLE=YES",
    ])
    exit_code = main()
    assert exit_code == 1  # PREFLIGHT_FAIL, never a silent pass


def test_missing_snapshot_passes_when_declared_optional(tmp_path, monkeypatch):
    root = _make_artifact_tree(tmp_path, with_sqlite=False)
    monkeypatch.setattr(sys, "argv", [
        "preflight_release_capabilities.py", root, "--require", "NLSC_CODE_RESOLVER_AVAILABLE=NO",
    ])
    exit_code = main()
    assert exit_code == 0  # explicitly declared optional -- a legitimate, non-silent choice


def test_present_snapshot_passes_when_required(tmp_path, monkeypatch):
    root = _make_artifact_tree(tmp_path, with_sqlite=True)
    monkeypatch.setattr(sys, "argv", [
        "preflight_release_capabilities.py", root, "--require", "NLSC_CODE_RESOLVER_AVAILABLE=YES",
    ])
    exit_code = main()
    assert exit_code == 0
