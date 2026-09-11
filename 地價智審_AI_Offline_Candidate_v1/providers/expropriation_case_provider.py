# -*- coding: utf-8 -*-
"""
ExpropriationCaseProvider — 新北市已公告徵收案件地籍資料 (data.ntpc.gov.tw
OpenAPI, dataset_id DD9C0006-8FAD-450D-8C7B-E59240B0ED13).

Phase API-1.5 (Cadastral Identifier Normalization + Official Dataset
Snapshot Stabilization) rewrite of Phase API-1's original implementation.
Two fixes, both documented in docs/phase9/api_integration_spec.md:

1. IDENTIFIER SEMANTICS: Phase API-1 passed `ctx.segment_code` (this
   project's own internal 地價區段代碼, e.g. "P002-00") into the query as
   if it were the government dataset's `segment` field (a real 地籍段名,
   e.g. "金美段") -- a category error. This module now parses `ctx.
   parcel_id` (the case's `base_parcel_id`, e.g. "金美段489地號") via
   providers/cadastral_identifier.py's CadastralParcelIdentifierParser to
   get the real section_name, and NEVER reads `ctx.segment_code` for this
   purpose.

2. PRIMARY QUERY STRATEGY: Phase API-1's live pagination scan (bounded at
   2000 rows/district) is demoted to an explicit, separately-named fallback
   method (`query_case_via_live_scan`) that `query_case`/`fetch` NEVER call
   automatically. The PRIMARY path now reads from providers/
   cadastral_dataset_cache.py's local snapshot (populated out-of-band by
   scripts/sync_expropriation_dataset.py, which fully syncs this dataset --
   only ~9,437 rows city-wide, small enough for a complete sync). A
   complete local index eliminates the truncation-driven UNKNOWN Phase
   API-1 produced for any district with more records than its scan bound.

Coverage limit (from the dataset's own description, not a guess): only
92年以後 內政部核准之一般徵收案件（不含更正及撤銷徵收），no 區段徵收.
Therefore a query miss can NEVER be reported as "this parcel was not
expropriated" -- see domain.models.ExpropriationCaseStatus's own docstring.
This provider produces EXTERNAL CORROBORATING EVIDENCE ONLY.

CORRECTED FINDING (discovered later in Phase API-1.5, supersedes Phase
API-1's belief that `filter=district eq ...` was a working server-side
filter): direct comparison proved `filter=district eq 蘆洲區`, `filter=
district eq 板橋區` (a district that never once appears in the response),
and NO filter at all return byte-identical results. **The `filter`
parameter is a COMPLETE no-op on this dataset -- not just for segment/id,
for district too.** Phase API-1's "verification" was a false positive: it
only ever tested with 蘆洲區, which happens to be the first district in
this dataset's raw storage order, so an entirely non-functional filter
looked like it was narrowing results. This is exactly why the PRIMARY path
(`query_case`) below never issues a live filtered request at all -- it
only reads scripts/sync_expropriation_dataset.py's already-synced,
filter-free full-table snapshot. `query_case_via_live_scan` (the fallback)
now also re-verifies `district` client-side (not just segment/id), since
without a working filter it is effectively scanning from page 0 of the
WHOLE table regardless of which district was requested.

---

## Multi-Record Parcel Semantics (Phase API-1.9)

Phase API-1.8's live full-dataset sync discovered (district,segment,
land_no) is NOT a unique key in this dataset. Phase API-1.9's follow-up
audit of the complete 9,437-row snapshot classified EVERY multi-record
parcel precisely (see docs/phase9/api_integration_spec.md for the full
methodology/output):

    TOTAL_ROWS = 9437
    UNIQUE_PARCEL_KEYS = 8966
    MULTI_RECORD_PARCEL_KEYS = 441
    MAX_RECORDS_PER_PARCEL = 3
    EXACT_DUPLICATE_FULL_ROWS = 90 (raw redundant rows, not distinct parcels)

    Of the 441 multi-record parcels:
      322 = same year, different project (PARCEL_MULTI_EVENT)
       57 = different year AND different project (PARCEL_MULTI_EVENT)
        0 = different year, same project
       62 = EXACT_SOURCE_DUPLICATE (every row for that parcel is byte-
            identical -- one real event, listed 2-3 times in the source)

`query_case()` (the PRIMARY path) therefore NEVER takes "the first
matching row" -- it calls `CadastralDatasetCache.lookup_expropriation_
matches()` (not `lookup_expropriation()`, which is a legacy single-result
wrapper kept only for callers that have accepted that limitation) to
retrieve EVERY raw row, then collapses raw rows sharing the identical
(sus_year, pro_name) signature down to ONE `ExpropriationCaseMatch` each
-- an EXACT_SOURCE_DUPLICATE parcel's 2-3 identical rows produce exactly
one match (not 2-3 phantom "events"), while `ExpropriationCaseEvidence.
raw_match_count` separately preserves the raw row count for audit. A
genuine PARCEL_MULTI_EVENT parcel (district/segment/land_no shared,
sus_year and/or pro_name differ) produces one match per distinct event --
ALL of them, never silently narrowed to one.

Deterministic ordering: matches are returned in the order `lookup_
expropriation_matches()` sorts its rows (full tuple: district, segment,
land_no_raw, sus_year, pro_name) -- since district/segment/land_no are
constant within one query, this is effectively sorted by (sus_year,
pro_name), matching this round's suggested ordering and guaranteeing the
same snapshot always returns matches in the same order regardless of
SQLite's own internal row order.

Backward compatibility for the pre-1.9 single-value convenience fields
(`announcement_year`/`project_name`): populated from that one event only
when there is EXACTLY one distinct event (`match_count == 1`, unchanged
behavior for the 8,966 - 62 = 8,904 parcels that were already unambiguous);
left explicitly `None` -- never the first/latest/highest-year event -- when
`match_count > 1`, so a caller reading only these two legacy fields for a
genuine multi-event parcel sees an honest "no single value applies", not a
silently-arbitrary one. `matches` always carries the full, correct list.

`query_case_via_live_scan()` (the FALLBACK, never auto-invoked) is NOT
covered by this multi-match guarantee: `_scan_district()` still returns
on the FIRST matching row it encounters while paging through a district,
by design (enumerating every match would require always scanning to the
district's end rather than stopping early, materially changing this
fallback's already-bounded resource profile). This is an accepted,
explicitly-documented scope boundary -- the fallback exists only for when
no local snapshot is available at all, and the frozen "Official Cadastral
Evidence Pipeline" this round formalizes is the snapshot-based PRIMARY
path, not the live-scan fallback.
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
from typing import List, Optional

from base import DataProvider, ProviderContext
from cadastral_identifier import (
    CadastralParcelIdentifierParser, LandNumberNormalizer, ParcelIdentifierParseStatus,
)
from cadastral_dataset_cache import CadastralDatasetCache, CadastralDatasetCacheError, SCOPE_ALL

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    NormalizedDataPoint, ExpropriationCaseEvidence, ExpropriationCaseStatus, ExpropriationCaseMatch,
)

DATASET_ID = "DD9C0006-8FAD-450D-8C7B-E59240B0ED13"
DATASET_NAME = "新北市已公告徵收案件地籍資料"
SOURCE_AUTHORITY = "新北市政府地政局"
DATASET_URL = f"https://data.ntpc.gov.tw/api/datasets/{DATASET_ID}/json"
OFFICIAL_PAGE_URL = f"https://data.ntpc.gov.tw/datasets/{DATASET_ID}"

# Live-scan fallback bound (see query_case_via_live_scan's own docstring --
# this method is NO LONGER the primary path, kept only for explicit,
# opt-in use / tests demonstrating why the snapshot path exists).
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
    """Mirrors providers/osm_facility_lookup.py's `_http_get` retry/backoff
    convention (stdlib urllib only). Returns None for ANY failure."""
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


def _build_page_url(district: str, page: int) -> str:
    params = {"filter": f"district eq {district}", "size": str(_PAGE_SIZE), "page": str(page)}
    return f"{DATASET_URL}?{urllib.parse.urlencode(params)}"


def _scan_district(district: str, segment: str, land_no: str):
    """0-indexed `page`. `filter=district eq ...` is a confirmed COMPLETE
    no-op (see module docstring) -- this function therefore ALSO
    re-verifies `district` client-side on every row (not just segment/id),
    since the "district" URL parameter below has no actual effect on what
    the server returns. Returns ("found", row) / ("not_found", None) /
    ("truncated", None) / ("error", None)."""
    for page in range(0, _MAX_PAGES):
        rows = _http_get_json(_build_page_url(district, page))
        if rows is None:
            return "error", None
        for row in rows:
            if (str(row.get("district", "")).strip() == district
                    and str(row.get("segment", "")).strip() == segment
                    and str(row.get("id", "")).strip() == land_no):
                return "found", row
        if len(rows) < _PAGE_SIZE:
            return "not_found", None
    return "truncated", None


class MockExpropriationCaseProvider(DataProvider):
    provider_name = "MockExpropriationCaseProvider"

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        return [
            NormalizedDataPoint(
                field="expropriation_case_status", value=ExpropriationCaseStatus.UNKNOWN.value,
                unit=None, source=MOCK_SOURCE, source_type="Mock", coordinate=None,
                confidence="UNKNOWN", retrieved_at=now,
                notes=(
                    "Mock Mode示範資料：本Provider於Mock Mode不會呼叫任何網路服務，"
                    "此欄位在Golden Case來源文件中無對應官方查證數值可供Mock，"
                    "誠實回傳UNKNOWN而非虛構一個FOUND/NOT_FOUND狀態。"
                ),
            ),
        ]

    def query_case(self, ctx: ProviderContext) -> ExpropriationCaseEvidence:
        return ExpropriationCaseEvidence(
            status=ExpropriationCaseStatus.UNKNOWN,
            district=ctx.district, land_no=ctx.parcel_id,
            confidence="UNKNOWN", requires_manual_review=True,
            notes="Mock Mode：未呼叫真實API，無官方查證結果可供示範。",
        )


class RealExpropriationCaseProvider(DataProvider):
    """Real path never falls back to Mock. `cache` is DI-injectable (mirrors
    RealRoadProvider's `evidence_sources` DI pattern) purely for tests --
    production code always uses the default CadastralDatasetCache()."""
    provider_name = "RealExpropriationCaseProvider"

    def __init__(self, cache: Optional[CadastralDatasetCache] = None):
        self._cache = cache or CadastralDatasetCache()

    def fetch(self, ctx: ProviderContext) -> List[NormalizedDataPoint]:
        now = datetime.now()
        evidence = self.query_case(ctx)
        return [
            NormalizedDataPoint(
                field="expropriation_case_status", value=evidence.status.value,
                unit=None,
                source=evidence.source_url or DATASET_NAME,
                source_type="GovernmentOpenData",
                coordinate=None,
                confidence=evidence.confidence,
                retrieved_at=now,
                notes=evidence.notes,
            ),
        ]

    def query_case(self, ctx: ProviderContext) -> ExpropriationCaseEvidence:
        """PRIMARY path (Phase API-1.5): parses ctx.parcel_id into a real
        cadastral section_name/land_no (NEVER ctx.segment_code -- see
        module docstring), then looks up the LOCAL SYNCED SNAPSHOT. Never
        calls the network itself."""
        district = ctx.district
        retrieved_at = datetime.now(timezone.utc)

        parsed = CadastralParcelIdentifierParser.parse(ctx.parcel_id)
        if parsed.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN:
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.UNKNOWN,
                district=district, land_no=parsed.raw_input,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
                source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
                retrieved_at=retrieved_at,
                query_parameters={"raw_parcel_identifier": parsed.raw_input},
                confidence="UNKNOWN", requires_manual_review=True,
                notes=f"地籍識別字串解析失敗（PARSE_UNCERTAIN）：{parsed.notes}",
            )

        section_name = parsed.section_name
        normalized_id = LandNumberNormalizer.to_expropriation_id(parsed.land_no_main, parsed.land_no_sub)
        query_parameters = {
            "lookup.district": district or "", "lookup.section_name": section_name or "",
            "lookup.normalized_land_no": normalized_id or "",
            "raw_parcel_identifier": parsed.raw_input,
        }

        if not district or not section_name or not normalized_id:
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.UNKNOWN,
                district=district, segment=section_name, land_no=normalized_id,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
                source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
                retrieved_at=retrieved_at, query_parameters=query_parameters,
                confidence="UNKNOWN", requires_manual_review=True,
                notes="案件缺少district或地籍段名/地號無法正規化，無法查詢官方徵收案件資料。",
            )

        try:
            raw_matches = self._cache.lookup_expropriation_matches(
                DATASET_ID, SCOPE_ALL, district, section_name, normalized_id
            )
        except CadastralDatasetCacheError:
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.UNKNOWN,
                district=district, segment=section_name, land_no=normalized_id,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
                source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
                retrieved_at=retrieved_at, query_parameters=query_parameters,
                authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    "官方徵收案件資料尚未同步至本地snapshot，無法查詢。"
                    "請先執行 `py scripts/sync_expropriation_dataset.py`。"
                ),
            )

        meta = self._cache.get_snapshot_meta(DATASET_ID, SCOPE_ALL)

        # Collapse raw rows -> DISTINCT events (Phase API-1.9, see module
        # docstring's "Multi-Record Parcel Semantics"). `raw_matches` is
        # already sorted deterministically by lookup_expropriation_
        # matches() (full row tuple) -- de-duplicating in that same order
        # via a seen-set preserves determinism rather than introducing a
        # new ordering step.
        raw_match_count = len(raw_matches)
        distinct_events = []
        seen_signatures = set()
        for row in raw_matches:
            signature = (row.get("sus_year"), row.get("pro_name"))
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                distinct_events.append(row)
        distinct_event_count = len(distinct_events)

        if distinct_event_count == 0:
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.NOT_FOUND_IN_DATASET,
                district=district, segment=section_name, land_no=normalized_id,
                match_count=0, matches=[], raw_match_count=0, distinct_event_count=0,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
                source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
                retrieved_at=retrieved_at, query_parameters=query_parameters,
                authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
                confidence="中", requires_manual_review=True,
                local_snapshot_version=meta.get("downloaded_at") if meta else None,
                checksum=meta.get("checksum_sha256") if meta else None,
                downloaded_at=meta.get("downloaded_at") if meta else None,
                record_count=meta.get("record_count") if meta else None,
                schema_version=meta.get("schema_version") if meta else None,
                notes=(
                    "此地號於官方「已公告徵收案件地籍資料」本地snapshot（已完整同步全資料集）中查無相符紀錄。"
                    "此資料集僅涵蓋92年以後一般徵收案件（不含更正及撤銷徵收、不含區段徵收），"
                    "查無紀錄不代表此地號確定未被徵收，仍需人工覆核。"
                ),
            )

        matches = [
            ExpropriationCaseMatch(
                announcement_year=row.get("sus_year"), project_name=row.get("pro_name"),
                district=district, segment=section_name, land_no=normalized_id,
            )
            for row in distinct_events
        ]

        if distinct_event_count == 1:
            announcement_year = distinct_events[0].get("sus_year")
            project_name = distinct_events[0].get("pro_name")
            notes = (
                "此地號於官方「已公告徵收案件地籍資料」本地snapshot中有相符紀錄，"
                "但本資料集僅涵蓋92年以後一般徵收案件（不含更正及撤銷徵收、不含區段徵收），"
                "此結果僅供人工覆核之佐證，不構成最終法律結論。"
            )
            if raw_match_count > 1:
                notes += (
                    f"（原始snapshot中此地號有{raw_match_count}筆逐欄位完全相同之重複列，"
                    "經去重後判定為同一真實事件，非多筆獨立徵收案件。）"
                )
        else:
            # match_count > 1: NEVER silently narrow to first/latest/
            # highest-year -- see ExpropriationCaseEvidence's docstring.
            announcement_year = None
            project_name = None
            notes = (
                f"此地號於官方「已公告徵收案件地籍資料」本地snapshot中有{distinct_event_count}筆"
                "內容不同之徵收事件紀錄（不同年度及/或不同工程計畫），可能為同一地籍分次被不同"
                "計畫徵收。announcement_year/project_name因無法代表單一事件而刻意留空，"
                "請見matches欄位取得全部事件明細；本資料集僅涵蓋92年以後一般徵收案件"
                "（不含更正及撤銷徵收、不含區段徵收），仍需人工覆核確認與本案關聯性。"
            )

        return ExpropriationCaseEvidence(
            status=ExpropriationCaseStatus.FOUND_IN_DATASET,
            district=district, segment=section_name, land_no=normalized_id,
            announcement_year=announcement_year, project_name=project_name,
            match_count=distinct_event_count, matches=matches,
            raw_match_count=raw_match_count, distinct_event_count=distinct_event_count,
            dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
            source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
            retrieved_at=retrieved_at, query_parameters=query_parameters,
            authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
            confidence="中", requires_manual_review=True,
            local_snapshot_version=meta.get("downloaded_at") if meta else None,
            checksum=meta.get("checksum_sha256") if meta else None,
            downloaded_at=meta.get("downloaded_at") if meta else None,
            record_count=meta.get("record_count") if meta else None,
            schema_version=meta.get("schema_version") if meta else None,
            notes=notes,
        )

    def query_case_via_live_scan(self, ctx: ProviderContext, section_name: str, land_no: str) -> ExpropriationCaseEvidence:
        """FALLBACK, opt-in only -- NEVER called automatically by
        `query_case`/`fetch` (Phase API-1.5's instructions explicitly
        forbid this bounded live-pagination approach from being the primary
        strategy, since segment/id filters are server-side no-ops and a
        district can exceed the scan bound -- see this module's original
        Phase API-1 docstring, preserved in git-adjacent history/docs/
        phase9/api_integration_spec.md). Caller must supply the ALREADY-
        PARSED section_name/land_no explicitly (never ctx.segment_code)."""
        district = ctx.district
        retrieved_at = datetime.now(timezone.utc)
        query_parameters = {
            "server_filter.district": district or "",
            "client_match.segment": section_name or "", "client_match.id": land_no or "",
        }

        if not district or not section_name or not land_no:
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.UNKNOWN,
                district=district, segment=section_name, land_no=land_no,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
                source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
                retrieved_at=retrieved_at, query_parameters=query_parameters,
                confidence="UNKNOWN", requires_manual_review=True,
                notes="缺少district/section_name/land_no，無法查詢官方徵收案件資料。",
            )

        outcome, row = _scan_district(district, section_name, land_no)

        if outcome == "error":
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.UNKNOWN, district=district, segment=section_name, land_no=land_no,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME, source_authority=SOURCE_AUTHORITY,
                source_url=OFFICIAL_PAGE_URL, retrieved_at=retrieved_at, query_parameters=query_parameters,
                authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
                confidence="UNKNOWN", requires_manual_review=True,
                notes="呼叫新北市OpenAPI失敗或逾時，無法確認查詢結果，非查無資料。",
            )
        if outcome == "truncated":
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.UNKNOWN, district=district, segment=section_name, land_no=land_no,
                dataset_id=DATASET_ID, dataset_name=DATASET_NAME, source_authority=SOURCE_AUTHORITY,
                source_url=OFFICIAL_PAGE_URL, retrieved_at=retrieved_at, query_parameters=query_parameters,
                authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"「{district}」之官方徵收案件紀錄筆數超過本Provider之掃描上限"
                    f"（{_PAGE_SIZE}筆×{_MAX_PAGES}頁），無法確認是否已涵蓋此地號，判定為無法確認。"
                    "（此為live-scan備援路徑，正式路徑請執行sync腳本建立完整snapshot。）"
                ),
            )
        if outcome == "found":
            return ExpropriationCaseEvidence(
                status=ExpropriationCaseStatus.FOUND_IN_DATASET, district=district, segment=section_name,
                land_no=land_no, announcement_year=str(row.get("sus_year")) if row.get("sus_year") is not None else None,
                project_name=row.get("pro_name"), dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
                source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
                retrieved_at=retrieved_at, query_parameters=query_parameters,
                authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
                confidence="中", requires_manual_review=True,
                notes="（live-scan備援路徑）此地號於官方資料中有相符紀錄，僅供人工覆核之佐證。",
            )
        return ExpropriationCaseEvidence(
            status=ExpropriationCaseStatus.NOT_FOUND_IN_DATASET, district=district, segment=section_name,
            land_no=land_no, dataset_id=DATASET_ID, dataset_name=DATASET_NAME,
            source_authority=SOURCE_AUTHORITY, source_url=OFFICIAL_PAGE_URL,
            retrieved_at=retrieved_at, query_parameters=query_parameters,
            authoritative_status="OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE",
            confidence="中", requires_manual_review=True,
            notes="（live-scan備援路徑）查無相符紀錄，不代表確定未被徵收，仍需人工覆核。",
        )
