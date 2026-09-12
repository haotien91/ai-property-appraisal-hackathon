# TEAMMATE_RULE_SOURCE_CLASSIFICATION

**日期**：2026-09-11
**性質**：STEP 4 §1 — 對`docs/incoming_rule_sources/`內每份隊友資料
分類。分類集合：`CENTRAL_MAXIMUM`／`LOCAL_GRADE_RULE`／
`LOCAL_ADJUSTMENT_MATRIX`／`FACTOR_ALIAS`／`REGRESSION_FIXTURE`／
`PROVENANCE_SOURCE`／`UNKNOWN`。

---

## 實際掃描結果

`docs/incoming_rule_sources/`目錄實際內容（已確認）：

```
1040130影響地價個別因素評價基準表(發布).doc
regional_rate_calculation.json
附件一影響住宅用地區域因素評價基準表.doc
附件二影響商業用地區域因素評價基準表.doc
附件三影響工業用地區域因素評價基準表.doc
附件四影響農業用地區域因素評價基準表.doc
```

`config.json`／`commercial_land_standard_schema.json`——**確認不存在**
（`ls docs/incoming_rule_sources/`未見這兩個檔案，先前STEP 1稽核已有
同樣發現，本輪再次確認）。分類：`UNKNOWN`（檔案缺失，非本輪可分類之
內容）。

## 分類表

| 檔案 | 分類 | 理由 |
|---|---|---|
| `附件一影響住宅用地區域因素評價基準表.doc` | **CENTRAL_MAXIMUM**＋**PROVENANCE_SOURCE** | 內政部104年令官方文件之住宅用地區域因素表，僅提供每細項之最大影響範圍（百分比）上限，無grade band、無adjustment matrix；經STEP 4 §2交叉比對，其378筆可比對儲存格與既有`data/rules/central_max_adjustment_range.json`（418筆）**逐格完全一致**（見下方§2結果）——可作為既有digitized central registry之provenance/cross-check來源，**非**新的規則來源 |
| `附件二影響商業用地區域因素評價基準表.doc` | **CENTRAL_MAXIMUM**＋**PROVENANCE_SOURCE** | 同上，商業用地版本 |
| `附件三影響工業用地區域因素評價基準表.doc` | **CENTRAL_MAXIMUM**＋**PROVENANCE_SOURCE** | 同上，工業用地版本 |
| `附件四影響農業用地區域因素評價基準表.doc` | **CENTRAL_MAXIMUM**＋**PROVENANCE_SOURCE** | 同上，農業用地版本 |
| `1040130影響地價個別因素評價基準表(發布).doc` | **CENTRAL_MAXIMUM**＋**PROVENANCE_SOURCE** | 同上，個別因素（5種用地類別橫向比較）版本 |
| `regional_rate_calculation.json` | **REGRESSION_FIXTURE** | `status:"incomplete"`、28筆記錄全數`comparable_missing`（比較標的留空未算），`step_rate_pct`為`max_abs_rate_pct/(grade_count-1)`公式推算值（非官方明細表直接記載之數字）——STEP 1稽核已確認此分類，STEP 4沿用並正式將其複製為`tests/fixtures/teammate_regional_rate_calculation.json`（見§7），**不得**作為Runtime Rule |
| `config.json` | UNKNOWN（檔案不存在） | 使用者提及但目錄中未找到 |
| `commercial_land_standard_schema.json` | UNKNOWN（檔案不存在） | 同上 |

**本輪未發現任何檔案應歸類為**`LOCAL_GRADE_RULE`**或**
`LOCAL_ADJUSTMENT_MATRIX`——隊友提供的5份.doc官方文件，其表格結構
本質上就是中央上限表（每細項僅一個最大百分比數字），**不含**地方
分級條件（grade band）或修正率矩陣（adjustment matrix），這點與
STEP 1稽核時的「重要觀念」提醒完全吻合：中央表≠地方grade rule。
`FACTOR_ALIAS`分類：本輪未在隊友資料本身找到一份現成的alias清單
（`config.json`不存在），但在交叉比對過程中，**衍生**出11組真實的
factor命名差異，已整理進`data/rules/factor_alias_registry.json`
（見§6，該檔案本身不是隊友原始資料，是本輪比對後的產出）。

## 與既有專案資料之關係

| 隊友資料 | 對應既有專案資料 | 關係 |
|---|---|---|
| 附件一~四.doc + 1040130.doc | `data/rules/central_max_adjustment_range.json`（418筆，已數位化） | 同一份內政部104年令來源，STEP 4實測確認**完全一致**（見`TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md`§2） |
| `regional_rate_calculation.json` | 無直接對應（既有專案沒有這種「已編碼比準地+待補比較標的」格式的資料） | 純粹為teammate自行推算之worked-example／regression fixture |

## 結論

```
TEAMMATE_CENTRAL_DATA_DUPLICATES_EXISTING=YES
```

本階段**不**建立第二份中央registry，5份.doc檔案的角色是
**cross-check / provenance source**，用以驗證既有
`central_max_adjustment_range.json`之數位化正確性——驗證結果為
完全一致，詳見`docs/audit/TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md`。
