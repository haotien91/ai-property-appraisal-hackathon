# EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT

**日期**：2026-09-10
**性質**：STEP 3B — 讓Phase 3A Evaluation Standard Importer產生的Rule
Candidate，經過Human Review（Confirm/Reject/Edit）後才能真正影響
Analyze/CompleteForm/Review。延續STEP 2（`CaseRulePackage`/
`CaseRuleRepository`）與STEP 3A（`engine/evaluation_standard_importer.py`）
既有基礎，本輪**不新增**任何AI/LLM、前端、隊友資料整合、
GradeEngine/AdjustmentEngine改寫、第二套Calculator。

---

**STEP5 更新（2026-09-11）**：`evaluation_standard.py` 的
`get_evaluation_standard_candidate()`／`confirm_evaluation_standard()`
（本輪程式邏輯**未修改**）是 Blind Case 與 Golden 雙輸入 E2E 測試中
Human Confirmation Gate 的唯一入口——本輪新增測試
（`tests/test_competition_blind_case.py`／
`tests/test_competition_dual_input_e2e.py`）刻意透過這兩個 handler
（而非直接呼叫 `CaseRuleRepository.confirm()`）完成確認，符合 STEP5
§15「禁止直接instantiate RuleEngine然後assert」的精神延伸——連
Repository 層的呼叫也盡量走 handler 層。另外新增了 `document_upload.py`
的 `document_type` 選填欄位（見
`COMPETITION_E2E_PHASE5_REPORT.md` §1），使 Evaluation Standard 上傳
路徑可與查估書表上傳路徑明確區分，不影響本報告原有 5 個 handler 的
邏輯本身。

## 1. Candidate Persistence

**重用**STEP 2既有`CaseRuleRepository`/`save_candidate()`，**未**建立
第二個平行Rule Repository。`engine/evaluation_standard_importer.py`的
`build_case_rule_package_from_pdf()`已回傳一個真正的`CaseRulePackage`
（STEP 2既有domain model），Phase 3B新增的
`backend/handlers/evaluation_standard.py::extract_evaluation_standard()`
呼叫方式為：

```python
package, _report = build_case_rule_package_from_pdf(...)
repository.save_candidate(package)   # STEP 2既有方法，未修改簽章
```

依`case_id + package_id`可完整重新讀回（`repository.get_package(case_id,
package_id)`，STEP 2既有方法），不侷限於當次Lambda呼叫的Python物件生命
週期——與STEP 2「跨Lambda warm container持久化」之既有設計原則一致。

## 2. Status State Machine

延續STEP 2/3A既有`CaseRulePackageStatus`列舉，**新增**`AMBIGUOUS`值
（Phase 3A已加入，本輪沿用未再變更）：

```
DRAFT / EXTRACTED / PARTIAL / AMBIGUOUS / CONFIRMED / REJECTED
```

允許的轉換（本輪實測驗證）：

- `EXTRACTED → CONFIRMED`：`CaseRuleRepository.confirm()`（STEP 2既有
  方法，CONFIRMED Gate未修改）直接接受。
- `PARTIAL → 編輯/補齊 → CONFIRMED`：透過本輪新增之
  `resolve_candidate_factor_mapping()`（補齊未mapping因素）與
  `submit_human_edits()`（修正欄位）後confirm。
- `AMBIGUOUS → 人工選擇/修改 → CONFIRMED`：同上，差別僅在於
  package層級status在補齊前為AMBIGUOUS（代表目前完全沒有任何候選
  乾淨可用）。

**任何non-CONFIRMED package絕不會進入正式RuleEngine**——
`rule_engine_factory.build_rule_engine_for_case()`（STEP 2既有，本輪
未修改其resolution邏輯本身）只掃描`status==CONFIRMED`的package；
EXTRACTED/PARTIAL/AMBIGUOUS/DRAFT/REJECTED一律視為「尚無confirmed
package」，落回STATIC_LOCAL並帶`CASE_RULE_NOT_CONFIRMED`警示（見
第11節）。REJECTED package不會被自動重新套用——`get_confirmed_package()`
與`build_rule_engine_for_case()`皆只認`status==CONFIRMED`，REJECTED
package即使其`regional_rules`/`individual_rules`資料仍在，也永遠不會
被讀取用於grading（第10節有實測）。

