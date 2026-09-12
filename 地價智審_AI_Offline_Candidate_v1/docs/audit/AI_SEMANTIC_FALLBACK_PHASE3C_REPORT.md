# AI_SEMANTIC_FALLBACK_PHASE3C_REPORT

**日期**：2026-09-10
**性質**：STEP 3C — 只有deterministic importer無法可靠解析的項目，才使用
AI產生semantic candidate。延續STEP 2/3A/3B（`CaseRulePackage`/
`CaseRuleRepository`/Importer/Human Confirmation）既有基礎，本輪
**不修改**任何STEP 2/3A/3B既有邏輯本身，只新增一層**永遠advisory**的
AI輔助。

---

**STEP5 更新（2026-09-11）**：本輪**未修改**本報告所述任何模組
（`SemanticRuleMappingProvider`／`MockSemanticRuleMappingProvider`／
`BedrockSemanticRuleMappingProvider`）。STEP5 §27 重申並再次確認 AI
邊界：Blind Case（`docs/audit/BLIND_CASE_PHASE5_REPORT.md`）全程未
呼叫本模組任何元件（直接以 JSON fixture 構造規則，繞過 AI Semantic
Fallback），Golden dual-input 測試（G2）雖走過真實 PDF Importer，但
其 44 個 deterministic-matched 因素已足以涵蓋測試所需的
主要道路寬度／面前道路寬度，故也未觸發本模組。`AI_DIRECT_GRADE_
FOUND=NO`／`AI_DIRECT_RATE_FOUND=NO`／`AI_DIRECT_PRICE_FOUND=NO`
於 STEP5 最終輸出區塊中再次確認為 NO。

## 1. Trigger Conditions

`CaseRuleRepository.propose_ai_candidates()`（`backend/handlers/
case_rule_repository.py`，本輪新增，STEP 2/3B唯一Repository實作類別上
新增的方法）是**唯一**可能呼叫`SemanticRuleMappingProvider`的地方。
觸發條件：迭代`package.metadata['extraction_candidates']`（STEP 3A
Importer之完整候選snapshot），對`status != "EXTRACTED"`的候選才呼叫
provider——結構上以`if c.get("status") == "EXTRACTED": continue`直接
跳過，非依賴慣例。由於STEP 3A的`_validate_candidate()`已把
UNKNOWN_FACTOR／RULE_EXTRACTION_AMBIGUOUS／RULE_EXTRACTION_INCOMPLETE／
MATRIX_INCOMPLETE／GRADE_RANGE_OVERLAP／GRADE_RANGE_GAP等**任何**
deterministic失敗都會導致`status`變成PARTIAL或AMBIGUOUS（絕不會是
EXTRACTED），此單一狀態檢查即涵蓋使用者列出的全部觸發條件（實測第9節
TEST 3特別針對`RULE_EXTRACTION_AMBIGUOUS`單獨驗證，因為真實PDF擷取
出的47個候選中未自然出現此issue，故以合成資料驗證觸發邏輯本身正確，
而非僅依賴UNKNOWN_FACTOR剛好涵蓋所有案例）。

**Deterministic已成功解析的Rule不得再送AI重判**：實測（TEST 1）用一個
會記錄每次被呼叫之`candidate_id`的test double包裝
`MockSemanticRuleMappingProvider`，對真實PDF擷取結果（19個EXTRACTED、
28個非EXTRACTED）呼叫`propose_ai_candidates()`後，確認19個EXTRACTED
候選的`candidate_id`集合與provider實際被呼叫過的集合**完全不相交**。

## 2. AI Output Contract

新增`domain.models.SemanticRuleMappingCandidate`（`extra="forbid"`），
**結構上**只有以下欄位存在，沒有其他：

```
candidate_factor_id, candidate_condition_id, normalized_condition_candidate,
candidate_unit, candidate_value_type, confidence, reason
```

不存在`final_grade`/`final_rank`/`final_adjustment_rate`/`final_price`
這類欄位——不是「驗證時拒絕」，是這個Pydantic model**根本沒有這個欄位
可以賦值**，即使上游驗證邏輯有漏洞，也沒有地方可以把一個最終決策值
硬塞進這個model裡。

