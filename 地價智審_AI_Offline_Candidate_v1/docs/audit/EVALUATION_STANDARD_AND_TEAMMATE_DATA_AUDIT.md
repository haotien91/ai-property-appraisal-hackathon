# EVALUATION_STANDARD_AND_TEAMMATE_DATA_AUDIT

**日期**：2026-09-09
**性質**：PHASE 1 READ-ONLY AUDIT——本文件記錄期間**未修改任何production
程式碼**，僅讀取/解析既有原始碼與`docs/incoming_rule_sources/`資料。
**範圍**：(A) 比賽當天新評價基準明細表動態匯入能力現況；(B) 隊友資料
（`docs/incoming_rule_sources/`）與既有專案之整合可行性評估。

**方法論附註**：`docs/incoming_rule_sources/`裡的5份`.doc`檔為舊版
Word二進位格式（Composite Document File V2），無法用一般PDF/文字工具
直接讀取；本輪確認Windows環境已有`antiword`（`/mingw64/bin/antiword`）
可正確以UTF-8解出繁體中文表格內容，據此完成下方逐格比對，非憑檔名猜測
內容。

---

## 1. Current Rule Architecture

```
RuleEngine（engine/rule_engine.py，deterministic-only）
  ├─ 建構時吃一份「扁平規則列表」（List[Dict]），呼叫端負責提供
  ├─ normalize() → select_rules()（依city/district/land_use_type/
  │  factor(+可選rule_set)查表）→ grade()（數值區間或label比對）
  │  → adjustment()（矩陣查表）
  └─ 從不猜測：查無規則、單位不符、grade_code不在矩陣內，一律拋出
     明確typed exception（RuleNotFoundError/WrongUnitError/
     GradeNotComparableError/AmbiguousFactorError）

GradeEngine（engine/grade_engine.py）——包一層RuleEngine.grade()，
  轉成帶Evidence/traceability的domain model，**本身不含任何評等邏輯**

AdjustmentEngine（engine/adjustment_engine.py）——包一層
  RuleEngine.adjustment()，矩陣查表結果轉domain model，**本身不含
  任何矩陣邏輯**（矩陣單一來源於rule record本身）

CalculationEngine / ComparableSelectionEngine / FormCompletionEngine /
AuditEngine / CrossFormValidationEngine——皆為RuleEngine/GradeEngine/
AdjustmentEngine之下游消費者，本輪未深入逐一稽核，但已確認
FormCompletionEngine（見第3節）完全透過field_id/factor泛用呼叫
GradeEngine/AdjustmentEngine，未寫死任何特定土地使用類別邏輯。
```

**與本輪任務最直接相關、但先前對話未曾提及的既有子系統**（本輪稽核
才發現）：

```
RuleTableIngest（engine/rule_table_ingest.py）
  三份CSV（factors.csv／bands.csv／matrix.csv）→ build_rules_from_csv()
  → RuleEngine可直接吃的規則JSON列表

RuleTableValidator（engine/rule_table_validator.py）
  對candidate規則列表做ERROR/WARNING分級檢查（必要欄位、value_type
  列舉、grade_code重複、矩陣是否方陣、max_adjustment是否與矩陣一致、
  級距銜接、7組官方分級用語比對……）

scripts/ingest_rule_table.py —— CLI包裝上述兩者，決賽現場實際操作介面
docs/phase8a/rule_table_ingestion_guide.md —— 完整操作指南（已存在）

CentralMaxRangeDatasetValidator（engine/central_max_range_validator.py）
  驗證data/rules/central_max_adjustment_range.json（418筆，內政部104年
  令「影響地價個別/區域因素評價基準表」全數位化，涵蓋5種用地×
  regional+individual）本身結構完整性

data/rules/central_max_range_local_mapping.json —— 記錄「地方規則檔
vs 中央上限」之對應狀態，目前恰好2筆、全數UNMAPPED（見第6節）
```

**關鍵事實**：這些模組（`rule_table_ingest.py`／`rule_table_validator.py`
／`scripts/ingest_rule_table.py`／`central_max_range_validator.py`）
**已經是本專案既有、已測試、已文件化的程式碼**，並非本輪才發現需要
新建——本輪任務A（動態匯入能力）的核心轉換與驗證邏輯**已經存在**，
缺口在「串接到實際案件審查流程」這一段（見第6/9節）。

