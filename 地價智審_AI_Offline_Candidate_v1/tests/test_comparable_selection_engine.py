# -*- coding: utf-8 -*-
"""
Tests for engine/comparable_selection_engine.py --
不動產估價技術規則 §25/§26/§27 (verified verbatim against
law.moj.gov.tw/LawClass/LawAll.aspx?pcode=D0060077 during this session).

Golden-Case-grounded numbers are used where practical (18M road width
example etc. from other test files' conventions), but the §25/26/27
thresholds themselves are the official regulation's own numbers (15%, 30%,
20%), not project-invented values.
"""
import sys
import os
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.comparable_selection_engine import ComparableSelectionEngine
from engine.calculation_engine import CalculationEngine
from domain.models import (
    AdjustmentResult, GradeResult, RuleResult, Evidence, SourceType, PartyRole,
    ExclusionDeterminationStatus,
)


@pytest.fixture()
def engine():
    return ComparableSelectionEngine()


def _adj(factor: str, pct: str, comparable_id: str = "comp1") -> AdjustmentResult:
    ev = Evidence(source="test", source_type=SourceType.RULE_ENGINE)
    rr = RuleResult(rule_id="TEST-01", factor=factor, matched=True, grade="優",
                     grade_code=1, grade_label="test", source_document="test", source_page="p.0")
    gr = GradeResult(field_id="test", factor=factor, party_role=PartyRole.BASE_PARCEL,
                      party_id="base", raw_value=0, normalized_value=0, rule_result=rr, evidence=ev)
    return AdjustmentResult(
        field_id="test", factor=factor, comparable_id=comparable_id, base_grade=gr, comparable_grade=gr,
        adjustment_pct=Decimal(pct), rule_id="TEST-01", matrix_row_grade_code=1, matrix_col_grade_code=1,
    )


class TestSection25SingleItemThreshold:
    """§25: 「...任一單獨項目之價格調整率大於百分之十五...應排除該比較標的」"""

    def test_single_item_over_15pct_triggers_exclusion(self, engine):
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("主要道路寬度", "16")],  # > 15
            individual_adjustments=[],
        )
        assert result.excluded is True
        assert "主要道路寬度" in result.over_15pct_items

    def test_single_item_exactly_15pct_does_not_trigger(self, engine):
        """§25 says "大於" (greater than) 15%, so exactly 15% must NOT trigger."""
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("主要道路寬度", "15")],
            individual_adjustments=[],
        )
        assert result.excluded is False
        assert result.over_15pct_items == []

    def test_negative_adjustment_uses_absolute_value(self, engine):
        """A -16% adjustment is just as much "任一單獨項目大於15%" in
        magnitude as +16% -- §25 doesn't exempt the unfavorable direction."""
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("主要道路寬度", "-16")],
            individual_adjustments=[],
        )
        assert result.excluded is True

    def test_checks_both_regional_and_individual_items(self, engine):
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("主要道路寬度", "5")],
            individual_adjustments=[_adj("深度", "20")],
        )
        assert result.excluded is True
        assert "深度" in result.over_15pct_items


