# -*- coding: utf-8 -*-
"""
build_clean_official_template.py — PDF-OFFICIAL-1 Task 2.

Produces data/templates/official_appraisal_form_v1.pdf: a copy of the
official data/sources/competition/查估書表範本.pdf with every EXAMPLE
VALUE white-boxed out, using the SAME mechanical, non-guessed
classification rules as scripts/analyze_official_template.py (never a
manual/hand-picked region):

  VALUE_STANDALONE            -> white-box the entire span's bbox.
  VALUE_AFTER_FULLWIDTH_COLON -> white-box ONLY the text after the last
                                  colon (the label, e.g. "名稱：", is left
                                  untouched).
  CHECKBOX_GLYPH               -> white-box the ENTIRE span (checkbox
                                  glyphs + any embedded distance number +
                                  the "○本區段內／●本區段外(距 M)"-style
                                  label all together) -- the ORIGINAL span
                                  text is preserved verbatim in the
                                  companion manifest JSON so
                                  official_pdf_renderer.py can redraw the
                                  fixed label text + a fresh, correctly
                                  positioned ○/●/digits without ever
                                  needing pixel-level glyph surgery.
  STATIC_LABEL                 -> never touched.

Pages 3-5 (the three map attachments -- 地價區段略圖/使用分區圖/地價區段
圖, confirmed in docs/pdf/OFFICIAL_TEMPLATE_SURVEY.md to contain no
appraisal-form fields at all) are copied through completely UNMODIFIED.

The ORIGINAL official PDF is never modified in place -- this script only
ever reads it and writes a new file.

Outputs, alongside the cleaned PDF:
  data/templates/official_appraisal_form_v1.redaction_manifest.json
    -- one entry per white-boxed region (page, bbox, original text,
       classification) for full auditability of exactly what was removed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import fitz

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_official_template import analyze_page  # noqa: E402

PAGES_WITH_FIELDS = (0, 1, 2)
PAD = 0.6  # points of padding around each redacted bbox, to fully cover anti-aliased glyph edges

# FINAL GATE fix: PyMuPDF's rawdict splits a superscript character (a
# different font size within the same visual word, e.g. the "²" in "M²")
# into its OWN span, distinct from the surrounding text -- and that
# isolated span's text often comes back as a bare "2" (no superscript
# markup survives text extraction), which scripts/analyze_official_
# template.py's mechanical classifier then correctly-by-its-own-rules
# (a bare digit matches the VALUE_STANDALONE regex) but WRONGLY-in-fact
# flags as an examine value to redact -- it is actually part of a FIXED
# unit label ("M²", square meters), never itself a filled-in value.
# Confirmed by re-inspecting docs/pdf/_template_survey/page2_spans.json:
# both instances are immediately preceded by a STATIC_LABEL span ending
# in "(M" and followed by one starting with ")". Excluded here BY EXACT
# MEASURED BBOX (not a heuristic pattern) so this fix cannot accidentally
# also exclude some other, genuine value that happens to be the digit "2".
KNOWN_FALSE_POSITIVE_VALUE_STANDALONE_BBOXES = {
    (2, (129.38, 123.89, 131.72, 129.07)),  # 面積(M²) row's superscript "2"
    (2, (159.02, 102.34, 161.6, 108.05)),  # 調整至估價基準日單價(元/M²) row's superscript "2"
}


def _is_known_false_positive(page_no: int, bbox) -> bool:
    for pno, known_bbox in KNOWN_FALSE_POSITIVE_VALUE_STANDALONE_BBOXES:
        if pno != page_no:
            continue
        if all(abs(a - b) < 0.5 for a, b in zip(bbox, known_bbox)):
            return True
    return False


# TABLE1-SAFE-WIRING-1: 墓地/殯儀館/火葬場/納骨塔 (funeral_facility
# sub-rows) each have a leading ○/● row-selector glyph GLUED to the row's
# PERMANENT official label as one merged span in the source PDF (e.g.
# "●墓　地", "○殯儀館") -- confirmed via data/templates/official_
# appraisal_form_v1.redaction_manifest.json's own checkbox_chars field
# (exactly ONE circle per span, positioned at the span's own x0). The
# mechanical CHECKBOX_GLYPH rule -- correct for the "○本區段內...○本
# 區段外(距 M)" clauses, which are entirely Golden-Case-specific and
# dynamic -- over-redacts here: it also removes the row's fixed label text
# (墓地/殯儀館/火葬場/納骨塔), which is NOT Golden-Case-specific and
# should never have been cleared. Each bbox pair below was measured
# directly via PyMuPDF against the ORIGINAL 查估書表範本.pdf (never a
# heuristic pattern -- same exact-bbox-allowlist discipline as
# KNOWN_FALSE_POSITIVE_VALUE_STANDALONE_BBOXES above): the key is the
# full span's own pre-pad bbox (so this only ever matches these 4 exact
# known spans), the value is that SAME span's own checkbox_chars[0]
# bbox (the circle glyph alone) -- redaction narrows to ONLY that
# circle, leaving the label text after it fully intact on the clean
# template so official_pdf_renderer.py can draw a fresh ○/● in front of
# an official label it never has to re-derive or guess.
KNOWN_LABEL_PREFIX_CIRCLE_OVERRIDES = {
    (0, (330.91, 313.34, 354.79, 319.22)): (330.91, 313.34, 336.79, 319.22),  # 墓地
    (0, (330.91, 327.98, 354.79, 333.86)): (330.91, 327.98, 336.79, 333.86),  # 殯儀館
    (0, (330.91, 342.64, 354.79, 348.52)): (330.91, 342.64, 336.79, 348.52),  # 火葬場
    (0, (330.91, 357.28, 354.79, 363.16)): (330.91, 357.28, 336.79, 363.16),  # 納骨塔
    # TABLE1-MAJOR-STATION-1: 大型車站's 高鐵站/火車站/捷運站 rows have
    # the SAME "circle glued to permanent default label" shape --
    # "○無高鐵站"/"○無火車站"/"○無捷運站" are each ONE merged span (see
    # the manifest's own checkbox_chars: exactly one circle each, at the
    # span's own x0). "無XXX站" is the template's own PERMANENT default
    # text (present on every copy of this form, meaning "no such station
    # found here"), never Golden-Case-specific -- unlike the 4th row in
    # this same section ("●國光客運金山站", a bus-terminal name that IS
    # genuine Golden Case content), which is intentionally left as-is
    # (fully redacted, no override) since 客運/bus-terminal is out of
    # this round's scope entirely.
    (0, (99.38, 237.26, 129.23, 243.14)): (99.38, 237.26, 105.26, 243.14),  # 無高鐵站
    (0, (99.38, 251.9, 129.23, 257.78)): (99.38, 251.9, 105.26, 257.78),   # 無火車站
    (0, (99.38, 281.18, 129.23, 287.06)): (99.38, 281.18, 105.26, 287.06),  # 無捷運站
}


def _label_prefix_circle_override(page_no: int, bbox):
    for (pno, known_bbox), circle_bbox in KNOWN_LABEL_PREFIX_CIRCLE_OVERRIDES.items():
        if pno != page_no:
            continue
        if all(abs(a - b) < 0.5 for a, b in zip(bbox, known_bbox)):
            return circle_bbox
    return None

# STEP "PDF-OFFICIAL-1" Task 2 supplement: scripts/analyze_official_
# template.py's mechanical classifier only catches VALUE_STANDALONE spans
# matching a numeric/percent/date/dash SHAPE and VALUE_AFTER_FULLWIDTH_
# COLON spans -- it deliberately does NOT try to guess that an arbitrary
# short CJK-text span (e.g. "都市計畫內", "第二種商業區", "已完全開發")
# is a filled-in example VALUE rather than a fixed LABEL, since many
# genuine static labels are equally short CJK phrases (see that script's
# own docstring/comment on this). The entries below are NOT guessed: each
# bbox was measured directly from data/sources/competition/查估書表範本
# .pdf via PyMuPDF rawdict (page 0 = 表1 only, verified against docs/pdf/
# _template_survey/page0_spans.json and cross-checked against data/golden
# /golden_case_input.py's real BASE_REGIONAL factor list -- every value
# here is confirmed to be exactly the Golden Case's own example data, not
# an assumption). Anything NOT in this list stays untouched (conservative
# default) and is documented as still containing example data in
# docs/pdf/OFFICIAL_TEMPLATE_SURVEY.md's "TEMPLATE_CLEANUP_MANUAL_
# REQUIRED" section.
SUPPLEMENTAL_REDACTIONS_PAGE0 = [
    # (bbox, original_text, field_id) -- CJK-value spans the mechanical
    # classifier left as STATIC_LABEL by default.
    ([152.66, 105.23, 182.54, 111.11], "都市計畫內", "regional_zoning_inside_outside"),
    ([152.66, 119.87, 188.54, 125.75], "第二種商業區", "regional_land_use_zone"),
    ([152.66, 163.79, 158.54, 169.67], "無", "regional_construction_prohibited"),
    ([152.66, 185.78, 158.54, 191.66], "無", "regional_construction_restricted"),
    # "　12　　  M" -- only the "12" digits (chars 3-4 of the span) are the
    # value; " M" is the fixed unit label, kept intact.
    ([151.57, 222.38, 163.0, 228.26], "12", "regional_avg_road_width"),
    ([185.06, 371.68, 214.94, 377.56], "已完全開發", "regional_road_development"),
    ([185.06, 430.24, 238.94, 436.12], "有排水系統不易淹水", "regional_drainage_quality"),
    ([185.06, 444.88, 220.94, 450.76], "該區地勢平坦", "regional_terrain"),
    ([372.43, 583.87, 408.31, 589.75], "顧客通行量多", "regional_customer_traffic"),
    ([372.43, 601.39, 417.25, 607.27], "90%以上作為店舖", "regional_shop_contiguity"),
    # Public-facility checkbox+distance clauses with a CLEAN, unambiguous
    # 1:1 mapping to a single regional factor (see docs/pdf/
    # OFFICIAL_TEMPLATE_SURVEY.md §5 for the facilities EXCLUDED this
    # round because the template splits one factor across 2+ sub-rows --
    # 大型車站/站牌/殯葬設施/電業設施/廢棄物處理/水污染等 -- which this
    # round does not attempt to sub-classify without a genuine per-
    # sub-type data source).
    ([372.43, 506.71, 468.38, 512.59], "○本區段內　○本區段外(距 M)", "regional_department_store_proximity"),
    ([372.43, 535.99, 468.22, 541.87], "○本區段內  ●本區段外(距 210 M)", "regional_financial_institution_proximity"),
    ([372.43, 553.51, 468.38, 559.39], "○本區段內　○本區段外(距 M)", "regional_entertainment_proximity"),
    ([372.43, 571.03, 468.38, 576.91], "○本區段內　●本區段外(距 850 M)", "regional_exhibition_hotel_proximity"),
    ([156.39, 313.1, 231.27, 318.98], "○本區段內  ○本區段外(距", "regional_interchange_proximity"),
    # FINAL GATE round: 2 more single-row (unambiguous 1:1) facility
    # checkbox+distance clauses, confirmed via docs/pdf/_template_survey/
    # page0_spans.json to have exactly ONE row each (unlike 大型車站/
    # 站牌/市場/公園廣場徒步區/電業設施/殯葬設施/廢棄物處理/水污染等,
    # which genuinely split one regional factor across 2+ template rows
    # and remain COORDINATE_MANUAL_REVIEW_REQUIRED -- see docs/pdf/
    # OFFICIAL_TEMPLATE_SURVEY.md §5).
    ([357.07, 123.11, 456.02, 128.99], "●本區段內　○本區段外(距 M)", "regional_tourism_proximity"),
    ([357.07, 152.39, 456.02, 158.27], "○本區段內　●本區段外(距 120 M)", "regional_parking_convenience"),
    # TABLE1-SAFE-WIRING-1: 殯儀館/火葬場 rows each have an orphaned
    # " M)" fragment -- the tail of their own "○本區段內...(距　　M)"
    # checkbox clause -- tokenized by PyMuPDF as a SEPARATE word from the
    # rest of that clause (their Golden Case value is "無"/blank distance,
    # leaving a wide gap before "M)" in the source PDF's content stream)
    # and so never caught by the mechanical CHECKBOX_GLYPH classifier
    # (found and confirmed via direct page.get_text("words") re-inspection
    # this round, not assumed) -- leaving a floating, unexplained "M)"
    # with no matching clause once official_pdf_renderer.py draws its own
    # fresh checkbox+distance text nearby. Redacted here as a plain
    # leftover fragment (never any row's permanent label).
    ([513.2, 327.98, 519.13, 333.86], "M)", "_residual_fragment_funeral_home_checkbox_tail"),
    ([513.2, 342.64, 519.13, 348.52], "M)", "_residual_fragment_crematorium_checkbox_tail"),
    # TABLE1-MAJOR-STATION-1: same class of orphaned " M)" checkbox-tail
    # fragment (found via direct page.get_text("words") re-inspection this
    # round), for 高鐵站/火車站/捷運站's own inside/outside distance
    # clauses -- their Golden Case values are all blank distance, so the
    # tail sits far enough right of "(距" to tokenize as a separate word
    # the mechanical classifier's CHECKBOX_GLYPH span (ending at "距")
    # never covered. The 4th row in this section (客運/國光客運金山站,
    # y=266.54) has a REAL distance value ("300 M)"), so its own "M)" is
    # already inside that row's own (much wider) redacted span -- no
    # fragment left behind there, confirmed, not assumed.
    ([252.12, 237.26, 258.06, 243.14], "M)", "_residual_fragment_hsr_checkbox_tail"),
    ([252.12, 251.9, 258.05, 257.78], "M)", "_residual_fragment_tra_checkbox_tail"),
    ([252.12, 281.18, 258.05, 287.06], "M)", "_residual_fragment_mrt_checkbox_tail"),
    # 建築型態 has NO Structured Result source (see docs/pdf/OFFICIAL_
    # TEMPLATE_SURVEY.md §5) -- redacted anyway (kept permanently BLANK,
    # never re-filled) purely so the "clean" template genuinely has zero
    # residual example text, per Task 2's "清除範例資料值" requirement --
    # NOT wired to any field_id/profile entry, and never will render text.
    ([372.55, 652.15, 430.03, 659.23], "連棟透天厝、公寓", "_unmapped_building_type_no_source"),
    # Header identity fields -- caught by neither VALUE_STANDALONE (a
    # segment code like "P002-00" or a district name are not numeric/
    # percent/date-shaped) nor VALUE_AFTER_FULLWIDTH_COLON (no colon in
    # either span).
    ([36.1, 70.8, 90.5, 79.1], "新北市金山區", "city_district"),
    ([141.4, 87.7, 170.7, 96.0], "P002-00", "segment_code"),
    ([279.6, 83.7, 553.1, 100.1], "北側至金包里街以北臨路第一筆宗地，南側至中山路以南第一筆宗地，西側至中正路，東側至福德街之第二種商業區土地劃為P002-00區段。", "segment_scope"),
]

# Page 2 (表4) supplement -- same principle: every bbox measured directly
# from the real template via rawdict, cross-checked against docs/pdf/
# _template_survey/page2_spans.json and data/golden/golden_case_input.py.
# CELL-REGION style (slightly generous rectangles covering the whole
# table cell, not just the example text's own snug bbox) is used for the
# per-comparable "條件" value cells specifically, since a real case's
# text length will differ from the Golden Case example's -- a snug bbox
# sized to "中山路" would not fully cover a longer road name.
SUPPLEMENTAL_REDACTIONS_PAGE2 = [
    # 基本資料 (parcel identifiers)
    ([205, 68, 300, 78], "新北市金山區金美段489地號", "base_parcel_id"),
    ([346, 68, 441, 78], "新北市金山區溫泉段218地號", "comparable_parcel_id"),
    # 宗地條件
    ([205, 159, 300, 169], "方形", "individual_land_shape[base]"),
    ([346, 159, 441, 169], "方形", "individual_land_shape[comp]"),
    ([205, 171, 300, 181], "單面臨街", "individual_street_frontage[base]"),
    ([346, 171, 441, 181], "單面臨街", "individual_street_frontage[comp]"),
    ([205, 183, 300, 194], "平坦", "individual_land_terrain[base]"),
    ([346, 183, 441, 194], "平坦", "individual_land_terrain[comp]"),
    # 道路條件
    ([205, 195, 300, 206], "主要道路", "individual_road_type[base]"),
    ([346, 195, 441, 206], "次要道路", "individual_road_type[comp]"),
    ([205, 208, 268, 219], "中山路", "individual_frontage_road_width.road_name[base]"),
    ([346, 208, 396, 219], "金包里街", "individual_frontage_road_width.road_name[comp]"),
    # 接近條件 (5 proximity-facility-name rows, ~13.1pt apart)
    ([205, 221, 268, 232], "金山國小", "individual_school_proximity.name[base]"),
    ([346, 221, 396, 232], "金山國小", "individual_school_proximity.name[comp]"),
    ([205, 234, 268, 245], "金山市場", "individual_market_proximity.name[base]"),
    ([346, 234, 396, 245], "金山市場", "individual_market_proximity.name[comp]"),
    ([205, 247, 268, 258], "中山溫泉公園", "individual_park_proximity.name[base]"),
    ([340, 247, 396, 258], "中山溫泉公園", "individual_park_proximity.name[comp]"),
    ([205, 260, 268, 271], "金山區公所站", "individual_station_proximity.name[base]"),
    ([340, 260, 396, 271], "金山區公所站", "individual_station_proximity.name[comp]"),
    ([205, 273, 268, 284], "老街商圈", "individual_commercial_district_proximity.name[base]"),
    ([346, 273, 396, 284], "老街商圈", "individual_commercial_district_proximity.name[comp]"),
    ([205, 286, 268, 297], "金山第一公墓", "individual_nuisance_facility.name[base]"),
    ([340, 286, 396, 297], "金山第一公墓", "individual_nuisance_facility.name[comp]"),
    # 周邊環境條件
    ([205, 299, 285, 310], "可路邊停車", "individual_parking_convenience[base]"),
    ([346, 299, 415, 310], "不可路邊停車", "individual_parking_convenience[comp]"),
    # 行政條件
    ([205, 312, 290, 323], "第二種商業區", "individual_zoning_designation[base]"),
    ([346, 312, 415, 323], "第二種商業區", "individual_zoning_designation[comp]"),
    # Header value fields
    ([697.68, 33.21, 745.68, 40.89], "1140901-99-001", "case_no"),
    # OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 5/6: this footer cell is
    # "填寫日期" (form-FILLING date, i.e. when the estimator filled out
    # THIS form) -- a genuinely DIFFERENT concept from 表4's own header
    # "估價基準日" (appraisal BASE date, case.appraisal_base_date). The
    # original example's "114 年 09 月 18 日" here is a different date
    # from the header's "1140901" (Sept 1) -- proof these were never the
    # same value even in the template's own example. Renamed from the
    # previous (wrong) "appraisal_base_date" field_id -- see
    # build_official_template_profile.py's build_table4() for where
    # case.appraisal_base_date is now actually wired (the header cell
    # below, Task 5), and official_pdf_renderer.py for why this
    # "form_fill_date" field_id is deliberately NEVER wired to any source
    # (Task 6: no verified fill/completion/review-date concept exists
    # anywhere in this codebase's domain model) -- stays blank, never
    # backfilled with appraisal_base_date as a stand-in.
    #
    # Only the date portion (chars after "填寫日期：") is redacted;
    # "承辦員：" label at the tail of the SAME span is left alone since it
    # is itself a label with nothing filled in in this example, so nothing
    # to clear there.
    ([105.26, 540.57, 174.38, 548.25], "114 年 09 月 18 日", "form_fill_date"),
    # 備註欄 narrative paragraphs -- case-specific commentary with NO
    # Structured Result source (FormCompletionEngine computes numbers,
    # never free-text narrative) -- redacted and left BLANK by the
    # renderer, never fabricated.
    ([189, 423, 777, 477], "（比準地或各比較標的 備註欄 narrative, 5 lines）", "table4_remarks_comparable"),
    ([189, 483, 777, 523], "（全案 備註欄 narrative, 4 lines）", "table4_remarks_case"),
]


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 1/2: the previous round's
# add_redact_annot(rect, fill=(1,1,1)) always filled white, regardless of
# the cell's actual background -- inside the template's own gray checkbox
# columns (表1) and pale-yellow label/summary columns (表4/表5-2 use the
# same yellow but 表5-2 happened to have none of its OWN redacted spans
# land inside a yellow cell) this left a visibly wrong white rectangular
# "hole". Fixed by reading each cell's REAL background fill directly from
# the original template's own vector drawing objects (page.get_drawings())
# -- never guessed, never a rendered-pixel approximation -- and reusing
# that exact fill for the redaction in that cell; a redaction with no
# enclosing gray/yellow drawing (the common case -- plain white cells)
# still gets fill=(1,1,1) exactly as before.
def _background_fill_regions(doc: "fitz.Document", page_no: int) -> list:
    """[(fitz.Rect, (r,g,b))] for this page's own gray/yellow cell-shading
    rectangles, read straight from the PDF's content stream. Deliberately
    narrow hue windows (gray: R≈G≈B in 0.6-0.9; yellow: R,G≈1.0, B in
    0.6-0.9) so this can only ever match the 2 known shading colors this
    template actually uses -- never a border/grid-line fill (those are
    solid black, (0,0,0), excluded by construction) and never an
    unrelated color if the source template ever changes."""
    regions = []
    for d in doc[page_no].get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue
        r, g, b = fill
        is_gray = abs(r - g) < 0.02 and abs(g - b) < 0.02 and 0.6 < r < 0.9
        is_yellow = r > 0.95 and g > 0.95 and 0.6 < b < 0.9
        if is_gray or is_yellow:
            regions.append((d["rect"], fill))
    return regions


def _cell_background_fill(regions: list, rect: "fitz.Rect"):
    """The SMALLEST-area gray/yellow region whose bbox contains `rect`'s
    own center point (smallest wins so a small row-specific shaded cell is
    preferred over a large shaded column it happens to sit inside) --
    (1,1,1) [white] when the point falls in no known shaded region, i.e.
    the ordinary case of a plain white cell."""
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    best_fill, best_area = None, None
    for region_rect, fill in regions:
        if region_rect.x0 <= cx <= region_rect.x1 and region_rect.y0 <= cy <= region_rect.y1:
            area = region_rect.width * region_rect.height
            if best_area is None or area < best_area:
                best_fill, best_area = fill, area
    return best_fill or (1, 1, 1)


def build(pdf_path: str, out_pdf_path: str, out_manifest_path: str) -> dict:
    """FINAL GATE fix: the previous round used `page.draw_rect(..., fill=
    white)` to "clear" example values -- this only PAINTS OVER the text
    visually; the original glyphs remain fully present in the page's
    content stream underneath (confirmed via `page.search_for()` finding
    "都市計畫內"/"70%"/"212,958"/etc. at their exact original bbox in the
    supposedly-clean template -- a real, serious finding, not a
    hypothetical). This is the classic "black box over sensitive text"
    redaction mistake. Fixed by using PyMuPDF's actual redaction API
    (`add_redact_annot` + `apply_redactions()`), which REMOVES the
    underlying text/drawing content within each rect from the page,
    not merely obscures it -- verified by scripts/verify_no_residual_
    text.py and tests/test_official_pdf_output.py after this fix."""
    doc = fitz.open(pdf_path)
    manifest = []
    redact_rects_by_page = {pno: [] for pno in PAGES_WITH_FIELDS}
    # Read every page's own gray/yellow cell-shading rectangles from the
    # ORIGINAL (still fully intact -- nothing redacted yet) document,
    # once, before any redaction happens.
    bg_regions_by_page = {pno: _background_fill_regions(doc, pno) for pno in PAGES_WITH_FIELDS}

    for pno in PAGES_WITH_FIELDS:
        spans = analyze_page(doc, pno)
        for e in spans:
            cls = e["classification"]
            if cls == "STATIC_LABEL":
                continue
            if cls == "VALUE_STANDALONE" and _is_known_false_positive(pno, e["bbox"]):
                continue
            if cls == "VALUE_AFTER_FULLWIDTH_COLON":
                if "value_bbox" not in e:
                    continue  # colon with nothing after it -- nothing to redact
                rect = fitz.Rect(*e["value_bbox"])
            else:  # VALUE_STANDALONE, CHECKBOX_GLYPH
                circle_override = _label_prefix_circle_override(pno, e["bbox"]) if cls == "CHECKBOX_GLYPH" else None
                rect = fitz.Rect(*(circle_override or e["bbox"]))
            rect = fitz.Rect(rect.x0 - PAD, rect.y0 - PAD, rect.x1 + PAD, rect.y1 + PAD)
            fill = _cell_background_fill(bg_regions_by_page[pno], rect)
            redact_rects_by_page[pno].append((rect, fill))
            manifest.append({
                "page": pno, "classification": cls, "bbox": [round(v, 2) for v in rect],
                "original_text": e["text"], "redaction_fill": [round(v, 4) for v in fill],
                **({"label_text": e["label_text"], "value_text": e["value_text"]} if "label_text" in e else {}),
                **({"checkbox_chars": e["checkbox_chars"]} if "checkbox_chars" in e else {}),
            })

    for page_no, supplement in ((0, SUPPLEMENTAL_REDACTIONS_PAGE0), (2, SUPPLEMENTAL_REDACTIONS_PAGE2)):
        for bbox, original_text, field_id in supplement:
            rect = fitz.Rect(*bbox)
            rect = fitz.Rect(rect.x0 - PAD, rect.y0 - PAD, rect.x1 + PAD, rect.y1 + PAD)
            fill = _cell_background_fill(bg_regions_by_page[page_no], rect)
            redact_rects_by_page[page_no].append((rect, fill))
            manifest.append({
                "page": page_no, "classification": "SUPPLEMENTAL_MANUAL_VERIFIED", "bbox": [round(v, 2) for v in rect],
                "original_text": original_text, "field_id": field_id, "redaction_fill": [round(v, 4) for v in fill],
            })

    for pno, rect_fills in redact_rects_by_page.items():
        page = doc[pno]
        for rect, fill in rect_fills:
            # OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 2: fill now matches
            # the CELL's own real background (white/gray/yellow, read from
            # the template itself -- see _background_fill_regions()/
            # _cell_background_fill() above) instead of always white. The
            # underlying text/drawing content within the rect is still
            # actually REMOVED by apply_redactions() below either way --
            # not just painted over -- so CLEAN_TEMPLATE_RESIDUAL_SAMPLE_
            # FOUND stays NO regardless of which fill color is used.
            page.add_redact_annot(rect, fill=fill)
        if rect_fills:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    os.makedirs(os.path.dirname(out_pdf_path), exist_ok=True)
    doc.save(out_pdf_path, garbage=3, deflate=True)
    doc.close()

    with open(out_manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    return {
        "source_pdf": pdf_path, "source_sha256": _sha256(pdf_path),
        "clean_pdf": out_pdf_path, "clean_sha256": _sha256(out_pdf_path),
        "redacted_region_count": len(manifest),
        "pages_modified": list(PAGES_WITH_FIELDS),
        "pages_untouched": [i for i in range(fitz.open(pdf_path).page_count) if i not in PAGES_WITH_FIELDS],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=os.path.join("data", "sources", "competition", "查估書表範本.pdf"))
    parser.add_argument("--out", default=os.path.join("data", "templates", "official_appraisal_form_v1.pdf"))
    parser.add_argument("--manifest", default=os.path.join(
        "data", "templates", "official_appraisal_form_v1.redaction_manifest.json"))
    args = parser.parse_args()
    result = build(args.pdf, args.out, args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
