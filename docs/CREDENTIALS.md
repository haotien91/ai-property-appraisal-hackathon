# AWS 憑證放哪裡

結論先講：**部署到 AWS 上時，一把金鑰都不要有。**

`llm_provider.make_bedrock_llm()` 刻意不接收憑證參數。boto3 會依序自動解析：

```
1. 環境變數     AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
2. 設定檔       ~/.aws/credentials 的 profile
3. 容器角色     ECS / Fargate 的 task role        ← 部署用這個
4. 實例角色     EC2 instance profile
```

程式碼完全不需要知道用的是哪一條。

---

## 依環境選擇

| 執行環境 | 憑證怎麼來 | 要設什麼 |
| --- | --- | --- |
| 本機開發 | `~/.aws/credentials` 的 profile | `AWS_PROFILE=hackathon` |
| **ECS / Fargate** | **IAM task role** | **不用設，也沒有金鑰** |
| EC2 | instance profile | 不用設 |
| Lambda | execution role | 不用設 |
| AWS 之外（地端、他雲） | Secrets Manager 取出後注入 | 見最後一節 |

`AWS_REGION` 與 `BEDROCK_MODEL_ID` 是**設定不是機密**，放環境變數沒問題。

---

## 本機開發

不要用 PowerShell 的 `$env:AWS_SECRET_ACCESS_KEY=...`。理由：會留在 shell
歷史、會傳給每一個子行程、關掉視窗就消失還得重打。改用 profile：

```powershell
aws configure --profile hackathon
# AWS Access Key ID     : AKIA...
# AWS Secret Access Key : ...
# Default region name   : us-east-1

$env:AWS_PROFILE="hackathon"
$env:AWS_REGION="us-east-1"
$env:BEDROCK_MODEL_ID="global.amazon.nova-2-lite-v1:0"

python llm_provider.py          # 煙霧測試
```

金鑰只存在 `~/.aws/credentials`，那個檔案不在專案目錄內，不可能誤 commit。

專案根目錄的 `.env.example` 列出所有非機密設定，複製成 `.env` 使用。
`.gitignore` 已擋掉 `.env` 與 `.env.*`（`.env.example` 例外）、
`credentials.json`、`*.pem`、`*.key`、`.aws/`。

---

## ECS / Fargate（實際部署）

> 本節描述目標部署形態。專案目前沒有 Dockerfile（曾有一版，已移除，
> 可從 git history 取回：`git show 63b3c80:Dockerfile`）。
> 容器化時記得兩件事：安裝 `fonts-noto-cjk`（否則中文變豆腐框），
> 以及改用 `opencv-python-headless`。

Task definition 有**兩個角色**，這是最常搞混的地方：

| 欄位 | 誰在用 | 該給什麼權限 |
| --- | --- | --- |
| `executionRoleArn` | ECS agent，在啟動容器**之前** | 拉 ECR 映像、寫 CloudWatch Logs、讀 Secrets Manager |
| `taskRoleArn` | **你的程式**，執行期間 | `bedrock:InvokeModel` |

`bedrock:InvokeModel` 要放在 **task role**。放在 execution role 是無效的 ——
那個角色在容器跑起來後就不再參與，boto3 拿不到它。

### 1. 建立 task role

```bash
cat > bedrock-task-trust.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ecs-tasks.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role \
  --role-name zoneMapTaskRole \
  --assume-role-policy-document file://bedrock-task-trust.json
```

### 2. 只給需要的權限

```bash
cat > bedrock-invoke-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "InvokeBedrockModel",
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel"],
    "Resource": [
      "arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0",
      "arn:aws:bedrock:*:*:inference-profile/global.amazon.nova-2-lite-v1:0"
    ]
  }]
}
EOF

aws iam put-role-policy \
  --role-name zoneMapTaskRole \
  --policy-name BedrockInvoke \
  --policy-document file://bedrock-invoke-policy.json
```

用跨區推論（CRIS）時 **兩個 ARN 都要給**：請求打到 inference profile，
profile 再轉發到各區的 foundation model，兩層都會做授權檢查。

不要用 `AmazonBedrockFullAccess` 之類的託管政策。那會連
`bedrock:DeleteModelInvocationLoggingConfiguration` 都一起給，本專案只需要
呼叫模型。

### 3. Task definition

