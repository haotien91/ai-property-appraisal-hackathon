# -*- coding: utf-8 -*-
"""
_aws_mock_reset.py — TEST-ONLY helper (never imported by production code
under backend/handlers/, engine/, providers/, or domain/).

STEP5 FINAL GATE Part A root cause (docs/audit/STEP5_FINAL_GATE_REPORT.md):
every backend/handlers/*.py module that touches DynamoDB or S3 reads its
table/bucket name as a MODULE-LEVEL constant via `os.environ.get("X_NAME",
"<default>")` -- evaluated exactly once, at that module's first `import`
in the process (e.g. case_store.TABLE_NAME, document_upload.DOCUMENT_
BUCKET, evaluation_standard.DOCUMENT_BUCKET, pdf_handler.PDF_BUCKET, ...).
This is CORRECT, intentional production behavior: a real deployed Lambda
function's environment variables are fixed for its entire container
lifetime, so reading them once and reusing the value across warm
invocations is the standard, efficient AWS Lambda idiom -- there is no
production bug here, and this file must never be imported by production
code to "fix" it there.

The problem is TEST-ONLY: a single `pytest` invocation runs MANY test
files, each wanting its OWN differently-named mocked table/bucket via
`monkeypatch.setenv(...)` + a fresh `moto.mock_aws()` context. Python
caches modules in `sys.modules` -- whichever test file happens to trigger
a given handler module's FIRST-EVER import in the whole process
permanently fixes that module's resource-name constant for the rest of
the process; every OTHER test file's own env var override has no effect
on an already-imported module. This produced two confirmed, reproduced
symptoms depending on which module got "poisoned" first and by what:
  - case_store.TABLE_NAME bound to the OS default ("AIValuationCases")
    instead of "test-table" -> botocore.errorfactory.ResourceNotFoundException.
  - evaluation_standard.py / document_upload.py / document_extract.py's
    DOCUMENT_BUCKET bound to the OS default ("ai-valuation-documents")
    instead of a test file's own bucket name -> a real S3 put_object
    (to the CORRECT, just-created bucket) followed by a head_object
    against the STALE bucket name never finds it -> "DOCUMENT_NOT_UPLOADED"
    even though the upload genuinely succeeded.

Fix: every AWS-mocking fixture across the test suite calls
`reset_cached_aws_module_state()` immediately after `monkeypatch.setenv(...)`
sets the correct env vars for THIS test (and after entering the fresh
`mock_aws()` context, mirroring the already-established, narrower
`importlib.reload(case_store)` pattern several STEP5 test files already
used before this file existed). Only modules already present in
`sys.modules` are reloaded (skipping a reload for a module the current
test file never needed keeps this cheap and side-effect-free).
"""
from __future__ import annotations

import importlib
import sys

# Every backend/handlers/*.py module confirmed (via `grep -n "^[A-Z_]* = os
# .environ.get(" backend/handlers/*.py`) to bind a DynamoDB table name or
# S3 bucket name as a module-level constant. Kept as one explicit list
# (not auto-discovered) so a new such module added later is a deliberate,
# reviewable addition here, not silently invisible to this reset.
_AWS_RESOURCE_NAME_MODULES = (
    "case_store",
    "document_upload",
    "document_extract",
    "document_get_extraction",
    "document_confirm",
    "evaluation_standard",
    "review",
    "pdf_handler",
)


def reset_cached_aws_module_state() -> None:
    """Call this AFTER monkeypatch.setenv(...) has set the CURRENT test's
    table/bucket env vars and AFTER entering `with mock_aws():` -- forces
    every already-imported module in _AWS_RESOURCE_NAME_MODULES to re-run
    its module-level `os.environ.get(...)` line against the now-current
    environment. A module never imported by the running process (or by
    this test file) is left untouched -- importlib.reload() only makes
    sense for a module already in sys.modules."""
    for name in _AWS_RESOURCE_NAME_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
