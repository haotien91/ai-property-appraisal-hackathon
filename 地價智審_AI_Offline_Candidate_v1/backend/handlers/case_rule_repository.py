# -*- coding: utf-8 -*-
"""
case_rule_repository.py — storage abstraction for CaseRulePackage records
(docs/audit/CASE_SCOPED_RULE_ARCHITECTURE_REPORT.md).

Mirrors case_store.py's existing PK=CASE#<case_no> / SK=<record type>
single-table design rather than inventing a second storage mechanism --
CaseRulePackage records live in the SAME DynamoDB table as everything else
case_store.py already manages, under two SK families:

    SK = "CASE_RULE_PACKAGE#<package_id>"   one item per package version
    SK = "CASE_RULE_CONFIRMED_POINTER"      at most one item per case,
                                             {"package_id": "..." | None}
                                             naming the currently CONFIRMED
                                             package (package_id=None means
                                             "no package is confirmed right
                                             now", written explicitly by
                                             reject() rather than deleting
                                             the item)

DynamoCaseRuleRepository is the ONLY concrete implementation. It is already
DynamoDB-backed via case_store.py -- no separate in-memory implementation is
provided, since a real handler invocation (analyze/complete_form/review) may
run in a different warm Lambda container than the one that confirmed a
package, so an in-memory store could never satisfy this architecture's own
cross-invocation persistence requirement. Tests exercise this repository the
same way tests/test_backend_handlers_e2e.py already exercises case_store.py:
via moto's mock_aws() against a real boto3 DynamoDB client, not a parallel
test-only implementation that could drift from production behavior.

CONFIRMED Gate: confirm() re-runs engine.rule_table_validator.RuleTableValidator
against the package's regional_rules+individual_rules and refuses (raises
CaseRulePackageInvalidError) if any ERROR-severity issue is found. Only ONE
package may be CONFIRMED per case_id at a time -- confirm() raises
CaseRulePackageAlreadyConfirmedError if a different package is already
CONFIRMED; the caller must reject() it first. Neither gate is bypassable by
calling save_candidate() again with a pre-set status="CONFIRMED": confirm()
is the only method that ever transitions a package INTO CONFIRMED.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import case_store
from domain.models import CaseRulePackage, CaseRulePackageStatus, RuleFieldEdit
from rule_table_validator import RuleTableValidator
from evaluation_standard_importer import reevaluate_candidate_with_canonical_factor, candidate_to_rule_records, RuleCandidate
from semantic_rule_mapping_provider import SemanticRuleMappingProvider, MockSemanticRuleMappingProvider
from competition_rule_profiles import COMPETITION_RULE_PROFILE_REGISTRY


class CaseRulePackageNotFoundError(Exception):
    """Raised by get_package()-dependent operations (confirm/reject) when
    package_id does not exist for the given case_id. Never a silent no-op."""


class CaseRulePackageInvalidError(Exception):
    """Raised by confirm() (the CONFIRMED Gate) and, independently, by
    backend/handlers/rule_engine_factory.py::build_rule_engine_for_case()
    (defense-in-depth re-validation of an already-CONFIRMED package) when
    RuleTableValidator.has_errors() is True. Carries the raw ValidationIssue
    list so a caller can surface CASE_RULE_INVALID / MANUAL_REVIEW_REQUIRED
    with specifics -- never a silent fallback to the static baseline."""

    def __init__(self, message: str, issues=None):
        super().__init__(message)
        self.issues = list(issues or [])


class CaseRulePackageAlreadyConfirmedError(Exception):
    """Raised by confirm() when a DIFFERENT package_id for the same case_id
    is already CONFIRMED. This repository never silently supersedes a
    CONFIRMED package -- the caller must reject() the existing one first."""


class CaseRulePackageNotEditableError(Exception):
    """Raised by submit_human_edits()/resolve_candidate_factor_mapping()
    when the target package is already CONFIRMED or REJECTED. A CONFIRMED
    package must be reject()ed (creating room for a new candidate) rather
    than mutated in place -- editing an already-in-production package
    in place would let production results change without a fresh
    CONFIRMED Gate re-validation. A REJECTED package is simply dead."""


class CaseRuleCandidateNotFoundError(Exception):
    """Raised by resolve_candidate_factor_mapping() when candidate_id does
    not exist in package.metadata['extraction_candidates']."""


class RuleRecordNotFoundError(Exception):
    """Raised by submit_human_edits() when an edit names a rule_id that
    does not exist in the package's current regional_rules/individual_rules."""


