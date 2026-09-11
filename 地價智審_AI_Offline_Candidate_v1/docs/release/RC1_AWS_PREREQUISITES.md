# RC1 AWS Deployment Prerequisites

> 本文件僅整理`infra/template.yaml`與既有文件（`docs/phase7/`、
> `docs/phase9/`）之既有結果，本輪**未做任何新研究**、未新增任何AWS
> 資源、未執行`sam deploy`。分類定義：
> - `CREATED_BY_STACK`：`sam deploy`本身會建立，部署者不需事先準備
> - `EXTERNAL_PREREQUISITE`：部署前必須已存在/已取得，`sam deploy`
>   不會、也不應該自動建立
> - `OPTIONAL`：可省略，省略後對應功能會誠實降級（非崩潰）
> - `NOT_READY`：目前程式碼/範本尚未涵蓋，需額外工作或真實環境才能定案

## AWS Region

`EXTERNAL_PREREQUISITE`。`infra/template.yaml`未寫死region。本輪
`sam validate`/`sam build`皆以`--region us-east-1`示範執行，但這只是
驗證用參數，不代表正式競賽環境必須用此region。**待確認**：
`BedrockModelId`（見下方）預設之Claude 3.5 Sonnet模型在正式部署
region是否可用，需於該region實際開通Bedrock model access後才能確定
（Bedrock模型可用性依region而異，本輪無AWS帳號可查證）。

## AWS Authentication

`EXTERNAL_PREREQUISITE`。需要正式比賽方提供之AWS帳號/IAM
使用者或角色之有效憑證（access key或SSO/AssumeRole皆可，`sam deploy`
兩者皆支援）。本開發環境全程無任何AWS帳號存取權限，此為NOT_READY
狀態中「等待正式比賽環境」的核心項目。

## IAM / CloudFormation Permissions

`EXTERNAL_PREREQUISITE`。部署者所用之IAM身分需有足夠權限建立：
`AWS::Serverless::Function`（Lambda）、`AWS::IAM::Role`（每個
Function之執行角色，由SAM依各Function之`Policies:`自動產生，非
本模板手動定義）、`AWS::DynamoDB::Table`、`AWS::S3::Bucket`
（×4，見下方）、`AWS::ApiGateway::*`、`AWS::CloudFront::*`、
`AWS::Logs::LogGroup`、ECR repository（PdfFunction為Image
PackageType，`sam deploy`會提示是否由SAM管理image repo）。
具體所需IAM action清單本輪未逐條列舉（可用`sam deploy --guided`
互動流程或`aws cloudformation`錯誤訊息迭代取得，非本輪範疇）。

## Cadastral Snapshot S3 Bucket

`EXTERNAL_PREREQUISITE`。`infra/template.yaml`的
`CadastralSnapshotBucketName`參數**無預設值**，且此bucket**刻意不由
本CloudFormation stack建立**（設計理由見`backend/handlers/
cadastral_snapshot_bootstrap.py`docstring與`infra/template.yaml`該
參數註解：避免stack刪除時被bucket中的物件卡住）。部署前必須：
1. 於部署者自行管理之S3 bucket（非stack建立）
2. 上傳`data/cadastral_dataset_cache.sqlite3`至該bucket
3. 部署時傳入`--parameter-overrides CadastralSnapshotBucketName=<你的bucket>`

## Immutable Snapshot Key

`EXTERNAL_PREREQUISITE`（有預設值，可覆寫）。
`CadastralSnapshotS3Key`預設值：
```
datasets/cadastral/v2026-09-06/cadastral_dataset_cache.sqlite3
```
規則（見模板註解）：**每次resync後必須發布新key，不可覆寫既有key的
物件**，並同步更新下方Snapshot SHA256。

## Snapshot SHA256

`EXTERNAL_PREREQUISITE`（有預設值，本輪已重新驗證與本機當前檔案
完全相符，未過期）。`CadastralSnapshotSha256`預設值：
```
d2ffbe849c21273a0a6602ce25b58e940d23125c9bba779b34e1a5b589656e42
```
（對應`data/cadastral_dataset_cache.sqlite3`，426,459,136 bytes，本輪
以`hashlib.sha256`重新逐位元組計算確認相符）。此值必須與**實際上傳至
S3的物件**逐位元組相符——`cadastral_snapshot_bootstrap.py`會拒絕發布
任何checksum不符的下載結果，寧可回報`DATASET_UNAVAILABLE`也不使用
不明資料。

## SAM Parameters（`infra/template.yaml` Parameters區塊，全部4個）

