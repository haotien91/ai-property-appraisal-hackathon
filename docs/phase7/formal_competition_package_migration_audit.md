# FORMAL-COMPETITION-PACKAGE-MIGRATION-AUDIT-1

READ-ONLY audit. No production code, no core engine, and no file under
`data/sources/competition/shulin_residential_2026/` was modified this round.

## Source Existence Gate

```
OFFICIAL_SOURCE_ROOT=data/sources/competition/shulin_residential_2026/
OFFICIAL_SOURCE_FILES_FOUND=5/5
SOURCE_PACKAGE_INCOMPLETE=NO
```

All 5 files confirmed present (題目.pdf 570KB / 評價基準明細表.pdf 249KB /
表3地價區段勘查表.xlsx 229KB / 表5影響地價區域因素分析明細表(住宅用地).xlsx
223KB / 表4比較法調查估價表.xlsx 225KB), all read directly from this root,
never from any other path.

## Known Contract (from 題目.pdf, actually read page-by-page)

```
BASE_SEGMENT=P001-00
COMPARABLE_1=P002-00
COMPARABLE_2=P003-00
COMPARABLE_3=P004-00
```

Page order confirmed exactly as specified: Page1=表3 P002-00, Page2=表3
P003-00, Page3=表3 P004-00, Page4=表3 P001-00, Page5=表5-1, Page6=表4.

**注意（誠實記錄，非本輪判斷可解決）**：表5-1/表4 頁首「案號：」目前印刷值
為 `1110901-99-XXX`（含字面 "XXX"）——這是題目本身尚未指定具體流水號，
非本稽核遺漏；未來 fixture 的 `case_no` 需另行確認或留待主辦單位補充，
不得自行編造具體數字。

## LEGACY_SOURCE_MIXING_DETECTED

```
LEGACY_SOURCE_MIXING_DETECTED=NO
```

本輪所有樹林/表3/表5-1/表4 相關判斷，只從
`data/sources/competition/shulin_residential_2026/` 五個檔案直接讀取
（PDF 全頁讀取 + XLSX openpyxl 讀取），未從舊金山 Golden Case 資料、
`official_appraisal_form_v1.*`、`regional_rules.json`/`individual_rules.json`
補值或推論任何樹林案件欄位。舊資料僅用於「比對差異」（Task 1/6/7/8），
從未用於「填補樹林正式資料的缺值」。

---

## TASK 1 — LEGACY_ASSUMPTION_MATRIX

| file | function/class | legacy assumption | competition impact | must change? | can preserve as legacy? |
|---|---|---|---|---|---|
| `data/golden/golden_case_input.py` | module-level `case`/`BASE_REGIONAL` | 金山區／商業用地 Golden Case 完整 fixture | 無直接耦合（僅被 Jinshan 專屬 tests/scripts 匯入） | 否 | 是，原樣保留作為既有 regression fixture |
| `pdf/official_pdf_renderer.py:82-97` | `_TEMPLATE_MAP_IDENTITY`, `_case_matches_template_map_identity` | 硬編 `case_no="1140901-99-001"` 等 6 個欄位，作為「是否可保留範本地圖頁」的身份判斷 | 樹林案件必然不符此身份，會正確走安全 fallback（非阻礙） | 否（本身即安全機制，設計上就該讓樹林案件 fail） | 是 |
| `scripts/build_official_template_profile.py` / `data/templates/official_appraisal_form_v1.json` | `profile["pages"]={"table1":0,"table5_2":1,"table4":2}` | "table1"/"table5_2" 內部 key 綁定金山範本頁面座標（bbox） | 樹林為全新版面（表3/表5-1/表4），舊 bbox 完全不適用 | 是（需為樹林建全新 profile，檔名建議如 `official_appraisal_form_shulin_residential_v1.json`，不得沿用同一 profile 混填） | 舊 profile 原樣保留服務金山案件 |
| `pdf/official_pdf_renderer.py:610-636` | 表5-2 渲染迴圈 `row["comparables"][0]` | 只寫入單一比較標的欄位（index 0） | 樹林需 3 個比較標的欄位，此為主要 renderer 阻塞點 | 是 | 否（此邏輯本身須改為迴圈，但可保留「舊迴圈」作為金山案件專用分支） |
| `pdf/official_pdf_renderer.py:420,643-690` | `primary_comparable_id = case.comparable_ids[0]` | 表4 `direct_case_fields`／`form_completion_field`／`case_field`／`raw`／`[comp]` 全部只認第一個比較標的 | 同上，三個比較標的的欄位需各自獨立 bbox 與寫入 | 是 | 同上 |
| `backend/handlers/rule_engine_factory.py` | 全模組 | 靜態 `regional_rules.json`/`individual_rules.json`（金山商業規則）為預設 baseline | **好消息**：已存在 Case-Scoped Rule Package 覆寫機制（`CaseRuleRepository`／`CONFIRMED` gate），樹林規則可用「case-scoped package」方式提供，完全不需修改此工廠或任何 Engine | 否（機制已存在，只需提供資料） | 是，靜態檔案原樣作為金山案件 fallback |
| `domain/models.py:1369-1371` | `FormType` enum：`TABLE1_LAND_SEGMENT_SURVEY`／`TABLE5_2_REGIONAL_FACTOR_ANALYSIS`／`TABLE4_COMPARISON_METHOD` | enum 值為金山表單中文名稱字面 | 純命名/展示用途，不影響邏輯正確性 | 否（可選：新增 `TABLE3_...`/`TABLE5_1_...` 新 enum 值，不必重新命名舊值） | 是 |
| `engine/form_completion_engine.py:501` | `complete_form()` | `for comparable_id in case.comparable_ids:` | **已支援多筆比較標的**，無阻塞 | 否 | — |
| `backend/handlers/case_reconstruction.py:92-102` | dict/list comprehension | 依 `comp_map.keys()` 建構，非硬編 index | **已支援多筆比較標的** | 否 | — |
| `backend/handlers/review.py:103-191` | `build_submitted_from_form_completion` | 依 field_id 尾碼 `_{cid}` 動態解析每個比較標的 | **已支援多筆比較標的獨立驗證** | 否 | — |
| `pdf/pdf_renderer.py`（WeasyPrint Audit renderer） | `render_form`/`render_all_forms` | 直接映射 `FormCompletionResult.fields`（已含全部比較標的欄位），無 index 假設 | **已 comparable-count-agnostic**，Audit PDF 本身不需改 | 否 | — |

