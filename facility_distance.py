"""設施邊界最短距離計算模組。

分層設計，每層可獨立替換：

1. 發現層：由 getFacility.py 的 COM_009~COM_012 查詢取得 Facility（點 + 名稱 + marktype）。
2. 幾何層：GeometryProvider 依 Facility 取得面／點幾何；可串接多來源並自動降級。
3. 計算層：投影到 TWD97 TM2（EPSG:3826）後計算查詢點到設施幾何的最短距離。

各層只依賴 Facility 的座標與 marktype，不綁死任何特定設施類別，
因此可直接套用於公墓、加油站、變電所等任何 COM 類別。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol, runtime_checkable

import requests
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from getFacility import (
    Facility,
    FuneralFacilityCategory,
    find_nearest_funeral_facilities,
)

# TWD97 TM2（EPSG:3826）適用臺灣本島；WGS84 經緯度先轉此投影再算公尺距離。
_WGS84_TO_TWD97 = Transformer.from_crs(
    "EPSG:4326",
    "EPSG:3826",
    always_xy=True,
).transform

# 公開 Overpass 端點；可替換為自架鏡像。
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

# Overpass 公用端點會拒絕沒有有意義 User-Agent 的請求（回 406／429）。
OVERPASS_HEADERS = {
    "User-Agent": "nlsc-facility-distance/1.0 (land valuation research)",
    "Accept": "application/json",
}

# OSM 內代表墓地／殯葬設施的面狀標籤組合。
_OSM_FUNERAL_SELECTORS = (
    '["landuse"="cemetery"]',
    '["amenity"="grave_yard"]',
    '["amenity"="crematorium"]',
    '["amenity"="funeral_hall"]',
)


class GeometrySource(Enum):
    """幾何來源可信度分級，供審查層判斷是否為法定值。"""

    OFFICIAL_CADASTRE = ("official", "官方地籍面", True)
    OSM_POLYGON = ("osm", "OSM 面資料", False)
    REPRESENTATIVE_POINT = ("point", "僅設施代表點", False)

    def __init__(self, code: str, label: str, authoritative: bool) -> None:
        self.code = code
        self.label = label
        self.authoritative = authoritative


@dataclass(frozen=True)
class FacilityGeometry:
    """設施的幾何與其來源等級。geometry 為 WGS84 經緯度。"""

    facility_id: str
    name: str
    mark_type: str
    geometry: BaseGeometry
    source: GeometrySource


@dataclass(frozen=True)
class DistanceResult:
    """查詢點到設施幾何的最短距離結果。"""

    facility: Facility
    distance_meters: float
    inside: bool
    source: GeometrySource

    @property
    def is_authoritative(self) -> bool:
        return self.source.authoritative

    def to_dict(self) -> dict[str, Any]:
        return {
            "facility": self.facility.to_dict(),
            "distance_meters": self.distance_meters,
            "inside": self.inside,
            "source": self.source.code,
            "source_label": self.source.label,
            "is_authoritative": self.is_authoritative,
        }


@runtime_checkable
class GeometryProvider(Protocol):
    """輸入一筆已發現的設施，回傳其面／點幾何；查無回傳 None。"""

    def fetch(self, facility: Facility) -> FacilityGeometry | None: ...


class OverpassGeometryProvider:
    """由 OpenStreetMap Overpass API 取得設施面資料。

    以設施代表點為中心，在 search_radius_meters 內尋找符合殯葬標籤的
    way／relation 面，回傳距代表點最近的一個面。OSM 為社群資料，
    僅供參考，來源標記為 OSM_POLYGON。
    """

    def __init__(
        self,
        *,
        endpoint: str = OVERPASS_ENDPOINT,
        selectors: Iterable[str] = _OSM_FUNERAL_SELECTORS,
        search_radius_meters: int = 300,
        timeout_seconds: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._selectors = tuple(selectors)
        self._search_radius_meters = search_radius_meters
        self._timeout_seconds = timeout_seconds
        self._session = session

    def _build_query(self, longitude: float, latitude: float) -> str:
        # around 以「代表點」為圓心搜尋，涵蓋面資料的一部分即可命中整個面。
        radius = self._search_radius_meters
        clauses = "".join(
            f"way(around:{radius},{latitude},{longitude}){selector};"
            f"relation(around:{radius},{latitude},{longitude}){selector};"
            for selector in self._selectors
        )
        return f"[out:json][timeout:{self._timeout_seconds}];({clauses});out geom;"

    def fetch(self, facility: Facility) -> FacilityGeometry | None:
        query = self._build_query(facility.longitude, facility.latitude)

        owns_session = self._session is None
        client = self._session or requests.Session()
        try:
            response = client.post(
                self._endpoint,
                data={"data": query},
                headers=OVERPASS_HEADERS,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        finally:
            if owns_session:
                client.close()

        elements = payload.get("elements", [])
        candidates = [
            geometry
            for geometry in (self._element_to_geometry(element) for element in elements)
            if geometry is not None and not geometry.is_empty
        ]
        if not candidates:
            return None

        # 以代表點為基準取最近的面（在 TWD97 下比較公尺距離）。
        point = shapely_transform(
            _WGS84_TO_TWD97,
            Point(facility.longitude, facility.latitude),
        )
        nearest = min(
            candidates,
            key=lambda geom: shapely_transform(_WGS84_TO_TWD97, geom).distance(point),
        )
        return FacilityGeometry(
            facility_id=facility.id,
            name=facility.name,
            mark_type=facility.mark_type,
            geometry=nearest,
            source=GeometrySource.OSM_POLYGON,
        )

    @staticmethod
    def _element_to_geometry(element: dict[str, Any]) -> BaseGeometry | None:
        """將 Overpass way／relation 的 geometry 轉為 shapely 幾何。"""

        element_type = element.get("type")
        if element_type == "way":
            coords = [
                (node["lon"], node["lat"])
                for node in element.get("geometry", [])
                if "lon" in node and "lat" in node
            ]
            if len(coords) < 3:
                return None
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            return shape({"type": "Polygon", "coordinates": [coords]})

        if element_type == "relation":
            polygons = []
            for member in element.get("members", []):
                if member.get("type") != "way" or member.get("role") not in {
                    "outer",
                    "",
                }:
                    continue
                coords = [
                    (node["lon"], node["lat"])
                    for node in member.get("geometry", [])
                    if "lon" in node and "lat" in node
                ]
                if len(coords) < 3:
                    continue
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                polygons.append({"type": "Polygon", "coordinates": [coords]})
            if not polygons:
                return None
            if len(polygons) == 1:
                return shape(polygons[0])
            return shape(
                {
                    "type": "MultiPolygon",
                    "coordinates": [polygon["coordinates"] for polygon in polygons],
                }
            )

        return None


class GeometryResolver:
    """依序嘗試多個 provider，第一個成功者勝出；全失敗則退化為代表點。"""

    def __init__(self, providers: Iterable[GeometryProvider]) -> None:
        self._providers = tuple(providers)

    def resolve(self, facility: Facility) -> FacilityGeometry:
        for provider in self._providers:
            try:
                geometry = provider.fetch(facility)
            except requests.RequestException:
                # 單一 provider 網路失敗不應中斷整條 chain，改用下一個來源。
                geometry = None
            if geometry is not None:
                return geometry

        return FacilityGeometry(
            facility_id=facility.id,
            name=facility.name,
            mark_type=facility.mark_type,
            geometry=Point(facility.longitude, facility.latitude),
            source=GeometrySource.REPRESENTATIVE_POINT,
        )


def compute_distance(
    longitude: float,
    latitude: float,
    facility: Facility,
    geometry: FacilityGeometry,
) -> DistanceResult:
    """在 TWD97 TM2 下計算查詢點到設施幾何的最短距離（公尺）。"""

    query_point = shapely_transform(_WGS84_TO_TWD97, Point(longitude, latitude))
    projected = shapely_transform(_WGS84_TO_TWD97, geometry.geometry)

    inside = bool(projected.covers(query_point))
    distance = 0.0 if inside else query_point.distance(projected)
    return DistanceResult(
        facility=facility,
        distance_meters=round(distance, 1),
        inside=inside,
        source=geometry.source,
    )


def _default_resolver() -> GeometryResolver:
    # 目前僅有 OSM 面來源；取得地籍授權後可在最前面插入官方 provider。
    return GeometryResolver([OverpassGeometryProvider()])


def measure_nearest_funeral_distances(
    longitude: float,
    latitude: float,
    *,
    radius_meters: int = 2000,
    resolver: GeometryResolver | None = None,
) -> dict[FuneralFacilityCategory, DistanceResult | None]:
    """對四類最近殯葬設施各計算邊界最短距離；某類查無回傳 None。"""

    active_resolver = resolver or _default_resolver()
    nearest = find_nearest_funeral_facilities(
        longitude,
        latitude,
        radius_meters,
    )

    results: dict[FuneralFacilityCategory, DistanceResult | None] = {}
    for category, facility in nearest.items():
        if facility is None:
            results[category] = None
            continue
        geometry = active_resolver.resolve(facility)
        results[category] = compute_distance(longitude, latitude, facility, geometry)
    return results


if __name__ == "__main__":
    # 使用者提供格式為「緯度, 經度」：25.222398, 121.637880。
    latitude = 25.222398
    longitude = 121.637880
    radius_meters = 2000

    print(
        f"查詢中心：緯度 {latitude}, 經度 {longitude}；"
        f"最大半徑：{radius_meters} 公尺"
    )

    distances = measure_nearest_funeral_distances(
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
    )

    print("\n=== 各類最近殯葬設施（邊界最短距離）===")
    for category, result in distances.items():
        if result is None:
            print(f"{category.label}：{radius_meters}m 內查無")
            continue

        position = "本區段內" if result.inside else "本區段外"
        print(
            f"{category.label}：{result.facility.name}；"
            f"{position}；邊界最短距離 {result.distance_meters}m；"
            f"代表點距離 {result.facility.distance_meters}m；"
            f"來源 {result.source.label}"
            f"（{'法定' if result.is_authoritative else '參考'}）"
        )

    print(
        "\n注意：OSM 面為社群參考資料，非官方法定邊界；"
        "取得官方地籍面後應改用 OFFICIAL_CADASTRE 來源重新計算。"
    )
