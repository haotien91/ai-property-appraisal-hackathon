# -*- coding: utf-8 -*-
"""
Direct unit tests for providers/nlsc_land_number_encoder.py (Phase API-2.3).
Every case is derived from an official NLSC documented example (see that
module's docstring) or a boundary/error condition of the documented 4+4
digit contract -- never a guessed format.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

import pytest  # noqa: E402

from nlsc_land_number_encoder import NlscLandNumberEncoder, NlscLandNumberEncoderError  # noqa: E402


def test_golden_case_489_encodes_to_04890000():
    assert NlscLandNumberEncoder.encode(489, 0) == "04890000"


def test_main_only_defaults_sub_to_zero():
    assert NlscLandNumberEncoder.encode(489) == "04890000"


def test_main_with_nonzero_sub():
    assert NlscLandNumberEncoder.encode(977, 6) == "09770006"


def test_zero_main_and_sub():
    assert NlscLandNumberEncoder.encode(0, 0) == "00000000"


@pytest.mark.parametrize("main,sub,expected", [
    (1, 0, "00010000"),
    (509, 0, "05090000"),
    (892, 0, "08920000"),
    (977, 1, "09770001"),
])
def test_official_documented_examples(main, sub, expected):
    assert NlscLandNumberEncoder.encode(main, sub) == expected


def test_negative_main_raises():
    with pytest.raises(NlscLandNumberEncoderError):
        NlscLandNumberEncoder.encode(-1, 0)


def test_negative_sub_raises():
    with pytest.raises(NlscLandNumberEncoderError):
        NlscLandNumberEncoder.encode(1, -1)


def test_main_over_9999_raises():
    with pytest.raises(NlscLandNumberEncoderError):
        NlscLandNumberEncoder.encode(10000, 0)


def test_sub_over_9999_raises():
    with pytest.raises(NlscLandNumberEncoderError):
        NlscLandNumberEncoder.encode(1, 10000)


def test_is_already_encoded_true_for_valid_8_digit_string():
    assert NlscLandNumberEncoder.is_already_encoded("04890000") is True


def test_is_already_encoded_false_for_7_digit_string():
    assert NlscLandNumberEncoder.is_already_encoded("0489000") is False


def test_is_already_encoded_false_for_non_digit_string():
    assert NlscLandNumberEncoder.is_already_encoded("0489000a") is False


def test_is_already_encoded_false_for_none():
    assert NlscLandNumberEncoder.is_already_encoded(None) is False
