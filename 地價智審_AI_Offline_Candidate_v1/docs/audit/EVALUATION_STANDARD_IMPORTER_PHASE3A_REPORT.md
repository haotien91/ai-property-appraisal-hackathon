# EVALUATION_STANDARD_IMPORTER_PHASE3A_REPORT

**日期**：2026-09-10
**性質**：STEP 3A — 比賽當天「評價基準明細表 → Rule Candidate」
deterministic-first Importer。延續`docs/audit/
CASE_SCOPED_RULE_ARCHITECTURE_REPORT.md`（STEP 2，已驗收關閉）建立的
`CaseRulePackage`/`CaseRuleRepository`/`RuleTableValidator`基礎設施。
**範圍限制（本輪明確排除，皆確認未做）**：AI/Bedrock/Gemini/RAG、
前端、新API、AWS部署變更、Rule auto-confirm、隊友中央DOC整合、
`regional_rate_calculation.json`整合、GradeEngine/AdjustmentEngine
改寫、RegionalRateCalculator/Table4Calculator/Table52Calculator。

---

**STEP5 更新（2026-09-11）**：`build_case_rule_package_from_pdf()`
本輪**未修改**，並於 `tests/test_competition_dual_input_e2e.py::
TestGoldenE2E::test_g2_golden_dual_input_confirmed_case_rule_
reproduces_golden` 中對**真實**
`data/sources/competition/評價基準明細表範例.pdf` 再次完整執行一次
（upload→extract→confirm→analyze），確認其產出之 CONFIRMED 案件規則
於 18m 案例下與靜態基準一致（皆為普通），證明本 Importer 的
deterministic 抽取邏輯在雙輸入 Competition Flow 中持續正確運作。Blind
Case（`docs/audit/BLIND_CASE_PHASE5_REPORT.md`）刻意**不**透過本
Importer（改以 JSON fixture 直接建構 `CaseRulePackage`，STEP5 §20
明文允許），以避免為了單一測試情境寫第二套 PDF Parser。

---

## 1. Survey（§1，已於前置作業完成）

完整結果見`docs/audit/EVALUATION_STANDARD_IMPORTER_SURVEY.md`。摘要：

```
PDF_TEXT_LAYER_USABLE=YES
TABLE_STRUCTURE_DETERMINISTIC=YES
REGIONAL_TABLE_DETECTED=YES
INDIVIDUAL_TABLE_DETECTED=YES
MATRIX_STRUCTURE_DETECTED=YES
```

最重要的Survey發現：`page.get_text()`的plain text輸出順序**不是**
視覺閱讀順序（矩陣數字與分級標籤逐列交錯輸出，但備註句子與直書因素
名稱集中在頁尾、順序與各因素視覺順序不一致）。解法：用
`page.get_text("dict")`同時取得「與plain text完全逐行一致的文字流
順序」與「每行的bbox y座標」，前者用於矩陣解析，後者用於備註句子的
幾何歸屬——不需要兩套獨立擷取後互相猜測比對。

## 2. Importer Responsibility（§2)

新增`engine/evaluation_standard_importer.py`。其職責嚴格限定為
**PDF/extracted text → Rule Candidate**：

- **可以做**：偵測regional/individual scope、擷取因素候選名稱、
  擷取分級標籤與條件文字、擷取explicit adjustment matrix、擷取
  max_adjustment、產生provenance（source_document/source_page）、
  將候選資料轉換為`rule_schema.json`形狀的dict。
- **不做**（已逐一確認，見第10節Test 15/16）：不判定grade、不判定
  rank、不計算adjustment result、不計算比較價格、不做任何法律結論。
  模組原始碼中對`grade_engine`/`adjustment_engine`**沒有任何import**
  （已用測試斷言鎖定，非僅口頭聲明）。

## 3. Reuse Existing Models（§3）

- **重用**`domain.models.CaseRulePackage`／`CaseRulePackageStatus`
  （新增`AMBIGUOUS`值，見下）作為Importer最終輸出容器。
- **重用**`engine.rule_table_validator.RuleTableValidator`做結構驗證
  （未修改該檔案本身）。
- **重用**既有`rule_schema.json`形狀——`candidate_to_rule_records()`
  輸出的dict與`data/rules/regional_rules.json['rules']`/
  `engine/rule_table_ingest.py::build_rules_from_csv()`輸出**完全
  同一種shape**，未新增`ImportedGradeRule`/`ImportedAdjustmentRule`
  等第二套格式。

