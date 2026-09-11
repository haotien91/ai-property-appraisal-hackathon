# -*- coding: utf-8 -*-
"""
Direct unit tests for providers/nlsc_cadastral_code_resolver.py (Phase
API-2.3). Uses a real, tmp_path-scoped NlscCodeCache seeded with small
synthetic fixtures via the same Atomic Staging Contract the real
scripts/sync_nlsc_codes.py job uses -- never a mocked cache object, since
NlscCadastralCodeResolver's whole job is exact-match SQL lookups against
that store.

One additional test (`test_golden_case_against_real_synced_cache`) runs
against the ACTUAL data/nlsc_code_cache.sqlite3 populated by this round's
real `py scripts/sync_nlsc_codes.py` run against NLSC's live, confirmed-
open ListCounty/ListTown/ListLandSection endpoints -- skipped automatically
if that file does not exist (e.g. a fresh checkout that hasn't run the
sync job yet), so the rest of the suite never depends on live network
state or a specific machine's local data/ directory.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

import pytest  # noqa: E402

from nlsc_code_cache import NlscCodeCache  # noqa: E402
from nlsc_cadastral_code_resolver import NlscCadastralCodeResolver  # noqa: E402
from domain.models import SectionCodeMatchStatus  # noqa: E402

REAL_DB_PATH = os.path.join(REPO_ROOT, "data", "nlsc_code_cache.sqlite3")


def _seeded_cache(tmp_path, *, county_code="F", town_code="F25", counties=None, towns=None, sections=None):
    cache = NlscCodeCache(db_path=str(tmp_path / "nlsc_codes.sqlite3"))
    cache.begin_staging_run("counties")
    cache.stage_counties(counties or [{"county_code": county_code, "county_name": "新北市"}])
    cache.promote_counties(source_service="ListCounty", source_url="http://x")

    cache.begin_staging_run("towns")
    cache.stage_towns(county_code, towns or [{"town_code": town_code, "town_name": "金山區"}])
    cache.promote_towns(source_service="ListTown", source_url="http://x")

    cache.begin_staging_run("sections")
    # Sections are always staged under 新北市/"F" regardless of
    # county_code/town_code above -- ListLandSection's real sync scope
    # (see nlsc_code_cache.SECTION_SNAPSHOT_COVERAGE) is New Taipei only,
    # so a caller testing a non-"F" county via county_code= is exactly
    # trying to exercise the OUT_OF_COVERAGE path, not a genuine
    # "F"-scoped section match.
    cache.stage_sections("F", "F25", sections if sections is not None else [
        {"office": "FD", "section_code": "1027", "section_name": "金美段"},
    ])
    cache.promote_sections(source_service="ListLandSection", source_url="http://x")
    return cache


def test_resolve_found_returns_codes_and_office(tmp_path):
    cache = _seeded_cache(tmp_path)
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "金山區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.FOUND
    assert ev.city_code == "F"
    assert ev.town_code == "F25"
    assert ev.section_code == "1027"
    assert ev.office == "FD"
    assert ev.requires_manual_review is True  # human review still flagged per this round's contract


def test_resolve_county_not_found(tmp_path):
    cache = _seeded_cache(tmp_path)
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("不存在市", "金山區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.NOT_FOUND


def test_resolve_district_not_found(tmp_path):
    cache = _seeded_cache(tmp_path)
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "不存在區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.NOT_FOUND
    assert ev.city_code == "F"  # county stage already resolved before district failed


def test_resolve_section_not_found(tmp_path):
    cache = _seeded_cache(tmp_path)
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "金山區", "不存在段")
    assert ev.match_status == SectionCodeMatchStatus.NOT_FOUND
    assert ev.town_code == "F25"  # town stage already resolved before section failed


def test_resolve_county_ambiguous_preserves_raw_candidates(tmp_path):
    cache = _seeded_cache(tmp_path, counties=[
        {"county_code": "F", "county_name": "重複市"},
        {"county_code": "Z", "county_name": "重複市"},
    ])
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("重複市", "金山區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.AMBIGUOUS
    assert len(ev.candidates) == 2
    assert {c["county_code"] for c in ev.candidates} == {"F", "Z"}


def test_resolve_town_ambiguous_preserves_raw_candidates(tmp_path):
    cache = _seeded_cache(tmp_path, towns=[
        {"town_code": "F25", "town_name": "重複區"},
        {"town_code": "F99", "town_name": "重複區"},
    ])
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "重複區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.AMBIGUOUS
    assert len(ev.candidates) == 2


def test_resolve_section_ambiguous_preserves_raw_candidates_never_auto_picks(tmp_path):
    cache = _seeded_cache(tmp_path, sections=[
        {"office": "FD", "section_code": "1027", "section_name": "重複段"},
        {"office": "FD", "section_code": "9999", "section_name": "重複段"},
    ])
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "金山區", "重複段")
    assert ev.match_status == SectionCodeMatchStatus.AMBIGUOUS
    assert len(ev.candidates) == 2
    assert ev.section_code is None  # must NOT silently pick "the first one"


def test_resolve_non_new_taipei_county_is_out_of_coverage_not_not_found(tmp_path):
    # Phase API-2.3H §7: ListLandSection was only ever synced for 新北市
    # (see nlsc_code_cache.SECTION_SNAPSHOT_COVERAGE_COUNTY_CODES) -- a
    # county outside that scope must report OUT_OF_COVERAGE, never
    # NOT_FOUND (which would misrepresent "we never synced this" as
    # "NLSC confirmed no such section").
    cache = _seeded_cache(
        tmp_path, county_code="A", town_code="A01",
        counties=[{"county_code": "A", "county_name": "臺北市"}],
        towns=[{"town_code": "A01", "town_name": "中正區"}],
    )
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("臺北市", "中正區", "隨便段")
    assert ev.match_status == SectionCodeMatchStatus.OUT_OF_COVERAGE
    assert ev.match_status != SectionCodeMatchStatus.NOT_FOUND
    assert ev.city_code == "A"
    assert ev.town_code == "A01"


def test_resolve_new_taipei_county_within_coverage_can_still_not_found(tmp_path):
    # Sanity check: coverage gating must not accidentally suppress a
    # genuine NOT_FOUND for a county that WAS synced (新北市/"F").
    cache = _seeded_cache(tmp_path)
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "金山區", "不存在段")
    assert ev.match_status == SectionCodeMatchStatus.NOT_FOUND


def test_resolve_never_synced_cache_returns_not_found_with_sync_hint(tmp_path):
    cache = NlscCodeCache(db_path=str(tmp_path / "nlsc_codes.sqlite3"))
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("新北市", "金山區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.NOT_FOUND
    assert "sync_nlsc_codes.py" in ev.notes


def test_resolve_whitespace_only_normalization_not_fuzzy(tmp_path):
    cache = _seeded_cache(tmp_path)
    resolver = NlscCadastralCodeResolver(cache=cache)
    ev = resolver.resolve("  新北市  ", "  金山區  ", "  金美段  ")
    assert ev.match_status == SectionCodeMatchStatus.FOUND
    assert ev.section_code == "1027"

    # A genuinely different (non-whitespace) string must still fail exact match.
    ev2 = resolver.resolve("新北市", "金山區", "金美")  # missing trailing 段
    assert ev2.match_status == SectionCodeMatchStatus.NOT_FOUND


@pytest.mark.skipif(not os.path.exists(REAL_DB_PATH), reason="data/nlsc_code_cache.sqlite3 尚未由 scripts/sync_nlsc_codes.py 產生")
def test_golden_case_against_real_synced_cache():
    resolver = NlscCadastralCodeResolver(cache=NlscCodeCache(db_path=REAL_DB_PATH))
    ev = resolver.resolve("新北市", "金山區", "金美段")
    assert ev.match_status == SectionCodeMatchStatus.FOUND
    assert ev.city_code == "F"
    assert ev.town_code == "F25"
    assert ev.section_code == "1027"
    assert ev.office == "FD"
