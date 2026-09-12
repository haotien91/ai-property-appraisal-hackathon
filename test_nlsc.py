"""NLSC 圖台網址、宗地驗證與設施距離的測試。

預設只跑不連網的邏輯測試；加 --live 才會呼叫真實 NLSC／OSM API：
    python -m pytest test_nlsc.py
    python -m pytest test_nlsc.py --live
"""

from __future__ import annotations

import base64
import io
import json
import math
import tempfile
from pathlib import Path

import pytest
import requests
from PIL import Image
from shapely.geometry import Point, Polygon

from facility_distance import (
    FacilityGeometry,
    GeometryResolver,
    GeometrySource,
    OverpassGeometryProvider,
    compute_distance,
    measure_nearest_funeral_distances,
)
from getFacility import (
    Facility,
    FacilityCategory,
    FuneralFacilityCategory,
    _parse_facility,
    _validate_search_parameters,
    find_nearest_funeral_facilities,
)
from nlsc_map_url import (
    BaseMap,
    CountyCode,
    ExtraLayer,
    LandParcel,
    _normalize_base_map,
    _normalize_layers,
    build_coordinate_url,
    build_goland_url,
    build_map_plan,
    build_open_url,
)
from nlsc_parcel_map import (
    CADASTRAL_TILE_MAX_ZOOM,
    CADASTRAL_TILE_MIN_ZOOM,
    CADASTRAL_TILE_REFERER,
    CADASTRAL_TILE_URL_TEMPLATE,
    DEFAULT_PARCEL_LAYERS,
    MAX_SAFE_ZOOM,
    MIN_ZOOM,
    PARCEL_READING_BASE_MAP,
    ParcelInfo,
    _decode_b64_text,
    _fit_zoom,
    _iter_json_documents,
    _to_float,
    build_parcel_view,
    verify_parcels,
)
from render_parcel_map import (
    DEFAULT_LABEL_OVERLAY,
    LABEL_OVERLAY_WITH_HOUSE_NUMBER,
    _SESSION_GATED_LAYERS,
    _fit_zoom_for_viewport,
    _load_font,
    _pick_road_label_anchor,
    _render_rotated_label,
    _road_label_candidates,
    is_alley,
    _tile_url,
    build_arg_parser,
    create_tile_session,
    fetch_road_names,
    lonlat_to_pixel,
    main,
    parse_parcel_token,
    pixel_to_lonlat,
    render_parcel_map,
)

# 已驗證存在的實際地號：新北市 F、金美段 1027、489 與 490。
KNOWN_SECTION = "1027"
KNOWN_PARCELS = ("489", "490")
MISSING_PARCEL = "489-1"


# ---------------------------------------------------------------- 縣市代碼


class TestCountyCode:
    @pytest.mark.parametrize(
        ("value", "expected_code"),
        [
            ("F", "F"),
            ("f", "F"),
            ("新北市", "F"),
            ("新北", "F"),
            ("臺北縣", "F"),  # 改制前舊名沿用同一代碼
            ("台北縣", "F"),  # 台／臺 正規化
            ("臺北市", "A"),
            ("台中市", "B"),
            ("桃園縣", "H"),
            ("桃園市", "H"),
        ],
    )
    def test_parse_accepts_codes_names_and_aliases(self, value, expected_code):
        assert CountyCode.parse(value).code == expected_code

    def test_parse_is_idempotent_for_enum(self):
        assert CountyCode.parse(CountyCode.NEW_TAIPEI) is CountyCode.NEW_TAIPEI

    def test_taipei_city_wins_over_county_alias_when_abbreviated(self):
        # 「臺北」同時是臺北市簡稱與臺北縣簡稱，應解析為現行的臺北市。
        assert CountyCode.parse("臺北") is CountyCode.TAIPEI

    def test_codes_are_unique(self):
        codes = [county.code for county in CountyCode]
        assert len(codes) == len(set(codes))

    @pytest.mark.parametrize("value", ["", "   ", "火星市", "9", "ZZ市"])
    def test_parse_rejects_invalid(self, value):
        with pytest.raises(ValueError):
            CountyCode.parse(value)

    def test_error_message_lists_current_counties_only(self):
        with pytest.raises(ValueError) as exc:
            CountyCode.parse("火星市")
        message = str(exc.value)
        assert "F=新北市" in message
        assert "臺中縣" not in message  # 已廢止的縣市不應出現在建議清單


# ---------------------------------------------------------------- 地段地號


class TestLandParcel:
    @pytest.mark.parametrize(
        ("section", "number", "expected"),
        [
            ("1027", "489", "102704890000"),
            ("1027", 489, "102704890000"),
            (1027, 489, "102704890000"),
            ("1027", "489-1", "102704890001"),
            ("1027", "1", "102700010000"),
            ("1027", "9999-9999", "102799999999"),
            ("12", "489", "001204890000"),  # 段代碼自動補零
            ("1027", " 489 ", "102704890000"),  # 去除空白
        ],
    )
    def test_parse_encodes_12_digit_code(self, section, number, expected):
        assert LandParcel.parse(section, number).code == expected
        assert len(LandParcel.parse(section, number).code) == 12

    def test_code_is_section_plus_land_number(self):
        parcel = LandParcel.parse(KNOWN_SECTION, "489")
        assert parcel.section_code == "1027"
        assert parcel.land_number_code == "04890000"
        assert parcel.code == parcel.section_code + parcel.land_number_code

    def test_str_returns_code(self):
        assert str(LandParcel.parse("1027", "489")) == "102704890000"

    def test_display_includes_child_number_only_when_present(self):
        assert "489 地號" in LandParcel.parse("1027", "489").display
        assert "489-1 地號" in LandParcel.parse("1027", "489-1").display

    def test_is_hashable_and_comparable(self):
        first = LandParcel.parse("1027", "489")
        second = LandParcel.parse("1027", "489")
        assert first == second
        assert len({first, second}) == 1

    @pytest.mark.parametrize(
        ("section", "number"),
        [
            ("1027", "abc"),
            ("1027", ""),
            ("1027", "489-x"),
            ("1027", "0"),  # 母號不可為 0
            ("1027", "10000"),  # 母號超過 4 位
            ("12345", "489"),  # 段代碼超過 4 位
            ("abcd", "489"),  # 段代碼非數字
        ],
    )
    def test_parse_rejects_invalid(self, section, number):
        with pytest.raises(ValueError):
            LandParcel.parse(section, number)

    def test_direct_construction_validates_section_length(self):
        with pytest.raises(ValueError):
            LandParcel(section_code="102", parent_number=489)


# ---------------------------------------------------------------- 網址組裝


class TestUrlNormalizers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("EMAP", "EMAP_B"),
            ("EMAP_B", "EMAP_B"),  # 已有後綴不重複加
            (BaseMap.EMAP, "EMAP_B"),
            (BaseMap.PHOTO2, "PHOTO2_B"),
        ],
    )
    def test_base_map_gets_single_b_suffix(self, value, expected):
        assert _normalize_base_map(value) == expected

    def test_base_map_rejects_empty(self):
        with pytest.raises(ValueError):
            _normalize_base_map("   ")

    def test_layers_join_with_comma_and_drop_blanks(self):
        assert _normalize_layers([]) == ""
        assert _normalize_layers(["", "  "]) == ""
        assert (
            _normalize_layers([ExtraLayer.URBAN, ExtraLayer.DMAPS]) == "URBAN,DMAPS"
        )

    @pytest.mark.parametrize("bad", ["A,B", "A/B"])
    def test_layers_reject_separator_inside_code(self, bad):
        with pytest.raises(ValueError):
            _normalize_layers([bad])


