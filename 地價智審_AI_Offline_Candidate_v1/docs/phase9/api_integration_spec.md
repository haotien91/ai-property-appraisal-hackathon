# Phase 9 — API Integration Spec（Phase API-1 + Phase API-1.5）

> 對應「Multi-Jurisdiction Official Data + API Integration Audit」批准之
> Phase API-1 修正版範圍：`ExpropriationCaseProvider` + `LandPriceProvider`。
> 第三個Facility API本輪未實作（Audit已載明理由：既有公車/停車場資料並非
> 查估手冊「學校/市場/公園/車站/商圈」五類接近條件的完整官方替代來源）。
>
> **⚠️ 本文件§3與§8第3項之原始內容已被Phase API-1.5發現並修正為錯誤——
> 見文件末「Phase API-1.5」一節之「重大修正發現」。district filter
> 實際上並非如原文所述真正生效，而是完全無效（no-op）。原文保留於下方
> 僅供沿革記錄，請以文件末最新結論為準。**

## 1. API Source

### 1.1 新北市已公告徵收案件地籍資料

- **官方權責機關**：新北市政府地政局
- **dataset_id**：`DD9C0006-8FAD-450D-8C7B-E59240B0ED13`
- **官方頁面**：`https://data.ntpc.gov.tw/datasets/DD9C0006-8FAD-450D-8C7B-E59240B0ED13`
- **API endpoint**：`https://data.ntpc.gov.tw/api/datasets/DD9C0006-8FAD-450D-8C7B-E59240B0ED13/json`
- **資料量**：約9,437筆（92年以後）
- **官方描述之涵蓋範圍限制**：僅92年以後內政部核准之一般徵收案件，**不含更正及撤銷徵收**，**不含區段徵收**
- **認證**：免認證，公開存取

### 1.2 新北市公告土地現值（114年）

- **官方權責機關**：新北市政府地政局
- **dataset_id**：`826870ef-4ea5-48bf-915b-e0a33158cf06`
- **官方頁面**：`https://data.ntpc.gov.tw/datasets/826870ef-4ea5-48bf-915b-e0a33158cf06`
- **API endpoint**：`https://data.ntpc.gov.tw/api/datasets/826870ef-4ea5-48bf-915b-e0a33158cf06/json`
- **資料量**：約1,153,450筆
- **認證**：免認證，公開存取
- **重要**：113年另有一個獨立dataset_id（未於本輪驗證schema），本輪僅收錄114年欄位對照

## 2. Schema（實測，非文件宣稱）

| Dataset | district | segment | 地號欄位 | 現值/其他欄位 |
|---|---|---|---|---|
| 徵收案件 | `district` | `segment` | `id` | `sus_year`（公告年度）、`pro_name`（工程名稱） |
| 公告土地現值114年 | `district` | `segment` | `lid` | `official_value_busiprval`（公告土地現值）。**確認無「公告地價」欄位** |

## 3. 本輪實作過程中新發現、已修正Audit報告的重大限制（誠實記錄）

上一輪Audit的background research agent僅測試了單一欄位filter（`district eq X`）即回報VERIFIED_AVAILABLE。**本輪實作時以真實API直接測試compound filter，發現以下此前未被發現的平台限制**：

- **`filter`參數只有`district eq ...`這一個欄位是真正被伺服器端honored的**。`segment eq ...`、`id eq ...`、`lid eq ...`——不論單獨使用或以`and`組合——**皆被伺服器端完全忽略**，回傳的是未經該欄位過濾的原始順序資料。此結論以三組獨立測試證實：
  1. `filter=district eq 蘆洲區 and segment eq 保新段 and id eq 1510000`（真實存在的組合）與只用`district eq 蘆洲區`回傳**完全相同**的前20筆資料（未收斂到單一筆）
  2. `filter=district eq 板橋區 and segment eq 不存在ZZZ and lid eq 00000000`（不可能存在的組合）仍回傳5筆資料，證明segment/lid完全未被過濾
  3. `filter=id eq 9999999999`（單獨使用，無district）回傳的仍是資料集最前面幾筆，證明並非"AND失效"而是"該欄位從未被讀取"
- **`page`參數為0-indexed**：`page=0`與省略`page`回傳相同的第一頁；`page=1`已經是第二頁。此為程式實作時透過直接請求驗證，非文件宣稱。

**因此本Provider的實際查詢策略**：僅以`district eq {district}`向伺服器端過濾（唯一真正生效的欄位），再以`page`分頁掃描該行政區之全部紀錄，`segment`/地號欄位完全在**用戶端**逐筆精確比對。這比原Audit報告設想的「送出compound filter並防禦性驗證」更保守、更誠實。

## 4. Provider Behavior

### 4.1 ExpropriationCaseProvider

- 掃描上限：`_PAGE_SIZE=200` × `_MAX_PAGES=10` = 最多2000筆／行政區
- 掃描完畢（最後一頁筆數<200）且無比對命中 → `NOT_FOUND_IN_DATASET`（附完整涵蓋範圍限制說明，不宣稱「確定未徵收」）
- 命中筆數上限但仍未掃描完 → `UNKNOWN`（無法確認，非猜測）
- 實測發現：部分行政區（如蘆洲區，因單一大型捷運/道路徵收案含極多宗地）**超過2000筆掃描上限**，會誠實回報`UNKNOWN`而非錯誤地宣稱`NOT_FOUND_IN_DATASET`——此為本Provider防禦設計的直接證明，見下方「真實API request example」

### 4.2 LandPriceProvider

- 同樣機制（district過濾＋分頁掃描），但**此dataset單一行政區可能達數萬筆**（全市115萬筆／約29區），遠超2000筆掃描上限，**大量查詢會誠實回報`UNKNOWN`（截斷）而非成功比對**。此為本輪已知、已揭露之限制（見「Known limitations」）
- DatasetVersion→SchemaAdapter：`_YEAR_SCHEMAS`字典，本輪僅收錄"114"一個已驗證年度；任何其他年度直接回報`UNKNOWN`，不猜欄位名稱

## 5. Mock Behavior

兩個Provider之Mock版本**完全不呼叫網路**，且因Golden Case（查估書表範本.pdf）本身無對應之官方徵收案件比對值/公告現值數值可供Mock，**誠實回傳UNKNOWN**而非虛構一個FOUND/NOT_FOUND狀態或金額（符合provider-contract skill「Mock值必須可追溯至實際官方來源，不得憑空捏造」原則）。

## 6. Failure Behavior

| 情境 | 結果 |
|---|---|
| 案件缺少district/segment/地號（含`base_parcel_id`預設值"TBD"） | `UNKNOWN`，不呼叫網路 |
| HTTP逾時/連線失敗/非200 | `UNKNOWN`，`requires_manual_review=True` |
| JSON解析失敗/非list回應 | `UNKNOWN`（`_http_get_json`統一回傳None） |
| 掃描上限內查無比對 | `NOT_FOUND_IN_DATASET`（徵收）／`NOT_FOUND_FOR_PARCEL`（現值），附涵蓋限制說明 |
| 掃描上限已達但未確認涵蓋完整 | `UNKNOWN`（截斷，非猜測） |
| Real Mode下任何失敗 | **絕不**silent fallback至Mock Provider（以`test_real_provider_never_falls_back_to_mock_on_failure`鎖定） |

## 7. Evidence Contract

`ExpropriationCaseEvidence`／`LandPriceEvidence`（`domain/models.py`新增，皆`extra="forbid"`）：

- `status` / `announced_land_current_value_status` / `announced_land_price_status`：三態或四態，永遠不得混淆「查無資料」與「此年度schema本無此欄位」
- `dataset_id` / `dataset_name` / `source_authority` / `source_url` / `retrieved_at` / `query_parameters`：完整provenance，`query_parameters`區分`server_filter.*`（實際送出且生效之過濾條件）與`client_match.*`（用戶端額外比對之欄位）
- `authoritative_status`：`OFFICIAL_OPEN_DATA` / `OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE`
- `requires_manual_review`：**恆為True**——本輪明確定位為external corroborating evidence only，任何結果都不構成最終法律結論
- `local_snapshot_version` / `checksum`（僅`LandPriceEvidence`）：本輪恆為`None`（本Provider為Runtime Query，非DatasetRegistry管理之本地snapshot，不得假造這兩個欄位）

## 8. Known Limitations（誠實揭露，非隱藏）

1. **LandPriceProvider非Audit報告原先建議之SCHEDULED_SYNC/VERSIONED_SNAPSHOT架構**，而是有界的per-case Runtime Query。這是本輪「只做最小必要修改」下的刻意取捨：完整同步115萬筆資料之pipeline超出本輪範圍。後果是：對於掃描順序中排序較後的大型行政區地號，查詢經常誠實回報`UNKNOWN`（截斷）而非確認值——這是揭露的已知限制，不是隱藏的bug。
2. **公告地價（非現值）本輪完全未實作讀取**——僅114年schema已驗證且該年度本身不含此欄位；其他年度schema皆未驗證，一律`UNKNOWN`。
3. **compound filter不work**是本輪實作時才發現、上一輪Audit報告未能發現的平台限制，已更新至此文件；上一輪報告中「VERIFIED_AVAILABLE」的判定本身仍成立（district filter確實生效），但原報告隱含假設的「可用filter做精確地號查詢」需要修正為「僅能靠分頁掃描+用戶端比對」。
4. 兩個Provider皆未整合進`RuleEngine`/`AuditEngine`/`CalculationEngine`——僅作為`FACTORS.points`（NormalizedDataPoint）與`FACTORS.expropriation_case_evidence`／`FACTORS.land_price_evidence`（完整evidence物件）持久化，供未來人工查閱或後續輪次接入使用。

