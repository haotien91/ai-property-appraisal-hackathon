# Phase API-2.2 — Official Parcel Coordinate Source Audit

> AUDIT + VERIFY + DESIGN ONLY. No Provider written this round. No NLSC/TGOS
> credentials requested or created. No live authenticated call made against
> any restricted-eligibility endpoint. Findings below are drawn from (a) an
> official, dated NLSC PDF obtained via WebFetch, (b) public documentation
> pages, (c) this project's own prior verified findings (Phase API-1's
> TGOS_AUTH_PENDING backlog entry, Phase API-2's landmark-dataset CRS
> spot-check). Where public sources did not answer a question, this
> document says so explicitly rather than inferring or guessing.

## 0. Primary Source

**內政部國土測繪中心《國土測繪圖資服務雲申請服務介接說明表》**（製表日期：
110/07/05，即2021-07-05），取自
`https://ws.moi.gov.tw/Download.ashx?...`（連結自官方頁面
`https://www.nlsc.gov.tw/NLSC_Content.aspx?n=1742&s=266359`搜尋結果鏈）。
此為官方PDF，逐項列出全部圖資/API服務之：類別、代號、功能編號、服務型態
（WMS/WMTS/WFS/API）、綁定方式（綁定URL/綁定IP）、提供對象、備註。以下
稽核結果**直接取自此表格逐字內容**，非摘要臆測。

**時效性提醒**：此文件日期為2021-07-05，可能已有後續更新版本（例如搜尋
結果另發現一則較新聞稿提及「模糊檢索API」已開放民營機構申請——見§5，但
該開放**僅限模糊檢索/門牌類API（ADR_\*），未見任何來源顯示CAD_\*地籍
API之提供對象已變更**）。本輪未能取得更新版本，如有需要應直接向國土
測繪中心確認現況。

## 1. NLSC 地籍 API 逐一稽核（§四要求之6項＋官方表列出之完整地籍API家族）

原始表格「地籍圖」類別（項次1-15）完整逐字轉錄：

| 項次 | 圖資名稱（代碼） | 功能編號 | 服務型態 | 綁定URL | 綁定IP | 提供對象 | 備註 |
|---|---|---|---|---|---|---|---|
| 1 | 地籍圖磚 | - | WMS/WMTS | V(WMTS) | V(WMS/WMTS) | 中央機關、地方政府及國營事業 | 圖磚影像 |
| 2 | 地籍圖WFS**（僅限內網使用，不得對外開放）** | - | WFS | | V | 中央機關與本中心簽訂測繪合作契約者，限供內網使用；或中央機關/地方政府僅以圖片網站輸出套疊可對外（註1） | 取得「向量」資料（GML） |
| 3 | 【地籍圖API】指定地號查詢「地籍圖」**（僅限內網使用，不得對外開放）**（CadasMapQuery） | MAP_001 | API | | V | 同上 | GML/KML/SHP |
| 4 | 【地籍圖API】指定坐標查詢「地籍圖」**（僅限內網使用，不得對外開放）**（CadasMapPointQuery） | MAP_002 | API | | V | 同上 | GML/KML/SHP |
| 5 | 【地籍API】指定地號查詢位置（**CadasMapPosition**） | **CAD_001** | API | **V** | **V** | 中央機關、地方政府及國營事業 | 回傳XML |
| 6 | 【地籍API】指定地號查詢著色圖（CadasMapImage） | CAD_002 | API | V | V | 同上 | 回傳JSON |
| 7 | 【地籍API】單點坐標查詢地段號（GetLandNO） | CAD_003 | API | V | V | 同上 | 回傳XML |
| 8 | 【地籍API】地段號查詢坐標（**GetLandPositionLongitudeLatitude**） | **CAD_004** | API | **V** | **V** | 中央機關、地方政府及國營事業 | 回傳XML |
| 9 | 【地籍API】坐標查地段號（QryTileMapIndex(1)） | CAD_005 | API | V | V | 同上 | 回傳JSON |
| 10 | 【地籍API】地段號宗地定位（**QryTileMapIndex(2)**） | **CAD_006** | API | **V** | **V** | 中央機關、地方政府及國營事業 | 回傳JSON |
| 11 | 【地籍API】指定地號查詢土地標示資料（**CadasAttrQuery**） | **CAD_007** | API | **V** | **V** | 同上 | 回傳JSON |
| 12 | 【地籍API】地段代碼回傳測繪段籍屬性（**GetLandSecInfoNlsc**） | **CAD_008** | API | **V** | **V** | 同上 | 回傳XML |
| 13 | 【地籍API】指定門牌查詢地號（AddressQueryLand） | CAD_009 | API | V | V | 同上 | 回傳XML |
| 14 | 【地籍API】指定範圍查詢地號清單（**CadasLandNoQuery**） | **CAD_010** | API | **V** | **V** | 同上 | 回傳JSON |
| 15 | 【地籍API】指定地號查詢建號列表與土地權利人類別（CadasLandInfo） | CAD_011 | API | V | V | 同上 | 回傳JSON |

**逐一結論（§四要求之6項）**：

1. **CadasMapPosition (CAD_001)**：存在，API，需URL+IP雙綁定，提供對象
   為中央機關/地方政府/國營事業，回傳XML。**未公開之欄位級技術文件**
   （見§2）。
2. **GetLandPositionLongitudeLatitude (CAD_004)**：存在，同上限制，回傳
   XML。函式名稱明確含"LongitudeLatitude"，暗示輸出為經緯度（非投影
   坐標），但未見官方欄位定義證實。
3. **QryTileMapIndex(2) (CAD_006)**：存在，「地段號宗地定位」，同上
   限制，回傳JSON。
4. **CadasAttrQuery (CAD_007)**：存在，「指定地號查詢土地標示資料」，
   同上限制，回傳JSON——此為地籍**屬性**（如地目、面積等文字資料），
   非座標本身。
5. **GetLandSecInfoNlsc (CAD_008)**：存在，「地段代碼回傳測繪段籍
   屬性」，同上限制，回傳XML——**此極可能正是Golden Case §八所需之
   「district/section_name → 官方段籍代碼」查詢服務**，但同樣受
   相同eligibility限制，本輪無法實際呼叫驗證。
6. **CadasLandNoQuery (CAD_010)**：存在，「指定範圍查詢地號清單」，
   同上限制，回傳JSON。

**全部6項（及整個CAD_\*地籍API家族之CAD_001~CAD_011）之提供對象皆為同一
行文字：「中央機關、地方政府及國營事業」**——無例外，無academic/民間
企業/一般民眾之開放紀錄（見§5進一步討論）。

## 2. CadasMapPosition 特別驗證（§五，20項逐一回答）

| # | 項目 | 稽核結果 |
|---|---|---|
| 1 | repX/repY正式定義 | **未能取得**——欄位級技術文件（含XML schema/範例）僅存在於S09SOA開發者入口（`https://maps.nlsc.gov.tw/S09SOA`為前端frameset頁面，實際API文件疑似需登入後之開發者專區方可查閱，本輪多次WebFetch/WebSearch皆未能取得該層級細節） |
| 2 | 是否為宗地代表點 | 函式中文名稱為「指定地號查詢**位置**」（query **position**），非「查詢**中心點**」或「查詢**質心**」，用字上偏向定位點而非幾何中心 |
| 3 | 是否可視為parcel centroid | **不可**——無官方文件明確使用「centroid」/「質心」/「形心」等詞彙描述repX/repY，依使用者本輪明確指示不得自行認定 |
| 4 | 還是僅定位用representative point | 依現有證據（函式命名慣例、輸出同時含repX/repY與left-bottom/right-top邊界框——邊界框的存在暗示這是「宗地外接矩形」搭配「一個代表點」的常見GIS地籍定位模式，而非幾何質心計算結果），**較可能**是純定位用途之representative point，而非centroid計算結果——但仍屬推論，非官方確認 |
| 5 | CRS | **未確認**（見下） |
| 6 | datum | **未確認**；併同§十三與Phase API-2landmark資料集之twd97_x/y先例，**推測**（非確認）與TWD97家族一致 |
| 7 | coordinate order | 未確認（X,Y或Y,X皆有可能，視是否為TM2投影或地理經緯度而定） |
| 8 | response XML schema | 未取得 |
| 9 | API endpoint | 已知功能路徑存在於`https://maps.nlsc.gov.tw/S09SOA`體系下，確切URL路徑未經授權帳號無法查驗 |
| 10 | POST/GET | 未確認 |
| 11 | authentication | **確認需要**——見§1提供對象欄，需經申請取得帳密/金鑰後綁定URL+IP方可呼叫 |
| 12 | Token algorithm | 未確認 |
| 13 | account requirements | 需為中央機關、地方政府或國營事業（見§5） |
| 14 | rate limit | 未確認 |
| 15 | IP binding | **確認需要**（綁定IP欄=V）——依官方表格備註(2)：「綁定IP，係直接限制該IP的伺服器使用，安全性較高，**不得為全機關對外之共用IP**」，即必須是單一、固定、專屬的伺服器IP |
| 16 | URL binding | **確認需要**（綁定URL欄=V）——依備註(3)：「綁定URL，可直接提供網頁服務，安全性較低」 |
| 17 | eligibility | 中央機關、地方政府、國營事業（見§5） |
| 18 | Terms | 未見完整使用條款全文（僅表格備註），未進一步深入條款頁面（帳號登入後方顯示） |
| 19 | production usage restrictions | 未確認細節，但IP+URL雙綁定本身即為強使用限制 |
| 20 | 是否適合AWS Lambda | 見§7完整討論——**結構上有摩擦但非不可行**，惟前提需先解決§5之eligibility問題 |

**重要語意結論**：依使用者本輪明確要求，`repX`/`repY`**不得**命名為
`parcel_centroid`。若本專案未來真的取得此API存取權，對應之
`domain.models`欄位應命名為`OFFICIAL_PARCEL_REPRESENTATIVE_POINT`（或
等價名稱），並在欄位docstring中明確聲明「此為官方定位點，非經幾何運算
之centroid，除非未來取得官方文件明確定義為centroid」。

## 3. API Access / Eligibility（§六）

**逐API標記**（依§1表格之提供對象欄）：

| API | Status |
|---|---|
| CAD_001~CAD_011（全部地籍API） | **REQUIRES_APPLICATION**（且僅限特定申請人類別，見下） |
| MAP_001/MAP_002（地籍圖向量查詢） | **REQUIRES_APPLICATION** + 額外**僅限內網使用**（比一般REQUIRES_APPLICATION更嚴格） |
| 地籍圖磚WMS/WMTS（A1） | **REQUIRES_APPLICATION**（非公開免申請——見§11重要更正） |
| 地籍圖WFS（A2） | **REQUIRES_APPLICATION** + **僅限內網使用**（最嚴格層級） |

**申請人資格範圍（官方表格逐字）**：「中央機關、地方政府及國營事業」
——即中央政府機關、**地方政府**（如新北市政府）、以及國營事業三類。
**不包含**：一般學術機構、民間企業、個人、競賽參賽團隊。

**本專案資格判定**：本專案為「新北市政府AI黑客松參賽作品」——**參賽
團隊本身不是地方政府、中央機關或國營事業，不具備獨立申請資格**。
唯一可能之申請主體是**新北市政府本身**（作為地方政府，主辦單位）以
新北市政府名義申請、取得帳密後，再決定是否/如何提供給本專案使用（例如
以新北市政府自有伺服器代為查詢後轉發，或核發一組供本專案使用之憑證並
綁定本專案之伺服器IP/URL）。**這是一個組織/業務決策，非本輪技術稽核
可單方確認**，需實際與新北市政府/主辦單位溝通。

`GOVERNMENT_SPONSOR_REQUIRED = YES`（若要使用CAD_\*系列，依現有唯一
找到之官方文件，除新北市政府或其他適格機關代為申請/使用外，無其他
合法路徑）。

（附註：搜尋結果另發現一則較新聞稿，提及國土測繪中心「模糊檢索API」
已開放**民營機構**免費申請介接——此為**不同的API家族**（門牌/模糊檢索
ADR_\*，非CAD_\*地籍定位API），且新聞稿本身未見具體日期版本之官方
表格佐證，本輪不將此開放範圍套用至CAD_\*地籍API之結論。）

## 4. AWS Compatibility 評估（§七，僅設計，不建立資源）

若未來新北市政府核准並提供CAD_001等API之憑證，且該憑證綁定方式為
**固定IP**（見§3，IP綁定為必要條件，非全機關共用IP）：

**問題**：AWS Lambda預設之outbound連線並非固定單一IP——Lambda若部署於
VPC外（no VPC config）或未經NAT Gateway，其對外IP來自AWS動態管理之
IP池，**無法**滿足NLSC「必須為單一伺服器專屬IP」之綁定要求。

**設計方案（僅提出，本輪不建立）**：

```
Lambda（VPC內，private subnet）
    → NAT Gateway（該subnet之route table導向NAT Gateway）
    → NAT Gateway關聯一個Elastic IP（固定、公開）
    → 以此Elastic IP向NLSC申請IP綁定
    → NLSC CadasMapPosition等API
```

**成本／複雜度／黑客松適用性評估**：

| 面向 | 評估 |
|---|---|
| Cost | NAT Gateway按小時計費（約US$0.045/hr，另計流量費），加上Elastic IP在未關聯資源時亦計費——對黑客松demo規模而言金額不高，但**是額外常態性成本**，且需要事先於AWS帳戶內建立、非一次性 |
| Complexity | 需要VPC設計（private subnet + NAT Gateway + IGW + route table）、Lambda需改為VPC-attached（會增加cold start時間），架構複雜度明顯高於目前完全serverless、無VPC依賴之設計 |
| Hackathon suitability | **較低**——黑客松時程通常短、demo環境經常重建，NAT Gateway+Elastic IP需要穩定、長期存在的AWS環境設定，且IP綁定申請本身可能有審核等待期（TGOS同體系服務曾明示72小時，NLSC本輪未見時效承諾），與黑客松「快速迭代」步調不易配合 |

**結論**：技術上可行（Lambda→private subnet→NAT Gateway→Elastic IP→
NLSC），但屬於架構複雜度與維運成本的顯著提升，且前提（§3之申請資格）
尚未解決。本輪僅提出設計，不建立任何AWS資源。

## 5. Golden Case Input Mapping（§八）

Golden Case現有欄位：`district="金山區"`、`section_name="金美段"`、
`land_no="489"`。

依使用者要求，優先嘗試官方`ListCounty`/`ListTown`/`ListLandSection`類
服務或現有專案source完成「金山區/金美段 → 官方段籍代碼」對應——**未能
完成**：

- `GetLandSecInfoNlsc`（CAD_008，「地段代碼回傳測繪段籍屬性」）看似
  最相關，但屬CAD_\*地籍API家族之一員，**同樣受§3所述eligibility與
  URL+IP綁定限制**，本輪無帳密無法實際呼叫查驗「金美段」對應之官方
  Sec代碼。
- 未發現任何**公開免申請**版本之ListCounty/ListTown/ListLandSection
  服務（官方表格中所有地籍相關服務皆列在受限之CAD_\*/MAP_\*/WFS
  家族內）。