class TestBuildOpenUrl:
    def test_layers_only(self):
        url = build_open_url(BaseMap.EMAP, (ExtraLayer.URBAN, ExtraLayer.DMAPS))
        assert url == "https://maps.nlsc.gov.tw/open/EMAP_B/URBAN,DMAPS"

    def test_base_map_without_layers(self):
        assert build_open_url(BaseMap.EMAP) == (
            "https://maps.nlsc.gov.tw/open/EMAP_B"
        )


class TestBuildGolandUrl:
    def test_single_parcel_without_layers(self):
        url = build_goland_url("新北市", LandParcel.parse("1027", "489"))
        assert url == "https://maps.nlsc.gov.tw/goland/F/102704890000"

    def test_multiple_parcels_joined_with_comma(self):
        url = build_goland_url(
            "F",
            [LandParcel.parse("1027", "489"), LandParcel.parse("1027", "490")],
            BaseMap.EMAP,
            (ExtraLayer.URBAN, ExtraLayer.DMAPS),
        )
        assert url == (
            "https://maps.nlsc.gov.tw/goland/F/"
            "102704890000,102704900000/EMAP_B/URBAN,DMAPS"
        )

    def test_layers_require_base_map(self):
        with pytest.raises(ValueError):
            build_goland_url(
                "F",
                LandParcel.parse("1027", "489"),
                layers=(ExtraLayer.DMAPS,),
            )

    def test_empty_parcels_rejected(self):
        with pytest.raises(ValueError):
            build_goland_url("F", [])


class TestBuildCoordinateUrl:
    def test_coordinates_only(self):
        url = build_coordinate_url(121.63788, 25.222398)
        assert url == "https://maps.nlsc.gov.tw/go/121.637880/25.222398"

    def test_with_zoom_base_map_and_layers(self):
        url = build_coordinate_url(
            121.63788,
            25.222398,
            zoom=17,
            base_map=BaseMap.EMAP,
            layers=(ExtraLayer.URBAN,),
        )
        assert url == (
            "https://maps.nlsc.gov.tw/go/121.637880/25.222398/17/EMAP_B/URBAN"
        )

    @pytest.mark.parametrize(
        ("longitude", "latitude"),
        [(181, 25), (-181, 25), (121, 91), (121, -91)],
    )
    def test_rejects_out_of_range_coordinates(self, longitude, latitude):
        with pytest.raises(ValueError):
            build_coordinate_url(longitude, latitude)

    @pytest.mark.parametrize("zoom", [0, 21, -1])
    def test_rejects_out_of_range_zoom(self, zoom):
        with pytest.raises(ValueError):
            build_coordinate_url(121.6, 25.2, zoom=zoom)

    def test_base_map_requires_zoom(self):
        with pytest.raises(ValueError):
            build_coordinate_url(121.6, 25.2, base_map=BaseMap.EMAP)


class TestBuildMapPlan:
    def test_plan_exposes_three_urls(self):
        plan = build_map_plan("新北市", LandParcel.parse("1027", "489"))
        assert plan.county is CountyCode.NEW_TAIPEI
        assert plan.layer_url.startswith("https://maps.nlsc.gov.tw/open/")
        assert plan.parcel_url == "https://maps.nlsc.gov.tw/goland/F/102704890000"
        assert "102704890000" in plan.combined_url
        assert "EMAP_B" in plan.combined_url

    def test_default_layers_include_zoning_and_cadastral(self):
        plan = build_map_plan("F", LandParcel.parse("1027", "489"))
        assert "URBAN" in plan.combined_url
        assert "DMAPS" in plan.combined_url


# ---------------------------------------------------------------- 宗地驗證


class TestParcelHelpers:
    def test_decode_b64_text(self):
        assert _decode_b64_text(base64.b64encode("金美段".encode()).decode()) == "金美段"
        assert _decode_b64_text("") == ""

    def test_decode_b64_text_returns_original_on_failure(self):
        assert _decode_b64_text("不是base64") == "不是base64"

    def test_iter_json_documents_parses_concatenated_arrays(self):
        # 著色 API 多筆地號時會把多個 array 直接串接，不是合法單一 JSON。
        body = json.dumps([{"a": 1}]) + json.dumps([{"b": 2}])
        with pytest.raises(json.JSONDecodeError):
            json.loads(body)

        documents = _iter_json_documents(body)
        assert documents == [[{"a": 1}], [{"b": 2}]]

    def test_iter_json_documents_handles_single_and_empty(self):
        assert _iter_json_documents("") == []
        assert _iter_json_documents("[{}]") == [[{}]]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("1.5", 1.5), (2, 2.0), (None, None), ("", None), ("abc", None)],
    )
    def test_to_float(self, value, expected):
        assert _to_float(value) == expected


class TestParcelLayerDefaults:
    """地號文字只在地籍圖上；電子地圖上的數字是門牌，不可混淆。"""

    def test_defaults_include_cadastral_for_parcel_labels(self):
        assert ExtraLayer.DMAPS in DEFAULT_PARCEL_LAYERS
        assert ExtraLayer.URBAN in DEFAULT_PARCEL_LAYERS

    def test_defaults_exclude_text_vector_layer(self):
        # NLSCVET 是向量地名註記，不是地號，且非 WMTS 圖磚，不應混充宗地文字。
        codes = {str(layer) for layer in DEFAULT_PARCEL_LAYERS}
        assert "NLSCVET" not in codes

    def test_reading_base_map_has_no_house_numbers(self):
        # 判讀地號時要避開含門牌的底圖。
        assert PARCEL_READING_BASE_MAP is BaseMap.EMAP_NO_HOUSE_NUMBER
        assert str(PARCEL_READING_BASE_MAP) == "EMAP16"

    def test_cadastral_tile_zoom_range_within_safe_cap(self):
        assert CADASTRAL_TILE_MIN_ZOOM < CADASTRAL_TILE_MAX_ZOOM
        assert CADASTRAL_TILE_MAX_ZOOM == MAX_SAFE_ZOOM

    def test_cadastral_tile_endpoint_uses_landmaps_host(self):
        # 走 wmts.nlsc.gov.tw 取 DMAPS 一律回空白圖，必須用 landmaps。
        assert CADASTRAL_TILE_URL_TEMPLATE.startswith(
            "https://landmaps.nlsc.gov.tw/S_Maps/wmts/DMAPS/"
        )
        assert CADASTRAL_TILE_REFERER == "https://maps.nlsc.gov.tw/"


class TestFitZoom:
    def test_tiny_parcel_is_capped_at_max_safe_zoom(self):
        # 宗地僅約 20 公尺，若不設上限會縮放到 z21+（該層級無圖磚）。
        zoom = _fit_zoom(121.637449, 25.221357, 121.637647, 25.221534)
        assert zoom == MAX_SAFE_ZOOM
        assert zoom <= 20

    def test_larger_extent_gets_smaller_zoom(self):
        small = _fit_zoom(121.60, 25.20, 121.61, 25.21)
        large = _fit_zoom(121.00, 25.00, 122.00, 26.00)
        assert large < small
        assert large >= 1

    def test_zoom_never_exceeds_cap(self):
        for delta in (1e-6, 1e-4, 1e-2, 1.0):
            zoom = _fit_zoom(121.6, 25.2, 121.6 + delta, 25.2 + delta)
            assert 1 <= zoom <= MAX_SAFE_ZOOM