## 3. Human Edit

新增`CaseRuleRepository.submit_human_edits(case_id, package_id, edits,
edited_by)`與`resolve_candidate_factor_mapping(case_id, package_id,
candidate_id, canonical_factor_id, edited_by)`（`backend/handlers/
case_rule_repository.py`）。

**可修正欄位**（對照使用者要求逐項確認）：

| 使用者要求 | 實作方式 |
|---|---|
| factor mapping | `resolve_candidate_factor_mapping()`——重新對Importer保存的完整candidate（bands/matrix皆已存於`metadata['extraction_candidates']`，見Phase 3A `RuleCandidate.to_dict()`）套用人工指定的`canonical_factor_id`，呼叫`engine/evaluation_standard_importer.py`新增之`reevaluate_candidate_with_canonical_factor()`重新驗證 |
| grade label / grade condition | `submit_human_edits()`，`field="grade_label"` |
| bounds | `field="lower_bound"`/`"upper_bound"` |
| inclusive flags | `field="lower_inclusive"`/`"upper_inclusive"` |
| unit | `field="unit"` |
| adjustment matrix | `field="adjustment_matrix"`——**自動套用到同一factor的所有grade列**（見下方說明） |
| max adjustment | `field="max_adjustment"`——同上，自動套用到同一factor所有列 |
| applicability | `field="applicability"`，`new_value="NOT_APPLICABLE"`時直接移除該規則列 |

**matrix/max_adjustment的「同factor所有列」設計**：既有慣例（STEP 2/3A
皆遵守）是同一factor的每個grade列都攜帶**完全相同**的完整矩陣（見
`rule_schema.json`與`RuleTableValidator._check_matrix_consistency_
within_factor`）。若只修改單一列的矩陣，會立即被既有validator判定為
「同一因素內各等級列matrix不一致」ERROR。`submit_human_edits()`因此
對這兩個欄位主動套用到同factor全部列，避免人工編輯反而製造出既有
validator會抓到的新錯誤。

**Provenance（每次修改必留trace）**：新增domain model
`RuleFieldEdit`（`domain/models.py`）：

```python
class RuleFieldEdit(BaseModel):
    rule_id: str
    field: str
    original_extracted_value: Any = None
    confirmed_value: Any = None
    edited: bool = True
    confirmed_by: Optional[str] = None
    confirmed_at: datetime
```

`CaseRulePackage`新增`edit_history: List[RuleFieldEdit]`欄位，
**append-only**——每次`submit_human_edits()`/
`resolve_candidate_factor_mapping()`呼叫都只PUSH新entry，從不刪除或
覆寫既有entry。實測（第14節TEST 7/8）證實：修改前的原始值（Importer
真正擷取出的值）被完整保留在`edit_history[0].original_extracted_value`
（或該欄位第一次被編輯時的entry），即使規則列本身的CURRENT值已變成
人工修正後的新值。

## 4. Confirmation Validation

`CaseRuleRepository.confirm()`（STEP 2既有，Phase 3A已修正為
scope-分離驗證，本輪**未再修改**該方法本身的驗證邏輯）在CONFIRM前
重新執行`validate_package_rules_scoped()`（STEP 2/3A既有函式，分別對
regional_rules／individual_rules各自呼叫`RuleTableValidator`，避免
建蔽率/容積率/地勢等跨scope同名因素被誤判——見Phase 3A報告第11節）。

