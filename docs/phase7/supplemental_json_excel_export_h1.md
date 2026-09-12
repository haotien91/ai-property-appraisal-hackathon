# SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1

前置：C1/D1/E1=PASS，F1 Local E2E=PASS。本輪目標：在**不重算任何數值**的
前提下，為同一案件新增 JSON／Excel／ZIP 三種補充輸出，Official PDF（E1）
維持唯一正式交付物，計算語意完全不變。

## 架構

```
Case + Segment Data + Table51Analysis(C1) + Table4Analysis(D1)
                ↓
        CaseExportBundle (export/models.py)
        ├── Official PDF   (export/pdf_export.py -> 既有 shulin_official_pdf_renderer.py)
        ├── JSON Export     (export/json_exporter.py)
        └── Excel Export    (export/excel_exporter.py)
```

`export/bundle_builder.py::build_case_export_bundle(case_no)` 是唯一資料
組裝點：呼叫既有 `table51_analysis.build_table51_analysis_for_case()`（C1）
與 `table4_analysis.get_table4_analysis()`（D1），**不**新增第二套
grading／adjustment／weight／FAR 邏輯。PDF／JSON／Excel 三者皆消費同一個
`CaseExportBundle`，保證三者一致（Task 20）。

## JSON

`schema_version="1.0"`。內容：`case`/`segments`/`table3`/`table5_1`
（= `Table51Analysis.model_dump_json()`）/`table4`（=
`Table4Analysis.model_dump_json()`）/`review`/`review_status`/
`manual_review_items`/`provenance`。raw_value 與 evaluation_value（若有）
維持分離，data provenance 與 rule provenance 亦不合併（沿用 C1/D1 既有
FactorInput/Table51FactorResult/Table4FactorResult schema，未新增第二套
provenance 模型）。

## Shulin Review 缺口（F1 已識別，本輪誠實延續）

`review()`／`get_result()` 為 legacy 單一比較標的流程，讀取 bare
`"FACTORS"` record，Shulin 案件只寫入 `FACTORS#<segment_code>`。本輪
**未**重寫 review handler、**未**建立新 review engine、**未**把 legacy
review 結果冒充為 Shulin 正式 review。`CaseExportBundle.review=null`，
`review_status="NOT_AVAILABLE_FOR_SHULIN_YET"`——此狀態**不**阻擋 JSON／
Excel／Bundle 輸出。`manual_review_items` 完全不依賴 review()，直接從
Table51Analysis/Table4Analysis 之 `requires_manual_review` 因素、FAR
特殊政策（`is_far_special_policy`）、非 `HUMAN_CONFIRMED` 之權重狀態整理
取得。

`SHULIN_SEGMENT_AWARE_REVIEW_PENDING=YES`（本輪未改變此狀態，亦非本輪
blocker）。

## Excel

**不**重畫版型、**不**建立新樣板、**不**依賴 Microsoft Excel COM／
LibreOffice。流程：`openpyxl.load_workbook(來源xlsx)`（僅讀取，from-disk
copy 從未被寫回）→ 填值 → `wb.save(輸出路徑)`（另一路徑）。儲存格座標
100% 重用 E1 已審核並視覺驗證過的 `data/templates/shulin/shulin_table
{3,51,4}_mapping.json` 之 `source_cell`——與 PDF 使用同一份 mapping，避免
第二套猜測。數值以 Excel 原生數字寫入（非 PDF 用的「+6.25%」格式化字串），
因 Excel 輸出目的是可編輯資料，非畫面呈現。

輸出 6 個獨立檔案（本輪不合成單一 workbook，理由：降低 merged
cells／print area／page setup 被破壞風險）：
`表3_P002-00.xlsx`／`表3_P003-00.xlsx`／`表3_P004-00.xlsx`／
`表3_P001-00.xlsx`／`表5-1_區域因素分析.xlsx`／`表4_比較法調查估價表.xlsx`。

FAR／未確認權重／比準地比較價格：結構性保持空白（Table4Analysis 本身
這些欄位就是 None／weight_status≠HUMAN_CONFIRMED，寫入函式對 None 值
直接略過，從未填 0／33.33%／任何 fabricated 值）。

`land_use_status`（表3多選 checkbox）本輪**不**在 Excel 中重繪（該邏輯
屬於 PDF 專屬的 redact+redraw 視覺處理，在真實試算表儲存格中重現有破壞
既有 checkbox 圖形風險，故保持原樣未觸碰）。

## API Routes（`infra/template.yaml`，新增，未修改既有 `/pdf`）

```
GET /api/cases/{id}/export/json    ai-valuation-export-json-get
GET /api/cases/{id}/export/excel   ai-valuation-export-excel-get
GET /api/cases/{id}/export/bundle  ai-valuation-export-bundle-get
```

三者皆為 `PackageType: Image`，重用 `PdfFunction` 既有 Docker image
（`backend/docker/pdf.Dockerfile` 新增 `openpyxl`＋`COPY export/`，
`ImageConfig.Command` 依 function 各自覆寫進入點，未建立第二份
Dockerfile）。`/export/json` 直接回傳 JSON body；`/export/excel`／
`/export/bundle` 沿用既有 PDF 之 S3 + presigned URL 慣例（object key：
`<case_no>/exports/{excel,bundle}/<timestamp>_<filename>`）。

## Official PDF 保護

`export/pdf_export.py::render_official_pdf_from_bundle()` 直接重用
`pdf/shulin_official_pdf_renderer.py::render_shulin_official_six_page_
pdf()`（E1、未修改），僅將 bundle 內已算好的 Table51Analysis/
Table4Analysis 重新 `model_validate` 回模型物件餵入——不重算任何格值/
調整率/權重，頁數/順序/內容語意與 E1 完全相同（測試 test_10 驗證）。

## 測試（Task 22/23）

`tests/test_supplemental_exports_h1.py`：6 個 test 函式涵蓋 11 項驗證
（JSON 有效性+4 segment+lineage、6 個 Excel 皆可開啟+來源 XLSX
sha256 未變、missing≠0+FAR/weight 留白、P002/P003/P004 固定值 JSON/Excel
一致、Official PDF 六頁無迴歸、ZIP 內容完整）。

過程中修正一個真實 bug：`CaseExportBundle` 內的數值來自
`Table4Analysis.model_dump_json()`，pydantic JSON 模式把 `Decimal`
序列化為字串（JSON 無原生 Decimal），若不處理會把數字寫成 Excel 文字
儲存格（如 `'130167'` 而非 `130167`）——已用 mapping 既有 `formatter`
欄位（如 `int_no_unit`）還原為真正數字修正。

`tests/test_supplemental_exports_h1.py` + `tests/test_official_six_page_
pdf_e1.py` 皆通過，且本輪未修改 C1/D1/E1 之計算或渲染邏輯，故未執行
全量 regression（符合本輪指示）。
