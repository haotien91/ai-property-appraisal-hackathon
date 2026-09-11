# -*- coding: utf-8 -*-
"""
Data Provider interfaces (Data Acquisition Layer), per master project
instructions section 8.

Design principle: providers are Adapters. Today only Mock implementations
exist (grounded in the Golden Case, per Phase 5 instruction "不得自行捏造
資料" -- Mock values must trace to an actual official source, not be
invented). A future real implementation (government Open Data API, GIS
service, etc.) can be swapped in without touching any caller, because every
provider implements the same `fetch()` contract and returns the same
`NormalizedDataPoint` shape.

No provider ever returns a guessed value. If data is unavailable, it
returns a NormalizedDataPoint with value=None, confidence='UNKNOWN', and an
explanatory note -- callers (FormCompletionEngine et al.) are responsible
for surfacing this as MANUAL_REVIEW_REQUIRED, never for filling gaps
themselves.
"""
from __future__ import annotations

import abc
import sys
import os
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint, Coordinate, TargetCoordinateEvidence  # noqa: E402


class ProviderContext:
    """Minimal context passed to every provider: which case/segment/parcel
    it should fetch data for. Deliberately does NOT include city/district
    hardcoded defaults -- always supplied by the caller (Phase 1 REQ-006/007:
    system must not be built for a single segment/case).

    `plan_id` (internal identifier into data/rules/plan_zone_floor_area_
    ratios.json, e.g. "jinshan") is ALWAYS human-supplied, NEVER auto-derived
    from center_coordinate or district -- automatic coordinate ->都市計畫
    resolution was investigated (NTPC's own "新北市都市計畫範圍" open-data
    shapefile) and found infeasible: that dataset's Name/LblName attribute
    field is corrupted at the source (raw DBF bytes contain literal U+FFFD
    replacement-character sequences, verified byte-for-byte during this
    session -- not a wrong `encoding=` guess on this codebase's part), and
    the dataset's Url field is empty for every record, so there is no
    fallback lookup either. Until NTPC publishes a usable version of that
    dataset (or another authoritative plan-name source is found),
    `plan_id` can only come from whoever already knows which 都市計畫 a
    case's segment falls in -- e.g. an estimator entering it by hand."""

    def __init__(self, case_no: str, city: str, district: str,
                 segment_code: str, parcel_id: Optional[str] = None,
                 center_coordinate: Optional[Coordinate] = None,
                 plan_id: Optional[str] = None,
                 center_coordinate_evidence: Optional[TargetCoordinateEvidence] = None):
        self.case_no = case_no
        self.city = city
        self.district = district
        self.segment_code = segment_code
        self.parcel_id = parcel_id
        self.center_coordinate = center_coordinate
        self.plan_id = plan_id
        # Phase API-2.1 Coordinate Provenance Contract (see domain.models.
        # TargetCoordinateEvidence's docstring): WHERE center_coordinate
        # came from, so a consumer (OfficialFacilityProvider) can tell an
        # official coordinate apart from a Nominatim/demo/unverified one
        # instead of treating "center_coordinate is not None" as proof of
        # official provenance. Additive/optional -- every existing caller
        # that constructs a ProviderContext without this argument is
        # unaffected; a None here is treated as UNKNOWN provenance by
        # consumers, never silently upgraded to OFFICIAL.
        self.center_coordinate_evidence = center_coordinate_evidence


class DataProvider(abc.ABC):
    """Base interface every concrete provider must implement."""

    provider_name: str = "AbstractDataProvider"

    @abc.abstractmethod
    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        """Returns a list of NormalizedDataPoint for the fields this
        provider is responsible for. Must NEVER raise for 'no data found' --
        return a NormalizedDataPoint with value=None/confidence='UNKNOWN'
        instead, so the caller can decide how to handle it (never crash the
        whole pipeline because one facility type has no data)."""
        raise NotImplementedError

    def _unknown(self, field: str, notes: str) -> NormalizedDataPoint:
        """Helper for the common 'no data available' case."""
        return NormalizedDataPoint(
            field=field, value=None, unit=None,
            source=f"{self.provider_name}（無資料）", source_type="UNKNOWN",
            coordinate=None, confidence="UNKNOWN",
            retrieved_at=datetime.now(), notes=notes,
        )
