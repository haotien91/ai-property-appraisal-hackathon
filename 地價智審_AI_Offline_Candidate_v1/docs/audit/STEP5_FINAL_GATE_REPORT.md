# STEP5_FINAL_GATE_REPORT

**日期**：2026-09-11
**性質**：STEP 5 FINAL GATE — 只處理兩件事：(A) Test-order isolation 真正
root cause，(B) 在真正可用 WeasyPrint 的環境下，實際執行
`pdf_handler.get_pdf()` 並驗證產出之 PDF bytes/內容。本輪**未**新增任何
功能、未改 GradeEngine/AdjustmentEngine 邏輯、未加新 Rule、未擴充隊友
資料、未改 frontend、未 deploy AWS、未重新設計 Step Functions。

---

## A. Test Order Isolation — Root Cause

### A0. 環境切換說明

本輪工作環境由前一輪的 Windows host（WeasyPrint 原生函式庫缺失，
`OSError: cannot load library 'libgobject-2.0-0'`）切換為另一台 Windows
host，此處 **WeasyPrint 實際可正常運作**（含 Cairo/Pango/GTK 原生依賴）。
這使得 Part B 的真實 PDF 驗證第一次成為可能，也意外讓 Part A 的調查
過程中發現了一個先前从未被觸發過的**額外真實 bug**（見 A4）。

### A1. 已確認的兩個 ROOT CAUSE（同一類別，兩個症狀）

**TEST_ORDER_DEPENDENCY_FOUND=YES**

`backend/handlers/*.py` 中每一個直接讀取 DynamoDB 資料表名稱或 S3
bucket 名稱的模組，皆採用如下寫法：

```python
TABLE_NAME = os.environ.get("CASES_TABLE_NAME", "AIValuationCases")        # case_store.py
DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")  # document_upload.py / document_extract.py /
                                                                                      # document_get_extraction.py / document_confirm.py /
                                                                                      # evaluation_standard.py / review.py
PDF_BUCKET = os.environ.get("PDF_BUCKET_NAME", "ai-valuation-pdfs")        # pdf_handler.py
```

這些都是**模組層級常數**，只在該模組於當前 Python process **第一次**
被 `import` 時求值一次。`pytest` 一次執行會在**同一個 process** 內載入
數十個測試檔案；每個測試檔案透過 `monkeypatch.setenv(...)` +
`moto.mock_aws()` 各自想要一個「自己的」資料表/桶名稱——但只要某個
模組已經被**任何一個**測試檔案 import 過一次，該模組的
`TABLE_NAME`/`DOCUMENT_BUCKET`/`PDF_BUCKET` 就永久固定為當時的值，
之後**任何**其他測試檔案再怎麼 `monkeypatch.setenv()`，對已被 import
過的模組完全無效（Python 對已存在於 `sys.modules` 的模組不會重新執行
其頂層程式碼）。

**實際重現的兩種症狀**：

1. **`case_store.TABLE_NAME` 污染**——某個「不需要 DynamoDB」的測試
   （例如 `test_competition_blind_case.py::TestBlindMatrixDiffersFromStatic`，
   純資料比對，不用 `ddb_env` fixture）觸發其 autouse
   teardown（`import collect_data`）時，`CASES_TABLE_NAME`
   從未被設定過，`case_store.TABLE_NAME` 便永久綁死在 OS 預設值
   `"AIValuationCases"`。後續任何測試呼叫 DynamoDB 一律
   `ResourceNotFoundException`。
2. **`evaluation_standard.DOCUMENT_BUCKET`（及 `document_upload.py`/
   `document_extract.py` 同名常數）污染**——`test_competition_blind_
   case.py` 的測試會 `import evaluation_standard`（呼叫
   `confirm_evaluation_standard()`），但該檔案的 `ddb_env` fixture從未
   設定 `DOCUMENT_BUCKET_NAME`（它完全不用文件上傳），於是
   `evaluation_standard.DOCUMENT_BUCKET` 永久綁死在 OS 預設值
   `"ai-valuation-documents"`。當 `test_evaluation_standard_human_
   confirmation.py`（本身有自己的、正確設定的 bucket 名稱）**在它之後**
   執行時，`document_upload.request_upload()` 用**它自己 import 到的
   （尚未污染的）模組**成功建立文件記錄並上傳到**正確**的桶，但
   `evaluation_standard.extract_evaluation_standard()` 用**已污染**的
   `DOCUMENT_BUCKET` 常數去 `head_object`，目標桶在當前 `mock_aws()`
   session 中根本不存在 → `DOCUMENT_NOT_UPLOADED`（即使上傳其實成功）。
   這造成該檔案 18-19 個測試（幾乎全部）失敗。