- 現有專案source（`providers/cadastral_identifier.py`等）為NTPC（新北
  市政府地政局）體系之地號解析邏輯，其正規化目標是NTPC OpenData之
  `segment`/`lid`欄位，**與NLSC（內政部國土測繪中心）之Sec代碼命名
  空間並無已知對應關係**，不可越級借用。

`GOLDEN_CASE_SECTION_CODE_RESOLVED = NO`——誠實維持NO，不猜測代碼。

## 6. Land Number Encoding（§九）

現有`LandNumberNormalizer.to_land_price_lid()`/`to_expropriation_id()`
之零填充規則（4碼本號+4碼子號，或不補零本號+4碼子號），是**針對NTPC
（新北市政府地政局）OpenData之`lid`/`id`欄位**、以數百至數千筆真實
NTPC資料逐一比對驗證而得（Phase API-1.5）——這是**NTPC自己的資料庫
內部編碼慣例**，並非台灣地籍地號的通用官方標準格式。

NLSC（內政部國土測繪中心）為**不同機關、不同系統**，其CAD_\*API之
「地號」（No）參數格式**未見官方文件公開說明**，本輪無法實證489地號
應如何表示（`489`／`04890000`／`0489000`／其他）——且如§3所述，即使
想以最小測試呼叫方式實證，也因無帳密而無法實際發送請求驗證。

`GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED = NO`——不沿用NTPC encoding
之假設，誠實標示未解決。

## 7. Official Section Code Path（§十，設計）

```
district（金山區）/ section_name（金美段）
        ↓
[官方section-code lookup —— 目前唯一已知候選：NLSC GetLandSecInfoNlsc
 (CAD_008)，但受§3 eligibility限制，本輪無法驗證]
        ↓
NLSC Sec（官方段籍代碼）
        ↓
land number（格式依§9仍待官方確認）
        ↓
NLSC CadasMapPosition / GetLandPositionLongitudeLatitude
```

**明確設計原則**（供未來取得API存取權後之實作參考，本輪不實作）：
不得以「同名段模糊比對→取第一筆」方式簡化此流程。若查詢過程中發現：
(a) 同一行政區內存在多個同名或近似名稱之段（例如「一小段」「二小段」
之類細分情形）、(b) district資訊不足以唯一定位、或(c) NLSC回傳之
section mapping本身含糊（例如同時比對出多筆候選），則一律回報
`MANUAL_REVIEW_REQUIRED`，不得自動選擇任一筆。

## 8. NLSC WMS/WMTS 稽核（§十一）——重要更正一般假設

依§1官方表格，「地籍圖磚」（WMS/WMTS圖磚影像服務）**同樣**列於
「提供對象：中央機關、地方政府及國營事業」欄下，**非**無須申請之公開
服務——這更正了一個常見的直覺假設（「WMS/WMTS通常是公開瀏覽服務」）。

**必須區分兩個不同層次**：

| 層次 | 是否公開 | 說明 |
|---|---|---|
| A. 人工瀏覽（於瀏覽器開啟`https://maps.nlsc.gov.tw`觀看地圖） | **公開，任何人可瀏覽** | 純粹視覺呈現，無法程式化取得座標資料 |
| B. 程式化WMS/WMTS服務端點（供軟體系統直接請求圖磚/圖層） | **需申請**，同CAD_\*之eligibility限制 | 即使核准，取得的仍只是「圖磚影像」，非座標數值 |

**逐項判定**：

- A. visualization（人工瀏覽）：**VISUALIZATION_ONLY**，任何人可用，
  但無法程式化取得地號對應座標。
- B. section boundary（段界）：地籍圖磚/WFS皆可能隱含段界資訊，但
  皆需申請（B層級），且WFS更限於**內網使用**（見下）。
- C. cadastral parcel geometry（單一宗地geometry）：**地籍圖WFS**
  （取得GML向量資料）可能提供，但**僅限內網使用，不得對外開放**
  （官方表格明文）——即使新北市政府代為申請成功，此服務依規定**不能
  提供給外部（含AWS Lambda）呼叫**，架構上不可行，非僅eligibility
  問題。
- D. attributes（地籍屬性文字，如地目/面積）：`CadasAttrQuery`
  (CAD_007)可能提供，同CAD_\*限制。
- E. parcel representative coordinate（宗地代表座標）：`CadasMapPosition`
  (CAD_001)——**是目前唯一「可能」同時滿足（a）非內網限定、（b）
  API形式、（c）回傳座標數值**三條件的服務，但仍受§3 eligibility限制。

**結論**：不能因WMS能畫出地籍圖，就認定可以直接取得parcel coordinate
——本輪明確避免此推論錯誤。

## 9. Parcel Geometry Possibility（§十二）

依§1/§8：官方地籍宗地**polygon geometry**取得管道為「地籍圖WFS」
（GML向量資料）與「CadasMapQuery/CadasMapPointQuery」（MAP_001/002，
GML/KML/SHP）——**三者皆明文「僅限內網使用，不得對外開放」**。

**這是比eligibility更根本的架構限制**：即使新北市政府以地方政府身份
成功申請，這些服務依官方規定**只能在政府機關內部網路使用**，**不能**
提供給外部系統（包含本專案未來可能部署之AWS Lambda）呼叫。因此：

`OFFICIAL_SOURCE_GEOMETRY`（透過API/WFS取得官方宗地polygon）→
**對外部雲端部署架構而言不可行**，非本輪或未來輪次申請努力可解決之
問題，而是官方服務條款本身之硬性限制。

若未來確實取得`OFFICIALSOURCE_GEOMETRY`（例如透過政府內部系統代為
匯出後另行提供靜態檔案，非直接API存取），則`SYSTEM_COMPUTED_CENTROID`
（本專案自行以polygon頂點計算幾何中心）與`OFFICIAL_REPRESENTATIVE_
POINT`（CadasMapPosition之repX/repY）**必須在Evidence中明確分開標示
來源**，不得混同呈現為同一種「官方座標」（例如需各自標註
`coordinate_derivation = OFFICIAL_DERIVED_CENTROID` vs
`OFFICIAL_REPRESENTATIVE_POINT`）。

## 10. TGOS Secondary Audit（§十三，僅簡要比較）

延續Phase API-1既有稽核（`docs/backlog.md::TGOS_AUTH_PENDING`）：TGOS
MAP API for Web之「定位」功能官方明文僅五種：**坐標定位、地址(門牌)
定位、地標定位、道路定位、行政區定位**——**沒有「地號定位」**這一類。

本輪額外搜尋確認：TGOS官方文件（`api.tgos.tw`）之定位服務清單
（AddrLocate地址定位、POILocate地標定位、Locate/ComplexLocate複合式
定位）**皆以地址或地標名稱為輸入**，本質上是**address/POI → coordinate**
的geocoding服務，而非**district+section+land_no → coordinate**的
地籍宗地查詢服務。

**結論**：TGOS主要是**地址→座標**，不是地號→宗地座標的官方等價替代。
**不得**把TGOS當成地籍宗地座標查詢之替代方案。TGOS仍需會員申請+
APIKey（Domain/IP綁定），本輪**未**實作TGOS，維持`TGOS_AUTH_PENDING`
現況不變。

## 11. Coordinate Source Priority（§十四，依本輪Audit結果排序）

依證據強度與可行性排序（非預設順序，依查證結果決定）：

| 優先序 | 來源 | 分類 | 可行性現況 |
|---|---|---|---|
| 1 | NLSC官方宗地代表點（CadasMapPosition, CAD_001） | **OFFICIAL** | 存在但受§3 eligibility+§4 AWS IP綁定雙重前提未解決，目前**不可用** |
| 2 | NLSC官方宗地geometry→系統計算centroid | **OFFICIAL_DERIVED** | 依§9，取得管道（WFS/MAP_\*）**僅限內網**，對外部雲端架構**結構性不可行**，優先序低於#1 |
| 3 | 其他已驗證官方地籍來源 | OFFICIAL | 本輪未發現除NLSC外之其他官方地號級座標來源 |
| 4 | 呼叫端提交之已驗證parcel座標 | MIXED（視提交來源而定，見Phase API-2.1 TargetCoordinateEvidence contract） | 理論路徑存在（`SUBMITTED_BY_CALLER`），但伺服器無法驗證其真實官方性，且現行前端從未送出此欄位 |
| 5 | TGOS地址geocoding | EXTERNAL | 非地號查詢之官方等價替代（見§10），且尚未申請APIKey |
| 6 | Nominatim | EXTERNAL | 現行`DATA_PROVIDER_MODE=real`唯一實際運作之fallback，已於Phase API-2.1標記`EXTERNAL_UNVERIFIED`，不得誤標官方 |
| 7 | Demo centroid（如`JINSHAN_DISTRICT_CENTROID`） | DEMO_ONLY | 僅供示範/測試，非任何case之查估用座標 |

## 12. Facility Evidence Integration Design（§十五，設計，未實作）

```
district + section_name + land_no
        ↓
[待未來批准] OfficialParcelCoordinateProvider
   （輸入：district/section_name/land_no；
    內部：district+section_name → NLSC Sec code lookup
          → land_no encoding轉換（§9尚未解決前不得實作）
          → CadasMapPosition查詢
    輸出：TargetCoordinateEvidence，
          source_type=OFFICIAL_GIS，
          authoritative_status=OFFICIAL，
          precision_level=PARCEL）
        ↓
TargetCoordinateEvidence（Phase API-2.1既有contract，無需修改schema）
        ↓
OfficialFacilityProvider.query_facility()（Phase API-2/2.1既有邏輯，
  已正確處理target_coordinate_evidence.authoritative_status==OFFICIAL
  之情形，無需修改）
        ↓
HAVERSINE_WGS84 / STRAIGHT_LINE_REFERENCE（Phase API-2.1既有邏輯）
        ↓
FacilityEvidence（distance_authoritative_status="OFFICIAL"，
  official_distance_ready=True——僅當facility_coordinate與target_
  coordinate皆為OFFICIAL時成立，現有程式碼已支援此判定，屆時**無需
  修改**official_facility_provider.py本身）
```

此設計證實：Phase API-2.1已建立之`TargetCoordinateEvidence`/
`distance_authoritative_status`/`official_distance_ready` contract
**已具備前瞻相容性**——未來若`OfficialParcelCoordinateProvider`就緒，
只需該新Provider正確產出`authoritative_status=OFFICIAL`之
`TargetCoordinateEvidence`並傳入`ProviderContext.center_coordinate_
evidence`，既有`OfficialFacilityProvider`與`FacilityEvidence`模型
**完全不需修改**即可正確升級為`official_distance_ready=True`。距離
證據依然**不等於**grade，也**不等於**adjustment rate——此原則不因
座標來源升級而改變。

## 13. Golden Case Proof Goal（§十六）

```
金山區 / 金美段 / 489地號
        ↓
OFFICIAL_PARCEL_REPRESENTATIVE_POINT
        ↓ [本輪未取得NLSC API存取權，無法執行此步驟]
```

`TARGET_COORDINATE = 未取得`
`TARGET_COORDINATE_SOURCE = NLSC CadasMapPosition（規劃中，未存取）`
`TARGET_COORDINATE_AUTHORITY = 無法判定（因未實際取得座標）`
`TARGET_COORDINATE_CRS = 無法判定`

`GOLDEN_CASE_OFFICIAL_COORDINATE_READY = NO`——依使用者本輪明示，此為
本輪**允許且正確**之結論，未偽造任何Golden Case座標或距離結果。

## 14. API MASTER MATRIX（§十七）

| Source | Service | API Code | Input | Output | CRS | Auth | Binding | Eligible | Runtime Suitable | Parcel Point Suitable | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| NLSC | CadasMapPosition（指定地號查詢位置） | CAD_001 | City/Sec/No（地號） | XML：repX/repY＋left-bottom/right-top X/Y | 未確認（推測TWD97家族） | 需申請 | URL+IP雙綁定 | 中央機關/地方政府/國營事業 | 需先解決IP綁定與AWS架構（見§4） | **是**（若取得存取權，回傳格式最貼近「代表座標」需求） | VERIFIED_REQUIRES_APPLICATION |
| NLSC | GetLandPositionLongitudeLatitude（地段號查詢坐標） | CAD_004 | Sec+No | XML：經緯度（函式名稱暗示） | 未確認（函式名稱暗示地理經緯度，可能TWD97/WGS84相容） | 需申請 | URL+IP雙綁定 | 同上 | 同上 | 是（與CAD_001同為候選） | VERIFIED_REQUIRES_APPLICATION |
| NLSC | QryTileMapIndex(2)（地段號宗地定位） | CAD_006 | Sec+No | JSON | 未確認 | 需申請 | URL+IP雙綁定 | 同上 | 同上 | 可能（用途描述近似定位） | VERIFIED_REQUIRES_APPLICATION |
| NLSC | CadasAttrQuery（指定地號查詢土地標示資料） | CAD_007 | 地號 | JSON（地籍屬性，非座標） | N/A | 需申請 | URL+IP雙綁定 | 同上 | 同上 | **否**（僅屬性，非座標） | VERIFIED_REQUIRES_APPLICATION |
| NLSC | CadasLandNoQuery（指定範圍查詢地號清單） | CAD_010 | 範圍 | JSON（地號清單） | N/A | 需申請 | URL+IP雙綁定 | 同上 | 同上 | 否（清單非單一座標） | VERIFIED_REQUIRES_APPLICATION |
| NLSC | GetLandSecInfoNlsc（地段代碼回傳測繪段籍屬性） | CAD_008 | 地段代碼 | XML | N/A | 需申請 | URL+IP雙綁定 | 同上 | 同上 | 否（段籍屬性/代碼，非座標） | VERIFIED_REQUIRES_APPLICATION |
| NLSC | 地籍圖磚 WMS/WMTS | A1（無API代號） | 圖磚請求 | 圖磚影像 | N/A | 需申請 | URL(WMTS)+IP(WMS/WMTS) | 中央機關/地方政府/國營事業 | 不適用（僅影像） | 否 | **VISUALIZATION_ONLY**（程式化端點仍需申請；人工瀏覽器瀏覽本身公開） |
| NLSC | 地籍圖WFS | A2（無API代號） | - | GML向量（polygon） | N/A | 需申請+**僅限內網** | IP綁定 | 中央機關/地方政府/國營事業（且限內網使用） | **NOT_SUITABLE**（內網限定，外部雲端無法呼叫） | 是（若可存取，可算centroid），但存取本身不可行 | **VERIFIED_RESTRICTED**（內網限定） |
| TGOS | AddrLocate/POILocate等（Geocoding） | - | 地址/地標名稱 | 座標 | TWD97/WGS84（TGOS文件確認支援EPSG:3826等） | 需會員+APIKey | Domain/IP綁定 | 一般申請（會員制，非中央機關限定） | 需申請（Phase API-1已標記TGOS_AUTH_PENDING，未申請） | **否**（地址非地號查詢，非等價替代） | VERIFIED_REQUIRES_APPLICATION（且不適用於本需求） |

## 15. Implementation Gate（§十八）

逐項檢核：

1. API正式存在？→ **是**（CadasMapPosition等，官方PDF列明）。
2. 輸入可由Golden Case正確組成？→ **否**（§5/§6：section code與land
   number encoding皆未解決）。
