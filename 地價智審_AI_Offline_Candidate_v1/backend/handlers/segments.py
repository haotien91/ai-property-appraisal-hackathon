# -*- coding: utf-8 -*-
"""
segments.py — COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 14 API contract.

    GET /api/cases/{id}/segments               -> get_segments
    GET /api/cases/{id}/segments/{segment_code} -> get_segment

Thin handlers over competition_segments.py (mirrors facility_confirmation.py's
own thin-wrapper-over-repository shape). segment_code is always EXPLICIT in
the path -- never inferred. A case that never defined a segment map (every
legacy Jinshan case) returns {"case_no": ..., "segments": null} from
get_segments, 200 not 404 -- the CASE exists, it simply has no multi-segment
structure, which is a valid, unremarkable state (Task 16).
"""
from __future__ import annotations

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import competition_segments  # noqa: E402
from competition_segments import InvalidSegmentCodeError  # noqa: E402


def get_segments(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_segments"):
        if case_store.get_case_meta(case_no) is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")
        segment_map = competition_segments.get_segment_map(case_no)
        return response(200, {
            "case_no": case_no,
            "segments": segment_map.model_dump(mode="json") if segment_map else None,
        })


def get_segment(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    segment_code = event.get("pathParameters", {}).get("segment_code")
    with timed_step(case_no, "get_segment"):
        if case_store.get_case_meta(case_no) is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")
        try:
            segment = competition_segments.resolve_segment(case_no, segment_code)
        except InvalidSegmentCodeError as e:
            return error_response(400, "INVALID_SEGMENT_CODE", str(e))
        return response(200, {"case_no": case_no, "segment": segment.model_dump(mode="json")})
