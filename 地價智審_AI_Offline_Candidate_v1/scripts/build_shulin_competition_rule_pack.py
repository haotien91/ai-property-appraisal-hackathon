# -*- coding: utf-8 -*-
"""
build_shulin_competition_rule_pack.py — SHULIN-COMPETITION-RULE-PACK-A2 Task 2/4.

Generates the OFFICIAL 樹林住宅 competition rule pack:
  data/rules/competition/shulin_residential_2026/regional_rules.json   (29 factors)
  data/rules/competition/shulin_residential_2026/individual_rules.json (19 standard factors)
  data/rules/competition/shulin_residential_2026/manifest.json

SOURCE OF TRUTH (the ONLY source for every grade/threshold/matrix number
below): data/sources/competition/shulin_residential_2026/評價基準明細表.pdf,
pages 1-5 (regional) and 6-9 (individual), as visually verified cell-by-cell
against 220 DPI page renders in SHULIN-RULE-SOURCE-TRUTH-GATE-A1 (see
docs/phase7/shulin_rule_source_truth_gate_a1.md). This script contains NO
threshold copied from data/rules/regional_rules.json/individual_rules.json
(the legacy Jinshan commercial pack) -- those two files are read by nothing
here and are never modified by this script.

容積率 (floor_area_ratio_individual) is DELIBERATELY ABSENT from
individual_rules.json below -- the source PDF's own matrix cells for this
factor are blank (「詳備註」only: 土地開發分析法試算 + 併同區域因素容積率
考量), so no rule record is fabricated for it. Its policy
(MANUAL_REVIEW_REQUIRED / LAND_DEVELOPMENT_ANALYSIS_REQUIRED) is recorded
only in manifest.json -- see Task 16: a RuleNotFoundError from RuleEngine
for this factor is the CORRECT, un-guessed behavior, never silently
special-cased.

land_depth (IND-LAND_DEPTH) is the ONE non-monotonic factor: grade 普通
(grade_code=3) genuinely covers TWO disjoint ranges (7≤depth<14 OR
40≤depth<50), expressed via the NEW optional `range_segments` field (see
engine/rule_engine.py::RuleEngine._match_numeric() and
engine/rule_table_validator.py's generalized-to-segments checks). All other
non-2-grade factors have exactly ONE lower/upper_bound pair per grade_code,
unaffected by this schema addition.
"""
from __future__ import annotations

import hashlib
import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "data", "rules", "competition", "shulin_residential_2026")
SOURCE_PDF = os.path.join(REPO_ROOT, "data", "sources", "competition", "shulin_residential_2026", "評價基準明細表.pdf")

CITY = "新北市"
DISTRICT = "樹林區"
LAND_USE_TYPE = "普通住宅用地"
EFFECTIVE_DATE = "1110901"
SOURCE_DOCUMENT = "評價基準明細表.pdf"
VERSION = "1.0"
PROFILE_ID = "shulin_residential_2026"


def _linear_matrix(codes, vals):
    """codes: list of grade_code (str) in best-to-worst order. vals: the
    SAME-length list of "distance from best" magnitudes for that row (i.e.
    row 1 of the printed matrix, e.g. [0, 2.5, 5, 7.5, 10]). Valid ONLY for
    exact-decimal intervals (every factor here except other_factors, whose
    7x7 matrix is hardcoded verbatim below instead -- see module docstring
    on why: 3.33/6.67/13.33/16.67 are ROUNDED printed values, and
    subtracting them back out does not reproduce the source exactly)."""
    return {
        codes[i]: {codes[j]: round(vals[j] - vals[i], 4) for j in range(len(codes))}
        for i in range(len(codes))
    }


def _max_abs(matrix):
    return max(abs(v) for row in matrix.values() for v in row.values())


