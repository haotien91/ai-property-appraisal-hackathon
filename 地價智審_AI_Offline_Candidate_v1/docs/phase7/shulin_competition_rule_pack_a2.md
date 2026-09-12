# SHULIN-COMPETITION-RULE-PACK-A2

本輪允許 production code 修改，但範圍嚴格限定於：A. 建立正式樹林住宅
Competition Rule Pack；B. 解決 land_depth 非單調區間；C. 樹林 Competition
Profile 禁止 silent fallback 到 Jinshan rules。**未**進行 Table3 migration、
Table5-1/Table4 renderer、Official PDF V2、Frontend migration、Final E2E。

## Rule Pack Location

```
data/rules/competition/shulin_residential_2026/regional_rules.json    (139 rule records / 29 factors)
data/rules/competition/shulin_residential_2026/individual_rules.json  (84 rule records / 19 standard factors)
data/rules/competition/shulin_residential_2026/manifest.json
```

生成腳本：`scripts/build_shulin_competition_rule_pack.py`（可重覆執行，
非手工編輯 JSON）。**未修改** `data/rules/regional_rules.json`／
`individual_rules.json`（仍為 legacy Jinshan 商業規則，原樣保留）。

## Source SHA

```
評價基準明細表.pdf SHA256 = a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c
```

與 SHULIN-RULE-SOURCE-TRUTH-GATE-A1 記錄之值完全一致，並鎖定於
`manifest.json.source_sha256`／`COMPETITION_RULE_PROFILE_REGISTRY`
（`backend/handlers/competition_rule_profiles.py`）兩處。

## 29 Regional + 19 Individual（+ FAR 特例）

```
REGIONAL_RULE_FACTOR_COUNT=29（139 rule records，含唯一一個7級因素「其他影響因素」）
INDIVIDUAL_RULE_FACTOR_COUNT=19（84 rule records，容積率個別因素刻意不建立rule record）
```

所有 grade/threshold/adjustment_matrix 數值皆逐格對照
`docs/phase7/shulin_rule_source_truth_gate_a1.md`（上一輪視覺核對結果）
硬編寫入生成腳本，**未**從 `data/rules/regional_rules.json`／
`individual_rules.json` 複製任何門檻數字。

## FAR Policy

`floor_area_ratio_individual`（個別因素容積率）**沒有**對應 rule record：

```json
{
  "factor": "floor_area_ratio_individual",
  "calculation_policy": "MANUAL_REVIEW_REQUIRED",
  "reason": "LAND_DEVELOPMENT_ANALYSIS_REQUIRED",
  "source_page": "p.8"
}
```

（見 `manifest.json.special_policies`）。任何嘗試以 `rule_set="individual"`
對 factor="容積率" 呼叫 `RuleEngine.grade()` 會得到真實的 `RuleNotFoundError`
——這是刻意、正確的行為（Task 16），已由
`tests/test_shulin_competition_rule_pack.py::TestNonMonotonicLandDepth::
test_floor_area_ratio_individual_raises_never_silently_graded` 鎖定。

## Non-Monotonic Design（land_depth）

`engine/rule_engine.py::RuleEngine._match_numeric()` 新增可選欄位
`range_segments`（一個 rule record 可攜帶多個互斥數值區段），對**沒有**
此欄位的既有規則（全部 Jinshan 規則 + 本包 84 個個別因素規則中的 82 個）
完全透明、零行為變化——僅 `IND-LAND_DEPTH-03`（普通）與 `IND-LAND_DEPTH-05`
（劣）兩筆規則使用：

```json
"range_segments": [
  {"lower_bound": 7, "lower_inclusive": true, "upper_bound": 14, "upper_inclusive": false},
  {"lower_bound": 40, "lower_inclusive": true, "upper_bound": 50, "upper_inclusive": false}
]
```

`engine/rule_table_validator.py` 同步更新：
- `_check_bound_contiguity` 由「依 grade_code 排序」改為「依實際數值排序」
  （對既有單調因素結果完全相同，僅對非單調因素才有差異），並支援展開
  `range_segments`。
- 新增 `_check_range_segments`：偵測 unreachable range（lower≥upper）、
  完全重複區段、genuine數值重疊（相鄰觸界不算重疊）。

```
NON_MONOTONIC_RANGE_SCHEMA_SUPPORTED=YES（range_segments，向下相容）
NON_MONOTONIC_RANGE_RUNTIME_VERIFIED=YES（Task 9 全部12組數值-等級組合實際跑過 RuleEngine，逐一OK）
```

