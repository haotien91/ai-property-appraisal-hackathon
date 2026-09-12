# COMPETITION_E2E_PHASE5_REPORT

**日期**：2026-09-11
**性質**：STEP 5 — Competition End-to-End + Blind Case。證明查估書表
（Input A）與評價基準明細表（Input B）雙輸入 Competition Flow 可從頭跑到
尾，並建立真正不同規則的 Blind Case 證明新評價基準會真的改變結果、且
案件間互不污染。

---

## 0. CORE FREEZE 遵守情況

本輪**未重寫**任何 Core Freeze 清單模組：`GradeEngine`／
`AdjustmentEngine`／`CalculationEngine`／`FormCompletionEngine`／
`CaseRuleRepository`／`EvaluationStandardImporter` 皆未修改其內部邏輯。
`CrossFormValidationEngine`／`AuditEngine`（不在凍結清單內）新增了 3 項
呼叫（見 §5），皆為**重用既有** `validate_field_pair()`／`validate_sum()`
的組裝呼叫，非新邏輯。

**未建立**任何第二套計算器：無 Second Grade Engine、Second Adjustment
Engine、RegionalRateCalculator、Table4Calculator、Table52Calculator、
BlindCaseCalculator。Blind Case 完全走 production path（見
`BLIND_CASE_PHASE5_REPORT.md`）。

```
SECOND_CALCULATOR_CREATED=NO
```

## 1. 本輪發現並修正的真實 Integration Bug（非 Core Freeze 清單內）

以下修改皆屬 `backend/handlers/*.py`（handler 層，非 Core Freeze
engine），依 §0 規定的格式列出：

### Bug 1 — Production PDF 只回表4+表5-2（P1，已知缺口）

- **CORE_MODULE_MODIFIED**：`backend/handlers/pdf_handler.py`（非 Core
  Freeze 清單成員）
- **WHY_MODIFIED**：`get_pdf()` 過去只呼叫
  `PdfRenderer.render_form()`（表4+表5-2），從未呼叫既有、已可運作的
  `pdf/pdf_renderer.py::build_table1_pdf_bytes()` —— 該函式已存在且已被
  `tests/test_phase5_golden_pipeline.py` 單獨測試，但從未被 production
  handler 呼叫。
- **BEFORE_BEHAVIOR**：`GET /api/cases/{id}/pdf` 回應僅含一份
  `pdf_url`（表4+表5-2）。
- **AFTER_BEHAVIOR**：回應改為 `{"forms": {"表1": {...}, "表4+表5-2":
  {...}}, "pdf_url": <表4+表5-2，向後相容>}`，兩份 PDF 皆產出並各自存入
  S3、回傳各自 presigned URL。`segment_scope`／`base_regional_factors`
  取自既有 `case_reconstruction.build_case_and_regional_factors()`（與
  `complete_form.py`／`review.py` 相同呼叫方式）。
- **REGRESSION_RESULT**：無既有測試涵蓋 `pdf_handler.py`（確認過
  `tests/` 內無任何檔案匯入或呼叫 `get_pdf`），故本次修改零既有測試
  風險；WeasyPrint 於本機 Windows 環境無法執行（見
  `PDF_OUTPUT_PHASE5_REPORT.md`），該部分邏輯無法在本機以外於 Docker/
  Lambda 容器映像驗證，已誠實標記。

### Bug 2 — `land_use_type` 於建案時遭靜默丟棄（真實嚴重 bug）

- **CORE_MODULE_MODIFIED**：`backend/handlers/cases.py`（非 Core Freeze
  清單成員）
