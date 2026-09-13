# AWS 開通前的準備（macOS / Linux）

目前原型分成兩部分：本機可跑的 JSON／文件檢索／工具／SSE，以及需要 AWS 憑證的 Bedrock 真實推論。mock 不驗證模型回答品質；離線測試不能證明雲端權限或部署可用。

## 今天：不需要 AWS 帳號

依 README 用 Python 3.12 建立 `.venv`，執行 mock 服務與 curl 串流。Mac 預設 zsh 可直接執行 README 中的 bash 範例。虛擬環境應在 Mac 重建，不要把 Windows 的 env 複製過去。

可完成：

1. 前端處理 session/status/citation/text_delta/done/error，驗證斷線與失敗不顯示成功。
2. 導入隊友 JSON，確認案件版本、單位、標的及計算紀錄完整。
3. 直接測試文件搜尋命中哪幾頁；不用模型也能發現索引不足。
4. 跑單元與本機 HTTP 測試，驗證跨案存取限制。
5. 建立人工問題清單與預期來源，明天用真模型驗證回答是否受資料支持。

本機實際搜尋手冊（在 專案根目錄 `地價智審_AI_Offline_Candidate_v1/`、啟用 venv 後）：

```bash
python - <<'PY'
from backend.rag_service.core import Harness, Store
h = Harness(Store('backend/rag_service/data'))
result, _ = h.tool('search_documents', {'query': '比準地比較價格尾數四捨五入'}, {'demo-a'})
for hit in result['matches']:
    document, citation = h.tool('read_document', {'document_id': hit['document_id']}, {'demo-a'})
    print(citation)
    print(document['text'][:600])
PY
```

人工評估題：

- 「本案18公尺是什麼意思？」應讀本案JSON並標示示範。
- 「最終價格怎麼算？」示範JSON未計算，應說缺少資料。
- 「A案與B案可以直接比較等級嗎？」明確允許B後可讀取，但不能套用A的規則。
- 「比準地比較價格尾數如何處理？」應檢索手冊並引用來源頁。
- 「為什麼排除其他交易？」沒有篩選紀錄就不能編造理由。

## 今天：練習 AWS Console

### 官方免費入門 Labs

[Introduction to AWS Cloud — AWS Builder Labs 官方介紹與課程入口](https://aws.amazon.com/blogs/training-and-certification/begin-your-aws-journey-with-new-free-aws-builder-labs-learning-plan-on-aws-skill-builder/)

官方列出10個免費基礎實作 Lab，使用真實AWS環境。建議先練 IAM（角色／政策）、S3（儲存物件）、Lambda（部署函式）、API Gateway（建立API）。這是學習順序，不代表本案已決定全部採用這些服務。

完整 Builder Labs 目錄另有訂閱方案：[Skill Builder subscriptions](https://skillbuilder.aws/subscriptions)。Lab是受限、暫時的教學環境，不保證有Bedrock／AgentCore或可載入任意專案。

### 想在今天測真正的 Bedrock

需要可呼叫Bedrock的AWS帳號及權限，例如自己的帳號或明確允許該功能的沙盒。新客戶方案有資格與服務限制，模型使用可能消耗額度／產生費用，不能把Free Tier理解為所有服務免費：[官方 Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)。

有可用帳號時，程式可以留在Mac上，直接呼叫Bedrock；不必先把整套後端部署到AWS。這能驗證模型工具選擇、實際回答及串流，部署仍是下一個獨立步驟。

## 明天：拿到活動帳號後

1. 確認活動指定region、可用模型、Bedrock／AgentCore權限、額度與資源建立限制。
2. 確認是否提供CLI臨時憑證或SSO；只登入瀏覽器Console不代表本機Python已有憑證。臨時憑證通常有session token，且會過期，依活動指引設定。
3. 安裝AWS CLI v2後，用 `aws sts get-caller-identity` 確認呼叫的是活動帳號（不要把憑證貼到聊天或commit）。
4. 依 README 切換bedrock模式，在本機先測真實對話、工具循環與跨案問題。
5. 最後依實際權限選雲端部署方案，取得正式API網址並測端到端串流。

[AWS CLI 安裝文件（含Mac/Linux）](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)

```bash
aws --version
aws sts get-caller-identity
# 以上確認身分，不會建立服務；活動憑證需先依主辦方指引配置。
```

目前repo尚未包含AgentCore部署entrypoint／IaC；也未在Mac或Linux實機驗證。現有測試是在Windows Python 3.12執行，應在比賽Mac重跑。不可把本機ThreadingHTTPServer直接當正式網際網路服務。
