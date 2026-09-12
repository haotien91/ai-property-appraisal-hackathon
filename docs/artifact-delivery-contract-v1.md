# 書表交付與儲存接口 v1

日期：2026-09-12

本文件定義完整接口目標。已部署生成端 `/v1/imports` 匯入 adapter，實際接口與限制見 ../services/artifact-import/README.md；以下案件管理及 `/api/v1/...` 路徑仍是未全部實作的目標。2026-09-12 已部署獨立的私有 S3 與 DynamoDB 基礎設施，見 ../infra/artifact-storage/README.md。舊版 infra/template.yml 與既有 API 文件不能視為本接口已存在的證據。

## 1. 分工與範圍

- 生成端：接收輸入檔，產出書表 PDF 與每張表對應的 JSON，回報檔案清單與 PDF 頁碼關聯。
- 應用 API：管理行政區、案件、組別、生成版本及檔案登錄；負責 S3 上傳／下載入口與存取權限。
- 前端：透過應用 API 列出與預覽檔案，不自行推測 S3 路徑。
- Agent 工具：透過已授權的組別、版本與檔案清單取得 JSON；官方手冊維持獨立檢索來源。

**本規範不定義書表 JSON 的內部欄位、公式格式或跨表引用格式。由生成端完成後提供 schema 與範例，再實作讀取 adapter。** 以下識別碼與檔案 metadata 均為 API 外層資料，不要求寫進書表 JSON。

資料庫已選 DynamoDB，bucket 為 ntpc-appraisal-artifacts-137336531963-us-west-2。登入／服務認證方式及生成任務啟動方式尚待選定；不影響本文件的識別與交付契約。部署授權來自使用者後續明確請求，不能僅憑此文件推定新的部署授權。

## 2. 資料層級

行政區 → 案件 → 組別 → 生成版本。

- 案件可包含多個組別。
- 一組接收評價基準明細表、地價區段勘查來源檔；來源檔可包含 n 個區段。
- 一次生成對應一個組別的一個版本，包含 n 張區段勘查表、一張比較法估價表、一張區域因素分析表。
- 一張表一份 JSON。因此上述版本有 n + 2 份書表 JSON。
- PDF 的檔案切分獨立：區段勘查可合併成一份 PDF，也可分成多份；比較法估價及區域因素分析各一份 PDF。
- 同組重新生成建立新版本，保留舊版本；不得把不同版本的書表混合成一次交付。

## 3. 識別碼與不可變資料

以下技術 ID 均由應用 API 產生 UUIDv4；案號、名稱與區段編號不是技術 ID。

| ID | 識別對象 |
| --- | --- |
| case_id | 案件 |
| group_id | 案件內的一組估價資料 |
| run_id | 該組一次生成版本 |
| form_id | 該版本的一張書表；重跑產生新的 form_id |
| document_id | 一個實體檔案，例如 JSON 或 PDF |

同一張表的 JSON 與 PDF 透過 form_id 關聯；合併 PDF 可以對應多個 form_id。segment_code 是生成端提供的區段編號 metadata，不能取代 form_id。新舊版本跨表精確匹配暫不承諾。

建立 run 時固定其輸入 document_id 清單。來源檔被替換時建立新文件／新 run，不覆寫已完成版本使用的來源。完成交付後，該 run 的檔案內容及關聯不可修改。

## 4. 檔案登錄資料

### 書表登錄（不屬於 JSON 內容）

- form_id：API 分配。
- form_type：survey / comparison / regional_factors。
- segment_code：survey 必填；其他表可省略。
- title：顯示名稱。
- order：顯示順序，非 PDF 頁碼。
- producer_schema_version：生成端對其 JSON 格式的版本標記；實際格式由生成端定義。

### 文件登錄

- document_id：API 分配。
- run_id 或輸入文件所屬 group_id：由服務端關聯。
- kind：source_criteria / source_survey / form_json / form_pdf。
- filename、content_type、size_bytes、sha256。
- form_ids：產出文件所對應的書表；JSON 恰好一個，PDF 可一個或多個。
- bucket、object_key：服務端保存，不由前端或模型指定。
- pdf_page_count：PDF 必填，完成交付時檢查。

另保存每張表的 json_document_id 及 pdf_document_id、page_start、page_end。頁碼為 **從 1 開始的 PDF 實體頁序，含首尾**；不是紙面印出的頁碼。一張表可以跨多頁，區段順序不能從檔名推測。

## 5. S3 與 DB 邊界

S3 儲存原始輸入、書表 JSON 與 PDF。DB 保存層級、版本、文件 metadata、關聯及完成狀態，不保存 PDF 本體或把有時效的下載 URL 當永久位置。

建議服務端配置的 key 形式：

```text
cases/{case_id}/groups/{group_id}/inputs/{document_id}.pdf
cases/{case_id}/groups/{group_id}/runs/{run_id}/{document_id}.json
cases/{case_id}/groups/{group_id}/runs/{run_id}/{document_id}.pdf
```

Bucket 名稱由部署配置決定。下載時，以 document_id 向 API 申請有時效的 URL；重新開啟或連結過期重新申請。JSON 讀取工具經服務端授權後取得內容，不依賴使用者的下載 URL。

生成書表不自動加入官方手冊知識庫。寫入 S3、完成文件交付和 Agent 能理解 JSON 是三件不同的事。

## 6. 交付流程與 API

以下路徑以 /api/v1 為前綴；是待實作接口。請求／回應外層契約版本與 producer_schema_version 分開管理。

