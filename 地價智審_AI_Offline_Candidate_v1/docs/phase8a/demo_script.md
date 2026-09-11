# Phase 8A — Demo Script（離線腳本，非正式Live Demo宣告）

> 本腳本假設**Mock Mode**（本地或Frontend部署後但Backend尚未接上）或
> **未來真實部署後**皆可使用（頁面邏輯完全相同，見`js/api.js`設計）。
> 本文件**不宣稱**AWS已就緒，僅提供展示流程腳本本身。

## 開場（30秒）

> 「不動產估價案件審查，目前是人工比對表1/表5-2/表4三張表單、人工查
> 評價基準明細表、人工計算修正率與加總——耗時且容易出錯。我們的系統
> 讓AI協助完成這整個流程，同時保留deterministic規則引擎確保正確性，
> 人工僅做最終確認。」

## Step 1：Landing Page（index.html，30秒）

- 展示深色/粉紅主題、「AI智慧填表 + AI智慧審查」產品故事。
- 點擊「建立案件」導向`case-new.html`。

## Step 2：建立案件（case-new.html，30秒）

- 展示表單（案號/區段編號/行政區/用地類別）。
- 說明：決賽當日區段編號/範圍通常已預填（Phase 1官方確認）。

## Step 3：資料收集（data.html，30秒）

- 展示7個Data Provider取得之欄位統計（已取得/缺漏）。
- **重點台詞**：「缺漏欄位不會被AI猜測，而是明確標示需人工確認。」

## Step 4：因素分析（analysis.html，1分鐘）

- 展示Grade/Adjustment表格，逐條附`rule_id`可追溯至評價基準明細表頁碼。
- **重點台詞**：「這些等級判定完全由規則引擎deterministic產生，不是
  AI憑感覺判斷。」

## Step 5：書表填寫（form-fill.html，1分鐘）

- 展示完整Traceability：raw_value→rule→grade→adjustment→formula→
  calculation→final_value。
- 指出`MANUAL`徽章之欄位（如「價格形成因素之相近程度」），說明這是
  刻意保留給估價師專業判斷的欄位，AI不代為決定。

## Step 6：PDF預覽（pdf-preview.html，30秒）

- 說明官方查估書表範本.pdf本身非可填PDF（技術限制已於Phase 5發現並
  記錄），系統採Fallback Reproduction，PDF上仍完整標示每個欄位之
  AUTOMATIC/MANUAL來源。

## 選用附加：Document Review Optional Demo（document-review.html，2分鐘，可視時間彈性穿插於Step 6之後或全部省略）

> 此為 FORM_COMPLETION 書表填寫流程之外，**第二種**可送入智慧審查的資料
> 來源（`submission_source=DOCUMENT`）。與Step 2-8之既有流程互相獨立、
> 互不影響，跳過本段不影響主線Demo完整性。

流程：**案件 → 上傳PDF → Extraction → 顯示CELL_BLANK摘要 → Human
Confirmation → DOCUMENT Review → Result**

- 從stepper點入「文件上傳（選用）」，畫面頂部**必定**先看到一個明顯橫幅，
  清楚標示目前是 **LOCAL/MOCK** 或 **PRODUCTION**：
  - **LOCAL/MOCK**（目前唯一可實際展示之模式，見下方「已知限制」）：
    黃色橫幅明確聲明「不會呼叫真實S3 Presigned Upload、Lambda擷取或雲端
    人工確認寫入」。**重點台詞**：「這裡展示的擷取結果，並非憑空編造的
    示範數字，而是`LocalExtractionProvider`對真實Golden Case PDF實際執行
    擷取後的輸出（見`scripts/build_document_extraction_mock.py`），只是
    整個流程在瀏覽器端本地完成，沒有真的打S3/Lambda。」
  - **PRODUCTION**（需真實AWS部署後）：綠色橫幅，會呼叫真實
    `requestDocumentUpload`→`putDocumentFile`→`extractDocument`→
    `getDocumentExtraction`→`confirmDocument`API合約，行為與Mock模式下
    頁面邏輯完全相同（見`js/api.js`設計），僅資料來源真實。
