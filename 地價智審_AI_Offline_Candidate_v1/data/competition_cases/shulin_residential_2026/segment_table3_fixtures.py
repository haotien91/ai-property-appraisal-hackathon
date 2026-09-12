# -*- coding: utf-8 -*-
"""
segment_table3_fixtures.py — COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 5.

The FOUR official Competition Table3 (表3 地價區段勘查表) input fixtures for
shulin_residential_2026's real case contract:

    P001-00 = 比準地 (BASE_SEGMENT)
    P002-00 = 比較標的1 (COMPARABLE_SEGMENT_1)
    P003-00 = 比較標的2 (COMPARABLE_SEGMENT_2)
    P004-00 = 比較標的3 (COMPARABLE_SEGMENT_3)

SOURCE OF TRUTH: data/sources/competition/shulin_residential_2026/題目.pdf
(pages 1-4, one 表3 form per segment) -- READ-ONLY, never modified. Every
value below was extracted via PyMuPDF text extraction AND independently
re-verified by rendering each page to an image and visually inspecting it
(this project's established "text extraction is unreliable for column-
aligned forms" discipline) before being hand-transcribed here. NEVER
copied from data/golden/golden_case_input.py (the Jinshan Golden Case) --
that case is a different competition round, different district, different
segments entirely.

Every field below that 題目.pdf actually FILLED IN is captured as a
COMPETITION_PROVIDED_FIXED FactorInput-shaped dict. Every field the exam
form left as an UNMARKED checkbox / blank distance (schools, markets,
stations, 站牌, 交流道, 殯葬/廢棄物/電業設施 subtypes, 環境污染, 工商活動,
其他影響因素, 風勢, 土質, 農地改良, ...) is genuinely NOT PROVIDED --
deliberately absent here, never guessed or defaulted (matches this
project's "never guess when data is missing" constitution). A future
Provider/human survey step may fill these in later; this fixture makes no
claim about them one way or the other.

field_id/factor pairs match the 29 REG-* factors already built into
data/rules/competition/shulin_residential_2026/regional_rules.json (SHULIN-
COMPETITION-RULE-PACK-A2) -- e.g. "regional_building_coverage_ratio"/"建蔽率"
-- so these fixtures are immediately gradeable once a Shulin CaseRulePackage
is CONFIRMED for a case (out of THIS round's scope: no Table5-1 grading is
run here, per Task 13).

Only-relative-order note (page 4/5 of 題目.pdf, the accompanying blank
表4/表5-1 forms): 地價區段 column order is always base=P001-00, comp1=
P002-00, comp2=P003-00, comp3=P004-00 -- confirmed consistent across every
page. parcel_ids below come from that page's "0基本資料" row (one parcel
address per segment) -- purely descriptive metadata, NOT a Table4/Table5-1
calculation input (out of scope this round).
"""
from __future__ import annotations

SOURCE_DOCUMENT = "題目.pdf"
CITY = "新北市"
DISTRICT = "樹林區"
LAND_USE_TYPE = "普通住宅用地"
APPRAISAL_PERIOD = "1110901"  # 年期, identical across all 4 segments' 表3


def _evidence(source_page: str) -> dict:
    return {
        "source": f"{SOURCE_DOCUMENT} 表3 地價區段勘查表（{source_page}）",
        "source_type": "競賽題目提供固定值",
        "source_document": SOURCE_DOCUMENT,
        "source_page": source_page,
        # Deliberately NOT a percentage/AI confidence score (Task 11: a
        # COMPETITION_PROVIDED_FIXED value's confidence must never be
        # AI-rewritten) -- "確定" is a fixed, human-legible label, not a
        # number an AI_ASSISTED_FILL-style score could plausibly overwrite.
        "confidence": "確定",
        "notes": "競賽題目.pdf既定固定值，不得被Provider/AI覆寫。",
    }


def _factor(field_id: str, factor: str, raw_value, unit, source_page: str) -> dict:
    return {"field_id": field_id, "factor": factor, "raw_value": raw_value, "unit": unit,
            "evidence": _evidence(source_page)}


