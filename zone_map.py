"""地價區段圖的函式庫介面：直接 merge 進別人的程式時用這支。

給整合方的兩個函式就夠了：

    import zone_map

    # 1) 程式啟動時呼叫一次（載入分區圖、開啟三層快取）
    zone_map.init()

    # 2) 需要時才出圖
    png_bytes = zone_map.render_png(cases, zone_code="P001-00")

為什麼要 init()
--------------
三層快取與使用分區圖都是 module-level 狀態，只在行程記憶體內：

    使用分區圖    170 MB shapefile，34,190 筆，載入 1.8s   → 只該付一次
    路網網格快取  未啟用時 Overpass 佔總耗時 92%           → 決定性影響
    圖磚磁碟快取  一次出圖 140 張圖磚
    出圖結果快取  同條件直接回既有 PNG

實測耗時（同一台機器）：

    完全冷啟動（新區域）        60 ～ 143 s   ← 要向 Overpass 抓路網網格
    路網與圖磚已快取            4.3 ～ 9.3 s
    結果快取命中                0.06 s

所以：init() 一定要在啟動時呼叫，不要放在請求路徑上；
而且務必先用 prewarm_tiles.py 把目標區域預熱過再上線。

執行緒安全
----------
出圖流程共用 Pillow 畫布與 requests session，不是執行緒安全的。
本模組內建 Semaphore，超過 max_concurrency 的呼叫會排隊等待。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger("zone_map")

_DEFAULT_TILE_CACHE = os.environ.get("NLSC_TILE_CACHE", "_tile_cache")
_DEFAULT_ROAD_CACHE = os.environ.get("ROAD_CACHE_DIR", "_road_cache")
_DEFAULT_RESULT_CACHE = os.environ.get("ZONE_MAP_CACHE", "_result_cache")

_state_lock = threading.Lock()
_initialised = False
_render_gate: threading.BoundedSemaphore | None = None
_result_cache_dir: Path | None = None


@dataclass
class RenderInfo:
    """出圖結果的摘要，供呼叫方記錄或顯示。"""

    area_m2: float
    boundary_source: str
    zoom: int
    parcel_inside_ratio: float
    parcels: list[str]
    matched_roads: dict[str, str]
    direction_audit: dict[str, bool]
    road_labels: list[str]
    warnings: list[str]
    cache_hit: bool = False
    elapsed_seconds: float = 0.0
    cache_key: str = ""

    @property
    def all_directions_ok(self) -> bool:
        """四個方位在幾何上是否都成立；未檢核（無 OSM 幾何）視為未通過。"""

        return bool(self.direction_audit) and all(self.direction_audit.values())


@dataclass
class InitReport:
    """init() 的結果；上線前請檢查 font_path 與 zoning_features。"""

    zoning_features: int = 0
    zoning_seconds: float = 0.0
    tile_cache_dir: str = ""
    road_cache_dir: str = ""
    result_cache_dir: str = ""
    font_path: str | None = None
    cached_tiles: int = 0
    cached_road_cells: int = 0
    cached_results: int = 0
    problems: list[str] = field(default_factory=list)


def init(
    *,
    tile_cache_dir: str | Path | None = _DEFAULT_TILE_CACHE,
    road_cache_dir: str | Path | None = _DEFAULT_ROAD_CACHE,
    result_cache_dir: str | Path | None = _DEFAULT_RESULT_CACHE,
    zoning_shapefile: str | Path | None = None,
    max_concurrency: int = 2,
    preload_zoning: bool = True,
    force: bool = False,
) -> InitReport:
    """啟動時呼叫一次：開快取、預載分區圖、檢查字型。

    傳 None 可關掉對應的快取層（不建議關路網快取，那是 92% 的耗時）。
    重複呼叫會直接回傳上次結果，除非 force=True。
    """

    global _initialised, _render_gate, _result_cache_dir

    with _state_lock:
        if _initialised and not force:
            return _last_report

        import render_parcel_map
        import road_cache

        report = InitReport()

        target = render_parcel_map.set_tile_cache_dir(tile_cache_dir)
        report.tile_cache_dir = str(target) if target else "（未啟用）"
        if tile_cache_dir is None:
            report.problems.append("圖磚快取未啟用：每次出圖會抓 140 張圖磚")

        if road_cache_dir is None:
            report.road_cache_dir = "（未啟用）"
            report.problems.append(
                "路網快取未啟用：Overpass 會佔總耗時 92%，同步呼叫會等 60 秒以上"
            )
        else:
            road_target = road_cache.install(road_cache_dir)
            report.road_cache_dir = str(road_target) if road_target else "（未啟用）"

        if result_cache_dir is None:
            _result_cache_dir = None
            report.result_cache_dir = "（未啟用）"
        else:
            _result_cache_dir = Path(result_cache_dir)
            _result_cache_dir.mkdir(parents=True, exist_ok=True)
            report.result_cache_dir = str(_result_cache_dir)

        # 沒有中文字型時 Pillow 會退回點陣字型，路名會整排變豆腐框。
        report.font_path = render_parcel_map.resolve_font_path()
        if report.font_path is None:
            report.problems.append(
                "找不到中文字型：路名與地號會變成豆腐框。"
                "請安裝 fonts-noto-cjk 或用 ZONE_MAP_FONT 指定字型檔"
            )

        if preload_zoning:
            started = time.perf_counter()
            try:
                from render_zone_boundary import load_zoning_layer

                layer = load_zoning_layer(
                    Path(zoning_shapefile) if zoning_shapefile else None
                )
                report.zoning_features = len(layer)
                report.zoning_seconds = time.perf_counter() - started
            except Exception as exc:  # noqa: BLE001 - 啟動診斷要看到任何失敗
                report.problems.append(f"使用分區圖載入失敗：{exc}")

        report.cached_tiles = _count(tile_cache_dir, "*.tile")
        report.cached_road_cells = _count(road_cache_dir, "*.json.gz")
        report.cached_results = _count(result_cache_dir, "*.png")
        if report.cached_road_cells == 0 and road_cache_dir is not None:
            report.problems.append(
                "路網快取為空：第一次出圖會向 Overpass 抓網格，可能等 60～140 秒。"
                "建議先跑 prewarm_tiles.py 預熱"
            )

        _render_gate = threading.BoundedSemaphore(max(1, max_concurrency))
        _initialised = True
        globals()["_last_report"] = report
        return report


_last_report = InitReport()


def _count(directory: str | Path | None, pattern: str) -> int:
    if directory is None:
        return 0
    root = Path(directory)
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob(pattern))


def _normalise_cases(cases) -> list[dict]:
    """接受 dict、dict 陣列，或條件 JSON 的路徑。"""

    if isinstance(cases, (str, Path)):
        from render_zone_boundary import load_cases

        return load_cases(cases)
    if isinstance(cases, dict):
        return [cases]
    return [dict(case) for case in cases]


def _cache_key(cases: list[dict], options: dict) -> str:
    blob = json.dumps(
        {"cases": cases, "options": options}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def render_png(
    cases,
    *,
    zone_code: str | None = None,
    county: str = "新北市",
    district: str | None = None,
    width: int = 1400,
    height: int = 1000,
    boundary_source: str = "zoning",
    include_alleys: bool = False,
    annotate_roads: bool = True,
    refresh: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[bytes, RenderInfo]:
    """出圖並回傳 (PNG bytes, 摘要)。

    cases 可以是單一 dict、dict 陣列，或條件 JSON 的檔案路徑。
    同一區段有多筆比準地時放成陣列，四方位條件必須一致。

    timeout_seconds 是「等待併發額度」的上限，不是出圖本身的上限：
    出圖無法安全中斷（外部請求與檔案寫入進行中），要限時請由呼叫方
    自行放到背景工作處理。
    """

    if not _initialised:
        # 沒 init 也能跑，但會用預設值且每次重讀分區圖，明確警告。
        logger.warning("zone_map.init() 未呼叫，改用預設快取設定")
        init()

    from render_zone_boundary import RELATIONS, render_zone_boundary

    normalised = _normalise_cases(cases)
    if not normalised:
        raise ValueError("cases 為空")

    # 多筆比準地必須共用同一組界線條件，否則是不同區段。
    first = normalised[0].get("constraints") or {}
    for index, case in enumerate(normalised[1:], start=2):
        constraints = case.get("constraints") or {}
        differing = [
            relation
            for relation in (*RELATIONS, "zone")
            if constraints.get(relation) != first.get(relation)
        ]
        if differing:
            raise ValueError(
                f"第 1 筆與第 {index} 筆的 {'、'.join(differing)} 不同，"
                "屬於不同區段，請分成兩次呼叫"
            )

    options = {
        "zone_code": zone_code,
        "county": county,
        "district": district,
        "width": width,
        "height": height,
        "boundary_source": boundary_source,
        "include_alleys": include_alleys,
        "annotate_roads": annotate_roads,
    }
    key = _cache_key(normalised, options)

    if _result_cache_dir is not None and not refresh:
        cached = _result_cache_dir / f"{key}.png"
        if cached.exists():
            sidecar = cached.with_suffix(".json")
            info = RenderInfo(
                area_m2=0.0,
                boundary_source=boundary_source,
                zoom=0,
                parcel_inside_ratio=0.0,
                parcels=[],
                matched_roads={},
                direction_audit={},
                road_labels=[],
                warnings=[],
                cache_hit=True,
                cache_key=key,
            )
            if sidecar.exists():
                try:
                    saved = json.loads(sidecar.read_text(encoding="utf-8"))
                    for field_name, value in saved.items():
                        if hasattr(info, field_name):
                            setattr(info, field_name, value)
                    info.cache_hit = True
                    info.cache_key = key
                except (OSError, ValueError):
                    pass
            return cached.read_bytes(), info

    gate = _render_gate or threading.BoundedSemaphore(1)
    if not gate.acquire(timeout=timeout_seconds if timeout_seconds else None):
        raise TimeoutError("等待出圖額度逾時，目前併發已滿")

    started = time.perf_counter()
    try:
        with TemporaryDirectory(prefix="zone-map-") as workspace:
            case_path = Path(workspace) / "case.json"
            case_path.write_text(
                json.dumps(normalised, ensure_ascii=False), encoding="utf-8"
            )
            output_path = Path(workspace) / "boundary.png"
            result = render_zone_boundary(
                case_path,
                output_path,
                county=county,
                district=district,
                zone_code=zone_code,
                width=width,
                height=height,
                boundary_source=boundary_source,
                include_alleys=include_alleys,
                annotate_roads=annotate_roads,
            )
            payload = output_path.read_bytes()
    finally:
        gate.release()

    info = RenderInfo(
        area_m2=round(result.area_m2, 1),
        boundary_source=result.boundary_source,
        zoom=result.zoom,
        parcel_inside_ratio=round(result.parcel_inside_ratio, 4),
        parcels=[item.code for item in result.parcels],
        matched_roads=dict(result.matched_roads),
        direction_audit=dict(result.direction_audit),
        road_labels=list(result.road_labels),
        warnings=list(result.warnings),
        cache_hit=False,
        elapsed_seconds=round(time.perf_counter() - started, 2),
        cache_key=key,
    )

    if _result_cache_dir is not None:
        try:
            _result_cache_dir.mkdir(parents=True, exist_ok=True)
            (_result_cache_dir / f"{key}.png").write_bytes(payload)
            (_result_cache_dir / f"{key}.json").write_text(
                json.dumps(
                    {
                        "area_m2": info.area_m2,
                        "boundary_source": info.boundary_source,
                        "zoom": info.zoom,
                        "parcel_inside_ratio": info.parcel_inside_ratio,
                        "parcels": info.parcels,
                        "matched_roads": info.matched_roads,
                        "direction_audit": info.direction_audit,
                        "road_labels": info.road_labels,
                        "warnings": info.warnings,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("結果快取寫入失敗：%s", exc)

    return payload, info


def render_to_file(cases, output_path: str | Path, **kwargs) -> RenderInfo:
    """出圖並寫到指定路徑，回傳摘要。"""

    payload, info = render_png(cases, **kwargs)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return info


def cache_status() -> dict:
    """回傳三層快取的即時統計，供健康檢查或監控使用。"""

    import render_parcel_map
    import road_cache

    return {
        "initialised": _initialised,
        "tiles": render_parcel_map.tile_cache_stats(),
        "roads": road_cache.stats(),
        "cached_results": _count(_result_cache_dir, "*.png"),
        "font_path": _last_report.font_path,
        "zoning_features": _last_report.zoning_features,
    }
