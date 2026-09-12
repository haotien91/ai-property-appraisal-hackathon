# OFFICIAL-PDF-FINAL-QUALITY-GATE-1 — 驗證與修正報告

## 範圍與 Core Freeze 遵循

本輪修改：`scripts/build_clean_official_template.py`、
`scripts/build_official_template_profile.py`、`pdf/official_pdf_renderer.py`
（僅 render_official_pdf 內之 wiring/mapping，未動任何估價邏輯）、
`scripts/build_official_pdf_mock.py`、`tests/test_official_pdf_output.py`
（更新 1 個既有測試＋新增 1 個測試類別）。

**未修改**：Rule Engine／Grade Engine／Adjustment Engine／Calculation
Engine／Form Completion Engine／GIS Engine／Road Width Resolver／
adjustment matrix／rule definitions。全數以 `grep`/人工比對確認未觸碰
`engine/` 下任何檔案。

## TASK 1 — Clean Template Redaction Artifact 稽核

以 200 DPI 渲染 clean template，對 462 筆 redaction manifest 逐一比對
「redaction 區塊中心色」vs「區塊外緣色」，找出「中心為白色、外緣為灰/黃色」
的不匹配案例：

```
WHITE_REDACTION_ARTIFACT_FOUND_BEFORE=YES
count=86（表1灰色核取方塊欄56筆，rgb≈(191,192,191)／表4淡黃色欄位標籤列
          30筆，rgb≈(255,255,204)）
```

根因：`build_clean_official_template.py`（前一輪 PDF-OFFICIAL-1）的
`page.add_redact_annot(rect, fill=(1,1,1))` 一律使用白色填色，未讀取被
redact 儲存格本身的真實背景色。

## TASK 2 — 保留儲存格背景色

修正 `build_clean_official_template.py`：新增
`_background_fill_regions()`（直接讀取原始 PDF 自身的 vector fill
drawing 物件，取得每頁真實的灰／黃底色區塊，而非渲染像素猜測）與
`_cell_background_fill()`（取包含該 redaction 中心點、面積最小的底色區
域，找不到則維持白色）。`add_redact_annot(rect, fill=...)` 改用此結果。
`apply_redactions()` 呼叫方式完全不變（仍是真正的文字/繪圖移除，非疊加
白框）。

重新執行後：

```
WHITE_REDACTION_ARTIFACT_FOUND_AFTER=NO（86筆全部修正，人工400 DPI視覺比
  對亦確認灰色/淡黃色欄位恢復為連續底色，無白色破洞）
CLEAN_TEMPLATE_RESIDUAL_SAMPLE_FOUND=NO（scripts/verify_no_residual_
  text.py：462筆全數0殘留，與修正前相同——確認只換填色，未恢復任何
  sample text）
```

## TASK 3 — road_name 追蹤

```
ROAD_NAME_SOURCE_EXISTS=YES（providers/road_provider.py 之
  MockRoadProvider/RealRoadProvider 皆會產出 main_road_name）
ROAD_NAME_FACTORS_EXISTS=YES（修正後；修正前為NO——本系統真實
  backend/handlers/collect_data.py 本來就會同時跑 MockRoadProvider，只有
  離線 Mock 產生腳本 scripts/build_official_pdf_mock.py 漏掉這支
  provider）
ROAD_NAME_PASSED_TO_OFFICIAL_RENDERER=YES（修正後）
ROAD_NAME_RENDER_PROFILE_EXISTS=YES（renderer 本身早已支援讀取
  facility_points 之 main_road_name，本來就沒有 profile 缺口）
ROAD_NAME_RENDERED=YES（修正後，400 DPI 人工視覺確認「名稱：中山路」；
  注意：字型 NotoSansTC-VF.ttf 對「路」字回填的文字層 codepoint 為 CJK
  COMPATIBILITY IDEOGRAPH U+F937 而非標準 U+8DEF，純文字比對
  `'中山路' in text` 會誤判為 False——這是本機字型層級的既有現象，非本輪
  引入，也不影響實際視覺呈現，已以人工視覺渲染而非文字搜尋作為權威驗證）
```

修正：`scripts/build_official_pdf_mock.py` 的 `_facility_points_from_mock_
provider()` 新增呼叫 `MockRoadProvider().fetch(ctx)` 並併入 points 清單
（純 mock/data wiring 修正，未動 Road Width Resolver 任何一行）。

## TASK 4 — 表5-2 Header 完整性

