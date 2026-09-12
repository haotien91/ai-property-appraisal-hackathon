# SHULIN-RULE-SOURCE-TRUTH-GATE-A1

READ-ONLY 稽核。本輪未建立任何 production rule JSON，未修改任何 production
code／core engine，未寫入 `data/sources/competition/shulin_residential_2026/`。

全部矩陣本輪皆以 220 DPI 頁面圖像**逐格人工目視**核對（非文字層抽取），
針對 `題目.pdf` page 6 額外以 400 DPI 局部裁圖核對欄位對齊。

```
RULE_SOURCE_SHA256:
  題目.pdf = 11702e63ddb40ca9390cbab149e7b549e292fa09e834d43119a56043da15f16d
  評價基準明細表.pdf = a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c
```

---

## TASK 1/2/3 — SHULIN_REGIONAL_RULE_SOURCE_TRUTH（29/29，逐格視覺核對）

固定：`REGIONAL_FACTOR_COUNT=29`、`REGIONAL_MATRIX_RECONCILED_COUNT=29`。
每個矩陣皆為完整 n×n（n=grade_count），對角線=0，正負號互為鏡射，與
評價基準明細表.pdf 原表完全一致（無任何等距外推補值）。

### 土地使用管制（p.1）

| factor_id | label | grade | 全矩陣 (row=基準,col=目標區段；值=修正率) |
|---|---|---|---|
| zoning_inside_outside | 都市計畫內外 | 2 | 優{優:0,劣:+20} 劣{優:-20,劣:0} |
| land_use_zone | 使用分區(使用地類別) | 5 | interval 5：優{0,+5,+10,+15,+20} 稍優{-5,0,+5,+10,+15} 普通{-10,-5,0,+5,+10} 稍劣{-15,-10,-5,0,+5} 劣{-20,-15,-10,-5,0}；優=商業區/捷運用地(聯開)，稍優=住宅區/市場用地，普通=甲建/乙建/特定專用區/多目標使用之其他公共設施用地，稍劣=工業區/丙建/丁建，劣=其他可建築用地 |
| building_coverage_ratio | 建蔽率 | 5 | interval 2.5：優{0,+2.5,+5,+7.5,+10}…劣{-10,-7.5,-5,-2.5,0}；優≥80%，稍優70-80%，普通60-70%，稍劣50-60%，劣<50% |
| floor_area_ratio | 容積率 | 5 | interval 6.25：優{0,+6.25,+12.5,+18.75,+25}…劣{-25,-18.75,-12.5,-6.25,0}；優≥460%，稍優360-460%，普通260-360%，稍劣180-260%，劣<180% |
| construction_prohibited | 有無禁止建築 | 2 | 優{0,+50} 劣{-50,0}；優=無禁止，劣=有禁止 |
| construction_restricted | 有無限制建築 | 3 | interval 25：優{0,+25,+50} 普通{-25,0,+25} 劣{-50,-25,0}；優=無限制，普通=部分限制(高度或面積)，劣=限制整體開發 |

### 交通運輸（p.2）

| factor_id | label | grade | interval | 門檻 |
|---|---|---|---|---|
| main_road_width | 主要道路寬度 | 5 | 3.75 | 優≥28m 稍優20-28 普通12-20 稍劣8-12 劣<8m |
| avg_road_width | 區段內道路平均寬度 | 5 | 3 | 優≥20m 稍優15-20 普通10-15 稍劣8-10 劣<8m |
| major_station_proximity | 接近大型車站之程度 | 5 | 2.5 | 優區段內或<500m 稍優500-1000 普通1000-1500 稍劣1500-2000 劣≥2000或無 |
| bus_stop_proximity | 站牌之接近程度或密集程度 | 5 | 1 | 優區段內或<200m 稍優200-400 普通400-600 稍劣600-800 劣≥800或無 |
| interchange_proximity | 接近交流道之有無及程度 | 5 | 1 | 優區段內或<1000m 稍優1000-2000 普通2000-3000 稍劣3000-4000 劣≥4000或無 |
| road_development_level | 區段內道路規劃及闢建程度 | 5 | 2.5 | 優全部規劃闢建 稍優大部分 普通部分 稍劣砂石路 劣全無 |

