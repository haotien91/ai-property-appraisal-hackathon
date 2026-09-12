# Phase 7 — Architecture Overview

## 誠實聲明（置於文件最前，非附註）

**本開發環境無AWS帳號存取權限、無網路路徑可呼叫AWS control-plane API**
（`bash_tool`網路白名單僅含pypi/npm/github等套件來源網域，已於Phase 6
Textract決策時發現同一限制）。因此：

- 本文件與`infra/`、`backend/`下之全部產出，皆為**可部署但尚未實際部署**
  之程式碼與IaC，非已運行之AWS服務。
- **不存在**真實可訪問之AWS-hosted前端URL或Backend API endpoint。
- 所有「本階段完成」之判斷，皆指「程式碼/IaC本身邏輯正確、經本地測試/
  mock驗證通過」，不代表已在真實AWS帳號中驗證過。
- 此限制已誠實記錄，不隱藏、不以虛構URL冒充完成（呼應主專案指示
  「不得將localhost當正式Demo」「隱藏Known Limitation」之禁止事項的精神——
  此處更進一步：連localhost都不需要，因為問題是「從未真實部署」而非
  「僅部署到本機」）。

---

## 系統分層架構

```
┌─────────────────────────────────────────────────────────┐
│  Frontend（Amazon S3 + CloudFront）                       │
│  Makerthon_test_1改造：8個App Pages + js/api.js統一API層   │
└───────────────────┬─────────────────────────────────────┘
                     │ HTTPS
┌────────────────────▼────────────────────────────────────┐
│  Amazon API Gateway（REST API, prod stage）                │
└───────────────────┬─────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│  AWS Lambda（9個Function，見aws_services.md）              │
│  cases / collect_data / analyze / complete_form /         │
│  review / pdf_handler（Container Image）/ result /         │
│  explanation（Bedrock）                                    │
└──────┬──────────────┬──────────────┬─────────────────────┘
       │              │              │
┌──────▼─────┐  ┌─────▼──────┐  ┌────▼──────────┐
│ DynamoDB   │  │ S3(PDF)    │  │ Bedrock        │
│ (案件資料)  │  │            │  │ (Explanation)  │
└────────────┘  └────────────┘  └────────────────┘

┌─────────────────────────────────────────────────────────┐
│  AWS Step Functions（orchestration，見step_functions_spec.md）│
│  Case→Data→Rule→Calculation→FormCompletion→PDF→Review→    │
│  AIExplanation→Complete                                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Amazon Bedrock AgentCore Managed Knowledge Base           │
│  （作業手冊/評價基準明細表/官方文件，Rule Explanation用）    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Amazon CloudWatch（Logs + Metrics + X-Ray Tracing）        │
└─────────────────────────────────────────────────────────┘
```

## 核心設計原則（貫穿Phase 1-7）

1. **Deterministic Engine負責正確性，LLM僅負責理解與解釋**：Bedrock（Part F）
   僅用於`explanation.py`之自然語言摘要，絕不參與Grade/Adjustment/
   Calculation/Distance判定——這些已在Phase 3-5以`RuleEngine`/
   `CalculationEngine`/`GeoDistanceEngine`實作為deterministic程式碼，
   AWS化過程未改變此原則，僅是將既有Python引擎搬遷至Lambda執行環境。
2. **規則不寫死於Step Functions Choice**：`infra/statemachine/workflow.asl.json`
   之唯一`Choice`狀態（`CheckDataCompleteness`）僅依`collect-data`回傳之
   `status`欄位（COMPLETE/PARTIAL）分支，不含任何評價基準明細表之級距
   數值或優劣等級判斷邏輯。
3. **既有前端優先**：Frontend（Part A/B）建立於Makerthon_test_1既有HTML/CSS/
   JS架構上，未引入React/Vue/Angular，深色/粉紅主題與Navbar/Footer/
   Attribution連結全數保留。
4. **不誇大完成度**：本文件與其餘6份Phase 7文件，對「已完成」與
   「尚未完成/尚未驗證」之區分，逐項明確標示，不使用模糊語言掩蓋落差。

## 各Phase產出如何對應到AWS元件

| Phase產出 | AWS對應 |
|---|---|
| Phase 3 `RuleEngine` | 打包進`EngineLayer`（Lambda Layer），供`analyze`/`complete_form`/`review` Function共用 |
| Phase 4 `FormCompletionEngine`等 | `complete_form.py` Lambda handler |
| Phase 5 Data Providers | `collect_data.py` Lambda handler |
| Phase 5 `PdfRenderer`（weasyprint） | `pdf_handler.py`，因系統依賴改採Container Image部署 |
| Phase 6 `AuditEngine`等 | `review.py` Lambda handler |
| Phase 4 Pydantic domain models | 序列化為DynamoDB `data`屬性與API回應body |
| Phase 4 `frontend/mock/*.json` | Mock Mode下前端直接讀取；Production Mode下由對應Lambda產生相同schema |