## 3. Structured Output

新增`providers/semantic_rule_mapping_provider.py::
validate_ai_response_schema(raw) -> (status, error_message)`，純函式，
strict驗證：

- `raw`非dict → `SCHEMA_INVALID`
- 缺少必要欄位（`confidence`、`reason`）→ `SCHEMA_INVALID`
- 出現任何`_ALLOWED_KEYS`以外的未預期欄位 → `SCHEMA_INVALID`
- `confidence`非`HIGH`/`MEDIUM`/`LOW`之一 → `SCHEMA_INVALID`

第10節（測試對照表）逐項驗證：JSON非dict／缺必要欄位／未預期欄位／
confidence值不合法，皆被拒絕。

## 4. Confidence Threshold

`confidence`欄位定義為`HIGH`/`MEDIUM`/`LOW`三值字串（非獨立enum
class，因為它只是`SemanticRuleMappingCandidate.confidence: str`欄位，
由`validate_ai_response_schema()`把關合法值——與其他domain model用
`str, Enum`的慣例略有不同，是因為這裡的三值純粹是「人工複核優先序」
標籤，不驅動任何程式分支邏輯，用plain str field＋驗證函式已足夠，
未過度設計成獨立enum）。

**confidence只影響human review priority，不能讓HIGH confidence自動
CONFIRMED**——`propose_ai_candidates()`**完全不**碰
`regional_rules`/`individual_rules`／`status`欄位，無論AI回傳
confidence為何。實測（TEST 4）用一個永遠回傳`confidence="HIGH"`的
test double跑過`propose_ai_candidates()`後，確認package的
`regional_rules`/`individual_rules`與呼叫前逐字節相同、`status`仍非
CONFIRMED。**所有AI candidate都必須經Human Confirmation**——AI候選
只會出現在`package.metadata['ai_semantic_candidates']`，要讓它真正
影響`regional_rules`/`individual_rules`，唯一路徑是人工呼叫STEP 3B
既有的`resolve_candidate_factor_mapping()`/`submit_human_edits()`（
本輪**未新增**任何「接受AI建議」的獨立API，刻意重用同一套已有
re-validate機制，避免出現繞過confirm流程的捷徑）。

## 5. Provenance

新增`domain.models.SemanticRuleMappingResult`，每筆AI candidate結果
完整保存：

```
candidate_id, original_text, deterministic_failure_reason,
provider, model_id, prompt_version, status, candidate, error_message,
created_at
```

實測（TEST 11）逐欄確認全部存在。Human Confirmation後：
`package.metadata['ai_semantic_candidates']`**維持不變**（原始AI
candidate完整保留，不因人工後續編輯而被覆寫或刪除）；人工的決定
（包括是否採用AI建議、或選了完全不同的值）另外記錄在STEP 3B既有的
`package.edit_history`（`RuleFieldEdit`，含`confirmed_by`/
`confirmed_at`）——兩份紀錄各自獨立、互不覆蓋。實測（TEST 12）：
人工刻意選了與AI建議**不同**的`canonical_factor_id`後，確認
`ai_semantic_candidates`裡的原始AI建議與人工編輯前逐字節相同
（`ai_after == ai_before`），且`edit_history`正確記錄人工最終選擇的
值。

## 6. AI Boundary Guard

`validate_ai_response_schema()`同時扮演AI Boundary Guard：任何回應
若含`grade`/`final_grade`/`grade_code`/`final_grade_code`/`rank`/
`final_rank`/`adjustment_rate`/`final_adjustment_rate`/
`adjustment_pct`/`final_adjustment_pct`/`price`/`final_price`/
`comparison_price`/`base_parcel_comparison_price`/`legal_conclusion`/
`final_legal_conclusion`/`legal_basis`等禁止欄位，直接標記
`status="SCOPE_VIOLATION"`並**不**建構`SemanticRuleMappingCandidate`
（`candidate=None`）——此檢查在必要欄位檢查**之前**執行，確保「同時
缺必要欄位又夾帶禁止欄位」的回應仍被判定為更嚴重的SCOPE_VIOLATION，
不會被較輕的SCHEMA_INVALID掩蓋（第10節TEST）。實測End-to-end（TEST 6
延伸）：用一個會回傳夾帶`grade`欄位的test double跑過完整
`propose_ai_candidates()`流程，確認最終存入
`ai_semantic_candidates`的每一筆結果皆為`SCOPE_VIOLATION`、
`candidate=None`——不會有任何違規內容漏網進入正式資料。

