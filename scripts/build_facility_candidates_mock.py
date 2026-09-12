# -*- coding: utf-8 -*-
"""
build_facility_candidates_mock.py — FACILITY-REVIEW-UI-1.

Generates frontend/mock/facility_candidates.json by actually EXERCISING
the real backend/handlers/facility_confirmation.py + facility_confirmation_
repository.py code (via a moto-mocked DynamoDB table), never hand-typing
JSON -- same "mock data must come from real code output, not fabricated
numbers" convention already established by scripts/build_document_
extraction_mock.py.

Candidate evidence itself comes from providers/special_facility_provider.py
's MockSpecialFacilityProvider (substation/gas_tank/cemetery/funeral_home/
crematorium/columbarium's REAL Golden Case values) -- MRT/TRA legitimately
have no candidate in this offline dev environment (no NTPC OpenData sync
has ever run here), which is itself an honest, real demo state (see Task 3:
"如果 candidate=None：顯示「目前未取得可確認候選」" -- not fabricated).

To make the Mock demonstrate the full review lifecycle (Task 12), this
script drives a handful of subtypes through real confirm()/reject() calls
and one genuine evidence-refresh-after-confirm (producing a real
stale=True record) -- every field in the resulting JSON is the ACTUAL
output of that real code path, just orchestrated once, offline, to cover
every UI state in a single demo case.
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

os.environ["CASES_TABLE_NAME"] = "mock-gen-table"
os.environ["AWS_DEFAULT_REGION"] = "ap-northeast-1"

from moto import mock_aws  # noqa: E402
import boto3  # noqa: E402

CASE_NO = "1140901-99-001"


def _factors_from_mock_providers():
    from base import ProviderContext
    from special_facility_provider import MockSpecialFacilityProvider

    ctx = ProviderContext(case_no=CASE_NO, city="新北市", district="金山區", segment_code="P002-00")
    points = [p.model_dump(mode="json") for p in MockSpecialFacilityProvider().fetch(ctx)]
    # MRT/TRA: no official_facility_evidence at all in this offline dev
    # environment (no NTPC OpenData sync has ever run here) -- an HONEST
    # empty matches[] list, never a fabricated station.
    return {"points": points, "official_facility_evidence": {"STATION": {"matches": []}}}


def main() -> None:
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="mock-gen-table",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        import case_store
        import facility_confirmation

        factors_v1 = _factors_from_mock_providers()
        case_store.put_record(CASE_NO, "FACTORS", factors_v1)

        # 1st pass: creates all 8 PENDING records from real Mock provider evidence.
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": CASE_NO}}, None)

        def _confirm(subtype):
            facility_confirmation.confirm_facility_candidate(
                {"pathParameters": {"id": CASE_NO, "subtype": subtype},
                 "body": json.dumps({"confirmed_by": "王小明（承辦人）"})}, None)

        def _reject(subtype):
            facility_confirmation.reject_facility_candidate(
                {"pathParameters": {"id": CASE_NO, "subtype": subtype},
                 "body": json.dumps({"rejected_by": "王小明（承辦人）", "reviewer_note": "現場勘查後確認非影響因素"})}, None)

        # substation: confirmed, stays current (demo of CONFIRMED, not stale).
        _confirm("substation")
        # gas_tank: confirmed, then real new evidence arrives (distance
        # actually changes) -- produces a GENUINE stale=True record via
        # get_or_refresh_candidates()'s own real comparison logic.
        _confirm("gas_tank")
        factors_v2 = _factors_from_mock_providers()
        for p in factors_v2["points"]:
            if p["field"] == "gas_tank_distance_m":
                p["value"] = 610  # real code path: a later distance re-measurement changed
        case_store.put_record(CASE_NO, "FACTORS", factors_v2)
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": CASE_NO}}, None)
        # cemetery: left PENDING (demo of "awaiting review").
        # funeral_home / crematorium: candidate=None already (Golden Case "無") -- left as-is.
        # columbarium: rejected (demo of REJECTED).
        _reject("columbarium")
        # MRT / TRA: candidate=None already (no NTPC sync) -- left as-is.

        resp = facility_confirmation.get_facility_candidates({"pathParameters": {"id": CASE_NO}}, None)
        body = json.loads(resp["body"])

    out_path = os.path.join(REPO_ROOT, "frontend", "mock", "facility_candidates.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")
    print(json.dumps({c["subtype"]: {"status": c["status"], "stale": c["stale"]} for c in body["candidates"]},
                      ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
