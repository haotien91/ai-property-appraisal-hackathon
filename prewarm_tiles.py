"""圖磚預熱：先把目標區段用到的 NLSC 圖磚抓進磁碟快取。

同步 API 的前提是圖磚已在本機。實測一次出圖要 140 張圖磚、
冷啟動 26～73 秒；預熱後這段幾乎歸零，剩下的只有分區萃取與
合成標註。

用法：
    # 針對既有條件 JSON 預熱（可一次給多個）
    python prewarm_tiles.py P001.json P002.json P003.json P004.json

    # 指定快取目錄（預設吃 NLSC_TILE_CACHE，再退回 ./_tile_cache）
    python prewarm_tiles.py P002.json --cache-dir D:\\tiles

預熱的做法是「直接照正式流程出一次圖」，而不是自己算圖磚範圍：
視野與層級是由區段大小推導出來的，重算一次容易跟正式流程不一致，
反而預熱到錯誤的圖磚。多花一次合成的時間換取百分之百一致。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import render_parcel_map
import road_cache
from render_zone_boundary import load_cases, render_zone_boundary

DEFAULT_CACHE_DIR = os.environ.get("NLSC_TILE_CACHE", "_tile_cache")
DEFAULT_ROAD_CACHE_DIR = os.environ.get("ROAD_CACHE_DIR", "_road_cache")


def prewarm(
    input_paths: list[str],
    *,
    cache_dir: str = DEFAULT_CACHE_DIR,
    road_cache_dir: str = DEFAULT_ROAD_CACHE_DIR,
    width: int = 1400,
    height: int = 1000,
    boundary_source: str = "zoning",
) -> int:
    target = render_parcel_map.set_tile_cache_dir(cache_dir)
    road_target = road_cache.install(road_cache_dir)
    print(f"圖磚快取目錄：{target}")
    print(f"路網快取目錄：{road_target}")

    failures = 0
    with TemporaryDirectory(prefix="prewarm-") as workspace:
        for index, path in enumerate(input_paths, start=1):
            label = Path(path).stem
            try:
                cases = load_cases(path)
            except (OSError, ValueError) as exc:
                print(f"[{index}/{len(input_paths)}] {label}  讀取失敗：{exc}")
                failures += 1
                continue

            render_parcel_map.reset_tile_cache_stats()
            road_cache.reset_stats()
            started = time.perf_counter()
            try:
                result = render_zone_boundary(
                    path,
                    Path(workspace) / f"{label}.png",
                    zone_code=f"{label}-00",
                    width=width,
                    height=height,
                    boundary_source=boundary_source,
                )
            except Exception as exc:  # noqa: BLE001 - 預熱不該因單一案子中斷
                print(f"[{index}/{len(input_paths)}] {label}  出圖失敗：{exc}")
                failures += 1
                continue

            tiles = render_parcel_map.tile_cache_stats()
            roads = road_cache.stats()
            print(
                f"[{index}/{len(input_paths)}] {label}  "
                f"{time.perf_counter() - started:6.1f}s  "
                f"{len(cases)} 筆宗地  {result.area_m2:>8,.0f} m²  "
                f"圖磚 命中{tiles['hit']:>4}/抓取{tiles['miss']:>4}  "
                f"路網格 命中{roads['cell_hit']:>2}/抓取{roads['cell_fetch']:>2}"
            )

    tile_files = list(Path(cache_dir).rglob("*.tile"))
    road_files = list(Path(road_cache_dir).rglob("*.json.gz"))
    print(
        f"\n快取現況："
        f"圖磚 {len(tile_files):,} 張 "
        f"{sum(f.stat().st_size for f in tile_files) / 1e6:.1f} MB；"
        f"路網 {len(road_files):,} 格 "
        f"{sum(f.stat().st_size for f in road_files) / 1e6:.1f} MB"
    )
    if failures:
        print(f"有 {failures} 個案子未完成")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prewarm_tiles.py",
        description="先抓好 NLSC 圖磚，讓同步出圖 API 能在數秒內回應",
    )
    parser.add_argument("inputs", nargs="+", help="條件 JSON，可多個")
    parser.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help=f"圖磚快取目錄，預設 {DEFAULT_CACHE_DIR}",
    )
    parser.add_argument(
        "--road-cache-dir",
        default=DEFAULT_ROAD_CACHE_DIR,
        help=f"路網網格快取目錄，預設 {DEFAULT_ROAD_CACHE_DIR}",
    )
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--boundary-source", default="zoning")
    args = parser.parse_args(argv)

    return prewarm(
        args.inputs,
        cache_dir=args.cache_dir,
        road_cache_dir=args.road_cache_dir,
        width=args.width,
        height=args.height,
        boundary_source=args.boundary_source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