class TestSection25TotalThreshold:
    """§25: 「...情況、價格日期、區域因素及個別因素調整總調整率大於百分之
    三十時，判定該比較標的與勘估標的差異過大，應排除該比較標的之適用」

    §25's text names the four inputs (情況/價格日期/區域/個別) but never
    defines how to combine them into "總調整率" -- re-verified directly
    against law.moj.gov.tw's §25 full text and the 土地徵收補償市價查估
    作業手冊's 表4 section (p.52-53) during this session. The manual's only
    "絕對值加總" formula there (item「十」：各項先取絕對值後加總) is
    explicitly tied to item「十一」's §27 WEIGHT decision, not §25's
    exclusion clause. So check_exclusion() reports two candidate readings
    for human reference and `excluded` never depends on either -- these
    tests lock in that "unconfirmed reference value" behavior, replacing
    the previous (incorrect) assumption that crossing 30% under either
    reading should auto-exclude."""

    def test_total_over_30pct_never_auto_excludes_even_when_no_single_item_over_15(self, engine):
        # 4 items at 8% each = 32% total under either reading, none individually over 15%
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "8"), _adj("b", "8")],
            individual_adjustments=[_adj("c", "8"), _adj("d", "8")],
        )
        assert result.total_adjustment_signed_sum_pct == Decimal("32")
        assert result.total_adjustment_abs_component_sum_pct == Decimal("32")
        assert result.total_over_30pct_signed_sum is True
        assert result.total_over_30pct_abs_component_sum is True
        assert result.excluded is False, "30% total is not a confirmed legal basis -- must never auto-exclude"
        assert result.over_15pct_items == []

    def test_legal_basis_is_always_marked_unconfirmed(self, engine):
        result = engine.check_exclusion(
            "comp1", regional_adjustments=[_adj("a", "5")], individual_adjustments=[],
        )
        assert result.total_adjustment_legal_basis == "LEGAL_BASIS_UNCONFIRMED"

    def test_total_exactly_30pct_does_not_trigger_either_reading(self, engine):
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "15")],
            individual_adjustments=[_adj("b", "15")],
        )
        assert result.total_adjustment_signed_sum_pct == Decimal("30")
        assert result.total_adjustment_abs_component_sum_pct == Decimal("30")
        assert result.total_over_30pct_signed_sum is False
        assert result.total_over_30pct_abs_component_sum is False

    def test_situation_and_price_date_adjustments_count_toward_total(self, engine):
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "10")],
            individual_adjustments=[_adj("b", "10")],
            situation_adjustment_pct=Decimal("6"),
            price_date_adjustment_pct=Decimal("6"),
        )
        assert result.total_adjustment_signed_sum_pct == Decimal("32")
        assert result.total_adjustment_abs_component_sum_pct == Decimal("32")
        assert result.total_over_30pct_signed_sum is True
        assert result.total_over_30pct_abs_component_sum is True

    def test_mixed_signs_make_the_two_readings_genuinely_diverge(self, engine):
        """The whole reason two candidates exist: four items that each stay
        at/under the confirmed 15% single-item threshold (so `excluded` is
        NOT triggered by that unrelated, confirmed rule) but whose signs
        mostly cancel out -- net signed sum is tiny (reading A: not
        flagged) while their raw adjustment exposure is large (reading B:
        flagged) -- exactly the scenario that would be silently
        mis-handled by picking only one interpretation."""
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "15"), _adj("b", "-15")],
            individual_adjustments=[_adj("c", "15"), _adj("d", "-13")],
        )
        assert result.over_15pct_items == []  # every item is AT (not over) 15%
        assert result.total_adjustment_signed_sum_pct == Decimal("2")
        assert result.total_adjustment_abs_component_sum_pct == Decimal("58")
        assert result.total_over_30pct_signed_sum is False
        assert result.total_over_30pct_abs_component_sum is True
        assert result.excluded is False  # still never auto-excluded on the total alone

    def test_total_over_30_and_single_item_over_15_both_surface_independently(self, engine):
        """A single item over 15% DOES drive excluded=True (confirmed §25
        text); that must keep working even though the 30% total is now
        reference-only."""
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "20")],
            individual_adjustments=[_adj("b", "20")],
        )
        assert result.excluded is True
        assert "a" in result.over_15pct_items
        assert result.total_over_30pct_signed_sum is True