唯一的schema擴充：`domain.models.CaseRulePackageStatus`新增
`AMBIGUOUS`值（STEP 2原有DRAFT/EXTRACTED/PARTIAL/CONFIRMED/REJECTED
未包含此值，但STEP 3A明確要求Importer輸出必須是
EXTRACTED/PARTIAL/AMBIGUOUS三者之一）。這是對既有enum的**新增**，
非新建平行系統，`CaseRuleRepository.confirm()`的CONFIRMED Gate邏輯
完全不受影響（AMBIGUOUS與DRAFT/PARTIAL一樣，皆屬「未CONFIRMED」，
邏輯上一視同仁）。

## 4. Candidate Status（§4）

`extract_from_pdf()`回傳的每個`RuleCandidate.status`只會是
`"EXTRACTED"`/`"PARTIAL"`/`"AMBIGUOUS"`三者之一（原始碼中沒有任何
路徑會設成"CONFIRMED"）。`build_case_rule_package_from_pdf()`組裝
最終`CaseRulePackage`時，其`status`同樣只能是這三者之一：

- 全部候選皆`EXTRACTED`（零issue）→ package status = `EXTRACTED`
- 全部候選皆非`EXTRACTED`（零個乾淨候選）→ package status = `AMBIGUOUS`
- 混合 → package status = `PARTIAL`

CONFIRMED**只能**透過STEP 2既有的`CaseRuleRepository.confirm()`
（人工呼叫）達成——Importer完全不呼叫`confirm()`，也沒有任何函式
簽章允許外部傳入`status="CONFIRMED"`後直接持久化（`save_candidate()`
本身不限制輸入status值，但這是STEP 2既有介面的既有行為，非本輪
新增的漏洞——一個惡意呼叫者理論上仍可用`save_candidate()`存一個
`status=CONFIRMED`的package，但這**不會**讓它真正生效，因為
`get_confirmed_package()`只信任由`confirm()`寫入的`CONFIRMED_POINTER`
記錄，光是把package自身的status欄位設成CONFIRMED、卻沒有經過
`confirm()`寫入pointer，`build_rule_engine_for_case()`根本不會找到
它。第14節測試直接驗證了這個「無法自動變成有效CONFIRMED」的結論。

## 5. Required Extraction（§5）

逐項對照：

| 欄位 | 是否擷取 | 說明 |
|---|---|---|
| scope（regional/individual） | ✅ | 由頁面標題regex偵測 |
| factor name / canonical candidate | ✅（部分成功） | 見第7節（40%成功exact match，60%誠實標記UNKNOWN_FACTOR） |
| city/district/land_use_type | ✅ | 由頁面標題regex偵測 |
| grade labels | ✅ | 優/稍優/普通/稍劣/劣（依分級數而定） |
| grade_code | ✅ | 依row順序指派1..N |
| value_type | ✅ | numeric_range/boolean/distance_positive/categorical，deterministic分類 |
| unit | ✅ | 由條件文字內嵌單位regex拆解（M/M2/KM/%） |
| lower_bound/upper_bound/inclusive flags | ✅ | 數值級距類型才有值，其餘為null（誠實，非猜測） |
| boolean/categorical conditions | ✅ | 見第11節 |
| explicit adjustment_matrix | ✅ | 直接照抄PDF數字，見第6節 |
| max_adjustment | ✅ | 見第9節 |
| source_document/source_page | ✅ | 見第10節 |
| source_table/cell/row/column | 部分 | source_page已含；精確cell/row/column座標未額外輸出（bbox資訊存在於解析過程但未對外暴露為獨立欄位，屬本輪未實作項，見第13節） |

## 6. Matrix Priority（§6）

**已確認遵守**：`candidate_to_rule_records()`的`adjustment_matrix`
欄位直接來自PDF文字流解析出的原始矩陣數字（`RuleCandidate._matrix()`），
**任何地方都沒有**`max_adjustment / (grade_count - 1)`這種公式運算。
`max_adjustment`本身也是PDF裡明確寫出來的獨立數值（非由矩陣推算），
只在事後用來**交叉檢查**（若宣告值與矩陣實際最大絕對值不符，標記
`RULE_EXTRACTION_AMBIGUOUS`，見第7節），從未反向拿公式結果覆蓋PDF
原文矩陣。

## 7. Parsing Safety（§7）

