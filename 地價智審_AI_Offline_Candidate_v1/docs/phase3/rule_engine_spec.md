# Phase 3 — Rule Engine Spec（規則引擎規格）

## 1. 目標與範圍

將新北市金山區商業用地之「影響地價區域因素評價基準明細表」（28項因素）與
「影響地價個別因素評價基準明細表」（19項因素）轉為機器可讀之 JSON 規則
（`schemas/rule_schema.json` + `data/rules/regional_rules.json` +
`data/rules/individual_rules.json`），並提供最小可用之 deterministic Rule Engine
（`engine/rule_engine.py`）完成 Normalize → Rule Selection → Range Match →
Grade Result → Adjustment Matrix Lookup 五步驟。

**明確排除於本階段範圍**：AWS部署、Bedrock整合、前端串接、GIS實際量測（僅使用
Golden Case已知數值驗證，不做地理運算）、其他縣市/行政區/用地類別之規則（Schema
設計為可擴充，但本階段僅填入金山區商業用地資料）。

---

## 2. 資料來源與查核方式

依任務要求「不得只依OCR Parsed Text建規則」，本階段對評價基準明細表範例.pdf
全部9頁（區域因素頁1-5，個別因素頁6-9）逐頁以圖像方式重新視覺核對，**未**單獨
依賴先前階段之OCR文字擷取結果。每一因素之grade band與adjustment_matrix數值，
均以圖像直接讀取後手動轉錄至 `data/rules/*.json`，並盡可能以 Golden Case
（案號1140901-99-001）之表4/表5-2實際記載值進行交叉驗證。

**驗證覆蓋率**：
- 個別因素19項中，5項（深度、道路種類、面前道路寬度、嫌惡設施、停車方便性）
  具備「比準地≠比較標的」之非平凡Golden Case數據，可獨立驗證grade band與矩陣
  查找兩者皆正確；其餘14項比準地與比較標的等級相同（差異率恆為0），僅能驗證
  grade band本身，無法驗證矩陣非對角線數值。
- 區域因素28項全數為退化情形（比準地與比較標的1同屬P002-00區段），僅能驗證
  grade band，無法驗證矩陣方向或非對角線數值（見source_anomalies.md ANOMALY-05）。

---

## 3. 關鍵發現：修正率矩陣行列方向證明

評價基準明細表之修正率矩陣表頭文字為「比凖地(比較標的)」（欄）／「宗地
(比準地)」（列），此文字組合本身無法單看字面確定方向。本階段以**非平凡Golden
Case數值**反推驗證，證明過程如下：

### 3.1 範例：宗地深度

- Golden Case 表4 記載：比準地深度=23m，比較標的1深度=16m，差異率=**1.00%**
- 依評價基準明細表個別因素頁6，深度級距：優=40m以上未滿100m，稍優=30m以上未滿
  40m，**普通=20m以上未滿30m**，**稍劣=10m以上未滿20m**，劣=未滿10m或100m以上
- 23m → 普通（代碼3）；16m → 稍劣（代碼4）
- 矩陣「普通」列數值為 `[-2.00, -1.00, 0, 1.00, 2.00]`（對應優/稍優/普通/稍劣/劣欄）
- 若列=比準地(普通)，欄=比較標的(稍劣)：matrix[3][4] = **1.00** ✓ 與官方記載
  完全相符
- 若列=比較標的(稍劣)，欄=比準地(普通)：matrix[4][3] = **-1.00** ✗ 與官方記載
  （+1.00%）矛盾

**結論：列=比準地等級，欄=比較標的等級。**

### 3.2 交叉驗證（獨立於3.1的另外4組非平凡數據）

| 因素 | 比準地值→等級 | 比較標的值→等級 | matrix[比準地列][比較標的欄] | 官方記載差異率 | 結果 |
|---|---|---|---|---|---|
| 道路種類 | 主要道路→優(1) | 次要道路→稍優(2) | matrix[1][2]=2.00 | 2.00% | 一致 |
| 面前道路寬度 | 18M→稍優(2) | 6M→稍劣(4) | matrix[2][4]=5.00 | 5.00% | 一致 |
| 嫌惡設施 | 260M→普通(3) | 80M→劣(5) | matrix[3][5]=3.00 | 3.00% | 一致 |
| 停車方便性 | 可路邊停車→優(1) | 不可路邊停車→劣(5) | matrix[1][5]=2.00 | 2.00% | 一致 |

5組獨立數據**全數一致**，矩陣方向判定具高信心。此判定已寫入
`data/rules/*.json` 頂層 `matrix_orientation_note` 欄位，並套用於全部28項區域
因素（依類比，見source_anomalies.md ANOMALY-05之保留意見）與19項個別因素。

---

## 4. Rule Schema 設計說明

見 `schemas/rule_schema.json`（JSON Schema draft-07）。設計要點：

- **可擴充性**：`city`/`district`/`land_use_type` 三欄位構成規則的「管轄範圍
  Key」，`RuleEngine` 以 `(city, district, land_use_type, factor)` 為索引鍵；
  新增其他行政區或用地類別，僅需新增對應規則記錄，不需修改 Engine 程式碼
  （驗證見 `tests/test_rule_engine_core.py::TestRuleNotFound::test_unknown_district_raises`
  與 `test_unknown_land_use_type_raises`，證明未定義管轄範圍會明確報錯而非
  誤用金山區規則）。
- **一列一規則（grade-band-level granularity）**：每個 grade band（優/稍優/
  普通/稍劣/劣或其子集）為一筆獨立規則記錄，自帶完整 `adjustment_matrix`（同一
  因素的5筆記錄矩陣內容相同，屬刻意的自我描述冗餘設計，讓 Rule Engine 載入
  單筆規則即可完成 grade 判定與 matrix 查找兩步驟，無需額外join）。
