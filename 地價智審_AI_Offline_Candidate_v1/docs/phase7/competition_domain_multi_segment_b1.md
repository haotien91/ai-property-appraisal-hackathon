# COMPETITION-DOMAIN-MULTI-SEGMENT-B1

前置 Gate：`FORMAL_COMPETITION_PACKAGE_MIGRATION_AUDIT=PASS`、
`SHULIN_RULE_SOURCE_TRUTH_GATE_A1=PASS`、`SHULIN_COMPETITION_RULE_PACK_A2=PASS`。

本輪目的：把系統從 case-level／single-segment 假設，遷移為
`case_id + segment_code` 為核心識別方式，讓正式競賽案件契約
（base_segment=P001-00, comparison_1=P002-00, comparison_2=P003-00,
comparison_3=P004-00）的 4×Table3（表3 地價區段勘查表）資料能同時存在，
互不覆寫。**只**處理 Multi-Segment Domain / Table3 data architecture /
segment-scoped factors / segment-scoped facility confirmation；**未**開始
Table5-1 calculation、Table4 migration、Official PDF V2、Frontend
redesign、AWS deployment。

## Segment Domain Model

`domain/models.py` 新增：

- `SegmentRole`（`BASE_SEGMENT` / `COMPARABLE_SEGMENT_1` / `_2` / `_3`）。
- `CompetitionSegment`（segment_code / segment_role / comparison_index /
  parcel_ids / district / land_use_type），內建 `model_validator` 檢查
  `segment_role` 與 `comparison_index` 必須一致（BASE 不得帶
  comparison_index；COMPARABLE_SEGMENT_N 必須帶 comparison_index=N）。
- `CompetitionSegmentMap`（case_id / base_segment / comparables[]），
  `model_validator` 檢查 base_segment 角色正確、comparables 之
  segment_code／comparison_index 皆不重複。

**Role mapping 明確性（Task 1）**：`segment_role` 永遠來自呼叫端（fixture
script／未來前端表單）在建立案件時明確提供的欄位，程式碼**完全沒有**任何
解析 `segment_code` 字串前綴（如 `"P001"`）來猜測角色的邏輯——
`tests/test_competition_domain_multi_segment_b1.py::
test_segment_role_never_inferred_from_string_prefix` 用一組完全不像
`P00N-00` 格式的 segment_code（`ZZZ-99`/`QQQ-01`）驗證角色解析純粹依賴
顯式欄位。

## Storage Model

新模組 `backend/handlers/competition_segments.py`：

- `SEGMENTS_SK = "SEGMENTS"`：一個 case 一筆記錄，存放整個
  `CompetitionSegmentMap`。
- `factors_sk(segment_code)`：`None`（每個既有 legacy 呼叫端的預設值）
  → 原本的 `"FACTORS"` SK，與 B1 之前完全相同；有值 → 各自獨立的
  `"FACTORS#<segment_code>"` DynamoDB item。
- `resolve_segment(case_id, segment_code)`：只在**該 case 自己的**
  segment map 內查找，找不到（無論是根本不存在的代碼，或屬於別的 case）
  一律 `raise InvalidSegmentCodeError`——不需要額外的跨 case 檢查，
  `case_store.py` 既有的 `PK=CASE#<case_no>` 分區設計本身就保證了
  這一點（Task 15）。

`FACILITY_CONFIRMATION` 記錄（`backend/handlers/
facility_confirmation_repository.py`）：`_sk(subtype, segment_code=None)`
——`None` → 原本的 `"FACILITY_CONFIRMATION#<subtype>"`；有值 →
`"FACILITY_CONFIRMATION#<segment_code>#<subtype>"`，四個 segment 的同一
subtype 各自是獨立 item。`get_or_refresh_candidates`／`confirm`／
`reject` 皆新增可選 `segment_code` 參數，貫穿到 `_sk()`。

## Legacy Compatibility（Task 16）

`rule_profile_id`（A2 Final Gate 已建立的先例）同樣的設計模式：**每個新
參數都是 EXPLICIT + OPTIONAL，預設 None**。

- `cases.create_case()`：`segments` body 欄位省略 → 完全不寫入任何
  `SEGMENTS` 記錄，行為與 B1 之前逐字相同。
