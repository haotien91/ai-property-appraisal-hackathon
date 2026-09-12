# Phase 7 — Backend Deployment（API Gateway + Lambda）

## 現況（誠實聲明）

**尚未實際部署到AWS**。以下驗證已在本地完成，且結果附於本文件供覆核：

| 驗證項目 | 方法 | 結果 |
|---|---|---|
| 全部9個Lambda handler語法正確 | `python -m py_compile` | PASS |
| 全部9個handler可正確import（無模組衝突） | 直接import測試 | PASS（過程中發現並修正`pdf.py`與`pdf/`套件之模組名稱衝突，已重新命名為`pdf_handler.py`） |
| `cases.py`（list/create/get）邏輯正確性 | `moto`模擬DynamoDB，實際呼叫function並斷言回應內容 | PASS（過程中發現並修正2項真實bug：①`get_case_meta`遺失`updated_at`欄位；②`create_case`回傳自身暫存dict而非權威儲存值，皆已修正並重新測試確認） |
| `infra/template.yaml`語法正確性 | PyYAML解析 | PASS（過程中發現並修正7處`{id}`路徑字串未加引號導致YAML flow-mapping解析錯誤之問題） |
| `infra/statemachine/workflow.asl.json`結構正確性 | JSON解析＋狀態參照完整性檢查 | PASS（0個懸空狀態參照） |

**未驗證**：Container Image實際build（無Docker daemon/ECR存取權限）、
真實AWS帳號之IAM權限是否恰好足夠、Lambda冷啟動實際延遲、Bedrock模型
實際可用性（`explanation.py`之Bedrock呼叫僅語法正確，未實際呼叫過
Bedrock Runtime API）。

## 部署步驟（供具備AWS帳號權限者執行）

```bash
cd infra/

# 1. 建置（含Container Image build，需要Docker）
sam build --use-container

# 2. 首次部署（互動式設定Stack名稱、Region等）
sam deploy --guided

# 3. 取得輸出值
sam list stack-outputs --stack-name ai-valuation
#   ApiInvokeUrl        -> 填入前端js/config.js
#   FrontendCloudFrontUrl -> 前端正式URL
#   CasesTableName       -> 確認DynamoDB Table已建立

# 4. 部署Step Functions（SAM template目前未內含StateMachine資源，
#    需另外以下列指令部署，或後續版本併入template.yaml之
#    AWS::Serverless::StateMachine資源）
aws stepfunctions create-state-machine \
    --name ai-valuation-workflow \
    --definition file://statemachine/workflow.asl.json \
    --role-arn <StepFunctionsExecutionRole ARN>
```

## Lambda Layer vs Container Image 分工

| Function | 封裝方式 | 理由 |
|---|---|---|
| `cases` / `collect_data` / `analyze` / `complete_form` / `review` / `result` / `explanation` | Zip + `EngineLayer` | 純Python依賴（pydantic/boto3），Layer共用避免重複打包 |
| `pdf_handler` | Container Image | weasyprint需要Cairo/Pango/Noto CJK字型等系統層級依賴，見`backend/docker/pdf.Dockerfile` |

## 已知限制／TODO（誠實記錄）

1. `review.py`目前為**MVP簡化版本**：僅示範如何從`FormCompletionResult`
   重建部分`SubmittedFormData`並呼叫`AuditEngine`之骨架，**未完整**
   重建`CompetitionCase`與區域因素清單（此重建邏輯已在
   `tests/test_smart_review.py`中完整測試過，正式版應複用該測試檔案中
   之重建模式，而非重新設計）。此為本階段誠實揭露之範圍縮減，非隱藏
   之缺陷。
2. `infra/template.yaml`之`ExplanationFunction`與`NotifyManualReviewFunctionArn`
   （Step Functions ASL中引用）尚缺對應的Lambda資源定義完整串接
   （`explanation.py`程式碼已完成，但SAM template中的Step Functions
   StateMachine資源與Lambda Function ARN變數替換尚未完整串接，需部署時
   另行處理IAM權限與ARN傳遞）。
3. 未設定Lambda Provisioned Concurrency，首次呼叫會有冷啟動延遲
   （對黑客松Demo流量可接受，正式高流量情境應評估）。
4. DynamoDB Table目前無TTL設定，Demo案件資料會永久保留，正式環境應評估
   資料保留政策。

## Phase DEPLOY-1 — Local AWS Build & Runtime Packaging Acceptance（本輪新增）

本地工具鏈稽核：`aws`/`sam`/`docker`/`make`皆**未安裝**於本開發環境
（僅`python 3.14.0`可用），故本輪**無法**實際執行`sam validate`／
`sam build`／`sam local invoke`——依本輪明確指示，未自行安裝任何工具，
亦**未**以手動複製Makefile邏輯之方式冒充`SAM_BUILD_PASS`（此手法僅
於Phase API-2.3F針對NLSC snapshot packaging之**狹義**驗證中使用過，
本輪未延伸套用於「實際SAM build artifact」相關之判準）。

