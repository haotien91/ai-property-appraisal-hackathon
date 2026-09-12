# Phase API-2 — Official Facility Evidence Pipeline

> This is a NEW, separate document from `docs/phase9/api_integration_spec.md`
> deliberately -- that document covers the FROZEN (as of Phase API-1.9)
> Official Cadastral Evidence Pipeline (cadastral identifier normalization,
> expropriation/land-price snapshots, atomic staging, versioned checksum,
> multi-record expropriation semantics). This round's explicit instruction
> is "禁止再修改" that pipeline except for a genuine P0 correctness blocker
> (none found), so its documentation stays untouched. Everything below is
> new, additive infrastructure for a disjoint dataset family (school/market/
> park/station), reusing the SAME architectural principles (Atomic Staging
> Contract, official-first, never-fabricate) but never the same code/tables.

## 0. Scope

First batch only: **學校 (school) / 市場 (market) / 公園‧廣場 (park) / 車站
(station)**. 捷運站/公車站 were evaluated as part of the "車站" audit (see
§3D). No TGOS/NLSC/OSM fallback this round; no routing API; no LLM anywhere
in this pipeline; Rule/Grade/Adjustment/Calculation Engines untouched.

## 1. Repo Audit (§1 of the round's instructions)

`CURRENT_FACILITY_CAPABILITY`:

| Capability | Exists? | Where |
|---|---|---|
| FacilityProvider (generic, official-source) | **NO** (before this round) | -- |
| POIProvider (OSM-based) | YES, but NOT official | `providers/osm_facility_lookup.py` (`find_nearest_facility`, Overpass API) |
| DistanceCalculator | YES, FROZEN (`engine/` = Core Freeze) | `engine/geo_distance_engine.py` (`GeoDistanceEngine.straight_line_distance`/`route_distance`, Haversine WGS84 + OSRM) |
| CoordinateProvider | Partial | `ProviderContext.center_coordinate` (existing field, reused here) |
| GeocodingProvider | YES, but Nominatim-based, out of scope this round | `osm_facility_lookup.geocode()` |

Existing real-mode facility infrastructure already covers a similar set of
factors via **OpenStreetMap Overpass** (NOT official government data):
`providers/real_facility_provider_base.py` + `providers/public_facility_
provider.py` (market/park/tourism/parking/wastewater) + `providers/
transportation_provider.py` (major_station/interchange/bus_stop). These are
**NOT modified or replaced** by this round -- they continue to serve
`FACTORS.points` exactly as before. This round's `OfficialFacilityProvider`
is a SEPARATE, ADDITIVE evidence trail (`official_facility_evidence` in
`FACTORS`), specifically because the round's mandate is *official* data,
which the existing OSM pipeline's own docstrings already admit it is not
("OSM為社群協作資料，非官方登記資料").

No pre-existing `FacilityEvidence`/`FacilityMatch` model, no pre-existing
official-dataset facility cache. Both built new this round (additive to
`domain/models.py`; a new, separate `providers/facility_dataset_cache.py`
-- NOT an extension of the frozen `cadastral_dataset_cache.py`).

## 2. Appraisal-Manual Basis + FACILITY_FACTOR_MAPPING (§2)

Per `docs/phase2/distance_rules.md` (already-established Phase 2 findings,
re-read for this round, not re-litigated):

