"""pytest 共用設定。

test.py 是手動執行的整合腳本（import 時即呼叫真實 API 並 assert），
不是 pytest 測試模組，必須排除以免收集階段就發出網路請求。
"""

import pytest

collect_ignore = ["test.py"]


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