**PRODUCTION_STATE_LEAK_FOUND=NO（本輪的意義下：這不是 production
bug）**——真實部署的 Lambda function，環境變數在該 function 整個容器
生命週期內固定不變，「模組第一次 import 時讀取一次環境變數、之後重複
使用」正是 AWS Lambda 官方建議的高效寫法（避免每次呼叫都重新讀
`os.environ`）。沒有任何真實請求路徑會讓同一個 Lambda function 在
同一個 warm container 內看到兩個不同的 `CASES_TABLE_NAME`/
`DOCUMENT_BUCKET_NAME` 值。這純粹是**測試基礎設施**的問題：多個測試
檔案在同一個 process 內想要各自獨立的 mock 環境，而 production
程式碼的快取假設（環境變數不變）在測試場景下不成立。

**ROOT_CAUSE 摘要**：
```
backend/handlers/*.py 內的模組層級 `os.environ.get("X_NAME", default)`
常數只在該模組於當前 process 第一次 import 時求值一次；pytest 在同一
process 內執行多個測試檔案，每個檔案透過 monkeypatch.setenv() 想要
不同的資料表/桶名稱，但只有「第一個 import 該模組的檔案」的環境變數
真正生效，其餘檔案的 monkeypatch 對已快取的模組完全無效。
```

### A2. 修正方式（僅 tests，未觸碰任何 production 程式碼的這個行為）

新增 `tests/_aws_mock_reset.py`（純測試輔助模組，`backend/handlers/`
`engine/` `providers/` `domain/` 內任何 production 檔案都未匯入它）：

```python
_AWS_RESOURCE_NAME_MODULES = (
    "case_store", "document_upload", "document_extract",
    "document_get_extraction", "document_confirm",
    "evaluation_standard", "review", "pdf_handler",
)

def reset_cached_aws_module_state() -> None:
    for name in _AWS_RESOURCE_NAME_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
```

在每個建立 `mock_aws()` 環境的既有 fixture 中（`with mock_aws(): ...
<這裡呼叫> ... yield`），加入一行 `reset_cached_aws_module_state()`
呼叫，套用到以下**全部** 8 個使用 moto 的測試檔案（逐一確認，非部分）：

| 檔案 | Fixture |
|---|---|
| `tests/test_ai_semantic_fallback.py` | `env` |
| `tests/test_backend_document_handlers_e2e.py` | `document_env` |
| `tests/test_backend_handlers_e2e.py` | `ddb_env` |
| `tests/test_case_scoped_rule_architecture.py` | `ddb_env` |
| `tests/test_evaluation_standard_human_confirmation.py` | `env` |
| `tests/test_competition_blind_case.py` | `ddb_env`（原本只 reload
  `case_store` 一個模組，本輪改用共用 helper，涵蓋全部 8 個模組） |
| `tests/test_competition_dual_input_e2e.py` | `competition_env`（同上） |
| `tests/test_competition_cross_form_tamper_and_failure.py` | `ddb_env`（同上） |
| `tests/test_pdf_output_runtime_verification.py`（本輪新增） | `pdf_env` |

**明確未做**：沒有在任何 `backend/handlers/*.py` 內加入
`reset_global_state()`、`if pytest in sys.modules`、`TEST_MODE`
之類的 production hack——`tests/_aws_mock_reset.py` 是純測試檔案，
production 程式碼完全未被修改（除了 B 部分找到的 `pdf_handler.py`
真實 bug，見 A4/B 節，該修改與 test-order 無關）。

### A3. 5 組 File-Order Permutation 驗證結果

修正前（重現用）：

| Order | 內容 | 修正前結果 |
|---|---|---|
| 1 | 2→3A→3B→3C→4→5 | 148 passed（3B 在任何 STEP5 檔案前執行，未觸發） |
| 2 | 5→4→3C→3B→3A→2 | **19 failed**（3B 全滅） |
| 3 | 3B→5(blind)→2→4→3A→3C | 129 passed（3B 最先執行，未觸發） |
| 4 | Blind→3B→DualInput→2→3C | **19 failed**（3B 全滅） |
| 5 | 隨機排列（seed=20260911） | **19 failed**（3B 全滅） |

修正後（本輪實際重跑全部 5 組，含新增的 `test_pdf_output_runtime_
verification.py`）：

