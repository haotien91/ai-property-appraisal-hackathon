---
name: provider-contract
description: This skill should be used when modifying or adding a Data Provider or Document Extraction Provider (files under providers/, backend/handlers/collect_data.py, backend/handlers/case_reconstruction.py, DatasetRegistry usage, DATA_PROVIDER_MODE handling), or when asked "should this fall back to Mock", "how should a real provider report missing data", "is this source official or supporting", "how do I add a new evidence_sources entry", or "is this provider AWS/runtime verified" in this land-appraisal-review repo. Covers the Adapter Pattern contract every Provider (land use, road width, document extraction, OSM-backed facility providers) must follow.
version: 0.1.0
---

# Data Provider Contract

Every Provider in this repo (`providers/base.py`'s `DataProvider`,
`providers/document_extraction_provider.py`'s `DocumentExtractionProvider`)
follows one Adapter Pattern contract: same interface, interchangeable Mock
vs Real implementations, callers never branch on which is active. The
rules below are what makes a new provider consistent with the existing
ones — each is enforced by current code and locked in by passing tests.

## Real Path Must Never Silently Fall Back to Mock

A `Real*Provider` MUST NOT return Mock/Golden-Case fixed values when it
cannot find real data — it MUST return `value=None`,
`confidence="UNKNOWN"`, and (for the richer Evidence models)
`requires_manual_review=True`, with `notes` explaining why. This applies
even when a Mock provider happens to sit right next to it in the same
module (`RealLandUseProvider`, `RealRoadProvider`) — proximity to a Mock
implementation is not license to borrow its numbers. `DataProvider.fetch()`
MUST NOT raise for "no data found"; a raised exception and a silently
guessed value are both wrong — the correct response is an honest UNKNOWN
data point the caller can act on.

## DATA_PROVIDER_MODE: Accepted Values and Fail-Fast

Only `"mock"` and `"real"` are valid `DATA_PROVIDER_MODE` values in this
repo — there is no `"offline"` or `"hybrid"` mode (`real` mode itself
already mixes live API calls, local-snapshot reads, and honest UNKNOWN
per field, per field's own data-source reality; that is not a separate
mode dimension). Unset MUST resolve to `"mock"` as an explicit
development default. Any other value (a typo, `"offline"`, `"hybrid"`, an
empty string) MUST raise immediately at module-load time
(`INVALID_DATA_PROVIDER_MODE`) — never silently coerced to `"mock"`. See
`backend/handlers/collect_data.py`'s `_resolve_data_provider_mode()`.

## Provenance Fields Are Not Optional

Every `NormalizedDataPoint`/Evidence-shaped model MUST retain
`source`/`source_type`/`confidence`/`retrieved_at`/`notes` (or the richer
per-domain equivalents: `RoadWidthEvidence`'s `evidence_type`/
`dataset_id`/`dataset_version`/`legal_status`/`derivation_method`/
`requires_manual_review`, `ExtractedField`'s `extraction_method`/
`bounding_box`/`page`). A field this codebase has no first-class slot for
(e.g. `source_url`/`dataset_version` on `NormalizedDataPoint`) MUST be
composed into the existing `source`/`notes` strings, per the established
convention (see `RealLandUseProvider._land_use_zone_point()`) — MUST NOT
be silently dropped, and a brand-new provider MUST NOT invent a parallel
provenance schema when an existing one already fits.

## DatasetRegistry Traceability Contract

`DatasetRegistry.get_current_snapshot(dataset_id)` is keyed by
`dataset_id` (its primary key), NOT by `dataset_version` — a model or
Evidence that carries `dataset_version` but not `dataset_id` cannot
actually be traced back to a registry row (`checksum`/`local_path`/
`source_last_modified`). Any new Evidence-shaped model wrapping a
DatasetRegistry-backed lookup MUST carry both. `DatasetRegistry` keeps
only the CURRENT row per `dataset_id` (upsert, no version history) — an
older Evidence entry's `dataset_version` may no longer match the current
row after a re-sync; this is a real, honest traceability gap (do not
paper over it by assuming the current row always answers for historical
Evidence) and MUST NOT be silently treated as still-resolvable without
re-verifying the version actually matches.

## Submitted Data Is Never Overwritten by External Evidence

A Provider's resolved/reference value MUST NOT be written into a
`SubmittedFormData.submitted_*` field, and a submitted value MUST NOT be
promoted into an official Evidence entry just because it happens to match
what a Provider independently resolved (or because OCR happened to read
the same number off a document). These are two separately-sourced facts
compared against each other, not one fact duplicated — see
`case_reconstruction.py`'s `extract_submitted_land_use_ratio_fields()`
(reads `case.base_parcel_factors`, the submitted side) versus
`_official_regional_raw_value()` (reads independently-sourced
`base_regional_factors`), and the equivalent RoadWidth split in
`review.py`.

## Public/Crowdsourced Sources Are Supporting, Not Official

OSM (Overpass/Nominatim), OSRM routing, and any similarly public/
crowdsourced API MUST be labeled with `confidence` at most `"中"` (never
`"高"`) and `source_type` reflecting their non-official nature — MUST NOT
be presented as equivalent to a government open-data snapshot or a legal
source. A Real facility provider backed by such a source still MUST NOT
guess a subjective or boundary-dependent field (e.g. "is this within the
segment", density/traffic-volume style judgment calls) just because a
nearby-by-distance answer is available — leave it
`MANUAL_REVIEW_REQUIRED` when no Source defines the derivation formula.

## Plan-Specific Source Uncertainty Must Not Be Silently Resolved

`plan_id` is always human-supplied, never inferred from coordinates (the
relevant NTPC plan-boundary shapefile's name attribute is corrupted at
the source — verified byte-for-byte, not a wrong encoding guess). A
zone's resolution registered under one `plan_id` MUST NOT be returned for
a different or absent `plan_id`, even when the zone name string matches
(`LandUseRatioEngine`'s Layer 2 tests lock this in). A per-plan source
document that is itself a review/draft document rather than the final
gazetted version MUST keep `requires_manual_review=True` on its resulting
rule entry — do not upgrade its confidence just because its numbers
happen to match a Golden Case value.

## CODE_READY vs RUNTIME_VERIFIED — Do Not Conflate

Distinguish these statuses precisely when describing or building a
provider; do not blur them into "it works":

| Status | Meaning |
|---|---|
| `MOCK_ONLY` | Fixed Golden-Case values, no real query logic |
| `TEST_FIXTURE_ONLY` | Deterministic canned output built for a specific test, not a real source |
| `CODE_READY` | Real logic/adapter shape exists and runs, but the live external system it targets has not been reached from this environment |
| `LOCAL_RUNTIME_VERIFIED` | Actually executed successfully against real local data (e.g. a real archived PDF, a real local snapshot file) |
| `AWS_RUNTIME_VERIFIED` | Actually executed successfully against a live AWS service — nothing in this repo currently holds this status |
| `BACKLOG` | Not implemented; tracked, not silently assumed done |

`TextractExtractionProvider` is `CODE_READY` (its response-parsing logic
is independently unit-tested against a synthetic response) but MUST NOT
be described as `AWS_RUNTIME_VERIFIED` — it has never been executed
against real AWS Textract in this environment, and its `classify()`/
`extract_fields()` methods raise rather than pretend to work.
`LocalExtractionProvider` (PyMuPDF text-layer extraction) IS
`LOCAL_RUNTIME_VERIFIED` — it has actually been run against this repo's
own archived PDF and its output checked against known values. IaC
(`infra/template.yaml`) being lint-clean is `CODE_READY`, not "deployed" —
MUST NOT describe it as running/verified without an actual `sam deploy`
having occurred.
