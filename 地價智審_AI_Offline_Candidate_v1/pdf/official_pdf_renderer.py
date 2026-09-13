# -*- coding: utf-8 -*-
"""
official_pdf_renderer.py — PDF-OFFICIAL-1 Task 4.

Overlays an already-computed Structured Result (CompetitionCase +
regional_base_factors/regional_comparable_factors + FormCompletionResult)
onto data/templates/official_appraisal_form_v1.pdf using PyMuPDF, driven
entirely by data/templates/official_appraisal_form_v1.json's coordinates.

DATA FLOW (STEP "PDF-OFFICIAL-1" Task 4's explicit requirement):

    Engine Result -> Form Completion Result -> Official PDF Renderer

NEVER the reverse. This module imports NOTHING from engine/ (no
RuleEngine, GradeEngine, AdjustmentEngine, CalculationEngine,
ComparableSelectionEngine, CaseRuleRepository, EvaluationStandardImporter,
AI Semantic Mapping Provider, GIS Provider) -- it only reads:
  - domain.models.CompetitionCase / FactorInput (raw, already-collected
    facts -- 表1/表4's "raw survey value" fields)
  - a plain dict shaped like FormCompletionResult.model_dump() (表5-2's
    already-computed grade/adjustment-percent fields, 表4's already-
    computed price fields) -- passed in by the caller, never recomputed
    here.

Every value written is either:
  (a) read directly from one of those two inputs by an EXACT field_id/
      factor match (no re-derivation), or
  (b) left blank, when the Structured Result genuinely has no matching
      field -- NEVER fabricated, NEVER a guess (STOP/NO-GO rule 4).

TEMPLATE FINGERPRINT GUARD (Task 10): render_official_pdf() re-hashes
data/templates/official_appraisal_form_v1.pdf on every call and compares
it against the profile's own recorded clean_template_sha256 -- a mismatch
(the template file changed without regenerating the profile) raises
TemplateLayoutUnconfirmedError rather than blind-filling coordinates that
may no longer correspond to the template's actual layout.

FACILITY-CONFIRMATION-GATE-1: utility (substation/gas_tank), funeral
(cemetery/funeral_home/crematorium/columbarium), and major_station
(MRT/TRA) rows are the ONE exception to "reads only case/regional_base_
factors/form_completion_fields" above -- this module never reads raw
provider evidence for those 8 rows at all anymore. It reads ONLY
`confirmed_facility_selections` (a dict of already-human-confirmed
selections, built by pdf_handler.py via facility_confirmation_
repository.py) -- never FACTORS.points' substation_name/gas_tank_name/
.../official_facility_evidence["STATION"] directly. See render_official_
pdf()'s own docstring for the exact contract. road_name (facility_points'
"main_road_name") is NOT part of this gate (TABLE1-SAFE-WIRING-1's
original, simpler wiring, unchanged).
"""
from __future__ import annotations

import ctypes.util
import hashlib
import json
import os
import platform
from decimal import Decimal
from typing import Any, Dict, List, Optional

import fitz

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
DEFAULT_TEMPLATE_PATH = os.path.join(_REPO_ROOT, "data", "templates", "official_appraisal_form_v1.pdf")
DEFAULT_PROFILE_PATH = os.path.join(_REPO_ROOT, "data", "templates", "official_appraisal_form_v1.json")

# OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 7/8/9 (tightened by
# OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 1/2): pages 3-5 (地價區段略圖/
# 使用分區圖/地價區段圖) are copied VERBATIM from data/templates/
# official_appraisal_form_v1.pdf -- which is itself derived from the
# official 查估書表範本.pdf, whose own map attachments genuinely depict
# ONE specific real case (the Golden Case this whole competition fixture
# is built around: 案號1140901-99-001, 新北市金山區 P002-00 區段, 比準地
# 金美段489地號, 比較標的 溫泉段218地號 -- and carry Golden-Case-specific
# annotations, not just a generic segment boundary drawing). Matching only
# on city/district/segment_code is NOT sufficient: the SAME P002-00
# segment can legitimately contain multiple different real cases with
# different base/comparable parcels, none of which this template's map
# actually depicts. For any case that isn't a byte-for-byte identity match
# on ALL of case_no + city + district + segment_code + base_parcel_id +
# comparable_ids, silently keeping these 3 pages would attach the WRONG
# case's official map to a real appraisal document -- a genuine data-
# safety risk, not a cosmetic one. This codebase has no per-case GIS map
# generation capability at all (GIS Engine is Core-Frozen and out of this
# round's scope) -- this is a PDF-OUTPUT-ONLY safe fallback: a non-
# matching case gets these 3 pages blanked out with an explicit, honest
# notice instead of a wrong map, NEVER a fabricated or silently-reused one.
_TEMPLATE_MAP_IDENTITY = {
    "case_no": "1140901-99-001",
    "segment_code": "P002-00",
    "city": "新北市",
    "district": "金山區",
    "base_parcel_id": "金美段489地號",
    "comparable_ids": ["溫泉段218地號"],
}
_MAP_PAGE_INDEXES = (3, 4, 5)
_MAP_PAGE_TITLES = {3: "地價區段略圖", 4: "使用分區圖", 5: "地價區段圖"}


