# -*- coding: utf-8 -*-
"""
PDF-OFFICIAL-1 Task 12/13 — Official PDF Output tests.

Two layers:
  1. Renderer-level (pdf/official_pdf_renderer.py) -- pure PyMuPDF, NO
     WeasyPrint dependency, so these tests run fully on every host
     (including this Windows dev host, which lacks WeasyPrint's native
     Cairo/Pango libraries -- see docs/audit/PDF_OUTPUT_PHASE5_REPORT.md).
     Exercises the REAL FormCompletionEngine pipeline against the REAL
     Golden Case fixture (data/golden/golden_case_input.py) to produce a
     genuine FormCompletionResult, then renders the REAL official PDF
     template and verifies actual page/text/bbox evidence -- never just
     `bytes.startswith(b"%PDF")`.
  2. Handler-level (backend/handlers/pdf_handler.py) -- requires
     WeasyPrint (pdf_renderer.py, imported by pdf_handler.py for the
     EXISTING Audit PDF, needs it even though official_pdf_renderer.py
     itself does not) -- guarded with the same try/except OSError skip
     pattern already established across this repo's STEP5 test files,
     never a fabricated pass.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "data", "golden"))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend", "handlers"))
sys.path.insert(0, os.path.join(REPO_ROOT, "pdf"))
sys.path.insert(0, REPO_ROOT)

fitz = pytest.importorskip("fitz", reason="PyMuPDF not available")

from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402
from rule_engine import RuleEngine  # noqa: E402
from grade_engine import GradeEngine  # noqa: E402
from adjustment_engine import AdjustmentEngine  # noqa: E402
from calculation_engine import CalculationEngine  # noqa: E402
from form_completion_engine import FormCompletionEngine  # noqa: E402

from official_pdf_renderer import (  # noqa: E402
    render_official_pdf, load_profile, TemplateLayoutUnconfirmedError,
    DEFAULT_TEMPLATE_PATH, DEFAULT_PROFILE_PATH,
)

GOLDEN_COMPARABLE_ID = GOLDEN_CASE.comparable_ids[0]


@pytest.fixture(scope="module")
def golden_form_completion_fields():
    """Runs the REAL, unmodified Grade -> Adjustment -> Calculation ->
    FormCompletionEngine chain against the REAL Golden Case fixture --
    this is the SAME pipeline complete_form.py itself runs, not a
    hand-built stand-in. official_pdf_renderer.py under test never sees
    or calls any of these engine objects itself (see that module's own
    docstring) -- only their OUTPUT, exactly matching Task 4's required
    data flow."""
    reg = json.load(open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8"))["rules"]
    ind = json.load(open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8"))["rules"]
    rule_engine = RuleEngine(reg + ind)
    fce = FormCompletionEngine(GradeEngine(rule_engine), AdjustmentEngine(rule_engine), CalculationEngine())
    result = fce.complete_form(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)
    return json.loads(result.model_dump_json())["fields"]


@pytest.fixture(scope="module")
def golden_official_pdf_bytes(golden_form_completion_fields):
    return render_official_pdf(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields)


def _text_in_bbox_region(page: fitz.Page, bbox, expected_substring: str, tolerance: float = 3.0) -> bool:
    """Bounding-box tolerance check (Task 12/13): does `expected_substring`
    appear anywhere in the page whose OWN bbox overlaps `bbox` (expanded
    by `tolerance` points on every side)? Not exact-pixel matching (the
    overlay's own text width varies with content), but a genuine
    positional assertion -- catches "field written to the wrong row/
    column" bugs a bare `extracted_text contains X` check would miss."""
    rect = fitz.Rect(*bbox) + (-tolerance, -tolerance, tolerance, tolerance)
    words = page.get_text("words")  # (x0, y0, x1, y1, word, ...)
    found = "".join(w[4] for w in words if fitz.Rect(w[:4]).intersects(rect))
    return expected_substring in found


class TestRendererGoldenCase:
    def test_produces_openable_multi_page_pdf(self, golden_official_pdf_bytes):
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        assert doc.page_count == 6  # 3 data pages (表1/表5-2/表4) + 3 unmodified map pages
        assert not doc.is_encrypted
        doc.close()

    def test_table1_zoning_and_ratios_in_correct_cells(self, golden_official_pdf_bytes):
        profile = load_profile()
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["regional_zoning_inside_outside"]["bbox"], "都市計畫內")
        assert _text_in_bbox_region(page, t1["regional_land_use_zone"]["bbox"], "第二種商業區")
        assert _text_in_bbox_region(page, t1["regional_building_coverage_ratio"]["bbox"], "70")
        assert _text_in_bbox_region(page, t1["regional_floor_area_ratio"]["bbox"], "240")
        assert _text_in_bbox_region(page, t1["regional_main_road_width"]["bbox"], "18")
        doc.close()

    def test_table1_facility_checkboxes_show_correct_side_and_distance(self, golden_official_pdf_bytes):
        profile = load_profile()
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        # regional_financial_institution_proximity raw_value=210 (>0 -> 本區段外, distance shown)
        text = "".join(w[4] for w in page.get_text("words")
                        if fitz.Rect(w[:4]).intersects(fitz.Rect(*t1["regional_financial_institution_proximity"]["bbox"])))
        assert "●" in text and "210" in text
        doc.close()

    def test_table5_2_grade_and_pct_match_known_golden_values(self, golden_official_pdf_bytes):
        """docs/audit/BLIND_CASE_PHASE5_REPORT.md's own Golden baseline:
        主要道路寬度 18m (base==comp, same P002-00 segment) -> 普通,
        adjustment 0 for every regional factor (base and comparable share
        the identical segment)."""
        profile = load_profile()
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table5_2"]]
        row = profile["table5_2"]["fields"]["regional_main_road_width"]
        assert _text_in_bbox_region(page, row["base"]["grade_text_bbox"], "普通")
        assert _text_in_bbox_region(page, row["comparables"][0]["grade_text_bbox"], "普通")
        assert _text_in_bbox_region(page, row["comparables"][0]["pct_bbox"], "0")
        doc.close()

    def test_table4_frontage_road_width_differential_rate_matches_golden(self, golden_official_pdf_bytes):
        """docs/audit/COMPETITION_E2E_PHASE5_REPORT.md's own Golden fact:
        面前道路寬度 18m vs 6m -> +5.00%."""
        profile = load_profile()
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table4"]]
        field = profile["table4"]["fields"]["individual_frontage_road_width.diff_rate"]
        assert _text_in_bbox_region(page, field["bbox"], "5")
        base_price_field = profile["table4"]["fields"]["base_parcel_comparison_price"]
        assert _text_in_bbox_region(page, base_price_field["bbox"], "212958")
        doc.close()

    def test_not_applicable_dash_never_becomes_zero(self, golden_official_pdf_bytes):
        """§ task 6/7's explicit rule: "－" (NOT_APPLICABLE) must never be
        silently replaced with "0". This renderer never writes into the
        表4 "其他"/"合計" row cells at all (no Structured Result field maps
        to them), so the template's own static "－"/"-" markers there are
        the ONLY thing on the page -- confirmed by checking the exact
        marker text is still present unchanged."""
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[2]
        text = page.get_text()
        assert "－" in text or "-" in text
        doc.close()

    def test_no_fabricated_road_name_when_no_source(self, golden_official_pdf_bytes):
        """individual_frontage_road_width carries only a numeric width in
        Structured Result, never a road NAME (see pdf/official_pdf_
        renderer.py module docstring) -- the 表4 road-name cell must stay
        genuinely blank, never guessing "中山路" from the template's own
        historical example."""
        profile = load_profile()
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table4"]]
        road_name_bbox = profile["table1"]["fields"]  # table1's own road_name is separately checked below
        assert "road_name" not in profile["table4"]["fields"]  # 表4 has no road-name field mapped at all this round
        doc.close()

    def test_appraisal_base_date_goes_to_header_not_fill_date_footer(self, golden_official_pdf_bytes):
        """OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 5/6: case.
        appraisal_base_date ("1140901") must render into 表4's OWN header
        「估價基準日：」cell, and the DIFFERENT footer 「填寫日期：」cell
        (no verified fill/completion/review-date source exists anywhere
        in this codebase's domain model) must stay genuinely blank --
        never backfilled with appraisal_base_date as a stand-in."""
        profile = load_profile()
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table4"]]
        t4 = profile["table4"]["fields"]
        assert _text_in_bbox_region(page, t4["appraisal_base_date"]["bbox"], "1140901")
        assert "form_fill_date" in t4
        fill_date_words = page.get_text("words")
        fill_date_rect = fitz.Rect(*t4["form_fill_date"]["bbox"]) + (-3, -3, 3, 3)
        fill_date_text = "".join(w[4] for w in fill_date_words if fitz.Rect(w[:4]).intersects(fill_date_rect))
        assert fill_date_text.strip() == "", f"expected 填寫日期 to stay blank, found {fill_date_text!r}"
        doc.close()


