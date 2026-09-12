# -*- coding: utf-8 -*-
"""
case_store.py — DynamoDB single-table adapter for case data.

Table design (see docs/phase7/aws_services.md §Database for the full
justification of DynamoDB over RDS/PostgreSQL):

    PK = "CASE#<case_no>"
    SK = "META" | "FACTORS" | "FORM_COMPLETION" | "REVIEW_RESULT"

Each item's `data` attribute holds the JSON-serialized Pydantic model
(case metadata / FactorInput lists / FormCompletionResult / ReviewResult).
This mirrors the existing Pydantic -> JSON serialization already used
throughout Phases 4-6 (form_completion.json, review_result.json, etc.),
so no new serialization logic is needed -- the same `model_dump_json()`
output that already round-trips through pytest and the frontend mock
files is what gets written to and read from DynamoDB.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3

TABLE_NAME = os.environ.get("CASES_TABLE_NAME", "AIValuationCases")

_dynamodb = None


def _table():
    global _dynamodb
    if _dynamodb is None:
        # FRONTEND-UNIFIED-EXPORT-WIRING-F2: LOCAL_AWS_ENDPOINT_URL is unset
        # in every real deployment (never referenced by infra/template.yaml),
        # so production behavior is exactly `boto3.resource("dynamodb")` as
        # before. It exists only so a local `sam local start-api` run can
        # point at a standalone moto server without touching AWS SDK default
        # credential/endpoint resolution for production.
        local_endpoint = os.environ.get("LOCAL_AWS_ENDPOINT_URL")
        if local_endpoint:
            _dynamodb = boto3.resource("dynamodb", endpoint_url=local_endpoint)
        else:
            _dynamodb = boto3.resource("dynamodb")
    return _dynamodb.Table(TABLE_NAME)


def _dynamodb_safe(value):
    """boto3's Table resource rejects a native Python float outright
    ("Float types are not supported. Use Decimal types instead.") -- any
    handler that stores a Pydantic model_dump(mode="json") payload
    containing a float (a coordinate's latitude/longitude, a resolved
    percentage like RealLandUseProvider's float(resolved_value_pct), ...)
    would otherwise crash put_record. Converted via str() first (not
    Decimal(value) directly) to avoid picking up float's own binary
    imprecision -- the standard boto3-recommended pattern."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dynamodb_safe(v) for v in value]
    return value


def put_case_meta(case_no: str, meta: Dict[str, Any]) -> None:
    _table().put_item(Item={
        "PK": f"CASE#{case_no}", "SK": "META",
        "case_no": case_no, "data": _dynamodb_safe(meta),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_case_meta(case_no: str) -> Optional[Dict[str, Any]]:
    resp = _table().get_item(Key={"PK": f"CASE#{case_no}", "SK": "META"})
    item = resp.get("Item")
    if not item:
        return None
    # Merge the top-level updated_at (set authoritatively by put_case_meta)
    # back into the returned dict -- the caller's `data` payload should
    # never need to carry its own copy of this timestamp.
    return {**item["data"], "updated_at": item.get("updated_at")}


def list_case_metas(limit: int = 20) -> List[Dict[str, Any]]:
    """MVP scan-based listing. Acceptable at hackathon/demo scale (dozens,
    not millions, of cases); documented as a known scaling limitation in
    docs/phase7/aws_services.md rather than hidden -- a GSI on a constant
    partition + updated_at sort key would be the production-scale fix."""
    resp = _table().scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("SK").eq("META"),
        Limit=limit,
    )
    return [item["data"] for item in resp.get("Items", [])]


def put_record(case_no: str, sk: str, data: Dict[str, Any]) -> None:
    _table().put_item(Item={
        "PK": f"CASE#{case_no}", "SK": sk,
        "case_no": case_no, "data": _dynamodb_safe(data),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_record(case_no: str, sk: str) -> Optional[Dict[str, Any]]:
    resp = _table().get_item(Key={"PK": f"CASE#{case_no}", "SK": sk})
    item = resp.get("Item")
    if not item:
        return None
    return {**item["data"], "_updated_at": item.get("updated_at")} if isinstance(item["data"], dict) else item["data"]


def query_records_by_sk_prefix(case_no: str, sk_prefix: str) -> List[Dict[str, Any]]:
    """Returns every record for one case whose SK starts with sk_prefix (e.g.
    all "CASE_RULE_PACKAGE#<package_id>" versions for a case -- see
    case_rule_repository.py). A Query with a begins_with SK condition, not a
    Scan -- PK is exact, so cost stays O(items for this one case) regardless
    of overall table size, same as every other lookup in this module."""
    resp = _table().query(
        KeyConditionExpression=(
            boto3.dynamodb.conditions.Key("PK").eq(f"CASE#{case_no}")
            & boto3.dynamodb.conditions.Key("SK").begins_with(sk_prefix)
        ),
    )
    return [item["data"] for item in resp.get("Items", [])]


# ---------------------------------------------------------------------
# Document Upload / Extraction / Confirmation SK helpers (Phase E).
#
# One case may have MULTIPLE uploaded documents, so these are NOT fixed
# SK values like "FACTORS"/"FORM_COMPLETION" above -- each is keyed by its
# own document_id, mirroring this table's existing PK=CASE#<case_no>/
# SK=<record type> design (see module docstring). put_record()/get_record()
# above are reused as-is; these are just typo-safe SK-string builders, not
# a new storage mechanism.
# ---------------------------------------------------------------------

def document_sk(document_id: str) -> str:
    return f"DOCUMENT#{document_id}"


def extraction_sk(document_id: str) -> str:
    return f"EXTRACTION#{document_id}"


def confirmation_sk(document_id: str) -> str:
    return f"CONFIRMATION#{document_id}"
