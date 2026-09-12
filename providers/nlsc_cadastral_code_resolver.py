# -*- coding: utf-8 -*-
"""
nlsc_cadastral_code_resolver.py — Phase API-2.3: resolves
(city_name, district_name, section_name) -> NLSC's official
(county_code, town_code, section_code), using ONLY the three services
Phase API-2.2R2 live-verified as genuinely open/no-auth (ListCounty,
ListTown, ListLandSection), read from the local providers/nlsc_code_
cache.py snapshot (never the network at request time -- see that module's
docstring for why).

Exact-match only, per this round's explicit "禁止fuzzy first-match/
contains first-match/LLM selection" instruction: normalization is limited
to whitespace-stripping (never fuzzy/substring/edit-distance matching).
0 matches -> NOT_FOUND. >1 matches -> AMBIGUOUS, with every raw candidate
row preserved in `NlscSectionCodeEvidence.candidates` -- never silently
resolved to "the first one" (same principle as Phase API-1.9's multi-match
expropriation contract, applied here to section-code resolution instead
of facility distance).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NlscSectionCodeEvidence, SectionCodeMatchStatus  # noqa: E402

from nlsc_code_cache import NlscCodeCache, NlscCodeCacheError, SECTION_SNAPSHOT_COVERAGE  # noqa: E402

LIST_COUNTY_URL = "https://api.nlsc.gov.tw/other/ListCounty"
LIST_TOWN_URL = "https://api.nlsc.gov.tw/other/ListTown"
LIST_LAND_SECTION_URL = "https://api.nlsc.gov.tw/other/ListLandSection"
SOURCE_AUTHORITY = "內政部國土測繪中心"


def _normalize(name: Optional[str]) -> Optional[str]:
    """Whitespace-only normalization -- NEVER fuzzy/substring matching.
    A caller passing a name with extraneous whitespace (e.g. from OCR/user
    input) should not spuriously fail an otherwise-exact match, but this
    must not become a slippery slope into "close enough" matching."""
    if name is None:
        return None
    return name.strip()


class NlscCadastralCodeResolver:
    """DI-injectable `cache`, mirroring this project's established Real
    provider convention (RealExpropriationCaseProvider et al.). The
    default (no `cache` injected) construction is
    `NlscCodeCache(read_only=True)` (Phase API-2.3H §9): this resolver
    only ever runs in the Lambda request path, which must never attempt
    mkdir/CREATE TABLE/write against a snapshot a separate SYNC_READ_WRITE
    job (scripts/sync_nlsc_codes.py) already populated -- see nlsc_code_
    cache.py's own docstring for RUNTIME_READ_ONLY vs SYNC_READ_WRITE."""

    def __init__(self, cache: Optional[NlscCodeCache] = None):
        self._cache = cache or NlscCodeCache(read_only=True)

    def resolve(self, city_name: str, district_name: str, section_name: str) -> NlscSectionCodeEvidence:
        retrieved_at = datetime.now()
        city_name = _normalize(city_name)
        district_name = _normalize(district_name)
        section_name = _normalize(section_name)

        try:
            counties = self._cache.lookup_county(city_name) if city_name else []
        except NlscCodeCacheError:
            return NlscSectionCodeEvidence(
                city_name=city_name, district_name=district_name, section_name=section_name,
                match_status=SectionCodeMatchStatus.NOT_FOUND,
                source_service="ListCounty", source_url=LIST_COUNTY_URL, retrieved_at=retrieved_at,
                requires_manual_review=True,
                notes="NLSC county代碼snapshot尚未同步，請先執行 `py scripts/sync_nlsc_codes.py`。",
            )
        if not city_name or len(counties) == 0:
            return NlscSectionCodeEvidence(
                city_name=city_name, district_name=district_name, section_name=section_name,
                match_status=SectionCodeMatchStatus.NOT_FOUND,
                source_service="ListCounty", source_url=LIST_COUNTY_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=f"官方ListCounty清單中查無縣市名稱「{city_name}」之精確相符項。",
            )
        if len(counties) > 1:
            return NlscSectionCodeEvidence(
                city_name=city_name, district_name=district_name, section_name=section_name,
                match_status=SectionCodeMatchStatus.AMBIGUOUS,
                candidates=[dict(c) for c in counties],
                source_service="ListCounty", source_url=LIST_COUNTY_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=f"官方ListCounty清單中「{city_name}」對應多筆county_code，需人工確認。",
            )
        county_code = counties[0]["county_code"]

        try:
            towns = self._cache.lookup_town(county_code, district_name) if district_name else []
        except NlscCodeCacheError:
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, section_name=section_name,
                match_status=SectionCodeMatchStatus.NOT_FOUND,
                source_service="ListTown", source_url=LIST_TOWN_URL, retrieved_at=retrieved_at,
                requires_manual_review=True,
                notes="NLSC town代碼snapshot尚未同步，請先執行 `py scripts/sync_nlsc_codes.py`。",
            )
        if not district_name or len(towns) == 0:
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, section_name=section_name,
                match_status=SectionCodeMatchStatus.NOT_FOUND,
                source_service="ListTown", source_url=LIST_TOWN_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=f"官方ListTown（{city_name}）清單中查無行政區名稱「{district_name}」之精確相符項。",
            )
        if len(towns) > 1:
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, section_name=section_name,
                match_status=SectionCodeMatchStatus.AMBIGUOUS,
                candidates=[dict(t) for t in towns],
                source_service="ListTown", source_url=LIST_TOWN_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=f"官方ListTown清單中「{district_name}」對應多筆town_code，需人工確認。",
            )
        town_code = towns[0]["town_code"]

        # Phase API-2.3H §7: county/town resolution succeeded, but
        # ListLandSection was only ever synced for 新北市 -- a 0-row
        # section lookup for any OTHER county would NOT mean "NLSC has no
        # such section" (that claim requires having actually synced that
        # county's sections), it means this codebase never tried. Checked
        # BEFORE running the section lookup so this is a deterministic,
        # coverage-driven answer, never conflated with a genuine
        # zero-match result within a county that WAS synced.
        if not self._cache.is_county_in_section_coverage(county_code):
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, town_code=town_code,
                section_name=section_name,
                match_status=SectionCodeMatchStatus.OUT_OF_COVERAGE,
                source_service="ListLandSection", source_url=LIST_LAND_SECTION_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=(
                    f"SECTION_SNAPSHOT_COVERAGE={SECTION_SNAPSHOT_COVERAGE}："
                    f"county_code={county_code!r}之地段清單本輪從未同步（非查無資料，"
                    "而是同步範圍本身未涵蓋），不得標記為NOT_FOUND。"
                ),
            )

        try:
            sections = self._cache.lookup_section(county_code, town_code, section_name) if section_name else []
        except NlscCodeCacheError:
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, town_code=town_code,
                section_name=section_name,
                match_status=SectionCodeMatchStatus.NOT_FOUND,
                source_service="ListLandSection", source_url=LIST_LAND_SECTION_URL, retrieved_at=retrieved_at,
                requires_manual_review=True,
                notes="NLSC section代碼snapshot尚未同步，請先執行 `py scripts/sync_nlsc_codes.py`。",
            )
        if not section_name or len(sections) == 0:
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, town_code=town_code,
                section_name=section_name,
                match_status=SectionCodeMatchStatus.NOT_FOUND,
                source_service="ListLandSection", source_url=LIST_LAND_SECTION_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=f"官方ListLandSection（{district_name}）清單中查無地段名稱「{section_name}」之精確相符項。",
            )
        if len(sections) > 1:
            return NlscSectionCodeEvidence(
                city_name=city_name, city_code=county_code, district_name=district_name, town_code=town_code,
                section_name=section_name,
                match_status=SectionCodeMatchStatus.AMBIGUOUS,
                candidates=[dict(s) for s in sections],
                source_service="ListLandSection", source_url=LIST_LAND_SECTION_URL, retrieved_at=retrieved_at,
                authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
                notes=f"官方ListLandSection清單中「{section_name}」對應多筆section_code，需人工確認，不得自動擇一。",
            )
        section = sections[0]

        return NlscSectionCodeEvidence(
            city_name=city_name, city_code=county_code, district_name=district_name, town_code=town_code,
            section_name=section_name, section_code=section["section_code"], office=section.get("office"),
            match_status=SectionCodeMatchStatus.FOUND,
            source_service="ListLandSection", source_url=LIST_LAND_SECTION_URL, retrieved_at=retrieved_at,
            authoritative_status="OFFICIAL_OPEN_DATA", requires_manual_review=True,
            notes="縣市/行政區/地段名稱皆於NLSC官方開放代碼服務中取得唯一精確相符結果。",
        )
