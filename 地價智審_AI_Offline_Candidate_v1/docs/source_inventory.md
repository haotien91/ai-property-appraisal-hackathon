# Source Inventory — 官方資料來源盤點

> 本文件由 `data/sources/source_manifest.json` 之內容整理而成（人工可讀版本），為2026-09-02 Source Audit之產出。機器可讀版本以`source_manifest.json`為準；本文件若與該JSON有出入，以JSON為準。

**盤點時間**：2026-09-02T18:10:00+08:00　**來源總數**：22


## AUTHORITATIVE_USED（實際形成程式規則/被Provider使用之官方依據）（12筆）

### 【命題文件】地政局-新北市政府AI黑客松競賽

- **source_id**：`competition_proposal_document`
- **issuing_agency**：新北市政府地政局
- **source_type**：COMPETITION_PROVIDED_PDF
- **local_snapshot_path**：`data/sources/competition/【命題文件】地政局-新北市政府AI黑客松競賽.pdf`
- **original_filename**：【命題文件】地政局-新北市政府AI黑客松競賽.pdf
- **retrieved_at**：2026-08-31T13:55:19+08:00 —— 使用者提供檔案之本機檔案系統時間戳記（非本系統網路擷取），代表使用者取得/放置此檔案的時間，非官方發布時間
- **sha256**：`cef00fae7f311153e63c975f4ba2368975d1bd0809de8f3badcf0da7d4fd1e65`
- **byte_size**：159,059
- **legal_status**：COMPETITION_OFFICIAL_MATERIAL
- **used_by**：docs/phase1/evidence_matrix.md; docs/phase1/official_requirements.md; docs/phase1/competition_io_spec.md
- **notes**：比賽官方命題文件，Phase 1需求盤點（evidence_matrix.md）之主要依據之一

### 土地徵收補償市價查估作業手冊

- **source_id**：`competition_valuation_manual`
- **issuing_agency**：內政部（比賽單位提供版本）
- **source_type**：COMPETITION_PROVIDED_PDF
- **local_snapshot_path**：`data/sources/competition/土地徵收補償市價查估作業手冊.pdf`
- **original_filename**：土地徵收補償市價查估作業手冊.pdf
- **retrieved_at**：2026-08-31T13:55:18+08:00 —— 使用者提供檔案之本機檔案系統時間戳記
- **sha256**：`97cc40697cad3647eb9a121b258791c426c702303c8b66456d2784546a13b342`
- **byte_size**：3,055,284
- **legal_status**：COMPETITION_OFFICIAL_MATERIAL
- **articles_or_sections_used**：p.10-12（審查重點）, p.48-49（評價基準明細表）, p.52-53（比較法調查估價表/§27權重舉例級距）
- **used_by**：engine/calculation_engine.py; engine/comparable_selection_engine.py; docs/phase2/calculation_dependency.md; docs/phase2/business_process.md; docs/phase1/evidence_matrix.md（REQ-020/021/022/025/026等多項）
- **notes**：REQ-021/022進位規則、REQ-025/026彈性條款、CalculationEngine整條計算鏈之主要來源

### 查估書表範本

- **source_id**：`competition_form_template`
- **issuing_agency**：新北市政府地政局（比賽單位提供版本）
- **source_type**：COMPETITION_PROVIDED_PDF
- **local_snapshot_path**：`data/sources/competition/查估書表範本.pdf`
- **original_filename**：查估書表範本.pdf
- **retrieved_at**：2026-08-31T13:55:28+08:00 —— 使用者提供檔案之本機檔案系統時間戳記
- **sha256**：`2e6c16ebac75dce1a12515454237c825f0b688c1788b816ae48a50c637f67bb5`
- **byte_size**：2,677,025
- **legal_status**：COMPETITION_OFFICIAL_MATERIAL
- **articles_or_sections_used**：表1, 表4, 表5-2
- **used_by**：data/golden/golden_case_input.py（Golden Case案號1140901-99-001全部數值）; providers/land_use_provider.py（MockLandUseProvider._MOCK_VALUES）; 各Mock Provider之_MOCK_VALUES
- **notes**：本專案Golden Case（案號1140901-99-001，金山區P002-00）之逐字數值來源，貫穿整個系統的核心比對基準

### 評價基準明細表範例

