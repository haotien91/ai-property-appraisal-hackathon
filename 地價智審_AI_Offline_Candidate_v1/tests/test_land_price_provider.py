# -*- coding: utf-8 -*-
"""
Tests for providers/land_price_provider.py (Phase API-1.5 rewrite). Same
convention as tests/test_expropriation_case_provider.py: PRIMARY path
tested against an injected CadastralDatasetCache (real sqlite3, tmp_path-
scoped), FALLBACK live-scan path tested with `_http_get_json` monkeypatched.
"""
import os
import sys
from datetime import datetime
from decimal import Decimal

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from domain.models import LandPriceFieldStatus  # noqa: E402
import land_price_provider as lpp  # noqa: E402
from cadastral_dataset_cache import CadastralDatasetCache  # noqa: E402

DATASET_ID_114 = "826870ef-4ea5-48bf-915b-e0a33158cf06"


def _fresh_cache(tmp_path):
    return CadastralDatasetCache(db_path=str(tmp_path / "cache.sqlite3"))


def _synced_cache(tmp_path, district, records):
    cache = _fresh_cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id=DATASET_ID_114, dataset_name="新北市114年公告土地現值", dataset_year="114",
        source_url=f"https://data.ntpc.gov.tw/datasets/{DATASET_ID_114}", source_authority=lpp.SOURCE_AUTHORITY,
        scope_key=district, records=records,
    )
    return cache


CTX = ProviderContext(
    case_no="T", city="新北市", district="板橋區", segment_code="P002-00", parcel_id="忠孝段1地號",
)


# ---------------------------------------------------------------------------
# 1. known year schema
# ---------------------------------------------------------------------------

def test_known_year_schema_resolves_dataset(tmp_path):
    cache = _synced_cache(tmp_path, "板橋區", [
        {"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"},
    ])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")
    assert evidence.dataset_id == DATASET_ID_114
    assert evidence.dataset_year == "114"


# ---------------------------------------------------------------------------
# 2/3. year with current-value only / missing land-price field
# (schema version mismatch is item 12 -- same underlying mechanism)
# ---------------------------------------------------------------------------

def test_114_schema_never_confuses_field_not_available_with_not_found(tmp_path):
    cache = _synced_cache(tmp_path, "板橋區", [
        {"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"},
    ])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")

    assert evidence.announced_land_current_value == Decimal("278000")
    assert evidence.announced_land_current_value_status == LandPriceFieldStatus.AVAILABLE
    assert evidence.announced_land_price is None
    assert evidence.announced_land_price_status == LandPriceFieldStatus.FIELD_NOT_AVAILABLE_FOR_YEAR
    assert evidence.announced_land_price_status != LandPriceFieldStatus.NOT_FOUND_FOR_PARCEL


def test_schema_mismatch_status_persists_even_on_not_found(tmp_path):
    """Item 12 (schema version mismatch): even when the PARCEL isn't found,
    the schema-level fact (this year has no 公告地價 column) must still be
    reported -- it's a property of the dataset, not of any one lookup."""
    cache = _synced_cache(tmp_path, "板橋區", [])  # synced, but empty
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")

    assert evidence.announced_land_current_value_status == LandPriceFieldStatus.NOT_FOUND_FOR_PARCEL
    assert evidence.announced_land_price_status == LandPriceFieldStatus.FIELD_NOT_AVAILABLE_FOR_YEAR
    assert "無「公告地價」欄位" in evidence.notes


# ---------------------------------------------------------------------------
# 4. unknown year schema
# ---------------------------------------------------------------------------

def test_unverified_year_schema_returns_unknown_without_touching_cache(tmp_path):
    cache = _fresh_cache(tmp_path)  # would raise if touched (nothing synced)
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="999")

    assert evidence.announced_land_current_value_status == LandPriceFieldStatus.UNKNOWN
    assert evidence.announced_land_price_status == LandPriceFieldStatus.UNKNOWN
    assert evidence.dataset_id is None


# ---------------------------------------------------------------------------
# 5. land number match
# ---------------------------------------------------------------------------

def test_exact_land_no_match_returns_available_value(tmp_path):
    cache = _synced_cache(tmp_path, "板橋區", [
        {"district": "板橋區", "segment": "忠孝段", "lid": "00010001", "official_value_busiprval": "999999"},
        {"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"},
    ])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")
    assert evidence.announced_land_current_value == Decimal("278000")


# ---------------------------------------------------------------------------
# 6. snapshot unavailable
# ---------------------------------------------------------------------------

def test_snapshot_unavailable_returns_unknown_with_sync_instruction(tmp_path):
    cache = _fresh_cache(tmp_path)
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")

    assert evidence.announced_land_current_value_status == LandPriceFieldStatus.UNKNOWN
    assert "sync_land_price_dataset.py" in evidence.notes
    assert "涵蓋「板橋區」" in evidence.notes


# ---------------------------------------------------------------------------
# 7. snapshot checksum / 8. record count (Snapshot Contract)
# ---------------------------------------------------------------------------

def test_snapshot_checksum_and_record_count_populated_on_hit(tmp_path):
    cache = _synced_cache(tmp_path, "板橋區", [
        {"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"},
    ])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")

    assert evidence.checksum is not None
    assert evidence.record_count == 1
    assert evidence.local_snapshot_version is not None
    assert evidence.schema_version is not None


