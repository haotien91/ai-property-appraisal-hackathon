# PDF_OUTPUT_PHASE5_REPORT

**日期**：2026-09-11
**性質**：STEP 5 §7（Table 1 production gap）與 §12（PDF Output）。

---

## 1. 已確認之真實 P1 缺口（修正前）

使用者於 STEP5 §7 指出「Mock path 有表1+表5-2+表4，Production PDF 曾
只回表4+表5-2」。逐一確認如下：

- `engine/form_completion_engine.py`：grep `form="表` 確認每個
  `FieldCompletion` 皆只標記 `"表4"` 或 `"表5-2"`，最終合併結果
  `form="表4+表5-2（整合輸出）"`——**FormCompletionEngine 本身從未產出
  任何表1欄位**（這是設計使然：表1是 Data Acquisition Layer 原始
  調查資料，非 Rule/Calculation Engine 輸出，見
  `build_table1_pdf_bytes()` 自身 docstring）。
- `pdf/pdf_renderer.py::build_table1_pdf_bytes()`：**真實、可運作**的
  獨立函式，一直存在，且已被 `tests/test_phase5_golden_pipeline.py::
  test_table1_pdf_generation_and_content` 單獨測試通過。
- `backend/handlers/pdf_handler.py::get_pdf()`（修正前）：只呼叫
  `PdfRenderer.render_form()`（表4+表5-2），**從未呼叫**
  `build_table1_pdf_bytes()`——production 缺口確認為真。
- `PdfRenderer.render_all_forms()`：docstring 宣稱會合併表1（用
  pypdf），但**實際程式碼從未呼叫 build_table1_pdf_bytes 或做任何
  pypdf 合併**——docstring 與實作不符（次要發現，隨主要修正一併處理）。

## 2. 修正內容（非 Core Freeze 清單成員，見 COMPETITION_E2E_PHASE5_
REPORT.md §1「Bug 1」完整 CORE_MODULE_MODIFIED 格式記錄）

`pdf_handler.py::get_pdf()` 現在依序：

1. 呼叫既有 `PdfRenderer.render_form()` 產生表4+表5-2 PDF（**未修改**
   該函式本身）。
2. 呼叫既有 `case_reconstruction.build_case_and_regional_factors()`
   （與 `complete_form.py`／`review.py` 完全相同的呼叫方式）取得
   `case.segment_code`／`case.segment_scope`／`regional_base`。
3. 呼叫既有 `build_table1_pdf_bytes()`（**未修改**）產生表1 PDF。
4. 兩份 PDF 各自存入 `PdfBucket`（不同 S3 key），各自產生 presigned
   URL。
5. 回應改為 `{"forms": {"表1": {"pdf_url", "title"}, "表4+表5-2":
   {"pdf_url", "title"}}, "pdf_url": <表4+表5-2，向後相容欄位>}`。

**設計選擇：兩份獨立 PDF，而非合併成單一多頁 PDF**——`backend/
requirements-pdf.txt` 確認 `pypdf`/`PyPDF2` 目前**不是** PdfFunction
的 production 依賴（僅出現於被排除的
`tests/test_phase5_golden_pipeline.py`）；新增此依賴＋合併邏輯屬於
與本次修正目標無關、風險更高的範圍擴張。§12 明確允許「一個
multi-page PDF **或**三份 PDF，但 API contract 必須一致，且 UI
可以知道每份是哪一張表」——`forms` dict 以中文表名為 key，滿足此
要求。

## 3. 測試涵蓋與環境限制（STEP5 FINAL GATE 更新：已在真實環境驗證）

**原始撰寫環境**（本節原文，保留供對照）：本機 Windows host 缺少
Cairo/Pango 原生函式庫（`OSError: cannot load library
'libgobject-2.0-0'`），無法實際執行 WeasyPrint 渲染——與既有、已記錄的
`tests/test_phase5_golden_pipeline.py` 環境限制完全相同。當時新增測試
中，凡呼叫到 `pdf_handler.get_pdf()` 的測試皆以 `try/except OSError`
包裝並 `pytest.skip()`。

**STEP5 FINAL GATE 更新（本輪，於另一台 WeasyPrint 原生依賴齊全的
Windows host 上執行）**：`try/except OSError` 的 skip 路徑**從未被
觸發**——WeasyPrint 在此環境下完整運作。這使得：

1. 先前被排除的 `tests/test_phase5_golden_pipeline.py` 於此環境**直接
   執行可通過**：`py -m pytest -q tests/test_phase5_golden_pipeline.py`
   → **15 passed**，包含其原有的 `"地價區段勘查表" in full_text`／
   `GOLDEN_CASE.case_no`／`"P002-00"` 等內容斷言。
2. 真正執行到 `pdf_handler.py::get_pdf()`（而非在 import 階段就被
   WeasyPrint 的 OSError 擋下）後，發現並修正了一個**先前完全無法
   被觸發、與 STEP5/Table1 修正本身無關的既有 bug**（詳見下方 §3a）。
