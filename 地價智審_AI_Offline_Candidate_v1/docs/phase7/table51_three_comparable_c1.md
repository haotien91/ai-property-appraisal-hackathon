# TABLE51-THREE-COMPARABLE-C1

前置：`COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1=PASS`。本輪目標：
正式建立「表5-1 影響地價區域因素分析明細表（住宅用地）」的三比較標的
（P002-00/P003-00/P004-00 對 base=P001-00）計算鏈，完全 comparable-count-
aware（不得只適用 3 個比較標的的特例）。**未**修改 A2 已確認的 Shulin
29-factor rule pack；**未**開始 Table4 migration／Official PDF V2／
AWS deployment。

## Task 0 — Read-Only Audit（先行完成才動 production code）

```
CURRENT_TABLE51_RUNTIME_SUPPORTED=NO
CURRENT_TABLE51_SUPPORTS_THREE_COMPARABLES=PARTIAL（FormCompletionEngine.complete_form()
  本就 `for comparable_id in case.comparable_ids` 泛型迭代，但沒有任何
  handler/domain model 以「表5-1」之名整合過）
CURRENT_TABLE51_INPUT_IS_SEGMENT_SCOPED=NO（case_reconstruction.py::
  build_case_and_regional_factors() 100% 沿用單一 case-level factors_record
  形狀，analyze.py/review.py 皆只 `case_store.get_record(case_no, "FACTORS")`）
CURRENT_TABLE51_OUTPUT_IS_COMPARABLE_SCOPED=YES（FieldCompletion.field_id
  已含 `_{comparable_id}` 後綴，天生 N-comparable-aware）
CURRENT_TABLE51_USES_SHULIN_29_FACTORS=NO（無任何 handler 把 rule_profile_id
  ="shulin_residential_2026" 與 segment-scoped 多比較標的資料串接起來）
CURRENT_TABLE51_CAN_DISTINGUISH_UNKNOWN_FROM_ZERO=NO（見下方 Blocker 3）
```

**Blocker（修正前）**：
1. `case_reconstruction.py::build_case_and_regional_factors()` 對 segment
   概念零感知。
2. 全 repo grep 找不到任何名為「Table5-1／表5-1」的 domain model／handler
   （只在 docs/data 出現）。
3. `FormCompletionEngine.complete_table5_2_regional_factors()` 只處理
   `base_by_field ∩ comp_by_field`（交集）——任一 factor 若完全缺席於某一側，
   會被**靜默跳過**（無 FieldCompletion、無 warning），與其姊妹函式
   `complete_table4_individual_factors()`（已有明確的
   `missing_on_comp + missing_on_base` → `FieldStatus.UNKNOWN` 處理）不同。
4. 只有 flat 總修正數（`CalculationEngine.regional_total_adjustment()`，
   單純加總），無任何「8 類別小計」的計算或呈現。
5. 沒有 handler 把 rule_profile_id + 3 個 segment-scoped 比較標的串起來。

**Task 5 — Official XLSX 公式稽核**：直接以 openpyxl 讀取
`表5影響地價區域因素分析明細表(住宅用地).xlsx`（Shulin 官方來源目錄下）
之「表5-1區域因素明細表(住)」工作表（45列×17欄）：

- 8 類別×29 因素列一一對應 A2 rule pack 的 29 個 REG-* 因素與
  `category` 欄位（土地使用管制(1)=6項、交通運輸(2)=6項、自然條件(3)=5項、
  土地改良(4)=1項、公共建設(5)=6項、特殊設施(6)=3項、環境污染(7)=1項、
  其他影響因素(8)=1項，總計29，與 manifest.json 的
  `regional_factor_count=29` 完全吻合）。
- `B42` 儲存格內容為文字字串 `"=(1)+(2)+(3)+(4)+(5)+(6)+(7)+(8)"`
  （openpyxl `data_type='s'`，**非** `'f'`）——這是官方空白表格自己的
  文字註記，不是可執行的 Excel 公式。每個小計儲存格（G11/J11/M11/...）
  同樣是純文字 `'％'`（待填占位符），不是公式。

```
OFFICIAL_XLSX_FORMULA_AUDITED=YES
```

