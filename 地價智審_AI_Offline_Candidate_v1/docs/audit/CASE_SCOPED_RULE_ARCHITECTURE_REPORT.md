# CASE_SCOPED_RULE_ARCHITECTURE_REPORT

**日期**：2026-09-10
**性質**：本階段目標為建立 CASE-SCOPED RULE ARCHITECTURE（P0 blocker修復），
延續`docs/audit/EVALUATION_STANDARD_AND_TEAMMATE_DATA_AUDIT.md`的P0結論。
**範圍限制（本輪明確排除）**：PDF Importer、AI/Bedrock/RAG、中央上限重新
數位化、`regional_rate_calculation.json`整合、新GradeEngine/
RegionalRateCalculator/Table4Calculator/Table52Calculator——本輪只解決
「同一Case如何安全使用confirmed規則、且不同Case互不污染」這個單一問題。
**保留現有中央資料**：未新建`central_factor_maximums.json`或其他中央
Registry，未更改`data/rules/central_max_range_local_mapping.json`目前
UNMAPPED狀態，未自行判定商業用地high/medium/ordinary/neighborhood對應。

---

**STEP5 更新（2026-09-11）**：本架構於 Blind Case（見
`docs/audit/BLIND_CASE_PHASE5_REPORT.md`）中被完整、真實地行使——證明
`build_rule_engine_for_case()`／`CaseRuleRepository.confirm()` 不只
在 STEP2 的合成測試中運作，在一個真正不同的評價基準（不同 grade
band、不同 adjustment matrix）下同樣正確運作，且 Golden/Blind
Case（warm runtime）互不污染，直接驗證本報告 Test 6「No Cross-case
Leak」的持續有效性。本輪**未修改**本報告所述任何模組
（`CaseRulePackage`／`CaseRuleRepository`／`rule_engine_factory`），
唯一相關但**非**本架構本身的變更是 `backend/handlers/cases.py` 修正
`land_use_type` 建案時遭靜默丟棄的 bug（見
`COMPETITION_E2E_PHASE5_REPORT.md` §1），該 bug 與本架構的 rule
resolution 邏輯無關，純屬案件 meta 儲存層級的缺口。

## 1. Before Architecture

`backend/handlers/analyze.py`／`complete_form.py`／`review.py`三個handler
各自獨立呼叫：

```python
RuleEngine(reg + ind)   # reg/ind 皆為固定讀取
                        # data/rules/regional_rules.json 與
                        # data/rules/individual_rules.json
```

三處寫死載入邏輯彼此獨立維護（`analyze.py`甚至額外用了一個
module-level `_RULE_ENGINE` cache——真實存在的warm-Lambda跨請求洩漏
風險）。沒有任何機制讓某個Case使用不同於這兩個固定檔案的規則，也沒有
任何資料模型可以表示「這個Case有一份待確認/已確認的評價基準規則」。

## 2. After Architecture

```
Human/CSV Importer（engine/rule_table_ingest.py，既有，未改動）
        ↓ 產出 rule_schema.json 形狀的 regional_rules/individual_rules
CaseRulePackage（domain/models.py，新增）— DRAFT
        ↓ CaseRuleRepository.save_candidate()
        ↓ 人工核對（Human Confirmation）
        ↓ CaseRuleRepository.confirm()  —— CONFIRMED Gate
              （RuleTableValidator.has_errors()==True 即拒絕，不改狀態）
CaseRulePackage — CONFIRMED（每個case_id最多同時1份）
        ↓
rule_engine_factory.build_rule_engine_for_case(case_id)
        ↓（scope-level replacement；無confirmed package則走STATIC_LOCAL）
RuleEngine（engine/rule_engine.py，完全未修改）
        ↓
GradeEngine / AdjustmentEngine / FormCompletionEngine / AuditEngine
（全部完全未修改）
```

三個handler現在都呼叫同一個
`from rule_engine_factory import build_rule_engine_for_case`，對同一
`case_no`必定解析出同一份規則（見第6節）。

## 3. CaseRulePackage Schema

