# Phase 8A — Offline Readiness Report

> 本輪Acceptance Review過程中，透過實際端到端執行（非僅檢查class可
> instantiate）發現並修復2項真實HIGH/CRITICAL功能缺陷，詳見文件末尾
> 「本輪修復記錄」。以下矩陣反映修復後之現況。

| Area | Status | Evidence | Remaining Work |
|---|---|---|---|
| Official Requirement Understanding | READY | Phase 1六份文件皆有Source citation與Evidence Classification | 無 |
| Rule Engine | READY | 本輪重新執行`RuleEngine.grade()`，5項Golden Case因素全數PASS | 無 |
| Calculation | READY | `CalculationEngine`本輪重新執行，完整精度鏈驗證 | 無 |
| Golden Case | READY | 本輪重新對照Source逐項列表（見STEP 5結果），5/5 PASS | 無 |
| Form Completion | READY | `FormCompletionEngine`本輪重新執行，final_value=212958 | 無 |
| PDF | READY（Prototype/Fallback，非Official Production PDF） | 本輪重新產生，6頁，qpdf結構檢查通過，中文可見，212958可擷取 | 官方書表本身不可填（Phase5已確認），無法產出「Official Production PDF」，僅能是Fallback Reproduction |
| Smart Review | READY（本輪修復後） | `review.py`原本為**硬編碼空result之HIGH FUNCTIONAL BLOCKER**，本輪發現並修復，現已真正呼叫`AuditEngine.review()`，經tamper測試證實非虛假通過 | 見下方修復記錄 |
| Cross-form | READY | `CrossFormValidationEngine`本輪重新執行，正確區分Error vs Inconsistent | 無 |
| Frontend | READY | 8個App Page + index.html，Mock Mode運作，Loading/Error State存在，無新框架，Attribution保留 | 未於真實AWS URL驗證（Phase 7B範圍） |
| Backend | READY（本輪修復後） | 9個Lambda handler；本輪發現`collect_data.py`**每次呼叫必定crash**之CRITICAL bug並修復，完整E2E鏈（create→collect→analyze→complete-form→review）本輪以moto實測通過 | 未部署至真實AWS |
| Mock Mode | READY | `js/config.js`預設`MODE: "mock"`，7個mock JSON檔案存在且與真實Pydantic序列化一致 | 無 |
| Bedrock Code | CODE_READY（設定完整性MEDIUM待改善） | `explanation.py`語法正確、有fallback降級邏輯；`BEDROCK_MODEL_ID`環境變數未於`template.yaml`顯式定義（見STEP 8重新評估） | 部署時建議於IaC明確定義此變數；實際呼叫需RUNTIME_VALIDATION_REQUIRED |
| AgentCore | NOT IMPLEMENTED（DOCUMENTED + OPTIONAL/P1） | `aws_services.md` Part G僅有設計文件與S3來源桶IaC規劃，無實際Knowledge Base資源、無檢索/Citation程式碼 | 見STEP 9精確分類；非本次競賽P0要求（Phase 1 evaluation_focus.md未將AgentCore列為必做） |
| AWS IaC | CODE_READY | `template.yaml`（19資源）、`workflow.asl.json`皆通過cfn-lint/JSON驗證 | 需真實AWS帳號執行`sam deploy` |
| Security | PASS | 本輪重新全掃描，0筆真實憑證洩漏 | 建議補充repo-root `.gitignore`（未發現，非安全漏洞，屬預防性建議） |
| Demo Script | READY | `demo_script.md`涵蓋16項故事要素，時長5-6分鐘，AWS為支撐非主體 | 無 |
| Backup Demo | READY | Mock Mode本身即為完整Offline Backup（見STEP 12） | 無 |

---

## 本輪修復記錄（CRITICAL/HIGH，環境內可修復，已修復）

### 修復1：`collect_data.py` 每次呼叫必定崩潰（CRITICAL）

**問題**：`body.get("base_parcel_factors", []) + body.get("comparable_factors", {})`
嘗試以`+`連接list與dict，**即便在完全空白的request body下也會崩潰**
（`TypeError: can only concatenate list (not "dict") to list`），此為
`docs/phase4/frontend_api_contract.md`明文之標準request格式，非邊緣案例。

**修復**：改為明確建構dict結構：
```python
"user_submitted_factors": {
    "base_parcel_factors": body.get("base_parcel_factors", []),
    "comparable_factors": body.get("comparable_factors", {}),
}
```

**驗證**：`tests/test_backend_handlers_e2e.py::TestCollectDataCrashFix`（3項測試），
moto模擬DynamoDB實際呼叫確認不再崩潰。

### 修復2：`review.py` 從未真正呼叫審查邏輯（HIGH FUNCTIONAL BLOCKER）

**問題**：舊版`review.py`雖建立`AuditEngine`實例，但**從未呼叫其`.review()`
方法**，直接回傳硬編碼之`{"issues": [], ...}`，無論輸入為何。此為本次
Acceptance Review明確警示之「不要只因AuditEngine instance successfully
created就宣稱Smart Review READY」情境，經檢視原始碼確認**完全命中**。

**修復**：新增`backend/handlers/case_reconstruction.py`共用重建邏輯
（同時重構`complete_form.py`以消除重複），`review.py`改為完整重建
`CompetitionCase`+區域因素清單，從已儲存之`FORM_COMPLETION`結果重建
`SubmittedFormData`，並**實際呼叫**`audit.review(case, submitted,
regional_base, regional_comp)`。

**驗證**：`tests/test_backend_handlers_e2e.py::TestReviewActuallyRunsAuditLogic`
（3項測試），其中`test_review_detects_a_deliberately_corrupted_stored_value`
刻意竄改已儲存資料後重新呼叫`review()`，證實能真實偵測（非虛假通過）。

**誠實限制（本輪修復後仍存在，已於review.py docstring記載）**：因無真實
PDF/Textract資料抽取來源，此MVP版本審查的是「案件自身已計算結果之
再現性/一致性」，尚非「與真實填寫在PDF上的獨立提交值」比對。此限制
與Phase 6 `document_extraction_spec.md`所述之`TextractAdapter`擴充點
完全一致，未來接上真實資料源時無需修改`AuditEngine`本身。