| Order | 內容 | 修正後結果 |
|---|---|---|
| 1 | 2→3A→3B→3C→4→5(全部4檔) | 151 passed, 0 failed |
| 2 | 5(全部4檔)→4→3C→3B→3A→2 | 151 passed, 0 failed |
| 3 | 3B→5(blind)→2→4→3A→3C | 129 passed, 0 failed |
| 4 | Blind→3B→DualInput→2→3C | 91 passed, 0 failed |
| 5 | 隨機排列（seed=20260911） | 148 passed, 0 failed |

```
ORDER_1=PASS
ORDER_2=PASS
ORDER_3=PASS
ORDER_4=PASS
ORDER_5=PASS
ALL_TEST_ORDERS_ISOLATED=YES
```

在本輪 WeasyPrint 原生依賴齊全的環境下，`try/except OSError` 的 skip
路徑從未被觸發（0 skip）——先前唯一允許的 skip 類別（WeasyPrint/Pango
host limitation）在此環境下不適用，因為該限制不存在。

### A4. 本輪額外發現的真實 production bug（與 test-order 無直接關係，
於 Part B 驗證過程中發現）——完整記錄於 §B 與 `PDF_OUTPUT_PHASE5_
REPORT.md`

`backend/handlers/pdf_handler.py::get_pdf()` 呼叫
`FormCompletionResult.model_validate(form_completion)` 時，因
`FormCompletionResult`/`FieldCompletion`（`domain/models.py`）皆宣告
`model_config = ConfigDict(extra="forbid")`，而 `complete_form.py`
儲存進 DynamoDB 的紀錄同時包含：(1) `FormCompletionResult` 自身 3 個
`@computed_field`（`completed_count`/`manual_review_count`/
`unknown_count`，`model_dump_json()`會序列化但`model_validate()`
拒絕接收）；(2) `complete_form.py`額外附加的
`rule_source_type`/`rule_resolution_status`/`rule_package_id`/
`rule_resolution_warnings`（STEP2 追溯機制）；(3)
`case_store.get_record()`附加的 `_updated_at`。這在**先前的
Windows host**上從未被觸發過——因為 WeasyPrint 匯入失敗永遠先發生，
程式碼根本沒機會執行到這一行；本輪換到 WeasyPrint 可正常運作的
host，才第一次讓這行真正被執行到，暴露出這個**與 STEP5/test-order
完全無關、原本就存在**的既有缺陷。詳見 §B 與
`PDF_OUTPUT_PHASE5_REPORT.md` 之完整 ROOT_CAUSE/PRODUCTION_IMPACT/FIX
記錄。此為本輪**唯一**修改 production 邏輯之處，且屬於「找到明確
integration bug 才修 production code」的正當情形，非為了讓測試通過
而加的 hack。

---

## B. Real PDF Runtime Verification

### B0. 環境確認

本 host 的 `sam build --use-container` 建置之 `PdfFunction` Container
Image 環境等效（實際 Cairo/Pango/GTK 原生依賴皆已安裝）能讓 WeasyPrint
真正運作，故本輪選擇直接在本機（同樣具備原生依賴）驗證
`pdf_handler.get_pdf()`，而非透過 `sam local invoke`（後者仍需
Docker Desktop 執行一個完整 Lambda 容器並掛載程式碼，本輪環境下直接
Python 呼叫已足以達成 §B1-B7 的全部要求：真正呼叫 handler、真正產生
bytes、真正用 PyMuPDF 開啟驗證內容）。

**證據，而非硬寫 PASS**：`tests/test_phase5_golden_pipeline.py`
（先前因 WeasyPrint 限制被排除於常規 regression 之外的既有測試檔案）
在本 host 上直接執行，**15 passed**，包含其原有的
`test_table1_pdf_generation_and_content`（`"地價區段勘查表" in
full_text`）與其餘表4+表5-2 內容檢查——這是先前從未在任何環境下被
真正驗證通過的既有測試，此次意外提供了額外的獨立佐證。

### B1-B2. 真正呼叫 pdf_handler.get_pdf()

新增 `tests/test_pdf_output_runtime_verification.py`：透過
`create_case → collect_data → analyze → complete_form → review`
（全部真實 handler，moto-mocked DynamoDB/S3）建立一個完整、真實的
`FORM_COMPLETION`/`REVIEW_RESULT` 記錄後，**直接呼叫**
`pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)`
（不只 import，而是真正執行、真正寫入 mocked S3、真正回傳 200）。

### B3-B4. Bytes / 內容驗證