- **WHY_MODIFIED**：撰寫 STEP5 §25 RULESET_UNAVAILABLE 測試（住宅用地）
  時發現：`create_case()` 雖在 `CompetitionCase` model 上驗證了
  `land_use_type`，卻從未把它寫進 `meta` dict —— 每個下游讀取者
  （`analyze.py`／`case_reconstruction.py`）皆用
  `meta.get("land_use_type", "商業用地")`，於是**任何**非商業用地案件
  （住宅／工業／農業／其他）在整個 pipeline 中被靜默當成商業用地處理，
  不會出現任何錯誤或警告 —— 比 RULESET_UNAVAILABLE 未觸發更嚴重：系統
  會自信地產出「完整但錯誤」的估價結果。
- **BEFORE_BEHAVIOR**：`create_case(land_use_type="住宅用地")` 後，
  `analyze()` 仍會用商業用地規則成功配對出 grade（見本次除錯過程的真實
  重現：修正前 `test_residential_without_case_rule_raises_no_silent_
  grade` 得到 `grades=[{"factor": "主要道路寬度", ...})`，而非預期的
  RULE_NOT_FOUND）。
- **AFTER_BEHAVIOR**：`meta["land_use_type"]` 正確寫入（僅於非空時寫入，
  保留既有省略時預設商業用地的相容行為）；同一測試現在得到
  `grades=[]`、`unresolved_factors[0].reason=="RULE_NOT_FOUND"`、
  `rule_resolution_status=="STATIC_LOCAL"`，符合
  `RULE_COVERAGE_MATRIX.md` 記載之 `NOT_RUNTIME_GRADE_READY`。
- **REGRESSION_RESULT**：`for f in tests/*.py calling create_case: grep
  land_use_type` 確認每個既有呼叫 `create_case` 的測試檔案皆已顯式提供
  `land_use_type`，故本次修正不影響任何既有測試（已於全套 regression
  1006 passed 中再次確認）。

### 追加：dual-input document_type 防呆（STEP5 §1）

- **CORE_MODULE_MODIFIED**：`backend/handlers/document_upload.py`／
  `document_extract.py`／`evaluation_standard.py`（皆非 Core Freeze）
- **WHY_MODIFIED**：確認過往完全沒有機制阻止把「評價基準明細表」誤傳入
  `document_extract.py`（查估書表擷取管線），反之亦然 —— §1 明確要求
  區分。
- **BEFORE_BEHAVIOR**：`request_upload()` 不接受也不儲存 document
  類型；`extract_document()`／`extract_evaluation_standard()` 對任何
  document_id 一視同仁。
- **AFTER_BEHAVIOR**：`request_upload()` 新增選填 `document_type`
  （`"APPRAISAL_FORM"` | `"EVALUATION_STANDARD"`，省略時保持
  `None`／向後相容）；兩個 extract handler 各自新增守門檢查 —— 僅當
  `document_type` **明確**存在且不符合時才拒絕（400
  `WRONG_DOCUMENT_TYPE`），未標記的舊資料不受影響。
- **REGRESSION_RESULT**：`tests/test_backend_document_handlers_e2e.py`／
  `tests/test_evaluation_standard_human_confirmation.py` 等既有呼叫
  `request_upload` 皆未提供 `document_type`（已逐檔確認），故不受影響；
  `tests/test_competition_dual_input_e2e.py` 新增測試明確驗證兩份真實
  PDF 各自標記正確 document_type。

## 2. Competition Case State（§2）

`domain/models.py` 新增 `CompetitionLifecycleStage`
（CREATED→...→PDF_READY，另有 MANUAL_REVIEW_REQUIRED／FAILED 作為隨時
可達的 side exit）與 `CompetitionCaseState`（`case_id`／各階段
`*_status`／`overall_status`／`manual_review_required`／
`blocking_issues`）。`backend/handlers/competition_state.py` 提供
`init_state()`／`get_state()`／`advance()`／`add_blocking_issue()`，
持久化於既有 `case_store.put_record/get_record`（SK=
`"COMPETITION_STATE"`），**未新增任何儲存機制**。`advance()` 拒絕
lifecycle 倒退（除非目的地或當前狀態本身是 side exit），確保不會假裝
COMPLETE。

