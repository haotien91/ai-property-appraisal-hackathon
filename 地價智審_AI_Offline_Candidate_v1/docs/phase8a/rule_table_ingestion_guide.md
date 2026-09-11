# 決賽當天：如何把新的評價基準明細表接進系統

## 為什麼需要這份文件

Phase 1 evidence matrix REQ-006 已確認：決賽當天提供的區段與評價基準明細表
**跟賽前金山區範例不同**。`data/rules/regional_rules.json` /
`individual_rules.json` 目前是人工謄寫金山區範例的結果——決賽現場拿到新表格
後，必須有一條路徑能把新表格變成 `RuleEngine` 可以吃的規則資料，而且這條路
徑本身要**可驗證、可快速抓錯**，因為現場沒有時間慢慢除錯。

這份指南就是那條路徑：三個 CSV 檔 → `scripts/ingest_rule_table.py` →
驗證過的規則 JSON。

## 三個 CSV 檔案

範例（依真實金山區「都市計畫內外」因素製作）在
[`data/rules/templates/`](../../data/rules/templates/)：

| 檔案 | 一列代表 | 誰來填 |
|---|---|---|
| `factors.csv` | 一個評價因素（都市計畫內外、主要道路寬度…） | 對照評價基準明細表的「因素」欄 |
| `bands.csv` | 一個因素底下的一個優劣等級（優/稍優/普通/稍劣/劣） | 對照該因素的級距說明 |
| `matrix.csv` | 一個因素裡，某等級比對某等級的修正率 | 對照該因素的修正率矩陣 |

三個檔案用 `rule_id_prefix`（例如 `REG-MAIN_ROAD_WIDTH`）互相對應，同一個
因素在三個檔案裡都要用同一個 prefix。

**這不需要懂 JSON 或程式**——填法跟填 Excel 表格一樣，一格一格對照官方PDF
明細表填入即可。若現場能用 Bedrock 讀取PDF表格輔助草擬這三個CSV，人工只需
快速核對修改，比從零手寫更快；若沒有，人工直接照PDF謄寫也可行。

## 轉換與驗證

```bash
py scripts/ingest_rule_table.py \
    --factors path/to/factors.csv \
    --bands path/to/bands.csv \
    --matrix path/to/matrix.csv \
    --out data/rules/new_segment_regional_rules.json \
    --rule-set regional_rules
```

- 若有結構性錯誤（CSV 缺欄位、prefix對不上），直接報錯、不輸出檔案。
- 若通過結構檢查但有業務邏輯問題（矩陣不是方陣、`max_adjustment`跟矩陣算出
  來的不一致、同一因素各等級列的矩陣互相矛盾…），**一次列出所有問題**，
  ERROR一律不輸出檔案；WARNING（例如矩陣非正負對稱、級距銜接不連續）會印出
  但不擋輸出，因為這些狀況官方表格本身也可能刻意如此設計。
- 全部通過後，輸出的 JSON 格式與 `data/rules/regional_rules.json` 完全一致，
  可直接被 `RuleEngine` 載入使用。

同一份 `regional_rules.json` / `individual_rules.json`（金山區範例）已經過
這個驗證器全面檢查、**零ERROR**（見 `tests/test_rule_table_ingest.py::
TestRuleTableValidatorOnRealData`），證明驗證規則沒有對官方真實資料誤判。

## 驗證器實際檢查什麼（`engine/rule_table_validator.py`）

- 必要欄位是否齊全、`value_type` 是否為官方允許的列舉值
- 同一因素內 `grade_code` 是否重複
- 同一因素底下每一列的 `adjustment_matrix` 是否完全一致（同因素應攜帶同一份完整矩陣）
- 矩陣是否為方陣（列/欄的等級代碼跟這個因素實際擁有的等級一致）
- `max_adjustment` 是否等於矩陣中絕對值最大者
- 【WARNING】同等級對比對角線是否為0、矩陣是否正負對稱、數值級距銜接處是否連續
- 【WARNING】`grade` 文字是否落在7組已知官方分級用語內（2/3/5級×3種用語/7/9級，
  見作業手冊p.48-49、p.52-53）；同一因素底下是否混用了兩組不同用語。**刻意
  是WARNING非ERROR**——作業手冊明文「9級以上由地方政府自行決定等級細項」，
  用語本非全國固定清單，不可把合法的地方自訂分級當成錯誤擋下來。決賽案例
  用地類別/區段確定跟金山區不同（REQ-006/007），分級制度也可能不同。

## 這條路徑目前故意沒做的事

- **不呼叫任何 AI/OCR 自動讀PDF**——這條路徑本身不依賴 Bedrock，任何時候都能
  跑（含離線開發環境）。若決賽現場要接 Bedrock 讀PDF草擬CSV，那是這條路徑
  「前面」再加一段 Adapter，不需要修改 `rule_table_ingest.py` /
  `rule_table_validator.py` 本身（同 `docs/phase6/document_extraction_spec.md`
  的 Adapter Pattern）。
- **不驗證 PDF 抽取本身有沒有抄錯**——這是「結構是否合法、內部是否自洽」的
  檢查，不是「跟官方PDF逐字比對是否正確」的檢查；後者仍需人工核對PDF原文。
