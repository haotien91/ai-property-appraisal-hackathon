# 文件處理分支整合檢查

檢查日期：2026-09-13。目標 `basic/document-processing-pipeline`，commit `4aef92edf19aa03bfa75f11e9a980b7158e23e55`；目前主工作分支 `feat/artifact-db`，HEAD `d9001ec`。

初次檢查結論：新版內容可沿用，但有整合與部署缺口。以下保留初始發現；修正狀態見本節。

## 整合修正狀態

- 分支 `codex/pipeline-integration`；先以 b50c7de 保存本機增量，再連接隊友分支 ancestry。保留 root services/infra、參考資料與新版前端；不產生第二套 artifact API 副本。
- 修正 Lambda 容器 data 路徑；新增 regression test。
- PDF 表3從實際比較順序與基準區段產生，不再使用固定編號。比較欄超過三個時，同一類 PDF 續頁；Excel 續工作表。儲存 JSON 的 comparison_index 不變，只有頁內視覺欄位重排。
- ZIP 附 artifact-pages.json，PDF endpoint 回傳頁碼與實際頁數。prepare_pipeline_delivery.py 驗證完整性及頁碼重疊，產生三類 PDF 和現有 client 可用的 manifest。
- Claude provider 改為 Converse，保留 schema/人工確認閘門；無法連線記錄 PROVIDER_UNAVAILABLE，不用 mock 替代。實際 AWS Sonnet 4.6 呼叫回傳 OK；已涵蓋 JSON code fence 與禁止輸出欄位檢查。這證明本機透過既有角色可呼叫，並不表示新 pipeline 已部署。
- 前端 data=live 保留現有 artifact 工作區；隊友 production 模式從其 API 載入案件後，導向其真實 PDF 頁面，避免將真實案件開成 mock 預覽。
- 已通過 66 個 targeted backend tests、18 個 artifact API tests、frontend adapter test 與 JS syntax checks。
- 已用新編號、兩個區段生成四頁 PDF，檢查三種書表圖片，並成功轉成上傳 manifest。
- 未部署隊友 pipeline、未更改現有 AWS 資源，未跑完整 GIS/LFS 資料流程。上傳工具已可供接線，producer job 自動呼叫與正式 execution role 權限仍需部署時設定。

## 補充：內容繼承與 Git 歷史

重新 fetch 後遠端仍為上述兩個 commit。使用 Git blob hash 比對並考慮目錄搬移，確認使用者所說「隊友以已推上版本內容為基礎，再整合開發」與檔案證據相符；不能因為沒有 merge base 就推論內容被覆蓋。

| 範圍 | 比對結果 |
| --- | --- |
| 我們已提交的 services/artifact-import | 13 個檔案完全相同，搬至中文專案目錄內 |
| 我們已提交的 infra/artifact-* | 6 個檔案完全相同，搬至中文專案目錄內 |
| artifact-delivery-contract-v1.md | 完全相同，搬至中文專案目錄內 |
| 我們已提交的 frontend/app | 70 個相同、14 個修改；對方另有 9 個新檔 |
| group-workspace.js / group-workspace.css | 與我們已提交版本完全相同 |
| backend | 對方新增 29 個檔案、修改 13 個 |
| export / pdf | 分別新增 7 / 3 個檔案 |

前端修改包含背景、圖例、上傳相關頁面，以及 production 模式從 `Api.listCases()` 載入案件。另一前端分支 `origin/feature/simplified-workflow-ai-chat` 的 79 個 frontend/app 檔案有 72 個與對方相同，也支持它含有該前端的內容，但無法單靠檔案證明使用過哪個 Git merge 指令。

`ac43614` 是沒有 parent 的 root commit，`4aef92e` 的 parent 只有 `ac43614`。因此是內容有繼承、歷史沒有連接；我們的 `d9001ec` 仍在 origin/feat/artifact-db，並未從該分支消失。

**本機未提交內容必須另行保留：** artifact-adapter.js、preview_server.py、前端 live mode 與 PDF 導航/標籤修正、前端 adapter 測試、services/claude-vision。這些不在對方分支，不能拿對方整包覆蓋工作目錄。

更新整合建議：以對方較新的 backend 與已整合前端內容為目標基礎，保留本機增量、參考資料及已部署 API 的路徑契約，再逐一整合兩套案件讀取方式。不將缺少共同 ancestor 本身當作退回舊 backend 的理由。原始參考資料有部分搬至 data/sources，其他資料需保留；沒有證據代表所有原始資料都已遺失。

## 必須先處理