- **value_type 五種型態**：`numeric_range`（一般數值級距，如建蔽率）、
  `boolean`（2級有無/可否）、`categorical`（多級文字描述無數值級距，如使用
  分區）、`distance_positive`（越近越優）、`distance_negative`（越遠越優，
  嫌惡/污染/特殊設施類）。
- **anomaly_flag**：非null時指向 `source_anomalies.md` 對應編號，供 Smart
  Review 或人工複核時快速定位regulatory依據存疑之規則。

---

## 5. Rule Engine 設計說明

見 `engine/rule_engine.py`。五步驟對應：

| 步驟 | 方法 | 說明 |
|---|---|---|
| Normalize | `RuleEngine.normalize()` | 去除字串前後空白；**刻意不做**單位換算（km→m等），避免掩蓋Source資料本身的單位問題（見source_anomalies.md ANOMALY-01/02） |
| Rule Selection | `RuleEngine.select_rules()` | 依`(city,district,land_use_type,factor)`四鍵索引，找不到規則時拋出`RuleNotFoundError`，絕不回傳預設等級 |
| Range Match | `RuleEngine._match_numeric()` / `_match_label()` | 數值型依`lower_bound`/`upper_bound`/`lower_inclusive`/`upper_inclusive`比對；類別型依`grade_label`精確字串比對；**兩者互斥使用**（見§6之型態路由規則） |
| Grade Result | `RuleEngine.grade()` | 回傳含`rule_id`/`grade`/`grade_code`/`matched_rule`（含完整矩陣）之`GradeResult` |
| Adjustment Matrix Lookup | `RuleEngine.adjustment()` | 輸入兩個同一因素的`GradeResult`（比準地、比較標的），回傳矩陣查找之差異率；跨因素查找會拋出`RuleNotFoundError` |

**核心設計原則（呼應主專案指示第10-12節）**：
- 全程deterministic，無任何LLM呼叫或啟發式猜測。
- 找不到規則、單位不符、跨因素比對等情形，**一律拋出明確例外**，不回傳猜測值
  或預設值。此為`RuleNotFoundError`/`WrongUnitError`/`GradeNotComparableError`
  三種例外類別存在的直接原因。

### 5.1 「區段內有」類別哨兵狀態（重要設計細節）

部分距離類因素（如停車場地之便利程度）之「優」等級定義為文字性狀態「區段內有」
而非數值距離，此狀態在資料上以`lower_bound=None, upper_bound=None`表示。開發
過程中發現：若不特別處理，任何數值輸入都會被預設邏輯誤判為此哨蘵狀態並回傳
「優」（因為「上下界皆為None」在單純的`>=`/`<`比對中預設視為通過）。已修正
`_match_numeric()`使其明確跳過此類記錄，僅能透過傳入等於`grade_label`的文字
（如`"區段內有"`）明確命中；修正過程與回歸測試詳見
`source_anomalies.md` ANOMALY-09。

---

## 6. 輸入型態路由規則

`grade()`方法依`value_type`與輸入之Python型態決定比對路徑：

```
value_type ∈ {numeric_range, distance_positive, distance_negative}:
    若輸入為 str  → 走類別比對（含"區段內有"等哨兵狀態）
    若輸入為 number → 走數值區間比對
value_type ∈ {boolean, categorical}:
    一律走類別比對（含Python bool會被轉為"有"/"無"再比對，非"有"/"無"標籤
    之布林因素則會找不到規則而報錯，見test_parking_convenience_boolean_python_bool_input）
```

---

## 7. 可追溯性（Traceability）

`data/rules/regional_rules.json` 與 `data/rules/individual_rules.json` 每筆
規則均含 `source_document`／`source_page`／`source_note` 三欄位，`source_note`
另包含該因素之Golden Case驗證結果摘要（若適用）。完整可追溯鏈：

```
規則記錄 (rule_id)
  → source_page (評價基準明細表範例.pdf 頁碼)
  → source_note (官方原文摘要 + Golden Case交叉驗證結果)
  → anomaly_flag (若有疑義，指向 source_anomalies.md 編號)
```

---

## 8. 已知限制（承接並延伸 Phase 1/2）

1. 宗地深度之U型分級（ANOMALY-03）尚未在Engine中完整實作雙區間比對，超過100m
   之深度值目前會導致`RuleNotFoundError`而非正確判定為「劣」。
2. 區域因素矩陣方向為類比推斷，非獨立驗證（ANOMALY-05）。
3. 表4「6其他」欄位無對應規則（ANOMALY-07），遇此欄位須MANUAL_REVIEW_REQUIRED。
4. 兩處m/km疑似誤植（ANOMALY-01/02）採推定值運作，非官方確認之訂正。
5. 「交流道無」對應最劣等級之判定邏輯不明（ANOMALY-04），Rule Engine不自動
   處理「無此設施」之輸入，需由呼叫端依評價基準明細表明文決定。
6. 多比較標的（2筆以上）情境下之權重公式（Phase 1 open_questions.md B-2/C-5）
   仍不在本階段Rule Engine範圍內，`adjustment()`僅處理單一因素之grade-to-grade
   查找，不涉及多比較標的加權平均。

## 9. Golden Source 驗證結果摘要

`tests/test_golden_case.py`：19筆個別因素測試（5筆非平凡+14筆平手）＋11筆區域
因素等級測試＋2筆加總測試，全數通過，個別因素差異率加總=13.00%（與表4「合計」
記載完全一致），區域因素加總=0.00%（與表5-2/表4記載完全一致）。