- `collect_data.collect_data()`：pathParameters 沒有 `segment_code` →
  使用 `meta["district"]`／`meta["segment_code"]`（案件層級，B1 之前的
  唯一來源）與裸 `"FACTORS"` SK，完全不變。
- `facility_confirmation.py` 三個 handler：同樣，沒有 `segment_code`
  → 裸 `"FACILITY_CONFIRMATION#<subtype>"` SK，完全不變。

`tests/test_competition_domain_multi_segment_b1.py::
TestLegacyCompatibility` 兩項測試分別驗證：(1) 一個完全沒有 segment map
的 legacy 金山案件，collect_data／facility_confirmation 全流程行為不變；
(2) 同一個 Shulin 案件同時使用 legacy（無 segment_code）與 segment-scoped
呼叫，兩組記錄各自獨立存在，互不覆寫、互不污染。

## Competition Provided Fixed Values（Task 4）

`domain/models.py::SourceType` 新增 `COMPETITION_PROVIDED_FIXED`
（"競賽題目提供固定值"）。`collect_data.py` 新增請求 body 欄位
`competition_provided_factors`（清單，FactorInput 形狀），持久化到 FACTORS
記錄的**獨立**鍵 `competition_provided_factors`——與 Provider 產生的
`points`／`regional_base_factors` 完全是不同的 dict key，從架構上就不可能
互相覆寫：Provider 端若對同一 field_id 產生不同數值，只會落在
`points`／`regional_base_factors`（REFERENCE_EVIDENCE 性質），
`competition_provided_factors` 永遠原封不動。`confidence` 欄位固定填入
"確定" 這種人類可讀標籤，而非 AI 產生的百分比信心分數，避免被誤認為
可被 AI 覆寫的欄位。

## 4×Table3 Fixtures（Task 5）

`data/competition_cases/shulin_residential_2026/segment_table3_fixtures.py`
——4 個真實競賽 fixture（P001-00／P002-00／P003-00／P004-00），資料來源
**只有** `data/sources/competition/shulin_residential_2026/題目.pdf`（表3
地價區段勘查表，共4頁，每頁一個 segment），**未使用**任何 Jinshan Golden
Case 數值。每個數值皆先用 PyMuPDF 文字抽取、再逐頁渲染成圖片肉眼複核
（本專案既有的「文字抽取對齊式表格不可靠」紀律）。

| 欄位 | P001-00(base) | P002-00(comp1) | P003-00(comp2) | P004-00(comp3) |
|---|---|---|---|---|
| 都市計畫內外 | 都市計畫內 | 都市計畫內 | 都市計畫內 | 都市計畫內 |
| 使用分區 | 第一種住宅區 | 第一種住宅區 | 第一種住宅區 | 第一種住宅區 |
| 建蔽率 | 50% | 50% | 50% | 50% |
| 容積率 | 260% | 200% | 260% | 260% |
| 有無禁止/限制建築 | 無/無 | 無/無 | 無/無 | 無/無 |
| 主要道路(名稱/寬度) | 八德街/28M | 樹人街/7M | 東榮街/10M | 潭興街/10M |
| 區段內道路平均寬度 | 12M | 6M | 7M | 7M |
| 區段內道路規劃闢建程度 | 大部分規劃及闢建 | 部分規劃及闢建 | 全部規劃及闢建 | 全部規劃及闢建 |
| 日照/景觀/傾斜度/排水/地勢 | 充分/視野景觀尚可/平均坡度未滿5度/普通完善/極平坦堅硬 | 同左 | 同左 | 同左 |
| 建築密度(描述性metadata，非29因素之一) | 60% | 70% | 70% | 50% |
| 土地利用現況 | 商業用+住宅用(唯一雙勾) | 住宅用 | 住宅用 | 住宅用 |

題目.pdf 對每個 segment 的學校/市場/公園/大型車站/站牌/交流道/殯葬/
廢棄物處理/電業設施/環境污染/工商活動/其他影響因素等**全部留白未勾選**
——誠實地不予杜撰，這些欄位在 fixture 中完全不存在（而非填入猜測值或
預設值），未來需要人工現勘或 Provider 補值時才會產生。

## Segment-Scoped Facility Confirmation（Task 6/7/8/9）

