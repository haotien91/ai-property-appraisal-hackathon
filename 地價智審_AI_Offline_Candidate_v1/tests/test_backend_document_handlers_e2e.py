# -*- coding: utf-8 -*-
"""
Phase E backend document handler E2E tests: Document Upload -> Extraction
-> Human Confirmation -> Smart Review Backend Vertical Slice, via
moto-mocked DynamoDB + S3 (same pattern as tests/test_backend_handlers_
e2e.py -- ddb_env there is mirrored here as document_env, extended with a
mocked DocumentBucket).

Does NOT re-test 表5-2 row/column extraction correctness itself -- that
is CORE FREEZE, already locked in by tests/test_document_extraction_
provider.py and tests/test_document_extraction_e2e.py. These tests only
prove the NEW backend wiring (S3 upload/download, DynamoDB pointer
records, artifact JSON round-tripping, review.py's submission_source
branch) correctly connects to the FROZEN, already LOCAL_RUNTIME_VERIFIED
extraction/confirmation/audit components.

LOCAL moto verification only -- NOT AWS-deployed, NOT AWS_RUNTIME_VERIFIED.
"""
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

GOLDEN_PDF_PATH = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")
DOCUMENT_BUCKET = "test-document-bucket"
AWS_REGION = "ap-northeast-1"


@pytest.fixture()
def _require_fitz():
    pytest.importorskip("fitz")


@pytest.fixture()
def document_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("DOCUMENT_BUCKET_NAME", DOCUMENT_BUCKET)
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
        s3.create_bucket(Bucket=DOCUMENT_BUCKET,
                          CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        yield


def _make_case(cases_module, case_no, comparable_ids=None):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地",
        "comparable_ids": comparable_ids or ["comp1"],
    })}
    return cases_module.create_case(event, None)


def _seed_golden_document_case(case_no):
    """Seeds a case's FACTORS record with the REAL Golden Case's
    independently-known regional facts (data/golden/golden_case_input.py),
    exactly matching what tests/test_document_extraction_e2e.py's
    TestGoldenTable52RealPdfE2E already relies on -- so a DOCUMENT-source
    review against the real PDF can be compared against that already-
    verified 28/28 PASS baseline. Returns (comparable_id,)."""
    import cases
    import case_store

    golden_dir = os.path.join(REPO_ROOT, "data", "golden")
    if golden_dir not in sys.path:
        sys.path.insert(0, golden_dir)
    from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL  # noqa: E402

    comparable_id = GOLDEN_CASE.comparable_ids[0]
    _make_case(cases, case_no, comparable_ids=[comparable_id])

    regional_dicts = [
        {"field_id": fi.field_id, "factor": fi.factor, "raw_value": fi.raw_value, "unit": fi.unit}
        for fi in BASE_REGIONAL
    ]
    case_store.put_record(case_no, "FACTORS", {
        "user_submitted_factors": {"base_parcel_factors": [], "comparable_factors": {comparable_id: []}},
        "regional_base_factors": regional_dicts,
        # Golden Case fact: 比準地與比較標的1同屬地價區段 P002-00 (see
        # docs/phase2/business_process.md) -- same regional_dicts reused.
        "regional_comparable_factors": {comparable_id: regional_dicts},
        "land_normal_price": {comparable_id: "184763"},
        "price_date_rate": {comparable_id: "2.00"},
        "weight": {comparable_id: "100"},
        "plan_identification": {
            "internal_plan_id": "jinshan", "confirmed_plan_name": "金山都市計畫",
            "plan_identification_source": "MANUAL_INPUT",
        },
    })
    return comparable_id


def _put_golden_pdf(s3, s3_key):
    with open(GOLDEN_PDF_PATH, "rb") as f:
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=s3_key, Body=f.read(), ContentType="application/pdf")


