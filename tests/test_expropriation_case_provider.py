# -*- coding: utf-8 -*-
"""
Tests for providers/expropriation_case_provider.py (Phase API-1.5
rewrite). The PRIMARY path (`query_case`/`fetch`) is tested against an
injected, in-memory-backed CadastralDatasetCache (real sqlite3, throwaway
file per test via tmp_path -- not mocked at the HTTP layer, since the
primary path never touches the network at all). The FALLBACK live-scan
path (`query_case_via_live_scan`) is tested separately with `_http_get_
json` monkeypatched, exactly like Phase API-1's original suite, specifically
to prove it is never invoked by the primary path (item 16).
"""
import os
import sys
from datetime import datetime

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from domain.models import ExpropriationCaseStatus  # noqa: E402
import expropriation_case_provider as ecp  # noqa: E402
from cadastral_dataset_cache import CadastralDatasetCache, SCOPE_ALL  # noqa: E402


def _fresh_cache(tmp_path):
    return CadastralDatasetCache(db_path=str(tmp_path / "cache.sqlite3"))


def _synced_cache(tmp_path, records):
    cache = _fresh_cache(tmp_path)
    cache.write_expropriation_snapshot(
        dataset_id=ecp.DATASET_ID, dataset_name=ecp.DATASET_NAME,
        source_url=ecp.OFFICIAL_PAGE_URL, source_authority=ecp.SOURCE_AUTHORITY,
        scope_key=SCOPE_ALL, records=records,
    )
    return cache


# Use a raw identifier the parser can actually decompose to main=151 sub=0
# (matches the real government id "1510000" verified during Phase API-1.5).
CTX_REAL_SHAPE = ProviderContext(
    case_no="T", city="新北市", district="蘆洲區", segment_code="P002-00", parcel_id="保新段151地號",
)


# ---------------------------------------------------------------------------
# 10. exact match
# ---------------------------------------------------------------------------

def test_exact_match_against_synced_snapshot(tmp_path):
    cache = _synced_cache(tmp_path, [
        {"district": "蘆洲區", "segment": "保新段", "id": "1510000", "sus_year": "2003", "pro_name": "測試工程"},
    ])
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_REAL_SHAPE)

    assert evidence.status == ExpropriationCaseStatus.FOUND_IN_DATASET
    assert evidence.announcement_year == "2003"
    assert evidence.project_name == "測試工程"
    assert evidence.segment == "保新段"
    assert evidence.land_no == "1510000"


# ---------------------------------------------------------------------------
# 11. not found in dataset
# ---------------------------------------------------------------------------

def test_not_found_in_fully_synced_snapshot(tmp_path):
    cache = _synced_cache(tmp_path, [
        {"district": "蘆洲區", "segment": "別的段", "id": "9999999", "sus_year": "1999", "pro_name": "別的工程"},
    ])
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_REAL_SHAPE)

    assert evidence.status == ExpropriationCaseStatus.NOT_FOUND_IN_DATASET
    assert "查無相符紀錄" in evidence.notes
    assert "不含區段徵收" in evidence.notes
    # A real NOT_FOUND from a complete snapshot carries provenance proving
    # the whole dataset was actually checked, not truncated.
    assert evidence.checksum is not None
    assert evidence.record_count == 1


# ---------------------------------------------------------------------------
# 13. snapshot unavailable (never synced)
# ---------------------------------------------------------------------------

def test_snapshot_unavailable_returns_unknown_with_sync_instruction(tmp_path):
    cache = _fresh_cache(tmp_path)  # never synced
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_REAL_SHAPE)

    assert evidence.status == ExpropriationCaseStatus.UNKNOWN
    assert "sync_expropriation_dataset.py" in evidence.notes
    assert evidence.checksum is None


# ---------------------------------------------------------------------------
# Golden Case normalization (item 15) -- section_name must never be the
# price_segment_code, and the real Golden Case identifier must resolve
# correctly through the whole pipeline.
# ---------------------------------------------------------------------------

