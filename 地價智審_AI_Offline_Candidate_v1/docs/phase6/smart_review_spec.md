# Phase 6 — Smart Review Architecture Spec

## 五大元件

| 元件 | 檔案 | 職責 | LLM？ |
|---|---|---|---|
| `AuditEngine` | `engine/audit_engine.py` | 頂層orchestrator，遍歷所有因素與跨表欄位，組裝完整`ReviewResult` | 否 |
| `CrossFormValidationEngine` | `engine/cross_form_validation_engine.py` | 檢查應完全相符之跨表欄位對（如表5-2總修正數 vs 表4區域調整率） | 否 |
| `CalculationValidator` | `engine/calculation_validator.py` | 重新計算修正率(矩陣查表)/小計/總計，比對提交值 | 否 |
| `RuleValidator` | `engine/rule_validator.py` | 重新以RuleEngine核算優劣等級，比對提交等級 | 否 |
| `DependencyImpactAnalyzer` | `engine/dependency_impact_analyzer.py` | 依`data/dependency_graph.json`之pattern-based規則，追溯上游錯誤之下游影響範圍 | 否 |

**全部五個元件皆為deterministic**，符合Phase 6「真正判定錯誤的核心仍使用
deterministic code，LLM暫時不是必要條件」之明文要求；五者共用同一組Phase
3/4已驗證之`RuleEngine`/`CalculationEngine`，未重複實作或另建一套規則。

## Issue Schema（`schemas/review_result.schema.json`）

`AuditIssue`包含Phase 6指定之全部13個欄位：`issue_id`, `severity`,
`issue_type`, `source_form`, `field`, `label`, `submitted_value`,
`expected_value`, `rule_id`, `source`, `explanation_data`,
`recommendation_data`, `upstream_dependency`, `downstream_impact`。

`explanation_data`／`recommendation_data`採**結構化物件**（而非純文字
`explanation`/`recommendation`字串），額外提供`check_type`（9種列舉）、
`rule_citation`、`computed_steps`（逐步計算過程）、`suggested_value`、
`requires_human_review`，供前端可分別渲染而非僅顯示整段文字。

Severity（`CRITICAL`/`HIGH`/`MEDIUM`/`LOW`/`INFO`）與issue_type
（`Passed`/`Error`/`Warning`/`Missing`/`Inconsistent`/`Low Confidence`）
為兩個獨立維度：severity依`downstream_impact`是否觸及
`base_parcel_comparison_price`動態決定（有則`CRITICAL`，僅局部影響則
`HIGH`），非寫死每種check_type對應固定severity。

## REQUIRED CHECKS 覆蓋對照

| Phase 6要求 | 實作元件 | 對應`CheckType` |
|---|---|---|
| Grade Error | `RuleValidator.validate_grade` | `GRADE_ERROR` |
| Adjustment Error | `CalculationValidator.validate_adjustment` | `ADJUSTMENT_ERROR` |
| Subtotal Error | `CalculationValidator.validate_sum` | `SUBTOTAL_ERROR` |
| Total Error | `CalculationValidator.validate_sum` | `TOTAL_ERROR` |
| Missing | 三個Validator皆有對應分支 | `MISSING` |
| Wrong Unit | `RuleValidator.validate_grade`（捕捉`WrongUnitError`） | `WRONG_UNIT` |
| Rule Not Found | `RuleValidator.validate_grade`（捕捉`RuleNotFoundError`/`AmbiguousFactorError`） | `RULE_NOT_FOUND` |
| Cross-form Inconsistent | `CrossFormValidationEngine` | `CROSS_FORM_INCONSISTENT` |
| Upstream/Downstream Impact | `DependencyImpactAnalyzer`，注入所有Issue之`downstream_impact`欄位 | — |

## Demo Error Cases（皆以Golden Case Source為準，非隨意數字）

| Case | 欄位 | 真實正確值（Source） | 故意提交之錯誤值 | 偵測結果 |
|---|---|---|---|---|
| A | 主要道路寬度(regional)優劣等級 | 18M -> 普通（docs/phase3/rule_engine_spec.md獨立驗證） | 「優」 | Error/GRADE_ERROR/CRITICAL |
| B | 深度(individual)差異率 | 比準地23m(普通)/比較標的16m(稍劣) -> 1.00%（docs/phase3/rule_engine_spec.md §3.1非平凡驗證案例） | 「5.00」 | Error/ADJUSTMENT_ERROR/CRITICAL |
| C | 區域因素調整百分率跨表一致性 | 表5-2真實總修正數=0.00%（同區段P002-00特例，docs/phase2/business_process.md Step3） | 表4填「5.00」 | Inconsistent/CROSS_FORM_INCONSISTENT/CRITICAL |

三案例之「正確基準」與「故意錯誤值」皆記錄於
`data/demo_errors/build_demo_submission.py`檔案開頭docstring，並可由
`tests/test_smart_review.py::TestDemoErrorCases`重現驗證。

## 完整性驗證（非僅3案例）

除3個demo案例外，`build_demo_submission.py`同時建構**其餘45個欄位之正確
提交值**（由真實Rule/Adjustment Engine計算，非人工輸入），確保：

- `test_exactly_three_non_passed_issues`：驗證AuditEngine對45個正確欄位
  皆正確判定為`Passed`，僅3個錯誤欄位判定為非`Passed`（若驗證邏輯有誤判，
  此測試會偵測出額外的偽陽性或偽陰性）。
- `test_all_three_cases_are_critical_severity`：驗證三案例之
  `downstream_impact`皆正確追溯至`base_parcel_comparison_price`。

## 已知限制

1. `RuleValidator`目前僅處理表5-2/表4之優劣等級與修正率檢查，尚未涵蓋
   表1原始資料本身之合理性檢查（如「主要道路寬度=18M」此數值是否合理，
   僅檢查其「等級判定」是否正確）。
2. `CalculationValidator.validate_sum`為通用加總檢查函式，Subtotal Error
   與Total Error共用同一實作（差異僅在傳入之`component_values`層級與
   `check_type`標籤），未針對兩者的階層關係（小計→總計）建立額外的
   階層一致性交叉檢查——若小計本身錯誤但總計恰好因巧合仍正確加總出
   相同結果，目前設計會分別各自判定，不會產生額外的「小計與總計不
   相容」提示。
3. Amazon Textract或其他真實PDF資料抽取整合，依規劃保留為未來擴充點
   （見`document_extraction_spec.md`），本階段未實作。
