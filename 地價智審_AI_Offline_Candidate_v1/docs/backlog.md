# Backlog（低優先度、非阻擋性項目）

> 本文件記錄已發現但刻意不在當下輪次修正之LOW severity項目，避免遺失
> 但也不無限擴大單一輪次之修正範圍。

## Windows `/tmp` Portability Fix（Release Candidate Cleanup，2026-09-04）

**根因**：`backend/handlers/document_extract.py`原寫死
`tmp_path = f"/tmp/{document_id}.pdf"`（POSIX路徑）。AWS Lambda執行環境
本身有真實可寫之`/tmp`，此假設在正式部署目標下成立；但在Windows本機直接
執行`pytest`時，`/tmp`不存在（Windows無此路徑），導致
`s3.download_file()`寫入時拋`FileNotFoundError`，`tests/
test_backend_document_handlers_e2e.py`5項測試全數失敗（皆為同一根因，
非5個獨立bug）。此bug先前未被發現，純粹因為本機此前一直缺`moto`/`boto3`
導致整個測試檔案被module-level skip、從未真正執行過。

**修復（本輪，CORE FREEZE之唯一例外，僅此檔案）**：改用
`tempfile.gettempdir()`取代寫死路徑——Windows下解析為真實OS暫存目錄，
Lambda/Linux下（無`TMPDIR`環境變數覆寫）仍解析為`/tmp`（Python
`tempfile`模組POSIX預設候選清單之一），行為對正式部署目標完全不變。
extraction algorithm／handler API／S3行為／validation邏輯／
`LocalExtractionProvider`／business result／response contract**皆未
更動**，僅暫存路徑取得方式改變。

**驗證**：修復前`tests/test_backend_document_handlers_e2e.py`：
5 failed / 7 passed；修復後：**12 passed**。全套件`py -m pytest -q`
修復後於本機重新執行得到**553 passed, 0 failed, 0 skipped**，與Historical
full regression baseline（553 passed/0 failed）完全一致。

## Independent Uploaded Document Extraction Vertical Slice — 現況盤點＋本輪完成範圍（2026-09-03）

**現況盤點（實際掃描`backend/`/`pdf/`/`providers/`/`schemas/`/`engine/`/
`frontend/`/`tests/`/README/HANDOFF_MANIFEST/docs/，非假設）**：
- `docs/phase6/document_extraction_spec.md`：**設計決策文件，非實作**。
  明文因白名單網路無法呼叫AWS Textract，Phase 6改採「Mock Extracted
  Data」，並定義`SubmittedFormData`為未來`TextractAdapter`（明文「本階段
  不存在」）的介面seam
- `TextractAdapter`：僅在`document_extraction_spec.md`／
  `HANDOFF_MANIFEST.md`／`review.py`註解中被「提及為未來擴充點」，
  **repo中零實作**（無任何class/function定義）
- `pdf/pdf_renderer.py`等：PDF**產生**（weasyprint HTML→PDF輸出用），與
  PDF**讀取/擷取**是完全不同方向的關注點，不可混淆，本輪未重用（性質
  不同）
- `backend/handlers/pdf_handler.py`：唯一既有S3互動（`s3.put_object`
  上傳「產生的」PDF並回傳presigned URL），從未讀取上傳檔案
- OCR/文字擷取：**repo內完全不存在任何讀取真實PDF/圖片位元組並程式化
  擷取文字/欄位的程式碼**——無Textract呼叫、無pytesseract/easyocr、無
  PyMuPDF/pypdf文字層擷取，`backend/requirements*.txt`亦未宣告任何此類
  套件（`pymupdf`雖已被安裝於本開發環境，但先前從未被此repo的任何
  requirements檔案或程式碼宣告/使用過）
- 表單分類／bounding box／confidence（OCR意義上的）／上傳檔案端點／
  人工確認Audit Log：**repo內皆不存在**（`domain/models.py`已有的
  `confidence`/`requires_manual_review`欄位屬於Data Provider層之外部
  資料集查詢信心，非OCR擷取信心，語意不同，本輪未混用而是新增獨立的
  `ExtractedField`模型）

**結論**：本輪是在**全新領域**建置，非重做已有功能；`SubmittedFormData`
介面本身（`engine/audit_engine.py`）維持不變，正是`document_extraction_
spec.md`當初設計的正確銜接點。

**本輪已完成**：
- `domain.models`新增`FormType`/`FormClassificationResult`/
  `ExtractionMethod`/`BoundingBox`/`ExtractedField`/
  `HumanConfirmationRecord`（沿用既有Evidence/NormalizedDataPoint
  provenance慣例之欄位命名，非另造平行schema）
- `engine/form_classifier.py`：deterministic-first（固定標題/表號/
  欄位名稱權重加總，無任何LLM呼叫），對本repo實際歸檔之
  `查估書表範本.pdf`6頁**逐頁驗證**：頁1/2/3正確分類為表1/表5-2/表4
  （confidence 0.9-1.0），頁4-6（地價區段略圖等地圖頁）正確回報
  UNCERTAIN＋`requires_manual_review=True`（未被錯誤分類進3張目標表單
  之一）
- `providers/document_extraction_provider.py`：`DocumentExtractionProvider`
  抽象基底＋3個實作：
  - `LocalExtractionProvider`：**真實、已驗證**——以PyMuPDF讀取PDF文字層
    ＋詞級bounding box，對`查估書表範本.pdf`實際擷取9個欄位（表1：
    使用分區/建蔽率/容積率/主要道路名稱/主要道路寬度/區段內道路平均
    寬度；表4：土地正常單價/比準地比較價格/比較標的權重），逐一比對
    Golden Case真值全數正確（第二種商業區/70/240/中山路/18/12/
    184,763/212,958/100）
  - `FixtureExtractionProvider`：MOCK_FIXTURE，供測試建構特定（含刻意
    錯誤）擷取結果，不依賴第二份真實PDF
  - `TextractExtractionProvider`：**CODE_READY，非AWS_RUNTIME_VERIFIED**
    ——實作`_parse_textract_response()`（純函式，已用合成Textract回應
    格式獨立驗證），但`classify()`/`extract_fields()`本身呼叫boto3
    Textract client，因本環境無確認之AWS網路存取，**從未實際對真實
    AWS執行過**，呼叫時明確`raise NotImplementedError`並於訊息中重申
    此限制，不得謊稱已可用
  - 表5-2**本輪僅完成分類（title/表號偵測正確），欄位擷取未完成**——
    28項因素×3欄（優劣等級/修正百分比）之表格式版面，與表1/表4「同列
    label→value」的擷取策略本質不同，需另一套table-row擷取邏輯，誠實
    記錄非本輪範圍，非靜默略過
