# -*- coding: utf-8 -*-
"""
facility_confirmation_repository.py — FACILITY-CONFIRMATION-GATE-1.

Storage abstraction for FacilityConfirmationRecord (domain/models.py),
following the SAME design pattern as case_rule_repository.py's CONFIRMED
Gate (docs precedent, not shared code -- see that module's own docstring
for the original architecture this borrows from):

    - Records live in case_store.py's SAME shared DynamoDB table, under a
      NEW SK family: SK = "FACILITY_CONFIRMATION#<subtype>" (one record
      per case_id+subtype, no version history -- unlike CaseRulePackage,
      a facility candidate has no "package" concept to version).
    - Only confirm()/reject() ever mutate `status` -- no provider,
      collect_data.py, or official_pdf_renderer.py may create a record
      already CONFIRMED (TABLE1-MOST-IMPACTFUL-CROSS-CUTTING-AUDIT's
      Task 8 recommendation, implemented here).
    - `candidate` (the latest system recommendation) and
      `confirmed_selection` (a snapshot taken ONLY at confirm() time) are
      separate fields -- a later candidate refresh never silently alters
      what a human actually confirmed (see Task 6 stale-evidence handling
      below).

CORE FREEZE (FACILITY-CONFIRMATION-GATE-1): this module never imports or
calls RuleEngine/GradeEngine/AdjustmentEngine/CalculationEngine/
FormCompletionEngine/GIS Engine/RoadWidthResolver, and never reads/writes
regional_rules.json/individual_rules.json. It reads ONLY FACTORS.points
and FACTORS.official_facility_evidence -- the SAME already-collected
provider evidence official_pdf_renderer.py's TABLE1-SAFE-WIRING-1 and
TABLE1-MAJOR-STATION-1 code already read, just relocated here so
candidate derivation is a service the PDF renderer no longer performs
itself (see that module's own updated docstring).

SCOPE (this round): utility (substation/gas_tank), funeral (cemetery/
funeral_home/crematorium/columbarium), major_station (MRT/TRA) -- 8
subtypes. Waste (sewage_plant/landfill/incinerator) is deliberately NOT
included (incinerator's power=generator tag is still unsafe per
TABLE1-WASTE-FACILITY-1, and waste was never wired to the official PDF at
all -- see MULTI_CANDIDATE_SELECTION_SUPPORTED note below for a related,
separate limitation that also applies to utility/funeral candidates).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import case_store
from domain.models import FacilityCandidate, FacilityConfirmationRecord, FacilityConfirmationStatus

UTILITY_SUBTYPES = ("substation", "gas_tank")
FUNERAL_SUBTYPES = ("cemetery", "funeral_home", "crematorium", "columbarium")
MAJOR_STATION_SUBTYPES = ("MRT", "TRA")
ALL_SUBTYPES = UTILITY_SUBTYPES + FUNERAL_SUBTYPES + MAJOR_STATION_SUBTYPES

_SK_PREFIX = "FACILITY_CONFIRMATION#"

# Same "無" convention as official_pdf_renderer.py's own _NO_MATCH_VALUES
# (providers/special_facility_provider.py's Mock provider; the ORIGINAL
# 查估書表範本.pdf itself prints "名稱：無" for a confirmed-absent
# facility) -- never a real facility literally named "無".
_NO_MATCH_VALUES = (None, "", "無")


def _sk(subtype: str, segment_code: Optional[str] = None) -> str:
    """COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 6/7: segment_code is
    EXPLICIT and OPTIONAL. None (every legacy Jinshan caller, and every
    caller that has not been updated to pass one -- Task 16) -> the
    ORIGINAL "FACILITY_CONFIRMATION#<subtype>" key, byte-identical to
    pre-B1 behavior; legacy records under this key remain fully readable
    and writable exactly as before. A real segment_code -> its own,
    independent "FACILITY_CONFIRMATION#<segment_code>#<subtype>" item, so
    P001-00/P002-00/P003-00/P004-00's station candidates (etc.) can never
    overwrite each other -- each segment+subtype pair is a distinct
    DynamoDB item, not a shared one keyed only by subtype."""
    return f"{_SK_PREFIX}{segment_code}#{subtype}" if segment_code else f"{_SK_PREFIX}{subtype}"


class FacilityConfirmationNotFoundError(Exception):
    """Raised by confirm()/reject() when no candidate record exists yet
    for case_id+subtype -- the caller must call get_or_refresh_candidates()
    first (mirrors case_rule_repository.CaseRulePackageNotFoundError's
    "never a silent no-op" discipline)."""


class FacilityConfirmationEmptyCandidateError(Exception):
    """Raised by confirm() when the record's current `candidate` is None
    (no facility evidence exists at all for this subtype) -- there is
    nothing to confirm; confirming "nothing" would be indistinguishable
    from a fabricated confirmation."""


# ---------------------------------------------------------------------------
# Candidate derivation (moved from official_pdf_renderer.py's TABLE1-SAFE-
# WIRING-1 / TABLE1-MAJOR-STATION-1 / MAJOR-STATION-NO-DISTANCE-SAFETY-GATE
# code -- same algorithms, unchanged, now living where they are actually
# used: producing PENDING candidates, never producing a PDF).
# ---------------------------------------------------------------------------

def _is_valid_distance_m(v: Any) -> bool:
    """MAJOR-STATION-NO-DISTANCE-SAFETY-GATE: a distance is only usable as
    grounds to select a station candidate when it is a genuine, verified
    positive measurement -- None, a string sentinel like "UNKNOWN", NaN, a
    negative value, and exactly 0 are all rejected."""
    if v is None or isinstance(v, bool) or isinstance(v, str):
        return False
    if not isinstance(v, (int, float, Decimal)):
        return False
    v = float(v)
    if v != v:  # NaN != NaN
        return False
    return v > 0


def _select_station_match(matches: List[Dict[str, Any]], subtype: str) -> Optional[Dict[str, Any]]:
    """Never matches[0], never a stable-sort-order fallback. Filters to
    EXACTLY the requested facility_subtype (government-assigned, never a
    name/distance guess), keeps only matches with a valid positive
    distance_m, returns the smallest of those -- or None whenever NO
    candidate of that subtype has a usable distance, regardless of how
    many candidates exist (including exactly one)."""
    candidates = [m for m in matches if m.get("facility_subtype") == subtype]
    if not candidates:
        return None
    valid = [m for m in candidates if _is_valid_distance_m(m.get("distance_m"))]
    if not valid:
        return None
    return min(valid, key=lambda m: float(m["distance_m"]))


def _derive_utility_funeral_candidate(prefix: str, points_by_field: Dict[str, dict]) -> Optional[dict]:
    """Reads f"{prefix}_name"/f"{prefix}_distance_m" from FACTORS.points by
    EXACT field key -- never derived from each other or any other field.
    The underlying provider (RealFacilityProviderBase.fetch() ->
    find_nearest_facility()) already collapsed to a single nearest result
    before this point (TABLE1-MOST-IMPACTFUL-CROSS-CUTTING-AUDIT's
    CANDIDATE_COLLAPSE_RISK=YES finding) -- selection_basis says so
    honestly, never MOST_IMPACTFUL."""
    name_p = points_by_field.get(f"{prefix}_name")
    if name_p is None or name_p.get("value") in _NO_MATCH_VALUES:
        return None
    dist_p = points_by_field.get(f"{prefix}_distance_m")
    raw_distance = dist_p.get("value") if dist_p is not None else None
    try:
        distance = float(raw_distance) if raw_distance is not None else None
    except (TypeError, ValueError):
        distance = None  # a non-numeric sentinel (e.g. "UNKNOWN") is never surfaced as a distance
    facility_type = "utility" if prefix in UTILITY_SUBTYPES else "funeral"
    return {
        "facility_type": facility_type,
        "facility_subtype": prefix,
        "name": str(name_p["value"]),
        "distance_m": distance,
        "source": name_p.get("source"),
        "source_type": name_p.get("source_type"),
        "source_url": None,
        "dataset_id": None,
        "confidence": name_p.get("confidence"),
        "retrieved_at": str(name_p.get("retrieved_at")) if name_p.get("retrieved_at") else None,
        "provenance_notes": name_p.get("notes"),
        "selection_basis": "NEAREST_PROVIDER_RESULT",
    }


def _derive_major_station_candidate(subtype: str, station_matches: List[dict],
                                     station_evidence: dict) -> Optional[dict]:
    """Reads ONLY official_facility_evidence["STATION"]["matches"] (NTPC
    OpenData via official_facility_provider.py) -- never the generic OSM
    major_station path in FACTORS.points (Source Priority, unchanged from
    TABLE1-MAJOR-STATION-1)."""
    match = _select_station_match(station_matches, subtype)
    if match is None:
        return None
    return {
        "facility_type": "major_station",
        "facility_subtype": subtype,
        "name": match.get("name"),
        "distance_m": float(match["distance_m"]),
        "source": match.get("source_authority") or station_evidence.get("source_authority"),
        "source_type": "GovernmentOpenData",
        "source_url": station_evidence.get("source_url"),
        "dataset_id": match.get("source_dataset_id") or station_evidence.get("dataset_id"),
        "confidence": station_evidence.get("confidence"),
        "retrieved_at": str(station_evidence.get("retrieved_at")) if station_evidence.get("retrieved_at") else None,
        "provenance_notes": None,
        "selection_basis": "NEAREST_VALID_DISTANCE",
    }


def derive_candidates(factors_record: dict) -> Dict[str, Optional[dict]]:
    """Pure function (no I/O beyond reading the already-fetched
    factors_record dict) -- given case_store's own FACTORS record shape,
    returns {subtype: candidate_dict_or_None} for exactly ALL_SUBTYPES.
    Never modifies factors_record."""
    points_by_field = {p["field"]: p for p in (factors_record.get("points") or []) if p.get("field")}
    station_evidence = (factors_record.get("official_facility_evidence") or {}).get("STATION") or {}
    station_matches = station_evidence.get("matches") or []

    out: Dict[str, Optional[dict]] = {}
    for prefix in UTILITY_SUBTYPES + FUNERAL_SUBTYPES:
        out[prefix] = _derive_utility_funeral_candidate(prefix, points_by_field)
    for subtype in MAJOR_STATION_SUBTYPES:
        out[subtype] = _derive_major_station_candidate(subtype, station_matches, station_evidence)
    return out


# ---------------------------------------------------------------------------
# CONFIRMED Gate repository
# ---------------------------------------------------------------------------

def _candidate_differs(fresh: Optional[dict], confirmed: Optional[dict]) -> bool:
    """Task 6 stale-evidence check. Compares only the fields that would
    change what actually renders (name/distance_m) -- provenance metadata
    drifting (e.g. retrieved_at) alone does not count as "the evidence
    changed" for this purpose."""
    if (fresh is None) != (confirmed is None):
        return True
    if fresh is None:
        return False
    if fresh.get("name") != confirmed.get("name"):
        return True
    fresh_d, conf_d = fresh.get("distance_m"), confirmed.get("distance_m")
    if fresh_d is None or conf_d is None:
        return fresh_d != conf_d
    return abs(float(fresh_d) - float(conf_d)) > 1e-6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_storage_metadata(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "_updated_at"}


