# -*- coding: utf-8 -*-
"""
review.py — POST /api/cases/{id}/review. Runs Phase 6's AuditEngine
against the case's stored raw factors, re-deriving expected grades/
adjustments via RuleEngine and comparing against what was actually
submitted (deterministic Smart Review, no LLM involved in the pass/fail
determination itself).

Two submission sources, selected via the request body's optional
`submission_source` field (Phase E, backward compatible -- omitted
defaults to the ORIGINAL "FORM_COMPLETION" behavior unchanged):

- "FORM_COMPLETION" (default): the pre-existing MVP behavior, semantics
  UNCHANGED by Phase E -- since no real PDF extraction was wired into this
  endpoint until Phase E, there is still no independent "submitted" data
  source distinct from what complete_form.py already computed; this path
  audits the case's own computed FORM_COMPLETION result against a fresh
  independent re-computation. A REAL regression WAS found and fixed here
  while wiring Phase E (not a new feature): the Grade Representation
  Contract round made AuditEngine._review_regional_factors() (shared code,
  unaware of submission_source) key its PRIMARY correctness check off
  submitted_grade_CODE, but this function only ever populated submitted_
  grades (TEXT) -- so every regional factor silently started resolving to
  MISSING instead of PASSED for this path specifically, uncaught by any
  existing test (none asserted PASSED for a regional factor through this
  handler). Fixed via _grade_code_for_text() below: a text->code lookup
  against the SAME canonical rule table, not a fresh raw-value grading.
- "DOCUMENT": the real submission source Phase E adds -- an uploaded PDF's
  LocalExtractionProvider output (document_extract.py's extraction.json
  artifact) plus any HumanConfirmationRecords (document_confirm.py's
  confirmations.json artifact), reconstructed via the FROZEN, unmodified
  engine/extraction_to_submitted_form.py::build_submitted_form_from_
  extraction(). Requires `document_id` in the request body. Selection is
  ALWAYS explicit (submission_source must literally say "DOCUMENT") --
  never inferred from "an extraction record happens to exist for this
  case", per this round's guardrail.

Both paths still require a FACTORS record (independently-sourced
regional_base_factors/road_width_evidence/plan identification -- the
"official/expected" side AuditEngine grades FROM, per the Submitted vs
Official/Reference Separation principle) -- DOCUMENT mode only changes
HOW `submitted` (the SubmittedFormData) is built, never how the
independent reference side is built.
"""
from __future__ import annotations

import sys
import os
import json

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402
from case_reconstruction import (  # noqa: E402
    build_case_and_regional_factors, extract_submitted_land_use_ratio_fields,
    extract_plan_identification, extract_submitted_main_road_width, extract_road_width_evidence,
)