- **source_id**：`competition_grading_example`
- **issuing_agency**：新北市政府地政局（比賽單位提供版本）
- **source_type**：COMPETITION_PROVIDED_PDF
- **local_snapshot_path**：`data/sources/competition/評價基準明細表範例.pdf`
- **original_filename**：評價基準明細表範例.pdf
- **retrieved_at**：2026-08-31T13:55:31+08:00 —— 使用者提供檔案之本機檔案系統時間戳記
- **sha256**：`cb8d37247b01f37b050ffa9e58cb301a3ad068ec1a2d280d2e049c718a6465dc`
- **byte_size**：802,827
- **legal_status**：COMPETITION_OFFICIAL_MATERIAL
- **used_by**：data/rules/regional_rules.json; data/rules/individual_rules.json（131+84項評價基準規則之來源範例）
- **notes**：Rule Engine評價基準規則（優劣等級/調整百分比）之範例格式與數值來源

### 不動產估價技術規則

- **source_id**：`law_estate_valuation_technical_rules`
- **issuing_agency**：內政部
- **source_type**：HTML_SNAPSHOT
- **source_url**：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=D0060077
- **local_snapshot_path**：`data/sources/laws/不動產估價技術規則_law.moj.gov.tw_snapshot.html`
- **retrieved_at**：2026-09-01T16:23:43+08:00 —— 本系統直接fetch該URL之時間（本機檔案系統時間戳記，同session內完成）
- **sha256**：`4555f443a3fbdb417326ed1594e71d8153fc582b94ef7f86861312c78aa8ce26`
- **byte_size**：134,580
- **legal_status**：OFFICIAL_LAW_FULL_TEXT
- **dataset_id**：D0060077
- **dataset_version**：民國102年12月20日修正（本系統fetch時之現行版本標示）
- **articles_or_sections_used**：第21條, 第23條, 第25條, 第26條, 第27條
- **used_by**：engine/comparable_selection_engine.py（ComparableSelectionEngine）; domain/models.py（ComparableExclusionCheck/TrialPriceGapCheck/SuggestedWeight）; docs/phase1/evidence_matrix.md REQ-029; docs/phase1/open_questions.md B-2/B-5
- **notes**：content_type=HTML_SNAPSHOT。§25之30%「總調整率」計算公式於本法全文中查無明文定義（詳見docs/phase1/open_questions.md B-5之二次查證記錄）；§27無權重計算公式，僅有「資料可信度」「相近程度」兩項質性考量。全國法規資料庫內容未來可能修正，本snapshot為2026-09-01查證當下之版本存證

### 行政院公報第021卷第020期內政篇（附「影響地價個別因素評價基準表」／「影響地價區域因素評價基準表」）

- **source_id**：`gazette_central_max_adjustment_range`
- **issuing_agency**：內政部
- **source_type**：OFFICIAL_PDF
- **source_url**：https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg021020/ch02/type2/gov10/num7/Eg.pdf
- **local_snapshot_path**：`data/sources/urban_planning/內政部104年公報_影響地價個別因素及區域因素評價基準表.pdf`
- **original_filename**：Eg.pdf
- **retrieved_at**：2026-09-01T17:27:22+08:00 —— 本系統直接下載該URL之時間；2026-09-02二次重新下載比對byte-for-byte一致（獨立驗證）
- **sha256**：`2fca02b093aeb8e2c0bb6533f333fd287666546ac23c272245e40db380618b75`
- **byte_size**：878,170
- **legal_status**：OFFICIAL_GAZETTE_ORDER
- **dataset_version**：20150130（公報發布日）／1040301（令文生效日）
- **articles_or_sections_used**：內政部104年1月30日台內地字第10413006723號令附表
- **used_by**：data/rules/central_max_adjustment_range.json; engine/central_max_range_validator.py
- **notes**：PDF內嵌字型無可用ToUnicode對照表，中文標籤文字層擷取為亂碼（pypdf/pymupdf皆同），改以PyMuPDF渲染高解析度圖像後人工逐格核對轉錄（見data/rules/central_max_adjustment_range.json之digitization_method）

### 都市計畫法新北市施行細則

