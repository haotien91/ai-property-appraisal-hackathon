# OFFICIAL_PDF_VISUAL_VERIFICATION

**日期**：2026-09-11
**性質**：PDF-OFFICIAL-1 Task 12/13 — Golden Case 官方格式 PDF 之實際渲染
驗證與 Visual Regression。

---

## 0. FINAL GATE 更新（本輪）

上一輪遺留 2 個真實問題，經 200-400 DPI 局部放大截圖逐格檢視後發現並修正：

1. **`觀光遊憩設施` checkbox 列完全空白**：`regional_tourism_proximity`
   的 Golden 值為字串 `"區段內有"`（非數字），renderer 原本只接受數字
   raw_value 換算距離，非數字值被直接跳過（誠實但不完整）。修正：
   新增對字面字串 `"區段內有"` 的機械式判讀（→ 勾選「本區段內」，
   不顯示距離），僅此一個字面值，不做其他猜測。
2. **表4「合計」列個別因素調整合計欄位完全空白**：座標先前未測得。
   已測得精確 bbox 並對應至 `individual_adjustment_total_{cid}`。

兩項修正後重新產出 Golden Official PDF，200-400 DPI 局部放大截圖
（`docs/pdf/visual_verification/zoom_table1_tourism_parking.png`、
`zoom_table4_adjustment_total.png`）與整頁截圖確認：無跨欄、無重疊、
無截斷、checkbox 位置正確、中文正常、無殘留範例值。

```
TABLE1_VISUAL_VERIFIED=YES
TABLE52_VISUAL_VERIFIED=YES
TABLE4_VISUAL_VERIFIED=YES
CLEAN_TEMPLATE_RESIDUAL_SAMPLE_FOUND=NO
```

逐項 8 點檢查結果（見 §0.1-§0.8）：

| # | 檢查項目 | 表1 | 表5-2 | 表4 |
|---|---|---|---|---|
| 1 | 文字落在正確 cell | PASS | PASS | PASS |
| 2 | 覆蓋官方固定文字 | 無（0 起始即為修正項目） | 無 | 無 |
| 3 | 越過格線 | 無 | 無 | 無 |
| 4 | Clipping | 無（overflow policy 實測全程 0 筆觸發） | 無 | 無 |
| 5 | checkbox 位置正確 | PASS（7處，含本輪修正之觀光遊憩設施） | N/A（無 checkbox） | N/A |
| 6 | 中文正常 | PASS | PASS | PASS |
| 7 | 同欄位內容重疊 | 無 | 無 | 無 |
| 8 | Clean Template 殘留範例值 | 無（詳見 §0.2） | 無 | 無 |

### 0.1 殘留範例值檢查方法

以 `page.get_text()` 對 `data/templates/official_appraisal_form_v1.pdf`
（clean template，未 overlay 任何 Golden 值）逐頁擷取全文，人工比對
原始官方 PDF 之已知範例值字串（"都市計畫內"、"70%"、"240%"、"中山路"、
"金美段489地號"、"212,958" 等 30+ 組關鍵字），確認**全數不存在**於
clean template 頁面文字中（表1/表5-2/表4 三頁皆確認）。地圖附件
（頁3-5）之文字與原始官方 PDF 逐字相同（`tests/test_official_pdf_
output.py::TestOriginalTemplatePreserved::test_map_pages_3_4_5_byte_
identical_to_source` 自動化驗證，見 §3）。

---

## 1. 產出方式

100% 走真實、未修改之 production 引擎鏈：

```
RuleEngine + GradeEngine + AdjustmentEngine + CalculationEngine
  -> FormCompletionEngine.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)
  -> FormCompletionResult（json.loads(model_dump_json())["fields"]）
  -> official_pdf_renderer.render_official_pdf(
         GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, fields
     )
  -> PDF bytes（6 頁，含 3 頁未變更之地圖附件）
```

`official_pdf_renderer.py` 本身完全不 import `engine/` 任一模組（見該
檔案 module docstring）——上述引擎鏈完全由 `tests/test_official_pdf_
output.py` 的 fixture 自行執行後，才把**結果**交給 renderer，符合
Task 4「Engine Result → Form Completion Result → Official PDF
Renderer」資料流要求。

Golden Case 渲染結果：`render_official_pdf.last_overflow_fields == []`
（零個欄位觸發 FIELD_OVERFLOW_MANUAL_REVIEW）。

## 2. Page Screenshots（150 DPI PNG，PyMuPDF `get_pixmap()`）

| 檔案 | 內容 |
|---|---|
| `docs/pdf/visual_verification/clean_template_page{0,1,2}.png` | Clean Template（無範例值，僅供比對表格線/標籤是否被誤删） |
| `docs/pdf/visual_verification/golden_official_page{0,1,2}.png` | Golden Case 實際渲染結果 |

### 表1（page 0）視覺確認重點
- 都市計畫內／第二種商業區／建蔽率70%／容積率240%／有無禁止建築=無／
  有無限制建築=無：皆位於正確欄位，無跨欄溢出、無與相鄰欄位重疊。
- 主要道路「寬度：18M」正確落於「名稱：」右側欄位；「名稱：」本身維持
  空白（無資料來源，誠實留白，見 §5 已知限制）。
