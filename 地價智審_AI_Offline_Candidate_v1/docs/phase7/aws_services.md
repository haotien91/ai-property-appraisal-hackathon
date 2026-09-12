# Phase 7 — AWS Services（選型與理由）

## Part D — Backend：API Gateway + Lambda（非ECS/Fargate）

**選擇：API Gateway + Lambda。**

理由：
1. **成本**：MVP/黑客松展示流量低且集中於Demo時段，Lambda閒置時成本為零；
   Fargate即便設定最小任務數為0也需考量冷啟動延遲與較高的常駐運算單價，
   對展示型流量不划算（呼應主專案指示第34節「黑客松優先...成本合理」）。
2. **部署複雜度**：Lambda搭配SAM可用單一`template.yaml`完整定義API Gateway
   路由+Lambda+IAM權限，不需額外管理ECS Cluster、Task Definition、
   Service、ALB Target Group等資源，更符合黑客松時程。
3. **既有程式碼相容性**：Phase 3-6之引擎程式碼皆為短生命週期、無狀態、
   純運算之Python函式，執行時間預期在數百毫秒至數秒內（PDF產出稍長，
   已設定60秒timeout），完全落在Lambda 15分鐘上限內，無需長駐服務。
4. **例外處理**：PDF Function因需要weasyprint+Cairo+Pango+Noto CJK字型等
   系統層級依賴（見docs/phase5/pdf_output_spec.md已驗證之技術選型），
   改採**Lambda Container Image**（非傳統zip Layer）封裝，仍屬Lambda範疇，
   未引入ECS/Fargate，符合「不要同時使用兩套只為了炫技」之要求。

**未選擇ECS/Fargate之理由**：本系統無需求要求常駐、有狀態、需要WebSocket
長連線或超過15分鐘之運算，Fargate之優勢（無Lambda執行時間上限、更彈性之
運算資源配置）在本系統情境下未被實際利用，反而增加不必要之維運複雜度。

## Part E — Step Functions

見 `docs/phase7/step_functions_spec.md`。核心原則：orchestration本身
（重試、錯誤處理、Choice分支）與business rule（評價基準明細表之級距/
修正率）完全分離，Choice狀態僅依engine回傳之`status`/`error_code`分支。

## Part F — Bedrock

**用途**：`explanation.py` Lambda function，接收**已經算好**的
`FormCompletionResult`/`ReviewResult`，請Bedrock（Claude via Bedrock
Runtime）將其摘要為平實中文說明文字，供審查人員快速掌握案件狀態。

**明確排除**：Grade、Adjustment、Calculation、Distance判定，全數已在
Phase 3-5以Python deterministic engine完成，Bedrock之prompt中僅包含
「已算好的常數」（如最終價格、問題數量），不曾要求模型自行運算或判斷
任何規則值。若Bedrock呼叫失敗，`explanation.py`會降級為樣板式摘要文字
（見程式碼內`except Exception`分支），確保Explanation步驟失敗不阻擋
Step Functions主流程（見`workflow.asl.json`之`AiExplanation`狀態Catch
邏輯，失敗後仍導向`Complete`而非`PipelineFailed`）。

## Part G — Bedrock AgentCore Managed Knowledge Base

**設計（可行時建立，本階段僅完成S3來源文件規劃，未實際建立Knowledge Base
資源，因無AWS存取權限）**：

- 來源：`KnowledgeBaseSourceBucket`（見`infra/template.yaml`），規劃存放
  土地徵收補償市價查估作業手冊.pdf、評價基準明細表範例.pdf、查估書表
  範本.pdf等官方文件。
- 用途：Rule Explanation Q&A（如「為什麼建蔽率70%判定為稍優」可由
  Knowledge Base檢索作業手冊相關頁面佐證）、Source Retrieval、Citation。
- **明確排除**：Knowledge Base（RAG）不負責deterministic rules本身之
  判定——若審查人員詢問「這筆建蔽率的正確等級是多少」，答案來自
  `RuleEngine`（已在`rule_id`/`source_page`中附上精確引用），Knowledge
  Base僅用於「為什麼」層級的補充解釋，不用於「是什麼」層級的數值判定。

## Part H — Database：DynamoDB（非RDS/PostgreSQL）

**選擇：DynamoDB。**

理由：
1. **資料模型天然適合document store**：每個案件之核心資料
   （`CompetitionCase`、`FormCompletionResult`、`ReviewResult`）皆已是
   Pydantic model，序列化後即為完整JSON文件，DynamoDB可直接以單一
   attribute（`data`）儲存整個JSON blob，無需拆解為正規化的關聯式資料表。
2. **存取模式簡單**：目前所有查詢皆為「依case_no取得單一案件」或
   「列出案件清單」，屬單一partition key查找，非需要跨表JOIN之複雜查詢，
   RDS之關聯式查詢能力在此情境下未被實際利用。
3. **與Lambda/Serverless架構一致**：DynamoDB On-Demand計費模式與Lambda
   同樣「閒置零成本」，且無需VPC設定（RDS若要被Lambda存取，通常需要
   VPC+RDS Proxy處理連線池問題，增加額外複雜度與冷啟動延遲風險）。
4. **Single-Table Design**：`PK=CASE#<case_no>`, `SK=META|FACTORS|
   FORM_COMPLETION|REVIEW_RESULT|EXPLANATION`，一次GetItem即可取得單一
   案件之單一階段資料，符合各Lambda handler各自獨立讀寫特定階段資料之
   存取模式。

**已知限制（誠實記錄，非隱藏）**：`case_store.py::list_case_metas()`目前
採用DynamoDB `Scan`（見程式碼註解），僅適合黑客松Demo規模（數十筆案件），
正式大規模生產環境應改用GSI（Global Secondary Index，以常數值為partition
key、`updated_at`為sort key）以支援高效分頁查詢，此非本階段範圍。

## Part I — CloudWatch（監控）

見 `backend/handlers/common.py::log_step()`。設計原則：

- **只記錄**：`case_no`、`step`名稱、`status`（START/SUCCESS/ERROR）、
  `duration_ms`、`error_code`（例外類別名稱，非完整錯誤訊息內文）。
- **絕不記錄**：任何原始因素數值（如建蔽率70%）、比較標的地址、估價金額
  等案件實質內容，避免CloudWatch Logs成為敏感個資/財產資料外洩管道
  （呼應主專案指示第33節「不得將完整敏感案件內容輸出到一般Log」）。
- **X-Ray Tracing**：`template.yaml`之`Globals.Function.Tracing: Active`
  已啟用，供Step Functions各步驟之延遲/錯誤率視覺化追蹤，不依賴額外
  自訂程式碼。
- **API Gateway Access Log**：`ApiAccessLogGroup`記錄`requestId`/`status`/
  `path`/`errorMessage`，同樣不含request body（避免記錄使用者提交之
  案件明細）。
