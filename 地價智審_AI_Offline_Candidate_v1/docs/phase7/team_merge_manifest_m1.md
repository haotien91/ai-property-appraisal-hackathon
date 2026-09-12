# TEAM-PROJECT-MERGE-M1 — Merge Manifest (Ultra-Fast Mode)

合併原則：**前端 UI/UX 以隊友版本為主；後端估價／PDF／匯出以本專案為唯一正式來源。**

備份：`地價智審_AI_Offline_Candidate_PRE_TEAM_MERGE_BACKUP/`（完整 1.4GB 複本，合併前建立）。

## 四份隊友專案的實際內容

四份 ZIP 都各自包含一份 `地價智審_AI_Offline_Candidate_v1`（Phase 8A 時期的共同基底，早於本專案的 C1/D1/E1/H1 樹林競賽功能），各自加上自己的功能：

| ZIP | 實際新增內容 |
|---|---|
| feat-artifact-db | 外層 `services/artifact-import/`、`infra/artifact-api/`、`infra/artifact-storage/`、`docs/artifact-delivery-contract-v1.md`；內層前端 `index.html`（landing + 案件庫）、`case.html`、`case-library.{js,css}`、`group-workspace.{js,css}`、`data/ntpc-districts.js` |
| feat-landing | 與 artifact-db 的內層專案比對結果為 **完全相同**（`diff -rq` 無任何差異）→ 視為已包含於 artifact-db |
| feat-rag | `backend/rag_service/`（Bedrock ConverseStream、read-only tools、keyword-overlap retrieval）、`docs/RAG_AWS_今晚會議規劃.md` |
| feature-simplified-workflow-ai-chat | 前端 `index.html`、`case-new.html`、`pdf-preview.html`（PDF + AI Chat 並排）、`result.html` |

**前端基準版本更新（2026-09-13）**：改以 `ai-property-appraisal-hackathon-feature-simplified-workflow-ai-chat (2).zip`（00:55 版，最新）為準。該版隊友已自行把 landing／案件庫／group-workspace 併入同一份前端，因此 `frontend/app/` 下所有 `.html`、`css/*`、`data/*`、`js/{case-library,group-workspace,main}.js` 皆直接採用該 ZIP 內容（逐檔覆蓋，未做樣式調整）。`js/api.js` 維持本專案版本（隊友版本仍為其嚴格子集），`js/config.js`、`js/case-context.js` 與該 ZIP 內容完全相同。

## 合併決策表

| Feature | Source | Destination | Action | Status |
|---|---|---|---|---|
| Landing + 案件庫首頁 | artifact-db `index.html` | `frontend/app/index.html` | USE_TEAMMATE | Done |
| 案件庫頁 | artifact-db `case.html` | `frontend/app/case.html` | USE_TEAMMATE | Done |
| 案件庫邏輯 | artifact-db `js/case-library.js` | 同路徑 | ADAPT（demoCases → 真實 `Api.listCases()`） | Done |
| 群組工作區 | artifact-db `js/group-workspace.js` + css | 同路徑 | USE_TEAMMATE | Done |
| 行政區地圖資料 | artifact-db `data/ntpc-districts.js` | 同路徑 | USE_TEAMMATE | Done |
| 案件建立頁 | simplified `case-new.html` | 同路徑 | USE_TEAMMATE（已內建「API 未串接」誠實降級） | Done |
| PDF + AI Chat 工作區 | simplified `pdf-preview.html` | 同路徑 | ADAPT（表1/表5-2 三檔 → 正式六頁 PDF 分頁籤） | Done |
| 案件結果頁 | simplified `result.html` | 同路徑 | ADAPT（加入 H1 匯出區、修正假 PASS、missing≠0） | Done |
| landing ZIP | — | — | SKIP_DUPLICATE | Done |
| RAG 服務 | rag `backend/rag_service/` | `backend/rag_service/` | USE_TEAMMATE + 新增 CaseExportBundle adapter | Code only |
| Artifact 匯入服務 | artifact-db `services/artifact-import/` | `services/` | USE_TEAMMATE | Code only |
| Artifact infra | artifact-db `infra/artifact-{api,storage}/` | `infra/` | USE_TEAMMATE | Code only |
| 估價計算 / 規則引擎 / 六頁 PDF / CaseExportBundle / JSON・Excel・Bundle 匯出 / provider pipeline | 本專案 | — | KEEP_CURRENT | 未更動 |
| `js/api.js`、`js/config.js`、`js/case-context.js` | 本專案 | — | KEEP_CURRENT | 未更動（本專案版本為隊友版本的超集） |

## 為什麼 api.js 不需要合併

比對三方 `api.js` 方法清單：兩位隊友的版本都是本專案版本的**嚴格子集**（本專案另有 `getOfficialPdf`/`getAuditPdf`/`getExportJson`/`getExportExcel`/`getExportBundle`/facility 系列）。隊友頁面呼叫的每個方法本專案都已具備，因此保留本專案版本即可，無需改寫任何一方。

## 已修正的舊假設

| 舊假設 | 修正 |
|---|---|
| 表1 + 表5-2 + 表4 三個獨立 PDF | 正式六頁 PDF（表3×4 + 表5-1 + 表4），分頁籤以 `#page=N` 切換，表3 另有 P002/P003/P004/P001 四個區段鈕 |
| `demoCases` 硬編案件清單 | Production 模式改讀 `GET /api/cases`；demo 資料僅保留於 Mock 模式並明確區隔 |
| `status === 'COMPLETED'` 顯示為「通過」 | Shulin 案件改顯示「待人工複核（Shulin segment-aware review 尚未建置）」 |
| 未回報的檢核計數顯示 `0` | 改顯示 `—`（missing ≠ zero） |

## 未完成／需後續處理

- `RAG_LIVE_VERIFIED=NO`：`backend/rag_service/` 程式碼已就位，但 Bedrock 憑證／串流部署未驗證；`case_export_bundle_adapter.py` 只是最小 adapter，尚未有排程/觸發把正式 bundle 推進 RAG 資料夾。
- `ARTIFACT_DB_LIVE_VERIFIED=NO`：`services/artifact-import/` 與 `infra/artifact-{api,storage}/` 程式碼已就位，未連線驗證隊友已部署的 AWS endpoint。
- `case-new.html` 的 `Api.extractCaseMetadata`／`Api.createCaseWithDocuments` 尚未實作；頁面目前會誠實顯示「文件擷取 API 尚未串接」，未使用假資料。
- AI Chat 目前在 `Api.askCaseAssistant` 不存在時，落回頁面自帶且明確標示為 Mock 的回覆文字，未偽裝成真實 AI 回答。