## Fail-Closed Behavior

`backend/handlers/competition_rule_profiles.py`（新檔案，避免
`rule_engine_factory.py`↔`case_rule_repository.py` 循環匯入）：

```python
COMPETITION_RULE_PROFILE_REGISTRY = {
    "shulin_residential_2026": {
        "district": "樹林區", "land_use_type": "普通住宅用地",
        "source_document": "評價基準明細表.pdf",
        "source_sha256": "a7574aaf...",
    },
}
```

`backend/handlers/rule_engine_factory.py::build_rule_engine_for_case()`
新增**可選**參數 `rule_profile_id: Optional[str] = None`——**每個既有呼叫端
（analyze.py/complete_form.py/review.py）完全不變**（未傳入此參數，行為與
本輪之前逐字相同）。當顯式傳入已知 competition profile id 時：

- 無任何 package、或無 CONFIRMED package → `raise RuleProfileNotReadyError`
  （不再 return STATIC_LOCAL）
- 有 CONFIRMED package 但 `regional_rules`／`individual_rules` 任一為空
  （partial）→ 同樣 raise（Task 12：競賽 profile 不允許逐 scope 局部
  fallback 到 Jinshan）
- CONFIRMED package 之 `metadata.competition_profile` 與
  `COMPETITION_RULE_PROFILE_REGISTRY` 不符 → 同樣 raise（defense in depth；
  `case_rule_repository.py::confirm()` 本身也已在 CONFIRM 當下擋下這種
  package，見下）
- 傳入未知 profile_id（拼字錯誤等）→ 同樣 raise（不得誤判為"沒有profile"
  而悄悄變成static baseline）

`backend/handlers/case_rule_repository.py::confirm()` 新增：若 package 的
`metadata["competition_profile"]` 存在（**選擇性欄位**，Legacy Jinshan
package 從未設定，完全不受影響），必須與 `COMPETITION_RULE_PROFILE_REGISTRY`
完全一致才能 CONFIRMED，否則 `raise CaseRulePackageInvalidError`。

```
COMPETITION_FAIL_CLOSED_ENABLED=YES
LEGACY_JINSHAN_STATIC_FALLBACK_PRESERVED=YES（rule_profile_id未傳入時，逐字相同的既有邏輯路徑）
```

## Legacy Compatibility

- `data/rules/regional_rules.json`／`individual_rules.json`：位元組級未變動。
- `engine/rule_engine.py`／`rule_table_validator.py` 之修改皆為「新增可選
  行為」（range_segments 不存在時走原路徑）——以 Jinshan 規則重新驗證
  （`tests/test_case_scoped_rule_architecture.py` 19項、
  `tests/test_rule_engine_core.py`／`test_rule_table_ingest.py`／
  `test_teammate_rule_data_integration.py`）全數維持通過，0 ERROR。
- 唯一需要調整的既有測試：`tests/test_case_scoped_rule_architecture.py`
  的 `_case_a_road_width_override()` fixture 原本用 15/15 建構一個
  grade_code=3 的空區間（lower==upper）——這在新的
  `_check_range_segments`（unreachable range 檢查）下正確地被判定為
  ERROR。已修正 fixture 邊界為 18/18（維持測試原意：18m判定為稍優）並保持
  結構有效（見該檔案該函式最新docstring），非放寬新驗證邏輯。

## Ambiguous Subtype Policy

記錄於 `manifest.json.ambiguous_subtype_scope`：

- 納骨塔（殯葬設施子類）／污水處理場（廢棄物處理設施子類）：
  `RULE_SCOPE_AMBIGUOUS`——評價基準明細表原文明確列舉部分子項但缺一項，
  Table3表單結構卻並列提供該欄位。不得自動歸入對應 factor 產生修正率，
  只能 candidate/evidence collection，final grading 為
  `MANUAL_REVIEW_REQUIRED`。
- 高鐵站/火車站/客運站/捷運站（大型車站子類）：
  `INCLUDED_BY_PARENT_RULE_AND_FORM_STRUCTURE`——regional rule原文僅用
  「大型車站」整體類別名稱（非枚舉式），涵蓋全部4個子項，不影響 parent
  rule evaluation。

```
FUNERAL_COLUMBARIUM_POLICY=RULE_SCOPE_AMBIGUOUS（納骨塔）
WASTE_WASTEWATER_POLICY=RULE_SCOPE_AMBIGUOUS（污水處理場）
```

