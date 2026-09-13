# -*- coding: utf-8 -*-
"""
build_shulin_pdf_mappings.py — OFFICIAL-SIX-PAGE-PDF-E1 Task 3.

Build-time (PyMuPDF-only, no pywin32/Excel dependency -- safe to re-run
anywhere the repo is checked out) generator for the 3 auditable overlay
mapping configs:

  data/templates/shulin/shulin_table3_mapping.json
  data/templates/shulin/shulin_table51_mapping.json
  data/templates/shulin/shulin_table4_mapping.json

Every entry traces back to a REAL cell/merged-range reference in the
corresponding official XLSX sheet (never a hand-guessed pixel number):
this script reads each field's `source_cell` (an Excel A1-style range,
taken directly from the openpyxl audit of the 3 official workbooks --
see docs/phase7/official_six_page_pdf_e1.md Task 1/3) and resolves it to
a PDF-point rectangle via pdf/shulin_pdf_grid.py's grid-boundary
extraction against the ALREADY-BUILT blank template PDF (data/templates/
shulin/shulin_table{3,51,4}_blank_v1.pdf, produced by
build_shulin_official_pdf_templates.py) -- i.e. the rectangle is
measured from the real rendered grid Excel itself drew, not computed
from an Excel-column-width-to-pixel formula.

Fields with no corresponding runtime evidence remain absent.  The local
automatic workflow can now supply nearest-facility names and measured
straight-line distances from NLSC/OSM; those cells are mapped below while
inside/outside circles remain blank unless a real segment Polygon proves
the relationship.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
import zipfile

import fitz

import sys
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "pdf"))
from shulin_pdf_grid import extract_grid_boundaries, rect_for_span, reconcile_boundaries  # noqa: E402

_TEMPLATE_DIR = os.path.join(_REPO_ROOT, "data", "templates", "shulin")
_SRC_DIR = os.path.join(_REPO_ROOT, "data", "sources", "competition", "shulin_residential_2026")

DEFAULT_FONT_SIZE = 8.5
DEFAULT_MIN_FONT_SIZE = 5.5

_DEFAULT_COL_WIDTH_CHARS = 8.43  # Excel's own fallback when a column has no explicit width


def _column_number(label: str) -> int:
    value = 0
    for char in label:
        value = value * 26 + ord(char.upper()) - ord("A") + 1
    return value


def _range_boundaries(reference: str):
    start, _, end = reference.partition(":")
    end = end or start
    import re
    first = re.fullmatch(r"([A-Z]+)(\d+)", start)
    last = re.fullmatch(r"([A-Z]+)(\d+)", end)
    if not first or not last:
        raise ValueError(f"Invalid Excel range: {reference}")
    return _column_number(first[1]), int(first[2]), _column_number(last[1]), int(last[2])


def _xlsx_dimensions(path: str, sheet_name: str, num_cols: int, num_rows: int):
    """Read only column widths and row heights from the OOXML package.

    The mapping generator does not need cell formulas or styles, so using the
    standard library avoids making openpyxl a runtime/setup dependency.
    """
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_doc = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rel_pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target_by_id = {item.attrib["Id"]: item.attrib["Target"] for item in relations.findall(f"{{{rel_pkg}}}Relationship")}
        sheet_node = next((node for node in workbook.findall(f".//{{{main}}}sheet") if node.attrib["name"] == sheet_name), None)
        if sheet_node is None:
            raise ValueError(f"Sheet not found: {sheet_name}")
        relation_id = sheet_node.attrib[f"{{{rel_doc}}}id"]
        target = target_by_id[relation_id].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        sheet = ET.fromstring(archive.read(target))

    col_widths = [_DEFAULT_COL_WIDTH_CHARS] * num_cols
    for node in sheet.findall(f".//{{{main}}}cols/{{{main}}}col"):
        # Match the historical openpyxl ColumnDimension lookup: a grouped
        # <col min="..." max="..."> record is keyed at its first column;
        # unkeyed members use Excel's default width.
        index = int(node.attrib["min"])
        if 1 <= index <= num_cols:
            col_widths[index - 1] = float(node.attrib.get("width", _DEFAULT_COL_WIDTH_CHARS))
    sheet_format = sheet.find(f"{{{main}}}sheetFormatPr")
    default_row_h = float(sheet_format.attrib.get("defaultRowHeight", 15.0)) if sheet_format is not None else 15.0
    row_heights = [default_row_h] * num_rows
    for node in sheet.findall(f".//{{{main}}}sheetData/{{{main}}}row"):
        index = int(node.attrib["r"])
        if index <= num_rows and "ht" in node.attrib:
            row_heights[index - 1] = float(node.attrib["ht"])
    return col_widths, row_heights


def _sheet_grid(xlsx_name: str, sheet_name: str, blank_pdf_name: str, num_cols: int, num_rows: int,
                 force_leading_rows: "int | None" = None):
    """Returns (x_boundaries, y_boundaries), reconciled to EXACTLY
    num_cols+1 / num_rows+1 entries using the sheet's own declared column
    widths / row heights as the proportional reference (see
    reconcile_boundaries()'s docstring for why this is necessary -- 表3 in
    particular has a few genuinely borderless spacer columns that
    get_drawings()-based detection alone cannot find). `force_leading_rows`
    is passed straight to reconcile_boundaries()'s `force_leading` for the
    row axis only -- see that function's docstring for why 表3 needs this
    (rows 1-2 have no cell borders at all, and the auto-fit alone was
    verified, via page.search_for() ground truth, to sometimes pick the
    wrong neighboring split)."""
    col_widths, row_heights = _xlsx_dimensions(
        os.path.join(_SRC_DIR, xlsx_name), sheet_name, num_cols, num_rows
    )

    doc = fitz.open(os.path.join(_TEMPLATE_DIR, blank_pdf_name))
    raw_xs, raw_ys = extract_grid_boundaries(doc[0])
    doc.close()
    xs = reconcile_boundaries(raw_xs, col_widths)
    ys = reconcile_boundaries(raw_ys, row_heights, force_leading=force_leading_rows)
    return xs, ys


def _resolve(xs, ys, source_cell: str):
    min_col, min_row, max_col, max_row = _range_boundaries(source_cell)
    return rect_for_span(xs, ys, min_col, min_row, max_col, max_row)


def _entry(field_id, source_sheet, source_cell, xs, ys, *, align="left",
           font_size=DEFAULT_FONT_SIZE, min_font_size=DEFAULT_MIN_FONT_SIZE,
           formatter="str", kind="text", left_pad=None, x_span=None):
    rect = _resolve(xs, ys, source_cell)
    if x_span is not None:
        # Some highly merged Table 3 rows omit vertical borders. Keep the
        # Excel cell for traceability and replace only the horizontal span
        # with coordinates measured from the blank PDF's text/grid anchors.
        rect[0], rect[2] = x_span
    entry = {
        "field_id": field_id,
        "source_sheet": source_sheet,
        "source_cell": source_cell,
        "rect": rect,
        "font_size": font_size,
        "min_font_size": min_font_size,
        "align": align,
        "formatter": formatter,
        "kind": kind,
    }
    if left_pad is not None:
        entry["left_pad"] = left_pad
    return entry


# ---------------------------------------------------------------------------
# TABLE3 (表3區段勘查表) -- one field catalog, reused for all 4 pages
# (P002/P003/P004/P001), since every page overlays the SAME blank template
# with that page's own segment's data.
# ---------------------------------------------------------------------------

def build_table3_mapping() -> list:
    sheet = "表3區段勘查表"
    xs, ys = _sheet_grid(
        "表3地價區段勘查表.xlsx", sheet, "shulin_table3_blank_v1.pdf", num_cols=22, num_rows=46,
        # Ground-truth verified (page.search_for() against the real blank
        # template): rows 1-2 (title + column-header row) have NO cell
        # borders anywhere, so the first REAL detected row boundary is
        # already row 3's own top edge -- i.e. exactly 2 leading row
        # boundaries are missing, not whatever the auto-fit alone guesses.
        force_leading_rows=2,
    )

    entries = [
        _entry("year_period", sheet, "B3:D3", xs, ys, align="center", x_span=(34.7, 84.0)),
        _entry("segment_code", sheet, "G3:H3", xs, ys, align="center", x_span=(125.6, 181.0)),
        _entry("zone_range_description", sheet, "L3:V3", xs, ys, font_size=7, min_font_size=5, x_span=(285.9, 572.9)),
        # NOTE (fixed after visual overlay verification): D{row}:G4-style
        # cells hold the row's own LABEL text (e.g. D4:G4="都市計畫(內外)"),
        # NOT the fillable value -- the real value cell is the NEXT column
        # group over (H4:K4 for rows 4-9/10, I{row}:K{row} for rows 23-29,
        # confirmed cell-by-cell against the real XLSX). H6:K6/H7:K7 also
        # carry a static "-" placeholder glyph already, so those two use
        # kind="redact_and_write" (redact the existing glyph before writing
        # the real value) rather than a plain overlay that would collide
        # with it.
        # left_pad is widened for rows whose own LABEL text visually runs
        # close to (or slightly past) its nominal cell edge -- confirmed via
        # rendered-page visual inspection, not guessed; the value would
        # otherwise sit flush against the tail of the label with no visible
        # gap between them.
        # left_pad values below are measured directly (page.search_for() on
        # the real blank template) as this row's OWN label's rendered
        # right edge minus the value rect's own x0, +~4pt clearance -- e.g.
        # "都市計畫(內外)" label span ends at x=97.7, H4:K4's x0=84.6, so a
        # value written flush at x0 would visually collide with the tail
        # of the label; not a wrong-cell placement, just insufficient
        # clearance from a label wider than its own nominal D:G span.
        _entry("regional_zoning_inside_outside", sheet, "H4:K4", xs, ys, x_span=(145.3, 285.8)),
        _entry("regional_land_use_zone", sheet, "H5:K5", xs, ys, x_span=(145.3, 285.8)),
        _entry("regional_building_coverage_ratio", sheet, "H6:K6", xs, ys, align="right", formatter="pct_no_unit", kind="redact_and_write", x_span=(145.3, 285.8)),
        _entry("regional_floor_area_ratio", sheet, "H7:K7", xs, ys, align="right", formatter="pct_no_unit", kind="redact_and_write", x_span=(145.3, 285.8)),
        _entry("regional_construction_prohibited", sheet, "H8:K8", xs, ys, x_span=(145.3, 285.8)),
        _entry("regional_construction_restricted", sheet, "H9:K10", xs, ys, font_size=7.5, x_span=(145.3, 285.8)),
        _entry("main_road_name", sheet, "G11:H11", xs, ys, font_size=7, min_font_size=5, x_span=(125.6, 231.0)),
        _entry("regional_main_road_width", sheet, "J11:J11", xs, ys, align="right", formatter="num_no_unit", x_span=(251.0, 272.0)),
        _entry("regional_avg_road_width", sheet, "G12:G12", xs, ys, align="right", formatter="num_no_unit", x_span=(112.0, 145.0)),
        _entry("bus_stop_name", sheet, "F17:G17", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 152.0)),
        _entry("bus_stop_distance_m", sheet, "J17:J17", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("interchange_name", sheet, "F19:H19", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 152.0)),
        _entry("interchange_distance_m", sheet, "J19:J19", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("regional_road_development_level", sheet, "I23:K23", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(181.3, 285.8)),
        _entry("regional_sunlight", sheet, "I24:K24", xs, ys, x_span=(181.3, 285.8)),
        _entry("regional_view", sheet, "I25:K25", xs, ys, x_span=(181.3, 285.8)),
        _entry("regional_slope", sheet, "I26:K26", xs, ys, x_span=(181.3, 285.8)),
        _entry("regional_drainage_quality", sheet, "I27:K27", xs, ys, x_span=(181.3, 285.8)),
        _entry("regional_terrain", sheet, "I28:K28", xs, ys, x_span=(181.3, 285.8)),
        # Public facilities are written only as names/distances.  The ○
        # inside/outside marks stay blank unless a real segment Polygon is
        # available; a distance from a representative point cannot prove
        # which side of the segment boundary a facility lies on.
        _entry("school_elementary_name", sheet, "F35:H35", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 180.0)),
        _entry("school_elementary_distance_m", sheet, "J35:J35", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("school_junior_high_name", sheet, "F36:H36", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 180.0)),
        _entry("school_junior_high_distance_m", sheet, "J36:J36", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("school_senior_high_name", sheet, "F37:H37", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 180.0)),
        _entry("school_senior_high_distance_m", sheet, "J37:J37", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("school_college_name", sheet, "F38:H38", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 180.0)),
        _entry("school_college_distance_m", sheet, "J38:J38", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("market_name", sheet, "F39:H39", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 180.0)),
        _entry("market_distance_m", sheet, "J39:J39", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("park_name", sheet, "F42:H42", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(98.0, 180.0)),
        _entry("park_distance_m", sheet, "J42:J42", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(252.0, 272.0)),
        _entry("tourism_facility_name", sheet, "S4:S5", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("tourism_facility_distance_m", sheet, "U4:U5", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("parking_lot_name", sheet, "S6:S7", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("parking_lot_distance_m", sheet, "U6:U7", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("service_facility_name", sheet, "S8:S9", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("service_facility_distance_m", sheet, "U8:U9", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("substation_name", sheet, "S14:S15", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("substation_distance_m", sheet, "U14:U15", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("gas_tank_name", sheet, "S16:S17", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("gas_tank_distance_m", sheet, "U16:U17", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("cemetery_name", sheet, "S18:S18", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("cemetery_distance_m", sheet, "U18:U18", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("funeral_home_name", sheet, "S19:S19", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("funeral_home_distance_m", sheet, "U19:U19", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("crematorium_name", sheet, "S20:S20", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("crematorium_distance_m", sheet, "U20:U20", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("columbarium_name", sheet, "S21:S21", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("columbarium_distance_m", sheet, "U21:U21", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("sewage_plant_name", sheet, "S22:S22", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("sewage_plant_distance_m", sheet, "U22:U22", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("landfill_name", sheet, "S23:S23", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("landfill_distance_m", sheet, "U23:U23", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("incinerator_name", sheet, "S24:S24", xs, ys, font_size=6, min_font_size=4, x_span=(413.0, 462.0)),
        _entry("incinerator_distance_m", sheet, "U24:U24", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("department_store_name", sheet, "S30:V30", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(413.0, 570.0)),
        _entry("department_store_distance_m", sheet, "U31:U31", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("financial_institution_name", sheet, "S32:V32", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(413.0, 570.0)),
        _entry("financial_institution_distance_m", sheet, "U33:U33", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("entertainment_facility_name", sheet, "S34:V34", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(413.0, 570.0)),
        _entry("entertainment_facility_distance_m", sheet, "U35:U35", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        _entry("exhibition_hotel_name", sheet, "S36:V36", xs, ys, font_size=6.5, min_font_size=4.5, x_span=(413.0, 570.0)),
        _entry("exhibition_hotel_distance_m", sheet, "U37:U37", xs, ys, align="right", formatter="int_no_unit", font_size=7, x_span=(537.0, 558.0)),
        # building_density_pct/building_type remain out of the mapping: the
        # overlapping rows 42-43 have not been pixel-verified for safe text.
        # land_use_status is a multi-select checkbox line, redrawn (never a
        # plain text overlay) by shulin_official_pdf_renderer.py -- kind
        # "checkbox_multiselect" marks it for that special handling; the
        # rect below is the FULL line's own bbox for redaction+redraw.
        _entry("land_use_status", sheet, "Q44:V44", xs, ys, font_size=8, kind="checkbox_multiselect"),
    ]
    return entries


# ---------------------------------------------------------------------------
# TABLE51 (表5-1區域因素明細表(住)) -- 29 regional factors across 8
# categories + 8 category subtotals + 1 grand total, each with base grade
# (no pct -- base is the reference) + 3 comparables' grade+pct.
# ---------------------------------------------------------------------------

# (category_index, factor field_id, factor row) -- one row per factor, in
# official-form order; matches data/rules/competition/shulin_residential_2026/
# regional_rules.json's own 29 REG-*/regional_other catalog (SHULIN-
# COMPETITION-RULE-PACK-A2), cross-verified row-by-row against the real
# 表5-1區域因素明細表(住) sheet (docs/phase7/official_six_page_pdf_e1.md Task 1).
_TABLE51_FACTOR_ROWS = [
    (1, "regional_zoning_inside_outside", 5),
    (1, "regional_land_use_zone", 6),
    (1, "regional_building_coverage_ratio", 7),
    (1, "regional_floor_area_ratio", 8),
    (1, "regional_construction_prohibited", 9),
    (1, "regional_construction_restricted", 10),
    (2, "regional_main_road_width", 12),
    (2, "regional_avg_road_width", 13),
    (2, "regional_major_station_proximity", 14),
    (2, "regional_bus_stop_proximity", 15),
    (2, "regional_interchange_proximity", 16),
    (2, "regional_road_development_level", 17),
    (3, "regional_sunlight", 19),
    (3, "regional_view", 20),
    (3, "regional_slope", 21),
    (3, "regional_drainage_quality", 22),
    (3, "regional_terrain", 23),
    (4, "regional_land_improvement", 25),
    (5, "regional_school_proximity", 27),
    (5, "regional_market_proximity", 28),
    (5, "regional_park_proximity", 29),
    (5, "regional_tourism_facility_proximity", 30),
    (5, "regional_parking_convenience", 31),
    (5, "regional_service_facility_proximity", 32),
    (6, "regional_utility_facility_proximity", 34),
    (6, "regional_funeral_facility_proximity", 35),
    (6, "regional_waste_facility_proximity", 36),
    (7, "regional_pollution_proximity", 38),
    (8, "regional_other", 40),
]
_TABLE51_SUBTOTAL_ROWS = {1: 11, 2: 18, 3: 24, 4: 26, 5: 33, 6: 37, 7: 39, 8: 41}
_TABLE51_GRAND_TOTAL_ROW = 42


def _anchor_table51_rows(ys: list) -> list:
    """Rows 5-44 carry no explicit xlsx height, yet Excel auto-grows rows
    whose labels wrap (e.g. row 16 交流道…), so the proportional
    reconciliation drifts one row from category (2) onward. Every one of
    those rows has its own ruled line on the blank PDF, so they are taken
    verbatim, anchored on the first factor label and checked against labels
    further down the page."""
    doc = fitz.open(os.path.join(_TEMPLATE_DIR, "shulin_table51_blank_v1.pdf"))
    try:
        page = doc[0]
        _raw_xs, raw_ys = extract_grid_boundaries(page)

        def label_y(text):
            hits = [r for r in page.search_for(text) if r.x0 < 200]
            if not hits:
                raise RuntimeError(f"表5-1 blank PDF has no label {text!r}")
            return (hits[0].y0 + hits[0].y1) / 2

        top = max(i for i, y in enumerate(raw_ys) if y <= label_y("都市計畫"))
        if top + 40 >= len(raw_ys):
            raise RuntimeError("表5-1 blank PDF has fewer ruled rows than rows 5-44")
        fixed = list(ys)
        for row in range(5, 45):
            fixed[row - 1] = raw_ys[top + row - 5]
        fixed[44] = raw_ys[top + 40]
        fixed[45] = max(fixed[45], fixed[44])
        for text, row in (("交流道之有無", 16), ("接近學校之程度", 27), ("停車場地之便利程度", 31), ("全案", 44)):
            if not fixed[row - 1] < label_y(text) < fixed[row]:
                raise RuntimeError(f"表5-1 row {row} does not contain its label {text!r}")
        return fixed
    finally:
        doc.close()


def build_table51_mapping() -> list:
    sheet = "表5-1區域因素明細表(住)"
    xs, ys = _sheet_grid(
        "表5影響地價區域因素分析明細表(住宅用地).xlsx", sheet, "shulin_table51_blank_v1.pdf",
        num_cols=13, num_rows=45,
    )
    ys = _anchor_table51_rows(ys)

    entries = [
        _entry("segment_code[base]", sheet, "C3:D3", xs, ys, align="center"),
        _entry("segment_code[comp1]", sheet, "E3:G3", xs, ys, align="center"),
        _entry("segment_code[comp2]", sheet, "H3:J3", xs, ys, align="center"),
        _entry("segment_code[comp3]", sheet, "K3:M3", xs, ys, align="center"),
    ]
    for _cat, field_id, row in _TABLE51_FACTOR_ROWS:
        entries.append(_entry(f"{field_id}.base_grade", sheet, f"C{row}:C{row}", xs, ys, align="center"))
        entries.append(_entry(f"{field_id}.comp1_grade", sheet, f"E{row}:E{row}", xs, ys, align="center"))
        entries.append(_entry(f"{field_id}.comp1_pct", sheet, f"G{row}:G{row}", xs, ys, align="right", formatter="pct_signed"))
        entries.append(_entry(f"{field_id}.comp2_grade", sheet, f"H{row}:H{row}", xs, ys, align="center"))
        entries.append(_entry(f"{field_id}.comp2_pct", sheet, f"J{row}:J{row}", xs, ys, align="right", formatter="pct_signed"))
        entries.append(_entry(f"{field_id}.comp3_grade", sheet, f"K{row}:K{row}", xs, ys, align="center"))
        entries.append(_entry(f"{field_id}.comp3_pct", sheet, f"M{row}:M{row}", xs, ys, align="right", formatter="pct_signed"))

    for cat, row in _TABLE51_SUBTOTAL_ROWS.items():
        entries.append(_entry(f"category{cat}.comp1_subtotal_pct", sheet, f"E{row}:F{row}", xs, ys, align="right", formatter="pct_signed_no_unit"))
        entries.append(_entry(f"category{cat}.comp2_subtotal_pct", sheet, f"H{row}:I{row}", xs, ys, align="right", formatter="pct_signed_no_unit"))
        entries.append(_entry(f"category{cat}.comp3_subtotal_pct", sheet, f"K{row}:L{row}", xs, ys, align="right", formatter="pct_signed_no_unit"))

    row = _TABLE51_GRAND_TOTAL_ROW
    entries.append(_entry("grand_total.comp1_pct", sheet, f"E{row}:F{row}", xs, ys, align="right", formatter="pct_signed_no_unit"))
    entries.append(_entry("grand_total.comp2_pct", sheet, f"H{row}:I{row}", xs, ys, align="right", formatter="pct_signed_no_unit"))
    entries.append(_entry("grand_total.comp3_pct", sheet, f"K{row}:L{row}", xs, ys, align="right", formatter="pct_signed_no_unit"))
    for key, cell in (("base", "C43:D43"), ("comp1", "E43:G43"), ("comp2", "H43:J43"), ("comp3", "K43:M43"),
                      ("whole_case", "C44:M44")):
        entries.append(_entry(f"remarks.{key}", sheet, cell, xs, ys, font_size=6.5, min_font_size=4))
    return entries


# ---------------------------------------------------------------------------
# TABLE4 (表4比較法調查估價表) -- 0基本資料 (rows 5-8) + 20 individual
# factor slots (rows 9-28, incl. FAR at row 26 and catch-all "其他" at row
# 28) + summary block (rows 29-32).
# ---------------------------------------------------------------------------

# field_id (engine/individual_factor_catalog.py's own catalog) -> Table4 row.
# Verified cell-by-cell against the real 表4比較法調查估價表 sheet (docs/
# phase7/official_six_page_pdf_e1.md Task 1): rows 9-25 and 27 are the 18
# non-FAR/non-catch-all IND-* rule-derived factors (in official form
# numbering order 7-23,25), row 26 (numbered item "24容積率(%)") is the FAR
# special-policy slot (individual_floor_area_ratio -- deliberately no rule
# record, see engine/individual_factor_catalog.py's FAR_FIELD_ID), row 28
# ("6其他", unnumbered catch-all) is individual_other.
_TABLE4_INDIVIDUAL_ROWS = {
    "individual_land_area": 9,
    "individual_land_width": 10,
    "individual_land_depth": 11,
    "individual_land_shape": 12,
    "individual_street_frontage": 13,
    "individual_terrain_form4": 14,
    "individual_road_type": 15,
    "individual_frontage_road_width": 16,
    "individual_school_proximity": 17,
    "individual_market_proximity": 18,
    "individual_park_proximity": 19,
    "individual_station_proximity": 20,
    "individual_commercial_district_proximity": 21,
    "individual_nuisance_facility": 22,
    "individual_parking_convenience": 23,
    "individual_zoning_designation": 24,
    "individual_building_coverage_ratio": 25,
    "individual_floor_area_ratio": 26,  # FAR special policy -- see Task 6/7
    "individual_construction_restriction": 27,
    "individual_other": 28,
}

# Per-comparable column groups (1-indexed openpyxl columns): condition
# (base's own raw value / comparable's own raw value) spans 3 cols,
# diff%(P00N only) is the 1 column right after.
_TABLE4_COLS = {
    "base": {"cond": ("D", "F")},
    "comp1": {"cond": ("G", "I"), "pct": "J"},
    "comp2": {"cond": ("K", "M"), "pct": "N"},
    "comp3": {"cond": ("O", "Q"), "pct": "R"},
}


def build_table4_mapping() -> list:
    sheet = "表4比較法調查估價表"
    xs, ys = _sheet_grid(
        "表4比較法調查估價表.xlsx", sheet, "shulin_table4_blank_v1.pdf", num_cols=18, num_rows=36,
    )

    entries = []
    # Header / case-fixed fields.
    # NOTE (fixed after visual smoke-test verification): K1="估價基準日："
    # and O1="案號：" are the LABEL cells themselves (single, unmerged) --
    # appraisal_base_date's real value slot is the merge right after K1
    # (L1:N1); case_no's is the (unmerged, but blank/available) columns
    # right after O1 (P1:R1). The original K1:N1/O1:R1 mapping wrongly
    # started each rect AT its own label cell, visually swapping which
    # value seemed to belong to which label.
    entries.append(_entry("appraisal_base_date", sheet, "L1:N1", xs, ys, align="center"))
    entries.append(_entry("case_no", sheet, "P1:R1", xs, ys, align="center"))
    entries.append(_entry("base_parcel_serial_number", sheet, "H2:I2", xs, ys, align="center"))
    entries.append(_entry("comp1_instance_no", sheet, "L2:M2", xs, ys, align="center"))
    entries.append(_entry("comp2_instance_no", sheet, "P2:Q2", xs, ys, align="center"))
    # NOTE: comp3 has no 4th "實例編號" header slot on this 3-comparable-wide
    # official form beyond O2 (which is the "比較標的3" label itself, not a
    # separate 編號 box) -- left unmapped rather than guessed.

    # 0基本資料 rows: 5=土地正常單價, 6=交易日期+調整百分率, 7=調整至估價基準日單價, 8=地價區段號+區域因素調整百分率
    for cid, (col_lo, col_hi) in (("comp1", ("G", "J")), ("comp2", ("K", "N")), ("comp3", ("O", "R"))):
        entries.append(_entry(f"{cid}.land_normal_price_raw", sheet, f"{col_lo}5:{col_hi}5", xs, ys, align="right", formatter="int_no_unit"))
    entries.append(_entry("comp1.transaction_date_raw", sheet, "G6:I6", xs, ys, align="center"))
    entries.append(_entry("comp1.price_date_adjustment_pct_raw", sheet, "J6:J6", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp2.transaction_date_raw", sheet, "K6:M6", xs, ys, align="center"))
    entries.append(_entry("comp2.price_date_adjustment_pct_raw", sheet, "N6:N6", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp3.transaction_date_raw", sheet, "O6:Q6", xs, ys, align="center"))
    entries.append(_entry("comp3.price_date_adjustment_pct_raw", sheet, "R6:R6", xs, ys, align="right", formatter="pct_no_unit"))
    for cid, (col_lo, col_hi) in (("comp1", ("G", "J")), ("comp2", ("K", "N")), ("comp3", ("O", "R"))):
        entries.append(_entry(f"{cid}.adjusted_price_raw", sheet, f"{col_lo}7:{col_hi}7", xs, ys, align="right", formatter="int_no_unit"))
    entries.append(_entry("base.segment_code", sheet, "D8:F8", xs, ys, align="center"))
    entries.append(_entry("comp1.segment_code", sheet, "G8:I8", xs, ys, align="center"))
    entries.append(_entry("comp1.regional_adjustment_pct", sheet, "J8:J8", xs, ys, align="right", formatter="pct_signed"))
    entries.append(_entry("comp2.segment_code", sheet, "K8:M8", xs, ys, align="center"))
    entries.append(_entry("comp2.regional_adjustment_pct", sheet, "N8:N8", xs, ys, align="right", formatter="pct_signed"))
    entries.append(_entry("comp3.segment_code", sheet, "O8:Q8", xs, ys, align="center"))
    entries.append(_entry("comp3.regional_adjustment_pct", sheet, "R8:R8", xs, ys, align="right", formatter="pct_signed"))

    # 20 individual factor rows.
    for field_id, row in _TABLE4_INDIVIDUAL_ROWS.items():
        entries.append(_entry(f"individual.{field_id}.base_raw", sheet, f"D{row}:F{row}", xs, ys, font_size=7.5))
        entries.append(_entry(f"individual.{field_id}.comp1_raw", sheet, f"G{row}:I{row}", xs, ys, font_size=7.5))
        entries.append(_entry(f"individual.{field_id}.comp1_pct", sheet, f"J{row}:J{row}", xs, ys, align="right", formatter="pct_signed"))
        entries.append(_entry(f"individual.{field_id}.comp2_raw", sheet, f"K{row}:M{row}", xs, ys, font_size=7.5))
        entries.append(_entry(f"individual.{field_id}.comp2_pct", sheet, f"N{row}:N{row}", xs, ys, align="right", formatter="pct_signed"))
        entries.append(_entry(f"individual.{field_id}.comp3_raw", sheet, f"O{row}:Q{row}", xs, ys, font_size=7.5))
        entries.append(_entry(f"individual.{field_id}.comp3_pct", sheet, f"R{row}:R{row}", xs, ys, align="right", formatter="pct_signed"))

    # Summary block: row29=合計(individual total), row30 left-half=調整絕對值加總,
    # row31 left-half=試算價格/right-half=權重, row32=比準地比較價格 (single wide cell).
    entries.append(_entry("comp1.individual_adjustment_total_pct", sheet, "G29:J29", xs, ys, align="right", formatter="pct_signed"))
    entries.append(_entry("comp2.individual_adjustment_total_pct", sheet, "K29:N29", xs, ys, align="right", formatter="pct_signed"))
    entries.append(_entry("comp3.individual_adjustment_total_pct", sheet, "O29:R29", xs, ys, align="right", formatter="pct_signed"))
    entries.append(_entry("comp1.adjustment_abs_sum", sheet, "G30:H30", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp2.adjustment_abs_sum", sheet, "K30:L30", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp3.adjustment_abs_sum", sheet, "O30:P30", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp1.trial_price", sheet, "G31:H31", xs, ys, align="right", formatter="int_no_unit"))
    entries.append(_entry("comp2.trial_price", sheet, "K31:L31", xs, ys, align="right", formatter="int_no_unit"))
    entries.append(_entry("comp3.trial_price", sheet, "O31:P31", xs, ys, align="right", formatter="int_no_unit"))
    entries.append(_entry("comp1.weight_pct", sheet, "I31:J31", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp2.weight_pct", sheet, "M31:N31", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("comp3.weight_pct", sheet, "Q31:R31", xs, ys, align="right", formatter="pct_no_unit"))
    entries.append(_entry("base_comparison_price", sheet, "G32:R32", xs, ys, align="right", formatter="int_no_unit"))
    for key, cell in (("base", "D33:F33"), ("comp1", "G33:J33"), ("comp2", "K33:N33"), ("comp3", "O33:R33"),
                      ("whole_case", "D34:R34")):
        entries.append(_entry(f"remarks.{key}", sheet, cell, xs, ys, font_size=6.5, min_font_size=4))
    return entries


def build_all() -> None:
    mappings = {
        "shulin_table3_mapping.json": build_table3_mapping(),
        "shulin_table51_mapping.json": build_table51_mapping(),
        "shulin_table4_mapping.json": build_table4_mapping(),
    }
    for filename, entries in mappings.items():
        out_path = os.path.join(_TEMPLATE_DIR, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
        print(f"{filename}: {len(entries)} fields")


if __name__ == "__main__":
    build_all()
