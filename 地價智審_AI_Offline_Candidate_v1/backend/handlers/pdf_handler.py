# -*- coding: utf-8 -*-
"""pdf.py — GET /api/cases/{id}/pdf. Runs Phase 5's PdfRenderer and stores
the output in S3, returning presigned URLs (never returns raw PDF bytes
through API Gateway's payload limits).

STEP 5 §7 fix (docs/audit/COMPETITION_E2E_PHASE5_REPORT.md): before this
round, get_pdf() rendered ONLY 表4+表5-2 (via FormCompletionEngine's
FormCompletionResult, which itself never emits 表1 FieldCompletion
records -- 表1 is raw Data Acquisition Layer survey data, not a Rule/
Calculation Engine output). pdf/pdf_renderer.py::build_table1_pdf_bytes()
already existed and was already exercised by tests/test_phase5_golden_
pipeline.py, but nothing in the PRODUCTION handler ever called it -- this
is the exact "Mock path 有表1, Production 只回表4+表5-2" gap STEP 5
identified. Fixed by ALSO rendering 表1 here (from the same base_regional_
factors case_reconstruction.py already reconstructs for complete_form.py/
review.py) and returning BOTH presigned URLs, clearly labeled -- two
files, not a forced single-PDF merge (no pypdf/PyPDF2 dependency exists
in backend/requirements-pdf.txt today, and merging would be unrelated,
higher-risk scope creep for this fix). PdfRenderer/build_table1_pdf_bytes
themselves are UNCHANGED -- this is a handler-level wiring fix, not a
Core Freeze engine (docs/audit/COMPETITION_E2E_PHASE5_REPORT.md §0's
frozen list is GradeEngine/AdjustmentEngine/CalculationEngine/
FormCompletionEngine/CaseRuleRepository/EvaluationStandardImporter, none
of which this file is or calls into for its own logic)."""
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
from case_reconstruction import build_case_and_regional_factors  # noqa: E402

from domain.models import FormCompletionResult, FieldCompletion  # noqa: E402
from pdf_renderer import PdfRenderer, build_table1_pdf_bytes  # noqa: E402
from official_pdf_renderer import (  # noqa: E402
    render_official_pdf, TemplateLayoutUnconfirmedError, FontUnavailableError,
)
from facility_confirmation_repository import default_facility_confirmation_repository  # noqa: E402
from shulin_official_pdf_handler import get_shulin_official_pdf_if_applicable  # noqa: E402

PDF_BUCKET = os.environ.get("PDF_BUCKET_NAME", "ai-valuation-pdfs")
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


def _as_form_completion_result(raw: dict) -> FormCompletionResult:
    """STEP5 §FINAL-GATE fix: FormCompletionResult/FieldCompletion both
    declare `model_config = ConfigDict(extra="forbid")` (domain/models.py),
    but the dict stored under case_store's "FORM_COMPLETION" record is NOT
    a pure FormCompletionResult dump -- it is complete_form.py's own
    ENRICHED version:
      - FormCompletionResult.model_dump_json() itself already serializes
        3 @computed_field properties (completed_count/manual_review_count/
        unknown_count) that are readable output-only, never accepted back
        as constructor input by model_validate() -- this alone breaks a
        pure round-trip, with zero relation to any STEP5/case-scoped-rule
        change.
      - complete_form.py additionally stamps `rule_source_type` onto every
        field dict and `rule_resolution_status`/`rule_package_id`/
        `rule_resolution_warnings` at the top level (STEP2 traceability,
        handler-layer-only, never part of FormCompletionEngine's own
        domain model).
      - case_store.get_record() appends its own `_updated_at`.
    review.py's build_submitted_from_form_completion() never hit this
    because it treats the stored record as a plain dict and never re-
    validates it against FormCompletionResult -- pdf_handler.py is the
    ONLY caller that does, and until WeasyPrint could actually run far
    enough to reach this line (blocked by an unrelated native-library
    limitation on the original STEP5 dev host), this ValidationError was
    never observed. Filters BOTH the top-level dict and every nested
    field dict down to each model's own declared (non-computed)
    model_fields, via introspection rather than a hardcoded key list, so
    a future field added to either model needs no change here."""
    allowed_top = set(FormCompletionResult.model_fields)
    allowed_field = set(FieldCompletion.model_fields)
    cleaned = {k: v for k, v in raw.items() if k in allowed_top}
    cleaned["fields"] = [
        {k: v for k, v in f.items() if k in allowed_field} for f in raw.get("fields", [])
    ]
    return FormCompletionResult.model_validate(cleaned)