- 從 mocked S3（`pdf_handler.py` 實際寫入的同一個 bucket/prefix
  慣例）直接 `get_object()` 讀回兩份 PDF 的原始 bytes（不解析
  presigned URL）。
- `len(bytes) > 0`，`bytes.startswith(b"%PDF-")`——兩份皆驗證通過。
- 以 PyMuPDF（`fitz.open(stream=bytes, filetype="pdf")`）開啟，
  `page_count > 0`——兩份皆驗證通過。
- 文字擷取（`page.get_text()`）：表1 PDF 內含
  `"地價區段勘查表"`；兩份 PDF 皆含案號本身（`case_no`）與
  Golden-specific 值 `"P002-00"`（區段編號）；表4+表5-2 PDF 另含
  確認過的 rule_id `"REG-MAIN_ROAD_WIDTH"`（真實比對用之靜態規則
  編號，非硬寫字串）。中文字型文字擷取在本環境下**穩定可用**（與
  `test_phase5_golden_pipeline.py`既有結果一致），故未需退回
  page-count-only 的弱驗證。

### B5. 案件識別與 Golden 值

同一測試同時斷言 `case_no` 與 `"P002-00"`（segment_code）**兩者皆**
出現在兩份 PDF 各自擷取的文字中——不只驗證表名，也驗證了案件識別
資訊真的被渲染進 PDF。

### B6. Output Contract

```python
assert "表1" in body["forms"]
assert "表4+表5-2" in body["forms"]
assert body["pdf_url"] == body["forms"]["表4+表5-2"]["pdf_url"]
```

三項皆通過——`forms.表1`/`forms.表4+表5-2`皆存在，
`pdf_url`（向後相容欄位）確實指向表4+表5-2。

### B7. Failure Mode 安全性（兩個獨立情境）

1. 案件從未完成 `complete_form`（無 `FORM_COMPLETION` 記錄）→
   `get_pdf()` 回傳 400 `VALIDATION_ERROR`；驗證 PDF bucket 內**沒有**
   任何該案件的物件被寫入（無 fabricated artifact）。
2. `FORM_COMPLETION` 記錄本身格式異常（`fields` 欄位是字串而非
   list，模擬未來欄位/資料毀損情境）→ 透過本輪新增的
   `_as_form_completion_result()` 之 `try/except`，同樣回傳 400
   `VALIDATION_ERROR`，而非未攔截的 500/`ValidationError`外洩；
   同樣驗證 PDF bucket 內無 fabricated artifact。

兩者皆為 safe failure，無 fabricated URL、無 empty PDF、無 false
PDF_READY 狀態。

---

## C. 最終 Regression

### C1. 5 組 Permutation（見 §A3）：全數 PASS，0 failed。

### C2. 完整套件（預設字母序）——STEP5 FINAL GATE 2 修正後

```
py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py
-> 1011 passed in 521.00s (0:08:40)
```

**修正歷程（誠實記錄，取代先前版本的措辭）**：STEP5 FINAL GATE
（第一輪）曾記錄 5 個失敗，並使用「pre-existing」一詞描述——這個措辭
不準確：本專案先前的 baseline（同一 session 內，另一台機器）曾是
**1006 passed / 0 failed**，這 5 個失敗並非「這個專案本來就有」的
缺陷，而是**這一份 v12 專案複本在這台機器上的環境/地理資料集差異**
所致，正確分類為 **STEP5-unrelated environment/geodata regression**
（見下方 §E 完整 root cause 分析與修正）。修正後，這 5 個測試皆已
恢復 **0 failed**，詳見 §E。

```
FULL_REGRESSION=PASS
FULL_REGRESSION_PASSED=1011
FULL_REGRESSION_FAILED=0
FULL_REGRESSION_SKIPPED=0
```

（實際重新執行 `py -m pytest -q --ignore=tests/test_phase5_golden_
pipeline.py` 之終端輸出：`1011 passed in 521.00s (0:08:40)`，
0 failed、0 skipped、2 warnings（既有 pytest fixture 棄用警告，與
本輪修改無關）。5 個先前失敗的都市計畫測試現皆計入 passed，見 §E。）

### C3. SAM Validate / Build — 本輪（STEP5 FINAL GATE 2）環境限制之
誠實記錄

