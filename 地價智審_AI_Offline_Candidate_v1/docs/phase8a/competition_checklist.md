# Phase 8A — Competition Checklist（現況盤點，非READY FOR COMPETITION宣告）

> 依`ENVIRONMENT-BLOCKED CONDITIONAL HANDOFF`指示，本文件**不宣稱**
> AWS Ready／Live Demo Ready／Deployment Passed，僅誠實盤點現況。

## Independent Uploaded Document Extraction Vertical Slice（2026-09-03，第一輪：engine/provider層＋Golden/Error PDF E2E）

- [x] **現況盤點**：完整掃描`backend/`/`pdf/`/`providers/`/`schemas/`/
      `engine/`/`frontend/`/`tests/`/README/HANDOFF_MANIFEST/docs/，確認
      `docs/phase6/document_extraction_spec.md`為設計決策文件（非實作），
      `TextractAdapter`僅為文件中提及之未來擴充點（repo零實作），repo內
      **無任何**讀取真實PDF/圖片並程式化擷取文字的既有程式碼。詳見
      `docs/backlog.md`「Independent Uploaded Document Extraction Vertical
      Slice」一節完整盤點記錄。未重做任何已有功能
- [x] **Extraction schema**：`domain.models`新增`FormType`／
      `FormClassificationResult`／`ExtractionMethod`／`BoundingBox`／
      `ExtractedField`／`HumanConfirmationRecord`，沿用既有Evidence/
      NormalizedDataPoint provenance慣例欄位命名，未另造平行schema
- [x] **FormClassifier**（`engine/form_classifier.py`）：deterministic-
      first（固定標題/表號/欄位標籤權重加總，無LLM呼叫），對
      `查估書表範本.pdf`實際6頁逐頁驗證：頁1/2/3→表1/表5-2/表4
      （confidence 0.9-1.0），頁4-6（地圖頁）→UNCERTAIN＋
      requires_manual_review=True，未被誤分類
- [x] **DocumentExtractionProvider三實作**（`providers/document_
      extraction_provider.py`）：`LocalExtractionProvider`（真實PyMuPDF
      文字層＋詞級bbox擷取，對Golden Case PDF驗證9個欄位全數正確）、
      `FixtureExtractionProvider`（MOCK_FIXTURE，測試用）、
      `TextractExtractionProvider`（CODE_READY，明確標示非
      AWS_RUNTIME_VERIFIED，呼叫時raise NotImplementedError，其純
      response-parsing邏輯已用合成Textract回應獨立驗證）
- [x] **代表性欄位**：表1擷取使用分區/建蔽率/容積率/主要道路名稱/
      主要道路寬度/區段內道路平均寬度（6項），表4擷取土地正常單價/
      比準地比較價格/比較標的權重（3項），共9項，逐一比對Golden Case
      真值全數正確；表5-2僅完成分類、欄位擷取誠實記錄為本輪未完成
      （28因素×3欄之表格式版面需不同擷取策略）
- [x] **OCR/AI不得決定規則**：`ExtractedField`只保存raw_text/
      normalized_value，新增測試`test_ocr_never_outputs_a_grade_or_
      adjustment_verdict`明確鎖定擷取結果不得為「優」「良」「普通」
      「差」「劣」「估價錯誤」等判定字樣
- [x] **Human confirmation**（`engine/human_confirmation.py`）：
      `flag_low_confidence()`（預設閾值0.75，可配置）／`confirm_field()`
      （保存`extracted_value`/`confirmed_value`/`confirmed_by`/
      `confirmed_at`，沿用新增之`HumanConfirmationRecord`而非另建Audit
      Log）／`resolve_confirmed_values()`——**結構上**保證低信心欄位無
      對應確認紀錄時恆回傳`None`，非僅文件約定
- [x] **Golden PDF E2E打通**：`tests/test_document_extraction_e2e.py::
      TestGoldenDocumentE2E`——真實PDF→classify→extract→confirm if
      needed→reconstruction（`engine/extraction_to_submitted_form.py`）
      →`AuditEngine.review()`，確認第二種商業區/BCR70/FAR240正確進入
      既有系統並PASSED/PASSED。main_road_width=18誠實維持
      `ROAD_WIDTH_UNAVAILABLE`（未因OCR讀到18就升格為官方Evidence，
      符合指示明文警告，測試斷言鎖定）
- [x] **Error PDF E2E打通**：`TestErrorDocumentE2E`——
      FixtureExtractionProvider模擬FAR被改為300之擷取結果，確認
      submitted=300（OCR擷取值）≠official=240（獨立解析參考值）→
      `FLOOR_AREA_RATIO_INCONSISTENT`；另一項測試證明低信心擷取在無
      人工確認前無法進入AuditEngine（`resolve_confirmed_values`回傳
      None，reconstruction結果為None，最終回報MISSING而非誤判）
- [x] **是否真的用了AWS Textract**：**沒有**。`TextractExtractionProvider`
      為CODE_READY介面（`_parse_textract_response()`純函式邏輯已驗證），
      但`classify()`/`extract_fields()`從未對真實AWS執行過，呼叫時明確
      raise並於錯誤訊息重申此限制，不宣稱AWS_RUNTIME_VERIFIED
- [x] 新增`backend/requirements-extraction.txt`（`pymupdf`/`boto3`/
      `pydantic`），比照`requirements-pdf.txt`per-Lambda-function慣例
- [x] **尚未完成（誠實記錄）**：無backend Lambda handler／上傳端點串接
      （本輪僅engine/provider層，比照建蔽率/容積率/道路寬度先前之
      「先engine層、再backend串接」節奏）；表5-2欄位擷取；
      TextractExtractionProvider未經AWS_RUNTIME_VERIFIED；僅9個代表性
      欄位非整張表
- [x] 完整regression：**493 passed / 0 failed / 0 skipped**（含上一輪
      454項＋本輪新增39項）；`--collect-only`同樣回報493項；LandUseRatio/
      RoadWidth/DATA_PROVIDER_MODE fail-fast/Golden Case既有regression
      皆未受影響
- [x] WeasyPrint已知非致命warning本輪未處理（未變更）

## Road Width Backend Vertical Slice（2026-09-03，Multi-Evidence Model之後端串接輪）

- [x] **collect_data持久化RoadWidth Evidence**：檢查
      `RealRoadProvider`→`NormalizedDataPoint`/`RoadWidthEvidence`→
      `collect_data.py`→`FACTORS`資料流後，新增
      `_resolve_road_width_evidence(ctx)`——僅在`DATA_PROVIDER_MODE=real`
      時呼叫`RealRoadProvider.resolve_main_road_width(ctx)`取得完整
      `RoadWidthResolutionResult.evidence`（每筆皆保留road_name/width_m/
      evidence_type/source_name/source_agency/source_url/dataset_id/
      dataset_version/retrieved_at/confidence/legal_status/
      derivation_method/requires_manual_review/notes全數欄位，沿用既有
      `RoadWidthEvidence` model，未另造schema），寫入
      `factors_record["road_width_evidence"]`（一個list，**非**單一
      `main_road_width=18`裸數字）。Mock模式維持`[]`（Mock無evidence
      概念，見item 6）
- [x] **review.py正式讀回**：`case_reconstruction.py`新增
      `extract_road_width_evidence(factors_record)`（重建為真正的
      `RoadWidthEvidence`物件list，非raw dict）；`review.py`讀回並傳入
      `AuditEngine.review(..., road_width_evidence=...)`。**未**在
      review handler內hardcode `18m`/`12m`/`中山路`——確認`review.py`
      全文搜尋無任何此類字面值
