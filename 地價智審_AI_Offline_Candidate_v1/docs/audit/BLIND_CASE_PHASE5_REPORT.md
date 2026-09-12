# BLIND_CASE_PHASE5_REPORT

**日期**：2026-09-11
**性質**：STEP 5 §14-§19 — 本輪最重要測試。建立真正不同規則的 Blind
Case，證明新評價基準真的會改變 Grade 與 Adjustment Rate，且不同案件間
完全不互相污染。

---

## 1. Fixture 設計

`tests/fixtures/competition/blind_case_changed_standard/blind_evaluation_standard.json`
（唯一存放位置，符合 §19：Blind-specific 資料僅存在於
`tests/fixtures/`／`tests/`／`docs/audit/`，從未進入 `engine/`／
`backend/handlers/`）。

**因素**：`主要道路寬度`（regional scope，與 Golden Case 同一真實因素，
非發明新因素）——刻意選用與靜態基準**同一個真實因素**，才能誠實證明
「同一輸入、不同標準、不同結果」，而非比較兩個不相干的東西。

| grade_code | grade | 靜態基準（regional_rules.json） | Blind fixture |
|---|---|---|---|
| 1 | 優 | ≥30m | ≥30m |
| 2 | 稍優 | 20–<30m | **15–<30m** |
| 3 | 普通 | 15–<20m | **10–<15m** |
| 4 | 稍劣 | 10–<15m | **5–<10m** |
| 5 | 劣 | <10m | **<5m** |

Adjustment matrix：靜態基準 step=3.75、max=15；Blind fixture **step=4、
max=16**（`0,4,8,12,16 / -4,0,4,8,12 / -8,-4,0,4,8 / -12,-8,-4,0,4 /
-16,-12,-8,-4,0`）——兩者皆為使用者於 STEP5 §14/§17 規格中逐字指定之
數值，非任意選擇。

`metadata` 明確標記 `TEST_ONLY=true`／`NON_OFFICIAL=true`／
`BLIND_CASE_RULE=true`（§14 要求）；`rule_version="TEST_ONLY-blind-v1"`；
`rule_id` 前綴 `REG-BLIND_MAIN_ROAD_WIDTH-*`（`REG-` 前綴是
`engine/rule_engine.py::RuleEngine._infer_rule_set()` 判斷
`rule_set="regional"` 的**唯一**依據，純字串前綴判斷——非任意選擇，是
與既有引擎介面對齊的必要條件，已在除錯過程中實測確認）。

## 2. 真正的 Pipeline，非直接 instantiate RuleEngine（§15）

`tests/test_competition_blind_case.py::_draft_blind_package()` 直接從
上述 JSON 建構 `CaseRulePackage`（§20 明文允許：JSON fixture 仍須經過
CaseRulePackage/confirmation path，且不得為了測試寫第二套 PDF Parser——
PDF 解析本身是 STEP3A 已凍結、已測試的獨立元件，此處刻意不重新觸碰）。

之後的每一步都是**真實 production 呼叫**，無任何跳過：

```
CaseRuleRepository.save_candidate(pkg)                         [B1]
  -> evaluation_standard.get_evaluation_standard_candidate()    [B2 前置]
  -> evaluation_standard.confirm_evaluation_standard()          [B3, 真實 handler]
  -> analyze.analyze()                                          [B4]
  -> complete_form.complete_form()                              [B6]
  -> review.review()                                            [B7, B8]
```

全程**未**出現 `RuleEngine(...)` 直接實例化＋assert 的模式（唯一一次
直接使用 `RuleEngine`／`GradeEngine`／`AdjustmentEngine` 是在 production
handler 內部，由 `rule_engine_factory.build_rule_engine_for_case()`
建構，與 Golden Case 走完全相同的程式碼路徑）。

## 3. 12 項測試結果

`tests/test_competition_blind_case.py`（12 passed）：