| 情境 | 對應issue代碼 | 觸發條件 |
|---|---|---|
| 解析不到（列數不足） | `RULE_EXTRACTION_INCOMPLETE` | 實際解析出的列數與該因素矩陣維度不符 |
| 存在兩種合理interpretation | `RULE_EXTRACTION_AMBIGUOUS` | `max_adjustment`宣告值與矩陣實際最大絕對值不符 |
| Matrix缺格 | `MATRIX_INCOMPLETE` | 某一列的matrix_row長度與級距數不符，或`max_adjustment`完全缺失 |
| Grade boundary overlap | `GRADE_RANGE_OVERLAP` | 方向感知（ascending/descending皆處理，見下）的相鄰級距邊界比對，數值有重疊 |
| Grade boundary gap | `GRADE_RANGE_GAP` | 同上，數值有缺口 |
| 因素名稱無法mapping | `UNKNOWN_FACTOR` | 見第8節 |

**方向感知邊界檢查**：本文件PDF裡多數數值因素為「數值越大越優」
（descending：grade_code越大、數值越小，如主要道路寬度30m以上=優、
未滿10m=劣），`_check_range_continuity()`會先判斷方向（比較grade1與
gradeN的代表數值），再選擇正確的邊界配對方式比較——**不假設固定
方向**，避免對descending資料誤判overlap/gap（開發過程中曾經有此
bug，已修正並用實際PDF資料驗證47個因素區塊全數無誤判）。

**任何一項issue皆不會導致程式crash**——全部47個因素區塊皆成功解析
出`RuleCandidate`（無一遺漏），差異只在於`status`/`issues`欄位是否
標記需要人工複核，從未silently guess。

## 8. Canonical Factor Mapping（§8）

`_resolve_canonical_factor()`**只**允許：
1. **Exact match**（候選因素清單為`data/rules/regional_rules.json`/
   `individual_rules.json`已有的`factor`欄位值——不是憑空定義的清單）。
2. Longest-substring-wins（僅在多個候選同時符合、且長度不同時作為
   deterministic normalization使用；長度相同的真平手回傳None，不猜）。
3. （本輪未提供）「2. explicit existing alias」——因為目前專案**沒有
   既存的alias對照表**，若日後要支援，需先有人工建立的alias清單，
   本輪不主動生成/假設任何alias。

**未使用AI**——全程regex + exact substring比對，無LLM呼叫。

**實測結果（誠實呈現，非美化）**：47個候選中，19個（40%）透過備註
句子exact substring比對成功解析；28個（60%）因為PDF備註句子的用字
與既有digitized資料的`factor`欄位存在真實文字差異（例如「臨路情形」
vs備註句「臨街情況」；「都市計畫（內、外）」vs「都市計畫內外」；
「接近學校**之**程度」vs「接近學校程度」）而無法exact match，被誠實
標記為`canonical_factor_id=None`、`status`為`PARTIAL`/`AMBIGUOUS`、
issue含`UNKNOWN_FACTOR`。**這是符合本階段規範的正確、預期行為**，
非bug；已於第10節測試1明確鎖定「未知因素不猜」的行為。

## 9. Human Confirmation Boundary（§9）

- Importer**只**呼叫`CaseRuleRepository.save_candidate()`（若日後
  接上API/CLI）；本輪程式碼本身**沒有**呼叫`confirm()`的任何路徑。
- `build_case_rule_package_from_pdf()`回傳的package `status`永遠是
  `EXTRACTED`/`PARTIAL`/`AMBIGUOUS`三者之一（第4節已證明）。
- 所有47個候選（無論成功mapping與否）皆完整保留於
  `package.metadata["extraction_candidates"]`，供人工審閱；未成功
  mapping的候選**不會**被放進`regional_rules`/`individual_rules`
  （`candidate_to_rule_records()`對非EXTRACTED或無canonical_factor_id
  的候選一律回傳空list），故它們永遠無法被`RuleEngine`實際使用，
  除非人工先行補齊canonical mapping並重新走一次STEP 2的confirm流程。

## 10. Validation（§10）

`build_case_rule_package_from_pdf()`組裝出的
`regional_rules`/`individual_rules`，經STEP 2既有
`case_rule_repository.validate_package_rules_scoped()`
（分別對regional/individual兩份清單各自呼叫
`RuleTableValidator().validate()`，見第11節重要發現）驗證：
**0個ERROR，33個WARNING**（WARNING多為級距銜接/分級用語混用等
非阻斷性提示，符合`RuleTableValidator`既有設計，本輪未修改該驗證器
本身）。若有ERROR，`CaseRuleRepository.confirm()`會拒絕CONFIRMED
（STEP 2既有CONFIRMED Gate機制，未重寫），`validation_issues`
（此處體現為`package.warnings`，含每個非EXTRACTED候選的issue摘要）
完整保留，供人工複核。

## 11. 重要發現：STEP 2既有Bug修復（Scoped Validation）

