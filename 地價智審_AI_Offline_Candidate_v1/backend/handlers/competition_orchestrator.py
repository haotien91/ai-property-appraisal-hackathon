# -*- coding: utf-8 -*-
"""
competition_orchestrator.py — STEP5 §5 Competition E2E Orchestrator.

PURE ORCHESTRATION ONLY: every method below calls an EXISTING handler
function (document_extract.extract_document, evaluation_standard.*,
collect_data.collect_data, analyze.analyze, complete_form.complete_form,
review.review, pdf_handler.get_pdf) with a constructed API-Gateway-shaped
event dict, exactly as API Gateway itself would invoke it, and interprets
that handler's own response. It NEVER recomputes a grade, adjustment,
trial price, or form total itself -- if a caller needs a different
number, that means an upstream handler is wrong, not that this file should
patch the difference. This is deliberately NOT a second Grade/Adjustment/
Calculation Engine (STEP5 §0 Core Freeze ban list); it is a sequencing
+ CompetitionCaseState bookkeeping layer only.

Step Functions scaffold check (STEP5 §29): infra/template.yaml has NO
AWS::Serverless::StateMachine resource and no .asl.json state-machine
definition anywhere in this repo (confirmed by grep across infra/ before
writing this file) -- there is nothing to reuse or extend, and building a
brand-new state machine this round would be exactly the "強行重寫
orchestration infra" §29 says not to do when it can't actually be
deployed/tested end-to-end in this environment. This module is the
"shared service/orchestrator + handlers" alternative §29 explicitly
sanctions; STEP_FUNCTIONS_PRODUCTION_READY=NO is reported honestly in
docs/audit/COMPETITION_E2E_PHASE5_REPORT.md.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

import document_extract  # noqa: E402
import evaluation_standard  # noqa: E402
import collect_data  # noqa: E402
import analyze  # noqa: E402
import complete_form  # noqa: E402
import review  # noqa: E402
import competition_state  # noqa: E402
from domain.models import CompetitionLifecycleStage  # noqa: E402


class CompetitionStepFailedError(Exception):
    """Raised by run_full_pipeline() when a step returns a non-2xx
    response, carrying enough of that response for the caller to inspect
    without re-parsing JSON. The pipeline is left at whatever
    CompetitionLifecycleStage.advance() call already recorded (MANUAL_
    REVIEW_REQUIRED or FAILED, per _stage_for_failure below) -- this
    exception is only how run_full_pipeline() stops early and reports
    which step failed and why."""

    def __init__(self, step: str, status_code: int, body: dict):
        self.step = step
        self.status_code = status_code
        self.body = body
        super().__init__(f"Competition pipeline step {step!r} failed ({status_code}): {body}")


def _event(case_no: str, document_id: str = None, package_id: str = None, body: dict = None) -> dict:
    path_params = {"id": case_no}
    if document_id is not None:
        path_params["document_id"] = document_id
    if package_id is not None:
        path_params["package_id"] = package_id
    return {"pathParameters": path_params, "body": json.dumps(body or {}, ensure_ascii=False)}


def _stage_for_failure(status_code: int) -> CompetitionLifecycleStage:
    """409 (CASE_RULE_INVALID / already-confirmed / not-editable / validation-
    failed-on-confirm) and the RULE_REVIEW_REQUIRED-style 4xx business
    states are all "a human needs to look at this", never silently
    treated as a hard crash; anything else (5xx, unexpected) is FAILED."""
    if 400 <= status_code < 500:
        return CompetitionLifecycleStage.MANUAL_REVIEW_REQUIRED
    return CompetitionLifecycleStage.FAILED


class CompetitionOrchestrator:
    def __init__(self, case_no: str):
        self.case_no = case_no
        if competition_state.get_state(case_no) is None:
            competition_state.init_state(case_no)

    def _fail(self, step: str, resp: dict) -> None:
        body = json.loads(resp["body"])
        competition_state.add_blocking_issue(
            self.case_no, f"{step}: {body.get('error', {}).get('code', 'UNKNOWN')}",
        )
        competition_state.advance(self.case_no, _stage_for_failure(resp["statusCode"]))
        raise CompetitionStepFailedError(step, resp["statusCode"], body)

    # ------------------------------------------------------------------
    # Individual steps (each callable independently -- a test or a caller
    # that already has some stages done may call only what it still needs)
    # ------------------------------------------------------------------
    def extract_appraisal_document(self, document_id: str) -> dict:
        resp = document_extract.extract_document(_event(self.case_no, document_id=document_id), None)
        if resp["statusCode"] >= 300:
            self._fail("extract_appraisal_document", resp)
        body = json.loads(resp["body"])
        competition_state.advance(
            self.case_no, CompetitionLifecycleStage.APPRAISAL_EXTRACTED,
            appraisal_document_id=document_id, appraisal_extraction_status=body.get("status", "EXTRACTED"),
        )
        return body

    def import_evaluation_standard(self, document_id: str, package_id: str = None) -> dict:
        resp = evaluation_standard.extract_evaluation_standard(
            _event(self.case_no, document_id=document_id, body={"package_id": package_id} if package_id else None),
            None,
        )
        if resp["statusCode"] >= 300:
            self._fail("import_evaluation_standard", resp)
        dto = json.loads(resp["body"])
        next_stage = (
            CompetitionLifecycleStage.RULE_CONFIRMED if dto.get("status") == "CONFIRMED"
            else CompetitionLifecycleStage.RULE_REVIEW_REQUIRED
        )
        competition_state.advance(
            self.case_no, next_stage,
            evaluation_standard_document_id=document_id, evaluation_standard_package_id=dto["package_id"],
            evaluation_standard_status=dto.get("status"),
        )
        return dto

    def confirm_evaluation_standard(self, package_id: str, confirmed_by: str = "competition_orchestrator") -> dict:
        """The Human Confirmation Gate itself (STEP5 §3): only a genuine
        CONFIRMED response advances case_rule_status past RULE_REVIEW_
        REQUIRED -- an invalid/rejected confirmation attempt goes through
        the SAME _fail() path as any other step, never silently ignored."""
        resp = evaluation_standard.confirm_evaluation_standard(
            _event(self.case_no, package_id=package_id, body={"confirmed_by": confirmed_by}), None,
        )
        if resp["statusCode"] >= 300:
            self._fail("confirm_evaluation_standard", resp)
        dto = json.loads(resp["body"])
        competition_state.advance(
            self.case_no, CompetitionLifecycleStage.RULE_CONFIRMED,
            evaluation_standard_package_id=package_id, evaluation_standard_status=dto.get("status"),
            case_rule_status="CONFIRMED",
        )
        return dto

    def collect_data(self, request_body: dict) -> dict:
        resp = collect_data.collect_data(_event(self.case_no, body=request_body), None)
        if resp["statusCode"] >= 300:
            self._fail("collect_data", resp)
        body = json.loads(resp["body"])
        competition_state.advance(
            self.case_no, CompetitionLifecycleStage.DATA_COLLECTED, collect_data_status="COLLECTED",
        )
        return body

    def analyze(self) -> dict:
        resp = analyze.analyze(_event(self.case_no), None)
        if resp["statusCode"] >= 300:
            self._fail("analyze", resp)
        body = json.loads(resp["body"])
        competition_state.advance(
            self.case_no, CompetitionLifecycleStage.ANALYZED,
            analyze_status=body.get("rule_resolution_status", "ANALYZED"),
        )
        return body

    def complete_form(self) -> dict:
        resp = complete_form.complete_form(_event(self.case_no), None)
        if resp["statusCode"] >= 300:
            self._fail("complete_form", resp)
        body = json.loads(resp["body"])
        competition_state.advance(
            self.case_no, CompetitionLifecycleStage.FORMS_COMPLETED, complete_form_status="COMPLETED",
        )
        return body

    def review(self, submission_source: str = "FORM_COMPLETION", document_id: str = None) -> dict:
        body_in = {"submission_source": submission_source}
        if document_id is not None:
            body_in["document_id"] = document_id
        resp = review.review(_event(self.case_no, body=body_in), None)
        if resp["statusCode"] >= 300:
            self._fail("review", resp)
        body = json.loads(resp["body"])
        manual_review = any(
            i.get("issue_type") in ("Error", "Inconsistent") for i in body.get("issues", [])
        )
        competition_state.advance(
            self.case_no,
            CompetitionLifecycleStage.MANUAL_REVIEW_REQUIRED if manual_review else CompetitionLifecycleStage.REVIEWED,
            review_status="REVIEWED",
        )
        return body

    def generate_pdf(self, generation_id=None, case_id=None, group_id=None) -> dict:
        import competition_segments
        if competition_segments.get_segment_map(self.case_no) is None:
            # Preserve the legacy Golden Case renderer; the current delivery schema is segment-scoped.
            import pdf_handler
            resp=pdf_handler.get_pdf(_event(self.case_no),None)
            if resp['statusCode'] >= 300: self._fail('generate_pdf',resp)
            competition_state.advance(self.case_no, CompetitionLifecycleStage.PDF_READY, pdf_status="READY")
            return json.loads(resp['body'])
        from generate_artifacts import generate_and_publish
        import uuid
        if generation_id is None:
            if not hasattr(self, '_generation_id'): self._generation_id=str(uuid.uuid4())
            generation_id=self._generation_id
        result=generate_and_publish(self.case_no,generation_id,case_id=case_id,group_id=group_id)
        competition_state.advance(self.case_no, CompetitionLifecycleStage.PDF_READY, pdf_status="READY")
        return {'generation_id':generation_id,'artifacts':result}

    # ------------------------------------------------------------------
    # Full pipeline convenience (STEP5 §5's named sequence). Every step is
    # OPTIONAL via its *_document_id/*_package_id argument being None --
    # a caller that already ran document upload/extraction/confirmation
    # itself (e.g. to test the Human Confirmation Gate in isolation) may
    # start this from collect_data onward.
    # ------------------------------------------------------------------
    def run_full_pipeline(
        self, collect_data_body: dict,
        appraisal_document_id: str = None,
        evaluation_standard_document_id: str = None, evaluation_standard_package_id: str = None,
        confirm_rule: bool = False, confirmed_by: str = "competition_orchestrator",
        run_review: bool = True, run_pdf: bool = True,
        generation_id: str = None, artifact_case_id: str = None, artifact_group_id: str = None,
    ) -> dict:
        trace = {}
        if appraisal_document_id is not None:
            trace["appraisal_extraction"] = self.extract_appraisal_document(appraisal_document_id)
        if evaluation_standard_document_id is not None:
            dto = self.import_evaluation_standard(
                evaluation_standard_document_id, package_id=evaluation_standard_package_id,
            )
            trace["evaluation_standard_import"] = dto
            if confirm_rule:
                trace["evaluation_standard_confirmation"] = self.confirm_evaluation_standard(
                    dto["package_id"], confirmed_by=confirmed_by,
                )

        trace["collect_data"] = self.collect_data(collect_data_body)
        trace["analyze"] = self.analyze()
        trace["complete_form"] = self.complete_form()
        if run_review:
            trace["review"] = self.review()
        if run_pdf:
            trace["pdf"] = self.generate_pdf(generation_id, artifact_case_id, artifact_group_id)
        return trace
