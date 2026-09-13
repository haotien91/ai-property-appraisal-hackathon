# EC2 展示部署

以 feature/simplified-workflow-ai-chat 的完整應用部署，區域 us-west-2。
目前使用單台 Ubuntu 24.04 t3.medium、Nginx、systemd；資源 ID 見 deployment.json。

- 應用來源：/opt/ntpc/app，venv：/opt/ntpc/venv。
- 案件與 PDF：/var/lib/ntpc/cases；公開資料快取：/var/lib/ntpc/public-cache。
- 使用 EBS，root volume 的 DeleteOnTermination=false。刪主機不等於刪資料磁碟。
- APP_MODE=local 表示由同一站提供目前的 API bridge；不可直接改 production，否則新接口注入會停用。
- Nginx 80 → 127.0.0.1:8124；目前是 HTTP，沒有宣稱 HTTPS。
- Security Group 僅允許主辦方四個 IPv4 與部署時的測試來源 IP；沒有開 SSH。
- 透過 SSM 管理，執行角色具備 SSM、部署程式包讀取及 chat-policy.json 所列的指定 Harness／artifact API 權限。

## 安裝與更新

將不含 .env、憑證及 git 資料的專案部署到 /opt/ntpc/app，執行：

```bash
sudo bash /opt/ntpc/app/infra/ec2/install.sh
```

install.sh 會安裝 Python 依賴、Noto CJK、設定服務與反向代理，再測案件 API。
第一次啟動若剛好碰到 nginx reload，健康檢查會重試 HTTP 錯誤。
更新程式時不要覆蓋 /var/lib/ntpc；完成後 `sudo systemctl restart ntpc-app`。
服務啟動不需要終端機持續連線。

## 操作檢查

```bash
systemctl status ntpc-app nginx
journalctl -u ntpc-app -n 40 --no-pager
curl --fail http://127.0.0.1/api/cases
```

## 功能界線

生成仍支援樹林 P001-00～P004-00；聊天已接 Harness。
案件／PDF 留在 EC2 磁碟，JSON 於首次提問時登錄至 S3/DynamoDB。
請用根網址或 index.html，不使用舊版 `?data=live` 測試方式。
來源資料不足時仍應顯示缺漏，不能把部署成功視為估價正確或未知題目驗收通過。

## JSON Harness 聊天

`POST /api/chat` 接收 case_no、question，選用 version 與最近 8 則 history。
後端從目前 EC2 案件生成穩定 JSON（沿用案件建立時間），首次問答經 artifact API
匯入 S3/DynamoDB，保存 case_id/group_id/run_id/version 於案件目錄。
EC2 執行角色使用獨立的 API workspace，不會自動合併 WSParticipantRole 名下舊案件。
前端仍從 EC2 案件庫讀取 PDF；本次 JSON 匯入不含 PDF。

每次問答提供同一 JSON 快照给 InvokeHarness，關閉舊 Excel 工具；不經 PDF OCR/RAG。
目前整份 JSON 送入上下文，最大 1.5 MB，未實作按欄位取用；大案件有較高 token 成本。
官方手冊工具暫未開放，回答不能冒稱已查證手冊。
版本改變時捨棄舊歷史；每次使用新 runtime session，前端明確帶入同版本歷史。

指定 Harness 的 IAM 需同時有 InvokeHarness、InvokeAgentRuntime；精確資源與
必要 artifact 路由列於 chat-policy.json。密鑰不進前端，服務錯誤不回傳 mock 答案。
