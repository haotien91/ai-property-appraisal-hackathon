# -*- coding: utf-8 -*-
"""
rule_engine_factory.py — the single place analyze.py/complete_form.py/
review.py build a RuleEngine from, replacing three independent
`RuleEngine(reg + ind)` call sites that each hardcoded loading ONLY
data/rules/regional_rules.json + individual_rules.json
(docs/audit/CASE_SCOPED_RULE_ARCHITECTURE_REPORT.md P0 blocker).

Resolution order (Case Rule Override Semantics):

  1. If case_id has exactly one CONFIRMED CaseRulePackage (via
     CaseRuleRepository), its regional_rules/individual_rules take
     SCOPE-LEVEL precedence over the static baseline: a scope (regional or
     individual) the package did not supply falls back to the matching
     STATIC scope. Nothing is merged at the individual-factor level -- see
     the architecture report's rationale for why scope-level replacement is
     the safer starting policy.
  2. Otherwise (no case_id, no package ever submitted, or a package exists
     but was never CONFIRMED) -- the existing static
     data/rules/regional_rules.json + individual_rules.json baseline,
     UNCHANGED from pre-existing behavior. This is NOT a degraded fallback;
     it is the normal baseline every case used before this architecture
     existed, and remains so for every case that never uses a case-scoped
     package.

Never a silent static fallback for an actually-broken CONFIRMED package:
raises CaseRulePackageInvalidError (imported from case_rule_repository) if
a CONFIRMED package fails RuleTableValidator's structural checks at
resolution time -- defense-in-depth on top of CaseRuleRepository.confirm()'s
own CONFIRMED Gate, for the should-never-happen case of state drift after
confirmation.

Deliberately NOT cached at module level. A warm Lambda container that served
Case A's request and then serves Case B's request must never let Case A's
confirmed rules leak into Case B's RuleEngine (see Test 6 / "No Cross-case
Leak" in the architecture report). RuleEngine construction is cheap (pure
in-memory indexing over a few hundred dict records) -- there is no
meaningful performance reason to cache it, and caching is exactly the kind
of shortcut that already produced a real latent bug (analyze.py's
module-level `_RULE_ENGINE` cache, removed as part of this change).

SHULIN-COMPETITION-RULE-PACK-A2 Task 10/11/12 addendum -- Competition Profile
fail-closed resolution:

The "silent STATIC_LOCAL fallback" behavior above is exactly right for every
EXISTING (legacy Jinshan) caller, which never passes `rule_profile_id` --
their behavior is 100% unchanged by this addendum (see every branch below
still short-circuits to the pre-existing logic whenever `rule_profile_id`
is None, which is also every existing call site's implicit default).

But it would be actively unsafe for a case that explicitly IS scoped to a
known competition rule profile (e.g. "shulin_residential_2026", built by
scripts/build_shulin_competition_rule_pack.py) to ever silently compute
grades against data/rules/regional_rules.json/individual_rules.json (the
Jinshan COMMERCIAL rule pack) just because nobody has confirmed a Shulin-
scoped CaseRulePackage yet. `build_rule_engine_for_case()` therefore takes
an EXPLICIT, optional `rule_profile_id` parameter (never inferred from
case_no prefix, district string, or land_use_type alone -- Task 10): when
it names a profile in COMPETITION_RULE_PROFILE_REGISTRY, the function
raises RuleProfileNotReadyError instead of returning STATIC_LOCAL whenever:
  - no package exists at all, or none is CONFIRMED (Task 11), or
  - a CONFIRMED package exists but is missing EITHER regional_rules OR
    individual_rules (Task 12 -- for a competition profile, partial
    per-scope fallback to Jinshan static is exactly as unsafe as a
    complete fallback, so both scopes are required together), or
  - a CONFIRMED package's own metadata.competition_profile does not
    identify itself as this exact profile_id/source_document/source_sha256
    (Task 13 -- defense in depth; the SAME check also runs inside
    case_rule_repository.py's confirm() so a mismatched package can never
    reach CONFIRMED in the first place, but this function does not trust
    that alone).
"""
from __future__ import annotations

import json
import os
from typing import Optional, Tuple