## Test Results

```
tests/test_shulin_competition_rule_pack.py         30 passed（新檔案：Task 9/17/18全部場景）
tests/test_case_scoped_rule_architecture.py        19 passed（既有，fixture已修正，行為未變）
tests/test_rule_engine_core.py                      + tests/test_rule_table_ingest.py
  + tests/test_teammate_rule_data_integration.py    全數維持通過
Full regression (tests/ --ignore=test_phase5_golden_pipeline.py):
  見本文件 Final Report 段落之 TESTS_PASSED/FAILED/SKIPPED
```

已知 Windows WeasyPrint 3 項失敗（`tests/test_pdf_output_runtime_
verification.py::TestPdfHandlerActuallyInvoked`）與本輪修改前基準完全相同
（libgobject-2.0-0 原生函式庫問題，與本輪Rule Engine/Validator/
Rule Pack/Factory變更完全無關），照實列為 PRE_EXISTING_FAILURE，
未新增任何 skip 掩蓋。

---

# SHULIN-COMPETITION-RULE-PACK-A2-FINAL-GATE-1 追加驗證

本節記錄 FINAL-GATE-1 這一輪新增的驗證與修正（僅做 FINAL VERIFICATION，
未開始 Phase B / Table3 / Table5-1 / Table4 renderer / Official PDF V2 /
Frontend migration / Final E2E）。

## Production Wiring Proof（Task 1）

發現：`rule_profile_id` 在 A2 當時只加到了
`rule_engine_factory.py::build_rule_engine_for_case()` 這個函式本身的
**參數**上——`cases.py`／`analyze.py`／`complete_form.py`／`review.py`
完全沒有讀取、保存、或轉發它。這代表一個真的透過 API 建立的樹林案件，
會**靜默**解析為 `STATIC_LOCAL`（金山規則），沒有任何錯誤——與 Task 11
的要求直接矛盾。本輪已修正：

- `backend/handlers/cases.py::create_case()`：新增可選 body 欄位
  `rule_profile_id`（對照 `COMPETITION_RULE_PROFILE_REGISTRY` 驗證；未知
  值 → 400；省略時對既有呼叫端完全零影響），寫入 `meta["rule_profile_id"]`。
- `case_store.put_case_meta`／`get_case_meta` 對此欄位無需任何 schema
  修改（本就是自由格式 dict，直接透明往返）。
- `backend/handlers/analyze.py`／`complete_form.py`／`review.py`：三者皆
  改為 `build_rule_engine_for_case(case_no, rule_profile_id=meta.get(
  "rule_profile_id"))`，並新增 `except RuleProfileNotReadyError` →
  `409 RULE_PROFILE_NOT_READY`（先前完全沒有這個 except，該例外會直接
  變成未攔截的例外拋出）。

驗證：`tests/test_shulin_a2_final_gate.py::TestProductionRuleProfileWiring`
（7 tests）——經由**真實** `cases.create_case` → `case_store` 重新讀取 →
`analyze.analyze`／`complete_form.complete_form`／`review.review`，證明
rule_profile_id 從建立案件到三個 pipeline 阶段全程透傳，而非僅測試
factory 函式本身。

## Real-Handler Fail-Closed E2E（Task 2）與 Partial-Package（Task 3）

`tests/test_shulin_a2_final_gate.py::TestRealHandlerFailClosedE2E`（情境
A-D）與 `TestPartialPackageRuntimeSafetyRealHandler`（regional-only /
individual-only）——**全部三個 handler**（Analyze / Complete Form /
Review）各自獨立驗證，不再只測 Analyze：

```
SHULIN_PARTIAL_PACKAGE_ANALYZE_FAIL_CLOSED=YES
SHULIN_PARTIAL_PACKAGE_COMPLETE_FORM_FAIL_CLOSED=YES
SHULIN_PARTIAL_PACKAGE_REVIEW_FAIL_CLOSED=YES
```

（`review.review()` 在 `submission_source=FORM_COMPLETION`——預設值——下有
自己更早的一道 precondition：沒有 FORM_COMPLETION 紀錄就先回 400，與
rule_profile_id 完全無關；測試中對這個不相關的 precondition 用一個最小
stub 紀錄繞過，才能真正驗證 review.py 自己是否有把 rule_profile_id 轉發
出去，而不是被更早的 400 意外掩蓋了 409 的驗證意圖。）

