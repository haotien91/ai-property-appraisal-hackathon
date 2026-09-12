# -*- coding: utf-8 -*-
"""
zone_name_normalizer — maps an official raw zoning-shapefile zone name
（如NtpcZoningProvider查得的「第二種商業區」）to one of 都市計畫法新北市
施行細則附表一的19種標準分區類別（如「商業區」），for Layer 1（`data/rules/
ntpc_common_zone_ratios.json`）之建蔽率查詢 -- WITHOUT ever overwriting or
discarding the official raw name itself, per this codebase's project-wide
"官方原始資料與系統推論不可混在一起" principle.

Two deterministic strategies, tried in order:
  1. EXACT_MATCH -- the raw name already equals one of the 19 known
     categories verbatim (e.g. "工業區").
  2. SUBGRADE_PREFIX_STRIP -- strip a leading "第X種"（第一/二/三/四/五種）
     sub-grade prefix and any trailing "（...）"/"(...)" parenthetical
     annotation, then re-check against the 19 categories (e.g.
     "第二種商業區(附)" -> "商業區").

Anything neither strategy resolves is reported as UNCLASSIFIABLE，
requires_manual_review=True -- most commonly this means the raw name is
actually a 公共設施用地 designation (道路用地／綠地／學校用地／停車場用地等,
observed directly in the real NTPC zoning shapefile during this session),
which belongs to 附表三（公共設施用地建蔽率及容積率規定表), a DIFFERENT
table this codebase has not digitized. This function never guesses a
fallback category for those.
"""
from __future__ import annotations

import re
import sys
import os
from typing import FrozenSet, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import ZoneNameNormalizationResult  # noqa: E402
from engine.land_use_ratio_engine import load_common_zone_ratio_dataset  # noqa: E402

_SUBGRADE_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十]+種")
_PARENTHETICAL_SUFFIX_RE = re.compile(r"[（(][^（）()]*[）)]$")


def _known_categories() -> FrozenSet[str]:
    dataset = load_common_zone_ratio_dataset()
    return frozenset(e.zone_name for e in dataset.entries)


_KNOWN_CATEGORIES: Optional[FrozenSet[str]] = None


def _categories() -> FrozenSet[str]:
    global _KNOWN_CATEGORIES
    if _KNOWN_CATEGORIES is None:
        _KNOWN_CATEGORIES = _known_categories()
    return _KNOWN_CATEGORIES


def normalize_zone_name(official_raw_zone_name: str) -> ZoneNameNormalizationResult:
    raw = official_raw_zone_name
    categories = _categories()

    if raw in categories:
        return ZoneNameNormalizationResult(
            official_raw_zone_name=raw, normalized_zone_category=raw,
            normalization_method="EXACT_MATCH", normalization_confidence="高",
            requires_manual_review=False,
        )

    stripped = _SUBGRADE_PREFIX_RE.sub("", raw)
    stripped = _PARENTHETICAL_SUFFIX_RE.sub("", stripped).strip()
    if stripped != raw and stripped in categories:
        return ZoneNameNormalizationResult(
            official_raw_zone_name=raw, normalized_zone_category=stripped,
            normalization_method="SUBGRADE_PREFIX_STRIP", normalization_confidence="高",
            requires_manual_review=False,
        )

    return ZoneNameNormalizationResult(
        official_raw_zone_name=raw, normalized_zone_category=None,
        normalization_method="UNCLASSIFIABLE", normalization_confidence="UNKNOWN",
        requires_manual_review=True,
    )