新增於`domain/models.py`（沿用既有Pydantic風格，`model_config =
ConfigDict(extra="forbid")`）：

```python
class CaseRulePackageStatus(str, Enum):
    DRAFT = "DRAFT"
    EXTRACTED = "EXTRACTED"
    PARTIAL = "PARTIAL"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"

class CaseRulePackage(BaseModel):
    case_id: str
    package_id: str
    rule_version: str
    source_document: str
    source_sha256: Optional[str]
    source_type: str
    status: CaseRulePackageStatus = CaseRulePackageStatus.DRAFT
    regional_rules: List[Dict[str, Any]] = []
    individual_rules: List[Dict[str, Any]] = []
    created_at: datetime
    confirmed_at: Optional[datetime]
    confirmed_by: Optional[str]
    rejected_at: Optional[datetime]
    rejected_by: Optional[str]
    rejection_reason: Optional[str]
    metadata: Dict[str, Any] = {}
    warnings: List[str] = []
```

`regional_rules`/`individual_rules`**刻意重用**現有`rule_schema.json`
形狀——與`data/rules/regional_rules.json['rules']`／
`individual_rules.json['rules']`、以及`engine/rule_table_ingest.py::
build_rules_from_csv()`的輸出**完全同一種dict shape**，不是新格式。
`RuleEngine.__init__()`今天就直接吃得下。

另新增`RuleSourceType`（`STATIC_LOCAL`/`CASE_IMPORTED_CONFIRMED`）、
`CaseRuleTraceInfo`、`CaseRuleResolution`——皆為第8節Traceability機制
使用的輔助模型，全部位於`domain/models.py`檔案尾端新增區塊。

## 4. Repository Design

新增`backend/handlers/case_rule_repository.py`：

- `CaseRuleRepository`：純介面（`NotImplementedError`），定義
  `save_candidate`／`get_package`／`list_packages`／`confirm`／
  `reject`／`get_confirmed_package`。
- `DynamoCaseRuleRepository`：**唯一**實作，重用既有
  `backend/handlers/case_store.py`的PK=`CASE#<case_no>`／
  SK=`<record type>`單表設計，而非另建儲存機制：
  - `SK = "CASE_RULE_PACKAGE#<package_id>"`：一個package一筆
  - `SK = "CASE_RULE_CONFIRMED_POINTER"`：每個case最多一筆，
    `{"package_id": "..." | None}`
- 為支援「依SK前綴查詢同一case下所有package版本」，於
  `case_store.py`新增一個公開函式
  `query_records_by_sk_prefix(case_no, sk_prefix)`（Query，非Scan，
  成本仍為O(單一case的記錄數)，不影響既有任何函式）。

**未提供獨立的in-memory測試實作**：分析後認為沒有必要——一次真實
案件審查（analyze→complete_form→review）可能落在warm Lambda的不同
container上執行，in-memory store無法滿足這個跨呼叫持久性需求，因此
production唯一可行實作本來就必須是DynamoDB-backed。測試採用與
`tests/test_backend_handlers_e2e.py`對`case_store.py`完全相同的手法
——moto `mock_aws()`模擬真實DynamoDB，測試走的是**與production完全
相同的程式碼路徑**，而非可能與production行為漂移的另一套test double。

**CONFIRMED Gate**（`confirm()`內）：
1. 目標package必須存在（否則`CaseRulePackageNotFoundError`）。
2. 同一case_id若已有**另一個**package處於CONFIRMED，直接拒絕
   （`CaseRulePackageAlreadyConfirmedError`）——每個case同時最多僅
   一份CONFIRMED package，要換版本必須先`reject()`舊的。
3. 對`regional_rules + individual_rules`執行
   `engine/rule_table_validator.RuleTableValidator().validate()`，
   若`has_errors()`為真則拒絕（`CaseRulePackageInvalidError`，附帶
   原始issue列表），**不修改狀態**。
4. 通過後才寫入`status=CONFIRMED`／`confirmed_at`／`confirmed_by`，
   並更新pointer。

