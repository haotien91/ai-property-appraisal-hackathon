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