- `engine/human_confirmation.py`：`flag_low_confidence()`／
  `confirm_field()`／`resolve_confirmed_values()`——confidence低於閾值
  （預設0.75）之欄位，在無對應`HumanConfirmationRecord`前，
  `resolve_confirmed_values()`恆回傳`None`，**結構上**（非僅文件約定）
  無法讓未經確認之低信心擷取值流入`extraction_to_submitted_form.py`
- `engine/extraction_to_submitted_form.py`：已確認欄位值→
  `SubmittedFormData`（僅本輪MVP之4個表1欄位：使用分區/建蔽率/容積率/
  主要道路寬度），純複製，無任何計算/判級邏輯
- **Golden Document E2E**：`tests/test_document_extraction_e2e.py::
  TestGoldenDocumentE2E`——PDF→classify→extract→confirm if needed
  （皆高信心，無需人工確認）→reconstruction→`AuditEngine.review()`，
  確認第二種商業區/BCR 70/FAR 240正確進入既有系統並PASSED/PASSED；
  main_road_width=18誠實維持`ROAD_WIDTH_UNAVAILABLE`（未因OCR讀到18就
  升格為官方Evidence，符合指示item 8明文警告）
- **Error Document E2E**：`TestErrorDocumentE2E`——FixtureExtractionProvider
  模擬FAR被改為300之擷取結果，確認submitted=300（OCR擷取值，未被覆寫）
  vs official=240（獨立解析之外部參考值）→`FLOOR_AREA_RATIO_INCONSISTENT`；
  另補一項低信心擷取未經確認即被攔截（`resolve`回傳`None`，不進入
  AuditEngine前即被攔下）之regression
- 新增測試39項（`test_form_classifier.py`11＋
  `test_document_extraction_provider.py`15＋`test_human_confirmation.py`
  10＋`test_document_extraction_e2e.py`3）
- `backend/requirements-extraction.txt`（新檔，比照`requirements-pdf.txt`
  之per-Lambda-function慣例）宣告`pymupdf`/`boto3`/`pydantic`

**本輪尚未完成（誠實記錄）**：
- 無任何backend Lambda handler／API endpoint串接此vertical slice——
  本輪僅完成engine/provider層＋Golden/Error E2E（比照建蔽率/容積率／
  道路寬度先前皆是先完成engine層、下一輪才做backend串接之節奏）
- 無上傳檔案端點（S3 presigned PUT、multipart等）
- 表5-2欄位擷取（僅分類完成，見上）
- `TextractExtractionProvider`從未對真實AWS執行過，CODE_READY≠
  AWS_RUNTIME_VERIFIED
- 表1/表4僅擷取本輪列出的9個代表性欄位，未擷取整張表全部欄位
- 實價登錄、地籍、土壤液化、新道路資料來源、DatasetRegistry歷史表
  重新設計、全部14張書表、AI估價模型——依指示本輪皆未開始

## Road Width Multi-Evidence Model — 本輪已完成範圍與尚未完成範圍（2026-09-03）

**現況盤點（實際查證，非假設）**：`providers/road_provider.py`原僅有
`MockRoadProvider`，涵蓋4欄位：`main_road_name`（顯示用，不參與評等）、
`main_road_width`（區域因素，`data/rules/regional_rules.json`「主要道路
寬度」規則，Golden Case 18M→普通等級，與表5-2一致）、
`segment_avg_road_width`（區域因素「區段內道路平均寬度」，Golden Case
12M）、`road_development_level`（區域因素「道路規劃闢建程度」，enum制）。

**官方來源查證結果（實際搜尋已歸檔PDF全文，非猜測）**：
- A（政府官方／授權道路資料）：未確認本地可靠來源
- B（都市計畫道路寬度）：`data/sources/urban_planning/變更金山細部計畫...`
  PDF確實多處記載「計畫道路寬度」規定（如「第一、二種住宅區面臨計畫
  道路寬度10公尺以上」），但那是**建築線退縮等管制規則的門檻值**，
  **不是**逐路查詢用之結構化道路寬度資料表（不同於建蔽率/容積率已有
  附表一19分區×2指標的結構化表格可查）。同文件確認「中山路」僅作為
  地理位置參照（Ⅰ-2至Ⅱ-1道路），**無任何處記載其實際寬度數值**
- C（OSM/外部地圖）：已知涵蓋率過低不可靠（沿用先前調查結論）
- D（系統幾何估算）：本codebase無此能力
- E（使用者／估價書申報值）：`查估書表範本.pdf`第1、3頁確認「主要道路
  名稱：中山路　寬度：18M」——**此為範例報告書本身的申報值／現場勘查
  記載，並非政府開放資料集查得之官方屬性**。逐字搜尋全部4份已歸檔
  競賽PDF＋金山都市計畫PDF，**查無任何獨立來源確認中山路=18M**

**結論（誠實記錄，未假造）**：Golden Case main_road_width=18M目前
**SOURCE_UNCONFIRMED**——無法以官方/可靠來源獨立驗證，`tests/
test_smart_review.py::TestRoadWidthWiringInAuditEngine::
test_golden_case_18m_source_unconfirmed_without_evidence`鎖定此誠實行為
（Mock Golden Case展示與Real驗證邏輯已分離：Mock模式仍固定回傳18/12，
Real模式的`RoadWidthResolver`在無Evidence時誠實回報UNAVAILABLE，絕不
沿用Mock數字）。