`reject()`會清除pointer（若目前指向被reject的package），確保
`get_confirmed_package()`絕不會回傳一個已被reject的package。

**真實bug修復（測試過程中發現）**：`case_store.put_record()`會把
浮點數轉成`Decimal`才寫入DynamoDB（`_dynamodb_safe`，避免boto3對
native float的拒絕），讀回時boto3也回傳`Decimal`。這代表一個
case-scoped規則的`adjustment_matrix`/`lower_bound`/`max_adjustment`
若原本是`float`，經過一次DynamoDB round-trip後會變成`Decimal`，
而`RuleTableValidator._check_max_adjustment()`內的`float(x) - y`
運算對`float`與`Decimal`混用會直接`TypeError`（Python允許
Decimal與float比較大小，但不允許直接算術運算）。新增
`case_rule_repository._decimal_to_native()`，在`get_package()`／
`list_packages()`讀出資料時遞迴把`Decimal`還原為`float`/`int`，確保
一份case-scoped規則不論從DynamoDB或從static JSON檔載入，數值型別
對`RuleEngine`/`RuleTableValidator`/`AdjustmentEngine`而言完全一致
——這不只是為了讓測試通過，是這個架構要成立的必要正確性保證。

## 5. Rule Resolution Order

新增`backend/handlers/rule_engine_factory.py::build_rule_engine_for_case
(case_id, repository=None) -> (RuleEngine, CaseRuleResolution)`：

1. 無`case_id` → 純static baseline，`resolution_status="STATIC_LOCAL"`。
2. 有`case_id`但`repository.list_packages(case_id)`裡沒有任何
   `status==CONFIRMED`的package → static baseline；若曾經存在過
   任何package（只是沒被確認）→ 額外附上
   `warnings=["CASE_RULE_NOT_CONFIRMED"]`（第9節）。
3. 剛好1個CONFIRMED package → **scope-level replacement**：
   - `regional_rules`：若package有提供（非空list）就整批取代static
     的regional_rules；若package沒提供（空list）則落回static的
     regional_rules。
   - `individual_rules`：邏輯相同，獨立判定。
   - **不會**在同一個factor上把case規則與static規則合併——這是
     使用者要求的「先採最安全策略」：scope-level replacement優於
     individual-factor silent merge，除非未來schema能證明安全。
   - Resolution前**再次**對該CONFIRMED package跑一次
     `RuleTableValidator`（defense-in-depth，防止confirm()之後的
     資料drift，如手動改DynamoDB item）；若失敗，`raise
     CaseRulePackageInvalidError`，**絕不**靜默退回static（第9節）。
4. 超過1個CONFIRMED package（理論上不該發生，`confirm()`本身已擋）
   → 同樣`raise CaseRulePackageInvalidError`，defense-in-depth。

## 6. Handler Wiring

`analyze.py`／`complete_form.py`／`review.py`三者的修改**完全相同的
模式**：

```python
try:
    rule_engine, rule_resolution = build_rule_engine_for_case(case_no)
except CaseRulePackageInvalidError as e:
    return error_response(409, "CASE_RULE_INVALID", f"{e}. MANUAL_REVIEW_REQUIRED")
```

三者都對**同一個`case_no`**呼叫**同一支factory函式**——不存在
「Analyze用Case Rule、CompleteForm用Static Rule」這種分裂狀態，因為
三者都是問同一個repository同一個問題（這個case目前的CONFIRMED
package是誰），答案必然一致（除非在兩次呼叫之間發生了
confirm/reject，那本來就該反映最新狀態，屬預期行為，非bug）。

移除的舊程式碼：
- `analyze.py`的module-level`_RULE_ENGINE`cache與`_load_rule_engine()`
- `complete_form.py`／`review.py`各自的`_rule_engine()`

## 7. Isolation Strategy

- **資料層隔離**：`CaseRulePackage`／pointer皆以`PK=CASE#<case_no>`
  分區，`repository.list_packages(case_id)`／`get_confirmed_package
  (case_id)`只查詢單一case的分區，結構上不可能讀到別的case的package。
