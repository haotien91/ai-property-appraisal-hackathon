# -*- coding: utf-8 -*-
"""
case_reconstruction.py — shared helper for building a CompetitionCase +
regional factor lists from stored DynamoDB records. Used by both
complete_form.py and review.py so the reconstruction logic exists in
exactly one place (per "Prefer existing implementation. Do not duplicate
modules").
"""
from __future__ import annotations

from decimal import Decimal

from domain.models import CompetitionCase, FactorInput, Evidence, SourceType, RoadWidthEvidence

_DEFAULT_EVIDENCE = Evidence(source="使用者輸入/Data Acquisition Layer", source_type=SourceType.AI_ASSISTED_FILL)

# Evidence.source_type is a closed enum (schemas/field_dictionary.json's
# source_type taxonomy) with no member for "government open data API" or
# "mock fixture" -- NormalizedDataPoint.source_type is a free string
# ('GovernmentOpenData'/'Mock'/...) with no such constraint. Mapped here
# (not by adding new enum members) per "use existing convention, don't
# build a parallel schema": GIS_MEASUREMENT is the closest existing member
# for a spatial/government-registry lookup (e.g. NtpcZoningProvider's
# point-in-polygon query); anything else falls back to the same
# AI_ASSISTED_FILL default every other Data-Acquisition-Layer value uses.
_PROVIDER_SOURCE_TYPE_MAP = {
    "GovernmentOpenData": SourceType.GIS_MEASUREMENT,
}


def _dynamodb_round_tripped_to_str(value):
    """A number round-tripped through case_store.py's DynamoDB item comes
    back as Decimal (boto3's Table resource deserializes the Number type
    that way); if it then passes through a Union[float, int, ...] Pydantic
    field with float listed first (e.g. FactorInput.raw_value), it becomes
    a plain float by the time application code sees it. Stringified so the
    value ends up JSON/DynamoDB-safe downstream (a raw float fails
    REVIEW_RESULT's own later put_record with boto3's "Float types are not
    supported" error) and matches this codebase's convention of string-
    typed Decimal-derived numeric fields (whole numbers rendered without a
    trailing ".0")."""
    if isinstance(value, (Decimal, float)):
        d = Decimal(str(value))
        return str(int(d)) if d == d.to_integral_value() else str(d)
    return value


def _evidence_from_dict(ev_raw: dict) -> Evidence:
    # TABLE51-THREE-COMPARABLE-C1-FINAL-GATE-1 Task 2 fix: a caller that
    # already stores a genuine SourceType enum VALUE (e.g. collect_data.py's
    # competition_provided_factors, tagged "競賽題目提供固定值" ==
    # SourceType.COMPETITION_PROVIDED_FIXED.value) must pass through as-is
    # -- never silently reclassified as AI_ASSISTED_FILL just because it
    # isn't a Provider-taxonomy string. _PROVIDER_SOURCE_TYPE_MAP (below)
    # remains the fallback ONLY for genuine free-form Provider-taxonomy
    # strings (e.g. "GovernmentOpenData"/"Mock") that are NOT already a
    # valid SourceType value.
    raw_source_type = ev_raw.get("source_type")
    try:
        source_type = SourceType(raw_source_type)
    except ValueError:
        source_type = _PROVIDER_SOURCE_TYPE_MAP.get(raw_source_type, SourceType.AI_ASSISTED_FILL)
    return Evidence(
        source=ev_raw.get("source") or _DEFAULT_EVIDENCE.source, source_type=source_type,
        confidence=ev_raw.get("confidence"), retrieved_at=ev_raw.get("retrieved_at"),
        notes=ev_raw.get("notes"),
    )


def to_factor_inputs(raw_list):
    """`r["evidence"]`, when present, carries a Data Provider's own
    NormalizedDataPoint provenance (source/source_type/confidence/
    retrieved_at/notes -- see collect_data.py's _to_regional_factor())
    through to this FactorInput, instead of every entry silently getting
    the same generic "user input" Evidence regardless of where it actually
    came from. Absent for ordinary request-body-submitted factors
    (unchanged behavior, fully backward compatible)."""
    result = []
    for r in raw_list:
        ev_raw = r.get("evidence")
        ev = _evidence_from_dict(ev_raw) if ev_raw else _DEFAULT_EVIDENCE
        result.append(FactorInput(field_id=r["field_id"], factor=r["factor"], raw_value=r["raw_value"],
                                   unit=r.get("unit"), evidence=ev))
    return result


