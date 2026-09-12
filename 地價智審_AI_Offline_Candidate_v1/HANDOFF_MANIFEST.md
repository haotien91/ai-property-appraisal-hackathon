# HANDOFF_MANIFEST

# Project

地價智審 AI — AI輔助不動產估價案件審查系統
2026新北市AI智慧城市黑客松・地政局組

## Current Status

Phase 8A Clean Pass

AWS Runtime Not Yet Deployed

## Verified Test Status

**Historical full regression baseline：553 passed / 0 failed**（歷史正式紀錄，含「都市計畫分區＋建蔽率＋容積率」＋Road Width Multi-Evidence Model封板＋Independent Uploaded Document Extraction Vertical Slice；舊值388/493已過時）。

**Windows RC Cleanup稽核（2026-09-04，本輪`py -m pytest -q`實際重新執行，非估算）：553 passed, 0 failed, 0 skipped**，與Historical baseline完全一致。此前Windows本機曾只能得到533 passed／5 failed（`backend/handlers/document_extract.py`原本寫死`/tmp/{document_id}.pdf`，Windows無此路徑，於`test_backend_document_handlers_e2e.py`5項測試皆拋`FileNotFoundError`——同一根因，非5個獨立bug）／另有環境下15項因`tests/test_phase5_golden_pipeline.py`匯入`weasyprint`時WeasyPrint/cffi在Windows探測原生Pango/cairo/GTK3函式庫失敗而無法collect（`ENVIRONMENT_NOT_VERIFIED`）。本輪已將`document_extract.py`之暫存路徑改為`tempfile.gettempdir()`（跨平台，Lambda/Linux仍解析為`/tmp`），修復後前述5項Windows failures已轉為PASS；WeasyPrint原生函式庫探測本輪執行環境下未重現失敗（未安裝任何系統級GTK套件，純屬環境本身之已知敏感行為，見下）。環境：Python 3.13.2、weasyprint 69.0、cffi 2.1.1、Windows-11-10.0.26200-SP0。
`cfn-lint infra/template.yaml`：0 errors
`sam validate --lint`：Valid SAM Template，0 errors

## Main Components

- **Frontend**：`frontend/app/`（改造自Makerthon_test_1，9個App Pages + index.html，Mock/Production雙模式；新增`document-review.html`——文件上傳/擷取/人工確認，選用流程）
- **Backend**：`backend/handlers/`（9個Lambda Handler + case_reconstruction共用邏輯）
- **Rule Engine**：`engine/rule_engine.py`（131+84條規則，deterministic）
- **Grade Engine**：`engine/grade_engine.py`
- **Adjustment Engine**：`engine/adjustment_engine.py`
- **Calculation Engine**：`engine/calculation_engine.py`（完整精度保留）
- **Comparable Selection**：`engine/comparable_selection_engine.py`（不動產估價
  技術規則§25/§26排除檢核＋§27權重建議，見evidence_matrix.md REQ-029）
- **Form Completion**：`engine/form_completion_engine.py`
- **PDF**：`pdf/`（PdfTemplate + PdfRenderer，weasyprint）
- **Smart Review**：`engine/audit_engine.py` + `engine/rule_validator.py` + `engine/calculation_validator.py`
- **Cross-form Validation**：`engine/cross_form_validation_engine.py`（表5-2/表4獨立欄位比對）
- **Dependency Impact**：`engine/dependency_impact_analyzer.py`
- **Data Acquisition**：`providers/`（7個Provider；5個支援`DATA_PROVIDER_MODE=real`
  即時查詢OpenStreetMap，2個維持Mock，見`docs/phase8a/real_data_provider_guide.md`）
- **Rule Table Ingestion**：`engine/rule_table_ingest.py`、`engine/rule_table_validator.py`、
  `scripts/ingest_rule_table.py`（決賽現場新評價基準明細表→可用規則JSON，見
  `docs/phase8a/rule_table_ingestion_guide.md`）
- **AWS IaC**：`infra/template.yaml`（SAM，19資源）+ `infra/statemachine/workflow.asl.json`

## Important Entry Points