- 表1 (地價區段勘查表，REGIONAL factors) -- measured from **本區段中心點**,
  covers 大型車站/站牌/學校/市場/公園廣場徒步區/... (`data/rules/
  regional_rules.json`'s `REG-MAJOR_STATION_PROXIMITY-*`/`REG-MARKET_
  PROXIMITY-*`/`REG-PARK_PROXIMITY-*` -- **no REG-SCHOOL_PROXIMITY exists**
  at the regional level).
- 表4 (宗地個別因素清冊，INDIVIDUAL factors, "接近條件") -- measured from
  **該宗地中心點**, covers 學校/市場/公園、廣場/車站/商圈 (`data/rules/
  individual_rules.json`'s `"factor": "接近學校之程度"` rows -- category
  "接近條件(3)", already deterministic and Golden-Case-verified: "150m/
  100m皆優(<200m)→0.00%一致").
- Manual's algorithm guidance (p.24): 通達-type facilities (school/market)
  → route/walking distance suggested; nuisance-type (焚化爐 etc.) → straight-
  line. Manual ALSO states (p.24): "同一細項有多個設施存在...則以對當地
  地價影響最大者填寫...由查估人員判斷" -- an EXPLICIT human-judgment
  requirement when multiple facilities exist, never a pure nearest-wins
  rule. This directly shaped `FacilityEvidence.matches` (always ALL
  candidates) vs `.nearest` (a convenience pointer only, never the manual's
  required "most impactful" choice).

| facility_type | related_factor_id | official_basis | distance_required | grade_rule_available | runtime_status |
|---|---|---|---|---|---|
| SCHOOL | 個別因素"接近學校之程度"（`individual_rules.json`） | 評價基準明細表範例.pdf p.7 | 是（宗地中心點量測） | **YES**（<200m等既有門檻，Golden Case已驗證） | 本輪新增官方座標來源（見§3A），先前唯一來源OSM因涵蓋率過低完全留空 |
| MARKET | 區域因素"接近市場之程度"（`regional_rules.json` REG-MARKET_PROXIMITY-*） | 評價基準明細表範例.pdf | 是（區段中心點量測） | YES（既有regional門檻） | 官方資料集僅有名稱/地址，無座標（ADDRESS_ONLY），距離事實本輪無法由官方來源計算；既有OSM pipeline仍為唯一可算距離之來源 |
| PARK | 區域因素"接近公園...之程度"（REG-PARK_PROXIMITY-*） | 同上 | 是 | YES | 同MARKET，ADDRESS_ONLY |
| STATION | 區域因素"接近大型車站之程度"（REG-MAJOR_STATION_PROXIMITY-*） | 同上 | 是 | YES | 本輪新增官方座標來源（捷運站/火車站，見§3A）；既有OSM pipeline之`major_station`鎖定`amenity=bus_station`，語意上實為巴士站而非manual所指車站，本輪官方資料明確區分捷運/火車站 |

**重要**：API提供距離事實不代表可直接決定grade/adjustment_rate——Grade
Engine/data/rules/individual_rules.json與regional_rules.json之既有
deterministic門檻**完全未變更**，本Provider之輸出只餵入`FacilityEvidence`
（新增之corroborating evidence），不進入RuleEngine/GradeEngine/
AdjustmentEngine/CalculationEngine之計算路徑。

## 3. NTPC OpenData Audit (§3-4)

方法：下載完整資料集清單CSV（`https://data.ntpc.gov.tw/api/datasets/info/
csv`，1,747個資料集），以關鍵字搜尋後逐一以直接API請求驗證欄位/座標/CRS/
filter行為（非僅閱讀資料集標題/描述）。

### A. 學校 + D. 車站（同一資料集）

**新北市重要地標資訊**（`6DCFF24A-838C-40FB-A9DF-F1160AFAFE84`，新北市政府
研究發展考核委員會/資訊中心，來源為「新北iMAP」，每日更新）：

- fields（VERIFIED直接查詢）：`objectid, 行政區, 地標類型, 地標名稱, 地址,
  電話, 網址, twd97_x, twd97_y, 更新日期`
- **coordinate_fields = twd97_x/twd97_y**；**CRS = TWD97 TM2（EPSG:3826）**
  ——由欄位名稱本身明確標示，非猜測；經`pyproj.Transformer.from_crs(
  "EPSG:3826","EPSG:4326")`重投影後之座標，與已知真實地點（例：國立臺北
  大學三峽校區重投影後為(121.373,24.945)，與該校真實所在地吻合）交叉比對
  驗證正確，非僅信任函式庫。**此pyproj+EPSG:3826轉換方式與參數，與
  `scripts/sync_ntpc_zoning_dataset.py`已驗證使用之方式完全相同**，非本輪
  新發明的轉換。
- 全量2,056筆，29種`地標類型`，本輪採用：國民小學(242)/國民中學(110)/
  完全中學(50)/高中職(44)/大專院校(26) → SCHOOL（共472筆）；捷運站(115)/
  火車站(27) → STATION（共142筆）。刻意排除公車站（見下方D段說明）。
- **filter行為驗證**（NTPC_SERVER_FILTER_TRUST_POLICY = UNTRUSTED_UNLESS_
  VERIFIED_PER_DATASET，延續Phase API-1政策）：對此資料集實測
  `filter=地標類型 eq 國民小學` vs 無filter vs `filter=地標類型 eq
  不存在的分類XYZ123` vs `filter=行政區 eq 金山區`，**四者回傳完全相同之
  2,056筆資料**——filter為COMPLETE NO-OP（本專案第三個證實此行為之NTPC
  資料集，繼徵收案件與土地現值資料集之後）。`scripts/sync_facility_
  dataset.py`因此從不傳送filter參數，一律全量抓取後client-side比對。
- record_count=2,056；update_frequency=每日；coverage=新北市全境（29行政
  區皆有資料）；authentication=無需認證；runtime_usability=可即時查詢但
  不作為primary path；sync_usability=**高**（單頁即可取得全部資料，遠低於
  WAF門檻）。

### B. 市場

**新北市公有市場及超市清冊**（`785BE91A-CAAF-4E1C-91D6-F7D616D31A45`，
經濟發展局/市場處，每年更新）：

- fields（VERIFIED）：`item, name, county, countycode, town, areacode,
  address, phone, types`
- **無任何座標欄位**——非CRS不明，而是資料集本身結構上就沒有座標，誠實
  標記為`ADDRESS_ONLY`，不猜測、不偷偷Nominatim geocode。
- record_count=48（VERIFIED，全量抓取實測）。

### C. 公園／廣場

**新北市公園**（`5FE3A136-29CC-4695-A17E-6636A32C3342`，農業局/綠美化
環境景觀處，每年更新）：

- fields（VERIFIED）：`seqno, name, area, address, management,
  localcallservice, areacode`
- **無任何座標欄位**——同上，標記`ADDRESS_ONLY`。
- record_count=635（VERIFIED）。
- 附註：新北市重要地標資訊內另有「防災公園」類別（24筆，含座標），但屬
  「災害應變指定公園」之窄類別，與作業手冊「里鄰公園、一般公園、廣場、
  徒步區」語意不完全對應，本輪**未**將其納入PARK facility_type（避免
  以偏概全，誤導成「新北市只有24座公園」）。

### D. 車站：刻意排除公車站

`新北市公車站位資訊`（`34B402A8-53D9-483D-9406-24A682C2D6DC`，每5分鐘
更新，屬公車動態資訊）**未**用於STATION facility_type——依本輪明確指示
「不要把bus stop直接等同查估手冊中的車站」，且landmark資料集本身已提供
乾淨、官方標籤明確之捷運站/火車站分類，無需額外判斷公車站是否算「車站」。

## 4. FACILITY_DATASET_MATRIX

| Facility Type | Dataset | Dataset ID | Coordinates | CRS | Coverage | Auth | Filter Verified | Runtime Strategy | Usable |
|---|---|---|---|---|---|---|---|---|---|
| SCHOOL | 新北市重要地標資訊 | 6DCFF24A-838C-40FB-A9DF-F1160AFAFE84 | YES (twd97_x/y) | TWD97 TM2 (EPSG:3826) → 轉WGS84 | 新北市全境 | 免認證 | CONFIRMED NO-OP | SCHEDULED_SYNC → snapshot | **YES** |
| STATION | 新北市重要地標資訊（同上） | 6DCFF24A-838C-40FB-A9DF-F1160AFAFE84 | YES | TWD97 TM2 → 轉WGS84 | 新北市全境 | 免認證 | CONFIRMED NO-OP | SCHEDULED_SYNC → snapshot | **YES** |
| MARKET | 新北市公有市場及超市清冊 | 785BE91A-CAAF-4E1C-91D6-F7D616D31A45 | NO（ADDRESS_ONLY） | N/A | 新北市全境 | 免認證 | 未測（無filter需求，全量僅48筆） | SCHEDULED_SYNC → snapshot | PARTIAL（存在性/地址可查，距離不可算） |
| PARK | 新北市公園 | 5FE3A136-29CC-4695-A17E-6636A32C3342 | NO（ADDRESS_ONLY） | N/A | 新北市全境 | 免認證 | 未測（同上） | SCHEDULED_SYNC → snapshot | PARTIAL（同上） |

```
SCHOOL_DATA_READY = YES
MARKET_DATA_READY = PARTIAL
PARK_DATA_READY = PARTIAL
STATION_DATA_READY = YES
GOLDEN_CASE_COORDINATE_READY = NO
```

## 5. Golden Case Coordinate Availability (§11)

檢查`data/golden/golden_case_input.py`（無任何座標欄位）與repo內所有
Golden-Case相關fixture：**無任何官方/可信之地號級座標**。唯二存在的座標
來源：(a) `providers/transportation_provider.py`之`JINSHAN_DISTRICT_
CENTROID`——文件中明確標註為Wikipedia來源之DEMO-ONLY錨點，從未宣稱為此
地號之權威座標；(b) `backend/handlers/collect_data.py`之
`_resolve_center_coordinate()` Nominatim geocode fallback——僅為「市+區+
段名」文字之粗略geocode，非經驗證之地號級官方座標，且本輪明確禁止
「偷偷Nominatim geocode」。兩者皆不符合本輪「官方/可信座標」門檻。

**`GOLDEN_CASE_COORDINATE_READY = NO`**。

**缺少的資料來源**：一個官方、地號級（非僅市/區/段名文字）的座標登記
（例如地籍圖數值化成果、或TGOS地址定位服務）——兩者皆為本輪明確禁止
（NLSC/TGOS）之範疇，需在未來獲批准之輪次另行處理。

**已完成之驗證（使用測試用demo錨點座標，非聲稱為Golden Case官方座標）**：

```
[TEST-ONLY, Wikipedia-sourced Jinshan district demo anchor 25.23611,121.61750]
SCHOOL: match_count=8（金山區真實8所學校，官方資料）, nearest=私立法鼓山僧伽大學, distance_m=603.25
STATION: match_count=0（金山區確實無捷運/火車站，符合地理事實）
MARKET: match_count=1（金山中繼公有市場）, distance_method=UNKNOWN（ADDRESS_ONLY，無法算距離）
```

## 6. Provider Architecture (§6)

`providers/official_facility_provider.py`：`MockOfficialFacilityProvider` /
`RealOfficialFacilityProvider`（沿用既有Mock/Real DI慣例，同
`RealExpropriationCaseProvider`）。`query_facility(ctx, facility_type)` 輸入
`facility_type` + `ctx.center_coordinate`（重用既有`ProviderContext`欄位，
未新增欄位）+ 隱含`ctx.district`；輸出`FacilityEvidence`（`domain/
models.py`新增，additive，`matches: List[FacilityMatch]`、`nearest`、
`match_count`、`distance_method`、完整provenance）。單一架構涵蓋全部4類，
不因SCHOOL/STATION有座標而MARKET/PARK無座標分別建立不同架構——差異僅在
`FacilityMatch.coordinate_status`（WGS84 vs ADDRESS_ONLY vs
COORDINATE_CRS_UNKNOWN）與對應之`distance_m`是否為None。

## 7. Distance Calculation (§7)

僅geodesic直線距離（`GeoDistanceEngine.straight_line_distance`，Haversine
WGS84公式，**FROZEN引擎，本輪未修改**），`FacilityEvidence.distance_method
= GEODESIC_WGS84`。未串接OSRM/Google Directions/任何routing API，即使
作業手冊對學校/市場「建議」路線距離——因該建議措辭為"建議"而非強制規定
（`docs/phase2/distance_rules.md`已標註此點為【待確認】層級），且routing
API屬本輪明確禁止範疇。無LLM涉入距離計算之任何環節。

## 8. Distance Evidence ≠ Grade (§8)

`FacilityEvidence`/`FacilityMatch`模型中**無**`grade`或`adjustment_rate`
欄位（`tests/test_official_facility_provider.py::
test_evidence_model_has_no_grade_field`鎖定）；`official_facility_
provider.py`**未import**任何Rule/Grade/Adjustment/Calculation Engine
（`test_provider_never_imports_rule_or_grade_engine`結構性鎖定）。Grade
判定完全維持既有`data/rules/individual_rules.json`/`regional_rules.json`
既有deterministic規則，由人工確認表單值後餵入，本Provider之輸出僅止於
`FACTORS.official_facility_evidence`，未接入計算路徑。

## 9. Snapshot Strategy (§9)

`providers/facility_dataset_cache.py`（**全新、獨立**於已凍結之
`cadastral_dataset_cache.py`——原因見該模組docstring：不可修改已凍結
pipeline，重新套用相同「STAGING→VALIDATE→ATOMIC PROMOTE→CURRENT」原則於
新的、不相干的資料表）。三個來源資料集（landmark/market/park）皆為
SCHEDULED_SYNC→本地snapshot策略（資料量小、更新頻率低，符合本輪「Demo
不應強依賴政府API即時可用」原則），`scripts/sync_facility_dataset.py`
執行，任一資料集同步失敗皆不影響其餘資料集之CURRENT snapshot（各自獨立
dataset_id namespace）。

## 10. Official-first (§10)

`official_facility_provider.py`結構性未import`osm_facility_lookup`或任何
geocoding函式（`test_module_never_imports_osm_or_geocoding`以AST解析
import語句鎖定，非僅字串搜尋docstring）。MARKET/PARK因官方資料無座標而
無法算距離時，回傳誠實之`ADDRESS_ONLY`/`UNKNOWN`，**不**自動改用OSM查詢
座標。

## 11. Mock/Real (§12)

`MockOfficialFacilityProvider`：完全offline，`query_facility()`一律回傳
`match_count=0`/`UNKNOWN`，`fetch()`每個field皆`value=None`。
`RealOfficialFacilityProvider`：真實路徑缺資料一律`UNKNOWN`/`match_count=0`
並附sync指示，**從不**silent fallback至Mock（`test_real_never_falls_
back_to_mock`鎖定：real evidence之`dataset_id`必為實際值，mock恆為
`None`）。

## 12. Tests (§13) — 22項情境，全數offline

`tests/test_facility_dataset_cache.py`（9項，cache層級staging/promote/
atomicity/lookup）+ `tests/test_official_facility_provider.py`（22項，
provider層級）。對照表：

| # | 情境 | 測試 |
|---|---|---|
| 1 | school nearest lookup | `test_school_nearest_lookup` |
| 2 | market nearest lookup | `test_market_nearest_lookup_address_only` |
| 3 | park nearest lookup | `test_park_nearest_lookup_address_only` |
| 4 | station nearest lookup | `test_station_nearest_lookup` |
| 5 | no coordinate dataset | `test_no_coordinate_dataset_never_fabricates_distance` |
| 6 | unknown CRS | `test_unknown_crs_never_computes_distance` |
| 7 | empty dataset | `test_empty_dataset_zero_matches` |
| 8 | target coordinate missing | `test_target_coordinate_missing` |
| 9 | exact same coordinate distance=0 | `test_exact_same_coordinate_distance_zero` |
| 10 | deterministic distance | `test_deterministic_distance_repeatable` |
| 11 | nearest selection deterministic | `test_nearest_selection_deterministic_regardless_of_lookup_order` |
| 12 | tie distance ordering | `test_tie_distance_deterministic_tiebreak` |
| 13 | dataset provenance | `test_dataset_provenance_present` |
| 14 | Mock offline | `test_mock_never_touches_network_or_cache` |
| 15 | Real no silent fallback | `test_real_never_falls_back_to_mock` |
| 16 | malformed official record | `test_sync_script_skips_unmapped_landmark_type_and_bad_coordinate` |
| 17 | invalid coordinate | 同上（`twd97_x="not-a-number"`分支） |
| 18 | snapshot unavailable | `test_snapshot_unavailable_returns_unknown_with_sync_instruction` |
| 19 | API filter actually ignored | `test_sync_script_never_sends_filter_param`（結構性；live驗證見§3A） |
| 20 | official-first behavior | `test_module_never_imports_osm_or_geocoding` |
| 21 | distance evidence does not directly set grade | `test_evidence_model_has_no_grade_field` + `test_provider_never_imports_rule_or_grade_engine` |
| 22 | Golden Case behavior | `test_golden_case_target_coordinate_unavailable`（synthetic但反映真實情況：無座標） |

## 13. Real Sync 執行結果（真實對政府API執行）

```
landmark: fetched 2056 rows
landmark: mapped 614 rows to SCHOOL/STATION (skipped 1442 unmapped-type, 0 bad-coordinate)
landmark: promote完成 total_record_count=614
新北市公有市場及超市清冊: fetched 48 rows
新北市公有市場及超市清冊: promote完成（ADDRESS_ONLY，無座標）total_record_count=48
新北市公園: fetched 635 rows
新北市公園: promote完成（ADDRESS_ONLY，無座標）total_record_count=635
```

## 14. Core Freeze + Cadastral Pipeline Freeze複查

- Core Freeze 36檔案（`engine/`、`document_extraction_provider.py`、
  `data/rules/`、`schemas/`）SHA-256組合雜湊：BEFORE=AFTER=
  `0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  ——**`CORE_FREEZE_VIOLATION = NO`**。
- OFFICIAL_CADASTRAL_EVIDENCE_PIPELINE相關檔案（`cadastral_identifier.py`/
  `cadastral_dataset_cache.py`/`expropriation_case_provider.py`/
  `land_price_provider.py`/`sync_expropriation_dataset.py`/`sync_land_
  price_dataset.py`）：本輪**零次**Write/Edit呼叫觸及這些檔案，其相關
  測試套件（`test_cadastral_dataset_cache.py`等）於本輪結束時仍全數通過，
  無任何行為變化——**`CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO`**。

## 15. Regression

```
py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py
674 passed, 0 failed （較Phase API-1.9的643項，新增31項Phase API-2測試）
```

## 16. Phase API-2 Release Gate

```
FACILITY_DATA_AUDIT = PASS
VERIFIED_FACILITY_TYPES = SCHOOL, STATION
IMPLEMENTED_FACILITY_TYPES = SCHOOL, MARKET, PARK, STATION
  （MARKET/PARK實作為誠實ADDRESS_ONLY路徑，不計算距離，不視為VERIFIED）
OFFICIAL_FACILITY_PROVIDER = PASS
DISTANCE_ENGINE = PASS（重用既有FROZEN GeoDistanceEngine，未修改）
MOCK_OFFLINE = PASS
REAL_NO_SILENT_FALLBACK = PASS
GOLDEN_CASE_COORDINATE_READY = NO
GOLDEN_CASE_FACILITY_EVIDENCE = TARGET_COORDINATE_UNAVAILABLE
  （8校實際存在於金山區官方資料中，但因無官方地號座標無法算距離；
    0捷運/火車站，符合金山區實際地理狀況）
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
CORE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 674 passed, 0 failed
PHASE_API_2_RELEASE_READY = YES
```

Implementation Gate（§16原始要求：至少2類以上核心Facility具備VERIFIED
official dataset + 可信座標 + CRS確認才允許進入Implementation）**已達成**
——SCHOOL與STATION兩類皆有VERIFIED官方資料集、真實WGS84座標（經TWD97轉換
並交叉驗證）、CRS明確確認（EPSG:3826，非猜測）。MARKET/PARK維持誠實
ADDRESS_ONLY，未因「湊四類」而引入TGOS/NLSC/OSM等本輪禁止之外部來源。

本輪未觸碰TGOS、NLSC、OSM fallback、Nominatim fallback、routing API、
LLM POI分類/grade決策/距離估計，未修改Rule/Grade/Adjustment/Calculation
Engine，未修改已凍結之Cadastral Pipeline，未自動產生新區域因素規則，未將
距離自動轉換為調整率，符合使用者明確之範圍限制。

**本輪至此停止，不自行開始TGOS/NLSC/Phase API-2.1/ComparableProvider/
Multi-Jurisdiction Runtime Rules。**

---

# Phase API-2.1 — Facility Coordinate Provenance & Spatial Scope Hardening

Freeze前最後一輪硬化，處理Phase API-2遺留之4項議題：target coordinate
provenance、cross-district搜尋、STATION涵蓋語意、distance method命名。

## 17. Target Coordinate Provenance Audit（§2）

完整追蹤實際runtime路徑（非僅檢查`OfficialFacilityProvider`本身是否
import Nominatim）：

```
Input (body/meta)
  → backend/handlers/collect_data.py::_resolve_center_coordinate_evidence()
      分支1: body["center_coordinate"]（呼叫端提交）
      分支2: DATA_PROVIDER_MODE≠real → None
      分支3: DATA_PROVIDER_MODE=real 且無提交座標 → geocode(place)（Nominatim）
  → ProviderContext.center_coordinate + center_coordinate_evidence（本輪新增）
  → OfficialFacilityProvider.query_facility()
```

`CENTER_COORDINATE_SOURCE_AT_RUNTIME`：

| 可能來源 | 是否為實際可達路徑 | 說明 |
|---|---|---|
| submitted coordinate | **是**，但僅限直接API呼叫（非經前端UI） | 前端`frontend/`原始碼中從未實際送出`center_coordinate`欄位（已grep確認），故此路徑理論上可達、實務上非現行前端之路徑；即使可達，伺服器仍無法驗證其原始來源，標記`EXTERNAL_UNVERIFIED` |
| 官方GIS | **否**，不存在此路徑 | 本專案目前無任何官方地號級座標登記來源會寫入`ctx.center_coordinate` |
| Nominatim | **是**，real mode下唯一實際會觸發之fallback | `osm_facility_lookup.geocode()`，僅達區/段文字geocode精細度 |
| demo centroid | **否**，不存在此路徑 | `transportation_provider.py`之`JINSHAN_DISTRICT_CENTROID`僅用於該模組自身未被呼叫之`demo_geo_engine_usage()`，從未寫入`ctx.center_coordinate` |
| other/UNKNOWN | mock mode或無座標時 | 回傳`None` |

## 18. 禁止Unofficial Coordinate被誤標官方（§3-5）

新增`domain/models.py::TargetCoordinateEvidence`（additive，見§4）與
`FacilityEvidence.distance_authoritative_status`／`official_distance_
ready`。判定規則：

```
無可計算距離 → distance_authoritative_status = "UNKNOWN"
有可計算距離 且 target_coordinate_evidence.authoritative_status == OFFICIAL
    → "OFFICIAL"，official_distance_ready = True
有可計算距離 但 target座標非OFFICIAL（EXTERNAL_UNVERIFIED/DEMO_ONLY/UNKNOWN）
    → "MIXED_SOURCE"，official_distance_ready = False
```

`ProviderContext`新增一個additive欄位`center_coordinate_evidence`（未大規模
refactor——建構子簽章僅新增一個具預設值之具名參數，既有呼叫端不受影響）。
`collect_data.py`於`_resolve_center_coordinate_evidence()`每個分支皆標註
其provenance，取代原本「回傳裸座標、來源匿名」之設計。

**Golden Case驗證**：即使假設性地為Golden Case提供一個demo/Nominatim座標
（真實地號本身仍無官方座標——見§5），`official_distance_ready`仍正確回報
`False`，`distance_authoritative_status`為`MIXED_SOURCE`，不因座標非None
就誤標官方：

```
Golden Case (demo coord): match_count=472 nearest=私立法鼓山僧伽大學
distance_authoritative_status=MIXED_SOURCE official_distance_ready=False
```

`GOLDEN_CASE_OFFICIAL_DISTANCE_READY = NO`（本輪維持與Phase API-2相同
結論，且本輪新增之欄位提供更明確、無法被誤讀之訊號）。

## 19. Cross-District Spatial Search（§6-7）

`lookup_facilities()`早已支援`district=None`（不限制行政區），但
`OfficialFacilityProvider`先前一律傳入`ctx.district`作為硬性篩選。本輪
修正：當查詢座標可用**且**設施類型具座標（SCHOOL/STATION）時，改為
`district=None`（全新北市範圍搜尋），僅在查詢座標不可用時才以行政區作為
「顯示用之選擇性優化」。MARKET/PARK（恆無座標）維持行政區篩選（無距離
排序語意可被扭曲）。

**Boundary Test（§7，真實地理距離驗證）**：

```
Facility A（同區，~2500m）vs Facility B（鄰區，~180m）
結果：nearest = Facility B（正確跨區找到最近設施，未因行政區不同排除）
```

**真實資料驗證**（demo座標，非聲稱官方）：

```
STATION with demo coord (金山區query): match_count=142（全新北市，非僅金山區之0筆）
  nearest=淡金鄧公站(淡海輕軌綠山線) district=淡水區 distance_m=17428.85 subtype=MRT
```

金山區本身無捷運/火車站，若維持行政區硬性篩選將永遠回傳0筆——本輪修正後
正確地在全新北市範圍內找到最近站點（雖距離達17.4公里，此為金山區地理
位置之真實反映，非bug）。

## 20. Most Impactful vs Nearest（§8）

`nearest`維持為「distance convenience evidence」，**非**
`selected_appraisal_facility`。`FacilityEvidence`/`FacilityMatch` model
中確認無此類易誤導之欄位命名（`test_nearest_field_is_not_named_or_
documented_as_selection`鎖定），docstring明確加註「DISTANCE CONVENIENCE
POINTER ONLY」。`matches`持續保留全部候選，符合作業手冊p.24「由查估人員
判斷」之要求。

## 21. STATION Coverage Audit（§9-10）

對「新北市重要地標資訊」完整29類`地標類型`重新audit（見Phase API-2§3A
已列出之完整清單），確認：**不存在**高鐵、客運站、轉運站類別。另以
NTPC完整1,747個資料集清單搜尋「高鐵」「客運站」「轉運站」「輕軌」「客運
轉運」「轉運中心」「長途客運」等關鍵字，**零筆命中**——新北市OpenData
平台上不存在此類獨立資料集。

**意外正面發現**：「輕軌」（淡海輕軌／安坑輕軌）車站**確實存在**於資料中，
但未被來源資料集獨立分類，而是與一般捷運站共用同一`地標類型=捷運站`
標籤（實測：23筆站名含「輕軌」字樣之紀錄，`source_category`皆為
"捷運站"）。由於來源本身未做此區分，本專案`facility_subtype`之"MRT"
bucket為粗粒度標籤，未強行以站名字串比對方式猜測細分（避免無官方分類
依據之臆測），`source_category`忠實保留原始"捷運站"字串供人工判讀。

```
STATION_DATA_READY = PARTIAL
```

（涵蓋捷運/輕軌/火車，缺高鐵與長途客運轉運站；本輪未因此接入TGOS/NLSC/
OSM填補缺口，誠實維持PARTIAL。）

## 22. Station Subtype（§10）

`FacilityMatch`新增additive欄位`facility_subtype`（本專案粗粒度分類：
`ELEMENTARY`/`JUNIOR_HIGH`/`COMPLETE_SCHOOL`/`SENIOR_HIGH`/`COLLEGE`/
`MRT`/`TRA`）與`source_category`（**逐字保留**來源官方分類字串，例如
"國民小學"/"捷運站"）。normalize為粗類別（SCHOOL/STATION）後，原始細
分類**不再遺失**（`test_station_subtype_preserved_through_sync`鎖定）。

## 23. Distance Method Semantics（§11-12）

`FacilityDistanceMethod.GEODESIC_WGS84`重新命名為`HAVERSINE_WGS84`
（技術實作確實是Haversine great-circle公式，"geodesic"一詞可能涵蓋更廣
之橢球面演算法，本專案未實作，故用更精確之命名，避免overclaim）。新增
`FacilityDistanceSemantics`列舉，`STRAIGHT_LINE_REFERENCE`／
`NOT_APPLICABLE`兩值，明確標示此距離僅為「直線參考距離」，絕不可能被
誤稱為`ROUTE_DISTANCE`／`WALKING_DISTANCE`（`test_distance_semantics_
never_labeled_route_or_walking`結構性鎖定列舉本身不存在該等值）。

Distance Evidence仍維持corroborating/reference性質，即使作業手冊對學校/
市場等通達型設施建議路線距離，本輪Haversine結果**不得**自動套入grade
門檻，待未來量測標準contract正式確認後方可考慮（見§8/Phase API-2§12
之既有原則，本輪未變更）。

## 24. CRS Evidence（§13）

再次搜尋官方文件：一般TWD97/TM2/EPSG:3826標準文件（epsg.io、OSGeo wiki、
TGOS API文件）確認EPSG:3826即為台灣標準TWD97/TM2 121分帶定義，且TGOS
平台本身明確提供"TWD97 (EPSG:3826)"作為座標選項；但**直接查詢新北市
重要地標資訊資料集本身之NTPC頁面**（`https://data.ntpc.gov.tw/datasets/
6dcff24a-838c-40fb-a9df-f1160afafe84`），**未**發現該資料集自身之metadata
明確聲明其twd97_x/twd97_y欄位採用TWD97 TM2 121分帶／EPSG:3826（僅列出
欄位名稱，無座標系統說明文字；已以WebFetch直接查詢確認）。

**誠實結論**：不寫成「欄位名稱本身明確證明EPSG:3826」（欄位名稱本身確實
未必完整表達分帶/EPSG code，使用者提醒正確）。综合證據強度：

```
CRS_VERIFICATION = STRONGLY_CORROBORATED
```

（支持證據：欄位命名慣例、新北市地理範圍下數值量級與TM2-121分帶東距/
北距預期範圍吻合、pyproj轉換後座標與已知真實地點交叉比對正確；缺少：
資料集發布方自身明確聲明CRS之官方文件。未達`VERIFIED_OFFICIAL`門檻。）

## 25. Snapshot Metadata改善（§14）

`facility_dataset_cache.py`之`snapshot_meta`新增additive欄位
`source_record_count`/`mapped_record_count`/`skipped_record_count`
（`record_count`維持原意＝stored count，不重新定義）。`FacilityEvidence`
新增`source_record_count`/`stored_record_count`，`record_count`標記為
DEPRECATED別名以維持既有讀者相容。真實同步結果：

```
landmark: source_record_count=2056 mapped_record_count=614 skipped_record_count=1442
新北市公有市場及超市清冊: source_record_count=48 total_record_count=48
新北市公園: source_record_count=635 total_record_count=635
```

不再讓單一`record_count`（614）被誤讀為「官方資料集原始僅614筆」。

## 26. Regression + Freeze複查

- `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
  **688 passed**，0 failed（較Phase API-2的674項，新增14項Phase API-2.1
  測試）。
- Core Freeze 36檔案SHA-256：BEFORE=AFTER=
  `0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  ——**`CORE_FREEZE_VIOLATION = NO`**。
- Cadastral Pipeline 6檔案SHA-256組合雜湊：本輪前後皆為
  `cec5f900b8daf50dd3289e35f3f28a45b7dc6c51f6c7e079e7c142367c4bdc04`
  ——**`CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO`**（本輪零次Write/Edit
  觸及`cadastral_identifier.py`/`cadastral_dataset_cache.py`/
  `expropriation_case_provider.py`/`land_price_provider.py`/
  `sync_expropriation_dataset.py`/`sync_land_price_dataset.py`）。

## 27. Phase API-2.1 Release Gate

> **Phase API-2.2訂正**：下方`NOMINATIM_CONTAMINATION_RISK = NO`一項，
> 語意易被誤讀為「Nominatim已從runtime移除」，**並非事實**——Nominatim
> 在`DATA_PROVIDER_MODE=real`且呼叫端未提交座標時，**仍是唯一實際會
> 觸發之座標fallback路徑**（見Phase API-2.1§17
> `CENTER_COORDINATE_SOURCE_AT_RUNTIME`）。Phase API-2.1修正的是「Nominatim
> 產生的座標不得被誤標為官方」，而非移除Nominatim本身。正確訂正為：
>
> ```
> NOMINATIM_OFFICIAL_MISLABELING_RISK = NO
> NOMINATIM_REFERENCE_DISTANCE_PATH = PRESENT
> ```
>
> 說明：Nominatim路徑依然存在（`NOMINATIM_REFERENCE_DISTANCE_PATH =
> PRESENT`），且此路徑產生之座標所計算出的距離，現在**正確地**被標示為
> `distance_authoritative_status = MIXED_SOURCE`、
> `official_distance_ready = False`（`NOMINATIM_OFFICIAL_MISLABELING_
> RISK = NO`，即「不會被誤標為官方」，而非「Nominatim不存在」）。下方
> Phase API-2.1原始記錄之`NOMINATIM_CONTAMINATION_RISK = NO`保留供追溯，
> 但正式報告請一律引用上述訂正後之兩個欄位。

```
TARGET_COORDINATE_PROVENANCE = PASS
NOMINATIM_CONTAMINATION_RISK = NO   # 見上方Phase API-2.2訂正
CROSS_DISTRICT_SEARCH = PASS
STATION_COVERAGE = PARTIAL
STATION_SUBTYPE_PRESERVED = PASS
DISTANCE_METHOD_SEMANTICS = PASS
CRS_VERIFICATION = STRONGLY_CORROBORATED
SOURCE_VS_STORED_COUNT_SEPARATED = PASS
GOLDEN_CASE_OFFICIAL_DISTANCE_READY = NO
CORE_FREEZE_VIOLATION = NO
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 688 passed, 0 failed
PHASE_API_2_1_RELEASE_READY = YES
```

本輪未進行TGOS/NLSC整合、未新增OSM/Nominatim呼叫（僅為既有Nominatim
呼叫結果補上provenance標籤，未新增呼叫次數或路徑）、未使用OSRM/Google
Directions、未修改Rule/Grade/Adjustment/Calculation Engine、未修改已凍結
Cadastral Pipeline，符合使用者明確之範圍限制。

**本輪至此停止，不自行開始TGOS/NLSC。**