def _apply_competition_provided_precedence(
    base_list: list, factors_record: dict, fixed_key: str = "competition_provided_factors",
) -> list:
    """COMPETITION-DOMAIN-MULTI-SEGMENT-B1-FINAL-GATE-1 Task 6 (generalized
    in TABLE4-THREE-COMPARABLE-D1 to also cover individual factors via
    `fixed_key="competition_provided_individual_factors"` -- same merge
    logic, no second implementation): a COMPETITION_PROVIDED_FIXED value
    (see collect_data.py) ALWAYS wins over a Provider-derived `base_list`
    entry for the SAME field_id -- the Provider's differing value is
    dropped from what actually feeds the Rule/Grade Engine here (it
    remains separately readable from FACTORS.regional_base_factors/points
    for audit, just never used for grading), never merged/averaged/
    silently preferred. A field_id the fixed bucket does NOT mention is
    left completely untouched (whatever `base_list` already had for it,
    unchanged). Note: `user_submitted_factors` (any "AI/user supplemental"
    value a caller might separately put there) is NEVER read by this
    function at all -- for regional factors it has no path into grading in
    the first place; for individual factors it is the `base_list` itself,
    which fixed values still take precedence over."""
    fixed = factors_record.get(fixed_key) or []
    if not fixed:
        return base_list
    fixed_field_ids = {f["field_id"] for f in fixed}
    merged = [f for f in base_list if f.get("field_id") not in fixed_field_ids]
    merged.extend(fixed)
    return merged


def apply_competition_provided_individual_precedence(base_list: list, factors_record: dict) -> list:
    """TABLE4-THREE-COMPARABLE-D1 Task 2/10: the individual-factor
    counterpart of _apply_competition_provided_precedence(), reading
    factors_record["competition_provided_individual_factors"] (see
    collect_data.py) instead of the regional bucket. `base_list` is
    normally `user_submitted_factors.base_parcel_factors` or one
    comparable_id's list from `user_submitted_factors.comparable_factors`."""
    return _apply_competition_provided_precedence(
        base_list, factors_record, fixed_key="competition_provided_individual_factors",
    )


def build_case_and_regional_factors(case_no: str, meta: dict, factors_record: dict):
    """Returns (CompetitionCase, regional_base_factors, regional_comparable_factors)
    from stored case metadata + FACTORS record. Raises the same exceptions a
    caller would need to catch as VALIDATION_ERROR."""
    user_factors = factors_record.get("user_submitted_factors", {})
    base_list = user_factors.get("base_parcel_factors", []) if isinstance(user_factors, dict) else []
    comp_map = user_factors.get("comparable_factors", {}) if isinstance(user_factors, dict) else {}
    regional_base = _apply_competition_provided_precedence(
        factors_record.get("regional_base_factors", []), factors_record,
    )
    regional_comp = factors_record.get("regional_comparable_factors", {})

    case = CompetitionCase(
        case_no=case_no, appraisal_period=meta.get("appraisal_period", ""),
        appraisal_base_date=meta.get("appraisal_base_date", ""),
        segment_code=meta["segment_code"], segment_scope=meta.get("segment_scope", ""),
        city=meta.get("city", "新北市"), district=meta["district"],
        land_use_type=meta.get("land_use_type", "商業用地"),
        base_parcel_id=meta.get("base_parcel_id", "TBD"),
        base_parcel_factors=to_factor_inputs(base_list),
        comparable_ids=list(comp_map.keys()),
        comparable_factors={cid: to_factor_inputs(v) for cid, v in comp_map.items()},
        comparable_land_normal_price={cid: Decimal(str(v)) for cid, v in
                                       factors_record.get("land_normal_price", {}).items()},
        comparable_price_date_adjustment_rate={cid: Decimal(str(v)) for cid, v in
                                                factors_record.get("price_date_rate", {}).items()},
        comparable_weight={cid: Decimal(str(v)) for cid, v in
                            factors_record.get("weight", {}).items()},
    )
    return (case, to_factor_inputs(regional_base),
            {cid: to_factor_inputs(v) for cid, v in regional_comp.items()})


