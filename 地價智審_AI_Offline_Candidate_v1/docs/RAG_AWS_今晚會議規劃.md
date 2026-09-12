# 地價智審 AI：RAG／AWS 工作分工與明日實作計畫

這份文件是今晚團隊會議使用的版本。目標是先講清楚我的負責範圍、需要向生成端拿什麼資料，以及明天拿到 AWS 資源後怎麼選服務。前端畫面先不在今晚改，明天拿到 API 契約後再和前端一起接。

## 一、我的負責範圍

生成端完成查估資料、填表、篩選與計算後，我負責做一個「案件結果問答助理」：

```text
已生成案件 JSON
       + 官方說明／作業手冊／適用規則
                    ↓
        單一對話 Agent + Harness
                    ↓
       使用者問問題 → 自主查詢唯讀資料 → 串流回答＋來源
```

這個助理的工作是解釋結果，不重新估價、不修改查估書表，也不自行補出生成端沒有的數字。

預設只使用目前案件和共通知識。使用者明確要求比較其他案件時，才把指定案件加入查詢範圍；後端仍要檢查這些案件是否允許被讀取。

## 二、需要向生成端取得的資料

查估書範本 PDF 是最後給人看的交付成果；Agent 應該優先讀生成端保留的結構化 JSON，而不是再從 PDF 反向猜資料。

請生成端至少提供：

| 資料 | 用途 |
|---|---|
| 案件 ID、版本、地區、用地類別、估價基準日 | 確認回答的案件和適用範圍 |
| 表1、表5-2、表4 的欄位和值 | 解釋表格目前填了什麼 |
| 比準地、比較標的及其身分 | 避免把不同宗地的數字混在一起 |
| 原始值、單位、資料狀態、是否人工確認 | 說明資料是已確認、缺漏或待人工判斷 |
| 適用的評價基準明細表與版本 | 解釋等級門檻和修正率矩陣 |
| 規則來源文件、頁碼、因素名稱 | 回答「這個規則從哪裡來」 |
| 計算輸入、公式、中間值、結果 | 回答「這個數字怎麼算」 |
| 比較標的篩選、採用或排除理由 | 回答「為什麼選這筆資料」 |
| UNKNOWN／人工確認原因 | 避免模型把缺資料說成已判定 |

最重要的追溯鏈是：

```text
最終結果 → 計算紀錄 → 原始值與適用規則 → 文件或資料來源
```

如果生成端暫時只有查估書表的 JSON，也可以先開始；缺少計算或篩選紀錄時，助理要明確說「目前資料沒有記錄」，不能從結果倒推後假裝是原始紀錄。

## 三、S3 要不要用

答案取決於採用哪一條 RAG 路線。

### 不一定需要 S3

若明天先做可展示的最小版本，可以：

- 把案件 JSON 放在後端可讀的檔案或既有資料層。
- 把少量官方說明轉成 Markdown／文字，放入本地或後端部署包。
- 由 Harness 的工具讀 JSON，再用簡單搜尋或直接讀取文件。

這條路可以先驗證回答內容和前端串流，不依賴 S3。

### 使用 Bedrock Knowledge Bases 時，很可能需要 S3

