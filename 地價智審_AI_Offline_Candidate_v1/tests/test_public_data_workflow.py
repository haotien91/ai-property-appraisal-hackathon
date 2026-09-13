"""Contract, provenance and end-to-end regression tests; no live requests."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from providers.nlsc_public_api import NlscPublicApi, xml_rows
from providers.public_http import PublicHttpClient, PublicDataError
from providers.public_data_collector import PublicDataCollector, school_prefixes
from backend.public_data_service import PublicDataService, prepare_request, regional_factor_points
from backend.local_workflow_service import extract_case_metadata


class FakeHttp:
    def get(self, url, **kwargs):
        meta = {"url": url, "retrieved_at": 1789257600, "cached": True, "stale": False}
        if "TownVillagePointQuery1" in url:
            return json.dumps({"ctyName": "新北市", "townName": "樹林區", "villageName": "潭底里"}, ensure_ascii=False).encode(), meta
        if "TownVillagePointQuery/" in url:
            return b'<root><sectName>SECTION</sectName><sectCode>1942</sectCode></root>', meta
        if "LandUsePointQuery" in url:
            return '<root><ITEM><NAME>住宅</NAME><LYEAR>2023</LYEAR><LMONTH>7</LMONTH></ITEM></root>'.encode(), meta
        if "TextQueryRoad" in url:
            return '<roads><road><name>樹人街</name><townName>樹林區</townName><townCode>F17</townCode></road></roads>'.encode(), meta
        if "ListRoadCross" in url:
            return '<crossRoads><crossRoad><name>大安路</name><x>121.42</x><y>24.99</y></crossRoad></crossRoads>'.encode(), meta
        raise PublicDataError("not configured")

    def json(self, url, **kwargs):
        meta = {"url": url, "retrieved_at": 1789257600}
        if "MarkBufferAnlys/edu/" in url:
            return [{"id":"1","name":"樹林國民小學附設幼兒園","lon":121.42001,"lat":24.99},
                    {"id":"2","name":"樹林國民小學","lon":121.421,"lat":24.99}], meta
        if "MarkBufferAnlys" in url:
            return [], meta
        if "overpass" in url:
            return {"elements":[{"id":3,"type":"node","lon":121.4202,"lat":24.99,
                                  "tags":{"amenity":"fuel","name":"加油站"}},
                                 {"id":4,"type":"node","lon":121.4203,"lat":24.99,
                                  "tags":{"leisure":"park","name":"測試公園"}}]}, meta
        raise PublicDataError("not configured")


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(PublicDataCollector, "_local_facilities", lambda *a: ([], {}))
    import land_use_provider, urban_plan_boundary_provider
    monkeypatch.setattr(land_use_provider.RealLandUseProvider, "fetch", lambda *a: [])
    monkeypatch.setattr(urban_plan_boundary_provider.RealUrbanPlanBoundaryProvider, "resolve_urban_plan",
                        lambda *a: SimpleNamespace(plan_id=None, plan_name=None))
    return {"case_no":"A","segment_code":"S1","city":"新北市","district":"樹林區",
            "center_coordinate":{"longitude":121.42,"latitude":24.99}}


def test_xml_and_live_content_negotiated_json():
    assert xml_rows(b'<root><townCode>F17</townCode></root>') == [{"townCode":"F17"}]
    assert xml_rows(b'{"townCode":"F17"}') == [{"townCode":"F17"}]
    assert xml_rows(b'<crossRoads/>') == []
    with pytest.raises(PublicDataError):
        xml_rows(b'<invalid')


@pytest.mark.parametrize("lon,lat", [(24.99,121.42),(float('nan'),25),(181,25),(121,float('inf'))])
def test_reject_bad_coordinate(lon, lat):
    with pytest.raises(ValueError):
        NlscPublicApi.coordinate(lon,lat)


def test_school_classification_does_not_use_host_institution():
    assert school_prefixes("科技大學辦理非營利幼兒園") == []
    assert school_prefixes("樹林國民小學附設幼兒園") == []
    assert school_prefixes("樹林高級中學附設國中") == ["school_junior_high"]


def test_real_sources_flow_to_survey_without_invented_absence(isolated):
    result = PublicDataCollector(FakeHttp()).collect(isolated)
    points = {p["field"]:p for p in result["points"]}
    assert points["school_elementary_name"]["value"] == "樹林國民小學"
    assert points["school_elementary_distance_m"]["value"] > 0
    assert points["school_elementary_distance_m"]["distance_method"] == "STRAIGHT_LINE"
    assert points["school_elementary_distance_m"]["status"] == "REFERENCE"
    assert points["school_elementary_within_segment"]["value"] is None
    assert points["gas_tank_name"]["value"] is None  # fuel station is NOT storage tank
    assert points["land_use_current"]["value"] == "住宅"
    assert points["land_use_zone"]["value"] is None  # land use is NOT zoning
    assert result["total_field_count"] == 133
    assert result["collected_field_count"] + len(result["missing_field_ids"]) == 133


def test_polygon_enables_inside_without_distance_threshold(isolated):
    isolated["segment_geometry"]={"type":"Polygon","coordinates":[[[121.419,24.989],[121.422,24.989],
                                           [121.422,24.991],[121.419,24.991],[121.419,24.989]]]}
    result=PublicDataCollector(FakeHttp()).collect(isolated)
    points={p["field"]:p for p in result["points"]}
    assert points["park_within_segment"]["value"] is True


def test_fixed_value_retained_with_conflicting_api_evidence(isolated):
    isolated["provided_values"]={"land_use_current":"住商混合"}
    result=PublicDataCollector(FakeHttp()).collect(isolated)
    point=next(p for p in result["points"] if p["field"]=="land_use_current")
    assert point["value"]=="住商混合"
    assert point["status"]=="PROVIDED"
    assert point["alternatives"][0]["value"]=="住宅"


def test_network_failure_stays_missing_not_zero(isolated):
    class Down(FakeHttp):
        def json(self,*a,**kw): raise PublicDataError("offline")
        def get(self,*a,**kw): raise PublicDataError("offline")
    result=PublicDataCollector(Down()).collect(isolated)
    assert all(p["value"] is None for p in result["points"] if p["field"].endswith('_distance_m'))
    assert any(s["status"]=="UNAVAILABLE" for s in result["sources"])


def test_road_lookup_uses_returned_town_code(tmp_path):
    result=PublicDataService(tmp_path,FakeHttp()).locate({"county_code":"F","district":"樹林區","road":"樹人街"})
    assert result["candidates"][0]["longitude"]==121.42
    assert '/F17/' in result["candidates"][0]["source_url"]


def test_presets_are_explicit_and_isolated():
    with pytest.raises(ValueError):
        prepare_request({"case_no":"A","segment_code":"P001-00","use_project_source":True})
    generic=prepare_request({"case_no":"A","segment_code":"P001-00","profile_id":"custom"})
    assert not generic.get("competition_provided_factors")
    widths=[]
    for code in ('P001-00','P002-00','P003-00','P004-00'):
        request=prepare_request({"case_no":"A","segment_code":code,"profile_id":"shulin_residential_2026","use_project_source":True})
        widths.append(next(f['raw_value'] for f in request['competition_provided_factors'] if f['field_id']=='regional_main_road_width'))
    assert widths == [28,7,10,10]


def test_persist_restore_case_and_segment_scope(tmp_path):
    service=PublicDataService(tmp_path,FakeHttp())
    a=service.collect({"case_no":"A","segment_code":"S1","manual_values":{"main_road_width":12}})
    service.collect({"case_no":"A","segment_code":"S2","manual_values":{"main_road_width":9}})
    service.collect({"case_no":"B","segment_code":"S1","manual_values":{"main_road_width":20}})
    assert service.load('A','S1')==a
    assert service.load('A','missing') is None


def test_bundle_uses_real_rule_engines_and_preserves_unknowns(tmp_path):
    bundle=PublicDataService(tmp_path,FakeHttp()).bundle('A')
    assert len(bundle['segments'])==4
    assert len(bundle['table51']['comparisons'])==3
    for comparison in bundle['table51']['comparisons']:
        assert len(comparison['factor_results'])==29
        # PARTIAL_DRAFT: total over resolved factors, unresolved ones listed.
        assert comparison['calculation_mode']=='PARTIAL_DRAFT'
        assert comparison['total_adjustment_pct'] is not None
        assert comparison['excluded_factor_ids']
        assert any(r['adjustment_pct'] is not None for r in comparison['factor_results'])
    table4=bundle['table4']
    assert [c['land_normal_price_raw'] for c in table4['comparisons']]==['130167','135275','170909']
    assert all(c['trial_price'] is not None for c in table4['comparisons'])
    assert all('individual_floor_area_ratio' in c['excluded_factor_ids'] for c in table4['comparisons'])
    assert all(c['weight_status']=='SYSTEM_AUXILIARY_SUGGESTION' and c['weight_requires_human_confirmation']
               for c in table4['comparisons'])
    assert sum(float(c['weight_pct']) for c in table4['comparisons'])==pytest.approx(100)
    assert table4['base_comparison_price'] is not None
    assert '需人工確認' in table4['remarks']['whole_case']


def test_public_distances_feed_corresponding_regional_factors(isolated):
    result = PublicDataCollector(FakeHttp()).collect(isolated)
    points = regional_factor_points(result)
    assert points["regional_school_proximity"]["value"] > 0
    assert points["regional_park_proximity"]["value"] > 0
    assert points["regional_school_proximity"]["status"] == "REFERENCE"


def test_project_documents_extract_existing_page_metadata():
    source = ROOT / "data/sources/competition/shulin_residential_2026"
    appraisal = (source / "題目.pdf").read_bytes()
    criteria = (source / "評價基準明細表.pdf").read_bytes()
    result = extract_case_metadata([("題目.pdf", appraisal)], criteria)
    assert result["segment_code"] == "P001-00"
    assert result["district"] == "樹林區"
    assert result["land_use_type"] == "普通住宅用地"
    assert result["segment_scope"]


def test_boundary_roads_parse_segment_range_text():
    from backend.local_workflow_service import boundary_roads
    assert boundary_roads("沿八德街以西、啟智街及未開闢計畫道路以南、啟智街187巷以東、啟智街187巷24弄以北之捷運開發區"
                          "(變更前為第一種住宅區)") == ["八德街", "啟智街", "啟智街187巷", "啟智街187巷24弄"]
    assert boundary_roads("沿潭興街以西、潭興街107巷21弄以東及以北、潭興街91巷以南之第一種住宅區") == [
        "潭興街", "潭興街107巷21弄", "潭興街91巷"]


def test_segment_geometry_uses_boundary_road_corners(tmp_path):
    from urllib.parse import unquote
    from backend.local_workflow_service import LocalWorkflowService
    corners = {("八德街", "啓智街"): (121.41567, 24.98754), ("八德街", "啓智街187巷24弄"): (121.41599, 24.98711),
               ("啓智街187巷", "啓智街"): (121.41522, 24.98729), ("啓智街187巷", "啓智街187巷24弄"): (121.41559, 24.98678)}

    class RoadHttp:
        def get(self, url, **kwargs):
            meta = {"url": url}
            road = unquote(url).rstrip("/").rsplit("/", 1)[-1]
            if "TextQueryRoad" in url:
                if "啟" in road:
                    return b"<roads/>", meta  # NLSC only knows the 啓 spelling
                return f"<roads><road><name>{road}</name><townName>樹林區</townName><townCode>F17</townCode></road></roads>".encode(), meta
            rows = [(b if a == road else a, xy) for (a, b), xy in corners.items() if road in (a, b)]
            body = "".join(f"<crossRoad><name>{n}</name><x>{x}</x><y>{y}</y></crossRoad>" for n, (x, y) in rows)
            return f"<crossRoads>{body}</crossRoads>".encode(), meta

    segment = {"district": "樹林區", "zone_range_description": "沿八德街以西、啟智街及未開闢計畫道路以南、"
               "啟智街187巷以東、啟智街187巷24弄以北之捷運開發區"}
    location = LocalWorkflowService(tmp_path, RoadHttp())._segment_geometry(segment)
    assert location["selection"] == "BOUNDARY_ROAD_CORNERS"
    assert location["geometry"]["type"] == "Polygon"
    assert 121.4152 < location["longitude"] < 121.4160 and 24.9867 < location["latitude"] < 24.9876


def test_lookalike_places_do_not_fill_form_categories():
    from providers.public_data_collector import name_allowed, nuisance_prefixes, station_kind
    assert not name_allowed("department_store", "樹林百貨商行", require=True)
    assert name_allowed("department_store", "家樂福樹林店", require=True)
    assert not name_allowed("exhibition_hotel", "金都賓館", require=True)
    assert not name_allowed("school_college", "某科技大學文創產學園區")
    assert not name_allowed("service_facility", "樹林長照服務中心", require=True)
    assert name_allowed("service_facility", "樹林郵局", require=True)
    assert nuisance_prefixes("市立殯儀館附設火化場") == ["crematorium"]
    assert nuisance_prefixes("樹林區第一公墓") == ["cemetery"]
    assert station_kind("樹林", {"railway": "station"}) == "major_station_train"


def test_table51_mapping_rows_sit_on_their_printed_labels():
    import fitz
    template = ROOT / "data/templates/shulin"
    rects = {e["field_id"]: e["rect"] for e in json.loads((template / "shulin_table51_mapping.json").read_text("utf-8"))}
    doc = fitz.open(template / "shulin_table51_blank_v1.pdf")
    page = doc[0]
    labels = {"regional_interchange_proximity": "交流道之有無", "regional_road_development_level": "區段內道路規劃",
              "regional_school_proximity": "接近學校之程度", "regional_parking_convenience": "停車場地之便利程度",
              "regional_funeral_facility_proximity": "殯葬設施", "regional_pollution_proximity": "水污染"}
    for field_id, label in labels.items():
        hit = next(r for r in page.search_for(label) if r.x0 < 200)
        _x0, y0, _x1, y1 = rects[f"{field_id}.comp1_grade"]
        assert y0 < (hit.y0 + hit.y1) / 2 < y1, field_id
    subtotals = sorted((r for r in page.search_for("百分比小計") if r.x0 < 200), key=lambda r: r.y0)
    for index, hit in enumerate(subtotals, 1):
        _x0, y0, _x1, y1 = rects[f"category{index}.comp1_subtotal_pct"]
        assert y0 < (hit.y0 + hit.y1) / 2 < y1, index
    assert "remarks.whole_case" in rects
    doc.close()


def test_table3_every_facility_row_has_inside_outside_circles():
    import fitz
    from pdf.shulin_official_pdf_renderer import _table3_row_anchors
    doc = fitz.open(ROOT / "data/templates/shulin/shulin_table3_blank_v1.pdf")
    anchors = _table3_row_anchors(doc[0])
    assert len(anchors) == 37
    assert anchors["major_station_hsr"]["inside"].x1 < anchors["major_station_hsr"]["outside"].x0
    assert anchors["exhibition_hotel"]["lead"] is None or anchors["exhibition_hotel"]["lead"].x0 > 300
    doc.close()


def test_runtime_api_bridge_uses_existing_frontend_extension_points():
    from scripts.serve_app import API_BRIDGE
    page = (ROOT / "frontend/app/case-new.html").read_text("utf-8")
    assert "typeof Api.extractCaseMetadata === 'function'" in page
    assert "typeof Api.createCaseWithDocuments === 'function'" in page
    assert "global.Api.extractCaseMetadata" in API_BRIDGE
    assert "global.Api.createCaseWithDocuments" in API_BRIDGE


def test_lambda_public_strategy_preserves_case_evidence(monkeypatch):
    """The existing collect-data endpoint can opt into the same collector."""
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    handlers = str(ROOT / "backend" / "handlers")
    if handlers not in sys.path:
        sys.path.insert(0, handlers)
    import importlib
    import collect_data
    importlib.reload(collect_data)
    stored = {}
    previous = {"user_submitted_factors": {"base_parcel_factors": [{"field_id": "x"}]},
                "competition_provided_factors": [{"field_id": "old", "raw_value": 1}]}
    monkeypatch.setattr(collect_data.case_store, "get_case_meta", lambda _: {
        "case_no": "A", "city": "新北市", "district": "樹林區", "segment_code": "S1",
        "base_parcel_id": "樹林段1地號",
    })
    monkeypatch.setattr(collect_data.case_store, "get_record", lambda *_: previous)
    monkeypatch.setattr(collect_data.case_store, "put_record", lambda _case, _sk, value: stored.update(value))
    captured = {}
    def fake_collect(_self, request):
        captured.update(request)
        return {"case_no": "A", "segment_code": "S1", "points": [{"field": "park_name", "value": "公園"}]}
    monkeypatch.setattr(PublicDataCollector, "collect", fake_collect)
    fixed = [{"field_id": "regional_park_distance_m", "raw_value": 500}]
    event = {"pathParameters": {"id": "A"}, "body": json.dumps({
        "acquisition_strategy": "public", "competition_provided_factors": fixed,
    })}
    response = collect_data.collect_data(event, None)
    assert response["statusCode"] == 200
    assert captured["competition_provided_factors"] == fixed
    assert stored["competition_provided_factors"] == fixed
    assert stored["user_submitted_factors"] == previous["user_submitted_factors"]
    assert stored["public_data_draft"]["points"][0]["value"] == "公園"
