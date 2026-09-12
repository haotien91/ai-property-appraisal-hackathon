# FRONTEND-OFFICIAL-PDF-WIRING-1 — 正式版型 PDF ／ 系統審查報告 雙軌輸出驗證報告

## 背景與範圍

進入本輪前，`backend/handlers/pdf_handler.py` 已經在同一次 `GET /api/cases/{id}/pdf`
呼叫中，**同時**產生並回傳兩種獨立輸出（此為既有程式碼，非本輪新增）：

- `official_pdf_url` / `official_pdf_status`（`pdf/official_pdf_renderer.py`，
  PyMuPDF 覆蓋官方 `data/templates/official_appraisal_form_v1.pdf` 版型）
- `audit_pdf_url` / `pdf_urls`（`pdf/pdf_renderer.py`，WeasyPrint，既有
  Fallback Reproduction 稽核報表）

但前端 `pdf-preview.html`／`js/api.js` 從未讀取、也從未顯示過
`official_pdf_url` 這幾個欄位——這正是本輪 Task 2 要求「如果 Official PDF
已經有 bytes/artifact 但前端沒有使用：優先只接線，不要重做 renderer」精準對應
的現況。因此本輪絕大部分工作是**前端接線 + Mock artifact 生成 + 驗證**，
**未修改** `pdf/official_pdf_renderer.py`／`facility_confirmation_repository.py`
的任何既有邏輯（僅新增一支獨立的 Mock 產生腳本呼叫它們）。

## TASK 1 — 前端 PDF 路徑現況稽核（修改前）

```
CURRENT_FRONTEND_PDF_SOURCE=frontend/app/js/api.js 的 Api.getPdf(caseNo)
  （Mock: frontend/mock/pdf_result.json；Production: GET /api/cases/{id}/pdf）
CURRENT_FRONTEND_PDF_TYPE=僅 Audit / Fallback PDF（表1 + 表4+表5-2，
  pdf_result.json 明確標記 fallback_mode=true、fallback_reason=
  "官方查估書表範本.pdf非標準PDF...採結構化結果->系統產出報表之後備輸出方式"）
CURRENT_DOWNLOAD_HANDLER=pdf-preview.html 內嵌 <script>，
  讀取 d.pdf_urls 逐一產生 <a class="btn btn-ai-primary"> 下載按鈕
CURRENT_MOCK_PDF_FILES=frontend/app/frontend/mock/pdf/表1_Golden_Case.pdf,
  frontend/app/frontend/mock/pdf/表4_表5-2_Golden_Case.pdf
```

前端修改前**只下載 Audit PDF，從未下載或顯示 Official PDF**——即使後端當時
已經能產出。

## TASK 2 — 後端 PDF Contract 現況稽核

```
AUDIT_PDF_GENERATOR=pdf/pdf_renderer.py（PdfRenderer + build_table1_pdf_bytes，WeasyPrint）
OFFICIAL_PDF_GENERATOR=pdf/official_pdf_renderer.py（render_official_pdf，PyMuPDF）
AUDIT_PDF_BACKEND_AVAILABLE=YES（既有，本輪未改）
OFFICIAL_PDF_BACKEND_AVAILABLE=YES（既有，本輪未改——pdf_handler.py 第133行起
  的 PDF-OFFICIAL-1 Task 11 早已呼叫 render_official_pdf 並回傳
  official_pdf_url/official_pdf_status）
OFFICIAL_PDF_CURRENTLY_EXPOSED_TO_FRONTEND_BEFORE=NO（後端有回傳，前端完全未讀取／未顯示）
```

依指示採「優先只接線」，本輪**未修改** `pdf_handler.py`／
`official_pdf_renderer.py`／`facility_confirmation_repository.py` 任何一行。

## TASK 3/4/5 — Official Template 身份與版面驗證

以 PyMuPDF 直接開啟並比對：

