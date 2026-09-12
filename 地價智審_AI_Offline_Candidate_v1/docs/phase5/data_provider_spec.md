# Phase 5 — Data Provider Spec

## 目標

將 Phase 4 僅能吃 Mock JSON 的系統，擴充為具備「真實資料來源抽象」的架構：
七個 Provider 介面 + 七個 Mock 實作。所有 Mock 數值取自已於 Phase 1-4 反覆驗證
之 Golden Case（案號1140901-99-001），**未自行捏造任何數值**。

## 介面設計（`providers/base.py`）

```python
class ProviderContext:
    case_no, city, district, segment_code, parcel_id, center_coordinate

class DataProvider(abc.ABC):
    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]
```

`fetch()` **絕不拋例外**代表「查無資料」；查無資料時回傳
`NormalizedDataPoint(value=None, confidence="UNKNOWN", notes="...")`，
交由呼叫端（`FormCompletionEngine`／未來的 Cross-form Engine）決定如何處理
（標記 `MANUAL_REVIEW_REQUIRED`），Provider 本身不做業務判斷。

## NormalizedDataPoint 欄位（`domain/models.py`）

| 欄位 | 說明 |
|---|---|
| `field` | 對應 `schemas/field_dictionary.json` 之 `field_id` |
| `value` | 數值/文字/布林，`None`表示未知（絕不捏造） |
| `unit` | 單位，類別型欄位為`None` |
| `source` | 人類可讀來源說明 |
| `source_type` | `"Mock"` / `"官方提供資料"` / `"GovernmentOpenData"` / `"UNKNOWN"` 等 |
| `coordinate` | `Optional[Coordinate]`；本階段全數為`None`（見下方座標說明） |
| `confidence` | `"高"` / `"中"` / `"低"` / `"UNKNOWN"` |
| `retrieved_at` | 查詢時間戳 |
| `notes` | 補充說明，`UNKNOWN`時**必填**解釋原因 |

## 七個 Provider 與 field_dictionary.json 類別對照

| Provider | 對應類別 | 欄位數（本次Mock覆蓋） |
|---|---|---|
| `MockLandUseProvider` | 土地使用管制／自然條件／其他影響因素 | 16 |
| `MockRoadProvider` | 交通運輸（道路子集：主要道路、區段內道路平均寬度、道路規劃闢建程度） | 4 |
| `MockTransportationProvider` | 交通運輸（大眾運輸子集：大型車站、站牌、交流道、接近聚落/運銷/消費市場程度） | 11 |
| `MockPublicFacilityProvider` | 公共建設(設施清冊) | 30 |
| `MockSpecialFacilityProvider` | 特殊設施（電業氣體燃料＋殯葬設施） | 18 |
| `MockEnvironmentalProvider` | 環境污染＋廢棄物處理 | 24 |
| `MockCommercialActivityProvider` | 工商活動 | 18 |

**合計 121 筆 NormalizedDataPoint**（66筆有值、55筆UNKNOWN，比例反映Golden Case
原始表單本身即有多處欄位留空之事實，非Provider實作缺陷）。

## 座標（Coordinate）政策

官方 Source（查估書表範本.pdf、評價基準明細表範例.pdf）**僅提供設施名稱與
距離數字**（如「金山第一公墓...距80M」），**未提供任何座標**。因此本階段
全部 Mock Provider 之 `coordinate` 欄位皆為 `None`，並於 `notes` 誠實註明
「Sources未提供座標」，**不自行查詢或推算座標值來填補**。

`MockTransportationProvider` 額外提供 `demo_geo_engine_usage()` 方法，示範
「若未來取得真實座標」時應如何呼叫 `GeoDistanceEngine`——此方法**不被
`fetch()`呼叫**，僅作為未來真實資料源介接時的參考範例，使用之錨點座標為
新北市金山區之真實、可查證座標（Wikipedia來源，見`geo_engine_spec.md`），
非虛構值。

## 可擴充性

新增真實資料源（如政府開放資料API）時，僅需新增一個實作
`DataProvider.fetch()` 的類別，`FormCompletionEngine`（Phase 6起）與
`ProviderContext`介面完全不需修改，符合Adapter Pattern可插拔設計原則。

## 測試

`tests/test_phase5_golden_pipeline.py::TestDataProviders`（3項）：
- 七個Provider皆可實例化並成功`fetch()`
- 查無資料時回傳`UNKNOWN`而非捏造值，且`notes`必填
- 每筆NormalizedDataPoint欄位結構完整
