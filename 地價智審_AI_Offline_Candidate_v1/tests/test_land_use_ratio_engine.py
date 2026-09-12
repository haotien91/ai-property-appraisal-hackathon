# -*- coding: utf-8 -*-
"""
Tests for engine/land_use_ratio_engine.py's two-layer 建蔽率／容積率 lookup:

Layer 1 (data/rules/ntpc_common_zone_ratios.json): 都市計畫法新北市施行
細則附表一, a genuine citywide ceiling table verified against the actual
article text (第三十六條) via clean PDF text-layer extraction (not image
transcription -- this source PDF has no font-encoding issue).

Layer 2 (data/rules/plan_zone_floor_area_ratios.json): per-plan 容積率
facts. Currently exactly one CONFIRMED entry (金山都市計畫／第二種商業區／
240%), sourced from 變更金山細部計畫（土地使用分區管制要點專案通盤檢討）書
(新北市政府, 民國109年11月) and cross-checked against Golden Case's own
70%/240% values.

These tests lock in: the fallback priority chain never silently
generalizes one plan's number to another, 建蔽率/容積率's DEFERRED_TO_*
cells never produce a fabricated number, and the two source datasets
still schema-validate and contain the expected entries.
"""
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    LandUseRatioMetric, LandUseRatioCellState, NtpcCommonZoneRatioEntry,
    NtpcCommonZoneRatioDataset, NtpcCommonZoneRatioSourceDocument,
    PlanZoneFloorAreaRatioEntry, FloorAreaRatioRuleStatus,
)
from engine.land_use_ratio_engine import (  # noqa: E402
    LandUseRatioEngine, LandUseRatioDatasetError,
    load_common_zone_ratio_dataset, load_plan_zone_floor_area_ratios,
)


@pytest.fixture(scope="module")
def engine():
    return LandUseRatioEngine()


class TestDatasetsLoadAndValidate:
    def test_common_dataset_loads(self):
        ds = load_common_zone_ratio_dataset()
        assert isinstance(ds, NtpcCommonZoneRatioDataset)

    def test_common_dataset_entry_count(self):
        ds = load_common_zone_ratio_dataset()
        # 19 zone rows x 2 metrics, +1 extra for 旅館區's terrain-split 容積率
        assert len(ds.entries) == 19 * 2 + 1

    def test_common_dataset_source_citation(self):
        ds = load_common_zone_ratio_dataset()
        src = ds.source_document
        assert src.document_name == "都市計畫法新北市施行細則"
        assert src.issuing_agency == "新北市政府"
        assert len(src.source_pdf_sha256) == 64
        assert src.source_pdf_byte_size > 0

    def test_plan_entries_load(self):
        entries = load_plan_zone_floor_area_ratios()
        assert len(entries) == 1
        assert entries[0].plan_id == "jinshan"

    def test_missing_common_file_raises_dataset_error(self):
        with pytest.raises(LandUseRatioDatasetError):
            load_common_zone_ratio_dataset("does/not/exist.json")


class TestEvidenceFieldsArePreserved:
    """Item 5's explicit list: every resolution must carry enough to trace
    back to its exact source, not just a human-readable notes string."""

    def test_ntpc_common_layer_result_carries_full_citation(self, engine):
        r = engine.resolve_building_coverage_rate("商業區")
        assert r.legal_source == "都市計畫法新北市施行細則"
        assert r.article_or_section == "第三十六條、附表一"
        assert r.source_url.startswith("https://www.planning.ntpc.gov.tw/")
        assert len(r.source_document_checksum) == 64
        assert r.dataset_version == "民國111年3月16日修正"

    def test_plan_specific_result_carries_full_citation(self, engine):
        r = engine.resolve_floor_area_ratio("第二種商業區", plan_id="jinshan")
        assert r.official_plan_name == "金山都市計畫"
        assert r.plan_id == "jinshan"
        assert "變更金山細部計畫" in r.legal_source
        assert "第五點" in r.article_or_section
        assert r.source_url.startswith("https://www.planning.ntpc.gov.tw/")
        assert len(r.source_document_checksum) == 64
        assert r.dataset_version == "中華民國109年11月"

    def test_unavailable_result_carries_no_fabricated_citation(self, engine):
        r = engine.resolve_floor_area_ratio("商業區")
        assert r.legal_source is None
        assert r.source_url is None
        assert r.source_document_checksum is None

    def test_missing_plan_file_raises_dataset_error(self):
        with pytest.raises(LandUseRatioDatasetError):
            load_plan_zone_floor_area_ratios("does/not/exist.json")