---

## 2. Existing Engine Coverage

| Engine | 是否可重用於本輪任務 | 依據 |
|---|---|---|
| RuleEngine | ✅ 可完全重用，不需修改 | 建構時吃任意規則列表，非寫死讀取特定檔案 |
| GradeEngine | ✅ 可完全重用，不需修改 | 純泛用包裝，rule_set參數強制顯式指定 |
| AdjustmentEngine | ✅ 可完全重用，不需修改 | 矩陣邏輯單一來源於rule record，無重複實作 |
| FormCompletionEngine | ✅ 可完全重用，不需修改 | 表4/表5-2生成透過field_id/factor泛用呼叫，見第3節 |
| RuleTableIngest/Validator | ✅ 已存在、已測試，本輪只需擴充呼叫方式（見第9/12節），核心邏輯不需重寫 |
| CentralMaxRangeDatasetValidator | ✅ 已存在、已測試（27項），僅驗證central資料集本身，未做local↔central交叉驗證（本輪缺口，見第6節） |

**不應重建**（呼應使用者明確指示，本輪確認這些既有元件確實涵蓋對應
職責，重建它們會是不必要的重複工作）：RegionalRateCalculator（功能已
由RuleEngine+AdjustmentEngine涵蓋）、Table52Calculator/Table4Calculator
（功能已由FormCompletionEngine涵蓋）、NewGradeEngine（功能已由
GradeEngine涵蓋）。

---

## 3. Existing Rule Coverage

**`data/rules/regional_rules.json`**：131筆，`land_use_type`欄位
**僅有一種值：「商業用地」**（新北市金山區，Golden Case範圍）。
Schema欄位：`rule_id, version, city, district, land_use_type, category,
factor, value_type, unit, direction, lower_bound, upper_bound,
lower_inclusive, upper_inclusive, grade, grade_code, grade_label,
adjustment_matrix, max_adjustment, effective_date, source_document,
source_page, source_note, anomaly_flag`。

**`data/rules/individual_rules.json`**：84筆，`land_use_type`欄位**同樣
僅有「商業用地」**，19個因素。Schema與regional_rules.json完全一致。

**`adjustment_matrix`實際內容**：本輪抽查發現**並非統一套用
`max_rate/(grade_count-1)`**——

- 2級（優/劣）布林因素「都市計畫（內、外）」：矩陣為
  `{"1":{"1":0,"5":20},"5":{"1":-20,"5":0}}`，max_adjustment=20，
  恰好等於中央上限表對應儲存格之20%（見第5節交叉驗證）。
- 5級因素「面積」：矩陣呈現`0,2,4,6,8`線性等距（max_adjustment=8，
  即8÷4=2/級），**這個特定因素恰好確實符合`max/(count-1)`模式**。

**結論（呼應使用者「重要觀念」之提醒）**：現有已驗證（Golden Case）
資料本身就顯示矩陣**因素而異**——有些線性等距、有些不是（2級因素的
「等距」只是2級系統的數學必然，不能類推到5級因素）。這證實了
`max_rate/(grade_count-1)`**不能被當成放諸四海皆準的官方公式自動套用
到所有新因素**，必須以官方明細表/矩陣原文（若有）為準，查無矩陣時
誠實標記為缺失，而非自動套公式生成。

---

## 4. Teammate New Coverage

`docs/incoming_rule_sources/`實際存在檔案（**使用者訊息提及之
`config.json`／`commercial_land_standard_schema.json`兩檔案本輪掃描
`.doc`確認：目錄中並不存在，見第16節Risks**）：