- [x] **submitted／external Evidence分離**：`submitted_main_road_width`
      無`individual_`因素清單對應項（主要道路寬度是REGIONAL factor，非
      per-comparable比較之個別因素），故不硬塞進`base_parcel_factors`，
      改用獨立頂層request body key`submitted_main_road_width`（比照
      `plan_id`既有慣例），持久化進
      `factors_record["road_width_submission"]`，經
      `extract_submitted_main_road_width()`讀回。新增regression
      `test_c_reliable_evidence_mismatch_is_inconsistent`：submitted=12、
      reference=18，跑完`collect_data→complete_form→review`後
      `submitted_value`仍為`"12"`，`expected_value`為`"18"`，兩者互不
      覆寫
- [x] **Backend E2E新增5項**（`tests/test_backend_handlers_e2e.py::
      TestRoadWidthBackendE2E`），僅stub `evidence_sources`這一個DI邊界
      （比照`TestLandUseRatioProviderDrivenBackendE2E`既有慣例）：
      - A. No evidence：`evidence_sources=[]`→`ROAD_WIDTH_UNAVAILABLE`
        （Missing），`expected_value=None`，未fallback Mock 18m
      - B. Reliable evidence matches：TEST_INJECTED_REFERENCE=18、
        submitted=18→PASSED
      - C. Reliable evidence mismatch：submitted=12、
        TEST_INJECTED_REFERENCE=18→ROAD_WIDTH_INCONSISTENT，
        submitted_value=12／expected_value=18分別驗證
      - D. Evidence conflict：都市計畫20／外部地圖18／幾何估算17.5→
        ROAD_WIDTH_EVIDENCE_CONFLICT（Warning，requires_human_review=
        True），確認**不會**變成ROAD_WIDTH_INCONSISTENT
      - 額外：`test_real_mode_never_produces_golden_case_numbers_without_
        evidence`鎖定real模式無資料時`expected_value`恆為None
- [x] **TEST_INJECTED_REFERENCE標記**：B/C/D三項測試中，注入之evidence
      的`source_name`皆明確標記為字面字串`"TEST_INJECTED_REFERENCE"`，
      類別docstring亦明確聲明「Golden Case's 18m...is NEVER presented as
      an official reference here」，避免與Golden Case真實申報值混淆
- [x] **Real mode完全不fallback Mock，重新鎖定**：`RealRoadProvider`
      預設建構（無injected evidence_sources）在real模式下，
      `collect_data`→`FACTORS`→`review`全鏈路皆確認`main_road_width`
      恆為`UNKNOWN`／`ROAD_WIDTH_UNAVAILABLE`，除非evidence本身真的
      提供數值；`test_a_no_evidence_is_unavailable_not_fallback_mock`與
      `test_real_mode_never_produces_golden_case_numbers_without_
      evidence`兩項獨立測試共同鎖定
- [x] **segment_avg_road_width／road_development_level本輪仍不處理**：
      依指示維持backlog，real模式持續回報UNKNOWN
- [x] **既有regression重新確認全數通過**（204項獨立重跑）：
      RoadWidthResolver（10項）、RoadWidthValidator（8項）、
      AuditEngine RoadWidth wiring（`TestRoadWidthWiringInAuditEngine`
      8項）、LandUseRatio全套（validator/engine/provider/backend E2E）、
      DATA_PROVIDER_MODE fail-fast（含於backend E2E）、Provider-driven
      zoning（`TestLandUseRatioProviderDrivenBackendE2E`）、Golden Case
      （`test_golden_case.py`/`test_form_completion_golden.py`）皆綠燈
- [x] WeasyPrint已知非致命warning本輪未處理（依指示）
- [x] 完整regression：**454 passed / 0 failed / 0 skipped**（含上一輪
      449項＋本輪新增5項）；`--collect-only`同樣回報454項

## Road Width Multi-Evidence Model（2026-09-03，「都市計畫分區＋建蔽率＋容積率」封板後新一輪）

- [x] **現況盤點與官方來源查證**：實際逐字搜尋已歸檔之金山都市計畫PDF、
      查估書表範本.pdf、土地徵收補償市價查估作業手冊.pdf，確認A（政府
      官方道路資料）／C（OSM）／D（幾何估算）皆無可靠來源，B（都市計畫
      道路寬度）僅有建築線退縮門檻值而非結構化逐路寬度表，E（申報值）
      即Golden Case「中山路18M」實際出處——**全數4份已歸檔文件皆查無
      獨立來源確認18M**，誠實記錄為SOURCE_UNCONFIRMED，未假造。詳見
      `docs/backlog.md`「Road Width Multi-Evidence Model」一節完整查證
      記錄
- [x] **RoadWidthEvidence／RoadWidthEvidenceType／RoadWidthResolutionResult**
      （`domain/models.py`）：5種evidence_type
      （OFFICIAL_ATTRIBUTE／URBAN_PLAN_DESIGN_WIDTH／
      EXTERNAL_MAP_ATTRIBUTE／GEOMETRIC_ESTIMATE／SUBMITTED_VALUE），
      每筆Evidence保存road_name/width_m/source_name/source_agency/
      source_url/dataset_id/dataset_version/retrieved_at/confidence/
      legal_status/derivation_method/requires_manual_review/notes全數
      指定欄位。沿用ZoningQueryResult/LandUseRatioResolutionResult既有
      「獨立richly-typed result model」慣例，未疊加進NormalizedDataPoint/
      Evidence（兩者皆無evidence_type/dataset_id/legal_status/
      derivation_method first-class欄位）
- [x] **RoadWidthResolver**（`engine/road_width_resolver.py`）：優先序
      OFFICIAL_ATTRIBUTE＞URBAN_PLAN_DESIGN_WIDTH＞EXTERNAL_MAP_ATTRIBUTE
      ＞GEOMETRIC_ESTIMATE，但優先序**僅用於「全部可用Evidence數值一致
      時決定引用哪一筆」**，任兩筆Evidence數值不一致（含跨tier，如都市
      計畫20m/地圖18m/幾何估算17.5m三方案例）一律回`CONFLICT`＋
      `MANUAL_REVIEW_REQUIRED`並保留全部Evidence，絕不平均、絕不因
      優先序高就強行採用。SUBMITTED_VALUE恆被排除於resolution候選
      之外
- [x] **RoadWidthValidator**（`engine/road_width_validator.py`）：三態
      不得混淆——`ROAD_WIDTH_UNAVAILABLE`（MISSING，查無Evidence非申報
      錯誤）／`ROAD_WIDTH_EVIDENCE_CONFLICT`（WARNING，來源衝突非申報
      錯誤，即使申報值恰好等於某一衝突候選值之一，也不得判為相符或
      違規）／`ROAD_WIDTH_INCONSISTENT`（INCONSISTENT，僅當resolution
      成功解析出單一高信心值且該Evidence本身未標記
      requires_manual_review時才可產生；Stale等低信心resolution即使
      數值一致或不一致，一律降級為WARNING，不斷言PASSED或INCONSISTENT）
- [x] **RealRoadProvider**（`providers/road_provider.py`）：保留
      `MockRoadProvider`不變；`RealRoadProvider`預設建構（未注入
      evidence_sources）誠實回傳`main_road_width=None/confidence=UNKNOWN`
      ——**不**回退Golden Case之18/12。支援DI注入`evidence_sources`
      （callable list，比照`RealLandUseProvider`之`zoning_provider`注入
      慣例），供未來若確認可靠來源時接入而不需重新設計model/resolver/
      validator。已接入`collect_data.py`真實模式provider清單（取代
      原本road_provider恆定為Mock之舊行為，修正「real path不得fallback
      mock」對此provider先前未落實之缺口）
