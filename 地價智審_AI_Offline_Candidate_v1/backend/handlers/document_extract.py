# -*- coding: utf-8 -*-
"""
document_extract.py — POST /api/cases/{id}/documents/{document_id}/extract.

The FIRST point any server-side code actually reads an uploaded document's
bytes (document_upload.py only ever issued a presigned URL, never touched
content). Real validation happens here, in order:
  1. S3 HeadObject -- does the object exist at all (did the client's PUT
     actually complete)?
  2. Size check against MAX_DOCUMENT_SIZE_BYTES.
  3. Download to Lambda /tmp/{document_id}.pdf.
  4. Magic-bytes check (b"%PDF-") -- Content-Type/extension are never
     trusted as the sole signal (a client can lie about either).
  5. PyMuPDF must actually succeed opening the file.
Only after all 5 pass does this call the FROZEN, already LOCAL_RUNTIME_
VERIFIED LocalExtractionProvider -- completely unmodified, still a local-
path API (source_document: str), which is exactly why step 3 downloads to
/tmp first rather than trying to hand it bytes/a stream.

Extraction output (classifications + ExtractedField[], already run through
flag_low_confidence()) is written as ONE JSON artifact to DocumentBucket
(cases/{case_no}/{document_id}/extraction.json) -- never embedded directly
in the DynamoDB EXTRACTION#{document_id} item, which stays a small pointer
record only (extraction_s3_key/status/field_count/requires_confirmation_
count/created_at/extractor/extractor_version). This follows the same
artifact+pointer split PdfFunction already uses for its own S3 output.
"""
from __future__ import annotations

import sys
import os
import json
import tempfile
from datetime import datetime, timezone

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from document_extraction_provider import LocalExtractionProvider  # noqa: E402
from human_confirmation import flag_low_confidence  # noqa: E402

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")
MAX_DOCUMENT_SIZE_BYTES = int(os.environ.get("MAX_DOCUMENT_SIZE_BYTES", str(20 * 1024 * 1024)))  # 20MB default
EXTRACTOR_NAME = "LocalExtractionProvider"
EXTRACTOR_VERSION = "1.0"

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _extraction_s3_key(case_no: str, document_id: str) -> str:
    return f"cases/{case_no}/{document_id}/extraction.json"


def extract_document(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    document_id = event.get("pathParameters", {}).get("document_id")
    with timed_step(case_no, "extract_document"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))
        if doc_meta is None:
            return error_response(404, "DOCUMENT_NOT_FOUND", f"找不到文件 {document_id}")

        # STEP5 §1 dual-input contract: only enforced when the upload
        # actually declared a document_type (see document_upload.py) --
        # an undeclared/pre-STEP5 document is not blocked, only a
        # document EXPLICITLY tagged EVALUATION_STANDARD is refused here,
        # so it can never be silently misrouted into the generic appraisal-
        # form OCR pipeline.
        doc_type = doc_meta.get("document_type")
        if doc_type is not None and doc_type != "APPRAISAL_FORM":
            return error_response(
                400, "WRONG_DOCUMENT_TYPE",
                f"此文件已標記為 {doc_type}，非查估書表，不可送入書表擷取流程",
            )

        s3 = _s3_client()
        s3_key = doc_meta["s3_key"]

        try:
            head = s3.head_object(Bucket=DOCUMENT_BUCKET, Key=s3_key)
        except ClientError:
            return error_response(
                400, "DOCUMENT_NOT_UPLOADED",
                "尚未偵測到已上傳之文件內容，請確認 PUT 至 upload_url 是否已成功完成",
            )

        size = head.get("ContentLength", 0)
        if size > MAX_DOCUMENT_SIZE_BYTES:
            return error_response(
                400, "DOCUMENT_TOO_LARGE",
                f"文件大小 {size} bytes 超過上限 {MAX_DOCUMENT_SIZE_BYTES} bytes",
            )

        # Portability fix (Phase RC-3): tempfile.gettempdir() resolves to the
        # real OS temp dir on Windows (dev/test) and to "/tmp" on Lambda/Linux
        # (its own env has no TMPDIR override, so Python's posix fallback list
        # -- ['/tmp', '/var/tmp', '/usr/tmp'] -- applies unchanged) -- same
        # download/probe/cleanup behavior either way, just no longer a
        # hardcoded POSIX path that doesn't exist on Windows.
        tmp_path = os.path.join(tempfile.gettempdir(), f"{document_id}.pdf")
        try:
            s3.download_file(DOCUMENT_BUCKET, s3_key, tmp_path)

            with open(tmp_path, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                return error_response(
                    400, "INVALID_PDF",
                    "檔案內容非有效 PDF（magic bytes 不符 %PDF-，Content-Type/副檔名僅供輔助判斷，不可信）",
                )

            import fitz  # PyMuPDF -- local import, mirrors LocalExtractionProvider's own convention
            try:
                probe = fitz.open(tmp_path)
                probe.close()
            except Exception as e:
                return error_response(400, "INVALID_PDF", f"PDF 無法開啟：{e}")

            provider = LocalExtractionProvider()
            classifications = provider.classify(tmp_path)
            fields = provider.extract_fields(tmp_path, classifications)
            fields = flag_low_confidence(fields)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        requires_confirmation_count = sum(1 for f in fields if f.requires_manual_review)
        artifact = {
            "document_id": document_id,
            "classifications": [json.loads(c.model_dump_json()) for c in classifications],
            "fields": [json.loads(f.model_dump_json()) for f in fields],
        }
        extraction_s3_key = _extraction_s3_key(case_no, document_id)
        s3.put_object(
            Bucket=DOCUMENT_BUCKET, Key=extraction_s3_key,
            Body=json.dumps(artifact, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

        extraction_meta = {
            "extraction_s3_key": extraction_s3_key, "status": "COMPLETED",
            "field_count": len(fields), "requires_confirmation_count": requires_confirmation_count,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extractor": EXTRACTOR_NAME, "extractor_version": EXTRACTOR_VERSION,
        }
        case_store.put_record(case_no, case_store.extraction_sk(document_id), extraction_meta)

        return response(200, {"case_no": case_no, "document_id": document_id, **extraction_meta})
