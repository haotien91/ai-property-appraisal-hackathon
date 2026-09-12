# -*- coding: utf-8 -*-
"""Tests for providers/document_extraction_provider.py."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402
from providers.document_extraction_provider import (  # noqa: E402
    LocalExtractionProvider, FixtureExtractionProvider, TextractExtractionProvider,
    build_fixture_field,
)
from domain.models import (  # noqa: E402
    FormType, FormClassificationResult, ExtractionMethod, ExtractedField, BoundingBox,
    ExtractionStatus, ExtractionRole, ExtractionSubjectRole,
)

GOLDEN_PDF = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")


@pytest.fixture(scope="module")
def _require_fitz():
    pytest.importorskip("fitz")


class TestLocalExtractionProviderAgainstRealGoldenPdf:
    """Real PyMuPDF text-layer extraction against the actual archived PDF
    -- every assertion below is checked against the true Golden Case
    numbers (第二種商業區/70%/240%/中山路/18M/12M/184,763/212,958/100%),
    not fabricated expected values."""

    @pytest.fixture(scope="class")
    def extracted(self, _require_fitz):
        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        return {f.field_id: f for f in fields}

    def test_zoning_designation_matches_golden_case(self, extracted):
        f = extracted["individual_zoning_designation"]
        assert f.normalized_value == "第二種商業區"
        assert f.extraction_method == ExtractionMethod.TEXT_LAYER
        assert f.requires_manual_review is False
        assert f.bounding_box is not None
        assert f.page == 1

    def test_building_coverage_ratio_matches_golden_case(self, extracted):
        f = extracted["individual_building_coverage_ratio"]
        assert f.normalized_value == "70"
        assert f.unit == "%"

    def test_floor_area_ratio_matches_golden_case(self, extracted):
        f = extracted["individual_floor_area_ratio"]
        assert f.normalized_value == "240"
        assert f.unit == "%"

    def test_main_road_name_and_width_match_golden_case(self, extracted):
        assert extracted["main_road_name"].normalized_value == "中山路"
        width = extracted["main_road_width"]
        assert width.normalized_value == "18"
        assert width.unit == "M"

    def test_segment_avg_road_width_matches_golden_case(self, extracted):
        f = extracted["segment_avg_road_width"]
        assert f.normalized_value == "12"
        assert f.unit == "M"

    def test_table4_fields_match_golden_case(self, extracted):
        assert extracted["individual_land_normal_price"].normalized_value == "184,763"
        assert extracted["base_parcel_comparison_price"].normalized_value == "212,958"
        assert extracted["comparable_weight"].normalized_value == "100"
        assert extracted["comparable_weight"].unit == "%"

    def test_uncertain_pages_contribute_no_fields(self, _require_fitz):
        """Pages 4-6 (map diagrams) classify UNCERTAIN -- extract_fields()
        must not attempt (and fabricate) anything from them."""
        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        uncertain_pages = {c.page_number for c in classifications if c.form_type == FormType.UNCERTAIN}
        assert uncertain_pages == {4, 5, 6}
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        assert all(f.page not in uncertain_pages for f in fields)

    def test_all_extracted_fields_carry_full_provenance(self, extracted):
        for f in extracted.values():
            assert f.source_document == GOLDEN_PDF
            assert f.extraction_method == ExtractionMethod.TEXT_LAYER
            assert 0.0 <= f.confidence <= 1.0

    def test_ocr_never_outputs_a_grade_or_adjustment_verdict(self, extracted):
        """Locks in item 6: nothing this provider emits is itself a grade
        label (優/良/普通/差/劣) or an adjustment-rate judgment -- only raw
        text/normalized values."""
        forbidden = {"優", "良", "普通", "差", "劣", "估價錯誤"}
        for f in extracted.values():
            assert f.normalized_value not in forbidden
            assert f.raw_text not in forbidden


class TestTable52CanonicalLookup:
    """Validates the lookup building logic (Contract v3 blocker 5) against
    the REAL schemas/field_dictionary.json -- 28 regional factors, each
    with grade_base/grade_comparable_n/adjustment_pct roles, confirmed by
    direct inspection during this round's development."""

    def test_builds_exactly_28_factors_with_zero_exclusions(self):
        from providers.document_extraction_provider import build_table5_2_canonical_lookup
        lookup, excluded = build_table5_2_canonical_lookup()
        assert len(lookup) == 28
        assert excluded == []

    def test_known_real_factor_labels_resolve_to_known_field_ids(self):
        from providers.document_extraction_provider import build_table5_2_canonical_lookup
        lookup, _ = build_table5_2_canonical_lookup()
        assert lookup["使用分區(使用地類別)"] == "regional_land_use_zone"
        assert lookup["主要道路寬度"] == "regional_main_road_width"
        assert lookup["有無限制建築（整體開發、面積限制、高度限制……等）"] == "regional_construction_restricted"

    def test_role_set_mismatch_is_excluded_not_guessed(self):
        """Constructs a synthetic (not real-file) dictionary with a factor
        missing its adjustment_pct role -- must be excluded, never treated
        as if the missing role's marker were somehow present."""
        import tempfile
        import json as _json
        from providers.document_extraction_provider import build_table5_2_canonical_lookup

        data = {"fields": [
            {"field_id": "regional_x_grade_base", "chinese_label": "X因素－優劣等級(比準地)", "form": "表5-2"},
            {"field_id": "regional_x_grade_comparable_n", "chinese_label": "X因素－優劣等級(比較標的N)", "form": "表5-2"},
            # adjustment_pct role missing entirely
        ]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False)
            path = f.name
        lookup, excluded = build_table5_2_canonical_lookup(path)
        assert lookup == {}
        assert excluded and excluded[0][0] == "regional_x"
        assert "role_set_mismatch" in excluded[0][1]

    def test_label_prefix_collision_excludes_both_factors(self):
        """Two DIFFERENT base field_ids whose labels normalize to the SAME
        prefix must both be excluded -- never silently pick one (no fuzzy
        disambiguation)."""
        import tempfile
        import json as _json
        from providers.document_extraction_provider import build_table5_2_canonical_lookup

        def _triple(base, label):
            return [
                {"field_id": f"{base}_grade_base", "chinese_label": f"{label}－優劣等級(比準地)", "form": "表5-2"},
                {"field_id": f"{base}_grade_comparable_n", "chinese_label": f"{label}－優劣等級(比較標的N)", "form": "表5-2"},
                {"field_id": f"{base}_adjustment_pct", "chinese_label": f"{label}－修正百分比(比較標的N)", "form": "表5-2"},
            ]

        data = {"fields": _triple("regional_a", "同名因素") + _triple("regional_b", "同名因素")}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False)
            path = f.name
        lookup, excluded = build_table5_2_canonical_lookup(path)
        assert "同名因素" not in lookup
        excluded_bases = {e[0] for e in excluded}
        assert excluded_bases == {"regional_a", "regional_b"}


