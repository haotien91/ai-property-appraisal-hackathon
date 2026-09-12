# -*- coding: utf-8 -*-
"""
Core Rule Engine tests: Lower Boundary, Upper Boundary, Inclusive/Exclusive,
Boolean, Category, Positive Distance, Negative Distance, Rule Not Found,
Wrong Unit, Adjustment Matrix.
"""
import pytest
from rule_engine import RuleNotFoundError, WrongUnitError, GradeNotComparableError
from conftest import CITY, DISTRICT, LAND_USE


# ---------------------------------------------------------------------
# Boundary tests on 主要道路寬度 (main_road_width)
# Bands: 優=30以上(incl) 稍優=20~30 普通=15~20 稍劣=10~15 劣=<10 (all lower-inclusive, upper-exclusive)
# ---------------------------------------------------------------------
class TestLowerUpperBoundary:
    FACTOR = "主要道路寬度"

    def test_lower_boundary_inclusive_15_is_normal(self, engine):
        # 15 is the lower bound of 普通(15m以上未滿20m) -> should be 普通, NOT 稍劣
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 15, unit="M")
        assert r.grade == "普通"
        assert r.grade_code == 3

    def test_upper_boundary_exclusive_20_is_not_normal(self, engine):
        # 20 is the upper bound of 普通(未滿20m) -> should roll over to 稍優, NOT 普通
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 20, unit="M")
        assert r.grade == "稍優"
        assert r.grade_code == 2

    def test_just_below_upper_boundary_is_normal(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 19.99, unit="M")
        assert r.grade == "普通"

    def test_lowest_band_no_lower_bound(self, engine):
        # 劣 = 未滿10m或無 (no explicit lower bound, i.e. covers 0 and negative-ish inputs)
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 0, unit="M")
        assert r.grade == "劣"
        assert r.grade_code == 5

    def test_highest_band_no_upper_bound(self, engine):
        # 優 = 30m以上 (no explicit upper bound)
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 999, unit="M")
        assert r.grade == "優"
        assert r.grade_code == 1

    def test_golden_case_18m_is_normal(self, engine):
        # Golden Case: 主要道路寬度=18M, 表5-2 records grade_code=3 (普通)
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 18, unit="M")
        assert r.grade == "普通"
        assert r.grade_code == 3


class TestInclusiveExclusive:
    """Explicit test that the engine respects lower_inclusive=True /
    upper_inclusive=False semantics used throughout the source tables
    ('X以上未滿Y')."""

    def test_building_coverage_ratio_exact_lower_bound_60_is_excellent(self, engine):
        # 優=60%以上 (60 itself included)
        r = engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 60, unit="%", rule_set="regional")
        assert r.grade == "優"

    def test_building_coverage_ratio_59_99_is_not_excellent(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 59.99, unit="%", rule_set="regional")
        assert r.grade == "稍優"

    def test_building_coverage_ratio_golden_case_70_percent(self, engine):
        # Golden Case: 建蔽率=70% both sides, 表5-2 records grade_code=1 (優)
        r = engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 70, unit="%", rule_set="regional")
        assert r.grade == "優"
        assert r.grade_code == 1


class TestBoolean:
    def test_construction_prohibited_none_is_excellent(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "有無禁止建築", "無")
        assert r.grade == "優"
        assert r.grade_code == 1

    def test_construction_prohibited_yes_is_poor(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "有無禁止建築", "有")
        assert r.grade == "劣"
        assert r.grade_code == 5

    def test_parking_convenience_boolean_python_bool_input(self, engine, individual_rules):
        # 停車方便性 (individual) uses a 2-level boolean-style factor with
        # non-有/無 labels; verify Python bool True/False is NOT silently
        # coerced into an unrelated label (should raise, not guess).
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, DISTRICT, LAND_USE, "停車方便性", True)


class TestCategory:
    def test_land_use_zone_second_commercial(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "使用分區(使用地類別)", "第二種商業區")
        assert r.grade == "優"
        assert r.grade_code == 1

    def test_land_use_zone_unknown_category_raises(self, engine):
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, DISTRICT, LAND_USE, "使用分區(使用地類別)", "工業區")

    def test_road_type_golden_case_categories(self, engine, individual_rules):
        base = engine.grade(CITY, DISTRICT, LAND_USE, "道路種類", "主要道路")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "道路種類", "次要道路")
        assert base.grade == "優" and comp.grade == "稍優"


