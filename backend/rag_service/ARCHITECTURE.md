# 問答原型的定位與邊界

本模組位於真正專案根目錄的 backend/rag_service/。它是生成後的唯讀問答後端；handlers/ 仍是既有估價流程入口，不把未部署的本機HTTP服務冒充Lambda handler。

目前 core.py 管理 JSON Store、文件檢索、唯讀工具與模型循環；server.py 僅是本機HTTP/SSE入口。AWS Runtime adapter 尚待帳號權限確定後實作。data/ 是模組的範例資料，不是隊友生成端的正式資料庫；以 RAG_DATA_DIR 接入生成資料。

## API

POST /projects/{project_id}/chat/stream 僅收 message、conversation_id。
project_id 決定目前案件；後端token對應的allowlist決定授權範圍。未來正式身份層替換allowlist，不讓前端自行授權。

工具提供 search_projects、read_project、search_documents、read_document；不列舉問題類型，也沒有routing Agent。跨案只是模型可完成的用途，不是專用請求欄位。

搜尋案件只回傳有權讀取案件的metadata。文件預設查本案與共通文件，模型當輪讀取另一授權案件後才能查該案文件。是否真的需要跨案由模型依使用者語意判斷；硬性安全邊界是後端授權，兩者不可混稱。

session 綁目前案件與授權範圍，記錄已讀案件版本，後續版本變更要求新對話。模型每輪取得目前完整JSON；歷史只保留最近10輪文字，工具證據若需再次引用應重新讀取。正式持久化／多使用者身份仍未實作。

## 尚未解決的限制

- 文件搜尋是關鍵字重疊，不是向量索引；需用實際問題評估召回率。
- PDF文字保留頁碼但未還原矩陣，精確數值必須来自生成JSON。
- 任意JSON可被包進data，但欄位語意、規則及計算追溯仍依赖生成端契約。
- citation事件表示資料已提供，不是已完成逐句來源核驗。
- mock只驗證協議，Bedrock真模型、部署與Mac/Linux仍需實測。

## 位置修正

此前外層rag_service/已移至本位置；外層會議文件移至專案docs/。啟動與測試一律從v1根目錄執行：

```bash
python -m backend.rag_service.server
python -m unittest backend.rag_service.test_service -v
```
