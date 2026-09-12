# OFFICIAL-SIX-PAGE-PDF-E1

前置：`TABLE51_THREE_COMPARABLE_C1=PASS`、`TABLE4_THREE_COMPARABLE_D1=PASS`。
目標：正式建立樹林案的官方六頁 PDF（表3×4＋表5-1＋表4），與既有 Audit PDF／
金山官方 PDF 完全分離、獨立輸出。

## Task 0/1 — 現況稽核（已於前一輪回報，摘要）

`CURRENT_OFFICIAL_PDF_SUPPORTS_SHULIN=NO`（既有 `pdf/official_pdf_renderer.py`
僅支援金山單一比較標的、硬編碼 `comparable_ids[0]`、覆蓋 `data/templates/
official_appraisal_form_v1.pdf` 之金山地圖頁）。`CURRENT_AUDIT_PDF_SEPARATE_
FROM_OFFICIAL=YES`（`pdf_handler.py` 既有回應已分離 `official_pdf_url` 與
`audit_pdf_url`，本輪沿用同一分離原則）。表3/表5-1/表4三份官方 XLSX 皆已用
openpyxl 逐欄稽核完成。

## Task 2 — Build-Time Template 策略

本機無 LibreOffice，改用 **Microsoft Excel COM 自動化**（`scripts/
build_shulin_official_pdf_templates.py`，僅 build-time 執行，`runtime`
從未 import pywin32）：ReadOnly 開啟來源 XLSX、`Worksheet.
ExportAsFixedFormat`（單一 sheet，非整本 workbook）匯出、關閉不儲存。產出：

```
data/templates/shulin/shulin_table3_blank_v1.pdf
data/templates/shulin/shulin_table51_blank_v1.pdf
data/templates/shulin/shulin_table4_blank_v1.pdf
data/templates/shulin/shulin_template_identity.json
```

每筆 identity 紀錄 `template_id/template_version/source_xlsx/source_sheet/
source_xlsx_sha256/derived_pdf_sha256/build_method=
MICROSOFT_EXCEL_COM_BUILD_TIME_EXPORT/build_timestamp/provenance_note`
（明確聲明「非官方原始 PDF」）。`pdf/shulin_official_pdf_renderer.py` 每次
render 前重新計算 `derived_pdf_sha256` 並比對，不符即
`TemplateIdentityMismatchError`（fail-closed，同既有 Jinshan
`TemplateLayoutUnconfirmedError` 模式）。

```
RUNTIME_LIBREOFFICE_REQUIRED=NO
RUNTIME_TEMPLATE_DERIVED_FROM_OFFICIAL_XLSX=YES
```

## Task 3 — 可稽核 Overlay Mapping

`pdf/shulin_pdf_grid.py`：從 Excel 自身匯出的 PDF 讀取真實邊框繪製
（`page.get_drawings()` 之 `re`/`l` 兩種 path item 皆讀取——僅讀 `re`
一度漏掉表3三成欄界），以 tolerance-clustering 還原格線，再用
`reconcile_boundaries()` 補足偵測不到邊框的欄/列（表3標題列1-2無框線、
部分欄無框線）——採「缺口自動比對＋已知 leading offset 可覆寫」演算法，
以 `page.search_for()` 之真實標籤座標交叉驗證修正（發現並修正一次
`_best_leading_offset` 自動判斷誤選之 bug）。`scripts/build_shulin_pdf_
mappings.py` 產出：

```
data/templates/shulin/shulin_table3_mapping.json（16 fields）
data/templates/shulin/shulin_table51_mapping.json（234 fields）
data/templates/shulin/shulin_table4_mapping.json（177 fields）
```

每筆 entry 含 `field_id/source_sheet/source_cell/rect/font_size/align/
formatter`，`source_cell` 為真實 Excel A1 座標，可逐一追溯回 XLSX。

## Task 4 — 表3四頁渲染

