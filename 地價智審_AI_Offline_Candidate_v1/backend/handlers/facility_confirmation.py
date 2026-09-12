# -*- coding: utf-8 -*-
"""
facility_confirmation.py — FACILITY-CONFIRMATION-GATE-1 backend interface.

Three thin handlers over facility_confirmation_repository.py's CONFIRMED
Gate (mirrors evaluation_standard.py's own thin-wrapper-over-repository
shape almost exactly):

    get_facility_candidates     -- refreshes candidates from current
                                    FACTORS, returns every subtype's record
    confirm_facility_candidate  -- PENDING -> CONFIRMED
    reject_facility_candidate   -- (PENDING|CONFIRMED) -> REJECTED

This file contains NO evidence-derivation or selection logic of its own
-- that lives entirely in facility_confirmation_repository.py, which is
the only module here that reads FACTORS.points/official_facility_
evidence. official_pdf_renderer.py never runs from this file's code path;
pdf_handler.py reads confirmed selections independently via
facility_confirmation_repository.default_facility_confirmation_repository()
.get_confirmed_selections().
"""
from __future__ import annotations

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402

from facility_confirmation_repository import (  # noqa: E402
    default_facility_confirmation_repository,
    FacilityConfirmationNotFoundError, FacilityConfirmationEmptyCandidateError,
)
import competition_segments  # noqa: E402
from competition_segments import InvalidSegmentCodeError  # noqa: E402


def _segment_gate(case_no: str, segment_code):
    """COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1 Task 1: a
    Competition case (competition_segments.is_competition_case() --
    has its own CompetitionSegmentMap, or a known competition
    rule_profile_id) may NEVER address facility-confirmation scope via
    the legacy "FACILITY_CONFIRMATION#<subtype>" key -- segment_code is
    mandatory for it, full stop. A genuinely legacy (non-Competition)
    case keeps using that key exactly as before when segment_code is
    omitted (Task 7/16). Returns an error_response dict to return
    immediately, or None to proceed."""
    if segment_code:
        try:
            competition_segments.resolve_segment(case_no, segment_code)
        except InvalidSegmentCodeError as e:
            return error_response(400, "INVALID_SEGMENT_CODE", str(e))
        return None
    if competition_segments.is_competition_case(case_no):
        return error_response(
            400, "SEGMENT_CODE_REQUIRED",
            f"案件 {case_no} 為 Competition case（已定義 segment map 或 rule_profile_id），"
            f"facility-candidates 操作必須提供 segment_code，不得使用 legacy key",
        )
    return None


