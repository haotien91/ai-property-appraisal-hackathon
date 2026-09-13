# -*- coding: utf-8 -*-
"""
semantic_rule_mapping_provider.py — AI Semantic Fallback for the
Evaluation Standard Importer (docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_
REPORT.md). Follows this repo's existing Adapter Pattern Provider
contract (providers/base.py's DataProvider, providers/document_
extraction_provider.py's DocumentExtractionProvider): one interface,
interchangeable Mock vs Real implementations, callers never branch on
which is active.

Scope discipline (Phase 3C §1/§2/§6 -- enforced structurally, not just by
convention):
- Only ever called for a candidate the DETERMINISTIC Importer (engine/
  evaluation_standard_importer.py) already flagged non-EXTRACTED
  (UNKNOWN_FACTOR / RULE_EXTRACTION_AMBIGUOUS / a PARTIAL-status
  candidate's unparsed condition) -- see backend/handlers/case_rule_
  repository.py::propose_ai_candidates(), the only caller. A cleanly-
  EXTRACTED candidate is never re-judged by AI.
- A provider may populate ONLY the fields on domain.models.
  SemanticRuleMappingCandidate -- no grade/rank/adjustment_rate/price/
  legal_conclusion field exists on that model at all. validate_ai_
  response_schema() below is the FIRST line of defense (runs on the
  RAW dict before it ever reaches that model): any forbidden key ->
  SCOPE_VIOLATION, any missing required key or unexpected key ->
  SCHEMA_INVALID. Neither ever becomes a SemanticRuleMappingCandidate.
- Every result -- OK, SCHEMA_INVALID, SCOPE_VIOLATION, or (for a real
  provider that could not even reach the model) PROVIDER_UNAVAILABLE --
  is wrapped in a SemanticRuleMappingResult and persisted for human
  review (backend/handlers/case_rule_repository.py::propose_ai_
  candidates()). Nothing here ever writes into CaseRulePackage.
  regional_rules/individual_rules directly; only a human, via the
  EXISTING Phase 3B resolve_candidate_factor_mapping()/submit_human_
  edits(), can do that -- confidence affects ONLY which candidates a
  human reviewer sees first, never auto-confirmation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from domain.models import SemanticRuleMappingCandidate, SemanticRuleMappingResult

PROMPT_VERSION = "semantic-rule-mapping-v1"

# Phase 3C §2's exact output contract -- REQUIRED must always be present;
# OPTIONAL may be omitted; anything else (including FORBIDDEN) is rejected.
_REQUIRED_KEYS = {"confidence", "reason"}
_OPTIONAL_KEYS = {
    "candidate_factor_id", "candidate_condition_id", "normalized_condition_candidate",
    "candidate_unit", "candidate_value_type",
}
_ALLOWED_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS

# §6 AI Boundary Guard: any of these appearing in a raw AI response is an
# AI_OUTPUT_SCOPE_VIOLATION, checked BEFORE the schema/required-field
# check below (a response that both smuggles a forbidden field AND is
# otherwise well-formed must still be rejected as a scope violation, not
# quietly accepted for its well-formed parts).
_FORBIDDEN_KEYS = {
    "grade", "final_grade", "grade_code", "final_grade_code",
    "rank", "final_rank",
    "adjustment_rate", "final_adjustment_rate", "adjustment_pct", "final_adjustment_pct",
    "price", "final_price", "comparison_price", "base_parcel_comparison_price",
    "legal_conclusion", "final_legal_conclusion", "legal_basis",
}

_VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


def validate_ai_response_schema(raw: Any) -> Tuple[str, Optional[str]]:
    """Pure function: (status, error_message). status is one of
    'OK' | 'SCOPE_VIOLATION' | 'SCHEMA_INVALID'. Never raises -- an
    unparseable/wrong-shaped response is a data outcome to report, not an
    exception to propagate into the Importer/repository layer."""
    if not isinstance(raw, dict):
        return "SCHEMA_INVALID", f"AI回應非JSON object，收到型別：{type(raw).__name__}"

    keys = set(raw.keys())
    forbidden_hit = keys & _FORBIDDEN_KEYS
    if forbidden_hit:
        return "SCOPE_VIOLATION", (
            f"AI回應包含禁止輸出之最終決策欄位：{sorted(forbidden_hit)}（AI_OUTPUT_SCOPE_VIOLATION，"
            f"AI僅能輸出候選欄位，不得輸出grade/rank/adjustment_rate/price/legal_conclusion）"
        )

    missing = _REQUIRED_KEYS - keys
    if missing:
        return "SCHEMA_INVALID", f"AI回應缺少必要欄位：{sorted(missing)}"

    unexpected = keys - _ALLOWED_KEYS
    if unexpected:
        return "SCHEMA_INVALID", f"AI回應包含未預期欄位：{sorted(unexpected)}"

    confidence = raw.get("confidence")
    if not isinstance(confidence, str) or confidence.upper() not in _VALID_CONFIDENCE:
        return "SCHEMA_INVALID", f"confidence必須為HIGH/MEDIUM/LOW，收到：{confidence!r}"

    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return "SCHEMA_INVALID", "reason必須為非空字串"

    return "OK", None


def _build_result(candidate_id: str, original_text: str, deterministic_failure_reasons: List[str],
                   provider_name: str, model_id: Optional[str], raw: Optional[dict]) -> SemanticRuleMappingResult:
    """Shared assembly logic for both providers below -- validates `raw`
    (or, if the provider could not produce one at all, reports
    PROVIDER_UNAVAILABLE) and wraps the outcome with full provenance
    (§5)."""
    now = datetime.now(timezone.utc)
    if raw is None:
        return SemanticRuleMappingResult(
            candidate_id=candidate_id, original_text=original_text,
            deterministic_failure_reason=list(deterministic_failure_reasons),
            provider=provider_name, model_id=model_id, prompt_version=PROMPT_VERSION,
            status="PROVIDER_UNAVAILABLE", candidate=None,
            error_message="Provider 未能取得回應（無法連線/逾時/未設定憑證）",
            created_at=now,
        )

    status, error_message = validate_ai_response_schema(raw)
    candidate = None
    if status == "OK":
        candidate = SemanticRuleMappingCandidate(
            candidate_factor_id=raw.get("candidate_factor_id"),
            candidate_condition_id=raw.get("candidate_condition_id"),
            normalized_condition_candidate=raw.get("normalized_condition_candidate"),
            candidate_unit=raw.get("candidate_unit"),
            candidate_value_type=raw.get("candidate_value_type"),
            confidence=raw["confidence"].upper(),
            reason=raw["reason"],
        )
    return SemanticRuleMappingResult(
        candidate_id=candidate_id, original_text=original_text,
        deterministic_failure_reason=list(deterministic_failure_reasons),
        provider=provider_name, model_id=model_id, prompt_version=PROMPT_VERSION,
        status=status, candidate=candidate, error_message=error_message, created_at=now,
    )


class SemanticRuleMappingProvider:
    """Interface only -- documents the contract every implementation below
    follows. No behavior of its own."""
    provider_name: str = "SemanticRuleMappingProvider"
    status: str = "ABSTRACT"

    def propose_candidate(self, candidate_id: str, original_text: str, deterministic_failure_reasons: List[str],
                           scope: str, city: str, district: str, land_use_type: str,
                           known_factor_names: List[str]) -> SemanticRuleMappingResult:
        raise NotImplementedError


# ---------------------------------------------------------------------
# MockSemanticRuleMappingProvider -- MOCK_ONLY. Deliberately a simple,
# fully offline, reproducible character-overlap heuristic -- NOT a real
# semantic/LLM understanding of the text. Exists so this module's
# integration (trigger conditions, schema validation, provenance,
# confidence handling, human-review wiring) is independently testable
# without any network access or AWS credentials, matching this repo's
# existing Mock-provider convention throughout providers/.
# ---------------------------------------------------------------------

class MockSemanticRuleMappingProvider(SemanticRuleMappingProvider):
    provider_name = "MockSemanticRuleMappingProvider"
    status = "MOCK_ONLY"

    def propose_candidate(self, candidate_id: str, original_text: str, deterministic_failure_reasons: List[str],
                           scope: str, city: str, district: str, land_use_type: str,
                           known_factor_names: List[str]) -> SemanticRuleMappingResult:
        raw = self._heuristic_response(original_text, known_factor_names)
        return _build_result(candidate_id, original_text, deterministic_failure_reasons,
                              self.provider_name, "mock-heuristic-v1", raw)

    @staticmethod
    def _char_overlap_score(a: str, b: str) -> float:
        """Jaccard similarity over character sets -- crude on purpose (a
        MOCK stand-in for a real semantic matcher, not a claim of NLP
        quality). Deterministic and reproducible: same inputs always
        produce the same score, unlike a real LLM call."""
        set_a, set_b = set(a), set(b)
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    def _heuristic_response(self, original_text: str, known_factor_names: List[str]) -> dict:
        scored = sorted(
            ((name, self._char_overlap_score(original_text, name)) for name in known_factor_names if name),
            key=lambda t: t[1], reverse=True,
        )
        best_name, best_score = scored[0] if scored else (None, 0.0)

        if best_score >= 0.6:
            confidence, factor_id = "HIGH", best_name
        elif best_score >= 0.3:
            confidence, factor_id = "MEDIUM", best_name
        elif best_score > 0.0:
            confidence, factor_id = "LOW", best_name
        else:
            confidence, factor_id = "LOW", None

        reason = (
            f"字元集合Jaccard相似度={best_score:.2f}（與候選因素名稱{best_name!r}比對）"
            if best_name else "候選因素清單中無任何字元重疊，無法提出建議"
        )
        return {"candidate_factor_id": factor_id, "confidence": confidence, "reason": reason}


# ---------------------------------------------------------------------
# BedrockSemanticRuleMappingProvider -- CODE_READY interface, NOT
# AWS_RUNTIME_VERIFIED (see providers/document_extraction_provider.py's
# TextractExtractionProvider for the exact same pattern this mirrors:
# propose_candidate() itself RAISES rather than attempting an unverified
# live call, while the prompt-building and response-parsing logic are
# separate PURE functions, independently unit-tested against synthetic
# fixtures -- see tests/test_semantic_rule_mapping_provider.py).
# ---------------------------------------------------------------------

class BedrockSemanticRuleMappingProvider(SemanticRuleMappingProvider):
    """Adapter shape for AWS Bedrock (Anthropic Claude via the Bedrock
    Messages API) -- NOT Gemini or any other provider; this repo's own
    existing Bedrock usage (engine/explanation.py's Part F) is the
    precedent this mirrors, kept consistent rather than introducing a
    second AI vendor. `_build_prompt()`/`_parse_bedrock_response()` are
    pure functions (no boto3 call) -- use those to verify prompt/parsing
    logic without live AWS access. `propose_candidate()` itself has never
    been executed against a real Bedrock endpoint in this environment (no
    confirmed network egress/credentials) -- CODE_READY, NOT
    AWS_RUNTIME_VERIFIED; do not report this as a working real-mode path
    without first actually running it against a real AWS account."""
    provider_name = "BedrockSemanticRuleMappingProvider"
    status = "CODE_READY"

    def __init__(self, bedrock_client=None, model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"):
        self._client = bedrock_client  # injected lazily; boto3.client("bedrock-runtime") in production
        self._model_id = model_id

    def propose_candidate(self, candidate_id: str, original_text: str, deterministic_failure_reasons: List[str],
                           scope: str, city: str, district: str, land_use_type: str,
                           known_factor_names: List[str]) -> SemanticRuleMappingResult:
        raise NotImplementedError(
            "BedrockSemanticRuleMappingProvider.propose_candidate() is CODE_READY but NOT "
            "AWS_RUNTIME_VERIFIED -- this environment has no confirmed Bedrock network egress or "
            "credentials (see docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_REPORT.md). Wire a real "
            "boto3 bedrock-runtime client and verify against a real AWS account before using this "
            "in place of MockSemanticRuleMappingProvider. _build_prompt()/_parse_bedrock_response() "
            "are independently unit-tested pure functions -- use those to verify prompt/parsing "
            "logic without a live call."
        )

    @staticmethod
    def _build_prompt(original_text: str, deterministic_failure_reasons: List[str], scope: str,
                       city: str, district: str, land_use_type: str, known_factor_names: List[str]) -> str:
        """Pure function: the exact prompt text that would be sent to
        Bedrock, versioned by PROMPT_VERSION above. Explicitly instructs
        the model on the output contract AND the boundary it must never
        cross -- but the ENFORCEMENT of that boundary is
        validate_ai_response_schema() above, never a trust-the-prompt
        assumption."""
        factor_list = "、".join(known_factor_names)
        return (
            f"[prompt_version={PROMPT_VERSION}]\n"
            f"你是一個地價評估規則比對輔助工具。以下是一段從PDF擷取、但deterministic解析器"
            f"無法確定對應到哪個既有因素的原始文字，原因：{', '.join(deterministic_failure_reasons)}。\n"
            f"scope={scope} city={city} district={district} land_use_type={land_use_type}\n"
            f"原始文字：{original_text!r}\n"
            f"既有已知因素名稱清單（僅能從中選一個，或留空表示不確定）：{factor_list}\n\n"
            f"請只回傳一個JSON object，且只能包含以下欄位："
            f"candidate_factor_id, candidate_condition_id, normalized_condition_candidate, "
            f"candidate_unit, candidate_value_type（皆可為null/省略）, "
            f"confidence（必填，HIGH/MEDIUM/LOW之一）, reason（必填，簡短說明）。\n"
            f"絕對不可輸出grade、rank、adjustment_rate、price、legal_conclusion或任何形式的"
            f"最終決策欄位——你只能提出候選建議，不能做出最終判定。"
        )

    @staticmethod
    def _parse_bedrock_response(response_body: dict) -> dict:
        """Pure function: Bedrock's Messages API response envelope
        (`{"content": [{"type": "text", "text": "<json string>"}], ...}`)
        -> the raw candidate dict (NOT yet validated -- callers must still
        run it through validate_ai_response_schema()). Raises ValueError
        (never silently returns a guessed dict) if the envelope shape or
        the inner text is not valid JSON -- an unparseable response must
        surface as SCHEMA_INVALID via _build_result(), never as a
        fabricated empty candidate."""
        import json

        content = response_body.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("Bedrock回應缺少content陣列")
        text = next((block.get("text") for block in content if block.get("type") == "text"), None)
        if not text:
            raise ValueError("Bedrock回應content中無text區塊")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Bedrock回應text非合法JSON：{e}") from e
