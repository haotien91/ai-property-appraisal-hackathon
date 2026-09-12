# -*- coding: utf-8 -*-
"""
COMPETITION-DOMAIN-MULTI-SEGMENT-B1 tests.

Exercises the REAL production handler path -- cases.create_case (with an
explicit `segments` body) -> competition_segments.py's storage -> collect_
data.collect_data (with an explicit segment_code pathParameter) ->
facility_confirmation.py's three handlers (also segment-scoped) -- against
moto-mocked DynamoDB, using the REAL shulin_residential_2026 Table3
fixtures (data/competition_cases/shulin_residential_2026/
segment_table3_fixtures.py), never Jinshan Golden Case values.

Scenarios A-J below map 1:1 to this round's own Task 17 list.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "data", "competition_cases", "shulin_residential_2026"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

import segment_table3_fixtures as fx  # noqa: E402

DISTRICT = fx.DISTRICT
LAND_USE_TYPE = fx.LAND_USE_TYPE


@pytest.fixture(autouse=True)
def _restore_data_provider_mode_for_later_modules(monkeypatch):
    """See tests/test_case_scoped_rule_architecture.py's identical fixture
    for the full incident writeup."""
    yield
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)


def _ensure_mock_collect_data(monkeypatch):
    import collect_data
    import importlib
    monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")
    importlib.reload(collect_data)
    return collect_data


@pytest.fixture()
def ddb_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-b1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName="test-table-b1",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _make_shulin_case(cases_module, case_no, with_segments=True):
    body = {
        "case_no": case_no, "segment_code": "P001-00", "city": "新北市", "district": DISTRICT,
        "land_use_type": LAND_USE_TYPE, "appraisal_period": fx.APPRAISAL_PERIOD,
        "appraisal_base_date": fx.APPRAISAL_PERIOD, "segment_scope": "P001-00 比準地區段",
        "base_parcel_id": fx.P001_SEGMENT_META["parcel_ids"][0], "comparable_ids": ["P002-00", "P003-00", "P004-00"],
    }
    if with_segments:
        body["segments"] = fx.segment_map_body()
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _make_legacy_case(cases_module, case_no):
    body = {
        "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
        "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
        "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
    }
    return cases_module.create_case({"body": json.dumps(body)}, None)


def _collect_data_for_segment(collect_data_module, case_no, segment_code):
    return collect_data_module.collect_data(
        {"pathParameters": {"id": case_no, "segment_code": segment_code},
         "body": json.dumps({"competition_provided_factors": fx.SEGMENT_TABLE3_FACTORS[segment_code]})},
        None,
    )


def _points(*pairs):
    return [{"field": f, "value": v, "unit": u, "source": "TEST_FIXTURE", "source_type": "Mock",
             "coordinate": None, "confidence": "高", "retrieved_at": None, "notes": ""}
            for f, v, u in pairs]


# ---------------------------------------------------------------------------
# A. Create Shulin Competition Case -> 4 segments persist
# ---------------------------------------------------------------------------

class TestSegmentDomainAndPersistence:
    def test_a_create_case_persists_four_segments(self, ddb_env):
        import cases
        import case_store
        case_no = "B1-A-001"
        resp = _make_shulin_case(cases, case_no)
        assert resp["statusCode"] == 201
        created = json.loads(resp["body"])
        codes = {s["segment_code"] for s in ([created["segments"]["base_segment"]] + created["segments"]["comparables"])}
        assert codes == {"P001-00", "P002-00", "P003-00", "P004-00"}

        # reconstruction: re-read (not the in-memory local), via BOTH the
        # segment repository directly and the real get_case handler.
        import competition_segments
        remap = competition_segments.get_segment_map(case_no)
        assert {s.segment_code for s in remap.all_segments()} == {"P001-00", "P002-00", "P003-00", "P004-00"}
        base = remap.base_segment
        assert base.segment_role.value == "BASE_SEGMENT"
        comp1 = next(s for s in remap.comparables if s.comparison_index == 1)
        assert comp1.segment_role.value == "COMPARABLE_SEGMENT_1" and comp1.segment_code == "P002-00"

        get_resp = cases.get_case({"pathParameters": {"id": case_no}}, None)
        assert get_resp["statusCode"] == 200
        reread = json.loads(get_resp["body"])
        assert reread["segments"]["base_segment"]["segment_code"] == "P001-00"

    def test_segment_role_never_inferred_from_string_prefix(self, ddb_env):
        """A segment_code that does NOT look like "P00N-00" at all must
        still resolve correctly purely from the explicit segment_role
        supplied at creation time -- proving role assignment never parses
        the code string itself."""
        import cases
        case_no = "B1-A-002"
        body = {
            "case_no": case_no, "segment_code": "ZZZ-99", "city": "新北市", "district": DISTRICT,
            "land_use_type": LAND_USE_TYPE, "appraisal_period": fx.APPRAISAL_PERIOD,
            "appraisal_base_date": fx.APPRAISAL_PERIOD, "segment_scope": "測試",
            "base_parcel_id": "TBD", "comparable_ids": ["QQQ-01"],
            "segments": {
                "base_segment": {"segment_code": "ZZZ-99", "segment_role": "BASE_SEGMENT",
                                  "district": DISTRICT, "land_use_type": LAND_USE_TYPE},
                "comparables": [{"segment_code": "QQQ-01", "segment_role": "COMPARABLE_SEGMENT_1",
                                  "comparison_index": 1, "district": DISTRICT, "land_use_type": LAND_USE_TYPE}],
            },
        }
        resp = cases.create_case({"body": json.dumps(body)}, None)
        assert resp["statusCode"] == 201

        import competition_segments
        seg = competition_segments.resolve_segment(case_no, "QQQ-01")
        assert seg.segment_role.value == "COMPARABLE_SEGMENT_1"

    def test_mismatched_role_and_comparison_index_rejected(self, ddb_env):
        import cases
        case_no = "B1-A-003"
        body = {
            "case_no": case_no, "segment_code": "P001-00", "city": "新北市", "district": DISTRICT,
            "land_use_type": LAND_USE_TYPE, "appraisal_period": fx.APPRAISAL_PERIOD,
            "appraisal_base_date": fx.APPRAISAL_PERIOD, "segment_scope": "測試",
            "base_parcel_id": "TBD", "comparable_ids": ["P002-00"],
            "segments": {
                "base_segment": {"segment_code": "P001-00", "segment_role": "BASE_SEGMENT",
                                  "district": DISTRICT, "land_use_type": LAND_USE_TYPE},
                "comparables": [{"segment_code": "P002-00", "segment_role": "COMPARABLE_SEGMENT_2",
                                  "comparison_index": 1, "district": DISTRICT, "land_use_type": LAND_USE_TYPE}],
            },
        }
        resp = cases.create_case({"body": json.dumps(body)}, None)
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# B / Task 10 / Task 11. Table3 factor isolation
# ---------------------------------------------------------------------------

class TestTable3FactorIsolation:
    def test_b_four_segments_table3_factors_isolated(self, ddb_env, monkeypatch):
        import cases
        import case_store
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "B1-B-001"
        _make_shulin_case(cases, case_no)

        for code in ("P001-00", "P002-00", "P003-00", "P004-00"):
            resp = _collect_data_for_segment(collect_data, case_no, code)
            assert resp["statusCode"] == 200, resp["body"]

        import competition_segments

        def _road_width(code):
            rec = case_store.get_record(case_no, competition_segments.factors_sk(code))
            factor = next(f for f in rec["competition_provided_factors"] if f["field_id"] == "regional_main_road_width")
            return factor["raw_value"]

        widths = {code: _road_width(code) for code in ("P001-00", "P002-00", "P003-00", "P004-00")}
        assert widths == {"P001-00": 28, "P002-00": 7, "P003-00": 10, "P004-00": 10}
        # SEGMENT_FACTOR_ISOLATION_VERIFIED: reading P002 must never yield P001's 28.
        assert widths["P002-00"] != widths["P001-00"]

        # Each segment's OWN FACTORS item also carries its own segment_code.
        for code in widths:
            rec = case_store.get_record(case_no, competition_segments.factors_sk(code))
            assert rec["segment_code"] == code

    def test_updating_one_segment_never_touches_another(self, ddb_env, monkeypatch):
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "B1-B-002"
        _make_shulin_case(cases, case_no)
        for code in ("P001-00", "P002-00"):
            _collect_data_for_segment(collect_data, case_no, code)

        # Re-submit (UPDATE) P002-00 alone with a deliberately different value.
        collect_data.collect_data(
            {"pathParameters": {"id": case_no, "segment_code": "P002-00"},
             "body": json.dumps({"competition_provided_factors": [
                 {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 999, "unit": "M",
                  "evidence": {"source": "test", "source_type": "競賽題目提供固定值"}},
             ]})},
            None,
        )
        p001 = case_store.get_record(case_no, competition_segments.factors_sk("P001-00"))
        p001_width = next(f for f in p001["competition_provided_factors"] if f["field_id"] == "regional_main_road_width")
        assert p001_width["raw_value"] == 28  # untouched by P002-00's update


# ---------------------------------------------------------------------------
# I. Competition fixed value cannot be overwritten by provider
# ---------------------------------------------------------------------------

class TestCompetitionProvidedFixedImmutability:
    def test_i_fixed_value_survives_alongside_differing_provider_point(self, ddb_env, monkeypatch):
        import cases
        import case_store
        import competition_segments
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "B1-I-001"
        _make_shulin_case(cases, case_no)
        _collect_data_for_segment(collect_data, case_no, "P001-00")

        rec = case_store.get_record(case_no, competition_segments.factors_sk("P001-00"))
        fixed = next(f for f in rec["competition_provided_factors"] if f["field_id"] == "regional_main_road_width")
        assert fixed["raw_value"] == 28
        assert fixed["evidence"]["source_type"] == "競賽題目提供固定值"

        # Mock providers' own `points` (whatever they independently produced
        # for this same call) live in a COMPLETELY SEPARATE key -- never
        # merged into, and never capable of overwriting, competition_
        # provided_factors, regardless of what they contain.
        assert "points" in rec
        # Re-affirm after a second collect_data call for the SAME segment
        # (simulating a later provider re-fetch) -- the fixed value must
        # still read back identically.
        _collect_data_for_segment(collect_data, case_no, "P001-00")
        rec2 = case_store.get_record(case_no, competition_segments.factors_sk("P001-00"))
        fixed2 = next(f for f in rec2["competition_provided_factors"] if f["field_id"] == "regional_main_road_width")
        assert fixed2["raw_value"] == 28


# ---------------------------------------------------------------------------
# C/D/E/F, Task 8/9. Facility candidate segment isolation.
# ---------------------------------------------------------------------------

class TestFacilityCandidateSegmentIsolation:
    def _seed_station_points(self, case_no, segment_code, name, distance_m):
        import case_store
        import competition_segments
        sk = competition_segments.factors_sk(segment_code)
        rec = case_store.get_record(case_no, sk) or {}
        rec["points"] = _points(("substation_name", name, None), ("substation_distance_m", distance_m, "M"))
        case_store.put_record(case_no, sk, rec)

    def test_c_four_segments_same_subtype_candidate_isolation(self, ddb_env, monkeypatch):
        import cases
        import facility_confirmation as fc
        _make_shulin_case(cases, "B1-C-001")
        case_no = "B1-C-001"
        segments_and_names = {
            "P001-00": ("P001變電所", 100), "P002-00": ("P002變電所", 200),
            "P003-00": ("P003變電所", 300), "P004-00": ("P004變電所", 400),
        }
        for code, (name, dist) in segments_and_names.items():
            self._seed_station_points(case_no, code, name, dist)

        results = {}
        for code in segments_and_names:
            resp = fc.get_facility_candidates(
                {"pathParameters": {"id": case_no, "segment_code": code}}, None)
            assert resp["statusCode"] == 200
            body = json.loads(resp["body"])
            substation = next(c for c in body["candidates"] if c["subtype"] == "substation")
            results[code] = substation["candidate"]["name"]

        assert results == {
            "P001-00": "P001變電所", "P002-00": "P002變電所",
            "P003-00": "P003變電所", "P004-00": "P004變電所",
        }
        # SEGMENT_SCOPED_CANDIDATE_ISOLATION: all 4 exist simultaneously,
        # none overwrote another.
        assert len(set(results.values())) == 4

    def test_d_confirm_p001_does_not_confirm_p002(self, ddb_env, monkeypatch):
        import cases
        import facility_confirmation as fc
        case_no = "B1-D-001"
        _make_shulin_case(cases, case_no)
        self._seed_station_points(case_no, "P001-00", "P001變電所", 100)
        self._seed_station_points(case_no, "P002-00", "P002變電所", 200)
        fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P001-00"}}, None)
        fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P002-00"}}, None)

        resp = fc.confirm_facility_candidate(
            {"pathParameters": {"id": case_no, "segment_code": "P001-00", "subtype": "substation"},
             "body": json.dumps({"confirmed_by": "tester"})}, None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["status"] == "CONFIRMED"

        p002 = fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P002-00"}}, None)
        p002_sub = next(c for c in json.loads(p002["body"])["candidates"] if c["subtype"] == "substation")
        assert p002_sub["status"] == "PENDING"  # SEGMENT_SCOPED_CONFIRM_ISOLATION

    def test_e_reject_p003_does_not_modify_others(self, ddb_env, monkeypatch):
        import cases
        import facility_confirmation as fc
        case_no = "B1-E-001"
        _make_shulin_case(cases, case_no)
        for code, (name, dist) in {
            "P001-00": ("P001變電所", 100), "P002-00": ("P002變電所", 200),
            "P003-00": ("P003變電所", 300), "P004-00": ("P004變電所", 400),
        }.items():
            self._seed_station_points(case_no, code, name, dist)
            fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": code}}, None)
            fc.confirm_facility_candidate(
                {"pathParameters": {"id": case_no, "segment_code": code, "subtype": "substation"},
                 "body": json.dumps({"confirmed_by": "tester"})}, None)

        reject_resp = fc.reject_facility_candidate(
            {"pathParameters": {"id": case_no, "segment_code": "P003-00", "subtype": "substation"},
             "body": json.dumps({"rejected_by": "tester"})}, None)
        assert json.loads(reject_resp["body"])["status"] == "REJECTED"

        for code in ("P001-00", "P002-00", "P004-00"):
            resp = fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": code}}, None)
            sub = next(c for c in json.loads(resp["body"])["candidates"] if c["subtype"] == "substation")
            assert sub["status"] == "CONFIRMED"  # SEGMENT_SCOPED_REJECT_ISOLATION

    def test_f_refresh_p004_does_not_stale_p001(self, ddb_env, monkeypatch):
        """Task 9: refreshing ONE segment's candidates (new evidence)
        must only ever be able to flag THAT segment's own CONFIRMED record
        stale -- never a different segment's."""
        import cases
        import facility_confirmation as fc
        case_no = "B1-F-001"
        _make_shulin_case(cases, case_no)
        self._seed_station_points(case_no, "P001-00", "P001變電所", 100)
        self._seed_station_points(case_no, "P004-00", "P004變電所A", 400)
        fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P001-00"}}, None)
        fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P004-00"}}, None)
        fc.confirm_facility_candidate(
            {"pathParameters": {"id": case_no, "segment_code": "P001-00", "subtype": "substation"},
             "body": json.dumps({"confirmed_by": "tester"})}, None)
        fc.confirm_facility_candidate(
            {"pathParameters": {"id": case_no, "segment_code": "P004-00", "subtype": "substation"},
             "body": json.dumps({"confirmed_by": "tester"})}, None)

        # New evidence arrives for P004-00 only.
        self._seed_station_points(case_no, "P004-00", "P004變電所B", 999)
        fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P004-00"}}, None)

        p004 = fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P004-00"}}, None)
        p004_sub = next(c for c in json.loads(p004["body"])["candidates"] if c["subtype"] == "substation")
        assert p004_sub["stale"] is True

        p001 = fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P001-00"}}, None)
        p001_sub = next(c for c in json.loads(p001["body"])["candidates"] if c["subtype"] == "substation")
        assert p001_sub["stale"] is False  # SEGMENT_SCOPED_STALE_ISOLATION


