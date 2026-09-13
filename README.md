# 地價區段圖產製

輸入段號地號與四方位界線道路條件，在 NLSC 光柵底圖上框出地價區段範圍、
填色比準地宗地、標註界線路名與區段編號，輸出 PNG。

```
座標 + 基本資料 + 區段描述
        │
        ├─ NLSC TownVillagePointQuery → 段名／段代碼（不交給 LLM 猜）
        ├─ LLM → 四方位條件 JSON → 驗證與校正
        │
        └─ 使用分區圖 + NLSC 圖磚 + OSM 路網 → 區段圖 PNG
```

## 安裝

```bash
pip install -r requirements.txt
```

需要中文字型。Linux 請安裝 `fonts-noto-cjk`，否則路名與地號會變成豆腐框
（`zone_map.init()` 會檢查並警告）。

### 參考資料（未隨程式碼發佈）

新北市使用分區 shapefile 合計 179 MB，單一 `.shp` 就 162 MB，超過 GitHub
的 100 MB 單檔上限，因此不在 repo 內。請自行下載並解壓到專案根目錄：

```
新北市使用分區/
  新北市使用分區.shp
  新北市使用分區.dbf
  新北市使用分區.shx
  新北市使用分區.prj
  新北市使用分區.cpg
```

來源：新北市政府開放資料平台「使用分區」圖資（EPSG:3826，34,190 筆，
欄位僅 `ZONE`，含 600 種分區；「道路用地」8,575 筆本身即構成區段分隔）。

也可以改用 GeoParquet（體積約三分之一，適合放進容器 image）：

```python
import geopandas as gpd
layer = gpd.read_file("新北市使用分區/新北市使用分區.shp")
layer.to_crs("EPSG:3826")[["ZONE", "geometry"]].to_parquet(
    "zoning.parquet", compression="brotli"
)
```

再用環境變數指過去：`NTPC_ZONING_SHP=zoning.parquet`

### 預熱（**上線前必做**）

出圖耗時 92% 花在 Overpass 查詢。快取以 **2 km 地理網格**為單位，
不是以案件為單位 —— 整個行政區抓過一次，該區任何新案件都不再碰 Overpass。

```bash
# 樹林區整區，約 8 分鐘、1.3 MB
python road_cache.py --bbox 121.38 24.94 121.46 25.03

# 先估要抓幾格
python road_cache.py --bbox 121.38 24.94 121.46 25.03 --dry-run

# 順便暖圖磚並驗證環境
python prewarm_tiles.py P001.json P002.json P003.json P004.json
```

實測（同一台機器）：

| 情境 | 耗時 |
| --- | --- |
| 完全冷啟動（新區域，要抓路網網格） | 60 ～ 143 s |
| 路網與圖磚已快取 | 4.3 ～ 9.3 s |
| 出圖結果快取命中 | 0.06 s |

## 三種使用方式

### 1. 函式庫（merge 進其他程式）

```python
import zone_map

# 啟動時一次：載入 170 MB 分區圖、開啟三層快取
report = zone_map.init()
assert not report.problems, report.problems

# 需要時才出圖
png_bytes, info = zone_map.render_png(cases, zone_code="P001-00")
info = zone_map.render_to_file("P001.json", "out.png", zone_code="P001-00")
```

`init()` 必須在啟動時呼叫，不要放在請求路徑上。

### 2. 端到端 pipeline（含 LLM）

```python
from getSectionCode import run_pipeline

result = run_pipeline(
    longitude, latitude, basic_info, section_description,
    zone_code="P001-00",
    llm=my_llm,          # (prompt: str) -> str，見下方
)
```

LLM 是可注入的 callable（`llm(prompt: str) -> str`），實作放在 `llm_provider.py`。

### 接 Amazon Bedrock

用 Converse API，無伺服器、按 token 計費。需要的 IAM 權限只有
`bedrock:InvokeModel`。