def get_pdf(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    with timed_step(case_no, "get_pdf"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"案件不存在：{case_no}")

        # OFFICIAL-SIX-PAGE-PDF-E1 Task 11/12: a segment-scoped competition
        # case (CompetitionSegmentMap present, e.g. shulin_residential_2026)
        # NEVER falls through to the legacy single-comparable Jinshan
        # renderer below -- get_shulin_official_pdf_if_applicable() returns
        # None only when this case has no segment map at all, in which case
        # the ORIGINAL legacy logic runs completely untouched.
        shulin_result = get_shulin_official_pdf_if_applicable(event, context, case_no, meta)
        if shulin_result is not None:
            return shulin_result

        form_completion = case_store.get_record(case_no, "FORM_COMPLETION")
        factors_record = case_store.get_record(case_no, "FACTORS")
        if form_completion is None or factors_record is None:
            return error_response(400, "VALIDATION_ERROR", "案件尚未完成書表填寫，無法產出PDF")

        try:
            result = _as_form_completion_result(form_completion)
        except Exception as e:
            return error_response(400, "VALIDATION_ERROR", f"已儲存之書表資料格式錯誤，無法產出PDF：{e}")
        renderer = PdfRenderer()
        now = datetime.now()
        s3 = _s3_client()

        table4_52_bytes = renderer.render_form(result, "表4+表5-2（系統整合輸出）", "表4", case_no, meta["segment_code"])
        table4_52_key = f"{case_no}/form4_5-2_{now.strftime('%Y%m%d%H%M%S')}.pdf"
        s3.put_object(Bucket=PDF_BUCKET, Key=table4_52_key, Body=table4_52_bytes, ContentType="application/pdf")
        table4_52_url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": PDF_BUCKET, "Key": table4_52_key}, ExpiresIn=3600,
        )

        try:
            case, regional_base, regional_comp = build_case_and_regional_factors(case_no, meta, factors_record)
        except Exception as e:
            return error_response(400, "VALIDATION_ERROR", f"案件資料格式錯誤，無法產出表1：{e}")

        table1_bytes = build_table1_pdf_bytes(
            case_no, case.segment_code, case.segment_scope, regional_base, now.isoformat(),
        )
        table1_key = f"{case_no}/form1_{now.strftime('%Y%m%d%H%M%S')}.pdf"
        s3.put_object(Bucket=PDF_BUCKET, Key=table1_key, Body=table1_bytes, ContentType="application/pdf")
        table1_url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": PDF_BUCKET, "Key": table1_key}, ExpiresIn=3600,
        )

        # PDF-OFFICIAL-1 Task 11: a SECOND, independent PDF output in the
        # official 查估書表 layout, alongside (never replacing) the
        # existing Audit PDFs above. official_pdf_renderer.py imports
        # NOTHING from engine/ -- it only overlays the SAME already-
        # computed `result`/`regional_base` this handler already built for
        # the Audit PDFs, via PyMuPDF onto data/templates/official_
        # appraisal_form_v1.pdf. A template-fingerprint mismatch or a
        # missing CJK font is reported honestly (official_pdf_status),
        # never allowed to fail the whole request -- the Audit PDFs above
        # are unaffected either way (Task 10's "fallback到Audit PDF").
        official_pdf_url = None
        official_pdf_status = "READY"
        try:
            # TABLE1-SAFE-WIRING-1: road_name comes straight from
            # FACTORS.points (already-collected NormalizedDataPoints,
            # unmodified from what collect_data.py persisted) -- never
            # routed through regional_base/Rule-Grade-Adjustment-
            # Calculation-FormCompletion Engine (Core Freeze).
            # FACILITY-CONFIRMATION-GATE-1: utility/funeral/major_station
            # rows are driven ONLY by human-CONFIRMED facility_
            # confirmation_repository records -- raw provider evidence
            # (FACTORS.points' substation_name/.../official_facility_
            # evidence["STATION"]) is never read here or by the renderer
            # for these 8 rows anymore; a case with no confirmation
            # records yet (or every record still PENDING/REJECTED) simply
            # gets an empty dict back, which the renderer treats as
            # "leave every one of these 8 rows blank" -- never a crash,
            # never a silent fallback to raw evidence (Task 10).
            # FACILITY-STALE-CONFIRMATION-GATE-1: get_active_confirmed_
            # selections() (NOT get_confirmed_selections()) -- a CONFIRMED
            # record whose evidence has since drifted (stale=True) is
            # withheld here exactly like PENDING/REJECTED, even though its
            # confirmed_selection/confirmed_by history is still preserved
            # in the repository for a reviewer to see and reconfirm.
            confirmed_facility_selections = default_facility_confirmation_repository().get_active_confirmed_selections(case_no)
            official_bytes = render_official_pdf(
                case, regional_base, regional_comp, json.loads(result.model_dump_json())["fields"],
                facility_points=factors_record.get("points"),
                confirmed_facility_selections=confirmed_facility_selections,
            )
            official_key = f"{case_no}/official_form_{now.strftime('%Y%m%d%H%M%S')}.pdf"
            s3.put_object(Bucket=PDF_BUCKET, Key=official_key, Body=official_bytes, ContentType="application/pdf")
            official_pdf_url = s3.generate_presigned_url(
                "get_object", Params={"Bucket": PDF_BUCKET, "Key": official_key}, ExpiresIn=3600,
            )
            if render_official_pdf.last_overflow_fields:
                official_pdf_status = "PARTIAL_FIELD_OVERFLOW_MANUAL_REVIEW"
        except TemplateLayoutUnconfirmedError:
            official_pdf_status = "TEMPLATE_LAYOUT_UNCONFIRMED"
        except FontUnavailableError:
            official_pdf_status = "FONT_UNAVAILABLE"
        except Exception:
            official_pdf_status = "OFFICIAL_PDF_GENERATION_FAILED"

        forms = {
            "表1": {"pdf_url": table1_url, "title": "地價區段勘查表"},
            "表4+表5-2": {"pdf_url": table4_52_url, "title": "比較法調查估價表 ＋ 影響地價區域因素分析明細表"},
        }
        if official_pdf_url is not None:
            forms["official_form"] = {"pdf_url": official_pdf_url, "title": "官方查估書表格式（表1+表5-2+表4）"}

        return response(200, {
            "case_no": case_no, "generated_at": now.isoformat(),
            "forms": forms,
            # Backward-compatible top-level field (pre-STEP-5 API contract):
            # existing callers reading `pdf_url` still get the 表4+表5-2 PDF.
            "pdf_url": table4_52_url,
            "official_pdf_url": official_pdf_url,
            "official_pdf_status": official_pdf_status,
            "audit_pdf_url": table4_52_url,
        })
