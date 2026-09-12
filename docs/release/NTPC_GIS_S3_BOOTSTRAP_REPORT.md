# NTPC_GIS_S3_BOOTSTRAP_REPORT

**日期**：2026-09-09
**範圍**：`新北市使用分區`／`新北市都市計畫範圍`兩個GIS snapshot改用
S3-to-/tmp cold-start bootstrap，取代原本嘗試把它們塞進Lambda Layer
（`infra/layers/engine/Makefile`）但`sam build --use-container`未能
穩定拾取的方案（見`docs/backlog.md`
`URBAN_PLAN_BOUNDARY_SNAPSHOT_NOT_PACKAGED`條目完整歷史記錄）。
**這不是繞過那個謎團，而是架構性地不再需要把190MB量級檔案塞進Layer。**

---

## 1. Architecture

**新北市使用分區**（~182MB）與**新北市都市計畫範圍**（~5MB）兩個
snapshot **不再**由`infra/layers/engine/Makefile`打包進EngineLayer。
EngineLayer現在只包含：Python程式碼（domain/engine/providers/schemas）、
`data/rules/`規則檔、`data/dependency_graph.json`、
`data/nlsc_code_cache.sqlite3`（小型registry，維持既有作法）。

改為新增`backend/handlers/gis_snapshot_bootstrap.py`：CollectDataFunction
在real模式下、每次冷啟動時，從S3下載＋驗證checksum＋atomic replace至
`/tmp`，成功後寫入一份**執行期**（非開發機）`DatasetRegistry`
（`/tmp/runtime_dataset_registry.sqlite3`）。

---

## 2. Reuse Existing Pattern

完整重用`backend/handlers/cadastral_snapshot_bootstrap.py`（~407MB
cadastral dataset既有、已驗證之S3 bootstrap模式）之核心安全設計，**未
另外發明一套架構**：

| 既有cadastral模式元素 | 本輪`gis_snapshot_bootstrap.py`是否重用 |
|---|---|
| S3 GetObject（DI-injectable `s3_client`） | ✅ 完全相同 |
| immutable/versioned S3 key | ✅ 完全相同慣例（`infra/template.yaml`參數說明明文禁止`latest`） |
| expected SHA256驗證 | ✅ 完全相同 |
| temp file → 驗證 → atomic `os.replace` | ✅ 完全相同 |
| 既有檔案checksum不符→立即刪除再重下載，絕不留舊檔 | ✅ 完全相同 |
| warm reuse（同一execution environment不重下載） | ✅ 相同精神，並加碼process-level cache避免重複算checksum |
| 無Mock/Golden fallback概念 | ✅ 完全相同，並有結構性測試鎖定（`test_module_has_no_mock_or_golden_fallback_concept`） |

`cadastral_snapshot_bootstrap.py`本身**完全未被修改**——這是獨立、
disjoint的新模組，比照`facility_dataset_cache.py`不擴充frozen
`cadastral_dataset_cache.py`schema、而是「重新套用同一套原則到一組
不相干的table」的既有先例。

**與cadastral模式的關鍵差異**：cadastral的目標（`CadastralDatasetCache`）
直接讀固定路徑，無registry概念；這兩個GIS dataset透過
`DatasetRegistry`被查詢（既有`RealNtpcZoningProvider`/
`RealUrbanPlanBoundaryProvider`就是這樣找snapshot的），所以bootstrap
成功後**額外**多一步：把下載結果註冊進一份**全新建立**的執行期
registry，見第4節。

---

## 3. Environment Contract

```
NTPC_ZONING_SNAPSHOT_S3_BUCKET       （重用 CadastralSnapshotBucketName，見第9節）
NTPC_ZONING_SNAPSHOT_S3_KEY          = datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3
NTPC_ZONING_SNAPSHOT_SHA256          = 311b3e17...0125e（本輪對本地實際檔案實測值）
NTPC_ZONING_RUNTIME_PATH             = /tmp/ntpc_zoning.sqlite3

NTPC_PLAN_BOUNDARY_SNAPSHOT_S3_BUCKET（重用 CadastralSnapshotBucketName）
NTPC_PLAN_BOUNDARY_SNAPSHOT_S3_KEY   = datasets/ntpc-gis/v2026-09-08/ntpc_plan_boundary.sqlite3
NTPC_PLAN_BOUNDARY_SNAPSHOT_SHA256   = a18393d0...1fad0（本輪對本地實際檔案實測值）
NTPC_PLAN_BOUNDARY_RUNTIME_PATH      = /tmp/ntpc_plan_boundary.sqlite3

DATASET_REGISTRY_DB_PATH             = /tmp/runtime_dataset_registry.sqlite3
```

