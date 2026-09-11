# 地價智審 AI：黑客松交接

程式碼位於 [`地價智審_AI_Offline_Candidate_v1/`](地價智審_AI_Offline_Candidate_v1/README.md)。

## 下載與啟動

先安裝 Python（後端 AWS runtime 使用 3.12）、Git 與 Git LFS。下載前執行一次：

```sh
git lfs install
git clone <此專案的 GitHub URL>
cd <clone 產生的資料夾>
git lfs pull
cd 地價智審_AI_Offline_Candidate_v1
python -m http.server 8000 --directory frontend/app
```

開啟 http://localhost:8000/index.html 。目前前端預設為 mock 模式，可直接展示，不需要 Node.js、npm 或 AWS 憑證。這是前端範例展示；完整後端 API 與真實資料模式需要另外部署／設定，並非啟動靜態伺服器就會啟動後端。

後端依賴與部署說明請見專案內 `backend/requirements*.txt`、`.env.example` 與 `docs/phase7/aws_deployment_runbook.md`。PDF 輸出另有 WeasyPrint 系統依賴，請依專案文件安裝。

## GitHub 收錄範圍

保留程式碼、測試、依賴清單、基礎設施設定、`.env.example`、共用 AI skills、規則 JSON、mock、Golden Case、範例 PDF、官方來源資料及 SQLite。`frontend/app/lib/` 是現成瀏覽器依賴，沒有 npm 安裝流程可以取代它，因此保留。

根目錄 `.gitignore` 排除本機 IDE／助手設定、憑證、Python 虛擬環境與快取、`node_modules/`、SAM 建置產物、測試覆蓋率及 SQLite 暫存 sidecar。內層原有 `.gitignore` 繼續生效。SQLite 主檔不排除；提交快照前應停止寫入並完成 WAL checkpoint，以免快照缺少尚未合併的資料。

`data/cadastral_dataset_cache.sqlite3` 約 406.7 MiB，已在根目錄 `.gitattributes` 設定 Git LFS。其餘 SQLite 正常收錄。GitHub 一般 Git 檔案上限為 100 MiB，因此上傳者也必須在第一次 `git add` 前執行 `git lfs install`，並確認 `git lfs ls-files` 包含這個資料庫。資料庫內容須隨 LFS 一起推送；只有 pointer 的 checkout 無法使用。接收者請使用 Git LFS clone／pull，不要依賴 Download ZIP。

來源資料與黑客松參考資料目前保留，避免破壞來源追溯與交接。這次檢查不代表已逐份確認外部文件的再散布授權或個資；公開發布前仍需確認分享範圍。

## 本次檢查（2026-09-11）

- 此 Git repo 原本尚無已追蹤檔案，沒有需要解除追蹤的舊產物。
- 排除 `.git/` 與 `.aws-sam/` 後，工作資料約 868.6 MiB（含尚未排除的本機快取）；最大單檔為上述地籍資料庫。
- 已對非建置檔案搜尋常見 AWS access key、GitHub token、OpenAI project key 與 private-key 標記，未找到匹配；這不是完整的敏感資料稽核。
- Initial commit 包含忽略規則、LFS 屬性與交接說明；本次整理未執行完整應用測試。
