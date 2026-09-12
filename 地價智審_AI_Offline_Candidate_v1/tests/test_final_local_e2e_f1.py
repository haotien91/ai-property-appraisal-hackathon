# -*- coding: utf-8 -*-
"""
FINAL-LOCAL-E2E-F1 tests.

Minimal, high-value validation of the complete Shulin local flow -- NOT a
re-verification of C1/D1/E1's own already-tested internals (see
tests/test_table51_three_comparable_c1.py / test_table4_three_comparable_
d1.py / test_official_six_page_pdf_e1.py for those). Reuses the same
moto+real-handler pattern established in those files.

Note on review()/get_result(): both are LEGACY handlers built for the
single-comparable Jinshan flow -- review() reads the bare "FACTORS"
DynamoDB record (via build_case_and_regional_factors()), which a segment-
scoped Shulin case never writes (only "FACTORS#<segment_code>" records
exist). No prior round (C1/D1/E1) built a segment-scoped replacement for
review()/get_result(), and building one now would be new development,
out of this round's explicit "validation only" scope. test_1 below calls
both handlers and asserts on their REAL, honest behavior rather than
forcing an assumed outcome either way.
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import sys

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
PDF_BUCKET = "test-f1-pdf-bucket"
_STUB_AFFECTED_MODULES = ("pdf_handler", "pdf_renderer", "official_pdf_renderer", "shulin_official_pdf_handler", "weasyprint")


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
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-f1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("PDF_BUCKET_NAME", PDF_BUCKET)
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table-f1",
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


def _confirm_shulin_package(case_no, package_id="PKG-F1"):
    from case_rule_repository import DynamoCaseRuleRepository
    repo = DynamoCaseRuleRepository()
    pkg = CaseRulePackage(
        case_id=case_no, package_id=package_id, rule_version="f1-test-v1",
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


def _get_pdf(case_no):
    for mod in _STUB_AFFECTED_MODULES:
        sys.modules.pop(mod, None)
    import types
    fake = types.ModuleType("weasyprint")

    class _FakeHTML:
        def __init__(self, *a, **kw):
            pass

        def write_pdf(self):
            return b"%PDF-fake"

    fake.HTML = _FakeHTML
    sys.modules["weasyprint"] = fake
    try:
        import pdf_handler
        return pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
    finally:
        for mod in _STUB_AFFECTED_MODULES:
            sys.modules.pop(mod, None)


def _read_pdf_bytes(resp):
    body = json.loads(resp["body"])
    s3 = boto3.client("s3", region_name="ap-northeast-1")
    listing = s3.list_objects_v2(Bucket=PDF_BUCKET, Prefix=body["case_no"])
    key = [o["Key"] for o in listing["Contents"] if "official_six_page" in o["Key"]][0]
    return s3.get_object(Bucket=PDF_BUCKET, Key=key)["Body"].read()


def _full_shulin_setup(monkeypatch, case_no):
    import cases
    collect_data = _ensure_mock_collect_data(monkeypatch)
    create_resp = _make_shulin_case(cases, case_no)
    assert create_resp["statusCode"] in (200, 201), create_resp["body"]
    collect_results = {}
    for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
        r = _collect_segment(collect_data, case_no, code)
        collect_results[code] = r
    _confirm_shulin_package(case_no)
    return collect_results


# ---------------------------------------------------------------------------
# 1. Full local handler chain.
# ---------------------------------------------------------------------------

class TestFullLocalFlow:
    def test_1_full_chain_create_collect_confirm_table51_table4_review_pdf_result(self, ddb_env, monkeypatch):
        case_no = "F1-FULLFLOW-001"
        collect_results = _full_shulin_setup(monkeypatch, case_no)
        for code, r in collect_results.items():
            assert r["statusCode"] == 200, f"{code}: {r['body']}"

        import table51_analysis
        t51 = table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)
        assert t51["statusCode"] == 200, t51["body"]

        import table4_analysis
        t4 = table4_analysis.get_table4_analysis({"pathParameters": {"id": case_no}}, None)
        assert t4["statusCode"] == 200, t4["body"]
        assert json.loads(t4["body"])["comparable_count"] == 3

        pdf_resp = _get_pdf(case_no)
        assert pdf_resp["statusCode"] == 200, pdf_resp["body"]
        pdf_body = json.loads(pdf_resp["body"])
        assert pdf_body["official_pdf_page_count"] == 6

        import result as result_module
        result_resp = result_module.get_result({"pathParameters": {"id": case_no}}, None)
        # get_result()/review() are LEGACY, single-comparable handlers (see
        # module docstring) -- no segment-scoped equivalent exists from any
        # prior round, and building one is out of this round's scope.
        # get_result() itself never 404s once case meta exists (it treats
        # missing FORM_COMPLETION/REVIEW_RESULT as empty, not an error) --
        # asserted here as the actual, honest behavior for a Shulin case.
        assert result_resp["statusCode"] == 200
        result_body = json.loads(result_resp["body"])
        assert result_body["case_no"] == case_no

        import review as review_module
        review_resp = review_module.review({"pathParameters": {"id": case_no}, "body": "{}"}, None)
        # review() requires the bare "FACTORS" record (legacy single-
        # comparable shape) -- a Shulin case only ever writes segment-
        # scoped "FACTORS#<segment_code>" records, so this MUST fail
        # closed with a clean VALIDATION_ERROR, never a crash and never a
        # fabricated success.
        assert review_resp["statusCode"] == 400
        assert json.loads(review_resp["body"])["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 2. P002/P003/P004 lineage -- no cross-contamination.
# ---------------------------------------------------------------------------

class TestSegmentLineage:
    def test_2_table51_and_table4_lineage_per_comparable(self, ddb_env, monkeypatch):
        case_no = "F1-LINEAGE-001"
        _full_shulin_setup(monkeypatch, case_no)

        import table51_analysis
        t51 = table51_analysis.get_table51_analysis({"pathParameters": {"id": case_no}}, None)
        t51_body = json.loads(t51["body"])["analysis"]
        assert t51_body["base_segment_code"] == "P001-00"
        by_index = {c["comparison_index"]: c for c in t51_body["comparisons"]}
        assert by_index[1]["comparable_segment_code"] == "P002-00"
        assert by_index[2]["comparable_segment_code"] == "P003-00"
        assert by_index[3]["comparable_segment_code"] == "P004-00"

        import table4_analysis
        t4 = table4_analysis.get_table4_analysis({"pathParameters": {"id": case_no}}, None)
        t4_body = json.loads(t4["body"])["analysis"]
        assert t4_body["base_segment_code"] == "P001-00"
        t4_by_index = {c["comparison_index"]: c for c in t4_body["comparisons"]}
        for idx, expected_seg in ((1, "P002-00"), (2, "P003-00"), (3, "P004-00")):
            t4_comp = t4_by_index[idx]
            t51_comp = by_index[idx]
            assert t4_comp["comparable_segment_code"] == expected_seg
            # Table4's regional_adjustment_pct must come from the SAME
            # comparable's own Table5-1 total -- never a different one,
            # never averaged.
            assert t4_comp["regional_adjustment_source_comparison_index"] == idx
            if t51_comp["total_adjustment_pct"] is not None:
                assert t4_comp["regional_adjustment_pct"] == t51_comp["total_adjustment_pct"]


# ---------------------------------------------------------------------------
# 3. Final six-page PDF.
# ---------------------------------------------------------------------------

class TestFinalPdfSmoke:
    def test_3_final_pdf_six_pages_correct_order_no_legacy_leakage(self, ddb_env, monkeypatch):
        case_no = "F1-PDF-001"
        _full_shulin_setup(monkeypatch, case_no)
        pdf_bytes = _read_pdf_bytes(_get_pdf(case_no))
        assert pdf_bytes.startswith(b"%PDF")
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert len(doc) == 6
        order_markers = ["P002-00", "P003-00", "P004-00", "P001-00", "表5-1", "表4"]
        full_text = ""
        for i, marker in enumerate(order_markers):
            page_text = doc[i].get_text()
            full_text += page_text
            assert marker in page_text
        doc.close()
        for forbidden in ("金山區", "金美段", "溫泉段", "查估書表範本", "地價區段略圖", "使用分區圖"):
            assert forbidden not in full_text


# ---------------------------------------------------------------------------
# 4. Safety: FAR / weight / missing / Shulin-never-falls-back-to-Jinshan.
# ---------------------------------------------------------------------------

class TestSafetyChecks:
    def test_4_far_weight_and_missing_stay_blank_never_fabricated(self, ddb_env, monkeypatch):
        case_no = "F1-SAFETY-001"
        _full_shulin_setup(monkeypatch, case_no)
        pdf_bytes = _read_pdf_bytes(_get_pdf(case_no))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page6_text = doc[5].get_text()
        doc.close()
        for forbidden in ("33.33%", "33.34%", "MANUAL_REVIEW_REQUIRED", "SYSTEM_AUXILIARY"):
            assert forbidden not in page6_text

        import table4_analysis
        t4 = table4_analysis.get_table4_analysis({"pathParameters": {"id": case_no}}, None)
        t4_body = json.loads(t4["body"])["analysis"]
        for comp in t4_body["comparisons"]:
            # FAR has no rule record in Shulin's own pack by design (A2) --
            # individual_adjustment_total_pct must stay None (never a
            # fabricated 0/partial sum), and weight must never be auto-
            # filled without HUMAN_CONFIRMED status.
            assert comp["individual_adjustment_total_pct"] is None
            assert comp["weight_status"] != "HUMAN_CONFIRMED"
            assert comp["weight_pct"] is None

    def test_5_shulin_case_never_falls_back_to_jinshan_renderer(self, ddb_env, monkeypatch):
        import cases
        case_no = "F1-NOFALLBACK-001"
        _make_legacy_case(cases, case_no)
        import case_store
        meta = case_store.get_case_meta(case_no)
        from shulin_official_pdf_handler import get_shulin_official_pdf_if_applicable
        # A genuinely legacy (non-segmented) case -- the branch must return
        # None (signal: fall through to the UNTOUCHED legacy path), never
        # attempt to build a Shulin six-page PDF for it.
        assert get_shulin_official_pdf_if_applicable({"pathParameters": {"id": case_no}}, None, case_no, meta) is None

        case_no2 = "F1-NOFALLBACK-002"
        _full_shulin_setup(monkeypatch, case_no2)
        pdf_resp = _get_pdf(case_no2)
        body = json.loads(pdf_resp["body"])
        # A genuine Shulin case must get the six-page official form, never
        # silently degrade to the single-comparable Jinshan shape (which
        # would show "表1"/"表4+表5-2" combined titles instead).
        assert body.get("official_pdf_page_count") == 6
        assert "official_form" not in body.get("forms", {}) or "表1" not in body.get("forms", {}).get("official_form", {}).get("title", "")


# ---------------------------------------------------------------------------
# No primary-comparable shortcut / early averaging (static scan, reused
# convention from D1/E1 -- AST docstring stripping avoids false positives
# on this file's OWN explanatory prose).
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


class TestNoShortcutRegression:
    def test_6_no_primary_comparable_shortcut_anywhere_in_shulin_path(self):
        for path in (
            os.path.join(REPO_ROOT, "backend", "handlers", "shulin_official_pdf_handler.py"),
            os.path.join(REPO_ROOT, "backend", "handlers", "table4_analysis.py"),
            os.path.join(REPO_ROOT, "pdf", "shulin_official_pdf_renderer.py"),
        ):
            src = _code_lines_only(path)
            assert "comparable_ids[0]" not in src
            assert "comparables[0]" not in src
            assert "average(" not in src
