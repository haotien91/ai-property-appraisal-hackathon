import json

import geopandas as gpd
from shapely.geometry import Point, box

from src.output import write_result_geojson
from src.validate import intersect_target_zone, validate_result


def test_intersection_prefers_anchor_component_and_drops_tiny_sliver():
    candidate = box(-10, -10, 10, 10)
    zones = gpd.GeoDataFrame(
        {"ZONE": ["第一種住宅區", "第一種住宅區"]},
        geometry=[box(-5, -5, 5, 5), box(9.5, 9.5, 10.0, 10.0)],
        crs="EPSG:3826",
    )
    anchor = Point(0, 0)
    result = intersect_target_zone(candidate, zones, anchor, min_area_m2=1.0)
    assert result.contains(anchor)
    assert result.area == 100


def test_validation_report_accepts_valid_nontrivial_result():
    result = box(-5, -5, 5, 5)
    report = validate_result(result, min_area_m2=20)
    assert report.valid
    assert report.area_m2 == 100


def test_write_result_geojson_serializes_metadata(tmp_path):
    path = tmp_path / "result.geojson"
    write_result_geojson(path, box(0, 0, 10, 10), {"section_code": "1902", "parcel": "284"})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert data["features"][0]["properties"]["parcel"] == "284"
