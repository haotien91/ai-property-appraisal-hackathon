# -*- coding: utf-8 -*-
import json
import os
import sys
import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))

from rule_engine import RuleEngine  # noqa: E402


@pytest.fixture(scope="session")
def regional_rules():
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        return json.load(f)["rules"]


@pytest.fixture(scope="session")
def individual_rules():
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        return json.load(f)["rules"]


@pytest.fixture(scope="session")
def all_rules(regional_rules, individual_rules):
    return regional_rules + individual_rules


@pytest.fixture()
def engine(all_rules):
    return RuleEngine(all_rules)


CITY = "新北市"
DISTRICT = "金山區"
LAND_USE = "商業用地"
