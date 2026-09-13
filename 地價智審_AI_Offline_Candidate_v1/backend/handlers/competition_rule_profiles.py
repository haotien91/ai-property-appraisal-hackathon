# -*- coding: utf-8 -*-
"""
competition_rule_profiles.py — SHULIN-COMPETITION-RULE-PACK-A2 Task 10/13.

The SINGLE source of truth for "what does a valid CONFIRMED CaseRulePackage
for competition profile X look like". A standalone module (rather than
living inside rule_engine_factory.py or case_rule_repository.py) purely to
avoid a circular import: rule_engine_factory.py already imports FROM
case_rule_repository.py, so a registry needed by BOTH must live somewhere
neither of them is the source of, or one direction would import the other.

Adding a new competition profile in a future round means adding ONE entry
here -- never scattering hardcoded district/land_use_type/source_sha256
checks across rule_engine_factory.py and case_rule_repository.py
separately (they would drift).
"""
from __future__ import annotations

# profile_id -> the exact source identity a CONFIRMED CaseRulePackage for
# that profile must declare in its own metadata["competition_profile"]
# (see case_rule_repository.py::confirm()'s CONFIRM-time gate) and that
# rule_engine_factory.py::build_rule_engine_for_case() re-checks (defense
# in depth) before ever resolving a RuleEngine from it.
COMPETITION_RULE_PROFILE_REGISTRY = {
    "shulin_residential_2026": {
        "district": "樹林區",
        "land_use_type": "普通住宅用地",
        "source_document": "評價基準明細表.pdf",
        "source_sha256": "a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c",
    },
}
