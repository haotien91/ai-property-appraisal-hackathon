# -*- coding: utf-8 -*-
"""
export/excel_exporter.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 4-13.

Copies the 3 OFFICIAL source XLSX workbooks (never modified in place --
openpyxl.load_workbook() only ever reads them; only the IN-MEMORY copy is
ever wb.save()'d, always to a different output path) and fills ONLY the
already-audited target sheet's fillable cells with values taken straight
from an already-built CaseExportBundle (export/bundle_builder.py) --
never recomputed here. Reuses the EXACT cell coordinates E1 already
audited and visually verified (data/templates/shulin/shulin_table{3,51,4}
_mapping.json's own `source_cell` A1-style references), so a field that
lands correctly on the PDF lands on the same real Excel cell here too
(Task 20 consistency) -- no second, independent cell-mapping guess.

Values are written as NATIVE numbers/strings (e.g. a percentage's plain
Decimal->float number, not a formatted "+6.25%" string) since this output
is meant to be human-editable, not a rendered visual page -- unlike the
PDF renderer's `formatter` field (built for fixed-width text layout),
this module only ever uses `source_cell` from the mapping JSON.

None values are left BLANK (openpyxl simply never assigns them) --
NEVER coerced to 0/empty string/any placeholder. The checkbox_multiselect
land_use_status field (Table3) is intentionally NOT written here at all:
redrawing a checkbox glyph is a PDF-visual concern (pdf/shulin_official_
pdf_renderer.py's own redact+redraw path); doing the same inside a real
editable spreadsheet cell risks corrupting the template's own static
glyphs for no offsetting benefit, so it is left at the template's own
default state, same as every other not-yet-wired facility field.
"""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from typing import Any, Dict, Optional

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from domain.grade_scales import grade_code, regional_grade_scales, table3_grade_values
from export.models import CaseExportBundle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_SRC_DIR = os.path.join(_REPO_ROOT, "data", "sources", "competition", "shulin_residential_2026")
_MAPPING_DIR = os.path.join(_REPO_ROOT, "data", "templates", "shulin")

TABLE3_SOURCE_XLSX = os.path.join(_SRC_DIR, "表3地價區段勘查表.xlsx")
TABLE51_SOURCE_XLSX = os.path.join(_SRC_DIR, "表5影響地價區域因素分析明細表(住宅用地).xlsx")
TABLE4_SOURCE_XLSX = os.path.join(_SRC_DIR, "表4比較法調查估價表.xlsx")

TABLE3_SHEET = "表3區段勘查表"
TABLE51_SHEET = "表5-1區域因素明細表(住)"
TABLE4_SHEET = "表4比較法調查估價表"


class SourceWorkbookModifiedError(Exception):
    """Raised if a source XLSX's own sha256 changes across a call --
    would mean something (not this module) wrote to it in place."""


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_mapping(filename: str) -> Dict[str, dict]:
    path = os.path.join(_MAPPING_DIR, filename)
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    return {e["field_id"]: e for e in entries}


def _top_left_cell(source_cell: str) -> str:
    return source_cell.split(":")[0]


_NUMERIC_FORMATTERS = {"pct_signed", "pct_no_unit", "pct_signed_no_unit", "num_no_unit", "int_no_unit"}


def _to_native(value: Any, formatter: str) -> Any:
    """CaseExportBundle's table5_1/table4 dicts come from Table51Analysis/
    Table4Analysis's OWN `.model_dump_json()` (export/bundle_builder.py) --
    pydantic's JSON mode serializes Decimal as a STRING (JSON has no
    native Decimal type), so by the time this module sees a value it is
    already a plain str, not the original Decimal instance. A naive
    `isinstance(value, Decimal)` check (tried first here) therefore never
    fires, and a numeric value would get written into Excel as TEXT
    (caught by test_9: openpyxl read back '130167' the string, not 130167
    the number). Fixed by using the SAME mapping `formatter` field the PDF
    renderer already carries per field (e.g. "int_no_unit") to decide
    whether to convert back to a real number here."""
    if isinstance(value, Decimal):
        return float(value)
    if formatter == "int_no_unit":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return value
    if formatter in _NUMERIC_FORMATTERS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _write(ws: Worksheet, entry: Optional[dict], value: Any) -> None:
    if entry is None or value is None or value == "":
        return  # unmapped field, or missing value -- NEVER coerced to 0/blank-as-zero
    if entry.get("kind") == "checkbox_multiselect":
        return  # see module docstring
    cell_ref = _top_left_cell(entry["source_cell"])
    ws[cell_ref] = _to_native(value, entry.get("formatter", "str"))


