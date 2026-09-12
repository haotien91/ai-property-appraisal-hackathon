# RC1 Release Manifest

> 本文件為Freeze快照，記錄RC1建立當下之驗證結果。所有數字皆為本輪
> 實際重新執行取得（`py -m pytest`／`sam validate`／`sam build
> --use-container`／實際於Lambda runtime container內執行import與
> native依賴驗證），非沿用歷史記錄未查證之數字。方法與原始輸出見
> `docs/release/RC1_FREEZE_REPORT.md`。

```
Release Name:      地價智審 AI Offline RC1
Release Date:      2026-09-07

Regression:                798 passed, 0 failed
  (py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py)

SAM Validate:               PASS
  (sam validate --lint --region us-east-1 --template-file infra/template.yaml)

SAM Container Build:        PASS
  (sam build --use-container --template-file infra/template.yaml
   --parameter-overrides CadastralSnapshotBucketName=test-placeholder-bucket)

Zip Lambda Handler Import:  13/13 PASS
  （13個Zip-type function逐一於public.ecr.aws/lambda/python:3.12容器內
    掛載對應之EngineLayer/[ExtractionLayer]＋function code、以Lambda
    runtime相同之/opt+/var/task路徑配置實際import handler module並
    斷言entry point函式存在，非本機Windows native import）

PdfFunction Runtime:        PASS
  （於pdffunction:pdf-latest實際Container Image內：①import pdf_handler
    與pdf.pdf_renderer成功；②實際呼叫weasyprint.HTML(...).write_pdf()
    產生含中文字之真實PDF位元組流，確認Cairo/Pango/Noto CJK字型系統
    依賴於Container內真正可用，非僅import成功）

Document Extraction:        318 fields PASS
  （frontend/mock/document_extraction.json，field_count=318，5項Golden
    值第二種商業區/建蔽率70%/容積率240%/中山路/18M全數存在；來源為
    LocalExtractionProvider對真實查估書表範本.pdf之PyMuPDF擷取結果，
    見OFFLINE-ACCEPTANCE-1驗收記錄）

Golden Browser Case:        PASS
  （result.html：48 passed/0 error/0 warning/0 missing/0 inconsistent，
    無false positive；見docs/phase8a/backup_demo_guide.md與
    docs/backlog.md OFFLINE-ACCEPTANCE-1章節）

Error Browser Case:         PASS
  （review.html：45 Passed/2 Error/1 Inconsistent，對應
    docs/phase8a/error_case_showcase.md既有Case A/B/C三項既有文件化
    刻意竄改場景，由AuditEngine對Golden Case+刻意竄改值重新執行取得，
    非本輪新設計）

Cadastral S3 Bootstrap:     CODE READY / STUBBED S3 VERIFIED
  （backend/handlers/cadastral_snapshot_bootstrap.py：單元測試見
    tests/test_cadastral_snapshot_bootstrap.py，已由collect_data.py
    實際呼叫（第605行），以DI-injectable s3_client通過模擬情境測試
    ALREADY_PRESENT/DOWNLOADED/DATASET_UNAVAILABLE三態；未對真實AWS
    S3 bucket執行過，無真實網路/IAM/延遲驗證）

AWS Deployment:              PENDING COMPETITION AWS ENVIRONMENT
AWS Real Runtime:            NOT VERIFIED
NLSC CAD001 Live Runtime:    DISABLED / AUTH CONTRACT PENDING
RAG:                         NOT IMPLEMENTED / OPTIONAL

Core Freeze SHA256:
  0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51
  （36檔案，見下方清單；本輪recompute與此值逐位元組相符，
    CORE_FREEZE_VIOLATION = NO）

Cadastral Pipeline Freeze（6檔）：
  逐檔SHA-256（本輪recompute，與docs/phase9/official_parcel_
  coordinate_audit.md最近一次記錄之per-file雜湊值完全一致）：

  bff8acfb260c53851c9995fa5c3da2d0950382287c33882bb93efcb4b034f3d5  providers/cadastral_identifier.py
  21e83193ce0d773ed1d77f64f2bcf433c393b850b969ad86cb6c0c094980e22e  providers/cadastral_dataset_cache.py
  9d5d17be48cfc7414612b365d7ba153f536815c6d14326b7c9ef9890fa7d5931  providers/expropriation_case_provider.py
  c4690f227f7e3aab8a665e5c694fd638ffa4df04dcc861d9de6ea01e66c1e832  providers/land_price_provider.py
  15172c355122d1e4f08724679871717b3e63deafc9daf735098f4ebf19a25eea  scripts/sync_expropriation_dataset.py
  c9425b4d1a4c724f3642e38ce1102dbcaa389a369dc9c8fa962c9efc4b73a0e7  scripts/sync_land_price_dataset.py

  最近一次文件記錄之組合雜湊（本輪未能以既有工具鏈重新反推出完全
  相同之組合演算法，故不覆寫此值；改以上方6組逐檔SHA-256作為本輪
  實際驗證依據，見RC1_FREEZE_REPORT.md「Cadastral Pipeline組合雜湊
  方法論限制」節之誠實說明）：
  cec5f900b8daf50dd3289e35f3f28a45b7dc6c51f6c7e079e7c142367c4bdc04

CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
```

## Core Freeze 36檔案清單（`docs/core_freeze_manifest.sha256`，本輪逐檔重新驗證全數相符）

```
data/rules/central_max_adjustment_range.json
data/rules/central_max_range_local_mapping.json
data/rules/individual_rules.json
data/rules/ntpc_common_zone_ratios.json
data/rules/plan_zone_floor_area_ratios.json
data/rules/regional_rules.json
data/rules/templates/bands_template.csv
data/rules/templates/factors_template.csv
data/rules/templates/matrix_template.csv
engine/adjustment_engine.py
engine/audit_engine.py
engine/calculation_engine.py
engine/calculation_validator.py
engine/central_max_range_validator.py
engine/comparable_selection_engine.py
engine/cross_form_validation_engine.py
engine/dependency_impact_analyzer.py
engine/extraction_to_submitted_form.py
engine/form_classifier.py
engine/form_completion_engine.py
engine/geo_distance_engine.py
engine/grade_engine.py
engine/human_confirmation.py
engine/land_use_ratio_engine.py
engine/land_use_ratio_validator.py
engine/road_width_resolver.py
engine/road_width_validator.py
engine/rule_engine.py
engine/rule_table_ingest.py
engine/rule_table_validator.py
engine/rule_validator.py
engine/zone_name_normalizer.py
providers/document_extraction_provider.py
schemas/field_dictionary.json
schemas/review_result.schema.json
schemas/rule_schema.json
```

（逐檔SHA-256見`docs/core_freeze_manifest.sha256`，本輪未修改此檔案本身。）
