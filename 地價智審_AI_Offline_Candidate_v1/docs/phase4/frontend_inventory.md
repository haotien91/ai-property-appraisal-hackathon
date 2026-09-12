# Phase 4 — Frontend Inventory（Makerthon_test_1 完整盤點）

> 依主專案指示第18-19節（Read Existing Frontend First / Do Not Rebuild），本文件
> 為 Makerthon_test_1.zip 之完整、逐檔案盤點。**本階段僅盤點與分類，未修改任何
> 檔案**。壓縮包內實際資料夾名稱為
> `Makerthon_test_1/地價智審 AI/`（含中文字元，Zip內部以UTF-8編碼儲存）。

## 分類定義

- **KEEP**：現況良好，直接沿用，無需改動。
- **MODIFY**：結構/樣式可沿用，但內容需更新（例如將模特兒經紀公司文案改為地政
  審查系統文案）。
- **REUSE**：可作為其他頁面之樣板／參考基礎（結構或CSS class可複製到新頁面）。
- **ADD**：目前缺少，需於後續Phase新增。
- **REMOVE_LATER**：與本專案業務無關或非優先項目，暫不刪除（避免破壞既有結構），
  標記供未來視需要移除或忽略。

---

## A. HTML 頁面（共7個，實際檔案為準）

| 檔案 | 現況 | 分類 | 說明 |
|---|---|---|---|
| `index.html` | `<title>地價智審 AI｜AI 輔助不動產估價案件審查</title>`；已客製化，含511行/13KB內嵌`<style>`深色主題+粉紅/珊瑚色系配色（`#09090b`~`#18181d`背景，`#ff0a78`/`#ff4268`/`#ff6a87`強調色，`#35c46a`綠/`#ffb020`黃疑似對應Passed/Warning狀態色）；含首頁/系統介紹/核心功能/審查流程/審查結果5個錨點區塊，內容為說明性文案（無實際表單） | **MODIFY** | Landing Page，符合主專案指示第20節定位；後續Phase需將導覽列錨點連結擴充為指向真實App Pages（case.html等），而非僅頁內錨點 |
| `about.html` | `<title>Poseify - Modeling Agency Website Template</title>`；**未客製化**，內容為模特兒經紀公司「關於我們」+8位模特兒團隊照片區塊 | **MODIFY** | 結構（標題區+內文+團隊照片Grid）可沿用為「系統介紹」或「開發團隊介紹」頁，但全部文案與圖片需替換 |
| `service.html` | 同上（未客製化），內容為模特兒經紀「服務項目」4項+客戶推薦3則 | **MODIFY** | 結構可沿用為「核心功能」頁（Core A/Core B介紹），文案需全部替換 |
| `team.html` | 同上（未客製化），內容為8位模特兒團隊卡片 | **REMOVE_LATER** | 與本專案業務關聯度低（B2G內部審查工具不需要模特兒式團隊展示），暫不處理，優先度低 |
| `testimonial.html` | 同上（未客製化），內容為3則客戶推薦語 | **REMOVE_LATER** | 「客戶推薦」形式不適用於政府內部審查系統，暫不處理 |
| `contact.html` | 同上（未客製化），含1個通用聯絡表單（姓名/信箱/主旨/訊息），純前端無實際送出邏輯 | **REMOVE_LATER** | 可能於更後期轉為「問題回報」表單，非本階段優先項目 |
| `404.html` | 同上（未客製化），標準錯誤頁 | **KEEP** | 錯誤頁不需業務客製化，直接沿用即可 |

**重要發現**：僅 `index.html` 之 `<title>` 與導覽列文字經過客製化（首頁/系統介紹/
核心功能/審查流程/審查結果），其餘6頁**仍是原始「Poseify」模特兒經紀公司範本**，
標題、文案、圖片說明全數未變更。此與Phase 2 `frontend_data_contract.md` 第一部分
之先前發現一致，本階段以完整檔案掃描重新確認。

---

## B. CSS

| 檔案 | 分類 | 說明 |
|---|---|---|
| `css/bootstrap.min.css`（168KB） | **KEEP** | Bootstrap標準vendor檔案，未客製化，不需改動 |
| `css/style.css`（8KB） | **MODIFY** | 樣板原有樣式（未見自訂 `:root` CSS變數，配色多以Bootstrap `.bg-dark`等utility class控制）；index.html之深色/粉紅主題配色目前**僅存在於index.html內嵌`<style>`區塊**，尚未回寫至此檔案供其他頁面共用，Phase 7整合時應考慮將深色主題色票提升為此檔案之共用CSS變數，避免各頁樣式不一致 |

---

## C. JavaScript

| 檔案 | 分類 | 說明 |
|---|---|---|
| `js/main.js`（4KB） | **KEEP** | 通用UI行為（spinner載入畫面、sticky navbar、回頂部按鈕、wow.js初始化），與業務邏輯無關，可直接沿用；主專案指示第22節要求另建`js/api.js`/`js/config.js`，屬**ADD**（見下） |
| `js/api.js` | **ADD** | 尚不存在，Phase 7建立API串接時需新增，統一管理API base URL與fetch邏輯 |
| `js/config.js` | **ADD** | 尚不存在，Phase 7建立時需新增，區分local/production環境設定 |

---

## D. 圖片資源（img/）

**實際存在**（7個檔案）：`carousel-1.jpg`、`carousel-2.jpg`、`footer-bg.png`、
`picture1.jpg`、`picture2.jpg`、`picture3.jpg`、`service-2.jpg`