- **ERROR**：`confirm()`拋出`CaseRulePackageInvalidError`（STEP 2既有
  例外類別），`backend/handlers/evaluation_standard.py::
  confirm_evaluation_standard()`（新增handler）接住後回傳HTTP 409、
  `error.code="CASE_RULE_VALIDATION_FAILED"`，並在頂層
  `validation_issues`欄位列出每一筆`str(ValidationIssue)`——非僅
  一句籠統訊息。**package狀態不會被改成CONFIRMED**（實測第14節
  TEST 6）。
- **WARNING**：不阻擋Confirm（`RuleTableValidator.has_errors()`只看
  ERROR），但`save_candidate()`/`submit_human_edits()`皆會把
  WARNING訊息合併進`package.warnings`（STEP 2既有機制沿用），
  CONFIRMED後這些warning仍保留在package紀錄裡，供後續review查閱。

## 5. Source Provenance

`extract_evaluation_standard()`（新增handler）確保CONFIRMED後仍可回答
「這條規則從哪一頁來？」：

- `package.source_document`：實際上傳的S3 object key（**非**臨時
  `/tmp/{document_id}.pdf`路徑——handler在呼叫Importer後明確覆寫
  `package.source_document = s3_key`，避免provenance指向一個Lambda
  執行完就消失的暫存路徑）。
- `package.source_sha256`：Importer對PDF bytes計算之SHA-256
  （`engine/evaluation_standard_importer.py`既有）。
- `regional_rules`/`individual_rules`每筆記錄自帶`source_page`
  （PDF頁碼，1-indexed）與`source_note`（含原始備註句子
  `note_text_candidate`，即`extraction_method`所依據的原始文字）。
- `metadata['extraction_candidates']`保留**每一個**候選（不論是否
  成功mapping）的完整`bands`/`matrix`/`note_text_candidate`/
  `canonical_factor_id`/`status`/`issues`——即使某候選從未變成正式
  規則列，其原始文字與頁碼仍完整可查。
- `confirmation metadata`：`confirmed_at`/`confirmed_by`（STEP 2既有
  欄位）＋本輪新增`edit_history`（第3節）。

## 6. Candidate Review Model

新增`build_review_dto(package: CaseRulePackage) -> dict`（純函式，
`backend/handlers/case_rule_repository.py`），輸出：

```json
{
  "package_id": "...", "case_id": "...", "status": "PARTIAL",
  "summary": {
    "total_factors": 47, "extracted": 19, "partial": 0, "ambiguous": 28,
    "validation_error_count": 0, "warning_count": 33
  },
  "factors": [
    {
      "factor_id": "CAND-P2-00", "scope": "regional",
      "original_factor_text": "以區段內主要道路寬度來衡量",
      "canonical_factor_id": "主要道路寬度",
      "grade_conditions": [{"grade": "優", "grade_code": 1, "condition_text": "30m以上", ...}, ...],
      "matrix": {"1": {"1": 0, "2": 3.75, ...}, ...},
      "max_adjustment": 15.0,
      "status": "EXTRACTED", "issues": [], "requires_human_review": false
    }
  ]
}
```

此為`get_evaluation_standard_candidate`/`extract_evaluation_standard`/
`submit_evaluation_standard_edits`/`confirm_evaluation_standard`/
`reject_evaluation_standard`五個handler**共同**回傳的body結構——
未實作UI，但欄位已對齊前端可直接消費的形狀（factor-level
`requires_human_review`布林值可直接驅動「需要人工複核」清單／badge）。

## 7. Minimal API / Handler

新增`backend/handlers/evaluation_standard.py`，五個handler：

| Handler | Method+Path | 對應能力 |
|---|---|---|
| `extract_evaluation_standard` | POST `/api/cases/{id}/evaluation-standard/{document_id}/extract` | A：已上傳文件→執行Importer→save candidate |
| `get_evaluation_standard_candidate` | GET `/api/cases/{id}/evaluation-standard/{package_id}` | B：讀取candidate供人工review |
| `submit_evaluation_standard_edits` | POST `/api/cases/{id}/evaluation-standard/{package_id}/edits` | C：提交人工修正→validate |
| `confirm_evaluation_standard` | POST `/api/cases/{id}/evaluation-standard/{package_id}/confirm` | D：confirm |
| `reject_evaluation_standard` | POST `/api/cases/{id}/evaluation-standard/{package_id}/reject` | E：reject |