| 檔案 | 已用antiword解出內容 | 對應之現有系統資料 |
|---|---|---|
| 附件一影響住宅用地區域因素評價基準表.doc | ✅ 已讀取，14主要項目、住宅用地4種子級別（高級/中級/普通/村里鄰）×每細項1個最大百分比 | **無**local regional_rules.json涵蓋（現有僅商業用地） |
| 附件二影響商業用地區域因素評價基準表.doc | ✅ 已讀取，結構同上，商業用地4種子級別（高度/中度/普通/村里鄰商業用地） | **與`central_max_adjustment_range.json`商業用地regional部分逐格比對一致**（見第5節） |
| 附件三影響工業用地區域因素評價基準表.doc | ✅ 已讀取 | **無**local coverage |
| 附件四影響農業用地區域因素評價基準表.doc | ✅ 已讀取 | **無**local coverage |
| 1040130影響地價個別因素評價基準表(發布).doc | ✅ 已讀取，12主要項目×5種用地（住宅/商業/工業/農業/其他）之最大百分比，含「-」（不予考慮）語意 | 與`central_max_adjustment_range.json`個別因素部分**同一份內政部104年令**，欄位結構高度吻合 |
| regional_rate_calculation.json | ✅ 已讀取，28筆，`scope: "regional"`，欄位含商業用地地域因素之特有細項（百貨公司/金融機構/娛樂設施/展覽旅館/客流量/店面連續性等，`central_max_adjustment_range.json`未見這些細項） | 見第5節分析——這是「比準地已編碼、比較標的留空待補」的**未完成**worked example，非authoritative規則庫 |

**真正的新coverage在哪裡**：不是「中央上限數字」本身（那份已經有
418筆digitized），而是：
1. **住宅/工業/農業用地的LOCAL評價基準表原始文字**（表格標題、細項
   命名、子級別命名如「高級住宅用地」「大規模工業用地」之官方定義
   說明段落）——`central_max_adjustment_range.json`目前只存數字
   （`major_category_name`/`item_name`/`max_range_pct`），沒有存
   附件一~四文末那幾段「高級住宅用地係指……」的官方定義說明文字，
   附件.doc檔案裡有。
2. **`regional_rate_calculation.json`裡的商業用地地域因素特有細項**
   （百貨公司接近程度、金融機構接近程度、娛樂設施接近程度、展覽/
   旅館接近程度、客流量、店面連續性）——這6項**不在**現有131筆
   `regional_rules.json`裡，也**不在**`central_max_adjustment_range.
   json`的418筆裡（中央表本身欄位較粗略，此6項應屬地方自行補充之
   細項，比對附件二.doc內容確認附件二本身也未見這6項——**來源未明**，
   見第16節）。

---

## 5. Duplicate Work

**逐格交叉驗證（本輪實際執行，非假設）**：附件一「都市計畫（內、
外）」住宅用地四子級別皆20%，與`central_max_adjustment_range.json`
中`table_type=regional, land_use_type=住宅用地, item_name=都市計畫
（含、外）`四筆子級別記錄**逐字比對，數值完全一致（皆20）**。

**結論**：`附件一~四.doc`＋`1040130個別因素.doc`這5份檔案的
**表格數字本身**，與`data/rules/central_max_adjustment_range.json`
（418筆，本專案上一輪已透過PyMuPDF渲染圖像+人工逐格核對轉錄完成
數位化，因官方PDF內嵌字型無ToUnicode對照表、文字層擷取為亂碼）
**高度重疊、屬同一份內政部104年令來源**。重新把這5份`.doc`轉成JSON
規則會是**重複勞動**——除非目的是「拿一份文字層乾淨的來源，交叉
驗證上一輪人工圖像轉錄有沒有抄錯」（這件事本身有價值，見第12節
建議，但屬於QA用途，不是新增資料）。

**非重複、屬新增的部分**：附件文件裡的官方文字定義段落（第4節第1點）
與`regional_rate_calculation.json`裡的6項商業用地特有細項（第4節
第2點）。

---

## 6. Current Dynamic Import Gap

**存在的部分**：`.csv三件套 → rule_table_ingest.build_rules_from_csv()
→ rule_table_validator驗證 → 輸出可用JSON`這條路徑**已經存在、已測試、
已文件化**（`docs/phase8a/rule_table_ingestion_guide.md`）。

**缺口（本輪稽核實際追蹤程式碼確認）**：`backend/handlers/analyze.py`
：34、`complete_form.py`：30、`review.py`：85，**三個handler各自獨立**
呼叫`RuleEngine(reg + ind)`，其中`reg`/`ind`**寫死只讀取
`data/rules/regional_rules.json`／`individual_rules.json`這兩個檔案**
——沒有任何機制讓`ingest_rule_table.py`轉出的新JSON（例如
`data/rules/new_segment_regional_rules.json`）在案件審查當下被這三個
handler實際載入使用。也就是說：