| 用途 | 檔案 |
|---|---|
| 前端首頁 | `frontend/app/index.html` |
| 前端API客戶端 | `frontend/app/js/api.js` + `frontend/app/js/config.js` + `frontend/app/js/case-context.js`（case_no/document_id單一存取慣例） |
| Backend Lambda handlers | `backend/handlers/cases.py`、`collect_data.py`、`analyze.py`、`complete_form.py`、`review.py`、`pdf_handler.py`、`result.py`、`explanation.py` |
| Rule Engine | `engine/rule_engine.py` |
| 規則資料 | `data/rules/regional_rules.json`、`data/rules/individual_rules.json` |
| 決賽現場新規則接入 | `scripts/ingest_rule_table.py`（CSV模板見`data/rules/templates/`） |
| 真實資料來源即時查詢驗證 | `scripts/smoke_test_real_providers.py`（人工執行，非pytest收集項目） |
| Golden Case | `data/golden/golden_case_input.py` |
| Demo Error Cases | `data/demo_errors/build_demo_submission.py` |
| AWS IaC | `infra/template.yaml` |
| Step Functions定義 | `infra/statemachine/workflow.asl.json` |
| 測試套件 | `tests/test_*.py`（553項；Historical baseline 553 passed/0 failed，2026-09-04 Windows RC Cleanup稽核重新執行同樣得到553 passed/0 failed；見上方「Verified Test Status」） |

## How To Run Locally

```bash
# 安裝Python依賴
pip install -r backend/requirements.txt --break-system-packages
pip install -r backend/requirements-pdf.txt --break-system-packages
pip install pydantic jsonschema jinja2 weasyprint pytest moto boto3 --break-system-packages
# 完整553項需再補（皆為既有requirements-*.txt已宣告之依賴，非本輪新增）：
pip install pymupdf shapely pyshp pyproj --break-system-packages

# 啟動前端（Mock Mode，無需後端/AWS）
cd frontend/app
python3 -m http.server 8000
# 瀏覽器開啟 http://localhost:8000/index.html

# 產生Golden Case PDF（見docs/phase8a/backup_demo_guide.md完整範例）
python3 -c "見 docs/phase8a/backup_demo_guide.md 之完整可執行程式碼"
```

## How To Run Tests

```bash
cd tests
python3 -m pytest -v          # 詳細輸出
python3 -m pytest             # 摘要輸出

# IaC驗證
cd ../infra
cfn-lint template.yaml
sam validate --lint
```

## AWS Deployment Status

**NOT YET DEPLOYED**

- AWS Code / IaC：READY
- AWS Runtime：NOT YET DEPLOYED
- Bedrock：CONFIG_READY / RUNTIME_VALIDATION_PENDING
- Step Functions：CONFIG_READY / RUNTIME_VALIDATION_PENDING
- Frontend AWS Hosting：PLANNED / NOT DEPLOYED
- Backend AWS Hosting：PLANNED / NOT DEPLOYED

部署步驟見 `docs/phase7/aws_deployment_runbook.md`（17步驟），部署後請依
`docs/phase7/aws_runtime_acceptance_checklist.md`逐項確認。

## Next Phase

Phase 7B — AWS Runtime Deployment（需真實AWS帳號權限，本交付版本本身
未執行任何AWS部署動作）

## Known Limitations

- Independent Uploaded PDF Review 尚未完成（目前僅Structured Case Smart
  Review，見`docs/phase6/document_extraction_spec.md`之TextractAdapter
  擴充點設計）
  > **⚠️已過時/superseded（2026-09-03）**：第一輪（engine/provider層）
  > 已完成——`FormClassifier`＋`LocalExtractionProvider`（真實PyMuPDF
  > 擷取，已用本repo歸檔之Golden Case PDF驗證9個代表性欄位）＋
  > `engine/human_confirmation.py`＋Golden／Error PDF E2E皆已打通並串接
  > 既有`AuditEngine`，見`docs/backlog.md`「Independent Uploaded Document
  > Extraction Vertical Slice」一節。**仍未完成**：backend Lambda
  > handler／上傳端點串接、表5-2欄位擷取、`TextractExtractionProvider`
  > 之AWS_RUNTIME_VERIFIED。保留原文字僅為維持歷史紀錄。
- AgentCore Runtime 尚未驗證（僅S3來源桶IaC規劃＋設計文件，屬P1/Optional）
- AWS Runtime 尚未部署（見上方AWS Deployment Status）
- `review.py`目前之Handler-level資料重建邏輯，與`tests/test_smart_review.py`
  之測試重建模式一致，但簡化了部分邊緣情境處理（見`docs/backlog.md`）
- 4項pyflakes程式碼風格警告（未使用之import/變數，不影響功能）
- 16個Legacy缺失前端圖片（0個位於Critical Demo Path，見
  `docs/phase4/frontend_inventory.md`與Phase 8A盤點結果）
- README.md／完整文件請見`docs/backlog.md`最新彙整