def _open_source_copy(source_path: str) -> "openpyxl.Workbook":
    """Loads (read-only in effect, since we never call wb.save(source_path))
    a fresh in-memory copy of the source workbook. keep_vba/keep_links left
    at defaults; data_only=False so any pre-existing formula text (Task 13:
    none were found in any of these 3 blank forms, per E1's own openpyxl
    audit) would be preserved verbatim rather than replaced by its last
    cached value."""
    return openpyxl.load_workbook(source_path, data_only=False)


# ---------------------------------------------------------------------------
# Table3 (per segment)
# ---------------------------------------------------------------------------

def export_table3_excel(bundle: CaseExportBundle, segment_code: str, out_path: str) -> None:
    before_sha = _sha256(TABLE3_SOURCE_XLSX)
    mapping = _load_mapping("shulin_table3_mapping.json")
    wb = _open_source_copy(TABLE3_SOURCE_XLSX)
    ws = wb[TABLE3_SHEET]

    _write(ws, mapping.get("segment_code"), segment_code)

    seg_data = bundle.table3.get(segment_code, {})
    raw_by_field = {f["field_id"]: f.get("raw_value") for f in seg_data.get("factors", [])}

    seg_meta = bundle.segments.get(segment_code, {})
    city = bundle.case.get("city") or ""
    district = seg_meta.get("district") or bundle.case.get("district") or ""
    _write(ws, mapping.get("zone_range_description"), f"{city}{district}")

    for field_id, raw_value in raw_by_field.items():
        _write(ws, mapping.get(field_id), raw_value)
    for field_id, value in table3_grade_values(bundle.table5_1, segment_code, bundle.profile_id).items():
        _write(ws, mapping.get(field_id), value)

    wb.save(out_path)
    after_sha = _sha256(TABLE3_SOURCE_XLSX)
    if before_sha != after_sha:
        raise SourceWorkbookModifiedError(TABLE3_SOURCE_XLSX)


# ---------------------------------------------------------------------------
# Table5-1
# ---------------------------------------------------------------------------

def export_table51_excel(bundle: CaseExportBundle, out_path: str) -> None:
    before_sha = _sha256(TABLE51_SOURCE_XLSX)
    mapping = _load_mapping("shulin_table51_mapping.json")
    wb = _open_source_copy(TABLE51_SOURCE_XLSX)
    ws = wb[TABLE51_SHEET]

    t51 = bundle.table5_1
    scales = regional_grade_scales(bundle.profile_id or t51.get("rule_profile_id"))
    _write(ws, mapping.get("segment_code[base]"), t51.get("base_segment_code"))

    comp_slot = {1: "comp1", 2: "comp2", 3: "comp3"}
    for comp in t51.get("comparisons", []):
        slot = comp_slot.get(comp.get("comparison_index"))
        if slot is None:
            continue
        _write(ws, mapping.get(f"segment_code[{slot}]"), comp.get("comparable_segment_code"))

        for fr in comp.get("factor_results", []):
            _write(ws, mapping.get(f"{fr['field_id']}.base_grade"), fr.get("base_grade"))
            _write(ws, mapping.get(f"{fr['field_id']}.{slot}_grade"), fr.get("comparable_grade"))
            _write(ws, mapping.get(f"{fr['field_id']}.base_grade_code"),
                   grade_code(scales, fr["field_id"], fr.get("base_grade")))
            _write(ws, mapping.get(f"{fr['field_id']}.{slot}_grade_code"),
                   grade_code(scales, fr["field_id"], fr.get("comparable_grade")))
            _write(ws, mapping.get(f"{fr['field_id']}.{slot}_pct"), fr.get("adjustment_pct"))

        for cs in comp.get("category_subtotals", []):
            _write(ws, mapping.get(f"category{cs['category_index']}.{slot}_subtotal_pct"), cs.get("subtotal_pct"))

        _write(ws, mapping.get(f"grand_total.{slot}_pct"), comp.get("total_adjustment_pct"))

    for key, text in (t51.get("remarks") or {}).items():
        _write(ws, mapping.get(f"remarks.{key}"), text)

    wb.save(out_path)
    after_sha = _sha256(TABLE51_SOURCE_XLSX)
    if before_sha != after_sha:
        raise SourceWorkbookModifiedError(TABLE51_SOURCE_XLSX)


