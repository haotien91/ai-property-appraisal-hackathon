# -*- coding: utf-8 -*-
"""
LandPriceProvider — 新北市公告土地現值 (data.ntpc.gov.tw OpenAPI).

Phase API-1.5 (Cadastral Identifier Normalization + Official Dataset
Snapshot Stabilization) rewrite of Phase API-1's original implementation.
Same two fixes as providers/expropriation_case_provider.py (see that
module's docstring for the full rationale):

1. IDENTIFIER SEMANTICS: now parses `ctx.parcel_id` via providers/
   cadastral_identifier.py's CadastralParcelIdentifierParser to get the
   real 地籍段名 (section_name), NEVER `ctx.segment_code` (this project's
   own internal 地價區段代碼).
2. PRIMARY QUERY STRATEGY: Phase API-1's bounded live pagination scan is
   demoted to an explicit fallback (`query_land_price_via_live_scan`). The
   PRIMARY path reads providers/cadastral_dataset_cache.py's local
   snapshot, populated by scripts/sync_land_price_dataset.py's single
   full, filter-free pass over the whole ~1.15M-row dataset (a per-
   district-scoped download was attempted first and abandoned -- see the
   "CORRECTED FINDING" note below for why), which buckets every row into a
   separate snapshot keyed by that row's own `district` field. A district
   with no completed sync yet resolves to UNKNOWN with an explicit
   instruction to run the sync script, never a guess and never the old
   bounded-scan fallback invoked silently.

Mandatory DatasetVersion->SchemaAdapter (unchanged from Phase API-1): the
114年 dataset (826870ef-4ea5-48bf-915b-e0a33158cf06) carries ONLY 公告土地
現值 (`official_value_busiprval`), confirmed to have NO 公告地價 column.
Only "114" is listed in `_YEAR_SCHEMAS`; any other year resolves to
UNKNOWN rather than guessing a field mapping never verified.

CORRECTED FINDING (discovered mid-implementation of Phase API-1.5,
supersedes Phase API-1's belief that `filter=district eq ...` was a
working server-side filter for this dataset): while attempting a
per-district sync for 金山區, the sync unexpectedly kept growing past
144,000 rows -- direct inspection showed pages deep into that "district
eq 金山區" query were returning **板橋區** rows. Further direct comparison
proved `filter=district eq 板橋區`, `filter=district eq 金山區`, and NO
filter at all return byte-identical results starting from page 0. **The
`filter` parameter is a COMPLETE no-op on this dataset too -- not just for
segment/lid, for district as well.** Phase API-1's original "verification"
tested only 板橋區, which happens to be the first district in this
dataset's raw storage order (~1.15M rows total), so a fully non-functional
filter looked like it was working. Consequently:
  - scripts/sync_land_price_dataset.py no longer scopes its download via a
    `filter` parameter -- it does a single full, filter-free pass over the
    dataset and buckets rows into per-district snapshots by each row's OWN
    `district` field (the only reliable source of truth), the same
    principle scripts/sync_expropriation_dataset.py already used.
  - `query_land_price_via_live_scan` (the fallback) now also re-verifies
    `district` client-side, and is effectively only useful for whichever
    district sits at the very start of the raw table -- kept for interface/
    test completeness, not as a real per-district query mechanism.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from base import DataProvider, ProviderContext
from cadastral_identifier import (
    CadastralParcelIdentifierParser, LandNumberNormalizer, ParcelIdentifierParseStatus,
)
from cadastral_dataset_cache import CadastralDatasetCache, CadastralDatasetCacheError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    NormalizedDataPoint, LandPriceEvidence, LandPriceFieldStatus,
)

SOURCE_AUTHORITY = "新北市政府地政局"
API_BASE = "https://data.ntpc.gov.tw/api/datasets"

DEFAULT_VERIFIED_YEAR = "114"

# DatasetVersion -> SchemaAdapter (unchanged from Phase API-1).
_YEAR_SCHEMAS = {
    "114": {
        "dataset_id": "826870ef-4ea5-48bf-915b-e0a33158cf06",
        "dataset_name": "新北市114年公告土地現值",
        "land_no_field": "lid",
        "current_value_field": "official_value_busiprval",
        "land_price_field": None,  # confirmed absent from the 114年 schema
    },
}

# Live-scan fallback bound (see query_land_price_via_live_scan's own
# docstring -- no longer the primary path).
_PAGE_SIZE = 200
_MAX_PAGES = 10
_REQUEST_TIMEOUT_S = 6.0

USER_AGENT = (
    "ai-valuation-review-competition-tool/1.0 "
    "(New Taipei City AI Hackathon 2026, Land Administration track; "
    "non-commercial competition use, offline dev candidate)"
)

MOCK_SOURCE = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


def _http_get_json(url: str, timeout_s: float = _REQUEST_TIMEOUT_S, retries: int = 1,
                    backoff_s: float = 2.0) -> "list | None":
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status != 200:
                    return None
                body = json.loads(resp.read().decode("utf-8"))
                return body if isinstance(body, list) else None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            if attempt >= retries:
                return None
            attempt += 1
            time.sleep(backoff_s)


def _to_decimal(raw) -> Optional[Decimal]:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _build_page_url(dataset_id: str, district: str, page: int) -> str:
    params = {"filter": f"district eq {district}", "size": str(_PAGE_SIZE), "page": str(page)}
    return f"{API_BASE}/{dataset_id}/json?{urllib.parse.urlencode(params)}"


def _scan_district(dataset_id: str, land_no_field: str, district: str, segment: str, land_no: str):
    """`filter=district eq ...` is a confirmed COMPLETE no-op on this
    dataset too (see module docstring) -- re-verifies `district`
    client-side on every row, since the "district" URL parameter has no
    actual server-side effect."""
    for page in range(0, _MAX_PAGES):
        rows = _http_get_json(_build_page_url(dataset_id, district, page))
        if rows is None:
            return "error", None
        for row in rows:
            if (str(row.get("district", "")).strip() == district
                    and str(row.get("segment", "")).strip() == segment
                    and str(row.get(land_no_field, "")).strip() == land_no):
                return "found", row
        if len(rows) < _PAGE_SIZE:
            return "not_found", None
    return "truncated", None


def _build_result_from_record(district, segment, land_no, resolved_year, schema, record, meta, notes):
    """Shared record-to-Evidence mapping for both the snapshot path and the
    live-scan fallback path, so the DatasetVersion->SchemaAdapter logic
    (announced_land_price staying FIELD_NOT_AVAILABLE_FOR_YEAR for a
    schema with no such column, never conflated with NOT_FOUND) lives in
    exactly one place."""
    price_status = (
        LandPriceFieldStatus.FIELD_NOT_AVAILABLE_FOR_YEAR if schema["land_price_field"] is None
        else LandPriceFieldStatus.UNKNOWN
    )
    if record is None:
        return LandPriceEvidence(
            district=district, segment=segment, land_no=land_no, dataset_year=resolved_year,
            dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
            source_url=f"https://data.ntpc.gov.tw/datasets/{schema['dataset_id']}",
            announced_land_current_value_status=LandPriceFieldStatus.NOT_FOUND_FOR_PARCEL,
            announced_land_price_status=price_status,
            authoritative_status="OFFICIAL_OPEN_DATA",
            confidence="中", requires_manual_review=True,
            local_snapshot_version=meta.get("downloaded_at") if meta else None,
            checksum=meta.get("checksum_sha256") if meta else None,
            downloaded_at=meta.get("downloaded_at") if meta else None,
            record_count=meta.get("record_count") if meta else None,
            schema_version=meta.get("schema_version") if meta else None,
            notes=notes,
        )
    current_value = _to_decimal(record.get(schema["current_value_field"]))
    land_price_value = _to_decimal(record.get(schema["land_price_field"])) if schema["land_price_field"] else None
    return LandPriceEvidence(
        district=district, segment=segment, land_no=land_no, dataset_year=resolved_year,
        announced_land_current_value=current_value,
        announced_land_current_value_status=(
            LandPriceFieldStatus.AVAILABLE if current_value is not None else LandPriceFieldStatus.UNKNOWN
        ),
        announced_land_price=land_price_value,
        announced_land_price_status=(
            price_status if schema["land_price_field"] is None
            else (LandPriceFieldStatus.AVAILABLE if land_price_value is not None else LandPriceFieldStatus.UNKNOWN)
        ),
        dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
        source_url=f"https://data.ntpc.gov.tw/datasets/{schema['dataset_id']}",
        authoritative_status="OFFICIAL_OPEN_DATA",
        confidence="高", requires_manual_review=True,
        local_snapshot_version=meta.get("downloaded_at") if meta else None,
        checksum=meta.get("checksum_sha256") if meta else None,
        downloaded_at=meta.get("downloaded_at") if meta else None,
        record_count=meta.get("record_count") if meta else None,
        schema_version=meta.get("schema_version") if meta else None,
        notes=notes,
    )


class MockLandPriceProvider(DataProvider):
    provider_name = "MockLandPriceProvider"

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        return [
            NormalizedDataPoint(
                field="announced_land_current_value", value=None, unit="元/M2",
                source=MOCK_SOURCE, source_type="Mock", coordinate=None,
                confidence="UNKNOWN", retrieved_at=now,
                notes=(
                    "Mock Mode示範資料：本Provider於Mock Mode不會呼叫任何網路服務，"
                    "此欄位在Golden Case來源文件中無對應官方公告現值數值可供Mock，"
                    "誠實回傳UNKNOWN而非虛構一個金額。"
                ),
            ),
        ]

    def query_land_price(self, ctx: ProviderContext, year: Optional[str] = None) -> LandPriceEvidence:
        return LandPriceEvidence(
            district=ctx.district, land_no=ctx.parcel_id,
            dataset_year=year or DEFAULT_VERIFIED_YEAR,
            confidence="UNKNOWN", requires_manual_review=True,
            notes="Mock Mode：未呼叫真實API，無官方查證結果可供示範。",
        )


class RealLandPriceProvider(DataProvider):
    """Real path never falls back to Mock. `cache` is DI-injectable purely
    for tests -- production code always uses the default
    CadastralDatasetCache()."""
    provider_name = "RealLandPriceProvider"

    def __init__(self, cache: Optional[CadastralDatasetCache] = None):
        self._cache = cache or CadastralDatasetCache()

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        evidence = self.query_land_price(ctx)
        points = [
            NormalizedDataPoint(
                field="announced_land_current_value",
                value=float(evidence.announced_land_current_value)
                if evidence.announced_land_current_value is not None else None,
                unit="元/M2",
                source=evidence.source_url or (evidence.dataset_name or "新北市公告土地現值"),
                source_type="GovernmentOpenData",
                coordinate=None,
                confidence=evidence.confidence,
                retrieved_at=now,
                notes=f"[{evidence.announced_land_current_value_status.value}] {evidence.notes or ''}".strip(),
            ),
        ]
        points.append(NormalizedDataPoint(
            field="announced_land_price",
            value=float(evidence.announced_land_price) if evidence.announced_land_price is not None else None,
            unit="元/M2",
            source=evidence.source_url or (evidence.dataset_name or "新北市公告土地現值"),
            source_type="GovernmentOpenData",
            coordinate=None,
            confidence="UNKNOWN" if evidence.announced_land_price_status != LandPriceFieldStatus.AVAILABLE
            else evidence.confidence,
            retrieved_at=now,
            notes=f"[{evidence.announced_land_price_status.value}] {evidence.notes or ''}".strip(),
        ))
        return points

    def query_land_price(self, ctx: ProviderContext, year: Optional[str] = None) -> LandPriceEvidence:
        """PRIMARY path (Phase API-1.5): parses ctx.parcel_id (NEVER ctx.
        segment_code) and reads the local per-district snapshot. Never
        calls the network itself."""
        district = ctx.district
        resolved_year = year or DEFAULT_VERIFIED_YEAR

        schema = _YEAR_SCHEMAS.get(resolved_year)
        if schema is None:
            return LandPriceEvidence(
                district=district, land_no=ctx.parcel_id, dataset_year=resolved_year,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=LandPriceFieldStatus.UNKNOWN,
                source_authority=SOURCE_AUTHORITY,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"{resolved_year}年之新北市公告土地現值dataset schema本輪尚未經人工驗證，"
                    "為避免假設不存在的欄位對照，兩欄位皆誠實回報UNKNOWN。"
                ),
            )

        parsed = CadastralParcelIdentifierParser.parse(ctx.parcel_id)
        if parsed.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN:
            price_status = (
                LandPriceFieldStatus.FIELD_NOT_AVAILABLE_FOR_YEAR if schema["land_price_field"] is None
                else LandPriceFieldStatus.UNKNOWN
            )
            return LandPriceEvidence(
                district=district, land_no=parsed.raw_input, dataset_year=resolved_year,
                dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=price_status,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=f"地籍識別字串解析失敗（PARSE_UNCERTAIN）：{parsed.notes}",
            )

        section_name = parsed.section_name
        normalized_lid = LandNumberNormalizer.to_land_price_lid(parsed.land_no_main, parsed.land_no_sub)
        price_status = (
            LandPriceFieldStatus.FIELD_NOT_AVAILABLE_FOR_YEAR if schema["land_price_field"] is None
            else LandPriceFieldStatus.UNKNOWN
        )

        if not district or not section_name or not normalized_lid:
            return LandPriceEvidence(
                district=district, segment=section_name, land_no=normalized_lid, dataset_year=resolved_year,
                dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=price_status,
                confidence="UNKNOWN", requires_manual_review=True,
                notes="案件缺少district或地籍段名/地號無法正規化，無法查詢官方公告土地現值。",
            )

        try:
            record = self._cache.lookup_land_price(
                schema["dataset_id"], district, district, section_name, normalized_lid,
            )
        except CadastralDatasetCacheError:
            return LandPriceEvidence(
                district=district, segment=section_name, land_no=normalized_lid, dataset_year=resolved_year,
                dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=price_status,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"「{district}」之{resolved_year}年公告土地現值尚未同步至本地snapshot，無法查詢。"
                    f"請先執行 `py scripts/sync_land_price_dataset.py`（完整同步，會涵蓋「{district}」）。"
                ),
            )

        meta = self._cache.get_snapshot_meta(schema["dataset_id"], district)
        notes = (
            (f"{resolved_year}年官方公告土地現值本地snapshot（已完整同步「{district}」）中查得相符紀錄。"
             if record is not None else
             f"{resolved_year}年公告土地現值本地snapshot（已完整同步「{district}」）中查無此地號紀錄。")
        )
        if schema["land_price_field"] is None:
            notes += f" {resolved_year}年之新北市公告土地現值dataset schema已確認無「公告地價」欄位（僅有公告土地現值）。"
        return _build_result_from_record(district, section_name, normalized_lid, resolved_year, schema, record, meta, notes)

    def query_land_price_via_live_scan(self, ctx: ProviderContext, section_name: str, land_no: str,
                                        year: Optional[str] = None) -> LandPriceEvidence:
        """FALLBACK, opt-in only -- NEVER called automatically by
        `query_land_price`/`fetch` (Phase API-1.5 forbids this bounded
        live-pagination approach from being the primary strategy). Caller
        must supply the ALREADY-PARSED section_name/land_no explicitly."""
        district = ctx.district
        resolved_year = year or DEFAULT_VERIFIED_YEAR
        schema = _YEAR_SCHEMAS.get(resolved_year)
        if schema is None:
            return LandPriceEvidence(
                district=district, dataset_year=resolved_year,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=LandPriceFieldStatus.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=f"{resolved_year}年schema本輪尚未經人工驗證。",
            )
        price_status = (
            LandPriceFieldStatus.FIELD_NOT_AVAILABLE_FOR_YEAR if schema["land_price_field"] is None
            else LandPriceFieldStatus.UNKNOWN
        )

        if not district or not section_name or not land_no:
            return LandPriceEvidence(
                district=district, segment=section_name, land_no=land_no, dataset_year=resolved_year,
                dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=price_status,
                confidence="UNKNOWN", requires_manual_review=True,
                notes="缺少district/section_name/land_no，無法查詢官方公告土地現值。",
            )

        outcome, record = _scan_district(schema["dataset_id"], schema["land_no_field"], district, section_name, land_no)

        if outcome == "error":
            return LandPriceEvidence(
                district=district, segment=section_name, land_no=land_no, dataset_year=resolved_year,
                dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=price_status,
                confidence="UNKNOWN", requires_manual_review=True,
                notes="呼叫新北市OpenAPI失敗或逾時，無法確認查詢結果。（live-scan備援路徑）",
            )
        if outcome == "truncated":
            return LandPriceEvidence(
                district=district, segment=section_name, land_no=land_no, dataset_year=resolved_year,
                dataset_id=schema["dataset_id"], dataset_name=schema["dataset_name"], source_authority=SOURCE_AUTHORITY,
                announced_land_current_value_status=LandPriceFieldStatus.UNKNOWN,
                announced_land_price_status=price_status,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"「{district}」紀錄筆數超過掃描上限（{_PAGE_SIZE}筆×{_MAX_PAGES}頁），"
                    "無法確認涵蓋範圍。（live-scan備援路徑，正式路徑請執行sync腳本）"
                ),
            )

        notes = (
            f"{resolved_year}年官方公告土地現值live-scan查得相符紀錄。" if outcome == "found"
            else f"{resolved_year}年公告土地現值資料集中live-scan查無此地號紀錄。"
        )
        return _build_result_from_record(
            district, section_name, land_no, resolved_year, schema,
            record if outcome == "found" else None, None, notes,
        )