class TestGoldenDocumentBackendE2E:
    """Points 1-9 and 15 of this round's test plan: the full real-client
    sequence (create case -> request upload -> PUT to S3 -> extract ->
    confirm -> DOCUMENT review), using the REAL archived Golden PDF."""

    def test_full_golden_document_pipeline(self, document_env, _require_fitz):
        import document_upload
        import document_extract
        import document_get_extraction
        import document_confirm
        import review
        import case_store

        case_no = "DOC-E2E-001"
        comparable_id = _seed_golden_document_case(case_no)

        # 2. request upload URL
        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 201
        upload_body = json.loads(resp["body"])
        document_id = upload_body["document_id"]
        assert upload_body["case_no"] == case_no
        assert upload_body["upload_url"]
        assert upload_body["expires_in"] > 0

        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))
        assert doc_meta["upload_status"] == "PENDING"

        # 3. S3 mock 放入真實 Golden PDF -- simulates the client's PUT
        # completing (not an actual presigned-URL HTTP round trip).
        s3 = boto3.client("s3", region_name=AWS_REGION)
        _put_golden_pdf(s3, doc_meta["s3_key"])

        # 4. extract
        resp = document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )
        assert resp["statusCode"] == 200
        extract_body = json.loads(resp["body"])
        assert extract_body["status"] == "COMPLETED"
        assert extract_body["field_count"] > 0
        assert extract_body["extractor"] == "LocalExtractionProvider"

        # 5. extraction artifact written to S3 (not embedded in DynamoDB)
        extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
        assert extraction_meta is not None
        obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"])
        artifact = json.loads(obj["Body"].read().decode("utf-8"))
        assert len(artifact["fields"]) == extract_body["field_count"]

        # 6. DynamoDB EXTRACTION# record is a small pointer, not the payload
        assert extraction_meta["field_count"] == extract_body["field_count"]
        assert "extraction_s3_key" in extraction_meta

        # GET extraction result endpoint
        resp = document_get_extraction.get_extraction(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )
        assert resp["statusCode"] == 200
        get_body = json.loads(resp["body"])
        # field_count round-trips through DynamoDB as Decimal -> this
        # codebase's own established convention (common.py's
        # DecimalEncoder) renders it as a STRING in the JSON response,
        # same as every other DynamoDB-sourced numeric field elsewhere in
        # this backend (see case_reconstruction.py's
        # _dynamodb_round_tripped_to_str) -- not a bug, cast for comparison.
        assert int(get_body["field_count"]) == extract_body["field_count"]
        assert len(get_body["fields"]) == extract_body["field_count"]

        # 7. confirmation persistence -- Golden PDF is all high-confidence
        # (Phase D: 0 manual review), so this proves the endpoint persists
        # correctly on an empty confirmation set; a non-empty flow is
        # covered by TestMissingConfirmationBlocksReview below.
        resp = document_confirm.confirm_document(
            {"pathParameters": {"id": case_no, "document_id": document_id},
             "body": json.dumps({"confirmations": []})},
            None,
        )
        assert resp["statusCode"] == 200
        confirm_body = json.loads(resp["body"])
        assert confirm_body["confirmed_count"] == 0
        confirmation_meta = case_store.get_record(case_no, case_store.confirmation_sk(document_id))
        assert confirmation_meta is not None

        # 8. DOCUMENT review
        resp = review.review(
            {"pathParameters": {"id": case_no},
             "body": json.dumps({"submission_source": "DOCUMENT", "document_id": document_id})},
            None,
        )
        assert resp["statusCode"] == 200
        review_body = json.loads(resp["body"])
        assert review_body["submission_source"] == "DOCUMENT"
        assert review_body["document_id"] == document_id

        # 15. matches Phase D's already-verified Golden Real-PDF E2E result
        # (tests/test_document_extraction_e2e.py::TestGoldenTable52RealPdfE2E):
        # 28/28 Grade Identity PASS, 28/28 Grade Representation PASS.
        identity_issues = [
            i for i in review_body["issues"]
            if i["field"].startswith("regional_") and i["field"].endswith(f"_adjustment_pct_{comparable_id}")
        ]
        representation_issues = [
            i for i in review_body["issues"] if i["field"].endswith(f"_grade_representation_{comparable_id}")
        ]
        assert len(identity_issues) == 28
        assert all(i["issue_type"] == "Passed" for i in identity_issues)
        assert len(representation_issues) == 28
        assert all(i["issue_type"] == "Passed" for i in representation_issues)

        # Original submitted text preserved verbatim through the FULL
        # backend round-trip (S3 artifact -> review) -- the 2 evidence-
        # confirmed boolean factors' "無" must not have been rewritten.
        for field_id in ("regional_construction_prohibited", "regional_construction_restricted"):
            issue = next(i for i in representation_issues if i["field"].startswith(field_id))
            assert issue["submitted_value"] == "無"


