# -*- coding: utf-8 -*-
"""
explanation.py — Bedrock usage (Part F). STRICTLY limited to natural-
language explanation/summary of ALREADY-COMPUTED deterministic results.

This function receives a FormCompletionResult and/or ReviewResult (both
already fully computed by Phases 3-6's deterministic engines) and asks a
Bedrock foundation model to write a plain-language cover summary for a
human reviewer. It NEVER asks Bedrock to determine a Grade, Adjustment,
Calculation, or Distance -- those values are passed in as已經算好的
constants inside the prompt, not left for the model to derive. If Bedrock
is unavailable or returns something unusable, this function degrades to a
template-based summary rather than blocking the pipeline (Step Functions
treats this as a non-critical enrichment step, see
docs/phase7/step_functions_spec.md).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/opt/python")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common import response, error_response, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
_bedrock = None


def _bedrock_client():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime")
    return _bedrock


def _build_prompt(form_completion: dict, review_result: dict) -> str:
    """The prompt embeds ONLY already-computed values (final_value,
    issue_type, severity, etc.) as literal facts to summarize -- it never
    asks the model to compute, judge, or verify a number itself."""
    final_field = next((f for f in form_completion.get("fields", [])
                         if f["field_id"] == "base_parcel_comparison_price"), None)
    final_price = final_field["final_value"] if final_field else "N/A"
    issues = review_result.get("issues", [])
    non_passed = [i for i in issues if i.get("issue_type") != "Passed"]

    facts = {
        "case_no": form_completion.get("case_no"),
        "final_price": final_price,
        "completed_count": form_completion.get("completed_count"),
        "manual_review_count": form_completion.get("manual_review_count"),
        "review_issue_count": len(non_passed),
        "review_issues_summary": [
            {"field": i["field"], "issue_type": i["issue_type"], "severity": i["severity"],
             "summary": i["explanation_data"]["summary"]}
            for i in non_passed[:10]
        ],
    }
    return (
        "你是不動產估價案件審查的AI助理。以下是系統已經以deterministic規則引擎"
        "計算完成的結果（你不需要、也不應該重新驗算任何數字，只需要用平實的中文"
        "摘要說明給審查人員看）：\n\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
        "請以3-5句話摘要本案件目前狀態，包含：比準地比較價格、是否有需要人工"
        "確認的問題、以及最主要的問題是什麼（若有）。不要輸出任何JSON或程式碼，"
        "只需要一段平實的中文說明文字。"
    )


def generate_explanation(event, context):
    case_no = event.get("pathParameters", {}).get("id") if event.get("pathParameters") else event.get("case_no")
    with timed_step(case_no, "generate_explanation"):
        form_completion = case_store.get_record(case_no, "FORM_COMPLETION")
        review_result = case_store.get_record(case_no, "REVIEW_RESULT")
        if form_completion is None:
            return error_response(400, "VALIDATION_ERROR", "案件尚未完成書表填寫")

        prompt = _build_prompt(form_completion, review_result or {"issues": []})

        try:
            resp = _bedrock_client().invoke_model(
                modelId=BEDROCK_MODEL_ID,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            payload = json.loads(resp["body"].read())
            summary_text = payload["content"][0]["text"]
        except Exception as e:
            # Degrade gracefully: a template-based summary is always available,
            # so Bedrock being unreachable never blocks Step Functions.
            final_field = next((f for f in form_completion.get("fields", [])
                                 if f["field_id"] == "base_parcel_comparison_price"), None)
            summary_text = (
                f"（Bedrock暫時無法使用，改用系統預設摘要）案件 {case_no} 之比準地比較價格為 "
                f"{final_field['final_value'] if final_field else 'N/A'}。"
                f"完成欄位數：{form_completion.get('completed_count')}，"
                f"待人工確認欄位數：{form_completion.get('manual_review_count')}。"
            )

        case_store.put_record(case_no, "EXPLANATION", {"summary_text": summary_text})
        return response(200, {"case_no": case_no, "summary_text": summary_text})