- 區段內道路平均寬度「12」與模板既有靜態「M」單位標籤無重複顯示（本輪
  除錯過程中發現並修正之真實 bug，見 §4）。
- 5 個 checkbox+距離設施（金融機構／百貨公司／娛樂設施／大型展示中心
  或觀光飯店／交流道）皆正確顯示「○本區段內 ●本區段外(距 N M)」，
  ●/○ 位置正確反映 raw_value 是否 >0。
- 表頭「新北市金山區」「P002-00」「北側至...東側至...」等識別欄位
  正確覆蓋，無與模板原始範例文字重疊（除錯過程中發現並修正，見 §4）。

### 表5-2（page 1）視覺確認重點
- 28 個區域因素列，比準地／比較標的1 優劣等級文字（優/稍優/普通/
  稍劣/劣）與修正百分比（0）皆正確落於各自欄位，無跨列/跨欄錯位。
- 主要道路寬度列：比準地=普通、比較標的1=普通（Golden Case 已知事實：
  18m，同一 P002-00 區段，見 `docs/audit/BLIND_CASE_PHASE5_REPORT.md`
  之 STATIC_18M_GRADE=普通 基準）——**逐字比對相符**。
- 比較標的2/3 欄位正確保持空白（Golden Case 僅 1 個比較標的，無資料
  可填，非程式錯誤）。

### 表4（page 2）視覺確認重點
- 基本資料：金美段489地號／溫泉段218地號（`base_parcel_id`／
  `comparable_id`）正確顯示。
- 宗地/道路/接近/周邊環境/行政 條件：方形、單面臨街、平坦、主要道路／
  次要道路、可路邊停車／不可路邊停車、商業區／商業區、建蔽率70%／70%、
  容積率240%／240%、有無禁限建「無禁止或限制建築」皆正確落於比準地／
  比較標的1 兩欄。
- 面前道路寬度差異率＝**5**（%）——與 `docs/audit/COMPETITION_E2E_
  PHASE5_REPORT.md` 記載之 Golden Case 已知事實「面前道路寬度18m vs
  6m → +5.00%」**逐字相符**。
- 土地正常單價184763、調整百分率2.00、調整至估價基準日單價188458.26、
  試算價格212957.8338、比較標的權重100、**比準地比較價格212958**——
  與既有 STEP5 報告記載之 Golden Case 最終價格**完全一致**。
- 表4「其他」「合計」列之「－」（NOT_APPLICABLE）標記維持模板原樣，
  未被替換為 0（Task 6/7 明文要求之驗證項目）。

## 3. Bounding-Box Tolerance 驗證（非僅像素比對）

`tests/test_official_pdf_output.py::TestRendererGoldenCase` 以
`page.get_text("words")` 取得每個字詞的實際座標，比對其是否落在
`data/templates/official_appraisal_form_v1.json` 所記錄之欄位 bbox
（±3pt 容差）內，而非僅檢查「文字有出現在頁面某處」。11 項測試全數
通過，涵蓋表1/5-2/4 三表之數值、checkbox 狀態、逐字已知 Golden 數值
（普通/5/212958 等）。

**已知限制（誠實記錄）**：本輪未建立逐 pixel diff 的自動化影像比對
（例如 SSIM/perceptual hash），僅有上述「bounding-box 內文字內容比對」
+ 人工目視 PNG 截圖比對兩種驗證方式。若未來需要偵測「數值正確但字體
渲染異常（如字重、字距跑版）」這類問題，需另外導入影像比對工具，
本輪未做。

## 4. 除錯過程中發現並修正之真實問題（非憑空假設）

以下皆為**實際渲染後從 PNG 截圖目視發現**的問題，逐一修正並重新驗證：

1. **表1標頭文字重疊**：`新北市金山區`／`P002-00`／區段範圍敘述文字
   原始範例值未被清除，新值疊加其上造成重疊——修正：於
   `scripts/build_clean_official_template.py` 新增 3 個 supplemental
   redaction 區域。
2. **區段內道路平均寬度「12M M」重複顯示單位**：renderer 誤將
   `unit="M"` 附加於已含固定「M」靜態標籤的欄位——修正：對此欄位特判
   不重複附加單位。
3. **表5-2優劣等級文字被截斷**（欄寬僅容納1個中文字，2字詞「稍優」
   等溢出）：`CELL_W_TEXT` 從 9.0pt 加大至 20.0pt。
4. **建蔽率／容積率於表1完全空白**：兩欄位原始值因符合數字/百分比
   regex 已被機械式分類器自動清除，但 profile 產生器遺漏將其納入
   欄位對應——已補上。

## 5. 已知限制（誠實記錄，非隱瞞）

見 `docs/pdf/OFFICIAL_TEMPLATE_SURVEY.md` §5 完整清單（建築密度/型態、
日照等無資料來源欄位；設施名稱類欄位；備註欄敘述文字；殯葬/電業/
廢棄物/水污染等一對多子列；填寫日期格式未轉換）。這些欄位在 Golden
Case 輸出中維持空白或原始格式，**不是**渲染失敗，而是 Structured
Result 本身確實沒有對應資料時的誠實留白（STOP/NO-GO 規則4）。
