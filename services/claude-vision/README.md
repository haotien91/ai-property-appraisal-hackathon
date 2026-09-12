# 後端呼叫 Claude：文字＋圖片

不另建 HTTP proxy。後端透過 boto3 的 Bedrock Runtime `converse` 呼叫 AWS 託管模型；與案件匯入 API、harness、RAG 分開。

## 設定

- Region：`us-west-2`
- Model ID：`us.anthropic.claude-sonnet-4-6`
- AWS service endpoint：`https://bedrock-runtime.us-west-2.amazonaws.com`（SDK 自動使用，不是 OpenAI-compatible endpoint）
- 認證：AWS 執行角色，自動取得臨時憑證。
- 模型可由 `BEDROCK_MODEL_ID` 或函式 model_id 覆寫；更換時也要同步 IAM policy。

2026-09-13 已在 account `137336531963`，使用 `WSParticipantRole/Participant` 實測：64×64 紅色色塊＋一句問題，模型回覆 Red，31 input tokens、4 output tokens，stop_reason=end_turn。這證明此身分能呼叫，不代表未提供的 backend role 已獲授權，也不代表書表辨識品質已驗收。

此 US inference profile 可路由到 us-west-2、us-east-1、us-east-2；不是保證資料只留在 us-west-2。caller-policy.json 列出這些目標的模型與 profile ARN。

## 本機先試

```sh
python3 -m pip install -r services/claude-vision/requirements.txt
python3 services/claude-vision/claude_vision.py \
  --image /path/to/table.png \
  --prompt '請逐項讀取圖片中的欄位與數值；看不清楚時請標示，不要猜測。' \
  --profile hackathon
```

## 放進隊友的程式

將 claude_vision.py 加入 backend 可 import 的路徑：

```python
from claude_vision import describe_image

result = describe_image(
    image_bytes,
    '請讀取圖片中的欄位名稱與數值，保留單位。',
    image_format='png',
    max_tokens=4096,
)
text = result['text']
if result['stop_reason'] == 'max_tokens':
    # 回應被截斷，不要把它當完整 JSON／表格使用。
    raise RuntimeError('Model output reached max_tokens')
```

AWS 執行時不傳 profile，不設定長期 Access Key。Lambda 使用 execution role；ECS 使用 task role（不是 task execution role）。由部署者將 caller-policy.json 的授權加到該角色；此檔尚未附加到任何新 backend role，因角色尚未確認。此政策僅含同步 Converse 所需 InvokeModel；串流另需 InvokeModelWithResponseStream。

若執行環境在沒有對外連線的私有 VPC，仍需 NAT 或可用的 Bedrock Runtime VPC endpoint；IAM 授權不會自動提供網路連線。

## 輸入與錯誤

- helper 支援單張 PNG/JPEG/GIF/WebP，每張保守限制 3,750,000 bytes；像素尺寸與模型特有限制仍由 Bedrock 驗證。PDF 須先由生成端轉成圖片，不把 PDF bytes 冒充圖片。
- boto3 接收原始 bytes，SDK 處理編碼；不要先把 bytes 轉成 base64 字串再放入 bytes 欄位。
- 回傳 text、stop_reason、usage、request_id；沒有保證模型文字符合 JSON schema，解析後仍須驗證。
- AccessDenied：確認 caller role 的 InvokeModel 權限、模型啟用條件及組織 SCP。ModelNotReady／Throttling：採有限重試與排隊，不平行無上限發送。
- read timeout 180 秒、最多兩次 SDK 嘗試。重試模型推論可能重複計費，無業務冪等保證。若外層使用同步 API Gateway，需考慮更短的 HTTP timeout；長任務應交由背景 worker。
- 本 helper 不存圖片、提示或回覆，也不輸出 AWS 憑證。

官方文件：[圖片內容格式](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ImageBlock.html)、[跨區域 profile 與權限](https://docs.aws.amazon.com/en_en/bedrock/latest/userguide/geographic-cross-region-inference.html)。
