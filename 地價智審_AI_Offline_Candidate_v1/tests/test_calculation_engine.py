# -*- coding: utf-8 -*-
"""Calculation Engine tests: precision preservation, rounding rule placement,
and rejection of invalid weight sets."""
import sys
import os
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.calculation_engine import CalculationEngine, CalculationEngineError
from domain.models import AdjustmentResult, GradeResult, RuleResult, Evidence, SourceType, PartyRole


@pytest.fixture()
def calc():
    return CalculationEngine()


def _fake_adjustment(factor: str, pct: str) -> AdjustmentResult:
    """Builds a minimal AdjustmentResult for testing individual_adjustment_total /
    regional_total_adjustment without needing a real RuleEngine round-trip."""
    ev = Evidence(source="test", source_type=SourceType.RULE_ENGINE)
    rr = RuleResult(rule_id="TEST-01", factor=factor, matched=True, grade="優",
                     grade_code=1, grade_label="test", source_document="test", source_page="p.0")
    gr = GradeResult(field_id="test", factor=factor, party_role=PartyRole.BASE_PARCEL,
                      party_id="base", raw_value=0, normalized_value=0, rule_result=rr, evidence=ev)
    return AdjustmentResult(
        field_id="test", factor=factor, comparable_id="comp1", base_grade=gr, comparable_grade=gr,
        adjustment_pct=Decimal(pct), rule_id="TEST-01", matrix_row_grade_code=1, matrix_col_grade_code=1,
    )


class TestPrecisionPreservation:
    def test_price_date_adjustment_uses_decimal_not_float(self, calc):
        step = calc.price_date_adjustment(184763, 2.00)
        assert isinstance(step.raw_result, Decimal)
        assert step.raw_result == Decimal("188458.26")
        assert step.rounded_result is None  # must NOT be rounded at this step

    def test_trial_price_from_unrounded_intermediate_matches_official(self, calc):
        step1 = calc.price_date_adjustment(184763, 2.00)
        step2 = calc.trial_price(step1.raw_result, Decimal("0.00"), Decimal("13.00"))
        assert step2.raw_result == Decimal("212957.8338")

    def test_trial_price_from_rounded_intermediate_would_mismatch_official(self, calc):
        """Negative-control test: demonstrates WHY the engine must not round
        early. Using the displayed 188,459 instead of raw 188,458.26 produces
        212,959, one unit off from the official 212,958."""
        step_wrong = calc.trial_price(Decimal("188459"), Decimal("0.00"), Decimal("13.00"))
        assert step_wrong.raw_result.quantize(Decimal("1")) == Decimal("212959")
        assert step_wrong.raw_result.quantize(Decimal("1")) != Decimal("212958")

    def test_final_rounding_only_happens_at_base_parcel_comparison_price(self, calc):
        step1 = calc.price_date_adjustment(184763, 2.00)
        step2 = calc.trial_price(step1.raw_result, Decimal("0.00"), Decimal("13.00"))
        final = calc.base_parcel_comparison_price(
            {"comp1": step2.raw_result}, {"comp1": Decimal("100")}
        )
        assert final.rounded_result == Decimal("212958")
        assert final.raw_result == Decimal("212957.8338")  # raw preserved even at final step


class TestIndividualAdjustmentTotal:
    def test_sums_all_differential_rates(self, calc):
        adjustments = [
            _fake_adjustment("深度", "1.00"),
            _fake_adjustment("道路種類", "2.00"),
            _fake_adjustment("面前道路寬度", "5.00"),
            _fake_adjustment("嫌惡設施之有無", "3.00"),
            _fake_adjustment("停車方便性", "2.00"),
        ]
        step = calc.individual_adjustment_total(adjustments)
        assert step.raw_result == Decimal("13.00")

    def test_empty_list_sums_to_zero(self, calc):
        step = calc.individual_adjustment_total([])
        assert step.raw_result == Decimal("0")


class TestRegionalTotalAdjustment:
    def test_all_zero_when_same_segment(self, calc):
        adjustments = [_fake_adjustment(f"factor{i}", "0.00") for i in range(28)]
        step = calc.regional_total_adjustment(adjustments)
        assert step.raw_result == Decimal("0")


class TestAdjustmentAbsSum:
    def test_matches_golden_case(self, calc):
        step = calc.adjustment_abs_sum(Decimal("2.00"), Decimal("0.00"), Decimal("13.00"))
        assert step.raw_result == Decimal("15.00")

    def test_negative_rates_are_absolute_valued(self, calc):
        step = calc.adjustment_abs_sum(Decimal("-2.00"), Decimal("-1.00"), Decimal("-13.00"))
        assert step.raw_result == Decimal("16.00")


class TestWeightValidation:
    def test_weights_must_sum_to_100(self, calc):
        with pytest.raises(CalculationEngineError):
            calc.base_parcel_comparison_price(
                {"comp1": Decimal("100000"), "comp2": Decimal("200000")},
                {"comp1": Decimal("50"), "comp2": Decimal("40")},  # sums to 90, not 100
            )

    def test_mismatched_keys_rejected(self, calc):
        with pytest.raises(CalculationEngineError):
            calc.base_parcel_comparison_price(
                {"comp1": Decimal("100000")},
                {"comp2": Decimal("100")},
            )

    def test_multi_comparable_weighted_average(self, calc):
        result = calc.base_parcel_comparison_price(
            {"comp1": Decimal("200000"), "comp2": Decimal("100000")},
            {"comp1": Decimal("60"), "comp2": Decimal("40")},
        )
        # 200000*0.6 + 100000*0.4 = 120000 + 40000 = 160000
        assert result.rounded_result == Decimal("160000")


class TestRoundingHalfUp:
    def test_half_rounds_up_not_banker(self, calc):
        # 0.5 should round to 1 under ROUND_HALF_UP (conventional 四捨五入),
        # not to 0 as Python's default banker's rounding would.
        result = calc.base_parcel_comparison_price(
            {"comp1": Decimal("100000.5")}, {"comp1": Decimal("100")}
        )
        assert result.rounded_result == Decimal("100001")