- 選擇一個PDF並點「上傳並擷取」，展示擷取結果摘要：**總欄位／成功擷取／
  需人工確認**三個數字，以及「需人工確認」原因摘要（文件原始欄位空白／
  低信心辨識／解析失敗／未知因素）。
  - **重點台詞**：「這個案例168筆需人工確認，100%是CELL_BLANK——PDF裡
    比較標的2、3欄位本來就是空白，AI誠實回報『沒有資料』，不是猜錯，也
    不是168個AI錯誤。」
  - 展開依「比較標的」分組、預設收合之明細表，示範修改「確認值」後送出
    Human Confirmation。
- 前往`review.html`，切換「審查資料來源」為DOCUMENT，執行審查。
  - **若在LOCAL/MOCK模式下操作**：畫面會明確提示「FORM_COMPLETION與
    DOCUMENT共用相同的示範Review fixture，切換來源不會改變顯示的審查
    結果」——**誠實說明**，不隱瞞Mock模式下兩來源尚無法展示差異化結果。
- 最終導向`result.html`，與既有FORM_COMPLETION路徑共用同一套Result UI，
  未另建第二套結果渲染邏輯。

## Step 7：智慧審查（review.html，1分鐘，**核心亮點**）

- 若使用Demo Error Case資料，展示3個真實偵測到的錯誤案例：
  - Grade Error（18M道路寬度誤填等級）
  - Adjustment Error（深度差異率誤填）
  - Cross-form Inconsistent（表5-2/表4總修正數不一致）
- **重點台詞**：「這3個問題都是我們刻意植入、以Golden Case真實數據
  為基準的測試案例，系統100%偵測到，且每個問題都附上會影響到哪些
  下游欄位（Downstream Impact），包括最終比準地比較價格。」
- **若被問及Cross-form檢查是否只是自己比對自己**：可明確回答——表5-2
  「影響地價區域因素總修正數」與表4「區域因素調整百分率」在資料模型中
  是兩個獨立儲存欄位（field_id/form分屬不同），可分別被獨立修改；系統
  已通過雙向測試驗證（僅竄改表5-2一側能被偵測、僅竄改表4一側也能被
  偵測），並非簡單的A==A同義比對。

## Step 8：案件結果（result.html，30秒）

- 展示最終比準地比較價格（Golden Case為212,958元/M²）。

## 收尾（30秒）

> 「以上流程，Rule Engine、Adjustment Engine、Calculation Engine、
> Smart Review全部是deterministic程式碼，經過553項自動化測試驗證
> （Historical full regression baseline：553 passed/0 failed；
> 2026-09-04 Windows RC Cleanup稽核重新執行同樣得到553 passed/0 failed）。
> AI（Bedrock）僅負責最後的自然語言摘要說明，不參與任何數值判定。」

## 已知限制（誠實說明，若被問及）

- 若被問到「這是部署在AWS上的嗎」：誠實回答——程式碼與IaC已完整準備
  （SAM Template、Lambda Handler、Step Functions定義皆已通過離線驗證），
  但受限於開發環境本身無AWS存取權限，尚未實際部署，見
  `docs/phase7/aws_deployment_runbook.md`可供任何人接手部署。
- 若被問到「Bedrock/AgentCore真的用了嗎」：誠實回答——程式碼已完成
  （`explanation.py`），但從未實際呼叫過，因同樣受限於AWS存取權限。
- 若展示「文件上傳 Document Review Optional Demo」且被問到「這是LOCAL/
  MOCK還是AWS Live」：誠實回答——目前僅LOCAL/MOCK可實際展示（見上方該段
  落之橫幅說明），Production Mode之真實S3 Presigned Upload/Lambda擷取
  合約程式碼已備妥但同樣受限於AWS存取權限，從未實際打過真實AWS。
- 前端9個App Pages（含新增之`document-review.html`）之瀏覽器互動驗收：
  static validation（語法檢查／HTML標籤配對／fixture JSON解析／本地
  HTTP資源可達性）已完成；人工瀏覽器實際操作驗收（manual browser
  acceptance）依實際執行狀態填寫，若尚未執行則誠實回報「pending」，
  不得宣稱已完成瀏覽器驗證卻未真正操作過。

## 時間總計

主線（Step 1-8）約5-6分鐘，符合官方簡報時間限制（Phase 1 REQ-014）；
加計選用之Document Review Optional Demo（2分鐘）約7-8分鐘，視現場時間
彈性決定是否展示此段。
