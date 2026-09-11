# -*- coding: utf-8 -*-
"""pdf.py — GET /api/cases/{id}/pdf. Runs Phase 5's PdfRenderer and stores
the output in S3, returning a presigned URL (never returns raw PDF bytes
through API Gateway's payload limits)."""
from __future__ import annotations

import sys
import os
import json
from datetime import datetime

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402

from domain.models import FormCompletionResult  # noqa: E402
from pdf_renderer import PdfRenderer  # noqa: E402

PDF_BUCKET = os.environ.get("PDF_BUCKET_NAME", "ai-valuation-pdfs")
_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def get_pdf(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_pdf"):
        form_completion = case_store.get_record(case_no, "FORM_COMPLETION")
        meta = case_store.get_case_meta(case_no)
        if form_completion is None or meta is None:
            return error_response(400, "VALIDATION_ERROR", "案件尚未完成書表填寫，無法產出PDF")

        result = FormCompletionResult.model_validate(form_completion)
        renderer = PdfRenderer()
        pdf_bytes = renderer.render_form(result, "表4+表5-2（系統整合輸出）", "表4", case_no, meta["segment_code"])

        key = f"{case_no}/form4_5-2_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        _s3_client().put_object(Bucket=PDF_BUCKET, Key=key, Body=pdf_bytes, ContentType="application/pdf")
        url = _s3_client().generate_presigned_url(
            "get_object", Params={"Bucket": PDF_BUCKET, "Key": key}, ExpiresIn=3600,
        )
        return response(200, {"case_no": case_no, "pdf_url": url, "generated_at": datetime.now().isoformat()})