# =============================================================================
# REGIONAL (29 factors) -- 評價基準明細表.pdf p.1-5
# =============================================================================
# Each entry: key, factor_cn, category, value_type, unit, direction, source_page,
# grades=[(code, grade, grade_label, lower, upper, lower_inc, upper_inc), ...],
# matrix (already the FULL dict, codes as str).
REGIONAL = [
    dict(key="ZONING_INSIDE_OUTSIDE", factor_cn="都市計畫內外", category="土地使用管制(1)",
         value_type="boolean", unit=None, direction="positive", source_page="p.1",
         grades=[
             (1, "優", "都市計畫內", None, None, None, None),
             (2, "劣", "都市計畫外", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2"], [0, 20])),
    dict(key="LAND_USE_ZONE", factor_cn="使用分區(使用地類別)", category="土地使用管制(1)",
         value_type="categorical", unit=None, direction="positive", source_page="p.1",
         grades=[
             (1, "優", "商業區、捷運用地(聯開)", None, None, None, None),
             (2, "稍優", "住宅區、市場用地", None, None, None, None),
             (3, "普通", "甲建、乙建、特定專用區、多目標使用之其他公共設施用地", None, None, None, None),
             (4, "稍劣", "工業區、丙建、丁建", None, None, None, None),
             (5, "劣", "其他可建築用地", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 5, 10, 15, 20])),
    dict(key="BUILDING_COVERAGE_RATIO", factor_cn="建蔽率", category="土地使用管制(1)",
         value_type="numeric_range", unit="%", direction="positive", source_page="p.1",
         grades=[
             (1, "優", "80%以上", 80, None, True, None),
             (2, "稍優", "70%以上未滿80%", 70, 80, True, False),
             (3, "普通", "60%以上未滿70%", 60, 70, True, False),
             (4, "稍劣", "50%以上未滿60%", 50, 60, True, False),
             (5, "劣", "未滿50%", None, 50, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="FLOOR_AREA_RATIO", factor_cn="容積率", category="土地使用管制(1)",
         value_type="numeric_range", unit="%", direction="positive", source_page="p.1",
         grades=[
             (1, "優", "460%以上", 460, None, True, None),
             (2, "稍優", "360%以上未滿460%", 360, 460, True, False),
             (3, "普通", "260%以上未滿360%", 260, 360, True, False),
             (4, "稍劣", "180%以上未滿260%", 180, 260, True, False),
             (5, "劣", "未滿180%", None, 180, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 6.25, 12.5, 18.75, 25])),
    dict(key="CONSTRUCTION_PROHIBITED", factor_cn="有無禁止建築", category="土地使用管制(1)",
         value_type="boolean", unit=None, direction="positive", source_page="p.1",
         grades=[
             (1, "優", "無禁止建築", None, None, None, None),
             (2, "劣", "有禁止建築", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2"], [0, 50])),
    dict(key="CONSTRUCTION_RESTRICTED", factor_cn="有無限制建築(整體開發、面積限制、高度限制)", category="土地使用管制(1)",
         value_type="categorical", unit=None, direction="positive", source_page="p.1",
         grades=[
             (1, "優", "無限制建築", None, None, None, None),
             (2, "普通", "部分限制建築(如高度限制或面積限制)", None, None, None, None),
             (3, "劣", "限制整體開發", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3"], [0, 25, 50])),
    dict(key="MAIN_ROAD_WIDTH", factor_cn="主要道路寬度", category="交通運輸(2)",
         value_type="numeric_range", unit="M", direction="positive", source_page="p.2",
         grades=[
             (1, "優", "28m以上", 28, None, True, None),
             (2, "稍優", "20m以上未滿28m", 20, 28, True, False),
             (3, "普通", "12m以上未滿20m", 12, 20, True, False),
             (4, "稍劣", "8m以上未滿12m", 8, 12, True, False),
             (5, "劣", "未滿8m", None, 8, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 3.75, 7.5, 11.25, 15])),
    dict(key="AVG_ROAD_WIDTH", factor_cn="區段內道路平均寬度", category="交通運輸(2)",
         value_type="numeric_range", unit="M", direction="positive", source_page="p.2",
         grades=[
             (1, "優", "20m以上", 20, None, True, None),
             (2, "稍優", "15m以上未滿20m", 15, 20, True, False),
             (3, "普通", "10m以上未滿15m", 10, 15, True, False),
             (4, "稍劣", "8m以上未滿10m", 8, 10, True, False),
             (5, "劣", "未滿8m", None, 8, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 3, 6, 9, 12])),
    dict(key="MAJOR_STATION_PROXIMITY", factor_cn="接近大型車站之程度", category="交通運輸(2)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.2",
         grades=[
             (1, "優", "區段內有大型車站或距離未滿500m", None, 500, None, False),
             (2, "稍優", "500m以上未滿1000m", 500, 1000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "1500m以上未滿2000m", 1500, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="BUS_STOP_PROXIMITY", factor_cn="站牌之接近程度或密集程度", category="交通運輸(2)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.2",
         grades=[
             (1, "優", "區段內有站牌或距離未滿200m", None, 200, None, False),
             (2, "稍優", "200m以上未滿400m", 200, 400, True, False),
             (3, "普通", "400m以上未滿600m", 400, 600, True, False),
             (4, "稍劣", "600m以上未滿800m", 600, 800, True, False),
             (5, "劣", "800m以上或無", 800, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1, 2, 3, 4])),
    dict(key="INTERCHANGE_PROXIMITY", factor_cn="交流道之有無及接近交流道之程度", category="交通運輸(2)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.2",
         grades=[
             (1, "優", "區段內有交流道或距離未滿1000m", None, 1000, None, False),
             (2, "稍優", "1000m以上未滿2000m", 1000, 2000, True, False),
             (3, "普通", "2000m以上未滿3000m", 2000, 3000, True, False),
             (4, "稍劣", "3000m以上未滿4000m", 3000, 4000, True, False),
             (5, "劣", "4000m以上或無", 4000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1, 2, 3, 4])),
    dict(key="ROAD_DEVELOPMENT_LEVEL", factor_cn="區段內道路規劃及闢建程度", category="交通運輸(2)",
         value_type="categorical", unit=None, direction="positive", source_page="p.2",
         grades=[
             (1, "優", "全部規劃及闢建", None, None, None, None),
             (2, "稍優", "大部分規劃及闢建", None, None, None, None),
             (3, "普通", "部分規劃及闢建", None, None, None, None),
             (4, "稍劣", "砂石路", None, None, None, None),
             (5, "劣", "全無規劃及闢建", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="SUNLIGHT", factor_cn="日照", category="自然條件(3)",
         value_type="categorical", unit=None, direction="positive", source_page="p.3",
         grades=[
             (1, "優", "充分", None, None, None, None),
             (2, "稍優", "少許有陰雨", None, None, None, None),
             (3, "普通", "有部分陰雨", None, None, None, None),
             (4, "稍劣", "有相當陰雨", None, None, None, None),
             (5, "劣", "大部分陰雨", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="VIEW", factor_cn="景觀", category="自然條件(3)",
         value_type="categorical", unit=None, direction="positive", source_page="p.3",
         grades=[
             (1, "優", "視野極寬廣、景觀極優美", None, None, None, None),
             (2, "稍優", "視野寬廣、景觀優美", None, None, None, None),
             (3, "普通", "視野、景觀尚可", None, None, None, None),
             (4, "稍劣", "視野、景觀差", None, None, None, None),
             (5, "劣", "視野、景觀極差", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    dict(key="SLOPE", factor_cn="傾斜度", category="自然條件(3)",
         value_type="numeric_range", unit="度", direction="positive", source_page="p.3",
         grades=[
             (1, "優", "平均坡度未滿5度", None, 5, None, False),
             (2, "稍優", "平均坡度5度以上未滿10度", 5, 10, True, False),
             (3, "普通", "平均坡度10度以上未滿15度", 10, 15, True, False),
             (4, "稍劣", "平均坡度15度以上未滿20度", 15, 20, True, False),
             (5, "劣", "平均坡度20度以上", 20, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 3.75, 7.5, 11.25, 15])),
    dict(key="DRAINAGE_QUALITY", factor_cn="排水之良否", category="自然條件(3)",
         value_type="categorical", unit=None, direction="positive", source_page="p.3",
         grades=[
             (1, "優", "極完善", None, None, None, None),
             (2, "稍優", "非常完善", None, None, None, None),
             (3, "普通", "普通完善", None, None, None, None),
             (4, "稍劣", "不良", None, None, None, None),
             (5, "劣", "極不良", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="TERRAIN", factor_cn="地勢", category="自然條件(3)",
         value_type="categorical", unit=None, direction="positive", source_page="p.3",
         grades=[
             (1, "優", "極平坦堅硬", None, None, None, None),
             (2, "稍優", "平坦地", None, None, None, None),
             (3, "普通", "緩傾斜地", None, None, None, None),
             (4, "稍劣", "低地、溼地", None, None, None, None),
             (5, "劣", "地勢孤劣地", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="LAND_IMPROVEMENT", factor_cn="建築基地改良或其他改良", category="土地改良(4)",
         value_type="categorical", unit=None, direction="positive", source_page="p.3",
         grades=[
             (1, "優", "四項以上", None, None, None, None),
             (2, "稍優", "三項", None, None, None, None),
             (3, "普通", "二項", None, None, None, None),
             (4, "稍劣", "一項", None, None, None, None),
             (5, "劣", "無", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="SCHOOL_PROXIMITY", factor_cn="接近學校之程度", category="公共建設(5)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.4",
         grades=[
             (1, "優", "區段內有學校者或距離未滿300m", None, 300, None, False),
             (2, "稍優", "300m以上未滿500m", 300, 500, True, False),
             (3, "普通", "500m以上未滿800m", 500, 800, True, False),
             (4, "稍劣", "800m以上未滿1000m", 800, 1000, True, False),
             (5, "劣", "1000m以上或無", 1000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2, 4, 6, 8])),
    dict(key="MARKET_PROXIMITY", factor_cn="接近市場之程度", category="公共建設(5)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.4",
         grades=[
             (1, "優", "區段內有市場者或距離未滿300m", None, 300, None, False),
             (2, "稍優", "300m以上未滿500m", 300, 500, True, False),
             (3, "普通", "500m以上未滿800m", 500, 800, True, False),
             (4, "稍劣", "800m以上未滿1000m", 800, 1000, True, False),
             (5, "劣", "1000m以上或無", 1000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2, 4, 6, 8])),
    dict(key="PARK_PROXIMITY", factor_cn="接近公園(里鄰公園、一般公園)、廣場、徒步區之程度", category="公共建設(5)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.4",
         grades=[
             (1, "優", "區段內有公園者或距離未滿300m", None, 300, None, False),
             (2, "稍優", "300m以上未滿500m", 300, 500, True, False),
             (3, "普通", "500m以上未滿800m", 500, 800, True, False),
             (4, "稍劣", "800m以上未滿1000m", 800, 1000, True, False),
             (5, "劣", "1000m以上或無", 1000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2, 4, 6, 8])),
    dict(key="TOURISM_FACILITY_PROXIMITY", factor_cn="接近觀光遊憩設施之程度", category="公共建設(5)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.4",
         grades=[
             (1, "優", "區段內有或距離未滿500m", None, 500, None, False),
             (2, "稍優", "500m以上未滿1000m", 500, 1000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "1500m以上未滿2000m", 1500, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.5, 3, 4.5, 6])),
    dict(key="PARKING_CONVENIENCE", factor_cn="停車場地之便利程度", category="公共建設(5)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.4",
         grades=[
             (1, "優", "區段內有停車位者或距離未滿200m", None, 200, None, False),
             (2, "稍優", "200m以上未滿400m", 200, 400, True, False),
             (3, "普通", "400m以上未滿600m", 400, 600, True, False),
             (4, "稍劣", "600m以上未滿1000m", 600, 1000, True, False),
             (5, "劣", "1000m以上或無", 1000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.5, 3, 4.5, 6])),
    dict(key="SERVICE_FACILITY_PROXIMITY", factor_cn="接近服務性設施的程度(郵局、銀行、醫院、機關等設施)", category="公共建設(5)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.4",
         grades=[
             (1, "優", "區段內有或距離未滿500m", None, 500, None, False),
             (2, "稍優", "500m以上未滿1000m", 500, 1000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "1500m以上未滿2000m", 1500, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.5, 3, 4.5, 6])),
    dict(key="UTILITY_FACILITY_PROXIMITY", factor_cn="變電所或高壓鐵塔、瓦斯槽之有無及接近程度", category="特殊設施(6)",
         value_type="distance_negative", unit="M", direction="positive_distance_better", source_page="p.5",
         grades=[
             (1, "優", "2000m以上或無", 2000, None, True, None),
             (2, "稍優", "1500m以上未滿2000m", 1500, 2000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "500m以上未滿1000m", 500, 1000, True, False),
             (5, "劣", "區段內有或距離未滿500m", None, 500, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="FUNERAL_FACILITY_PROXIMITY", factor_cn="墓地、殯儀館、火葬場之有無及接近程度", category="特殊設施(6)",
         value_type="distance_negative", unit="M", direction="positive_distance_better", source_page="p.5",
         grades=[
             (1, "優", "2000m以上或無", 2000, None, True, None),
             (2, "稍優", "1500m以上未滿2000m", 1500, 2000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "500m以上未滿1000m", 500, 1000, True, False),
             (5, "劣", "區段內有或距離未滿500m", None, 500, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="WASTE_FACILITY_PROXIMITY", factor_cn="垃圾場或掩埋場、焚化爐之有無及接近程度", category="特殊設施(6)",
         value_type="distance_negative", unit="M", direction="positive_distance_better", source_page="p.5",
         grades=[
             (1, "優", "2000m以上或無", 2000, None, True, None),
             (2, "稍優", "1500m以上未滿2000m", 1500, 2000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "500m以上未滿1000m", 500, 1000, True, False),
             (5, "劣", "區段內有或距離未滿500m", None, 500, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 3.75, 7.5, 11.25, 15])),
    dict(key="POLLUTION_PROXIMITY", factor_cn="環境污染(水污染、噪音污染、廢氣污染、廢棄物污染等)之有無及接近程度", category="環境污染(7)",
         value_type="distance_negative", unit="M", direction="positive_distance_better", source_page="p.5",
         grades=[
             (1, "優", "2000m以上或無", 2000, None, True, None),
             (2, "稍優", "1500m以上未滿2000m", 1500, 2000, True, False),
             (3, "普通", "1000m以上未滿1500m", 1000, 1500, True, False),
             (4, "稍劣", "500m以上未滿1000m", 500, 1000, True, False),
             (5, "劣", "區段內有或距離未滿500m", None, 500, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 5, 10, 15, 20])),
    dict(key="OTHER_FACTORS", factor_cn="其他影響因素", category="其他影響因素(8)",
         value_type="categorical", unit=None, direction="positive", source_page="p.5",
         grades=[
             (1, "極優", "其他影響因素極優", None, None, None, None),
             (2, "優", "其他影響因素優", None, None, None, None),
             (3, "稍優", "其他影響因素稍優", None, None, None, None),
             (4, "普通", "其他影響因素普通", None, None, None, None),
             (5, "稍劣", "其他影響因素稍差", None, None, None, None),
             (6, "劣", "其他影響因素差", None, None, None, None),
             (7, "極劣", "其他影響因素極差", None, None, None, None),
         ],
         # Hardcoded verbatim from the printed 7x7 (NOT derived via
         # subtraction from rounded 3.33/6.67/13.33/16.67 -- see module
         # docstring: doing so would NOT reproduce the source exactly,
         # e.g. 6.67-3.33=3.34 != the printed 3.33).
         matrix={
             "1": {"1": 0, "2": 3.33, "3": 6.67, "4": 10, "5": 13.33, "6": 16.67, "7": 20},
             "2": {"1": -3.33, "2": 0, "3": 3.33, "4": 6.67, "5": 10, "6": 13.33, "7": 16.67},
             "3": {"1": -6.67, "2": -3.33, "3": 0, "4": 3.33, "5": 6.67, "6": 10, "7": 13.33},
             "4": {"1": -10, "2": -6.67, "3": -3.33, "4": 0, "5": 3.33, "6": 6.67, "7": 10},
             "5": {"1": -13.33, "2": -10, "3": -6.67, "4": -3.33, "5": 0, "6": 3.33, "7": 6.67},
             "6": {"1": -16.67, "2": -13.33, "3": -10, "4": -6.67, "5": -3.33, "6": 0, "7": 3.33},
             "7": {"1": -20, "2": -16.67, "3": -13.33, "4": -10, "5": -6.67, "6": -3.33, "7": 0},
         }),
]

assert len(REGIONAL) == 29, f"expected 29 regional factors, got {len(REGIONAL)}"

# =============================================================================
# INDIVIDUAL (19 standard-matrix factors) -- 評價基準明細表.pdf p.6-9
# 容積率 (floor_area_ratio_individual) intentionally NOT included -- see
# module docstring and manifest.json's calculation_policy.
# =============================================================================
INDIVIDUAL = [
    dict(key="LAND_AREA", factor_cn="面積", category="宗地條件(1)",
         value_type="numeric_range", unit="M2", direction="positive", source_page="p.6",
         grades=[
             (1, "優", "600m2以上", 600, None, True, None),
             (2, "稍優", "400m2以上未滿600m2", 400, 600, True, False),
             (3, "普通", "200m2以上未滿400m2", 200, 400, True, False),
             (4, "稍劣", "50m2以上未滿200m2", 50, 200, True, False),
             (5, "劣", "50m2以下", None, 50, None, True),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="LAND_WIDTH", factor_cn="寬度", category="宗地條件(1)",
         value_type="numeric_range", unit="M", direction="positive", source_page="p.6",
         grades=[
             (1, "優", "20m以上", 20, None, True, None),
             (2, "稍優", "15m以上未滿20m", 15, 20, True, False),
             (3, "普通", "8m以上未滿15m", 8, 15, True, False),
             (4, "稍劣", "4m以上未滿8m", 4, 8, True, False),
             (5, "劣", "未滿4m", None, 4, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    # --- NON-MONOTONIC: land_depth (Task 5/6) ---
    dict(key="LAND_DEPTH", factor_cn="深度", category="宗地條件(1)",
         value_type="numeric_range", unit="M", direction="positive", source_page="p.6",
         grades=[
             # (code, grade, grade_label, lower, upper, lower_inc, upper_inc, range_segments)
             (1, "優", "14m以上未滿30m", 14, 30, True, False, None),
             (2, "稍優", "30m以上未滿40m", 30, 40, True, False, None),
             (3, "普通", "7m以上未滿14m 或 40m以上未滿50m", None, None, None, None, [
                 {"lower_bound": 7, "lower_inclusive": True, "upper_bound": 14, "upper_inclusive": False},
                 {"lower_bound": 40, "lower_inclusive": True, "upper_bound": 50, "upper_inclusive": False},
             ]),
             (4, "稍劣", "50m以上未滿60m", 50, 60, True, False, None),
             (5, "劣", "未滿7m 或 60m以上", None, None, None, None, [
                 {"lower_bound": None, "lower_inclusive": None, "upper_bound": 7, "upper_inclusive": False},
                 {"lower_bound": 60, "lower_inclusive": True, "upper_bound": None, "upper_inclusive": None},
             ]),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5]),
         has_range_segments=True),
    dict(key="LAND_SHAPE", factor_cn="形狀", category="宗地條件(1)",
         value_type="boolean", unit=None, direction="positive", source_page="p.6",
         grades=[
             (1, "優", "方形、梯形", None, None, None, None),
             (2, "劣", "不規則形、長條形", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2"], [0, 5])),
    dict(key="STREET_FRONTAGE", factor_cn="臨路情形", category="宗地條件(1)",
         value_type="categorical", unit=None, direction="positive", source_page="p.6",
         grades=[
             (1, "優", "3面以上臨街", None, None, None, None),
             (2, "稍優", "路角地", None, None, None, None),
             (3, "普通", "雙面臨街", None, None, None, None),
             (4, "稍劣", "單面臨街", None, None, None, None),
             (5, "劣", "未臨街地", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="LAND_TERRAIN_INDIVIDUAL", factor_cn="地勢", category="宗地條件(1)",
         value_type="boolean", unit=None, direction="positive", source_page="p.6",
         grades=[
             (1, "優", "平坦", None, None, None, None),
             (2, "劣", "高亢或低窪", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2"], [0, 10])),
    dict(key="ROAD_TYPE", factor_cn="道路種類", category="道路條件(2)",
         value_type="categorical", unit=None, direction="positive", source_page="p.7",
         grades=[
             (1, "優", "主要道路", None, None, None, None),
             (2, "稍優", "次要道路", None, None, None, None),
             (3, "普通", "巷道", None, None, None, None),
             (4, "稍劣", "農路", None, None, None, None),
             (5, "劣", "無", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    dict(key="FRONTAGE_ROAD_WIDTH", factor_cn="面前道路寬度", category="道路條件(2)",
         value_type="numeric_range", unit="M", direction="positive", source_page="p.7",
         grades=[
             (1, "優", "20m以上", 20, None, True, None),
             (2, "稍優", "12m以上未滿20m", 12, 20, True, False),
             (3, "普通", "8m以上未滿12m", 8, 12, True, False),
             (4, "稍劣", "5m以上未滿8m", 5, 8, True, False),
             (5, "劣", "未滿5m或無", None, 5, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 3, 6, 9, 12])),
    dict(key="SCHOOL_PROXIMITY_INDIVIDUAL", factor_cn="接近學校程度", category="接近條件(3)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.7",
         grades=[
             (1, "優", "未滿250m", None, 250, None, False),
             (2, "稍優", "250m以上未滿500m", 250, 500, True, False),
             (3, "普通", "500m以上未滿1000m", 500, 1000, True, False),
             (4, "稍劣", "1000m以上未滿2000m", 1000, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    dict(key="MARKET_PROXIMITY_INDIVIDUAL", factor_cn="接近市場程度", category="接近條件(3)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.7",
         grades=[
             (1, "優", "未滿250m", None, 250, None, False),
             (2, "稍優", "250m以上未滿500m", 250, 500, True, False),
             (3, "普通", "500m以上未滿1000m", 500, 1000, True, False),
             (4, "稍劣", "1000m以上未滿2000m", 1000, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    dict(key="PARK_PROXIMITY_INDIVIDUAL", factor_cn="接近公園、廣場程度", category="接近條件(3)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.7",
         grades=[
             (1, "優", "未滿250m", None, 250, None, False),
             (2, "稍優", "250m以上未滿500m", 250, 500, True, False),
             (3, "普通", "500m以上未滿1000m", 500, 1000, True, False),
             (4, "稍劣", "1000m以上未滿2000m", 1000, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    dict(key="STATION_PROXIMITY_INDIVIDUAL", factor_cn="接近車站程度", category="接近條件(3)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.7",
         grades=[
             (1, "優", "未滿250m", None, 250, None, False),
             (2, "稍優", "250m以上未滿500m", 250, 500, True, False),
             (3, "普通", "500m以上未滿1000m", 500, 1000, True, False),
             (4, "稍劣", "1000m以上未滿2000m", 1000, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 1.25, 2.5, 3.75, 5])),
    dict(key="COMMERCIAL_DISTRICT_PROXIMITY", factor_cn="接近商圈程度", category="接近條件(3)",
         value_type="distance_positive", unit="M", direction="negative_distance_better", source_page="p.8",
         grades=[
             (1, "優", "未滿250m", None, 250, None, False),
             (2, "稍優", "250m以上未滿500m", 250, 500, True, False),
             (3, "普通", "500m以上未滿1000m", 500, 1000, True, False),
             (4, "稍劣", "1000m以上未滿2000m", 1000, 2000, True, False),
             (5, "劣", "2000m以上或無", 2000, None, True, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="NUISANCE_FACILITY", factor_cn="嫌惡設施之有無", category="周邊環境條件(4)",
         value_type="distance_negative", unit="M", direction="positive_distance_better", source_page="p.8",
         grades=[
             (1, "優", "2000m以上或無", 2000, None, True, None),
             (2, "稍優", "1000m以上未滿2000m", 1000, 2000, True, False),
             (3, "普通", "500m以上未滿1000m", 500, 1000, True, False),
             (4, "稍劣", "200m以上未滿500m", 200, 500, True, False),
             (5, "劣", "未滿200m", None, 200, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2, 4, 6, 8])),
    dict(key="PARKING_CONVENIENCE_INDIVIDUAL", factor_cn="停車方便性", category="周邊環境條件(4)",
         value_type="categorical", unit=None, direction="positive", source_page="p.8",
         grades=[
             (1, "優", "停車方便性優", None, None, None, None),
             (2, "普通", "停車方便性普通", None, None, None, None),
             (3, "劣", "停車方便性劣", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3"], [0, 2.5, 5])),
    dict(key="ZONING_DESIGNATION", factor_cn="使用分區或編定", category="行政條件(5)",
         value_type="categorical", unit=None, direction="positive", source_page="p.8",
         grades=[
             (1, "優", "商業區、捷運用地(聯開)", None, None, None, None),
             (2, "稍優", "住宅區、市場用地", None, None, None, None),
             (3, "普通", "甲建、乙建、特定專用區、多目標使用之其他公共設施用地", None, None, None, None),
             (4, "稍劣", "工業區、丙建、丁建", None, None, None, None),
             (5, "劣", "其他可建築用地", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 3.75, 7.5, 11.25, 15])),
    dict(key="BUILDING_COVERAGE_RATIO_INDIVIDUAL", factor_cn="建蔽率", category="行政條件(5)",
         value_type="numeric_range", unit="%", direction="positive", source_page="p.8",
         grades=[
             (1, "優", "80%以上", 80, None, True, None),
             (2, "稍優", "70%以上未滿80%", 70, 80, True, False),
             (3, "普通", "60%以上未滿70%", 60, 70, True, False),
             (4, "稍劣", "50%以上未滿60%", 50, 60, True, False),
             (5, "劣", "未滿50%", None, 50, None, False),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 2.5, 5, 7.5, 10])),
    dict(key="CONSTRUCTION_RESTRICTION_INDIVIDUAL", factor_cn="有無禁限建", category="行政條件(5)",
         value_type="categorical", unit=None, direction="positive", source_page="p.9",
         grades=[
             (1, "優", "無禁止或限制建築", None, None, None, None),
             (2, "稍優", "限制建築高度", None, None, None, None),
             (3, "普通", "限制建築高度及面積", None, None, None, None),
             (4, "稍劣", "限制整體開發", None, None, None, None),
             (5, "劣", "禁止建築", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2", "3", "4", "5"], [0, 12.5, 25, 37.5, 50])),
    dict(key="DEAD_END_ALLEY", factor_cn="無尾巷", category="其他(6)",
         value_type="boolean", unit=None, direction="positive", source_page="p.9",
         grades=[
             (1, "優", "無", None, None, None, None),
             (2, "劣", "無尾巷", None, None, None, None),
         ],
         matrix=_linear_matrix(["1", "2"], [0, 5])),
]

assert len(INDIVIDUAL) == 19, f"expected 19 standard individual factors, got {len(INDIVIDUAL)}"


def _build_records(factors, prefix, note_prefix):
    records = []
    for f in factors:
        matrix = f["matrix"]
        max_adj = _max_abs(matrix)
        for g in f["grades"]:
            if len(g) == 8:
                code, grade, grade_label, lower, upper, lower_inc, upper_inc, segments = g
            else:
                code, grade, grade_label, lower, upper, lower_inc, upper_inc = g
                segments = None
            rec = {
                "rule_id": f"{prefix}-{f['key']}-{code:02d}",
                "version": VERSION,
                "city": CITY,
                "district": DISTRICT,
                "land_use_type": LAND_USE_TYPE,
                "category": f["category"],
                "factor": f["factor_cn"],
                "value_type": f["value_type"],
                "unit": f["unit"],
                "direction": f["direction"],
                "lower_bound": lower,
                "upper_bound": upper,
                "lower_inclusive": lower_inc,
                "upper_inclusive": upper_inc,
                "grade": grade,
                "grade_code": code,
                "grade_label": grade_label,
                "adjustment_matrix": matrix,
                "max_adjustment": max_adj,
                "effective_date": EFFECTIVE_DATE,
                "source_document": SOURCE_DOCUMENT,
                "source_page": f["source_page"],
                "source_note": f"{note_prefix}－{f['factor_cn']}：依評價基準明細表{f['source_page']}原文逐格視覺核對建立，非沿用金山商業規則。",
                "anomaly_flag": None,
            }
            if segments is not None:
                rec["range_segments"] = segments
            records.append(rec)
    return records


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    regional_records = _build_records(REGIONAL, "REG", "樹林住宅區域因素")
    individual_records = _build_records(INDIVIDUAL, "IND", "樹林住宅個別因素")

    with open(os.path.join(OUT_DIR, "regional_rules.json"), "w", encoding="utf-8") as f:
        json.dump({"rules": regional_records}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "individual_rules.json"), "w", encoding="utf-8") as f:
        json.dump({"rules": individual_records}, f, ensure_ascii=False, indent=1)

    manifest = {
        "profile_id": PROFILE_ID,
        "city": CITY,
        "district": DISTRICT,
        "land_use_type": LAND_USE_TYPE,
        "source_document": SOURCE_DOCUMENT,
        "source_sha256": _sha256(SOURCE_PDF),
        "effective_date": EFFECTIVE_DATE,
        "version": VERSION,
        "regional_factor_count": len(REGIONAL),
        "individual_standard_factor_count": len(INDIVIDUAL),
        "regional_rule_record_count": len(regional_records),
        "individual_rule_record_count": len(individual_records),
        "other_factors_7grade": {
            "included_in_pack": True,
            "grade_count": 7,
            "competition_case_auto_graded": False,
            "note": "本案表5-1「其他影響因素」百分比小計已由主辦單位固定為「－ 無 0.00」，"
                    "本7級矩陣僅供schema完整性/未來其他案件使用，本案運算路徑不得對它產生非零判斷。",
        },
        "special_policies": [
            {
                "factor": "floor_area_ratio_individual",
                "factor_cn": "容積率(個別因素)",
                "calculation_policy": "MANUAL_REVIEW_REQUIRED",
                "reason": "LAND_DEVELOPMENT_ANALYSIS_REQUIRED",
                "source_page": "p.8",
                "note": "評價基準明細表原文：「1.容積率差異以土地開發分析法進行試算調整 "
                        "2.本項需與區域因素容積率併同考量，調整不足者另於區域因素補充調整」。"
                        "本規則包無此factor之rule record（故意留白，非缺漏）——任何嘗試以"
                        "rule_set='individual'對factor='容積率'呼叫GradeEngine都會得到"
                        "RuleNotFoundError，呼叫端必須將其導向MANUAL_REVIEW_REQUIRED，"
                        "不得改用regional容積率矩陣、不得預設0、不得自行發明公式。",
            },
        ],
        "ambiguous_subtype_scope": [
            {"parent_factor": "funeral_facility_proximity", "subtype": "納骨塔",
             "scope": "RULE_SCOPE_AMBIGUOUS",
             "note": "評價基準明細表原文僅列舉「墓地、殯儀館、火葬場」，未提及納骨塔；"
                     "Table3表單結構將納骨塔與其他三者並列於同一殯葬群組。不得自動歸入"
                     "本factor產生修正率，final grading須MANUAL_REVIEW_REQUIRED；"
                     "candidate/evidence collection仍可進行。"},
            {"parent_factor": "waste_facility_proximity", "subtype": "污水處理場",
             "scope": "RULE_SCOPE_AMBIGUOUS",
             "note": "評價基準明細表原文僅列舉「垃圾場或掩埋場、焚化爐」，未提及污水處理場；"
                     "Table3表單結構將污水處理場與其他二者並列於同一廢棄物處理群組。不得自動"
                     "歸入本factor產生修正率，final grading須MANUAL_REVIEW_REQUIRED；"
                     "candidate/evidence collection仍可進行。"},
            {"parent_factor": "major_station_proximity",
             "subtypes": ["高鐵站", "火車站", "客運站", "捷運站"],
             "scope": "INCLUDED_BY_PARENT_RULE_AND_FORM_STRUCTURE",
             "note": "評價基準明細表原文使用「大型車站」整體類別名稱，未逐一列舉子類型"
                     "（與納骨塔/污水處理場「明確列出部分子項卻缺一項」的情形不同，此處"
                     "是完全未枚舉、以parent category概括）。不代表每個subtype都由評價基準"
                     "逐字明列，但涵蓋全部4個子項，不影響parent rule evaluation。"},
        ],
        "non_monotonic_factors": [
            {"factor": "land_depth", "rule_set": "individual", "grade_code_with_multiple_segments": 3,
             "grade": "普通", "segments": [[7, 14], [40, 50]],
             "note": "見individual_rules.json中IND-LAND_DEPTH-03之range_segments欄位。"},
        ],
        "generated_by": "scripts/build_shulin_competition_rule_pack.py",
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print(f"wrote {len(regional_records)} regional rule records ({len(REGIONAL)} factors)")
    print(f"wrote {len(individual_records)} individual rule records ({len(INDIVIDUAL)} factors)")
    print(f"manifest source_sha256={manifest['source_sha256']}")


if __name__ == "__main__":
    main()