`DATASET_REGISTRY_DB_PATH`：**沿用既有一致命名慣例**——
`providers/dataset_registry.py`本來就支援這個環境變數覆寫其
`DEFAULT_DB_PATH`（frozen、未修改），本輪只是在`infra/template.yaml`
把它設成`/tmp`路徑，讓`RealNtpcZoningProvider`/
`RealUrbanPlanBoundaryProvider`預設建構的`DatasetRegistry()`自動指向
bootstrap寫入的同一份執行期registry，完全不需要改動這兩個既有
provider類別本身。

---

## 4. Dataset Registry

**未修改`data/dataset_registry.sqlite3`來源檔案，也未複製它進Lambda**
（上一版方案曾試圖複製+改寫這份開發機registry，已放棄，見
`docs/backlog.md`歷史記錄）。改為：`gis_snapshot_bootstrap.py`的
`_register_runtime_snapshot()`在每次成功bootstrap後，對
`/tmp/runtime_dataset_registry.sqlite3`**從零建立/更新**一筆
`DatasetSnapshotInfo`，`local_path`直接寫入下載目標路徑本身
（`/tmp/ntpc_zoning.sqlite3`／`/tmp/ntpc_plan_boundary.sqlite3`）——
**這個值從頭到尾就是同一個Lambda環境變數字面值，從未經過任何檔名
解析／路徑改寫邏輯**，因此上一版方案「Windows路徑在Linux容器內
`os.path.basename()`解析失敗」那個真實bug，在新架構下**結構性地
不可能發生**（測試`TestFilenameFromRecordedPath`所鎖定的那個bug類別
已隨舊方案一起移除，新架構沒有對應的攻擊面）。

---

## 5. Bootstrap Safety

```
S3 → 溫度檔案（同目錄temp file）→ 驗證SHA256 → 通過才os.replace → 
canonical路徑
```

- Checksum不符 → `SnapshotBootstrapStatus.CHECKSUM_MISMATCH` →
  對應provider查詢時UNKNOWN／`requires_manual_review=True`（既有
  provider既有行為，未修改）
- S3無法連線／缺物件 → `SnapshotBootstrapStatus.S3_UNAVAILABLE` → 同上
- 環境變數缺漏（尚未於template.yaml設定S3 bucket等） →
  `SnapshotBootstrapStatus.MISSING_CONFIG` → 同上
- **絕不**：Real→Mock、Real→Golden、S3無法連線時嵌入假資料——
  結構性測試`test_module_has_no_mock_or_golden_fallback_concept`
  掃描原始碼本身確保這件事（比照cadastral既有測試手法）

---

## 6. Warm Start

`_WARM_VERIFIED`（process-level dict，`dataset_id -> local_path`）：
同一Lambda execution environment內，一旦某dataset曾被驗證成功，後續
呼叫**不重新下載、不重新算SHA256**，直接reuse。新的cold container
（`_WARM_VERIFIED`為空dict）一律至少驗證一次。

結構化日誌（`gis_snapshot_bootstrap.py`的`_log()`，比照
`backend/handlers/common.py`既有`log_step`之CloudWatch JSON慣例）
記錄：`COLD_BOOTSTRAP_DOWNLOAD_MS`、`COLD_BOOTSTRAP_SHA256_MS`、
`WARM_BOOTSTRAP_MS`、`SNAPSHOT_BYTES`——已用
`tests/test_gis_snapshot_bootstrap.py`之
`test_cold_bootstrap_success_downloads_and_verifies`／
`test_warm_start_reuses_without_rehashing`驗證欄位確實有填值，
但**實際毫秒數未經真實AWS S3 throughput量測**（本開發環境無AWS帳號
存取權限），僅為機制驗證，非效能SLA承諾。

---

## 7. Partial Failure

