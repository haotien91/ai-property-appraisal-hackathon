/**
 * api.js — 統一 API 呼叫層，所有頁面透過此檔案存取後端資料，
 * 不直接使用 fetch() 呼叫 Hardcode 的 URL。
 *
 * Mock Mode：讀取 frontend/mock/*.json 靜態檔案（無需後端）。
 * Production Mode：呼叫 window.APP_CONFIG.API_BASE_URL 下的真實端點，
 * Request/Response Schema 依 docs/phase4/frontend_api_contract.md。
 *
 * 兩種模式回傳之 Promise resolve 值 Schema 完全一致（Phase 5已確保
 * frontend/mock/*.json 直接由真實Pydantic模型序列化產生，見
 * docs/phase5/... 與 frontend/mock/README內之產生方式說明），
 * 因此頁面渲染邏輯不需因應模式切換而改寫。
 */
(function (global) {
  "use strict";

  const cfg = global.APP_CONFIG || { MODE: "mock", MOCK_BASE_PATH: "frontend/mock" };

  function mockUrl(name) {
    return `${cfg.MOCK_BASE_PATH}/${name}.json`;
  }

  function prodUrl(path) {
    return `${cfg.API_BASE_URL}${path}`;
  }

  /** 統一錯誤物件，符合 docs/phase4/frontend_api_contract.md 之 Error Schema。 */
  function apiError(code, message, fieldId) {
    return { error: { code, message, field_id: fieldId || null, details: {} } };
  }

  async function getJson(url) {
    const res = await fetch(url);
    if (!res.ok) {
      // FACILITY-REVIEW-UI-1: prefer the backend's OWN error_response()
      // body (error.code/message, e.g. "FACTORS_NOT_FOUND") when present
      // -- purely additive, existing callers that only read a generic
      // CASE_NOT_FOUND/INTERNAL_ERROR fallback are unaffected when the
      // response body isn't JSON or carries no error.code.
      let backendError = null;
      try { backendError = await res.json(); } catch (e) { /* not JSON, fall through to generic */ }
      if (backendError && backendError.error && backendError.error.code) throw backendError;
      throw apiError(
        res.status === 404 ? "CASE_NOT_FOUND" : "INTERNAL_ERROR",
        `請求失敗：${url} (HTTP ${res.status})`
      );
    }
    return res.json();
  }

  const Api = {
    mode: cfg.MODE,

    /** GET /api/cases */
    async listCases() {
      if (cfg.MODE === "mock") return getJson(mockUrl("case"));
      return getJson(prodUrl("/api/cases"));
    },

    /** GET /api/cases/{id} */
    async getCase(caseNo) {
      if (cfg.MODE === "mock") {
        const list = await getJson(mockUrl("case"));
        const found = list.cases.find((c) => c.case_no === caseNo);
        if (!found) throw apiError("CASE_NOT_FOUND", `找不到案件 ${caseNo}`);
        return found;
      }
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}`));
    },

    /** POST /api/cases (Production only; Mock Mode 僅回傳既有Mock資料，不建立新案件) */
    async createCase(payload) {
      if (cfg.MODE === "mock") {
        console.warn("[api.js] Mock Mode 不支援建立新案件，回傳既有Golden Case示範資料。");
        return getJson(mockUrl("case")).then((d) => d.cases[0]);
      }
      const res = await fetch(prodUrl("/api/cases"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw apiError("VALIDATION_ERROR", "建立案件失敗");
      return res.json();
    },

    /** POST /api/cases/{id}/collect-data */
    async collectData(caseNo, payload) {
      if (cfg.MODE === "mock") return getJson(mockUrl("data_collection"));
      const res = await fetch(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/collect-data`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw apiError("VALIDATION_ERROR", "資料收集失敗");
      return res.json();
    },

    /** POST /api/cases/{id}/analyze */
    async analyze(caseNo) {
      if (cfg.MODE === "mock") return getJson(mockUrl("analysis"));
      const res = await fetch(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/analyze`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) throw apiError("INTERNAL_ERROR", "分析失敗");
      return res.json();
    },

    /** POST /api/cases/{id}/complete-form */
    async completeForm(caseNo) {
      if (cfg.MODE === "mock") return getJson(mockUrl("form_completion"));
      const res = await fetch(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/complete-form`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) throw apiError("VALIDATION_ERROR", "書表填寫失敗");
      return res.json();
    },

    /** GET /api/cases/{id}/pdf -- raw backend response, carries BOTH the
     * Official PDF (official_pdf_url/official_pdf_status) and the Audit
     * PDF(s) (audit_pdf_url/pdf_urls) in one payload (see backend/handlers/
     * pdf_handler.py). Prefer getOfficialPdf()/getAuditPdf() below for any
     * new caller -- kept here unchanged for backward compatibility. */
    async getPdf(caseNo) {
      if (cfg.MODE === "mock") return getJson(mockUrl("pdf_result"));
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/pdf`));
    },

    /**
     * FRONTEND-OFFICIAL-PDF-WIRING-1 Task 10: explicit, non-ambiguous
     * accessor for ONLY the Official（正式版型查估書表）PDF -- overlaid onto
     * the real 查估書表範本.pdf by pdf/official_pdf_renderer.py, gated to
     * ACTIVE_CONFIRMED_SELECTION facility rows only (FACILITY-CONFIRMATION-
     * GATE-1 / FACILITY-STALE-CONFIRMATION-GATE-1). Never returns an Audit
     * PDF under a different name -- `pdf_url` is null when generation
     * failed (see `status`), the caller must NOT substitute audit_pdf_url
     * in that case (Task 15).
     */
    async getOfficialPdf(caseNo) {
      const d = await Api.getPdf(caseNo);
      return {
        pdf_url: d.official_pdf_url || null,
        status: d.official_pdf_status || (d.official_pdf_url ? "READY" : "OFFICIAL_PDF_GENERATION_FAILED"),
        title: (d.forms && d.forms.official_form && d.forms.official_form.title) || "官方查估書表格式（表1+表5-2+表4）",
        generated_at: d.generated_at,
      };
    },

    /**
     * Task 10: explicit accessor for ONLY the Audit／系統審查報告 PDF(s)
     * (Fallback Reproduction -- 表1 + 表4+表5-2，含 Rule/Formula/Source/
     * provenance 追溯資訊). Unaffected by whether the Official PDF above
     * succeeded or failed.
     */
    async getAuditPdf(caseNo) {
      const d = await Api.getPdf(caseNo);
      return {
        pdf_url: d.audit_pdf_url || d.pdf_url || null,
        pdf_urls: d.pdf_urls || [],
        forms_included: d.forms_included || [],
        page_count: d.page_count || {},
        fallback_mode: d.fallback_mode,
        fallback_reason: d.fallback_reason,
        generated_at: d.generated_at,
      };
    },

    /**
     * FRONTEND-UNIFIED-EXPORT-WIRING-F2 Task 4: GET /api/cases/{id}/export/json
     * -- backend_handlers/export_handler.py::get_export_json() returns the
     * CaseExportBundle JSON INLINE (not a presigned URL) since it is small
     * enough for a direct response; this method returns the parsed object
     * plus the raw text (so callers can offer a byte-identical download
     * without re-serializing anything client-side -- Task 12: frontend
     * never recomputes/reshapes backend output).
     */
    async getExportJson(caseNo) {
      if (cfg.MODE === "mock") {
        // frontend/mock/export_json_result.json is a REAL CaseExportBundle
        // JSON (produced by the exact same export/bundle_builder.py +
        // json_exporter.py the real backend calls, just precomputed to a
        // static file -- same established pattern as pdf_result.json).
        const res = await fetch(mockUrl("export_json_result"));
        const text = await res.text();
        return { text, data: JSON.parse(text) };
      }
      const url = prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/export/json`);
      const res = await fetch(url);
      const text = await res.text();
      if (!res.ok) {
        let backendError = null;
        try { backendError = JSON.parse(text); } catch (e) { /* not JSON */ }
        if (backendError && backendError.error && backendError.error.code) throw backendError;
        throw apiError(res.status === 404 ? "CASE_NOT_FOUND" : "INTERNAL_ERROR", `JSON 匯出失敗 (HTTP ${res.status})`);
      }
      return { text, data: JSON.parse(text) };
    },

    /**
     * Task 5: GET /api/cases/{id}/export/excel -- returns a presigned S3 URL
     * (excel_zip_url) to a zip of the 6 Excel files (export_handler.py::
     * get_export_excel(), never rebuilt/unzipped client-side).
     */
    async getExportExcel(caseNo) {
      if (cfg.MODE === "mock") {
        return getJson(mockUrl("export_excel_result"));
      }
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/export/excel`));
    },

    /**
     * Task 6: GET /api/cases/{id}/export/bundle -- returns a presigned S3 URL
     * (bundle_zip_url) to the complete ZIP (JSON + 6 Excel + Official PDF,
     * export_handler.py::get_export_bundle(), reusing E1's renderer as-is).
     */
    async getExportBundle(caseNo) {
      if (cfg.MODE === "mock") {
        return getJson(mockUrl("export_bundle_result"));
      }
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/export/bundle`));
    },

    /**
     * POST /api/cases/{id}/review
     * options（皆可選，省略時＝既有行為不變，submission_source預設
     * FORM_COMPLETION）：
     *   { submission_source: "FORM_COMPLETION" | "DOCUMENT", document_id }
     */
    async review(caseNo, options) {
      if (cfg.MODE === "mock") return getJson(mockUrl("review_result"));
      const res = await fetch(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/review`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options || {}),
      });
      if (!res.ok) throw apiError("INTERNAL_ERROR", "Smart Review失敗");
      return res.json();
    },

    /** GET /api/cases/{id}/result */
    async getResult(caseNo) {
      if (cfg.MODE === "mock") return getJson(mockUrl("result"));
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/result`));
    },

    /**
     * POST /api/cases/{id}/documents — 建立document_id並取得presigned
     * upload URL。呼叫端須自行以 PUT 將PDF bytes直接送至upload_url
     * （本方法不搬運檔案內容，Lambda/API Gateway從不經手PDF本體）。
     */
    async requestDocumentUpload(caseNo) {
      if (cfg.MODE === "mock") {
        // LOCAL/MOCK：回傳固定示範 document_id，並未真的建立 S3 Presigned
        // URL（upload_url 為 null）——呼叫端不得對此值執行真正的 PUT。
        console.warn("[api.js] Mock Mode：requestDocumentUpload 回傳 LOCAL/MOCK 示範資料，未呼叫真實 AWS。");
        return getJson(mockUrl("document_upload"));
      }
      const res = await fetch(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/documents`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) throw apiError("VALIDATION_ERROR", "建立上傳網址失敗");
      return res.json();
    },

    /** 使用requestDocumentUpload()回傳之upload_url，直接PUT檔案至S3。
     * LOCAL/MOCK：uploadUrl 恆為 null（見 requestDocumentUpload 之Mock分支），
     * 此處明確跳過、不假裝上傳成功——絕不對 null URL 發出fetch請求偽裝S3已收檔。 */
    async putDocumentFile(uploadUrl, file) {
      if (cfg.MODE === "mock" || !uploadUrl) {
        console.warn("[api.js] Mock Mode 或無 upload_url：跳過真實檔案上傳（未呼叫S3）。");
        return true;
      }
      const res = await fetch(uploadUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/pdf" },
        body: file,
      });
      if (!res.ok) throw apiError("VALIDATION_ERROR", "文件上傳失敗");
      return true;
    },

    /** POST /api/cases/{id}/documents/{document_id}/extract */
    async extractDocument(caseNo, documentId) {
      if (cfg.MODE === "mock") {
        // LOCAL/MOCK：不呼叫任何Lambda，直接回傳已由LocalExtractionProvider
        // 實際對Golden PDF執行擷取後序列化之固定結果摘要（見
        // scripts/build_document_extraction_mock.py），非憑空杜撰之數字。
        console.warn("[api.js] Mock Mode：extractDocument 回傳 LOCAL/MOCK 既有擷取結果，未呼叫真實 Lambda。");
        const d = await getJson(mockUrl("document_extraction"));
        return {
          case_no: caseNo, document_id: documentId, status: d.status,
          field_count: d.field_count, requires_confirmation_count: d.requires_confirmation_count,
          created_at: d.created_at, extractor: d.extractor, extractor_version: d.extractor_version,
          local_mock: true, local_mock_notice: d.local_mock_notice,
        };
      }
      const res = await fetch(
        prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/documents/${encodeURIComponent(documentId)}/extract`),
        { method: "POST", headers: { "Content-Type": "application/json" } }
      );
      if (!res.ok) throw apiError("INTERNAL_ERROR", "文件擷取失敗");
      return res.json();
    },

    /** GET /api/cases/{id}/documents/{document_id}/extraction */
    async getDocumentExtraction(caseNo, documentId) {
      if (cfg.MODE === "mock") {
        console.warn("[api.js] Mock Mode：getDocumentExtraction 回傳 LOCAL/MOCK 既有擷取結果，未呼叫真實 API。");
        const d = await getJson(mockUrl("document_extraction"));
        return { ...d, case_no: caseNo, document_id: documentId };
      }
      return getJson(
        prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/documents/${encodeURIComponent(documentId)}/extraction`)
      );
    },

    /**
     * POST /api/cases/{id}/documents/{document_id}/confirm
     * confirmations: [{field_id, extraction_role, extraction_subject_role,
     *                  comparable_slot, confirmed_value, confirmed_by}]
     * 須帶完整composite identity（非field_id單一鍵），呼應
     * engine/human_confirmation.py之既有設計。
     */
    async confirmDocument(caseNo, documentId, confirmations) {
      if (cfg.MODE === "mock") {
        // LOCAL/MOCK：僅在瀏覽器端回顯使用者實際送出之確認筆數，
        // 不會真的寫入任何後端儲存（未呼叫真實 AWS Lambda/S3）。
        console.warn("[api.js] Mock Mode：confirmDocument 為 LOCAL/MOCK 回顯，未實際寫入後端。");
        const base = await getJson(mockUrl("document_confirm"));
        return {
          ...base, case_no: caseNo, document_id: documentId,
          confirmed_count: (confirmations || []).length, skipped_count: 0, skipped: [],
        };
      }
      const res = await fetch(
        prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/documents/${encodeURIComponent(documentId)}/confirm`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmations: confirmations || [] }),
        }
      );
      if (!res.ok) throw apiError("VALIDATION_ERROR", "人工確認送出失敗");
      return res.json();
    },

    /**
     * GET /api/cases/{id}/facility-candidates
     * FACILITY-CONFIRMATION-GATE-1／FACILITY-REVIEW-UI-1：utility(2)/
     * funeral(4)/major_station(2) 共8個 subtype 之 candidate + 確認狀態。
     * 呼叫本方法會在 Backend 端刷新 candidate 快照（不影響 status/
     * confirmed_selection，那兩者只由 confirm/reject 變更 —— 見
     * facility_confirmation_repository.py）。
     */
    async getFacilityCandidates(caseNo) {
      if (cfg.MODE === "mock") {
        // LOCAL/MOCK：本檔案由 scripts/build_facility_candidates_mock.py
        // 對真實 MockSpecialFacilityProvider 資料實際執行 confirm()/
        // reject()/get_or_refresh_candidates() 產生（含一筆真實
        // stale=True 記錄），非手工杜撰之示範數字。
        console.warn("[api.js] Mock Mode：getFacilityCandidates 回傳 LOCAL/MOCK 既有確認狀態快照，未呼叫真實 API。");
        return getJson(mockUrl("facility_candidates"));
      }
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/facility-candidates`));
    },

    /**
     * POST /api/cases/{id}/facility-candidates/{subtype}/confirm
     * Mock Mode 不支援真的改變後端狀態（沒有可寫入的後端）——回傳明確
     * 錯誤而非假裝成功，呼叫端必須誠實顯示「Mock Mode 無法確認」。
     */
    async confirmFacilityCandidate(caseNo, subtype, confirmedBy) {
      if (cfg.MODE === "mock") {
        throw apiError("MOCK_MODE_NOT_SUPPORTED", "LOCAL/MOCK 展示模式無法送出真實確認（沒有可寫入的後端）。請切換至 Production 模式並連接真實 API。");
      }
      const res = await fetch(
        prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/facility-candidates/${encodeURIComponent(subtype)}/confirm`),
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed_by: confirmedBy }) }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw apiError((body && body.error && body.error.code) || "VALIDATION_ERROR", (body && body.error && body.error.message) || "確認失敗");
      }
      return res.json();
    },

    /** POST /api/cases/{id}/facility-candidates/{subtype}/reject */
    async rejectFacilityCandidate(caseNo, subtype, rejectedBy, reviewerNote) {
      if (cfg.MODE === "mock") {
        throw apiError("MOCK_MODE_NOT_SUPPORTED", "LOCAL/MOCK 展示模式無法送出真實不採用決定（沒有可寫入的後端）。請切換至 Production 模式並連接真實 API。");
      }
      const res = await fetch(
        prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/facility-candidates/${encodeURIComponent(subtype)}/reject`),
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rejected_by: rejectedBy, reviewer_note: reviewerNote || undefined }) }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw apiError((body && body.error && body.error.code) || "VALIDATION_ERROR", (body && body.error && body.error.message) || "不採用送出失敗");
      }
      return res.json();
    },
  };

  global.Api = Api;

  /**
   * FRONTEND-UNIFIED-EXPORT-WIRING-F2 Task 11: minimal shared download
   * helper -- JSON export returns its content inline (no S3 URL), so a
   * real client-side Blob download is the only way to save it; Excel/
   * Bundle already have a presigned URL and just need a plain
   * <a download> (see result.html), so this helper is JSON-only on
   * purpose (kept small, not a generic "download anything" abstraction).
   */
  function downloadTextAsFile(text, filename, mimeType) {
    const blob = new Blob([text], { type: mimeType || "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  global.downloadTextAsFile = downloadTextAsFile;
})(window);
