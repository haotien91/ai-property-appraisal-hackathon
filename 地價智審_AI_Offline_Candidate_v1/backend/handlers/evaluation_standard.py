# -*- coding: utf-8 -*-
"""
evaluation_standard.py — minimal backend interface for the Evaluation
Standard Importer -> AI Semantic Fallback -> Human Confirmation ->
CaseRuleRepository workflow (docs/audit/EVALUATION_STANDARD_HUMAN_
CONFIRMATION_PHASE3B_REPORT.md, docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_
REPORT.md).

Reuses the EXISTING document-upload primitive (document_upload.py's
RequestUploadFunction, unmodified) rather than inventing a new upload
mechanism: a client uploads the evaluation-standard PDF exactly like any
other case document (POST .../documents -> presigned URL -> client PUTs
the file), then extract_evaluation_standard() below reads it back from
DocumentBucket by document_id, mirroring document_extract.py's own S3
read/validate pattern almost verbatim.

Six handlers, all thin wrappers over engine/evaluation_standard_importer.py
(extraction), providers/semantic_rule_mapping_provider.py (AI Semantic
Fallback, ADVISORY only), and case_rule_repository.py (persistence/edits/
confirmation) -- this file contains NO grading/adjustment/validation logic
of its own:

    extract_evaluation_standard        -- PDF (already uploaded) -> save_candidate()
    propose_evaluation_standard_ai_candidates -- AI semantic candidates for non-EXTRACTED factors
    get_evaluation_standard_candidate  -- read back a Review DTO
    submit_evaluation_standard_edits   -- human edits / factor-mapping fixes
    confirm_evaluation_standard        -- CONFIRMED Gate (delegates to repository)
    reject_evaluation_standard         -- REJECTED (delegates to repository)

Case isolation (§9) is enforced structurally, not by an extra ownership
check here: every CaseRuleRepository method takes case_id from the URL
path and looks up records under PK=CASE#<case_id> only -- a package_id
that actually belongs to a different case simply cannot be found (get_
package returns None), which these handlers surface as 404, never a
cross-case read/write.
"""
from __future__ import annotations

import os
import json
import tempfile
from datetime import datetime, timezone

import runtime_paths  # noqa: E402
runtime_paths.bootstrap()

from common import response, error_response, parse_body, timed_step  # noqa: E402
import case_store  # noqa: E402
import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from evaluation_standard_importer import build_case_rule_package_from_pdf  # noqa: E402
from case_rule_repository import (  # noqa: E402
    default_case_rule_repository, build_review_dto,
    CaseRulePackageNotFoundError, CaseRulePackageInvalidError, CaseRulePackageAlreadyConfirmedError,
    CaseRulePackageNotEditableError, CaseRuleCandidateNotFoundError, RuleRecordNotFoundError,
)
from semantic_rule_mapping_provider import MockSemanticRuleMappingProvider, BedrockSemanticRuleMappingProvider  # noqa: E402

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET_NAME", "ai-valuation-documents")
MAX_DOCUMENT_SIZE_BYTES = int(os.environ.get("MAX_DOCUMENT_SIZE_BYTES", str(20 * 1024 * 1024)))

# Same "unset -> explicit mock default; anything else must be exactly one
# of the known values, or fail fast" convention as collect_data.py's
# DATA_PROVIDER_MODE (see the provider-contract skill) -- never silently
# coerced to "mock" from a typo.
_VALID_SEMANTIC_PROVIDER_MODES = ("mock", "bedrock")


class InvalidSemanticProviderModeError(Exception):
    pass


