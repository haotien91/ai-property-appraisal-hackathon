/**
 * facility-review.js — FACILITY-REVIEW-UI-1.
 *
 * Renders the "設施證據人工確認" card (see pdf-preview.html) driving
 * Raw Evidence -> System Candidate -> PENDING -> 人工確認/不採用 ->
 * CONFIRMED -> 正式PDF (FACILITY-CONFIRMATION-GATE-1 /
 * FACILITY-STALE-CONFIRMATION-GATE-1). This file ONLY renders what the
 * backend candidate DTO already says and calls the existing confirm/
 * reject endpoints -- it never invents a status, never marks something
 * confirmed locally without a successful API response, and never claims
 * a CONFIRMED selection is government-verified data.
 *
 * 本輪只顯示 8 個已支援 subtype（utility x2 / funeral x4 / major_station
 * x2）；waste/market/park/pollution/bus_stop 不在此輪範圍。
 */
(function (global) {
  "use strict";

  var SUPPORTED_SUBTYPES = [
    "substation", "gas_tank", "cemetery", "funeral_home", "crematorium", "columbarium", "MRT", "TRA",
  ];

  var SUBTYPE_LABELS = {
    substation: "變電所", gas_tank: "瓦斯槽",
    cemetery: "墓地", funeral_home: "殯儀館", crematorium: "火葬場", columbarium: "納骨塔",
    MRT: "捷運站", TRA: "火車站",
  };

  var SUBTYPE_GROUP = {
    substation: "電業氣體燃料設施", gas_tank: "電業氣體燃料設施",
    cemetery: "殯葬設施", funeral_home: "殯葬設施", crematorium: "殯葬設施", columbarium: "殯葬設施",
    MRT: "大型車站", TRA: "大型車站",
  };

  var STATUS_LABELS = { PENDING: "待確認", CONFIRMED: "已確認", REJECTED: "不採用" };
  var STATUS_BADGE_CLASS = { PENDING: "badge-manual", CONFIRMED: "badge-passed", REJECTED: "badge-error" };

  // Task 9: provenance wording must be exact -- Mock must never read as
  // official/government data, and OSM/API must never be upgraded either.
  var SOURCE_TYPE_LABELS = {
    GovernmentOpenData: "政府開放資料",
    API: "OpenStreetMap／外部資料",
    Mock: "測試資料",
  };

  // Task 10.
  var SELECTION_BASIS_LABELS = {
    NEAREST_PROVIDER_RESULT: "系統依目前資料來源提供的最近候選",
    NEAREST_VALID_DISTANCE: "系統依有效距離選出最近候選",
  };

  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function errMessage(e) {
    return (e && e.error && e.error.message) || String(e);
  }

  function sourceTypeLabel(sourceType) {
    if (!sourceType) return "未知來源";
    return SOURCE_TYPE_LABELS[sourceType] || esc(sourceType);
  }

  function distanceText(candidate) {
    if (!candidate || candidate.distance_m === null || candidate.distance_m === undefined) return "未提供";
    return candidate.distance_m + " 公尺";
  }

  /** Task 3/9/10: candidate的完整事實表格，或「無候選」訊息 —— 絕不用
   * 假的預設值填補缺漏欄位。 */
  function candidateFactsHtml(candidate) {
    if (!candidate) {
      return '<p class="section-text mb-0" style="color:var(--ai-muted);">' +
        '<i class="fa-solid fa-circle-info me-1"></i>目前未取得可確認候選</p>';
    }
    var basisText = SELECTION_BASIS_LABELS[candidate.selection_basis] || candidate.selection_basis || "-";
    return '' +
      '<table class="app-table mb-2" style="font-size:.86rem;">' +
      '<tbody>' +
      '<tr><th style="width:100px;">名稱</th><td>' + (esc(candidate.name) || "（無名稱）") + '</td></tr>' +
      '<tr><th>距離</th><td>' + distanceText(candidate) + '</td></tr>' +
      '<tr><th>資料來源</th><td>' + (esc(candidate.source) || "-") + '</td></tr>' +
      '<tr><th>來源類型</th><td>' + sourceTypeLabel(candidate.source_type) + '</td></tr>' +
      '<tr><th>信心程度</th><td>' + (esc(candidate.confidence) || "-") + '</td></tr>' +
      '</tbody></table>' +
      '<p class="section-text mb-0" style="font-size:.78rem;">' +
      '<i class="fa-solid fa-robot me-1"></i>' + basisText + '<br>' +
      '<span style="color:var(--ai-muted);">系統建議不等同正式認定，須經人工確認。</span></p>';
  }

  function officialFormStatus(record) {
    // Task 12.
    if (record.status === "CONFIRMED" && !record.stale) {
      return { text: "已允許寫入正式 PDF", color: "#35c46a" };
    }
    if (record.status === "CONFIRMED" && record.stale) {
      return { text: "暫停寫入正式 PDF，等待重新確認", color: "#ffb020" };
    }
    return { text: "尚未寫入正式 PDF", color: "#a8a8b3" };
  }

  function actionButtonsHtml(subtype, mode) {
    // mode: "confirm_reject"（PENDING 有候選）或 "reconfirm_reject"（STALE）
    var confirmLabel = mode === "reconfirm_reject" ? "確認最新候選" : "確認採用";
    return '' +
      '<div class="d-flex flex-wrap gap-2 mt-3">' +
      '<button type="button" class="btn btn-ai-primary btn-sm facility-confirm-btn" data-subtype="' + esc(subtype) + '">' +
      '<i class="fa-solid fa-check me-1"></i>' + confirmLabel + '</button>' +
      '<button type="button" class="btn btn-ai-outline btn-sm facility-reject-btn" data-subtype="' + esc(subtype) + '">' +
      '<i class="fa-solid fa-xmark me-1"></i>不採用</button>' +
      '</div>' +
      '<div class="facility-action-log section-text mt-2" style="font-size:.8rem;"></div>';
  }

  /** Task 6/7/8/5: 依 status/stale/candidate 組出單一 subtype card 的 body。 */
  function cardBodyHtml(record) {
    var status = record.status;
    var stale = !!record.stale;
    var formStatus = officialFormStatus(record);
    var html = "";

    html += '<div class="d-flex align-items-center flex-wrap gap-2 mb-2">' +
      '<span class="' + STATUS_BADGE_CLASS[status] + '">' + STATUS_LABELS[status] + '</span>';
    if (status === "CONFIRMED" && stale) {
      html += '<span class="badge-error"><i class="fa-solid fa-triangle-exclamation me-1"></i>資料已更新，需重新確認</span>';
    }
    html += '</div>';

    html += '<p class="section-text mb-2" style="font-size:.82rem;">' +
      '<i class="fa-solid fa-file-signature me-1"></i>正式表單狀態：' +
      '<strong style="color:' + formStatus.color + ';">' + formStatus.text + '</strong></p>';

    if (status === "REJECTED") {
      html += '<p class="section-text">此候選已標記為不採用。</p>';
      if (record.reviewer_note) html += '<p class="section-text" style="font-size:.85rem;">備註：' + esc(record.reviewer_note) + '</p>';
      if (record.candidate) {
        html += '<details class="mt-2"><summary style="cursor:pointer;color:var(--ai-muted);font-size:.82rem;">查看原候選內容</summary>' +
          '<div class="mt-2">' + candidateFactsHtml(record.candidate) + '</div></details>';
      }
      html += '<p class="section-text mt-2" style="font-size:.76rem;color:var(--ai-muted);">' +
        '目前後端尚未提供「不採用 → 重新開啟審查」功能；如需重新審查請洽系統管理員（BACKLOG）。</p>';
      return html;
    }

    if (status === "CONFIRMED" && !stale) {
      html += candidateFactsHtml(record.confirmed_selection);
      html += '<p class="section-text mt-2" style="font-size:.82rem;color:#35c46a;">' +
        '<i class="fa-solid fa-circle-check me-1"></i>此資料將寫入正式查估書表。</p>';
      html += confirmedByLine(record);
      html += '<p class="section-text mt-1" style="font-size:.74rem;color:var(--ai-muted);">' +
        '「已確認」表示承辦人採用此候選，並非官方政府資料驗證結果。</p>';
      return html;
    }

    if (status === "CONFIRMED" && stale) {
      html += '<div class="comparison-row">' +
        '<div class="comparison-box"><small>先前確認</small>' + candidateFactsHtml(record.confirmed_selection) + '</div>' +
        '<div class="comparison-box"><small>最新候選</small>' + candidateFactsHtml(record.candidate) + '</div>' +
        '</div>';
      html += '<p class="section-text" style="font-size:.82rem;color:#ffb020;">' +
        '<i class="fa-solid fa-triangle-exclamation me-1"></i>重新確認前，正式 PDF 將保持空白。</p>';
      html += confirmedByLine(record);
      html += actionButtonsHtml(record.subtype, "reconfirm_reject");
      return html;
    }

    // PENDING
    html += candidateFactsHtml(record.candidate);
    if (record.candidate) html += actionButtonsHtml(record.subtype, "confirm_reject");
    return html;
  }

  function confirmedByLine(record) {
    if (!record.confirmed_by) return "";
    return '<p class="section-text mt-1" style="font-size:.78rem;color:var(--ai-muted);">確認人：' + esc(record.confirmed_by) + '</p>';
  }

  function subtypeCardHtml(record) {
    return '' +
      '<div class="col-md-6 col-lg-4">' +
      '<div class="app-card h-100 mb-0" data-facility-card data-subtype="' + esc(record.subtype) + '">' +
      '<p class="section-text mb-1" style="font-size:.78rem;color:var(--ai-muted);">' + esc(SUBTYPE_GROUP[record.subtype]) + '</p>' +
      '<h4 style="font-size:1.1rem;color:#fff;">' + esc(SUBTYPE_LABELS[record.subtype]) + '</h4>' +
      cardBodyHtml(record) +
      '</div></div>';
  }

  function summaryStripHtml(records) {
    var counts = { PENDING: 0, CONFIRMED_ACTIVE: 0, CONFIRMED_STALE: 0, REJECTED: 0 };
    records.forEach(function (r) {
      if (r.status === "CONFIRMED" && r.stale) counts.CONFIRMED_STALE++;
      else if (r.status === "CONFIRMED") counts.CONFIRMED_ACTIVE++;
      else if (r.status === "REJECTED") counts.REJECTED++;
      else counts.PENDING++;
    });
    function cell(cls, count, label) {
      return '<div class="col-6 col-md-3"><div class="result-card ' + cls + '">' +
        '<span class="result-count">' + count + '</span><span class="result-label">' + label + '</span></div></div>';
    }
    return cell("passed", counts.CONFIRMED_ACTIVE, "已確認（將寫入PDF）") +
      cell("warning", counts.CONFIRMED_STALE, "需重新確認") +
      cell("missing", counts.PENDING, "待確認") +
      cell("error", counts.REJECTED, "不採用");
  }

  var FacilityReview = {
    _caseNo: null,

    async init(caseNo) {
      this._caseNo = caseNo;
      if (!caseNo) {
        this._showError("缺少案號（case_no），請從案件列表或建立案件重新進入。");
        return;
      }
      await this.reload();
    },

    async reload() {
      var loadingEl = document.getElementById("facilityLoadingState");
      var errorEl = document.getElementById("facilityErrorState");
      var contentEl = document.getElementById("facilityContent");
      loadingEl.style.display = "flex";
      errorEl.style.display = "none";
      contentEl.style.display = "none";
      try {
        var data = await Api.getFacilityCandidates(this._caseNo);
        var records = (data.candidates || []).filter(function (c) {
          return SUPPORTED_SUBTYPES.indexOf(c.subtype) !== -1;
        });
        // 依 SUPPORTED_SUBTYPES 固定順序顯示，不依 API 回傳順序（避免每次刷新排序跳動）。
        records.sort(function (a, b) {
          return SUPPORTED_SUBTYPES.indexOf(a.subtype) - SUPPORTED_SUBTYPES.indexOf(b.subtype);
        });
        document.getElementById("facilitySummaryStrip").innerHTML = summaryStripHtml(records);
        document.getElementById("facilityCardsContainer").innerHTML = records.map(subtypeCardHtml).join("");
        this._bindActions();
        loadingEl.style.display = "none";
        contentEl.style.display = "block";
      } catch (e) {
        loadingEl.style.display = "none";
        var code = (e && e.error && e.error.code) || "";
        var message;
        if (code === "FACTORS_NOT_FOUND" || code === "CASE_NOT_FOUND") {
          message = "找不到本案件的資料收集結果（FACTORS），請先完成「資料收集」步驟後再回到本頁。";
        } else if (code === "MOCK_MODE_NOT_SUPPORTED") {
          message = errMessage(e);
        } else {
          message = "設施證據資料載入失敗：" + errMessage(e) + "（可能是網路連線問題，請確認後端服務是否可連線）";
        }
        this._showError(message);
      }
    },

    _showError(message) {
      var errorEl = document.getElementById("facilityErrorState");
      errorEl.textContent = message;
      errorEl.style.display = "block";
      document.getElementById("facilityLoadingState").style.display = "none";
      document.getElementById("facilityContent").style.display = "none";
    },

    _bindActions() {
      var self = this;
      document.querySelectorAll(".facility-confirm-btn").forEach(function (btn) {
        btn.addEventListener("click", function () { self._doConfirm(btn); });
      });
      document.querySelectorAll(".facility-reject-btn").forEach(function (btn) {
        btn.addEventListener("click", function () { self._doReject(btn); });
      });
    },

    async _doConfirm(btn) {
      var subtype = btn.getAttribute("data-subtype");
      var logEl = btn.closest("[data-facility-card]").querySelector(".facility-action-log");
      var buttons = btn.closest("[data-facility-card]").querySelectorAll("button");
      buttons.forEach(function (b) { b.disabled = true; });
      logEl.innerHTML = '<span style="color:var(--ai-muted);"><span class="spinner-border spinner-border-sm me-1"></span>送出確認中...</span>';
      try {
        await Api.confirmFacilityCandidate(this._caseNo, subtype, "frontend-demo-user");
        // Task 5: 不得前端自己改 local status 假裝成功 -- 一律以 API 回應
        // 後重新整批 GET 的結果為準。
        await this.reload();
      } catch (e) {
        buttons.forEach(function (b) { b.disabled = false; });
        logEl.innerHTML = '<span style="color:#ff4268;"><i class="fa-solid fa-circle-exclamation me-1"></i>確認失敗：' + esc(errMessage(e)) + '</span>';
      }
    },

    async _doReject(btn) {
      var subtype = btn.getAttribute("data-subtype");
      var logEl = btn.closest("[data-facility-card]").querySelector(".facility-action-log");
      var buttons = btn.closest("[data-facility-card]").querySelectorAll("button");
      buttons.forEach(function (b) { b.disabled = true; });
      logEl.innerHTML = '<span style="color:var(--ai-muted);"><span class="spinner-border spinner-border-sm me-1"></span>送出不採用中...</span>';
      try {
        await Api.rejectFacilityCandidate(this._caseNo, subtype, "frontend-demo-user", null);
        await this.reload();
      } catch (e) {
        buttons.forEach(function (b) { b.disabled = false; });
        logEl.innerHTML = '<span style="color:#ff4268;"><i class="fa-solid fa-circle-exclamation me-1"></i>不採用送出失敗：' + esc(errMessage(e)) + '</span>';
      }
    },
  };

  global.FacilityReview = FacilityReview;
})(window);
