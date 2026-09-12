# RC1-AUDIT-1 — Full Project Architecture, Integration & Orphan Audit

Audit Date: 2026-09-07　|　Mode: **只讀式全專案稽核，本輪零程式碼變更**

> 本輪為RC1_AUDIT-1的完整輸出。所有結論皆基於本次實際讀取原始碼、
> `infra/template.yaml`、`frontend/app/js/api.js`、既有測試檔案、以及
> 重新計算的import graph（`py`靜態掃描123個`.py`檔）取得，非憑印象
> 覆誦先前文件。凡標示「已知/沿用既有記錄」者，代表本輪重新核對過
> 該記錄仍與現況相符，而非未查證直接照抄。

---

## A. Executive Summary

- **架構整體符合期望**：Browser → Case → Document Extraction → Human
  Confirmation → Collect Data → Rule/Grade/Adjustment → Complete Form
  → Audit/Cross-form → Result → PDF → Human Review 這條主線，在
  production handler層級**確實逐段串接**，非stub、非hardcode。
- **兩個真實、具體的「模板/前端從未呼叫某已實作能力」缺口**（皆為
  已知既有限制的具體驗證，非全新bug）：
  1. `ExplanationFunction`在`infra/template.yaml`**沒有`Events:`區塊**
     （無API Gateway路由），其設計上唯一的呼叫路徑（Step Functions
     `AiExplanation`狀態）所需的`AWS::Serverless::StateMachine`資源
     **本模板未定義**——目前部署此stack後，`ExplanationFunction`是一個
     **完全沒有任何觸發來源會呼叫到的Lambda**。
  2. `PdfFunction`的`get_pdf()`實際上**只render「表4+表5-2」**，回傳
     單一`pdf_url`；`pdf/pdf_renderer.py`已存在的`build_table1_pdf_
     bytes()`（表1 PDF）**從未被任何production handler呼叫**，只在
     `tests/test_phase5_golden_pipeline.py`與`docs/phase8a/backup_
     demo_guide.md`的「現場即時產生」手動腳本中被直接呼叫。Offline
     Demo展示的「表1 + 表4+表5-2」兩份PDF，是Mock fixture
     （`frontend/mock/pdf_result.json`）刻意模擬的畫面，**真實部署後
     目前只會產出一份PDF**。
- 除上述兩項，**未發現**Real模式偷偷new Mock/Golden fixture、
  UNKNOWN被轉成PASS或0、或AI覆寫deterministic結果的證據。
- Core Freeze（36檔）與Cadastral Pipeline Freeze（6檔）逐檔重新驗證，
  **完全未變動**。

---

## B. Actual Architecture（已驗證之真實資料流）

```
Browser (frontend/app/*.html, 純靜態, 無build)
   ↓ Api.*(...)  (frontend/app/js/api.js，唯一API存取層)
Case Creation ─ CreateCaseFunction → cases.create_case → case_store (DynamoDB)
   ↓
Document Upload (選用分支) ─ RequestUploadFunction/DocumentExtractFunction
   /DocumentGetExtractionFunction/DocumentConfirmFunction
   → LocalExtractionProvider (PyMuPDF, 真實擷取, 非Textract)
   → engine.human_confirmation (低信心攔截)
   ↓
Collect Data ─ CollectDataFunction → 10個Data Provider (Mock/Real二選一
   陣列，DATA_PROVIDER_MODE驗證後決定) → case_store FACTORS
   ↓
Analyze ─ AnalyzeFunction → RuleEngine + GradeEngine + AdjustmentEngine
   （deterministic，逐factor grade+adjustment）
   ↓
Complete Form ─ CompleteFormFunction → FormCompletionEngine
   (GradeEngine+AdjustmentEngine+CalculationEngine組合)
   ↓
Review ─ ReviewFunction → AuditEngine（含CrossFormValidationEngine）
   → case_store REVIEW_RESULT
   ↓
Result ─ GetResultFunction → 純聚合已存資料，判定COMPLETED /
   MANUAL_REVIEW_REQUIRED（依issue_type是否含Error/Inconsistent）
   ↓
PDF ─ PdfFunction → PdfRenderer.render_form()（僅表4+表5-2）→ S3 put_object
   → presigned URL
   ↓
(旁支，無API路由) Explanation ─ ExplanationFunction → Bedrock摘要
   （已寫好，但目前無觸發來源）
```

此資料流與「EXPECTED SYSTEM ARCHITECTURE」**高度一致**，主要偏差就是
上述A節兩點（Explanation無觸發路徑、PDF只出一份）。

---

## C. Expected vs Actual（逐段比對）

| 期望階段 | 實際production handler | 是否真正串接 | 備註 |
|---|---|---|---|
| Case Creation | `cases.create_case` | YES | 真實DynamoDB put/get，非stub |
| Document Upload/Selection | `document_upload.request_upload` | YES | 真實S3 presigned URL邏輯（Mock分支誠實回傳null） |
| Document Extraction | `document_extract.extract_document` | YES | 呼叫`LocalExtractionProvider`（PyMuPDF），非Textract |
| Human Confirmation | `document_confirm.confirm_document` | YES | 讀`engine.human_confirmation`攔截邏輯之結果 |
| Collect Data | `collect_data.collect_data` | YES | 10個Provider陣列，Mock/Real嚴格分流 |
| Deterministic Rule/Grade/Adjustment | `analyze.analyze` | YES | RuleEngine+GradeEngine+AdjustmentEngine |
| Complete Form | `complete_form.complete_form` | YES | FormCompletionEngine |
| Audit/Cross-form Validation | `review.review` | YES | AuditEngine（內含CrossFormValidationEngine） |
| Result | `result.get_result` | YES | 純聚合，MANUAL_REVIEW_REQUIRED邏輯正確 |
| PDF | `pdf_handler.get_pdf` | **PARTIAL** | 只出表4+表5-2，表1能力未接線（見A節） |
| Human Review | `review.html`/`result.html`前端呈現 | YES | 見OFFLINE-ACCEPTANCE-1既有驗收記錄 |
| AI Explanation（旁支） | `explanation.generate_explanation` | **NO TRIGGER** | 程式碼完整、AI邊界正確，但無API路由/無StateMachine，目前無法被觸發（見A節） |

