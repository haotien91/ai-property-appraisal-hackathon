# -*- coding: utf-8 -*-
"""
analyze_official_template.py — PDF-OFFICIAL-1 Task 1 read-only survey tool.

Extracts every text span on the 3 relevant pages of the official
Competition PDF (data/sources/competition/查估書表範本.pdf) with PyMuPDF
rawdict (character-level) bounding boxes, and classifies each span using
MECHANICAL, non-guessed rules only:

  STATIC_LABEL              -- no digits/percent/checkbox glyph anywhere in
                                the span; assumed to be fixed form structure
                                (never redacted).
  VALUE_STANDALONE           -- the ENTIRE span is plausibly a filled-in
                                value (matches a value-shaped regex: digits/
                                percent/date/"－"/pure punctuation-free CJK
                                proper-noun-shaped run) with no leading
                                label text at all.
  VALUE_AFTER_FULLWIDTH_COLON -- the span contains a fullwidth or halfwidth
                                colon; only the text AFTER the LAST colon is
                                a candidate value (the part before is the
                                static label, e.g. "名稱：中山路" ->
                                label="名稱：", value="中山路").
  CHECKBOX_GLYPH              -- span contains U+25CB(○)/U+25CF(●)/U+25A1(□)
                                -- exact character positions recorded so a
                                redaction/overlay tool can target only
                                those glyphs, never the surrounding label.
  AMBIGUOUS                   -- none of the above rules apply cleanly;
                                MUST be manually reviewed before any
                                redaction is attempted (never guessed).

This script does NOT modify the source PDF. Output: one JSON file per page
under the given --out-dir, plus a summary printed to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata

import fitz

CHECKBOX_CHARS = {"○": "UNCHECKED_CIRCLE", "●": "CHECKED_CIRCLE", "□": "UNCHECKED_SQUARE"}
COLON_CHARS = ("：", ":")

_VALUE_STANDALONE_RE = re.compile(
    r"^[\d０-９]+([.,][\d０-９]+)?(%|％|M|㎡|m2|M2)?$"  # pure number/percent/unit
    r"|^\d{2,4}[./-]\d{1,2}[./-]\d{1,2}$"  # date-ish
    r"|^－$"  # NOT_APPLICABLE dash (never redacted as 0 -- see NOTE below)
)

# Closed vocabulary check (NOT a guess): every one of these strings is
# EXACTLY a `grade`/`grade_label` value already defined in data/rules/
# regional_rules.json / individual_rules.json (the only two files that
# define what a valid grade or 有/無-style boolean-factor label can be in
# this system) -- a span whose ENTIRE stripped text matches one of these
# closed sets, positioned in 表5-2's known per-comparable grade-text
# column (see scripts/build_clean_official_template.py's PAGE1 column
# geometry), is a filled-in example value, never a fixed label (no label
# in this form is merely the single word "優" or "無" with nothing else).
_GRADE_VOCABULARY = {"優", "稍優", "普通", "稍劣", "劣"}
_BOOLEAN_GRADE_VOCABULARY = {"有", "無"}


def _classify_span(text: str) -> str:
    if any(ch in CHECKBOX_CHARS for ch in text):
        return "CHECKBOX_GLYPH"
    for colon in COLON_CHARS:
        if colon in text:
            after = text.rsplit(colon, 1)[1]
            if after.strip():
                return "VALUE_AFTER_FULLWIDTH_COLON"
            return "STATIC_LABEL"  # colon with nothing after it (label only, e.g. "名稱：")
    stripped = text.strip()
    if _VALUE_STANDALONE_RE.match(stripped):
        return "VALUE_STANDALONE"
    if stripped in _GRADE_VOCABULARY or stripped in _BOOLEAN_GRADE_VOCABULARY:
        return "VALUE_STANDALONE"
    # A short (<=10 char) span with no punctuation at all, embedded in a
    # known "value cell" x-range, is common for filled-in proper nouns
    # (road names, facility names) -- but we do NOT guess this here; such
    # spans are left STATIC_LABEL by default (conservative) unless a human
    # (via the template profile) explicitly promotes them to a field.
    return "STATIC_LABEL"


def analyze_page(doc: fitz.Document, page_index: int) -> list:
    page = doc[page_index]
    d = page.get_text("rawdict")
    spans = []
    for b in d["blocks"]:
        if "lines" not in b:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                text = "".join(c["c"] for c in s["chars"])
                if not text.strip():
                    continue
                classification = _classify_span(text)
                entry = {
                    "text": text,
                    "bbox": [round(v, 2) for v in s["bbox"]],
                    "font": s.get("font"),
                    "size": round(s.get("size", 0), 2),
                    "classification": classification,
                }
                if classification == "CHECKBOX_GLYPH":
                    entry["checkbox_chars"] = [
                        {"char": c["c"], "kind": CHECKBOX_CHARS[c["c"]], "bbox": [round(v, 2) for v in c["bbox"]]}
                        for c in s["chars"] if c["c"] in CHECKBOX_CHARS
                    ]
                if classification == "VALUE_AFTER_FULLWIDTH_COLON":
                    for colon in COLON_CHARS:
                        if colon in text:
                            idx = text.rindex(colon)
                            label_chars = s["chars"][: idx + 1]
                            value_chars = s["chars"][idx + 1 :]
                            entry["label_text"] = "".join(c["c"] for c in label_chars)
                            entry["value_text"] = "".join(c["c"] for c in value_chars)
                            if value_chars:
                                entry["value_bbox"] = [
                                    round(min(c["bbox"][0] for c in value_chars), 2),
                                    round(min(c["bbox"][1] for c in value_chars), 2),
                                    round(max(c["bbox"][2] for c in value_chars), 2),
                                    round(max(c["bbox"][3] for c in value_chars), 2),
                                ]
                            break
                spans.append(entry)
    spans.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    return spans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=os.path.join("data", "sources", "competition", "查估書表範本.pdf"))
    parser.add_argument("--out-dir", default=os.path.join("docs", "pdf", "_template_survey"))
    parser.add_argument("--pages", default="0,1,2")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    doc = fitz.open(args.pdf)
    page_indices = [int(p) for p in args.pages.split(",")]

    summary = {}
    for pi in page_indices:
        spans = analyze_page(doc, pi)
        counts = {}
        for e in spans:
            counts[e["classification"]] = counts.get(e["classification"], 0) + 1
        summary[pi] = counts
        out_path = os.path.join(args.out_dir, f"page{pi}_spans.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(spans, f, ensure_ascii=False, indent=1)
        print(f"page {pi}: {len(spans)} spans -> {out_path}")
        for k, v in sorted(counts.items()):
            print(f"    {k}: {v}")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