| 參數 | 分類 | 預設值 | 備註 |
|---|---|---|---|
| `BedrockModelId` | OPTIONAL | `anthropic.claude-3-5-sonnet-20241022-v2:0` | 僅`ExplanationFunction`使用，未設定model access時該Function會呼叫失敗，但不影響核心估價流程 |
| `CadastralSnapshotBucketName` | EXTERNAL_PREREQUISITE | 無 | 見上 |
| `CadastralSnapshotS3Key` | EXTERNAL_PREREQUISITE | 有預設值 | 見上 |
| `CadastralSnapshotSha256` | EXTERNAL_PREREQUISITE | 有預設值 | 見上 |

## DynamoDB

`CREATED_BY_STACK`。單一Table`AIValuationCases`（`PAY_PER_REQUEST`計費、
`PK`/`SK`複合鍵、PITR啟用、SSE啟用）。無需事先準備。

## Document / PDF Buckets

`CREATED_BY_STACK`（皆4個S3 bucket，`BucketName`含`${AWS::AccountId}`
確保帳號內唯一，皆已設定`PublicAccessBlockConfiguration`全開）：
- `DocumentBucket`（`ai-valuation-documents-<AccountId>`，使用者上傳
  來源文件，90天生命週期到期）
- `PdfBucket`（`ai-valuation-pdfs-<AccountId>`，系統產出PDF，90天到期）
- `FrontendBucket`（`ai-valuation-frontend-<AccountId>`，靜態前端，
  設定`WebsiteConfiguration`但實際由CloudFront+OAC存取，非直接公開）
- `KnowledgeBaseSourceBucket`（`ai-valuation-kb-source-<AccountId>`，
  為未來RAG/Knowledge Base預留，目前無程式碼寫入/讀取此bucket）

## ECR

`CREATED_BY_STACK`（SAM自動管理）。`PdfFunction`為
`PackageType: Image`，模板本身未定義`AWS::ECR::Repository`資源——
`sam deploy`會依`samconfig.toml`設定或互動詢問是否由SAM CLI自動建立
並管理對應的ECR repository（`sam deploy --guided`預設流程）。首次
部署前無需手動建立ECR repo，但需要IAM權限允許SAM建立/推送。

## CloudFront

`CREATED_BY_STACK`。`FrontendDistribution`+`FrontendOAC`
（Origin Access Control，`SigningBehavior: always`），來源為
`FrontendBucket`，`DefaultCacheBehavior`使用AWS Managed-
CachingOptimized政策，404導向`/404.html`。部署後之
`FrontendCloudFrontUrl`為Output值，需部署後手動同步至前端
`js/config.js`（模板註解已提及，非自動化步驟）。

## Bedrock（Optional Config）

`OPTIONAL`。僅`ExplanationFunction`使用（Part F自然語言摘要，
不涉及Grade/Adjustment/Calculation/Distance等核心評價邏輯，見
`infra/template.yaml`該Function上方註解）。IAM Policy已內建
`bedrock:InvokeModel`（`Resource: arn:aws:bedrock:${AWS::Region}::
foundation-model/*`）。**額外需要**（`EXTERNAL_PREREQUISITE`，且不由
CloudFormation管理）：於部署目標region之Bedrock主控台手動啟用該
Foundation Model的model access（AWS帳號層級一次性設定，非IaC資源，
`sam deploy`無法自動完成）。若未啟用，`ExplanationFunction`呼叫會
失敗，但不影響其餘13個Function與核心估價/審查流程。

## 部署順序建議（依上述相依關係整理，本輪未新增邏輯，僅排序既有事實）

1. 取得AWS帳號存取權限（EXTERNAL）
2. 建立/選定Cadastral Snapshot S3 bucket並上傳snapshot物件、確認
   SHA256相符（EXTERNAL）
3. （如需啟用Bedrock摘要功能）於目標region手動啟用Model Access
   （EXTERNAL、OPTIONAL）
4. `sam build --use-container`
5. `sam deploy --guided`（首次；帶入`CadastralSnapshotBucketName`等
   parameter-overrides；若使用Image PackageType，依提示允許SAM管理
   ECR repo）
6. 取出Outputs（`ApiInvokeUrl`/`FrontendCloudFrontUrl`/
   `CasesTableName`），回填`frontend/app/js/config.js`之
   `API_BASE_URL`並將`MODE`改為`"production"`
7. 針對第5-6項Known Limitations（29秒逾時／EDGE vs REGIONAL／
   S3 bootstrap實測耗時）進行真實環境benchmark，必要時調整
   `CollectDataFunction`之`MemorySize`/`EphemeralStorage`或API Gateway
   端點設定
