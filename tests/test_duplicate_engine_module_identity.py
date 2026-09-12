# -*- coding: utf-8 -*-
"""
SHULIN-COMPETITION-RULE-PACK-A2-FINAL-GATE-1 Task 2.

Found during this gate: engine/form_completion_engine.py and engine/
extraction_to_submitted_form.py imported their sibling engine modules via
the QUALIFIED "engine.xxx" path, while EVERY real production handler
(analyze.py/complete_form.py/review.py/document_confirm.py/document_
extract.py) imports the SAME modules via the BARE "xxx" path (engine/ is
placed directly on sys.path -- see runtime_paths.bootstrap()). Python
treats "grade_engine" and "engine.grade_engine" (etc.) as two entirely
separate module objects with two separate exception/model classes of the
same name, even though they come from the same source file on disk.

This silently broke complete_form.py's/review.py's intended graceful
degradation: FormCompletionEngine's `except (GradeEngineError,
AdjustmentEngineError)` never matched the actual exception instances
raised by a GradeEngine built (by complete_form.py/review.py) from the
bare module -- so any genuinely-missing rule (e.g. Shulin's individual
floor_area_ratio, which deliberately has no rule record) crashed the
Lambda handler with an UNHANDLED exception instead of surfacing
FieldStatus.MANUAL_REVIEW_REQUIRED. Fixed by making form_completion_
engine.py's and extraction_to_submitted_form.py's own internal imports
bare, matching every real caller.

This file locks in two things:
1. The production import graph, when loaded exactly as a real Lambda
   invocation would load it (handler first), never ends up with both a
   bare and a qualified copy of the same engine module in sys.modules.
2. The concrete failure mode this caused (a GradeEngineError from a
   FormCompletionEngine-driven grade must be caught, not propagate) does
   not regress -- a direct, minimal repro, independent of the larger
   Shulin-specific real-handler test in test_shulin_a2_final_gate.py.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules known to be imported bare by at least one real production caller
# (backend/handlers/*.py) -- these must NEVER also exist under sys.modules
# as "engine.<name>" once a full production import chain has been loaded.
_BARE_PRODUCTION_MODULES = [
    "grade_engine", "adjustment_engine", "calculation_engine",
    "comparable_selection_engine", "form_completion_engine",
    "audit_engine", "human_confirmation", "extraction_to_submitted_form",
    "rule_engine", "geo_distance_engine",
]


@pytest.fixture()
def _fresh_sys_modules(monkeypatch):
    """Import identity is a sys.modules-global concern -- start from a
    clean slate for the specific module names under test so an earlier
    test file's imports (bare or qualified) in the SAME pytest session
    can't mask a real regression here, and so this test doesn't leave
    residue for whatever runs after it."""
    to_clear = [
        name for name in list(sys.modules)
        if name in _BARE_PRODUCTION_MODULES or name.startswith("engine.")
    ]
    saved = {name: sys.modules.pop(name) for name in to_clear}
    yield
    for name in list(sys.modules):
        if name in _BARE_PRODUCTION_MODULES or name.startswith("engine."):
            del sys.modules[name]
    sys.modules.update(saved)


class TestNoDuplicateEngineModuleIdentityInProductionImportGraph:
    def test_full_handler_import_chain_never_double_loads_engine_modules(self, _fresh_sys_modules):
        """Import every real production handler that transitively pulls in
        the full engine/ dependency graph (complete_form.py covers Grade/
        Adjustment/Calculation/FormCompletion/ComparableSelection; review.py
        additionally covers Audit/HumanConfirmation/ExtractionToSubmitted
        Form/RuleValidator/RoadWidth/LandUseRatio), then assert no bare
        engine module ALSO exists under its "engine.<name>" qualified key."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
        sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
        sys.path.insert(0, REPO_ROOT)

        import complete_form  # noqa: F401
        import review  # noqa: F401
        import analyze  # noqa: F401

        duplicated = [
            name for name in _BARE_PRODUCTION_MODULES
            if name in sys.modules and f"engine.{name}" in sys.modules
        ]
        assert duplicated == [], (
            f"these modules are loaded under BOTH a bare and an 'engine.' qualified "
            f"identity in the same process -- exception/model classes raised under one "
            f"identity will never be caught by an `except`/`isinstance` written against "
            f"the other: {duplicated}"
        )

    def test_grade_engine_error_class_identity_is_singular(self, _fresh_sys_modules):
        """The concrete class-identity check, directly: however grade_
        engine.py ends up on sys.path, GradeEngineError as seen by
        form_completion_engine.py (imported bare, matching its real
        production callers) must be the SAME class object grade_engine.py
        itself raises -- not a same-named but distinct class."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
        sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
        sys.path.insert(0, REPO_ROOT)

        import grade_engine
        import form_completion_engine

        assert form_completion_engine.GradeEngineError is grade_engine.GradeEngineError
        assert "engine.grade_engine" not in sys.modules


def _make_evidence():
    from domain.models import Evidence, SourceType
    return Evidence(source="test", source_type=SourceType.AI_ASSISTED_FILL)


def test_unresolvable_individual_factor_degrades_gracefully_standalone():
    """Standalone function form (avoids the placeholder class above being
    collected by pytest) -- genuinely exercises complete_table4_individual_
    factors() with a factor name that has NO individual rule record at all
    in the legacy Jinshan static pack, proving the fix independent of any
    Shulin-specific data."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo_root, "engine"))
    sys.path.insert(0, repo_root)

    import json
    from rule_engine import RuleEngine
    from grade_engine import GradeEngine
    from adjustment_engine import AdjustmentEngine
    from calculation_engine import CalculationEngine
    from form_completion_engine import FormCompletionEngine
    from domain.models import CompetitionCase, FactorInput, PartyRole

    with open(os.path.join(repo_root, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(repo_root, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    engine = RuleEngine(reg + ind)
    fce = FormCompletionEngine(GradeEngine(engine), AdjustmentEngine(engine), CalculationEngine())

    ev = _make_evidence()
    case = CompetitionCase(
        case_no="DUP-IDENTITY-001", appraisal_period="1140901", appraisal_base_date="1140901",
        segment_code="P002-00", segment_scope="測試區段", city="新北市", district="金山區",
        land_use_type="商業用地", base_parcel_id="TBD", comparable_ids=["comp1"],
        base_parcel_factors=[FactorInput(field_id="individual_no_such_factor", factor="不存在的因素",
                                          raw_value=1, unit=None, evidence=ev)],
        comparable_factors={"comp1": [FactorInput(field_id="individual_no_such_factor", factor="不存在的因素",
                                                    raw_value=2, unit=None, evidence=ev)]},
    )

    fields, adjustments = fce.complete_table4_individual_factors(case, "comp1")
    assert len(fields) == 1
    assert fields[0].status.value == "MANUAL_REVIEW_REQUIRED"
    assert adjustments == []