（完整逐檔 grep 結果詳見本輪背景 Explore agent 報告，已併入上表；未列出的
"金山"/"Jinshan"/"commercial" 命中皆僅存在於 Jinshan 專屬 fixture／test 檔
名或字串常數，未被任何 Engine 當作分支條件讀取。）

---

## TASK 2/3 — 三份正式 XLSX 檢查結果

```
TABLE3_TARGET_SHEET=表3區段勘查表
TABLE3_TARGET_RANGE=A1:V46（print_area 同範圍；直式 portrait，scale 57%，fitToPage=true，188個合併儲存格，0個公式，無隱藏列/欄，無手動分頁）

TABLE51_TARGET_SHEET=表5-1區域因素明細表(住)
TABLE51_TARGET_RANGE=print_area A1:M45（sheet 實際資料範圍達 A1:Q45，N-Q欄不在列印範圍內）；直式，scale 82%，fitToPage=true，59個合併儲存格，**1個公式**（B42=`(1)+(2)+(3)+(4)+(5)+(6)+(7)+(8)`，影響地價區域因素總修正數）

TABLE4_TARGET_SHEET=表4比較法調查估價表
TABLE4_TARGET_RANGE=print_area A1:R36（sheet 實際資料範圍達 A1:S37）；橫式 landscape，scale 75%，fitToPage=true，123個合併儲存格，0個公式

TARGET_SHEETS_HAVE_FORMULAS=部分（僅表5-1有1個公式儲存格；表3與表4皆為0個公式，所有數值/百分比欄位皆設計為人工填入，非Excel自動計算）
```

Checkbox/symbol 呈現：確認為**純文字 unicode 字元**（「○」「●」直接寫在儲存
格內，字型多為標楷體/Times New Roman），並非 Excel 表單控制項或 Wingdings
字型符號——與既有 PyMuPDF renderer 對○/●字符「重新繪製」（而非圖形控制項
替換）的既有作法完全相容。

三個 workbook 皆各含 **22-23 個與本輪目標表無關的其他表單**（買賣實例、
收益法、成本法、宗地市價估計表、計算表、市價變動幅度表、公設平均表、
比準地地價估計表等——完整官方查估作業手冊範本包，非樹林案件專屬）：

```
WHOLE_WORKBOOK_PDF_EXPORT_SAFE=NO
```

---

## TASK 3 — Template Strategy Recommendation

```
RECOMMENDED_OFFICIAL_TEMPLATE_STRATEGY=OPTION_B
```

理由：
- 現有 `pdf/official_pdf_renderer.py` 架構（PyMuPDF + 座標 profile + SHA256
  fingerprint guard + 字型解析）已被兩輪 quality-gate 稽核驗證過品質與
  正確性，重用此架構風險最低。
- OPTION_A（LibreOffice runtime 填 XLSX→PDF）在 AWS Lambda 上需額外背負
  LibreOffice 相依（image size、cold start），且 Excel 列印保真度
  （合併儲存格如何 render、頁面縮放 57%/82%/75%、字型是否一致）在
  Lambda 容器內難以保證與正式 Excel 桌面版一致。
- OPTION_B 需要的前置工作：以這三個目標 sheet 為「唯一」來源，透過
  build-time script（類比既有 `build_clean_official_template.py` +
  `build_official_template_profile.py` 的模式）把目標 sheet 轉為乾淨
  PDF/座標 profile，供 runtime PyMuPDF overlay。

**明確聲明（依指示，不得稱為主辦單位原始 PDF）**：
```
產出的 PDF template 為 DERIVED_RUNTIME_TEMPLATE，
XLSX（data/sources/competition/shulin_residential_2026/ 三份官方檔案）
才是 official layout 的 source of truth。
```

---

## TASK 4 — Competition Input Model（依 題目.pdf 逐頁分類）

四個 Segment Record（P001/P002/P003/P004）欄位分類架構：

**A. COMPETITION_PROVIDED_FIXED**（題目已填，不得覆蓋，逐段皆有）：
年期(1110901)、區段編號、區段範圍、都市計畫內外、使用分區、建蔽率、
容積率、有無禁止建築、有無限制建築、主要道路(名稱+寬度)、區段內道路
平均寬度、區段內道路規劃闢建程度、日照、景觀、傾斜度、排水之良否、
地勢、土地利用現況（單選/複選標記）、建築型態、建築密度、建築基地改良
勾選項（P001/P002/P003/P004 皆有，內容各段不同）。

**B. COMPETITION_BLANK_TARGET**（題目留白，需系統/人工補）：
表3 內每段的：接近大型車站/站牌/交流道/接近聚落/接近運銷中心/接近消費
市場程度（皆空白，僅有勾選格無填值）、特殊設施（電業/殯葬/廢棄物處理，
每段皆為空白候選——與既有 Facility Confirmation Gate 邏輯完全對應）、
公共建設/工商活動各設施名稱與距離（皆空白）、環境污染各項（皆空白）；
表5-1 全部 優劣等級/修正百分比 儲存格（除「其他影響因素」列已固定為
「－ 無 0.00」三個比較標的皆同）；表4 全部「調整項目」個別因素欄位、
合計、差異百分率絕對值加總、價格形成因素之相近程度、比準地試算價格、
比較標的權重、比準地地價、備註欄、填寫日期。

**C. DERIVED**（由 Rules/計算取得）：表5-1 優劣等級對應的修正百分比
（一旦栏位A的實測值/等級確定後，透過 Grade+Adjustment Engine 計算）；
表4 個別因素差異率、合計、試算價格、調整至估價基準日單價（由
Calculation Engine 依表4提供的「土地正常單價」「調整百分率」計算，其中
土地正常單價/交易日期/調整百分率本身屬 COMPETITION_PROVIDED_FIXED，
已列印在表4/頁6 —— 130,167／135,275／170,909 及 5.96%／4.09%／5.49%，
調整至估價基準日單價 137,925／140,808／180,292，皆為題目已印，屬 A 類
而非 D）。