| 檔案 | 分類 | 說明 |
|---|---|---|
| `carousel-1.jpg` | **KEEP** | 被 `css/style.css` 引用作為背景圖（`linear-gradient` overlay），仍在使用中 |
| `picture1.jpg`, `picture3.jpg` | **KEEP** | 被 `index.html` 引用中 |
| `service-2.jpg` | **KEEP** | 被 `service.html` 引用中 |
| `carousel-2.jpg`, `footer-bg.png`, `picture2.jpg` | **REMOVE_LATER** | 程式化掃描全部HTML+CSS檔案，**未發現任何引用**，屬孤立（orphaned）素材，暫不刪除但標記供未來清理 |
| `Poseify.jpg`（top-level，非img/內） | **REMOVE_LATER** | 原始模板之預覽截圖，未被任何頁面引用，純屬模板供應商素材 |

**重大發現（Broken Reference）**：程式化交叉比對全部6個HTML頁面實際引用之
`img/*` 路徑與 `img/` 資料夾實際內容，發現**16個引用之圖片檔案不存在**：

| 缺少的圖片 | 被哪些頁面引用 | 分類 |
|---|---|---|
| `img/favicon.ico` | **全部7個HTML頁面**（含index.html） | **ADD**（最高優先，影響全站） |
| `img/about.png` | about.html | ADD（若about.html採MODIFY路線則需要；若內容全部重寫則此參照本身應一併移除） |
| `img/service-1.jpg`, `service-3.jpg`, `service-4.jpg` | service.html | ADD（同上邏輯，依service.html最終內容決定） |
| `img/team-1.jpg` ~ `img/team-8.jpg`（共8個） | about.html, team.html | ADD 或隨REMOVE_LATER頁面一併處理 |
| `img/testimonial-1.jpg` ~ `img/testimonial-3.jpg`（共3個） | service.html, testimonial.html | 同上 |

**建議處理原則**：`favicon.ico`缺失影響全部頁面（包含已客製化之index.html），
應列為Phase 7最優先修復項目；其餘缺失圖片集中於已標記REMOVE_LATER或待MODIFY
之頁面，可與該頁面之內容改版一併處理，不需獨立處理。

---

## E. Libraries（lib/，vendor第三方套件）

| 資料夾/檔案 | 分類 | 說明 |
|---|---|---|
| `lib/animate/`（animate.css, animate.min.css） | **KEEP** | CSS動畫效果庫，vendor檔案 |
| `lib/easing/`（easing.js, easing.min.js） | **KEEP** | jQuery easing外掛 |
| `lib/lightbox/`（css/js/images，含`links.php`） | **KEEP** | 圖片燈箱效果；`links.php`為模板供應商之後端範例檔，本專案不使用PHP後端，**該.php檔本身可標記REMOVE_LATER**（不影響前端功能，純屬未使用之附屬檔案） |
| `lib/owlcarousel/`（含`LICENSE`檔） | **KEEP** | 輪播套件；內含自身LICENSE檔案，一併保留 |
| `lib/waypoints/`（waypoints.min.js，含`links.php`） | **KEEP**（`links.php`同上標記**REMOVE_LATER**） | 捲動觸發效果套件 |
| `lib/wow/`（wow.js, wow.min.js） | **KEEP** | 捲動動畫觸發套件，`js/main.js`已呼叫`new WOW().init()` |

**確認事項**：全部lib/套件均為原生JavaScript/jQuery外掛，**未發現**React、
Vue、Angular或其他前端框架痕跡，符合主專案指示第18節「禁止建立React/Next.js/
Vue/Angular」之現況要求（本身即未使用，非本階段刻意規避）。

---

## F. 授權與說明文件

| 檔案 | 分類 | 說明 |
|---|---|---|
| `LICENSE.txt` | **KEEP** | Creative Commons Attribution 4.0授權，**明文規定不得移除作者歸屬連結** |
| `READ-ME.txt` | **KEEP** | 模板來源說明（HTML Codex「Poseify - Modeling Agency Website Template」） |

**重要授權限制**：`index.html` 頁尾現有 `Designed By <a href="https://htmlcodex.com">HTML Codex</a>` 歸屬連結，**依LICENSE.txt明文規定不得移除**，
Phase 7後續修改頁尾時須保留此連結（可調整樣式但不可刪除或隱藏）。

---

## G. 分類統計總覽

| 分類 | 檔案數（不含lib/內部子檔案逐一列出，以資料夾為單位計） |
|---|---|
| KEEP | 404.html、css/bootstrap.min.css、js/main.js、img/(4個使用中檔案)、lib/全部套件（6個資料夾）、LICENSE.txt、READ-ME.txt |
| MODIFY | index.html、about.html、service.html、css/style.css |
| REMOVE_LATER | team.html、testimonial.html、contact.html、img/(3個孤立檔案)、Poseify.jpg、lib/lightbox/links.php、lib/waypoints/links.php |
| ADD | favicon.ico（最高優先）、其他15個缺失圖片（依頁面改版進度處理）、js/api.js、js/config.js |
| REUSE | about.html/service.html之區塊結構（標題區+內容Grid樣式）可作為未來data.html/analysis.html等頁面之排版參考 |

---

## H. 本階段結論

- 未建立任何新前端框架（React/Vue/Angular/Next.js），符合規定。
- 未對Makerthon_test_1進行任何實際修改，僅讀取盤點。
- 發現1項需優先處理之技術缺陷（favicon.ico於全站缺失），記錄供Phase 7處理，
  本階段不修復。
- 發現授權合規限制（HTML Codex歸屬連結不可移除），記錄供後續開發遵守。
