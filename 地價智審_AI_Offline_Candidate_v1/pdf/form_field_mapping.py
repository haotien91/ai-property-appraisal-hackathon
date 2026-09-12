# -*- coding: utf-8 -*-
"""
FormFieldMapping — classifies every FieldCompletion as belonging to a form
section and as AUTOMATIC (produced deterministically by Rule/Adjustment/
Calculation Engine) or MANUAL (requires estimator judgment, e.g.
price_formation_similarity). This classification drives both the PDF
renderer's visual styling (manual fields are highlighted) and the
Automatic/Manual disclosure table required by the Phase 5 FALLBACK spec.
"""
from __future__ import annotations
import sys
import os
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import FieldCompletion, SourceType  # noqa: E402
from pdf.coordinate_mapping import FieldCoordinate, build_coordinate_mapping  # noqa: E402


MANUAL_SOURCE_TYPES = {
    SourceType.AI_SUGGESTED_HUMAN_CONFIRMED.value,
    SourceType.MANUAL_SIGNOFF.value,
    "AI輔助建議＋人工核定",  # matches source strings built ad hoc in FormCompletionEngine
    "使用者輸入",
}


def classify_fill_mode(field: FieldCompletion) -> str:
    """AUTOMATIC if the value was produced by Rule/Adjustment/Calculation
    Engine; MANUAL if it required (or still requires) estimator judgment or
    direct user input. Never guesses -- based purely on the field's own
    recorded `source` string and status, which the engines are responsible
    for setting honestly (see engine/form_completion_engine.py)."""
    if field.status.value in ("MANUAL_REVIEW_REQUIRED", "UNKNOWN"):
        return "MANUAL"
    source_lower = field.source or ""
    for marker in MANUAL_SOURCE_TYPES:
        if marker in source_lower:
            return "MANUAL"
    return "AUTOMATIC"


def map_field_to_section(field_id: str, form: str) -> str:
    """Best-effort human-readable section label derived from the field_id's
    naming convention (see schemas/field_dictionary.json naming_convention).
    Used only for PDF layout grouping, not for any business logic."""
    fid = field_id
    if form == "表4":
        if fid.startswith("individual_land") or fid.startswith("individual_street") :
            return "宗地條件"
        if fid.startswith("individual_road") or fid.startswith("individual_frontage"):
            return "道路條件"
        if any(fid.startswith(p) for p in ("individual_school", "individual_market",
                                            "individual_park", "individual_station",
                                            "individual_commercial_district")):
            return "接近條件"
        if fid.startswith("individual_nuisance") or fid.startswith("individual_parking"):
            return "周邊環境條件"
        if any(fid.startswith(p) for p in ("individual_zoning", "individual_building",
                                            "individual_floor", "individual_construction")):
            return "行政條件"
        if any(k in fid for k in ("price", "trial", "adjustment_abs", "comparable_weight",
                                   "region_adjustment", "base_parcel_comparison")):
            return "比較價格計算"
        return "其他"
    if form == "表5-2":
        return "區域因素"
    return "地價區段勘查表"


def build_field_mappings(fields: List[FieldCompletion], form: str) -> List[Tuple[FieldCompletion, FieldCoordinate]]:
    """Returns (field, coordinate) pairs, page_hint fixed at 1 since each
    form is rendered as its own self-contained flowing document (weasyprint
    paginates automatically; we don't attempt to force a specific page
    number, since we don't have an official page grid to match)."""
    result = []
    for f in fields:
        section = map_field_to_section(f.field_id, form)
        fill_mode = classify_fill_mode(f)
        coord = build_coordinate_mapping(
            form=form, section=section, row_label=f.chinese_label,
            field_id=f.field_id, page_hint=1, fill_mode=fill_mode,
        )
        result.append((f, coord))
    return result
