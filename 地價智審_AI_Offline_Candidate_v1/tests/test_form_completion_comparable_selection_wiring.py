# -*- coding: utf-8 -*-
"""
Tests for engine/form_completion_engine.py's wiring of
engine/comparable_selection_engine.py (不動產估價技術規則 §25/§26/§27) into
the main Structured Input -> Completed Fields pipeline. Before this wiring,
ComparableSelectionEngine was fully tested (tests/test_comparable_selection_
engine.py) but never called from FormCompletionEngine/complete_form.py --
this file locks in that it is no longer an orphan module.

Most tests here call FormCompletionEngine's new methods directly with
hand-built AdjustmentResult/CompetitionCase objects (same pattern as
tests/test_comparable_selection_engine.py's `_adj()` helper) rather than
driving the real RuleEngine to coincidentally produce a >15%/>20% threshold
breach -- that would make the test fragile against unrelated rule-data
changes. The one true end-to-end check (TestGoldenCaseEndToEndWiring) runs
the real pipeline to prove the wiring is actually reachable from
complete_form(), not just correct in isolation.
"""
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "golden"))

from domain.models import (  # noqa: E402
    AdjustmentResult, GradeResult, RuleResult, Evidence, SourceType, PartyRole,
    CompetitionCase, FieldStatus,
)
from engine.grade_engine import GradeEngine  # noqa: E402
from engine.adjustment_engine import AdjustmentEngine  # noqa: E402
from engine.calculation_engine import CalculationEngine  # noqa: E402
from engine.comparable_selection_engine import ComparableSelectionEngine  # noqa: E402
from engine.form_completion_engine import FormCompletionEngine  # noqa: E402
from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402


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


def _case(comparable_ids, land_price, date_rate, weight=None):
    return CompetitionCase(
        case_no="WIRING-TEST-001", appraisal_period="1140901", appraisal_base_date="1140901",
        segment_code="TEST-00", segment_scope="測試區段", city="新北市", district="測試區",
        land_use_type="商業用地", base_parcel_id="測試比準地",
        comparable_ids=comparable_ids,
        comparable_land_normal_price={cid: land_price for cid in comparable_ids},
        comparable_price_date_adjustment_rate={cid: date_rate for cid in comparable_ids},
        comparable_weight=weight or {},
    )


@pytest.fixture()
def fce():
    """No real RuleEngine needed -- every method under test here takes
    pre-computed AdjustmentResult lists, not raw factor inputs."""
    return FormCompletionEngine(GradeEngine(None), AdjustmentEngine(None), CalculationEngine())


