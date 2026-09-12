# -*- coding: utf-8 -*-
"""
FACILITY-CONFIRMATION-GATE-1 tests.

Two layers:
  1. Repository-level (backend/handlers/facility_confirmation_repository.py)
     -- pure candidate-derivation logic (moved from official_pdf_renderer.py
     -- see that module's own updated docstring) plus the CONFIRMED Gate
     itself, backed by moto-mocked DynamoDB (same pattern as
     tests/test_evaluation_standard_human_confirmation.py).
  2. Handler-level (backend/handlers/facility_confirmation.py) -- the 3
     Lambda-style handlers, exercised directly (no API Gateway involved),
     covering Task 9's full A-J anti-fabrication/idempotency matrix.
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, REPO_ROOT)

try:
    from moto import mock_aws
    import boto3
except ImportError:
    pytest.skip("moto/boto3 not available", allow_module_level=True)

AWS_REGION = "ap-northeast-1"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("CASES_TABLE_NAME", "test-table-facility")
    monkeypatch.setenv("AWS_DEFAULT_REGION", AWS_REGION)
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=AWS_REGION)
        ddb.create_table(
            TableName="test-table-facility",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                   {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from _aws_mock_reset import reset_cached_aws_module_state
        reset_cached_aws_module_state()
        yield


def _points(*pairs):
    """pairs: (field, value, unit). Builds a FACTORS.points-shaped list."""
    return [{"field": f, "value": v, "unit": u, "source": "TEST_FIXTURE", "source_type": "Mock",
             "coordinate": None, "confidence": "高", "retrieved_at": None, "notes": ""}
            for f, v, u in pairs]


def _factors_record(points=None, station_matches=None):
    return {
        "points": points or [],
        "official_facility_evidence": {"STATION": {"matches": station_matches or [],
                                                     "dataset_id": "TEST-DATASET",
                                                     "source_authority": "TEST-AUTHORITY",
                                                     "source_url": "https://example.invalid/test",
                                                     "confidence": "中", "retrieved_at": "2026-01-01T00:00:00"}},
    }


def _station_match(subtype, name, distance_m=None, source_dataset_id="TEST-DATASET",
                    source_authority="TEST-AUTHORITY"):
    return {"facility_subtype": subtype, "name": name, "distance_m": distance_m,
            "source_dataset_id": source_dataset_id, "source_authority": source_authority}


# ---------------------------------------------------------------------------
# 1. Candidate derivation (moved selection-algorithm tests -- Task 4/6)
# ---------------------------------------------------------------------------

class TestCandidateDerivation:
    def test_utility_candidate_derived_from_points(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(points=_points(
            ("substation_name", "測試變電所", None), ("substation_distance_m", 700, "M"),
        ))
        candidates = derive_candidates(factors)
        assert candidates["substation"]["name"] == "測試變電所"
        assert candidates["substation"]["distance_m"] == 700.0
        assert candidates["substation"]["selection_basis"] == "NEAREST_PROVIDER_RESULT"
        assert candidates["substation"]["facility_type"] == "utility"
        assert candidates["gas_tank"] is None

    def test_funeral_candidate_derived_from_points(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(points=_points(("cemetery_name", "測試公墓", None), ("cemetery_distance_m", 80, "M")))
        candidates = derive_candidates(factors)
        assert candidates["cemetery"]["name"] == "測試公墓"
        assert candidates["cemetery"]["facility_type"] == "funeral"
        for other in ("funeral_home", "crematorium", "columbarium"):
            assert candidates[other] is None

    def test_no_match_marker_treated_as_absent(self):
        """"無" is this codebase's own established convention for
        confirmed-absent, never a real facility name (see providers/
        special_facility_provider.py's Mock provider)."""
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(points=_points(("funeral_home_name", "無", None)))
        assert derive_candidates(factors)["funeral_home"] is None

    def test_task8_a_real_sewage_plant_not_in_scope_this_round(self):
        """FACILITY-CONFIRMATION-GATE-1's scope is utility/funeral/
        major_station only -- waste subtypes (sewage_plant/landfill/
        incinerator) must never appear in ALL_SUBTYPES this round."""
        from facility_confirmation_repository import ALL_SUBTYPES
        assert "sewage_plant" not in ALL_SUBTYPES
        assert "landfill" not in ALL_SUBTYPES
        assert "incinerator" not in ALL_SUBTYPES
        assert set(ALL_SUBTYPES) == {"substation", "gas_tank", "cemetery", "funeral_home",
                                      "crematorium", "columbarium", "MRT", "TRA"}

    def test_task6_e_multiple_mrt_nearest_valid_distance_selected(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[
            _station_match("MRT", "測試1200公尺捷運站", 1200.0),
            _station_match("MRT", "測試800公尺捷運站", 800.0),
            _station_match("MRT", "測試300公尺捷運站", 300.0),
        ])
        candidate = derive_candidates(factors)["MRT"]
        assert candidate["name"] == "測試300公尺捷運站"
        assert candidate["distance_m"] == 300.0
        assert candidate["selection_basis"] == "NEAREST_VALID_DISTANCE"

    def test_task6_f_multiple_mrt_all_distance_none_none_selected(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[
            _station_match("MRT", "測試甲站", None), _station_match("MRT", "測試乙站", None),
        ])
        assert derive_candidates(factors)["MRT"] is None

    def test_task6_g_single_mrt_without_distance_still_not_selected(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[_station_match("MRT", "測試唯一站", None)])
        assert derive_candidates(factors)["MRT"] is None

    @pytest.mark.parametrize("bad_distance", ["UNKNOWN", -1, 0, math.nan])
    def test_task6_h_i_invalid_distance_variants_not_selected(self, bad_distance):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[_station_match("MRT", "測試站", bad_distance)])
        assert derive_candidates(factors)["MRT"] is None

    def test_task6_j_name_reads_like_mrt_but_distance_missing_stays_none(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[_station_match("MRT", "某某捷運站", None)])
        assert derive_candidates(factors)["MRT"] is None

    def test_subtype_never_derived_from_name_text(self):
        """A match literally named "台北高鐵站" but with facility_subtype
        None must never populate MRT/TRA -- selection is purely by the
        source's own facility_subtype field."""
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[
            {"facility_subtype": None, "name": "台北高鐵站", "distance_m": 10.0}
        ])
        candidates = derive_candidates(factors)
        assert candidates["MRT"] is None
        assert candidates["TRA"] is None

    def test_major_station_provenance_marked_government_open_data(self):
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(station_matches=[_station_match("MRT", "測試站", 500.0)])
        candidate = derive_candidates(factors)["MRT"]
        assert candidate["source_type"] == "GovernmentOpenData"
        assert candidate["dataset_id"] == "TEST-DATASET"
        assert candidate["source"] == "TEST-AUTHORITY"

    def test_utility_provenance_marked_mock_when_source_is_mock(self):
        """Task 8/J: source_type is copied verbatim from the underlying
        NormalizedDataPoint -- never upgraded to look more authoritative
        than it is."""
        from facility_confirmation_repository import derive_candidates
        factors = _factors_record(points=_points(("substation_name", "測試變電所", None)))
        candidate = derive_candidates(factors)["substation"]
        assert candidate["source_type"] == "Mock"