class TestPositiveDistance:
    """distance_positive: closer = better grade (優)."""
    FACTOR = "接近大型車站之程度"

    def test_close_distance_is_excellent(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 100, unit="M")
        assert r.grade == "優"

    def test_far_distance_is_poor(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 5000, unit="M")
        assert r.grade == "劣"

    def test_school_proximity_individual_golden_case(self, engine, individual_rules):
        # Golden Case 表4: 比準地 150m -> 優 (優=未滿200m)
        r = engine.grade(CITY, DISTRICT, LAND_USE, "接近學校之程度", 150, unit="M")
        assert r.grade == "優"


class TestNegativeDistance:
    """distance_negative: farther = better grade (優) — nuisance/pollution facilities."""
    FACTOR = "殯葬設施之有無及接近程度"

    def test_far_distance_is_excellent(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 5000, unit="M")
        assert r.grade == "優"

    def test_close_distance_is_poor(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 80, unit="M")
        assert r.grade == "劣"

    def test_golden_case_cemetery_80m_is_poor(self, engine):
        # Golden Case 表1: 墓地距80M, 表5-2 records grade_code=5 (劣)
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 80, unit="M")
        assert r.grade == "劣"
        assert r.grade_code == 5


class TestRuleNotFound:
    def test_unknown_factor_raises(self, engine):
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, DISTRICT, LAND_USE, "不存在的因素XYZ", 10, unit="M")

    def test_unknown_district_raises(self, engine):
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, "不存在的區", LAND_USE, "主要道路寬度", 18, unit="M")

    def test_unknown_land_use_type_raises(self, engine):
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, DISTRICT, "工業用地", "主要道路寬度", 18, unit="M")

    def test_value_out_of_all_bands_never_happens_but_bad_type_raises(self, engine):
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, DISTRICT, LAND_USE, "主要道路寬度", "十八公尺", unit="M")


class TestWrongUnit:
    def test_km_instead_of_m_raises_not_silently_converts(self, engine):
        with pytest.raises(WrongUnitError):
            engine.grade(CITY, DISTRICT, LAND_USE, "主要道路寬度", 18, unit="KM")

    def test_missing_unit_raises(self, engine):
        with pytest.raises(WrongUnitError):
            engine.grade(CITY, DISTRICT, LAND_USE, "主要道路寬度", 18, unit=None)

    def test_percent_field_with_m_unit_raises(self, engine):
        with pytest.raises(WrongUnitError):
            engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 70, unit="M", rule_set="regional")


class TestAdjustmentMatrix:
    def test_diagonal_is_always_zero(self, engine):
        base = engine.grade(CITY, DISTRICT, LAND_USE, "主要道路寬度", 18, unit="M")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "主要道路寬度", 18, unit="M")
        assert engine.adjustment(base, comp) == 0.0

    def test_nontrivial_matrix_lookup_land_depth(self, engine):
        # Golden Case 表4: 比準地深度23m(普通), 比較標的16m(稍劣), 差異率=1.00%
        base = engine.grade(CITY, DISTRICT, LAND_USE, "深度", 23, unit="M")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "深度", 16, unit="M")
        assert base.grade == "普通" and comp.grade == "稍劣"
        assert engine.adjustment(base, comp) == pytest.approx(1.00)

    def test_nontrivial_matrix_lookup_frontage_road_width(self, engine):
        # Golden Case: 比準地18M(稍優), 比較標的6M(稍劣), 差異率=5.00%
        base = engine.grade(CITY, DISTRICT, LAND_USE, "面前道路寬度", 18, unit="M")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "面前道路寬度", 6, unit="M")
        assert engine.adjustment(base, comp) == pytest.approx(5.00)

    def test_nontrivial_matrix_lookup_nuisance_facility(self, engine):
        # Golden Case: 比準地260M(普通), 比較標的80M(劣), 差異率=3.00%
        base = engine.grade(CITY, DISTRICT, LAND_USE, "嫌惡設施之有無", 260, unit="M")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "嫌惡設施之有無", 80, unit="M")
        assert engine.adjustment(base, comp) == pytest.approx(3.00)

    def test_asymmetric_sign_is_negated(self, engine):
        base = engine.grade(CITY, DISTRICT, LAND_USE, "深度", 16, unit="M")   # 稍劣
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "深度", 23, unit="M")   # 普通
        assert engine.adjustment(base, comp) == pytest.approx(-1.00)

    def test_matrix_lookup_different_factor_raises(self, engine):
        base = engine.grade(CITY, DISTRICT, LAND_USE, "深度", 23, unit="M")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, "寬度", 5, unit="M")
        with pytest.raises(RuleNotFoundError):
            engine.adjustment(base, comp)


