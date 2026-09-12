# -*- coding: utf-8 -*-
"""
CoordinateMapping — records WHERE each field appears in the generated PDF.

IMPORTANT DESIGN NOTE (documented per Phase 5 FALLBACK requirement: "需要
清楚記錄Automatic/Manual"): 查估書表範本.pdf is not a real, fillable PDF --
it is a ZIP-encapsulated image+text bundle (confirmed in Phase 1 and
re-confirmed in this phase via `check_fillable_fields.py`, which fails with
"invalid pdf header: PK"). There is therefore no official AcroForm field
grid or PDF-point coordinate system to map onto. This module's
"coordinates" are LOGICAL positions (page, section, row label) within our
own system-generated report layout, not pixel/point coordinates on the
original government form. This is the FALLBACK path explicitly permitted
by the Phase 5 instructions.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class FieldCoordinate(BaseModel):
    """Logical (not pixel/point) position of one field within the generated PDF."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    form: str                      # "表1" | "表5-2" | "表4"
    page_hint: int                 # 1-based logical page within that form's section
    section: str                   # human-readable section heading, e.g. "土地使用管制"
    row_label: str                 # the row/label this field renders under
    fill_mode: str                 # "AUTOMATIC" | "MANUAL"


def build_coordinate_mapping(form: str, section: str, row_label: str,
                              field_id: str, page_hint: int = 1,
                              fill_mode: str = "AUTOMATIC") -> FieldCoordinate:
    return FieldCoordinate(
        field_id=field_id, form=form, page_hint=page_hint,
        section=section, row_label=row_label, fill_mode=fill_mode,
    )
