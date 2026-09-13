# -*- coding: utf-8 -*-
"""
shulin_official_pdf_handler.py — OFFICIAL-SIX-PAGE-PDF-E1 Task 11/12.

Builds the OFFICIAL six-page PDF for a segment-scoped (Shulin-style,
three-comparable) competition case, reusing the EXISTING runtime results
verbatim (no regrading, no recalculation, no new business logic):

    build_table51_analysis_for_case()  (table51_analysis.py -- C1 runtime)
    get_table4_analysis()              (table4_analysis.py -- D1 runtime)
    _load_segment_regional_factors()   (table51_analysis.py -- per-segment
                                         raw Table3 data, CompetitionProvided
                                         precedence already applied)

wired into pdf/shulin_official_pdf_renderer.py (PyMuPDF-only overlay onto
the 3 DERIVED_RUNTIME_TEMPLATE blank PDFs under data/templates/shulin/).

get_shulin_official_pdf_if_applicable() is the single entry point
backend/handlers/pdf_handler.py calls at the TOP of its own get_pdf():
returns None when this case has no CompetitionSegmentMap at all (i.e. a
legacy Jinshan-style case) -- signalling the caller to fall through to
its existing, UNTOUCHED legacy rendering path -- or a complete HTTP
response dict (success OR a fail-closed error) when it does. This is the
ONLY branch point; nothing else in pdf_handler.py's legacy code changes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import boto3

import case_store
import competition_segments
from rule_engine_factory import RuleProfileNotReadyError
from case_rule_repository import CaseRulePackageInvalidError
from common import response, error_response, timed_step

from table51_analysis import (
    build_table51_analysis_for_case, _load_segment_regional_factors,
    SegmentMapRequiredError, SegmentFactorsNotFoundError,
)
import table4_analysis as table4_analysis_handler

from domain.models import Table4Analysis

import sys
_PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pdf")
if _PDF_DIR not in sys.path:
    sys.path.insert(0, _PDF_DIR)
from shulin_official_pdf_renderer import (  # noqa: E402
    render_shulin_official_six_page_pdf, TABLE3_PAGE_ORDER,
    TemplateMissingError, TemplateIdentityMismatchError,
    ShulinPdfDataUnavailableError, FontUnavailableError,
)

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


def _build_table3_data_by_segment(case_no: str, segment_map, city: Optional[str]) -> Any:
    """Returns {segment_code: {field_id: raw_value}} for all 4 segments,
    in TABLE3_PAGE_ORDER's own segment-role terms (comp1/comp2/comp3/base
    -- generically, not hardcoded to "P002-00" etc.), or a segment_code
    string naming the FIRST segment with no FACTORS record yet (caller
    fails closed on that -- never fabricates a blank Table3 page).

    `zone_range_description` (表3's 區段範圍 cell) is prefixed with this
    segment's own already-known, real city+district (CompetitionSegment.
    district / the case's own city, never fabricated) -- _load_segment_
    regional_factors() itself carries no city/district field_id at all
    (those live on CompetitionSegment/case meta, not the REG-* regional
    factor catalog), so without this the official PDF would never display
    either, even though both are genuine, already-verified case facts."""
    ordered_segments = sorted(segment_map.comparables, key=lambda s: s.comparison_index or 0)
    ordered_segments.append(segment_map.base_segment)
    data: Dict[str, Dict[str, Any]] = {}
    for seg in ordered_segments:
        factors = _load_segment_regional_factors(case_no, seg.segment_code)
        if factors is None:
            return seg.segment_code  # signals "missing", per docstring above
        seg_data = {fi.field_id: fi.raw_value for fi in factors}
        city_district = f"{city or ''}{seg.district or ''}"
        existing_zone = seg_data.get("zone_range_description")
        seg_data["zone_range_description"] = f"{city_district}{existing_zone or ''}"
        data[seg.segment_code] = seg_data
    return data


def get_shulin_official_pdf_if_applicable(event, context, case_no: str, meta: dict) -> Optional[Dict[str, Any]]:
    segment_map = competition_segments.get_segment_map(case_no)
    if segment_map is None:
        return None  # not a segment-scoped case -- caller falls through to legacy Jinshan path

    with timed_step(case_no, "get_shulin_official_pdf"):
        # Task 12 fail-closed checks: segment map missing already handled
        # above (None); every other required upstream input is checked
        # explicitly below -- NEVER emit a partially-valid PDF.
        try:
            table51_analysis, _rule_resolution = build_table51_analysis_for_case(case_no, meta)
        except SegmentMapRequiredError:
            return error_response(400, "SEGMENT_MAP_REQUIRED", "案件尚未定義 segment map，無法產出正式PDF")
        except SegmentFactorsNotFoundError as e:
            return error_response(400, "VALIDATION_ERROR", str(e))
        except RuleProfileNotReadyError as e:
            return error_response(409, "RULE_PROFILE_NOT_READY", str(e))
        except CaseRulePackageInvalidError as e:
            return error_response(409, "CASE_RULE_INVALID", f"{e}. MANUAL_REVIEW_REQUIRED")

        table4_resp = table4_analysis_handler.get_table4_analysis(
            {"pathParameters": {"id": case_no}}, context,
        )
        if table4_resp.get("statusCode") != 200:
            return table4_resp  # already a clean, fail-closed error_response -- propagate verbatim
        table4_analysis = Table4Analysis.model_validate(json.loads(table4_resp["body"])["analysis"])

        table3_data_or_missing_segment = _build_table3_data_by_segment(case_no, segment_map, meta.get("city"))
        if isinstance(table3_data_or_missing_segment, str):
            return error_response(
                400, "VALIDATION_ERROR",
                f"segment {table3_data_or_missing_segment!r} 尚未執行 collect_data，無法產出表3頁面",
            )
        table3_data_by_segment = table3_data_or_missing_segment
        missing_pages = [seg for seg in TABLE3_PAGE_ORDER if seg not in table3_data_by_segment]
        if missing_pages:
            return error_response(400, "VALIDATION_ERROR", f"缺少下列 segment 之表3資料：{missing_pages}")

        try:
            pdf_bytes = render_shulin_official_six_page_pdf(
                table3_data_by_segment, table51_analysis, table4_analysis,
                case_no=meta.get("case_no") or case_no,
                appraisal_base_date=meta.get("appraisal_base_date"),
            )
        except (TemplateMissingError, TemplateIdentityMismatchError) as e:
            return error_response(500, "OFFICIAL_PDF_TEMPLATE_UNAVAILABLE", str(e))
        except FontUnavailableError as e:
            return error_response(500, "OFFICIAL_PDF_FONT_UNAVAILABLE", str(e))
        except ShulinPdfDataUnavailableError as e:
            return error_response(400, "VALIDATION_ERROR", str(e))

        now = datetime.now()
        s3 = _s3_client()
        key = f"{case_no}/official_six_page_{now.strftime('%Y%m%d%H%M%S')}.pdf"
        s3.put_object(Bucket=PDF_BUCKET, Key=key, Body=pdf_bytes, ContentType="application/pdf")
        official_pdf_url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": PDF_BUCKET, "Key": key}, ExpiresIn=3600,
        )

        return response(200, {
            "case_no": case_no, "generated_at": now.isoformat(),
            "forms": {"official_form": {"pdf_url": official_pdf_url, "title": "官方六頁式查估書表（表3x4+表5-1+表4）"}},
            "pdf_url": official_pdf_url,
            "official_pdf_url": official_pdf_url,
            "official_pdf_status": "READY",
            "official_pdf_page_count": 6,
        })
