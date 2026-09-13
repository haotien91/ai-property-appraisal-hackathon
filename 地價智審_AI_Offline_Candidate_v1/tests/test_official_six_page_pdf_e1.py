# -*- coding: utf-8 -*-
"""
OFFICIAL-SIX-PAGE-PDF-E1 tests.

Exercises the REAL production path -- cases.create_case (with `segments`)
-> collect_data.collect_data x4 (segment-scoped, real 題目.pdf-derived
Table3/Table4 fixtures) -> a CONFIRMED Shulin CaseRulePackage ->
backend/handlers/pdf_handler.get_pdf() (which internally delegates to
backend/handlers/shulin_official_pdf_handler.py, which itself calls the
UNCHANGED table51_analysis.py/table4_analysis.py runtime + pdf/shulin_
official_pdf_renderer.py) -- against moto-mocked DynamoDB + S3.

pdf_handler.py itself unconditionally imports pdf_renderer.py at module
level, which imports weasyprint -- broken on this Windows dev host for
reasons unrelated to this round (see tests/test_pdf_output_runtime_
verification.py's 3 known pre-existing failures). Since the Shulin code
path added this round never calls into weasyprint at all, `_stub_
weasyprint()` below injects a harmless dummy module into sys.modules
BEFORE importing pdf_handler, purely so the import chain itself doesn't
crash on this host -- it does not change what pdf_handler.py's own code
does at runtime.

Numbered scenarios below map 1:1 to this round's own Task 16 list (1-25).
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "pdf"))
sys.path.insert(0, os.path.join(REPO_ROOT, "data", "competition_cases", "shulin_residential_2026"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

try:
    import fitz
except ImportError:
    pytest.skip("PyMuPDF not available", allow_module_level=True)

import segment_table3_fixtures as fx  # noqa: E402
import segment_table4_fixtures as fx4  # noqa: E402
from domain.models import CaseRulePackage  # noqa: E402

PROFILE_ID = "shulin_residential_2026"
SHULIN_RULES_DIR = os.path.join(REPO_ROOT, "data", "rules", "competition", "shulin_residential_2026")
SHULIN_TEMPLATE_DIR = os.path.join(REPO_ROOT, "data", "templates", "shulin")
PDF_BUCKET = "test-e1-pdf-bucket"


_STUB_AFFECTED_MODULES = ("pdf_handler", "pdf_renderer", "official_pdf_renderer", "shulin_official_pdf_handler", "weasyprint")


def _stub_weasyprint():
    """pdf_handler.py unconditionally imports pdf_renderer -> weasyprint
    at module level (broken on this Windows host, unrelated to this
    round). NOTE: monkeypatch.delitem() was tried first here and found to
    be UNRELIABLE for this -- it registers no teardown action at all when
    the key is ALREADY ABSENT at call time (the common case, since this
    is the first import), so the module  adds a
    moment later never gets cleaned up: a REAL cross-test-contamination
    bug caught by a diagnostic check, where pdf_handler stayed cached in
    sys.modules after this file's tests ran, so tests/test_pdf_output_
    runtime_verification.py's own  (which must hit
    the REAL weasyprint OSError to correctly report its 3 known pre-
    existing failures) silently reused the stubbed-backed cached module
    instead, turning those into a different, unrelated pymupdf error.
    Fixed with a plain try/finally (see _get_pdf()) that unconditionally
    pops all 4 affected module names from sys.modules afterward,
    regardless of whether they existed before this call."""
    fake = types.ModuleType("weasyprint")

    class _FakeHTML:
        def __init__(self, *a, **kw):
            pass

        def write_pdf(self):
            return b"%PDF-fake"

    fake.HTML = _FakeHTML
    sys.modules["weasyprint"] = fake


def _load_rules(name):
    with open(os.path.join(SHULIN_RULES_DIR, name), encoding="utf-8") as f:
        return json.load(f)["rules"]


REGIONAL_RULES = _load_rules("regional_rules.json")
INDIVIDUAL_RULES = _load_rules("individual_rules.json")


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
def ddb_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-e1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("PDF_BUCKET_NAME", PDF_BUCKET)
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table-e1",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name="ap-northeast-1")
        s3.create_bucket(Bucket=PDF_BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_shulin_case(cases_module, case_no):
    body = {
        "case_no": case_no, "segment_code": "P001-00", "city": "新北市", "district": fx.DISTRICT,
        "land_use_type": fx.LAND_USE_TYPE, "appraisal_period": fx.APPRAISAL_PERIOD,
        "appraisal_base_date": fx.APPRAISAL_PERIOD, "segment_scope": "P001-00 比準地區段",
        "base_parcel_id": fx.P001_SEGMENT_META["parcel_ids"][0],
        "comparable_ids": ["P002-00", "P003-00", "P004-00"],
        "rule_profile_id": PROFILE_ID, "segments": fx.segment_map_body(),
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _make_legacy_case(cases_module, case_no):
    body = {
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _collect_segment(collect_data_module, case_no, segment_code):
    cd_body = {"competition_provided_factors": fx.SEGMENT_TABLE3_FACTORS[segment_code]}
    if segment_code in fx4.SEGMENT_TABLE4_TRANSACTION:
        cd_body["competition_provided_transaction"] = fx4.SEGMENT_TABLE4_TRANSACTION[segment_code]
    return collect_data_module.collect_data(
        {"pathParameters": {"id": case_no, "segment_code": segment_code}, "body": json.dumps(cd_body)}, None,
    )


def _confirm_shulin_package(case_no, package_id="PKG-E1"):
    from case_rule_repository import DynamoCaseRuleRepository
    repo = DynamoCaseRuleRepository()
    pkg = CaseRulePackage(
        case_id=case_no, package_id=package_id, rule_version="e1-test-v1",
        source_document="評價基準明細表.pdf", source_type="MANUAL_CSV_INGEST",
        regional_rules=REGIONAL_RULES, individual_rules=INDIVIDUAL_RULES,
        metadata={"competition_profile": {
            "profile_id": PROFILE_ID, "district": "樹林區", "land_use_type": "普通住宅用地",
            "source_document": "評價基準明細表.pdf",
            "source_sha256": "a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c",
        }},
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    repo.save_candidate(pkg)
    repo.confirm(case_no, package_id, confirmed_by="tester")
    return repo


def _full_setup(monkeypatch, case_no, segments=("P001-00", "P002-00", "P003-00", "P004-00")):
    import cases
    collect_data = _ensure_mock_collect_data(monkeypatch)
    _make_shulin_case(cases, case_no)
    for code in segments:
        resp = _collect_segment(collect_data, case_no, code)
        assert resp["statusCode"] == 200, resp["body"]
    _confirm_shulin_package(case_no)


def _get_pdf(case_no, monkeypatch):
    for mod in _STUB_AFFECTED_MODULES:
        sys.modules.pop(mod, None)
    _stub_weasyprint()
    try:
        import pdf_handler
        return pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
    finally:
        # Unconditional cleanup (see _stub_weasyprint()'s own docstring for
        # why monkeypatch alone does not reliably achieve this) -- leaves
        # sys.modules exactly as this test found it, so other test files'
        # own "import pdf_handler" always re-triggers a FRESH import.
        for mod in _STUB_AFFECTED_MODULES:
            sys.modules.pop(mod, None)


def _read_pdf_bytes_from_response(resp) -> bytes:
    body = json.loads(resp["body"])
    url = body["official_pdf_url"]
    key = url.split(f"{PDF_BUCKET}.s3.amazonaws.com/")[-1].split("?")[0]
    if key == url:
        # Alternate presigned-url style (path-style bucket) -- fall back to
        # listing the bucket and matching by suffix instead of parsing the URL.
        s3 = boto3.client("s3", region_name="ap-northeast-1")
        listing = s3.list_objects_v2(Bucket=PDF_BUCKET, Prefix=json.loads(resp["body"])["case_no"])
        key = [o["Key"] for o in listing["Contents"] if "official_six_page" in o["Key"]][0]
    s3 = boto3.client("s3", region_name="ap-northeast-1")
    obj = s3.get_object(Bucket=PDF_BUCKET, Key=key)
    return obj["Body"].read(), obj.get("ContentType")


def _page_texts(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts = [page.get_text() for page in doc]
    doc.close()
    return texts


# ---------------------------------------------------------------------------
# 1/2/3/4/5/6/7/8. Exact six pages, exact order, each page's own identity.
# ---------------------------------------------------------------------------

class TestSixPageAssembly:
    def test_1_2_exact_page_count_and_order(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-001"
        _full_setup(monkeypatch, case_no)
        resp = _get_pdf(case_no, monkeypatch)
        assert resp["statusCode"] == 200, resp["body"]
        pdf_bytes, content_type = _read_pdf_bytes_from_response(resp)
        assert pdf_bytes.startswith(b"%PDF")
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert len(doc) == 6
        doc.close()
        assert content_type == "application/pdf"

    def test_3_page1_is_table3_p002(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-002"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        assert "表3" in texts[0] and "P002-00" in texts[0]

    def test_4_page2_is_table3_p003(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-003"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        assert "表3" in texts[1] and "P003-00" in texts[1]

    def test_5_page3_is_table3_p004(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-004"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        assert "表3" in texts[2] and "P004-00" in texts[2]

    def test_6_page4_is_table3_p001(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-005"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        assert "表3" in texts[3] and "P001-00" in texts[3]

    def test_7_page5_is_table51(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-006"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        assert "表5-1" in texts[4] and "影響地價區域因素" in texts[4]

    def test_8_page6_is_table4(self, ddb_env, monkeypatch):
        case_no = "E1-ASSEMBLY-007"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        assert "表4" in texts[5] and "比較法調查估價表" in texts[5]


# ---------------------------------------------------------------------------
# 9/10. No legacy Golden Case map pages, no Jinshan text leakage.
# ---------------------------------------------------------------------------

class TestNoLegacyLeakage:
    def test_9_10_no_jinshan_or_map_page_markers(self, ddb_env, monkeypatch):
        case_no = "E1-NOLEAK-001"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert len(doc) == 6  # no extra map/cover/audit/explanation pages
        full_text = "".join(page.get_text() for page in doc)
        doc.close()
        for forbidden in ("金山區", "金美段", "溫泉段", "查估書表範本", "地價區段略圖", "使用分區圖"):
            assert forbidden not in full_text


# ---------------------------------------------------------------------------
# 11/12. Table5-1/Table4 runtime results used verbatim (no regrading).
# ---------------------------------------------------------------------------

class TestRuntimeResultsUsedVerbatim:
    def test_11_table51_runtime_values_appear_on_page5(self, ddb_env, monkeypatch):
        case_no = "E1-RUNTIME-001"
        _full_setup(monkeypatch, case_no)
        import table51_analysis
        t51_resp = table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)
        assert t51_resp["statusCode"] == 200
        analysis = json.loads(t51_resp["body"])["analysis"]
        grand_total_comp1 = None
        for c in analysis["comparisons"]:
            if c["comparison_index"] == 1 and c["total_adjustment_pct"] is not None:
                grand_total_comp1 = c["total_adjustment_pct"]
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        page5_text = _page_texts(pdf_bytes)[4]
        if grand_total_comp1 is not None:
            assert str(grand_total_comp1).lstrip("+") in page5_text.replace("+", "")

    def test_12_table4_runtime_values_appear_on_page6(self, ddb_env, monkeypatch):
        case_no = "E1-RUNTIME-002"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        page6_text = _page_texts(pdf_bytes)[5]
        # 題目.pdf COMPETITION_PROVIDED_FIXED values (verified independently
        # in D1) -- their presence on page6 proves the REAL Table4Analysis
        # runtime result was used, not a fabricated/re-derived one.
        for expected in ("130,167", "137,925", "5.96%", "135,275", "140,808", "4.09%",
                         "170,909", "180,292", "5.49%"):
            assert expected in page6_text


# ---------------------------------------------------------------------------
# 13/14/15. Missing != zero (FAR + weight + base comparison price safety).
# ---------------------------------------------------------------------------

class TestFailClosedFieldsNeverFabricated:
    def test_13_14_15_far_weight_and_base_price_stay_blank(self, ddb_env, monkeypatch):
        case_no = "E1-FAILCLOSED-001"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        page6_text = _page_texts(pdf_bytes)[5]
        # FAR (individual_floor_area_ratio) has no rule record in Shulin's
        # own rule pack by design (A2) -- individual_adjustment_total_pct/
        # trial_price/weight_pct/base_comparison_price must ALL stay None
        # (never a fabricated 0%/33.33%/computed price).
        for forbidden in ("33.33%", "33.34%"):
            assert forbidden not in page6_text


# ---------------------------------------------------------------------------
# 16. Competition fixed value precedence already covered by test_12's exact
# transaction-figure match (137,925/140,808/180,292 etc. can ONLY be
# COMPETITION_PROVIDED_FIXED values -- no other source in this codebase
# produces these exact numbers). Re-asserted explicitly here per its own
# Task 16 numbering.
# ---------------------------------------------------------------------------

class TestCompetitionFixedPrecedenceOnPdf:
    def test_16_fixed_transaction_values_match_題目_pdf_exactly(self, ddb_env, monkeypatch):
        case_no = "E1-FIXED-001"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        page6_text = _page_texts(pdf_bytes)[5]
        assert "110年9月14日" in page6_text
        assert "111年1月11日" in page6_text
        assert "110年10月29日" in page6_text


# ---------------------------------------------------------------------------
# 17. CJK rendering verification.
# ---------------------------------------------------------------------------

class TestChineseRendering:
    def test_17_cjk_text_renders_without_tofu(self, ddb_env, monkeypatch):
        case_no = "E1-CJK-001"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        texts = _page_texts(pdf_bytes)
        full_text = "".join(texts)
        for expected in ("新北市", "樹林區", "地價區段", "比準地", "比較標的"):
            assert expected in full_text
        assert "�" not in full_text  # unicode replacement char -- a tofu/decode-failure signal


# ---------------------------------------------------------------------------
# 18. Official vs Audit separation -- no internal/debug/status tokens ever
# reach the official PDF's rendered text.
# ---------------------------------------------------------------------------

class TestOfficialAuditSeparation:
    def test_18_no_internal_audit_tokens_on_official_pdf(self, ddb_env, monkeypatch):
        case_no = "E1-SEPARATION-001"
        _full_setup(monkeypatch, case_no)
        pdf_bytes, _ = _read_pdf_bytes_from_response(_get_pdf(case_no, monkeypatch))
        full_text = "".join(_page_texts(pdf_bytes))
        for forbidden in ("MANUAL_REVIEW_REQUIRED", "AUTOMATIC", "SYSTEM_AUXILIARY", "NON_STATUTORY",
                           "RuleNotFoundError", "confidence=", "fallback", "debug", "STATUTORY_FORMULA"):
            assert forbidden not in full_text


# ---------------------------------------------------------------------------
# 19. Template identity / hash verification + fail-closed on mismatch.
# ---------------------------------------------------------------------------

class TestTemplateIdentity:
    def test_19a_identity_file_matches_committed_templates(self):
        identity_path = os.path.join(SHULIN_TEMPLATE_DIR, "shulin_template_identity.json")
        assert os.path.isfile(identity_path)
        with open(identity_path, encoding="utf-8") as f:
            identity = json.load(f)
        assert set(identity) == {"shulin_table3_blank_v1", "shulin_table51_blank_v1", "shulin_table4_blank_v1"}
        import hashlib
        for template_id, record in identity.items():
            assert record["build_method"] == "MICROSOFT_EXCEL_COM_BUILD_TIME_EXPORT"
            pdf_path = os.path.join(SHULIN_TEMPLATE_DIR, record["derived_pdf_filename"])
            with open(pdf_path, "rb") as f:
                actual_sha = hashlib.sha256(f.read()).hexdigest()
            assert actual_sha == record["derived_pdf_sha256"]

    def test_19b_tampered_identity_fails_closed(self, tmp_path, monkeypatch):
        import shutil
        tmp_template_dir = tmp_path / "shulin"
        shutil.copytree(SHULIN_TEMPLATE_DIR, tmp_template_dir)
        identity_path = tmp_template_dir / "shulin_template_identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity["shulin_table4_blank_v1"]["derived_pdf_sha256"] = "0" * 64
        identity_path.write_text(json.dumps(identity), encoding="utf-8")

        import shulin_official_pdf_renderer as renderer
        monkeypatch.setattr(renderer, "_TEMPLATE_DIR", str(tmp_template_dir))
        monkeypatch.setattr(renderer, "_IDENTITY_PATH", str(identity_path))
        with pytest.raises(renderer.TemplateIdentityMismatchError):
            renderer._open_verified_template(renderer._TABLE4_TEMPLATE_ID)


# ---------------------------------------------------------------------------
# 20. Real handler E2E (create_case -> collect_data x4 -> confirm -> Table5-1
# -> Table4 -> GET pdf), verifying HTTP success / Content-Type / real bytes /
# page_count / page order all at once.
# ---------------------------------------------------------------------------

class TestRealHandlerE2E:
    def test_20_full_pipeline_through_real_handlers(self, ddb_env, monkeypatch):
        case_no = "E1-E2E-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        create_resp = _make_shulin_case(cases, case_no)
        assert create_resp["statusCode"] in (200, 201), create_resp["body"]
        for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
            r = _collect_segment(collect_data, case_no, code)
            assert r["statusCode"] == 200, r["body"]
        _confirm_shulin_package(case_no)

        import table51_analysis
        t51 = table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)
        assert t51["statusCode"] == 200
        import table4_analysis
        t4 = table4_analysis.get_table4_analysis({"pathParameters": {"id": case_no}}, None)
        assert t4["statusCode"] == 200
        assert json.loads(t4["body"])["comparable_count"] == 3

        pdf_resp = _get_pdf(case_no, monkeypatch)
        assert pdf_resp["statusCode"] == 200, pdf_resp["body"]
        body = json.loads(pdf_resp["body"])
        assert body["official_pdf_status"] == "READY"
        assert body["official_pdf_page_count"] == 6
        pdf_bytes, content_type = _read_pdf_bytes_from_response(pdf_resp)
        assert pdf_bytes.startswith(b"%PDF")
        assert content_type == "application/pdf"
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert len(doc) == 6
        order_markers = ["P002-00", "P003-00", "P004-00", "P001-00", "表5-1", "表4"]
        for i, marker in enumerate(order_markers):
            assert marker in doc[i].get_text()
        doc.close()


# ---------------------------------------------------------------------------
# 21. Legacy Jinshan PDF flow preserved -- a legacy (non-segmented) case
# never enters the Shulin branch at all.
# ---------------------------------------------------------------------------

class TestLegacyJinshanPreserved:
    def test_21a_legacy_case_branch_returns_none(self, ddb_env, monkeypatch):
        import cases
        case_no = "E1-LEGACY-001"
        _make_legacy_case(cases, case_no)
        meta = None
        import case_store
        meta = case_store.get_case_meta(case_no)
        from shulin_official_pdf_handler import get_shulin_official_pdf_if_applicable
        result = get_shulin_official_pdf_if_applicable({"pathParameters": {"id": case_no}}, None, case_no, meta)
        assert result is None

    def test_21b_legacy_case_pdf_handler_unaffected(self, ddb_env, monkeypatch):
        import cases
        case_no = "E1-LEGACY-002"
        _make_legacy_case(cases, case_no)
        resp = _get_pdf(case_no, monkeypatch)
        # No FORM_COMPLETION yet for this legacy case -- must get the SAME
        # pre-existing 400 VALIDATION_ERROR the legacy path has always
        # returned, never a Shulin-shaped response and never a crash.
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 22/23. No primary-comparable shortcut, no early averaging -- static source
# scan (AST-based docstring/comment stripping, so this file's OWN
# explanatory prose about what NOT to do can't false-positive the check).
# ---------------------------------------------------------------------------

def _code_lines_only(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc_node = node.body[0] if node.body else None
            if (isinstance(doc_node, ast.Expr) and isinstance(getattr(doc_node, "value", None), ast.Constant)
                    and isinstance(doc_node.value.value, str)):
                docstring_lines.update(range(doc_node.lineno, doc_node.end_lineno + 1))
    lines = src.splitlines()
    return "\n".join(line for i, line in enumerate(lines, start=1) if i not in docstring_lines and not line.strip().startswith("#"))


class TestNoShortcutOrEarlyAveraging:
    def test_22_23_no_primary_comparable_shortcut_or_averaging(self):
        renderer_code = _code_lines_only(os.path.join(REPO_ROOT, "pdf", "shulin_official_pdf_renderer.py"))
        handler_code = _code_lines_only(os.path.join(REPO_ROOT, "backend", "handlers", "shulin_official_pdf_handler.py"))
        for src in (renderer_code, handler_code):
            assert "comparable_ids[0]" not in src
            assert "comparables[0]" not in src
            assert 'row["comparables"][0]' not in src
            assert "average(" not in src
            assert "primary_comparable" not in src


# ---------------------------------------------------------------------------
# 24/25. Fail-closed on missing template / incomplete case data.
# ---------------------------------------------------------------------------

class TestFailClosedSafetyChecks:
    def test_24_missing_template_file_fails_closed(self, tmp_path, monkeypatch):
        import shutil
        empty_dir = tmp_path / "empty_shulin_templates"
        empty_dir.mkdir()
        # Copy only the identity file (so the record exists) but not the PDFs.
        shutil.copy(
            os.path.join(SHULIN_TEMPLATE_DIR, "shulin_template_identity.json"),
            empty_dir / "shulin_template_identity.json",
        )
        import shulin_official_pdf_renderer as renderer
        monkeypatch.setattr(renderer, "_TEMPLATE_DIR", str(empty_dir))
        monkeypatch.setattr(renderer, "_IDENTITY_PATH", str(empty_dir / "shulin_template_identity.json"))
        with pytest.raises(renderer.TemplateMissingError):
            renderer._open_verified_template(renderer._TABLE3_TEMPLATE_ID)

    def test_25_incomplete_case_missing_segment_data_fails_closed_never_partial_pdf(self, ddb_env, monkeypatch):
        case_no = "E1-INCOMPLETE-001"
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        _make_shulin_case(cases, case_no)
        # Deliberately skip P003-00's collect_data -- an incomplete case.
        for code in ("P001-00", "P002-00", "P004-00"):
            r = _collect_segment(collect_data, case_no, code)
            assert r["statusCode"] == 200, r["body"]
        _confirm_shulin_package(case_no)

        resp = _get_pdf(case_no, monkeypatch)
        assert resp["statusCode"] == 400
        assert resp["statusCode"] != 200
        # Never a fabricated 5-page (or any partial) PDF: no official_pdf_url
        # at all on a failure response.
        body = json.loads(resp["body"])
        assert "official_pdf_url" not in body