Task 6/7 已如上「Storage Model」所述完成 SK 遷移，legacy 記錄完全不受
影響（同一份程式碼、同一個 repository class，只是 `_sk()` 的組合方式
依 `segment_code` 是否為 `None` 分支）。

Task 8/9（candidate/confirm/reject/stale isolation）驗證：見下方 Test
Results 的 C/D/E/F 四項——同一 subtype（substation）在 4 個 segment 各自
獨立存在不同 candidate；confirm P001-00 不影響 P002-00 仍為 PENDING；
reject P003-00 不影響 P001-00/P002-00/P004-00 仍為 CONFIRMED；只刷新
P004-00 的證據使其 CONFIRMED 記錄變 stale，P001-00 的 CONFIRMED 記錄
`stale` 仍為 `False`。這些隔離性完全來自「每個 segment+subtype 都是
獨立 DynamoDB item」這個儲存層設計，repository 本身沒有任何跨 segment
的比對/污染邏輯。

```
SEGMENT_SCOPED_CANDIDATE_ISOLATION=YES
```

## Provider Integration（Task 12）

`providers/base.py::ProviderContext` 本就攜帶 `segment_code`／`district`
（Phase 1 REQ-006/007 既有設計，非本輪新增）。`collect_data.py` 本輪修改
為：當 pathParameters 帶 `segment_code` 時，改用該 segment 自己的
`district`（來自 `CompetitionSegmentMap`，而非案件層級 `meta`）與
`segment_code` 本身建構 `ProviderContext`，所以每個 Provider 執行時
`ctx.segment_code` 就是目標 segment，不再是案件層級的單一值——證據/
candidate/distance 因此可追溯到 P001/P002/P003/P004（透過 `ctx` 本身
與各自獨立的 `FACTORS#<segment_code>` 儲存位置）。**已知限制**：NLSC
官方座標解析（`_resolve_nlsc_official_coordinate_evidence`）與都市計畫
邊界自動解析（`_resolve_urban_plan_result`）兩個輔助函式本輪**未**同步
改為 per-segment（兩者本輪保持讀取案件層級 `meta`），留給後續 Phase 視
需要調整——不影響本輪的核心交付（Table3 資料儲存層的 segment 隔離）。

## No Premature Table5-1 Calculation（Task 13）

本輪未觸碰 `AdjustmentEngine`/`CalculationEngine`/`RuleEngine`，未對
P001/P002/P003/P004 之間的 Table3 因素進行任何比較修正率計算——4 個
segment 的 Table3 資料目前各自獨立儲存、各自可被 grade（若未來 Shulin
CaseRulePackage 被 CONFIRMED），但「比準地 vs 比較標的」跨 segment 比較
與 Table5-1 輸出留給下一輪 `TABLE51-THREE-COMPARABLE-C1`。

## API Contract（Task 14）

新增 `backend/handlers/segments.py`：

```
GET /api/cases/{id}/segments               -> get_segments
GET /api/cases/{id}/segments/{segment_code} -> get_segment
```

既有 3 個 facility handler（`backend/handlers/facility_confirmation.py`）
新增可選 `segment_code` pathParameter：

```
GET  /api/cases/{id}/facility-candidates                                (legacy, unchanged)
GET  /api/cases/{id}/segments/{segment_code}/facility-candidates        (NEW)
POST /api/cases/{id}/facility-candidates/{subtype}/confirm              (legacy, unchanged)
POST /api/cases/{id}/segments/{segment_code}/facility-candidates/{subtype}/confirm  (NEW)
POST .../{subtype}/reject                                               (同上，兩種路徑皆支援)
```

`cases.create_case()` 新增可選 body 欄位 `segments`（Task 2 的
base_segment/comparables[] 形狀）；`collect_data()` 新增可選
pathParameter `segment_code`。

**已知限制**：`infra/template.yaml`（API Gateway/Lambda route 註冊）
本輪**未**修改——這是純 IaC 設定變更，不影響任何程式邏輯或測試，且本輪
明確禁止 AWS deployment，故留給實際佈署前的一個小型 follow-up。

## Invalid / Cross-Case Segment Safety（Task 15）