**上傳機制100%重用既有架構**：`extract_evaluation_standard`**不**新建
上傳endpoint——PDF上傳沿用既有`document_upload.py::request_upload`
（`RequestUploadFunction`，完全未修改）之presigned URL流程，與案件
書表PDF共用同一套S3上傳機制；`extract_evaluation_standard`只負責
「文件已上傳完成後」的讀取與解析，其S3讀取/驗證邏輯（HeadObject→
大小檢查→下載→magic bytes檢查）幾乎逐行照抄既有
`document_extract.py::extract_document()`的既驗證過模式。

`infra/template.yaml`新增五個對應`AWS::Serverless::Function`資源
（`ExtractEvaluationStandardFunction`等），`ExtractEvaluationStandard
Function`額外掛載`ExtractionLayer`（pymupdf，與既有
`DocumentExtractFunction`相同理由），其餘四個僅掛載`EngineLayer`。

## 8. Atomic Confirmation

**結論：既有單一`PutItem`寫入已滿足使用者要求的核心不變量，未新增
DynamoDB TransactWriteItems**（評估後判定非必要，理由如下）。

`CaseRuleRepository.confirm()`（STEP 2既有實作）把`status=CONFIRMED`
與`regional_rules`/`individual_rules`寫在**同一個Pydantic物件**、
序列化後透過**同一次**`case_store.put_record()`（底層為DynamoDB
`PutItem`，單一item層級具原子性）寫入——因此「status已CONFIRMED但
rules未完整寫入」或「rules寫入一半status未更新」兩者皆**結構上
不可能發生**：status與rules是同一個item裡的欄位，不存在兩者分開
寫入的中間狀態。

唯一存在的「兩步驟」是confirm()額外寫入的
`CASE_RULE_CONFIRMED_POINTER`（第二個獨立item）。但實際追蹤
`rule_engine_factory.build_rule_engine_for_case()`（正式production
resolution路徑）後確認：**它完全不讀取這個pointer**——而是呼叫
`list_packages()`直接掃描、過濾`status==CONFIRMED`。因此就算
package本身寫入成功、pointer寫入失敗（例如Lambda執行中途被terminate），
production grading邏輯依然正確（因為它根本不依賴pointer）；唯一受
影響的是`get_confirmed_package()`這個輔助方法與`confirm()`自己的
「是否已有其他CONFIRMED package」防呆檢查，兩者皆有
defense-in-depth處理pointer與實際狀態不一致的情形（`get_confirmed_
package()`會在pointer所指package實際status非CONFIRMED時回傳None，
而非信任一個可能過時的pointer；`rule_engine_factory`本身若掃到
「超過1個CONFIRMED package」的不變量被打破，會直接拋出
`CaseRulePackageInvalidError`而非猜測，見STEP 2 CASE_SCOPED_RULE_
ARCHITECTURE_REPORT.md）。基於此分析，本輪判定新增
TransactWriteItems所增加的實作複雜度與部署風險，並未換取任何目前
未被涵蓋的正確性保證，故不採用。

## 9. Case Isolation

**結構性強制，非額外檢查**：`CaseRuleRepository`每個方法都以
`case_id`作為DynamoDB `PK=CASE#<case_id>`的一部分——`get_package
(case_id, package_id)`在錯的`case_id`下查詢，結構上就是查詢一個
不存在的分區鍵組合，**必定**回傳`None`（`confirm()`/`reject()`/
`submit_human_edits()`/`resolve_candidate_factor_mapping()`皆先呼叫
`get_package()`，None時一律`raise CaseRulePackageNotFoundError`）。
`backend/handlers/evaluation_standard.py`的五個handler皆直接把
URL路徑的`{id}`當作`case_id`傳入，未額外實作「ownership check」——
因為底層儲存設計本身就是ownership check（見第14節TEST 9實測：
Case B嘗試confirm屬於Case A的package_id，得到404，Case A自己仍可
正常confirm）。

