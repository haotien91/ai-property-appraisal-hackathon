# TABLE4-THREE-COMPARABLE-D1

前置：`TABLE51_THREE_COMPARABLE_C1=PASS`。本輪目標：正式建立「表4 比較法
調查估價表」的 P002/P003/P004 三比較標的獨立計算鏈，完全重用既有 Grade/
Adjustment/Calculation/ComparableSelection Engine（無第二套 grading 或
calculation 邏輯），Table5-1 區域因素修正一律從 C1 已建立之 Table51Analysis
橋接，不再重算一次。**未**修改 A2 已確認之 Shulin rule pack；**未**開始
Official PDF V2／AWS deployment／前端修改。

## Task 0 — Read-Only Audit

```
CURRENT_TABLE4_RUNTIME_SUPPORTED=PARTIAL（FormCompletionEngine.complete_form()
  既有 N-comparable 泛型迴圈可用，但無 segment-scoped 輸入、無 Table5-1 橋接、
  無 provenance 分離）
CURRENT_TABLE4_SUPPORTS_THREE_COMPARABLES=YES（既有 complete_form()/
  complete_comparable_price()/complete_comparable_weight_fields() 皆
  `for comparable_id in case.comparable_ids` 泛型迭代）
CURRENT_TABLE4_INPUT_IS_SEGMENT_SCOPED=NO（case_reconstruction.py 仍為單一
  case-level factors_record 形狀）
CURRENT_TABLE4_REGIONAL_ADJUSTMENT_FROM_TABLE51=NO（原本各自獨立重跑29因素
  grading，未讀取 table51_analysis.py 已算好的結果）
CURRENT_TABLE4_INDIVIDUAL_RULE_PROFILE_SHULIN=NO（尚無 handler 串接
  rule_profile_id=shulin_residential_2026 + segment-scoped 個別因素）
CURRENT_TABLE4_PRIMARY_COMPARABLE_SHORTCUT_PRESENT=NO
CURRENT_TABLE4_FAR_SPECIAL_RULE_SUPPORTED=YES（A2 已建立，無 rule record，
  RuleNotFoundError→MANUAL_REVIEW_REQUIRED，既有 FormCompletionEngine 已
  正確處理）
CURRENT_TABLE4_MISSING_DISTINGUISHED_FROM_ZERO=YES
CURRENT_TABLE4_PROVENANCE_COMPLETE=NO（FieldCompletion 扁平單一 source，
  未拆分 data/rule/calculation provenance）
```

`engine/calculation_engine.py`／`engine/comparable_selection_engine.py`
已逐項稽核：全部來源誠實（`土地徵收補償市價查估作業手冊.pdf p.52-53`／
`查估書表範本.pdf 表4/表5-2`），`base_parcel_comparison_price()` 已強制
呼叫端提供合計=100%權重（否則 raise），`suggest_weights()` 已明確標示
「§27無公式，僅為具名慣例」且 `requires_human_confirmation` 恆為
`True`——**兩個 Engine 皆無需修改**，本輪直接重用。

## Task 1 — Official Table4 XLSX Layout Audit

Sheet：`表4比較法調查估價表`（`表4比較法調查估價表(2).xlsx`），A1:S37。
`data_type='f'` 全表掃描 **0** 個真正 Excel formula（與 Table5-1 空白表
同樣模式，只有純文字標籤與「M」/空白 placeholder）。

- P001（比準地）：D:F 條件欄，無差異率欄。
- P002：條件G:I／差異率J。P003：條件K:M／差異率N。P004：條件O:Q／差異率R。
- 0基本資料：A5 土地正常單價、A6 交易日期＋C6 調整百分率、A7 調整至估價
  基準日單價(元/M2)、A8 地價區段號＋C8 區域因素調整百分率（Table5-1 橋接
  銜接點）。