- **執行層隔離**：`build_rule_engine_for_case()`**刻意不做任何
  模組層級快取**（no `_RULE_ENGINE`/`_ENGINE_CACHE`之類的全域變數）
  ——每次呼叫都重新從repository讀取、重新建構`RuleEngine`。
  `RuleEngine.__init__()`本身只是對數百筆dict做記憶體索引，成本低，
  沒有必要為效能犧牲隔離性，而舊的`_RULE_ENGINE`cache正是這次分析
  中發現的真實latent bug（若未移除，一旦warm Lambda container先服務
  Case A（confirmed override）、再服務Case B，Case B將沿用Case A的
  `RuleEngine`實例，讀到錯誤規則）。
- 已有測試（第10節Test 5/6）直接證明：Case A confirmed override後，
  同一process內先跑A兩次、再跑B，B仍正確解析為`STATIC_LOCAL`且產出
  static grade。

## 8. Traceability

**刻意不修改**`GradeEngine`／`AdjustmentEngine`／
`FormCompletionEngine`／`AuditEngine`本身（皆維持使用者「不要重寫」
的要求）。做法：

- `build_rule_engine_for_case()`回傳的`CaseRuleResolution.
  trace_by_rule_id: Dict[rule_id, CaseRuleTraceInfo]`，在合併規則
  「之前」就已經對每一筆rule_id（不論static或case-scoped）建好
  provenance記錄（`rule_source_type`／`case_id`／`package_id`／
  `source_document`／`source_sha256`／`rule_version`）。
- 三個handler在**現有**回傳結構（`grades`/`adjustments`裡的
  `rule_id`、`FormCompletionResult.fields[].rule_id`、
  `AuditResult.issues[].rule_id`——這些`rule_id`欄位本來就存在，
  完全沒有新增）之上，用`trace_by_rule_id.get(rule_id)`查出對應的
  `rule_source_type`，寫回同一筆記錄／同一份回應JSON裡。這是**純
  後處理**，不涉及引擎內部邏輯。
- `ANALYSIS`/`FORM_COMPLETION`/`REVIEW_RESULT`三個儲存記錄與對應的
  API回應，現在都額外帶有`rule_resolution_status`／`rule_package_id`
  ／`rule_resolution_warnings`（案件層級）以及每筆grade/adjustment/
  field/issue的`rule_source_type`（規則層級）。

## 9. Failure Semantics

| 情境 | resolution_status | 行為 |
|---|---|---|
| 無case_id | `STATIC_LOCAL` | 正常，非錯誤 |
| 從未建立過package | `STATIC_LOCAL` | 正常，非錯誤，`warnings=[]` |
| package存在但無一CONFIRMED | `STATIC_LOCAL` | 正常使用static，`warnings=["CASE_RULE_NOT_CONFIRMED"]`——明確告知評審「我們知道有草稿，但故意用static」，避免誤以為新基準已套用 |
| 恰好1個CONFIRMED，且通過結構驗證 | `CASE_IMPORTED_CONFIRMED`（或該package未提供任何規則時退回`STATIC_LOCAL`） | 依scope-level replacement規則套用 |
| CONFIRMED package結構驗證失敗（confirm()之後drift） | *(不回傳，直接raise)* | `build_rule_engine_for_case()`丟出`CaseRulePackageInvalidError`；三個handler一致回應HTTP 409、`error.code="CASE_RULE_INVALID"`、訊息含`MANUAL_REVIEW_REQUIRED`；**不寫入**任何ANALYSIS/FORM_COMPLETION/REVIEW_RESULT記錄（第10節Test 9/11直接驗證） |
| 超過1個CONFIRMED（不變量被打破） | *(不回傳，直接raise)* | 同上，視為`CASE_RULE_INVALID` |

## 10. Tests

新增`tests/test_case_scoped_rule_architecture.py`（19 tests，全數
通過），對應使用者要求的12項＋3項repository層級不變量測試：