def test_snapshot_checksum_populated_on_not_found_too(tmp_path):
    """A synced-but-empty scope still has real, non-fabricated provenance
    -- checksum/record_count are properties of the SNAPSHOT, not of
    whether this one lookup happened to match."""
    cache = _synced_cache(tmp_path, "板橋區", [])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")
    assert evidence.checksum is not None
    assert evidence.record_count == 0


def test_no_fabricated_snapshot_provenance_when_never_synced(tmp_path):
    cache = _fresh_cache(tmp_path)
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")
    assert evidence.local_snapshot_version is None
    assert evidence.checksum is None
    assert evidence.record_count is None


# ---------------------------------------------------------------------------
# 9. local indexed lookup (basic sanity: two different districts don't leak)
# ---------------------------------------------------------------------------

def test_lookup_is_scoped_per_district_no_cross_contamination(tmp_path):
    cache = _fresh_cache(tmp_path)
    cache.write_land_price_snapshot(
        dataset_id=DATASET_ID_114, dataset_name="x", dataset_year="114",
        source_url="u", source_authority="a", scope_key="板橋區",
        records=[{"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "111"}],
    )
    cache.write_land_price_snapshot(
        dataset_id=DATASET_ID_114, dataset_name="x", dataset_year="114",
        source_url="u", source_authority="a", scope_key="金山區",
        records=[{"district": "金山區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "222"}],
    )
    ctx_banqiao = ProviderContext(case_no="T", city="新北市", district="板橋區", segment_code="X", parcel_id="忠孝段1地號")
    ctx_jinshan = ProviderContext(case_no="T", city="新北市", district="金山區", segment_code="X", parcel_id="忠孝段1地號")

    ev1 = lpp.RealLandPriceProvider(cache=cache).query_land_price(ctx_banqiao, year="114")
    ev2 = lpp.RealLandPriceProvider(cache=cache).query_land_price(ctx_jinshan, year="114")

    assert ev1.announced_land_current_value == Decimal("111")
    assert ev2.announced_land_current_value == Decimal("222")


# ---------------------------------------------------------------------------
# Golden Case normalization
# ---------------------------------------------------------------------------

def test_golden_case_identifier_never_uses_price_segment_code(tmp_path):
    golden_ctx = ProviderContext(
        case_no="1140901-99-001", city="新北市", district="金山區", segment_code="P002-00",
        parcel_id="金美段489地號",
    )
    cache = _synced_cache(tmp_path, "金山區", [])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(golden_ctx, year="114")

    assert evidence.segment == "金美段"
    assert evidence.segment != "P002-00"
    assert evidence.land_no == "04890000"  # LandNumberNormalizer.to_land_price_lid(489, 0)


def test_parse_uncertain_identifier_short_circuits(tmp_path):
    bad_ctx = ProviderContext(
        case_no="T", city="新北市", district="金山區", segment_code="P002-00", parcel_id="P002-00",
    )
    cache = _fresh_cache(tmp_path)  # would raise if touched
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(bad_ctx, year="114")
    assert "PARSE_UNCERTAIN" in evidence.notes
    assert evidence.segment is None


# ---------------------------------------------------------------------------
# 14. no silent Mock fallback
# ---------------------------------------------------------------------------

def test_real_provider_never_falls_back_to_mock(tmp_path):
    cache = _fresh_cache(tmp_path)
    real_evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")
    mock_evidence = lpp.MockLandPriceProvider().query_land_price(CTX)

    assert real_evidence.dataset_id == DATASET_ID_114
    assert mock_evidence.dataset_id is None
    assert real_evidence.notes != mock_evidence.notes


def test_mock_fetch_is_fully_offline(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("MockLandPriceProvider must never call the network")
    monkeypatch.setattr(lpp, "_http_get_json", _boom)
    points = lpp.MockLandPriceProvider().fetch(CTX)
    assert points[0].value is None
    assert points[0].source_type == "Mock"


# ---------------------------------------------------------------------------
# 16. old runtime paging path does not silently override snapshot result
# ---------------------------------------------------------------------------

def test_primary_path_never_invokes_live_scan(tmp_path, monkeypatch):
    def _boom(url):
        raise AssertionError("query_land_price() must never touch the network")
    monkeypatch.setattr(lpp, "_http_get_json", _boom)

    cache = _synced_cache(tmp_path, "板橋區", [
        {"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"},
    ])
    evidence = lpp.RealLandPriceProvider(cache=cache).query_land_price(CTX, year="114")
    assert evidence.announced_land_current_value == Decimal("278000")


def test_live_scan_fallback_is_a_separate_named_method(monkeypatch):
    def _page(url):
        return [{"district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"}]
    monkeypatch.setattr(lpp, "_http_get_json", _page)

    evidence = lpp.RealLandPriceProvider().query_land_price_via_live_scan(CTX, "忠孝段", "00010000", year="114")
    assert evidence.announced_land_current_value == Decimal("278000")


def test_schema_adapter_is_dict_driven():
    assert "114" in lpp._YEAR_SCHEMAS
    schema_114 = lpp._YEAR_SCHEMAS["114"]
    assert schema_114["land_price_field"] is None
    assert schema_114["current_value_field"] == "official_value_busiprval"
    assert schema_114["land_no_field"] == "lid"