`TABLE3_PAGE_ORDER=["P002-00","P003-00","P004-00","P001-00"]`，每頁獨立讀取
`table51_analysis.py::_load_segment_regional_factors()`（既有函式，
CompetitionProvided precedence 已套用，未新增第二套邏輯）取得該 segment
專屬 `FACTORS#<segment_code>` 原始值。城市/行政區（新增：`_build_table3_
data_by_segment()` 於 `shulin_official_pdf_handler.py` 中，將 `meta["city"]`
與 `CompetitionSegment.district`——皆為既有真實資料，非杜撰——併入區段範圍
欄位，使頁面可顯示「新北市」「樹林區」）。未涵蓋之設施類欄位（學校/市場/
殯葬/車站等 checkbox+距離）誠實留白：Shulin 尚無如金山
`facility_confirmation_repository.py` 之對應資料來源，已於 mapping builder
腳本中明確註記為已知範圍限制。

```
TABLE3_P002_RENDERED=YES
TABLE3_P003_RENDERED=YES
TABLE3_P004_RENDERED=YES
TABLE3_P001_RENDERED=YES
TABLE3_SEGMENT_CROSS_CONTAMINATION=NO
```

## Task 5 — 表5-1渲染

`build_table51_page()` 直接消費 `Table51Analysis`（C1 runtime, 未重新
grading），29 個 factor 分 8 類＋總修正數逐一對應真實 XLSX 列號（`docs/
phase7/table51_three_comparable_c1_final_gate.md` 已確立之列序）。
`requires_manual_review` 或 `subtotal_pct/total_adjustment_pct=None`
之欄位保持空白，從未填 0。修正一次「小計/總修正數欄位重複顯示%」問題
（該列自身已有獨立「％」靜態標籤儲存格，新增 `pct_signed_no_unit`
formatter 避免 `+6.25%%`）。

```
PDF_TABLE51_USES_C1_RUNTIME_RESULT=YES
PDF_TABLE51_REGRADES_FACTORS=NO
PDF_TABLE51_COMPARABLE_COUNT=3
PDF_TABLE51_MISSING_AS_ZERO=NO
```

## Task 6 — 表4渲染

`build_table4_page()` 直接消費 `Table4Analysis`（D1 runtime）。P002/P003/
P004 各自獨立寫入交易日期/土地正常單價/調整百分率/調整至估價基準日單價/
地價區段（bridge 來源）/區域因素調整百分率/20格個別因素（含 FAR 特殊政策
第26列、其他catch-all第28列）。修正一次「估價基準日／案號」欄位互換 bug
（`K1`/`O1` 為標籤本身而非值欄，值欄實為 `L1:N1`/`P1:R1`）。

```
PDF_TABLE4_USES_D1_RUNTIME_RESULT=YES
PDF_TABLE4_COMPARABLE_COUNT=3
PDF_TABLE4_P002_MAPPING_VERIFIED=YES
PDF_TABLE4_P003_MAPPING_VERIFIED=YES
PDF_TABLE4_P004_MAPPING_VERIFIED=YES
```

## Task 7/8 — FAR／權重安全

`Table4FactorResult`/`Table4Comparison` 模型本身在 D1 已 fail-closed
（FAR 無 rule record→恆 None；`weight_status` 恆非 `HUMAN_CONFIRMED`）。
`build_table4_page()` 因此**結構性**無法填入 FAR/權重/比準地比較價格
（後者 `Table4Analysis` 根本無此欄位，非只是留空）——非執行期額外檢查
達成，而是資料源頭本就沒有可填的值。

```
PDF_FAR_FAKE_ZERO_USED=NO
PDF_FAR_STANDARD_MATRIX_USED=NO
PDF_UNVERIFIED_WEIGHT_AUTO_FILLED=NO
PDF_FAKE_BASE_COMPARISON_PRICE_USED=NO
```

## Task 9 — Renderer 技術選型

`pdf/shulin_official_pdf_renderer.py`：**僅** PyMuPDF；獨立於
`pdf/official_pdf_renderer.py`（金山）、從未 import 或修改該檔／
`data/templates/official_appraisal_form_v1.pdf`。CJK 字型解析為本檔獨立
的最小複製（`_resolve_cjk_font_path()`），刻意不共用金山模組以維持
完全解耦。

