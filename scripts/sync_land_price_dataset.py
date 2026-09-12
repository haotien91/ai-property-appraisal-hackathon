# -*- coding: utf-8 -*-
"""
Out-of-band ETL job: full sync of 新北市公告土地現值 (114年 dataset_id
826870ef-4ea5-48bf-915b-e0a33158cf06, ~1.15M rows city-wide) into
providers/cadastral_dataset_cache.py's local SQLite store, bucketed by
each row's OWN `district` field into one snapshot per district.

CORRECTED ARCHITECTURE (Phase API-1.5): `filter=district eq {district}` is
a COMPLETE no-op on this dataset (see providers/expropriation_case_
provider.py's module docstring for the proof) -- this script does exactly
ONE full, filter-free pass over the whole dataset and buckets every row
into its OWN `district` field's snapshot.

LARGE-PAGE MIGRATION (Phase API-1.7): PAGE_SIZE=500,000 (down from 200,
~5,750 requests), verified equivalent to the PAGE_SIZE=200 crawl via
scratchpad/verify_large_page.py's key-set comparison against the
then-existing full snapshot.

ATOMIC STAGING CONTRACT (Phase API-1.8): Phase API-1.7's checkpoint logic
called `write_land_price_snapshot()` directly -- writing PARTIAL,
mid-crawl data straight into the authoritative CURRENT snapshot tables
that `RealLandPriceProvider` reads from. A network interruption or
validation failure between checkpoints therefore risked leaving a
PARTIAL result indistinguishable from a COMPLETE one to any Provider
querying it afterward -- a correctness risk, not just a resilience one.

This version instead follows:

    Official API -> download -> STAGING -> validation -> [ALL PASS] ->
    atomic promote -> CURRENT

Every checkpoint below calls `cache.stage_land_price_records()` (STAGING
only -- see providers/cadastral_dataset_cache.py's module docstring for
why Provider lookups can never see this). CURRENT is touched EXACTLY ONCE
per run, via `cache.promote_land_price_staging_to_current()`, and ONLY
after the full crawl finishes AND both integrity checks below (duplicate
key, total record count) pass. Any exception anywhere in the crawl --
network interruption, malformed response, a failed integrity check --
routes to `cache.mark_staging_failed()` instead, which touches only
diagnostic bookkeeping; CURRENT is left holding whatever the previous
successful run promoted (Last-Known-Good), for every district, not a mix
of old-for-some/new-for-others (`promote_land_price_staging_to_current()`
is a single sqlite3 transaction across every district it is asked to
promote -- see that method's docstring for why this makes partial
promotion structurally impossible, not just unlikely).

This is a manually-triggered maintenance job -- never run inside a Lambda
request/response path.

Usage:
    py scripts/sync_land_price_dataset.py
    py scripts/sync_land_price_dataset.py --max-pages 1   # bounded partial run for local testing only
                                                            # (stages but NEVER promotes)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "providers"))

from cadastral_dataset_cache import CadastralDatasetCache  # noqa: E402

SOURCE_AUTHORITY = "新北市政府地政局"
DATASET_ID = "826870ef-4ea5-48bf-915b-e0a33158cf06"
DATASET_NAME = "新北市114年公告土地現值"
DATASET_YEAR = "114"
API_URL = f"https://data.ntpc.gov.tw/api/datasets/{DATASET_ID}/json"
SOURCE_URL = f"https://data.ntpc.gov.tw/datasets/{DATASET_ID}"

# Verified safe in Phase API-1.7 (scratchpad/verify_large_page.py): size=
# 500000 succeeds in ~24s/request; size>=~1,200,000 was observed to trigger
# a WAF rejection. 500000 is the verified value, not an assumed maximum.
PAGE_SIZE = 500_000
REQUEST_TIMEOUT_S = 120.0
INTER_PAGE_DELAY_S = 0.5
RETRIES_PER_PAGE = 3
RETRY_BACKOFF_S = 5.0
CHECKPOINT_EVERY_PAGES = 1  # stage (never promote) after every page

# Last known-good full-sync total (Phase API-1.6's completed 29-district
# sync). This is a DIVERGENCE SIGNAL, not a hard equality gate -- the
# government dataset can legitimately grow/shrink between announcement
# cycles, so the check only aborts on a large unexplained drop
# (symptomatic of a truncated/short response being silently accepted),
# never on exact mismatch.
EXPECTED_TOTAL_RECORD_COUNT_HINT = 1_153_450
_TOTAL_COUNT_DROP_ABORT_THRESHOLD = 0.5  # abort if actual < 50% of hint


class SyncIntegrityError(RuntimeError):
    """Raised for a validation failure (duplicate key / total count) that
    must prevent promotion -- distinct from a network/transport error only
    in that it happens AFTER a structurally complete crawl."""


def _fetch_page(page: int) -> list:
    # No `filter` param at all -- confirmed to have zero effect either way
    # (see module docstring), so omitting it is equally correct and avoids
    # implying a scoping that does not happen.
    params = {"size": str(PAGE_SIZE), "page": str(page)}
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ai-valuation-review-competition-tool/1.0"})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} at page {page}")
                body = json.loads(resp.read().decode("utf-8"))
            if not isinstance(body, list):
                raise RuntimeError(f"Unexpected non-list response shape at page {page}")
            return body
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            attempt += 1
            if attempt > RETRIES_PER_PAGE:
                raise
            print(f"  page {page}: transient error ({e}), retry {attempt}/{RETRIES_PER_PAGE}...")
            time.sleep(RETRY_BACKOFF_S)


def _validate_no_duplicate_keys(by_district: dict) -> int:
    seen = set()
    duplicates = 0
    for district, records in by_district.items():
        for r in records:
            key = (district, r.get("segment"), r.get("lid"))
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
    return duplicates


def _validate_total_count(total: int) -> None:
    if total < EXPECTED_TOTAL_RECORD_COUNT_HINT * _TOTAL_COUNT_DROP_ABORT_THRESHOLD:
        raise SyncIntegrityError(
            f"完整性驗證失敗：總筆數={total} 遠低於已知基準"
            f"={EXPECTED_TOTAL_RECORD_COUNT_HINT}（低於{_TOTAL_COUNT_DROP_ABORT_THRESHOLD:.0%}），"
            f"疑似回應被截斷，中止同步（STAGING保留供診斷，CURRENT snapshot未變更）。"
        )
    diff = total - EXPECTED_TOTAL_RECORD_COUNT_HINT
    print(f"總筆數驗證：實際={total}，已知基準={EXPECTED_TOTAL_RECORD_COUNT_HINT}，差異={diff:+d}"
          f"（差異為政府資料集自然變動之訊號，非硬性相等要求）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="僅供本地測試用之上限頁數；省略則完整同步全資料集（約3頁，每頁至多500000筆）。"
             "使用此參數時，資料僅寫入STAGING，絕不promote至CURRENT（此為PARTIAL執行，"
             "不應也不會覆蓋現有正式snapshot）。",
    )
    args = parser.parse_args()

    cache = CadastralDatasetCache()
    cache.begin_staging_run(DATASET_ID, note="sync_land_price_dataset.py run started")
    by_district = defaultdict(list)
    page = 0
    total = 0
    reached_end = False

    def _stage(label: str):
        print(f"\n[{label}] 寫入STAGING（非CURRENT）：{len(by_district)} 個行政區（累計{total}筆）...")
        for district, records in sorted(by_district.items()):
            cache.stage_land_price_records(DATASET_ID, district, records, dataset_year=DATASET_YEAR)
            print(f"  {district}: staged_count={len(records)}")

    try:
        while True:
            rows = _fetch_page(page)
            total += len(rows)
            for row in rows:
                d = row.get("district")
                if d:
                    by_district[d].append(row)
            print(f"page {page}: rows={len(rows)}, total so far={total}, "
                  f"districts seen so far={len(by_district)}")
            if len(rows) < PAGE_SIZE:
                print(f"page {page}: short page ({len(rows)} rows) -- reached end of dataset.")
                reached_end = True
                break
            page += 1
            if args.max_pages is not None and page >= args.max_pages:
                print(f"達到 --max-pages={args.max_pages} 上限，停止（此為PARTIAL執行，僅寫入STAGING，不promote）。")
                break
            if page % CHECKPOINT_EVERY_PAGES == 0:
                _stage(f"checkpoint@page{page}")
            time.sleep(INTER_PAGE_DELAY_S)

        # Final checkpoint so STAGING reflects everything fetched, whether
        # or not promotion follows.
        _stage("final")

        if not reached_end:
            # Only reachable via --max-pages (an intentional partial test
            # run) -- any OTHER way of not reaching the end is a raised
            # exception, handled below, not a normal loop exit.
            print("此為PARTIAL執行（受--max-pages限制），未涵蓋完整資料集，"
                  "資料保留於STAGING，不promote至CURRENT，正式snapshot維持前一版本不變。")
            return 0

        duplicates = _validate_no_duplicate_keys(by_district)
        if duplicates > 0:
            raise SyncIntegrityError(
                f"完整性驗證失敗：偵測到 {duplicates} 筆重複之 (district,segment,lid) key，"
                f"疑似large-page分頁重疊，中止同步（STAGING保留供診斷，CURRENT snapshot未變更）。"
            )
        print("重複key驗證：duplicates=0（通過）")
        _validate_total_count(total)

        print(f"\n驗證全數通過，執行atomic promote（STAGING -> CURRENT，"
              f"涵蓋{len(by_district)}個行政區，單一交易）...")
        result = cache.promote_land_price_staging_to_current(
            dataset_id=DATASET_ID, dataset_name=DATASET_NAME, dataset_year=DATASET_YEAR,
            source_url=SOURCE_URL, source_authority=SOURCE_AUTHORITY,
            scope_keys=sorted(by_district.keys()),
        )
        for p in result["promoted"]:
            print(f"  [CURRENT] {p['scope_key']}: record_count={p['record_count']} "
                  f"checksum_sha256={p['checksum_sha256'][:16]}...")
        print(f"\nPromote完成：checksum_algorithm={result['checksum_algorithm']}，"
              f"總筆數={result['total_record_count']}，行政區數={len(result['promoted'])}")
        return 0

    except Exception as e:
        cache.mark_staging_failed(DATASET_ID, note=str(e))
        print(f"\n同步失敗：{e}")
        print("CURRENT snapshot完全未變更（維持上一版本已驗證完成之資料）；"
              "已抓取但未promote之資料保留於STAGING供診斷，下次執行"
              "begin_staging_run()時會被清除並重新開始。")
        raise


if __name__ == "__main__":
    sys.exit(main())
