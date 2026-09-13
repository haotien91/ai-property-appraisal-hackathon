# 團隊串接設定

`.env.shared.example` 是會提交到 Git 的非機密預設值。主程式、RAG 及 artifact 上傳 CLI 都會載入它，再讀取專案根目錄的 `.env`。優先序為：終端機環境變數 > `.env` > 共用範本。檔案只支援 `KEY=value`，不執行 shell 指令或變數展開。

1. 複製 `.env.example` 為 `.env`，補上已核發的值。
2. 主程式使用 `start-app.ps1` 或 `python scripts/serve_app.py --port 8124`。
3. RAG 安裝 `backend/rag_service/requirements.txt`，設定自行產生的 `RAG_API_TOKEN`，執行 `python -m backend.rag_service.server`。`RAG_MODE=bedrock` 才會使用 AWS 模型；需具有模型權限的 `AWS_PROFILE` 與正確 `BEDROCK_MODEL_ID`。
4. Artifact 安裝 `services/artifact-import/requirements.txt`，執行 `python services/artifact-import/client.py --bundle <輸出的JSON> --result <結果JSON路徑>`。CLI 會讀取共用端點，使用 AWS credential chain 做 SigV4 簽章；可用 `--profile` 指定帳號。實際上傳會建立雲端資料。

`APP_MODE=local` 使用本機案件後端；`mock` 使用前端示範資料；`production` 需填主系統的 HTTPS `API_BASE_URL`，會停用本機 API bridge。此設定注入由 `serve_app.py` 提供，單純靜態 hosting 仍須修改 `frontend/app/js/config.js`。只有前端允許的 MODE、API_BASE_URL、LOCAL_BACKEND 會傳到瀏覽器。

圖片中的 artifact 端點是獨立 `/v1/imports` API，不能填成主系統 API_BASE_URL。它使用 us-west-2；主 SAM stack 建議區域是 ap-northeast-1。BUCKET、TABLE 是 artifact Lambda 的部署設定，不是瀏覽器金鑰，應由該服務部署者設定。

仍需團隊提供：主系統 API 網址、PDF/DOCUMENT/CADASTRAL bucket 真實名稱、可用 AWS profile、NLSC 核發帳密。NLSC 的兩個功能開關維持 false，直到官方認證契約確認。圖片只有欄位名稱與占位值，不能據此取得正式密鑰。

主 SAM 部署不讀取本機 `.env`；CasesTable、PdfBucket、DocumentBucket 由 `infra/template.yaml` 建立並注入 Lambda，地籍 snapshot 與模型需透過既有 SAM parameters 設定。

共用範本、端點、程式碼與部署描述會正常提交；`.env`、AWS 憑證及私鑰維持忽略。GitHub 內容不包含真正密鑰。未取得正式憑證前，尚未驗證 NLSC、Bedrock 或 artifact 的實際雲端連線。既有問答介面與 RAG 的產品流程整合不等同於設定載入，本次未新增聊天 UI。

影片原始資料及重複副本已移除，來源 manifest 與 inventory 同步刪除。舊需求文件中以逐字稿或時間軸推導的描述屬歷史分析，不再作為可驗證來源，使用前須重新以正式命題文件確認。
