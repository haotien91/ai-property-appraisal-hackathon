# -*- coding: utf-8 -*-
"""
cross_check_teammate_central_data.py — STEP 4 §2 Central Maximum
cross-check (docs/audit/TEAMMATE_RULE_DATA_INTEGRATION_REPORT.md).

Parses the teammate's 5 official 內政部104年令 .doc attachments
(docs/incoming_rule_sources/附件一~四*.doc + 1040130*.doc) via `antiword`
(the same tool this project's own audit work already used to read these
files -- see docs/phase8a and the Step 1 audit) and cross-checks every
(factor, land_use_type/subgrade) cell against the ALREADY-DIGITIZED
`data/rules/central_max_adjustment_range.json` (418 entries).

This is deliberately a CROSS-CHECK, not a re-import: the teammate .doc
files are classified CENTRAL_MAXIMUM / PROVENANCE_SOURCE (docs/audit/
TEAMMATE_RULE_SOURCE_CLASSIFICATION.md), and this script's job is only to
confirm (or refute) that the existing digitized registry already
represents the SAME official source correctly -- never to build a second
central registry (`central_factor_maximums.json` or similar is explicitly
forbidden by this round's scope).

The teammate .doc tables use a DIFFERENT, much simpler layout than the
evaluation-standard PDF Phase 3A's Importer targets: no grade bands, no
adjustment matrix -- one row per factor, one column per land-use-subgrade,
a single "max %" (or "-" for not-applicable) value per cell. antiword
happens to render these specific tables as clean pipe-delimited rows
(unlike the evaluation-standard PDF's scrambled stream order), so this
uses its own small, dedicated parser rather than reusing engine/
evaluation_standard_importer.py's matrix-block state machine, which
targets a structurally different table shape.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = os.path.join(REPO_ROOT, "docs", "incoming_rule_sources")
CENTRAL_REGISTRY_PATH = os.path.join(REPO_ROOT, "data", "rules", "central_max_adjustment_range.json")

REGIONAL_FILES = [
    ("附件一影響住宅用地區域因素評價基準表.doc", "住宅用地",
     ["高級住宅用地", "中級住宅用地", "普通住宅用地", "村里鄰住宅用地"]),
    ("附件二影響商業用地區域因素評價基準表.doc", "商業用地",
     ["高度商業用地", "中度商業用地", "普通商業用地", "村里鄰商業用地"]),
    ("附件三影響工業用地區域因素評價基準表.doc", "工業用地",
     ["大規模工業用地", "中小規模工業用地"]),
    ("附件四影響農業用地區域因素評價基準表.doc", "農業用地", [None]),
]
INDIVIDUAL_FILE = ("1040130影響地價個別因素評價基準表(發布).doc",
                    ["住宅用地", "商業用地", "工業用地", "農業用地", "其他用地"])

_HEADER_ROW_NAMES = {"細項", "主要項目"}

# 舖 (U+8216) vs 鋪 (U+92EA) -- both mean "to pave/lay". Observed as a
# genuine transcription/font variant between antiword's extraction of the
# teammate .doc files (uses 舖) and the existing central registry (uses
# 鋪) -- see docs/audit/TEAMMATE_RULE_SOURCE_CLASSIFICATION.md. Folded
# ONLY in a second, explicitly-labeled comparison pass -- never silently
# merged into the primary exact-match comparison, so the finding stays
# visible rather than masked.
_VARIANT_CHAR_MAP = {"舖": "鋪"}


class AntiwordNotAvailableError(Exception):
    pass


def antiword_available() -> bool:
    return shutil.which("antiword") is not None


def extract_doc_text(doc_path: str) -> str:
    if not antiword_available():
        raise AntiwordNotAvailableError(
            "antiword not found on PATH -- required to read the teammate .doc attachments "
            "(docs/incoming_rule_sources/*.doc). No fallback: an honest failure here, not a "
            "guessed/empty extraction."
        )
    result = subprocess.run(["antiword", "-m", "UTF-8.txt", doc_path],
                             capture_output=True, text=True, encoding="utf-8", check=True)
    return result.stdout


def parse_teammate_table(text: str, n_value_cols: int) -> List[Dict[str, Any]]:
    """Parses antiword's pipe-delimited rendering of one of these tables
    into a flat list of {"item_name": str, "values": [str, ...]}. Handles
    word-wrapped item names (a row with a non-empty second column but no
    values is a continuation of the PREVIOUS data row's item_name, e.g.
    "建築基地改良（整平或填挖基地...舖築" + "道路、埋設管道...其他改良")."""
    rows: List[Dict[str, Any]] = []
    pending: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 + n_value_cols:
            continue
        col1 = cells[1]
        vals = cells[2:2 + n_value_cols]
        has_values = any(v != "" for v in vals)
        has_item = col1 != ""
        if has_values and has_item:
            rows.append({"item_name": col1, "values": list(vals)})
            pending = rows[-1]
        elif not has_values and has_item:
            if pending is not None:
                pending["item_name"] += col1
    return [r for r in rows if r["item_name"].strip() not in _HEADER_ROW_NAMES]


def norm_item(name: Optional[str], fold_variant_chars: bool = False) -> str:
    if name is None:
        return ""
    name = re.sub(r"^\d+\.", "", name.strip())
    name = re.sub(r"\s+", "", name)
    if fold_variant_chars:
        for a, b in _VARIANT_CHAR_MAP.items():
            name = name.replace(a, b)
    return name


def _teammate_value(raw: str) -> Optional[str]:
    """'-' means NOT_APPLICABLE (§3) -- normalized to None here, matching
    the central registry's own `max_range_pct=None` convention for
    DASH_NOT_APPLICABLE cells (domain.models.CentralMaxRangeCellState) --
    NEVER coerced to "0" (0% is a real, different, applicable value)."""
    raw = raw.strip()
    if raw == "" or raw == "-":
        return None
    return raw


def run_cross_check(central_entries: Optional[List[dict]] = None,
                     source_dir: str = SOURCE_DIR) -> Dict[str, Any]:
    """Returns a result dict: total_checked, total_match, total_mismatch,
    total_no_central_match, variant_char_matches (list), mismatches (list),
    no_central_match (list). Never raises for a data discrepancy -- only
    for antiword being unavailable (see extract_doc_text) or a source file
    missing, both genuine "this cross-check cannot run" conditions."""
    if central_entries is None:
        with open(CENTRAL_REGISTRY_PATH, encoding="utf-8") as f:
            central_entries = json.load(f)["entries"]

    result = {
        "total_checked": 0, "total_match": 0, "total_mismatch": 0, "total_no_central_match": 0,
        "variant_char_matches": [], "mismatches": [], "no_central_match": [],
    }

    def _check_cell(item_name: str, key_field: str, key_value, central_index: dict,
                     central_index_variant: dict, teammate_raw: str):
        result["total_checked"] += 1
        teammate_val = _teammate_value(teammate_raw)
        ce = central_index.get((norm_item(item_name), key_value))
        if ce is not None:
            central_val = ce.get("max_range_pct")
            if _values_equal(central_val, teammate_val):
                result["total_match"] += 1
            else:
                result["total_mismatch"] += 1
                result["mismatches"].append({
                    "item_name": item_name, key_field: key_value,
                    "central": central_val, "teammate": teammate_val,
                })
            return
        ce_variant = central_index_variant.get((norm_item(item_name, True), key_value))
        if ce_variant is not None:
            central_val = ce_variant.get("max_range_pct")
            match = _values_equal(central_val, teammate_val)
            result["variant_char_matches"].append({
                "item_name": item_name, key_field: key_value,
                "central": central_val, "teammate": teammate_val, "value_match": match,
            })
            if match:
                result["total_match"] += 1
            else:
                result["total_mismatch"] += 1
            return
        result["total_no_central_match"] += 1
        result["no_central_match"].append({"item_name": item_name, key_field: key_value, "teammate": teammate_val})

    for fname, land_use_type, subgrades in REGIONAL_FILES:
        text = extract_doc_text(os.path.join(source_dir, fname))
        rows = parse_teammate_table(text, len(subgrades))
        central_index = {(norm_item(e["item_name"]), e["land_use_subgrade"]): e
                          for e in central_entries if e["land_use_type"] == land_use_type and e["table_type"] == "regional"}
        central_index_variant = {(norm_item(e["item_name"], True), e["land_use_subgrade"]): e
                                  for e in central_entries if e["land_use_type"] == land_use_type and e["table_type"] == "regional"}
        for row in rows:
            for sub, val in zip(subgrades, row["values"]):
                _check_cell(row["item_name"], "land_use_subgrade", sub, central_index, central_index_variant, val)

    fname, land_use_types = INDIVIDUAL_FILE
    text = extract_doc_text(os.path.join(source_dir, fname))
    rows = parse_teammate_table(text, len(land_use_types))
    central_index = {(norm_item(e["item_name"]), e["land_use_type"]): e
                      for e in central_entries if e["table_type"] == "individual"}
    central_index_variant = {(norm_item(e["item_name"], True), e["land_use_type"]): e
                              for e in central_entries if e["table_type"] == "individual"}
    for row in rows:
        for lut, val in zip(land_use_types, row["values"]):
            _check_cell(row["item_name"], "land_use_type", lut, central_index, central_index_variant, val)

    return result


def _values_equal(a, b) -> bool:
    a_norm = None if a is None else str(a).strip()
    b_norm = None if b is None else str(b).strip()
    return a_norm == b_norm


if __name__ == "__main__":
    r = run_cross_check()
    print(f"TOTAL_CHECKED={r['total_checked']}")
    print(f"TOTAL_MATCH={r['total_match']}")
    print(f"TOTAL_MISMATCH={r['total_mismatch']}")
    print(f"TOTAL_NO_CENTRAL_MATCH={r['total_no_central_match']}")
    print(f"TOTAL_VARIANT_CHAR_MATCHES={len(r['variant_char_matches'])}")
    for m in r["mismatches"]:
        print(f"MISMATCH: {m}")
    for m in r["no_central_match"]:
        print(f"NO_CENTRAL_MATCH: {m}")