```
OFFICIAL_PDF_RENDERER=PYMUPDF
OFFICIAL_PDF_USES_WEASYPRINT=NO
PDF_MAPPING_CONFIG_AUDITABLE=YES
```

## Task 10 — 六頁組裝

`render_shulin_official_six_page_pdf()`：單一 `fitz.Document`
（先插入6個空白模板頁，再統一於同一份文件上疊字，CJK 字型僅需嵌入一次
——最初「每頁各自建立獨立文件再合併」的作法導致單一字型檔重複嵌入6次，
產出83MB的六頁PDF；改為單文件後配合 `subset_fonts()` 降至約340KB）。
組裝後即時檢查 `page_count==6`，不符則 `ShulinPdfDataUnavailableError`。

```
OFFICIAL_PDF_PAGE_COUNT=6
OFFICIAL_PDF_PAGE_ORDER_VERIFIED=YES
LEGACY_GOLDEN_MAP_PAGE_PRESENT=NO
```

## Task 11/12 — Handler Wiring

新增 `backend/handlers/shulin_official_pdf_handler.py::
get_shulin_official_pdf_if_applicable(event, context, case_no, meta)`：
`competition_segments.get_segment_map(case_no) is None` 時回傳 `None`
（訊號呼叫端 fall through 至既有金山邏輯）；否則完整處理 Shulin 六頁流程並
回傳最終 response。`backend/handlers/pdf_handler.py::get_pdf()` 在最頂端
呼叫此函式，`None` 才繼續執行**完全未改動**之既有金山程式碼——未新增重複
路由，沿用既有 `GET /api/cases/{id}/pdf`（`infra/template.yaml`
既有註冊、`backend/docker/pdf.Dockerfile` 之整目錄 COPY 已自動涵蓋新增
檔案，無需修改 infra）。

```
SHULIN_OFFICIAL_PDF_API_WIRED=YES
SHULIN_PDF_FALLBACK_TO_JINSHAN=NO
LEGACY_JINSHAN_PDF_FLOW_PRESERVED=YES
```

## Task 13/14 — Fail-Closed 安全檢查

`get_shulin_official_pdf_if_applicable()` 顯式攔截並轉譯：
`SegmentMapRequiredError`→400、`SegmentFactorsNotFoundError`（含缺件
segment_code清單）→400、`RuleProfileNotReadyError`→409、
`CaseRulePackageInvalidError`→409、`table4_analysis.get_table4_analysis()`
非200時原樣轉發其錯誤、`TemplateMissingError`/`TemplateIdentityMismatchError`
→500、`FontUnavailableError`→500、`ShulinPdfDataUnavailableError`→400。
任何一種失敗都不會呼叫 S3 put_object，故從未產出部分頁 PDF。

## Task 15/16 — 測試

新增 `tests/test_official_six_page_pdf_e1.py`（22 tests，涵蓋原 Task 16
所列25項編號情境，多項合併測試）：六頁順序、無金山洩漏、Table51/Table4
runtime 值原樣呈現、FAR/權重/比準地比較價格恆空白、題目.pdf 固定值精確
呈現、CJK 無亂碼、Official/Audit 分離（靜態掃描 + 渲染文字雙重驗證）、
Template identity 正常＋竄改後 fail-closed、真實 handler 全流程 E2E、
金山 legacy 分支不受影響、原始碼靜態掃描防
`comparable_ids[0]`/`comparables[0]`/`average(`/`primary_comparable`、
遺失模板／不完整案件 fail-closed。

pdf_handler.py 模組層級無條件 `import weasyprint`（Windows 本機缺
libgobject，與本輪無關的既有限制）——測試以 `sys.modules["weasyprint"]`
注入最小假模組繞過**僅import**階段，Shulin 分支程式碼本身從未呼叫
weasyprint 之任何函式。

## Task 17 — Regression

E1 專屬測試：22 passed。全量迴歸結果見 D1_FINAL_REPORT 等價區塊（本輪
FINAL REPORT）。
