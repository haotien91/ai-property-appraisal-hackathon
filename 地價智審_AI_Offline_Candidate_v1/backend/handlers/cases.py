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

from domain.models import CompetitionCase  # noqa: E402
from pydantic import ValidationError  # noqa: E402


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

        meta = {
            "case_no": case.case_no, "segment_code": case.segment_code,
            "district": case.district, "status": "IN_PROGRESS",
            # city/segment_scope: needed by collect_data.py to geocode a
            # center coordinate for the Real*Provider (OSM) path when
            # DATA_PROVIDER_MODE=real -- previously validated on the
            # CompetitionCase model above but dropped here before storage.
            "city": case.city, "segment_scope": case.segment_scope,
        }
        case_store.put_case_meta(case.case_no, meta)
        # Re-read rather than echo the local `meta` dict: put_case_meta
        # writes the authoritative updated_at as a sibling attribute (see
        # case_store.py), so the local variable never has it -- returning
        # `meta` directly would silently omit updated_at from the response.
        stored = case_store.get_case_meta(case.case_no)
        return response(201, stored)


def get_case(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_case"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")
        return response(200, meta)
