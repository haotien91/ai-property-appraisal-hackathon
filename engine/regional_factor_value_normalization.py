# -*- coding: utf-8 -*-
"""
regional_factor_value_normalization.py — TABLE51-THREE-COMPARABLE-C1-
FINAL-GATE-1 Task 1.

Some regional factors' RAW competition-provided value (exactly as 題目.pdf
prints it -- e.g. 使用分區(使用地類別)="第一種住宅區") is not itself the
categorical BAND LABEL data/rules/competition/shulin_residential_2026/
regional_rules.json's grade_label expects (e.g. "住宅區、市場用地"). This
module maps raw_value -> evaluation_value for grading purposes WITHOUT
ever mutating raw_value -- callers (engine/table51_analysis_engine.py)
store both, separately, plus normalization_reason/mapping_source.

Every mapping here is sourced from docs/phase7/
shulin_rule_source_truth_gate_a1.md's own visually-verified reading of
評價基準明細表.pdf (the RULE_SOURCE_OF_TRUTH document) -- NOT invented in
this module:

  使用分區(使用地類別) 的 5 個級距是「分組描述」，不是逐一列舉每個實際
  分區名稱：優=商業區/捷運用地(聯開)，稍優=住宅區/市場用地，普通=甲建/
  乙建/特定專用區/多目標使用之其他公共設施用地，稍劣=工業區/丙建/丁建，
  劣=其他可建築用地。"第一種住宅區"/"第二種住宅區"/"第三種住宅區" 等
  都市計畫法定分區名稱，依其「住宅區」性質歸類為「住宅區、市場用地」級距
  ——這是 that document 對這個因素既有的分級方式，不是本模組另行發明的
  對照表。

  有無禁止建築 / 有無限制建築 的級距本身就是"無禁止建築"/"有禁止建築"、
  "無限制建築"/"部分限制建築(...)"/"限制整體開發" 這樣的完整用語，題目.pdf
  的勾選只記錄"無"/"有" 二元狀態，需要組回完整級距用語才能比對。

  建築基地改良或其他改良 的級距是依「勾選項目數」分級（四項以上/三項/
  二項/一項/無），題目.pdf 記錄的是實際勾選了哪些項目（整平或填挖基地/
  開挖水溝/水土保持/鋪築道路/埋設管道/修築駁嵌/其他），需要先數出勾選
  項目數才能對應級距。
"""
from __future__ import annotations

from typing import Optional, Tuple, Union

MAPPING_SOURCE = "評價基準明細表.pdf（見 docs/phase7/shulin_rule_source_truth_gate_a1.md 已驗證之分類對照）"

# 使用分區(使用地類別)：都市計畫法定分區名稱 -> 評價基準明細表既有級距分組。
# 目前僅收錄本專案實際遇過的分區名稱 -- 遇到未收錄的分區名稱時，
# normalize_regional_factor_value() 回傳 (None, None, None)，該因素維持
# MANUAL_REVIEW_REQUIRED（絕不猜測），而非靜默假設某個級距。
_LAND_USE_ZONE_BAND_MAP = {
    "第一種住宅區": "住宅區、市場用地",
    "第二種住宅區": "住宅區、市場用地",
    "第三種住宅區": "住宅區、市場用地",
    "住宅區": "住宅區、市場用地",
    "市場用地": "住宅區、市場用地",
    "第一種商業區": "商業區、捷運用地(聯開)",
    "第二種商業區": "商業區、捷運用地(聯開)",
    "第三種商業區": "商業區、捷運用地(聯開)",
    "商業區": "商業區、捷運用地(聯開)",
    "捷運用地": "商業區、捷運用地(聯開)",
    "工業區": "工業區、丙建、丁建",
    "丙種建築用地": "工業區、丙建、丁建",
    "丁種建築用地": "工業區、丙建、丁建",
}

_BOOLEAN_PRESENCE_BAND_MAP = {
    "regional_construction_prohibited": {"無": "無禁止建築", "有": "有禁止建築"},
    "regional_construction_restricted": {"無": "無限制建築"},
}

_LAND_IMPROVEMENT_COUNT_BANDS = ((4, "四項以上"), (3, "三項"), (2, "二項"), (1, "一項"), (0, "無"))
_LAND_IMPROVEMENT_BAND_LABELS = {label for _, label in _LAND_IMPROVEMENT_COUNT_BANDS}


def _count_land_improvement_items(raw_value) -> int:
    if not isinstance(raw_value, str) or raw_value.strip() in ("", "無"):
        return 0
    return len([p for p in raw_value.split("、") if p.strip()])


def normalize_regional_factor_value(
    field_id: str, raw_value: Union[float, int, str, bool, None],
) -> Tuple[Optional[Union[float, int, str, bool]], Optional[str], Optional[str]]:
    """Returns (evaluation_value, normalization_reason, mapping_source).
    All three are None when this field_id/raw_value needs no mapping at
    all (every numeric factor, and any categorical/boolean factor whose
    raw text already matches a rule-pack band verbatim) -- callers must
    treat a None evaluation_value as "use raw_value as-is for grading",
    never as a failure."""
    # TABLE4-THREE-COMPARABLE-D1: individual_zoning_designation (使用分區
    #或編定, IND-ZONING_DESIGNATION) uses the IDENTICAL 5-band grouping as
    # regional_land_use_zone (verified: both list "商業區、捷運用地(聯開)"/
    # "住宅區、市場用地"/"甲建、乙建、特定專用區、多目標使用之其他公共設施
    # 用地"/"工業區、丙建、丁建"/"其他可建築用地" as their grade_labels) --
    # safe to reuse the same raw-zone-name -> band mapping.
    if field_id in ("regional_land_use_zone", "individual_zoning_designation") and isinstance(raw_value, str):
        band = _LAND_USE_ZONE_BAND_MAP.get(raw_value)
        if band is not None and band != raw_value:
            return band, f"使用分區類型「{raw_value}」依評價基準明細表歸類為級距「{band}」", MAPPING_SOURCE
        return None, None, None

    if field_id in _BOOLEAN_PRESENCE_BAND_MAP and isinstance(raw_value, str):
        band = _BOOLEAN_PRESENCE_BAND_MAP[field_id].get(raw_value)
        if band is not None and band != raw_value:
            return band, f"勾選狀態「{raw_value}」對應評價基準明細表級距「{band}」", MAPPING_SOURCE
        return None, None, None

    if field_id == "regional_land_improvement":
        if raw_value in _LAND_IMPROVEMENT_BAND_LABELS:
            # Already a valid count-band label (e.g. a caller that already
            # did its own classification) -- never re-count/re-map it.
            return None, None, None
        count = _count_land_improvement_items(raw_value)
        band = next(label for threshold, label in _LAND_IMPROVEMENT_COUNT_BANDS if count >= threshold)
        if band != raw_value:
            return band, f"改良項目「{raw_value}」共{count}項，對應評價基準明細表級距「{band}」", MAPPING_SOURCE
        return None, None, None

    return None, None, None
