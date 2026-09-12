# -*- coding: utf-8 -*-
"""
case_export_bundle_adapter.py — TEAM-PROJECT-MERGE-M1.

The RAG service (backend/rag_service/core.py, contributed by the RAG
teammate) reads "projects" as JSON files under
`backend/rag_service/data/projects/<project_id>.json`; its shipped
`demo-a.json`/`demo-b.json` are DEMO fixtures only.

This adapter is the single, minimal seam that lets the RAG service answer
about a REAL case: it writes the already-computed `CaseExportBundle`
(export/bundle_builder.py -- the H1 unified export model, the same one the
Official 6-page PDF / JSON / Excel outputs are built from) into that
directory, unchanged.

DELIBERATELY READ-ONLY WITH RESPECT TO VALUATION:
this module never recomputes, re-grades, or re-weights anything. It does
not touch Table5-1/Table4 results, FAR, comparable weights, or review
status -- it only serializes what the valuation backend already produced,
so the RAG assistant can read/explain/cite it. The valuation source of
truth stays `export/bundle_builder.py`, never the RAG service.
"""
from __future__ import annotations

import json
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(_THIS_DIR, "data", "projects")

# The teammate's retrieval implementation is keyword-overlap scoring
# (see core.py), NOT embeddings/vector search. Stated explicitly so nothing
# downstream claims vector RAG that does not exist.
RAG_RETRIEVAL_MODE = "KEYWORD_OVERLAP"


def project_id_for_case(case_no: str) -> str:
    return f"case-{case_no}"


def write_case_export_bundle_as_project(bundle, projects_dir: str = PROJECTS_DIR) -> str:
    """Serializes an already-built CaseExportBundle to the RAG service's
    project store and returns the written path. `bundle` is whatever
    export/bundle_builder.py::build_case_export_bundle() returned -- its
    JSON is used verbatim (model_dump_json), never re-shaped, so the RAG
    assistant reads exactly what the official outputs were built from."""
    os.makedirs(projects_dir, exist_ok=True)
    project_id = project_id_for_case(bundle.case_no)
    path = os.path.join(projects_dir, f"{project_id}.json")
    payload = json.loads(bundle.model_dump_json())
    payload["project_id"] = project_id
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
