# -*- coding: utf-8 -*-
"""
Out-of-band ETL job: download, verify, reproject, and locally cache 新北市
使用分區 (NTPC zoning shapefile) as a SQLite snapshot that
providers/ntpc_zoning_provider.py's RealNtpcZoningProvider can query
directly via DatasetRegistry.

This is a manually-triggered maintenance job -- NEVER run inside a
Lambda request/response path (the source ZIP is large; verified
78,815,382 bytes as of 2026-09-02). Run it once, then every subsequent
RealNtpcZoningProvider query picks up the new snapshot automatically
through DatasetRegistry.

Usage:
    py scripts/sync_ntpc_zoning_dataset.py
    py scripts/sync_ntpc_zoning_dataset.py --out-dir data/snapshots

Pipeline (facts below were verified live against the real government
source in this session, not assumed -- recorded so the next run doesn't
have to re-discover them, and so this script fails loudly instead of
silently mis-processing data if any of them ever change):

  1. GET the dataset's metadata API (data.ntpc.gov.tw) and find the
     "新北市使用分區" item's `link` -- never hardcode the download URL,
     since the metadata API is the authoritative pointer to it.
  2. URL-encode the (Chinese) filename in that link and download with a
     realistic browser User-Agent. data.ntpc.gov.tw's WAF (F5 BIG-IP
     ASM) rejected the raw UTF-8 filename and a generic/descriptive
     tool User-Agent outright ("Request Rejected") when this was first
     tried live -- a real browser UA string was required.
  3. zipfile.testzip() to catch a truncated/corrupted download before
     touching it further.
  4. Extract, read `.prj`, and refuse to proceed if it does not contain
     the expected TWD97 TM Taiwan (EPSG:3826) marker -- never silently
     assume WGS84 or any other CRS.
  5. pyproj-reproject every polygon from EPSG:3826 to WGS84 (EPSG:4326);
     shapely make_valid() repairs any invalid ring geometry first.
  6. Write one row per polygon into a fresh SQLite `zoning_polygons`
     table (zone_name, plan_name, geometry_wkb, min/max lon/lat bbox
     columns + indexes) -- matches what
     RealNtpcZoningProvider._query_local_snapshot() expects.
  7. Register the new snapshot with DatasetRegistry (checksum computed
     from the finished SQLite file; never registers an unverified or
     partial result).

plan_name backfill (spatial join against "新北市都市計畫範圍"): "新北市使用分區"'s
shapefile has exactly one attribute field, `ZONE` -- there is no plan_name
field in this dataset, so plan_name cannot come from it directly. Instead,
this job additionally reads a *locally archived* second shapefile,
data/sources/gis/新北市都市計畫範圍.zip (never re-downloaded here -- see
`load_plan_boundary_polygons`'s docstring for why), and does a
centroid-in-polygon join to find which 都市計畫 each zoning polygon falls
in. That shapefile's own `Name`/`LblName` attribute fields are corrupted
at the source (verified byte-for-byte, see docs/source_inventory.md's
`ntpc_plan_boundary_shapefile` entry) -- this job never reads them.
Instead it joins on that shapefile's `key`/`SDF_ID` fields (verified
intact) against a manually-derived fix-up table,
data/sources/gis/新北市都市計畫範圍_名稱對照表.json (see that same doc's
`ntpc_plan_boundary_name_lookup_patch` entry for provenance/caveats). If
either local file is missing or malformed, this job degrades gracefully:
it logs a warning and falls back to writing plan_name as NULL for every
row (today's behavior) rather than failing the entire zoning sync over a
secondary enrichment step.

Note: plan_name (this job's output; the official Chinese 都市計畫 name) is
a different concept from plan_id (an internal slug like "jinshan" used as
a key into data/rules/plan_zone_floor_area_ratios.json -- see
providers/base.py's ProviderContext docstring). This job does not resolve
plan_id; that mapping remains human-supplied.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))
sys.path.insert(0, REPO_ROOT)

import shapefile  # noqa: E402  (pyshp)
import pyproj  # noqa: E402
from shapely.geometry import shape as shapely_shape  # noqa: E402
from shapely.ops import transform as shapely_transform  # noqa: E402
from shapely.validation import make_valid  # noqa: E402
from shapely import wkb as shapely_wkb  # noqa: E402

from domain.models import DatasetSnapshotInfo  # noqa: E402
from dataset_registry import DatasetRegistry, compute_file_checksum  # noqa: E402

DATASET_ID = "ntpc_zoning"
METADATA_API_URL = "https://data.ntpc.gov.tw/api/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5/json"
DATASET_PAGE_URL = "https://data.ntpc.gov.tw/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5"
SOURCE_NAME = "新北市都市計畫土地使用分區及範圍圖"
SOURCE_AGENCY = "新北市政府城鄉發展局"
ITEM_NAME = "新北市使用分區"
REFRESH_POLICY = "quarterly"

# plan_name backfill inputs -- both read from a local archive, never
# downloaded by this script (see module docstring). The boundary shapefile
# and its name-lookup patch change far less often than the zoning dataset
# itself, and re-deriving the lookup table requires a manual, one-time
# browser/token dance (see docs/source_inventory.md) that must not be
# automated into a routine sync job.
PLAN_BOUNDARY_ZIP_PATH = os.path.join(REPO_ROOT, "data", "sources", "gis", "新北市都市計畫範圍.zip")
PLAN_NAME_LOOKUP_PATH = os.path.join(REPO_ROOT, "data", "sources", "gis", "新北市都市計畫範圍_名稱對照表.json")

# providers/urban_plan_boundary_provider.py's own queryable snapshot --
# separate DatasetRegistry entry/table from DATASET_ID="ntpc_zoning" above,
# on purpose (see that provider's module docstring): urban-plan-boundary
# resolution and 使用分區/zone_name resolution must stay independently
# derivable evidences, never one result silently standing in for the other.
PLAN_BOUNDARY_DATASET_ID = "ntpc_plan_boundary"
PLAN_BOUNDARY_ID_REGISTRY_PATH = os.path.join(REPO_ROOT, "data", "rules", "urban_plan_id_registry.json")

# The lookup table's own sentinel for "not inside any formal 都市計畫" --
# surfaced as plan_name=None (never as the literal Chinese string), matching
# this codebase's convention that "no applicable value" is None, not a
# human-readable placeholder.
NON_PLAN_SENTINEL = "非都市計畫區"

# A generic/descriptive tool User-Agent (fine for OSM's Nominatim/Overpass,
# see providers/osm_facility_lookup.py) is NOT enough for data.ntpc.gov.tw's
# WAF -- verified live in this session that only a real browser UA works.
DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

EXPECTED_PRJ_MARKER = "TWD_1997_TM_Taiwan"
SOURCE_EPSG = "EPSG:3826"
TARGET_EPSG = "EPSG:4326"


class SyncError(Exception):
    pass


def fetch_download_link() -> str:
    req = urllib.request.Request(METADATA_API_URL, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        items = json.loads(resp.read().decode("utf-8"))
    for item in items:
        if item.get("name") == ITEM_NAME:
            link = item.get("link")
            if link:
                return link
    raise SyncError(f"metadata API未回傳名為「{ITEM_NAME}」且含link的項目，無法取得下載連結（不猜測URL）")


def download_zip(url: str, dest_path: str, retries: int = 2) -> None:
    encoded_url = urllib.parse.quote(url, safe=":/")
    req = urllib.request.Request(encoded_url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    attempt = 0
    last_err: Optional[Exception] = None
    while attempt <= retries:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status != 200:
                    raise SyncError(f"下載失敗，HTTP狀態碼={resp.status}")
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            attempt += 1
    raise SyncError(f"下載{url}失敗，已重試{retries}次仍失敗：{last_err}")


def verify_zip_integrity(zip_path: str) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise SyncError(f"ZIP檔案損毀，第一個壞檔為：{bad}")


def extract_zip(zip_path: str, dest_dir: str) -> str:
    """Extracts and returns the path to the single .shp file inside."""
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    shp_files = [f for f in os.listdir(dest_dir) if f.lower().endswith(".shp")]
    if len(shp_files) != 1:
        raise SyncError(f"解壓後預期剛好1個.shp檔，實際找到{len(shp_files)}個：{shp_files}")
    return os.path.join(dest_dir, shp_files[0])


def verify_crs(shp_path: str) -> None:
    prj_path = os.path.splitext(shp_path)[0] + ".prj"
    if not os.path.exists(prj_path):
        raise SyncError(f"找不到.prj檔案（{prj_path}），無法確認座標系統，不假設WGS84")
    with open(prj_path, "r", encoding="utf-8") as f:
        content = f.read()
    if EXPECTED_PRJ_MARKER not in content:
        raise SyncError(
            f".prj內容不含預期的「{EXPECTED_PRJ_MARKER}」標記，座標系統可能已變更，"
            f"需人工確認後更新本腳本，不自動假設仍是{SOURCE_EPSG}。實際內容前200字：{content[:200]}"
        )


def _detect_encoding(shp_path: str) -> str:
    """Reads the shapefile's .cpg every run rather than hardcoding a
    previously-observed encoding (BIG5, confirmed live in this session) --
    the source could change it without notice."""
    cpg_path = os.path.splitext(shp_path)[0] + ".cpg"
    if not os.path.exists(cpg_path):
        raise SyncError(f"找不到.cpg編碼宣告檔（{cpg_path}），不猜測文字編碼")
    with open(cpg_path, "r", encoding="ascii", errors="ignore") as f:
        enc = f.read().strip()
    if not enc:
        raise SyncError(f".cpg檔案存在但內容為空（{cpg_path}），不猜測文字編碼")
    return enc


def iter_zone_polygons(shp_path: str) -> Iterator[Tuple[str, bytes, Tuple[float, float, float, float]]]:
    """Yields (zone_name, geometry_wkb_wgs84, (min_lon, max_lon, min_lat,
    max_lat)) for every usable polygon record, reprojected from
    EPSG:3826 to WGS84."""
    encoding = _detect_encoding(shp_path)
    transformer = pyproj.Transformer.from_crs(SOURCE_EPSG, TARGET_EPSG, always_xy=True)

    # Explicitly closed (context manager) rather than left to GC -- on
    # Windows an unclosed pyshp Reader keeps its .dbf/.shp file handles
    # open, which then makes sync()'s work_dir cleanup silently fail
    # (shutil.rmtree(..., ignore_errors=True) swallows it, leaving ~170MB
    # of extracted shapefile behind after every real sync() run; verified
    # live in this session -- same root cause already fixed for
    # load_plan_boundary_polygons's reader below).
    with shapefile.Reader(shp_path, encoding=encoding) as reader:
        field_names = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
        if "ZONE" not in field_names:
            raise SyncError(f"shapefile欄位不含預期的ZONE欄位，實際欄位：{field_names}（來源結構可能已變更）")

        for sr in reader.iterShapeRecords():
            zone_name = sr.record["ZONE"]
            if not zone_name or not str(zone_name).strip():
                continue  # no usable zone label -- skip rather than fabricate one
            zone_name = str(zone_name).strip()

            geom = shapely_shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                geom = make_valid(geom)
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                print(f"警告：跳過非多邊形幾何（{geom.geom_type}），ZONE={zone_name}", file=sys.stderr)
                continue

            geom_wgs84 = shapely_transform(transformer.transform, geom)
            min_lon, min_lat, max_lon, max_lat = geom_wgs84.bounds
            yield zone_name, shapely_wkb.dumps(geom_wgs84), (min_lon, max_lon, min_lat, max_lat)


def load_plan_name_lookup(lookup_path: str) -> Dict[Tuple[str, int], str]:
    """Parses the manual key/sdf_id -> plan-name fix-up table (see
    docs/source_inventory.md's `ntpc_plan_boundary_name_lookup_patch` entry
    for how it was derived and why the boundary shapefile's own Name/
    LblName fields cannot be read directly). Keyed by (key, sdf_id) as a
    compound pair -- that pair is unique in this dataset even though `key`
    alone repeats (key=999 appears twice with two different sdf_id
    values)."""
    if not os.path.exists(lookup_path):
        raise SyncError(f"找不到都市計畫名稱對照表（{lookup_path}），不猜測名稱")
    with open(lookup_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("lookup")
    if not entries:
        raise SyncError(f"都市計畫名稱對照表（{lookup_path}）缺少lookup陣列或為空，不視為有效資料")

    lookup: Dict[Tuple[str, int], str] = {}
    for entry in entries:
        try:
            key = str(entry["key"])
            sdf_id = int(entry["sdf_id"])
            name = entry["name"]
        except (KeyError, TypeError, ValueError) as e:
            raise SyncError(f"都市計畫名稱對照表格式不符，記錄={entry!r}：{e}")
        if not name:
            raise SyncError(f"都市計畫名稱對照表記錄名稱為空，記錄={entry!r}")
        lookup[(key, sdf_id)] = name
    return lookup


def _extract_plan_boundary_shp(zip_path: str, work_dir: str) -> str:
    """Shared unzip+CRS-verify step for 新北市都市計畫範圍.zip (read from a
    local archive -- see module docstring, never downloaded by this
    function). Returns the extracted .shp path."""
    if not os.path.exists(zip_path):
        raise SyncError(f"找不到都市計畫範圍圖資（{zip_path}），不猜測邊界")
    verify_zip_integrity(zip_path)
    extract_dir = os.path.join(work_dir, "plan_boundary_extracted")
    shp_path = extract_zip(zip_path, extract_dir)
    verify_crs(shp_path)
    return shp_path


def _iter_plan_boundary_shp_features(shp_path: str) -> Iterator[Tuple[str, int, object]]:
    """Yields (key, SDF_ID, geometry_wgs84) for every boundary polygon in
    the extracted 新北市都市計畫範圍 shapefile. Reads only `key`/`SDF_ID`
    (verified intact at the source) -- never this shapefile's own Name/
    LblName attributes, which are corrupted at the source. Name/plan_id
    resolution is the caller's job (see `load_plan_boundary_polygons` for
    the fail-loudly variant used to backfill 新北市使用分區's plan_name, and
    `load_plan_boundary_features_for_provider` for the tolerant variant
    used to build providers/urban_plan_boundary_provider.py's own queryable
    snapshot)."""
    encoding = _detect_encoding(shp_path)
    transformer = pyproj.Transformer.from_crs(SOURCE_EPSG, TARGET_EPSG, always_xy=True)

    # Explicitly closed (context manager) rather than left to GC -- on
    # Windows an unclosed pyshp Reader keeps its .dbf/.shp file handles
    # open, which then makes the caller's temp-directory cleanup fail
    # (verified live in this session).
    with shapefile.Reader(shp_path, encoding=encoding) as reader:
        field_names = [f[0] for f in reader.fields[1:]]
        for required in ("key", "SDF_ID"):
            if required not in field_names:
                raise SyncError(
                    f"都市計畫範圍shapefile欄位不含預期的{required}欄位，實際欄位：{field_names}"
                    "（來源結構可能已變更）"
                )

        for sr in reader.iterShapeRecords():
            key = str(sr.record["key"])
            sdf_id = int(sr.record["SDF_ID"])

            geom = shapely_shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                geom = make_valid(geom)
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                print(
                    f"警告：跳過非多邊形都市計畫範圍幾何（{geom.geom_type}），key={key}/SDF_ID={sdf_id}",
                    file=sys.stderr,
                )
                continue

            yield key, sdf_id, shapely_transform(transformer.transform, geom)


def load_plan_boundary_polygons(
    zip_path: str, lookup: Dict[Tuple[str, int], str], work_dir: str
) -> List[Tuple[str, object]]:
    """Extracts 新北市都市計畫範圍.zip and returns [(plan_name, shapely
    geometry in WGS84), ...] for every boundary polygon, joined against
    `lookup` for the actual name. FAILS LOUDLY if a record's (key, SDF_ID)
    is not in `lookup` -- used to backfill 新北市使用分區's plan_name column
    (scripts/sync_ntpc_zoning_dataset.py's own zoning sync), where a gap
    means the reference table itself needs updating. Contrast with
    `load_plan_boundary_features_for_provider`, which tolerates a mapping
    gap per-record instead (needed so providers/urban_plan_boundary_
    provider.py can report PLAN_MAPPING_UNAVAILABLE at query time rather
    than never being able to build a snapshot at all)."""
    shp_path = _extract_plan_boundary_shp(zip_path, work_dir)
    polygons: List[Tuple[str, object]] = []
    for key, sdf_id, geom_wgs84 in _iter_plan_boundary_shp_features(shp_path):
        name = lookup.get((key, sdf_id))
        if name is None:
            raise SyncError(
                f"都市計畫範圍記錄(key={key}, SDF_ID={sdf_id})不在名稱對照表中，"
                "來源資料可能已變更，需人工確認並更新對照表，不猜測名稱"
            )
        polygons.append((name, geom_wgs84))
    return polygons


def load_plan_id_registry(registry_path: str) -> Dict[Tuple[str, int], Tuple[Optional[str], str]]:
    """Parses data/rules/urban_plan_id_registry.json (see
    scripts/build_urban_plan_id_registry.py) into
    {(source_key, source_sdf_id): (plan_id, plan_name)}. plan_id is None
    for the dataset's own "非都市計畫區" (not-a-plan) sentinel entries --
    never a synthesized placeholder."""
    if not os.path.exists(registry_path):
        raise SyncError(f"找不到都市計畫plan_id registry（{registry_path}），需先執行"
                         "scripts/build_urban_plan_id_registry.py")
    with open(registry_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries")
    if not entries:
        raise SyncError(f"都市計畫plan_id registry（{registry_path}）缺少entries陣列或為空")

    registry: Dict[Tuple[str, int], Tuple[Optional[str], str]] = {}
    for entry in entries:
        try:
            key = str(entry["source_key"])
            sdf_id = int(entry["source_sdf_id"])
            plan_id = entry["plan_id"]
            plan_name = entry["plan_name"]
        except (KeyError, TypeError, ValueError) as e:
            raise SyncError(f"都市計畫plan_id registry格式不符，記錄={entry!r}：{e}")
        registry[(key, sdf_id)] = (plan_id, plan_name)
    return registry


def load_plan_boundary_features_for_provider(
    zip_path: str, id_registry: Dict[Tuple[str, int], Tuple[Optional[str], str]], work_dir: str
) -> List[Tuple[str, int, Optional[str], Optional[str], bool, object]]:
    """Extracts 新北市都市計畫範圍.zip and returns, for every boundary
    polygon: (source_key, source_sdf_id, plan_id, plan_name, mapping_found,
    geometry_wgs84). Unlike `load_plan_boundary_polygons`, TOLERATES a
    record whose (key, SDF_ID) is absent from `id_registry`
    (mapping_found=False, plan_id=None, plan_name=None) instead of raising
    -- this is what lets providers/urban_plan_boundary_provider.py report
    PLAN_MAPPING_UNAVAILABLE for that specific polygon at query time rather
    than the whole snapshot build failing over one unmapped record."""
    shp_path = _extract_plan_boundary_shp(zip_path, work_dir)
    records: List[Tuple[str, int, Optional[str], Optional[str], bool, object]] = []
    for key, sdf_id, geom_wgs84 in _iter_plan_boundary_shp_features(shp_path):
        mapped = id_registry.get((key, sdf_id))
        if mapped is None:
            records.append((key, sdf_id, None, None, False, geom_wgs84))
        else:
            plan_id, plan_name = mapped
            records.append((key, sdf_id, plan_id, plan_name, True, geom_wgs84))
    return records


def resolve_plan_name(
    zone_geometry_wkb: bytes, boundary_polygons: List[Tuple[str, object]]
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (plan_name, note) for one zoning polygon, tested against
    `boundary_polygons` via its centroid (a representative point rather
    than full-polygon containment, since the two datasets are digitized
    independently and their boundary lines do not perfectly coincide).
    Never guesses when the result is ambiguous or empty -- returns
    plan_name=None with an explanatory note instead, per this codebase's
    project-wide "不得自行捏造資料" rule."""
    centroid = shapely_wkb.loads(zone_geometry_wkb).centroid
    matches = [name for name, polygon in boundary_polygons if polygon.contains(centroid)]

    if len(matches) == 0:
        return None, "centroid未落在任何都市計畫範圍多邊形內（資料集邊界誤差或範圍外），需人工確認"
    if len(matches) > 1:
        candidates = "、".join(sorted(set(matches)))
        return None, f"centroid同時落在{len(matches)}個都市計畫範圍多邊形內（候選：{candidates}），無法唯一判定，需人工確認"

    name = matches[0]
    if name == NON_PLAN_SENTINEL:
        return None, "都市計畫範圍圖資標示為非都市計畫區"
    return name, None


def iter_zone_polygons_with_plan_name(
    shp_path: str, boundary_polygons: List[Tuple[str, object]]
) -> Iterator[Tuple[str, bytes, Tuple[float, float, float, float], Optional[str], Optional[str]]]:
    """Wraps `iter_zone_polygons`, additionally resolving plan_name/note per
    polygon via `resolve_plan_name`. Kept separate from `iter_zone_polygons`
    itself so that function's existing contract (and tests) are unaffected
    when the plan-boundary join is unavailable (see `sync()`'s fallback)."""
    for zone_name, geom_wkb, bbox in iter_zone_polygons(shp_path):
        plan_name, plan_name_note = resolve_plan_name(geom_wkb, boundary_polygons)
        yield zone_name, geom_wkb, bbox, plan_name, plan_name_note


def write_sqlite_snapshot(records: Iterator[tuple], db_path: str) -> int:
    """Accepts either the plain 3-tuples `iter_zone_polygons` yields
    (zone_name, geometry_wkb, bbox) -- plan_name/plan_name_note default to
    NULL, today's behavior when the plan-boundary join is unavailable -- or
    the 5-tuples `iter_zone_polygons_with_plan_name` yields (same plus
    plan_name, plan_name_note). Kept as one function (rather than two)
    since every consumer (real sync job, tests) needs the same table/index
    creation regardless of which producer feeds it."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE zoning_polygons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_name TEXT NOT NULL,
                plan_name TEXT,
                plan_name_note TEXT,
                geometry_wkb BLOB NOT NULL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX idx_zoning_min_lon ON zoning_polygons(min_lon)")
        conn.execute("CREATE INDEX idx_zoning_max_lon ON zoning_polygons(max_lon)")
        conn.execute("CREATE INDEX idx_zoning_min_lat ON zoning_polygons(min_lat)")
        conn.execute("CREATE INDEX idx_zoning_max_lat ON zoning_polygons(max_lat)")

        count = 0
        for record in records:
            if len(record) == 5:
                zone_name, geom_wkb, (min_lon, max_lon, min_lat, max_lat), plan_name, plan_name_note = record
            else:
                zone_name, geom_wkb, (min_lon, max_lon, min_lat, max_lat) = record
                plan_name, plan_name_note = None, None
            conn.execute(
                "INSERT INTO zoning_polygons "
                "(zone_name, plan_name, plan_name_note, geometry_wkb, min_lon, max_lon, min_lat, max_lat) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (zone_name, plan_name, plan_name_note, geom_wkb, min_lon, max_lon, min_lat, max_lat),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def sync(out_dir: Optional[str] = None, keep_temp: bool = False) -> DatasetSnapshotInfo:
    # abspath (not just the REPO_ROOT-based default) so DatasetSnapshotInfo.
    # local_path is always CWD-independent -- a caller-supplied relative
    # --out-dir would otherwise register a path that only resolves from
    # whatever directory this script happened to run in, breaking every
    # later query from a different CWD (verified live in this session).
    out_dir = os.path.abspath(out_dir or os.path.join(REPO_ROOT, "data", "snapshots"))
    os.makedirs(out_dir, exist_ok=True)
    version = datetime.now().strftime("%Y-%m-%d")

    work_dir = os.path.join(out_dir, f"_work_{DATASET_ID}")
    os.makedirs(work_dir, exist_ok=True)
    zip_path = os.path.join(work_dir, "download.zip")
    extract_dir = os.path.join(work_dir, "extracted")

    print(f"[1/7] 查詢metadata API取得下載連結：{METADATA_API_URL}")
    link = fetch_download_link()
    print(f"      取得連結：{link}")

    print("[2/7] 下載ZIP檔（來源約75MB，請稍候）...")
    download_zip(link, zip_path)
    size = os.path.getsize(zip_path)
    print(f"      下載完成，檔案大小：{size:,} bytes")

    print("[3/7] 驗證ZIP完整性...")
    verify_zip_integrity(zip_path)

    print("[4/7] 解壓縮...")
    shp_path = extract_zip(zip_path, extract_dir)

    print("[5/7] 驗證座標系統（.prj）...")
    verify_crs(shp_path)
    print(f"      確認為{SOURCE_EPSG}（TWD97 TM Taiwan），將重投影至{TARGET_EPSG}")

    print("[6/7] 讀取並重投影所有分區多邊形，寫入SQLite快照...")
    plan_name_status_note = (
        "plan_name欄位固定為NULL：本次都市計畫範圍疊合未執行（見上方警告訊息）。"
    )
    boundary_polygons: Optional[List[Tuple[str, object]]] = None
    try:
        lookup = load_plan_name_lookup(PLAN_NAME_LOOKUP_PATH)
        boundary_polygons = load_plan_boundary_polygons(PLAN_BOUNDARY_ZIP_PATH, lookup, work_dir)
        plan_name_status_note = (
            f"plan_name透過與本地封存之「新北市都市計畫範圍」圖資（{PLAN_BOUNDARY_ZIP_PATH}）"
            f"及名稱修復對照表（{PLAN_NAME_LOOKUP_PATH}）做centroid point-in-polygon空間疊合取得，"
            "詳見docs/source_inventory.md「ntpc_plan_boundary_name_lookup_patch」條目。"
        )
    except SyncError as e:
        print(f"      警告：都市計畫邊界疊合失敗（{e}），本次快照plan_name將全數留空", file=sys.stderr)

    db_path = os.path.join(out_dir, f"{DATASET_ID}_{version}.sqlite3")
    if boundary_polygons is not None:
        records = iter_zone_polygons_with_plan_name(shp_path, boundary_polygons)
    else:
        records = iter_zone_polygons(shp_path)
    record_count = write_sqlite_snapshot(records, db_path)
    print(f"      完成，共寫入{record_count}筆分區圖徵至{db_path}")

    print("[7/7] 登記至DatasetRegistry...")
    checksum = compute_file_checksum(db_path)
    info = DatasetSnapshotInfo(
        dataset_id=DATASET_ID,
        source_name=SOURCE_NAME,
        source_agency=SOURCE_AGENCY,
        source_url=DATASET_PAGE_URL,
        local_snapshot_version=version,
        local_path=db_path,
        checksum=checksum,
        license=None,
        refresh_policy=REFRESH_POLICY,
        last_synced_at=datetime.now(),
        record_count=record_count,
        notes=f"來源ZIP下載自{link}。{plan_name_status_note}",
    )
    registry = DatasetRegistry()
    registry.register_snapshot(info)
    print(f"      已登記快照版本{version}於DatasetRegistry（dataset_id={DATASET_ID}）")

    if not keep_temp:
        shutil.rmtree(work_dir, ignore_errors=True)

    return info


def write_plan_boundary_sqlite_snapshot(
    records: List[Tuple[str, int, Optional[str], Optional[str], bool, object]], db_path: str
) -> int:
    """Writes providers/urban_plan_boundary_provider.py's own snapshot table
    (`plan_boundary_polygons`) -- DELIBERATELY a separate table/file from
    `zoning_polygons` above (see PLAN_BOUNDARY_DATASET_ID's comment).
    `records` is `load_plan_boundary_features_for_provider`'s output:
    (source_key, source_sdf_id, plan_id, plan_name, mapping_found,
    geometry_wgs84)."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE plan_boundary_polygons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                source_sdf_id INTEGER NOT NULL,
                plan_id TEXT,
                plan_name TEXT,
                mapping_found INTEGER NOT NULL,
                geometry_wkb BLOB NOT NULL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX idx_plan_boundary_min_lon ON plan_boundary_polygons(min_lon)")
        conn.execute("CREATE INDEX idx_plan_boundary_max_lon ON plan_boundary_polygons(max_lon)")
        conn.execute("CREATE INDEX idx_plan_boundary_min_lat ON plan_boundary_polygons(min_lat)")
        conn.execute("CREATE INDEX idx_plan_boundary_max_lat ON plan_boundary_polygons(max_lat)")

        count = 0
        for source_key, source_sdf_id, plan_id, plan_name, mapping_found, geom_wgs84 in records:
            min_lon, min_lat, max_lon, max_lat = geom_wgs84.bounds
            conn.execute(
                "INSERT INTO plan_boundary_polygons "
                "(source_key, source_sdf_id, plan_id, plan_name, mapping_found, geometry_wkb, "
                " min_lon, max_lon, min_lat, max_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_key, source_sdf_id, plan_id, plan_name, 1 if mapping_found else 0,
                    shapely_wkb.dumps(geom_wgs84), min_lon, max_lon, min_lat, max_lat,
                ),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def sync_plan_boundary(out_dir: Optional[str] = None) -> DatasetSnapshotInfo:
    """Builds providers/urban_plan_boundary_provider.py's queryable
    snapshot from LOCAL archives only (新北市都市計畫範圍.zip + the
    canonical plan_id registry) -- no network access, unlike `sync()`
    above, since both inputs are static local files (see PLAN_BOUNDARY_
    ZIP_PATH/PLAN_BOUNDARY_ID_REGISTRY_PATH's comments for why they are
    never re-fetched automatically)."""
    # abspath (not just the REPO_ROOT-based default) so DatasetSnapshotInfo.
    # local_path is always CWD-independent -- a caller-supplied relative
    # --out-dir would otherwise register a path that only resolves from
    # whatever directory this script happened to run in, breaking every
    # later query from a different CWD (verified live in this session).
    out_dir = os.path.abspath(out_dir or os.path.join(REPO_ROOT, "data", "snapshots"))
    os.makedirs(out_dir, exist_ok=True)
    version = datetime.now().strftime("%Y-%m-%d")

    work_dir = os.path.join(out_dir, f"_work_{PLAN_BOUNDARY_DATASET_ID}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        print(f"[1/3] 讀取plan_id registry：{PLAN_BOUNDARY_ID_REGISTRY_PATH}")
        id_registry = load_plan_id_registry(PLAN_BOUNDARY_ID_REGISTRY_PATH)

        print(f"[2/3] 讀取都市計畫範圍圖資（本地封存，不連網）：{PLAN_BOUNDARY_ZIP_PATH}")
        records = load_plan_boundary_features_for_provider(PLAN_BOUNDARY_ZIP_PATH, id_registry, work_dir)
        unmapped = [r for r in records if not r[4]]
        if unmapped:
            print(
                f"      警告：{len(unmapped)}筆邊界圖徵在plan_id registry中查無對應"
                "（查詢時將回報PLAN_MAPPING_UNAVAILABLE，不猜測）",
                file=sys.stderr,
            )

        db_path = os.path.join(out_dir, f"{PLAN_BOUNDARY_DATASET_ID}_{version}.sqlite3")
        record_count = write_plan_boundary_sqlite_snapshot(records, db_path)
        print(f"[3/3] 完成，共寫入{record_count}筆都市計畫範圍圖徵至{db_path}")

        checksum = compute_file_checksum(db_path)
        info = DatasetSnapshotInfo(
            dataset_id=PLAN_BOUNDARY_DATASET_ID,
            source_name="新北市都市計畫範圍",
            source_agency=SOURCE_AGENCY,
            source_url=DATASET_PAGE_URL,
            local_snapshot_version=version,
            local_path=db_path,
            checksum=checksum,
            license=None,
            refresh_policy=REFRESH_POLICY,
            last_synced_at=datetime.now(),
            record_count=record_count,
            notes=(
                f"來源：本地封存{PLAN_BOUNDARY_ZIP_PATH}（不連網）；"
                f"plan_id/plan_name透過{PLAN_BOUNDARY_ID_REGISTRY_PATH}（key/SDF_ID複合鍵）解析；"
                f"{len(unmapped)}筆查無對應" if unmapped else "全數50筆邊界圖徵皆成功對應plan_id/plan_name"
            ),
        )
        registry = DatasetRegistry()
        registry.register_snapshot(info)
        print(f"      已登記快照版本{version}於DatasetRegistry（dataset_id={PLAN_BOUNDARY_DATASET_ID}）")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=None, help="快照輸出目錄（預設data/snapshots）")
    parser.add_argument("--keep-temp", action="store_true", help="保留下載的ZIP與解壓檔案（除錯用）")
    parser.add_argument(
        "--skip-plan-boundary", action="store_true",
        help="略過都市計畫範圍snapshot同步（僅同步使用分區，供除錯用）",
    )
    args = parser.parse_args()

    try:
        info = sync(out_dir=args.out_dir, keep_temp=args.keep_temp)
    except SyncError as e:
        print(f"\n同步失敗：{e}", file=sys.stderr)
        return 1

    print(f"\n同步成功：{info.record_count}筆分區圖徵，版本{info.local_snapshot_version}")

    if not args.skip_plan_boundary:
        try:
            plan_info = sync_plan_boundary(out_dir=args.out_dir)
            print(f"\n都市計畫範圍同步成功：{plan_info.record_count}筆，版本{plan_info.local_snapshot_version}")
        except SyncError as e:
            # Does not fail the overall run -- 使用分區 (the more critical,
            # already-consumed dataset) synced fine above; RealUrbanPlan
            # BoundaryProvider degrades to UNKNOWN if its own snapshot never
            # gets built, same as any other UNAVAILABLE dataset.
            print(f"\n警告：都市計畫範圍同步失敗（{e}），plan_id將暫時仍為UNKNOWN", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
