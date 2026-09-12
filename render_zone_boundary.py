"""依道路條件框出地價區段範圍，並疊在 NLSC 光柵地籍圖上輸出 PNG。

資料來源全部為真實線上服務，不使用合成幾何：
- 宗地位置與範圍：NLSC 著色索引 API（nlsc_parcel_map）
- 道路中心線：OpenStreetMap Overpass（重用 POC 的 src.roads）
- 底圖／地籍圖／段籍圖／路名：NLSC WMTS（render_parcel_map）

方位語意沿用 POC 的 src.partition：
    north_of 表示「本區段位於該路之北」，該路因此在區段南側。

流程：
1. 由 input JSON 取得地段地號與四條界線道路。
2. 向 NLSC 驗證宗地並取得實際範圍（不採用輸入座標，避免輸入誤差）。
3. 向 Overpass 取四條道路，投影至 TWD97 TM2（EPSG:3826）。
4. 以道路緩衝帶切割 ROI，取出「包含宗地且滿足四個方位」的區塊。
5. 依區段範圍決定視野與縮放層級，抓 NLSC 圖磚合成底圖。
6. 畫上綠色區段邊界與紅色宗地，並驗證宗地確實在區段內。
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import os
import re
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw
from pyproj import Transformer
from shapely.geometry import (
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
    mapping,
)
from shapely.ops import linemerge, nearest_points, split, unary_union
from shapely.prepared import prep
from shapely.ops import transform as shapely_transform

from block_extract import BlockExtractionError, extract_block
from facility_distance import OVERPASS_HEADERS
from nlsc_map_url import (
    BaseMap,
    CountyCode,
    ExtraLayer,
    LandParcel,
    build_goland_url,
)
from nlsc_parcel_map import MAX_SAFE_ZOOM, MIN_ZOOM, ParcelInfo, verify_parcels
from render_parcel_map import (
    DEFAULT_LABEL_OVERLAY,
    _fetch_layer_image,
    _load_font,
    _render_rotated_label,
    _road_label_candidates,
    create_tile_session,
    fetch_road_names,
    is_alley,
    lonlat_to_pixel,
    pixel_to_lonlat,
)

# POC 的 roads/partition 只依賴 requests/pyproj/shapely，可直接重用。
_POC_ROOT = (
    Path(__file__).parent / "ntpc_boundary_poc_work_ready" / "ntpc_boundary_poc"
)
if str(_POC_ROOT) not in sys.path:
    sys.path.insert(0, str(_POC_ROOT))

from src.normalize import first_explicit_road  # noqa: E402
from src.partition import (  # noqa: E402
    _satisfies_direction,
    build_barriers,
    partition_roi,
)
from src.roads import RoadFeature, fetch_roads, resolve_road  # noqa: E402

_TO_3826 = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True).transform
_TO_4326 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True).transform

RELATIONS = ("north_of", "west_of", "south_of", "east_of")

# 疊圖：地籍圖提供宗地界線與地號、段籍圖提供地段外圍、EMAP12 補回路名。
DEFAULT_OVERLAYS: tuple[ExtraLayer | str, ...] = (
    ExtraLayer.DMAPS,
    ExtraLayer.LANDSECT,
)

BOUNDARY_COLOR = (200, 0, 0, 255)
PARCEL_COLOR = (255, 43, 0, 150)
PARCEL_OUTLINE = (176, 12, 18, 255)

# 參考圖樣式：路名藍字、區段編號與比準地為紅字紅框白底。
ROAD_LABEL_COLOR = (0, 60, 200, 255)
ROAD_LABEL_HALO = (255, 255, 255, 235)
ANNOTATION_TEXT = (200, 0, 0, 255)
ANNOTATION_BORDER = (200, 0, 0, 255)
ANNOTATION_FILL = (255, 255, 255, 240)


@dataclass
class BoundaryResult:
    output_path: Path
    parcel: LandParcel
    parcel_info: ParcelInfo
    boundary_3826: Polygon
    boundary_4326: Polygon
    matched_roads: dict[str, str]
    zoom: int
    width: int
    height: int
    area_m2: float
    parcel_inside_ratio: float
    anchor_inside: bool
    road_offset_m: float = 0.0
    boundary_source: str = "cadastral"
    snapped_vertices: int = 0
    average_snap_px: float = 0.0
    verify_url: str = ""
    zone_code: str | None = None
    road_labels: tuple[str, ...] = ()
    direction_audit: dict[str, bool] = field(default_factory=dict)
    layer_tiles: dict[str, tuple[int, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # 一個區段可有多筆比準地；parcel/parcel_info 保留為第一筆以維持相容。
    parcels: tuple[LandParcel, ...] = ()
    parcel_infos: tuple[ParcelInfo, ...] = ()


def load_case(path: str | Path) -> dict:
    return load_cases(path)[0]


def load_cases(path: str | Path) -> list[dict]:
    """讀取條件 JSON；允許單一物件或物件陣列。

    一個地價區段可以有多筆比準地（實測 P001 太平段 367 與 917
    相距 8.6 m，落在同一個使用分區多邊形），此時 JSON 為陣列，
    所有宗地要畫在同一張圖上、共用同一個區段範圍。
    """

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    cases = payload if isinstance(payload, list) else [payload]
    if not cases:
        raise ValueError("條件 JSON 為空陣列")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"條件 JSON 第 {index + 1} 筆不是物件")
        if "section" not in case or "parcel" not in case:
            raise ValueError(f"條件 JSON 第 {index + 1} 筆缺少 section 或 parcel")
    return cases


def boundary_road_names(case: dict) -> tuple[dict[str, str], list[str]]:
    """取四個方位的道路名稱；複合描述只取第一個明確路名並記錄警告。"""

    constraints = case.get("constraints") or {}
    names: dict[str, str] = {}
    warnings: list[str] = []
    for relation in RELATIONS:
        raw = constraints.get(relation)
        if not raw:
            raise ValueError(f"input 缺少方位條件：{relation}")
        road, notes = first_explicit_road(str(raw))
        names[relation] = road
        warnings.extend(f"{relation}: {note}" for note in notes)

    # 同一條路被指派到多個方位時，第二次切割不會有作用，
    # 區段會比實際大。這是資料問題，必須明確點出而非默默出圖。
    grouped: dict[str, list[str]] = {}
    for relation, road in names.items():
        grouped.setdefault(road, []).append(relation)
    for road, relations in grouped.items():
        if len(relations) > 1:
            # 同一條路可能因轉折（L 形）而構成兩側邊界，照指派使用即可。
            warnings.append(
                f"註：{road} 同時作為 {'、'.join(relations)} 的界線，已照指派採用"
            )

    return names, warnings


def create_overpass_session() -> requests.Session:
    """Overpass 公用端點會拒絕沒有有意義 User-Agent 的請求（回 406／429）。

    POC 的 src.roads.fetch_roads 預設直接用 requests 模組、不帶標頭，
    因此必須注入這個 session，否則一律 406。
    """

    session = requests.Session()
    session.headers.update(OVERPASS_HEADERS)
    return session


def resolve_boundary_roads(
    latitude: float,
    longitude: float,
    names: dict[str, str],
    *,
    radii: Iterable[int] = (450, 750, 1200, 1800),
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    session: requests.Session | None = None,
    require_all: bool = True,
) -> dict[str, RoadFeature]:
    """逐步放大搜尋半徑取回界線道路。

    require_all=False 時允許部分解析：OSM 常缺小巷弄
    （實測 P003 的啟智街187巷24弄 不存在，只有啟智街187巷），
    但界線文字位置由區段邊界決定、地籍萃取也不需要道路幾何，
    因此缺少某條路不應讓整案失敗。
    """

    unique_names = list(dict.fromkeys(names.values()))
    last_error: Exception | None = None

    owns_session = session is None
    client = session or create_overpass_session()
    try:
        for radius in radii:
            features: list[RoadFeature] | None = None
            for attempt in range(1, max(1, attempts) + 1):
                try:
                    features = fetch_roads(
                        latitude,
                        longitude,
                        radius,
                        unique_names,
                        session=client,
                    )
                    break
                except requests.RequestException as exc:
                    # 連線或限流問題與「查無道路」必須分開判讀。
                    last_error = exc
                    if attempt < attempts:
                        time.sleep(retry_delay_seconds * attempt)

            if features is None:
                continue

            resolved: dict[str, RoadFeature] = {}
            failures: dict[str, Exception] = {}
            for relation, name in names.items():
                try:
                    resolved[relation] = resolve_road(features, name)
                except Exception as exc:
                    failures[relation] = exc

            if not failures:
                return resolved
            if not require_all and resolved:
                # 記錄缺少的道路，但仍回傳已解析的部分。
                for relation, exc in failures.items():
                    last_error = exc
                resolved["__missing__"] = None  # type: ignore[assignment]
                del resolved["__missing__"]
                setattr(resolve_boundary_roads, "last_missing", sorted(failures))
                return resolved
            last_error = next(iter(failures.values()))
    finally:
        if owns_session:
            client.close()

    raise RuntimeError(f"無法取得四條界線道路（已放大搜尋半徑）：{last_error}")


def parcel_polygon_3826(info: ParcelInfo) -> Polygon:
    """NLSC 回傳的宗地範圍為外接矩形，用於落點檢核已足夠。"""

    if not info.has_extent:
        raise ValueError("宗地無範圍資料，無法檢核是否位於區段內")
    rectangle = box(
        info.min_longitude,
        info.min_latitude,
        info.max_longitude,
        info.max_latitude,
    )
    return shapely_transform(_TO_3826, rectangle)



def _tint_pixel_size_m(info: ParcelInfo) -> float:
    """著色圖單一像素代表的地面尺寸（公尺）。"""

    if not info.tint_image_png or not info.has_extent:
        return 0.0
    image = Image.open(io.BytesIO(info.tint_image_png))
    if image.width == 0:
        return 0.0
    mid_latitude = (info.min_latitude + info.max_latitude) / 2
    width_m = (
        (info.max_longitude - info.min_longitude)
        * 111_320
        * math.cos(math.radians(mid_latitude))
    )
    return abs(width_m) / image.width


def parcel_polygon_from_tint(info: ParcelInfo) -> Polygon | MultiPolygon | None:
    """由著色圖 alpha 遮罩萃取真實宗地多邊形（WGS84）。

    著色圖是宗地實際輪廓（實測文林段317僅 30% 不透明），
    取其外輪廓即為宗地邊界，用於保證比準地完整落在區段內。
    """

    if not info.tint_image_png or not info.has_extent:
        return None

    image = Image.open(io.BytesIO(info.tint_image_png)).convert("RGBA")
    alpha = np.asarray(image)[:, :, 3]
    mask = (alpha > 10).astype(np.uint8)
    if mask.sum() == 0:
        return None

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None

    pixel_height, pixel_width = mask.shape
    delta_longitude = (info.max_longitude - info.min_longitude) / pixel_width
    delta_latitude = (info.max_latitude - info.min_latitude) / pixel_height

    # 取全部外輪廓：宗地可能被道路或建物切成數塊，
    # 只取最大塊會漏掉其餘部分，導致補齊後仍不足 100%。
    parts: list[Polygon] = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, 1.0, True)
        ring: list[tuple[float, float]] = []
        for point in approx:
            pixel_x = float(point[0][0])
            pixel_y = float(point[0][1])
            longitude = info.min_longitude + (pixel_x + 0.5) * delta_longitude
            latitude = info.max_latitude - (pixel_y + 0.5) * delta_latitude
            ring.append((longitude, latitude))
        if len(ring) < 3:
            continue
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty and polygon.area > 0:
            parts.append(polygon)

    if not parts:
        return None
    merged = unary_union(parts)
    return merged if not merged.is_empty else None


def parcel_shape_points_3826(
    info: ParcelInfo,
    *,
    max_points: int = 6000,
) -> list[tuple[float, float]]:
    """由 NLSC 著色圖的 alpha 遮罩取出真實宗地的取樣點（EPSG:3826）。

    著色圖是北上正射、覆蓋 [lx, ly, rx, ry] 的 RGBA 影像，
    不透明處即為宗地實際範圍。實測 P004 文林段317 僅 30% 不透明，
    證明它是真實輪廓而非外接矩形，因此可用來精確檢核是否落在區段內。
    """

    if not info.tint_image_png or not info.has_extent:
        return []

    image = Image.open(io.BytesIO(info.tint_image_png)).convert("RGBA")
    alpha = image.getchannel("A").load()
    pixel_width, pixel_height = image.size
    if pixel_width == 0 or pixel_height == 0:
        return []

    delta_longitude = (info.max_longitude - info.min_longitude) / pixel_width
    delta_latitude = (info.max_latitude - info.min_latitude) / pixel_height

    # 取樣密度：宗地小時逐像素，大時等距抽樣，避免點數爆掉。
    step = max(1, int(math.sqrt(pixel_width * pixel_height / max_points)))

    points: list[tuple[float, float]] = []
    for pixel_y in range(0, pixel_height, step):
        for pixel_x in range(0, pixel_width, step):
            if alpha[pixel_x, pixel_y] <= 10:
                continue
            longitude = info.min_longitude + (pixel_x + 0.5) * delta_longitude
            # 影像第一列對應最大緯度。
            latitude = info.max_latitude - (pixel_y + 0.5) * delta_latitude
            points.append(_TO_3826(longitude, latitude))
    return points


def parcel_inside_ratio(
    boundary: Polygon,
    parcel_points: list[tuple[float, float]],
) -> float:
    """真實宗地取樣點落在區段內的比例。"""

    if not parcel_points:
        return 0.0
    checker = prep(boundary)
    inside = sum(1 for point in parcel_points if checker.covers(Point(point)))
    return inside / len(parcel_points)


def extend_line(line: LineString, distance: float) -> LineString:
    """沿兩端末段方向延長線段，中間頂點保留原樣。

    OSM 的巷道常只涵蓋街廓的一小段（本案啟智街14巷僅約 60 m），
    直接用原線切 ROI 切不斷，必須先延長成能貫穿 ROI 的切割線。
    """

    coords = list(line.coords)
    if len(coords) < 2:
        raise ValueError("道路幾何至少需要兩個點")

    def outward(inner: tuple[float, float], outer: tuple[float, float]):
        delta_x = outer[0] - inner[0]
        delta_y = outer[1] - inner[1]
        norm = math.hypot(delta_x, delta_y)
        if norm == 0:
            return outer
        scale = distance / norm
        return (outer[0] + delta_x * scale, outer[1] + delta_y * scale)

    start = outward(coords[1], coords[0])
    end = outward(coords[-2], coords[-1])
    return LineString([start, *coords, end])


def _as_single_line(geometry) -> LineString:
    """把可能為 MultiLineString 的道路併成單一線；無法併時取最長段。"""

    if isinstance(geometry, LineString):
        return geometry

    merged = linemerge(geometry)
    if isinstance(merged, LineString):
        return merged
    return max(merged.geoms, key=lambda part: part.length)


def _keep_side_with_parcel(
    area: Polygon,
    cutter: LineString,
    parcel: Polygon,
    anchor: Point,
) -> tuple[Polygon, bool]:
    """用切割線把區域切兩半，保留含宗地的那半。

    回傳 (保留的多邊形, 是否真的被切開)。
    """

    pieces = [
        piece
        for piece in split(area, cutter).geoms
        if isinstance(piece, Polygon) and piece.area > 1.0
    ]
    if len(pieces) < 2:
        return area, False

    overlapping = [piece for piece in pieces if piece.intersects(parcel)]
    if overlapping:
        return max(
            overlapping,
            key=lambda piece: piece.intersection(parcel).area,
        ), True

    # 宗地緊鄰切割線時可能兩側都不重疊，退回離錨點最近者。
    return min(pieces, key=lambda piece: piece.distance(anchor)), True


def _offset_away_from(
    cutter: LineString,
    reference: Point,
    distance: float,
) -> LineString:
    """把切割線往遠離參考點的方向平移。

    區段界線道路本身有路寬，緊鄰道路的宗地若以道路中心線切割，
    會被切掉臨路的一小條。外推半個路寬可讓臨路宗地完整落入區段。
    """

    if distance <= 0:
        return cutter

    candidates = []
    for signed in (distance, -distance):
        try:
            offset = cutter.offset_curve(signed)
        except Exception:
            continue
        if offset.is_empty:
            continue
        line = _as_single_line(offset)
        if isinstance(line, LineString) and not line.is_empty:
            candidates.append(line)

    if not candidates:
        return cutter
    # 取離宗地較遠者，等於把界線往外推、讓區段變大。
    return max(candidates, key=lambda line: line.distance(reference))


def _cut_with_roads(
    roi: Polygon,
    roads: dict[str, RoadFeature],
    parcel: Polygon,
    anchor: Point,
    offset_m: float,
) -> tuple[Polygon, list[str]]:
    warnings: list[str] = []
    span = math.hypot(roi.bounds[2] - roi.bounds[0], roi.bounds[3] - roi.bounds[1])
    parcel_center = parcel.centroid

    area: Polygon | MultiPolygon = roi
    for relation in RELATIONS:
        if relation not in roads:
            # OSM 缺漏該路時跳過此側，其餘條件仍可縮小範圍。
            warnings.append(f"{relation} 無道路幾何，該側未切割")
            continue
        road = roads[relation]
        cutter = extend_line(_as_single_line(road.geometry), span)
        cutter = _offset_away_from(cutter, parcel_center, offset_m)
        # 平移後長度可能不足，重新延長確保仍能貫穿。
        cutter = extend_line(_as_single_line(cutter), span)

        area, was_cut = _keep_side_with_parcel(area, cutter, parcel, anchor)
        if not was_cut:
            warnings.append(
                f"{relation}（{road.name}）未能切割範圍，該側邊界可能未生效"
            )
        if area.is_empty or area.area <= 0:
            raise ValueError(
                f"套用 {relation}（{road.name}）後範圍為空，請檢查方位條件是否矛盾"
            )

    if isinstance(area, MultiPolygon):
        area = max(area.geoms, key=lambda piece: piece.area)
    return area, warnings


NTPC_ZONING_SHP = (
    Path(__file__).parent / "新北市使用分區" / "新北市使用分區.shp"
)

_zoning_cache: dict[str, object] = {}


def load_zoning_layer(path: Path | None = None):
    """讀取新北市使用分區圖（EPSG:3826），結果快取。

    實測 34,190 筆、600 種分區，欄位為 ZONE；
    「道路用地」本身即為一種分區（8,575 筆），天然構成區段分隔。
    """

    import geopandas as gpd

    target = Path(path or os.environ.get("NTPC_ZONING_SHP") or NTPC_ZONING_SHP)
    key = str(target)
    if key not in _zoning_cache:
        if not target.exists():
            # 這份資料不在 repo 裡（179 MB，超過 GitHub 限制），
            # 錯誤訊息必須直接告訴使用者去哪拿，否則很難自行排查。
            raise ValueError(
                f"找不到使用分區圖：{target}\n"
                "  這份地理資料未隨程式碼發佈，請依 README「參考資料」下載\n"
                "  新北市使用分區 shapefile，解壓到專案根目錄的"
                " 新北市使用分區/ 資料夾，\n"
                "  或用環境變數 NTPC_ZONING_SHP 指到實際路徑。"
            )
        # 支援 GeoParquet：容器部署時用 parquet 體積約為 shapefile 的三分之一。
        if target.suffix.lower() == ".parquet":
            layer = gpd.read_parquet(target)
        else:
            layer = gpd.read_file(target)
        if layer.crs is None:
            raise ValueError("使用分區圖缺少 CRS")
        _zoning_cache[key] = layer.to_crs("EPSG:3826")
    return _zoning_cache[key]


def _normalize_zone_name(text: str) -> str:
    return re.sub(r"[（(].*?[）)]|\s+", "", str(text))


def _same_zone_cluster(
    same_zone,
    seed_index,
    point: Point,
    *,
    gap_tolerance_m: float = 1.0,
) -> tuple[Polygon, int]:
    """把與種子相連、且分區相同的多邊形全部併起來。

    使用分區圖會把同一個街廓的同分區切成多筆（實測 P002 樹德段
    被切成 2 筆：6,760 + 700 m²），只取包含宗地的那一筆會漏掉
    住宅區的一部分。相鄰同分區之間沒有道路用地分隔，本來就屬於
    同一個地價區段，因此以連通分量合併。
    """

    spatial_index = same_zone.sindex
    cluster = {seed_index}
    frontier = [seed_index]
    while frontier:
        following: list = []
        for index in frontier:
            probe = same_zone.geometry.loc[index].buffer(gap_tolerance_m)
            for position in spatial_index.query(probe):
                candidate = same_zone.index[position]
                if candidate in cluster:
                    continue
                if same_zone.geometry.loc[candidate].intersects(probe):
                    cluster.add(candidate)
                    following.append(candidate)
        frontier = following

    merged = unary_union(list(same_zone.geometry.loc[list(cluster)]))
    if not merged.is_valid:
        merged = merged.buffer(0)
    if isinstance(merged, MultiPolygon):
        parts = list(merged.geoms)
        holding = [part for part in parts if part.covers(point)]
        merged = (holding or parts)[0] if holding else max(parts, key=lambda p: p.area)
    return merged, len(cluster)


def extract_zoning_block(
    longitude: float,
    latitude: float,
    target_zone: str,
    *,
    shapefile: Path | None = None,
    search_radius_m: float = 60.0,
    merge_same_zone: bool = True,
) -> tuple[Polygon, str, list[str]]:
    """取出「包含宗地且分區相符」的使用分區多邊形作為區段範圍。

    這是最直接的定義：地價區段即為使用分區圖上的一個街廓單元，
    分隔物就是圖上的道路用地，不需要從光柵猜道路、也不依賴 OSM。
    實測 P004 以此得 4,283 m²，與人工確認的 4,302 m² 僅差 0.4%。
    """

    layer = load_zoning_layer(shapefile)
    notes: list[str] = []
    point = Point(*_TO_3826(longitude, latitude))
    wanted = _normalize_zone_name(target_zone)

    zones = layer["ZONE"].astype(str).map(_normalize_zone_name)
    containing = layer[layer.geometry.contains(point)]

    match = containing[
        containing.index.map(lambda i: zones.loc[i] == wanted)
    ]
    if len(match) == 0 and len(containing) > 0:
        actual = "、".join(sorted(set(containing["ZONE"].astype(str))))
        notes.append(
            f"宗地實際落在「{actual}」而非指定的「{target_zone}」"
        )

    if len(match) == 0:
        # 宗地可能位於道路用地內（實測 P003 樹德段1415 即是），
        # 改取鄰近且分區相符者。
        nearby = layer[layer.geometry.distance(point) <= search_radius_m]
        nearby = nearby[nearby.index.map(lambda i: zones.loc[i] == wanted)]
        if len(nearby) == 0:
            raise ValueError(
                f"{search_radius_m:.0f} m 內找不到分區「{target_zone}」的多邊形"
            )
        nearby = nearby.assign(_d=nearby.geometry.distance(point))
        match = nearby.sort_values("_d").head(1)
        notes.append(
            f"宗地未落在「{target_zone}」內，改取最近者"
            f"（{float(match['_d'].iloc[0]):.1f} m）"
        )

    row = match.iloc[0]
    geometry = row.geometry
    if isinstance(geometry, MultiPolygon):
        geometry = max(geometry.geoms, key=lambda part: part.area)
    if not geometry.is_valid:
        geometry = geometry.buffer(0)

    if merge_same_zone:
        same_zone = layer[layer.index.map(lambda i: zones.loc[i] == wanted)]
        merged, count = _same_zone_cluster(same_zone, match.index[0], point)
        if count > 1 and merged.area > geometry.area:
            notes.append(
                f"同分區相鄰多邊形已合併（{count} 筆，"
                f"{geometry.area:,.0f} → {merged.area:,.0f} m²）"
            )
            geometry = merged

    return geometry, str(row["ZONE"]), notes


def road_cut_seed(
    roi: Polygon,
    roads: dict[str, RoadFeature],
    parcel: Polygon,
    anchor: Point,
) -> tuple[float, float] | None:
    """用可用的方位條件切出區域，回傳其代表點（WGS84）作為街廓種子。

    只要有一條界線道路就能縮小範圍；四條齊全時等同完整的道路切割。
    """

    try:
        area, _warnings = _cut_with_roads(roi, roads, parcel, anchor, 0.0)
    except ValueError:
        return None
    if isinstance(area, MultiPolygon):
        area = max(area.geoms, key=lambda part: part.area)
    if area.is_empty or area.area <= 0:
        return None
    point = area.representative_point()
    longitude, latitude = _TO_4326(point.x, point.y)
    return longitude, latitude


def road_bounded_polygon(
    roi: Polygon,
    roads: dict[str, RoadFeature],
    parcel: Polygon,
    anchor: Point,
    *,
    road_offset_m: float | None = None,
    max_offset_m: float = 12.0,
    required_inside_ratio: float = 0.999,
    parcel_points: list[tuple[float, float]] | None = None,
) -> tuple[Polygon, float, list[str]]:
    """以四條界線道路逐次切割 ROI，得到包含宗地的街廓範圍。

    不使用緩衝帶切格：本案四條道路長度僅 60～260 m，
    無法切穿 600 m 的 ROI，緩衝帶差集只會得到整個 ROI。

    road_offset_m 為 None 時自動由 0 起試，找出能讓宗地完整落入
    區段的最小外推量（上限 max_offset_m），以滿足「宗地必須在區段內」。
    回傳 (區段多邊形, 實際採用的外推量, 警告)。
    """

    if road_offset_m is not None:
        area, warnings = _cut_with_roads(roi, roads, parcel, anchor, road_offset_m)
        return area, road_offset_m, warnings

    def ratio_of(area: Polygon) -> float:
        # 有真實輪廓取樣點時以其為準；否則退回外接矩形面積比。
        if parcel_points:
            return parcel_inside_ratio(area, parcel_points)
        return area.intersection(parcel).area / parcel.area if parcel.area else 0.0

    best: tuple[Polygon, float, list[str]] | None = None
    best_ratio = -1.0
    for offset in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, max_offset_m):
        if offset > max_offset_m:
            break
        area, warnings = _cut_with_roads(roi, roads, parcel, anchor, offset)
        ratio = ratio_of(area)
        if ratio >= required_inside_ratio:
            return area, offset, warnings
        # 保留目前最好的結果，避免全部都不足時無可回傳。
        if ratio > best_ratio:
            best = (area, offset, warnings)
            best_ratio = ratio

    assert best is not None
    return best


def audit_directions(
    boundary: Polygon,
    roads: dict[str, RoadFeature],
) -> dict[str, bool]:
    """檢核區段相對各界線道路的方位是否符合條件。

    以區段形心（而非 representative_point）比對道路最近點，
    形心對凸形街廓較穩定。
    """

    centroid = boundary.centroid
    result: dict[str, bool] = {}
    for relation, road in roads.items():
        _, nearest = nearest_points(centroid, road.geometry)
        if relation == "north_of":
            result[relation] = centroid.y > nearest.y
        elif relation == "south_of":
            result[relation] = centroid.y < nearest.y
        elif relation == "east_of":
            result[relation] = centroid.x > nearest.x
        else:
            result[relation] = centroid.x < nearest.x
    return result



def _measure_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    line_spacing: int,
) -> tuple[int, int, list[int]]:
    """量測多行文字的整體寬高與每行高度。"""

    widths: list[int] = []
    heights: list[int] = []
    for line in lines:
        box_ = draw.textbbox((0, 0), line, font=font)
        widths.append(box_[2] - box_[0])
        heights.append(box_[3] - box_[1])
    total_height = sum(heights) + line_spacing * (len(lines) - 1)
    return max(widths), total_height, heights


def _find_clear_spot(
    canvas_width: int,
    canvas_height: int,
    box_width: int,
    box_height: int,
    keep_clear: Polygon,
    occupied: list[Polygon],
    *,
    prefer: Point | None = None,
    margin: int = 14,
    step: int = 10,
) -> tuple[int, int] | None:
    """找一個不覆蓋 keep_clear（區段內部）且不與既有標註重疊的位置。

    prefer 給定時取離該點最近的可用位置，讓引線盡量短。
    """

    best: tuple[float, int, int] | None = None
    max_x = canvas_width - box_width - margin
    max_y = canvas_height - box_height - margin
    if max_x < margin or max_y < margin:
        return None

    for y in range(margin, max_y + 1, step):
        for x in range(margin, max_x + 1, step):
            rect = box(x, y, x + box_width, y + box_height)
            if rect.intersects(keep_clear):
                continue
            if any(rect.intersects(other) for other in occupied):
                continue
            score = (
                rect.centroid.distance(prefer)
                if prefer is not None
                else rect.distance(keep_clear)
            )
            if best is None or score < best[0]:
                best = (score, x, y)

    if best is None:
        return None
    return best[1], best[2]


def _draw_label_box(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    lines: list[str],
    font,
    *,
    padding: int = 9,
    line_spacing: int = 7,
    border_width: int = 2,
) -> Polygon:
    """畫出紅框白底紅字的標註框，回傳其螢幕範圍。"""

    x, y = position
    text_width, text_height, heights = _measure_lines(
        draw, lines, font, line_spacing
    )
    rect = (
        x,
        y,
        x + text_width + padding * 2,
        y + text_height + padding * 2,
    )
    draw.rectangle(
        rect,
        fill=ANNOTATION_FILL,
        outline=ANNOTATION_BORDER,
        width=border_width,
    )

    cursor_y = y + padding
    for line, line_height in zip(lines, heights):
        line_box = draw.textbbox((0, 0), line, font=font)
        draw.text(
            (x + padding - line_box[0], cursor_y - line_box[1]),
            line,
            font=font,
            fill=ANNOTATION_TEXT,
        )
        cursor_y += line_height + line_spacing

    return box(*rect)


def is_road_segment(name: str) -> bool:
    """排除非路段的具名線：步道、自行車道、橋樑、匝道等。

    OSM 會把 highway=footway／cycleway 也標上名稱，
    但「各路段」指的是道路，這些不應出現在路段大字中。
    """

    excluded = (
        "步道",
        "自行車道",
        "人行道",
        "橋",
        "匝道",
        "高架",
        "隧道",
        "圳",
        "階梯",
    )
    return not any(token in name for token in excluded)


def _relation_side_ok(
    relation: str,
    point: tuple[float, float],
    center: tuple[float, float],
) -> bool:
    """判斷候選位置是否落在該方位條件所指的那一側。

    方位語意是「區段位於該路的哪一側」，因此道路本身在相反側：
        north_of X → 區段在 X 之北 → X 在區段之南
        south_of X → X 在區段之北
        east_of  X → X 在區段之西
        west_of  X → X 在區段之東
    螢幕座標 y 向下遞增，故「南」為 y 較大。
    """

    point_x, point_y = point
    center_x, center_y = center
    if relation == "north_of":
        return point_y > center_y
    if relation == "south_of":
        return point_y < center_y
    if relation == "east_of":
        return point_x < center_x
    if relation == "west_of":
        return point_x > center_x
    return True


def _boundary_side_candidates(
    boundary_screen: Polygon,
    relation: str,
    *,
    outward_px: float = 26.0,
    min_segment_px: float = 15.0,
) -> list[tuple[float, float, float, float]]:
    """由區段邊界取出指定方位那一側的標註候選位置。

    界線道路的文字位置應由「區段邊界的哪一側」決定，而不是跟著
    該路的 OSM 幾何跑。這樣同一條路兼任兩個方位時，
    north_of 的文字放南側、east_of 的文字放西側，
    也不受 OSM 缺少某段幾何的影響（潭興街107巷21弄 實際為 L 形，
    OSM 只收錄西北段）。

    文字沿邊界方向旋轉，並向外偏移 outward_px 以免蓋住邊界線。
    """

    ring = list(boundary_screen.exterior.coords)
    centroid = boundary_screen.centroid
    center = (centroid.x, centroid.y)

    found: list[tuple[float, float, float, float]] = []
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)
        if length < min_segment_px:
            continue

        angle = math.degrees(math.atan2(-delta_y, delta_x))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180

        for ratio in (0.5, 0.35, 0.65):
            point_x = x1 + delta_x * ratio
            point_y = y1 + delta_y * ratio
            if not _relation_side_ok(relation, (point_x, point_y), center):
                continue
            # 由形心往外推，讓文字落在邊界外側。
            vector_x = point_x - center[0]
            vector_y = point_y - center[1]
            norm = math.hypot(vector_x, vector_y) or 1.0
            found.append(
                (
                    point_x + vector_x / norm * outward_px,
                    point_y + vector_y / norm * outward_px,
                    angle,
                    length,
                )
            )

    found.sort(key=lambda item: -item[3])
    return found


def _boundary_edge_candidates(
    boundary_screen: Polygon,
    *,
    relation: str,
    road_line: LineString | None,
    outward_px: float = 26.0,
    min_segment_px: float = 15.0,
) -> list[tuple[float, float, float, float]]:
    """列出可放界線道路文字的邊界線段候選。

    排序原則：
    1. 有該路的實際幾何時，取「離該路最近」的邊界線段。
       這是主要依據——旋轉約 45° 的街廓，一條邊會同時滿足「北」與「西」，
       只靠方位判斷會把文字放到錯誤的邊（實測 P002 的長壽街21巷
       會被放到北邊而非東邊）。
    2. 沒有幾何時（OSM 缺漏或限流），退回方位語意判斷。

    文字沿邊界方向旋轉，並由形心往外推，避免壓住邊界線。
    """

    ring = list(boundary_screen.exterior.coords)
    centroid = boundary_screen.centroid
    center = (centroid.x, centroid.y)

    found: list[tuple[float, float, float, float, float]] = []
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)
        if length < min_segment_px:
            continue

        angle = math.degrees(math.atan2(-delta_y, delta_x))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180

        segment = LineString([(x1, y1), (x2, y2)])
        if road_line is not None:
            score = segment.distance(road_line)
        else:
            mid = ((x1 + x2) / 2, (y1 + y2) / 2)
            side_ok = _relation_side_ok(relation, mid, center)
            # 無幾何時：先看方位是否相符，再取較長線段。
            score = (0.0 if side_ok else 1e6) - length

        for ratio in (0.5, 0.35, 0.65):
            point_x = x1 + delta_x * ratio
            point_y = y1 + delta_y * ratio
            vector_x = point_x - center[0]
            vector_y = point_y - center[1]
            norm = math.hypot(vector_x, vector_y) or 1.0
            found.append(
                (
                    score,
                    point_x + vector_x / norm * outward_px,
                    point_y + vector_y / norm * outward_px,
                    angle,
                    length,
                )
            )

    found.sort(key=lambda item: item[0])
    return [(x, y, angle, length) for _score, x, y, angle, length in found]


# 各方位條件所指的「道路相對區段的方向」，螢幕座標 y 向下為南。
_RELATION_TARGET = {
    "north_of": (0.0, 1.0),   # 區段在該路之北 → 該路在南
    "south_of": (0.0, -1.0),  # 該路在北
    "east_of": (-1.0, 0.0),   # 區段在該路之東 → 該路在西
    "west_of": (1.0, 0.0),    # 該路在東
}


def _boundary_sides(
    boundary_screen: Polygon,
    *,
    angle_tolerance_deg: float = 28.0,
    min_length_px: float = 25.0,
    simplify_px: float = 10.0,
) -> list[tuple[float, float, float, float, tuple[float, float]]]:
    """把邊界外環合併成幾條主要邊。

    邊界多邊形常被切成很多小線段（P004 有 22 個頂點），
    直接取單一線段會選到很短的碎邊，因此先把方向接近的連續線段合併，
    讓每一「側」成為一條完整的邊。

    回傳 [(中點x, 中點y, 角度, 長度, 外法向量)]。
    """

    # 使用分區圖的多邊形非常精細（實測 P004 有 388 個頂點），
    # 直接逐段合併會因為細碎鋸齒而累積不到最小長度，導致取不到任何邊。
    # 先化簡到只留主要轉折，才能得到「每一側一條邊」。
    simplified = boundary_screen.simplify(simplify_px, preserve_topology=True)
    if simplified.is_empty or not isinstance(simplified, Polygon):
        simplified = boundary_screen

    ring = list(simplified.exterior.coords)
    if len(ring) < 4:
        return []

    centroid = boundary_screen.centroid
    segments = list(zip(ring, ring[1:]))

    merged: list[list[tuple[tuple[float, float], tuple[float, float]]]] = []
    for segment in segments:
        (x1, y1), (x2, y2) = segment
        if math.hypot(x2 - x1, y2 - y1) <= 0:
            continue
        if not merged:
            merged.append([segment])
            continue

        previous = merged[-1][-1]
        (px1, py1), (px2, py2) = previous
        previous_angle = math.degrees(math.atan2(py2 - py1, px2 - px1))
        current_angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        difference = abs((current_angle - previous_angle + 180) % 360 - 180)
        if difference <= angle_tolerance_deg:
            merged[-1].append(segment)
        else:
            merged.append([segment])

    sides: list[tuple[float, float, float, float, tuple[float, float]]] = []
    for group in merged:
        start = group[0][0]
        end = group[-1][1]
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length < min_length_px:
            continue

        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2

        # 外法向量：取垂直於該邊的方向，再用形心決定朝外的正負號。
        # 不可用「形心→邊中點」的放射向量：分區多邊形有凹口時，
        # 相鄰兩邊的放射向量幾乎相同（實測 P002 東邊與東北斜邊都是
        # (0.93, ±0.38)），分不出東與北，長壽街21巷會被標到東北斜邊。
        radial_x = mid_x - centroid.x
        radial_y = mid_y - centroid.y
        normal_x, normal_y = delta_y, -delta_x
        if normal_x * radial_x + normal_y * radial_y < 0:
            normal_x, normal_y = -delta_y, delta_x
        norm = math.hypot(normal_x, normal_y) or 1.0
        normal = (normal_x / norm, normal_y / norm)

        angle = math.degrees(math.atan2(-delta_y, delta_x))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180

        sides.append((mid_x, mid_y, angle, length, normal))

    sides.sort(key=lambda item: -item[3])
    return sides


def _assign_relations_to_sides(
    sides: list[tuple[float, float, float, float, tuple[float, float]]],
    relations: list[str],
    *,
    max_sides: int = 8,
) -> dict[str, int]:
    """把每個方位一對一指派到一條邊界邊，取總吻合度最大的組合。

    只用「取吻合度最高的邊」會出錯：旋轉約 45° 的街廓有兩條邊同時
    朝南，貪心挑選會讓兩個方位搶到同一條邊或選錯邊（實測 P002 因此
    把長壽街21巷 標到北邊）。改以一對一指派並在所有組合中取最大總分。
    """

    if not sides or not relations:
        return {}

    candidates = sides[:max_sides]
    scores: dict[str, list[float]] = {}
    for relation in relations:
        target = _RELATION_TARGET.get(relation)
        row: list[float] = []
        for _mid_x, _mid_y, _angle, length, normal in candidates:
            if target is None:
                row.append(0.0)
                continue
            dot = normal[0] * target[0] + normal[1] * target[1]
            # 方向吻合度為主，邊長為輔（避免選到短碎邊）。
            row.append(dot * math.sqrt(length))
        scores[relation] = row

    # 邊數少於方位數時無法一對一（小街廓合併後可能只有 3 條邊）。
    # 改為貪心指派並盡量避免重複用同一條邊：同一條路兼任兩方位時
    # 若兩者搶到同一條邊，兩個標註會疊在一起看起來只有一個。
    if len(candidates) < len(relations):
        assignment: dict[str, int] = {}
        used: set[int] = set()
        order = sorted(
            relations,
            key=lambda relation: -max(scores[relation]),
        )
        for relation in order:
            available = [i for i in range(len(candidates)) if i not in used]
            pool = available or list(range(len(candidates)))
            index = max(pool, key=lambda i: scores[relation][i])
            assignment[relation] = index
            used.add(index)
        return assignment

    best_total = None
    best_assignment: dict[str, int] = {}
    for combination in itertools.permutations(
        range(len(candidates)), len(relations)
    ):
        total = sum(
            scores[relation][index]
            for relation, index in zip(relations, combination)
        )
        if best_total is None or total > best_total:
            best_total = total
            best_assignment = dict(zip(relations, combination))

    return best_assignment


def _draw_road_labels(
    canvas: Image.Image,
    roads: list[tuple[str, list[tuple[float, float]]]],
    zoom: int,
    left: float,
    top: float,
    width: int,
    height: int,
    *,
    font_size: int,
    max_labels: int,
    include_alleys: bool,
    required_relations: dict[str, str] | None = None,
    boundary_screen: Polygon | None = None,
    road_lines_screen: dict[str, LineString] | None = None,
    protected: list[Polygon] | None = None,
) -> tuple[Image.Image, list[str], list[Polygon], list[str]]:
    """沿路方向以藍色大字標註路名。

    required_relations 是 {方位: 路名}，為 JSON 指定的界線道路：
    - 一定要標出文字，即使是巷弄、即使所有位置都被佔用（容許重疊）
    - 逐「方位」標註，因此同一條路若同時作為兩個方位的界線
      （例如 L 形的潭興街107巷21弄 兼任 north_of 與 east_of），
      會在兩側各標一次
    - 文字放在該方位語意所指的那一側，而非單純取最長路段

    回傳 (畫布, 已標路名, 佔用範圍, 未能標註的方位)。
    """

    required_relations = required_relations or {}
    required = {name for name in required_relations.values() if name}
    font = _load_font(font_size)

    candidates: dict[str, list[tuple[float, float, float, float]]] = {}
    for name, points in roads:
        if name not in required:
            if not include_alleys and is_alley(name):
                continue
            if not is_road_segment(name):
                continue
        screen_points = []
        for longitude, latitude in points:
            point_x, point_y = lonlat_to_pixel(longitude, latitude, zoom)
            screen_points.append((point_x - left, point_y - top))
        found = _road_label_candidates(screen_points, width, height)
        if found:
            candidates.setdefault(name, []).extend(found)

    for name in candidates:
        candidates[name].sort(key=lambda item: -item[3])

    placed: list[str] = []
    # 受保護區域（宗地填色）先放進 occupied，任何文字都不得覆蓋。
    occupied: list[Polygon] = list(protected or [])
    protected_count = len(occupied)

    # 同名道路若被指派到多個方位，兩次標註必須分開，否則會疊在同一處。
    placed_points: dict[str, list[tuple[float, float]]] = {}
    same_name_gap_px = 90.0

    def try_place(
        name: str,
        ordered: list[tuple[float, float, float, float]],
        *,
        force: bool,
    ) -> bool:
        nonlocal canvas
        previous = placed_points.get(name, [])
        fallback: tuple[Image.Image, float, float, Polygon] | None = None
        for point_x, point_y, angle, _length in ordered:
            # 與同名既有標註距離太近就跳過，讓兩側各自看得到文字。
            if any(
                math.dist((point_x, point_y), earlier) < same_name_gap_px
                for earlier in previous
            ):
                continue
            label = _render_rotated_label(
                name,
                font,
                angle,
                fill=ROAD_LABEL_COLOR,
                halo=ROAD_LABEL_HALO,
            )
            paste_x = point_x - label.width / 2
            paste_y = point_y - label.height / 2
            rect = box(
                paste_x,
                paste_y,
                paste_x + label.width,
                paste_y + label.height,
            )
            # 必須完整落在畫面內，否則文字會被裁掉。
            margin = 6
            if rect.bounds[0] < margin or rect.bounds[1] < margin:
                continue
            if rect.bounds[2] > width - margin or rect.bounds[3] > height - margin:
                continue

            if fallback is None:
                fallback = (label, paste_x, paste_y, rect)
            if any(rect.intersects(other) for other in occupied):
                continue

            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            layer.paste(label, (int(round(paste_x)), int(round(paste_y))), label)
            canvas = Image.alpha_composite(canvas, layer)
            occupied.append(rect)
            placed.append(name)
            placed_points.setdefault(name, []).append((point_x, point_y))
            return True

        # 界線道路一定要有文字：所有位置都被佔用時容許重疊。
        if force and fallback is not None:
            label, paste_x, paste_y, rect = fallback
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            layer.paste(label, (int(round(paste_x)), int(round(paste_y))), label)
            canvas = Image.alpha_composite(canvas, layer)
            occupied.append(rect)
            placed.append(name)
            placed_points.setdefault(name, []).append(
                (paste_x + label.width / 2, paste_y + label.height / 2)
            )
            return True
        return False

    # 一、界線道路逐方位標註，文字放在該方位所指的那一側。
    missing_relations: list[str] = []
    side_gaps: list[str] = []
    if boundary_screen is not None:
        centroid = boundary_screen.centroid
        center = (centroid.x, centroid.y)
        edge = boundary_screen.exterior
    else:
        center = (width / 2, height / 2)
        edge = None

    # 先把四個方位一對一指派到邊界的四條邊。
    side_list = (
        _boundary_sides(boundary_screen) if boundary_screen is not None else []
    )
    wanted = [r for r in RELATIONS if required_relations.get(r)]
    assignment = _assign_relations_to_sides(side_list, wanted)

    for relation in RELATIONS:
        name = required_relations.get(relation)
        if not name:
            continue
        # 界線道路的位置由區段邊界決定，只需要路名；
        # 因此 Overpass 取不到幾何（限流或缺漏）時仍必須標出文字。
        if name not in candidates and boundary_screen is None:
            missing_relations.append(f"{relation}（{name}）")
            continue

        def sort_key(item, relation=relation):
            point = (item[0], item[1])
            side_ok = _relation_side_ok(relation, point, center)
            distance = (
                edge.distance(Point(point)) if edge is not None else 0.0
            )
            return (not side_ok, distance)

        # 文字要落在該道路本身，且取離區段邊界最近的一段，
        # 這樣看圖時文字與路線對得起來（純用邊界線段會讓文字脫離實際路段）。
        # 位置完全由「方位 → 邊界邊」的指派決定，不依賴 OSM 幾何。
        ordered: list[tuple[float, float, float, float]] = []
        index = assignment.get(relation)
        if index is not None and index < len(side_list):
            mid_x, mid_y, angle, length, normal = side_list[index]
            for offset in (30.0, 44.0, 18.0, 58.0):
                ordered.append(
                    (
                        mid_x + normal[0] * offset,
                        mid_y + normal[1] * offset,
                        angle,
                        length,
                    )
                )
            # 同一條邊上再取幾個位置，供同名標註分開時使用。
            for ratio in (0.3, 0.7):
                shift = (ratio - 0.5) * length
                direction = math.radians(-angle)
                ordered.append(
                    (
                        mid_x + math.cos(direction) * shift + normal[0] * 30.0,
                        mid_y + math.sin(direction) * shift + normal[1] * 30.0,
                        angle,
                        length,
                    )
                )
        if not ordered and name in candidates:
            ordered = sorted(candidates[name], key=sort_key)
            side_gaps.append(f"{relation}（{name}）")

        # 最終保證：仍無位置時，由區段形心朝該方位所指方向推出一個點。
        # 界線道路必須有文字，不能因為取不到邊或沒有 OSM 幾何而消失。
        if not ordered and boundary_screen is not None:
            target = _RELATION_TARGET.get(relation, (0.0, 1.0))
            minx, miny, maxx, maxy = boundary_screen.bounds
            reach_x = (maxx - minx) / 2 + 34
            reach_y = (maxy - miny) / 2 + 34
            centre = boundary_screen.centroid
            ordered = [
                (
                    centre.x + target[0] * reach_x,
                    centre.y + target[1] * reach_y,
                    0.0 if abs(target[1]) > abs(target[0]) else 90.0,
                    100.0,
                )
            ]
            side_gaps.append(f"{relation}（{name}）")

        if not try_place(name, ordered, force=True):
            missing_relations.append(f"{relation}（{name}）")

    # 二、其餘道路依線段長度補上，受 max_labels 限制。
    others = sorted(
        (name for name in candidates if name not in required),
        key=lambda name: (is_alley(name), -candidates[name][0][3]),
    )
    for name in others:
        if len(placed) >= max_labels:
            break
        try_place(name, candidates[name], force=False)

    for item in side_gaps:
        missing_relations.append(f"[方位側無該路幾何] {item}")

    return canvas, placed, occupied[protected_count:], missing_relations


def restyle_layer(
    image: Image.Image,
    color: tuple[int, int, int] | None,
    opacity: float,
) -> Image.Image:
    """改變圖層線條顏色與透明度。

    地籍圖（DMAPS）圖磚實測：90% 完全透明，所有可見像素都是單一洋紅
    RGB(255, 0, 255)，僅 alpha 變化（反鋸齒）。因此整片換色不會誤傷其他內容，
    降低不透明度也能讓綠色區段線與紅色宗地更突出。
    """

    if color is None and opacity >= 1.0:
        return image

    red, green, blue, alpha = image.split()
    if color is not None:
        solid = Image.new("RGB", image.size, color)
        red, green, blue = solid.split()
    if opacity < 1.0:
        scale = max(0.0, min(1.0, opacity))
        alpha = alpha.point(lambda value: int(value * scale))
    return Image.merge("RGBA", (red, green, blue, alpha))


_NAMED_COLORS = {
    "black": (0, 0, 0),
    "gray": (90, 90, 90),
    "grey": (90, 90, 90),
    "dimgray": (60, 60, 60),
    "magenta": (255, 0, 255),
    "blue": (0, 60, 200),
}


def parse_color(text: str | None) -> tuple[int, int, int] | None:
    """接受 none／色名／#RRGGBB。"""

    if text is None:
        return None
    value = text.strip().lower()
    if value in ("", "none", "original", "keep"):
        return None
    if value in _NAMED_COLORS:
        return _NAMED_COLORS[value]
    if value.startswith("#") and len(value) == 7:
        return (
            int(value[1:3], 16),
            int(value[3:5], 16),
            int(value[5:7], 16),
        )
    raise ValueError(
        f"無法解析顏色 {text!r}；可用 none、"
        f"{'／'.join(sorted(_NAMED_COLORS))} 或 #RRGGBB"
    )