## 9. 真實 API Request/Response Example（去除大量資料，僅節錄）

### 徵收案件（命中）

Request：
```
GET https://data.ntpc.gov.tw/api/datasets/DD9C0006-8FAD-450D-8C7B-E59240B0ED13/json?filter=district+eq+%E8%98%86%E6%B4%B2%E5%8D%80&size=200&page=0
```

Response（節錄第一筆）：
```json
[
  {"sus_year": "2003", "pro_name": "台北都會區大眾捷運系統蘆洲支線(捷一、捷二、捷三、捷四)",
   "district": "蘆洲區", "segment": "保新段", "id": "1510000"},
  ...
]
```

Evidence（本Provider產出）：
```json
{
  "status": "FOUND_IN_DATASET",
  "district": "蘆洲區", "segment": "保新段", "land_no": "1510000",
  "announcement_year": "2003",
  "project_name": "台北都會區大眾捷運系統蘆洲支線(捷一、捷二、捷三、捷四)",
  "dataset_id": "DD9C0006-8FAD-450D-8C7B-E59240B0ED13",
  "source_authority": "新北市政府地政局",
  "authoritative_status": "OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
  "requires_manual_review": true,
  "notes": "此地號於官方「已公告徵收案件地籍資料」中有相符紀錄，但本資料集僅涵蓋92年以後一般徵收案件（不含更正及撤銷徵收、不含區段徵收），此結果僅供人工覆核之佐證，不構成最終法律結論。"
}
```

### 公告土地現值（命中）

Request：
```
GET https://data.ntpc.gov.tw/api/datasets/826870ef-4ea5-48bf-915b-e0a33158cf06/json?filter=district+eq+%E6%9D%BF%E6%A9%8B%E5%8D%80&size=200&page=0
```

Response（節錄第一筆）：
```json
[{"country": "新北市", "district": "板橋區", "segment": "忠孝段", "lid": "00010000", "official_value_busiprval": "278000"}, ...]
```

Evidence：
```json
{
  "district": "板橋區", "segment": "忠孝段", "land_no": "00010000", "dataset_year": "114",
  "announced_land_current_value": "278000", "announced_land_current_value_status": "AVAILABLE",
  "announced_land_price": null, "announced_land_price_status": "FIELD_NOT_AVAILABLE_FOR_YEAR",
  "authoritative_status": "OFFICIAL_OPEN_DATA", "requires_manual_review": true
}
```

### API unavailable（模擬逾時）

```json
{
  "status": "UNKNOWN",
  "notes": "呼叫新北市OpenAPI失敗或逾時，無法確認查詢結果，非查無資料。",
  "requires_manual_review": true
}
```

---

# Phase API-1.5 — Cadastral Identifier Normalization + Official Dataset Snapshot Stabilization

## 10. 目的

修正Phase API-1的兩個根本問題：(1) 誤將`price_segment_code`（如"P002-00"）
當作官方資料集之地籍段名查詢；(2) 以有界live pagination scan作為主要查詢
策略，導致大型行政區經常誠實但無用地回報`UNKNOWN`。

## 11. 重大修正發現：district filter 其實也是完全無效（superseding §3/§8-3）

Phase API-1報告（本文件§3、§8第3項）曾記載「`filter=district eq ...`
確實生效，只有segment/id/lid被忽略」。**Phase API-1.5在實作過程中發現此
結論本身就是錯的**：

- 嘗試對`LandPriceProvider`做「僅同步金山區」時，同步筆數不正常地持續
  超過144,000筆仍未停止（金山區實際不可能有這麼多公告土地現值紀錄）
- 直接檢查第700頁內容，回傳的`district`欄位全部是「板橋區」，而非
  請求的「金山區」
- 進一步用最小可重現測試直接比對三種請求：`filter=district eq 板橋區`、
  `filter=district eq 金山區`、完全不帶filter——**三者從第0頁開始回傳
  逐位元組完全相同的資料**
- 對徵收案件資料集重複相同測試，同樣證實：`filter=district eq 蘆洲區`
  與`filter=district eq 板橋區`（一個完全不會出現在結果中的行政區）與
  無filter，三者結果相同

**結論：兩個資料集的`filter`參數對`district`欄位同樣是完全無效（no-op），
不只是先前發現的segment/id/lid。** Phase API-1的「驗證」之所以「看起來
成功」，純粹是因為測試時剛好選到「蘆洲區」（徵收案件）與「板橋區」
（公告土地現值）——這兩個行政區恰好是各自資料集在原始儲存順序中的
**第一個**行政區，使一個完全不做任何過濾的參數，看起來像是「篩選對了」。

**影響**：
- `ExpropriationCaseProvider`的完整同步（`scripts/sync_expropriation_
  dataset.py`）**不受影響**——該script從一開始就沒有用任何filter，是
  對全資料表做無filter的完整分頁，因此其9,437筆同步結果依然正確、完整。
- `LandPriceProvider`原本設計的「per-district filter同步」**架構上不成立
  ，已重新設計**為單次無filter完整分頁、依每筆紀錄自身的`district`欄位
  分桶寫入各行政區snapshot（見§13）。
- 兩個Provider的`query_*_via_live_scan`備援方法，原本假設「至少district
  有被過濾」，**已修正**為在用戶端也重新驗證`district`（見程式碼），並
  在文件中明確標註：在filter完全無效的現實下，此備援方法實質上只能對
  「剛好排在原始資料表最前面的行政區」有意義，其餘行政區會在掃描上限內
  持續回報`truncated`→`UNKNOWN`。

## 12. Cadastral Parcel Identifier Contract

`providers/cadastral_identifier.py`。核心分離：

| 概念 | 範例 | 來源欄位 | 用途 |
|---|---|---|---|
| `price_segment_code`（地價區段代碼） | `P002-00` | `ProviderContext.segment_code` | 本專案內部地價區段劃分，**不得**用於查詢政府開放資料 |
| `section_name`（地籍段名） | `金美段` | 解析自`ProviderContext.parcel_id`（即`base_parcel_id`） | 查詢政府開放資料集之`segment`欄位用 |

`CadastralParcelIdentifierParser.parse(raw_input)`回傳`CadastralParcelIdentifier`：
`section_name` / `subsection_name` / `land_no_raw` / `land_no_main` /
`land_no_sub` / `parse_status`（`PARSED`/`PARSE_UNCERTAIN`）/
`requires_manual_review` / `notes` / `raw_input`（原始字串恆保留）。

Regex要求`section`群組必須含字面「段」字——任何不含「段」的字串（包含
`price_segment_code`本身，如"P002-00"）**在正規表達式層級就不可能**被
解析為`section_name`，非執行期額外檢查，是結構性防呆。

已驗證案例：
- `金美段489地號` → section=金美段, main=489, sub=0
- `頂溪段一小段489之2地號` → section=頂溪段, subsection=一小段, main=489, sub=2
- `P002-00` → PARSE_UNCERTAIN（不含「段」）

## 13. Land Number Normalization（實測驗證，非假設）

`providers/cadastral_identifier.py::LandNumberNormalizer`。**兩個資料集
格式不同**，分別實測驗證：

| 資料集 | 格式 | 驗證方式 |
|---|---|---|
| 公告土地現值 `lid` | 主號4碼零填補 + 子號4碼零填補（共8碼） | 實際擷取5,600筆真實紀錄（7個行政區），**全數**恰為8碼，主號最大觀察值814仍以4碼零填補呈現，0筆例外 |
| 已公告徵收案件 `id` | 主號**不填補**（原始位數）+ 子號4碼零填補 | 實際擷取3,000筆真實紀錄（5個行政區）；長度分佈5/6/7/8碼皆有，且同一段內多筆紀錄呈現「固定字首＋4碼遞增字尾」規律（例如字首"214"搭配16組不同3-4碼子號0019~0074），證實主號不補零、子號固定4碼 |

`LandNumberNormalizer.to_land_price_lid(main, sub)` / `.to_expropriation_id(main, sub)`
——兩個獨立方法，**永不共用**格式假設。main/sub超出已驗證範圍
（>9999）或為負數/None時回傳`None`（UNKNOWN），不外插猜測。

## 14. Snapshot Architecture（取代Runtime Pagination作為主要策略）

`providers/cadastral_dataset_cache.py`——獨立於既有`DatasetRegistry`
之新SQLite store（理由：`DatasetRegistry`為`dataset_id`單一PK設計，
不支援本輪per-jurisdiction/per-district scope_key之需求；為避免動到
`RealNtpcZoningProvider`已依賴之既有schema，另建一個小型、同原則
（sqlite3、checksum、CURRENT-only）的平行store）。

Schema：`snapshot_meta`（`dataset_id`+`scope_key`複合PK，含
`dataset_name`/`dataset_year`/`source_url`/`source_authority`/
`downloaded_at`/`source_last_modified`/`checksum_sha256`/`record_count`/
`schema_version`/`local_path`）+ `expropriation_records`/`land_price_records`
（含索引）。

**兩個sync腳本，皆為filter-free完整分頁**（因§11發現filter完全無效，
唯一可靠做法就是不依賴filter，完整讀取後用戶端依實際`district`欄位分桶）：

- `scripts/sync_expropriation_dataset.py`：單次完整同步（無filter，
  ~9,437筆，scope_key固定為`SCOPE_ALL`）。**已執行完成**：
  `record_count=9437`，`checksum_sha256=5cfadda70f5c5b08c88d4c2b95c12a7d540b5fa0a70a47664f424129b8df670a`