class TestParcelInfo:
    def _info(self, **overrides):
        defaults = dict(
            parcel=LandParcel.parse("1027", "489"),
            exists=True,
            section_name="金美段",
            land_office="汐止",
            min_longitude=121.6374,
            min_latitude=25.2213,
            max_longitude=121.6376,
            max_latitude=25.2215,
        )
        defaults.update(overrides)
        return ParcelInfo(**defaults)

    def test_center_is_extent_midpoint(self):
        info = self._info()
        assert info.has_extent
        longitude, latitude = info.center
        assert longitude == pytest.approx(121.6375, abs=1e-6)
        assert latitude == pytest.approx(25.2214, abs=1e-6)

    def test_missing_extent_has_no_center(self):
        info = self._info(min_longitude=None)
        assert not info.has_extent
        assert info.center is None

    def test_display_does_not_duplicate_section_suffix(self):
        # sectStr 已含「段」，不可再補一個。
        assert self._info().display == "金美段 489 地號"
        assert "段段" not in self._info().display

    def test_display_falls_back_to_section_code(self):
        assert self._info(section_name="").display == "1027 段 489 地號"

    def test_save_tint_image_writes_png(self, tmp_path):
        payload = b"\x89PNG\r\n\x1a\nfake"
        info = self._info(tint_image_png=payload)
        target = info.save_tint_image(tmp_path / "out" / "tint.png")
        assert target is not None
        assert target.read_bytes() == payload

    def test_save_tint_image_returns_none_without_image(self, tmp_path):
        assert self._info().save_tint_image(tmp_path / "none.png") is None


# ---------------------------------------------------------------- 設施距離


def _facility(longitude: float, latitude: float, **overrides) -> Facility:
    defaults = dict(
        category=FacilityCategory.DISAMENITY,
        geometry_type="Point",
        id="TEST0001",
        name="測試公墓",
        short_name="測試",
        address="新北市金山區",
        telephone="",
        longitude=longitude,
        latitude=latitude,
        distance_meters=500,
        mark_type=FuneralFacilityCategory.CEMETERY.mark_type,
    )
    defaults.update(overrides)
    return Facility(**defaults)


class TestGeometrySource:
    def test_only_official_cadastre_is_authoritative(self):
        assert GeometrySource.OFFICIAL_CADASTRE.authoritative is True
        assert GeometrySource.OSM_POLYGON.authoritative is False
        assert GeometrySource.REPRESENTATIVE_POINT.authoritative is False


class TestComputeDistance:
    def _geometry(self, geom, source=GeometrySource.OSM_POLYGON):
        return FacilityGeometry(
            facility_id="TEST0001",
            name="測試公墓",
            mark_type=FuneralFacilityCategory.CEMETERY.mark_type,
            geometry=geom,
            source=source,
        )

    def test_point_inside_polygon_reports_zero_and_inside(self):
        polygon = Polygon(
            [
                (121.600, 25.200),
                (121.610, 25.200),
                (121.610, 25.210),
                (121.600, 25.210),
            ]
        )
        facility = _facility(121.605, 25.205)
        result = compute_distance(121.605, 25.205, facility, self._geometry(polygon))

        assert result.inside is True
        assert result.distance_meters == 0.0

    def test_point_outside_polygon_measures_to_boundary(self):
        polygon = Polygon(
            [
                (121.600, 25.200),
                (121.601, 25.200),
                (121.601, 25.201),
                (121.600, 25.201),
            ]
        )
        # 位於西側 0.001 度經度 ≈ 100 公尺處。
        facility = _facility(121.6005, 25.2005)
        result = compute_distance(121.599, 25.2005, facility, self._geometry(polygon))

        assert result.inside is False
        assert result.distance_meters == pytest.approx(100, abs=15)

    def test_boundary_distance_is_shorter_than_centroid_distance(self):
        # 這是改用面資料的重點：邊界距離必然小於到代表點的距離。
        polygon = Polygon(
            [
                (121.600, 25.200),
                (121.610, 25.200),
                (121.610, 25.210),
                (121.600, 25.210),
            ]
        )
        centroid = polygon.centroid
        facility = _facility(centroid.x, centroid.y)

        boundary = compute_distance(
            121.598, 25.205, facility, self._geometry(polygon)
        )
        point_only = compute_distance(
            121.598,
            25.205,
            facility,
            self._geometry(Point(centroid.x, centroid.y)),
        )
        assert boundary.distance_meters < point_only.distance_meters

    def test_source_propagates_to_result(self):
        polygon = Polygon(
            [(121.60, 25.20), (121.61, 25.20), (121.61, 25.21), (121.60, 25.21)]
        )
        facility = _facility(121.605, 25.205)

        official = compute_distance(
            121.598,
            25.205,
            facility,
            self._geometry(polygon, GeometrySource.OFFICIAL_CADASTRE),
        )
        osm = compute_distance(121.598, 25.205, facility, self._geometry(polygon))

        assert official.is_authoritative is True
        assert osm.is_authoritative is False
        assert osm.to_dict()["source"] == "osm"


class TestGeometryResolver:
    class _NullProvider:
        def fetch(self, facility):
            return None

    class _FailingProvider:
        def fetch(self, facility):
            raise requests.ConnectionError("模擬連線失敗")

    class _StubProvider:
        def __init__(self, geometry):
            self._geometry = geometry
            self.calls = 0

        def fetch(self, facility):
            self.calls += 1
            return self._geometry

    def _stub_geometry(self):
        return FacilityGeometry(
            facility_id="TEST0001",
            name="測試公墓",
            mark_type=FuneralFacilityCategory.CEMETERY.mark_type,
            geometry=Polygon(
                [(121.60, 25.20), (121.61, 25.20), (121.61, 25.21), (121.60, 25.21)]
            ),
            source=GeometrySource.OFFICIAL_CADASTRE,
        )

    def test_falls_back_to_representative_point_when_all_fail(self):
        resolver = GeometryResolver([self._NullProvider(), self._FailingProvider()])
        geometry = resolver.resolve(_facility(121.6, 25.2))

        assert geometry.source is GeometrySource.REPRESENTATIVE_POINT
        assert geometry.geometry.equals(Point(121.6, 25.2))

    def test_first_successful_provider_wins(self):
        stub = self._StubProvider(self._stub_geometry())
        second = self._StubProvider(self._stub_geometry())
        resolver = GeometryResolver([stub, second])

        geometry = resolver.resolve(_facility(121.6, 25.2))
        assert geometry.source is GeometrySource.OFFICIAL_CADASTRE
        assert stub.calls == 1
        assert second.calls == 0  # 前一個成功就不再往下試

    def test_network_error_is_skipped_not_raised(self):
        stub = self._StubProvider(self._stub_geometry())
        resolver = GeometryResolver([self._FailingProvider(), stub])

        geometry = resolver.resolve(_facility(121.6, 25.2))
        assert geometry.source is GeometrySource.OFFICIAL_CADASTRE
        assert stub.calls == 1


