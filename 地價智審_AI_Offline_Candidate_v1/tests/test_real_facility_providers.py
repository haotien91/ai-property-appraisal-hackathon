# -*- coding: utf-8 -*-
"""
Tests for the Real*Provider classes (providers/{transportation,
public_facility,special_facility,environmental,commercial_activity}_
provider.py). All network access is mocked by monkeypatching
`find_nearest_facility` -- these tests must stay offline-safe; the live,
network-hitting proof-of-generalization lives in
scripts/smoke_test_real_providers.py instead, deliberately outside pytest's
default collection.

What these tests exist to catch:
1. Field-name parity with the Mock providers -- a Real provider that
   silently drops or renames a field would break FormCompletionEngine
   downstream without any obviously-related test failing.
2. The two "never guess" guarantees this whole codebase is built around:
   *_within_segment is ALWAYS MANUAL_REVIEW_REQUIRED (no GIS boundary data
   available), and subjective/assessed fields (bus_stop_density,
   customer_traffic_volume, shop_contiguity, pollution-source fields,
   school tiers) are NEVER derived from a lookup result, regardless of
   what the (mocked) OSM lookup returns.
"""
import os
import sys
from datetime import datetime

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

from base import ProviderContext  # noqa: E402
from domain.models import Coordinate  # noqa: E402
from osm_facility_lookup import OsmFacility, OsmLookupResult  # noqa: E402
import real_facility_provider_base  # noqa: E402
import transportation_provider  # noqa: E402
import public_facility_provider  # noqa: E402
import special_facility_provider  # noqa: E402
import environmental_provider  # noqa: E402
import commercial_activity_provider  # noqa: E402


CTX_WITH_COORD = ProviderContext(
    case_no="TEST-001", city="新北市", district="板橋區", segment_code="TEST-SEG",
    center_coordinate=Coordinate(latitude=25.0138, longitude=121.4629),
)
CTX_NO_COORD = ProviderContext(
    case_no="TEST-002", city="新北市", district="板橋區", segment_code="TEST-SEG",
)

FOUND_RESULT = OsmLookupResult(
    found=True,
    facility=OsmFacility(name="測試設施", latitude=25.02, longitude=121.47, distance_m=123.4, osm_tags={"name": "測試設施"}),
    match_count=3,
)
NOT_FOUND_RESULT = OsmLookupResult(found=False, facility=None, match_count=0, reason="模擬查無資料")


def _patch_lookup(monkeypatch, result):
    """Patch the lookup at every place a provider module bound it via
    `from osm_facility_lookup import find_nearest_facility` -- the name is
    bound at import time in each importing module's own namespace, so the
    origin module's attribute must be patched separately from each of
    these, not just from osm_facility_lookup itself. Also removes the
    polite inter-query pacing (real_facility_provider_base._INTER_QUERY_
    DELAY_S's time.sleep) -- that pacing exists to be considerate to the
    live Overpass server and has no purpose (only cost) once the network
    call itself is mocked out."""
    monkeypatch.setattr(real_facility_provider_base, "find_nearest_facility", lambda *a, **k: result)
    monkeypatch.setattr(transportation_provider, "find_nearest_facility", lambda *a, **k: result)
    monkeypatch.setattr(real_facility_provider_base.time, "sleep", lambda s: None)


def _fields(points):
    return {p.field for p in points}


def _by_field(points):
    return {p.field: p for p in points}


