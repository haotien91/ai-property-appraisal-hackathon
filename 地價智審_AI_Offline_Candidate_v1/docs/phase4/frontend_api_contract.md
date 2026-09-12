# Phase 4 — Frontend API Contract

> 依主專案指示第22節，本文件定義前端`js/api.js`與後端溝通之Request/Response/
> Error Schema。所有Schema欄位命名**直接沿用**`domain/models.py`與
> `schemas/field_dictionary.json`之`field_id`（依Phase 2 frontend_data_contract.md
> 已建議之原則），避免前後端各自一套命名。**本文件僅定義契約，本階段未實作
> 任何Web Framework（無FastAPI/Flask路由程式碼），API本身待Phase 5/7實作。**

## 通用 Error Schema

所有端點失敗時，一律回傳以下結構（HTTP狀態碼依錯誤性質對應4xx/5xx）：

```json
{
  "error": {
    "code": "RULE_NOT_FOUND | WRONG_UNIT | VALIDATION_ERROR | CASE_NOT_FOUND | INTERNAL_ERROR",
    "message": "人類可讀錯誤說明",
    "field_id": "若錯誤與特定欄位相關，標示對應field_id，否則為null",
    "details": {}
  }
}
```

`code`列舉值對應後端Engine例外：
- `RULE_NOT_FOUND` ↔ `engine.rule_engine.RuleNotFoundError`
- `WRONG_UNIT` ↔ `engine.rule_engine.WrongUnitError`
- `VALIDATION_ERROR` ↔ Pydantic `ValidationError`（domain/models.py之型別驗證失敗）
- `CASE_NOT_FOUND` ↔ 查無對應`case_no`
- `INTERNAL_ERROR` ↔ 未預期之伺服器錯誤（不得暴露內部堆疊細節給前端）

---

## GET /api/cases

**用途**：取得案件列表。

**Request**：Query參數 `?status=&page=&page_size=`（皆選填）

