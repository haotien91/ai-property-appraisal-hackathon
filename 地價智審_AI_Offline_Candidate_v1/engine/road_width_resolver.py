# -*- coding: utf-8 -*-
"""
RoadWidthResolver — decides whether a set of independently-sourced
RoadWidthEvidence entries for one road amounts to a single trustworthy
reference width, a genuine conflict, or nothing usable at all.

Critical distinction this module exists to enforce: 都市計畫道路寬度
(URBAN_PLAN_DESIGN_WIDTH -- the planned/regulatory width) is not the same
quantity as a road's現況/as-built width (OFFICIAL_ATTRIBUTE/
EXTERNAL_MAP_ATTRIBUTE/GEOMETRIC_ESTIMATE) -- a plan can call for a 20m
road that has not yet been widened past 18m. This module does NOT try to
silently reconcile that difference (average it, prefer one type by
assumption, etc.) -- ANY disagreement between two or more considered
RoadWidthEvidence entries produces CONFLICT/MANUAL_REVIEW_REQUIRED with
every entry preserved, regardless of which evidence_type each side is.
Priority order is used ONLY to decide which evidence becomes "the" cited
source when every available entry already agrees on one value -- never to
override a disagreement.

SUBMITTED_VALUE evidence is never a resolution candidate here -- what an
appraiser wrote on the report is compared against this module's output by
RoadWidthValidator, not folded into computing it (that would make the
"submitted vs official" check tautological).
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    RoadWidthEvidence, RoadWidthEvidenceType, RoadWidthResolutionResult, RoadWidthResolutionStatus,
)

# Lower index = higher priority = preferred citation when all available
# evidence agrees on one value.
_PRIORITY_ORDER = [
    RoadWidthEvidenceType.OFFICIAL_ATTRIBUTE,
    RoadWidthEvidenceType.URBAN_PLAN_DESIGN_WIDTH,
    RoadWidthEvidenceType.EXTERNAL_MAP_ATTRIBUTE,
    RoadWidthEvidenceType.GEOMETRIC_ESTIMATE,
]


def _priority_rank(evidence_type: RoadWidthEvidenceType) -> int:
    try:
        return _PRIORITY_ORDER.index(evidence_type)
    except ValueError:
        return len(_PRIORITY_ORDER)  # unranked type sorts last, never crashes


class RoadWidthResolver:
    def resolve(self, evidence: List[RoadWidthEvidence]) -> RoadWidthResolutionResult:
        candidates = [
            e for e in evidence
            if e.evidence_type != RoadWidthEvidenceType.SUBMITTED_VALUE and e.width_m is not None
        ]

        if not candidates:
            return RoadWidthResolutionResult(
                status=RoadWidthResolutionStatus.UNAVAILABLE, evidence=candidates,
                requires_manual_review=True,
                notes="無任何非申報來源之道路寬度Evidence可用，需人工確認",
            )

        distinct_values = {Decimal(str(e.width_m)) for e in candidates}
        if len(distinct_values) > 1:
            summary = "、".join(f"{e.evidence_type.value}={e.width_m}m" for e in candidates)
            return RoadWidthResolutionResult(
                status=RoadWidthResolutionStatus.CONFLICT, evidence=candidates,
                requires_manual_review=True,
                notes=f"多來源道路寬度數值不一致，未自動選定，需人工確認：{summary}",
            )

        best = min(candidates, key=lambda e: _priority_rank(e.evidence_type))
        return RoadWidthResolutionResult(
            status=RoadWidthResolutionStatus.RESOLVED, resolved_width_m=best.width_m,
            resolved_evidence=best, evidence=candidates,
            requires_manual_review=best.requires_manual_review,
            notes=f"採用{best.evidence_type.value}（{best.source_name or '來源未標示'}），"
            f"其餘{len(candidates) - 1}筆Evidence數值一致" if len(candidates) > 1
            else f"僅{best.evidence_type.value}一筆Evidence可用",
        )