## 10. Multiple Packages

`save_candidate()`允許同一case多次呼叫（不同`package_id`）——本輪
`extract_evaluation_standard()`預設`package_id = f"EVALSTD-
{document_id}"`（每次上傳新文件產生新package_id，天然不衝突）。
第一次上傳錯誤 → `reject_evaluation_standard()` → 第二次上傳 →
`confirm_evaluation_standard()`，完整流程已實測（第14節TEST 11）。
「同一Case同時最多一個CONFIRMED Package」沿用STEP 2既有不變量
（`confirm()`內建之`CaseRulePackageAlreadyConfirmedError`檢查，
本輪未修改該檢查邏輯），實測確認（TEST 12）。

## 11. No Silent Static Result

延續STEP 2既有設計（`rule_engine_factory.build_rule_engine_for_case()`
之`CASE_RULE_NOT_CONFIRMED` warning機制，本輪未修改）——已上傳但未
CONFIRMED的evaluation-standard candidate存在時，Analyze/CompleteForm/
Review的回應與儲存紀錄裡`rule_resolution_status`仍為`"STATIC_LOCAL"`，
且`rule_resolution_warnings`陣列明確包含`"CASE_RULE_NOT_CONFIRMED"`
字串（第14節TEST 4/16實測）——前端可依此明確呈現「目前結果尚未使用
新評價基準」，不會誤導使用者以為新基準已套用。

## 12. Existing Engines

**`GradeEngine`／`AdjustmentEngine`本輪完全未修改**（`engine/
grade_engine.py`／`engine/adjustment_engine.py`兩檔案本輪zero diff）。
Phase 3B所有provenance/editing/confirmation邏輯皆在
`CaseRulePackage`/`CaseRuleRepository`/`evaluation_standard.py`
三者之間完成，最終交給`RuleEngine`（同樣未修改）的
`regional_rules`/`individual_rules`仍是純`rule_schema.json`形狀的
dict列表，與STEP 2/3A完全一致的資料介面。

## 13. Do Not — 逐項確認

| 禁止事項 | 確認 |
|---|---|
| Gemini/Bedrock/RAG/semantic LLM | 未使用，factor mapping resolution為人工透過API明確指定`canonical_factor_id`，非AI推論 |
| frontend redesign | 未觸碰任何前端檔案 |
| 隊友資料整合 | 未讀取`docs/incoming_rule_sources/` |
| 新GradeEngine/AdjustmentEngine | 未新建，見第12節 |
| RegionalRateCalculator/Table4Calculator/Table52Calculator | 未新建 |

## 14. Tests

新增`tests/test_evaluation_standard_human_confirmation.py`（18 tests，
對應使用者要求逐項）：

| # | 測試 | 對應class/method |
|---|---|---|
| 1 | Importer result successfully saved as EXTRACTED | `TestCandidatePersistence` |
| 2 | EXTRACTED cannot affect production result | `TestUnconfirmedCannotAffectResult::test_extracted_status_...` |
| 3 | PARTIAL cannot affect production result | 同上`::test_partial_status_...` |
| 4 | AMBIGUOUS cannot affect production result | 同上`::test_ambiguous_status_...` |
| 5 | Valid EXTRACTED → CONFIRMED | `TestConfirmationFlow::test_valid_extracted_confirms_successfully` |
| 6 | Validation ERROR prevents confirmation | 同上`::test_validation_error_prevents_confirmation` |
| 7 | Human edit preserved in provenance | `TestHumanEditProvenance::test_human_edit_preserved_in_provenance` |
| 8 | Original extracted value preserved | 同上`::test_original_extracted_value_preserved` |
| 9 | Case A cannot confirm Case B package | `TestCaseIsolation` |
| 10 | Rejected package never affects result | `TestRejectAndReupload::test_rejected_package_never_affects_result` |
| 11 | Second upload after rejection works | 同上`::test_second_upload_after_rejection_works` |
| 12 | Only one confirmed package per case | `TestMultipleConfirmedPackagesForbidden` |
| 13 | Confirmed package immediately used by Analyze | `TestConfirmedPackageUsedByAllThreeHandlers::test_analyze_...` |
| 14 | Confirmed package used by CompleteForm | 同上`::test_complete_form_...` |
| 15 | Confirmed package used by Review | 同上`::test_review_...` |
| 16 | CASE_RULE_NOT_CONFIRMED warning surfaced | `TestNotConfirmedWarningSurfaced` |
| 17 | No Mock fallback | `TestNoMockFallback` |
| 18 | No Golden fallback | `TestNoGoldenFallback` |