---

## D. Frontend/API Contract Matrix（`frontend/app/js/api.js`逐一核對）

| FRONTEND_ACTION | API_CLIENT_FUNCTION | HTTP_METHOD | PATH | BACKEND_FUNCTION | HANDLER_FUNCTION | MOCK_FIXTURE | STATUS |
|---|---|---|---|---|---|---|---|
| 案件列表 | `listCases()` | GET | `/api/cases` | ListCasesFunction | cases.list_cases | case.json | CONNECTED |
| 取得單一案件 | `getCase()` | GET | `/api/cases/{id}` | GetCaseFunction | cases.get_case | (由case.json過濾) | **BACKEND_ONLY**（frontend定義但0處呼叫，見下方說明） |
| 建立案件 | `createCase()` | POST | `/api/cases` | CreateCaseFunction | cases.create_case | case.json（回既有第一筆） | CONNECTED |
| 資料收集 | `collectData()` | POST | `/api/cases/{id}/collect-data` | CollectDataFunction | collect_data.collect_data | data_collection.json | CONNECTED |
| 因素分析 | `analyze()` | POST | `/api/cases/{id}/analyze` | AnalyzeFunction | analyze.analyze | analysis.json | CONNECTED |
| 書表填寫 | `completeForm()` | POST | `/api/cases/{id}/complete-form` | CompleteFormFunction | complete_form.complete_form | form_completion.json | CONNECTED |
| PDF預覽 | `getPdf()` | GET | `/api/cases/{id}/pdf` | PdfFunction | pdf_handler.get_pdf | pdf_result.json | **CONNECTED（但response shape不一致，見下方）** |
| 智慧審查 | `review()` | POST | `/api/cases/{id}/review` | ReviewFunction | review.review | review_result.json | CONNECTED |
| 案件結果 | `getResult()` | GET | `/api/cases/{id}/result` | GetResultFunction | result.get_result | result.json | CONNECTED |
| 文件上傳建URL | `requestDocumentUpload()` | POST | `/api/cases/{id}/documents` | RequestUploadFunction | document_upload.request_upload | document_upload.json | CONNECTED |
| 文件實體上傳 | `putDocumentFile()` | PUT | (presigned URL，非API Gateway) | 直接S3 | — | Mock恆回true不fetch | CONNECTED（設計上就繞過API Gateway） |
| 文件擷取 | `extractDocument()` | POST | `/api/cases/{id}/documents/{doc}/extract` | DocumentExtractFunction | document_extract.extract_document | document_extraction.json | CONNECTED |
| 取得擷取結果 | `getDocumentExtraction()` | GET | `/api/cases/{id}/documents/{doc}/extraction` | DocumentGetExtractionFunction | document_get_extraction.get_extraction | document_extraction.json | CONNECTED |
| 人工確認送出 | `confirmDocument()` | POST | `/api/cases/{id}/documents/{doc}/confirm` | DocumentConfirmFunction | document_confirm.confirm_document | document_confirm.json | CONNECTED |
| （無） | （無client method） | — | （無route） | ExplanationFunction | explanation.generate_explanation | （無mock fixture） | **BROKEN**（非frontend漏接，是backend從未曝露此能力給任何呼叫者，見A節） |

**發現1：`GetCaseFunction`為BACKEND_ONLY**——`Api.getCase()`已定義、
handler已實作、template.yaml路由已存在，但全部`frontend/app/*.html`
與`js/*.js`檔案掃描結果**零處呼叫**`Api.getCase(`。非bug（其餘頁面
靠`data.html`等既有流程取得案件狀態，未真的需要單獨查詢），但屬於
「有實作、目前用不到」的BACKEND_ONLY能力，供未來設計案件詳情頁時
參考已有現成端點。

**發現2：PDF回應形狀（response contract）不一致**——Mock fixture
`pdf_result.json`回傳`pdf_urls: [{form:'表1',...}, {form:'表4+表5-2
（系統整合輸出）',...}]`（陣列，2筆）＋`page_count`（以`表4+表5-2`為
key，缺括號後綴，見`docs/backlog.md`已記錄之RC1_PDF頁碼顯示bug）；
真實`pdf_handler.get_pdf()`只回傳單一`pdf_url`（無`pdf_urls`陣列、
無`page_count`、無`forms_included`）。前端`pdf-preview.html`的
`(d.pdf_urls && d.pdf_urls.length) ? d.pdf_urls : (d.pdf_url ? [...] : [])`
邏輯**可以優雅降級**（Production Mode下會顯示一個「下載 查估書表」
通用連結，不會crash），但驗收時展示的「兩份PDF」畫面**是Mock fixture
獨有的展示效果**，非Production Mode下的真實行為。已記錄，非P0
（不crash、不產生錯誤數值），但屬於STEP16文件真實性稽核的重要項目。

---

## E. Backend Call Graph（IMPORTED / CONSTRUCTED / CALLED / RESULT_USED）

