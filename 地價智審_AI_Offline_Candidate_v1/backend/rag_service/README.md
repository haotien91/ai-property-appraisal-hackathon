# 查估結果問答原型

单一 Agent + 唯讀工具循環，沒有 routing Agent。每次請求載入目前案件完整 JSON；只在需要時搜尋文件、讀文件或讀其他明確允許的案件。**不執行估價、不回填表格、不修改生成結果。**

## 1. 離線啟動（不用安裝套件）

從 專案根目錄 `地價智審_AI_Offline_Candidate_v1/`，在 macOS 的 zsh 或 Linux 的 bash 執行（Python 3.12 建議）：

```bash
python3 -m venv .venv
source .venv/bin/activate
export RAG_API_TOKEN='replace-with-your-local-test-token'
export RAG_MODE=mock
python -m backend.rag_service.server
```

服務僅監聽 `127.0.0.1:8090`。`GET /health` 回報模式。
mock 模式只驗證 JSON 載入、session、權限與 SSE；**回覆是明確標示的固定示範，不是模型回答，也不是 RAG 品質驗證**。兩份案件皆為虛構。

另一個終端機（curl 的 -N 關閉輸出緩衝，直接看到 SSE）：

```bash
curl -N --fail-with-body http://127.0.0.1:8090/projects/demo-a/chat/stream \
  -H 'Authorization: Bearer replace-with-your-local-test-token' \
  -H 'Content-Type: application/json' \
  --data '{"message":"請解釋本案道路寬度"}'
```

瀏覽器即時串流請用 `client.js` 的 fetch reader。前端 dev server 設同源 proxy `/projects` → `http://127.0.0.1:8090`；本原型不開放任意 CORS。Mac/Linux 使用 repo 相對路徑，沒有依賴 Windows 磁碟路徑。

## 2. 接 AWS Bedrock

```bash
source .venv/bin/activate
python -m pip install -r backend/rag_service/requirements.txt
export RAG_MODE=bedrock
export AWS_REGION='us-west-2' # 改為主辦方開通的區域
export BEDROCK_MODEL_ID='<支援 ConverseStream 與 tool use 的模型或 inference profile ID>'
# 以 AWS_PROFILE 或 AWS SDK 預設 credential chain 提供主辦方憑證，勿寫入檔案。
python -m backend.rag_service.server
```

需要 `bedrock:InvokeModelWithResponseStream` 與選用模型／inference profile 的適當權限。AWS 錯誤不會退回 mock。
目前未在真 AWS 帳號實測；AgentCore 部署亦未包含在本版。Harness 與 HTTP 層分開，未來可以加 AgentCore entrypoint，不必改成 routing 架構。

官方介面參考：https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html

## 3. 導入隊友 JSON：只需加外層，不打散表格

放在 `backend/rag_service/data/projects/<project_id>.json`：

```json
{
  "project_id": "case-001",
  "version": "2026-09-12-v1",
  "title": "案件名稱",
  "is_mock": false,
  "data": { "隊友原本的JSON": "整份放在這裡，保留原始欄位與結構" }
}
```

`data` 接受任意 JSON；不用對照固定28項或固定用地類別。請盡量保留單位、標的身分、規則來源、計算與篩選紀錄。缺少的內容模型只能說缺少，不能假裝推算就是原始紀錄。

設定 `export RAG_ALLOWED_PROJECTS='case-001,case-002'` 並重啟。可用 `RAG_DATA_DIR` 指向自己的資料目錄（內含 projects/ 與 documents.json）。案件資料變更必須更新 version。

這不是自動辨識任意 JSON 語意的保證：若欄名不清楚，可在 data 加欄位字典。原型每次完整載入，不截斷 JSON；超過模型 context 時會明確失敗，需要後續按實際資料量調整。

## 4. 文件檢索