憑證放哪裡、ECS task role 怎麼設、常見錯誤怎麼排除，見
[docs/CREDENTIALS.md](docs/CREDENTIALS.md)。摘要：**部署到 AWS 上時一把金鑰
都不要有**，用 IAM task role；本機開發用 `~/.aws` 的 profile，不要用
PowerShell 環境變數存 secret。

```powershell
pip install boto3

aws configure --profile hackathon        # 金鑰只存在 ~/.aws，不會誤 commit
$env:AWS_PROFILE="hackathon"
$env:AWS_REGION="us-east-1"
$env:BEDROCK_MODEL_ID="global.amazon.nova-2-lite-v1:0"

python llm_provider.py                    # 煙霧測試，會回報實際用到哪組憑證
```

Nova 2 等模型在美國以外呼叫時，裸的 `amazon.nova-2-lite-v1:0` 會被拒，
需要跨區推論的 profile id（`global.` / `us.` / `eu.` / `apac.` 前綴）。

```python
from getSectionCode import run_pipeline
from llm_provider import make_bedrock_llm

result = run_pipeline(
    longitude, latitude, basic_info, section_description,
    zone_code="P001-00",
    llm=make_bedrock_llm(),      # 讀 AWS_REGION 與 BEDROCK_MODEL_ID
)
```

CLI 直接接：

```bash
python getSectionCode.py run --lon 121.416165 --lat 24.982979 \
  --basic-info "..." --description "..." --zone-code P001-00 --llm bedrock
```

`temperature` 預設 0.0 —— 產生結構化 JSON 要低隨機性，否則同樣輸入會拆出
不同結果。

重試策略區分兩種失敗：限流／逾時會指數退避重試（帶抖動），而
`ValidationException`（model id 打錯）、`AccessDeniedException`（IAM 沒開）、
`max_tokens` 截斷都是確定性失敗，直接拋出不浪費時間重試。

CLI 逐步執行（還沒接 LLM 時）：

```bash
python getSectionCode.py prompt --lon 121.416165 --lat 24.982979 \
  --basic-info "新北市樹林區太平段367、917地號" \
  --description "沿東榮街以北、鎮前街411巷1弄以南、東榮街88巷以東、鎮前街367巷以西之第一種住宅區"

python getSectionCode.py save --response-file ai.txt --lon 121.416165 --lat 24.982979 -o P001.json
python getSectionCode.py render P001.json --zone-code P001-00 -o P001_boundary.png

# 或一次跑完並留檔
python getSectionCode.py run --lon 121.416165 --lat 24.982979 \
  --basic-info "..." --description "..." --zone-code P001-00 --llm-response-file ai.txt
```

### 3. HTTP API

```bash
uvicorn zone_map_api:app --host 0.0.0.0 --port 8080 --workers 1
```

`--workers 1` 是刻意的：分區圖與快取都在行程記憶體內，多 worker 會各讀一份
170 MB。要擴充請水平加容器。

```
GET  /segments                    列出 local_cases 裡可出圖的區段
POST /zone-map/from-description   由中文區段描述出圖（需 LLM）→ image/png
POST /zone-map                    已有結構化條件時的入口 → image/png
POST /zone-map/summary            同上但回 JSON 摘要
GET  /healthz                     就緒狀態與快取統計
```

摘要（面積、LLM 拆出的條件、方位檢核、校正紀錄、留檔路徑）放在
`X-Zone-Map-Summary` 標頭。

**描述入口**是給人用的：呼叫方不必自己把中文敘述拆成四個方位欄位。
最小請求只要一個欄位，其餘從 local_cases 讀：

```json
{ "segment_code": "P004-00" }
```

也可以完全指定，不碰 local_cases：

```json
{
  "basic_info": "新北市樹林區文林段317地號",
  "section_description": "沿潭興街以西、潭興街107巷21弄以東及以北、潭興街91巷以南之第一種住宅區",
  "zone_code": "P004-00"
}
```

兩者都給時以明確提供的值優先。每次請求都會留檔到 `artifacts/`（可用
`"archive": false` 關閉）。

LLM 未就緒時服務仍可啟動 —— 只有描述入口需要模型，缺憑證時回 503 並
說明該檢查什麼，`/zone-map` 與 `/healthz` 不受影響。