class TestTable52RowColumnExtraction:
    """Real-PDF regression: this session's measured bbox coordinates
    (documented in the earlier planning rounds) exist ONLY here as test
    assertions -- production code (_t5_2_column_bands et al.) derives
    everything from the page's own header word bboxes at runtime, per
    Contract v3 guardrail 5."""

    @pytest.fixture(scope="class")
    def t5_2_fields(self, _require_fitz):
        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        return fields

    def test_all_28_canonical_factors_produce_extracted_or_cell_blank_never_row_not_found(self, t5_2_fields):
        """The real Golden PDF's table header/labels ARE all locatable --
        a regression against row-detection false negatives."""
        statuses = {f.extraction_status for f in t5_2_fields
                    if f.page == 2 and f.extraction_role in (ExtractionRole.GRADE_TEXT, ExtractionRole.GRADE_CODE)}
        assert ExtractionStatus.ROW_NOT_FOUND not in statuses

    def test_no_unknown_factor_on_real_golden_pdf(self, t5_2_fields):
        assert not [f for f in t5_2_fields if f.extraction_status == ExtractionStatus.UNKNOWN_FACTOR]

    def test_multiline_wrapped_label_resolves_to_correct_canonical_factor(self, t5_2_fields):
        """regional_construction_restricted's real label wraps across 2
        physical PDF lines ("有無限制建築（整體開發、面" / "積限制、高度
        限制……等）") -- the greedy canonical-prefix merge must still
        resolve it, not split it into 2 unrelated blocks."""
        matches = [f for f in t5_2_fields if f.field_id == "regional_construction_restricted"
                   and f.extraction_subject_role == ExtractionSubjectRole.BASE]
        assert len(matches) == 2  # GRADE_CODE + GRADE_TEXT
        assert all(f.extraction_status == ExtractionStatus.EXTRACTED for f in matches)

    def test_base_grade_matches_real_golden_case_value(self, t5_2_fields):
        f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                 and x.extraction_subject_role == ExtractionSubjectRole.BASE
                 and x.extraction_role == ExtractionRole.GRADE_TEXT)
        assert f.raw_text == "普通"
        assert f.extraction_status == ExtractionStatus.EXTRACTED

    def test_base_grade_code_matches_real_golden_case_value(self, t5_2_fields):
        f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                 and x.extraction_subject_role == ExtractionSubjectRole.BASE
                 and x.extraction_role == ExtractionRole.GRADE_CODE)
        assert f.raw_text == "3"

    def test_comparable_1_grade_matches_real_golden_case_value(self, t5_2_fields):
        f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                 and x.extraction_subject_role == ExtractionSubjectRole.COMPARABLE and x.comparable_slot == 1
                 and x.extraction_role == ExtractionRole.GRADE_TEXT)
        assert f.raw_text == "普通"

    def test_comparable_1_adjustment_pct_matches_real_golden_case_value(self, t5_2_fields):
        f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                 and x.extraction_subject_role == ExtractionSubjectRole.COMPARABLE and x.comparable_slot == 1
                 and x.extraction_role == ExtractionRole.ADJUSTMENT_PCT)
        assert f.raw_text == "0.00"
        assert f.extraction_status == ExtractionStatus.EXTRACTED

    def test_comparable_2_and_3_are_cell_blank_not_row_not_found(self, t5_2_fields):
        """Golden Case only populates comparable 1 -- comparable 2/3's
        columns are genuinely blank cells on an EXISTING, located row, not
        a missing row (the exact distinction Contract v3 blocker 2 fixed)."""
        for slot in (2, 3):
            f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                     and x.extraction_subject_role == ExtractionSubjectRole.COMPARABLE
                     and x.comparable_slot == slot and x.extraction_role == ExtractionRole.GRADE_TEXT)
            assert f.extraction_status == ExtractionStatus.CELL_BLANK
            assert f.raw_text == ""

    def test_grade_code_role_field_id_matches_grade_text_role_field_id(self, t5_2_fields):
        """GRADE_CODE and GRADE_TEXT for the same factor/subject share ONE
        canonical field_id, disambiguated by extraction_role -- no
        `__grade_code_evidence`-style pseudo field_id (Contract v3 blocker 6,
        rejected in the prior round)."""
        text_f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                      and x.extraction_subject_role == ExtractionSubjectRole.BASE
                      and x.extraction_role == ExtractionRole.GRADE_TEXT)
        code_f = next(x for x in t5_2_fields if x.field_id == "regional_main_road_width"
                      and x.extraction_subject_role == ExtractionSubjectRole.BASE
                      and x.extraction_role == ExtractionRole.GRADE_CODE)
        assert text_f.field_id == code_f.field_id == "regional_main_road_width"

    def test_total_adjustment_matches_real_golden_case_value(self, t5_2_fields):
        f = next(x for x in t5_2_fields if x.field_id == "regional_total_adjustment")
        assert f.raw_text == "0.00"
        assert f.unit == "%"
        assert f.extraction_role == ExtractionRole.TOTAL
        assert f.extraction_status == ExtractionStatus.EXTRACTED

    def test_full_field_count_matches_28_factors_times_11_plus_total(self, t5_2_fields):
        """28 factors x (2 BASE fields + 3 comparable slots x 3 roles) + 1
        TOTAL field = 309 -- a coarse but real regression against
        structural drift (e.g. a header-detection change that silently
        drops a comparable slot)."""
        t5_2 = [f for f in t5_2_fields
                if f.page == 2 and (f.extraction_role != ExtractionRole.VALUE or f.field_id == "UNKNOWN_FACTOR")]
        assert len(t5_2) == 309