### 自然條件 + 土地改良（p.3）

| factor_id | label | grade | interval | 門檻 |
|---|---|---|---|---|
| sunlight | 日照 | 5 | 2.5 | 優充分 稍優少許陰雨 普通部分陰雨 稍劣相當陰雨 劣大部分陰雨 |
| view | 景觀 | 5 | 1.25 | 優視野極寬廣景觀極優美 …劣視野景觀極差 |
| slope | 傾斜度 | 5 | 3.75 | 優<5度 稍優5-10 普通10-15 稍劣15-20 劣≥20度 |
| drainage_quality | 排水之良否 | 5 | 2.5 | 優極完善 稍優非常完善 普通普通完善 稍劣不良 劣極不良 |
| terrain | 地勢 | 5 | 2.5 | 優極平坦堅硬 稍優平坦地 普通緩傾斜地 稍劣低地濕地 劣地勢孤劣地 |
| land_improvement | 建築基地改良或其他改良 | 5 | 2.5 | 優四項以上 稍優三項 普通二項 稍劣一項 劣無（土地改良(4)類別，獨立於自然條件之外） |

### 公共建設（p.4，含本輪視覺重驗）

| factor_id | label | grade | interval | 門檻 |
|---|---|---|---|---|
| school_proximity | 接近學校之程度 | 5 | 2 | 優<300m 稍優300-500 普通500-800 稍劣800-1000 劣≥1000或無 |
| market_proximity | 接近市場之程度 | 5 | 2 | 同上門檻結構 |
| park_proximity | 接近公園廣場徒步區之程度 | 5 | 2 | 同上門檻結構 |
| tourism_proximity | 接近觀光遊憩設施之程度 | 5 | 1.5 | 優區段內或<500m 稍優500-1000 普通1000-1500 稍劣1500-2000 劣≥2000或無 |
| parking_convenience | 停車場地之便利程度 | 5 | 1.5 | **本輪視覺重驗**：優區段內有停車位或<200m 稍優200-400 普通400-600 稍劣600-1000 劣≥1000或無 |
| service_facility_proximity | 接近服務性設施的程度 | 5 | 1.5 | **本輪視覺重驗**：優區段內或<500m 稍優500-1000 普通1000-1500 稍劣1500-2000 劣≥2000或無 |

```
PARKING_RULE_VISUALLY_VERIFIED=YES（400 DPI視覺核對確認：門檻為200/400/600/1000m，上一輪因PDF文字抽取欄位重排誤判為"2000m以上或無"，本輪已用圖像修正）
SERVICE_FACILITY_RULE_VISUALLY_VERIFIED=YES（門檻為500/1000/1500/2000m，與上一輪文字推測結果一致，本輪以圖像確認無誤）
```

### 特殊設施 + 環境污染 + 其他影響因素（p.5）

| factor_id | label | grade | interval | 門檻 |
|---|---|---|---|---|
| utility_facility_proximity | 變電所或高壓鐵塔、瓦斯槽之有無及接近程度 | 5 | 2.5 | 優≥2000m或無 稍優1500-2000 普通1000-1500 稍劣500-1000 劣區段內或<500m |
| funeral_facility_proximity | 墓地、殯儀館、火葬場之有無及接近程度 | 5 | 2.5 | 同上門檻結構（方向：近=劣） |
| waste_facility_proximity | 垃圾場或掩埋場、焚化爐之有無及接近程度 | 5 | 3.75 | 優≥2000m或無 稍優1500-2000 普通1000-1500 稍劣500-1000 劣區段內或<500m |
| pollution_proximity | 環境污染(水/噪音/廢氣/廢棄物污染等)之有無及接近程度 | 5 | 5 | 同上門檻結構 |
| other_factors | 其他影響因素 | **7** | 3.33 | 極優{0,3.33,6.67,10,13.33,16.67,20} 優{-3.33,0,3.33,…,16.67} 稍優{-6.67,-3.33,0,…,13.33} 普通{-10,-6.67,-3.33,0,3.33,6.67,10} 稍劣{-13.33,-10,-6.67,-3.33,0,3.33,6.67} 劣{-16.67,…,-3.33,0,3.33} 極劣{-20,-16.67,-13.33,-10,-6.67,-3.33,0}（唯一7級因素，7x7矩陣全數視覺核對，對角線=0，正負鏡射一致） |

