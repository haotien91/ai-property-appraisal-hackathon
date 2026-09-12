# -*- coding: utf-8 -*-
"""
document_confirm.py — POST /api/cases/{id}/documents/{document_id}/confirm.

Records a human's confirmation/correction of low-confidence ExtractedFields
via the FROZEN engine/human_confirmation.py::confirm_field() -- unmodified.
Identity is matched on the FULL composite key (field_id, extraction_role,
extraction_subject_role, comparable_slot), never field_id alone: 表5-2
legitimately extracts several ExtractedFields sharing one canonical
field_id (e.g. a factor's BASE grade code vs BASE grade text), and
collapsing them to field_id-only would silently misattribute a
confirmation to the wrong one (the exact bug found and fixed in engine/
human_confirmation.py during the Grade Representation Contract round --
NOT reintroduced here).

Confirmations are stored as ONE JSON artifact in DocumentBucket (cases/
{case_no}/{document_id}/confirmations.json), never embedded directly in
the DynamoDB CONFIRMATION#{document_id} item (same artifact+pointer
principle as document_extract.py's extraction.json, applied here because
this repo must not assume DynamoDB's item-size limit is safe to ignore
for a document with many low-confidence fields).

Repeated calls to this endpoint MERGE (by composite identity) rather than
replace -- a client confirming fields incrementally across multiple
requests must not lose earlier confirmations.
"""
from __future__ import annotations

import sys
import os
import json
from datetime import datetime, timezone

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402

from human_confirmation import confirm_field  # noqa: E402
from domain.models import ExtractedField, HumanConfirmationRecord  # noqa: E402

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _confirmation_s3_key(case_no: str, document_id: str) -> str:
    return f"cases/{case_no}/{document_id}/confirmations.json"


def _identity_key(field_id, extraction_role, extraction_subject_role, comparable_slot):
    return (field_id, extraction_role, extraction_subject_role, comparable_slot)


def confirm_document(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    document_id = event.get("pathParameters", {}).get("document_id")
    body = parse_body(event)
    with timed_step(case_no, "confirm_document"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
        if extraction_meta is None:
            return error_response(404, "EXTRACTION_NOT_FOUND", f"找不到文件 {document_id} 之擷取結果，請先執行 extract")

        s3 = _s3_client()
        extraction_obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"])
        extraction_artifact = json.loads(extraction_obj["Body"].read().decode("utf-8"))
        fields = [ExtractedField.model_validate(f) for f in extraction_artifact.get("fields", [])]
        by_identity = {
            _identity_key(f.field_id, f.extraction_role.value, f.extraction_subject_role.value, f.comparable_slot): f
            for f in fields
        }

        # Merge with any existing confirmations for this document (see
        # module docstring) rather than replacing them.
        confirmation_s3_key = _confirmation_s3_key(case_no, document_id)
        existing_records = {}
        prior_meta = case_store.get_record(case_no, case_store.confirmation_sk(document_id))
        if prior_meta is not None:
            try:
                prior_obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=prior_meta["confirmation_s3_key"])
                prior_artifact = json.loads(prior_obj["Body"].read().decode("utf-8"))
                for r in prior_artifact.get("confirmations", []):
                    key = _identity_key(r["field_id"], r["extraction_role"], r["extraction_subject_role"],
                                         r.get("comparable_slot"))
                    existing_records[key] = r
            except Exception:
                pass  # no prior artifact readable -- start fresh, never crash the confirm call over it

        skipped = []
        for c in body.get("confirmations", []):
            key = _identity_key(
                c.get("field_id"), c.get("extraction_role"), c.get("extraction_subject_role"),
                c.get("comparable_slot"),
            )
            field = by_identity.get(key)
            if field is None:
                skipped.append(c)
                continue
            record: HumanConfirmationRecord = confirm_field(
                field, confirmed_value=c["confirmed_value"], confirmed_by=c.get("confirmed_by", "unknown"),
            )
            existing_records[key] = json.loads(record.model_dump_json())

        merged = list(existing_records.values())
        s3.put_object(
            Bucket=DOCUMENT_BUCKET, Key=confirmation_s3_key,
            Body=json.dumps({"confirmations": merged}, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

        confirmation_meta = {
            "confirmation_s3_key": confirmation_s3_key, "confirmed_count": len(merged),
            "skipped_count": len(skipped), "created_at": datetime.now(timezone.utc).isoformat(),
        }
        case_store.put_record(case_no, case_store.confirmation_sk(document_id), confirmation_meta)

        return response(200, {
            "case_no": case_no, "document_id": document_id,
            "confirmed_count": confirmation_meta["confirmed_count"],
            "skipped_count": confirmation_meta["skipped_count"],
            "skipped": skipped,
        })