3. 回傳可作宗地代表座標？→ **部分**（CAD_001/CAD_004疑似可行，但未經
   欄位級文件證實）。
4. CRS明確？→ **否**（未取得官方欄位定義，僅命名慣例推論）。
5. 使用資格可取得？→ **未確認**（需新北市政府或其他適格機關代為申請，
   非本輪可單方確認之組織決策）。
6. API Terms允許？→ **部分已知**（URL+IP雙綁定、內網限制等條款已知，
   完整條款全文未取得）。
7. AWS/Demo deployment可行？→ **有條件可行**（需NAT Gateway+Elastic
   IP，屬額外架構複雜度，見§4）。

**`SAFE_TO_IMPLEMENT_OFFICIAL_PARCEL_COORDINATE = NO`**——條件2、4、5
未滿足，條件3部分滿足、條件6部分已知、條件7有條件可行但增加複雜度。
本輪**不寫Provider**，等待下一輪批准（需先解決：新北市政府/主辦單位
之申請意願確認、NLSC欄位級技術文件取得、Golden Case之section
code/land number encoding實證）。

## 16. Final Report（§二十）

```
OFFICIAL_PARCEL_COORDINATE_AUDIT = PARTIAL
BEST_OFFICIAL_SOURCE = NLSC 國土測繪圖資服務雲（內政部國土測繪中心）
BEST_API_CODE = CAD_001（CadasMapPosition）／CAD_004（GetLandPositionLongitudeLatitude）並列候選
API_ACCESS_STATUS = VERIFIED_REQUIRES_APPLICATION
APPLICATION_REQUIRED = YES
IP_BINDING_REQUIRED = YES
GOVERNMENT_SPONSOR_REQUIRED = YES
PARCEL_POINT_SEMANTICS = REPRESENTATIVE_POINT（推論，非官方明確定義；不得標為CENTROID）
CRS_VERIFIED = NO
GOLDEN_CASE_SECTION_CODE_RESOLVED = NO
GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED = NO
GOLDEN_CASE_OFFICIAL_COORDINATE_READY = NO
NTPC_FACILITY_SEARCH_COVERAGE = NEW_TAIPEI_CITY_ONLY
ABSOLUTE_NEAREST_CLAIM_ALLOWED = NO
SAFE_TO_IMPLEMENT_OFFICIAL_PARCEL_COORDINATE = NO
CORE_FREEZE_VIOLATION = NO
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
```

本輪完成之程式碼變更（皆為Phase API-2.1既有Facility Pipeline之
additive澄清/修正，非本輪Audit主體之NLSC/TGOS實作）：

1. `docs/phase9/facility_evidence_pipeline.md`§27：訂正
   `NOMINATIM_CONTAMINATION_RISK`語意，新增`NOMINATIM_OFFICIAL_
   MISLABELING_RISK = NO`＋`NOMINATIM_REFERENCE_DISTANCE_PATH =
   PRESENT`，明確聲明Nominatim路徑依然存在，只是產生之距離已正確標示
   為MIXED_SOURCE。
2. `domain/models.py::FacilityEvidence`新增additive欄位
   `search_coverage`（預設"NEW_TAIPEI_CITY"），docstring明確聲明
   `nearest`/`matches`語意為`nearest_within_dataset_coverage`，
   **非**`nearest_global`。`providers/official_facility_provider.py`
   notes文字同步更新，明確標示搜尋範圍。
3. 新增3項測試鎖定上述語意（`test_search_coverage_field_states_new_
   taipei_city_only`等）。

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
**691 passed**，0 failed（較Phase API-2.1的688項，新增3項）。

Core Freeze 36檔案SHA-256：BEFORE=AFTER=
`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`——
`CORE_FREEZE_VIOLATION = NO`。

Cadastral Pipeline 6檔案SHA-256組合雜湊：本輪前後皆為
`cec5f900b8daf50dd3289e35f3f28a45b7dc6c51f6c7e079e7c142367c4bdc04`——
`CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO`（本輪零次Write/Edit觸及
`cadastral_identifier.py`/`cadastral_dataset_cache.py`/
`expropriation_case_provider.py`/`land_price_provider.py`/
`sync_expropriation_dataset.py`/`sync_land_price_dataset.py`）。

本輪未修改Rule/Grade/Adjustment/Calculation Engine、未修改Facility
distance rules（除§2/§3之語意澄清外，distance計算邏輯本身未變）、未
自動用Nominatim/district centroid補官方座標、未digitize WMS影像、未
OCR地籍圖、未以LLM猜測座標、未建立NLSC憑證、未建立NAT Gateway、未申請
任何API、未實作TGOS、未修改Rule/Grade Engine，符合使用者明確之範圍
限制。

（原Phase API-2.2結論至此。以下為Phase API-2.2R之更新——見下方
`CURRENT_DOCUMENTATION_CORRECTION`，**不覆蓋上方原始記錄**，僅補充/
訂正。）

---

# Phase API-2.2R — Current NLSC Documentation Re-Audit

> 目的：Phase API-2.2引用之官方PDF為110/07/05（2021）版，可能已過時。
> 本輪以目前官方管道重新查證，逐一標明`OLD_FINDING`／`CURRENT_FINDING`／
> `SOURCE_DATE`／`REASON`，**不silently overwrite歷史記錄**。AUDIT ONLY，
> 未建立NLSC/TGOS憑證、未建立AWS資源、未修改Core/Cadastral Pipeline/
> Facility Engine。本輪臨時下載之PDF存放於Claude scratchpad暫存目錄
> （session-scoped，非repo runtime source tree）。

## R0. 本輪新取得之官方來源

1. **內政部國土測繪中心《推廣教學及研究介接應用國土測繪圖資網路服務
   申請說明》**——透過`https://www.geog.ntu.edu.tw`（國立臺灣大學地理
   環境資源學系轉載之官方申請說明PDF，含官方申請書範本、附件一~五）
   下載取得。文件本身未標製表日期，但內附申請書範例日期為**112年9月**
   （2023年9月）與**113年8月**（2024年8月）之服務介接起迄期間，確認
   此文件**晚於**Phase API-2.2使用之110/07/05（2021）版本，屬**目前
   仍在使用之現行文件**。
2. 官方文件內文明確引用**現行**介接服務說明頁面
   `https://maps.nlsc.gov.tw/S09SOA`（與Phase API-2.2引用之連結相同，
   確認此為現行官方入口，未變更）。
3. 透過WebSearch取得之官方政策摘要（非單一PDF，但內容與上述PDF交叉
   一致）：確認「民營團體及公司應檢送『115年度國土測繪圖資網路服務
   訂閱申請書』申請，經審查通過或繳費後提供服務」——證實**115年度**
   （即現行、非2021年）確有一份**專供民營團體/公司**使用之訂閱申請書，
   與政府/國營/學術單位所用之「網路介接申請書及說明」為**兩條不同
   申請路徑**。
4. **未能取得**：《國土測繪圖資服務雲介接服務技術手冊(WFS及API)》
   PDF本體——嘗試透過WebSearch與WebFetch多次查詢`https://maps.nlsc.
   gov.tw/S09SOA`與其下載專區頁面（`MbIndex_qryPage.action?fun=8`），
   該入口為**JavaScript動態渲染之選單系統**，本輪工具（WebFetch將HTML
   轉為文字之靜態擷取方式）**無法執行其JS以取得實際下載連結**——這是
   **工具限制**，而非確認「需要登入才能取得」（依使用者本輪明確指示
   訂正此假設，見下方R5）。

## R1. Applicant Eligibility 重新驗證（§三）

**現行確認**（依R0.1文件逐字＋R0.3政策摘要交叉驗證）：

> 「圖資服務雲目前已發布之網路地圖服務，其中開放類API、圖臺類API及
> 多數WMS、WMTS等毋需申請、不限對象皆可免費介接使用；僅有少數服務
> 包含各式圖資之WFS、**地籍圖資服務**、門牌API、國土利用現況調查API
> 及路徑規劃API等，**僅供政府機關、國營機構及學術單位申請**，門牌API
> 另供民營機構申請使用。」

且R0.1文件本身確認：「本說明所稱之需申請介接服務內容類別如下：
(一)**地籍圖**。(二)臺灣通用電子地圖。(三)國土利用現況調查成果。
(四)模糊檢索及門牌服務。(五)路徑規劃。(六)全國門牌地址定位服務。」
——明確將**地籍圖**列為此學術申請管道涵蓋之類別之一。

**逐API重新標記**（CAD_001/CAD_004/CAD_006/CAD_007/CAD_008/CAD_010，
現行皆屬「地籍圖資服務」大類，本輪未見任何文件將其中特定代號排除於
此規則之外，故6項代號適用相同結論）：

| 申請人類型 | 是否可申請 | 條件 |
|---|---|---|
| GOVERNMENT（政府機關） | **是** | 直接以機關名義申請（一般申請書） |
| ACADEMIC（學術單位） | **是**（Phase API-2.2遺漏之現行管道） | 須為與國土測繪中心**已簽署合作協議書**之大專院校系(所)，申請人須為該系所教職員／碩士以上研究生／博士後研究員；用途限教學或研究，**不得涉及商業營利行為**；服務期限以1年為原則；系所審查→中心審查（30日內，必要時延長15日） |
| PRIVATE_COMPANY（民營公司） | **地籍圖資服務：否**（僅門牌API可，且需另一份「115年度訂閱申請書」，可能為付費制） | 明確排除於地籍圖資服務之提供對象外 |
| PRIVATE_ORGANIZATION（民營團體） | 同上，**否**（地籍圖資服務不含） | 同上 |
| INDIVIDUAL（個人） | **否**（除非以學術單位申請人身分，即隸屬已簽約系所之個人） | 無獨立申請管道 |

**訂正**：不再使用`GOVERNMENT_SPONSOR_REQUIRED = YES`這一過度簡化的
結論。更精確之狀態為：

```
APPLICANT_ENTITY_REQUIRED = GOVERNMENT_OR_STATE_OWNED_ENTERPRISE_OR_ACADEMIC_WITH_SIGNED_AGREEMENT
ELIGIBILITY_DEPENDS_ON_APPLICANT_TYPE = YES
```

**對本專案之意義**：本專案（黑客松參賽團隊）本身仍非上述三類合格
申請人，但**多了一條先前未評估之路徑**——若能與一所**已與國土測繪
中心簽署合作協議書**之大專院校地理/測量/土地相關系所合作（例如由
該系所教職員或研究生以「教學或研究」名義申請、且服務介接期間內
本專案之查詢邏輯可框架為該研究之一部分），在**非商業營利**前提下，
理論上可行。惟：(a)是否已有相關系所簽約、(b)該系所是否願意配合、
(c)黑客松競賽成果本質上是否符合「教學或研究，未涉及商業營利」之
切結要求，皆為本輪無法單方確認之組織/業務決策，**非單純技術問題**。
新北市政府（地方政府）路徑依然有效（見Phase API-2.2原始結論），
兩條路徑並存，非互斥。

## R2. Binding 重新驗證（§四）——重大訂正

Phase API-2.2依110/07/05表格之兩欄勾選（綁定URL=V、綁定IP=V）判讀為
「URL+IP雙綁定（必須同時）」。R0.1現行文件之逐字內容顯示**此判讀
需要修正**：

> 「(二)綁定IP
>  由使用該IP的伺服器（或應用程式）送出呼叫API，**資料安全性需求
>    較高的WFS、地籍圖API（MAP_001,MAP_002)限綁定IP使用**。
>  該IP必需為該伺服器專用的對外IP，不得為學校共用的對外IP。
>  地籍圖WFS及地籍圖API(MAP_001,MAP_002)限對內網提供服務，請勿對外
>    開放，惟如將取得之向量資料轉成圖片再輸出於網站不受此限。」

**關鍵字**：「**限**綁定IP使用」這句話**明確只套用於WFS與MAP_001/
MAP_002**（回傳向量幾何、資料安全性需求較高之服務）——這代表其餘
地籍API（CAD_001/CAD_004/CAD_006/CAD_007/CAD_008/CAD_010，回傳的是
代表點座標或屬性文字，非完整向量幾何）**未被此句話列為「限IP」**，
暗示其綁定方式**可能較不受限**（例如允許URL綁定作為替代）。

**但本輪誠實聲明**：現有文件**沒有**另外用同樣明確的句子逐一說明
「CAD_001綁定方式=URL或IP二選一」——此為**由排除法推論**，非逐字
確認。故本輪判定：

```
BINDING_MODE = D（視服務類別而定）
  - WFS / MAP_001 / MAP_002（向量幾何）：確認限IP綁定
  - CAD_001/004/006/007/008/010（地籍API，代表點/屬性）：現行文件
    未明確列入「限IP」之列，較可能允許URL綁定，但無逐字確認，
    維持UNCONFIRMED（非直接判定為「二選一」）
```

**訂正**：Phase API-2.2「URL+IP雙綁定（必須同時）」之結論，對CAD_\*
系列而言**證據不足**，不應維持原判定；對WFS/MAP_001/MAP_002而言，
「限IP」之結論**維持不變**（仍確認為IP綁定限定，且額外確認**僅限
內網**，此點與Phase API-2.2一致，未變更）。

## R3. AWS 重新評估（§五）

依R2之訂正，Lambda是否需要固定egress IP，**視CAD_\*系列binding
究竟是否真的限IP而定**——此點本輪**未能確認**（見R2）。因此：

```
AWS_FIXED_EGRESS_IP_REQUIRED = CONDITIONAL
```

（若CAD_\*確認限IP綁定→需要NAT Gateway+Elastic IP設計，同Phase
API-2.2原方案；若CAD_\*確認可用URL綁定→Lambda搭配API Gateway自訂
網域（Custom Domain Name）即可滿足「綁定URL」，**完全不需要**NAT
Gateway/Elastic IP，架構大幅簡化、成本大幅降低。）

**Design比較（僅設計，不建立資源）**：

| 架構 | 適用情境 | 優點 | 缺點 |
|---|---|---|---|
| Lambda（no VPC）→ API Gateway自訂網域 → NLSC（URL綁定） | 若CAD_\*確認支援URL綁定 | 無需VPC/NAT Gateway，架構單純，成本低，維持現有serverless設計 | 仰賴CAD_\*確實支援URL綁定（未確認） |
| Lambda（VPC內）→ NAT Gateway → Elastic IP → NLSC（IP綁定） | 若CAD_\*確認限IP綁定（同WFS/MAP_\*） | 確定可行（前提eligibility解決） | 需VPC設計、NAT Gateway常態成本、Lambda冷啟動時間增加 |

## R4. 現行NLSC Matrix（§十）

