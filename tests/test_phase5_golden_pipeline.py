# -*- coding: utf-8 -*-
"""
Phase 5 Golden Case test: Input -> Data -> Rule -> Calculation -> Completed
Form -> PDF. Verifies the PDF is openable, has a reasonable page count, has
extractable/visible text, and that key traceable values (e.g. the
post-BLK-01-fix rule_id for 建蔽率) appear in the rendered output.
"""
import sys
import os
from decimal import Decimal

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "engine", "providers", "pdf", os.path.join("data", "golden")):
    sys.path.insert(0, os.path.join(REPO_ROOT, p) if p else REPO_ROOT)

from rule_engine import RuleEngine  # noqa: E402
from engine.grade_engine import GradeEngine  # noqa: E402
from engine.adjustment_engine import AdjustmentEngine  # noqa: E402
from engine.calculation_engine import CalculationEngine  # noqa: E402
from engine.form_completion_engine import FormCompletionEngine  # noqa: E402
from engine.geo_distance_engine import GeoDistanceEngine, DistanceMethod  # noqa: E402
import engine.geo_distance_engine as geo_distance_engine_module  # noqa: E402
from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402
from pdf.pdf_renderer import PdfRenderer, build_table1_pdf_bytes  # noqa: E402

from providers.base import ProviderContext  # noqa: E402
from providers.land_use_provider import MockLandUseProvider  # noqa: E402
from providers.road_provider import MockRoadProvider  # noqa: E402
from providers.transportation_provider import MockTransportationProvider  # noqa: E402
from providers.public_facility_provider import MockPublicFacilityProvider  # noqa: E402
from providers.special_facility_provider import MockSpecialFacilityProvider  # noqa: E402
from providers.environmental_provider import MockEnvironmentalProvider  # noqa: E402
from providers.commercial_activity_provider import MockCommercialActivityProvider  # noqa: E402

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


ALL_PROVIDERS = [
    MockLandUseProvider, MockRoadProvider, MockTransportationProvider,
    MockPublicFacilityProvider, MockSpecialFacilityProvider,
    MockEnvironmentalProvider, MockCommercialActivityProvider,
]


@pytest.fixture(scope="module")
def rule_engine_instance():
    import json
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return RuleEngine(reg + ind)


@pytest.fixture(scope="module")
def golden_form_result(rule_engine_instance):
    fce = FormCompletionEngine(
        GradeEngine(rule_engine_instance), AdjustmentEngine(rule_engine_instance), CalculationEngine()
    )
    return fce.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)


# ---------------------------------------------------------------------
# Part 1: Data Providers
# ---------------------------------------------------------------------
class TestDataProviders:
    def test_all_seven_providers_instantiate_and_fetch(self):
        ctx = ProviderContext(case_no=GOLDEN_CASE.case_no, city=GOLDEN_CASE.city,
                               district=GOLDEN_CASE.district, segment_code=GOLDEN_CASE.segment_code)
        total_points = 0
        for ProviderCls in ALL_PROVIDERS:
            points = ProviderCls().fetch(ctx)
            assert len(points) > 0
            total_points += len(points)
        assert total_points > 100

    def test_unavailable_data_returns_unknown_not_fabricated(self):
        """Providers must never invent a value for missing data."""
        ctx = ProviderContext(case_no=GOLDEN_CASE.case_no, city=GOLDEN_CASE.city,
                               district=GOLDEN_CASE.district, segment_code=GOLDEN_CASE.segment_code)
        points = MockPublicFacilityProvider().fetch(ctx)
        unknowns = [p for p in points if p.value is None]
        assert len(unknowns) > 0
        for p in unknowns:
            assert p.confidence == "UNKNOWN"
            assert p.notes  # must explain WHY, never a silent None

    def test_every_data_point_has_full_normalized_schema(self):
        ctx = ProviderContext(case_no=GOLDEN_CASE.case_no, city=GOLDEN_CASE.city,
                               district=GOLDEN_CASE.district, segment_code=GOLDEN_CASE.segment_code)
        points = MockLandUseProvider().fetch(ctx)
        for p in points:
            assert p.field
            assert p.source
            assert p.source_type
            assert p.confidence
            assert p.retrieved_at is not None
            # coordinate is Optional by design (Sources don't provide lat/lon
            # for named facilities) -- must be None, never a fabricated pair
            if p.coordinate is not None:
                assert p.coordinate.latitude is not None