class TestExtractedFieldRequiredSchemaMigration:
    """Locks in Contract v3 blocker 1: extraction_status/extraction_role/
    extraction_subject_role/comparable_slot are an intentional REQUIRED
    schema migration, not a silently-defaulted additive change -- omitting
    any of them must raise, not fall back to a guessed default."""

    _BASE_KWARGS = dict(
        field_id="x", raw_text="70", normalized_value="70", unit=None, confidence=0.9,
        page=1, bounding_box=None, extraction_method=ExtractionMethod.MOCK_FIXTURE,
        source_document="FIXTURE", requires_manual_review=False,
    )

    def test_missing_extraction_status_raises(self):
        with pytest.raises(Exception):
            ExtractedField(
                **self._BASE_KWARGS, extraction_role=ExtractionRole.VALUE,
                extraction_subject_role=ExtractionSubjectRole.NONE, comparable_slot=None,
            )

    def test_missing_extraction_role_raises(self):
        with pytest.raises(Exception):
            ExtractedField(
                **self._BASE_KWARGS, extraction_status=ExtractionStatus.EXTRACTED,
                extraction_subject_role=ExtractionSubjectRole.NONE, comparable_slot=None,
            )

    def test_missing_extraction_subject_role_raises(self):
        with pytest.raises(Exception):
            ExtractedField(
                **self._BASE_KWARGS, extraction_status=ExtractionStatus.EXTRACTED,
                extraction_role=ExtractionRole.VALUE, comparable_slot=None,
            )

    def test_missing_comparable_slot_raises(self):
        with pytest.raises(Exception):
            ExtractedField(
                **self._BASE_KWARGS, extraction_status=ExtractionStatus.EXTRACTED,
                extraction_role=ExtractionRole.VALUE, extraction_subject_role=ExtractionSubjectRole.NONE,
            )

    def test_fully_specified_construction_succeeds(self):
        field = ExtractedField(
            **self._BASE_KWARGS, extraction_status=ExtractionStatus.EXTRACTED,
            extraction_role=ExtractionRole.VALUE, extraction_subject_role=ExtractionSubjectRole.NONE,
            comparable_slot=None,
        )
        assert field.extraction_status == ExtractionStatus.EXTRACTED

    def test_all_four_existing_production_constructor_sites_pass_new_fields(self, _require_fitz):
        """The literal migration checklist made executable -- grep for
        `ExtractedField(` in providers/document_extraction_provider.py
        found exactly 4 call sites (_field, _missing, build_fixture_field,
        TextractExtractionProvider._parse_textract_response); this test
        exercises all 4 and asserts each produces the CORRECT status/role,
        not just "doesn't crash"."""
        from providers.document_extraction_provider import (
            LocalExtractionProvider, TextractExtractionProvider, build_fixture_field,
        )

        # _field() -- success path (via a real Golden PDF extraction)
        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        far = next(f for f in fields if f.field_id == "individual_floor_area_ratio")
        assert far.extraction_status == ExtractionStatus.EXTRACTED
        assert far.extraction_role == ExtractionRole.VALUE
        assert far.extraction_subject_role == ExtractionSubjectRole.NONE
        assert far.comparable_slot is None

        # _missing() -- all 3 original failure reasons, via a spec whose
        # anchor cannot be found on a real page (anchor_not_found path)
        from providers.document_extraction_provider import _FieldSpec, LocalExtractionProvider as LEP
        missing_provider = LEP()
        missing_field = missing_provider._missing(
            _FieldSpec("nonexistent_field", "不存在", "不存在的錨點文字", "same_line"),
            page_number=1, source_document=GOLDEN_PDF, reason="anchor_not_found",
        )
        assert missing_field.extraction_status == ExtractionStatus.ROW_NOT_FOUND
        blank_field = missing_provider._missing(
            _FieldSpec("y", "y", "y", "same_line"), 1, GOLDEN_PDF, "no_value_word_on_same_line",
        )
        assert blank_field.extraction_status == ExtractionStatus.CELL_BLANK
        parse_failed_field = missing_provider._missing(
            _FieldSpec("z", "z", "z", "inline_regex"), 1, GOLDEN_PDF, "inline_pattern_not_matched",
        )
        assert parse_failed_field.extraction_status == ExtractionStatus.PARSE_FAILED

        # build_fixture_field() -- default path
        fixture_field = build_fixture_field("some_field", "70")
        assert fixture_field.extraction_status == ExtractionStatus.EXTRACTED
        assert fixture_field.extraction_role == ExtractionRole.VALUE
        assert fixture_field.extraction_subject_role == ExtractionSubjectRole.NONE
        assert fixture_field.comparable_slot is None

        # TextractExtractionProvider._parse_textract_response() -- synthetic response
        response = {
            "Blocks": [
                {"Id": "key1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"], "Confidence": 98.0,
                 "Relationships": [{"Type": "CHILD", "Ids": ["word1"]}, {"Type": "VALUE", "Ids": ["val1"]}]},
                {"Id": "word1", "BlockType": "WORD", "Text": "建蔽率"},
                {"Id": "val1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"], "Confidence": 95.0,
                 "Relationships": [{"Type": "CHILD", "Ids": ["word2"]}]},
                {"Id": "word2", "BlockType": "WORD", "Text": "70%"},
            ]
        }
        textract_fields = TextractExtractionProvider._parse_textract_response(response, "test.pdf", page=1)
        assert textract_fields[0].extraction_status == ExtractionStatus.EXTRACTED
        assert textract_fields[0].extraction_role == ExtractionRole.VALUE
        assert textract_fields[0].extraction_subject_role == ExtractionSubjectRole.NONE
        assert textract_fields[0].comparable_slot is None