- [x] **AuditEngine.review()正式串接**：`SubmittedFormData`新增
      `submitted_main_road_width`；`AuditEngine.review()`新增
      `road_width_evidence`參數，內部呼叫`RoadWidthResolver`→
      `RoadWidthValidator`，case-level執行（非per-comparable）。
      submitted／official/reference全程分離：`submitted_main_road_width`
      只讀申報值，resolved reference只讀`road_width_evidence`，
      `AuditIssue.submitted_value`／`expected_value`兩者皆保存，任一衝突
      情境下皆不互相覆寫（已用regression鎖定，見下方測試）
- [x] **Golden Case 18M獨立來源確認結果**：**無法獨立確認**（見上）。
      已明確分離Mock Golden Case展示（`MockRoadProvider`仍固定回傳
      18/12，UI/PDF展示不受影響）與Real驗證邏輯
      （`TestRoadWidthWiringInAuditEngine::
      test_golden_case_18m_source_unconfirmed_without_evidence`確認
      Golden Case demo fixture在無road_width_evidence時誠實回報
      `ROAD_WIDTH_UNAVAILABLE`，並以獨立測試
      `test_golden_case_18m_passes_when_reliable_evidence_confirms_it`
      證明「若未來真有可靠Evidence確認18M，同一套validator邏輯會正確
      PASS」——證明現在的UNAVAILABLE是Evidence缺口本身，不是validator
      有bug）
- [x] **Error Case**：submitted=12m、reliable reference=18m（單一
      OFFICIAL_ATTRIBUTE來源）→`ROAD_WIDTH_INCONSISTENT`，已測試證明
- [x] **測試新增35項**：`tests/test_road_width_resolver.py`（10項，含
      三方衝突不平均、優先序僅用於同值仲裁）、`tests/
      test_road_width_validator.py`（8項，三態不混淆＋stale evidence不
      斷言PASS/INCONSISTENT）、`tests/test_road_provider.py`（8項，
      RealRoadProvider不fallback Mock＋DI注入場景）、`tests/
      test_smart_review.py::TestRoadWidthWiringInAuditEngine`（8項，
      Golden／Error／多來源一致／衝突／無Evidence／submitted-official
      分離／evidence lineage）；既有`TestDemoErrorCases`兩項因新增
      case-level check（Golden Case road width誠實回報UNAVAILABLE）
      而更新斷言（3個demo error仍為3個CRITICAL，另加1個誠實的MEDIUM
      MISSING，總計4個non-passed，明確標註原因與新增測試佐證，非
      鬆動既有驗收標準）
- [x] **既有regression重新確認全數通過**：LandUseRatio全套（validator/
      engine/provider/backend E2E，134項獨立重跑）、DATA_PROVIDER_MODE
      fail-fast（含於backend E2E套件）、Golden Case（`test_golden_case.py`
      /`test_form_completion_golden.py`）皆綠燈，未受本輪影響
- [x] **尚未完成（誠實記錄）**：`collect_data.py`尚未持久化
      RoadWidthEvidence／resolution進`FACTORS`record，`review.py`尚未
      讀回並傳入`AuditEngine.review()`——本輪僅完成到engine層，backend
      handler串接為下一輪工作，詳見`docs/backlog.md`
- [x] WeasyPrint已知非致命warning本輪未處理（依指示）
- [x] 完整regression：**449 passed / 0 failed / 0 skipped**（含上一輪
      414項＋本輪新增35項）；`--collect-only`同樣回報449項

## 安全性修正：Fail-fast DATA_PROVIDER_MODE＋GIS Evidence checksum可追溯性（2026-09-03，封板前收尾）

- [x] **DATA_PROVIDER_MODE不得靜默fallback Mock**：`collect_data.py`原本
      `_MODE_PROVIDERS.get(DATA_PROVIDER_MODE, _MODE_PROVIDERS["mock"])`
      會讓任何拼錯或未知的mode字串靜默回退成mock，違反「Real path不得
      fallback Mock」核心原則。改為`_resolve_data_provider_mode()`：
      環境變數**未設定**→`"mock"`（明確的開發預設值，非fallback）；
      設定為`"mock"`或`"real"`→照用；設定為**任何其他字串**（typo、
      `"offline"`、`"hybrid"`、空字串等）→立即`raise RuntimeError`，
      訊息含`INVALID_DATA_PROVIDER_MODE`，在模組載入時（Lambda cold
      start／test reload的當下）就失敗，不是等到某個request才發現
- [x] **新增9項測試**（`tests/test_backend_handlers_e2e.py`）：
      `TestDataProviderModeSwitch`新增`test_mock_mode_uses_mock_land_use_
      provider`／`test_unset_mode_is_an_explicit_default_not_a_fallback`；
      新增`TestDataProviderModeFailsFastOnInvalidValue`：
      `test_invalid_mode_raises_instead_of_falling_back_to_mock`
      （parametrize六組：`typo`／`offline`／`hybrid`／空字串／
      `REALX`／`raел`西里爾字母混淆字，皆raise）、
      `test_invalid_mode_never_produces_a_working_all_providers_list`
      （直接證明`ALL_PROVIDERS`在失敗的reload後**未被靜默重新指派成
      mock清單**——例外在該指派陳述式執行前就丟出，維持reload前的舊
      值不變，而非悄悄變成mock）
- [x] **GIS Evidence→dataset_version→DatasetRegistry→checksum可追溯性
      確認**：檢查後發現`dataset_version`本身**不足以**定位
      `DatasetRegistry`中的快照列——`DatasetRegistry.get_current_snapshot()`
      以`dataset_id`（非`dataset_version`）為查詢鍵／primary key
      （`providers/dataset_registry.py`）。`ZoningQueryResult`原本只帶
      `dataset_version`（值即`snapshot.local_snapshot_version`），未帶
      `dataset_id`（雖然`providers/ntpc_zoning_provider.py`內部一直有
      固定常數`DATASET_ID="ntpc_zoning"`，只是從未透過該result物件對外
      暴露）。**最小修正**：`domain.models.ZoningQueryResult`新增
      `dataset_id: Optional[str] = None`欄位（單純新增欄位，非重造
      Source Registry）；`ntpc_zoning_provider.py`3處建構
      `ZoningQueryResult`處補上`dataset_id=DATASET_ID`；
      `land_use_provider.py`的`_land_use_zone_point()`composed notes
      字串新增`dataset_id=...`（沿用既有「無first-class欄位者組合進
      source/notes」慣例，未新增duplicate schema）
- [x] **新增2項端到端追溯性測試**（`tests/test_land_use_provider.py::
      TestGisEvidenceTraceabilityToDatasetRegistry`，皆實際跑
      `RealNtpcZoningProvider`＋真實`DatasetRegistry`，非手造
      `ZoningQueryResult`）：
      1. `test_evidence_dataset_id_locates_the_registry_row_and_its_checksum`：
         真實point-in-polygon查詢產生的`land_use_zone`NormalizedDataPoint
         之notes含`dataset_id=ntpc_zoning`與`資料集版本=...`，且以該
         `dataset_id`呼叫`DatasetRegistry.get_current_snapshot()`確實能
         取回**相符的checksum／source_url**
      2. `test_superseded_snapshot_version_is_an_honest_gap_not_silently_
         papered_over`：**誠實記錄一個現有設計限制**——`DatasetRegistry`
         每個`dataset_id`僅保留「目前這一列」（upsert，無版本歷史表），
         若某筆Evidence記錄的`dataset_version`所屬快照之後被重新
         sync覆蓋，該筆Evidence的**確切checksum已無法從DatasetRegistry
         回溯**（只能確認「目前的current snapshot版本與這筆Evidence不
         符」，屬於detectable mismatch，而非靜默視為仍可追溯）。本輪依
         指示**未**新增版本歷史表（避免另造重複Source Registry），此
         限制誠實記錄於程式註解與本文件，供未來若需要更強追溯性時參考
- [x] **未修改Evidence model本身**（`domain.models.Evidence`／
      `FactorInput`）——僅`ZoningQueryResult`新增一個欄位；沿用既有
      「無對應first-class欄位者組合進source/notes」慣例
