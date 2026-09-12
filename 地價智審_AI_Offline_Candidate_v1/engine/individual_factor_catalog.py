# -*- coding: utf-8 -*-
"""
individual_factor_catalog.py — TABLE4-THREE-COMPARABLE-D1.

Maps each "IND-*" rule_id prefix (data/rules/competition/
shulin_residential_2026/individual_rules.json) to the field_id
`schemas/field_dictionary.json` ALREADY establishes for it. Unlike
engine/table51_analysis_engine.py's REGIONAL catalog builder (which safely
derives field_id mechanically as "regional_" + lowercased rule_id suffix,
verified to match field_dictionary.json for all 29 factors), the INDIVIDUAL
convention does NOT mechanically match for two factors:

  IND-LAND_TERRAIN_INDIVIDUAL (地勢) -> field_dictionary.json calls this
    "individual_terrain_form4", not "individual_land_terrain_individual"
    (地勢 exists as BOTH a regional and an individual factor; the
    "_form4" suffix disambiguates the individual-scope one from
    "regional_terrain").

  IND-DEAD_END_ALLEY (無尾巷) -> field_dictionary.json has NO dedicated
    field_id for this at all. 表4比較法調查估價表(2).xlsx's own blank
    layout has no "無尾巷" row either -- only a generic, unnumbered
    "6其他" (other) catch-all row after the 19 numbered individual
    factors. field_dictionary.json's matching slot is "individual_other".
    So 無尾巷 is mapped to "individual_other" here -- a genuine, source-
    verified mapping (the official form's own generic catch-all row),
    not a guess.

This module is therefore an EXPLICIT lookup table, sourced from
schemas/field_dictionary.json's already-established field_id conventions
-- never a mechanical/guessed transformation for individual factors.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Tuple

INDIVIDUAL_FIELD_ID_MAP: Dict[str, str] = {
    "IND-LAND_AREA": "individual_land_area",
    "IND-LAND_WIDTH": "individual_land_width",
    "IND-LAND_DEPTH": "individual_land_depth",
    "IND-LAND_SHAPE": "individual_land_shape",
    "IND-STREET_FRONTAGE": "individual_street_frontage",
    "IND-LAND_TERRAIN_INDIVIDUAL": "individual_terrain_form4",
    "IND-ROAD_TYPE": "individual_road_type",
    "IND-FRONTAGE_ROAD_WIDTH": "individual_frontage_road_width",
    "IND-SCHOOL_PROXIMITY_INDIVIDUAL": "individual_school_proximity",
    "IND-MARKET_PROXIMITY_INDIVIDUAL": "individual_market_proximity",
    "IND-PARK_PROXIMITY_INDIVIDUAL": "individual_park_proximity",
    "IND-STATION_PROXIMITY_INDIVIDUAL": "individual_station_proximity",
    "IND-COMMERCIAL_DISTRICT_PROXIMITY": "individual_commercial_district_proximity",
    "IND-NUISANCE_FACILITY": "individual_nuisance_facility",
    "IND-PARKING_CONVENIENCE_INDIVIDUAL": "individual_parking_convenience",
    "IND-ZONING_DESIGNATION": "individual_zoning_designation",
    "IND-BUILDING_COVERAGE_RATIO_INDIVIDUAL": "individual_building_coverage_ratio",
    "IND-CONSTRUCTION_RESTRICTION_INDIVIDUAL": "individual_construction_restriction",
    "IND-DEAD_END_ALLEY": "individual_other",
}

# Shulin A2's deliberate special policy: NO rule record exists for this
# field_id at all (data/rules/competition/shulin_residential_2026/
# manifest.json's special_policies -- "容積率差異以土地開發分析法進行試算
# 調整", MANUAL_REVIEW_REQUIRED/LAND_DEVELOPMENT_ANALYSIS_REQUIRED,
# source_page="p.8"). Included in the catalog explicitly (not derived from
# any rule record, since none exists) so Table4AnalysisEngine always
# produces exactly 20 factor slots, never silently 19.
FAR_FIELD_ID = "individual_floor_area_ratio"
FAR_FACTOR_NAME = "容積率"


def build_individual_factor_catalog(individual_rule_records: List[dict]) -> "OrderedDict[str, str]":
    """field_id -> factor_name, one entry per DISTINCT standard-matrix
    individual factor (via INDIVIDUAL_FIELD_ID_MAP) PLUS the FAR special
    policy factor appended last -- always 20 entries for the Shulin
    individual_rules.json (19 standard + FAR), generically extensible to
    any other rule_profile's own IND-* records recognized by
    INDIVIDUAL_FIELD_ID_MAP (an IND- prefix NOT in the map is skipped,
    never guessed)."""
    catalog: "OrderedDict[str, str]" = OrderedDict()
    for r in individual_rule_records:
        rule_id = r.get("rule_id", "")
        if not rule_id.startswith("IND-"):
            continue
        prefix = rule_id.rsplit("-", 1)[0]
        field_id = INDIVIDUAL_FIELD_ID_MAP.get(prefix)
        if field_id is None:
            continue
        if field_id not in catalog:
            catalog[field_id] = r["factor"]
    catalog[FAR_FIELD_ID] = FAR_FACTOR_NAME
    return catalog