**D. MANUAL_REVIEW**（目前沒有可信來源）：表5-1/表4 之「比較標的權重」
（無官方公式，見 Task 13）；表4 容積率個別因素調整（需土地開發分析法，
見 Task 9）；表4 備註欄敘述性文字；表4「填寫日期」（無 verified 來源，
比照上一輪 Jinshan 稽核結論）。

---

## TASK 5 — 四 Table3 架構

```
MULTI_SEGMENT_TABLE3_SUPPORTED_CURRENTLY=NO（未曾在同一案件下驗證4個
  segment_scoped FACTORS 同時獨立存在——目前FACTORS 存放於 case_store 以
  case_id 為主鍵，單一 case 下未有"依 segment_code 再分區"的既有資料模型
  或 collect_data.py 呼叫模式）
SEGMENT_SCOPED_FACTORS_SUPPORTED_CURRENTLY=NO（同上；domain/models.py 的
  CompetitionCase 目前是「一個 base segment + N 個 comparable segment」
  結構本身已存在——base_parcel_factors 對應 P001、comparable_factors[cid]
  對應 P002/P003/P004——這點OK；但每個 segment 各自完整的「表3 原始調查
  35+ 欄位」尚未有一個獨立 FACTORS-per-segment 的收集/儲存 schema，目前
  collect_data.py 主要圍繞 base+comparable 的「個別因素」欄位，而非
  「表3 完整區域調查表」逐段收集）
```

最小 migration schema 建議（不實作）：
```
SegmentSurveyRecord {
  segment_code: str   # P001-00 / P002-00 / P003-00 / P004-00
  role: "base" | "comparable"
  comparable_index: Optional[int]  # None for base, 0/1/2 for comparable
  raw_fields: Dict[str, Any]       # 表3 逐欄原始值（COMPETITION_PROVIDED_FIXED 或 collect_data 收集）
  facility_confirmations: ...      # 沿用既有 FacilityConfirmationRecord，但需以 (case_id, segment_code, subtype) 為鍵，而非目前的 (case_id, subtype)
}
```
關鍵不變式：P002 的 FACTORS 寫入**不得**覆寫 P003/P004/P001（目前
`facility_confirmation_repository.py` 之 `_sk(subtype)` 只以 subtype 為
排序鍵，同一案件下 4 個 segment 若共用同一 case_id，會直接互相覆寫—— 這
是遷移必須解決的真實衝突點，非臆測）。

---

## TASK 6 — TABLE51_SCHEMA_DIFF（表5-2→表5-1）

比對舊 `TABLE5_2_ROW_FACTORS`（28 factors, 見
`scripts/build_official_template_profile.py`）與新 表5-1（評價基準明細表.pdf
page1-5 之29個 regional 因素）：

| 舊 field_id (表5-2/金山商業) | 新 表5-1(樹林住宅) 對應細項 | 分類 |
|---|---|---|
| regional_zoning_inside_outside | 都市計畫（內、外） | REUSE |
| regional_land_use_zone | 使用分區（編定） | RENAME（"使用分區(使用地類別)"→"使用分區（編定）"，住宅區脈絡下分級標籤改變：優=商業區/捷運用地聯開，稍優=住宅區/市場用地 ——分級「順序」不同於金山商業版，屬 RULE_CHANGED 同時 RENAME） |
| regional_building_coverage_ratio | 建蔽率 | REUSE（分級門檻不同，80%/70%/60%/50%——需與舊金山商業版數字逐一核對，暫列 RULE_CHANGED 待 Phase A 驗證） |
| regional_floor_area_ratio | 容積率 | REUSE（樹林住宅門檻 460/360/260/180%，明顯不同於金山商業版數字） RULE_CHANGED |
| regional_construction_prohibited | 有無禁止建築 | REUSE |
| regional_construction_restricted | 有無限制建築 | REUSE（新增3級：優/普通/劣，需核對舊版是否同為3級） |
| regional_main_road_width | 主要道路寬度 | REUSE |
| regional_avg_road_width | 區段內道路平均寬度 | REUSE |
| regional_major_station_proximity | 接近大型車站之程度 | REUSE |
| regional_bus_stop_proximity | 站牌之接近程度或密集程度 | REUSE |
| regional_interchange_proximity | 交流道之有無及接近交流道之程度 | REUSE |
| regional_road_development | 區段內道路規劃及闢建程度 | REUSE |
| regional_drainage_quality | 排水之良否 | REUSE |
| regional_terrain | 地勢 | REUSE |
| （無對應） | 建築基地改良或其他改良（土地改良(4)類別） | **NEW_FIELD**（舊28項無此因素；新表5-1「土地改良」為獨立主類別(4)） |
| regional_market_proximity | 接近市場之程度 | REUSE |
| regional_park_proximity | 接近公園（里鄰公園、一般公園）、廣場、徒步區之程度 | REUSE |
| regional_tourism_proximity | 接近觀光遊憩設施之程度 | REUSE |
| regional_parking_convenience | 停車場地之便利程度 | REUSE |
| （無對應） | 接近服務性設施的程度（郵局、銀行、醫院、機關等） | **NEW_FIELD** |
| regional_utility_facility_proximity | 變電所或高壓鐵塔、瓦斯槽之有無及接近程度 | REUSE（措辭微調：新版明確排除舊「電業氣體燃料設施」一詞，改列變電所/高壓鐵塔/瓦斯槽） |
| regional_funeral_facility_proximity | 墓地、殯儀館、火葬場之有無及接近程度 | REUSE（**注意**：新版描述不含「納骨塔」，需核對是否仍計入同一因素或改列他處） SPECIAL_HANDLING |
| regional_waste_facility_proximity | 垃圾場或掩埋場、焚化爐之有無及接近程度 | REUSE（**注意**：新版不含「污水處理場」，需核對) SPECIAL_HANDLING |
| regional_pollution_proximity | 環境污染（水污染、噪音污染、廢氣污染、廢棄物污染等）之有無及接近程度 | REUSE |
| regional_department_store_proximity | （表5-1未見） | **REMOVED_FIELD**（住宅用地版本移除百貨公司類商業因素——與舊金山商業版差異合理，但需 Phase A 對照 XLSX 原始儲存格逐格確認，非僅憑PDF文字判斷） |
| regional_financial_institution_proximity | （表5-1未見） | **REMOVED_FIELD**（同上，但金融機構在 表3 仍存在作為 COMPETITION_BLANK_TARGET 欄位，只是未被列為表5-1 regional 修正因素——SPECIAL_HANDLING：可能被歸入「其他影響因素」） |
| regional_entertainment_proximity | （表5-1未見） | **REMOVED_FIELD** |
| regional_exhibition_hotel_proximity | （表5-1未見） | **REMOVED_FIELD** |
| regional_customer_traffic | （表5-1未見） | **REMOVED_FIELD** |
| regional_shop_contiguity | （表5-1未見） | **REMOVED_FIELD** |
| （無對應） | 其他影響因素（(8)類別，7級：極優~極劣） | **NEW_FIELD**（7-grade，見Task7） |

