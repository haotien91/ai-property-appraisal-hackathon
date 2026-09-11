# -*- coding: utf-8 -*-
"""Tests for engine/human_confirmation.py -- the gate ensuring a low-
confidence extracted value can never reach deterministic rule judgment
without a human having actually confirmed it."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from engine.human_confirmation import (  # noqa: E402
    flag_low_confidence, confirm_field, resolve_confirmed_values, DEFAULT_CONFIDENCE_THRESHOLD,
)
from providers.document_extraction_provider import build_fixture_field  # noqa: E402
from domain.models import (  # noqa: E402
    ExtractionStatus, ExtractionRole, ExtractionSubjectRole,
)

NONE_IDENTITY = ExtractionSubjectRole.NONE  # 表1/表4-style single-value fields: no base/comparable split


class TestFlagLowConfidence:
    def test_below_threshold_is_flagged(self):
        field = build_fixture_field("x", "70", confidence=0.5, requires_manual_review=False)
        flagged = flag_low_confidence([field], threshold=0.75)
        assert flagged[0].requires_manual_review is True

    def test_at_or_above_threshold_is_not_flagged(self):
        field = build_fixture_field("x", "70", confidence=0.9, requires_manual_review=False)
        flagged = flag_low_confidence([field], threshold=0.75)
        assert flagged[0].requires_manual_review is False

    def test_already_flagged_stays_flagged_never_downgraded(self):
        field = build_fixture_field("x", "70", confidence=0.99, requires_manual_review=True)
        flagged = flag_low_confidence([field], threshold=0.5)
        assert flagged[0].requires_manual_review is True

    def test_default_threshold_is_075(self):
        assert DEFAULT_CONFIDENCE_THRESHOLD == 0.75


class TestConfirmField:
    def test_records_extracted_and_confirmed_values_separately(self):
        field = build_fixture_field("individual_floor_area_ratio", "300", confidence=0.4,
                                     requires_manual_review=True)
        record = confirm_field(field, confirmed_value="240", confirmed_by="appraiser_a")
        assert record.extracted_value == "300"
        assert record.confirmed_value == "240"
        assert record.confirmed_by == "appraiser_a"
        assert record.confirmed_at is not None

    def test_human_affirming_the_extracted_value_is_still_a_real_confirmation(self):
        field = build_fixture_field("x", "70", confidence=0.5, requires_manual_review=True)
        record = confirm_field(field, confirmed_value="70", confirmed_by="appraiser_b")
        assert record.confirmed_value == "70"
        assert record.confirmed_by == "appraiser_b"


class TestResolveConfirmedValues:
    def test_high_confidence_field_uses_its_own_normalized_value(self):
        field = build_fixture_field("individual_building_coverage_ratio", "70", confidence=0.95,
                                     requires_manual_review=False)
        resolved = resolve_confirmed_values([field])
        assert resolved[("individual_building_coverage_ratio", ExtractionRole.VALUE, NONE_IDENTITY, None)] == "70"

    def test_low_confidence_field_without_confirmation_resolves_to_none(self):
        """The critical guarantee: OCR alone, no matter how it's called,
        can never hand a flagged value to a deterministic engine."""
        field = build_fixture_field("individual_floor_area_ratio", "300", confidence=0.3,
                                     requires_manual_review=True)
        resolved = resolve_confirmed_values([field], confirmations=None)
        assert resolved[("individual_floor_area_ratio", ExtractionRole.VALUE, NONE_IDENTITY, None)] is None

    def test_low_confidence_field_with_confirmation_uses_confirmed_value(self):
        field = build_fixture_field("individual_floor_area_ratio", "3O0", confidence=0.3,
                                     requires_manual_review=True)
        record = confirm_field(field, confirmed_value="300", confirmed_by="appraiser_c")
        resolved = resolve_confirmed_values([field], confirmations=[record])
        assert resolved[("individual_floor_area_ratio", ExtractionRole.VALUE, NONE_IDENTITY, None)] == "300"

    def test_confirmation_for_a_different_field_id_does_not_leak_across(self):
        field_a = build_fixture_field("field_a", "1", confidence=0.2, requires_manual_review=True)
        field_b = build_fixture_field("field_b", "2", confidence=0.2, requires_manual_review=True)
        record = confirm_field(field_a, confirmed_value="1-confirmed", confirmed_by="x")
        resolved = resolve_confirmed_values([field_a, field_b], confirmations=[record])
        assert resolved[("field_a", ExtractionRole.VALUE, NONE_IDENTITY, None)] == "1-confirmed"
        assert resolved[("field_b", ExtractionRole.VALUE, NONE_IDENTITY, None)] is None

    def test_base_and_comparable_slot_confirmations_do_not_collide(self):
        """The regression test proving the composite-key migration actually
        fixes the collision found while implementing Contract v3: two
        ExtractedFields sharing ONE canonical field_id (as 表5-2's BASE and
        COMPARABLE-1 grade text legitimately do) must resolve to
        INDEPENDENT confirmed values, never overwrite one another."""
        base_field = build_fixture_field(
            "regional_land_use_zone", "1 優", "優", confidence=0.3, requires_manual_review=True,
            extraction_role=ExtractionRole.GRADE_TEXT, extraction_subject_role=ExtractionSubjectRole.BASE,
            comparable_slot=None,
        )
        comp1_field = build_fixture_field(
            "regional_land_use_zone", "3 普通", "普通", confidence=0.3, requires_manual_review=True,
            extraction_role=ExtractionRole.GRADE_TEXT, extraction_subject_role=ExtractionSubjectRole.COMPARABLE,
            comparable_slot=1,
        )
        base_record = confirm_field(base_field, confirmed_value="優-confirmed", confirmed_by="appraiser_x")
        comp1_record = confirm_field(comp1_field, confirmed_value="普通-confirmed", confirmed_by="appraiser_x")

        resolved = resolve_confirmed_values([base_field, comp1_field], confirmations=[base_record, comp1_record])
        assert resolved[("regional_land_use_zone", ExtractionRole.GRADE_TEXT,
                          ExtractionSubjectRole.BASE, None)] == "優-confirmed"
        assert resolved[("regional_land_use_zone", ExtractionRole.GRADE_TEXT,
                          ExtractionSubjectRole.COMPARABLE, 1)] == "普通-confirmed"

    def test_confirming_base_only_does_not_leak_into_comparable_slot(self):
        base_field = build_fixture_field(
            "regional_land_use_zone", "1 優", "優", confidence=0.3, requires_manual_review=True,
            extraction_role=ExtractionRole.GRADE_TEXT, extraction_subject_role=ExtractionSubjectRole.BASE,
            comparable_slot=None,
        )
        comp1_field = build_fixture_field(
            "regional_land_use_zone", "3 普通", "普通", confidence=0.3, requires_manual_review=True,
            extraction_role=ExtractionRole.GRADE_TEXT, extraction_subject_role=ExtractionSubjectRole.COMPARABLE,
            comparable_slot=1,
        )
        base_record = confirm_field(base_field, confirmed_value="優-confirmed", confirmed_by="appraiser_x")
        resolved = resolve_confirmed_values([base_field, comp1_field], confirmations=[base_record])
        assert resolved[("regional_land_use_zone", ExtractionRole.GRADE_TEXT,
                          ExtractionSubjectRole.BASE, None)] == "優-confirmed"
        assert resolved[("regional_land_use_zone", ExtractionRole.GRADE_TEXT,
                          ExtractionSubjectRole.COMPARABLE, 1)] is None

    def test_grade_code_and_grade_text_for_same_base_do_not_collide(self):
        """Regression for the bug found while wiring Grade Representation
        Contract Check A/B: BASE/GRADE_CODE and BASE/GRADE_TEXT share the
        SAME (field_id, subject_role, slot) triple -- without
        extraction_role in the identity key, one would silently overwrite
        the other in resolve_confirmed_values()'s dict."""
        code_field = build_fixture_field(
            "regional_main_road_width", "3", "3", confidence=0.3, requires_manual_review=True,
            extraction_role=ExtractionRole.GRADE_CODE, extraction_subject_role=ExtractionSubjectRole.BASE,
            comparable_slot=None,
        )
        text_field = build_fixture_field(
            "regional_main_road_width", "普通", "普通", confidence=0.3, requires_manual_review=True,
            extraction_role=ExtractionRole.GRADE_TEXT, extraction_subject_role=ExtractionSubjectRole.BASE,
            comparable_slot=None,
        )
        code_record = confirm_field(code_field, confirmed_value="3", confirmed_by="appraiser_y")
        text_record = confirm_field(text_field, confirmed_value="普通", confirmed_by="appraiser_y")
        resolved = resolve_confirmed_values([code_field, text_field], confirmations=[code_record, text_record])
        assert resolved[("regional_main_road_width", ExtractionRole.GRADE_CODE,
                          ExtractionSubjectRole.BASE, None)] == "3"
        assert resolved[("regional_main_road_width", ExtractionRole.GRADE_TEXT,
                          ExtractionSubjectRole.BASE, None)] == "普通"