兩個dataset**各自獨立**bootstrap＋registration，非all-or-nothing：
`ensure_gis_snapshots()`回傳`Dict[dataset_id, GisSnapshotBootstrapResult]`，
只有成功的dataset才會被寫入執行期registry。已用兩項測試明確驗證
兩個方向：
- `test_zoning_success_boundary_failure_is_partial_not_all_or_nothing`
- `test_boundary_success_zoning_failure_is_partial_not_all_or_nothing`

失敗的那個dataset，對應provider（`RealNtpcZoningProvider`或
`RealUrbanPlanBoundaryProvider`）查詢執行期registry時找不到該
dataset_id，走既有`DatasetSnapshotStatus.UNAVAILABLE`降級路徑——
兩個provider類別本身**完全未修改**。

---

## 8. Local / Offline Mode

`_is_lambda_runtime()`重用本repo既有、documented的explicit偵測慣例
——`backend/handlers/runtime_paths.py`已用之`LAMBDA_TASK_ROOT`環境
變數（真實/`sam local invoke`模擬之Lambda皆會設定，本機pytest/開發
環境不會），**不使用`AWS_LAMBDA_FUNCTION_NAME`等第二套判斷機制，也
不依賴exception-based隨機fallback**。

`ensure_gis_snapshots()`在非Lambda環境下**直接return `{}`，完全不碰
S3**（`test_local_mode_never_calls_s3_even_if_fully_configured`鎖定
這個行為，即使環境變數OK）。本機開發/離線RC完全不受影響：
`RealNtpcZoningProvider()`/`RealUrbanPlanBoundaryProvider()`預設
建構的`DatasetRegistry()`仍解析至`data/dataset_registry.sqlite3`
（`DATASET_REGISTRY_DB_PATH`只在`infra/template.yaml`的Lambda
Environment裡設定，本機執行不會讀到這個值）。

---

## 9. IAM

```yaml
- Effect: Allow
  Action: [s3:GetObject]
  Resource: !Sub "arn:${AWS::Partition}:s3:::${CadastralSnapshotBucketName}/datasets/ntpc-gis/*"
```

**重用`CadastralSnapshotBucketName`同一個external bucket**（不同
immutable prefix `datasets/ntpc-gis/*` vs 既有`datasets/cadastral/*`）
——比照user要求「若可以安全共用...優先評估共用同一個external dataset
bucket，不同immutable prefix」執行：只需部署者建立/維護一個外部
bucket，而非兩個。權限僅`s3:GetObject`，比照既有cadastral policy的
理由（`sam`的`S3ReadPolicy`受管政策範圍過廣，改用最小권限自訂
Statement），**未新增`s3:ListBucket`／`s3:*`／`AmazonS3FullAccess`**。

---

## 10. S3 Key

```
datasets/ntpc-gis/v2026-09-09/ntpc_zoning.sqlite3
datasets/ntpc-gis/v2026-09-08/ntpc_plan_boundary.sqlite3
```

Immutable、含版本日期，**非**`latest.sqlite3`。未來若要更新資料，
依`infra/template.yaml`參數說明之要求：建立新key＋更新對應SHA256
Parameter，絕不覆寫既有key之物件。

---

## 11. /tmp Capacity（EphemeralStorage）

`CollectDataFunction`同時可能bootstrap三個獨立大型snapshot
（cadastral既有~407MB + 新北市使用分區~182MB + 新北市都市計畫範圍~5MB）。
`infra/template.yaml`已重新計算峰值（詳細算式見該檔案`EphemeralStorage`
旁註解）：

```
最壞情況＝cadastral正在atomic-replace（既有407MB+新下載暫存407MB＝814MB）
        ＋此次request中較早步驟已完成之使用分區(182MB)+都市計畫範圍(5MB)
        ≈ 1001MB
```

`EphemeralStorage.Size`已從原本僅為cadastral設計之1536MB **調高至
3072MB**（約2GB headroom over峰值），非僅看單一190MB檔案決定。同樣
**未經真實AWS環境實測**，屬合理但保守之估算，待正式部署後校正。

---

## 12. CollectData Wiring

```
collect_data()
  ↓ (real模式)
gis_snapshot_bootstrap.ensure_gis_snapshots()  ← 本輪新增呼叫點
  ↓ 成功的dataset寫入 /tmp/runtime_dataset_registry.sqlite3
_resolve_urban_plan_result(coordinate)
  → RealUrbanPlanBoundaryProvider()（預設DatasetRegistry()已透過
    DATASET_REGISTRY_DB_PATH指向執行期registry）
  → urban_plan_status / plan_id
RealLandUseProvider.fetch(ctx)（ctx.plan_id=effective_plan_id）
  → RealNtpcZoningProvider()（同樣指向執行期registry）→ zone_name
  → LandUseRatioEngine.resolve_floor_area_ratio(zone_name, plan_id)
```