- `scripts/sync_land_price_dataset.py`：單次完整同步（無filter，
  ~1,153,450筆，依每筆`district`欄位分桶寫入各自的scope_key）。**本輪
  執行狀態見§15**。

Provider之`query_case()`/`query_land_price()`（主要路徑）**只讀本地
snapshot，從不呼叫網路**；`query_case_via_live_scan()`/
`query_land_price_via_live_scan()`（備援路徑）維持獨立命名、**永不被
主要路徑自動呼叫**（`test_primary_path_never_invokes_live_scan`鎖定）。

## 15. Golden Case Re-test（金山區／金美段／489地號）

```
PARSED_IDENTIFIER = section_name="金美段", subsection_name=None, land_no_main=489, land_no_sub=0
NORMALIZED_IDENTIFIER = expropriation_id="4890000"（主號不補零+子號4碼）, land_price_lid="04890000"（主號4碼+子號4碼）
LOOKUP_KEY = (dataset_id, district="金山區", section_name="金美段", normalized_land_no)

EXPROPRIATION_RESULT = NOT_FOUND_IN_DATASET
  （已對9,437筆完整同步之snapshot查詢，checksum=5cfadda7...，record_count=9437，
    確認「金山區」轄下無「金美段」489地號之相符徵收公告紀錄。
    依資料集涵蓋限制（僅92年後一般徵收，不含更正/撤銷/區段徵收），
    此為誠實的「查無」而非「確定未徵收」。）

LAND_PRICE_RESULT = FOUND（AVAILABLE）
  announced_land_current_value = 57305（元/M2）
  announced_land_current_value_status = AVAILABLE
  announced_land_price_status = FIELD_NOT_AVAILABLE_FOR_YEAR（114年schema本無此欄位，非查無資料）
  checksum_sha256 = f89163463a4a98186b9c8ce446bf4c3dbd6748b67fcc0a108b8b4ebd82a0c97b
  record_count = 25361（「金山區」完整同步筆數）
  （來自scripts/sync_land_price_dataset.py對114年公告土地現值資料集之全量
    同步、依「金山區」分桶之snapshot，非runtime pagination）
```

**此為本輪最具體的正面驗證**：系統成功以「金山區」+「金美段」+「489地號」
（正確解析自`base_parcel_id`，全程未使用`price_segment_code`）查得
Golden Case比準地之真實官方公告土地現值57,305元/M²，並附完整checksum/
record_count provenance可供人工覆核來源可靠性。

---

# Phase API-1.6 — Snapshot Finalization & Release Gate

## 16. Land Price Full Snapshot：最終完成

`scripts/sync_land_price_dataset.py`（含checkpoint+retry修正版）完整執行
成功，**29/29行政區**，總筆數**1,153,450**，與資料集官方metadata筆數完全
吻合，程序正常結束（exit code 0，`reached_end=True`）。過程中修正了一項
本身的bug：原始版本僅在最後才寫入snapshot，中途一次網路連線中斷
（`WinError 10054`，發生於約92萬筆處）導致該次執行全部進度未落地；
已加入每250頁checkpoint機制與重試邏輯後，第三次執行順利完成。

## 17. Bulk Download Audit（實測，非僅查看dataset頁面）

對dataset_id`826870ef-4ea5-48bf-915b-e0a33158cf06`實際發出請求測試：

| Endpoint | 結果 |
|---|---|
| `.../csv`（無size參數） | HTTP 200，但預設僅回傳30筆（與`/json`同樣受`size`/`page`分頁機制約束，非自動完整匯出） |
| `.../xml`（無size參數） | HTTP 200，`Content-Type: application/xml`，同樣受分頁約束 |
| `.../zip` | HTTP 400 Bad Request——**不存在**此bulk zip端點 |
| `.../json?size=100000` | **HTTP 200，實際回傳100,000筆，耗時4.91秒，12.4MB** |
| `.../json?size=200000` | 成功，200,000筆，9.79秒，24.6MB |
| `.../json?size=500000` | 成功，500,000筆，23.28秒，62.3MB |
| `.../json?size=1200000` | **WAF拒絕**（F5-style「Request Rejected」，非官方文件所述限制，屬平台防護層攔截） |
| `.../csv?size=500000` | 成功，500,000筆，13.47秒，**29.3MB**（同筆數下比JSON更省流量） |

**重大發現：`size`參數並非文件宣稱或本輪先前假設的200筆上限**——伺服器
實際可接受遠大於200的`size`值（至少到500,000皆成功），僅在極端值
（≥約1,200,000）遭WAF攔截。這代表**本輪已完成之5,750次×200筆分頁請求
方式，並非最有效率的作法**。

### A（5,750次分頁 size=200） vs B（單次/少量大size請求）比較

| 面向 | A：現行5,750次×200筆 | B：3次×500,000筆（或12次×100,000） |
|---|---|---|
| Request count | ~5,750 | 3（或12） |
| Total bytes | 未逐一加總，但每頁含完整JSON鍵名重複開銷 | 同總筆數下bytes相近或更低（CSV格式更省） |
| Speed | 實測約35-50分鐘（含0.15秒/頁禮貌延遲＋重試） | 3次×500,000筆實測外插約70秒（3×23.3秒），**快約30-40倍** |
| Retry complexity | 需對5,750次請求個別重試，checkpoint機制才能避免全部重來 | 僅需對2-3次大請求重試，複雜度大幅降低 |
| Checksum reproducibility | 已發現的排序/欄位問題（見下§18）影響同樣存在，與請求次數無關 | 相同 |
| Server load | 對公開政府API發出5,750次請求，即使有禮貌延遲仍屬較高負擔 | 對伺服器僅2-3次大型查詢，負擔顯著更低，更符合公開API使用禮節 |
| Implementation complexity | 需完整分頁迴圈＋checkpoint機制（已實作） | 更簡單：迴圈2-3次＋合併結果即可 |
| Demo/deploy stability | 長時間執行（30-60分鐘），中途失敗風險視窗大（已實際發生一次） | 短時間執行（<2分鐘），失敗視窗小，更適合Demo/CI環境 |

**結論：B方案明顯較優。** 建議下一輪（非本輪，因本輪禁止implementation
變更除非「非常小的非核心變更」）將`scripts/sync_land_price_dataset.py`
與`scripts/sync_expropriation_dataset.py`之分頁邏輯改為使用
`size=200000~500000`區間之大分頁（保留低於WAF門檻之安全邊際），
可將兩個sync腳本的執行時間從數十分鐘降至數分鐘內，同時降低對公開
政府API的請求次數負擔近99%。**本輪僅提出此migration plan，未變更
現行`_PAGE_SIZE=200`實作**（現行200筆分頁本身無正確性問題，僅是效率
議題，符合本輪「先不要改implementation」之指示）。

## 18. Snapshot Integrity 稽核結果

對全部29個行政區、1,153,450筆已同步紀錄逐筆檢查：

| 檢查項 | 結果 |
|---|---|
| snapshot_meta必要欄位完整性（dataset_id/dataset_year/scope_key/record_count/checksum_sha256/downloaded_at/schema_version/source_url/source_authority/dataset_name） | **全數完整，0筆缺漏** |
| Duplicate rows（同一district內(district,segment,lid)重複） | **0筆** |
| Missing district | **0筆** |
| Empty segment | **0筆** |
| Invalid/empty lid | **0筆** |
| Invalid official_value_busiprval（無法轉數值或負值） | **0筆** |
| 總筆數 vs 各district record_count加總 | 1,153,450，與snapshot_meta記錄完全一致 |

**checksum可重新計算一致性——發現一項需誠實記錄的機制脆弱性（非資料損毀）**：

單純從`land_price_records`資料表重新`SELECT`並重算checksum，**逐一行政區
比對皆不相符**。深入排查後找到精確成因（非資料本身有誤）：

1. `compute_records_checksum()`對**list**做`json.dumps`，`sort_keys=True`
   只排序每筆dict內部的key，**不會**排序list本身的順序；若重新查詢時
   SQL未加`ORDER BY rowid`，SQLite不保證回傳順序與原始insert順序一致
2. 原始checksum計算時之record包含官方API回傳的`country`欄位（固定值
   "新北市"），但`land_price_records`資料表schema**未儲存**此欄位

加入`ORDER BY rowid`還原原始順序、並補回`country`欄位後，對4個行政區
（金山區/板橋區/烏來區/鶯歌區）之checksum**皆完全重新計算吻合**，證明
**資料本身100%完整無誤**，純粹是checksum機制本身「排序敏感」+「未
持久化country欄位」的設計脆弱性，導致「單純重新查表算checksum」這種
最直覺的驗證方式會誤判失敗。

**本輪僅記錄此發現於`docs/backlog.md`（見`CHECKSUM_REPRODUCIBILITY_
FRAGILITY`），不在本輪修改`compute_records_checksum()`**（會使現有29筆
已寫入之checksum全數作廢，屬implementation變更，超出本輪Release Gate
稽核範疇）。

## 19. NTPC_SERVER_FILTER_TRUST_POLICY

> 適用範圍：本專案任何未來新增之data.ntpc.gov.tw（或其他NTPC OpenData
> 平台）Provider/sync腳本。

**原則：所有NTPC OpenData的server-side filter，預設狀態為
`UNTRUSTED_UNLESS_VERIFIED_PER_DATASET`。**

理由（本輪實際發現，非理論性假設）：`已公告徵收案件地籍資料`與
`公告土地現值`兩個各自獨立的dataset，其`filter=district eq ...`參數
**皆被證實對district欄位本身也完全無效**（見§11）——不是「大部分
filter有效，只有少數欄位無效」的漸進式限制，而是**同一平台上，同一種
`filter`參數語法，在不同dataset間的實際生效程度完全不可預測**，且
「filter看起來有效」極易因巧合（測試值剛好等於資料表原始儲存順序的
第一筆）而產生false positive。