- 個別因素調整（A9:A29，20列）：1宗地條件(6：面積/寬度/深度/形狀/臨街
  情形/地勢)、2道路條件(2：道路種類/面前道路寬度)、3接近條件(5：學校/
  市場/公園廣場/車站/商圈)、4周邊環境條件(2：嫌惡設施/停車方便性)、
  5行政條件(4：使用分區或編定/建蔽率/容積率/有無禁限建)、6其他(1，
  通用catch-all)。19個編號項目(7-25)+1其他=20，逐項比對
  `schemas/field_dictionary.json` 既有 `individual_*` 命名全部吻合。
- 決定地價：B30調整百分率絕對值加總、B31試算價格、D31比較標的權重、
  B32比準地比較價格。
- 全部數值儲存格於空白表單中**皆留白**。

```
OFFICIAL_TABLE4_XLSX_AUDITED=YES
OFFICIAL_TABLE4_LAYOUT_MAPPING_COMPLETE=YES
```

## Task 2 — 題目.pdf Table4 Fixed Values（發現並修正一個真實資料錯誤）

視覺重新核對 題目.pdf p.6（估價基準日1110901，案號1110901-99-XXX），發現
COMPETITION-DOMAIN-MULTI-SEGMENT-B1 輪建立 `segment_table3_fixtures.py`
時 `parcel_ids`（比準地/比較標的地號）記錯——表4頁面把地號與 P00N-00
代碼放在同一欄，是最不會混淆的來源，據此**修正**：

| segment_code | 正確地號 | B1輪原值（已修正） |
|---|---|---|
| P001-00（宗地流水號0003） | 新北市樹林區樹德段1415地號 | ~~文林段317地號~~ |
| P002-00（實例編號1） | 新北市樹林區樹德段284地號 | （本來就對）|
| P003-00（實例編號2） | 新北市樹林區太平段367、917地號 | ~~樹德段1415地號~~ |
| P004-00（實例編號3） | 新北市樹林區文林段317地號 | ~~太平段367、917地號~~ |

（該欄位本就標註為「純描述性 metadata，非計算輸入」，故此前錯誤未曾影響
任何 grading/計算；已確認無既有測試 hardcode 舊值。）

交易資料（新增 `data/competition_cases/shulin_residential_2026/
segment_table4_fixtures.py`）：

| | P002-00 | P003-00 | P004-00 |
|---|---|---|---|
| 交易日期 | 110年9月14日 | 111年1月11日 | 110年10月29日 |
| 土地正常單價 | 130,167 | 135,275 | 170,909 |
| 調整百分率 | 5.96% | 4.09% | 5.49% |
| 調整至估價基準日單價 | 137,925 | 140,808 | 180,292 |

```
P002_TABLE4_FIXED_MAPPING_VERIFIED=YES
P003_TABLE4_FIXED_MAPPING_VERIFIED=YES
P004_TABLE4_FIXED_MAPPING_VERIFIED=YES
BLANK_XLSX_USED_AS_CASE_VALUE_SOURCE=NO
```

## Task 3 — Generic Table4 Domain Model

`domain/models.py` 新增 `Table4FactorResult`／`Table4WeightStatus`／
`Table4Comparison`／`Table4Analysis`——與 Table51 系列同一 provenance
設計原則：raw_value 永不被 normalization 覆寫（分離出 evaluation_value）、
data provenance（`*_source_type`/`*_source`）與 rule provenance
（`rule_id`/`rule_source_document`/`rule_source_page`）分離。`Table4Analysis`
無 `primary_comparable` 欄位，`comparisons[]` 恆為清單。

## Task 4 — Table5-1 → Table4 Bridge

`backend/handlers/table51_analysis.py` 重構出可重用函式
`build_table51_analysis_for_case(case_no, meta)`（回傳 `(Table51Analysis,
CaseRuleResolution)`），`table4_analysis.py` 直接呼叫此函式取得**每個
comparable自己的** `Table51Comparison`，透過
`comp.segment_code` 對應查表（`table51_by_segment` dict），**從未**重新
呼叫 GradeEngine 對 29 個區域因素重算一次。`Table4AnalysisEngine.
build_comparison()` 本身會**主動檢查** `regional_comparison.
comparable_segment_code == comparable_segment_code`，不符即 `raise
ValueError`——防止任何呼叫端誤把錯的 comparable 之 Table5-1 結果傳入
（測試 `test_5_no_table51_table4_cross_contamination` 直接驗證）。

