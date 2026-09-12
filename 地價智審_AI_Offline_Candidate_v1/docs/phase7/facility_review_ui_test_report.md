# FACILITY-REVIEW-UI-1 — 設施證據人工確認 前端驗證報告

## 範圍

`frontend/app/js/facility-review.js`（渲染/互動邏輯）+ `frontend/app/js/api.js`
新增之 `getFacilityCandidates`/`confirmFacilityCandidate`/`rejectFacilityCandidate`
三個方法 + `frontend/app/pdf-preview.html` 新增之「設施證據人工確認」卡片。
不涉及、未修改任何 Rule/Grade/Adjustment/Calculation/Form Completion/GIS Engine
或 Road Width Resolver，亦未修改後端 confirm/reject 語意。

## 驗證方式（誠實區分）

本環境無瀏覽器自動化工具（無 Playwright/Puppeteer，`WebFetch` 明確不支援
localhost）。改採 Node.js + jsdom（僅安裝於環境暫存目錄，**未**加入本專案任何
`package.json`/依賴，維持專案「純靜態網頁、無建置工具鏈」慣例）：

1. 以 `python -m http.server` 在專案根目錄起本地靜態伺服器。
2. 以 `JSDOM.fromURL()` 載入一個僅含 `config.js`/`api.js`/`case-context.js`/
   `facility-review.js` 與卡片容器 DOM 的最小測試頁（不含 jQuery/Bootstrap
   等外部 CDN 依賴，避免此沙箱無網路對外存取造成的無關雜訊），對 **真實**
   `frontend/mock/facility_candidates.json`（見下方生成方式）執行
   `FacilityReview.init()`，檢查真正被瀏覽器引擎解析執行後的 DOM 結果。
3. 針對 Task 14 情境 A–J，另以同一份真實程式碼、監測（monkeypatch）
   `Api.getFacilityCandidates`/`confirmFacilityCandidate`/
   `rejectFacilityCandidate` 三個方法（保留 `facility-review.js` 本身完全不變）
   餵入涵蓋全部 8 種 subtype 狀態與三種 provenance 的合成資料，逐一斷言渲染
   結果與按鈕行為。

**因此本輪結論為 `UI_MOCK_VERIFIED=YES`、`REAL_BACKEND_E2E_VERIFIED=NO`
——尚未對已部署之真實 API Gateway 端點執行過端對端操作（現況見
`backend_deployment.md`：尚無可存取之真實部署）。**

## Mock Fixture 來源

`frontend/mock/facility_candidates.json`（連同 `frontend/app/frontend/mock/`
之對應複本）由 `scripts/build_facility_candidates_mock.py` 產生 —— 該腳本以
moto 模擬 DynamoDB，實際呼叫專案真實的
`facility_confirmation.get_facility_candidates/confirm_facility_candidate/
reject_facility_candidate` handler 與真實 `MockSpecialFacilityProvider`，
走過一次「PENDING→CONFIRMED→（改變 factors 後）CONFIRMED+stale」與一次
「PENDING→REJECTED」的真實生命週期，而非手工編造 JSON。

## Task 14 情境 A–J 結果

以下由 `node scenarios.js`（見下方逐行輸出）實際執行取得，17 項斷言全數
PASS，0 FAIL：

| 情境 | 描述 | 結果 |
|---|---|---|
| A | PENDING+候選 → 顯示「確認採用」/「不採用」按鈕 | PASS |
| B | CONFIRMED（未過期）→ 顯示 `confirmed_selection`、無按鈕、「此資料將寫入正式查估書表」 | PASS |
| C | STALE → 同時顯示「先前確認」與「最新候選」（440 vs 610） | PASS |
| D | STALE → 明確聲明「重新確認前，正式 PDF 將保持空白」 | PASS |
| E | `candidate=null` → 顯示「目前未取得可確認候選」，無按鈕 | PASS |
| F | `source_type=Mock` → 顯示「測試資料」 | PASS |
| G | `source_type=GovernmentOpenData` → 顯示「政府開放資料」 | PASS |
| H | Confirm API 成功 → 觸發重新 GET，狀態變為已確認（非前端假造） | PASS |
| I | Confirm API 失敗 → 狀態維持待確認，顯示中文錯誤訊息，**未**假裝成功 | PASS |
| J | Reject API 成功 → 觸發重新 GET，狀態變為不採用 | PASS |

另補充驗證（非 Task 14 必要項，但屬本輪 Pass Condition 直接相關）：
- `source_type=API`（OSM）→ 顯示「OpenStreetMap／外部資料」（PASS）
- CONFIRMED 卡片文字中**不**出現「政府認證」字樣（PASS，避免 Task 9 禁詞）
- STALE 卡片顯示「⚠ 資料已更新，需重新確認」高辨識度警示徽章（PASS）
- STALE 卡片「正式表單狀態」顯示「暫停寫入正式 PDF，等待重新確認」（PASS）
- REJECTED 卡片顯示「此候選已標記為不採用」且無任何操作按鈕（未發明
  reopen 端點）（PASS）

## 完整 8-subtype 單頁渲染快照（`node verify.js` 節錄）

```
=== SUMMARY STRIP ===
1已確認（將寫入PDF）1需重新確認5待確認1不採用

=== CARD COUNT === 8

--- substation ---  已確認／無按鈕／此資料將寫入正式查估書表／並非官方政府資料驗證結果
--- gas_tank ---    已確認＋資料已更新，需重新確認／先前確認440／最新候選610／
                    重新確認前，正式PDF將保持空白／[確認最新候選][不採用]
--- cemetery ---    待確認／[確認採用][不採用]
--- funeral_home --- 待確認／目前未取得可確認候選／無按鈕
--- crematorium ---  待確認／目前未取得可確認候選／無按鈕
--- columbarium ---  不採用／此候選已標記為不採用／備註：現場勘查後確認非影響因素／無按鈕
--- MRT ---          待確認／目前未取得可確認候選／無按鈕
--- TRA ---          待確認／目前未取得可確認候選／無按鈕
```

## 已知限制 / BACKLOG

- REJECTED 卡片目前僅顯示狀態，後端尚無「不採用→重新開啟」端點，本輪未新增
  （見卡片內文字：「目前後端尚未提供『不採用 → 重新開啟審查』功能」）。如需
  重新審查請洽系統管理員；若日後確有需求，應另開一輪明確新增對應後端端點
  後再補前端功能，本輪依指示不擅自發明。
- 尚未執行 Manual browser acceptance（人工於真實瀏覽器點擊操作），理由同
  `frontend_deployment.md` 既有誠實聲明：本環境無瀏覽器可用。