| # | 測試 | 對應class |
|---|---|---|
| 1 | No Case Rule → 既有static行為不變 | `TestNoCaseRuleStaticBaselineUnchanged` |
| 2 | Confirmed Case Rule → Analyze使用Case Rule | `TestConfirmedCaseRuleAnalyze` |
| 3 | Confirmed Case Rule → CompleteForm使用同一Case Rule | `TestConfirmedCaseRuleCompleteForm` |
| 4 | Confirmed Case Rule → Review使用同一Case Rule | `TestConfirmedCaseRuleReview` |
| 5 | Case A / Case B isolation | `TestCaseIsolation` |
| 6 | Warm-process重複呼叫不洩漏（含「模組層級無RuleEngine cache」的直接斷言） | `TestWarmProcessNoLeak` |
| 7 | Unconfirmed（DRAFT/EXTRACTED/PARTIAL）不可影響結果 | `TestUnconfirmedCannotAffectResult`（parametrized） |
| 8 | Rejected不可影響結果（含confirm→reject→再查一次的完整序列） | `TestRejectedCannotAffectResult` |
| 9 | Invalid confirmed package → `CASE_RULE_INVALID`，無靜默fallback | `TestInvalidConfirmedPackage`（含confirm()本身拒絕＋resolver defense-in-depth兩種情境） |
| 10 | Golden既有行為不變（無Case Rule時） | `TestGoldenLikeFlowUnaffected` |
| 11 | No Mock fallback（無效package時不寫入任何結果記錄） | `TestNoMockFallbackOnInvalidRule` |
| 12 | No Golden fallback（不相關案件不會沿用任何快取/寫死的Golden數值） | `TestNoGoldenFallback` |
| - | Repository不變量（單一CONFIRMED、NotFound、list_packages按case隔離） | `TestRepositoryInvariants` |

固定測項（fixture factor）：`主要道路寬度`（`regional_main_road_width`）
——真實static規則中18m落在`普通`(grade_code=3)，`_case_a_road_width_
override()`把`稍優`(grade_code=2)的區間放寬到`15<=x<30`，讓同樣18m
在case override下改判為`稍優`(grade_code=2)——與使用者範例
「Case A→稍優／Case B→普通」完全對應，且此override本身通過
`RuleTableValidator`（零ERROR，僅有關於級距銜接與分級用語混用的
WARNING，皆不阻擋confirm）。

## 11. Modified Files

- `domain/models.py`：新增`RuleSourceType`／`CaseRulePackageStatus`／
  `CaseRulePackage`／`CaseRuleTraceInfo`／`CaseRuleResolution`（檔案
  尾端新增區塊，未改動任何既有class）。
- `backend/handlers/case_store.py`：新增`query_records_by_sk_prefix()`
  （純新增函式，未改動既有函式）。
- `backend/handlers/analyze.py`：移除module-level`_RULE_ENGINE`cache
  與`_load_rule_engine()`；改用`build_rule_engine_for_case()`；新增
  `CASE_RULE_INVALID`錯誤處理；為`grades`/`adjustments`附加
  `rule_source_type`；`ANALYSIS`記錄與回應新增`rule_resolution_status`
  /`rule_package_id`/`rule_resolution_warnings`。
- `backend/handlers/complete_form.py`：移除`_rule_engine()`；改用
  `build_rule_engine_for_case()`；同上錯誤處理；為`fields[]`附加
  `rule_source_type`；`FORM_COMPLETION`記錄與回應新增同上三個欄位。
- `backend/handlers/review.py`：移除`_rule_engine()`；改用
  `build_rule_engine_for_case()`；同上錯誤處理；為`issues[]`附加
  `rule_source_type`；`REVIEW_RESULT`記錄與回應新增同上三個欄位。

## 12. Added Files

- `backend/handlers/case_rule_repository.py`：`CaseRuleRepository`介面、
  `DynamoCaseRuleRepository`實作、`CaseRulePackageNotFoundError`／
  `CaseRulePackageInvalidError`／`CaseRulePackageAlreadyConfirmedError`、
  `_decimal_to_native()`、`default_case_rule_repository()`。
