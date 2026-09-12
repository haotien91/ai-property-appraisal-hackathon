# -*- coding: utf-8 -*-
"""
build_shulin_official_pdf_templates.py — OFFICIAL-SIX-PAGE-PDF-E1 Task 1/2.

BUILD-TIME ONLY script (run manually by a developer on a machine with
Microsoft Excel installed; NEVER imported or invoked by any Lambda
handler or by pdf/shulin_official_pdf_renderer.py). Exports exactly ONE
named worksheet from each of the 3 official Shulin competition XLSX
sources to a standalone, single-page blank PDF template, using Excel COM
automation (win32com) -- the ONLY tool available in this environment
capable of faithfully rendering an XLSX sheet's real layout (borders,
merged cells, CJK fonts, page setup/print area/scale) without a runtime
LibreOffice/Excel dependency: this script runs ONCE, offline, and its
OUTPUT (a static PDF file) is what gets committed and read at runtime.

Each source workbook is opened ReadOnly and closed WITHOUT saving --
the original XLSX is never modified. Only ONE worksheet is ever
exported per call (Worksheet.ExportAsFixedFormat, never
Workbook.ExportAsFixedFormat) -- the other ~21 sheets in each workbook
(other 查估書表 form types, irrelevant to this competition case) are
never touched, matching TASK 1's WHOLE_WORKBOOK_EXPORT_USED=NO
requirement.

The 3 outputs are DERIVED_RUNTIME_TEMPLATEs, not the OFFICIAL_XLSX_
LAYOUT_SOURCE itself -- data/templates/shulin/shulin_template_identity.
json records, for each one, the exact source XLSX filename + its own
sha256, the derived PDF's own sha256, and build_method=
"MICROSOFT_EXCEL_COM_BUILD_TIME_EXPORT" so nobody downstream can mistake
this for an original official government PDF. pdf/shulin_official_pdf_
renderer.py re-checks derived_pdf_sha256 on every render call (mirroring
the existing Jinshan TemplateLayoutUnconfirmedError pattern in
pdf/official_pdf_renderer.py) and refuses to render if the committed PDF
file has drifted from what this script produced.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)

_SRC_DIR = os.path.join(_REPO_ROOT, "data", "sources", "competition", "shulin_residential_2026")
_OUT_DIR = os.path.join(_REPO_ROOT, "data", "templates", "shulin")

TEMPLATE_VERSION = "v1"
BUILD_METHOD = "MICROSOFT_EXCEL_COM_BUILD_TIME_EXPORT"

# (template_id, source xlsx filename, source sheet name, output pdf filename)
TARGETS = [
    (
        "shulin_table3_blank_v1",
        "表3地價區段勘查表.xlsx",
        "表3區段勘查表",
        "shulin_table3_blank_v1.pdf",
    ),
    (
        "shulin_table51_blank_v1",
        "表5影響地價區域因素分析明細表(住宅用地).xlsx",
        "表5-1區域因素明細表(住)",
        "shulin_table51_blank_v1.pdf",
    ),
    (
        "shulin_table4_blank_v1",
        "表4比較法調查估價表.xlsx",
        "表4比較法調查估價表",
        "shulin_table4_blank_v1.pdf",
    ),
]


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_all() -> dict:
    import win32com.client as win32  # local import: this module is build-time-only,
    # never imported by the runtime renderer/handler -- keeping the import inside the
    # function (rather than at module scope) means a stray `import build_shulin_
    # official_pdf_templates` from application code would fail loudly if pywin32 is
    # unavailable in that environment, rather than silently succeeding.

    os.makedirs(_OUT_DIR, exist_ok=True)

    excel = win32.Dispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.AutomationSecurity = 1  # msoAutomationSecurityLow -- no macro-security prompt for a plain data workbook
    identity: dict = {}
    try:
        for template_id, src_name, sheet_name, out_name in TARGETS:
            src_path = os.path.join(_SRC_DIR, src_name)
            out_path = os.path.join(_OUT_DIR, out_name)
            source_xlsx_sha256 = _sha256(src_path)

            wb = excel.Workbooks.Open(
                Filename=src_path, ReadOnly=True, UpdateLinks=0, IgnoreReadOnlyRecommended=True,
            )
            try:
                if sheet_name not in [s.Name for s in wb.Worksheets]:
                    raise RuntimeError(f"{src_name!r} has no sheet named {sheet_name!r}")
                ws = wb.Worksheets(sheet_name)
                ws.ExportAsFixedFormat(0, out_path)  # 0 = xlTypePDF; single-sheet export only
            finally:
                wb.Close(SaveChanges=False)

            derived_pdf_sha256 = _sha256(out_path)
            identity[template_id] = {
                "template_id": template_id,
                "template_version": TEMPLATE_VERSION,
                "source_xlsx": src_name,
                "source_sheet": sheet_name,
                "source_xlsx_sha256": source_xlsx_sha256,
                "derived_pdf_sha256": derived_pdf_sha256,
                "derived_pdf_filename": out_name,
                "build_method": BUILD_METHOD,
                "build_timestamp": datetime.now(timezone.utc).isoformat(),
                "provenance_note": (
                    "This PDF is a DERIVED_RUNTIME_TEMPLATE mechanically exported from the "
                    "single named worksheet above via Microsoft Excel COM automation at "
                    "build time. It is NOT an original official government PDF and must "
                    "never be presented as one."
                ),
            }
    finally:
        excel.Quit()

    identity_path = os.path.join(_OUT_DIR, "shulin_template_identity.json")
    with open(identity_path, "w", encoding="utf-8") as f:
        json.dump(identity, f, ensure_ascii=False, indent=2)
    return identity


if __name__ == "__main__":
    result = build_all()
    for tid, info in result.items():
        print(f"{tid}: {info['derived_pdf_filename']} sha256={info['derived_pdf_sha256'][:12]}...")
