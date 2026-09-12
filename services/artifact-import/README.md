# JSON / PDF 匯入 API

本服務把生成端 schema 1.0 完整 JSON 拆成 n 張表 3、1 張表 4、1 張表 5-1，另存 context 與 review，原始 JSON 也保留。JSON 內容與數字精度不變；不進行估價計算、不呼叫模型、不把書表加入 RAG。

部署位於 AWS 137336531963 / us-west-2，stack `ntpc-artifact-import-api-v1`。實際 endpoint 與測試結果見本目錄 `deployment.json`。

## 交付前先確認的權限

1. 呼叫方 IAM role 需有 [caller-policy.json](caller-policy.json) 中指定 API 的 `execute-api:Invoke` 權限。此檔是待附加的權限範例，放進 Git 不會自動授權。
2. 本次已實測的角色是 `WSParticipantRole`。未測試或授權尚未提供 ARN 的組員 backend role。
3. 不同 role 目前有不同資料 workspace；只附加 Invoke 權限可以使用 API，但不會因此看見 WSParticipantRole 先前的案件。正式 backend／harness 要共用既有案件，需另外設定明確的角色到團隊映射，這部分尚未部署。
4. 已 imported 的 run 不可追加／替換 PDF。JSON 和 PDF 分階段產生時，先等 PDF 齊全再 complete，或用新 run 重新交付。這是交付版本限制，不是上傳失敗。

## 組員快速使用

需要 Python 3.10+、boto3，以及有 `execute-api:Invoke` 權限的 AWS 憑證。不要把憑證貼到文件或交給前端瀏覽器。可用既有 `hackathon` profile，或省略 `--profile` 使用程式執行環境的 IAM role。

```sh
python3 -m pip install boto3
python3 services/artifact-import/client.py \
  --endpoint 'https://zyte6qrr2k.execute-api.us-west-2.amazonaws.com' \
  --profile hackathon \
  --bundle /path/to/case_data.json \
  --idempotency-key 'unique-generation-request-001' \
  --result /tmp/import-result.json
```

第一輪不傳 case_id、group_id，API 會建立並回傳兩個 ID。既有案件新增組別只傳 `--case-id`；同組重新生成同時傳 `--case-id` 與 `--group-id`，並使用新的 Idempotency-Key。網路重試沿用同 key 和相同檔案／metadata，不重建版本。已存在的 ID 必須屬於目前 IAM role workspace。

腳本依序申請上傳 URL、PUT 檔案、觸發拆分、保存結果。結果檔不保存 signed URL 或 AWS 密鑰。若中途失敗，沿用終端印出的 key 重跑；相同 key 不得更換檔案內容或描述。

### 一起傳 PDF

加上 `--pdf-manifest /path/to/pdfs.json`。其中路徑相對於 manifest 所在目錄：

```json
[
  {
    "path": "survey.pdf",
    "kind": "survey_pdf",
    "segments": [
      {"segment_code": "P002-00", "page_start": 1, "page_end": 1},
      {"segment_code": "P003-00", "page_start": 2, "page_end": 2},
      {"segment_code": "P004-00", "page_start": 3, "page_end": 3},
      {"segment_code": "P001-00", "page_start": 4, "page_end": 4}
    ]
  },
  {"path": "comparison.pdf", "kind": "comparison_pdf"},
  {"path": "regional-factors.pdf", "kind": "regional_factors_pdf"}
]
```

以上是頁碼格式範例，必須按實際生成 PDF 填入。表 3 可多頁，或每區段各一份 PDF；每份 survey_pdf 都需宣告其涵蓋的區段與頁碼。另支援 `source_criteria`、`source_survey` 上傳來源 PDF。

第一版在建立 import 時一次宣告全部檔案。JSON 可以單獨匯入，但已匯入版本不可追加 PDF；之後 PDF 備齊時，以同 group_id、新 run 匯入 JSON + PDF。避免讓 agent 在同一版本讀到前後變動的資料。後續若要分階段追加，需要明確的 staging / 發布流程，不是覆寫已發布版本。

## 呼叫流程

所有 API 使用 AWS IAM SigV4（service `execute-api`，region `us-west-2`）。這是組員後端整合服務，尚不是前端使用者登入 API。

