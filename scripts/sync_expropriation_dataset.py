# -*- coding: utf-8 -*-
"""
Out-of-band ETL job: full sync of 新北市已公告徵收案件地籍資料
(dataset_id DD9C0006-8FAD-450D-8C7B-E59240B0ED13, ~9,437 rows city-wide)
into providers/cadastral_dataset_cache.py's local SQLite store, so
RealExpropriationCaseProvider can answer from a local, exact, complete
index instead of Phase API-1's bounded (and frequently truncating,
therefore UNKNOWN-prone) live pagination scan.

A FULL sync is feasible for this dataset specifically because it is small
(~9,437 rows total, unlike the ~1.15M-row land-price dataset -- see
scripts/sync_land_price_dataset.py's module docstring for why THAT one is
synced per-district instead). Verified live during this round: the
platform's `filter` query parameter only honors `district eq ...` (see
providers/expropriation_case_provider.py's module docstring) -- but
OMITTING `filter` entirely still returns the whole table, paginated in a
stable order, so this script does not need to loop over a hardcoded list
of district names; it just pages through everything until a short page
signals the end.

This is a manually-triggered maintenance job -- never run inside a Lambda
request/response path. Run it once; RealExpropriationCaseProvider then
reads whatever providers/cadastral_dataset_cache.py currently has synced.

ATOMIC STAGING CONTRACT (Phase API-1.8): this script already only ever
wrote to the CURRENT snapshot ONCE, at the very end, after the full crawl
completed in memory -- so it never had Phase API-1.7's land-price
checkpoint-writes-partial-data-to-CURRENT bug in the first place (a crash
mid-crawl here always left CURRENT completely untouched, at the cost of
losing all in-memory progress and needing a full from-scratch rerun --
acceptable given this dataset is only ~9,437 rows / ~48 requests). It is
migrated to the same stage-then-promote path as
scripts/sync_land_price_dataset.py anyway, purely for system-wide
consistency of the "Provider can only ever read a fully-validated CURRENT
snapshot" invariant (see providers/cadastral_dataset_cache.py's module
docstring) -- not because this script had a bug to fix.

DUPLICATE KEY FINDING (Phase API-1.8, discovered LIVE while migrating this
script): (district, segment, land_no) is NOT a unique key in this
dataset -- a real, live full crawl found 441 distinct (district,segment,
id) collisions, each representing the SAME cadastral parcel expropriated
under MULTIPLE separate government projects in different years (e.g.
板橋區/江子翠段第一崁小段/10067 appears under three distinct 2005-2006
road-widening projects with different `sus_year`/`pro_name`). This is
real, legitimate government data, not a corruption signal -- unlike the
land-price dataset's (district,segment,lid), which IS empirically unique
(0 duplicates across all 1,153,450 real rows, verified Phase API-1.7/1.8).
Promoting therefore passes `enforce_duplicate_check=False` here (the
duplicate count is still computed and logged for visibility, just not
treated as a promotion-blocking integrity failure). See
docs/backlog.md::EXPROPRIATION_KEY_NOT_UNIQUE_MULTI_PROJECT_PARCELS for
the corresponding (out-of-scope for this round) Provider-level gap this
surfaces: RealExpropriationCaseProvider.query_case()'s current lookup
returns only ONE of potentially several real matching records when more
than one exists for the same (district,segment,land_no).

Usage:
    py scripts/sync_expropriation_dataset.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "providers"))

from cadastral_dataset_cache import CadastralDatasetCache, SCOPE_ALL  # noqa: E402

DATASET_ID = "DD9C0006-8FAD-450D-8C7B-E59240B0ED13"
DATASET_NAME = "新北市已公告徵收案件地籍資料"
SOURCE_AUTHORITY = "新北市政府地政局"
DATASET_URL = f"https://data.ntpc.gov.tw/api/datasets/{DATASET_ID}/json"
OFFICIAL_PAGE_URL = f"https://data.ntpc.gov.tw/datasets/{DATASET_ID}"

PAGE_SIZE = 200
REQUEST_TIMEOUT_S = 10.0
INTER_PAGE_DELAY_S = 0.2  # be a polite client against a public government API


def _fetch_page(page: int) -> list:
    params = {"size": str(PAGE_SIZE), "page": str(page)}
    url = f"{DATASET_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ai-valuation-review-competition-tool/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} at page {page}")
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, list):
        raise RuntimeError(f"Unexpected non-list response shape at page {page}")
    return body


def main() -> int:
    cache = CadastralDatasetCache()
    cache.begin_staging_run(DATASET_ID, note="sync_expropriation_dataset.py run started")
    try:
        all_rows = []
        page = 0
        while True:
            rows = _fetch_page(page)
            all_rows.extend(rows)
            print(f"page {page}: +{len(rows)} rows (total so far: {len(all_rows)})")
            if len(rows) < PAGE_SIZE:
                break
            page += 1
            time.sleep(INTER_PAGE_DELAY_S)

        cache.stage_expropriation_records(DATASET_ID, SCOPE_ALL, records=all_rows)
        # enforce_duplicate_check=False: (district,segment,land_no) is NOT
        # a unique key in this dataset -- see module docstring's "DUPLICATE
        # KEY FINDING" for the live-verified reason (441 real collisions,
        # each a genuine multi-project parcel, not corruption).
        result = cache.promote_expropriation_staging_to_current(
            dataset_id=DATASET_ID, dataset_name=DATASET_NAME, source_url=OFFICIAL_PAGE_URL,
            source_authority=SOURCE_AUTHORITY, scope_keys=[SCOPE_ALL],
            enforce_duplicate_check=False,
        )
        promoted = result["promoted"][0]
        print(f"\nPromote完成：record_count={promoted['record_count']} "
              f"checksum_sha256={promoted['checksum_sha256']} "
              f"checksum_algorithm={result['checksum_algorithm']} "
              f"duplicate_key_count={promoted['duplicate_key_count']}"
              f"（非promote失敗條件，見module docstring說明）")

        # Phase API-1.9: formal PARCEL_MULTI_EVENT vs EXACT_SOURCE_DUPLICATE
        # breakdown (raw duplicate_key_count above conflates the two --
        # this audit distinguishes them, see providers/cadastral_dataset_
        # cache.py::audit_expropriation_parcel_multiplicity's docstring and
        # providers/expropriation_case_provider.py's module docstring for
        # the definitions). Read-only, does not affect promote/CURRENT.
        audit = cache.audit_expropriation_parcel_multiplicity(DATASET_ID, SCOPE_ALL)
        print(
            f"\n多筆事件/重複列 Audit：\n"
            f"  TOTAL_ROWS = {audit['total_rows']}\n"
            f"  UNIQUE_PARCEL_KEYS = {audit['unique_parcel_keys']}\n"
            f"  MULTI_RECORD_PARCEL_KEYS = {audit['multi_record_parcel_keys']}\n"
            f"  MAX_RECORDS_PER_PARCEL = {audit['max_records_per_parcel']}\n"
            f"  PARCEL_MULTI_EVENT_KEYS = {audit['parcel_multi_event_keys']}"
            f"（同parcel、內容不同之真實多事件——合法官方資料，非錯誤）\n"
            f"  EXACT_SOURCE_DUPLICATE_KEYS = {audit['exact_source_duplicate_only_keys']}"
            f"（同parcel、全部rows逐欄位相同——來源資料重複列，非多事件）\n"
            f"  EXACT_DUPLICATE_FULL_ROWS = {audit['exact_duplicate_full_rows']}"
            f"（重複列之raw row數，非parcel數）"
        )
        return 0
    except Exception as e:
        cache.mark_staging_failed(DATASET_ID, note=str(e))
        print(f"\n同步失敗：{e}")
        print("CURRENT snapshot完全未變更（維持上一版本已驗證完成之資料）。")
        raise


if __name__ == "__main__":
    sys.exit(main())
