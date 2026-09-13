# EC2 展示部署

以 feature/simplified-workflow-ai-chat 的完整應用部署，區域 us-west-2。
目前使用單台 Ubuntu 24.04 t3.medium、Nginx、systemd；資源 ID 見 deployment.json。

- 應用來源：/opt/ntpc/app，venv：/opt/ntpc/venv。
- 案件與 PDF：/var/lib/ntpc/cases；公開資料快取：/var/lib/ntpc/public-cache。
- 使用 EBS，root volume 的 DeleteOnTermination=false。刪主機不等於刪資料磁碟。
- APP_MODE=local 表示由同一站提供目前的 API bridge；不可直接改 production，否則新接口注入會停用。
- Nginx 80 → 127.0.0.1:8124；目前是 HTTP，沒有宣稱 HTTPS。
- Security Group 僅允許主辦方四個 IPv4 與部署時的測試來源 IP；沒有開 SSH。
- 透過 SSM 管理，執行角色僅有 SSM 與部署程式包讀取權限，不含 Bedrock 或 artifact API 權限。

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

此部署保留分支現有行為：生成支援樹林 P001-00～P004-00，聊天仍為示範，
案件／PDF 存在 EC2 磁碟，尚未自動寫入原有 S3/DynamoDB 案件庫。
請用根網址或 index.html，不使用舊版 `?data=live` 測試方式。
來源資料不足時仍應顯示缺漏，不能把部署成功視為估價正確或未知題目驗收通過。