因此本輪的聚合邏輯（每類別小計＝該類別所有因素修正百分比之和；
影響地價區域因素總修正數＝8個類別小計之和）明確標示為
`OFFICIAL_BLANK_FORM_FORMULA`（源自官方空白表格自身的文字結構與
B42 儲存格的顯式標註），**不**宣稱為法規原文明定的公式（除非未來另行
於評價基準明細表.pdf/作業手冊找到逐字依據），亦**非**本專案自行發明。

```
OFFICIAL_XLSX_FORMULA_FABRICATED=NO
```

## Task 1 — Generic Table5-1 Domain Model

`domain/models.py` 新增：

- `Table51FactorResult`：29 因素中的一列，對「一組 base↔comparable
  lineage」——field_id/factor_name/category/base_segment_code/
  comparable_segment_code/base_raw_value/comparable_raw_value/
  base_grade/comparable_grade/adjustment_pct/rule_id/source/status/
  requires_manual_review/reason。
- `Table51CategorySubtotal`：category/category_index(1-8)/subtotal_pct
  （該類別任一因素 requires_manual_review 時強制為 `None`，絕不用部分
  加總冒充完整小計）/requires_manual_review。
- `Table51Comparison`：comparable_segment_code/comparison_index(1/2/3)/
  factor_results[]（29項）/category_subtotals[]（8項）/
  total_adjustment_pct（同樣 fail-closed 為 `None`）/status/
  requires_manual_review。
- `Table51Analysis`：case_id/base_segment_code/rule_profile_id/
  comparisons[]——**三條獨立 lineage**（P001→P002、P001→P003、
  P001→P004），從不合併或平均。

## Task 2 — Segment-Scoped Input Loading + Fixed Value Precedence 重用

`backend/handlers/table51_analysis.py::_load_segment_regional_factors()`：
對每個 segment_code 各自呼叫
`case_store.get_record(case_no, competition_segments.factors_sk(segment_code))`
（完全獨立的 DynamoDB item，P002 讀不到 P003/P004），並直接呼叫
`case_reconstruction._apply_competition_provided_precedence()`——
**B1-FINAL-GATE-1 已確認的正式 precedence path**，本輪未重新實作第二套
precedence 邏輯，純粹重用。

## Task 3 — 29 Regional Factors（沿用既有 Grade Engine）

`engine/table51_analysis_engine.py::build_regional_factor_catalog()`：
factor 目錄**完全從呼叫端已解析出的 `RuleEngine` 內部索引動態萃取**
（`extract_regional_rule_records()`，與 `AdjustmentEngine._lookup_
matched_rule()` 相同的既有存取模式），field_id 由 rule_id 前綴
（如 `REG-BUILDING_COVERAGE_RATIO`）通用轉換而來（`regional_` +
小寫化後綴）——不是寫死的 29 項清單，任何 rule_profile 的 REG-* 規則都能
套用同一套轉換規則。

`Table51AnalysisEngine.build_comparison()` 對每個因素直接呼叫既有
`GradeEngine.grade_factor()` + `AdjustmentEngine.compute_adjustment()`
（與 `FormCompletionEngine` 完全相同的兩個 public API，沒有寫第二套
grading logic）。2-grade/3-grade/5-grade/7-grade matrix 與已支援的
land_depth non-monotonic range，皆透過同一個 `RuleEngine`/
`RuleTableValidator` 生效，本輪未新增或修改任何 matrix/grading 程式碼。

若某因素在 base 或 comparable 任一側完全缺席（不是「grade 不出來」，而是
「根本沒有這筆資料」）——這是 `FormCompletionEngine.complete_table5_2_
regional_factors()` 原本會靜默跳過的情況——`Table51AnalysisEngine` 明確
產生 `status=MANUAL_REVIEW_REQUIRED` 且 `reason` 清楚指出缺少哪一方的
資料，從不 fabricate。