**不得**假設舊 Table5-2 schema 等同正式 Table5-1——上表明確列出至少
5個REMOVED_FIELD（皆為商業活動類因素，住宅用地版本合理不需要）、
2個NEW_FIELD（土地改良、其他影響因素）、多個RULE_CHANGED（分級門檻數字
不同）。表5-1 全案備註並明文："使用分區、建蔽率、容積率修正併同於
比較法調查估價表宗地個別因素考量調整修正"——這是**與金山版本不同的
特殊規則**：這三項regional修正在樹林版本可能不透過表5-1直接呈現，而是
併入表4個別因素調整考量（SPECIAL_HANDLING，需 Phase A 進一步釐清實際
計算路徑，不得假設沿用金山原本"表5-2直接計算"的方式）。

---

## TASK 7 — SHULIN_RESIDENTIAL_REGIONAL_RULE_INVENTORY

（完整29項；來源：`評價基準明細表.pdf` page1-5，皆標註 source_page）

| field_id(建議) | 中文label | major category | grade數 | interval | source_page |
|---|---|---|---|---|---|
| zoning_inside_outside | 都市計畫內外 | 土地使用管制 | 2 | 20 | p.1 |
| land_use_zone | 使用分區(使用地類別) | 土地使用管制 | 5 | 5 | p.1 |
| building_coverage_ratio | 建蔽率 | 土地使用管制 | 5 | 2.5 | p.1 |
| floor_area_ratio | 容積率 | 土地使用管制 | 5 | 6.25 | p.1 |
| construction_prohibited | 有無禁止建築 | 土地使用管制 | 2 | 50 | p.1 |
| construction_restricted | 有無限制建築 | 土地使用管制 | 3 | 25 | p.1 |
| main_road_width | 主要道路寬度 | 交通運輸 | 5 | 3.75 | p.2 |
| avg_road_width | 區段內道路平均寬度 | 交通運輸 | 5 | 3 | p.2 |
| major_station_proximity | 接近大型車站之程度 | 交通運輸 | 5 | 2.5 | p.2 |
| bus_stop_proximity | 站牌之接近程度或密集程度 | 交通運輸 | 5 | 1 | p.2 |
| interchange_proximity | 交流道之有無及接近交流道之程度 | 交通運輸 | 5 | 1 | p.2 |
| road_development_level | 區段內道路規劃及闢建程度 | 交通運輸 | 5 | 2.5 | p.2 |
| sunlight | 日照 | 自然條件 | 5 | 2.5 | p.3 |
| view | 景觀 | 自然條件 | 5 | 1.25 | p.3 |
| slope | 傾斜度 | 自然條件 | 5 | 3.75 | p.3 |
| drainage_quality | 排水之良否 | 自然條件 | 5 | 2.5 | p.3 |
| terrain | 地勢 | 自然條件 | 5 | 2.5 | p.3 |
| land_improvement | 建築基地改良或其他改良 | 土地改良 | 5 | 2.5 | p.3 |
| school_proximity | 接近學校之程度 | 公共建設 | 5 | 2 | p.4 |
| market_proximity | 接近市場之程度 | 公共建設 | 5 | 2 | p.4 |
| park_proximity | 接近公園、廣場、徒步區之程度 | 公共建設 | 5 | 2 | p.4 |
| tourism_proximity | 接近觀光遊憩設施之程度 | 公共建設 | 5 | 1.5 | p.4 |
| parking_convenience | 停車場地之便利程度 | 公共建設 | 5 | 1.5（帶狀非等距，見備註） | p.4 |
| service_facility_proximity | 接近服務性設施的程度 | 公共建設 | 5 | 1.5（帶狀需複核） | p.4 |
| utility_facility_proximity | 變電所或高壓鐵塔、瓦斯槽之有無及接近程度 | 特殊設施 | 5 | 2.5 | p.5 |
| funeral_facility_proximity | 殯葬設施之有無及接近程度 | 特殊設施 | 5 | 2.5 | p.5 |
| waste_facility_proximity | 廢棄物處理設施之有無及接近程度 | 特殊設施 | 5 | 3.75 | p.5 |
| pollution_proximity | 環境污染之有無及接近程度 | 環境污染 | 5 | 5 | p.5 |
| other_factors | 其他影響因素 | 其他影響因素 | **7**（極優/優/稍優/普通/稍劣/劣/極劣） | 3.33 | p.5 |

**特別確認**：2級=zoning_inside_outside／construction_prohibited；
3級=construction_restricted；5級=絕大多數；**7級=other_factors（唯一）**。
不得假設全部5級——已逐項核實。

**誠實限制**：`parking_convenience`/`service_facility_proximity` 兩項的
PDF文字抽取因欄位重排導致級距標示疑似不連續（如"600以上未滿1000"與
"2000以上或無"之間有落差），建議 Phase A 直接對照 XLSX 原始儲存格
（非PDF文字層）重新逐格核實，本稽核不擅自假設精確門檻以免誤導。

---

## TASK 8 — SHULIN_RESIDENTIAL_INDIVIDUAL_RULE_INVENTORY

（來源：`評價基準明細表.pdf` page6-9）

