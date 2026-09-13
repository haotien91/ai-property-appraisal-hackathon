# -*- coding: utf-8 -*-
"""
cases.py — Lambda handlers for GET /api/cases, POST /api/cases,
GET /api/cases/{id}.

Deployment note: this file (and every other handler in backend/handlers/)
expects the repository root (domain/, engine/, providers/, pdf/, data/,
schemas/) to be present on sys.path -- see infra/template.yaml's Lambda
Layer definition, which packages those directories once and shares them
across all handler functions rather than duplicating ~2MB of engine code
into every function's deployment zip.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, "/opt/python")  # Lambda Layer mount point, see infra/template.yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402

from domain.models import CompetitionCase, CompetitionSegmentMap  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from competition_rule_profiles import COMPETITION_RULE_PROFILE_REGISTRY  # noqa: E402
import competition_segments  # noqa: E402


def list_cases(event, context):
    with timed_step("*", "list_cases"):
        query = event.get("queryStringParameters") or {}
        limit = int(query.get("page_size", 20))
        cases = case_store.list_case_metas(limit=limit)
        return response(200, {
            "cases": cases, "total": len(cases),
            "page": int(query.get("page", 1)), "page_size": limit,
        })


def create_case(event, context):
    body = parse_body(event)
    case_no = body.get("case_no")
    with timed_step(case_no or "UNKNOWN", "create_case"):
        try:
            # Validate against the same CompetitionCase shape used throughout
            # Phases 4-6 -- base_parcel_factors/comparable_factors are filled
            # in later via collect-data, so they default to empty here.
            case = CompetitionCase(
                case_no=case_no,
                appraisal_period=body.get("appraisal_period", ""),
                appraisal_base_date=body.get("appraisal_base_date", ""),
                segment_code=body.get("segment_code", ""),
                segment_scope=body.get("segment_scope", ""),
                city=body.get("city", ""),
                district=body.get("district", ""),
                land_use_type=body.get("land_use_type", ""),
                base_parcel_id=body.get("base_parcel_id", "TBD"),
                comparable_ids=body.get("comparable_ids", []),
            )
        except ValidationError as e:
            return error_response(400, "VALIDATION_ERROR", f"案件資料驗證失敗：{e.errors()[0]['msg']}")

        if case_store.get_case_meta(case.case_no) is not None:
            return error_response(400, "VALIDATION_ERROR", f"案號 {case.case_no} 已存在", field_id="case_no")

        # SHULIN-COMPETITION-RULE-PACK-A2-FINAL-GATE-1 Task 1: rule_profile_id
        # is EXPLICIT and OPTIONAL, accepted only from the request body itself
        # -- never inferred from case_no/district/land_use_type (Task 10's own
        # requirement). An omitted value keeps every existing (legacy Jinshan)
        # caller's behavior 100% unchanged (meta simply has no such key, and
        # build_rule_engine_for_case() treats a missing/None rule_profile_id
        # exactly as before). An explicitly-supplied but unknown profile id is
        # rejected at creation time rather than silently stored and only
        # discovered as a failure later at analyze/complete-form/review time.
        rule_profile_id = body.get("rule_profile_id")
        if rule_profile_id and rule_profile_id not in COMPETITION_RULE_PROFILE_REGISTRY:
            return error_response(
                400, "VALIDATION_ERROR",
                f"rule_profile_id={rule_profile_id!r} 不是已知的 competition rule profile",
                field_id="rule_profile_id",
            )

        # COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 1/2: EXPLICIT, OPTIONAL
        # segment map -- {"base_segment": {...}, "comparables": [...]}. A
        # caller that omits "segments" entirely (every existing legacy
        # Jinshan caller) gets byte-identical behavior: no SEGMENTS record
        # is ever written, and every downstream handler (collect_data.py,
        # facility_confirmation.py) treats a missing segment map exactly
        # as before. segment_role is validated by CompetitionSegmentMap's
        # own Pydantic model (never inferred from segment_code's string
        # shape here or anywhere else -- Task 1's explicit prohibition).
        segments_body = body.get("segments")
        segment_map = None
        if segments_body is not None:
            try:
                segment_map = CompetitionSegmentMap(case_id=case.case_no, **segments_body)
            except ValidationError as e:
                return error_response(
                    400, "VALIDATION_ERROR", f"segments 驗證失敗：{e.errors()[0]['msg']}", field_id="segments",
                )

        meta = {
            "case_no": case.case_no, "segment_code": case.segment_code,
            "appraisal_period": case.appraisal_period,
            "appraisal_base_date": case.appraisal_base_date,
            "base_parcel_id": case.base_parcel_id, "comparable_ids": case.comparable_ids,
            "district": case.district, "status": "IN_PROGRESS",
            # city/segment_scope: needed by collect_data.py to geocode a
            # center coordinate for the Real*Provider (OSM) path when
            # DATA_PROVIDER_MODE=real -- previously validated on the
            # CompetitionCase model above but dropped here before storage.
            "city": case.city, "segment_scope": case.segment_scope,
        }
        if rule_profile_id:
            meta["rule_profile_id"] = rule_profile_id
        if case.land_use_type:
            # STEP5 §25 fix (docs/audit/COMPETITION_E2E_PHASE5_REPORT.md):
            # land_use_type was validated on the CompetitionCase model
            # above but then silently DROPPED before storage -- every
            # downstream reader (analyze.py, case_reconstruction.py) reads
            # meta.get("land_use_type", "商業用地"), so a case genuinely
            # created for 住宅用地/工業用地/農業用地/其他用地 was silently
            # treated as 商業用地 end-to-end (confidently producing a FULL,
            # wrong result, not even a visible RULESET_UNAVAILABLE) --
            # found via tests/test_competition_dual_input_e2e.py's
            # RULESET_UNAVAILABLE test. Guarded on non-empty so a caller
            # that omits land_use_type entirely (existing tests/callers)
            # keeps the exact same downstream "商業用地" default behavior
            # as before -- this only stops an EXPLICITLY-supplied value
            # from being thrown away.
            meta["land_use_type"] = case.land_use_type
        case_store.put_case_meta(case.case_no, meta)
        if segment_map is not None:
            competition_segments.save_segment_map(segment_map)
        # Re-read rather than echo the local `meta` dict: put_case_meta
        # writes the authoritative updated_at as a sibling attribute (see
        # case_store.py), so the local variable never has it -- returning
        # `meta` directly would silently omit updated_at from the response.
        stored = case_store.get_case_meta(case.case_no)
        if segment_map is not None:
            # Re-read (not the local segment_map) for the same reason as
            # `stored` above -- proves the map genuinely round-trips
            # through storage, not just an in-memory echo (Task 17-A).
            stored["segments"] = competition_segments.get_segment_map(case.case_no).model_dump(mode="json")
        return response(201, stored)


def get_case(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_case"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")
        segment_map = competition_segments.get_segment_map(case_no)
        if segment_map is not None:
            meta = dict(meta)
            meta["segments"] = segment_map.model_dump(mode="json")
        return response(200, meta)
