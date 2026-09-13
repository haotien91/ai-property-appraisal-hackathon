# 本機後端整合

來源：`origin/basic/document-processing-pipeline`，commit `f6ca3a6`。
保留目前分支的首頁、建立案件、多檔 PDF 擷取、三步驟導覽與預覽版面。
兩個分支沒有共同 Git 祖先，因此匯入後端、規則、模板與對應測試，再調整前端介面。

## 啟動

在 repository 根目錄執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-local-app.ps1
```

開啟 http://localhost:8124/index.html 。保留啟動視窗；關閉後服務也會停止。
可使用 `-Port 8125` 指定其他連接埠。`python -m http.server` 只提供靜態頁面，不具備後端 API。

新電腦需要 Python 3.11 以上，在 repository 根目錄建立環境：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\地價智審_AI_Offline_Candidate_v1\backend\requirements-local-app.txt
```

## 使用範圍

- PDF 基本資料仍由瀏覽器讀取，保留四張勘查表選擇與人工修正。
- 建立案件後，文件傳給同一台電腦上的 Python 服務。
- 組員的六頁產表流程限定樹林住宅 P001-00～P004-00 指定案例，採內建案例、規則，配合 NLSC 與 OSM 公開資料查詢；不是任意 PDF 的完整自動估價引擎。
- 評價基準 PDF 目前記錄雜湊，尚不會自動轉換成計算規則。修改基本欄位也不會改寫內建案例計算資料。
- 公開資料不足或未確認時保留缺漏／待確認，結果狀態為 MANUAL_REVIEW_REQUIRED；不以假資料替代。
- PDF 頁籤對應同一檔案的第 1、5、6 頁；前四頁是四個區段勘查表。下載選單提供完整 PDF、Excel ZIP、含 JSON 的整包。
- 首頁透過本機 API 顯示已建立案件。AI 聊天仍是示範回覆，尚未串接 LLM；本次沒有部署 AWS。
- 案件輸出在 `data/local_cases/`，公開資料快取在 `data/public_data_cache/`，均不加入 Git。

## 驗證

`tests/test_public_data_workflow.py` 與 `tests/test_local_workflow_integration.py` 共 26 項測試通過，涵蓋來源證據、未知值、區段資料、規則運算、表格欄位、前端介接，以及案件儲存、六頁 PDF、Excel／JSON 匯出。Excel 模板合併儲存格的註解會被 openpyxl 略過，會顯示警告。
另以使用者提供的兩份 PDF 在 localhost:8124 實際完成上傳、公開資料查詢、產表、頁籤跳頁與結果讀取。
測試命令（在專案子目錄）：`../.venv/Scripts/python.exe -m pytest tests/test_public_data_workflow.py -q`。