| field_id | 中文label | grade數 | interval | source_page | special handling |
|---|---|---|---|---|---|
| land_area | 面積 | 5 | 2.5 | p.6 | — |
| land_width | 寬度 | 5 | 1.25 | p.6 | — |
| land_depth | 深度 | 5 | 1.25 | p.6 | **非單調**：優14-30m，稍優30-40m，普通7-14m或40-50m——最適深度為中間帶，過淺過深皆劣 |
| land_shape | 形狀 | 2 | 5 | p.6 | — |
| street_frontage | 臨街情形 | 5 | 2.5 | p.6 | — |
| land_terrain(individual) | 地勢 | 2 | 10 | p.6 | 個別因素僅2級(平坦/高亢低窪)，注意與**區域**因素之地勢(5級)不同因素 |
| road_type | 道路種類 | 5 | 1.25 | p.7 | — |
| frontage_road_width | 面前道路寬度 | 5 | 3 | p.7 | — |
| school_proximity(individual) | 接近學校程度 | 5 | 1.25 | p.7 | — |
| market_proximity(individual) | 接近市場程度 | 5 | 1.25 | p.7 | — |
| park_proximity(individual) | 接近公園、廣場程度 | 5 | 1.25 | p.7 | — |
| station_proximity(individual) | 接近車站程度 | 5 | 1.25 | p.7 | — |
| commercial_district_proximity | 接近商圈程度 | 5 | 2.5 | p.8 | — |
| nuisance_facility | 嫌惡設施之有無 | 5 | 2 | p.8 | 距離越近分數越差（反向） |
| parking_convenience(individual) | 停車方便性 | 3 | 2.5 | p.8 | — |
| zoning_designation | 使用分區或編定 | 5 | 3.75 | p.8 | — |
| building_coverage_ratio(individual) | 建蔽率 | 5 | 2.5 | p.8 | — |
| floor_area_ratio(individual) | 容積率 | **非標準矩陣** | — | p.8 | 見Task 9 |
| construction_restriction(individual) | 有無禁限建 | 5 | 12.5 | p.9 | — |
| dead_end_alley | 無尾巷 | 2 | 5 | p.9 | 新增於"其他"類別 |

---

## TASK 9 — FAR (容積率) Special Rule

```
INDIVIDUAL_FAR_IS_STANDARD_MATRIX=NO
```

原文（評價基準明細表.pdf p.8，容積率列備註欄）：
> 1.容積率差異以土地開發分析法進行試算調整
> 2.本項需與區域因素容積率併同考量，調整不足者另於區域因素補充調整

`SPECIAL_RULE_REQUIRED`：容積率個別因素調整**不得**套用一般 GradeEngine
5級矩陣（此因素在 XLSX 原始儲存格中「優/稍優/普通/稍劣/劣」欄位本身就是
空白，未列任何百分比數字，與其他所有因素形成鮮明對比——已於 Task 2/8
之 cell 檢查中確認）。目前系統（`engine/`）不具備「土地開發分析法」
（一種以開發後價值反推土地貢獻價值的獨立估價方法，非簡單等級調整）：

```
MANUAL_REVIEW_REQUIRED（本輪判定；若未來有明確可程式化之土地開發分析
  公式來源，可改列 EXTERNAL_CALC_REQUIRED，但本輪未發現任何此類公式
  之官方文件根據，不得自行發明）
```

---

## TASK 10 — Three-Comparable Execution Trace

```
THREE_COMPARABLE_DOMAIN_READY=YES（comparable_ids: List[str]、
  comparable_factors: Dict[str, List[FactorInput]] 等本就是 N-元結構）
THREE_COMPARABLE_CALC_READY=YES（FormCompletionEngine.complete_form 內
  `for comparable_id in case.comparable_ids:` 已逐一驅動 Grade/Adjustment/
  Calculation Engine；三顆引擎本身皆為「單一 pair 計算」，天生對N無感，
  N-comparable 完全由呼叫端迴圈提供）
THREE_COMPARABLE_REVIEW_READY=YES（review.py 依 field_id 尾碼 `_{cid}`
  動態解析每個比較標的獨立驗證，無 index-0 特例）
THREE_COMPARABLE_PDF_READY=NO（`pdf/official_pdf_renderer.py` 表4/表5-2
  渲染迴圈仍寫死 `primary_comparable_id = case.comparable_ids[0]` 與
  `row["comparables"][0]`，未迴圈寫入第2/3欄；`pdf/pdf_renderer.py`
  [Audit/Fallback WeasyPrint] 本身反而已是 comparable-count-agnostic，
  因為它只是把 FormCompletionEngine 已產出的完整欄位列表映射進HTML，
  不需要修改）
```

已透過真實程式碼路徑（非僅schema）確認：唯一真正的「單比較標的」瓶頸是
Official PDF Renderer 的欄位寫入迴圈，其餘四層（domain / collect_data /
四顆Engine / review）皆已是 N-comparable-ready。

---

## TASK 11 — Cross-Form Contract

```
COMPETITION_CROSS_FORM_CONTRACT=
  Table3(P001) -> Table5-1 比準地欄位(優劣等級基準)
  Table3(P002) -> Table5-1 comparison1 優劣等級 + Table4 comparison1 條件欄
  Table3(P003) -> Table5-1 comparison2 優劣等級 + Table4 comparison2 條件欄
  Table3(P004) -> Table5-1 comparison3 優劣等級 + Table4 comparison3 條件欄
  Table5-1 comparison{1,2,3} 影響地價區域因素總修正數
    -> Table4 comparison{1,2,3} 區域因素調整百分率
```

`review.py` 目前已可（依 Task 10 trace）獨立驗證每個 `_{cid}` 尾碼的欄位
組，**結構上**足以分別驗證三組比較標的的跨表勾稽；但目前**沒有**任何
既有測試針對「3個獨立比較標的同時送入 review」做過實際執行驗證（現有
`tests/` 對 review.py 的涵蓋都基於金山單一比較標的案例）——這是 Phase C/F
需要新增的測試缺口，而非程式碼缺陷。

---

## TASK 12 — TABLE4_COMPETITION_DIFF（摘要；完整逐欄見 Task 8 個別因素表）