def test_golden_case_identifier_never_uses_price_segment_code(tmp_path):
    golden_ctx = ProviderContext(
        case_no="1140901-99-001", city="新北市", district="金山區", segment_code="P002-00",
        parcel_id="金美段489地號",
    )
    cache = _synced_cache(tmp_path, [])  # empty but SYNCED (proves NOT_FOUND, not UNKNOWN)
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(golden_ctx)

    assert evidence.segment == "金美段"  # the real cadastral section, parsed from base_parcel_id
    assert evidence.segment != "P002-00"  # never the price_segment_code
    assert evidence.land_no == "4890000"  # LandNumberNormalizer.to_expropriation_id(489, 0)
    assert evidence.status == ExpropriationCaseStatus.NOT_FOUND_IN_DATASET


def test_parse_uncertain_identifier_short_circuits_before_any_lookup(tmp_path):
    bad_ctx = ProviderContext(
        case_no="T", city="新北市", district="金山區", segment_code="P002-00", parcel_id="P002-00",
    )
    cache = _fresh_cache(tmp_path)  # not synced -- if this were touched it would raise
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(bad_ctx)

    assert evidence.status == ExpropriationCaseStatus.UNKNOWN
    assert "PARSE_UNCERTAIN" in evidence.notes
    assert evidence.segment is None


# ---------------------------------------------------------------------------
# 14. no silent Mock fallback
# ---------------------------------------------------------------------------

def test_real_provider_never_falls_back_to_mock(tmp_path):
    cache = _fresh_cache(tmp_path)
    real_evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_REAL_SHAPE)
    mock_evidence = ecp.MockExpropriationCaseProvider().query_case(CTX_REAL_SHAPE)

    assert real_evidence.dataset_id == ecp.DATASET_ID
    assert real_evidence.source_authority == ecp.SOURCE_AUTHORITY
    assert mock_evidence.dataset_id is None
    assert real_evidence.notes != mock_evidence.notes


def test_mock_fetch_is_fully_offline(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("MockExpropriationCaseProvider must never call the network")
    monkeypatch.setattr(ecp, "_http_get_json", _boom)
    points = ecp.MockExpropriationCaseProvider().fetch(CTX_REAL_SHAPE)
    assert points[0].value == ExpropriationCaseStatus.UNKNOWN.value
    assert points[0].source_type == "Mock"


# ---------------------------------------------------------------------------
# 16. old runtime paging path does not silently override snapshot result
# ---------------------------------------------------------------------------

def test_primary_path_never_invokes_live_scan(tmp_path, monkeypatch):
    """Proves query_case() (primary) never calls the network at all, even
    when a live-scan WOULD have found a different answer -- the snapshot
    result must never be silently overridden by a live lookup."""
    def _boom(url):
        raise AssertionError("query_case() must never touch the network -- that is query_case_via_live_scan's job")
    monkeypatch.setattr(ecp, "_http_get_json", _boom)

    cache = _synced_cache(tmp_path, [
        {"district": "蘆洲區", "segment": "保新段", "id": "1510000", "sus_year": "2003", "pro_name": "測試工程"},
    ])
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_REAL_SHAPE)
    assert evidence.status == ExpropriationCaseStatus.FOUND_IN_DATASET  # from the snapshot, not the network


def test_live_scan_fallback_is_a_separate_named_method(monkeypatch):
    """The fallback exists and works when EXPLICITLY invoked -- it is not
    deleted, just never auto-triggered."""
    def _page(url):
        return [{"district": "蘆洲區", "segment": "保新段", "id": "1510000", "sus_year": "2003", "pro_name": "測試工程"}]
    monkeypatch.setattr(ecp, "_http_get_json", _page)

    evidence = ecp.RealExpropriationCaseProvider().query_case_via_live_scan(CTX_REAL_SHAPE, "保新段", "1510000")
    assert evidence.status == ExpropriationCaseStatus.FOUND_IN_DATASET
    assert evidence.project_name == "測試工程"


def test_fetch_uses_primary_snapshot_path_not_network(tmp_path, monkeypatch):
    def _boom(url):
        raise AssertionError("fetch() must use the snapshot path")
    monkeypatch.setattr(ecp, "_http_get_json", _boom)

    cache = _synced_cache(tmp_path, [])
    provider = ecp.RealExpropriationCaseProvider(cache=cache)
    points = provider.fetch(CTX_REAL_SHAPE)
    assert points[0].value == ExpropriationCaseStatus.NOT_FOUND_IN_DATASET.value


