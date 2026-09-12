# Phase 2 — Frontend Data Contract（前端資料契約）

> Part G。依主專案指示第18-19節（Read Existing Frontend First / Do Not Rebuild），
> 本階段**未修改** Makerthon_test_1 任何檔案，僅讀取檢視。本文件記錄現況並提供
> 未來 Phase（Phase 4「Form Completion Core + Frontend Inventory」／Phase 7
> 「Existing Frontend Integration」）串接時可直接引用的 Backend Field ID ↔ UI Label
> 對照規格。

## 第一部分：Makerthon_test_1 現況盤點（Fact Finding，非假設）

檢視範圍：`Makerthon_test_1/地價智審 AI/` 全部7個HTML頁面。

| 頁面 | `<title>` 現況 | 內容性質 |
|---|---|---|
| index.html | 「地價智審 AI｜AI 輔助不動產估價案件審查」（**已客製化**） | 單頁式Landing Page，含5個錨點區塊（#home #about #features #workflow #result），皆為行銷/概念說明文字，**無實際表單欄位** |
| about.html | 「Poseify - Modeling Agency Website Template」（原始模板，**未客製化**） | 通用模板頁 |
| service.html | 同上（未客製化） | 通用模板頁 |
| team.html | 同上（未客製化） | 通用模板頁 |
| testimonial.html | 同上（未客製化） | 通用模板頁 |
| contact.html | 同上（未客製化） | 含1個`<form>`標籤，為通用聯絡表單（姓名/信箱/訊息），非案件相關 |
| 404.html | 同上（未客製化） | 錯誤頁 |

**結論（Fact，非推論）**：目前 Makerthon_test_1 僅 index.html 之標題與導覽列
（首頁/系統介紹/核心功能/審查流程/審查結果）經過客製化，其餘6頁仍為原始
「Poseify」模特兒經紀公司範本，**未經修改**。index.html 內的「審查流程」
（#workflow）與「審查結果」（#result）區塊，內容為**行銷性質的靜態說明文字**
（例如「01建立與上傳」「02 AI擷取」等步驟說明、「22 Passed／2 Error／1...」
等示意數字），**並非串接後端資料的實際表單欄位或動態元件**。

**因此**：本階段**無法**從現有前端萃取出真正存在的「UI Label」來源，因為
case.html／case-new.html／data.html／form-fill.html／pdf-preview.html／
review.html／result.html（主專案指示第20節規劃之應用頁面）**尚未建立**。

依主專案指示第18節「Do Not Rebuild From Scratch」與第21節「Frontend禁止
Hardcode業務邏輯」，本階段**不**建立上述頁面（Phase 2 明確禁止大量前端修改），
僅將以下內容記錄為**規格（Spec）**，供 Phase 4/7 開發時直接引用，並標示其
分類為【團隊建議】（設計規格），非「現有前端已存在之對照」。

---

## 第二部分：index.html 現有行銷文案可沿用之UX措辭（可直接複用，非新建議）

index.html 現有文案已包含與本系統概念一致的措辭，Phase 4/7 開發實際功能頁面時
建議沿用，以維持文案一致性（依主專案指示第19節「優先延續既有...Typography」）：

| index.html 現有文案 | 對應概念 |
|---|---|
| 「01 建立與上傳」「建立案件與上傳書表」 | Case建立 + 書表上傳（本文件Step0-1，見business_process.md） |
| 「02 AI 擷取」 | Data Acquisition Layer |
| 「規則智慧核算：依行政區、用地類別與規則版本，自動判定級距、優劣等級及正確修正率」 | Rule Engine / Grade Engine / Adjustment Engine |
| 「Review Result...原始值、表單填載值、系統判定與規則依據」 | Smart Review之AuditIssue結構（submitted_value / expected_value / rule_id） |
| 「Passed / Error」（數字示意） | Smart Review之severity分類（Passed/Error/Warning/Missing/Inconsistent/Low Confidence，主專案指示第17節） |

---

## 第三部分：Backend Field ID ↔ UI Label 對照規格（【團隊建議】，供未來頁面開發用）

以下對照表以 `schemas/field_dictionary.json` 之 `field_id` 與 `chinese_label`
為基礎整理，供 Phase 4 建立 `form-fill.html` 等頁面時直接引用，避免重複翻譯
中英文欄位名稱。**此對照表不代表現有前端已實作，僅為規格文件。**

### 表1 地價區段勘查表（部分代表性欄位，完整清單見 field_dictionary.json）