對外部署務必設定 `ZONE_MAP_API_KEYS`，否則不做驗證，等於把你的 IP
借給任何人去打 NLSC。

## 案件資料來源

出圖需要的兩個輸入都在地價智審的 workflow bundle 裡，不必人工轉抄：

```
bundle.segments.{區段編號}.request.base_parcel_id   完整地段地號
bundle.segments.{區段編號}.request.segment_scope    區段範圍描述
```

預設讀 `地價智審_AI_Offline_Candidate_v1/data/local_cases/*.json`，
可用 `LOCAL_CASES_DIR` 覆寫。同一區段出現在多個檔案時取 `created_at` 較晚者。

```python
from getSectionCode import get_segment, load_segments

load_segments()              # {區段編號: SegmentRequest}
get_segment("P004-00")       # .basic_info / .section_description
```

## 段籍索引（免每次查 API）

段代碼幾乎不變，但每次出圖都打 `ListLandSection` 很浪費：新北市 29 個
行政區、1,350 個地段，全查一輪要三十秒以上，還多一個對外失敗點。

```powershell
python getSectionCode.py build-index --county 新北市
```

產出 `data/land_sections.json`（165 KB）。實測索引命中時**零次網路請求、
0.6 毫秒**完成六次查詢。索引只是加速用的快取，查不到會自動回退 API，
檔案不存在也只印一行提示。

## 座標是選用的

座標在整條流程只用於「查段代碼」，之後所有幾何都以 NLSC 依地號回傳的
實際範圍為準。所以只要有完整地段地號就能出圖：

```powershell
python getSectionCode.py run `
  --basic-info "新北市樹林區太平段367、917地號" `
  --description "沿東榮街以北、鎮前街411巷1弄以南、東榮街88巷以東、鎮前街367巷以西之第一種住宅區" `
  --zone-code P003-00 --llm bedrock