def _fit_zoom(
    min_longitude: float,
    min_latitude: float,
    max_longitude: float,
    max_latitude: float,
    width: int,
    height: int,
) -> int:
    for zoom in range(MAX_SAFE_ZOOM, MIN_ZOOM - 1, -1):
        left, top = lonlat_to_pixel(min_longitude, max_latitude, zoom)
        right, bottom = lonlat_to_pixel(max_longitude, min_latitude, zoom)
        if abs(right - left) <= width and abs(bottom - top) <= height:
            return zoom
    return MIN_ZOOM


def _road_lines_screen(
    roads: dict[str, RoadFeature],
    zoom: int,
    left: float,
    top: float,
) -> dict[str, LineString]:
    """把各方位界線道路的幾何轉為螢幕座標，用於比對最近的邊界線段。"""

    result: dict[str, LineString] = {}
    for relation, feature in roads.items():
        geometry = shapely_transform(_TO_4326, feature.geometry)
        lines = (
            [geometry]
            if isinstance(geometry, LineString)
            else list(getattr(geometry, "geoms", []))
        )
        points: list[tuple[float, float]] = []
        for line in lines:
            for longitude, latitude in line.coords:
                point_x, point_y = lonlat_to_pixel(longitude, latitude, zoom)
                points.append((point_x - left, point_y - top))
        if len(points) >= 2:
            result[relation] = LineString(points)
    return result


