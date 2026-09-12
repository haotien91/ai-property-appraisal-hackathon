# -*- coding: utf-8 -*-
"""
competition_state.py — STEP5 §2 Competition Case State tracking.

Pure bookkeeping, persisted via case_store's EXISTING generic put_record/
get_record under SK="COMPETITION_STATE" (no new storage mechanism, no
schema migration). This module never computes a grade/adjustment/price/
verdict itself -- every field it writes is either supplied by a caller
(normally CompetitionOrchestrator, see competition_orchestrator.py) or
copied verbatim from another stage's own already-computed status string
(e.g. rule_resolution_status, CaseRulePackageStatus's own values). It is
strictly a SECOND, read-side view of "how far has this case gotten and is
anything blocking it" -- never a second source of truth for what any
stage actually decided.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

import case_store  # noqa: E402
from domain.models import CompetitionCaseState, CompetitionLifecycleStage  # noqa: E402

_SK = "COMPETITION_STATE"

# Lifecycle stages in their suggested forward order (STEP5 §2). Used only
# to detect an ILLEGAL backward move in advance() below -- MANUAL_REVIEW_
# REQUIRED and FAILED are terminal-ish "side exits" reachable from ANY
# stage (a blocking condition can occur at any point), not part of this
# forward sequence.
_FORWARD_ORDER = [
    CompetitionLifecycleStage.CREATED,
    CompetitionLifecycleStage.DOCUMENTS_UPLOADED,
    CompetitionLifecycleStage.APPRAISAL_EXTRACTED,
    CompetitionLifecycleStage.EVALUATION_STANDARD_EXTRACTED,
    CompetitionLifecycleStage.RULE_REVIEW_REQUIRED,
    CompetitionLifecycleStage.RULE_CONFIRMED,
    CompetitionLifecycleStage.DATA_COLLECTED,
    CompetitionLifecycleStage.ANALYZED,
    CompetitionLifecycleStage.FORMS_COMPLETED,
    CompetitionLifecycleStage.REVIEWED,
    CompetitionLifecycleStage.PDF_READY,
]
_SIDE_EXITS = {CompetitionLifecycleStage.MANUAL_REVIEW_REQUIRED, CompetitionLifecycleStage.FAILED}


class IllegalStateTransitionError(Exception):
    pass


def init_state(case_id: str) -> CompetitionCaseState:
    """Creates (or resets) a case's COMPETITION_STATE record at CREATED.
    Idempotent -- calling this on an already-tracked case simply restarts
    tracking; it never touches META/FACTORS/any other record."""
    state = CompetitionCaseState(case_id=case_id, updated_at=datetime.now(timezone.utc))
    _save(state)
    return state


def get_state(case_id: str) -> "CompetitionCaseState | None":
    record = case_store.get_record(case_id, _SK)
    if record is None:
        return None
    record = {k: v for k, v in record.items() if k != "_updated_at"}
    return CompetitionCaseState.model_validate(record)


def _save(state: CompetitionCaseState) -> None:
    case_store.put_record(state.case_id, _SK, state.model_dump(mode="json"))


def advance(
    case_id: str, new_stage: CompetitionLifecycleStage, **field_updates,
) -> CompetitionCaseState:
    """Moves a tracked case to `new_stage`, also applying any of this
    model's other fields passed as keyword arguments (e.g.
    appraisal_document_id=..., analyze_status=...). Refuses to move
    BACKWARD along the forward lifecycle (a real bug signal -- e.g. a
    caller accidentally re-running an earlier stage's state update after a
    later one already completed) UNLESS the case is already in a side-exit
    stage (MANUAL_REVIEW_REQUIRED/FAILED), which a caller may always leave
    by retrying forward, or the new stage IS itself a side exit (always
    allowed, from anywhere -- a blocking condition can surface at any
    point per §2)."""
    state = get_state(case_id)
    if state is None:
        state = init_state(case_id)

    if new_stage not in _SIDE_EXITS and state.overall_status not in _SIDE_EXITS:
        try:
            old_idx = _FORWARD_ORDER.index(state.overall_status)
            new_idx = _FORWARD_ORDER.index(new_stage)
        except ValueError:
            old_idx = new_idx = None
        if old_idx is not None and new_idx is not None and new_idx < old_idx:
            raise IllegalStateTransitionError(
                f"案件 {case_id} 狀態不得從 {state.overall_status.value} 倒退至 {new_stage.value}"
            )

    for field, value in field_updates.items():
        if not hasattr(state, field):
            raise IllegalStateTransitionError(f"CompetitionCaseState 沒有欄位 {field!r}")
        setattr(state, field, value)

    state.overall_status = new_stage
    state.manual_review_required = (
        new_stage == CompetitionLifecycleStage.MANUAL_REVIEW_REQUIRED or state.manual_review_required
    )
    state.updated_at = datetime.now(timezone.utc)
    _save(state)
    return state


def add_blocking_issue(case_id: str, issue: str) -> CompetitionCaseState:
    """Appends a blocking-issue description WITHOUT itself forcing a stage
    transition -- the caller (orchestrator) decides separately whether the
    issue is severe enough to move to MANUAL_REVIEW_REQUIRED/FAILED via
    advance(), keeping "what went wrong" and "what stage are we now in" as
    two independent decisions."""
    state = get_state(case_id)
    if state is None:
        state = init_state(case_id)
    if issue not in state.blocking_issues:
        state.blocking_issues.append(issue)
    state.updated_at = datetime.now(timezone.utc)
    _save(state)
    return state