class TestWithinSegmentSentinel:
    """Regression test locking in the fix for a real bug found while building
    this suite: distance_positive/negative factors whose 優 grade is defined
    as '區段內有' (facility located inside the segment, i.e. not expressed as
    a numeric distance at all) must NOT be reachable by passing an arbitrary
    numeric distance -- only by passing the label itself. Before the fix, any
    numeric input incorrectly matched grade 優 first because the sentinel
    band's bounds were both None."""

    FACTOR = "停車場地之便利程度"

    def test_within_segment_label_gives_excellent(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, "區段內有")
        assert r.grade == "優"
        assert r.grade_code == 1

    def test_numeric_distance_never_matches_the_sentinel_band(self, engine):
        # Golden Case: 120M -> 稍優 (未滿200m), must NOT be graded 優.
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 120, unit="M")
        assert r.grade == "稍優"
        assert r.grade_code == 2

    def test_zero_distance_still_does_not_mean_within_segment(self, engine):
        # 0 is a legitimate (if edge-case) numeric distance and must be
        # graded via the numeric bands (稍優, since 0 < 200), never silently
        # treated as "within segment".
        r = engine.grade(CITY, DISTRICT, LAND_USE, self.FACTOR, 0, unit="M")
        assert r.grade == "稍優"


class TestRuleSetDisambiguation:
    """Regression tests for Phase 4 NO-GO Recovery BLK-01: '建蔽率' and
    '容積率' exist in BOTH the regional and individual evaluation tables
    with DIFFERENT thresholds (regional 建蔽率 優=60%以上 vs individual 建蔽率
    優=80%以上). Before the fix, combining both rule sets into one engine
    silently let the regional threshold win for individual-factor lookups.
    These tests lock in: (1) each rule_set gives its own correct threshold,
    (2) omitting rule_set on a genuinely ambiguous factor is a hard error,
    not a silent guess, (3) non-colliding factors remain fully backward
    compatible without needing rule_set at all."""

    def test_regional_building_coverage_ratio_threshold(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 70, unit="%", rule_set="regional")
        assert r.grade == "優"  # regional: 優=60%以上
        assert r.rule_id.startswith("REG-")

    def test_individual_building_coverage_ratio_threshold(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 70, unit="%", rule_set="individual")
        assert r.grade == "稍優"  # individual: 優=80%以上, 70% falls in 稍優=70-80%
        assert r.rule_id.startswith("IND-")

    def test_regional_floor_area_ratio_threshold(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "容積率", 240, unit="%", rule_set="regional")
        assert r.grade == "優"  # regional: 優=240%以上
        assert r.rule_id.startswith("REG-")

    def test_individual_floor_area_ratio_threshold(self, engine):
        r = engine.grade(CITY, DISTRICT, LAND_USE, "容積率", 240, unit="%", rule_set="individual")
        assert r.grade == "稍優"  # individual: 優=300%以上, 240% falls in 稍優=240-300%
        assert r.rule_id.startswith("IND-")

    def test_ambiguous_factor_without_rule_set_raises(self, engine):
        from rule_engine import AmbiguousFactorError
        with pytest.raises(AmbiguousFactorError):
            engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 70, unit="%")

    def test_non_colliding_factor_still_works_without_rule_set(self, engine):
        """Backward compatibility: '主要道路寬度' only exists in the regional
        table, so omitting rule_set must continue to work exactly as before
        the fix (no test outside this class needed to change)."""
        r = engine.grade(CITY, DISTRICT, LAND_USE, "主要道路寬度", 18, unit="M")
        assert r.grade == "普通"

    def test_wrong_rule_set_value_raises_rule_not_found(self, engine):
        with pytest.raises(RuleNotFoundError):
            engine.grade(CITY, DISTRICT, LAND_USE, "建蔽率", 70, unit="%", rule_set="not_a_real_rule_set")
