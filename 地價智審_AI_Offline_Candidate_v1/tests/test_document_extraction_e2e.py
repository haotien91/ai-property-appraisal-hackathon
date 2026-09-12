# -*- coding: utf-8 -*-
"""
Golden/Error Document E2E: PDF -> classify -> extract -> confirm if needed
-> reconstruction (SubmittedFormData) -> AuditEngine.review(), proving the
Independent Uploaded Document Extraction Vertical Slice actually connects
to the existing, already-tested Smart Review pipeline -- not a parallel
demo that never touches AuditEngine.

Golden Case caveat (item 8 of this round's instructions, honestly
enforced): main_road_width=18M is the report's own SUBMITTED value (成估
appraiser's field observation) with no independently-confirmable official
source (see docs/backlog.md's Road Width Multi-Evidence Model survey).
Extracting "18" from the PDF text layer does NOT upgrade it to an official
RoadWidthEvidence -- no road_width_evidence is injected here, so the
road-width check honestly resolves ROAD_WIDTH_UNAVAILABLE, same as every
other round that has tested this distinction.
"""
import os
import sys
from decimal import Decimal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))

import pytest  # noqa: E402
from providers.document_extraction_provider import (  # noqa: E402
    LocalExtractionProvider, FixtureExtractionProvider, build_fixture_field,
)
from domain.models import (  # noqa: E402
    FormType, FormClassificationResult, CompetitionCase, FactorInput, Evidence, SourceType,
    IssueType, CheckType, ExtractionRole, ExtractionSubjectRole,
)
# SHULIN-COMPETITION-RULE-PACK-A2-FINAL-GATE-1 Task 2 fix: bare imports, not
# "engine.xxx" -- matching engine/extraction_to_submitted_form.py's own
# (now-bare) internal imports and every real production caller, so this
# test's AuditEngine/SubmittedFormData share ONE canonical module identity
# with the ones build_submitted_form_from_extraction() constructs
# internally, rather than two distinct classes of the same name.
from human_confirmation import flag_low_confidence, resolve_confirmed_values  # noqa: E402
from extraction_to_submitted_form import build_submitted_form_from_extraction  # noqa: E402
from audit_engine import AuditEngine  # noqa: E402
from rule_engine import RuleEngine  # noqa: E402

GOLDEN_PDF = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")


def _rule_engine():
    import json
    with open(os.path.join(REPO_ROOT, "data", "rules", "regional_rules.json"), encoding="utf-8") as f:
        reg = json.load(f)["rules"]
    with open(os.path.join(REPO_ROOT, "data", "rules", "individual_rules.json"), encoding="utf-8") as f:
        ind = json.load(f)["rules"]
    return RuleEngine(reg + ind)


def _minimal_case(case_no="EXTRACTION-E2E-001"):
    return CompetitionCase(
        case_no=case_no, appraisal_period="1140901", appraisal_base_date="1140901",
        segment_code="P002-00", segment_scope="金山區溫泉段", city="新北市", district="金山區",
        land_use_type="商業用地", base_parcel_id="金美段489地號",
        base_parcel_factors=[], comparable_ids=[],
    )


def _official_zone_factor(zone_name="第二種商業區"):
    ev = Evidence(source="測試案件既有結構化資料", source_type=SourceType.AI_ASSISTED_FILL)
    return [FactorInput(field_id="regional_land_use_zone", factor="使用分區(使用地類別)",
                         raw_value=zone_name, unit=None, evidence=ev)]


@pytest.fixture(scope="module")
def _require_fitz():
    pytest.importorskip("fitz")


