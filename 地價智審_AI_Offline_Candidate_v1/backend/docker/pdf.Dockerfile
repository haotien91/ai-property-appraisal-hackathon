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

COPY backend/requirements-pdf.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt --target ${LAMBDA_TASK_ROOT}

# 共用引擎程式碼（與EngineLayer內容相同，Container Image路徑無法掛載zip Layer，
# 故此處改為直接COPY進image）
COPY domain ${LAMBDA_TASK_ROOT}/domain
COPY engine ${LAMBDA_TASK_ROOT}/engine
COPY pdf ${LAMBDA_TASK_ROOT}/pdf
COPY data ${LAMBDA_TASK_ROOT}/data
COPY schemas ${LAMBDA_TASK_ROOT}/schemas
COPY backend/handlers ${LAMBDA_TASK_ROOT}/

CMD ["pdf_handler.get_pdf"]
