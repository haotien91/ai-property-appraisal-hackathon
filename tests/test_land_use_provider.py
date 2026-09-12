# -*- coding: utf-8 -*-
"""Tests for providers/land_use_provider.py. RealLandUseProvider's zoning
lookup is stubbed (offline, no filesystem/DatasetRegistry touch) -- the
live end-to-end proof (real ~75MB shapefile, real point-in-polygon query)
was run manually during development; see
tests/test_sync_ntpc_zoning_dataset.py and providers/ntpc_zoning_provider.py
for the offline logic tests those pieces already carry on their own."""
import os
import sys
from datetime import datetime

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from land_use_provider import MockLandUseProvider, RealLandUseProvider  # noqa: E402
from ntpc_zoning_provider import RealNtpcZoningProvider  # noqa: E402
from domain.models import Coordinate, ZoningQueryResult  # noqa: E402

CTX = ProviderContext(
    case_no="TEST-001", city="新北市", district="金山區", segment_code="P002-00",
    center_coordinate=Coordinate(latitude=25.0, longitude=121.5),
)


def _ctx_with_plan(plan_id):
    return ProviderContext(
        case_no="TEST-001", city="新北市", district="金山區", segment_code="P002-00",
        center_coordinate=Coordinate(latitude=25.0, longitude=121.5), plan_id=plan_id,
    )


# Every field MockLandUseProvider covers except land_use_zone/
# building_coverage_ratio/floor_area_ratio (those three now have real
# resolution logic, covered by their own test classes below).
_OTHER_FIELDS = [
    "zoning_inside_outside",
    "construction_prohibited", "construction_restricted", "drainage_quality",
    "terrain", "sunlight", "view", "slope_degree", "wind", "soil_quality",
    "building_density", "building_type", "land_use_current",
]


class _StubZoningProvider:
    """Stands in for RealNtpcZoningProvider -- returns a canned
    ZoningQueryResult without touching DatasetRegistry/sqlite/shapely."""

    def __init__(self, result: ZoningQueryResult):
        self._result = result

    def query(self, center):
        return self._result


class TestMockLandUseProviderUnchanged:
    def test_returns_golden_case_zone(self):
        points = MockLandUseProvider().fetch(CTX)
        zone = next(p for p in points if p.field == "land_use_zone")
        assert zone.value == "第二種商業區"
        assert zone.source_type == "Mock"

    def test_field_count_matches_mock_values_table(self):
        points = MockLandUseProvider().fetch(CTX)
        assert len(points) == len(MockLandUseProvider._MOCK_VALUES)


class TestRealLandUseProviderZoneFound:
    def _provider(self, **overrides):
        fields = dict(
            zone_name="第二種商業區", plan_name=None, matched_polygon_count=1,
            dataset_version="2026-09-02", requires_manual_review=False,
            notes="圖資僅供參考，仍以發布實施之都市計畫書圖為準",
        )
        fields.update(overrides)
        return RealLandUseProvider(zoning_provider=_StubZoningProvider(ZoningQueryResult(**fields)))

    def test_land_use_zone_passes_through_raw_value_unmodified(self):
        points = self._provider().fetch(CTX)
        zone = next(p for p in points if p.field == "land_use_zone")
        assert zone.value == "第二種商業區"
        assert zone.source_type == "GovernmentOpenData"
        assert zone.confidence == "高"
        assert zone.coordinate == CTX.center_coordinate

    def test_source_and_notes_preserve_dataset_version_url_legal_status(self):
        points = self._provider().fetch(CTX)
        zone = next(p for p in points if p.field == "land_use_zone")
        assert "新北市政府城鄉發展局" in zone.source
        assert "2026-09-02" in zone.notes
        assert "data.ntpc.gov.tw" in zone.notes
        assert "reference_only" in zone.notes

    def test_manual_review_flag_downgrades_confidence_not_dropped(self):
        provider = self._provider(requires_manual_review=True, matched_polygon_count=2)
        points = provider.fetch(CTX)
        zone = next(p for p in points if p.field == "land_use_zone")
        assert zone.value == "第二種商業區"  # still surfaced, not withheld
        assert zone.confidence == "中"

    def test_other_fields_are_unknown_not_fallback_to_mock(self):
        points = self._provider().fetch(CTX)
        by_field = {p.field: p for p in points}
        for field in _OTHER_FIELDS:
            assert by_field[field].value is None
            assert by_field[field].confidence == "UNKNOWN"
            assert by_field[field].source_type == "UNKNOWN"

    def test_field_count_matches_mock_provider(self):
        real_points = self._provider().fetch(CTX)
        mock_points = MockLandUseProvider().fetch(CTX)
        assert len(real_points) == len(mock_points)


