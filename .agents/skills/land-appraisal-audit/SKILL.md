---
name: land-appraisal-audit
description: This skill should be used when modifying or extending engine/ (RuleEngine, CalculationEngine, AuditEngine, ComparableSelectionEngine, LandUseRatioEngine/Validator, RoadWidthResolver/Validator, FormClassifier, human_confirmation), domain/models.py's Issue/Evidence/resolution models, or tests/ covering Smart Review, cross-form validation, comparable selection (§25/§26/§27), LandUseRatio resolution, RoadWidth evidence, or document extraction in this land-appraisal-review repo. Also use when asked "does this comply with §25/26/27", "can OCR/AI set a grade or adjustment", "is this Golden Case number a general rule", "how should this Evidence/UNKNOWN case be reported", or when adding a new deterministic check, Issue type, or Evidence-producing code path.
version: 0.1.0
---

# Land Appraisal Audit Rules

This repo's Smart Review pipeline (`engine/audit_engine.py` orchestrating
Phase 6's five deterministic components — `RuleValidator` /
`CalculationValidator` / `CrossFormValidationEngine` /
`DependencyImpactAnalyzer`, plus `RuleEngine`/`CalculationEngine`
underneath — and the later `ComparableSelectionEngine` /
`LandUseRatioValidator` / `RoadWidthValidator` additions built on the same
deterministic-only principle) exists to catch appraisal report errors
deterministically, never by guessing. Every rule below is enforced by
current code and locked in by passing tests — none is aspirational.

## Submitted vs Official/Reference Separation

MUST keep "what was submitted" (`SubmittedFormData.submitted_*`,
`AuditIssue.submitted_value`) and "what the system independently resolved"
(`AuditIssue.expected_value`, `LandUseRatioResolutionResult`,
`RoadWidthResolutionResult`) as two distinct values that are never allowed
to overwrite one another, even when they agree. This is why
`AuditEngine._official_regional_raw_value()` reads `base_regional_factors`
(independently-sourced) rather than the submitted form, and why
`case_reconstruction.extract_submitted_land_use_ratio_fields()` reads
`case.base_parcel_factors` (user-submitted) rather than Provider output.
MUST NOT let a Provider's resolved value silently become the submitted
value, or vice versa — a check comparing a value to itself is tautological
and catches nothing (see `test_submitted_value_never_overwritten_by_
provider_reference` and its RoadWidth/LandUseRatio equivalents).

## Deterministic Pipeline Ownership

MUST NOT let OCR/extraction/LLM output directly become a grade (優/良/
普通/差/劣), an adjustment rate, a cross-form correctness verdict, or a
final valuation decision. Only `RuleEngine.grade()` may assign a grade;
only `AdjustmentEngine`/the adjustment matrix may assign an adjustment
rate; only `CalculationEngine` may recompute a numeric result; only
`AuditEngine`/its Validators may render an audit verdict.
`ExtractedField` (`domain/models.py`) MUST carry only `raw_text`/
`normalized_value` — never a grade label or verdict string (see
`test_ocr_never_outputs_a_grade_or_adjustment_verdict`). This separation
is the direct implementation of the master instructions' "LLM/OCR負責理解，
Deterministic Engine負責正確性" and Phase 6's five-component design
(`docs/phase6/smart_review_spec.md`).

## Human Confirmation Gate

A field extracted below the confidence threshold (`engine/human_
confirmation.py`, default 0.75, configurable) MUST have
`requires_manual_review=True`, and MUST NOT reach
`extraction_to_submitted_form.py`'s reconstruction step until a matching
`HumanConfirmationRecord.confirmed_value` exists —
`resolve_confirmed_values()` returns `None` for an unconfirmed flagged
field by construction, not by convention. `requires_manual_review` MUST
only ever be upgraded (False→True), never downgraded, by a later step.

## Smart Review Traceability

Every `AuditIssue` MUST carry `submitted_value`, `expected_value`, and
`explanation_data.computed_steps` sufficient for a human reviewer to see
why a verdict was reached without re-deriving it — never a bare pass/fail
with no evidence. `explanation_data.check_type` MUST identify which
deterministic check produced the issue.

## Cross-Form Validation Independence

