# -*- coding: utf-8 -*-
"""
Tests for the digitized central max-adjustment-range dataset
(data/rules/central_max_adjustment_range.json -- 內政部104年1月30日台內地字
第10413006723號令附表) and its loader/validator
(engine/central_max_range_validator.py).

Spot-check values below were re-verified against high-resolution crops of
the source PDF's rendered pages during transcription (not re-derived here
-- this file locks in that the shipped JSON still matches that transcription,
catching any accidental edit/corruption of the data file itself).
"""
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    CentralMaxRangeCellState, CentralMaxRangeEntry, CentralMaxRangeDataset,
    CentralMaxRangeSourceDocument, LocalToCentralMappingStatus,
)
from engine.central_max_range_validator import (  # noqa: E402
    load_central_max_range_dataset, load_local_mapping_statuses,
    CentralMaxRangeDatasetValidator, CentralMaxRangeDatasetError,
)


@pytest.fixture(scope="module")
def dataset():
    return load_central_max_range_dataset()


@pytest.fixture(scope="module")
def by_key(dataset):
    """(table_type, land_use_type, subgrade, item_name or major_category_name
    when item_name is None) -> entry, for quick spot-checks."""
    d = {}
    for e in dataset.entries:
        key = (e.table_type, e.land_use_type, e.land_use_subgrade,
               e.item_name if e.item_name is not None else e.major_category_name)
        d[key] = e
    return d


class TestDatasetLoadsAndValidates:
    def test_loads_without_error(self, dataset):
        assert isinstance(dataset, CentralMaxRangeDataset)

    def test_entry_count(self, dataset):
        # individual: 20 rows x 5 land types = 100
        # regional: 住宅29x4 + 商業29x4 + 工業21x2 + 農業20x1 + 其他24x1 = 116+116+42+20+24 = 318
        assert len(dataset.entries) == 100 + 318

    def test_validator_reports_zero_issues_on_real_dataset(self, dataset):
        issues = CentralMaxRangeDatasetValidator().validate(dataset)
        assert issues == [], f"unexpected issues: {issues}"

    def test_source_document_citation_present(self, dataset):
        src = dataset.source_document
        assert src.issuing_agency == "內政部"
        assert src.document_number == "台內地字第10413006723號"
        assert src.gazette_publish_date == "20150130"
        assert src.effective_date == "1040301"
        assert src.source_url.startswith("https://gazette.nat.gov.tw/")
        assert len(src.source_pdf_sha256) == 64
        assert src.source_pdf_byte_size > 0
        assert src.digitization_method == "MANUAL_VISUAL_TRANSCRIPTION"

    def test_missing_file_raises_dataset_error_not_generic_exception(self):
        with pytest.raises(CentralMaxRangeDatasetError):
            load_central_max_range_dataset("does/not/exist.json")


class TestIndividualFactorTableSpotChecks:
    """商業用地 column, page 30 (item numbers per the source's own numbering)."""

    def test_area_item7(self, by_key):
        e = by_key[("individual", "商業用地", None, "面積")]
        assert e.max_range_pct == Decimal("10")
        assert e.cell_state == CentralMaxRangeCellState.VALUE
        assert e.major_category_code == "1"
        assert e.item_code == 7

    def test_frontage_item11_matches_regional_road_width_scale(self, by_key):
        """臨街情形 -- one of the larger max-range items."""
        e = by_key[("individual", "商業用地", None, "臨街情形")]
        assert e.max_range_pct == Decimal("30")

    def test_floor_area_ratio_item24(self, by_key):
        e = by_key[("individual", "商業用地", None, "容積率")]
        assert e.max_range_pct == Decimal("50")

    def test_dash_cell_for_agricultural_school_proximity(self, by_key):
        """農業用地's 接近學校之程度 is printed as "-" in the source."""
        e = by_key[("individual", "農業用地", None, "接近學校之程度")]
        assert e.cell_state == CentralMaxRangeCellState.DASH_NOT_APPLICABLE
        assert e.max_range_pct is None

    def test_category_level_other_row_has_no_item_code(self, by_key):
        e = by_key[("individual", "商業用地", None, "其他")]
        assert e.item_code is None
        assert e.item_name is None
        assert e.max_range_pct == Decimal("30")