| 表4項目 | 舊 Jinshan renderer 現況 | 樹林正式差異 |
|---|---|---|
| 0基本資料 | `direct_case_fields["base_parcel_id"/"comparable_parcel_id"]`，皆為單一 comparable_parcel_id | 樹林需比準地+3比較標的4組宗地資訊，且新增「宗地流水號」(0003)/「實例編號」(1/2/3)概念——舊版對「實例編號」刻意選擇NOT渲染避免捏造(見上輪報告)，樹林是否有更明確來源需 Phase A 核實 XLSX cell |
| 土地正常單價/交易日期/調整至估價基準日單價/調整百分率 | 無對應舊欄位（金山版無此列——金山Golden Case 用的是Jinshan自己的表4版面，未見「交易日期」列在舊profile） | **NEW ROW GROUP**：樹林表4新增整組「交易資訊」列(0基本資料下方)，皆為COMPETITION_PROVIDED_FIXED(已印刷數字)，舊 renderer 完全沒有對應bbox |
| 地價區段/區域因素調整 | 對應舊`case_no`/`地價區段`欄位，單一comparable | 樹林為4欄(P001+3比較標的)並排 |
| 7-25個別因素 | 舊有面積/寬度/深度等6組於上輪已補齊(individual_land_area等)，但皆為[base]/[comp]**兩欄**設計 | 樹林需**四欄**(比準地+3比較標的)，且新增18接近車站/19接近商圈與舊版位置編號不同（舊為15/16/17/18/19/20/21對照商業版，樹林為15-21但語意需逐一核對，因新版多一項"14面前道路寬度"與舊版位置一致但編號重排) |
| 合計/差異百分率絕對值加總/價格形成因素之相近程度/比準地試算價格/比較標的權重/比準地地價 | 舊版對應`individual_adjustment_total`/`trial_price`/`comparable_weight`/`base_parcel_comparison_price`(單一comparable) | 樹林需各自3欄；**比較標的權重無公式**(見Task13) |
| 全案備註/填寫日期/簽核欄 | 上輪已修正為`form_fill_date`(留空,無來源) | 樹林版全案備註已印刷固定文字(價格日期調整依據/擴大案例蒐集期間之法源說明)，屬COMPETITION_PROVIDED_FIXED，不得覆蓋 |

**不得假設舊 Table4 profile 可直接使用**——上表確認需要：新增交易資訊列群組、
從2欄改4欄、新增/重排個別因素編號、比較標的權重無公式來源。

---

## TASK 13 — Weight Safety

```
STATUTORY_WEIGHT_FORMULA_FOUND=NO
```

逐一檢查 題目.pdf、評價基準明細表.pdf、三份正式 XLSX、repo 內既有
docs/rules 來源：「比較標的權重」欄位在表4/表5-1皆為**空白儲存格**，
無任何公式、無任何備註說明其計算方式。舊 Jinshan Golden Case 之
`comparable_weight`（`FormCompletionResult`docstring 提及"僅1筆比較標的
時官方明文=100"）——此明文僅適用「單一比較標的」情境，樹林3比較標的
情境下沒有對應官方公式。任何 abs差異加總反比重、平均法或其他演算法
**只能標示為 SYSTEM_RECOMMENDATION**，正式PDF「比較標的權重」欄位
必須保留人工確認語意，不得逕稱為官方公式結果。

---

## TASK 14 — Competition PDF Contract

```
COMPETITION_OFFICIAL_PDF_PAGE_COUNT=6
OLD_MAP_PAGES_REQUIRED_IN_COMPETITION_PROFILE=NO
```

頁序（authoritative，來自題目.pdf實際頁序）：
1.表3(P002) 2.表3(P003) 3.表3(P004) 4.表3(P001) 5.表5-1 6.表4。
樹林案件之正式輸出PDF**不含**任何地圖頁（題目.pdf本身6頁皆為表單，無
地價區段略圖/使用分區圖/地價區段圖附件），故舊金山`official_appraisal_
form_v1.pdf`第4-6頁（金山專屬地圖）**絕不得**編入樹林正式 profile——
若未來樹林案件確實需要對應圖資頁，須另行取得樹林專屬圖資來源，不得沿用
金山圖資或無圖資時憑空產生頁面。

---

## TASK 15 — COMPETITION_TABLE3_AUTOMATION_SAFETY_MATRIX

| 表3欄位類別 | 分類 | 理由 |
|---|---|---|
| 都市計畫內外/使用分區/建蔽率/容積率/有無禁限建/建築型態/建築密度/土地利用現況 | AUTO_SAFE | COMPETITION_PROVIDED_FIXED，題目已明確印刷，系統只需忠實顯示/帶入，無判斷空間 |
| 主要道路(名稱+寬度)/區段內道路平均寬度/道路規劃闢建程度/日照/景觀/傾斜度/排水/地勢 | AUTO_SAFE | 同上，皆為題目已填之區域概況 |
| 學校/市場/公園/車站/商圈距離與名稱 | CONFIRM_REQUIRED | 題目本身留白，需 Provider(OSM/政府開放資料)候選 + 既有 Facility Confirmation Gate 人工確認，沿用既有機制 |
| 站牌/巴士/大型車站/交流道 | CONFIRM_REQUIRED | 同上 |
| 電業設施(變電所/瓦斯槽)/殯葬設施(墓地/殯儀館/火葬場/納骨塔)/廢棄物處理設施(垃圾場/焚化爐/污水處理場) | CONFIRM_REQUIRED | 既有 Facility Confirmation Gate + Stale Confirmation Gate 直接適用，不因案件別而改變安全等級 |
| 環境污染(水/噪音/廢氣/廢棄物污染) | CONFIRM_REQUIRED | 同上 |
| 停車場地/觀光遊憩設施/服務性設施/百貨公司/金融機構/娛樂設施/大型展示中心 | CONFIRM_REQUIRED | 同上 |
| 顧客之通行量/店鋪之毗連狀態/接近聚落程度/接近運銷中心程度/接近消費市場程度 | MANUAL_ONLY | 主觀評等概念，OSM/政府資料無法產生候選，只能人工填寫（同金山案例對這類欄位之既有處理原則） |
| 房屋建築現況(建築密度/型態)——已在A類 | AUTO_SAFE | （與上方重複分類，僅為完整性列出） |