class DynamoFacilityConfirmationRepository:
    """The ONLY concrete implementation -- DynamoDB-backed via
    case_store.py, same rationale as DynamoCaseRuleRepository (a real
    handler invocation may run in a different warm Lambda container than
    the one that confirmed a candidate, so an in-memory store could never
    satisfy cross-invocation persistence)."""

    def get_record(self, case_id: str, subtype: str, segment_code: Optional[str] = None) -> Optional[FacilityConfirmationRecord]:
        data = case_store.get_record(case_id, _sk(subtype, segment_code))
        if data is None:
            return None
        return FacilityConfirmationRecord.model_validate(_strip_storage_metadata(data))

    def get_or_refresh_candidates(self, case_id: str, factors_record: dict,
                                   segment_code: Optional[str] = None) -> List[FacilityConfirmationRecord]:
        """Refreshes `candidate` for every subtype from the CURRENT
        factors_record. Never touches `status`/`confirmed_selection` --
        those are mutated ONLY by confirm()/reject(). A CONFIRMED record
        whose fresh candidate now differs from its confirmed_selection is
        flagged `stale=True` (Task 6) rather than silently overwritten;
        it keeps rendering its existing confirmed_selection until a human
        re-confirms or rejects it.

        segment_code (Task 8/9): each segment's records are independent
        DynamoDB items (see _sk()), so refreshing one segment's candidates
        can never mark a DIFFERENT segment's CONFIRMED record stale --
        stale-ness is computed purely from THIS record's own candidate vs
        confirmed_selection, never any other segment's."""
        candidates = derive_candidates(factors_record)
        now = _now()
        records: List[FacilityConfirmationRecord] = []
        for subtype, candidate in candidates.items():
            sk = _sk(subtype, segment_code)
            existing = case_store.get_record(case_id, sk)
            if existing is None:
                record = {
                    "case_id": case_id, "subtype": subtype, "segment_code": segment_code,
                    "status": FacilityConfirmationStatus.PENDING.value,
                    "candidate": candidate, "confirmed_selection": None,
                    "reviewer_note": None, "confirmed_by": None, "rejected_by": None,
                    "stale": False, "created_at": now, "updated_at": now,
                }
                case_store.put_record(case_id, sk, record)
            else:
                record = _strip_storage_metadata(dict(existing))
                status = record.get("status")
                changed = record.get("candidate") != candidate
                if status == FacilityConfirmationStatus.CONFIRMED.value:
                    is_stale = _candidate_differs(candidate, record.get("confirmed_selection"))
                    if changed or is_stale != record.get("stale", False):
                        record["candidate"] = candidate
                        record["stale"] = is_stale
                        record["updated_at"] = now
                        case_store.put_record(case_id, sk, record)
                elif changed:
                    record["candidate"] = candidate
                    record["updated_at"] = now
                    case_store.put_record(case_id, sk, record)
            records.append(FacilityConfirmationRecord.model_validate(record))
        return records

    def confirm(self, case_id: str, subtype: str, confirmed_by: str,
                segment_code: Optional[str] = None) -> FacilityConfirmationRecord:
        sk = _sk(subtype, segment_code)
        existing = case_store.get_record(case_id, sk)
        if existing is None:
            raise FacilityConfirmationNotFoundError(
                f"case_id={case_id!r} segment_code={segment_code!r} subtype={subtype!r} 尚無 candidate 記錄，"
                f"請先呼叫 get_or_refresh_candidates()"
            )
        record = _strip_storage_metadata(dict(existing))
        if record.get("candidate") is None:
            raise FacilityConfirmationEmptyCandidateError(
                f"case_id={case_id!r} segment_code={segment_code!r} subtype={subtype!r} 目前無任何 facility evidence，無法 confirm"
            )
        record["status"] = FacilityConfirmationStatus.CONFIRMED.value
        record["confirmed_selection"] = record["candidate"]
        record["confirmed_by"] = confirmed_by
        record["rejected_by"] = None
        record["stale"] = False
        record["updated_at"] = _now()
        case_store.put_record(case_id, sk, record)
        return FacilityConfirmationRecord.model_validate(record)

    def reject(self, case_id: str, subtype: str, reviewer_note: Optional[str] = None,
               rejected_by: Optional[str] = None, segment_code: Optional[str] = None) -> FacilityConfirmationRecord:
        sk = _sk(subtype, segment_code)
        existing = case_store.get_record(case_id, sk)
        if existing is None:
            raise FacilityConfirmationNotFoundError(
                f"case_id={case_id!r} segment_code={segment_code!r} subtype={subtype!r} 尚無 candidate 記錄，"
                f"請先呼叫 get_or_refresh_candidates()"
            )
        record = _strip_storage_metadata(dict(existing))
        record["status"] = FacilityConfirmationStatus.REJECTED.value
        record["rejected_by"] = rejected_by
        if reviewer_note:
            record["reviewer_note"] = reviewer_note
        record["confirmed_selection"] = None
        record["confirmed_by"] = None
        record["stale"] = False
        record["updated_at"] = _now()
        case_store.put_record(case_id, sk, record)
        return FacilityConfirmationRecord.model_validate(record)

    def get_confirmed_selections(self, case_id: str) -> Dict[str, dict]:
        """Reads every subtype's record, returns {subtype: confirmed_
        selection_dict} for every status==CONFIRMED record -- REGARDLESS
        of `stale`. NOT the PDF-facing read path (see get_active_
        confirmed_selections() for that, FACILITY-STALE-CONFIRMATION-
        GATE-1) -- this method still exists for any future caller that
        genuinely wants "every selection a human has ever confirmed,
        including ones a later evidence refresh has since flagged
        stale=True" (e.g. an audit/history view), and is kept unchanged
        rather than silently repurposed so nothing that might already
        depend on its original behavior breaks. pdf_handler.py no longer
        calls this method."""
        out: Dict[str, dict] = {}
        for subtype in ALL_SUBTYPES:
            rec = self.get_record(case_id, subtype)
            if rec is not None and rec.status == FacilityConfirmationStatus.CONFIRMED and rec.confirmed_selection is not None:
                out[subtype] = rec.confirmed_selection.model_dump(mode="json")
        return out

    def get_active_confirmed_selections(self, case_id: str) -> Dict[str, dict]:
        """FACILITY-STALE-CONFIRMATION-GATE-1: the SOLE read path
        official_pdf_renderer.py is meant to be driven from (via
        pdf_handler.py). Returns {subtype: confirmed_selection_dict} for
        ONLY records satisfying ACTIVE_CONFIRMED_SELECTION -- status==
        CONFIRMED AND stale==False AND confirmed_selection is not None.
        A CONFIRMED-but-stale record (new evidence has since diverged
        from what was confirmed -- see get_or_refresh_candidates()) is
        deliberately EXCLUDED here, exactly like PENDING/REJECTED/missing
        -- its confirmed_selection/confirmed_by/history are NEVER
        deleted (see confirm()/get_or_refresh_candidates()), only
        withheld from this one PDF-facing read until a human re-confirms
        (clearing stale) or rejects it."""
        out: Dict[str, dict] = {}
        for subtype in ALL_SUBTYPES:
            rec = self.get_record(case_id, subtype)
            if (rec is not None and rec.status == FacilityConfirmationStatus.CONFIRMED
                    and not rec.stale and rec.confirmed_selection is not None):
                out[subtype] = rec.confirmed_selection.model_dump(mode="json")
        return out


_default_repository: Optional[DynamoFacilityConfirmationRepository] = None


def default_facility_confirmation_repository() -> DynamoFacilityConfirmationRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = DynamoFacilityConfirmationRepository()
    return _default_repository
