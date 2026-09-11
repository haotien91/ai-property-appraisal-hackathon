/**
 * case-context.js — 單一 case_no / document_id 存取慣例，供所有頁面共用。
 * 依 Final Frontend Integration Phase G-1/G-2：不得每頁各自 hardcode，
 * 也不得讓 Production Mode 因遺失 case_no 而打出 /api/cases/undefined/...。
 *
 * case_no：query string (?case=) 優先，其次 sessionStorage（同分頁跨頁保留），
 * 最後 fallback 到 APP_CONFIG.DEFAULT_CASE_NO（單一案件 Demo 情境）。
 * 每次讀到 query string 的 case，立即寫回 sessionStorage，
 * 確保後續頁面即使連結忘記帶 ?case=，仍能從 sessionStorage 復原。
 *
 * document_id：僅用 sessionStorage（不放 query string）。document_id 是
 * UUID，使用者不會手動輸入/分享網址帶著它；且必須與目前操作中的 case_no
 * 綁定，避免瀏覽器同時開啟兩個不同案件分頁時互相污染（見 Phase G-2 稽核
 * 結論：不得使用 global hardcoded document id，也不得讓 backend 猜最新文件）。
 */
(function (global) {
  "use strict";

  var CASE_KEY = "currentCaseNo";
  var DOC_PREFIX = "currentDocumentId:"; // + case_no，document_id 與 case_no 綁定儲存

  function currentCaseNo() {
    var fromQuery = new URLSearchParams(location.search).get("case");
    if (fromQuery) {
      try { sessionStorage.setItem(CASE_KEY, fromQuery); } catch (e) { /* storage unavailable, still usable this page */ }
      return fromQuery;
    }
    try {
      var stored = sessionStorage.getItem(CASE_KEY);
      if (stored) return stored;
    } catch (e) { /* ignore */ }
    return ((global.APP_CONFIG || {}).DEFAULT_CASE_NO) || null;
  }

  function setCurrentCaseNo(caseNo) {
    try { sessionStorage.setItem(CASE_KEY, caseNo); } catch (e) { /* ignore */ }
  }

  function withCaseQuery(href, caseNo) {
    var c = caseNo || currentCaseNo();
    if (!c) return href;
    var sep = href.indexOf("?") === -1 ? "?" : "&";
    return href + sep + "case=" + encodeURIComponent(c);
  }

  function currentDocumentId(caseNo) {
    var c = caseNo || currentCaseNo();
    if (!c) return null;
    try { return sessionStorage.getItem(DOC_PREFIX + c); } catch (e) { return null; }
  }

  function setCurrentDocumentId(documentId, caseNo) {
    var c = caseNo || currentCaseNo();
    if (!c) return;
    try { sessionStorage.setItem(DOC_PREFIX + c, documentId); } catch (e) { /* ignore */ }
  }

  /** 套用 withCaseQuery() 到頁面上所有帶 data-preserve-case 屬性的連結，
   * 讓 app-stepper／CTA 連結不必逐一在 inline script 內手動組字串。 */
  function applyCaseQueryToLinks(root) {
    var scope = root || document;
    var caseNo = currentCaseNo();
    if (!caseNo) return;
    scope.querySelectorAll("a[data-preserve-case]").forEach(function (a) {
      a.setAttribute("href", withCaseQuery(a.getAttribute("href"), caseNo));
    });
  }

  global.CaseContext = {
    currentCaseNo: currentCaseNo,
    setCurrentCaseNo: setCurrentCaseNo,
    withCaseQuery: withCaseQuery,
    currentDocumentId: currentDocumentId,
    setCurrentDocumentId: setCurrentDocumentId,
    applyCaseQueryToLinks: applyCaseQueryToLinks,
  };
})(window);