def _record_to_dto(record) -> dict:
    return {
        "subtype": record.subtype,
        "segment_code": record.segment_code,
        "status": record.status.value,
        "candidate": record.candidate.model_dump(mode="json") if record.candidate else None,
        "confirmed_selection": record.confirmed_selection.model_dump(mode="json") if record.confirmed_selection else None,
        "reviewer_note": record.reviewer_note,
        "confirmed_by": record.confirmed_by,
        "rejected_by": record.rejected_by,
        "stale": record.stale,
        # FACILITY-STALE-CONFIRMATION-GATE-1 Task 5: derived, not stored --
        # true exactly when this record is CONFIRMED but withheld from the
        # official PDF (get_active_confirmed_selections()) because new
        # evidence has since diverged from what was confirmed. A future
        # frontend can show a "請重新確認" prompt keyed off this alone,
        # without re-deriving the CONFIRMED+stale condition itself.
        "requires_reconfirmation": record.status.value == "CONFIRMED" and record.stale,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def get_facility_candidates(event, context):
    """GET /api/cases/{id}/facility-candidates, or (COMPETITION-DOMAIN-
    MULTI-SEGMENT-B1 Task 14) GET /api/cases/{id}/segments/{segment_code}/
    facility-candidates -- segment_code is an EXPLICIT, OPTIONAL
    pathParameter (Task 16: a caller that omits it, e.g. every existing
    legacy Jinshan route, gets byte-identical behavior).

    Refreshes every subtype's `candidate` snapshot from the case's (or
    segment's) CURRENT FACTORS record (never touches status/confirmed_
    selection -- see facility_confirmation_repository.get_or_refresh_
    candidates()) and returns all 8 records (utility x2 / funeral x4 /
    major_station x2). 404 only when the case has never even run
    collect_data for this scope (no FACTORS record at all) -- an existing
    case/segment with genuinely no facility evidence still returns 200
    with every `candidate`=None."""
    case_no = event.get("pathParameters", {}).get("id")
    segment_code = event.get("pathParameters", {}).get("segment_code")
    with timed_step(case_no, "get_facility_candidates"):
        gate_error = _segment_gate(case_no, segment_code)
        if gate_error is not None:
            return gate_error
        factors_record = case_store.get_record(case_no, competition_segments.factors_sk(segment_code))
        if factors_record is None:
            return error_response(404, "FACTORS_NOT_FOUND", f"案件 {case_no} 尚未執行 collect_data，無 FACTORS 資料")
        repository = default_facility_confirmation_repository()
        records = repository.get_or_refresh_candidates(case_no, factors_record, segment_code=segment_code)
        return response(200, {
            "case_no": case_no, "segment_code": segment_code,
            "candidates": [_record_to_dto(r) for r in records],
        })


def confirm_facility_candidate(event, context):
    """POST /api/cases/{id}/facility-candidates/{subtype}/confirm, or
    POST /api/cases/{id}/segments/{segment_code}/facility-candidates/
    {subtype}/confirm. Body: {"confirmed_by": str}. Requires
    get_facility_candidates to have been called at least once for this
    (segment_code, subtype) scope -- never auto-creates-then-confirms."""
    case_no = event.get("pathParameters", {}).get("id")
    segment_code = event.get("pathParameters", {}).get("segment_code")
    subtype = event.get("pathParameters", {}).get("subtype")
    body = parse_body(event)
    confirmed_by = body.get("confirmed_by") or "unknown"
    with timed_step(case_no, "confirm_facility_candidate"):
        gate_error = _segment_gate(case_no, segment_code)
        if gate_error is not None:
            return gate_error
        repository = default_facility_confirmation_repository()
        try:
            record = repository.confirm(case_no, subtype, confirmed_by, segment_code=segment_code)
        except FacilityConfirmationNotFoundError as e:
            return error_response(404, "FACILITY_CANDIDATE_NOT_FOUND", str(e))
        except FacilityConfirmationEmptyCandidateError as e:
            return error_response(400, "FACILITY_CANDIDATE_EMPTY", str(e))
        return response(200, _record_to_dto(record))


def reject_facility_candidate(event, context):
    """POST /api/cases/{id}/facility-candidates/{subtype}/reject, or
    POST /api/cases/{id}/segments/{segment_code}/facility-candidates/
    {subtype}/reject.
    Body: {"reviewer_note": str (optional), "rejected_by": str (optional)}."""
    case_no = event.get("pathParameters", {}).get("id")
    segment_code = event.get("pathParameters", {}).get("segment_code")
    subtype = event.get("pathParameters", {}).get("subtype")
    body = parse_body(event)
    reviewer_note = body.get("reviewer_note")
    rejected_by = body.get("rejected_by")
    with timed_step(case_no, "reject_facility_candidate"):
        gate_error = _segment_gate(case_no, segment_code)
        if gate_error is not None:
            return gate_error
        repository = default_facility_confirmation_repository()
        try:
            record = repository.reject(case_no, subtype, reviewer_note=reviewer_note,
                                        rejected_by=rejected_by, segment_code=segment_code)
        except FacilityConfirmationNotFoundError as e:
            return error_response(404, "FACILITY_CANDIDATE_NOT_FOUND", str(e))
        return response(200, _record_to_dto(record))