```
決賽現場產生新規則JSON（已存在的能力）
        ↓
     ✋ 缺口 ✋（三個handler不知道要多讀這個檔案）
        ↓
RuleEngine實際被案件審查使用（既有能力）
```

**這正是使用者Point A「動態匯入能力」目前唯一真正缺失的一段**——
不是規則轉換/驗證邏輯本身，是「載入哪些規則檔案」這個決定目前是
三處handler裡的常數，不是可設定的東西。

同時確認：`data/rules/central_max_range_local_mapping.json`兩筆
UNMAPPED紀錄與`docs/phase8a/competition_checklist.md`第641-643行**已
明文記錄**「本輪範圍：僅數位化＋建立資料模型與資料集結構驗證器。
**尚未實作**『地方基準表 vs 中央上限』之交叉驗證邏輯……留待後續輪次」
——這正是使用者Point B（中央/地方資料整合）的既有、已規劃但延後的
下一步，非本輪憑空新增的想法。

---

## 7. Hardcoded Dependencies

| 類別 | 是否hardcoded | 位置 |
|---|---|---|
| 商業用地（regional_rules.json/individual_rules.json之唯一land_use_type） | ✅是 | `data/rules/regional_rules.json`/`individual_rules.json`本身資料範圍，非程式邏輯寫死 |
| 金山區（city/district） | ✅是（同上，資料範圍層級） | 同上兩檔案`city`/`district`欄位皆固定為新北市/金山區 |
| RuleEngine載入哪些檔案 | ✅是（程式碼層級） | `backend/handlers/{analyze,complete_form,review}.py`各自的`reg + ind`載入邏輯，三處各自獨立寫死 |
| Factor names / grade conditions / adjustment matrices | ❌否，非程式碼寫死 | 完全資料驅動（`data/rules/*.json`），`RuleEngine`本身是泛用deterministic查表引擎，不含任何特定因素邏輯 |
| Land use subtype（住宅/商業/工業/農業/其他） | 部分——`central_max_range_validator.py`的`VALID_LAND_USE_TYPES`常數硬編碼5個合法用地類別字串（用於資料集結構驗證，非規則判定邏輯） | `engine/central_max_range_validator.py:34` |
| 金山座標/Golden Case數值 | ✅是，但**僅限於資料取得層（Providers）之Mock/示範值**，與RuleEngine/GradeEngine完全無關 | `providers/{commercial_activity,land_price,official_facility,osm_facility_lookup,public_facility,road,special_facility,transportation}_provider.py`、`engine/land_use_ratio_engine.py`（容積率Layer 2目前唯一1筆為jinshan，前次對話已確認之獨立議題，與本輪評價基準表無關） |

**釐清**：GradeEngine/AdjustmentEngine/RuleEngine三者本身**完全不含**
任何「金山」「商業」「特定因素名稱」的程式碼邏輯寫死——所有這類特定性
都在`data/rules/*.json`資料檔案裡，這正是為什麼第2節判定這三個引擎
「可完全重用」。真正的hardcoded問題是**呼叫端（三個handler）決定讀
哪個檔案**這件事，而非引擎本身。

---

## 8. Recommended Rule Data Classification

依使用者提供的四分類：

| 資料 | 分類 | 理由 |
|---|---|---|
| 附件一~四.doc + 1040130個別因素.doc | **CENTRAL_MAXIMUM** | 提供land use type + factor + 最大調整幅度（含「-」不予考慮語意）+ applicability，**不含**grade condition細節或explicit adjustment matrix，與`data/rules/central_max_adjustment_range.json`同性質、高度重疊（第5節） |
| `regional_rate_calculation.json` | **FIXTURE / Regression Data / Cross-check Data**（使用者原判斷正確，本輪確認） | 檔案本身`status: "incomplete"`, `calculated: 0`, 28筆全數`status: "comparable_missing"`——連teammate自己都還沒把「比較標的」那一半填完，是一份「待補worked example」，並非可直接餵給RuleEngine的規則庫；其`step_rate_pct`欄位由`max_abs_rate_pct/(grade_count-1)`公式產生，屬teammate自行推算之**衍生值**，非官方明細表直接記載，不得直接視為authoritative |
| `commercial_land_standard_schema.json` | **未知（檔案缺失）** | 被`regional_rate_calculation.json`的`valuation_source.schema_reference`引用，但本輪掃描`docs/incoming_rule_sources/`確認實體檔案不存在（見第16節） |
| `config.json` | **未知（檔案缺失）** | 使用者訊息提及「可能包含」，本輪掃描確認目錄中不存在此檔案 |