```

一個限制：**段名跨行政區會重複**（新北市 1,218 種段名中有 72 種，例如
太平段在新店區是 0797、樹林區是 1921），所以縣市＋行政區＋段名三者都要給。

## 條件 JSON 格式

一個區段一份 JSON。有多筆比準地時用陣列，四方位條件必須相同。

```json
[
  {
    "section": { "name": "太平段", "code": "1921" },
    "parcel": "367",
    "constraints": {
      "north_of": "東榮街",
      "west_of":  "鎮前街367巷",
      "south_of": "鎮前街411巷1弄",
      "east_of":  "東榮街88巷",
      "zone": "第一種住宅區"
    }
  }
]
```

`north_of` 的語意是「區段在該路之北」，所以該路畫在**南側**。

## 快取與產出的分野

| | 路徑 | 檔名 | 可否刪除 | 用途 |
| --- | --- | --- | --- | --- |
| 快取 | `_road_cache` `_tile_cache` `_result_cache` | sha256 | 可以，自動重建 | 省時間 |
| 產出 | `artifacts/{區段編號}/{時間戳}/` | 人看得懂 | 不行 | 追溯稽核 |

每次 `run_pipeline()` 留 8 個檔，其中 `llm_raw.txt`（LLM 原始回覆）與
`corrections.json`（系統校正了什麼）是稽核關鍵 —— 日後有人質疑某張圖，
要能證明改過什麼、為什麼改。

上 AWS 後：產出 → S3（不可重建、要保留）；快取 → EFS 或 bake 進 image；
分區圖 → bake 進 image。

## 環境變數

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| `NTPC_ZONING_SHP` | `新北市使用分區/新北市使用分區.shp` | 分區圖路徑，支援 `.parquet` |
| `NLSC_TILE_CACHE` | `_tile_cache` | 圖磚快取目錄 |
| `ROAD_CACHE_DIR` | `_road_cache` | 路網網格快取目錄 |
| `ZONE_MAP_CACHE` | `_result_cache` | 出圖結果快取目錄 |
| `ZONE_MAP_ARCHIVE` | `artifacts` | 產出檔案庫根目錄 |
| `ZONE_MAP_FONT` | 自動偵測 | 指定中文字型檔 |
| `ZONE_MAP_API_KEYS` | 空（不驗證） | API key，逗號分隔 |
| `AWS_REGION` | 無 | Bedrock 區域 |
| `BEDROCK_MODEL_ID` | 無 | Bedrock model id |
| `LLM_PROVIDER` | `bedrock` | `bedrock` 或 `echo`（測試用，不連網） |
| `AWS_PROFILE` | 無 | 本機開發用的 `~/.aws` profile；正式環境不要設 |
| `LOCAL_CASES_DIR` | `地價智審_.../data/local_cases` | 案件 workflow JSON 目錄 |

以上都是**非機密設定**，可以放環境變數或 `.env`（見 `.env.example`）。
AWS 憑證不在此列，見 [docs/CREDENTIALS.md](docs/CREDENTIALS.md)。
| `NLSC_TILE_WORKERS` | `4` | 圖磚並行抓取數 |
| `ROAD_CACHE_CELL_DEGREES` | `0.02` | 路網網格邊長（度） |

## 已知限制

- **邊界止於道路用地邊緣**，不是路中心線。實測 P002 得 7,475 m²，
  人工參考值 9,213 m²，差額約等於周長 × 4.6 m（巷道半寬）。
- **OSM 缺漏巷弄**時方位檢核會顯示未通過，但不擋出圖（`--strict` 才擋）：
  P004 的 `潭興街107巷21弄` 實際為 L 形，OSM 只有直線段；
  P003 的 `啟智街187巷24弄` 在 OSM 完全不存在。
  界線文字位置由區段邊界的幾何決定，不依賴 OSM，因此仍會正確標註。
- **z21 起 NLSC 無圖磚**，`MAX_SAFE_ZOOM=20`。
- 出圖非執行緒安全，已由 Semaphore 限制併發。

## 實測結果

| 案例 | 比準地 | 面積 | 方位檢核 | 路名標註 |
| --- | --- | --- | --- | --- |
| P001 | 太平段 367、917 | 4,226 m² | 全通過 | 8 條 |
| P002 | 樹德段 284 | 7,475 m² | 全通過 | 9 條 |
| P003 | 樹德段 1415 | 1,906 m² | 3/4（OSM 缺路） | 8 條 |
| P004 | 文林段 317 | 4,283 m² | 2/4（OSM 缺 L 形） | 5 條 |

## 運作原理

地段地號怎麼抓、區段範圍怎麼決定、界線路名怎麼標、用了哪些 open data，
以及過程中踩過的坑，都寫在 [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)。

## 主要檔案

| 檔案 | 用途 |
| --- | --- |
| `zone_map.py` | 函式庫介面：`init()` / `render_png()` / `render_to_file()` |
| `getSectionCode.py` | 段籍查詢、LLM prompt、JSON 驗證校正、端到端 pipeline |
| `render_zone_boundary.py` | 出圖主邏輯：分區萃取、方位指派、路名標註 |
| `road_cache.py` | 路網網格快取（決定性的效能關鍵）與區域預熱 CLI |
| `render_parcel_map.py` | NLSC 圖磚抓取、拼接、磁碟快取、字型解析 |
| `nlsc_parcel_map.py` | 地號驗證與宗地真實輪廓 |
| `nlsc_map_url.py` | NLSC 圖台網址組裝 |
| `block_extract.py` | 由電子地圖萃取街廓（`boundary_source=cadastral`） |
| `zone_map_api.py` | FastAPI 服務 |
| `prewarm_tiles.py` | 圖磚與路網預熱 |
| `nlsc_http.py` | NLSC 的 TLS 相容 adapter 與 Overpass 請求標頭 |
| `llm_provider.py` | Bedrock Converse 呼叫、重試策略、`.env` 載入 |
| `test.py` | 八層整合測試（會呼叫真實服務，非 pytest） |
| `data/land_sections.json` | 新北市 1,350 個地段的代碼索引 |
| `boundary_core/` | 路名正規化、道路解析、方位檢核的底層工具 |