# ---------------------------------------------------------------------------
# G/H, Task 15. Invalid / cross-case segment safety.
# ---------------------------------------------------------------------------

class TestInvalidAndCrossCaseSegmentSafety:
    def test_g_invalid_segment_code_blocks_collect_data(self, ddb_env, monkeypatch):
        import cases
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "B1-G-001"
        _make_shulin_case(cases, case_no)
        resp = collect_data.collect_data(
            {"pathParameters": {"id": case_no, "segment_code": "P999-00"}, "body": "{}"}, None)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "INVALID_SEGMENT_CODE"

    def test_g_invalid_segment_code_blocks_facility_candidates(self, ddb_env):
        import cases
        import facility_confirmation as fc
        case_no = "B1-G-002"
        _make_shulin_case(cases, case_no)
        resp = fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P999-00"}}, None)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "INVALID_SEGMENT_CODE"

    def test_g_invalid_segment_code_blocks_get_segment(self, ddb_env):
        import cases
        import segments as segments_handler
        case_no = "B1-G-003"
        _make_shulin_case(cases, case_no)
        resp = segments_handler.get_segment(
            {"pathParameters": {"id": case_no, "segment_code": "P999-00"}}, None)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"]["code"] == "INVALID_SEGMENT_CODE"

    def test_h_cross_case_segment_leakage_blocks(self, ddb_env):
        """A segment_code that IS valid for case X must never resolve for
        a DIFFERENT case Y that never defined it."""
        import cases
        import competition_segments
        from competition_segments import InvalidSegmentCodeError
        case_x, case_y = "B1-H-X", "B1-H-Y"
        _make_shulin_case(cases, case_x)
        _make_legacy_case(cases, case_y)  # never defines any segment map

        seg = competition_segments.resolve_segment(case_x, "P002-00")
        assert seg.segment_code == "P002-00"
        with pytest.raises(InvalidSegmentCodeError):
            competition_segments.resolve_segment(case_y, "P002-00")