class TestBuildingCoverageRateLookup:
    def test_commercial_zone_matches_golden_case(self, engine):
        """商業區建蔽率70% -- also cross-checked against Golden Case's own
        70% (data/golden/golden_case_input.py), though the source citation
        is the施行細則 itself, not merely "matches Golden Case"."""
        r = engine.resolve_building_coverage_rate("商業區")
        assert r.resolved_value_pct == Decimal("70")
        assert r.resolution_layer == "NTPC_COMMON"
        assert r.requires_manual_review is False

    def test_residential_zone(self, engine):
        r = engine.resolve_building_coverage_rate("住宅區")
        assert r.resolved_value_pct == Decimal("50")

    def test_conservation_zone_caveat_surfaced_in_notes(self, engine):
        r = engine.resolve_building_coverage_rate("保存區、古蹟保存區")
        assert r.resolved_value_pct == Decimal("60")
        assert "原有建築物已超過者" in r.notes

    def test_unknown_zone_name_is_unavailable_not_a_guess(self, engine):
        r = engine.resolve_building_coverage_rate("完全不存在的分區")
        assert r.resolved_value_pct is None
        assert r.resolution_layer == "UNAVAILABLE"
        assert r.requires_manual_review is True


class TestFloorAreaRatioLayer1CommonFallback:
    def test_industrial_zone_has_real_common_value(self, engine):
        """工業區容積率210%是附表一真正的固定值（非依都市計畫書訂定），
        沒有plan_id時仍應能直接從Layer 1解出。"""
        r = engine.resolve_floor_area_ratio("工業區")
        assert r.resolved_value_pct == Decimal("210")
        assert r.resolution_layer == "NTPC_COMMON"

    def test_residential_and_commercial_zones_have_no_common_value(self, engine):
        """住宅區／商業區容積率在附表一本身即註明依都市計畫書訂定，
        沒有plan_id、也沒有Layer 2登記時，必須誠實回報UNAVAILABLE，
        不得回退為任何固定百分比。"""
        for zone in ("住宅區", "商業區"):
            r = engine.resolve_floor_area_ratio(zone)
            assert r.resolved_value_pct is None, zone
            assert r.resolution_layer == "UNAVAILABLE", zone
            assert r.rule_status == "UNAVAILABLE_PER_PLAN"
            assert r.requires_manual_review is True

    def test_hotel_zone_terrain_split_not_modeled_as_single_common_lookup(self, engine):
        """旅館區容積率依山坡地/平地而不同，engine目前的resolve_floor_area_
        ratio不支援terrain_qualifier參數，這代表它必須誠實回報UNAVAILABLE
        而非隨意挑一個地形的數字。"""
        r = engine.resolve_floor_area_ratio("旅館區")
        assert r.resolved_value_pct is None
        assert r.resolution_layer == "UNAVAILABLE"


class TestFloorAreaRatioLayer2PlanSpecific:
    def test_jinshan_second_commercial_zone_resolves_to_240(self, engine):
        r = engine.resolve_floor_area_ratio("第二種商業區", plan_id="jinshan")
        assert r.resolved_value_pct == Decimal("240")
        assert r.resolution_layer == "PLAN_SPECIFIC"
        assert r.rule_status == "CONFIRMED"
        assert r.requires_manual_review is True  # still flagged -- see entry's own notes
        assert "金山" in r.notes

    def test_plan_specific_entry_takes_priority_over_common_layer(self, engine):
        """第二種商業區本身不在附表一內（附表一只有籠統的「商業區」），
        所以這個案例本來就不會撞到Layer 1；改用一個確實兩層都有資料的
        情境驗證優先序：假設「商業區」本身在某個(假造)plan_id下也有登記，
        Layer 2必須贏過Layer 1的210-like固定值。這裡直接用注入的Engine
        實例（非讀真實檔案）建構這個情境，避免動到正式資料集。"""
        common = load_common_zone_ratio_dataset()
        plan_entries = [PlanZoneFloorAreaRatioEntry(
            plan_id="test_plan", plan_name="測試都市計畫", zone_name="工業區",
            floor_area_ratio_pct=Decimal("999"), rule_status=FloorAreaRatioRuleStatus.CONFIRMED,
            document_title="test doc", document_agency="test", document_date="test",
            article_or_section="test", source_url="https://example.com", source_pdf_sha256="0" * 64,
            requires_manual_review=False,
        )]
        eng = LandUseRatioEngine(common_dataset=common, plan_entries=plan_entries)
        r = eng.resolve_floor_area_ratio("工業區", plan_id="test_plan")
        assert r.resolved_value_pct == Decimal("999")  # Layer 2 wins even though Layer 1 has 210
        assert r.resolution_layer == "PLAN_SPECIFIC"

    def test_unregistered_plan_id_never_borrows_another_plans_number(self, engine):
        """關鍵防呆：同一個分區名稱「第二種商業區」在一個未登記的
        都市計畫（如「banqiao」）下查詢，絕對不能沿用金山的240%。"""
        r = engine.resolve_floor_area_ratio("第二種商業區", plan_id="banqiao")
        assert r.resolved_value_pct is None
        assert r.resolution_layer == "UNAVAILABLE"
        assert r.rule_status == "UNAVAILABLE_PER_PLAN"
        assert r.requires_manual_review is True

    def test_zone_name_only_in_plan_registry_without_plan_id_is_unavailable(self, engine):
        """沒有提供plan_id時，即使「第二種商業區」剛好在Layer 2某處有登記，
        也不應該被意外撈到（避免在多個都市計畫都有同名分區時撞錯筆）。"""
        r = engine.resolve_floor_area_ratio("第二種商業區")
        assert r.resolved_value_pct is None
        assert r.resolution_layer == "UNAVAILABLE"


