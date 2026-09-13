# -*- coding: utf-8 -*-
"""
build_official_pdf_mock.py — FRONTEND-OFFICIAL-PDF-WIRING-1 Task 11.

Generates a Golden Case "Official PDF" mock artifact
(frontend/mock/pdf/official_form_Golden_Case.pdf, mirrored to frontend/
app/frontend/mock/pdf/ per this repo's existing nested-mock-path
convention) by actually EXERCISING the real, unmodified pipeline:

    RuleEngine -> GradeEngine -> AdjustmentEngine -> CalculationEngine
    -> FormCompletionEngine -> official_pdf_renderer.render_official_pdf()

against the REAL Golden Case fixture (data/golden/golden_case_input.py) --
the exact same construction tests/test_official_pdf_output.py already uses
to exercise the renderer, and the exact same case_no ("1140901-99-001")
scripts/build_facility_candidates_mock.py uses for facility_candidates.json.
No engine here is touched or reimplemented; this script only calls them.

FACILITY-CONFIRMATION-GATE-1 / FACILITY-STALE-CONFIRMATION-GATE-1
consistency (Task 12): this script replays the IDENTICAL confirm/reject/
evidence-refresh sequence as build_facility_candidates_mock.py against a
moto-mocked DynamoDB table, then reads confirmed_facility_selections via
the REAL facility_confirmation_repository.get_active_confirmed_selections()
-- the SAME gated read path pdf_handler.py itself uses -- so the resulting
mock Official PDF shows substation (CONFIRMED, not stale) and leaves
gas_tank/cemetery/funeral_home/crematorium/columbarium/MRT/TRA blank,
exactly matching frontend/mock/facility_candidates.json's own states:
  substation   -> CONFIRMED, stale=false  => rendered
  gas_tank     -> CONFIRMED, stale=true   => blank (stale, must reconfirm)
  cemetery     -> PENDING                -> blank
  funeral_home -> PENDING, candidate=None -> blank
  crematorium  -> PENDING, candidate=None -> blank
  columbarium  -> REJECTED               -> blank
  MRT / TRA    -> PENDING, candidate=None -> blank
Never hand-edits the PDF or fills these in from raw FACTORS -- if this
script's own gate call ever returns something different from the above,
that MUST surface as a visible mismatch, not be silently "corrected".
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "data/golden", "backend/handlers", "pdf", "providers", ""):
    sys.path.insert(0, os.path.join(REPO_ROOT, p) if p else REPO_ROOT)

os.environ.setdefault("CASES_TABLE_NAME", "mock-gen-table-official-pdf")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")

from moto import mock_aws  # noqa: E402
import boto3  # noqa: E402

from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402
from rule_engine import RuleEngine  # noqa: E402
from grade_engine import GradeEngine  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from calculation_engine import CalculationEngine  # noqa: E402
from form_completion_engine import FormCompletionEngine  # noqa: E402
from official_pdf_renderer import render_official_pdf  # noqa: E402

CASE_NO = GOLDEN_CASE.case_no
assert CASE_NO == "1140901-99-001", CASE_NO


def _real_form_completion_fields():
    reg = json.load(open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8"))["rules"]
    ind = json.load(open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8"))["rules"]
    rule_engine = RuleEngine(reg + ind)
    fce = FormCompletionEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine())
    result = fce.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)
    return json.loads(result.model_dump_json())["fields"]


def _facility_points_from_mock_provider():
    """OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 3 fix: production's real
    backend/handlers/collect_data.py always runs MockRoadProvider/
    RealRoadProvider ALONGSIDE MockSpecialFacilityProvider, so a real
    case's FACTORS.points always carries a "main_road_name" point --
    official_pdf_renderer.py's road_name cell was never missing a data
    source or a render-profile entry, it was only ever missing from THIS
    offline mock-generation script, which previously built facility_points
    from MockSpecialFacilityProvider alone. Adding MockRoadProvider's
    points here is pure mock/data-wiring -- it does not touch
    RoadWidthResolver, RoadWidthValidator, or any other Engine."""
    from base import ProviderContext
    from special_facility_provider import MockSpecialFacilityProvider
    from road_provider import MockRoadProvider

    ctx = ProviderContext(case_no=CASE_NO, city="新北市", district="金山區", segment_code="P002-00")
    points = [p.model_dump(mode="json") for p in MockSpecialFacilityProvider().fetch(ctx)]
    points += [p.model_dump(mode="json") for p in MockRoadProvider().fetch(ctx)]
    return {"points": points, "official_facility_evidence": {"STATION": {"matches": []}}}


def _confirmed_facility_selections():
    """Replays the EXACT same confirm/reject/evidence-refresh sequence as
    scripts/build_facility_candidates_mock.py against a fresh moto-mocked
    table, then reads back via the real gated
    get_active_confirmed_selections() -- never hand-built."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName=os.environ["CASES_TABLE_NAME"],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        import case_store
        import facility_confirmation
        from facility_confirmation_repository import default_facility_confirmation_repository

        factors_v1 = _facility_points_from_mock_provider()
        case_store.put_record(CASE_NO, "FACTORS", factors_v1)
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": CASE_NO}}, None)

        def _confirm(subtype):
            facility_confirmation.confirm_facility_candidate(
                {"pathParameters": {"id": CASE_NO, "subtype": subtype},
                 "body": json.dumps({"confirmed_by": "王小明（承辦人）"})}, None)

        def _reject(subtype):
            facility_confirmation.reject_facility_candidate(
                {"pathParameters": {"id": CASE_NO, "subtype": subtype},
                 "body": json.dumps({"rejected_by": "王小明（承辦人）", "reviewer_note": "現場勘查後確認非影響因素"})}, None)

        _confirm("substation")
        _confirm("gas_tank")
        factors_v2 = _facility_points_from_mock_provider()
        for p in factors_v2["points"]:
            if p["field"] == "gas_tank_distance_m":
                p["value"] = 610
        case_store.put_record(CASE_NO, "FACTORS", factors_v2)
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": CASE_NO}}, None)
        _reject("columbarium")

        selections = default_facility_confirmation_repository().get_active_confirmed_selections(CASE_NO)
        return selections, factors_v2["points"]


def main() -> None:
    confirmed_selections, facility_points = _confirmed_facility_selections()
    print("confirmed_facility_selections (ACTIVE_CONFIRMED_SELECTION only):",
          json.dumps(confirmed_selections, ensure_ascii=False))
    expected_keys = {"substation"}
    if set(confirmed_selections.keys()) != expected_keys:
        raise SystemExit(
            f"FATAL: expected ONLY {expected_keys} to pass the confirmation gate (matching "
            f"frontend/mock/facility_candidates.json's substation=CONFIRMED/stale=false state), "
            f"got {set(confirmed_selections.keys())} -- refusing to write a mock PDF that would "
            f"misrepresent the gate."
        )

    form_completion_fields = _real_form_completion_fields()
    pdf_bytes = render_official_pdf(
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, form_completion_fields,
        facility_points=facility_points,
        confirmed_facility_selections=confirmed_selections,
    )

    out_paths = [
        os.path.join(REPO_ROOT, "frontend", "mock", "pdf", "official_form_Golden_Case.pdf"),
        os.path.join(REPO_ROOT, "frontend", "app", "frontend", "mock", "pdf", "official_form_Golden_Case.pdf"),
    ]
    for out_path in out_paths:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(pdf_bytes)
        print(f"wrote {out_path} ({len(pdf_bytes)} bytes)")


if __name__ == "__main__":
    main()