class TestFixtureExtractionProvider:
    def test_returns_exactly_the_constructed_fixture_regardless_of_path(self):
        classification = FormClassificationResult(
            page_number=1, form_type=FormType.TABLE1_LAND_SEGMENT_SURVEY,
            confidence=1.0, evidence=["fixture"], requires_manual_review=False,
        )
        field = build_fixture_field("individual_floor_area_ratio", "300%", "300", unit="%")
        provider = FixtureExtractionProvider(classification, [field])

        classifications = provider.classify("this/path/is/ignored.pdf")
        assert classifications == [classification]
        fields = provider.extract_fields("also/ignored.pdf", classifications)
        assert len(fields) == 1
        assert fields[0].extraction_method == ExtractionMethod.MOCK_FIXTURE
        assert fields[0].normalized_value == "300"

    def test_build_fixture_field_normalizes_by_default(self):
        f = build_fixture_field("x", "70%", unit="%")
        assert f.normalized_value == "70"
        assert f.extraction_method == ExtractionMethod.MOCK_FIXTURE


class TestTextractExtractionProviderCodeReadyNotVerified:
    """CODE_READY != AWS_RUNTIME_VERIFIED -- classify()/extract_fields()
    must refuse to silently pretend to work without a real, verified AWS
    connection; only the pure response-parsing logic is independently
    testable offline."""

    def test_classify_raises_not_silently_returns_empty(self):
        provider = TextractExtractionProvider()
        with pytest.raises(NotImplementedError, match="AWS_RUNTIME_VERIFIED"):
            provider.classify("some.pdf")

    def test_extract_fields_raises_not_silently_returns_empty(self):
        provider = TextractExtractionProvider()
        with pytest.raises(NotImplementedError, match="AWS_RUNTIME_VERIFIED"):
            provider.extract_fields("some.pdf", [])

    def test_response_parser_is_independently_testable_offline(self):
        """A synthetic Textract-shaped response (this codebase's own
        construction, not a real API call) -- proves the parsing LOGIC is
        real and correct, independent of whether AWS itself has ever been
        reached."""
        response = {
            "Blocks": [
                {"Id": "key1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"],
                 "Confidence": 98.0,
                 "Relationships": [
                     {"Type": "CHILD", "Ids": ["word1"]},
                     {"Type": "VALUE", "Ids": ["val1"]},
                 ]},
                {"Id": "word1", "BlockType": "WORD", "Text": "建蔽率"},
                {"Id": "val1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"],
                 "Confidence": 95.0,
                 "Relationships": [{"Type": "CHILD", "Ids": ["word2"]}],
                 "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.05, "Height": 0.02}}},
                {"Id": "word2", "BlockType": "WORD", "Text": "70%"},
            ]
        }
        fields = TextractExtractionProvider._parse_textract_response(response, "test.pdf", page=1)
        assert len(fields) == 1
        f = fields[0]
        assert f.field_id == "建蔽率"
        assert f.raw_text == "70%"
        assert f.normalized_value == "70"
        assert f.extraction_method == ExtractionMethod.AWS_TEXTRACT
        assert f.confidence == pytest.approx(0.95)
        assert f.bounding_box is not None
        assert f.requires_manual_review is False

    def test_response_parser_flags_low_confidence_for_manual_review(self):
        response = {
            "Blocks": [
                {"Id": "key1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"],
                 "Confidence": 40.0,
                 "Relationships": [{"Type": "CHILD", "Ids": ["word1"]}, {"Type": "VALUE", "Ids": ["val1"]}]},
                {"Id": "word1", "BlockType": "WORD", "Text": "容積率"},
                {"Id": "val1", "BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"], "Confidence": 40.0,
                 "Relationships": [{"Type": "CHILD", "Ids": ["word2"]}]},
                {"Id": "word2", "BlockType": "WORD", "Text": "??%"},
            ]
        }
        fields = TextractExtractionProvider._parse_textract_response(response, "test.pdf")
        assert fields[0].requires_manual_review is True
