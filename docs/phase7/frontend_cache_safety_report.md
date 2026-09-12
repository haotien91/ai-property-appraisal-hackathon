# FRONTEND-CACHE-SAFETY-1 — 靜態資源 Cache-Busting

## 背景

Chrome 實測：更新 `pdf-preview.html`／`api.js` 後首次一般整理仍顯示舊版
Official PDF failure UI，Ctrl+F5（強制略過快取）後才恢復正常——典型瀏覽器
靜態資源快取問題。本輪只做最小改善，不動 backend／Engine。

## TASK 1 — 現況稽核

檢查全部 16 個 `frontend/app/*.html`，`js/config.js`／`js/api.js`／
`js/case-context.js`／`js/facility-review.js`／`css/style.css` 的引用**完全
沒有**任何 version／cache-busting query string（純 `src="js/api.js"`）。

```
CACHE_BUSTING_BEFORE=NONE（16頁全數無 ?v= 或任何快取破壞機制）
```

`js/config.js` 內雖已有 `APP_VERSION: "phase7-1.0"` 欄位，但這是執行期 JS
資料值、目前無任何地方讀取使用，且無法用來為「config.js 自己」的
`<script src>` 加上 query string（先有雞或先有蛋問題）——不構成現成可用的
cache-busting 機制。

## TASK 2/3 — 最小方案

固定版本字串 `20260912-1`（非隨機 timestamp，每次都相同，直到下次刻意更新版號）。
以腳本對 16 個 HTML 檔案做單純字串取代（僅本專案自有、實際被本輪及先前
FACILITY-REVIEW-UI-1／FRONTEND-OFFICIAL-PDF-WIRING-1 兩輪修改過、且會影響
Demo 的檔案；未動 jQuery/Bootstrap/animate/owlcarousel/lightbox/wow/easing/
waypoints/main.js 等未曾修改過的 vendor 檔案）：

```
css/style.css        -> css/style.css?v=20260912-1     （16頁全部）
js/config.js         -> js/config.js?v=20260912-1       （8頁：有載入的頁面）
js/api.js            -> js/api.js?v=20260912-1          （同上8頁）
js/case-context.js   -> js/case-context.js?v=20260912-1 （同上8頁）
js/facility-review.js-> js/facility-review.js?v=20260912-1（僅 pdf-preview.html）
```

未使用 `Date.now()`／隨機亂數，未關閉瀏覽器任何既有快取行為（僅追加 query
string，HTTP 快取政策本身完全不變）。

```
CACHE_BUSTING_AFTER=query-string?v=20260912-1（固定版本，同一版本橫跨全部16頁）
```

## TASK 3 — 一致性驗證

`grep -c "v=20260912-1"` 逐頁計數：純 CSS 頁（404/about/contact/index/
service/team/testimonial）各1、有載入 config/api/case-context 的8頁各4、
pdf-preview.html 5（含 facility-review.js）——共 44 處，**只有一個版本字串
`20260912-1`**，無任何頁面殘留舊版或不同版本。重覆執行同一取代腳本結果為
「0 files changed」，證明具冪等性、不會重覆疊加版本參數。

```
SHARED_API_VERSION_CONSISTENT=YES
```

## TASK 4 — 「一般整理」（非 Ctrl+F5）驗證

以 jsdom 對**正式** `frontend/app/pdf-preview.html`（帶新版 `?v=` 的真實檔案，
非簡化測試頁）重新整頁載入（模擬一般 refresh，非強制清快取）：

```
node cache_safety_check.js
PASS  Versioned js/api.js still loads and attaches window.Api
PASS  Versioned js/facility-review.js still loads and attaches window.FacilityReview
PASS  Versioned js/case-context.js still loads and attaches window.CaseContext
PASS  Facility Review UI renders after normal load (8 subtype cards)
PASS  Official PDF UI renders after normal load (download link present)
PASS  Audit PDF UI renders after normal load (2 download links present)
=== RESULT: 6 PASS / 0 FAIL ===
```

```
NORMAL_REFRESH_LOADS_CURRENT_OFFICIAL_UI=YES
NORMAL_REFRESH_LOADS_CURRENT_FACILITY_UI=YES
```

（無瀏覽器自動化工具可用，同前兩輪之誠實聲明；上述為 jsdom 對真實檔案的
執行期驗證，非僅靜態檢視原始碼。）

## TASK 5 — JS 語法檢查

本輪僅修改 16 個 `.html` 檔案的 `<script src>`／`<link href>` 屬性字串，
**未修改任何 `.js` 檔案內容**。仍對全部相關 JS 檔案執行 `node --check` 作為
防禦性確認：

```
node --check js/config.js         => OK
node --check js/api.js            => OK
node --check js/case-context.js   => OK
node --check js/facility-review.js=> OK
```

未新增任何 framework 或 build system（沿用純字串取代，無 webpack/vite/npm
script 依賴）。

## Regression

```
python -m pytest tests -q --ignore=tests/test_phase5_golden_pipeline.py
  => 3 failed, 1088 passed, 3 skipped
  （與本輪修改前基準完全相同 -- 3項失敗仍是既有 Windows WeasyPrint/
    libgobject 原生函式庫問題，與本輪純前端HTML屬性修改無關）
NEW_FAILURE_COUNT=0
```

## 未做（刻意排除，避免 overbuild）

- 未導入 Service Worker／manifest 版本控制。
- 未對 vendor/CDN 檔案（bootstrap/jquery/animate/owlcarousel/lightbox/wow/
  easing/waypoints/main.js）加上版本參數（未曾被本專案修改，非本輪範圍）。
- 未關閉或修改任何 HTTP Cache-Control 標頭／伺服器設定。
- 未使用亂數或每次 reload 都變動的 timestamp。
