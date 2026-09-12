# Makefile (repo root) — SAM build entrypoint for EngineLayer
# (AWS::Serverless::LayerVersion, Metadata.BuildMethod: makefile).
#
# WHY THIS FILE EXISTS HERE (Phase DEPLOY-1B incident record): AWS SAM's
# CustomMakeBuilder requires a file literally named "Makefile" to exist at
# the resource's ContentUri root (see aws_lambda_builders/workflows/
# custom_make/DESIGN.md: "The Makefile ... is present in the same path as
# the original source directory" -- a plain os.path.join, never a nested
# search). EngineLayer's build needs to copy domain/engine/providers/
# schemas/data/rules from the REPO ROOT -- under `sam build --use-container`,
# SAM mounts/copies ONLY ContentUri into the build environment, so a
# Makefile living in infra/layers/engine/ (the previous ContentUri) had NO
# access to anything outside that folder once containerized (confirmed via
# an actual `sam build --use-container` failure: `cp: cannot stat
# '//domain'`, because that Makefile's own `REPO_ROOT := $(THIS_DIR)../../../`
# arithmetic silently resolved past the container's filesystem root once
# the mount no longer preserved the real repo's directory depth).
#
# Fix: EngineLayer's ContentUri is now `../` (repo root, relative to
# infra/template.yaml) so the ENTIRE repo root is mounted/copied for the
# build, and this Makefile -- now correctly found at that ContentUri's
# root -- can reference domain/engine/providers/schemas/data with plain,
# unambiguous relative paths (no THIS_DIR/REPO_ROOT climbing needed at all,
# since make's own working directory during the build IS this repo root).
#
# Does NOT modify domain/engine/providers/schemas themselves -- pure copy
# + pip install, identical in effect to the pre-existing infra/layers/
# engine/Makefile (kept in place this round as a legacy/native-build
# reference, unused by `sam build --use-container` now that EngineLayer's
# ContentUri points here instead -- see that file's own comments).
#
# ExtractionLayer UPDATE (Phase DEPLOY-1B, second round): after EngineLayer
# was fixed and verified (actual `sam build --use-container` PASS, real
# artifact smoke-tested inside public.ecr.aws/lambda/python:3.12), `sam
# build` continued to ExtractionLayer and hit the EXACT SAME root-cause
# class: infra/layers/extraction/requirements.txt's own
# `-r ../../../backend/requirements-extraction.txt` line assumes the same
# fixed 3-level ContentUri-to-repo-root depth, and also resolved past the
# container's filesystem root once containerized (confirmed via an actual
# failed build: "Could not open requirements file: ... '/backend/
# requirements-extraction.txt'"). Fixed the same way: ExtractionLayer's
# ContentUri is now also `../` (repo root), and `build-ExtractionLayer`
# below lives in this same root Makefile (one CustomMakeBuilder manifest,
# one target per build_logical_id, exactly as aws_lambda_builders' custom_
# make workflow is designed to support). infra/layers/extraction/
# requirements.txt, backend/requirements-extraction.txt, and infra/layers/
# extraction/Makefile are all UNCHANGED -- pymupdf's version is still
# declared in exactly one place (backend/requirements-extraction.txt).
#
# Known cost (Phase DEPLOY-1B risk disclosure, not fixed by this round):
# aws_lambda_builders' CustomMakeWorkflow copies the ENTIRE ContentUri
# (now repo root) into a scratch directory before invoking make, with only
# ".aws-sam" and ".git" hardcoded as exclusions -- there is no .samignore
# support in this SAM CLI version. This means every `sam build` now copies
# the full repo root (including the large data/cadastral_dataset_cache.
# sqlite3 and data/sources/, neither of which this target ever reads) into
# a temporary scratch directory, even though neither ends up in the final
# artifact (only the explicit `cp` lines below determine that). This adds
# build-time I/O and temp disk usage, not final Layer size.

build-EngineLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"

	cp -r "domain" "$(ARTIFACTS_DIR)/python/domain"
	cp -r "engine" "$(ARTIFACTS_DIR)/python/engine"
	cp -r "providers" "$(ARTIFACTS_DIR)/python/providers"
	cp -r "schemas" "$(ARTIFACTS_DIR)/python/schemas"

	mkdir -p "$(ARTIFACTS_DIR)/python/data"
	cp -r "data/rules" "$(ARTIFACTS_DIR)/python/data/rules"
	cp "data/dependency_graph.json" "$(ARTIFACTS_DIR)/python/data/dependency_graph.json"
	if [ -f "data/nlsc_code_cache.sqlite3" ]; then \
		cp "data/nlsc_code_cache.sqlite3" "$(ARTIFACTS_DIR)/python/data/nlsc_code_cache.sqlite3"; \
	fi

	find "$(ARTIFACTS_DIR)/python" -type d -name "__pycache__" -exec rm -rf {} +
	find "$(ARTIFACTS_DIR)/python" -type f -name "*.pyc" -delete

	pip install -r "infra/layers/engine/requirements.txt" -t "$(ARTIFACTS_DIR)/python"

build-ExtractionLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"

	pip install -r "infra/layers/extraction/requirements.txt" -t "$(ARTIFACTS_DIR)/python"

	find "$(ARTIFACTS_DIR)/python" -type d -name "__pycache__" -exec rm -rf {} +
	find "$(ARTIFACTS_DIR)/python" -type f -name "*.pyc" -delete
