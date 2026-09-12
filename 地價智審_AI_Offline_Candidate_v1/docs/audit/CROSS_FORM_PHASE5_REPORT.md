# CROSS_FORM_PHASE5_REPORT

**日期**：2026-09-11
**性質**：STEP 5 §10（Cross-Form Tamper Detection A-E）與 §11（Smart
Review 五大驗證領域）。

---

## 1. 既有能力盤點（修改前）

`engine/cross_form_validation_engine.py`（未修改，非 Core Freeze 但本輪
無需改動其邏輯）原有兩個方法：

- `validate_regional_total_matches_table4()`：表5-2 影響地價區域因素
  總修正數 vs 表4 區域因素調整百分率，兩個**獨立儲存**欄位比對。
- `validate_field_pair()`：通用兩欄位比對（任意 label/field_id）。

`engine/audit_engine.py::AuditEngine.review()`（修改前）僅呼叫前者一次
（案件層級 case identity／comparable identity／price subtotal 完全未
檢查）。

## 2. 本輪新增（§10 要求的完整清單：case identity/segment identity/
comparable identity/regional grade/regional adjustment total/
individual adjustment/expected vs submitted/price subtotal/final
trial price）

| §10 要求項目 | 涵蓋方式 | 是否新增 |
|---|---|---|
| regional grade | `RuleValidator.validate_grade_identity()` | 既有
  （STEP Phase D 起已存在） |
| regional adjustment total | `validate_regional_total_matches_
  table4()` | 既有 |
| individual adjustment | `CalculationValidator.validate_adjustment()` | 既有 |
| expected vs submitted | 上述所有 Validator 皆為此模式（見 §4） | 既有 |
| **case identity** | `_field_pair_or_missing()` 新呼叫 `validate_
  field_pair()`，比對 `case.case_no`（review 時重建）vs `form_
  completion["case_no"]`（complete_form 時戳記） | **新增** |
| **comparable identity** | 比對表5-2欄位命名空間
  （`regional_total_adjustment_{cid}`）vs 表4欄位命名空間
  （`region_adjustment_rate_{cid}`）各自出現的 comparable_id 集合 | **新增** |
| **price / subtotal / final trial price** | `_review_final_trial_
  price_subtotal()`，重用既有 `CalculationValidator.validate_sum()`：
  `sum(trial_price[cid] * weight[cid] / 100)` vs
  `base_parcel_comparison_price` 欄位自身儲存的**未四捨五入**原始值 | **新增** |
| segment identity | 見 §3「誠實記錄之已知限制」 | 未新增（見下） |

三項新增皆位於 `engine/audit_engine.py::AuditEngine.review()`，全部
透過既有 `CrossFormValidationEngine.validate_field_pair()` 或
`CalculationValidator.validate_sum()` 組裝，**沒有新增任何獨立驗證
邏輯**——符合 Core Freeze 精神（雖然 `AuditEngine`／
`CrossFormValidationEngine` 不在凍結清單內）。

新增 `_field_pair_or_missing()` 防呆包裝：兩側任一為 `None`（尚未由
呼叫端填入，例如 DOCUMENT submission_source 路徑本輪未擴充填入這三個
新欄位）時，誠實回報 `IssueType.MISSING`，而非讓 `validate_field_pair()`
把 `None` 字串化成 `"None"` 後誤判為 PASSED（雙方皆缺）或 INCONSISTENT
（單方缺）——嚴格遵守 land-appraisal-audit skill 之「Three-State
Verification Outcome Semantics：cannot verify 絕不能長得像 confirmed
mismatch」。

## 3. 誠實記錄之已知限制：segment identity

