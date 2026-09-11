# -*- coding: utf-8 -*-
"""
Competition-day CLI: turn three filled-in CSV files (factors/bands/matrix,
see data/rules/templates/ for the format and a worked example) into a
rule_schema.json-conformant rule file RuleEngine can load directly.

Usage:
    py scripts/ingest_rule_table.py \
        --factors path/to/factors.csv \
        --bands path/to/bands.csv \
        --matrix path/to/matrix.csv \
        --out data/rules/new_segment_rules.json \
        --rule-set regional_rules

Exits with status 1 and prints every problem found (never just the first
one) if RuleTableValidator finds any ERROR-severity issue -- nothing is
written in that case. WARNING-severity issues are printed but do not block
the write, since they may be intentional (e.g. an official table that is
not perfectly anti-symmetric).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))

from rule_table_ingest import build_rules_from_csv, RuleTableCsvError  # noqa: E402
from rule_table_validator import RuleTableValidator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factors", required=True, help="factors.csv 路徑")
    parser.add_argument("--bands", required=True, help="bands.csv 路徑")
    parser.add_argument("--matrix", required=True, help="matrix.csv 路徑")
    parser.add_argument("--out", required=True, help="輸出的規則JSON路徑")
    parser.add_argument(
        "--rule-set", default="regional_rules",
        help="寫入輸出JSON外層 rule_set 欄位（例如 regional_rules 或 individual_rules）",
    )
    args = parser.parse_args()

    try:
        rules = build_rules_from_csv(args.factors, args.bands, args.matrix)
    except RuleTableCsvError as e:
        print(f"CSV 結構錯誤，無法轉換：{e}", file=sys.stderr)
        return 1

    issues = RuleTableValidator().validate(rules)
    for issue in issues:
        print(issue, file=sys.stderr)

    if RuleTableValidator.has_errors(issues):
        print(
            f"\n共發現 {len(issues)} 個問題，其中含ERROR，未寫入輸出檔案。"
            "請修正上述ERROR後重新執行。",
            file=sys.stderr,
        )
        return 1

    cities = {r["city"] for r in rules}
    districts = {r["district"] for r in rules}
    land_use_types = {r["land_use_type"] for r in rules}
    factor_count = len({r["factor"] for r in rules})

    output = {
        "schema_version": "1.0",
        "rule_set": args.rule_set,
        "city": next(iter(cities)) if len(cities) == 1 else sorted(cities),
        "district": next(iter(districts)) if len(districts) == 1 else sorted(districts),
        "land_use_type": next(iter(land_use_types)) if len(land_use_types) == 1 else sorted(land_use_types),
        "factor_count": factor_count,
        "rule_count": len(rules),
        "rules": rules,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    warning_count = sum(1 for i in issues if i.severity == "WARNING")
    print(
        f"轉換完成：{factor_count} 個因素、{len(rules)} 筆規則，"
        f"{warning_count} 個WARNING（已列印於上，非阻擋性）。已寫入 {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