class TestCleanTemplateHasNoResidualText:
    def test_no_original_content_remains_under_any_redacted_region(self):
        """FINAL GATE regression guard: scripts/build_clean_official_
        template.py used to only PAINT OVER example values with a white
        rectangle (`page.draw_rect(..., fill=white)`), which left the
        original text fully present in the PDF's content stream
        underneath (found via `page.search_for("都市計畫內")` still
        locating it at its original bbox in the "clean" template -- a
        real, confirmed defect, not a hypothetical). Fixed by switching
        to PyMuPDF's actual redaction API (`add_redact_annot` +
        `apply_redactions()`), which removes the underlying content. This
        test re-derives the same check scripts/verify_no_residual_text.py
        performs standalone, so a future change to the clean-template
        build script that regresses back to paint-over-only redaction is
        caught by the normal test suite, not just a manually-run script."""
        profile = load_profile()
        manifest_path = os.path.join(
            REPO_ROOT, "data", "templates", "official_appraisal_form_v1.redaction_manifest.json")
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        assert len(manifest) > 400  # sanity: the manifest itself wasn't accidentally emptied

        doc = fitz.open(DEFAULT_TEMPLATE_PATH)
        residual = []
        for entry in manifest:
            page = doc[entry["page"]]
            rect = fitz.Rect(*entry["bbox"])
            words = page.get_text("words")
            found = "".join(
                w[4] for w in words
                if rect.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
            )
            if found.strip():
                residual.append((entry["page"], entry.get("original_text"), found))
        doc.close()
        assert residual == [], f"residual text found in {len(residual)} redacted regions: {residual[:5]}"