class TestGoldenDocumentE2E:
    def test_golden_pdf_classify_extract_confirm_reconstruct_audit(self, _require_fitz):
        # 1. classify
        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        table1 = [c for c in classifications if c.form_type == FormType.TABLE1_LAND_SEGMENT_SURVEY]
        assert len(table1) == 1

        # 2. extract
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        fields = flag_low_confidence(fields)  # 3. confirm if needed -- none needed here (all high-confidence)
        assert all(not f.requires_manual_review for f in fields
                   if f.field_id in ("individual_zoning_designation", "individual_building_coverage_ratio",
                                      "individual_floor_area_ratio", "main_road_width"))

        # 4. reconstruction -> SubmittedFormData
        submitted = build_submitted_form_from_extraction(_minimal_case().case_no, fields)
        assert submitted.submitted_land_use_zone == "第二種商業區"
        assert submitted.submitted_building_coverage_rate == "70"
        assert submitted.submitted_floor_area_ratio == "240"
        assert submitted.submitted_main_road_width == "18"
        # plan_id is always human-supplied, never extracted from the PDF text
        # itself (it is not a literal field on the report) -- set here the
        # same way any real workflow would (a human/case-setup step).
        submitted.internal_plan_id = "jinshan"
        submitted.confirmed_plan_name = "金山都市計畫"
        submitted.plan_identification_source = "MANUAL_INPUT"

        # 5. existing Smart Review pipeline, untouched
        case = _minimal_case()
        audit = AuditEngine(_rule_engine(), os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        result = audit.review(case, submitted, _official_zone_factor(), {})

        bcr = [i for i in result.issues if i.field == "building_coverage_ratio"][0]
        far = [i for i in result.issues if i.field == "floor_area_ratio"][0]
        road = [i for i in result.issues if i.field == "main_road_width"][0]

        assert bcr.issue_type == IssueType.PASSED
        assert far.issue_type == IssueType.PASSED
        assert far.explanation_data.check_type == CheckType.PASSED_CHECK
        # main_road_width: submitted=18 (extracted) is preserved, but with
        # no road_width_evidence injected, this honestly stays UNAVAILABLE
        # -- 18m is never silently promoted from SUBMITTED_VALUE to an
        # official reference just because OCR read it off the page.
        assert road.issue_type == IssueType.MISSING
        assert road.explanation_data.check_type == CheckType.ROAD_WIDTH_UNAVAILABLE
        assert road.submitted_value == "18"


class TestErrorDocumentE2E:
    """A fixture 'document' whose FAR was altered from Golden Case's true
    240 to 300 -- the exact regression this round's instructions call out
    as critical proof that OCR's submitted value and the official external
    reference are genuinely two separate things, not the same number
    trivially compared to itself."""

    def _fixture_provider(self, far_raw_text="300%", far_normalized="300", confidence=0.95):
        classification = FormClassificationResult(
            page_number=1, form_type=FormType.TABLE1_LAND_SEGMENT_SURVEY,
            confidence=1.0, evidence=["fixture_document"], requires_manual_review=False,
        )
        fields = [
            build_fixture_field("individual_zoning_designation", "第二種商業區", confidence=0.95),
            build_fixture_field("individual_building_coverage_ratio", "70%", "70", unit="%", confidence=0.95),
            build_fixture_field("individual_floor_area_ratio", far_raw_text, far_normalized,
                                 unit="%", confidence=confidence),
        ]
        return FixtureExtractionProvider(classification, fields)

    def test_far_300_extracted_vs_official_240_is_inconsistent(self):
        provider = self._fixture_provider()
        classifications = provider.classify("error_document.pdf")
        fields = provider.extract_fields("error_document.pdf", classifications)
        fields = flag_low_confidence(fields)
        assert all(not f.requires_manual_review for f in fields)  # confidence=0.95, no confirmation needed

        submitted = build_submitted_form_from_extraction("ERROR-DOC-001", fields)
        assert submitted.submitted_floor_area_ratio == "300"  # extracted value preserved verbatim
        submitted.internal_plan_id = "jinshan"

        case = _minimal_case("ERROR-DOC-001")
        audit = AuditEngine(_rule_engine(), os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        result = audit.review(case, submitted, _official_zone_factor(), {})

        far = [i for i in result.issues if i.field == "floor_area_ratio"][0]
        assert far.issue_type == IssueType.INCONSISTENT
        assert far.explanation_data.check_type == CheckType.FLOOR_AREA_RATIO_INCONSISTENT
        assert far.submitted_value == "300"   # OCR's submitted value -- untouched
        assert far.expected_value == "240"    # official external reference -- independently resolved
        assert far.submitted_value != far.expected_value

    def test_low_confidence_far_extraction_blocks_until_confirmed(self):
        """If the extraction itself were low-confidence, the mismatch must
        NOT reach AuditEngine at all until a human confirms -- proving
        item 7's gate is real, not just documented."""
        provider = self._fixture_provider(confidence=0.3)
        classifications = provider.classify("error_document.pdf")
        fields = provider.extract_fields("error_document.pdf", classifications)
        fields = flag_low_confidence(fields)

        far_field = next(f for f in fields if f.field_id == "individual_floor_area_ratio")
        assert far_field.requires_manual_review is True

        # No confirmation supplied -- reconstruction must not leak the
        # unconfirmed value through.
        submitted = build_submitted_form_from_extraction("ERROR-DOC-002", fields)
        assert submitted.submitted_floor_area_ratio is None

        case = _minimal_case("ERROR-DOC-002")
        submitted.internal_plan_id = "jinshan"
        audit = AuditEngine(_rule_engine(), os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        result = audit.review(case, submitted, _official_zone_factor(), {})
        far = [i for i in result.issues if i.field == "floor_area_ratio"][0]
        # MISSING (can't verify, unconfirmed), never a fabricated PASS or
        # a premature INCONSISTENT based on an unreviewed OCR guess.
        assert far.issue_type == IssueType.MISSING
        assert far.explanation_data.check_type == CheckType.MISSING


def _golden_case_module():
    """Adds data/golden and data/demo_errors to sys.path (the established
    convention, see tests/test_smart_review.py) and returns the real
    Golden Case CompetitionCase + regional factor lists + the existing
    hand-built demo-error fixture builder -- never re-typed/duplicated
    here."""
    for p in (os.path.join("data", "golden"), os.path.join("data", "demo_errors")):
        path = os.path.join(REPO_ROOT, p)
        if path not in sys.path:
            sys.path.insert(0, path)
    from golden_case_input import case as GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL  # noqa: E402
    from build_demo_submission import build_submitted_form_with_demo_errors  # noqa: E402
    return GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, build_submitted_form_with_demo_errors


class TestGoldenTable52RealPdfE2E:
    """GOLDEN REAL-PDF E2E for 表5-2 -- LocalExtractionProvider reads the
    ACTUAL archived 查估書表範本.pdf (no FixtureExtractionProvider anywhere
    in this class). Proves the row/column-band extraction algorithm's real
    output, reconstructed via the BASE-grade-only mapping (Contract v3
    §3/§4, re-verified against the real AuditEngine._review_regional_
    factors consumer), reproduces the SAME AuditIssue verdicts as
    data/demo_errors/build_demo_submission.py's independently-hand-built
    (but real-RuleEngine-derived) fixture pipeline, for every factor BOTH
    pipelines can structurally verify. This is a cross-pipeline
    EQUIVALENCE check between two independently-constructed
    SubmittedFormData builders fed into the SAME unmodified AuditEngine --
    never an a-priori "must be PASSED because it's Golden Case" shortcut."""

    def test_extraction_derived_grades_match_existing_fixture_pipeline_for_uncorrupted_factors(self, _require_fitz):
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, build_with_demo_errors = _golden_case_module()
        rule_engine_instance = _rule_engine()

        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        fields = flag_low_confidence(fields)

        # comparable_ids comes from the REAL structured case, never guessed
        # from the PDF's own comparable-column layout (Contract v3 §4).
        extraction_submitted = build_submitted_form_from_extraction(
            GOLDEN_CASE.case_no, fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        fixture_submitted = build_with_demo_errors(rule_engine_instance, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)

        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        extraction_result = audit.review(GOLDEN_CASE, extraction_submitted, BASE_REGIONAL, COMP_REGIONAL)
        fixture_result = audit.review(GOLDEN_CASE, fixture_submitted, BASE_REGIONAL, COMP_REGIONAL)

        comparable_id = GOLDEN_CASE.comparable_ids[0]
        # Grade Representation Contract Phase D: Check A (grade identity)
        # now compares CODE, not text -- sidestepping the "無 vs 優"
        # representation question entirely. Only regional_main_road_width
        # is excluded, for the one reason that remains real: Case A's
        # KNOWN, documented deliberate corruption in the FIXTURE pipeline
        # only (code=1 vs the PDF's real code=3) -- extraction correctly
        # disagreeing with a deliberate corruption is correct behavior,
        # not a gap. The 2 boolean factors (regional_construction_
        # prohibited/_restricted) are NO LONGER excluded: their PDF code
        # (1) matches RuleEngine's expected code (1) exactly, same as
        # every other factor -- the prior round's "grade vocabulary gap"
        # was resolved by comparing codes instead of text, see
        # TestGradeRepresentationContractResolved below.
        checked = 0
        for base_input in BASE_REGIONAL:
            field_id = base_input.field_id
            if field_id == "regional_main_road_width":
                continue
            full_field_id = f"{field_id}_adjustment_pct_{comparable_id}"
            extraction_issue = next(i for i in extraction_result.issues if i.field == full_field_id)
            fixture_issue = next(i for i in fixture_result.issues if i.field == full_field_id)
            assert extraction_issue.issue_type == fixture_issue.issue_type, field_id
            assert extraction_issue.expected_value == fixture_issue.expected_value, field_id
            checked += 1
        assert checked == 27  # 28 regional factors - the 1 remaining known, verified exclusion

    def test_real_main_road_width_extraction_correctly_disagrees_with_case_a_fixture_corruption(self, _require_fitz):
        """The excluded factor above, checked explicitly in the OTHER
        direction: real PDF extraction reads the TRUE printed code (3) and
        therefore PASSES Check A, while the fixture pipeline's
        deliberately wrong code (1) fails -- proving the exclusion above
        is not hiding a real extraction bug, it is the expected, correct
        divergence."""
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, build_with_demo_errors = _golden_case_module()
        rule_engine_instance = _rule_engine()

        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        fields = flag_low_confidence(fields)
        extraction_submitted = build_submitted_form_from_extraction(
            GOLDEN_CASE.case_no, fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        fixture_submitted = build_with_demo_errors(rule_engine_instance, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)

        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        extraction_result = audit.review(GOLDEN_CASE, extraction_submitted, BASE_REGIONAL, COMP_REGIONAL)
        fixture_result = audit.review(GOLDEN_CASE, fixture_submitted, BASE_REGIONAL, COMP_REGIONAL)

        comparable_id = GOLDEN_CASE.comparable_ids[0]
        full_field_id = f"regional_main_road_width_adjustment_pct_{comparable_id}"
        extraction_issue = next(i for i in extraction_result.issues if i.field == full_field_id)
        fixture_issue = next(i for i in fixture_result.issues if i.field == full_field_id)

        assert extraction_issue.submitted_value == "3"  # real PDF's true printed grade_code
        assert extraction_issue.issue_type == IssueType.PASSED
        assert fixture_issue.submitted_value == "1"  # Case A's deliberate code corruption
        assert fixture_issue.issue_type == IssueType.ERROR

    def test_boolean_factor_grade_vocabulary_gap_resolved_by_grade_identity_check(self, _require_fitz):
        """Supersedes the prior round's documented gap: comparing
        submitted_grade_code (Check A) instead of submitted_grade_text
        against a text-only expected.grade resolves the false positive
        for regional_construction_prohibited/_restricted entirely -- the
        real PDF's printed text ("無") is preserved verbatim as evidence
        (never rewritten to "優"), and is separately validated by Check B
        as an officially-supported representation for these 2 factors
        (see the Grade Representation Contract audit's evidence: 評價基準
        明細表範例 explicitly defines 優=無/劣=有 for both)."""
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, build_with_demo_errors = _golden_case_module()
        rule_engine_instance = _rule_engine()

        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        fields = flag_low_confidence(fields)
        extraction_submitted = build_submitted_form_from_extraction(
            GOLDEN_CASE.case_no, fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        comparable_id = GOLDEN_CASE.comparable_ids[0]

        for field_id in ("regional_construction_prohibited", "regional_construction_restricted"):
            # Original printed text is preserved verbatim -- never rewritten.
            assert extraction_submitted.submitted_grades[(field_id, comparable_id)] == "無"
            assert extraction_submitted.submitted_grade_codes[(field_id, comparable_id)] == "1"

        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        result = audit.review(GOLDEN_CASE, extraction_submitted, BASE_REGIONAL, COMP_REGIONAL)
        for field_id in ("regional_construction_prohibited", "regional_construction_restricted"):
            identity_issue = next(i for i in result.issues if i.field == f"{field_id}_adjustment_pct_{comparable_id}")
            representation_issue = next(
                i for i in result.issues if i.field == f"{field_id}_grade_representation_{comparable_id}"
            )
            assert identity_issue.issue_type == IssueType.PASSED
            assert identity_issue.submitted_value == "1"
            assert identity_issue.expected_value == "1"
            assert representation_issue.issue_type == IssueType.PASSED
            assert representation_issue.submitted_value == "無"  # preserved verbatim, not "優"

    def test_extraction_derived_total_matches_existing_fixture_pipelines_own_regional_total(self, _require_fitz):
        """Guardrail 2: compares against the EXISTING fixture pipeline's
        OWN submitted_totals['regional_total_...'] -- 表5-2's side, which
        build_demo_submission.py's Case C docstring confirms stays CORRECT
        at 0.00 -- never against Error Case C's deliberately-corrupted
        表4-side value (region_adjustment_rate_...), which would make that
        injected error look like the oracle."""
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, build_with_demo_errors = _golden_case_module()
        rule_engine_instance = _rule_engine()

        provider = LocalExtractionProvider()
        classifications = provider.classify(GOLDEN_PDF)
        fields = provider.extract_fields(GOLDEN_PDF, classifications)
        fields = flag_low_confidence(fields)
        extraction_submitted = build_submitted_form_from_extraction(
            GOLDEN_CASE.case_no, fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        fixture_submitted = build_with_demo_errors(rule_engine_instance, GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL)

        comparable_id = GOLDEN_CASE.comparable_ids[0]
        key = f"regional_total_{comparable_id}"
        assert extraction_submitted.submitted_totals[key] == fixture_submitted.submitted_totals[key]
        assert extraction_submitted.submitted_totals[key] == "0.00"


class TestFixtureErrorE2ETable52:
    """FIXTURE Error E2E for 表5-2 -- NOT Real Error PDF E2E (Contract v3
    §1/§8: that would require a second, genuinely erroneous real PDF,
    which does not exist in this repo and is not built this round).
    FixtureExtractionProvider-style synthetic ExtractedFields (built via
    build_fixture_field(), MOCK_FIXTURE) supply a deliberately wrong BASE
    grade, reusing the EXISTING, already-verified Demo Error Case A numbers
    (優 vs true 普通 for regional_main_road_width) -- this tests the
    reconstruction -> AuditEngine seam in isolation from real PDF reading,
    never claims to prove LocalExtractionProvider itself reads a real
    erroneous document correctly."""

    def test_fixture_wrong_base_grade_code_and_text_produces_grade_error(self):
        """Grade Representation Contract Phase D: Check A (the valuation-
        critical verdict) is driven by grade_code, not text -- a fixture
        supplying only wrong TEXT with no code would resolve to MISSING on
        Check A (code cell genuinely absent), not ERROR. A real wrong
        submission corrupts code and text TOGETHER (self-consistently),
        exactly like build_demo_submission.py's Case A."""
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, _build_with_demo_errors = _golden_case_module()
        rule_engine_instance = _rule_engine()
        comparable_id = GOLDEN_CASE.comparable_ids[0]

        fields = [
            build_fixture_field(
                "regional_main_road_width", "1", "1", confidence=0.95,
                extraction_role=ExtractionRole.GRADE_CODE,
                extraction_subject_role=ExtractionSubjectRole.BASE, comparable_slot=None,
            ),
            build_fixture_field(
                "regional_main_road_width", "優", "優", confidence=0.95,
                extraction_role=ExtractionRole.GRADE_TEXT,
                extraction_subject_role=ExtractionSubjectRole.BASE, comparable_slot=None,
            ),
        ]
        fields = flag_low_confidence(fields)
        submitted = build_submitted_form_from_extraction(
            "FIXTURE-ERROR-T52-001", fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        assert submitted.submitted_grade_codes[("regional_main_road_width", comparable_id)] == "1"
        assert submitted.submitted_grades[("regional_main_road_width", comparable_id)] == "優"

        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        result = audit.review(GOLDEN_CASE, submitted, BASE_REGIONAL, COMP_REGIONAL)

        identity_issue = next(i for i in result.issues
                               if i.field == f"regional_main_road_width_adjustment_pct_{comparable_id}")
        representation_issue = next(i for i in result.issues
                                     if i.field == f"regional_main_road_width_grade_representation_{comparable_id}")
        assert identity_issue.issue_type == IssueType.ERROR
        assert identity_issue.explanation_data.check_type == CheckType.GRADE_ERROR
        assert identity_issue.submitted_value == "1"
        assert identity_issue.expected_value == "3"
        # Representation is self-consistent (code=1's own text is 優) --
        # orthogonal to Check A's failure, never conflated with it.
        assert representation_issue.issue_type == IssueType.PASSED

    def test_fixture_wrong_code_only_with_no_matching_text_is_missing_not_error(self):
        """If only the code cell is extracted with no corresponding text
        cell (e.g. a CELL_BLANK text cell upstream), Check A still fires
        correctly off the code alone; Check B correctly reports MISSING
        (cannot assess representation without both) rather than guessing."""
        GOLDEN_CASE, BASE_REGIONAL, COMP_REGIONAL, _build_with_demo_errors = _golden_case_module()
        rule_engine_instance = _rule_engine()
        comparable_id = GOLDEN_CASE.comparable_ids[0]

        fields = [build_fixture_field(
            "regional_main_road_width", "1", "1", confidence=0.95,
            extraction_role=ExtractionRole.GRADE_CODE,
            extraction_subject_role=ExtractionSubjectRole.BASE, comparable_slot=None,
        )]
        fields = flag_low_confidence(fields)
        submitted = build_submitted_form_from_extraction(
            "FIXTURE-ERROR-T52-004", fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        audit = AuditEngine(rule_engine_instance, os.path.join(REPO_ROOT, "data", "dependency_graph.json"))
        result = audit.review(GOLDEN_CASE, submitted, BASE_REGIONAL, COMP_REGIONAL)

        identity_issue = next(i for i in result.issues
                               if i.field == f"regional_main_road_width_adjustment_pct_{comparable_id}")
        representation_issue = next(i for i in result.issues
                                     if i.field == f"regional_main_road_width_grade_representation_{comparable_id}")
        assert identity_issue.issue_type == IssueType.ERROR  # code=1 vs expected=3, still fires
        assert representation_issue.issue_type == IssueType.MISSING  # no text to assess

    def test_fixture_comparable_grade_text_never_reaches_submitted_form(self):
        """COMPARABLE/GRADE_TEXT is evidence-only this round (§3/§4: no
        existing AuditEngine consumer for a comparable's own regional
        grade) -- even with a high-confidence, fully-confirmed extraction,
        it must never appear in submitted_grades."""
        GOLDEN_CASE, _BASE_REGIONAL, _COMP_REGIONAL, _build = _golden_case_module()
        comparable_id = GOLDEN_CASE.comparable_ids[0]

        fields = [build_fixture_field(
            "regional_main_road_width", "普通", "普通", confidence=0.95,
            extraction_role=ExtractionRole.GRADE_TEXT,
            extraction_subject_role=ExtractionSubjectRole.COMPARABLE, comparable_slot=1,
        )]
        fields = flag_low_confidence(fields)
        submitted = build_submitted_form_from_extraction(
            "FIXTURE-ERROR-T52-002", fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        assert ("regional_main_road_width", comparable_id) not in submitted.submitted_grades

    def test_fixture_base_grade_code_reaches_submitted_grade_codes_not_submitted_grades(self):
        """Grade Representation Contract Phase D revises the prior round's
        blanket "GRADE_CODE is evidence-only" rule: BASE/GRADE_CODE now
        reconstructs into the SEPARATE submitted_grade_codes dict (never
        into submitted_grades, which is TEXT-only) -- Check A's whole
        point is having code and text as two independent submitted
        signals, not one collapsing into the other."""
        GOLDEN_CASE, _BASE_REGIONAL, _COMP_REGIONAL, _build = _golden_case_module()
        comparable_id = GOLDEN_CASE.comparable_ids[0]

        fields = [build_fixture_field(
            "regional_main_road_width", "1", "1", confidence=0.95,
            extraction_role=ExtractionRole.GRADE_CODE,
            extraction_subject_role=ExtractionSubjectRole.BASE, comparable_slot=None,
        )]
        fields = flag_low_confidence(fields)
        submitted = build_submitted_form_from_extraction(
            "FIXTURE-ERROR-T52-003", fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        assert submitted.submitted_grade_codes[("regional_main_road_width", comparable_id)] == "1"
        assert ("regional_main_road_width", comparable_id) not in submitted.submitted_grades

    def test_fixture_comparable_grade_code_stays_evidence_only(self):
        """Unlike BASE/GRADE_CODE, COMPARABLE/GRADE_CODE still has no
        AuditEngine consumer -- Check A/B are BASE-only, matching Check
        A/B's own scope (both compare BASE's submitted grade against the
        SAME factor's raw-value-derived expected grade)."""
        GOLDEN_CASE, _BASE_REGIONAL, _COMP_REGIONAL, _build = _golden_case_module()
        comparable_id = GOLDEN_CASE.comparable_ids[0]

        fields = [build_fixture_field(
            "regional_main_road_width", "1", "1", confidence=0.95,
            extraction_role=ExtractionRole.GRADE_CODE,
            extraction_subject_role=ExtractionSubjectRole.COMPARABLE, comparable_slot=1,
        )]
        fields = flag_low_confidence(fields)
        submitted = build_submitted_form_from_extraction(
            "FIXTURE-ERROR-T52-005", fields, comparable_ids=GOLDEN_CASE.comparable_ids,
        )
        assert ("regional_main_road_width", comparable_id) not in submitted.submitted_grade_codes