- [x] **regional_comparable_factors維持backlog不處理**（依指示，未擴充
      Comparable schema）
- [x] **既有Provider-driven regression重新確認全數通過**：Golden
      70/240→PASSED/PASSED、Error 60/300→INCONSISTENT/INCONSISTENT、
      submitted≠official（防overwrite）、zoning not found≠Mock fallback，
      4項皆維持綠燈，未受本輪修改影響
- [x] WeasyPrint已知非致命warning本輪未處理（依指示）
- [x] 完整regression：**414 passed / 0 failed / 0 skipped**（含上一輪403
      項＋本輪新增11項：9項fail-fast mode測試＋2項GIS追溯性測試）；
      `--collect-only`同樣回報414項

## Provider輸出正式寫入regional_base_factors（2026-09-03，收尾輪之二）

- [x] **實際流程檢查**：`collect_data.py`→provider registry/RealLandUseProvider
      →`FACTORS`record→`regional_base_factors`此前完全未串接
      （`collect_data.py`只寫`points`／`user_submitted_factors`，
      `case_reconstruction.py`實際讀的`regional_base_factors`從未被寫入，
      見`docs/backlog.md`上一輪記錄，本輪修復）
- [x] **`regional_land_use_zone`正式打通**：`collect_data.py`新增
      `_to_regional_factor()`，把land_use_provider（`MockLandUseProvider`
      或`RealLandUseProvider`，視`DATA_PROVIDER_MODE`）輸出中的
      `land_use_zone`／`building_coverage_ratio`／`floor_area_ratio`三個
      `NormalizedDataPoint`對應為`regional_land_use_zone`／
      `regional_building_coverage_ratio`／`regional_floor_area_ratio`三個
      FactorInput，寫回`factors_record["regional_base_factors"]`。
      `NtpcZoningProvider`→`official_raw_zone_name`→
      `regional_base_factors["regional_land_use_zone"]`→
      `case_reconstruction`→`AuditEngine`全鏈路已由Provider-driven E2E
      測試證明（見下方），非手動seed
- [x] **submitted／official永遠分離**：`regional_building_coverage_ratio`／
      `regional_floor_area_ratio`僅作為補充evidence寫入
      `regional_base_factors`（`AuditEngine`目前的建蔽率/容積率check仍只
      讀取`regional_land_use_zone`取得分區名稱，內部透過
      `LandUseRatioValidator`自行呼叫`LandUseRatioEngine`重新解析官方
      參考值，**不**直接信任Provider算好的數字）；`submitted_building_
      coverage_rate`／`submitted_floor_area_ratio`只讀`case.base_parcel_
      factors`（使用者POST的`individual_*`欄位），與Provider輸出完全是
      兩條獨立路徑，未曾交叉污染。新增regression
      `test_submitted_value_never_overwritten_by_provider_reference`：
      submitted FAR=300、Provider/official FAR=240，經
      `collect_data`→`complete_form`→`review`後`submitted_value`仍為
      `"300"`（若曾變成`"240"`此測試會失敗）
- [x] **Evidence lineage**：沿用既有`FactorInput.evidence`（`Evidence`
      model）schema，**未另造一套**——`case_reconstruction.to_factor_
      inputs()`新增讀取每筆raw dict的`"evidence"`子物件（不存在時維持
      原本的通用「使用者輸入」Evidence，向後相容）；`source_url`／
      `source_agency`／`dataset_version`／`legal_status`／
      `derivation_method`這幾項`Evidence`本身無對應欄位者，沿用
      `RealLandUseProvider`既有慣例組合進`source`／`notes`字串（未新增
      duplicate schema）；`source_type`（`NormalizedDataPoint`為開放字串
      如`"GovernmentOpenData"`/`"Mock"`，`Evidence.source_type`為封閉
      enum）以對映表轉換，非新增enum成員：`"GovernmentOpenData"`→
      `SourceType.GIS_MEASUREMENT`（現有enum中最貼近GIS空間查詢語意者），
      其餘沿用既有預設`SourceType.AI_ASSISTED_FILL`。`checksum`欄位誠實
      說明：`ZoningQueryResult`本身目前未攜帶per-query checksum
      （checksum屬於`providers/dataset_registry.py`的資料集快照層級，
      非單筆查詢結果），故未偽造此欄位，未串接
- [x] **Real／Mock（現有僅此兩種，見下方誠實澄清）查不到zoning時絕不
      fallback Mock**：新增
      `test_zoning_not_found_is_unknown_not_fallback_to_mock`——Provider
      查無涵蓋座標（`zone_name=None`）時，`regional_base_factors`完全不
      含`regional_land_use_zone`（非UNKNOWN佔位，直接缺席），
      `AuditEngine`誠實回報`LAND_USE_RATIO_RULE_UNAVAILABLE`
      （issue_type=Missing），確認**未**意外沿用同模組內MockLandUseProvider
      的Golden Case分區「第二種商業區」
- [x] **DynamoDB round-trip float bug（本輪新發現，非既有回歸）**：
      `case_store.put_record`此前對任何payload內含原生Python `float`
      （例如`Coordinate.latitude/longitude`、`RealLandUseProvider`算出的
      `float(resolved_value_pct)`）一律被boto3拒絕
      （`Float types are not supported`），本輪第一次因為
      Provider-driven測試把真實座標＋成功解析的zoning結果一路寫進
      `FACTORS`record才被觸發（先前的backend E2E從未讓`RealNtpcZoning
      Provider`真正resolve成功過）。修復：`case_store.py`新增
      `_dynamodb_safe()`遞迴轉換（float→Decimal，經str()避免二進位誤差），
      套用在`put_case_meta`／`put_record`兩個寫入點，一次性防護所有
      handler，而非各自零星修補
- [x] **Provider-driven Backend E2E新增4項**
      （`tests/test_backend_handlers_e2e.py::
      TestLandUseRatioProviderDrivenBackendE2E`）：僅stub
      `RealNtpcZoningProvider`這一個I/O邊界（與`tests/test_land_use_
      provider.py`既有、文件明載之慣例相同），其餘（正規化、
      LandUseRatioEngine解析、`_to_regional_factor()`對應）全部走真實
      邏輯，**未**手動塞`regional_base_factors=[{"regional_land_use_zone":
      "第二種商業區"}]`：
      - `test_golden_provider_driven_flows_to_passed`：Provider解出第二種
        商業區，submitted BCR=70/FAR=240 → PASSED/PASSED
      - `test_error_provider_driven_flows_to_inconsistent`：submitted
        BCR=60/FAR=300 → INCONSISTENT/INCONSISTENT
      - `test_submitted_value_never_overwritten_by_provider_reference`：
        防止overwrite的關鍵regression（見上）
      - `test_zoning_not_found_is_unknown_not_fallback_to_mock`：查無zoning
        時的誠實行為（見上）
- [x] **DATA_PROVIDER_MODE誠實澄清（重要，回應「請再次確認mock/offline/
      online/hybrid四種mode」）**：`backend/handlers/collect_data.py`目前
      **只有兩種實際模式**：`"mock"`（預設）與`"real"`。`"offline"`／
      `"hybrid"`**在這個程式碼庫中不存在**——`DATA_PROVIDER_MODE`讀到任何
      未識別字串（含字面上打錯或打算表示的"offline"/"hybrid"）時，
      `_MODE_PROVIDERS.get(DATA_PROVIDER_MODE, _MODE_PROVIDERS["mock"])`
      會**靜默回退成mock模式**，不會報錯也不會有任何獨立行為——這點本輪
      誠實記錄但**不視為本輪缺口修正**（未被要求新增這兩種模式，新增
      屬於超出「只完成regional_base_factors這個缺口」之範圍，避免臨時
      擴大範圍）。`real`模式下`RealLandUseProvider`使用本地zoning
      shapefile快照（無執行期網路呼叫）本質上已具備user所稱「offline」
      之特性，最貼近的既有實作即是`real`模式本身，並非另一個獨立模式