`InvalidSegmentCodeError`（`competition_segments.py`）統一涵蓋「代碼根本
不存在」與「代碼屬於別的 case」兩種情況——因為 `resolve_segment()` 永遠
只在傳入的 `case_id` 自己的 segment map 內查找，`case_store.py` 的
`PK=CASE#<case_no>` 分區設計已經讓兩種情況殊途同歸。`collect_data.py`／
`facility_confirmation.py`／`segments.py` 三處皆已接上此例外 →
`400 INVALID_SEGMENT_CODE`。

## Test Modification / New Tests（Task 17）

`tests/test_competition_domain_multi_segment_b1.py`（16 tests，A-J 全部
場景 + 額外的角色驗證/更新隔離/legacy-vs-segment 共存測試），完全新增，
未修改任何既有測試檔案的斷言。

## Test Results（Task 18）

```
python -m pytest tests -q --ignore=tests/test_phase5_golden_pipeline.py > full_regression_b1.log 2>&1
echo "PYTEST_EXIT_CODE=$?" >> full_regression_b1.log
```

（`tests/test_phase5_golden_pipeline.py` 排除原因與 SHULIN-COMPETITION-
RULE-PACK-A2-FINAL-GATE-1 完全相同：模組層級 `import weasyprint` 在此
Windows 環境缺少 `libgobject-2.0-0`，會讓整個 pytest session 於
collection 階段中斷，而非僅該檔案本身失敗——與本輪任何變更無關。）

實際結果見本輪 FINAL REPORT 之 `TESTS_PASSED`/`TESTS_FAILED`/
`TESTS_SKIPPED`/`PYTEST_EXIT_CODE`。

## Remaining Blockers / Known Scope Limitations

1. `infra/template.yaml` 尚未註冊新 route（純 IaC，見上）。
2. ~~NLSC 官方座標解析／都市計畫邊界自動解析兩個輔助函式尚未 per-segment
   化~~ — **已於 B1-FINAL-GATE-1 解決**，見下方新增章節。
3. 4 個 segment 的其餘 14 個 Table3 因素（學校/市場/公園/大型車站/站牌/
   交流道/殯葬/廢棄物處理/電業設施/環境污染/工商活動/其他影響因素）在
   題目.pdf 中皆留白，本輪誠實地未填入——留待未來人工現勘或 Provider
   查詢補齊，這是資料本身的限制，不是架構限制。
4. Table5-1 跨 segment 比較修正率計算刻意留給下一輪
   `TABLE51-THREE-COMPARABLE-C1`（Task 13 本輪明確禁止）。

---

# COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1 追加驗證

B1 主體完成後，正式宣告 PASS 前發現並補上三項 safety gap。

## Task 1: Competition 不得走 legacy facility key

新增 `competition_segments.is_competition_case(case_id, meta=None)`：一個
case 只要「有自己的 CompetitionSegmentMap」**或**「meta 記錄的
`rule_profile_id` 屬於 `COMPETITION_RULE_PROFILE_REGISTRY`」，就是
Competition case。`facility_confirmation.py` 新增 `_segment_gate()`
共用檢查（三個 handler 皆呼叫）：

- `segment_code` 有值 → 照舊解析驗證（Task 15 的 `INVALID_SEGMENT_CODE`）。
- `segment_code` 省略 且 `is_competition_case()` 為真 → 立即
  `400 SEGMENT_CODE_REQUIRED`，**不** fallback 到
  `FACILITY_CONFIRMATION#<subtype>`。
- `segment_code` 省略 且非 Competition case → 完全比照 B1 之前的 legacy
  行為（Task 7 保留）。

```
COMPETITION_FACILITY_SEGMENT_CODE_REQUIRED=YES
COMPETITION_LEGACY_FACILITY_FALLBACK_BLOCKED=YES
```

原本 B1 測試檔 `test_competition_domain_multi_segment_b1.py::
TestLegacyCompatibility::test_legacy_and_segmented_records_never_collide`
的舊假設（Shulin case 可以省略 segment_code 呼叫 legacy path）已按此輪
指示更正為驗證新的、更嚴格的行為（省略 segment_code 現在必須被擋下）。

## Task 2: No legacy competition contamination