def _resolve_semantic_provider():
    mode = os.environ.get("SEMANTIC_RULE_MAPPING_PROVIDER_MODE", "mock")
    if mode not in _VALID_SEMANTIC_PROVIDER_MODES:
        raise InvalidSemanticProviderModeError(
            f"SEMANTIC_RULE_MAPPING_PROVIDER_MODE 必須為 {_VALID_SEMANTIC_PROVIDER_MODES} 之一，收到：{mode!r}"
        )
    if mode == "bedrock":
        # BedrockSemanticRuleMappingProvider is CODE_READY, NOT AWS_RUNTIME_
        # VERIFIED -- its propose_candidate() itself raises NotImplementedError
        # rather than attempting a live call this environment has never
        # verified works (see that class's own docstring). Selecting this
        # mode is intentionally still wired through so a real deployment
        # with actual Bedrock access can switch to it via env var alone,
        # without a code change.
        return BedrockSemanticRuleMappingProvider()
    return MockSemanticRuleMappingProvider()

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _known_factor_lists():
    data_dir = runtime_paths.data_dir()
    with open(os.path.join(data_dir, "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(data_dir, "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return sorted({r["factor"] for r in reg}), sorted({r["factor"] for r in ind})


def extract_evaluation_standard(event, context):
    """POST /api/cases/{id}/evaluation-standard/{document_id}/extract.
    document_id must already have a completed S3 upload (same document_
    upload.py -> client PUT flow every other document handler uses).
    Never auto-confirms -- see build_case_rule_package_from_pdf()'s own
    status derivation (EXTRACTED/PARTIAL/AMBIGUOUS only)."""
    case_no = event.get("pathParameters", {}).get("id")
    document_id = event.get("pathParameters", {}).get("document_id")
    body = parse_body(event)
    with timed_step(case_no, "extract_evaluation_standard"):
        meta = case_store.get_case_meta(case_no)
        if meta is None:
            return error_response(404, "CASE_NOT_FOUND", f"找不到案件 {case_no}")

        doc_meta = case_store.get_record(case_no, case_store.document_sk(document_id))
        if doc_meta is None:
            return error_response(404, "DOCUMENT_NOT_FOUND", f"找不到文件 {document_id}")

        # STEP5 §1 dual-input contract -- mirror of document_extract.py's
        # own guard, same opt-in semantics (see document_upload.py).
        doc_type = doc_meta.get("document_type")
        if doc_type is not None and doc_type != "EVALUATION_STANDARD":
            return error_response(
                400, "WRONG_DOCUMENT_TYPE",
                f"此文件已標記為 {doc_type}，非評價基準明細表，不可送入評價基準擷取流程",
            )

        s3 = _s3_client()
        s3_key = doc_meta["s3_key"]
        try:
            head = s3.head_object(Bucket=DOCUMENT_BUCKET, Key=s3_key)
        except ClientError:
            return error_response(
                400, "DOCUMENT_NOT_UPLOADED",
                "尚未偵測到已上傳之文件內容，請確認 PUT 至 upload_url 是否已成功完成",
            )
        if head.get("ContentLength", 0) > MAX_DOCUMENT_SIZE_BYTES:
            return error_response(400, "DOCUMENT_TOO_LARGE", "文件大小超過上限")

        tmp_path = os.path.join(tempfile.gettempdir(), f"{document_id}.pdf")
        try:
            s3.download_file(DOCUMENT_BUCKET, s3_key, tmp_path)
            with open(tmp_path, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                return error_response(400, "INVALID_PDF", "檔案內容非有效 PDF（magic bytes 不符 %PDF-）")

            package_id = body.get("package_id") or f"EVALSTD-{document_id}"
            rule_version = body.get("rule_version") or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            known_reg, known_ind = _known_factor_lists()

            package, _report = build_case_rule_package_from_pdf(
                tmp_path, case_id=case_no, package_id=package_id, rule_version=rule_version,
                known_regional_factors=known_reg, known_individual_factors=known_ind,
                created_at=datetime.now(timezone.utc),
            )
            # source_document must identify the ORIGINAL uploaded object,
            # not the ephemeral /tmp path build_case_rule_package_from_pdf()
            # read bytes from.
            package.source_document = s3_key
        except Exception as e:
            return error_response(400, "EXTRACTION_FAILED", f"評價基準明細表解析失敗：{e}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        repository = default_case_rule_repository()
        saved = repository.save_candidate(package)
        return response(201, build_review_dto(saved))


def propose_evaluation_standard_ai_candidates(event, context):
    """POST /api/cases/{id}/evaluation-standard/{package_id}/ai-candidates.
    AI Semantic Fallback (docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_REPORT.md)
    -- ALWAYS advisory: only ever populates package.metadata
    ['ai_semantic_candidates'], never regional_rules/individual_rules. A
    human still calls submit_evaluation_standard_edits (whose body may
    name any candidate_factor_id, including but not required to be the
    AI's suggestion) to actually act on a proposal."""
    case_no = event.get("pathParameters", {}).get("id")
    package_id = event.get("pathParameters", {}).get("package_id")
    with timed_step(case_no, "propose_evaluation_standard_ai_candidates"):
        try:
            provider = _resolve_semantic_provider()
        except InvalidSemanticProviderModeError as e:
            return error_response(500, "INVALID_SEMANTIC_PROVIDER_MODE", str(e))

        known_reg, known_ind = _known_factor_lists()
        repository = default_case_rule_repository()
        try:
            package = repository.propose_ai_candidates(
                case_no, package_id, provider=provider,
                known_regional_factors=known_reg, known_individual_factors=known_ind,
            )
        except CaseRulePackageNotFoundError:
            return error_response(404, "CASE_RULE_PACKAGE_NOT_FOUND", f"找不到 package {package_id}")
        except CaseRulePackageNotEditableError as e:
            return error_response(409, "CASE_RULE_NOT_EDITABLE", str(e))
        except NotImplementedError as e:
            # BedrockSemanticRuleMappingProvider is CODE_READY, NOT
            # AWS_RUNTIME_VERIFIED (see its own docstring) -- this is the
            # honest, expected outcome of selecting SEMANTIC_RULE_MAPPING_
            # PROVIDER_MODE=bedrock in an environment with no verified
            # Bedrock access, never silently substituted with a Mock
            # result (see "No Real->Mock silent fallback", Phase 3C §9).
            return error_response(503, "AI_PROVIDER_UNAVAILABLE", str(e))

        return response(200, build_review_dto(package))


def get_evaluation_standard_candidate(event, context):
    """GET /api/cases/{id}/evaluation-standard/{package_id}."""
    case_no = event.get("pathParameters", {}).get("id")
    package_id = event.get("pathParameters", {}).get("package_id")
    with timed_step(case_no, "get_evaluation_standard_candidate"):
        repository = default_case_rule_repository()
        package = repository.get_package(case_no, package_id)
        if package is None:
            return error_response(404, "CASE_RULE_PACKAGE_NOT_FOUND", f"找不到 package {package_id}")
        return response(200, build_review_dto(package))


def submit_evaluation_standard_edits(event, context):
    """POST /api/cases/{id}/evaluation-standard/{package_id}/edits.
    Body: {"factor_mappings": [{"candidate_id", "canonical_factor_id"}, ...],
           "edits": [{"rule_id", "field", "new_value"}, ...], "edited_by": str}
    Both lists optional (either or both may be supplied); factor_mappings
    are applied first (they can ADD new rule records that a subsequent
    edit in the same request might target)."""
    case_no = event.get("pathParameters", {}).get("id")
    package_id = event.get("pathParameters", {}).get("package_id")
    body = parse_body(event)
    edited_by = body.get("edited_by") or "unknown"
    with timed_step(case_no, "submit_evaluation_standard_edits"):
        repository = default_case_rule_repository()
        try:
            for mapping in body.get("factor_mappings", []):
                repository.resolve_candidate_factor_mapping(
                    case_no, package_id, mapping["candidate_id"], mapping["canonical_factor_id"], edited_by,
                )
            package = None
            edits = body.get("edits", [])
            if edits:
                package = repository.submit_human_edits(case_no, package_id, edits, edited_by)
            else:
                package = repository.get_package(case_no, package_id)
        except CaseRulePackageNotFoundError:
            return error_response(404, "CASE_RULE_PACKAGE_NOT_FOUND", f"找不到 package {package_id}")
        except CaseRuleCandidateNotFoundError as e:
            return error_response(400, "CASE_RULE_CANDIDATE_NOT_FOUND", str(e))
        except RuleRecordNotFoundError as e:
            return error_response(400, "CASE_RULE_RECORD_NOT_FOUND", str(e))
        except CaseRulePackageNotEditableError as e:
            return error_response(409, "CASE_RULE_NOT_EDITABLE", str(e))
        except ValueError as e:
            return error_response(400, "VALIDATION_ERROR", str(e))

        if package is None:
            return error_response(404, "CASE_RULE_PACKAGE_NOT_FOUND", f"找不到 package {package_id}")
        return response(200, build_review_dto(package))


def confirm_evaluation_standard(event, context):
    """POST /api/cases/{id}/evaluation-standard/{package_id}/confirm.
    Never silently falls back -- an invalid or already-superseded package
    returns 409 with validation_issues, not a 200 pretending to succeed."""
    case_no = event.get("pathParameters", {}).get("id")
    package_id = event.get("pathParameters", {}).get("package_id")
    body = parse_body(event)
    confirmed_by = body.get("confirmed_by") or "unknown"
    with timed_step(case_no, "confirm_evaluation_standard"):
        repository = default_case_rule_repository()
        try:
            package = repository.confirm(case_no, package_id, confirmed_by)
        except CaseRulePackageNotFoundError:
            return error_response(404, "CASE_RULE_PACKAGE_NOT_FOUND", f"找不到 package {package_id}")
        except CaseRulePackageAlreadyConfirmedError as e:
            return error_response(409, "CASE_RULE_ALREADY_CONFIRMED", str(e))
        except CaseRulePackageInvalidError as e:
            return response(409, {
                "error": {"code": "CASE_RULE_VALIDATION_FAILED", "message": str(e), "field_id": None, "details": {}},
                "validation_issues": [str(i) for i in e.issues],
            })
        return response(200, build_review_dto(package))


def reject_evaluation_standard(event, context):
    """POST /api/cases/{id}/evaluation-standard/{package_id}/reject."""
    case_no = event.get("pathParameters", {}).get("id")
    package_id = event.get("pathParameters", {}).get("package_id")
    body = parse_body(event)
    reason = body.get("reason") or "未提供原因"
    rejected_by = body.get("rejected_by")
    with timed_step(case_no, "reject_evaluation_standard"):
        repository = default_case_rule_repository()
        try:
            package = repository.reject(case_no, package_id, reason, rejected_by=rejected_by)
        except CaseRulePackageNotFoundError:
            return error_response(404, "CASE_RULE_PACKAGE_NOT_FOUND", f"找不到 package {package_id}")
        return response(200, build_review_dto(package))
