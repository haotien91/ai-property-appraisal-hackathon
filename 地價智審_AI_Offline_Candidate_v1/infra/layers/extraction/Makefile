# infra/layers/extraction/Makefile — ExtractionLayer build (SAM
# Metadata.BuildMethod: makefile). `sam build` invokes
# `make build-ExtractionLayer` from this directory with $(ARTIFACTS_DIR)
# set to the layer's build output directory. Produces exactly:
#
#   $(ARTIFACTS_DIR)/python/<pip-installed pymupdf/fitz runtime files>
#
# ONLY pymupdf -- this layer carries no repo source code at all (domain/
# engine/providers already come from EngineLayer, which
# DocumentExtractFunction also attaches). Attached ONLY to
# DocumentExtractFunction in infra/template.yaml -- must not be attached
# to any other Lambda (no other function imports fitz/pymupdf).
#
# pymupdf is a native compiled (non-pure-Python) wheel -- building this
# layer on a non-Linux host (e.g. this repo's Windows dev environment)
# would produce an incompatible wheel. MUST be built via
# `sam build --use-container` (or an equivalent Linux/manylinux builder),
# never by copying a locally pip-installed site-packages directory.

THIS_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

build-ExtractionLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	pip install -r "$(THIS_DIR)requirements.txt" -t "$(ARTIFACTS_DIR)/python"
	find "$(ARTIFACTS_DIR)/python" -type d -name "__pycache__" -exec rm -rf {} +
	find "$(ARTIFACTS_DIR)/python" -type f -name "*.pyc" -delete
