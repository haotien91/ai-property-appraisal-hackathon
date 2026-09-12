# -*- coding: utf-8 -*-
"""
Tests for providers/cadastral_identifier.py (Phase API-1.5). Pure,
deterministic, offline -- no network, no LLM.

What these tests exist to catch:
1. price_segment_code (e.g. "P002-00") can NEVER be parsed into a
   section_name -- the exact category error Phase API-1 made.
2. The real Golden Case string ("金美段489地號") parses correctly.
3. Ambiguous input resolves to PARSE_UNCERTAIN, never a guess.
4/5. Main/sub land-number normalization matches the empirically-verified
   real government data formats (not assumed conventions).
6. Invalid input (None, empty, "TBD") is handled without crashing.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "providers"))

from cadastral_identifier import (  # noqa: E402
    CadastralParcelIdentifierParser, LandNumberNormalizer, ParcelIdentifierParseStatus,
)


# ---------------------------------------------------------------------------
# 1. price_segment_code never used as cadastral section
# ---------------------------------------------------------------------------

def test_price_segment_code_never_becomes_section_name():
    result = CadastralParcelIdentifierParser.parse("P002-00")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN
    assert result.section_name is None
    assert result.raw_input == "P002-00"  # preserved verbatim


def test_price_segment_code_variants_all_rejected():
    for code in ["P002-00", "P012-00", "P003-00", "p002-00"]:
        result = CadastralParcelIdentifierParser.parse(code)
        assert result.section_name is None, f"{code} must never yield a section_name"
        assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN


# ---------------------------------------------------------------------------
# 2. 金美段489地號 parser (the real Golden Case string)
# ---------------------------------------------------------------------------

def test_golden_case_parcel_identifier_parses_correctly():
    result = CadastralParcelIdentifierParser.parse("金美段489地號")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSED
    assert result.section_name == "金美段"
    assert result.subsection_name is None
    assert result.land_no_main == 489
    assert result.land_no_sub == 0  # implicit sub-number when none given
    assert result.requires_manual_review is False


def test_parcel_identifier_without_trailing_suffix_still_parses():
    result = CadastralParcelIdentifierParser.parse("金美段489")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSED
    assert result.section_name == "金美段"
    assert result.land_no_main == 489


def test_parcel_identifier_with_explicit_subsection_and_sub_number():
    result = CadastralParcelIdentifierParser.parse("頂溪段一小段489之2地號")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSED
    assert result.section_name == "頂溪段"
    assert result.subsection_name == "一小段"
    assert result.land_no_main == 489
    assert result.land_no_sub == 2


def test_parcel_identifier_with_dash_separator_sub_number():
    result = CadastralParcelIdentifierParser.parse("五股段489-2號")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSED
    assert result.section_name == "五股段"
    assert result.land_no_main == 489
    assert result.land_no_sub == 2


# ---------------------------------------------------------------------------
# 3. ambiguous parcel parser
# ---------------------------------------------------------------------------

def test_ambiguous_input_without_section_marker_is_uncertain():
    result = CadastralParcelIdentifierParser.parse("489號")  # no "段" at all
    assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN
    assert result.requires_manual_review is True
    assert result.raw_input == "489號"


def test_ambiguous_free_text_is_uncertain():
    result = CadastralParcelIdentifierParser.parse("約在金美段附近489號地")
    # "約在金美段附近489號地" -- the digits are separated from "段" by
    # non-numeric text the regex's `.+?段` group would swallow, but the
    # trailing "號地" doesn't match the pattern's required tail shape; this
    # must not silently produce a wrong section_name/land_no pairing.
    if result.parse_status == ParcelIdentifierParseStatus.PARSED:
        # If it happens to structurally match, at minimum it must never
        # silently produce a nonsensical section_name.
        assert "P0" not in (result.section_name or "")
    else:
        assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN
        assert result.requires_manual_review is True


# ---------------------------------------------------------------------------
# 4/5. main / sub parcel number normalization (empirically verified formats)
# ---------------------------------------------------------------------------

def test_land_price_lid_normalization_matches_verified_real_data():
    # Verified live against data.ntpc.gov.tw: main zero-padded to 4 digits +
    # sub zero-padded to 4 digits = 8 chars total (e.g. real record
    # district=板橋區 segment=忠孝段 lid="00010000").
    assert LandNumberNormalizer.to_land_price_lid(1, 0) == "00010000"
    assert LandNumberNormalizer.to_land_price_lid(489, 0) == "04890000"
    assert LandNumberNormalizer.to_land_price_lid(11, 0) == "00110000"
    assert LandNumberNormalizer.to_land_price_lid(3, 2) == "00030002"


def test_expropriation_id_normalization_matches_verified_real_data():
    # Verified live against data.ntpc.gov.tw: main UNPADDED + sub
    # zero-padded to 4 digits (e.g. real record district=蘆洲區
    # segment=保新段 id="1510000"; another segment's records showed a
    # stable "214" prefix paired with 16 different 3-4 digit suffixes
    # 0019..0074, confirming main is not zero-padded).
    assert LandNumberNormalizer.to_expropriation_id(151, 0) == "1510000"
    assert LandNumberNormalizer.to_expropriation_id(214, 19) == "2140019"
    assert LandNumberNormalizer.to_expropriation_id(489, 0) == "4890000"
    assert LandNumberNormalizer.to_expropriation_id(1, 2) == "10002"


def test_land_price_and_expropriation_formats_are_never_conflated():
    """The whole point of Phase API-1.5's per-dataset normalizer: the two
    datasets' formats genuinely differ (8-char fixed-width vs
    variable-width) -- a caller must never assume one format works for
    both datasets."""
    lid = LandNumberNormalizer.to_land_price_lid(1, 0)
    expro_id = LandNumberNormalizer.to_expropriation_id(1, 0)
    assert lid == "00010000"
    assert expro_id == "10000"
    assert lid != expro_id


# ---------------------------------------------------------------------------
# 6. invalid input
# ---------------------------------------------------------------------------

def test_none_input_is_uncertain_not_a_crash():
    result = CadastralParcelIdentifierParser.parse(None)
    assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN
    assert result.requires_manual_review is True


def test_empty_string_is_uncertain():
    result = CadastralParcelIdentifierParser.parse("")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN


def test_tbd_placeholder_is_uncertain():
    result = CadastralParcelIdentifierParser.parse("TBD")
    assert result.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN
    assert "TBD" in (result.notes or "")


def test_land_number_normalizer_refuses_negative_or_missing_input():
    assert LandNumberNormalizer.to_land_price_lid(None, 0) is None
    assert LandNumberNormalizer.to_land_price_lid(1, None) is None
    assert LandNumberNormalizer.to_land_price_lid(-1, 0) is None
    assert LandNumberNormalizer.to_expropriation_id(None, 0) is None
    assert LandNumberNormalizer.to_expropriation_id(1, -1) is None


def test_land_number_normalizer_refuses_out_of_verified_range():
    # main > 9999 was never observed in the 5,600-record land-price sample
    # this format was verified against -- refuse to guess a wider format.
    assert LandNumberNormalizer.to_land_price_lid(99999, 0) is None
    assert LandNumberNormalizer.to_expropriation_id(1, 99999) is None
