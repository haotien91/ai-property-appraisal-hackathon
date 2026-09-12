# -*- coding: utf-8 -*-
"""
UrbanPlanBoundaryProvider — point-in-polygon query against a locally-cached
snapshot of 新北市都市計畫範圍 (which 都市計畫, if any, a coordinate falls
in), answering "urban_plan_status: INSIDE/OUTSIDE/AMBIGUOUS/UNKNOWN/
PLAN_MAPPING_UNAVAILABLE" + a canonical `plan_id`
(data/rules/urban_plan_id_registry.json).

DELIBERATELY independent from providers/ntpc_zoning_provider.py's zone_name
(使用分區) lookup, even though both are point-in-polygon queries against
sibling NTPC GIS datasets read from the same coordinate. They answer
different questions ("which 都市計畫" vs "which 使用分區") from
independently-maintained shapefiles whose boundary lines do not perfectly
coincide, so this module never folds its result into ZoningQueryResult or
vice versa -- callers (see providers/land_use_provider.py) query both and
keep two separate evidences, converging only at
engine/land_use_ratio_engine.py's resolve_floor_area_ratio(zone_name=,
plan_id=) call site.

This provider NEVER touches the network -- see
scripts/sync_ntpc_zoning_dataset.py's sync_plan_boundary() for the actual
(offline, local-archive-only) snapshot build, a separate, manually-
triggered job, never run inside a Lambda request path.

Performance: the boundary dataset is small (50 polygons across all of New
Taipei City), so this provider fully deserializes it into memory once per
process (a lazy, process-level cache keyed by dataset snapshot version --
see `_PROCESS_CACHE`) rather than re-reading/re-parsing the SQLite snapshot
on every request; a stale cache is detected and rebuilt if the underlying
snapshot version changes (e.g. after a fresh sync_plan_boundary() run).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import Coordinate, DatasetSnapshotStatus, UrbanPlanBoundaryResult, UrbanPlanStatus  # noqa: E402
from base import ProviderContext  # noqa: E402  (re-exported for callers' type hints)
from dataset_registry import DatasetRegistry  # noqa: E402

try:
    from shapely import wkb as shapely_wkb
    from shapely.geometry import Point
    _SHAPELY_AVAILABLE = True
except ImportError:  # pragma: no cover -- exercised only if shapely truly missing
    _SHAPELY_AVAILABLE = False

DATASET_ID = "ntpc_plan_boundary"

# ~20-22m at New Taipei City's latitude (1 degree latitude ~= 111km;
# 1 degree longitude at 25 deg N ~= 111km * cos(25 deg) ~= 100.6km) -- a
# coordinate whose distance to a matched (or near-miss) boundary polygon's
# edge is within this tolerance is NOT hard-classified; the two source
# shapefiles (使用分區 / 都市計畫範圍) are digitized independently and their
# boundary lines do not perfectly coincide, so a confident INSIDE/OUTSIDE
# this close to an edge would overstate this provider's actual precision.
BOUNDARY_TOLERANCE_DEGREES = 0.0002

SOURCE_DATASET_NAME = "新北市都市計畫範圍"

# Cache entry: {dataset_version: [(plan_id, plan_name, mapping_found,
# source_key, source_sdf_id, shapely_geometry), ...]}. Keyed on
# (db_path, dataset_version) so a fresh sync_plan_boundary() run (new
# version, new file) is picked up rather than serving a stale in-memory
# copy for the life of the process.
_PROCESS_CACHE: dict = {}


class RealUrbanPlanBoundaryProvider:
    """Real implementation: reads whatever local snapshot DatasetRegistry
    currently points to for `ntpc_plan_boundary`. Never falls back to a
    Mock/guessed value, never picks an arbitrary candidate on ambiguity --
    every degraded case (no coordinate, no snapshot, no match, multiple
    matches, an unmapped source record) resolves to an honest non-INSIDE
    status with requires_manual_review set where appropriate, per this
    codebase's project-wide "不得自行捏造資料" rule."""

    def __init__(self, registry: Optional[DatasetRegistry] = None):
        self.registry = registry or DatasetRegistry()

    def resolve_urban_plan(self, center: Optional[Coordinate]) -> UrbanPlanBoundaryResult:
        if center is None or center.latitude is None or center.longitude is None:
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.UNKNOWN,
                source_dataset=SOURCE_DATASET_NAME,
                notes="無中心點座標，無法查詢（不捏造座標）",
            )

        if not _SHAPELY_AVAILABLE:
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.UNKNOWN,
                source_dataset=SOURCE_DATASET_NAME,
                notes="shapely套件未安裝，無法執行point-in-polygon查詢",
            )

        status = self.registry.check_staleness(DATASET_ID)
        if status == DatasetSnapshotStatus.UNAVAILABLE:
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.UNKNOWN,
                source_dataset=SOURCE_DATASET_NAME,
                notes=(
                    f"本地無{DATASET_ID}快照，尚未執行"
                    "scripts/sync_ntpc_zoning_dataset.py的sync_plan_boundary()，"
                    "或本地檔案已遺失，需人工確認"
                ),
            )

        snapshot = self.registry.get_current_snapshot(DATASET_ID)
        polygons = self._load_cached_polygons(snapshot.local_path, snapshot.local_snapshot_version)
        is_stale = status == DatasetSnapshotStatus.STALE
        stale_note = (
            f"（本地快照版本{snapshot.local_snapshot_version}已逾refresh_policy，可能非最新）"
            if is_stale else ""
        )

        pt = Point(center.longitude, center.latitude)
        contains_matches = [row for row in polygons if row[5].contains(pt)]
        near_boundary = [row for row in polygons if row[5].boundary.distance(pt) <= BOUNDARY_TOLERANCE_DEGREES]

        if len(contains_matches) > 1:
            candidates = "、".join(
                f"{name or '(mapping unavailable)'}({key}/{sdf_id})"
                for _pid, name, _mf, key, sdf_id, _geom in contains_matches
            )
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.AMBIGUOUS,
                source_dataset=SOURCE_DATASET_NAME,
                source_version=snapshot.local_snapshot_version,
                requires_manual_review=True,
                notes=f"座標同時落在{len(contains_matches)}個都市計畫範圍圖徵內（{candidates}），"
                      f"無法唯一判定，需人工確認{stale_note}",
            )

        if len(contains_matches) == 0:
            if near_boundary:
                candidates = "、".join(
                    f"{name or '(mapping unavailable)'}({key}/{sdf_id})"
                    for _pid, name, _mf, key, sdf_id, _geom in near_boundary
                )
                return UrbanPlanBoundaryResult(
                    urban_plan_status=UrbanPlanStatus.UNKNOWN,
                    source_dataset=SOURCE_DATASET_NAME,
                    source_version=snapshot.local_snapshot_version,
                    requires_manual_review=True,
                    notes=(
                        f"座標未落在任何都市計畫範圍圖徵內，但距離下列圖徵邊界在容許誤差"
                        f"（約{BOUNDARY_TOLERANCE_DEGREES}度，約22公尺）內，不可硬判：{candidates}，"
                        f"需人工確認{stale_note}"
                    ),
                )
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.UNKNOWN,
                source_dataset=SOURCE_DATASET_NAME,
                source_version=snapshot.local_snapshot_version,
                notes=f"座標未落在任何都市計畫範圍圖徵內（資料集邊界誤差、範圍外，或超出本資料集"
                      f"涵蓋範圍），不得逕行判定為都市計畫外{stale_note}",
            )

        # Exactly one contains-match.
        plan_id, plan_name, mapping_found, source_key, source_sdf_id, geom = contains_matches[0]
        source_code = f"{source_key}/{source_sdf_id}"
        is_near_edge = contains_matches[0] in near_boundary

        if not mapping_found:
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.PLAN_MAPPING_UNAVAILABLE,
                source_code=source_code,
                source_dataset=SOURCE_DATASET_NAME,
                source_version=snapshot.local_snapshot_version,
                requires_manual_review=True,
                notes=f"座標落在都市計畫範圍圖徵(key={source_key}, SDF_ID={source_sdf_id})內，"
                      f"但該圖徵在plan_id registry中查無對應之plan_id/plan_name，"
                      f"不可用圖徵代碼猜測名稱，需人工確認{stale_note}",
            )

        if plan_id is None:
            # mapping_found=True but plan_id=None -- the "非都市計畫區"
            # (not-a-formal-plan) sentinel, i.e. genuinely OUTSIDE any
            # 都市計畫's boundary, per data/rules/urban_plan_id_registry.json.
            return UrbanPlanBoundaryResult(
                urban_plan_status=UrbanPlanStatus.OUTSIDE,
                source_code=source_code,
                source_dataset=SOURCE_DATASET_NAME,
                source_version=snapshot.local_snapshot_version,
                requires_manual_review=is_stale or is_near_edge,
                notes=f"座標落在都市計畫範圍圖資標示為「{plan_name}」（非正式都市計畫）之圖徵內"
                      f"{'；距邊界在容許誤差內，建議人工確認' if is_near_edge else ''}{stale_note}",
            )

        return UrbanPlanBoundaryResult(
            urban_plan_status=UrbanPlanStatus.INSIDE,
            plan_id=plan_id,
            plan_name=plan_name,
            source_code=source_code,
            source_dataset=SOURCE_DATASET_NAME,
            source_version=snapshot.local_snapshot_version,
            requires_manual_review=is_stale or is_near_edge,
            notes=(
                f"座標落在都市計畫範圍圖徵(key={source_key}, SDF_ID={source_sdf_id})內，"
                f"經data/rules/urban_plan_id_registry.json解析為「{plan_name}」（plan_id={plan_id}）"
                f"{'；距邊界在容許誤差內，建議人工確認' if is_near_edge else ''}{stale_note}"
            ),
        )

    def _load_cached_polygons(
        self, db_path: str, dataset_version: str
    ) -> List[Tuple[Optional[str], Optional[str], bool, str, int, object]]:
        cache_key = (db_path, dataset_version)
        cached = _PROCESS_CACHE.get(cache_key)
        if cached is not None:
            return cached

        rows: List[Tuple[Optional[str], Optional[str], bool, str, int, object]] = []
        conn = sqlite3.connect(db_path)
        try:
            for plan_id, plan_name, mapping_found, source_key, source_sdf_id, geometry_wkb in conn.execute(
                "SELECT plan_id, plan_name, mapping_found, source_key, source_sdf_id, geometry_wkb "
                "FROM plan_boundary_polygons"
            ):
                rows.append((
                    plan_id, plan_name, bool(mapping_found), source_key, source_sdf_id,
                    shapely_wkb.loads(geometry_wkb),
                ))
        finally:
            conn.close()

        # Process-level cache only (never persisted) -- bounded to whatever
        # dataset versions this process has actually queried, which in
        # practice is one (the current DatasetRegistry snapshot) for the
        # life of a Lambda container.
        _PROCESS_CACHE[cache_key] = rows
        return rows