**本輪已完成**：`domain.models.RoadWidthEvidence`/`RoadWidthEvidenceType`
/`RoadWidthResolutionResult`模型、`engine/road_width_resolver.py`
（優先序＋衝突偵測，都市計畫寬度與現況寬度保持不同語意，衝突時保留
全部Evidence並回`MANUAL_REVIEW_REQUIRED`，不平均不亂猜）、`engine/
road_width_validator.py`（`ROAD_WIDTH_UNAVAILABLE`/
`ROAD_WIDTH_EVIDENCE_CONFLICT`/`ROAD_WIDTH_INCONSISTENT`三態不得混淆）、
`providers/road_provider.py`新增`RealRoadProvider`（DI-testable、未接任何
evidence_sources時誠實回傳UNKNOWN，已接入`collect_data.py`真實模式
provider清單，取代原本`main_road_width`在real模式下仍固定跑Mock的舊
行為）、`engine/audit_engine.py`正式串接（`SubmittedFormData.
submitted_main_road_width`＋`AuditEngine.review()`新增`road_width_evidence`
參數）。

**本輪尚未完成（誠實記錄，非本輪範圍）**：
- `backend/handlers/collect_data.py`尚未將`RealRoadProvider`解析出的
  `RoadWidthEvidence`／`RoadWidthResolutionResult`持久化進`FACTORS`
  record，`review.py`也尚未讀回並傳入`AuditEngine.review()`的
  `road_width_evidence`參數——本輪僅完成到engine層（AuditEngine接受
  該參數並正確運作，已有完整測試證明），backend handler層的實際資料流
  串接是下一輪的工作（比照建蔽率/容積率當初也是先engine層、再AuditEngine
  串接、最後才是backend串接的三輪節奏）
  > **⚠️已過時/superseded（2026-09-03同日稍晚，backend vertical slice輪）**：
  > 上一句已完成——`collect_data.py`新增`_resolve_road_width_evidence()`，
  > 把`RealRoadProvider.resolve_main_road_width()`的完整evidence清單
  > （real模式，mock模式維持`[]`）持久化進
  > `factors_record["road_width_evidence"]`；`submitted_main_road_width`
  > 透過新的頂層request body key（非硬塞進base_parcel_factors）持久化進
  > `factors_record["road_width_submission"]`；`case_reconstruction.py`
  > 新增`extract_road_width_evidence()`／`extract_submitted_main_road_
  > width()`；`review.py`讀回兩者並傳入`AuditEngine.review(...,
  > road_width_evidence=...)`。5項新Backend E2E
  > （`tests/test_backend_handlers_e2e.py::TestRoadWidthBackendE2E`）
  > 涵蓋無Evidence/PASS/mismatch/conflict/real-mode不fallback五種情境，
  > 詳見`docs/phase8a/competition_checklist.md`「Road Width Backend
  > Vertical Slice」一節。保留原文字僅為維持歷史紀錄。
- `segment_avg_road_width`／`road_development_level`：依指示本輪僅處理
  `main_road_width`，這兩者在real模式下持續回報UNKNOWN，無resolution
  基礎設施，維持backlog
- A類（政府官方道路資料）與D類（幾何估算）皆無實作，僅在架構上以
  `evidence_sources`注入點預留擴充能力，未來若找到可靠來源可直接接入
  而不需重新設計model/resolver/validator

## BLK-03（Phase 7 NO-GO Recovery第2輪發現，`pyflakes`）

| 檔案 | 問題 | 建議修法 |
|---|---|---|
| `backend/handlers/analyze.py:85` | `except ... as e:`未使用`e` | 將`e`納入log訊息或明確`except Exception:`捨棄變數 |
| `backend/handlers/explanation.py:95` | 同上 | 同上 |
| `backend/handlers/pdf_handler.py:9` | `import json`未使用 | 移除該行import |
| `backend/handlers/review.py:51` | `audit`變數賦值後未使用 | 對應下方「review.py MVP簡化」項目一併處理 |

## review.py（已於Phase 8A修復，原MVP簡化描述已過時）

**原描述（已過時，保留供追溯）**：`backend/handlers/review.py`僅示範重建
`SubmittedFormData`，未呼叫`AuditEngine.review()`。

**現況（Phase 8A Acceptance Review發現並修復）**：此問題實際嚴重度為
**HIGH FUNCTIONAL BLOCKER**（非LOW）——舊版`review.py`從未呼叫審查邏輯，
永遠回傳硬編碼空result。已於Phase 8A修復，見
`docs/phase8a/offline_readiness_report.md`修復記錄2。修復後仍保留之
**LOW severity**限制：目前審查的是「案件自身計算結果之再現性」而非
「與獨立提交來源之比對」（因無真實Textract整合），此為刻意的MVP範圍
邊界，非缺陷，已於`review.py` docstring明確記載。

## Phase 7A 環境設定完整性缺口

**已解決**：`BEDROCK_MODEL_ID`已於`infra/template.yaml::ExplanationFunction.Properties.
Environment.Variables`顯式定義（`BEDROCK_MODEL_ID: !Ref BedrockModelId`），本項目原記錄
之缺口已不存在，僅保留紀錄避免誤判為未修。

## 其他新發現（Phase 8A）

- Repo根目錄無`.gitignore`（僅有`tests/.pytest_cache/.gitignore`為工具
  自動產生）。非安全漏洞（本輪掃描確認無真實憑證洩漏），但屬預防性
  最佳實踐缺口，建議未來新增涵蓋`__pycache__/`、`.env`、`*.pem`等模式。
- Repo根目錄無`README.md`（先前規劃於Phase 7離線工作項目但尚未建立）。

## Phase 4既有已知限制（沿用，未在Phase 7處理）

- `favicon.ico`等16個缺失圖片（Phase 4 `frontend_inventory.md`已記錄）。
- `analysis.json`之`grades`/`adjustments`陣列內部物件為簡化自建結構，
  非完整`GradeResult`/`AdjustmentResult`序列化（Phase 4驗收時已記錄為
  次要發現）。

## collect_data.py 未持久化 regional_base_factors／regional_comparable_factors（2026-09-02發現，非本輪範圍）