class TestOverpassElementParsing:
    parse = staticmethod(OverpassGeometryProvider._element_to_geometry)

    def test_closed_way_becomes_polygon(self):
        element = {
            "type": "way",
            "geometry": [
                {"lon": 121.60, "lat": 25.20},
                {"lon": 121.61, "lat": 25.20},
                {"lon": 121.61, "lat": 25.21},
                {"lon": 121.60, "lat": 25.20},
            ],
        }
        geom = self.parse(element)
        assert isinstance(geom, Polygon)
        assert geom.area > 0

    def test_open_way_is_closed_automatically(self):
        element = {
            "type": "way",
            "geometry": [
                {"lon": 121.60, "lat": 25.20},
                {"lon": 121.61, "lat": 25.20},
                {"lon": 121.61, "lat": 25.21},
            ],
        }
        geom = self.parse(element)
        assert isinstance(geom, Polygon)
        assert geom.area > 0

    def test_way_with_too_few_nodes_is_rejected(self):
        element = {
            "type": "way",
            "geometry": [
                {"lon": 121.60, "lat": 25.20},
                {"lon": 121.61, "lat": 25.20},
            ],
        }
        assert self.parse(element) is None

    def test_relation_outer_members_become_polygon(self):
        element = {
            "type": "relation",
            "members": [
                {
                    "type": "way",
                    "role": "outer",
                    "geometry": [
                        {"lon": 121.60, "lat": 25.20},
                        {"lon": 121.61, "lat": 25.20},
                        {"lon": 121.61, "lat": 25.21},
                    ],
                }
            ],
        }
        geom = self.parse(element)
        assert isinstance(geom, Polygon)

    def test_unknown_element_type_returns_none(self):
        assert self.parse({"type": "node", "lon": 121.6, "lat": 25.2}) is None
        assert self.parse({}) is None


class TestOverpassQueryBuilding:
    def test_query_includes_user_agent_safe_selectors_and_center(self):
        provider = OverpassGeometryProvider(search_radius_meters=250)
        query = provider._build_query(121.637242, 25.226921)

        assert "landuse" in query and "cemetery" in query
        assert "around:250,25.226921,121.637242" in query  # Overpass 是 lat,lon 順序
        assert query.startswith("[out:json]")
        assert "out geom;" in query


# ---------------------------------------------------------------- 設施查詢


class TestFacilityCategories:
    def test_disamenity_maps_to_com_012(self):
        category = FacilityCategory.DISAMENITY
        assert category.service_code == "COM_012"
        assert category.api_path == "dis"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("dis", FacilityCategory.DISAMENITY),
            ("DIS", FacilityCategory.DISAMENITY),
            ("com012", FacilityCategory.DISAMENITY),
            ("COM_012", FacilityCategory.DISAMENITY),
            ("edu", FacilityCategory.EDUCATION),
            ("med", FacilityCategory.MEDICAL),
            ("bus", FacilityCategory.BUSINESS),
        ],
    )
    def test_parse_accepts_aliases(self, value, expected):
        assert FacilityCategory.parse(value) is expected

    def test_parse_rejects_unknown(self):
        with pytest.raises(ValueError):
            FacilityCategory.parse("unknown")

    def test_funeral_mark_types_are_distinct_and_documented(self):
        expected = {
            FuneralFacilityCategory.CEMETERY: "9350200",
            FuneralFacilityCategory.FUNERAL_HOME: "9930201",
            FuneralFacilityCategory.CREMATORIUM: "9930202",
            FuneralFacilityCategory.COLUMBARIUM: "9930203",
        }
        for category, mark_type in expected.items():
            assert category.mark_type == mark_type

        mark_types = [c.mark_type for c in FuneralFacilityCategory]
        assert len(mark_types) == len(set(mark_types))


class TestSearchParameterValidation:
    @pytest.mark.parametrize(
        ("longitude", "latitude", "radius"),
        [
            (181, 25, 1000),
            (121, 91, 1000),
            (121, 25, 0),
            (121, 25, -1),
            (121, 25, 1.5),  # 必須是整數公尺
            (121, 25, True),  # bool 不算整數
        ],
    )
    def test_rejects_invalid(self, longitude, latitude, radius):
        with pytest.raises(ValueError):
            _validate_search_parameters(longitude, latitude, radius)

    def test_accepts_valid(self):
        assert _validate_search_parameters(121.637880, 25.222398, 2000) is None


class TestParseFacility:
    def test_parses_api_payload(self):
        facility = _parse_facility(
            FacilityCategory.DISAMENITY,
            {
                "type": "Point",
                "lon": "121.637242",
                "lat": "25.226921",
                "marktype": 9350200,
                "id": "B0001",
                "name": "新北市金山區第一公墓",
                "sname": "第一公墓",
                "addr": "新北市金山區",
                "tel": "",
                "distance": "607",
            },
        )
        assert facility.longitude == pytest.approx(121.637242)
        assert facility.distance_meters == 607
        assert facility.mark_type == "9350200"  # 一律正規化為字串
        assert facility.to_dict()["marktype"] == "9350200"

    @pytest.mark.parametrize(
        "payload",
        [
            {"lat": 25.2, "distance": 100},  # 缺 lon
            {"lon": 121.6, "distance": 100},  # 缺 lat
            {"lon": 121.6, "lat": 25.2},  # 缺 distance
            {"lon": "x", "lat": 25.2, "distance": 100},
        ],
    )
    def test_rejects_incomplete_payload(self, payload):
        with pytest.raises(ValueError):
            _parse_facility(FacilityCategory.DISAMENITY, payload)


# ---------------------------------------------------------------- 連網測試


@pytest.mark.live
class TestLiveParcelVerification:
    def test_known_parcels_exist_and_missing_one_is_reported(self):
        parcels = [
            LandParcel.parse(KNOWN_SECTION, KNOWN_PARCELS[0]),
            LandParcel.parse(KNOWN_SECTION, KNOWN_PARCELS[1]),
            LandParcel.parse(KNOWN_SECTION, MISSING_PARCEL),
        ]
        infos = verify_parcels("新北市", parcels)
        by_code = {info.parcel.code: info for info in infos}

        assert by_code["102704890000"].exists is True
        assert by_code["102704900000"].exists is True
        assert by_code["102704890001"].exists is False

    def test_existing_parcel_returns_section_and_extent(self):
        info = verify_parcels(
            "新北市",
            LandParcel.parse(KNOWN_SECTION, KNOWN_PARCELS[0]),
        )[0]

        assert info.exists
        assert info.section_name == "金美段"
        assert info.land_office == "汐止"
        assert info.has_extent
        longitude, latitude = info.center
        # 應落在金山區已知範圍附近。
        assert 121.63 < longitude < 121.65
        assert 25.21 < latitude < 25.23

    def test_view_plan_excludes_missing_parcels_and_caps_zoom(self):
        plan = build_parcel_view(
            "新北市",
            [
                LandParcel.parse(KNOWN_SECTION, KNOWN_PARCELS[0]),
                LandParcel.parse(KNOWN_SECTION, MISSING_PARCEL),
            ],
        )

        assert len(plan.found) == 1
        assert len(plan.missing) == 1
        # 不存在的地號不應出現在著色網址，否則整批看起來像沒作用。
        assert "102704890001" not in plan.parcel_url
        assert "102704890000" in plan.parcel_url
        assert plan.zoom <= MAX_SAFE_ZOOM

    def test_tint_image_is_png(self):
        info = verify_parcels(
            "新北市",
            LandParcel.parse(KNOWN_SECTION, KNOWN_PARCELS[0]),
            fetch_images=True,
        )[0]
        assert info.tint_image_png is not None
        assert info.tint_image_png.startswith(b"\x89PNG")