1. **[P1] 容器找錯規則資料目錄。** `backend/docker/pdf.Dockerfile:36` 將 data 放入 `${LAMBDA_TASK_ROOT}/data`，但 `backend/handlers/runtime_paths.py:139–148` 在 Lambda 下只找 `/opt/python/data`。`rule_engine_factory.py:156` 進入規則解析便呼叫 `_load_static_rules()`；PDF 與 JSON/Excel export 使用同一容器，因此會受影響。已以現有 data 目錄加上 `LAMBDA_TASK_ROOT` 重現 FileNotFoundError。應支援容器的 task-root 路徑，再跑容器啟動與匯出測試。

2. **[P1] 輸出寫死區段編號與頁數。** `pdf/shulin_official_pdf_renderer.py:70,410–444` 與 `export/excel_exporter.py:238–248` 固定 P002/P003/P004/P001；`backend/handlers/shulin_official_pdf_handler.py:140` 也按此檢查。更換區段編號會被拒絕；多出區段不會進入固定輸出迴圈。須從本次輸入的 segment map 排序產生表3，不得由測試題編號決定內容。表4、表5目前模板只有三個比較欄，超出欄數的處理需明確定義，不能默默略過。

3. **Claude 語意映射尚未接線。** `providers/semantic_rule_mapping_provider.py:238–247` 的 `propose_candidate()` 直接拋 NotImplementedError。設定 `SEMANTIC_RULE_MAPPING_PROVIDER_MODE=bedrock` 不會讓它變成可用功能。這是規則語意映射，不代表所有文件提取都必須呼叫 Claude；我們已驗證的 Claude 圖片 helper 可作接線基礎，但仍需依此 provider 的輸入輸出規格整合。

4. **生成端尚未呼叫案件庫匯入 API。** `backend/handlers/export_handler.py` 上傳 ZIP 至 PDF_BUCKET_NAME，回傳暫時下載網址；backend/export 未找到 `/v1/imports` 呼叫。因此這份分支本身不會把產出登錄到現有案件庫。應串接既有 create-import → presigned upload → complete 流程，使用指定 case_id/group_id；生產端 case_no 不能當成我們的 UUID。

## 整合方式

- 兩分支沒有共同 Git ancestor。目標分支同時把 root 的 services/infra/docs 移進中文專案目錄；直接整包取代會破壞現有路径，也會覆蓋未提交的 UI 改動。建議從我們現有分支建立整合分支，按需要移入 backend/domain/engine/providers/export/pdf/data 與測試，保留 root 的已部署 API。
- `CaseExportBundle` 繼續作 JSON 交付格式，保留原始檔並按表拆分，不需改成向量 RAG。
- 目前 ZIP 的 `official_6_page.pdf` 是表3、表5、表4合併檔。現有 API 接收 survey/comparison/regional_factors 類型，故交付端需依**明確頁碼 manifest**分出三類 PDF；survey 仍可合併成一份。不能永遠猜「最後兩頁」或寫死前四頁。
- 這次不直接修改隊友估價規則、不部署其舊 infra。先修輸出與執行路徑，串匯入 API，再用改編號/改數量資料做端到端驗證。

## 檢查範圍與證據

- 已在 `/tmp/ntpc-pipeline-review` 隔離展開分支，原工作目錄保留。
- `test_competition_domain_multi_segment_b1.py`：16 passed。
- 匯出測試因缺 PyMuPDF 整個模組 skip，不能當作通過。
- 分支 `frontend/mock/export_json_result.json` 已交給現有 splitter 實跑通過：4 survey、1 comparison、1 regional_factors，以及 context/review。這驗證的是附帶樣本，不代表任意新題目都通過。
- 尚未執行完整 Docker/AWS 端到端流程，未驗證估價結果的業務正確性。
- LFS snapshot 本次未下載；依賴真實 GIS snapshot 的測試未納入。

## 正向觀察

多區段資料有各自的紀錄；JSON/PDF/Excel 共用匯出模型，缺少估價值會保留空值與人工確認資訊。這些方向可以沿用。

安全性：未完成全面審核，不能宣稱安全通過。正確性：上述 P1 阻擋部署與換題。效能：本次未做負載測試。可維護性：共享模型可沿用，但 Git 歷史與目錄搬移需整理。

## 自動交付修正

移除未經討論的 ZIP 匯入頁及本機 POST/job API。生成端多區段 generate_pdf 現在接 generate_and_publish，直接交付 PDF/JSON，保留重試 snapshot；僅上傳成功才標記完成。原始文件建立頁尚未接部署的背景 worker，不能稱為前端端到端已完成。