# ---------------------------------------------------------------------------
# Table4
# ---------------------------------------------------------------------------

def export_table4_excel(bundle: CaseExportBundle, out_path: str) -> None:
    before_sha = _sha256(TABLE4_SOURCE_XLSX)
    mapping = _load_mapping("shulin_table4_mapping.json")
    wb = _open_source_copy(TABLE4_SOURCE_XLSX)
    ws = wb[TABLE4_SHEET]

    t4 = bundle.table4
    _write(ws, mapping.get("case_no"), bundle.case_no)
    _write(ws, mapping.get("appraisal_base_date"), bundle.appraisal_base_date)
    _write(ws, mapping.get("base.segment_code"), t4.get("base_segment_code"))

    comp_slot = {1: "comp1", 2: "comp2", 3: "comp3"}
    for comp in t4.get("comparisons", []):
        slot = comp_slot.get(comp.get("comparison_index"))
        if slot is None:
            continue
        for suffix in ("land_normal_price_raw", "transaction_date_raw", "price_date_adjustment_pct_raw",
                       "adjusted_price_raw", "regional_adjustment_pct", "individual_adjustment_total_pct",
                       "adjustment_abs_sum", "trial_price"):
            _write(ws, mapping.get(f"{slot}.{suffix}"), comp.get(suffix))
        _write(ws, mapping.get(f"{slot}.segment_code"), comp.get("comparable_segment_code"))

        # Task 10: weight stays blank unless HUMAN_CONFIRMED, or a disclosed
        # SYSTEM_AUXILIARY suggestion on an opt-in PARTIAL_DRAFT analysis.
        draft_weight = (t4.get("calculation_mode") == "PARTIAL_DRAFT"
                        and comp.get("weight_status") == "SYSTEM_AUXILIARY_SUGGESTION")
        if comp.get("weight_status") == "HUMAN_CONFIRMED" or draft_weight:
            _write(ws, mapping.get(f"{slot}.weight_pct"), comp.get("weight_pct"))

        labels = t4.get("condition_labels") or {}
        for fr in comp.get("individual_factor_results", []):
            _write(ws, mapping.get(f"individual.{fr['field_id']}.base_name"),
                   (labels.get(t4.get("base_segment_code")) or {}).get(fr["field_id"]))
            _write(ws, mapping.get(f"individual.{fr['field_id']}.{slot}_name"),
                   (labels.get(comp.get("comparable_segment_code")) or {}).get(fr["field_id"]))
            _write(ws, mapping.get(f"individual.{fr['field_id']}.base_raw"), fr.get("base_raw_value"))
            _write(ws, mapping.get(f"individual.{fr['field_id']}.{slot}_raw"), fr.get("comparable_raw_value"))
            _write(ws, mapping.get(f"individual.{fr['field_id']}.{slot}_pct"), fr.get("adjustment_pct"))

    # base_comparison_price: only PARTIAL_DRAFT analyses carry one; strict
    # D1 analyses leave it None, so the cell stays blank for them.
    _write(ws, mapping.get("base_comparison_price"), t4.get("base_comparison_price"))
    for key, text in (t4.get("remarks") or {}).items():
        _write(ws, mapping.get(f"remarks.{key}"), text)

    wb.save(out_path)
    after_sha = _sha256(TABLE4_SOURCE_XLSX)
    if before_sha != after_sha:
        raise SourceWorkbookModifiedError(TABLE4_SOURCE_XLSX)


TABLE3_PAGE_ORDER = ["P002-00", "P003-00", "P004-00", "P001-00"]


def export_all_excel_files(bundle: CaseExportBundle, out_dir: str) -> Dict[str, str]:
    """Writes all 6 output files into out_dir, returns {logical_name: path}."""
    os.makedirs(out_dir, exist_ok=True)
    paths: Dict[str, str] = {}
    for seg in TABLE3_PAGE_ORDER:
        out_path = os.path.join(out_dir, f"表3_{seg}.xlsx")
        export_table3_excel(bundle, seg, out_path)
        paths[f"table3_{seg}"] = out_path

    t51_path = os.path.join(out_dir, "表5-1_區域因素分析.xlsx")
    export_table51_excel(bundle, t51_path)
    paths["table51"] = t51_path

    t4_path = os.path.join(out_dir, "表4_比較法調查估價表.xlsx")
    export_table4_excel(bundle, t4_path)
    paths["table4"] = t4_path
    return paths
