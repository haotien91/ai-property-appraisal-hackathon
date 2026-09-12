# OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 — 驗證與修正報告

## Core Freeze 遵循

本輪修改：`pdf/official_pdf_renderer.py`（map identity 檢查＋date wiring，
未動任何估價邏輯）、`scripts/build_clean_official_template.py`（僅重新命名
一個 field_id 標籤，redaction bbox/fill 完全不變）、
`scripts/build_official_template_profile.py`（新增1個 header 欄位對應）、
`tests/test_official_pdf_output.py`（新增3個測試類別）。**未修改**
Rule/Grade/Adjustment/Calculation/Form Completion/GIS Engine、
Road Width Resolver、任何 rules／adjustment matrices 檔案。

## TASK 1/2 — Tighten Golden Map Identity

舊版 `_case_matches_template_map_identity()` 僅比對
`segment_code + city + district`——同一 P002-00 區段內理論上可存在不同
案件（不同比準地/比較標的），而範本地圖明確含 Golden Case 專屬標註
（金美段489地號／溫泉段218地號），故「同區段」≠「同圖資身份」。

修正後身份檢查（`domain/models.py::CompetitionCase` 之
`case_no`／`city`／`district`／`segment_code`／`base_parcel_id`／
`comparable_ids` 皆為必要欄位，非「if available」的選填欄位，故全數
納入判斷，非僅在「有提供時才檢查」）：

```python
def _case_matches_template_map_identity(case) -> bool:
    return (
        case.case_no == "1140901-99-001"
        and case.city == "新北市"
        and case.district == "金山區"
        and case.segment_code == "P002-00"
        and case.base_parcel_id == "金美段489地號"
        and list(case.comparable_ids or []) == ["溫泉段218地號"]
    )
```

```
MAP_IDENTITY_REQUIRES_CASE_NO=YES
MAP_IDENTITY_REQUIRES_SEGMENT=YES
MAP_IDENTITY_REQUIRES_LOCATION=YES
MAP_IDENTITY_REQUIRES_PARCEL_WHEN_AVAILABLE=YES（base_parcel_id／
  comparable_ids 在此網域模型上恆為必要欄位，非選填，故一律檢查）
```

## TASK 3 — Same-Segment Different-Case Fixture

新增 `tests/test_official_pdf_output.py::TestSameSegmentDifferentCaseMapSafety`：
複製 Golden Case，僅將 `case_no` 換成 `"9999999-88-777"`（city/district/
segment_code/base_parcel_id/comparable_ids 全部維持與 Golden Case相同）。
渲染後對 page 3/4/5 逐頁確認：

```
SAME_SEGMENT_DIFFERENT_CASE_RECEIVES_GOLDEN_MAPS=NO
```

無 `"1140901-99-001"`／`"金美段489地號"`／`"溫泉段218地號"` 殘留，
`get_images()==0`（底圖影像確實移除），顯示「圖資待人工附具」誠實提示。

## TASK 4 — Different Parcel Identity Test

`domain/models.py::CompetitionCase.base_parcel_id`／`comparable_ids` 為
必要欄位，非選填：

```
PARCEL_IDENTITY_NOT_AVAILABLE_FOR_MAP_GATE=NO（本網域模型上皆為必要且已
  驗證存在的欄位）
```

新增 `TestDifferentParcelIdentityMapSafety`：複製 Golden Case，僅將
`base_parcel_id` 換成 `"金美段999地號"`（case_no/segment/city/district
全部保持 Golden Case 原值）——即使如此，圖資仍必須被拒絕保留，因為
`_case_matches_template_map_identity()` 現在同時檢查 base_parcel_id。
測試確認：page 3/4/5 皆不含 `"金美段489地號"`，`get_images()==0`，
顯示「圖資待人工附具」。

## TASK 5/6 — 表4 日期語意修正

追蹤發現：原範本頁首「估價基準日：」儲存格 bbox（[542.86,32.13,570.94,
41.01]）之範例值為 `"1140901"`，與 `case.appraisal_base_date` **完全相符**
——但此欄位從未被 profile 對應，`case.appraisal_base_date` 卻被錯誤寫入
頁尾「填寫日期：」儲存格（範例值 `"114 年 09 月 18 日"`，與頭部日期本就
不同，證明兩者原本就是不同概念）。

