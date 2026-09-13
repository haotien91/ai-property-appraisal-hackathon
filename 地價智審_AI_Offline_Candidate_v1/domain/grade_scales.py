"""Grade → position in the 評價基準明細表 matrix (1 = best) per regional factor.

Read from the rule pack itself, so 表3's side columns and 表5-1's 優劣等級
number cell print the same 「等級序號／等級數」 the matrix defines (e.g.
都市計畫內外 優 = 1 of 2, 主要道路寬度 普通 = 3 of 5)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The form's field_id differs from the mechanical rule-prefix derivation here.
_FIELD_ALIASES = {"regional_other_factors": "regional_other"}


@lru_cache(maxsize=8)
def regional_grade_scales(profile_id):
    """{field_id: {grade: code}}; empty when the profile has no rule pack."""
    path = ROOT / "data/rules/competition" / str(profile_id or "") / "regional_rules.json"
    if not profile_id or not path.is_file():
        return {}
    scales = {}
    for rule in json.loads(path.read_text("utf-8"))["rules"]:
        field_id = "regional_" + rule["rule_id"].rsplit("-", 1)[0].removeprefix("REG-").lower()
        field_id = _FIELD_ALIASES.get(field_id, field_id)
        scales.setdefault(field_id, {})[rule["grade"]] = int(rule["grade_code"])
    return scales


def grade_code(scales, field_id, grade):
    return (scales.get(field_id) or {}).get(grade) if grade else None


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def table3_grade_values(table51_analysis, segment_code, profile_id=None):
    """{"grade_code.<field_id>": n, "grade_count.<field_id>": levels} for one
    segment's 表3 page, from that segment's own Table 5-1 grades (the base
    segment's base grades). Accepts the analysis model or its JSON dict."""
    scales = regional_grade_scales(profile_id or _get(table51_analysis, "rule_profile_id"))
    is_base = segment_code == _get(table51_analysis, "base_segment_code")
    values = {}
    for comparison in _get(table51_analysis, "comparisons") or []:
        if not is_base and _get(comparison, "comparable_segment_code") != segment_code:
            continue
        for result in _get(comparison, "factor_results") or []:
            field_id = _get(result, "field_id")
            code = grade_code(scales, field_id, _get(result, "base_grade" if is_base else "comparable_grade"))
            if code is not None and f"grade_code.{field_id}" not in values:
                values[f"grade_code.{field_id}"] = code
                values[f"grade_count.{field_id}"] = len(scales[field_id])
    return values
