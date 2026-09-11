# -*- coding: utf-8 -*-
"""RoadProvider — 主要道路寬度／區段內道路平均寬度／道路規劃闢建程度.

RealRoadProvider implements Phase 8's Road Width Multi-Evidence Model
(engine/road_width_resolver.py + engine/road_width_validator.py). There is
NO single, precise, authoritative New Taipei City road-width API this
codebase has confirmed access to -- verified during this round's source
survey (see docs/backlog.md "Road Width Multi-Evidence Model" entry):
  A. 政府官方／授權道路資料（如路型圖資之道路屬性表）— not confirmed
     locally available or reliably queryable for this competition.
  B. 都市計畫道路（計畫寬度）— New Taipei City urban-plan documents do
     record road-width REQUIREMENTS (e.g. this round's archived 金山細部
     計畫 PDF: "第一、二種住宅區面臨計畫道路寬度10公尺以上"), but not a
     structured, queryable per-road width dataset this codebase has
     digitized -- unlike land_use_ratio_engine's 建蔽率/容積率 tables,
     there is no equivalent per-road width table to look up.
  C. OSM/external map reference — already documented (docs/backlog.md) as
     too sparse for reliable Taiwan road-width coverage.
  D. 系統幾何估算 (GIS road-edge geometry) — no such capability exists in
     this codebase.
  E. 使用者／估價書申報值 — always available (what 表1's main_road_width
     field itself carries), but this is the SUBMITTED side of the
     comparison, never a resolution candidate (RoadWidthEvidenceType.
     SUBMITTED_VALUE is explicitly excluded by RoadWidthResolver).

Given none of A-D has a confirmed, reliable, queryable source right now,
RealRoadProvider's default construction (no injected evidence_sources)
genuinely returns UNKNOWN for main_road_width -- NOT Golden Case's 18m,
NOT a guess. It accepts injected `evidence_sources` (callables producing
List[RoadWidthEvidence]) so a future confirmed source (a synced official
dataset, mirroring providers/ntpc_zoning_provider.py's pattern) can be
wired in later without redesigning this provider or the Resolver/Validator
that consume its output."""
from __future__ import annotations
from datetime import datetime
from typing import Callable, List, Optional

from base import DataProvider, ProviderContext
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint, RoadWidthEvidence  # noqa: E402
from engine.road_width_resolver import RoadWidthResolver  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


class MockRoadProvider(DataProvider):
    provider_name = "MockRoadProvider"

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        return [
            NormalizedDataPoint(field="main_road_name", value="中山路", unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now,
                                 notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="main_road_width", value=18, unit="M",
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now,
                                 notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="segment_avg_road_width", value=12, unit="M",
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now,
                                 notes="Mock資料，取自Golden Case"),
            NormalizedDataPoint(field="road_development_level", value="已完全開發", unit=None,
                                 source=SRC, source_type="Mock", coordinate=None,
                                 confidence="高", retrieved_at=now,
                                 notes="Mock資料，取自Golden Case"),
        ]


class RealRoadProvider(DataProvider):
    """See module docstring for the honest current-state survey (A-E).
    `evidence_sources` are optional DI hooks -- each a callable
    `(ProviderContext) -> List[RoadWidthEvidence]` representing one
    non-submitted evidence category (A/B/C/D). None injected (this round's
    actual default) means genuinely no confirmed real source exists yet,
    so every evidence_sources call below returns [] and RoadWidthResolver
    correctly reports UNAVAILABLE -- never Golden Case's 18m/12m, never a
    guess."""
    provider_name = "RealRoadProvider"

    # segment_avg_road_width/road_development_level have no resolution
    # infrastructure at all yet this round (Phase 8 item 5: main_road_width
    # only) -- tracked in docs/backlog.md, always UNKNOWN in real mode.
    _NO_REAL_SOURCE_FIELDS = ["segment_avg_road_width", "road_development_level"]

    def __init__(self, evidence_sources: Optional[List[Callable[[ProviderContext], List[RoadWidthEvidence]]]] = None,
                 resolver: Optional[RoadWidthResolver] = None):
        self._evidence_sources = evidence_sources or []
        self._resolver = resolver or RoadWidthResolver()

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        resolution = self.resolve_main_road_width(ctx)

        points = [self._main_road_width_point(resolution, now)]
        if resolution.status.value == "RESOLVED" and resolution.resolved_evidence.road_name:
            points.append(NormalizedDataPoint(
                field="main_road_name", value=resolution.resolved_evidence.road_name, unit=None,
                source=resolution.resolved_evidence.source_name or self.provider_name,
                source_type=resolution.resolved_evidence.evidence_type.value,
                coordinate=None,
                confidence="中" if resolution.requires_manual_review else "高",
                retrieved_at=now, notes=resolution.notes,
            ))
        else:
            points.append(self._unknown("main_road_name", "查無可信道路名稱來源，需人工確認"))

        for field in self._NO_REAL_SOURCE_FIELDS:
            points.append(self._unknown(
                field, "尚無可靠公開資料源可用於real模式，需人工確認（見docs/backlog.md）",
            ))
        return points

    def resolve_main_road_width(self, ctx: ProviderContext):
        """Aggregates every injected evidence source's output for THIS
        ctx and hands it to RoadWidthResolver -- exposed separately (not
        just via fetch()'s NormalizedDataPoint shape) because AuditEngine's
        RoadWidthValidator needs the full RoadWidthResolutionResult
        (all evidence, conflict state), not a collapsed single value."""
        evidence: List[RoadWidthEvidence] = []
        for source in self._evidence_sources:
            evidence.extend(source(ctx) or [])
        return self._resolver.resolve(evidence)

    def _main_road_width_point(self, resolution, now: datetime) -> NormalizedDataPoint:
        if resolution.status.value != "RESOLVED":
            return self._unknown(
                "main_road_width",
                resolution.notes or "查無可信道路寬度Evidence，需人工確認",
            )
        best = resolution.resolved_evidence
        return NormalizedDataPoint(
            field="main_road_width", value=float(resolution.resolved_width_m), unit="M",
            source=best.source_name or self.provider_name,
            source_type=best.evidence_type.value,
            coordinate=None,
            confidence="中" if resolution.requires_manual_review else "高",
            retrieved_at=now, notes=resolution.notes,
        )