**修正既有 fixture 資料的真實錯誤**（非本輪新增之 bug，是 B1 輪
`segment_table3_fixtures.py` 遺留的格式問題，本輪驗證時發現）：
`regional_land_use_zone`/`regional_construction_prohibited`/
`regional_construction_restricted`/`regional_land_improvement` 四個
categorical/boolean 因素，原本存的是題目.pdf的**原始勾選文字**
（如"第一種住宅區"、"無"），但 rule pack 的 grade_label 是**分類後的
級距描述**（如"住宅區、市場用地"、"無禁止建築"、"四項以上"）。已依
`docs/phase7/shulin_rule_source_truth_gate_a1.md` 中**已由 A1 輪驗證過
來源**的分類對照（"第一種住宅區"屬"住宅區/市場用地"級距；4項打勾對應
"四項以上"）修正 fixture 資料本身，非規則庫或引擎邏輯變更。

## Task 4 — Three Independent Comparisons

`Table51AnalysisEngine.build_analysis()` 對呼叫端傳入的
`comparables: List[Tuple[segment_code, comparison_index, district,
land_use_type, factors]]` 逐一呼叫 `build_comparison()`，每次呼叫互相
獨立（無共享可變狀態），回傳三個獨立 `Table51Comparison`。**沒有**
`average(P002,P003,P004)` 或 `comparable_ids[0]` 這類 shortcut——
`table51_analysis.py` 的 handler 對 segment map 宣告的**每一個**比較標的
都要求已有 FACTORS 資料，任何一個缺席即整體 fail-closed（`400
VALIDATION_ERROR`），不會靜默只處理其中一部分。

## Task 6 — Missing / Unknown / Manual Review Safety

- Case A（29 因素皆足夠）：`tests/test_table51_three_comparable_c1.py::
  TestOfficialXlsxFormulaMapping::test_k_...`（合成完整資料集）→
  `status=COMPLETED`，`total_adjustment_pct` 為真實數字。
- Case B（P003 缺一項）：`TestMissingVsZeroAndManualReviewIsolation::
  test_f_...` → 只有 P003 的該項為 `MANUAL_REVIEW_REQUIRED`，P002/P004
  仍 `COMPLETED`。
- Case C（P002 某因素合法真值=0）：`test_e_zero_is_a_legitimate_value_
  distinct_from_missing` → `status=COMPLETED`，與同一比較中另一筆真正缺席
  的因素（`raw_value=None`）明確區分。
- Case D（value missing → 不得轉 0）：同上，`base_raw_value`/
  `comparable_raw_value` 缺席時皆為 `None`，從未預設為 `0`。
- Case E（rule 不足 → 不得 fallback Jinshan）：`table51_analysis.py`
  透過 `build_rule_engine_for_case(rule_profile_id="shulin_residential_
  2026")` 取得 RuleEngine——若無 CONFIRMED package，直接
  `409 RULE_PROFILE_NOT_READY`，從不落回 Jinshan 靜態規則（沿用
  SHULIN-COMPETITION-RULE-PACK-A2/B1 已確認的 fail-closed 閘門，未重新
  實作）。

## Task 7 — Competition Fixed Value Conflict（Table5-1 Runtime）

`test_g_base_fixed_value_precedence`／`test_h_comparable_fixed_value_
precedence`：對 base(P001) 與 comparable(P002) 各自建構
CompetitionProvided=28/X、Provider=99/55、AI補充=123/77 三方衝突，
Table5-1 實際回傳的 `base_raw_value`/`comparable_raw_value` 皆為
CompetitionProvided 值，從未被 99/55 或 123/77 覆寫。

```
TABLE51_BASE_FIXED_VALUE_PRECEDENCE=YES
TABLE51_COMPARABLE_FIXED_VALUE_PRECEDENCE=YES
```

## Task 8 — Lineage / Provenance

`Table51Analysis.case_id`/`rule_profile_id` + 每筆 `Table51FactorResult`
的 `field_id`/`category`/`base_segment_code`/`comparable_segment_code`/
`status`/`reason`（manual review 時）/`rule_id`/`source`/`base_grade`/
`comparable_grade`/`adjustment_pct`（completed 時）——完整可回答「這個
數字是哪個 segment、哪個 field、哪個來源算出來的」，從未把不同來源的值
合併成一個看不出來源的數字（COMPETITION_PROVIDED_FIXED 與 Provider
reference 分別保留在各自的儲存鍵下，見 Task 2）。

## Task 9 — Legacy Compatibility