`backend/handlers/collect_data.py`目前只把Provider查得的點位寫入
`factors_record["points"]`（一個扁平list，目前無任何handler讀取）與
`user_submitted_factors.base_parcel_factors`/`comparable_factors`（逕自
`body.get(...)`，未經Provider處理）；`case_reconstruction.py`實際讀取的
`regional_base_factors`／`regional_comparable_factors`兩個key，
**`collect_data.py`從未寫入過**——這代表目前透過真實API流程（非測試手動
seed）呼叫`review()`時，區域因素（含`regional_land_use_zone`）恆為空
list，`_review_regional_factors`與`AuditEngine`的建蔽率/容積率官方基準
解析都拿不到資料。`tests/test_backend_handlers_e2e.py`裡每個既有E2E測試
（`_seed_full_case`）都手動在`collect_data()`之後直接改寫
`factors_record["regional_base_factors"]`，就是為了繞過這個缺口，本身
即為此問題存在的證據。**修復需要把`collect_data.py`的Provider輸出（或
`points`list）轉換為`regional_base_factors`/`regional_comparable_factors`
形狀寫回`FACTORS`record**，屬於獨立的一輪整合工作，不在
LandUseRatioValidator↔AuditEngine後端串接（2026-09-02收尾輪）範圍內，
故僅記錄不修正。

> **⚠️已過時/superseded（2026-09-03）**：`regional_land_use_zone`（以及
> `regional_building_coverage_ratio`／`regional_floor_area_ratio`，作為
> 補充evidence）已於本輪修復——`collect_data.py`新增
> `_to_regional_factor()`，將land_use_provider（Mock或Real，視
> `DATA_PROVIDER_MODE`而定）輸出之`NormalizedDataPoint`對應為
> `regional_base_factors`之FactorInput形狀並寫回FACTORS record，見
> `docs/phase8a/competition_checklist.md`「Provider輸出正式寫入
> regional_base_factors」一節。**`regional_comparable_factors`（比較標的
> 側之區域因素）仍未修復**——本輪僅處理比準地（base）側，因
> `AuditEngine`目前的建蔽率/容積率檢查只讀取`regional_base_factors`；
> 比較標的側區域因素若未來需要類似Provider查詢，需另立一輪（涉及為每個
> 比較標的解析各自座標，目前request payload/資料模型皆未設計此欄位）。

## Real Data Provider 尚未涵蓋範圍（本輪新增，非阻擋）

- `providers/land_use_provider.py`（都市計畫使用分區／建蔽容積率等地政登記
  資料）：`land_use_zone`欄位已在`DATA_PROVIDER_MODE=real`下改用
  `RealNtpcZoningProvider`（新北市政府城鄉發展局分區圖資本地快照）查詢，
  **但其餘14個欄位**（建蔽率、容積率、禁限建、排水、地勢等）仍固定回傳
  `UNKNOWN`，本輪未找到可靠公開資料源可即時查詢。
  > **⚠️已過時/superseded（2026-09-02）**：上一句已不準確。`building_
  > coverage_ratio`／`floor_area_ratio`兩欄位已改用`LandUseRatioEngine`
  > 兩層規則庫（`data/rules/ntpc_common_zone_ratios.json`＋
  > `data/rules/plan_zone_floor_area_ratios.json`）查詢並解析，real模式
  > 下不再固定`UNKNOWN`；且該兩欄位已進一步串接至`LandUseRatioValidator`
  > ↔`AuditEngine.review()`（見`docs/phase8a/competition_checklist.md`
  > 「LandUseRatioValidator↔AuditEngine正式串接」一節）。目前仍固定
  > `UNKNOWN`者為**剩餘13個欄位**（禁限建、排水、地勢等，`providers/
  > land_use_provider.py`之`_NO_REAL_SOURCE_FIELDS`），非原文所稱14個。
  > 保留原文字僅為維持歷史紀錄，不代表目前現況。

  `providers/road_provider.py`
  （道路寬度）整個Provider仍固定使用Mock：OSM對台灣道路寬度標記涵蓋率過低
  不可靠。見`docs/phase8a/real_data_provider_guide.md`。
- 已接上真實資料源的5個Provider中，部分子欄位仍誠實留白（非規避）：站牌
  密集程度、顧客通行量、店舖毗連狀態、各類污染源、國小/國中/高中/大學
  分級——皆為Sources未提供可計算規則之主觀評估欄位，見同上文件「絕不猜測
  的兩條界線」。
- 決賽當天正式AWS Lambda環境對外連線至`overpass-api.de`／
  `nominatim.openstreetmap.org`之可行性尚未驗證（本開發環境已確認可連線，
  但正式AWS帳號網路政策未知），應於AWS部署演練時第一批測試。

## LAND_PRICE_FULL_SYNC_STATUS（Phase API-1.5，2026-09-05）

`scripts/sync_land_price_dataset.py`對新北市114年公告土地現值（~1,153,450筆）
執行單次無filter完整分頁同步（原因見`docs/phase9/api_integration_spec.md`
§11：district filter經證實對此資料集完全無效，per-district範圍同步之
設計已放棄，改為完整同步後依每筆紀錄自身`district`欄位分桶）。此為對
公開政府API的一次性、約5,750次分頁請求之大型ETL工作，執行時間約
30-60分鐘。

> **✅ Phase API-1.6更新（2026-09-05）：已完成**。第三次執行（加入
> checkpoint+retry修正，見`CHECKSUM_REPRODUCIBILITY_FRAGILITY`前一則
> 記錄無關、屬另一次修正）成功完成：**29/29行政區，總筆數1,153,450**，
> 與官方dataset metadata完全吻合，程序正常結束（非中途中止）。第一次
> 因架構誤解（district filter）作廢重做，第二次因網路連線中斷
> （`WinError 10054`，發生於約92萬筆處）且原始設計僅於結尾寫入而遺失
> 全部進度——已修正為每250頁checkpoint寫入。`LandPriceProvider`現可對
> 全部29個行政區之查詢回答真實資料（不再需要「請先執行sync腳本」之
> UNKNOWN回應）。