@pytest.mark.live
class TestLiveMapUrls:
    @staticmethod
    def _session():
        from getFacility import NlscSSLAdapter

        session = requests.Session()
        session.mount("https://maps.nlsc.gov.tw/", NlscSSLAdapter())
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) nlsc-test/1.0",
                "Accept": "text/html",
            }
        )
        return session

    def test_generated_urls_load_the_map_portal(self):
        plan = build_map_plan("新北市", LandParcel.parse(KNOWN_SECTION, "489"))
        with self._session() as session:
            for url in (plan.layer_url, plan.parcel_url, plan.combined_url):
                response = session.get(url, timeout=60)
                assert response.status_code == 200
                assert "國土測繪圖資服務雲" in response.text

    def test_goland_expands_to_mapshow_with_parcel_and_layers(self):
        url = build_goland_url(
            "新北市",
            [LandParcel.parse("1027", "489"), LandParcel.parse("1027", "490")],
            BaseMap.EMAP,
            (ExtraLayer.URBAN, ExtraLayer.DMAPS),
        )
        with self._session() as session:
            body = session.get(url, timeout=60).text

        assert "parno=102704890000,102704900000" in body.replace("&amp;", "&")
        assert "overlay=URBAN,DMAPS" in body.replace("&amp;", "&")


class TestTileMath:
    def test_pixel_origin_and_center(self):
        # z0 世界為 256x256；經緯度 (0,0) 應落在正中央。
        x, y = lonlat_to_pixel(0.0, 0.0, 0)
        assert x == pytest.approx(128.0)
        assert y == pytest.approx(128.0)

    def test_pixel_scales_with_zoom(self):
        x0, _ = lonlat_to_pixel(121.6, 25.2, 10)
        x1, _ = lonlat_to_pixel(121.6, 25.2, 11)
        assert x1 == pytest.approx(x0 * 2, rel=1e-9)

    def test_longitude_increases_x_latitude_increases_decreases_y(self):
        base_x, base_y = lonlat_to_pixel(121.6, 25.2, 18)
        east_x, _ = lonlat_to_pixel(121.7, 25.2, 18)
        _, north_y = lonlat_to_pixel(121.6, 25.3, 18)
        assert east_x > base_x
        assert north_y < base_y  # 緯度越高，像素 y 越小

    def test_extreme_latitude_is_clamped(self):
        # 未夾住會產生無限值，導致整張圖無法計算。
        _, y = lonlat_to_pixel(121.6, 90.0, 10)
        assert math.isfinite(y)


class TestTileRouting:
    def test_cadastral_uses_landmaps_with_referer(self):
        url, headers = _tile_url("DMAPS", 19, 439291, 224165)
        assert url.startswith("https://landmaps.nlsc.gov.tw/S_Maps/wmts/DMAPS/")
        assert headers["Referer"] == CADASTRAL_TILE_REFERER

    def test_zoning_uses_wmts_and_is_session_gated(self):
        # URBAN 未先造訪圖台建立授權時，所有層級都可能回 0 位元組。
        assert "URBAN" in _SESSION_GATED_LAYERS
        url, headers = _tile_url("URBAN", 20, 878583, 448330)
        assert url.startswith("https://wmts.nlsc.gov.tw/wmts/URBAN/")
        assert "Referer" in headers

    def test_plain_base_map_needs_no_referer(self):
        url, headers = _tile_url("EMAP16", 20, 878583, 448330)
        assert url.startswith("https://wmts.nlsc.gov.tw/wmts/EMAP16/")
        assert "Referer" not in headers

    def test_url_contains_zoom_row_col_in_wmts_order(self):
        url, _ = _tile_url("EMAP16", 18, 111, 222)
        # WMTS RESTful 順序為 {TileMatrix}/{TileRow}/{TileCol}
        assert url.endswith("/18/222/111")


class TestPixelRoundTrip:
    @pytest.mark.parametrize(
        ("longitude", "latitude"),
        [(121.637566, 25.221432), (0.0, 0.0), (-120.5, -33.9), (139.7, 35.7)],
    )
    def test_lonlat_pixel_round_trip(self, longitude, latitude):
        for zoom in (10, 15, 20):
            x, y = lonlat_to_pixel(longitude, latitude, zoom)
            back_lon, back_lat = pixel_to_lonlat(x, y, zoom)
            assert back_lon == pytest.approx(longitude, abs=1e-6)
            assert back_lat == pytest.approx(latitude, abs=1e-6)

    def test_viewport_bbox_is_west_south_east_north(self):
        zoom = 18
        center_x, center_y = lonlat_to_pixel(121.6376, 25.2214, zoom)
        left = center_x - 600
        top = center_y - 450
        west, north = pixel_to_lonlat(left, top, zoom)
        east, south = pixel_to_lonlat(left + 1200, top + 900, zoom)

        assert west < east
        assert south < north  # 螢幕上方是較高緯度


class TestRoadLabelAnchor:
    def test_picks_longest_visible_segment(self):
        points = [(10.0, 10.0), (30.0, 10.0), (330.0, 10.0)]
        anchor = _pick_road_label_anchor(points, 400, 300)
        assert anchor is not None
        mid_x, mid_y, angle = anchor
        # 最長線段是 30→330，中點應為 180。
        assert mid_x == pytest.approx(180.0)
        assert mid_y == pytest.approx(10.0)
        assert angle == pytest.approx(0.0, abs=1e-6)

    def test_returns_none_when_all_offscreen(self):
        points = [(-500.0, -500.0), (-400.0, -480.0)]
        assert _pick_road_label_anchor(points, 400, 300) is None

    def test_angle_kept_upright(self):
        # 由右下往左上的線段，角度須落在可讀範圍內。
        points = [(300.0, 250.0), (50.0, 50.0)]
        anchor = _pick_road_label_anchor(points, 400, 300)
        assert anchor is not None
        _, _, angle = anchor
        assert -90 <= angle <= 90

    def test_vertical_road_angle_is_upright(self):
        points = [(100.0, 250.0), (100.0, 20.0)]
        anchor = _pick_road_label_anchor(points, 400, 300)
        assert anchor is not None
        _, _, angle = anchor
        assert -90 <= angle <= 90
        assert abs(abs(angle) - 90) == pytest.approx(0.0, abs=1e-6)

    def test_single_point_has_no_segment(self):
        assert _pick_road_label_anchor([(100.0, 100.0)], 400, 300) is None


class TestRoadLabelCandidates:
    """必須提供多個候選位置，否則碰撞時整條路都標不出來。"""

    def test_returns_multiple_candidates_per_segment(self):
        points = [(10.0, 10.0), (310.0, 10.0)]
        candidates = _road_label_candidates(points, 400, 300)
        # 單一線段也應給出中點與 1/3、2/3 三個備援位置。
        assert len(candidates) >= 3
        xs = sorted(round(item[0]) for item in candidates)
        assert xs == [112, 160, 208]

    def test_candidates_sorted_by_segment_length(self):
        points = [(10.0, 50.0), (40.0, 50.0), (340.0, 50.0)]
        candidates = _road_label_candidates(points, 400, 300)
        lengths = [item[3] for item in candidates]
        assert lengths == sorted(lengths, reverse=True)
        assert lengths[0] == pytest.approx(300.0)

    def test_offscreen_candidates_are_dropped(self):
        points = [(-500.0, -500.0), (-400.0, -480.0)]
        assert _road_label_candidates(points, 400, 300) == []

    def test_anchor_helper_returns_longest_candidate(self):
        points = [(10.0, 50.0), (40.0, 50.0), (340.0, 50.0)]
        anchor = _pick_road_label_anchor(points, 400, 300)
        candidates = _road_label_candidates(points, 400, 300)
        assert anchor == candidates[0][:3]


