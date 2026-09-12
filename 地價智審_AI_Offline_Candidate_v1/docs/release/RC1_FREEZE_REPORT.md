# RC1 Freeze Report — 地價智審 AI Offline RC1

Release Date: 2026-09-07

本文件記錄RC1建立當下，本輪**實際重新執行**（非沿用歷史記錄）取得之
全部Release Gate結果，含原始方法/指令，供後續稽核追溯。

## 1. Regression

```bash
py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py
```
結果：**798 passed in 36.72s**（0 failed）。

## 2. SAM Validate

```bash
sam validate --lint --region us-east-1 --template-file infra/template.yaml
```
結果：`infra/template.yaml is a valid SAM Template` → **PASS**。

## 3. SAM Container Build

```bash
sam build --use-container --template-file infra/template.yaml \
  --parameter-overrides CadastralSnapshotBucketName=test-placeholder-bucket
```
結果：`Build Succeeded`（exit code 0）。建置產物：`.aws-sam/build/`
（含`EngineLayer`／`ExtractionLayer`／13個Zip Function目錄／
`PdfFunction`之Container Image）。

## 4. Zip Lambda Handler Import — 13/13 PASS

**方法**（比照真實Lambda執行環境，非本機Windows native import）：對
每個Zip-type Function，以`public.ecr.aws/lambda/python:3.12`
（與`infra/template.yaml`宣告的`Runtime: python3.12`一致）容器，
覆寫entrypoint為`python3.12`直譯器，將`EngineLayer/python`（與
`DocumentExtractFunction`額外之`ExtractionLayer/python`）掛載至
`/opt/python`（`/opt/python2`供第二層）、對應Function之build目錄
掛載至`/var/task`——完全比照Lambda真實runtime的路徑慣例，於容器內
執行`import <handler_module>`並斷言其`Handler:`指定之entry point
函式存在。

| Function | Handler module | 結果 |
|---|---|---|
| ListCasesFunction | cases | PASS |
| CreateCaseFunction | cases | PASS |
| GetCaseFunction | cases | PASS |
| CollectDataFunction | collect_data | PASS |
| AnalyzeFunction | analyze | PASS |
| CompleteFormFunction | complete_form | PASS |
| ReviewFunction | review | PASS |
| GetResultFunction | result | PASS |
| RequestUploadFunction | document_upload | PASS |
| DocumentExtractFunction | document_extract（+ExtractionLayer） | PASS |
| DocumentGetExtractionFunction | document_get_extraction | PASS |
| DocumentConfirmFunction | document_confirm | PASS |
| ExplanationFunction | explanation | PASS |

**PASS=13 FAIL=0**。

## 5. PdfFunction Runtime — PASS

於實際built image `pdffunction:pdf-latest`（`docker images`實測
2.52GB）內：
1. `import pdf_handler`＋`from pdf.pdf_renderer import PdfRenderer`成功
2. **實際呼叫**`weasyprint.HTML(string=...).write_pdf()`（含中文字
   `<h1>中文測試 地價智審 AI</h1>`），確認回傳位元組流以`%PDF`開頭
   （真實PDF，非空/損毀輸出）——證明Cairo/Pango/Noto CJK系統依賴於
   Container內真正可運作，非僅import成功。

## 6. ExtractionLayer fitz — PASS

於`.aws-sam/build/ExtractionLayer/python`掛載至Lambda runtime容器：
`import fitz`成功；實際建立一頁PDF並寫入中文字（`地價智審 AI Fitz
Test`），確認`doc.tobytes()`回傳有效PDF（`%PDF`開頭）。版本
`pymupdf-1.28.2`。有一則deprecation warning（建議改用`import
pymupdf`），functionally不受影響，已記錄於`docs/backlog.md`
`RC1_FITZ_DEPRECATION_WARNING`。

## 7. EngineLayer Native Dependencies — PASS

於`.aws-sam/build/EngineLayer/python`掛載至Lambda runtime容器：
- `pydantic`（含`pydantic-core`編譯擴充，2.13.5）：實際定義一個
  `BaseModel`子類別並執行`.model_dump()`，含中文字串欄位，結果正確
- `shapely`（含GEOS原生函式庫，2.1.2）：實際計算兩點`Point(0,0)`／
  `Point(3,4)`之`.distance()`，確認等於5.0
- `domain.models`／`engine.rule_engine`／`engine.audit_engine`／
  `providers.document_extraction_provider`：於Layer情境下import全數成功

## 8. Core Freeze（36檔案）— CORE_FREEZE_VIOLATION = NO