本輪工作環境（同 STEP5 FINAL GATE 第一輪所使用之機器，見 §A0）
**仍未安裝** AWS SAM CLI 亦**未安裝** Docker（`Get-Command sam`／
`Get-Command docker` 皆找不到，`$env:PATH`亦不含任何 sam/docker
相關路徑）。使用者已明確指出：本輪修改了 `backend/handlers/
pdf_handler.py`（`_as_form_completion_result()` 新增於 STEP5 FINAL
GATE 第一輪），因此**先前的 `SAM_BUILD_USE_CONTAINER=PASS` 不得直接
視為目前最新 source tree 已重新驗證完成**——這個判斷是對的，本報告
在此**不再沿用**前一輪的 SAM_BUILD 結果作為「目前 source tree」的
驗證證據。

**誠實現狀**：本輪環境缺少 SAM CLI/Docker，**無法**針對目前最新
source tree（含 `pdf_handler.py` 修正 + 本輪 GIS registry 修正）
重新執行 `sam validate --lint`／`sam build --use-container`。這不是
「假設會通過」，也不是「沿用舊結果」——而是誠實回報「本輪這台機器上
無法執行此驗證」：

```
SAM_VALIDATE_CURRENT_SOURCE=NOT_VERIFIED
SAM_BUILD_USE_CONTAINER=NOT_VERIFIED
LATEST_PDF_HANDLER_PACKAGED=NO
```

（**更正（STEP5 FINAL CLOSURE 第三輪，見 §F）**：此處先前版本曾寫
`=FAIL`；使用者明確指出「不得寫 SAM_BUILD_USE_CONTAINER=FAIL，若
根本沒有執行」，應寫 `NOT_VERIFIED`——這是正確的更正，`FAIL` 意味著
「實際執行後失敗」，而本輪的真實狀態是「連嘗試執行都做不到（工具鏈
不存在）」，兩者語意不同，`NOT_VERIFIED`/`DEFERRED` 才是準確描述。
若需要對目前最新 source tree 完成這兩項驗證，需要在具備 Docker +
AWS SAM CLI 的環境下重新執行，本輪環境不具備此條件。）

## E. STEP5 FINAL GATE 2 — GIS Runtime Asset Closure

### E1. Read-Only Audit（§A）結果

1. **`data/` runtime GIS 檔案是否存在**：`data/snapshots/ntpc_plan_
   boundary_2026-09-08.sqlite3`（5,419,008 bytes）與 `data/snapshots/
   ntpc_zoning_2026-09-09.sqlite3`（190,365,696 bytes）**皆存在**於
   這份 v12 專案複本中——**不是**檔案遺失（排除
   `PROJECT_COPY_MISSING_ASSET`／`RUNTIME_DATASET_MISSING`）。
2. **`dataset_registry.sqlite3` 目前實際記錄的 `local_path`**（讀取
   `dataset_snapshots` 資料表逐欄確認）：
   ```
   ntpc_plan_boundary -> D:\黑客松影片\地價智審_AI_Offline_Candidate\
     地價智審_AI_Offline_Candidate_v6\地價智審_AI_Offline_Candidate\
     data\snapshots\ntpc_plan_boundary_2026-09-08.sqlite3
   ntpc_zoning        -> D:\黑客松影片\地價智審_AI_Offline_Candidate\
     地價智審_AI_Offline_Candidate_v6\地價智審_AI_Offline_Candidate\
     data\snapshots\ntpc_zoning_2026-09-09.sqlite3
   ```
   兩者皆為**上一台機器（D: 磁碟機）的絕對路徑**，在這台機器（`C:\
   Users\I-WAYNE.WU\Desktop\新北市政府黑克嵩\地價智審_AI_Offline_
   Candidate_v12\...`）上該路徑**不存在**——這正是
   `providers/dataset_registry.py::DatasetRegistry.check_staleness()`
   內 `os.path.exists(snapshot.local_path)` 檢查會回傳
   `UNAVAILABLE`、進而讓 `RealUrbanPlanBoundaryProvider`／
   `RealNtpcZoningProvider` 誠實回報 `UNKNOWN`（而非猜測 `INSIDE`）
   的直接原因。
3. **`DATASET_REGISTRY_DB_PATH` 環境變數**：一般執行時未設定（測試
   fixture 才會透過 `tmp_path` 覆寫），故 production/一般本機執行
   確實使用 `providers/dataset_registry.py::DEFAULT_DB_PATH`（即
   `<repo_root>/data/dataset_registry.sqlite3`，**相對於目前
   repo_root 動態計算**，本身不是問題來源——問題是這個 DB **內部
   儲存的 `local_path` 欄位值**是絕對路徑字串，不會隨專案複製到
   新路徑而自動更新）。