## 7. Provider Abstraction

新增`providers/semantic_rule_mapping_provider.py`（依循本專案既有
Provider Pattern，`providers/base.py`/`providers/document_extraction_
provider.py`之慣例）：

- `SemanticRuleMappingProvider`：純介面。
- `MockSemanticRuleMappingProvider`：**`status = "MOCK_ONLY"`**。
  以字元集合Jaccard相似度對`known_factor_names`評分，deterministic、
  全離線、可重現（相同輸入永遠得到相同輸出，實測TEST 17），刻意
  簡陋（不宣稱真實語意理解），純粹用於讓本輪整合邏輯（觸發條件、
  schema驗證、provenance、confidence處理、人工複核銜接）可離線測試。
- `BedrockSemanticRuleMappingProvider`：**`status = "CODE_READY"`**，
  **非**`AWS_RUNTIME_VERIFIED`。`propose_candidate()`本身直接
  `raise NotImplementedError`並附上完整說明——**完全比照**本專案
  既有`providers/document_extraction_provider.py::
  TextractExtractionProvider`的既定模式（該class同樣是「CODE_READY
  interface，未曾在此環境對真實AWS執行過，classify()/extract_fields()
  直接raise」），而非嘗試一個從未驗證過的即時呼叫、失敗了才回退。
  `_build_prompt()`/`_parse_bedrock_response()`兩個**純函式**（無
  boto3呼叫）已獨立單元測試（TEST 17延伸）：prompt內容含明確的
  邊界指示（「絕對不可輸出grade/rank/adjustment_rate/price/
  legal_conclusion」）與`PROMPT_VERSION`；response解析對Bedrock
  Messages API的`{"content":[{"type":"text","text":"<json>"}]}`
  信封格式正確解出內層JSON，對非法JSON明確`raise ValueError`
  （不會回傳猜測值）。`model_id`預設值
  `anthropic.claude-3-5-sonnet-20241022-v2:0`與本專案既有
  `backend/handlers/explanation.py`（Part F既有Bedrock整合）完全
  一致，非引入第二個AI供應商慣例。**未綁死Gemini**——介面與兩個
  實作皆為Bedrock/Mock，程式碼中無任何Gemini相關內容。

## 8. No AI Fallback in Runtime Calculation

`engine/grade_engine.py`／`engine/adjustment_engine.py`／
`engine/calculation_engine.py`**三個檔案本輪完全未修改**（皆為
zero diff），且以測試鎖定「未來也不能被靜默改動成會呼叫AI」——
TEST 14/15/16以grep比對三個檔案的import陳述式，確認皆不含
`semantic_rule_mapping`或`bedrock`關鍵字。AI只存在於
`CaseRuleRepository.propose_ai_candidates()`（Importer/Rule Mapping
layer），與Grade/Adjustment/Calculation三個正式計算引擎完全隔離。

## 9. No Real→Mock Silent Fallback

依循`provider-contract` skill之核心原則（「Real Path Must Never
Silently Fall Back to Mock」）：

- `BedrockSemanticRuleMappingProvider.propose_candidate()`直接
  `raise NotImplementedError`（非回傳一個「看起來正常」的結果）。
  實測（TEST 18）：`repository.propose_ai_candidates(..., provider=
  BedrockSemanticRuleMappingProvider())`確實拋出`NotImplementedError`，
  且package的`ai_semantic_candidates`metadata在呼叫前後**完全沒有
  變化**（沒有任何結果被靜默寫入，遑論被Mock結果取代）。