`table51_analysis.py` 對沒有 `CompetitionSegmentMap` 的案件（每個既有
金山案件）明確回傳 `400 SEGMENT_MAP_REQUIRED`，從不嘗試把單一比較標的
硬塞進三比較標的模型，也從不要求 legacy case 建立 4 個 segment。既有
`complete_form.py`／`review.py` 單一比較標的流程完全未被本輪觸碰
（測試 `test_j_legacy_single_comparable_complete_form_still_works` 鎖定）。

```
LEGACY_JINSHAN_SINGLE_COMPARABLE_PRESERVED=YES
```

## Test Results（Task 10/11/12）

新增 `tests/test_table51_three_comparable_c1.py`（14 tests，Task 10 A-L
全覆蓋，含 Task 11 的真實 handler E2E：create_case→collect_data×4→
CONFIRMED rule package→get_table51_analysis）。

實際 `TESTS_PASSED`/`TESTS_FAILED`/`TESTS_SKIPPED`/`PYTEST_EXIT_CODE`
見本輪 FINAL REPORT。

---

# TABLE51-THREE-COMPARABLE-C1-FINAL-GATE-1 追加驗證

C1 主體完成後，正式宣告 PASS 前發現並修正兩個資料可信度缺口。

## Task 1: 恢復 Competition Source Truth（raw_value 不得被改寫）

**發現**：C1 主體為了讓 4 個 categorical/boolean 因素（使用分區/有無禁止
建築/有無限制建築/建築基地改良）能被 Grade Engine 正確分級，直接把
`segment_table3_fixtures.py` 的 `raw_value` 從題目.pdf 原始文字（如
"第一種住宅區"、"無"）**覆寫**成規則庫的級距標籤（如"住宅區、市場用地"、
"無禁止建築"）——這違反了 COMPETITION_PROVIDED_FIXED 必須逐字保留原始值
的原則。

**修正**：

1. `segment_table3_fixtures.py` 的 `raw_value` 已還原為題目.pdf 逐字原文。
2. 新增 `engine/regional_factor_value_normalization.py`：
   `normalize_regional_factor_value(field_id, raw_value) -> (evaluation_
   value, normalization_reason, mapping_source)`，映射表本身取材自
   `docs/phase7/shulin_rule_source_truth_gate_a1.md`**已驗證**之
   評價基準明細表.pdf 分級對照（使用分區的5個級距是「分組描述」而非逐一
   列舉分區名稱；有無禁止/限制建築的級距是完整用語而非二元勾選；建築基地
   改良依「勾選項目數」分級），非本模組另行發明。找不到對照時明確回傳
   `(None, None, None)`，該因素維持 `MANUAL_REVIEW_REQUIRED`，絕不猜測。
3. `engine/table51_analysis_engine.py::build_comparison()`：呼叫
   `normalize_regional_factor_value()` 取得 `evaluation_value`，**只**對
   一個「僅供 grading 使用的 FactorInput 複本」（`.model_copy(update=...)`）
   套用，原始 `FactorInput`（因此 `raw_value`）從未被修改。

**額外發現並修正的關聯 bug**：`case_reconstruction.py::_evidence_from_dict()`
原本無條件把任何不在 `_PROVIDER_SOURCE_TYPE_MAP`（只認得
"GovernmentOpenData"）內的 `source_type` 字串都預設為 `AI_ASSISTED_FILL`
——這表示 `competition_provided_factors` 明明已經存了正確的
`SourceType.COMPETITION_PROVIDED_FIXED.value`（"競賽題目提供固定值"）
字串，經過 `to_factor_inputs()` 重建後卻被**靜默改判**成
"AI輔助填寫"！這是 Task 2 provenance 測試（D/E/F/G）第一次執行時就抓到的
真實 bug，非本輪新增。修正：先嘗試 `SourceType(raw_source_type)` 直接
還原，只有在該字串本來就不是任何合法 `SourceType` 值時（例如 Provider
自己的 "GovernmentOpenData"/"Mock" 這類自由格式標籤），才 fallback 到
`_PROVIDER_SOURCE_TYPE_MAP`。

```
COMPETITION_RAW_VALUE_PRESERVED=YES
RULE_EVALUATION_VALUE_SEPARATED_FROM_RAW=YES
COMPETITION_FIXTURE_SOURCE_TRUTH_RESTORED=YES
```