class TestExclusionDeterminationStatus:
    """Three-state verdict (added because `excluded=False` alone lets a
    downstream reader conflate "genuinely confirmed not excluded" with
    "the only concern is the 30% total check, whose formula is
    LEGAL_BASIS_UNCONFIRMED" -- see ExclusionDeterminationStatus's
    docstring). `excluded` is kept for backward compatibility and must
    always agree with `exclusion_determination_status == EXCLUDED`."""

    def test_single_item_over_15pct_is_excluded(self, engine):
        result = engine.check_exclusion(
            "comp1", regional_adjustments=[_adj("a", "20")], individual_adjustments=[],
        )
        assert result.exclusion_determination_status == ExclusionDeterminationStatus.EXCLUDED
        assert result.excluded is True

    def test_no_threshold_triggered_is_not_excluded(self, engine):
        result = engine.check_exclusion(
            "comp1", regional_adjustments=[_adj("a", "5")], individual_adjustments=[_adj("b", "3")],
        )
        assert result.exclusion_determination_status == ExclusionDeterminationStatus.NOT_EXCLUDED
        assert result.excluded is False

    def test_30pct_formula_unconfirmed_is_undetermined_not_excluded_and_not_not_excluded(self, engine):
        """Same mixed-sign fixture as TestSection25TotalThreshold's
        divergence test: every item stays at/under the confirmed 15%
        single-item threshold, but the abs-component-sum candidate reading
        crosses 30% -- this must land on UNDETERMINED, neither of the
        other two states."""
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "15"), _adj("b", "-15")],
            individual_adjustments=[_adj("c", "15"), _adj("d", "-13")],
        )
        assert result.exclusion_determination_status == ExclusionDeterminationStatus.UNDETERMINED
        assert result.exclusion_determination_status != ExclusionDeterminationStatus.EXCLUDED
        assert result.exclusion_determination_status != ExclusionDeterminationStatus.NOT_EXCLUDED
        # Backward-compat boolean must NOT be readable as "confirmed pass" --
        # it is simply False, same bit pattern as the true NOT_EXCLUDED case.
        assert result.excluded is False

    def test_excluded_boolean_always_agrees_with_status_across_all_three_states(self, engine):
        excluded_case = engine.check_exclusion(
            "comp1", regional_adjustments=[_adj("a", "20")], individual_adjustments=[],
        )
        not_excluded_case = engine.check_exclusion(
            "comp1", regional_adjustments=[_adj("a", "5")], individual_adjustments=[],
        )
        undetermined_case = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "15"), _adj("b", "-15")],
            individual_adjustments=[_adj("c", "15"), _adj("d", "-13")],
        )
        assert excluded_case.excluded == (
            excluded_case.exclusion_determination_status == ExclusionDeterminationStatus.EXCLUDED
        )
        assert not_excluded_case.excluded == (
            not_excluded_case.exclusion_determination_status == ExclusionDeterminationStatus.EXCLUDED
        )
        assert undetermined_case.excluded == (
            undetermined_case.exclusion_determination_status == ExclusionDeterminationStatus.EXCLUDED
        )
        assert excluded_case.excluded is True
        assert not_excluded_case.excluded is False
        assert undetermined_case.excluded is False


class TestSection25ExceptionProviso:
    """§25但書：「但勘估標的性質特殊或區位特殊缺乏市場交易資料，並於估價
    報告書中敘明者，不在此限」-- this codebase records whether the caller
    supplied documentation, it never invents or auto-approves the
    exception."""

    def test_rule_verdict_is_excluded_regardless_of_exception_note(self, engine):
        """excluded reflects the RULE's verdict; the exception is a
        separate, human-documented override surfaced alongside it, not a
        silent suppression of the finding itself."""
        result = engine.check_exclusion(
            "comp1",
            regional_adjustments=[_adj("a", "20")],
            individual_adjustments=[],
            exception_note="本案位處特殊地形區位，市場交易資料稀少，經查證確認此為該區段唯一可得比較標的",
        )
        assert result.excluded is True  # the §25 threshold verdict itself never changes
        assert result.exception_claimed is True
        assert result.exception_note is not None

    def test_no_exception_note_means_exception_not_claimed(self, engine):
        result = engine.check_exclusion(
            "comp1", regional_adjustments=[_adj("a", "20")], individual_adjustments=[],
        )
        assert result.exception_claimed is False
        assert result.exception_note is None


class TestSection26PriceGap:
    """§26：「檢討後試算價格之間差距仍達百分之二十以上者，應排除該試算
    價格之適用」，差距 = (高低價格之差) / (高低價格平均值)。"""

    def test_exact_official_formula(self, engine):
        # high=120, low=100 -> diff=20, avg=110 -> 20/110 = 18.1818...%
        result = engine.check_trial_price_gap({"comp1": Decimal("120"), "comp2": Decimal("100")})
        assert result.max_price == Decimal("120")
        assert result.min_price == Decimal("100")
        expected_gap = (Decimal("120") - Decimal("100")) / ((Decimal("120") + Decimal("100")) / Decimal("2")) * Decimal("100")
        assert result.gap_pct == expected_gap
        assert result.triggered is False  # ~18.18% < 20%

    def test_gap_at_exactly_20pct_triggers(self, engine):
        # high=110, low=90 -> diff=20, avg=100 -> exactly 20%
        result = engine.check_trial_price_gap({"comp1": Decimal("110"), "comp2": Decimal("90")})
        assert result.gap_pct == Decimal("20")
        assert result.triggered is True  # §26 says "達" (reaches) 20%, i.e. >= not just >

    def test_identifies_which_comparable_is_high_and_low(self, engine):
        result = engine.check_trial_price_gap({"compA": Decimal("100"), "compB": Decimal("200")})
        assert result.max_comparable_id == "compB"
        assert result.min_comparable_id == "compA"

    def test_picks_actual_max_min_across_three_or_more(self, engine):
        result = engine.check_trial_price_gap(
            {"c1": Decimal("100"), "c2": Decimal("150"), "c3": Decimal("90")}
        )
        assert result.max_price == Decimal("150")
        assert result.min_price == Decimal("90")

    def test_single_comparable_raises_not_silently_passes(self, engine):
        """§26's comparison is undefined for one price -- must not silently
        report triggered=False, which would misrepresent 'not applicable'
        as 'checked and fine'."""
        with pytest.raises(ValueError):
            engine.check_trial_price_gap({"comp1": Decimal("100")})