- Handler層（`evaluation_standard.py::
  propose_evaluation_standard_ai_candidates`）新增環境變數
  `SEMANTIC_RULE_MAPPING_PROVIDER_MODE`（沿用`DATA_PROVIDER_MODE`
  的既定慣例：未設定時明確預設為`"mock"`；設為非`mock`/`bedrock`
  之任何其他值——例如使用者若誤植`"gemini"`——直接fail-fast回傳
  HTTP 500 `INVALID_SEMANTIC_PROVIDER_MODE`，**不會**靜默退回mock）。
  設為`"bedrock"`時，handler捕捉`NotImplementedError`後回傳
  **HTTP 503** `AI_PROVIDER_UNAVAILABLE`（誠實回報「AI目前不可用」），
  **不會**改用Mock結果偽裝成功（TEST 18逐一驗證：拋例外路徑、
  handler 503路徑、無效mode之500路徑，三者皆實測）。

## 10. Tests

新增`tests/test_ai_semantic_fallback.py`（37 tests，涵蓋使用者要求
之18項，部分項目因跨越schema驗證與端對端兩層而各自有獨立測試）：

| # | 測試 | 對應class/method |
|---|---|---|
| 1 | deterministic success → AI not called | `TestTriggerConditions::test_deterministic_success_does_not_call_ai` |
| 2 | unknown factor → AI candidate | 同上`::test_unknown_factor_produces_ai_candidate` |
| 3 | ambiguous factor → AI candidate | 同上`::test_ambiguous_factor_produces_ai_candidate`（合成RULE_EXTRACTION_AMBIGUOUS情境） |
| 4 | AI high confidence → still not auto confirm | `TestConfidenceNeverAutoConfirms::test_high_confidence_still_not_auto_confirmed` |
| 5 | AI low confidence → human review | 同上`::test_low_confidence_requires_human_review` |
| 6 | malformed JSON rejected | `TestSchemaValidationAndScopeGuard::test_malformed_json_rejected` |
| 7 | AI final grade field rejected | 同上`::test_ai_final_grade_field_rejected`（parametrized：grade/final_grade/grade_code） |
| 8 | AI rank field rejected | 同上`::test_ai_rank_field_rejected` |
| 9 | AI adjustment rate field rejected | 同上`::test_ai_adjustment_rate_field_rejected` |
| 10 | AI price field rejected | 同上`::test_ai_price_field_rejected` |
| 11 | provenance preserved | `TestProvenance::test_provenance_preserved` |
| 12 | human edit overrides AI candidate | `TestHumanOverride::test_human_edit_overrides_ai_candidate_and_original_preserved` |
| 13 | original AI candidate preserved | 同上（同一測試同時驗證） |
| 14 | no GradeEngine AI call | `TestNoEngineCallsAI`（parametrized，含grade_engine.py） |
| 15 | no AdjustmentEngine AI call | 同上（含adjustment_engine.py） |
| 16 | no CalculationEngine AI call | 同上（含calculation_engine.py） |
| 17 | mock provider deterministic tests | `TestMockProviderDeterministic`（4個子測試：可重現性、兩個provider之status值、Bedrock純函式） |
| 18 | no Real→Mock silent fallback | `TestNoRealToMockSilentFallback`（3個子測試：repository層拋例外、handler層503、無效mode 500） |

額外新增`TestHandlerSmoke`（2個測試）驗證
`propose_evaluation_standard_ai_candidates` handler在預設mock模式下
的端對端成功路徑，以及package不存在時的404路徑。

## 11. Regression

| 項目 | 指令 | 結果 |
|---|---|---|
| Phase 3C專項測試 | `py -m pytest -q tests/test_ai_semantic_fallback.py` | **37 passed** |
| Phase 3B + 3A + STEP 2專項測試（確認無regression） | `py -m pytest -q tests/test_ai_semantic_fallback.py tests/test_evaluation_standard_human_confirmation.py tests/test_evaluation_standard_importer.py tests/test_case_scoped_rule_architecture.py` | **94 passed** |
| 完整套件（排除既有環境限制的weasyprint檔案） | `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py` | **954 passed, 0 failed**（917既有＋37新增） |
| SAM Lint | `sam validate --lint`（於`infra/`） | **PASS** |
| SAM Build | `sam build --use-container`（真實container build） | **Build Succeeded**（18個function皆build成功，含新增之`ProposeEvaluationStandardAiCandidatesFunction`；已逐一確認該function與既有`ExtractEvaluationStandardFunction`之build artifact皆含`evaluation_standard.py`／`case_rule_repository.py`，`EngineLayer`之`providers/`目錄含`semantic_rule_mapping_provider.py`，`domain/models.py`含`SemanticRuleMappingCandidate`） |