A cross-form check (e.g. `CrossFormValidationEngine.validate_regional_
total_matches_table4`) MUST compare two independently-populated stored
fields, never the same underlying value read twice into two keys —
tampering with either side alone must be independently detectable (see
`TestCrossFormIndependentFieldIdentity`'s tamper-one-side-only tests).

## Three-State Verification Outcome Semantics

A Provider-layer `UNKNOWN`/`UNCLASSIFIABLE` value (see the `provider-contract`
skill) surfaces at the Issue layer as one of three `IssueType`s that MUST
NOT be conflated with one another:

- **Cannot verify** (no reference data exists at all) →
  `IssueType.MISSING` (e.g. `LAND_USE_RATIO_RULE_UNAVAILABLE`,
  `ROAD_WIDTH_UNAVAILABLE`). This is never itself a finding against the
  submission.
- **Reference data conflicts with itself** (2+ independently-sourced
  values disagree) → `IssueType.WARNING` (e.g. `ROAD_WIDTH_EVIDENCE_
  CONFLICT`, `ZONE_CATEGORY_NORMALIZATION_UNCERTAIN`). MUST NOT be
  reported as `INCONSISTENT` even when the submitted value happens to
  match one of the disagreeing candidates.
- **Confirmed mismatch against a single, high-confidence resolved
  reference** → `IssueType.INCONSISTENT` (e.g. `FLOOR_AREA_RATIO_
  INCONSISTENT`, `ROAD_WIDTH_INCONSISTENT`). MUST only be produced when
  resolution is unambiguous and not itself flagged `requires_manual_
  review` (a stale or low-confidence resolved reference MUST NOT drive a
  firm INCONSISTENT/PASSED verdict — downgrade to WARNING instead).

## §25/§26/§27 Legal Basis (不動產估價技術規則, 內政部)

Verified directly against `law.moj.gov.tw/LawClass/LawAll.aspx?pcode=D0060077`
(see `data/sources/source_manifest.json`'s `law_estate_valuation_technical_
rules` entry) — implemented in `engine/comparable_selection_engine.py`.

- **§25 single-item threshold (15%)**: a confirmed, unambiguous legal
  formula. Any single regional/individual adjustment item whose absolute
  value exceeds 15% MUST drive `excluded=True`. This is the ONLY input
  that may set `excluded`.
- **§25 total-adjustment threshold (30%)**: the regulation names its four
  inputs (情況、價格日期、區域因素、個別因素調整) but defines no formula
  for combining them into one "總調整率" number. MUST NOT treat "調整
  百分率絕對值加總" (the 作業手冊's only such formula, p.52-53 item「十」)
  as §25's formula — that formula is explicitly for §27's weight decision,
  not §25's exclusion test. `total_adjustment_legal_basis` MUST stay
  `"LEGAL_BASIS_UNCONFIRMED"`, and a comparable whose either candidate
  reading exceeds 30% MUST resolve to `ExclusionDeterminationStatus.
  UNDETERMINED` — a real third state, not "not excluded" and not a
  fabricated legal conclusion.
- **§26 (20% price-gap threshold)**: confirmed, exact formula
  `(high-low)/((high+low)/2)`; requires 2+ trial prices, raises rather
  than silently no-op-ing for fewer.
- **§27 (3+ comparables, no weight formula)**: the regulation's own text
  requires "三件以上比較標的" and gives two qualitative considerations
  (資料可信度／相近程度), no equation. `suggest_weights()`'s inverse-
  adjustment-magnitude weighting is a disclosed CONVENTION, not a legal
  formula — MUST NOT be described as the official/required method.
  `SuggestedWeight.requires_human_confirmation` MUST stay `True`; nothing
  may auto-apply a suggested weight into
  `CalculationEngine.base_parcel_comparison_price()` without that
  confirmation. Final valuation decisions MUST NOT be made by AI/LLM
  autonomously.
- If a §25/§26/§27 question cannot be answered from this repo's own
  archived law snapshot, do not fill the gap with general real-estate
  knowledge — report it unconfirmed, matching the existing pattern.

## LandUseRatio Resolution & Plan-Specific Uncertainty

`LandUseRatioEngine`'s two-layer priority (individual 都市計畫 registry →
citywide 附表一 common table → UNAVAILABLE) MUST NOT be flattened into a
single table, and a zone's resolution under one `plan_id` MUST NOT be
reused for a different (or absent) `plan_id` — a plan-specific 建蔽率/容積率
number is not the same fact as the citywide default. Every resolved
reference MUST carry `resolution_layer`/`legal_source`/`dataset_version`/
`requires_manual_review` through to the Issue's `computed_steps` (see
`land_use_ratio_validator.py`'s `_evidence_steps`), never just a bare
number. `plan_id` is always human-supplied — MUST NOT be inferred from
coordinates (the NTPC plan-boundary shapefile's name field is corrupted at
the source; see `providers/base.py`'s `ProviderContext` docstring).

## RoadWidth Multi-Evidence Conflict Handling

`RoadWidthResolver` MUST NOT average or silently pick between disagreeing
evidence values, regardless of `RoadWidthEvidenceType` priority tier —
priority order (`OFFICIAL_ATTRIBUTE` > `URBAN_PLAN_DESIGN_WIDTH` >
`EXTERNAL_MAP_ATTRIBUTE` > `GEOMETRIC_ESTIMATE`) breaks ties ONLY among
evidence that already agrees on one value; any numeric disagreement
between two or more considered entries MUST resolve to `CONFLICT` with
every entry preserved, never dropped. 都市計畫道路寬度 and 現況道路寬度
MUST be kept as distinct semantic quantities, not treated as
interchangeable measurements of the same fact — a legitimate difference
between them is still surfaced (not silently reconciled), per this
module's own design.

## Golden Case Is a Regression Fixture, Not a Domain Rule

`data/golden/golden_case_input.py` and `data/demo_errors/build_demo_
submission.py` exist to prove the deterministic engines reproduce one
already-verified official example — MUST NOT generalize any specific
number, zone name, segment code, or plan_id from that fixture (or from
`FixtureExtractionProvider`/Mock Provider fixed values, or `data/demo_
errors/`'s deliberately-tampered values) into a rule applied to other
cases. When adding a new check, ground its threshold/logic in an official
source (`data/rules/`, `data/sources/source_manifest.json`) or existing
code/tests — never in "this is what the Golden Case happens to show".

## Maturity-Status Discipline

MUST NOT describe `TextractExtractionProvider` (or any AWS-calling code
this repo has not actually executed against a live AWS account) as
verified/working — state its actual, current status
(CODE_READY/LOCAL_RUNTIME_VERIFIED/AWS_RUNTIME_VERIFIED/MOCK_ONLY/
BACKLOG) explicitly rather than implying a stronger one. See the
`provider-contract` skill for the full status vocabulary and its
provider-layer contracts.
