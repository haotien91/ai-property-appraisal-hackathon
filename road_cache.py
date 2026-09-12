"""具名道路的本機網格快取，取代逐次呼叫 Overpass。

為什麼需要
----------
實測一次出圖 91.5 秒，其中 Overpass 佔 92%：

    Overpass 解析四條界線道路   61.2s   ← resolve_boundary_roads
                                        最多 4 種半徑 × 3 次重試 = 12 次查詢
    Overpass 抓畫面內所有路名   23.3s   ← fetch_road_names

公用端點單次就要 5～20 秒，且常回 429／504。同步 API 不可能建立在
這種延遲上，而且兩支查詢問的其實是同一件事：某個範圍內的具名道路。

做法
----
把經緯度切成固定網格（預設 0.02°，約 2 km），每格只向 Overpass 要一次
`way["highway"]["name"]`，結果以 gzip JSON 存到磁碟。之後任何查詢
（不論是半徑式或 bbox 式）都只是從涵蓋到的網格取資料再過濾。

道路幾何幾乎不變，快取沒有到期機制；要更新就刪目錄重抓。
跨網格的道路會在多格重複出現，合併時以 OSM id 去重。

安裝
----
    import road_cache
    road_cache.install(cache_dir="_road_cache")

install() 會把 render_zone_boundary 命名空間裡的 fetch_roads 與
fetch_road_names 換成快取版，其餘邏輯（半徑放大、路名比對、
方位指派）完全不動。
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import requests

from facility_distance import OVERPASS_ENDPOINT, OVERPASS_HEADERS

# 不能依賴 render_zone_boundary 先被 import：本模組可能先載入
# （zone_map_api 的 import 順序就是如此），因此自行把 POC 根目錄
# 放進 sys.path，否則 src.normalize 會 ImportError。
_POC_ROOT = (
    Path(__file__).parent / "ntpc_boundary_poc_work_ready" / "ntpc_boundary_poc"
)
if str(_POC_ROOT) not in sys.path:
    sys.path.insert(0, str(_POC_ROOT))

from src.normalize import normalize_name  # noqa: E402
from src.roads import _TO_3826, RoadFeature, _project_lonlat  # noqa: E402

CELL_DEGREES = float(os.environ.get("ROAD_CACHE_CELL_DEGREES", "0.02"))
DEFAULT_CACHE_DIR = os.environ.get("ROAD_CACHE_DIR", "_road_cache")

_cache_dir: Path | None = None
_lock = threading.Lock()
_memory: dict[tuple[int, int], list[dict]] = {}
_stats = {"cell_hit": 0, "cell_fetch": 0, "cell_fail": 0}

# 網格邊界對齊用的縮放：0.02° 以整數 key 表示避免浮點誤差。
_SCALE = round(1.0 / CELL_DEGREES)


def set_cache_dir(path: str | Path | None) -> Path | None:
    global _cache_dir
    if path is None:
        _cache_dir = None
        return None
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    _cache_dir = target
    return target


def stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def reset_stats() -> None:
    with _lock:
        _stats.update(cell_hit=0, cell_fetch=0, cell_fail=0)


def _cell_indices(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
) -> list[tuple[int, int]]:
    x0 = math.floor(min_longitude * _SCALE)
    x1 = math.floor(max_longitude * _SCALE)
    y0 = math.floor(min_latitude * _SCALE)
    y1 = math.floor(max_latitude * _SCALE)
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def _cell_bounds(cell: tuple[int, int]) -> tuple[float, float, float, float]:
    x, y = cell
    return (
        x / _SCALE,
        y / _SCALE,
        (x + 1) / _SCALE,
        (y + 1) / _SCALE,
    )


def _cell_path(cell: tuple[int, int]) -> Path | None:
    if _cache_dir is None:
        return None
    x, y = cell
    return _cache_dir / f"{x}_{y}.json.gz"


def _load_cell(cell: tuple[int, int]) -> list[dict] | None:
    if cell in _memory:
        with _lock:
            _stats["cell_hit"] += 1
        return _memory[cell]

    path = _cell_path(cell)
    if path is None or not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None

    elements = payload.get("elements", [])
    _memory[cell] = elements
    with _lock:
        _stats["cell_hit"] += 1
    return elements


def _store_cell(cell: tuple[int, int], elements: list[dict]) -> None:
    _memory[cell] = elements
    path = _cell_path(cell)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.part")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "cell": list(cell),
                    "cell_degrees": CELL_DEGREES,
                    "bounds": _cell_bounds(cell),
                    "elements": elements,
                },
                handle,
                ensure_ascii=False,
            )
        temporary.replace(path)
    except OSError:
        return


def _fetch_cell(
    cell: tuple[int, int],
    session: requests.Session,
    *,
    timeout_seconds: int = 90,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> list[dict] | None:
    """向 Overpass 要一個網格內所有具名道路；失敗回 None 且不寫快取。"""

    min_longitude, min_latitude, max_longitude, max_latitude = _cell_bounds(cell)
    query = (
        f"[out:json][timeout:{timeout_seconds}];"
        'way["highway"]["name"]'
        f"({min_latitude},{min_longitude},{max_latitude},{max_longitude});"
        "out geom;"
    )

    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = session.post(
                OVERPASS_ENDPOINT,
                data={"data": query},
                headers=OVERPASS_HEADERS,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            if attempt < attempts:
                time.sleep(retry_delay_seconds * attempt)
            continue

        elements = [
            element
            for element in payload.get("elements", [])
            if element.get("type") == "way"
            and (element.get("tags") or {}).get("name")
            and len(element.get("geometry") or []) >= 2
        ]
        with _lock:
            _stats["cell_fetch"] += 1
        return elements

    with _lock:
        _stats["cell_fail"] += 1
    return None


def named_roads_in_bbox(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
    *,
    session: requests.Session | None = None,
) -> list[dict]:
    """回傳範圍內所有具名道路的 Overpass way 元素（已跨網格去重）。"""

    owns_session = session is None
    client = session or requests.Session()
    try:
        merged: dict[int, dict] = {}
        for cell in _cell_indices(
            min_longitude, min_latitude, max_longitude, max_latitude
        ):
            elements = _load_cell(cell)
            if elements is None:
                elements = _fetch_cell(cell, client)
                if elements is None:
                    continue
                _store_cell(cell, elements)
            for element in elements:
                merged[int(element.get("id", 0))] = element
        return list(merged.values())
    finally:
        if owns_session:
            client.close()


def _elements_to_features(elements: list[dict]) -> list[RoadFeature]:
    from shapely.geometry import LineString

    features: list[RoadFeature] = []
    for element in elements:
        name = ((element.get("tags") or {}).get("name") or "").strip()
        geometry = element.get("geometry") or []
        if not name or len(geometry) < 2:
            continue
        lonlat = [(float(p["lon"]), float(p["lat"])) for p in geometry]
        features.append(
            RoadFeature(
                int(element.get("id", 0)),
                name,
                LineString(_project_lonlat(lonlat)),
            )
        )
    return features


def cached_fetch_roads(
    latitude: float,
    longitude: float,
    radius_m: int,
    names: list[str],
    session=None,
) -> list[RoadFeature]:
    """取代 src.roads.fetch_roads：半徑查詢改由網格快取供應。

    仍依名稱過濾，保持 resolve_road 失敗訊息裡的 available 清單語意
    （實測 P003 需要看到「只有啟智街187巷、沒有187巷24弄」）。
    """

    # 半徑換算成經緯度範圍；緯度方向固定，經度方向隨緯度收縮。
    delta_latitude = radius_m / 111_320.0
    cosine = max(math.cos(math.radians(latitude)), 1e-6)
    delta_longitude = radius_m / (111_320.0 * cosine)

    elements = named_roads_in_bbox(
        longitude - delta_longitude,
        latitude - delta_latitude,
        longitude + delta_longitude,
        latitude + delta_latitude,
        session=session if isinstance(session, requests.Session) else None,
    )

    wanted = {normalize_name(name) for name in names}
    features = [
        feature
        for feature in _elements_to_features(elements)
        if normalize_name(feature.name) in wanted
    ]

    # Overpass 的 way(around:) 是圓形範圍，這裡補上距離過濾以維持一致。
    from shapely.geometry import Point

    center = Point(*_TO_3826.transform(longitude, latitude))
    return [
        feature
        for feature in features
        if feature.geometry.distance(center) <= radius_m
    ]


def cached_fetch_road_names(
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
    """取代 render_parcel_map.fetch_road_names，回傳 [(路名, [(lon, lat)...])]。"""

    elements = named_roads_in_bbox(
        min_longitude,
        min_latitude,
        max_longitude,
        max_latitude,
        session=session,
    )

    roads: list[tuple[str, list[tuple[float, float]]]] = []
    for element in elements:
        name = ((element.get("tags") or {}).get("name") or "").strip()
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


def install(cache_dir: str | Path | None = DEFAULT_CACHE_DIR) -> Path | None:
    """把快取版注入 render_zone_boundary 的命名空間。

    只換這兩個查詢函式，半徑放大、路名比對、方位指派等已驗證過的
    邏輯完全不動。
    """

    import render_parcel_map
    import render_zone_boundary

    target = set_cache_dir(cache_dir)
    render_zone_boundary.fetch_roads = cached_fetch_roads
    render_zone_boundary.fetch_road_names = cached_fetch_road_names
    render_parcel_map.fetch_road_names = cached_fetch_road_names
    return target


def prewarm_bbox(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
    *,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    dry_run: bool = False,
) -> dict[str, int]:
    """把一個範圍（例如整個行政區）的網格全部抓齊。

    這才是正確的預熱粒度。快取以網格為單位，不以案件為單位：
    整個行政區抓過一次之後，該區任何新的條件 JSON 都不必再碰 Overpass。
    """

    set_cache_dir(cache_dir)
    reset_stats()
    cells = _cell_indices(min_longitude, min_latitude, max_longitude, max_latitude)
    missing = [cell for cell in cells if _load_cell(cell) is None]
    print(
        f"範圍涵蓋 {len(cells)} 格（每格 {CELL_DEGREES}°）："
        f"已有 {len(cells) - len(missing)} 格，需抓 {len(missing)} 格"
    )
    if dry_run:
        # 實測單格約 10～35 秒，用 25 秒估算給使用者一個量級。
        print(f"預估耗時約 {len(missing) * 25 / 60:.0f} 分鐘（未實際抓取）")
        return stats()

    with requests.Session() as session:
        for index, cell in enumerate(missing, start=1):
            started = time.perf_counter()
            elements = _fetch_cell(cell, session)
            if elements is None:
                print(f"  [{index}/{len(missing)}] {cell[0]}_{cell[1]} 抓取失敗")
                continue
            _store_cell(cell, elements)
            print(
                f"  [{index}/{len(missing)}] {cell[0]}_{cell[1]}  "
                f"{len(elements):5,} 條道路  {time.perf_counter() - started:5.1f}s"
            )
    return stats()


def prewarm_around(
    longitude: float,
    latitude: float,
    radius_km: float,
    *,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    dry_run: bool = False,
) -> dict[str, int]:
    """以一個點為中心、指定半徑（公里）預熱。"""

    delta_latitude = radius_km * 1000 / 111_320.0
    cosine = max(math.cos(math.radians(latitude)), 1e-6)
    delta_longitude = radius_km * 1000 / (111_320.0 * cosine)
    return prewarm_bbox(
        longitude - delta_longitude,
        latitude - delta_latitude,
        longitude + delta_longitude,
        latitude + delta_latitude,
        cache_dir=cache_dir,
        dry_run=dry_run,
    )


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="road_cache.py",
        description=(
            "預熱具名道路網格快取。以地理區域為單位，"
            "不是以案件為單位：整區抓過一次，該區所有 JSON 都不再碰 Overpass。"
        ),
    )
    parser.add_argument(
        "--cache-dir", default=DEFAULT_CACHE_DIR, help=f"預設 {DEFAULT_CACHE_DIR}"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只回報需要抓幾格與預估時間，不實際抓取",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="經緯度範圍，例：--bbox 121.38 24.94 121.46 25.03",
    )
    group.add_argument(
        "--around",
        nargs=3,
        type=float,
        metavar=("LON", "LAT", "RADIUS_KM"),
        help="以點為中心的半徑，例：--around 121.42 24.99 5",
    )
    group.add_argument(
        "--from-json",
        nargs="+",
        metavar="CASE_JSON",
        help="由條件 JSON 的宗地位置推得範圍（各自外擴 --margin-km）",
    )
    parser.add_argument(
        "--margin-km",
        type=float,
        default=2.5,
        help="--from-json 時每個宗地外擴的公里數，預設 2.5（涵蓋 1800 m 搜尋半徑）",
    )
    args = parser.parse_args(argv)

    if args.bbox:
        stats_result = prewarm_bbox(
            *args.bbox, cache_dir=args.cache_dir, dry_run=args.dry_run
        )
    elif args.around:
        longitude, latitude, radius_km = args.around
        stats_result = prewarm_around(
            longitude,
            latitude,
            radius_km,
            cache_dir=args.cache_dir,
            dry_run=args.dry_run,
        )
    else:
        from nlsc_map_url import LandParcel
        from nlsc_parcel_map import verify_parcels
        from render_zone_boundary import load_cases

        # 由 NLSC 取回宗地實際位置，再把所有位置的外擴範圍聯集成一個 bbox。
        west = south = float("inf")
        east = north = float("-inf")
        for path in args.from_json:
            parcels = [
                LandParcel.parse(str(case["section"]["code"]), str(case["parcel"]))
                for case in load_cases(path)
            ]
            for info in verify_parcels("新北市", parcels, fetch_images=False):
                if not info.exists:
                    continue
                longitude, latitude = info.center
                delta_latitude = args.margin_km * 1000 / 111_320.0
                cosine = max(math.cos(math.radians(latitude)), 1e-6)
                delta_longitude = args.margin_km * 1000 / (111_320.0 * cosine)
                west = min(west, longitude - delta_longitude)
                east = max(east, longitude + delta_longitude)
                south = min(south, latitude - delta_latitude)
                north = max(north, latitude + delta_latitude)

        if west > east:
            print("沒有任何宗地可定位，無法推得範圍")
            return 1
        print(f"由 JSON 推得範圍：經 {west:.4f}~{east:.4f} 緯 {south:.4f}~{north:.4f}")
        stats_result = prewarm_bbox(
            west, south, east, north, cache_dir=args.cache_dir, dry_run=args.dry_run
        )

    files = list(Path(args.cache_dir).rglob("*.json.gz"))
    print(
        f"\n結果 {stats_result}；"
        f"快取現況 {len(files)} 格、"
        f"{sum(f.stat().st_size for f in files) / 1e6:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
