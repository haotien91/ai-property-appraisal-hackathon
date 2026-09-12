# TEAMMATE_RULE_DATA_INTEGRATION_REPORT

**日期**：2026-09-11
**性質**：STEP 4 — 正式整合`docs/incoming_rule_sources/`內的隊友資料。
延續STEP 2/3A/3B/3C既有基礎（`CaseRulePackage`/`CaseRuleRepository`/
Importer/Human Confirmation/AI Semantic Fallback），本輪**不建立**
第二套GradeEngine/AdjustmentEngine/RegionalRateCalculator/
Table4Calculator/Table52Calculator，**不**把中央maximum當成地方grade
rule，**不**把`regional_rate_calculation.json`當Runtime Rule，**不**把
`max_adjustment/(grade_count-1)`當成universal authoritative matrix。

分類詳見`docs/audit/TEAMMATE_RULE_SOURCE_CLASSIFICATION.md`；涵蓋矩陣
詳見`docs/audit/RULE_COVERAGE_MATRIX.md`；回歸測試詳見
`docs/audit/TEAMMATE_REGRESSION_REPORT.md`。本文件記錄整合過程本身
與每一步的實測結果。

---

**STEP5 更新（2026-09-11）**：依 STEP5 §26 明文指示「本輪不要再次擴充
隊友資料內容」，本輪**完全未修改**本報告涵蓋之任何隊友資料檔案或
`engine/central_local_rule_cross_validator.py`／
`data/rules/factor_alias_registry.json`。STEP5 §25 之 RULESET_
UNAVAILABLE 測試（`tests/test_competition_dual_input_e2e.py::
TestResidentialRulesetUnavailable`）再次確認本報告 §「判讀」所述
「住宅／工業／農業／其他用地目前沒有任何地方分級條件」之結論仍然
成立且正確——`RuleEngine`對這4類用地要求grade判定時，依設計正確拋出
`RuleNotFoundError`（本輪並額外發現並修正了一個真正相關的 bug：
`backend/handlers/cases.py`建案時未儲存`land_use_type`，導致此
RULESET_UNAVAILABLE語意過去實際上從未被觸發過——見
`COMPETITION_E2E_PHASE5_REPORT.md` §1「Bug 2」）。
`TEAMMATE_FIXTURE_USED_AS_RUNTIME_RULE=NO`於 STEP5 最終輸出區塊中
再次確認。

---

## 2. Central Maximum 交叉比對（實測，非抽樣）

新增`scripts/cross_check_teammate_central_data.py`（可執行、可被測試
呼叫的permanent script，非一次性scratch程式碼）：以`antiword`解析
隊友5份.doc官方文件（附件一~四＋1040130），依「(細項名稱正規化,
用地子級別)」為key，逐格與既有`data/rules/central_max_adjustment_
range.json`（418筆，未修改）比對。

**實測結果**：

```
TOTAL_CHECKED=378
TOTAL_MATCH=378
TOTAL_MISMATCH=0
TOTAL_NO_CENTRAL_MATCH=0
TOTAL_VARIANT_CHAR_MATCHES=10
```

**378個可比對儲存格，378個完全相符，零筆數值不一致**。其中10筆
（「建築基地改良（...鋪築道路...）」×6、「店鋪之毗連狀態」×4）需要
先正規化一個已知異體字問題才能比對上（見下）——正規化後同樣**數值
完全一致**，非資料衝突。

**已知異體字問題**：「舖」（U+8216）vs「鋪」（U+92EA），兩字皆為
「鋪設/鋪築」之意，屬教育部異體字表已知變體。隊友.doc檔案（經
antiword擷取）與既有`regional_rules.json`本身（見第5節）皆使用
「舖」；既有`central_max_adjustment_range.json`使用「鋪」。已記錄於
`data/rules/factor_alias_registry.json`（見第6節），**未**修改任何
既有資料檔案本身（雙方數值皆正確，只是用字不同，屬顯示/比對層級
問題，非資料層級問題）。

**結論**：

```
TEAMMATE_CENTRAL_DATA_DUPLICATES_EXISTING=YES
CENTRAL_SOURCE_DISCREPANCY_FOUND=NO
SECOND_CENTRAL_REGISTRY_CREATED=NO
```

