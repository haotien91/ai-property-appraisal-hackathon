# -*- coding: utf-8 -*-
"""
PdfRenderer — Structured Result -> Completed Form -> PDF.

Uses weasyprint (HTML/CSS -> PDF via the system's native font rendering),
NOT reportlab, because reportlab's built-in CID font support for Traditional
Chinese (MSung-Light) was tested and found to silently produce a BLANK page
(no visible glyphs) in this environment -- see docs/phase5/pdf_output_spec.md
for the reproduction. weasyprint correctly renders and produces
text-extractable Traditional Chinese via the system's installed Noto Sans
CJK TC font, independently verified before this module was written.
"""
from __future__ import annotations
import sys
import os
from datetime import datetime
from collections import OrderedDict
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import FormCompletionResult, FieldCompletion  # noqa: E402
from pdf.form_field_mapping import build_field_mappings  # noqa: E402
from pdf.pdf_template import render_form_html  # noqa: E402

import weasyprint  # noqa: E402


class PdfRenderer:
    def render_form(self, result: FormCompletionResult, form_title: str,
                     form_key: str, case_no: str, segment_code: str) -> bytes:
        """form_key: which logical form these fields belong to ('表1' | '表5-2' | '表4' |
        combined). Returns raw PDF bytes."""
        mapped = build_field_mappings(result.fields, form_key)

        sections: "OrderedDict[str, list]" = OrderedDict()
        for field, coord in mapped:
            sections.setdefault(coord.section, []).append({
                "label": field.chinese_label,
                "status": field.status.value,
                "value": _stringify(field.final_value),
                "rule_id": field.rule_id,
                "grade": field.grade,
                "formula": field.formula,
                "calculation": field.calculation,
                "source": field.source,
                "warnings": field.warnings,
                "fill_mode": coord.fill_mode,
            })

        engine_versions = ", ".join(f"{k}={v}" for k, v in (result.engine_versions or {}).items())
        html = render_form_html(
            form_title=form_title, case_no=case_no, segment_code=segment_code,
            generated_at=result.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            sections=sections, engine_versions=engine_versions or "N/A",
        )
        return weasyprint.HTML(string=html).write_pdf()

    def render_all_forms(self, result: FormCompletionResult, case_no: str,
                          segment_code: str, output_path: str) -> str:
        """Renders 表4/表5-2 fields (currently combined in result.fields per
        Phase 4's FormCompletionEngine.complete_form output) as one combined
        multi-section PDF, then merges with pypdf into a single output file.
        表1 (raw survey data, not yet modeled as FieldCompletion in Phase 4)
        is rendered from the raw base_regional/individual factor inputs
        separately -- see build_table1_pdf_bytes below."""
        pdf_bytes = self.render_form(
            result, form_title="表4 比較法調查估價表 ＋ 表5-2 影響地價區域因素分析明細表（系統整合輸出）",
            form_key="表4", case_no=case_no, segment_code=segment_code,
        )
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)
        return output_path


def _stringify(value) -> str:
    if value is None:
        return "N/A"
    return str(value)


def build_table1_pdf_bytes(case_no: str, segment_code: str, segment_scope: str,
                            base_regional_factors: List, generated_at: str) -> bytes:
    """Renders 表1 地價區段勘查表 from the raw regional FactorInput list
    (base parcel's segment-level survey data), since Phase 4's
    FormCompletionEngine does not currently emit FieldCompletion records for
    表1 itself (only for the 表5-2/表4 derived Grade/Adjustment/Calculation
    outputs). This keeps 表1's presentation honest: these are raw collected
    values (AI輔助填寫/使用者輸入), not Rule/Calculation Engine outputs, so
    every row is classified AUTOMATIC=False in spirit -- but since 表1 has
    no "manual review" concept of its own (it's all Data Acquisition Layer
    output), we label them by their actual source_type instead."""
    sections: "OrderedDict[str, list]" = OrderedDict()
    for fi in base_regional_factors:
        section = "區段資料"
        sections.setdefault(section, []).append({
            "label": fi.factor, "status": "COMPLETED" if fi.raw_value is not None else "UNKNOWN",
            "value": _stringify(fi.raw_value) + (f" {fi.unit}" if fi.unit else ""),
            "rule_id": None, "grade": None, "formula": None, "calculation": None,
            "source": fi.evidence.source, "warnings": [],
            "fill_mode": "AUTOMATIC" if fi.evidence.source_type.value == "AI輔助填寫" else "MANUAL",
        })
    html = render_form_html(
        form_title=f"表1 地價區段勘查表（區段範圍：{segment_scope}）",
        case_no=case_no, segment_code=segment_code, generated_at=generated_at,
        sections=sections, engine_versions="Data Acquisition Layer v1.0",
    )
    return weasyprint.HTML(string=html).write_pdf()