# ---------------------------------------------------------------------------
# J, Task 16. Legacy compatibility.
# ---------------------------------------------------------------------------

class TestLegacyCompatibility:
    def test_j_legacy_case_collect_data_and_facility_confirmation_unaffected(self, ddb_env, monkeypatch):
        import cases
        import case_store
        import facility_confirmation as fc
        collect_data = _ensure_mock_collect_data(monkeypatch)
        case_no = "B1-J-001"
        _make_legacy_case(cases, case_no)

        resp = collect_data.collect_data(
            {"pathParameters": {"id": case_no}, "body": json.dumps({
                "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
                "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
            })}, None,
        )
        assert resp["statusCode"] == 200
        stored = case_store.get_record(case_no, "FACTORS")  # bare legacy SK, unchanged
        assert stored is not None
        assert stored["segment_code"] is None

        rec = case_store.get_record(case_no, "FACILITY_CONFIRMATION#substation")
        assert rec is None  # nothing yet -- but the LEGACY key is what would be used
        resp2 = fc.get_facility_candidates({"pathParameters": {"id": case_no}}, None)
        assert resp2["statusCode"] == 200
        legacy_rec = case_store.get_record(case_no, "FACILITY_CONFIRMATION#substation")
        assert legacy_rec is not None  # legacy (no "#segment#") key used, exactly as pre-B1

    def test_legacy_and_segmented_records_never_collide(self, ddb_env, monkeypatch):
        """Superseded by COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1
        Task 1: a Competition case (like this Shulin one) may no longer
        address facility-confirmation scope via the legacy no-segment_code
        path AT ALL -- see tests/test_competition_domain_multi_segment_b1_
        final_gate.py's TestSegmentCodeRequiredForCompetition for the full
        A/B/C coverage of that stricter contract. This test now only
        checks that the segment-scoped path itself still works and
        produces its own, independent record -- the legacy-vs-segmented
        SK-disjointness proof for a genuinely NON-Competition case is
        covered by test_j_legacy_case_collect_data_and_facility_
        confirmation_unaffected above."""
        import cases
        import case_store
        import facility_confirmation as fc
        case_no = "B1-J-002"
        _make_shulin_case(cases, case_no)
        case_store.put_record(case_no, "FACTORS#P001-00", {"points": []})

        # Legacy-style call (no segment_code) on a Competition case is now
        # BLOCKED outright (Task 1) -- never silently falls back.
        legacy_resp = fc.get_facility_candidates({"pathParameters": {"id": case_no}}, None)
        assert legacy_resp["statusCode"] == 400
        assert json.loads(legacy_resp["body"])["error"]["code"] == "SEGMENT_CODE_REQUIRED"

        seg_resp = fc.get_facility_candidates({"pathParameters": {"id": case_no, "segment_code": "P001-00"}}, None)
        assert seg_resp["statusCode"] == 200

        import facility_confirmation_repository as fcr
        seg_rec = case_store.get_record(case_no, fcr._sk("columbarium", "P001-00"))
        assert seg_rec is not None
        assert seg_rec["status"] == "PENDING"
        assert seg_rec["confirmed_by"] is None