def _package_sk(package_id: str) -> str:
    return f"CASE_RULE_PACKAGE#{package_id}"


_PACKAGE_SK_PREFIX = "CASE_RULE_PACKAGE#"
_CONFIRMED_POINTER_SK = "CASE_RULE_CONFIRMED_POINTER"

# Phase 3B §3's explicit editable-field list, mapped onto the ACTUAL
# rule_schema.json dict keys candidate_to_rule_records() produces --
# "grade label"/"grade condition" are the SAME stored field (grade_label);
# "factor mapping" is handled separately by resolve_candidate_factor_
# mapping() (it operates on the Importer's raw candidate, before a rule
# record even exists) rather than here; "applicability" is a special
# pseudo-field (see submit_human_edits()) that removes a record rather
# than patching it.
_EDITABLE_FIELDS = {
    "grade_label", "lower_bound", "upper_bound", "lower_inclusive", "upper_inclusive",
    "unit", "adjustment_matrix", "max_adjustment", "factor", "applicability",
}


def _decimal_to_native(value):
    """case_store.put_record() converts every float to Decimal before
    writing to DynamoDB (boto3's Table resource rejects native float --
    see case_store._dynamodb_safe), and reading it back returns Decimal,
    never float. regional_rules/individual_rules are plain rule_schema.json
    -shaped dicts (lower_bound, max_adjustment, adjustment_matrix values,
    ...) that RuleEngine/RuleTableValidator/AdjustmentEngine expect as
    float/int -- exactly the same types json.load() produces for the
    static data/rules/*.json files. Converting back here keeps a
    case-scoped rule dict numerically IDENTICAL in type-shape to a static
    one regardless of which storage round-trip it came from, so neither
    RuleEngine's numeric comparisons nor RuleTableValidator's numeric
    arithmetic (float(x) - other, which raises TypeError for a bare
    Decimal-vs-float mix) can behave differently for a case-scoped rule
    than for a static one."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_native(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_native(v) for v in value]
    return value


def validate_package_rules_scoped(regional_rules: list, individual_rules: list):
    """Runs RuleTableValidator SEPARATELY on regional_rules and
    individual_rules, never on their concatenation. RuleTableValidator
    groups issues purely by (city, district, land_use_type, factor) with
    no rule_set/scope awareness -- some factor NAMES are legitimately
    reused across both scopes with different meanings (建蔽率/容積率/地勢
    exist in both data/rules/regional_rules.json and individual_rules.json
    today, the exact AmbiguousFactorError precedent -- see
    docs/phase3/source_anomalies.md ANOMALY-06). Validating the
    concatenation would misreport EVERY such factor as a duplicate
    grade_code ERROR -- confirmed empirically: even the real, already-
    Golden-validated regional_rules.json + individual_rules.json produces
    4 such false ERRORs when concatenated and validated together. Scope
    separation here mirrors how RuleEngine itself scopes rule_id lookups
    (REG-/IND- prefix), so a package is judged the same way it will
    actually be used."""
    return RuleTableValidator().validate(regional_rules) + RuleTableValidator().validate(individual_rules)


def _strip_storage_metadata(data: dict) -> dict:
    """case_store.get_record()/query_records_by_sk_prefix() may carry a
    "_updated_at" bookkeeping key that is not part of the CaseRulePackage
    schema (model_config extra="forbid") -- stripped before validation.
    Also converts any Decimal leaves back to float/int -- see
    _decimal_to_native()."""
    return {k: _decimal_to_native(v) for k, v in data.items() if k != "_updated_at"}


class CaseRuleRepository:
    """Interface only -- documents the contract so a future backend swap
    (e.g. a dedicated table instead of case_store.py's shared one) only
    needs to implement this shape. No behavior of its own."""

    def save_candidate(self, package: CaseRulePackage) -> CaseRulePackage:
        raise NotImplementedError

    def get_package(self, case_id: str, package_id: str) -> Optional[CaseRulePackage]:
        raise NotImplementedError

    def list_packages(self, case_id: str) -> List[CaseRulePackage]:
        raise NotImplementedError

    def confirm(self, case_id: str, package_id: str, confirmed_by: str) -> CaseRulePackage:
        raise NotImplementedError

    def reject(self, case_id: str, package_id: str, reason: str, rejected_by: Optional[str] = None) -> CaseRulePackage:
        raise NotImplementedError

    def get_confirmed_package(self, case_id: str) -> Optional[CaseRulePackage]:
        raise NotImplementedError

    def submit_human_edits(self, case_id: str, package_id: str, edits: List[Dict[str, Any]],
                            edited_by: str) -> CaseRulePackage:
        raise NotImplementedError

    def resolve_candidate_factor_mapping(self, case_id: str, package_id: str, candidate_id: str,
                                          canonical_factor_id: str, edited_by: str) -> CaseRulePackage:
        raise NotImplementedError

    def propose_ai_candidates(self, case_id: str, package_id: str,
                               provider: Optional[SemanticRuleMappingProvider] = None) -> CaseRulePackage:
        raise NotImplementedError


class DynamoCaseRuleRepository(CaseRuleRepository):
    def save_candidate(self, package: CaseRulePackage) -> CaseRulePackage:
        """Persists a DRAFT/EXTRACTED/PARTIAL candidate as-is (never mutates
        status -- only confirm()/reject() do that). Runs RuleTableValidator
        eagerly so WARNING-severity issues are visible on the package right
        away (non-blocking -- a draft is allowed to be imperfect); ERROR
        issues are recorded the same way here but do NOT block save_candidate
        itself, only confirm() actually gates on them."""
        if not package.case_id.strip():
            raise ValueError("case_id 不得為空")
        if not package.package_id.strip():
            raise ValueError("package_id 不得為空")

        issues = validate_package_rules_scoped(package.regional_rules, package.individual_rules)
        merged_warnings = list(package.warnings)
        for issue in issues:
            text = str(issue)
            if text not in merged_warnings:
                merged_warnings.append(text)
        package.warnings = merged_warnings

        case_store.put_record(package.case_id, _package_sk(package.package_id),
                               package.model_dump(mode="json"))
        return package

    def get_package(self, case_id: str, package_id: str) -> Optional[CaseRulePackage]:
        data = case_store.get_record(case_id, _package_sk(package_id))
        if data is None:
            return None
        return CaseRulePackage.model_validate(_strip_storage_metadata(data))

    def list_packages(self, case_id: str) -> List[CaseRulePackage]:
        rows = case_store.query_records_by_sk_prefix(case_id, _PACKAGE_SK_PREFIX)
        return [CaseRulePackage.model_validate(_strip_storage_metadata(r)) for r in rows]

    def confirm(self, case_id: str, package_id: str, confirmed_by: str) -> CaseRulePackage:
        target = self.get_package(case_id, package_id)
        if target is None:
            raise CaseRulePackageNotFoundError(f"case_id={case_id!r} package_id={package_id!r} 不存在")

        pointer = case_store.get_record(case_id, _CONFIRMED_POINTER_SK)
        currently_confirmed_id = (pointer or {}).get("package_id")
        if currently_confirmed_id and currently_confirmed_id != package_id:
            raise CaseRulePackageAlreadyConfirmedError(
                f"case_id={case_id!r} 已有 CONFIRMED package_id={currently_confirmed_id!r}，"
                f"請先呼叫 reject() 該 package 才能 confirm 新版本"
            )

        issues = validate_package_rules_scoped(target.regional_rules, target.individual_rules)
        if RuleTableValidator.has_errors(issues):
            error_count = sum(1 for i in issues if i.severity == "ERROR")
            raise CaseRulePackageInvalidError(
                f"case_id={case_id!r} package_id={package_id!r} 未通過 RuleTableValidator"
                f"（{error_count} 個 ERROR），無法 CONFIRMED",
                issues=issues,
            )

        # SHULIN-COMPETITION-RULE-PACK-A2 Task 13: a package that OPTS IN to
        # being competition-profiled (by setting metadata["competition_
        # profile"]) must declare a source identity matching
        # COMPETITION_RULE_PROFILE_REGISTRY exactly, or it may never reach
        # CONFIRMED -- rule_engine_factory.py's own re-check at resolution
        # time is defense-in-depth on TOP of this, not a substitute for it.
        # A package that never sets this metadata key (every existing/
        # legacy Jinshan package) is completely unaffected -- this whole
        # block is a no-op for them.
        declared_profile = (target.metadata or {}).get("competition_profile")
        if declared_profile:
            profile_id = declared_profile.get("profile_id")
            expected = COMPETITION_RULE_PROFILE_REGISTRY.get(profile_id)
            if expected is None:
                raise CaseRulePackageInvalidError(
                    f"case_id={case_id!r} package_id={package_id!r}: metadata.competition_profile."
                    f"profile_id={profile_id!r} 不是已知的 competition profile，無法 CONFIRMED"
                )
            mismatch = (
                declared_profile.get("district") != expected["district"]
                or declared_profile.get("land_use_type") != expected["land_use_type"]
                or declared_profile.get("source_document") != expected["source_document"]
                or declared_profile.get("source_sha256") != expected["source_sha256"]
            )
            if mismatch:
                raise CaseRulePackageInvalidError(
                    f"case_id={case_id!r} package_id={package_id!r}: metadata.competition_profile="
                    f"{declared_profile!r} 與 profile_id={profile_id!r} 的正式來源身份"
                    f"{expected!r} 不符，無法 CONFIRMED（source identity 驗證失敗）"
                )

        # Atomicity (docs/audit/EVALUATION_STANDARD_HUMAN_CONFIRMATION_
        # PHASE3B_REPORT.md §8): status and regional_rules/individual_
        # rules are fields on the SAME Pydantic object, serialized and
        # written via ONE put_record()/PutItem call below -- DynamoDB's
        # PutItem is atomic per-item, so "status=CONFIRMED but rules not
        # written" and "rules written but status stale" are both
        # structurally impossible; there is no two-step window between
        # them. The SEPARATE _CONFIRMED_POINTER_SK write after it is not
        # part of that guarantee, but rule_engine_factory.build_rule_
        # engine_for_case() never reads the pointer -- it scans list_
        # packages() and filters status==CONFIRMED directly -- so a
        # crash between these two writes cannot make production silently
        # use the wrong rules; worst case is get_confirmed_package()
        # returning None until the pointer catches up (see that method's
        # own defense-in-depth check), never a wrong answer.
        target.status = CaseRulePackageStatus.CONFIRMED
        target.confirmed_at = datetime.now(timezone.utc)
        target.confirmed_by = confirmed_by
        case_store.put_record(case_id, _package_sk(package_id), target.model_dump(mode="json"))
        case_store.put_record(case_id, _CONFIRMED_POINTER_SK, {"package_id": package_id})
        return target

    def reject(self, case_id: str, package_id: str, reason: str, rejected_by: Optional[str] = None) -> CaseRulePackage:
        target = self.get_package(case_id, package_id)
        if target is None:
            raise CaseRulePackageNotFoundError(f"case_id={case_id!r} package_id={package_id!r} 不存在")

        target.status = CaseRulePackageStatus.REJECTED
        target.rejected_at = datetime.now(timezone.utc)
        target.rejected_by = rejected_by
        target.rejection_reason = reason
        case_store.put_record(case_id, _package_sk(package_id), target.model_dump(mode="json"))

        pointer = case_store.get_record(case_id, _CONFIRMED_POINTER_SK)
        if (pointer or {}).get("package_id") == package_id:
            case_store.put_record(case_id, _CONFIRMED_POINTER_SK, {"package_id": None})
        return target

    def _find_rule_record(self, target: CaseRulePackage, rule_id: str):
        for lst in (target.regional_rules, target.individual_rules):
            for r in lst:
                if r.get("rule_id") == rule_id:
                    return lst, r
        return None, None

    def submit_human_edits(self, case_id: str, package_id: str, edits: List[Dict[str, Any]],
                            edited_by: str) -> CaseRulePackage:
        """Applies field-level edits to rule records ALREADY present in
        target.regional_rules/individual_rules (docs/audit/
        EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md §3).
        Each edit is {"rule_id": str, "field": str, "new_value": Any}.
        `field="applicability"` with new_value="NOT_APPLICABLE" removes
        that rule record entirely (its factor does not apply to this
        case) -- the ONLY edit type that deletes rather than patches.
        `field` in {"adjustment_matrix", "max_adjustment"} is applied to
        EVERY grade-row of the SAME factor (not just the named rule_id),
        matching the existing convention that these two fields are
        duplicated identically across a factor's rows -- editing only one
        row would otherwise immediately fail RuleTableValidator's
        cross-row consistency check. Every change appends a RuleFieldEdit
        to edit_history (original_extracted_value/confirmed_value/edited/
        confirmed_by/confirmed_at) -- append-only, nothing is ever
        silently overwritten without a trace (§3's explicit requirement).
        Refuses (CaseRulePackageNotEditableError) once the package is
        CONFIRMED or REJECTED."""
        target = self.get_package(case_id, package_id)
        if target is None:
            raise CaseRulePackageNotFoundError(f"case_id={case_id!r} package_id={package_id!r} 不存在")
        if target.status in (CaseRulePackageStatus.CONFIRMED, CaseRulePackageStatus.REJECTED):
            raise CaseRulePackageNotEditableError(
                f"case_id={case_id!r} package_id={package_id!r} 狀態為 {target.status.value}，"
                f"不可編輯（CONFIRMED 須先 reject() 才能再編輯；REJECTED 永久不可編輯）"
            )

        now = datetime.now(timezone.utc)
        for edit in edits:
            rule_id, field_name, new_value = edit["rule_id"], edit["field"], edit.get("new_value")
            if field_name not in _EDITABLE_FIELDS:
                raise ValueError(f"field={field_name!r} 不在允許編輯清單內：{sorted(_EDITABLE_FIELDS)}")

            container, record = self._find_rule_record(target, rule_id)
            if record is None:
                raise RuleRecordNotFoundError(
                    f"rule_id={rule_id!r} 不存在於 case_id={case_id!r} package_id={package_id!r} 目前的規則列表"
                )

            if field_name == "applicability":
                original_value = "APPLICABLE"
                if new_value == "NOT_APPLICABLE":
                    container.remove(record)
                target.edit_history.append(RuleFieldEdit(
                    rule_id=rule_id, field="applicability", original_extracted_value=original_value,
                    confirmed_value=new_value, confirmed_by=edited_by, confirmed_at=now,
                ))
                continue

            targets = [record]
            if field_name in ("adjustment_matrix", "max_adjustment"):
                factor = record.get("factor")
                targets = [r for r in container if r.get("factor") == factor]
            for r in targets:
                original_value = r.get(field_name)
                r[field_name] = new_value
                target.edit_history.append(RuleFieldEdit(
                    rule_id=r.get("rule_id"), field=field_name, original_extracted_value=original_value,
                    confirmed_value=new_value, confirmed_by=edited_by, confirmed_at=now,
                ))

        issues = validate_package_rules_scoped(target.regional_rules, target.individual_rules)
        merged_warnings = list(target.warnings)
        for issue in issues:
            text = str(issue)
            if text not in merged_warnings:
                merged_warnings.append(text)
        target.warnings = merged_warnings

        case_store.put_record(case_id, _package_sk(package_id), target.model_dump(mode="json"))
        return target

    def resolve_candidate_factor_mapping(self, case_id: str, package_id: str, candidate_id: str,
                                          canonical_factor_id: str, edited_by: str) -> CaseRulePackage:
        """Human resolves the "factor mapping" edit (§3) for ONE candidate
        that the Importer could not exact-match (UNKNOWN_FACTOR) or that
        the human wants to remap. Reconstructs the candidate's FULL data
        (bands/matrix -- see RuleCandidate.to_dict(), stored by the
        Importer, no PDF re-parse needed) from package.metadata
        ['extraction_candidates'], re-validates it with the human-supplied
        canonical_factor_id (engine/evaluation_standard_importer.py::
        reevaluate_candidate_with_canonical_factor() -- UNKNOWN_FACTOR
        clears, but any OTHER genuine issue, e.g. MATRIX_INCOMPLETE, still
        blocks it from becoming usable rule records). Only when the
        candidate re-validates to EXTRACTED are rule records generated
        and merged into regional_rules/individual_rules (replacing any
        records from a PRIOR resolution attempt for this exact
        candidate_id, matched precisely via the candidate_id embedded in
        rule_id -- see candidate_to_rule_records()). The stored candidate
        snapshot itself is always updated, even when still not EXTRACTED,
        so the Review DTO reflects the latest attempt."""
        target = self.get_package(case_id, package_id)
        if target is None:
            raise CaseRulePackageNotFoundError(f"case_id={case_id!r} package_id={package_id!r} 不存在")
        if target.status in (CaseRulePackageStatus.CONFIRMED, CaseRulePackageStatus.REJECTED):
            raise CaseRulePackageNotEditableError(
                f"case_id={case_id!r} package_id={package_id!r} 狀態為 {target.status.value}，不可編輯"
            )

        candidates = target.metadata.get("extraction_candidates", [])
        candidate_dict = next((c for c in candidates if c.get("candidate_id") == candidate_id), None)
        if candidate_dict is None:
            raise CaseRuleCandidateNotFoundError(
                f"candidate_id={candidate_id!r} 不存在於 case_id={case_id!r} package_id={package_id!r}"
            )

        candidate = RuleCandidate.from_dict(candidate_dict)
        original_canonical = candidate.canonical_factor_id
        reevaluate_candidate_with_canonical_factor(candidate, canonical_factor_id)

        now = datetime.now(timezone.utc)
        target.edit_history.append(RuleFieldEdit(
            rule_id=f"candidate:{candidate_id}", field="canonical_factor_id",
            original_extracted_value=original_canonical, confirmed_value=canonical_factor_id,
            confirmed_by=edited_by, confirmed_at=now,
        ))

        for i, c in enumerate(candidates):
            if c.get("candidate_id") == candidate_id:
                candidates[i] = candidate.to_dict()
                break
        target.metadata["extraction_candidates"] = candidates

        container = target.regional_rules if candidate.scope == "regional" else target.individual_rules
        container[:] = [r for r in container if candidate_id not in r.get("rule_id", "")]
        if candidate.status == "EXTRACTED":
            container.extend(candidate_to_rule_records(candidate, package_id))

        case_store.put_record(case_id, _package_sk(package_id), target.model_dump(mode="json"))
        return target

    def propose_ai_candidates(self, case_id: str, package_id: str,
                               provider: Optional[SemanticRuleMappingProvider] = None,
                               known_regional_factors: Optional[List[str]] = None,
                               known_individual_factors: Optional[List[str]] = None) -> CaseRulePackage:
        """Phase 3C §1's ONLY entry point that may call a
        SemanticRuleMappingProvider: iterates package.metadata
        ['extraction_candidates'] and calls the provider ONLY for
        candidates the deterministic Importer already flagged non-
        EXTRACTED (status != "EXTRACTED", i.e. carries at least one of
        UNKNOWN_FACTOR / RULE_EXTRACTION_AMBIGUOUS / RULE_EXTRACTION_
        INCOMPLETE / MATRIX_INCOMPLETE) -- an already-EXTRACTED candidate
        is NEVER passed to the provider (§1 "Deterministic已成功解析的
        Rule不得再送AI重判"), enforced here by the `if c.get("status") ==
        "EXTRACTED": continue` skip below, not by convention.

        known_regional_factors/known_individual_factors are the SAME
        canonical-factor whitelists the deterministic matcher itself was
        given (engine/evaluation_standard_importer.py::build_case_rule_
        package_from_pdf()'s own parameters, normally the full `factor`
        vocabulary from data/rules/regional_rules.json/individual_rules.
        json) -- callers (backend/handlers/evaluation_standard.py) pass
        the SAME lists so the AI sees the SAME universe of valid answers
        the deterministic exact-match already tried, not some separately
        -maintained list. If omitted (e.g. a repository-level test that
        doesn't care), falls back to whatever THIS package's own already-
        resolved candidates/rules happen to use -- a smaller, self-
        referential set, fine for isolated testing but not what real
        usage should rely on.

        Every result (OK / SCHEMA_INVALID / SCOPE_VIOLATION /
        PROVIDER_UNAVAILABLE) is stored verbatim in package.metadata
        ['ai_semantic_candidates'][candidate_id] -- this method NEVER
        writes into regional_rules/individual_rules itself, regardless of
        confidence (§4: "所有AI candidate都必須經Human Confirmation").
        A human accepts an AI suggestion by calling the EXISTING
        resolve_candidate_factor_mapping()/submit_human_edits() with
        whatever value they choose (which may or may not be the AI's
        candidate_factor_id) -- there is no separate "accept AI
        candidate" code path that could bypass that same human-driven,
        re-validated confirmation flow."""
        target = self.get_package(case_id, package_id)
        if target is None:
            raise CaseRulePackageNotFoundError(f"case_id={case_id!r} package_id={package_id!r} 不存在")
        if target.status in (CaseRulePackageStatus.CONFIRMED, CaseRulePackageStatus.REJECTED):
            raise CaseRulePackageNotEditableError(
                f"case_id={case_id!r} package_id={package_id!r} 狀態為 {target.status.value}，不可編輯"
            )

        provider = provider or MockSemanticRuleMappingProvider()
        candidates = target.metadata.get("extraction_candidates", [])
        ai_results = dict(target.metadata.get("ai_semantic_candidates", {}))

        for c in candidates:
            if c.get("status") == "EXTRACTED":
                continue  # never re-judge a cleanly-resolved candidate (§1)
            known_names = self._known_factor_names_for_scope(
                target, c.get("scope"), known_regional_factors, known_individual_factors,
            )
            result = provider.propose_candidate(
                candidate_id=c["candidate_id"], original_text=c.get("note_text_candidate") or "",
                deterministic_failure_reasons=c.get("issues", []),
                scope=c.get("scope"), city=c.get("city"), district=c.get("district"),
                land_use_type=c.get("land_use_type"), known_factor_names=known_names,
            )
            ai_results[c["candidate_id"]] = json.loads(result.model_dump_json())

        target.metadata["ai_semantic_candidates"] = ai_results
        case_store.put_record(case_id, _package_sk(package_id), target.model_dump(mode="json"))
        return target

    @staticmethod
    def _known_factor_names_for_scope(package: CaseRulePackage, scope: Optional[str],
                                       known_regional_factors: Optional[List[str]],
                                       known_individual_factors: Optional[List[str]]) -> List[str]:
        """The candidate-name whitelist an AI provider may choose from,
        scoped to regional vs individual so a provider can never propose a
        factor from the wrong scope. Prefers the caller-supplied full
        canonical lists (see propose_ai_candidates() docstring); falls
        back to whatever this package's own already-resolved candidates/
        rules happen to use only when the caller didn't supply one."""
        supplied = known_regional_factors if scope == "regional" else known_individual_factors
        if supplied:
            return sorted({n for n in supplied if n})

        candidates = package.metadata.get("extraction_candidates", [])
        names = {c.get("canonical_factor_id") for c in candidates
                 if c.get("scope") == scope and c.get("canonical_factor_id")}
        container = package.regional_rules if scope == "regional" else package.individual_rules
        names.update(r.get("factor") for r in container if r.get("factor"))
        return sorted(n for n in names if n)

    def get_confirmed_package(self, case_id: str) -> Optional[CaseRulePackage]:
        pointer = case_store.get_record(case_id, _CONFIRMED_POINTER_SK)
        package_id = (pointer or {}).get("package_id")
        if not package_id:
            return None
        package = self.get_package(case_id, package_id)
        # Defense in depth: the pointer should only ever name a CONFIRMED
        # package by construction (only confirm() writes it), but if state
        # ever drifted, the safest behavior is "no confirmed package" (the
        # static baseline) rather than trusting a stale/corrupted pointer.
        if package is None or package.status != CaseRulePackageStatus.CONFIRMED:
            return None
        return package


def build_review_dto(package: CaseRulePackage) -> Dict[str, Any]:
    """Pure function (no I/O): UI-ready Review DTO (docs/audit/
    EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md §6). Derived
    entirely from data already on the package -- package.metadata
    ['extraction_candidates'] (the Importer's full per-candidate snapshot,
    kept fresh by resolve_candidate_factor_mapping()) for the per-factor
    review list, and the CURRENT regional_rules/individual_rules (which
    reflect any submit_human_edits() patches) for the validation summary."""
    candidates = package.metadata.get("extraction_candidates", [])
    ai_candidates = package.metadata.get("ai_semantic_candidates", {})
    status_counts: Dict[str, int] = {}
    for c in candidates:
        status_counts[c.get("status", "AMBIGUOUS")] = status_counts.get(c.get("status", "AMBIGUOUS"), 0) + 1

    issues = validate_package_rules_scoped(package.regional_rules, package.individual_rules)
    validation_error_count = sum(1 for i in issues if i.severity == "ERROR")
    warning_count = sum(1 for i in issues if i.severity == "WARNING")

    factors = []
    for c in candidates:
        matrix = {}
        bands = c.get("bands", [])
        for row in bands:
            matrix[str(row["grade_code"])] = {
                str(other["grade_code"]): row["matrix_row"][i] if i < len(row["matrix_row"]) else None
                for i, other in enumerate(bands)
            }
        factors.append({
            "factor_id": c.get("candidate_id"),
            "scope": c.get("scope"),
            "original_factor_text": c.get("note_text_candidate"),
            "canonical_factor_id": c.get("canonical_factor_id"),
            "grade_conditions": [
                {
                    "grade": b["grade"], "grade_code": b["grade_code"], "condition_text": b["condition_text"],
                    "value_type": b["value_type"], "lower_bound": b["lower_bound"], "upper_bound": b["upper_bound"],
                    "unit": b["unit"], "anomaly_flag": b.get("anomaly_flag"),
                }
                for b in bands
            ],
            "matrix": matrix,
            "max_adjustment": c.get("max_adjustment_declared"),
            "status": c.get("status"),
            "issues": c.get("issues", []),
            "requires_human_review": c.get("status") != "EXTRACTED",
            # AI Semantic Fallback (docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_
            # REPORT.md) -- present ONLY when propose_ai_candidates() was
            # ever called for this candidate; ALWAYS advisory (see that
            # report's §4) -- a human still calls resolve_candidate_
            # factor_mapping()/submit_human_edits() to act on it, nothing
            # here is auto-applied regardless of confidence.
            "ai_candidate": ai_candidates.get(c.get("candidate_id")),
        })

    return {
        "package_id": package.package_id, "case_id": package.case_id, "status": package.status.value,
        "summary": {
            "total_factors": len(candidates),
            "extracted": status_counts.get("EXTRACTED", 0),
            "partial": status_counts.get("PARTIAL", 0),
            "ambiguous": status_counts.get("AMBIGUOUS", 0),
            "validation_error_count": validation_error_count,
            "warning_count": warning_count,
        },
        "factors": factors,
    }


_default_repository: Optional[DynamoCaseRuleRepository] = None


def default_case_rule_repository() -> DynamoCaseRuleRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = DynamoCaseRuleRepository()
    return _default_repository
