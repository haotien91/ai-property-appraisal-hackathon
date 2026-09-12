# EVALUATION_STANDARD_IMPORTER_SURVEY

**日期**：2026-09-10
**輸入檔案**：`data/sources/competition/評價基準明細表範例.pdf`
**性質**：STEP 3A §1 READ-ONLY SURVEY — 本文件記錄對該PDF實際結構的
逐步探查結果（使用PyMuPDF，本專案既有相依套件，未使用OCR/AI），以及
據此設計之deterministic解析演算法的實測驗證結果。

---

## 摘要結論

```
PDF_TEXT_LAYER_USABLE=YES
TABLE_STRUCTURE_DETERMINISTIC=YES
REGIONAL_TABLE_DETECTED=YES
INDIVIDUAL_TABLE_DETECTED=YES
MATRIX_STRUCTURE_DETECTED=YES
```

---

## 1. Page Count / 基本結構

- **9頁**（`pymupdf.open(...).page_count == 9`）。
- 每頁皆有可提取文字層（`page.get_text()`非空，長度965~1500字元/頁），
  **無**garbled字型問題（不像`central_max_adjustment_range.json`來源PDF
  那樣因ToUnicode對照表缺失而產生亂碼）——這是一份文字層乾淨、可
  deterministic解析的PDF。

## 2. Regional / Individual 兩張表之偵測

透過頁面標題文字（每頁重複出現）以regex擷取：

```
regex: (?P<city>\S+?市)(?P<district>\S+?區)(?P<land_use_type>\S+?用地)
       影響地價(?P<scope_zh>區域|個別)因素評價基準明細表
```

結果：**第0-4頁**標題含「...影響地價**區域**因素評價基準明細表」
（REGIONAL，5頁，28個因素區塊）；**第5-8頁**標題含「...影響地價
**個別**因素評價基準明細表」（INDIVIDUAL，4頁，19個因素區塊）。
此regex在全部9頁上皆成功匹配（無NO_MATCH），city=新北市、
district=金山區、land_use_type=商業用地，與既有`data/rules/
regional_rules.json`/`individual_rules.json`之city/district/
land_use_type欄位完全一致——**確認此PDF與既有Golden Case digitized
資料同源**（見下方交叉比對）。

## 3. Table Layout — 重大發現：文字擷取順序非視覺閱讀順序

`page.get_text()`（plain text模式）逐行輸出的順序，**不是**表格視覺
上的「一行一行」閱讀順序，而是PDF content stream的繪製順序，具體
表現為：**每個因素的矩陣區塊會與該因素自己的分級標籤逐列交錯輸出**
（例如：第1列5個數字→第1列的等級標籤「優：30m以上」→第2列5個
數字→第2列標籤「稍優：...」→...），但**頁面備註句子（「以XX來衡量」）
與直書因素名稱**卻集中在該頁**所有**因素區塊處理完後、於文字流尾端
才一次性全部輸出，且彼此排列順序與各因素在頁面上的視覺順序**不一致**
（例如page1的6個備註句子輸出順序為[block5,block1,block0,block2,
block3,block4]，非[0,1,2,3,4,5]）。

**這代表**：
- 矩陣本身（數字+分級標籤）**可以**直接用文字流順序（stream order）
  deterministic解析——已對全部47個因素區塊實測驗證，100%正確（見
  第5節）。
- 但備註句子/直書因素名稱**不能**用文字流順序對應到正確的因素區塊，
  必須改用**幾何座標（bbox y座標）**才能正確對應——已改用
  `page.get_text("dict")`取得每行文字之bbox，並實測驗證
  `get_text("dict")`的行順序與`get_text()`plain text的行順序**逐行
  完全一致**（367行，0個不一致，兩頁面樣本皆驗證），故可用「同一份
  有序清單、額外攜帶座標」的方式同時滿足「文字流順序解析矩陣」與
  「y座標比對備註句子所屬區塊」兩個需求，不需要兩套獨立擷取再互相
  猜測比對。

**結論**：`TABLE_STRUCTURE_DETERMINISTIC=YES`——結構是可靠、可重現的
（同一份PDF每次解析結果完全相同），但**不是naive plain-text row-major
可以直接讀出來的**，需要專門的deterministic演算法（本輪已實作並驗證，
見`engine/evaluation_standard_importer.py`），這是本次Survey最重要的
技術發現。

## 4. Grade Labels / Grade Conditions