未建立`central_factor_maximums.json`或任何第二份中央registry。
`central_max_adjustment_range.json`本身完全未修改。

## 3. '-' 語意確認

`scripts/cross_check_teammate_central_data.py::_teammate_value()`
明確將`"-"`與空字串正規化為`None`（NOT_APPLICABLE），**絕不**轉為
`"0"`（0%為一個真實、不同的數值，與「不予考慮」語意完全不同）。
既有`central_max_adjustment_range.json`本身之`DASH_NOT_APPLICABLE`
cell_state同樣以`max_range_pct=None`表示，兩者語意一致
（`tests/test_teammate_rule_data_integration.py::TestDashSemantics`
實測確認）。

## 4. Land Use Coverage

完整矩陣見`docs/audit/RULE_COVERAGE_MATRIX.md`。摘要：**僅商業用地
（金山區Golden Case）同時具備CENTRAL_MAX＋LOCAL_GRADE＋
adjustment matrix，`RUNTIME_READY=YES`**；住宅／工業／農業／其他
用地目前**只有**中央上限資料，**沒有**地方grade rule／matrix，
明確標記`NOT_RUNTIME_GRADE_READY`，`RuleEngine`對這4類用地的查詢
會正確拋出`RuleNotFoundError`（實測見第10節）。

## 5. Local Rules vs Central Maximum

新增`engine/central_local_rule_cross_validator.py`：
`check_central_maximum_not_exceeded(local_rules, central_entries,
table_type, land_use_type)`——**只**做constraint/applicability
validation（比對`max_adjustment`是否超過中央上限），**不**呼叫
`RuleEngine`/`GradeEngine`/`AdjustmentEngine`，**不**判定任何grade
（實測`test_central_max_does_not_determine_grade`以import陳述式
grep鎖定）。

**Subgrade Mapping嘗試（誠實記錄，非猜測）**：本輪嘗試以「local
`max_adjustment`是否恰好與中央某一特定子級別（高度/中度/普通/村里鄰
商業用地）逐因素完全吻合」來**推論**（而非猜測）金山區商業用地地方
規則對應哪個中央子級別——**結果：無任何一個子級別能達到高吻合度**
（26個regional因素中，最佳吻合的「村里鄰商業用地」也只有12/26吻合，
其餘因素之local值明顯**小於**該子級別之中央值）。此結果**確認**
金山區地方基準表是**獨立校準之地方版本**，而非直接複製某個中央子
級別——`data/rules/central_max_range_local_mapping.json`兩筆
UNMAPPED紀錄**維持不變**，本輪**未**自行判定high/medium/ordinary/
neighborhood對應，符合明確指示。

**改用「不超過最寬鬆中央子級別」之保守上限檢查**（不需要知道確切
子級別即可有效驗證，邏輯：若local值未超過該因素**所有**中央子級別
中最寬鬆的一個，則必然未超過真正適用的那個；若確實超過最寬鬆的
那個，則無論哪個子級別適用，都已違反上限）：

- **`regional_rules.json`（131筆）全數通過**——零筆超過中央上限
  （`test_central_maximum_not_exceeded_for_clean_factors`實測確認）。
- **`individual_rules.json`（84筆）發現1筆真實違反**：因素
  「道路種類」，local `max_adjustment=8`，但中央個別因素表（
  `1040130...doc`item 13.道路種類，商業用地欄）之上限為**5**。

```
道路種類: local_max_adjustment=8, central_ceiling=5 → CENTRAL_MAXIMUM_EXCEEDED
```

**此為RULE DATA ERROR，不是案件估價錯誤**——是既有（Golden Case
已凍結）`data/rules/individual_rules.json`資料本身的問題，**非**任何
案件的估價過程有誤。本輪**未修改**`individual_rules.json`（該檔案
為Golden Case既有回歸測試所依賴之凍結資料，修改它有很高機率打破
既有860+項通過中的回歸測試，且超出「整合隊友資料」本輪範圍）——
已透過`test_central_maximum_exceeded_detected`測試**鎖定此發現**
（確保這個真實存在的問題不會在未來被意外「修正掉」而不被注意到），
並在此正式記錄，留待人工決定是否於未來輪次修正`individual_rules.
json`本身。