class TestReviewSourceSelection:
    """Points 9-10: FORM_COMPLETION legacy path unaffected; source
    selection is always explicit, never auto-switched just because an
    extraction record exists for the case."""

    def _seed_form_completion_case(self, case_no):
        import cases
        import collect_data
        import complete_form
        import case_store

        _make_case(cases, case_no)
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)
        factors = case_store.get_record(case_no, "FACTORS")
        factors["regional_base_factors"] = [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}]
        factors["regional_comparable_factors"] = {"comp1": [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}]}
        factors["land_normal_price"] = {"comp1": "184763"}
        factors["price_date_rate"] = {"comp1": "2.00"}
        factors["weight"] = {"comp1": "100"}
        case_store.put_record(case_no, "FACTORS", factors)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

    def test_omitted_submission_source_defaults_to_form_completion(self, document_env):
        """9. FORM_COMPLETION legacy review -- omitted body (or no body key
        at all) must behave exactly as before Phase E: regional factors
        PASS via the (now code-populated) FORM_COMPLETION path."""
        import review

        case_no = "SOURCE-DEFAULT-001"
        self._seed_form_completion_case(case_no)
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body.get("submission_source") == "FORM_COMPLETION"
        issue = next(i for i in body["issues"] if i["field"] == "regional_main_road_width_adjustment_pct_comp1")
        assert issue["issue_type"] == "Passed"

    def test_explicit_document_requires_document_id(self, document_env):
        import review

        case_no = "SOURCE-MISSING-DOCID-001"
        self._seed_form_completion_case(case_no)
        resp = review.review(
            {"pathParameters": {"id": case_no}, "body": json.dumps({"submission_source": "DOCUMENT"})}, None,
        )
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_invalid_submission_source_value_rejected(self, document_env):
        import review

        case_no = "SOURCE-INVALID-001"
        self._seed_form_completion_case(case_no)
        resp = review.review(
            {"pathParameters": {"id": case_no}, "body": json.dumps({"submission_source": "SOMETHING_ELSE"})}, None,
        )
        assert resp["statusCode"] == 400

    def test_extraction_record_existing_does_not_auto_switch_source(self, document_env, _require_fitz):
        """The explicit guardrail: a case that ALSO has a completed
        document extraction must NOT have review() silently prefer it --
        calling review() without submission_source (or with
        "FORM_COMPLETION" explicitly) must still use the FORM_COMPLETION
        data, proven by the FORM_COMPLETION-only signal (no _grade_
        representation_ issues, which only the DOCUMENT path's Check B
        against submitted_grade_codes populated by extraction would add
        in the SAME shape/count as the extraction pipeline)."""
        import cases
        import collect_data
        import complete_form
        import document_upload
        import document_extract
        import review
        import case_store

        case_no = "SOURCE-NOAUTOSWITCH-001"
        # Seed a FORM_COMPLETION-style case (小 individual-only, no
        # regional factors) so its own review() result is trivially
        # distinguishable from a DOCUMENT-sourced Golden Case review.
        _make_case(cases, case_no, comparable_ids=["comp1"])
        collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
            "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
            "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
        })}, None)
        complete_form.complete_form({"pathParameters": {"id": case_no}}, None)

        # ALSO upload+extract a real document under the SAME case (its
        # existence alone must not change review()'s default behavior).
        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        document_id = json.loads(resp["body"])["document_id"]
        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))
        s3 = boto3.client("s3", region_name=AWS_REGION)
        _put_golden_pdf(s3, doc_meta["s3_key"])
        document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )
        extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
        assert extraction_meta is not None  # sanity: the extraction record genuinely exists

        # Default call (no submission_source) -- must still be FORM_COMPLETION.
        resp = review.review({"pathParameters": {"id": case_no}}, None)
        body = json.loads(resp["body"])
        assert body.get("submission_source") == "FORM_COMPLETION"
        # The FORM_COMPLETION-seeded case has NO regional factors at all,
        # so if DOCUMENT data had leaked in, 28 regional identity/
        # representation issues would appear. It must not.
        assert not any(i["field"].startswith("regional_") for i in body["issues"])