def _table3_factors(source_page: str, *, bcr: float, far: float, road_name: str, road_width: float,
                     avg_road_width: float, road_development: str) -> list:
    return [
        _factor("regional_zoning_inside_outside", "都市計畫內外", "都市計畫內", None, source_page),
        # TABLE51-THREE-COMPARABLE-C1-FINAL-GATE-1 Task 1: raw_value MUST
        # stay the VERBATIM 題目.pdf text ("第一種住宅區") -- it is NOT a
        # rule-pack categorical band label, and must never be silently
        # rewritten into one here. The band mapping ("住宅區、市場用地")
        # is applied ONLY at grading time, as a separate evaluation_value,
        # by engine/regional_factor_value_normalization.py -- see
        # engine/table51_analysis_engine.py's build_comparison(). This
        # fixture's job is to record what 題目.pdf actually says, nothing
        # more.
        _factor("regional_land_use_zone", "使用分區(使用地類別)", "第一種住宅區", None, source_page),
        _factor("regional_building_coverage_ratio", "建蔽率", bcr, "%", source_page),
        _factor("regional_floor_area_ratio", "容積率", far, "%", source_page),
        # Same principle: 題目.pdf's checkbox literally reads "無" (unmarked
        # = no prohibition) -- kept verbatim, never expanded to "無禁止
        # 建築" here (that expansion happens only at grading time).
        _factor("regional_construction_prohibited", "有無禁止建築", "無", None, source_page),
        _factor("regional_construction_restricted", "有無限制建築(整體開發、面積限制、高度限制)", "無", None, source_page),
        _factor("regional_main_road_width", "主要道路寬度", road_width, "M", source_page),
        _factor("regional_avg_road_width", "區段內道路平均寬度", avg_road_width, "M", source_page),
        _factor("regional_road_development_level", "區段內道路規劃及闢建程度", road_development, None, source_page),
        _factor("regional_sunlight", "日照", "充分", None, source_page),
        _factor("regional_view", "景觀", "視野、景觀尚可", None, source_page),
        _factor("regional_slope", "傾斜度", "平均坡度未滿5度", None, source_page),
        _factor("regional_drainage_quality", "排水之良否", "普通完善", None, source_page),
        _factor("regional_terrain", "地勢", "極平坦堅硬", None, source_page),
        # Kept as the LITERAL ticked-item list 題目.pdf shows (verified
        # visually identical across all 4 rendered pages: 整平或填挖基地/
        # 開挖水溝/鋪築道路/埋設管道) -- never collapsed into a count-band
        # label ("四項以上") here; that mapping happens only at grading
        # time (engine/regional_factor_value_normalization.py counts the
        # 、-delimited items).
        _factor(
            "regional_land_improvement", "建築基地改良或其他改良",
            "整平或填挖基地、開挖水溝、鋪築道路、埋設管道", None, source_page,
        ),
    ]


# ---------------------------------------------------------------------------
# P001-00 -- BASE_SEGMENT (比準地). Page 4 of 題目.pdf (0-indexed page 3).
# ---------------------------------------------------------------------------
P001_TABLE3_FACTORS = _table3_factors(
    "p.4", bcr=50, far=260, road_name="八德街", road_width=28,
    avg_road_width=12, road_development="大部分規劃及闢建",
)
P001_SEGMENT_META = {
    "segment_code": "P001-00", "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
    # TABLE4-THREE-COMPARABLE-D1 Task 2 fix: this was WRONG (had P004's
    # address) -- corrected via DIRECT visual confirmation of 題目.pdf's
    # own 表4 比較法調查估價表 page (0基本資料/地價區段 rows put the parcel
    # address and "P001-00" literally in the SAME column, unambiguous,
    # unlike the Table5-1 page's own text-extraction ordering this value
    # was originally taken from). 比準地 宗地流水號="0003".
    "parcel_ids": ["新北市樹林區樹德段1415地號"],
    "zone_range_description": "沿八德街以西、啟智街及未開闢計畫道路以南、啟智街187巷以東、啟智街187巷24弄以北之捷運開發區(變更前為第一種住宅區)",
    "building_density_pct": 60, "building_type": "透天厝、公寓",
    "land_use_status": ["商業用", "住宅用"],  # both marked -- the only segment with a dual mark
    "main_road_name": "八德街",
}