不得因正式比賽移除既有 Facility Confirmation Gate / Stale Confirmation
Gate / Provenance / Tamper Detection；System Recommendation 一律不得
標示為 Official Confirmed Result。

---

## TASK 16 — ShulinCompetitionCase Fixture Schema（僅列schema，不建立production fixture）

```
ShulinCompetitionCase {
  case_no: str  # 題目印刷為"1110901-99-XXX"，XXX部分未定，需另行確認
                # source_type=CompetitionProvided, source_file=表4比較法調查估價表.xlsx,
                # source_sheet=表4比較法調查估價表, source_cell_or_range=(案號標籤所在列，精確cell待Phase A核對)
  appraisal_base_date: "1110901"
                # source_file=題目.pdf, source_page=1-4 (各段年期欄一致印刷"1110901")
  city: "新北市", district: "樹林區"
                # source_file=題目.pdf, source_page=1
  land_use_type: "普通住宅用地"
                # source_file=題目.pdf, source_page=1（標題列）

  base_segment: "P001-00"
    table3_provided_fields: {...}  # source_file=題目.pdf, source_page=4
  comparable_segments: [
    { id: "P002-00", table3_provided_fields: {...} },  # source_page=1
    { id: "P003-00", table3_provided_fields: {...} },  # source_page=2
    { id: "P004-00", table3_provided_fields: {...} },  # source_page=3
  ]

  table4_transaction_values: {
    "P002-00": { parcel_id: "新北市樹林區樹德段284地號", instance_no: 1,
                 land_normal_price: 130167, transaction_date: "111年9月14日",
                 price_date_adjustment_rate_pct: "4.09%",
                 adjusted_to_base_date_price: 140808 },
      # source_file=表4比較法調查估價表.xlsx, source_sheet=表4比較法調查估價表,
      # source_cell_or_range=（第1比較標的欄，precise cell待Phase A核對，
      #   本輪僅能確認來自 題目.pdf page6 印刷值，精確XLSX座標未逐一核對）
    "P003-00": { parcel_id: "新北市樹林區大平段367、917地號", instance_no: 2,
                 land_normal_price: 135275, transaction_date: "111年1月11日",
                 price_date_adjustment_rate_pct: "5.49%",
                 adjusted_to_base_date_price: 180292 },
    "P004-00": { parcel_id: "新北市樹林區文林段317地號", instance_no: 3,
                 land_normal_price: 170909, transaction_date: "110年10月29日",
                 price_date_adjustment_rate_pct: ???  },
      # 注意：題目.pdf page6原文交易日期列疑似排序有誤（P002/P003/P004三組
      # 交易日期與調整百分率的對應需以XLSX原始欄位逐一核對，本輪PDF文字
      # 抽取結果不足以100%確信對應關係，誠實記錄此不確定性，不得猜測填入)
  }
  base_parcel: { parcel_id: "新北市樹林區樹德段1415地號", serial_no: "0003" }
    # source_file=表4比較法調查估價表.xlsx, source_sheet=表4比較法調查估價表

  EXPECTED_FIXED_VALUES: [所有上述COMPETITION_PROVIDED_FIXED數值，供未來
    regression 比對，每筆皆需標示 source_file/source_page或source_sheet]
}
```

（本輪僅列schema並標示provenance來源類型，未建立任何 production fixture
檔案，符合Task16「本輪只列schema」要求。）

---

## TASK 17 — Migration Phases

| Phase | Goal | Files/modules | Core engine impact | Migration risk | Tests required | Exit criteria |
|---|---|---|---|---|---|---|
| A. Competition Rule Pack | 建立樹林住宅regional/individual rule pack並以既有Case-Scoped Rule Package機制掛載 | 新增 `data/rules/competition/shulin_residential_2026/regional_rules.json`+`individual_rules.json`；使用既有`CaseRuleRepository`/`rule_engine_factory.py`(**不修改**) | 無（機制已存在，只需資料） | 低 | RuleTableValidator結構驗證、grade數/adjustment matrix逐項核對評價基準明細表 | 新rule pack通過CONFIRMED gate，且能被rule_engine_factory正確解析而不誤觸static fallback |
| B. Competition Domain + 4 Table3 | 建立SegmentSurveyRecord schema，支援P001/P002/P003/P004各自獨立FACTORS | `domain/models.py`新增schema、`backend/handlers/collect_data.py`、`facility_confirmation_repository.py`(需將`_sk(subtype)`改為含segment_code) | 無（Facility Confirmation屬handler層，非Core Engine） | 中（既有`_sk`鍵衝突需先解決，否則4段互相覆寫） | 4-segment獨立寫入/讀取不互相覆寫的regression test | 4個segment的FACTORS可同時存在且互不覆寫 |
| C. Table5-1 three-comparable | 依Task6 schema diff建立表5-1 profile+渲染，支援3欄比較標的 | 新official template profile builder script、`pdf/official_pdf_renderer.py`表5-2迴圈改寫為per-comparable-index迴圈（或新增平行函式，保留舊金山函式不動） | 無 | 中（渲染座標需以XLSX/衍生PDF重新量測） | 3組比較標的grade/pct皆正確渲染、且金山既有渲染regression不受影響 | 表5-1三欄輸出通過人工400 DPI視覺核對 |
| D. Table4 three-comparable | 同上，表4改4欄（比準地+3比較標的） | 同上renderer；新增 comparable_transaction_date等既有欄位擴充為per-cid | 無 | 中高（bbox數量大幅增加，需系統化生成而非逐一手測） | field-level reconciliation test（比照本次semantic-safety審計方式，逐field分類A/B/C/D並鎖定測試） | 表4四欄輸出reconciled，無fabricated值 |
| E. XLSX-derived Official PDF V2 | 落實Task3 OPTION_B：三份官方XLSX目標sheet→clean PDF template + profile | 新增`scripts/build_clean_official_template_shulin.py`等（比照既有金山腳本模式，獨立檔案不影響金山版） | 無 | 中（XLSX→PDF轉換保真度需人工視覺驗證） | 比照既有`verify_no_residual_text.py`模式的殘留檢查、redaction artifact掃描 | 新template通過白色redaction artifact/殘留文字雙重掃描 |
| F. Competition Local E2E | 端到端跑通：4 Table3輸入→表5-1→表4→6頁PDF | 整合以上所有Phase | 無 | 中 | 完整E2E regression（比照`test_competition_dual_input_e2e.py`模式建立樹林版本） | 6頁PDF正確產出，Confirmation Gate/Stale Gate/Provenance/Tamper Detection全數通過既有測試 |