## CHECKSUM_REPRODUCIBILITY_FRAGILITY（Phase API-1.6，2026-09-05）

> **✅ Phase API-1.7已解決（2026-09-05）**：新增獨立版本化演算法
> `CANONICAL_V2`（`providers/cadastral_dataset_cache.py::
> compute_canonical_checksum_v2_land_price/_expropriation`），固定欄位
> 陣列順序＋依`(district,segment,lid或id)`排序整個records列表＋新增
> `land_price_records.country`欄位持久化，使checksum可**單純從資料表
> 重新查詢即100%重現**，無需`ORDER BY rowid`或補回未持久化欄位等workaround。
> 原`compute_records_checksum()`**未被覆蓋**，完整保留為`LEGACY_V1`
> （既有已寫入之snapshot其checksum欄位意義不變）。`snapshot_meta`新增
> `checksum_algorithm`／`checksum_schema_version`欄位（additive schema
> migration，既有row預設標記為`LEGACY_V1`）。全部29個土地現值行政區
> snapshot已重新以CANONICAL_V2同步並獨立驗證29/29 MATCH。詳見
> `docs/phase9/api_integration_spec.md`§23-24。下方為Phase API-1.6原始
> 記錄，保留供追溯脆弱性成因。

`providers/cadastral_dataset_cache.py::compute_records_checksum()`對
records list做`json.dumps(..., sort_keys=True)`——`sort_keys`僅排序
每筆dict的key，**不排序list本身順序**。單純從`land_price_records`／
`expropriation_records`資料表重新`SELECT`（未加`ORDER BY rowid`）並
重算checksum，因SQLite不保證回傳順序等於原始insert順序，**幾乎必然
不吻合**；另外原始checksum計算時之record含官方API的`country`欄位，
但SQL schema未持久化此欄位，進一步導致「直覺式」重新計算失敗。

**已驗證**（加`ORDER BY rowid`+補回`country`後，4個行政區之checksum
100%吻合）：**資料本身完全正確，此為checksum機制設計脆弱性，非資料
損毀**。詳見`docs/phase9/api_integration_spec.md`§18。

**下一輪建議修復**（本輪未執行，會使現有已寫入之checksum全數作廢）：
1. `compute_records_checksum()`改為先依穩定key（如`(district,segment,
   lid或id)`）排序records再序列化，使checksum與查詢/儲存順序無關
2. 決定`country`欄位去留：若無實質provenance價值，應直接排除於checksum
   輸入之外並在程式註解說明；若需保留，應在`land_price_records`schema
   新增對應欄位持久化

## BULK_DOWNLOAD_MIGRATION_CANDIDATE（Phase API-1.6，2026-09-05）

> **✅ Phase API-1.7已實作（2026-09-05）**：`scripts/sync_land_price_
> dataset.py`之`PAGE_SIZE`已由200改為500,000（§20先以page=0,1,2對比
> 既有snapshot逐key驗證等價性，確認`LARGE_PAGE_PAGINATION_VERIFIED=YES`
> 後才migrate）。實測：request數由~5,768降為3，執行時間由35-50分鐘
> 降為102秒。retry/checkpoint/timeout/response shape validation均保留，
> 並新增總筆數與重複key驗證。`scripts/sync_expropriation_dataset.py`
> （原~48次請求，資料量小，效益有限）**本輪未migrate**，維持PAGE_SIZE
> =200，非本輪範圍。CSV transport經實測欄位完全保真且bytes/速度皆優於
> JSON，但本輪評估切換效益（30秒級）不足以抵銷新增解析層與重新驗證
> pipeline之成本，**維持JSON transport**，CSV仍留待未來輪次評估。詳見
> `docs/phase9/api_integration_spec.md`§20-22。下方為Phase API-1.6原始
> 記錄，保留供追溯。

實測發現data.ntpc.gov.tw之`size`參數並非200筆上限，實際可接受至少
500,000（更大則遭WAF攔截）。`scripts/sync_expropriation_dataset.py`／
`scripts/sync_land_price_dataset.py`現行皆使用`size=200`分頁（前者
~48次請求、後者~5,750次請求），效率遠低於使用大size分頁之替代方案
（估計後者可縮短至3次請求、時間從30-60分鐘降至約1-2分鐘）。詳細比較
見`docs/phase9/api_integration_spec.md`§17。**下一輪建議**：將兩個
sync腳本之`PAGE_SIZE`調整為200,000~500,000區間（保留WAF安全邊際），
其餘邏輯（checkpoint、checksum、per-district分桶）不需改變。本輪未
執行此變更（現行200筆分頁無正確性問題，僅效率議題）。

## DATASET_VERSION_HISTORY_BACKLOG（Phase API-1，2026-09-05）

> **⚠️ Phase API-1.5更新（2026-09-05）**：以下原文所述「兩者皆為bounded
> per-case Runtime Query，非本地snapshot」**已不再準確**。Phase API-1.5
> 新增`providers/cadastral_dataset_cache.py`（獨立於`DatasetRegistry`
> 之平行SQLite store，理由見`docs/phase9/api_integration_spec.md`§14），
> 兩個Provider之主要查詢路徑現在**都讀本地snapshot**。但下方原文所指出的
> 根本限制依然存在且未解決：`cadastral_dataset_cache.py`同樣是
> upsert-only（每次sync以`DELETE`+`INSERT`整批取代），**同樣沒有歷史
> 版本表**，`effective_date`/`publication_date`/`jurisdiction`欄位
> 依然缺席。換言之，本輪是新增了一個「與DatasetRegistry原則相同、但
> 範圍更窄」的平行store，並未解決本backlog項目所述的根本設計缺口，
> 該缺口目前同時存在於兩個store中，仍待後續輪次一併處理。

`providers/dataset_registry.py`目前`dataset_id TEXT PRIMARY KEY`＋
`ON CONFLICT(dataset_id) DO UPDATE`（upsert-only），只保留每個dataset_id的
CURRENT一筆，無版本歷史表。本輪（Phase API-1）新增之`ExpropriationCaseProvider`
／`LandPriceProvider`並未使用DatasetRegistry（兩者皆為bounded per-case
Runtime Query，非本地snapshot），故本輪**未觸碰**此問題，但Audit報告已指出
此限制未來會造成：