- [x] 完整regression：**403 passed / 0 failed / 0 skipped**（含上一輪399
      項＋本輪新增4項Provider-driven E2E）；`--collect-only`同樣回報403項

## Backend資料流串接＋已知環境警告（2026-09-02，收尾輪）

- [x] **API payload → case reconstruction → SubmittedFormData → AuditEngine.
      review()完整資料流**：`backend/handlers/case_reconstruction.py`新增
      `extract_submitted_land_use_ratio_fields(case)`（讀取
      `case.base_parcel_factors`中`individual_zoning_designation`／
      `individual_building_coverage_ratio`／`individual_floor_area_ratio`
      三個既有field_id——與`docs/phase2/form_mapping.md`／
      `data/golden/golden_case_input.py`同一convention，非新發明）與
      `extract_plan_identification(factors_record)`（讀取
      `collect_data.py`新增持久化之`plan_identification`）；`review.py`
      呼叫兩者填入`SubmittedFormData`。**未hardcode**`jinshan`/70/240——
      這些字面值只出現在測試檔案本身
- [x] **plan_id持久化修正**：`collect_data.py`先前將`plan_id`計算進
      `ProviderContext`後，該請求一結束就遺失（下一次獨立的`review()`
      請求讀不到）。本輪新增`factors_record["plan_identification"]`
      持久化該值，`plan_identification_source`於有`plan_id`時記為
      `"MANUAL_INPUT"`（呼應`_resolve_plan_id`docstring既有事實：plan_id
      恆為人工提供）
- [x] **DynamoDB round-trip型別修正（本輪新發現之真實bug，非既有回歸）**：
      `case_store`透過boto3 Table resource讀回的數字一律是`Decimal`，
      `FactorInput.raw_value`（`Union[float, int, ...]`，float排序在前）
      會將其強制轉為`float`；`AuditIssue.submitted_value`／
      `expected_value`一旦帶有原生`float`，`case_store.put_record`
      （`REVIEW_RESULT`）會被boto3拒絕（`Float types are not supported`）。
      此前從未被任何既有測試發現，因為過去沒有任何check會把
      `case.base_parcel_factors`的raw_value直接回顯進`AuditIssue`欄位。
      修正：`extract_submitted_land_use_ratio_fields`將Decimal/float正規化
      為字串（整數值輸出`"70"`而非`"70.0"`）；`LandUseRatioValidator.
      _compare()`原本用`float(resolved_value_pct)`（與本專案其餘validator
      一致改為`str(...)`，例如`CalculationValidator`測試中`expected_value
      == "1"`），兩處皆已補上regression test（`test_land_use_ratio_
      validator.py`舊斷言70.0/240.0/210.0同步改為字串；backend E2E新增
      `test_golden_payload_flows_through_to_passed_building_coverage_and_far`
      等5項）
- [x] **後端E2E新增5項**（`tests/test_backend_handlers_e2e.py::
      TestLandUseRatioBackendE2E`）：plan_identification持久化驗證、
      Golden 70/240→PASS、Error 60/300→INCONSISTENT、無plan_id→
      ZONING_PLAN_UNRESOLVED（非誤標）
- [x] **中文Evidence編碼驗證**：實際以`repr()`／寫入UTF-8檔案（繞過
      Windows終端機codepage）直接比對，確認`official_raw_zone_name ==
      "第二種商業區"`、`normalized_zone_category == "商業區"`、
      `confirmed_plan_name == "金山都市計畫"`皆為`True`；console輸出中
      看到的`�...`亂碼**純屬Git Bash終端機codepage顯示問題，Domain/JSON/
      API層級資料完全正常，未修改任何正式資料**。新增regression test
      `test_evidence_chinese_text_is_not_mojibake`鎖定：直接字串比對＋
      `json.dumps(..., ensure_ascii=False)`往返後仍含正確中文、不含
      U+FFFD替代字元
- [x] **regional_base_factors後端持久化仍是已知缺口（非本輪範圍，誠實記錄）**：
      `collect_data.py`目前仍未把Provider（`RealLandUseProvider`等）取得
      的區域因素點位持久化進`factors_record["regional_base_factors"]`／
      `["regional_comparable_factors"]`——這是本輪之前就存在的缺口（見
      `tests/test_backend_handlers_e2e.py`既有測試皆手動於`_seed_full_case`
      中補入這兩個key即為證據），因此本輪新增的backend E2E測試比照既有
      模式手動seed`regional_land_use_zone`。真實案件在這個缺口修好前，
      `official_raw_zone_name`在backend路徑下會恆為None，
      建蔽率/容積率檢查會誠實回報`LAND_USE_RATIO_RULE_UNAVAILABLE`（不會
      誤報，但也還無法真正核對）——已記入`docs/backlog.md`
- [x] **pytest環境警告已定位、非intermittent、但非fatal**：
      `tests/test_phase5_golden_pipeline.py`匯入`pdf/pdf_renderer.py`→
      `weasyprint`時，連續5次獨立重跑（另加完整`pytest -q`第6次）皆
      **穩定重現**一則`Windows fatal exception: access violation`
      traceback，指向`cffi.api._load_backend_lib`→`weasyprint/text/
      ffi.py`的`dlopen`探測階段。**非本專案程式碼問題**（純第三方原生
      函式庫載入路徑），且**每次exit code皆為0、測試結果皆為399 passed
      / 0 failed / 0 skipped，不影響任何測試判定**。因為它是穩定重現、
      有明確定位、且非fatal，**不歸類為INTERMITTENT_ENVIRONMENT_ISSUE**
      （後者僅適用於「無法重現」的情況），也**不宣稱CLEAN PASS**——如實
      記錄為「PASS附一項已知非致命原生函式庫警告」。環境：Python
      3.13.2、weasyprint 69.0、cffi 2.1.1、Windows-11-10.0.26200-SP0。
      本輪未更換PDF renderer或重構（依指示）
- [x] 完整regression：**399 passed / 0 failed / 0 skipped**（含上一輪
      394項＋本輪新增5項）；`--collect-only`同樣回報399項，數字一致

## LandUseRatioValidator↔AuditEngine正式串接（2026-09-02，本輪）

- [x] **SubmittedFormData擴充**（向後相容，全數optional，預設None）：
      `submitted_land_use_zone`／`submitted_building_coverage_rate`／
      `submitted_floor_area_ratio`／`confirmed_plan_name`／
      `internal_plan_id`／`plan_identification_source`，見
      `engine/audit_engine.py`
- [x] **正式串接**：`AuditEngine.review()`現在會在每個case-level（非
      per-comparable，因建蔽率/容積率是表1欄位而非表4/表5-2逐筆比較標的
      欄位）呼叫`LandUseRatioValidator.validate_building_coverage_rate`／
      `validate_floor_area_ratio`。official端（比對基準）之分區名稱取自
      `base_regional_factors`之`regional_land_use_zone`（獨立於申報表單
      的真實資料），而非申報表單自身——避免申報表單同時謊報分區與比率時
      自我一致而漏檢
- [x] **Golden Case（金山、第二種商業區、建蔽率70%、容積率240%）**：
      PASSED/PASSED，`tests/test_smart_review.py::
      TestLandUseRatioWiringInAuditEngine::test_golden_case_70_240_passes_
      both_checks`
- [x] **Error Case（建蔽率60%、容積率300%）**：自動產生
      `BUILDING_COVERAGE_RATE_INCONSISTENT`／`FLOOR_AREA_RATIO_
      INCONSISTENT`（issue_type=INCONSISTENT），見
      `test_error_case_60_300_detected_as_inconsistent`