本輪透過純靜態檢查（讀取template.yaml/Makefile/Dockerfile/
requirements、`os.environ.get`呼叫交叉比對、檔案大小量測）發現兩項
**新問題**，皆為發現並回報，未修改：

1. **`data/cadastral_dataset_cache.sqlite3`（407MB）與`data/sources/`
   （400MB）結構上無法以現行Lambda Layer方式打包**（單一Layer與
   Function合計解壓縮上限250MB，407MB已單獨超過）。`infra/layers/
   engine/Makefile`目前正確地未嘗試打包此檔案，但這代表
   `RealExpropriationCaseProvider`/`RealLandPriceProvider`（Official
   Cadastral Evidence Pipeline，已Freeze）在**實際AWS部署**中會恆為
   「尚未同步」狀態，即使本地開發機已完成真實同步——此為本地可用不
   代表AWS package可用之具體案例。候選方案（本輪僅提出，不實作）：
   S3 + Lambda `/tmp`下載（搭配Ephemeral Storage設定，最高10GB）、
   EFS掛載、或改用真正的資料庫服務（如Aurora Serverless/DynamoDB）
   取代單一大型SQLite檔案。
2. **`backend/docker/pdf.Dockerfile`之`COPY data ${LAMBDA_TASK_ROOT}/
   data`複製整個`data/`目錄（含上述807MB）進PdfFunction Container
   Image**，但`pdf/`套件與`pdf_handler.py`逐行檢查後**完全不讀取**
   `data/rules/`、任何`.sqlite3`檔案、或`data/dependency_graph.json`
   ——雖Container Image有10GB上限、不構成build失敗，但屬不必要之
   image bloat（拖慢部署/冷啟動、浪費ECR儲存），與`EngineLayer`
   Makefile本身刻意窄化`data/`複製範圍的既有紀律不一致。候選修正
   （本輪僅提出，不實作）：比照EngineLayer，僅`COPY`
   `data/rules`與`data/dependency_graph.json`。

另確認：`EngineLayer`（pydantic-core／shapely，皆含compiled native
extension）與`ExtractionLayer`（pymupdf，`Makefile`自身docstring已
明文要求）**皆**須以`sam build --use-container`（或等效Linux建置
環境）建置，否則在本機（Windows）直接`pip install`會產生錯誤
平台之wheel，造成「`sam build`本機執行成功，但實際Lambda執行時
ImportError/架構不符」之隱藏風險——`DOCKER_TOOLCHAIN_BLOCKER = YES`。
`EngineLayer`/`ExtractionLayer`皆宣告`Metadata.BuildMethod: makefile`，
故`make`亦為獨立必要之blocker（`MAKE_TOOLCHAIN_BLOCKER = YES`），
即使`sam`/`docker`皆已具備仍會卡關。

環境變數稽核：`NLSC_CAD_API_ENABLED`／`NLSC_CAD_AUTH_CONTRACT_
VERIFIED`／`NLSC_CAD_API_USERNAME`／`NLSC_CAD_API_TOKEN`皆**未**出現
於`infra/template.yaml`，故部署後預設值為程式碼自身之安全預設（皆為
false/未設定）——`NLSC_LIVE_DEFAULT_DISABLED = YES`，符合本輪要求。
`DATA_PROVIDER_MODE`同樣未於template.yaml設定，代表部署後預設為
`mock`模式（安全但需注意：若要啟用real mode需自行於template加入此
環境變數，目前並非隱含開啟）。

Golden Case複查（透過真實Provider查詢，未修改任何資料）：
公告土地現值57,305元/m²（AVAILABLE）、徵收NOT_FOUND_IN_DATASET、
NLSC identifier F/F25/1027/04890000、CAD_001 = AUTH_REQUIRED（LIVE未
啟用）、Nominatim = REFERENCE_ONLY，皆與既有紀錄一致，本輪未變更。

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
**785 passed, 0 failed**。Core Freeze／Cadastral Pipeline SHA-256
本輪前後完全相符（本輪為純稽核，未修改任何repo原始碼）。

**結論**：`AWS_LOCAL_BUILD_READY = NO`——非因程式碼本身有誤，而是
本機工具鏈（`sam`/`docker`/`make`）完全缺失，無法實際驗證
`sam build`是否成功；且本輪額外發現之兩項packaging問題（cadastral
大型SQLite無法打包、PdfFunction image不必要地包含807MB資料）在
真正取得工具鏈後仍需個別處理，非本輪範疇。
