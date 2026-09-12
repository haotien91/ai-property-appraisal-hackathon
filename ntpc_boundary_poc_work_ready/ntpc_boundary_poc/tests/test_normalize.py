from pathlib import Path

import pytest

from src.normalize import first_explicit_road, normalize_name
from src.models import load_case


def test_normalize_name_removes_spaces_and_fullwidth_punctuation():
    assert normalize_name(" 長壽街 21 巷，") == "長壽街21巷"


def test_first_explicit_road_keeps_first_named_road_and_warns_on_composite():
    road, warnings = first_explicit_road("啟智街及未開闢計畫道路")
    assert road == "啟智街"
    assert warnings


def test_load_case_reads_structured_contract():
    case = load_case(Path("input.example.json"))
    assert case.section.code == "1902"
    assert case.parcel == "284"
    assert case.constraints.zone == "第一種住宅區"