| Service | Current Official Doc Date | Applicants | Binding | Output | CRS | Parcel Point | AWS Suitable | Status |
|---|---|---|---|---|---|---|---|---|
| CAD_001 CadasMapPosition | 現行（R0.1文件，晚於2021） | GOV/SOE/ACADEMIC(簽約) | UNCONFIRMED（推論非限IP，見R2） | XML，repX/repY+bbox | UNCONFIRMED | 是（候選） | CONDITIONAL | VERIFIED_REQUIRES_APPLICATION |
| CAD_004 GetLandPositionLongitudeLatitude | 現行 | GOV/SOE/ACADEMIC(簽約) | UNCONFIRMED | XML，經緯度 | UNCONFIRMED | 是（候選） | CONDITIONAL | VERIFIED_REQUIRES_APPLICATION |
| CAD_006 QryTileMapIndex(2) | 現行 | GOV/SOE/ACADEMIC(簽約) | UNCONFIRMED | JSON | UNCONFIRMED | 可能 | CONDITIONAL | VERIFIED_REQUIRES_APPLICATION |
| CAD_007 CadasAttrQuery | 現行 | GOV/SOE/ACADEMIC(簽約) | UNCONFIRMED | JSON（屬性非座標） | N/A | 否 | CONDITIONAL | VERIFIED_REQUIRES_APPLICATION |
| CAD_008 GetLandSecInfoNlsc | 現行 | GOV/SOE/ACADEMIC(簽約) | UNCONFIRMED | XML（段籍代碼屬性） | N/A | 否（但為Golden Case段代碼查詢候選） | CONDITIONAL | VERIFIED_REQUIRES_APPLICATION |
| CAD_010 CadasLandNoQuery | 現行 | GOV/SOE/ACADEMIC(簽約) | UNCONFIRMED | JSON（地號清單） | N/A | 否 | CONDITIONAL | VERIFIED_REQUIRES_APPLICATION |
| Cadastral WMS（地籍圖磚） | 現行 | GOV/SOE/ACADEMIC（推論，見R1，未見明確排除） | 確認需申請（非完全免申請） | 圖磚影像 | N/A | 否 | 不適用 | VISUALIZATION_ONLY（人工瀏覽公開；程式化端點需申請） |
| Cadastral WMTS | 同上 | 同上 | 同上 | 圖磚影像 | N/A | 否 | 不適用 | VISUALIZATION_ONLY |
| Cadastral WFS | 現行，R0.1文件重新確認 | GOV/SOE/ACADEMIC(簽約)，且**僅限內網使用** | **確認限IP綁定**（未變更） | GML向量（polygon） | N/A | 是（若可存取） | **NOT_SUITABLE**（內網限定，外部雲端無法呼叫，此結論維持不變） | VERIFIED_RESTRICTED |

## R5. Technical Manual 可用性訂正

Phase API-2.2文件中**未明確主張**「技術文件需登入」（該輪誠實記錄為
「未能取得，疑似需登入後之開發者專區」），但本輪依使用者指示重新
確認：**入口本身並無登入牆之直接證據**——`https://maps.nlsc.gov.tw/
S09SOA`及其下載專區`MbIndex_qryPage.action?fun=8`皆為**公開可訪問
之URL**（無需帳密即可開啟頁面本身），問題在於該頁面以JavaScript
動態載入選單/檔案清單內容，本輪所用之研究工具（WebFetch，將頁面
轉換為靜態文字後分析）**無法執行該JS**，因此無法取得選單展開後的
實際下載連結——這是**工具/方法限制**，不是「文件本身需要登入才能
看到」。

```
CURRENT_DOCUMENTATION_CORRECTION:
  - OLD_FINDING: "CAD_001等地籍API技術文件疑似需登入開發者專區方可查閱"
  - CURRENT_FINDING: "S09SOA入口本身公開可訪問，技術手冊下載連結因
      該頁面採JavaScript動態渲染，本輪工具無法解析取得，非確認需登入"
  - SOURCE_DATE: 本輪（Phase API-2.2R）WebFetch/WebSearch多次嘗試，
      2026-09（本輪執行時間）
  - REASON: 使用者明確指出不應假設需登入，經重新嘗試後訂正為
      「工具限制」而非「登入限制」，兩者對應之後續行動不同
      （前者建議未來以真人瀏覽器實際操作確認，後者才需要帳密）
```

`TECHNICAL_MANUAL_AVAILABLE = PARTIAL`——手冊存在且入口公開，但本輪
研究工具未能實際取得其完整內容。

## R6. Golden Case Mapping 再次嘗試（§八）

依R0.1文件與R5之限制，本輪**仍未能**取得CAD_001/CAD_004所需之
`City`/`Sec`/`No`欄位格式規範（技術手冊未能取得，見R5），亦未找到
公開、免申請之`ListCounty`/`ListTown`/`ListLandSection`類服務。

```
CAD_001_REQUEST_SCHEMA_RESOLVED = NO
CAD_001_RESPONSE_SCHEMA_RESOLVED = NO
CAD_001_CRS_RESOLVED = NO
GOLDEN_CASE_SECTION_CODE_RESOLVED = NO
GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED = NO
```

依使用者本輪指示「只有最新技術手冊仍不足才標UNRESOLVED」——本輪已
使用現行（非2021）文件重新嘗試，**技術手冊本身仍不足**（R5：可及性
問題，非文件不存在），故上述UNRESOLVED標記為本輪之正確、誠實結論，
非因怠於嘗試。

## R7. Implementation Gate 重新輸出（§十二）

```
APPLICATION_REQUIRED = YES
APPLICANT_ENTITY_REQUIRED = GOVERNMENT_OR_STATE_OWNED_ENTERPRISE_OR_ACADEMIC_WITH_SIGNED_AGREEMENT
PRIVATE_COMPANY_ELIGIBLE = NO（地籍圖資服務類別明確排除；其餘類別可能另有付費訂閱管道，與本需求無關）
ACADEMIC_ELIGIBLE = YES（條件：已簽約系所+教學或研究用途+非商業營利+30日審查）
GOVERNMENT_SPONSOR_REQUIRED = NO（訂正——不再是唯一路徑，改用ELIGIBILITY_DEPENDS_ON_APPLICANT_TYPE）
BINDING_MODE = D（視服務類別而定；WFS/MAP_001/002確認限IP，CAD_*系列UNCONFIRMED）
AWS_FIXED_EGRESS_IP_REQUIRED = CONDITIONAL
TECHNICAL_MANUAL_AVAILABLE = PARTIAL（入口公開，內容本輪工具未能取得）
CAD_001_REQUEST_SCHEMA_RESOLVED = NO
CAD_001_RESPONSE_SCHEMA_RESOLVED = NO
CAD_001_CRS_RESOLVED = NO
GOLDEN_CASE_SECTION_CODE_RESOLVED = NO
GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED = NO
SAFE_TO_IMPLEMENT_OFFICIAL_PARCEL_COORDINATE = NO
```

**與Phase API-2.2比較**：`SAFE_TO_IMPLEMENT`結論**維持NO**（技術文件
與Golden Case映射問題依然存在），但**eligibility圖像明顯轉為更樂觀**
（訂正前誤判為「僅新北市政府可行」，訂正後確認「新北市政府或合作
學術系所皆為可行路徑」），且**binding/AWS結論由確定性負面（一定要
NAT Gateway）轉為條件式**（可能不需要，視CAD_\*系列binding細節而定，
待技術手冊實際取得後可再確認）。此為本輪Re-Audit之核心價值：修正了
Phase API-2.2因舊版文件而**過度悲觀**之eligibility與AWS結論，而非
發現新的阻礙。

## R8. Freeze複查

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：**691
passed**，0 failed（本輪僅新增/修改docs，未變更任何程式碼，測試數與
Phase API-2.2結束時完全相同）。

Core Freeze 36檔案SHA-256：BEFORE=AFTER=
`0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d1a51`——
`CORE_FREEZE_VIOLATION = NO`。

Cadastral Pipeline 6檔案SHA-256組合雜湊：本輪前後皆為
`cec5f900b8daf50dd3289e35f3f28a45b7dc6c51f6c7e079e7c142367c4bdc04`——
`CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO`。

Facility Engine（`providers/official_facility_provider.py`／
`providers/facility_dataset_cache.py`／`domain/models.py`之Facility
相關model）：本輪**零次**Write/Edit觸及，僅新增/編輯docs檔案。

本輪臨時下載之PDF（`nlsc_academic_apply.pdf`）存放於Claude session
scratchpad暫存目錄，**未**存入repo runtime source tree。

（Phase API-2.2R結論至此。以下為Phase API-2.2R2之重大更新——本輪首次
成功實際取得NLSC技術文件本體並活呼叫公開API，見下方。**不覆蓋以上
記錄**，僅補充/訂正。）

---

# Phase API-2.2R2 — Current NLSC Source Reconciliation + Open Code API Verification

> AUDIT + LIVE VERIFICATION ONLY。未實作OfficialParcelCoordinateProvider/
> TGOSProvider/任何NLSC runtime Provider，未申請帳號，未建立AWS資源。
> 本輪對**確認為開放（無may_apply圖示）**之COM_003/004/006三個API進行
> 真實活呼叫（GET請求，無認證資訊），對CAD_001/CAD_004等**需申請**之
> API僅取得其**公開技術文件內容**（未嘗試實際呼叫，因其標示需申請，
> 貿然呼叫屬未授權存取嘗試，本輪主動避免）。

## S0. 方法論突破：`Api_ajax_list.jsp`與逐API`.../pro/{ApiName}.jsp`

前兩輪（Phase API-2.2／2.2R）因`https://maps.nlsc.gov.tw/S09SOA`為
JavaScript動態渲染頁面，WebFetch工具無法解析其選單/技術文件連結。本輪
直接以`urllib`對該頁面實際載入之AJAX端點發出GET請求（非猜測，係從
搜尋引擎索引結果中找到`Api_ajax_list.jsp`此一真實存在之URL後驗證），
成功取得**未經JS渲染之原始HTML**，其中：

1. `https://maps.nlsc.gov.tw/S09SOA/pro/Api_ajax_list.jsp`——完整API
   清單，每個API條目之HTML明確標示是否含`may_apply.png`圖示（需申請
   標記）。**COM_001~COM_017（全部歸類於"開放"分類頁籤）皆無此圖示，
   CAD_\*/MAP_\*/ADR_\*/LUI_\*/ROU_\*則皆有**——此為本輪對Phase
   API-2.2R之`BINDING`／`ELIGIBILITY`推論的**逐位元組HTML證據**，非
   摘要臆測。
2. 每個API條目之html皆含對應技術文件之AJAX載入路徑，例如
   `./pro/ListCounty.jsp`、`./pro/CadasMapPosition.jsp`、
   `./pro/GetLandPositionLongitudeLatitude.jsp`——**這些個別技術文件
   頁面本身可直接以GET請求取得，無需登入**，此為Phase API-2.2R誤判
   為「需登入」之根本原因所在的直接反證。

## S1. PRIVATE_COMPANY / PRIVATE_ORGANIZATION / INDIVIDUAL Eligibility 更正（§一）

使用者指出Phase API-2.2R之`PRIVATE_COMPANY_ELIGIBLE = NO`與現行NLSC
115年度商用公告衝突。本輪**未能取得**一份具official date標記、逐字
列出「民營團體及公司可申請地籍API」之PDF公告本體（多次搜尋僅得到
search引擎之摘要式陳述，無法如R2對CAD_001技術文件般取得逐字HTML
證據）。因此本輪**不採用非逐字確認之推翻**，但**同時承認**：

- Phase API-2.2R所引用之摘要（「僅供政府機關、國營機構及學術單位申請」
  排除民營）與使用者本輪引用之摘要（「政府機關、國營機構、學術單位、
  民營團體、公司皆可申請」）**兩者皆為search引擎摘要層級，非本輪逐字
  驗證之PDF原文**——兩者證據強度相同（皆為間接摘要），本輪**沒有
  更強證據可以偏好任一方**。
- 已確認之**間接但一致**證據：兩輪搜尋皆一致提到「民營團體及公司
  應檢送**115年度國土測繪圖資網路服務訂閱申請書**申請，經審查通過
  或**繳費**後提供服務」——這句話本身**兩輪皆出現、用字相同**，代表
  這是穩定、可信的現行事實：**民營團體/公司確實有一條專屬申請/訂閱
  管道**，differs from政府/學術之「網路介接申請書」。此管道是否涵蓋
  地籍API（CAD_\*），**兩輪之搜尋摘要對此點的措辭有出入**（一輪稱
  「僅有少數服務...僅供政府機關、國營機構及學術單位申請」暗示排除
  地籍；使用者本輪引用之文字則明確列出「地籍API」為115年度商用訂閱
  可申請項目之一）。

**本輪誠實判定**（証據不足以偏向排除或包含，故不得各打五十大板地
維持舊結論，也不得直接採信新指稱）：

```
PRIVATE_COMPANY_ELIGIBLE = CONDITIONAL
PRIVATE_ORGANIZATION_ELIGIBLE = CONDITIONAL
INDIVIDUAL_ELIGIBLE = UNCONFIRMED
```

`CONDITIONAL`原因：確認存在「115年度訂閱申請書」此一民營專屬管道，
但該管道**是否涵蓋CAD_\*地籍API本身**，本輪兩份間接摘要說法不一致，
需要**實際取得該訂閱申請書PDF原文**（本輪嘗試以`Download.action?
fileName=115年度國土測繪圖資網路服務訂閱申請書.pdf`等常見檔名猜測
下載，**未成功**——見下方S1.1）方能一錘定音。

### S1.1 115年度訂閱申請書下載嘗試（失敗，誠實記錄）

比照S7成功取得技術手冊之方法（`Download.action?fileName=...`直接
猜測檔名），本輪嘗試以下檔名皆**未成功**（HTTP 404或405）：
「115年度國土測繪圖資網路服務訂閱申請書.pdf」、「網路服務訂閱申請書.
pdf」、「商用訂閱申請書.pdf」等常見組合。**不排除**實際檔名與本輪
猜測不同（如年度數字格式、全形/半形括號等差異），本輪未窮舉所有可能
檔名組合。

**結論**：`PRIVATE_COMPANY_ELIGIBLE`／`PRIVATE_ORGANIZATION_ELIGIBLE`
維持`CONDITIONAL`，非本輪之前武斷的NO，也非直接採信使用者所述之YES，
待未來實際取得該訂閱申請書原文後可一次性解決。

## S2. Source Precedence Matrix（§二）