- **source_id**：`ntpc_urban_planning_enforcement_rules`
- **issuing_agency**：新北市政府
- **source_type**：OFFICIAL_PDF
- **source_url**：https://www.planning.ntpc.gov.tw/uploaddowndoc?dis=planningcitylaw&file=planningcitylaw/202303271001520.pdf&filedisplay=1110316-%E9%83%BD%E5%B8%82%E8%A8%88%E7%95%AB%E6%B3%95%E6%96%B0%E5%8C%97%E5%B8%82%E6%96%BD%E8%A1%8C%E7%B4%B0%E5%89%87(%E5%85%A8%E6%A2%9D%E6%96%8758%E6%A2%9D)(111%E5%B9%B43%E6%9C%8818%E6%97%A5%E7%94%9F%E6%95%88).pdf&flag=doc
- **local_snapshot_path**：`data/sources/urban_planning/都市計畫法新北市施行細則.pdf`
- **original_filename**：1110316-都市計畫法新北市施行細則(全條文58條)(111年3月18日生效).pdf
- **retrieved_at**：2026-09-02T16:41:00+08:00 —— 本系統直接下載該URL之時間；同日獨立重新下載比對byte-for-byte一致
- **sha256**：`a541d6ae74657cc28b202c2c5c2ac177ed51f08008d40fc417e4806808063daa`
- **byte_size**：658,093
- **legal_status**：OFFICIAL_LOCAL_ORDINANCE_FULL_TEXT
- **dataset_version**：民國111年3月16日修正（111年3月18日生效）
- **articles_or_sections_used**：第三十六條, 附表一（土地使用分區建蔽率及容積率規定表）
- **used_by**：data/rules/ntpc_common_zone_ratios.json; engine/land_use_ratio_engine.py; engine/zone_name_normalizer.py
- **notes**：PDF文字層可直接乾淨擷取，非圖像/亂碼問題，已逐條與附表一原文人工比對確認

### 變更金山細部計畫（土地使用分區管制要點專案通盤檢討）書

- **source_id**：`jinshan_detailed_plan_review`
- **issuing_agency**：新北市政府
- **source_type**：OFFICIAL_PDF
- **source_url**：https://www.planning.ntpc.gov.tw/uploaddowndoc?file=anounce/202012081632471.pdf&filedisplay=1091023(%E7%94%A8%E5%8D%B0%E7%89%88)%E8%AE%8A%E6%9B%B4%E9%87%91%E5%B1%B1%E7%B4%B0%E9%83%A8%E8%A8%88%E7%95%AB%EF%BC%88%E5%9C%9F%E5%9C%B0%E4%BD%BF%E7%94%A8%E5%88%86%E5%8D%80%E7%AE%A1%E5%88%B6%E8%A6%81%E9%BB%9E%E5%B0%88%E6%A1%88%E9%80%9A%E7%9B%A4%E6%AA%A2%E8%A8%8E%EF%BC%89%E6%9B%B8(%E5%85%A8).pdf&flag=doc
- **local_snapshot_path**：`data/sources/urban_planning/變更金山細部計畫_土地使用分區管制要點專案通盤檢討_書.pdf`
- **original_filename**：1091023(用印版)變更金山細部計畫（土地使用分區管制要點專案通盤檢討）書(全).pdf
- **retrieved_at**：2026-09-02T16:43:00+08:00 —— 本系統直接下載該URL之時間；同日獨立重新下載比對byte-for-byte一致
- **sha256**：`83b2afb2be304783e32948880cb545b84a2b072fc74eff3e882693842536847e`
- **byte_size**：3,929,549
- **legal_status**：OFFICIAL_PLAN_REVIEW_DOCUMENT
- **dataset_version**：中華民國109年11月
- **articles_or_sections_used**：土地使用分區管制要點第五點（現行條文）
- **used_by**：data/rules/plan_zone_floor_area_ratios.json; engine/land_use_ratio_engine.py
- **notes**：本文件為都市計畫法第26條通盤檢討程序文件之現行條文對照表（非最終核定發布版本），第二種商業區「建蔽率不得大於70%，容積率不得大於240%」於文件中多處（如第2525-2527行）一致重複出現，與Golden Case完全吻合。該條文於本次通盤檢討中標註「已整併至第4點」，本系統未取得後續核定發布之最終版本，故data/rules/plan_zone_floor_area_ratios.json該筆記錄requires_manual_review=True

### 新北市使用分區（Shapefile）