4. **`NTPC_ZONING_DB_PATH`／`NTPC_PLAN_BOUNDARY_DB_PATH`**：於
   `providers/`、`engine/`、`backend/handlers/` 全文搜尋，**不存在**
   這兩個環境變數名稱——`UrbanPlanBoundaryProvider`／
   `NtpcZoningProvider` 皆透過 `DatasetRegistry`（統一之 dataset_id
   -> local_path 對照層）取得路徑，不直接讀取個別環境變數，這是
   **已驗證架構本來的設計**，非本輪需修改之處。
5. **Golden coordinate 查詢為何回 UNKNOWN**：確認為上述第2點的直接
   結果——並非 shapely 未安裝（已確認 `_SHAPELY_AVAILABLE=True`）、
   並非座標本身錯誤、並非 Provider 邏輯錯誤。

```
URBAN_PLAN_FAILURE_ROOT_CAUSE=DATASET_REGISTRY_PATH_WRONG
```

### E2. Snapshot Integrity Verification（§E，接受前必須驗證）

| 資料集 | 登記 checksum | 實際 SHA-256 | 相符 | SQLite integrity_check |
|---|---|---|---|---|
| ntpc_plan_boundary | `a18393d0...dc1fad0` | `a18393d0...dc1fad0` | ✔ 完全相符 | `ok` |
| ntpc_zoning | `311b3e17...430125e` | `311b3e17...430125e` | ✔ 完全相符 | `ok` |

兩份檔案的 SHA-256 與 registry 內**原本已登記**的 checksum **逐字元
完全相符**（非重新計算後回填一個新值——是拿現有檔案去比對 registry
既有的、來自上一台機器 sync 時的原始 checksum），且 `PRAGMA
integrity_check` 皆回傳 `ok`。這證明複製到這台機器的檔案是**逐位元
組相同、未毀損的原始快照**，只是 registry 的路徑欄位需要更新去指向
它們在新位置的正確路徑。

```
GIS_SNAPSHOT_INTEGRITY=PASS
```

### E3. 修正方式（§D：未盲目重新生成官方資料，未 hardcode）

新增 `scripts/repair_dataset_registry_local_paths.py`（一次性資料
修復工具，比照既有 `scripts/sync_ntpc_zoning_dataset.py` 的定位，
供未來任何專案複本移動路徑時重複使用）：

1. 對 registry 內每個 `dataset_id`，若其 `local_path` 在目前機器上
   不存在，才嘗試修復（`local_path` 有效者完全跳過，不做任何寫入）。
2. 依既有命名慣例（`data/snapshots/<dataset_id>_<local_snapshot_
   version>.sqlite3`）計算候選路徑；若候選檔案不存在，回報
   `UNRESOLVED`，**不修改** registry。
3. 計算候選檔案的 SHA-256，**必須**與 registry 內既有（未變動）的
   `checksum` 欄位完全相符才繼續；不符則回報
   `CHECKSUM_MISMATCH`，**不修改** registry（絕不接受未經驗證的
   檔案）。
4. 驗證通過後，透過 `DatasetRegistry.register_snapshot()`（與
   `sync_ntpc_zoning_dataset.py`同一個既有 upsert API，非新機制）
   寫回，**僅更新 `local_path` 一個欄位**，其餘欄位
   （`checksum`／`record_count`／`last_synced_at`／`notes`⋯）原封
   不動地帶回。

**未做**：未修改 `providers/urban_plan_boundary_provider.py`、
`providers/ntpc_zoning_provider.py`、`providers/dataset_registry.py`、
`engine/land_use_ratio_engine.py` 任何一行；未新建 Golden fixture；
未 hardcode 金山／第二種商業區／FAR 240 於任何 production 程式碼；
未重新下載或重新生成官方資料集本身。

### E4. Golden 驗證（§F，實際即時查詢結果）

```
urban_plan_status: INSIDE
plan_id: jinshan
zone_name: 第二種商業區
```

（BCR/FAR 之 70/240 由 `tests/test_urban_plan_golden_e2e.py::
test_real_land_use_provider_full_chain_without_any_human_plan_id`
透過**完整 RealLandUseProvider 鏈路**——而非單獨呼叫
`engine.resolve_building_coverage_rate()`——驗證為
`points["building_coverage_ratio"].value == 70.0`；FAR 240 則由
`test_far_240_requires_the_resolved_plan_id_not_hardcoded` 驗證：
`resolve_floor_area_ratio("第二種商業區", plan_id=None)` 之
`resolved_value_pct != 240`（`resolution_layer` 非 `PLAN_SPECIFIC`）
——即**不帶 plan_id 絕不會巧合等於 240**；只有帶入
`RealUrbanPlanBoundaryProvider` 實際 resolve 出的 `plan_id="jinshan"`
才會得到 `240`／`PLAN_SPECIFIC`／`rule_status="CONFIRMED"`——證明 FAR
240 是真正由 plan_id+zone_name 兩者共同決定，並非任何形式的
Golden-specific 寫死。）

