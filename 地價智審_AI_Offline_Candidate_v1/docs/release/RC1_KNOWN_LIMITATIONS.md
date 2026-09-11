# RC1 Known Limitations

> 誠實原則：本文件只記錄**尚未完成/尚未驗證**之事項，不得將已知限制
> 寫成已完成。凡本文件列出者，代表RC1當下**確定沒有**做到，而非「可能
> 沒做到」。已完成事項見`RC1_RELEASE_MANIFEST.md`。

## 1. 正式 AWS 尚未部署

`sam deploy`從未對任何真實AWS帳號執行過。`infra/template.yaml`已通過
`sam validate --lint`與`sam build --use-container`（見Release Manifest），
但這只證明範本語法正確、artifact可建置，**不等於**部署至真實帳號會
成功（IAM權限、Service Quota、跨服務相依關係僅能在真實部署時才能
完整驗證）。

## 2. Offline Browser Demo 使用 Mock Mode

`frontend/app/js/config.js`預設`MODE:"mock"`，所有展示流程讀取
`frontend/mock/*.json`靜態fixture，非呼叫真實API Gateway/Lambda。
瀏覽器操作驗收（OFFLINE-ACCEPTANCE-1）確認之PASS結果，驗證範疇是
「前端流程可否完整走完＋顯示邏輯是否誠實」，**不等於**「後端Lambda在
真實AWS上可正常回應」。細部行為澄清（避免措辭誤導）：

- `DOCUMENT_FILE_SELECTION = PASS`（瀏覽器`<input type=file>`實際選取
  查估書表範本.pdf成功）
- `DOCUMENT_UPLOAD = MOCK_SKIPPED`（Mock Mode下`Api.putDocumentFile`
  跳過真實`fetch`，未執行任何S3 PUT，見`frontend/app/js/api.js`）
- `REAL_S3_UPLOAD_VERIFIED = NO`
- `FIELD_CONFIRMATION_UI = PASS`（確認畫面互動、按鈕、清單渲染正常）
- `FIELD_CONFIRMATION_PERSISTENCE = MOCK_ONLY`（`Api.confirmDocument`
  Mock分支僅本地回顯，未呼叫真實後端）
- `REAL_BACKEND_CONFIRMATION_VERIFIED = NO`
- `OFFLINE_BROWSER_PDF_OUTPUT = PASS`（Mock fixture提供之PDF檔案實際
  可開啟、含正確中文字與關鍵數值）
- `PDF_RUNTIME_GENERATION = PASS`（PdfFunction Container Image內
  weasyprint實際render出含中文之PDF，見Release Manifest；此為
  **本地Docker容器**內驗證，非透過已部署之真實Lambda）
- `AWS_PDF_END_TO_END = NOT_VERIFIED`

## 3. 真實 S3 Upload 尚未驗證

`RequestUploadFunction`/`DocumentConfirmFunction`等handler程式碼與單元
測試（moto模擬）皆存在且通過，但從未對真實S3 bucket執行過一次真實
`PUT`。`REAL_S3_UPLOAD = NO`。

## 4. DynamoDB Cloud E2E 尚未驗證

`CasesTable`（`infra/template.yaml`）語法正確、可被`sam build`解析，
但從未在真實AWS帳號上建立過，故未有任何真實讀寫延遲/一致性/IAM權限
之實測資料。`REAL_DYNAMODB_E2E = NO`。

## 5. 407MB Cadastral Snapshot：S3 → /tmp Bootstrap Code Ready，尚未真AWS Benchmark

`backend/handlers/cadastral_snapshot_bootstrap.py`（Phase DEPLOY-1E）
已完成、已單元測試、已由`collect_data.py`實際呼叫，行為（checksum
驗證＋atomic publish＋stale檔案清除）皆有測試覆蓋。**但**：真實S3
下載407MB於Lambda冷啟動情境下的實際耗時，是否能在API Gateway REST
API固定29秒逾時內完成，**從未在真實AWS環境測量過**（見下方第6點）。
`CollectDataFunction`目前設定`MemorySize: 1536` /
`EphemeralStorage: 1536`為**保守推估值**，非實測後之調校值。

## 6. API Gateway 目前為 EDGE，正式部署建議 REGIONAL，是否需 >29 秒待真 AWS Benchmark 後決定

`infra/template.yaml`之`ApiGateway`（`AWS::Serverless::Api`）未設定
`EndpointConfiguration`，故採API Gateway預設值EDGE-optimized。REST
API固定29秒逾時為平台限制，非本模板可調整之`Timeout`設定；若真實
AWS環境下第5點之S3下載+checksum驗證耗時經常逼近或超過29秒，需評估
方案（如：改用非同步Step Functions流程、預先於部署時暖機、或改走
REGIONAL端點降低延遲）。此決策**待真實AWS benchmark後**才能拍板，
本輪未變更端點設定。