class TestAlleyClassification:
    @pytest.mark.parametrize(
        "name",
        ["中山路343巷", "仁愛路67巷", "溫泉路2巷", "某路12弄"],
    )
    def test_alleys_detected(self, name):
        assert is_alley(name) is True

    @pytest.mark.parametrize(
        "name",
        ["中山路", "中正路", "金包里街", "忠孝一路", "溫泉路", "三民街"],
    )
    def test_main_roads_not_alleys(self, name):
        assert is_alley(name) is False

    def test_main_roads_sort_before_alleys(self):
        # 巷弄數量遠多於主要道路，排序必須先分類再比長度，
        # 否則中正路這種主要道路會被巷弄擠出額度。
        names = ["中山路343巷", "中正路", "溫泉路2巷", "中山路"]
        lengths = {"中山路343巷": 500.0, "中正路": 180.0, "溫泉路2巷": 400.0, "中山路": 300.0}
        ordered = sorted(names, key=lambda n: (is_alley(n), -lengths[n]))
        assert ordered[:2] == ["中山路", "中正路"]


class TestRotatedLabelRendering:
    def test_produces_non_empty_rgba_image(self):
        font = _load_font(30)
        image = _render_rotated_label(
            "中山路",
            font,
            0.0,
            fill=(20, 20, 20, 255),
            halo=(255, 255, 255, 235),
        )
        assert image.mode == "RGBA"
        assert image.width > 10 and image.height > 10
        # 必須真的有畫上像素，否則標註等於沒做。
        assert image.getbbox() is not None

    def test_rotation_changes_bounding_box(self):
        font = _load_font(30)
        flat = _render_rotated_label(
            "忠孝一路", font, 0.0, (0, 0, 0, 255), (255, 255, 255, 255)
        )
        tilted = _render_rotated_label(
            "忠孝一路", font, 60.0, (0, 0, 0, 255), (255, 255, 255, 255)
        )
        assert tilted.height > flat.height


class TestLabelOverlayConfiguration:
    def test_default_label_overlay_is_transparent_no_house_number(self):
        # EMAP12 = 臺灣通用電子地圖透明(無門牌)，可疊在分區之上顯示路名。
        assert DEFAULT_LABEL_OVERLAY == "EMAP12"
        assert LABEL_OVERLAY_WITH_HOUSE_NUMBER == "EMAP2"

    def test_label_overlay_is_not_a_session_gated_layer(self):
        assert DEFAULT_LABEL_OVERLAY not in _SESSION_GATED_LAYERS

    def test_label_overlay_routes_to_wmts_without_referer(self):
        url, headers = _tile_url(DEFAULT_LABEL_OVERLAY, 19, 439291, 224165)
        assert url.startswith("https://wmts.nlsc.gov.tw/wmts/EMAP12/")
        assert "Referer" not in headers


class TestFetchRoadNamesFailure:
    class _RaisingSession:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("模擬 Overpass 連線失敗")

        def close(self):
            pass

    class _BadJsonSession:
        class _Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("非 JSON")

        def post(self, *args, **kwargs):
            return self._Response()

        def close(self):
            pass

    def test_network_failure_returns_empty_not_raise(self):
        # 路名是加值資訊，取不到不應讓整張圖出不來。
        roads = fetch_road_names(
            121.63,
            25.21,
            121.64,
            25.23,
            session=self._RaisingSession(),
        )
        assert roads == []

    def test_invalid_payload_returns_empty(self):
        roads = fetch_road_names(
            121.63,
            25.21,
            121.64,
            25.23,
            session=self._BadJsonSession(),
        )
        assert roads == []


class TestRenderZoomFitting:
    def test_tiny_parcel_capped_at_max_safe_zoom(self):
        zoom = _fit_zoom_for_viewport(
            121.637449,
            25.221357,
            121.637647,
            25.221534,
            1200,
            900,
        )
        assert zoom == MAX_SAFE_ZOOM

    def test_large_extent_zooms_out(self):
        zoom = _fit_zoom_for_viewport(121.0, 25.0, 122.0, 26.0, 1200, 900)
        assert MIN_ZOOM <= zoom < MAX_SAFE_ZOOM

    def test_result_always_within_bounds(self):
        for delta in (1e-6, 1e-3, 0.1, 5.0):
            zoom = _fit_zoom_for_viewport(
                121.6, 25.2, 121.6 + delta, 25.2 + delta, 800, 600
            )
            assert MIN_ZOOM <= zoom <= MAX_SAFE_ZOOM


class TestCliParcelToken:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("1027/489", "102704890000"),
            ("1027:489", "102704890000"),
            ("1027/489-1", "102704890001"),
            (" 1027/489 ", "102704890000"),
            ("12/489", "001204890000"),
        ],
    )
    def test_parses_section_and_number(self, token, expected):
        assert parse_parcel_token(token).code == expected

    def test_default_section_allows_bare_number(self):
        assert parse_parcel_token("489", "1027").code == "102704890000"
        assert parse_parcel_token("489-1", "1027").code == "102704890001"

    def test_explicit_section_overrides_default(self):
        assert parse_parcel_token("1026/218", "1027").code == "102602180000"

    def test_bare_number_without_default_is_rejected(self):
        # 「-」是子號分隔符，不能當地段分隔符，所以純數字必須有 --section。
        with pytest.raises(ValueError, match="段/地號"):
            parse_parcel_token("489")

    @pytest.mark.parametrize("token", ["", "   ", "/489", "1027/", "abc/def"])
    def test_invalid_tokens_rejected(self, token):
        with pytest.raises(ValueError):
            parse_parcel_token(token)


class TestCliArgParser:
    def test_defaults_match_documented_behaviour(self):
        args = build_arg_parser().parse_args(["1027/489"])
        assert args.county == "新北市"
        assert args.output == "parcel_map.png"
        assert args.width == 1200
        assert args.height == 900
        assert args.base_map == "EMAP16"  # 不含門牌
        assert args.max_roads == 20
        assert args.include_alleys is False  # 預設不標巷弄
        assert args.no_roads is False

    def test_multiple_parcels_and_output(self):
        args = build_arg_parser().parse_args(
            ["1027/489", "1026/218", "-o", "out.png"]
        )
        assert args.parcels == ["1027/489", "1026/218"]
        assert args.output == "out.png"

    def test_section_shorthand(self):
        args = build_arg_parser().parse_args(["-s", "1027", "489", "490"])
        assert args.section == "1027"
        assert args.parcels == ["489", "490"]

    def test_toggles(self):
        args = build_arg_parser().parse_args(
            [
                "1027/489",
                "--no-roads",
                "--include-alleys",
                "--no-zoning",
                "--no-cadastral",
                "--no-label-overlay",
                "--no-parcel-labels",
            ]
        )
        assert args.no_roads is True
        assert args.include_alleys is True
        assert args.no_zoning is True
        assert args.no_cadastral is True
        assert args.no_label_overlay is True
        assert args.no_parcel_labels is True

    def test_base_map_choices_restricted(self):
        with pytest.raises(SystemExit):
            build_arg_parser().parse_args(["1027/489", "--base-map", "NOSUCH"])

    def test_parcels_are_required(self):
        with pytest.raises(SystemExit):
            build_arg_parser().parse_args([])


class TestCliMainErrorHandling:
    def test_bad_token_returns_exit_code_2(self, capsys):
        assert main(["489"]) == 2
        assert "段/地號" in capsys.readouterr().err

    def test_unknown_county_returns_error(self, capsys):
        assert main(["1027/489", "--county", "火星市"]) == 1
        assert "無法出圖" in capsys.readouterr().err


class TestRenderValidation:
    def test_rejects_bad_canvas_size(self):
        parcel = LandParcel.parse("1027", "489")
        for width, height in ((0, 900), (1200, 0), (-1, 100)):
            with pytest.raises(ValueError):
                render_parcel_map("F", parcel, width=width, height=height)

    def test_rejects_zoom_beyond_cap(self):
        parcel = LandParcel.parse("1027", "489")
        with pytest.raises(ValueError):
            render_parcel_map("F", parcel, zoom=MAX_SAFE_ZOOM + 1)

    def test_rejects_empty_parcels(self):
        with pytest.raises(ValueError):
            render_parcel_map("F", [])


