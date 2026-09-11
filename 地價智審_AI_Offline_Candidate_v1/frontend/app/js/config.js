/**
 * config.js — 統一環境設定，供 js/api.js 及所有頁面使用。
 * 依主專案指示第22節：不得每個HTML各自Hardcode API URL。
 *
 * MOCK MODE 為預設值：因本階段尚無真實可存取之AWS API Gateway端點
 * （見 docs/phase7/backend_deployment.md 已誠實記錄之部署限制），
 * 前端優先以 frontend/mock/*.json 運作，待後端真實部署後，
 * 僅需切換 APP_CONFIG.MODE 為 "production" 並設定正確的 API_BASE_URL，
 * 不需修改任何頁面程式碼。
 */
window.APP_CONFIG = {
  // "mock" | "production"
  MODE: "mock",

  // Production 模式下實際呼叫的 API Gateway base URL。
  // 部署後請將此值改為 docs/phase7/api_gateway_spec.md 所述之
  // Invoke URL（例如 https://xxxxx.execute-api.ap-northeast-1.amazonaws.com/prod）。
  API_BASE_URL: "https://REPLACE_WITH_API_GATEWAY_INVOKE_URL",

  // Mock JSON 檔案所在路徑（相對於各HTML頁面）。
  MOCK_BASE_PATH: "frontend/mock",

  // 目前案件（Demo/單一案件情境下使用；多案件情境由 case.html 列表決定）。
  DEFAULT_CASE_NO: "1140901-99-001",

  // 版本標記，顯示於頁尾/Debug區塊，方便追蹤前端部署版本。
  APP_VERSION: "phase7-1.0",
};