# ---------------------------------------------------------------------------
# 2. Repository CONFIRMED Gate
# ---------------------------------------------------------------------------

class TestConfirmedGate:
    def test_get_or_refresh_creates_pending_records(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository, ALL_SUBTYPES
        repo = DynamoFacilityConfirmationRepository()
        factors = _factors_record(points=_points(("substation_name", "測試變電所", None)))
        records = repo.get_or_refresh_candidates("CASE-1", factors)
        assert {r.subtype for r in records} == set(ALL_SUBTYPES)
        for r in records:
            assert r.status.value == "PENDING"
            assert r.confirmed_selection is None
        substation = next(r for r in records if r.subtype == "substation")
        assert substation.candidate.name == "測試變電所"

    def test_confirm_requires_existing_record(self, env):
        from facility_confirmation_repository import (
            DynamoFacilityConfirmationRepository, FacilityConfirmationNotFoundError,
        )
        repo = DynamoFacilityConfirmationRepository()
        with pytest.raises(FacilityConfirmationNotFoundError):
            repo.confirm("CASE-NEVER-SEEDED", "substation", "reviewer1")

    def test_confirm_empty_candidate_refused(self, env):
        from facility_confirmation_repository import (
            DynamoFacilityConfirmationRepository, FacilityConfirmationEmptyCandidateError,
        )
        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("CASE-2", _factors_record())  # no evidence at all
        with pytest.raises(FacilityConfirmationEmptyCandidateError):
            repo.confirm("CASE-2", "substation", "reviewer1")

    def test_confirm_transitions_pending_to_confirmed_and_snapshots_selection(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        factors = _factors_record(points=_points(("substation_name", "測試變電所", None), ("substation_distance_m", 700, "M")))
        repo.get_or_refresh_candidates("CASE-3", factors)
        record = repo.confirm("CASE-3", "substation", "reviewer1")
        assert record.status.value == "CONFIRMED"
        assert record.confirmed_selection.name == "測試變電所"
        assert record.confirmed_by == "reviewer1"

        selections = repo.get_confirmed_selections("CASE-3")
        assert selections["substation"]["name"] == "測試變電所"
        assert "gas_tank" not in selections  # never confirmed, absent entirely

    def test_reject_transitions_to_rejected_and_clears_confirmed_selection(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        factors = _factors_record(points=_points(("cemetery_name", "測試公墓", None)))
        repo.get_or_refresh_candidates("CASE-4", factors)
        repo.confirm("CASE-4", "cemetery", "reviewer1")
        record = repo.reject("CASE-4", "cemetery", reviewer_note="不採用", rejected_by="reviewer2")
        assert record.status.value == "REJECTED"
        assert record.confirmed_selection is None
        assert repo.get_confirmed_selections("CASE-4") == {}

    def test_provider_or_collect_data_can_never_create_a_confirmed_record(self, env):
        """Task 2/5: get_or_refresh_candidates() (the ONLY thing
        collect_data.py-adjacent code ever calls) must always create new
        records as PENDING, never CONFIRMED."""
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        factors = _factors_record(points=_points(("substation_name", "測試變電所", None)))
        records = repo.get_or_refresh_candidates("CASE-5", factors)
        assert all(r.status.value == "PENDING" for r in records)

    def test_task6_stale_evidence_flagged_not_silently_overwritten(self, env):
        """New collect_data evidence differing from an already-CONFIRMED
        selection must NOT silently overwrite confirmed_selection -- only
        flag stale=True; the PDF still renders the OLD confirmed value
        until a human re-confirms or rejects."""
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        factors_v1 = _factors_record(points=_points(("substation_name", "測試變電所A", None), ("substation_distance_m", 700, "M")))
        repo.get_or_refresh_candidates("CASE-6", factors_v1)
        repo.confirm("CASE-6", "substation", "reviewer1")

        factors_v2 = _factors_record(points=_points(("substation_name", "測試變電所B", None), ("substation_distance_m", 900, "M")))
        records = repo.get_or_refresh_candidates("CASE-6", factors_v2)
        substation = next(r for r in records if r.subtype == "substation")
        assert substation.status.value == "CONFIRMED"  # untouched
        assert substation.confirmed_selection.name == "測試變電所A"  # untouched
        assert substation.stale is True  # flagged
        assert substation.candidate.name == "測試變電所B"  # latest recommendation, refreshed

        # The PDF-facing read path is unaffected by staleness -- still
        # returns the OLD confirmed selection until a human acts.
        selections = repo.get_confirmed_selections("CASE-6")
        assert selections["substation"]["name"] == "測試變電所A"

    def test_reconfirming_a_stale_record_clears_the_stale_flag(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        factors_v1 = _factors_record(points=_points(("cemetery_name", "測試公墓A", None)))
        repo.get_or_refresh_candidates("CASE-7", factors_v1)
        repo.confirm("CASE-7", "cemetery", "reviewer1")
        factors_v2 = _factors_record(points=_points(("cemetery_name", "測試公墓B", None)))
        repo.get_or_refresh_candidates("CASE-7", factors_v2)
        record = repo.confirm("CASE-7", "cemetery", "reviewer2")  # re-confirm with the NEW candidate
        assert record.stale is False
        assert record.confirmed_selection.name == "測試公墓B"

    def test_task12_multi_candidate_selection_not_supported(self, env):
        """Explicit, honest limitation record (Task 12): utility/funeral
        candidates are always the provider's own already-collapsed
        nearest result -- this repository has no mechanism to offer a
        second/third candidate for a reviewer to pick among."""
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        factors = _factors_record(points=_points(("substation_name", "測試變電所", None)))
        records = repo.get_or_refresh_candidates("CASE-8", factors)
        substation = next(r for r in records if r.subtype == "substation")
        # Exactly one candidate slot exists per subtype -- no list/array
        # of alternatives is ever offered.
        assert substation.candidate is not None
        assert not hasattr(substation, "candidates")  # singular field name, by design


MULTI_CANDIDATE_SELECTION_SUPPORTED = False  # see test_task12_multi_candidate_selection_not_supported


# ---------------------------------------------------------------------------
# 3. Handler-level (backend/handlers/facility_confirmation.py)
# ---------------------------------------------------------------------------

class TestFacilityConfirmationHandlers:
    def test_get_returns_404_when_no_factors_record(self, env):
        import facility_confirmation
        resp = facility_confirmation.get_facility_candidates({"pathParameters": {"id": "NEVER-COLLECTED"}}, None)
        assert resp["statusCode"] == 404

    def test_get_creates_and_returns_all_8_pending_candidates(self, env):
        import case_store
        import facility_confirmation
        case_store.put_record("CASE-H1", "FACTORS", _factors_record(
            points=_points(("substation_name", "測試變電所", None)),
            station_matches=[_station_match("MRT", "測試站", 500.0)],
        ))
        resp = facility_confirmation.get_facility_candidates({"pathParameters": {"id": "CASE-H1"}}, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert len(body["candidates"]) == 8
        by_subtype = {c["subtype"]: c for c in body["candidates"]}
        assert by_subtype["substation"]["status"] == "PENDING"
        assert by_subtype["substation"]["candidate"]["name"] == "測試變電所"
        assert by_subtype["gas_tank"]["candidate"] is None

    def test_confirm_then_reject_full_cycle(self, env):
        import case_store
        import facility_confirmation
        case_store.put_record("CASE-H2", "FACTORS", _factors_record(
            points=_points(("cemetery_name", "測試公墓", None)),
        ))
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": "CASE-H2"}}, None)

        confirm_resp = facility_confirmation.confirm_facility_candidate(
            {"pathParameters": {"id": "CASE-H2", "subtype": "cemetery"}, "body": json.dumps({"confirmed_by": "王小明"})}, None)
        assert confirm_resp["statusCode"] == 200
        assert json.loads(confirm_resp["body"])["status"] == "CONFIRMED"

        reject_resp = facility_confirmation.reject_facility_candidate(
            {"pathParameters": {"id": "CASE-H2", "subtype": "cemetery"}, "body": json.dumps({"rejected_by": "李小華"})}, None)
        assert reject_resp["statusCode"] == 200
        assert json.loads(reject_resp["body"])["status"] == "REJECTED"

    def test_confirm_unknown_subtype_returns_404(self, env):
        import case_store
        import facility_confirmation
        case_store.put_record("CASE-H3", "FACTORS", _factors_record())
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": "CASE-H3"}}, None)
        resp = facility_confirmation.confirm_facility_candidate(
            {"pathParameters": {"id": "CASE-H3", "subtype": "not_a_real_subtype"},
             "body": json.dumps({"confirmed_by": "x"})}, None)
        assert resp["statusCode"] == 404

    def test_task9_i_repeated_get_does_not_silently_convert_confirmed(self, env):
        """A new collect_data-equivalent FACTORS write followed by another
        get_facility_candidates() call must NOT silently move a CONFIRMED
        subtype to a fresh, different candidate/status -- see the stale
        mechanism (TestConfirmedGate.test_task6_stale_evidence_flagged_
        not_silently_overwritten) exercised here through the handler."""
        import case_store
        import facility_confirmation
        case_store.put_record("CASE-H4", "FACTORS", _factors_record(
            points=_points(("substation_name", "測試變電所A", None)),
        ))
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": "CASE-H4"}}, None)
        facility_confirmation.confirm_facility_candidate(
            {"pathParameters": {"id": "CASE-H4", "subtype": "substation"},
             "body": json.dumps({"confirmed_by": "王小明"})}, None)

        case_store.put_record("CASE-H4", "FACTORS", _factors_record(
            points=_points(("substation_name", "測試變電所B", None)),
        ))
        resp = facility_confirmation.get_facility_candidates({"pathParameters": {"id": "CASE-H4"}}, None)
        body = json.loads(resp["body"])
        substation = next(c for c in body["candidates"] if c["subtype"] == "substation")
        assert substation["status"] == "CONFIRMED"
        assert substation["confirmed_selection"]["name"] == "測試變電所A"
        assert substation["stale"] is True

    def test_task9_j_mock_confirmation_never_becomes_government_source(self, env):
        """CONFIRMED means "a reviewer accepted this candidate", never
        "this is government-official data" -- source_type stays exactly
        what the underlying evidence said (Mock here), and no field in
        the DTO ever claims OFFICIAL_GOVERNMENT_CONFIRMED."""
        import case_store
        import facility_confirmation
        case_store.put_record("CASE-H5", "FACTORS", _factors_record(
            points=_points(("cemetery_name", "測試公墓", None)),
        ))
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": "CASE-H5"}}, None)
        resp = facility_confirmation.confirm_facility_candidate(
            {"pathParameters": {"id": "CASE-H5", "subtype": "cemetery"},
             "body": json.dumps({"confirmed_by": "王小明"})}, None)
        body = json.loads(resp["body"])
        assert body["confirmed_selection"]["source_type"] == "Mock"
        assert "OFFICIAL_GOVERNMENT_CONFIRMED" not in json.dumps(body)


# ---------------------------------------------------------------------------
# 4. FACILITY-STALE-CONFIRMATION-GATE-1: ACTIVE_CONFIRMED_SELECTION
# ---------------------------------------------------------------------------

class TestActiveConfirmedSelectionGate:
    """Task 7 A-J. status==CONFIRMED alone is no longer sufficient to
    reach the official PDF -- stale==False is also required. Covers
    utility, funeral, and major_station (Task 7's explicit "至少覆蓋" list)."""

    def _confirm_then_go_stale(self, repo, case_id, subtype, points_v1, points_v2, station_matches_v1=None,
                                station_matches_v2=None):
        repo.get_or_refresh_candidates(case_id, _factors_record(points=points_v1, station_matches=station_matches_v1))
        repo.confirm(case_id, subtype, "reviewer1")
        return repo.get_or_refresh_candidates(
            case_id, _factors_record(points=points_v2, station_matches=station_matches_v2))

    def test_a_confirmed_not_stale_included_in_active_selections(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("A1", _factors_record(points=_points(("substation_name", "測試變電所", None))))
        repo.confirm("A1", "substation", "reviewer1")
        active = repo.get_active_confirmed_selections("A1")
        assert active["substation"]["name"] == "測試變電所"

    def test_b_confirmed_stale_excluded_from_active_selections(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        self._confirm_then_go_stale(
            repo, "B1", "substation",
            _points(("substation_name", "測試變電所A", None)),
            _points(("substation_name", "測試變電所B", None)),
        )
        active = repo.get_active_confirmed_selections("B1")
        assert "substation" not in active

    def test_c_stale_preserves_historical_confirmed_selection(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        records = self._confirm_then_go_stale(
            repo, "C1", "cemetery",
            _points(("cemetery_name", "測試公墓A", None)),
            _points(("cemetery_name", "測試公墓B", None)),
        )
        cemetery = next(r for r in records if r.subtype == "cemetery")
        assert cemetery.status.value == "CONFIRMED"
        assert cemetery.stale is True
        assert cemetery.confirmed_selection.name == "測試公墓A"  # NOT deleted

    def test_d_stale_preserves_confirmed_by(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("D1", _factors_record(points=_points(("funeral_home_name", "測試殯儀館A", None))))
        repo.confirm("D1", "funeral_home", "王小明")
        records = repo.get_or_refresh_candidates(
            "D1", _factors_record(points=_points(("funeral_home_name", "測試殯儀館B", None))))
        funeral_home = next(r for r in records if r.subtype == "funeral_home")
        assert funeral_home.confirmed_by == "王小明"  # NOT cleared just because it went stale

    def test_e_reconfirm_latest_candidate_clears_stale_and_becomes_active(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        self._confirm_then_go_stale(
            repo, "E1", "substation",
            _points(("substation_name", "測試變電所A", None)),
            _points(("substation_name", "測試變電所B", None)),
        )
        record = repo.confirm("E1", "substation", "reviewer2")  # reconfirm with the latest (stale) candidate
        assert record.stale is False
        assert record.confirmed_selection.name == "測試變電所B"
        active = repo.get_active_confirmed_selections("E1")
        assert active["substation"]["name"] == "測試變電所B"

    def test_f_pending_excluded_from_active_selections(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("F1", _factors_record(points=_points(("substation_name", "測試變電所", None))))
        # never confirmed
        assert repo.get_active_confirmed_selections("F1") == {}

    def test_g_rejected_excluded_from_active_selections(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("G1", _factors_record(points=_points(("substation_name", "測試變電所", None))))
        repo.confirm("G1", "substation", "reviewer1")
        repo.reject("G1", "substation", rejected_by="reviewer2")
        assert repo.get_active_confirmed_selections("G1") == {}

    def test_h_raw_evidence_without_any_confirmation_excluded(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("H1", _factors_record(points=_points(("cemetery_name", "測試公墓", None))))
        # candidate exists (PENDING) but was never confirmed at all
        assert repo.get_active_confirmed_selections("H1") == {}

    def test_i_stale_record_never_falls_back_to_raw_candidate(self, env):
        """A stale record's own `candidate` field DOES hold the fresh
        evidence (for a reviewer to see and act on) -- but
        get_active_confirmed_selections() must never substitute it in
        place of the withheld confirmed_selection."""
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        records = self._confirm_then_go_stale(
            repo, "I1", "substation",
            _points(("substation_name", "測試變電所A", None)),
            _points(("substation_name", "測試變電所B", None)),
        )
        substation = next(r for r in records if r.subtype == "substation")
        assert substation.candidate.name == "測試變電所B"  # fresh evidence IS visible on the record
        active = repo.get_active_confirmed_selections("I1")
        assert "substation" not in active  # but never surfaces via the PDF-facing read

    def test_j_mrt_stale_confirmation_excluded_from_active_selections(self, env):
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        repo = DynamoFacilityConfirmationRepository()
        self._confirm_then_go_stale(
            repo, "J1", "MRT", None, None,
            station_matches_v1=[_station_match("MRT", "測試捷運站A", 500.0)],
            station_matches_v2=[_station_match("MRT", "測試捷運站B", 300.0)],
        )
        active = repo.get_active_confirmed_selections("J1")
        assert "MRT" not in active

    def test_requires_reconfirmation_dto_field(self, env):
        import case_store
        import facility_confirmation
        case_store.put_record("K1", "FACTORS", _factors_record(points=_points(("cemetery_name", "測試公墓A", None))))
        facility_confirmation.get_facility_candidates({"pathParameters": {"id": "K1"}}, None)
        facility_confirmation.confirm_facility_candidate(
            {"pathParameters": {"id": "K1", "subtype": "cemetery"}, "body": json.dumps({"confirmed_by": "x"})}, None)
        case_store.put_record("K1", "FACTORS", _factors_record(points=_points(("cemetery_name", "測試公墓B", None))))
        resp = facility_confirmation.get_facility_candidates({"pathParameters": {"id": "K1"}}, None)
        cemetery = next(c for c in json.loads(resp["body"])["candidates"] if c["subtype"] == "cemetery")
        assert cemetery["requires_reconfirmation"] is True
        assert cemetery["status"] == "CONFIRMED"  # never silently downgraded to REJECTED


class TestStaleConfirmationEndToEndWithRenderer:
    """Task 8: full repository -> renderer path -- a stale CONFIRMED
    selection must produce a BLANK row on the actual official PDF, not
    just an empty dict in isolation."""

    @pytest.fixture(scope="class")
    def golden_fields(self):
        engine_dir = os.path.join(REPO_ROOT, "engine")
        golden_dir = os.path.join(REPO_ROOT, "data", "golden")
        pdf_dir = os.path.join(REPO_ROOT, "pdf")
        for d in (engine_dir, golden_dir, pdf_dir, REPO_ROOT):
            if d not in sys.path:
                sys.path.insert(0, d)
        pytest.importorskip("fitz", reason="PyMuPDF not available")
        from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL
        from rule_engine import RuleEngine
        from grade_engine import GradeEngine
        from adjustment_engine import AdjustmentEngine
        from calculation_engine import CalculationEngine
        from form_completion_engine import FormCompletionEngine
        reg = json.load(open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8"))["rules"]
        ind = json.load(open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8"))["rules"]
        rule_engine = RuleEngine(reg + ind)
        fce = FormCompletionEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine())
        result = fce.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)
        return GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, json.loads(result.model_dump_json())["fields"]

    def _render(self, golden_fields, active_selections):
        from official_pdf_renderer import render_official_pdf
        case, base, comp, fields = golden_fields
        return render_official_pdf(case, base, comp, fields, confirmed_facility_selections=active_selections)

    def _text_in_bbox(self, page, bbox, substring, tolerance=3.0):
        import fitz
        rect = fitz.Rect(*bbox) + (-tolerance, -tolerance, tolerance, tolerance)
        found = "".join(w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(rect))
        return substring in found

    def test_substation_stale_renders_blank_current_renders(self, env, golden_fields):
        import fitz
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        from official_pdf_renderer import load_profile

        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("REND-1", _factors_record(
            points=_points(("substation_name", "測試變電所A", None), ("substation_distance_m", 700, "M"))))
        repo.confirm("REND-1", "substation", "reviewer1")

        # -- current (not stale) render: name appears --
        active_current = repo.get_active_confirmed_selections("REND-1")
        pdf_current = self._render(golden_fields, active_current)
        profile = load_profile()
        doc = fitz.open(stream=pdf_current, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        assert self._text_in_bbox(page, profile["table1"]["fields"]["substation"]["name_bbox"], "測試變電所A")
        doc.close()

        # -- new evidence arrives, confirmation goes stale --
        repo.get_or_refresh_candidates("REND-1", _factors_record(
            points=_points(("substation_name", "測試變電所B", None), ("substation_distance_m", 900, "M"))))
        active_stale = repo.get_active_confirmed_selections("REND-1")
        assert "substation" not in active_stale
        pdf_stale = self._render(golden_fields, active_stale)
        doc = fitz.open(stream=pdf_stale, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        name_text = "".join(w[4] for w in page.get_text("words")
                             if fitz.Rect(w[:4]).intersects(fitz.Rect(*profile["table1"]["fields"]["substation"]["name_bbox"])))
        assert name_text.strip() == ""  # blank, neither the old nor the new name
        doc.close()

    def test_mrt_stale_renders_blank_current_renders(self, env, golden_fields):
        import fitz
        from facility_confirmation_repository import DynamoFacilityConfirmationRepository
        from official_pdf_renderer import load_profile

        repo = DynamoFacilityConfirmationRepository()
        repo.get_or_refresh_candidates("REND-2", _factors_record(
            station_matches=[_station_match("MRT", "測試捷運站A", 850.0)]))
        repo.confirm("REND-2", "MRT", "reviewer1")

        active_current = repo.get_active_confirmed_selections("REND-2")
        pdf_current = self._render(golden_fields, active_current)
        profile = load_profile()
        doc = fitz.open(stream=pdf_current, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        assert self._text_in_bbox(page, profile["table1"]["fields"]["major_station_mrt"]["name_bbox"], "測試捷運站A")
        doc.close()

        repo.get_or_refresh_candidates("REND-2", _factors_record(
            station_matches=[_station_match("MRT", "測試捷運站C", 300.0)]))
        active_stale = repo.get_active_confirmed_selections("REND-2")
        assert "MRT" not in active_stale
        pdf_stale = self._render(golden_fields, active_stale)
        doc = fitz.open(stream=pdf_stale, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        mrt_spec = profile["table1"]["fields"]["major_station_mrt"]
        default_text = "".join(w[4] for w in page.get_text("words")
                                if fitz.Rect(w[:4]).intersects(fitz.Rect(*mrt_spec["name_bbox"])))
        assert "無捷運站" in default_text  # back to template default, not either candidate name
        circle_text = "".join(w[4] for w in page.get_text("words")
                               if fitz.Rect(w[:4]).intersects(fitz.Rect(*mrt_spec["circle_bbox"])))
        assert "●" not in circle_text
        doc.close()
