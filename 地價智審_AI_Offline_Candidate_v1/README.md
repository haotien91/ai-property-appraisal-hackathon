# 地價智審 AI

## 原始簡化流程＋後端自動產表

前端維持原本的簡化工作流程。使用者在 `case-new.html` 上傳查估書與評價基準明細表後，後端會從文件及公開資料源補齊有證據的資料，套用既有規則引擎，產生表 3、表 5-1、表 4 的六頁正式 PDF。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-local-app.txt
powershell -ExecutionPolicy Bypass -File .\start-app.ps1
```

啟動後開啟 `http://127.0.0.1:8000/case-new.html`。資料來源、欄位狀態與限制見 [`docs/automatic_data_pipeline.md`](docs/automatic_data_pipeline.md)。

**AI 輔助不動產估價案件審查系統**｜2026 新北市 AI 智慧城市黑客松・地政局組

> 「以前」：人工查資料 → 人工查規則 → 人工計算 → 人工填表 → 人工跨表比對
> 「現在」：AI 整理資料 → 規則自動判定 → 程式精準計算 → AI 輔助填表 →
> 自動跨表審查 → AI 解釋 → 人工最終確認

## 專案狀態（誠實聲明）

| 項目 | 狀態 |
|---|---|
| Phase 1-6（需求分析／規則數位化／核心引擎／資料層＋PDF／Smart Review） | **COMPLETED** |
| Phase 7A（前端＋後端＋AWS IaC 離線就緒度） | **READY** |
| Phase 8A（離線競賽準備驗收） | **PASS** —— **Historical full regression baseline：553 passed / 0 failed**（歷史正式紀錄，含「都市計畫分區＋建蔽率＋容積率」＋Road Width Multi-Evidence Model封板＋Independent Uploaded Document Extraction Vertical Slice）。**Windows RC Cleanup稽核（2026-09-04，本輪實際重新執行，非估算）**：修復`backend/handlers/document_extract.py`原本寫死`/tmp/{document_id}.pdf`（Windows無此路徑）之可攜性問題後（改用`tempfile.gettempdir()`，見`docs/backlog.md`），本機`py -m pytest -q`重新跑出 **553 passed, 0 failed, 0 skipped**，與Historical baseline完全一致（`test_phase5_golden_pipeline.py`匯入WeasyPrint時，本輪執行環境未重現先前紀錄之`Windows fatal exception: access violation`警告，順利collect並全數passed；此為WeasyPrint/cffi在Windows探測原生Pango/cairo/GTK3函式庫時已知的環境敏感行為，並非每次重跑都必然重現，不代表本專案程式碼問題）。**修復前**之Windows本機狀態為533 passed／5 failed（皆為同一個`/tmp`路徑問題，非5個獨立bug）／另有部分環境下15項因WeasyPrint原生函式庫探測失敗而無法collect（`ENVIRONMENT_NOT_VERIFIED`，非測試本身問題）。 |
| Phase 7B（AWS 實際部署） | **DEFERRED**（尚未建立正式AWS環境，非失敗、非阻擋） |
| Phase 8B（正式競賽最終驗收） | **NOT STARTED** |

**目前沒有真實可訪問的 AWS URL 或 API Endpoint。** 所有「已完成」之描述，
指「程式碼/IaC本身邏輯正確，且經本地測試/Mock驗證通過」，不代表已在真實
AWS帳號中部署驗證。詳見 [`docs/phase7/architecture.md`](docs/phase7/architecture.md)
開頭聲明與 [`docs/phase8a/offline_readiness_report.md`](docs/phase8a/offline_readiness_report.md)。

## 核心能力

- **Rule Engine**：131項區域因素＋84項個別因素評價基準，全數deterministic，
  可追溯至評價基準明細表原始頁碼。
- **Grade / Adjustment / Calculation Engine**：優劣等級判定、修正率矩陣查表、
  完整精度保留之數值計算（重現官方Golden Case答案212,958元/M²）。
- **Form Completion Engine**：Structured Input → 表1/表5-2/表4 完整填寫，
  每個欄位皆有 raw_value→rule→grade→adjustment→formula→calculation→
  final_value 完整追溯鏈。
- **PDF Output**：weasyprint + Noto CJK字型，正確渲染繁體中文，官方書表本身
  非可填PDF，採Fallback Reproduction並於PDF上明確標示。
- **Smart Review**：AuditEngine + CrossFormValidationEngine + 
  CalculationValidator + RuleValidator + DependencyImpactAnalyzer，5個
  deterministic引擎，可偵測Grade Error / Adjustment Error / Cross-form
  Inconsistent，並提供Downstream Impact追溯。
- **Data Acquisition Layer**：7個Provider介面，Adapter Pattern設計，
  `DATA_PROVIDER_MODE`切換不需改動呼叫端程式碼。5個已接上OpenStreetMap
  即時查詢（已用非金山區真實座標實測）；`land_use_provider`已接上
  NtpcZoningProvider（本地分區圖資快照）＋LandUseRatioEngine（建蔽率/
  容積率兩層規則庫）；`road_provider`的`RealRoadProvider`已具備
  RoadWidthResolver多來源仲裁架構，但因**尚未確認任何可靠即時道路寬度
  來源**（詳見`docs/backlog.md`「Road Width Multi-Evidence Model」查證
  記錄），real模式下`main_road_width`誠實回報UNKNOWN，非Mock回退。
  > **⚠️已過時/superseded（2026-09-03）**：上一句原寫「其中5個已接上
  > OpenStreetMap...另2個（地政登記/道路寬度資料）因無可靠公開資料源
  > 仍為Mock」，此描述已不準確——地政登記（land_use_provider）已於
  > 更早一輪串接，道路寬度（road_provider）本輪已新增RealRoadProvider
  > 架構（雖仍誠實回報UNKNOWN，但已非「維持Mock」）。保留原文字僅為
  > 維持歷史紀錄。
