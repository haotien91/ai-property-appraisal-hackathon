from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from src.zoning import ZoningSource, load_zoning, select_zone


def test_load_zoning_reprojects_and_clips_local_geojson(tmp_path):
    src = tmp_path / "zones.geojson"
    gdf = gpd.GeoDataFrame(
        {"ZONE": ["第一種住宅區", "第二種住宅區"]},
        geometry=[box(121.420, 24.993, 121.422, 24.995), box(121.50, 25.00, 121.51, 25.01)],
        crs="EPSG:4326",
    )
    gdf.to_file(src, driver="GeoJSON")

    from src.crs import anchor_3826, roi_square
    roi = roi_square(anchor_3826(24.994, 121.421), 600)
    loaded = load_zoning(ZoningSource(url=str(src), local_path=src), roi)
    assert str(loaded.crs).upper().endswith("3826")
    assert len(loaded) == 1


def test_select_zone_normalizes_name(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"ZONE": [" 第一種住宅區 ", "第二種住宅區"]},
        geometry=[box(0,0,1,1), box(2,2,3,3)],
        crs="EPSG:3826",
    )
    selected = select_zone(gdf, "第一種住宅區")
    assert len(selected) == 1
    assert selected.iloc[0]["ZONE"].strip() == "第一種住宅區"
