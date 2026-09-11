# Phase 7 — Cost Notes

## 聲明

以下為**估算**，非實際帳單（本階段未實際部署，見`architecture.md`
開頭聲明）。估算基於AWS公開定價（截至Claude知識截止日之公開資訊，
實際費率請以AWS官方定價頁面為準）與黑客松Demo等級之低流量假設
（每日數十次案件處理，非生產環境高流量）。

## 各服務閒置/低流量成本特性

| 服務 | 計費模式 | 黑客松Demo情境下之成本特性 |
|---|---|---|
| Lambda | 按呼叫次數+執行時間計費 | 閒置時完全免費；每月前100萬次呼叫在AWS Free Tier內 |
| API Gateway | 按請求數計費 | 低流量下每月費用通常低於US$1 |
| DynamoDB (On-Demand) | 按讀寫請求計費 | 閒置零成本；Demo規模（數十案件）每月費用可忽略 |
| S3 (PDF+Frontend) | 按儲存量+請求數計費 | 靜態資產+少量PDF，每月費用通常低於US$1 |
| CloudFront | 按流量計費 | Demo流量下每月費用可忽略，且有Free Tier額度 |
| Step Functions (Standard) | 按狀態轉換次數計費 | 每次案件處理約10次狀態轉換，Demo規模下成本可忽略 |
| Bedrock | 按輸入/輸出token計費 | 每次Explanation呼叫約數百token，成本取決於選用模型單價 |
| CloudWatch Logs | 按儲存量+擷取量計費 | 已設定30天Log Group Retention（見template.yaml），避免無限累積 |

## 為何不選擇成本較高的替代方案

- **ECS/Fargate常駐服務**：即便設定最小任務數，仍會產生持續運算費用，
  對Demo等級之間歇性流量不划算（見`aws_services.md` Part D理由）。
- **RDS（即便是最小規格）**：即便使用最小db.t4g.micro等規格，仍需支付
  按小時計費之運算費用（不像DynamoDB On-Demand可真正降到零），且需要
  額外考慮Multi-AZ/備份儲存等成本項目。
- **Lambda Provisioned Concurrency**：可消除冷啟動延遲，但需持續付費，
  黑客松Demo可接受冷啟動延遲（通常<2秒），故未啟用。

## 成本控制建議（供未來實際部署參考）

1. `PdfBucket`已設定90天生命週期規則自動清除舊PDF（見`template.yaml`），
   避免儲存成本隨案件數無限增長。
2. `CloudWatch Logs`已設定30天保留，避免Log儲存成本無限增長。
3. 若決賽/正式使用後流量明顯提升，應重新評估是否需要
   Provisioned Concurrency（降低延遲）或DynamoDB Provisioned Capacity
   （若流量模式穩定可預測，可能比On-Demand更省成本）。
4. Bedrock為token計費，`explanation.py`已限制`max_tokens: 400`
   （見程式碼），避免單次呼叫產生過長、成本過高的回應。

## 已知限制

本估算未包含：AWS Support Plan費用、跨區域資料傳輸費用（假設所有資源
部署於同一Region）、超出Free Tier後之精確費率試算（需視實際帳號的
Free Tier使用狀況與部署Region而定，建議部署前以
[AWS Pricing Calculator](https://calculator.aws)依實際預期流量重新試算）。