| 測試 | 驗證內容 |
|---|---|
| `TestBlindMatrixDiffersFromStatic`（2項，資料層級） | §17：同一
  grade pair (2,5)，靜態 matrix=11.25 vs Blind matrix=12，數值真的不同；
  fixture metadata 三個標記皆存在 |
| B1 `test_b1_package_starts_non_confirmed` | 儲存後 status≠CONFIRMED |
| B2 `test_b2_before_confirmation_cannot_affect_result` | 未確認時
  `resolution_status="STATIC_LOCAL"`、`warnings=["CASE_RULE_NOT_
  CONFIRMED"]`、grade 仍為普通 |
| B3+B4 `test_b3_b4_after_confirmation_analyze_uses_blind_rule_and_
  grade_differs` | 確認後 `resolution_status="CASE_IMPORTED_CONFIRMED"`；
  18m grade **普通→稍優** |
| B5 `test_b5_adjustment_rate_differs_from_static` | 同一 18m/3m 情境：
  靜態 adjustment=**7.5%**（matrix[3][5]），Blind
  adjustment=**12%**（matrix[2][5]）——grade code 與 matrix step 皆不同，
  複合證明 |
| B6 `test_b6_complete_form_reflects_blind_rule` | 表5-2/表4 欄位皆反映
  Blind 值（`region_adjustment_rate_comp1="12"`） |
| B7 `test_b7_review_understands_blind_rule` | Smart Review 正確重新
  推導 Blind 規則下的 expected grade（PASSED，非誤報） |
| B8 `test_b8_cross_form_uses_blind_values` | Cross-form 檢查同樣使用
  Blind 值（12%），非靜態值 |
| G1（Golden 基準） | 18m→普通 |
| G2/B9（`test_g2_g3_golden_blind_golden_sequence_no_cross_case_leak`）| Golden→Blind→Golden，第二次 Golden 仍為普通/STATIC_LOCAL |
| B10（`test_b10_blind_a_blind_b_blind_a_warm_runtime_no_leak`） | Blind
  A→Blind B→Blind A，同一 warm process，各自 package_id 與 grade 皆
  正確隔離 |

另於 `tests/test_competition_dual_input_e2e.py::TestGoldenE2E::
test_g3_golden_after_blind_case_unchanged` 從另一方向（真實雙輸入
Golden + 獨立 Blind case）重複驗證隔離性。

## 4. 最終數值總結

```
STATIC_18M_GRADE=普通
BLIND_18M_GRADE=稍優
GRADE_CHANGED_BY_NEW_STANDARD=YES
ADJUSTMENT_CHANGED_BY_NEW_STANDARD=YES
BLIND_CASE_RULE_IMPORT=PASS
BLIND_CASE_CONFIRMATION=PASS
BLIND_COMPLETE_FORM=PASS
BLIND_REVIEW=PASS
BLIND_CROSS_FORM=PASS
CASE_A_CASE_B_ISOLATION=PASS
WARM_RUNTIME_RULE_LEAK_FOUND=NO
```

## 5. §18 Traceability

`CaseRuleTraceInfo`（既有 STEP2 模型，未修改）已涵蓋
`case_id`／`package_id`／`source_document`／`source_sha256`／
`rule_version`；`analyze.py`／`complete_form.py`／`review.py` 皆已將
`rule_source_type` 附掛回每個欄位/issue（既有機制，本輪未變更）。
「為什麼 Blind Case 的 18m 與 Golden Case 得到不同等級」可由
`rule_resolution_status`＋`rule_package_id`＋每個 grade/issue 的
`rule_source_type="CASE_IMPORTED_CONFIRMED"` 直接回答，無需額外欄位。

## 6. §27 AI 邊界

Blind Case 全程**未**呼叫任何 AI/LLM 元件——fixture 直接以 JSON 構造，
繞過 STEP3C 的 AI Semantic Fallback（該元件本輪未變更，仍只服務於
Evaluation Standard 因素名稱對應，不涉及 grade/rate/price 決策）。