---

## TASK 4/5 — SHULIN_INDIVIDUAL_RULE_SOURCE_TRUTH（20項，逐格視覺核對）

```
INDIVIDUAL_FACTOR_COUNT=20
INDIVIDUAL_STANDARD_MATRIX_COUNT=19
INDIVIDUAL_SPECIAL_RULE_COUNT=1（容積率）
```

### 宗地條件（p.6）

| factor_id | label | grade | interval | 門檻／special_handling |
|---|---|---|---|---|
| land_area | 面積 | 5 | 2.5 | 優≥600m² 稍優400-600 普通200-400 稍劣50-200 劣<50m² |
| land_width | 寬度 | 5 | 1.25 | 優≥20m 稍優15-20 普通8-15 稍劣4-8 劣<4m |
| land_depth | 深度 | 5 | 1.25 | **非單調**：優14-30m 稍優30-40m 普通7-14m**或**40-50m 稍劣50-60m 劣<7m**或**≥60m（矩陣本身仍為標準5x5，但門檻函數非單調，見TASK10） |
| land_shape | 形狀 | 2 | 5 | 優方形/梯形 劣不規則形/長條形 |
| street_frontage | 臨路情形 | 5 | 2.5 | 優3面以上臨街 稍優路角地 普通雙面臨街 稍劣單面臨街 劣未臨街地 |
| land_terrain_individual | 地勢 | 2 | 10 | 優平坦 劣高亢或低窪（個別因素，與區域因素之地勢(5級)為不同因素） |

### 道路條件 + 接近條件（p.7-8）

| factor_id | label | grade | interval | 門檻 |
|---|---|---|---|---|
| road_type | 道路種類 | 5 | 1.25 | 優主要道路 稍優次要道路 普通巷道 稍劣農路 劣無 |
| frontage_road_width | 面前道路寬度 | 5 | 3 | 優≥20m 稍優12-20 普通8-12 稍劣5-8 劣<5m或無 |
| school_proximity_individual | 接近學校程度 | 5 | 1.25 | 優<250m 稍優250-500 普通500-1000 稍劣1000-2000 劣≥2000或無 |
| market_proximity_individual | 接近市場程度 | 5 | 1.25 | 同上門檻結構 |
| park_proximity_individual | 接近公園、廣場程度 | 5 | 1.25 | 同上門檻結構 |
| station_proximity_individual | 接近車站程度 | 5 | 1.25 | 同上門檻結構 |
| commercial_district_proximity | 接近商圈程度 | 5 | 2.5 | 優<250m 稍優250-500 普通500-1000 稍劣1000-2000 劣≥2000或無 |
| nuisance_facility | 嫌惡設施之有無 | 5 | 2 | **反向**：優≥2000m或無 稍優1000-2000 普通500-1000 稍劣200-500 劣<200m（越近越劣） |

### 周邊環境條件 + 行政條件 + 其他（p.8-9）