from rule_engine import RuleEngine  # noqa: E402
from audit_engine import AuditEngine, SubmittedFormData  # noqa: E402
from extraction_to_submitted_form import build_submitted_form_from_extraction  # noqa: E402
from domain.models import ExtractedField, HumanConfirmationRecord  # noqa: E402

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _rule_engine():
    data_dir = runtime_paths.data_dir()
    with open(os.path.join(data_dir, "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(data_dir, "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return RuleEngine(reg + ind)


def _grade_code_for_text(rule_engine, city, district, land_use_type, factor, grade_text, rule_set):
    """FORM_COMPLETION-path-only helper (see module docstring's Phase E
    regression note): given a factor's already-computed grade TEXT --
    itself a SUBMITTED representation (FormCompletionEngine's own prior
    RuleEngine.grade() call on the raw value, not a fresh raw-fact
    grading performed here) -- finds the matching grade_code via the SAME
    canonical rule table. This mirrors RuleValidator.validate_grade_
    representation()'s own code->text lookup (Grade Representation
    Contract, Phase D), just in reverse (text->code); it is NOT extraction
    inferring a grade from a raw property fact. Returns None (never a
    guess) if the factor's rules can't be found, or grade_text doesn't
    match exactly one grade band for this factor."""
    try:
        ni = rule_engine.normalize(city, district, land_use_type, factor, None, unit=None, rule_set=rule_set)
        candidates = rule_engine.select_rules(ni)
    except Exception:
        return None
    matches = {str(r["grade_code"]) for r in candidates if r.get("grade") == grade_text}
    if len(matches) != 1:
        return None
    return next(iter(matches))


def build_submitted_from_form_completion(
    case_no: str, form_completion: dict,
    rule_engine=None, case=None, regional_base=None,
) -> SubmittedFormData:
    """Builds a full SubmittedFormData (grades + grade codes + adjustments
    + cross-form totals) from an already-computed FormCompletionResult's
    fields, using the exact field_id conventions FormCompletionEngine
    emits (see engine/form_completion_engine.py). As of the Phase 8A
    cross-form wiring fix, 表5-2's regional_total_adjustment_{cid} and
    表4's region_adjustment_rate_{cid} are two INDEPENDENT stored fields
    (not one field read twice) -- each is read from its own field_id here.

    rule_engine/case/regional_base are OPTIONAL (default None, in which
    case submitted_grade_codes is simply left empty for this path, same
    as before Phase E) -- when supplied by review()'s only real caller,
    also populates submitted_grade_codes via _grade_code_for_text() above,
    fixing the Phase E regression documented in this module's docstring."""
    submitted = SubmittedFormData(case_no=case_no)
    factor_by_field_id = {fi.field_id: fi.factor for fi in (regional_base or [])}

    for f in form_completion.get("fields", []):
        field_id = f["field_id"]

        if "_differential_rate_" in field_id and f.get("adjustment") is not None:
            base_field, cid = field_id.split("_differential_rate_", 1)
            submitted.submitted_adjustments[(base_field, cid)] = f["adjustment"]

        elif "_adjustment_pct_" in field_id and f.get("grade") is not None:
            base_field, cid = field_id.split("_adjustment_pct_", 1)
            grade_str = f["grade"]
            if grade_str and grade_str.startswith("base="):
                base_grade = grade_str.split(",")[0].replace("base=", "").strip()
                submitted.submitted_grades[(base_field, cid)] = base_grade
                if rule_engine is not None and case is not None and base_field in factor_by_field_id:
                    code = _grade_code_for_text(
                        rule_engine, case.city, case.district, case.land_use_type,
                        factor_by_field_id[base_field], base_grade, rule_set="regional",
                    )
                    if code is not None:
                        submitted.submitted_grade_codes[(base_field, cid)] = code

        elif field_id.startswith("region_adjustment_rate_"):
            # 表4's own field -- independent identity from 表5-2's field below.
            cid = field_id[len("region_adjustment_rate_"):]
            submitted.submitted_totals[f"region_adjustment_rate_{cid}"] = f.get("final_value")

        elif field_id.startswith("regional_total_adjustment_"):
            # 表5-2's own field -- independent identity, previously did not
            # exist at all as a distinct stored record (Phase 8A fix).
            cid = field_id[len("regional_total_adjustment_"):]
            submitted.submitted_totals[f"regional_total_{cid}"] = f.get("final_value")

    return submitted


def _load_document_submission(case_no: str, document_id: str, comparable_ids):
    """DOCUMENT-source reconstruction: reads the extraction.json +
    (optional) confirmations.json artifacts document_extract.py/
    document_confirm.py wrote to DocumentBucket, then hands them to the
    FROZEN build_submitted_form_from_extraction() exactly as tests/
    test_document_extraction_e2e.py already proves works against a real
    Golden PDF. Returns (SubmittedFormData, None) on success or
    (None, error_response(...)) on a handled failure -- never raises past
    this function for an expected "not found yet" condition."""
    extraction_meta = case_store.get_record(case_no, case_store.extraction_sk(document_id))
    if extraction_meta is None:
        return None, error_response(
            400, "VALIDATION_ERROR", f"找不到文件 {document_id} 之擷取結果，請先呼叫 .../extract",
        )

    s3 = _s3_client()
    extraction_obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=extraction_meta["extraction_s3_key"])
    extraction_artifact = json.loads(extraction_obj["Body"].read().decode("utf-8"))
    fields = [ExtractedField.model_validate(f) for f in extraction_artifact.get("fields", [])]

    # Confirmations are OPTIONAL -- a document with no low-confidence
    # fields may never have had confirm called at all; resolve_confirmed_
    # values() (inside build_submitted_form_from_extraction, unmodified)
    # already handles confirmations=[] correctly: a still-flagged field
    # with no confirmation resolves to None and is never written into
    # SubmittedFormData, so it can never reach AuditEngine/RuleValidator.
    confirmations = []
    confirmation_meta = case_store.get_record(case_no, case_store.confirmation_sk(document_id))
    if confirmation_meta is not None:
        confirmation_obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=confirmation_meta["confirmation_s3_key"])
        confirmation_artifact = json.loads(confirmation_obj["Body"].read().decode("utf-8"))
        confirmations = [HumanConfirmationRecord.model_validate(c)
                          for c in confirmation_artifact.get("confirmations", [])]

    submitted = build_submitted_form_from_extraction(case_no, fields, confirmations, comparable_ids=comparable_ids)
    return submitted, None