| source_title | authority | published_date | scope | applicant_type | service_scope | freshness | superseded_status |
|---|---|---|---|---|---|---|---|
| 國土測繪圖資服務雲申請服務介接說明表 | NLSC | 110/07/05（2021） | 全服務清單 | 政府/國營（表格原文未見學術/民營欄位） | 全部CAD_\*/MAP_\*/WFS/WMS等 | 低（已知有更新版本） | **SUPERSEDED**（Phase API-2.2使用，Phase API-2.2R起訂正部分結論） |
| 推廣教學及研究介接應用國土測繪圖資網路服務申請說明 | NLSC | 未標製表日期，內附範例申請書日期112年9月/113年8月 | 學術單位專屬管道 | 已簽約系所之教職員/碩士以上研究生/博士後研究員 | 地籍圖/臺灣通用電子地圖/國土利用/模糊檢索門牌/路徑規劃/全國門牌 | 中高（晚於2021版，仍在使用） | **CURRENT**（Phase API-2.2R新增引用） |
| Api_ajax_list.jsp + 逐API`.../pro/*.jsp`技術文件 | NLSC（S09SOA系統本體） | 無版本日期標示（動態頁面，即時反映現行系統設定） | 每個API之逐字技術contract | 不適用（純技術文件，非申請資格文件） | CAD_001/004/006/007/008/010/COM_003/004/006等本輪逐一取得 | **最高**（系統本體、非轉載/摘要，即時） | **CURRENT**（Phase API-2.2R2本輪新增，本輪最主要依據來源） |
| 國土測繪圖資服務雲介接服務技術手冊.pdf | NLSC | 未於檔案本體確認（3.7MB，本輪工具未能渲染內文） | 綜合技術手冊（WFS+API） | 不適用 | 全部服務 | 中（已下載，內容未逐頁驗證） | **CURRENT**（本輪成功下載，但內容未逐頁核實，優先權低於S09SOA即時頁面） |
| 115年度商用訂閱相關摘要（兩輪之search引擎摘要，非PDF原文） | 間接（非NLSC一手PDF） | 「115年度」（暗示現行） | 民營團體/公司專屬管道 | 民營團體/公司 | 兩輪摘要對是否含地籍API說法不一 | 低（皆為二手摘要） | **UNCONFIRMED**（未達可裁決之證據等級） |

**原則確認**：本輪`Api_ajax_list.jsp`與逐API技術文件頁**優先權最高**
（系統本體即時內容），其次為NLSC自行發布之PDF（技術手冊、學術申請
說明），2021舊表格與純search摘要**降級為HISTORICAL/UNCONFIRMED**，
但**不刪除**，皆保留於Phase API-2.2/2.2R原始章節供追溯。

## S3. Open API 實際驗證（§三、§四）——真實活呼叫，非文件推測

### S3.1 HTML圖示證據（may_apply.png有無）

由`Api_ajax_list.jsp`原始HTML逐位元組確認：

| API | 所屬分類頁籤 | 第一格`<td>`是否含`may_apply.png` | 判定 |
|---|---|---|---|
| COM_003 ListCounty | 開放 | **否**（`<td width="10%"></td>`空白） | **確認開放，無需申請** |
| COM_004 ListTown | 開放 | **否** | **確認開放，無需申請** |
| COM_006 ListLandSection | 開放 | **否** | **確認開放，無需申請** |
| CAD_001 CadasMapPosition | 地籍 | **是** | 需申請（不變） |
| CAD_004 GetLandPositionLongitudeLatitude | 地籍 | **是** | 需申請（不變） |

### S3.2 真實活呼叫結果（GET，無任何認證資訊）

```
GET https://api.nlsc.gov.tw/other/ListCounty
→ HTTP 200，XML，21個縣市代碼，包含 <countycode>F</countycode><countyname>新北市</countyname>

GET https://api.nlsc.gov.tw/other/ListTown/F
→ HTTP 200，XML，29個行政區代碼，包含 <towncode>F25</towncode><townname>金山區</townname>

GET https://api.nlsc.gov.tw/other/ListLandSection/F/F25
→ HTTP 200，XML，26筆地段，包含
  <office>FD</office><officestr>汐止</officestr>
  <sectcode>1027</sectcode><sectstr>金美段</sectstr>
```

`OPEN_LISTCOUNTY_VERIFIED = YES`
`OPEN_LISTTOWN_VERIFIED = YES`
`OPEN_LISTLANDSECTION_VERIFIED = YES`

## S4. Golden Case Section Code——正式解決（§四）

```
NEW_TAIPEI_CITY_CODE = F
JINSHAN_TOWN_CODE = F25
JINMEI_SECTION_CODE = 1027（office=FD，officestr=汐止，sectstr=金美段）
```

`ListLandSection`回傳之26筆地段清單中，「金美段」**僅出現一次**
（exact normalization match，非fuzzy比對，無需MANUAL_REVIEW_REQUIRED）。
完整原始三個欄位（`office`/`sectcode`/`sectstr`）皆已保存於本文件
（見§S3.2），未省略任何欄位。

**重要附帶發現**：金山區（行政區）之地籍段隸屬地政事務所為**汐止**
（`office=FD`／`officestr=汐止`），而非「金山地政事務所」——確認
地政事務所轄區與行政區並非一對一對應，此為官方資料本身之事實，非
本專案臆測。

`GOLDEN_CASE_SECTION_CODE_RESOLVED = YES`

## S5. Open API Authentication 確認（§五）

`ListCounty`/`ListTown`/`ListLandSection`三個活呼叫**皆為純GET請求，
未帶任何Username/Token/Password/API Key**，且**皆成功回傳HTTP 200**
——確認：

```
CURRENT_S09SOA_OPEN_API_AUTH = NONE（無需任何認證資訊）
```

與使用者提及之舊e-GNSS類文件（Username/Token/Password模式）**明確
區分為不同系統**——本輪未在`api.nlsc.gov.tw`/`S09SOA`任何一個開放API
之技術文件或實際呼叫中發現token/key相關參數，**不得**將e-GNSS之
authentication模型套用於COM_003/004/006。

## S6. Technical Manual：狀態訂正 + 實際取得（§六、§七）

依使用者指示訂正混合概念：

```
TECHNICAL_MANUAL_AVAILABLE = YES
TECHNICAL_MANUAL_RETRIEVED = YES
```

**實際取得記錄**：

| 欄位 | 內容 |
|---|---|
| title | 國土測繪圖資服務雲介接服務技術手冊 |
| download_url | `https://maps.nlsc.gov.tw/S09SOA/Download.action?fileName=國土測繪圖資服務雲介接服務技術手冊.pdf`（URL編碼後） |
| retrieved_at | 本輪執行時間（2026-09，session時間戳記見下方檔案） |
| file_size | 3,794,120 bytes |
| sha256 | `bc3277024b25ea6e03ba62655d8a9fed2e9811e5a1aa509df49244048c858cf5` |
| document_date/version | 檔案本體未見版本頁碼標示，本輪工具環境缺少PDF渲染套件（poppler-utils），**未能逐頁解析內文** |

**誠實聲明**：此PDF**已成功下載**（存放於Claude session scratchpad，
未存入repo），但因本環境缺少PDF轉圖工具，本輪**未逐頁閱讀其內文**。
不過，本輪**已透過另一條同樣官方、同一系統（`S09SOA`）之管道**——
逐API之`.../pro/{ApiName}.jsp`技術文件頁面——**取得與此手冊同等或
更即時**之技術contract內容（見§S7），故CAD_001/CAD_004等關鍵contract
之解析**不依賴**此PDF能否被渲染，兩者互為佐證而非唯一依賴。

## S7. CAD_001 / CAD_004 完整Contract（§九、§十）——VERIFIED_DOCUMENTED

直接取自`https://maps.nlsc.gov.tw/S09SOA/pro/CadasMapPosition.jsp`與
`.../pro/GetLandPositionLongitudeLatitude.jsp`之官方逐字內容（非推測）：

### CAD_001 CadasMapPosition

```
CAD_001_ENDPOINT = https://api.nlsc.gov.tw/dmaps/CadasMapPosition/{City}/{Sec}/{No}[/{CRS}]  — VERIFIED_DOCUMENTED
CAD_001_METHOD = GET（路徑參數式）— VERIFIED_DOCUMENTED
CAD_001_REQUEST_FIELDS = City（縣市代碼）/Sec（地段代碼）/No（地號8碼）/[坐標類別代碼：4326或3826，預設經緯度] — VERIFIED_DOCUMENTED
CAD_001_AUTH = 需申請（見§S3.1之may_apply圖示），但技術文件本身未見token/key請求參數 — VERIFIED_DOCUMENTED（無token）+ UNCONFIRMED（實際binding執行機制）

CITY_FORMAT = 單一英文字母（如F=新北市，來自ListCounty） — VERIFIED_DOCUMENTED
TOWN_REQUIRED = 否（CAD_001本身不需town參數；但取得Sec本身仍需先經ListTown查出town以供ListLandSection使用） — VERIFIED_DOCUMENTED
SEC_FORMAT = 4碼數字字串（地段代碼，例："0012"，與ListLandSection之sectcode同格式） — VERIFIED_DOCUMENTED
NO_FORMAT = 8碼數字字串，官方範例"00010000" — VERIFIED_DOCUMENTED

CAD_001_RESPONSE_FIELDS = repX, repY, ldX, ldY, rtX, rtY — VERIFIED_DOCUMENTED
REP_X_DEFINITION = 官方文件僅稱「代表點X」，未使用centroid/質心/形心等字眼 — VERIFIED_DOCUMENTED（一定不是官方稱為centroid，語意仍維持OFFICIAL_PARCEL_REPRESENTATIVE_POINT，非CENTROID）
REP_Y_DEFINITION = 「代表點Y」 — VERIFIED_DOCUMENTED
BOUNDING_BOX_FIELDS = ldX/ldY（左下）、rtX/rtY（右上） — VERIFIED_DOCUMENTED

CRS = 可選4326（WGS84經緯度，未指定時之預設）或3826（TWD97 TM2） — VERIFIED_DOCUMENTED
DATUM = WGS84（當CRS=4326時） — VERIFIED_DOCUMENTED
COORDINATE_ORDER = repX=經度、repY=緯度（依官方範例repX=120.683552、repY=24.142313之數值範圍判定，台灣經度約120-122、緯度約22-25） — VERIFIED_DOCUMENTED（由範例數值範圍佐證，非文件逐字明講lon在前，但數值範圍已無歧義）
```

### CAD_004 GetLandPositionLongitudeLatitude

```
CAD_004_ENDPOINT = https://api.nlsc.gov.tw/S_Maps_WebService/qryLand/GetLandPositionLongitudeLatitude/{City}/{Sec}/{No}（landmaps.nlsc.gov.tw同義；另有XML包裹之POST-style變體） — VERIFIED_DOCUMENTED
CAD_004_METHOD = GET（同時支援簡式路徑與XML query string兩種呼叫方式） — VERIFIED_DOCUMENTED
CAD_004_INPUT = City、Sec、No（8碼） — VERIFIED_DOCUMENTED
CAD_004_OUTPUT = XML：<LONGITUDE>、<LATITUDE>、<STATUS>（回應本身宣告encoding="BIG5"，非UTF-8——實作時須注意） — VERIFIED_DOCUMENTED
CAD_004_CRS = WGS84（官方文件逐字："傳回結果：XML；WGS84坐標"，比CAD_001更明確，無需額外指定參數） — VERIFIED_DOCUMENTED
CAD_004_COORDINATE_SEMANTICS = 直接查詢座標（非representative point+bbox語意），回應更單純，僅一組經緯度 — VERIFIED_DOCUMENTED
```

### CAD_001 vs CAD_004 比較

若僅需單一座標點供距離計算（本專案OfficialFacilityProvider之實際
需求），**CAD_004語意更直接、CRS保證更明確（固定WGS84，無需額外
指定參數）**；CAD_001額外提供bounding box（可用於未來評估宗地範圍
大小，但本專案本輪不需要），且CRS需顯式選擇4326才等同WGS84（省略
時預設亦為經緯度，實務上等價，但CAD_004的官方文件用詞更斬釘截鐵）。
**建議未來若獲准實作，優先採用CAD_004**作為主要representative
coordinate來源，CAD_001作為輔助（若需bbox資訊時才加查）。兩者
eligibility/binding要求相同（同屬地籍API家族）。

## S8. Land Number Encoding——正式解決（§十一）

四個獨立官方範例交叉驗證，**全數一致**：

| API | 官方範例地號 | 格式 |
|---|---|---|
| CAD_001 | `00010000` | 8碼，zero-padded |
| CAD_004 | `05090000` | 8碼，zero-padded |
| CAD_007（CadasAttrQuery） | `08920000`（亦接受簡式`892`） | 8碼，zero-padded；**另支援簡式** |
| CAD_010（CadasLandNoQuery回應） | `09770001`~`09770006` | 8碼，zero-padded |

格式與本專案既有`LandNumberNormalizer.to_land_price_lid()`（4碼主號+
4碼子號，zero-padded，共8碼）**完全相同**。Golden Case「489地號」
（主號489、子號0）依此格式應表示為：

```
GOLDEN_CASE_NO = 04890000
```

`GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED = YES`

**但誠實備註**：CAD_007文件額外揭示部分API接受「簡式」（如`892`）
作為`00010000`格式的替代寫法——本輪**未確認**CAD_001/CAD_004是否
同樣接受簡式（其官方範例僅展示8碼式），故若未來實作，**應優先採用
已驗證之8碼式**（`04890000`），簡式僅供CAD_007等已明確文件支援之
API使用，不得跨API假設通用。

## S9. Binding Matrix（§十三）——維持Phase API-2.2R推論，本輪未獲新證據

本輪逐API技術文件頁（`.../pro/{ApiName}.jsp`）**皆為純技術request/
response contract，未包含binding（URL/IP）相關說明**——binding資訊
屬帳號申請層級文件（見Phase API-2.2R之學術申請說明PDF），非個別API
技術文件之內容範疇。故本輪**未取得**逐API binding細節之新證據，
Binding Matrix維持Phase API-2.2R之推論結果：

| Service | URL Binding | IP Binding | Either | IP Only | Both Required | Status |
|---|---|---|---|---|---|---|
| CAD_001 | UNCONFIRMED | UNCONFIRMED | 可能（推論） | 未確認 | 未確認 | UNCONFIRMED（非WFS/MAP_\*，推論較不受限） |
| CAD_004 | UNCONFIRMED | UNCONFIRMED | 可能（推論） | 未確認 | 未確認 | UNCONFIRMED |
| CAD_006 | UNCONFIRMED | UNCONFIRMED | 可能（推論） | 未確認 | 未確認 | UNCONFIRMED |
| MAP_001 | 否 | 是 | 否 | **是**（學術文件逐字確認） | - | **VERIFIED_RESTRICTED**（+僅限內網） |
| MAP_002 | 否 | 是 | 否 | **是**（同上） | - | **VERIFIED_RESTRICTED**（+僅限內網） |
| WFS（地籍圖） | 否 | 是 | 否 | **是**（同上） | - | **VERIFIED_RESTRICTED**（+僅限內網） |
| WMTS（地籍圖磚） | 部分（依2021表格） | 部分 | 可能 | 未見「限IP」字樣 | 未確認 | UNCONFIRMED |

**附帶觀察（非結論）**：CAD_004文件同時列出`api.nlsc.gov.tw`與
`landmaps.nlsc.gov.tw`兩個mirror主機，是否分別對應「URL綁定」與
「IP綁定」兩種帳號類型之不同進入點，本輪**未找到明確說明**，僅為
一項值得未來驗證之觀察，不作為結論依據。

## S10. AWS Decision（§十四）——維持CONDITIONAL

依S9，CAD_001/CAD_004之binding模式本輪**仍未確認**是否為IP_ONLY，
故：

```
AWS_FIXED_EGRESS_IP_REQUIRED = CONDITIONAL
```

（維持Phase API-2.2R之判定與設計比較表，本輪未發現需修改該設計
比較之新證據。）

## S11. Phase API-2.2R2 CURRENT_SOURCE_RECONCILIATION 總表（§十五）

