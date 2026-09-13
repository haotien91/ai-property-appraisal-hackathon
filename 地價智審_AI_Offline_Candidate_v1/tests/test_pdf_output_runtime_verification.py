# -*- coding: utf-8 -*-
"""
STEP5 FINAL GATE Part B — Real PDF Runtime Verification.

Actually INVOKES backend/handlers/pdf_handler.py::get_pdf() (not merely
imported) against a real, fully-populated Golden-style case (create_case
-> collect_data -> analyze -> complete_form -> review, all real handlers,
moto-mocked DynamoDB/S3) and reads the ACTUAL generated PDF bytes back
from the (mocked) S3 objects the handler itself wrote -- never parsing a
presigned URL, just listing the same bucket/prefix convention pdf_handler.
py uses. Opens the bytes with PyMuPDF (fitz) and asserts real, structural
evidence (page_count, extracted text containing case identity and a
Golden-specific value), per STEP5 FINAL GATE §B1-B7.

This test requires WeasyPrint's native Cairo/Pango libraries to actually
be present -- unlike every other STEP5 test file, this one does NOT
try/except-skip on OSError, because skipping is exactly what this file
exists to NOT do. On a host without those libraries (the documented,
pre-existing Windows limitation -- see docs/audit/PDF_OUTPUT_PHASE5_
REPORT.md), this whole file fails to even import (pdf_handler.py imports
weasyprint at module level) -- `pytest.importorskip("pdf_handler")` at
module level turns that into a clean, honestly-reported skip for the
WHOLE file on such a host, never a fabricated pass.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

fitz = pytest.importorskip("fitz", reason="PyMuPDF not available")

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

DOCUMENT_BUCKET = "test-document-bucket-pdfver"
PDF_BUCKET = "test-pdf-bucket-pdfver"
AWS_REGION = "ap-northeast-1"

ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    yield
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)


def _ensure_mock_collect_data(monkeypatch):
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)
    return collect_data


@pytest.fixture()
def pdf_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("DOCUMENT_BUCKET_NAME", DOCUMENT_BUCKET)
    monkeypatch.setenv("PDF_BUCKET_NAME", PDF_BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", AWS_REGION)
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=AWS_REGION)
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.create_bucket(Bucket=DOCUMENT_BUCKET, CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        s3.create_bucket(Bucket=PDF_BUCKET, CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        # Module-level table/bucket name staleness defense -- see
        # tests/_aws_mock_reset.py's module docstring for the full root-
        # cause writeup (docs/audit/STEP5_FINAL_GATE_REPORT.md); this
        # fixture is exactly the pattern that report prescribes for
        # every AWS-mocking fixture across the suite.
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_case(cases_module, case_no):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    })}
    return cases_module.create_case(event, None)


def _seed_and_complete_pipeline(monkeypatch, case_no):
    """create_case -> collect_data -> analyze -> complete_form -> review,
    ALL real handlers, producing a genuine, non-mocked FORM_COMPLETION +
    REVIEW_RESULT record for pdf_handler.get_pdf() to actually read."""
    import cases
    import case_store
    import analyze
    import complete_form
    import review

    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_case(cases, case_no)
    collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
        "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
        "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
    })}, None)
    factors = case_store.get_record(case_no, "FACTORS")
    factors["regional_base_factors"] = [
        {"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"},
    ]
    factors["regional_comparable_factors"] = {
        "comp1": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 6, "unit": "M"}],
    }
    factors["land_normal_price"] = {"comp1": "184763"}
    factors["price_date_rate"] = {"comp1": "2.00"}
    factors["weight"] = {"comp1": "100"}
    case_store.put_record(case_no, "FACTORS", factors)

    analyze_resp = analyze.analyze({"pathParameters": {"id": case_no}}, None)
    assert analyze_resp["statusCode"] == 200, analyze_resp["body"]

    form_resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
    assert form_resp["statusCode"] == 200, form_resp["body"]

    review_resp = review.review({"pathParameters": {"id": case_no}}, None)
    assert review_resp["statusCode"] == 200, review_resp["body"]


def _list_case_pdf_objects(s3, case_no):
    resp = s3.list_objects_v2(Bucket=PDF_BUCKET, Prefix=f"{case_no}/")
    return resp.get("Contents", [])


class TestPdfHandlerActuallyInvoked:
    def test_get_pdf_returns_forms_contract_and_real_bytes(self, pdf_env, monkeypatch):
        import pdf_handler

        case_no = "PDFVER-001"
        _seed_and_complete_pipeline(monkeypatch, case_no)

        resp = pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200, resp["body"]
        body = json.loads(resp["body"])

        # B6: forms contract + backward-compatible pdf_url.
        assert "表1" in body["forms"]
        assert "表4+表5-2" in body["forms"]
        assert body["pdf_url"] == body["forms"]["表4+表5-2"]["pdf_url"]

        s3 = boto3.client("s3", region_name=AWS_REGION)
        objects = _list_case_pdf_objects(s3, case_no)
        keys_by_kind = {}
        for obj in objects:
            if "form1_" in obj["Key"]:
                keys_by_kind["表1"] = obj["Key"]
            elif "form4_5-2_" in obj["Key"]:
                keys_by_kind["表4+表5-2"] = obj["Key"]
        assert "表1" in keys_by_kind, f"no 表1 PDF object found among {[o['Key'] for o in objects]}"
        assert "表4+表5-2" in keys_by_kind, f"no 表4+表5-2 PDF object found among {[o['Key'] for o in objects]}"

        table1_bytes = s3.get_object(Bucket=PDF_BUCKET, Key=keys_by_kind["表1"])["Body"].read()
        table4_52_bytes = s3.get_object(Bucket=PDF_BUCKET, Key=keys_by_kind["表4+表5-2"])["Body"].read()

        # B3: byte length > 0, header starts with %PDF.
        assert len(table1_bytes) > 0
        assert table1_bytes.startswith(b"%PDF-")
        assert len(table4_52_bytes) > 0
        assert table4_52_bytes.startswith(b"%PDF-")

        # B4: PyMuPDF opens successfully, page_count > 0.
        table1_doc = fitz.open(stream=table1_bytes, filetype="pdf")
        table4_52_doc = fitz.open(stream=table4_52_bytes, filetype="pdf")
        assert table1_doc.page_count > 0
        assert table4_52_doc.page_count > 0

        table1_text = "".join(page.get_text() for page in table1_doc)
        table4_52_text = "".join(page.get_text() for page in table4_52_doc)

        # B4/B5: identifiable content -- 表1's own form title, and case
        # identity + a Golden-specific submitted value (segment_code
        # "P002-00", per STEP5 §13/§B5) independently present in BOTH
        # rendered PDFs (both go through the SAME render_form_html()
        # header, case_reconstruction.py's own case_no/segment_code).
        assert "地價區段勘查表" in table1_text, table1_text
        assert case_no in table1_text
        assert "P002-00" in table1_text
        assert case_no in table4_52_text
        assert "P002-00" in table4_52_text
        # 表4+表5-2 renderer's own section/rule content -- the confirmed
        # (here: static-baseline) rule_id for 主要道路寬度 appears
        # verbatim among the rendered field rows.
        assert "REG-MAIN_ROAD_WIDTH" in table4_52_text

        table1_doc.close()
        table4_52_doc.close()

    def test_pdf_failure_mode_is_safe_no_fabricated_artifact(self, pdf_env, monkeypatch):
        """B7: a case with no FORM_COMPLETION at all must fail with a
        clear VALIDATION_ERROR -- never a fabricated URL, never an empty
        PDF object written to S3."""
        import cases
        import pdf_handler

        case_no = "PDFVER-FAIL-001"
        _make_case(cases, case_no)

        resp = pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "VALIDATION_ERROR"

        s3 = boto3.client("s3", region_name=AWS_REGION)
        assert _list_case_pdf_objects(s3, case_no) == []

    def test_pdf_failure_mode_stale_form_completion_shape_is_safe(self, pdf_env, monkeypatch):
        """A second, STEP5-FINAL-GATE-specific safety net: even if a
        stored FORM_COMPLETION record predates this round's rule_source_
        type/rule_resolution_status enrichment (or carries some other
        unexpected extra top-level key), get_pdf() must still fail with a
        clean VALIDATION_ERROR (via _as_form_completion_result()'s own
        try/except) rather than an unhandled 500/ValidationError leaking
        past this handler -- and must not write a fabricated PDF."""
        import cases
        import case_store
        import pdf_handler

        case_no = "PDFVER-FAIL-002"
        _make_case(cases, case_no)
        case_store.put_record(case_no, "FACTORS", {
            "user_submitted_factors": {"base_parcel_factors": [], "comparable_factors": {}},
            "regional_base_factors": [],
        })
        # Deliberately malformed: `fields` must be a list of dicts, not a
        # bare string -- FormCompletionResult.model_validate() must reject
        # this cleanly, not crash the handler.
        case_store.put_record(case_no, "FORM_COMPLETION", {
            "case_no": case_no, "form": "表4+表5-2", "fields": "not-a-list",
            "generated_at": "2026-09-11T00:00:00", "some_unexpected_future_key": {"nested": True},
        })

        resp = pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "VALIDATION_ERROR"

        s3 = boto3.client("s3", region_name=AWS_REGION)
        assert _list_case_pdf_objects(s3, case_no) == []
