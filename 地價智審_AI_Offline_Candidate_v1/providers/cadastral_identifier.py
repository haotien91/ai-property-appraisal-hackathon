# -*- coding: utf-8 -*-
"""
cadastral_identifier.py — Phase API-1.5's Cadastral Parcel Identifier
contract: a strict separation between the 地價區段代碼 (`price_segment_
code`, e.g. "P002-00" -- this project's own internal price-zone code,
carried as `ProviderContext.segment_code`) and the 地籍段名 (`section_
name`, e.g. "金美段" -- a real cadastral section name government land
datasets index by). Phase API-1 conflated these by passing `ctx.segment_
code` straight into the NTPC OpenAPI's `segment` query field; this module
is the fix. See docs/phase9/api_integration_spec.md for the full incident
record.

Two independent, deterministic, LLM-free responsibilities:

1. CadastralParcelIdentifierParser -- parses a free-text cadastral
   reference (e.g. "金美段489地號", the exact string this codebase's
   `base_parcel_id` field already carries -- see domain.models.
   CompetitionCase) into (section_name, subsection_name, land_no_main,
   land_no_sub). Anything not matching a recognized, unambiguous pattern
   resolves to PARSE_UNCERTAIN / requires_manual_review=True -- NEVER a
   best-effort guess, and NEVER lets a price_segment_code-shaped string
   (or anything not containing "段") through as a section_name.

2. LandNumberNormalizer -- converts a parsed (main, sub) pair into the
   exact raw string format each dataset's own `id`/`lid` field actually
   uses. These are NOT the same format between datasets (empirically
   verified against the live API during this round, not assumed):

   - 新北市公告土地現值 (`lid`): main ZERO-PADDED to 4 digits + sub
     ZERO-PADDED to 4 digits, concatenated (8 chars total). Verified
     against 5,600 real records across 7 districts -- every single one
     was exactly 8 characters, with main values observed up to 814 still
     rendered as a 4-digit zero-padded prefix (e.g. main=1 -> "0001").
   - 新北市已公告徵收案件 (`id`): main NOT padded (raw digit string,
     whatever width the actual number needs) + sub ZERO-PADDED to 4
     digits, concatenated (variable total length). Verified against 3,000
     real records across 5 districts: total string length varied (5/6/7/8
     characters observed) and in every case, the LAST 4 characters formed
     a coherent zero-padded sub-number sequence while the PREFIX matched a
     stable, repeating value across many consecutive sub-numbers (e.g. one
     segment showed a constant 3-digit prefix "214" paired with 16
     different 3-4 digit suffixes 0019..0074 -- i.e. suffix always 4
     digits, prefix unpadded).

   A main/sub pair with no verified target-format mapping (i.e. a
   currently-unsupported dataset) resolves to UNKNOWN / requires_manual_
   review=True rather than guessing a format.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ParcelIdentifierParseStatus(str, Enum):
    PARSED = "PARSED"
    PARSE_UNCERTAIN = "PARSE_UNCERTAIN"


class CadastralParcelIdentifier(BaseModel):
    """Deliberately separate from domain.models' Evidence-shaped models --
    this is a text-parsing utility result (an intermediate artifact), not
    an Evidence carrying external-source provenance. `raw_input` is ALWAYS
    preserved verbatim, even on PARSE_UNCERTAIN, so a human reviewer can
    see exactly what this codebase failed to confidently parse."""
    model_config = ConfigDict(extra="forbid")

    raw_input: str
    parse_status: ParcelIdentifierParseStatus
    section_name: Optional[str] = None
    subsection_name: Optional[str] = None
    land_no_raw: Optional[str] = None
    land_no_main: Optional[int] = None
    land_no_sub: Optional[int] = None
    requires_manual_review: bool = False
    notes: Optional[str] = None


# Matches e.g. "金美段489地號", "頂溪段一小段489之2地號", "五股段489-2號".
# Group `section` REQUIRES the literal character "段" -- a string with no
# "段" at all (e.g. "P002-00", a price_segment_code) can never match this
# group, so it can never become a section_name (the exact guardrail this
# round's instructions demand). `subsection` is optional and itself must
# also end in "段" (小段 names always do, e.g. "一小段"). The land number is
# `\d+` optionally followed by a "之" or "-" separated sub-number, with an
# optional trailing "地號"/"號" suffix.
_PARCEL_PATTERN = re.compile(
    r"^(?P<section>.+?段)"
    r"(?P<subsection>.+?段)?"
    r"(?P<main>\d+)"
    r"(?:[之\-](?P<sub>\d+))?"
    r"(?:地號|號)?$"
)


class CadastralParcelIdentifierParser:
    """Stateless, deterministic, regex-based -- no LLM, no fuzzy matching,
    no "best guess" fallback. See module docstring."""

    @staticmethod
    def parse(raw_input: Optional[str]) -> CadastralParcelIdentifier:
        text = (raw_input or "").strip()

        if not text or text.upper() == "TBD":
            return CadastralParcelIdentifier(
                raw_input=raw_input or "", parse_status=ParcelIdentifierParseStatus.PARSE_UNCERTAIN,
                requires_manual_review=True,
                notes="缺少地籍識別字串（或為建立案件時之預設值「TBD」），無法解析。",
            )

        match = _PARCEL_PATTERN.match(text)
        if not match:
            return CadastralParcelIdentifier(
                raw_input=text, parse_status=ParcelIdentifierParseStatus.PARSE_UNCERTAIN,
                requires_manual_review=True,
                notes=(
                    "輸入字串不符合可辨識之地籍格式（須含「段」字與地號數字，"
                    "例如「金美段489地號」）。原始字串已完整保留，不做任何猜測性解析。"
                ),
            )

        section = match.group("section")
        subsection = match.group("subsection")
        main_str = match.group("main")
        sub_str = match.group("sub")

        # A section_name that is itself exactly this project's own
        # price_segment_code shape (e.g. "P002-00") would only be possible
        # here if the regex somehow matched such a string -- it cannot,
        # since price_segment_code never contains "段". This assertion-by-
        # construction is documented rather than re-checked at runtime, to
        # avoid implying there is a plausible code path that could still
        # let it through silently.
        land_no_main = int(main_str)
        land_no_sub = int(sub_str) if sub_str is not None else 0
        land_no_raw = f"{main_str}之{sub_str}" if sub_str is not None else main_str

        return CadastralParcelIdentifier(
            raw_input=text, parse_status=ParcelIdentifierParseStatus.PARSED,
            section_name=section, subsection_name=subsection,
            land_no_raw=land_no_raw, land_no_main=land_no_main, land_no_sub=land_no_sub,
            requires_manual_review=False,
            notes=None,
        )


class LandNumberNormalizer:
    """See module docstring for the empirical verification behind each
    target format. Adding a new dataset's format requires adding a new
    method here (each independently verified against real data) -- never
    a shared "guess the format" helper."""

    @staticmethod
    def to_land_price_lid(main: Optional[int], sub: Optional[int]) -> Optional[str]:
        """新北市公告土地現值 `lid` format: 4-digit zero-padded main +
        4-digit zero-padded sub. Returns None (caller must treat as
        UNKNOWN) for out-of-range or missing input rather than truncating
        or guessing."""
        if main is None or sub is None or main < 0 or sub < 0:
            return None
        if main > 9999 or sub > 9999:
            # Never verified against a real example this large -- refuse
            # to guess a wider format than the one actually confirmed.
            return None
        return f"{main:04d}{sub:04d}"

    @staticmethod
    def to_expropriation_id(main: Optional[int], sub: Optional[int]) -> Optional[str]:
        """新北市已公告徵收案件 `id` format: UNPADDED main + 4-digit
        zero-padded sub."""
        if main is None or sub is None or main < 0 or sub < 0:
            return None
        if sub > 9999:
            return None
        return f"{main:d}{sub:04d}"
