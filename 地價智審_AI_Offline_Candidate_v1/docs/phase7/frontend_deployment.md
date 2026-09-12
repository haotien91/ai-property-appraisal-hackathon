# Phase 7 — Frontend Deployment（S3 + CloudFront）

## 現況（誠實聲明）

**尚未實際部署**（無AWS存取權限，見`architecture.md`開頭聲明）。以下為
一位擁有AWS帳號權限者可直接依循執行的部署步驟。

**前端驗證現況（2026-09-04 RC Cleanup稽核，誠實區分已完成與未完成部分）**：
目前共9個App Pages（含新增之`document-review.html`——文件上傳/擷取/
人工確認）。

- **Static validation：已完成**——Node/語法檢查、HTML標籤配對、fixture
  JSON解析、`python -m http.server`本地起服務後對9頁所有本地`href`/`src`
  參照逐一HTTP可達性掃描（除pre-existing之`favicon.ico`等16個缺失圖片，
  見下方「已知限制」，無因本輪修改新增之404）。
- **Manual browser acceptance（人工實際操作瀏覽器點擊驗收）：pending**
  ——尚未由人工或瀏覽器自動化工具實際執行過完整互動驗收checklist
  （案件建立→case_no全程保留→文件上傳→Extraction→Human Confirmation→
  DOCUMENT Review→Result，含console是否有JS Exception）。**不得**宣稱
  9頁已由Playwright或其他工具「完整驗證」，除非之後真的重新執行並附上
  可覆現之結果——本文件先前版本之相關宣稱缺乏可覆現證據，已於本輪移除。

## 部署步驟

```bash
# 1. 部署後端（先取得API Gateway Invoke URL，見backend_deployment.md）

# 2. 更新前端設定
# 編輯 frontend/app/js/config.js：
#   MODE: "production"
#   API_BASE_URL: "<Step 1取得之Invoke URL>"

# 3. 建立S3 Bucket並上傳（Bucket本身已定義於infra/template.yaml之FrontendBucket）
aws s3 sync frontend/app/ s3://ai-valuation-frontend-<ACCOUNT_ID>/ \
    --exclude "mock/*" --exclude "*.md"
# 註：Mock JSON檔案（frontend/mock/*.json）可保留於S3供切回Mock Mode時使用，
# 不影響Production Mode運作（js/api.js依MODE變數決定是否讀取）

# 4. 透過CloudFront發佈（Distribution已定義於template.yaml之FrontendDistribution，
#    使用Origin Access Control，S3 Bucket本身維持Private，僅CloudFront可讀取）
aws cloudfront create-invalidation \
    --distribution-id <FrontendDistribution輸出之ID> --paths "/*"

# 5. 取得正式URL
aws cloudformation describe-stacks --stack-name ai-valuation \
    --query "Stacks[0].Outputs[?OutputKey=='FrontendCloudFrontUrl'].OutputValue"
```

## 靜態網站設定重點

- **未使用**S3靜態網站託管（Website Hosting）的公開端點，改用
  **CloudFront + Origin Access Control（OAC）**，S3 Bucket維持
  `BlockPublicAcls: true`等全私有設定（見`template.yaml::FrontendBucket`），
  僅CloudFront可透過OAC簽章存取，避免S3端點被繞過CDN直接公開存取。
- **404處理**：`CustomErrorResponses`將404導向既有`404.html`
  （Makerthon_test_1原始檔案，未經修改，見Phase 4
  `frontend_inventory.md`之KEEP分類）。
- **Cache策略**：使用AWS Managed-CachingOptimized Policy，HTML/CSS/JS
  預設會被CloudFront快取，正式更新前端後需執行上方Step 4之
  `create-invalidation`清除快取。

## Mock Mode ↔ Production Mode切換

前端設計為兩種模式共用完全相同的頁面程式碼（見`js/api.js`），差異僅在
`js/config.js::APP_CONFIG.MODE`。部署至CloudFront後，若後端Lambda/API
Gateway尚未部署完成，前端仍可以`MODE: "mock"`獨立運作展示（讀取
`frontend/mock/*.json`），此為主專案指示第24節「Mock Mode」要求之直接
實作，也是本階段favicon.ico等已知前端缺陷（見Phase 4
`frontend_inventory.md`）以外，目前**唯一**已知會影響Production Mode
但不影響Mock Mode之因素——API_BASE_URL預設值為佔位字串，部署前必須
更新（見上方Step 2）。

## 已知限制

1. `favicon.ico`等16個缺失圖片（Phase 4已記錄）尚未於本階段補齊，不影響
   核心功能，僅影響瀏覽器分頁圖示等視覺細節。
2. CORS設定（`template.yaml::ApiGateway.Cors.AllowOrigin: "*"`）為開發
   階段寬鬆設定，正式上線前應改為CloudFront實際網域，見`api_gateway_spec.md`。
