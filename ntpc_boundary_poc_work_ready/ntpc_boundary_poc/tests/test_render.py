import geopandas as gpd
from shapely.geometry import LineString, Point, box

from src.models import BoundaryCase, Constraints, SectionInfo
from src.render import render_preview


def test_render_preview_creates_png(tmp_path):
    roi = box(-20, -20, 20, 20)
    roads = {
        "樹人街": LineString([(-15,-10),(15,-10)]),
        "長壽街21巷": LineString([(10,-15),(10,15)]),
        "啟智街14巷": LineString([(-15,10),(15,10)]),
        "樹德街136巷": LineString([(-10,-15),(-10,15)]),
    }
    zoning = gpd.GeoDataFrame(
        {"ZONE":["第一種住宅區"]}, geometry=[box(-9,-9,9,9)], crs="EPSG:3826"
    )
    case = BoundaryCase(
        24.99384,121.421118,SectionInfo("樹德段","1902"),"284",
        Constraints("樹人街","長壽街21巷","啟智街14巷","樹德街136巷","第一種住宅區")
    )
    path = tmp_path / "preview.png"
    render_preview(path, roi, roads, zoning, box(-9,-9,9,9), Point(0,0), case)
    assert path.exists()
    assert path.stat().st_size > 1000