- 無法回溯「這筆Evidence在查詢當下，官方資料集是哪一個版本/checksum」
- 若未來`LandPriceProvider`改為Audit建議之SCHEDULED_SYNC/VERSIONED_SNAPSHOT
  架構（本輪因時間/範圍考量未實作，見`docs/phase9/api_integration_spec.md`
  「Known limitations」），DatasetRegistry現有schema需要能記錄
  `effective_date`（法律生效日，區別於`source_last_modified`技術時間戳）、
  `publication_date`、`jurisdiction`欄位，且需要能保留多筆歷史版本（非
  upsert覆蓋），否則「哪一個歷史查詢用的是哪個版本」會無法追溯

**本輪僅記錄，不實作**（依Audit報告第八節「本次只提出設計建議，禁止直接
重構Registry」之既有結論，本輪維持不變）。

## NLSC_ROAD_WIDTH_SEMANTIC_MAPPING = UNCONFIRMED（Phase API-1，2026-09-05）

Audit報告已確認NLSC「臺灣通用電子地圖」道路中線圖層（WFS代碼`EMAP_ROAD`）
確實存在官方`WIDTH`（路寬）屬性欄位，定義為「原則上紀錄各路段之最大路面
寬度，即含中央分隔島之參考道路面範圍」。**但**：

- 查估手冊之「面前道路寬度」／`main_road_width`要求的是**實際出入通行用
  之道路路寬**（估價師現場勘查認定之申報值，見`docs/backlog.md`「Road
  Width Multi-Evidence Model」一節E項）
- NLSC `WIDTH`為**含中央分隔島之參考路面測繪推估值**，與「實際出入通行
  寬度」在語意上**是否等價，本輪未經法律/測繪專業審查確認**

因此：**NLSC_ROAD_WIDTH_SEMANTIC_MAPPING = UNCONFIRMED**。本輪（Phase
API-1）**未實作**任何將NLSC WIDTH寫入`main_road_width`或`RoadWidthEvidence`
的程式碼（依批准範圍第七節明文禁止）。`RoadWidthResolver`/
`RoadWidthValidator`維持現狀不變。若未來要接入，須先由具測繪/估價專業背景
之人員確認兩者語意等價性，並可能需要在`RoadWidthEvidenceType`新增一個
獨立分類（而非套用既有「都市計畫寬度」或「現況寬度」兩類），因為NLSC WIDTH
既非都市計畫規定寬度，也未必等同現況實測寬度。

此外，NLSC EMAP_ROAD本身之授權門檻極高（需與國土測繪中心簽訂「測繪合作
契約」，官方提供對象列示為中央機關/地方政府/國營事業，未見對本團隊此類
競賽/民間單位開放之證據），即使語意確認可行，取得資料授權本身仍是獨立的
前置障礙。

## TGOS_AUTH_PENDING（Phase API-1，2026-09-05）

TGOS MAP API（地址定位AddrLocate、地標查詢POILocate/PointBuffer/PoiBuffer）
皆需要TGOS會員資格＋線上申請APPId/APIKey，且APIKey與申請登記之Domain/IP
綁定，官方文件未承諾審核時效（同體系「TGOS批次地址比對服務」文件明示
72小時）。截至本輪（Phase API-1），**尚未申請、尚未取得APIKey**。

- **AWS Secrets Manager／TGOS Secret：未新增**（依批准範圍第七節明文禁止，
  本輪未修改AWS資源）
- **`TGOSProvider`：未實作**（依批准範圍第七節明文禁止）
- 現況：`osm_facility_lookup.py`（Nominatim/Overpass）仍為Geocoding/POI
  唯一資料源，維持不變
- 若未來申請取得APIKey：依「官方>TGOS>OSM」既有優先序原則，接入時**不得**
  silent fallback——TGOS呼叫失敗時必須外顯回退原因（`fallback_reason`），
  不可悄悄改用OSM卻不留痕跡

## EXPROPRIATION_SYNC_LARGE_PAGE_NOT_MIGRATED（Phase API-1.7，2026-09-05）

`scripts/sync_expropriation_dataset.py`（已公告徵收案件地籍資料，
~9,437筆，單一`scope_key=__ALL__`）本輪**未migrate**至large-page
（仍為`PAGE_SIZE=200`，約48次請求）——使用者本輪明確範圍僅要求migrate
`scripts/sync_land_price_dataset.py`。因資料量遠小於土地現值資料集，
48次請求之絕對耗時本就不高，效益有限，但架構上完全可套用相同已驗證之
large-page策略。連帶地，既有徵收案件snapshot（`scope_key=__ALL__`）
之`checksum_algorithm`欄位因未重新sync，經schema migration後仍標記為
`LEGACY_V1`（非資料錯誤，屬預期行為——見`CHECKSUM_REPRODUCIBILITY_
FRAGILITY`），與土地現值29個snapshot已全數升級至`CANONICAL_V2`不一致。
**下一輪若處理徵收案件sync效率，建議一併將其checksum升級為
CANONICAL_V2**（`write_expropriation_snapshot`已支援，僅需重新執行
sync腳本一次）。

## CSV_TRANSPORT_NOT_ADOPTED（Phase API-1.7，2026-09-05）

CSV transport經本輪實測（size=2,000筆樣本＋§17既有size=500,000大樣本）
確認：欄位鍵集合、順序、`lid`零填充字串保真度皆與JSON完全一致（0筆
cross-format mismatch），且bytes約為JSON之46%、速度更快
（13.47秒 vs 23.28秒＠500,000筆）。**本輪決定不切換**——已完成之
large-page＋JSON migration已將3頁全量sync降至102秒，CSV可再省之
30秒級效益不足以抵銷新增CSV解析層（`csv.DictReader`＋UTF-8 BOM處理）＋
重新驗證整條pipeline（provider查詢/checksum/Golden Case）之工程成本。
詳細比較表見`docs/phase9/api_integration_spec.md`§22。若未來sync頻率
提高或資料量再成長數倍，可重新評估。