情境 D（CONFIRMED 完整 package）額外驗證：把 建蔽率=55% 同時餵給 Analyze /
Complete Form / Review——金山 static 規則下 55% 落在 grade2（稍優），但
樹林規則下落在 grade4（稍劣）——三個 handler 的真實回傳結果都是「稍劣」，
證明真的是樹林自己的門檻在生效，不是恰好兩邊都一樣的巧合。

## 發現並修正的 Python Duplicate Module Identity 問題（Task 2 附加項）

**這是本輪最重大的發現**：`engine/form_completion_engine.py` 與
`engine/extraction_to_submitted_form.py` 用 `from engine.grade_engine
import ...`（qualified）風格匯入其他 engine 子模組，但**所有真實
production handler**（`complete_form.py`／`review.py`／`analyze.py`／
`document_confirm.py`／`document_extract.py`）都用 `from grade_engine
import ...`（bare）風格建構這些引擎的實例——因為 `engine/` 目錄本身就在
`sys.path` 上（見 `runtime_paths.bootstrap()`）。

Python 對同一份原始碼、兩種不同 import 路徑，會建立**兩個完全不同的
module 物件**，因此 `grade_engine.GradeEngineError` 與
`engine.grade_engine.GradeEngineError` 是**兩個不同的 class**，即使名字
和原始碼一模一樣。實際後果：`FormCompletionEngine` 內
`except (GradeEngineError, AdjustmentEngineError)` 用的是 qualified 版本
的 class，但真正拋出的例外（來自用 bare import 建構的 `GradeEngine`
實例）是 bare 版本的 class——`except` **永遠對不上**，導致任何真的缺少
規則的個別／區域因素（例如樹林的 `floor_area_ratio_individual`，本就刻意
沒有 rule record）都會讓 `complete_form.py`／`review.py` 的 Lambda handler
以**未攔截例外**崩潰，而不是優雅降級為 `FieldStatus.MANUAL_REVIEW_
REQUIRED`——這正是本輪 FAR real-handler 測試第一次執行時實際撞到的崩潰
（見下方 Task 4 段落），不是假設性風險。

修正（全部改為 bare import，比對 production 實際運行方式）：
- `engine/form_completion_engine.py`：`grade_engine`／`adjustment_engine`／
  `calculation_engine`／`comparable_selection_engine`。
- `engine/extraction_to_submitted_form.py`：`audit_engine`／
  `human_confirmation`。
- 對應測試檔案（`tests/test_form_completion_comparable_selection_wiring.py`、
  `tests/test_document_extraction_e2e.py`）原本用 qualified 風格
  獨立建構同一批引擎實例，同步改為 bare，恢復整個流程共用單一 module
  identity。

新增 `tests/test_duplicate_engine_module_identity.py`（3 tests）：
1. 真實載入 `complete_form.py`／`review.py`／`analyze.py`
   （production handler 的完整 import chain）後，掃描 `sys.modules`，
   確認同一模組不會同時以 bare 與 `engine.` qualified 兩種身分存在。
2. 直接斷言 `form_completion_engine.GradeEngineError is grade_engine.
   GradeEngineError`（class identity 相等）。
3. 純 Jinshan static 規則（不涉及任何樹林資料）下，一個真的無規則可比對
   的個別因素，透過 `FormCompletionEngine.complete_table4_individual_
   factors()` 必須優雅降級為 `MANUAL_REVIEW_REQUIRED`，而非拋出未攔截
   例外——證明修正對 Shulin 以外的既有案例同樣成立。

完整掃描 `backend/handlers/*.py` + `engine/*.py` + `providers/*.py`（排除
`scripts/`：那裡的兩個腳本各自獨立執行，從未與任何 handler 在同一 process
內共同載入，不構成 runtime 風險），確認掃描後**沒有**任何 production
模組同時存在 bare 與 qualified 兩種身分：

```
DUPLICATE_ENGINE_MODULE_IDENTITY_PRESENT=NO
```

## FAR Runtime Proof（Task 4，更新）

`tests/test_shulin_a2_final_gate.py::TestFarRuntimeSafetyRealHandler`——
透過真實 CONFIRMED 樹林 package + 真實 `complete_form.complete_form()`
（不是 FormCompletionEngine 的獨立單元測試）餵入
`individual_floor_area_ratio`：