# ---------------------------------------------------------------------------
# Phase API-1.9: Multi-Record Parcel Semantics
#
# Real-shape fixture: district=新店區/segment=粗坑段/id=5080002 mirrors the
# ACTUAL live parcel Phase API-1.8 first found with a duplicate key (though
# with synthetic sus_year/pro_name values here, since these tests must stay
# offline). CTX below decomposes (via CadastralParcelIdentifierParser) to
# section_name=粗坑段, land_no=5080002 (main=508, sub=2 -> "5080002" per
# LandNumberNormalizer.to_expropriation_id).
# ---------------------------------------------------------------------------

CTX_MULTI = ProviderContext(
    case_no="T2", city="新北市", district="新店區", segment_code="P999-00", parcel_id="粗坑段508之2地號",
)


def _multi_records(*event_rows):
    """Each item is (sus_year, pro_name); district/segment/id are fixed to
    the CTX_MULTI shape above."""
    return [
        {"district": "新店區", "segment": "粗坑段", "id": "5080002", "sus_year": y, "pro_name": p}
        for y, p in event_rows
    ]


def test_multi_record_same_parcel_different_project_same_year(tmp_path):
    """same year, different project -- the most common real category found
    in Phase API-1.9's audit (322/441)."""
    cache = _synced_cache(tmp_path, _multi_records(
        ("2005", "第一次徵收工程"), ("2005", "第二次徵收工程"),
    ))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.status == ExpropriationCaseStatus.FOUND_IN_DATASET
    assert evidence.match_count == 2
    assert evidence.distinct_event_count == 2
    assert evidence.raw_match_count == 2
    assert {(m.announcement_year, m.project_name) for m in evidence.matches} == {
        ("2005", "第一次徵收工程"), ("2005", "第二次徵收工程"),
    }


def test_multi_record_same_parcel_different_year(tmp_path):
    """different year (and, per the real audit, always also a different
    project name in practice -- 0 parcels were "different year only, same
    project" -- but the provider logic must handle this shape correctly
    regardless)."""
    cache = _synced_cache(tmp_path, _multi_records(
        ("2010", "國道拓寬工程(第一次)"), ("2011", "國道拓寬工程(第二次)"),
    ))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.match_count == 2
    years = {m.announcement_year for m in evidence.matches}
    assert years == {"2010", "2011"}


def test_exact_duplicate_source_rows_collapse_to_one_match_but_raw_count_preserved(tmp_path):
    """62/441 real multi-record parcels are EXACT_SOURCE_DUPLICATE (every
    row byte-identical) -- one real event listed 2-3 times. This must
    produce exactly ONE ExpropriationCaseMatch (never 2-3 phantom events),
    while raw_match_count still honestly reports the raw row count."""
    cache = _synced_cache(tmp_path, _multi_records(
        ("2018", "捷運系統計畫工程"), ("2018", "捷運系統計畫工程"), ("2018", "捷運系統計畫工程"),
    ))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.status == ExpropriationCaseStatus.FOUND_IN_DATASET
    assert evidence.raw_match_count == 3
    assert evidence.distinct_event_count == 1
    assert evidence.match_count == 1
    assert len(evidence.matches) == 1
    # Single real event -- backward-compatible convenience fields ARE populated.
    assert evidence.announcement_year == "2018"
    assert evidence.project_name == "捷運系統計畫工程"
    assert "3筆" in evidence.notes or "重複列" in evidence.notes


def test_all_matches_returned_no_first_match_loss(tmp_path):
    """Direct proof against the exact failure mode this round forbids:
    with 3 genuinely distinct events staged, the provider must return ALL
    3 in `matches`, never just the first one found by SQL."""
    cache = _synced_cache(tmp_path, _multi_records(
        ("2005", "工程甲"), ("2006", "工程乙"), ("2007", "工程丙"),
    ))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.match_count == 3
    assert len(evidence.matches) == 3
    project_names = {m.project_name for m in evidence.matches}
    assert project_names == {"工程甲", "工程乙", "工程丙"}


def test_query_case_uses_matches_lookup_not_legacy_single_result(tmp_path, monkeypatch):
    """Structural proof (not just behavioral) that query_case() calls
    lookup_expropriation_matches(), never the legacy single-result
    lookup_expropriation() which cannot represent multiplicity at all."""
    cache = _synced_cache(tmp_path, _multi_records(("2005", "工程甲"), ("2006", "工程乙")))

    def _boom(*a, **k):
        raise AssertionError("query_case() must not call the legacy single-result lookup_expropriation()")
    monkeypatch.setattr(type(cache), "lookup_expropriation", _boom)

    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)
    assert evidence.match_count == 2


