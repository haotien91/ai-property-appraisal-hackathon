# Phase 6 — Document Extraction Spec

## 決策：Mock Extracted Data，非 Amazon Textract

依 Phase 6 指示「如果此階段需要讀已填PDF，可以評估Amazon Textract或Mock
Extracted Data，不要為了使用Textract而強制使用」，本階段**採用Mock
Extracted Data**，理由如下：

1. **環境限制**：本開發環境之網路存取權限為白名單制（見系統網路設定），
   僅能存取套件安裝來源（pypi/npm/github等），**無法呼叫AWS Textract API**
   （非白名單網域），故技術上無法在本階段實際整合Textract。
2. **本階段核心目標非OCR，而是「已抽取資料之後的比對邏輯」**：Phase 6要求
   的是「AuditEngine/CrossFormValidationEngine/CalculationValidator/
   RuleValidator/DependencyImpactAnalyzer」等deterministic比對元件，
   OCR/文件抽取是**上游、獨立**的關注點——`SubmittedFormData`
   （`engine/audit_engine.py`）已將「已填表單之實際內容」抽象為一個獨立
   資料結構，未來若要串接Textract，僅需新增一個「Textract Adapter」將
   Textract回傳之文字/表格結果轉為`SubmittedFormData`即可，**不需修改
   任何驗證引擎**。此設計呼應Phase 5 Data Provider之Adapter Pattern。
3. **不強制使用Textract之官方依據**：Phase 6指示本身即明文「不要為了使用
   Textract而強制使用」，故不使用Textract不需要額外的正當化理由；本節
   僅補充說明「即便有Textract存取權限，Phase 6之核心驗收標準（Demo Error
   Case是否可被自動偵測、Downstream Impact是否可追溯）亦與OCR技術本身
   無關」。

## SubmittedFormData 設計

```python
class SubmittedFormData:
    case_no: str
    submitted_grades: Dict[(field_id, comparable_id), str]
    submitted_adjustments: Dict[(field_id, comparable_id), Any]
    submitted_totals: Dict[str, Any]
```

此結構代表「已填表單之實際內容」，無論該內容來自：
- 真實PDF經Textract或其他OCR抽取
- 使用者於前端表單直接輸入
- 本階段Demo Error Case之人工建構（見下方）

驗證引擎（`RuleValidator`等）**皆以相同方式處理**，不關心資料來源，符合
「LLM/OCR負責理解，Deterministic Engine負責正確性」之原則。

## 未來Textract整合路徑（設計預留，非本階段實作）

```
Textract回傳之表格/表單資料
  -> TextractAdapter.parse()（未來新增，本階段不存在）
  -> SubmittedFormData（本階段已定義，介面穩定）
  -> AuditEngine.review()（本階段已實作，無需修改）
```

## Demo Error Case 資料建構方式

`data/demo_errors/build_demo_submission.py` **未手動輸入任何模擬OCR結果**，
而是：
1. 呼叫真實`RuleEngine`，以Golden Case之真實原始值（如主要道路寬度18M）
   計算出「正確」的`SubmittedFormData`基準（每一欄位皆與系統deterministic
   核算結果一致）。
2. **僅**對3個demo案例對應之3個特定欄位，覆寫為刻意錯誤之提交值。

此設計確保：
- 47個正常欄位之「Passed」判定，是系統對「真實正確提交」的正確辨識，
  非測試作弊（若驗證邏輯本身有誤，這47筆會連帶失敗，而非虛假通過）。
- 3個demo案例之錯誤值，可追溯至具體的Golden Case真實數字對照
  （見`build_demo_submission.py`檔案開頭docstring之逐案說明），
  非憑空捏造之測試數字。