**附註（沿用STEP 3B已記錄之本機環境問題，非本輪程式碼問題）**：本輪
`sam build --use-container`同樣遇到本機Avast防毒軟體Web Shield造成
的容器內pip SSL憑證驗證失敗，以與STEP 3B完全相同的方式（暫時合併
CA bundle、透過`--container-env-var-file`傳入、建置完成後刪除暫存檔）
解決，**未修改**任何專案建置設定檔。

## 12. Files Added / Modified

**新增**：
- `providers/semantic_rule_mapping_provider.py`（`SemanticRuleMappingProvider`介面、`MockSemanticRuleMappingProvider`、`BedrockSemanticRuleMappingProvider`、`validate_ai_response_schema()`、`_build_result()`）
- `tests/test_ai_semantic_fallback.py`（37 tests）
- `docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_REPORT.md`（本文件）

**修改**：
- `domain/models.py`：新增`SemanticRuleMappingCandidate`／
  `SemanticRuleMappingResult`兩個model。
- `backend/handlers/case_rule_repository.py`：新增
  `propose_ai_candidates()`／`_known_factor_names_for_scope()`；
  `build_review_dto()`的每個factor entry新增`ai_candidate`欄位
  （present僅當該candidate曾被`propose_ai_candidates()`處理過）。
- `backend/handlers/evaluation_standard.py`：新增
  `propose_evaluation_standard_ai_candidates` handler、
  `_resolve_semantic_provider()`、
  `InvalidSemanticProviderModeError`。
- `infra/template.yaml`：新增
  `ProposeEvaluationStandardAiCandidatesFunction`（含
  `bedrock:InvokeModel` IAM權限，比照既有`ExplanationFunction`）。

**未修改**：`engine/grade_engine.py`／`engine/adjustment_engine.py`／
`engine/calculation_engine.py`／`engine/rule_engine.py`／
`engine/evaluation_standard_importer.py`／`rule_engine_factory.py`／
`document_upload.py`／任何GIS/S3 Bootstrap/Cadastral相關檔案／
`docs/incoming_rule_sources/`／前端。

## 13. Remaining Gaps

1. **無「一鍵採用AI建議」的UI/API捷徑**：人工要採用AI候選，仍需自行
   把`ai_candidate.candidate.candidate_factor_id`複製到
   `resolve_candidate_factor_mapping()`的呼叫參數——這是刻意設計
   （避免出現一個可以繞過完整re-validate流程的「一鍵接受」按鈕），
   但若未來要做前端，UI仍可以是「一鍵帶入AI建議值到編輯表單」，
   實際confirm呼叫依然是同一套既有API，非本輪範圍。
2. **`known_factor_names`目前僅涵蓋STEP 2既有靜態規則檔之factor
   清單**：`propose_ai_candidates()`若未收到呼叫端明確傳入的
   `known_regional_factors`/`known_individual_factors`，會退回只用
   「這個package自己已經解析成功的因素名稱」作為候選清單（較小、
   自我侷限）——`evaluation_standard.py`的handler已正確傳入完整
   清單，但直接呼叫repository方法（例如未來的CLI/腳本用途）若忘記
   傳入，會得到較差的AI候選品質，非錯誤但值得留意。
3. **Mock provider的字元集合Jaccard評分法為刻意簡陋之啟發式**：
   對用字差異較大但語意相同的因素名稱（例如「臨路情形」vs
   「臨街情況」）評分不佳，這是預期的、誠實的限制——真正需要語意
   理解的案例，交由（尚未AWS_RUNTIME_VERIFIED的）
   `BedrockSemanticRuleMappingProvider`日後接上真實AWS帳號後處理，
   Mock provider從未宣稱能取代它。
