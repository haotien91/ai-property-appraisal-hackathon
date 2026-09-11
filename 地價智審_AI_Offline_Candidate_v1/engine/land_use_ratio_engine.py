# -*- coding: utf-8 -*-
"""
LandUseRatioEngine — 建蔽率／容積率 two-layer lookup.

Layer 1 (`data/rules/ntpc_common_zone_ratios.json`): 都市計畫法新北市施行
細則附表一 -- a genuine citywide ceiling table (verified 第三十六條:
"各土地使用分區之建蔽率不得超過附表一之規定"). Covers both 建蔽率 and
容積率, but for 住宅區/商業區/其他使用分區 the 容積率 CELL ITSELF says
there is no citywide figure -- "依實際發展，循都市計畫程序，於都市計畫書
中訂定" -- each 都市計畫 area sets its own.

Layer 2 (`data/rules/plan_zone_floor_area_ratios.json`): per-(都市計畫,
zone) 容積率 facts, populated ONLY from that specific plan's own official
document. Currently contains exactly one confirmed entry (金山都市計畫 /
第二種商業區 / 240%, matching Golden Case) -- this is NOT a general
"commercial zone = 240%" rule and must never be applied to any other plan.

Lookup priority (per project decision, not invented here):
  個別都市計畫土地使用分區管制要點（Layer 2, CONFIRMED only）
  -> 新北市共通規定（Layer 1, only when that zone's cell_state is VALUE）
  -> UNAVAILABLE（誠實回報查無資料，不得回退為Mock或猜測值）

建蔽率 has no Layer 2 registry in this codebase (not requested) -- it
always resolves from Layer 1, which does have a real value for every zone
type this table covers.
"""
from __future__ import annotations

import json
import sys
import os
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    LandUseRatioMetric, LandUseRatioCellState, NtpcCommonZoneRatioEntry,
    NtpcCommonZoneRatioDataset, PlanZoneFloorAreaRatioEntry, FloorAreaRatioRuleStatus,
    LandUseRatioResolutionResult,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COMMON_PATH = os.path.join(_REPO_ROOT, "data", "rules", "ntpc_common_zone_ratios.json")
DEFAULT_PLAN_PATH = os.path.join(_REPO_ROOT, "data", "rules", "plan_zone_floor_area_ratios.json")


class LandUseRatioDatasetError(Exception):
    pass


def load_common_zone_ratio_dataset(path: str = DEFAULT_COMMON_PATH) -> NtpcCommonZoneRatioDataset:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise LandUseRatioDatasetError(f"無法讀取或解析{path}：{e}") from e
    try:
        return NtpcCommonZoneRatioDataset.model_validate(raw)
    except Exception as e:
        raise LandUseRatioDatasetError(f"{path}未通過schema驗證：{e}") from e


def load_plan_zone_floor_area_ratios(path: str = DEFAULT_PLAN_PATH) -> List[PlanZoneFloorAreaRatioEntry]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise LandUseRatioDatasetError(f"無法讀取或解析{path}：{e}") from e
    try:
        return [PlanZoneFloorAreaRatioEntry.model_validate(item) for item in raw]
    except Exception as e:
        raise LandUseRatioDatasetError(f"{path}未通過schema驗證：{e}") from e


