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

Known data-shape gap (see providers/ntpc_zoning_provider.py's module
docstring): "新北市使用分區"'s shapefile has exactly one attribute field,
`ZONE` -- there is no plan_name field in this dataset. plan_name is
therefore always written as NULL by this job; populating it would
require a separate spatial join against the "新北市都市計畫範圍" dataset,
not yet implemented.
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
from typing import Iterator, Optional, Tuple

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

    reader = shapefile.Reader(shp_path, encoding=encoding)
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


def write_sqlite_snapshot(
    records: Iterator[Tuple[str, bytes, Tuple[float, float, float, float]]], db_path: str
) -> int:
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
        for zone_name, geom_wkb, (min_lon, max_lon, min_lat, max_lat) in records:
            conn.execute(
                "INSERT INTO zoning_polygons "
                "(zone_name, plan_name, geometry_wkb, min_lon, max_lon, min_lat, max_lat) "
                "VALUES (?, NULL, ?, ?, ?, ?, ?)",
                (zone_name, geom_wkb, min_lon, max_lon, min_lat, max_lat),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def sync(out_dir: Optional[str] = None, keep_temp: bool = False) -> DatasetSnapshotInfo:
    out_dir = out_dir or os.path.join(REPO_ROOT, "data", "snapshots")
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
    db_path = os.path.join(out_dir, f"{DATASET_ID}_{version}.sqlite3")
    record_count = write_sqlite_snapshot(iter_zone_polygons(shp_path), db_path)
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
        notes=(
            f"來源ZIP下載自{link}。plan_name欄位固定為NULL："
            "本資料集shapefile僅含ZONE屬性欄位，無都市計畫名稱欄位，"
            "如需plan_name須另外對「新北市都市計畫範圍」資料集做空間疊合，尚未實作。"
        ),
    )
    registry = DatasetRegistry()
    registry.register_snapshot(info)
    print(f"      已登記快照版本{version}於DatasetRegistry（dataset_id={DATASET_ID}）")

    if not keep_temp:
        shutil.rmtree(work_dir, ignore_errors=True)

    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=None, help="快照輸出目錄（預設data/snapshots）")
    parser.add_argument("--keep-temp", action="store_true", help="保留下載的ZIP與解壓檔案（除錯用）")
    args = parser.parse_args()

    try:
        info = sync(out_dir=args.out_dir, keep_temp=args.keep_temp)
    except SyncError as e:
        print(f"\n同步失敗：{e}", file=sys.stderr)
        return 1

    print(f"\n同步成功：{info.record_count}筆分區圖徵，版本{info.local_snapshot_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
