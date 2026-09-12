# -*- coding: utf-8 -*-
"""Tests for engine/form_classifier.py against this repo's own archived
Golden Case PDF (data/sources/competition/查估書表範本.pdf) -- not
synthetic text, the real 6-page document."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402
from engine.form_classifier import FormClassifier  # noqa: E402
from domain.models import FormType  # noqa: E402

GOLDEN_PDF = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")


@pytest.fixture(scope="module")
def golden_pages():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(GOLDEN_PDF)
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


class TestDeterministicMarkers:
    def test_synthetic_table1_title_classifies_confidently(self):
        text = "表1  地價區段勘查表\n新北市金山區\n使用分區(使用地類別)\n第二種商業區"
        result = FormClassifier().classify(text, page_number=1)
        assert result.form_type == FormType.TABLE1_LAND_SEGMENT_SURVEY
        assert result.confidence >= 0.6
        assert result.requires_manual_review is False
        assert any("title_match" in e for e in result.evidence)

    def test_synthetic_table5_2_title_classifies_confidently(self):
        text = "表5-2  影響地價區域因素分析明細表（商業用地）\n優劣等級\n修正百分比"
        result = FormClassifier().classify(text, page_number=1)
        assert result.form_type == FormType.TABLE5_2_REGIONAL_FACTOR_ANALYSIS
        assert result.requires_manual_review is False

    def test_synthetic_table4_title_classifies_confidently(self):
        text = "表4  比較法調查估價表\n土地正常單價\n試算價格\n比準地比較價格"
        result = FormClassifier().classify(text, page_number=1)
        assert result.form_type == FormType.TABLE4_COMPARISON_METHOD
        assert result.requires_manual_review is False

    def test_unrelated_text_is_uncertain_not_a_guess(self):
        result = FormClassifier().classify("這是一份完全無關的文件，內容與查估書表無關。", page_number=1)
        assert result.form_type == FormType.UNCERTAIN
        assert result.requires_manual_review is True

    def test_empty_text_is_uncertain(self):
        result = FormClassifier().classify("", page_number=1)
        assert result.form_type == FormType.UNCERTAIN
        assert result.requires_manual_review is True

    def test_ai_cannot_be_the_sole_basis_no_llm_call_here(self):
        """Deterministic-first per instruction: classify() must not import
        or call any LLM client -- verified by checking the module has no
        such dependency at all (an AI signal, if ever added, must be
        additive, never load-bearing on its own)."""
        import engine.form_classifier as mod
        assert "anthropic" not in dir(mod)
        assert "openai" not in dir(mod)
        assert not hasattr(mod, "call_llm")


class TestAgainstRealGoldenCasePdf:
    """The actual archived competition PDF -- not a synthetic string."""

    def test_page_1_is_table1(self, golden_pages):
        result = FormClassifier().classify(golden_pages[0], page_number=1)
        assert result.form_type == FormType.TABLE1_LAND_SEGMENT_SURVEY
        assert result.confidence >= 0.9

    def test_page_2_is_table5_2(self, golden_pages):
        result = FormClassifier().classify(golden_pages[1], page_number=2)
        assert result.form_type == FormType.TABLE5_2_REGIONAL_FACTOR_ANALYSIS
        assert result.confidence >= 0.9

    def test_page_3_is_table4(self, golden_pages):
        result = FormClassifier().classify(golden_pages[2], page_number=3)
        assert result.form_type == FormType.TABLE4_COMPARISON_METHOD
        assert result.confidence >= 0.9

    def test_map_diagram_pages_4_to_6_are_uncertain_not_misclassified(self, golden_pages):
        """Pages 4-6 are 地價區段略圖/使用分區圖/地價區段圖 (map diagrams,
        not one of the 3 supported forms) -- must never be forced into
        one of the 3 FormTypes just because SOME text overlaps."""
        for page_number in (4, 5, 6):
            result = FormClassifier().classify(golden_pages[page_number - 1], page_number=page_number)
            assert result.form_type == FormType.UNCERTAIN
            assert result.requires_manual_review is True

    def test_classify_document_returns_one_result_per_page_in_order(self, golden_pages):
        results = FormClassifier().classify_document(golden_pages)
        assert len(results) == 6
        assert [r.page_number for r in results] == [1, 2, 3, 4, 5, 6]
        assert [r.form_type for r in results[:3]] == [
            FormType.TABLE1_LAND_SEGMENT_SURVEY,
            FormType.TABLE5_2_REGIONAL_FACTOR_ANALYSIS,
            FormType.TABLE4_COMPARISON_METHOD,
        ]