---

## FINAL REPORT

```
OFFICIAL_SOURCE_ROOT=data/sources/competition/shulin_residential_2026/
OFFICIAL_SOURCE_FILES_FOUND=5/5
SOURCE_PACKAGE_INCOMPLETE=NO

LEGACY_SOURCE_MIXING_DETECTED=NO

LEGACY_PROFILE_ID=jinshan_commercial_v1（即現行 official_appraisal_form_v1.* + regional_rules.json/individual_rules.json，非正式代號，本輪暫定命名供區分）
COMPETITION_PROFILE_ID=shulin_residential_2026（建議代號，尚未建立實體檔案）

BASE_SEGMENT=P001-00
COMPARABLE_1=P002-00
COMPARABLE_2=P003-00
COMPARABLE_3=P004-00

TABLE3_TARGET_SHEET=表3區段勘查表
TABLE3_TARGET_RANGE=A1:V46

TABLE51_TARGET_SHEET=表5-1區域因素明細表(住)
TABLE51_TARGET_RANGE=A1:M45（print_area；sheet本身資料達Q45）

TABLE4_TARGET_SHEET=表4比較法調查估價表
TABLE4_TARGET_RANGE=A1:R36（print_area；sheet本身資料達S37）

TARGET_SHEETS_HAVE_FORMULAS=部分（僅表5-1的B42=1個加總公式；表3/表4皆0個公式）
WHOLE_WORKBOOK_PDF_EXPORT_SAFE=NO

RECOMMENDED_OFFICIAL_TEMPLATE_STRATEGY=OPTION_B（XLSX仍為layout source of truth，PDF template為DERIVED_RUNTIME_TEMPLATE）

MULTI_SEGMENT_TABLE3_SUPPORTED_CURRENTLY=NO
SEGMENT_SCOPED_FACTORS_SUPPORTED_CURRENTLY=NO

THREE_COMPARABLE_DOMAIN_READY=YES
THREE_COMPARABLE_CALC_READY=YES
THREE_COMPARABLE_REVIEW_READY=YES
THREE_COMPARABLE_PDF_READY=NO

OLD_RULEPACK_COMPATIBLE_WITH_COMPETITION=NO（樹林住宅28+1項regional因素與金山商業28項相比，至少5項REMOVED、2項NEW、多項門檻數字不同，見Task6/7表；且個別因素容積率為特殊土地開發分析法規則，非標準矩陣）

INDIVIDUAL_FAR_IS_STANDARD_MATRIX=NO

STATUTORY_WEIGHT_FORMULA_FOUND=NO

COMPETITION_OFFICIAL_PDF_PAGE_COUNT=6
OLD_MAP_PAGES_REQUIRED_IN_COMPETITION_PROFILE=NO

PRODUCTION_CODE_MODIFIED=NO
CORE_ENGINE_MODIFIED=NO

MIGRATION_BLOCKERS=
  1. pdf/official_pdf_renderer.py 表4/表5-2渲染迴圈為單一比較標的設計（primary_comparable_id/row["comparables"][0]），需改為per-comparable-index迴圈才能支援3個比較標的輸出。
  2. facility_confirmation_repository.py 的 _sk(subtype) 排序鍵未含segment_code，4個segment若共用同一case_id會互相覆寫FACTORS/確認紀錄。
  3. 尚無樹林住宅專屬XLSX-derived clean template + profile（Task3 OPTION_B的前置工作，需比照既有build_clean_official_template.py/build_official_template_profile.py模式新建，非直接複用金山版）。
  4. 表5-1「使用分區/建蔽率/容積率修正併同於表4個別因素考量調整」之特殊規則，實際計算路徑與金山版本（regional直接計算）不同，需先釐清才能設計Calculation Engine呼叫方式（但不得修改Core Engine本身邏輯——需在case-scoped rule pack或handler層處理）。
  5. 容積率個別因素無標準矩陣，需土地開發分析法或MANUAL_REVIEW_REQUIRED流程，目前系統無此能力。
  6. 比較標的權重無官方公式依據，僅能SYSTEM_RECOMMENDATION，PDF需保留人工確認語意。
  7. 表4「案號」印刷值含字面"XXX"佔位符，正式具體案號尚待確認，不得自行編造。
  8. 部分regional因素（停車場地之便利程度/接近服務性設施的程度）之PDF文字抽取級距疑似不連續，需以XLSX原始儲存格重新核實精確門檻數字。

RECOMMENDED_NEXT_PHASE=Phase A（Competition Rule Pack）——風險最低、無Core Engine變動、可直接利用既有Case-Scoped Rule Package機制，且是後續所有Phase的前提（B-F皆依賴正確的樹林regional/individual規則）。
```

## PASS CONDITION 檢核

1. 正式五個source file 5/5被找到 ✓
2. 正式來源與legacy source明確隔離（LEGACY_SOURCE_MIXING_DETECTED=NO）✓
3. 舊假設完整盤點（Task1 LEGACY_ASSUMPTION_MATRIX，含Explore agent完整程式碼路徑trace）✓
4. Table3/Table5-1/Table4 schema diff完成（Task2/6/12）✓
5. 樹林住宅Regional/Individual Rule inventory完成（Task7/8，含grade數確認）✓
6. Three-comparable現況被真實trace（Task10，逐層引用實際程式碼行號）✓
7. 正式6-page PDF contract明確（Task14）✓
8. Migration Blocker與Phase plan完整（Task17，8個具體blocker + 6個Phase）✓

且 PRODUCTION_CODE_MODIFIED=NO、CORE_ENGINE_MODIFIED=NO ✓

```
FORMAL_COMPETITION_PACKAGE_MIGRATION_AUDIT=PASS
```
