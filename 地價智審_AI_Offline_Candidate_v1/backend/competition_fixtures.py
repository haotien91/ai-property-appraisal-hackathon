"""Load bundled case inputs from this checkout, independent of a 'data' namespace."""
from functools import lru_cache
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "competition_cases" / "shulin_residential_2026"


@lru_cache(maxsize=2)
def _load(name):
    path = FIXTURE_DIR / (name + ".py")
    # Download ZIP commonly creates a deeply nested Windows directory.
    filename = str(path.resolve())
    if os.name == "nt" and not filename.startswith("\\\\?\\"):
        filename = "\\\\?\\UNC\\" + filename[2:] if filename.startswith("\\\\") else "\\\\?\\" + filename
    if not os.path.isfile(filename):
        raise ImportError(
            f"專案必要檔案不存在：{path}。請完整解壓縮最新分支到較短的路徑"
            "（例如 C:\\appraisal），不要只複製 frontend；關閉舊後端後重新啟動。"
        )
    spec = importlib.util.spec_from_file_location("_appraisal_" + name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_table3():
    return _load("segment_table3_fixtures")


def load_table4():
    return _load("segment_table4_fixtures")


def check_required_fixtures():
    load_table3()
    load_table4()