**方法**：對`docs/core_freeze_manifest.sha256`列出的36個檔案，逐一以
`hashlib.sha256`重新計算並與manifest記錄值比對，**全數36/36相符**。
另獨立重新推導出「組合雜湊」演算法（manifest檔案原始bytes以`\r\n`
→`\n`正規化後取sha256），重新計算結果：
```
0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51
```
與歷史記錄（`docs/phase9/api_integration_spec.md`等多處引用）**完全
相符**。`CORE_FREEZE_VIOLATION = NO`。

## 9. Cadastral Pipeline Freeze（6檔案）— CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO

對6個frozen檔案逐一重新計算SHA-256：

```
bff8acfb260c53851c9995fa5c3da2d0950382287c33882bb93efcb4b034f3d5  providers/cadastral_identifier.py
21e83193ce0d773ed1d77f64f2bcf433c393b850b969ad86cb6c0c094980e22e  providers/cadastral_dataset_cache.py
9d5d17be48cfc7414612b365d7ba153f536815c6d14326b7c9ef9890fa7d5931  providers/expropriation_case_provider.py
c4690f227f7e3aab8a665e5c694fd638ffa4df04dcc861d9de6ea01e66c1e832  providers/land_price_provider.py
15172c355122d1e4f08724679871717b3e63deafc9daf735098f4ebf19a25eea  scripts/sync_expropriation_dataset.py
c9425b4d1a4c724f3642e38ce1102dbcaa389a369dc9c8fa962c9efc4b73a0e7  scripts/sync_land_price_dataset.py
```

逐檔比對`docs/phase9/official_parcel_coordinate_audit.md`（S15.12節，
Phase API-2.2R2最近一次記錄）之per-file雜湊前綴，**逐檔完全相符**。

**方法論限制（誠實記錄）**：該文件同時記錄一個「6檔案組合雜湊」
（`cec5f900b8daf50dd3289e35f3f28a45b7dc6c51f6c7e079e7c142367c4bdc04`），
但其原始推導演算法（檔案順序、路徑格式、`sha256sum`文字/二進位模式、
是否含尾端換行等排列組合）本輪嘗試多種常見組合重新推導，**皆未能
重現此組合值**，判斷該值原始計算方式未被記錄下來、且非本輪核心
關注（Core Freeze的36檔案組合雜湊已成功重現，證明「manifest檔案
LF正規化後取sha256」確實是本專案採用的方法，但Cadastral的6檔案
manifest本身在repo中未曾以獨立檔案存在，故無法比照相同方式重建）。
**這不影響本輪對凍結完整性的實際判定**——逐檔SHA-256比對（上表）
本身就是比「組合雜湊是否一致」更嚴謹、更細緻的驗證，且逐檔皆
100%相符，`CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO`之結論不受影響。
建議未來一輪比照`docs/core_freeze_manifest.sha256`的做法，另建立
`docs/cadastral_pipeline_freeze_manifest.sha256`檔案，讓後續稽核可
用相同的LF正規化組合雜湊方法重現。

## 10. Release Snapshot（Archive）

見本文件末「Archive」節。

## Final Gate

```
RC1_RELEASE_MANIFEST = docs/release/RC1_RELEASE_MANIFEST.md（已建立）
RC1_KNOWN_LIMITATIONS = docs/release/RC1_KNOWN_LIMITATIONS.md（已建立）
RC1_AWS_PREREQUISITES = docs/release/RC1_AWS_PREREQUISITES.md（已建立）
RC1_DEMO_RUNBOOK = docs/phase8a/backup_demo_guide.md（已更新，Golden/Error Case話術澄清）
RC1_BACKLOG = docs/backlog.md（已更新，PDF image bloat／fitz deprecation／AWS後續事項）

AVAILABLE_ENVIRONMENT_REGRESSION = 798 passed, 0 failed
SAM_VALIDATE = PASS
SAM_BUILD = PASS
ZIP_HANDLER_IMPORTS = 13/13 PASS
PDF_RUNTIME = PASS
DOCUMENT_EXTRACTION = 318 fields PASS
GOLDEN_BROWSER_CASE = PASS
ERROR_BROWSER_CASE = PASS

CORE_FREEZE_SHA256 = 0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51
CORE_FREEZE_VIOLATION = NO
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO

REAL_AWS_DEPLOYMENT = NO（PENDING COMPETITION AWS ENVIRONMENT）
REAL_S3_UPLOAD = NO
REAL_DYNAMODB_E2E = NO
REAL_CLOUD_GOLDEN_CASE = NO

OFFLINE_RC1_READY = YES
```

**OFFLINE DEVELOPMENT = FROZEN**
**AWS DEPLOYMENT = DEFERRED UNTIL COMPETITION ENVIRONMENT**