## 7. CAD001 Live Runtime 未啟用

`NLSC_CAD_API_ENABLED`等相關環境變數未出現於`infra/template.yaml`，
部署後預設關閉（`CAD001_RUNTIME = AUTH_REQUIRED`/
`AUTH_CONTRACT_UNVERIFIED`，視feature flag狀態而定，皆為誠實、非
fabricated結果，見`docs/phase9/official_parcel_coordinate_audit.md`）。
NLSC會員資格/API金鑰皆**未申請**。

## 8. OCR Scanned Image Runtime 尚未正式完成

文件擷取（`LocalExtractionProvider`）以PyMuPDF讀取PDF文字層+bounding
box，已對`查估書表範本.pdf`驗證表1/表4之代表性欄位（見
`docs/backlog.md`「Independent Uploaded Document Extraction」章節）。
**表5-2欄位擷取僅完成分類，未完成表格式版面之逐欄位擷取**；
`TextractExtractionProvider`為CODE_READY但**從未對真實AWS Textract
執行過**（`NotImplementedError`保護，防止誤用）；對純掃描影像（無
文字層）之PDF，本系統目前無OCR能力可用。

## 9. New Taipei Runtime Jurisdiction 為主要Ready範圍

已接上真實資料源之Provider（`LandPriceProvider`／
`RealNtpcZoningProvider`／`RealExpropriationCaseProvider`等）之查詢
範圍與已驗證之Golden Case，皆以**新北市**轄區為主要對象；其他縣市
之資料涵蓋範圍與正確性，本輪未系統性驗證。

## 10. PDF Image 存在Bloat但不阻擋Deployment

`backend/docker/pdf.Dockerfile`第23行`COPY data ${LAMBDA_TASK_ROOT}/
data`複製整個`data/`目錄（本輪實測808MB，含407MB cadastral sqlite與
400MB `data/sources/`）進PdfFunction Container Image，但`pdf/`套件與
`pdf_handler.py`實際上完全不讀取這些檔案（僅需`data/rules/`與
`data/dependency_graph.json`，比照`EngineLayer`Makefile已窄化之複製
範圍）。本輪實測`pdffunction:pdf-latest`最終image大小為**2.52GB**
（Docker `DISK USAGE`），雖遠低於Lambda Container Image的10GB上限、
不構成build或deploy失敗，但拖慢image push/pull與冷啟動，屬不必要
浪費。已記錄於`docs/backlog.md`，本輪未修正（依指示僅整理文件，不
改核心功能/Dockerfile）。

## 11. RAG 未實作且非核心必要功能

Retrieval-Augmented Generation（若涉及以向量檢索輔助
`ExplanationFunction`之自然語言摘要）**完全未實作**，亦不在
Grade/Adjustment/Calculation/Distance等核心評價邏輯之必要路徑上
（`ExplanationFunction`定位為Part F「僅自然語言摘要」，見
`infra/template.yaml`註解）。`RAG = NOT_IMPLEMENTED / OPTIONAL`，
不影響RC1之Release Gate判定。

## 附錄：其他既有、非本輪新發現之限制（引用自 docs/backlog.md，未重複展開）

- `review.py`MVP範圍邊界：審查的是「案件自身計算結果之再現性」而非
  「與獨立提交來源之比對」（因無真實Textract整合）——刻意的MVP範圍
  邊界，非缺陷。
- `regional_comparable_factors`（比較標的側區域因素）尚未由
  `collect_data.py`持久化——僅比準地（base）側已修復。
- `providers/road_provider.py`（道路寬度）Real模式下仍全數回報
  UNKNOWN（無官方結構化資料源）；Golden Case main_road_width=18M為
  `SOURCE_UNCONFIRMED`（估價書申報值，非獨立官方來源可驗證）。
- `providers/land_use_provider.py`剩餘13個欄位（禁限建/排水/地勢等）
  real模式下仍固定回傳UNKNOWN。
- NLSC道路寬度語意對應（`NLSC_ROAD_WIDTH_SEMANTIC_MAPPING`）與TGOS
  （`TGOS_AUTH_PENDING`）皆未實作/未取得授權，見`docs/backlog.md`
  對應章節。
- `providers/document_extraction_provider.py:405`使用`import fitz`
  （PyMuPDF舊別名），本輪實測確認functionally正確但已產生
  deprecation warning（「Use `import pymupdf` instead」），建議未來
  migrate，非RC1阻擋項目。
