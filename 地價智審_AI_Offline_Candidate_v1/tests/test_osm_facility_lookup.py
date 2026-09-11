# -*- coding: utf-8 -*-
"""
Tests for providers/osm_facility_lookup.py. All network access is mocked
(patches `_http_get`) -- this suite must stay offline-safe and fast, unlike
the separate live smoke-test script (scripts/smoke_test_real_providers.py),
which deliberately DOES make real calls and is not collected by pytest.
"""
import json
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

import osm_facility_lookup as ofl  # noqa: E402
from domain.models import Coordinate  # noqa: E402
from engine.geo_distance_engine import DistanceMethod, DistanceResult  # noqa: E402
from datetime import datetime  # noqa: E402


CENTER = Coordinate(latitude=25.0, longitude=121.5)


def _overpass_payload(elements):
    return json.dumps({"version": 0.6, "elements": elements}).encode("utf-8")


class TestFindNearestFacility:
    def test_no_center_coordinate_returns_not_found_without_network_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: called.append(1) or b"{}")
        result = ofl.find_nearest_facility(None, ["amenity=school"], 1000)
        assert result.found is False
        assert "座標" in result.reason
        assert called == []  # must not even attempt a network call

    def test_network_failure_returns_not_found_with_reason(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: None)
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 1000)
        assert result.found is False
        assert "Overpass" in result.reason

    def test_empty_elements_returns_not_found(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload([]))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 1000)
        assert result.found is False
        assert "查無" in result.reason

    def test_malformed_json_returns_not_found(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: b"not json{{{")
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 1000)
        assert result.found is False

    def test_picks_nearest_of_multiple_elements(self, monkeypatch):
        elements = [
            {"type": "node", "lat": 25.01, "lon": 121.5, "tags": {"name": "Far School"}},
            {"type": "node", "lat": 25.0001, "lon": 121.5, "tags": {"name": "Near School"}},
        ]
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(elements))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        assert result.found is True
        assert result.facility.name == "Near School"
        assert result.match_count == 2

    def test_way_element_uses_center_lat_lon(self, monkeypatch):
        elements = [{"type": "way", "center": {"lat": 25.001, "lon": 121.5}, "tags": {"name": "A Road-adjacent Building"}}]
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(elements))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        assert result.found is True
        assert result.facility.latitude == 25.001

    def test_name_falls_back_to_zh_hant_tag(self, monkeypatch):
        elements = [{"type": "node", "lat": 25.001, "lon": 121.5, "tags": {"name:zh-Hant": "中文名稱"}}]
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(elements))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        assert result.facility.name == "中文名稱"

    def test_name_falls_back_to_unnamed_placeholder(self, monkeypatch):
        elements = [{"type": "node", "lat": 25.001, "lon": 121.5, "tags": {}}]
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(elements))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        assert result.facility.name == "(未命名)"

    def test_elements_missing_coordinates_are_skipped_not_crashed(self, monkeypatch):
        elements = [
            {"type": "node", "tags": {"name": "No Coords"}},
            {"type": "node", "lat": 25.001, "lon": 121.5, "tags": {"name": "Has Coords"}},
        ]
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(elements))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        assert result.found is True
        assert result.facility.name == "Has Coords"

    def test_distance_uses_project_geo_distance_engine(self, monkeypatch):
        """Not a duplicate Haversine implementation: same numeric result as
        calling GeoDistanceEngine directly on the same two points."""
        elements = [{"type": "node", "lat": 25.01, "lon": 121.51, "tags": {"name": "X"}}]
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(elements))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        expected = ofl._geo_engine.straight_line_distance(
            CENTER, "c", Coordinate(latitude=25.01, longitude=121.51), "d",
        ).distance_m
        assert result.facility.distance_m == expected


