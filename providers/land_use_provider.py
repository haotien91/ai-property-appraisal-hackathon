# -*- coding: utf-8 -*-
"""
LandUseProvider — 土地使用管制／自然條件／其他影響因素 fields.

MockLandUseProvider: all values are the actual Golden Case values already
independently verified against 查估書表範本.pdf 表1 in Phase 2/3 (NOT
fabricated). Coordinate is None throughout because the official source
documents give facility names/percentages/text descriptions, not lat/lon --
returning None here (rather than inventing a coordinate) is the honest
behavior per "不得自行捏造資料".

RealLandUseProvider: `land_use_zone` (RealNtpcZoningProvider, a local zoning-
shapefile snapshot), `building_coverage_ratio` (engine/land_use_ratio_
engine.py Layer 1 -- 都市計畫法新北市施行細則附表一, a genuine citywide
table), and `floor_area_ratio` (same engine's Layer 2 per-plan registry,
falling back to Layer 1 only for the zone types that actually have a
citywide value) now have confirmed real data sources. The zoning
provider's official_raw_zone_name (e.g. "第二種商業區") is normalized via
engine/zone_name_normalizer.py purely to look up Layer 1's 19-category
table -- it is never overwritten; Layer 2 always keys on the raw name.
`floor_area_ratio`'s Layer 2 requires ctx.plan_id, which is ALWAYS
human-supplied (see providers/base.py's ProviderContext docstring for why
automatic coordinate->都市計畫 resolution is not available). Every other
field this provider is responsible for (construction_prohibited/restricted,
drainage_quality, terrain, ...) still has no reliable public data source
(tracked in docs/backlog.md) and is returned as UNKNOWN -- NEVER silently
filled from MockLandUseProvider's Golden Case values, which would
misrepresent a Golden-Case-specific number as the real answer for whatever
segment is actually under review.
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from base import DataProvider, ProviderContext
from ntpc_zoning_provider import RealNtpcZoningProvider
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import NormalizedDataPoint  # noqa: E402
from engine.land_use_ratio_engine import LandUseRatioEngine  # noqa: E402
from engine.zone_name_normalizer import normalize_zone_name  # noqa: E402

SRC = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


class MockLandUseProvider(DataProvider):
    provider_name = "MockLandUseProvider"

    # Golden Case values, matching data/golden/golden_case_input.py BASE_REGIONAL
    _MOCK_VALUES = {
        "zoning_inside_outside": ("都市計畫內", None),
        "land_use_zone": ("第二種商業區", None),
        "building_coverage_ratio": (70, "%"),
        "floor_area_ratio": (240, "%"),
        "construction_prohibited": ("無", None),
        "construction_restricted": ("無", None),
        "drainage_quality": ("有排水系統不易淹水", None),
        "terrain": ("該區地勢平坦", None),
        "sunlight": (None, None),   # Golden Case leaves this blank in the source form
        "view": (None, None),
        "slope_degree": (None, None),
        "wind": (None, None),
        "soil_quality": (None, None),
        "building_density": (95, "%"),
        "building_type": ("連棟透天厝、公寓", None),
        "land_use_current": ("住商混合", None),
    }

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        points = []
        for field, (value, unit) in self._MOCK_VALUES.items():
            if value is None:
                points.append(self._unknown(
                    field, f"查估書表範本.pdf表1 Golden Case此欄位留空，來源未提供數值"
                ))
                continue
            points.append(NormalizedDataPoint(
                field=field, value=value, unit=unit, source=SRC,
                source_type="Mock", coordinate=None, confidence="高",
                retrieved_at=datetime.now(),
                notes="Mock資料，數值取自Golden Case官方範例，非即時查詢結果",
            ))
        return points


class RealLandUseProvider(DataProvider):
    provider_name = "RealLandUseProvider"

    # Every MockLandUseProvider field except land_use_zone/building_coverage_
    # ratio/floor_area_ratio -- no confident public data source exists for
    # these yet (docs/backlog.md). Listed explicitly (not derived from
    # MockLandUseProvider._MOCK_VALUES) so a future field addition to the
    # Mock list does not silently start returning UNKNOWN here without a
    # deliberate decision.
    _NO_REAL_SOURCE_FIELDS = [
        "zoning_inside_outside",
        "construction_prohibited", "construction_restricted", "drainage_quality",
        "terrain", "sunlight", "view", "slope_degree", "wind", "soil_quality",
        "building_density", "building_type", "land_use_current",
    ]

    def __init__(self, zoning_provider: Optional[RealNtpcZoningProvider] = None,
                 ratio_engine: Optional[LandUseRatioEngine] = None):
        self._zoning_provider = zoning_provider or RealNtpcZoningProvider()
        self._ratio_engine = ratio_engine or LandUseRatioEngine()

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        zoning_result = self._zoning_provider.query(ctx.center_coordinate)
        official_raw_zone_name = zoning_result.zone_name
        norm = normalize_zone_name(official_raw_zone_name) if official_raw_zone_name is not None else None

        points = [
            self._land_use_zone_point(zoning_result, ctx, now),
            self._building_coverage_ratio_point(official_raw_zone_name, norm, now),
            self._floor_area_ratio_point(official_raw_zone_name, norm, ctx.plan_id, now),
        ]
        for field in self._NO_REAL_SOURCE_FIELDS:
            points.append(self._unknown(
                field, "尚無可靠公開資料源可用於real模式，需人工確認（見docs/backlog.md）",
            ))
        return points

    def _land_use_zone_point(self, result, ctx: ProviderContext, now: datetime) -> NormalizedDataPoint:
        if result.zone_name is None:
            return self._unknown("land_use_zone", result.notes or "查無都市計畫使用分區資料")

        # result.zone_name is passed through exactly as read from the source
        # shapefile's ZONE attribute -- never rewritten/"corrected" to match
        # some expected format. NormalizedDataPoint has no first-class
        # source_url/dataset_version/legal_status fields yet (that schema
        # migration is separate, larger work -- see docs/backlog.md), so
        # those are composed into `source`/`notes` here rather than lost.
        # dataset_id (DatasetRegistry's primary key, e.g. "ntpc_zoning")
        # composed in alongside dataset_version -- DatasetRegistry.
        # get_current_snapshot() is keyed by dataset_id, not dataset_version,
        # so dataset_version alone cannot locate the registry row that
        # carries checksum/local_path/source_last_modified (see providers/
        # dataset_registry.py). Both together let a human trace this exact
        # 「第二種商業區」determination back to a specific local snapshot,
        # and from there to its SHA-256 -- as long as that snapshot is
        # still DatasetRegistry's CURRENT row for this dataset_id; a later
        # re-sync overwrites the row (single current-snapshot-per-dataset_id
        # design, no history table), which would make an OLDER evidence
        # entry's exact snapshot unretrievable -- an honest, documented
        # limitation, not silently papered over.
        source_extra = (
            f"dataset_id={result.dataset_id or '未知'}；"
            f"資料集版本={result.dataset_version or '未知'}；"
            f"來源網址={result.source_url}；"
            f"法定地位={result.legal_status}；"
            f"取得方式={result.derivation_method}"
        )
        combined_notes = f"{result.notes}；{source_extra}" if result.notes else source_extra

        return NormalizedDataPoint(
            field="land_use_zone", value=result.zone_name, unit=None,
            source=f"{result.source_name}（{result.source_agency}）",
            source_type="GovernmentOpenData",
            coordinate=ctx.center_coordinate,
            confidence="中" if result.requires_manual_review else "高",
            retrieved_at=now, notes=combined_notes,
        )

    def _building_coverage_ratio_point(
        self, official_raw_zone_name: Optional[str], norm, now: datetime,
    ) -> NormalizedDataPoint:
        """Layer 1 only (都市計畫法新北市施行細則附表一) -- 建蔽率 has no
        per-plan registry in this codebase. official_raw_zone_name is
        normalized (never overwritten) purely to look up the common table;
        the raw value itself is not surfaced on this field (it belongs to
        land_use_zone) but drives everything here."""
        if official_raw_zone_name is None:
            return self._unknown("building_coverage_ratio", "查無都市計畫使用分區資料，無法查詢建蔽率")

        if norm.normalized_zone_category is None:
            return self._unknown(
                "building_coverage_ratio",
                f"官方分區名稱「{official_raw_zone_name}」無法可靠歸類至都市計畫法新北市施行"
                f"細則附表一之標準分區類別（normalization_method={norm.normalization_method}），"
                "需人工確認",
            )

        result = self._ratio_engine.resolve_building_coverage_rate(norm.normalized_zone_category)
        if result.resolved_value_pct is None:
            return self._unknown(
                "building_coverage_ratio",
                f"分區「{norm.normalized_zone_category}」（原始：{official_raw_zone_name}）"
                f"於附表一無固定建蔽率數值：{result.notes}",
            )

        notes = (
            f"official_raw_zone_name={official_raw_zone_name}；"
            f"normalized_zone_category={norm.normalized_zone_category}"
            f"（normalization_method={norm.normalization_method}，"
            f"normalization_confidence={norm.normalization_confidence}）；"
            f"resolution_layer={result.resolution_layer}；{result.notes}"
        )
        return NormalizedDataPoint(
            field="building_coverage_ratio", value=float(result.resolved_value_pct), unit="%",
            source="都市計畫法新北市施行細則附表一（新北市政府）", source_type="GovernmentOpenData",
            coordinate=None, confidence="中", retrieved_at=now, notes=notes,
        )

    def _floor_area_ratio_point(
        self, official_raw_zone_name: Optional[str], norm, plan_id: Optional[str], now: datetime,
    ) -> NormalizedDataPoint:
        """Layer 2（個別都市計畫，需plan_id）優先於Layer 1（新北市共通規定，
        僅當該分區容積率cell_state=VALUE）；查無資料時UNKNOWN，絕不回退
        Mock或猜測。plan_id永遠是外部（人工）提供，本方法不自行推定。"""
        if official_raw_zone_name is None:
            return self._unknown("floor_area_ratio", "查無都市計畫使用分區資料，無法查詢容積率")

        # Layer 2 keys on official_raw_zone_name AS-IS (never the normalized
        # category) -- a specific plan's 土地使用分區管制要點 speaks in its
        # own exact zone names (e.g. "第二種商業區"), not the施行細則附表一's
        # generic categories. The engine's Layer 1 fallback (inside
        # resolve_floor_area_ratio) is given normalized_zone_category
        # separately so a sub-graded raw name that DOES have a real common
        # value (e.g. hypothetically "第一種工業區" -> "工業區" -> 210%) is
        # not missed just because it never appears verbatim in the 19-row
        # common table.
        result = self._ratio_engine.resolve_floor_area_ratio(
            official_raw_zone_name, plan_id=plan_id,
            normalized_zone_category=norm.normalized_zone_category if norm else None,
        )

        if result.resolved_value_pct is not None:
            notes = (
                f"official_raw_zone_name={official_raw_zone_name}；plan_id={plan_id}；"
                f"resolution_layer={result.resolution_layer}；{result.notes}"
            )
            return NormalizedDataPoint(
                field="floor_area_ratio", value=float(result.resolved_value_pct), unit="%",
                source=(
                    "個別都市計畫土地使用分區管制要點" if result.resolution_layer == "PLAN_SPECIFIC"
                    else "都市計畫法新北市施行細則附表一（新北市政府）"
                ),
                source_type="GovernmentOpenData", coordinate=None,
                confidence="中" if result.requires_manual_review else "高",
                retrieved_at=now, notes=notes,
            )

        if plan_id is None:
            # plan_id reaching this function as None means: no human
            # override, AND coordinate-based auto-resolution (backend/
            # handlers/collect_data.py's _resolve_urban_plan_result ->
            # providers/urban_plan_boundary_provider.py) either wasn't
            # attempted (Mock mode / no coordinate) or did not resolve to
            # exactly one 都市計畫 (OUTSIDE/AMBIGUOUS/UNKNOWN/PLAN_MAPPING_
            # UNAVAILABLE) -- this note deliberately does not repeat the
            # older "本系統無法自動由座標判定" claim, which is no longer
            # accurate in general (see providers/base.py's ProviderContext
            # docstring for the full history).
            zoning_note = (
                f"官方分區名稱「{official_raw_zone_name}」；未提供plan_id，且無法由座標自動判定"
                "所屬唯一都市計畫（可能為Mock模式無座標、座標落在都市計畫外、"
                "無法唯一判定，或資料集尚未同步），需人工提供plan_id或逕行人工查詢"
                "該分區實際所屬都市計畫之容積率規定"
            )
        else:
            zoning_note = f"官方分區名稱「{official_raw_zone_name}」；plan_id={plan_id}；{result.notes}"
        return self._unknown("floor_area_ratio", zoning_note)
