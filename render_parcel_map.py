"""把已定位的宗地圖抓下來合成成 PNG。

流程：
1. 用著色索引 API 驗證地號並取得宗地範圍與著色圖。
2. 由範圍算出中心點與縮放層級（上限 z20，z21 起無圖磚）。
3. 依 Web Mercator 抓底圖與疊圖圖磚並拼接。
4. 把宗地著色圖依其實際範圍貼上。
5. 可選擇在宗地上標註地號文字。

圖磚來源分兩類，必須分開處理：
- 地籍圖 DMAPS：landmaps.nlsc.gov.tw/S_Maps/wmts，且必須帶 Referer，
  否則回傳 HTTP 200 但 0 位元組空白圖。
- 其他圖層（EMAP、URBAN、LANDSECT…）：wmts.nlsc.gov.tw/wmts。

用法：
    python render_parcel_map.py
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests
from PIL import Image, ImageDraw, ImageFont

from facility_distance import OVERPASS_ENDPOINT, OVERPASS_HEADERS
from getFacility import NlscSSLAdapter
from nlsc_map_url import BaseMap, CountyCode, ExtraLayer, LandParcel
from nlsc_parcel_map import (
    CADASTRAL_TILE_REFERER,
    DEFAULT_PARCEL_LAYERS,
    MAX_SAFE_ZOOM,
    MIN_ZOOM,
    ParcelInfo,
    verify_parcels,
)

TILE_SIZE = 256

# 需要透過 landmaps 主機並帶 Referer 的圖層。
_LANDMAPS_LAYERS = {"DMAPS"}
_LANDMAPS_TEMPLATE = (
    "https://landmaps.nlsc.gov.tw/S_Maps/wmts/{layer}/default/"
    "GoogleMapsCompatible/{z}/{y}/{x}"
)
_WMTS_TEMPLATE = (
    "https://wmts.nlsc.gov.tw/wmts/{layer}/default/GoogleMapsCompatible/{z}/{y}/{x}"
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) nlsc-render/1.0",
    "Accept": "image/png,image/jpeg,image/*,*/*",
}

# 透明文字圖層：疊在不透明的使用分區之上，才看得到道路名稱。
# EMAP12 = 臺灣通用電子地圖透明(無門牌)，實測 98% 透明的 RGBA PNG。
# 若需要門牌可改用 EMAP2（臺灣通用電子地圖透明）。
DEFAULT_LABEL_OVERLAY = "EMAP12"
LABEL_OVERLAY_WITH_HOUSE_NUMBER = "EMAP2"

# 中文字型候選。找不到任何一個就會退回 Pillow 內建點陣字型，
# 那個字型沒有 CJK 字符，路名與地號會整排變成豆腐框，
# 因此 Linux 容器必須安裝中文字型（見 Dockerfile 的 fonts-noto-cjk）。
# ZONE_MAP_FONT 可指定單一字型檔，優先於下列候選。
_FONT_CANDIDATES = tuple(
    path
    for path in (
        os.environ.get("ZONE_MAP_FONT"),
        # Linux／容器
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        # Windows
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\msjhbd.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
    )
    if path
)


def resolve_font_path() -> str | None:
    """回傳實際會用到的字型檔，找不到回 None（此時中文會是豆腐框）。"""

    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


MAP_PORTAL_URL = "https://maps.nlsc.gov.tw/"

# 需要先造訪圖台建立授權才會回傳圖磚的圖層。
# 未造訪時會回 HTTP 200 但 0 位元組空白圖；造訪後授權會延續一段時間
# （不僅限同一 session），因此固定先暖機最保險。
_SESSION_GATED_LAYERS = {
    "URBAN",
    "nURBAN",
    "nURBAN1",
    "nURBAN2",
    "LUIMAP",
    "LAND_OPENDATA",
}


def create_tile_session(*, warm_up: bool = True) -> requests.Session:
    """建立圖磚連線。

    warm_up 會先造訪圖台首頁取得 JSESSIONID；都市計畫使用分區等圖層
    未帶此 cookie 時會回傳 HTTP 200 但 0 位元組的空白圖。
    """

    session = requests.Session()
    for host in (
        "https://wmts.nlsc.gov.tw/",
        "https://landmaps.nlsc.gov.tw/",
        MAP_PORTAL_URL,
    ):
        session.mount(host, NlscSSLAdapter())
    session.headers.update(_HEADERS)

    if warm_up:
        try:
            session.get(MAP_PORTAL_URL, timeout=60)
        except requests.RequestException:
            # 暖機失敗不阻斷流程；受影響圖層會回報空白圖磚數。
            pass
    return session


# ------------------------------------------------------------ Web Mercator


def lonlat_to_pixel(
    longitude: float,
    latitude: float,
    zoom: int,
) -> tuple[float, float]:
    """經緯度轉為該縮放層級的世界像素座標。"""

    world = TILE_SIZE * (2**zoom)
    x = (longitude + 180.0) / 360.0 * world
    # 緯度需夾在 Web Mercator 有效範圍內，避免極區產生無限值。
    clamped = max(min(latitude, 85.05112878), -85.05112878)
    sin_lat = math.sin(math.radians(clamped))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * world
    return x, y


def pixel_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    """世界像素座標轉回經緯度（lonlat_to_pixel 的反函式）。"""

    world = TILE_SIZE * (2**zoom)
    longitude = x / world * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / world
    latitude = math.degrees(math.atan(math.sinh(n)))
    return longitude, latitude


def fetch_road_names(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
    *,
    session: requests.Session | None = None,
    timeout_seconds: int = 60,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> list[tuple[str, list[tuple[float, float]]]]:
    """由 OpenStreetMap 取得範圍內具名道路的名稱與線段座標。

    NLSC 沒有可直接取用的路名向量服務，故改用 Overpass；
    公用端點會拒絕沒有有意義 User-Agent 的請求，
    且連續請求會被限流（429／504），因此需要重試與間隔。
    回傳 [(路名, [(經度, 緯度), ...]), ...]，最終失敗時回傳空清單。
    """

    query = (
        f"[out:json][timeout:{timeout_seconds}];"
        f'way["highway"]["name"]'
        f"({min_latitude},{min_longitude},{max_latitude},{max_longitude});"
        f"out geom;"
    )

    owns_session = session is None
    client = session or requests.Session()
    payload: dict | None = None
    try:
        for attempt in range(1, max(1, attempts) + 1):
            try:
                response = client.post(
                    OVERPASS_ENDPOINT,
                    data={"data": query},
                    headers=OVERPASS_HEADERS,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError):
                # 被限流時退讓後重試；仍失敗則放棄路名標註。
                if attempt < attempts:
                    time.sleep(retry_delay_seconds * attempt)
    finally:
        if owns_session:
            client.close()

    if payload is None:
        # 路名標註屬加值資訊，取不到不應中斷出圖。
        return []

    roads: list[tuple[str, list[tuple[float, float]]]] = []
    for element in payload.get("elements", []):
        name = (element.get("tags") or {}).get("name", "").strip()
        if not name:
            continue
        points = [
            (node["lon"], node["lat"])
            for node in element.get("geometry", [])
            if "lon" in node and "lat" in node
        ]
        if len(points) >= 2:
            roads.append((name, points))
    return roads


def _draw_text_with_halo(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    halo: tuple[int, int, int, int],
    halo_width: int = 3,
) -> None:
    """描邊文字，確保在彩色分區圖上仍清楚可讀。"""

    x, y = position
    for offset_x in range(-halo_width, halo_width + 1):
        for offset_y in range(-halo_width, halo_width + 1):
            if offset_x or offset_y:
                draw.text(
                    (x + offset_x, y + offset_y),
                    text,
                    font=font,
                    fill=halo,
                )
    draw.text((x, y), text, font=font, fill=fill)


def _render_rotated_label(
    text: str,
    font: ImageFont.ImageFont,
    angle_degrees: float,
    fill: tuple[int, int, int, int],
    halo: tuple[int, int, int, int],
) -> Image.Image:
    """產生已旋轉的描邊文字圖，用於沿路方向標註。"""

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), text, font=font)
    pad = 8
    width = box[2] - box[0] + pad * 2
    height = box[3] - box[1] + pad * 2

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    _draw_text_with_halo(
        ImageDraw.Draw(layer),
        (pad - box[0], pad - box[1]),
        text,
        font,
        fill,
        halo,
    )
    return layer.rotate(angle_degrees, expand=True, resample=Image.BICUBIC)


def _road_label_candidates(
    screen_points: list[tuple[float, float]],
    width: int,
    height: int,
) -> list[tuple[float, float, float, float]]:
    """列出該路所有可見線段的候選標註點。

    回傳 [(中點x, 中點y, 角度, 線段長度), ...]，依長度由長到短。
    提供多個候選是必要的：只取最長線段時，一旦與其他標籤碰撞就會
    整條路都標不出來（實測中正路即因此消失）。
    """

    candidates: list[tuple[float, float, float, float]] = []

    for (x1, y1), (x2, y2) in zip(screen_points, screen_points[1:]):
        # 兩端都遠在畫面外的線段直接略過。
        if not (
            (-50 <= x1 <= width + 50 and -50 <= y1 <= height + 50)
            or (-50 <= x2 <= width + 50 and -50 <= y2 <= height + 50)
        ):
            continue

        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)
        if length <= 0:
            continue

        # 除線段中點外，另取 1/3、2/3 位置作為備援錨點。
        for ratio in (0.5, 0.34, 0.66):
            point_x = x1 + delta_x * ratio
            point_y = y1 + delta_y * ratio
            if not (0 <= point_x <= width and 0 <= point_y <= height):
                continue

            # 螢幕 y 向下為正，取負值換回一般數學角度。
            angle = math.degrees(math.atan2(-delta_y, delta_x))
            # 讓文字維持正向可讀。
            if angle > 90:
                angle -= 180
            elif angle < -90:
                angle += 180

            candidates.append((point_x, point_y, angle, length))

    candidates.sort(key=lambda item: -item[3])
    return candidates


def _pick_road_label_anchor(
    screen_points: list[tuple[float, float]],
    width: int,
    height: int,
) -> tuple[float, float, float] | None:
    """取最長可見線段的標註點，回傳 (中點x, 中點y, 角度)。"""

    candidates = _road_label_candidates(screen_points, width, height)
    if not candidates:
        return None
    point_x, point_y, angle, _length = candidates[0]
    return point_x, point_y, angle


def is_alley(name: str) -> bool:
    """判斷是否為巷弄。

    巷弄數量遠多於主要道路，若純以線段長度排序，
    主要道路會被巷弄擠出標註額度。
    """

    return any(token in name for token in ("巷", "弄", "衖"))


# ---------------------------------------------------------------------------
# 圖磚磁碟快取
#
# 一次出圖要抓 140 張圖磚（4 圖層 × 30～35 張），實測冷啟動 26～73 秒，
# 同步 API 無法承受。圖磚是靜態內容，相鄰區段又大量共用，
# 因此以磁碟快取換取可預期的回應時間，同時降低對 NLSC 的請求量。
# ---------------------------------------------------------------------------

_TILE_CACHE_DIR: Path | None = None
_TILE_CACHE_LOCK = threading.Lock()
_TILE_CACHE_STATS = {"hit": 0, "miss": 0, "store": 0}

# HTTP 200 但內容過小代表該圖磚本來就空白（超出可用層級或無資料）。
# 這種結果同樣要快取，否則每次出圖都會重新問一遍。
_BLANK_MARKER = b"__NLSC_BLANK__"


def set_tile_cache_dir(path: str | Path | None) -> Path | None:
    """設定圖磚快取目錄；傳入 None 代表關閉快取。"""

    global _TILE_CACHE_DIR
    if path is None:
        _TILE_CACHE_DIR = None
        return None
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    _TILE_CACHE_DIR = target
    return target


def tile_cache_stats() -> dict[str, int]:
    with _TILE_CACHE_LOCK:
        return dict(_TILE_CACHE_STATS)


def reset_tile_cache_stats() -> None:
    with _TILE_CACHE_LOCK:
        _TILE_CACHE_STATS.update(hit=0, miss=0, store=0)


def _tile_cache_path(layer: str, zoom: int, tile_x: int, tile_y: int) -> Path | None:
    if _TILE_CACHE_DIR is None:
        return None
    # 每層 z 一個目錄、每個 x 一個子目錄，避免單一目錄檔案數爆掉。
    return _TILE_CACHE_DIR / str(layer) / str(zoom) / str(tile_x) / f"{tile_y}.tile"


def _read_cached_tile(layer: str, zoom: int, tile_x: int, tile_y: int) -> bytes | None:
    path = _tile_cache_path(layer, zoom, tile_x, tile_y)
    if path is None or not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    with _TILE_CACHE_LOCK:
        _TILE_CACHE_STATS["hit"] += 1
    return data


def _write_cached_tile(
    layer: str, zoom: int, tile_x: int, tile_y: int, data: bytes
) -> None:
    path = _tile_cache_path(layer, zoom, tile_x, tile_y)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先寫暫存再改名，避免多執行緒或中斷造成半截檔案。
        temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part")
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError:
        return
    with _TILE_CACHE_LOCK:
        _TILE_CACHE_STATS["store"] += 1


# 環境變數啟用快取，容器裡指到掛載的 volume 即可，程式碼不用改。
if os.environ.get("NLSC_TILE_CACHE"):
    set_tile_cache_dir(os.environ["NLSC_TILE_CACHE"])


def _tile_url(layer: str, zoom: int, tile_x: int, tile_y: int) -> tuple[str, dict]:
    code = str(layer)
    template = _LANDMAPS_TEMPLATE if code in _LANDMAPS_LAYERS else _WMTS_TEMPLATE
    headers = dict(_HEADERS)
    # 地籍圖需 Referer；session 綁定的圖層帶上 Referer 亦無害。
    if code in _LANDMAPS_LAYERS or code in _SESSION_GATED_LAYERS:
        headers["Referer"] = CADASTRAL_TILE_REFERER
    return template.format(layer=code, z=zoom, x=tile_x, y=tile_y), headers


TILE_FETCH_WORKERS = int(os.environ.get("NLSC_TILE_WORKERS", "4"))


def _fetch_single_tile(
    layer: str,
    zoom: int,
    tile_x: int,
    tile_y: int,
    session: requests.Session,
    timeout_seconds: int,
) -> bytes | None:
    """取回單張圖磚的原始位元組；空白或失敗回 None。

    快取命中不碰網路；命中空白標記也直接回 None。
    """

    cached = _read_cached_tile(layer, zoom, tile_x, tile_y)
    if cached is not None:
        return None if cached == _BLANK_MARKER else cached

    with _TILE_CACHE_LOCK:
        _TILE_CACHE_STATS["miss"] += 1

    url, headers = _tile_url(layer, zoom, tile_x, tile_y)
    try:
        response = session.get(url, headers=headers, timeout=timeout_seconds)
    except requests.RequestException:
        # 連線失敗可能是暫時性的，不寫入快取以免把錯誤固化。
        return None

    if response.status_code != 200:
        return None
    if len(response.content) < 100:
        # 空白圖磚（常見於超出可用層級或缺 Referer）回傳極小內容。
        _write_cached_tile(layer, zoom, tile_x, tile_y, _BLANK_MARKER)
        return None

    _write_cached_tile(layer, zoom, tile_x, tile_y, response.content)
    return response.content


def _fetch_layer_image(
    layer: str,
    zoom: int,
    left: float,
    top: float,
    width: int,
    height: int,
    session: requests.Session,
    *,
    timeout_seconds: int = 60,
    workers: int | None = None,
) -> tuple[Image.Image, int, int]:
    """抓取並拼接單一圖層，回傳 (影像, 成功圖磚數, 空白圖磚數)。

    圖磚以執行緒池並行抓取：單張圖層 30～35 張圖磚循序抓取是
    同步出圖的主要耗時來源，並行後牆鐘時間大幅下降。
    貼圖仍在主執行緒依序進行，避免 Pillow 的畫布競爭。
    """

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    first_tile_x = math.floor(left / TILE_SIZE)
    last_tile_x = math.floor((left + width - 1) / TILE_SIZE)
    first_tile_y = math.floor(top / TILE_SIZE)
    last_tile_y = math.floor((top + height - 1) / TILE_SIZE)

    max_index = 2**zoom
    targets: list[tuple[int, int, int]] = []
    for tile_y in range(first_tile_y, last_tile_y + 1):
        if not 0 <= tile_y < max_index:
            continue
        for tile_x in range(first_tile_x, last_tile_x + 1):
            targets.append((tile_x, tile_x % max_index, tile_y))

    if not targets:
        return canvas, 0, 0

    pool_size = max(1, workers if workers is not None else TILE_FETCH_WORKERS)
    pool_size = min(pool_size, len(targets))

    if pool_size == 1:
        payloads = [
            _fetch_single_tile(
                layer, zoom, wrapped_x, tile_y, session, timeout_seconds
            )
            for _tile_x, wrapped_x, tile_y in targets
        ]
    else:
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            payloads = list(
                pool.map(
                    lambda item: _fetch_single_tile(
                        layer, zoom, item[1], item[2], session, timeout_seconds
                    ),
                    targets,
                )
            )

    filled = 0
    blank = 0
    for (tile_x, _wrapped_x, tile_y), payload in zip(targets, payloads):
        if payload is None:
            blank += 1
            continue
        try:
            tile = Image.open(io.BytesIO(payload)).convert("RGBA")
        except OSError:
            blank += 1
            continue

        offset_x = int(round(tile_x * TILE_SIZE - left))
        offset_y = int(round(tile_y * TILE_SIZE - top))
        canvas.paste(tile, (offset_x, offset_y), tile)
        filled += 1

    return canvas, filled, blank


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit_zoom_for_viewport(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
    width: int,
    height: int,
    *,
    padding_ratio: float = 3.0,
) -> int:
    """找出能把範圍含 padding 放進畫布的最大層級，且不超過 z20。"""

    for zoom in range(MAX_SAFE_ZOOM, MIN_ZOOM - 1, -1):
        left, top = lonlat_to_pixel(min_longitude, max_latitude, zoom)
        right, bottom = lonlat_to_pixel(max_longitude, min_latitude, zoom)
        span_x = max(abs(right - left), 1.0) * padding_ratio
        span_y = max(abs(bottom - top), 1.0) * padding_ratio
        if span_x <= width and span_y <= height:
            return zoom
    return MIN_ZOOM


@dataclass
class RenderResult:
    output_path: Path
    county: CountyCode
    zoom: int
    center: tuple[float, float]
    width: int
    height: int
    parcels: tuple[ParcelInfo, ...]
    layer_tiles: dict[str, tuple[int, int]] = field(default_factory=dict)
    road_labels: tuple[str, ...] = ()

    @property
    def found(self) -> tuple[ParcelInfo, ...]:
        return tuple(info for info in self.parcels if info.exists)

    @property
    def missing(self) -> tuple[ParcelInfo, ...]:
        return tuple(info for info in self.parcels if not info.exists)


def render_parcel_map(
    county: CountyCode | str,
    parcels: LandParcel | Iterable[LandParcel],
    output_path: str | Path = "parcel_map.png",
    *,
    base_map: BaseMap | str = BaseMap.EMAP_NO_HOUSE_NUMBER,
    overlays: Iterable[ExtraLayer | str] = DEFAULT_PARCEL_LAYERS,
    width: int = 1200,
    height: int = 900,
    zoom: int | None = None,
    annotate: bool = True,
    label_overlay: str | None = DEFAULT_LABEL_OVERLAY,
    annotate_roads: bool = True,
    road_font_size: int = 34,
    max_road_labels: int = 20,
    include_alleys: bool = False,
    session: requests.Session | None = None,
) -> RenderResult:
    """抓取並合成已定位的宗地圖，存成 PNG。

    base_map 預設用不含門牌的 EMAP16，避免門牌數字與地號混淆。
    label_overlay 是透明文字圖層，疊在不透明的使用分區之上，
    否則道路名稱會被分區顏色蓋掉；設為 None 可關閉。
    annotate_roads 會另外以大字沿路方向標註路名（資料來自 OpenStreetMap）。
    預設不標巷弄；巷弄數量多且對判讀區位幫助有限，
    需要時可設 include_alleys=True。
    """

    if width <= 0 or height <= 0:
        raise ValueError("畫布寬高必須大於 0")
    if zoom is not None and not MIN_ZOOM <= zoom <= MAX_SAFE_ZOOM:
        raise ValueError(f"縮放層級必須介於 {MIN_ZOOM} 到 {MAX_SAFE_ZOOM}")

    parsed_county = CountyCode.parse(county)
    parcel_list = [parcels] if isinstance(parcels, LandParcel) else list(parcels)
    if not parcel_list:
        raise ValueError("至少需要一筆地段地號")

    infos = verify_parcels(parsed_county, parcel_list, fetch_images=True)
    located = [info for info in infos if info.has_extent]
    if not located:
        raise ValueError(
            "所有地號都查無範圍，無法定位；請確認縣市代碼、段代碼與地號是否正確"
        )

    min_longitude = min(info.min_longitude for info in located)
    min_latitude = min(info.min_latitude for info in located)
    max_longitude = max(info.max_longitude for info in located)
    max_latitude = max(info.max_latitude for info in located)

    resolved_zoom = zoom or _fit_zoom_for_viewport(
        min_longitude,
        min_latitude,
        max_longitude,
        max_latitude,
        width,
        height,
    )
    center = (
        (min_longitude + max_longitude) / 2,
        (min_latitude + max_latitude) / 2,
    )

    center_x, center_y = lonlat_to_pixel(center[0], center[1], resolved_zoom)
    left = center_x - width / 2
    top = center_y - height / 2

    owns_session = session is None
    client = session or create_tile_session()
    try:
        canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        layer_tiles: dict[str, tuple[int, int]] = {}

        # 順序決定疊圖結果：底圖 → 分區與地籍 → 透明文字圖層。
        # 文字圖層必須在最後，否則路名會被不透明的分區顏色蓋住。
        stack: list[str] = [str(base_map), *[str(layer) for layer in overlays]]
        if label_overlay:
            stack.append(str(label_overlay))

        for code in stack:
            image, filled, blank = _fetch_layer_image(
                code,
                resolved_zoom,
                left,
                top,
                width,
                height,
                client,
            )
            layer_tiles[code] = (filled, blank)
            canvas = Image.alpha_composite(canvas, image)

        roads: list[tuple[str, list[tuple[float, float]]]] = []
        if annotate_roads:
            west, north = pixel_to_lonlat(left, top, resolved_zoom)
            east, south = pixel_to_lonlat(
                left + width,
                top + height,
                resolved_zoom,
            )
            roads = fetch_road_names(west, south, east, north)
    finally:
        if owns_session:
            client.close()

    # 貼上宗地著色圖：依各自範圍換算像素框後縮放貼上。
    for info in located:
        if not info.tint_image_png:
            continue
        try:
            tint = Image.open(io.BytesIO(info.tint_image_png)).convert("RGBA")
        except OSError:
            continue

        box_left, box_top = lonlat_to_pixel(
            info.min_longitude,
            info.max_latitude,
            resolved_zoom,
        )
        box_right, box_bottom = lonlat_to_pixel(
            info.max_longitude,
            info.min_latitude,
            resolved_zoom,
        )
        target_width = max(int(round(box_right - box_left)), 1)
        target_height = max(int(round(box_bottom - box_top)), 1)
        resized = tint.resize((target_width, target_height), Image.LANCZOS)

        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        layer.paste(
            resized,
            (int(round(box_left - left)), int(round(box_top - top))),
            resized,
        )
        canvas = Image.alpha_composite(canvas, layer)

    # 大字路名：同名道路只標一次，沿路方向旋轉。
    road_labels: list[str] = []
    occupied: list[tuple[float, float, float, float]] = []
    if roads:
        road_font = _load_font(road_font_size)

        # 同一路名常由多條 way 組成，候選錨點需合併後一併評估。
        candidates_by_name: dict[str, list[tuple[float, float, float, float]]] = {}
        for name, points in roads:
            if not include_alleys and is_alley(name):
                continue

            screen_points = []
            for longitude, latitude in points:
                point_x, point_y = lonlat_to_pixel(
                    longitude,
                    latitude,
                    resolved_zoom,
                )
                screen_points.append((point_x - left, point_y - top))

            found = _road_label_candidates(screen_points, width, height)
            if found:
                candidates_by_name.setdefault(name, []).extend(found)

        for name in candidates_by_name:
            candidates_by_name[name].sort(key=lambda item: -item[3])

        # 主要道路優先於巷弄，其次才比線段長度。
        # include_alleys=True 時巷弄才會進來，且一律排在主要道路之後。
        ordered_names = sorted(
            candidates_by_name,
            key=lambda name: (is_alley(name), -candidates_by_name[name][0][3]),
        )

        for name in ordered_names:
            if len(road_labels) >= max_road_labels:
                break

            for point_x, point_y, angle, _length in candidates_by_name[name]:
                label = _render_rotated_label(
                    name,
                    road_font,
                    angle,
                    fill=(20, 20, 20, 255),
                    halo=(255, 255, 255, 235),
                )
                paste_x = point_x - label.width / 2
                paste_y = point_y - label.height / 2
                box = (
                    paste_x,
                    paste_y,
                    paste_x + label.width,
                    paste_y + label.height,
                )

                if box[2] < 0 or box[0] > width or box[3] < 0 or box[1] > height:
                    continue

                collides = any(
                    box[0] < other[2]
                    and box[2] > other[0]
                    and box[1] < other[3]
                    and box[3] > other[1]
                    for other in occupied
                )
                if collides:
                    # 換下一個候選位置，而不是整條路直接放棄。
                    continue

                layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                layer.paste(
                    label,
                    (int(round(paste_x)), int(round(paste_y))),
                    label,
                )
                canvas = Image.alpha_composite(canvas, layer)

                occupied.append(box)
                road_labels.append(name)
                break

    if annotate:
        draw = ImageDraw.Draw(canvas)
        font = _load_font(18)
        placed: list[tuple[float, float, float, float]] = list(occupied) if roads else []

        for info in located:
            longitude, latitude = info.center
            point_x, point_y = lonlat_to_pixel(longitude, latitude, resolved_zoom)
            screen_x = point_x - left
            screen_y = point_y - top

            label = info.display
            box = draw.textbbox((0, 0), label, font=font)
            text_width = box[2] - box[0]
            text_height = box[3] - box[1]
            pad = 4
            rect_x = screen_x - text_width / 2 - pad
            rect_y = screen_y - text_height - 18 - pad

            # 相鄰宗地的標籤會疊在一起，往上讓開直到不重疊。
            rect_width = text_width + pad * 2
            rect_height = text_height + pad * 2
            step = rect_height + 4
            for _ in range(12):
                collides = any(
                    rect_x < other_right
                    and rect_x + rect_width > other_left
                    and rect_y < other_bottom
                    and rect_y + rect_height > other_top
                    for other_left, other_top, other_right, other_bottom in placed
                )
                if not collides:
                    break
                rect_y -= step
            placed.append(
                (rect_x, rect_y, rect_x + rect_width, rect_y + rect_height)
            )

            # 標籤讓開後，用細線連回宗地中心。
            draw.line(
                [screen_x, rect_y + rect_height, screen_x, screen_y],
                fill=(200, 30, 30, 180),
                width=1,
            )

            draw.rectangle(
                [
                    rect_x,
                    rect_y,
                    rect_x + text_width + pad * 2,
                    rect_y + text_height + pad * 2,
                ],
                fill=(255, 255, 255, 235),
                outline=(200, 30, 30, 255),
                width=2,
            )
            draw.text(
                (rect_x + pad, rect_y + pad),
                label,
                fill=(180, 20, 20, 255),
                font=font,
            )
            # 以十字標出宗地中心。
            draw.line(
                [screen_x - 8, screen_y, screen_x + 8, screen_y],
                fill=(200, 30, 30, 255),
                width=2,
            )
            draw.line(
                [screen_x, screen_y - 8, screen_x, screen_y + 8],
                fill=(200, 30, 30, 255),
                width=2,
            )

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(target, format="PNG")

    return RenderResult(
        output_path=target,
        county=parsed_county,
        zoom=resolved_zoom,
        center=center,
        width=width,
        height=height,
        parcels=tuple(infos),
        layer_tiles=layer_tiles,
        road_labels=tuple(road_labels),
    )


def parse_parcel_token(
    token: str,
    default_section: str | None = None,
) -> LandParcel:
    """解析命令列的地段地號。

    支援 "1027/489"、"1027:489"、"1027/489-1"；
    若給了 --section，也可只寫 "489"。
    分隔符不用 "-"，因為 "-" 已用於子號。
    """

    text = token.strip()
    if not text:
        raise ValueError("地段地號不可為空")

    for separator in ("/", ":"):
        if separator in text:
            section, number = text.split(separator, 1)
            return LandParcel.parse(section, number)

    if default_section:
        return LandParcel.parse(default_section, text)

    raise ValueError(
        f"無法解析 {token!r}；請寫成 段/地號（例如 1027/489），"
        "或改用 --section 指定地段"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_parcel_map.py",
        description="抓取國土測繪圖台的宗地圖並合成 PNG（含使用分區、地籍地號、路名大字）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "範例：\n"
            "  python render_parcel_map.py 1027/489\n"
            "  python render_parcel_map.py 1027/489 1027/490\n"
            "  python render_parcel_map.py 1027/489 1026/218 -o 金山.png\n"
            "  python render_parcel_map.py -s 1027 489 490 491\n"
            "  python render_parcel_map.py 1027/489-1 --county 新北市\n"
            "  python render_parcel_map.py 1027/489 --base-map PHOTO2 --width 1600\n"
        ),
    )
    parser.add_argument(
        "parcels",
        nargs="+",
        metavar="段/地號",
        help="地段地號，可多筆；例如 1027/489 1026/218。搭配 --section 時可只寫地號。",
    )
    parser.add_argument(
        "-c",
        "--county",
        default="新北市",
        help="縣市名稱或代碼，預設 新北市（可用 F）",
    )
    parser.add_argument(
        "-s",
        "--section",
        default=None,
        help="預設地段代碼（4 碼）；設定後地號可只寫數字",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="parcel_map.png",
        help="輸出 PNG 路徑，預設 parcel_map.png",
    )
    parser.add_argument("--width", type=int, default=1200, help="圖寬，預設 1200")
    parser.add_argument("--height", type=int, default=900, help="圖高，預設 900")
    parser.add_argument(
        "--zoom",
        type=int,
        default=None,
        help=f"指定縮放層級 1~{MAX_SAFE_ZOOM}；預設自動依宗地範圍決定",
    )
    parser.add_argument(
        "--base-map",
        default=BaseMap.EMAP_NO_HOUSE_NUMBER.value,
        choices=[item.value for item in BaseMap],
        help="底圖代碼，預設 EMAP16（不含門牌，避免與地號混淆）",
    )
    parser.add_argument(
        "--no-zoning",
        action="store_true",
        help="不套都市計畫使用分區圖",
    )
    parser.add_argument(
        "--no-cadastral",
        action="store_true",
        help="不套地籍圖（會失去宗地界線與地號文字）",
    )
    parser.add_argument(
        "--no-label-overlay",
        action="store_true",
        help="不套透明文字圖層（路名會被使用分區顏色蓋掉）",
    )
    parser.add_argument(
        "--no-roads",
        action="store_true",
        help="不加大字路名標註",
    )
    parser.add_argument(
        "--include-alleys",
        action="store_true",
        help="路名標註含巷弄，預設不含",
    )
    parser.add_argument(
        "--max-roads",
        type=int,
        default=20,
        help="最多標註幾條路名，預設 20",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=34,
        help="路名字級，預設 34",
    )
    parser.add_argument(
        "--no-parcel-labels",
        action="store_true",
        help="不標宗地地號框",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        parcels = [
            parse_parcel_token(token, args.section) for token in args.parcels
        ]
    except ValueError as exc:
        print(f"參數錯誤：{exc}", file=sys.stderr)
        return 2

    overlays: list[ExtraLayer | str] = []
    if not args.no_zoning:
        overlays.append(ExtraLayer.URBAN)
    if not args.no_cadastral:
        overlays.append(ExtraLayer.DMAPS)

    try:
        result = render_parcel_map(
            county=args.county,
            parcels=parcels,
            output_path=args.output,
            base_map=args.base_map,
            overlays=tuple(overlays),
            width=args.width,
            height=args.height,
            zoom=args.zoom,
            annotate=not args.no_parcel_labels,
            label_overlay=None if args.no_label_overlay else DEFAULT_LABEL_OVERLAY,
            annotate_roads=not args.no_roads,
            road_font_size=args.font_size,
            max_road_labels=args.max_roads,
            include_alleys=args.include_alleys,
        )
    except ValueError as exc:
        print(f"無法出圖：{exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"連線失敗：{exc}", file=sys.stderr)
        return 1

    print(f"縣市：{result.county.label}（{result.county.code}）")
    for info in result.parcels:
        status = "✓" if info.exists else "✗ 查無"
        print(f"  {status} {info.parcel.code}  {info.display}")

    if result.missing:
        print(
            f"\n注意：{len(result.missing)} 筆查無，已從著色排除；"
            "請確認縣市、段代碼與地號"
        )

    print(f"\n中心：{result.center[0]:.6f}, {result.center[1]:.6f}")
    print(f"層級：z{result.zoom}（上限 z{MAX_SAFE_ZOOM}）")
    print("圖層圖磚（成功/空白）：")
    for code, (filled, blank) in result.layer_tiles.items():
        flag = "" if filled else "   <-- 全空白，該圖層沒畫上"
        print(f"  {code:10s} {filled} / {blank}{flag}")

    if result.road_labels:
        print(f"\n大字路名標註 {len(result.road_labels)} 條：")
        print("  " + "、".join(result.road_labels))
    elif not args.no_roads:
        print("\n未取得路名標註（Overpass 無回應或範圍內無具名道路）")

    size_kb = result.output_path.stat().st_size / 1024
    print(
        f"\n已存出：{result.output_path}（{size_kb:.1f} KB，"
        f"{result.width}x{result.height}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