class TestSection27SuggestedWeight:
    """§27 gives no formula -- every result here must be explicitly marked
    as a non-legal suggestion, never presented as if it were as
    deterministic/official as a Grade or Adjustment Matrix lookup."""

    def test_always_marked_as_requiring_human_confirmation(self, engine):
        weights = engine.suggest_weights({"comp1": Decimal("10"), "comp2": Decimal("20")})
        assert all(w.requires_human_confirmation is True for w in weights.values())

    def test_source_document_explicitly_disclaims_being_the_official_regulation(self, engine):
        """The string legitimately mentions 不動產估價技術規則 (to explain
        WHY this is a suggestion -- the regulation doesn't specify one),
        but must do so as a negation/disclaimer ("非...明文規定"), never as
        an unqualified citation the way ComparableExclusionCheck's
        source_document="不動產估價技術規則" is for §25/§26."""
        weights = engine.suggest_weights({"comp1": Decimal("10")})
        source = weights["comp1"].source_document
        assert source != "不動產估價技術規則"
        assert "系統建議" in source
        assert "非" in source

    def test_smaller_adjustment_magnitude_gets_larger_suggested_weight(self, engine):
        weights = engine.suggest_weights({"comp1": Decimal("5"), "comp2": Decimal("25")})
        assert weights["comp1"].suggested_weight_pct > weights["comp2"].suggested_weight_pct

    def test_equal_magnitudes_split_evenly(self, engine):
        weights = engine.suggest_weights({"comp1": Decimal("10"), "comp2": Decimal("10"), "comp3": Decimal("10")})
        pcts = sorted(w.suggested_weight_pct for w in weights.values())
        # allow at most a 0.01 residual adjustment on one of the three
        assert pcts[-1] - pcts[0] <= Decimal("0.02")

    def test_zero_adjustment_does_not_divide_by_zero(self, engine):
        weights = engine.suggest_weights({"comp1": Decimal("0"), "comp2": Decimal("10")})
        assert weights["comp1"].suggested_weight_pct > weights["comp2"].suggested_weight_pct

    def test_single_comparable_gets_100pct(self, engine):
        weights = engine.suggest_weights({"comp1": Decimal("7")})
        assert weights["comp1"].suggested_weight_pct == Decimal("100.00")

    def test_suggested_weights_always_sum_to_exactly_100(self, engine):
        """Rounding drift (e.g. three equal comparables -> 33.33x3=99.99)
        must be corrected -- a human who accepts every suggestion as-is
        should not then hit CalculationEngine's exact-100% validation."""
        cases = [
            {"c1": Decimal("10"), "c2": Decimal("10"), "c3": Decimal("10")},
            {"c1": Decimal("3"), "c2": Decimal("7"), "c3": Decimal("11")},
            {"c1": Decimal("0")},
            {"c1": Decimal("5"), "c2": Decimal("5")},
        ]
        for adjustment_abs_sums in cases:
            weights = engine.suggest_weights(adjustment_abs_sums)
            total = sum((w.suggested_weight_pct for w in weights.values()), Decimal("0"))
            assert total == Decimal("100.00"), f"{adjustment_abs_sums} -> {total}"

    def test_accepting_suggestions_as_is_works_directly_in_calculation_engine(self, engine):
        """End-to-end proof: this is not just internally self-consistent --
        it's actually valid input to the function it's meant to feed."""
        adjustment_abs_sums = {"c1": Decimal("10"), "c2": Decimal("10"), "c3": Decimal("10")}
        weights = engine.suggest_weights(adjustment_abs_sums)
        weights_pct = {cid: w.suggested_weight_pct for cid, w in weights.items()}
        trial_prices = {"c1": Decimal("100000"), "c2": Decimal("110000"), "c3": Decimal("105000")}

        calc = CalculationEngine()
        step = calc.base_parcel_comparison_price(trial_prices, weights_pct)  # must not raise
        assert step.rounded_result is not None

    def test_empty_input_raises(self, engine):
        with pytest.raises(ValueError):
            engine.suggest_weights({})