def _ring_to_screen(
    ring: Iterable[tuple[float, float]],
    zoom: int,
    left: float,
    top: float,
) -> list[tuple[float, float]]:
    points = []
    for longitude, latitude in ring:
        point_x, point_y = lonlat_to_pixel(longitude, latitude, zoom)
        points.append((point_x - left, point_y - top))
    return points


def render_zone_boundary(
    input_path: str | Path,
    output_path: str | Path = "zone_boundary.png",
    *,
    county: CountyCode | str = "新北市",
    base_map: BaseMap | str = BaseMap.EMAP_NO_HOUSE_NUMBER,
    overlays: Iterable[ExtraLayer | str] = DEFAULT_OVERLAYS,
    label_overlay: str | None = DEFAULT_LABEL_OVERLAY,
    width: int = 1400,
    height: int = 1000,
    roi_size_m: float = 600.0,
    road_offset_m: float | None = None,
    padding_ratio: float = 0.45,
    boundary_line_width: int = 3,
    boundary_source: str = "zoning",
    zoom_override: int | None = None,
    zone_code: str | None = None,
    district: str | None = None,
    cadastral_color: tuple[int, int, int] | None = (0, 0, 0),
    cadastral_opacity: float = 0.6,
    annotate_roads: bool = True,
    road_font_size: int = 30,
    max_road_labels: int = 18,
    include_alleys: bool = False,
) -> BoundaryResult:
    cases = load_cases(input_path)
    case = cases[0]
    names, warnings = boundary_road_names(case)

    # 同一張圖只能有一組界線道路；條件不一致代表是不同區段，必須分開出圖。
    for index, other in enumerate(cases[1:], start=2):
        other_names, _ = boundary_road_names(other)
        if other_names != names:
            raise ValueError(
                f"第 1 筆與第 {index} 筆的界線道路條件不同，"
                "屬於不同區段，請分成兩個 JSON 各自出圖"
            )
        if other["constraints"]["zone"] != case["constraints"]["zone"]:
            raise ValueError(f"第 1 筆與第 {index} 筆的分區不同，無法畫成同一區段")

    parcels = [
        LandParcel.parse(str(item["section"]["code"]), str(item["parcel"]))
        for item in cases
    ]

    # 以 NLSC 實際回傳的宗地範圍為準，而非輸入座標。
    infos = verify_parcels(county, parcels, fetch_images=True)
    missing = [
        item.code for item, found in zip(parcels, infos) if not found.exists
    ]
    if missing:
        raise ValueError(
            f"NLSC 查無地號 {'、'.join(missing)}；請確認縣市、段代碼與地號"
        )
    info = infos[0]
    parcel = parcels[0]
    if len(cases) > 1:
        warnings.append(
            f"本區段共 {len(cases)} 筆比準地："
            + "、".join(
                f"{item['section']['name']}{item['parcel']}" for item in cases
            )
        )

    # 錨點取所有宗地的共同中心，讓視野與 OSM 查詢同時涵蓋每一筆。
    parcel_geoms = [parcel_polygon_3826(item) for item in infos]
    parcel_geom = unary_union(parcel_geoms)
    anchor = parcel_geom.centroid
    anchor_longitude, anchor_latitude = _TO_4326(anchor.x, anchor.y)

    # cadastral 模式的邊界來自地籍萃取、界線文字來自 JSON 路名，
    # OSM 僅用於方位檢核與補充其他路名，因此取不到時不應中斷出圖。
    roads: dict[str, RoadFeature] = {}
    setattr(resolve_boundary_roads, "last_missing", [])
    try:
        roads = resolve_boundary_roads(
            anchor_latitude,
            anchor_longitude,
            names,
            require_all=False,
        )
    except (RuntimeError, requests.RequestException) as exc:
        warnings.append(
            f"OSM 道路查詢失敗（{exc}）；界線文字改由指定路名與區段邊界定位，"
            "方位檢核與其他路名標註本次略過"
        )

    missing_roads = [
        relation for relation in RELATIONS if relation not in roads
    ]
    if missing_roads:
        detail = "、".join(
            f"{relation}（{names[relation]}）" for relation in missing_roads
        )
        warnings.append(
            f"OSM 查無下列界線道路：{detail}；"
            "已改用地籍萃取取得邊界，文字仍依指定路名與方位標註"
        )

    roi = box(
        anchor.x - roi_size_m / 2,
        anchor.y - roi_size_m / 2,
        anchor.x + roi_size_m / 2,
        anchor.y + roi_size_m / 2,
    )
    parcel_points: list[tuple[float, float]] = []
    for item in infos:
        parcel_points.extend(parcel_shape_points_3826(item))
    if not parcel_points:
        warnings.append("無宗地著色圖，改以外接矩形檢核是否落在區段內")

    if boundary_source in ("zoning", "auto"):
        try:
            # 多筆比準地各自查所屬分區多邊形再聯集：可能落在不同多邊形，
            # 只用第一筆的中心會漏掉另一筆。
            zone_parts: list[Polygon] = []
            matched_zones: list[str] = []
            zone_notes: list[str] = []
            for item in infos:
                item_longitude, item_latitude = item.center
                part, matched, notes = extract_zoning_block(
                    item_longitude,
                    item_latitude,
                    case["constraints"]["zone"],
                )
                if not any(part.equals(existing) for existing in zone_parts):
                    zone_parts.append(part)
                    matched_zones.append(matched)
                zone_notes.extend(notes)
            zone_geometry = unary_union(zone_parts)
            if isinstance(zone_geometry, MultiPolygon):
                warnings.append(
                    f"{len(zone_parts)} 筆比準地分屬不同分區多邊形且不相連，"
                    "區段範圍取其聯集（畫面會有多塊）"
                )
        except (ValueError, ImportError) as exc:
            if boundary_source == "zoning":
                raise ValueError(f"使用分區圖萃取失敗：{exc}") from exc
            warnings.append(f"使用分區圖萃取失敗（{exc}），改用其他來源")
        else:
            boundary_3826 = zone_geometry
            applied_offset = 0.0
            block_info = None
            matched_zone = "、".join(dict.fromkeys(matched_zones))
            warnings.extend(dict.fromkeys(zone_notes))
            warnings.append(
                f"區段範圍取自使用分區圖：{matched_zone}"
                f"（{zone_geometry.area:,.0f} m²）"
            )
            boundary_source = "zoning"

    # auto：先試道路切割，方位檢核全數通過且面積合理才採用；
    # 否則改用地籍萃取。P002 的四條界線道路能圍成封閉區域，道路切割正確；
    # P004 因 潭興街107巷21弄 兼任兩方位且 OSM 缺 L 形轉折，
    # 道路切割會得到 45,152 m² 的錯誤範圍，必須改用地籍萃取。
    if boundary_source == "auto" and not missing_roads and roads:
        try:
            trial, trial_offset, trial_warnings = road_bounded_polygon(
                roi,
                roads,
                parcel_geom,
                anchor,
                road_offset_m=road_offset_m,
                parcel_points=parcel_points,
            )
        except ValueError:
            trial = None

        if trial is not None:
            trial_audit = audit_directions(trial, roads)
            plausible = trial.area <= 40_000.0
            if all(trial_audit.values()) and plausible:
                boundary_source = "roads"
            else:
                failed = [r for r, ok in trial_audit.items() if not ok]
                reason = (
                    f"方位檢核未通過（{'、'.join(failed)}）"
                    if failed
                    else f"面積 {trial.area:,.0f} m² 不合理"
                )
                warnings.append(
                    f"自動選擇邊界來源：道路切割{reason}，改用地籍萃取"
                )
                boundary_source = "cadastral"
        else:
            warnings.append("自動選擇邊界來源：道路切割失敗，改用地籍萃取")
            boundary_source = "cadastral"
    elif boundary_source == "auto":
        reason = (
            "界線道路未全數解析" if missing_roads else "無 OSM 道路"
        )
        warnings.append(f"自動選擇邊界來源：{reason}，改用地籍萃取")
        boundary_source = "cadastral"

    if boundary_source == "zoning":
        pass
    elif boundary_source == "cadastral":
        # 由電子地圖辨識道路空間、連通擴張出街廓，再吸附到地籍圖宗地界線。
        # OSM 巷弄常缺漏（潭興街107巷21弄實際為 L 形，OSM 只有直線段），
        # 因此不以道路中心線切割。
        # 只有 JSON 指定的四條界線道路算分隔，其餘巷道併入街廓。
        boundary_lines_4326: list[list[tuple[float, float]]] = []
        for feature in roads.values():
            geometry = shapely_transform(_TO_4326, feature.geometry)
            parts = (
                [geometry]
                if isinstance(geometry, LineString)
                else list(getattr(geometry, "geoms", []))
            )
            for part in parts:
                coords = [(x, y) for x, y in part.coords]
                if len(coords) >= 2:
                    boundary_lines_4326.append(coords)

        # 由方位條件推得種子點，避免宗地位於道路內時選錯街廓。
        seed = None
        if roads:
            seed = road_cut_seed(roi, roads, parcel_geom, anchor)
            if seed is not None:
                warnings.append(
                    "街廓種子點由方位條件推得"
                    f"（{seed[0]:.6f}, {seed[1]:.6f}）"
                )

        try:
            block = extract_block(
                anchor_longitude,
                anchor_latitude,
                zoom=19,
                window_m=roi_size_m,
                boundary_road_lines=boundary_lines_4326 or None,
                seed_lonlat=seed,
            )
        except BlockExtractionError as exc:
            raise ValueError(f"街廓萃取失敗：{exc}") from exc

        boundary_3826 = block.polygon_3826
        applied_offset = 0.0
        warnings.extend(block.warnings)
        block_info = block
    else:
        boundary_3826, applied_offset, cut_warnings = road_bounded_polygon(
            roi,
            roads,
            parcel_geom,
            anchor,
            road_offset_m=road_offset_m,
            parcel_points=parcel_points,
        )
        warnings.extend(cut_warnings)
        block_info = None
    boundary_4326 = shapely_transform(_TO_4326, boundary_3826)

    direction_audit = audit_directions(boundary_3826, roads) if roads else {}
    for relation, ok in direction_audit.items():
        if not ok:
            warnings.append(
                f"方位檢核未通過：{relation}（{roads[relation].name}）"
            )

    if parcel_points:
        inside_ratio = parcel_inside_ratio(boundary_3826, parcel_points)
    else:
        inside_ratio = (
            boundary_3826.intersection(parcel_geom).area / parcel_geom.area
            if parcel_geom.area
            else 0.0
        )
    # 比準地依定義必屬本區段；若萃取結果切掉宗地一角（光柵解析度與
    # 界線吸附的誤差），以真實宗地輪廓補齊，確保宗地完整落在區段內。
    if inside_ratio < 0.999:
        true_parts = [
            shapely_transform(_TO_3826, shape)
            for shape in (parcel_polygon_from_tint(item) for item in infos)
            if shape is not None
        ]
        if true_parts:
            # 逐筆補齊：多筆比準地時若只算聯集的間距，已在區段內的那筆會
            # 讓 gap 變成 0，落在區段外的另一筆就補不進來。
            merged = boundary_3826
            for item, part in zip(infos, true_parts):
                # 輪廓簡化會切角，涵蓋不到部分邊緣像素；
                # 依著色圖像素尺寸做半個像素對角線的緩衝即可完全覆蓋。
                pixel_size_m = _tint_pixel_size_m(item)
                grow = pixel_size_m * 0.75 if pixel_size_m > 0 else 0.0

                # 宗地可能完全落在區段外（實測 P003 樹德段1415 僅 19.7 m²，
                # 位於街廓外 0.4 m）。此時 union 會得到 MultiPolygon，
                # 取最大塊就會把宗地丟掉，比例仍為 0%。
                # 因此先補足間隙讓兩者相接，確保聯集為單一多邊形。
                gap = part.distance(merged)
                if gap > 0:
                    grow = max(grow, gap + 0.5)
                if grow > 0:
                    part = part.buffer(grow)
                candidate = unary_union([merged, part])
                if isinstance(candidate, MultiPolygon):
                    candidate = max(candidate.geoms, key=lambda p: p.area)
                if isinstance(candidate, Polygon) and not candidate.is_empty:
                    merged = candidate
            if isinstance(merged, Polygon) and not merged.is_empty:
                boundary_3826 = merged
                boundary_4326 = shapely_transform(_TO_4326, boundary_3826)
                if parcel_points:
                    inside_ratio = parcel_inside_ratio(boundary_3826, parcel_points)
                warnings.append(
                    "已以真實宗地輪廓補齊區段，確保比準地完整落在區段內"
                )

    anchor_inside = bool(boundary_3826.covers(anchor))

    if not anchor_inside:
        warnings.append("宗地代表點未落在區段內，請檢視道路資料是否完整")
    if inside_ratio < 0.999:
        warnings.append(
            f"宗地僅 {inside_ratio:.1%} 落在區段內，可能緊鄰界線道路"
        )

    # 視野以區段範圍為準並留邊，確保紅色宗地與四條界線道路都看得到。
    min_longitude, min_latitude, max_longitude, max_latitude = boundary_4326.bounds
    span_longitude = max_longitude - min_longitude
    span_latitude = max_latitude - min_latitude
    min_longitude -= span_longitude * padding_ratio
    max_longitude += span_longitude * padding_ratio
    min_latitude -= span_latitude * padding_ratio
    max_latitude += span_latitude * padding_ratio

    zoom = zoom_override or _fit_zoom(
        min_longitude,
        min_latitude,
        max_longitude,
        max_latitude,
        width,
        height,
    )
    center_longitude = (min_longitude + max_longitude) / 2
    center_latitude = (min_latitude + max_latitude) / 2
    center_x, center_y = lonlat_to_pixel(center_longitude, center_latitude, zoom)
    left = center_x - width / 2
    top = center_y - height / 2

    stack = [str(base_map), *[str(layer) for layer in overlays]]
    if label_overlay:
        stack.append(str(label_overlay))

    layer_tiles: dict[str, tuple[int, int]] = {}
    with create_tile_session() as session:
        canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        for code in stack:
            image, filled, blank = _fetch_layer_image(
                code,
                zoom,
                left,
                top,
                width,
                height,
                session,
            )
            layer_tiles[code] = (filled, blank)
            # 地籍圖改黑並降低不透明度，讓綠色區段線與紅色宗地更突出。
            if code == str(ExtraLayer.DMAPS):
                image = restyle_layer(
                    image, cadastral_color, cadastral_opacity
                )
            canvas = Image.alpha_composite(canvas, image)

    # 紅色宗地：貼 NLSC 著色圖，那是真實輪廓（不是外接矩形）
    parcel_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for item, item_geom in zip(infos, parcel_geoms):
        if item.tint_image_png:
            tint = Image.open(io.BytesIO(item.tint_image_png)).convert("RGBA")
            box_left, box_top = lonlat_to_pixel(
                item.min_longitude, item.max_latitude, zoom
            )
            box_right, box_bottom = lonlat_to_pixel(
                item.max_longitude, item.min_latitude, zoom
            )
            target_width = max(int(round(box_right - box_left)), 1)
            target_height = max(int(round(box_bottom - box_top)), 1)
            resized = tint.resize((target_width, target_height), Image.LANCZOS)
            parcel_layer.paste(
                resized,
                (int(round(box_left - left)), int(round(box_top - top))),
                resized,
            )
        else:
            # 沒有著色圖才退回外接矩形，並已在 warnings 標明。
            parcel_ring = _ring_to_screen(
                mapping(shapely_transform(_TO_4326, item_geom))["coordinates"][0],
                zoom,
                left,
                top,
            )
            ImageDraw.Draw(parcel_layer).polygon(
                parcel_ring,
                fill=PARCEL_COLOR,
                outline=PARCEL_OUTLINE,
            )
    canvas = Image.alpha_composite(canvas, parcel_layer)

    # 宗地填色範圍：任何文字都不得覆蓋（含路名與標註框）。
    protected_regions: list[Polygon] = []
    for item in infos:
        if not item.has_extent:
            continue
        guard_left, guard_top = lonlat_to_pixel(
            item.min_longitude, item.max_latitude, zoom
        )
        guard_right, guard_bottom = lonlat_to_pixel(
            item.max_longitude, item.min_latitude, zoom
        )
        pad = 4
        protected_regions.append(
            box(
                guard_left - left - pad,
                guard_top - top - pad,
                guard_right - left + pad,
                guard_bottom - top + pad,
            )
        )

    # 綠色區段邊界（含內環，避免有洞的區塊畫錯）
    boundary_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    boundary_draw = ImageDraw.Draw(boundary_layer)
    geo = mapping(boundary_4326)
    rings = geo["coordinates"] if geo["type"] == "Polygon" else []
    boundary_screen: Polygon | None = None
    for index, ring in enumerate(rings):
        screen_ring = _ring_to_screen(ring, zoom, left, top)
        if len(screen_ring) >= 2:
            boundary_draw.line(
                screen_ring + [screen_ring[0]],
                fill=BOUNDARY_COLOR,
                width=boundary_line_width,
                joint="curve",
            )
        if index == 0 and len(screen_ring) >= 3:
            boundary_screen = Polygon(screen_ring)
    canvas = Image.alpha_composite(canvas, boundary_layer)

    # 宗地填色重貼一次：極小宗地（P003 僅 19.7 m²）緊貼邊界時，
    # 綠色邊界線寬會把填色蓋掉，重貼可確保宗地一定看得見。
    canvas = Image.alpha_composite(canvas, parcel_layer)

    if boundary_screen is None or not boundary_screen.is_valid:
        boundary_screen = box(0, 0, 1, 1)

    # 路名大字（藍色，沿路方向）
    road_labels: list[str] = []
    occupied: list[Polygon] = []
    if annotate_roads:
        view_west, view_north = pixel_to_lonlat(left, top, zoom)
        view_east, view_south = pixel_to_lonlat(left + width, top + height, zoom)
        road_lines = fetch_road_names(
            view_west,
            view_south,
            view_east,
            view_north,
        )
        # 四條界線道路一定要標，含巷弄；同一條路兼任兩方位會標兩次。
        # 無 OSM 比對結果時直接採用 JSON 指定的路名。
        # OSM 查無的方位一律用 JSON 指定的路名，確保四個方位都標得出來。
        required_relations = {
            relation: (
                roads[relation].name if relation in roads else names[relation]
            )
            for relation in RELATIONS
        }
        canvas, road_labels, occupied, missing_required = _draw_road_labels(
            canvas,
            road_lines,
            zoom,
            left,
            top,
            width,
            height,
            font_size=road_font_size,
            max_labels=max_road_labels,
            include_alleys=include_alleys,
            required_relations=required_relations,
            boundary_screen=boundary_screen,
            protected=protected_regions,
            road_lines_screen=_road_lines_screen(
                roads,
                zoom,
                left,
                top,
            ),
        )
        if not road_labels:
            warnings.append("未取得路名標註（Overpass 無回應或範圍內無具名道路）")
        for item in missing_required:
            if item.startswith("[方位側無該路幾何]"):
                target = item.split("]", 1)[1].strip()
                warnings.append(
                    f"{target} 無 OSM 幾何可比對，文字位置改依方位語意推定"
                )
            else:
                warnings.append(f"界線道路未能標註（畫面內找不到位置）：{item}")

    # 區段編號與比準地標註：一律放在區段之外，不覆蓋區段內資料
    occupied = list(occupied) + protected_regions
    annotation_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    annotation_draw = ImageDraw.Draw(annotation_layer)
    annotation_font = _load_font(24)

    parcel_points_screen: list[Point] = []
    for item in infos:
        item_x, item_y = lonlat_to_pixel(*item.center, zoom)
        parcel_points_screen.append(Point(item_x - left, item_y - top))
    parcel_screen_x, parcel_screen_y = lonlat_to_pixel(
        anchor_longitude,
        anchor_latitude,
        zoom,
    )
    parcel_point = Point(parcel_screen_x - left, parcel_screen_y - top)

    # 比準地說明框：貼近宗地但不進區段，並以引線指向宗地
    benchmark_lines: list[str] = []
    if zone_code:
        district_name = district or f"{info.land_office}區"
        county_label = CountyCode.parse(county).label
        # 多筆比準地同段時併成「太平段367、917地號」，否則逐行列出。
        sections = {item["section"]["name"] for item in cases}
        if len(sections) == 1:
            parcel_lines = [
                f"{county_label}{district_name}{cases[0]['section']['name']}"
                + "、".join(str(item["parcel"]) for item in cases)
                + "地號"
            ]
        else:
            parcel_lines = [
                f"{county_label}{district_name}"
                f"{item['section']['name']}{item['parcel']}地號"
                for item in cases
            ]
        benchmark_lines = [
            f"區段{zone_code}比準地：",
            *parcel_lines,
            f"（{case['constraints']['zone']}）",
        ]

        text_width, text_height, _ = _measure_lines(
            annotation_draw, benchmark_lines, annotation_font, 7
        )
        spot = _find_clear_spot(
            width,
            height,
            text_width + 18,
            text_height + 18,
            boundary_screen,
            occupied,
            prefer=parcel_point,
        )
        if spot is None:
            warnings.append("找不到不覆蓋區段的位置放置比準地標註")
        else:
            rect = _draw_label_box(
                annotation_draw,
                spot,
                benchmark_lines,
                annotation_font,
            )
            occupied.append(rect)
            # 引線由框邊連到每一筆宗地，多筆比準地都要指得到。
            for target_parcel in parcel_points_screen or [parcel_point]:
                _, edge = nearest_points(target_parcel, rect.exterior)
                annotation_draw.line(
                    [(edge.x, edge.y), (target_parcel.x, target_parcel.y)],
                    fill=ANNOTATION_BORDER,
                    width=2,
                )

        # 區段編號框：緊貼區段外緣
        code_lines = [f"區段{zone_code}"]
        code_width, code_height, _ = _measure_lines(
            annotation_draw, code_lines, annotation_font, 7
        )
        code_spot = _find_clear_spot(
            width,
            height,
            code_width + 18,
            code_height + 18,
            boundary_screen,
            occupied,
        )
        if code_spot is None:
            warnings.append("找不到不覆蓋區段的位置放置區段編號")
        else:
            code_rect = _draw_label_box(
                annotation_draw,
                code_spot,
                code_lines,
                annotation_font,
            )
            occupied.append(code_rect)
            _, edge = nearest_points(
                boundary_screen.exterior.interpolate(
                    boundary_screen.exterior.project(code_rect.centroid)
                ),
                code_rect.exterior,
            )
            target_point = boundary_screen.exterior.interpolate(
                boundary_screen.exterior.project(code_rect.centroid)
            )
            annotation_draw.line(
                [(edge.x, edge.y), (target_point.x, target_point.y)],
                fill=ANNOTATION_BORDER,
                width=2,
            )

    canvas = Image.alpha_composite(canvas, annotation_layer)

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(target, format="PNG")

    return BoundaryResult(
        output_path=target,
        parcel=parcel,
        parcel_info=info,
        boundary_3826=boundary_3826,
        boundary_4326=boundary_4326,
        # OSM 查無的方位以 JSON 指定的路名補上，避免報表出現空白路名。
        matched_roads={
            relation: (
                roads[relation].name if relation in roads else names[relation]
            )
            for relation in RELATIONS
        },
        zoom=zoom,
        width=width,
        height=height,
        area_m2=boundary_3826.area,
        parcel_inside_ratio=inside_ratio,
        anchor_inside=anchor_inside,
        road_offset_m=applied_offset,
        boundary_source=boundary_source,
        snapped_vertices=block_info.snapped_vertices if block_info else 0,
        average_snap_px=block_info.average_snap_px if block_info else 0.0,
        verify_url=build_goland_url(
            county,
            parcels,
            base_map,
            (*overlays, label_overlay) if label_overlay else tuple(overlays),
        ),
        zone_code=zone_code,
        road_labels=tuple(road_labels),
        direction_audit=direction_audit,
        layer_tiles=layer_tiles,
        warnings=warnings,
        parcels=tuple(parcels),
        parcel_infos=tuple(infos),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_zone_boundary.py",
        description="依 input JSON 的道路條件框出區段範圍，疊在 NLSC 地籍圖上輸出 PNG。",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=str(_POC_ROOT / "input.example.json"),
        help="條件 JSON，預設為 POC 的 input.example.json",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="zone_boundary.png",
        help="輸出 PNG，預設 zone_boundary.png",
    )
    parser.add_argument("-c", "--county", default="新北市", help="縣市，預設 新北市")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument(
        "--roi",
        type=float,
        default=600.0,
        help="切格用的 ROI 邊長（公尺），預設 600",
    )
    parser.add_argument(
        "--road-offset",
        type=float,
        default=None,
        help=(
            "界線往外推的公尺數（考量路寬）；"
            "預設自動找出能讓宗地完整落入區段的最小值"
        ),
    )
    parser.add_argument(
        "--base-map",
        default=BaseMap.EMAP_NO_HOUSE_NUMBER.value,
        choices=[item.value for item in BaseMap],
    )
    parser.add_argument(
        "--zone-code",
        default=None,
        help="區段編號，例如 P002-00；給定後會標註區段編號與比準地說明",
    )
    parser.add_argument(
        "--district",
        default=None,
        help="鄉鎮市區名稱，預設由地政事務所名稱推導（例如 樹林 → 樹林區）",
    )
    parser.add_argument(
        "--no-roads",
        action="store_true",
        help="不標註路名大字",
    )
    parser.add_argument(
        "--include-alleys",
        action="store_true",
        help="路名標註含巷弄，預設不含",
    )
    parser.add_argument(
        "--road-font-size",
        type=int,
        default=30,
        help="路名字級，預設 30",
    )
    parser.add_argument(
        "--max-roads",
        type=int,
        default=18,
        help="最多標註幾條路名，預設 18",
    )
    parser.add_argument(
        "--boundary-source",
        default="zoning",
        choices=["zoning", "auto", "cadastral", "roads"],
        help=(
            "zoning：取使用分區圖上包含宗地的分區多邊形（預設，最直接）；"
            "auto：先試道路切割，不成再地籍萃取；"
            "cadastral：一律地籍萃取；roads：一律道路中心線切割"
        ),
    )
    parser.add_argument(
        "--line-width",
        type=int,
        default=3,
        help="區段邊界線寬，預設 3",
    )
    parser.add_argument(
        "--cadastral-color",
        default="black",
        help="地籍圖線條顏色，預設 black；none 保留原本洋紅",
    )
    parser.add_argument(
        "--cadastral-opacity",
        type=float,
        default=0.6,
        help="地籍圖不透明度 0~1，預設 0.6",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="方位檢核未通過時回傳非零結束碼（預設只警告不擋）",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.45,
        help="視野留邊比例，越大視野越廣，預設 0.45",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=None,
        help="強制指定縮放層級 1~20；預設依區段大小自動決定",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = render_zone_boundary(
            args.input,
            args.output,
            county=args.county,
            base_map=args.base_map,
            width=args.width,
            height=args.height,
            roi_size_m=args.roi,
            road_offset_m=args.road_offset,
            zone_code=args.zone_code,
            district=args.district,
            boundary_source=args.boundary_source,
            padding_ratio=args.padding,
            zoom_override=args.zoom,
            boundary_line_width=args.line_width,
            cadastral_color=parse_color(args.cadastral_color),
            cadastral_opacity=args.cadastral_opacity,
            annotate_roads=not args.no_roads,
            road_font_size=args.road_font_size,
            max_road_labels=args.max_roads,
            include_alleys=args.include_alleys,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"無法產出區段圖：{exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"連線失敗：{exc}", file=sys.stderr)
        return 1

    listed = list(zip(result.parcels, result.parcel_infos)) or [
        (result.parcel, result.parcel_info)
    ]
    print(f"比準地宗地共 {len(listed)} 筆：")
    for item_parcel, item_info in listed:
        print(
            f"  {item_info.display}（{item_parcel.code}，"
            f"{item_info.land_office}地政）"
            f" 中心 {item_info.center[0]:.6f}, {item_info.center[1]:.6f}"
        )

    print("\n界線道路與方位檢核：")
    for relation in RELATIONS:
        road = result.matched_roads.get(relation, "")
        if not result.direction_audit:
            mark = "未檢核（無 OSM 幾何）"
        else:
            mark = "通過" if result.direction_audit.get(relation) else "未通過"
        print(f"  {relation:9s} {road:14s} {mark}")

    print(f"\n區段面積：{result.area_m2:,.0f} m²（{result.area_m2 / 10000:.4f} 公頃）")
    print(f"邊界來源：{result.boundary_source}")
    if result.boundary_source == "cadastral":
        print(
            f"吸附宗地界線：{result.snapped_vertices} 點，"
            f"平均位移 {result.average_snap_px:.1f} px"
        )
    else:
        print(f"界線外推：{result.road_offset_m:.1f} m（考量路寬）")
    print(f"宗地代表點在區段內：{'是' if result.anchor_inside else '否'}")
    print(f"宗地面積落在區段內比例：{result.parcel_inside_ratio:.1%}")
    print(f"層級：z{result.zoom}")
    if result.zone_code:
        print(f"區段編號：{result.zone_code}")
    print("\nJSON 指定界線道路的文字標註（逐方位）：")
    label_counts: dict[str, int] = {}
    for name in result.road_labels:
        label_counts[name] = label_counts.get(name, 0) + 1
    needed: dict[str, int] = {}
    for relation in RELATIONS:
        road = result.matched_roads.get(relation, "")
        needed[road] = needed.get(road, 0) + 1
    for relation in RELATIONS:
        road = result.matched_roads.get(relation, "")
        drawn = label_counts.get(road, 0)
        mark = "已標註" if drawn >= needed.get(road, 1) else "未標註"
        extra = f"（該路共標 {drawn} 次）" if needed.get(road, 1) > 1 else ""
        print(f"  {relation:9s} {road:18s} {mark}{extra}")

    if result.road_labels:
        print(f"\n路名大字標註 {len(result.road_labels)} 條：")
        print("  " + "、".join(result.road_labels))

    print("圖層圖磚（成功/空白）：")
    for code, (filled, blank) in result.layer_tiles.items():
        flag = "" if filled else "   <-- 全空白"
        print(f"  {code:10s} {filled} / {blank}{flag}")

    if result.warnings:
        print("\n警告：")
        for message in result.warnings:
            print(f"  - {message}")

    failed_relations = [
        relation for relation, ok in result.direction_audit.items() if not ok
    ]
    if failed_relations and args.strict:
        print(
            "\n--strict：以下方位條件在幾何上不成立 → "
            f"{'、'.join(failed_relations)}\n"
            "  圖檔仍已輸出供診斷。",
            file=sys.stderr,
        )
        return 1

    print("\nNLSC 圖台核對連結（已套地籍圖、段籍圖與路名，宗地會著色）：")
    print(f"  {result.verify_url}")

    size_kb = result.output_path.stat().st_size / 1024
    print(
        f"\n已存出：{result.output_path}（{size_kb:.1f} KB，"
        f"{result.width}x{result.height}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
