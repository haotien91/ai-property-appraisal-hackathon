import geopandas as gpd
from PIL import Image
from shapely.geometry import LineString, Point, box

from src.models import BoundaryCase, Constraints, SectionInfo
from src.render import render_boundary_map


def test_boundary_map_marks_target_zone_and_final_boundary(tmp_path):
    roi = box(-20, -20, 20, 20)
    roads = {
        "樹人街": LineString([(-15, -10), (15, -10)]),
        "長壽街21巷": LineString([(10, -15), (10, 15)]),
        "啟智街14巷": LineString([(-15, 10), (15, 10)]),
        "樹德街136巷": LineString([(-10, -15), (-10, 15)]),
    }
    zoning = gpd.GeoDataFrame(
        {"ZONE": ["第一種住宅區"]},
        geometry=[box(-9, -9, 9, 9)],
        crs="EPSG:3826",
    )
    case = BoundaryCase(
        24.99384,
        121.421118,
        SectionInfo("樹德段", "1902"),
        "284",
        Constraints(
            "樹人街",
            "長壽街21巷",
            "啟智街14巷",
            "樹德街136巷",
            "第一種住宅區",
        ),
    )
    path = tmp_path / "boundary_map.png"

    render_boundary_map(path, roi, roads, zoning, box(-9, -9, 9, 9), Point(0, 0), case)

    assert path.exists()
    assert path.stat().st_size > 1000
    with Image.open(path) as im:
        assert im.width >= 800
        assert im.height >= 800