```
TABLE4_P002_TABLE51_BRIDGE_VERIFIED=YES
TABLE4_P003_TABLE51_BRIDGE_VERIFIED=YES
TABLE4_P004_TABLE51_BRIDGE_VERIFIED=YES
TABLE4_TABLE51_CROSS_CONTAMINATION=NO
```

## Task 5 — Shulin Individual Factors（19標準+1FAR=20）

新增 `engine/individual_factor_catalog.py`：**明確對照表**（非機械推導，
因兩個因素與 `schemas/field_dictionary.json` 既有命名不機械吻合）：
`IND-LAND_TERRAIN_INDIVIDUAL`→`individual_terrain_form4`（地勢同時存在
regional/individual，用`_form4`後綴區分）；`IND-DEAD_END_ALLEY`（無尾巷）
→`individual_other`（XLSX本身只有通用「6其他」列，無專屬「無尾巷」列，
field_dictionary.json 也只有 `individual_other` 這個對應槽）。

每個 comparable 皆呼叫 `Table4AnalysisEngine._build_individual_factor_results()`
— 直接呼叫既有 `GradeEngine.grade_factor()`/`AdjustmentEngine.
compute_adjustment()`（同一組 API，`FormCompletionEngine` 也是這樣呼叫），
無第二套 grading。P001 vs P002/P003/P004 各自獨立。

```
TABLE4_INDIVIDUAL_FACTOR_COUNT=20
TABLE4_STANDARD_MATRIX_FACTOR_COUNT=19
TABLE4_SPECIAL_RULE_FACTOR_COUNT=1
```

## Task 6 — FAR Special Rule

`individual_floor_area_ratio` 完全比照 A2 之既有政策：Shulin
individual_rules.json 對此 field_id **無任何 rule record**，
`GradeEngine.grade_factor()` 必然 `RuleNotFoundError`→
`GradeEngineError`→`MANUAL_REVIEW_REQUIRED`，`reason` 明確引用
評價基準明細表.pdf p.8 之「容積率差異以土地開發分析法進行試算調整」，
**從未**改用區域因素容積率矩陣、**從未**回傳 fabricated 0%、**從未**因
兩邊 raw_value 相同就自行判定 adjustment=0（因為 grading 階段根本不會
走到那一步，缺 rule record 就直接是 RuleNotFoundError，不看兩邊值是否
相同）。因 FAR 恆需人工複核，`individual_adjustment_total_pct` 對
Shulin residential 案件**恆為 None**（fail-closed，即使其餘19項全數
COMPLETED）——已在測試中明確驗證並記錄為**預期行為**，非 bug。

```
TABLE4_INDIVIDUAL_FAR_STANDARD_MATRIX_USED=NO
TABLE4_INDIVIDUAL_FAR_FAKE_ZERO_USED=NO
TABLE4_INDIVIDUAL_FAR_FAIL_CLOSED=YES
```

## Task 7 — Missing != Zero

`Table4AnalysisEngine._build_individual_factor_results()` 完全比照
Table51 之既有邏輯：因素缺席於任一側→`MANUAL_REVIEW_REQUIRED`+`raw_
value=None`；合法真值0（如建蔽率0%）→正常分級、`status=COMPLETED`。
`individual_adjustment_total_pct`/`trial_price`/`adjustment_abs_sum`
於任一必要輸入缺席時皆為 `None`，從未部分加總冒充完整值。

```
TABLE4_MISSING_DISTINGUISHED_FROM_ZERO=YES
TABLE4_MANUAL_REVIEW_ISOLATED_PER_COMPARABLE=YES
```

## Task 8/9 — Calculation/Weight Formula Audit