# ---------------------------------------------------------------------
# Part 2: GeoDistanceEngine
# ---------------------------------------------------------------------
class TestGeoDistanceEngine:
    def test_haversine_matches_constructed_known_distance(self):
        """Independently constructs a destination point at a mathematically
        exact 700m due north of a real (Wikipedia-sourced) anchor, then
        verifies straight_line_distance recovers ~700m."""
        import math
        from domain.models import Coordinate

        engine = GeoDistanceEngine()
        anchor = Coordinate(latitude=25.23611, longitude=121.61750)  # 金山區中心, Wikipedia
        R = 6371000.0
        d = 700.0
        bearing = 0.0  # due north
        lat1 = math.radians(anchor.latitude)
        lat2 = math.asin(math.sin(lat1) * math.cos(d / R) + math.cos(lat1) * math.sin(d / R) * math.cos(bearing))
        dest = Coordinate(latitude=math.degrees(lat2), longitude=anchor.longitude)

        result = engine.straight_line_distance(anchor, "錨點", dest, "構造測試點")
        assert result.status == "COMPLETED"
        assert abs(result.distance_m - 700.0) < 0.5

    def test_missing_coordinate_is_manual_review_not_fabricated(self):
        from domain.models import Coordinate
        engine = GeoDistanceEngine()
        known = Coordinate(latitude=25.0, longitude=121.0)
        unknown = Coordinate(latitude=None, longitude=None)
        result = engine.straight_line_distance(known, "起點", unknown, "無座標終點")
        assert result.status == "MANUAL_REVIEW_REQUIRED"
        assert result.distance_m is None

    def test_route_distance_completes_via_osrm(self, monkeypatch):
        """Phase 1 REQ-010 (second confirmation: "最近的步行距離") requires
        walking ROUTE distance for convenience-type facilities -- this is
        now implemented via OSRM's public foot-routing API (see
        engine/geo_distance_engine.py's module docstring for why an earlier
        version of this test/engine assumed no network access was possible
        here, before that assumption was actually tested). Mocked here to
        keep this test file offline/fast; the live call is proven to work
        by providers/osm_facility_lookup.py's own test suite and by
        scripts/smoke_test_real_providers.py."""
        import json as json_module

        class FakeResp:
            status = 200
            def read(self):
                return json_module.dumps({"code": "Ok", "routes": [{"distance": 842.5}]}).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            geo_distance_engine_module.urllib.request, "urlopen", lambda req, timeout: FakeResp()
        )
        from domain.models import Coordinate
        engine = GeoDistanceEngine()
        a = Coordinate(latitude=25.2, longitude=121.6)
        b = Coordinate(latitude=25.21, longitude=121.61)
        result = engine.route_distance(a, "起點", b, "終點")
        assert result.status == "COMPLETED"
        assert result.distance_m == 842.5

    def test_route_distance_network_failure_is_manual_review_not_fabricated(self, monkeypatch):
        """Never silently substitutes straight-line distance for a failed
        route-distance request -- a network failure/timeout must surface as
        MANUAL_REVIEW_REQUIRED, same as every other network call in this
        codebase."""
        import urllib.error

        def raise_error(req, timeout):
            raise urllib.error.URLError("simulated network failure")

        monkeypatch.setattr(geo_distance_engine_module.urllib.request, "urlopen", raise_error)
        from domain.models import Coordinate
        engine = GeoDistanceEngine()
        a = Coordinate(latitude=25.2, longitude=121.6)
        b = Coordinate(latitude=25.21, longitude=121.61)
        result = engine.route_distance(a, "起點", b, "終點")
        assert result.status == "MANUAL_REVIEW_REQUIRED"
        assert result.distance_m is None

    def test_compute_dispatches_correctly(self, monkeypatch):
        import json as json_module

        class FakeResp:
            status = 200
            def read(self):
                return json_module.dumps({"code": "Ok", "routes": [{"distance": 500.0}]}).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            geo_distance_engine_module.urllib.request, "urlopen", lambda req, timeout: FakeResp()
        )
        from domain.models import Coordinate
        engine = GeoDistanceEngine()
        a = Coordinate(latitude=25.2, longitude=121.6)
        b = Coordinate(latitude=25.21, longitude=121.61)
        straight = engine.compute(a, "a", b, "b", DistanceMethod.STRAIGHT_LINE)
        route = engine.compute(a, "a", b, "b", DistanceMethod.ROUTE)
        assert straight.status == "COMPLETED"
        assert route.status == "COMPLETED"
        assert route.distance_m == 500.0


