# -*- coding: utf-8 -*-
"""Tests for engine/zone_name_normalizer.py."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.zone_name_normalizer import normalize_zone_name  # noqa: E402


class TestExactMatch:
    def test_plain_category_name_matches_itself(self):
        r = normalize_zone_name("商業區")
        assert r.normalized_zone_category == "商業區"
        assert r.normalization_method == "EXACT_MATCH"
        assert r.normalization_confidence == "高"
        assert r.requires_manual_review is False

    def test_compound_category_name(self):
        r = normalize_zone_name("保存區、古蹟保存區")
        assert r.normalized_zone_category == "保存區、古蹟保存區"
        assert r.normalization_method == "EXACT_MATCH"


class TestSubgradePrefixStrip:
    def test_second_commercial_zone_normalizes_to_commercial(self):
        """The whole reason this module exists: Golden Case's official
        raw zone name."""
        r = normalize_zone_name("第二種商業區")
        assert r.normalized_zone_category == "商業區"
        assert r.normalization_method == "SUBGRADE_PREFIX_STRIP"
        assert r.normalization_confidence == "高"
        assert r.requires_manual_review is False

    def test_official_raw_name_is_preserved_unmodified(self):
        """The whole point of this model: official_raw_zone_name must
        never be silently overwritten by the normalization."""
        r = normalize_zone_name("第二種商業區")
        assert r.official_raw_zone_name == "第二種商業區"

    def test_first_residential_zone_with_parenthetical_suffix(self):
        r = normalize_zone_name("第一種住宅區(特1)")
        assert r.normalized_zone_category == "住宅區"

    def test_third_residential_zone_with_fullwidth_parentheses(self):
        r = normalize_zone_name("第三種住宅區（附）")
        assert r.normalized_zone_category == "住宅區"

    def test_fourth_and_fifth_grade_still_resolve(self):
        assert normalize_zone_name("第四種住宅區").normalized_zone_category == "住宅區"
        assert normalize_zone_name("第五種住宅區").normalized_zone_category == "住宅區"


class TestUnclassifiable:
    def test_public_facility_land_types_are_not_guessed(self):
        """道路用地／綠地／學校用地 etc. are 公共設施用地（附表三), a
        different table this codebase has not digitized -- must not be
        force-mapped onto any of the 19 附表一 categories."""
        for raw in ("道路用地", "綠地", "學校用地", "停車場用地", "鐵路用地"):
            r = normalize_zone_name(raw)
            assert r.normalized_zone_category is None, raw
            assert r.normalization_method == "UNCLASSIFIABLE", raw
            assert r.normalization_confidence == "UNKNOWN", raw
            assert r.requires_manual_review is True, raw

    def test_jia_yi_bing_ding_industrial_subtype_not_guessed(self):
        """甲/乙/丙/丁種工業區 use a DIFFERENT sub-grade naming convention
        than 第X種 -- this module deliberately does not extend pattern
        matching to cover it without an equally explicit official
        confirmation that 附表一's plain 工業區 figures apply identically
        to every sub-type."""
        r = normalize_zone_name("乙種工業區")
        assert r.normalized_zone_category is None
        assert r.requires_manual_review is True

    def test_unrecognized_free_text_is_unclassifiable(self):
        r = normalize_zone_name("完全不存在的分區名稱")
        assert r.normalized_zone_category is None
        assert r.normalization_method == "UNCLASSIFIABLE"