§10 要求之「segment identity」（表1↔表5-2↔表4 之區段編號一致性）
**未**新增獨立檢查：目前資料模型中，`FormCompletionEngine` 的輸出
（`FORM_COMPLETION` 記錄）並未把 `segment_code` 當作欄位戳記於任何
`FieldCompletion`；表1（`build_table1_pdf_bytes`）與表4+表5-2
（`render_form`）目前皆從**同一個** `case_reconstruction.build_case_
and_regional_factors()` 讀出的 `meta["segment_code"]`／
`case.segment_code` 取值，並非兩個獨立填寫來源。若在此加入一個
「比對」，會違反 land-appraisal-audit skill 明文禁止的「不得比較
同一欄位自己跟自己」（tautological check，what looks like a check but
never fails）。要讓 segment identity 成為真正獨立可驗證的檢查，需要
修改 `FormCompletionEngine`（Core Freeze 清單成員）讓它在輸出中也戳記
一份 `segment_code`——依 §0 規定，此改動需要明確的 integration bug
才可進行，而目前並無證據顯示 segment_code 在任何真實路徑上會不一致
（它是案件層級的單一權威值，不像 comparable_id／case_no 存在「複數
記錄各自填寫」的真實場景）。誠實記錄為已知、可解釋的範圍限制，而非
遺漏。

## 4. §10 A-E 測試結果

`tests/test_competition_cross_form_tamper_and_failure.py::
TestCrossFormTamperDetection`（5 tests, all passed）：

| Scenario | 測試 | 竄改方式 | 結果 |
|---|---|---|---|
| A. 正常 Golden | `test_a_normal_golden_all_cross_form_checks_pass` | 無 | 全部 PASSED（含新增的 case/comparable identity） |
| B. 表5-2 regional total 被竄改 | `test_b_table52_regional_total_
  tampered` | 直接改寫已儲存 FORM_COMPLETION 記錄的
  `regional_total_adjustment_comp1.final_value` | INCONSISTENT |
| C. 表4 使用錯誤 regional value | `test_c_table4_uses_wrong_regional_
  value` | 改寫**另一側** `region_adjustment_rate_comp1.final_value`
  （表5-2 側保持不變） | INCONSISTENT（證明雙側皆獨立可偵測，非寫死
  方向） |
| D. Expected / Submitted 分離 | `test_d_expected_vs_submitted_never_
  tautological` | 竄改個別因素差異率至 999.99 | ERROR，且
  `expected_value`（真實 matrix 推導值）≠竄改後的
  `submitted_value`，兩者也互不相等——非同義重複比較 |
| E. Case rule changed but downstream form remains old value | `test_e_
  case_rule_changed_but_downstream_form_stays_old` | complete_form 於
  靜態基準下完成後（18m→普通），**之後**才確認 Blind 規則（18m→稍優），
  刻意不重跑 complete_form | Review 重新從**當前 CONFIRMED**
  規則推導 expected（稍優/grade_code=2），與表單上仍殘留的舊值
  （普通/grade_code=3）比對 → ERROR/INCONSISTENT，`rule_source_
  type="CASE_IMPORTED_CONFIRMED"` |

```
CROSS_FORM_VALIDATION=PASS
CROSS_FORM_TAMPER_DETECTION=PASS
```

## 5. §11 Smart Review 五大領域

| 領域 | 元件 | 涵蓋 |
|---|---|---|
| RULE VALIDATION | `RuleValidator` | grade 正確性、grade 表述一致性 |
| FORM VALIDATION | `LandUseRatioValidator`／`RoadWidthValidator` | 表1 欄位 |
| CROSS-FORM VALIDATION | `CrossFormValidationEngine` + 本輪新增 3 項 | 見 §2 |
| CALCULATION VALIDATION | `CalculationValidator` | 差異率／加總 |
| DATA COMPLETENESS | 上述皆有 MISSING 分支 | 見 IssueType |

六種 issue type（ERROR/WARNING/MISSING/INCONSISTENT/LOW_CONFIDENCE/
PASSED）皆已於既有 `domain.models.IssueType` 定義並使用，本輪未新增
或修改。

**Central maximum 未確認之誤報防護**：本輪未新增任何直接呼叫 central
maximum 表格產生 grade 的路徑；`LEGAL_BASIS_UNCONFIRMED`／
`MANUAL_REVIEW_REQUIRED` 語意延續 STEP4 既有設計（`RULE_COVERAGE_
MATRIX.md`），未於本輪變更。