| factor_id | label | grade | interval | 門檻 |
|---|---|---|---|---|
| parking_convenience_individual | 停車方便性 | 3 | 2.5 | 優/普通/劣（無距離門檻，主觀評等） |
| zoning_designation | 使用分區或編定 | 5 | 3.75 | 優商業區/捷運用地(聯開) 稍優住宅區/市場用地 普通甲建/乙建/特定專用區/多目標使用之其他公共設施用地 稍劣工業區/丙建/丁建 劣其他可建築用地 |
| building_coverage_ratio_individual | 建蔽率 | 5 | 2.5 | 優≥80% 稍優70-80% 普通60-70% 稍劣50-60% 劣<50% |
| **floor_area_ratio_individual** | **容積率** | **無標準矩陣** | — | **視覺確認：儲存格完全空白（無任何優/稍優/普通/稍劣/劣分級文字，僅備註）**："1.容積率差異以土地開發分析法進行試算調整 2.本項需與區域因素容積率併同考量，調整不足者另於區域因素補充調整" |
| construction_restriction_individual | 有無禁限建 | 5 | 12.5 | 優無禁止或限制建築 稍優限制建築高度 普通限制建築高度及面積 稍劣限制整體開發 劣禁止建築 |
| dead_end_alley | 無尾巷 | 2 | 5 | 優無 劣無尾巷 |

```
INDIVIDUAL_FAR_IS_STANDARD_MATRIX=NO
INDIVIDUAL_FAR_POLICY=MANUAL_REVIEW_REQUIRED（正式來源要求「土地開發分析法」，本系統無此計算能力，且正式XLSX/PDF皆未提供完整可程式化公式，不得自行發明）
```

---

## TASK 6 — RULE_TO_TABLE51_MAPPING

全部29項regional factor對照 `表5影響地價區域因素分析明細表(住宅用地).xlsx`
之「表5-1區域因素明細表(住)」sheet（已於題目.pdf page5印刷版確認完整列序）：

| # | factor_id | Table5-1 對應 | 分類 |
|---|---|---|---|
| 1-6 | 土地使用管制6項 | 主要項目(1)逐列 | DIRECT_ROW（全部6項） |
| 7-12 | 交通運輸6項 | 主要項目(2)逐列 | DIRECT_ROW（全部6項） |
| 13-17 | 自然條件5項(日照/景觀/傾斜度/排水/地勢) | 主要項目(3)逐列 | DIRECT_ROW（全部5項） |
| 18 | 建築基地改良或其他改良 | 主要項目(4)土地改良，獨立單列 | DIRECT_ROW |
| 19-24 | 公共建設6項 | 主要項目(5)逐列 | DIRECT_ROW（全部6項） |
| 25 | 電業設施及公用氣體燃料設施 | 主要項目(6)特殊設施，列名"變電所或高壓鐵塔、瓦斯槽之有無及接近程度" | DIRECT_ROW（RENAME措辭） |
| 26 | 殯葬設施之有無及接近程度 | 主要項目(6)，列名"墓地、殯儀館、火葬場之有無及接近程度" | DIRECT_ROW，但**SPECIAL_HANDLING**：列名未提及納骨塔，見TASK7 |
| 27 | 廢棄物處理設施之有無及接近程度 | 主要項目(6)，列名"垃圾場或掩埋場、焚化爐之有無及接近程度" | DIRECT_ROW，但**SPECIAL_HANDLING**：列名未提及污水處理場，見TASK7 |
| 28 | 環境污染之有無及接近程度 | 主要項目(7)環境汙染，單列 | DIRECT_ROW |
| 29 | 其他影響因素 | 主要項目(8)，單列 | DIRECT_ROW，但**SPECIAL_HANDLING**：此列儲存格本身無任何優劣等級輸入格，「百分比小計」已由主辦單位預先固定填為「－ 無 0.00」（三個比較標的皆同）——即本案7級矩陣雖存在於評價基準明細表，但實際本案並不需要（也不能）對它進行分級判斷，直接視為0 |

無 AGGREGATED（表5-1沒有把多個regional factor合併成一列的情形）、
無 NOT_IN_TABLE51（評價基準明細表的29項regional factor全數在Table5-1找得到對應列）。

---

## TASK 7 — Table3 Subtype Aggregation Audit

逐一比對 `題目.pdf`（Table3實際勾選欄位結構）與評價基準明細表.pdf之regional
rule文字，不假設「聚合label未逐字列出subtype」等於「排除」：

