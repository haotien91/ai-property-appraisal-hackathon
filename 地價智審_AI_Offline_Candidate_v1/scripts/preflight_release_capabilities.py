# -*- coding: utf-8 -*-
"""
preflight_release_capabilities.py — Phase API-2.3F §8: Release Build
Missing-Snapshot Behavior.

Problem this closes: infra/layers/engine/Makefile's `build-EngineLayer`
copies `data/nlsc_code_cache.sqlite3` into the Layer artifact ONLY IF that
file exists at build time (see that Makefile's own comment for why the
guard is `if [ -f ... ]` rather than an unconditional cp -- a fresh
checkout that has never run `py scripts/sync_nlsc_codes.py` must not fail
packaging outright). That guard makes a genuinely MISSING snapshot build
successfully and SILENTLY -- exactly the "不能部署後才發現" (must not
discover this only after deployment) risk this round explicitly forbids.

This script is the fix: run it AFTER `make build-EngineLayer` (or, for a
quick local check, against the repo root itself) and BEFORE `sam deploy`.
It inspects whichever tree it is pointed at for
`python/data/nlsc_code_cache.sqlite3` (build artifact layout) or
`data/nlsc_code_cache.sqlite3` (repo layout), and reports one explicit
capability:

    NLSC_CODE_RESOLVER_AVAILABLE = YES | NO

Two usage modes:

1. Informational (no --require): always prints the capability, exit 0.
   Never silently omits the finding -- NO is reported just as loudly as
   YES, both to stdout and in a small JSON manifest file (Phase API-2.3F
   §8's "capability manifest must明確 NLSC_CODE_RESOLVER_AVAILABLE = NO"
   requirement) written next to the inspected tree.
2. Enforcing (--require NLSC_CODE_RESOLVER_AVAILABLE=YES): exits 1
   (PREFLIGHT_FAIL) if the actual computed capability does not match --
   this is the gate a deployment pipeline should call when the release is
   SUPPOSED to ship with a working NlscCadastralCodeResolver; omit
   --require (or pass =NO) for a deliberate optional/degraded build.

This script does not build anything, does not call AWS, and does not
require `make`/`sam` to be installed -- it only inspects a directory tree
already produced by one of those tools (or the repo itself, for a quick
pre-build sanity check).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

MANIFEST_FILENAME = "release_capability_manifest.json"


def _candidate_sqlite_paths(root: str) -> list:
    return [
        os.path.join(root, "python", "data", "nlsc_code_cache.sqlite3"),  # build artifact layout
        os.path.join(root, "data", "nlsc_code_cache.sqlite3"),            # repo layout
    ]


def compute_capabilities(root: str) -> dict:
    sqlite_present = any(os.path.isfile(p) for p in _candidate_sqlite_paths(root))
    return {
        "NLSC_CODE_RESOLVER_AVAILABLE": "YES" if sqlite_present else "NO",
    }


def _parse_require(raw: str) -> dict:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--require必須為KEY=VALUE格式，收到={raw!r}")
    key, value = raw.split("=", 1)
    return {key.strip(): value.strip().upper()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="build artifact目錄或repo root（預設目前目錄）")
    parser.add_argument("--require", action="append", default=[], type=_parse_require,
                         help="KEY=YES或KEY=NO，例如 --require NLSC_CODE_RESOLVER_AVAILABLE=YES；"
                              "實際值與此不符時PREFLIGHT_FAIL（exit 1）")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    capabilities = compute_capabilities(root)

    manifest_path = os.path.join(root, MANIFEST_FILENAME)
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(capabilities, f, ensure_ascii=False, indent=2, sort_keys=True)
        manifest_written = manifest_path
    except OSError as e:
        # A read-only inspected tree (e.g. a packaged artifact mounted
        # read-only) must not crash the preflight check itself -- the
        # capability finding below is still printed either way.
        manifest_written = f"（無法寫入，root可能為唯讀：{e}）"

    print("=== Phase API-2.3F Release Capability Preflight ===")
    print(f"root: {root}")
    for key, value in sorted(capabilities.items()):
        print(f"{key} = {value}")
    print(f"manifest: {manifest_written}")

    failed = []
    for requirement in args.require:
        for key, expected in requirement.items():
            actual = capabilities.get(key, "NO")
            if actual != expected:
                failed.append((key, expected, actual))

    if failed:
        print("PREFLIGHT_FAIL:")
        for key, expected, actual in failed:
            print(f"  {key}: required={expected} actual={actual}")
        return 1

    print("PREFLIGHT_RESULT = PASS" if args.require else "PREFLIGHT_RESULT = INFO_ONLY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