```
TABLE52_CASE_NO_SOURCE=case.case_no（與表4已使用的同一值）
TABLE52_COMPARABLE1_INSTANCE_ID_SOURCE=不存在——網域模型（domain/
  models.py）中沒有任何「實例編號」概念（不同於 comparable_parcel_id），
  查全庫確認無此欄位
TABLE52_CASE_NO_RENDERED=YES（修正後；修正前為NO——profile 從未有
  table5_2 header 對應項）
TABLE52_COMPARABLE1_INSTANCE_ID_RENDERED=NO（維持空白——依指示「不得捏造
  1」，即使原始範本例圖恰好顯示"1"，因無對應 Structured Result 來源，
  歸類 SOURCE_NOT_AVAILABLE）
```

修正：`scripts/build_official_template_profile.py::build_table5_2()`
新增 `case_no` 欄位項（bbox 取自 redaction manifest 已量測之
value_bbox），`pdf/official_pdf_renderer.py` 表5-2 迴圈新增一行明確寫入
（跳過此 key 不進入 28-factor 迴圈)。

## TASK 5 — 表4 完整性稽核（依指定 25 個項目逐一 trace）

| # | 項目 | 分類 | 備註 |
|---|---|---|---|
| 1 | 基本資料 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 2 | 交易日期 | SOURCE_AVAILABLE_RENDERED | **本輪修正**（`comparable_transaction_date`，profile 缺口） |
| 3 | 面積 | SOURCE_AVAILABLE_RENDERED | **本輪修正**（`individual_land_area`，profile 缺口） |
| 4 | 寬度 | SOURCE_AVAILABLE_RENDERED | **本輪修正**（`individual_land_width`，profile 缺口＋omit_unit避免overflow） |
| 5 | 深度 | SOURCE_AVAILABLE_RENDERED | **本輪修正**（`individual_land_depth`，profile 缺口） |
| 6 | 形狀 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 7 | 臨街情形 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 8 | 地勢 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 9 | 道路種類 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 10 | 面前道路名稱/寬度 | SOURCE_AVAILABLE_RENDERED（寬度）＋SOURCE_NOT_AVAILABLE（名稱） | 寬度**本輪修正**；名稱無來源，維持空白 |
| 11 | 學校 | SOURCE_AVAILABLE_RENDERED（距離）＋SOURCE_NOT_AVAILABLE（校名） | 距離**本輪修正**；校名無來源 |
| 12 | 市場 | 同上 | 距離**本輪修正** |
| 13 | 公園 | 同上 | 距離**本輪修正** |
| 14 | 車站 | 同上 | 距離**本輪修正** |
| 15 | 商圈 | 同上 | 距離**本輪修正** |
| 16 | 嫌惡設施 | 同上 | 距離**本輪修正** |
| 17 | 停車 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 18 | 使用分區 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 19 | 建蔽率 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 20 | 容積率 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 21 | 禁限建 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 22 | 調整率 | SOURCE_AVAILABLE_RENDERED | 既有（區域/個別/日期三種調整率皆有） |
| 23 | 試算價格 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 24 | 權重 | SOURCE_AVAILABLE_RENDERED | 既有 |
| 25 | 比準地比較價格 | SOURCE_AVAILABLE_RENDERED | 既有 |

Mutually-exclusive 統計（以 Task 5 列出的 25 個「列項目」為計數單位，每項
只計 1 次；第10、11-16 共 7 項雖同時含「有來源已render的數字」與「無來源
的設施/道路名稱」兩個子欄位，仍各算作 1 個 SOURCE_AVAILABLE_RENDERED 列
項目——因為該列在畫面上並非全空白，主要可計算之數值已正確填入；其名稱
子欄位缺口已在上表逐列註記，未被隱藏或忽略）：

```
TABLE4_TOTAL_TARGET=25
TABLE4_SOURCE_AVAILABLE_RENDERED=25
TABLE4_SOURCE_AVAILABLE_NOT_RENDERED=0
TABLE4_SOURCE_NOT_AVAILABLE=0（於列層級；子欄位層級有7組「設施/道路名稱」
  屬 SOURCE_NOT_AVAILABLE，詳見上表，未計入此列層級加總避免重複計數）
TABLE4_MANUAL_REQUIRED=0
```

本輪額外發現但**不在 Task 5 指定清單內、本輪未修**：頁首「估價基準日：」
儲存格（bbox 於原始範本量測為 x=543-570,y≈33）目前空白，而
`case.appraisal_base_date="1140901"` 實際被寫入頁尾「填寫日期：」儲存格
（語意上為不同概念，原範本填寫日期範例為"114年09月18日"，與
估價基準日"1140901"是兩個不同日期）——記錄為 BACKLOG，非本輪範圍。

## TASK 6 — Source vs Renderer 分類原則

所有本輪判定為 SOURCE_AVAILABLE_RENDERER_GAP（分類 A）的欄位皆已修正
（面積/寬度/深度、交易日期、表5-2案號、6組接近條件距離、面前道路寬度，
共 16 個獨立 profile 欄位新增）。分類 B（SOURCE_NOT_AVAILABLE，如設施
名稱、比較標的1實例編號）與分類 C 一律保持空白，**未填入任何 Golden
Case sample text 冒充真實資料**。

