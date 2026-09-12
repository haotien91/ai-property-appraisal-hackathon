import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, box

from src.main import run_case
from src.roads import RoadFeature


def test_run_case_with_injected_vector_sources_produces_all_outputs(tmp_path):
    input_path = tmp_path / "case.json"
    input_path.write_text(json.dumps({
        "lat": 24.99384,
        "lon": 121.421118,
        "section": {"name": "樹德段", "code": "1902"},
        "parcel": "284",
        "constraints": {
            "north_of": "樹人街",
            "west_of": "長壽街21巷",
            "south_of": "啟智街14巷",
            "east_of": "樹德街136巷",
            "zone": "第一種住宅區"
        }
    }, ensure_ascii=False), encoding="utf-8")

    def road_provider(case, anchor):
        x, y = anchor.x, anchor.y
        return {
            "north_of": RoadFeature(1, "樹人街", LineString([(x-100,y-50),(x+100,y-50)])),
            "west_of": RoadFeature(2, "長壽街21巷", LineString([(x+60,y-100),(x+60,y+100)])),
            "south_of": RoadFeature(3, "啟智街14巷", LineString([(x-100,y+60),(x+100,y+60)])),
            "east_of": RoadFeature(4, "樹德街136巷", LineString([(x-70,y-100),(x-70,y+100)])),
        }

    def zoning_provider(case, roi, anchor):
        x, y = anchor.x, anchor.y
        return gpd.GeoDataFrame(
            {"ZONE": ["第一種住宅區"]},
            geometry=[box(x-65,y-45,x+55,y+55)],
            crs="EPSG:3826",
        )

    out = tmp_path / "out"
    result = run_case(input_path, out, road_provider=road_provider, zoning_provider=zoning_provider)
    assert result["status"] == "ok"
    assert (out / "result.geojson").exists()
    assert (out / "debug.geojson").exists()
    assert (out / "preview.png").exists()
    assert (out / "boundary_map.png").exists()
    assert (out / "constraint_audit.json").exists()
    props = json.loads((out / "result.geojson").read_text(encoding="utf-8"))["features"][0]["properties"]
    assert props["section_code"] == "1902"
    assert props["parcel"] == "284"
    assert props["zone_constraint"]["valid"] is True
    audit = json.loads((out / "constraint_audit.json").read_text(encoding="utf-8"))
    assert audit["target_zone"] == "第一種住宅區"
    assert audit["zone_constraint_passed"] is True
    assert audit["outside_target_zone_area_m2"] <= 0.01
