# OFFICIAL_TEMPLATE_SURVEY

**日期**：2026-09-11
**性質**：PDF-OFFICIAL-1 Task 1 — 官方查估書表範本（唯讀）盤點。

---

## 1. 檔案基本資訊

| 項目 | 值 |
|---|---|
| 檔案路徑 | `data/sources/competition/查估書表範本.pdf` |
| 檔案大小 | 2,677,025 bytes |
| SHA256 | `2e6c16ebac75dce1a12515454237c825f0b688c1788b816ae48a50c637f67bb5` |
| PDF 版本 | 1.6（magic bytes `%PDF-1.6`，標準、有效 PDF） |
| 總頁數 | **6** |
| AcroForm 欄位 | **0**（每頁 `page.widgets()` 皆為空——非可填寫表單 PDF，確認題目描述屬實） |
| 加密/權限限制 | 無（`doc.is_encrypted == False`） |

**重要更正**：`pdf/coordinate_mapping.py` 現有註解宣稱此檔案「is a ZIP-encapsulated
image+text bundle...fails with 'invalid pdf header: PK'」。本輪以 PyMuPDF
實際開啟、讀取文字座標、擷取字型皆完全成功，且 magic bytes 確認為標準
`%PDF-1.6`，並非 ZIP。此差異可能是該註解撰寫當時使用了不同版本的檔案，
或原始判斷有誤；本輪**未修改**該註解本身（`coordinate_mapping.py` 屬既有
Audit PDF 架構的一部分，其「Logical, not pixel/point coordinates」設計
本身仍然正確且應保留——本輪新增的是一套**平行、獨立**的 Official PDF
Overlay 機制，不影響、不取代既有 Audit PDF 路徑），僅在此誠實記錄此項
發現供後續參考。

## 2. 每頁尺寸與內容

| Page Index | 尺寸 (pt) | 方向 | Rotation | 內容 |
|---|---|---|---|---|
| 0 | 595.2 × 841.68 | A4 直向 | 0 | **表1 地價區段勘查表** |
| 1 | 595.2 × 841.68 | A4 直向 | 0 | **表5-2 影響地價區域因素分析明細表（商業用地）** |
| 2 | 841.68 × 595.2 | A4 橫向 | 0 | **表4 比較法調查估價表** |
| 3 | 1190.4 × 841.68 | A3 橫向 | 0 | 地價區段略圖（地圖附件，無表單欄位） |
| 4 | 1190.4 × 841.68 | A3 橫向 | 0 | 地價使用分區圖（地圖附件，無表單欄位） |
| 5 | 1190.4 × 841.68 | A3 橫向 | 0 | 地價區段圖（地圖附件，無表單欄位） |

MediaBox 與 CropBox 完全一致（無額外裁切差異）；`page.rotation` 皆為 0
（無需在座標系統中額外處理旋轉）。

## 3. 是否可用 PyMuPDF 讀取並 Overlay

**確認可行**。`page.get_text("rawdict")` 可精確取得每個文字 span、甚至
每個「字元」的 bounding box（見 §5），字型為內嵌（embedded）TrueType
子集 `DFKaiShu-SB-Estd-BF`（標楷體），`Identity-H`/`WinAnsiEncoding`
兩種編碼皆有使用。`page.draw_rect()`（白色填色，用於清除範例值）與
`page.insert_text()`/`page.insert_textbox()`（寫入新值）皆已於本輪
`pdf/official_pdf_renderer.py` 實測成功產出完整、可重新開啟、文字可
被正確擷取的 PDF（見 `docs/pdf/OFFICIAL_PDF_VISUAL_VERIFICATION.md`）。

