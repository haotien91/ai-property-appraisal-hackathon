# -*- coding: utf-8 -*-
"""
Part E — Golden Test: 案號 1140901-99-001（新北市金山區 P002-00）full
Structured Input -> Completed Fields pipeline test.

All expected values are the same officially-printed Golden Case numbers
independently re-verified in Phase 2 (calculation_dependency.md) and
re-derived again from first principles in Phase 3 (rule_engine_spec.md).
This test exercises the REAL engines (GradeEngine -> AdjustmentEngine ->
CalculationEngine -> FormCompletionEngine), not a mocked/hardcoded shortcut:
if any rule value, matrix orientation, or calculation formula regresses,
this test will fail.
"""
import sys
import os
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "golden"))

from domain.models import FieldStatus  # noqa: E402
from engine.grade_engine import GradeEngine  # noqa: E402
from engine.adjustment_engine import AdjustmentEngine  # noqa: E402
from engine.calculation_engine import CalculationEngine  # noqa: E402
from engine.form_completion_engine import FormCompletionEngine  # noqa: E402
from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402


@pytest.fixture()
def golden_result(engine):
    """`engine` fixture (Phase 3's RuleEngine, loaded with real rule data) is
    reused from tests/conftest.py so this test exercises the exact same
    rule set validated by the 69 Phase 3 tests, not a separate copy."""
    ge = GradeEngine(engine)
    ae = AdjustmentEngine(engine)
    ce = CalculationEngine()
    fce = FormCompletionEngine(ge, ae, ce)
    return fce.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)


class TestGoldenFormCompletionEndToEnd:
    def test_final_price_matches_official_golden_case(self, golden_result):
        final = next(f for f in golden_result.fields if f.field_id == "base_parcel_comparison_price")
        assert final.status == FieldStatus.COMPLETED
        assert final.final_value == Decimal("212958"), (
            "比準地比較價格 must equal the officially printed Golden Case value 212,958"
        )

    def test_individual_adjustment_total_matches_official_13_percent(self, golden_result):
        field = next(f for f in golden_result.fields if "individual_adjustment_total" in f.field_id)
        assert field.final_value == Decimal("13.00")

    def test_regional_total_matches_official_zero(self, golden_result):
        field = next(f for f in golden_result.fields if "region_adjustment_rate" in f.field_id)
        assert field.final_value == Decimal("0.00")

    def test_adjustment_abs_sum_matches_official_15_percent(self, golden_result):
        field = next(f for f in golden_result.fields if "adjustment_abs_sum" in f.field_id)
        assert field.final_value == Decimal("15.00")

    def test_trial_price_matches_full_precision_official_value(self, golden_result):
        field = next(f for f in golden_result.fields if f.field_id.startswith("trial_price_"))
        assert field.final_value == Decimal("212957.8338")

    def test_no_field_silently_skipped(self, golden_result):
        """Every one of the 19 individual + 28 regional factors must produce
        either a COMPLETED or an explicitly-flagged
        MANUAL_REVIEW_REQUIRED/UNKNOWN field -- never silently absent."""
        assert len(golden_result.fields) >= (19 + 28 + 6), (
            f"expected at least 19 individual + 28 regional + 6 summary fields, "
            f"got {len(golden_result.fields)}"
        )

    def test_only_expected_manual_review_field_is_price_formation_similarity(self, golden_result):
        """price_formation_similarity is a genuine estimator-judgment field
        (Phase 1 open_questions.md B-2/C-5): it is CORRECT for this to be the
        only field requiring manual review, not a bug."""
        manual = [f for f in golden_result.fields if f.status == FieldStatus.MANUAL_REVIEW_REQUIRED]
        assert len(manual) == 1
        assert "price_formation_similarity" in manual[0].field_id

    def test_no_unknown_fields(self, golden_result):
        unknown = [f for f in golden_result.fields if f.status == FieldStatus.UNKNOWN]
        assert unknown == [], f"unexpected UNKNOWN fields: {[f.field_id for f in unknown]}"

    def test_every_completed_field_carries_full_traceability(self, golden_result):
        """TRACEABILITY requirement: every COMPLETED field must expose a
        source. Fields produced BY the Rule/Adjustment Engine must also
        carry rule_id+grade; fields produced BY the Calculation Engine must
        also carry formula+calculation. Directly user/config-supplied
        fields (e.g. comparable_weight, when given up front rather than
        derived) legitimately carry neither chain -- their source field
        alone is their provenance, and that is not a gap."""
        DIRECTLY_SUPPLIED_PREFIXES = ("comparable_weight_",)
        for f in golden_result.fields:
            if f.status != FieldStatus.COMPLETED:
                continue
            assert f.source, f"{f.field_id} missing source"
            if f.field_id.startswith(DIRECTLY_SUPPLIED_PREFIXES):
                continue
            has_rule_chain = f.rule_id is not None and f.grade is not None
            has_calc_chain = f.formula is not None and f.calculation is not None
            assert has_rule_chain or has_calc_chain, (
                f"{f.field_id} has neither a rule-based nor a calculation-based traceability chain"
            )

    def test_depth_field_completion_full_chain(self, golden_result):
        """Spot-check one individual factor's FieldCompletion for the exact
        traceability fields required by the Phase 4 spec: raw_value,
        normalized_value, source, rule_id, grade, adjustment, formula,
        calculation, final_value."""
        field = next(
            f for f in golden_result.fields
            if f.field_id == "individual_land_depth_differential_rate_溫泉段218地號"
        )
        assert field.raw_value is not None
        assert field.normalized_value is not None
        assert field.source
        assert field.rule_id
        assert "普通" in field.grade and "稍劣" in field.grade
        assert field.adjustment == Decimal("1.00")
        assert field.formula
        assert field.calculation
        assert field.final_value == Decimal("1.00")


class TestAPIContractSerialization:
    """Regression test for Phase 4 NO-GO Recovery BLK-02: completed_count/
    manual_review_count/unknown_count are documented in
    docs/phase4/frontend_api_contract.md as part of the complete-form JSON
    response, but were implemented as plain @property (not @computed_field),
    so Pydantic's model_dump_json() silently omitted them. This test locks
    in that they now actually appear in serialized output."""

    def test_summary_counts_appear_in_json_serialization(self, golden_result):
        dumped = golden_result.model_dump_json()
        assert '"completed_count"' in dumped
        assert '"manual_review_count"' in dumped
        assert '"unknown_count"' in dumped

    def test_summary_counts_have_correct_values_in_serialized_output(self, golden_result):
        import json as _json
        dumped = _json.loads(golden_result.model_dump_json())
        assert dumped["completed_count"] == golden_result.completed_count
        assert dumped["manual_review_count"] == golden_result.manual_review_count
        assert dumped["unknown_count"] == golden_result.unknown_count
