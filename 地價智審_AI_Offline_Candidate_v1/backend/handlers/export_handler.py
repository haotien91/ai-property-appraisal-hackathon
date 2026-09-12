# -*- coding: utf-8 -*-
"""
export_handler.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 16/17.

GET /api/cases/{id}/export/json    -> CaseExportBundle JSON, inline body.
GET /api/cases/{id}/export/excel   -> zip of 6 Excel files, uploaded to S3
                                       + presigned URL (same convention as
                                       pdf_handler.py's own official PDF
                                       output -- binary payloads are never
                                       returned inline through API Gateway
                                       here).
GET /api/cases/{id}/export/bundle  -> zip of JSON + 6 Excel + the Official
                                       PDF (reusing shulin_official_pdf_
                                       renderer.py, never re-derived),
                                       same S3 + presigned URL convention.

Does NOT touch GET /api/cases/{id}/pdf (pdf_handler.py, unchanged) -- these
are new, additive routes only.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import boto3

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
for _p in (_REPO_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import response, error_response, timed_step  # noqa: E402

from export.bundle_builder import build_case_export_bundle, CaseNotFoundError, SegmentMapRequiredForExportError  # noqa: E402
from export.json_exporter import export_bundle_to_json_bytes, json_filename  # noqa: E402
from export.zip_bundle import (  # noqa: E402
    build_export_zip_bytes, bundle_zip_filename, excel_only_zip_bytes, excel_zip_filename,
)

EXPORT_BUCKET = os.environ.get("PDF_BUCKET_NAME", "ai-valuation-pdfs")
_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        # FRONTEND-UNIFIED-EXPORT-WIRING-F2: see case_store.py's matching
        # comment -- unset in every real deployment, production behavior
        # unchanged.
        local_endpoint = os.environ.get("LOCAL_AWS_ENDPOINT_URL")
        if local_endpoint:
            _s3 = boto3.client("s3", endpoint_url=local_endpoint)
        else:
            _s3 = boto3.client("s3")
    return _s3


def _build_bundle_or_error(case_no: str):
    try:
        return build_case_export_bundle(case_no), None
    except CaseNotFoundError:
        return None, error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")
    except SegmentMapRequiredForExportError:
        return None, error_response(
            400, "SEGMENT_MAP_REQUIRED",
            "案件尚未定義 segment map，Supplemental Export 目前僅支援 segment-scoped 競賽案件",
        )


def get_export_json(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_export_json"):
        bundle, err = _build_bundle_or_error(case_no)
        if err is not None:
            return err
        body_bytes = export_bundle_to_json_bytes(bundle)
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Content-Disposition": f'attachment; filename="{json_filename(case_no)}"',
                "Access-Control-Allow-Origin": "*",
            },
            "body": body_bytes.decode("utf-8"),
        }


def get_export_excel(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_export_excel"):
        bundle, err = _build_bundle_or_error(case_no)
        if err is not None:
            return err
        zip_bytes = excel_only_zip_bytes(bundle)
        now = datetime.now()
        key = f"{case_no}/exports/excel/{now.strftime('%Y%m%d%H%M%S')}_{excel_zip_filename(case_no)}"
        s3 = _s3_client()
        s3.put_object(Bucket=EXPORT_BUCKET, Key=key, Body=zip_bytes, ContentType="application/zip")
        url = s3.generate_presigned_url("get_object", Params={"Bucket": EXPORT_BUCKET, "Key": key}, ExpiresIn=3600)
        return response(200, {"case_no": case_no, "generated_at": now.isoformat(), "excel_zip_url": url})


def get_export_bundle(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_export_bundle"):
        bundle, err = _build_bundle_or_error(case_no)
        if err is not None:
            return err
        zip_bytes = build_export_zip_bytes(bundle, include_pdf=True)
        now = datetime.now()
        key = f"{case_no}/exports/bundle/{now.strftime('%Y%m%d%H%M%S')}_{bundle_zip_filename(case_no)}"
        s3 = _s3_client()
        s3.put_object(Bucket=EXPORT_BUCKET, Key=key, Body=zip_bytes, ContentType="application/zip")
        url = s3.generate_presigned_url("get_object", Params={"Bucket": EXPORT_BUCKET, "Key": key}, ExpiresIn=3600)
        return response(200, {"case_no": case_no, "generated_at": now.isoformat(), "bundle_zip_url": url})