| 方法 | 路徑 | 內容 |
| --- | --- | --- |
| GET | `/v1/health` | 需認證的存活檢查 |
| POST | `/v1/imports` | 建立上傳批次；必須帶 Idempotency-Key |
| POST | `/v1/imports/{run_id}/upload-access` | pending 批次重取上傳連結 |
| POST | `/v1/imports/{run_id}/complete` | 驗證所有上傳、拆分、固定 VersionId、發布目錄 |
| GET | `/v1/imports/{run_id}` | 查狀態與書表／文件關聯 |
| GET | `/v1/imports/{run_id}/forms/{form_id}` | 讀書表，可用 `segment_code`、`field_id` 篩選 |
| GET | `/v1/imports/{run_id}/documents/{document_id}/access` | 取得固定 S3 版本的下載 URL；可帶 `disposition=inline` |

建立 import body（SHA-256 為完整小寫 hex）：

```json
{
  "bundle": {
    "filename": "case_data.json",
    "size_bytes": 246603,
    "sha256": "<64 lowercase hex characters>"
  },
  "pdfs": []
}
```

可選欄位 `case_id`、`group_id`。每個 pdf descriptor 也有 filename、size_bytes、sha256，加上 kind 和 survey 的 segments。

回應包含 `case_id`、`group_id`、`run_id` 與 uploads。按順序將每個檔案 PUT 到 url，帶 required_headers；Content-Length 必須等於申報長度。檔案直傳 S3，不塞進 API body。上傳連結 900 秒，實際可用期亦受簽署者臨時憑證期限影響。

完成後回傳：

- status = imported：JSON 已拆分登錄成功，**不是估價資料已完整或已審核**。
- pdf_complete：所有產出書表都有 PDF 關聯時才是 true。
- forms：form_id、form_type、可選 segment_code、json_document_id、可選 pdf 頁碼關聯。
- documents：包含原始 bundle、n+2 張書表 JSON、context、review，以及上傳 PDF 的固定 S3 VersionId。

本次 n=4：6 個 form；JSON-only 共 9 個 document（原始包 1＋書表 6＋context 1＋review 1）。context 保留來源 schema_version、case、segments、provenance 及未識別的頂層欄位；review 保存 review、review_status、manual_review_items。沒有把組員 JSON 的 case_id 當應用 UUID。

## 限制與驗證

- JSON 上限 5 MiB，單份 PDF 20 MiB，整批 40 MiB；最多 20 份 PDF、200 張表 3。
- 僅支援生成端 schema_version `1.0`。驗證 table3、segments、比準地與 comparisons 成員及 index 一致，拒絕重複 JSON key。
- 不驗證公式正確性，也不把缺值改成零；保留 Decimal 精度、null 與原始文字。
- S3 上傳 SHA-256 與長度必須吻合；PDF 必須可解析、非加密，頁碼需在實體 PDF 範圍內。
- 結構化讀取回應超過 12 KB 時回 413，要求縮小範圍或下載完整 JSON，不靜默截斷。
- 匯入 pending → processing → imported；可處理的驗證失敗退回 pending。一次發布完整目錄；S3 衍生寫入若中途失敗可留下未被目錄引用的檔案，重試成功後只發布成功那批；不自動清除舊資料。
- processing 有 120 秒 lease。Gateway 約 29 秒 timeout，Lambda 60 秒；逾時先 GET 查狀態，不要直接開新 run。若 Lambda 終止，lease 到期可重送 complete。
- JSON 與 PDF 是否真的同一份計算內容，仍由生成端保證；API 做格式／檔案／關聯驗證，不做內容等價判定。

## 權限與部署邊界

同一 IAM role 的不同 session 共用 workspace；不同 role 不可讀寫其案件。這是目前競賽團隊整合權限，不是個別公務員的案件 ACL。組員需有指定 API 的 execute-api:Invoke；Agent Lambda 若使用此 API，也需相應 caller role 授權／workspace 設計。

沒有加入 IP-only 封鎖，四個主辦方來源 IP 可到達認證入口，仍需有效簽章。未認證請求會被 API Gateway 擋下。S3 保持私有；沒有 CORS wildcard，也沒有把 AWS 憑證放進網頁。

