"""由 NLSC 通用電子地圖萃取街廓範圍。

動機：OSM 路網對巷弄常有缺漏（實測潭興街107巷21弄實際為 L 形，
OSM 只收錄西北側直線段 123 m，缺少轉折後的西南段），
以道路中心線切割會得到錯誤範圍。

做法：改由官方電子地圖辨識「道路空間」，再從目標宗地連通擴張出街廓。
邊界因此自然沿宗地與道路的交界走，而非道路中心線。

關鍵實測數據（EMAP16，z19，樹林區文林段一帶）：
- 道路為純白 RGB(255,255,255) 佔 16.2%
- 街區內非道路地面為淺灰 RGB(247,247,247) 佔 16.9%
- 建物為淺紫 RGB(234,227,234) 佔 28.9%
- 機關學校用地為米色 RGB(249,245,236) 佔 11.8%
門檻取 min(RGB) >= 253 可乾淨隔出道路網（約 18%），
不會把街區內空地或建物誤判為道路。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
import requests
from PIL import Image
from scipy import ndimage
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform

from render_parcel_map import (
    _fetch_layer_image,
    create_tile_session,
    lonlat_to_pixel,
    pixel_to_lonlat,
)

_TO_3826 = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True).transform

# 道路判定門檻：純白為道路，淺灰街區空地不算。
ROAD_WHITE_THRESHOLD = 253

# 分析用底圖。EMAP16 不含等高線及門牌，雜訊最少。
ANALYSIS_BASE_MAP = "EMAP16"


class BlockExtractionError(RuntimeError):
    pass


@dataclass
class BlockExtraction:
    polygon_4326: Polygon
    polygon_3826: Polygon
    zoom: int
    area_m2: float
    road_ratio: float
    touches_window_edge: bool
    vertex_count: int
    snapped_vertices: int = 0
    average_snap_px: float = 0.0
    debug_images: dict[str, Image.Image] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _rasterize_boundary_roads(
    lines_screen: list[list[tuple[float, float]]],
    shape: tuple[int, int],
    buffer_px: int,
) -> np.ndarray:
    """把界線道路畫成遮罩，用來判斷某條道路是否為區段界線。"""

    mask = np.zeros(shape, dtype=np.uint8)
    thickness = max(1, buffer_px * 2)
    for points in lines_screen:
        if len(points) < 2:
            continue
        array = np.array([[int(round(x)), int(round(y))] for x, y in points])
        cv2.polylines(mask, [array], False, 1, thickness)
    return mask


def _merge_across_internal_lanes(
    road: np.ndarray,
    labels: np.ndarray,
    start_label: int,
    boundary_mask: np.ndarray | None,
    resolution: float,
    *,
    max_merge_width_m: float,
    boundary_overlap_ratio: float = 0.35,
    max_rounds: int = 12,
) -> tuple[np.ndarray, int]:
    """由起始街廓出發，跨越「非界線且較窄」的道路合併相鄰街廓。

    地價區段的分隔只有 JSON 指定的四條界線道路；街廓內部的巷道
    （例如 P002 的啟智街19巷、P004 的潭興街91巷1弄）不應把區段切開。

    判斷是否可跨越有兩道條件，缺一不可：
    1. 該段道路不落在界線道路遮罩上
    2. 該段道路實際寬度小於 max_merge_width_m

    第 2 條是必要的保險：OSM 巷弄常有缺漏（潭興街107巷21弄 實際為 L 形，
    OSM 只收錄西北段），若只看第 1 條，缺漏路段會被誤判成內部巷而讓街廓外溢。
    """

    # 膨脹核必須足以跨越可合併的道路寬度，否則偵測不到對面的街廓。
    # 道路寬 20~40 px，用固定 7x7（僅延伸 3 px）會完全跨不過去，
    # 實測會讓 P002 只併到 84 m² 的碎塊而卡在 6,216 m²。
    reach_px = int(math.ceil(max_merge_width_m / resolution)) + 4
    kernel_size = max(9, min(2 * reach_px + 1, 121))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (kernel_size, kernel_size),
    )
    # 道路內部的距離轉換：值 × 2 即為該處道路寬度（像素）。
    road_distance = cv2.distanceTransform(road, cv2.DIST_L2, 5)
    min_neighbour_px = int(round(50.0 / (resolution**2)))

    current = (labels == start_label).astype(np.uint8)
    merged_count = 0

    for _ in range(max_rounds):
        grown = cv2.dilate(current, kernel)
        neighbour_labels = set(np.unique(labels[(grown > 0) & (current == 0)]))
        neighbour_labels.discard(0)
        neighbour_labels.discard(start_label)
        if not neighbour_labels:
            break

        changed = False
        grown_current = cv2.dilate(current, kernel) > 0
        for label in neighbour_labels:
            other = (labels == label).astype(np.uint8)
            # 略過碎塊，避免大量無意義的形態學運算。
            if int(other.sum()) < min_neighbour_px:
                continue
            separating = (
                grown_current & (cv2.dilate(other, kernel) > 0) & (road > 0)
            )
            if not separating.any():
                continue

            if boundary_mask is not None:
                # 內部巷道會在路口與界線道路相接，若用「有碰到就算界線」
                # 會把所有內部巷誤判成邊界（實測 P002 因此卡在 6,216 m²）。
                # 改以重疊比例判斷：多數落在界線道路上才算邊界。
                separating_total = int(separating.sum())
                on_boundary = int((boundary_mask > 0)[separating].sum())
                if (
                    separating_total > 0
                    and on_boundary / separating_total > boundary_overlap_ratio
                ):
                    continue

            width_m = float(road_distance[separating].max()) * 2.0 * resolution
            if width_m > max_merge_width_m:
                continue

            current = ((current > 0) | (other > 0) | separating).astype(np.uint8)
            merged_count += 1
            changed = True

        if not changed:
            break

    filled = ndimage.binary_fill_holes(current.astype(bool))
    return filled.astype(np.uint8), merged_count


def _fill_internal_notches(mask: np.ndarray, bridge_px: int) -> np.ndarray:
    """填補街廓內部窄巷造成的凹口與孔洞。

    地價區段的邊界是指定的界線道路；街廓內部的窄巷（例如潭興街91巷1弄）
    不應讓區段邊界凹進去。

    採用「對街廓遮罩做閉運算」而非「對道路遮罩做開運算」：
    開運算的侵蝕會在路名文字孔洞與轉角處把路網打斷，實測會讓所有街廓
    連成一片（面積由 4,089 m² 暴增到 182,686 m² 並碰到視窗邊界）。
    閉運算只在自身凹陷處補上像素，且核心小於界線道路寬度時
    不可能跨越界線道路併入鄰街廓。
    """

    if bridge_px < 3:
        return mask

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (bridge_px, bridge_px),
    )
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # 補完外圍凹口後，再填掉完全封閉的內部孔洞。
    filled = ndimage.binary_fill_holes(closed.astype(bool))
    return filled.astype(np.uint8)


def _road_mask(
    image: Image.Image,
    threshold: int,
    close_px: int,
) -> np.ndarray:
    """由電子地圖取出道路遮罩，並填補路名文字造成的孔洞。

    路名文字是深色、疊在白色路面上，會在道路遮罩中形成孔洞；
    若不填補，連通擴張可能從文字缺口穿越道路、把兩個街廓連成一個。
    """

    array = np.asarray(image.convert("RGB"))
    road = np.all(array >= threshold, axis=2).astype(np.uint8)

    if close_px > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (close_px, close_px),
        )
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, kernel)
    return road


def _cadastral_mask(image: Image.Image, alpha_threshold: int = 30) -> np.ndarray:
    """地籍圖桃紅界線遮罩。

    DMAPS 圖磚實測：90% 完全透明，可見像素一律為 RGB(255,0,255)，
    僅 alpha 變化，因此直接用 alpha 判定即可，不需比對顏色。
    """

    array = np.asarray(image.convert("RGBA"))
    return (array[:, :, 3] >= alpha_threshold).astype(np.uint8)


def _snap_to_cadastral(
    points: list[tuple[float, float]],
    cadastral: np.ndarray,
    max_snap_px: float,
) -> tuple[list[tuple[float, float]], int, float]:
    """把輪廓頂點吸附到最近的宗地界線像素。

    街廓輪廓來自電子地圖的道路邊緣，與地籍圖界線可能相差數像素
    （兩者是不同圖資）。吸附後邊界才真正沿宗地界線走。
    回傳 (吸附後頂點, 成功吸附數, 平均位移像素)。
    """

    if cadastral.sum() == 0:
        return points, 0, 0.0

    # 對每個像素求「到最近界線像素的距離」與該界線像素座標。
    distance, indices = ndimage.distance_transform_edt(
        cadastral == 0,
        return_indices=True,
    )
    rows, cols = indices
    height, width = cadastral.shape

    snapped: list[tuple[float, float]] = []
    moved = 0
    total_shift = 0.0
    for point_x, point_y in points:
        x = int(np.clip(round(point_x), 0, width - 1))
        y = int(np.clip(round(point_y), 0, height - 1))
        if distance[y, x] <= max_snap_px:
            target_x = float(cols[y, x])
            target_y = float(rows[y, x])
            total_shift += math.hypot(target_x - point_x, target_y - point_y)
            moved += 1
            snapped.append((target_x, target_y))
        else:
            snapped.append((point_x, point_y))

    # 移除吸附後產生的連續重複點。
    deduped: list[tuple[float, float]] = []
    for point in snapped:
        if not deduped or math.dist(point, deduped[-1]) > 0.5:
            deduped.append(point)
    if len(deduped) >= 2 and math.dist(deduped[0], deduped[-1]) <= 0.5:
        deduped.pop()

    average_shift = total_shift / moved if moved else 0.0
    return deduped, moved, average_shift


def _pick_component(
    labels: np.ndarray,
    seed_x: int,
    seed_y: int,
) -> int:
    """取種子點所在的連通元件；若種子落在道路上，取最近的非道路像素。"""

    height, width = labels.shape
    seed_x = int(np.clip(seed_x, 0, width - 1))
    seed_y = int(np.clip(seed_y, 0, height - 1))

    label = int(labels[seed_y, seed_x])
    if label != 0:
        return label

    # 宗地緊鄰道路時代表點可能落在路面，向外螺旋找最近的街廓像素。
    for radius in range(1, 60):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                x = seed_x + dx
                y = seed_y + dy
                if 0 <= x < width and 0 <= y < height:
                    candidate = int(labels[y, x])
                    if candidate != 0:
                        return candidate
    raise BlockExtractionError("宗地周圍找不到街廓區域，請確認座標或放大分析範圍")


def extract_block(
    longitude: float,
    latitude: float,
    *,
    zoom: int = 19,
    window_m: float = 600.0,
    threshold: int = ROAD_WHITE_THRESHOLD,
    close_px: int = 7,
    simplify_px: float = 2.5,
    min_area_m2: float = 300.0,
    snap_to_cadastral: bool = True,
    max_snap_px: float = 12.0,
    internal_lane_width_m: float = 7.0,
    boundary_road_lines: list[list[tuple[float, float]]] | None = None,
    max_merge_width_m: float = 0.0,
    seed_lonlat: tuple[float, float] | None = None,
    session: requests.Session | None = None,
    keep_debug: bool = False,
) -> BlockExtraction:
    """萃取包含指定座標的街廓範圍。

    window_m 是分析視窗邊長（公尺）。街廓必須完整落在視窗內，
    否則輪廓會被視窗邊界截斷；本函式會回報 touches_window_edge。
    """

    if zoom < 1 or zoom > 20:
        raise ValueError("縮放層級必須介於 1 到 20")

    # 由目標層級的解析度換算視窗像素大小。
    resolution = 156_543.033_92 * math.cos(math.radians(latitude)) / (2**zoom)
    size_px = int(round(window_m / resolution))
    size_px = max(256, min(size_px, 2048))

    center_x, center_y = lonlat_to_pixel(longitude, latitude, zoom)
    left = center_x - size_px / 2
    top = center_y - size_px / 2

    owns_session = session is None
    client = session or create_tile_session()
    cadastral_image: Image.Image | None = None
    try:
        base, filled, blank = _fetch_layer_image(
            ANALYSIS_BASE_MAP,
            zoom,
            left,
            top,
            size_px,
            size_px,
            client,
        )
        if snap_to_cadastral:
            cadastral_image, cadastral_filled, _ = _fetch_layer_image(
                "DMAPS",
                zoom,
                left,
                top,
                size_px,
                size_px,
                client,
            )
            if cadastral_filled == 0:
                cadastral_image = None
    finally:
        if owns_session:
            client.close()

    if filled == 0:
        raise BlockExtractionError(
            f"{ANALYSIS_BASE_MAP} 在 z{zoom} 取不到任何圖磚，無法分析"
        )

    warnings: list[str] = []
    if blank:
        warnings.append(f"{ANALYSIS_BASE_MAP} 有 {blank} 張圖磚為空白，邊界可能不完整")

    road = _road_mask(base, threshold, close_px)
    road_ratio = float(road.mean())
    if road_ratio < 0.02:
        raise BlockExtractionError(
            f"道路遮罩僅 {road_ratio:.1%}，門檻 {threshold} 可能不適用於此底圖"
        )
    if road_ratio > 0.6:
        raise BlockExtractionError(
            f"道路遮罩高達 {road_ratio:.1%}，門檻 {threshold} 過寬，會把街區誤判為道路"
        )

    block_space = (1 - road).astype(np.uint8)
    count, labels = cv2.connectedComponents(block_space, connectivity=4)
    if count <= 1:
        raise BlockExtractionError("道路未能分隔出任何街廓")

    # 種子點決定要取哪一個街廓。宗地若本身位於道路範圍內
    # （實測 P003 樹德段1415 僅 19.7 m²，338 個像素 100% 落在道路上），
    # 用宗地位置會往最近的非道路區塊擴散、可能落在道路錯誤的一側，
    # 因此改由方位條件推得的點作為種子。
    if seed_lonlat is not None:
        seed_px, seed_py = lonlat_to_pixel(seed_lonlat[0], seed_lonlat[1], zoom)
        seed_x = int(round(seed_px - left))
        seed_y = int(round(seed_py - top))
    else:
        seed_x = int(round(center_x - left))
        seed_y = int(round(center_y - top))
    label = _pick_component(labels, seed_x, seed_y)

    # 只有 JSON 指定的界線道路算分隔：先跨越非界線且較窄的巷道合併街廓。
    boundary_mask: np.ndarray | None = None
    if boundary_road_lines:
        screen_lines = [
            [
                (
                    lonlat_to_pixel(longitude, latitude, zoom)[0] - left,
                    lonlat_to_pixel(longitude, latitude, zoom)[1] - top,
                )
                for longitude, latitude in line
            ]
            for line in boundary_road_lines
        ]
        boundary_mask = _rasterize_boundary_roads(
            screen_lines,
            road.shape,
            buffer_px=max(2, int(round(3.0 / resolution))),
        )

    # 跨街廓合併預設關閉：實測不穩定，會讓街廓沿巷道一路蔓延到錯誤位置
    # （P002 隨寬度上限由 10,818 → 15,254 → 147,744 m²，且落點偏離目標街廓）。
    # 內部窄巷改由下方的凹口填補處理，已足以應付 P004 的潭興街91巷1弄。
    if max_merge_width_m > 0:
        mask, merged_lanes = _merge_across_internal_lanes(
            road,
            labels,
            label,
            boundary_mask,
            resolution,
            max_merge_width_m=max_merge_width_m,
        )
        if merged_lanes:
            warnings.append(
                f"已跨越 {merged_lanes} 條非界線巷道合併街廓"
                f"（寬度上限 {max_merge_width_m:.0f} m）"
            )
    else:
        mask = (labels == label).astype(np.uint8)

    # 再填掉殘餘凹口。
    if internal_lane_width_m > 0:
        bridge_px = int(round(internal_lane_width_m / resolution))
        mask = _fill_internal_notches(mask, bridge_px)

    # 街廓是否碰到視窗邊界：碰到就代表範圍被截斷。
    touches_edge = bool(
        mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any()
    )
    if touches_edge:
        warnings.append(
            "街廓延伸到分析視窗邊界，範圍可能被截斷；請加大 window_m"
        )

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise BlockExtractionError("找不到街廓輪廓")
    contour = max(contours, key=cv2.contourArea)

    approx = cv2.approxPolyDP(contour, simplify_px, True)
    points = [(float(p[0][0]), float(p[0][1])) for p in approx]
    if len(points) < 3:
        raise BlockExtractionError("街廓輪廓點數不足，無法構成多邊形")

    snapped_count = 0
    average_snap = 0.0
    cadastral = None
    if cadastral_image is not None:
        cadastral = _cadastral_mask(cadastral_image)
        if cadastral.sum() == 0:
            warnings.append("地籍圖無界線像素，未進行宗地界線吸附")
        else:
            points, snapped_count, average_snap = _snap_to_cadastral(
                points,
                cadastral,
                max_snap_px,
            )
            if len(points) < 3:
                raise BlockExtractionError("吸附後輪廓點數不足")
            ratio = snapped_count / max(len(points), 1)
            if ratio < 0.5:
                warnings.append(
                    f"僅 {ratio:.0%} 頂點成功吸附到宗地界線"
                    f"（容許 {max_snap_px:.0f} px），邊界可能未完全貼合"
                )
    elif snap_to_cadastral:
        warnings.append("取不到地籍圖圖磚，未進行宗地界線吸附")

    ring = [
        pixel_to_lonlat(left + point_x, top + point_y, zoom) for point_x, point_y in points
    ]
    polygon_4326 = Polygon(ring)
    if not polygon_4326.is_valid:
        polygon_4326 = polygon_4326.buffer(0)
    if polygon_4326.is_empty:
        raise BlockExtractionError("街廓多邊形無效")

    polygon_3826 = shapely_transform(_TO_3826, polygon_4326)
    area = polygon_3826.area
    if area < min_area_m2:
        raise BlockExtractionError(
            f"街廓面積僅 {area:.0f} m²，小於門檻 {min_area_m2:.0f} m²，"
            "可能誤選到宗地內的小區塊"
        )

    debug: dict[str, Image.Image] = {}
    if keep_debug:
        debug["base"] = base.convert("RGB")
        debug["road"] = Image.fromarray((road * 255).astype(np.uint8), mode="L")
        debug["block"] = Image.fromarray((mask * 255).astype(np.uint8), mode="L")
        if cadastral is not None:
            debug["cadastral"] = Image.fromarray(
                (cadastral * 255).astype(np.uint8), mode="L"
            )

    return BlockExtraction(
        polygon_4326=polygon_4326,
        polygon_3826=polygon_3826,
        zoom=zoom,
        area_m2=area,
        road_ratio=road_ratio,
        touches_window_edge=touches_edge,
        vertex_count=len(points),
        snapped_vertices=snapped_count,
        average_snap_px=average_snap,
        debug_images=debug,
        warnings=warnings,
    )


if __name__ == "__main__":
    import argparse
    import json
    import sys

    from nlsc_map_url import LandParcel
    from nlsc_parcel_map import verify_parcels

    parser = argparse.ArgumentParser(
        description="由電子地圖萃取街廓範圍（沿宗地／道路交界，非道路中心線）"
    )
    parser.add_argument("input", help="條件 JSON，需含 section.code 與 parcel")
    parser.add_argument("-c", "--county", default="新北市")
    parser.add_argument("--zoom", type=int, default=19)
    parser.add_argument("--window", type=float, default=600.0)
    parser.add_argument("--threshold", type=int, default=ROAD_WHITE_THRESHOLD)
    parser.add_argument("--close-px", type=int, default=7)
    parser.add_argument(
        "--internal-lane-width",
        type=float,
        default=7.0,
        help="要併入街廓的內部窄巷寬度上限（公尺），預設 7；設 0 不填凹口",
    )
    parser.add_argument("--simplify-px", type=float, default=2.5)
    parser.add_argument("--debug-prefix", default=None, help="存出中間遮罩影像")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as handle:
        case = json.load(handle)

    parcel = LandParcel.parse(case["section"]["code"], case["parcel"])
    info = verify_parcels(args.county, parcel)[0]
    if not info.exists:
        print(f"NLSC 查無地號 {parcel.code}", file=sys.stderr)
        raise SystemExit(1)

    longitude, latitude = info.center
    print(f"宗地：{info.display}（{parcel.code}）中心 {longitude:.6f}, {latitude:.6f}")

    try:
        result = extract_block(
            longitude,
            latitude,
            zoom=args.zoom,
            window_m=args.window,
            threshold=args.threshold,
            close_px=args.close_px,
            internal_lane_width_m=args.internal_lane_width,
            simplify_px=args.simplify_px,
            keep_debug=args.debug_prefix is not None,
        )
    except (BlockExtractionError, ValueError) as exc:
        print(f"萃取失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n街廓面積：{result.area_m2:,.0f} m²（{result.area_m2 / 10000:.4f} 公頃）")
    print(f"道路遮罩佔比：{result.road_ratio:.1%}")
    print(f"輪廓頂點數：{result.vertex_count}")
    print(
        f"吸附到宗地界線：{result.snapped_vertices}/{result.vertex_count} 點"
        f"，平均位移 {result.average_snap_px:.1f} px"
    )
    print(f"碰到視窗邊界：{'是' if result.touches_window_edge else '否'}")
    if result.warnings:
        print("\n警告：")
        for message in result.warnings:
            print(f"  - {message}")

    if args.debug_prefix:
        for name, image in result.debug_images.items():
            path = f"{args.debug_prefix}_{name}.png"
            image.save(path)
            print(f"  已存出 {path}")
