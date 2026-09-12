# -*- coding: utf-8 -*-
"""
FormClassifier — deterministic-first classification of one page's raw text
into one of this round's 3 supported form types (表1/表5-2/表4), or
UNCERTAIN.

"Deterministic-first" per instruction: classification is driven by fixed
title strings, table-number tokens, and distinctive field-label markers
that this codebase has already independently verified appear on the real
查估書表範本.pdf (see docs/backlog.md's Document Extraction survey) --
never a bare LLM guess. An AI/LLM MAY be layered on top in a future round
as an additional signal, but it must never be the sole basis for a
classification this module reports as confident; that boundary is why
FormClassifier lives in engine/ (deterministic-first, same footing as
RuleEngine/ZoneNameNormalizer) rather than being folded into an
LLM-calling module.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import FormType, FormClassificationResult  # noqa: E402

# Each form's markers, in DESCENDING specificity -- a title match is the
# strongest signal (near-zero false-positive rate: these exact strings are
# printed once, verbatim, at the top of their form), a table-number token
# alone is next (e.g. "表4" could theoretically appear in running prose,
# so it contributes less confidence on its own), and a distinctive field
# label is the weakest (used only to nudge confidence up when a title is
# for some reason not detected, e.g. a cropped scan).
_FORM_MARKERS = {
    FormType.TABLE1_LAND_SEGMENT_SURVEY: {
        "title": ["表1", "地價區段勘查表"],
        "table_number": ["表1"],
        "field_labels": ["使用分區(使用地類別)", "主要道路", "區段內道路平均寬度", "土地利用現況"],
    },
    FormType.TABLE5_2_REGIONAL_FACTOR_ANALYSIS: {
        "title": ["表5-2", "影響地價區域因素分析明細表"],
        "table_number": ["表5-2"],
        "field_labels": ["優劣等級", "修正百分比", "影響地價\n區域因素\n總修正數", "影響地價區域因素總修正數"],
    },
    FormType.TABLE4_COMPARISON_METHOD: {
        "title": ["表4", "比較法調查估價表"],
        "table_number": ["表4"],
        "field_labels": ["土地正常單價", "試算價格", "比準地比較價格", "價格形成因素之相近程度"],
    },
}

_TITLE_WEIGHT = 0.6
_TABLE_NUMBER_WEIGHT = 0.25
_FIELD_LABEL_WEIGHT = 0.05  # per matched label, capped below
_FIELD_LABEL_CAP = 0.15
_CONFIDENCE_THRESHOLD = 0.6  # below this, report UNCERTAIN rather than a low-confidence guess


class FormClassifier:
    def classify(self, page_text: str, page_number: int) -> FormClassificationResult:
        best_type: "FormType | None" = None
        best_score = 0.0
        best_evidence: list = []

        for form_type, markers in _FORM_MARKERS.items():
            score = 0.0
            evidence = []

            for title in markers["title"]:
                if title in page_text:
                    score += _TITLE_WEIGHT
                    evidence.append(f"title_match:{title}")
                    break  # one title hit is enough; don't double-count synonyms

            for token in markers["table_number"]:
                if token in page_text:
                    score += _TABLE_NUMBER_WEIGHT
                    evidence.append(f"table_number_match:{token}")
                    break

            label_hits = 0
            for label in markers["field_labels"]:
                if label in page_text:
                    label_hits += 1
                    evidence.append(f"field_label_match:{label}")
            score += min(label_hits * _FIELD_LABEL_WEIGHT, _FIELD_LABEL_CAP)

            score = min(score, 1.0)
            if score > best_score:
                best_score, best_type, best_evidence = score, form_type, evidence

        if best_type is None or best_score < _CONFIDENCE_THRESHOLD:
            return FormClassificationResult(
                page_number=page_number, form_type=FormType.UNCERTAIN,
                confidence=best_score, evidence=best_evidence or ["no_marker_matched"],
                requires_manual_review=True,
            )

        return FormClassificationResult(
            page_number=page_number, form_type=best_type, confidence=best_score,
            evidence=best_evidence, requires_manual_review=False,
        )

    def classify_document(self, pages: "list[str]") -> "list[FormClassificationResult]":
        return [self.classify(text, page_number=i + 1) for i, text in enumerate(pages)]
