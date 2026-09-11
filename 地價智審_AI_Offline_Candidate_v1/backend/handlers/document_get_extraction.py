# -*- coding: utf-8 -*-
"""
document_get_extraction.py — GET /api/cases/{id}/documents/{document_id}/extraction.

Reads back the extraction.json artifact document_extract.py wrote to
DocumentBucket, via the small DynamoDB pointer record (EXTRACTION#
{document_id}). Performs no computation of its own -- pure artifact
retrieval, same principle as backend/handlers/result.py.
"""
from __future__ import annotations

import sys
import os
import json

sys.path.insert(0, "/opt/python")
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def get_extraction(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    document_id = event.get("pathParameters", {}).get("document_id")
    with timed_step(case_no, "get_extraction"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
        if extraction_meta is None:
            return error_response(404, "EXTRACTION_NOT_FOUND", f"找不到文件 {document_id} 之擷取結果，請先執行 extract")

        obj = _s3_client().get_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"])
        artifact = json.loads(obj["Body"].read().decode("utf-8"))

        return response(200, {
            "case_no": case_no, "document_id": document_id,
            "status": extraction_meta.get("status"),
            "field_count": extraction_meta.get("field_count"),
            "requires_confirmation_count": extraction_meta.get("requires_confirmation_count"),
            "extractor": extraction_meta.get("extractor"),
            "extractor_version": extraction_meta.get("extractor_version"),
            "created_at": extraction_meta.get("created_at"),
            "classifications": artifact.get("classifications", []),
            "fields": artifact.get("fields", []),
        })
