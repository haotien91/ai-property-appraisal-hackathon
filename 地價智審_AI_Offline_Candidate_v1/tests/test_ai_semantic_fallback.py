# -*- coding: utf-8 -*-
"""
Phase 3C tests: AI Semantic Fallback (docs/audit/AI_SEMANTIC_FALLBACK_
PHASE3C_REPORT.md). Exercises providers/semantic_rule_mapping_provider.py
(schema validation, scope guard, Mock/Bedrock providers) and its
integration into backend/handlers/case_rule_repository.py::propose_ai_
candidates() / backend/handlers/evaluation_standard.py::propose_
evaluation_standard_ai_candidates -- via the REAL sample PDF, the same
one Phase 3A/3B's tests use.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

try:
    import pymupdf  # noqa: F401
except ImportError:
    pytest.skip("pymupdf not available", allow_module_level=True)

import semantic_rule_mapping_provider as srmp  # noqa: E402
from semantic_rule_mapping_provider import (  # noqa: E402
    MockSemanticRuleMappingProvider, BedrockSemanticRuleMappingProvider,
    validate_ai_response_schema, SemanticRuleMappingProvider,
)

PDF_PATH = os.path.join(REPO_ROOT, "data", "sources", "competition", "評價基準明細表範例.pdf")
DOCUMENT_BUCKET = "test-document-bucket"
AWS_REGION = "ap-northeast-1"
ROAD_WIDTH_FACTOR = "主要道路寬度"


def _known_factor_lists():
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return sorted({r["factor"] for r in reg}), sorted({r["factor"] for r in ind})


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("DOCUMENT_BUCKET_NAME", DOCUMENT_BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", AWS_REGION)
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=AWS_REGION)
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.create_bucket(Bucket=DOCUMENT_BUCKET, CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        # STEP5 FINAL GATE Part A (docs/audit/STEP5_FINAL_GATE_REPORT.md):
        # case_store.TABLE_NAME / document_upload.DOCUMENT_BUCKET / etc.
        # are module-level constants bound once at first import -- reset
        # here so an earlier test FILE's env vars (this file may run
        # after any of them, in any pytest execution order) can never
        # leave a stale table/bucket name cached for the rest of this
        # process.
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


class _CountingProvider(SemanticRuleMappingProvider):
    """Test double: wraps MockSemanticRuleMappingProvider but records every
    candidate_id it was actually invoked for -- used to prove the trigger
    filter (§1) never calls a provider for an already-EXTRACTED candidate."""
    provider_name = "CountingProvider"
    status = "TEST_FIXTURE_ONLY"

    def __init__(self):
        self.delegate = MockSemanticRuleMappingProvider()
        self.called_for = []

    def propose_candidate(self, candidate_id, original_text, deterministic_failure_reasons,
                           scope, city, district, land_use_type, known_factor_names):
        self.called_for.append(candidate_id)
        return self.delegate.propose_candidate(candidate_id, original_text, deterministic_failure_reasons,
                                                 scope, city, district, land_use_type, known_factor_names)


def _build_and_save_package(case_no, package_id="AI-PKG"):
    import evaluation_standard_importer as esi
    from case_rule_repository import default_case_rule_repository

    known_reg, known_ind = _known_factor_lists()
    package, _report = esi.build_case_rule_package_from_pdf(
        PDF_PATH, case_id=case_no, package_id=package_id, rule_version="v1",
        known_regional_factors=known_reg, known_individual_factors=known_ind,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    repo = default_case_rule_repository()
    repo.save_candidate(package)
    return package_id, known_reg, known_ind


# ---------------------------------------------------------------------------
# TEST 1 -- deterministic success -> AI not called
# ---------------------------------------------------------------------------

class TestTriggerConditions:
    def test_deterministic_success_does_not_call_ai(self, env):
        from case_rule_repository import default_case_rule_repository

        case_no = "AI-T1-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()
        before = repo.get_package(case_no, package_id)
        extracted_ids = {c["candidate_id"] for c in before.metadata["extraction_candidates"] if c["status"] == "EXTRACTED"}
        assert extracted_ids, "fixture sanity: expected at least one EXTRACTED candidate"

        provider = _CountingProvider()
        repo.propose_ai_candidates(case_no, package_id, provider=provider,
                                    known_regional_factors=known_reg, known_individual_factors=known_ind)

        assert extracted_ids.isdisjoint(set(provider.called_for)), (
            "AI provider was called for an already-EXTRACTED candidate -- "
            "deterministic success must never be re-judged by AI (§1)"
        )

    # ---------------------------------------------------------------------
    # TEST 2/3 -- unknown factor / ambiguous factor -> AI candidate
    # ---------------------------------------------------------------------

    def test_unknown_factor_produces_ai_candidate(self, env):
        from case_rule_repository import default_case_rule_repository, build_review_dto

        case_no = "AI-T2-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()
        before = repo.get_package(case_no, package_id)
        unknown = next(c for c in before.metadata["extraction_candidates"] if "UNKNOWN_FACTOR" in c["issues"])

        updated = repo.propose_ai_candidates(case_no, package_id, provider=MockSemanticRuleMappingProvider(),
                                              known_regional_factors=known_reg, known_individual_factors=known_ind)
        ai = updated.metadata["ai_semantic_candidates"]
        assert unknown["candidate_id"] in ai
        assert ai[unknown["candidate_id"]]["status"] == "OK"
        assert ai[unknown["candidate_id"]]["candidate"]["confidence"] in ("HIGH", "MEDIUM", "LOW")

        dto = build_review_dto(updated)
        factor_entry = next(f for f in dto["factors"] if f["factor_id"] == unknown["candidate_id"])
        assert factor_entry["ai_candidate"] is not None

    def test_ambiguous_factor_produces_ai_candidate(self, env):
        """A candidate whose issue is specifically RULE_EXTRACTION_AMBIGUOUS
        (not UNKNOWN_FACTOR) -- the real sample PDF happens to produce zero
        such candidates (every max_adjustment matched its matrix exactly),
        so this constructs one synthetically to prove the trigger filter is
        based on status != EXTRACTED generically, not hardcoded to the
        UNKNOWN_FACTOR issue code specifically."""
        from case_rule_repository import default_case_rule_repository
        from domain.models import CaseRulePackage, CaseRulePackageStatus

        case_no = "AI-T3-001"
        package_id = "AI-T3-PKG"
        repo = default_case_rule_repository()
        package = CaseRulePackage(
            case_id=case_no, package_id=package_id, rule_version="v1",
            source_document="synthetic", source_sha256=None, source_type="PDF_DETERMINISTIC_EXTRACTION",
            status=CaseRulePackageStatus.AMBIGUOUS, regional_rules=[], individual_rules=[],
            created_at=datetime.datetime.now(datetime.timezone.utc),
            metadata={"extraction_candidates": [{
                "candidate_id": "CAND-SYN-01", "scope": "regional", "city": "新北市", "district": "金山區",
                "land_use_type": "商業用地", "source_document": "synthetic", "source_page": "1",
                "note_text_candidate": "以主要道路寬度來衡量", "canonical_factor_id": ROAD_WIDTH_FACTOR,
                "max_adjustment_declared": 999.0, "status": "AMBIGUOUS", "issues": ["RULE_EXTRACTION_AMBIGUOUS"],
                "bands": [
                    {"grade": "優", "grade_code": 1, "condition_text": "30m以上", "value_type": "numeric_range",
                     "lower_bound": 30, "upper_bound": None, "lower_inclusive": True, "upper_inclusive": None,
                     "unit": "M", "anomaly_flag": None, "matrix_row": [0, 15]},
                    {"grade": "劣", "grade_code": 2, "condition_text": "未滿30m", "value_type": "numeric_range",
                     "lower_bound": None, "upper_bound": 30, "lower_inclusive": None, "upper_inclusive": False,
                     "unit": "M", "anomaly_flag": None, "matrix_row": [-15, 0]},
                ],
            }]},
        )
        repo.save_candidate(package)

        updated = repo.propose_ai_candidates(case_no, package_id, provider=MockSemanticRuleMappingProvider())
        ai = updated.metadata["ai_semantic_candidates"]
        assert "CAND-SYN-01" in ai
        assert ai["CAND-SYN-01"]["deterministic_failure_reason"] == ["RULE_EXTRACTION_AMBIGUOUS"]


# ---------------------------------------------------------------------------
# TEST 4/5 -- confidence affects ONLY review priority, never auto-confirm
# ---------------------------------------------------------------------------

class _AlwaysHighConfidenceProvider(SemanticRuleMappingProvider):
    """Test double: always proposes a well-formed, HIGH-confidence
    candidate_factor_id -- used to prove that even a maximally-confident
    AI proposal still never auto-applies (§4), independent of whatever
    confidence distribution the real PDF's heuristic-scored candidates
    happen to produce."""
    provider_name = "AlwaysHighConfidenceProvider"
    status = "TEST_FIXTURE_ONLY"

    def propose_candidate(self, candidate_id, original_text, deterministic_failure_reasons,
                           scope, city, district, land_use_type, known_factor_names):
        raw = {"candidate_factor_id": (known_factor_names or [None])[0], "confidence": "HIGH",
               "reason": "test fixture: always HIGH"}
        return srmp._build_result(candidate_id, original_text, deterministic_failure_reasons,
                                   self.provider_name, "test-model", raw)


class TestConfidenceNeverAutoConfirms:
    def test_high_confidence_still_not_auto_confirmed(self, env):
        from case_rule_repository import default_case_rule_repository

        case_no = "AI-T4-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()
        before = repo.get_package(case_no, package_id)
        before_regional = json.loads(json.dumps(before.regional_rules))
        before_individual = json.loads(json.dumps(before.individual_rules))

        updated = repo.propose_ai_candidates(case_no, package_id, provider=_AlwaysHighConfidenceProvider(),
                                              known_regional_factors=known_reg, known_individual_factors=known_ind)
        ai = updated.metadata["ai_semantic_candidates"]
        assert ai and all(r["candidate"]["confidence"] == "HIGH" for r in ai.values())
        # Regardless of confidence, regional_rules/individual_rules and
        # package status must be COMPLETELY unaffected by propose_ai_candidates().
        assert updated.regional_rules == before_regional
        assert updated.individual_rules == before_individual
        assert updated.status != "CONFIRMED"

    def test_low_confidence_requires_human_review(self, env):
        from case_rule_repository import default_case_rule_repository, build_review_dto

        case_no = "AI-T5-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()

        updated = repo.propose_ai_candidates(case_no, package_id, provider=MockSemanticRuleMappingProvider(),
                                              known_regional_factors=known_reg, known_individual_factors=known_ind)
        dto = build_review_dto(updated)
        low_conf_factors = [
            f for f in dto["factors"]
            if f["ai_candidate"] and f["ai_candidate"]["candidate"]
            and f["ai_candidate"]["candidate"]["confidence"] == "LOW"
        ]
        assert low_conf_factors, "fixture sanity: expected at least one LOW-confidence AI candidate"
        assert all(f["requires_human_review"] for f in low_conf_factors)


# ---------------------------------------------------------------------------
# TEST 6-10 -- strict schema validation / AI Boundary Guard
# ---------------------------------------------------------------------------

class TestSchemaValidationAndScopeGuard:
    def test_malformed_json_rejected(self):
        status, err = validate_ai_response_schema("this is not a dict")
        assert status == "SCHEMA_INVALID"
        assert err

    def test_missing_required_field_rejected(self):
        status, err = validate_ai_response_schema({"confidence": "HIGH"})  # missing reason
        assert status == "SCHEMA_INVALID"

    def test_unexpected_field_rejected(self):
        status, err = validate_ai_response_schema({
            "confidence": "HIGH", "reason": "x", "totally_unexpected_field": 1,
        })
        assert status == "SCHEMA_INVALID"

    def test_invalid_confidence_value_rejected(self):
        status, err = validate_ai_response_schema({"confidence": "VERY_HIGH", "reason": "x"})
        assert status == "SCHEMA_INVALID"

    @pytest.mark.parametrize("forbidden_field,value", [
        ("grade", "優"), ("final_grade", "優"), ("grade_code", 1),
    ])
    def test_ai_final_grade_field_rejected(self, forbidden_field, value):
        status, err = validate_ai_response_schema({"confidence": "HIGH", "reason": "x", forbidden_field: value})
        assert status == "SCOPE_VIOLATION"
        assert "AI_OUTPUT_SCOPE_VIOLATION" in err or forbidden_field in err

    @pytest.mark.parametrize("forbidden_field,value", [("rank", 1), ("final_rank", 1)])
    def test_ai_rank_field_rejected(self, forbidden_field, value):
        status, err = validate_ai_response_schema({"confidence": "HIGH", "reason": "x", forbidden_field: value})
        assert status == "SCOPE_VIOLATION"

    @pytest.mark.parametrize("forbidden_field,value", [
        ("adjustment_rate", 3.75), ("final_adjustment_rate", 3.75), ("adjustment_pct", 3.75),
    ])
    def test_ai_adjustment_rate_field_rejected(self, forbidden_field, value):
        status, err = validate_ai_response_schema({"confidence": "HIGH", "reason": "x", forbidden_field: value})
        assert status == "SCOPE_VIOLATION"

    @pytest.mark.parametrize("forbidden_field,value", [
        ("price", 184763), ("final_price", 184763), ("comparison_price", 184763),
        ("base_parcel_comparison_price", 184763),
    ])
    def test_ai_price_field_rejected(self, forbidden_field, value):
        status, err = validate_ai_response_schema({"confidence": "HIGH", "reason": "x", forbidden_field: value})
        assert status == "SCOPE_VIOLATION"

    def test_scope_violation_takes_priority_over_schema_invalid(self):
        """A response that is BOTH missing a required field AND smuggles a
        forbidden field must be reported as SCOPE_VIOLATION -- the more
        severe finding, never masked by the less severe one."""
        status, err = validate_ai_response_schema({"grade": "優"})  # missing confidence/reason too
        assert status == "SCOPE_VIOLATION"

    def test_scope_violation_response_never_becomes_a_candidate_via_provider(self, env):
        """End-to-end: even if a provider's raw response smuggled a
        forbidden field, propose_ai_candidates() must store it as a
        SCOPE_VIOLATION result with candidate=None, never construct a
        SemanticRuleMappingCandidate from it."""
        from case_rule_repository import default_case_rule_repository

        class _ScopeViolatingProvider(SemanticRuleMappingProvider):
            provider_name = "ScopeViolatingProvider"
            status = "TEST_FIXTURE_ONLY"

            def propose_candidate(self, candidate_id, original_text, deterministic_failure_reasons,
                                   scope, city, district, land_use_type, known_factor_names):
                return srmp._build_result(candidate_id, original_text, deterministic_failure_reasons,
                                           self.provider_name, "test-model", {"grade": "優", "reason": "x", "confidence": "HIGH"})

        case_no = "AI-T6-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()
        updated = repo.propose_ai_candidates(case_no, package_id, provider=_ScopeViolatingProvider(),
                                              known_regional_factors=known_reg, known_individual_factors=known_ind)
        ai = updated.metadata["ai_semantic_candidates"]
        assert ai, "expected at least one AI result"
        for result in ai.values():
            assert result["status"] == "SCOPE_VIOLATION"
            assert result["candidate"] is None


# ---------------------------------------------------------------------------
# TEST 11/13 -- provenance preserved / original AI candidate preserved
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_provenance_preserved(self, env):
        from case_rule_repository import default_case_rule_repository

        case_no = "AI-T11-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()
        updated = repo.propose_ai_candidates(case_no, package_id, provider=MockSemanticRuleMappingProvider(),
                                              known_regional_factors=known_reg, known_individual_factors=known_ind)
        ai = updated.metadata["ai_semantic_candidates"]
        sample = next(iter(ai.values()))
        for field in ("original_text", "deterministic_failure_reason", "provider", "model_id",
                      "prompt_version", "status", "created_at"):
            assert field in sample, f"missing provenance field: {field}"
        assert sample["provider"] == "MockSemanticRuleMappingProvider"
        assert sample["prompt_version"] == srmp.PROMPT_VERSION
        if sample["candidate"]:
            assert "confidence" in sample["candidate"] and "reason" in sample["candidate"]


# ---------------------------------------------------------------------------
# TEST 12/13 -- human edit overrides AI candidate / original preserved
# ---------------------------------------------------------------------------

class TestHumanOverride:
    def test_human_edit_overrides_ai_candidate_and_original_preserved(self, env):
        from case_rule_repository import default_case_rule_repository

        case_no = "AI-T12-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()
        before = repo.get_package(case_no, package_id)
        unknown = next(c for c in before.metadata["extraction_candidates"] if "UNKNOWN_FACTOR" in c["issues"])

        updated = repo.propose_ai_candidates(case_no, package_id, provider=MockSemanticRuleMappingProvider(),
                                              known_regional_factors=known_reg, known_individual_factors=known_ind)
        ai_before = updated.metadata["ai_semantic_candidates"][unknown["candidate_id"]]

        # Human deliberately picks a DIFFERENT factor than whatever the AI
        # proposed (or, if AI proposed None, the human still picks one).
        human_choice = "都市計畫（內、外）"
        final = repo.resolve_candidate_factor_mapping(
            case_no, package_id, unknown["candidate_id"], human_choice, edited_by="human-reviewer",
        )

        resolved_candidate = next(
            c for c in final.metadata["extraction_candidates"] if c["candidate_id"] == unknown["candidate_id"]
        )
        assert resolved_candidate["canonical_factor_id"] == human_choice

        # The AI's original proposal is untouched, regardless of what the human chose.
        ai_after = final.metadata["ai_semantic_candidates"][unknown["candidate_id"]]
        assert ai_after == ai_before

        edit = next(e for e in final.edit_history if e.rule_id == f"candidate:{unknown['candidate_id']}")
        assert edit.confirmed_value == human_choice


# ---------------------------------------------------------------------------
# TEST 14/15/16 -- GradeEngine/AdjustmentEngine/CalculationEngine never call AI
# ---------------------------------------------------------------------------

class TestNoEngineCallsAI:
    @pytest.mark.parametrize("relative_path", [
        "engine/grade_engine.py", "engine/adjustment_engine.py", "engine/calculation_engine.py",
    ])
    def test_engine_never_imports_semantic_provider(self, relative_path):
        src = open(os.path.join(REPO_ROOT, relative_path), encoding="utf-8").read()
        import_lines = [l for l in src.splitlines() if l.strip().startswith(("import ", "from "))]
        assert not any("semantic_rule_mapping" in l or "bedrock" in l.lower() for l in import_lines), (
            f"{relative_path} must never import the AI semantic mapping provider or boto3 bedrock client"
        )


# ---------------------------------------------------------------------------
# TEST 17 -- mock provider deterministic tests
# ---------------------------------------------------------------------------

class TestMockProviderDeterministic:
    def test_same_input_produces_identical_output(self):
        provider = MockSemanticRuleMappingProvider()
        args = dict(
            candidate_id="CAND-X", original_text="以主要道路寬度來衡量",
            deterministic_failure_reasons=["UNKNOWN_FACTOR"], scope="regional",
            city="新北市", district="金山區", land_use_type="商業用地",
            known_factor_names=["主要道路寬度", "建蔽率", "容積率"],
        )
        r1 = provider.propose_candidate(**args)
        r2 = provider.propose_candidate(**args)
        assert r1.candidate.candidate_factor_id == r2.candidate.candidate_factor_id
        assert r1.candidate.confidence == r2.candidate.confidence
        assert r1.status == r2.status == "OK"

    def test_mock_provider_status_is_mock_only(self):
        assert MockSemanticRuleMappingProvider.status == "MOCK_ONLY"

    def test_bedrock_provider_status_is_code_ready_not_runtime_verified(self):
        assert BedrockSemanticRuleMappingProvider.status == "CODE_READY"

    def test_bedrock_prompt_and_parser_are_pure_functions(self):
        prompt = BedrockSemanticRuleMappingProvider._build_prompt(
            original_text="以主要道路寬度來衡量", deterministic_failure_reasons=["UNKNOWN_FACTOR"],
            scope="regional", city="新北市", district="金山區", land_use_type="商業用地",
            known_factor_names=["主要道路寬度"],
        )
        assert srmp.PROMPT_VERSION in prompt
        assert "grade" not in prompt.lower() or "絕對不可輸出grade" in prompt  # boundary instruction present

        canned = {"content": [{"type": "text", "text": json.dumps({"confidence": "HIGH", "reason": "測試"})}]}
        parsed = BedrockSemanticRuleMappingProvider._parse_bedrock_response(canned)
        assert parsed == {"confidence": "HIGH", "reason": "測試"}

        with pytest.raises(ValueError):
            BedrockSemanticRuleMappingProvider._parse_bedrock_response({"content": [{"type": "text", "text": "not json"}]})


# ---------------------------------------------------------------------------
# TEST 18 -- no Real->Mock silent fallback
# ---------------------------------------------------------------------------

class TestNoRealToMockSilentFallback:
    def test_bedrock_provider_never_falls_back_to_mock_silently(self, env, monkeypatch):
        from case_rule_repository import default_case_rule_repository

        case_no = "AI-T18-001"
        package_id, known_reg, known_ind = _build_and_save_package(case_no)
        repo = default_case_rule_repository()

        with pytest.raises(NotImplementedError):
            repo.propose_ai_candidates(case_no, package_id, provider=BedrockSemanticRuleMappingProvider(),
                                        known_regional_factors=known_reg, known_individual_factors=known_ind)
        # No AI result was silently written using a Mock provider instead.
        after = repo.get_package(case_no, package_id)
        assert "ai_semantic_candidates" not in after.metadata or not after.metadata["ai_semantic_candidates"]

    def test_handler_selecting_bedrock_mode_returns_503_not_mock_result(self, env, monkeypatch):
        import evaluation_standard

        case_no = "AI-T18-002"
        package_id, _known_reg, _known_ind = _build_and_save_package(case_no)
        monkeypatch.setenv("SEMANTIC_RULE_MAPPING_PROVIDER_MODE", "bedrock")

        resp = evaluation_standard.propose_evaluation_standard_ai_candidates(
            {"pathParameters": {"id": case_no, "package_id": package_id}}, None,
        )
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["error"]["code"] == "AI_PROVIDER_UNAVAILABLE"

    def test_invalid_provider_mode_fails_fast(self, env, monkeypatch):
        import evaluation_standard

        case_no = "AI-T18-003"
        package_id, _known_reg, _known_ind = _build_and_save_package(case_no)
        monkeypatch.setenv("SEMANTIC_RULE_MAPPING_PROVIDER_MODE", "gemini")

        resp = evaluation_standard.propose_evaluation_standard_ai_candidates(
            {"pathParameters": {"id": case_no, "package_id": package_id}}, None,
        )
        assert resp["statusCode"] == 500
        assert json.loads(resp["body"])["error"]["code"] == "INVALID_SEMANTIC_PROVIDER_MODE"


# ---------------------------------------------------------------------------
# Handler-level smoke test (real API surface, mock mode default)
# ---------------------------------------------------------------------------

class TestHandlerSmoke:
    def test_propose_ai_candidates_handler_default_mock_mode(self, env):
        import evaluation_standard

        case_no = "AI-HANDLER-001"
        package_id, _known_reg, _known_ind = _build_and_save_package(case_no)
        resp = evaluation_standard.propose_evaluation_standard_ai_candidates(
            {"pathParameters": {"id": case_no, "package_id": package_id}}, None,
        )
        assert resp["statusCode"] == 200
        dto = json.loads(resp["body"])
        assert any(f["ai_candidate"] is not None for f in dto["factors"])

    def test_propose_ai_candidates_unknown_package_returns_404(self, env):
        import evaluation_standard

        case_no = "AI-HANDLER-002"
        _make = _build_and_save_package  # ensure case exists in DDB is not required for this 404 path
        resp = evaluation_standard.propose_evaluation_standard_ai_candidates(
            {"pathParameters": {"id": case_no, "package_id": "NO-SUCH-PKG"}}, None,
        )
        assert resp["statusCode"] == 404