**Response 200**：
```json
{
  "cases": [
    {
      "case_no": "1140901-99-001",
      "segment_code": "P002-00",
      "district": "金山區",
      "status": "COMPLETED | IN_PROGRESS | MANUAL_REVIEW_REQUIRED",
      "updated_at": "2026-08-30T12:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

**Response（EMPTY狀態）**：`cases: []`，前端依此渲染EMPTY State（見主專案指示第23節）。

**Error**：無案件不視為錯誤（回傳空陣列），僅伺服器錯誤時回傳`INTERNAL_ERROR`。

---

## POST /api/cases

**用途**：建立新案件，對應`domain.models.CompetitionCase`（不含`base_parcel_factors`
等因素細節，因素資料由後續`collect-data`端點提供）。

**Request**：
```json
{
  "case_no": "1140901-99-001",
  "appraisal_period": "1140901",
  "appraisal_base_date": "1140901",
  "segment_code": "P002-00",
  "segment_scope": "...",
  "city": "新北市",
  "district": "金山區",
  "land_use_type": "商業用地",
  "base_parcel_id": "金美段489地號",
  "comparable_ids": ["溫泉段218地號"]
}
```

**Response 201**：同上結構，額外附加`created_at`。

**Error**：`VALIDATION_ERROR`（缺少必要欄位，對應`CompetitionCase`之Pydantic
必填限制）；重複`case_no`時回傳`VALIDATION_ERROR`並於`details`註明衝突原因。

---

## GET /api/cases/{id}

**用途**：取得單一案件完整資料。

**Response 200**：完整`CompetitionCase`結構（含已收集之`base_parcel_factors`／
`comparable_factors`，若尚未收集則為空陣列）。

**Error**：`CASE_NOT_FOUND`（404）。

---

## POST /api/cases/{id}/collect-data

**用途**：提交/更新該案件之原始因素資料（表1相關133個欄位，見
`schemas/field_dictionary.json`）。

**Request**：
```json
{
  "base_parcel_factors": [
    {"field_id": "main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M",
     "evidence": {"source": "使用者輸入", "source_type": "AI輔助填寫"}}
  ],
  "comparable_factors": {
    "溫泉段218地號": [ /* 同上結構 */ ]
  }
}
```
逐一對應`domain.models.FactorInput`。

**Response 200**：
```json
{
  "case_no": "1140901-99-001",
  "collected_field_count": 19,
  "missing_field_ids": ["individual_land_shape"],
  "status": "PARTIAL | COMPLETE"
}
```

**Error**：`VALIDATION_ERROR`（`raw_value`型別與`unit`不符`field_dictionary.json`
定義）；此端點**不**呼叫Rule Engine，僅做資料存檔與Pydantic型別驗證，Grade
判定延後至`analyze`端點（保持單一職責）。

---

## POST /api/cases/{id}/analyze

**用途**：對已收集資料執行 Grade Engine + Adjustment Engine，回傳
`GradeResult`/`AdjustmentResult`清單（尚未產出完整表單，僅分析結果）。

**Request**：無body（依`case_no`讀取已存資料）或 `{"force_recompute": true}`。

**Response 200**：
```json
{
  "case_no": "1140901-99-001",
  "grades": [ /* domain.models.GradeResult 陣列，含party_role/factor/grade/grade_code */ ],
  "adjustments": [ /* domain.models.AdjustmentResult 陣列 */ ],
  "unresolved_factors": [
    {"field_id": "...", "reason": "RULE_NOT_FOUND", "factor": "..."}
  ]
}
```

**Error**：`RULE_NOT_FOUND`（列於`unresolved_factors`而非整體失敗——單一因素
找不到規則不應阻擋其餘因素之分析結果回傳，呼應`engine/form_completion_engine.py`
之「單欄位失敗轉MANUAL_REVIEW_REQUIRED、不阻斷整體流程」設計）。

---

## POST /api/cases/{id}/complete-form

**用途**：執行完整 Grade→Adjustment→Calculation→FieldCompletion pipeline
（`engine.form_completion_engine.FormCompletionEngine.complete_form`），
回傳`FormCompletionResult`。

**Response 200**：
```json
{
  "case_no": "1140901-99-001",
  "form": "表4+表5-2（整合輸出）",
  "fields": [
    {
      "field_id": "individual_land_depth_differential_rate_溫泉段218地號",
      "chinese_label": "深度差異率", "form": "表4",
      "raw_value": "base=23, comp=16", "normalized_value": "base=普通, comp=稍劣",
      "source": "Rule Engine（評價基準明細表範例.pdf p.6）",
      "rule_id": "IND-LAND_DEPTH-03", "grade": "base=普通, comp=稍劣",
      "adjustment": "1.00", "formula": "adjustment_matrix[base_grade_code][comparable_grade_code]",
      "calculation": "matrix[3][4] = 1.00", "final_value": "1.00",
      "status": "COMPLETED"
    }
  ],
  "generated_at": "2026-08-30T12:00:00Z",
  "completed_count": 54, "manual_review_count": 1, "unknown_count": 0,
  "warnings": ["..."]
}
```
逐欄位對應`domain.models.FieldCompletion`（Decimal欄位序列化為字串，避免JSON
浮點數精度問題——此為`engine/calculation_engine.py`全程使用Decimal之直接延伸
要求，序列化邊界不得引入float）。

**Error**：`VALIDATION_ERROR`（案件資料不完整，無法執行pipeline）。

---

## GET /api/cases/{id}/pdf

**用途**：取得填寫完成之PDF（Phase 5 PDF Engine範圍，本階段僅定義契約）。

**Response 200**：`Content-Type: application/pdf`（二進位串流）或
```json
{"pdf_url": "https://.../case-1140901-99-001.pdf", "generated_at": "..."}
```
（二擇一，實際採用哪種由Phase 5/7依部署方式決定，本階段不預先鎖定）。

**Error**：`CASE_NOT_FOUND`；若表單尚未`complete-form`過，回傳
`VALIDATION_ERROR`（訊息：「案件尚未完成書表填寫，無法產出PDF」）。

---

## POST /api/cases/{id}/review

**用途**：執行Smart Review（Phase 6範圍，本階段僅定義契約占位）。

**Response 200**：
```json
{
  "case_no": "1140901-99-001",
  "issues": [
    {
      "issue_id": "...", "severity": "Passed|Error|Warning|Missing|Inconsistent|Low Confidence",
      "source_form": "表4", "field": "region_adjustment_rate",
      "submitted_value": "5.00", "expected_value": "0.00",
      "rule_id": "...", "explanation": "...", "recommendation": "...",
      "upstream_dependency": [...], "downstream_impact": [...]
    }
  ]
}
```
（AuditIssue結構呼應主專案指示第17節；本階段Domain Model尚未定義對應Pydantic
Model，Phase 6建立時應保持此契約形狀）。

**Error**：`CASE_NOT_FOUND`。

---

## GET /api/cases/{id}/result

**用途**：取得案件最終總覽。

**Response 200**：
```json
{
  "case_no": "1140901-99-001",
  "status": "COMPLETED | MANUAL_REVIEW_REQUIRED",
  "pdf_url": "...",
  "review_summary": {"passed": 20, "error": 0, "warning": 1},
  "final_value": {"base_parcel_comparison_price": "212958"}
}
```

**Error**：`CASE_NOT_FOUND`。

---

## 序列化注意事項（供Phase 5/7實作時遵守）

1. 所有`Decimal`欄位（如`adjustment`, `final_value`, `raw_result`）序列化為
   **字串**而非JSON number，避免JavaScript浮點數精度問題破壞
   `docs/phase2/calculation_dependency.md`與`docs/phase3`已驗證之精度保留原則。
2. `FieldStatus`/`SourceType`等Enum欄位序列化為其字串值（Pydantic預設行為），
   前端應以字串比對而非數字代碼判斷狀態。
3. 任何回應皆不得將Grade/Adjustment/Calculation之數值以外的**規則內容本身**
   （如完整adjustment_matrix）回傳給前端，前端只消費「已算好的結果」，避免
   業務規則外洩至前端可被竄改之環境（呼應主專案指示「規則不得放在Frontend」）。