```json
{
  "family": "zone-map",
  "networkMode": "awsvpc",
  "executionRoleArn": "arn:aws:iam::<ACCOUNT>:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::<ACCOUNT>:role/zoneMapTaskRole",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "containerDefinitions": [{
    "name": "zone-map",
    "image": "<ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/zone-map:latest",
    "essential": true,
    "portMappings": [{ "containerPort": 8080, "protocol": "tcp" }],
    "environment": [
      { "name": "AWS_REGION",       "value": "us-east-1" },
      { "name": "BEDROCK_MODEL_ID", "value": "global.amazon.nova-2-lite-v1:0" },
      { "name": "NLSC_TILE_CACHE",  "value": "/var/cache/nlsc-tiles" },
      { "name": "ROAD_CACHE_DIR",   "value": "/var/cache/nlsc-roads" },
      { "name": "ZONE_MAP_CACHE",   "value": "/var/cache/zone-maps" }
    ],
    "secrets": [
      {
        "name": "ZONE_MAP_API_KEYS",
        "valueFrom": "arn:aws:secretsmanager:us-east-1:<ACCOUNT>:secret:zone-map/api-keys"
      }
    ]
  }]
}
```

注意 `environment` 裡**沒有任何 AWS 憑證**。只有 region 與 model id，
這兩個不是機密。

`environment` 與 `secrets` 的差別很重要：`environment` 的值在 console 與
`aws ecs describe-task-definition` 的輸出裡都是明文，任何有 read 權限的人
都看得到。`secrets` 只存 Secrets Manager 或 SSM 的 ARN，值在容器啟動時
才由 ECS agent 注入。

所以 `ZONE_MAP_API_KEYS`（對外 API 的金鑰）走 `secrets`，
`AWS_REGION` 走 `environment`。

---

## 驗證憑證有沒有生效

`llm_provider.describe_credentials()` 會回報 boto3 **實際**解析到哪一組憑證，
不輸出祕密值。診斷 `AccessDenied` 時第一件事就是確認這個 —— 很容易以為
在用 A profile 其實在用 B。

```bash
python llm_provider.py
```

```
=== 憑證 ===
  region             = us-east-1
  profile            = hackathon
  credential_source  = shared-credentials-file      ← 來源
  access_key_id      = AKIA…7X2Q                    ← 只顯示頭尾
  has_session_token  = False
  account            = 123456789012
  arn                = arn:aws:iam::123456789012:user/dev
```

在 Fargate 上跑同一支指令，`credential_source` 會是 `container-role`，
`arn` 會是 `arn:aws:sts::...:assumed-role/zoneMapTaskRole/...`。
看到這個就代表 task role 生效了。

---

## 常見錯誤與對應

`llm_provider._explain_bedrock_error()` 會把錯誤碼翻成下一步。

| 錯誤 | 原因 | 處理 |
| --- | --- | --- |
| `ValidationException` 提到 inference profile | 裸 model id 在該區不支援隨需呼叫 | 改用 `global.` / `us.` / `eu.` / `apac.` 前綴 |
| `AccessDeniedException` | IAM 沒開，或 Bedrock console 的 Model access 未申請 | 兩者都要確認 |
| `UnrecognizedClientException` | 憑證無效 | 檢查 key 是否貼錯 |
| `InvalidSignatureException` | 臨時憑證過期或缺 session token | 補 `AWS_SESSION_TOKEN` |
| `ResourceNotFoundException` | model id 拼錯或該區沒有 | `aws bedrock list-foundation-models --region <區域>` |

Nova 2 系列在美國以外呼叫時，裸的 `amazon.nova-2-lite-v1:0` 會被拒，
必須用跨區推論的 profile id。這是實際會踩到的第一個坑。

---

## 在 AWS 之外執行

只有這種情況才需要長期金鑰。做法是存進 Secrets Manager，執行時取出：

```python
import json

import boto3

secrets = boto3.client("secretsmanager", region_name="us-east-1")
payload = json.loads(
    secrets.get_secret_value(SecretId="zone-map/aws-keys")["SecretString"]
)

import llm_provider

session = boto3.Session(
    aws_access_key_id=payload["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=payload["AWS_SECRET_ACCESS_KEY"],
    region_name="us-east-1",
)
llm = llm_provider.make_bedrock_llm(
    client=session.client("bedrock-runtime")
)
```

但這是雞生蛋問題：你需要憑證才能讀 Secrets Manager。所以只在真的無法用
IAM role 時才這樣做，並記得設定金鑰輪替。