## EXPROPRIATION_KEY_NOT_UNIQUE_MULTI_PROJECT_PARCELS（Phase API-1.8，2026-09-05）

> ✅ **Phase API-1.9已解決（2026-09-05）**：`CadastralDatasetCache`新增
> `lookup_expropriation_matches()`（回傳List[Dict]，取代`Real
> ExpropriationCaseProvider`原本使用的單筆`lookup_expropriation()`），
> `ExpropriationCaseEvidence`新增`match_count`/`matches`/
> `raw_match_count`/`distinct_event_count`欄位，`RealExpropriationCase
> Provider.query_case()`現在會取回並保留該地號**全部**真實比對紀錄，
> 不再靜默漏失。詳見`docs/phase9/api_integration_spec.md`§37-45與
> `providers/expropriation_case_provider.py`模組docstring。
>
> 同時修正本項目原記錄中一項不精確之處：Phase API-1.8僅抽樣前20組
> 重複key即誤判「僅1組為EXACT_SOURCE_DUPLICATE」；Phase API-1.9對全部
> 441組完整分類後，正確數字為**379組PARCEL_MULTI_EVENT**（同parcel、
> 內容不同之真實多事件）＋**62組EXACT_SOURCE_DUPLICATE**（同parcel、
> 全部rows逐欄位相同）＋**90筆EXACT_DUPLICATE_FULL_ROWS**（重複列之raw
> row數，非parcel數）。下方為Phase API-1.8原始記錄，保留供追溯。

> ⚠️ **Phase API-1.8原始severity評估**（本項目與下方其餘LOW項目不同，
> 建議優先處理）——雖然不影響Phase API-1.8 Release Gate判定（未改變
> Golden Case結果、未違反該輪atomicity/completeness範疇），但涉及
> `RealExpropriationCaseProvider`可能**靜默漏失真實案件資料**，已於
> Phase API-1.9處理完成（見上方✅）。

**發現方式**：Phase API-1.8為`scripts/sync_expropriation_dataset.py`
新增atomic staging/promote流程時，`promote_expropriation_staging_to_
current()`加入的promote-time重複key防禦性檢查，在對**真實政府API**
執行完整同步時意外觸發失敗，因而發現：

`(district, segment, land_no)` **並非**已公告徵收案件地籍資料集的唯一
鍵。對全部9,437筆真實資料完整掃描，發現**441組**不同的
`(district,segment,id)`重複key（共471筆重複列），絕大多數代表**同一
地籍（同區/段/地號）在不同年度被不同徵收計畫分別徵收**（例如
板橋區/江子翠段第一崁小段/10067同時出現在2005年與2006年的3個不同
側環河快速道路工程徵收案中，`sus_year`/`pro_name`皆不同）；另有1組
（淡水區/水仙段/5080002）為逐欄位完全相同之單純重複列。

**影響**：`providers/cadastral_dataset_cache.py::lookup_expropriation()`
（`RealExpropriationCaseProvider.query_case()`唯一之lookup路徑）在有
多筆符合`(district,segment,land_no)`的紀錄時，目前的SQL查詢邏輯只會
回傳**第一筆**符合的紀錄，其餘真實存在、內容不同的徵收案件紀錄會被
**靜默忽略**，使用者無從得知同一地號其實還有其他徵收案例存在。此為
Phase API-1.5設計時「一個地號=一筆徵收案件」的隱含假設，被本輪真實
資料完整掃描證偽。

**本輪處理方式**（未修正Provider邏輯，僅記錄+繞過阻擋）：
`promote_expropriation_staging_to_current()`新增`enforce_duplicate_
check`參數（預設`True`，適用於土地現值等經驗證唯一之key），
`scripts/sync_expropriation_dataset.py`顯式傳入`enforce_duplicate_
check=False`（重複計數仍會記錄於`duplicate_key_count`供可見性，但不
阻擋promote——否則此資料集因441組真實重複，將永遠無法通過atomic
staging同步）。

**下一輪建議修復方向**（本輪未執行，需Provider層級設計調整，超出本輪
snapshot atomicity/completeness範疇）：
1. `lookup_expropriation()`改為回傳**list**而非單筆，`RealExpropriation
   CaseProvider.query_case()`與`ExpropriationCaseEvidence`需相應調整
   以承載「同一地號有多筆徵收案件」的情形（例如新增
   `MULTIPLE_MATCHES_FOUND`狀態，或`evidence.matches: List[...]`）
2. 或在key中納入`sus_year`/`pro_name`使查詢語意變成「查詢特定計畫」而
   非「查詢地號」——但需先確認案件審查流程是否已知具體計畫名稱/年度，
   否則無法據此縮小查詢
3. 兩種方向皆需具備土地徵收業務知識之人員參與設計，避免產生新的誤導性
   假設

## OFFLINE-ACCEPTANCE-1 Browser Demo 驗收發現（2026-09-06，P1/P2，未修正）

本輪為「Offline Browser Demo Acceptance」，範圍限定為**驗證**既有離線
Demo流程能否完整走完，明文禁止本輪順手修UI/重構。以下2項於實際瀏覽器
操作（Playwright + `python3 -m http.server 8000` from `frontend/app/`，
真實上傳`查估書表範本.pdf`走完Create Case→...→Result全流程）中發現，
皆判定為P1/P2（不阻擋流程、不產生錯誤結果、不誤導UNKNOWN為PASS），
故僅記錄不修正。

### 1. `css/img/picture3.jpg` 404（P2，純美觀）

`frontend/app/`下某CSS規則引用`css/img/picture3.jpg`，但實際圖片檔位於
`img/picture3.jpg`（相對於`frontend/app/`，非`css/`底下）。瀏覽器console
出現一則404，不影響任何功能性頁面內容或資料正確性，純缺一張裝飾性背景圖。

**建議修法**：修正CSS規則中的相對路徑（`css/img/`→`../img/`或改為
`img/`），或直接補一份圖片至`css/img/`。

### 2. PDF預覽頁「表4+表5-2」頁數顯示為「-頁」（P1，純顯示層，非資料遺失）

