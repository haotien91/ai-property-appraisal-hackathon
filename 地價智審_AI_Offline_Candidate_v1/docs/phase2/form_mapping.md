# Phase 2 — Form Mapping（表單欄位對照）

> 本文件整理表1／表5-2／表4三張核心表之間的欄位對照關係。所有 `standard_field`
> 均對應 `schemas/field_dictionary.json` 中之 `field_id`，避免以中文字串作為系統
> Key（依主專案指示第6節要求）。凡標示【待確認】者，沿用 Phase 1 結論，不自行
> 補齊。

## 對照原則

1. **rule_required = True**：表示此欄位轉換須經過 Rule Engine（依評價基準明細表
   deterministic 判定），不可由 LLM 自行判定優劣等級。
2. **calculation_required = True**：表示此欄位轉換須經過 Calculation Engine
   （deterministic 數學運算），不可由 LLM 自行計算數值。
3. 本表僅列出**具代表性**之對照關係（表1→表5-2共27組區域因素、表5-2→表4之關鍵
   總修正數傳遞、表4內部計算鏈之3個關鍵步驟），完整逐欄對照請對照
   `schemas/field_dictionary.json` 之 `category` 欄位分組查閱；表1→表4個別因素
   部分之對照關係，因表4個別因素多數欄位（面積、寬度、深度、道路種類等）並非
   直接承接表1既有欄位，而是另有其獨立資料來源（宗地個別因素清冊、地籍資料，
   此二者屬本次競賽核心三表以外之書表），故本表僅列出少數確有直接欄位承接關係
   者，其餘於 `business_process.md` Step4 中以文字說明資料來源。

## 表1 → 表5-2：區域因素判定對照（27組）＋ 表5-2 → 表4 ＋ 表4內部計算鏈

表1所調查之原始區域資料（如「主要道路寬度18M」），須經 Rule Engine 依當次評價
基準明細表之級距判定，轉換為表5-2之優劣等級。此轉換**不得**由AI自行判斷，且
依作業手冊明文規定：「優劣等級：表5-2內所填載之比準地地價區段與各比較標的地價
區段之優劣等級，應與地價區段勘查表內所調查之基本資料及優劣等級相符」（作業手冊
p.47-48）——此為官方審查checklist之明確檢查點（Phase 1 REQ-023）。