---

## 9. Recommended Import Architecture

**不新建第二套計算引擎**，僅補齊第6節指出的「載入哪些規則檔案」這個
缺口，最小化改動範圍：

```
Competition Evaluation Standard（PDF/已知格式）
        ↓
Importer（已存在：factors.csv/bands.csv/matrix.csv →
         engine/rule_table_ingest.py → engine/rule_table_validator.py）
        ↓（本輪需新增：一個「今天要用哪些規則檔案」的可設定清單，
         而非三處handler各自的常數 reg+ind）
Human Confirmation（ERROR一律擋下不輸出；WARNING列出但不擋，
         人工決定是否接受——此把關機制已存在於rule_table_validator.py，
         本輪只需在CLI/流程上明確要求「有ERROR就不能送出」）
        ↓
Case Rule（本輪需新增：某案件使用哪一份/哪幾份規則JSON的紀錄，
         使analyze.py/complete_form.py/review.py三處handler能一致地
         知道「這個案件該用哪份規則」，而非只有金山區這一份全域固定
         規則）
        ↓
Existing GradeEngine / AdjustmentEngine / FormCompletionEngine
         （完全不需修改）
```

---

## 10. AI Boundary

本專案既有程式碼**已經**在多處明文貫徹「AI不得直接判定grade/rank/
adjustment/legal結論」原則，本輪任務應**沿用**而非重新定義：

- `engine/form_classifier.py`docstring："never a bare LLM guess...
  it must never be the sole basis for a classification"
- `providers/document_extraction_provider.py`docstring："This module's
  classes never emit a grade（普通）, an adjustment rate（3.75%）, or a
  correctness verdict"
- `docs/phase8a/rule_table_ingestion_guide.md`："若現場能用Bedrock讀取
  PDF表格輔助草擬這三個CSV，人工只需快速核對修改……不呼叫任何AI/OCR
  自動讀PDF——這條路徑本身不依賴Bedrock"

**本輪對應套用**：若未來要接Bedrock輔助草擬factors.csv/bands.csv/
matrix.csv（從PDF/`.doc`draft三份CSV），AI僅能做semantic
extraction／field mapping candidate／condition parsing
candidate／explanation，**輸出必須先過`RuleTableValidator`（ERROR
擋下）再經人工確認**才能進入`RuleEngine`——這個把關點**已經存在**
（`rule_table_validator.py`的ERROR/WARNING機制本身即是human
confirmation gate的前置條件），不需新建。AI絕不可以：直接輸出
grade、直接輸出adjustment rate、直接輸出比較價格、直接下法律結論。

---

## 11. Case-scoped Rule Strategy

**目前完全不支援**（第6節已確認）。建議最小實作：

1. Case記錄（`case_store`既有機制）新增一個欄位，記錄該案件實際使用
   的規則JSON檔案路徑（或版本標籤），預設值指向現有
   `regional_rules.json`/`individual_rules.json`（確保**既有Golden
   Case行為零改動**——這是達成「不修改production行為除非必要」的
   關鍵設計）。
2. 三處handler（`analyze.py`/`complete_form.py`/`review.py`）改為
   讀取這個per-case設定，而非寫死的`reg + ind`兩個檔案——**只改
   「讀哪個檔案」這一行，不改RuleEngine建構方式或呼叫介面**。
3. 決賽現場流程：`ingest_rule_table.py`產生新JSON → 人工核對 →
   透過新增的case-scoped設定，把該案件指向新JSON（可以是「取代」
   或「疊加」既有金山商業用地規則，取決於決賽當天新表格是否為
   全新用地類別——若是全新land_use_type如住宅/工業/農業，用「疊加」
   更安全，不影響既有商業用地資料）。

---

## 12. Minimum Implementation Plan