# ---------------------------------------------------------------------------
# P002-00 -- COMPARABLE_SEGMENT_1 (比較標的1). Page 1 of 題目.pdf (page 0).
# ---------------------------------------------------------------------------
P002_TABLE3_FACTORS = _table3_factors(
    "p.1", bcr=50, far=200, road_name="樹人街", road_width=7,
    avg_road_width=6, road_development="部分規劃及闢建",
)
P002_SEGMENT_META = {
    "segment_code": "P002-00", "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
    "parcel_ids": ["新北市樹林區樹德段284地號"],
    "zone_range_description": "沿樹人街以北、長壽街21巷以西、啟智街14巷以南及樹德街136巷以東之第一種住宅區",
    "building_density_pct": 70, "building_type": "公寓、透天",
    "land_use_status": ["住宅用"],
    "main_road_name": "樹人街",
}

# ---------------------------------------------------------------------------
# P003-00 -- COMPARABLE_SEGMENT_2 (比較標的2). Page 2 of 題目.pdf (page 1).
# ---------------------------------------------------------------------------
P003_TABLE3_FACTORS = _table3_factors(
    "p.2", bcr=50, far=260, road_name="東榮街", road_width=10,
    avg_road_width=7, road_development="全部規劃及闢建",
)
P003_SEGMENT_META = {
    "segment_code": "P003-00", "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
    # TABLE4-THREE-COMPARABLE-D1 Task 2 fix: corrected (see P001's note above).
    "parcel_ids": ["新北市樹林區太平段367、917地號"],
    "zone_range_description": "沿東榮街以北、鎮前街411巷1弄以南、東榮街88巷以東、鎮前街367巷以西之第一種住宅區",
    "building_density_pct": 70, "building_type": "公寓、透天",
    "land_use_status": ["住宅用"],
    "main_road_name": "東榮街",
}

# ---------------------------------------------------------------------------
# P004-00 -- COMPARABLE_SEGMENT_3 (比較標的3). Page 3 of 題目.pdf (page 2).
# ---------------------------------------------------------------------------
P004_TABLE3_FACTORS = _table3_factors(
    "p.3", bcr=50, far=260, road_name="潭興街", road_width=10,
    avg_road_width=7, road_development="全部規劃及闢建",
)
P004_SEGMENT_META = {
    "segment_code": "P004-00", "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
    # TABLE4-THREE-COMPARABLE-D1 Task 2 fix: corrected (see P001's note above).
    "parcel_ids": ["新北市樹林區文林段317地號"],
    "zone_range_description": "沿潭興街以西、潭興街107巷21弄以東及以北、潭興街91巷以南之第一種住宅區",
    "building_density_pct": 50, "building_type": "公寓",
    "land_use_status": ["住宅用"],
    "main_road_name": "潭興街",
}


SEGMENT_TABLE3_FACTORS = {
    "P001-00": P001_TABLE3_FACTORS, "P002-00": P002_TABLE3_FACTORS,
    "P003-00": P003_TABLE3_FACTORS, "P004-00": P004_TABLE3_FACTORS,
}
SEGMENT_META = {
    "P001-00": P001_SEGMENT_META, "P002-00": P002_SEGMENT_META,
    "P003-00": P003_SEGMENT_META, "P004-00": P004_SEGMENT_META,
}


def segment_map_body() -> dict:
    """The exact `segments` body shape POST /api/cases expects (Task 2)."""
    return {
        "base_segment": {
            "segment_code": "P001-00", "segment_role": "BASE_SEGMENT",
            "parcel_ids": P001_SEGMENT_META["parcel_ids"],
            "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
        },
        "comparables": [
            {
                "segment_code": "P002-00", "segment_role": "COMPARABLE_SEGMENT_1", "comparison_index": 1,
                "parcel_ids": P002_SEGMENT_META["parcel_ids"],
                "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
            },
            {
                "segment_code": "P003-00", "segment_role": "COMPARABLE_SEGMENT_2", "comparison_index": 2,
                "parcel_ids": P003_SEGMENT_META["parcel_ids"],
                "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
            },
            {
                "segment_code": "P004-00", "segment_role": "COMPARABLE_SEGMENT_3", "comparison_index": 3,
                "parcel_ids": P004_SEGMENT_META["parcel_ids"],
                "district": DISTRICT, "land_use_type": LAND_USE_TYPE,
            },
        ],
    }
