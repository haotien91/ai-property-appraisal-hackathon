# Phase 7 — API Gateway Spec

## 端點對照

全部端點與`docs/phase4/frontend_api_contract.md`定義之Request/Response/
Error Schema**逐一對應**，前端`js/api.js`已依該契約實作，本節僅補充
AWS層級之路由/Lambda對應關係。

| 方法+路徑 | Lambda Function | 逾時 |
|---|---|---|
| `GET /api/cases` | `ListCasesFunction` | 30s（Global預設） |
| `POST /api/cases` | `CreateCaseFunction` | 30s |
| `GET /api/cases/{id}` | `GetCaseFunction` | 30s |
| `POST /api/cases/{id}/collect-data` | `CollectDataFunction` | 30s |
| `POST /api/cases/{id}/analyze` | `AnalyzeFunction` | 60s |
| `POST /api/cases/{id}/complete-form` | `CompleteFormFunction` | 60s |
| `GET /api/cases/{id}/pdf` | `PdfFunction`（Container Image） | 60s |
| `POST /api/cases/{id}/review` | `ReviewFunction` | 60s |
| `GET /api/cases/{id}/result` | `GetResultFunction` | 30s |

## CORS

`infra/template.yaml::ApiGateway.Cors`目前設定`AllowOrigin: "*"`（開發
階段）。**正式上線前應收斂**為CloudFront實際網域（例如
`https://dxxxxx.cloudfront.net`），避免任意來源網站可直接呼叫本API。

## Error Schema一致性

所有Lambda handler之錯誤回應皆透過`backend/handlers/common.py::error_response()`
統一產生，格式與`frontend_api_contract.md`定義完全一致：

```json
{"error": {"code": "...", "message": "...", "field_id": null, "details": {}}}
```

`code`列舉值：`CASE_NOT_FOUND` (404) / `VALIDATION_ERROR` (400) /
`RULE_NOT_FOUND` (依情境) / `WRONG_UNIT` (依情境) / `INTERNAL_ERROR` (500)。

## 存取記錄（Access Log）

`ApiAccessLogGroup`記錄`requestId`/`status`/`path`/`errorMessage`，
**不記錄**request/response body（避免CloudWatch Logs間接留存案件明細，
呼應`aws_services.md` Part I之敏感資料保護原則）。

## 尚未部署驗證

本規格文件描述之API Gateway設定已於`infra/template.yaml`中定義並通過
YAML語法驗證，但**未曾**實際部署至AWS帳號、**未曾**取得真實Invoke URL、
**未曾**以真實HTTP請求驗證各端點行為（僅以Lambda handler層級之
`moto`模擬測試驗證過業務邏輯，見`backend_deployment.md`驗證表）。
