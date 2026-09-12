from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import requests

from .config import DEFAULT_CACHE_DIR, NTPC_ZONING_INDEX_CSV
from .normalize import normalize_name


class ZoningSourceError(RuntimeError):
    pass


class ZoneNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZoningSource:
    url: str
    local_path: Path | None = None
    name: str | None = None


def _decode_csv(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ZoningSourceError("Unable to decode NTPC zoning index CSV")


def discover_ntpc_zoning_download(session=requests) -> ZoningSource:
    response = session.get(NTPC_ZONING_INDEX_CSV, timeout=45)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(_decode_csv(response.content))))
    if not rows:
        raise ZoningSourceError("NTPC zoning index returned no rows")

    link_keys = ["link", "下載連結", "下載鏈結", "url", "URL"]
    name_keys = ["name", "名稱"]
    candidates: list[ZoningSource] = []
    for row in rows:
        link = next((str(row.get(k, "")).strip() for k in link_keys if row.get(k)), "")
        if not link:
            continue
        name = next((str(row.get(k, "")).strip() for k in name_keys if row.get(k)), "") or None
        candidates.append(ZoningSource(link, None, name))
    if not candidates:
        raise ZoningSourceError("No download link found in NTPC zoning index")

    # Prefer GIS vector archives/files over non-spatial metadata rows.
    rank = {".zip": 0, ".gpkg": 1, ".geojson": 2, ".json": 3, ".shp": 4}
    candidates.sort(key=lambda s: rank.get(Path(urlparse(s.url).path).suffix.lower(), 99))
    return candidates[0]


def _download_source(source: ZoningSource, cache_dir: Path = DEFAULT_CACHE_DIR, session=requests) -> Path:
    if source.local_path is not None:
        return Path(source.local_path)
    parsed = urlparse(source.url)
    if parsed.scheme in ("", "file"):
        return Path(parsed.path if parsed.scheme == "file" else source.url)

    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(parsed.path).name or "ntpc_zoning_download"
    dest = cache_dir / filename
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    response = session.get(source.url, timeout=120)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def _vector_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return f"zip://{path}"
    return str(path)


def load_zoning(source: ZoningSource, roi_3826, cache_dir: Path = DEFAULT_CACHE_DIR) -> gpd.GeoDataFrame:
    path = _download_source(source, cache_dir=cache_dir)
    if not path.exists():
        raise ZoningSourceError(f"Zoning source does not exist: {path}")
    try:
        gdf = gpd.read_file(_vector_path(path))
    except Exception as exc:
        raise ZoningSourceError(f"Unable to read zoning vector {path}: {exc}") from exc
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        raise ZoningSourceError("Zoning vector has no CRS")
    gdf = gdf.to_crs("EPSG:3826")
    return gpd.clip(gdf, roi_3826)


def _zone_field(gdf: gpd.GeoDataFrame) -> str:
    preferred = ["ZONE", "zone", "分區", "使用分區", "ZONE_NAME", "zonename", "NAME", "name"]
    for col in preferred:
        if col in gdf.columns:
            return col
    # Heuristic: choose the first object/string column with values that mention 區.
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        vals = gdf[col].dropna().astype(str).head(100)
        if any("區" in v for v in vals):
            return col
    raise ZoneNotFoundError(f"Unable to identify zoning-name field; columns={list(gdf.columns)}")


def _normalize_zone(text: str) -> str:
    return normalize_name(re.sub(r"[（(].*?[）)]", "", str(text)))


def select_zone(gdf: gpd.GeoDataFrame, requested_name: str) -> gpd.GeoDataFrame:
    if gdf.empty:
        raise ZoneNotFoundError("Zoning GeoDataFrame is empty")
    field = _zone_field(gdf)
    target = _normalize_zone(requested_name)
    mask = gdf[field].fillna("").astype(str).map(_normalize_zone) == target
    selected = gdf.loc[mask].copy()
    if selected.empty:
        available = sorted(set(gdf[field].dropna().astype(str)))[:30]
        raise ZoneNotFoundError(f"Zone '{requested_name}' not found; sample available={available}")
    return selected