若要使用 AWS 管理的 Knowledge Base 做文件 RAG，最自然的做法是把作業手冊、規則說明、轉換後的文件放到 S3，再讓 Knowledge Base 建立索引。AWS 官方的 S3 data source 會支援文件同步、metadata 與增量更新，S3 bucket 和 Knowledge Base 必須在同一 Region。[S3 作為 Knowledge Base 資料來源](https://docs.aws.amazon.com/bedrock/latest/userguide/s3-data-source-connector.html)

因此會議上應該這樣說：

> 「S3 不是我們問答 API 的必需品；如果明天採用 Bedrock Knowledge Bases，S3 會作為官方文件的資料來源。案件 JSON 則仍可由案件資料層提供，不應把所有精確數值只放進向量庫。」

另外，Knowledge Base 的文件資料一旦同步，能查到它的人取決於 `bedrock:Retrieve` 權限，因此案件私有資料不能不加區分地放到共用知識庫。[Knowledge Bases 權限與資料來源](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html)

## 四、Lambda 要不要用

Lambda 也不是 AgentCore 的必需品。AgentCore Runtime 本身可以託管 Agent，並透過 `InvokeAgentRuntime` 回傳串流；官方文件明確支援串流輸出和 session。[Invoke AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html)

但這個專案已經有後端 Lambda，所以 Lambda 仍有三個可能用途：

| 用途 | 是否建議 | 說明 |
|---|---|---|
| 讀取既有案件資料、組裝請求 | 建議評估 | 可沿用隊友目前的後端邏輯 |
| 作為前端 API 轉接層 | 視明天權限決定 | 前端不直接暴露 AWS 憑證，由 Lambda 呼叫 AgentCore 或 Bedrock |
| 直接承載整個串流 Agent | 暫不優先 | Lambda Function URL 有 response streaming，但要設定特定 invocation mode；若經 API Gateway，串流行為也要實測 |

如果明天 AgentCore Runtime 可以直接提供適合前端的安全 endpoint，可能不需要新增 Lambda。若前端必須透過現有 API Gateway，則沿用或新增一個薄 Lambda adapter 比較容易接回既有系統，但要實測 SSE 是否被中間層緩衝。

Lambda 的結論是：

> 「Lambda 是整合層的選項，不是 RAG 或 AgentCore 的必要條件。先確認前端要怎麼安全呼叫，再決定是否使用。」

## 五、可能使用的 AWS 元件

| 元件 | 角色 | 明天狀態 |
|---|---|---|
| Amazon Bedrock Model | 生成回答、理解問題、選擇唯讀工具 | 必須確認可用模型與 model ID |
| AgentCore Runtime／Harness | 執行單一 Agent、工具循環、session、串流 | 優先嘗試；確認活動帳號是否開通 |
| Bedrock Knowledge Bases | 搜尋作業手冊、規則說明等文件 | 有時間和權限再接；先驗證小批文件 |
| S3 | Knowledge Base 的官方文件資料來源 | 使用 Knowledge Base 時需要；固定流程原型可不用 |
| Lambda | 既有案件資料整合或 API adapter | 視現有架構和串流實測決定 |
| API Gateway／Function URL | 對前端提供 API | 明天確認哪個能保留串流 |
| IAM | 控制 Lambda、AgentCore、Bedrock、S3 存取 | 必須先確認權限，不要用帳號 root 憑證 |
| CloudWatch Logs | 查看部署與模型呼叫錯誤 | 建議開啟，尤其是明天第一次部署 |

今晚不需要先決定 OpenSearch、向量資料庫或 SageMaker。若使用 Managed Knowledge Base，向量儲存與 embedding 由 Knowledge Base 設定處理；案件 JSON 的精確查詢仍由工具直接讀取。

## 六、明天拿到 AWS 資源後的順序

### 先確認環境

```bash
aws --version
aws sts get-caller-identity
aws configure list
```

先確認活動帳號、Region、臨時憑證是否包含 session token，以及以下權限：

- 呼叫指定 Bedrock model 或 inference profile。
- `bedrock-agentcore:InvokeAgentRuntime`，若使用 AgentCore。
- 建立或使用 Knowledge Base、讀取其資料來源，若使用 Knowledge Bases。
- 執行既有或新增 Lambda、查看 CloudWatch Logs，若沿用後端。

### 再測最小鏈路

```text
本機案件 JSON
  → AgentCore／Bedrock
  → 直接問一個簡單問題
  → 確認能回答並取得串流
```

接著測：

1. 問目前案件某一個欄位的意義。
2. 問某個計算結果，確認回答只使用 JSON 中存在的計算紀錄。
3. 明確要求比較第二案件，確認後端才開放第二案件。
4. 問 JSON 沒有的篩選理由，確認它會說資料不足。
5. 使用手冊規則問題，確認引用頁碼或文件來源。

### 最後再接前端

明天和前端約定：

```http
POST /projects/{project_id}/chat/stream
```

請求至少包含：

```json
{
  "message": "為什麼這個因素是普通？",
  "conversation_id": "optional-session-id"
}
```

回傳事件沿用原型：`session`、`status`、`citation`、`text_delta`、`done`、`error`。前端今晚不需要改畫面，只要明天知道如何訂閱串流並把事件放進聊天元件。

## 七、Lambda 快速複習

Lambda 最基本的概念是：AWS 收到事件後，呼叫你指定的 handler；handler 讀取 `event`，需要時讀 `context`，最後回傳結果。

最小 Python 範例：

```python
import json

def lambda_handler(event, context):
    message = event.get("message", "")
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": message})
    }
```

對本案來說，Lambda 可能會是：

```text
前端 → API Gateway／Function URL → Lambda adapter
                                   ├─ 驗證案件範圍
                                   ├─ 讀案件 JSON
                                   └─ 呼叫 AgentCore／Bedrock
```

需要記住的幾個點：

- Lambda 的 execution role 決定它能不能呼叫 Bedrock、AgentCore、S3 或其他 AWS 服務。
- 環境變數放 model ID、Region、資料位置等設定，不要把憑證寫入程式。
- timeout、memory、log 都要在部署設定中確認。
- 每次 invocation 的本地檔案與記憶不應被視為永久儲存。
- 串流不是普通 Lambda JSON response 自動就有；需要支援 response streaming 的入口與設定。AWS 官方說明：[Lambda Function URLs](https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html)、[Lambda response streaming](https://aws.amazon.com/blogs/compute/introducing-aws-lambda-response-streaming/)。

Lambda 官方快速入門：[Lambda execution role](https://docs.aws.amazon.com/lambda/latest/dg/lambda-intro-execution-role.html)。

## 八、今晚會議可以直接講的版本

> 我負責在生成端完成查估書表後，建立一個案件結果問答助理。生成端要提供版本化的案件 JSON，裡面保留表1、表5-2、表4、適用規則、計算紀錄、來源和篩選理由。助理是單一 Agent，由 Harness 管理工具呼叫，預設只讀目前案件；使用者明確要求時才比較指定案件。今晚先不改前端，先確認資料契約和 AWS 路線。S3 不是必需品；如果用 Bedrock Knowledge Base，S3 會放官方說明文件。Lambda 也不是 AgentCore 必需品，但可能作為既有後端的 API adapter。明天拿到 AWS 權限後先驗證 Bedrock／AgentCore 的最小串流鏈路，再決定是否接 Knowledge Base、S3 和 Lambda，最後再與前端接 API。

## 九、目前明確不承諾的事情

- 還沒有決定一定使用 S3、Lambda、Knowledge Bases 或 SageMaker。
- 尚未確認活動帳號開放哪些 AgentCore 功能、模型和 Region。
- 尚未在真正 AWS 帳號驗證串流、權限或費用。
- 前端畫面與最終 endpoint 仍等明天和前端人員確認。
