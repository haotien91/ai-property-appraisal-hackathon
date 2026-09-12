# -*- coding: utf-8 -*-
"""
human_confirmation.py — the gate between "a document was extracted" and
"this value may enter deterministic rule judgment".

Division of responsibility this module exists to enforce (per this round's
instructions, item 6):
    OCR / text-layer extraction  = reads text and its position
    Rule Engine                  = judges grade (優/良/普通/差/劣)
    Adjustment Engine            = judges adjustment rate
    Calculation Engine           = recomputes numeric results
    AuditEngine                  = judges audit correctness
None of the above may ever be fed a value this module has not cleared.
A field whose extraction confidence is below `threshold` gets
requires_manual_review=True (already set by DocumentExtractionProvider)
and MUST have a HumanConfirmationRecord with a non-None confirmed_value
before confirmed_normalized_value() below will return anything for it --
there is no way to bypass this by construction, not just by convention.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import ExtractedField, HumanConfirmationRecord  # noqa: E402

DEFAULT_CONFIDENCE_THRESHOLD = 0.75

# (field_id, extraction_role, extraction_subject_role, comparable_slot) --
# field_id ALONE is no longer a unique identity once 表5-2 legitimately
# reuses one canonical field_id across BASE and every COMPARABLE slot (see
# ExtractedField's docstring in domain/models.py); extraction_role must
# ALSO be in the key, not just subject_role/slot, because 表5-2's BASE/
# GRADE_CODE and BASE/GRADE_TEXT ExtractedFields share the SAME
# (field_id, subject_role=BASE, slot=None) triple but are two genuinely
# different submitted values -- omitting extraction_role would silently
# collapse them onto one dict entry (found while wiring the Grade
# Representation Contract's Check A/B, the first consumers needing both
# reconstructed simultaneously). Every caller of resolve_confirmed_values()
# must key/match on this full 4-tuple, never on a subset of it.


def _identity(field: ExtractedField):
    return (field.field_id, field.extraction_role, field.extraction_subject_role, field.comparable_slot)


def flag_low_confidence(fields: List[ExtractedField],
                         threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> List[ExtractedField]:
    """Returns a new list where any field below `threshold` (configurable,
    per instruction) has requires_manual_review forced True -- even if the
    extraction provider itself already set it True for other reasons
    (never downgraded back to False here, only ever upgraded)."""
    result = []
    for f in fields:
        if f.confidence < threshold and not f.requires_manual_review:
            f = f.model_copy(update={"requires_manual_review": True})
        result.append(f)
    return result


def confirm_field(field: ExtractedField, confirmed_value: str, confirmed_by: str,
                   confirmed_at: Optional[datetime] = None) -> HumanConfirmationRecord:
    """Records a human's confirmation/correction of one extracted field.
    confirmed_value is NEVER auto-derived from extracted_value here -- a
    caller (a UI, a script) always supplies it explicitly, even when it
    happens to equal what was extracted (a human affirming "yes, this is
    right" is still a real confirmation event, not skipped)."""
    return HumanConfirmationRecord(
        field_id=field.field_id, extraction_role=field.extraction_role,
        extraction_subject_role=field.extraction_subject_role, comparable_slot=field.comparable_slot,
        extracted_value=field.normalized_value,
        confirmed_value=confirmed_value, confirmed_by=confirmed_by,
        confirmed_at=confirmed_at or datetime.now(),
    )


def resolve_confirmed_values(
    fields: List[ExtractedField], confirmations: Optional[List[HumanConfirmationRecord]] = None,
) -> Dict[tuple, "str | None"]:
    """The single seam every downstream consumer (extraction_to_submitted_
    form.py) must go through -- returns
    (field_id, extraction_subject_role, comparable_slot) -> the value
    actually usable for structured input:
      - not requires_manual_review: the extraction's own normalized_value
        (confidence was high enough to trust without a human)
      - requires_manual_review AND a matching confirmation exists with a
        non-None confirmed_value: that confirmed_value (a human looked at
        it)
      - requires_manual_review with no confirmation yet: None -- this
        field is NOT YET usable; it must not silently fall through to its
        raw (unconfirmed, low-confidence) extracted value.
    This is the one function name that answers "did OCR ever get to
    directly hand a value to a deterministic engine without human review"
    -- the answer must always be no for a flagged field.

    Keyed by the full (field_id, extraction_subject_role, comparable_slot)
    identity, not field_id alone -- 表5-2 legitimately extracts several
    ExtractedFields sharing one canonical field_id (BASE grade text,
    COMPARABLE-1 grade text, COMPARABLE-1 grade code, ...); field_id alone
    would silently collapse all of them onto one dict entry."""
    by_identity = {
        (c.field_id, c.extraction_role, c.extraction_subject_role, c.comparable_slot): c
        for c in (confirmations or [])
    }
    resolved: Dict[tuple, "str | None"] = {}
    for f in fields:
        identity = _identity(f)
        if not f.requires_manual_review:
            resolved[identity] = f.normalized_value
            continue
        confirmation = by_identity.get(identity)
        resolved[identity] = confirmation.confirmed_value if confirmation else None
    return resolved