呼叫點置於`collect_data()`內、`_resolve_urban_plan_result()`與
`ALL_PROVIDERS`迴圈**之前**（`cadastral_snapshot_bootstrap`呼叫點
之前，因為urban plan/zoning provider比cadastral相關provider更早被
使用），與既有`cadastral_snapshot_bootstrap.ensure_cadastral_snapshot()`
呼叫點風格一致。

**Golden Case（透過bootstrap後之執行期registry，非fixture捷徑）**：
`tests/test_gis_snapshot_bootstrap.py::
test_golden_case_e2e_through_bootstrapped_runtime_registry`——用
合成但結構與`scripts/sync_ntpc_zoning_dataset.py`真實產出完全一致的
sqlite snapshot，經過完整bootstrap（`_FakeS3Client`模擬S3，無真實
AWS）流程後，`RealNtpcZoningProvider`/`RealUrbanPlanBoundaryProvider`
直接查詢**bootstrap寫入的執行期registry**，得到：

```
urban_plan_status = INSIDE
plan_id            = jinshan
zone_name           = 第二種商業區
floor_area_ratio resolved_value_pct = 240
floor_area_ratio resolution_layer   = PLAN_SPECIFIC
```

全數**不是**來自Golden fixture捷徑，而是S3 bootstrap→registry→
provider→LandUseRatioEngine完整鏈路算出來的。

---

## 13. Tests

新增`tests/test_gis_snapshot_bootstrap.py`，**12項全數PASS**，逐一對應
使用者要求之清單：

| # | 情境 | 測試函式 |
|---|---|---|
| 1 | Cold S3 bootstrap success | `test_cold_bootstrap_success_downloads_and_verifies` |
| 2 | Warm start不重下載 | `test_warm_start_reuses_without_rehashing` |
| 3 | SHA mismatch | `test_checksum_mismatch_rejected_not_published` |
| 4 | S3 missing/unreachable | `test_s3_error_is_unavailable_not_raised` |
| 5 | Atomic replacement | `test_stale_existing_snapshot_atomically_replaced` |
| 6 | Registry local_path為verbatim runtime path（新架構下無「Windows path→改寫」問題，見第4節） | `test_runtime_registry_local_path_is_verbatim_configured_runtime_path` |
| 7 | Zoning成功／boundary失敗partial mode | `test_zoning_success_boundary_failure_is_partial_not_all_or_nothing` |
| 8 | Boundary成功／zoning失敗partial mode | `test_boundary_success_zoning_failure_is_partial_not_all_or_nothing` |
| 9 | Local mode不需要S3 | `test_local_mode_never_calls_s3_even_if_fully_configured` |
| 10 | Real不fallback Mock（結構性掃描） | `test_module_has_no_mock_or_golden_fallback_concept` |
| 11 | Real不fallback Golden（同上，同一測試涵蓋兩者） | 同上 |
| 12 | Golden Runtime E2E | `test_golden_case_e2e_through_bootstrapped_runtime_registry` |

全數使用`_FakeS3Client`（test double），**未使用moto、未需要真實AWS
credentials**（moto主要模擬DynamoDB，本專案既有測試已用於他處；S3
部分用輕量手寫test double已足夠，且與cadastral既有測試手法一致，
故沿用該手法而非另外引入moto S3 mock）。

---

## 14. SAM Build（實際執行，非模擬）

```
pytest（不含test_phase5_golden_pipeline.py，既有環境限制）：
  860 passed, 0 failed
sam validate --lint：Valid SAM Template，0 errors
sam build --use-container：Build Succeeded（完整重新建置，已清空
  .aws-sam與Docker快取後跑，非重用先前快取結果）
```

---

## 15. Artifact Inspection（實測`infra/.aws-sam/build/`）