**強制要求**：任何新Provider或sync腳本，在依賴NTPC OpenData的
server-side filter之前，**必須**執行以下實證測試並將證據保留於程式
註解或commit訊息中：

1. **filtered request**：`filter={欲使用之過濾條件}`
2. **unfiltered control request**：完全不帶`filter`參數，其餘參數
   （`size`/`page`）相同
3. 比較兩者結果：若**逐位元組相同**（尤其是回傳筆數與內容），該filter
   對此dataset **視為無效**，不得依賴
4. 進一步用**故意不可能命中的filter值**（如filter=district eq 一個
   確定不存在的地名）重複測試——若仍回傳非空結果，進一步證實filter
   完全被忽略而非「查無資料」

只有通過以上實證，該dataset的該filter欄位才可標記為`VERIFIED_
FUNCTIONAL_FOR_THIS_DATASET`並在程式中依賴；否則一律視為裝飾性參數，
所有範圍縮小/精確查詢邏輯必須在**用戶端**對完整（或大分頁）回傳結果
執行。

本文件為documentation/provider guidance，未修改任何frozen core。

**重點**：兩者的`district`/`section_name`皆正確為「金山區」／「金美段」，
**未曾**出現"P002-00"進入任何查詢欄位的情況——此為本輪最核心的修正目標，
已透過`test_golden_case_identifier_never_uses_price_segment_code`鎖定。

---

# Phase API-1.7 — Bulk Sync Optimization + Canonical Snapshot Integrity

## 20. Large-Page Pagination 驗證（實證，非假設）

對114年公告土地現值資料集（dataset_id `826870ef-4ea5-48bf-915b-e0a33158cf06`），
以`page=0,1,2`＋`size=500000`發出3次真實請求，並以**當時既有之完整SQLite
snapshot**作為比對基準（comparison oracle），逐項查核：

| 檢查項 | 結果 |
|---|---|
| 總筆數 | 500,000 + 500,000 + 153,450 = **1,153,450**（與已知基準完全一致） |
| 合併後重複(district,segment,lid)筆數 | **0** |
| 分頁間重疊（page0∩page1／page1∩page2／page0∩page2） | **0 / 0 / 0** |
| page0、page1是否皆滿頁（排除因缺頁造成之提前截斷假象） | **是**（皆500,000筆） |
| 首筆/末筆紀錄合理性 | 首筆＝板橋區忠孝段00010000（278000元），末筆＝樹林區光興段13510000（1900元），皆為合理實際資料 |
| 29個行政區是否全數出現 | **是，29/29** |
| Key-set比對（large-page抓取 vs 既有snapshot） | **SET_A == SET_B：True**（雙向差集皆為空集合，1,153,450＝＝1,153,450） |

**結論：`LARGE_PAGE_PAGINATION_VERIFIED = YES`**——large-page分頁與原
PAGE_SIZE=200分頁抓取之資料**完全等價**（非僅筆數相同，而是逐筆key-set
完全相同），可安全作為後續sync腳本之抓取策略。

## 21. Bulk Sync Migration（`scripts/sync_land_price_dataset.py`）

因上一節驗證通過，已將該腳本之`PAGE_SIZE`由200改為**500,000**（§17已
實測size≥1,200,000會遭WAF拒絕，500,000為已驗證安全值，非理論上限）。

**保留、未移除之完整性驗證機制**（使用者明確要求「不要因request數變少
就移除完整性驗證」）：

- retry-with-backoff（`RETRIES_PER_PAGE=3`，`RETRY_BACKOFF_S=5.0`，因
  單次請求變大，timeout同步由10秒提高至120秒）
- 每頁checkpoint（`CHECKPOINT_EVERY_PAGES=1`——即使現在只剩3頁，仍保留
  「任一頁後即落地」機制，避免page1與page2之間中斷導致約100萬筆已抓取
  資料遺失）
- response shape validation（沿用：非list型態視為錯誤）
- **新增**：總筆數驗證（`_validate_total_count`）——與已知基準
  （1,153,450）比對，若實際筆數低於基準50%則視為疑似截斷並中止（不寫入
  新snapshot）；此為**訊號式**檢查而非硬性相等（政府資料集本身會隨公告
  週期自然變動筆數），因此僅記錄差異、不因非零差異而失敗
- **新增**：重複key驗證（`_validate_no_duplicate_keys`）——因large-page
  策略下，分頁間重疊在理論上並非結構性不可能（不同於PAGE_SIZE=200時代
  單調遞增爬取的隱含保證），故明確於每次完整同步後檢查

### 實際執行結果（真實對政府API執行，非模擬）

| 指標 | Before（PAGE_SIZE=200） | After（PAGE_SIZE=500,000） |
|---|---|---|
| Request數 | 約5,768次（⌈1,153,450／200⌉） | **3次** |
| 執行時間 | 實測約35-50分鐘（§17記錄） | **實測102秒**（本輪實際重跑，含checkpoint寫入時間） |
| 總筆數 | 1,153,450 | 1,153,450（差異＝0） |
| 重複key | 0 | 0 |
| 涵蓋行政區數 | 29/29 | 29/29 |
| Exit code | 0 | 0 |

**`BULK_SYNC_MIGRATED = YES`**。

## 22. CSV vs JSON Transport 比較（實測，含本輪新增之欄位保真度驗證）

延續§17之位元組/耗時實測（size=500,000：JSON 62.3MB/23.28秒 vs CSV
29.3MB/13.47秒），本輪額外對兩種格式做**欄位保真度**與**編碼**實測
（size=2,000筆樣本，取自同一次即時查詢窗口）：

| 面向 | JSON | CSV | 結論 |
|---|---|---|---|
| Bytes（同筆數） | 244,001 bytes | 112,064 bytes | CSV約為JSON之46%——與§17大樣本比例（29.3/62.3≈47%）一致 |
| 欄位鍵集合 | `country,district,segment,lid,official_value_busiprval` | 完全相同 | **一致，無欄位遺失或新增** |
| 欄位順序 | 固定 | 與JSON相同 | 一致 |
| 跨格式值一致性（500筆交叉比對） | — | — | **0筆mismatch** |
| `lid`零填充保真度（本專案關鍵contract） | 保持字串`'00010000'` | 保持字串`'00010000'`（`csv.DictReader`不做型別推斷） | **兩者皆完整保留前導零，無精度/型別損失** |
| 中文編碼（UTF-8） | 原生 | 需以`utf-8-sig`解碼（BOM前綴） | CSV多一道BOM處理步驟，否則header首欄位名會被BOM污染 |
| Parser複雜度 | `json.loads()`直接得到現有程式碼慣用之dict-per-row結構，**零改動成本** | 需引入`csv.DictReader`＋BOM處理，且現有`_fetch_page`／provider程式碼皆以dict-per-row為假設，需新增轉換層 | JSON複雜度更低 |
| Schema穩定性 | 同一資料來源，兩格式皆為官方即時序列化結果，非各自獨立維護 | 同左 | 兩者風險相同 |
| Memory usage（現行非streaming實作） | 全量載入記憶體後`json.loads` | 全量載入記憶體後`csv.DictReader`逐行解析 | 現行實作下無顯著差異；CSV僅在**未來改為串流讀取**時才有理論優勢（JSON需額外streaming parser依賴） |
| 速度（大量，§17） | 23.28秒／500,000筆 | 13.47秒／500,000筆 | CSV較快，但兩者在3頁全量sync中之總差異僅約30秒（102秒 vs 預估約70-80秒） |

**決策：本輪維持JSON作為sync transport，不切換至CSV。**

理由：CSV在bytes／速度上確實較優，但（a）絕對效益有限——3頁全量sync
已從35-50分鐘降至102秒，此30秒級的額外節省在整體流程中邊際效益低；
（b）切換transport需新增CSV解析層＋BOM處理＋重新驗證整條pipeline
（provider查詢、checksum計算、Golden Case），屬非必要之額外變更面；
（c）**Provider evidence contract（`ExpropriationCaseEvidence`／
`LandPriceEvidence`之欄位結構、checksum計算依據之record dict形狀）
本輪未因此改變**，維持JSON transport即可原生滿足此要求，無需額外
保證。CSV transport本身已實測可行且欄位完全保真，記錄於
`docs/backlog.md::BULK_DOWNLOAD_MIGRATION_CANDIDATE`供未來輪次評估是否
值得投入切換成本。

## 23. Canonical Checksum Contract（`CANONICAL_V2` vs `LEGACY_V1`）

延續§18發現之checksum可重現性脆弱性（list順序敏感＋`country`欄位未
持久化），本輪**未直接覆蓋**原`compute_records_checksum()`（保留為
`LEGACY_V1`，程式碼與行為完全不變，供既有已寫入之LEGACY_V1 snapshot
之checksum維持可解讀），而是新增**獨立、可版本識別**之
`CANONICAL_V2`演算法（`providers/cadastral_dataset_cache.py`）：

- 固定欄位集合＋固定陣列順序（非dict）：
  - 土地現值：`(country, district, segment, lid, official_value_busiprval)`
  - 徵收案件：`(district, segment, id, sus_year, pro_name)`
- 固定紀錄排序：依`(district, segment, lid)`／`(district, segment, id)`
  排序整個陣列列表，**不依賴SQLite rowid，不依賴insertion order**
- 固定UTF-8編碼＋固定JSON序列化規則
  （`json.dumps(arrays, ensure_ascii=False, separators=(",", ":"))`，
  因序列化對象為陣列列表而非dict，`sort_keys`已無意義，不使用）