```
FUNERAL_SUBTYPE_SCOPE=
  墓地=INCLUDED_BY_EXPLICIT_RULE
  殯儀館=INCLUDED_BY_EXPLICIT_RULE
  火葬場=INCLUDED_BY_EXPLICIT_RULE
  納骨塔=INCLUDED_BY_FORM_STRUCTURE_BUT_RULE_SCOPE_AMBIGUOUS
    （Table3將納骨塔與墓地/殯儀館/火葬場並列於同一"殯葬"群組提供欄位，
    但regional rule原文明確列舉"墓地、殯儀館、火葬場"三者，未提及納骨塔，
    未達"完全枚舉且包含"的確定程度，也未達"明確排除"的確定程度——誠實
    列為AMBIGUOUS，留待Phase A2向主辦單位或以Table4個別因素之"殯葬設施
    有無"欄位交叉確認，不得片面認定）

WASTE_SUBTYPE_SCOPE=
  垃圾場或掩埋場=INCLUDED_BY_EXPLICIT_RULE
  焚化爐=INCLUDED_BY_EXPLICIT_RULE
  污水處理場=INCLUDED_BY_FORM_STRUCTURE_BUT_RULE_SCOPE_AMBIGUOUS
    （同上理由：Table3提供污水處理場欄位於同一"廢棄物處理"群組，但
    regional rule原文僅列舉"垃圾場或掩埋場、焚化爐"，未提及污水處理場）

STATION_SUBTYPE_SCOPE=
  高鐵站=INCLUDED_BY_EXPLICIT_RULE
  火車站=INCLUDED_BY_EXPLICIT_RULE
  客運站=INCLUDED_BY_EXPLICIT_RULE
  捷運站=INCLUDED_BY_EXPLICIT_RULE
    （regional rule原文僅用"大型車站"整體類別名稱，未嘗試枚舉任何特定
    子類型——與殯葬/廢棄物設施「明確列出部分子項」的情形性質不同，此處
    視為涵蓋Table3底下"大型車站"群組的全部4個子項）

MARKET_SUBTYPE_SCOPE=
  傳統市場=INCLUDED_BY_EXPLICIT_RULE
  超級市場=INCLUDED_BY_EXPLICIT_RULE
  超大型購物中心=INCLUDED_BY_EXPLICIT_RULE
    （regional rule原文完整枚舉"傳統市場、超級市場、超大型購物中心"，
    與Table3的3個子項逐一對應，無遺漏）

PARK_SUBTYPE_SCOPE=
  里鄰公園=INCLUDED_BY_EXPLICIT_RULE
  一般公園=INCLUDED_BY_EXPLICIT_RULE
  廣場/徒步區=INCLUDED_BY_EXPLICIT_RULE
    （regional rule原文完整枚舉"里鄰公園、一般公園...廣場、徒步區"，
    與Table3的3個子項逐一對應，無遺漏）
```

---

## TASK 8/9 — Provenance Correction ＋ Exact Transaction Mapping

```
BLANK_XLSX_USED_AS_CASE_VALUE_SOURCE=NO
```

實際檢視 `表4比較法調查估價表.xlsx`（`表4比較法調查估價表` sheet，
D4:R20範圍）：確認所有「0基本資料」「土地正常單價」「交易日期」
「調整百分率」「調整至估價基準日單價」等VALUE儲存格**皆為空白**——僅有
列標籤與"M"單位符號，無任何宗地名稱/價格/日期數字。三個比較標的之全部
交易案件值只存在於`題目.pdf` page 6（已填版）。

以 400 DPI 對 `題目.pdf` page 6 頂部裁圖做**逐欄視覺對齊**核對（非文字線性
順序推測），發現上一輪 fixture 草案的P002/P003比較標的交易日期發生**欄位
誤植**（P002誤植為"111年9月14日"，實際應為"110年9月14日"）。本輪視覺
核對後之正確 mapping：