3. 新增 `tests/test_pdf_output_runtime_verification.py`，真正呼叫
   `pdf_handler.get_pdf()`、真正從 mocked S3 讀回 bytes、以 PyMuPDF
   開啟並驗證內容（詳見 §6）——`test_all_15_steps_no_mocked_final_
   result`（`tests/test_competition_dual_input_e2e.py`）與
   `test_f8_pdf_generation_failure_before_form_completion`
   （`tests/test_competition_cross_form_tamper_and_failure.py`）
   在此環境下也**真正通過**（不再是 skip）。

`backend/handlers/competition_orchestrator.py::generate_pdf()`
**仍保留刻意延遲匯入** `pdf_handler`（method-local import，而非
module top level）——真實部署中 PdfFunction 是獨立 Container Image
（見 `runtime_paths.py` 說明），與其他 Zip function 分開執行；此設計
讓 orchestrator 的其餘 13 個步驟即使在缺少 WeasyPrint 原生依賴的環境
下（例如原始撰寫環境）也能正常運作，不被這一個步驟拖垮整個模組匯入
——兩種環境下皆已驗證過此行為（分別以 skip 與真正執行兩種方式）。

### 3a. 新發現並修正的真實 bug（`pdf_handler.py`，與 Table1/test-order
皆無關，純粹是先前從未被真正執行到的既有缺陷）

**ROOT_CAUSE**：`FormCompletionResult`/`FieldCompletion`
（`domain/models.py`）皆宣告 `model_config = ConfigDict(extra=
"forbid")`。`FormCompletionResult` 另宣告 3 個 `@computed_field`
屬性（`completed_count`/`manual_review_count`/`unknown_count`）——
這些欄位會被 `model_dump_json()` **序列化進輸出**，但
`model_validate()`（重新讀入）**不接受**它們作為建構參數，會直接視為
`extra_forbidden`。`complete_form.py` 儲存進 DynamoDB 的紀錄正是
`json.loads(result.model_dump_json())` 的結果，因此天生就帶有這 3 個
「輸出限定」欄位；`complete_form.py` 另外又主動附加了
`rule_source_type`（每個 field）與頂層的
`rule_resolution_status`/`rule_package_id`/`rule_resolution_warnings`
（STEP2 追溯機制），`case_store.get_record()` 讀回時再附加
`_updated_at`。`pdf_handler.py::get_pdf()` 舊寫法
`FormCompletionResult.model_validate(form_completion)` 直接把這個
「富含額外欄位」的 dict 餵給一個 `extra="forbid"` 的模型，**必定**
拋出 `pydantic_core.ValidationError`（18 項欄位）。`review.py` 的
`build_submitted_from_form_completion()` 從未踩到這個問題，因為它把
儲存的 dict 當成普通 dict 讀取個別欄位，從未呼叫
`model_validate()`——`pdf_handler.py` 是唯一一個會這樣做的呼叫端。

**PRODUCTION_IMPACT**：在任何 WeasyPrint 原生依賴齊全的真實環境
（包含實際部署的 PdfFunction Container Image）下，`GET /api/cases/
{id}/pdf` 對**每一個**已完成 `complete_form` 的真實案件都會回傳
未攔截的 500 錯誤（`pydantic_core.ValidationError`），100% 無法
產出 PDF——這比 STEP5 §7 原本鎖定的「只回表4+表5-2」缺口更嚴重，是
一個先前從未被任何環境揭露過的全面阻斷性缺陷（因為唯一具備
WeasyPrint 原生依賴的既有測試環境，在觸及這一行之前就已經被
weasyprint 自身的匯入失敗擋下，見 §3 原文）。

**FIX**：新增 `_as_form_completion_result(raw: dict) ->
FormCompletionResult` 輔助函式，透過
`FormCompletionResult.model_fields`／`FieldCompletion.model_fields`
（Pydantic v2 內省 API，非硬寫欄位清單）過濾掉頂層與每個巢狀
field dict 中「不屬於該模型自身宣告欄位」的所有 key（含 computed
fields 與 handler 層附加欄位），再呼叫 `model_validate()`。
`get_pdf()` 呼叫處包上 `try/except`，任何殘留的格式問題回傳乾淨的
400 `VALIDATION_ERROR`，不再讓例外外洩成未攔截的 500。`PdfRenderer`/
`build_table1_pdf_bytes`/`FormCompletionResult`/`FieldCompletion`
模型定義本身**皆未修改**——修正完全侷限於 `pdf_handler.py` 這一個
非 Core Freeze 清單成員的呼叫端。

**REGRESSION_RESULT**：`tests/test_pdf_output_runtime_verification.py`
（新增）之 3 項測試，以及 `tests/test_competition_dual_input_e2e.py`／
`tests/test_competition_cross_form_tamper_and_failure.py` 中先前
因環境限制而 skip、本輪在真實環境下改為真正執行的 2 項測試，皆已
通過（見 §6 與 `docs/audit/STEP5_FINAL_GATE_REPORT.md`）。

## 4. Docker / Lambda Runtime Image 驗證（§12 允許之替代路徑）