- **Independent Uploaded Document Extraction**（第一輪，engine/provider
  層）：`FormClassifier`（deterministic-first表單分類）＋
  `DocumentExtractionProvider`（`LocalExtractionProvider`以PyMuPDF
  文字層＋bounding box對本repo歸檔之`查估書表範本.pdf`實際擷取9個
  代表性欄位，Golden Case真值全數驗證正確；`TextractExtractionProvider`
  CODE_READY但非AWS_RUNTIME_VERIFIED）＋`engine/human_confirmation.py`
  （低信心欄位結構上無法未經確認即進入規則判斷）＋
  `engine/extraction_to_submitted_form.py`（銜接既有`SubmittedFormData`
  ／`AuditEngine`，Golden／Error PDF E2E皆已打通）。尚無backend Lambda
  handler／上傳端點串接，見`docs/backlog.md`

**全程無LLM參與正確性判定**：Bedrock（規劃中）僅用於自然語言摘要，
Grade/Adjustment/Calculation/Distance全數由deterministic Python引擎完成。

## 專案結構

```
domain/          Pydantic領域模型（CompetitionCase, FieldCompletion, AuditIssue...）
engine/          11個deterministic引擎（RuleEngine, CalculationEngine, AuditEngine, RuleTableValidator...）
providers/       7個Data Acquisition Provider（5個支援Mock/Real雙模式，2個維持Mock）
pdf/             PdfTemplate + PdfRenderer（weasyprint）
scripts/         決賽現場CLI工具（ingest_rule_table.py：新評價基準明細表→可用規則JSON）
data/            規則資料（131+84條規則）、Golden Case、Demo Error Cases、依存關係圖、規則表CSV模板
data/sources/    官方來源原始檔案歸檔（比賽文件/法規/都市計畫書/GIS原始ZIP），見docs/source_inventory.md
schemas/         JSON Schema（rule_schema, review_result.schema, field_dictionary）
backend/         9個Lambda Handler + case_reconstruction共用邏輯
frontend/app/    改造自Makerthon_test_1之9個App Pages + index.html（含新增document-review.html文件上傳/擷取/人工確認頁）
infra/           SAM Template（19資源）+ Step Functions ASL
tests/           553項pytest測試（Rule/Calculation/Golden Case/Smart Review/Handler E2E/規則表接入/真實資料來源/分級制度彈性/LandUseRatioValidator↔AuditEngine串接＋後端資料流E2E＋DATA_PROVIDER_MODE fail-fast＋GIS Evidence checksum可追溯性＋Road Width Multi-Evidence Model（engine＋backend）E2E＋Independent Uploaded Document Extraction（FormClassifier/DocumentExtractionProvider/human confirmation/Golden＋Error PDF E2E＋backend document handlers E2E）。Historical full regression baseline：553 passed/0 failed；2026-09-04 Windows RC Cleanup稽核修復document_extract.py之/tmp路徑可攜性問題後重新執行同樣得到553 passed/0 failed，見docs/backlog.md）
docs/            Phase 1-7完整文件 + Phase 8A競賽準備文件
```

## 快速開始（本地驗證，Mock Mode）

```bash
# 執行完整測試套件
cd tests && python3 -m pytest -v

# 本地啟動前端（Mock Mode，無需後端）
cd frontend/app && python3 -m http.server 8000
# 瀏覽器開啟 http://localhost:8000/index.html

# 產生Golden Case PDF
cd /path/to/repo
python3 -c "見 docs/phase8a/demo_script.md 完整範例"
```

## 部署至真實AWS（尚未執行，供未來部署者參考）

依 [`docs/phase7/aws_deployment_runbook.md`](docs/phase7/aws_deployment_runbook.md)
17步驟操作，完成後於 
[`docs/phase7/aws_runtime_acceptance_checklist.md`](docs/phase7/aws_runtime_acceptance_checklist.md)
逐項勾選確認。

## 完整文件索引

| Phase | 內容 |
|---|---|
| [`docs/phase1/`](docs/phase1/) | 官方需求分析、Evidence Matrix |
| [`docs/phase2/`](docs/phase2/) | 業務流程、表單對應、依存關係 |
| [`docs/phase3/`](docs/phase3/) | 規則數位化、Rule Engine規格 |
| [`docs/phase4/`](docs/phase4/) | Domain Model、Frontend盤點、API Contract |
| [`docs/phase5/`](docs/phase5/) | Data Provider、Geo Engine、PDF規格 |
| [`docs/phase6/`](docs/phase6/) | Smart Review架構、Document Extraction決策 |
| [`docs/phase7/`](docs/phase7/) | AWS架構、服務選型、部署Runbook |
| [`docs/phase8a/`](docs/phase8a/) | 離線競賽準備、Demo Script、Readiness Report |
| [`docs/backlog.md`](docs/backlog.md) | 已知限制與未來精進項目 |

## 授權與歸屬

前端基於 Makerthon_test_1（HTML Codex「Poseify」模板），依LICENSE.txt要求
保留頁尾歸屬連結，未經修改移除。