## TASK 7 — Map Page 生產安全稽核

```
NON_GOLDEN_CASE_WOULD_RECEIVE_GOLDEN_MAPS_BEFORE=YES
  （render_official_pdf() 修正前恆定開啟同一份 template_path，
  對 page index 3/4/5 完全不做任何 case 判斷或處理，任何 case_no/
  segment_code 皆會原樣保留金山 Golden Case 的圖資）
MAP_SAMPLE_DATA_LEAK_RISK_BEFORE=YES
```

## TASK 8/9 — Safe Map Policy 與 Provenance

新增 `pdf/official_pdf_renderer.py::_case_matches_template_map_identity()`
`_apply_map_page_safety()`（純 PDF 輸出層修正，**未重寫或呼叫** GIS
Engine 任何程式碼）：

- 案件 segment_code/city/district 與範本圖資實際所屬（P002-00／新北市
  金山區）完全相符 → 保留原圖資（圖片/文字皆不變），並在頁面下緣加註
  一行 Provenance：「圖資來源：查估書表範本.pdf 原始附圖（新北市金山區
  P002-00 區段，案號 {case_no}）」。
- 不相符 → 對該頁執行**整頁** `add_redact_annot` + `apply_redactions
  (images=PDF_REDACT_IMAGE_REMOVE)`（真正移除底圖影像，非僅疊加白框），
  改印「{頁名}：圖資待人工附具（本案件（案號…／城市區 區段代碼）與範本
  圖資（新北市金山區 P002-00 區段）不符，本系統尚無可驗證之對應圖資來
  源，請承辦人另行檢附）」。

```
NON_GOLDEN_CASE_RECEIVES_GOLDEN_MAPS_AFTER=NO
GOLDEN_MAP_PAGES_PRESERVED_WHEN_MATCHING_CASE=YES
```

## TASK 10 — 視覺驗證（400 DPI，重新產生之 Golden Official PDF）

Page 1/2/3 人工目視比對官方範本：表格框線、標題、欄位位置、checkbox/
circle 位置皆與範本一致；填入文字未溢出（寬度/深度欄改用 omit_unit 後
已無 FIELD_OVERFLOW_MANUAL_REVIEW，`render_official_pdf.last_overflow_
fields == []`）；無 Fallback banner；無 AUTOMATIC/MANUAL 徽章；無 Rule/
Formula 計算痕跡。

```
WHITE_REDACTION_ARTIFACT_FOUND_AFTER=NO
```

## TASK 11 — Non-Golden Safety Fixture

新增 `tests/test_official_pdf_output.py::TestNonGoldenCaseMapSafety`（3
個新測試，皆 PASS）：以 Golden Case 複製一份、僅替換
case_no="1150312-55-002"／segment_code="B999-00"／city="新北市"／
district="板橋區"，渲染後對 page 3/4/5 逐頁搜尋
`"1140901-99-001"`／`"P002-00"`／`"金美段489地號"`／`"溫泉段218地號"`：
全數 0 命中，且 `get_images()==0`（底圖影像確實被移除，不只是文字），
並顯示「圖資待人工附具」等誠實提示；另有一個對照測試確認 Golden Case
自身完全不受此安全檢查影響（圖片數維持14張，不出現提示文字）。

## TASK 12 — Confirmation Gate 迴歸

`facility_confirmation_repository.get_active_confirmed_selections()` 完
全未修改；重新產生之 Golden Mock Official PDF 再次確認：僅 substation
（CONFIRMED, stale=false）顯示，gas_tank（stale=true）／cemetery
（PENDING）／columbarium（REJECTED）／funeral_home／crematorium／MRT／
TRA 全部空白。`tests/test_facility_confirmation.py`：43 passed。

```
CONFIRMATION_GATE_UNCHANGED=YES
```

## TASK 13 — Regression

```
python -m pytest tests/test_official_pdf_output.py -q
  => 33 passed, 1 skipped（新增3個non-Golden map safety測試＋更新1個
     既有map測試，皆PASS）
python -m pytest tests/test_facility_confirmation.py -q
  => 43 passed
python -m pytest tests -q --ignore=tests/test_phase5_golden_pipeline.py
  => 3 failed, 1091 passed, 3 skipped
     （3項失敗與本輪修改前基準完全相同：tests/test_pdf_output_runtime_
     verification.py::TestPdfHandlerActuallyInvoked 三項，皆因本機
     Windows缺少libgobject-2.0-0／WeasyPrint原生函式庫，與Official
     Renderer（純PyMuPDF路徑）完全無關，未新增任何skip掩蓋新錯誤）
```

```
NEW_FAILURE_COUNT=0
```
