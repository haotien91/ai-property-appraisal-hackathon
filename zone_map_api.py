"""地價區段圖 HTTP API（方案 B：同步回傳 PNG）。

設計取捨
--------
出圖冷啟動實測 26～73 秒，主要花在抓 140 張 NLSC 圖磚。
同步 API 要能用，必須讓圖磚幾乎都命中快取，因此：

  * 圖磚磁碟快取（NLSC_TILE_CACHE）→ 第二次起不再打 NLSC
  * 出圖結果快取（ZONE_MAP_CACHE）→ 同條件直接回既有 PNG
  * 使用分區圖記憶體快取 → 常駐行程只讀一次 170 MB shapefile
  * 併發閘門 → 同時出圖數上限，避免多人請求把 NLSC 打爆

未命中快取的請求仍可能要數十秒。正式對外請先跑 prewarm_tiles.py
把目標區域的圖磚抓齊，或改用方案 A（非同步 job）。

啟動：
    uvicorn zone_map_api:app --host 0.0.0.0 --port 8080 --workers 1

--workers 1 是刻意的：分區圖與圖磚快取都在行程記憶體內，
多 worker 會各讀一份 170 MB，記憶體翻倍且無好處。
要擴充請水平加容器，不要加 worker。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

import render_parcel_map
import road_cache
from render_zone_boundary import RELATIONS, render_zone_boundary

logger = logging.getLogger("zone_map_api")

# --- 設定（全部走環境變數，容器不用改程式碼） ---------------------------------

TILE_CACHE_DIR = os.environ.get("NLSC_TILE_CACHE", "/var/cache/nlsc-tiles")
ROAD_CACHE_DIR = os.environ.get("ROAD_CACHE_DIR", "/var/cache/nlsc-roads")
RESULT_CACHE_DIR = os.environ.get("ZONE_MAP_CACHE", "/var/cache/zone-maps")
API_KEYS = {
    key.strip()
    for key in os.environ.get("ZONE_MAP_API_KEYS", "").split(",")
    if key.strip()
}
MAX_CONCURRENT_RENDERS = int(os.environ.get("ZONE_MAP_MAX_CONCURRENCY", "2"))
ZONING_SHAPEFILE = os.environ.get("NTPC_ZONING_SHP")

# 出圖不是執行緒安全的（Pillow 畫布、requests session 共用），
# 且同時出圖越多對 NLSC 越不友善，因此用 Semaphore 限流。
_render_gate = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)


# --- 請求／回應模型 -----------------------------------------------------------


class Constraints(BaseModel):
    north_of: str = Field(..., description="區段在該路之北 → 該路在南側")
    west_of: str = Field(..., description="區段在該路之西 → 該路在東側")
    south_of: str = Field(..., description="區段在該路之南 → 該路在北側")
    east_of: str = Field(..., description="區段在該路之東 → 該路在西側")
    zone: str = Field(..., description="使用分區，例：第一種住宅區")


class Section(BaseModel):
    name: str = Field(..., description="段名，例：太平段")
    code: str = Field(..., description="段代碼 4 碼，例：1921")


class Case(BaseModel):
    section: Section
    parcel: str = Field(..., description="地號，只填數字")
    constraints: Constraints


class ZoneMapRequest(BaseModel):
    """一個區段一次請求；多筆比準地放在 cases 陣列裡。"""

    cases: list[Case] = Field(..., min_length=1)
    zone_code: str | None = Field(None, description="區段編號，例：P001-00")
    county: str = Field("新北市")
    district: str | None = Field(None, description="行政區，預設由段籍推得")
    width: int = Field(1400, ge=400, le=4000)
    height: int = Field(1000, ge=400, le=4000)
    boundary_source: Literal["zoning", "cadastral", "roads", "auto"] = "zoning"
    include_alleys: bool = False
    annotate_roads: bool = True
    refresh: bool = Field(False, description="true 則忽略結果快取重新出圖")


# --- 認證 --------------------------------------------------------------------


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """未設定 ZONE_MAP_API_KEYS 時不驗證，方便本機開發。

    對外部署務必設定：這支服務會代你去打 NLSC，
    沒有驗證等於把你的 IP 借給任何人。
    """

    if not API_KEYS:
        return
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="X-API-Key 不正確或缺少")


# --- 快取 --------------------------------------------------------------------


def _cache_key(request: ZoneMapRequest) -> str:
    """只納入會影響圖面的欄位；refresh 不列入，否則永遠不會命中。"""

    payload = request.model_dump(exclude={"refresh"})
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _result_path(key: str) -> Path:
    return Path(RESULT_CACHE_DIR) / f"{key}.png"


# --- 出圖 --------------------------------------------------------------------


def _render_to_bytes(request: ZoneMapRequest) -> tuple[bytes, dict]:
    """把請求寫成暫存 JSON 後呼叫既有出圖流程，回傳 (PNG bytes, 摘要)。

    出圖端以檔案為輸入介面，這裡不改動它，避免動到已驗證的邏輯。
    """

    cases = [case.model_dump() for case in request.cases]

    with TemporaryDirectory(prefix="zone-map-") as workspace:
        case_path = Path(workspace) / "case.json"
        case_path.write_text(
            json.dumps(cases, ensure_ascii=False), encoding="utf-8"
        )
        output_path = Path(workspace) / "boundary.png"

        result = render_zone_boundary(
            case_path,
            output_path,
            county=request.county,
            district=request.district,
            zone_code=request.zone_code,
            width=request.width,
            height=request.height,
            boundary_source=request.boundary_source,
            include_alleys=request.include_alleys,
            annotate_roads=request.annotate_roads,
        )
        payload = output_path.read_bytes()

    summary = {
        "area_m2": round(result.area_m2, 1),
        "boundary_source": result.boundary_source,
        "zoom": result.zoom,
        "parcel_inside_ratio": round(result.parcel_inside_ratio, 4),
        "parcels": [item.code for item in result.parcels],
        "matched_roads": result.matched_roads,
        "direction_audit": result.direction_audit,
        "road_labels": list(result.road_labels),
        "warnings": result.warnings,
    }
    return payload, summary


# --- 應用程式 ----------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)

    render_parcel_map.set_tile_cache_dir(TILE_CACHE_DIR)
    # 路網快取是同步 API 的關鍵：未安裝時 Overpass 佔總耗時 92%。
    road_cache.install(ROAD_CACHE_DIR)
    Path(RESULT_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    logger.info("圖磚快取：%s", TILE_CACHE_DIR)
    logger.info("路網快取：%s", ROAD_CACHE_DIR)
    logger.info("結果快取：%s", RESULT_CACHE_DIR)
    if not API_KEYS:
        logger.warning("未設定 ZONE_MAP_API_KEYS，此服務目前不做驗證")

    # 啟動就把 170 MB 分區圖讀進記憶體，別讓第一個請求付這個成本。
    started = time.perf_counter()
    try:
        from render_zone_boundary import load_zoning_layer

        layer = load_zoning_layer(Path(ZONING_SHAPEFILE) if ZONING_SHAPEFILE else None)
        app.state.zoning_features = len(layer)
        logger.info(
            "使用分區圖已載入：%s 筆，耗時 %.1f 秒",
            f"{len(layer):,}",
            time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001 - 啟動診斷需要看到任何失敗原因
        app.state.zoning_features = 0
        logger.error("使用分區圖載入失敗：%s", exc)

    yield


app = FastAPI(
    title="地價區段圖 API",
    version="1.0",
    description="輸入段號地號與四方位界線道路，回傳標註好的區段圖 PNG。",
    lifespan=lifespan,
)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    """存活與就緒檢查；ALB / ECS health check 指這裡。"""

    ready = bool(getattr(app.state, "zoning_features", 0))
    return {
        "status": "ok" if ready else "degraded",
        "zoning_features": getattr(app.state, "zoning_features", 0),
        "tile_cache": render_parcel_map.tile_cache_stats(),
        "road_cache": road_cache.stats(),
        "auth_enabled": bool(API_KEYS),
        "max_concurrency": MAX_CONCURRENT_RENDERS,
    }


@app.post(
    "/zone-map",
    tags=["render"],
    responses={200: {"content": {"image/png": {}}, "description": "區段圖 PNG"}},
    dependencies=[Depends(require_api_key)],
)
def create_zone_map(request: ZoneMapRequest) -> Response:
    """同步出圖並回傳 PNG。

    出圖摘要（面積、方位檢核、警告）放在回應標頭，
    讓呼叫方不用另外開一支 endpoint 也能拿到診斷資訊。
    """

    # 多筆比準地必須共用同一組界線條件，否則是不同區段。
    first = request.cases[0].constraints
    for index, case in enumerate(request.cases[1:], start=2):
        differing = [
            relation
            for relation in (*RELATIONS, "zone")
            if getattr(case.constraints, relation) != getattr(first, relation)
        ]
        if differing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"第 1 筆與第 {index} 筆的 {'、'.join(differing)} 不同，"
                    "屬於不同區段，請分成兩次請求"
                ),
            )

    key = _cache_key(request)
    cached = _result_path(key)
    if cached.exists() and not request.refresh:
        logger.info("結果快取命中 %s", key)
        return Response(
            content=cached.read_bytes(),
            media_type="image/png",
            headers={"X-Zone-Map-Cache": "hit", "X-Zone-Map-Key": key},
        )

    if not _render_gate.acquire(timeout=5):
        raise HTTPException(
            status_code=503,
            detail=(
                f"出圖併發已達上限（{MAX_CONCURRENT_RENDERS}），請稍後重試"
            ),
        )
    started = time.perf_counter()
    try:
        payload, summary = _render_to_bytes(request)
    except ValueError as exc:
        # 條件本身不成立（查無地號、分區找不到）屬於呼叫方的問題。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("出圖失敗")
        raise HTTPException(status_code=502, detail=f"出圖失敗：{exc}") from exc
    finally:
        _render_gate.release()

    elapsed = time.perf_counter() - started
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(payload)
    except OSError as exc:
        logger.warning("結果快取寫入失敗：%s", exc)

    logger.info(
        "出圖完成 %s：%.1f 秒，%s m²",
        key,
        elapsed,
        f"{summary['area_m2']:,.0f}",
    )
    return Response(
        content=payload,
        media_type="image/png",
        headers={
            "X-Zone-Map-Cache": "miss",
            "X-Zone-Map-Key": key,
            "X-Zone-Map-Elapsed": f"{elapsed:.1f}",
            "X-Zone-Map-Area-M2": f"{summary['area_m2']:.1f}",
            "X-Zone-Map-Summary": _summary_header(summary),
        },
    )


# HTTP header 只允許 latin-1，路名與警告是中文，必須轉成 \uXXXX。
# 另外 header 總量通常上限 8 KB，過長就截斷並改請呼叫方走 /summary。
_SUMMARY_HEADER_LIMIT = 6000


def _summary_header(summary: dict) -> str:
    encoded = json.dumps(summary, ensure_ascii=True)
    if len(encoded) <= _SUMMARY_HEADER_LIMIT:
        return encoded
    compact = {
        key: summary[key]
        for key in ("area_m2", "boundary_source", "zoom", "parcel_inside_ratio")
        if key in summary
    }
    compact["truncated"] = True
    return json.dumps(compact, ensure_ascii=True)


@app.post(
    "/zone-map/summary",
    tags=["render"],
    dependencies=[Depends(require_api_key)],
)
def create_zone_map_summary(request: ZoneMapRequest) -> dict:
    """同樣出圖，但回 JSON 摘要而非 PNG；給只想看檢核結果的呼叫方。"""

    if not _render_gate.acquire(timeout=5):
        raise HTTPException(status_code=503, detail="出圖併發已達上限，請稍後重試")
    started = time.perf_counter()
    try:
        payload, summary = _render_to_bytes(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        _render_gate.release()

    key = _cache_key(request)
    try:
        target = _result_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    except OSError:
        pass

    return {
        "key": key,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "png_bytes": len(payload),
        **summary,
    }
