# -*- coding: utf-8 -*-
"""
SHULIN-COMPETITION-RULE-PACK-A2 regression tests.

Three independent concerns, kept in one file per this round's own scope:

1. TestRulePackValidation (Task 17) -- the generated Shulin regional/
   individual rule packs pass RuleTableValidator with zero ERRORs, and the
   legacy Jinshan static packs are UNCHANGED by this round's engine/
   rule_table_validator.py generalization (same file, same 0-error result
   as before range_segments existed).

2. TestNonMonotonicLandDepth (Task 5/6/7/9) -- runtime-verifies the exact
   12 (value -> expected grade) pairs Task 9 specifies for land_depth's
   non-monotonic range_segments, via the REAL RuleEngine, and confirms an
   ordinary single-range Jinshan factor is completely unaffected by the
   same engine code path.

3. TestCompetitionFailClosedResolution (Task 18, scenarios A-H) -- exercises
   rule_engine_factory.build_rule_engine_for_case() directly (this round
   does NOT touch analyze.py/complete_form.py/review.py -- Table3/Domain
   migration is explicitly out of scope) against moto-mocked DynamoDB, the
   same pattern tests/test_case_scoped_rule_architecture.py already
   established.
"""
from __future__ import annotations

import copy
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

from rule_engine import RuleEngine  # noqa: E402
from rule_table_validator import RuleTableValidator  # noqa: E402
from domain.models import CaseRulePackage  # noqa: E402

SHULIN_DIR = os.path.join(REPO_ROOT, "data", "rules", "competition", "shulin_residential_2026")
PROFILE_ID = "shulin_residential_2026"
EXPECTED_IDENTITY = {
    "district": "樹林區", "land_use_type": "普通住宅用地",
    "source_document": "評價基準明細表.pdf",
    "source_sha256": "a7574aaf56b546737df8b3b34459e764523be77ae4af25490d617a41a33ed09c",
}


def _load(name):
    with open(os.path.join(SHULIN_DIR, name), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. Rule pack validation (Task 17)
# ---------------------------------------------------------------------------

class TestRulePackValidation:
    def test_shulin_regional_pack_has_29_factors_zero_errors(self):
        rules = _load("regional_rules.json")["rules"]
        factor_ids = {r["rule_id"].rsplit("-", 1)[0] for r in rules}
        assert len(factor_ids) == 29
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], f"unexpected ERRORs in Shulin regional pack: {errors}"

    def test_shulin_individual_pack_has_19_standard_factors_zero_errors(self):
        rules = _load("individual_rules.json")["rules"]
        factor_ids = {r["rule_id"].rsplit("-", 1)[0] for r in rules}
        assert len(factor_ids) == 19
        assert not any("FLOOR_AREA_RATIO" in fid for fid in factor_ids), (
            "floor_area_ratio_individual must NOT have a fabricated rule record (Task 4/16)"
        )
        issues = RuleTableValidator().validate(rules)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], f"unexpected ERRORs in Shulin individual pack: {errors}"

    def test_manifest_records_far_manual_review_policy(self):
        manifest = _load("manifest.json")
        far_policy = next(
            p for p in manifest["special_policies"] if p["factor"] == "floor_area_ratio_individual"
        )
        assert far_policy["calculation_policy"] == "MANUAL_REVIEW_REQUIRED"
        assert far_policy["reason"] == "LAND_DEVELOPMENT_ANALYSIS_REQUIRED"
        assert manifest["source_sha256"] == EXPECTED_IDENTITY["source_sha256"]

    def test_other_factors_7grade_present_but_not_auto_graded_this_case(self):
        manifest = _load("manifest.json")
        assert manifest["other_factors_7grade"]["included_in_pack"] is True
        assert manifest["other_factors_7grade"]["grade_count"] == 7
        assert manifest["other_factors_7grade"]["competition_case_auto_graded"] is False

    def test_legacy_jinshan_packs_unaffected_by_generalized_validator(self):
        """The engine/rule_table_validator.py generalization (grade_code-
        order -> value-order sort, plus the new _check_range_segments) must
        not introduce a single new ERROR for the pre-existing Jinshan
        static rule packs -- every one of their records has no
        range_segments at all, so this is a pure no-op path for them."""
        with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
            reg = json.load(f)["rules"]
        with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
            ind = json.load(f)["rules"]
        issues = RuleTableValidator().validate(reg) + RuleTableValidator().validate(ind)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], f"legacy Jinshan pack regressed: {errors}"


# ---------------------------------------------------------------------------
# 2. Non-monotonic land_depth (Task 5/6/7/9)
# ---------------------------------------------------------------------------