- **source_id**：`ntpc_zoning_shapefile`
- **issuing_agency**：新北市政府城鄉發展局
- **source_type**：GIS_SHAPEFILE_ZIP
- **source_url**：https://urban.planning.ntpc.gov.tw/opendataDownload/新北市使用分區.zip
- **local_snapshot_path**：`data/sources/gis/新北市使用分區.zip`
- **original_filename**：新北市使用分區.zip
- **retrieved_at**：2026-09-02T09:41:42+08:00 —— 本系統直接下載該URL之時間
- **sha256**：`6a59efb20a75e18ba7e71cb7953b03454cab0717c28558841a506acf7113d215`
- **byte_size**：78,815,382
- **legal_status**：OFFICIAL_OPEN_DATA
- **dataset_id**：fe26e0a5-54c2-4876-bbc7-150243c048f5（item 1）
- **used_by**：providers/ntpc_zoning_provider.py（RealNtpcZoningProvider）; scripts/sync_ntpc_zoning_dataset.py
- **notes**：CRS=EPSG:3826（TWD97 TM Taiwan，經.prj WKT參數獨立驗證），編碼=BIG5（經.cpg確認），34,190筆分區圖徵，欄位僅ZONE（無plan_name欄位）。本次archive之raw ZIP即scripts/sync_ntpc_zoning_dataset.py實際處理之來源檔案；sync job產生之processed SQLite snapshot（供RealNtpcZoningProvider查詢用）為另一衍生產物，不在此manifest範圍內（屬DatasetRegistry管理之runtime資料，非官方來源存證）

### OpenStreetMap Overpass API（設施查詢）

- **source_id**：`osm_overpass_api`
- **issuing_agency**：OpenStreetMap Foundation（公開服務）
- **source_type**：LIVE_API_NO_SNAPSHOT
- **source_url**：https://overpass-api.de/api/interpreter
- **local_snapshot_path**：（無，見source_type/notes說明原因）
- **retrieved_at**：（不適用） —— 即時查詢API，每次case查詢時即時呼叫，本質上無固定版本快照可存證
- **legal_status**：PUBLIC_CROWDSOURCED_DATA
- **used_by**：providers/osm_facility_lookup.py; providers/real_facility_provider_base.py
- **notes**：DATA_PROVIDER_MODE=real時，5個Real*Provider（transportation/public_facility/special_facility/environmental/commercial_activity）之設施查詢來源，每次查詢即時呼叫，無版本化快照

### OpenStreetMap Nominatim API（地理編碼）

- **source_id**：`osm_nominatim_api`
- **issuing_agency**：OpenStreetMap Foundation（公開服務）
- **source_type**：LIVE_API_NO_SNAPSHOT
- **source_url**：https://nominatim.openstreetmap.org/search
- **local_snapshot_path**：（無，見source_type/notes說明原因）
- **retrieved_at**：（不適用） —— 即時查詢API
- **legal_status**：PUBLIC_CROWDSOURCED_DATA
- **used_by**：providers/osm_facility_lookup.py（geocode()）
- **notes**：DATA_PROVIDER_MODE=real且未提供center_coordinate時，由city+district+segment_scope地理編碼取得座標

### OSRM Project（步行路網距離）

- **source_id**：`osrm_routing_api`
- **issuing_agency**：Project OSRM（公開服務）
- **source_type**：LIVE_API_NO_SNAPSHOT
- **source_url**：https://router.project-osrm.org/route/v1/foot
- **local_snapshot_path**：（無，見source_type/notes說明原因）
- **retrieved_at**：（不適用） —— 即時查詢API
- **legal_status**：PUBLIC_SERVICE
- **used_by**：engine/geo_distance_engine.py（REQ-010一般設施步行距離）
- **notes**：REQ-010：一般設施（交通/公共建設/商業機構）採OSRM步行路網距離，特殊設施（殯葬/電業/廢棄物）採直線距離


## SUPPORTING_USED（輔助佐證，非規則主要依據）（5筆）

### 土地徵收補償市價查估作業手冊 -- 獨立副本checksum比對（非獨立來源，佐證用）

- **source_id**：`competition_valuation_manual_independent_verification_note`
- **issuing_agency**：不適用（比對記錄，非文件本身）
- **source_type**：CHECKSUM_CROSS_VERIFICATION_NOTE
- **local_snapshot_path**：（無，見source_type/notes說明原因）
- **original_filename**：klcg_attach.pdf（本輪比對後因與competition_valuation_manual byte-for-byte相同已刪除，不重複存放）
- **retrieved_at**：（不適用） —— 本輪Source Audit過程中，於scratchpad發現一份先前研究階段下載、檔名暗示可能來自基隆市政府網站之PDF（klcg_attach.pdf），確切下載URL因早於本次對話之contextsummary而無法100%重建，屬PROVENANCE_UNCERTAIN。但經SHA-256比對，其內容與competition_valuation_manual（比賽單位提供之土地徵收補償市價查估作業手冊.pdf）**byte-for-byte完全相同**（皆為97cc40697cad3647eb9a121b258791c426c702303c8b66456d2784546a13b342）
- **sha256**：`97cc40697cad3647eb9a121b258791c426c702303c8b66456d2784546a13b342`
- **byte_size**：3,055,284
- **legal_status**：PROVENANCE_UNCERTAIN_BUT_CONTENT_VERIFIED_IDENTICAL
- **notes**：此為獨立佐證記錄，非額外文件：證明比賽單位提供之作業手冊與（推測為）另一政府網站流通版本checksum完全一致，增加對competition_valuation_manual內容未被竄改/未被誤傳之信心，但不將其視為獨立來源（下載URL不確定，不得冒充官方確認來源）