class TestComparableExclusionWiring:
    def test_no_threshold_breach_is_completed_not_manual_review(self, fce):
        case = _case(["comp1"], Decimal("100000"), Decimal("2"))
        fields, abs_sum = fce.complete_comparable_price(
            case, "comp1", individual_adjustments=[_adj("深度", "3")],
            regional_adjustments=[_adj("主要道路寬度", "2")],
        )
        exclusion = next(f for f in fields if f.field_id == "comparable_exclusion_check_comp1")
        assert exclusion.status == FieldStatus.COMPLETED
        assert exclusion.warnings == []
        assert abs_sum is not None

    def test_single_item_over_15pct_is_manual_review_with_warning(self, fce):
        case = _case(["comp1"], Decimal("100000"), Decimal("2"))
        fields, _ = fce.complete_comparable_price(
            case, "comp1", individual_adjustments=[_adj("深度", "20")],
            regional_adjustments=[],
        )
        exclusion = next(f for f in fields if f.field_id == "comparable_exclusion_check_comp1")
        assert exclusion.status == FieldStatus.MANUAL_REVIEW_REQUIRED
        assert exclusion.warnings, "must surface a warning, not silently drop the comparable"
        assert "§25" in exclusion.warnings[0]
        assert "深度" in exclusion.calculation

    def test_30pct_total_alone_is_undetermined_not_excluded_and_not_no_need(self, fce):
        """The 30% "總調整率" threshold has no confirmed formula (see
        ComparableExclusionCheck's docstring) -- it must still surface for
        human attention (status=MANUAL_REVIEW_REQUIRED) but must be worded
        as UNDETERMINED ("待確認"), NOT as a rule-driven exclusion
        recommendation ("建議排除") NOR as a confirmed pass ("無需排除") --
        either of those would misrepresent an unconfirmed formula's result
        as a determined one."""
        case = _case(["comp1"], Decimal("100000"), Decimal("2"))
        fields, _ = fce.complete_comparable_price(
            case, "comp1",
            individual_adjustments=[_adj("深度", "15"), _adj("寬度", "-14")],
            regional_adjustments=[_adj("主要道路寬度", "15")],
        )
        exclusion = next(f for f in fields if f.field_id == "comparable_exclusion_check_comp1")
        assert exclusion.status == FieldStatus.MANUAL_REVIEW_REQUIRED
        assert exclusion.final_value != "建議排除，需人工確認"
        assert "無需排除" not in exclusion.final_value
        assert "待確認" in exclusion.final_value
        assert any("LEGAL_BASIS_UNCONFIRMED" in w or "查無明文定義" in w for w in exclusion.warnings)

    def test_no_exclusion_field_when_price_chain_cannot_start(self, fce):
        """land_price/date_rate missing -> the whole chain bails out before
        exclusion-checking is even possible; must not fabricate a check."""
        case = _case(["comp1"], Decimal("100000"), Decimal("2"))
        case.comparable_land_normal_price = {}
        case.comparable_price_date_adjustment_rate = {}
        fields, abs_sum = fce.complete_comparable_price(
            case, "comp1", individual_adjustments=[], regional_adjustments=[],
        )
        assert not any(f.field_id == "comparable_exclusion_check_comp1" for f in fields)
        assert abs_sum is None


class TestWeightSuggestionWiring:
    def test_single_comparable_official_note_when_weight_already_supplied(self, fce):
        case = _case(["comp1"], Decimal("100000"), Decimal("2"), weight={"comp1": Decimal("100")})
        fields = fce.complete_comparable_weight_fields(case, {"comp1": Decimal("5")})
        weight_field = fields[0]
        assert weight_field.status == FieldStatus.COMPLETED
        assert weight_field.final_value == Decimal("100")
        assert weight_field.source == "使用者輸入"
        assert "作業手冊p.52-53" in weight_field.calculation

    def test_single_comparable_missing_weight_flags_official_expectation(self, fce):
        case = _case(["comp1"], Decimal("100000"), Decimal("2"))
        fields = fce.complete_comparable_weight_fields(case, {"comp1": Decimal("5")})
        weight_field = fields[0]
        assert weight_field.status == FieldStatus.MANUAL_REVIEW_REQUIRED
        assert weight_field.final_value is None
        assert "應為100%" in weight_field.calculation

    def test_multi_comparable_equal_adjustment_suggests_even_split(self, fce):
        case = _case(["comp1", "comp2"], Decimal("100000"), Decimal("2"))
        fields = fce.complete_comparable_weight_fields(
            case, {"comp1": Decimal("5"), "comp2": Decimal("5")},
        )
        by_id = {f.field_id: f for f in fields}
        for cid in ("comp1", "comp2"):
            f = by_id[f"comparable_weight_{cid}"]
            assert f.status == FieldStatus.MANUAL_REVIEW_REQUIRED
            assert f.final_value is None, "suggestion must never auto-fill final_value"
            assert "50.00%" in f.calculation
            assert "系統建議" in f.calculation
            assert f.warnings and "§27" in f.warnings[0]

    def test_multi_comparable_smaller_adjustment_gets_larger_suggested_weight(self, fce):
        """§27's own language: 相近程度 (closeness) -> smaller total
        adjustment magnitude is presumed more similar -> suggested higher
        weight. comp1 has the smaller abs_sum here."""
        case = _case(["comp1", "comp2"], Decimal("100000"), Decimal("2"))
        fields = fce.complete_comparable_weight_fields(
            case, {"comp1": Decimal("5"), "comp2": Decimal("25")},
        )
        by_id = {f.field_id: f for f in fields}
        comp1_pct = Decimal(by_id["comparable_weight_comp1"].calculation.split("：")[-1].split("%")[0])
        comp2_pct = Decimal(by_id["comparable_weight_comp2"].calculation.split("：")[-1].split("%")[0])
        assert comp1_pct > comp2_pct
        assert comp1_pct + comp2_pct == Decimal("100.00")

    def test_explicit_weight_on_one_comparable_does_not_block_suggestion_on_the_other(self, fce):
        case = _case(["comp1", "comp2"], Decimal("100000"), Decimal("2"), weight={"comp1": Decimal("60")})
        fields = fce.complete_comparable_weight_fields(
            case, {"comp1": Decimal("5"), "comp2": Decimal("5")},
        )
        by_id = {f.field_id: f for f in fields}
        assert by_id["comparable_weight_comp1"].status == FieldStatus.COMPLETED
        assert by_id["comparable_weight_comp1"].final_value == Decimal("60")
        assert by_id["comparable_weight_comp2"].status == FieldStatus.MANUAL_REVIEW_REQUIRED
        assert by_id["comparable_weight_comp2"].final_value is None