**根因**：`frontend/mock/pdf_result.json`（及其巢狀複本
`frontend/app/frontend/mock/pdf_result.json`）中，`forms_included`／
`pdf_urls[].form`使用的標籤是`"表4+表5-2（系統整合輸出）"`（含括號後綴），
但`page_count`物件的key卻是不含括號的`"表4+表5-2"`。`pdf-preview.html`
（第79行）以`forms_included`陣列的完整標籤字串去查`page_count`物件，
因key不吻合而查無對應值，fallback顯示為`'-'`。

**影響範圍確認（不是P0）**：僅影響頁面上「頁數」這行輔助顯示文字；
下載連結（`pdf_urls[].url`）本身key正確、不受影響。已直接以`pypdf`
解析`frontend/app/frontend/mock/pdf/表4_表5-2_Golden_Case.pdf`確認：
檔案有效（PDF 1.7，6頁）、可正常開啟、內含正確中文字（`建蔽率`／
`容積率`皆可擷取到）與正確關鍵數值（案號`1140901-99-001`、
最終比準地比較價格`212958`皆存在於文字層）。PDF本身完全正常，僅頁數
提示文字顯示錯誤，故列為P1而非P0。

**建議修法**：將`page_count`物件的key改為與`forms_included`/
`pdf_urls[].form`完全一致的完整標籤字串（即`"表4+表5-2（系統整合
輸出）"`），或改用穩定的短key（如`"表4_表5-2"`）並讓兩處各自的顯示邏輯
都用同一個穩定key去對照，避免顯示用全稱與查找用key混用。

### 3.（非bug，僅記錄以避免未來誤判）review.html與result.html之計數差異為刻意設計

`review.html`（讀`frontend/mock/review_result.json`）顯示45 Passed／
2 Error／1 Inconsistent；`result.html`（讀`frontend/mock/result.json`）
顯示48 passed／0 error／0 inconsistent，兩者對同一Golden Case
（`1140901-99-001`）看似矛盾。經查`scripts/build_demo_review_result_
mock.py`docstring與`docs/phase8a/error_case_showcase.md`：這是**刻意
設計**——`review_result.json`是`docs/phase8a/demo_script.md`Step 7
「系統能抓出3個刻意植入之錯誤」這個賣點專用的Error Case展示fixture
（由真實engine對Golden Case+3個刻意竄改值重新執行取得，非手動編造，
45個未竄改欄位仍全數Passed，證明非無差別報錯），而`result.html`則展示
Golden Case本身（未經竄改）的乾淨最終結果。`docs/phase8a/backup_demo_
guide.md`第8/9步本就是把這兩頁當成**兩個獨立的展示橋段**依序介紹，非
同一資料的前後狀態。

**建議（非本輪修正，僅供未來排練現場demo話術參考）**：現場如果依序點過
這兩頁，建議明確口述「這是Smart Review抓錯能力的展示」再點`review.html`、
「這是Golden Case本身乾淨過關的最終結果」再點`result.html`，避免聽眾誤以為
是同一次審查的前後矛盾結果。

## RC1_PDF_IMAGE_BLOAT（P1/P2，2026-09-07，RC1 Freeze時確認）

`backend/docker/pdf.Dockerfile`第23行`COPY data ${LAMBDA_TASK_ROOT}/
data`複製整個`data/`目錄（本輪實測808MB，含407MB cadastral sqlite與
400MB `data/sources/`）進PdfFunction Container Image，但`pdf/`套件與
`pdf_handler.py`實際完全不讀取這些檔案（僅需`data/rules/`與
`data/dependency_graph.json`）。RC1 Freeze時以`docker images`實測
`pdffunction:pdf-latest`最終image大小為**2.52GB**。不阻擋build/deploy
（遠低於10GB上限），但拖慢image push/pull與冷啟動，屬不必要浪費。

**建議修法**（本輪未執行，比照`EngineLayer`Makefile已窄化之複製
範圍）：`pdf.Dockerfile`改為僅`COPY data/rules`與
`data/dependency_graph.json`，取代整個`COPY data`。

## RC1_FITZ_DEPRECATION_WARNING（P2，2026-09-07，RC1 Freeze時確認）

`providers/document_extraction_provider.py:405`使用`import fitz`
（PyMuPDF舊別名）。RC1 Freeze時於ExtractionLayer實際built artifact內
執行`import fitz`確認functionally正確（可正常建立/寫入PDF），但
pymupdf 1.28.2已產生deprecation warning：「The `fitz` API is
deprecated and will be removed in future. Use `import pymupdf`
instead.」。非阻擋項目，建議未來一輪順手改為`import pymupdf`。

## RC1後續事項（AWS部署／RAG，僅記錄不實作）

- **AWS Deployment Prerequisites**：完整清單（region/認證/IAM權限/
  Cadastral Snapshot S3 bucket/SAM parameters/DynamoDB/S3/ECR/
  CloudFront/Bedrock optional）見`docs/release/RC1_AWS_PREREQUISITES.md`，
  待正式比賽AWS環境取得後依該文件「部署順序建議」節執行。
- **API Gateway EDGE vs REGIONAL、29秒逾時**：待真實AWS benchmark後
  決定，見`docs/release/RC1_KNOWN_LIMITATIONS.md`第6點。
- **RAG（Retrieval-Augmented Generation）**：`NOT_IMPLEMENTED /
  OPTIONAL`，非核心必要功能，不影響RC1 Release Gate判定，見
  `docs/release/RC1_KNOWN_LIMITATIONS.md`第11點。

## 優先度說明

`EXPROPRIATION_KEY_NOT_UNIQUE_MULTI_PROJECT_PARCELS`已於Phase API-1.9
解決（見該條目✅標記），不再需要優先處理標記。以上其餘項目皆為LOW
severity：不影響核心正確性（Rule/Grade/Adjustment/Calculation/Golden
Case/Cross-form）、不影響安全性、不影響AWS部署本身是否可行，僅為程式碼
品質/設定完整性之精進空間，不構成任何Phase之GO/NO-GO判定依據。
