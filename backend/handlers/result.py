# -*- coding: utf-8 -*-
"""result.py — GET /api/cases/{id}/result. Aggregates the final view from
already-stored records; performs no new engine computation itself."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, "/opt/python")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402


def get_result(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_result"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        form_completion = case_store.get_record(case_no, "FORM_COMPLETION") or {}
        review_result = case_store.get_record(case_no, "REVIEW_RESULT") or {}

        fields = form_completion.get("fields", [])
        final_field = next((f for f in fields if f["field_id"] == "base_parcel_comparison_price"), None)
        issues = review_result.get("issues", [])

        status = "COMPLETED"
        if any(i.get("issue_type") in ("Error", "Inconsistent") for i in issues):
            status = "MANUAL_REVIEW_REQUIRED"

        return response(200, {
            "case_no": case_no, "status": status,
            "pdf_url": meta.get("pdf_url"),
            "review_summary": {
                "passed": sum(1 for i in issues if i.get("issue_type") == "Passed"),
                "error": sum(1 for i in issues if i.get("issue_type") == "Error"),
                "warning": sum(1 for i in issues if i.get("issue_type") == "Warning"),
            },
            "final_value": {"base_parcel_comparison_price": final_field["final_value"] if final_field else None},
        })