import runtime_paths
from rule_engine import RuleEngine
from rule_table_validator import RuleTableValidator
from domain.models import CaseRulePackageStatus, CaseRuleResolution, CaseRuleTraceInfo, RuleSourceType
from competition_rule_profiles import COMPETITION_RULE_PROFILE_REGISTRY


class RuleProfileNotReadyError(Exception):
    """Raised by build_rule_engine_for_case() (Task 11) when `rule_profile_id`
    names a KNOWN competition profile (COMPETITION_RULE_PROFILE_REGISTRY)
    but no CONFIRMED, complete, correctly-identified CaseRulePackage exists
    for it yet. Deliberately a DISTINCT exception from CaseRulePackage
    InvalidError (a structurally-broken package) and from the ordinary
    STATIC_LOCAL resolution (the pre-existing, still-valid legacy
    behavior for every case that never sets rule_profile_id) -- a caller
    must never catch this and quietly substitute the static Jinshan pack.
    `resolution_status` is always the literal string "RULE_PROFILE_NOT_READY"."""

    resolution_status = "RULE_PROFILE_NOT_READY"

    def __init__(self, message: str, case_id: Optional[str] = None, rule_profile_id: Optional[str] = None):
        super().__init__(message)
        self.case_id = case_id
        self.rule_profile_id = rule_profile_id

from case_rule_repository import (
    CaseRuleRepository, CaseRulePackageInvalidError, default_case_rule_repository, validate_package_rules_scoped,
)