### 完整逐字稿（黑客松命題說明會議）

- **source_id**：`competition_transcript`
- **issuing_agency**：新北市政府地政局（會議逐字稿）
- **source_type**：COMPETITION_PROVIDED_TEXT
- **local_snapshot_path**：`data/sources/competition/完整逐字稿.txt`
- **original_filename**：完整逐字稿.txt
- **retrieved_at**：2026-08-31T13:54:17+08:00 —— 使用者提供檔案之本機檔案系統時間戳記
- **sha256**：`b99cc23ce7b1ee7636282b1477481d39eedf0586b7a00c788c7fb55863db7190`
- **byte_size**：52,061
- **legal_status**：COMPETITION_SUPPORTING_MATERIAL
- **used_by**：docs/phase1/evidence_matrix.md（REQ-027/028，ASR錯字訂正）; docs/phase1/glossary.md
- **notes**：語音辨識逐字稿，含已知ASR錯字（如「地震局」應為「地政局」），僅作輔助佐證，非法規/規則之主要依據

### 影片視覺時間軸

- **source_id**：`competition_video_timeline`
- **issuing_agency**：新北市政府地政局（會議錄影視覺時間軸）
- **source_type**：COMPETITION_PROVIDED_PDF
- **local_snapshot_path**：`data/sources/competition/影片視覺時間軸.pdf`
- **original_filename**：影片視覺時間軸.pdf
- **retrieved_at**：2026-08-31T13:54:14+08:00 —— 使用者提供檔案之本機檔案系統時間戳記
- **sha256**：`4f01a745cc627fe1d16d0d025136362bf9970023b05558400dceda1ae6186c6b`
- **byte_size**：9,847,529
- **legal_status**：COMPETITION_SUPPORTING_MATERIAL
- **used_by**：docs/phase1/evidence_matrix.md（REQ-027畫面比對，如Teams參與者列表確認「地政局」正確名稱）
- **notes**：用於視覺比對逐字稿之ASR錯字（如透過畫面確認正確機關名稱），非法規/規則之主要依據

### 不動產估價技術規則 第27條 單頁快照（補充驗證，非獨立法源）

- **source_id**：`law_estate_valuation_technical_rules_art27_single_page`
- **issuing_agency**：內政部
- **source_type**：HTML_SNAPSHOT
- **source_url**：https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=D0060077&flno=27
- **local_snapshot_path**：`data/sources/laws/不動產估價技術規則_law.moj.gov.tw_第27條單頁_snapshot.html`
- **retrieved_at**：2026-09-01T16:23:00+08:00 —— 本系統直接fetch該URL之時間，與law_estate_valuation_technical_rules全文快照同一研究階段
- **sha256**：`61e2d8f2e8bafaf3e624b1fb19f098efa010c6e35390ef804299980ec5a7792f`
- **byte_size**：43,662
- **legal_status**：OFFICIAL_LAW_SINGLE_ARTICLE
- **dataset_id**：D0060077
- **articles_or_sections_used**：第27條
- **used_by**：engine/comparable_selection_engine.py（§27權重無官方公式之確認）
- **notes**：單一條文頁面快照，內容與law_estate_valuation_technical_rules全文快照中之§27完全一致，作為補充交叉驗證，非獨立來源

### 新北市都市計畫土地使用分區及範圍圖 -- dataset metadata API回應