固定情境：全部測試走**真實**PDF（`評價基準明細表範例.pdf`）＋真實
`document_upload.py`上傳流程＋真實五個新handler，非mock/簡化版路徑。
TEST 13-15使用主要道路寬度的真實EXTRACTED規則，透過
`submit_evaluation_standard_edits`對稍優級距下界做一次真實人工編輯
（20→15，與STEP 2既有Golden驗證手法一致），使18m改判為稍優，
與static基準的普通形成可獨立驗證的差異。

## 15. Regression

| 項目 | 指令 | 結果 |
|---|---|---|
| Phase 3B專項測試 | `py -m pytest -q tests/test_evaluation_standard_human_confirmation.py` | **18 passed** |
| Phase 3A + STEP 2專項測試（確認無regression） | `py -m pytest -q tests/test_evaluation_standard_importer.py tests/test_case_scoped_rule_architecture.py` | **39 passed** |
| 完整套件（排除既有環境限制的weasyprint檔案） | `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py` | **917 passed, 0 failed**（899既有＋18新增） |
| SAM Lint | `sam validate --lint`（於`infra/`） | **PASS** |
| SAM Build | `sam build --use-container`（真實container build） | **Build Succeeded**（詳見下方附註）；`ExtractEvaluationStandardFunction`／`GetEvaluationStandardCandidateFunction`／`SubmitEvaluationStandardEditsFunction`／`ConfirmEvaluationStandardFunction`／`RejectEvaluationStandardFunction`五個新function之build artifact皆已逐一確認含`evaluation_standard.py`與更新後的`case_rule_repository.py`；`EngineLayer`之`domain/models.py`已確認含`RuleFieldEdit` |

**附註（誠實記錄，非專案程式碼問題）**：本輪執行`sam build
--use-container`時，本機Docker容器內的pip安裝一度因本機Avast
防毒軟體之「Web Shield」HTTPS流量掃描（會以Avast自身的根憑證重新
簽署所有HTTPS連線，Windows主機信任該憑證但Linux build容器的CA
信任庫不包含它）而回報SSL憑證驗證失敗，與`infra/layers/engine/
Makefile`、`backend/requirements*.txt`或本輪任何程式碼變更皆無關
（同一份Makefile在STEP 2/Phase 3A的先前build已成功執行過完全相同的
`pip install`）。以`docker run curlimages/curl -k`成功、去掉`-k`
即失敗，直接證實純屬憑證信任鏈問題，非網路連線或DNS問題。透過
`sam build --use-container --container-env-var-file`暫時傳入一份
「certifi預設憑證＋Avast憑證」合併後的CA bundle（本機暫存檔，
建置完成後已刪除，**未修改**`infra/layers/engine/Makefile`或任何
專案憑證/網路設定）成功完成建置——這是本機環境的一次性疑難排解，
非專案建置流程的變更；團隊其他成員／CI環境執行標準
`sam build --use-container`指令應不受影響（除非該機器也有相同的
防毒軟體TLS攔截設定）。

## 16. Files Added / Modified

**新增**：
- `backend/handlers/evaluation_standard.py`（五個handler）
- `tests/test_evaluation_standard_human_confirmation.py`（18 tests）
- `docs/audit/EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md`（本文件）