| # | OLD_FINDING | R1_FINDING（Phase API-2.2R） | R2_CURRENT_FINDING（本輪） | CURRENT_SOURCE | SOURCE_DATE | WHY_CHANGED |
|---|---|---|---|---|---|---|
| 1 | PRIVATE_COMPANY_ELIGIBLE=NO（2021表格未列民營） | PRIVATE_COMPANY_ELIGIBLE=NO（search摘要：地籍圖資僅供政府/國營/學術） | PRIVATE_COMPANY_ELIGIBLE=**CONDITIONAL**（兩輪摘要說法衝突，且115年度訂閱申請書原文未能取得，證據不足以偏向任一方） | 兩份search引擎摘要（皆非PDF原文） | 皆稱「115年度」 | 使用者指出與現行公告衝突，經重新檢視發現雙方證據皆為間接摘要，不應武斷維持NO |
| 2 | 技術文件「疑似需登入」 | 訂正為「工具限制，非登入限制」 | **實際取得**，確認純GET、無登入、無token | `Api_ajax_list.jsp`+逐API`.../pro/*.jsp` | 系統即時內容 | 找到AJAX端點真實URL，繞過JS渲染工具限制 |
| 3 | CAD_001 CRS未確認 | CAD_001 CRS未確認 | **VERIFIED_DOCUMENTED**：可選4326(預設)/3826 | CadasMapPosition.jsp官方逐字 | 系統即時內容 | 直接取得官方技術文件原文 |
| 4 | CAD_004 CRS未確認 | CAD_004 CRS未確認 | **VERIFIED_DOCUMENTED**：固定WGS84 | GetLandPositionLongitudeLatitude.jsp官方逐字 | 系統即時內容 | 同上 |
| 5 | GOLDEN_CASE_SECTION_CODE_RESOLVED=NO | 維持NO | **YES**（F/F25/1027） | ListCounty/ListTown/ListLandSection活呼叫 | 本輪即時查詢 | 確認三API皆為開放API後直接活呼叫取得真實資料 |
| 6 | GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED=NO | 維持NO | **YES**（04890000，8碼zero-padded，四筆官方範例交叉驗證） | CAD_001/004/007/010官方範例 | 系統即時內容 | 官方文件範例本身即完整揭露此格式，非推測 |
| 7 | TECHNICAL_MANUAL_AVAILABLE=PARTIAL（狀態混用） | 同左 | **AVAILABLE=YES，RETRIEVED=YES**（兩個獨立布林狀態） | Download.action直接下載成功 | 手冊檔案本體 | 找到正確檔名後直接下載成功，且逐API技術頁面亦已取得等效內容 |
| 8 | BINDING_MODE推論（D，未逐字確認CAD_\*） | 同左 | 維持UNCONFIRMED（本輪技術文件頁不含binding資訊，此為文件範疇問題非查證怠惰） | （無新來源） | - | binding屬帳號申請層級文件，與逐API技術contract分屬不同文件範疇 |

## S12. Implementation Gate 重新輸出（§十六）

```
CURRENT_NLSC_DOCS_VERIFIED = YES
PRIVATE_COMPANY_ELIGIBLE = CONDITIONAL
PRIVATE_ORGANIZATION_ELIGIBLE = CONDITIONAL
INDIVIDUAL_ELIGIBLE = UNCONFIRMED
TECHNICAL_MANUAL_AVAILABLE = YES
TECHNICAL_MANUAL_RETRIEVED = YES
OPEN_LISTCOUNTY_VERIFIED = YES
OPEN_LISTTOWN_VERIFIED = YES
OPEN_LISTLANDSECTION_VERIFIED = YES
GOLDEN_CASE_SECTION_CODE_RESOLVED = YES
GOLDEN_CASE_LAND_NUMBER_ENCODING_RESOLVED = YES
CAD_001_REQUEST_SCHEMA_RESOLVED = YES
CAD_001_RESPONSE_SCHEMA_RESOLVED = YES
CAD_001_CRS_RESOLVED = YES
CAD_001_BINDING_MODE = UNCONFIRMED
AWS_FIXED_EGRESS_IP_REQUIRED = CONDITIONAL
SAFE_TO_IMPLEMENT_OFFICIAL_PARCEL_COORDINATE = PARTIAL
```

**與Phase API-2.2R比較**：本輪是三輪Audit中最關鍵的一輪——先前
阻礙`SAFE_TO_IMPLEMENT`的兩大技術缺口（Golden Case座標映射鏈路、
CAD_001/004 request/response contract）**已完全解決**。目前唯一
仍未解決的是**eligibility細節（民營資格CONDITIONAL）**與**binding
機制（IP/URL）**——這兩項**不是技術問題，而是帳號申請層級的行政/
契約問題**，需要實際向NLSC申請或取得115年度訂閱申請書原文才能一次
性釐清，非本輪（AUDIT ONLY，禁止申請帳號）可單方解決。故整體判定
由Phase API-2.2R之`NO`**上修為`PARTIAL`**——技術可行性已大幅提升，
僅剩行政資格與binding細節待確認。

## S13. Freeze複查

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：本輪
僅新增/編輯docs與臨時下載檔案（存放於Claude session scratchpad），
未變更任何repo程式碼，測試數與前一輪完全相同。

Core Freeze／Cadastral Pipeline／Facility Engine：本輪**零次**Write/
Edit觸及`engine/`、`data/rules/`、`schemas/`、已凍結之6個cadastral
pipeline檔案、或`providers/official_facility_provider.py`／
`providers/facility_dataset_cache.py`／`domain/models.py`中Facility
相關model。僅新增/編輯本文件（`docs/phase9/official_parcel_
coordinate_audit.md`）。

本輪下載之所有檔案（`api_ajax_list.html`、9個逐API技術文件`.html`、
`manual_candidate.pdf`）皆存放於Claude session scratchpad暫存目錄，
**未**存入repo runtime source tree、`data/rules/`或任何runtime路徑。

**Phase API-2.2R2至此停止（AUDIT ONLY）。不自行開始Implementation。**

---

## S14. Phase API-2.3 — Official Parcel Coordinate Provider（Auth-Gated Implementation）

與S1-S13（三輪AUDIT ONLY）不同，本輪（Phase API-2.3）**首次獲授權
實際實作**，前提為維持Core Freeze與已凍結之Cadastral Pipeline不變、
新增功能一律為additive、且絕不允許本輪開頭明確指出之反樣式：
「沒有NLSC key → 偷偷Nominatim → 假裝官方」。

### S14.1 實作摘要

新增檔案（皆為additive，未修改任何既有frozen模組）：

| 檔案 | 用途 |
|---|---|
| `domain/models.py`（僅新增class，既有class逐字未動） | `NlscSectionCodeEvidence`／`SectionCodeMatchStatus`／`OfficialParcelCoordinateEvidence`／`ParcelCoordinateStatus`／`ParcelCoordinateSemantics` |
| `providers/nlsc_code_cache.py` | 獨立SQLite store（Atomic Staging Contract：STAGING→驗證→原子promote→CURRENT），儲存ListCounty/ListTown/ListLandSection同步結果，與已凍結之`cadastral_dataset_cache.py`完全分離 |
| `providers/nlsc_land_number_encoder.py` | NLSC地號8碼編碼（4碼主+4碼副zero-padded），刻意獨立於`cadastral_identifier.py`之`LandNumberNormalizer`（兩套不同政府系統之contract，僅巧合演算法相同） |
| `providers/nlsc_cadastral_code_resolver.py` | 縣市/行政區/地段名稱 → NLSC官方代碼，僅使用三個已確認開放之服務，精確比對，0筆→NOT_FOUND，>1筆→AMBIGUOUS並保留全部原始候選 |
| `providers/official_parcel_coordinate_provider.py` | CAD_001（CadasMapPosition）Auth-Gated Provider本體：`build_cadas_map_position_request()`／`parse_cadas_map_position_response()`（純函式，無需憑證即可完整單元測試）、`MockOfficialParcelCoordinateProvider`（永遠離線、誠實UNKNOWN）、`RealOfficialParcelCoordinateProvider`（Auth Gate見S14.3）、`to_target_coordinate_evidence()`（Facility Integration glue） |
| `scripts/sync_nlsc_codes.py` | 手動觸發之ETL job，同步ListCounty（全部）／ListTown（全部縣市）／ListLandSection（僅新北市29區，與本專案既有範圍一致） |
| `tests/fixtures/nlsc/*.xml` | 取自官方技術手冊CAD_001已公開回應範例之最小fixture（非手冊全文複製） |
| `tests/test_nlsc_land_number_encoder.py`／`test_nlsc_code_cache.py`／`test_nlsc_cadastral_code_resolver.py`／`test_official_parcel_coordinate_provider.py` | 共62項測試，覆蓋本輪要求之30項情境（見S14.5） |

`backend/handlers/collect_data.py`新增`_resolve_nlsc_official_coordinate_
evidence()`，於`_resolve_center_coordinate_evidence()`既有的
SUBMITTED_BY_CALLER分支之後、Nominatim geocode分支之前插入NLSC
official座標嘗試——僅NLSC回傳SUCCESS時才採用（OFFICIAL、PARCEL層級精
細度），任何其他狀態（含AUTH_REQUIRED）皆回傳`None`並無縫falls through
至既有Nominatim/未變更邏輯。`OfficialFacilityProvider`本身**未修改**。

### S14.2 真實同步結果（ListCounty/ListTown/ListLandSection，無需憑證）

`py scripts/sync_nlsc_codes.py`已對NLSC三個確認開放、無需認證之服務
執行真實線上同步（約51次HTTP請求），結果：

```
counties: 22筆（全國）
towns: 365筆（全國22縣市）
sections（僅新北市29區）: 1350筆
```

全程**零筆**「同步失敗」。Golden Case端到端驗證（`NlscCadastralCodeResolver`
讀取此真實同步後之本地snapshot，非即時呼叫網路）：

```
新北市 → county_code = F
金山區 → town_code = F25
金美段 → section_code = 1027, office = FD
NlscLandNumberEncoder.encode(489, 0) = 04890000
```

與Phase API-2.2R2先前之即時查證結果完全一致。

### S14.3 Auth Gate（本輪核心不變式）

`RealOfficialParcelCoordinateProvider.query_parcel_coordinate()`流程：

```
district/section/land_no
  → NlscCadastralCodeResolver（本地snapshot，無需憑證）
  → NlscLandNumberEncoder（純函式）
  → [Auth Gate] NLSC_CAD_API_ENABLED（預設false）且
                NLSC_CAD_API_USERNAME/NLSC_CAD_API_TOKEN皆存在？
      → 否：回傳 AUTH_REQUIRED（section_code／nlsc_land_no等識別
             解析結果**照樣保留**，證明失敗發生於「授權層」而非
             「識別解析層」）
      → 是：實際呼叫CAD_001 → parse_cadas_map_position_response()
             → SUCCESS時才回傳OFFICIAL座標
```

`DATA_PROVIDER_MODE=real`本身**不會**觸發CAD_001呼叫——僅
feature flag與憑證同時具備才會。本輪環境未設定任何真實憑證（未申請、
未猜測、無硬編碼密鑰），故`RealOfficialParcelCoordinateProvider`之
「Real Auth Gate open」分支為**已測試但本輪從未實際觸發**之程式碼
（以mocked HTTP回應驗證，見`test_real_provider_success_path_with_
mocked_network`）。

CRS處理：`build_cadas_map_position_request()`預設請求`output_crs=
"4326"`（CAD_001官方確認直接支援WGS84輸出），故Lambda request path
本身**不需要**pyproj重新投影——與本專案既有慣例一致（pyproj僅存在於
`backend/requirements-sync.txt`，供sync-time ETL使用，未列入
`backend/requirements.txt`之Lambda runtime依賴）。若`output_crs=
"3826"`（僅供request builder契約完整性與測試使用，Real path實際上
從未如此呼叫），回應解析器誠實地保留`source_coordinate_x/y`與
`source_crs=EPSG:3826`，但刻意不將其誤標為WGS84經緯度（`latitude`/
`longitude`保持`None`），並於`notes`中說明原因。

### S14.4 Eligibility文件更新（§22 reconciliation）

依本輪使用者提供之現行115年度NLSC訂閱資訊，更新S12之
`PRIVATE_COMPANY_ELIGIBLE=CONDITIONAL`判定：

```
PRIVATE_COMPANY_ELIGIBLE = YES
PRIVATE_ORGANIZATION_ELIGIBLE = YES
INDIVIDUAL_ELIGIBLE = UNCONFIRMED
```

**Reconciliation說明**：S11/S12之`CONDITIONAL`判定基礎是兩份「間接
search引擎摘要」（非115年度訂閱申請書PDF原文），彼此說法衝突。本輪
依使用者提供之現行115年度資訊更新為`YES`（民營團體及公司皆可訂閱
地籍API等服務）。**惟此更新僅解決「何種身分類別可訂閱」之資格問題
本身**——實際申請人身分認定、URL/IP binding執行細節，仍需透過正式
向NLSC提出申請/訂閱程序才能一次性確認（`CAD001_BINDING_MODE =
UNCONFIRMED`維持不變，理由同S11第8項：binding屬帳號申請層級文件，
與本輪已取得之逐API技術contract分屬不同文件範疇）。本輪**未**提出
任何實際申請、**未**建立帳號、**未**猜測憑證。

### S14.5 測試覆蓋（62項，涵蓋本輪要求之30項情境）

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
**753 passed, 0 failed**（含本輪新增之62項NLSC相關測試）。涵蓋：
ListCounty/Town/LandSection精確比對之FOUND/NOT_FOUND/AMBIGUOUS、
Golden section=1027端到端解析、地號編碼（含官方4組範例交叉驗證）、
CAD_001 request builder（4326/3826）、response parser（成功／缺欄位／
malformed XML／空body／401/403/400/404/5xx／網路錯誤／未預期狀態碼）、
feature flag關閉／憑證缺失皆回傳AUTH_REQUIRED且保留已解析之識別碼、
Mock Provider離線不觸網、Real Provider絕不因授權失敗而silent fallback
至Nominatim或宣稱OFFICIAL、`to_target_coordinate_evidence()`對
非SUCCESS狀態一律回傳`None`、Golden Case最終auth-gated結果。

### S14.6 Freeze複查

Core Freeze（`engine/`＋`data/rules/`＋`schemas/`＋
`providers/document_extraction_provider.py`，共36檔）：
`COMBINED_MANIFEST_HASH`本輪後 = `0ddef3718492cca7efce20f3b070ccd7d5d
36dabaf979f1f96d2df13959d1a51`，與Phase API-1.9凍結時之基準值**完全
相符**，逐位元組零差異。

已凍結之Cadastral Pipeline（6檔：`cadastral_identifier.py`／
`cadastral_dataset_cache.py`／`expropriation_case_provider.py`／
`land_price_provider.py`／`sync_expropriation_dataset.py`／
`sync_land_price_dataset.py`）：逐檔SHA-256本輪後與Phase API-2.2R2
基準值**逐位元組完全相符**（`CADASTRAL_PIPELINE_FREEZE_VIOLATION =
NO`）。

`providers/official_facility_provider.py`／`providers/facility_dataset_
cache.py`本輪**未修改**（Facility Integration僅透過既有
`ProviderContext.center_coordinate`／`center_coordinate_evidence`
掛鉤，不觸及該Provider本身程式碼）。

