# -*- coding: utf-8 -*-
"""
runtime_paths.py — Phase DEPLOY-1D: shared Lambda/local runtime import-path
bootstrap. NO business logic, NO estimation rules, NO data queries, NO AWS
service calls -- this file's only job is making `import <module>` resolve
correctly for domain/engine/providers/pdf regardless of which of three
environments the calling handler is actually running in:

1. A real (or `sam local invoke`-emulated) AWS Lambda Zip function with
   EngineLayer attached -- engine/providers/domain/schemas live under
   `/opt/python/` (the Layer mount point).
2. A real (or emulated) AWS Lambda Container Image function (PdfFunction)
   -- it does NOT attach EngineLayer at all (see infra/template.yaml);
   `pdf/` is instead baked directly into the image at
   `${LAMBDA_TASK_ROOT}/pdf` (`backend/docker/pdf.Dockerfile`'s own
   `COPY pdf ${LAMBDA_TASK_ROOT}/pdf`).
3. Local development / pytest -- nothing is mounted at `/opt` or
   `/var/task`; the real repo checkout itself must be added to sys.path.

INCIDENT THIS FIXES (Phase DEPLOY-1C): every backend/handlers/*.py file
that needed engine/ or providers/ or pdf/ computed a project root as
`os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
__file__))))` -- three directory levels up from the handler file's OWN
location. That arithmetic is correct ONLY for scenario 3 above, where the
file genuinely sits at `<repo-root>/backend/handlers/X.py` (three levels
deep). SAM's `CodeUri: ../backend/handlers/` FLATTENS every Zip
function's deployment package, so at runtime the very same file is
`/var/task/X.py` (zero levels deep) -- climbing three levels from there
lands on `/`, silently producing `/engine`, `/providers`, `/pdf`, none of
which exist, so those sys.path entries were always dead weight in a REAL
deployment, and any project import depending on them (e.g. `import
rule_engine`, `import base`, `import human_confirmation`, `import
document_extraction_provider`, `import pdf_renderer`) crashed at cold
start with `ModuleNotFoundError` -- confirmed via an actual `sam build
--use-container` artifact run inside `public.ecr.aws/lambda/python:3.12`
and `sam local invoke`, not merely inferred. The identical three-level
assumption also broke inside PdfFunction's Container Image
(`pdf_handler.py`'s equivalent computation also resolved to `/pdf`, not
the real `${LAMBDA_TASK_ROOT}/pdf`).

This module deliberately does NOT use its OWN `__file__`'s directory
depth to decide which of the three scenarios above applies -- this file
is ITSELF deployed via the exact same flattening CodeUri mechanism as
every other handler, so that same assumption would be exactly as broken
here as it was in the code this fixes. Instead, it detects the
environment via `LAMBDA_TASK_ROOT`, an environment variable that AWS
Lambda's own runtime -- real or `sam local invoke`'s emulator, both
confirmed empirically this round -- always sets, and which local
pytest/dev execution never sets. `__file__`-relative repo-root
computation is used ONLY in the one branch where it is actually valid:
genuine local execution from the real, unflattened source tree.
"""
from __future__ import annotations

import os
import sys


def _add_if_exists(path: "str | None") -> None:
    """Never adds a nonexistent path to sys.path (an unverified path
    entry is exactly the pattern that let the original bug hide
    undetected -- every handler already "successfully" ran `sys.path.
    insert(0, os.path.join(_ROOT, "engine"))` in production with no
    error of its own, because inserting a nonexistent path is not itself
    an error; only the later `import` statement failed)."""
    if path and os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


def bootstrap() -> None:
    """Call this ONCE, before any `domain`/`engine`/`providers`/`pdf`
    project import, from every backend/handlers/*.py entrypoint that
    needs one of those. Idempotent (safe to call more than once) and
    side-effect-free beyond sys.path mutation -- no I/O, no AWS clients,
    no business logic. Unconditionally tries every candidate path for
    the detected environment; harmless for a handler that only needs a
    subset, since `_add_if_exists` silently skips whichever ones the
    current deployment target doesn't have (e.g. PdfFunction has no
    /opt/python at all, and most Zip functions have no LAMBDA_TASK_ROOT/
    pdf)."""
    lambda_task_root = os.environ.get("LAMBDA_TASK_ROOT")

    if lambda_task_root:
        # Real or sam-local-invoke-emulated Lambda. EngineLayer (if
        # attached to this function) mounts at /opt/python; PdfFunction's
        # Container Image instead bakes pdf/ directly under
        # LAMBDA_TASK_ROOT (see backend/docker/pdf.Dockerfile) and has no
        # Layer at all -- both are handled the same way here, purely by
        # checking which paths actually exist.
        _add_if_exists("/opt/python")
        _add_if_exists("/opt/python/engine")
        _add_if_exists("/opt/python/providers")
        _add_if_exists(os.path.join(lambda_task_root, "pdf"))
    else:
        # Local development / pytest: this file's own location IS a
        # reliable "3 levels below repo root" signal ONLY in this
        # branch, since nothing has flattened the source tree here.
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _add_if_exists(repo_root)
        _add_if_exists(os.path.join(repo_root, "engine"))
        _add_if_exists(os.path.join(repo_root, "providers"))
        _add_if_exists(os.path.join(repo_root, "pdf"))


def data_dir() -> str:
    """Returns the real path to `data/` (containing `rules/` and
    `dependency_graph.json`) for the current environment -- a plain
    directory path for direct file reads (`json.load`, etc.), NOT a
    sys.path entry. Some handlers (analyze.py/complete_form.py/review.py)
    read data/rules/*.json and data/dependency_graph.json directly rather
    than importing them as Python modules, so they need this in addition
    to (not instead of) `bootstrap()` -- the same `_ROOT`-climbing bug
    this module fixes affected these direct file-path reads too (they
    used to compute `os.path.join(_ROOT, "data", ...)` with the same
    broken `_ROOT`).

    Never guesses: returns whichever of `/opt/python/data` (EngineLayer's
    mount point) or `<repo-root>/data` (local dev) actually exists;
    raises FileNotFoundError if neither does, since a handler that reads
    data/rules/*.json has no reasonable degraded/Mock behavior to fall
    back to without it."""
    lambda_task_root = os.environ.get("LAMBDA_TASK_ROOT")
    if lambda_task_root:
        candidates = ["/opt/python/data"]
    else:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates = [os.path.join(repo_root, "data")]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    raise FileNotFoundError(f"data目錄不存在於任何候選路徑：{candidates}")