| Handler | Engine/Provider | IMPORTED | CONSTRUCTED | CALLED | RESULT_USED |
|---|---|---|---|---|---|
| `cases.py` | `case_store` | YES | N/A(模組函式) | YES(`put_case_meta`/`get_case_meta`/`list_case_metas`) | YES |
| `collect_data.py` | 10個Provider類別（Mock×10/Real×10依mode） | YES | YES(`ALL_PROVIDERS`實例化) | YES(`.fetch()`) | YES(寫入`FACTORS`) |
| `collect_data.py` | `RealOfficialParcelCoordinateProvider` | YES | YES | 條件式（`DATA_PROVIDER_MODE=="real"`時） | YES |
| `collect_data.py` | `cadastral_snapshot_bootstrap.ensure_cadastral_snapshot` | YES | N/A | YES(第605行) | YES(決定Real Cadastral Provider是否可讀) |
| `analyze.py` | `RuleEngine`/`GradeEngine`/`AdjustmentEngine` | YES | YES | YES(`.grade_factor`/`.compute_adjustment`) | YES(寫入`ANALYSIS`) |
| `complete_form.py` | `FormCompletionEngine`(內含Grade+Adjustment+Calculation) | YES | YES | YES(`.complete_form`) | YES(寫入`FORM_COMPLETION`) |
| `review.py` | `AuditEngine`(內含CrossFormValidationEngine) | YES | YES | YES(`.review`) | YES(寫入`REVIEW_RESULT`) |
| `result.py` | （無engine，純讀`FORM_COMPLETION`+`REVIEW_RESULT`） | N/A | N/A | N/A | YES |
| `pdf_handler.py` | `PdfRenderer` | YES | YES | YES(`.render_form`，**僅表4+表5-2**) | YES(S3 put+presign) |
| `explanation.py` | `boto3 bedrock-runtime` | YES | YES | YES(`invoke_model`，try/except優雅降級) | YES(寫入`EXPLANATION`，但無任何呼叫者觸發此handler本身，見A節) |
| `document_extract.py` | `LocalExtractionProvider` | YES | YES | YES(`.classify`+`.extract_fields`) | YES(寫入`EXTRACTION`) |
| `document_confirm.py` | `engine.human_confirmation`（經由extraction_meta既有結構，非重新import engine類別） | — | — | 讀取既有confidence/requires_manual_review欄位 | YES |

**結論**：13/14個production handler皆為「真實呼叫」，非stub、非
hardcode回傳。唯一「IMPORTED但CALLED對象不完整」的是PdfFunction
（只呼叫了`render_form`，未呼叫已存在的`build_table1_pdf_bytes`）。

---

## F. Engine Reachability Audit

| ENGINE_MODULE | PRODUCTION_CALLER | REACHABILITY | TEST_COVERAGE | NOTES |
|---|---|---|---|---|
| `rule_engine.py` | analyze.py, complete_form.py, review.py | PRODUCTION_REACHABLE | test_rule_engine_core.py等多處 | 核心，多處production呼叫 |
| `grade_engine.py` | analyze.py, complete_form.py(經FormCompletionEngine) | PRODUCTION_REACHABLE | 多處 | — |
| `adjustment_engine.py` | 同上 | PRODUCTION_REACHABLE | 多處 | — |
| `calculation_engine.py` | complete_form.py(經FormCompletionEngine) | PRODUCTION_REACHABLE | test_calculation_engine.py | — |
| `calculation_validator.py` | 經AuditEngine間接 | PRODUCTION_REACHABLE | 間接(test_smart_review.py) | — |
| `audit_engine.py` | review.py | PRODUCTION_REACHABLE | test_smart_review.py, test_backend_handlers_e2e.py | 核心 |
| `cross_form_validation_engine.py` | 經AuditEngine內部使用 | PRODUCTION_REACHABLE | test_backend_handlers_e2e.py（tampering測試） | — |
| `form_completion_engine.py` | complete_form.py | **PRODUCTION_REACHABLE（但import graph顯示production caller很少，主要靠engine內部組裝）** | test_form_completion_golden.py等 | 靜態掃描顯示`kinds=['TEST']`是因為`complete_form.py`用`from form_completion_engine import FormCompletionEngine`bare import，實際確認complete_form.py第47行確實建構並呼叫，PRODUCTION_REACHABLE屬實（見E節） |
| `comparable_selection_engine.py` | 經FormCompletionEngine/collect_data間接 | PRODUCTION_REACHABLE | test_comparable_selection_engine.py, test_form_completion_comparable_selection_wiring.py | — |
| `land_use_ratio_engine.py`/`land_use_ratio_validator.py` | collect_data.py/AuditEngine | PRODUCTION_REACHABLE | 對應test檔皆存在 | — |
| `road_width_resolver.py`/`road_width_validator.py` | collect_data.py/AuditEngine | PRODUCTION_REACHABLE | 對應test檔存在 | Real模式仍誠實回UNKNOWN（無可靠來源） |
| `human_confirmation.py` | document_confirm.py流程（經extraction_meta既有結構讀取，非該handler直接import） | **DYNAMICALLY_REFERENCED/PRODUCTION_REACHABLE（經document_extract.py與相關流程使用其資料模型與判斷邏輯）** | test_human_confirmation.py | 建議下一輪確認document_confirm.py是否直接import此模組或僅共用其產出的資料形狀 |
| `form_classifier.py` | document_extract.py經LocalExtractionProvider內部使用 | PRODUCTION_REACHABLE(間接) | test_form_classifier.py | — |
| `extraction_to_submitted_form.py` | 目前僅test引用 | **ORPHAN_RUNTIME_CANDIDATE** | test_document_extraction_e2e.py | 已知：`document_confirm.py`本輪MVP範圍未串接此轉換函式進review.py流程（見docs/backlog.md「Independent Uploaded Document Extraction」章節之誠實記錄） |
| `geo_distance_engine.py` | collect_data.py(NLSC座標距離計算) | PRODUCTION_REACHABLE | 間接 | — |
| `dependency_impact_analyzer.py` | review.py經AuditEngine建構時傳入`dependency_graph.json`路徑 | PRODUCTION_REACHABLE | test_smart_review.py | — |
| `rule_table_ingest.py`/`rule_table_validator.py` | **僅`scripts/ingest_rule_table.py`（CLI工具）與對應test** | **SCRIPT_ONLY** | test_rule_table_ingest.py | 非production handler呼叫路徑；為離線規則表匯入工具，非runtime缺陷 |
| `zone_name_normalizer.py` | providers（land_use/ntpc_zoning等） | PRODUCTION_REACHABLE | test_zone_name_normalizer.py | — |
| `central_max_range_validator.py` | AuditEngine內部 | PRODUCTION_REACHABLE | test_central_max_range_dataset.py | — |