測試：`tests/test_table51_c1_final_gate.py::TestRawValuePreservedFromCompetitionSourceTruth`
（A/B/C）——A/B 驗證正常化前後值分別可查詢且分級正確；C 逐一比對 4 個
segment 之 `FACTORS#P00N.competition_provided_factors` 與 fixture 完全
一致，並明確斷言 4 個高風險欄位絕不等於任何規則庫級距標籤。

## Task 2: 完整 Table5-1 輸入 Provenance

`domain/models.py::Table51FactorResult` 重新設計欄位，明確拆開三種
provenance：

- **輸入資料 provenance**（這個數字從哪裡來）：
  `base_source_type`/`base_source`/`comparable_source_type`/
  `comparable_source`。
- **正常化 provenance**（如果套用了級距映射）：
  `base_evaluation_value`/`comparable_evaluation_value`/
  `normalization_reason`/`mapping_source`。
- **規則 provenance**（哪個規則/規定判定出等級）：
  `rule_id`/`rule_source_document`/`rule_source_page`。

三者完全獨立欄位，不再合併成單一模糊的 `source` 字串。CompetitionProvided
因素可明確查得 `source_type="競賽題目提供固定值"`、`source` 含
"題目.pdf"；Provider/AI 來源的因素則保留各自的 `source_type`/`source`，
不會被 Rule Engine 的來源覆蓋。即使 `status=MANUAL_REVIEW_REQUIRED`
（例如遇到映射表沒收錄的分區名稱），輸入 provenance 依然完整保留
（`base_source_type`/`base_source` 不因分級失敗而消失）。

```
TABLE51_BASE_INPUT_PROVENANCE_COMPLETE=YES
TABLE51_COMPARABLE_INPUT_PROVENANCE_COMPLETE=YES
TABLE51_RULE_PROVENANCE_COMPLETE=YES
TABLE51_DATA_SOURCE_AND_RULE_SOURCE_SEPARATED=YES
```

測試：`tests/test_table51_c1_final_gate.py::TestCompleteInputAndRuleProvenance`
（D/E/F/G）。

## Task 3: Official Aggregation Regression

`Table51AnalysisEngine._build_category_subtotals()`/`_build_grand_total()`
邏輯未變（本輪未修改）——任一必要因素 `MANUAL_REVIEW_REQUIRED` 時，該
category 之 `subtotal_pct` 與最終 `total_adjustment_pct` 皆強制為
`None`，從未用部分加總冒充完整總修正數。`docs/phase7/
table51_three_comparable_c1.md`／`engine/table51_analysis_engine.py`
皆只使用 `OFFICIAL_BLANK_FORM_FORMULA` 字樣，未出現
`STATUTORY_FORMULA`/`LEGAL_FORMULA`（測試：
`test_documentation_never_claims_statutory_or_legal_formula`
直接掃描原始碼與文件確認）。

```
TABLE51_PARTIAL_TOTAL_FABRICATED=NO
OFFICIAL_XLSX_FORMULA_FABRICATED=NO
```

## Task 4: API Wiring Audit

`infra/template.yaml` 先前**確實未**註冊 `GET /api/cases/{id}/table5-1`
路由（`grep` 直接確認）。本輪已新增最小、安全的路由註冊
（`GetTable51AnalysisFunction`，與既有 `GetFacilityCandidatesFunction`
同一模式：`DynamoDBCrudPolicy` + 單一 GET Api Event），純 IaC 設定變更，
不影響任何既有測試或程式邏輯（YAML 語法已驗證可解析）。

```
TABLE51_HANDLER_EXISTS=YES
TABLE51_PUBLIC_API_ROUTE_REGISTERED=YES
```

測試：`tests/test_table51_c1_final_gate.py::TestApiRouteRegistration`
（確認 template.yaml 內容 + handler 確實可呼叫，非路由 smoke 假稱已存在）。

## Test Results（Final Gate）

新增 `tests/test_table51_c1_final_gate.py`（11 tests，Task 1/2/3/4 全
覆蓋）。實際 `TESTS_PASSED`/`TESTS_FAILED`/`TESTS_SKIPPED`/
`PYTEST_EXIT_CODE` 見本輪 FINAL REPORT。