觀察到官方分級用語**完全對應**`engine/rule_table_validator.py`既有
`KNOWN_GRADE_VOCABULARIES`所收錄之「5級（一般優劣）：優/稍優/普通/
稍劣/劣」，另有2級（優/劣）與3級（優/普通/劣）之簡化版本，視因素性質
而定——與既有`regional_rules.json`/`individual_rules.json`的分級慣例
一致，無需新分級用語。

Grade condition文字型態，實測觀察到4種：
1. **純數值級距**：「30m以上」「20m以上未滿30m」「未滿6m或無」等——
   單位（m/m2/%/％/km）內嵌於文字中，需regex拆解，非獨立欄位。
2. **布林**：「有」「無」（僅2級因素，如「有無禁止建築」）。
3. **Sentinel + 遞增距離**（既有`RuleEngine`已支援之慣例）：優級為
   「區段內有」，其餘級距為遞增距離（如「未滿500m」「500m以上未滿
   1,000m」...）——對應`value_type=distance_positive`。
4. **複合OR條件**：「未滿10m或無」（單純「數值或無」，實測與既有
   `regional_rules.json`比對後確認等同於單純數值級距，「或無」僅為
   說明性文字，不影響lower/upper_bound）；「未滿10m或100m以上」
   （個別因素「寬度」的劣級，兩段不連續數值真正的複合OR條件，
   無法表達成單一[lower,upper)區間）——兩者已分別處理，見第6節。

## 5. Adjustment Matrix — Explicit Matrix偵測與交叉驗證

**全文件47個因素區塊，每個都帶有一份完整的NxN修正率矩陣**（N=2/3/5，
依該因素分級數而定），逐列輸出，矩陣本身**明確存在於PDF文字層**，
不需推算。已對全部47個區塊實測解析，矩陣皆呈現：
- 對角線=0
- 反對稱（`matrix[i][j] == -matrix[j][i]`）
- 每列（row）數值間距均等（等距，但**間距值本身逐因素不同**，並非
  統一套用`max/(grade_count-1)`公式後才符合——重要的是，這份PDF裡
  矩陣是**明確寫出來的**，不是靠公式重建的；本次解析器**直接照抄PDF
  裡寫的數字**，公式僅用於事後檢查`max_adjustment`宣告值是否與矩陣
  實際最大絕對值一致（見第6節`RULE_EXTRACTION_AMBIGUOUS`檢查），
  從未拿公式的結果取代PDF原文的矩陣數字。

**逐格黃金驗證**（`主要道路寬度`，regional table 第1頁第1個區塊）：
解析結果與`data/rules/regional_rules.json`裡既有的
`REG-MAIN_ROAD_WIDTH-*`五筆記錄，**grade_label、lower_bound、
upper_bound、unit、adjustment_matrix、max_adjustment 五個grade全部
逐格比對相符**（見`docs/audit/EVALUATION_STANDARD_IMPORTER_PHASE3A_
REPORT.md`§12），並以此候選規則直接建構一個真實`RuleEngine`實例，對
18m呼叫`grade()`，正確回傳「普通」（grade_code=3）——與Golden Case
已知結果一致。**這證實
`data/sources/competition/評價基準明細表範例.pdf`正是
`data/rules/regional_rules.json`/`individual_rules.json`當初人工
數位化時的原始來源文件**（同一份資料，只是這次改用程式解析而非
人工謄寫）。

## 6. Merged Cells / Repeated Headers / Unit Labels / Max Adjustment

- **Repeated Headers**：每頁重複出現「主要\n細\n項目\n項」（欄名，
  直書）與「新北市金山區商業用地影響地價XX因素評價基準明細表」
  （標題）——已用於偵測scope/city/district/land_use_type（第2節），
  非障礙。
- **Merged Cells**：PDF本身**沒有標記表格/儲存格結構的Tagged-PDF
  metadata**（純文字繪製，非結構化表格物件），「合併儲存格」的存在
  只能透過文字位置模式**推論**得出（例如：分類欄位如「土地使用管制」
  「交通運輸」顯然橫跨多個因素列，但PDF本身不提供明確的合併儲存格
  邊界資訊）。本輪範圍：**未**嘗試完整還原category（主要項目分類）
  欄位——`candidate_to_rule_records()`輸出的`category`欄位保留為
  `None`（`RuleTableValidator`僅檢查該欄位「存在」，不檢查非null，
  故不影響結構驗證），誠實標記為未實作，而非猜測。