def _load_static_rules() -> Tuple[list, list]:
    data_dir = runtime_paths.data_dir()
    with open(os.path.join(data_dir, "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(data_dir, "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return reg, ind


def _static_trace(rules, source_document_fallback: str) -> dict:
    trace = {}
    for r in rules:
        trace[r["rule_id"]] = CaseRuleTraceInfo(
            rule_source_type=RuleSourceType.STATIC_LOCAL,
            source_document=r.get("source_document") or source_document_fallback,
            rule_version=str(r["version"]) if r.get("version") is not None else None,
        )
    return trace


def _case_trace(rules, case_id: str, package) -> dict:
    trace = {}
    for r in rules:
        trace[r["rule_id"]] = CaseRuleTraceInfo(
            rule_source_type=RuleSourceType.CASE_IMPORTED_CONFIRMED,
            case_id=case_id, package_id=package.package_id,
            source_document=package.source_document, source_sha256=package.source_sha256,
            rule_version=package.rule_version,
        )
    return trace


def build_rule_engine_for_case(
    case_id: Optional[str], repository: Optional[CaseRuleRepository] = None,
    rule_profile_id: Optional[str] = None,
) -> Tuple[RuleEngine, CaseRuleResolution]:
    """Returns (RuleEngine, CaseRuleResolution). See module docstring for
    resolution order and failure semantics.

    rule_profile_id (Task 10): EXPLICIT and OPTIONAL, default None. Every
    existing caller that does not pass it gets EXACTLY the pre-existing
    behavior (legacy Jinshan static fallback, unchanged). Passing a known
    competition profile id (see COMPETITION_RULE_PROFILE_REGISTRY) makes
    every STATIC_LOCAL-fallback branch below raise RuleProfileNotReadyError
    instead (Task 11/12/13) -- never inferred from case_id/district/
    land_use_type."""
    repository = repository or default_case_rule_repository()
    reg_static, ind_static = _load_static_rules()
    static_trace = _static_trace(reg_static, "regional_rules.json")
    static_trace.update(_static_trace(ind_static, "individual_rules.json"))

    competition_profile = (
        COMPETITION_RULE_PROFILE_REGISTRY.get(rule_profile_id) if rule_profile_id else None
    )
    if rule_profile_id and competition_profile is None:
        # An explicitly-named profile that isn't even a KNOWN one is a
        # caller bug, not a "just use static" situation -- fail loudly
        # rather than silently treating an unrecognized profile id as
        # "no profile at all".
        raise RuleProfileNotReadyError(
            f"rule_profile_id={rule_profile_id!r} is not a known competition profile "
            f"(see COMPETITION_RULE_PROFILE_REGISTRY)",
            case_id=case_id, rule_profile_id=rule_profile_id,
        )

    if not case_id:
        if competition_profile:
            raise RuleProfileNotReadyError(
                f"rule_profile_id={rule_profile_id!r} requires a case_id (a competition profile "
                f"can never resolve against the static Jinshan baseline)",
                case_id=case_id, rule_profile_id=rule_profile_id,
            )
        return RuleEngine(reg_static + ind_static), CaseRuleResolution(
            case_id=case_id, resolution_status="STATIC_LOCAL", trace_by_rule_id=static_trace,
        )

    packages = repository.list_packages(case_id)
    confirmed = [p for p in packages if p.status == CaseRulePackageStatus.CONFIRMED]

    if not confirmed:
        if competition_profile:
            raise RuleProfileNotReadyError(
                f"case_id={case_id!r} rule_profile_id={rule_profile_id!r}: no CONFIRMED "
                f"CaseRulePackage exists yet -- refusing to fall back to the static Jinshan "
                f"commercial rule pack for a competition-profiled case",
                case_id=case_id, rule_profile_id=rule_profile_id,
            )
        warnings = ["CASE_RULE_NOT_CONFIRMED"] if packages else []
        return RuleEngine(reg_static + ind_static), CaseRuleResolution(
            case_id=case_id, resolution_status="STATIC_LOCAL",
            trace_by_rule_id=static_trace, warnings=warnings,
        )

    if len(confirmed) > 1:
        # Should never happen -- CaseRuleRepository.confirm() enforces at
        # most one CONFIRMED package per case_id. Defense-in-depth: never
        # silently pick one if the invariant was ever violated.
        raise CaseRulePackageInvalidError(
            f"case_id={case_id!r} 有 {len(confirmed)} 個 CONFIRMED package，"
            f"違反單一 CONFIRMED 不變量，MANUAL_REVIEW_REQUIRED"
        )
    package = confirmed[0]

    issues = validate_package_rules_scoped(package.regional_rules, package.individual_rules)
    if RuleTableValidator.has_errors(issues):
        raise CaseRulePackageInvalidError(
            f"case_id={case_id!r} package_id={package.package_id!r} 的 CONFIRMED 規則未通過"
            f"結構驗證，MANUAL_REVIEW_REQUIRED（可能於 CONFIRMED 後遭竄改）",
            issues=issues,
        )

    if competition_profile:
        # Task 12: partial fallback (one scope missing) is exactly as
        # unsafe as a complete fallback for a competition profile -- BOTH
        # scopes are required from the SAME confirmed package.
        if not package.regional_rules or not package.individual_rules:
            raise RuleProfileNotReadyError(
                f"case_id={case_id!r} rule_profile_id={rule_profile_id!r}: CONFIRMED package "
                f"{package.package_id!r} is missing regional_rules and/or individual_rules -- "
                f"a competition profile may never partially fall back to the static Jinshan pack "
                f"for the missing scope",
                case_id=case_id, rule_profile_id=rule_profile_id,
            )
        # Task 13: defense in depth -- re-check source identity even though
        # case_rule_repository.confirm() already gates this at CONFIRM time.
        declared = (package.metadata or {}).get("competition_profile") or {}
        mismatch = (
            declared.get("profile_id") != rule_profile_id
            or declared.get("source_document") != competition_profile["source_document"]
            or declared.get("source_sha256") != competition_profile["source_sha256"]
        )
        if mismatch:
            raise RuleProfileNotReadyError(
                f"case_id={case_id!r} rule_profile_id={rule_profile_id!r}: CONFIRMED package "
                f"{package.package_id!r}'s metadata.competition_profile={declared!r} does not "
                f"match the expected source identity {competition_profile!r}",
                case_id=case_id, rule_profile_id=rule_profile_id,
            )

    reg = package.regional_rules if package.regional_rules else reg_static
    ind = package.individual_rules if package.individual_rules else ind_static

    trace = dict(static_trace)
    trace.update(_case_trace(package.regional_rules, case_id, package))
    trace.update(_case_trace(package.individual_rules, case_id, package))

    resolution_status = (
        "CASE_IMPORTED_CONFIRMED" if (package.regional_rules or package.individual_rules) else "STATIC_LOCAL"
    )

    return RuleEngine(reg + ind), CaseRuleResolution(
        case_id=case_id, resolution_status=resolution_status, package_id=package.package_id,
        trace_by_rule_id=trace,
    )
