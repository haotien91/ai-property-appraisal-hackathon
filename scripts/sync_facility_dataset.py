# -*- coding: utf-8 -*-
"""
Out-of-band ETL job: full sync of Phase API-2's official facility datasets
into providers/facility_dataset_cache.py's local SQLite store, so
RealOfficialFacilityProvider can answer from a local, complete index
instead of depending on data.ntpc.gov.tw being reachable/fast at request
time (this round's explicit "Demo需要穩定,不要讓Demo強依賴政府API即時
可用" instruction).

Three source datasets, one sync run each (all small enough for a single
filter-free full-table fetch -- see providers/official_facility_provider.py's
module docstring for the live audit that found each of their exact sizes
and schemas):

1. 新北市重要地標資訊 (LANDMARK_DATASET_ID, 2,056 rows, daily-updated,
   sourced from 新北iMAP) -- covers SCHOOL (國民小學/國民中學/完全中學/
   高中職/大專院校, 472 rows total) and STATION (捷運站/火車站, 142 rows
   total). Coordinates are `twd97_x`/`twd97_y` -- TWD97 TM2 (EPSG:3826),
   NOT WGS84 -- reprojected here via `pyproj`, the SAME library and EPSG
   codes already verified correct in scripts/sync_ntpc_zoning_dataset.py
   (cross-checked during this round's audit against a real, well-known
   campus location, not merely trusted procedurally -- see facility_
   dataset_cache.py's module docstring for that spot-check).

2. 新北市公有市場及超市清冊 (MARKET_DATASET_ID) -- name/address/district
   only, NO coordinate field at all (verified via direct field inspection).
   Stored with coordinate_status=ADDRESS_ONLY, latitude/longitude=NULL --
   never geocoded (this round's explicit "不要偷偷Nominatim geocode" rule).

3. 新北市公園 (PARK_DATASET_ID) -- same shape, same ADDRESS_ONLY treatment.

`filter` query-parameter: CONFIRMED complete no-op on all three datasets
(same NTPC_SERVER_FILTER_TRUST_POLICY = UNTRUSTED_UNLESS_VERIFIED_PER_
DATASET this project has applied since Phase API-1) -- this script never
sends `filter`, always full-table fetch + client-side matching.

Each dataset gets its own begin_staging_run() -> stage_facility_records()
-> promote_facility_staging_to_current() cycle (Atomic Staging Contract,
same principle as the frozen cadastral pipeline's Phase API-1.8 design,
reapplied here to facility_dataset_cache.py's own, separate tables -- see
that module's docstring for why it is a separate store rather than an
extension of the frozen one). A failure on any one dataset leaves that
dataset's CURRENT snapshot exactly as it was; it does not affect the other
two datasets' snapshots (independent scope_key namespaces via disjoint
dataset_id values).

This is a manually-triggered maintenance job -- never run inside a Lambda
request/response path.

Usage:
    py scripts/sync_facility_dataset.py
    py scripts/sync_facility_dataset.py --dataset landmark   # sync only one
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import pyproj

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "providers"))

from facility_dataset_cache import FacilityDatasetCache, SCOPE_ALL  # noqa: E402
from official_facility_provider import (  # noqa: E402
    LANDMARK_DATASET_ID, LANDMARK_DATASET_NAME, LANDMARK_SOURCE_URL, SOURCE_AUTHORITY_LANDMARK,
    MARKET_DATASET_ID, MARKET_DATASET_NAME, MARKET_SOURCE_URL, SOURCE_AUTHORITY_MARKET,
    PARK_DATASET_ID, PARK_DATASET_NAME, PARK_SOURCE_URL, SOURCE_AUTHORITY_PARK,
)
from domain.models import FacilityType, FacilityCoordinateStatus  # noqa: E402

REQUEST_TIMEOUT_S = 30.0
FETCH_SIZE = 50_000  # every source dataset here is well under this (largest: 2,056 rows)

SOURCE_EPSG = "EPSG:3826"   # TWD97 TM2 -- verified via facility_dataset_cache.py's spot-check
TARGET_EPSG = "EPSG:4326"   # WGS84

_LANDMARK_TYPE_TO_FACILITY_TYPE = {
    "國民小學": FacilityType.SCHOOL, "國民中學": FacilityType.SCHOOL,
    "完全中學": FacilityType.SCHOOL, "高中職": FacilityType.SCHOOL, "大專院校": FacilityType.SCHOOL,
    "捷運站": FacilityType.STATION, "火車站": FacilityType.STATION,
}

# Phase API-2.1 §10: this project's own coarser subtype bucket per source
# category -- `source_category` (below) ALWAYS carries the verbatim
# original string regardless of whether a bucket is defined here.
_LANDMARK_TYPE_TO_SUBTYPE = {
    "國民小學": "ELEMENTARY", "國民中學": "JUNIOR_HIGH", "完全中學": "COMPLETE_SCHOOL",
    "高中職": "SENIOR_HIGH", "大專院校": "COLLEGE",
    # "捷運站" bucketed as "MRT" is a COARSE label -- the source dataset
    # itself does NOT separately categorize 輕軌 (light rail, e.g. 淡海輕軌/
    # 安坑輕軌) from heavy-rail MRT; both share the single "捷運站" landmark
    # type (verified live: 23 rows with "輕軌" in their own name string, all
    # tagged 地標類型=捷運站). This means light rail IS present in the data
    # (better coverage than "no 輕軌 category" alone would suggest), but
    # cannot be distinguished from MRT proper without pattern-matching the
    # NAME string -- a heuristic this project does not apply, since
    # `source_category` (always the verbatim "捷運站") already preserves
    # the one distinction the SOURCE actually makes; a reader needing the
    # MRT/light-rail split can inspect `name` themselves.
    "捷運站": "MRT", "火車站": "TRA",
    # No 高鐵/HSR or 客運/BUS_TERMINAL bucket exists here -- Phase API-2.1
    # §9's audit of this dataset's full 29 landmark categories found neither
    # present, and no other NTPC OpenData dataset covers them either (see
    # docs/phase9/facility_evidence_pipeline.md's Phase API-2.1 addendum).
    # STATION_DATA_READY is reported as PARTIAL, not YES, because of this
    # gap -- adding a bucket here would not manufacture data that does not
    # exist.
}


def _fetch_json(dataset_id: str) -> list:
    url = f"https://data.ntpc.gov.tw/api/datasets/{dataset_id}/json?" + urllib.parse.urlencode(
        {"size": str(FETCH_SIZE), "page": "0"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "ai-valuation-review-competition-tool/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for dataset {dataset_id}")
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, list):
        raise RuntimeError(f"Unexpected non-list response shape for dataset {dataset_id}")
    return body


def _sync_landmark(cache: FacilityDatasetCache) -> None:
    cache.begin_staging_run(LANDMARK_DATASET_ID, note="sync_facility_dataset.py: landmark run started")
    try:
        rows = _fetch_json(LANDMARK_DATASET_ID)
        print(f"landmark: fetched {len(rows)} rows")
        transformer = pyproj.Transformer.from_crs(SOURCE_EPSG, TARGET_EPSG, always_xy=True)

        records = []
        skipped_unmapped_type = 0
        skipped_bad_coordinate = 0
        for row in rows:
            landmark_type = row.get("地標類型")
            facility_type = _LANDMARK_TYPE_TO_FACILITY_TYPE.get(landmark_type)
            if facility_type is None:
                skipped_unmapped_type += 1
                continue
            try:
                twd97_x = float(row.get("twd97_x"))
                twd97_y = float(row.get("twd97_y"))
            except (TypeError, ValueError):
                skipped_bad_coordinate += 1
                continue
            lon, lat = transformer.transform(twd97_x, twd97_y)
            records.append({
                "facility_type": facility_type.value,
                "facility_id": f"landmark-{row.get('objectid')}",
                "district": row.get("行政區"),
                "name": row.get("地標名稱"),
                "address": row.get("地址"),
                "latitude": lat,
                "longitude": lon,
                "coordinate_status": FacilityCoordinateStatus.WGS84.value,
                "source_dataset_id": LANDMARK_DATASET_ID,
                "source_authority": SOURCE_AUTHORITY_LANDMARK,
                "facility_subtype": _LANDMARK_TYPE_TO_SUBTYPE.get(landmark_type),
                "source_category": landmark_type,
            })
        skipped_total = skipped_unmapped_type + skipped_bad_coordinate
        print(f"landmark: mapped {len(records)} rows to SCHOOL/STATION "
              f"(skipped {skipped_unmapped_type} unmapped-type, {skipped_bad_coordinate} bad-coordinate)")
        if not records:
            raise RuntimeError("landmark: 0 rows mapped to SCHOOL/STATION -- refusing to promote an empty snapshot")

        cache.stage_facility_records(LANDMARK_DATASET_ID, SCOPE_ALL, records)
        result = cache.promote_facility_staging_to_current(
            dataset_id=LANDMARK_DATASET_ID, dataset_name=LANDMARK_DATASET_NAME,
            source_url=LANDMARK_SOURCE_URL, source_authority=SOURCE_AUTHORITY_LANDMARK,
            scope_keys=[SCOPE_ALL],
            source_record_count=len(rows), mapped_record_count=len(records), skipped_record_count=skipped_total,
        )
        print(f"landmark: promote完成 source_record_count={len(rows)} "
              f"mapped_record_count={len(records)} skipped_record_count={skipped_total} "
              f"total_record_count={result['total_record_count']}")
    except Exception as e:
        cache.mark_staging_failed(LANDMARK_DATASET_ID, note=str(e))
        print(f"landmark: 同步失敗：{e}（CURRENT snapshot未變更）")
        raise


def _sync_address_only(cache: FacilityDatasetCache, *, dataset_id: str, dataset_name: str, source_url: str,
                        source_authority: str, facility_type: FacilityType, id_field: str, name_field: str,
                        address_field: str, district_field: str, category_field: Optional[str] = None) -> None:
    cache.begin_staging_run(dataset_id, note=f"sync_facility_dataset.py: {dataset_name} run started")
    try:
        rows = _fetch_json(dataset_id)
        print(f"{dataset_name}: fetched {len(rows)} rows")
        records = [
            {
                "facility_type": facility_type.value,
                "facility_id": f"{facility_type.value.lower()}-{row.get(id_field)}",
                "district": row.get(district_field),
                "name": row.get(name_field),
                "address": row.get(address_field),
                "latitude": None, "longitude": None,
                "coordinate_status": FacilityCoordinateStatus.ADDRESS_ONLY.value,
                "source_dataset_id": dataset_id,
                "source_authority": source_authority,
                "facility_subtype": None,
                "source_category": row.get(category_field) if category_field else None,
            }
            for row in rows
        ]
        if not records:
            raise RuntimeError(f"{dataset_name}: 0 rows fetched -- refusing to promote an empty snapshot")

        cache.stage_facility_records(dataset_id, SCOPE_ALL, records)
        result = cache.promote_facility_staging_to_current(
            dataset_id=dataset_id, dataset_name=dataset_name, source_url=source_url,
            source_authority=source_authority, scope_keys=[SCOPE_ALL],
            source_record_count=len(rows), mapped_record_count=len(records), skipped_record_count=0,
        )
        print(f"{dataset_name}: promote完成（ADDRESS_ONLY，無座標）"
              f"source_record_count={len(rows)} total_record_count={result['total_record_count']}")
    except Exception as e:
        cache.mark_staging_failed(dataset_id, note=str(e))
        print(f"{dataset_name}: 同步失敗：{e}（CURRENT snapshot未變更）")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["landmark", "market", "park", "all"], default="all")
    args = parser.parse_args()

    cache = FacilityDatasetCache()
    if args.dataset in ("landmark", "all"):
        _sync_landmark(cache)
    if args.dataset in ("market", "all"):
        _sync_address_only(
            cache, dataset_id=MARKET_DATASET_ID, dataset_name=MARKET_DATASET_NAME,
            source_url=MARKET_SOURCE_URL, source_authority=SOURCE_AUTHORITY_MARKET,
            facility_type=FacilityType.MARKET, id_field="item", name_field="name",
            address_field="address", district_field="town", category_field="types",
        )
    if args.dataset in ("park", "all"):
        _sync_address_only(
            cache, dataset_id=PARK_DATASET_ID, dataset_name=PARK_DATASET_NAME,
            source_url=PARK_SOURCE_URL, source_authority=SOURCE_AUTHORITY_PARK,
            facility_type=FacilityType.PARK, id_field="seqno", name_field="name",
            address_field="address", district_field="area", category_field=None,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