@pytest.mark.live
class TestLiveZoningSessionGate:
    """URBAN 圖磚需要先造訪圖台建立授權。

    實測行為：全新環境未暖機時會回傳 HTTP 200 但 0 位元組空白圖；
    一旦造訪過圖台，授權會延續一段時間（不僅限於同一 session，
    推測另有 IP 或伺服器端快取層），此時未暖機也可能取得圖磚。
    因此可靠的斷言只有「暖機後一定拿得到」，不能斷言「未暖機一定為空」。
    """

    ZOOM = 20
    TILE_X = 878583
    TILE_Y = 448330

    def _fetch(self, session) -> int:
        url, headers = _tile_url("URBAN", self.ZOOM, self.TILE_X, self.TILE_Y)
        response = session.get(url, headers=headers, timeout=60)
        assert response.status_code == 200
        return len(response.content)

    def test_warm_up_obtains_portal_session(self):
        with create_tile_session(warm_up=True) as session:
            assert "JSESSIONID" in session.cookies

    def test_with_warm_up_always_returns_tile(self):
        with create_tile_session(warm_up=True) as session:
            assert self._fetch(session) > 1000

    def test_blank_tile_is_served_as_http_200(self):
        # 取不到圖磚時不會是 4xx，而是 200 + 極小內容；
        # 這是「圖層靜默消失」的成因，渲染時必須以位元組數判斷。
        with create_tile_session(warm_up=False) as session:
            size = self._fetch(session)
        assert size == 0 or size > 1000

    def test_cadastral_needs_referer_not_portal_session(self):
        # 地籍圖靠 Referer，不靠圖台 session，未暖機也應取得圖磚。
        with create_tile_session(warm_up=False) as session:
            url, headers = _tile_url("DMAPS", 19, 439291, 224165)
            response = session.get(url, headers=headers, timeout=60)
            assert len(response.content) > 1000


@pytest.mark.live
class TestLiveRenderParcelMap:
    def test_renders_png_with_all_layers(self, tmp_path):
        output = tmp_path / "map.png"
        result = render_parcel_map(
            county="新北市",
            parcels=[
                LandParcel.parse(KNOWN_SECTION, "489"),
                LandParcel.parse(KNOWN_SECTION, "490"),
            ],
            output_path=output,
            width=800,
            height=600,
        )

        assert result.output_path.exists()
        assert result.output_path.stat().st_size > 10_000
        assert len(result.found) == 2
        assert result.zoom <= MAX_SAFE_ZOOM

        # 三個圖層都要真的抓到圖磚，不能靜默空白。
        for code in ("EMAP16", "URBAN", "DMAPS"):
            filled, _ = result.layer_tiles[code]
            assert filled > 0, f"{code} 沒有取得任何圖磚"

        with Image.open(result.output_path) as image:
            assert image.size == (800, 600)

    def test_missing_parcel_does_not_break_render(self, tmp_path):
        result = render_parcel_map(
            county="新北市",
            parcels=[
                LandParcel.parse(KNOWN_SECTION, "489"),
                LandParcel.parse(KNOWN_SECTION, MISSING_PARCEL),
            ],
            output_path=tmp_path / "partial.png",
            width=600,
            height=400,
        )
        assert len(result.found) == 1
        assert len(result.missing) == 1
        assert result.output_path.exists()

    def test_all_missing_parcels_raise(self, tmp_path):
        with pytest.raises(ValueError, match="查無範圍"):
            render_parcel_map(
                county="新北市",
                parcels=LandParcel.parse(KNOWN_SECTION, MISSING_PARCEL),
                output_path=tmp_path / "none.png",
            )


@pytest.mark.live
class TestLiveLabelOverlay:
    """透明文字圖層必須真的透明，否則會蓋掉底下的分區與地籍。"""

    ZOOM = 19
    TILE_X = 439291
    TILE_Y = 224165

    def _tile(self, layer: str) -> Image.Image:
        url, headers = _tile_url(layer, self.ZOOM, self.TILE_X, self.TILE_Y)
        with create_tile_session() as session:
            response = session.get(url, headers=headers, timeout=60)
        assert response.status_code == 200
        assert len(response.content) > 1000
        return Image.open(io.BytesIO(response.content)).convert("RGBA")

    def test_label_overlay_is_mostly_transparent(self):
        image = self._tile(DEFAULT_LABEL_OVERLAY)
        alpha = image.getchannel("A")
        transparent = sum(1 for value in alpha.get_flattened_data() if value < 255)
        ratio = transparent / (image.width * image.height)
        assert ratio > 0.8, f"透明比例僅 {ratio:.0%}，會遮住底下圖層"

    def test_label_overlay_has_visible_content(self):
        image = self._tile(DEFAULT_LABEL_OVERLAY)
        alpha = image.getchannel("A")
        opaque = sum(1 for value in alpha.get_flattened_data() if value > 0)
        assert opaque > 0, "文字圖層完全空白，路名不會出現"

    def test_opaque_base_map_is_not_transparent(self):
        # 對照組：底圖為不透明 JPEG，不能拿來當文字疊圖。
        image = self._tile("EMAP16")
        alpha = image.getchannel("A")
        transparent = sum(1 for value in alpha.get_flattened_data() if value < 255)
        assert transparent == 0