## 3. Evaluation Standard Gate ／ Rule Resolution Precedence（§3-§4）

未新增邏輯 —— 完全重用 STEP2/3B 既有
`rule_engine_factory.build_rule_engine_for_case()`（CONFIRMED Case
Rule > Applicable Static Local Rule > RULESET_UNAVAILABLE，scope-level
replacement，partial-scope 明確 traceable）。本輪僅以新測試驗證：
`tests/test_competition_blind_case.py::test_b2_before_confirmation_
cannot_affect_result`（未確認前不生效）、
`tests/test_competition_dual_input_e2e.py::TestResidentialRulesetUnav
ailable`（住宅用地 RULESET_UNAVAILABLE）。

## 4. Competition E2E Orchestrator（§5）

**Step Functions 現況檢查**：`grep -rn "AWS::Serverless::StateMachine"
infra/` 及 `find . -iname "*.asl.json"` 均無結果 —— `infra/` 目前**沒有**
任何 Step Functions scaffold 可重用，也沒有既有 `.asl.json` 定義。依 §29
指示「不要為了STEP 5強行重寫orchestration infra」，本輪**未**建立 Step
Functions state machine。

```
STEP_FUNCTIONS_PRODUCTION_READY=NO
```

新增 `backend/handlers/competition_orchestrator.py::CompetitionOrchestrator`
——純 orchestration：每個方法呼叫既有 handler 函式（`document_extract.
extract_document`／`evaluation_standard.*`／`collect_data.collect_data`／
`analyze.analyze`／`complete_form.complete_form`／`review.review`／
`pdf_handler.get_pdf`），並更新 `competition_state`。**不自行重新計算**
grade／adjustment／trial price／form totals。`pdf_handler` 特意延遲匯入
（method-local，非 module-level）——正式部署中 PdfFunction 是獨立
Container Image（見 `runtime_paths.py` 說明），與其他 Zip function 分開
執行；本機 Windows 缺少 WeasyPrint 原生依賴時，orchestrator 的其他步驟
仍可正常運作而不被拖垮。

## 5. Cross-Form Validation 補強（§10，見
`CROSS_FORM_PHASE5_REPORT.md` 詳述）

`engine/audit_engine.py::AuditEngine.review()` 新增 3 項案件層級
cross-form 檢查（`case_no_identity`／`comparable_id_set_identity`／
`base_parcel_comparison_price_subtotal`），皆透過既有
`CrossFormValidationEngine.validate_field_pair()`／
`CalculationValidator.validate_sum()` 組裝，新增
`_field_pair_or_missing()` 防呆（兩側任一為 None 時回報 MISSING，避免
偽造 INCONSISTENT）。既有 `tests/test_smart_review.py` 之
`test_exactly_three_demo_errors_plus_one_honest_road_width_gap` 因此
更新期望值（4→7 個 non-Passed issue，見該測試新增之
`step5_cross_form_fields` 斷言），其餘 76 項既有 Smart Review 測試無需
變更即通過。

## 6. Golden Case 全流程重跑（§13，見 G1-G3）

`tests/test_competition_dual_input_e2e.py::TestGoldenE2E`：

- G1（無新規則）：主要道路寬度18m→**普通**（STATIC_LOCAL）
- G2（真實 PDF 雙輸入，CONFIRMED 案件規則）：18m→**普通**（與官方
  評價基準明細表一致，非巧合——同一份官方 PDF 來源）
- G3（Blind Case 執行後）：Golden 仍為 STATIC_LOCAL／普通，未受污染

個別因素（面前道路寬度 18m vs 6m → +5.00%）於 G1 中一併驗證，無 fixture
fallback。

## 7. Blind Case（§14-§19，見 `BLIND_CASE_PHASE5_REPORT.md`）

## 8. 測試與 Regression（§33）

