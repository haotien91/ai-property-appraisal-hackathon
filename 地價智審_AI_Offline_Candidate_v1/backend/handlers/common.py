# -*- coding: utf-8 -*-
"""
common.py — shared utilities for every Lambda handler in this backend.

Design principles enforced here (per Phase 7 instructions):
- CloudWatch logging is structured (JSON) and NEVER logs full case content
  (raw factor values, PDF bytes, etc.) -- only case_no, step name, status,
  duration_ms, and error codes. See docs/phase7/aws_services.md §Monitoring.
- Every handler returns the same API Gateway proxy-integration response
  shape, and every error uses the Error Schema already defined in
  docs/phase4/frontend_api_contract.md (code/message/field_id/details) --
  the frontend's js/api.js was built against that exact schema.
"""
from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Dict

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class DecimalEncoder(json.JSONEncoder):
    """Decimal -> string, never float, to preserve the precision-preservation
    guarantee established in engine/calculation_engine.py (Phase 4)."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def log_step(case_no: str, step: str, status: str, duration_ms: float = None, error_code: str = None):
    """The ONLY logging function handlers should call for step-level events.
    Deliberately takes no raw field values / factor data as parameters, so
    it is structurally impossible to accidentally log sensitive case
    content through this helper."""
    record = {"case_no": case_no, "step": step, "status": status}
    if duration_ms is not None:
        record["duration_ms"] = round(duration_ms, 2)
    if error_code:
        record["error_code"] = error_code
    logger.info(json.dumps(record, ensure_ascii=False))


def timed_step(case_no: str, step: str):
    """Context manager: logs step start/end with duration, and the error
    code (not the error message body, which might echo submitted data) on
    failure."""

    class _Timer:
        def __enter__(self):
            self._t0 = time.time()
            log_step(case_no, step, "START")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            duration = (time.time() - self._t0) * 1000
            if exc_type is None:
                log_step(case_no, step, "SUCCESS", duration_ms=duration)
            else:
                log_step(case_no, step, "ERROR", duration_ms=duration, error_code=exc_type.__name__)
            return False  # never swallow exceptions

    return _Timer()


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # tightened at API Gateway/CloudFront in production, see aws_services.md
        },
        "body": json.dumps(body, ensure_ascii=False, cls=DecimalEncoder),
    }


def error_response(status_code: int, code: str, message: str, field_id: str = None) -> Dict[str, Any]:
    return response(status_code, {"error": {"code": code, "message": message, "field_id": field_id, "details": {}}})


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
