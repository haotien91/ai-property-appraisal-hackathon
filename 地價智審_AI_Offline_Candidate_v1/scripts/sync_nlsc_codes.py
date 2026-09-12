# -*- coding: utf-8 -*-
"""
Out-of-band ETL job: full sync of NLSC's three CONFIRMED-OPEN, no-auth code
services (Phase API-2.2R2 live audit) into providers/nlsc_code_cache.py's
local SQLite store, so NlscCadastralCodeResolver can answer from a local
snapshot instead of calling data.nlsc.gov.tw per case (this round's
explicit "不要讓每一筆案件都runtime call NLSC" instruction).

Three services, all GET, zero authentication (verified live, Phase
API-2.2R2 -- see docs/phase9/official_parcel_coordinate_audit.md §S3-S5):
  - ListCounty (COM_003): https://api.nlsc.gov.tw/other/ListCounty
    -- 1 request, ~21 counties.
  - ListTown (COM_004): https://api.nlsc.gov.tw/other/ListTown/{county_code}
    -- 1 request per county (~21 requests total, synced for ALL counties
    so a future case in any city can resolve its district, not just 新北市).
  - ListLandSection (COM_006): https://api.nlsc.gov.tw/other/ListLandSection/
    {county_code}/{town_code} -- 1 request per (county, town). Synced ONLY
    for 新北市's 29 towns this round (matching this entire project's
    established New Taipei City scope -- syncing every town in Taiwan
    would be several hundred additional live requests for no benefit this
    round's Golden Case and known use cases need; this is a documented,
    deliberate scope decision, not a silent omission -- see providers/
    nlsc_code_cache.py's module docstring).

Total live requests this round: 1 (counties) + ~21 (towns per county) +
29 (New Taipei sections) ~= 51, all against confirmed-open endpoints.

Each resource (counties/towns/sections) uses its own begin_staging_run() ->
stage_*() -> promote_*() cycle (Atomic Staging Contract, same principle as
the frozen cadastral pipeline's Phase API-1.8 design, reapplied to nlsc_
code_cache.py's own separate tables). A failure on any one resource leaves
that resource's CURRENT table exactly as it was.

This is a manually-triggered maintenance job -- never run inside a Lambda
request/response path.

Usage:
    py scripts/sync_nlsc_codes.py
    py scripts/sync_nlsc_codes.py --resource counties   # sync only one
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "providers"))

from nlsc_code_cache import NlscCodeCache  # noqa: E402

LIST_COUNTY_URL = "https://api.nlsc.gov.tw/other/ListCounty"
LIST_TOWN_URL = "https://api.nlsc.gov.tw/other/ListTown"
LIST_LAND_SECTION_URL = "https://api.nlsc.gov.tw/other/ListLandSection"

REQUEST_TIMEOUT_S = 15.0
INTER_REQUEST_DELAY_S = 0.2  # polite pacing against a public government API

NEW_TAIPEI_COUNTY_CODE = "F"  # confirmed live, Phase API-2.2R2


def _fetch_xml(url: str) -> ET.Element:
    req = urllib.request.Request(url, headers={"User-Agent": "ai-valuation-review-competition-tool/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        body = resp.read()
    return ET.fromstring(body)


def _sync_counties(cache: NlscCodeCache) -> list:
    cache.begin_staging_run("counties", note="sync_nlsc_codes.py: counties run started")
    try:
        root = _fetch_xml(LIST_COUNTY_URL)
        records = [
            {"county_code": item.findtext("countycode"), "county_name": item.findtext("countyname")}
            for item in root.findall("countyItem")
        ]
        if not records:
            raise RuntimeError("ListCounty: 0 rows fetched -- refusing to promote an empty snapshot")
        cache.stage_counties(records)
        result = cache.promote_counties(source_service="ListCounty", source_url=LIST_COUNTY_URL)
        print(f"counties: promote完成 record_count={result['record_count']}")
        return records
    except Exception as e:
        cache.mark_staging_failed("counties", note=str(e))
        print(f"counties: 同步失敗：{e}（CURRENT未變更）")
        raise


def _sync_towns(cache: NlscCodeCache, counties: list) -> None:
    cache.begin_staging_run("towns", note="sync_nlsc_codes.py: towns run started")
    try:
        for county in counties:
            county_code = county["county_code"]
            root = _fetch_xml(f"{LIST_TOWN_URL}/{county_code}")
            records = [
                {"town_code": item.findtext("towncode"), "town_name": item.findtext("townname")}
                for item in root.findall("townItem")
            ]
            cache.stage_towns(county_code, records)
            print(f"towns[{county_code}]: staged {len(records)} rows")
            time.sleep(INTER_REQUEST_DELAY_S)
        result = cache.promote_towns(source_service="ListTown", source_url=LIST_TOWN_URL)
        print(f"towns: promote完成 record_count={result['record_count']}")
    except Exception as e:
        cache.mark_staging_failed("towns", note=str(e))
        print(f"towns: 同步失敗：{e}（CURRENT未變更）")
        raise


def _sync_sections_for_new_taipei(cache: NlscCodeCache) -> None:
    cache.begin_staging_run("sections", note="sync_nlsc_codes.py: sections (New Taipei only) run started")
    try:
        root = _fetch_xml(f"{LIST_TOWN_URL}/{NEW_TAIPEI_COUNTY_CODE}")
        towns = [item.findtext("towncode") for item in root.findall("townItem")]
        for town_code in towns:
            sect_root = _fetch_xml(f"{LIST_LAND_SECTION_URL}/{NEW_TAIPEI_COUNTY_CODE}/{town_code}")
            records = [
                {
                    "office": item.findtext("office"), "section_code": item.findtext("sectcode"),
                    "section_name": item.findtext("sectstr"),
                }
                for item in sect_root.findall("sectItem")
            ]
            cache.stage_sections(NEW_TAIPEI_COUNTY_CODE, town_code, records)
            print(f"sections[{NEW_TAIPEI_COUNTY_CODE}/{town_code}]: staged {len(records)} rows")
            time.sleep(INTER_REQUEST_DELAY_S)
        result = cache.promote_sections(source_service="ListLandSection", source_url=LIST_LAND_SECTION_URL)
        print(f"sections: promote完成（僅新北市29區）record_count={result['record_count']}")
    except Exception as e:
        cache.mark_staging_failed("sections", note=str(e))
        print(f"sections: 同步失敗：{e}（CURRENT未變更）")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", choices=["counties", "towns", "sections", "all"], default="all")
    args = parser.parse_args()

    cache = NlscCodeCache()
    counties = None
    if args.resource in ("counties", "towns", "all"):
        counties = _sync_counties(cache)
    if args.resource in ("towns", "all"):
        if counties is None:
            counties = cache.get_current_counties()
        _sync_towns(cache, counties)
    if args.resource in ("sections", "all"):
        _sync_sections_for_new_taipei(cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
