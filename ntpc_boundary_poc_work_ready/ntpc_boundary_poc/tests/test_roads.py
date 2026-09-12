import pytest
from shapely.geometry import LineString

from src.roads import (
    RoadAmbiguousError,
    build_overpass_query,
    parse_overpass_ways,
    resolve_road,
)

SAMPLE = {
    "elements": [
        {
            "type": "way",
            "id": 1,
            "tags": {"highway": "residential", "name": "樹人街"},
            "geometry": [
                {"lat": 24.993, "lon": 121.420},
                {"lat": 24.994, "lon": 121.422},
            ],
        },
        {
            "type": "way",
            "id": 2,
            "tags": {"highway": "residential", "name": "長壽街21巷"},
            "geometry": [
                {"lat": 24.992, "lon": 121.423},
                {"lat": 24.995, "lon": 121.423},
            ],
        },
    ]
}


def test_build_overpass_query_contains_names_and_geometry_output():
    q = build_overpass_query(24.99384, 121.421118, 500, ["樹人街", "長壽街21巷"])
    assert 'way(around:500,24.99384,121.421118)' in q
    assert 'out geom;' in q
    assert '樹人街' in q
    assert '長壽街21巷' in q


def test_resolve_exact_named_road():
    roads = parse_overpass_ways(SAMPLE)
    road = resolve_road(roads, "樹人街")
    assert road.name == "樹人街"
    assert road.geometry.length > 100


def test_ambiguous_disconnected_same_name_raises():
    payload = {
        "elements": [
            {
                "type": "way", "id": 1,
                "tags": {"highway": "residential", "name": "測試路"},
                "geometry": [{"lat":24.99,"lon":121.40},{"lat":24.991,"lon":121.401}],
            },
            {
                "type": "way", "id": 2,
                "tags": {"highway": "residential", "name": "測試路"},
                "geometry": [{"lat":25.00,"lon":121.42},{"lat":25.001,"lon":121.421}],
            },
        ]
    }
    roads = parse_overpass_ways(payload)
    with pytest.raises(RoadAmbiguousError):
        resolve_road(roads, "測試路")