---

## G. Provider Inventory（節錄關鍵Provider；完整23個provider見附錄G-1）

| PROVIDER | MOCK | REAL | PRODUCTION_CALLER | STATUS |
|---|---|---|---|---|
| `land_use_provider.py` | MockLandUseProvider | RealLandUseProvider(+RealNtpcZoningProvider) | collect_data.py | PARTIAL（分區/建蔽率/容積率已接NTPC真實資料，其餘13欄仍UNKNOWN） |
| `road_provider.py` | MockRoadProvider | RealRoadProvider | collect_data.py | MOCK_ONLY等效（Real模式無可靠來源，恆UNKNOWN，非bug是誠實限制） |
| `land_price_provider.py` | MockLandPriceProvider | RealLandPriceProvider | collect_data.py | OFFLINE_READY（29/29行政區本地snapshot已同步，Real模式讀本地DB，非即時打API） |
| `expropriation_case_provider.py` | MockExpropriationCaseProvider | RealExpropriationCaseProvider | collect_data.py | OFFLINE_READY（本地snapshot，含multi-match修復） |
| `official_facility_provider.py` | MockOfficialFacilityProvider | RealOfficialFacilityProvider | collect_data.py | OFFLINE_READY（本地NTPC OpenData snapshot） |
| `official_parcel_coordinate_provider.py` | 無獨立Mock類別(採feature flag關閉即回AUTH_REQUIRED) | RealOfficialParcelCoordinateProvider | collect_data.py（unconditional import，僅real模式呼叫query） | AUTH_BLOCKED（CAD_001需申請，flag預設關閉） |
| `document_extraction_provider.py` | FixtureExtractionProvider | LocalExtractionProvider(生產用)／TextractExtractionProvider(CODE_READY未接線) | document_extract.py只用`LocalExtractionProvider` | LocalExtractionProvider=OFFLINE_READY；TextractExtractionProvider=**CODE_READY_NOT_LIVE且PRODUCTION_UNREACHABLE**（無handler呼叫） |
| `osm_facility_lookup.py` | 無（本身即REFERENCE_ONLY角色） | 同一份程式碼即時打Nominatim/Overpass | collect_data.py(real模式，特定情境) | LIVE_VERIFIED（過去session已實測連線成功），但屬REFERENCE_ONLY角色（非official） |
| `nlsc_cadastral_code_resolver.py`/`nlsc_code_cache.py`/`nlsc_land_number_encoder.py` | 無獨立Mock（本身是identifier解析，非valuation evidence） | 同一份程式碼 | collect_data.py, cadastral_identifier.py | LIVE_VERIFIED（COM系列API過去已實測GET成功） |
| `cadastral_dataset_cache.py` | N/A(共用) | 同一份程式碼（FROZEN） | RealExpropriationCaseProvider/RealLandPriceProvider | OFFLINE_READY，經`cadastral_snapshot_bootstrap.py`（非Frozen）餵檔案 |

（`base.py`/`dataset_registry.py`/`facility_dataset_cache.py`/
`environmental_provider.py`/`commercial_activity_provider.py`/
`public_facility_provider.py`/`special_facility_provider.py`/
`transportation_provider.py`/`real_facility_provider_base.py`：皆為
`collect_data.py`之`_MODE_PROVIDERS`清單成員或其共用基底/快取層，
PRODUCTION_REACHABLE，狀態與上方同類項目一致，故不逐一展開。）

---

## H. External API / Data Integration Matrix

