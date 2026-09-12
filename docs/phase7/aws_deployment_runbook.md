# AWS Deployment Runbook — 地價智審 AI

> 本Runbook供**取得真實AWS帳號權限之人員**依序執行。撰寫者（本AI助理）
> 本身無法執行任何一個步驟（無AWS Credentials、無AWS Network Access，
> 見`docs/phase7/architecture.md`開頭聲明），故本文件之每一步驟皆為
> **設計時驗證**（YAML/JSON語法、Python可執行性），非**執行時驗證**。
> 無法靜態驗證之步驟，已標記`RUNTIME_VALIDATION_REQUIRED`。

---

## 1. Prerequisites

| 項目 | 需求 | 驗證方式 |
|---|---|---|
| AWS Account | 具建立Lambda/API Gateway/DynamoDB/S3/CloudFront/Step Functions/Bedrock資源權限之帳號 | `aws sts get-caller-identity` |
| Region | 建議`ap-northeast-1`（東京，鄰近新北市黑客松展示需求） | — |
| IAM Permissions | 至少：`AWSLambda_FullAccess`、`AmazonAPIGatewayAdministrator`、`AmazonDynamoDBFullAccess`、`AmazonS3FullAccess`、`CloudFrontFullAccess`、`AWSStepFunctionsFullAccess`、`AmazonBedrockFullAccess`、`CloudFormationFullAccess`（或對應之細粒度自訂Policy） | `aws iam get-user` / Console檢視 |
| Python | 3.12（與`infra/template.yaml::Globals.Function.Runtime`一致） | `python3 --version` |
| Runtime工具 | AWS SAM CLI ≥1.100、Docker（PdfFunction為Container Image） | `sam --version`、`docker --version` |
| 必要環境變數 | 見下方Step 2起各步驟之環境變數表 | — |

**RUNTIME_VALIDATION_REQUIRED**：實際IAM權限是否足夠，僅能於部署時得知
（`sam deploy`若權限不足會明確報錯並中止，非靜默失敗）。

---

## 2. Credential Verification

**Command**：
```bash
aws sts get-caller-identity
aws configure get region
```
**Expected Result**：回傳`Account`/`UserId`/`Arn`，確認已登入正確帳號。
**Failure Condition**：`Unable to locate credentials`（本AI助理環境實測之
確切錯誤訊息，見前次NO-GO Recovery紀錄）→ 執行`aws configure`設定憑證。
**Verification Method**：比對回傳之`Account`與預期帳號ID一致。

---

## 3. Backend Deployment

**Command**：
```bash
cd infra/
sam build --use-container
sam deploy --guided
```
**Expected Result**：`sam deploy`互動式詢問Stack名稱、Region、是否允許
建立IAM Role等，完成後輸出`ApiInvokeUrl`/`FrontendCloudFrontUrl`/
`CasesTableName`（見`infra/template.yaml::Outputs`）。
**Failure Condition**：`sam build`若因`backend/docker/pdf.Dockerfile`
之`dnf install`套件不存在而失敗（因Amazon Linux版本差異）；`sam deploy`
若因IAM權限不足而失敗。
**Verification Method**：`sam list stack-outputs --stack-name <STACK_NAME>`
確認3個Output值皆非空。

**RUNTIME_VALIDATION_REQUIRED**：`backend/docker/pdf.Dockerfile`之
`dnf install cairo pango gdk-pixbuf2 google-noto-sans-cjk-ttc-fonts`
套件名稱基於Amazon Linux 2023假設，未經實際build驗證（本環境無Docker
daemon），建議部署者於`sam build`失敗時依實際套件庫調整套件名稱。

---

## 4. S3 Configuration

**Command**：
```bash
aws s3 ls | grep ai-valuation
```
**Expected Result**：列出3個Bucket：`ai-valuation-pdfs-<ACCOUNT_ID>`、
`ai-valuation-frontend-<ACCOUNT_ID>`、`ai-valuation-kb-source-<ACCOUNT_ID>`
（皆由`sam deploy`於Step 3自動建立，非獨立步驟）。
**Failure Condition**：Bucket名稱全域衝突（S3 Bucket名稱全球唯一），
需修改`infra/template.yaml`之`BucketName`加入額外唯一後綴。
**Verification Method**：`aws s3api get-bucket-policy-status --bucket <BUCKET>`
確認`BlockPublicAcls`等設定生效。

---

## 5. Frontend Deployment