- `backend/handlers/rule_engine_factory.py`：
  `build_rule_engine_for_case()`及其輔助函式。
- `tests/test_case_scoped_rule_architecture.py`：19 tests（第10節）。
- `docs/audit/CASE_SCOPED_RULE_ARCHITECTURE_REPORT.md`：本文件。

**未新增**（依使用者明確指示，避免重複造第二套系統）：
`RegionalRateCalculator`／`Table4Calculator`／`Table52Calculator`／
`NewGradeEngine`／`central_factor_maximums.json`／in-memory repository
實作／PDF Importer／任何AI呼叫。

## 13. Remaining Gaps

1. **PDF/Evaluation Standard Importer尚未串接**：`CaseRulePackage`目前
   只能透過repository API（`save_candidate`）以程式方式建立——決賽
   當天要把一份新的評價基準表變成`CaseRulePackage`，仍需要人工把
   官方表格轉成`rule_schema.json`形狀（可沿用既有
   `engine/rule_table_ingest.py::build_rules_from_csv()`，本輪未新增
   任何PDF/AI輔助這一段，依使用者明確排除範圍）。
2. **尚無API endpoint**：本輪未新增
   `POST /api/cases/{id}/rule-packages`之類的HTTP介面來呼叫
   `save_candidate`/`confirm`/`reject`——目前只能透過直接呼叫Python
   `CaseRuleRepository`（如本輪測試所做）。是否需要新增API endpoint
   （及對應的人工審核前端UI）留待下一輪，依使用者「本階段不做新API」
   之明確排除。
3. **中央/地方對應仍為UNMAPPED**：`data/rules/
   central_max_range_local_mapping.json`維持原狀，未變更，此為
   前次稽核報告已載明的既有延後項目，非本輪範圍。
4. **Scope-level replacement，非factor-level merge**：目前若一個
   CONFIRMED package只想覆蓋「主要道路寬度」這一個因素、其餘regional
   因素仍想沿用static，做不到——package的`regional_rules`若非空，
   就會**整批**取代static的regional scope。這是本輪刻意選擇的
   最安全策略（使用者原文：「先採最安全策略...比individual-factor
   silent merge更安全」），若未來需要更細粒度的factor-level override，
   需要額外設計（例如package只列出「要覆蓋的因素」、resolver對其餘
   因素回填static），本輪未實作。
5. **無回滾/版本歷史UI**：`list_packages()`/`get_package()`已足以
   在程式層面看到某case的所有package版本與各自狀態，但沒有任何
   人類可讀的「版本歷史」呈現介面。

## 14. Final Gate Verification（STEP 2 驗收，2026-09-10補充）

本節記錄STEP 2 Final Gate的實際執行結果，取代先前背景執行、未能
正式回報完成的規則測試。

**發現並修復一個真實的既有測試隔離缺陷（非production code缺陷）**：
完整套件執行時，`tests/test_case_scoped_rule_architecture.py`的第一個
測試在完整套件中卡住達數十分鐘（非單獨執行——單獨執行19個測試僅需
約12秒）。以`docker`層級CPU時間量測確認為真正的deadlock/長時間網路
逾時，而非單純變慢。逐步縮小範圍後定位：`tests/
test_backend_handlers_e2e.py::TestDataProviderModeSwitch`／
`TestDataProviderModeFailsFastOnInvalidValue`會把`collect_data`模組
`importlib.reload()`成「real」模式（真實連網Provider），
`monkeypatch.setenv`teardown只還原環境變數，**不會**還原已reload的
模組物件——這正是`tests/test_collect_data_nlsc_integration.py`自己
docstring與`_clean_env` fixture明文記載、且刻意防範的同一個既有
hazard（"Restore collect_data to a known-good state for later test
modules in this process"）。本檔案是alphabetical執行順序中，該洩漏
之後**第一個**單純呼叫`collect_data.collect_data()`卻沒有自行防禦
的檔案，因此在完整套件中觸發真實網路呼叫、於此sandbox環境無路由
可達、長時間逾時。