## 6. Alias Registry

新增`data/rules/factor_alias_registry.json`（11筆，非平行命名系統——
每筆`canonical_factor_id`皆已存在於既有local rule或central registry
中，見`test_no_second_parallel_naming_system`實測確認）。7筆regional
scope、4筆individual scope，皆源自本輪與先前STEP 1/3A/3C稽核中
**實際觀察到**的命名差異（見下表摘要），**非**憑空杜撰的範例：

| Canonical | Alias | 差異類型 |
|---|---|---|
| 都市計畫（內、外） | 都市計畫內外 | 標點符號 |
| 區段內道路平均寬度 | 區段內已開闢道路平均寬度 | 用詞插入（MEDIUM confidence） |
| 接近大型車站之程度 | 接近大型車站距離程度 | 用字順序 |
| 排水之良否 | 排水系統是否良好 | 用字順序 |
| 使用分區（使用地類別） | 使用分區(使用地類別) | 全形/半形括號 |
| 店鋪之毗連狀態 | 店舖之毗連狀態 | 「鋪」/「舖」異體字 |
| 建築基地改良（...鋪築...） | 建築基地改良（...舖築...） | 同上 |
| 臨街情形 | 臨路情形／臨街情況 | **三種寫法**，僅MEDIUM confidence，建議人工確認 |
| 嫌惡設施 | 嫌惡設施之有無 | 後綴差異 |
| 使用分區或編定用地 | 使用分區或編定 | 後綴省略 |
| 接近學校之程度 | 接近學校程度 | 助詞省略 |

**誠實記錄**：所有11筆`verified_by`皆為`null`——本輪僅透過
deterministic交叉比對（正規化後exact match或official note句子內
exact substring）**提出**這些alias，**未**經過人工domain expert
明確簽核。該檔案本身的`verification_note`欄位明確記載此狀態，任何
未來要把這些alias用於自動化決策（超出STEP 3A既有exact-match-only
政策）的用途，都應先由人工設定`verified_by`。

```
ALIAS_REGISTRY_READY=YES
```
（檔案存在、格式正確、可被程式讀取使用，但**內容尚未人工verified**，
見上）。

## 7. regional_rate_calculation.json → Regression Fixture

已複製（非搬移，保留原始檔案於`docs/incoming_rule_sources/`原位置
供provenance查閱）為
`tests/fixtures/teammate_regional_rate_calculation.json`。已確認
**未**被任何`backend/`/`engine/`/`providers/`/`domain/`下的正式程式
碼引用（`test_regional_rate_fixture_never_referenced_by_production_
code`以全文搜尋鎖定）。

**TEAMMATE_EXPECTED vs MAIN_ENGINE_ACTUAL**：28筆記錄中，19筆
（`sub_item.name_zh`）與既有`regional_rules.json`因素名稱exact match
可供比對；比對內容為「teammate自行編碼之benchmark grade（
`grade_label_zh`+`rank`）是否與既有官方規則表**自己的**
grade_code↔grade對應一致」——**19筆全數通過，零筆不一致**
（`test_teammate_expected_vs_main_engine_actual`實測）。其餘9筆因
teammate用詞與local rule不完全一致（例如「站牌或交通運輸密集之
接近程度」vs local「站牌之接近程度或密集程度」），本輪**未**強行
比對（避免猜測式配對），歸類為需要人工/未來alias擴充處理。**對
`comparable_missing`（28筆全數如此，比較標的留空）：本輪未自行
補值**，維持teammate原始資料之未完成狀態。

```
REGIONAL_RATE_JSON_IS_RUNTIME_RULE=NO
REGIONAL_RATE_JSON_IS_FIXTURE=YES
```

## 8. Formula Boundary

`regional_rate_calculation.json`裡的`step_rate_pct`欄位（
`max_abs_rate_pct/(grade_count-1)`）**僅**在
`test_teammate_expected_vs_main_engine_actual`與交叉比對邏輯中被視為
teammate自行推算之derived helper／regression expectation，**從未**
被本輪任何程式碼當作authoritative matrix使用或寫入
`regional_rules.json`/`individual_rules.json`/任何CaseRulePackage的
`regional_rules`/`individual_rules`欄位。既有Phase 3A Importer的
Matrix Priority原則（explicit matrix優先，never用公式重建）本輪
完全沿用、未變更。