**本次未更動既有 case-forms Gateway 或 landvalue-form-reader；harness 尚未切換到新 JSON API。** 已提供可按表／因素讀取的接口，但正式接入 harness 仍需調整同事工具及端到端對話測試。

這個 `/v1/imports` 是生成端的完整包 adapter，不代表原本草案的 `/api/v1/groups/...` 案件管理接口已全部實作。

## 開發測試

使用 `python3 -m pip install -r services/artifact-import/requirements-dev.txt` 安裝測試依賴。Lambda 執行環境本身提供 boto3；requirements.txt 為額外打包依賴。在 repository root：

```sh
PYTHONPATH=services/artifact-import python3 -m unittest discover -s services/artifact-import/tests -v
python3 infra/artifact-api/build.py --deps /path/to/dependencies
```

Lambda zip 僅包含 simplejson 與 pypdf 的純 Python 部分，不帶 macOS 編譯產物。部署範本見 `infra/artifact-api/template.json`；不使用舊專案 infra。

## 案件／組別目錄（2026-09-13）

不再需要先上傳檔案才能建立案件。前端／backend 可先建立有名稱的案件和組別，再把回傳的 ID 帶入 `/v1/imports`。

| 方法 | 路徑 | 資料 |
| --- | --- | --- |
| GET | `/v1/cases` | 列出目前 IAM workspace 的案件、案號與 ID |
| POST | `/v1/cases` | `{ "name": "樹林住宅用地查估案", "case_no": "1140901-99-001", "district": "樹林區" }` |
| GET / PATCH | `/v1/cases/{case_id}` | 查看／修改 name、case_no、district |
| GET | `/v1/cases/{case_id}/groups` | 列出該案件的組別名稱與 ID |
| POST | `/v1/cases/{case_id}/groups` | `{ "name": "第一組估價" }` |
| GET / PATCH | `/v1/groups/{group_id}` | 查看／修改 name、selected_run_id |
| GET | `/v1/groups/{group_id}/runs` | 新到舊列出生成版本、狀態與 pdf_complete |

兩個 POST 建立接口都需 Idempotency-Key。同 key 同內容重試回同一筆；同 key 改內容回 409。name 必填；case_no、district 可選。案號不是唯一鍵，不自動按同案號合併。PATCH 是設定指定值，不需要建立用冪等鍵；不允許改動案件／組別的父子關係或 UUID。

三種列表回 `{ "items": [...], "next_cursor": "..." }`，支援 limit（預設 50，上限 100）及 cursor。案件／組別按技術 ID 排序；生成版本按 created_at 新到舊排序。此版未提供依案號全文搜尋，應由列表取得案號與 case_id 的對應，或後續增加查詢索引。

selected_run_id 只能指向同組已 imported 的 run，不能選別組或尚未匯入的版本。未指定時不宣稱已有選定版本，前端可展示版本清單供選擇；新的匯入不覆蓋既有選擇。

為維持先前 client 相容，不帶 ID 的匯入仍會自動建立案件與組別，並加入目錄。成功匯入會以生成端 case_no、district 補上案件缺少的 metadata，不覆蓋已設定或手動修改的值。未命名案件顯示案號或「未命名案件」，未命名組別顯示「未命名估價組」；建議實際流程先命名再匯入。

目錄用 DynamoDB 獨立 index items 指向原紀錄，因此改名稱不用改 PDF／JSON，也不改 producer_case_no。既有資料的補建工具為 `infra/artifact-api/backfill_directory.py`，只加目錄與缺少的案號／行政區，不改檔案或生成版本。

前端尚未接上這些新 endpoint；目前完成的是 backend 目錄 API。

## 交付檢查（2026-09-13）

- 18 項測試涵蓋匯入、固定版本、資料隔離、目錄、PDF 頁碼、缺檔、欄位順序不影響冪等性，以及網路逾時後查詢原 run。
- 新冪等鍵以排序後的 JSON metadata 計算指紋；更新前已發出的舊鍵仍支援原欄位順序的重試。
- 每個因素陣列要求不重複的 field_id；因素若帶 base_segment_code／comparable_segment_code，必須與所屬表內標的一致。
- client 區分驗證失敗與暫時性網路／服務錯誤；驗證失敗直接回報，後者最多等待 180 秒查詢／重試同一 run。超時後仍須沿用原 key，不要建立新版本。
- requirements.txt／requirements-dev.txt 已明確排除全域 `*txt` 忽略規則，clone 後可取得依賴清單。

