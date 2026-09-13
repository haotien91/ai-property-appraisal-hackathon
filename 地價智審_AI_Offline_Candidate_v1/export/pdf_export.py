# -*- coding: utf-8 -*-
"""export/pdf_export.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 15/21.

Materializes the Official 6-page PDF FROM an already-built
CaseExportBundle -- reusing pdf/shulin_official_pdf_renderer.py's own
render_shulin_official_six_page_pdf() (E1, UNCHANGED) against the bundle's
own table3/table5_1/table4 data. This is NOT a second calculation path:
Table51Analysis/Table4Analysis were computed exactly once (in export/
bundle_builder.py), and this module only re-hydrates them back into their
pydantic model shape (model_validate on the SAME dict bundle_builder.py
already produced) so the renderer -- which expects model objects, not raw
dicts -- can draw the SAME numbers into the SAME 6 pages E1 already
verified. No new computation, no new template, no new mapping."""
from __future__ import annotations

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
for _p in (os.path.join(_REPO_ROOT, "pdf"), _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain.models import Table51Analysis, Table4Analysis  # noqa: E402
from shulin_official_pdf_renderer import render_shulin_official_six_page_pdf  # noqa: E402

from export.models import CaseExportBundle


def render_official_pdf_from_bundle(bundle: CaseExportBundle) -> bytes:
    table51_obj = Table51Analysis.model_validate(bundle.table5_1)
    table4_obj = Table4Analysis.model_validate(bundle.table4)

    table3_data_by_segment = {}
    city = bundle.case.get("city") or ""
    for segment_code, seg_table3 in bundle.table3.items():
        raw_by_field = {f["field_id"]: f.get("raw_value") for f in seg_table3.get("factors", [])}
        district = bundle.segments.get(segment_code, {}).get("district") or bundle.case.get("district") or ""
        raw_by_field["zone_range_description"] = f"{city}{district}"
        table3_data_by_segment[segment_code] = raw_by_field

    return render_shulin_official_six_page_pdf(
        table3_data_by_segment, table51_obj, table4_obj,
        case_no=bundle.case_no, appraisal_base_date=bundle.appraisal_base_date,
    )
