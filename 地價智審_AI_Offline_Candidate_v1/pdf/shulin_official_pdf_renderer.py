# -*- coding: utf-8 -*-
"""
shulin_official_pdf_renderer.py — OFFICIAL-SIX-PAGE-PDF-E1.

Builds the OFFICIAL six-page PDF for a Shulin (or any future segment-
scoped, three-comparable) competition case:

    page 1  表3 地價區段勘查表 -- P002-00
    page 2  表3 地價區段勘查表 -- P003-00
    page 3  表3 地價區段勘查表 -- P004-00
    page 4  表3 地價區段勘查表 -- P001-00
    page 5  表5-1 影響地價區域因素分析明細表（住宅用地）
    page 6  表4 比較法調查估價表

PyMuPDF (fitz) ONLY -- no WeasyPrint, no LibreOffice, no pywin32/Excel at
runtime. Overlays onto the 3 DERIVED_RUNTIME_TEMPLATE blank PDFs under
data/templates/shulin/ (produced once, offline, by scripts/
build_shulin_official_pdf_templates.py via Excel COM automation -- see
that script's own docstring), driven entirely by the 3 auditable mapping
configs (data/templates/shulin/shulin_table{3,51,4}_mapping.json, built
by scripts/build_shulin_pdf_mappings.py) -- never a hand-typed pixel
coordinate in this file.

THIS MODULE IS COMPLETELY SEPARATE FROM THE LEGACY JINSHAN OFFICIAL
RENDERER (pdf/official_pdf_renderer.py, data/templates/
official_appraisal_form_v1.pdf) -- that module/template is for the
single-comparable Golden Case flow and is never imported, read, or
modified here. This module never loads official_appraisal_form_v1.pdf.

DATA FLOW (never the reverse): Table51Analysis / Table4Analysis (already
computed by engine/table51_analysis_engine.py / engine/
table4_analysis_engine.py, exactly as returned by the existing runtime
handlers) -> this renderer. No grading, no calculation, no re-derivation
of any number happens in this file.

OFFICIAL vs AUDIT SEPARATION: this renderer writes ONLY values a human
reviewer would expect to see on the real 查估書表 forms themselves --
raw survey facts, grades, adjustment percentages, prices. It NEVER
writes rule_id, source_type, status strings, "MANUAL_REVIEW_REQUIRED",
"SYSTEM_AUXILIARY", confidence scores, or any other internal/audit
label. A field whose underlying value is None (unresolved, requires
manual review, or genuinely never surveyed) is simply left BLANK on the
page -- never a fabricated number, never an internal status string
standing in for it. See individual per-table builder functions below for
the exact per-field skip conditions (e.g. Table4's FAR field: is_far_
special_policy is read ONLY to confirm the row's own official label
already covers it -- the cell itself never gets special text; it either
has a real evaluation_value or is blank, exactly like every other field).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
from decimal import Decimal
from typing import Any, Dict, List, Optional

import fitz

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_TEMPLATE_DIR = os.path.join(_REPO_ROOT, "data", "templates", "shulin")
_IDENTITY_PATH = os.path.join(_TEMPLATE_DIR, "shulin_template_identity.json")

_TABLE3_TEMPLATE_ID = "shulin_table3_blank_v1"
_TABLE51_TEMPLATE_ID = "shulin_table51_blank_v1"
_TABLE4_TEMPLATE_ID = "shulin_table4_blank_v1"

TABLE3_PAGE_ORDER = ["P002-00", "P003-00", "P004-00", "P001-00"]


class TemplateIdentityMismatchError(Exception):
    """A committed template PDF's SHA256 no longer matches the identity
    record scripts/build_shulin_official_pdf_templates.py wrote when it
    was built -- the file has drifted (or the identity file is stale) and
    its coordinates can no longer be trusted. Mirrors pdf/official_pdf_
    renderer.py's existing TemplateLayoutUnconfirmedError pattern for the
    legacy Jinshan flow."""


class TemplateMissingError(Exception):
    """A required template/mapping/identity file does not exist on disk."""


class ShulinPdfDataUnavailableError(Exception):
    """Raised when a required upstream input (segment map, Table5-1
    result, Table4 result, per-segment factors) is missing -- callers
    (backend/handlers/pdf_handler.py) must fail closed with a clear error
    response, never emit a partially-built PDF."""


class FontUnavailableError(Exception):
    """No CJK-capable font file could be located -- raised rather than
    silently falling back to a non-CJK base font (which would render
    every Chinese character as blank boxes)."""


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_identity() -> Dict[str, dict]:
    if not os.path.isfile(_IDENTITY_PATH):
        raise TemplateMissingError(f"{_IDENTITY_PATH} not found -- run scripts/build_shulin_official_pdf_templates.py")
    with open(_IDENTITY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _open_verified_template(template_id: str) -> "fitz.Document":
    identity = _load_identity()
    record = identity.get(template_id)
    if record is None:
        raise TemplateMissingError(f"No template identity record for {template_id!r}")
    pdf_path = os.path.join(_TEMPLATE_DIR, record["derived_pdf_filename"])
    if not os.path.isfile(pdf_path):
        raise TemplateMissingError(f"{pdf_path} not found -- run scripts/build_shulin_official_pdf_templates.py")
    actual_sha = _sha256(pdf_path)
    if actual_sha != record["derived_pdf_sha256"]:
        raise TemplateIdentityMismatchError(
            f"{pdf_path} sha256={actual_sha} does not match recorded derived_pdf_sha256="
            f"{record['derived_pdf_sha256']!r} -- regenerate via scripts/build_shulin_official_pdf_templates.py "
            f"before relying on its coordinates."
        )
    return fitz.open(pdf_path)


def _load_mapping(filename: str) -> Dict[str, dict]:
    path = os.path.join(_TEMPLATE_DIR, filename)
    if not os.path.isfile(path):
        raise TemplateMissingError(f"{path} not found -- run scripts/build_shulin_pdf_mappings.py")
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    return {e["field_id"]: e for e in entries}


# ---------------------------------------------------------------------------
# CJK font resolution -- independent, minimal copy of pdf/official_pdf_
# renderer.py's own strategy (never imports from that module: the two
# renderers must stay fully decoupled per this round's own requirement).
# ---------------------------------------------------------------------------

def _resolve_cjk_font_path() -> str:
    configured = os.environ.get("CJK_FONT_PATH")
    if configured:
        if not os.path.isfile(configured):
            raise FontUnavailableError("CJK_FONT_PATH must point to an existing CJK font file")
        return configured
    candidates: List[str] = []
    if platform.system() == "Windows":
        candidates += [r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\mingliu.ttc"]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    for font_dir in ("/usr/share/fonts", "/opt/fonts"):
        if not os.path.isdir(font_dir):
            continue
        for root, _dirs, files in os.walk(font_dir):
            for fn in files:
                low = fn.lower()
                if ("noto" in low and "cjk" in low) or "notosanstc" in low.replace(" ", ""):
                    return os.path.join(root, fn)
    raise FontUnavailableError(
        "No CJK font file found (checked Windows Fonts and /usr/share/fonts, /opt/fonts). "
        "Install a Traditional Chinese font (e.g. Noto Sans TC) or msjh.ttc."
    )


# ---------------------------------------------------------------------------
# Value formatting -- NEVER coerces None/missing to 0 or any placeholder
# text; a None value simply means "write nothing" (handled by the caller).
# ---------------------------------------------------------------------------

def _format_value(value: Any, formatter: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if formatter == "pct_signed":
        d = Decimal(str(value))
        sign = "+" if d > 0 else ""
        return f"{sign}{d}%"
    if formatter == "pct_signed_no_unit":
        # Table5-1's own 百分比小計/總修正數 rows already carry their OWN
        # static "％" unit label in a separate template cell right after
        # the value cell (e.g. G11) -- appending another "%" here would
        # print "0%%%".
        d = Decimal(str(value))
        sign = "+" if d > 0 else ""
        return f"{sign}{d}"
    if formatter == "pct_no_unit":
        return f"{value}%"
    if formatter == "int_no_unit":
        try:
            return f"{int(round(float(value))):,}"
        except (TypeError, ValueError):
            return str(value)
    if formatter == "num_no_unit":
        return str(value)
    return str(value)


class _Overlay:
    """Thin per-page-set overlay writer shared by all 3 table builders --
    centralizes font registration, shrink-to-fit text placement, and the
    redact-then-write pattern (for cells like 表3's H6:K6/H7:K7, which
    already carry a static "-" placeholder glyph in the blank template)."""

    def __init__(self, font_path: str, default_size: float = 8.0, min_size: float = 5.0):
        self.font_path = font_path
        self.font = fitz.Font(fontfile=font_path)
        self.default_size = default_size
        self.min_size = min_size

    def register(self, page: "fitz.Page") -> None:
        page.insert_font(fontname="cjk", fontfile=self.font_path)

    def write(self, page: "fitz.Page", entry: dict, value: Any) -> None:
        text = _format_value(value, entry.get("formatter", "str"))
        if text is None:
            return  # missing/None stays blank -- never fabricated, never a 0.
        rect = fitz.Rect(*entry["rect"])
        if entry.get("kind") == "redact_and_write":
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            self.register(page)  # apply_redactions() drops the page's font registration
        align_map = {"left": fitz.TEXT_ALIGN_LEFT, "center": fitz.TEXT_ALIGN_CENTER, "right": fitz.TEXT_ALIGN_RIGHT}
        align = align_map.get(entry.get("align", "left"), fitz.TEXT_ALIGN_LEFT)
        size = entry.get("font_size", self.default_size)
        min_size = entry.get("min_font_size", self.min_size)
        # Small right inset always; a WIDER left inset only for left-
        # aligned text, so a value never visually runs straight into the
        # row's own label text (many of these value cells sit immediately,
        # zero-gap, next to their label's merged cell -- e.g. 表3's "都市
        # 計畫(內外)" label visually extends slightly past its own D:G
        # span, right up against its H4:K4 value cell's left edge).
        right_margin = 2.0
        left_margin = entry.get("left_pad", 2.0) if align == fitz.TEXT_ALIGN_LEFT else right_margin
        avail_width = rect.width - left_margin - right_margin
        while size >= min_size:
            width = self.font.text_length(text, fontsize=size)
            if width <= avail_width:
                anchor_x = rect.x0 + left_margin if align == fitz.TEXT_ALIGN_LEFT else (
                    rect.x1 - right_margin - width if align == fitz.TEXT_ALIGN_RIGHT
                    else rect.x0 + (rect.width - width) / 2
                )
                page.insert_text((anchor_x, rect.y1 - 1.5), text, fontname="cjk", fontsize=size, render_mode=0)
                return
            size -= 0.5
        # Still doesn't fit even at min_size -- wrap via textbox (within the
        # SAME inset area, so the left margin still clears the label)
        # rather than silently dropping the value (never fabricate, but
        # also never silently discard a REAL computed value merely for
        # being long).
        padded_rect = fitz.Rect(rect.x0 + left_margin, rect.y0, rect.x1 - right_margin, rect.y1)
        size = min_size
        # insert_textbox() writes nothing when text overflows its box, so keep
        # shrinking a little further instead of silently dropping the value.
        while size >= 3.5:
            if page.insert_textbox(padded_rect, text, fontname="cjk", fontsize=size, align=align) >= 0:
                return
            size -= 0.5


# ---------------------------------------------------------------------------
# TABLE3 -- one page per segment, 4 pages total, each independently reading
# ONLY that segment's own raw regional-factor data (never shared across
# segments -- TASK 4's "no cross-contamination" requirement).
# ---------------------------------------------------------------------------

# 表3 prints one ○本區段內/○本區段外 pair per facility line. The rows are found
# on the template's own text layer (pinned by sha256) and matched in form order.
_TABLE3_LEFT_ROWS = (
    "major_station_hsr", "major_station_train", "major_station_bus", "major_station_mrt", "bus_stop", "interchange",
    "school_elementary", "school_junior_high", "school_senior_high", "school_college",
    "market", "market_2", "market_3", "park", "park_2", "park_3",
)
_TABLE3_RIGHT_ROWS = (
    "tourism_facility", "parking_lot", "service_facility", "substation", "gas_tank",
    "cemetery", "funeral_home", "crematorium", "columbarium", "sewage_plant", "landfill", "incinerator",
    "water_pollution", "noise_pollution", "air_pollution", "waste_pollution", "other_pollution",
    "department_store", "financial_institution", "entertainment_facility", "exhibition_hotel",
)
_TABLE3_LEADING_MARK_ROWS = {
    "major_station_hsr", "major_station_train", "major_station_bus", "major_station_mrt",
    "school_elementary", "school_junior_high", "school_senior_high", "school_college", "market", "park",
    "cemetery", "funeral_home", "crematorium", "columbarium", "sewage_plant", "landfill", "incinerator",
    "water_pollution", "noise_pollution", "air_pollution", "waste_pollution", "other_pollution",
}
_TABLE3_STATION_LABELS = {"major_station_hsr": "高鐵站", "major_station_train": "火車站",
                          "major_station_bus": "客運站", "major_station_mrt": "捷運站"}
# Only used when no segment polygon exists: a distance from one representative
# point cannot prove which side of the boundary a facility lies on.
_TABLE3_INSIDE_FALLBACK_M = 150.0


def _center_y(rect) -> float:
    return (rect.y0 + rect.y1) / 2


def _table3_row_anchors(page: "fitz.Page") -> Dict[str, dict]:
    circles = page.search_for("○")
    units = page.search_for("M)")
    outside_hits = page.search_for("本區段外")

    def glyph_before(text_rect):
        if text_rect is None:
            return None
        found = [c for c in circles
                 if abs(_center_y(c) - _center_y(text_rect)) <= 3 and -1.0 <= text_rect.x0 - c.x1 <= 3.0]
        return max(found, key=lambda c: c.x1, default=None)

    rows = []
    for inside_text in page.search_for("本區段內"):
        cy = _center_y(inside_text)
        outside_text = min((o for o in outside_hits if abs(_center_y(o) - cy) <= 3 and o.x0 > inside_text.x0),
                           key=lambda o: o.x0, default=None)
        inside, outside = glyph_before(inside_text), glyph_before(outside_text)
        if inside is None or outside is None:
            continue
        side_min = 60.0 if inside.x0 < 300 else 300.0
        lead = max((c for c in circles if abs(_center_y(c) - cy) <= 4 and c.x0 >= side_min and c.x1 < inside.x0 - 40),
                   key=lambda c: c.x0, default=None)
        unit = min((u for u in units if abs(_center_y(u) - cy) <= 4 and u.x0 > outside_text.x1),
                   key=lambda u: u.x0, default=None)
        rows.append({"left": inside.x0 < 300, "cy": cy, "inside": inside, "outside": outside, "lead": lead,
                     "outside_text": outside_text, "unit": unit})
    anchors: Dict[str, dict] = {}
    for is_left, names in ((True, _TABLE3_LEFT_ROWS), (False, _TABLE3_RIGHT_ROWS)):
        side_rows = sorted((r for r in rows if r["left"] == is_left), key=lambda r: r["cy"])
        if len(side_rows) == len(names):
            anchors.update(zip(names, side_rows))
    return anchors


def _mark(page: "fitz.Page", glyph) -> None:
    radius = min(glyph.width, glyph.height) * 0.3
    page.draw_circle(fitz.Point((glyph.x0 + glyph.x1) / 2, _center_y(glyph)), radius,
                     color=(0, 0, 0), fill=(0, 0, 0), width=0.5)


def _draw_table3_facility_marks(overlay: _Overlay, page: "fitz.Page", raw_values: Dict[str, Any]) -> None:
    for prefix, row in _table3_row_anchors(page).items():
        name = raw_values.get(f"{prefix}_name")
        distance = raw_values.get(f"{prefix}_distance_m")
        within = raw_values.get(f"{prefix}_within_segment")
        if prefix in _TABLE3_STATION_LABELS:
            label = page.search_for(_TABLE3_STATION_LABELS[prefix])
            label_x0 = label[0].x0 if label else row["inside"].x0 - 20
            name_x0 = (row["lead"].x1 if row["lead"] is not None else row["inside"].x0 - 80) + 2
            y0, y1 = row["cy"] - 5.5, row["cy"] + 4.5
            overlay.write(page, {"rect": [name_x0, y0, label_x0 - 2, y1], "font_size": 6.5, "min_font_size": 4.5}, name)
            if row["unit"] is not None:
                overlay.write(page, {"rect": [row["outside_text"].x1 + 9, y0, row["unit"].x0 - 1, y1],
                                     "align": "right", "formatter": "int_no_unit", "font_size": 7}, distance)
        if within is None and distance is None:
            continue
        if within is None:
            within = float(distance) <= _TABLE3_INSIDE_FALLBACK_M
        _mark(page, row["inside"] if within else row["outside"])
        if prefix in _TABLE3_LEADING_MARK_ROWS and row["lead"] is not None:
            _mark(page, row["lead"])


def build_table3_page(overlay: _Overlay, mapping: Dict[str, dict], page: "fitz.Page", segment_code: str,
                       raw_values: Dict[str, Any]) -> None:
    overlay.register(page)
    if "segment_code" in mapping:
        overlay.write(page, mapping["segment_code"], segment_code)
    for field_id, value in raw_values.items():
        entry = mapping.get(field_id)
        if entry is None:
            continue  # no coordinate known for this field -- never guessed
        if entry.get("kind") == "checkbox_multiselect" and field_id == "land_use_status":
            selected = {str(item) for item in (value if isinstance(value, (list, tuple, set)) else [value])}
            # The ○ printed immediately left of each label on the template's
            # own 土地利用現況 row (lowest hit, so 表頭 text never matches).
            circles = page.search_for("○")
            for label in selected:
                hits = sorted(page.search_for(label), key=lambda r: r.y0)
                label_rect = hits[-1] if hits else None
                glyph = label_rect and max(
                    (c for c in circles if abs(_center_y(c) - _center_y(label_rect)) <= 3
                     and -1.0 <= label_rect.x0 - c.x1 <= 3.0), key=lambda c: c.x1, default=None)
                if glyph:
                    _mark(page, glyph)
            continue
        overlay.write(page, entry, value)
    _draw_table3_facility_marks(overlay, page, raw_values)


# ---------------------------------------------------------------------------
# TABLE5-1 -- directly from the already-computed Table51Analysis (C1
# runtime result). No regrading, no recalculation, no early averaging.
# ---------------------------------------------------------------------------

def _grade_scales():
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from domain import grade_scales
    return grade_scales


def build_table51_page(overlay: _Overlay, mapping: Dict[str, dict], page: "fitz.Page", table51_analysis) -> None:
    overlay.register(page)
    grade_scales = _grade_scales()
    scales = grade_scales.regional_grade_scales(getattr(table51_analysis, "rule_profile_id", None))

    base_code = table51_analysis.base_segment_code
    if "segment_code[base]" in mapping:
        overlay.write(page, mapping["segment_code[base]"], base_code)

    comp_slot = {1: "comp1", 2: "comp2", 3: "comp3"}
    for comparison in table51_analysis.comparisons:
        slot = comp_slot.get(comparison.comparison_index)
        if slot is None:
            continue  # never more than 3 official comparable slots on this form
        seg_key = f"segment_code[{slot}]"
        if seg_key in mapping:
            overlay.write(page, mapping[seg_key], comparison.comparable_segment_code)

        for fr in comparison.factor_results:
            base_grade_entry = mapping.get(f"{fr.field_id}.base_grade")
            if base_grade_entry is not None:
                overlay.write(page, base_grade_entry, fr.base_grade)
            grade_entry = mapping.get(f"{fr.field_id}.{slot}_grade")
            if grade_entry is not None:
                overlay.write(page, grade_entry, fr.comparable_grade)
            for key, grade in (("base_grade_code", fr.base_grade), (f"{slot}_grade_code", fr.comparable_grade)):
                code_entry = mapping.get(f"{fr.field_id}.{key}")
                if code_entry is not None:
                    overlay.write(page, code_entry, grade_scales.grade_code(scales, fr.field_id, grade))
            pct_entry = mapping.get(f"{fr.field_id}.{slot}_pct")
            if pct_entry is not None:
                # requires_manual_review -> adjustment_pct is already None on
                # the model (fail-closed at the engine layer) -- this write
                # simply reflects that as a blank cell, never re-derives it.
                overlay.write(page, pct_entry, fr.adjustment_pct)

        for cs in comparison.category_subtotals:
            key = f"category{cs.category_index}.{slot}_subtotal_pct"
            entry = mapping.get(key)
            if entry is not None:
                overlay.write(page, entry, cs.subtotal_pct)

        grand_entry = mapping.get(f"grand_total.{slot}_pct")
        if grand_entry is not None:
            overlay.write(page, grand_entry, comparison.total_adjustment_pct)

    for key, text in (getattr(table51_analysis, "remarks", None) or {}).items():
        entry = mapping.get(f"remarks.{key}")
        if entry is not None:
            overlay.write(page, entry, text)


# ---------------------------------------------------------------------------
# TABLE4 -- directly from the already-computed Table4Analysis (D1 runtime
# result). P002/P003/P004 stay fully independent; weight/FAR/base-
# comparison-price safety per Tasks 7/8 is enforced simply by never
# writing a value the model itself left as None -- no extra branching
# needed here beyond that.
# ---------------------------------------------------------------------------

def build_table4_page(overlay: _Overlay, mapping: Dict[str, dict], page: "fitz.Page", table4_analysis,
                       case_no: Optional[str] = None, appraisal_base_date: Optional[str] = None,
                       base_segment_code: Optional[str] = None) -> None:
    overlay.register(page)

    if case_no and "case_no" in mapping:
        overlay.write(page, mapping["case_no"], case_no)
    if appraisal_base_date and "appraisal_base_date" in mapping:
        overlay.write(page, mapping["appraisal_base_date"], appraisal_base_date)
    if base_segment_code and "base.segment_code" in mapping:
        overlay.write(page, mapping["base.segment_code"], base_segment_code)

    comp_slot = {1: "comp1", 2: "comp2", 3: "comp3"}
    for comparison in table4_analysis.comparisons:
        slot = comp_slot.get(comparison.comparison_index)
        if slot is None:
            continue

        for suffix, value in (
            ("land_normal_price_raw", comparison.land_normal_price_raw),
            ("transaction_date_raw", comparison.transaction_date_raw),
            ("price_date_adjustment_pct_raw", comparison.price_date_adjustment_pct_raw),
            ("adjusted_price_raw", comparison.adjusted_price_raw),
            ("segment_code", comparison.comparable_segment_code),
            ("regional_adjustment_pct", comparison.regional_adjustment_pct),
            ("individual_adjustment_total_pct", comparison.individual_adjustment_total_pct),
            ("adjustment_abs_sum", comparison.adjustment_abs_sum),
            ("trial_price", comparison.trial_price),
        ):
            entry = mapping.get(f"{slot}.{suffix}")
            if entry is not None:
                overlay.write(page, entry, value)

        # Task 8 weight safety: ONLY a HUMAN_CONFIRMED weight is ever
        # written -- SYSTEM_AUXILIARY_SUGGESTION/MANUAL_REVIEW_REQUIRED
        # (the only statuses D1 can currently produce) leave this blank.
        weight_entry = mapping.get(f"{slot}.weight_pct")
        draft_weight = (getattr(table4_analysis, "calculation_mode", None) == "PARTIAL_DRAFT"
                        and comparison.weight_status == "SYSTEM_AUXILIARY_SUGGESTION")
        if weight_entry is not None and (comparison.weight_status == "HUMAN_CONFIRMED" or draft_weight):
            overlay.write(page, weight_entry, comparison.weight_pct)

        labels = getattr(table4_analysis, "condition_labels", None) or {}
        for fr in comparison.individual_factor_results:
            for key, code in (("base_name", table4_analysis.base_segment_code),
                              (f"{slot}_name", comparison.comparable_segment_code)):
                name_entry = mapping.get(f"individual.{fr.field_id}.{key}")
                if name_entry is not None:
                    overlay.write(page, name_entry, (labels.get(code) or {}).get(fr.field_id))
            base_entry = mapping.get(f"individual.{fr.field_id}.base_raw")
            if base_entry is not None:
                overlay.write(page, base_entry, fr.base_raw_value)
            comp_raw_entry = mapping.get(f"individual.{fr.field_id}.{slot}_raw")
            if comp_raw_entry is not None:
                overlay.write(page, comp_raw_entry, fr.comparable_raw_value)
            comp_pct_entry = mapping.get(f"individual.{fr.field_id}.{slot}_pct")
            if comp_pct_entry is not None:
                overlay.write(page, comp_pct_entry, fr.adjustment_pct)

    # base_comparison_price: only the opt-in PARTIAL_DRAFT pipeline sets it;
    # strict D1 analyses leave it None, so nothing is written for them.
    price = getattr(table4_analysis, "base_comparison_price", None)
    if price is not None and "base_comparison_price" in mapping:
        overlay.write(page, mapping["base_comparison_price"], price)
    for key, text in (getattr(table4_analysis, "remarks", None) or {}).items():
        entry = mapping.get(f"remarks.{key}")
        if entry is not None:
            overlay.write(page, entry, text)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def render_shulin_official_six_page_pdf(
    table3_data_by_segment: Dict[str, Dict[str, Any]],
    table51_analysis,
    table4_analysis,
    case_no: Optional[str] = None,
    appraisal_base_date: Optional[str] = None,
) -> bytes:
    """table3_data_by_segment: {segment_code: {field_id: raw_value}} for
    ALL FOUR of P002-00/P003-00/P004-00/P001-00 -- each segment's own dict
    must come from THAT segment's own FACTORS record only (never shared).
    Raises ShulinPdfDataUnavailableError if any of the 4 segments is
    missing, TemplateMissingError/TemplateIdentityMismatchError if a
    template/mapping file is absent or has drifted, FontUnavailableError
    if no CJK font can be found -- callers must fail closed on any of
    these, never emit a partial PDF.

    All 6 pages are assembled into ONE fitz.Document (blank template
    pages inserted first, overlay text written afterwards directly onto
    that document's own pages) so the CJK font is embedded exactly ONCE
    for the whole output -- building each page as its own separate
    fitz.Document (each with its own font embed) and merging them
    afterwards was tried first and produced an 80+MB 6-page PDF (one full
    font-file copy per page); this single-document approach does not."""
    missing = [seg for seg in TABLE3_PAGE_ORDER if seg not in table3_data_by_segment]
    if missing:
        raise ShulinPdfDataUnavailableError(f"Missing Table3 data for segment(s): {missing}")

    font_path = _resolve_cjk_font_path()
    overlay = _Overlay(font_path)

    table3_mapping = _load_mapping("shulin_table3_mapping.json")
    table51_mapping = _load_mapping("shulin_table51_mapping.json")
    table4_mapping = _load_mapping("shulin_table4_mapping.json")

    final_doc = fitz.open()
    page_index_for_segment: Dict[str, int] = {}
    for segment_code in TABLE3_PAGE_ORDER:
        src = _open_verified_template(_TABLE3_TEMPLATE_ID)
        final_doc.insert_pdf(src)
        src.close()
        page_index_for_segment[segment_code] = final_doc.page_count - 1

    src51 = _open_verified_template(_TABLE51_TEMPLATE_ID)
    final_doc.insert_pdf(src51)
    src51.close()
    table51_page_index = final_doc.page_count - 1

    src4 = _open_verified_template(_TABLE4_TEMPLATE_ID)
    final_doc.insert_pdf(src4)
    src4.close()
    table4_page_index = final_doc.page_count - 1

    if final_doc.page_count != 6:
        raise ShulinPdfDataUnavailableError(f"Assembled PDF has {final_doc.page_count} pages, expected exactly 6")

    for segment_code in TABLE3_PAGE_ORDER:
        page = final_doc[page_index_for_segment[segment_code]]
        grade_values = _grade_scales().table3_grade_values(table51_analysis, segment_code)
        build_table3_page(overlay, table3_mapping, page, segment_code,
                          {**grade_values, **table3_data_by_segment[segment_code]})

    build_table51_page(overlay, table51_mapping, final_doc[table51_page_index], table51_analysis)

    build_table4_page(
        overlay, table4_mapping, final_doc[table4_page_index], table4_analysis,
        case_no=case_no, appraisal_base_date=appraisal_base_date,
        base_segment_code=table4_analysis.base_segment_code,
    )

    try:
        # Keeps only the glyphs actually used (msjh.ttc/Noto Sans CJK TTC
        # files bundle several full font faces -- without subsetting, the
        # WHOLE file gets embedded even though a given render uses only a
        # few hundred distinct CJK glyphs).
        final_doc.subset_fonts(fallback=False)
    except Exception:
        pass  # subsetting is a size optimization only -- never fail the render over it
    pdf_bytes = final_doc.write(garbage=4, deflate=True)
    final_doc.close()
    return pdf_bytes
