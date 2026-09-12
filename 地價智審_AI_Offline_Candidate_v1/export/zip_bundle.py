# -*- coding: utf-8 -*-
"""export/zip_bundle.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 15.

Assembles the Supplemental Export ZIP (JSON + 6 Excel files) and,
optionally, the Official PDF (Task 15: "如果方便且不破壞目前 PDF contract"),
all from ONE already-built CaseExportBundle -- no artifact here is
computed a second time; each is just written into the archive."""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from typing import Optional

from export.excel_exporter import export_all_excel_files
from export.json_exporter import export_bundle_to_json_bytes, json_filename
from export.models import CaseExportBundle


def build_export_zip_bytes(bundle: CaseExportBundle, include_pdf: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(json_filename(bundle.case_no), export_bundle_to_json_bytes(bundle))

        with tempfile.TemporaryDirectory() as tmp_dir:
            excel_paths = export_all_excel_files(bundle, tmp_dir)
            for logical_name, path in excel_paths.items():
                zf.write(path, arcname=f"excel/{os.path.basename(path)}")

        if include_pdf:
            from export.pdf_export import render_official_pdf_from_bundle
            pdf_bytes = render_official_pdf_from_bundle(bundle)
            zf.writestr("official_6_page.pdf", pdf_bytes)
            from domain.models import Table51Analysis, Table4Analysis
            from export.page_layout import page_layout
            _, _, pages = page_layout(bundle.table3, Table51Analysis.model_validate(bundle.table5_1),
                                      Table4Analysis.model_validate(bundle.table4))
            zf.writestr("artifact-pages.json", json.dumps(pages, ensure_ascii=False, indent=2))

    return buffer.getvalue()


def bundle_zip_filename(case_no: str) -> str:
    return f"case_{case_no}_export_bundle.zip"


def excel_only_zip_bytes(bundle: CaseExportBundle) -> bytes:
    """Task 16's /export/excel contract: the 6 Excel files only (no JSON,
    no PDF) as a zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        with tempfile.TemporaryDirectory() as tmp_dir:
            excel_paths = export_all_excel_files(bundle, tmp_dir)
            for logical_name, path in excel_paths.items():
                zf.write(path, arcname=os.path.basename(path))
    return buffer.getvalue()


def excel_zip_filename(case_no: str) -> str:
    return f"case_{case_no}_excel.zip"