## 4. 重要欄位錨點（節錄，完整清單見 `data/templates/
official_appraisal_form_v1.json`）

### 表1（page 0）
| 欄位 | 範例值 bbox (x0,y0,x1,y1) | Structured Result 來源 |
|---|---|---|
| 都市計畫(內外) | [152.66, 105.23, 182.54, 111.11] | `regional_base_factors` 之 `regional_zoning_inside_outside` |
| 使用分區(使用地類別) | [152.66, 119.87, 188.54, 125.75] | `regional_land_use_zone` |
| 建蔽率 | [152.66, 133.5, 175, 140.39] | `regional_building_coverage_ratio` |
| 容積率 | [152.66, 148.15, 178, 155.03] | `regional_floor_area_ratio` |
| 主要道路寬度 | [241.1, 207.74, 300, 213.62] | `regional_main_road_width` |
| 區段內道路平均寬度 | [151.57, 222.38, 163.0, 228.26] | `regional_avg_road_width` |
| 金融機構／百貨公司／娛樂設施／大型展示中心或觀光飯店／交流道（checkbox+距離） | 各自獨立 bbox | 對應 `regional_*_proximity` 的 raw_value（數字=距離） |

### 表5-2（page 1）—— 系統性列（row）結構
28 個區域因素，逐列由左至右：因素名稱（固定，x=99.38）→ 比準地優劣等級
（x=213.77 代碼／x=237.77 文字）→ 比較標的1優劣等級（x=267.77／294.41）→
修正百分比（x=322.99）→ 比較標的2/3（各 +91.58pt 位移）。行高
11.76pt（單行列）；跨行標籤（如「有無限制建築...」）佔用對應倍數行高。
完整 28 列清單與對應 `field_id`（如 `regional_main_road_width`）列於
`data/templates/official_appraisal_form_v1.json` 之 `table5_2.fields`。

### 表4（page 2）
`比準地`/`比較標的1` 兩欄（`比較標的2`/`3` 版面存在但 Golden Case 僅用
1 個比較標的，故本輪未實測其座標）。宗地/道路/接近/周邊環境/行政 五類
條件、每列一個 `差異率` 儲存格（x=451.03-469.49）。完整清單見
`table4.fields`。

## 5. 是否存在需人工確認之區域（TEMPLATE_CLEANUP_MANUAL_REQUIRED）

**FINAL GATE 更新**：`觀光遊憩設施`（`regional_tourism_proximity`）與
`停車場地`（`regional_parking_convenience`）於本輪重新盤點後確認為**單一、
不含糊**的 checkbox+距離列，已補上對應與渲染邏輯（含 `raw_value="區段內
有"` 此類非數字字串的正確 checkbox 判讀）；`個別因素調整合計`
（`individual_adjustment_total`，表4「合計」列）座標亦已測得並補上。
以下清單已移除這三項，僅保留**真正**因「一對多」版面結構或「無資料
來源」而無法安全完成的項目：

| 區域 | 原因 |
|---|---|
| 表1「建築密度」「建築型態」（連棟透天厝、公寓） | Structured Result 無對應欄位（`regional_base_factors` 無此二因素） |
| 表1「日照」「景觀」「領斜度」「風勢」「土質」 | 同上，無資料來源，且範例本身即為空白 |
| 表1 多數「公共設施」子項（大型車站之高鐵/火車/捷運子分類、站牌之
  密集程度三態、殯葬設施之墓地/殯儀館/火葬場/納骨塔4個子列、電業設施
  之變電所/瓦斯槽2個子列、廢棄物處理3個子列、水污染等6個子列） | 單一
  regional factor（如「殯葬設施之有無及接近程度」）對應模板上**多個**
  子列，本輪不猜測應勾選哪一個子列——已在 clean template 中清除範例值
  （避免顯示舊資料），但不會自動填入 |
| 表4 面前道路/學校/市場/公園/車站/商圈/嫌惡設施之「名稱」 | `FactorInput`
  僅有距離數字（如 `individual_school_proximity.raw_value=150`），
  無設施名稱欄位 |
| 表4 備註欄（比準地或各比較標的／全案，敘述性文字段落） | 無對應
  Structured Result（`FormCompletionEngine` 僅計算數值，不產生敘述文字） |
| 表4「填寫日期」格式 | Structured Result 的 `appraisal_base_date`
  為 ROC 純數字格式（如 "1140901"），模板原始格式為「114 年 09 月 18
  日」——本輪未做格式轉換，直接顯示原始字串（誠實但非模板原格式） |

以上皆為**明確記錄、非猜測**的已知限制，未來輪次可視需要擴充。

## 6. Template SHA256（供 Task 10 指紋比對）

- 原始官方 PDF：`2e6c16ebac75dce1a12515454237c825f0b688c1788b816ae48a50c637f67bb5`
- Clean Template（`data/templates/official_appraisal_form_v1.pdf`）：
  見 `data/templates/official_appraisal_form_v1.json` 之
  `clean_template_sha256`（每次重新產生 clean template 皆會變動，
  以該 JSON 檔案記錄的值為準）。

## 7. Extraction Notes

- 文字擷取採用 `page.get_text("rawdict")`（非 `"dict"` 或純文字模式）
  以取得**字元層級**（非僅 span 層級）bounding box——這是能夠精確
  切分「標籤：值」同一 span（如「名稱：中山路」）、以及精確定位單一
  checkbox 字元（○/●/□）的關鍵。
- 純文字模式（`page.get_text()`，stream order）在此 PDF 的**部分頁面**
  （尤其 page 2／表4）順序與視覺呈現不符（例如表4標題出現在文字流
  尾端而非開頭）；`"rawdict"` + 依 `(y0, x0)` 排序後的視覺順序則完全
  正確、可靠，本輪所有座標萃取皆以此為準。
- Checkbox 字元：○（U+25CB，未勾選，96次）、●（U+25CF，已勾選，19次）、
  □（U+25A1，另一種未勾選樣式，17次，用於「土地改良」等少數段落）。