- **Unit Labels**：無獨立「單位」欄位，單位**內嵌**於grade condition
  文字中（如「30m以上」「93m2以上」「240%以上」），已用regex
  deterministic拆解（`M`/`M2`/`KM`/`%`四種，對應既有schema慣例）。
  發現一處疑似原始資料抄錄異常：「普通：200km以上未滿400m」（單位
  前後不一致，km vs m）——已標記`anomaly_flag=SUSPECTED_UNIT_TYPO
  (km_vs_m)`，**未**自行「修正」為200m（無官方依據不得臆測，比照
  `rule_schema.json`既有`anomaly_flag`欄位「m/km誤植」註記慣例
  處理）。
- **Maximum Adjustment欄位**：每個因素區塊確實有一個獨立的
  「最大修正率」數值（本文件通稱max_adjustment），**明確存在於PDF
  文字層**，但其在文字流中出現的位置有一個需要特別處理的規律：
  - 3級以上因素：該數值緊接在「倒數第二列的分級標籤」之後、
    「最後一列的矩陣數值」之前出現（已於47個區塊中一致驗證）。
  - 2級因素：該數值出現在整個區塊的最後（最後一列標籤之後）。
  已實作對應的deterministic狀態機解析（非猜測位置，而是依「已收集
  幾列」動態判斷），對全部47區塊解析成功，且每一筆解析出的
  `max_adjustment`皆與該因素矩陣本身的實際最大絕對值一致（見第5節
  黃金驗證）。

## 7. 因素名稱（Factor Name）解析：主要限制

除了矩陣本身，另需要解析出「這個矩陣屬於哪個因素」（例如「主要道路
寬度」）。實測發現有兩種可能的因素名稱來源，皆存在於PDF文字層：

1. **備註句子**（如「以區段內主要道路寬度來衡量」）——透過y座標
   幾何比對可正確歸屬到對應區塊（見第3節），且能透過**exact
   substring比對**既有`regional_rules.json`/`individual_rules.json`
   的`factor`欄位清單，找出canonical factor name。
2. **直書因素名稱欄**（例如「主」「要」「道」「路」「寬」「度」
   逐字直書）——本輪**未**實作其幾何欄位重建（涉及「多欄直書、
   欄序右到左」的額外幾何規則），保留為未來可選強化項，不影響本輪
   deterministic-first範圍的核心正確性（見PHASE3A報告§13 Remaining
   Gaps）。

**實測結果（47個候選中）**：19個（40%）透過備註句子exact substring
比對成功解析出canonical_factor_id；28個（60%）因為PDF備註句子的
用字與既有digitized資料的`factor`欄位**存在真實的文字差異**（例如
「臨路情形」vs備註句「臨街情況」；「都市計畫（內、外）」vs備註句
「都市計畫內外」；「接近學校**之**程度」vs備註句「接近學校程度」少了
「之」字）而無法exact match，被誠實地標記為`UNKNOWN_FACTOR`/
`AMBIGUOUS`，**未**使用模糊比對或AI猜測解決——這是本次Survey確認的
`resolve_canonical_factor()`函式之「1. exact match」政策下的**預期
且正確**行為，非bug（詳見PHASE3A報告第10節之測試與第13節之
Remaining Gaps討論）。

## 8. 結論：本輪Importer演算法設計依據

基於以上發現，`engine/evaluation_standard_importer.py`的deterministic
演算法設計為：
1. 用標題regex偵測每頁的scope/city/district/land_use_type。
2. 用「比凖地/(比較標的)/宗地/(比準地)」四行header偵測每個因素區塊
   的起點，並用文字流順序的狀態機解析矩陣列、分級標籤、
   max_adjustment（含2級/3級以上兩種位置規律）。
3. 用`page.get_text("dict")`的bbox y座標，將每個區塊與其正確的備註
   句子配對（同一有序清單，不需二次文字比對）。
4. 用exact substring（最長匹配優先，平手時拒絕猜測）比對備註句子
   與既有canonical factor清單。
5. 對grade condition文字進行deterministic分類（boolean/numeric_range
   /distance_positive/categorical），複合OR條件與單位不一致等異常
   一律標記而非臆測。

完整實作、測試與驗證結果見
`docs/audit/EVALUATION_STANDARD_IMPORTER_PHASE3A_REPORT.md`。