class TestDocumentValidation:
    """Points 11-13: non-PDF rejection, oversized file rejection,
    nonexistent case/document."""

    def test_non_pdf_content_rejected(self, document_env):
        import document_upload
        import document_extract
        import case_store

        case_no = "VALID-NONPDF-001"
        import cases
        _make_case(cases, case_no)

        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        document_id = json.loads(resp["body"])["document_id"]
        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))

        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=doc_meta["s3_key"], Body=b"this is not a pdf file at all",
                      ContentType="application/pdf")  # lying Content-Type -- must not be trusted alone

        resp = document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "INVALID_PDF"

    def test_oversized_document_rejected(self, document_env, monkeypatch):
        import importlib
        import document_upload
        import case_store

        case_no = "VALID-OVERSIZE-001"
        import cases
        _make_case(cases, case_no)

        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        document_id = json.loads(resp["body"])["document_id"]
        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))

        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=doc_meta["s3_key"], Body=b"%PDF-1.4\n" + (b"0" * 200),
                      ContentType="application/pdf")

        monkeypatch.setenv("MAX_DOCUMENT_SIZE_BYTES", "100")
        import document_extract
        importlib.reload(document_extract)
        try:
            resp = document_extract.extract_document(
                {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
            )
            assert resp["statusCode"] == 400
            body = json.loads(resp["body"])
            assert body["error"]["code"] == "DOCUMENT_TOO_LARGE"
        finally:
            monkeypatch.delenv("MAX_DOCUMENT_SIZE_BYTES", raising=False)
            importlib.reload(document_extract)

    def test_nonexistent_case_rejected_on_every_endpoint(self, document_env):
        import document_upload
        import document_extract
        import document_get_extraction
        import document_confirm

        bogus = "NONEXISTENT-CASE-001"
        assert document_upload.request_upload({"pathParameters": {"id": bogus}}, None)["statusCode"] == 404
        assert document_extract.extract_document(
            {"pathParameters": {"id": bogus, "document_id": "doc1"}}, None,
        )["statusCode"] == 404
        assert document_get_extraction.get_extraction(
            {"pathParameters": {"id": bogus, "document_id": "doc1"}}, None,
        )["statusCode"] == 404
        assert document_confirm.confirm_document(
            {"pathParameters": {"id": bogus, "document_id": "doc1"}, "body": "{}"}, None,
        )["statusCode"] == 404

    def test_nonexistent_document_rejected(self, document_env):
        import cases
        import document_extract
        import document_get_extraction
        import document_confirm

        case_no = "VALID-NODOC-001"
        _make_case(cases, case_no)
        bogus_doc = "nonexistent-document-id"
        assert document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": bogus_doc}}, None,
        )["statusCode"] == 404
        assert document_get_extraction.get_extraction(
            {"pathParameters": {"id": case_no, "document_id": bogus_doc}}, None,
        )["statusCode"] == 404
        assert document_confirm.confirm_document(
            {"pathParameters": {"id": case_no, "document_id": bogus_doc}, "body": "{}"}, None,
        )["statusCode"] == 404

    def test_extract_before_upload_completes_is_rejected(self, document_env):
        """document_id exists (request_upload was called) but no S3 PUT
        ever happened -- extract must fail via HeadObject, not crash."""
        import cases
        import document_upload
        import document_extract

        case_no = "VALID-NOUPLOAD-001"
        _make_case(cases, case_no)
        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        document_id = json.loads(resp["body"])["document_id"]

        resp = document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body["error"]["code"] == "DOCUMENT_NOT_UPLOADED"