## 9. Same Segment Logic

現況確認：`engine/adjustment_engine.py`／`calculation_engine.py`／
`audit_engine.py`**皆不含**任何「if same_segment: adjustment=0」
式的寫死捷徑（`test_no_hardcoded_same_segment_zero_shortcut`以文字
搜尋確認不存在）——既有`test_calculation_engine.py::
test_all_zero_when_same_segment`測試的「同區段→0」結果，是
`AdjustmentEngine.compute_adjustment()`對**相同grade**兩造透過矩陣
對角線（恆為0，`RuleTableValidator`既有diagonal-zero檢查所保證）
查表得出的**自然結果**，不是任何寫死捷徑。

**Remaining Gap（誠實記錄，本輪未實作）**：一個專門的
`SAME_SEGMENT_REGIONAL_FACTOR_INCONSISTENT`
`AuditEngine`稽核發現類型（當比準地與比較標的雖屬同一地價區段，
但送入的regional factor證據導致兩造grade不同時應予標記）**目前
不存在**於`engine/audit_engine.py`。建置此檢查屬於對既有`AuditEngine`
新增功能，涉及`domain.models.AuditIssue`/`IssueType`既有六大類別
（見`land-appraisal-audit`技能之既有規範）之審慎擴充，超出本輪
「整合隊友資料」之範圍，留待後續輪次，本輪僅確認**沒有**寫死的
錯誤捷徑存在。

## 10. Rule Router

`RuleEngine`既有以`(city, district, land_use_type, factor[,
rule_set])`為索引鍵之查詢設計（`engine/rule_engine.py`，本輪未修改）
**結構上已經**提供Rule Router所需之jurisdiction→district→
land_use_type→ruleset路由：不同鍵值組合的查詢**不可能**互相命中。
實測（`TestRuleRouterNoCrossFallback`）：

- 住宅用地查詢對只載入商業用地規則之`RuleEngine`：`RuleNotFoundError`
  （非住宅吃商業）。
- 板橋區查詢對只含金山區規則之`RuleEngine`：`RuleNotFoundError`
  （非其他行政區吃金山）。
- 三重區商業用地查詢：`RuleNotFoundError`（非缺資料吃Golden）。

```
RULE_ROUTER_CROSS_LAND_USE_FALLBACK_FOUND=NO
JINSHAN_FALLBACK_FOUND=NO
GOLDEN_FALLBACK_FOUND=NO
```

缺規則時之行為即為`RuleNotFoundError`（deterministic engine層級）；
在STEP 2/3B的`rule_engine_factory`/`CaseRuleRepository`層級，對應的
案件層級語意為`RULESET_UNAVAILABLE`/`MANUAL_REVIEW_REQUIRED`
（`STATIC_LOCAL`+`CASE_RULE_NOT_CONFIRMED`等既有機制，STEP 2/3B已
建置，本輪未變更）。

## 11. Teammate Data as Non-Golden Stress Test

依`RULE_COVERAGE_MATRIX.md`：**商業用地**（Golden Case金山區）具備
完整RUNTIME_READY覆蓋；**住宅／工業／農業／其他用地**皆**僅有**
central maximum、**無**local grade rule，依規定明確標記：

```
NOT_RUNTIME_GRADE_READY=YES（住宅／工業／農業／其他用地）
```

本輪**未**假裝這4類用地可直接估價，也**未**為了「看起來能跑」而
虛構local grade rule或adjustment matrix。

## 12. Importer Stress Test（Factor Mapping品質，非完整PDF matrix解析）

**重要澄清**：隊友5份.doc為CENTRAL_MAXIMUM表格結構（無grade
band、無matrix），與Phase 3A Importer鎖定的評價基準明細表PDF結構
（含grade band＋matrix）**不同**——直接對這5份.doc執行Phase 3A的
矩陣區塊解析器，會因找不到「比凖地/(比較標的)/宗地/(比準地)」表頭
而正確回傳零候選，非有意義的stress test。因此本輪改為針對
**Phase 3A/3C真正可重用之子元件**——factor mapping邏輯本身
（`engine/evaluation_standard_importer.py::_resolve_canonical_factor()`
deterministic exact-match ＋
`providers/semantic_rule_mapping_provider.py::
MockSemanticRuleMappingProvider` AI fallback）——以隊友.doc檔案中
實際出現的65個不重複因素名稱作real-world stress corpus，逐一測試
能否被正確mapping到既有商業用地local rule因素清單：