| 項目 | 指令 | 結果 |
|---|---|---|
| STEP5 專項（3 個新檔案） | 見下表 | **31 tests**（29 passed + 2
  skipped，皆為已知 WeasyPrint 環境限制） |
| STEP4/3C/3B/3A/2（未修改任何其對應 production code，僅 audit_engine.py
  新增3項檢查已於 §5 說明並更新對應測試） | 併入全套 regression | 全數
  通過（見下） |
| 完整套件（排除既有環境限制檔案） | `py -m pytest -q
  --ignore=tests/test_phase5_golden_pipeline.py` | **1006 passed, 2
  skipped** |
| SAM Lint | `sam validate --lint`（於`infra/`） | **PASS** |
| SAM Build | `sam build --use-container --container-env-var-file
  _env.json`（暫時 CA bundle workaround，完成後已刪除） | **Build
  Succeeded**（含 PdfFunction Container Image 與所有既有 Zip
  function；`domain/`／`engine/`／`providers/` 皆包含本輪新增/修改
  內容） |

STEP5 新增測試檔案：

| 檔案 | 項目數 | 涵蓋 |
|---|---|---|
| `tests/test_competition_blind_case.py` | 12 | B1-B10, §17 矩陣差異,
  fixture metadata |
| `tests/test_competition_dual_input_e2e.py` | 5 | G1-G3, RULESET_
  UNAVAILABLE, 15-step Orchestrator E2E |
| `tests/test_competition_cross_form_tamper_and_failure.py` | 14 |
  Cross-form tamper A-E, Failure F1-F5,F7-F10 |
| `tests/test_pdf_output_runtime_verification.py`（STEP5 FINAL GATE
  新增） | 3 | 真實呼叫 `pdf_handler.get_pdf()`、byte-level/PyMuPDF
  內容驗證、failure-mode 安全性 |

**STEP5 FINAL GATE 更新（見 `docs/audit/STEP5_FINAL_GATE_REPORT.md`
完整記錄）**：上一輪報告曾將「以非預設檔案順序執行時出現 19 failed」
記錄為「已知但未進一步修補」的既有模式；本輪已**找出真正 root cause
並修正**——`backend/handlers/*.py` 內每個模組層級的
`os.environ.get("X_NAME", default)` 資料表/桶名稱常數只在該模組於
process 內第一次 import 時求值一次，多個測試檔案在同一 process 內
各自想要不同資料表/桶名稱時，只有第一個 import 該模組的檔案的環境
變數真正生效。修正方式：新增 `tests/_aws_mock_reset.py`（純測試輔助，
非 production 程式碼），在全部 8 個使用 `moto.mock_aws()` 的測試檔案
的環境 fixture 中呼叫，強制重新載入受影響模組。經**手動驗證 5 組
指定檔案順序（含一組隨機排列）**，修正後全數 0 failed
（`ALL_TEST_ORDERS_ISOLATED=YES`）。過程中另外發現並修正一個與
test-order 無關、`pdf_handler.py` 本身的既有真實 bug（見
`PDF_OUTPUT_PHASE5_REPORT.md` §3a）。

**環境切換誠實記錄**：STEP5 FINAL GATE 這一輪的工作環境換到另一台
Windows host，該host **WeasyPrint 原生依賴齊全，可正常渲染**——這使得
先前「因環境限制而 skip」的 PDF 相關測試本輪全部**真正執行並通過**
（0 skipped，見下方更新後的完整套件數字），也讓
`tests/test_phase5_golden_pipeline.py`（先前因同一限制被排除）在此
host 上直接執行可通過（15 passed，額外佐證）。同時，此 host 缺少
SAM CLI 與 Docker，故 `sam validate --lint`／`sam build --use-
container` 本輪沿用同一 session 前一輪（在具備兩者的 host 上）之真實
驗證結果，而非本輪重新執行——詳見 `STEP5_FINAL_GATE_REPORT.md` §C3。

