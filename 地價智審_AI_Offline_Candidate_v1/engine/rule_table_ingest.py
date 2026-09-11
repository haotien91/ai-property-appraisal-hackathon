# -*- coding: utf-8 -*-
"""
RuleTableIngest — converts a NEW 評價基準明細表 (given at competition time,
for a segment/land-use category the team has never seen before) from three
plain CSV files into rule_schema.json-conformant rule records that
RuleEngine can consume directly.

Why CSV, not raw JSON authoring or a bespoke UI:
- Officials themselves described "AI/system produces an Excel file, a human
  fills it in, then it gets used" as an ACCEPTABLE (if not the highest-
  scoring) workflow for the estimation FORM output (逐字稿 [00:19:04-00:19:11]).
  This module applies the identical pattern to the RULE TABLE input side:
  whether a human transcribes the new 評價基準明細表 by hand under time
  pressure, or an AI (e.g. Bedrock reading the PDF) drafts the CSV rows,
  the three files below are simple enough to fill without knowing the
  rule_schema.json structure or JSON syntax, and this module does the
  (deterministic, testable-offline) conversion + structural bookkeeping.
- This is deliberately independent of any live LLM/Bedrock call, so it is
  fully testable in an environment with no AWS access. A future Bedrock-
  based "read the PDF and draft these three CSVs" adapter can sit in front
  of this module without changing it at all (same Adapter Pattern already
  used by providers/ and pdf/ — see docs/phase6/document_extraction_spec.md).

Three input files, one row per...:

  factors.csv   — one row per FACTOR (columns: rule_id_prefix, version, city,
                  district, land_use_type, category, factor, value_type,
                  unit, direction, effective_date, source_document,
                  source_page, source_note)
  bands.csv     — one row per (FACTOR, GRADE) (columns: rule_id_prefix, grade,
                  grade_code, grade_label, lower_bound, upper_bound,
                  lower_inclusive, upper_inclusive, anomaly_flag)
  matrix.csv    — one row per (FACTOR, from_grade_code, to_grade_code)
                  (columns: rule_id_prefix, from_grade_code, to_grade_code,
                  adjustment_pct)

`rule_id_prefix` is the join key across all three files (e.g.
"REG-ZONING_INSIDE_OUTSIDE" or "IND-BUILDING_COVERAGE_RATIO_INDIVIDUAL").
Output rule_id per grade row is "<prefix>-<01-based sequence in ascending
grade_code order, zero-padded to 2 digits>", matching the existing
data/rules/*.json convention exactly.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Union


class RuleTableCsvError(Exception):
    """Raised for structural CSV problems (missing join key, malformed row)
    that make conversion impossible -- as opposed to RuleTableValidator
    issues, which are business-rule problems in an already-well-formed
    output. Never silently skip a bad row."""
    pass


def _read_csv_rows(source: Union[str, io.StringIO]) -> List[Dict[str, str]]:
    if isinstance(source, str):
        with open(source, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    return list(csv.DictReader(source))


def _parse_optional_float(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    if raw == "":
        return None
    return float(raw)


def _parse_optional_bool(raw: str) -> Optional[bool]:
    raw = (raw or "").strip().upper()
    if raw == "":
        return None
    if raw in ("TRUE", "T", "1", "YES", "Y"):
        return True
    if raw in ("FALSE", "F", "0", "NO", "N"):
        return False
    raise RuleTableCsvError(f"無法解析布林值：{raw!r}（應為 TRUE/FALSE）")


def build_rules_from_csv(
    factors_source: Union[str, io.StringIO],
    bands_source: Union[str, io.StringIO],
    matrix_source: Union[str, io.StringIO],
) -> List[Dict[str, Any]]:
    """Main entry point. Returns a flat list of rule records, one per
    (factor, grade) row -- the exact shape RuleEngine's constructor and
    rule_schema.json both expect. Raises RuleTableCsvError for structural
    problems (e.g. a matrix row referencing an unknown rule_id_prefix);
    does NOT perform business-rule validation (matrix squareness, symmetry,
    max_adjustment correctness, etc.) -- that is RuleTableValidator's job,
    kept separate so this function's failure mode is always "can't even
    build a record" rather than "built a record that might be wrong"."""
    factor_rows = _read_csv_rows(factors_source)
    band_rows = _read_csv_rows(bands_source)
    matrix_rows = _read_csv_rows(matrix_source)

    if not factor_rows:
        raise RuleTableCsvError("factors.csv 沒有任何資料列")

    factors_by_prefix: Dict[str, Dict[str, str]] = {}
    for row in factor_rows:
        prefix = (row.get("rule_id_prefix") or "").strip()
        if not prefix:
            raise RuleTableCsvError(f"factors.csv 有一列缺少 rule_id_prefix：{row}")
        if prefix in factors_by_prefix:
            raise RuleTableCsvError(f"factors.csv 中 rule_id_prefix={prefix!r} 重複出現")
        factors_by_prefix[prefix] = row

    bands_by_prefix: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in band_rows:
        prefix = (row.get("rule_id_prefix") or "").strip()
        if not prefix:
            raise RuleTableCsvError(f"bands.csv 有一列缺少 rule_id_prefix：{row}")
        if prefix not in factors_by_prefix:
            raise RuleTableCsvError(
                f"bands.csv 引用了 factors.csv 中不存在的 rule_id_prefix={prefix!r}"
            )
        bands_by_prefix[prefix].append(row)

    for prefix in factors_by_prefix:
        if not bands_by_prefix.get(prefix):
            raise RuleTableCsvError(f"factor {prefix!r} 在 bands.csv 中沒有任何等級列")

    matrix_by_prefix: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)
    for row in matrix_rows:
        prefix = (row.get("rule_id_prefix") or "").strip()
        if prefix not in factors_by_prefix:
            raise RuleTableCsvError(
                f"matrix.csv 引用了 factors.csv 中不存在的 rule_id_prefix={prefix!r}"
            )
        from_code = (row.get("from_grade_code") or "").strip()
        to_code = (row.get("to_grade_code") or "").strip()
        pct_raw = (row.get("adjustment_pct") or "").strip()
        if not from_code or not to_code or pct_raw == "":
            raise RuleTableCsvError(f"matrix.csv 有一列欄位不完整：{row}")
        matrix_by_prefix[prefix].setdefault(from_code, {})[to_code] = float(pct_raw)

    rules: List[Dict[str, Any]] = []
    for prefix, factor_row in factors_by_prefix.items():
        bands = sorted(
            bands_by_prefix[prefix], key=lambda b: int((b.get("grade_code") or "0").strip())
        )
        matrix = matrix_by_prefix.get(prefix, {})
        max_adjustment = max(
            (abs(v) for row in matrix.values() for v in row.values()), default=0.0
        )

        for seq, band in enumerate(bands, start=1):
            rule_id = f"{prefix}-{seq:02d}"
            rules.append({
                "rule_id": rule_id,
                "version": factor_row.get("version", "1.0"),
                "city": factor_row.get("city", ""),
                "district": factor_row.get("district", ""),
                "land_use_type": factor_row.get("land_use_type", ""),
                "category": factor_row.get("category", ""),
                "factor": factor_row.get("factor", ""),
                "value_type": factor_row.get("value_type", ""),
                "unit": (factor_row.get("unit") or "").strip() or None,
                "direction": factor_row.get("direction", ""),
                "lower_bound": _parse_optional_float(band.get("lower_bound", "")),
                "upper_bound": _parse_optional_float(band.get("upper_bound", "")),
                "lower_inclusive": _parse_optional_bool(band.get("lower_inclusive", "")),
                "upper_inclusive": _parse_optional_bool(band.get("upper_inclusive", "")),
                "grade": band.get("grade", ""),
                "grade_code": int(band.get("grade_code", "0")),
                "grade_label": (band.get("grade_label") or "").strip() or None,
                "adjustment_matrix": matrix,
                "max_adjustment": max_adjustment,
                "effective_date": factor_row.get("effective_date", ""),
                "source_document": factor_row.get("source_document", ""),
                "source_page": factor_row.get("source_page", ""),
                "source_note": factor_row.get("source_note", ""),
                "anomaly_flag": (band.get("anomaly_flag") or "").strip() or None,
            })

    return rules