初版是中文雙字詞＋英文詞的簡單關鍵字重疊排序，**尚無向量資料庫、embedding、BM25 或語意 reranker**。有基本檢索與來源回傳，可先測問題覆蓋率；search_documents 工具內部可替換成 Bedrock Knowledge Bases Retrieve，其他流程不變。

隨附非官方示範說明與作業手冊可抽取的文字頁面（引用採PDF頁碼，非印刷頁碼）。無文字頁未索引，不代表已讀取圖片。要重新匯入或新增參考資料，使用：

```bash
python -m backend.rag_service.import_documents '黑客松參考資料/土地徵收補償市價查估作業手冊.pdf' --shared --skip-empty-pages --out backend/rag_service/data/documents.json
python -m backend.rag_service.import_documents '黑客松參考資料/評價基準明細表範例.pdf' --project-id demo-a --out backend/rag_service/data/documents.json
```

PDF 按頁保存文字與頁碼，不靠任意字數切表；**並不代表PDF文字層已正確還原表格**。明細表矩陣應由生成／抽取流程確認後保存在案件 JSON，PDF 文字只供查找、解說與引用，不作精確矩陣來源。遇到空白文字層會停止，沒有偷偷做 OCR。原始 PDF 不變。
指定 --skip-empty-pages 才會略過無文字頁，並列印未索引頁碼；本次手冊第2、4頁未索引，其餘167頁保留文字。文件預設沒有 scope 就不可查；只有明確 `project_ids: []` 才是共通文件。適用於特定案件的規則不要設成 shared。

## 5. API / 前端

`POST /projects/{id}/chat/stream`，Authorization Bearer，UTF-8 JSON：

```json
{
  "message": "比較兩案道路条件，說明是否可直接比等級",
  "conversation_id": null
}
```

首次不傳 conversation_id，使用 session 事件回傳的 ID 接續。同一對話綁定目前案件與後端授權範圍；已讀案件版本更新時回409，需要新對話。
請求只接受 message 與 conversation_id。後端 allowlist 決定哪些案件可以被存取；模型根據問題使用 search_projects 查案件基本資訊，再以 read_project 讀取。不明確的案件需向使用者確認。預設只載入本案完整JSON，不把全部授權案件塞進context。
文件搜尋預設僅本案＋共通文件；當輪成功讀取其他授權案件後，才開放該案文件。權限由程式保證，但是否有必要跨案由模型依指令判斷，不能宣稱語意範圍已由程式硬性保證。

事件：`session`、`status`、`citation`、`text_delta`、`done`、`error`。
收到 error 或未收到 done 即斷線，不可標示回答完成。citation 表示實際提供給模型的來源，**不是逐句引用正確性已驗證**。模型內文依 prompt 引用 `[doc:ID]` 或 `[project:ID@版本#/data/路徑]`，正式產品仍應驗證引用。

## 6. 範圍與限制

- 這是單一使用者／單一共享API token的本機原型。allowed projects 是這個 token 的伺服器端 allowlist；正式多使用者部署必須接身份驗證與逐使用者ACL，不能把此 token 放進公開網頁。
- 本機 ThreadingHTTPServer 不是正式對外伺服器。部署時需 production ASGI／反向代理、HTTPS、速率限制、串流轉送與身份驗證。
- 對話只存記憶體，最多1000個；每案保留最近10組問答。重啟會消失，沒有跨工作程序共享。
- 模型最多6輪；超限／截斷回 error，不假裝完成。未做效能承諾。
- 只有唯讀工具，沒有網際網路搜索。其他縣市資料必須事先匯入並取得授權。
- 抽取內容及模型回答仍需評估；工具權限由程式保護，回答事實性不能只靠 prompt 保證。

## 7. 驗證

```bash
python -m unittest backend.rag_service.test_service -v
```

涵蓋模型碎片工具參數、工具結果回送、跨案拒絕、文件scope、路徑穿越、截斷與輪數失敗、HTTP認證、SSE與session隔離。使用假的Bedrock事件驗證協議，不能替代真AWS與回答品質測試。