class TestTrialPriceGapWiring:
    def test_fewer_than_two_prices_returns_none(self, fce):
        assert fce.complete_trial_price_gap_field({"comp1": Decimal("100000")}) is None
        assert fce.complete_trial_price_gap_field({}) is None

    def test_small_gap_is_completed_no_warning(self, fce):
        field = fce.complete_trial_price_gap_field(
            {"comp1": Decimal("100000"), "comp2": Decimal("105000")},
        )
        assert field.status == FieldStatus.COMPLETED
        assert field.warnings == []

    def test_large_gap_is_manual_review_with_warning(self, fce):
        field = fce.complete_trial_price_gap_field(
            {"comp1": Decimal("100000"), "comp2": Decimal("130000")},
        )
        assert field.status == FieldStatus.MANUAL_REVIEW_REQUIRED
        assert field.warnings
        assert "§26" in field.warnings[0]
        assert "comp1" in field.warnings[0] and "comp2" in field.warnings[0]


class TestConstructorBackwardCompatible:
    def test_three_positional_args_still_work_without_selection_engine(self):
        fce_default = FormCompletionEngine(GradeEngine(None), AdjustmentEngine(None), CalculationEngine())
        assert isinstance(fce_default._selection, ComparableSelectionEngine)

    def test_explicit_selection_engine_is_used(self):
        custom = ComparableSelectionEngine()
        fce_custom = FormCompletionEngine(
            GradeEngine(None), AdjustmentEngine(None), CalculationEngine(),
            comparable_selection_engine=custom,
        )
        assert fce_custom._selection is custom


class TestGoldenCaseEndToEndWiring:
    """Proves the wiring is reachable from complete_form() itself, not just
    correct when the new methods are called directly."""

    @pytest.fixture()
    def golden_result(self, engine):
        ge = GradeEngine(engine)
        ae = AdjustmentEngine(engine)
        fce_real = FormCompletionEngine(ge, ae, CalculationEngine())
        return fce_real.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)

    def test_exclusion_check_field_present_and_passes_for_golden_case(self, golden_result):
        comparable_id = GOLDEN_CASE.comparable_ids[0]
        exclusion = next(
            f for f in golden_result.fields if f.field_id == f"comparable_exclusion_check_{comparable_id}"
        )
        assert exclusion.status == FieldStatus.COMPLETED

    def test_no_trial_price_gap_field_with_only_one_comparable(self, golden_result):
        assert not any(f.field_id == "trial_price_gap_check" for f in golden_result.fields)

    def test_weight_field_still_completed_with_official_note(self, golden_result):
        comparable_id = GOLDEN_CASE.comparable_ids[0]
        weight_field = next(
            f for f in golden_result.fields if f.field_id == f"comparable_weight_{comparable_id}"
        )
        assert weight_field.status == FieldStatus.COMPLETED
        assert weight_field.final_value == Decimal("100")
