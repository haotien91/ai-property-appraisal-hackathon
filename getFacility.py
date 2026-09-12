from __future__ import annotations

import ssl
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter

API_BASE_URL = "https://api.nlsc.gov.tw/other/MarkBufferAnlys"
S09SOA_DOCUMENTATION_URL = "https://maps.nlsc.gov.tw/S09SOA/"


class FacilityCategory(Enum):
    """NLSC S09SOA COM_009～COM_012 設施類別。"""

    EDUCATION = ("COM_009", "edu", "文教設施")
    MEDICAL = ("COM_010", "med", "醫療設施")
    BUSINESS = ("COM_011", "bus", "工商設施")
    DISAMENITY = ("COM_012", "dis", "鄰避設施")

    def __init__(self, service_code: str, api_path: str, label: str) -> None:
        self.service_code = service_code
        self.api_path = api_path
        self.label = label

    @classmethod
    def parse(cls, value: FacilityCategory | str) -> FacilityCategory:
        if isinstance(value, cls):
            return value

        normalized = value.strip().lower().replace("_", "")
        for category in cls:
            aliases = {
                category.name.lower(),
                category.api_path,
                category.service_code.lower().replace("_", ""),
            }
            if normalized in aliases:
                return category

        supported = ", ".join(
            f"{category.api_path} ({category.service_code})" for category in cls
        )
        raise ValueError(f"不支援的設施類別 {value!r}；可用值：{supported}")


class NlscSSLAdapter(HTTPAdapter):
    """
    NLSC 憑證鏈缺少 Subject Key Identifier。

    僅停用 OpenSSL X509_STRICT 規則，仍保留憑證鏈、主機名稱及
    憑證有效期限驗證。
    """

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        context = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT

        pool_kwargs["ssl_context"] = context
        super().init_poolmanager(
            connections,
            maxsize,
            block=block,
            **pool_kwargs,
        )


def create_nlsc_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://api.nlsc.gov.tw/", NlscSSLAdapter())
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "nlsc-facility-client/1.0",
        }
    )
    return session


@dataclass(frozen=True)
class Facility:
    category: FacilityCategory
    geometry_type: str
    id: str
    name: str
    short_name: str
    address: str
    telephone: str
    longitude: float
    latitude: float
    distance_meters: int
    mark_type: str

    @property
    def service_code(self) -> str:
        return self.category.service_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_code": self.service_code,
            "category": self.category.api_path,
            "category_label": self.category.label,
            "type": self.geometry_type,
            "id": self.id,
            "name": self.name,
            "sname": self.short_name,
            "addr": self.address,
            "tel": self.telephone,
            "lon": self.longitude,
            "lat": self.latitude,
            "distance": self.distance_meters,
            "marktype": self.mark_type,
        }


# 保留舊程式可能使用的型別名稱；四類 API 共用相同資料結構。
MedicalFacility = Facility
DisFacility = Facility


def _validate_search_parameters(
    longitude: float,
    latitude: float,
    radius_meters: int,
) -> None:
    if not -180 <= longitude <= 180:
        raise ValueError("經度必須介於 -180 到 180")
    if not -90 <= latitude <= 90:
        raise ValueError("緯度必須介於 -90 到 90")
    if isinstance(radius_meters, bool) or not isinstance(radius_meters, int):
        raise ValueError("查詢半徑必須是整數公尺")
    if radius_meters <= 0:
        raise ValueError("查詢半徑必須大於 0 公尺")