`snapshot_meta`新增`checksum_algorithm`／`checksum_schema_version`兩欄
（皆為**additive schema migration**，以`PRAGMA table_info`偵測欄位是否
已存在再決定是否`ALTER TABLE ADD COLUMN`，對既有資料庫冪等、不破壞
既有row）；`checksum_algorithm`欄位新增時預設值為`'LEGACY_V1'`，確保
**既有、遷移前已寫入之row在遷移後仍正確標記為LEGACY_V1**，不會被誤讀
為CANONICAL_V2。`land_price_records`新增`country`欄位（同為additive
migration），使`CANONICAL_V2`之checksum得以**單純從資料表重新查詢即可
100%重現**，正面解決§18記錄之脆弱性根因（而非僅是繞過症狀）。

`write_land_price_snapshot()`／`write_expropriation_snapshot()`預設
`checksum_algorithm=CANONICAL_V2`，但仍完整保留`checksum_algorithm=
LEGACY_V1`之明確呼叫路徑作為向後相容逃生門（見
`tests/test_cadastral_dataset_cache.py::
test_write_land_price_snapshot_can_still_write_legacy_v1_explicitly`）。

新增`recompute_canonical_checksum(dataset_id, scope_key, kind)`方法，
供稽核工具「重新開啟SQLite、單純從已儲存欄位重新查詢並計算」，不依賴
任何記憶體中之原始抓取順序或額外未持久化欄位。

## 24. 完整29區CANONICAL_V2 Checksum驗證（非抽樣）

本輪對§21實際執行之29個行政區土地現值snapshot，**逐一**（非抽樣4個）
以全新`CadastralDatasetCache()`實例（獨立`sqlite3.connect()`，非重用
任何既有連線或記憶體狀態）重新查詢並呼叫`recompute_canonical_checksum`：

```
Total districts in snapshot_meta for land price dataset: 29
Districts with checksum_algorithm=CANONICAL_V2: 29
Districts with unexpected non-CANONICAL_V2 algorithm: []
MATCH count: 29 / 29
MISMATCH count: 0
Sum of record_count across all districts: 1153450
```

**`CANONICAL_CHECKSUM_REPRODUCIBLE = PASS`（29/29 MATCH）**——§18記錄之
「單純重新查表算checksum即失敗」問題，在CANONICAL_V2下**完全消除**。

## 25. Golden Case 最終複查（遷移後）

於large-page migration＋checksum contract變更**之後**，重新對金山區／
金美段／489地號執行完整查詢（`RealExpropriationCaseProvider`／
`RealLandPriceProvider`，非直接讀取snapshot_meta）：

```
ExpropriationCaseProvider.status = NOT_FOUND_IN_DATASET
ExpropriationCaseProvider.record_count = 9437
LandPriceProvider.announced_land_current_value = 57305（元/M²）
LandPriceProvider.announced_land_current_value_status = AVAILABLE
LandPriceProvider.record_count = 25361（金山區）
```

**業務結果與Phase API-1.5／1.6完全一致，未因sync transport或checksum
演算法變更而改變**（`LandPriceProvider.checksum`欄位之數值本身因演算法
由LEGACY_V1改為CANONICAL_V2而不同，此為**預期且刻意**之版本識別行為，
非資料錯誤——`checksum_algorithm`欄位已明確標示所使用之版本，供人工
覆核時正確解讀）。

## 26. Regression + Core Freeze 複查

- `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
  **599 passed**，0 failed，0 error。
- Core Freeze 36檔案SHA-256組合雜湊：
  - BEFORE：`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  - AFTER：`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  - **完全一致，`CORE_FREEZE_VIOLATION = NO`**（本輪所有變更皆侷限於
    `providers/cadastral_dataset_cache.py`、
    `scripts/sync_land_price_dataset.py`、對應測試檔案與本文件／
    `docs/backlog.md`，未觸及engine/、data/rules/、schemas/或
    document_extraction_provider.py）。

## 27. Phase API-1.7 Release Gate

```
LARGE_PAGE_PAGINATION_VERIFIED = YES
BULK_SYNC_MIGRATED = YES
SYNC_REQUEST_COUNT_BEFORE = ~5768（⌈1,153,450／200⌉）
SYNC_REQUEST_COUNT_AFTER = 3
SYNC_TIME_BEFORE = 約35-50分鐘（實測，§17）
SYNC_TIME_AFTER = 102秒（實測，本輪）
CANONICAL_CHECKSUM_REPRODUCIBLE = PASS
CHECKSUM_DISTRICTS_VERIFIED = 29/29
GOLDEN_CASE_OFFICIAL_VALUE = 57,305 元/M²（金山區／金美段／489地號）
CORE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 599 passed, 0 failed（不含
  test_phase5_golden_pipeline.py，依使用者指示排除）
PHASE_API_1_7_RELEASE_READY = YES
```

本輪未觸碰TGOS、NLSC、FacilityProvider、ComparableProvider或
Multi-Jurisdiction Runtime Rules，符合使用者明確之範圍限制。

---

# Phase API-1.8 — Snapshot Atomicity & Completeness Hardening

## 28. Partial Snapshot問題與Atomic Staging Contract

**問題（Phase API-1.7遺留之correctness風險）**：`scripts/sync_land_price_
dataset.py`原本在每次checkpoint時直接呼叫`write_land_price_snapshot()`，
即把「目前為止已抓取」的PARTIAL資料直接寫入`RealLandPriceProvider`會讀取
的正式`land_price_records`/`snapshot_meta`表。若crawl在兩次checkpoint
之間中斷，CURRENT表會停留在某次checkpoint當下的部分資料，且與「這個
行政區本來就只有這麼多筆」在結構上完全無法區分——這是correctness問題，
不僅是resilience問題。

**修正：Atomic Staging Contract**（`providers/cadastral_dataset_cache.py`）：

```
Official API -> download -> STAGING -> validation -> [全數PASS] ->
atomic promote -> CURRENT
```

- 新增`land_price_records_staging`／`expropriation_records_staging`／
  `staging_run_status`三張表（與現有CURRENT表schema對應，additive、
  不影響既有資料）。
- `stage_land_price_records()`／`stage_expropriation_records()`：每次
  checkpoint呼叫，**只寫STAGING**，可任意覆寫重複呼叫。
- `promote_land_price_staging_to_current()`／`promote_expropriation_
  staging_to_current()`：**唯一**能把資料寫入CURRENT的路徑，對傳入的
  **每一個**scope_key在**單一sqlite3交易**內完成「讀取staging→驗證→
  DELETE+INSERT CURRENT→更新snapshot_meta」——只要任一scope_key驗證失敗
  （staging無資料／重複key），整個呼叫在交易commit前拋出例外，Python
  `sqlite3.Connection.__exit__`自動rollback，使**該次promote呼叫中已
  執行的所有DELETE/INSERT語句全部復原**，CURRENT對每一個scope_key都維持
  呼叫前的完整舊版本，不會有「部分行政區已更新、部分還是舊資料」的
  混合狀態。
- `mark_staging_failed()`：任何失敗路徑呼叫此方法，**只更新
  `staging_run_status`診斷欄位**，不觸碰CURRENT或staging資料本身
  （失敗run之staging資料保留供診斷，直到下次`begin_staging_run()`
  才清除重來）。

**刻意不在`snapshot_meta`新增`is_complete`欄位**：因為`snapshot_meta`
的任何一列，現在**只可能**經由驗證通過之`promote_*`寫入——列存在本身
即等同COMPLETE/VALIDATED，額外加一個可能被獨立寫錯的flag欄位反而
多一個可能違反此不變量的地方。`get_snapshot_meta()`／`lookup_*()`
（Provider讀取路徑）**從未**查詢`*_staging`或`staging_run_status`，
結構上不可能讀到partial資料，不需要額外欄位保證。

## 29. `scripts/sync_land_price_dataset.py` 重寫

流程改為：`begin_staging_run()` → 迴圈中`_stage()`每頁checkpoint（只寫
STAGING）→ crawl完成後執行重複key驗證＋總筆數驗證 → **全數PASS**才呼叫
`promote_land_price_staging_to_current()`（單一交易，涵蓋全部29個
行政區）。任何例外（網路中斷／驗證失敗）皆導向`mark_staging_failed()`，
`finally`已移除舊有「不論成敗都flush到CURRENT」邏輯——失敗時**不會**
呼叫promote，CURRENT完全不受影響。`--max-pages`（測試用PARTIAL執行）
明確只stage、永不promote。

**實際對政府API重跑驗證**（真實執行，非模擬）：

```
page 0: rows=500000, total so far=500000
page 1: rows=500000, total so far=1000000
page 2: rows=153450, total so far=1153450
重複key驗證：duplicates=0（通過）
總筆數驗證：實際=1153450，已知基準=1153450，差異=+0
驗證全數通過，執行atomic promote（STAGING -> CURRENT，涵蓋29個行政區，單一交易）...
Promote完成：checksum_algorithm=CANONICAL_V3，總筆數=1153450，行政區數=29
```

執行時間93秒，exit code 0，與Phase API-1.7結果（1,153,450筆/29行政區）
完全一致。

## 30. `scripts/sync_expropriation_dataset.py` 一併遷移＋重大發現

此腳本原本就只在crawl**全部完成後**才寫入CURRENT一次（不像土地現值
腳本有checkpoint-to-CURRENT的bug），因此本身沒有Phase API-1.7那種
partial-overwrite風險。仍將其遷移至相同stage→promote路徑，純粹是為了
系統一致性（「Provider只能讀取完整驗證過之CURRENT」這個不變量應適用
全系統，不只土地現值）。

**遷移過程中，新增的promote-time重複key防禦性檢查，在對真實政府API
執行時意外發現一項重大資料事實**：`(district, segment, land_no)`
**並非**徵收案件資料集的唯一鍵——對全部9,437筆真實資料完整掃描，
發現**441組不同的(district,segment,id)重複key**（共471筆重複列，
因部分key出現3次），例如：

```
DUP key=('板橋區', '江子翠段第一崁小段', '10067') count=3
  {'sus_year': '2005', 'pro_name': '...(第一次)一併徵收', ...}
  {'sus_year': '2005', 'pro_name': '...(第二次)一併徵收', ...}
  {'sus_year': '2006', 'pro_name': '...(第三次)一併徵收', ...}