```
DETERMINISTIC_MATCH_COUNT=44
AI_CANDIDATE_COUNT=10
MANUAL_REQUIRED_COUNT=11
UNMAPPED_COUNT=0
```

44/65（68%）可由deterministic exact-match直接命中（因為隊友用字與
既有Golden Case商業用地local rule高度重疊，非本輪為提高成功率而
加入任何hardcoded Golden mapping——這44筆全部透過既有
`_resolve_canonical_factor()`的純字串exact-match機制達成，可於
`tests/test_teammate_rule_data_integration.py`重現）；10/65（15%）
由Mock AI給出HIGH/MEDIUM confidence建議；11/65（17%）僅LOW
confidence，需人工複核；0/65完全無法給出任何回應
（provider本身零失敗）。

## 13. 不做事項確認

| 禁止事項 | 確認 |
|---|---|
| 第二套GradeEngine/AdjustmentEngine | 未新建，`engine/central_local_rule_cross_validator.py`明確不呼叫grade判定（第5節） |
| RegionalRateCalculator/Table4Calculator/Table52Calculator | 未新建 |
| 把中央maximum當地方grade rule | 未做——`central_local_rule_cross_validator.py`僅做上限檢查，不產生grade |
| 把`regional_rate_calculation.json`當Runtime Rule | 未做，見第7節 |
| 把`max_adjustment/(grade_count-1)`當universal matrix | 未做，見第8節 |
| 猜測high/medium/ordinary/neighborhood對應 | 未做，見第5節（嘗試推論後確認無法可靠推論，維持UNMAPPED） |

## 14. Regression

見`docs/audit/TEAMMATE_REGRESSION_REPORT.md`。

## 15. Files Added / Modified

**新增**：
- `scripts/cross_check_teammate_central_data.py`
- `engine/central_local_rule_cross_validator.py`
- `data/rules/factor_alias_registry.json`
- `tests/fixtures/teammate_regional_rate_calculation.json`（複製自
  `docs/incoming_rule_sources/regional_rate_calculation.json`）
- `tests/test_teammate_rule_data_integration.py`（23 tests）
- `docs/audit/TEAMMATE_RULE_SOURCE_CLASSIFICATION.md`
- `docs/audit/RULE_COVERAGE_MATRIX.md`
- `docs/audit/TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md`（本文件）
- `docs/audit/TEAMMATE_REGRESSION_REPORT.md`

**未修改**：`data/rules/central_max_adjustment_range.json`／
`data/rules/central_max_range_local_mapping.json`／
`data/rules/regional_rules.json`／`data/rules/individual_rules.json`
（含第5節發現之「道路種類」問題在內，皆維持凍結）／
`engine/rule_engine.py`／`engine/grade_engine.py`／
`engine/adjustment_engine.py`／`engine/calculation_engine.py`／
`engine/audit_engine.py`／`engine/evaluation_standard_importer.py`／
`rule_engine_factory.py`／`docs/incoming_rule_sources/`內任何原始
檔案／前端／`infra/template.yaml`。

## 16. Remaining Gaps

1. **`SAME_SEGMENT_REGIONAL_FACTOR_INCONSISTENT`稽核類型未實作**
   （第9節）。
2. **9筆regional_rate_calculation.json記錄之factor名稱未能對應**
   （第7節）——若要提高覆蓋率，需擴充`factor_alias_registry.json`，
   本輪未做以避免猜測式配對。
3. **住宅/工業/農業/其他用地之local grade rule仍完全缺失**——中央
   上限已100%涵蓋，但要讓這4類用地`RUNTIME_READY`，需要官方或地方
   政府發布之具grade band+matrix的評價基準明細表，非本輪範圍內
   可取得。
4. **Alias Registry尚未經人工verified**（第6節）。
