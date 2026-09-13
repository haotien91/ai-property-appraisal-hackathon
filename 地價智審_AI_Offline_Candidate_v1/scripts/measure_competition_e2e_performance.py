# -*- coding: utf-8 -*-
"""
measure_competition_e2e_performance.py — STEP5 §30 performance measurement.

Runs the Competition E2E pipeline (via CompetitionOrchestrator) against a
moto-mocked DynamoDB/S3 twice in the SAME process -- once as a "cold-ish"
first run (this process's first-ever import of every engine/handler
module, first RuleEngine construction, first RuleTableValidator run) and
once "warm" (same process, same imports already cached, fresh RuleEngine
instances per STEP2's no-cache design but no import/module-load cost) --
and prints per-stage durations for both. Local-only benchmark, NOT an SLA:
STEP5 §30 explicitly says "只是benchmark，不要寫死SLA". PDF generation is
excluded from the timed stages on this host (WeasyPrint's native Cairo/
Pango libs are unavailable here -- the same documented, non-code-failure
environment limitation as tests/test_phase5_golden_pipeline.py); a
Docker/Lambda container image run would additionally capture that stage.

Usage: py scripts/measure_competition_e2e_performance.py
"""
from __future__ import annotations

import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, REPO_ROOT)

os.environ.setdefault("CASES_TABLE_NAME", "perf-table")
os.environ.setdefault("DOCUMENT_BUCKET_NAME", "perf-document-bucket")
os.environ.setdefault("PDF_BUCKET_NAME", "perf-pdf-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("DATA_PROVIDER_MODE", "mock")

from moto import mock_aws  # noqa: E402
import boto3  # noqa: E402

ROAD_WIDTH_FACTOR = "主要道路寬度"
ROAD_WIDTH_FIELD_ID = "regional_main_road_width"


def _make_case(cases_module, case_no):
    event = {"body": json.dumps({
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    })}
    return cases_module.create_case(event, None)


def _run_one_pipeline(case_no: str) -> dict:
    """Returns {stage_name: duration_ms} for one full evaluation-standard
    -> analyze -> complete_form -> review run, timed with the SAME
    time.perf_counter() calls the report uses (not relying on common.py's
    own INFO-level logging, so this works regardless of log configuration)."""
    import cases
    import case_store
    import collect_data
    import competition_orchestrator
    from case_rule_repository import DynamoCaseRuleRepository
    from domain.models import CaseRulePackage, CaseRulePackageStatus
    import datetime

    _make_case(cases, case_no)
    collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
        "base_parcel_factors": [], "comparable_factors": {"comp1": []},
    })}, None)
    factors = case_store.get_record(case_no, "FACTORS")
    factors["regional_base_factors"] = [
        {"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"},
    ]
    factors["regional_comparable_factors"] = {
        "comp1": [{"field_id": ROAD_WIDTH_FIELD_ID, "factor": ROAD_WIDTH_FACTOR, "raw_value": 18, "unit": "M"}],
    }
    factors["land_normal_price"] = {"comp1": "184763"}
    factors["price_date_rate"] = {"comp1": "2.00"}
    factors["weight"] = {"comp1": "100"}
    case_store.put_record(case_no, "FACTORS", factors)

    blind_fixture_path = os.path.join(
        REPO_ROOT, "tests", "fixtures", "competition", "blind_case_changed_standard",
        "blind_evaluation_standard.json",
    )
    with open(blind_fixture_path, encoding="utf-8") as f:
        fixture = json.load(f)

    timings = {}

    t0 = time.perf_counter()
    pkg = CaseRulePackage(
        case_id=case_no, package_id=f"PERF-PKG-{case_no}", rule_version="TEST_ONLY-perf-v1",
        source_document=blind_fixture_path, source_type="TEST_ONLY_JSON_FIXTURE",
        status=CaseRulePackageStatus.EXTRACTED,
        regional_rules=fixture["regional_rules"], individual_rules=[],
        created_at=datetime.datetime.now(datetime.timezone.utc), metadata=dict(fixture["metadata"]),
    )
    DynamoCaseRuleRepository().save_candidate(pkg)
    timings["evaluation_standard_import_ms"] = (time.perf_counter() - t0) * 1000

    orchestrator = competition_orchestrator.CompetitionOrchestrator(case_no)

    t0 = time.perf_counter()
    orchestrator.confirm_evaluation_standard(f"PERF-PKG-{case_no}", confirmed_by="perf-script")
    timings["confirmation_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    orchestrator.analyze()
    timings["analyze_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    orchestrator.complete_form()
    timings["complete_form_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    orchestrator.review()
    timings["review_ms"] = (time.perf_counter() - t0) * 1000

    timings["total_ms"] = sum(timings.values())
    return timings


def main():
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName=os.environ["CASES_TABLE_NAME"],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        cold = _run_one_pipeline("PERF-COLD-001")
        warm = _run_one_pipeline("PERF-WARM-001")

        result = {"cold_ms": cold, "warm_ms": warm, "note": "PDF generation excluded (WeasyPrint native libs unavailable on this host, STEP5 §12)"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


if __name__ == "__main__":
    main()