class TestCellStateNullabilityInvariant:
    def test_value_state_without_pct_raises(self):
        with pytest.raises(Exception):
            NtpcCommonZoneRatioEntry(
                zone_name="商業區", metric=LandUseRatioMetric.BUILDING_COVERAGE_RATE,
                cell_state=LandUseRatioCellState.VALUE, value_pct=None,
            )

    def test_deferred_state_with_pct_raises(self):
        with pytest.raises(Exception):
            NtpcCommonZoneRatioEntry(
                zone_name="商業區", metric=LandUseRatioMetric.FLOOR_AREA_RATIO,
                cell_state=LandUseRatioCellState.DEFERRED_TO_PLAN_DOCUMENT, value_pct=Decimal("240"),
            )


class TestPlanEntryConfirmedRequiresFullCitation:
    def test_confirmed_without_source_url_raises(self):
        with pytest.raises(Exception):
            PlanZoneFloorAreaRatioEntry(
                plan_id="x", plan_name="x都市計畫", zone_name="商業區",
                floor_area_ratio_pct=Decimal("100"), rule_status=FloorAreaRatioRuleStatus.CONFIRMED,
                document_title="doc", document_date="2026", article_or_section="第一點",
                source_url=None, source_pdf_sha256="0" * 64,
            )

    def test_unavailable_status_with_a_value_raises(self):
        """A non-CONFIRMED entry must never carry a concrete percentage --
        that would let a placeholder or draft number leak through as if
        it were usable."""
        with pytest.raises(Exception):
            PlanZoneFloorAreaRatioEntry(
                plan_id="x", plan_name="x都市計畫", zone_name="商業區",
                floor_area_ratio_pct=Decimal("100"),
                rule_status=FloorAreaRatioRuleStatus.UNAVAILABLE_PER_PLAN,
            )

    def test_source_unconfirmed_status_is_valid_without_citation(self):
        e = PlanZoneFloorAreaRatioEntry(
            plan_id="x", plan_name="x都市計畫", zone_name="商業區",
            rule_status=FloorAreaRatioRuleStatus.SOURCE_UNCONFIRMED,
            notes="Golden Case數值存在但官方來源尚未查證確認",
        )
        assert e.floor_area_ratio_pct is None


class TestGoldenCaseCrossCheck:
    """providers/land_use_provider.py's MockLandUseProvider._MOCK_VALUES
    hardcodes building_coverage_ratio=70 / floor_area_ratio=240 for
    land_use_zone="第二種商業區", independently of this new engine (those
    numbers were verified against 查估書表範本.pdf in an earlier phase of
    this project). This test proves the two now agree, without changing
    either source -- it does NOT wire the engine into MockLandUseProvider."""

    def test_engine_resolution_matches_mock_land_use_provider_constants(self, engine):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         "providers"))
        from land_use_provider import MockLandUseProvider  # noqa: E402

        mock_bcr, _ = MockLandUseProvider._MOCK_VALUES["building_coverage_ratio"]
        mock_far, _ = MockLandUseProvider._MOCK_VALUES["floor_area_ratio"]

        bcr = engine.resolve_building_coverage_rate("商業區")
        far = engine.resolve_floor_area_ratio("第二種商業區", plan_id="jinshan")
        assert bcr.resolved_value_pct == Decimal(str(mock_bcr)) == Decimal("70")
        assert far.resolved_value_pct == Decimal(str(mock_far)) == Decimal("240")
