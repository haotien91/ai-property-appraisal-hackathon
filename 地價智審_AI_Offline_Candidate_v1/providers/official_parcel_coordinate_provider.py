# -*- coding: utf-8 -*-
"""
official_parcel_coordinate_provider.py — Phase API-2.3 / API-2.3H: CAD_001
(CadasMapPosition) Auth-Gated Provider.

Per Phase API-2.2R2's live audit, CAD_001 requires an NLSC application/
credential (unlike ListCounty/ListTown/ListLandSection, which are
genuinely open -- see providers/nlsc_cadastral_code_resolver.py). This
round does NOT have a real, approved credential, does NOT request one,
and does NOT guess one. `RealOfficialParcelCoordinateProvider` therefore
implements a real, structurally-complete request/response path (unit-
tested against documented-example fixtures, see tests/fixtures/nlsc/),
but is gated behind THREE independent conditions, all required:

  1. NLSC_CAD_API_ENABLED (env var, default "false") -- a feature flag,
     independent of DATA_PROVIDER_MODE=real. Real mode alone must NEVER
     imply "call CAD_001"; this round's explicit instruction.
  2. CAD001_AUTH_CONTRACT_STATUS (Phase API-2.3H §2-3, a SOURCE-CODE
     constant below, NOT an env var) == "VERIFIED", gated additionally by
     the NLSC_CAD_AUTH_CONTRACT_VERIFIED env var. Re-auditing NLSC's own
     現行「國土測繪圖資服務雲介接服務技術手冊」(96 pages, full-text
     searched this round) and the 115年度訂閱申請書之一般申請說明文件
     found ZERO mention of Username/Password/Token/MD5/Authorization/
     HTTP header/query-parameter based credentials anywhere for ANY API
     in the family, including CAD_001 -- i.e. there is no documented
     REQUEST-LEVEL credential scheme this code could encode
     (CAD001_REQUEST_LEVEL_AUTH = NO_DOCUMENTED_CREDENTIAL_SCHEME /
     UNCONFIRMED). Separately, per Phase API-2.3F's correction of an
     over-inference in Phase API-2.3H: the general application document's
     十、其他注意事項(二)綁定IP section only walks through the IP
     REGISTRATION procedure for applicants who use IP binding -- it does
     NOT claim IP binding is CAD_001's only supported binding mode. The
     actual per-API binding-capability table lives in a separate
     attachment ("申請服務介接說明表", referenced but not itself fetched in
     an earlier round) which, per this round's information, lists CAD_001
     as supporting BOTH URL binding AND IP binding
     (CAD001_BINDING_CAPABILITY = URL_OR_IP_SUPPORTED). Regardless of
     which binding CAPABILITY exists, this project has not applied and
     has no approval, so which mode would actually be granted stays
     UNCONFIRMED (CAD001_APPROVED_BINDING_FOR_THIS_PROJECT), and whether a
     fixed AWS egress IP would even be required is therefore CONDITIONAL
     (AWS_FIXED_EGRESS_IP_REQUIRED) rather than an unconditional yes.
     NONE of this -- IP binding, URL binding, or anything else documented
     -- is a client-supplied credential this Python process could send in
     a header or parameter; binding (whichever mode) is enforced by NLSC's
     own server against registration data, not by anything in the HTTP
     request body. Phase API-2.3's original `X-NLSC-Username`/
     `X-NLSC-Token` HTTP headers were therefore an UNDOCUMENTED GUESS with
     no basis in any NLSC material, and have been REMOVED (Phase API-2.3H
     §3: "不要保留任何猜測性的...live behavior") and MUST NOT be
     reintroduced. CAD001_AUTH_CONTRACT_STATUS stays "UNCONFIRMED" until a
     human, having obtained a real approved NLSC application and observed
     real traffic, edits this exact constant -- never flip it based on
     inference alone, and never via an env var alone (Phase API-2.3F §2:
     NLSC_CAD_AUTH_CONTRACT_VERIFIED=true must NEVER be used in production
     to manually bypass this gate -- it exists for dependency-injected
     tests of the parser/request-builder only).
  3. NLSC_CAD_API_USERNAME / NLSC_CAD_API_TOKEN (env vars) -- kept as
     generic placeholder "required credential/config present" names for
     forward compatibility (Phase API-2.3H §4's third condition), in case
     a real application response ever reveals an actual client-side
     credential mechanism. NEVER hardcoded in this file, never committed,
     and (per point 2) never actually transmitted as an HTTP header today,
     since this codebase does not know what the real transport would be.

Whenever any gate is closed, `query_parcel_coordinate()` returns
`ParcelCoordinateStatus.AUTH_REQUIRED` (flag off / credentials missing) or
`ParcelCoordinateStatus.AUTH_CONTRACT_UNVERIFIED` (flag on + credentials
present, but the auth transport itself is still unconfirmed) -- NEVER a
silent fallback to Nominatim, a demo coordinate, or Mock data reported as
if it were this Provider's own success. This is the single most important
invariant this module exists to enforce (see Phase API-2.3's own opening
line: "沒有NLSC key -> 偷偷Nominatim -> 假裝官方" is exactly the failure
mode being guarded against). `county_code`/`section_code`/`nlsc_land_no`
are still populated on an AUTH_REQUIRED/AUTH_CONTRACT_UNVERIFIED result
whenever identifier resolution succeeded, proving the failure is at the
AUTHORIZATION layer, not the IDENTIFIER RESOLUTION layer.

Phase API-2.3H §5-6 (Negative Result Semantics): NLSC's technical manual
documents only ONE CAD_001 response shape -- the SUCCESS example. It never
documents what a "parcel not found" response looks like
(CAD001_DOCUMENTED_NOT_FOUND_RESPONSE = "UNCONFIRMED" below). This module
therefore no longer maps HTTP 404 / an empty HTTP 200 body / a parsed XML
response missing repX-repY to `ParcelCoordinateStatus.NOT_FOUND` -- doing
so would assert "official查無宗地" from evidence that only proves "the API
responded in a way we cannot confidently interpret". See
`parse_cadas_map_position_response()` for the corrected mapping.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from base import DataProvider, ProviderContext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    NormalizedDataPoint, OfficialParcelCoordinateEvidence, ParcelCoordinateStatus, ParcelCoordinateSemantics,
    TargetCoordinateEvidence, CoordinateSourceType, CoordinateAuthoritativeStatus,
)

from cadastral_identifier import CadastralParcelIdentifierParser, ParcelIdentifierParseStatus  # noqa: E402
from nlsc_cadastral_code_resolver import NlscCadastralCodeResolver  # noqa: E402
from nlsc_code_cache import NlscCodeCache  # noqa: E402
from nlsc_land_number_encoder import NlscLandNumberEncoder, NlscLandNumberEncoderError  # noqa: E402
from domain.models import SectionCodeMatchStatus  # noqa: E402

DATASET_NAME = "國土測繪圖資服務雲 CAD_001 CadasMapPosition"
SOURCE_AUTHORITY = "內政部國土測繪中心"
API_HOST = "https://api.nlsc.gov.tw"
REQUEST_TIMEOUT_S = 10.0

# Phase API-2.3H §2-3/§16 Release Gate field. A SOURCE-CODE constant, NOT
# an env var, deliberately -- flipping this to "VERIFIED" must be a
# reviewed code change made by a human who has actually seen NLSC's real
# documented (or observed) CAD_001 auth transport, never an environment
# toggle a deployment could set on a guess. See module docstring for the
# full re-audit this round performed (96-page technical manual + 115年度
# 訂閱申請書一般申請說明 full-text searched for Username/Password/Token/
# MD5/Authorization/Header/Query Parameter -- zero matches for a
# REQUEST-LEVEL credential scheme).
CAD001_AUTH_CONTRACT_STATUS = "UNCONFIRMED"
CAD001_DOCUMENTED_NOT_FOUND_RESPONSE = "UNCONFIRMED"

# Phase API-2.3F §1/§16 Release Gate fields -- corrects Phase API-2.3H's
# over-inference that "IP binding" was CAD_001's ONLY supported binding
# mode (that claim came from a document section that only explains the IP
# REGISTRATION procedure, not an exhaustive list of supported modes; see
# module docstring and docs/phase9/official_parcel_coordinate_audit.md
# §S15.1's correction). None of these four constants describe a
# request-level credential this code could send -- binding (whichever
# mode NLSC approves) is enforced entirely server-side against
# registration data.
CAD001_REQUEST_LEVEL_AUTH = "NO_DOCUMENTED_CREDENTIAL_SCHEME"  # equivalently UNCONFIRMED
CAD001_BINDING_CAPABILITY = "URL_OR_IP_SUPPORTED"
CAD001_APPROVED_BINDING_FOR_THIS_PROJECT = "UNCONFIRMED"
AWS_FIXED_EGRESS_IP_REQUIRED = "CONDITIONAL"

# Feature flag + auth-contract gate + credential config contract (Phase
# API-2.3 §11-12, hardened Phase API-2.3H §3-4) -- NEVER hardcoded values
# here, only the ENV VAR NAMES. Absence of real values is the expected,
# correct state in this round's environment.
ENV_ENABLED = "NLSC_CAD_API_ENABLED"
ENV_AUTH_CONTRACT_VERIFIED = "NLSC_CAD_AUTH_CONTRACT_VERIFIED"
ENV_USERNAME = "NLSC_CAD_API_USERNAME"
ENV_TOKEN = "NLSC_CAD_API_TOKEN"

MOCK_SOURCE = "查估書表範本.pdf 表1（Golden Case，案號1140901-99-001）"


def is_feature_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "false").strip().lower() == "true"


def is_auth_contract_verified() -> bool:
    """Phase API-2.3H §3-4: TWO independent locks, both required --
    the CAD001_AUTH_CONTRACT_STATUS source-code constant (only a human
    code-reviewed change can ever set it to "VERIFIED") AND the
    NLSC_CAD_AUTH_CONTRACT_VERIFIED env var. Neither one alone is
    sufficient: an operator cannot enable live calls merely by setting an
    env var while this codebase's own documented findings still say
    UNCONFIRMED, and this constant alone (even if someone edited it
    without updating deployment config) still requires an explicit env
    var before any deployment actually starts calling out."""
    if CAD001_AUTH_CONTRACT_STATUS != "VERIFIED":
        return False
    return os.environ.get(ENV_AUTH_CONTRACT_VERIFIED, "false").strip().lower() == "true"


def get_credentials() -> "tuple[Optional[str], Optional[str]]":
    """Reads credential config from environment only -- never a hardcoded
    default, never a guessed value. Returns (username, token), either of
    which may be None. See module docstring: this is a forward-compatible
    placeholder name, not a confirmed NLSC field -- these values are
    checked for presence but (per is_auth_contract_verified()'s gate)
    never actually sent to CAD_001 until a real, documented transport is
    confirmed."""
    return os.environ.get(ENV_USERNAME), os.environ.get(ENV_TOKEN)


def build_cadas_map_position_request(county_code: str, section_code: str, nlsc_land_no: str,
                                      output_crs: str = "4326") -> dict:
    """Pure, deterministic, no I/O -- fully unit-testable without
    credentials. Mirrors CAD_001's documented path-parameter contract
    (Phase API-2.2R2): /{City}/{Sec}/{No}[/{CRS}]. `output_crs` is ALWAYS
    passed explicitly (never omitted to rely on NLSC's "預設經緯度"
    default) -- being explicit about which CRS was requested is safer and
    more testable than depending on documented-but-implicit default
    behavior."""
    if output_crs not in ("4326", "3826"):
        raise ValueError(f"CadasMapPosition僅支援4326或3826，收到={output_crs!r}")
    endpoint = f"{API_HOST}/dmaps/CadasMapPosition/{county_code}/{section_code}/{nlsc_land_no}/{output_crs}"
    return {"endpoint": endpoint, "method": "GET", "params": {}, "output_crs": output_crs}


def parse_cadas_map_position_response(*, http_status: Optional[int], body: Optional[bytes],
                                       output_crs: str, network_error: Optional[str] = None) -> dict:
    """Pure function: (http_status, body, requested CRS) -> a dict with
    `status` (ParcelCoordinateStatus) plus whichever of repX/repY/ldX/ldY/
    rtX/rtY were parseable. NEVER raises for a malformed/unexpected
    response -- every branch returns a status, matching this codebase's
    "no provider crashes the pipeline" convention.

    IMPORTANT HONESTY NOTE (Phase API-2.3H §5-6, hardened from Phase
    API-2.3): NLSC's public technical documentation only shows a SUCCESS
    example for CAD_001 -- no documented error-response example exists for
    "parcel not found"/"invalid section"/"auth failure"/etc
    (CAD001_DOCUMENTED_NOT_FOUND_RESPONSE = "UNCONFIRMED"). Core principle
    this round enforces: **an unusual API response is NOT the same claim
    as "official查無宗地"**. Concretely, this function no longer maps HTTP
    404 / an empty HTTP 200 body / a parsed-but-fields-missing XML
    response to `ParcelCoordinateStatus.NOT_FOUND` -- those now map to
    `UNVERIFIED_RESPONSE` / `EMPTY_RESPONSE` / `UNVERIFIED_RESPONSE`
    respectively, each honestly labeled as "we cannot confidently
    interpret this", never "NLSC confirmed no such parcel". Malformed XML
    now maps to the dedicated `PARSE_FAILED` (previously folded into
    SERVICE_UNAVAILABLE, which conflated "we could not parse the body"
    with "the server appears to be down"). The remaining branches
    (401/403->AUTH_REQUIRED, 400->INVALID_REQUEST, >=500->
    SERVICE_UNAVAILABLE) are still REASONABLE, CONVENTIONAL REST API
    defaults, NOT NLSC-documented behavior -- unchanged from Phase
    API-2.3, to be refined once a real credential allows observing actual
    error responses."""
    if network_error is not None:
        return {"status": ParcelCoordinateStatus.SERVICE_UNAVAILABLE,
                "notes": f"網路請求失敗：{network_error}"}
    if http_status is None:
        return {"status": ParcelCoordinateStatus.UNKNOWN, "notes": "無HTTP狀態碼可供判讀"}
    if http_status in (401, 403):
        return {"status": ParcelCoordinateStatus.AUTH_REQUIRED,
                "notes": f"HTTP {http_status}：認證失敗或無存取權限（此為REST慣例推論，非NLSC官方文件明文之錯誤格式）"}
    if http_status == 400:
        return {"status": ParcelCoordinateStatus.INVALID_REQUEST,
                "notes": "HTTP 400：請求參數格式錯誤（縣市代碼/地段代碼/地號格式不符）"}
    if http_status == 404:
        return {
            "status": ParcelCoordinateStatus.UNVERIFIED_RESPONSE,
            "notes": (
                "HTTP 404：NLSC官方文件未證實此代表查無地號"
                "（CAD001_DOCUMENTED_NOT_FOUND_RESPONSE=UNCONFIRMED），"
                "故不得標記為NOT_FOUND，僅記錄為未經證實之回應狀態，需人工確認。"
            ),
        }
    if http_status >= 500:
        return {"status": ParcelCoordinateStatus.SERVICE_UNAVAILABLE, "notes": f"HTTP {http_status}：NLSC服務端錯誤"}
    if http_status != 200:
        return {"status": ParcelCoordinateStatus.UNKNOWN, "notes": f"未預期之HTTP狀態碼：{http_status}"}

    if not body:
        return {
            "status": ParcelCoordinateStatus.EMPTY_RESPONSE,
            "notes": "HTTP 200但回應內容為空。NLSC官方文件未證實空回應代表查無此地號，不得標記為NOT_FOUND，需人工確認。",
        }
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return {"status": ParcelCoordinateStatus.PARSE_FAILED,
                "notes": f"XML解析失敗（malformed XML）：{e}，無法確認是否查無資料或服務異常，需人工確認。"}

    def _find_float(tag: str) -> Optional[float]:
        el = root.find(tag)
        if el is None or el.text is None:
            return None
        try:
            return float(el.text.strip())
        except ValueError:
            return None

    rep_x, rep_y = _find_float("repX"), _find_float("repY")
    ld_x, ld_y = _find_float("ldX"), _find_float("ldY")
    rt_x, rt_y = _find_float("rtX"), _find_float("rtY")

    if rep_x is None or rep_y is None:
        return {
            "status": ParcelCoordinateStatus.UNVERIFIED_RESPONSE,
            "notes": (
                "XML成功解析但缺少repX/repY欄位。NLSC未公開此情境之官方回應範例，"
                "不得推論為查無此地號，僅記錄為未經證實之回應狀態，需人工確認。"
            ),
        }

    return {
        "status": ParcelCoordinateStatus.SUCCESS,
        "rep_x": rep_x, "rep_y": rep_y, "ld_x": ld_x, "ld_y": ld_y, "rt_x": rt_x, "rt_y": rt_y,
        "output_crs": output_crs,
    }


class MockOfficialParcelCoordinateProvider(DataProvider):
    """Mock Mode: never touches the network, never touches the NLSC code
    cache. Matches this codebase's established Mock convention (Phase
    API-1/API-2): honestly UNKNOWN, never a fabricated-but-plausible
    coordinate."""
    provider_name = "MockOfficialParcelCoordinateProvider"

    def fetch(self, ctx: ProviderContext) -> list:
        now = datetime.now()
        return [NormalizedDataPoint(
            field="official_parcel_coordinate_status", value=ParcelCoordinateStatus.UNKNOWN.value,
            unit=None, source=MOCK_SOURCE, source_type="Mock", coordinate=None,
            confidence="UNKNOWN", retrieved_at=now,
            notes="Mock Mode：本Provider於Mock Mode不呼叫任何網路服務或本地snapshot，誠實回傳UNKNOWN。",
        )]

    def query_parcel_coordinate(self, ctx: ProviderContext) -> OfficialParcelCoordinateEvidence:
        return OfficialParcelCoordinateEvidence(
            status=ParcelCoordinateStatus.UNKNOWN,
            city=ctx.city, district=ctx.district,
            coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
            confidence="UNKNOWN", requires_manual_review=True,
            notes="Mock Mode：未呼叫真實資料，無官方查證結果可供示範。",
        )


class RealOfficialParcelCoordinateProvider(DataProvider):
    """Real path never falls back to Mock, and (this round's central
    invariant) never falls back to Nominatim/demo-coordinate while
    reporting the result as this Provider's own official finding. `cache`/
    `code_resolver` are DI-injectable for tests."""
    provider_name = "RealOfficialParcelCoordinateProvider"

    def __init__(self, cache: Optional[NlscCodeCache] = None,
                 code_resolver: Optional[NlscCadastralCodeResolver] = None):
        # RUNTIME_READ_ONLY by default (Phase API-2.3H §9): this Provider
        # only ever runs in the Lambda request path, which must never
        # attempt to create/migrate the NLSC code cache -- see nlsc_code_
        # cache.py's docstring.
        self._cache = cache or NlscCodeCache(read_only=True)
        self._code_resolver = code_resolver or NlscCadastralCodeResolver(cache=self._cache)

    def fetch(self, ctx: ProviderContext) -> list:
        now = datetime.now()
        evidence = self.query_parcel_coordinate(ctx)
        return [NormalizedDataPoint(
            field="official_parcel_coordinate_status", value=evidence.status.value,
            unit=None, source=evidence.source_url or DATASET_NAME, source_type="GovernmentOpenData",
            coordinate=None, confidence=evidence.confidence, retrieved_at=now, notes=evidence.notes,
        )]

    def query_parcel_coordinate(self, ctx: ProviderContext) -> OfficialParcelCoordinateEvidence:
        """PRIMARY path: district/section/land_no -> NlscCadastralCodeResolver
        (open, no-auth, local snapshot) -> NlscLandNumberEncoder (pure) ->
        [Auth Gate] -> CAD_001 (only if gate open)."""
        retrieved_at = datetime.now()

        parsed = CadastralParcelIdentifierParser.parse(ctx.parcel_id)
        if parsed.parse_status == ParcelIdentifierParseStatus.PARSE_UNCERTAIN:
            return OfficialParcelCoordinateEvidence(
                status=ParcelCoordinateStatus.UNKNOWN, city=ctx.city, district=ctx.district,
                land_no_raw=parsed.raw_input, coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=f"地籍識別字串解析失敗（PARSE_UNCERTAIN）：{parsed.notes}",
            )

        section_evidence = self._code_resolver.resolve(ctx.city, ctx.district, parsed.section_name)
        if section_evidence.match_status != SectionCodeMatchStatus.FOUND:
            # NOT_FOUND/AMBIGUOUS both map to ParcelCoordinateStatus.UNKNOWN
            # -- the distinction between "no such section" and "multiple
            # candidate sections, needs a human" is NOT lost, it is fully
            # preserved in `notes` (via section_evidence.notes) and in
            # section_evidence.match_status itself for any caller that
            # inspects the NlscSectionCodeEvidence directly. OUT_OF_COVERAGE
            # (Phase API-2.3H §7) maps to its OWN distinct
            # ParcelCoordinateStatus instead -- "this county's sections were
            # never synced" must never look like "resolved, but ambiguous/
            # not found" to a caller only inspecting the top-level status.
            status = (
                ParcelCoordinateStatus.OUT_OF_COVERAGE
                if section_evidence.match_status == SectionCodeMatchStatus.OUT_OF_COVERAGE
                else ParcelCoordinateStatus.UNKNOWN
            )
            return OfficialParcelCoordinateEvidence(
                status=status,
                city=ctx.city, district=ctx.district, section_name=parsed.section_name,
                land_no_raw=parsed.raw_input,
                county_code=section_evidence.city_code, town_code=section_evidence.town_code,
                coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"地段代碼解析未成功（{section_evidence.match_status.value}），無法組成CAD_001請求。"
                    f"{section_evidence.notes or ''}"
                ),
            )

        try:
            nlsc_land_no = NlscLandNumberEncoder.encode(parsed.land_no_main, parsed.land_no_sub)
        except NlscLandNumberEncoderError as e:
            return OfficialParcelCoordinateEvidence(
                status=ParcelCoordinateStatus.UNKNOWN, city=ctx.city, district=ctx.district,
                section_name=parsed.section_name, land_no_raw=parsed.raw_input,
                county_code=section_evidence.city_code, town_code=section_evidence.town_code,
                section_code=section_evidence.section_code,
                coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=f"地號編碼失敗：{e}",
            )

        base_fields = dict(
            city=ctx.city, district=ctx.district, section_name=parsed.section_name,
            land_no_raw=parsed.raw_input, county_code=section_evidence.city_code,
            town_code=section_evidence.town_code, section_code=section_evidence.section_code,
            nlsc_land_no=nlsc_land_no, source_authority=SOURCE_AUTHORITY, retrieved_at=retrieved_at,
        )

        # --- Auth Gate (Phase API-2.3 §10-12, hardened Phase API-2.3H
        # §3-4): the invariant this whole module exists to enforce.
        # Identifier resolution above has ALREADY succeeded (county_code/
        # town_code/section_code/nlsc_land_no are all populated in
        # base_fields) -- proving a refusal here is at the AUTHORIZATION
        # layer, never the identifier-resolution layer. THREE independent
        # conditions, checked in this exact order, ALL required before any
        # network call:
        if not is_feature_enabled():
            return OfficialParcelCoordinateEvidence(
                status=ParcelCoordinateStatus.AUTH_REQUIRED, coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"NLSC_CAD_API_ENABLED未設為true（功能旗標關閉）。地號解析已成功"
                    f"（county_code={section_evidence.city_code}, town_code={section_evidence.town_code}, "
                    f"section_code={section_evidence.section_code}, nlsc_land_no={nlsc_land_no}），"
                    "失敗發生於授權層，非地籍識別解析層。絕不因此silent fallback至Nominatim或demo座標。"
                ),
                **base_fields,
            )
        # Checked BEFORE credentials, per Phase API-2.3H §3's explicit
        # instruction: even if NLSC_CAD_API_ENABLED=true AND credentials
        # are present, an unverified auth contract must still block the
        # network call -- this codebase has no documented basis for what
        # a real CAD_001 request's auth transport even looks like (see
        # module docstring's 96-page manual + 訂閱申請書 re-audit).
        if not is_auth_contract_verified():
            return OfficialParcelCoordinateEvidence(
                status=ParcelCoordinateStatus.AUTH_CONTRACT_UNVERIFIED,
                coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"CAD001_AUTH_CONTRACT_STATUS={CAD001_AUTH_CONTRACT_STATUS}"
                    "（NLSC官方技術手冊與115年度訂閱申請書皆未證實CAD_001之實際"
                    "runtime認證傳輸機制，僅證實綁定IP/URL為network層存取控制，"
                    "非client端可傳送之憑證）。地號解析已成功"
                    f"（county_code={section_evidence.city_code}, town_code={section_evidence.town_code}, "
                    f"section_code={section_evidence.section_code}, nlsc_land_no={nlsc_land_no}），"
                    "失敗發生於授權契約未確認層，非地籍識別解析層。絕不因此送出任何猜測性認證標頭，"
                    "亦絕不silent fallback至Nominatim或demo座標。NO NETWORK CALL。"
                ),
                **base_fields,
            )
        username, token = get_credentials()
        if not username or not token:
            return OfficialParcelCoordinateEvidence(
                status=ParcelCoordinateStatus.AUTH_REQUIRED, coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                notes=(
                    f"NLSC_CAD_API_USERNAME/NLSC_CAD_API_TOKEN未設定（缺少憑證）。地號解析已成功"
                    f"（county_code={section_evidence.city_code}, town_code={section_evidence.town_code}, "
                    f"section_code={section_evidence.section_code}, nlsc_land_no={nlsc_land_no}），"
                    "失敗發生於授權層，非地籍識別解析層。絕不因此silent fallback至Nominatim或demo座標。"
                ),
                **base_fields,
            )

        # Auth Gate fully open (feature enabled + auth contract VERIFIED +
        # credentials present) -- this round's CAD001_AUTH_CONTRACT_STATUS
        # constant is hardcoded "UNCONFIRMED" (see module docstring), so
        # is_auth_contract_verified() can never return True today and this
        # branch is structurally unreachable in this round's code as
        # shipped; it exists as real, tested code (via monkeypatched
        # network calls) for when a real, documented credential mechanism
        # is confirmed in a future, separately-approved round. Note this
        # request carries NO auth-related header/parameter of any kind --
        # exactly matching the documented CAD_001 request contract
        # (path-only: /{City}/{Sec}/{No}/{CRS}), since no client-side
        # credential transport is documented to exist. If/when
        # CAD001_AUTH_CONTRACT_STATUS is genuinely set to "VERIFIED", this
        # function MUST be updated to match whatever the REAL confirmed
        # mechanism is -- never guessed in advance.
        request = build_cadas_map_position_request(
            section_evidence.city_code, section_evidence.section_code, nlsc_land_no, output_crs="4326"
        )
        http_status, body, network_error = None, None, None
        try:
            req = urllib.request.Request(request["endpoint"], headers={
                "User-Agent": "ai-valuation-review-competition-tool/1.0",
            })
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                http_status = resp.status
                body = resp.read()
        except urllib.error.HTTPError as e:
            http_status = e.code
            body = e.read() if e.fp else None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            network_error = str(e)

        parsed_response = parse_cadas_map_position_response(
            http_status=http_status, body=body, output_crs="4326", network_error=network_error
        )
        return self._to_evidence(parsed_response, base_fields, request["endpoint"])

    @staticmethod
    def _to_evidence(parsed_response: dict, base_fields: dict, source_url: str) -> OfficialParcelCoordinateEvidence:
        status = parsed_response["status"]
        if status != ParcelCoordinateStatus.SUCCESS:
            return OfficialParcelCoordinateEvidence(
                status=status, coordinate_semantics=ParcelCoordinateSemantics.UNKNOWN,
                confidence="UNKNOWN", requires_manual_review=True,
                source_url=source_url, notes=parsed_response.get("notes"), **base_fields,
            )
        output_crs = parsed_response["output_crs"]
        rep_x, rep_y = parsed_response["rep_x"], parsed_response["rep_y"]

        if output_crs == "4326":
            # repX/repY ARE already WGS84 lon/lat -- still preserved
            # separately as source_coordinate_x/y per this round's "raw
            # value never overwritten" instruction.
            latitude, longitude = rep_y, rep_x
            crs_notes = ""
        else:
            # output_crs == "3826" (TWD97 TM2, meters, NOT degrees).
            # query_parcel_coordinate() above ALWAYS requests "4326" for
            # the real, live call (CAD_001 confirmed to support it
            # directly -- Phase API-2.2R2), specifically so this branch
            # is never exercised at runtime. Deliberately NOT doing a
            # pyproj reprojection here: this module is a Lambda request-
            # path provider, and this codebase's established convention
            # (see backend/requirements-sync.txt's comment, and
            # providers/facility_dataset_cache.py) is that pyproj-based
            # TWD97->WGS84 conversion happens ONCE at sync/ETL time, out
            # of the Lambda runtime, never per-request -- pyproj is not
            # even declared in backend/requirements.txt for this reason.
            # So rather than either (a) silently mislabeling TWD97 meters
            # as WGS84 degrees, or (b) adding a new Lambda runtime
            # dependency for a code path this round never calls, this
            # branch honestly leaves latitude/longitude unset and
            # explains why in `notes`. This function (and the request
            # builder's `output_crs="3826"` option) still exists and is
            # unit-tested for contract completeness/documentation fidelity.
            latitude, longitude = None, None
            crs_notes = (
                "output_crs=3826（TWD97 TM2，公尺）：本Provider之即時查詢路徑一律以4326直接請求"
                "（CAD_001官方確認支援），刻意不在Lambda request path中進行pyproj重新投影"
                "（重新投影僅於sync-time ETL執行一次，見backend/requirements-sync.txt），"
                "故此處latitude/longitude保持未設定，僅保留source_coordinate_x/y原始值。"
            )
        return OfficialParcelCoordinateEvidence(
            status=ParcelCoordinateStatus.SUCCESS,
            latitude=latitude, longitude=longitude,
            source_coordinate_x=rep_x, source_coordinate_y=rep_y,
            source_crs=f"EPSG:{output_crs}",
            coordinate_semantics=ParcelCoordinateSemantics.OFFICIAL_PARCEL_REPRESENTATIVE_POINT,
            authoritative_status="OFFICIAL", confidence="高", requires_manual_review=True,
            source_url=source_url,
            notes=("CAD_001成功回傳官方宗地代表點（非centroid，NLSC官方文件僅稱代表點）。" + crs_notes),
            **base_fields,
        )


def to_target_coordinate_evidence(evidence: OfficialParcelCoordinateEvidence) -> Optional[TargetCoordinateEvidence]:
    """Phase API-2.3 §16 Facility Integration glue: converts a SUCCESSFUL
    OfficialParcelCoordinateEvidence into the TargetCoordinateEvidence
    contract Phase API-2.1 already established -- OfficialFacilityProvider
    itself is NOT modified; it already knows how to honor
    `authoritative_status=OFFICIAL`. Returns None for any non-SUCCESS
    status (AUTH_REQUIRED included) -- a caller must not construct a
    TargetCoordinateEvidence at all in that case, not one that quietly
    claims some other, lesser authority."""
    if evidence.status != ParcelCoordinateStatus.SUCCESS:
        return None
    return TargetCoordinateEvidence(
        latitude=evidence.latitude, longitude=evidence.longitude,
        source_type=CoordinateSourceType.OFFICIAL_GIS,
        source_authority=evidence.source_authority, source_url=evidence.source_url,
        authoritative_status=CoordinateAuthoritativeStatus.OFFICIAL,
        precision_level="PARCEL", retrieved_at=evidence.retrieved_at,
        notes="來自NLSC CAD_001官方宗地代表點座標。",
    )