交付範圍：生成端可以開始串接；尚未完成跨 IAM role 團隊共用、前端登入與 harness Gateway 切換。不宣稱完成併發負載測試或正式多租戶權限驗收。

交付複查的修正版已獲 CloudFormation UPDATE_COMPLETE，且合成 JSON＋3 份 PDF 的線上匯入成功。最後的 Lambda CodeSha256 核對與合成測試資料清理因工具自動審查額度限制未執行；殘留 TEST 案件 ID 見 deployment.json 的 review_synthetic_fixture，不是正式估價案件。

## 前端真實資料預覽

```sh
python3 services/artifact-import/preview_server.py --port 8002 --profile hackathon
```

開啟 `http://127.0.0.1:8002/index.html?data=live`。不帶 `?data=live` 維持 mock。代理僅監聽 127.0.0.1，限制 Host 與可讀路徑，不提供寫入接口；AWS credentials 留在 Python process。這是本機整合預覽，不是可公開部署的使用者登入服務。

真實模式從目錄取得案件與組別，地圖依案件行政區標示；組別工作區可切換已匯入版本。PDF 使用目錄的版本固定存取連結，來源未上傳或產出 PDF 缺少時顯示空態，不用範例文件替代。分開的區段 PDF 也可從區段目錄切換。切換組別／版本會清除舊 iframe 並重新載入連結。

尚未實作：PDF 首頁 thumbnail、公開部署所需登入／session、跨 IAM role 共用、真實模式建立／改名 UI、harness 對話。真實模式暫停示範聊天與本機改名，避免誤以為已送到 backend。

無 computer use 驗證：`node services/artifact-import/tests/test_frontend_adapter.cjs` 檢查資料映射、分開 PDF、缺件與 scope。另需對實際 UI 做人工排版驗收。

## 接新版文件 pipeline 的合併 PDF

新版 `/api/cases/{id}/export/bundle` ZIP 除了 JSON、Excel、PDF，另附 `artifact-pages.json`。
PDF 檔名 `official_6_page.pdf` 暫保留相容性，實際頁數由 manifest 決定，不保證六頁。
`/api/cases/{id}/pdf` 回應也會提供 `artifact_pages` 與正確的 `official_pdf_page_count`。

先解壓到自己的工作目錄，再準備三類 PDF（survey 仍是一份多頁 PDF）：

```bash
python3 services/artifact-import/prepare_pipeline_delivery.py \
  --bundle /path/to/case_data.json \
  --pdf /path/to/official_6_page.pdf \
  --page-map /path/to/artifact-pages.json \
  --output /path/to/delivery
```

確認輸出後，沿用現有上傳 API client。`case-id` / `group-id` 是案件庫 UUID，**不是 producer case_no**。

```bash
python3 services/artifact-import/client.py \
  --endpoint https://zyte6qrr2k.execute-api.us-west-2.amazonaws.com \
  --bundle /path/to/delivery/bundle.json \
  --pdf-manifest /path/to/delivery/pdf-manifest.json \
  --case-id YOUR_CASE_UUID --group-id YOUR_GROUP_UUID \
  --idempotency-key YOUR_STABLE_JOB_UUID \
  --result /path/to/delivery/import-result.json
```

本機可另加 `--profile hackathon`；AWS 上使用 execution role，不帶 profile。
上傳重試必須重用同一批輸出與 idempotency key，不要重新生成不同 generated_at 的 JSON。
`prepare_pipeline_delivery.prepare()` 可直接從 Python 匯入；它只切檔與驗證，不會上傳。
不要把含授權資訊的 presigned URL 寫入日誌。

這次整合沒有自動替隊友的執行角色配置 IAM，也沒有部署其 pipeline。
執行角色仍須能呼叫匯入 API；不同角色的 workspace 隔離需沿用既有部署規格確認。