- [x] **不確定情況不誤報**：無`internal_plan_id`時回報
      `ZONING_PLAN_UNRESOLVED`（issue_type=WARNING），絕不誤標為
      `FLOOR_AREA_RATIO_INCONSISTENT`；`internal_plan_id`指向未登記該
      分區規則之都市計畫時回報`LAND_USE_RATIO_RULE_UNAVAILABLE`
      （issue_type=MISSING），不標為填錯或違規
- [x] **Evidence完整保存**：`LandUseRatioValidator`每個已解析分支（含
      MISSING/WARNING分支）現在會將submitted_value／official_raw_zone_
      name／normalized_zone_category／confirmed_plan_name／
      internal_plan_id／plan_identification_source／resolution_layer／
      legal_source／article_or_section／source_url／source_document_
      checksum／dataset_version／requires_manual_review完整寫入
      `explanation_data.computed_steps`（先前僅有裸的resolved_value_pct
      進入比對，其餘來源鏈資訊被捨棄，本輪修正）
- [x] 新增6項測試（`TestLandUseRatioWiringInAuditEngine`），另有既有
      Demo Error Case三項（regional grade/adjustment/cross-form）與
      cross-form/calculation/comparable selection既有測試全數維持通過
      （regression不受影響）；完整套件本地重新確認：**394 passed / 0
      failed / 0 skipped**（見下方「測試總數」一節）

## 建蔽率／容積率主流程串接（2026-09-02）

- [x] **分區名稱正規化不混用**：`engine/zone_name_normalizer.py`區分
      `official_raw_zone_name`（NtpcZoningProvider官方原值，如「第二種
      商業區」，永不被改寫）與`normalized_zone_category`（僅供Layer 1
      查詢用，如「商業區」），並保存`normalization_method`
      （EXACT_MATCH／SUBGRADE_PREFIX_STRIP／UNCLASSIFIABLE）與
      `normalization_confidence`。無法可靠歸類者（如公共設施用地「道路
      用地」「綠地」，屬附表三而非本輪已digitize之附表一）明確回報
      UNCLASSIFIABLE/UNKNOWN，不猜測
- [x] **查證新北市都市計畫範圍圖資，確認plan_name無法自動取得**：實際
      下載`新北市都市計畫範圍.zip`（NTPC官方open data）並檢查SHP schema
      ——確實存在`Name`/`LblName`欄位，但**逐byte核對後確認該欄位資料在
      來源端已損毀**（原始bytes內含字面上的U+FFFD替代字元序列，非本系統
      解碼參數猜錯；big5/utf-8/cp950三種編碼嘗試結果一致證實非編碼問題），
      `Url`欄位亦50筆記錄全數空白、無替代連結，資料集無CSV/GeoJSON等替代
      格式。依指示**停止自動推定**，`plan_id`改為必須由外部（人工）提供
      （`ProviderContext.plan_id`／request body `plan_id`），永不由座標
      或行政區名稱自動假設，此限制已寫入`providers/base.py`
      `ProviderContext`docstring
- [x] **RealLandUseProvider正式串接**（`DATA_PROVIDER_MODE=real`）：
      `land_use_zone`→NtpcZoningProvider（既有）；`building_coverage_
      ratio`→normalized_zone_category→LandUseRatioEngine Layer 1；
      `floor_area_ratio`→official_raw_zone_name+plan_id→Layer 2→
      （無plan_id或該計畫未登記時）Layer 1 fallback→UNKNOWN。查不到時
      一律`UNKNOWN`/confidence=`UNKNOWN`，絕不fallback Mock（已用測試
      證明：Mock模式容積率固定回傳240，real模式無plan_id時明確為None，
      兩者不會混淆）
- [x] **交叉驗證**：`engine/land_use_ratio_validator.py`
      `LandUseRatioValidator`比對申報值 vs 官方/參考值，新增5個
      `CheckType`：`BUILDING_COVERAGE_RATE_INCONSISTENT`／
      `FLOOR_AREA_RATIO_INCONSISTENT`（issue_type=INCONSISTENT，僅在
      真正查到官方值且與申報值不符時觸發）／`ZONING_PLAN_UNRESOLVED`／
      `ZONE_CATEGORY_NORMALIZATION_UNCERTAIN`／`LAND_USE_RATIO_RULE_
      UNAVAILABLE`（三者issue_type=WARNING或MISSING，明確測試證明「查無
      規則」絕不會被誤標為INCONSISTENT）。
      > **⚠️已過時/superseded（2026-09-02同日晚些時候）**：下一句原寫
      > 「本輪僅建立獨立可測試之validator……尚未接入AuditEngine.review()
      > 之自動per-case流程」，此限制已解除——已正式串接，見本文件最上方
      > 「LandUseRatioValidator↔AuditEngine正式串接」一節。保留原文字
      > 僅為維持歷史紀錄，不代表目前現況。
- [x] **Evidence完整保存**：`LandUseRatioResolutionResult`新增
      `official_plan_name`／`legal_source`／`article_or_section`／
      `source_url`／`source_document_checksum`／`dataset_version`欄位
      （原僅有notes自由文字），查無資料時上述欄位保持None、不捏造引註
- [x] 33項新增測試（10項zone_name_normalizer新檔+12項land_use_ratio_
      validator新檔+8項land_use_provider新場景（原9項增至17項）+3項
      LandUseRatioResolutionResult新增evidence欄位測試），涵蓋指示中列
      出的全部6項情境：第二種商業區分類正確且raw value不被改寫、金山+
      第二種商業區取得70%/240%、另一都市計畫不得取得金山240%、無
      plan_id時不得偷用240%、查無規則不得fallback Mock、申報值不一致
      產生對應Issue；Golden Case端到端測試維持通過
- [x] 完整regression tests：355（前一輪基準）+33＝388 passed / 0 failed
      （**當時**基準；同日稍晚LandUseRatioValidator↔AuditEngine串接輪
      再+6＝394 passed / 0 failed / 0 skipped，見文件最上方一節與下方
      「388項pytest測試」處之更新註記）

## 建蔽率／容積率兩層規則庫（2026-09-02）

- [x] **查證發現**：容積率對住宅區/商業區等最常見分區**沒有全市統一固定值**
      ——都市計畫法新北市施行細則附表一本身明文「依實際發展，循都市計畫
      程序，於都市計畫書中訂定」，需逐都市計畫查證，不可泛化成單一規則庫
- [x] **Layer 1（`data/rules/ntpc_common_zone_ratios.json`）**：都市計畫
      法新北市施行細則附表一，19種土地使用分區×2項指標（建蔽率/容積率）
      共39筆，含旅館區容積率依山坡地/平地分列之特殊結構；來源PDF文字層
      可直接乾淨擷取（與央表gazette PDF不同，無字型編碼異常），來源URL已
      重新下載一次確認SHA-256一致
- [x] 法源：第三十六條「各土地使用分區之建蔽率不得超過附表一之規定」——
      附表一為**全市法定上限**，個別都市計畫書如有更嚴格規定則從其規定
      （第三十六條第二項），此但書已寫入每筆entry之`ceiling_override_note`
- [x] **Layer 2（`data/rules/plan_zone_floor_area_ratios.json`）**：查證
      Golden Case所在之金山都市計畫，確認「變更金山細部計畫（土地使用
      分區管制要點專案通盤檢討）書」（新北市政府，民國109年11月）明文
      「第二種商業區...容積率不得大於240%」，與Golden Case表1所載
      70%/240%完全吻合，已建立為**第一個完整支援之per-plan容積率案例**
      （`requires_manual_review=True`，因該文件為通盤檢討程序文件，未
      追蹤後續是否有再修正版本，於notes中誠實揭露）