class LandUseRatioEngine:
    def __init__(
        self,
        common_dataset: Optional[NtpcCommonZoneRatioDataset] = None,
        plan_entries: Optional[List[PlanZoneFloorAreaRatioEntry]] = None,
    ):
        self._common = common_dataset if common_dataset is not None else load_common_zone_ratio_dataset()
        self._plan_entries = plan_entries if plan_entries is not None else load_plan_zone_floor_area_ratios()

        self._common_index: Dict[Tuple[str, LandUseRatioMetric], List[NtpcCommonZoneRatioEntry]] = {}
        for e in self._common.entries:
            self._common_index.setdefault((e.zone_name, e.metric), []).append(e)

        self._plan_index: Dict[Tuple[str, str], PlanZoneFloorAreaRatioEntry] = {
            (e.plan_id, e.zone_name): e for e in self._plan_entries
            if e.rule_status == FloorAreaRatioRuleStatus.CONFIRMED
        }

    def _resolve_common_layer(
        self, zone_name: str, metric: LandUseRatioMetric,
    ) -> Optional[LandUseRatioResolutionResult]:
        """Returns a resolved-or-explicitly-unavailable result from Layer 1
        alone, or None when the caller should try another layer first
        (only for the "no rows at all" case in resolve_floor_area_ratio,
        which still wants to fall through to its own UNAVAILABLE wording).
        A zone with MULTIPLE rows for this metric (currently only 旅館區's
        terrain-split 容積率) is deliberately never silently resolved to
        "whichever row happened to be first" -- this API takes no terrain
        argument, so it must say AMBIGUOUS rather than guess."""
        candidates = self._common_index.get((zone_name, metric))
        if not candidates:
            return None
        if len(candidates) > 1:
            qualifiers = "、".join(c.terrain_qualifier or "(未標示)" for c in candidates)
            return LandUseRatioResolutionResult(
                zone_name=zone_name, metric=metric, resolved_value_pct=None,
                resolution_layer="UNAVAILABLE", rule_status="AMBIGUOUS_MULTIPLE_VALUES",
                requires_manual_review=True,
                notes=f"附表一該分區依條件（{qualifiers}）分別訂有不同數值，"
                "本查詢介面未區分該條件，無法自動判定，需人工確認適用哪一項",
            )
        entry = candidates[0]
        if entry.cell_state == LandUseRatioCellState.VALUE:
            src = self._common.source_document
            return LandUseRatioResolutionResult(
                zone_name=zone_name, metric=metric, resolved_value_pct=entry.value_pct,
                resolution_layer="NTPC_COMMON", rule_status="CONFIRMED",
                legal_source=entry.legal_source, article_or_section=entry.article_or_section,
                source_url=src.source_url, source_document_checksum=src.source_pdf_sha256,
                dataset_version=src.version_note, requires_manual_review=False,
                notes=(entry.caveat_note + "；" if entry.caveat_note else "") + entry.ceiling_override_note,
            )
        return LandUseRatioResolutionResult(
            zone_name=zone_name, metric=metric, resolved_value_pct=None,
            resolution_layer="UNAVAILABLE", rule_status=entry.cell_state.value, requires_manual_review=True,
            notes="附表一該分區之此項數值非固定值（依都市計畫書或相關規定另行訂定），需另行查證",
        )

    def resolve_building_coverage_rate(self, zone_name: str) -> LandUseRatioResolutionResult:
        """建蔽率 always resolves from Layer 1 (no per-plan registry exists
        for this metric in this codebase)."""
        result = self._resolve_common_layer(zone_name, LandUseRatioMetric.BUILDING_COVERAGE_RATE)
        if result is not None:
            return result
        return LandUseRatioResolutionResult(
            zone_name=zone_name, metric=LandUseRatioMetric.BUILDING_COVERAGE_RATE,
            resolved_value_pct=None, resolution_layer="UNAVAILABLE",
            rule_status="UNKNOWN_ZONE", requires_manual_review=True,
            notes=f"都市計畫法新北市施行細則附表一未列出分區名稱「{zone_name}」，"
            "無法比對（可能為分區名稱拼寫不同或本表未涵蓋之特殊分區），需人工確認",
        )

    def resolve_floor_area_ratio(
        self, zone_name: str, plan_id: Optional[str] = None,
        normalized_zone_category: Optional[str] = None,
    ) -> LandUseRatioResolutionResult:
        """優先序：Layer 2（個別都市計畫，需plan_id且該筆為CONFIRMED，用
        `zone_name`原始官方分區名稱查詢，因為個別都市計畫土地使用分區管制
        要點本身就是用原始分區名稱如「第二種商業區」訂定的，不會理會附表一
        的19種通用分類）-> Layer 1（新北市共通規定；用`normalized_zone_
        category`查詢——附表一僅有19種通用分類，「第二種商業區」這種帶子
        級別的原始名稱不會直接命中，呼叫端須自行先做正規化並傳入，未傳入
        時退回用`zone_name`本身比對，僅適用於`zone_name`剛好已是通用分類
        之情形）-> UNAVAILABLE（誠實回報，requires_manual_review=True，
        絕不回退Mock）。"""
        if plan_id is not None:
            plan_entry = self._plan_index.get((plan_id, zone_name))
            if plan_entry is not None:
                return LandUseRatioResolutionResult(
                    zone_name=zone_name, plan_id=plan_id, official_plan_name=plan_entry.plan_name,
                    metric=LandUseRatioMetric.FLOOR_AREA_RATIO,
                    resolved_value_pct=plan_entry.floor_area_ratio_pct, resolution_layer="PLAN_SPECIFIC",
                    rule_status="CONFIRMED",
                    legal_source=plan_entry.document_title, article_or_section=plan_entry.article_or_section,
                    source_url=plan_entry.source_url, source_document_checksum=plan_entry.source_pdf_sha256,
                    dataset_version=plan_entry.document_date, requires_manual_review=plan_entry.requires_manual_review,
                    notes=f"{plan_entry.document_title}（{plan_entry.document_agency}，"
                    f"{plan_entry.document_date}）{plan_entry.article_or_section}"
                    + (f"；{plan_entry.notes}" if plan_entry.notes else ""),
                )

        common_lookup_key = normalized_zone_category if normalized_zone_category is not None else zone_name
        common_result = self._resolve_common_layer(common_lookup_key, LandUseRatioMetric.FLOOR_AREA_RATIO)
        if common_result is not None and common_result.resolved_value_pct is not None:
            common_result.plan_id = plan_id
            common_result.zone_name = zone_name  # report against the caller's original (raw) query subject
            return common_result
        if common_result is not None and common_result.rule_status == "AMBIGUOUS_MULTIPLE_VALUES":
            # Ambiguous (e.g. 旅館區's terrain split) is itself the honest
            # answer -- surface it rather than falling through to the
            # generic "no common value" wording below, which would imply
            # there is simply nothing on file (there IS data, just not
            # resolvable without a terrain argument this API doesn't take).
            common_result.plan_id = plan_id
            common_result.zone_name = zone_name
            return common_result

        plan_note = (
            f"個別都市計畫（plan_id={plan_id}）未登記此分區之容積率規則；"
            if plan_id else "未提供plan_id，略過個別都市計畫查詢；"
        )
        return LandUseRatioResolutionResult(
            zone_name=zone_name, plan_id=plan_id, metric=LandUseRatioMetric.FLOOR_AREA_RATIO,
            resolved_value_pct=None, resolution_layer="UNAVAILABLE",
            rule_status=FloorAreaRatioRuleStatus.UNAVAILABLE_PER_PLAN.value, requires_manual_review=True,
            notes=plan_note + "都市計畫法新北市施行細則附表一該分區容積率亦無全市共通值"
            "（依實際發展，循都市計畫程序，於都市計畫書中訂定），須查閱該分區實際所屬"
            "都市計畫之土地使用分區管制要點，不得沿用其他都市計畫之數值",
        )