```
GOLDEN_URBAN_PLAN_STATUS=INSIDE
GOLDEN_PLAN_ID=jinshan
GOLDEN_ZONE_NAME=第二種商業區
GOLDEN_BCR=70
GOLDEN_FAR=240
FAR_WITHOUT_PLAN_ID_RETURNS_240=NO
```

### E5. 隔離測試結果（§F）

```
py -m pytest -q tests/test_urban_plan_boundary_provider.py tests/test_urban_plan_golden_e2e.py
-> 16 passed, 0 failed
```

```
URBAN_PLAN_TESTS=PASS
URBAN_PLAN_TEST_COUNT=16
```

## F. STEP5 FINAL CLOSURE — Local Non-Container Verification（第三輪）

### F0. LOCAL_ENVIRONMENT_LIMITATION（誠實記錄，非降低驗收標準）

本機（同一台 Windows host）**沒有 Docker，也沒有 AWS SAM CLI**——
逐項確認：

```
Get-Command docker  -> 找不到（CommandNotFoundException）
Get-Command sam     -> 找不到（CommandNotFoundException）
docker --version    -> 找不到
sam --version       -> 找不到
```

```
DOCKER_READY=NO
SAM_CLI_READY=NO
```

因此 `sam validate --lint`／`sam build --use-container` **本輪皆未
執行**（連嘗試都沒有嘗試——不是執行後失敗）：

```
SAM_VALIDATE_CURRENT_SOURCE=NOT_VERIFIED
SAM_BUILD_USE_CONTAINER=NOT_VERIFIED
CONTAINER_BUILD_DEFERRED=YES
```

`infra/.aws-sam/build/` 目錄雖然存在於這份專案複本內（檔案時間戳記
顯示與本次專案複本建立/同步時間一致），但這**不是本輪、也不是這台
機器上執行 `sam build` 產生的**——本輪未曾在這台機器上執行過 `sam
build`（前述 `Get-Command sam` 已確認不存在）。依 §3 明文指示，此
既有目錄**不被視為** container build 成功之證據，僅供 §F3 之
GIS packaging 結構性檢查參考用途（讀取其目錄內容，非執行它）。

### F1. Source-Tree Inspection（§4-§5）

```
LATEST_PDF_HANDLER_SOURCE_PRESENT=YES
```

`backend/handlers/pdf_handler.py` 內確認存在
`_as_form_completion_result()`（第 54 行定義，第 101 行呼叫）——
STEP5 FINAL GATE 第一輪修正的 persistence-envelope 相容性修正
確實存在於目前 source tree。

```
CASE_RULE_SOURCE_PRESENT=YES
EVALUATION_STANDARD_IMPORTER_SOURCE_PRESENT=YES
AI_SEMANTIC_PROVIDER_SOURCE_PRESENT=YES
STEP4_RULE_ASSETS_SOURCE_PRESENT=YES
```

逐一確認實際路徑（依實際專案結構，非假設路徑）：
`backend/handlers/case_rule_repository.py`、
`backend/handlers/rule_engine_factory.py`、
`engine/evaluation_standard_importer.py`、
`providers/semantic_rule_mapping_provider.py`、
`engine/central_local_rule_cross_validator.py`、
`data/rules/factor_alias_registry.json` 皆存在。

### F2. GIS Packaging Architecture（§6）

檢查既有 `infra/.aws-sam/build/EngineLayer/python/data/` 目錄內容
（純讀取既有檔案，未觸發新 build）：僅含 `data/rules/*.json`、
`dependency_graph.json`、`nlsc_code_cache.sqlite3`（小型快取）——
**不含** `data/snapshots/` 下任何 `ntpc_zoning_*.sqlite3`／
`ntpc_plan_boundary_*.sqlite3` 快照檔案；對整個
`infra/.aws-sam/build` 目錄搜尋 `*zoning*`／`*plan_boundary*`，
僅命中 `providers/ntpc_zoning_provider.py`／
`providers/urban_plan_boundary_provider.py` 兩個原始碼檔案本身，
未命中任何大型 GIS 快照資料。確認 EngineLayer 封裝架構**仍維持**
S3 cold-bootstrap 設計（Provider 執行期透過 `DatasetRegistry` 讀取
`/tmp` 或本地快照路徑，快照本身不隨 Layer 一起封裝），本輪未做
任何改動。