class TestRegionalFactorTableSpotChecks:
    def test_residential_high_grade_road_width(self, by_key):
        e = by_key[("regional", "住宅用地", "高級住宅用地", "主要道路寬度")]
        assert e.max_range_pct == Decimal("25")

    def test_commercial_subgrades_differ_for_far_type_ratio(self, dataset):
        """建蔽率 across 商業用地's 4 subgrades -- confirms subgrade columns
        are genuinely distinct data, not accidentally all copies of one
        column."""
        rows = {
            e.land_use_subgrade: e.max_range_pct
            for e in dataset.entries
            if e.table_type == "regional" and e.land_use_type == "商業用地" and e.item_name == "建蔽率"
        }
        assert rows == {
            "高度商業用地": Decimal("15"), "中度商業用地": Decimal("15"),
            "普通商業用地": Decimal("15"), "村里鄰商業用地": Decimal("15"),
        }

    def test_commercial_floor_area_ratio_varies_by_subgrade(self, dataset):
        rows = {
            e.land_use_subgrade: e.max_range_pct
            for e in dataset.entries
            if e.table_type == "regional" and e.land_use_type == "商業用地" and e.item_name == "容積率"
        }
        assert rows == {
            "高度商業用地": Decimal("50"), "中度商業用地": Decimal("50"),
            "普通商業用地": Decimal("50"), "村里鄰商業用地": Decimal("40"),
        }

    def test_industrial_two_subgrades(self, dataset):
        rows = {
            e.land_use_subgrade: e.max_range_pct
            for e in dataset.entries
            if e.table_type == "regional" and e.land_use_type == "工業用地" and e.item_name == "主要道路寬度"
        }
        assert rows == {"大規模工業用地": Decimal("40"), "中小規模工業用地": Decimal("40")}

    def test_agricultural_no_subgrade(self, by_key):
        e = by_key[("regional", "農業用地", None, "保（排）水之良否")]
        assert e.max_range_pct == Decimal("30")

    def test_other_land_use_type_no_subgrade(self, by_key):
        e = by_key[("regional", "其他用地", None, "都市計畫（內、外）")]
        assert e.max_range_pct == Decimal("30")

    def test_blank_other_factors_row_every_land_use_type(self, dataset):
        """「其他影響因素」is a genuinely blank category row (no "-", no
        number) on every regional table -- must be BLANK_NO_DATA, not
        silently absent nor mistaken for DASH_NOT_APPLICABLE."""
        blanks = [
            e for e in dataset.entries
            if e.table_type == "regional" and e.major_category_name == "其他影響因素"
        ]
        # 住宅(4) + 商業(4) + 工業(2) + 農業(1) + 其他(1) = 12 subgrade columns total
        assert len(blanks) == 12
        for e in blanks:
            assert e.cell_state == CentralMaxRangeCellState.BLANK_NO_DATA
            assert e.max_range_pct is None
            assert e.item_code is None
            assert e.item_name is None