| source_form | source_field | standard_field | datatype | unit | transformation | rule_required | calculation_required | target_form | target_field | evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| 表1 | zoning_inside_outside | zoning_inside_outside | enum[都市計畫內,都市計畫外] | - | 都市計畫內外 -> 優劣等級(2級制: 優/劣) | True | False | 表5-2 | regional_zoning_inside_outside_grade_base | 作業手冊 p.47-48 |
| 表1 | land_use_zone | land_use_zone | string | - | 使用分區 -> 優劣等級(5級制) | True | False | 表5-2 | regional_land_use_zone_grade_base | 作業手冊 p.47-48 |
| 表1 | building_coverage_ratio | building_coverage_ratio | number | % | 建蔽率% -> 優劣等級(5級制,級距見評價基準明細表) | True | False | 表5-2 | regional_building_coverage_ratio_grade_base | 作業手冊 p.47-48 |
| 表1 | floor_area_ratio | floor_area_ratio | number | % | 容積率% -> 優劣等級(5級制,級距見評價基準明細表) | True | False | 表5-2 | regional_floor_area_ratio_grade_base | 作業手冊 p.47-48 |
| 表1 | construction_prohibited | construction_prohibited | enum[有,無] | - | 有無禁止建築 -> 優劣等級(2級制) | True | False | 表5-2 | regional_construction_prohibited_grade_base | 作業手冊 p.47-48 |
| 表1 | construction_restricted | construction_restricted | enum[有,無] | - | 有無限制建築 -> 優劣等級(2級制) | True | False | 表5-2 | regional_construction_restricted_grade_base | 作業手冊 p.47-48 |
| 表1 | main_road_width | main_road_width | number | M | 主要道路寬度M -> 優劣等級(5級制,級距見評價基準明細表) | True | False | 表5-2 | regional_main_road_width_grade_base | 作業手冊 p.47-48 |
| 表1 | segment_avg_road_width | segment_avg_road_width | number | M | 區段內道路平均寬度M -> 優劣等級(5級制) | True | False | 表5-2 | regional_avg_road_width_grade_base | 作業手冊 p.47-48 |
| 表1 | major_station_distance_m | major_station_distance_m | number | M | 大型車站距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_major_station_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | bus_stop_density | bus_stop_density | enum[非常密集,密集,不密集] | - | 站牌密集程度/距離 -> 優劣等級(5級制) | True | False | 表5-2 | regional_bus_stop_proximity_grade_base | 作業手冊 p.47-48 |
| 表1 | interchange_distance_m | interchange_distance_m | number | M | 交流道距離M -> 優劣等級(5級制,直線距離) | True | False | 表5-2 | regional_interchange_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | road_development_level | road_development_level | enum[已完全開發,大部分已完成,已規劃及闢建中,已進行規劃,尚未規劃] | - | 道路規劃闢建程度 -> 優劣等級(5級制) | True | False | 表5-2 | regional_road_development_grade_base | 作業手冊 p.47-48 |
| 表1 | drainage_quality | drainage_quality | enum[排水系統完善不曾淹水,有排水系統不易淹水,有排水系統偶有淹水但排水速度快,有排水系統偶有淹水,無排水系統易淹水] | - | 保排水之良否 -> 優劣等級(5級制) | True | False | 表5-2 | regional_drainage_quality_grade_base | 作業手冊 p.47-48 |
| 表1 | terrain | terrain | enum[該區地勢平坦,該區大部分地勢平坦,該區一半地勢平坦,該區大部分地勢高亢或低窪,該區全部地勢高亢或低窪] | - | 地勢描述 -> 優劣等級(5級制) | True | False | 表5-2 | regional_terrain_grade_base | 作業手冊 p.47-48 |
| 表1 | market_distance_m | market_distance_m | number | M | 市場距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_market_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | park_distance_m | park_distance_m | number | M | 公園距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_park_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | tourism_facility_distance_m | tourism_facility_distance_m | number | M | 觀光遊憩設施距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_tourism_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | parking_lot_distance_m | parking_lot_distance_m | number | M | 停車場地距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_parking_convenience_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | substation_distance_m | substation_distance_m | number | M | 變電所/瓦斯槽距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_utility_facility_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | cemetery_distance_m | cemetery_distance_m | number | M | 墓地/殯儀館/火葬場/納骨塔距離M -> 優劣等級(5級制,直線距離) | True | False | 表5-2 | regional_funeral_facility_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | sewage_plant_distance_m | sewage_plant_distance_m | number | M | 廢棄物處理設施距離M -> 優劣等級(5級制) | True | False | 表5-2 | regional_waste_facility_proximity_grade_base | 作業手冊 p.47-48 |
| 表1 | water_pollution_distance_m | water_pollution_distance_m | number | M | 環境污染距離M -> 優劣等級(5級制) | True | False | 表5-2 | regional_pollution_proximity_grade_base | 作業手冊 p.47-48 |
| 表1 | department_store_distance_m | department_store_distance_m | number | M | 百貨公司距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_department_store_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | financial_institution_distance_m | financial_institution_distance_m | number | M | 金融機構距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_financial_institution_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | entertainment_facility_distance_m | entertainment_facility_distance_m | number | M | 娛樂設施距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_entertainment_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | exhibition_hotel_distance_m | exhibition_hotel_distance_m | number | M | 大型展示中心距離M -> 優劣等級(5級制,最近距離) | True | False | 表5-2 | regional_exhibition_hotel_proximity_grade_base | 作業手冊 p.47-48；距離定義見distance_rules.md |
| 表1 | customer_traffic_volume | customer_traffic_volume | enum[顧客通行量多,顧客通行量稍多,顧客通行量普通,顧客通行量較少,顧客通行量少] | - | 顧客通行量描述 -> 優劣等級(5級制) | True | False | 表5-2 | regional_customer_traffic_grade_base | 作業手冊 p.47-48 |
| 表1 | shop_contiguity | shop_contiguity | enum[80%以上作為店舖,70%以上未滿80%作為店舖,60%以上未滿70%作為店舖,50%以上未滿60%作為店舖,未滿50%作為店舖] | - | 店舖毗連狀態% -> 優劣等級(5級制) | True | False | 表5-2 | regional_shop_contiguity_grade_base | 作業手冊 p.47-48 |
| 表5-2 | regional_total_adjustment | regional_total_adjustment | number | % | 影響地價區域因素總修正數 -> 區域因素調整百分率（逐字相符，非重新計算） | False | False | 表4 | region_adjustment_rate_n | 作業手冊 p.48「(4)」；Golden Case驗證：兩者皆為0.00% |
| 表1 | main_road_width | main_road_width | number | M | 主要道路名稱+寬度 -> 面前道路寬度(比準地) | False | False | 表4 | individual_frontage_road_width_base | 查估書表範本表1/表4欄位對照 |
| 表1 | district | district | string | - | （間接）承接使用分區 | False | False | 表4 | individual_zoning_designation_base | 查估書表範本表1/表4欄位對照 |
| 表4 | land_normal_price_n | land_normal_price_n | number | 元/M2 | ×(1+price_date_adjustment_rate_n)，需保留完整精度不可提前四捨五入 | False | True | 表4 | adjusted_price_n | 作業手冊 p.52-53；本階段獨立驗算，見calculation_dependency.md |
| 表4 | adjusted_price_n | adjusted_price_n | number | 元/M2 | ×(1+region_adjustment_rate_n+individual_adjustment_total_n)，需保留完整精度 | False | True | 表4 | trial_price_n | 作業手冊 p.52-53；本階段獨立驗算，見calculation_dependency.md |
| 表4 | trial_price_n | trial_price_n | number | 元/M2 | 依comparable_weight_n加權平均，最終四捨五入至個位數(REQ-022) | False | True | 表4 | base_parcel_comparison_price | 作業手冊 p.52-53；本階段獨立驗算，見calculation_dependency.md |