```

這是**真實、合法之政府資料**——同一地籍（同區/段/地號）在不同年度被
不同徵收計畫分別徵收，不是分頁重疊或資料損毀（另有**1組**逐欄位完全
相同之真重複：淡水區／水仙段／5080002，兩筆內容逐字一致，屬單純資料
重複列，不影響正確性）。這與土地現值資料集的`(district,segment,lid)`
**經驗證確實唯一**（1,153,450筆real rows中0重複）形成鮮明對比。

**因此**：`promote_land_price_staging_to_current()`／`promote_
expropriation_staging_to_current()`新增`enforce_duplicate_check`參數
（預設`True`）；土地現值sync維持預設（0重複是empirically justified的
硬性要求，任何重複皆代表large-page重疊等真實錯誤，必須阻擋promote）；
徵收案件sync明確傳入`enforce_duplicate_check=False`（重複計數仍會回傳
供可見性，只是不阻擋promote——若真的擋下，此資料集將**永遠無法**通過
atomic staging路徑完成同步，因為441組重複是資料本身的常態，不是bug）。

**本輪未修正、誠實記錄之延伸發現**：`RealExpropriationCaseProvider.
query_case()`目前的lookup（`lookup_expropriation()`）在有多筆符合
`(district,segment,land_no)`的紀錄時，僅回傳SQL查詢的第一筆，**silently
忽略其餘真實存在的徵收案件紀錄**。此為Phase API-1.5即已存在之設計假設
（「一個地號=一筆案件」）被真實資料證偽，但修正需要Provider層級的查詢
/回傳結構調整（例如回傳list而非單筆、或在key中納入年度/計畫），超出
本輪「snapshot atomicity/completeness」之範疇，記錄於`docs/backlog.md::
EXPROPRIATION_KEY_NOT_UNIQUE_MULTI_PROJECT_PARCELS`供未來輪次處理。

**實際對政府API重跑結果**：

```
Promote完成：record_count=9437 checksum_sha256=1dc99e0c745eb403afb9aacefe36c132ac4d12e5242302f96a9c7d65085b46aa
checksum_algorithm=CANONICAL_V3 duplicate_key_count=471（非promote失敗條件）
```

## 31. Canonical Checksum Tie Ordering（`CANONICAL_V2` → `CANONICAL_V3`）

**問題**：`CANONICAL_V2`（Phase API-1.7）排序時只用`(district,segment,
lid或id)`這個**前綴**作為sort key。Python的`list.sort()`是stable
sort——若兩筆紀錄剛好共用相同前綴但其餘欄位不同（例如上一節發現的441組
徵收案件重複key，`sus_year`/`pro_name`不同），排序結果會保留兩者在
**輸入list中的原始相對順序**，導致這種情況下checksum仍然是
input-order-dependent，與CANONICAL_V2宣稱的「完全不依賴輸入順序」不符。
以下測試證明此缺口確實存在：

```python
def test_canonical_v2_has_stable_sort_tie_ordering_gap():
    a = [row(lid="1", value="100"), row(lid="1", value="999")]  # 相同district/segment/lid，value不同
    b = list(reversed(a))
    assert compute_canonical_checksum_v2_land_price(a) != compute_canonical_checksum_v2_land_price(b)
