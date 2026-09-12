# -*- coding: utf-8 -*-
"""
nlsc_land_number_encoder.py — NLSC CAD API land-number ("地號") formatting
contract, per Phase API-2.2R2's live audit of NLSC's own technical
documentation (CadasMapPosition.jsp / GetLandPositionLongitudeLatitude.jsp /
CadasAttrQuery.jsp / CadasLandNoQuery.jsp response examples).

Deliberately a SEPARATE module/class from providers/cadastral_identifier.py's
`LandNumberNormalizer` (which encodes for NTPC's `lid`/`id` fields, 新北市
政府地政局's OWN dataset convention -- a completely different government
system from NLSC/內政部國土測繪中心). Both happen to compute the identical
8-digit zero-padded (4-digit main + 4-digit sub) string for the same input
this round -- confirmed by FOUR independent official NLSC examples
(CAD_001 "00010000", CAD_004 "05090000", CAD_007 "08920000", CAD_010
"09770001"~"09770006") -- but this is a fact about two systems'
conventions happening to coincide today, not a guarantee that will always
hold. Reusing `LandNumberNormalizer` directly for NLSC would make a future
reader unable to tell "this 8-digit string is NTPC's lid" from "this
8-digit string is NLSC's No" just by looking at which class produced it --
exactly the kind of cross-system conflation Phase API-1.5 already had to
fix once (price_segment_code vs section_name). This module exists so NLSC's
own contract has its own name, even though the arithmetic is currently
identical.
"""
from __future__ import annotations

from typing import Optional


class NlscLandNumberEncoderError(Exception):
    pass


class NlscLandNumberEncoder:
    """Stateless; every method is a pure function of its inputs."""

    @staticmethod
    def encode(main: int, sub: Optional[int] = None) -> str:
        """Encodes (main, sub) into NLSC CAD API's documented 8-digit
        format: 4-digit main + 4-digit sub, both zero-padded (per the four
        official examples in this module's docstring). `sub=None` is
        treated as 0 (a land number with no sub-number, e.g. "489地號"
        alone, per this project's established convention -- see
        providers/cadastral_identifier.py's identical treatment for the
        NTPC contract).

        Raises NlscLandNumberEncoderError for negative or >9999 values --
        never silently truncates or wraps, since a malformed encode here
        would produce a plausible-looking-but-wrong 8-digit string that
        CAD_001 would either reject or, worse, silently match a different
        real parcel."""
        if sub is None:
            sub = 0
        if main < 0 or sub < 0:
            raise NlscLandNumberEncoderError(f"main/sub不得為負數：main={main}, sub={sub}")
        if main > 9999 or sub > 9999:
            raise NlscLandNumberEncoderError(f"main/sub超過NLSC 4碼欄位上限9999：main={main}, sub={sub}")
        return f"{main:04d}{sub:04d}"

    @staticmethod
    def is_already_encoded(value: str) -> bool:
        """True iff `value` already matches NLSC's 8-digit format (8 ASCII
        digits) -- lets a caller pass through an already-correctly-encoded
        string without re-deriving it from a parsed main/sub pair, while
        still rejecting anything that merely LOOKS numeric but isn't
        exactly 8 digits (e.g. a 7-digit NTPC-style `id`, or a "簡式" short
        form some other NLSC API accepts -- see CadasAttrQuery's
        documented "8碼或簡式" alternative, which this encoder does NOT
        produce or accept, since CAD_001/CAD_004's own examples only ever
        show the 8-digit form)."""
        return isinstance(value, str) and len(value) == 8 and value.isdigit()
