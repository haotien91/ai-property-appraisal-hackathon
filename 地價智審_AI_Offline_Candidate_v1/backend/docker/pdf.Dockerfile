# PDF Function Container Image — 見 docs/phase5/pdf_output_spec.md
# weasyprint 需要 Cairo/Pango 系統函式庫 + Noto CJK 字型，一般zip-based Lambda
# 無法可靠提供這些系統層級依賴，故此Function改採Container Image封裝（AWS Lambda
# 原生支援，image大小限制10GB，遠大於本函式實際需求）。
FROM public.ecr.aws/lambda/python:3.12

# Cairo/Pango（weasyprint執行期依賴）+ Noto CJK繁體中文字型
# 對應本專案於Phase 5獨立驗證過之技術選型（reportlab CID字型在無字型檔情況下
# 會靜默產生空白頁，已改用weasyprint，見docs/phase5/pdf_output_spec.md）。
RUN dnf install -y \
        cairo pango gdk-pixbuf2 \
        google-noto-sans-cjk-ttc-fonts \
    && dnf clean all

COPY 地價智審_AI_Offline_Candidate_v1/backend/requirements-pdf.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt --target ${LAMBDA_TASK_ROOT}

# 共用引擎程式碼（與EngineLayer內容相同，Container Image路徑無法掛載zip Layer，
# 故此處改為直接COPY進image）
# FRONTEND-UNIFIED-EXPORT-WIRING-F2: `providers` was missing from this
# COPY list entirely -- undetected until this round's real `sam local
# start-api` end-to-end run (every prior round only ever called handler
# functions directly in-process, which never exercises a genuine Lambda
# cold start's actual import path). pdf_handler.py now transitively needs
# it (via shulin_official_pdf_handler.py -> rule_engine_factory.py ->
# providers/semantic_rule_mapping_provider.py) -- this bug would have
# broken the ALREADY-PASSED E1 Official PDF endpoint in a real AWS
# deployment too, not just the new F2 export routes. See backend/handlers/
# runtime_paths.py's matching fix (adds these same 2 directories to
# sys.path for the Container Image branch, previously only `pdf` was
# added there).
COPY 地價智審_AI_Offline_Candidate_v1/domain ${LAMBDA_TASK_ROOT}/domain
COPY 地價智審_AI_Offline_Candidate_v1/engine ${LAMBDA_TASK_ROOT}/engine
COPY 地價智審_AI_Offline_Candidate_v1/providers ${LAMBDA_TASK_ROOT}/providers
COPY 地價智審_AI_Offline_Candidate_v1/pdf ${LAMBDA_TASK_ROOT}/pdf
COPY 地價智審_AI_Offline_Candidate_v1/data ${LAMBDA_TASK_ROOT}/data
COPY 地價智審_AI_Offline_Candidate_v1/schemas ${LAMBDA_TASK_ROOT}/schemas
COPY 地價智審_AI_Offline_Candidate_v1/export ${LAMBDA_TASK_ROOT}/export
COPY 地價智審_AI_Offline_Candidate_v1/backend/handlers ${LAMBDA_TASK_ROOT}/

# SUPPLEMENTAL-JSON-EXCEL-EXPORT-H1: this SAME image is reused (via each
# function's own ImageConfig.Command override in infra/template.yaml, NOT
# a separate Dockerfile) for GetExportJsonFunction/GetExportExcelFunction/
# GetExportBundleFunction -- openpyxl is added here for their Excel export
# path; export/ is COPYed above for the same reason. This CMD remains
# PdfFunction's own default entrypoint.
COPY services/artifact-import/client.py services/artifact-import/deliver_generated.py services/artifact-import/prepare_pipeline_delivery.py services/artifact-import/splitter.py ${LAMBDA_TASK_ROOT}/artifact_import/
CMD ["pdf_handler.get_pdf"]
