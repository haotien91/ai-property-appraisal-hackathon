from pathlib import Path

NTPC_ZONING_INDEX_CSV = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "fe26e0a5-54c2-4876-bbc7-150243c048f5/csv/file"
)
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "ntpc_boundary_poc"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