class TestBuildingCoverageAndFloorAreaRatioWiring:
    """Item 6's explicit checklist: zone normalization keeps the official
    raw value intact, 金山's confirmed 70%/240% resolve correctly, no
    cross-plan leakage, no plan_id never borrows a per-plan number, and
    nothing here ever falls back to Mock."""

    def _provider(self, zone_name="第二種商業區", **overrides):
        fields = dict(
            zone_name=zone_name, plan_name=None, matched_polygon_count=1,
            dataset_version="2026-09-02", requires_manual_review=False,
        )
        fields.update(overrides)
        return RealLandUseProvider(zoning_provider=_StubZoningProvider(ZoningQueryResult(**fields)))

    def test_second_commercial_zone_normalizes_but_land_use_zone_keeps_raw_value(self):
        """第二種商業區 classifies to 商業區 for Layer 1's lookup, but
        land_use_zone itself must still read the untouched official raw
        name -- normalization must never leak into that field."""
        points = self._provider().fetch(_ctx_with_plan(None))
        by_field = {p.field: p for p in points}
        assert by_field["land_use_zone"].value == "第二種商業區"
        assert "normalized_zone_category=商業區" in by_field["building_coverage_ratio"].notes
        assert "official_raw_zone_name=第二種商業區" in by_field["building_coverage_ratio"].notes

    def test_building_coverage_rate_resolves_from_common_layer_even_without_plan_id(self):
        points = self._provider().fetch(_ctx_with_plan(None))
        bcr = next(p for p in points if p.field == "building_coverage_ratio")
        assert bcr.value == 70.0
        assert bcr.unit == "%"
        assert bcr.source_type == "GovernmentOpenData"

    def test_jinshan_plan_resolves_confirmed_70_and_240(self):
        points = self._provider().fetch(_ctx_with_plan("jinshan"))
        by_field = {p.field: p for p in points}
        assert by_field["building_coverage_ratio"].value == 70.0
        assert by_field["floor_area_ratio"].value == 240.0
        assert by_field["floor_area_ratio"].source_type == "GovernmentOpenData"

    def test_different_plan_does_not_inherit_jinshan_240(self):
        """關鍵防呆：同一分區名稱在另一個都市計畫下，不得沿用金山的240%。"""
        points = self._provider().fetch(_ctx_with_plan("banqiao"))
        far = next(p for p in points if p.field == "floor_area_ratio")
        assert far.value is None
        assert far.confidence == "UNKNOWN"

    def test_no_plan_id_never_borrows_240_either(self):
        points = self._provider().fetch(_ctx_with_plan(None))
        far = next(p for p in points if p.field == "floor_area_ratio")
        assert far.value is None
        assert far.confidence == "UNKNOWN"
        assert far.source_type == "UNKNOWN"

    def test_floor_area_ratio_missing_rule_never_falls_back_to_mock(self):
        """商業區(無plan_id)在Mock模式下會是240（Golden Case值），但real
        模式查無官方依據時絕不可回退成同一個數字——必須是None。"""
        points = self._provider().fetch(_ctx_with_plan(None))
        mock_points = MockLandUseProvider().fetch(CTX)
        mock_far = next(p for p in mock_points if p.field == "floor_area_ratio").value
        real_far = next(p for p in points if p.field == "floor_area_ratio").value
        assert mock_far == 240  # Mock still returns the Golden Case number...
        assert real_far is None  # ...but real must not silently copy it

    def test_unclassifiable_zone_flags_building_coverage_ratio_for_manual_review(self):
        """道路用地等公共設施用地類無法歸類到附表一的19種分區，建蔽率查詢
        必須誠實回報UNKNOWN，不得亂猜一個類別。"""
        points = self._provider(zone_name="道路用地").fetch(_ctx_with_plan(None))
        bcr = next(p for p in points if p.field == "building_coverage_ratio")
        assert bcr.value is None
        assert bcr.confidence == "UNKNOWN"
        assert "無法可靠歸類" in bcr.notes

    def test_zone_not_found_leaves_both_ratios_unknown(self):
        points = self._provider(
            zone_name=None, notes="座標落於已知分區圖資範圍外",
        ).fetch(_ctx_with_plan("jinshan"))
        by_field = {p.field: p for p in points}
        assert by_field["building_coverage_ratio"].value is None
        assert by_field["floor_area_ratio"].value is None