```
GIS_PACKAGING_ARCHITECTURE_UNCHANGED=YES
```

### F3. Worktree / Change Audit（§7-§8）

本專案未使用 git（`Is a git repository: false`），故以檔案
修改時間戳記作為證據：`data/dataset_registry.sqlite3`
（STEP5 FINAL GATE 2 之 GIS 修正，最後寫入時間
`2026-09-11 12:17:11`）之後，`find backend/handlers engine
providers domain -name "*.py" -newer data/dataset_registry.sqlite3`
**回傳 0 個檔案**——`FULL_REGRESSION_PASSED=1011` 之後，沒有任何
production `.py` 檔案被修改過。

```
CURRENT_SOURCE_MATCHES_LAST_REGRESSION=YES
```

依此，沿用已驗證之 1011 passed 結果，並另外**於本輪重新（非引用）
實際執行**兩個 PDF 相關測試檔案以求最新確認：

```
py -m pytest -q tests/test_phase5_golden_pipeline.py tests/test_pdf_output_runtime_verification.py
-> 18 passed, 0 failed (21.21s)
```

```
FULL_REGRESSION=PASS
FULL_REGRESSION_PASSED=1011
FULL_REGRESSION_FAILED=0
FULL_REGRESSION_SKIPPED=0
PHASE5_GOLDEN_PDF_TEST=PASS
PDF_RUNTIME_TEST=PASS
```

### F4. 結論

```
STEP5_FUNCTIONAL_READY=YES
STEP5_REGRESSION_READY=YES
STEP5_CONTAINER_VERIFICATION_PENDING=YES

STEP5_FINAL_GATE=HOLD
STEP5_CLOSED=NO
COMPETITION_LOCAL_BUILD_FROZEN=NO
```

**HOLD（非 FAIL）**：所有不需要 Docker 的驗收項目（test-order
isolation、PDF runtime verification、GIS root cause 修正與 Golden
驗證、完整 regression）皆已 PASS；唯一剩餘的關卡是
`sam validate --lint`／`sam build --use-container` 需要在具備
Docker + AWS SAM CLI 的環境下針對目前 source tree 重新執行——這是
本機工具鏈缺失，不是程式碼或邏輯缺陷，故不應計為 FAIL，但依使用者
明確指示，在此關卡通過前也不得宣稱 STEP5_CLOSED=YES。

## D. 修改檔案總表（本輪 STEP5 FINAL GATE，累計兩輪）

**Production 程式碼（僅 1 處，且有明確 ROOT_CAUSE/PRODUCTION_IMPACT/
FIX 記錄）**：
- `backend/handlers/pdf_handler.py`——新增
  `_as_form_completion_result()` 輔助函式，修正
  `FormCompletionResult.model_validate()` 因 `extra="forbid"` +
  computed fields + handler層追溯欄位而永遠失敗的既有 bug（詳見 §A4）。

**測試基礎設施（新增/修改，皆為 tests-only）**：
- 新增 `tests/_aws_mock_reset.py`
- 新增 `tests/test_pdf_output_runtime_verification.py`
- 修改（加入 `reset_cached_aws_module_state()` 呼叫）：
  `tests/test_ai_semantic_fallback.py`、
  `tests/test_backend_document_handlers_e2e.py`、
  `tests/test_backend_handlers_e2e.py`、
  `tests/test_case_scoped_rule_architecture.py`、
  `tests/test_evaluation_standard_human_confirmation.py`、
  `tests/test_competition_blind_case.py`、
  `tests/test_competition_dual_input_e2e.py`、
  `tests/test_competition_cross_form_tamper_and_failure.py`

**一次性資料修復工具（新增，非 production 邏輯，見 §E3）**：
- 新增 `scripts/repair_dataset_registry_local_paths.py`

**資料（非程式碼）變更（見 §E3，僅 `local_path` 欄位，checksum-verified
後才寫入）**：
- `data/dataset_registry.sqlite3`：`ntpc_plan_boundary`／
  `ntpc_zoning` 兩筆記錄的 `local_path` 欄位，由上一台機器的絕對路徑
  更新為這份專案複本的正確絕對路徑；其餘欄位（checksum/record_count/
  last_synced_at/notes 等）完全未變動。