- [x] **未泛化**：此240%僅登記於`plan_id="jinshan"`，未擴充至新北市其他
      都市計畫；查詢一個未登記的都市計畫（如虛構之`plan_id="banqiao"`）
      之「第二種商業區」時，明確回傳`UNAVAILABLE_PER_PLAN`，不會誤用
      金山的數值
- [x] `engine/land_use_ratio_engine.py`：`LandUseRatioEngine`實作查詢優先
      序（個別都市計畫→新北市共通規定→UNAVAILABLE），且對「多重數值」
      情境（如旅館區依地形分列）明確回傳AMBIGUOUS而非默默挑第一筆——此
      邊界案例由測試發現並修正（原實作會靜默回傳第一筆數值）
- [x] `PlanZoneFloorAreaRatioEntry`之pydantic驗證器強制`rule_status=
      CONFIRMED`必須帶齊完整引註欄位（文件名稱/日期/條號/URL/checksum），
      非CONFIRMED狀態則不得帶有具體數值
- [x] 23項測試，含Golden Case數值與engine解析結果一致性交叉驗證
- [x] **本輪範圍**：僅建立資料模型／規則庫／查詢引擎本身，**未**接入
      `RealLandUseProvider`（land_use_provider.py目前building_coverage_
      ratio/floor_area_ratio在real模式下仍固定UNKNOWN），接線為後續工作
      > **⚠️已過時/superseded（2026-09-02同日稍晚）**：`RealLandUseProvider`
      > 已於同日稍晚完成正式串接（見上方「建蔽率／容積率主流程串接」
      > 一節），real模式下不再固定UNKNOWN。保留原文字僅為維持歷史紀錄。
- [x] 未擴充至新北市其他都市計畫（依指示，僅完成金山一案）

## 中央「最大影響範圍」基準表數位化（Phase 1 REQ-025，2026-09-02）

- [x] 內政部104年1月30日台內地字第10413006723號令附表——「影響地價個別
      因素評價基準表」（1張，5種用地無子級別）與「影響地價區域因素評價
      基準表」（5張，住宅/商業用地各再分高度中度普通村里鄰4種子級別、
      工業用地分大規模中小規模2種子級別、農業/其他用地無子級別）——已
      完整數位化，共418筆儲存格，見`data/rules/central_max_adjustment_
      range.json`
- [x] 來源PDF內嵌字型無可用ToUnicode對照表，pypdf與PyMuPDF文字層擷取皆
      為亂碼（僅ASCII數字未受影響）；改以PyMuPDF將各頁面渲染為高解析度
      圖像，逐儲存格人工核對轉錄（`MANUAL_VISUAL_TRANSCRIPTION`），非OCR
- [x] 來源URL（`gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg021020/
      ch02/type2/gov10/num7/Eg.pdf`）已於本輪重新下載一次，SHA-256與
      byte size與原檔完全一致，確認引用來源穩定可重現
- [x] 官方符號「-」明確建模為`DASH_NOT_APPLICABLE`（該項不予考慮調整，
      非0%，依來源說明七）；「其他影響因素」列之真正空白儲存格（無數字
      亦無「-」）另建模為`BLANK_NO_DATA`，兩者與`VALUE`三態不得混淆，
      `CentralMaxRangeEntry`之pydantic驗證器強制`max_range_pct`之
      null/非null與`cell_state`一致
- [x] `engine/central_max_range_validator.py`：`CentralMaxRangeDataset
      Validator`檢查資料集本身結構完整性（land_use_type合法性、無重複
      儲存格、同一表格各子級別項目集合對稱），實際資料集0 issues；驗證器
      本身另以人工注入之錯誤資料證明確實會抓出問題（非空殼驗證器）
- [x] 地方`regional_rules.json`/`individual_rules.json`與中央表子級別
      之對應關係，因無官方依據，明確標記為`UNMAPPED`（`data/rules/
      central_max_range_local_mapping.json`），不自行猜測映射
- [x] **本輪範圍**：僅數位化＋建立資料模型與資料集結構驗證器。**尚未
      實作**「地方基準表 vs 中央上限」之交叉驗證邏輯（需先解決上述
      UNMAPPED），留待後續輪次，本輪未自行決定映射關係
- [x] 27項測試涵蓋schema驗證、跨表格/子級別數值抽查、cell_state
      nullability不變式、驗證器注入錯誤測試、UNMAPPED狀態測試

## 多比較標的處理（Phase 1 REQ-029：直接查證不動產估價技術規則母法規）

- [x] `engine/comparable_selection_engine.py`：§25單項調整率>15%排除檢核
      （含但書exception_note）、§26試算價格差距檢核（差距≥20%排除，公式
      (高-低)÷((高+低)/2)），皆為法規明文精確數字，非團隊自訂門檻
- [x] **【2026-09-02修正】** §25之30%「總調整率」門檻**非**明文精確數字可直接
      實作——最初版本誤將其自行定義為「先加總後取絕對值」並自動排除比較
      標的，經二次查證law.moj.gov.tw §25原文與作業手冊p.52-53確認：手冊
      唯一的「絕對值加總」公式明文僅適用於§27權重判斷，並非§25定義，§25
      條文本身未給出總調整率之計算方式。現改為僅計算兩種候選讀法供參考
      （`total_adjustment_signed_sum_pct`／`total_adjustment_abs_component_
      sum_pct`），`total_adjustment_legal_basis`固定標示
      `"LEGAL_BASIS_UNCONFIRMED"`，`excluded`不再受其影響（僅依15%單項
      門檻判定），但超過30%仍會觸發`MANUAL_REVIEW_REQUIRED`供人工判斷。
      詳見`docs/phase1/open_questions.md` B-5
- [x] **【小幅修正，同日】** `excluded=False`本身無法區分「已確認不需排除」
      與「僅30%總門檻公式未確認」兩種情況，新增三態欄位
      `exclusion_determination_status`（`EXCLUDED`/`NOT_EXCLUDED`/
      `UNDETERMINED`），`excluded`保留作向後相容（恆等於`status==EXCLUDED`）。
      `FormCompletionEngine`之`comparable_exclusion_check_{comparable_id}`
      欄位文字亦同步修正：UNDETERMINED時顯示「待確認」，不再顯示「無需
      排除」（此前措辭本身仍會誤導下游）
- [x] §27權重建議演算法（反比於調整率絕對值加總）明確標示為系統建議、
      非法規規定（`SuggestedWeight.requires_human_confirmation`恆為True），
      因§27原文查證確認官方無精確公式（僅「可信度」「相近程度」兩項質性
      考量），此為**直接fetch law.moj.gov.tw條文原文驗證**，非轉述AI摘要
- [x] 26項測試涵蓋§25/26/27（含30%門檻兩種候選讀法之divergence測試、
      LEGAL_BASIS_UNCONFIRMED標示測試），含「建議權重原樣接受可直接餵進
      `CalculationEngine.base_parcel_comparison_price()`不會因四捨五入
      誤差被拒絕」之端到端驗證
- [x] 順帶修正REQ-026原先「2件以上比較標的」之推測——母法規§27明文為
      「三件以上」
- [x] **已串接主流程**：`FormCompletionEngine`建構子新增可選的
      `comparable_selection_engine`參數（預設自動建立，向後相容既有3參數
      呼叫方式），`complete_form()`會自動產生`comparable_exclusion_check_
      {comparable_id}`（§25）、`trial_price_gap_check`（§26，≥2筆比較標的
      時才出現）、以及帶有系統建議值的`comparable_weight_{comparable_id}`
      （§27，`final_value`恆為`None`，僅在`calculation`文字中揭露建議值，
      需人工確認後另行填入`case.comparable_weight`才會被
      `base_parcel_comparison_price()`採用）三種欄位。`backend/handlers/
      complete_form.py`無需改動即自動取得此行為。17項新增測試見
      `tests/test_form_completion_comparable_selection_wiring.py`

