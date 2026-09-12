# infra/layers/engine/Makefile — EngineLayer build (SAM Metadata.BuildMethod:
# makefile). `sam build` invokes `make build-EngineLayer` from this
# directory with $(ARTIFACTS_DIR) set to the layer's build output
# directory. Produces exactly:
#
#   $(ARTIFACTS_DIR)/python/domain/
#   $(ARTIFACTS_DIR)/python/engine/
#   $(ARTIFACTS_DIR)/python/providers/
#   $(ARTIFACTS_DIR)/python/schemas/
#   $(ARTIFACTS_DIR)/python/data/rules/
#   $(ARTIFACTS_DIR)/python/data/dependency_graph.json
#   $(ARTIFACTS_DIR)/python/data/nlsc_code_cache.sqlite3 (if present -- see below)
#   $(ARTIFACTS_DIR)/python/<pip-installed pydantic/shapely>
#
# Deliberately narrower than the whole data/ tree: `grep`-verified (this
# round's audit) that backend/handlers, engine, and providers code ONLY
# ever reads data/rules/ and data/dependency_graph.json at runtime.
# data/sources/ alone is 100MB of archived reference PDFs/GIS files never
# read at runtime; data/golden/ and data/demo_errors/ are test-fixture-only
# Python files never imported by production code. Copying them would
# bloat the deployed Layer for zero runtime benefit.
#
# data/snapshots/*.sqlite3 (~190MB 新北市使用分區 + ~5MB 新北市都市計畫
#範圍) + data/dataset_registry.sqlite3 are DELIBERATELY NEVER packaged
# here (2026-09-09 URBAN_PLAN_BOUNDARY_SNAPSHOT_NOT_PACKAGED audit, see
# docs/backlog.md): an earlier attempt to bake them into this Layer hit
# both a real size concern (190MB alone is uncomfortably close to
# Lambda's 250MB combined Layer+package limit) and an unresolved `sam
# build --use-container` packaging anomaly on the dev machine that
# investigated it. The adopted fix is backend/handlers/gis_snapshot_
# bootstrap.py -- the SAME S3-to-/tmp-at-cold-start pattern already
# proven for the ~407MB cadastral dataset (backend/handlers/cadastral_
# snapshot_bootstrap.py, itself intentionally NOT packaged into this
# Layer either). Local/offline development is unaffected: it keeps
# reading data/snapshots/ + data/dataset_registry.sqlite3 exactly as
# scripts/sync_ntpc_zoning_dataset.py already produces them, via
# DatasetRegistry's own DEFAULT_DB_PATH -- nothing about that path
# resolution goes through this Makefile at all.
#
# Does NOT modify domain/engine/providers themselves -- this is a pure
# copy + pip install, no source transformation of any kind.

THIS_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(THIS_DIR)../../../)

# data/nlsc_code_cache.sqlite3 (Phase API-2.3H §10-11 packaging audit):
# BEFORE this round, this file was NOT copied here at all -- providers/
# nlsc_code_cache.py's DEFAULT_DB_PATH resolves (both in dev and once
# mounted at /opt/python/... in Lambda) to this exact
# $(ARTIFACTS_DIR)/python/data/ directory, so its ABSENCE from packaging
# was a real, live deployment gap: NlscCadastralCodeResolver/
# RealOfficialParcelCoordinateProvider would have found no DB file at all
# in a deployed Lambda even with NLSC_CAD_API_ENABLED=true. Chosen fix is
# Option A from this round's design comparison (see docs/phase9/
# official_parcel_coordinate_audit.md §S15.4): a build-time SQLite
# snapshot baked into EngineLayer (same pattern as data/rules/), read
# strictly read-only at runtime (NlscCodeCache(read_only=True), see that
# module's docstring) -- smaller change than Option B (S3 + /tmp download
# on cold start) for a snapshot this size (a few hundred KB; counties/
# towns/sections combined). The `if` guard below is deliberate: a fresh
# checkout that has never run `py scripts/sync_nlsc_codes.py` has no such
# file yet, and packaging must not hard-fail for that -- the Lambda-side
# read-only NlscCodeCache already degrades to a clear NlscCodeCacheError
# (never a crash) when the file is absent. This Makefile edit is a LOCAL
# packaging-design change only; it does not itself trigger `sam build` or
# `sam deploy`.
build-EngineLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"

	cp -r "$(REPO_ROOT)/domain" "$(ARTIFACTS_DIR)/python/domain"
	cp -r "$(REPO_ROOT)/engine" "$(ARTIFACTS_DIR)/python/engine"
	cp -r "$(REPO_ROOT)/providers" "$(ARTIFACTS_DIR)/python/providers"
	cp -r "$(REPO_ROOT)/schemas" "$(ARTIFACTS_DIR)/python/schemas"

	mkdir -p "$(ARTIFACTS_DIR)/python/data"
	cp -r "$(REPO_ROOT)/data/rules" "$(ARTIFACTS_DIR)/python/data/rules"
	cp "$(REPO_ROOT)/data/dependency_graph.json" "$(ARTIFACTS_DIR)/python/data/dependency_graph.json"
	if [ -f "$(REPO_ROOT)/data/nlsc_code_cache.sqlite3" ]; then \
		cp "$(REPO_ROOT)/data/nlsc_code_cache.sqlite3" "$(ARTIFACTS_DIR)/python/data/nlsc_code_cache.sqlite3"; \
	fi

	find "$(ARTIFACTS_DIR)/python" -type d -name "__pycache__" -exec rm -rf {} +
	find "$(ARTIFACTS_DIR)/python" -type f -name "*.pyc" -delete

	pip install -r "$(THIS_DIR)requirements.txt" -t "$(ARTIFACTS_DIR)/python"