class TestMissingConfirmationBlocksReview:
    """Point 14: an unconfirmed low-confidence field must resolve to None
    and produce MISSING, never leak into a PASSED/ERROR verdict -- proves
    the NEW backend wiring correctly reuses resolve_confirmed_values()'s
    existing (frozen, unmodified) guarantee end-to-end through S3/DynamoDB,
    not just in-process."""

    def test_unconfirmed_low_confidence_field_is_missing_not_leaked(self, document_env, _require_fitz):
        import document_upload
        import document_extract
        import review
        import case_store

        case_no = "DOC-LOWCONF-001"
        comparable_id = _seed_golden_document_case(case_no)

        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        document_id = json.loads(resp["body"])["document_id"]
        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))

        s3 = boto3.client("s3", region_name=AWS_REGION)
        _put_golden_pdf(s3, doc_meta["s3_key"])
        document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )

        # Directly force regional_main_road_width's BASE/GRADE_CODE field
        # to low-confidence in the stored artifact -- simulates a real
        # low-confidence extraction without needing a second fixture PDF
        # (表5-2 parser correctness itself is out of scope this round).
        extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
        obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"])
        artifact = json.loads(obj["Body"].read().decode("utf-8"))
        touched = False
        for f in artifact["fields"]:
            if (f["field_id"] == "regional_main_road_width" and f["extraction_role"] == "GRADE_CODE"
                    and f["extraction_subject_role"] == "BASE"):
                f["confidence"] = 0.3
                f["requires_manual_review"] = True
                touched = True
        assert touched
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"],
                       Body=json.dumps(artifact, ensure_ascii=False).encode("utf-8"), ContentType="application/json")

        # No confirm call at all -- review must not fabricate a value.
        resp = review.review(
            {"pathParameters": {"id": case_no},
             "body": json.dumps({"submission_source": "DOCUMENT", "document_id": document_id})},
            None,
        )
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        issue = next(i for i in body["issues"]
                     if i["field"] == f"regional_main_road_width_adjustment_pct_{comparable_id}")
        assert issue["issue_type"] == "Missing"
        assert issue["submitted_value"] is None

    def test_confirming_the_low_confidence_field_unblocks_review(self, document_env, _require_fitz):
        """The positive counterpart: confirming the SAME field (full
        composite identity) allows it to correctly flow through to a
        deterministic PASSED verdict."""
        import document_upload
        import document_extract
        import document_confirm
        import review
        import case_store

        case_no = "DOC-LOWCONF-002"
        comparable_id = _seed_golden_document_case(case_no)

        resp = document_upload.request_upload({"pathParameters": {"id": case_no}}, None)
        document_id = json.loads(resp["body"])["document_id"]
        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))

        s3 = boto3.client("s3", region_name=AWS_REGION)
        _put_golden_pdf(s3, doc_meta["s3_key"])
        document_extract.extract_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}}, None,
        )

        extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
        obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"])
        artifact = json.loads(obj["Body"].read().decode("utf-8"))
        for f in artifact["fields"]:
            if (f["field_id"] == "regional_main_road_width" and f["extraction_role"] == "GRADE_CODE"
                    and f["extraction_subject_role"] == "BASE"):
                f["confidence"] = 0.3
                f["requires_manual_review"] = True
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"],
                       Body=json.dumps(artifact, ensure_ascii=False).encode("utf-8"), ContentType="application/json")

        resp = document_confirm.confirm_document(
            {"pathParameters": {"id": case_no, "document_id": document_id}, "body": json.dumps({
                "confirmations": [{
                    "field_id": "regional_main_road_width", "extraction_role": "GRADE_CODE",
                    "extraction_subject_role": "BASE", "comparable_slot": None,
                    "confirmed_value": "3", "confirmed_by": "test_appraiser",
                }],
            })},
            None,
        )
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["confirmed_count"] == 1

        resp = review.review(
            {"pathParameters": {"id": case_no},
             "body": json.dumps({"submission_source": "DOCUMENT", "document_id": document_id})},
            None,
        )
        body = json.loads(resp["body"])
        issue = next(i for i in body["issues"]
                     if i["field"] == f"regional_main_road_width_adjustment_pct_{comparable_id}")
        assert issue["issue_type"] == "Passed"
        assert issue["submitted_value"] == "3"