**Command**：
```bash
# 先將 frontend/app/js/config.js 之 API_BASE_URL 改為Step3取得之ApiInvokeUrl，MODE改為"production"
aws s3 sync frontend/app/ s3://ai-valuation-frontend-<ACCOUNT_ID>/
```
**Expected Result**：`aws s3 sync`輸出upload清單，含全部HTML/CSS/JS/
`frontend/mock/*.json`檔案。
**Failure Condition**：S3 sync權限不足；`config.js`未正確更新導致前端
仍呼叫佔位URL。
**Verification Method**：`aws s3 ls s3://ai-valuation-frontend-<ACCOUNT_ID>/`
確認`index.html`等檔案存在。

---

## 6. CloudFront Configuration

**Command**：
```bash
aws cloudfront create-invalidation \
    --distribution-id <FrontendDistribution輸出之ID> --paths "/*"
```
**Expected Result**：回傳`Invalidation`物件含`Id`/`Status: InProgress`。
**Failure Condition**：Distribution ID錯誤（403/404）。
**Verification Method**：`curl -I https://<CloudFrontDomainName>/index.html`
確認HTTP 200。

---

## 7. API Gateway Configuration

已隨Step 3之SAM部署自動建立（見`infra/template.yaml::ApiGateway`）。

**Verification Method**：
```bash
curl -s https://<ApiInvokeUrl>/api/cases
```
**Expected Result**：HTTP 200，回傳`{"cases": [], "total": 0, ...}`
（DynamoDB尚無資料時之EMPTY狀態，見`docs/phase4/frontend_api_contract.md`）。
**Failure Condition**：HTTP 403（CORS或IAM問題）／HTTP 500（Lambda執行錯誤，
查CloudWatch Logs）。

---

## 8. Step Functions Deployment

**Command**：
```bash
aws stepfunctions create-state-machine \
    --name ai-valuation-workflow \
    --definition file://infra/statemachine/workflow.asl.json \
    --role-arn <StepFunctionsExecutionRole ARN>
```
**Expected Result**：回傳`stateMachineArn`。
**Failure Condition**：`workflow.asl.json`中`${XxxFunctionArn}`佔位符
未被實際Lambda ARN取代（見`docs/phase7/backend_deployment.md`已知限制
第2點）——部署前須先以`sed`或手動替換這些佔位符為Step 3取得之真實
Lambda ARN。
**Verification Method**：
```bash
aws stepfunctions start-execution \
    --state-machine-arn <ARN> --input '{"caseNo":"TEST-001"}'
aws stepfunctions describe-execution --execution-arn <execution-arn>
```
確認`status`最終為`SUCCEEDED`。**此為Step Functions「至少跑過一次」
之直接驗證方式，本AI助理環境無法執行此指令**（見`aws_runtime_acceptance_checklist.md`
對應勾選項）。

---

## 9. Bedrock Configuration

**Command**：
```bash
aws bedrock list-foundation-models --region ap-northeast-1 | grep claude
```
**Expected Result**：列出可用之Claude模型ID，確認與
`backend/handlers/explanation.py::BEDROCK_MODEL_ID`環境變數所指定之模型
一致（**注意**：`infra/template.yaml`目前未顯式定義`BEDROCK_MODEL_ID`
環境變數給`ExplanationFunction`，程式碼會使用內建預設值，建議部署時
於`template.yaml`之`ExplanationFunction.Properties.Environment.Variables`
明確加入此變數，見`docs/backlog.md`）。
**Failure Condition**：所選Region未提供Bedrock服務，或帳號未申請
Anthropic模型使用權限（Bedrock需先於Console啟用特定模型存取權）。
**Verification Method**：
```bash
curl -X POST https://<ApiInvokeUrl>/api/cases/<CASE_NO>/explanation
```
確認回傳`summary_text`為非樣板式（非"Bedrock暫時無法使用"開頭）之
真實中文摘要。

---

## 10. AgentCore Knowledge Base — **OPTIONAL / PENDING**

**狀態**：尚未實作（僅`infra/template.yaml::KnowledgeBaseSourceBucket`
規劃來源文件儲存位置，無實際Knowledge Base資源）。

若欲實作，建議步驟（**PENDING，本文件僅記錄設計方向，非可直接執行之
指令**）：
```bash
# 1. 上傳來源文件
aws s3 cp 土地徵收補償市價查估作業手冊.pdf s3://ai-valuation-kb-source-<ACCOUNT_ID>/
aws s3 cp 評價基準明細表範例.pdf s3://ai-valuation-kb-source-<ACCOUNT_ID>/

# 2. 透過Bedrock Console或API建立Knowledge Base（無CLI單一指令，需先建立
#    OpenSearch Serverless collection作為vector store，屬多步驟流程，
#    建議透過AWS Console精靈完成，非本Runbook範圍）
```

