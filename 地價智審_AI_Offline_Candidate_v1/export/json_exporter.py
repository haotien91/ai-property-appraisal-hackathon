# -*- coding: utf-8 -*-
"""export/json_exporter.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 2/3.

Pure serialization of an already-built CaseExportBundle -- reads no
DynamoDB/S3 itself, computes nothing. `case_<case_no>_data.json` is
machine-readable structured data straight from the bundle's own pydantic
schema (never PDF-text-extraction, never OCR)."""
from __future__ import annotations

from export.models import CaseExportBundle


def export_bundle_to_json_bytes(bundle: CaseExportBundle) -> bytes:
    return bundle.model_dump_json(indent=2).encode("utf-8")


def json_filename(case_no: str) -> str:
    return f"case_{case_no}_data.json"
