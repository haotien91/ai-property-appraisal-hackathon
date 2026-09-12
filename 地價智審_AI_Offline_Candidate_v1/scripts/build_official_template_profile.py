# -*- coding: utf-8 -*-
"""
build_official_template_profile.py — PDF-OFFICIAL-1 Task 3.

Generates data/templates/official_appraisal_form_v1.json from:
  - data/sources/competition/查估書表範本.pdf (the ORIGINAL, to recover
    exactly where each example value/checkbox used to sit -- the CLEAN
    template has those regions blanked, so coordinates must be read from
    the original)
  - scripts/build_clean_official_template.py's supplemental redaction
    lists (page0/page2 hand-verified field_id -> bbox mappings)
  - data/rules/regional_rules.json (the 28-factor canonical order used to
    label 表5-2's 28 rows)

表5-2's per-row grade_code/grade_text/pct cells are recovered by ROW-
INDEX ZIP, not by re-deriving Y coordinates from each label's own
(possibly multi-line) bbox: the 28 factor-label rows and the 28*N
value cells in each column are independently sorted top-to-bottom and
zipped positionally. This is safe because docs/pdf/OFFICIAL_TEMPLATE_
SURVEY.md's manual visual verification (clean_page1.png) already
confirmed the table has exactly 28 data rows with no skipped/merged
rows in any column, and every rule factor's column value uses the SAME
row order regional_rules.json itself lists them in (verified: the
template's own label text matches build_case_and_regional_factors()'s
regional_base_factors order 1:1 for every one of the 28 factors).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_clean_official_template import (  # noqa: E402
    SUPPLEMENTAL_REDACTIONS_PAGE0, SUPPLEMENTAL_REDACTIONS_PAGE2,
)
from analyze_official_template import analyze_page  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_PDF = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")
CLEAN_PDF = os.path.join(REPO_ROOT, "data", "templates", "official_appraisal_form_v1.pdf")
OUT_PROFILE = os.path.join(REPO_ROOT, "data", "templates", "official_appraisal_form_v1.json")

# The 28 regional factors in EXACTLY the row order the template prints
# them (top to bottom) -- matches data/rules/regional_rules.json's factor
# set (verified: same 28 names) and data/golden/golden_case_input.py's
# BASE_REGIONAL field_id convention (regional_<snake_case>).
TABLE5_2_ROW_FACTORS = [
    ("regional_zoning_inside_outside", "都市計畫（內、外）"),
    ("regional_land_use_zone", "使用分區(使用地類別)"),
    ("regional_building_coverage_ratio", "建蔽率"),
    ("regional_floor_area_ratio", "容積率"),
    ("regional_construction_prohibited", "有無禁止建築"),
    ("regional_construction_restricted", "有無限制建築（整體開發、面積限制、高度限制……等）"),
    ("regional_main_road_width", "主要道路寬度"),
    ("regional_avg_road_width", "區段內道路平均寬度"),
    ("regional_major_station_proximity", "接近大型車站之程度"),
    ("regional_bus_stop_proximity", "站牌之接近程度或密集程度"),
    ("regional_interchange_proximity", "交流道之有無及接近交流道之程度"),
    ("regional_road_development", "區段內道路規劃及闢建程度"),
    ("regional_drainage_quality", "排水之良否"),
    ("regional_terrain", "地勢"),
    ("regional_market_proximity", "接近市場之程度（傳統市場、超級市場、超大型購物中心）"),
    ("regional_park_proximity", "接近公園（里鄰公園、一般公園）、廣場、徒步區之程度"),
    ("regional_tourism_proximity", "接近觀光遊憩設施之程度"),
    ("regional_parking_convenience", "停車場地之便利程度"),
    ("regional_utility_facility_proximity", "電業設施及公用氣體燃料設施之有無及接近程度"),
    ("regional_funeral_facility_proximity", "殯葬設施之有無及接近程度"),
    ("regional_waste_facility_proximity", "廢棄物處理設施之有無及接近程度"),
    ("regional_pollution_proximity", "水污染、噪音污染、廢氣污染、廢棄物污染等之有無及接近程度"),
    ("regional_department_store_proximity", "百貨公司之有無、數量、接近程度"),
    ("regional_financial_institution_proximity", "金融機構之有無、數量、接近程度"),
    ("regional_entertainment_proximity", "娛樂設施之有無、數量、接近程度"),
    ("regional_exhibition_hotel_proximity", "大型展示中心或觀光飯店之有無、數量、接近程度"),
    ("regional_customer_traffic", "顧客通行量之多寡"),
    ("regional_shop_contiguity", "店舖之毗連狀態"),
]

# Column x0 anchors for comparable index 0 (比準地 has no pct column);
# comp N (0-based) offsets by COMP_STEP_X from comp 0.
BASE_GRADE_CODE_X = 213.77
BASE_GRADE_TEXT_X = 237.77
COMP0_GRADE_CODE_X = 267.77
COMP0_GRADE_TEXT_X = 294.41
COMP0_PCT_X = 322.99
COMP_STEP_X = 91.58
CELL_W_CODE, CELL_H = 5.0, 8.5
CELL_W_TEXT = 20.0  # must fit a 2-character grade word (稍優/稍劣/普通), not just 1 (優/劣)
CELL_W_PCT = 20.0


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _row_cell(x0: float, y0: float, w: float) -> list:
    return [round(x0, 2), round(y0 - 1.0, 2), round(x0 + w, 2), round(y0 + CELL_H, 2)]


def build_table5_2(doc: fitz.Document) -> dict:
    spans = analyze_page(doc, 1)
    labels = sorted(
        (e for e in spans if abs(e["bbox"][0] - 99.38) < 0.5 and e["classification"] == "STATIC_LABEL"
         and "百分比小計" not in e["text"] and "=(1)" not in e["text"]),
        key=lambda e: e["bbox"][1],
    )
    # Merge consecutive multi-line label fragments into one row per known
    # factor list length (28) -- fragments belonging to the SAME factor
    # are adjacent with a small y-gap (<11) and, joined, are a PREFIX of
    # (or equal to) the canonical factor text (with the form's own line-
    # break, e.g. an extra space, tolerated by substring containment
    # after stripping all whitespace).
    def _norm(s):
        return s.replace(" ", "").replace("\u3000", "")

    merged_rows = []
    i = 0
    factor_idx = 0
    while i < len(labels) and factor_idx < len(TABLE5_2_ROW_FACTORS):
        field_id, factor_text = TABLE5_2_ROW_FACTORS[factor_idx]
        buf = labels[i]["text"]
        y0 = labels[i]["bbox"][1]
        j = i
        while _norm(buf) != _norm(factor_text):
            if _norm(factor_text)[: len(_norm(buf))] != _norm(buf):
                raise RuntimeError(f"assembled label {buf!r} is not a prefix of expected factor {factor_text!r}")
            j += 1
            if j >= len(labels):
                raise RuntimeError(f"could not assemble label text for factor {factor_text!r}, got so far {buf!r}")
            buf += labels[j]["text"]
        merged_rows.append({"field_id": field_id, "factor": factor_text, "y0": y0})
        i = j + 1
        factor_idx += 1
    if factor_idx != len(TABLE5_2_ROW_FACTORS):
        raise RuntimeError(f"only matched {factor_idx}/{len(TABLE5_2_ROW_FACTORS)} rows")

    fields = {}
    for row in merged_rows:
        y0 = row["y0"]
        fields[row["field_id"]] = {
            "factor": row["factor"],
            "base": {
                "grade_code_bbox": _row_cell(BASE_GRADE_CODE_X, y0, CELL_W_CODE),
                "grade_text_bbox": _row_cell(BASE_GRADE_TEXT_X, y0, CELL_W_TEXT),
            },
            "comparables": [
                {
                    "grade_code_bbox": _row_cell(COMP0_GRADE_CODE_X + n * COMP_STEP_X, y0, CELL_W_CODE),
                    "grade_text_bbox": _row_cell(COMP0_GRADE_TEXT_X + n * COMP_STEP_X, y0, CELL_W_TEXT),
                    "pct_bbox": _row_cell(COMP0_PCT_X + n * COMP_STEP_X, y0, CELL_W_PCT),
                }
                for n in range(3)
            ],
        }
    # OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 4: 表5-2's own header 「案號：」
    # cell -- a SOURCE_AVAILABLE_RENDERER_GAP, not a missing source. This
    # bbox is the EXACT value_bbox scripts/analyze_official_template.py's
    # mechanical classifier already found and redacted for this span (see
    # data/templates/official_appraisal_form_v1.redaction_manifest.json:
    # page 1, classification VALUE_AFTER_FULLWIDTH_COLON, original_text
    # "案號：1140901-99-001") -- measured, not guessed. "kind": "header_text"
    # marks it for official_pdf_renderer.py's own special-cased handling
    # (a plain case.case_no write, never part of the 28-factor row loop).
    fields["case_no"] = {"bbox": [85.78, 75.2, 140.59, 83.96], "kind": "header_text"}
    # 比較標的1's own "實例編號：" value cell (VALUE_STANDALONE "1" in the
    # manifest) is DELIBERATELY NOT wired here -- no Structured Result in
    # this codebase's domain model represents an "instance number" concept
    # distinct from the comparable's own parcel id (see docs/pdf/
    # OFFICIAL_PDF_FINAL_QUALITY_GATE_1_REPORT.md Task 4: SOURCE_NOT_
    # AVAILABLE, kept intentionally blank -- writing the literal "1" back
    # would be exactly the kind of fabricated value this round's Task 4/6
    # explicitly forbids).
    return {"row_count": len(merged_rows), "fields": fields}


def build_table1() -> dict:
    fields = {}
    for bbox, _orig, field_id in SUPPLEMENTAL_REDACTIONS_PAGE0:
        if field_id.startswith("regional_") and "本區段內" not in _orig and "○" not in _orig:
            fields[field_id] = {"bbox": bbox, "kind": "text"}
    # 建蔽率/容積率 values ("70%"/"240%" in the original) already matched
    # the mechanical VALUE_STANDALONE classifier's percent-shaped regex
    # (see scripts/analyze_official_template.py) and so were ALREADY
    # correctly white-boxed in the clean template with no supplemental
    # entry needed -- these bboxes are measured the same way as every
    # other Task-5 field, just via the mechanical path instead of the
    # manual one.
    fields["regional_building_coverage_ratio"] = {"bbox": [152.66, 133.5, 175, 140.39], "kind": "text"}
    fields["regional_floor_area_ratio"] = {"bbox": [152.66, 148.15, 178, 155.03], "kind": "text"}
    # Checkbox+distance facility fields (5): kind="checkbox_distance" --
    # renderer draws ○/● + digits fresh using the field's own label text
    # (stored here) rather than surgical glyph replacement.
    checkbox_labels = {
        "regional_department_store_proximity": "○本區段內　○本區段外(距　　M)",
        "regional_financial_institution_proximity": "○本區段內　○本區段外(距　　M)",
        "regional_entertainment_proximity": "○本區段內　○本區段外(距　　M)",
        "regional_exhibition_hotel_proximity": "○本區段內　○本區段外(距　　M)",
        "regional_interchange_proximity": "○本區段內　○本區段外(距　　M)",
        "regional_tourism_proximity": "○本區段內　○本區段外(距　　M)",
        "regional_parking_convenience": "○本區段內　○本區段外(距　　M)",
    }
    for bbox, _orig, field_id in SUPPLEMENTAL_REDACTIONS_PAGE0:
        if field_id in checkbox_labels:
            fields[field_id] = {
                "bbox": bbox, "kind": "checkbox_distance", "label_template": checkbox_labels[field_id],
            }
    # Core numeric/text fields already correctly caught by the mechanical
    # classifier (VALUE_AFTER_FULLWIDTH_COLON) -- measured directly too.
    fields["road_name"] = {"bbox": [146.9, 207.74, 210, 213.62], "kind": "text"}
    fields["regional_main_road_width"] = {"bbox": [241.1, 207.74, 300, 213.62], "kind": "text_with_unit", "unit": "M"}
    fields["segment_code"] = {"bbox": [141.4, 87.7, 185, 96.0], "kind": "text"}
    fields["segment_scope"] = {"bbox": [279.6, 83.7, 553.1, 100.1], "kind": "text_wrap"}
    fields["city_district"] = {"bbox": [36.1, 70.8, 130, 79.1], "kind": "text"}

    # TABLE1-SAFE-WIRING-1: 電業設施 (substation/gas_tank) + 殯葬設施
    # (cemetery/funeral_home/crematorium/columbarium) evidence rows.
    # name_bbox/checkbox_bbox measured directly against the ORIGINAL
    # 查估書表範本.pdf via PyMuPDF words/rawdict (see this round's trace --
    # not a visual guess): each row's static "名稱：" prefix survives in
    # the clean template for funeral_home/crematorium/columbarium (their
    # VALUE_AFTER_FULLWIDTH_COLON span split the label from the "無"
    # example value; see clean_region_dump), but does NOT survive for
    # substation/gas_tank/cemetery (their "名稱：<value> <checkbox clause>"
    # was one single merged CHECKBOX_GLYPH span in the source, fully
    # redacted) -- name_has_prefix_label records this so
    # official_pdf_renderer.py never draws a duplicate "名稱：" over the
    # template's own surviving one. circle_bbox (funeral rows only) is the
    # exact bbox of the row-selector ○/● glyph the redaction script above
    # now narrows its redaction to -- the row's own label text
    # (墓地/殯儀館/火葬場/納骨塔) sits immediately after it and is left
    # completely untouched by this round's fix.
    # Cell widths verified (this round) via fitz.Font.text_length at the
    # profile's own min_font_size (4.2pt, the size official_pdf_renderer.py
    # always uses for these rows via _draw_checkbox_distance): the
    # "○本區段內　○本區段外(距 NNN M)" clause measures ~64.6pt and a
    # 10-CJK-character facility name measures ~42-55pt at that size, both
    # comfortably inside the widths below. checkbox_bbox's x1=510 stays
    # clear of a real, found-this-round residual: funeral_home/
    # crematorium's rows each still have their template's own static
    # " M)" unit-suffix surviving at x=513.2-519.13 (left untouched by
    # this round's redaction fix -- it was never part of the same
    # CHECKBOX_GLYPH span as their row-selector label) -- writing up to
    # only x1=510 never collides with it.
    fields["substation"] = {
        "kind": "utility_facility_evidence",
        "name_bbox": [372.43, 258.98, 437.0, 264.86],
        "checkbox_bbox": [440.0, 258.98, 510.0, 264.86],
        "name_has_prefix_label": False,
    }
    fields["gas_tank"] = {
        "kind": "utility_facility_evidence",
        "name_bbox": [372.43, 289.94, 437.0, 295.82],
        "checkbox_bbox": [440.0, 289.94, 510.0, 295.82],
        "name_has_prefix_label": False,
    }
    fields["cemetery"] = {
        "kind": "funeral_facility_evidence",
        "circle_bbox": [330.91, 313.34, 336.79, 319.22],
        "name_bbox": [372.43, 313.34, 437.0, 319.22],
        "checkbox_bbox": [440.0, 313.34, 510.0, 319.22],
        "name_has_prefix_label": False,
    }
    fields["funeral_home"] = {
        "kind": "funeral_facility_evidence",
        "circle_bbox": [330.91, 327.98, 336.79, 333.86],
        "name_bbox": [391.0, 327.98, 437.0, 333.86],
        "checkbox_bbox": [440.0, 327.98, 510.0, 333.86],
        "name_has_prefix_label": True,
    }
    fields["crematorium"] = {
        "kind": "funeral_facility_evidence",
        "circle_bbox": [330.91, 342.64, 336.79, 348.52],
        "name_bbox": [391.0, 342.64, 437.0, 348.52],
        "checkbox_bbox": [440.0, 342.64, 510.0, 348.52],
        "name_has_prefix_label": True,
    }
    fields["columbarium"] = {
        "kind": "funeral_facility_evidence",
        "circle_bbox": [330.91, 357.28, 336.79, 363.16],
        "name_bbox": [391.0, 357.28, 437.0, 363.16],
        "checkbox_bbox": [440.0, 357.28, 510.0, 363.16],
        "name_has_prefix_label": True,
    }

    # TABLE1-MAJOR-STATION-1: 大型車站's 高鐵站/火車站/捷運站 rows. Each
    # row's own label ("無高鐵站"/"無火車站"/"無捷運站") is the template's
    # PERMANENT default text (present on every copy of this form; see
    # scripts/build_clean_official_template.py's KNOWN_LABEL_PREFIX_
    # CIRCLE_OVERRIDES, which now narrows redaction to ONLY each row's
    # leading circle glyph, same technique as the 4 funeral rows). name_
    # bbox therefore OVERLAYS that default text (never appended after
    # it) only when a real match exists -- official_pdf_renderer.py must
    # draw the full replacement "●{station name}" there, not just the
    # name alone, exactly mirroring row 3 in this same section (客運/
    # 國光客運金山站, out of this round's scope but visible proof of the
    # template's own convention: found -> circle flips to ● and the
    # default "無..." text is replaced by the real name). checkbox_bbox
    # widened to x1=256 (not just 231.66, the original checkbox clause's
    # own span) because this round ALSO cleared the orphaned " M)" tail
    # fragment that used to sit at x=252.12-258.06 for these 3 rows
    # specifically (see build_clean_official_template.py) -- confirmed
    # via word-level re-inspection that nothing else occupies that zone.
    # 4th row (客運/國光客運金山站, y=266.54) is deliberately NOT given a
    # profile entry here -- 客運/bus-terminal has no official_facility_
    # subtype bucket at all (see scripts/sync_facility_dataset.py's
    # _LANDMARK_TYPE_TO_SUBTYPE) and is out of this round's scope.
    fields["major_station_hsr"] = {
        "kind": "major_station_evidence",
        "circle_bbox": [99.38, 237.26, 105.26, 243.14],
        "name_bbox": [105.38, 237.26, 153.0, 243.14],
        "checkbox_bbox": [155.68, 237.26, 256.0, 243.14],
        "default_label": "無高鐵站",
    }
    fields["major_station_tra"] = {
        "kind": "major_station_evidence",
        "circle_bbox": [99.38, 251.9, 105.26, 257.78],
        "name_bbox": [105.38, 251.9, 153.0, 257.78],
        "checkbox_bbox": [155.68, 251.9, 256.0, 257.78],
        "default_label": "無火車站",
    }
    fields["major_station_mrt"] = {
        "kind": "major_station_evidence",
        "circle_bbox": [99.38, 281.18, 105.26, 287.06],
        "name_bbox": [105.38, 281.18, 153.0, 287.06],
        "checkbox_bbox": [155.68, 281.18, 256.0, 287.06],
        "default_label": "無捷運站",
    }
    return {"fields": fields}


def build_table4() -> dict:
    fields = {}
    for bbox, _orig, field_id in SUPPLEMENTAL_REDACTIONS_PAGE2:
        fields[field_id] = {"bbox": bbox, "kind": "text"}
    fields["case_no"]["kind"] = "text"
    # OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 5: 表4頁首「估價基準日：」
    # value cell -- a SOURCE_AVAILABLE_RENDERER_GAP distinct from (and
    # previously confused with) the footer "填寫日期" cell above (see
    # build_clean_official_template.py's SUPPLEMENTAL_REDACTIONS_PAGE2 for
    # why they are different concepts). bbox is the EXACT value_bbox the
    # mechanical VALUE_STANDALONE classifier already found and redacted
    # for this span (original_text "1140901" -- matches case.
    # appraisal_base_date exactly -- see redaction_manifest.json).
    fields["appraisal_base_date"] = {"bbox": [542.86, 32.13, 570.94, 41.01], "kind": "text"}

    # "computed" fields: raw individual-factor values NOT already in
    # SUPPLEMENTAL_REDACTIONS_PAGE2 (建蔽率/容積率/有無禁限建), plus every
    # already-computed FormCompletionResult field 表4 displays (per-row
    # differential rate, 區域因素調整百分率, 試算價格, 比準地比較價格,
    # 比較標的權重, 土地正常單價, 調整至估價基準日單價, 調整百分率) --
    # ALL of these bboxes were ALREADY correctly white-boxed by the
    # mechanical VALUE_STANDALONE classifier (percent/number-shaped), so
    # no additional entry in SUPPLEMENTAL_REDACTIONS_PAGE2 was needed for
    # them, only a profile entry telling the renderer where to write.
    computed = {
        "individual_building_coverage_ratio[base]": ([247.13, 326.56, 257.87, 333.64], "raw", "base"),
        "individual_building_coverage_ratio[comp]": ([374.35, 326.56, 385.09, 333.64], "raw", "comp"),
        "individual_floor_area_ratio[base]": ([245.33, 338.08, 259.67, 345.16], "raw", "base"),
        "individual_floor_area_ratio[comp]": ([372.55, 338.08, 386.89, 345.16], "raw", "comp"),
        "individual_construction_restriction[base]": ([248.93, 349.6, 300, 356.68], "raw", "base"),
        "individual_construction_restriction[comp]": ([376.15, 349.6, 425, 356.68], "raw", "comp"),
        # OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 5/6: 面積(M2)/寬度(M)/
        # 深度(M) -- a SOURCE_AVAILABLE_RENDERER_GAP, not a missing source
        # (case.base_parcel_factors/comparable_factors already carry
        # individual_land_area/individual_land_width/individual_land_depth
        # for both base and comparable -- see data/golden/golden_case_
        # input.py). These bboxes were ALREADY correctly white-boxed by
        # the mechanical VALUE_STANDALONE classifier (numeric-shaped
        # "113.21"/"111.85"/"5"/"7"/"23"/"16" in the original template --
        # see data/templates/official_appraisal_form_v1.redaction_
        # manifest.json), they simply never had a profile entry telling
        # the renderer where to write the value back. The row's own
        # STATIC label already states the unit ("面積(M2)"/"寬度(M)"/
        # "深度(M)"), matching the original example's own bare-number
        # convention (no cell ever showed "5M", only "5") -- omit_unit
        # skips FactorInput.unit here so the renderer doesn't re-append a
        # redundant unit, which for 寬度's narrow bbox (originally sized
        # for a single glyph like "5") genuinely overflowed once "M" was
        # appended.
        "individual_land_area[base]": ([242.45, 124.15, 263.34, 133.19], "raw", "base"),
        "individual_land_area[comp]": ([369.19, 123.19, 390.08, 132.23], "raw", "comp"),
        "individual_land_width[base]": ([250.13, 135.31, 254.87, 144.35], "raw", "base"),
        "individual_land_width[comp]": ([377.35, 135.31, 382.09, 144.35], "raw", "comp"),
        "individual_land_depth[base]": ([248.33, 147.43, 256.67, 156.47], "raw", "base"),
        "individual_land_depth[comp]": ([375.55, 147.43, 383.89, 156.47], "raw", "comp"),
        # OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 5/6: the 7 「接近條件」/
        # 「面前道路」rows (面前道路寬度/學校/市場/公園/車站/商圈/嫌惡設施)
        # each have TWO separate value cells in the original template: a
        # NAME cell (already wired via SUPPLEMENTAL_REDACTIONS_PAGE2 --
        # "individual_X.name[base/comp]"/"individual_frontage_road_width.
        # road_name[base/comp]" -- genuinely SOURCE_NOT_AVAILABLE, no
        # per-comparable named-facility concept exists anywhere in this
        # codebase's domain model, correctly left blank) and a DISTANCE/
        # WIDTH NUMBER cell immediately followed by the template's own
        # STATIC "M" unit label. That second cell is a
        # SOURCE_AVAILABLE_RENDERER_GAP: case.base_parcel_factors/
        # comparable_factors already carry individual_frontage_road_width/
        # individual_school_proximity/individual_market_proximity/
        # individual_park_proximity/individual_station_proximity/
        # individual_commercial_district_proximity/individual_nuisance_
        # facility (all real Golden Case distances in meters) -- it simply
        # never had ITS OWN profile entry (only the .name and .diff_rate
        # cells did). Bboxes are the EXACT value_bbox the mechanical
        # VALUE_STANDALONE classifier already found and redacted for each
        # (original_text "18"/"6"/"150"/"100"/"30"/"92"/"190"/"200"/"80"/
        # "190"/"0"/"0"/"260"/"80" -- see redaction_manifest.json). omit_unit
        # again because "M" is the row's own static label, not part of the
        # value in the original example.
        "individual_frontage_road_width[base]": ([271.13, 209.02, 279.47, 218.06], "raw", "base"),
        "individual_frontage_road_width[comp]": ([400.99, 209.02, 405.73, 218.06], "raw", "comp"),
        "individual_school_proximity[base]": ([269.33, 222.1, 281.27, 231.14], "raw", "base"),
        "individual_school_proximity[comp]": ([397.39, 222.1, 409.33, 231.14], "raw", "comp"),
        "individual_market_proximity[base]": ([271.13, 235.18, 279.47, 244.22], "raw", "base"),
        "individual_market_proximity[comp]": ([399.19, 235.18, 407.53, 244.22], "raw", "comp"),
        "individual_park_proximity[base]": ([269.33, 248.26, 281.27, 257.3], "raw", "base"),
        "individual_park_proximity[comp]": ([397.39, 248.26, 409.33, 257.3], "raw", "comp"),
        "individual_station_proximity[base]": ([271.13, 261.34, 279.47, 270.38], "raw", "base"),
        "individual_station_proximity[comp]": ([397.39, 261.34, 409.33, 270.38], "raw", "comp"),
        "individual_commercial_district_proximity[base]": ([272.93, 274.42, 277.67, 283.46], "raw", "base"),
        "individual_commercial_district_proximity[comp]": ([400.99, 274.42, 405.73, 283.46], "raw", "comp"),
        "individual_nuisance_facility[base]": ([269.33, 287.5, 281.27, 296.54], "raw", "base"),
        "individual_nuisance_facility[comp]": ([399.19, 287.5, 407.53, 296.54], "raw", "comp"),
    }
    omit_unit_fields = {
        "individual_land_area[base]", "individual_land_area[comp]",
        "individual_land_width[base]", "individual_land_width[comp]",
        "individual_land_depth[base]", "individual_land_depth[comp]",
        "individual_frontage_road_width[base]", "individual_frontage_road_width[comp]",
        "individual_school_proximity[base]", "individual_school_proximity[comp]",
        "individual_market_proximity[base]", "individual_market_proximity[comp]",
        "individual_park_proximity[base]", "individual_park_proximity[comp]",
        "individual_station_proximity[base]", "individual_station_proximity[comp]",
        "individual_commercial_district_proximity[base]", "individual_commercial_district_proximity[comp]",
        "individual_nuisance_facility[base]", "individual_nuisance_facility[comp]",
    }
    for field_id, (bbox, kind, side) in computed.items():
        fields[field_id] = {"bbox": bbox, "kind": kind, "side": side}
        if field_id in omit_unit_fields:
            fields[field_id]["omit_unit"] = True

    diff_rate_rows = {
        "individual_frontage_road_width": 209.62,
        "individual_school_proximity": 222.7,
        "individual_market_proximity": 235.78,
        "individual_park_proximity": 248.86,
        "individual_station_proximity": 261.94,
        "individual_commercial_district_proximity": 275.02,
        "individual_nuisance_facility": 288.1,
        "individual_parking_convenience": 301.18,
        "individual_zoning_designation": 313.92,
        "individual_building_coverage_ratio": 325.44,
        "individual_floor_area_ratio": 336.96,
        "individual_construction_restriction": 348.48,
    }
    for field_id, y0 in diff_rate_rows.items():
        fields[f"{field_id}.diff_rate"] = {
            "bbox": [451.03, y0 - 1.0, 469.49, y0 + CELL_H], "kind": "form_completion_field",
            "source_field_id_template": f"{field_id}_differential_rate_{{cid}}",
        }

    fields["region_adjustment_rate"] = {
        "bbox": [451.03, 112.5, 469.49, 121.31], "kind": "form_completion_field",
        "source_field_id_template": "region_adjustment_rate_{cid}",
    }
    # PDF-RUNTIME-1 fix: widened from the original example's own snug bbox
    # (x1=379.39, sized for "212,958") -- a real, unrounded Decimal value
    # like "212957.8338" is longer and, verified inside the REAL AWS
    # Lambda Container Image runtime (Amazon Linux 2023's google-noto-
    # sans-cjk-ttc-fonts package, NOT the Windows dev host's NotoSansTC-VF
    # .ttf -- the two font files' actual glyph metrics differ enough that
    # text fitting fine locally overflowed here), triggered FIELD_
    # OVERFLOW_MANUAL_REVIEW even after shrink-to-min-font-size. The next
    # value (comparable_weight) starts at x=436.51 with nothing in
    # between, so widening to x1=430 (was 379.39) leaves a safety gap.
    fields["trial_price"] = {
        "bbox": [356.11, 396.9, 430.0, 405.76], "kind": "form_completion_field",
        "source_field_id_template": "trial_price_{cid}",
    }
    fields["comparable_weight"] = {
        "bbox": [436.51, 396.9, 453.21, 405.76], "kind": "form_completion_field",
        "source_field_id_template": "comparable_weight_{cid}",
    }
    fields["base_parcel_comparison_price"] = {
        "bbox": [536.26, 410.1, 559.54, 418.96], "kind": "form_completion_field",
        "source_field_id_template": "base_parcel_comparison_price",
    }
    fields["adjusted_price"] = {
        "bbox": [382.03, 102.3, 410, 111.23], "kind": "form_completion_field",
        "source_field_id_template": "adjusted_price_{cid}",
    }
    # land_normal_price / price_date_adjustment_rate are NOT
    # FormCompletionResult fields at all (FormCompletionEngine only ever
    # CONSUMES them as inputs to trial_price()) -- they live directly on
    # CompetitionCase (case.comparable_land_normal_price[cid] / case.
    # comparable_price_date_adjustment_rate[cid], collect_data.py's own
    # request-body-typed values), so the renderer reads them from `case`,
    # not `form_completion_fields`.
    fields["land_normal_price"] = {
        "bbox": [382.03, 81.7, 410, 90.59], "kind": "case_field",
        "case_attr": "comparable_land_normal_price",
    }
    fields["price_date_adjustment_rate"] = {
        "bbox": [450.24, 91.6, 470, 100.52], "kind": "case_field",
        "case_attr": "comparable_price_date_adjustment_rate",
    }
    # OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 5/6: 交易日期 -- a
    # SOURCE_AVAILABLE_RENDERER_GAP. case.comparable_transaction_date is
    # the SAME kind of case-level dict as comparable_land_normal_price/
    # comparable_price_date_adjustment_rate right above (collect_data.py's
    # own request-body-typed value, never a FormCompletionResult field),
    # it simply never had a table4 profile entry. bbox is the EXACT
    # value_bbox the mechanical VALUE_STANDALONE classifier already found
    # and redacted for this span (original_text "114.05.28" -- see
    # data/templates/official_appraisal_form_v1.redaction_manifest.json).
    fields["comparable_transaction_date"] = {
        "bbox": [366.19, 92.87, 393.07, 101.11], "kind": "case_field",
        "case_attr": "comparable_transaction_date",
    }
    # FINAL GATE round: measured directly from docs/pdf/_template_survey/
    # page2_spans.json ("13.00%" under the "合計" row, comp1's column) --
    # this field genuinely exists in FormCompletionResult
    # (individual_adjustment_total_{cid}) and was previously just not
    # measured, not ambiguous.
    fields["individual_adjustment_total"] = {
        "bbox": [356.71, 371.3, 405, 380.2], "kind": "form_completion_field",
        "source_field_id_template": "individual_adjustment_total_{cid}",
    }
    return {"fields": fields}


def main():
    doc = fitz.open(SOURCE_PDF)
    profile = {
        "template_id": "NTPC_APPRAISAL_2026_V1",
        "template_version": "v1",
        "source_pdf_relpath": "data/sources/competition/查估書表範本.pdf",
        "source_pdf_sha256": _sha256(SOURCE_PDF),
        "clean_template_relpath": "data/templates/official_appraisal_form_v1.pdf",
        "clean_template_sha256": _sha256(CLEAN_PDF),
        "pages": {"table1": 0, "table5_2": 1, "table4": 2},
        "page_sizes_pt": {
            "0": [595.2, 841.68],  # A4 portrait
            "1": [595.2, 841.68],  # A4 portrait
            "2": [841.68, 595.2],  # A4 landscape
        },
        "font": {
            "family": "Noto Sans TC",
            "license": "SIL Open Font License 1.1 (redistributable)",
            "lambda_candidates": [
                "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/google-noto-vf/NotoSansTC%5Bwght%5D.ttf",
            ],
            "windows_dev_candidate": "C:\\Windows\\Fonts\\NotoSansTC-VF.ttf",
            "note": "Never bundled in this repo -- resolved from the runtime environment's already-installed system font (same convention as pdf/pdf_renderer.py's WeasyPrint path, which relies on backend/docker/pdf.Dockerfile's `dnf install google-noto-sans-cjk-ttc-fonts`). See docs/pdf/OFFICIAL_TEMPLATE_SURVEY.md for the full inventory.",
        },
        "overflow_policy": {
            "default_font_size": 7.2,
            "min_font_size": 4.2,
            "shrink_step": 0.3,
            "strategy": ["shrink_to_min_font_size", "wrap_within_bbox_height", "FIELD_OVERFLOW_MANUAL_REVIEW"],
        },
        "checkbox_glyphs": {"unchecked": "○", "checked": "●", "unchecked_square": "□"},
        "not_applicable_marker": "－",
        "table1": build_table1(),
        "table5_2": build_table5_2(doc),
        "table4": build_table4(),
    }
    os.makedirs(os.path.dirname(OUT_PROFILE), exist_ok=True)
    with open(OUT_PROFILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    print(f"wrote {OUT_PROFILE}")
    print(f"table5_2 row_count={profile['table5_2']['row_count']}")
    print(f"table1 field count={len(profile['table1']['fields'])}")
    print(f"table4 field count={len(profile['table4']['fields'])}")


if __name__ == "__main__":
    main()
