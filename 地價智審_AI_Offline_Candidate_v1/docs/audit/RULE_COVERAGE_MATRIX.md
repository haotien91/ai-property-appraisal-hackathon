# RULE_COVERAGE_MATRIX

**日期**：2026-09-11
**性質**：STEP 4 §4 — 因素×scope×用地類別涵蓋矩陣。逐一計算自既有
`data/rules/central_max_adjustment_range.json`（418筆）／
`regional_rules.json`（131筆）／`individual_rules.json`（84筆）／
本輪新增`data/rules/factor_alias_registry.json`（11筆），非人工估計。

---

## 摘要矩陣（依用地類別×scope彙總）

| 用地類別 | CENTRAL_MAX（regional項目數） | CENTRAL_MAX（individual項目數） | LOCAL_GRADE_AVAILABLE（regional） | LOCAL_GRADE_AVAILABLE（individual） | RUNTIME_READY |
|---|---|---|---|---|---|
| 住宅用地 | ✅ 28項 | ✅ 19項 | ❌ 0個factor | ❌ 0個factor | ❌ **NOT_RUNTIME_GRADE_READY** |
| 商業用地 | ✅ 28項 | ✅ 19項 | ✅ 28個factor | ✅ 19個factor | ✅ YES（Golden Case金山區） |
| 工業用地 | ✅ 20項 | ✅ 19項 | ❌ 0個factor | ❌ 0個factor | ❌ **NOT_RUNTIME_GRADE_READY** |
| 農業用地 | ✅ 19項 | ✅ 19項 | ❌ 0個factor | ❌ 0個factor | ❌ **NOT_RUNTIME_GRADE_READY** |
| 其他用地 | ✅ 23項 | ✅ 19項 | ❌ 0個factor | ❌ 0個factor | ❌ **NOT_RUNTIME_GRADE_READY** |

**ALIAS_AVAILABLE**：`data/rules/factor_alias_registry.json`共11筆，
7筆regional scope、4筆individual scope，皆只涵蓋**商業用地**既有local
rule與中央表/PDF note之間的命名差異（見
`TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md`§6）——住宅/工業/農業/其他
用地目前**沒有**local rule可供比對，故也沒有對應的alias條目。

## 判讀

- **商業用地（金山區Golden Case）**：唯一同時具備CENTRAL_MAX、
  LOCAL_GRADE（含grade band與完整adjustment matrix）、ALIAS三者的
  用地類別——`RUNTIME_READY=YES`，`RuleEngine`可直接使用
  `data/rules/regional_rules.json`/`individual_rules.json`進行grade
  判定，此為既有已驗證之Golden Pipeline，本輪未變更。
- **住宅／工業／農業／其他用地**：中央上限（CENTRAL_MAX）**已完整
  涵蓋**（見`central_max_adjustment_range.json`既有418筆之一部分），
  但**沒有**任何地方分級條件（grade band）或修正率矩陣（adjustment
  matrix）資料——`RuleEngine`若被要求對這4類用地judge grade，會依
  設計正確拋出`RuleNotFoundError`（見§8「Unknown land use no
  fallback」測試），**不會**假裝可以估價。依STEP 4 §11明確要求，
  本文件明確標記這4類用地為`NOT_RUNTIME_GRADE_READY`，不得假裝可以
  直接估價。
- 取得這4類用地之`LOCAL_GRADE_AVAILABLE=YES`需要官方或地方政府
  發布的、**具備grade band與matrix**的評價基準明細表（類似
  `評價基準明細表範例.pdf`之結構，但為住宅/工業/農業/其他用地版本）
  ——隊友本輪提供的.doc檔案**不是**這種文件，只是中央上限表，
  無法直接補上這個缺口，需另尋官方/地方來源。

## 資料來源（供逐項因素查閱，非重複列出全部418+131+84筆於本文件）

- 中央上限：`data/rules/central_max_adjustment_range.json`（含
  `land_use_type`/`table_type`/`item_name`/`land_use_subgrade`/
  `max_range_pct`/`cell_state`欄位，可直接查詢任一因素在任一用地
  類別下的上限值）
- 地方grade rule（僅商業用地）：`data/rules/regional_rules.json`／
  `individual_rules.json`
- Alias對照：`data/rules/factor_alias_registry.json`
- 中央/地方對應狀態（僅商業用地，仍UNMAPPED，本輪未變更）：
  `data/rules/central_max_range_local_mapping.json`