**STEP5 FINAL GATE 2 更新**：上一版本此處曾記錄
`1006 passed, 5 failed`，5 個失敗集中在
`tests/test_urban_plan_boundary_provider.py`／`tests/test_urban_
plan_golden_e2e.py`。經完整 root cause 調查（`STEP5_FINAL_GATE_
REPORT.md` §E），確認這是**STEP5-unrelated environment/geodata
regression**（非「這個專案本來就有」的 pre-existing 缺陷——本專案
同一 session 內的既有 baseline 原是 1006 passed / 0 failed）：這份
v12 專案複本的 `data/dataset_registry.sqlite3` 內，`ntpc_plan_
boundary`／`ntpc_zoning` 兩個資料集的 `local_path` 欄位是**上一台
機器（D:磁碟機）的絕對路徑**，複製到這台機器後該路徑不存在，導致
`DatasetRegistry.check_staleness()` 誠實回報 `UNAVAILABLE`、Provider
誠實回報 `UNKNOWN`（而非猜測 `INSIDE`——這是正確的安全行為，不是
bug）。真正的快照檔案本身**完好存在**於這份複本的正確相對路徑
（SHA-256 checksum 與 registry 原始登記值逐字元相符，`PRAGMA
integrity_check`皆為`ok`），僅路徑欄位需要修正。修正方式：新增
`scripts/repair_dataset_registry_local_paths.py`（一次性資料修復
工具，僅在候選檔案 checksum 驗證通過後才更新 `local_path` 一個
欄位，其餘欄位不變），**未修改任何 Provider/engine 程式碼**，
**未新建 Golden fixture**，**未 hardcode** 金山/第二種商業區/FAR
240。修正後：`py -m pytest -q --ignore=tests/test_phase5_golden_
pipeline.py` → **1011 passed, 0 failed, 0 skipped**（521.00s）。
完整證據詳見 `STEP5_FINAL_GATE_REPORT.md` §E。

## 8a. 效能量測（§30，僅供參考，非 SLA）

`scripts/measure_competition_e2e_performance.py`（新增，local
benchmark-only utility，非 production 程式碼）於同一 process 內跑兩次
完整流程（confirm→analyze→complete_form→review；PDF 生成因本機
WeasyPrint 環境限制而排除，見 §PDF 報告）：

```json
{
  "cold_ms": {
    "evaluation_standard_import_ms": 7.49,
    "confirmation_ms": 33.71,
    "analyze_ms": 109.06,
    "complete_form_ms": 118.54,
    "review_ms": 131.22,
    "total_ms": 400.02
  },
  "warm_ms": {
    "evaluation_standard_import_ms": 6.36,
    "confirmation_ms": 34.54,
    "analyze_ms": 108.21,
    "complete_form_ms": 133.44,
    "review_ms": 141.88,
    "total_ms": 424.43
  }
}
```

**誠實說明**：此 harness 的「cold」與「warm」皆發生於同一 Python
process（moto/boto3/pydantic 等模組匯入成本已在腳本自身 import 階段
攤提），故兩者數字接近——量到的主要是「每次呼叫」的穩定成本
（DynamoDB/S3 mock 往返、RuleEngine 重新建構、RuleTableValidator 重新
驗證），而非真實 Lambda cold start（container 初始化／模組首次載入）
成本；真正的 Lambda 冷啟動時間需要實際部署後於 CloudWatch 量測，本輪
未部署，故不宣稱已涵蓋。

```
COMPETITION_E2E_COLD_MS=400.02
COMPETITION_E2E_WARM_MS=424.43
```

## 8b. Demo Trace（§31）

Machine-readable（節錄自 `tests/test_competition_dual_input_e2e.py::
TestCompetitionOrchestrator15Steps` 實際執行之 `common.py` 結構化
log，`{"case_no", "step", "status", "duration_ms"}`）：