- 第一次執行時，因為上述 duplicate module identity 問題，實際上是
  `GradeEngineError` **未被攔截、整個 handler 崩潰**——不是優雅的
  `RuleNotFoundError`，是更嚴重的未攔截例外。這是本輪修正 duplicate
  module identity 問題的直接動機，而非事後才想到的邊角案例。
- 修正後：回傳 200，該欄位 `status=MANUAL_REVIEW_REQUIRED`、
  `final_value=None`、`adjustment=None`，警告訊息非空但不含
  "regional" 字樣（從未靜默改用區域容積率），也沒有任何 adjustments
  entry 宣稱算出了這個欄位的真實差異率。

```
INDIVIDUAL_FAR_REAL_RUNTIME_VERIFIED=YES
INDIVIDUAL_FAR_REGIONAL_FALLBACK_OCCURRED=NO
INDIVIDUAL_FAR_FAKE_ZERO_OCCURRED=NO
```

## Ambiguous Subtype Safety（Task 5，程式碼稽核確認）

`tests/test_shulin_a2_final_gate.py::TestAmbiguousSubtypeRuntimeSafety`：

- 納骨塔（columbarium）：`facility_confirmation_repository.py` 的
  `FUNERAL_SUBTYPES` 確實包含它（candidate 收集 + human CONFIRM/REJECT
  閘門真實存在），但 `facility_confirmation.py` 自己的 docstring 明確
  記載其唯一消費者是 `pdf_handler.py`（Table1 顯示用途）；`collect_data.py`
  ——唯一會產生 `regional_base_factors`／`regional_comparable_factors`
  （GradeEngine 唯一會評分的資料）的地方——完全沒有引用 columbarium 或
  facility_confirmation 任何機制。也就是說，一個已 CONFIRMED 的納骨塔
  candidate，**在架構上就不可能**自動變成殯葬設施的區域修正率。
- 污水處理場（wastewater_facility）：`providers/public_facility_
  provider.py` 確實收集距離證據（服務於「接近服務性設施」因素），但
  `facility_confirmation_repository.py` 的 `ALL_SUBTYPES` 完全不包含它
  ——該模組自己的 docstring 明講「Waste...deliberately NOT included...
  waste was never wired to the official PDF at all」。所以污水處理場
  連候選確認閘門本身都還沒实作，誠實回報為尚未實作該工作流程，而非
  與納骨塔同一種「有閘門但不自動評分」狀態。

```
COLUMBARIUM_AUTO_GRADED=NO
WASTEWATER_AUTO_GRADED=NO
```

## Test Modification Integrity Audit（Task 6）

| 檔案 | 原測試目的 | 為何新版 Validator/import 修正使 fixture 失效 | 修改內容 | assertion 減少？ | assertion 放寬？ | 語意是否改變 |
|---|---|---|---|---|---|---|
| `tests/test_case_scoped_rule_architecture.py::_case_a_road_width_override()` | 驗證 human-edited 道路寬度規則能覆寫 static 規則並被案件正確使用 | 原 fixture 只改 grade_code=2 的 lower_bound（20→15），使 grade_code=3（[15,20)）與新 grade2（[15,30)）在 [15,20) 區間**真實重疊**——新版 `_check_range_segments()`（Task 8）正確判定為 ERROR | 邊界值從 15 改為 18（grade2 lower_bound 與 grade3 upper_bound 皆為 18），保持 18m 仍判為稍優 | 否 | 否 | 否——只是把一組本來就有重疊 bug 的假資料換成一組真正合法、語意相同（18m 稍優）的假資料 |
| `tests/test_evaluation_standard_human_confirmation.py::_widen_road_width_via_human_edit()` | 驗證透過真實 Importer 抽取的規則，human edit 後 18m 從普通變稍優 | 同上：原本只送一筆 edit（grade2.lower_bound: 20→15），使 grade2=[15,30) 與 grade3=[15,20) 重疊 | 改為送兩筆 edit（grade2.lower_bound→18、grade3.upper_bound→18），並同步更新兩個既有測試的期望值（15→18） | 否 | 否 | 否——測試驗證的行為（18m 稍優、原始值/edit 歷程可追溯）完全不變，只是修正後的規則資料不再重疊 |
| `tests/test_form_completion_comparable_selection_wiring.py`（imports） | 驗證 FormCompletionEngine 的 constructor 預設會建立 ComparableSelectionEngine | qualified import 造成的 class identity 不一致（見上方 duplicate module identity 段落），與此測試的驗證目的無關 | 4 個 import 從 `engine.xxx` 改為 `xxx`（bare），與 production 實際載入方式一致 | 否 | 否 | 否——只是改變測試建構實例的 import 路徑，斷言內容完全未變 |
| `tests/test_document_extraction_e2e.py`（imports） | 驗證 PDF 抽取 → SubmittedFormData → AuditEngine.review() 全流程 | 同上，qualified import 造成的 class identity 風險 | 4 個 import 從 `engine.xxx` 改為 `xxx`（bare） | 否 | 否 | 否 |

