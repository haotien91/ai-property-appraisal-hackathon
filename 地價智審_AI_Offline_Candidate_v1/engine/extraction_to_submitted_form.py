# -*- coding: utf-8 -*-
"""
extraction_to_submitted_form.py — the reconstruction step: confirmed
extracted field values -> SubmittedFormData, the exact seam docs/phase6/
document_extraction_spec.md described ("TextractAdapter... ->
SubmittedFormData -> AuditEngine.review() ... 無需修改") but never built.

This module does NOT compute anything -- it only copies already-human-
cleared text values (via engine/human_confirmation.py's
resolve_confirmed_values(), never a raw unconfirmed extraction) into the
matching SubmittedFormData attribute.

表5-2 reconstruction (re-verified against the REAL AuditEngine._review_
regional_factors consumer -- see engine/audit_engine.py -- not assumed):
submitted_grades[(field_id, comparable_id)] (TEXT) and submitted_grade_
codes[(field_id, comparable_id)] (CODE) are both read there, independently,
against base_input.raw_value (比準地's own regional factor) -- Grade
Representation Contract Phase D's Check A (code identity) and Check B
(code/text self-consistency). Only BASE/GRADE_TEXT and BASE/GRADE_CODE
reconstruct (each replicated across every comparable_id, matching the
existing fixture pipeline's own pattern for text); COMPARABLE/GRADE_TEXT,
COMPARABLE/GRADE_CODE, and ADJUSTMENT_PCT (any subject) stay evidence-only
ExtractedFields -- there is still no AuditEngine consumer for a
COMPARABLE's own regional grade (identity or representation), so
extracting them ahead of that consumer is not an error, just unused
evidence, same principle as 表1/表4's already-extracted-but-unconsumed
fields below.
"""
from __future__ import annotations

import sys
import os
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    ExtractedField, HumanConfirmationRecord, ExtractionRole, ExtractionSubjectRole,
)
from engine.audit_engine import SubmittedFormData  # noqa: E402
from engine.human_confirmation import resolve_confirmed_values  # noqa: E402

# extraction field_id -> SubmittedFormData attribute name, for VALUE-role
# (extraction_subject_role=NONE) fields -- 表1/表4's flat single-value
# convention, unchanged. main_road_name/segment_avg_road_width/
# individual_land_normal_price/base_parcel_comparison_price/
# comparable_weight are extracted (proving the extraction layer itself
# works beyond the 4 fields AuditEngine currently validates) but have no
# SubmittedFormData slot to flow into yet -- extracting a field ahead of
# the audit layer consuming it is not an error, just unused evidence.
_FIELD_TO_SUBMITTED_ATTR = {
    "individual_zoning_designation": "submitted_land_use_zone",
    "individual_building_coverage_ratio": "submitted_building_coverage_rate",
    "individual_floor_area_ratio": "submitted_floor_area_ratio",
    "main_road_width": "submitted_main_road_width",
}


def build_submitted_form_from_extraction(
    case_no: str, fields: List[ExtractedField],
    confirmations: Optional[List[HumanConfirmationRecord]] = None,
    comparable_ids: Optional[List[str]] = None,
) -> SubmittedFormData:
    """Builds a SubmittedFormData from confirmed extraction output. A field
    still requiring manual review with no matching confirmation resolves to
    None (per resolve_confirmed_values), which AuditEngine/
    LandUseRatioValidator/RoadWidthValidator already handle correctly as
    "cannot verify" (MISSING/UNAVAILABLE), never silently treated as a
    submitted 0 or empty string.

    comparable_ids defaults to None (treated as empty) -- callers that only
    care about 表1/表4 VALUE-role fields (existing tests, existing callers)
    are unaffected; 表5-2 reconstruction requires the caller's own
    CompetitionCase.comparable_ids, exactly like build_demo_submission.py's
    fixture builder does, since submitted_grades/submitted_totals are keyed
    by comparable_id, never guessed from the PDF's own column layout."""
    resolved = resolve_confirmed_values(fields, confirmations)
    submitted = SubmittedFormData(case_no=case_no)

    for field_id, attr_name in _FIELD_TO_SUBMITTED_ATTR.items():
        identity = (field_id, ExtractionRole.VALUE, ExtractionSubjectRole.NONE, None)
        if identity in resolved:
            setattr(submitted, attr_name, resolved[identity])

    comparable_ids = comparable_ids or []
    for f in fields:
        identity = (f.field_id, f.extraction_role, f.extraction_subject_role, f.comparable_slot)

        if f.extraction_role == ExtractionRole.GRADE_TEXT and f.extraction_subject_role == ExtractionSubjectRole.BASE:
            if identity not in resolved or resolved[identity] is None:
                continue
            value = resolved[identity]
            for comparable_id in comparable_ids:
                submitted.submitted_grades[(f.field_id, comparable_id)] = value

        elif f.extraction_role == ExtractionRole.GRADE_CODE and f.extraction_subject_role == ExtractionSubjectRole.BASE:
            if identity not in resolved or resolved[identity] is None:
                continue
            value = resolved[identity]
            for comparable_id in comparable_ids:
                submitted.submitted_grade_codes[(f.field_id, comparable_id)] = value

        elif f.extraction_role == ExtractionRole.TOTAL and f.extraction_subject_role == ExtractionSubjectRole.COMPARABLE:
            if identity not in resolved or resolved[identity] is None:
                continue
            slot = f.comparable_slot
            if slot is None or slot < 1 or slot > len(comparable_ids):
                continue  # PDF shows a comparable column the structured case doesn't have -- not guessed
            comparable_id = comparable_ids[slot - 1]
            submitted.submitted_totals[f"regional_total_{comparable_id}"] = resolved[identity]

        # COMPARABLE/GRADE_CODE, COMPARABLE/GRADE_TEXT, ADJUSTMENT_PCT (any
        # subject) -- evidence only this round, never written into
        # SubmittedFormData (no AuditEngine consumer exists for them yet).

    return submitted