def _case_matches_template_map_identity(case) -> bool:
    """True ONLY when this case is, byte-for-byte, the exact Golden Case
    the template's map pages were captured from -- case_no is checked
    FIRST and is REQUIRED (never sufficient alone, but a case_no mismatch
    alone is already disqualifying), then location, then segment, then
    verified parcel identity (base_parcel_id + comparable_ids) since
    CompetitionCase always carries both (never "if available" -- they are
    required fields on this model, see domain/models.py). Same segment_
    code + city + district is explicitly NOT enough on its own (Task 1):
    a different case with a different base/comparable parcel can share
    the same segment, and the template's map is specific to ONE case's
    parcels, not the segment in general."""
    return (
        case.case_no == _TEMPLATE_MAP_IDENTITY["case_no"]
        and case.city == _TEMPLATE_MAP_IDENTITY["city"]
        and case.district == _TEMPLATE_MAP_IDENTITY["district"]
        and case.segment_code == _TEMPLATE_MAP_IDENTITY["segment_code"]
        and case.base_parcel_id == _TEMPLATE_MAP_IDENTITY["base_parcel_id"]
        and list(case.comparable_ids or []) == _TEMPLATE_MAP_IDENTITY["comparable_ids"]
    )


def _apply_map_page_safety(doc: "fitz.Document", case, font_path: str) -> None:
    """Task 8's "safe map policy": Golden-Case-identity cases keep the
    template's own real map pages untouched (Task 9 provenance: a small
    stamped note naming the exact source, so nobody mistakes this for a
    generic official background applicable to any case). Every other case
    gets those 3 pages redacted to blank + a clear, honest Chinese notice
    -- never the Golden Case's map, never a fabricated map."""
    matches = _case_matches_template_map_identity(case)
    for idx in _MAP_PAGE_INDEXES:
        page = doc[idx]
        title = _MAP_PAGE_TITLES[idx]
        if matches:
            note = (
                f"圖資來源：查估書表範本.pdf 原始附圖（新北市金山區 P002-00 區段，"
                f"案號 {case.case_no}）"
            )
        else:
            # Full-page redaction (never a mere white rectangle painted on
            # top -- apply_redactions() actually removes the Golden Case
            # map image/text underneath, including the map IMAGE itself:
            # PDF_REDACT_IMAGE_REMOVE, unlike the NONE used elsewhere in
            # this module for small form-field cells that never contain a
            # background image) -- this is NEVER shown alongside any other
            # case's data.
            page.add_redact_annot(page.rect, fill=(1, 1, 1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
            # apply_redactions() rewrites the page's own resources dict,
            # dropping the "cjk" font reference registered on it earlier --
            # must be re-registered on THIS page before writing the notice.
            page.insert_font(fontname="cjk", fontfile=font_path)
            note = (
                f"{title}：圖資待人工附具（本案件（案號 {case.case_no}／"
                f"{case.city}{case.district} {case.segment_code} 區段）與範本圖資"
                f"（新北市金山區 P002-00 區段）不符，本系統尚無可驗證之對應圖資來源，"
                f"請承辦人另行檢附）"
            )
        rect = fitz.Rect(40, page.rect.height - 40, page.rect.width - 40, page.rect.height - 20)
        page.insert_textbox(rect, note, fontname="cjk", fontsize=7, align=fitz.TEXT_ALIGN_LEFT)


class TemplateLayoutUnconfirmedError(Exception):
    """The on-disk clean template's SHA256 no longer matches the profile
    that was generated from it -- coordinates cannot be trusted. Callers
    (pdf_handler.py) must fall back to the existing Audit PDF, never
    silently fill the (possibly now-wrong) coordinates."""


class FieldOverflowManualReviewError(Exception):
    """A single field's text could not fit its bbox even at the profile's
    min_font_size after wrapping -- Task 9's OVERFLOW POLICY last resort.
    Carries the field_id so a caller can decide whether to skip just that
    field (this renderer's own default) or abort the whole page."""

    def __init__(self, field_id: str, text: str):
        self.field_id = field_id
        self.text = text
        super().__init__(f"FIELD_OVERFLOW_MANUAL_REVIEW: field_id={field_id!r} text={text!r}")


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_profile(profile_path: str = DEFAULT_PROFILE_PATH) -> dict:
    with open(profile_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Font resolution (Task 8). Never bundled in this repo -- resolved from
# whatever CJK font the current runtime already has installed, mirroring
# pdf/pdf_renderer.py's existing WeasyPrint strategy (backend/docker/
# pdf.Dockerfile's `dnf install google-noto-sans-cjk-ttc-fonts`). Noto Sans
# TC is SIL Open Font License 1.1 -- redistributable, but redistribution
# is moot here since we never ship the font file ourselves.
# ---------------------------------------------------------------------------

class FontUnavailableError(Exception):
    """No CJK-capable font file could be located in this environment.
    Raised rather than silently falling back to a non-CJK base14 font,
    which would render every Chinese character as blank boxes (the exact
    "reportlab CID字型...靜默產生空白頁" hazard backend/docker/pdf.
    Dockerfile's own comment warns about for the OTHER renderer)."""


def _resolve_cjk_font_path(profile: dict) -> str:
    candidates = list(profile.get("font", {}).get("lambda_candidates", []))
    win_candidate = profile.get("font", {}).get("windows_dev_candidate")
    if win_candidate:
        candidates.append(win_candidate)
    # A few extra, broadly-present fallbacks (still never bundled):
    if platform.system() == "Windows":
        candidates += [
            r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\mingliu.ttc",
        ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    # Directory scan fallback: the exact filename `google-noto-sans-cjk-
    # ttc-fonts` installs (backend/docker/pdf.Dockerfile's `dnf install`)
    # is a real, but not independently re-verified-this-round, guess --
    # scanning the directories that RPM/dnf font packages install into on
    # Amazon Linux/Fedora-based images (what public.ecr.aws/lambda/python
    # is built on) for ANY CJK-named font file is more robust than a
    # single hardcoded path, without bundling a font file in this repo.
    for font_dir in ("/usr/share/fonts", "/opt/fonts"):
        if not os.path.isdir(font_dir):
            continue
        for root, _dirs, files in os.walk(font_dir):
            for fn in files:
                if ("noto" in fn.lower() and "cjk" in fn.lower()) or "notosanstc" in fn.lower().replace(" ", ""):
                    return os.path.join(root, fn)
    raise FontUnavailableError(
        f"No CJK font file found among candidates: {candidates}, nor via a directory scan of "
        f"/usr/share/fonts or /opt/fonts. On Lambda, ensure the Container Image installs "
        f"google-noto-sans-cjk-ttc-fonts (see backend/docker/pdf.Dockerfile); locally, install a "
        f"Traditional Chinese font (e.g. Noto Sans TC)."
    )


# ---------------------------------------------------------------------------
# Overflow policy (Task 9)
# ---------------------------------------------------------------------------

def _fit_text(page: fitz.Page, text: str, bbox: List[float], font: fitz.Font,
              default_size: float, min_size: float, shrink_step: float, field_id: str,
              align: int = fitz.TEXT_ALIGN_LEFT) -> float:
    """Writes `text` into `bbox`, shrinking font size in `shrink_step`
    decrements down to `min_size` if it doesn't fit on one line; if it
    still doesn't fit, wraps via fitz's own textbox layout at min_size.
    Raises FieldOverflowManualReviewError only if even that overflows the
    box height. Returns the font size actually used."""
    rect = fitz.Rect(*bbox)
    size = default_size
    while size >= min_size:
        width = font.text_length(text, fontsize=size)
        if width <= rect.width:
            page.insert_text((rect.x0, rect.y1 - 1.2), text, fontname="cjk", fontsize=size, render_mode=0)
            return size
        size -= shrink_step
    # Wrap at min_size using insert_textbox.
    rc = page.insert_textbox(rect, text, fontname="cjk", fontsize=min_size, align=align)
    if rc < 0:
        raise FieldOverflowManualReviewError(field_id, text)
    return min_size


def _draw_checkbox_distance(page: fitz.Page, bbox: List[float], font: fitz.Font,
                             label_template: str, is_outside: bool, distance_value: Optional[Any],
                             font_size: float) -> None:
    """Redraws a "○本區段內　○本區段外(距　　M)"-style clause fresh: the
    two checkbox glyphs (one becomes ● per is_outside) plus the distance
    number if given, positioned by simple left-to-right text flow inside
    bbox -- never surgical glyph replacement on the original template
    (that region is already white-boxed by the clean template)."""
    rect = fitz.Rect(*bbox)
    inside_glyph = "○" if is_outside else "●"
    outside_glyph = "●" if is_outside else "○"
    distance_text = "" if distance_value is None else str(distance_value)
    text = f"{inside_glyph}本區段內　{outside_glyph}本區段外(距 {distance_text} M)"
    page.insert_text((rect.x0, rect.y1 - 1.2), text, fontname="cjk", fontsize=font_size, render_mode=0)


def _decimal_str(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    return str(v)


def _overlay_major_station_match(page: fitz.Page, font: fitz.Font, spec: Dict[str, Any],
                                  match: Dict[str, Any], min_size: float) -> None:
    """A major_station row's default text ("無高鐵站"/"無火車站"/
    "無捷運站") is the template's own PERMANENT label, deliberately left
    intact on the clean template for the no-match case (see scripts/
    build_clean_official_template.py). When a real match exists it must
    be REPLACED (never appended alongside) -- exactly how the template's
    own 4th row in this section (客運/國光客運金山站) already
    demonstrates a found match looks: circle flips to ●, default text is
    replaced by the real name. A plain page.insert_text() would draw the
    new name ON TOP of the still-present "無XXX站" glyphs (garbled
    overlap) -- so the circle+name region is genuinely redacted first
    (PyMuPDF's real redaction API, never a draw_rect paint-over -- same
    principle as this repo's own FINAL GATE fix to the clean-template
    build script) before the fresh "●{name}" is written."""
    region = fitz.Rect(*spec["circle_bbox"]) | fitz.Rect(*spec["name_bbox"])
    page.add_redact_annot(region, fill=(1, 1, 1))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    name = str(match.get("name") or "")
    page.insert_text((region.x0, region.y1 - 1.2), f"●{name}", fontname="cjk", fontsize=min_size, render_mode=0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_official_pdf(
    case, regional_base_factors: List, regional_comparable_factors: Dict[str, List],
    form_completion_fields: List[Dict[str, Any]],
    template_path: str = DEFAULT_TEMPLATE_PATH, profile_path: str = DEFAULT_PROFILE_PATH,
    facility_points: Optional[List[Dict[str, Any]]] = None,
    confirmed_facility_selections: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bytes:
    """
    case: domain.models.CompetitionCase (already-reconstructed, e.g. via
          case_reconstruction.build_case_and_regional_factors()).
    regional_base_factors / regional_comparable_factors: the SAME
          FactorInput lists complete_form.py/review.py already use.
    form_completion_fields: FormCompletionResult.fields, as plain dicts
          (e.g. json.loads(FormCompletionResult.model_dump_json())["fields"]
          -- exactly what complete_form.py already stores under the
          FORM_COMPLETION record). NEVER recomputed here.
    facility_points: TABLE1-SAFE-WIRING-1. The SAME raw NormalizedDataPoint
          list already persisted verbatim under case_store's FACTORS.points
          record (backend/handlers/collect_data.py's `all_points`) -- e.g.
          `case_store.get_record(case_no, "FACTORS")["points"]`, passed
          straight through by pdf_handler.py. NEVER routed through
          regional_base_factors/Rule-Grade-Adjustment-Calculation-
          FormCompletion Engine (Core Freeze). Used ONLY to fill road_name
          (from the "main_road_name" point -- the SAME value RoadWidth
          Resolver/RealRoadProvider already resolved, never re-derived
          from address/segment_scope/name text here). road_name is NOT
          gated by confirmed_facility_selections this round (FACILITY-
          CONFIRMATION-GATE-1's scope is utility/funeral/major_station
          only) -- it is read straight from facility_points exactly as
          TABLE1-SAFE-WIRING-1 left it.
    confirmed_facility_selections: FACILITY-CONFIRMATION-GATE-1. A dict
          {subtype: {"name": str, "distance_m": Optional[float], ...}},
          normally `facility_confirmation_repository.
          default_facility_confirmation_repository().get_confirmed_
          selections(case_no)` -- i.e. ONLY subtypes with an explicit
          human-confirmed FacilityConfirmationRecord (status==CONFIRMED),
          for exactly these 8 keys: substation, gas_tank, cemetery,
          funeral_home, crematorium, columbarium, MRT, TRA. This
          function NEVER reads raw provider evidence (FACTORS.points'
          substation_name/gas_tank_name/.../official_facility_evidence
          ["STATION"]) to decide any of these 8 rows' checkbox state --
          a subtype absent from this dict (PENDING, REJECTED, stale-but-
          unconfirmed, or never derived at all) renders that row at its
          template default blank state, exactly the same as a subtype
          with zero evidence at all. See facility_confirmation_
          repository.py for how candidates are derived and confirmed;
          this renderer has no visibility into that process at all
          anymore (Core Freeze: this module reads ONLY the confirmed
          dict, never FACTORS directly, for these 8 rows).
          HSR is never a key in this dict (no verified source exists for
          it anywhere in this codebase) -- its row always stays at the
          template's own default "無高鐵站" appearance.

    Returns PDF bytes. Raises TemplateLayoutUnconfirmedError if the clean
    template's current SHA256 doesn't match the profile (Task 10).
    """
    profile = load_profile(profile_path)

    actual_sha = _sha256(template_path)
    if actual_sha != profile["clean_template_sha256"]:
        raise TemplateLayoutUnconfirmedError(
            f"data/templates/official_appraisal_form_v1.pdf sha256={actual_sha} does not match "
            f"profile's recorded clean_template_sha256={profile['clean_template_sha256']!r} -- "
            f"regenerate the profile (scripts/build_official_template_profile.py) before relying "
            f"on its coordinates, or this render would blind-fill a template whose layout is no "
            f"longer confirmed."
        )

    font_path = _resolve_cjk_font_path(profile)
    font = fitz.Font(fontfile=font_path)
    default_size = profile["overflow_policy"]["default_font_size"]
    min_size = profile["overflow_policy"]["min_font_size"]
    shrink_step = profile["overflow_policy"]["shrink_step"]
    overflow_fields: List[str] = []

    doc = fitz.open(template_path)
    for page in doc:
        page.insert_font(fontname="cjk", fontfile=font_path)

    # Task 7/8/9: map-page safety -- must run before any other page 3-5
    # content could conceivably be added (today there is none; the main
    # per-field write loop below only ever touches pages 0-2).
    _apply_map_page_safety(doc, case, font_path)

    def _write(page_index: int, bbox: List[float], text: Optional[str], field_id: str) -> None:
        if text is None or text == "":
            return
        page = doc[page_index]
        try:
            _fit_text(page, str(text), bbox, font, default_size, min_size, shrink_step, field_id)
        except FieldOverflowManualReviewError as e:
            overflow_fields.append(e.field_id)

    base_by_field = {fi.field_id: fi for fi in (case.base_parcel_factors or [])}
    comp_by_field: Dict[str, Dict[str, Any]] = {}
    for cid, lst in (case.comparable_factors or {}).items():
        comp_by_field[cid] = {fi.field_id: fi for fi in lst}
    regional_base_by_field = {fi.field_id: fi for fi in (regional_base_factors or [])}
    fc_by_field_id = {f["field_id"]: f for f in (form_completion_fields or [])}
    primary_comparable_id = case.comparable_ids[0] if case.comparable_ids else None

    # ---------------- 表1 ----------------
    t1 = profile["table1"]["fields"]
    p1 = profile["pages"]["table1"]
    if "segment_code" in t1:
        _write(p1, t1["segment_code"]["bbox"], case.segment_code, "segment_code")
    if "segment_scope" in t1:
        _write(p1, t1["segment_scope"]["bbox"], case.segment_scope, "segment_scope")
    if "city_district" in t1:
        _write(p1, t1["city_district"]["bbox"], f"{case.city}{case.district}", "city_district")

    for field_id, spec in t1.items():
        if field_id in ("segment_code", "segment_scope", "city_district", "road_name",
                         "regional_main_road_width",
                         "substation", "gas_tank", "cemetery", "funeral_home",
                         "crematorium", "columbarium"):
            continue
        fi = regional_base_by_field.get(field_id)
        if fi is None:
            continue
        if spec["kind"] == "checkbox_distance":
            try:
                distance = Decimal(str(fi.raw_value))
                _draw_checkbox_distance(
                    doc[p1], spec["bbox"], font, spec["label_template"],
                    is_outside=(distance > 0), distance_value=(distance if distance > 0 else None),
                    font_size=min_size,
                )
            except Exception:
                # raw_value is not numeric -- collect_data.py's Mock
                # provider (and RealLandUseProvider) represent a PRESENCE-
                # ONLY fact (no distance measured/applicable, the subject
                # is directly within the segment) as the literal string
                # "區段內有" for exactly this class of regional factor
                # (data/golden/golden_case_input.py's regional_tourism_
                # proximity, e.g.). This is a mechanical, exact-string
                # match -- NOT a guess -- and covers ONLY this one known
                # literal value, which maps unambiguously to the "○本區段
                # 內" checkbox side with no distance. Any OTHER non-
                # numeric string (including "區段內無", whose intended
                # checkbox mapping is less certain -- it may mean "not
                # applicable" rather than "confirmed outside at an unknown
                # distance" -- or a genuinely free-text factor never
                # routed to kind="checkbox_distance" in the profile, e.g.
                # "已完全開發") is left blank, never guessed.
                if str(fi.raw_value) == "區段內有":
                    _draw_checkbox_distance(doc[p1], spec["bbox"], font, spec["label_template"],
                                             is_outside=False, distance_value=None, font_size=min_size)
        else:
            # regional_avg_road_width's cell is immediately followed by the
            # template's OWN static "M" unit label (preserved, never
            # redacted -- see scripts/build_clean_official_template.py) --
            # appending the unit here would duplicate it. Every other
            # "text" field's cell has no such trailing static unit.
            if field_id == "regional_avg_road_width":
                value = fi.raw_value
            else:
                value = fi.raw_value if fi.unit is None else f"{fi.raw_value}{fi.unit}"
            _write(p1, spec["bbox"], value, field_id)

    # road name + width both come from RoadWidthEvidence when RESOLVED --
    # the caller passes it in via regional_base_factors' own evidence in
    # this simplified signature only when a RoadWidthResolutionResult is
    # not separately supplied; road_name is therefore only ever filled by
    # callers that pass road_name/road_width explicitly (see pdf_handler.py
    # wiring) -- this function itself never guesses a road name from a
    # bare regional_main_road_width FactorInput, which carries no name.
    main_road_fi = regional_base_by_field.get("regional_main_road_width")
    if main_road_fi is not None:
        _write(p1, t1["regional_main_road_width"]["bbox"],
               f"{main_road_fi.raw_value}{main_road_fi.unit or ''}", "regional_main_road_width")

    # TABLE1-SAFE-WIRING-1: road_name + utility/funeral facility evidence,
    # read ONLY from `facility_points` (FACTORS.points, passed straight
    # through by pdf_handler.py) -- never regional_base_factors, never
    # Rule/Grade/Adjustment/Calculation/FormCompletion Engine output.
    points_by_field = {p["field"]: p for p in (facility_points or []) if p.get("field")}

    # road_name: the SAME "main_road_name" NormalizedDataPoint
    # providers/road_provider.py's RealRoadProvider already emits from
    # RoadWidthResolver's own RESOLVED RoadWidthEvidence.road_name (or
    # honestly UNKNOWN otherwise) -- no new road-name logic/priority rule
    # of any kind is introduced here, this only reads the existing result.
    # "無" is this codebase's own established Mock/Golden-Case convention
    # for "confirmed no such facility/name recorded" (see e.g. providers/
    # special_facility_provider.py's MockSpecialFacilityProvider -- the
    # ORIGINAL 查估書表範本.pdf itself prints "名稱：無" for exactly this
    # case), NOT a real facility literally named "無" -- treated the same
    # as UNKNOWN (None/"") here, never rendered as a fake "found" match.
    _NO_MATCH_VALUES = (None, "", "無")

    road_name_p = points_by_field.get("main_road_name")
    if road_name_p is not None and road_name_p.get("value") not in _NO_MATCH_VALUES:
        _write(p1, t1["road_name"]["bbox"], str(road_name_p["value"]), "road_name")

    # FACILITY-CONFIRMATION-GATE-1: utility (substation/gas_tank), funeral
    # (cemetery/funeral_home/crematorium/columbarium), and major_station
    # (MRT/TRA) rows are driven EXCLUSIVELY by confirmed_facility_
    # selections -- this module no longer reads FACTORS.points'
    # substation_name/gas_tank_name/.../official_facility_evidence
    # ["STATION"] for these 8 rows at all (that candidate-derivation and
    # selection logic now lives in backend/handlers/facility_confirmation_
    # repository.py, which produces PENDING candidates a human must
    # explicitly confirm before they can ever appear here). A subtype
    # missing from confirmed_facility_selections (never derived, still
    # PENDING, REJECTED, or a stale-but-unreconfirmed CONFIRMED record)
    # renders that row at its template default blank state -- exactly
    # the same, indistinguishable outcome as "no evidence exists at all".
    def _confirmed(subtype: str) -> Optional[Dict[str, Any]]:
        if not confirmed_facility_selections:
            return None
        sel = confirmed_facility_selections.get(subtype)
        if not sel or not sel.get("name"):
            return None
        return sel

    for prefix in ("substation", "gas_tank", "cemetery", "funeral_home", "crematorium", "columbarium"):
        spec = t1.get(prefix)
        if spec is None:
            continue
        sel = _confirmed(prefix)
        name = sel.get("name") if sel else None
        distance = sel.get("distance_m") if sel else None
        if spec["kind"] == "funeral_facility_evidence":
            # Row-selector glyph: ALWAYS drawn (a permanent form control
            # needing SOME visible state), ● only when a CONFIRMED
            # selection exists -- never derived from the facility's name
            # text, only from confirmation status.
            circle_rect = fitz.Rect(*spec["circle_bbox"])
            circle_char = "●" if name is not None else "○"
            doc[p1].insert_text((circle_rect.x0, circle_rect.y1 - 1.2), circle_char,
                                 fontname="cjk", fontsize=min_size, render_mode=0)
        if name is None:
            continue
        name_text = name if spec["name_has_prefix_label"] else f"名稱：{name}"
        _write(p1, spec["name_bbox"], name_text, f"{prefix}_name")
        # These are all 嫌惡設施/公用設施-type facilities located via a
        # real-world OSM/coordinate query (never a "within segment"
        # determination -- see providers/real_facility_provider_base.py's
        # WITHIN_SEGMENT_NOTE, always UNKNOWN by design) -- a found match
        # is therefore always rendered as "本區段外(距 N M)", never
        # "本區段內", which this data never actually supports claiming.
        _draw_checkbox_distance(doc[p1], spec["checkbox_bbox"], font,
                                 "○本區段內　○本區段外(距　　M)",
                                 is_outside=True, distance_value=distance, font_size=min_size)

    # HSR is never a key in confirmed_facility_selections (no verified
    # source exists anywhere in this codebase -- see facility_
    # confirmation_repository.py's derive_candidates(), which never
    # produces an "HSR" candidate at all) -- included in this loop only
    # so its row's ○ circle is freshly drawn into its own now-empty slot
    # for visual completeness, never so it could ever be confirmed.
    for subtype, field_id in (("HSR", "major_station_hsr"), ("MRT", "major_station_mrt"),
                               ("TRA", "major_station_tra")):
        spec = t1.get(field_id)
        if spec is None:
            continue
        # Defense in depth (Task 5: "HSR 維持：永不自動填"): HSR is
        # hard-excluded from ever reading confirmed_facility_selections at
        # all, even if a caller's dict somehow contained an "HSR" key --
        # never trust a single upstream layer (facility_confirmation_
        # repository.py never producing an HSR candidate) to be the only
        # thing enforcing a "never" invariant.
        sel = _confirmed(subtype) if subtype != "HSR" else None
        if sel is None:
            # No CONFIRMED selection for this exact subtype -- the
            # template's own default "無高鐵站"/"無火車站"/"無捷運站"
            # label is left completely untouched; only the row-selector
            # circle is freshly drawn into its own now-empty slot
            # (matching the funeral-row convention). Deliberate
            # MANUAL_REVIEW_REQUIRED outcome, not a bug.
            circle_rect = fitz.Rect(*spec["circle_bbox"])
            doc[p1].insert_text((circle_rect.x0, circle_rect.y1 - 1.2), "○",
                                 fontname="cjk", fontsize=min_size, render_mode=0)
            continue
        _overlay_major_station_match(doc[p1], font, spec, sel, min_size)
        distance = sel.get("distance_m")  # None when not computable -- never fabricated
        # Haversine straight_line_distance() returns a float (whole-meter
        # rounding elsewhere in this codebase's own Mock fixtures is
        # purely cosmetic display convention, e.g. 700/300/750 in
        # providers/special_facility_provider.py) -- rounds the DISPLAYED
        # text only, never the underlying value used for selection.
        if distance is not None:
            distance = round(distance)
        _draw_checkbox_distance(doc[p1], spec["checkbox_bbox"], font,
                                 "○本區段內　○本區段外(距　　M)",
                                 is_outside=True, distance_value=distance, font_size=min_size)

    # ---------------- 表5-2 ----------------
    t52 = profile["table5_2"]["fields"]
    p52 = profile["pages"]["table5_2"]
    # OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 4: 表5-2's own header 案號
    # cell -- case.case_no is the SAME value already written onto 表4's
    # header a few lines below (direct_case_fields["case_no"]); this was a
    # SOURCE_AVAILABLE_RENDERER_GAP (profile never had a table5_2-side
    # entry for it), not a missing source, so it is wired here directly
    # rather than through the 28-factor-row loop below (which "case_no"
    # deliberately is NOT a member of).
    if "case_no" in t52 and case.case_no:
        _write(p52, t52["case_no"]["bbox"], case.case_no, "table5_2_case_no")
    for field_id, row in t52.items():
        if field_id == "case_no":
            continue
        base_fc = fc_by_field_id.get(f"{field_id}_adjustment_pct_{primary_comparable_id}") if primary_comparable_id else None
        if base_fc is None or not base_fc.get("grade"):
            continue
        grade_str = base_fc["grade"]  # "base=X, comp=Y"
        try:
            base_grade_text = grade_str.split(",")[0].replace("base=", "").strip()
            comp_grade_text = grade_str.split(",")[1].replace("comp=", "").strip()
        except IndexError:
            continue
        _write(p52, row["base"]["grade_text_bbox"], base_grade_text, f"{field_id}.base_grade")
        comp0 = row["comparables"][0]
        _write(p52, comp0["grade_text_bbox"], comp_grade_text, f"{field_id}.comp_grade")
        _write(p52, comp0["pct_bbox"], _decimal_str(base_fc.get("final_value")), f"{field_id}.pct")

    # ---------------- 表4 ----------------
    t4 = profile["table4"]["fields"]
    p4 = profile["pages"]["table4"]
    direct_case_fields = {
        "base_parcel_id": case.base_parcel_id,
        "comparable_parcel_id": primary_comparable_id,
        "case_no": case.case_no,
        "appraisal_base_date": case.appraisal_base_date,
    }
    for field_id, value in direct_case_fields.items():
        if field_id in t4 and value:
            _write(p4, t4[field_id]["bbox"], value, field_id)

    for field_id, spec in t4.items():
        if field_id in direct_case_fields:
            continue
        kind = spec.get("kind")
        if kind == "form_completion_field" and primary_comparable_id:
            source_field_id = spec["source_field_id_template"].format(cid=primary_comparable_id)
            fc = fc_by_field_id.get(source_field_id)
            if fc is not None and fc.get("final_value") is not None:
                _write(p4, spec["bbox"], _decimal_str(fc["final_value"]), field_id)
        elif kind == "case_field" and primary_comparable_id:
            value = getattr(case, spec["case_attr"], {}).get(primary_comparable_id)
            _write(p4, spec["bbox"], _decimal_str(value), field_id)
        elif kind == "raw":
            side = spec["side"]
            if side == "base":
                fi = base_by_field.get(field_id.replace("[base]", ""))
            else:
                fi = comp_by_field.get(primary_comparable_id, {}).get(field_id.replace("[comp]", "")) if primary_comparable_id else None
            if fi is not None:
                # omit_unit (Task 5/6): the row's own STATIC label already
                # states the unit (e.g. "面積(M2)") -- appending it again
                # in the value cell would be redundant with the original
                # template's own bare-number convention, and for a
                # narrower bbox (originally sized for a single glyph) can
                # genuinely overflow.
                if spec.get("omit_unit") or fi.unit is None:
                    value = fi.raw_value
                else:
                    value = f"{fi.raw_value}{fi.unit}"
                _write(p4, spec["bbox"], value, field_id)
        elif field_id.endswith("[base]"):
            base_field = field_id[: -len("[base]")]
            fi = base_by_field.get(base_field)
            if fi is not None and not isinstance(fi.raw_value, (int, float, Decimal)):
                _write(p4, spec["bbox"], fi.raw_value, field_id)
        elif field_id.endswith("[comp]"):
            comp_field = field_id[: -len("[comp]")]
            fi = comp_by_field.get(primary_comparable_id, {}).get(comp_field) if primary_comparable_id else None
            if fi is not None and not isinstance(fi.raw_value, (int, float, Decimal)):
                _write(p4, spec["bbox"], fi.raw_value, field_id)
        # road_name / *.name / table4_remarks_* fields: no Structured
        # Result source exists for these (see module docstring) -- left
        # blank, never fabricated.

    pdf_bytes = doc.write(garbage=3, deflate=True)
    doc.close()
    if overflow_fields:
        # Non-fatal: the PDF still generated with every OTHER field filled;
        # overflowed fields are simply left blank on the page. Callers
        # that need to surface FIELD_OVERFLOW_MANUAL_REVIEW to a human
        # reviewer read this attribute-style return -- kept simple (no
        # second return channel) by stashing it as a module-level last-run
        # diagnostic, mirrored into the handler's response separately.
        render_official_pdf.last_overflow_fields = overflow_fields
    else:
        render_official_pdf.last_overflow_fields = []
    return pdf_bytes


render_official_pdf.last_overflow_fields = []