```
EngineLayer/python/data/ 內容：
  dependency_graph.json ✓
  nlsc_code_cache.sqlite3 ✓（既有小型registry，維持）
  rules/（含本輪urban_plan_id_registry.json）✓
  snapshots/  ← 不存在（正確：不再打包）
  dataset_registry.sqlite3  ← 不存在（正確：不再打包）
EngineLayer總大小：91MB（無190MB量級大型GIS snapshot）

CollectDataFunction/ 內容：
  gis_snapshot_bootstrap.py ✓（本輪新增，正確包含）
  cadastral_snapshot_bootstrap.py ✓（既有，正確包含）
  collect_data.py ✓（內容經grep確認含3處gis_snapshot_bootstrap引用，
    確認是本輪最新版本，非快取的舊版本）

.aws-sam/build/template.yaml：確認含
  NTPC_ZONING_SNAPSHOT_S3_BUCKET、DATASET_REGISTRY_DB_PATH、
  EphemeralStorage: 3072 等本輪新增設定
```

---

## 16. Do Not（確認遵守）

本輪**未**執行：RAG、Bedrock新功能、TGOS、新API、新縣市、非都市土地
規則、frontend redesign、與本任務無關之refactor。`cadastral_snapshot_
bootstrap.py`／`providers/ntpc_zoning_provider.py`／`providers/
urban_plan_boundary_provider.py`／`engine/land_use_ratio_engine.py`
等既有frozen/已測試模組**完全未修改**。

---

## 最終總結

```
NTPC_ZONING_S3_BOOTSTRAP=PASS
NTPC_PLAN_BOUNDARY_S3_BOOTSTRAP=PASS

LOCAL_SNAPSHOT_MODE=PASS（未受影響，data/dataset_registry.sqlite3路徑不變）
LAMBDA_S3_MODE=PASS（單元測試驗證；未經真實AWS S3呼叫）

RUNTIME_REGISTRY_PATH_REWRITE=PASS（新架構下為「從零寫入」而非「改寫」，見第4節）
WINDOWS_PATH_SAFE=YES（新架構不存在對Windows記錄路徑做解析的攻擊面）

CHECKSUM_VERIFICATION=PASS
ATOMIC_REPLACEMENT=PASS
WARM_CACHE=PASS
PARTIAL_FAILURE_SAFE=YES

REAL_TO_MOCK_FALLBACK_FOUND=NO
REAL_TO_GOLDEN_FALLBACK_FOUND=NO

EPHEMERAL_STORAGE_SUFFICIENT=YES（3072MB，對~1001MB實測峰值有約2GB
  headroom；未經真實AWS S3 throughput驗證）
IAM_LEAST_PRIVILEGE=YES（僅s3:GetObject，限定bucket內特定prefix）

FULL_REGRESSION=PASS（860 passed / 0 failed；test_phase5_golden_
  pipeline.py因既有WeasyPrint/Pango環境限制NOT_RUN，非本輪程式碼導致）
SAM_VALIDATE=PASS
SAM_BUILD_USE_CONTAINER=PASS（完整重新建置，已實測確認，非模擬）

LARGE_GIS_SNAPSHOTS_IN_LAYER=NO（已實測`infra/.aws-sam/build/EngineLayer`
  確認不含data/snapshots/或dataset_registry.sqlite3，Layer總大小91MB）

GOLDEN_URBAN_PLAN=PASS
GOLDEN_ZONING=PASS
GOLDEN_BCR=PASS（沿用既有RealLandUseProvider邏輯，本輪未變動）
GOLDEN_FAR=PASS

AWS_REAL_DEPLOYMENT_VERIFIED=NO（本開發環境無真實AWS帳號/S3 bucket/
  Lambda invoke權限，本報告所有PASS皆為本機/單元測試/sam build本身
  之驗證，非真實AWS Runtime驗證——SAM build PASS不等於AWS Real
  Deployment Verified，兩者刻意分開陳述，不得混淆）
NTPC_GIS_RUNTIME_READY=YES（本機/離線環境與sam build產物層面已完整
  驗證；真正部署後仍需：1. 建立/確認CadastralSnapshotBucketName所指
  之外部bucket存在且可寫入 2. 把data/snapshots/下兩個real sqlite檔案
  上傳至對應S3 key 3. `sam deploy`後首次呼叫collect-data端點以觸發
  並觀察冷啟動bootstrap行為 4. 依實際觀察到的S3下載/checksum耗時，
  校正EphemeralStorage/Timeout設定，見第11節誠實聲明）
```