def review(event, context):
    case_no = event.get("pathParameters", {}).get("id")
    body = parse_body(event)
    submission_source = body.get("submission_source") or "FORM_COMPLETION"
    document_id = body.get("document_id")

    if submission_source not in ("FORM_COMPLETION", "DOCUMENT"):
        return error_response(
            400, "VALIDATION_ERROR",
            f"submission_source 必須為 FORM_COMPLETION 或 DOCUMENT，收到：{submission_source}",
        )
    if submission_source == "DOCUMENT" and not document_id:
        return error_response(400, "VALIDATION_ERROR", "submission_source=DOCUMENT 時，document_id 為必填", field_id="document_id")

    with timed_step(case_no, "review"):
        meta = case_store.get_case_meta(case_no)
        factors_record = case_store.get_record(case_no, "FACTORS")
        if meta is None or factors_record is None:
            return error_response(400, "VALIDATION_ERROR", "案件尚未完成資料收集，無法執行審查")

        form_completion = None
        if submission_source == "FORM_COMPLETION":
            form_completion = case_store.get_record(case_no, "FORM_COMPLETION")
            if form_completion is None:
                return error_response(400, "VALIDATION_ERROR", "案件尚未完成書表填寫，無法執行審查")

        try:
            case, regional_base, regional_comp = build_case_and_regional_factors(case_no, meta, factors_record)
        except Exception as e:
            return error_response(400, "VALIDATION_ERROR", f"案件資料格式錯誤：{e}")

        rule_engine = _rule_engine()

        if submission_source == "DOCUMENT":
            # DOCUMENT mode: submitted 表5-2 grades/codes/totals AND the 4
            # flat 表1 VALUE-role fields (使用分區/建蔽率/容積率/主要道路
            # 寬度) all come from the real uploaded PDF's own extraction --
            # never from collect_data's request-body-typed values, which
            # is what extract_submitted_land_use_ratio_fields()/
            # extract_submitted_main_road_width() would otherwise read.
            submitted, doc_error = _load_document_submission(case_no, document_id, case.comparable_ids)
            if doc_error is not None:
                return doc_error
        else:
            # build_submitted_from_form_completion() now populates BOTH
            # regional_total_{cid} (表5-2) and region_adjustment_rate_{cid}
            # (表4) directly from their own independent stored fields -- no
            # additional post-hoc duplication loop needed here (that loop,
            # which previously copied the single 表4 field's value into a
            # second key, has been removed as part of the Phase 8A
            # cross-form wiring fix).
            submitted = build_submitted_from_form_completion(
                case_no, form_completion, rule_engine=rule_engine, case=case, regional_base=regional_base,
            )

            # 表1 land-use-ratio fields: read from already-stored, already-
            # user-submitted data (case.base_parcel_factors) -- see
            # case_reconstruction.py. Never hardcoded; a case with no
            # plan_id genuinely gets None here, which LandUseRatioValidator
            # correctly reports as ZONING_PLAN_UNRESOLVED rather than
            # guessing.
            land_use = extract_submitted_land_use_ratio_fields(case)
            submitted.submitted_land_use_zone = land_use["land_use_zone"]
            submitted.submitted_building_coverage_rate = land_use["building_coverage_rate"]
            submitted.submitted_floor_area_ratio = land_use["floor_area_ratio"]

            # 表1 main_road_width: submitted value from collect_data's own
            # top-level request-body key -- never hardcoded here.
            submitted.submitted_main_road_width = extract_submitted_main_road_width(factors_record)

        # plan_id/confirmed_plan_name/plan_identification_source are
        # ALWAYS human-supplied (never extracted from a PDF, never typed
        # as an ordinary factor) -- identical for both submission sources,
        # read the same way regardless of path.
        plan_id_info = extract_plan_identification(factors_record)
        submitted.internal_plan_id = plan_id_info["internal_plan_id"]
        submitted.confirmed_plan_name = plan_id_info["confirmed_plan_name"]
        submitted.plan_identification_source = plan_id_info["plan_identification_source"]

        # road_width_evidence: independently-sourced reference evidence
        # (RealRoadProvider via collect_data.py), unrelated to submission
        # source -- reused unchanged for both paths (see
        # case_reconstruction.py's extract_road_width_evidence docstring).
        road_width_evidence = extract_road_width_evidence(factors_record)

        audit = AuditEngine(rule_engine, os.path.join(runtime_paths.data_dir(), "dependency_graph.json"))
        result = audit.review(case, submitted, regional_base, regional_comp,
                               road_width_evidence=road_width_evidence)

        result_json = json.loads(result.model_dump_json())
        result_json["submission_source"] = submission_source
        if document_id:
            result_json["document_id"] = document_id
        case_store.put_record(case_no, "REVIEW_RESULT", result_json)
        return response(200, result_json)
