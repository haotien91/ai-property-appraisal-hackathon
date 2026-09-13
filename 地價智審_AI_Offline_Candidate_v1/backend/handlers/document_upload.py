# -*- coding: utf-8 -*-
"""
document_upload.py — POST /api/cases/{id}/documents. Issues a presigned S3
PUT URL for a client to upload a source PDF DIRECTLY to S3 -- this handler
never receives or touches the PDF bytes itself (API Gateway/Lambda payload
limits are irrelevant to upload size here, by design; see infra/
template.yaml's DocumentBucket).

DocumentBucket is a SEPARATE bucket from PdfBucket (backend/handlers/
pdf_handler.py): PdfBucket holds SYSTEM-GENERATED output PDFs (表4+表5-2
renders), DocumentBucket holds USER-UPLOADED source documents. These are
never the same object, never the same bucket, per this round's explicit
architecture decision.

This handler does NOT verify the upload actually happened (a presigned URL
is fire-and-forget from the issuing Lambda's perspective) -- that
verification (S3 HeadObject + magic-bytes + PyMuPDF-open) happens in
document_extract.py's extract_document(), the first point where the
document's actual bytes are ever read server-side.
"""
from __future__ import annotations

import sys
import os
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/opt/python")
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")
UPLOAD_URL_EXPIRES_IN = int(os.environ.get("DOCUMENT_UPLOAD_URL_EXPIRES_IN", "3600"))

# STEP5 §1 dual-input contract: an uploaded document may OPTIONALLY declare
# which pipeline it belongs to. Optional (not required) so every pre-STEP5
# caller that never set this keeps working unchanged -- document_extract.py
# / evaluation_standard.py only ENFORCE a mismatch when document_type was
# actually declared (see their own "wrong document type" guards); an
# undeclared document is never blocked, only unprotected against the
# specific mix-up §1 describes.
_VALID_DOCUMENT_TYPES = ("APPRAISAL_FORM", "EVALUATION_STANDARD")

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _document_s3_key(case_no: str, document_id: str) -> str:
    return f"cases/{case_no}/{document_id}/original.pdf"


def request_upload(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "request_upload"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        document_type = parse_body(event).get("document_type")
        if document_type is not None and document_type not in _VALID_DOCUMENT_TYPES:
            return error_response(
                400, "VALIDATION_ERROR",
                f"document_type 必須為 {_VALID_DOCUMENT_TYPES} 之一或省略，收到：{document_type!r}",
                field_id="document_type",
            )

        document_id = str(uuid.uuid4())
        s3_key = _document_s3_key(case_no, document_id)

        upload_url = _s3_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": DOCUMENT_BUCKET, "Key": s3_key, "ContentType": "application/pdf"},
            ExpiresIn=UPLOAD_URL_EXPIRES_IN,
        )

        # upload_status stays "PENDING" -- this Lambda never confirms the
        # client actually completed the PUT; document_extract.py's own
        # S3 HeadObject is the real, later verification point.
        case_store.put_record(case_no, case_store.document_sk(document_id), {
            "document_id": document_id, "s3_key": s3_key, "upload_status": "PENDING",
            "document_type": document_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        return response(201, {
            "case_no": case_no, "document_id": document_id,
            "upload_url": upload_url, "expires_in": UPLOAD_URL_EXPIRES_IN,
        })