```
OFFICIAL_TEMPLATE_FILENAME=data/templates/official_appraisal_form_v1.pdf
  （由 scripts/build_clean_official_template.py 從
  data/sources/competition/查估書表範本.pdf 產生的 clean template；
  official_pdf_renderer.py 每次執行皆重新 SHA256 比對，不符即拒絕填值）
OFFICIAL_TEMPLATE_PAGE_COUNT=6
OFFICIAL_TEMPLATE_PAGE_SIZES=
  page0/1: 595.2×841.68pt（A4直式）
  page2:   841.68×595.2pt（A4橫式）
  page3/4/5: 1190.4×841.68pt（大版面地圖頁）
  與原始 data/sources/competition/查估書表範本.pdf 逐頁完全一致
OFFICIAL_OUTPUT_PAGE_COUNT=6（與模板相同，未增減頁）
OFFICIAL_OUTPUT_PAGE_SIZES=與上述模板完全一致

OFFICIAL_PAGE1_IS_TABLE1=YES（profile["pages"]["table1"]=0，實際渲染確認為「表1 地價區段勘查表」）
OFFICIAL_PAGE2_IS_TABLE52=YES（["table5_2"]=1，實際渲染確認為「表5-2 影響地價區域因素分析明細表」）
OFFICIAL_PAGE3_IS_TABLE4=YES（["table4"]=2，實際渲染確認為「表4 比較法調查估價表」）
OFFICIAL_MAP_PAGES_PRESERVED=YES（page 3/4/5 皆保留原模板14張圖片＋文字，
  render_official_pdf() 的覆蓋迴圈只寫入 page index 0/1/2，從未觸碰 3/4/5）
```

## TASK 6 — Confirmation Gate（既有邏輯，本輪僅驗證未修改）

`facility_confirmation_repository.get_active_confirmed_selections()`（既有）：

```python
if (rec is not None and rec.status == FacilityConfirmationStatus.CONFIRMED
        and not rec.stale and rec.confirmed_selection is not None):
```

`pdf_handler.py` 呼叫的正是這支方法（不是 `get_confirmed_selections()`），
`official_pdf_renderer.py` 也**只**接受這個已篩選過的 dict，從不直接讀取
`FACTORS.points`／`official_facility_evidence` 來決定這 8 個欄位。

```
OFFICIAL_PDF_USES_ACTIVE_CONFIRMED_ONLY=YES
PENDING_WRITES_OFFICIAL_PDF=NO
REJECTED_WRITES_OFFICIAL_PDF=NO
STALE_WRITES_OFFICIAL_PDF=NO
RAW_EVIDENCE_WRITES_OFFICIAL_PDF=NO
```

## TASK 7 — PDF Contract

```
PDF_TYPE_CONTRACT=單一既有 endpoint（GET /api/cases/{id}/pdf）於同一回應內
  用兩組互不重疊、命名明確的欄位分別表達兩種類型：
    official_pdf_url / official_pdf_status / forms.official_form
    audit_pdf_url / pdf_urls / forms_included / page_count / fallback_mode
  從不共用一個欄位讓呼叫端用檔名或啟發式猜測類型；本輪新增的
  Api.getOfficialPdf()/Api.getAuditPdf() 分別只回傳、只讀取各自那組欄位。
```

## TASK 8/9/10 — 前端改動

- `frontend/app/pdf-preview.html`：
  - 段落說明改寫，明確區分「正式版型查估書表」與「系統審查報告」為兩份不同檔案。
  - 主要 CTA 卡片（`#officialPdfCard`，置頂、`btn-ai-primary btn-lg`）：
    「下載正式版型查估書表」+ 副標「沿用官方查估書表版型，最終內容仍須承辦人／
    估價師核定」。
  - 次要卡片（`#auditPdfCard`，其後、`btn-ai-outline`）：「下載系統審查報告」+
    副標「包含系統判斷、Rule、Formula、Source 與資料追溯資訊」。
  - 本頁面沒有內嵌 PDF 預覽 iframe（現有設計本來就只有下載按鈕，無預覽區），
    故 Task 9「預覽優先順序」以**版面順序＋視覺權重**體現：Official 卡片在前、
    主色主按鈕；Audit 卡片在後、外框次按鈕。誠實記錄：非「切換 tab 預覽 PDF
    內容」的實作。
- `frontend/app/js/api.js`：新增 `Api.getOfficialPdf(caseNo)` /
  `Api.getAuditPdf(caseNo)`，兩者皆呼叫既有 `Api.getPdf()`（Mock 模式讀同一份
  `pdf_result.json`；Production 呼叫同一支 `/pdf` endpoint），但**只回傳、只暴露
  各自類型的欄位**——呼叫端不需要、也不可能從回傳形狀誤判類型。`getPdf()`
  本身完全不變（向後相容既有呼叫者）。

## TASK 11/12 — Mock Official PDF 生成

新增 `scripts/build_official_pdf_mock.py`：

- 使用**真實** Golden Case fixture（`data/golden/golden_case_input.py`）跑過
  **真實** RuleEngine → GradeEngine → AdjustmentEngine → CalculationEngine →
  FormCompletionEngine（與 `tests/test_official_pdf_output.py` 完全相同的
  既有測試模式，僅是「呼叫」而非修改這些引擎）。