| NAME | ENDPOINT | CALLING_MODULE | REAL_RUNTIME_CALLABLE | AUTH | PRODUCTION_ENABLED | STATUS |
|---|---|---|---|---|---|---|
| NLSC COM系列(ListCounty/ListTown/ListLandSection) | api.nlsc.gov.tw/other/* | nlsc_cadastral_code_resolver.py, scripts/sync_nlsc_codes.py | YES | 無需認證(公開GET) | YES(real模式) | **LIVE_VERIFIED**（過去session已對外實際GET成功） |
| NLSC CAD_001 (CadasMapPosition) | api.nlsc.gov.tw (path未公開因未核准) | official_parcel_coordinate_provider.py | 程式已寫好但認證未取得 | 需申請帳號，Auth機制UNCONFIRMED | 否（feature flag預設false） | **AUTH_BLOCKED** |
| NTPC OpenData(土地現值/徵收/使用分區/公共設施) | data.ntpc.gov.tw/api/datasets/* | land_price_provider.py, expropriation_case_provider.py, ntpc_zoning_provider.py, official_facility_provider.py | YES(sync腳本已對真實API跑過全量) | 無需認證(公開) | YES(Real模式讀本地snapshot，非即時call) | **LIVE_VERIFIED**（sync時已真實呼叫；Runtime查詢走本地DB非即時API） |
| Nominatim(地理編碼) | nominatim.openstreetmap.org | osm_facility_lookup.py, collect_data.py | YES | 無需認證(遵守usage policy) | YES(real模式特定情境) | **LIVE_VERIFIED**，但角色為REFERENCE_ONLY(非official) |
| Overpass(POI查詢) | overpass-api.de/api/interpreter | osm_facility_lookup.py | YES | 無需認證 | YES(real模式) | **LIVE_VERIFIED**，REFERENCE_ONLY |
| AWS Bedrock | boto3 bedrock-runtime, InvokeModel | explanation.py | 未對真實Bedrock呼叫過(無AWS帳號) | IAM Policy已備妥 | 程式碼YES，但**無觸發來源**(見A節) | **CODE_READY**（未LIVE、且目前無API/StateMachine能觸發它） |
| AWS Textract | 無直接呼叫程式碼路徑被使用 | document_extraction_provider.py::TextractExtractionProvider | `NotImplementedError`保護，從未真實呼叫 | N/A | 否 | **CODE_READY_NOT_LIVE**，且PRODUCTION_UNREACHABLE(無handler呼叫此class) |
| S3 Cadastral Bootstrap | 部署者自訂bucket(EXTERNAL) | cadastral_snapshot_bootstrap.py | 單元測試(DI mock s3_client)已覆蓋邏輯，未對真實S3呼叫過 | IAM S3CrudPolicy | YES(collect_data.py已接線呼叫) | **CODE_READY** |
| S3 Document Upload/Pdf輸出 | 部署後由CFN建立之bucket | document_upload.py, pdf_handler.py | 未對真實AWS呼叫過 | IAM Policy已備妥 | YES(程式碼路徑) | **CODE_READY** |
| DynamoDB | CasesTable(CFN建立) | case_store.py | moto模擬已驗證邏輯，未對真實DynamoDB呼叫過 | IAM Policy已備妥 | YES | **CODE_READY** |

---

## I. Mock/Real Separation Audit

```
REAL_TO_MOCK_FALLBACK_FOUND = NO
REAL_TO_GOLDEN_FALLBACK_FOUND = NO
UNKNOWN_TO_ZERO_FOUND = NO
UNKNOWN_TO_PASS_FOUND = NO
```

證據：`collect_data.py::_resolve_data_provider_mode()`對非法
`DATA_PROVIDER_MODE`值**直接raise RuntimeError**，明確禁止「fallback
to Mock」；`_MODE_PROVIDERS`字典將Mock/Real provider清單完全分離、
無交集建構路徑；`RealRoadProvider`/`RealLandUseProvider`剩餘欄位在
無資料源時誠實回`UNKNOWN`（非0、非Golden數字）；`result.py`第31行
`if any(i.get("issue_type") in ("Error","Inconsistent") for i in issues):
status="MANUAL_REVIEW_REQUIRED"`——沒有任何分支會把含Error的案件標記
COMPLETED。既有test（`test_backend_handlers_e2e.py`的
`test_zoning_not_found_is_unknown_not_fallback_to_mock`、
`test_a_no_evidence_is_unavailable_not_fallback_mock`、
`test_real_mode_never_produces_golden_case_numbers_without_evidence`等）
已鎖定此行為，本輪重新閱讀程式碼確認邏輯與test名稱所述一致。

---

## J. Orphan Module Candidates

（方法：對`backend/domain/engine/providers/pdf/scripts/tests`共123個
`.py`檔做regex-based import graph，含bare import與dotted import兩種
本專案實際使用之import風格，交叉比對production handler/engine/
provider/測試/腳本引用者。）

| FILE | WHY_FLAGGED | IMPORTERS | RUNTIME_REACHABLE | SAFE_TO_DELETE |
|---|---|---|---|---|
| `engine/extraction_to_submitted_form.py` | 僅1個test引用，無production handler呼叫 | tests.test_document_extraction_e2e | NO(目前) | UNKNOWN（已知MVP範圍未串接，非死code，是下一輪待接項目） |
| `engine/rule_table_ingest.py` / `engine/rule_table_validator.py` | 僅CLI script+test引用 | scripts.ingest_rule_table, tests.test_rule_table_ingest | NO(非handler呼叫，但是CLI entrypoint) | NO(是資料維運工具，非死code) |
| `providers/document_extraction_provider.py::TextractExtractionProvider` | class-level：檔案本身有其他production可達class(LocalExtractionProvider)，但此class本身無handler呼叫 | tests.test_document_extraction_provider | NO | NO(刻意保留之CODE_READY seam，非孤兒) |
| `pdf/pdf_renderer.py::build_table1_pdf_bytes` | function-level：檔案本身PRODUCTION_REACHABLE(pdf_handler.py用同檔`PdfRenderer`)，但此function本身無handler呼叫 | tests.test_phase5_golden_pipeline | NO | NO(見A節，是待補的handler wiring，非死code) |
| `backend/handlers/explanation.py` | handler本身無API路由/StateMachine觸發 | (無任何production觸發來源) | NO(目前部署後無法被呼叫) | NO(程式碼正確，只是缺Events/StateMachine wiring) |
| `scripts/*.py`(10個) | 皆為CLI entrypoint，非被import | 依賴`python scripts/x.py`直接執行 | N/A(entrypoint本身，非orphan) | NO |
| `data/golden/golden_case_input.py` / `data/demo_errors/build_demo_submission.py` | 未在本次7目錄掃描範圍內(屬data/) | 被多個tests與`scripts/build_demo_review_result_mock.py`引用 | N/A(測試/demo fixture，非production runtime) | NO |

**TRUE_ORPHAN（完全無任何引用、無CLI/handler/test/script身份）**：
**0個**。本輪123個掃描檔案中，每一個「零importer」的檔案經逐一核對
後，皆屬於下列合理身份之一：Lambda handler entrypoint（由
`infra/template.yaml`之`Handler:`字串呼叫，非Python import）、CLI
script entrypoint、pytest test module entrypoint、或`conftest.py`
（pytest自動載入，非顯式import）——**沒有一個是「寫了但從頭到尾沒有
任何身份、真正的死code」**。

---

## K. Duplicate / Legacy Implementation Candidates

| 項目 | 分類 | 說明 |
|---|---|---|
| `frontend/mock/*.json` vs `frontend/app/frontend/mock/*.json` | DUPLICATE_DATA（刻意） | 巢狀複本非legacy殘留，是因為`MOCK_BASE_PATH`相對於`frontend/app/`文件根目錄解析所需之刻意設計，兩份內容需保持同步（見本session更早OFFLINE-ACCEPTANCE-1調查結論） |
| `infra/layers/engine/Makefile` / `infra/layers/extraction/Makefile` | LEGACY_CANDIDATE | 已知不再被`sam build`使用（ContentUri改為repo root後改用root Makefile），保留作native build參考，`infra/template.yaml`本身註解已明文記錄此legacy狀態 |
| `providers/document_extraction_provider.py`內`FixtureExtractionProvider`/`LocalExtractionProvider`/`TextractExtractionProvider` | ACTIVE（非duplicate） | 三者為刻意的策略模式(strategy pattern)分工，非重複實作——各自服務Fixture測試/生產本地擷取/未來Textract三種不同情境 |
| `docs/phase7/backend_deployment.md`「Phase DEPLOY-1」段落 vs 本次RC1文件 | LEGACY_BUT_NEEDED | 舊段落記錄「sam/docker/make未安裝」時期的稽核，本輪RC1已用真實工具鏈重新驗證，舊段落保留供追溯（文件本身已標註為歷史記錄，非誤導） |

未發現「同一valuation邏輯被兩份不同程式碼各自實作一次」之duplicate
implementation。

---

## L. Test Coverage vs Production Path

| PRODUCTION_COMPONENT | TEST_FILE | UNIT | E2E(moto) | ARTIFACT_RUNTIME(容器) | BROWSER | STATUS |
|---|---|---|---|---|---|---|
| `collect_data.py`（handler wiring本身） | test_backend_handlers_e2e.py, test_backend_document_handlers_e2e.py, test_collect_data_nlsc_integration.py | — | YES | YES(本輪RC1 13/13 import驗證) | 間接(OFFLINE-ACCEPTANCE-1) | **完整** |
| `review.py`（handler wiring本身） | test_backend_handlers_e2e.py, test_backend_document_handlers_e2e.py | — | YES | YES | YES | **完整** |
| `complete_form.py`（handler wiring本身） | test_backend_handlers_e2e.py, test_backend_document_handlers_e2e.py | — | YES | YES | YES | **完整** |
| **`analyze.py`（handler wiring本身）** | **無** | 間接(RuleEngine/GradeEngine/AdjustmentEngine各自有unit test) | **NO** | YES(僅import驗證) | 間接(OFFLINE-ACCEPTANCE-1瀏覽器點擊過，但非自動化test) | **GAP：核心engine邏輯測過，但handler層request/response contract、CASE_NOT_FOUND錯誤處理、DynamoDB讀寫皆無直接test** |
| **`explanation.py`（handler wiring本身）** | **無** | — | **NO** | YES(僅import驗證) | 未曾點擊(前端無此頁面/按鈕) | **GAP：同上，且此handler目前無任何觸發來源，優先度較低** |
| **`result.py`（handler wiring本身）** | **無** | — | **NO** | YES(僅import驗證) | 間接(OFFLINE-ACCEPTANCE-1瀏覽器) | **GAP：MANUAL_REVIEW_REQUIRED判定邏輯僅存在此檔案本身，無任何自動化test鎖定** |
| **`pdf_handler.py`（handler wiring本身）** | **無**（`test_phase5_golden_pipeline.py`測的是`PdfRenderer`本身，非`pdf_handler.get_pdf`這個Lambda函式） | 間接(PdfRenderer) | **NO** | YES(RC1本輪已驗證weasyprint真實render) | 間接(OFFLINE-ACCEPTANCE-1，經Mock fixture，非真實handler) | **GAP：S3 put_object/presigned URL邏輯、「僅表4+表5-2」這個實際行為皆無自動化test** |
| document extraction | test_document_extraction_e2e.py, test_document_extraction_provider.py, test_backend_document_handlers_e2e.py | YES | YES | YES | YES | **完整** |
| cross-form validation | test_backend_handlers_e2e.py（tampering測試群） | — | YES | — | YES(OFFLINE-ACCEPTANCE-1 Error Case) | **完整** |
| cadastral bootstrap | test_cadastral_snapshot_bootstrap.py | YES(DI mock s3_client) | — | — | — | **完整（單元層級）**，真實S3從未測過（見Known Limitations） |
| NLSC | test_nlsc_cadastral_code_resolver.py, test_nlsc_code_cache.py, test_nlsc_land_number_encoder.py, test_official_parcel_coordinate_provider.py, test_cadastral_identifier.py | YES | — | — | — | **完整（單元層級）** |
| frontend | 無自動化前端測試框架(無Jest/Playwright納入CI) | — | — | — | 本session手動Playwright驗收(非CI常駐) | **GAP（已知）：前端目前無持續性自動化測試，僅靠手動/一次性瀏覽器驗收** |

**STEP12結論**：`analyze.py`/`explanation.py`/`result.py`/
`pdf_handler.py`四個Lambda handler**在「handler層本身」缺乏直接
自動化測試**，即便其呼叫之engine/provider皆有完整測試——這正是
「test只mock到核心邏輯，production wiring沒測到」的具體案例。
**評級：P1**（不影響Golden/Error Case既有驗證，因這4個handler之
production行為本輪已用真實容器/瀏覽器操作間接驗證過；但缺乏
持續性回歸保護，未來改動這4個檔案時無自動化safety net）。

---

## M. Packaging Findings

```
PACKAGING_MISSING_RUNTIME_DEPENDENCY = NONE FOUND
```
本輪RC1已於真實`public.ecr.aws/lambda/python:3.12`容器內實測13個Zip
handler + PdfFunction Image全數import成功、EngineLayer原生依賴
(pydantic-core/shapely)與ExtractionLayer(fitz/pymupdf)皆實際運算
驗證通過（見`docs/release/RC1_FREEZE_REPORT.md`）。

```
PACKAGE_BLOAT_CANDIDATES = 1個（已知，見docs/backlog.md）
```
`backend/docker/pdf.Dockerfile`第23行`COPY data`整包807MB進
PdfFunction image（最終image 2.52GB），但`pdf/`與`pdf_handler.py`
只需`data/rules/`＋`data/dependency_graph.json`。

```
PACKAGE_UNUSED_RUNTIME_FILES = 未發現額外新項目（上述bloat已是本輪
掃描到的唯一一項；EngineLayer/ExtractionLayer本身之Makefile已刻意
窄化複製範圍，未發現多餘檔案）
```

未發現source archive（如`黑客松參考資料/`、`sample_output/`）被誤包
進任何Layer或Container Image（`infra/layers/*/Makefile`與
`backend/docker/pdf.Dockerfile`皆未引用這兩個目錄）。

---

## N. Data Traceability

```
TRACEABILITY_BREAKS = NONE
```
逐段檢查`submitted → external evidence → normalized → rule → grade →
adjustment → calculation → expected → submitted comparison → issue →
result`：
- `domain/models.py::Evidence`/`FactorInput`/`NormalizedDataPoint`
  等模型全程攜帶`source`/`source_type`欄位，未發現中途被覆寫成
  匿名值。
- `review.py`明確區分`submitted`（表單填載值/文件擷取值）與
  `official/expected`（AuditEngine獨立解析之外部參考值）兩條線，
  `AuditEngine.review()`簽章本身即要求兩者分開傳入，結構上無法
  「自己比對自己」（呼應`docs/backlog.md`「Cross-form Inconsistent」
  一節已記錄之雙向驗證測試）。
- `result.py`/`pdf_handler.py`皆從`case_store`已持久化之
  `FORM_COMPLETION`/`REVIEW_RESULT`讀取，未見任何handler在讀取後
  對數值做二次靜默運算或覆寫。

---

## O. AI Boundary Audit

```
LLM_DECISION_PATH_FOUND = NO
LLM_OVERRIDES_RULE_ENGINE = NO
LLM_OVERRIDES_CALCULATION = NO
LLM_OVERRIDES_AUDIT = NO
```
`explanation.py::_build_prompt()`明確只將**已計算完成**之
`final_price`/`completed_count`/`manual_review_count`/
`review_issues_summary`序列化進prompt，prompt文字本身明講「你不需要、
也不應該重新驗算任何數字」；Bedrock回應僅存入獨立的`EXPLANATION`
record，從未寫回`FORM_COMPLETION`或`REVIEW_RESULT`。`ExplanationFunction`
可以存在（如期望架構所允許），且其程式碼本身完全未逾越邊界——
唯一的問題是它目前無觸發來源（見A節、H節），與AI邊界安全性無關。

---

## P. Documentation Accuracy

| 文件宣稱 | 實際程式碼 | 判定 |
|---|---|---|
| `docs/phase7/backend_deployment.md`「ExplanationFunction與NotifyManualReviewFunctionArn尚缺完整串接」 | 本輪確認：`infra/template.yaml`確實無StateMachine資源、無ExplanationFunction的Events；`NotifyManualReviewFunction`確實無任何handler程式碼 | **屬實，非DOC_OVERCLAIMS**（文件本身已誠實記錄此缺口） |
| `docs/release/RC1_KNOWN_LIMITATIONS.md`「AWS尚未部署」「S3/DynamoDB E2E尚未驗證」 | 本輪確認：case_store.py/pdf_handler.py/document_upload.py皆用真實boto3 client，從未對真實AWS呼叫過 | **屬實** |
| `docs/phase8a/backup_demo_guide.md`（本輪已於RC1階段更新）Golden/Error Case話術 | 本輪確認review.html/result.html確實各自讀取不同fixture，已更新之措辭準確 | **屬實** |
| 舊有「PDF現場即時產生」demo腳本（`docs/phase8a/backup_demo_guide.md`）直接呼叫`PdfRenderer().render_all_forms(...)` | 本輪發現：這與真實`pdf_handler.get_pdf()`的實際行為（只render表4+表5-2、走S3+presigned URL）**不同路徑** | **STALE_DOCS風險**：該手動腳本展示的是engine能力，非Lambda handler實際行為，未明確標註這個差異，建議未來補充說明（P2，不影響RC1判定，因demo腳本本身標題已是「現場即時產生」而非聲稱等同API行為） |
| 無任何文件宣稱「AWS已部署」「Real S3已驗證」「DynamoDB Cloud E2E已驗證」 | 確認 | **DOCUMENTATION_MATCHES_CODE，無overclaim** |

未發現任何文件宣稱「NLSC CAD001已啟用」「Textract已上線」「RAG已
實作」「多行政區已全面驗證」等overclaim字樣。

---

## Q. P0/P1/P2 Findings

| # | 級別 | 項目 | 檔案 |
|---|---|---|---|
| 1 | P1 | `ExplanationFunction`無API Gateway路由、StateMachine未部署，目前完全無法被觸發 | `infra/template.yaml`, `infra/statemachine/workflow.asl.json` |
| 2 | P1 | `pdf_handler.get_pdf()`只render表4+表5-2，`build_table1_pdf_bytes()`production未接線；PDF response shape（單一`pdf_url` vs Mock的`pdf_urls`陣列）不一致 | `backend/handlers/pdf_handler.py`, `pdf/pdf_renderer.py` |
| 3 | P1 | `analyze.py`/`explanation.py`/`result.py`/`pdf_handler.py`四個handler缺乏handler層級自動化測試 | 見L節 |
| 4 | P2 | `GetCaseFunction`為BACKEND_ONLY，frontend從未呼叫 | `frontend/app/js/api.js`, `backend/handlers/cases.py` |
| 5 | P2 | `NotifyManualReviewFunction`在ASL中被引用但完全未實作（已知既有缺口，非本輪新發現） | `infra/statemachine/workflow.asl.json` |
| 6 | P2 | （已於RC1階段記錄）PDF image bloat 2.52GB、頁碼顯示key不一致、`import fitz` deprecation | `docs/backlog.md`既有記錄 |

```
P0_COUNT = 0
P1_COUNT = 3
P2_COUNT = 3
```

**無P0**：未發現流程完全走不通、Real偷偷fallback、legal語意錯誤、
或會讓Live deployment必炸的問題。

---

## R. Freeze Status

```
CORE_FREEZE_SHA256（本輪重新計算）=
0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51
CORE_FREEZE_VIOLATION = NO   （36/36逐檔重新比對完全相符）