**P0（本輪任務A的唯一真正缺口）**：
1. 為`analyze.py`/`complete_form.py`/`review.py`新增一個共用的
   「取得本案件應使用之規則列表」函式（取代三處各自獨立的
   `reg + ind`），預設行為與現況完全一致。
2. Case-scoped規則檔案路徑之最小資料模型與儲存（見第11節）。

**P1（任務B，中央/地方整合，呼應
`docs/phase8a/competition_checklist.md`已標記的延後項目）**：
3. 撰寫一個**跨檔案交叉驗證器**（新模組，例如
   `engine/central_local_rule_cross_validator.py`，**不是**
   NewGradeEngine/NewCalculator——純粹是「local矩陣max是否超過
   central上限」的比對邏輯），把`central_max_range_local_mapping.json`
   兩筆UNMAPPED**在人工確認對應子級別後**改為MAPPED，據此檢查
   `regional_rules.json`/`individual_rules.json`裡商業用地各因素的
   `max_adjustment`是否未超過`central_max_adjustment_range.json`
   對應儲存格的`max_range_pct`——**這正是Point B的核心價值**：確保
   地方矩陣沒有意外超過中央法定上限，而非重新輸入一次中央數字。
4. （選用，QA性質）用附件一~四.doc的antiword解析結果，逐格比對
   `central_max_adjustment_range.json`既有418筆，交叉驗證上一輪
   人工圖像轉錄有無錯誤——第5節已示範方法，可寫成一次性驗證腳本，
   非長期維護的production程式碼。

**明確不做**：把`regional_rate_calculation.json`／附件.doc直接轉成
新的`data/rules/*.json`規則檔——因為(a)附件.doc數字與既有
`central_max_adjustment_range.json`重複（第5節），(b)
`regional_rate_calculation.json`本身`status=incomplete`且其
`step_rate_pct`為teammate自行推算非官方明細表直接數字，不符合本專案
「規則須可追溯至官方原始頁碼」之既有原則（見既有`regional_rules.json`
每筆皆有`source_document`/`source_page`/`source_note`）。

---

## 13. Files To Add

- `engine/central_local_rule_cross_validator.py`（P1，第12節第3點）
- `data/rules/case_rule_bindings.json` 或等效之case-scoped規則設定
  儲存（P0，具體形狀待與`case_store`既有介面設計對齊，本輪僅為
  read-only audit，不在此階段決定最終schema）
- （選用）`scripts/cross_check_central_max_range_doc_sources.py`
  ——一次性QA腳本，antiword解析`docs/incoming_rule_sources/*.doc`
  並比對`central_max_adjustment_range.json`（第12節第4點）

## 14. Files To Modify

- `backend/handlers/analyze.py`（第34行`RuleEngine(reg + ind)`附近）
- `backend/handlers/complete_form.py`（第30行同上）
- `backend/handlers/review.py`（第85行同上）
- `data/rules/central_max_range_local_mapping.json`（P1，UNMAPPED→
  MAPPED，**須人工確認**子級別對應，非程式自動判定）

## 15. Files NOT To Modify

- `engine/rule_engine.py`／`engine/grade_engine.py`／
  `engine/adjustment_engine.py`／`engine/form_completion_engine.py`
  （第2節已確認完全可重用，改動風險/效益比不划算）
- `engine/rule_table_ingest.py`／`engine/rule_table_validator.py`
  （已存在、已測試、已文件化，任務A不需重寫這兩者本身）
- `data/rules/regional_rules.json`／`individual_rules.json`（Golden
  Case既有frozen資料，第12節已說明不應把teammate資料直接寫入覆蓋）
- `data/rules/central_max_adjustment_range.json`（既有418筆已驗證
  資料，非本輪需要重新數位化的對象）
- GIS／S3 Bootstrap／Cadastral Pipeline相關全部檔案（依使用者本輪
  明確指示，除非regression發現問題）

## 16. Risks

1. **`config.json`／`commercial_land_standard_schema.json`實體缺失**：
   使用者訊息列出的7份teammate檔案中，這2份在
   `docs/incoming_rule_sources/`實際掃描**不存在**，僅
   `regional_rate_calculation.json`內部以`schema_reference`欄位
   引用其中一份的檔名。若這兩份檔案之後才會補上，本稽核之
   「Teammate New Coverage」（第4節）判斷可能需要在補齊後重新檢視。
