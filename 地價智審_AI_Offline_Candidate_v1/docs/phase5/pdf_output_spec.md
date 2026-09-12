# Phase 5 — PDF Output Spec

## 官方 PDF 可填性判定（Fallback 觸發原因）

依 Phase 5 指示，正式輸出前須先確認官方PDF能否可靠自動填寫。本階段執行
`/mnt/skills/public/pdf/scripts/check_fillable_fields.py` 於
`查估書表範本.pdf`，結果：

```
invalid pdf header: b'PK\x03\x04\x14'
EOF marker not found
pypdf.errors.PdfStreamError: Stream has ended unexpectedly
```

確認 `查估書表範本.pdf` 並非標準PDF（其真實格式為ZIP封裝的圖片+文字擷取
包，此發現已於Phase 1記錄），**無AcroForm可填欄位，亦無法做PDF結構化座標
擷取**。依 Phase 5 明文允許之 FALLBACK 路徑：

> 「如果官方PDF不能可靠自動填：建立可重現的fallback。例如：Structured
> Output → Intermediate File → PDF，但需要清楚記錄：Automatic / Manual」

本階段之PDF輸出**全面採用Fallback路徑**：`FormCompletionResult`（Structured
Output）→ Jinja2 HTML（Intermediate File）→ PDF。每頁PDF頂部皆有明確
免責聲明區塊，說明本報表非官方書表原始版面複製。

## 技術選型：從 reportlab 到 weasyprint（含一項真實踩坑記錄）

### 首次嘗試失敗（誠實記錄，未隱藏）

依PDF skill建議之工具（reportlab）建立PDF，繁體中文採reportlab內建CID字型
`MSung-Light`（Adobe標準字型參照，理論上不需外部字型檔）：

```python
pdfmetrics.registerFont(UnicodeCIDFont('MSung-Light'))
c.setFont('MSung-Light', 14)
c.drawString(100, 700, '新北市金山區商業用地影響地價區域因素評價基準明細表')
c.save()
```

`pdftotext`擷取結果為空白，**進一步以`pdf2image`將PDF轉為圖片並直接視覺
檢視**（而非僅信任程式執行無錯誤即代表成功），確認**整頁完全空白，無任何
可見文字**——`MSung-Light`在本環境實際上並無對應之可渲染字型資源，屬於
reportlab已知限制（其內建CID字型設計上假設檢視端已安裝對應系統字型，但
poppler等常見PDF渲染器並無此字型）。此問題**僅能透過實際渲染成圖片視覺
檢查才會發現**，單純檢查程式是否報錯或PDF檔案是否非零位元組皆無法偵測。

### 改採 weasyprint

改用 `weasyprint`（HTML/CSS → PDF，透過系統字型渲染引擎如Cairo/Pango存取
真實安裝之字型）。系統確認已安裝 `Noto Sans CJK TC`（
`/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`）。以相同測試文字
重新產生PDF，**視覺渲染 + 文字擷取雙重驗證皆確認正確**：

```
pdftotext輸出: 表5-2 新北市金山區商業用地影響地價 區域因素分析明細表
              案號：1140901-99-001 區段編號：P002-00
```

自此，本專案所有PDF輸出**一律採用weasyprint**，不使用reportlab之CJK CID
字型路徑。

## 元件設計

| 元件 | 檔案 | 職責 |
|---|---|---|
| `PdfTemplate` | `pdf/pdf_template.py` | Jinja2 HTML樣板，含免責聲明區塊、CSS樣式 |
| `FormFieldMapping` | `pdf/form_field_mapping.py` | 將`FieldCompletion`分類至表單區塊，判定`AUTOMATIC`/`MANUAL` |
| `CoordinateMapping` | `pdf/coordinate_mapping.py` | 記錄欄位之**邏輯位置**（頁/區塊/列標籤），非像素座標（見下方說明） |
| `PdfRenderer` | `pdf/pdf_renderer.py` | 整合以上三者，呼叫weasyprint產出實際PDF bytes |

### 關於「座標」之誠實說明

由於官方PDF本身不可填、不可做結構化座標擷取，本系統之`CoordinateMapping`
**並非**像素/PDF-point座標，而是「邏輯位置」（此欄位出現在哪一表單的哪個
區塊、哪一列標籤之下）。此設計選擇已於`pdf/coordinate_mapping.py`模組
docstring中明確記載，不假裝擁有其實不存在的官方座標對應關係。

## Automatic / Manual 判定邏輯（`form_field_mapping.py::classify_fill_mode`）

| 判定依據 | 結果 |
|---|---|
| `field.status` 為 `MANUAL_REVIEW_REQUIRED` 或 `UNKNOWN` | `MANUAL` |
| `field.source` 含「AI輔助建議＋人工核定」或「使用者輸入」等標記 | `MANUAL` |
| 其餘（Rule/Adjustment/Calculation Engine deterministic輸出） | `AUTOMATIC` |

每個PDF表格列皆有色彩標示（`AUTOMATIC`綠色徽章／`MANUAL`橘色徽章），
`MANUAL`列額外套用淺黃底色，供審查人員一眼辨識何處需要人工判斷。

## Golden Case 產出驗證結果

| 檔案 | 頁數 | 檔案大小 | 開啟測試 | 文字擷取測試 |
|---|---|---|---|---|
| 表4+表5-2整合輸出 | 6頁 | 115,391 bytes | PASS（pypdf可讀取） | PASS（案號/區段編號/rule_id等關鍵字均可擷取） |
| 表1 | 2頁 | 83,973 bytes | PASS | PASS（「地價區段勘查表」「P002-00」可擷取） |

視覺渲染另以`pdf2image`轉圖後人工檢視確認：表格欄位排列整齊、
AUTOMATIC/MANUAL徽章清楚可辨、繁體中文完整正確顯示、無亂碼或空白區塊。

## 開發過程中發現並修正之真實排版缺陷

首次產出之PDF中，較長之`rule_id`字串（如
`IND-BUILDING_COVERAGE_RATIO_INDIVIDUAL-02`）於表格欄位內在連字號處自動
換行，導致`pdftotext`擷取出的文字被切成`IND-`與
`BUILDING_COVERAGE_RATIO_INDIVIDUAL-02`兩個不連續片段，使得針對rule_id
之文字比對測試失敗。已於CSS新增`.trace .rule-id { white-space: nowrap; }`
修正，確保完整識別碼在PDF中維持單行、可被完整文字擷取比對。

## 測試

`tests/test_phase5_golden_pipeline.py::TestFullPipelineThroughPdf`（6項）：
最終價格與Golden Case一致、PDF可開啟、頁數合理（2-20頁）、文字可見且可
擷取、BLK-01修正後之正確rule_id出現於PDF、表1 PDF內容正確、MANUAL欄位
於PDF中可見標示。
