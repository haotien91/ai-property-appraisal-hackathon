# 書表儲存基礎設施

此目錄是 2026-09-12 新建的獨立部署，不使用舊版 infra/template.yml。

- AWS account：137336531963
- Region：us-west-2
- Profile：hackathon（本機設定，不包含於 repository）
- Stack：ntpc-appraisal-artifact-storage-v1
- Bucket：ntpc-appraisal-artifacts-137336531963-us-west-2
- DynamoDB table：ntpc-appraisal-metadata-v1

## 範圍

template.json 建立私有 S3 bucket、要求 TLS 的 bucket policy，以及按量計費 DynamoDB。S3 啟用版本與預設加密、禁止公開存取、由 bucket 擁有者擁有物件；DynamoDB 啟用時間點還原。儲存、請求及備份會產生 AWS 費用。

刪除 stack 或取代資源時保留 bucket／table；這不是自動清除比賽資源的腳本。S3 不自動刪除舊版本，只清理超過七天未完成的 multipart upload。

S3 版本功能不會自行讓應用不可變：文件完成登錄後，API 必須儲存並使用具體 VersionId，不能只讀相同 key 的 latest。

本 stack 僅管理儲存。後續已另外部署 `ntpc-artifact-import-api-v1`，提供 IAM 認證的 JSON/PDF 匯入接口，見 ../../services/artifact-import/README.md；前端登入與 Agent adapter 尚未完成。沒有更動既有手冊 bucket 或同事的 AgentCore／Lambda。

## IP 設定

organizer-source-ips.json 記錄主辦方要求允許的四個來源 IPv4，各使用 /32，未套用封鎖規則。主辦方「允許這四個 IP」不等同「只允許這四個 IP」。對外 API 的來源限制等待網路範圍確認。

這些來源限制若需要，應在 HTTPS API 入口設定；不用開放資料庫 port 或公開 bucket。IP 白名單不取代使用者／服務認證。AWS 內部 Agent 工具也不應被誤綁到現場四個外網 IP。

## 驗證／更新

```sh
aws cloudformation validate-template --template-body file://infra/artifact-storage/template.json --profile hackathon --region us-west-2
aws cloudformation describe-stacks --stack-name ntpc-appraisal-artifact-storage-v1 --profile hackathon --region us-west-2
```

後續變更先檢查 change set 是否會 replace 資料資源；不得直接覆寫或清空現有資料。此部署未建立共用密鑰或授權同事既有角色讀寫新 bucket；由後續 API／Agent 整合加入必要權限。

完整交付規格見 [artifact-delivery-contract-v1.md](../../docs/artifact-delivery-contract-v1.md)。目前選定的儲存引擎為 DynamoDB，表的 PK／SK 為服務內部實作，不是生成端 JSON 格式。