### S14.7 Golden Case最終結果（本輪環境無真實NLSC憑證下之正確PASS狀態）

```
SECTION_CODE = 1027
NLSC_LAND_NO = 04890000
IDENTIFIER_RESOLUTION = PASS
CAD001_REQUEST_BUILD = PASS
CAD001_RUNTIME = AUTH_REQUIRED
OFFICIAL_PARCEL_COORDINATE = NOT_AVAILABLE
OFFICIAL_DISTANCE_READY = NO
```

此為**本輪之正確PASS條件**，非失敗——識別碼解析／request builder／
response parser／Auth Gate四項判準皆通過，且全程無silent fallback。
未來若透過**正式管道**（非本輪範疇）取得核准之NLSC憑證，
`RealOfficialParcelCoordinateProvider`之Real Auth Gate open分支
（已測試、當前僅由mocked HTTP回應驗證）即可直接投入使用，無需重新
設計。

**Phase API-2.3至此停止。不自行開始憑證申請、AWS部署、TGOS實作、或
Phase API-2.4。**

---

## S15. Phase API-2.3H — Auth Semantics & Lambda Runtime Hardening

本輪（Phase API-2.3H）不新增API，目的是在正式取得Live CAD_001憑證與
AWS Lambda Runtime部署之前，修正Phase API-2.3遺留之數項correctness／
deployment risk。核心發現：**Phase API-2.3的`X-NLSC-Username`／
`X-NLSC-Token` HTTP headers是無官方文件依據的猜測**，已移除。

### S15.1 CAD_001認證傳輸重新稽核（§2-3）

本輪以`fitz`（PyMuPDF）對既有下載之官方文件進行**全文字擷取＋關鍵字
搜尋**（非僅重讀摘要）：

- **國土測繪圖資服務雲介接服務技術手冊**（`manual_candidate.pdf`，96頁，
  57,309字元全文）：搜尋`Username`/`Password`/`MD5`/`Authorization`/
  `Header`/`Token`/`帳號`/`密碼`/`授權`/`認證`/`API Key`等關鍵字，
  **零筆相符**（唯一"授權"相關字串為某圖層資料授權說明，與API認證
  無關）。
- **115年度訂閱申請書**（`nlsc_academic_apply.pdf`，13頁，一般性申請
  說明文件）：同樣搜尋，**零筆Username/Password/Token/MD5/
  Authorization/Header相符**。第十項(二)「綁定IP」段落：「由使用該
  IP的伺服器（或應用程式）送出呼叫API...該IP必需為該伺服器專用的對外
  IP，不得為學校共用的對外IP」——即NLSC伺服器端可依**來源IP**比對已
  登記之IP，並非client端於request中夾帶之憑證。**重要更正（Phase
  API-2.3F）**：此段落僅描述「若使用IP binding時之IP登記程序」，
  **不代表IP binding是CAD_001唯一支援之binding模式**——本文件本身
  第(三)項明白指出，逐API之實際綁定能力記載於**另一份附件「申請服務
  介接說明表」**（本輪之前的下載紀錄中未包含此附件本體），Phase
  API-2.3H誤將「一般申請說明僅講解了IP binding登記流程」推論為「IP
  binding是唯一機制」，這是對文件範疇的過度推論，已依本輪指示更正。
  依使用者本輪提供之現行「申請服務介接說明表」內容，CAD_001同時
  支援**URL binding**與**IP binding**兩種連線方式。
- 逐API技術文件頁（`CadasMapPosition.html`等）：CAD_001輸入參數欄位
  逐字為「縣市代碼/地段代碼/地號(8碼)[/坐標類別代碼]」，**無任何欄位
  提及認證資訊**。

**結論（Phase API-2.3F更正版）**：

```
CAD001_REQUEST_LEVEL_AUTH = NO_DOCUMENTED_CREDENTIAL_SCHEME / UNCONFIRMED
CAD001_BINDING_CAPABILITY = URL_OR_IP_SUPPORTED
CAD001_APPROVED_BINDING_FOR_THIS_PROJECT = UNCONFIRMED
AWS_FIXED_EGRESS_IP_REQUIRED = CONDITIONAL
CAD001_AUTH_CONTRACT_STATUS = UNCONFIRMED
```

NLSC官方文件對CAD_001之client端**request-level**認證傳輸機制（HTTP
header/query parameter/body欄位）**完全未證實不存在任何此類文件依據
（NO_DOCUMENTED_CREDENTIAL_SCHEME）**；已證實的是network層binding
**能力**同時涵蓋URL與IP兩種模式（`CAD001_BINDING_CAPABILITY =
URL_OR_IP_SUPPORTED`），但本專案**尚未**實際申請、**尚未**取得核准，
故本專案實際會被核准哪一種binding模式仍為`UNCONFIRMED`
（`CAD001_APPROVED_BINDING_FOR_THIS_PROJECT`）——這也是為何AWS是否
需要固定egress IP改回`CONDITIONAL`（若核准URL binding，則不一定需要
固定IP；若核准IP binding，才需要）。Phase API-2.3原有之
`X-NLSC-Username`／`X-NLSC-Token` HTTP headers**無任何官方文件依據**，
純屬本專案自行猜測，**維持移除**，不得因本輪之更正而重新加入任何
猜測性credentials。

### S15.2 Auth Gate強化（§3-4）

`providers/official_parcel_coordinate_provider.py`新增**雙重鎖**：

```
CAD001_AUTH_CONTRACT_STATUS = "UNCONFIRMED"   # 原始碼常數，非環境變數
NLSC_CAD_AUTH_CONTRACT_VERIFIED（環境變數，預設false）
```

`is_auth_contract_verified()`要求**兩者皆為真**才回傳True：即使未來
某次部署誤將環境變數設為true，只要原始碼常數仍是"UNCONFIRMED"（本輪
未變更且不應變更，除非有人類經審查之程式碼改動，基於**真實取得之
核准申請與實際觀察到之流量**才能改為"VERIFIED"），network call仍會
被阻擋。

新的Auth Gate判斷順序（三項條件皆須滿足）：

```
1. NLSC_CAD_API_ENABLED=true？否 → AUTH_REQUIRED
2. is_auth_contract_verified()？否 → AUTH_CONTRACT_UNVERIFIED
   （即使①已為true、憑證亦存在，仍在此擋下，符合本輪§3明確指示：
   "即使...credentials present也必須AUTH_CONTRACT_UNVERIFIED...
   NO NETWORK CALL"）
3. NLSC_CAD_API_USERNAME/TOKEN皆存在？否 → AUTH_REQUIRED
→ 三項皆通過才實際發出HTTP request（本輪環境下此分支恆不可達，
  因常數固定為UNCONFIRMED）
```

實際發出之HTTP request（僅存在於已測試但本輪不可達之程式碼路徑）
**不再夾帶任何認證相關header**，逐字對應官方文件之path-only request
contract（`/{City}/{Sec}/{No}/{CRS}`），並於程式碼註解中明確聲明：
待`CAD001_AUTH_CONTRACT_STATUS`真正確認為VERIFIED時，此函式**必須**
依當時真正確認之機制更新，不得預先猜測。

### S15.3 Negative Result Semantics修正（§5-6）

```
CAD001_DOCUMENTED_NOT_FOUND_RESPONSE = UNCONFIRMED
```

`parse_cadas_map_position_response()`不再將以下三種情境標記為
`NOT_FOUND`（NLSC官方文件僅公開SUCCESS範例，從未公開任何「查無地號」
回應範例）：

| 情境 | Phase API-2.3（修正前） | Phase API-2.3H（修正後） |
|---|---|---|
| HTTP 404 | NOT_FOUND | **UNVERIFIED_RESPONSE** |
| HTTP 200 + 空body | NOT_FOUND | **EMPTY_RESPONSE** |
| XML成功解析但缺repX/repY | NOT_FOUND | **UNVERIFIED_RESPONSE** |
| XML malformed（ParseError） | SERVICE_UNAVAILABLE | **PARSE_FAILED**（獨立狀態，不再與伺服器異常混同） |

`ParcelCoordinateStatus`列舉新增`AUTH_CONTRACT_UNVERIFIED`／
`UNVERIFIED_RESPONSE`／`EMPTY_RESPONSE`／`PARSE_FAILED`／
`OUT_OF_COVERAGE`；`NOT_FOUND`保留於列舉中（供未來NLSC真正公開查無
地號回應範例時使用），但本輪parser**不再產生**此狀態值。核心原則：
**API回應異常 ≠ 官方查無宗地**。

### S15.4 NLSC Section Snapshot Coverage Contract（§7）

```
SECTION_SNAPSHOT_COVERAGE = NEW_TAIPEI_CITY
```

`NlscCodeCache`新增`is_county_in_section_coverage(county_code)`（純
membership檢查，對照`SECTION_SNAPSHOT_COVERAGE_COUNTY_CODES =
frozenset({"F"})`）。`NlscCadastralCodeResolver.resolve()`於county/
town解析成功後、section查詢**之前**檢查此membership：非新北市之
county一律回傳`SectionCodeMatchStatus.OUT_OF_COVERAGE`（對應
`ParcelCoordinateStatus.OUT_OF_COVERAGE`），**絕不**與「已同步但查無
資料」之`NOT_FOUND`混同。新增測試驗證：新北市內之genuine NOT_FOUND
仍正確運作，未被coverage檢查誤蓋。

### S15.5 Snapshot Metadata擴充（§8）

`snapshot_meta`資料表新增`coverage`／`checksum_algorithm`兩欄位，
透過`PRAGMA table_info`偵測後以`ALTER TABLE ADD COLUMN`**非破壞性
遷移**（保留本輪已同步之真實資料，不重建資料表）。`_promote()`寫入
`coverage`（counties/towns="ALL_TAIWAN"，sections="NEW_TAIPEI_CITY"）
與`checksum_algorithm`（"CANONICAL_V1"）。已對`data/nlsc_code_cache.
sqlite3`重新執行`scripts/sync_nlsc_codes.py`，確認遷移成功且record
count（22/365/1350）與sections checksum（`912e7af3...`）與Phase
API-2.3時完全一致——證明本輪修改未影響已同步資料之完整性。

### S15.6 AWS Lambda Read-Only Audit（§9）

`NlscCodeCache`新增`read_only`建構參數：

```
SYNC_READ_WRITE（read_only=False，預設，供scripts/sync_nlsc_codes.py
與本模組測試使用）：維持Phase API-2.3行為，可mkdir/CREATE TABLE/寫入。

RUNTIME_READ_ONLY（read_only=True，NlscCadastralCodeResolver／
RealOfficialParcelCoordinateProvider於未注入cache時之預設）：
絕不mkdir、絕不CREATE TABLE、絕不staging，_connect()一律以
`file:...?mode=ro` URI開啟——由SQLite本身在OS層級拒絕任何寫入嘗試
（含隱含之journal檔案），而非僅依賴本類別自身之慣例。缺檔案時拋出
清楚之NlscCodeCacheError，而非造成Lambda冷啟動crash。
```

已直接測試驗證：唯讀模式下呼叫`begin_staging_run()`正確拋出拒絕寫入
之錯誤；唯讀模式下對不存在之db_path查詢正確拋出清楚錯誤而非崩潰。

### S15.7 Packaging稽核（§10-11）

**實際檢查**（非假設）`infra/layers/engine/Makefile`：本輪之前**僅**
複製`data/rules/`與`data/dependency_graph.json`進EngineLayer，**從未
複製**`data/nlsc_code_cache.sqlite3`——這是一個真實存在之部署缺口：
若曾經部署至AWS Lambda並啟用feature flag，`NlscCadastralCodeResolver`
會在`/opt/python/data/`下找不到任何NLSC snapshot檔案。

同時確認`providers/nlsc_code_cache.py`之`DEFAULT_DB_PATH`路徑運算
（`os.path.dirname(os.path.dirname(__file__))/data/nlsc_code_cache.
sqlite3`）在Lambda環境下**恰好**已正確解析至`/opt/python/data/`（與
`data/rules/`同一層），故不需修改任何程式碼路徑邏輯。

**部署策略比較**（本輪僅設計，不部署AWS）：

| 方案 | 說明 | 評估 |
|---|---|---|
| A. Build-time SQLite → EngineLayer（唯讀） | 與`data/rules/`相同模式，`sam build`時打包進Layer | **選用**：改動最小（Makefile一行`cp`），資料量小（22縣市+365鄉鎮+1350地段，數百KB），無額外執行期I/O或AWS資源 |
| B. S3 → Lambda `/tmp`（冷啟動下載） | 需新增S3 bucket、IAM權限、冷啟動下載邏輯 | 改動大、增加冷啟動延遲、需額外AWS資源，資料量太小不值得 |

已於`infra/layers/engine/Makefile`加入方案A（`if [ -f data/nlsc_code_
cache.sqlite3 ]; then cp ...; fi`，guard確保尚未執行過sync的全新
checkout不會導致packaging失敗）。**本輪未執行`sam build`／`sam
deploy`**，僅完成本地Makefile設計變更。

```
NLSC_CACHE_PACKAGED = DESIGN_ONLY
```

### S15.8 pyproj Runtime Audit（§12）

`grep`確認`providers/official_parcel_coordinate_provider.py`**不
import pyproj**。實際呼叫路徑（`query_parcel_coordinate()`）之
`build_cadas_map_position_request()`恆以`output_crs="4326"`呼叫（CAD_001
官方確認直接支援WGS84輸出），故Lambda request path**從不需要**pyproj
重新投影。`output_crs="3826"`選項僅為request builder contract完整性
與測試存在；`_to_evidence()`對3826回應**刻意不做**pyproj轉換，誠實
保留`source_coordinate_x/y`原始值並將`latitude`/`longitude`留空，
註解中說明原因（重新投影屬sync-time ETL職責，非Lambda runtime職責，
與`backend/requirements-sync.txt`/`backend/requirements.txt`之既有
分工一致）。

```
PYPROJ_RUNTIME_DEPENDENCY = NO
```

### S15.9 SQLite `:memory:` Audit（§13）

發現：`NlscCodeCache`每次`_connect()`皆開啟全新連線，若`db_path=
":memory:"`，每個連線將是**彼此獨立、互不可見**的資料庫——staging
與promote將無法看見彼此寫入之資料，靜默破壞Atomic Staging Contract。
本輪**明確拒絕**此用法（`__init__`偵測`db_path==":memory:"`時直接
拋出`NlscCodeCacheError`，說明原因），而非留下一個「恰好沒被測試
踩到」的陷阱。已於程式碼與測試中記錄，`tests/test_nlsc_code_cache.py`
確認拒絕行為正確觸發。

```
SQLITE_MEMORY_BEHAVIOR = PASS（明確拒絕，非隱藏陷阱）
```

### S15.10 collect_data Integration Tests（§14）