新測試（`test_competition_domain_multi_segment_b1_final_gate.py::
TestNoLegacyContamination`）：對一個 Shulin Competition case，故意直接用
`case_store.put_record()` 塞入一筆 legacy 格式的
`FACILITY_CONFIRMATION#substation`（CONFIRMED，帶一個刻意誤導的候選人
名稱「LEGACY污染變電所」），再對 P001-00/P002-00/P003-00/P004-00 分別呼叫
segment-scoped `get_facility_candidates`。四個 segment 讀到的候選人皆為
各自 FACTORS 記錄衍生的正確值（P00N變電所），從未讀到 legacy 記錄，且四個
segment 的初始狀態皆為 PENDING（不是被 legacy 記錄污染成 CONFIRMED）。
legacy 記錄本身完全未被觸碰。

```
COMPETITION_SEGMENT_READS_LEGACY_CONFIRMATION=NO
```

## Task 3/5: Segment-scoped geographic context

逐一追蹤 `collect_data.py` 的座標解析鏈：

| 函式 | 原本 | 本輪修正 |
|---|---|---|
| `_resolve_submitted_coordinate_evidence(body)` | 只讀 `body`，本就與 case/segment 層級無關 | 不需修改——每次 collect_data 呼叫本就有自己的 body |
| `_resolve_nlsc_official_coordinate_evidence(case_no, meta)` | 永遠用 `meta["district"]`／`meta["segment_code"]`／`meta.get("base_parcel_id")` | 新增可選 `segment=` 參數；有值時改用該 segment 自己的 `district`／`segment_code`／`parcel_ids[0]`，`None` 時完全不變 |
| `_resolve_nominatim_coordinate_evidence(meta)` | 永遠用 `meta.get("district")` | 新增可選 `segment=`，同上邏輯（`segment_scope` 目前無 per-segment 對應欄位，仍取自 meta，已誠實記錄為已知限制） |
| `_resolve_urban_plan_result(coordinate)` | 只吃座標，本就與 case/segment 層級無關 | 不需修改 |
| 主要 `ProviderContext`（`ctx`） | `district`/`segment_code` 已於 B1 主體改為 per-segment；`parcel_id` 當時仍固定用 `meta.get("base_parcel_id")` | 本輪補上：`parcel_id` 改用 `effective_parcel_id`（segment 自己的 `parcel_ids[0]`，`None` 時退回原本的 `meta.get("base_parcel_id")`） |

`collect_data()` 呼叫 `_resolve_coordinate_evidence_bundle(case_no, meta,
body, segment=segment)`，將已解析出的 `CompetitionSegment` 物件（或
`None`）貫穿到上述兩個 real-mode 專屬函式。**未 hardcode 任何
P001/P002/P003/P004 座標**——全部透過 generic `segment.district`／
`segment.parcel_ids[0]` 讀取，legacy（`segment=None`）呼叫端行為
完全不變（`tests/test_collect_data_nlsc_integration.py` 全數維持通過，
只調整了其中 3 個 monkeypatch lambda 的參數簽章以接受新的可選
`segment` 關鍵字參數，斷言內容未變）。

```
P001_COORDINATE_CONTEXT_SEGMENT_SCOPED=YES
P002_COORDINATE_CONTEXT_SEGMENT_SCOPED=YES
P003_COORDINATE_CONTEXT_SEGMENT_SCOPED=YES
P004_COORDINATE_CONTEXT_SEGMENT_SCOPED=YES

NLSC_COORDINATE_RESOLUTION_SEGMENT_SCOPED=YES
NOMINATIM_COORDINATE_RESOLUTION_SEGMENT_SCOPED=YES
URBAN_PLAN_RESOLUTION_SEGMENT_SCOPED=YES（本就只吃座標，不需 per-segment 特化）
```

## Task 4: Coordinate isolation runtime test

`test_f_four_segment_coordinates_isolated`：對 P001-00/P002-00/P003-00/
P004-00 分別提交 4 組不同 `center_coordinate`（A/B/C/D，緯度各異）並各自
呼叫 segment-scoped `collect_data`，讀回 `FACTORS#P00N` 各自的
`coordinate_evidence.submitted`／`analysis_coordinate`，確認 P00N 只帶
自己那組座標，四組座標互不相同、互不交叉。