```
TEST_ASSERTIONS_REMOVED=NO
TEST_ASSERTIONS_WEAKENED=NO
TEST_FIXTURE_SEMANTICS_PRESERVED=YES
```

## Rule Pack Integrity 重新驗證（Task 7）

```
REGIONAL_RULE_FACTOR_COUNT=29
INDIVIDUAL_STANDARD_RULE_FACTOR_COUNT=19
INDIVIDUAL_SPECIAL_RULE_FACTOR_COUNT=1（floor_area_ratio_individual，MANUAL_REVIEW_REQUIRED 政策，非假規則）
SOURCE_SHA256=a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c
```

來源檔案為 `data/sources/competition/shulin_residential_2026/評價基準明細表.pdf`
（讀取即時計算 SHA256 與上述完全相符；注意與 `data/sources/competition/`
根目錄下的範例檔 `評價基準明細表範例.pdf` 是不同檔案，不可混淆）。

`data/rules/regional_rules.json`／`individual_rules.json`（Jinshan legacy
static rules）本輪未做任何修改（僅供讀取比對）：

```
LEGACY_STATIC_RULE_FILES_MODIFIED=NO
```

## Full Regression（Task 8/9，真實 pytest exit code）

先嘗試不排除任何檔案：`python -m pytest tests -q`——在**collection 階段**
即因 `tests/test_phase5_golden_pipeline.py` 頂層 `import weasyprint` 觸發
`OSError: cannot load library 'libgobject-2.0-0'` 而整個中斷
（`Interrupted: 1 error during collection`，exit code 2，**0 個測試被執行**）
——這與 `tests/test_pdf_output_runtime_verification.py` 內 3 個測試（該檔
本身可以被 collect，只是個別測試執行時才觸發同一原生函式庫問題）性質不同：
前者若不排除，會讓整個 test session 完全無法執行，而非只是那個檔案本身
失敗。因此排除該檔案，並誠實記錄：

```
FULL_SUITE_EXCLUSIONS=tests/test_phase5_golden_pipeline.py（原因：模組層級
  import weasyprint 在此 Windows 環境缺少 libgobject-2.0-0，會讓整個
  pytest session 於 collection 階段中斷，而非僅該檔案本身失敗；與本輪
  A2 變更無關，純屬環境限制）
```

實際指令與真實 exit code（未透過 `| tail` 取得殼層的 exit code）：

```
python -m pytest tests -q --ignore=tests/test_phase5_golden_pipeline.py > full_regression.log 2>&1
echo "PYTEST_EXIT_CODE=$?" >> full_regression.log
```

結果：

```
3 failed, 1145 passed, 3 skipped in 230.00s (0:03:50)
PYTEST_EXIT_CODE=1
```

3 個失敗，逐一列出（Windows WeasyPrint/libgobject 已知問題，與本輪任何
production 變更無因果關係——這 3 個測試在本輪修改前的基準運行中即已
失敗，數量與名稱皆未變）：

```
tests/test_pdf_output_runtime_verification.py::TestPdfHandlerActuallyInvoked::test_get_pdf_returns_forms_contract_and_real_bytes
tests/test_pdf_output_runtime_verification.py::TestPdfHandlerActuallyInvoked::test_pdf_failure_mode_is_safe_no_fabricated_artifact
tests/test_pdf_output_runtime_verification.py::TestPdfHandlerActuallyInvoked::test_pdf_failure_mode_stale_form_completion_shape_is_safe
```

```
PRE_EXISTING_FAILURE_COUNT=3
NEW_FAILURE_COUNT=0
```

（1145 passed = 本輪修改前的既有基準 1126 passed + 本輪新增的 19 個測試
——`tests/test_shulin_a2_final_gate.py` 16 個 + `tests/test_duplicate_
engine_module_identity.py` 3 個——完全吻合，無任何既有測試被靜默跳過或
移除。）