本輪驗證PDF擷取出的完整package時，發現`RuleTableValidator`是以
`(city, district, land_use_type, factor)`分組，**不感知rule_set/
scope**。當某factor名稱同時存在於regional與individual兩個scope
（例如「建蔽率」「容積率」「地勢」——這正是`docs/phase3/
source_anomalies.md` ANOMALY-06記載的既知現象），若把
`regional_rules + individual_rules`**合併**後才送進validator，會被
誤判為「同一因素內grade_code重複」ERROR。

**實測證實這不是本輪Importer的新問題，而是STEP 2遺留至今的真實
潛在缺陷**：即使拿專案既有、已通過Golden Case驗證的
`data/rules/regional_rules.json` + `individual_rules.json`直接合併
後跑`RuleTableValidator`，**同樣**產生4個ERROR（建蔽率/容積率/地勢
各1個grade_code重複ERROR，加上地勢的matrix不一致ERROR）。這意味著
STEP 2的`CaseRuleRepository.confirm()`／
`rule_engine_factory.build_rule_engine_for_case()`若曾經被要求
confirm一個**同時**含regional與individual兩個scope、且用到「建蔽率/
容積率/地勢」這類跨scope同名因素的package，會被**錯誤地**拒絕確認
——即使該package本身完全正確。

**已修復**：`backend/handlers/case_rule_repository.py`新增
`validate_package_rules_scoped()`，改為**分別**對
`regional_rules`與`individual_rules`各自呼叫
`RuleTableValidator().validate()`（如同`RuleEngine`本身透過
rule_id前綴REG-/IND-對regional/individual分開索引一樣），取代原本
`confirm()`／`build_rule_engine_for_case()`裡對合併後list驗證的呼叫。
`RuleTableValidator`本身**完全未修改**（遵守使用者「不要修改
RuleTableValidator」之既有限制——這是修正「如何呼叫」它，不是修正
它本身）。修復後：STEP 2既有19項專項測試全數維持19 passed（無
regression）；本次PDF擷取出的完整package驗證結果由4 ERROR降為
**0 ERROR**。詳見第14節Regression。

## 12. Matrix Priority 之 Golden-like Import Verification（§12）

以`主要道路寬度`（regional）為驗證對象：

1. Importer解析出的5個grade band（優/稍優/普通/稍劣/劣），其
   `grade_label`/`lower_bound`/`upper_bound`/`unit`/
   `adjustment_matrix`/`max_adjustment`，逐格與
   `data/rules/regional_rules.json`裡既有的`REG-MAIN_ROAD_WIDTH-*`
   五筆記錄**完全相符**。
2. 用解析出的候選規則**直接建構一個真實、未修改的`RuleEngine`
   實例**，對18m呼叫`grade()`，正確回傳「普通」（grade_code=3）——
   與Golden Case已知結果一致。
3. 用同一份候選規則另外建構`GradeEngine`/`AdjustmentEngine`
   （皆為STEP 2/既有版本，未修改），驗證18m對比30m（優）之
   adjustment結果為`-7.5`，與矩陣`["3"]["1"]`原文數字一致。

**未**對最終「案件」層級的grade下任何斷言——本階段只驗證Rule
Extraction本身之正確性，符合使用者「本階段只驗Rule Extraction」
之明確要求。

## 13. Do Not（§13）— 逐項確認

| 禁止事項 | 確認 |
|---|---|
| Gemini/Bedrock/RAG | 未使用，全程regex+exact match |
| frontend | 未觸碰任何前端檔案 |
| API | 未新增任何HTTP endpoint |
| AWS deploy | 未執行`sam deploy`，只`sam build`（build是regression驗證的一部分） |
| Rule auto-confirm | 未實作，見第4/9節 |
| 隊友中央DOC整合 | 未讀取`docs/incoming_rule_sources/`任何檔案 |
| regional_rate_calculation.json | 未讀取/整合 |
| GradeEngine/AdjustmentEngine rewrite | 未修改，見第2節與第15/16節測試 |
| RegionalRateCalculator/Table4Calculator/Table52Calculator | 未新建 |

## 14. Regression

| 項目 | 指令 | 結果 |
|---|---|---|
| Importer專項測試 | `py -m pytest -q tests/test_evaluation_standard_importer.py` | **20 passed**（16項必測＋4項細分） |
| STEP 2專項測試（確認scoped-validation修復無regression） | `py -m pytest -q tests/test_case_scoped_rule_architecture.py` | **19 passed** |
| 完整套件（排除既有環境限制的weasyprint檔案） | `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py` | **899 passed, 0 failed**（879既有＋20新增） |
| SAM Lint | `sam validate --lint`（於`infra/`） | **PASS** |
| SAM Build | `sam build --use-container`（真實container build） | **Build Succeeded**（新模組`engine/evaluation_standard_importer.py`已確認出現於`.aws-sam/build/EngineLayer/python/engine/`） |