2. **`regional_rate_calculation.json`裡6項商業用地細項來源未明**
   （百貨公司/金融機構/娛樂設施/展覽旅館/客流量/店面連續性）——
   本輪比對附件二.doc本身**未見**這6項，也不在
   `central_max_adjustment_range.json`裡，來源可能是teammate另外
   參考的其他文件（例如查估作業手冊或地方自訂細項），**建議先向
   teammate確認這6項的官方依據**，再決定是否／如何納入
   `regional_rules.json`。
3. **`step_rate_pct`公式風險**（已於第3/8節詳述）：若未經查證直接
   把`regional_rate_calculation.json`的`step_rate_pct`當成新因素的
   官方矩陣依據，可能產生**表面上通過`RuleTableValidator`結構檢查、
   但數值本身並非官方真實矩陣**的規則——驗證器只檢查「矩陣自洽」
   （方陣、max一致、對角線為0等），**不檢查**「矩陣數值是否真的
   來自官方文件」，這一步仍需人工核對官方明細表原文。
4. **`.doc`格式本身的長期可維護性**：`antiword`為本機環境臨時發現
   可用之工具，非本專案既有相依套件（`backend/requirements*.txt`
   未見antiword或python-docx以外之.doc解析套件）。若P1的QA交叉比對
   腳本要長期維護，建議改用`python-docx`可讀之`.docx`格式（若
   teammate能另存新格式），或明確把`antiword`列為開發環境相依。
5. **Case-scoped規則儲存設計尚未定案**（第11/13節），本輪僅完成
   read-only audit，實際schema需另一輪明確設計並徵得同意後才能實作
   （避免與`case_store`既有介面衝突）。

---

## 最終總結

```
CURRENT_DYNAMIC_RULE_IMPORT=NO
```
（轉換＋驗證邏輯已存在且已測試，但案件審查流程三處handler目前寫死
只讀取金山商業用地兩個檔案，尚未有機制讓新匯入的規則實際被使用——
第6節）

```
CURRENT_RULE_SCHEMA_REUSABLE=YES
GRADE_ENGINE_REUSABLE=YES
ADJUSTMENT_ENGINE_REUSABLE=YES
FORM_COMPLETION_REUSABLE=YES

TEAMMATE_ADDS_NEW_RULE_DATA=YES
```
（住宅/工業/農業用地之官方定義說明文字＋`regional_rate_calculation.
json`裡6項商業用地特有細項，第4節）
```
TEAMMATE_ADDS_NEW_LAND_USE_COVERAGE=YES
```
（現有`regional_rules.json`/`individual_rules.json`僅涵蓋商業用地；
teammate的附件一/三/四對應住宅/工業/農業用地——但僅止於「中央上限
數字」層級，非LOCAL grade condition/matrix，且與既有
`central_max_adjustment_range.json`高度重疊，第4/5節）
```
REGIONAL_RATE_JSON_IS_RUNTIME_RULE=NO
REGIONAL_RATE_JSON_IS_FIXTURE=YES

CASE_SCOPED_RULE_SUPPORT=NO
PDF_EXTRACTION_REUSABLE=NO
```
（`engine/form_classifier.py`/`providers/document_extraction_
provider.py`之PDF擷取專為**已提交案件書表**（表1/表4/表5-2）設計，
與**評價基準明細表**這種完全不同版面/語意的來源文件無關，第14/
第1節「Q14」對應之現場稽核；`rule_table_ingest.py`本身刻意**不**
呼叫任何PDF/OCR，是CSV-based，這是既有、經過深思熟慮的設計選擇，
非缺陷）
```
SECOND_CALCULATOR_NEEDED=NO
AI_DIRECT_RANK_SAFE=NO

IMPORTER_IMPLEMENTATION_RECOMMENDED=YES
```
（非從零建置——`rule_table_ingest.py`/`rule_table_validator.py`已是
可用的Importer核心，僅需第9/12節之「Case Rule串接」補完最後一段）

```
P0_BLOCKERS=[
  "analyze.py/complete_form.py/review.py三處各自寫死RuleEngine(reg+ind)，
   無case-scoped規則檔案選擇機制（第6/11/12節）"
]
```
