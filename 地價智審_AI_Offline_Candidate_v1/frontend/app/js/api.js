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

    /** GET /api/cases/{id}/pdf */
    async getPdf(caseNo) {
      if (cfg.MODE === "mock") return getJson(mockUrl("pdf_result"));
      return getJson(prodUrl(`/api/cases/${encodeURIComponent(caseNo)}/pdf`));
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
  };

  global.Api = Api;
})(window);