## 決賽現場新規則接入（Phase 1 REQ-006：決賽案例與Golden Case不同）

- [x] `engine/rule_table_ingest.py` + `engine/rule_table_validator.py`：
      三個CSV（factors/bands/matrix）→ 驗證過的規則JSON，見
      `docs/phase8a/rule_table_ingestion_guide.md`
- [x] 驗證器對現有金山區`regional_rules.json`/`individual_rules.json`
      全面檢查，0 ERROR（`tests/test_rule_table_ingest.py`）
- [x] `scripts/ingest_rule_table.py` CLI，決賽現場可直接使用
- [ ] **尚未接上任何AI/OCR自動草擬CSV**——目前僅解決「人工填CSV→驗證→
      可用規則」這條保底路徑本身足夠快速可靠；AI輔助草擬CSV為前端加一段
      Adapter，屬後續加分項，非本輪範圍
- [x] 驗證器支援作業手冊p.48-49／p.52-53明文之全部5種分級制度（2/3/5/7/9級，
      含5級制底下3種不同用語版本）；不在7組已知用語內者為WARNING非ERROR，
      因9級以上用語本由地方政府自行決定，不可當成錯誤擋下合法資料

## 決賽現場真實資料來源（Phase 1 REQ-005：官方不提供GIS圖層，需自行取用公開資料）

- [x] `providers/osm_facility_lookup.py` + `providers/real_facility_
      provider_base.py`：即時查詢OpenStreetMap Overpass/Nominatim API，
      取代5個Mock Provider（transportation/public_facility/special_
      facility/environmental/commercial_activity），見
      `docs/phase8a/real_data_provider_guide.md`
- [x] 已用真實座標（板橋車站附近，非金山區）實測，查得真實設施名稱與
      距離（如「新民變電所」231.66M、「板信商業銀行」143.14M），證明
      系統能回答從未見過的地點，非僅複誦Golden Case
- [x] 查詢半徑對照`data/rules/regional_rules.json`各因素官方最遠級距
      設定，非隨意猜測數字
- [x] `DATA_PROVIDER_MODE`環境變數切換Mock/Real，預設Mock（測試套件全程
      使用Mock，不依賴網路）
- [x] `road_provider`誠實維持Mock（無可靠公開資料源），不假裝已解決；
      `land_use_provider`已有3個real欄位（land_use_zone/building_
      coverage_ratio/floor_area_ratio），其餘13個欄位仍誠實維持UNKNOWN
      （見下方「建蔽率／容積率主流程串接」）
- [x] REQ-010距離量測方法：一般設施（交通/公共建設/商業機構）用OSRM步行
      路網距離，特殊設施（殯葬/電業/廢棄物處理）用直線距離，依`data/rules/
      *.json`各因素`value_type`方向（distance_positive/negative）自動分派，
      已用真實座標實測（板橋車站附近查得步行距離845.3M/94.6M/868.7M，
      與直線距離明顯不同）。**本項目為本輪新發現並修復之落差**——先前所有
      Real Provider一律使用直線距離，包含應該用步行距離的一般設施
- [ ] **本開發環境已確認可連線overpass-api.de/nominatim.openstreetmap.org/
      router.project-osrm.org（皆HTTP 200），但決賽當天正式AWS Lambda環境
      之對外網路政策未知**，應列入AWS部署演練第一項要測試的項目

## 核心正確性（已於本地驗證）

- [x] Rule Engine：131+84項評價基準規則，Golden Case驗證通過
- [x] Grade/Adjustment Engine：矩陣方向以5組非平凡Golden Case數據獨立證明
- [x] Calculation Engine：完整精度保留，重現官方Golden Case值212,958
- [x] Form Completion Engine：Golden Case端到端測試通過
- [x] Smart Review（AuditEngine等5引擎）：3個Demo Error Case + 1個額外
      壓力測試案例，0偽陽性、0偽陰性
- [x] PDF Generation：weasyprint，繁體中文正確渲染，可開啟、頁數合理、
      文字可擷取（本地驗證，含qpdf結構完整性檢查）
- [x] （**⚠️已過時/superseded 2026-09-02**：本行原載388項，LandUseRatio
      Validator↔AuditEngine串接輪新增6項後，本地重新執行`pytest -q`／
      `--collect-only`確認為**394 passed / 0 failed / 0 skipped
      （394 collected）**，非用戶端某環境曾回報之372——後者無法在本專案
      目錄下重現，故不採用，以本專案目錄下實際執行結果為準）
      388項pytest測試，本輪重新執行仍全數通過（含19項規則表接入測試
      +40項真實資料來源測試（含REQ-010距離方法）+5項Provider模式切換測試
      +5項分級制度彈性測試+2項GeoDistanceEngine路網距離測試
      +26項多比較標的處理§25/26/27核心邏輯測試（含30%門檻兩種候選讀法
      divergence測試）+17項多比較標的處理主流程串接測試+16項DatasetRegistry
      測試+14項NTPC分區資料同步腳本測試+17項land_use_provider真實資料測試
      +27項中央最大影響範圍基準表數位化測試+23項建蔽率/容積率兩層規則庫測試
      +10項分區名稱正規化測試+12項建蔽率/容積率交叉驗證測試）

## 前端（已於本地/Mock Mode驗證）

- [x] 8個App Pages + index.html，Playwright headless瀏覽器實測可正確
      渲染真實資料
- [x] Mock Mode / Production Mode切換設計就緒
- [x] 既有Makerthon_test_1品牌/深色主題/Attribution連結保留
- [ ] **未於真實AWS URL驗證**（見`aws_runtime_acceptance_checklist.md`）

## 後端（已於本地/moto驗證）

- [x] 9個Lambda handler語法正確、可import、核心邏輯經moto模擬DynamoDB
      測試通過
- [ ] **未部署至真實AWS Lambda**
- [ ] **未取得真實API Gateway Invoke URL**

## AWS基礎設施（已完成IaC，未執行）

- [x] `infra/template.yaml`：19個資源，cfn-lint 0錯誤
- [x] `infra/statemachine/workflow.asl.json`：ASL語法有效，狀態參照完整
- [ ] **Step Functions從未實際建立或執行**
- [ ] **Bedrock從未實際呼叫**
- [ ] **AgentCore Knowledge Base未實作**（僅S3來源桶規劃）

## 安全性

- [x] 全repo程式化掃描：0筆Access Key/Secret/Token/Password
- [x] 無`.env`/`.pem`/`credentials`檔案
- [x] boto3使用預設credential chain，無硬編碼金鑰

## 文件完整性

- [x] Phase 1-7全部指定文件已產出
- [x] `docs/phase7/aws_deployment_runbook.md`：17步驟部署手冊
- [x] `docs/phase7/aws_runtime_acceptance_checklist.md`：部署後驗收清單
- [x] `docs/backlog.md`：已知LOW severity項目追蹤

## 決賽前仍需完成（依賴外部AWS環境）

1. 由具AWS權限之人員依`aws_deployment_runbook.md`實際部署
2. 完成`aws_runtime_acceptance_checklist.md`全部勾選項目
3. 以決賽當日實際提供之區段/評價基準明細表資料重新驗證系統
   （Phase 1已確認決賽案例與Golden Case不同，見REQ-006/007）
4. 若時間允許，實作AgentCore Knowledge Base（目前為OPTIONAL/PENDING）

## 明確聲明

本checklist所列「已完成」項目，指「程式碼/IaC本身邏輯正確且經本地/
Mock測試驗證通過」，**不代表**已在真實AWS環境中驗證。任何標示未勾選
之項目，在AWS Runtime Deployment（Phase 7B）完成前，皆不得於對外
簡報中宣稱為已完成。
