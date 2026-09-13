# 區段圖是怎麼畫出來的

1. [地段地號怎麼抓](#一地段地號怎麼抓)
2. [區段範圍怎麼決定](#二區段範圍怎麼決定)
3. [界線路名怎麼標](#三界線路名怎麼標)
3.2. [預設路徑的資料流](#三之二預設路徑的資料流) ← 想先看全貌從這節開始
4. [產圖的工具與順序](#四產圖的工具與順序)
5. [Open data 與外部服務](#五open-data-與外部服務)
6. [實測結果](#六實測結果)

各節都附上實際踩過的坑與量測數字，不只是「怎麼做」也包含「為什麼不用另一種做法」。

---

## 一、地段地號怎麼抓

### 1. 座標 → 段名與段代碼

只需要一支 API，一次回傳全部識別資料：

```
GET https://api.nlsc.gov.tw/other/TownVillagePointQuery/{經度}/{緯度}/4326
```

```xml
<townVillageItem>
  <ctyCode>F</ctyCode>          <ctyName>新北市</ctyName>
  <townCode>F17</townCode>      <townName>樹林區</townName>
  <officeCode>FI</officeCode>   <officeName>樹林</officeName>
  <sectCode>1921</sectCode>     <sectName>太平段</sectName>
  <villageCode>65000070019</villageCode> <villageName>東陽里</villageName>
</townVillageItem>
```

實作：`getSectionCode.py` 的 `resolve_section()`。

**關鍵設計：段名與段代碼絕不交給 LLM 決定。**

LLM 只負責把中文的方位敘述（「沿東榮街以北、鎮前街411巷1弄以南…」）拆成
四個欄位。段名段代碼直接注入 prompt 當已知條件，`save` 階段再用 API 值
強制校正一次。

這不是防禦性設計，是實際踩過的坑：LLM 會照抄 prompt 範例裡的
「樹德段 / 1902」，把太平段（1921）寫成 1902。NLSC 用 `190203670000`
查得到地號（因為樹德段真的有 367 地號），但位置整個跑掉 —— 兩宗地相距
429.8 m，落在完全不同的分區叢集。**不會報錯，只會靜靜畫錯**。

`validate_cases()` 會攔下並記錄三類錯誤：

| 錯誤 | 處理 |
| --- | --- |
| 段名／段代碼與 API 不符 | 覆寫為 API 值，寫入 `corrections.json` |
| `"parcel": "367地號"` | 去掉「地號」二字 |
| `"parcel": "367、917"` | 拒絕。多地號必須拆成陣列多筆 |
| 缺方位條件或 zone | 拒絕 |
| 多筆之間界線條件不一致 | 拒絕（那是不同區段） |

另有一支列出某地政事務所所有地段的 API，用於人工核對：

```
GET https://api.nlsc.gov.tw/other/ListLandSection/F/F17
→ 樹林地政事務所 47 個地段（1902 樹德段、1921 太平段、1937 文林段…）
```

### 2. 段代碼 + 地號 → 宗地真實輪廓

地段地號組成 4+8 碼：段代碼 4 碼 + 母號 4 碼 + 子號 4 碼。
太平段 367 → `1921` + `0367` + `0000` = `192103670000`。

```
POST https://landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex
```

這支同時做三件事：驗證地號存在、回傳宗地經緯度範圍、回傳**著色 PNG**。

著色圖很重要：它是宗地的**真實形狀**，不是外接矩形。早期版本用外接矩形，
超出宗地太多，還得外推 3 m 補償。現在直接把這張 PNG 依其地理範圍貼到畫布上。

實作：`nlsc_parcel_map.py` 的 `verify_parcels()`，支援一次查多筆。

---

## 二、區段範圍怎麼決定

### 資料來源：新北市使用分區圖

```
新北市使用分區.shp   EPSG:3826   34,190 筆   欄位只有 ZONE   600 種分區
```

關鍵觀察：**「道路用地」本身就是一種分區**（8,575 筆）。也就是說分區圖上
的道路用地天然把街廓切開了，不需要從光柵影像猜道路、也不需要依賴 OSM 幾何。

作法（`render_zone_boundary.py` 的 `extract_zoning_block()`）：

1. 用宗地中心點找出**包含該點、且 ZONE 符合 JSON 指定分區**的多邊形
2. 宗地若落在道路用地內（實測樹德段 1415 就是），改取 60 m 內最近的符合分區者
3. 用 `_same_zone_cluster()` 把**與它相連、分區相同**的多邊形全部併起來
4. 多筆比準地各自跑一遍再取聯集

第 3 步是必要的：使用分區圖會把同一個街廓的同分區切成多筆。樹德段那個
街廓被切成 2 筆（6,760 + 700 m²），只取包含宗地的那筆會漏掉一塊。

### 常見誤解：邊界不是從圖片萃取的

很容易以為流程是「抓 NLSC 的圖 → 套遮罩取出線條 → 沿著最接近指定道路的
線條畫框」。**預設路徑不是這樣。**

關鍵在順序：**邊界在任何圖磚下載之前就算完了，而且全程是向量運算。**
區段範圍是使用分區 shapefile 上一個現成的多邊形，`shapely` 取出後轉成
螢幕座標直接畫線。沒有遮罩、沒有線條萃取、沒有「找最近的線」。

之所以能這樣做，是因為「道路用地」在使用分區圖上本身就是一種分區
（新北市 8,575 筆），道路已經把街廓天然切開了，不需要從影像猜道路位置。

NLSC 的宗地著色 PNG 只有兩個用途，都與邊界無關：

1. 宗地填色（第 14／16 步）—— 用它是因為那是真實形狀而非外接矩形
2. 保證比準地落在區段內（第 7 步）—— 宗地沒完整落入時，用
   `parcel_polygon_from_tint()` 取真實輪廓 union 進邊界。這是整個預設
   路徑裡唯一讓影像影響幾何的地方，而且只為補足，不是決定街廓範圍

「抓圖 → 遮罩 → 萃取」的做法確實存在於 `block_extract.py`，但只有指定
`--boundary-source cadastral` 才會走，而且已被實測否決（見下節）。

### 曾經試過但放棄的兩條路

| 方法 | `boundary_source` | 為何放棄 |
| --- | --- | --- |
| 以 OSM 道路中心線切割 ROI | `roads` | 潭興街107巷21弄 實際是 L 形，OSM 只有直線段，且它同時是兩個方位的界線 → 文林段 317 得到 45,152 m² 的錯誤範圍 |
| 由電子地圖光柵萃取街廓 | `cadastral` | 樹德段 1415 得 89,360 m² 且位置錯誤 |

光柵萃取（`block_extract.py`）的實際步驟，供對照：

```
EMAP16 底圖（道路呈白色）
  → _road_mask()          白色像素 → 道路遮罩，閉運算補斷點
  → 把 JSON 指定的四條界線道路 rasterize 成 barrier
  → cv2.connectedComponents  找連通元件
  → _pick_component()     取包含種子點的那一塊 = 街廓
  → _cadastral_mask()     從 DMAPS 取洋紅色地籍線
  → _snap_to_cadastral()  把街廓頂點吸附到最近的地籍線
```

注意主要手段是**連通元件**，不是「找最近的線」；吸附地籍線是最後的修飾。

失敗原因很具體：樹德段 1415 只有 19.7 m²、338 個像素**百分之百落在道路上**，
所以種子點落在道路遮罩裡，連通元件選到整片路網而不是街廓。
這不是參數調得不夠好，是方法本身在極小宗地上不成立。

### 已知落差

分區多邊形止於**道路用地邊緣**，不是路中心線。樹德段 284 得 7,475 m²，
人工參考值 9,213 m²，差額約等於周長 × 4.6 m —— 正好是巷道半寬。
要對齊人工值就得把邊界外推到路中心線，目前沒做。

---

## 三、界線路名怎麼標

這是最容易出錯、也改最多次的部分。

### 語意先講清楚

JSON 的 `north_of` 意思是「**區段**在該路之北」，所以**該路要畫在南側**。

```python
_RELATION_TARGET = {
    "north_of": (0.0,  1.0),   # 區段在該路之北 → 該路在南（螢幕 y 向下為南）
    "south_of": (0.0, -1.0),   # 該路在北
    "east_of":  (-1.0, 0.0),   # 區段在該路之東 → 該路在西
    "west_of":  (1.0,  0.0),   # 該路在東
}
```

### 步驟 1：把邊界化簡成幾條「邊」

分區多邊形很精細（實測 388 個頂點）。直接逐段合併方向相近的線段會因鋸齒
累積不到最小長度，結果得到 **0 條邊**。必須先 `simplify(10.0)` 只留主要轉折，
才能得到「每一側一條邊」。

實作：`_boundary_sides()`。

### 步驟 2：算每條邊的外法向量 —— 這裡踩過坑

原本用「形心 → 邊中點」的**放射向量**。這在凹口處會失效：相鄰兩條邊的放射
向量幾乎一樣（實測樹德段的東邊與東北斜邊都算成 `(0.93, ±0.38)`），分不出
「東」和「北」，導致長壽街21巷被標到東北斜邊。

改用**邊本身的垂直向量**，再用形心決定朝外的正負號：

```python
radial_x, radial_y = mid_x - centroid.x, mid_y - centroid.y
normal_x, normal_y = delta_y, -delta_x            # 垂直於邊
if normal_x * radial_x + normal_y * radial_y < 0:  # 朝內就翻面
    normal_x, normal_y = -delta_y, delta_x
```

同一組資料下兩種算法的比較：

```
邊 mid=(882,626) 東邊      radial=(0.84, 0.54)   perp=(0.81, 0.59)
邊 mid=(841,456) 東北斜邊  radial=(0.93,-0.38)   perp=(0.55,-0.84)
                            ↑ 兩者幾乎相同        ↑ 明顯分得開
```

換成 perp 後四個方位全部對上人工參考圖，另外三個案例的指派結果與原本完全相同。

### 步驟 3：一對一最佳指派

不能各方位獨立挑「最吻合的邊」：旋轉約 45° 的街廓會有兩條邊同時偏南，
貪心挑選會讓兩個方位搶到同一條邊。改成列舉所有排列、取**總吻合度最大**的
組合（`_assign_relations_to_sides()`）。

評分 = 方向內積 × √邊長。乘邊長是為了避免選到短碎邊。

邊數少於方位數時（小街廓化簡後可能只剩 3 條邊）退回貪心，但會盡量避免重複
用同一條邊，否則兩個標註會疊在一起看起來只有一個。

### 步驟 4：文字位置

**文字位置完全由區段邊界的幾何決定，不跟隨 OSM 路段幾何。**

跟隨 OSM 的話，潭興街 會被畫到西南邊（因為 OSM 上那條路的最長可見線段在
那裡），而 JSON 明確說它是東邊界。

其他規則：

- **同一條路兼任兩個方位就標兩次**。潭興街107巷21弄 是 L 形，同時構成南、
  西兩側邊界，兩處都標。
- **JSON 指定的界線道路一定標，含巷弄**；其餘只標主要道路（`include_alleys`
  預設 False，因為巷弄數量遠多於主要道路，會把主要道路擠出標註額度）。
- **宗地填色區域是保護區**（`protected_regions`）：由宗地範圍加 4 px padding
  組成，任何文字（路名、區段編號、比準地說明框）都不得覆蓋。
- **OSM 查無該路也照標**。樹德段 1415 的 `啟智街187巷24弄` 在 OSM 完全不存在
  （只有 `啟智街187巷`），此時位置改由「形心 + 方位方向 × (半徑+34px)」推定。
- 方位檢核未通過**不擋出圖**，只寫進警告（加 `--strict` 才擋）。

### 步驟 5：底圖與疊圖

| 圖層 | 代碼 | 用途 |
| --- | --- | --- |
| 底圖 | `EMAP16` | 臺灣通用電子地圖（不含等高線及門牌）。刻意不用含門牌的版本，避免門牌號與地號混淆 |
| 疊圖 | `DMAPS` | 地籍圖，提供宗地界線與地號文字。原色為洋紅，改成黑色 60% 不透明度讓區段線與宗地更突出 |
| 疊圖 | `LANDSECT` | 地段外圍圖 |
| 疊圖 | `EMAP12` | 透明文字層（實測 98% 透明），補回被底圖蓋掉的路名 |

繪製順序：底圖 → 疊圖 → 宗地填色 → 區段邊界線 → **宗地填色再貼一次** → 文字標註。

最後那次重貼是必要的：樹德段 1415 只有 19.7 m² 又緊貼邊界，邊界線寬會直接把
填色蓋掉。

---

## 三之二、預設路徑的資料流

三個座標系，各有明確分工。搞清楚資料在哪個座標系，就能理解整條流程：

| 座標系 | 用途 | 為什麼 |
| --- | --- | --- |
| EPSG:4326（WGS84 經緯度） | 對外 API 的輸入輸出 | NLSC、OSM 都用這個 |
| EPSG:3826（TWD97 / TM2） | **所有幾何運算** | 單位是公尺，`polygon.area` 直接就是 m² |
| 螢幕像素 | 只有繪圖 | Web Mercator 換算，畫布左上角為原點 |

### 逐階段的輸入與輸出

**A. 文字 → 識別碼**（不碰幾何）

```
"新北市樹林區太平段367、917地號"
  └ parse_basic_info()        用 NLSC 官方名稱清單做最長前綴比對
      → 縣市=新北市 / 行政區=樹林區 / 段名=太平段 / 地號=[367, 917]
  └ resolve_section_by_name()
      ├ data/land_sections.json（命中則 0 次網路請求）
      └ 未命中 → ListCounty → ListTown → ListLandSection
      → 段代碼 1921
  └ LandParcel.parse("1921", "367")
      → 4+8 碼 192103670000
```

**B. 識別碼 → 宗地位置**（EPSG:4326）

```
POST landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex
  → ParcelInfo{
        exists, section_name, land_office,
        min/max_longitude, min/max_latitude,   ← 只有外接矩形
        tint_image_png                          ← 真實形狀只存在於這張 PNG
    }
```

**這裡有個關鍵限制：NLSC 不回傳向量輪廓。** 只給外接矩形加一張著色 PNG，
所以宗地的真實形狀從頭到尾都是像素，不是多邊形。後面兩處要用到真實形狀時
（填色、確認落在區段內）都得回頭讀這張 PNG。

**C. 投影到公尺制**（EPSG:4326 → EPSG:3826）

```
parcel_polygon_3826(info)      外接矩形 → EPSG:3826 多邊形
unary_union(所有宗地).centroid  → anchor（多筆比準地的共同形心）
_TO_4326(anchor)               → anchor 的經緯度，供後續 API 查詢用
```

**D. 取邊界**（EPSG:3826，純向量，這是核心）

```
新北市使用分區.shp
  └ geopandas.read_file() → 34,190 筆 GeoDataFrame（已 to_crs 3826）
  └ extract_zoning_block(anchor)
      ├ 篩 ZONE 符合 JSON 指定分區者
      ├ geometry.contains(anchor) → 命中的多邊形
      │   （宗地落在道路用地內時改取 60 m 內最近的符合者）
      └ _same_zone_cluster()  以空間索引 BFS 併入相連的同分區多邊形
  └ 多筆比準地各自跑一遍 → unary_union
      → boundary_3826
```

**E. 檢核與補齊**（EPSG:3826）

```
Overpass way["highway"]["name"]
  └ road_cache 網格快取 → RoadFeature（幾何已投影到 3826）
  └ audit_directions(boundary_3826, roads)   → {north_of: True, …}

parcel_shape_points_3826(info)   從 tint PNG 取樣真實輪廓的點
  └ parcel_inside_ratio(boundary_3826, points)
      └ < 99.9% 時 parcel_polygon_from_tint() → union 進 boundary_3826
```

**F. 換算到畫布**（EPSG:3826 → 4326 → 螢幕像素）

```
boundary_4326 = shapely_transform(_TO_4326, boundary_3826)
bbox = boundary_4326.bounds
bbox 四邊各外擴 span × padding_ratio(0.45)      留邊，讓界線道路都看得到
_fit_zoom(撐大後的 bbox, 1400, 1000)
    由 z20 往下試，找第一個能把 bbox 放進畫布的層級  → zoom
lonlat_to_pixel(bbox 中心, zoom) → (center_x, center_y)
left = center_x - width/2   top = center_y - height/2
_ring_to_screen(ring, zoom, left, top)          → 畫布座標
```

`left/top` 是由**中心點**回推的，不是直接取撐大後 bbox 的左上角 ——
`_fit_zoom` 選的層級通常讓內容小於畫布，從中心回推才會置中。

**G. 疊圖**

```
每個圖層 → _fetch_layer_image()
    ├ 算出覆蓋視野的圖磚範圍
    ├ 每張圖磚先查磁碟快取，未命中才向 WMTS 抓（4 執行緒並行）
    └ Pillow 拼接成整張圖層
  → alpha_composite 依序疊上：EMAP16 → DMAPS → LANDSECT → EMAP12
tint PNG → 依 min/max 經緯度縮放到對應像素尺寸 → paste
```

**H. 標註**（螢幕像素）

```
_boundary_sides(boundary_screen)
    ├ simplify(10px) 去掉鋸齒（388 頂點 → 主要轉折）
    ├ 合併方向接近的連續線段
    └ 每條邊算真實垂直外法向量
_assign_relations_to_sides()   列舉排列取總吻合度最大的一對一指派
_draw_road_labels()            旋轉描邊文字，避開 occupied 與宗地保護區
_find_clear_spot()             標註框放在區段外且不與既有文字重疊
```

### 外部依賴各自負責什麼

| 來源 | 提供 | 缺了會怎樣 |
| --- | --- | --- |
| 使用分區 shapefile | **區段邊界** | 無法出圖 |
| `qryTileMapIndex` | 宗地位置與著色圖 | 無法出圖 |
| `ListLandSection`／本地索引 | 段代碼 | 有座標時可繞過 |
| WMTS 圖磚 | 底圖與地籍線 | 圖面空白但邊界仍正確 |
| Overpass | 方位檢核、其他路名 | 只影響檢核結果與非界線路名，界線文字位置不受影響 |

最後一列是刻意的設計：**界線文字的位置由區段邊界的幾何決定，不依賴 OSM**。
所以 OSM 缺漏巷弄（實測樹德段 1415 的 `啟智街187巷24弄` 在 OSM 完全不存在）
不會讓圖標錯，只是方位檢核那欄會顯示未通過。

## 四、產圖的工具與順序

### 用了哪些套件、各自負責什麼

| 套件 | 角色 |
| --- | --- |
| `requests` | 抓 NLSC API、WMTS 圖磚、Overpass。搭配 `nlsc_http.NlscSSLAdapter` 處理 NLSC 的憑證問題 |
| `pyproj` | 座標系轉換。WGS84（EPSG:4326）↔ TWD97 / TM2 zone 121（EPSG:3826） |
| `shapely` | 所有幾何運算：分區多邊形聯集、宗地是否落在區段內、邊界化簡、文字框碰撞偵測 |
| `geopandas` | 只用來讀使用分區 shapefile 並做空間索引查詢（`sindex`） |
| `Pillow` | 圖磚拼接、圖層合成、文字繪製與旋轉、輸出 PNG |
| `opencv` | 只有 `block_extract.py`（非預設的光柵萃取路線）用到 |

**面積一律在 EPSG:3826 計算，不在經緯度計算。** TWD97 / TM2 是投影座標系，
單位是公尺，`polygon.area` 直接就是平方公尺。在 EPSG:4326 算面積會得到
「平方度」，毫無意義。

**繪圖一律在螢幕像素座標。** 幾何算完後才透過 `_ring_to_screen()` 轉成畫布座標。

### 座標怎麼對應到畫布

用標準的 Web Mercator 圖磚數學，圖磚 256×256（`TILE_SIZE = 256`）：

```python
world = TILE_SIZE * (2 ** zoom)          # 該層級的世界像素寬
x = (longitude + 180) / 360 * world
sin_lat = sin(radians(clamp(latitude, ±85.05112878)))
y = (0.5 - log((1 + sin_lat) / (1 - sin_lat)) / (4 * pi)) * world
```

緯度必須夾在 ±85.05112878°，否則極區會產生無限值。

`pixel_to_lonlat()` 是它的反函式，用來把畫布視野換算回經緯度，以便向
Overpass 查詢「畫面內有哪些路」。

畫布左上角的世界像素座標記為 `(left, top)`，任何幾何點的畫布位置就是
`lonlat_to_pixel(...) - (left, top)`。

### 縮放層級怎麼決定

先把區段的經緯度 bbox 四邊各外擴 `span × padding_ratio`（預設 0.45），
再由 `_fit_zoom()` 從 z20 往下試，取第一個能把撐大後的 bbox 放進
1400×1000 畫布的層級。

上限鎖在 `MAX_SAFE_ZOOM = 20`：實測 z21 起 NLSC 所有圖磚都回 0 位元組。

早期版本 zoom 太大，導致路名文字被畫面邊緣切掉、周邊主要道路也落在視野外，
所以改成先把視野放寬再出圖。

### 出圖的完整順序

`render_zone_boundary()` 的實際執行順序。前八步是算，後面才是畫。

**準備階段（幾何運算，還沒碰畫布）**

1. `load_cases()` 讀條件 JSON，驗證多筆比準地的界線條件一致
2. `verify_parcels()` 向 NLSC 驗證所有地號，取回經緯度範圍與著色 PNG
3. 錨點取所有宗地的**共同形心**（單筆時就是它自己）
4. `resolve_boundary_roads()` 解析四條界線道路的 OSM 幾何（走網格快取）
5. `extract_zoning_block()` 取分區多邊形並合併相連同分區者 → 區段範圍
6. `audit_directions()` 用 `_satisfies_direction()` 檢核方位是否成立
7. `parcel_inside_ratio()` 確認宗地完整落在區段內；不足時以宗地真實輪廓補齊
8. `_fit_zoom_for_viewport()` 決定層級與視野，算出 `(left, top)`

**繪製階段（由下往上疊，全部用 RGBA + `alpha_composite`）**

```
9.  白色底畫布                Image.new("RGBA", (1400, 1000), 白)
10. EMAP16   底圖             臺灣通用電子地圖（不含等高線及門牌）
11. DMAPS    地籍圖           經 restyle_layer() 改黑色、60% 不透明度（原色為洋紅）
12. LANDSECT 地段外圍圖
13. EMAP12   透明文字層       補回被底圖蓋掉的路名
14. 宗地著色                  貼 NLSC 著色 PNG（真實輪廓，非外接矩形）
15. 區段邊界線                boundary_draw.line(joint="curve")
16. 宗地著色  再貼一次        ★ 見下方說明
17. 路名大字（藍色，沿路方向）
18. 區段編號框、比準地說明框（紅字紅框白底 + 引線）
19. convert("RGB").save()     輸出 PNG
```

第 10～13 步每一層都是獨立抓取後 `alpha_composite` 疊上，因此圖層順序就是
清單順序，改順序只要換 `DEFAULT_OVERLAYS` 的排列。

**第 16 步為什麼要重貼一次宗地著色**：樹德段 1415 只有 19.7 m²，又緊貼區段
邊界。邊界線寬 3 px 會直接把整個宗地填色蓋掉，圖上看不到比準地在哪。把著色層
在邊界線之後再貼一次就解決了。這也是為什麼 `parcel_layer` 是先建好、
分兩次 composite，而不是畫完就丟。

### 文字怎麼放而不互相打到

所有已放置的文字都會把自己的矩形推進一個 `occupied: list[Polygon]`，
後續文字必須避開全部既有矩形。碰撞判定用 shapely 的 `rect.intersects(other)`。

`occupied` 的初始內容是 `protected_regions` —— **宗地填色範圍加 4 px padding**。
這是最高優先的保護區，任何文字都不得覆蓋比準地。

`_find_clear_spot()` 以 10 px 為步長掃過整個畫布，篩掉兩種位置：

- 與 `keep_clear`（區段多邊形內部）相交 → 標註框不進區段，避免蓋住區段內資料
- 與 `occupied` 任一矩形相交

剩下的候選中取分數最低者。給了 `prefer` 點（例如宗地位置）時，分數是「框心到
該點的距離」，讓引線盡量短；沒給時則取離區段最遠者。

路名是**旋轉文字**：`_render_rotated_label()` 先在獨立圖層用 `_draw_text_with_halo()`
畫好帶白色描邊的文字（描邊是為了在複雜底圖上維持可讀性），再用
`rotate(angle, expand=True, resample=BICUBIC)` 轉到與該邊平行的角度貼上畫布。
角度會正規化到 ±90° 之間，確保文字不會顛倒。

同一條路提供多個候選錨點（線段中點、1/3、2/3 位置）。只取最長線段的中點時，
一旦與其他標籤碰撞整條路就標不出來 —— 實測中正路就是這樣消失的。

---

## 五、Open data 與外部服務

### 檔案型資料（需自行下載，未隨程式碼發佈）

| 資料 | 規格 | 用途 |
| --- | --- | --- |
| 新北市使用分區 | shapefile，EPSG:3826，34,190 筆，179 MB | **決定區段範圍**。欄位只有 `ZONE` |

179 MB 超過 GitHub 100 MB 單檔上限，因此不在 repo 內。轉 GeoParquet（brotli）
可壓到 62.9 MB，適合放進容器 image。

### 國土測繪中心（NLSC）

| 端點 | 用途 |
| --- | --- |
| `api.nlsc.gov.tw/other/TownVillagePointQuery/{lon}/{lat}/4326` | 座標 → 縣市／行政區／地政事務所／段代碼／段名／村里 |
| `api.nlsc.gov.tw/other/ListLandSection/{縣市}/{鄉鎮}` | 列出某地政事務所所有地段 |
| `landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex` | 驗證地號、取宗地範圍與著色圖 |
| `landmaps.nlsc.gov.tw/S_Maps/wmts/{圖層}/default/GoogleMapsCompatible/{z}/{y}/{x}` | 地籍圖磚（DMAPS） |
| `wmts.nlsc.gov.tw/wmts/{圖層}/default/GoogleMapsCompatible/{z}/{y}/{x}` | 其他圖磚（EMAP16／EMAP12／LANDSECT／URBAN） |
| `maps.nlsc.gov.tw/goland/{縣市}/{地段地號}[/{底圖}_B/{圖層}]` | 圖台核對連結（人工複查用） |

三個實測得到的必要條件：

1. **DMAPS 必須走 `landmaps.nlsc.gov.tw` 且帶 `Referer: https://maps.nlsc.gov.tw/`**。
   走 `wmts.nlsc.gov.tw` 或不帶 Referer 會回 HTTP 200 但 0 位元組。
2. **URBAN 圖層需先造訪 `https://maps.nlsc.gov.tw/` 取 JSESSIONID**。
3. **z21 起所有圖磚皆 0 位元組**，因此 `MAX_SAFE_ZOOM = 20`。

另外 NLSC 的憑證鏈缺少 Subject Key Identifier，Python 3.13 起 OpenSSL 預設
啟用 `VERIFY_X509_STRICT` 會驗證失敗。`nlsc_http.py` 的 `NlscSSLAdapter`
只關掉這一條規則，憑證鏈、主機名稱、有效期限仍照驗 —— **不是 `verify=False`**。

### OpenStreetMap（Overpass API）

```
POST https://overpass-api.de/api/interpreter
way["highway"]["name"](minlat,minlon,maxlat,maxlon); out geom;
```

用途只有兩個：**方位檢核**、以及**補標 JSON 未指定的其他主要道路**。
界線道路的文字位置不依賴它。

Overpass 是整個流程唯一的效能瓶頸。未加快取時它佔總耗時 **92%**：

```
Overpass 解析四條界線道路   61.2s   (66.9%)   ← 半徑 450→750→1200→1800，每個重試 3 次
Overpass 抓畫面內所有路名   23.3s   (25.5%)
分區萃取                    2.8s
讀使用分區 shapefile        1.8s
抓／貼圖磚（已快取）         0.1s
────────────────────────────────────
總計                       91.5s
```

所以 `road_cache.py` 把經緯度切成 **0.02°（約 2 km）網格**，每格只向 Overpass
要一次具名道路，gzip JSON 存磁碟。之後不論半徑式或 bbox 式查詢都從網格取。
出圖從 91.5s 降到 **4.5s**，且四案的面積、方位檢核、路名清單逐項不變。

**快取單位是地理網格，不是案件。** 整個行政區抓過一次，該區任何新案件都不再
碰 Overpass：

```bash
python road_cache.py --bbox 121.38 24.94 121.46 25.03   # 樹林區 25 格，約 8 分鐘、1.3 MB
```

注意 Overpass 公用端點會拒絕沒有有意義 User-Agent 的請求（回 406／429），
標頭定義在 `nlsc_http.py` 的 `OVERPASS_HEADERS`。

### 授權

- NLSC 圖磚與 API：使用前請確認「國土測繪圖資服務雲」介接條款，特別是要對
  第三方轉發時。
- OSM：ODbL。快取內容若要隨程式碼散布需標示授權，因此 `_road_cache/` 與
  `_tile_cache/` 都在 `.gitignore` 內。

---

## 六、實測結果

| 案例 | 比準地 | 面積 | 方位檢核 | 路名標註 | 出圖 |
| --- | --- | --- | --- | --- | --- |
| 樹德段 1415 | 1 筆 | 1,906 m² | 3/4 | 8 條 | 4.9s |
| 樹德段 284 | 1 筆 | 7,475 m² | 4/4 | 9 條 | 4.6s |
| 太平段 367、917 | 2 筆 | 4,226 m² | 4/4 | 8 條 | 5.8s |
| 文林段 317 | 1 筆 | 4,283 m² | 2/4 | 5 條 | 4.9s |

方位檢核未通過的兩案都是 OSM 資料缺漏，不是幾何計算錯誤：

- 文林段 317：`潭興街107巷21弄` 實際為 L 形，OSM 只有直線段
- 樹德段 1415：`啟智街187巷24弄` 在 OSM 完全不存在

兩案的界線文字都仍正確標出，因為文字位置由區段邊界幾何決定。

（出圖時間為路網與圖磚皆已快取的狀態。完全冷啟動需 60～143 秒，
絕大部分花在向 Overpass 抓新網格。）