| | 比準地(P001-00) | 比較標的1(P002-00) | 比較標的2(P003-00) | 比較標的3(P004-00) |
|---|---|---|---|---|
| 宗地流水號/實例編號 | 0003 | 1 | 2 | 3 |
| parcel_id | 新北市樹林區樹德段1415地號 | 新北市樹林區樹德段284地號 | 新北市樹林區太平段367、917地號 | 新北市樹林區文林段317地號 |
| 土地正常單價 | (空白) | 130,167 | 135,275 | 170,909 |
| 交易日期 | 111年9月1日（＝估價基準日本身） | **110年9月14日** | 111年1月11日 | 110年10月29日 |
| 調整百分率(差異率) | (空白) | **5.96%** | 4.09% | 5.49% |
| 調整至估價基準日單價 | (空白) | **137,925** | 140,808 | 180,292 |

（皆 source_type=CompetitionProvided, source_file=題目.pdf, source_page=6；
比準地/區段欄另見page1-4各自Table3）

```
P002_TRANSACTION_MAPPING_VERIFIED=YES
P003_TRANSACTION_MAPPING_VERIFIED=YES
P004_TRANSACTION_MAPPING_VERIFIED=YES
```

---

## TASK 10 — Engine Compatibility Audit（讀真實程式碼，非假設）

以 `engine/rule_engine.py`（`RuleEngine._match_numeric`／`.adjustment()`）
與 `engine/rule_table_validator.py` 之實際邏輯核實：

```
ENGINE_SUPPORTS_2_GRADE=YES
ENGINE_SUPPORTS_3_GRADE=YES
ENGINE_SUPPORTS_5_GRADE=YES
ENGINE_SUPPORTS_7_GRADE=YES
```

證據：
- `RuleEngine.adjustment()` 之矩陣查找純為 `matrix[str(grade_code)][str(grade_code)]`
  的動態 dict 存取，從未硬編維度數字，天然支援任意 N。
- `RuleTableValidator._check_matrix_square()` 以 `band_codes`（該因素實際
  擁有的grade_code集合）動態比對矩陣列/欄鍵值是否一致，同樣不限定N。
- `RuleTableValidator.KNOWN_GRADE_VOCABULARIES`（line 56-64）**明確預先
  列出2/3/5(x3種用語)/7/9級**共7組合法用語清單，其中7級用語
  `{"極優","優","稍優","普通","稍劣","劣","極劣"}` 與樹林「其他影響因素」
  的7個等級文字**逐字相符**——這不是巧合推測，是既有程式碼本來就為此
  預留了結構空間。

```
ENGINE_SUPPORTS_NON_MONOTONIC_RANGE=NO
```

證據（真實 blocker，非臆測）：
- `RuleTableValidator._check_grade_code_uniqueness()`（line 172-181）
  對同一factor強制要求 grade_code **不得重複**——若要把深度"普通"的
  兩個不連續區間（7-14m 與 40-50m）表示為兩筆各自獨立的numeric_range
  規則列，兩者都必須標記grade_code=3(普通)，會被此檢查直接判定ERROR。
- `RuleEngine._match_numeric()` 本身雖然只是線性掃描比對，不會因兩個
  disjoint range而壞掉，但rule schema（`REQUIRED_FIELDS`僅有單一
  `lower_bound`/`upper_bound`欄位，非list）從結構上就不允許「一個
  grade對應多段不連續區間」。
- `_check_bound_contiguity()`（line 272-295）進一步假設同一factor的
  grade_code依序排列後bounds應首尾相接（單調遞增/遞減），非單調的深度
  規則會觸發此WARNING（非ERROR，但明確顯示現有邏輯是為單調情形設計的）。

**若需要 production code change**（本輪不修改，僅列 blocker）：
需擴充 rule schema 支援「一個grade_code對應多個(lower_bound,upper_bound)
區間」，並調整`RuleEngine._match_numeric()`改為對candidate中每個
(grade_code, range)子條目做比對而非「每factor每grade恰一列」的假設，
以及放寬`_check_grade_code_uniqueness()`使其允許同grade_code、不同range
的多筆列（但仍要防止真正重複/衝突）。

---

## TASK 11 — Competition Rule Fallback Risk（讀真實程式碼）