| 方法與路徑 | 行為 |
| --- | --- |
| POST /groups/{group_id}/runs | 接受 input_document_ids，建立版本；回傳 run_id |
| POST /runs/{run_id}/forms | 接受書表 metadata 陣列；逐項回傳 form_id |
| POST /runs/{run_id}/documents | 登錄文件 metadata 與 form_ids；回傳 document_id、upload_url、expires_at、required_headers |
| POST /runs/{run_id}/complete | 接受書表與 JSON／PDF／頁碼關聯；檢查並原子完成交付 |
| POST /runs/{run_id}/fail | 記錄生成失敗代碼及可顯示說明 |
| GET /groups/{group_id}/runs | 分頁列出生成版本；以 created_at 新到舊排序 |
| GET /runs/{run_id} | 回傳狀態、輸入文件、書表、文件及頁碼關聯 |
| GET /documents/{document_id}/access?disposition=inline\|attachment | 授權後回傳 url、expires_at；預覽／下載用途 |

建立 forms 和 documents 可分批執行，以支援生成端完成後才知道區段數的情況。列表分頁使用 opaque cursor，預設 50 筆，上限 100，回傳 next_cursor。

所有 POST 必須帶 Idempotency-Key。同一路徑與同一授權主體下，同 key 同 payload 回傳原結果；同 key 不同 payload 回傳 409。記錄保留於該 run 存續期間；建立 run 的 key 隨其結果保留。網路重試不建立額外版本。

上傳 URL 到期可透過 POST /documents/{document_id}/upload-access 重取，僅限未完成版本。上傳採 PUT，必須使用回傳的 required_headers。完成後的物件需以固定 S3 object version 或服務端封存物件鎖定讀取，避免尚未到期的上傳 URL 改變已交付內容；具體方式由儲存實作決定。

### complete 的外層資料範例

下例 ID 是占位文字，不是有效 UUID；n=1 僅為縮短範例，不限制實際區段數。

```json
{
  "forms": [
    {"form_id": "survey-form-id", "json_document_id": "survey-json-id", "pdf_document_id": "survey-pdf-id", "page_start": 1, "page_end": 2},
    {"form_id": "comparison-form-id", "json_document_id": "comparison-json-id", "pdf_document_id": "comparison-pdf-id", "page_start": 1, "page_end": 1},
    {"form_id": "factors-form-id", "json_document_id": "factors-json-id", "pdf_document_id": "factors-pdf-id", "page_start": 1, "page_end": 1}
  ]
}
```

此 manifest 僅傳文件關聯，不包任何書表 JSON 內容。合併 survey PDF 時，多張 survey form 使用相同 pdf_document_id，各有頁碼範圍。

## 7. 完成交付與錯誤

run 狀態：pending → ready 或 failed。pending 表示交付尚未完成，不宣稱 worker 已開始執行。failed 版本保留診斷；重做建立新 run。

complete 必須確認：

1. 引用的書表／文件都屬於同一 run；沒有遺漏已登錄的書表。
2. 至少一張 survey，恰好一張 comparison、一張 regional_factors。
3. 每張表有一份獨立 JSON 及一個有效 PDF 頁碼範圍。
4. 所有產物已上傳；實際長度、雜湊、可解析的 JSON／PDF 與登錄資料一致。
5. 頁碼在對應 PDF 範圍內，文件與 form_ids 關聯一致。

驗證失敗回報明確缺件／錯誤，run 保持 pending，可補齊後以新 Idempotency-Key 重送 complete。API 只做 JSON 語法檢查；在生成端 schema 尚未提供前，不聲稱已驗證表格內容、公式或語意。

ready 一次發布整組檔案清單；不能讓使用者或 agent 讀到半組文件。前端預設開啟最新 ready 版本，有新 pending／failed 時仍保留前次 ready 版本供查看。這是生成狀態，不是主頁的「待覆核／已完成」審核分類。

錯誤回應統一為 {"error":{"code":"...","message":"...","details":{}}}。HTTP：400 格式錯誤；401 未認證；403 無權存取；404 資源不存在；409 狀態或冪等衝突；422 交付驗證失敗；429 限流，附 Retry-After；5xx 暫時性服務錯誤。具體速率限制待部署設定。

每個接口驗證 caller 的案件權限及 ID 所屬關係。UUID 不是授權；生成服務只能登錄其被授權 run 的產物。確切 token／角色配置待認證方案選定。

## 8. Agent 讀取契約

- 對話固定 case_id、group_id、run_id；版本切換不得默默沿用舊資料上下文。
- 工具先列出該 run 的 form_id，再按 ID 讀 JSON。跨表比較讀同 run 的相關文件。
- 不掃描案件底下所有版本的 S3 檔案，也不靠「表3」選中某一張。
- JSON 結構未知時只能提供檔案取得接口；欄位搜尋、計算解釋與跨表語意引用須等生成端 schema 才能定義。
- 超出回應大小時明確回報需分頁／縮小範圍，不能靜默截斷。
- 既有 case-forms 使用 case_id + XLSX，尚未符合本規範；需更新 adapter 及工具介面後驗收，不能直接宣稱串接完成。

## 9. 交付驗收與待補規格

用 mock 檔即可先驗收：4 張 survey JSON、comparison JSON、regional_factors JSON；survey 合併 PDF；來源文件引用；所有頁碼關聯。另驗證多組隔離、兩次生成隔離、缺件拒絕完成、冪等重試與完成版本不被覆寫。

生成端後續提供：各類 JSON schema／完整範例、空值與數值規則、計算依據能提供的內容、跨表關聯欄位、schema 版本相容策略。這些不在本次代為決定。

應用端實作前補定：已啟用 S3 版本下的文件 VersionId 固定讀取實作、認證方式、檔案大小限制、URL 有效期與限流參數、來源檔上傳接口、如何啟動生成任務。生成端目前只需按 run 交付檔案，不必實作這些基礎設施。
