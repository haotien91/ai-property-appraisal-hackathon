# -*- coding: utf-8 -*-
"""
PdfTemplate — Jinja2 HTML templates for the system-generated fallback PDF
report. Since 查估書表範本.pdf is not a real fillable PDF (see
pdf/coordinate_mapping.py docstring), this is a FALLBACK reproduction, not
a pixel-perfect replica of the official form. Every page carries a visible
banner disclosing this, per Phase 5's "需要清楚記錄Automatic/Manual" and
general "不得隱藏Known Limitation" principle.
"""
from __future__ import annotations
import jinja2

BASE_CSS = """
@page { size: A4; margin: 1.6cm; }
body { font-family: "Noto Sans CJK TC", "Noto Sans CJK SC", sans-serif; font-size: 10pt; color: #111; }
h1 { font-size: 15pt; margin-bottom: 2pt; }
h2 { font-size: 12pt; margin-top: 14pt; margin-bottom: 4pt; border-bottom: 1.5pt solid #333; padding-bottom: 2pt; }
.disclosure-banner { background: #fff3cd; border: 1pt solid #b8860b; padding: 6pt 8pt; font-size: 8.5pt; margin-bottom: 10pt; }
.meta { font-size: 9pt; color: #444; margin-bottom: 8pt; }
table { border-collapse: collapse; width: 100%; margin-bottom: 10pt; font-size: 8.5pt; }
th, td { border: 0.6pt solid #666; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #eee; font-weight: bold; }
tr.manual td { background: #fff8e1; }
.badge-manual { display: inline-block; background: #d9822b; color: white; font-size: 7.5pt; padding: 1pt 4pt; border-radius: 3pt; }
.badge-auto { display: inline-block; background: #2e7d32; color: white; font-size: 7.5pt; padding: 1pt 4pt; border-radius: 3pt; }
.footer-note { font-size: 7.5pt; color: #666; margin-top: 14pt; }
.trace { font-size: 7.5pt; color: #555; }
.trace .rule-id { white-space: nowrap; }
"""

_TEMPLATE_SRC = """
<html><head><meta charset="utf-8"><style>{{ css }}</style></head>
<body>
<div class="disclosure-banner">
本頁為系統自動產生之報表（Fallback Reproduction），並非官方查估書表範本原始版面複製。
官方查估書表範本.pdf 為非標準PDF（無AcroForm可填欄位），故本系統採「結構化結果 →
系統產出報表」之後備輸出方式（見 docs/phase5/pdf_output_spec.md）。標示
<span class="badge-auto">AUTOMATIC</span> 之欄位由Rule/Adjustment/Calculation Engine
deterministic產生；標示 <span class="badge-manual">MANUAL</span> 之欄位需估價師專業判斷或人工輸入，
系統不代為決定其值。
</div>
<h1>{{ form_title }}</h1>
<div class="meta">
案號：{{ case_no }}　區段編號：{{ segment_code }}　產出時間：{{ generated_at }}
</div>

{% for section_name, rows in sections.items() %}
<h2>{{ section_name }}</h2>
<table>
<tr><th style="width:26%">欄位</th><th style="width:12%">狀態</th><th style="width:16%">數值</th><th style="width:46%">追溯資訊（Rule/Formula/Source）</th></tr>
{% for row in rows %}
<tr class="{{ 'manual' if row.fill_mode == 'MANUAL' else '' }}">
  <td>{{ row.label }}<br/><span class="badge-{{ 'manual' if row.fill_mode == 'MANUAL' else 'auto' }}">{{ row.fill_mode }}</span></td>
  <td>{{ row.status }}</td>
  <td>{{ row.value if row.value is not none else 'N/A' }}</td>
  <td class="trace">
    {% if row.rule_id %}rule_id: <span class="rule-id">{{ row.rule_id }}</span><br/>{% endif %}
    {% if row.grade %}grade: {{ row.grade }}<br/>{% endif %}
    {% if row.formula %}formula: {{ row.formula }}<br/>{% endif %}
    {% if row.calculation %}calc: {{ row.calculation }}<br/>{% endif %}
    source: {{ row.source }}
    {% if row.warnings %}<br/><b>warnings:</b> {{ row.warnings|join('; ') }}{% endif %}
  </td>
</tr>
{% endfor %}
</table>
{% endfor %}

<div class="footer-note">
系統版本：{{ engine_versions }}　｜　本報表由 AI 輔助不動產估價案件審查系統自動產生，
最終結果須經估價師/審查人員核定後方為正式文件。
</div>
</body></html>
"""

_env = jinja2.Environment(autoescape=True)
FORM_TEMPLATE = _env.from_string(_TEMPLATE_SRC)


def render_form_html(form_title: str, case_no: str, segment_code: str,
                      generated_at: str, sections: dict, engine_versions: str) -> str:
    return FORM_TEMPLATE.render(
        css=BASE_CSS, form_title=form_title, case_no=case_no,
        segment_code=segment_code, generated_at=generated_at,
        sections=sections, engine_versions=engine_versions,
    )