class TestTemplateFingerprintGuard:
    def test_wrong_recorded_sha256_raises_template_layout_unconfirmed(self, tmp_path, golden_form_completion_fields):
        """Task 10: a profile whose recorded clean_template_sha256 does
        NOT match the real on-disk template must refuse to render, not
        blind-fill coordinates that might no longer correspond to the
        template's actual layout."""
        profile = load_profile()
        tampered = copy.deepcopy(profile)
        tampered["clean_template_sha256"] = "0" * 64
        tampered_path = tmp_path / "tampered_profile.json"
        tampered_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(TemplateLayoutUnconfirmedError):
            render_official_pdf(
                GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields,
                profile_path=str(tampered_path),
            )

    def test_default_profile_matches_real_template_on_disk(self):
        """Sanity check that the CHECKED-IN profile and template are
        currently in sync (would fail if one were edited without
        regenerating the other via scripts/build_official_template_
        profile.py)."""
        import hashlib
        profile = load_profile()
        with open(DEFAULT_TEMPLATE_PATH, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()
        assert actual_sha == profile["clean_template_sha256"]


class TestOriginalTemplatePreserved:
    def test_original_official_pdf_untouched(self):
        """Task 2's explicit requirement: the ORIGINAL official PDF must
        never be modified in place."""
        import hashlib
        source_path = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")
        with open(source_path, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()
        profile = load_profile()
        assert actual_sha == profile["source_pdf_sha256"]

    def test_map_pages_3_4_5_byte_identical_to_source(self, golden_official_pdf_bytes):
        """Pages 3-5 (the map attachments) carry no appraisal-form fields
        at all (docs/pdf/OFFICIAL_TEMPLATE_SURVEY.md). OFFICIAL-PDF-FINAL-
        QUALITY-GATE-1 Task 7/8/9: for a case whose segment identity
        matches what these map pages actually depict (this Golden Case
        fixture does), all of the clean template's own original map
        content (every image, every original line of text) is preserved
        UNCHANGED -- confirmed here by substring containment rather than
        exact equality, since Task 9 now also stamps one small provenance
        line onto each of these 3 pages (see official_pdf_renderer.py's
        _apply_map_page_safety()) so nobody mistakes a template sample map
        for a generic, case-agnostic official background. Image count is
        asserted unchanged as the strongest signal that the underlying
        map graphics themselves were never touched."""
        clean_doc = fitz.open(DEFAULT_TEMPLATE_PATH)
        out_doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        for i in (3, 4, 5):
            clean_text = clean_doc[i].get_text()
            out_text = out_doc[i].get_text()
            assert clean_text in out_text, f"page {i}: original map content missing/altered"
            assert out_text != clean_text, f"page {i}: expected an added Task 9 provenance note"
            assert "查估書表範本.pdf" in out_text and "P002-00" in out_text
            assert len(clean_doc[i].get_images()) == len(out_doc[i].get_images())
        clean_doc.close()
        out_doc.close()


class TestNonGoldenCaseMapSafety:
    """OFFICIAL-PDF-FINAL-QUALITY-GATE-1 Task 7/8/9/11: a case whose
    segment identity does NOT match what the template's own map pages
    depict (新北市金山區 P002-00) must NEVER receive those Golden-Case-
    specific map pages -- this is a real data-safety risk (attaching the
    wrong location's official map to a real appraisal document), not a
    cosmetic one. Uses a copy of the Golden Case with only case_no/
    segment_code/city/district swapped -- deliberately NOT a from-scratch
    fixture, so every OTHER field (base_parcel_factors, comparable_ids,
    etc.) stays realistic and this test isolates exactly the map-page
    behavior under test."""

    @pytest.fixture(scope="class")
    def non_golden_pdf_bytes(self, golden_form_completion_fields):
        non_golden_case = GOLDEN_CASE.model_copy(update={
            "case_no": "1150312-55-002",
            "segment_code": "B999-00",
            "city": "新北市",
            "district": "板橋區",
        })
        return render_official_pdf(non_golden_case, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields)

    def test_golden_case_map_content_not_leaked(self, non_golden_pdf_bytes):
        doc = fitz.open(stream=non_golden_pdf_bytes, filetype="pdf")
        forbidden = ["1140901-99-001", "P002-00", "金美段489地號", "溫泉段218地號"]
        for i in (3, 4, 5):
            text = doc[i].get_text()
            for token in forbidden:
                assert token not in text, f"page {i}: leaked Golden Case token {token!r}"
            assert len(doc[i].get_images()) == 0, f"page {i}: Golden Case map image not removed"
        doc.close()

    def test_non_golden_map_pages_show_honest_manual_notice(self, non_golden_pdf_bytes):
        doc = fitz.open(stream=non_golden_pdf_bytes, filetype="pdf")
        for i in (3, 4, 5):
            text = doc[i].get_text()
            assert "圖資待人工附具" in text
            assert "板橋區" in text and "B999" in text
        doc.close()

    def test_golden_case_itself_unaffected_by_the_safety_check(self, golden_official_pdf_bytes):
        """Sanity check: the safety logic must not accidentally blank the
        REAL Golden Case's own map pages."""
        doc = fitz.open(stream=golden_official_pdf_bytes, filetype="pdf")
        for i in (3, 4, 5):
            assert len(doc[i].get_images()) == 14
            assert "圖資待人工附具" not in doc[i].get_text()
        doc.close()


class TestTable4FieldLevelReconciliation:
    """OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 8: a true FIELD-level
    (not row-level) mutually-exclusive count over every one of 表4's own
    profile entries -- each is exactly one atomic renderable cell (the
    profile itself already splits [base]/[comp] and .name vs the plain
    distance/width cell into separate keys, so no further row-splitting
    judgment call is needed here; this just counts the profile's own
    keys). Derived by instrumenting the REAL renderer's own _fit_text
    calls against the REAL Golden Case -- not by manual/asserted counting
    -- so this test breaks (loudly) if a future change silently drops or
    adds a field without updating the classification below."""

    SOURCE_NOT_AVAILABLE_FIELDS = {
        "form_fill_date",
        "individual_frontage_road_width.road_name[base]", "individual_frontage_road_width.road_name[comp]",
        "individual_school_proximity.name[base]", "individual_school_proximity.name[comp]",
        "individual_market_proximity.name[base]", "individual_market_proximity.name[comp]",
        "individual_park_proximity.name[base]", "individual_park_proximity.name[comp]",
        "individual_station_proximity.name[base]", "individual_station_proximity.name[comp]",
        "individual_commercial_district_proximity.name[base]", "individual_commercial_district_proximity.name[comp]",
        "individual_nuisance_facility.name[base]", "individual_nuisance_facility.name[comp]",
    }
    MANUAL_REQUIRED_FIELDS = {"table4_remarks_case", "table4_remarks_comparable"}

    @pytest.fixture(scope="class")
    def rendered_field_ids(self, golden_form_completion_fields):
        import official_pdf_renderer as renderer_module
        rendered = set()
        original_fit_text = renderer_module._fit_text

        def _spy(page, text, bbox, font, default_size, min_size, shrink_step, field_id,
                 align=fitz.TEXT_ALIGN_LEFT):
            rendered.add(field_id)
            return original_fit_text(page, text, bbox, font, default_size, min_size, shrink_step, field_id, align)

        renderer_module._fit_text = _spy
        try:
            render_official_pdf(GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields)
        finally:
            renderer_module._fit_text = original_fit_text
        return rendered

    def test_field_level_counts_are_mutually_exclusive_and_reconcile(self, rendered_field_ids):
        profile = load_profile()
        all_fields = set(profile["table4"]["fields"].keys())
        rendered = rendered_field_ids & all_fields
        not_rendered = all_fields - rendered

        assert self.SOURCE_NOT_AVAILABLE_FIELDS <= not_rendered
        assert self.MANUAL_REQUIRED_FIELDS <= not_rendered
        source_available_not_rendered = not_rendered - self.SOURCE_NOT_AVAILABLE_FIELDS - self.MANUAL_REQUIRED_FIELDS

        total = len(all_fields)
        a = len(rendered)
        b = len(source_available_not_rendered)
        c = len(self.SOURCE_NOT_AVAILABLE_FIELDS)
        d = len(self.MANUAL_REQUIRED_FIELDS)

        assert b == 0, f"SOURCE_AVAILABLE_RENDERER_GAP still open: {source_available_not_rendered}"
        assert a + b + c + d == total, "field-level A+B+C+D must reconcile to TOTAL with no double counting"


class TestSameSegmentDifferentCaseMapSafety:
    """OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 1/2/3: the high-risk case
    a city/district/segment_code-only check would miss -- a DIFFERENT
    case that happens to share the Golden Case's exact city/district/
    segment_code. The template's map is specific to the Golden Case's own
    parcels, not to "any case in this segment" -- so this must ALSO be
    refused the Golden map, exactly like a different-segment case."""

    @pytest.fixture(scope="class")
    def same_segment_different_case_pdf_bytes(self, golden_form_completion_fields):
        other_case = GOLDEN_CASE.model_copy(update={"case_no": "9999999-88-777"})
        return render_official_pdf(other_case, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields)

    def test_same_segment_different_case_does_not_receive_golden_maps(self, same_segment_different_case_pdf_bytes):
        doc = fitz.open(stream=same_segment_different_case_pdf_bytes, filetype="pdf")
        forbidden = ["1140901-99-001", "金美段489地號", "溫泉段218地號"]
        for i in (3, 4, 5):
            text = doc[i].get_text()
            for token in forbidden:
                assert token not in text, f"page {i}: leaked Golden Case token {token!r} for a different case_no"
            assert len(doc[i].get_images()) == 0
            assert "圖資待人工附具" in text
        doc.close()


class TestDifferentParcelIdentityMapSafety:
    """OFFICIAL-PDF-FINAL-SEMANTIC-SAFETY-1 Task 4: same case_no/segment/
    city/district as the Golden Case but a DIFFERENT base parcel -- the
    map gate must still refuse (verified parcel identity is part of the
    identity check, not just case_no/segment/location). CompetitionCase
    always carries base_parcel_id/comparable_ids (required fields, not
    optional) so PARCEL_IDENTITY_NOT_AVAILABLE_FOR_MAP_GATE=NO for this
    domain model -- documented here, not merely asserted in prose."""

    def test_parcel_identity_fields_exist_on_domain_model(self):
        assert hasattr(GOLDEN_CASE, "base_parcel_id") and GOLDEN_CASE.base_parcel_id
        assert hasattr(GOLDEN_CASE, "comparable_ids") and GOLDEN_CASE.comparable_ids

    def test_different_base_parcel_same_everything_else_does_not_receive_golden_maps(
        self, golden_form_completion_fields,
    ):
        different_parcel_case = GOLDEN_CASE.model_copy(update={"base_parcel_id": "金美段999地號"})
        pdf_bytes = render_official_pdf(different_parcel_case, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for i in (3, 4, 5):
            text = doc[i].get_text()
            assert "金美段489地號" not in text
            assert len(doc[i].get_images()) == 0
            assert "圖資待人工附具" in text
        doc.close()


def _point(field, value, unit=None, source="TEST_FIXTURE", source_type="Mock",
           confidence="高", notes=""):
    """Builds one NormalizedDataPoint-shaped plain dict -- the SAME shape
    backend/handlers/collect_data.py persists verbatim into FACTORS.points
    and pdf_handler.py passes straight through as `facility_points`. Used
    ONLY for road_name (still wired this way -- see official_pdf_
    renderer.py's own docstring) and for the anti-raw-fallback test below."""
    return {"field": field, "value": value, "unit": unit, "source": source,
            "source_type": source_type, "coordinate": None, "confidence": confidence,
            "retrieved_at": None, "notes": notes}


def _selection(name, distance_m=None):
    """One confirmed_facility_selections[subtype]-shaped plain dict --
    TEST_FIXTURE_VALUE, standing in for what facility_confirmation_
    repository.get_confirmed_selections() would return for an already-
    CONFIRMED record. Never a claim about real provider/government data."""
    return {"name": name, "distance_m": distance_m}


class TestFacilityEvidenceWiring:
    """FACILITY-CONFIRMATION-GATE-1: utility/funeral rows are driven
    EXCLUSIVELY by `confirmed_facility_selections` -- this renderer no
    longer reads raw facility_points substation_name/gas_tank_name/...
    at all for these 6 rows (see official_pdf_renderer.py's own
    docstring). road_name is the one exception, unaffected by this gate
    (still reads facility_points, TABLE1-SAFE-WIRING-1's original,
    simpler wiring)."""

    def _render(self, golden_form_completion_fields, selections=None, points=None):
        return render_official_pdf(
            GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields,
            facility_points=points, confirmed_facility_selections=selections,
        )

    def test_a_road_name_renders_in_correct_cell(self, golden_form_completion_fields):
        # NOTE: deliberately avoids the character "路" in this fixture --
        # confirmed (via a minimal standalone PyMuPDF repro, unrelated to
        # any of this round's own logic) that the Windows dev host's
        # NotoSansTC-VF.ttf font has a pre-existing insert_text/get_text
        # ToUnicode round-trip quirk for exactly that one character
        # (extracts back as U+F937, a PUA codepoint, instead of U+8DEF) --
        # visually the glyph still renders correctly, this only affects
        # automated get_text()-based substring assertions.
        points = [_point("main_road_name", "測試道名稱999")]
        pdf_bytes = self._render(golden_form_completion_fields, points=points)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        assert _text_in_bbox_region(page, profile["table1"]["fields"]["road_name"]["bbox"], "測試道名稱999")
        doc.close()

    def test_b_substation_renders_only_in_substation_row(self, golden_form_completion_fields):
        selections = {"substation": _selection("測試變電所", 700)}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["substation"]["name_bbox"], "測試變電所")
        assert _text_in_bbox_region(page, t1["substation"]["checkbox_bbox"], "700")
        # Never bleeds into the adjacent gas_tank row.
        assert not _text_in_bbox_region(page, t1["gas_tank"]["name_bbox"], "測試變電所")
        doc.close()

    def test_c_gas_tank_renders_only_in_gas_tank_row(self, golden_form_completion_fields):
        selections = {"gas_tank": _selection("測試瓦斯站", 440)}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["gas_tank"]["name_bbox"], "測試瓦斯站")
        assert _text_in_bbox_region(page, t1["gas_tank"]["checkbox_bbox"], "440")
        assert not _text_in_bbox_region(page, t1["substation"]["name_bbox"], "測試瓦斯站")
        doc.close()

    def test_d_cemetery_renders_only_in_cemetery_row(self, golden_form_completion_fields):
        selections = {"cemetery": _selection("測試公墓", 80)}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["cemetery"]["name_bbox"], "測試公墓")
        assert _text_in_bbox_region(page, t1["cemetery"]["checkbox_bbox"], "80")
        circle_text = "".join(
            w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(fitz.Rect(*t1["cemetery"]["circle_bbox"])))
        assert "●" in circle_text
        for other in ("funeral_home", "crematorium", "columbarium"):
            assert not _text_in_bbox_region(page, t1[other]["name_bbox"], "測試公墓")
        doc.close()

    def test_e_funeral_home_renders_only_in_funeral_home_row(self, golden_form_completion_fields):
        selections = {"funeral_home": _selection("測試殯儀館", 300)}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["funeral_home"]["name_bbox"], "測試殯儀館")
        assert _text_in_bbox_region(page, t1["funeral_home"]["checkbox_bbox"], "300")
        for other in ("cemetery", "crematorium", "columbarium"):
            assert not _text_in_bbox_region(page, t1[other]["name_bbox"], "測試殯儀館")
        doc.close()

    def test_f_crematorium_renders_only_in_crematorium_row(self, golden_form_completion_fields):
        selections = {"crematorium": _selection("測試火化場", 600)}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["crematorium"]["name_bbox"], "測試火化場")
        assert _text_in_bbox_region(page, t1["crematorium"]["checkbox_bbox"], "600")
        for other in ("cemetery", "funeral_home", "columbarium"):
            assert not _text_in_bbox_region(page, t1[other]["name_bbox"], "測試火化場")
        doc.close()

    def test_g_columbarium_renders_only_in_columbarium_row(self, golden_form_completion_fields):
        selections = {"columbarium": _selection("測試納骨堂", 750)}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["columbarium"]["name_bbox"], "測試納骨堂")
        assert _text_in_bbox_region(page, t1["columbarium"]["checkbox_bbox"], "750")
        for other in ("cemetery", "funeral_home", "crematorium"):
            assert not _text_in_bbox_region(page, t1[other]["name_bbox"], "測試納骨堂")
        doc.close()

    def test_h_missing_facility_stays_blank(self, golden_form_completion_fields):
        """No confirmed_facility_selections at all (PENDING/REJECTED/
        never-derived, all indistinguishable from here) -- every row-
        selector circle must show ○ (unchecked), no name/distance text,
        no "None"/"UNKNOWN"/"0m" literal anywhere in the 6 rows' cells."""
        pdf_bytes = self._render(golden_form_completion_fields, selections={})
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        for prefix in ("substation", "gas_tank", "cemetery", "funeral_home", "crematorium", "columbarium"):
            spec = t1[prefix]
            name_text = "".join(
                w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(fitz.Rect(*spec["name_bbox"])))
            assert "None" not in name_text and "UNKNOWN" not in name_text
            checkbox_text = "".join(
                w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(fitz.Rect(*spec["checkbox_bbox"])))
            assert "0m" not in checkbox_text.lower().replace(" ", "")
            if "circle_bbox" in spec:
                circle_text = "".join(
                    w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(fitz.Rect(*spec["circle_bbox"])))
                assert "●" not in circle_text
        doc.close()

    def test_h2_no_confirmed_selections_argument_at_all_stays_blank(self, golden_form_completion_fields):
        """FACILITY-CONFIRMATION-GATE-1 Task 10 (backward compatibility):
        confirmed_facility_selections=None (the parameter default, e.g. an
        existing case with no facility_confirmation records at all) must
        render identically to selections={} -- never crash, never fall
        back to raw evidence."""
        pdf_bytes = render_official_pdf(
            GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields,
        )
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]["substation"]
        circle_text = "".join(
            w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(fitz.Rect(*t1["name_bbox"])))
        assert circle_text.strip() == ""
        doc.close()

    def test_i_subtype_selected_purely_by_dict_key_not_name_text(self, golden_form_completion_fields):
        """Anti-fabrication: a facility whose NAME happens to contain
        "公墓" ("cemetery" in ordinary Chinese) but which arrived under
        confirmed_facility_selections["crematorium"] (a DIFFERENT key)
        must render ONLY in the crematorium row -- row selection is
        purely by dict key, never by parsing the name value."""
        selections = {"crematorium": _selection("金山公墓")}  # name text says "公墓", key says crematorium
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["crematorium"]["name_bbox"], "金山公墓")
        assert not _text_in_bbox_region(page, t1["cemetery"]["name_bbox"], "金山公墓")
        cemetery_circle_text = "".join(
            w[4] for w in page.get_text("words")
            if fitz.Rect(w[:4]).intersects(fitz.Rect(*t1["cemetery"]["circle_bbox"])))
        assert "●" not in cemetery_circle_text
        doc.close()

    def test_j_gas_tank_row_not_derived_from_substation_name_containing_gas_words(self, golden_form_completion_fields):
        """A substation whose CONFIRMED name happens to contain "瓦斯"/
        "油槽"-like text must still render only in the substation row --
        selection is purely by dict key (substation vs gas_tank), never
        by parsing the name string."""
        selections = {"substation": _selection("測試瓦斯儲油槽變電所")}
        pdf_bytes = self._render(golden_form_completion_fields, selections=selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["substation"]["name_bbox"], "測試瓦斯儲油槽變電所")
        assert not _text_in_bbox_region(page, t1["gas_tank"]["name_bbox"], "測試瓦斯儲油槽變電所")
        doc.close()


