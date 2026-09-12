# -*- coding: utf-8 -*-
"""
competition_segments.py — COMPETITION-DOMAIN-MULTI-SEGMENT-B1.

Storage abstraction for CompetitionSegmentMap (domain/models.py): the
explicit base_segment + comparables[] mapping for one Competition case
(e.g. shulin_residential_2026's P001-00/P002-00/P003-00/P004-00 contract).

    SK = "SEGMENTS" (one record per case_id, no version history -- the
    map is set once at case creation and is not expected to be edited
    afterward this round; a future round can add an edit path if needed).

A case that never defines a segment map (every existing legacy Jinshan
case) simply has no "SEGMENTS" record at all -- get_segment_map() returns
None, and every OTHER handler in this round (collect_data.py, facility_
confirmation.py) treats an absent/omitted segment_code exactly as before
(Task 16: legacy compatibility, never forced to pass segment_code).

Task 15 (invalid / cross-case segment safety): resolve_segment() looks up
segment_code strictly within THIS case_id's own map (case_store.py's
PK=CASE#<case_no> partitioning already makes a different case's map
physically a different DynamoDB item) -- so both "segment_code not part
of this case" and "segment_code belongs to a different case" collapse to
the exact same, single failure mode: InvalidSegmentCodeError. There is no
string-prefix parsing anywhere in this module (Task 1) -- segment_role
always comes from the explicit CompetitionSegmentMap a caller supplied at
case-creation time.
"""
from __future__ import annotations

from typing import List, Optional

import case_store
from domain.models import CompetitionSegment, CompetitionSegmentMap
from competition_rule_profiles import COMPETITION_RULE_PROFILE_REGISTRY

SEGMENTS_SK = "SEGMENTS"


class InvalidSegmentCodeError(Exception):
    """Raised by resolve_segment() when segment_code is not a member of
    case_id's own CompetitionSegmentMap -- covers BOTH a genuinely unknown
    code (e.g. "P999-00") and a code that belongs to a DIFFERENT case
    (Task 15). Never silently defaults to the base segment or any other
    segment."""


class SegmentCodeRequiredError(Exception):
    """COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1 Task 1. Raised
    when a caller addresses a Competition case's facility-confirmation
    scope (get/confirm/reject) WITHOUT an explicit segment_code -- a
    Competition case (see is_competition_case()) may NEVER fall back to
    the legacy "FACILITY_CONFIRMATION#<subtype>" key, even though that key
    remains fully supported for genuinely legacy (non-Competition) cases."""


def factors_sk(segment_code: Optional[str]) -> str:
    """The FACTORS storage key for one segment. None (the default for
    every legacy, non-segmented caller) -> the ORIGINAL bare "FACTORS" SK,
    byte-identical to pre-B1 behavior. A real segment_code -> its own,
    independent DynamoDB item ("FACTORS#<segment_code>"), so P001-00's
    Table3 factors can never be overwritten by a write to P002-00's (or
    vice versa) -- see docs/phase7/competition_domain_multi_segment_b1.md
    Task 3."""
    return f"FACTORS#{segment_code}" if segment_code else "FACTORS"


def save_segment_map(segment_map: CompetitionSegmentMap) -> None:
    case_store.put_record(segment_map.case_id, SEGMENTS_SK, segment_map.model_dump(mode="json"))


def get_segment_map(case_id: str) -> Optional[CompetitionSegmentMap]:
    data = case_store.get_record(case_id, SEGMENTS_SK)
    if data is None:
        return None
    data = {k: v for k, v in data.items() if k != "_updated_at"}
    return CompetitionSegmentMap.model_validate(data)


def list_segments(case_id: str) -> List[CompetitionSegment]:
    segment_map = get_segment_map(case_id)
    if segment_map is None:
        return []
    return segment_map.all_segments()


def is_competition_case(case_id: str, meta: Optional[dict] = None) -> bool:
    """True when case_id is a Competition case (has its own
    CompetitionSegmentMap, OR its stored meta names a known competition
    rule_profile_id -- e.g. "shulin_residential_2026") -- i.e. a case
    for which the legacy single-segment "FACILITY_CONFIRMATION#<subtype>"
    key would be UNSAFE (Task 1: a Competition case's facility scope must
    always be addressed with an explicit segment_code). False for every
    genuinely legacy Jinshan-style case, which keeps using the legacy key
    exactly as before."""
    if get_segment_map(case_id) is not None:
        return True
    if meta is None:
        meta = case_store.get_case_meta(case_id)
    return bool(meta and meta.get("rule_profile_id") in COMPETITION_RULE_PROFILE_REGISTRY)


def resolve_segment(case_id: str, segment_code: str) -> CompetitionSegment:
    segment_map = get_segment_map(case_id)
    if segment_map is None:
        raise InvalidSegmentCodeError(
            f"case_id={case_id!r} 尚未定義任何 segment map，無法解析 segment_code={segment_code!r}"
        )
    for segment in segment_map.all_segments():
        if segment.segment_code == segment_code:
            return segment
    raise InvalidSegmentCodeError(
        f"segment_code={segment_code!r} 不屬於 case_id={case_id!r} 的 segment map "
        f"（已知 segment_code：{[s.segment_code for s in segment_map.all_segments()]}）"
    )