def test_deterministic_match_ordering_independent_of_insertion_order(tmp_path):
    """Same snapshot content, inserted in reverse order -- matches must
    come back in the identical order either way (sorted by the full row
    tuple inside lookup_expropriation_matches(), not SQLite's return
    order)."""
    forward = _synced_cache(tmp_path / "a", _multi_records(
        ("2005", "工程甲"), ("2006", "工程乙"), ("2007", "工程丙"),
    ))
    reversed_records = list(reversed(_multi_records(
        ("2005", "工程甲"), ("2006", "工程乙"), ("2007", "工程丙"),
    )))
    backward = _synced_cache(tmp_path / "b", reversed_records)

    ev_forward = ecp.RealExpropriationCaseProvider(cache=forward).query_case(CTX_MULTI)
    ev_backward = ecp.RealExpropriationCaseProvider(cache=backward).query_case(CTX_MULTI)

    order_forward = [(m.announcement_year, m.project_name) for m in ev_forward.matches]
    order_backward = [(m.announcement_year, m.project_name) for m in ev_backward.matches]
    assert order_forward == order_backward


def test_single_match_backward_compatibility_convenience_fields_populated(tmp_path):
    """match_count == 1 (the pre-1.9 norm for 8,904/8,966 parcels) must
    keep populating announcement_year/project_name exactly as before."""
    cache = _synced_cache(tmp_path, _multi_records(("2003", "測試工程")))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.match_count == 1
    assert evidence.announcement_year == "2003"
    assert evidence.project_name == "測試工程"
    assert len(evidence.matches) == 1


def test_multi_match_convenience_fields_are_none_never_arbitrary(tmp_path):
    """match_count > 1 must leave announcement_year/project_name explicitly
    None -- never silently the first, latest, or highest-year match."""
    cache = _synced_cache(tmp_path, _multi_records(
        ("2005", "工程甲"), ("2020", "工程乙(最新)"),
    ))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.match_count == 2
    assert evidence.announcement_year is None
    assert evidence.project_name is None
    assert "matches" in evidence.notes or len(evidence.matches) == 2


def test_zero_match_status_and_fields_unchanged_by_multi_record_support(tmp_path):
    cache = _synced_cache(tmp_path, [])
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)

    assert evidence.status == ExpropriationCaseStatus.NOT_FOUND_IN_DATASET
    assert evidence.match_count == 0
    assert evidence.matches == []
    assert evidence.raw_match_count == 0
    assert evidence.distinct_event_count == 0
    assert evidence.announcement_year is None
    assert evidence.project_name is None


def test_mock_provider_returns_unknown_consistently_across_zero_single_multi_shapes():
    """Mock Mode never queries any real data (see module docstring) --
    confirming it returns the same honest UNKNOWN regardless of whether
    the real parcel would be zero/single/multi-match ensures Mock never
    accidentally leaks or fabricates real multi-match behavior."""
    mock = ecp.MockExpropriationCaseProvider()
    for ctx in (CTX_REAL_SHAPE, CTX_MULTI):
        evidence = mock.query_case(ctx)
        assert evidence.status == ExpropriationCaseStatus.UNKNOWN
        assert evidence.match_count == 0
        assert evidence.matches == []


def test_model_dump_includes_multi_match_fields_for_collect_data_persistence(tmp_path):
    """collect_data.py persists ExpropriationCaseEvidence via
    model_dump(mode="json") with no per-field logic (additive-by-
    construction) -- this proves match_count/matches/raw_match_count/
    distinct_event_count survive that exact serialization path."""
    cache = _synced_cache(tmp_path, _multi_records(("2005", "工程甲"), ("2006", "工程乙")))
    evidence = ecp.RealExpropriationCaseProvider(cache=cache).query_case(CTX_MULTI)
    dumped = evidence.model_dump(mode="json")

    assert dumped["match_count"] == 2
    assert len(dumped["matches"]) == 2
    assert dumped["matches"][0]["project_name"] in {"工程甲", "工程乙"}
    assert dumped["announcement_year"] is None
    assert dumped["project_name"] is None