class TestRealLandUseProviderZoneNotFound:
    def test_no_match_is_unknown_not_fabricated(self):
        result = ZoningQueryResult(
            zone_name=None, plan_name=None, matched_polygon_count=0,
            notes="座標落於已知分區圖資範圍外，或該處無分區資料",
        )
        provider = RealLandUseProvider(zoning_provider=_StubZoningProvider(result))
        points = provider.fetch(CTX)
        zone = next(p for p in points if p.field == "land_use_zone")
        assert zone.value is None
        assert zone.confidence == "UNKNOWN"
        assert "範圍外" in zone.notes

    def test_default_construction_uses_real_ntpc_zoning_provider(self, monkeypatch, tmp_path):
        """Not stubbed here -- proves the wiring itself (no injected
        zoning_provider) resolves to the real class. DATASET_REGISTRY_DB_PATH
        is redirected to tmp_path so this does not write into the actual
        project's data/ directory (see providers/dataset_registry.py)."""
        monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
        import land_use_provider as mod
        provider = mod.RealLandUseProvider()
        assert isinstance(provider._zoning_provider, mod.RealNtpcZoningProvider)


class TestGisEvidenceTraceabilityToDatasetRegistry:
    """regional_land_use_zone's Evidence must be able to answer "which
    official GIS snapshot was this determination based on" -- not just a
    human-readable dataset_version string, but a chain a script could
    actually walk: Evidence -> dataset_id -> DatasetRegistry.
    get_current_snapshot(dataset_id) -> checksum/source_url/local_path.
    Runs RealNtpcZoningProvider for real (a real sqlite zoning_polygons
    table + a real DatasetRegistry, both in tmp_path -- only the ~75MB
    production download is skipped, matching this file's own documented
    stubbing convention) rather than hand-constructing a ZoningQueryResult,
    so this proves the actual production code path sets dataset_id, not
    just that the field exists on the model."""

    def _build_zoning_snapshot(self, tmp_path, zone_name="第二種商業區", version="2026-09-03"):
        import sqlite3
        from shapely.geometry import box
        from providers.dataset_registry import DatasetRegistry, compute_file_checksum
        from domain.models import DatasetSnapshotInfo

        db_path = str(tmp_path / f"zoning_{version}.sqlite3")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE zoning_polygons (
                id INTEGER PRIMARY KEY AUTOINCREMENT, zone_name TEXT NOT NULL, plan_name TEXT,
                plan_name_note TEXT,
                geometry_wkb BLOB NOT NULL, min_lon REAL NOT NULL, max_lon REAL NOT NULL,
                min_lat REAL NOT NULL, max_lat REAL NOT NULL
            )"""
        )
        # A 1-degree square around the test coordinate (25.0, 121.5) -- more
        # than large enough to contain it with no ambiguity.
        poly_wkb = box(121.0, 24.5, 122.0, 25.5).wkb
        conn.execute(
            "INSERT INTO zoning_polygons "
            "(zone_name, plan_name, plan_name_note, geometry_wkb, min_lon, max_lon, min_lat, max_lat) "
            "VALUES (?, NULL, NULL, ?, 121.0, 122.0, 24.5, 25.5)",
            (zone_name, poly_wkb),
        )
        conn.commit()
        conn.close()

        registry_db_path = str(tmp_path / f"registry_{version}.sqlite3")
        registry = DatasetRegistry(db_path=registry_db_path)
        snapshot = DatasetSnapshotInfo(
            dataset_id="ntpc_zoning", source_name="新北市都市計畫土地使用分區及範圍圖",
            source_agency="新北市政府城鄉發展局",
            source_url="https://data.ntpc.gov.tw/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5",
            local_snapshot_version=version, local_path=db_path,
            checksum=compute_file_checksum(db_path), refresh_policy="quarterly",
            last_synced_at=datetime.now(),
        )
        registry.register_snapshot(snapshot)
        return registry, snapshot

    def test_evidence_dataset_id_locates_the_registry_row_and_its_checksum(self, tmp_path):
        registry, snapshot = self._build_zoning_snapshot(tmp_path)
        real_zoning_provider = RealNtpcZoningProvider(registry=registry)
        provider = RealLandUseProvider(zoning_provider=real_zoning_provider)

        points = provider.fetch(CTX)
        zone = next(p for p in points if p.field == "land_use_zone")
        assert zone.value == "第二種商業區"

        # The evidence trail a human/script actually has: the composed
        # notes string (NormalizedDataPoint has no first-class dataset_id
        # field yet -- see providers/land_use_provider.py's existing
        # composition convention).
        assert "dataset_id=ntpc_zoning" in zone.notes
        assert "資料集版本=2026-09-03" in zone.notes

        # Walk the chain for real: dataset_id -> DatasetRegistry -> checksum.
        located = registry.get_current_snapshot("ntpc_zoning")
        assert located is not None
        assert located.checksum == snapshot.checksum
        assert located.local_snapshot_version == "2026-09-03"  # still the CURRENT snapshot for this evidence
        assert located.source_url == "https://data.ntpc.gov.tw/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5"

    def test_superseded_snapshot_version_is_an_honest_gap_not_silently_papered_over(self, tmp_path):
        """DatasetRegistry keeps only the CURRENT row per dataset_id
        (upsert, no history table -- see providers/dataset_registry.py's
        ON CONFLICT DO UPDATE). If the dataset is re-synced after some
        Evidence was recorded, that Evidence's dataset_version no longer
        matches what get_current_snapshot() returns -- its EXACT historical
        checksum is genuinely unrecoverable under the current design. This
        test locks in that this is surfaced as a detectable mismatch
        (dataset_version != the newly-registered version), not silently
        treated as still-traceable."""
        registry, old_snapshot = self._build_zoning_snapshot(tmp_path, version="2026-01-01")
        real_zoning_provider = RealNtpcZoningProvider(registry=registry)
        old_result = real_zoning_provider.query(CTX.center_coordinate)
        assert old_result.dataset_version == "2026-01-01"

        # Simulate a later re-sync: register a NEWER snapshot (genuinely
        # different file content, so a genuinely different checksum) under
        # the same dataset_id (upsert overwrites the row).
        _, new_snapshot = self._build_zoning_snapshot(tmp_path, zone_name="第一種住宅區", version="2026-06-01")
        registry.register_snapshot(new_snapshot)

        current = registry.get_current_snapshot("ntpc_zoning")
        assert current.local_snapshot_version == "2026-06-01"
        # The OLD evidence's version no longer matches the registry's
        # current row -- its exact checksum at that point in time is not
        # retrievable from DatasetRegistry as currently designed.
        assert old_result.dataset_version != current.local_snapshot_version
        assert current.checksum != old_snapshot.checksum  # different file, different checksum
