# -*- coding: utf-8 -*-
"""
verify_no_residual_text.py — confirms data/templates/official_appraisal_
form_v1.pdf's content stream (not merely its rendered appearance) no
longer contains text within any of the regions
scripts/build_clean_official_template.py actually redacted.

This checks EACH redaction_manifest.json entry's own recorded bbox
directly (not a page-wide substring search, which produces false
positives: short example values like "無"/"主要道路"/"普通" are also
SUBSTRINGS of legitimate STATIC LABELS elsewhere on the same page, e.g.
"有無禁止建築"/"主要道路寬度" -- a page-wide search cannot tell those
apart from a genuine residual). Checking the manifest's own bbox is
precise: that exact region was deliberately cleared, so ANY text found
there now is either a real bug (redaction failed) or a false positive
from PyMuPDF's word-splitting picking up an adjacent character that
happens to sit just across the bbox edge -- both worth a human look, so
this script prints them all rather than silently filtering.

This is a REAL content-stream check (`page.get_text("words")`, actual
extractable text), not a visual/pixel check -- a `page.draw_rect()`
"redaction" that only paints OVER text would still fail this script,
which is exactly the bug this script exists to catch (see scripts/
build_clean_official_template.py's module docstring for the incident
this fixed).
"""
from __future__ import annotations

import json
import os
import sys

import fitz

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    template_path = os.path.join(REPO_ROOT, "data", "templates", "official_appraisal_form_v1.pdf")
    manifest_path = os.path.join(REPO_ROOT, "data", "templates", "official_appraisal_form_v1.redaction_manifest.json")
    doc = fitz.open(template_path)
    manifest = json.load(open(manifest_path, encoding="utf-8"))

    findings = []
    for entry in manifest:
        page = doc[entry["page"]]
        rect = fitz.Rect(*entry["bbox"])
        words = page.get_text("words")
        # A word's CENTER (not mere bbox overlap) must fall inside the
        # redacted rect -- adjacent STATIC LABEL text (e.g. a row-number
        # margin column sitting right next to its own row's label, like
        # "21" beside "停車方便性") can have a word-bbox that grazes a
        # neighboring redaction rect's edge without the label itself
        # having been anywhere near where the redacted content was;
        # center-containment avoids flagging that as a false positive
        # while still catching genuine residual text (which would be
        # centered on the redacted content's own original position).
        found_text = "".join(
            w[4] for w in words
            if rect.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
        )
        if found_text.strip():
            findings.append({
                "page": entry["page"], "redacted_bbox": entry["bbox"],
                "original_text": entry.get("original_text"), "residual_text_found": found_text,
            })
    doc.close()

    if findings:
        print(f"RESIDUAL TEXT FOUND in {len(findings)} of {len(manifest)} redacted regions:")
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 1
    print("CLEAN_TEMPLATE_RESIDUAL_SAMPLE_FOUND=NO")
    print(f"Checked all {len(manifest)} redacted regions from the manifest: 0 residual hits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