檢視 `backend/handlers/rule_engine_factory.py::build_rule_engine_for_case()`
實際邏輯（line 89-146）：

```python
if not case_id:
    return RuleEngine(reg_static + ind_static), ...STATIC_LOCAL...

packages = repository.list_packages(case_id)
confirmed = [p for p in packages if p.status == CONFIRMED]
if not confirmed:
    warnings = ["CASE_RULE_NOT_CONFIRMED"] if packages else []
    return RuleEngine(reg_static + ind_static), ...STATIC_LOCAL, warnings=warnings...
```

`reg_static + ind_static` 為 `data/rules/regional_rules.json` +
`individual_rules.json`（金山商業規則）。若樹林案件（`competition_profile=
shulin_residential_2026`）在尚未建立/CONFIRMED case-scoped rule package前
就被送去計算，此函式會**靜默**回傳金山商業規則的RuleEngine，僅在回傳的
`CaseRuleResolution.warnings`中留下一個字串"CASE_RULE_NOT_CONFIRMED"（
不是exception，不會擋下流程，呼叫端若沒有主動檢查這個warnings欄位就會
用錯誤規則算出數字）。

```
COMPETITION_CASE_CAN_FALLBACK_TO_JINSHAN_CURRENTLY=YES
```

列為 **Phase A2 blocker**。建議最小 fail-closed policy（本輪不實作）：

> 在 `CompetitionCase`（或呼叫`build_rule_engine_for_case`的handler層）
> 新增一個 `rule_profile_id` 標記（例如 `"shulin_residential_2026"` vs
> `None`/`"legacy"`）。當 `rule_profile_id` 不是legacy/None時，
> `build_rule_engine_for_case()`在`if not confirmed:`分支必須改為對這類
> case **拋出**明確的 `RULE_PROFILE_NOT_READY` 例外（或回傳一個
> `resolution_status="RULE_PROFILE_NOT_READY"`且呼叫端強制檢查並中止），
> 絕不可回退到`reg_static+ind_static`。既有金山（legacy，
> `rule_profile_id=None`）案件之現行靜態fallback行為**完全保留不動**，
> 此為新增的「profile感知」分支，非取代既有行為。

---

## FINAL REPORT