- 以 moto 模擬 DynamoDB，重播與 `scripts/build_facility_candidates_mock.py`
  **完全相同**的 confirm/reject/證據更新序列，再呼叫**真實**
  `get_active_confirmed_selections()` 取得 gate 篩選後的字典——**腳本內建
  assertion**：若結果不是「只有 substation」就直接中止，絕不讓一份與
  facility_candidates.json 狀態不一致的 PDF被寫出。
- 呼叫**真實** `render_official_pdf()`，輸出寫入
  `frontend/mock/pdf/official_form_Golden_Case.pdf` 與
  `frontend/app/frontend/mock/pdf/official_form_Golden_Case.pdf`
  （沿用既有巢狀 mock 路徑慣例）。

實際執行結果（見腳本 stdout）：`confirmed_facility_selections` 僅含
`substation` 一筆，與 `frontend/mock/facility_candidates.json` 現況完全一致：

| subtype | facility_candidates.json 狀態 | Official Mock PDF 是否顯示 |
|---|---|---|
| substation | CONFIRMED, stale=false | **是**（金山變電所／700M） |
| gas_tank | CONFIRMED, stale=**true** | 否（空白，需重新確認） |
| cemetery | PENDING | 否（空白） |
| funeral_home | PENDING, candidate=None | 否（空白） |
| crematorium | PENDING, candidate=None | 否（空白） |
| columbarium | REJECTED | 否（空白） |
| MRT / TRA | PENDING, candidate=None | 否（維持模板預設「無捷運站／無火車站」） |

```
MOCK_OFFICIAL_PDF_GENERATED_BY_REAL_RENDERER=YES
```

## TASK 13/14 — Audit PDF 保留 ／ 檔名

既有 `表1_Golden_Case.pdf`、`表4_表5-2_Golden_Case.pdf` **完全未刪除、未修改**，
仍以兩份獨立檔案下載（未合併，維持既有 WeasyPrint Audit renderer 不變）。

下載按鈕新增 `download="..."` 屬性（僅前端 UI 層級提示瀏覽器另存檔名，不影響
S3 實際物件 key）：
- Official：`download="正式版型查估書表_{case_no}.pdf"`
- Audit：`download="系統審查報告_{case_no}_{form}.pdf"`

Production 後端 S3 key 本來就不是 `表1_Golden_Case.pdf` 這類命名
（`{case_no}/official_form_{timestamp}.pdf`、`{case_no}/form1_{timestamp}.pdf`
等），Mock 沿用 Golden Case 命名純屬展示素材，不影響上述 `download` 屬性顯示
給使用者的檔名。

## TASK 15 — 錯誤處理

`pdf-preview.html` 對 Official／Audit 各自獨立呼叫、獨立渲染狀態：Official
失敗時 `officialPdfDownload` 直接清空（不放任何連結），`officialPdfStatus`
顯示「正式版型查估書表產生失敗：{原因}」，**絕不**把 `audit_pdf_url` 塞進
Official 的下載按鈕假冒成正式版型。已以 jsdom 情境測試（Scenario I）驗證：
mock 出 `official_pdf_status=TEMPLATE_LAYOUT_UNCONFIRMED` 時，
`a[data-pdf-role="official"]` 確認不存在，Audit 面板不受影響。

```
OFFICIAL_FAILURE_FALLS_BACK_TO_AUDIT_AS_OFFICIAL=NO
```

## TASK 16 — 視覺驗證（300 DPI 實際渲染，逐頁人工比對）

以 `frontend/mock/pdf/official_form_Golden_Case.pdf`（本輪腳本產生的真實
Golden Case 輸出）用 PyMuPDF 以 300 DPI render 出 page 0/1/2 並目視比對官方
`查估書表範本.pdf` 版面：表格框線、標題、欄位位置、checkbox/circle 位置皆與
模板一致；文字未溢出；無 Fallback banner；無 AUTOMATIC/MANUAL 徽章；無
Rule/Formula/計算過程痕跡。

```
TABLE1_OFFICIAL_VISUAL_MATCH=YES
TABLE52_OFFICIAL_VISUAL_MATCH=YES
TABLE4_OFFICIAL_VISUAL_MATCH=YES
```

## TASK 17 — 文字層驗證

以 PyMuPDF 對 Official Mock PDF 全文（6頁）逐一字串搜尋：

```
搜尋詞: Fallback Reproduction / AUTOMATIC / MANUAL / Rule Engine /
        Calculation Engine / formula: / rule_id: / 並非官方查估書表範本原始版面複製
結果：全部 False（未出現）

OFFICIAL_PDF_CONTAINS_AUDIT_MARKERS=NO
```