```
SEGMENT_COORDINATE_ISOLATION_VERIFIED=YES
```

## Task 5: NLSC official coordinate provenance（架構驗證，非 live 呼叫）

`NLSC_CAD_API_ENABLED`／`CAD001_AUTH_CONTRACT_STATUS` 本輪仍維持預設關閉
（無真實憑證），故：

```
LIVE_NLSC_SEGMENT_COORDINATE_VERIFIED=NO
```

但架構驗證（`test_g_nlsc_resolver_receives_correct_segment_identity`）：
monkeypatch `RealOfficialParcelCoordinateProvider` 以攔截真正傳入
`query_parcel_coordinate()` 的 `ProviderContext`（完全不觸碰網路），分別
對 P001-00／P002-00 呼叫 `_resolve_nlsc_official_coordinate_evidence`，
證實兩次呼叫收到的 `ctx.parcel_id`／`ctx.segment_code` 確實不同、且皆非
案件層級的 base parcel 占位值——每個 segment 真的用自己的 parcel identity
發送請求，而非全部共用比準地的。另一測試確認 `segment=None`（legacy）時
完全退回案件層級 meta 的行為，未受影響。

```
NLSC_REQUEST_USES_SEGMENT_IDENTITY=YES
```

## Task 6: Competition Fixed Value 真實 runtime precedence

`backend/handlers/case_reconstruction.py` 新增
`_apply_competition_provided_precedence()`：`build_case_and_regional_
factors()`（complete_form.py／review.py 實際餵給 Grade/Adjustment Engine
的規劃地區因素清單的唯一來源）現在會將 `competition_provided_factors`
（COMPETITION_PROVIDED_FIXED）疊加在 `regional_base_factors`
（Provider-derived）之上——同一 field_id 若兩邊都有值，Provider 的版本會
從「餵給 Grade Engine 的清單」中被剔除（但仍完整保留在
`regional_base_factors`／`points` 原始儲存位置，供稽核用，只是不再被
grading 使用），competition_provided_factors 的值永遠勝出。

測試（`test_h_fixed_28_wins_over_provider_99_and_supplemental_123`）：
`competition_provided_factors`=28、`regional_base_factors`=99（模擬
Provider reference）、`user_submitted_factors.base_parcel_factors`=123
（模擬「AI/使用者補充值」）三者並存於同一份 factors_record，呼叫真實的
`build_case_and_regional_factors()` 後，回傳的 regional_base 清單中
`regional_main_road_width` 唯一一筆值為 28。123 完全不需要特別攔截——
`build_case_and_regional_factors()` 本就從未讀取 `user_submitted_
factors` 來源建構 regional 因素清單，所以它在架構上本來就不可能覆寫任何
regional 因素。

```
COMPETITION_FIXED_VALUE_STORAGE_ISOLATED=YES
COMPETITION_FIXED_VALUE_RUNTIME_PRECEDENCE=YES
```

## Task 7: Legacy regression

`test_legacy_full_round_trip_unaffected`：legacy 金山案件（無 segments、
無 rule_profile_id）在完全不提供 `segment_code` 的情況下，
collect_data → get_facility_candidates → confirm → reject 全流程正常
運作，confirm 若無候選人正確回傳 `FACILITY_CANDIDATE_EMPTY`（400）而非
`SEGMENT_CODE_REQUIRED`——證明 Competition gate 完全沒有誤傷 legacy case。

```
LEGACY_NO_SEGMENT_PATH_PRESERVED=YES
```

## Test Results（Task 8/9）

新增 `tests/test_competition_domain_multi_segment_b1_final_gate.py`
（11 tests，涵蓋 Task 8 A-H 全部場景）。既有 `tests/test_competition_
domain_multi_segment_b1.py` 一項測試（`test_legacy_and_segmented_
records_never_collide`）依本輪指示更正為驗證新的、更嚴格行為。
`tests/test_collect_data_nlsc_integration.py` 三個 monkeypatch lambda
簽章調整以接受新的可選 `segment` 參數（斷言內容未變）。

實際 `TESTS_PASSED`/`TESTS_FAILED`/`TESTS_SKIPPED`/`PYTEST_EXIT_CODE`
見本輪 FINAL REPORT。