搜尋 domain/models.py 全庫，確認**不存在**任何
fill_date／completion_date／form_date／review_date 或等價「填寫日期」
概念欄位。

修正：
- `build_official_template_profile.py::build_table4()` 新增
  `fields["appraisal_base_date"] = {"bbox": [542.86,32.13,570.94,41.01], "kind":"text"}`
  （頁首儲存格，取代舊的頁尾對應）。
- `build_clean_official_template.py` 將頁尾儲存格之 field_id 由
  `"appraisal_base_date"`（錯誤命名）改為 `"form_fill_date"`——redaction
  bbox／fill color 完全不變，僅重新命名標籤，clean template 視覺內容不變
  （已以 `verify_no_residual_text.py`／artifact finder 雙重確認）。
- `pdf/official_pdf_renderer.py` 之 `direct_case_fields` 完全不變（原本
  就正確寫 `case.appraisal_base_date`，問題只在於它對應到錯的 bbox）；
  `form_fill_date` 未加入 `direct_case_fields`，故永遠保持空白。

```
APPRAISAL_BASE_DATE_SOURCE=case.appraisal_base_date（"1140901"）
APPRAISAL_BASE_DATE_RENDERED_TO_CORRECT_FIELD=YES（表4頁首「估價基準日」）
FILL_DATE_SOURCE_EXISTS=NO
FILL_DATE_RENDERED=NO（保持空白）
APPRAISAL_BASE_DATE_USED_AS_FILL_DATE=NO
```

## TASK 7 — 視覺驗證（400 DPI）

重新產生 Golden Official PDF：表4頁首「估價基準日：1140901」正確顯示；
頁尾「填寫日期：」右側確認為空白（`page.get_text("words")` 於該 bbox
±3pt 範圍內無任何字元）。已寫入 `test_appraisal_base_date_goes_to_
header_not_fill_date_footer` 測試鎖定此行為。

## TASK 8 — 表4 Field-Level 完整重新計算

以「監聽真實 renderer 的 `_fit_text` 呼叫」方式（非人工猜測）對照
Golden Case 實際渲染結果與 profile 全部80個 table4 欄位 key 逐一比對：

```
TABLE4_FIELD_TOTAL=80
TABLE4_FIELD_SOURCE_AVAILABLE_RENDERED=63
TABLE4_FIELD_SOURCE_AVAILABLE_NOT_RENDERED=0
TABLE4_FIELD_SOURCE_NOT_AVAILABLE=15
  （form_fill_date ×1；面前道路/學校/市場/公園/車站/商圈/嫌惡設施
  共7項設施 .name[base]/[comp] ×14 = 15，網域模型無對應「名稱」概念）
TABLE4_FIELD_MANUAL_REQUIRED=2
  （table4_remarks_case／table4_remarks_comparable——備註欄本質為人工
  敘述性文字，非結構化資料缺口）
TABLE4_FIELD_COUNT_RECONCILED=YES（63+0+15+2=80）
```

已寫入 `TestTable4FieldLevelReconciliation` 測試，透過監聽真實
`_fit_text()` 呼叫鎖定此分類，未來任何欄位增減若未同步更新分類會使測試
失敗（不會悄悄漂移）。

## TASK 9 — 不得捏造名稱

上述15個 SOURCE_NOT_AVAILABLE 欄位（設施名稱／填寫日期）本輪確認皆保持
空白，未因 Golden Case 範本例圖曾顯示過名稱文字而回填。

## TASK 10 — Regression

```
python -m pytest tests/test_official_pdf_output.py -q
  => 38 passed, 1 skipped（新增：TestTable4FieldLevelReconciliation 1項、
     TestSameSegmentDifferentCaseMapSafety 1項、
     TestDifferentParcelIdentityMapSafety 2項、日期語意測試1項；
     皆PASS，無新增skip）

python -m pytest tests/test_facility_confirmation.py -q
  => 43 passed

python -m pytest tests -q --ignore=tests/test_phase5_golden_pipeline.py
  => 見下方 FINAL REPORT（背景執行完整結果）
```

未新增任何 skip 標記掩蓋新錯誤。
