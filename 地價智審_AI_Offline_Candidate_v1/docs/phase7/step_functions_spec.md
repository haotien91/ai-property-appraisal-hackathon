# Phase 7 — Step Functions Spec

## 狀態機總覽

見 `infra/statemachine/workflow.asl.json`（已通過JSON語法驗證＋狀態參照
完整性檢查，見`backend_deployment.md`驗證表）。

```
CollectData → CheckDataCompleteness ─┬─(COMPLETE)→ Analyze
                                      └─(其他)→ AwaitManualDataReview → Analyze
Analyze → CompleteForm → GeneratePdf → Review → AiExplanation → Complete
（任一步驟Task失敗 → PipelineFailed；AiExplanation失敗例外 → 仍導向Complete）
```

## 規則不寫死於Choice狀態（核心設計約束）

本狀態機**僅有一個**`Choice`狀態：`CheckDataCompleteness`，其判斷條件為：

```json
{
  "Variable": "$.collectDataResult.status",
  "StringEquals": "COMPLETE",
  "Next": "Analyze"
}
```

此處比對的是`collect-data` Lambda回傳之**流程狀態字串**（COMPLETE/PARTIAL），
**不是**任何評價基準明細表之數值級距、優劣等級判斷式，或修正率門檻。
所有實際業務規則判斷（建蔽率70%應屬何等級、深度差異率應為多少%等），
全數封裝於`Analyze`/`CompleteForm` Task內部呼叫的`RuleEngine`/
`CalculationEngine`（Phase 3/4既有deterministic程式碼），Step Functions
本身對這些規則內容一無所知，僅接收Task回傳之結構化結果。

## 各狀態職責對照

| State | 職責 | 對應Lambda | 失敗處理 |
|---|---|---|---|
| CollectData | Case→Data（7個Provider） | `collect_data.py` | Retry 2次後轉PipelineFailed |
| CheckDataCompleteness | 分支（非業務規則） | 無（Choice） | — |
| AwaitManualDataReview | 資料不完整時暫停等待人工補件 | `NotifyManualReviewFunction`（Task Token模式） | — |
| Analyze | Rule（Grade+Adjustment） | `analyze.py` | Retry 2次後轉PipelineFailed |
| CompleteForm | Calculation | `complete_form.py` | Retry 2次後轉PipelineFailed |
| GeneratePdf | Form Completion→PDF | `pdf_handler.py`（Container Image） | Retry 2次後轉PipelineFailed |
| Review | Smart Review | `review.py` | Retry 2次後轉PipelineFailed |
| AiExplanation | AI Explanation（Bedrock） | `explanation.py` | **失敗不阻擋**，仍導向Complete（Explanation非核心正確性步驟） |
| Complete | 完成 | — | — |
| PipelineFailed | 任一核心步驟失敗之終止狀態 | — | — |

## Geo狀態說明

Phase 7指示之階段列表包含獨立的「Geo」步驟，但目前`CollectData`
（Data Acquisition Layer）已內部涵蓋`GeoDistanceEngine`之呼叫時機
（見`providers/transportation_provider.py::demo_geo_engine_usage`之
設計，實際Provider目前為Mock，尚未在正式流程中觸發真實座標計算）。
未獨立拆分為Step Functions狀態之理由：目前無真實座標來源（Phase 5已
誠實記錄「Sources未提供任何設施精確座標」），若拆分為獨立狀態但內容
恆為no-op，僅增加流程複雜度而無實質效益；待未來串接真實GIS資料源時，
建議在`CollectData`與`Analyze`之間插入獨立`GeoDistance` Task State。

## 重試與錯誤處理原則

- 每個核心Task（CollectData/Analyze/CompleteForm/GeneratePdf/Review）
  皆設定`Retry`（2次，指數退避），因Lambda冷啟動或DynamoDB短暫節流屬
  可重試之暫時性錯誤。
- `Catch`統一導向`PipelineFailed`（`Type: Fail`），**不會**靜默忽略錯誤
  繼續執行下一步驟——呼應主專案指示「不得為了讓測試通過而Hardcode答案」
  「不得bypass validation」之精神，延伸至正式Workflow設計。
- 例外：`AiExplanation`失敗時仍視為整體成功（因其屬於錦上添花之自然語言
  摘要，非核心正確性判定），此為唯一之刻意例外，已於ASL之Comment欄位
  明確註記理由，非隱藏的不一致行為。

## 尚未部署驗證

本規格文件描述之狀態機定義已通過**離線**JSON結構驗證，但**未曾**在真實
AWS Step Functions服務中實際執行過（無AWS存取權限，見
`architecture.md`開頭聲明）。ARN佔位符（如`${CollectDataFunctionArn}`）
需於實際部署時由SAM/CloudFormation替換為真實Lambda ARN。
