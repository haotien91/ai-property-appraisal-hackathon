# -*- coding: utf-8 -*-
"""
Regenerates frontend/*/mock/review_result.json from the REAL Demo Error
Case (tests/test_smart_review.py's fixture recipe: build_demo_submission.py
+ AuditEngine.review()), instead of the "all Passed" placeholder that was
previously checked in.

Why this exists: docs/phase8a/demo_script.md's Step 7 pitch is specifically
"the system catches 3 deliberately-injected errors with downstream impact
traced" -- but the frontend's review_result.json mock only ever contained
48 Passed / 0 Error issues, so clicking through the live browser demo could
never actually show that headline feature; it only existed inside the
pytest suite. This script closes that gap by running the exact same real
engines the tests already verify against, and serializing their real
output -- not hand-authoring a JSON file that merely LOOKS like engine
output.

Usage: py scripts/build_demo_review_result_mock.py
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "engine", os.path.join("data", "golden"), os.path.join("data", "demo_errors")):
    sys.path.insert(0, os.path.join(REPO_ROOT, p) if p else REPO_ROOT)

from rule_engine import RuleEngine  # noqa: E402
from engine.audit_engine import AuditEngine  # noqa: E402
from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402
from build_demo_submission import build_submitted_form_with_demo_errors  # noqa: E402

OUTPUT_PATHS = [
    os.path.join(REPO_ROOT, "frontend", "app", "frontend", "mock", "review_result.json"),
    os.path.join(REPO_ROOT, "frontend", "mock", "review_result.json"),
]


def main() -> int:
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        regional_rules = json.load(f)["rules"]
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        individual_rules = json.load(f)["rules"]
    rule_engine = RuleEngine(regional_rules + individual_rules)

    submitted = build_submitted_form_with_demo_errors(rule_engine, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)
    audit = AuditEngine(rule_engine, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
    result = audit.review(GOLDEN_CASE, submitted, BASE_REGIONAL, COMP_REGIONAL)

    payload = json.loads(result.model_dump_json())

    for path in OUTPUT_PATHS:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"寫入 {path}")

    print(
        f"\ncase_no={payload['case_no']}  "
        f"passed={payload['passed_count']}  error={payload['error_count']}  "
        f"warning={payload['warning_count']}  missing={payload['missing_count']}  "
        f"inconsistent={payload['inconsistent_count']}  low_confidence={payload['low_confidence_count']}"
    )
    non_passed = [i for i in payload["issues"] if i["issue_type"] != "Passed"]
    print(f"非Passed問題數：{len(non_passed)}")
    for i in non_passed:
        print(f"  - [{i['severity']}] {i['issue_type']}: {i['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