@pytest.mark.live
class TestLiveRoadAnnotation:
    def test_fetch_road_names_returns_named_ways(self):
        roads = fetch_road_names(121.630, 25.215, 121.645, 25.230)
        if not roads:
            pytest.skip("Overpass 未回應（可能被限流）")
        for name, points in roads:
            assert name
            assert len(points) >= 2

        names = {name for name, _ in roads}
        assert any("路" in name or "街" in name or "巷" in name for name in names)

    def test_render_draws_road_labels(self, tmp_path):
        result = render_parcel_map(
            county="新北市",
            parcels=LandParcel.parse(KNOWN_SECTION, "489"),
            output_path=tmp_path / "roads.png",
            width=900,
            height=700,
            annotate_roads=True,
            max_road_labels=6,
        )
        assert result.road_labels
        assert len(result.road_labels) <= 6
        assert len(set(result.road_labels)) == len(result.road_labels)  # 不重複

        # 透明文字圖層也要成功取得圖磚。
        filled, _ = result.layer_tiles[DEFAULT_LABEL_OVERLAY]
        assert filled > 0

    def test_alleys_are_excluded_by_default(self, tmp_path):
        # 巷弄數量多且對判讀區位幫助有限，預設不標。
        result = render_parcel_map(
            county="新北市",
            parcels=LandParcel.parse(KNOWN_SECTION, "489"),
            output_path=tmp_path / "priority.png",
            width=1200,
            height=900,
            max_road_labels=8,
        )
        if not result.road_labels:
            pytest.skip("Overpass 未回傳道路（可能被限流），此次無法驗證")

        alleys = [name for name in result.road_labels if is_alley(name)]
        assert alleys == [], f"預設不應標巷弄，卻出現：{alleys}"

    def test_alleys_can_be_opted_in(self, tmp_path):
        result = render_parcel_map(
            county="新北市",
            parcels=LandParcel.parse(KNOWN_SECTION, "489"),
            output_path=tmp_path / "alleys.png",
            width=1200,
            height=900,
            max_road_labels=30,
            include_alleys=True,
        )
        if not result.road_labels:
            pytest.skip("Overpass 未回傳道路（可能被限流），此次無法驗證")

        flags = [is_alley(name) for name in result.road_labels]
        first_alley = next((i for i, alley in enumerate(flags) if alley), None)
        if first_alley is None:
            pytest.skip("此視窗內沒有巷弄可供驗證排序")
        # 開啟後巷弄一律排在主要道路之後。
        assert all(flags[first_alley:]), (
            f"巷弄之後又出現主要道路，排序失效：{result.road_labels}"
        )

    def test_key_roads_are_not_dropped_by_collision(self, tmp_path):
        # 中山路與中正路都在範圍內；早期版本因碰撞即放棄而漏掉中正路。
        result = render_parcel_map(
            county="新北市",
            parcels=[
                LandParcel.parse(KNOWN_SECTION, "489"),
                LandParcel.parse("1026", "218"),
            ],
            output_path=tmp_path / "keyroads.png",
            width=1200,
            height=900,
            max_road_labels=20,
        )
        if not result.road_labels:
            pytest.skip("Overpass 未回傳道路（可能被限流），此次無法驗證")
        for expected in ("中山路", "中正路"):
            assert expected in result.road_labels, (
                f"缺少 {expected}；實際標註：{result.road_labels}"
            )

    def test_label_overlay_is_composited_after_zoning(self):
        result = render_parcel_map(
            county="新北市",
            parcels=LandParcel.parse(KNOWN_SECTION, "489"),
            output_path=Path(tempfile.gettempdir()) / "order.png",
            width=600,
            height=400,
            annotate_roads=False,
        )
        codes = list(result.layer_tiles)
        # 疊圖順序即為 dict 插入順序；文字圖層必須在使用分區之後。
        assert codes.index(DEFAULT_LABEL_OVERLAY) > codes.index("URBAN")

    def test_roads_can_be_disabled(self, tmp_path):
        result = render_parcel_map(
            county="新北市",
            parcels=LandParcel.parse(KNOWN_SECTION, "489"),
            output_path=tmp_path / "noroads.png",
            width=600,
            height=400,
            annotate_roads=False,
        )
        assert result.road_labels == ()

    def test_label_overlay_can_be_disabled(self, tmp_path):
        result = render_parcel_map(
            county="新北市",
            parcels=LandParcel.parse(KNOWN_SECTION, "489"),
            output_path=tmp_path / "nolabel.png",
            width=600,
            height=400,
            label_overlay=None,
            annotate_roads=False,
        )
        assert DEFAULT_LABEL_OVERLAY not in result.layer_tiles


@pytest.mark.live
class TestLiveCadastralTiles:
    """鎖住地籍圖圖磚的兩個必要條件：landmaps 主機 + Referer。"""

    LONGITUDE = 121.637549
    LATITUDE = 25.221446

    @staticmethod
    def _tile_xy(longitude: float, latitude: float, zoom: int) -> tuple[int, int]:
        import math

        n = 2.0**zoom
        x = int((longitude + 180.0) / 360.0 * n)
        y = int(
            (1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * n
        )
        return x, y

    @staticmethod
    def _session():
        from getFacility import NlscSSLAdapter

        session = requests.Session()
        session.mount("https://landmaps.nlsc.gov.tw/", NlscSSLAdapter())
        session.mount("https://wmts.nlsc.gov.tw/", NlscSSLAdapter())
        return session

    def _fetch(self, url: str, *, referer: bool) -> bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) nlsc-test/1.0",
            "Accept": "image/png,image/*,*/*",
        }
        if referer:
            headers["Referer"] = CADASTRAL_TILE_REFERER

        with self._session() as session:
            response = session.get(url, headers=headers, timeout=60)
            assert response.status_code == 200
            return response.content

    def test_cadastral_tile_needs_referer(self):
        x, y = self._tile_xy(self.LONGITUDE, self.LATITUDE, 19)
        url = CADASTRAL_TILE_URL_TEMPLATE.format(z=19, x=x, y=y)

        with_referer = self._fetch(url, referer=True)
        without_referer = self._fetch(url, referer=False)

        assert with_referer.startswith(b"\x89PNG")
        assert len(with_referer) > 1000
        # 未帶 Referer 會回傳 0 位元組空白圖，而不是 4xx。
        assert len(without_referer) == 0

    def test_wrong_host_returns_blank_even_with_referer(self):
        x, y = self._tile_xy(self.LONGITUDE, self.LATITUDE, 19)
        wrong = (
            "https://wmts.nlsc.gov.tw/wmts/DMAPS/default/"
            f"GoogleMapsCompatible/19/{y}/{x}"
        )
        assert len(self._fetch(wrong, referer=True)) == 0

    @pytest.mark.parametrize("zoom", [CADASTRAL_TILE_MIN_ZOOM, 18, CADASTRAL_TILE_MAX_ZOOM])
    def test_cadastral_tiles_available_across_documented_range(self, zoom):
        x, y = self._tile_xy(self.LONGITUDE, self.LATITUDE, zoom)
        content = self._fetch(
            CADASTRAL_TILE_URL_TEMPLATE.format(z=zoom, x=x, y=y),
            referer=True,
        )
        assert content.startswith(b"\x89PNG")
        assert len(content) > 1000

    def test_zoom_beyond_cap_is_blank(self):
        # z21 起無圖磚，這是 MAX_SAFE_ZOOM 的依據。
        zoom = MAX_SAFE_ZOOM + 1
        x, y = self._tile_xy(self.LONGITUDE, self.LATITUDE, zoom)
        content = self._fetch(
            CADASTRAL_TILE_URL_TEMPLATE.format(z=zoom, x=x, y=y),
            referer=True,
        )
        assert len(content) == 0

    def test_base_map_also_blank_beyond_cap(self):
        zoom = MAX_SAFE_ZOOM + 1
        x, y = self._tile_xy(self.LONGITUDE, self.LATITUDE, zoom)
        url = (
            "https://wmts.nlsc.gov.tw/wmts/EMAP/default/"
            f"GoogleMapsCompatible/{zoom}/{y}/{x}"
        )
        assert len(self._fetch(url, referer=True)) == 0


@pytest.mark.live
class TestLiveFacilityDistance:
    LONGITUDE = 121.637880
    LATITUDE = 25.222398

    def test_nearest_funeral_facilities_within_2000m(self):
        nearest = find_nearest_funeral_facilities(
            self.LONGITUDE,
            self.LATITUDE,
            2000,
        )
        assert set(nearest) == set(FuneralFacilityCategory)

        cemetery = nearest[FuneralFacilityCategory.CEMETERY]
        assert cemetery is not None
        assert "公墓" in cemetery.name
        assert cemetery.mark_type == FuneralFacilityCategory.CEMETERY.mark_type
        assert 0 < cemetery.distance_meters <= 2000

    def test_radius_over_2000_is_rejected(self):
        with pytest.raises(ValueError):
            find_nearest_funeral_facilities(self.LONGITUDE, self.LATITUDE, 2001)

    def test_boundary_distance_is_closer_than_representative_point(self):
        results = measure_nearest_funeral_distances(
            self.LONGITUDE,
            self.LATITUDE,
            radius_meters=2000,
        )
        cemetery = results[FuneralFacilityCategory.CEMETERY]
        assert cemetery is not None
        # 取得面資料後，邊界距離應明顯小於 API 的代表點距離。
        if cemetery.source is not GeometrySource.REPRESENTATIVE_POINT:
            assert cemetery.distance_meters < cemetery.facility.distance_meters