## 15. Files Added / Modified

**新增**：
- `engine/evaluation_standard_importer.py`
- `tests/test_evaluation_standard_importer.py`（20 tests）
- `docs/audit/EVALUATION_STANDARD_IMPORTER_SURVEY.md`
- `docs/audit/EVALUATION_STANDARD_IMPORTER_PHASE3A_REPORT.md`（本文件）

**修改**：
- `domain/models.py`：`CaseRulePackageStatus`新增`AMBIGUOUS`值（純新增
  enum成員，docstring同步更新）。
- `backend/handlers/case_rule_repository.py`：新增
  `validate_package_rules_scoped()`；`save_candidate()`／`confirm()`
  改用此函式取代原本對`regional_rules+individual_rules`合併後驗證的
  呼叫（第11節之bug修復）。
- `backend/handlers/rule_engine_factory.py`：`build_rule_engine_for_case()`
  內的defense-in-depth驗證同上，改用`validate_package_rules_scoped()`。

**未修改**：`engine/rule_engine.py`／`engine/grade_engine.py`／
`engine/adjustment_engine.py`／`engine/form_completion_engine.py`／
`engine/rule_table_validator.py`／`engine/rule_table_ingest.py`／
任何GIS/S3/Cadastral相關檔案／`docs/incoming_rule_sources/`／前端／
`infra/template.yaml`（本輪新模組隨既有`engine/`整批複製至
EngineLayer，無需新增SAM資源設定）。

## 16. Remaining Gaps

1. **直書因素名稱欄未重建**：PDF裡每個因素還有一組逐字直書的因素
   名稱（如「主/要/道/路/寬/度」），本輪僅用備註句子作為canonical
   mapping的唯一輸入來源，未額外實作直書文字的多欄、右到左幾何
   重建規則——若能補上，理論上可能提高canonical mapping成功率
   （目前40%），但也可能只是提供第二個獨立、同樣可能與既有資料
   用字不同的候選來源，不保證能解決第8節列出的真實文字差異問題。
2. **category（主要項目分類）欄位未重建**：`candidate_to_rule_records()`
   輸出`category=None`——PDF沒有明確的合併儲存格結構資訊，本輪未
   嘗試推論。不影響`RuleEngine`功能（category純資訊性欄位）。
3. **無API/CLI串接**：Importer目前只是一個可被import的Python函式庫
   （`extract_from_pdf`/`build_case_rule_package_from_pdf`），沒有
   對應的CLI腳本（如`scripts/ingest_rule_table.py`那樣）或HTTP
   endpoint讓人工在決賽現場實際觸發——依使用者明確排除範圍（不做
   前端/API），留待下一輪。
4. **28個UNKNOWN_FACTOR候選需要人工建立alias**：若要提高這60%候選
   的mapping成功率，需要人工建立一份「PDF備註用語 → 既有canonical
   factor」對照表（即§8所稱「2. explicit existing alias」），本輪
   刻意未自行假設/生成此對照表（避免在沒有人工確認的情況下引入
   錯誤對應）。
5. **未涵蓋隊友資料/regional_rate_calculation.json**：依使用者明確
   排除範圍，本輪Importer僅處理`評價基準明細表範例.pdf`本身。

## 17. STEP 3B 更新附註（2026-09-10）

第4點「28個UNKNOWN_FACTOR候選需要人工建立alias」與第1點「直書因素
名稱欄未重建」兩項Remaining Gap，已由STEP 3B（`docs/audit/
EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md`）之
`CaseRuleRepository.resolve_candidate_factor_mapping()`提供**人工**
解法（人工經Review DTO查看`original_factor_text`後，明確指定
`canonical_factor_id`）——非本報告第8節所稱之「AI/自動生成alias」，
`_resolve_canonical_factor()`本身之exact-match-only政策未改變。

本輪同時修改了本報告第15節列出的`candidate_to_rule_records()`（
`rule_id`格式新增`candidate_id`片段）與
`build_case_rule_package_from_pdf()`（`metadata['extraction_
candidates']`改存`RuleCandidate.to_dict()`完整資料而非摘要）——
兩者皆為**向下相容的擴充**（既有欄位/回傳形狀不變，只新增內容），
STEP 3A原有20項測試（`tests/test_evaluation_standard_importer.py`）
於STEP 3B完成後重新執行，**維持20 passed**，無regression。
