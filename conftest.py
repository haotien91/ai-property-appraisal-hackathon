"""pytest 共用設定。

預設只跑不連網的邏輯測試；標記 live 的測試會呼叫真實 NLSC／OSM API，
需加 --live 才執行。
"""

import pytest


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
