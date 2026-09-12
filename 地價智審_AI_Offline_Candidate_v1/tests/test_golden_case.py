# -*- coding: utf-8 -*-
"""
Golden Source validation: 案號 1140901-99-001（新北市金山區 P002-00）。
Expected values are re-derived directly from 查估書表範本.pdf (表1/表5-2/表4) and
評價基準明細表範例.pdf in THIS phase (via visual page inspection, not reused from
memory), per task instruction "Expected Value 必須重新從 Source 取得".
"""
import pytest
from conftest import CITY, DISTRICT, LAND_USE


# Non-trivial individual-factor cases (比準地 != 比較標的), each independently
# re-read from 查估書表範本.pdf 表4 (page 3) and cross-checked against
# 評價基準明細表範例.pdf 頁6-9 grade band definitions.
GOLDEN_INDIVIDUAL_CASES = [
    # (factor, base_value, comparable_value, unit, expected_base_grade,
    #  expected_comp_grade, expected_differential_rate)
    ("深度", 23, 16, "M", "普通", "稍劣", 1.00),
    ("道路種類", "主要道路", "次要道路", None, "優", "稍優", 2.00),
    ("面前道路寬度", 18, 6, "M", "稍優", "稍劣", 5.00),
    ("嫌惡設施之有無", 260, 80, "M", "普通", "劣", 3.00),
    ("停車方便性", "可路邊停車", "不可路邊停車", None, "優", "劣", 2.00),
]

# Trivial (tied-grade, differential=0) individual-factor cases, included for
# completeness even though they cannot disambiguate matrix orientation.
GOLDEN_INDIVIDUAL_TIED_CASES = [
    ("面積", 113.21, 111.85, "M2", 0.00),
    ("寬度", 5, 7, "M", 0.00),
    ("形狀", "方形", "方形", None, 0.00),
    ("臨路情形", "單面臨街", "單面臨街", None, 0.00),
    ("地勢", "平坦", "平坦", None, 0.00),
    ("接近學校之程度", 150, 100, "M", 0.00),
    ("接近市場之程度", 30, 92, "M", 0.00),
    ("接近公園、廣場之程度", 190, 200, "M", 0.00),
    ("接近車站之程度", 80, 190, "M", 0.00),
    ("接近商圈之程度", 0, 0, "M", 0.00),
    ("使用分區或編定", "商業區", "商業區", None, 0.00),
    ("建蔽率", 70, 70, "%", 0.00),
    ("容積率", 240, 240, "%", 0.00),
    ("有無禁限建", "無禁止或限制建築", "無禁止或限制建築", None, 0.00),
]

# Golden Case individual factor sum: expect 13.00% (see calculation_dependency.md Phase 2)
EXPECTED_INDIVIDUAL_TOTAL = 13.00


class TestGoldenCaseIndividualNonTrivial:
    @pytest.mark.parametrize(
        "factor,base_val,comp_val,unit,exp_base_grade,exp_comp_grade,exp_rate",
        GOLDEN_INDIVIDUAL_CASES,
    )
    def test_case(self, engine, factor, base_val, comp_val, unit,
                   exp_base_grade, exp_comp_grade, exp_rate):
        base = engine.grade(CITY, DISTRICT, LAND_USE, factor, base_val, unit=unit, rule_set="individual")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, factor, comp_val, unit=unit, rule_set="individual")
        assert base.grade == exp_base_grade, f"{factor}: base grade mismatch"
        assert comp.grade == exp_comp_grade, f"{factor}: comparable grade mismatch"
        rate = engine.adjustment(base, comp)
        assert rate == pytest.approx(exp_rate), f"{factor}: adjustment rate mismatch"


class TestGoldenCaseIndividualTied:
    @pytest.mark.parametrize("factor,base_val,comp_val,unit,exp_rate", GOLDEN_INDIVIDUAL_TIED_CASES)
    def test_tied_case(self, engine, factor, base_val, comp_val, unit, exp_rate):
        base = engine.grade(CITY, DISTRICT, LAND_USE, factor, base_val, unit=unit, rule_set="individual")
        comp = engine.grade(CITY, DISTRICT, LAND_USE, factor, comp_val, unit=unit, rule_set="individual")
        assert base.grade == comp.grade
        rate = engine.adjustment(base, comp)
        assert rate == pytest.approx(exp_rate)


