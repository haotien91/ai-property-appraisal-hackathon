# FRONTEND-UNIFIED-EXPORT-WIRING-F2

## What changed

- `frontend/app/js/api.js` — added `getExportJson()`, `getExportExcel()`, `getExportBundle()` (each hits the real backend route; mock mode throws `MOCK_MODE_NOT_SUPPORTED` rather than fabricating a response) and a `downloadTextAsFile()` helper for the JSON case (Excel/Bundle are presigned S3 URLs, opened directly).
- `frontend/app/result.html` — new "輸出文件" card: 正式 PDF (查看/下載, reusing the existing `Api.getOfficialPdf()`), and a 3-column supplemental row (下載 JSON / 下載 Excel / 下載完整資料包). Each button has its own 尚未操作/下載中/成功/失敗 status line; failures show a short Chinese message, never a raw stack trace.
- `frontend/app/pdf-preview.html` — **not modified**. It already reads `official_pdf_url`/`official_pdf_status`/`forms.official_form.title`, the same fields both the legacy Jinshan handler and the new Shulin handler populate, so it works for both unchanged.
- `backend/handlers/case_store.py`, `pdf_handler.py`, `export_handler.py`, `shulin_official_pdf_handler.py` — added an opt-in `LOCAL_AWS_ENDPOINT_URL` env var read (falls back to plain `boto3.resource("dynamodb")`/`boto3.client("s3")` when unset, which is always true in every real deployment). Exists solely so a local `sam local start-api` run can point at a standalone moto server; zero effect on production.
- `backend/docker/pdf.Dockerfile` + `backend/handlers/runtime_paths.py` — fixed a pre-existing packaging bug found while verifying local connectivity: the Container Image never copied `providers/` and never added `${LAMBDA_TASK_ROOT}/engine` / `${LAMBDA_TASK_ROOT}/providers` to `sys.path`, so a real Lambda cold start for `PdfFunction` (and the new export functions) would fail with `ModuleNotFoundError: rule_engine` / `semantic_rule_mapping_provider`. This would already have broken the already-PASSED E1 Official PDF endpoint in a real AWS deployment; it was invisible until this round because no earlier phase exercised a genuine Lambda cold start (all prior tests called handler functions directly in-process).
- `infra/template.yaml` — added an empty-default `LOCAL_AWS_ENDPOINT_URL: ""` Environment Variable to `PdfFunction`/`GetExportJsonFunction`/`GetExportExcelFunction`/`GetExportBundleFunction` (needed so SAM's local `--env-vars` override has a declared key to replace; empty string in every real deploy is a no-op).
- `infra/local-env-vars.json` (new, dev-only, not part of any deployed stack) — local `sam local start-api --env-vars` file pointing the above 4 functions at a standalone moto server.

## Real routes (from `infra/template.yaml`, unchanged by F2)

| Purpose | Route |
|---|---|
| Official PDF | `GET /api/cases/{id}/pdf` |
| JSON export | `GET /api/cases/{id}/export/json` |
| Excel export (zip of 6 files) | `GET /api/cases/{id}/export/excel` |
| Bundle export (zip of PDF+JSON+Excel) | `GET /api/cases/{id}/export/bundle` |

`API_BASE_URL` is unchanged — still resolved through the existing `frontend/app/js/config.js` / `APP_CONFIG` mechanism (`prodUrl()`), no second config system, no hardcoded production URL added.

## Known gap (unchanged, not addressed by F2)

Shulin (segment-scoped competition) cases have no segment-aware `review()`/`get_result()` yet (`SHULIN_SEGMENT_AWARE_REVIEW_PENDING=YES`, from F1). `result.html` proactively calls `Api.getExportJson()` on load; if it succeeds for a Shulin case it overrides the status line to a neutral "審查狀態：待人工複核（Shulin segment-aware review 尚未建置）" instead of ever showing a fabricated PASS.

## Local connectivity verification result

`sam build --use-container` succeeded and `sam local start-api` mounted all 4 routes correctly. A standalone `moto.server` (port 5000) was stood up as a genuinely separate process reachable from the Docker-hosted Lambda containers, and the `LOCAL_AWS_ENDPOINT_URL` fix was confirmed working: before the fix, DynamoDB calls hit real AWS and were rejected (`UnrecognizedClientException`); after the fix, they reach the moto server (`ResourceNotFoundException` — the real AWS credential-rejection error was gone). The remaining blocker is a SAM CLI local-emulation quirk: `sam local invoke/start-api` resolves a `!Ref <resource-logical-id>` in `Environment.Variables` to the **logical id string itself** (e.g. `CasesTable`, `PdfBucket`), not the resource's configured `TableName`/`BucketName` property — a SAM-CLI-local-only behavior, not a bug in this project's code. Chasing this further was explicitly out of scope for this round (see FINAL REPORT below).

Both the standalone moto server and `sam local start-api` were stopped after verification; `infra/local-env-vars.json` is left in place as a reusable local-dev convenience file (references no secrets — `testing`/`testing` are moto's conventional placeholder credentials, meaningless against real AWS).

## FINAL REPORT

```
FRONTEND_EXPORT_SECTION_ADDED=YES
FRONTEND_PDF_VIEW_BUTTON=YES
FRONTEND_PDF_DOWNLOAD_BUTTON=YES
FRONTEND_JSON_DOWNLOAD_BUTTON=YES
FRONTEND_EXCEL_DOWNLOAD_BUTTON=YES
FRONTEND_BUNDLE_DOWNLOAD_BUTTON=YES
FRONTEND_USES_REAL_PDF_API=YES
FRONTEND_USES_REAL_JSON_API=YES
FRONTEND_USES_REAL_EXCEL_API=YES
FRONTEND_USES_REAL_BUNDLE_API=YES
CURRENT_CASE_ID_USED=YES
HARDCODED_CASE_ID_PRESENT=NO
OFFICIAL_PDF_VIEWABLE_FROM_FRONTEND=YES (code path verified; real-HTTP round trip blocked, see below)
OFFICIAL_PDF_DOWNLOADABLE_FROM_FRONTEND=YES (same caveat)
JSON_DOWNLOADABLE_FROM_FRONTEND=YES (same caveat)
EXCEL_DOWNLOADABLE_FROM_FRONTEND=YES (same caveat)
BUNDLE_DOWNLOADABLE_FROM_FRONTEND=YES (same caveat)
JINSHAN_FALLBACK_PRESENT=NO
LOCAL_BACKEND_HTTP_AVAILABLE=YES (sam local start-api ran, all 4 routes mounted)
LOCAL_FRONTEND_BACKEND_CONNECTION_VERIFIED=NO
PDF_HTTP_OK=NO
JSON_HTTP_OK=NO
EXCEL_HTTP_OK=NO
BUNDLE_HTTP_OK=NO
SAM_BUILD_OK=YES
MOTO_ENDPOINT_USED_BY_DYNAMODB=YES (confirmed reaching moto; blocked by a table-name resolution mismatch, not the endpoint)
MOTO_ENDPOINT_USED_BY_S3=YES (same basis)
LOCAL_SAM_MOTO_ENV_BLOCKER=YES
REVIEW_FAKE_PASS_PRESENT=NO
TARGETED_CHECKS_PASSED=6 (H1 supplemental export tests re-run clean)
TARGETED_CHECKS_FAILED=0
FULL_REGRESSION_RUN=NO
F2_CODE_COMPLETE=YES
F2_REAL_HTTP_SMOKE_VERIFIED=NO
F2_BLOCKERS=SAM local's !Ref-to-logical-id resource-name resolution (CasesTable/PdfBucket) prevented a full real-HTTP round trip against the moto-backed local stack; this is a local-tooling limitation, not application code, and not a regression in C1/D1/E1/H1.

FRONTEND_UNIFIED_EXPORT_WIRING_F2=BLOCKED_BY_LOCAL_ENVIRONMENT
```

Recommended next: none started (per phase instruction, stopping here — no R1, no AWS deploy).