class TestCellStateNullabilityInvariant:
    def _minimal_kwargs(self, **overrides):
        kwargs = dict(
            table_type="individual", land_use_type="商業用地", land_use_subgrade=None,
            major_category_code="1", major_category_name="宗地條件",
            item_code=7, item_name="面積", cell_state=CentralMaxRangeCellState.VALUE,
            max_range_pct=Decimal("10"), source_page=30, source_table_title="test",
        )
        kwargs.update(overrides)
        return kwargs

    def test_value_state_without_pct_raises(self):
        with pytest.raises(Exception):
            CentralMaxRangeEntry(**self._minimal_kwargs(max_range_pct=None))

    def test_dash_state_with_pct_raises(self):
        with pytest.raises(Exception):
            CentralMaxRangeEntry(**self._minimal_kwargs(
                cell_state=CentralMaxRangeCellState.DASH_NOT_APPLICABLE, max_range_pct=Decimal("10"),
            ))

    def test_blank_state_with_pct_raises(self):
        with pytest.raises(Exception):
            CentralMaxRangeEntry(**self._minimal_kwargs(
                cell_state=CentralMaxRangeCellState.BLANK_NO_DATA, max_range_pct=Decimal("10"),
            ))

    def test_dash_state_without_pct_is_valid(self):
        e = CentralMaxRangeEntry(**self._minimal_kwargs(
            cell_state=CentralMaxRangeCellState.DASH_NOT_APPLICABLE, max_range_pct=None,
        ))
        assert e.max_range_pct is None


class TestValidatorCatchesInjectedProblems:
    """The real dataset passing with 0 issues (TestDatasetLoadsAndValidates)
    only proves the validator didn't complain -- these tests prove it would
    actually complain given a genuinely broken dataset."""

    def _entry(self, **overrides):
        kwargs = dict(
            table_type="regional", land_use_type="商業用地", land_use_subgrade="高度商業用地",
            major_category_code="1", major_category_name="土地使用管制",
            item_code=None, item_name="建蔽率", cell_state=CentralMaxRangeCellState.VALUE,
            max_range_pct=Decimal("15"), source_page=26, source_table_title="test",
        )
        kwargs.update(overrides)
        return CentralMaxRangeEntry(**kwargs)

    def _dataset(self, entries):
        return CentralMaxRangeDataset(
            source_document=CentralMaxRangeSourceDocument(
                source_pdf_sha256="0" * 64, source_pdf_byte_size=1,
                digitized_at="2026-01-01T00:00:00",
            ),
            entries=entries,
        )

    def test_unknown_land_use_type_is_error(self):
        ds = self._dataset([self._entry(land_use_type="奇怪用地")])
        issues = CentralMaxRangeDatasetValidator().validate(ds)
        assert any(i.severity == "ERROR" and "land_use_type" in i.message for i in issues)

    def test_duplicate_cell_is_error(self):
        ds = self._dataset([self._entry(), self._entry()])
        issues = CentralMaxRangeDatasetValidator().validate(ds)
        assert any(i.severity == "ERROR" and "重複" in i.message for i in issues)

    def test_asymmetric_subgrades_is_error(self):
        """高度商業用地 has 建蔽率, but 中度商業用地 is missing it entirely --
        a real transcription gap the validator must catch."""
        ds = self._dataset([
            self._entry(land_use_subgrade="高度商業用地", item_name="建蔽率"),
            self._entry(land_use_subgrade="中度商業用地", item_name="容積率", max_range_pct=Decimal("50")),
        ])
        issues = CentralMaxRangeDatasetValidator().validate(ds)
        assert any(i.severity == "ERROR" and "不對稱" in i.message for i in issues)

    def test_symmetric_subgrades_produce_no_asymmetry_error(self):
        ds = self._dataset([
            self._entry(land_use_subgrade="高度商業用地", item_name="建蔽率"),
            self._entry(land_use_subgrade="中度商業用地", item_name="建蔽率"),
        ])
        issues = CentralMaxRangeDatasetValidator().validate(ds)
        assert issues == []


class TestLocalMappingStatus:
    def test_loads_and_is_unmapped(self):
        mappings = load_local_mapping_statuses()
        assert len(mappings) == 2
        for m in mappings:
            assert isinstance(m, LocalToCentralMappingStatus)
            assert m.mapping_status == "UNMAPPED"
            assert m.mapped_central_land_use_type is None
            assert m.mapped_central_subgrade is None

    def test_covers_both_local_rule_files(self):
        mappings = load_local_mapping_statuses()
        files = {m.local_rule_file for m in mappings}
        assert files == {"data/rules/regional_rules.json", "data/rules/individual_rules.json"}