# ---------------------------------------------------------------------
# Part 3: Full pipeline through PDF
# ---------------------------------------------------------------------
class TestFullPipelineThroughPdf:
    def test_final_price_still_matches_official_golden_case(self, golden_form_result):
        final = next(f for f in golden_form_result.fields if f.field_id == "base_parcel_comparison_price")
        assert final.final_value == Decimal("212958")

    def test_pdf_generation_produces_openable_file(self, golden_form_result, tmp_path):
        renderer = PdfRenderer()
        output_path = str(tmp_path / "form4_5-2.pdf")
        renderer.render_all_forms(golden_form_result, GOLDEN_CASE.case_no, GOLDEN_CASE.segment_code, output_path)
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 10_000  # not a truncated/empty file

        assert PdfReader is not None, "pypdf must be available to verify PDF openability"
        reader = PdfReader(output_path)  # raises if not openable
        assert len(reader.pages) >= 1

    def test_pdf_page_count_is_reasonable(self, golden_form_result, tmp_path):
        """55 fields across ~10 sections should paginate to a small handful
        of pages, not 1 (too cramped to be legible) and not 50+ (broken
        layout looping)."""
        renderer = PdfRenderer()
        output_path = str(tmp_path / "form4_5-2.pdf")
        renderer.render_all_forms(golden_form_result, GOLDEN_CASE.case_no, GOLDEN_CASE.segment_code, output_path)
        reader = PdfReader(output_path)
        assert 2 <= len(reader.pages) <= 20

    def test_pdf_text_is_visible_and_extractable(self, golden_form_result, tmp_path):
        renderer = PdfRenderer()
        output_path = str(tmp_path / "form4_5-2.pdf")
        renderer.render_all_forms(golden_form_result, GOLDEN_CASE.case_no, GOLDEN_CASE.segment_code, output_path)
        reader = PdfReader(output_path)
        full_text = "".join(page.extract_text() for page in reader.pages)
        assert len(full_text.strip()) > 500
        assert GOLDEN_CASE.case_no in full_text
        assert GOLDEN_CASE.segment_code in full_text

    def test_pdf_reflects_the_blk01_fix_correctly(self, golden_form_result, tmp_path):
        """Regression check spanning phases: the corrected rule_id
        (IND-BUILDING_COVERAGE_RATIO_INDIVIDUAL-*) must appear in the actual
        rendered PDF text for the INDIVIDUAL (表4) 建蔽率 field specifically.
        Note: REG-BUILDING_COVERAGE_RATIO-* legitimately and correctly ALSO
        appears elsewhere in the PDF, for the separate REGIONAL (表5-2)
        建蔽率 field -- that is correct behavior, not the bug. The bug this
        regression test targets was the INDIVIDUAL field wrongly resolving
        to a REGIONAL rule_id; we verify against the structured field data
        directly (unambiguous) rather than a whole-document substring
        absence check (which would incorrectly flag the legitimate regional
        field too)."""
        individual_field = next(
            f for f in golden_form_result.fields
            if f.field_id == "individual_building_coverage_ratio_differential_rate_溫泉段218地號"
        )
        assert individual_field.rule_id == "IND-BUILDING_COVERAGE_RATIO_INDIVIDUAL-02"
        assert individual_field.rule_id.startswith("IND-")

        renderer = PdfRenderer()
        output_path = str(tmp_path / "form4_5-2.pdf")
        renderer.render_all_forms(golden_form_result, GOLDEN_CASE.case_no, GOLDEN_CASE.segment_code, output_path)
        reader = PdfReader(output_path)
        full_text = "".join(page.extract_text() for page in reader.pages)
        assert "IND-BUILDING_COVERAGE_RATIO_INDIVIDUAL" in full_text

    def test_table1_pdf_generation_and_content(self, tmp_path):
        pdf_bytes = build_table1_pdf_bytes(
            GOLDEN_CASE.case_no, GOLDEN_CASE.segment_code, GOLDEN_CASE.segment_scope,
            BASE_REGIONAL, "2026-08-30T00:00:00",
        )
        output_path = str(tmp_path / "form1.pdf")
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)
        reader = PdfReader(output_path)
        assert 1 <= len(reader.pages) <= 10
        full_text = "".join(page.extract_text() for page in reader.pages)
        assert "地價區段勘查表" in full_text
        assert "P002-00" in full_text

    def test_manual_field_visibly_flagged_in_pdf(self, golden_form_result, tmp_path):
        """price_formation_similarity is a genuine estimator-judgment field
        -- must be visibly marked MANUAL in the rendered PDF, not silently
        presented as if it were AUTOMATIC."""
        renderer = PdfRenderer()
        output_path = str(tmp_path / "form4_5-2.pdf")
        renderer.render_all_forms(golden_form_result, GOLDEN_CASE.case_no, GOLDEN_CASE.segment_code, output_path)
        reader = PdfReader(output_path)
        full_text = "".join(page.extract_text() for page in reader.pages)
        assert "MANUAL" in full_text