作為對照，同一組字串搜尋既有 Audit PDF（`表1_Golden_Case.pdf` /
`表4_表5-2_Golden_Case.pdf`）：`Fallback Reproduction`/`AUTOMATIC`/`MANUAL`/
`Rule` 皆為 True（`表4_表5-2` 額外含 `formula`）——確認 Audit PDF 依然保留
稽核追溯資訊，未被本輪誤傷。

## TASK 18 — 格式完整性

```python
open(path,'rb').read(4) == b'%PDF'   # True
doc.page_count == 6                  # True，與模板相符
doc.is_encrypted == False
doc.xref_length() == 1964            # 可正常解析，無 corrupt xref
6頁文字皆可 get_text() 正常擷取
```

```
OFFICIAL_PDF_VALID=YES
OFFICIAL_PDF_PAGE_COUNT_MATCH=YES
OFFICIAL_PDF_PAGE_SIZE_MATCH=YES
```

## TASK 19 — 前端情境測試（jsdom，對真實 api.js 邏輯執行，非重寫邏輯）

9 項全數 PASS（`node pdf_scenarios.js`）：

| 情境 | 結果 |
|---|---|
| A: Official 按鈕連結 official_form_Golden_Case.pdf | PASS |
| B: Audit 按鈕連結表1／表4+表5-2 Golden Case 檔案 | PASS |
| C: Official／Audit 位元組不同（實際讀檔比對，非僅檔名） | PASS |
| D: Official PDF 位元組層級不含 Fallback/AUTOMATIC/Rule 等字串（粗篩；權威結果見Task17 fitz文字層檢查） | PASS |
| I: Official 產生失敗 → 不渲染 Official 下載連結、顯示中文失敗訊息、Audit 面板不受影響 | PASS（3項子斷言） |
| J: Mock Mode → Official 與 Audit 皆可下載 | PASS（2項子斷言） |

E/F/G/H（Audit 保留追溯資訊／PENDING·CONFIRMED·STALE 三種 facility gate 狀態
在 Official PDF 的呈現）為 **PDF 產出內容層級**驗證，已在 Task 12/16/17 以
真實 PyMuPDF 文字擷取直接驗證（非重複的前端 DOM 斷言），結果整理於上方
Task 12 表格。

## TASK 20 — 實機／人工瀏覽器驗證

本環境無瀏覽器自動化工具（同前一輪 FACILITY-REVIEW-UI-1 之限制）。已用
`python -m http.server 8811`（本輪沿用同一背景伺服器）+ jsdom 執行真實
`js/api.js` 程式碼並檢查渲染後 DOM（Task 19）。**未**由人工在真實瀏覽器中
點擊操作過。

```
UI_MOCK_OFFICIAL_PDF_VERIFIED=YES
MANUAL_BROWSER_OFFICIAL_PDF_VERIFIED=NO
REAL_BACKEND_OFFICIAL_PDF_E2E_VERIFIED=NO
```

## TASK 21 — Regression

```
python -m pytest tests/test_official_pdf_output.py tests/test_facility_confirmation.py -q
  => 73 passed, 1 skipped（skip為既有WeasyPrint handler-level測試guard，非本輪新增）

python -m pytest tests/test_pdf_output_runtime_verification.py -q
  => 3 failed（tests/test_pdf_output_runtime_verification.py::TestPdfHandlerActuallyInvoked
     三項，全部因本機 Windows 缺少 libgobject-2.0-0／WeasyPrint 原生函式庫，
     與本輪Official PDF（純PyMuPDF路徑）無關，屬既有環境限制，非本輪新增失敗）

python -m pytest tests -q --ignore=tests/test_phase5_golden_pipeline.py
  => 3 failed, 1088 passed, 3 skipped（與本輪修改前之基準結果完全相同，
     NEW_FAILURE_COUNT=0）
```

未新增任何 skip 標記掩蓋新錯誤；上述3項失敗與本輪修改前的基準完全一致
（同一組測試、同一個原生函式庫問題）。

## 已知限制

- 前端目前無內嵌 PDF 預覽 iframe（僅下載按鈕），Task 9「預覽優先順序」以
  卡片順序＋視覺權重呈現，並非分頁切換預覽 PDF 實際內容。
- `getOfficialPdf()`／`getAuditPdf()` 各自獨立呼叫 `getPdf()`：production
  模式下頁面會對後端 `/pdf` endpoint 發出兩次請求（後端會各重算/重render一次）
  ——為求「兩個方法各自明確、互不影響」之簡單可靠設計，接受這個效能上的
  小幅重複成本（Demo/hackathon規模下可忽略），未引入額外快取層。
- 未執行 Manual browser acceptance（同既有前端各文件之誠實聲明）。