class TestFieldParityWithMock:
    """A Real provider must answer for at least every field its Mock
    counterpart declares -- FormCompletionEngine should never see a field
    silently disappear just because the data source changed."""

    def test_transportation(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        mock_fields = _fields(transportation_provider.MockTransportationProvider().fetch(CTX_WITH_COORD))
        real_fields = _fields(transportation_provider.RealTransportationProvider().fetch(CTX_WITH_COORD))
        assert mock_fields <= real_fields

    def test_public_facility(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        mock_fields = _fields(public_facility_provider.MockPublicFacilityProvider().fetch(CTX_WITH_COORD))
        real_fields = _fields(public_facility_provider.RealPublicFacilityProvider().fetch(CTX_WITH_COORD))
        assert mock_fields <= real_fields

    def test_special_facility(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        mock_fields = _fields(special_facility_provider.MockSpecialFacilityProvider().fetch(CTX_WITH_COORD))
        real_fields = _fields(special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD))
        assert mock_fields <= real_fields

    def test_environmental(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        mock_fields = _fields(environmental_provider.MockEnvironmentalProvider().fetch(CTX_WITH_COORD))
        real_fields = _fields(environmental_provider.RealEnvironmentalProvider().fetch(CTX_WITH_COORD))
        assert mock_fields <= real_fields

    def test_commercial_activity(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        mock_fields = _fields(commercial_activity_provider.MockCommercialActivityProvider().fetch(CTX_WITH_COORD))
        real_fields = _fields(commercial_activity_provider.RealCommercialActivityProvider().fetch(CTX_WITH_COORD))
        assert mock_fields <= real_fields


class TestNeverGuessesWithinSegment:
    def test_all_within_segment_fields_are_unknown_even_when_facility_found(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        for provider_cls in (
            transportation_provider.RealTransportationProvider,
            public_facility_provider.RealPublicFacilityProvider,
            special_facility_provider.RealSpecialFacilityProvider,
            environmental_provider.RealEnvironmentalProvider,
            commercial_activity_provider.RealCommercialActivityProvider,
        ):
            points = _by_field(provider_cls().fetch(CTX_WITH_COORD))
            within_fields = [f for f in points if f.endswith("_within_segment")]
            assert within_fields, f"{provider_cls.__name__} declared no within_segment fields at all"
            for f in within_fields:
                assert points[f].confidence == "UNKNOWN", f"{provider_cls.__name__}.{f} should never be guessed"
                assert points[f].value is None


class TestNeverGuessesSubjectiveFields:
    def test_bus_stop_density_never_derived(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(transportation_provider.RealTransportationProvider().fetch(CTX_WITH_COORD))
        assert points["bus_stop_density"].confidence == "UNKNOWN"

    def test_proximity_fields_never_derived(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(transportation_provider.RealTransportationProvider().fetch(CTX_WITH_COORD))
        for f in ("village_proximity", "distribution_center_proximity", "consumer_market_proximity"):
            assert points[f].confidence == "UNKNOWN"

    def test_customer_traffic_and_shop_contiguity_never_derived(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(commercial_activity_provider.RealCommercialActivityProvider().fetch(CTX_WITH_COORD))
        assert points["customer_traffic_volume"].confidence == "UNKNOWN"
        assert points["shop_contiguity"].confidence == "UNKNOWN"

    def test_pollution_source_fields_never_derived(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(environmental_provider.RealEnvironmentalProvider().fetch(CTX_WITH_COORD))
        for base in ("water_pollution", "noise_pollution", "air_pollution", "waste_pollution", "other_pollution"):
            assert points[f"{base}_name"].confidence == "UNKNOWN"

    def test_school_tier_fields_never_derived(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(public_facility_provider.RealPublicFacilityProvider().fetch(CTX_WITH_COORD))
        for group in ("school_elementary", "school_junior_high", "school_senior_high", "school_college"):
            assert points[f"{group}_name"].confidence == "UNKNOWN"


class TestFoundVsNotFound:
    def test_found_result_populates_name_and_distance(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD))
        assert points["cemetery_name"].value == "測試設施"
        assert points["cemetery_name"].confidence == "中"
        assert points["cemetery_name"].source_type == "API"
        assert points["cemetery_distance_m"].value == 123.4
        assert points["cemetery_distance_m"].unit == "M"

    def test_not_found_result_leaves_name_and_distance_unknown(self, monkeypatch):
        _patch_lookup(monkeypatch, NOT_FOUND_RESULT)
        points = _by_field(special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD))
        assert points["cemetery_name"].value is None
        assert points["cemetery_name"].confidence == "UNKNOWN"
        assert points["cemetery_distance_m"].value is None

    def test_no_center_coordinate_leaves_everything_unknown_without_crashing(self):
        # Deliberately unpatched: find_nearest_facility already short-circuits
        # (no network call) when center_coordinate is None, so this exercises
        # the real function end-to-end and proves the provider layer never
        # crashes or invents a coordinate when the caller has none to give.
        points = special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_NO_COORD)
        assert len(points) > 0
        assert all(p.confidence == "UNKNOWN" for p in points)


class TestQtyField:
    def test_qty_present_only_where_declared(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _by_field(commercial_activity_provider.RealCommercialActivityProvider().fetch(CTX_WITH_COORD))
        assert points["financial_institution_qty"].value == 3

    def test_qty_absent_for_specs_without_include_qty(self, monkeypatch):
        _patch_lookup(monkeypatch, FOUND_RESULT)
        points = _fields(special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD))
        assert "cemetery_qty" not in points


class TestMockUtilityFixtureSemanticCleanup:
    """TABLE1-MOCK-SEMANTIC-CLEANUP: MockSpecialFacilityProvider's
    gas_tank_name must read unambiguously as a gas storage facility (not
    an ordinary vehicle fuel/gas station), must never use a real company/
    institution name, and must carry an explicit test-data marker in the
    value itself. substation_name is unaffected (already unambiguous) --
    audited here too, per Task 1, and confirmed unchanged/still safe."""

    def test_gas_tank_fixture_no_longer_reads_as_a_fuel_station(self):
        points = {p.field: p for p in special_facility_provider.MockSpecialFacilityProvider().fetch(CTX_WITH_COORD)}
        value = points["gas_tank_name"].value
        assert value != "中油金山站"
        assert "中油" not in value  # no real petroleum company name
        assert "站" not in value    # avoids the "...station" reading entirely
        assert "瓦斯槽" in value or "儲油槽" in value  # unambiguously a tank, matching the official label
        assert "測試" in value  # explicit test-data marker baked into the value itself

    def test_gas_tank_fixture_marked_as_mock_not_official(self):
        points = {p.field: p for p in special_facility_provider.MockSpecialFacilityProvider().fetch(CTX_WITH_COORD)}
        assert points["gas_tank_name"].source_type == "Mock"
        assert points["substation_name"].source_type == "Mock"

    def test_substation_fixture_still_unambiguous_and_unchanged(self):
        points = {p.field: p for p in special_facility_provider.MockSpecialFacilityProvider().fetch(CTX_WITH_COORD)}
        assert points["substation_name"].value == "金山變電所"


class TestNoFabricatedHighVoltageTowerOrOilStorageTankSource:
    """TABLE1-MOCK-SEMANTIC-CLEANUP Task 4 #4: 表1's single "電業設施"
    row combines TWO official sub-labels each (變電所"或"高壓鐵塔;
    瓦斯槽"或"儲油槽) -- this codebase only ever sources the FIRST half
    of each pair (變電所 via power=substation; 瓦斯槽 via man_made=
    gasometer). There must be no code path anywhere that could report a
    match specifically identified as "高壓鐵塔" (high-voltage tower) or
    "儲油槽" (oil storage tank) -- neither has a verified OSM tag in this
    provider, and none may ever be silently fabricated."""

    def test_no_facility_spec_targets_high_voltage_tower_or_oil_tank_tags(self):
        all_selectors = [
            sel for spec in special_facility_provider.RealSpecialFacilityProvider.FACILITY_SPECS
            for sel in spec.overpass_selectors
        ]
        # power=tower / power=line are OSM's actual tags for transmission
        # towers/lines -- confirming neither is queried (no fabricated
        # 高壓鐵塔 signal exists to begin with).
        assert not any("tower" in sel for sel in all_selectors)
        assert not any(sel.startswith("power=line") for sel in all_selectors)
        # No oil-storage-tank-specific tag (e.g. man_made=storage_tank)
        # is queried either -- 儲油槽 has no source at all, by design.
        assert not any("storage_tank" in sel for sel in all_selectors)

    def test_gas_tank_field_prefix_has_no_separate_oil_tank_identity(self):
        """There is exactly ONE FacilitySpec for the combined "瓦斯槽或
        儲油槽" row (field_prefix="gas_tank") -- confirming this round's
        audit didn't leave behind a second, separate field/spec that
        could be mistaken for a genuine "儲油槽"-specific source."""
        prefixes = [s.field_prefix for s in special_facility_provider.RealSpecialFacilityProvider.FACILITY_SPECS]
        assert prefixes.count("gas_tank") == 1
        assert prefixes.count("substation") == 1
        assert not any("oil" in p or "tower" in p for p in prefixes)


class TestGasTankOsmTagSemanticSafety:
    """TABLE1-UTILITY-SAFETY-GATE: gas_tank's OSM query must never be able
    to match an ordinary vehicle fuel/gas STATION (amenity=fuel) and
    report it under the official "瓦斯槽或儲油槽" (gas tank/oil storage
    tank) label -- the two are not the same facility type, and a fuel
    station is common enough in OSM data that it would routinely win the
    "nearest" comparison over a genuine, much rarer gasometer."""

    def test_gas_tank_spec_no_longer_queries_amenity_fuel(self):
        specs = {s.field_prefix: s for s in special_facility_provider.RealSpecialFacilityProvider.FACILITY_SPECS}
        assert "gas_tank" in specs
        assert "amenity=fuel" not in specs["gas_tank"].overpass_selectors
        assert list(specs["gas_tank"].overpass_selectors) == ["man_made=gasometer"]

    def test_gas_tank_lookup_only_ever_requests_the_safe_tag(self, monkeypatch):
        """Spies on the actual selectors passed to find_nearest_facility
        for every FacilitySpec this provider fetches -- confirms the fix
        is live in the real call path, not just declared in the spec
        list (which could theoretically be overridden elsewhere)."""
        calls = []

        def fake(center, overpass_selectors, radius_m, **kwargs):
            calls.append(list(overpass_selectors))
            return FOUND_RESULT

        monkeypatch.setattr(real_facility_provider_base, "find_nearest_facility", fake)
        monkeypatch.setattr(real_facility_provider_base.time, "sleep", lambda s: None)
        special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD)

        gas_tank_index = [s.field_prefix for s in
                           special_facility_provider.RealSpecialFacilityProvider.FACILITY_SPECS].index("gas_tank")
        assert "amenity=fuel" not in calls[gas_tank_index]
        assert calls[gas_tank_index] == ["man_made=gasometer"]

    def test_ordinary_fuel_station_result_is_never_reported_as_gas_tank(self, monkeypatch):
        """Simulates the exact false-positive scenario: an ordinary gas
        station found nearby (this is what an amenity=fuel-only match
        would have looked like before the fix). Since the provider no
        longer sends amenity=fuel to Overpass at all, the mocked lookup
        here stands in for "whatever Overpass legitimately returns for
        the now-narrower man_made=gasometer-only query" -- a real fuel
        station can no longer be the source of this value, because the
        query that could have matched it no longer exists. This test
        exists to catch a regression if amenity=fuel is ever re-added."""
        specs = {s.field_prefix: s for s in special_facility_provider.RealSpecialFacilityProvider.FACILITY_SPECS}
        assert all(sel != "amenity=fuel" for sel in specs["gas_tank"].overpass_selectors), (
            "amenity=fuel must never be part of gas_tank's OSM query -- "
            "it identifies an ordinary vehicle fuel station, not a gas "
            "tank/oil storage tank, and is common enough to routinely "
            "false-positive against a genuine (much rarer) gasometer."
        )

    def test_subtype_never_derived_from_name_text_containing_gas_or_tank_words(self, monkeypatch):
        """Task 6 #5: even when a facility IS found (via the safe
        man_made=gasometer query), the provider must report it purely by
        field-prefix identity -- it must never additionally inspect the
        facility's own name string for "瓦斯"/"油槽"-like substrings to
        decide whether to accept/reject it. Verified here by confirming
        gas_tank_name is populated unconditionally from whatever
        find_nearest_facility returns, with no name-content filtering
        logic in the provider at all (RealFacilityProviderBase._facility_
        datapoints has no such check -- see real_facility_provider_base.py)."""
        arbitrary_name_result = FOUND_RESULT  # facility.name == "測試設施", contains neither "瓦斯" nor "油槽"
        monkeypatch.setattr(real_facility_provider_base, "find_nearest_facility", lambda *a, **k: arbitrary_name_result)
        monkeypatch.setattr(real_facility_provider_base.time, "sleep", lambda s: None)
        points = _by_field(special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD))
        # Reported anyway -- identity comes from WHICH query matched
        # (man_made=gasometer), never from parsing the returned name text.
        assert points["gas_tank_name"].value == "測試設施"


class TestReq010DistanceMethodSelection:
    """Phase 1 REQ-010: convenience-type (distance_positive) facilities use
    walking ROUTE distance; nuisance-type (distance_negative) facilities
    use STRAIGHT_LINE. These tests verify each provider actually requests
    the method its own FACILITY_SPECS declare, by spying on the
    distance_method kwarg find_nearest_facility was called with."""

    def _spy_lookup(self, monkeypatch):
        from engine.geo_distance_engine import DistanceMethod
        calls = []

        def fake(*args, **kwargs):
            calls.append(kwargs.get("distance_method", DistanceMethod.STRAIGHT_LINE))
            return FOUND_RESULT

        monkeypatch.setattr(real_facility_provider_base, "find_nearest_facility", fake)
        monkeypatch.setattr(transportation_provider, "find_nearest_facility", fake)
        monkeypatch.setattr(real_facility_provider_base.time, "sleep", lambda s: None)
        return calls

    def test_transportation_uses_route_for_major_station_and_interchange(self, monkeypatch):
        from engine.geo_distance_engine import DistanceMethod
        calls = self._spy_lookup(monkeypatch)
        transportation_provider.RealTransportationProvider().fetch(CTX_WITH_COORD)
        # 3 calls total: major_station + interchange (FACILITY_SPECS, both
        # ROUTE) plus bus_stop (a separate ad-hoc lookup in fetch()'s
        # override, distance_method irrelevant there since bus_stop has no
        # distance_m field at all -- only name/within_segment/density).
        assert len(calls) == 3
        assert calls.count(DistanceMethod.ROUTE) == 2

    def test_public_facility_uses_route_for_convenience_but_not_wastewater(self, monkeypatch):
        from engine.geo_distance_engine import DistanceMethod
        calls = self._spy_lookup(monkeypatch)
        public_facility_provider.RealPublicFacilityProvider().fetch(CTX_WITH_COORD)
        # market/park/tourism_facility/parking_lot -> ROUTE, wastewater_facility -> STRAIGHT_LINE
        assert calls.count(DistanceMethod.ROUTE) == 4
        assert calls.count(DistanceMethod.STRAIGHT_LINE) == 1

    def test_commercial_activity_uses_route_for_all_four_specs(self, monkeypatch):
        from engine.geo_distance_engine import DistanceMethod
        calls = self._spy_lookup(monkeypatch)
        commercial_activity_provider.RealCommercialActivityProvider().fetch(CTX_WITH_COORD)
        assert len(calls) == 4
        assert all(c == DistanceMethod.ROUTE for c in calls)

    def test_special_facility_still_uses_straight_line_for_every_spec(self, monkeypatch):
        from engine.geo_distance_engine import DistanceMethod
        calls = self._spy_lookup(monkeypatch)
        special_facility_provider.RealSpecialFacilityProvider().fetch(CTX_WITH_COORD)
        assert calls  # substation/gas_tank/cemetery/funeral_home/crematorium/columbarium
        assert all(c == DistanceMethod.STRAIGHT_LINE for c in calls)


class TestFacilityFoundButDistanceCalculationFailed:
    """A ROUTE lookup can find the facility (name/location) via Overpass
    but still fail to get a distance if the OSRM call itself fails -- that
    must degrade to MANUAL_REVIEW_REQUIRED for the distance field ONLY,
    never silently reporting the straight-line number under the ROUTE
    label, and never losing the name that WAS found."""

    def test_distance_none_becomes_unknown_while_name_stays_populated(self, monkeypatch):
        from engine.geo_distance_engine import DistanceMethod
        result_with_failed_distance = OsmLookupResult(
            found=True,
            facility=OsmFacility(
                name="測試設施", latitude=25.02, longitude=121.47,
                distance_m=None, distance_method=DistanceMethod.ROUTE,
                distance_note="模擬OSRM逾時", osm_tags={},
            ),
            match_count=1,
        )
        _patch_lookup(monkeypatch, result_with_failed_distance)
        points = _by_field(transportation_provider.RealTransportationProvider().fetch(CTX_WITH_COORD))
        assert points["major_station_name"].value == "測試設施"
        assert points["major_station_name"].confidence == "中"
        assert points["major_station_distance_m"].value is None
        assert points["major_station_distance_m"].confidence == "UNKNOWN"