def extract_submitted_land_use_ratio_fields(case: CompetitionCase) -> dict:
    """Reads 表1's 使用分區/建蔽率/容積率 out of case.base_parcel_factors --
    these are genuinely user-submitted (POSTed in collect_data's request
    body as ordinary individual FactorInput entries, same field_id
    convention as every other individual factor -- see docs/phase2/
    form_mapping.md and data/golden/golden_case_input.py), not provider-
    resolved, so comparing them against LandUseRatioEngine's independently
    resolved reference (see AuditEngine._official_regional_raw_value) is a
    genuine two-sided check, not a tautological A==A comparison. Returns
    None for any field not present in this case's submission -- never
    guessed or defaulted to a Golden Case value.

    See _dynamodb_round_tripped_to_str's docstring for why raw_value is
    stringified rather than passed through as whatever numeric type it
    happens to be by the time it reaches case.base_parcel_factors here."""
    by_field = {f.field_id: f.raw_value for f in case.base_parcel_factors}
    return {
        "land_use_zone": _dynamodb_round_tripped_to_str(by_field.get("individual_zoning_designation")),
        "building_coverage_rate": _dynamodb_round_tripped_to_str(
            by_field.get("individual_building_coverage_ratio")),
        "floor_area_ratio": _dynamodb_round_tripped_to_str(by_field.get("individual_floor_area_ratio")),
    }


def extract_plan_identification(factors_record: dict) -> dict:
    """Reads the plan-identification metadata collect_data.py persists
    (internal_plan_id/confirmed_plan_name/plan_identification_source) --
    plan_id is ALWAYS human-supplied (see providers/base.py's
    ProviderContext docstring: automatic coordinate->都市計畫 resolution
    was investigated and found infeasible), never inferred here. A case
    whose FACTORS record predates this field (or whose collect-data call
    never supplied plan_id) returns all-None, which AuditEngine correctly
    surfaces as ZONING_PLAN_UNRESOLVED -- never guessed."""
    pid = factors_record.get("plan_identification") or {}
    return {
        "internal_plan_id": pid.get("internal_plan_id"),
        "confirmed_plan_name": pid.get("confirmed_plan_name"),
        "plan_identification_source": pid.get("plan_identification_source"),
    }


def extract_submitted_main_road_width(factors_record: dict):
    """Reads 表1's submitted main_road_width -- collect_data.py persists
    this from its own top-level request-body key (main_road_width has no
    "individual_"-factor-list counterpart in this data model; 主要道路寬度
    is graded as a REGIONAL factor, not compared per-comparable), never
    from RealRoadProvider's output. Returns None (not a guess) when this
    case's collect-data call never supplied it."""
    submission = factors_record.get("road_width_submission") or {}
    return _dynamodb_round_tripped_to_str(submission.get("submitted_main_road_width"))


def extract_road_width_evidence(factors_record: dict) -> "list[RoadWidthEvidence]":
    """Reads collect_data.py's persisted road_width_evidence -- every
    RoadWidthEvidence entry RealRoadProvider considered (see
    collect_data._resolve_road_width_evidence), reconstructed as real
    RoadWidthEvidence objects (not raw dicts) so AuditEngine.review()'s
    road_width_evidence parameter receives exactly the type
    RoadWidthResolver expects. Each entry was written via
    model_dump(mode="json") -- Decimal/datetime fields already arrive as
    strings, which RoadWidthEvidence parses back natively, so (unlike
    FactorInput.raw_value) no DynamoDB-float-coercion workaround is needed
    here. A case whose FACTORS record predates this field (or ran in mock
    mode, which has no evidence concept at all) returns [] -- AuditEngine
    correctly surfaces that as ROAD_WIDTH_UNAVAILABLE, never a guess."""
    raw = factors_record.get("road_width_evidence") or []
    return [RoadWidthEvidence(**e) for e in raw]
