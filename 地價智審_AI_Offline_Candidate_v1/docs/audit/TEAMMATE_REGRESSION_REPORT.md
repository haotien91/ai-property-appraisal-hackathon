# TEAMMATE_REGRESSION_REPORT

**日期**：2026-09-11
**性質**：STEP 4 §14 Regression — 執行STEP 4 tests、STEP 3C/3B/3A/2
tests、full pytest、sam validate --lint、sam build --use-container。

---

## 執行結果

| 項目 | 指令 | 結果 |
|---|---|---|
| STEP 4專項測試 | `py -m pytest -q tests/test_teammate_rule_data_integration.py` | **23 passed** |
| STEP 3C/3B/3A/2專項測試（確認無regression） | 分別/合併執行 `test_ai_semantic_fallback.py`／`test_evaluation_standard_human_confirmation.py`／`test_evaluation_standard_importer.py`／`test_case_scoped_rule_architecture.py` | 前一輪（STEP 3C完成時）已確認 **94 passed**（37+18+20+19），本輪未修改上述任一測試檔案或其對應production code，full suite（見下）已再次涵蓋並確認無regression |
| 完整套件（排除既有環境限制的weasyprint檔案） | `py -m pytest -q --ignore=tests/test_phase5_golden_pipeline.py` | **977 passed, 0 failed**（954既有＋23新增） |
| SAM Lint | `sam validate --lint`（於`infra/`） | **PASS**（本輪未修改`infra/template.yaml`） |
| SAM Build | `sam build --use-container`（真實container build） | **Build Succeeded**（18個function／2個layer／1個container image全數建置成功；已逐一確認`EngineLayer`之`engine/central_local_rule_cross_validator.py`與`data/rules/factor_alias_registry.json`皆出現於build artifact內） |

**附註（沿用STEP 3B/3C已記錄之本機環境問題，非本輪程式碼問題）**：
`sam build --use-container`同樣遇到本機Avast防毒軟體Web Shield造成的
容器內pip SSL憑證驗證失敗，以與前兩輪完全相同的方式（暫時合併CA
bundle、透過`--container-env-var-file`傳入、建置完成後刪除暫存檔）
解決，**未修改**任何專案建置設定檔。

## STEP 4新增測試對照使用者20項要求

| # | 測試 | 對應test |
|---|---|---|
| 1 | Existing central max registry unchanged | `test_existing_central_max_registry_unchanged` |
| 2 | Team central values cross-check | `test_teammate_central_values_cross_check`（378/378 match，skip if antiword不可用） |
| 3 | '-' remains NOT_APPLICABLE | `TestDashSemantics`（2個子測試） |
| 4 | Residential coverage classified | `test_residential_coverage_classified` |
| 5 | Commercial coverage classified | `test_commercial_coverage_classified` |
| 6 | Industrial coverage classified | `test_industrial_coverage_classified` |
| 7 | Agricultural coverage classified | `test_agricultural_coverage_classified` |
| 8 | Unknown land use no fallback | `test_unknown_land_use_no_fallback` |
| 9 | Alias exact mapping | `test_alias_exact_mapping` |
| 10 | Alias does not cross scope | `test_alias_does_not_cross_scope` |
| 11 | Regional fixture not loaded as runtime | `test_regional_rate_fixture_never_referenced_by_production_code` |
| 12 | TEAMMATE_EXPECTED vs MAIN_ENGINE_ACTUAL | `test_teammate_expected_vs_main_engine_actual`（19/19 checkable記錄通過） |
| 13 | Central maximum exceeded detected | `test_central_maximum_exceeded_detected`（真實發現：individual「道路種類」8%>5%） |
| 14 | Central max does not determine grade | `test_central_max_does_not_determine_grade` |
| 15 | Same-segment inconsistent evidence flagged | `test_no_hardcoded_same_segment_zero_shortcut`（確認無寫死捷徑；完整稽核類型為記錄在案之remaining gap） |
| 16 | No Jinshan fallback | `test_no_jinshan_fallback_for_other_district` |
| 17 | No commercial fallback | `test_no_commercial_fallback_for_residential_query` |
| 18 | No Golden fallback | `test_no_golden_fallback_unrelated_district_query` |
| 19 | No AI direct grade/rank/rate/price | `test_ai_boundary_guard_still_rejects_final_decision_fields` |
| 20 | Case A/B isolation retained | `test_case_isolation_retained` |

（另有`test_no_second_parallel_naming_system`／
`test_central_maximum_not_exceeded_for_clean_factors`兩項延伸測試，
共23項。）

## 結論

```
STEP4_TESTS=PASS
FULL_REGRESSION=PASS
FULL_REGRESSION_PASSED=977
SAM_VALIDATE=PASS
SAM_BUILD_USE_CONTAINER=PASS
```