---

## 11. Database Initialization

DynamoDB Table已隨Step 3之SAM部署自動建立（`PAY_PER_REQUEST`模式，
無需預先佈建容量）。

**Verification Method**：
```bash
aws dynamodb describe-table --table-name AIValuationCases
```
確認`TableStatus: ACTIVE`。

---

## 12. CloudWatch Verification

**Command**：
```bash
aws logs tail /aws/lambda/ai-valuation-complete-form --follow
```
**Expected Result**：即時串流出`log_step()`產生之結構化JSON log
（見`backend/handlers/common.py`），格式為
`{"case_no": "...", "step": "...", "status": "...", "duration_ms": ...}`，
**確認不含**任何原始因素數值或案件明細內容。
**Failure Condition**：Log Group不存在（Lambda從未被呼叫過）。
**Verification Method**：人工檢視log內容，確認無敏感資料外洩
（呼應`aws_services.md` Part I設計原則）。

---

## 13. Smoke Test

**Command**：
```bash
curl -X POST https://<ApiInvokeUrl>/api/cases \
    -d '{"case_no":"SMOKE-001","segment_code":"P002-00","city":"新北市","district":"金山區","land_use_type":"商業用地","appraisal_period":"1140901","appraisal_base_date":"1140901","segment_scope":"...","base_parcel_id":"x","comparable_ids":["c1"]}'
curl https://<ApiInvokeUrl>/api/cases/SMOKE-001
```
**Expected Result**：第一個請求回傳201，第二個請求回傳200且
`case_no`/`updated_at`皆正確（`updated_at`非null，呼應前次NO-GO
Recovery所修復之bug）。
**Failure Condition**：任一請求回傳5xx。
**Verification Method**：比對回應JSON結構與`docs/phase4/frontend_api_contract.md`一致。

---

## 14. E2E Test

依序呼叫：`collect-data` → `analyze` → `complete-form` → `pdf` →
`review` → `result`，確認：
- `complete-form`回傳之`base_parcel_comparison_price`最終為`212958`
  （若使用Golden Case原始資料，此為`tests/test_form_completion_golden.py`
  已於本地驗證過之期望值，部署後應重現相同結果）。
- `pdf`回傳之`pdf_url`可實際下載，PDF可開啟、頁數合理、文字可見
  （本地已驗證過渲染邏輯本身正確，見`docs/phase5/pdf_output_spec.md`）。

**RUNTIME_VALIDATION_REQUIRED**：完整E2E鏈本身需要真實AWS環境執行，
本地測試（`tests/test_phase5_golden_pipeline.py`）僅驗證了各Function
內部邏輯，未驗證跨Lambda呼叫、DynamoDB讀寫、S3上傳下載之完整鏈路。

---

## 15. Live Demo URL Verification

**Command**：
```bash
curl -I https://<FrontendCloudFrontUrl>/index.html
curl -I https://<FrontendCloudFrontUrl>/case.html
```
**Expected Result**：皆回傳HTTP 200。
**Verification Method**：瀏覽器實際開啟`FrontendCloudFrontUrl`，確認
`case.html`能實際呼叫`ApiInvokeUrl`並顯示真實資料（需先完成Step 5之
`config.js`更新為`MODE: "production"`）。

---

## 16. Rollback

```bash
sam delete --stack-name <STACK_NAME>
```
**Expected Result**：刪除全部由SAM建立之資源（Lambda/API Gateway/
DynamoDB/S3 Bucket部分資源可能因`DeletionPolicy`需手動確認）。
**注意**：`PdfBucket`/`FrontendBucket`若非空，`sam delete`可能失敗，
需先手動清空Bucket內容。
**Failure Condition**：S3 Bucket非空導致刪除失敗。
**Verification Method**：`aws cloudformation describe-stacks --stack-name <STACK_NAME>`
確認Stack已不存在。

---

## 17. Resource Cleanup

黑客松展示結束後建議執行Rollback（見上方Step 16），避免產生非預期之
持續費用（雖`aws_services.md`／`cost_notes.md`已說明本架構閒置時
成本趨近於零，仍建議展示後清理，尤其DynamoDB/S3之儲存費用會隨時間
持續累積，即便金額很小）。