**修改**：
- `domain/models.py`：新增`RuleFieldEdit`；`CaseRulePackage`新增
  `edit_history: List[RuleFieldEdit]`欄位。
- `backend/handlers/case_rule_repository.py`：新增
  `submit_human_edits()`／`resolve_candidate_factor_mapping()`／
  `build_review_dto()`／`CaseRulePackageNotEditableError`／
  `CaseRuleCandidateNotFoundError`／`RuleRecordNotFoundError`／
  `_EDITABLE_FIELDS`／`_find_rule_record()`；`confirm()`新增一段
  atomicity說明註解（邏輯本身未變）。
- `engine/evaluation_standard_importer.py`：`RuleCandidate`新增
  `candidate_id`欄位與`to_dict()`/`from_dict()`；新增
  `reevaluate_candidate_with_canonical_factor()`；
  `candidate_to_rule_records()`之`rule_id`格式納入`candidate_id`
  （供人工重新mapping時精確比對取代舊記錄）；
  `build_case_rule_package_from_pdf()`的`metadata['extraction_
  candidates']`改存完整candidate資料（`to_dict()`）而非摘要。
- `infra/template.yaml`：新增五個`AWS::Serverless::Function`資源。

**未修改**：`engine/rule_engine.py`／`engine/grade_engine.py`／
`engine/adjustment_engine.py`／`engine/rule_table_validator.py`／
`engine/form_completion_engine.py`／`rule_engine_factory.py`之
resolution邏輯本身／`document_upload.py`／任何GIS/S3 Bootstrap/
Cadastral相關檔案／`docs/incoming_rule_sources/`／前端。

## 17. Remaining Gaps

1. **無前端UI**：`build_review_dto()`已產出UI-ready的response
   schema，但實際審核畫面（顯示factors清單、逐欄編輯表單、
   Confirm/Reject按鈕）未實作，依使用者明確排除範圍。
2. **factor_mappings/edits的批次交易語意**：`submit_
   evaluation_standard_edits()`一次請求內可包含多筆`factor_mappings`
   與`edits`，目前依序逐筆呼叫repository方法（每筆各自一次
   `put_record`），並非單一DynamoDB transaction——若請求中途失敗
   （例如第3筆edit的rule_id不存在），前面已成功的筆數仍會保留
   （不會回滾）。这與第8節「確保application-level atomic
   semantics」的範圍不同：第8節指的是CONFIRM本身（status+rules）的
   原子性，已確認滿足；多筆edits一次請求內的all-or-nothing語意
   目前**不**保證，留待後續視實際需求評估是否需要。
3. **人工編輯`factor`欄位未重新命名rule_id**：透過`submit_human_
   edits()`把`field="factor"`改成別的字串時，該規則列的`rule_id`
   （已包含舊factor名稱）不會跟著重新產生——僅適合小幅修正
   （例如修正錯字），大幅改變factor identity建議改用
   `resolve_candidate_factor_mapping()`（會重新產生正確的
   candidate-based規則列並移除舊的）。
4. **無版本比較/diff呈現**：`edit_history`已完整記錄每筆修改，但
   沒有一個「顯示某規則列從Importer原始值到目前CONFIRMED值，中間
   經過幾次編輯」的彙總視圖，需要人工自行從`edit_history`陣列過濾
   查閱。

## 18. STEP 3C 更新附註（2026-09-10）

STEP 3C（`docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_REPORT.md`）在本報告
第6節`build_review_dto()`的既有輸出結構上，為每個factor entry新增了
`ai_candidate`欄位（present僅當該candidate曾被STEP 3C新增之
`CaseRuleRepository.propose_ai_candidates()`處理過，否則為`null`）——
向下相容擴充，本報告第2-17節所述之confirm/reject/edit流程與狀態機
邏輯**完全未變更**。STEP 3B原有18項測試於STEP 3C完成後重新執行，
**維持18 passed**，無regression。