```

**修正：新增`CANONICAL_V3`（不覆蓋`CANONICAL_V2`）**——排序改為使用
**完整canonical欄位tuple**（每個欄位皆納入sort key，而非只取前綴）：

```python
def _canonicalize_v3(records, fields):
    arrays = [[r.get(f) for f in fields] for r in records]
    arrays.sort(key=lambda arr: tuple("" if v is None else v for v in arr))
    canonical = json.dumps(arrays, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

對於完全相同內容的兩筆紀錄，排序結果本就無關緊要（序列化後bytes
相同）；對於任何內容不同的兩筆紀錄，完整tuple sort key必然不同，故
結果對輸入順序**完全不敏感**，徹底排除此類edge case。

**版本治理（未偷偷改寫已發布演算法）**：`compute_canonical_checksum_v2_
*()`函式本身**完全未變更**（`test_canonical_v2_function_itself_is_
unchanged_by_v3_introduction`鎖定回歸），`CANONICAL_V2`已寫入之29個
土地現值snapshot若未重新sync，其checksum欄位仍是V2值，`checksum_
algorithm`欄位誠實標示為`CANONICAL_V2`，不會被誤讀。`write_*_snapshot()`
與`promote_*_staging_to_current()`的**預設值**由`CANONICAL_V2`改為
`CANONICAL_V3`（call-site預設值變更，非重新定義V2/V1本身），V1/V2仍完整
保留為顯式可選參數。

**有趣的驗證副產品**：土地現值全部29個行政區在本輪重新以atomic staging
路徑同步後，多數行政區的checksum**數值與Phase API-1.7時完全相同**
（例如金山區：皆為`95befd56914ce6a6dde36e0d2a5364bbe7910580ee29af0c65
d4be5f738fdcbf`）——這並非巧合或bug，而是**數學上的必然**：土地現值
資料集的`(district,segment,lid)`本身已驗證0重複，當前綴key本身已是
每筆紀錄的唯一識別，V2的「前綴排序」與V3的「完整tuple排序」對**無重複
key**的資料集必然產生完全相同的排序結果（因為前綴已經足以決定唯一
順序），故checksum bytes相同。反之，徵收案件資料集**確實有**441組
重複key，V2→V3遷移後其checksum數值**改變**（因排序依據不同）——這正好
從真實資料反向驗證了V3存在的必要性，也證明V2→V3遷移只在「真的有tie」
的地方才改變結果，其餘情況完全不受影響。

## 32. Large Page Content Equivalence（不僅Key-Set，含全欄位內容）

Phase API-1.7的§20僅驗證了large-page抓取與既有snapshot之**key-set**
（`(district,segment,lid)`）完全相符，文件當時已誠實標示為
「key-set完全相同」而非過度宣稱byte-for-byte equivalent。本輪執行更嚴格
之驗證：對既有CURRENT snapshot（Phase API-1.7之CANONICAL_V2版本，本輪
遷移前）與一次全新large-page抓取，比較**完整內容tuple**
`(country, district, segment, lid, official_value_busiprval)`：

```
TOTAL fresh fetch rows: 1153450
Fresh distinct full-content tuples: 1153450
Existing CURRENT snapshot distinct full-content tuples: 1153450
Symmetric difference: only_in_fresh=0, only_in_existing=0
CONTENT_EQUIVALENT (full field tuple, symmetric difference == 0): True
```

**`LARGE_PAGE_CONTENT_EQUIVALENT = PASS`**——本輪起可正式使用
`CONTENT_EQUIVALENT`一詞描述large-page抓取與既有snapshot之關係（不僅
key相同，全部5個欄位之內容亦逐筆完全相符，對稱差集為0）。

## 33. Failure Injection Tests（12項情境，全數以真實程式碼路徑驗證，非僅描述）

新增`tests/test_sync_land_price_dataset.py`（10個測試，透過monkeypatch
`_fetch_page`模擬各種失敗點，never觸碰真實網路）＋`tests/
test_cadastral_dataset_cache.py`新增之staging/promote/checksum測試，
涵蓋全部12項要求情境：

| # | 情境 | 測試 |
|---|---|---|
| 1 | failure after page0 | `test_failure_after_page0` |
| 2 | failure after page1 | `test_failure_after_page1` |
| 3 | malformed page2 | `test_malformed_page2` |
| 4 | duplicate key validation failure | `test_duplicate_key_validation_failure`（sync層級）＋`test_promote_rejects_duplicate_key_in_staging_and_leaves_current_untouched`（cache層級） |
| 5 | total count validation failure | `test_total_count_validation_failure` |
| 6 | staging never visible to Provider | `test_staging_never_visible_to_provider_after_failed_sync`＋`test_staging_never_visible_to_provider_lookup` |
| 7 | previous CURRENT survives failure | `test_checksum_and_record_count_survive_failed_sync`＋`test_last_known_good_survives_multi_district_promote_failure` |
| 8 | successful sync atomically replaces CURRENT | `test_successful_sync_atomically_replaces_current`＋`test_successful_promote_atomically_replaces_current` |
| 9 | checksum survives failed sync | `test_checksum_and_record_count_survive_failed_sync` |
| 10 | Golden Case survives failed sync | `test_golden_case_survives_failed_sync` |
| 11 | checksum input-order independence | `test_canonical_v3_ordinary_case_still_order_independent`等 |
| 12 | equal-primary-key different-content ordering | `test_canonical_v2_has_stable_sort_tie_ordering_gap`（證明V2缺口）＋`test_canonical_v3_closes_the_tie_ordering_gap`（證明V3修正） |

全數通過（見§34 regression結果）。

## 34. Golden Case最終複查

於全部atomic staging遷移＋兩個資料集皆重新以新pipeline同步完成後：

```
ExpropriationCaseProvider.status = NOT_FOUND_IN_DATASET
ExpropriationCaseProvider.record_count = 9437
LandPriceProvider.announced_land_current_value = 57305（元/M²）
LandPriceProvider.announced_land_current_value_status = AVAILABLE
LandPriceProvider.record_count = 25361（金山區）
```

業務結果與Phase API-1.5～1.7完全一致，provenance（checksum/record_count/
schema_version）皆存在且可追溯。

## 35. Regression + Core Freeze複查

- `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
  **627 passed**，0 failed，0 error（較Phase API-1.7的599項，新增28項
  Phase API-1.8測試）。
- Core Freeze 36檔案SHA-256組合雜湊：
  - BEFORE：`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  - AFTER：`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  - **完全一致，`CORE_FREEZE_VIOLATION = NO`**。

## 36. Phase API-1.8 Release Gate

```
ATOMIC_SNAPSHOT_PROMOTION = PASS
PARTIAL_SNAPSHOT_VISIBLE_TO_PROVIDER = NO
LAST_KNOWN_GOOD_SURVIVES_FAILURE = PASS
INTEGRITY_FAILURE_PROMOTES_DATA = NO
CANONICAL_CHECKSUM_ORDER_INDEPENDENT = PASS
LARGE_PAGE_CONTENT_EQUIVALENT = PASS
GOLDEN_CASE_OFFICIAL_VALUE = 57,305 元/M²（金山區／金美段／489地號）
CORE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 627 passed, 0 failed（不含
  test_phase5_golden_pipeline.py，依使用者指示排除）
PHASE_API_1_8_RELEASE_READY = YES
```

本輪未觸碰TGOS、NLSC、FacilityProvider、ComparableProvider或
Multi-Jurisdiction Runtime Rules，符合使用者明確之範圍限制。

**本輪意外發現並誠實記錄一項超出範疇之延伸議題**（見§30）：
`RealExpropriationCaseProvider`目前對重複地號僅回傳單筆紀錄，真實資料
證實此假設不成立（441組真實多專案徵收案例）。此發現不影響本輪
Release Gate判定（不屬於snapshot atomicity/completeness範疇，且未使
現有業務結果或Golden Case改變），已記錄於`docs/backlog.md`供未來輪次
處理。

---

# Phase API-1.9 — Expropriation Multi-Record Semantics & Deterministic Evidence

Phase API-1.8遺留之延伸議題（§30/§36結尾）本輪正式處理：
`RealExpropriationCaseProvider`對多筆比對紀錄僅回傳單筆的限制。

## 37. 資料語意Audit（完整9,437筆，非抽樣）

對CURRENT徵收案件snapshot逐筆分析：

```
TOTAL_ROWS = 9437
UNIQUE_PARCEL_KEYS = 8966
MULTI_RECORD_PARCEL_KEYS = 441
MAX_RECORDS_PER_PARCEL = 3
EXACT_DUPLICATE_FULL_ROWS = 90
```

441組multi-record parcel完整分類（非假設，逐組比對`sus_year`/`pro_name`
是否相同）：

| 分類 | 數量 |
|---|---|
| 1. 同parcel，不同year，**同**project | 0 |
| 2. 同parcel，**同**year，不同project | 322 |
| 3. 同parcel，year與project**皆**不同 | 57 |
| 4. 完全相同full row duplicate（單一真實事件被重複列出2-3次） | 62 |
| SUM | 441 |

（另有0組「mixed」——即同一parcel內同時存在真實不同事件與逐欄位完全
重複列的情形；本輪資料乾淨地二分為「全為不同事件」或「全為相同重複」，
無混合情形。）

**修正Phase API-1.8記錄之不精確處**：該輪僅檢視前20組重複key之抽樣
輸出，誤記「僅1組為完全相同重複」；本輪對全部441組完整分類後，正確
數字為上表所示（62組，非1組）。已同步更新`docs/backlog.md`。

範例（同year不同project，322組最大宗類別）：
```
三峽區/佳興段/8410000：
  2020年 三鶯線捷運系統計畫工程(三峽區介壽路至佳興路)(地上權)
  2020年 三鶯線捷運高架橋樑墩柱(編號P03-32)施作工程
```

範例（year與project皆不同，57組）：
```
五股區/水碓段水碓小段/3480025：
  2010年 國道1號五股至楊梅段拓寬工程(臺北縣五股鄉都市土地)
  2011年 國道1號五股至楊梅段拓寬工程（臺北縣五股鄉都市土地）
```

範例（完全相同重複，62組——注意：Phase API-1.8報告中原本誤認為的
「新店區/粗坑段/5080002」實為終端機CJK亂碼誤讀，真實parcel為
**淡水區/水仙段/5080002**，本輪已修正並重新驗證，見§42）：
```
淡水區/水仙段/5080002：
  2005年 淡水鎮竹圍國民小學殘障坡道(94年)  （2筆逐欄位完全相同）
```

## 38. PARCEL_MULTI_EVENT vs EXACT_SOURCE_DUPLICATE 正式區分

`providers/cadastral_dataset_cache.py`新增`audit_expropriation_parcel_
multiplicity()`方法，正式區分兩種duplicate（絕不混為一談）：

- **PARCEL_MULTI_EVENT**：同`(district,segment,land_no)`，但`sus_year`
  或`pro_name`不同——**可能合法之多事件資料**，本輪確認379組屬此類。
- **EXACT_SOURCE_DUPLICATE**：同`(district,segment,land_no)`，且
  `sus_year`與`pro_name`皆相同——**來源資料品質訊號**，62組屬此類。

兩者皆**不刪除**原始紀錄（raw snapshot忠實保存，見§41）；`sync_
expropriation_dataset.py`同步完成後印出完整分類（見§43）。

## 39. Cache Lookup Contract：`lookup_expropriation_matches()`

`CadastralDatasetCache.lookup_expropriation()`（原`fetchone()`風格，回傳
`Optional[Dict]`）**保留**為legacy單筆便利wrapper（供已接受此限制之
既有呼叫端使用），但**不再是`RealExpropriationCaseProvider`使用的
路徑**。新增：

```python
def lookup_expropriation_matches(self, dataset_id, scope_key, district,
                                  segment, land_no) -> List[Dict]:
    """回傳該parcel全部raw source rows，絕不first-match/fetchone/
    latest-wins/oldest-wins/random。"""
```

輸入不變（district/section/normalized land number），輸出改為
**全部**符合之raw rows（含EXACT_SOURCE_DUPLICATE之重複列，不去重—— 
去重邏輯在Provider層處理，見§40）。`RealExpropriationCaseProvider.
query_case()`已改為呼叫此方法（`tests/test_expropriation_case_provider.
py::test_query_case_uses_matches_lookup_not_legacy_single_result`以
monkeypatch方式結構性鎖定此呼叫路徑，而非僅驗證行為）。

## 40. Deterministic Ordering

`lookup_expropriation_matches()`依**完整row tuple**排序：
`(district, segment, land_no_raw, sus_year, pro_name)`——沿用Phase
API-1.8 CANONICAL_V3對checksum tie-ordering問題的相同原則（完整tuple
排序，非僅前綴），套用於查詢結果而非checksum。由於district/segment/
land_no在單次查詢內為常數，實質排序依據為`(sus_year, pro_name)`，符合
使用者建議之排序欄位。`tests/test_cadastral_dataset_cache.py::
test_lookup_expropriation_matches_deterministic_order_independent_of_
insertion`與`tests/test_expropriation_case_provider.py::
test_deterministic_match_ordering_independent_of_insertion_order`皆
以「正向插入」與「反向插入」兩份內容相同但順序相反的staging資料，
驗證promote後查詢結果順序完全一致。

## 41. Evidence Model（最小additive擴充）

`domain/models.py`新增`ExpropriationCaseMatch`（district/segment/
land_no為必填，announcement_year/project_name為Optional）；
`ExpropriationCaseEvidence`新增4個欄位（皆有預設值，對既有建構呼叫
完全向下相容）：

```python
match_count: int = 0                              # DISTINCT事件數
matches: List[ExpropriationCaseMatch] = []        # 全部DISTINCT事件
raw_match_count: int = 0                          # raw row數（含重複列）
distinct_event_count: int = 0                     # 與match_count同義
```

`RealExpropriationCaseProvider.query_case()`邏輯：取得`raw_matches`後，
依`(sus_year,pro_name)`簽章去重得到`distinct_events`——EXACT_SOURCE_
DUPLICATE之62組parcel雖raw_match_count=2或3，但因所有row簽章相同，
`distinct_event_count`正確收斂為1（產生**一個**`ExpropriationCaseMatch`，
非2-3個虛假事件）；PARCEL_MULTI_EVENT之379組parcel則`distinct_event_
count`＝實際不同事件數（2或3），對應數量之`ExpropriationCaseMatch`
**全部**保留。

## 42. Backward Compatibility：Convenience Fields

`announcement_year`/`project_name`（pre-1.9既有欄位）語意：

| match_count | 行為 |
|---|---|
| 0 | `NOT_FOUND_IN_DATASET`，兩欄位皆`None`（不變） |
| 1 | 兩欄位皆由該筆事件填入（**不變**——8,966-62=8,904個原本就無歧義之parcel，行為與Phase API-1.8完全相同；62個EXACT_SOURCE_DUPLICATE parcel因去重後distinct_event_count=1，同樣走此路徑，即使raw_match_count=2或3） |
| >1 | 兩欄位**明確設為`None`**——絕不偷偷選first/latest/max year。`matches`保留全部真實事件。`notes`明確說明原因並指向`matches`欄位 |

真實案例驗證（直接對CURRENT snapshot查詢，非模擬）：

**EXACT_SOURCE_DUPLICATE案例**（淡水區/水仙段/5080002，即Phase API-1.8
report中因終端機CJK亂碼被誤讀為「新店區/粗坑段」之同一筆真實資料）：
```
RAW_MATCH_COUNT = 2
DISTINCT_EVENT_COUNT = 1
match_count = 1
MATCHES = [{'announcement_year': '2005', 'project_name': '淡水鎮竹圍國民小學殘障坡道(94年)', ...}]
announcement_year = 2005（單一事件，convenience欄位正確填入）
project_name = 淡水鎮竹圍國民小學殘障坡道(94年)
notes包含：「原始snapshot中此地號有2筆逐欄位完全相同之重複列，經去重後判定為同一真實事件」
```

**PARCEL_MULTI_EVENT案例**（三峽區/佳興段/8410000，真實多事件）：
```
RAW_MATCH_COUNT = 2
DISTINCT_EVENT_COUNT = 2
match_count = 2
MATCHES = [
  {'announcement_year': '2020', 'project_name': '三鶯線捷運系統計畫工程(三峽區介壽路至佳興路)(地上權)', ...},
  {'announcement_year': '2020', 'project_name': '三鶯線捷運高架橋樑墩柱(編號P03-32)施作工程', ...},
]
announcement_year = None（無法代表單一事件，刻意留空）
project_name = None
```

**兩個真實案例皆證明：全部官方事件被完整保留與回傳，無first-match
遺失。**

## 43. Status語意（沿用既有enum，未新增）

`ExpropriationCaseStatus`維持`FOUND_IN_DATASET`/`NOT_FOUND_IN_DATASET`/
`UNKNOWN`三態，**未新增**任何狀態值。0 matches=NOT_FOUND_IN_DATASET；
1+ matches=FOUND_IN_DATASET（不論match_count為1或多筆，multiple並非
錯誤）。「downstream需人工判斷哪一案件與本案相關」之語意，由**既有**
`requires_manual_review`欄位承載——此欄位本就對**所有**
ExpropriationCaseEvidence硬編碼為`True`（Phase API-1即已如此，因本
evidence僅為corroborating evidence），故multi-match情境**不需要**新增
`MANUAL_REVIEW_REQUIRED`狀態值，既有設計已完整涵蓋此語意，符合使用者
「不要為了本輪大改整體Domain」之要求。

## 44. 資料集涵蓋限制維持不變

`NOT_FOUND_IN_DATASET`之notes文字（§37前後皆同）明確維持：「查無紀錄
不代表此地號確定未被徵收，仍需人工覆核」，`ExpropriationCaseStatus`
docstring原有之涵蓋範圍限制說明（僅涵蓋92年以後一般徵收案件，不含
更正/撤銷徵收，不含區段徵收）完全未變更。

## 45. `sync_expropriation_dataset.py`：Audit輸出＋Snapshot Integrity

同步腳本於promote完成後，呼叫新增之`audit_expropriation_parcel_
multiplicity()`並印出完整分類（真實執行結果）：

```
Promote完成：record_count=9437 checksum_sha256=1dc99e0c745eb403afb9aacefe36c132ac4d12e5242302f96a9c7d65085b46aa
checksum_algorithm=CANONICAL_V3 duplicate_key_count=471（非promote失敗條件）

多筆事件/重複列 Audit：
  TOTAL_ROWS = 9437
  UNIQUE_PARCEL_KEYS = 8966
  MULTI_RECORD_PARCEL_KEYS = 441
  MAX_RECORDS_PER_PARCEL = 3
  PARCEL_MULTI_EVENT_KEYS = 379（同parcel、內容不同之真實多事件——合法官方資料，非錯誤）
  EXACT_SOURCE_DUPLICATE_KEYS = 62（同parcel、全部rows逐欄位相同——來源資料重複列，非多事件）
  EXACT_DUPLICATE_FULL_ROWS = 90（重複列之raw row數，非parcel數）
```

**PARCEL_MULTI_EVENT不再被誤判為sync failure**（Phase API-1.8已修正，
`enforce_duplicate_check=False`延續不變）；**EXACT_SOURCE_DUPLICATE
被正式統計並留下audit訊息**，且raw snapshot中兩筆逐欄位完全相同之
紀錄**皆未被刪除**（`lookup_expropriation_matches()`忠實回傳全部原始
rows，見`test_lookup_expropriation_matches_preserves_exact_duplicate_
rows`）。Phase API-1.8之Atomic Staging／Last-Known-Good／CANONICAL_V3
全部維持不變（本輪未修改`scripts/sync_land_price_dataset.py`或任何
checksum演算法函式）。

## 46. Golden Case最終複查

```
ExpropriationCaseProvider.status = NOT_FOUND_IN_DATASET
ExpropriationCaseProvider.record_count = 9437
LandPriceProvider.announced_land_current_value = 57305（元/M²）
LandPriceProvider.announced_land_current_value_status = AVAILABLE
LandPriceProvider.record_count = 25361（金山區）
```

業務結果與先前所有輪次完全一致，`LandPriceProvider`完全未被本輪
變更觸及。

## 47. Regression + Core Freeze複查

- `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
  **643 passed**，0 failed，0 error（較Phase API-1.8的627項，新增16項
  Phase API-1.9測試：5項於`test_cadastral_dataset_cache.py`、11項於
  `test_expropriation_case_provider.py`）。
- Core Freeze 36檔案SHA-256組合雜湊：
  - BEFORE：`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  - AFTER：`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`
  - **完全一致，`CORE_FREEZE_VIOLATION = NO`**（本輪所有變更侷限於
    `domain/models.py`（additive）、`providers/cadastral_dataset_
    cache.py`、`providers/expropriation_case_provider.py`、
    `scripts/sync_expropriation_dataset.py`、對應測試檔案與文件，
    未觸及engine/、document extraction core、data/rules/、schemas/）。

## 48. Phase API-1.9 Release Gate

```
EXPROPRIATION_MULTI_RECORD_AUDIT = PASS
PARCEL_MULTI_EVENT_KEYS = 379
EXACT_SOURCE_DUPLICATES = 62
MULTI_MATCH_LOOKUP = PASS
ALL_SOURCE_EVENTS_PRESERVED = PASS
FIRST_MATCH_SILENT_LOSS = NO
DETERMINISTIC_MATCH_ORDER = PASS
SINGLE_MATCH_BACKWARD_COMPATIBILITY = PASS
COLLECT_DATA_MULTI_MATCH = PASS
GOLDEN_CASE_EXPROPRIATION = NOT_FOUND_IN_DATASET
GOLDEN_CASE_LAND_PRICE = 57,305 元/M²
ATOMIC_SNAPSHOT_CONTRACT = PASS
CANONICAL_V3_CONTRACT = PASS
CORE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 643 passed, 0 failed（不含
  test_phase5_golden_pipeline.py，依使用者指示排除）
PHASE_API_1_9_RELEASE_READY = YES
```

本輪未觸碰TGOS、NLSC、FacilityProvider、ComparableProvider或
Multi-Jurisdiction Runtime Rules，未新增任何新的外部API，符合使用者
明確之範圍限制。

## 49. OFFICIAL_CADASTRAL_EVIDENCE_PIPELINE = FROZEN

Phase API-1.9全部Release Gate條件通過，正式標記凍結，涵蓋：

- **Cadastral identifier normalization**（Phase API-1.5：
  `providers/cadastral_identifier.py`，`price_segment_code`與
  `section_name`永不混淆之解析contract）
- **Expropriation snapshot**（Phase API-1.5～1.9：filter-free全量同步，
  9,437筆，`scripts/sync_expropriation_dataset.py`）
- **Land price snapshot**（Phase API-1.5～1.8：large-page全量同步，
  1,153,450筆／29行政區，`scripts/sync_land_price_dataset.py`）
- **Atomic staging**（Phase API-1.8：STAGING→validation→atomic
  promote→CURRENT，Provider絕不讀取partial資料）
- **Last-known-good**（Phase API-1.8：任何sync失敗皆不影響既有CURRENT
  snapshot）
- **Versioned checksum**（Phase API-1.7～1.8：LEGACY_V1／CANONICAL_V2／
  CANONICAL_V3三版並存，皆可獨立驗證，絕不偷偷覆寫已發布演算法）
- **Multi-record expropriation semantics**（Phase API-1.9：
  `lookup_expropriation_matches()`＋`ExpropriationCaseMatch`／
  `match_count`／`matches`／`raw_match_count`／`distinct_event_count`，
  PARCEL_MULTI_EVENT與EXACT_SOURCE_DUPLICATE正式區分，絕不first-match
  遺失真實官方紀錄）
- **Evidence provenance**（全程：checksum/record_count/downloaded_at/
  schema_version/query_parameters/authoritative_status/requires_
  manual_review，供人工覆核可追溯來源）

此凍結範圍內之程式碼（`providers/cadastral_identifier.py`、
`providers/cadastral_dataset_cache.py`、
`providers/expropriation_case_provider.py`、
`providers/land_price_provider.py`、`scripts/sync_expropriation_
dataset.py`、`scripts/sync_land_price_dataset.py`、`domain/models.py`
中對應之Evidence models）自此輪起視為**穩定基線**，未來如需變更，
應視為獨立新輪次並重新走過完整驗證流程（Core Freeze SHA-256比對、
Golden Case複查、Regression），而非本凍結宣告範圍內之隨意修改。

**本輪至此停止，不自動開始Phase API-2。**
