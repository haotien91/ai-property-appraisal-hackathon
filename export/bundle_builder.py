# -*- coding: utf-8 -*-
"""
export/bundle_builder.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 1/19.

Builds ONE CaseExportBundle per case, reusing the EXACT SAME runtime
results already established and verified in C1/D1/E1 -- this module never
grades a factor, computes an adjustment, derives a weight, or evaluates
FAR. It only:
  (a) calls table51_analysis.build_table51_analysis_for_case() (C1),
  (b) calls table4_analysis.get_table4_analysis() (D1) and parses its
      already-serialized JSON response body,
  (c) calls table51_analysis._load_segment_regional_factors() per segment
      (the SAME per-segment Table3 raw-value loader shulin_official_pdf_
      handler.py already uses for the PDF's own Table3 pages),
  (d) walks the resulting Table51Analysis/Table4Analysis objects to collect
      every `requires_manual_review` factor/comparison into a flat
      manual_review_items list, PLUS the FAR and weight structural
      policies (Task 9/10) -- never from the legacy review()/REVIEW_RESULT
      record (see the module-level note below).

review()/get_result() note (F1's own identified gap, restated here since
this module is the reason CaseExportBundle.review can legitimately be
None): review() reads the case's bare "FACTORS" DynamoDB record, a shape
only the legacy single-comparable Jinshan flow ever writes. A Shulin
(segment-scoped) case never has one, so review() fails closed with a
clean 400 for such a case -- this is CORRECT, not a bug, and this module
never tries to paper over it by calling review() and hoping, or by
inventing a fabricated review result. CaseExportBundle.review is always
None for a Shulin case this round; review_status is always
"NOT_AVAILABLE_FOR_SHULIN_YET" for one. Building a segment-aware review()
replacement is explicitly out of this round's scope (see PHASE spec).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
for _p in (os.path.join(_REPO_ROOT, "backend", "handlers"), os.path.join(_REPO_ROOT, "engine"),
           os.path.join(_REPO_ROOT, "providers"), _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import case_store  # noqa: E402
import competition_segments  # noqa: E402
from table51_analysis import build_table51_analysis_for_case, _load_segment_regional_factors  # noqa: E402
import table4_analysis as table4_analysis_handler  # noqa: E402
from domain.models import Table4Analysis  # noqa: E402

from export.models import CaseExportBundle, ManualReviewItem, SCHEMA_VERSION  # noqa: E402


class CaseNotFoundError(Exception):
    pass


class SegmentMapRequiredForExportError(Exception):
    """H1's export bundle is only defined for segment-scoped (Shulin-style)
    cases this round -- a legacy Jinshan case has no Table51Analysis/
    Table4Analysis at all, so there is nothing to export via this path."""


def _far_manual_review_items(table4_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table4_analysis_dict.get("comparisons", []):
        for fr in comp.get("individual_factor_results", []):
            if fr.get("is_far_special_policy"):
                items.append(ManualReviewItem(
                    source="table4_far", segment_code=comp.get("comparable_segment_code"),
                    field_id=fr.get("field_id"), factor_name=fr.get("factor_name"),
                    reason=fr.get("reason") or "LAND_DEVELOPMENT_ANALYSIS_REQUIRED",
                ))
    return items


def _weight_manual_review_items(table4_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table4_analysis_dict.get("comparisons", []):
        if comp.get("weight_status") != "HUMAN_CONFIRMED":
            items.append(ManualReviewItem(
                source="table4_weight", segment_code=comp.get("comparable_segment_code"),
                reason=f"WEIGHT_NOT_HUMAN_CONFIRMED (status={comp.get('weight_status')})",
            ))
    return items


def _table51_manual_review_items(table51_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table51_analysis_dict.get("comparisons", []):
        seg = comp.get("comparable_segment_code")
        for fr in comp.get("factor_results", []):
            if fr.get("requires_manual_review"):
                items.append(ManualReviewItem(
                    source="table51_factor", segment_code=seg, field_id=fr.get("field_id"),
                    factor_name=fr.get("factor_name"), reason=fr.get("reason") or "MANUAL_REVIEW_REQUIRED",
                ))
    return items


def _table4_manual_review_items(table4_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table4_analysis_dict.get("comparisons", []):
        seg = comp.get("comparable_segment_code")
        for fr in comp.get("individual_factor_results", []):
            if fr.get("requires_manual_review") and not fr.get("is_far_special_policy"):
                items.append(ManualReviewItem(
                    source="table4_factor", segment_code=seg, field_id=fr.get("field_id"),
                    factor_name=fr.get("factor_name"), reason=fr.get("reason") or "MANUAL_REVIEW_REQUIRED",
                ))
    return items


def build_case_export_bundle(case_no: str) -> CaseExportBundle:
    meta = case_store.get_case_meta(case_no)
    if meta is None:
        raise CaseNotFoundError(case_no)

    segment_map = competition_segments.get_segment_map(case_no)
    if segment_map is None:
        raise SegmentMapRequiredForExportError(case_no)

    table51_analysis, _rule_resolution = build_table51_analysis_for_case(case_no, meta)
    table51_dict = json.loads(table51_analysis.model_dump_json())

    table4_resp = table4_analysis_handler.get_table4_analysis({"pathParameters": {"id": case_no}}, None)
    if table4_resp.get("statusCode") != 200:
        raise RuntimeError(f"table4_analysis failed: {table4_resp.get('body')}")
    table4_analysis = Table4Analysis.model_validate(json.loads(table4_resp["body"])["analysis"])
    table4_dict = json.loads(table4_analysis.model_dump_json())

    ordered_segments = sorted(segment_map.comparables, key=lambda s: s.comparison_index or 0)
    ordered_segments = ordered_segments + [segment_map.base_segment]

    segments: Dict[str, Dict[str, Any]] = {}
    table3: Dict[str, Dict[str, Any]] = {}
    for seg in ordered_segments:
        segments[seg.segment_code] = {
            "segment_code": seg.segment_code, "segment_role": seg.segment_role,
            "comparison_index": seg.comparison_index, "district": seg.district,
            "land_use_type": seg.land_use_type, "parcel_ids": seg.parcel_ids,
        }
        factors = _load_segment_regional_factors(case_no, seg.segment_code)
        table3[seg.segment_code] = {
            "segment_code": seg.segment_code,
            "factors": [json.loads(fi.model_dump_json()) for fi in (factors or [])],
        }

    manual_review_items: List[ManualReviewItem] = []
    manual_review_items += _table51_manual_review_items(table51_dict)
    manual_review_items += _table4_manual_review_items(table4_dict)
    manual_review_items += _far_manual_review_items(table4_dict)
    manual_review_items += _weight_manual_review_items(table4_dict)

    provenance = {
        "table51_source": "table51_analysis.py::build_table51_analysis_for_case() (C1 runtime, unchanged)",
        "table4_source": "table4_analysis.py::get_table4_analysis() (D1 runtime, unchanged)",
        "table3_source": "table51_analysis.py::_load_segment_regional_factors() (CompetitionProvided precedence already applied)",
        "review_source": "NOT_AVAILABLE -- see review_status",
        "official_pdf_source": "pdf/shulin_official_pdf_renderer.py (E1, unchanged -- this bundle never re-renders it)",
    }

    return CaseExportBundle(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc),
        case_no=case_no,
        profile_id=meta.get("rule_profile_id"),
        appraisal_base_date=meta.get("appraisal_base_date"),
        base_segment_code=segment_map.base_segment.segment_code,
        comparable_segment_codes=[c.segment_code for c in segment_map.comparables],
        case={
            "case_no": case_no, "city": meta.get("city"), "district": meta.get("district"),
            "land_use_type": meta.get("land_use_type"), "appraisal_period": meta.get("appraisal_period"),
            "rule_profile_id": meta.get("rule_profile_id"),
        },
        segments=segments,
        table3=table3,
        table5_1=table51_dict,
        table4=table4_dict,
        review=None,
        review_status="NOT_AVAILABLE_FOR_SHULIN_YET",
        manual_review_items=manual_review_items,
        provenance=provenance,
    )