- **source_id**：`ntpc_zoning_dataset_metadata_api`
- **issuing_agency**：新北市政府城鄉發展局
- **source_type**：JSON_SNAPSHOT
- **source_url**：https://data.ntpc.gov.tw/api/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5/json
- **local_snapshot_path**：`data/sources/manifests/ntpc_zoning_dataset_metadata_api_response.json`
- **retrieved_at**：2026-09-02T09:33:00+08:00 —— 本系統直接fetch該API之時間
- **sha256**：`51eda1ea1c7bfa31c494d755da5aa0e3601edd23a38ab046504445b32e64d060`
- **byte_size**：555
- **legal_status**：OFFICIAL_OPEN_DATA_METADATA
- **dataset_id**：fe26e0a5-54c2-4876-bbc7-150243c048f5
- **used_by**：scripts/sync_ntpc_zoning_dataset.py（fetch_download_link()）
- **notes**：此metadata回應本身列出「新北市使用分區」與「新北市都市計畫範圍」兩個項目之實際下載連結，為動態查詢結果，非固定版本文件


## INSPECTED_NOT_USED（已檢查但未採用）（1筆）

### 新北市都市計畫範圍（Shapefile）

- **source_id**：`ntpc_plan_boundary_shapefile`
- **issuing_agency**：新北市政府城鄉發展局
- **source_type**：GIS_SHAPEFILE_ZIP
- **source_url**：https://urban.planning.ntpc.gov.tw/opendataDownload/新北市都市計畫範圍.zip
- **local_snapshot_path**：`data/sources/gis/新北市都市計畫範圍.zip`
- **original_filename**：新北市都市計畫範圍.zip
- **retrieved_at**：2026-09-02T17:17:07+08:00 —— 本系統直接下載該URL之時間
- **sha256**：`ae50d49b98005532967f741e3759bca1f1f344bee8751d191c9e16c14851c0ec`
- **byte_size**：2,451,909
- **legal_status**：OFFICIAL_OPEN_DATA
- **dataset_id**：fe26e0a5-54c2-4876-bbc7-150243c048f5（item 2）
- **notes**：schema檢查結果：CRS=EPSG:3826（.prj確認），50筆都市計畫範圍圖徵，欄位=[key, Name, Url, SDF_ID, key1, LblName]。原預期Name/LblName欄位可提供官方都市計畫名稱，供point-in-polygon座標→都市計畫自動判定使用。**未採用原因**：逐byte核對Name/LblName欄位原始資料，確認其在來源端已損毀（原始bytes內含字面上的U+FFFD替代字元序列，big5/utf-8/cp950三種編碼嘗試結果一致，證實非本系統解碼參數猜錯）。Url欄位50筆記錄全數空白，無替代連結。資料集亦無CSV/GeoJSON等替代格式。故plan_id/official_plan_name之自動判定改為必須由外部（人工）提供，見providers/base.py ProviderContext docstring之完整記錄
  > **✅ 2026-09-08更新：Name/LblName損毀問題已找到修復對照表**（見下方
  > `ntpc_plan_boundary_name_lookup_patch`條目）——官方靜態Shapefile匯出
  > 本身仍是壞的（本輪已重新下載一次比對SHA256，逐位元組與上方記錄相同，
  > 證實官方尚未修復），但透過該局自己的即時ArcGIS地圖服務找到同一份
  > 資料的未損毀版本，key/SDF_ID數值逐筆核對一致，已建立獨立的名稱
  > 對照表。`plan_id`/`official_plan_name`目前**仍是**由外部（人工）提供
  > 這件事本身**未改變**（対照表尚未接回`providers/base.py`
  > `ProviderContext`或任何production程式碼，純資料修復，非程式碼串接），
  > 但若未來要做自動化，此對照表可以是`data/sources/gis/新北市都市計畫
  > 範圍.zip`裡幾何座標之外的另一個輸入來源。
  >
  > **✅ 2026-09-08再更新：本資料集之幾何座標（`key`/`SDF_ID`屬性）已
  > 正式接回程式碼**——`scripts/sync_ntpc_zoning_dataset.py`新增
  > `load_plan_boundary_polygons()`，讀本地封存之本zip（不重新下載），
  > 對每筆「新北市使用分區」多邊形之centroid做point-in-polygon空間疊合，
  > 查出所屬都市計畫邊界後，透過下方`ntpc_plan_boundary_name_lookup_patch`
  > 對照表換成正確名稱，回填至`zoning_polygons`表原本恆為`NULL`的
  > `plan_name`欄位（`providers/ntpc_zoning_provider.py`
  > `RealNtpcZoningProvider.query()`本就已能回傳`plan_name`，只是資料源
  > 一直是空的）。**仍未改變**：`plan_id`（`data/rules/plan_zone_floor_
  > area_ratios.json`查表用之內部代碼，如`"jinshan"`）依然是由外部
  > （人工）提供，未與此次解析出的`plan_name`（都市計畫中文全名）建立
  > 對應——兩者是不同概念，見`providers/base.py` `ProviderContext`
  > docstring之補充說明。落在都市計畫範圍圖資邊界外、或centroid同時
  > 落入多個邊界多邊形（兩資料集邊界線非完全疊合之已知現實）時，一律
  > 誠實回報`plan_name=None`並附註記，不猜測。