## 表1 → 表4：個別因素對照之限制說明

表4個別因素（面積、寬度、深度、道路種類、面前道路寬度、接近學校/市場/公園/車站/
商圈之程度、嫌惡設施、停車方便性等19項）之比準地與比較標的資料，**主要來自**
「宗地個別因素清冊」（比準地）與「買賣實例調查估價表」（比較標的），此二者為本次
競賽三張核心表以外之書表。表1（區域層級調查表）與表4（個別因素）在欄位語意上
高度相似（例如表1有「主要道路」，表4有「面前道路寬度」；表1有「墓地」，表4有
「嫌惡設施」），但**不是**直接的欄位承接關係——表1描述的是「整個地價區段」的
概況，表4描述的是「特定宗地／特定比較標的」相對於其自身位置的個別條件。

**重要區分（Golden Case驗證）**：以「距金山第一公墓之距離」為例——
- 表1（區段層級）：80M
- 表4個別因素（比準地本身）：260M
- 表4個別因素（比較標的1）：80M

三者為三個不同數值，不可將表1之區段層級數據直接複製為表4個別因素之比準地數值。
此發現已於 Phase 1 `open_questions.md` A-2 記錄，Phase 2 予以延續並在
`schemas/field_dictionary.json` 中以獨立 field_id（`cemetery_distance_m` 表1層級
vs `individual_nuisance_facility_base`/`_comparable_n` 表4層級）明確區分，避免
Rule Engine誤用。

## 交叉引用

- 距離定義與量測方法：見 `distance_rules.md`（Part F）
- 完整計算鏈與精度規則：見 `calculation_dependency.md`（Part E）
- Dependency影響範圍與Mermaid圖：見 `form_dependency.md`（Part D）
