import geopandas as gpd
from shapely.geometry import box

from src.validate import audit_zone_constraint


def test_zone_constraint_accepts_result_fully_inside_target_zone():
    result = box(0, 0, 10, 10)
    zones = gpd.GeoDataFrame(
        {"ZONE": ["第一種住宅區"]},
        geometry=[box(-5, -5, 15, 15)],
        crs="EPSG:3826",
    )

    report = audit_zone_constraint(result, zones, tolerance_m2=0.01)

    assert report.valid
    assert report.outside_area_m2 == 0.0
    assert report.inside_ratio == 1.0


def test_zone_constraint_rejects_any_material_area_outside_target_zone():
    result = box(0, 0, 10, 10)
    zones = gpd.GeoDataFrame(
        {"ZONE": ["第一種住宅區"]},
        geometry=[box(0, 0, 5, 10)],
        crs="EPSG:3826",
    )

    report = audit_zone_constraint(result, zones, tolerance_m2=0.01)

    assert not report.valid
    assert report.outside_area_m2 == 50.0
    assert report.inside_ratio == 0.5