```
REGIONAL_FACTOR_COUNT=29
REGIONAL_MATRIX_RECONCILED_COUNT=29

INDIVIDUAL_FACTOR_COUNT=20
INDIVIDUAL_STANDARD_MATRIX_COUNT=19
INDIVIDUAL_SPECIAL_RULE_COUNT=1

PARKING_RULE_VISUALLY_VERIFIED=YES
SERVICE_FACILITY_RULE_VISUALLY_VERIFIED=YES

FUNERAL_SUBTYPE_SCOPE=墓地/殯儀館/火葬場=INCLUDED_BY_EXPLICIT_RULE；納骨塔=INCLUDED_BY_FORM_STRUCTURE_BUT_RULE_SCOPE_AMBIGUOUS
WASTE_SUBTYPE_SCOPE=垃圾場或掩埋場/焚化爐=INCLUDED_BY_EXPLICIT_RULE；污水處理場=INCLUDED_BY_FORM_STRUCTURE_BUT_RULE_SCOPE_AMBIGUOUS
STATION_SUBTYPE_SCOPE=高鐵站/火車站/客運站/捷運站=INCLUDED_BY_EXPLICIT_RULE（全部4項，rule原文為整體類別名稱）
MARKET_SUBTYPE_SCOPE=傳統市場/超級市場/超大型購物中心=INCLUDED_BY_EXPLICIT_RULE（全部3項，完整枚舉）
PARK_SUBTYPE_SCOPE=里鄰公園/一般公園/廣場徒步區=INCLUDED_BY_EXPLICIT_RULE（全部3項，完整枚舉）

BLANK_XLSX_USED_AS_CASE_VALUE_SOURCE=NO

P002_TRANSACTION_MAPPING_VERIFIED=YES
P003_TRANSACTION_MAPPING_VERIFIED=YES
P004_TRANSACTION_MAPPING_VERIFIED=YES

ENGINE_SUPPORTS_2_GRADE=YES
ENGINE_SUPPORTS_3_GRADE=YES
ENGINE_SUPPORTS_5_GRADE=YES
ENGINE_SUPPORTS_7_GRADE=YES
ENGINE_SUPPORTS_NON_MONOTONIC_RANGE=NO

COMPETITION_CASE_CAN_FALLBACK_TO_JINSHAN_CURRENTLY=YES

INDIVIDUAL_FAR_IS_STANDARD_MATRIX=NO
INDIVIDUAL_FAR_POLICY=MANUAL_REVIEW_REQUIRED

PRODUCTION_CODE_MODIFIED=NO
CORE_ENGINE_MODIFIED=NO

SOURCE_TRUTH_BLOCKERS=
  1. ENGINE_SUPPORTS_NON_MONOTONIC_RANGE=NO — 深度(land_depth)個別因素之
     "普通=7-14m或40-50m"無法以現行rule schema（單一lower/upper_bound、
     grade_code不得重複）表示，需要schema擴充+RuleEngine._match_numeric
     +RuleTableValidator._check_grade_code_uniqueness三處協同修改，本輪
     不實作，留待Phase A2/B設計。
  2. COMPETITION_CASE_CAN_FALLBACK_TO_JINSHAN_CURRENTLY=YES —
     rule_engine_factory.py現行邏輯在無CONFIRMED case-scoped package時
     靜默回退金山商業規則，對樹林案件不安全，需新增profile感知的
     fail-closed分支（RULE_PROFILE_NOT_READY），本輪不實作。
  3. FUNERAL_SUBTYPE_SCOPE／WASTE_SUBTYPE_SCOPE各有1個子類型
     （納骨塔／污水處理場）之regional rule涵蓋範圍為AMBIGUOUS，需
     Phase A2向主辦單位確認或以其他正式文件佐證，不得自行認定。
  4. 其他影響因素(7級)在本案表5-1實際已被主辦單位固定為"－無0.00"，
     Phase A2建立rule pack時仍須完整收錄此7級矩陣（供schema完整性與
     未來其他案件使用），但本案運算路徑不需要、也不應該讓Grade/
     Adjustment Engine對它進行任何非零判斷。
  5. 表4「案號」印刷值仍含字面"XXX"占位符（1110901-99-XXX），非本輪
     可解決，需留待正式賽事確認。

RECOMMENDED_NEXT_PHASE=Phase A（Competition Rule Pack）——本輪已將29+20
项規則之grade/threshold/matrix完整、逐格視覺核對完成，且確認現有
RuleEngine/RuleTableValidator結構上已支援2/3/5/7級（僅深度之非單調
range需額外schema設計），可以開始草擬
data/rules/competition/shulin_residential_2026/{regional,individual}_rules.json
草稿（草稿階段，正式CONFIRMED前不影響任何既有case）。
```

## PASS CONDITION 檢核

1. 29/29 Regional factor 逐項來源與matrix完全reconciliation ✓（TASK1-3）
2. Individual factors 逐項reconciliation ✓（TASK4-5，20/20）
3. 疑似extraction錯位透過PDF visual解決 ✓（parking/service_facility
   已視覺重驗且結果與最終確認一致；P002/P003交易資料欄位誤植已透過
   400 DPI視覺對齊發現並修正）
4. 三比較標的交易資料逐欄visual mapping確認 ✓（TASK9，三者皆YES）
5. Blank XLSX不被錯當案件value source ✓（TASK8，NO）
6. 2/3/5/7-grade Engine compatibility被真實驗證 ✓（TASK10，讀取
   engine/rule_engine.py與rule_table_validator.py原始碼確認，非假設）
7. Jinshan fallback risk被明確識別 ✓（TASK11，YES，已列Phase A2 blocker
   並提出最小fail-closed policy草案，本輪未實作）

```
SHULIN_RULE_SOURCE_TRUTH_GATE_A1=PASS
```