**修復範圍**：只修改`tests/test_case_scoped_rule_architecture.py`
本身（新增`_ensure_mock_collect_data()`，在每個seed helper呼叫
`collect_data.collect_data()`之前強制reload回mock模式；新增
`_restore_data_provider_mode_for_later_modules` autouse fixture，
於teardown還原mock模式給後續檔案），**未修改**
`test_backend_handlers_e2e.py`／`collect_data.py`／任何GIS/S3/
Cadastral相關檔案。修復後以「先前卡住的完整檔案range」重跑一次
確認不再卡住（200 passed in 35.71s），再跑一次完整套件確認全綠。

**Final Gate執行結果**：

| 項目 | 指令 | 結果 |
|---|---|---|
| Case Rule專項測試（單獨執行） | `py -m pytest -q tests/test_case_scoped_rule_architecture.py` | **19 passed** |
| 完整套件（排除既有環境限制的weasyprint檔案） | `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py` | **879 passed, 0 failed, 0 error**（860既有＋19新增） |
| Golden／handler regression子集 | `py -m pytest -q tests/test_golden_case.py tests/test_form_completion_golden.py tests/test_backend_handlers_e2e.py tests/test_smart_review.py` | **140 passed** |
| SAM Lint | `sam validate --lint`（於`infra/`） | **PASS** |
| SAM Build | `sam build --use-container`（真實container build，非引用先前結果） | **Build Succeeded**（EngineLayer／ExtractionLayer／PdfFunction／13個Zip Functions，含AnalyzeFunction/CompleteFormFunction/ReviewFunction/CollectDataFunction全數建置成功） |
| Artifact檢查 | `ls .aws-sam/build/{AnalyzeFunction,CompleteFormFunction,ReviewFunction,CollectDataFunction}/` | `case_rule_repository.py`／`rule_engine_factory.py`／`case_store.py`皆存在於全部四個function的build artifact內 |
| Handler import smoke test | 於`public.ecr.aws/lambda/python:3.12`容器內，以`/var/task`=AnalyzeFunction build artifact、`/opt/python`=EngineLayer build artifact、`LAMBDA_TASK_ROOT`環境變數皆設定為真實Lambda佈局，直接`import analyze, complete_form, review, case_rule_repository, rule_engine_factory`及`from domain.models import CaseRulePackage, CaseRuleResolution, RuleSourceType` | **成功**（非僅檢查檔案存在，而是在真實Lambda runtime image內、以`runtime_paths.bootstrap()`真正會走的`LAMBDA_TASK_ROOT`分支，實際import成功） |

**未新增任何功能**：本輪Final Gate僅為驗證既有STEP 2實作與修復一個
測試隔離缺陷，未新增EvaluationStandardImporter、未改前端、未改GIS/
S3、未改隊友資料（`docs/incoming_rule_sources/`）。

## 15. STEP 3B 更新附註（2026-09-10）

STEP 3B（`docs/audit/EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_
REPORT.md`）在本報告建立的共用基礎上做了以下擴充（皆為**向下相容**
新增，本報告第4-9節所述之CONFIRMED Gate／Rule Resolution Order／
Isolation Strategy／Failure Semantics邏輯本身**未變更**）：

- `domain/models.py`的`CaseRulePackage`新增`edit_history: List[
  RuleFieldEdit]`欄位（新增的`RuleFieldEdit` model，append-only
  人工編輯稽核軌跡）。
- `backend/handlers/case_rule_repository.py`新增
  `submit_human_edits()`／`resolve_candidate_factor_mapping()`／
  `build_review_dto()`三個新方法／函式，供人工confirm前編輯規則列。
  第4節之CONFIRMED Gate（`confirm()`本身）、第11節提到的
  `validate_package_rules_scoped()`（scope分離驗證）**皆未修改**。
- `rule_engine_factory.py`之resolution邏輯（第5節Rule Resolution
  Order）**未修改**——STEP 2既有19項測試
  （`tests/test_case_scoped_rule_architecture.py`）於STEP 3B完成後
  重新執行，**維持19 passed**，無regression。