### 新北市都市計畫範圍_名稱對照表.json（Name/LblName修復對照表，非官方發布之衍生檔案）

- **source_id**：`ntpc_plan_boundary_name_lookup_patch`
- **issuing_agency**：本專案自建（衍生自新北市政府城鄉發展局資料，非官方直接發布之檔案）
- **source_type**：DERIVED_PATCH_TABLE（非原始官方發布格式，是本專案對上方
  `ntpc_plan_boundary_shapefile`損毀欄位的獨立修復對照表）
- **local_snapshot_path**：`data/sources/gis/新北市都市計畫範圍_名稱對照表.json`
- **原始資料來源**：新北市政府城鄉發展局「新北市城鄉資訊查詢平台」
  （https://urban.planning.ntpc.gov.tw/）背後之即時ArcGIS Server地圖服務，
  圖層`NTPC_Urban/NTPCUPGIS_SDE`（MapServer layer id=1，
  `NTPCUPGIS_SDE.dbo.Uplan`）——與上方壞掉的Shapefile是**同一套官方
  資料庫**，只是這個即時服務的Name/LblName欄位未損毀
- **retrieved_at**：2026-09-08
- **legal_status**：OFFICIAL_OPEN_DATA（資料本體仍是官方資料；但存取管道
  本身非data.ntpc.gov.tw正式公告之開放API，見下方重要但書）
- **驗證方式**：50筆記錄之key/SDF_ID數值，逐筆與已損毀shapefile之
  同名欄位核對，完全一致（例如key=64/SDF_ID=28兩邊皆同，對應Golden
  Case金山區之「金山都市計畫」），證實為同一份資料，非另一個不相關
  資料集
- **重要但書（存取方式，非資料本身）**：此ArcGIS REST服務平時需要
  access token才能查詢（未帶token直接查詢會得到「Token Required」
  錯誤），並非如data.ntpc.gov.tw那樣任何人皆可直接呼叫、有正式文件
  之開放API。本次取得方式是以Playwright載入官方查詢網頁、攔截該網頁
  本身向後端請求之臨時session token，再用該token查詢——**這是一次性
  資料修復手段，不是可長期倚賴、可正式串接進production的穩定介面**
  （token會過期，此存取模式亦非新北市政府對外公告之穩定服務）。若
  未來需要長期自動化取得此資料，應正式向新北市政府城鄉發展局洽詢
  ArcGIS服務介接授權，或請其修復官方開放資料平台上的Shapefile匯出
  流程本身，而非長期依賴此次取得的臨時token或這種攔截手法
- **已知資料本身之既有瑕疵（非本次修復引入，原樣保留）**：`key=999`
  在原始資料庫裡即有兩筆重複記錄，`Name`皆為「非都市計畫區」但
  `LblName`分別為「林口特定區計畫」與「東北角海岸風景特定區計畫」，
  本對照表原樣保留兩筆、未擅自判斷何者「正確」
- **幾何座標**：本對照表僅含屬性名稱，不含多邊形座標。50個都市計畫的
  幾何邊界本身在上方`新北市都市計畫範圍.zip`裡並未損毀，可沿用其
  geometry，僅需替換其原本壞掉的Name/LblName屬性
- **✅ 2026-09-08更新：已接進程式碼**——`scripts/sync_ntpc_zoning_
  dataset.py`之`load_plan_name_lookup()`讀取本檔案，建成
  `(key, sdf_id) -> name`對照dict，供`load_plan_boundary_polygons()`
  查詢每筆邊界多邊形之正確都市計畫名稱使用（`key=999`兩筆重複記錄因
  查表一律採複合鍵`(key, sdf_id)`而非單獨`key`，實際查詢不會遇到歧義）


## SEARCH_LEAD_NOT_USED（僅作搜尋線索，非官方來源，未採用）（4筆）

### gazette_eg01.pdf（來源不確定，內容疑似104年令另一份書表格式附件）