```
1.  create_case                          SUCCESS
2.  request_upload (APPRAISAL_FORM)      SUCCESS
3.  extract_document                     SUCCESS
4.  request_upload (EVALUATION_STANDARD) SUCCESS
5.  extract_evaluation_standard          SUCCESS
6.  get_evaluation_standard_candidate    SUCCESS  (candidate persisted)
7.  (status != CONFIRMED)                human confirmation required
8.  confirm_evaluation_standard          SUCCESS
9.  collect_data                         SUCCESS
10. analyze                              SUCCESS
11. complete_form                        SUCCESS
12. review                               SUCCESS
13. (cross-form checks embedded in #12)  PASSED
14. generate_pdf                         SKIPPED (WeasyPrint env limitation)
15. case finished                        overall_status != CREATED
```

Human-readable：

```
1. 查估書表已讀取（真實 查估書表範本.pdf）
2. 評價基準已讀取（真實 評價基準明細表範例.pdf）
3. N 個因素 deterministic match（見 EVALUATION_STANDARD_IMPORTER_
   PHASE3A_REPORT.md，本輪未重新統計，STEP3A 已記載 44 個）
4. N 個因素 AI candidate（同上，10 個）
5. N 個需人工確認（同上，11 個）
6. Rules confirmed（本輪 G2/15-step 測試皆實際呼叫 confirm_evaluation_
   standard 並取得 200 CONFIRMED）
7. Grade calculated（analyze.py，rule_source_type=CASE_IMPORTED_
   CONFIRMED）
8. Adjustment calculated（同上）
9. 表1 completed（pdf_handler.py 已修正呼叫 build_table1_pdf_bytes，
   本機無法實際渲染驗證位元組，見 PDF 報告）
10. 表5-2 completed（complete_form.py，rule_source_type 正確標記）
11. 表4 completed（同上）
12. Smart Review completed（review.py，含本輪新增 3 項 cross-form
    檢查）
13. PDF generated（CODE_READY，本機環境限制未能實際執行，見 PDF
    報告）
```

## 9. AWS / Local 邊界（§28）

本輪僅完成 local／moto／Docker（`sam build --use-container` 之
PdfFunction Container Image 建置）／SAM build 驗證，**未實際 deploy 到
AWS**。

```
AWS_REAL_DEPLOYMENT_VERIFIED=NO
```

## 10. 最終驗收矩陣（§34）

| # | 問題 | 答案 |
|---|---|---|
| A | 查估書表可以進 pipeline？ | YES（`document_extract.py` +
  document_type=APPRAISAL_FORM 守門） |
| B | 新的評價基準可以進 pipeline？ | YES（`evaluation_standard.py` +
  document_type=EVALUATION_STANDARD 守門） |
| C | 新規則未確認前不生效？ | YES（B2 測試證明） |
| D | 確認後真的改變 Grade？ | YES（B4：普通→稍優） |
| E | 確認後真的改變 Adjustment？ | YES（B5：7.5%→12%） |
| F | 三張表都產出？ | YES（表1+表5-2+表4，pdf_handler.py 修正後；本機
  WeasyPrint 環境限制無法實際渲染驗證，見
  PDF_OUTPUT_PHASE5_REPORT.md） |
| G | Cross-form 可以抓 tamper？ | YES（A-E 全數通過） |
| H | Blind 不污染 Golden？ | YES（G3, B9, B10） |
| I | 不存在 Mock / Golden fallback？ | YES（既有 STEP4 測試 + 本輪未
  新增任何 fallback 路徑） |
| J | PDF 可以產出？ | CODE_READY（`sam build` 成功建置 PdfFunction
  Container Image；本機無法實際執行 render，見 §J 之
  PDF_OUTPUT_PHASE5_REPORT.md 誠實說明） |

## 11. 最終結論

```
COMPETITION_LOCAL_E2E_READY=YES
BLIND_CASE_READY=YES
STEP5_READY=YES
```

完整最終輸出區塊見對話結尾（依使用者 §36 指定格式）。
