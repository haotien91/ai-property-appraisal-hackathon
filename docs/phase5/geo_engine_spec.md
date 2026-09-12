# Phase 5 — Geo Distance Engine Spec

## 官方依據（承接 Phase 2 distance_rules.md）

1. **量測起點**：表1區域因素以「本區段中心點」為起點；表4個別因素以
   「宗地中心點」為起點（作業手冊 p.22, p.28-32，高信心）。
2. **量測演算法**：殯葬/嫌惡設施類確認採**直線距離**（逐字稿兩次口頭覆述
   ＋作業手冊p.24焚化爐範例）；學校/市場等「通達性」設施，手冊僅建議採
   路線距離，但**未提供可實作之路網演算法**；其餘設施類別演算法選擇
   Sources未明確統一說明。

## 本階段實作範圍

| 演算法 | 實作狀態 | 理由 |
|---|---|---|
| 直線距離（Haversine） | **已實作，deterministic** | 純數學公式，官方確認為殯葬/嫌惡設施類之正確演算法，且不需外部服務 |
| 路線距離（路網） | **未實作，一律回傳MANUAL_REVIEW_REQUIRED** | 需要真實路網/GIS路由服務；本環境無網路存取權限可呼叫地圖API；Sources亦未提供可實作之精確演算法 |

依 Phase 5 指示「不同距離算法只能依Source確認後實作，不能確認時
MANUAL_REVIEW_REQUIRED」，本引擎**拒絕**以直線距離靜默替代路線距離請求，
避免誤植未確認之演算法。

## Haversine 實作與獨立驗證

```
distance = 2 × R × asin(√(sin²(Δlat/2) + cos(lat1)·cos(lat2)·sin²(Δlon/2)))
R = 6,371,000 公尺（WGS84球體近似半徑）
```

**驗證方法**：由於Sources未提供任何設施之精確座標，無法直接用Golden Case
反推驗證Haversine實作本身之正確性。本階段改採**獨立數學建構法**驗證：

1. 取一個真實、可查證之錨點座標——新北市金山區中心點
   （25.23611°N, 121.61750°E，來源：Wikipedia「Jinshan District, New Taipei」
   條目，非虛構）。
2. 以標準正向大地測量公式（forward geodesic），數學上精確建構一個位於
   錨點正北700公尺處之目標點。
3. 呼叫`straight_line_distance()`計算兩點距離，驗證結果與建構時之700公尺
   誤差 < 0.5公尺。

**結果**：誤差 = 0.0 公尺，Haversine實作獨立驗證正確
（`tests/test_phase5_golden_pipeline.py::TestGeoDistanceEngine::test_haversine_matches_constructed_known_distance`）。

此方法之優點：不依賴任何虛構資料，錨點為真實座標，目標點為數學可驗證之
精確建構值，測試本身不依賴Golden Case既有的、可能受限於已知案例的距離值。

## DistanceResult 追溯欄位

依 Phase 5 「保存：origin, destination, method, distance, unit, source」要求：

```python
class DistanceResult(BaseModel):
    origin: Coordinate
    origin_description: str
    destination: Coordinate
    destination_description: str
    method: DistanceMethod          # straight_line | route | unknown
    distance_m: Optional[float]
    unit: str = "M"
    source: str
    status: str                     # COMPLETED | MANUAL_REVIEW_REQUIRED
    notes: Optional[str]
    computed_at: datetime
```

## 已知限制

1. 座標缺失時（Sources未提供）一律回傳`MANUAL_REVIEW_REQUIRED`，不猜測。
2. 路線距離全面回傳`MANUAL_REVIEW_REQUIRED`，非本階段之實作缺陷，而是
   環境限制（無路網API存取權限）與Sources未確認演算法的雙重誠實反映。
3. 「一般設施最近距離」與「路線距離」之用語對應關係，承接Phase 3
   source_anomalies.md之未解問題，本階段未新增解決，僅在演算法選擇上
   採最保守（拒絕猜測）之處理方式。

## 測試

`tests/test_phase5_golden_pipeline.py::TestGeoDistanceEngine`（4項）：
Haversine正確性、座標缺失安全失敗、路線距離請求安全失敗、`compute()`
分派邏輯正確性。