見 Task 0：`CalculationEngine`／`ComparableSelectionEngine` 皆已誠實
標註來源，`base_parcel_comparison_price()` 拒絕自行推算權重，
`suggest_weights()` 明確標示「§27 無公式，SYSTEM_AUXILIARY，
NON_STATUTORY，requires_human_confirmation=True」。`Table4Comparison.
weight_status` 預設 `MANUAL_REVIEW_REQUIRED`，`weight_pct=None`，
`weight_requires_human_confirmation=True`，`weight_basis` 明確引用
§27 無公式之事實——本輪**未**產生任何最終加權比準地比較價格（超出範圍，
需要人工確認每個比較標的權重後才能計算，留待未來輪次視需求擴充）。

```
STATUTORY_WEIGHT_FORMULA_CLAIMED=NO
UNVERIFIED_WEIGHT_AUTO_APPLIED=NO
```

## Task 10 — Competition Fixed Value Precedence（個別因素）

`case_reconstruction.py::_apply_competition_provided_precedence()` 泛化
為接受 `fixed_key` 參數（預設仍為 regional 之
`competition_provided_factors`，向後相容零改變），新增
`apply_competition_provided_individual_precedence()` 使用
`competition_provided_individual_factors`（collect_data.py 新增此
request body 欄位，segment-scoped 儲存），套用在
`user_submitted_factors.base_parcel_factors`/`comparable_factors` 之上。

```
TABLE4_P002_FIXED_PRECEDENCE=YES
TABLE4_P003_FIXED_PRECEDENCE=YES
TABLE4_P004_FIXED_PRECEDENCE=YES
```

## Task 11 — Provenance

`Table4FactorResult`（同 Table51FactorResult 設計）+ `Table4Comparison`
之 `transaction_source_type`/`transaction_source`（0基本資料 provenance）
+ `regional_adjustment_source_comparison_index`（Table5-1 lineage 證明）。

```
TABLE4_INPUT_PROVENANCE_COMPLETE=YES
TABLE4_RULE_PROVENANCE_COMPLETE=YES
TABLE4_CALCULATION_PROVENANCE_COMPLETE=YES（CalculationEngine 本身已含
  formula/source_document/source_page，個別因素/區域因素/試算價格三段皆可
  追溯）
TABLE4_DATA_AND_RULE_SOURCE_SEPARATED=YES
```

## Task 12 — API Handler

`infra/template.yaml` 確認先前**未**註冊 `/api/cases/{id}/table4`
（grep 確認零命中）。新增 `GetTable4AnalysisFunction`（與既有
`GetTable51AnalysisFunction`/`GetFacilityCandidatesFunction` 同一模式）。

```
TABLE4_HANDLER_EXISTS=YES
TABLE4_PUBLIC_API_ROUTE_REGISTERED=YES
```

## Task 13 — Real Handler E2E

`tests/test_table4_three_comparable_d1.py::TestRealHandlerE2E`：
create_case（含 segments）→collect_data×4（真實 題目.pdf 表3+表4
fixture）→CONFIRMED Shulin rule package→table51_analysis→table4_analysis，
確認 `comparison_count=3`，P002/P003/P004 lineage 各自正確、
`adjusted_price_raw` 與 fixture 逐字相符。

```
TABLE4_REAL_HANDLER_RUNTIME_VERIFIED=YES
```

## Task 14 — Legacy Compatibility

Legacy 金山案件（無 segments）呼叫 `/table4` 得到明確
`400 SEGMENT_MAP_REQUIRED`，從未嘗試建立三比較標的模型；既有
`complete_form.py` 單一比較標的流程完全未被觸碰，測試鎖定持續通過。

```
LEGACY_JINSHAN_TABLE4_FLOW_PRESERVED=YES
```

## Test Results（Task 15/16）

新增 `tests/test_table4_three_comparable_d1.py`（23 tests，Task 15 全部
20 項編號場景 + 3 個額外的靜態原始碼掃描/base-side precedence 補充測試）。

實際 `TESTS_PASSED`/`TESTS_FAILED`/`TESTS_SKIPPED`/`PYTEST_EXIT_CODE`
見本輪 FINAL REPORT。
