> 來源撤回：本文件使用的會議錄影、逐字稿及視覺時間軸已移除。相關推論尚未重新以正式書面資料驗證，不可再視為官方依據。

# 決賽當天：真實資料來源 Provider（取代Mock）

## 為什麼需要這份文件

Phase 1 evidence matrix REQ-005/006/007 已確認：官方**不會**提供任何GIS圖層
原始檔，且決賽案例的區段一定跟賽前金山區範例不同。`providers/*.py` 原本
全部是 Mock 實作——回傳寫死的金山區 Golden Case 數值，不管實際問的是哪個
案件。這份文件說明如何切換到真實資料來源，讓系統能真的回答「一個從沒見過
的新北市地點」，而不是每次都複誦同一組金山區數字。

## 涵蓋範圍

7個Provider中，5個（`transportation_provider.py`、`public_facility_
provider.py`、`special_facility_provider.py`、`environmental_provider.py`、
`commercial_activity_provider.py`）欄位形狀高度一致（名稱／是否在區段內／
距離公尺數），改用 [OpenStreetMap](https://www.openstreetmap.org) 的
Overpass API（設施查詢）即時查詢真實資料。

**故意不處理**：`road_provider.py`（道路寬度）——OSM對台灣道路寬度標記
涵蓋率很低，沒有可靠的公開資料源可用，固定使用 Mock，誠實地留在
`docs/backlog.md` 追蹤。

`land_use_provider.py`（都市計畫使用分區／建蔽容積率等地政登記資料）：
**已接入** `DATA_PROVIDER_MODE`。`real` 模式下，`RealLandUseProvider`
的 `land_use_zone` 欄位改用 `providers/ntpc_zoning_provider.py` 的
`RealNtpcZoningProvider` 查詢新北市政府城鄉發展局公開的「新北市都市計畫
土地使用分區及範圍圖」（point-in-polygon，見下方「都市計畫使用分區」一
節）；`land_use_provider.py` 其餘欄位（建蔽率、容積率、禁限建、排水、地勢
等）仍無可靠公開資料源，`real` 模式下這些欄位固定回傳 `UNKNOWN`／
`MANUAL_REVIEW_REQUIRED`，**不會**偷偷退回 Mock 的金山區數值（那會讓
「這是Golden Case範例值」被誤當成「這是這個案件的真實查詢結果」）。

## 都市計畫使用分區（`RealNtpcZoningProvider`，已接入主流程）

`providers/ntpc_zoning_provider.py` 提供對「新北市使用分區」shapefile（新北
市政府城鄉發展局，[data.ntpc.gov.tw](https://data.ntpc.gov.tw/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5)）
的本地快照查詢：

1. 執行 `py scripts/sync_ntpc_zoning_dataset.py` 下載＋重投影（EPSG:3826→
   WGS84）＋寫入本地SQLite快照（`data/snapshots/ntpc_zoning_<版本>.sqlite3`），
   並登記進 `DatasetRegistry`（`data/dataset_registry.sqlite3`）。此腳本**不**
   在Lambda請求路徑上執行，須手動或另外排程觸發。
2. `RealNtpcZoningProvider.query(coordinate)` 用bbox預篩選＋shapely
   `.contains()`做point-in-polygon精確比對，回傳分區名稱；查無資料、快照
   過期（`STALE`）、快照不存在（`UNAVAILABLE`）、座標同時落在多個分區圖徵
   內，都誠實地回傳 `UNKNOWN`/`requires_manual_review=True`，不猜測。
3. 已知資料缺口：來源shapefile只有 `ZONE` 一個屬性欄位，沒有「都市計畫
   名稱」欄位，所以 `plan_name` 固定為 `None`；要補上須另外對「新北市都市
   計畫範圍」資料集做空間疊合，尚未實作。
4. 離線測試：`tests/test_dataset_registry.py`、
   `tests/test_sync_ntpc_zoning_dataset.py`、`tests/test_land_use_provider.py`
   全部使用合成的小型測試資料／stub，不依賴75MB的真實下載、也不會在跑
   測試時於專案 `data/` 目錄留下真實檔案（`DatasetRegistry` 支援
   `DATASET_REGISTRY_DB_PATH` 環境變數重導向，見
   `tests/test_backend_handlers_e2e.py`的`ddb_env` fixture）；真實下載＋
   重投影＋point-in-polygon的完整流程已於開發過程中人工對真實資料
   （34,179筆分區圖徵）驗證過一次（非測試套件一部分）。
5. **在決賽環境實際生效前，必須先手動執行一次
   `py scripts/sync_ntpc_zoning_dataset.py`**——在還沒跑過同步job、
   `DatasetRegistry`裡沒有`ntpc_zoning`快照記錄的環境下（例如一個全新
   部署、還沒手動跑過sync job的Lambda），`real`模式的`land_use_zone`
   會誠實回傳`UNKNOWN`（`DatasetSnapshotStatus.UNAVAILABLE`），而不是
   報錯或悄悄退回Mock值。

## 怎麼切換

環境變數 `DATA_PROVIDER_MODE`：

- `mock`（預設，未設定時的行為）：所有Provider回傳金山區Golden Case固定值，
  離線、快速、無需網路，測試套件全程使用此模式。
- `real`：6個Provider改用真實資料來源：5個（transportation/public_facility/
  special_facility/environmental/commercial_activity）**即時查詢OSM**
  （每次請求都打對外網路）；`land_use_provider`的`land_use_zone`欄位改
  查**本地快照**（`RealNtpcZoningProvider`，不打網路，只讀`sync_ntpc_
  zoning_dataset.py`事先寫好的SQLite檔案），其餘14個`land_use_provider`
  欄位固定`UNKNOWN`。`road_provider`仍固定為Mock（沒有任何模式下的真實
  資料來源）。

沒有獨立的「offline」或「hybrid」`DATA_PROVIDER_MODE`值——`real`模式本身
就是「部分欄位即時打網路、部分欄位讀本地快照、部分欄位誠實UNKNOWN」的
混合狀態，這是`land_use_zone`資料特性（政府只公開快照下載，沒有可查詢的
即時API）決定的，不是需要另外設計的模式維度。下表整理三種
`DATA_PROVIDER_MODE`取值×兩個受影響Provider的實際行為：

| DATA_PROVIDER_MODE | transportation等5個OSM Provider | land_use_provider.land_use_zone | land_use_provider其餘14欄位 | road_provider |
|---|---|---|---|---|
| （未設定，即`mock`） | Mock金山區固定值 | Mock金山區固定值（"第二種商業區"） | Mock金山區固定值 | Mock固定值 |
| `real`，且已跑過sync job | 即時查詢OSM | 查本地快照，回傳真實ZONE值 | `UNKNOWN` | Mock固定值 |
| `real`，但**尚未**跑過sync job | 即時查詢OSM | `UNKNOWN`（`UNAVAILABLE`，非報錯） | `UNKNOWN` | Mock固定值 |

決賽現場要切換時，在 `infra/template.yaml` 對應Lambda（`CollectDataFunction`）
的環境變數加上 `DATA_PROVIDER_MODE: real` 即可，`backend/handlers/
collect_data.py` 不需要改任何程式碼；但務必記得**額外**手動執行一次
`sync_ntpc_zoning_dataset.py`（此腳本不在Lambda請求路徑上，也不會被
`DATA_PROVIDER_MODE`自動觸發）。

## 座標怎麼來

`ProviderContext.center_coordinate` 是所有真實查詢的起點。取得順序：

1. 若前端/呼叫端在 `collect-data` 的請求 body 直接帶 `center_coordinate`
   （`{"latitude":.., "longitude":..}`），優先採用——最可靠，不猜測。
2. 否則，僅在 `DATA_PROVIDER_MODE=real` 時，用案件的 `city`+`district`+
   `segment_scope`（案件建立時填寫的區段範圍描述）透過 Nominatim 做
   best-effort地理編碼；查不到就是查不到，`center_coordinate`留空，
   後續所有真實Provider會誠實回傳 MANUAL_REVIEW_REQUIRED，不會用猜的
   座標硬算。

## 「絕不猜測」的兩條界線（刻意保留，不是尚未完成）

- **`{欄位}_within_segment`（是否在本區段內）永遠是 MANUAL_REVIEW_
  REQUIRED**：這需要地價區段的GIS邊界圖資，官方明確表示不提供（REQ-005）。
  用距離門檻去猜邊界，等於發明一條Sources沒有依據的業務規則——這正是本
  專案從 `GeoDistanceEngine` 到每個Provider都嚴格禁止的行為。
- **主觀評估欄位永遠不猜**：站牌密集程度（`bus_stop_density`）、顧客通行量
  （`customer_traffic_volume`）、店舖毗連狀態（`shop_contiguity`）、
  各類污染源欄位、國小/國中/高中/大學分級（OSM涵蓋率過低不可靠）——這些
  沒有Sources定義的公式可以從OSM原始資料換算，一律留 MANUAL_REVIEW_
  REQUIRED，交還人工判斷。

## 距離演算法：REQ-010（一般設施步行距離 vs 特殊設施直線距離）

官方兩次口頭確認（逐字稿）：**一般設施**（交通運輸、公共建設、商業機構等）
用「最近的步行距離」；**特殊設施**（殯葬設施等嫌惡設施）用「直線距離」。

- `engine/geo_distance_engine.py`的`route_distance()`現在真的會呼叫
  [OSRM](https://project-osrm.org)公開路由服務（`foot` profile，即步行路網），
  已實測可用；連線失敗時誠實回傳`MANUAL_REVIEW_REQUIRED`，絕不用直線距離
  靜默取代（避免誤植為不同演算法算出的數字）。
- 每個 `FacilitySpec` 依照 `data/rules/*.json` 該因素的 `value_type` 方向
  決定用哪種演算法：`distance_positive`（越近越好，便利性設施）用ROUTE，
  `distance_negative`（越近越差，嫌惡設施）用STRAIGHT_LINE：
  - `transportation_provider.py`（大型車站/交流道）→ ROUTE
  - `public_facility_provider.py`（市場/公園/觀光遊憩/停車場）→ ROUTE；
    `wastewater_facility`例外，屬嫌惡設施性質，維持STRAIGHT_LINE
  - `commercial_activity_provider.py`（百貨/金融/娛樂/展示中心）→ ROUTE
  - `special_facility_provider.py`、`environmental_provider.py`（變電所/
    瓦斯/殯葬/廢棄物處理等）→ 維持STRAIGHT_LINE（官方逐字稿明確確認）
- 找最近設施時仍用直線距離排序候選點（省去對每個候選都呼叫路由API的成本），
  只對「排序後勝出的那一個」額外呼叫一次OSRM算真實步行距離。若這唯一一次
  OSRM呼叫失敗，該筆設施的**名稱**仍會回報（因為Overpass查詢本身成功），
  但**距離**會標記MANUAL_REVIEW_REQUIRED，不會靜默退回直線距離數字。

## 半徑怎麼定的

每個設施類別的查詢半徑，**對照 `data/rules/regional_rules.json` 該因素
實際最差等級的 `upper_bound`** 設定（例如「接近市場之程度」官方級距最遠
到1800M，Provider就查1800M半徑），不是憑感覺挑的整數。詳見各Provider檔案
內的註解與 [providers/special_facility_provider.py](../../providers/special_facility_provider.py)
等檔案。

## 測試

- `tests/test_osm_facility_lookup.py`、`tests/test_real_facility_providers.py`：
  離線、mock掉網路請求，涵蓋欄位對應、絕不猜測邊界/主觀欄位、找到/找不到
  兩種情境。
- `tests/test_backend_handlers_e2e.py::TestDataProviderModeSwitch`：驗證
  `DATA_PROVIDER_MODE` 切換、座標解析優先順序，同樣全程mock網路。
- `scripts/smoke_test_real_providers.py`：**唯一**會真的打網路的測試，
  刻意不放進pytest。手動執行，人工檢查回傳的設施名稱/距離是否合理：
  ```bash
  py scripts/smoke_test_real_providers.py [緯度] [經度]
  ```
  預設座標為板橋車站附近（刻意跟金山區不同，用來證明系統真的能回答一個
  從沒見過的地點，不是又在複誦Golden Case數字）。

## 已知風險（誠實列出，非阻擋）

- **這個開發環境能連到 `overpass-api.de`／`nominatim.openstreetmap.org`
  （已實測 HTTP 200），但決賽當天正式AWS Lambda環境的對外網路政策未知**
  ——若AWS帳號有網路白名單限制，Lambda連不到這兩個服務，所有真實Provider
  會全部退回 MANUAL_REVIEW_REQUIRED（不會crash，但也就沒有真實資料）。
  這應該是「AWS部署演練」清單中第一件要測的事，見
  `docs/phase8a/competition_checklist.md`。
- Overpass公開伺服器對高頻請求有速率限制（開發時實測遇過，加了1次重試+
  3秒backoff緩解，但無法完全避免）；`RealFacilityProviderBase`在同一次
  `fetch()`內查詢多個設施類別時，會在每次查詢間加1秒間隔降低觸發機率。
- OSM是社群協作地圖資料，非官方登記資料，可能有名稱差異或資料缺漏——每筆
  真實查得的資料，`NormalizedDataPoint.notes`都會註明「建議人工快速核對」，
  `confidence`固定為「中」而非「高」，讓Smart Review/前端能清楚區分這是
  即時查詢結果，非官方權威來源。