class TestHttpGetRetry:
    def test_retries_once_on_failure_then_succeeds(self, monkeypatch):
        attempts = {"n": 0}

        class FakeResp:
            status = 200
            def read(self):
                return b'{"elements": []}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            attempts["n"] += 1
            if attempts["n"] == 1:
                import urllib.error
                raise urllib.error.URLError("simulated transient failure")
            return FakeResp()

        monkeypatch.setattr(ofl.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(ofl.time, "sleep", lambda s: None)
        raw = ofl._http_get("https://example.invalid", timeout_s=1, retries=1, backoff_s=0)
        assert raw == b'{"elements": []}'
        assert attempts["n"] == 2

    def test_gives_up_after_exhausting_retries(self, monkeypatch):
        def always_fail(req, timeout):
            import urllib.error
            raise urllib.error.URLError("always fails")

        monkeypatch.setattr(ofl.urllib.request, "urlopen", always_fail)
        monkeypatch.setattr(ofl.time, "sleep", lambda s: None)
        raw = ofl._http_get("https://example.invalid", timeout_s=1, retries=2, backoff_s=0)
        assert raw is None


class TestGeocode:
    def test_empty_query_returns_none_without_network_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: called.append(1) or b"[]")
        assert ofl.geocode("") is None
        assert called == []

    def test_network_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: None)
        assert ofl.geocode("新北市板橋區") is None

    def test_no_results_returns_none(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: b"[]")
        assert ofl.geocode("does not exist anywhere") is None

    def test_parses_first_result_into_coordinate(self, monkeypatch):
        payload = json.dumps([{"lat": "25.0138", "lon": "121.4629"}]).encode("utf-8")
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: payload)
        coord = ofl.geocode("新北市板橋區")
        assert coord.latitude == 25.0138
        assert coord.longitude == 121.4629


class TestFindNearestFacilityDistanceMethod:
    """Phase 1 REQ-010: convenience-type facilities must be reported with
    walking ROUTE distance, not straight-line -- these tests cover the
    distance_method plumbing through find_nearest_facility(), all with
    _http_get and route_distance mocked (offline-safe)."""

    ONE_ELEMENT = [{"type": "node", "lat": 25.001, "lon": 121.5, "tags": {"name": "測試設施"}}]

    def test_default_method_is_straight_line(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(self.ONE_ELEMENT))
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000)
        assert result.facility.distance_method == DistanceMethod.STRAIGHT_LINE
        assert result.facility.distance_m is not None

    def test_route_method_calls_route_distance_on_the_winning_candidate_only(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(self.ONE_ELEMENT))
        calls = []

        def fake_route_distance(origin, origin_description, destination, destination_description, timeout_s=8.0):
            calls.append((destination.latitude, destination.longitude))
            return DistanceResult(
                origin=origin, origin_description=origin_description,
                destination=destination, destination_description=destination_description,
                method=DistanceMethod.ROUTE, distance_m=999.0, source="fake OSRM",
                status="COMPLETED", computed_at=datetime.now(),
            )

        monkeypatch.setattr(ofl._geo_engine, "route_distance", fake_route_distance)
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000, distance_method=DistanceMethod.ROUTE)
        assert result.facility.distance_m == 999.0
        assert result.facility.distance_method == DistanceMethod.ROUTE
        assert len(calls) == 1  # exactly one extra call, not one per Overpass candidate

    def test_route_failure_leaves_distance_none_with_note_not_a_silent_straight_line_fallback(self, monkeypatch):
        monkeypatch.setattr(ofl, "_http_get", lambda *a, **k: _overpass_payload(self.ONE_ELEMENT))

        def failing_route_distance(origin, origin_description, destination, destination_description, timeout_s=8.0):
            return DistanceResult(
                origin=origin, origin_description=origin_description,
                destination=destination, destination_description=destination_description,
                method=DistanceMethod.ROUTE, distance_m=None, source="fake OSRM",
                status="MANUAL_REVIEW_REQUIRED", notes="模擬OSRM逾時",
                computed_at=datetime.now(),
            )

        monkeypatch.setattr(ofl._geo_engine, "route_distance", failing_route_distance)
        result = ofl.find_nearest_facility(CENTER, ["amenity=school"], 5000, distance_method=DistanceMethod.ROUTE)
        assert result.found is True  # the facility itself (name/location) was still found
        assert result.facility.distance_m is None
        assert result.facility.distance_note == "模擬OSRM逾時"