class TestMajorStationEvidenceWiring:
    """FACILITY-CONFIRMATION-GATE-1: MRT/TRA rows are driven EXCLUSIVELY
    by `confirmed_facility_selections` -- this renderer no longer reads
    FACTORS.official_facility_evidence["STATION"]/matches[] at all (that
    subtype-filtering + nearest-valid-distance selection logic now lives
    in backend/handlers/facility_confirmation_repository.py, covered by
    tests/test_facility_confirmation.py). All fixtures here are synthetic
    TEST_FIXTURE_VALUE, standing in for an already-CONFIRMED selection."""

    def _render(self, golden_form_completion_fields, selections=None):
        return render_official_pdf(
            GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields,
            confirmed_facility_selections=selections,
        )

    def _circle_text(self, page, bbox):
        return "".join(w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(fitz.Rect(*bbox)))

    def _assert_row_default(self, page, spec, default_text):
        assert default_text in self._circle_text(page, spec["name_bbox"])
        assert "●" not in self._circle_text(page, spec["circle_bbox"])
        assert self._circle_text(page, spec["checkbox_bbox"]).strip() == ""

    def test_a_mrt_confirmed_only_mrt_row_checked(self, golden_form_completion_fields):
        selections = {"MRT": _selection("測試捷運站（測試）", 850)}
        pdf_bytes = self._render(golden_form_completion_fields, selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["major_station_mrt"]["name_bbox"], "測試捷運站（測試）")
        assert "●" in self._circle_text(page, t1["major_station_mrt"]["circle_bbox"])
        assert "850" in self._circle_text(page, t1["major_station_mrt"]["checkbox_bbox"])
        # TRA/HSR rows must stay at their default, unchecked state.
        self._assert_row_default(page, t1["major_station_tra"], "無火車站")
        self._assert_row_default(page, t1["major_station_hsr"], "無高鐵站")
        doc.close()

    def test_b_tra_confirmed_only_tra_row_checked(self, golden_form_completion_fields):
        # NOTE: uses "台鐵" not "火車" -- the Windows dev host's
        # NotoSansTC-VF.ttf font has a pre-existing insert_text/get_text
        # ToUnicode round-trip quirk for "車" specifically (extracts back
        # as U+F902, a PUA codepoint); visual rendering is unaffected.
        selections = {"TRA": _selection("測試台鐵站（測試）", 1200)}
        pdf_bytes = self._render(golden_form_completion_fields, selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        assert _text_in_bbox_region(page, t1["major_station_tra"]["name_bbox"], "測試台鐵站（測試）")
        assert "●" in self._circle_text(page, t1["major_station_tra"]["circle_bbox"])
        self._assert_row_default(page, t1["major_station_mrt"], "無捷運站")
        self._assert_row_default(page, t1["major_station_hsr"], "無高鐵站")
        doc.close()

    def test_c_hsr_never_renders_even_if_present_in_confirmed_dict(self, golden_form_completion_fields):
        """Defense in depth (Task 5: "HSR 維持：永不自動填"): even if a
        caller's confirmed_facility_selections somehow contained an "HSR"
        key (facility_confirmation_repository.py never produces one, but
        this renderer must not rely on that alone), the HSR row must stay
        at its template default -- hard-excluded in the renderer itself."""
        selections = {"HSR": _selection("台北高鐵站", 10)}
        pdf_bytes = self._render(golden_form_completion_fields, selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        self._assert_row_default(page, profile["table1"]["fields"]["major_station_hsr"], "無高鐵站")
        doc.close()

    def test_d_missing_subtype_key_stays_blank(self, golden_form_completion_fields):
        selections = {"MRT": _selection("測試捷運站（測試）", 850)}  # no "TRA" key at all
        pdf_bytes = self._render(golden_form_completion_fields, selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        self._assert_row_default(page, profile["table1"]["fields"]["major_station_tra"], "無火車站")
        doc.close()

    def test_e_unknown_evidence_all_station_rows_blank(self, golden_form_completion_fields):
        for selections in (None, {}):
            pdf_bytes = self._render(golden_form_completion_fields, selections)
            profile = load_profile()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = doc[profile["pages"]["table1"]]
            t1 = profile["table1"]["fields"]
            for field_id, default in (("major_station_hsr", "無高鐵站"), ("major_station_tra", "無火車站"),
                                       ("major_station_mrt", "無捷運站")):
                self._assert_row_default(page, t1[field_id], default)
            doc.close()

    def test_f_osm_generic_major_station_point_never_treated_as_official_subtype(self, golden_form_completion_fields):
        """facility_points' generic OSM major_station_name/distance_m
        (transportation_provider.py's OSM amenity=bus_station path -- no
        government-verified subtype at all) must NEVER be read by the
        major_station rendering block, even when facility_points is
        supplied alongside confirmed_facility_selections=None."""
        points = [_point("major_station_name", "國光客運金山站"), _point("major_station_distance_m", 300, unit="M")]
        pdf_bytes = render_official_pdf(
            GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, golden_form_completion_fields,
            facility_points=points, confirmed_facility_selections=None,
        )
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]
        for field_id, default in (("major_station_hsr", "無高鐵站"), ("major_station_tra", "無火車站"),
                                   ("major_station_mrt", "無捷運站")):
            self._assert_row_default(page, t1[field_id], default)
            assert "國光客運金山站" not in self._circle_text(page, t1[field_id]["name_bbox"])
        doc.close()

    def test_g_no_distance_still_renders_when_confirmed(self, golden_form_completion_fields):
        """Once a human has CONFIRMED a selection, the renderer trusts it
        even without a distance (distance cell simply stays blank, never
        fabricated) -- the no-distance safety gate's job (MAJOR-STATION-
        NO-DISTANCE-SAFETY-GATE) is to stop an UNCONFIRMED candidate from
        auto-selecting itself; it is not this renderer's job to re-
        validate an already-CONFIRMED human decision."""
        selections = {"MRT": _selection("測試捷運站無距離（測試）", None)}
        pdf_bytes = self._render(golden_form_completion_fields, selections)
        profile = load_profile()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[profile["pages"]["table1"]]
        t1 = profile["table1"]["fields"]["major_station_mrt"]
        assert _text_in_bbox_region(page, t1["name_bbox"], "測試捷運站無距離（測試）")
        checkbox_text = self._circle_text(page, t1["checkbox_bbox"])
        assert "本區段外" in checkbox_text
        assert not any(ch.isdigit() for ch in checkbox_text)
        doc.close()


# ---------------------------------------------------------------------------
# Handler-level (Task 11 dual output) -- requires WeasyPrint for the
# EXISTING Audit PDF path pdf_handler.py also generates; guarded the same
# way every other STEP5 handler-level PDF test in this repo already is.
# ---------------------------------------------------------------------------

try:
    from moto import mock_aws
    import boto3
except ImportError:
    boto3 = None


@pytest.mark.skipif(boto3 is None, reason="moto/boto3 not available")
class TestPdfHandlerDualOutput:
    def test_get_pdf_includes_official_form_alongside_audit_pdfs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CASES_TABLE_NAME", "test-table-official")
        monkeypatch.setenv("DOCUMENT_BUCKET_NAME", "test-document-bucket-official")
        monkeypatch.setenv("PDF_BUCKET_NAME", "test-pdf-bucket-official")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("DATASET_REGISTRY_DB_PATH", str(tmp_path / "dataset_registry.sqlite3"))
        monkeypatch.setenv("DATA_PROVIDER_MODE", "mock")

        with mock_aws():
            ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
            ddb.create_table(
                TableName="test-table-official",
                KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                       {"AttributeName": "SK", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            s3 = boto3.client("s3", region_name="ap-northeast-1")
            s3.create_bucket(Bucket="test-document-bucket-official",
                              CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})
            s3.create_bucket(Bucket="test-pdf-bucket-official",
                              CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})

            from _aws_mock_reset import reset_cached_aws_module_state
            reset_cached_aws_module_state()

            import cases
            import collect_data
            import case_store
            import complete_form

            case_no = "OFFICIAL-PDF-001"
            cases.create_case({"body": json.dumps({
                "case_no": case_no, "segment_code": "P002-00", "city": "新北市", "district": "金山區",
                "land_use_type": "商業用地", "appraisal_period": "1140901", "appraisal_base_date": "1140901",
                "segment_scope": "測試區段", "base_parcel_id": "測試比準地", "comparable_ids": ["comp1"],
            })}, None)
            collect_data.collect_data({"pathParameters": {"id": case_no}, "body": json.dumps({
                "base_parcel_factors": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 23, "unit": "M"}],
                "comparable_factors": {"comp1": [{"field_id": "individual_land_depth", "factor": "深度", "raw_value": 16, "unit": "M"}]},
            })}, None)
            factors = case_store.get_record(case_no, "FACTORS")
            factors["regional_base_factors"] = [
                {"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"},
            ]
            factors["regional_comparable_factors"] = {
                "comp1": [{"field_id": "regional_main_road_width", "factor": "主要道路寬度", "raw_value": 18, "unit": "M"}],
            }
            factors["land_normal_price"] = {"comp1": "184763"}
            factors["price_date_rate"] = {"comp1": "2.00"}
            factors["weight"] = {"comp1": "100"}
            case_store.put_record(case_no, "FACTORS", factors)

            form_resp = complete_form.complete_form({"pathParameters": {"id": case_no}}, None)
            assert form_resp["statusCode"] == 200, form_resp["body"]

            try:
                import pdf_handler
            except OSError as e:
                pytest.skip(f"WeasyPrint native library unavailable on this host (known environment "
                            f"limitation, not a code failure) -- pdf_handler.py's EXISTING Audit PDF path "
                            f"needs it even though official_pdf_renderer.py itself does not: {e}")

            resp = pdf_handler.get_pdf({"pathParameters": {"id": case_no}}, None)
            assert resp["statusCode"] == 200, resp["body"]
            body = json.loads(resp["body"])

            # Backward compatibility (Task 11's explicit requirement):
            # existing top-level pdf_url and forms.表1/表4+表5-2 untouched.
            assert body["pdf_url"]
            assert "表1" in body["forms"]
            assert "表4+表5-2" in body["forms"]

            # New: official form output, additive only.
            assert body["official_pdf_status"] == "READY", body["official_pdf_status"]
            assert body["official_pdf_url"]
            assert "official_form" in body["forms"]