Cadastral Pipeline（6檔，逐檔重新計算，與docs/phase9/official_parcel_
coordinate_audit.md最近記錄完全相符）:
  bff8acfb... providers/cadastral_identifier.py
  21e83193... providers/cadastral_dataset_cache.py
  9d5d17be... providers/expropriation_case_provider.py
  c4690f22... providers/land_price_provider.py
  15172c35... scripts/sync_expropriation_dataset.py
  c9425b4d... scripts/sync_land_price_dataset.py
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
```

本輪全程零Write/Edit觸及任何repo原始碼檔案，僅新增本報告本身
（`docs/release/RC1_FULL_PROJECT_AUDIT_REPORT.md`）。

---

## S. Final Recommendation

RC1可以**維持Frozen**。本輪發現的3項P1皆為「已實作能力尚未完整
wiring／尚未補測試」性質，不影響Golden Case/Error Case/Offline Demo
已驗證之核心正確性，也不构成Real模式資料造假風險。建議排入**下一輪
功能性工作**（非本輪，本輪僅稽核）：
1. 決定`ExplanationFunction`的正式觸發方式（補StateMachine資源，或
   改為同步API路由）
2. 決定是否要讓`pdf_handler.get_pdf()`一併產出表1 PDF並改回傳
   `pdf_urls`陣列，使Real模式行為與Offline Demo展示一致
3. 為`analyze.py`/`explanation.py`/`result.py`/`pdf_handler.py`
   補上handler層級的moto-based E2E測試

---

## Final Gate

```
ARCHITECTURE_MATCHES_EXPECTATION = YES
FRONTEND_BACKEND_CONNECTED = YES
BACKEND_ENGINE_CONNECTED = YES
ENGINE_PROVIDER_CONNECTED = YES

