# -*- coding: utf-8 -*-
"""
export/models.py — SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1 Task 1.

CaseExportBundle: the ONE unified export model PDF/JSON/Excel all read
from -- never a second independent data path per output format. `table5_1`
/ `table4` are the EXACT `Table51Analysis.model_dump(mode="json")` /
`Table4Analysis.model_dump(mode="json")` dicts (C1/D1's own established
schemas, unchanged) -- this module never re-shapes or recomputes them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class ManualReviewItem(BaseModel):
    """One item requiring human attention before the case can be
    finalized -- derived DIRECTLY from Table51Analysis/Table4Analysis's
    own `requires_manual_review`/`reason` fields (or a structural policy
    like FAR/weight), never from the legacy review()/REVIEW_RESULT record
    (see bundle_builder.py's module docstring for why)."""
    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="e.g. 'table51_factor' / 'table4_factor' / 'table4_far' / 'table4_weight'")
    segment_code: Optional[str] = None
    field_id: Optional[str] = None
    factor_name: Optional[str] = None
    reason: str


class CaseExportBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    generated_at: datetime

    case_no: str
    profile_id: Optional[str] = None
    appraisal_base_date: Optional[str] = None

    base_segment_code: str
    comparable_segment_codes: List[str]

    case: Dict[str, Any]
    segments: Dict[str, Dict[str, Any]]
    table3: Dict[str, Dict[str, Any]]
    table5_1: Dict[str, Any]
    table4: Dict[str, Any]

    review: Optional[Dict[str, Any]] = None
    review_status: str = "NOT_AVAILABLE_FOR_SHULIN_YET"

    manual_review_items: List[ManualReviewItem] = Field(default_factory=list)

    provenance: Dict[str, Any] = Field(default_factory=dict)