class TestNonMonotonicLandDepth:
    NON_MONOTONIC_CASES = [
        (6.9, "劣"), (7, "普通"), (13.999, "普通"), (14, "優"), (29.999, "優"),
        (30, "稍優"), (39.999, "稍優"), (40, "普通"), (49.999, "普通"),
        (50, "稍劣"), (59.999, "稍劣"), (60, "劣"),
    ]

    @pytest.mark.parametrize("value,expected_grade", NON_MONOTONIC_CASES)
    def test_land_depth_non_monotonic_grading(self, value, expected_grade):
        rules = _load("individual_rules.json")["rules"]
        engine = RuleEngine(rules)
        result = engine.grade("新北市", "樹林區", "普通住宅用地", "深度", value, unit="M", rule_set="individual")
        assert result.grade == expected_grade, f"depth={value} graded {result.grade}, expected {expected_grade}"

    def test_land_depth_rule_uses_range_segments_for_grade_3_and_5_only(self):
        rules = _load("individual_rules.json")["rules"]
        depth_rules = {r["grade_code"]: r for r in rules if r["rule_id"].startswith("IND-LAND_DEPTH-")}
        assert depth_rules[1].get("range_segments") is None
        assert depth_rules[2].get("range_segments") is None
        assert len(depth_rules[3]["range_segments"]) == 2
        assert depth_rules[4].get("range_segments") is None
        assert len(depth_rules[5]["range_segments"]) == 2

    def test_floor_area_ratio_individual_raises_never_silently_graded(self):
        """Task 16: since no rule record exists for 容積率/individual at
        all (Task 4/6 -- deliberately absent, source cells are blank), the
        REAL RuleEngine must raise RuleNotFoundError -- never fall back to
        the regional 容積率 matrix, never default to a grade/adjustment of
        0, never invent one. A caller (a future FormCompletionEngine
        wiring, out of this round's scope) is responsible for turning this
        exception into MANUAL_REVIEW_REQUIRED -- this test only guards the
        rule-pack-level guarantee that no answer is fabricated here."""
        from rule_engine import RuleNotFoundError

        rules = _load("individual_rules.json")["rules"]
        engine = RuleEngine(rules)
        with pytest.raises(RuleNotFoundError):
            engine.grade("新北市", "樹林區", "普通住宅用地", "容積率", 250, unit="%", rule_set="individual")

    def test_ordinary_single_range_jinshan_factor_unaffected(self):
        """Same RuleEngine._match_numeric() code path, exercised against a
        plain single-range Jinshan factor with no range_segments at all --
        must behave exactly as before this round's change."""
        with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
            reg = json.load(f)["rules"]
        engine = RuleEngine(reg)
        result = engine.grade("新北市", "金山區", "商業用地", "建蔽率", 75, unit="%", rule_set="regional")
        assert result.grade_code == 1  # Jinshan's own 建蔽率 優=60%以上 (unrelated to Shulin's 80%)


# ---------------------------------------------------------------------------
# 3. Fail-closed competition profile resolution (Task 18, A-H)
# ---------------------------------------------------------------------------

@pytest.fixture()
def ddb_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _shulin_regional_rules():
    return _load("regional_rules.json")["rules"]


def _shulin_individual_rules():
    return _load("individual_rules.json")["rules"]


def _competition_metadata(sha_override=None):
    return {
        "competition_profile": {
            "profile_id": PROFILE_ID,
            "district": EXPECTED_IDENTITY["district"],
            "land_use_type": EXPECTED_IDENTITY["land_use_type"],
            "source_document": EXPECTED_IDENTITY["source_document"],
            "source_sha256": sha_override or EXPECTED_IDENTITY["source_sha256"],
        }
    }


def _draft_package(case_id, package_id, regional_rules=None, individual_rules=None, metadata=None):
    return CaseRulePackage(
        case_id=case_id, package_id=package_id, rule_version="shulin-test-v1",
        source_document="評價基準明細表.pdf", source_type="MANUAL_CSV_INGEST",
        regional_rules=regional_rules or [], individual_rules=individual_rules or [],
        metadata=metadata or {},
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )


class TestCompetitionFailClosedResolution:
    def test_a_legacy_jinshan_case_no_package_static_local(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        engine, resolution = build_rule_engine_for_case("JINSHAN-A2-A", repo)  # no rule_profile_id
        assert resolution.resolution_status == "STATIC_LOCAL"
        assert isinstance(engine, RuleEngine)

    def test_b_shulin_case_no_package_blocks(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case("SHULIN-A2-B", repo, rule_profile_id=PROFILE_ID)

    def test_c_shulin_case_pending_package_blocks(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        case_no = "SHULIN-A2-C"
        pkg = _draft_package(case_no, "PKG-C", regional_rules=_shulin_regional_rules(),
                              individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
        repo.save_candidate(pkg)  # never confirmed -- stays DRAFT
        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case(case_no, repo, rule_profile_id=PROFILE_ID)

    def test_d_shulin_case_confirmed_complete_package_resolves(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        case_no = "SHULIN-A2-D"
        pkg = _draft_package(case_no, "PKG-D", regional_rules=_shulin_regional_rules(),
                              individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-D", confirmed_by="tester")

        engine, resolution = build_rule_engine_for_case(case_no, repo, rule_profile_id=PROFILE_ID)
        assert resolution.resolution_status == "CASE_IMPORTED_CONFIRMED"
        assert resolution.package_id == "PKG-D"
        result = engine.grade("新北市", "樹林區", "普通住宅用地", "建蔽率", 85, unit="%", rule_set="regional")
        assert result.grade == "優"  # Shulin's own 建蔽率 優=80%以上 -- proves Shulin rules, not Jinshan, are in effect

    def test_e_shulin_case_confirmed_regional_only_blocks(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        case_no = "SHULIN-A2-E"
        pkg = _draft_package(case_no, "PKG-E", regional_rules=_shulin_regional_rules(),
                              individual_rules=[], metadata=_competition_metadata())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-E", confirmed_by="tester")
        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case(case_no, repo, rule_profile_id=PROFILE_ID)

    def test_f_shulin_case_confirmed_individual_only_blocks(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        case_no = "SHULIN-A2-F"
        pkg = _draft_package(case_no, "PKG-F", regional_rules=[],
                              individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
        repo.save_candidate(pkg)
        repo.confirm(case_no, "PKG-F", confirmed_by="tester")
        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case(case_no, repo, rule_profile_id=PROFILE_ID)

    def test_g_source_sha_mismatch_cannot_even_be_confirmed(self, ddb_env):
        """Task 13: source identity is checked at CONFIRM time itself
        (case_rule_repository.py), not merely at resolution time -- a
        mismatched package can never reach CONFIRMED in the first place."""
        from case_rule_repository import DynamoCaseRuleRepository, CaseRulePackageInvalidError

        repo = DynamoCaseRuleRepository()
        case_no = "SHULIN-A2-G"
        pkg = _draft_package(
            case_no, "PKG-G", regional_rules=_shulin_regional_rules(),
            individual_rules=_shulin_individual_rules(),
            metadata=_competition_metadata(sha_override="0" * 64),
        )
        repo.save_candidate(pkg)
        with pytest.raises(CaseRulePackageInvalidError):
            repo.confirm(case_no, "PKG-G", confirmed_by="tester")

    def test_g2_resolution_time_defense_in_depth_for_mismatched_identity(self, ddb_env, monkeypatch):
        """Even if a mismatched-identity package somehow became CONFIRMED
        (e.g. state drift, or a future bug bypassing confirm()'s own
        gate), build_rule_engine_for_case() must independently refuse it
        rather than trusting CONFIRMED status alone (Task 13's own
        "defense in depth" framing)."""
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        case_no = "SHULIN-A2-G2"
        pkg = _draft_package(
            case_no, "PKG-G2", regional_rules=_shulin_regional_rules(),
            individual_rules=_shulin_individual_rules(),
            metadata=_competition_metadata(sha_override="0" * 64),
        )
        repo.save_candidate(pkg)
        # Bypass confirm()'s own CONFIRM-time identity gate by writing the
        # CONFIRMED status directly (simulating state drift / a hypothetical
        # future bug in confirm() itself) rather than calling repo.confirm().
        import case_store
        stored = case_store.get_record(case_no, "CASE_RULE_PACKAGE#PKG-G2")
        stored["status"] = "CONFIRMED"
        case_store.put_record(case_no, "CASE_RULE_PACKAGE#PKG-G2", stored)
        case_store.put_record(case_no, "CASE_RULE_CONFIRMED_POINTER", {"package_id": "PKG-G2"})

        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case(case_no, repo, rule_profile_id=PROFILE_ID)

    def test_h_no_cross_case_leak(self, ddb_env):
        """A CONFIRMED Shulin package for case X must never be visible to
        a different case Y that has no package of its own -- Y still
        correctly blocks with RULE_PROFILE_NOT_READY, never silently
        inheriting X's rules."""
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        case_x, case_y = "SHULIN-A2-H-X", "SHULIN-A2-H-Y"
        pkg = _draft_package(case_x, "PKG-H-X", regional_rules=_shulin_regional_rules(),
                              individual_rules=_shulin_individual_rules(), metadata=_competition_metadata())
        repo.save_candidate(pkg)
        repo.confirm(case_x, "PKG-H-X", confirmed_by="tester")

        engine_x, resolution_x = build_rule_engine_for_case(case_x, repo, rule_profile_id=PROFILE_ID)
        assert resolution_x.resolution_status == "CASE_IMPORTED_CONFIRMED"

        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case(case_y, repo, rule_profile_id=PROFILE_ID)

    def test_unknown_profile_id_is_a_hard_error_not_a_static_fallback(self, ddb_env):
        from rule_engine_factory import build_rule_engine_for_case, RuleProfileNotReadyError
        from case_rule_repository import DynamoCaseRuleRepository

        repo = DynamoCaseRuleRepository()
        with pytest.raises(RuleProfileNotReadyError):
            build_rule_engine_for_case("ANY-CASE", repo, rule_profile_id="not_a_real_profile")