def _request_facility_data(
    category: FacilityCategory,
    longitude: float,
    latitude: float,
    radius_meters: int,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    _validate_search_parameters(longitude, latitude, radius_meters)
    url = (
        f"{API_BASE_URL}/{category.api_path}/"
        f"{longitude:.6f}/{latitude:.6f}/{radius_meters}"
    )

    owns_session = session is None
    client = session or create_nlsc_session()
    try:
        response = client.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    finally:
        if owns_session:
            client.close()

    if not isinstance(data, list):
        raise ValueError(f"API 回傳格式不是 JSON array：{data!r}")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError(f"API 回傳陣列包含非物件資料：{data!r}")
    return data


def _parse_facility(
    category: FacilityCategory,
    item: dict[str, Any],
) -> Facility:
    try:
        return Facility(
            category=category,
            geometry_type=str(item.get("type", "Point")),
            id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            short_name=str(item.get("sname", "")),
            address=str(item.get("addr", "")),
            telephone=str(item.get("tel", "")),
            longitude=float(item["lon"]),
            latitude=float(item["lat"]),
            distance_meters=int(item["distance"]),
            mark_type=str(item.get("marktype", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"設施資料欄位無法解析：{item!r}") from exc


def find_facilities(
    category: FacilityCategory | str,
    longitude: float,
    latitude: float,
    radius_meters: int = 1000,
    *,
    session: requests.Session | None = None,
) -> list[Facility]:
    """通用設施查詢；category 可傳 enum、edu、med、bus、dis 或 COM 代碼。"""

    parsed_category = FacilityCategory.parse(category)
    facilities = [
        _parse_facility(parsed_category, item)
        for item in _request_facility_data(
            parsed_category,
            longitude,
            latitude,
            radius_meters,
            session,
        )
    ]
    return sorted(facilities, key=lambda facility: facility.distance_meters)


def find_education_facilities(
    longitude: float,
    latitude: float,
    radius_meters: int = 1000,
    *,
    session: requests.Session | None = None,
) -> list[Facility]:
    """COM_009：查詢文教設施。"""

    return find_facilities(
        FacilityCategory.EDUCATION,
        longitude,
        latitude,
        radius_meters,
        session=session,
    )


def find_medical_facilities(
    longitude: float,
    latitude: float,
    radius_meters: int = 1000,
    *,
    session: requests.Session | None = None,
) -> list[Facility]:
    """COM_010：查詢醫療設施。"""

    return find_facilities(
        FacilityCategory.MEDICAL,
        longitude,
        latitude,
        radius_meters,
        session=session,
    )


def find_business_facilities(
    longitude: float,
    latitude: float,
    radius_meters: int = 1000,
    *,
    session: requests.Session | None = None,
) -> list[Facility]:
    """COM_011：查詢工商設施。"""

    return find_facilities(
        FacilityCategory.BUSINESS,
        longitude,
        latitude,
        radius_meters,
        session=session,
    )


def find_disamenity_facilities(
    longitude: float,
    latitude: float,
    radius_meters: int = 1000,
    *,
    session: requests.Session | None = None,
) -> list[Facility]:
    """COM_012：查詢鄰避設施。"""

    return find_facilities(
        FacilityCategory.DISAMENITY,
        longitude,
        latitude,
        radius_meters,
        session=session,
    )


class FuneralFacilityCategory(Enum):
    """COM_012 殯葬設施子類別；值為 NLSC marktype。"""

    CEMETERY = ("9350200", "墓地")
    FUNERAL_HOME = ("9930201", "殯儀館")
    CREMATORIUM = ("9930202", "火葬場")
    COLUMBARIUM = ("9930203", "納骨塔")

    def __init__(self, mark_type: str, label: str) -> None:
        self.mark_type = mark_type
        self.label = label


MAX_FUNERAL_SEARCH_RADIUS_METERS = 2000


def find_nearest_funeral_facilities(
    longitude: float,
    latitude: float,
    radius_meters: int = MAX_FUNERAL_SEARCH_RADIUS_METERS,
    *,
    session: requests.Session | None = None,
) -> dict[FuneralFacilityCategory, Facility | None]:
    """回傳搜尋半徑內四種殯葬設施各自最近的一筆，查無時為 None。

    搜尋半徑不可超過 2000 公尺；分類依據為 COM_012 的 marktype，
    不使用設施名稱關鍵字判斷。
    """

    _validate_search_parameters(longitude, latitude, radius_meters)
    if radius_meters > MAX_FUNERAL_SEARCH_RADIUS_METERS:
        raise ValueError(
            "殯葬設施查詢半徑不可超過 "
            f"{MAX_FUNERAL_SEARCH_RADIUS_METERS} 公尺"
        )

    nearest: dict[FuneralFacilityCategory, Facility | None] = {
        category: None for category in FuneralFacilityCategory
    }
    categories_by_mark_type = {
        category.mark_type: category for category in FuneralFacilityCategory
    }

    for facility in find_disamenity_facilities(
        longitude,
        latitude,
        radius_meters,
        session=session,
    ):
        category = categories_by_mark_type.get(facility.mark_type)
        if category is not None and nearest[category] is None:
            nearest[category] = facility
            if all(result is not None for result in nearest.values()):
                break

    return nearest


def find_all_facilities(
    longitude: float,
    latitude: float,
    radius_meters: int = 1000,
    categories: Iterable[FacilityCategory | str] = tuple(FacilityCategory),
) -> dict[FacilityCategory, list[Facility]]:
    """使用同一連線依序查詢多個設施類別。"""

    parsed_categories = [FacilityCategory.parse(category) for category in categories]
    with create_nlsc_session() as session:
        return {
            category: find_facilities(
                category,
                longitude,
                latitude,
                radius_meters,
                session=session,
            )
            for category in parsed_categories
        }


def print_facilities(
    facilities: Iterable[Facility],
    *,
    max_items: int | None = None,
) -> None:
    facility_list = list(facilities)
    if not facility_list:
        print("指定範圍內沒有設施。")
        return

    category = facility_list[0].category
    shown = facility_list if max_items is None else facility_list[:max_items]
    print(
        f"\n=== {category.label}（{category.service_code}，"
        f"共 {len(facility_list)} 筆）==="
    )
    for facility in shown:
        print(f"名稱：{facility.name}")
        print(f"地址：{facility.address}")
        print(f"電話：{facility.telephone}")
        print(f"距離：{facility.distance_meters} 公尺")
        print(f"坐標：{facility.longitude}, {facility.latitude}")
        print(f"設施代碼：{facility.mark_type}")
        print("-" * 40)

    omitted = len(facility_list) - len(shown)
    if omitted:
        print(f"其餘 {omitted} 筆未顯示。")


if __name__ == "__main__":
    longitude = 121.637901
    latitude = 25.222403
    radius_meters = 2000

    try:
        results = find_all_facilities(
            longitude=longitude,
            latitude=latitude,
            radius_meters=radius_meters,
        )
        for facilities in results.values():
            print_facilities(facilities, max_items=5)
    except requests.HTTPError as exc:
        print(f"API HTTP 錯誤：{exc}")
        if exc.response is not None:
            print(exc.response.text)
    except requests.RequestException as exc:
        print(f"網路連線錯誤：{exc}")
    except (ValueError, KeyError, TypeError) as exc:
        print(f"資料解析錯誤：{exc}")
