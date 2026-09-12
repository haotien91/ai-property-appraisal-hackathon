# Phase 8A — Backup Demo 操作指南

> 此為**現場故障備援方案**，非正式AWS Live Demo之替代宣稱。若AWS當日
> 網路/服務異常，此流程可獨立展示核心產品價值（Case→Data→Rule→Grade→
> Adjustment→Calculation→Form Completion→PDF→Smart Review）。

## 啟動步驟

```bash
# 1. 進入前端目錄，確認 Mock Mode 為預設（無需任何後端/AWS）
cd frontend/app
grep "MODE:" js/config.js   # 應顯示 MODE: "mock"

# 2. 啟動本地靜態伺服器
python3 -m http.server 8000

# 3. 瀏覽器開啟
open http://localhost:8000/index.html
```

## 展示流程（與`docs/phase8a/demo_script.md`相同故事線，純本地執行）

1. `index.html` → 點擊「建立案件」
2. `case-new.html` → 展示表單（Mock Mode下提交會導向既有Golden Case示範資料）
3. `case.html` → 顯示案件列表（讀取`frontend/mock/case.json`）
4. `data.html` → 顯示資料收集結果（讀取`frontend/mock/data_collection.json`）
5. `analysis.html` → 顯示Grade/Adjustment（讀取`frontend/mock/analysis.json`）
6. `form-fill.html` → 顯示完整Traceability（讀取`frontend/mock/form_completion.json`）
7. `pdf-preview.html` → 顯示PDF資訊（讀取`frontend/mock/pdf_result.json`）
8. `review.html` → **展示「系統能抓出3個刻意植入之錯誤」這個賣點**
   （讀取`frontend/mock/review_result.json`，此fixture是刻意竄改過的
   **Error Case展示資料**——45個未竄改欄位仍全數Passed，另3項為
   `docs/phase8a/error_case_showcase.md`記載之既有Case A/B/C，由
   AuditEngine對Golden Case+刻意竄改值重新執行取得，非本頁代表
   Golden Case本身有問題）
9. `result.html` → **展示Golden Case本身（未竄改）之乾淨最終結果**
   212,958元/M²，48 passed/0 error/0 inconsistent（讀取
   `frontend/mock/result.json`，與第8步是**不同資料/不同展示目的**，
   非同一次審查的前後狀態）

> ⚠️ **現場口述提醒（避免聽眾誤解系統前後矛盾）**：第8步與第9步刻意
> 使用兩份不同的展示資料，各自服務不同的賣點（「抓錯能力」vs
> 「乾淨過關的最終結果」）。若依序點過這兩頁，**務必口頭說明**：
> 「上一頁review.html刻意示範系統抓出3個刻意植入的錯誤；這一頁
> result.html顯示的是Golden Case本身、未經竄改的乾淨最終結果」，
> 避免聽眾誤以為review顯示3個issue、result卻顯示0 error是系統前後
> 不一致（bug）。這是Mock Mode下兩份獨立fixture的既定展示設計，
> 不是系統邏輯矛盾——見`docs/backlog.md`「OFFLINE-ACCEPTANCE-1
> Browser Demo 驗收發現」第3點之完整技術說明。

## PDF 現場即時產生（若需展示「真的能產生PDF」而非僅讀取Mock）

```bash
cd /path/to/repo
python3 << 'PYEOF'
import sys, os
sys.path.insert(0,'.'); sys.path.insert(0,'engine'); sys.path.insert(0,'data/golden'); sys.path.insert(0,'pdf')
from rule_engine import RuleEngine
import json
with open('data/rules/regional_rules.json',encoding='utf-8') as f: reg=json.load(f)['rules']
with open('data/rules/individual_rules.json',encoding='utf-8') as f: ind=json.load(f)['rules']
rule_engine = RuleEngine(reg+ind)
from engine.grade_engine import GradeEngine
from engine.adjustment_engine import AdjustmentEngine
from engine.calculation_engine import CalculationEngine
from engine.form_completion_engine import FormCompletionEngine
from golden_case_input import case, BASE_REGIONAL, COMP_REGIONAL
fce = FormCompletionEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine())
result = fce.complete_form(case, BASE_REGIONAL, COMP_REGIONAL)
from pdf.pdf_renderer import PdfRenderer
PdfRenderer().render_all_forms(result, case.case_no, case.segment_code, '/tmp/live_demo.pdf')
print("PDF產生完成：/tmp/live_demo.pdf")
PYEOF
open /tmp/live_demo.pdf
```

## Smart Review 現場即時偵測錯誤（若需展示「真的能抓錯」而非僅讀取Mock）

見`docs/phase8a/error_case_showcase.md`所附之完整可執行程式碼片段，可
現場複製貼上執行，即時展示3個Demo Error Case之偵測過程與結果。

## 若被詢問「這是不是就是正式AWS Demo」

**誠實回答範例**：「這是我們準備的離線備援展示，核心業務邏輯（規則引擎、
計算引擎、智慧審查）與AWS部署版本完全相同的程式碼，我們也準備了完整的
AWS部署腳本（`docs/phase7/aws_deployment_runbook.md`），只是尚未在
正式AWS帳號中執行部署，見`docs/phase8a/offline_readiness_report.md`
詳細記錄。」

## 檢查清單（現場開始展示前）

- [ ] `frontend/app/js/config.js`之`MODE`確認為`"mock"`
- [ ] `python3 -m http.server 8000`成功啟動且無錯誤
- [ ] 瀏覽器可正常開啟`index.html`並看到深色/粉紅主題正確渲染
- [ ] 9個頁面（index+8個App Pages）皆可透過navbar互相導覽
- [ ] （若需要）Python環境已安裝：pydantic, weasyprint（若需現場產生PDF）