- **source_id**：`uncertain_gazette_attachment_eg01`
- **issuing_agency**：UNKNOWN（內容顯示與內政部104年令書表格式相關，但確切來源URL無法重建）
- **source_type**：PROVENANCE_UNCERTAIN_PDF
- **local_snapshot_path**：`data/sources/urban_planning/uncertain_provenance/gazette_eg01.pdf`
- **original_filename**：gazette_eg01.pdf
- **retrieved_at**：（不適用） —— 檔案存在於scratchpad，本機mtime為2026-09-01 17:27，但確切下載URL/指令記錄早於本次對話之context summary，無法100%重建。內容為30頁PDF，與gazette_central_max_adjustment_range同樣有字型編碼異常（中文亂碼），文字內容顯示與「土地徵收補償市價查估相關書表格式」（同一104年令之另一項修正標的）有關，推測為同一104年1月30日台內地字第10413006723號令之另一份附件PDF，但未逐一比對確認
- **sha256**：`1445b3d2abbf3d3564bc5ee616bfba77b0d46af29c82ace1bfaf1d4f00d7cc1f`
- **byte_size**：872,292
- **legal_status**：PROVENANCE_UNCERTAIN
- **notes**：本輪Source Audit中發現但未能確認其在任何已出貨JSON/程式邏輯中被引用；未刪除（保留供後續人工核對），亦未升級為權威來源

### hccg_table.pdf（來源不確定，內容疑似地方政府買賣實例調查估價表格式）

- **source_id**：`uncertain_hccg_table`
- **issuing_agency**：UNKNOWN（檔名暗示可能為新竹市政府相關，未確認）
- **source_type**：PROVENANCE_UNCERTAIN_PDF
- **local_snapshot_path**：`data/sources/urban_planning/uncertain_provenance/hccg_table.pdf`
- **original_filename**：hccg_table.pdf
- **retrieved_at**：（不適用） —— 檔案存在於scratchpad，本機mtime為2026-09-01 17:27，確切下載URL/指令記錄早於本次對話之context summary，無法100%重建。內容為24頁PDF，文字層清晰可讀（非亂碼），顯示「土地徵收補償市價查估相關書表格式」、「不動產估價師」、「買賣實例總價格」等欄位，性質與作業手冊之書表附件類似，但未確認具體發布機關
- **sha256**：`73fd131fda1dd9837b32e5774cdd66d7cc5e1fc639b4af31831b7efd70223f59`
- **byte_size**：316,182
- **legal_status**：PROVENANCE_UNCERTAIN
- **notes**：本輪Source Audit中發現但未能確認其在任何已出貨JSON/程式邏輯中被引用；未刪除（保留供後續人工核對），亦未升級為權威來源

### RootLaw植根法律網（都市計畫法新北市施行細則檢索結果）

- **source_id**：`rootlaw_search_lead`
- **issuing_agency**：非官方（第三方法律資料庫）
- **source_type**：SEARCH_LEAD_NOT_ARCHIVED
- **source_url**：https://www.rootlaw.com.tw/LawContent.aspx?LawID=B020050000004500-1080703
- **local_snapshot_path**：（無，見source_type/notes說明原因）
- **retrieved_at**：（不適用） —— WebFetch請求回傳HTTP 403 Forbidden，從未成功取得內容
- **legal_status**：THIRD_PARTY_NON_AUTHORITATIVE
- **notes**：僅作為搜尋線索，用以定位官方planning.ntpc.gov.tw原始PDF連結；因403無法存取，且已找到官方PDF來源（ntpc_urban_planning_enforcement_rules），未被採用亦不作為法源

### 新北市土地使用分區建蔽率及容積率規定表（老闆廠辦房屋仲介有限公司整理製表）

- **source_id**：`bosshouse_third_party_table`
- **issuing_agency**：非官方（房仲業者整理）
- **source_type**：SEARCH_LEAD_NOT_ARCHIVED
- **source_url**：https://bosshouse.tw/upload/files/20160406095047.pdf
- **local_snapshot_path**：（無，見source_type/notes說明原因）
- **retrieved_at**：（不適用） —— 僅出現於搜尋結果列表，從未fetch或使用其內容
- **legal_status**：THIRD_PARTY_NON_AUTHORITATIVE
- **notes**：第三方房仲業者整理表格，非官方來源，未被引用；官方數值以ntpc_urban_planning_enforcement_rules（都市計畫法新北市施行細則附表一）為準
