# Phase 8A — Error Case 展示資料

> 全部案例皆以Golden Case真實驗證值為基準（見`docs/phase3/rule_engine_spec.md`），
> 故意植入之錯誤值與偵測結果，本輪皆由系統重新執行取得（非手動編造）。

## Case A — Grade Error（優劣等級錯誤）

| 項目 | 值 |
|---|---|
| 因素 | 主要道路寬度（區域因素） |
| 真實原始值 | 18M |
| 正確等級（Source驗證） | 普通 |
| 故意提交之錯誤等級 | 優 |
| 系統判定 | **Error / GRADE_ERROR / CRITICAL** |
| Downstream Impact | region_adjustment_rate → trial_price → base_parcel_comparison_price |

## Case B — Adjustment Error（修正率錯誤）

| 項目 | 值 |
|---|---|
| 因素 | 深度（個別因素） |
| 真實原始值 | 比準地23m（普通）／比較標的16m（稍劣） |
| 正確差異率（矩陣查表） | 1.00% |
| 故意提交之錯誤值 | 5.00% |
| 系統判定 | **Error / ADJUSTMENT_ERROR / CRITICAL** |
| Downstream Impact | individual_adjustment_total → trial_price → adjustment_abs_sum → base_parcel_comparison_price |

## Case C — Cross-form Inconsistent（跨表不一致，本輪重新驗證，含資料模型修正後之真實獨立比對）

本輪實際重新執行結果（僅竄改表5-2一側，表4完全不動）：

```
issue_type: Inconsistent
severity: CRITICAL
source_form: 表4
field: region_adjustment_rate_comp1
submitted_value: 0
expected_value: 3.00
explanation: 跨表不一致：表5-2「影響地價區域因素總修正數」=3.00%，
             表4「區域因素調整百分率」=0%，兩者應逐字相符但不一致
downstream_impact: [trial_price_comp1, adjustment_abs_sum_comp1,
                     base_parcel_comparison_price]
```

**技術可信度說明**（Demo中若被追問可引用）：此檢查比對之兩個值——表5-2
「影響地價區域因素總修正數」與表4「區域因素調整百分率」——在系統資料
模型中是**兩個獨立儲存之欄位**（field_id分別為
`regional_total_adjustment_{cid}`與`region_adjustment_rate_{cid}`），
非同一數值被讀取兩次冒充比對。系統已通過**雙向驗證**：僅竄改表5-2側
可被偵測、僅竄改表4側同樣可被偵測（見`tests/test_backend_handlers_e2e.py::TestCrossFormIndependentFieldIdentity`），
證明並非簡單的「自己比對自己」。

## 完整性佐證：正常案例0誤報

三個錯誤案例皆為刻意植入於原本完全正確的45個欄位案例中，系統對其餘
45個欄位全數正確判定為Passed，證明偵測邏輯本身具備真正的判別力
（能區分「真的錯」與「沒有錯」），而非無差別報錯或無差別放行。

## 額外壓力測試（非事先設計案例，證明非過度擬合）

除上述3個Demo案例外，過程中曾以第4個未事先規劃之錯誤（停車方便性差異率
誤填99.00，真實值應為2.00）進行盲測，系統同樣正確偵測，證明偵測邏輯為
通用比對機制，非針對特定案例硬編碼。