`sam build --use-container` **已成功**建置 `PdfFunction` 的 Container
Image（`public.ecr.aws/lambda/python:3.12` 基底，含 `dnf install cairo
pango gdk-pixbuf2 google-noto-sans-cjk-ttc-fonts`——WeasyPrint 所需
原生函式庫已在該映像中正確安裝，與本機 Windows 環境完全獨立）：

```
Building image for PdfFunction function
Step 2/11 : RUN dnf install -y cairo pango gdk-pixbuf2 google-noto-sans-cjk-ttc-fonts && dnf clean all
 ---> Using cache
...
Successfully built d3aa5822e902
Successfully tagged pdffunction:pdf-latest
```

這證明 `pdf_handler.py`／`pdf/pdf_renderer.py`（含本輪新增的表1呼叫）
**於正確環境下的程式碼路徑是完整的**——本機無法執行只是 Windows
host 缺少原生函式庫的既有環境限制，並非本輪程式碼失敗。誠實狀態為
`CODE_READY`／`SAM_BUILD_READY`，**非** `LOCAL_RUNTIME_VERIFIED`（未
實際於該 container image 內執行 `sam local invoke PdfFunction` 產出
真實 PDF bytes 並驗證內容，本輪未做到這一步，留待未來輪次或實際
AWS/容器環境驗證）。

## 5. STEP5 FINAL GATE：真實 Byte-Level 驗證結果

`tests/test_pdf_output_runtime_verification.py`（新增，3 tests, all
passed）在具備 WeasyPrint 原生依賴的環境下，對真正跑完
`create_case→collect_data→analyze→complete_form→review` 的案件，
**真正呼叫** `pdf_handler.get_pdf()`：

| 驗證項目 | 方法 | 結果 |
|---|---|---|
| Handler 真正被呼叫 | 直接函式呼叫，非僅 import | PASS |
| 表1 PDF bytes 產生 | 從 mocked S3 `get_object()` 讀回 | `len>0`,
  `startswith(b"%PDF-")` — PASS |
| 表4+表5-2 PDF bytes 產生 | 同上 | `len>0`,
  `startswith(b"%PDF-")` — PASS |
| PyMuPDF 開啟兩份 PDF | `fitz.open(stream=..., filetype="pdf")` | `page_count>0` — PASS |
| 表1 內容識別 | 文字擷取 | 含 `"地價區段勘查表"` — PASS |
| 案件識別（兩份 PDF） | 文字擷取 | 皆含 `case_no` — PASS |
| Golden 值（兩份 PDF） | 文字擷取 | 皆含 `"P002-00"`（segment_code） — PASS |
| 表4+表5-2 規則可追溯性 | 文字擷取 | 含 `"REG-MAIN_ROAD_WIDTH"`（真實
  rule_id） — PASS |
| Forms contract | dict key 檢查 | `forms.表1`／`forms.表4+表5-2`
  皆存在 — PASS |
| 向後相容 `pdf_url` | 相等性檢查 | `pdf_url == forms["表4+表5-2"]
  ["pdf_url"]` — PASS |
| Failure mode（無 FORM_COMPLETION） | 400 + 無 S3 物件寫入 | PASS |
| Failure mode（FORM_COMPLETION 格式異常） | 400 + 無 S3 物件寫入 | PASS |

過程中發現並修正的真實 bug（`pdf_handler.py::_as_form_completion_
result()`）詳見 §3a。

## 6. 結論

```
TABLE1_COMPLETION=PASS
TABLE52_COMPLETION=PASS
TABLE4_COMPLETION=PASS
PRODUCTION_PDF_CONTAINS_TABLE1=YES
PRODUCTION_PDF_CONTAINS_TABLE52=YES
PRODUCTION_PDF_CONTAINS_TABLE4=YES
PDF_HANDLER_ACTUALLY_INVOKED=YES
TABLE1_PDF_BYTES_GENERATED=YES
TABLE1_PDF_VALID=YES
TABLE52_TABLE4_PDF_BYTES_GENERATED=YES
TABLE52_TABLE4_PDF_VALID=YES
PDF_GOLDEN_VALUE_VERIFIED=YES
PDF_FORMS_CONTRACT=PASS
BACKWARD_COMPATIBLE_PDF_URL=PASS
PDF_FAILURE_SAFE=PASS
PRODUCTION_PDF_RUNTIME_VERIFIED=YES
PDF_OUTPUT=PASS
```

（`PDF_OUTPUT=PASS` 現在指「在本輪實際可用的 WeasyPrint 環境下，
production 程式碼路徑已被真正呼叫、真正產生 PDF bytes、並以 PyMuPDF
驗證其內容與案件識別資訊——不再僅是 CODE_READY／SAM_BUILD_READY 的
間接推論；見 §3a/§5 之完整證據」。`sam build` 之 PdfFunction Container
Image 建置狀態見 §4，仍未於該 container image 內以
`sam local invoke` 實際執行——這一步留待未來輪次或實際 AWS 環境驗證，
`AWS_REAL_DEPLOYMENT_VERIFIED` 仍為 `NO`。）