class TestGoldenCaseIndividualTotal:
    def test_sum_of_all_differentials_equals_13_percent(self, engine):
        total = 0.0
        for factor, base_val, comp_val, unit, _, _, exp_rate in GOLDEN_INDIVIDUAL_CASES:
            base = engine.grade(CITY, DISTRICT, LAND_USE, factor, base_val, unit=unit, rule_set="individual")
            comp = engine.grade(CITY, DISTRICT, LAND_USE, factor, comp_val, unit=unit, rule_set="individual")
            total += engine.adjustment(base, comp)
        for factor, base_val, comp_val, unit, _ in GOLDEN_INDIVIDUAL_TIED_CASES:
            base = engine.grade(CITY, DISTRICT, LAND_USE, factor, base_val, unit=unit, rule_set="individual")
            comp = engine.grade(CITY, DISTRICT, LAND_USE, factor, comp_val, unit=unit, rule_set="individual")
            total += engine.adjustment(base, comp)
        assert total == pytest.approx(EXPECTED_INDIVIDUAL_TOTAL), (
            f"Sum of all individual differential rates should equal 13.00% "
            f"(表4 golden case '合計' field), got {total}"
        )


# Regional factors: Golden Case is degenerate (比準地 and 比較標的1 share the same
# region P002-00, so every regional factor has identical grades on both sides and
# differential=0.00%). This CANNOT verify matrix orientation for the regional
# table (see docs/phase3/source_anomalies.md ANOMALY-05) but DOES verify that
# the grade-band boundaries themselves are correctly encoded, re-read directly
# from 查估書表範本.pdf 表1 (page 1) and 評價基準明細表範例.pdf 頁1-5.
GOLDEN_REGIONAL_GRADE_CASES = [
    # (factor, raw_value, unit, expected_grade_code)
    ("都市計畫（內、外）", "都市計畫內", None, 1),
    ("使用分區(使用地類別)", "第二種商業區", None, 1),
    ("建蔽率", 70, "%", 1),
    ("容積率", 240, "%", 1),
    ("有無禁止建築", "無", None, 1),
    ("有無限制建築（整體開發、面積限制、高度限制……等）", "無", None, 1),
    ("主要道路寬度", 18, "M", 3),
    ("區段內道路平均寬度", 12, "M", 3),
    ("排水之良否", "有排水系統不易淹水", None, 2),
    ("地勢", "該區地勢平坦", None, 1),
    ("停車場地之便利程度", 120, "M", 2),
]


class TestGoldenCaseRegionalGradeBands:
    @pytest.mark.parametrize("factor,value,unit,expected_code", GOLDEN_REGIONAL_GRADE_CASES)
    def test_regional_grade(self, engine, factor, value, unit, expected_code):
        r = engine.grade(CITY, DISTRICT, LAND_USE, factor, value, unit=unit, rule_set="regional")
        assert r.grade_code == expected_code, (
            f"{factor}={value}: expected grade_code {expected_code}, got {r.grade_code} "
            f"(re-verified against 表5-2 Golden Case p.2 and 評價基準明細表範例.pdf)"
        )

    def test_regional_total_adjustment_is_zero_for_same_segment_comparison(self, engine, regional_rules):
        """Golden Case's 比準地 and 比較標的1 are both in segment P002-00 -> every
        regional factor differential must be 0.00%, summing to regional_total=0.00%
        (see 表5-2 and 表4 both showing 0.00% for region adjustment)."""
        total = 0.0
        for factor, value, unit, _ in GOLDEN_REGIONAL_GRADE_CASES:
            base = engine.grade(CITY, DISTRICT, LAND_USE, factor, value, unit=unit, rule_set="regional")
            comp = engine.grade(CITY, DISTRICT, LAND_USE, factor, value, unit=unit, rule_set="regional")
            total += engine.adjustment(base, comp)
        assert total == pytest.approx(0.00)
