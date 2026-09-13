"""pytest 共用設定。

預設只跑不連網的邏輯測試；標記 live 的測試會呼叫真實 NLSC／OSM API，
需加 --live 才執行。

test.py 是手動執行的整合腳本（會呼叫 NLSC／Overpass／Bedrock 並消耗
token），不是 pytest 測試模組，必須排除以免收集階段就發出請求。
"""

import pytest

# 工作區內另外放置的團隊專案有自己的測試與相依（weasyprint、pyshp…），
# 裸跑 pytest 會在收集階段就 ImportError 並中斷，連本專案的測試都跑不到。
# 這些目錄不屬於本專案，一律排除。
collect_ignore = [
    "test.py",
    "ai-property-appraisal-hackathon",
    "地價智審_AI_Offline_Candidate_v1",
]


def pytest_ignore_collect(collection_path, config):
    """再加一道保險：任何自帶 pyproject/setup.py 的子目錄都不收集。

    團隊專案的位置會變動（實測從 ai-property-appraisal-hackathon/ 底下
    移到工作區根目錄），寫死名稱不夠。以「有自己的專案定義檔」判斷更穩。
    """

    if collection_path.is_dir() and collection_path != config.rootpath:
        for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
            if (collection_path / marker).exists():
                return True
    return None


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="一併執行會呼叫真實 NLSC／OSM API 的測試",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: 需要對外網路連線的測試（預設跳過，加 --live 才執行）",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return

    skip_live = pytest.mark.skip(reason="需加 --live 才執行（會呼叫真實 API）")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