| field_id | ↔ | UI Label（中文） | 建議UI元件 |
|---|---|---|---|
| segment_code | ↔ | 區段編號 | 唯讀文字（決賽當日預填） |
| segment_scope | ↔ | 區段範圍 | 唯讀多行文字（決賽當日預填） |
| zoning_inside_outside | ↔ | 都市計畫(內外) | 單選按鈕 |
| land_use_zone | ↔ | 使用分區(使用地類別) | 下拉選單（依當次評價基準明細表動態載入選項） |
| building_coverage_ratio | ↔ | 建蔽率 | 數字輸入（%） |
| floor_area_ratio | ↔ | 容積率 | 數字輸入（%） |
| main_road_name / main_road_width | ↔ | 主要道路 名稱／寬度 | 文字輸入＋數字輸入（M） |
| segment_avg_road_width | ↔ | 區段內道路平均寬度 | 數字輸入（M） |
| cemetery_name / cemetery_within_segment / cemetery_distance_m | ↔ | 墓地 名稱／本區段內外／距離 | 文字輸入＋單選（本區段內/外）＋數字輸入（M，本區段外時顯示） |
| drainage_quality | ↔ | 保(排)水之良否 | 下拉選單（5級描述） |
| terrain | ↔ | 地勢 | 下拉選單（5級描述） |
| customer_traffic_volume | ↔ | 顧客之通行量 | 下拉選單（5級描述） |
| shop_contiguity | ↔ | 店舖之毗連狀態 | 下拉選單（5級描述） |

### 表5-2 影響地價區域因素分析明細表（欄位樣式統一，以下為代表列）

| field_id 樣式 | ↔ | UI Label樣式 | 建議UI元件 |
|---|---|---|---|
| regional_<factor>_grade_base | ↔ | ＜因素名稱＞－優劣等級(比準地) | 唯讀（Rule Engine自動判定結果，不可手動覆寫） |
| regional_<factor>_grade_comparable_n | ↔ | ＜因素名稱＞－優劣等級(比較標的N) | 唯讀（Rule Engine自動判定結果） |
| regional_<factor>_adjustment_pct | ↔ | ＜因素名稱＞－修正百分比 | 唯讀（Adjustment Engine計算結果，人工僅可於備註欄註記理由後申請調整） |
| regional_total_adjustment | ↔ | 影響地價區域因素總修正數 | 唯讀（Calculation Engine加總結果，Cross-form核心檢查點，需與表4欄位一致並標示比對狀態） |

### 表4 比較法調查估價表（代表列）

| field_id | ↔ | UI Label | 建議UI元件 |
|---|---|---|---|
| land_normal_price_n | ↔ | 土地正常單價 | 唯讀（承接買賣實例調查估價表） |
| price_date_adjustment_rate_n | ↔ | 調整百分率(價格日期) | 數字輸入（AI建議值＋估價師可調整） |
| adjusted_price_n | ↔ | 調整至估價基準日單價 | 唯讀（計算結果；UI應提示：此為顯示用四捨五入值，內部計算不使用此顯示值，見calculation_dependency.md） |
| region_adjustment_rate_n | ↔ | 區域因素調整百分率 | 唯讀（承接表5-2，若與表5-2不一致應顯示Cross-form Inconsistent警示） |
| individual_adjustment_total_n | ↔ | 個別因素調整合計 | 唯讀（加總結果） |
| price_formation_similarity_n | ↔ | 價格形成因素之相近程度 | 下拉選單（較高/普通/較低，AI建議＋估價師核定） |
| comparable_weight_n | ↔ | 比較標的權重 | 數字輸入（%，AI建議＋估價師核定，僅1筆比較標的時鎖定100%） |
| base_parcel_comparison_price | ↔ | 比準地比較價格 | 唯讀（最終輸出，四捨五入至個位數） |

---

## 第四部分：API 資料流建議（呼應主專案指示第22節）

依主專案指示，Frontend 應透過統一 `js/api.js` 呼叫 Backend API，本階段建議
Response Schema 之欄位命名**直接沿用** `field_id`（例如API回傳 `{"main_road_width":
18, "main_road_width_unit": "M"}`），避免前端另建一套獨立命名，造成
Backend↔Frontend欄位對照混亂。此建議屬【團隊建議】，Phase 4/7 實作API時可直接
採用，亦可依實際框架需求調整，非強制規格。

---

## 待確認 / 已知限制

1. Makerthon_test_1 目前無任何案件管理／表單填寫頁面，本文件第三部分之UI元件
   建議純屬 Phase 2 之前瞻設計規格，尚未經過 Phase 4/7 實際開發驗證，亦未經
   使用者確認是否符合實際UI/UX需求。
2. 依主專案指示第19節，實際開發時應「優先延續既有Dark Theme／Pink Purple
   Accent／Bootstrap Components」，本文件之建議UI元件（下拉選單/數字輸入等）
   僅描述資料型態對應之元件類型，未指定實際CSS class或視覺樣式，後者應於
   Phase 4/7 依 Makerthon_test_1 既有CSS重新設計，本階段不代為決定。
3. 本文件不構成對 Makerthon_test_1 之任何修改，所有記錄均為唯讀檢視結果。
