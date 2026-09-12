# AWS Runtime Acceptance Checklist — 地價智審 AI

> 本checklist供**取得真實AWS環境後**逐項勾選。本AI助理於撰寫當下
> **全數項目皆為未勾選狀態**（無AWS Credentials/Network Access），
> 此為誠實初始狀態，非遺漏。每項對應`aws_deployment_runbook.md`之
> 特定步驟，供交叉核對。

## Credentials & Access

- [ ] AWS Credentials valid（`aws sts get-caller-identity`成功回傳，見Runbook Step 2）
- [ ] STS identity successful
- [ ] IAM權限足以完成`sam deploy`（見Runbook Step 1/3）

## Frontend

- [ ] Frontend deployed（S3 sync完成，見Runbook Step 5）
- [ ] Frontend URL accessible（CloudFront URL回傳HTTP 200，見Runbook Step 15）
- [ ] `config.js`之`MODE`已切換為`"production"`且`API_BASE_URL`已更新為真實值
- [ ] 8個App Pages（case/case-new/data/analysis/form-fill/pdf-preview/review/result）
      於真實CloudFront URL上皆可開啟且無Console Error

## Backend

- [ ] Backend deployed（`sam deploy`成功，見Runbook Step 3）
- [ ] API endpoint accessible（`curl <ApiInvokeUrl>/api/cases`回傳200，見Runbook Step 7）
- [ ] S3 operational（`aws s3 ls`確認3個Bucket存在，見Runbook Step 4）
- [ ] Database accessible（`aws dynamodb describe-table`回傳`ACTIVE`，見Runbook Step 11）

## Workflow

- [ ] Step Functions deployed（`create-state-machine`成功，見Runbook Step 8）
- [ ] Step Functions execution successful（`describe-execution`回傳`SUCCEEDED`，見Runbook Step 8）
- [ ] ASL中`${XxxFunctionArn}`佔位符已全數替換為真實Lambda ARN

## AI Services

- [ ] Bedrock invocation successful（`explanation`端點回傳非樣板式摘要，見Runbook Step 9）
- [ ] （若實作AgentCore）Knowledge Base synchronized
- [ ] （若實作AgentCore）Retrieval successful
- [ ] （若實作AgentCore）Citation returned

## Observability

- [ ] CloudWatch logs visible（`aws logs tail`可看到結構化log，見Runbook Step 12）
- [ ] CloudWatch logs確認不含敏感案件內容（人工抽查，見Runbook Step 12）
- [ ] X-Ray Tracing資料可見（Console檢視）

## End-to-End Correctness

- [ ] Golden Case E2E passed（`complete-form`回傳`base_parcel_comparison_price=212958`，
      見Runbook Step 14；本地已於`tests/test_form_completion_golden.py`驗證過相同期望值）
- [ ] PDF generated through deployed system（透過真實`pdf`端點產生，非本地`weasyprint`直接呼叫）
- [ ] 已部署系統產生之PDF：可開啟、頁數合理、文字可見、無明顯損毀
      （本地已於`tests/test_phase5_golden_pipeline.py`驗證過相同渲染邏輯）
- [ ] Smart Review works through deployed system（`review`端點回傳與本地
      `tests/test_smart_review.py`一致之Demo Error Case偵測結果）

## Security

- [ ] No real credentials committed（本地靜態掃描已確認乾淨，見
      `docs/phase7/aws_services.md`與歷次Acceptance Review之Security段落；
      部署後建議再次確認CloudFormation Stack本身未於任何Output/Log中
      洩漏憑證）
- [ ] S3 Buckets確認`BlockPublicAcls`等設定實際生效（非僅IaC定義，
      需於Console或`get-bucket-policy-status`確認執行時狀態）
- [ ] API Gateway CORS設定已從開發用`AllowOrigin: "*"`收斂為實際
      CloudFront網域（見`docs/phase7/api_gateway_spec.md`已知限制）

## 完成後

- [ ] 全部以上項目勾選完成後，方可將Phase 7B狀態由`BLOCKED_BY_ENVIRONMENT`
      更新為實際部署完成狀態，並重新執行正式Acceptance Review。