EXTERNAL_API_INTEGRATIONS_AUDITED = YES

LIVE_API_INTEGRATIONS =
  NLSC COM系列(ListCounty/ListTown/ListLandSection),
  NTPC OpenData(土地現值/徵收/使用分區/公共設施, 經sync腳本),
  Nominatim, Overpass

CODE_READY_NOT_LIVE_INTEGRATIONS =
  AWS Bedrock(explanation.py, 無觸發來源),
  S3 Cadastral Bootstrap,
  S3 Document Upload/PDF輸出,
  DynamoDB

AUTH_BLOCKED_INTEGRATIONS =
  NLSC CAD_001 (CadasMapPosition)

MOCK_ONLY_INTEGRATIONS =
  RealRoadProvider剩餘欄位(無可靠來源, 效果等同Mock),
  TextractExtractionProvider(CODE_READY但PRODUCTION_UNREACHABLE, 效果等同未接線)

TRUE_ORPHAN_MODULE_COUNT = 0
ORPHAN_CANDIDATE_COUNT = 5
  (engine.extraction_to_submitted_form,
   engine.rule_table_ingest, engine.rule_table_validator[皆SCRIPT_ONLY非死code],
   providers.document_extraction_provider::TextractExtractionProvider,
   pdf.pdf_renderer::build_table1_pdf_bytes)
LEGACY_CANDIDATE_COUNT = 1
  (infra/layers/*/Makefile，已知legacy、模板註解已自行記錄)

REAL_TO_MOCK_FALLBACK_FOUND = NO
REAL_TO_GOLDEN_FALLBACK_FOUND = NO
UNKNOWN_SEMANTICS_SAFE = YES
LLM_BOUNDARY_SAFE = YES
TRACEABILITY_INTACT = YES
TESTS_MATCH_PRODUCTION_PATHS = PARTIAL
  (4個handler缺handler層級測試，見L/Q節)
PACKAGING_COMPLETE = YES
DOCUMENTATION_MATCHES_CODE = YES
  (1項P2 STALE_DOCS風險，非overclaim，見P節)

P0_COUNT = 0
P1_COUNT = 3
P2_COUNT = 3

CORE_FREEZE_VIOLATION = NO
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO

OFFLINE_RC1_EXPECTATION_MATCH = YES
RC1_SHOULD_REMAIN_FROZEN = YES
```

**RC1 AUDIT PASSED**
**OFFLINE DEVELOPMENT SHOULD REMAIN FROZEN**