新增`tests/test_collect_data_nlsc_integration.py`（8項handler-level
測試＋1項Mock mode對照測試，共9項），直接呼叫`_resolve_center_
coordinate_evidence()`/`_resolve_nlsc_official_coordinate_evidence()`：
提交座標優先於NLSC與Nominatim（且NLSC/Nominatim完全不被呼叫）、NLSC
SUCCESS產生OFFICIAL TargetCoordinateEvidence、NLSC AUTH_REQUIRED正確
falls through至Nominatim且標記為NOMINATIM_EXTERNAL/EXTERNAL_UNVERIFIED
（絕非OFFICIAL）、auth contract未確認時CAD_001網路呼叫確認**從未
發生**（以monkeypatch urlopen若被呼叫則fail測試）、NLSC cache完全
不存在時handler不崩潰並正確fallback、非新北市案件正確回報
OUT_OF_COVERAGE並falls through、feature flag關閉（本輪實際預設值）
時行為與Phase API-2.3前完全一致、Mock mode下NLSC路徑完全不被觸碰。

### S15.11 Golden Case（§15）

```
新北市 → county_code = F
金山區 → town_code = F25
金美段 → section_code = 1027
489    → NlscLandNumberEncoder → 04890000
CAD001_RUNTIME = AUTH_REQUIRED（feature flag預設關閉時之實際結果，
                 若flag開啟則為AUTH_CONTRACT_UNVERIFIED，兩者皆為
                 本輪之正確、非fabricated結果）
```

未fabricate任何座標。

### S15.12 Freeze複查

Core Freeze（36檔）：`CORE_FREEZE_SHA256_BEFORE` = `CORE_FREEZE_SHA256_
AFTER` = `0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d
1a51`，逐位元組零差異。

Cadastral Pipeline（6檔）：本輪前後逐檔SHA-256完全相符（
`bff8acfb...`／`21e83193...`／`9d5d17be...`／`c4690f22...`／
`15172c35...`／`c9425b4d...`），`CADASTRAL_PIPELINE_FREEZE_VIOLATION
= NO`。

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
**767 passed, 0 failed**。

### S15.13 Release Gate 最終報告

```
CAD001_AUTH_CONTRACT_STATUS = UNCONFIRMED
GUESSED_AUTH_HEADERS_PRESENT = NO
UNVERIFIED_ERROR_CAN_PRODUCE_NOT_FOUND = NO
SECTION_COVERAGE_CONTRACT = PASS
NON_NTPC_FALSE_NOT_FOUND_RISK = NO
NLSC_CACHE_RUNTIME_READ_ONLY = PASS
NLSC_CACHE_PACKAGED = DESIGN_ONLY
PYPROJ_RUNTIME_DEPENDENCY = NO
SQLITE_MEMORY_BEHAVIOR = PASS
COLLECT_DATA_PRIORITY_TESTS = PASS
REAL_NO_SILENT_FALLBACK = PASS
GOLDEN_IDENTIFIER_RESOLUTION = PASS
LIVE_CAD001_RUNTIME_READY = NO
AWS_RUNTIME_READY = NO（設計完成，未部署）
CORE_FREEZE_VIOLATION = NO
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 767 passed, 0 failed
PHASE_API_2_3H_RELEASE_READY = YES
```

**Phase API-2.3H至此停止。不自行開始Phase API-2.4、憑證申請、AWS正式
部署、或TGOS實作。**

---

## S16. Phase API-2.3F — Final Freeze & Packaging Acceptance

NLSC Official Parcel Coordinate Pipeline最後一輪，目的是在正式凍結
前完成剩餘之文件語意修正、Coordinate Evidence架構修正、與Packaging
實際驗收（非僅檢查Makefile原始碼）。

### S16.1 CAD_001 Binding文件語意更正（§1）

Phase API-2.3H之S15.1有一處過度推論：將「115年度訂閱申請書（一般性
申請說明文件）僅描述IP binding之IP登記程序」誤推論為「IP binding是
CAD_001唯一支援之binding模式」。該文件本身第(三)項已明白指出，逐API
實際binding能力記載於**另一份附件「申請服務介接說明表」**（前幾輪
之下載紀錄中未包含此附件本體）。依本輪使用者提供之現行「申請服務
介接說明表」內容，CAD_001**同時支援URL binding與IP binding**。已更正
S15.1與`providers/official_parcel_coordinate_provider.py`模組docstring：

```
CAD001_REQUEST_LEVEL_AUTH = NO_DOCUMENTED_CREDENTIAL_SCHEME / UNCONFIRMED
CAD001_BINDING_CAPABILITY = URL_OR_IP_SUPPORTED
CAD001_APPROVED_BINDING_FOR_THIS_PROJECT = UNCONFIRMED
AWS_FIXED_EGRESS_IP_REQUIRED = CONDITIONAL
```

`X-NLSC-Username`／`X-NLSC-Token`猜測性HTTP headers**維持移除**，
不因本輪之更正而重新加入——這四個更正的常數描述的仍是network層
binding**能力**，不是任何client端可組裝之request-level憑證。

### S16.2 Live Gate Fail-Closed確認（§2）

`is_auth_contract_verified()`維持雙鎖設計（`CAD001_AUTH_CONTRACT_
STATUS`原始碼常數 AND `NLSC_CAD_AUTH_CONTRACT_VERIFIED`環境變數），
`CAD001_AUTH_CONTRACT_STATUS`本輪維持"UNCONFIRMED"，故無論production
environment如何設定`NLSC_CAD_AUTH_CONTRACT_VERIFIED`，皆無法繞過此
gate——這是本輪重新確認、未變更之既有設計（Phase API-2.3H已完成）。
測試中`_force_auth_contract_verified()`僅monkeypatch模組常數本身
（非production路徑可達之操作），用於單元測試parser/request builder，
不構成對production gate之繞過範例。

### S16.3 Coordinate Evidence Priority重新設計（§3-6）

**核心修正**：Phase API-2.3H「submitted coordinate存在→完全不查NLSC」
之設計對審查系統並不合適——會讓伺服器在有官方資料可查證時，選擇跳過
查證。本輪重新設計為三份獨立evidence，永不互相覆蓋：

```
SUBMITTED_COORDINATE_EVIDENCE       （body["center_coordinate"]，若存在）
OFFICIAL_PARCEL_COORDINATE_EVIDENCE （NLSC CAD_001，永遠嘗試，real mode）
NOMINATIM_REFERENCE_EVIDENCE        （REFERENCE_ONLY，僅當前兩者皆無時才查詢）
```

`backend/handlers/collect_data.py`新增`_resolve_coordinate_evidence_
bundle()`為唯一協調點，取代原本的單一`_resolve_center_coordinate_
evidence()`分支邏輯（該函式仍保留，改為薄wrapper回傳`analysis_
coordinate`，維持向後相容）。

**Analysis Coordinate Selection**（`_select_analysis_coordinate()`，
餵入`ProviderContext.center_coordinate`供距離計算使用）：

```
1. Official NLSC SUCCESS → OFFICIAL_PARCEL_REPRESENTATIVE_POINT（最高優先）
2. Official不可用 且 submitted存在 → SUBMITTED_COORDINATE
3. 兩者皆無 → Nominatim（REFERENCE_ONLY／EXTERNAL_UNVERIFIED）或None
   （絕不：Nominatim → OFFICIAL）
```

**Facility Integration**（§6）：`providers/official_facility_provider.py`
**本輪未修改**——其既有邏輯（Phase API-2.1）已正確依`target_coordinate_
evidence.authoritative_status == OFFICIAL`才將`official_distance_ready`
設為True，否則標記`MIXED_SOURCE`。本輪的修正發生在**上游**（餵入正確
provenance之evidence），非該Provider本身。

### S16.4 Submitted vs Official Comparison（§5）

新增`domain.models.CoordinateComparisonEvidence`／
`CoordinateComparisonStatus`（`MATCH`/`DIFFERENT`/`MANUAL_REVIEW_
REQUIRED`）。`_compute_coordinate_comparison()`僅在submitted與official
**同時存在**時產生，`coordinate_delta_m`透過**凍結**之`engine/geo_
distance_engine.py`的`GeoDistanceEngine.straight_line_distance()`
（Haversine）計算，未新增任何自訂距離公式。

**不建立法律threshold**：`COORDINATE_IDENTITY_EPSILON_M = 1.0`（公尺）
僅為GPS/CRS數值誤差之**數值相同性**容許範圍，非估價重大性門檻——
delta在此範圍內視為`MATCH`（純數值事實），超出則為`DIFFERENT`（純
幾何事實，非何者正確之判斷）；任一座標缺緯度/經度則為`MANUAL_REVIEW_
REQUIRED`。此Evidence model**沒有**任何"correct_coordinate"或
"winner"欄位——結構上即不可能讓下游（含LLM）從此evidence推論何者
正確。

### S16.5 Packaging Acceptance（§7-10，實際驗證，非僅檢查原始碼）

由於本環境未安裝`make`/`sam`，以Python精確複製`infra/layers/engine/
Makefile`之`build-EngineLayer`目標的複製步驟（相同來源路徑、相同
sqlite條件式guard），實際產出一份build artifact，並對**該artifact**
（非repo原始data/路徑）執行下列驗證：

```
PACKAGED_NLSC_CACHE_PRESENT = YES
（python/data/nlsc_code_cache.sqlite3 實際存在於artifact中）
```

對artifact執行read-only smoke test（`NlscCodeCache.DEFAULT_DB_PATH`
之路徑運算本身即解析至artifact樹狀結構內，非測試手動指定路徑）：

```
新北市/金山區/金美段 → county_code=F, town_code=F25, section_code=1027, office=FD
489 → NlscLandNumberEncoder → 04890000
PACKAGED_GOLDEN_RESOLUTION = PASS
```

Read-only enforcement雙層驗證：

```
NlscCodeCache(read_only=True).begin_staging_run() → 正確拋出NlscCodeCacheError
原生sqlite3以file:...?mode=ro開啟後執行INSERT → sqlite3.OperationalError
（SQLite本身在OS層級拒絕，非僅本專案自訂慣例）
PACKAGED_READ_ONLY_LOOKUP = PASS
```

已於`tests/test_engine_layer_packaging.py`（3項測試）將此驗收流程
固化為可重複執行之pytest測試，取代一次性手動驗證。

**測試隔離注意事項（自行發現並修正之錯誤）**：初版測試以
`importlib.reload()`搭配`sys.path`操作嘗試從packaged artifact重新
載入`nlsc_code_cache`模組，但`importlib.reload()`實際上是依模組**既有**
`__spec__.origin`重新執行，並不會依當下sys.path重新搜尋檔案位置——
此手法不僅未達成「從artifact載入」之目的，還會**污染**
`sys.modules["nlsc_code_cache"]`，導致同一pytest session中**其他**
測試檔案之後續import誤用此殘留、已失效之模組物件，造成間歇性、
與測試執行順序相關的失敗（於全套件回歸時發現：4項既有NLSC測試失敗，
單獨執行該檔案卻正常）。修正方式：改用`monkeypatch.delitem(sys.
modules, name)`（測試結束後自動還原至pre-test狀態）搭配全新
`importlib.import_module()`，確保每次測試皆真正從packaged路徑重新
匯入，且**不留下**任何跨測試污染。修正後已確認全套件785項測試
（含以不同檔案執行順序重跑驗證）皆穩定通過。

### S16.6 Release Build缺Snapshot行為（§8）

新增`scripts/preflight_release_capabilities.py`：檢查build artifact（或
repo）是否實際含有`nlsc_code_cache.sqlite3`，輸出明確之
`NLSC_CODE_RESOLVER_AVAILABLE = YES/NO`並寫入`release_capability_
manifest.json`（**絕不**silent omission——缺檔案時明確輸出`NO`，而非
略過該欄位）。支援`--require KEY=VALUE`強制模式：若聲稱`YES`但實際
`NO`，回傳exit code 1（`PREFLIGHT_FAIL`）；若明確聲明`--require ...=NO`
（刻意允許optional build），則正常通過。已以6項pytest測試
（`tests/test_preflight_release_capabilities.py`）鎖定此行為，含
「聲稱YES但檔案缺失→PREFLIGHT_FAIL」與「明確聲明NO→PASS」兩種情境。

### S16.7 Golden Case（§12，本輪環境無真實NLSC憑證下之正確結果）

透過`_resolve_coordinate_evidence_bundle()`端到端驗證（無body提交
座標，DATA_PROVIDER_MODE=real）：

```
GOLDEN_IDENTIFIER_RESOLUTION = PASS
county_code=F, town_code=F25, section_code=1027, nlsc_land_no=04890000
CAD001_LIVE = AUTH_REQUIRED（等同NOT_ENABLED，feature flag預設關閉）
OFFICIAL_COORDINATE = NOT_AVAILABLE（official_parcel為None）
analysis_coordinate → 正確fall through至Nominatim（REFERENCE_ONLY/
EXTERNAL_UNVERIFIED，絕非OFFICIAL）
OFFICIAL_DISTANCE_READY = NO
```

此為本輪之正確結果，非失敗。

### S16.8 Freeze複查

Core Freeze（36檔）：`CORE_FREEZE_SHA256_BEFORE` = `CORE_FREEZE_SHA256_
AFTER` = `0ddef3718492cca7efce20f3b070ccd7d5d36dabaf979f1f96d2df13959d
1a51`，逐位元組零差異。

Cadastral Pipeline（6檔）：本輪前後逐檔SHA-256完全相符，
`CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO`。

`py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py`：
**785 passed, 0 failed**（含本輪新增之3個測試檔：
`test_collect_data_nlsc_integration.py`重寫、`test_preflight_release_
capabilities.py`、`test_engine_layer_packaging.py`）。

### S16.9 Release Gate 最終報告

```
CAD001_BINDING_CAPABILITY = URL_OR_IP_SUPPORTED
CAD001_APPROVED_BINDING = UNCONFIRMED
GUESSED_AUTH_HEADERS_PRESENT = NO
SUBMITTED_AND_OFFICIAL_EVIDENCE_SEPARATED = PASS
SUBMITTED_BLOCKS_OFFICIAL_LOOKUP = NO
OFFICIAL_COORDINATE_SELECTION = PASS
NOMINATIM_REFERENCE_ONLY = PASS
PACKAGED_NLSC_CACHE_PRESENT = YES
PACKAGED_READ_ONLY_LOOKUP = PASS
PACKAGED_GOLDEN_RESOLUTION = PASS
MISSING_RELEASE_CACHE_FAILS_PREFLIGHT = PASS
LIVE_CAD001_RUNTIME_READY = NO
AWS_RUNTIME_CODE_READY = YES
CORE_FREEZE_VIOLATION = NO
CADASTRAL_PIPELINE_FREEZE_VIOLATION = NO
AVAILABLE_ENVIRONMENT_REGRESSION = 785 passed, 0 failed
PHASE_API_2_3F_RELEASE_READY = YES
```

### S16.10 Final Freeze

```
OFFICIAL_PARCEL_COORDINATE_PIPELINE = CODE_FROZEN
```

**Phase API-2.3F至此停止。NLSC Official Parcel Coordinate Pipeline
（Phase API-2.2/2.2R/2.2R2 audit → Phase API-2.3 implementation →
Phase API-2.3H hardening → Phase API-2.3F final freeze）於此正式凍結。
不自行開始Phase API-2.4、TGOS實作、Rule/Grade Engine修改、真實NLSC
申請、或AWS正式部署。**
