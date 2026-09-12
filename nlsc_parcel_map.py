"""地段地號存在性驗證與安全視野的圖台網址產生。

解決三個實際問題：

1. 多筆地號沒標出
   NLSC 只會著色「實際存在」的地號，不存在者靜默略過。
   本模組先呼叫著色索引 API 逐筆驗證，明確回報哪一筆查無。

2. 底圖空白
   goland 會自動縮放至宗地範圍；宗地常只有數十公尺寬，
   會縮放到 z21 以上，而該層級所有底圖皆無圖磚（回傳 0 位元組）→ 全白。
   本模組由宗地範圍算出「不超過 MAX_SAFE_ZOOM」的層級，
   另產生一個 go 網址供檢視，確保底圖有圖磚。

3. 宗地文字
   宗地地號文字只在地籍圖（DMAPS）圖磚上，已實圖確認 z12～z20 皆含地號註記。
   底圖「臺灣通用電子地圖」上的「225號」「209號」是門牌，不是地號，
   兩者疊在一起極易誤判；需要判讀地號時建議改用不含門牌的底圖
   （EMAP16 不含等高線及門牌，或 EMAP01 灰階）。

   注意：直接抓取地籍圖圖磚需指定
       https://landmaps.nlsc.gov.tw/S_Maps/wmts/DMAPS/default/GoogleMapsCompatible/{z}/{y}/{x}
   且必須帶 Referer: https://maps.nlsc.gov.tw/，否則回傳 0 位元組空白圖；
   走 wmts.nlsc.gov.tw 取 DMAPS 一律為空。從圖台網址帶入 DMAPS 不受影響，
   因為 Referer 由圖台自身提供。

著色索引 API（由圖台 qt_cadastralManager.js 取得）：
    GET https://landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex
        type=json&flag=3&county={縣市}&parno={12碼,可逗號多筆}
        &alpah=0.5f&imgflag=1[&color=#F70101]
回傳為多組 JSON array 串接，每組 = [宗地屬性, 著色影像與範圍]。
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

from getFacility import NlscSSLAdapter
from nlsc_map_url import (
    BaseMap,
    CountyCode,
    ExtraLayer,
    LandParcel,
    build_coordinate_url,
    build_goland_url,
    build_open_url,
)

CADASTRAL_INDEX_ENDPOINT = "https://landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex"

# z21 起所有 NLSC 圖磚（含 EMAP、PHOTO2、URBAN、DMAPS）皆回傳空白，
# 故安全上限設為 20。
MAX_SAFE_ZOOM = 20
MIN_ZOOM = 1

# 地籍圖圖磚可用層級：z12～z20（實測）。
CADASTRAL_TILE_MIN_ZOOM = 12
CADASTRAL_TILE_MAX_ZOOM = 20

# 地籍圖圖磚端點；直接取用時必須帶 Referer。
CADASTRAL_TILE_URL_TEMPLATE = (
    "https://landmaps.nlsc.gov.tw/S_Maps/wmts/DMAPS/default/"
    "GoogleMapsCompatible/{z}/{y}/{x}"
)
CADASTRAL_TILE_REFERER = "https://maps.nlsc.gov.tw/"

# 預設圖層：URBAN 提供使用分區、DMAPS 提供宗地界線與地號文字。
# 不放 NLSCVET：它是向量文字圖層（非 WMTS 圖磚），且提供地名註記而非地號。
DEFAULT_PARCEL_LAYERS: tuple[ExtraLayer | str, ...] = (
    ExtraLayer.URBAN,
    ExtraLayer.DMAPS,
)

# 需要清楚判讀地號時的建議底圖：不含門牌，避免與地號混淆。
PARCEL_READING_BASE_MAP = BaseMap.EMAP_NO_HOUSE_NUMBER

_HEADERS = {
    "User-Agent": "nlsc-parcel-map/1.0",
    "Accept": "*/*",
    "Referer": "https://maps.nlsc.gov.tw/",
}


def create_landmaps_session() -> requests.Session:
    """landmaps 主機憑證同樣缺少 Subject Key Identifier，需自訂 adapter。"""

    session = requests.Session()
    session.mount("https://landmaps.nlsc.gov.tw/", NlscSSLAdapter())
    session.headers.update(_HEADERS)
    return session


@dataclass(frozen=True)
class ParcelInfo:
    """單筆地段地號的驗證結果。"""

    parcel: LandParcel
    exists: bool
    section_name: str = ""
    land_office: str = ""
    min_longitude: float | None = None
    min_latitude: float | None = None
    max_longitude: float | None = None
    max_latitude: float | None = None
    tint_image_png: bytes | None = None

    @property
    def has_extent(self) -> bool:
        return None not in (
            self.min_longitude,
            self.min_latitude,
            self.max_longitude,
            self.max_latitude,
        )

    @property
    def center(self) -> tuple[float, float] | None:
        """回傳 (經度, 緯度)。"""

        if not self.has_extent:
            return None
        return (
            (self.min_longitude + self.max_longitude) / 2,
            (self.min_latitude + self.max_latitude) / 2,
        )

    @property
    def display(self) -> str:
        # sectStr 已含「段」字，不可再補。
        name = self.section_name or f"{self.parcel.section_code} 段"
        if self.parcel.child_number:
            return f"{name} {self.parcel.parent_number}-{self.parcel.child_number} 地號"
        return f"{name} {self.parcel.parent_number} 地號"

    def save_tint_image(self, path: str | Path) -> Path | None:
        """存出 NLSC 產生的宗地著色圖（PNG）；無影像回傳 None。"""

        if not self.tint_image_png:
            return None
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.tint_image_png)
        return target


def _decode_b64_text(value: str) -> str:
    """sectStr／officeStr 為 UTF-8 base64。解析失敗則原樣回傳。"""

    text = (value or "").strip()
    if not text:
        return ""
    try:
        return base64.b64decode(text).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return text


def _iter_json_documents(body: str) -> list[Any]:
    """回應可能是多個 JSON array 直接串接，需逐段解析。"""

    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    length = len(body)
    while index < length:
        while index < length and body[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        try:
            value, end = decoder.raw_decode(body, index)
        except json.JSONDecodeError:
            break
        documents.append(value)
        index = end
    return documents


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _request_parcel_body(
    county: CountyCode,
    parcel: LandParcel,
    session: requests.Session,
    *,
    timeout_seconds: int,
    attempts: int,
    retry_delay_seconds: float,
) -> str:
    """取回著色索引原始內容。

    此 API 對存在的地號仍會間歇性回傳空內容（圖台自身亦有 retry 與
    warm-up 機制），因此空回應必須重試，不能立即判定為查無。
    """

    params = {
        "type": "json",
        "flag": "3",
        "county": county.code,
        "parno": parcel.code,
        "alpah": "0.5f",
        "imgflag": 1,
    }

    body = ""
    for attempt in range(1, max(1, attempts) + 1):
        response = session.get(
            CADASTRAL_INDEX_ENDPOINT,
            params=params,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        body = response.text.strip()
        if body:
            return body
        if attempt < attempts:
            time.sleep(retry_delay_seconds)
    return body


def _query_single_parcel(
    county: CountyCode,
    parcel: LandParcel,
    session: requests.Session,
    *,
    timeout_seconds: int = 60,
    fetch_image: bool = False,
    attempts: int = 4,
    retry_delay_seconds: float = 0.8,
) -> ParcelInfo:
    body = _request_parcel_body(
        county,
        parcel,
        session,
        timeout_seconds=timeout_seconds,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
    )
    if not body:
        # 多次重試仍為空，才判定為查無此地號。
        return ParcelInfo(parcel=parcel, exists=False)

    meta: dict[str, Any] = {}
    image_row: dict[str, Any] = {}
    for document in _iter_json_documents(body):
        rows = document if isinstance(document, list) else [document]
        for row in rows:
            if not isinstance(row, dict):
                continue
            if "landno" in row and not meta:
                meta = row
            elif "image" in row and not image_row:
                image_row = row

    if not meta and not image_row:
        return ParcelInfo(parcel=parcel, exists=False)

    png: bytes | None = None
    if fetch_image and image_row.get("image"):
        try:
            png = base64.b64decode(image_row["image"])
        except (binascii.Error, ValueError):
            png = None

    return ParcelInfo(
        parcel=parcel,
        exists=True,
        section_name=_decode_b64_text(meta.get("sectStr", "")),
        land_office=_decode_b64_text(meta.get("officeStr", "")),
        min_longitude=_to_float(image_row.get("lx")),
        min_latitude=_to_float(image_row.get("ly")),
        max_longitude=_to_float(image_row.get("rx")),
        max_latitude=_to_float(image_row.get("ry")),
        tint_image_png=png,
    )


def verify_parcels(
    county: CountyCode | str,
    parcels: LandParcel | Iterable[LandParcel],
    *,
    session: requests.Session | None = None,
    fetch_images: bool = False,
    attempts: int = 4,
    retry_delay_seconds: float = 0.8,
) -> list[ParcelInfo]:
    """逐筆驗證地號是否存在，並取回宗地範圍與段名。"""

    parsed_county = CountyCode.parse(county)
    parcel_list = [parcels] if isinstance(parcels, LandParcel) else list(parcels)
    if not parcel_list:
        raise ValueError("至少需要一筆地段地號")

    owns_session = session is None
    client = session or create_landmaps_session()
    try:
        return [
            _query_single_parcel(
                parsed_county,
                parcel,
                client,
                fetch_image=fetch_images,
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            for parcel in parcel_list
        ]
    finally:
        if owns_session:
            client.close()


def _fit_zoom(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
    *,
    viewport_pixels: int = 900,
    padding_ratio: float = 1.8,
) -> int:
    """算出能容納範圍且不超過安全上限的縮放層級。"""

    mid_latitude = (min_latitude + max_latitude) / 2
    cos_lat = max(math.cos(math.radians(mid_latitude)), 1e-6)

    width_m = abs(max_longitude - min_longitude) * 111_320 * cos_lat
    height_m = abs(max_latitude - min_latitude) * 110_540
    span_m = max(width_m, height_m, 1.0) * padding_ratio

    for zoom in range(MAX_SAFE_ZOOM, MIN_ZOOM - 1, -1):
        resolution = 156_543.033_92 * cos_lat / (2**zoom)
        if span_m / viewport_pixels <= resolution:
            return zoom
    return MIN_ZOOM


@dataclass(frozen=True)
class ParcelViewPlan:
    """驗證結果與三個可用網址。"""

    county: CountyCode
    parcels: tuple[ParcelInfo, ...]
    zoom: int
    layer_url: str
    parcel_url: str
    view_url: str
    center: tuple[float, float] | None

    @property
    def found(self) -> tuple[ParcelInfo, ...]:
        return tuple(info for info in self.parcels if info.exists)

    @property
    def missing(self) -> tuple[ParcelInfo, ...]:
        return tuple(info for info in self.parcels if not info.exists)


def build_parcel_view(
    county: CountyCode | str,
    parcels: LandParcel | Iterable[LandParcel],
    *,
    base_map: BaseMap | str = BaseMap.EMAP,
    layers: Iterable[ExtraLayer | str] = DEFAULT_PARCEL_LAYERS,
    session: requests.Session | None = None,
    fetch_images: bool = False,
    verify: bool = True,
    attempts: int = 4,
    retry_delay_seconds: float = 0.8,
) -> ParcelViewPlan:
    """驗證地號並產生：套圖層網址、著色網址、安全縮放的檢視網址。

    著色網址只包含實際存在的地號，避免不存在的地號讓整批看起來沒作用。
    """

    parsed_county = CountyCode.parse(county)
    parcel_list = [parcels] if isinstance(parcels, LandParcel) else list(parcels)
    if not parcel_list:
        raise ValueError("至少需要一筆地段地號")

    if verify:
        infos = verify_parcels(
            parsed_county,
            parcel_list,
            session=session,
            fetch_images=fetch_images,
            attempts=attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
    else:
        infos = [ParcelInfo(parcel=parcel, exists=True) for parcel in parcel_list]

    existing = [info for info in infos if info.exists]
    # 著色網址優先只帶存在的地號；全部查無時仍帶原輸入以便使用者確認。
    tint_parcels = [info.parcel for info in existing] or parcel_list

    extents = [info for info in infos if info.has_extent]
    if extents:
        min_longitude = min(info.min_longitude for info in extents)
        min_latitude = min(info.min_latitude for info in extents)
        max_longitude = max(info.max_longitude for info in extents)
        max_latitude = max(info.max_latitude for info in extents)
        zoom = _fit_zoom(min_longitude, min_latitude, max_longitude, max_latitude)
        center = (
            (min_longitude + max_longitude) / 2,
            (min_latitude + max_latitude) / 2,
        )
        view_url = build_coordinate_url(
            center[0],
            center[1],
            zoom=zoom,
            base_map=base_map,
            layers=layers,
        )
    else:
        zoom = MAX_SAFE_ZOOM
        center = None
        view_url = ""

    return ParcelViewPlan(
        county=parsed_county,
        parcels=tuple(infos),
        zoom=zoom,
        layer_url=build_open_url(base_map, layers),
        parcel_url=build_goland_url(parsed_county, tint_parcels, base_map, layers),
        view_url=view_url,
        center=center,
    )


if __name__ == "__main__":
    # 新北市（F）金美段：489 存在、490 存在、489-1 不存在。
    candidates = [
        LandParcel.parse("1027", "489"),
        LandParcel.parse("1027", "490"),
        LandParcel.parse("1027", "489-1"),
    ]

    plan = build_parcel_view("新北市", candidates, fetch_images=True)

    print(f"縣市：{plan.county.label}（{plan.county.code}）")
    print("\n=== 地號驗證 ===")
    for info in plan.parcels:
        if info.exists:
            lon, lat = info.center or (0.0, 0.0)
            print(
                f"  ✓ {info.parcel.code}  {info.display}"
                f"（{info.land_office}地政）中心 {lon:.6f}, {lat:.6f}"
            )
        else:
            print(f"  ✗ {info.parcel.code}  查無此地號，已從著色網址排除")

    print(f"\n存在 {len(plan.found)} 筆、查無 {len(plan.missing)} 筆")
    print(f"建議縮放層級：{plan.zoom}（上限 {MAX_SAFE_ZOOM}，z21 起無圖磚）")

    print("\n1) 只套圖層（open）：")
    print(f"   {plan.layer_url}")
    print("2) 定位並著色宗地（goland，DMAPS 提供地號文字）：")
    print(f"   {plan.parcel_url}")
    print("3) 安全縮放檢視（go，底圖保證有圖磚）：")
    print(f"   {plan.view_url}")

    # 需要清楚判讀地號時，換成不含門牌的底圖避免與門牌混淆。
    reading = build_parcel_view(
        "新北市",
        [info.parcel for info in plan.found] or candidates,
        base_map=PARCEL_READING_BASE_MAP,
        verify=False,
    )
    print("4) 判讀地號用（底圖不含門牌，避免與地號混淆）：")
    print(f"   {reading.parcel_url}")

    print(
        "\n提醒：電子地圖上的「225號」是門牌，不是地號；"
        "地號來自地籍圖（DMAPS），可用層級 "
        f"z{CADASTRAL_TILE_MIN_ZOOM}～z{CADASTRAL_TILE_MAX_ZOOM}。"
    )

    saved = []
    for info in plan.found:
        path = info.save_tint_image(f"tint_{info.parcel.code}.png")
        if path:
            saved.append(path)
    if saved:
        print("\n已存出宗地著色圖：")
        for path in saved:
            print(f"   {path.name}（{path.stat().st_size} bytes）")
