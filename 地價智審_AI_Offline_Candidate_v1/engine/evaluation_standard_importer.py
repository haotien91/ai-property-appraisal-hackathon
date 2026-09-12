# -*- coding: utf-8 -*-
"""
evaluation_standard_importer.py — deterministic-first extraction of
「評價基準明細表」(evaluation-standard tables, e.g. 評價基準明細表範例.pdf)
into Rule Candidates that can be handed to CaseRuleRepository.save_candidate()
(docs/audit/EVALUATION_STANDARD_IMPORTER_PHASE3A_REPORT.md).

Scope discipline (per that report's explicit boundaries):
- This module does semantic EXTRACTION only: PDF text/geometry -> factor
  name candidate, grade bands, explicit adjustment matrix, provenance.
- It NEVER decides a grade, rank, adjustment rate, comparison price, or
  legal conclusion, and it NEVER auto-CONFIRMs a candidate. Every
  RuleCandidate/CaseRulePackage this module produces has status in
  {EXTRACTED, PARTIAL, AMBIGUOUS} -- CONFIRMED is reserved for
  CaseRuleRepository.confirm() after a human reviews the candidate.
- It reuses the EXISTING rule_schema.json shape (identical to what
  data/rules/regional_rules.json / individual_rules.json / engine/
  rule_table_ingest.py::build_rules_from_csv() already produce) and the
  EXISTING engine.rule_table_validator.RuleTableValidator for structural
  validation. No second rule format, no second GradeEngine/AdjustmentEngine.
- It NEVER reconstructs an adjustment_matrix via max_adjustment/(grade_count-1)
  -- when the PDF has an explicit matrix, that matrix is captured verbatim;
  when it can't be captured with confidence, the candidate is marked
  incomplete/ambiguous rather than synthesizing one.

PDF layout finding (see the Phase 3A survey for full detail): a naive
page.get_text() plain-text dump does NOT follow visual reading order for
this PDF (the content stream draws each factor's matrix numbers
interleaved with its own grade labels, but page-level note sentences and
vertical factor-name text are drawn out of visual order relative to the
matrix blocks). This module therefore parses matrix blocks from the
STREAM-ORDER token list (empirically verified deterministic and correct
for every block in the sample PDF -- see the survey), and separately uses
BBOX y-coordinates (page.get_text("dict")) to associate each block with
its note sentence by vertical position -- geometry-based table
reconstruction, not guessing.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import pymupdf
except ImportError:  # pragma: no cover - pymupdf is an existing project dependency
    import fitz as pymupdf  # type: ignore

from domain.models import CaseRulePackage, CaseRulePackageStatus

_GRADE_ROW_RE = re.compile(r"^(優|稍優|普通|稍劣|劣)[：:](.*)$")
_HEADER_LINE_1 = "比凖地"
_HEADER_LINE_2 = "(比較標的)"
_HEADER_LINE_3 = "宗地"
_TITLE_RE = re.compile(
    r"(?P<city>\S+?市)(?P<district>\S+?區)(?P<land_use_type>\S+?用地)"
    r"影響地價(?P<scope_zh>區域|個別)因素評價基準明細表"
)
_NOTE_RE = re.compile(r"以.{1,30}?(衡量|制定|計算|評估)")

_NUM = r"[\d,]+(?:\.\d+)?"
_UNIT = r"(m2|km|m|％|%)?"
_FULL_RANGE_RE = re.compile(rf"^({_NUM}){_UNIT}以上未滿({_NUM}){_UNIT}$")
_LOWER_ONLY_RE = re.compile(rf"^({_NUM}){_UNIT}以上$")
_UPPER_ONLY_RE = re.compile(rf"^未滿({_NUM}){_UNIT}$")

_UNIT_NORMALIZE = {"m": "M", "m2": "M2", "km": "KM", "%": "%", "％": "%", "": None, None: None}

_SENTINEL_BEST_LABEL = "區段內有"


def _is_num(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def _parse_num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class ExtractedGradeBand:
    grade: str
    grade_code: int
    condition_text: str
    value_type: str
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    lower_inclusive: Optional[bool] = None
    upper_inclusive: Optional[bool] = None
    unit: Optional[str] = None
    anomaly_flag: Optional[str] = None
    matrix_row: List[float] = field(default_factory=list)


@dataclass
class RuleCandidate:
    scope: str  # "regional" | "individual"
    city: str
    district: str
    land_use_type: str
    source_document: str
    source_page: str
    candidate_id: str = ""
    note_text_candidate: Optional[str] = None
    canonical_factor_id: Optional[str] = None
    bands: List[ExtractedGradeBand] = field(default_factory=list)
    max_adjustment_declared: Optional[float] = None
    status: str = "EXTRACTED"  # EXTRACTED | PARTIAL | AMBIGUOUS
    issues: List[str] = field(default_factory=list)

    @property
    def grade_count(self) -> int:
        return len(self.bands)

    def _matrix(self) -> Dict[str, Dict[str, float]]:
        matrix: Dict[str, Dict[str, float]] = {}
        for row_idx, band in enumerate(self.bands):
            row = {}
            for col_idx, other in enumerate(self.bands):
                if col_idx < len(band.matrix_row):
                    row[str(other.grade_code)] = band.matrix_row[col_idx]
            matrix[str(band.grade_code)] = row
        return matrix

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe serialization for persistence in CaseRulePackage.
        metadata['extraction_candidates'] (docs/audit/
        EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md §1) --
        carries the FULL band/matrix data, not just a summary, so a human
        reviewer's later factor-mapping edit (see backend/handlers/
        case_rule_repository.py::resolve_candidate_factor_mapping()) can
        regenerate real rule records WITHOUT re-running PDF extraction."""
        return {
            "candidate_id": self.candidate_id, "scope": self.scope,
            "city": self.city, "district": self.district, "land_use_type": self.land_use_type,
            "source_document": self.source_document, "source_page": self.source_page,
            "note_text_candidate": self.note_text_candidate,
            "canonical_factor_id": self.canonical_factor_id,
            "max_adjustment_declared": self.max_adjustment_declared,
            "status": self.status, "issues": list(self.issues),
            "bands": [
                {
                    "grade": b.grade, "grade_code": b.grade_code, "condition_text": b.condition_text,
                    "value_type": b.value_type, "lower_bound": b.lower_bound, "upper_bound": b.upper_bound,
                    "lower_inclusive": b.lower_inclusive, "upper_inclusive": b.upper_inclusive,
                    "unit": b.unit, "anomaly_flag": b.anomaly_flag, "matrix_row": list(b.matrix_row),
                }
                for b in self.bands
            ],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RuleCandidate":
        bands = [ExtractedGradeBand(**b) for b in d.get("bands", [])]
        return cls(
            scope=d["scope"], city=d["city"], district=d["district"], land_use_type=d["land_use_type"],
            source_document=d["source_document"], source_page=d["source_page"],
            candidate_id=d.get("candidate_id", ""), note_text_candidate=d.get("note_text_candidate"),
            canonical_factor_id=d.get("canonical_factor_id"), bands=bands,
            max_adjustment_declared=d.get("max_adjustment_declared"),
            status=d.get("status", "AMBIGUOUS"), issues=list(d.get("issues", [])),
        )


@dataclass
class ExtractionReport:
    source_document: str
    source_sha256: str
    page_count: int
    candidates: List[RuleCandidate] = field(default_factory=list)
    regional_table_detected: bool = False
    individual_table_detected: bool = False


# ---------------------------------------------------------------------
# Title / scope
# ---------------------------------------------------------------------

def _extract_title(page_text: str) -> Optional[Dict[str, str]]:
    m = _TITLE_RE.search(page_text)
    if not m:
        return None
    scope = "regional" if m.group("scope_zh") == "區域" else "individual"
    return {
        "city": m.group("city"), "district": m.group("district"),
        "land_use_type": m.group("land_use_type"), "scope": scope,
    }


# ---------------------------------------------------------------------
# Page line extraction: ONE canonical ordered (text, y0, x0) list, used
# for BOTH the stream-order matrix parser and y-position lookups. Verified
# (Phase 3A survey) that page.get_text("dict")'s line order is IDENTICAL,
# line-for-line, to page.get_text()'s plain-text line order for this PDF
# -- so a single dict-mode pass gives stream order AND geometry together,
# with no separate text-based re-matching (which would be ambiguous: many
# short tokens like "劣：" or "0" repeat across a page's multiple blocks).
# ---------------------------------------------------------------------

@dataclass
class _PageLine:
    text: str
    y0: float
    x0: float


def _extract_page_lines(page) -> List[_PageLine]:
    d = page.get_text("dict")
    out: List[_PageLine] = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if text:
                bbox = line["bbox"]
                out.append(_PageLine(text=text, y0=bbox[1], x0=bbox[0]))
    return out


# ---------------------------------------------------------------------
# Matrix block parsing (stream-order token state machine)
# ---------------------------------------------------------------------

def _find_block_starts(lines: List[str]) -> List[int]:
    starts = []
    for i in range(len(lines) - 3):
        if (lines[i] == _HEADER_LINE_1 and lines[i + 1] == _HEADER_LINE_2
                and lines[i + 2] == _HEADER_LINE_3 and lines[i + 3].strip().startswith("(比準地)")):
            starts.append(i + 4)
    return starts


def _parse_matrix_block(lines: List[str], start: int) -> Tuple[List[Tuple[str, str, List[float]]], Optional[float], int]:
    """Returns (rows, max_adjustment, end_index). rows = [(grade_word,
    condition_text, matrix_row_values)]. Never raises -- an incomplete
    block simply returns fewer rows than expected; the caller detects
    that via row-count vs. grade_count mismatch and flags MATRIX_INCOMPLETE
    rather than guessing the rest."""
    i = start
    rows: List[Tuple[str, str, List[float]]] = []
    max_adjustment: Optional[float] = None
    n: Optional[int] = None
    while True:
        nums: List[float] = []
        while i < len(lines) and _is_num(lines[i]):
            nums.append(_parse_num(lines[i]))
            i += 1
        if n is None:
            n = len(nums)
        if n and len(rows) == n - 1 and len(nums) == n + 1:
            # The lone extra leading token is the factor's max_adjustment
            # scalar -- observed positioned immediately before the LAST
            # row's own N values (see module docstring / Phase 3A survey).
            max_adjustment = nums[0]
            nums = nums[1:]
        if i >= len(lines):
            break
        m = _GRADE_ROW_RE.match(lines[i])
        if not m:
            break
        grade_word, desc = m.group(1), m.group(2).strip()
        i += 1
        if not desc and i < len(lines) and not _GRADE_ROW_RE.match(lines[i]) and not _is_num(lines[i]):
            desc = lines[i]
            i += 1
        rows.append((grade_word, desc, nums))
        if n and len(rows) == n:
            break
    if max_adjustment is None and i < len(lines) and _is_num(lines[i]):
        # 2-grade blocks place the lone scalar AFTER the last row instead
        # of before it (see survey) -- checked only once, harmlessly a
        # no-op if absent.
        max_adjustment = _parse_num(lines[i])
        i += 1
    return rows, max_adjustment, i


# ---------------------------------------------------------------------
# Note-sentence <-> block association (geometry / y-position, by INDEX
# into the same _PageLine list the matrix parser consumed -- never by
# re-matching text, which is ambiguous when short tokens like "劣："/"0"
# repeat across many blocks on one page).
# ---------------------------------------------------------------------

def _find_note_for_range(page_lines: List[_PageLine], y_range: Tuple[float, float],
                          margin: float = 5.0) -> Optional[str]:
    y0, y1 = y_range[0] - margin, y_range[1] + margin
    candidates = [pl.text for pl in page_lines if y0 <= pl.y0 <= y1 and _NOTE_RE.search(pl.text)]
    if len(candidates) == 1:
        return candidates[0]
    return None  # zero or ambiguous (>1) -- never guess which one


# ---------------------------------------------------------------------
# Grade-condition classification (deterministic; falls back to
# "categorical" -- never a wrong numeric_range guess)
# ---------------------------------------------------------------------

def _normalize_unit(raw: Optional[str]) -> Optional[str]:
    return _UNIT_NORMALIZE.get(raw, raw)


_OR_NONE_SUFFIX = "或無"


def _classify_condition(desc: str) -> Dict[str, Any]:
    if desc in ("有", "無"):
        return {"value_type": "boolean", "unit": None, "lower_bound": None, "upper_bound": None,
                "lower_inclusive": None, "upper_inclusive": None}
    if desc == _SENTINEL_BEST_LABEL:
        return {"value_type": "distance_positive", "unit": None, "lower_bound": None, "upper_bound": None,
                "lower_inclusive": None, "upper_inclusive": None}

    or_none_suffix = False
    core_desc = desc
    if desc.endswith(_OR_NONE_SUFFIX) and desc != _OR_NONE_SUFFIX:
        # "X或無" ("X, or none/not present") -- the existing static rule
        # files (data/rules/regional_rules.json) already treat this exact
        # pattern as a plain numeric threshold (verified: 主要道路寬度's
        # 劣 band is stored as lower_bound=None/upper_bound=10 despite its
        # grade_label being the full "未滿10m或無" text) -- the "或無"
        # qualifier is descriptive, not a second disjoint numeric
        # condition. Stripped ONLY for classification; condition_text
        # keeps the full original description verbatim.
        or_none_suffix = True
        core_desc = desc[: -len(_OR_NONE_SUFFIX)]

    if "或" in core_desc:
        # A genuinely compound, non-contiguous OR-condition (e.g. "未滿
        # 10m或100m以上" -- two disjoint numeric conditions, not
        # expressible as one [lower, upper) band) -- deliberately NOT
        # force-parsed into a clean numeric range; kept categorical
        # (exact-label match) so the compound semantics are never
        # silently dropped.
        return {"value_type": "categorical", "unit": None, "lower_bound": None, "upper_bound": None,
                "lower_inclusive": None, "upper_inclusive": None}

    m = _FULL_RANGE_RE.match(core_desc)
    if m:
        result = _classify_full_range(m, or_none_suffix)
        if result:
            return result
    m = _LOWER_ONLY_RE.match(core_desc)
    if m:
        return {"value_type": "numeric_range", "unit": _normalize_unit(m.group(2)),
                "lower_bound": _parse_num(m.group(1)), "upper_bound": None,
                "lower_inclusive": True, "upper_inclusive": None,
                "anomaly_flag": "OR_NONE_QUALIFIER_IGNORED" if or_none_suffix else None}
    m = _UPPER_ONLY_RE.match(core_desc)
    if m:
        return {"value_type": "numeric_range", "unit": _normalize_unit(m.group(2)),
                "lower_bound": None, "upper_bound": _parse_num(m.group(1)),
                "lower_inclusive": None, "upper_inclusive": False,
                "anomaly_flag": "OR_NONE_QUALIFIER_IGNORED" if or_none_suffix else None}
    return {"value_type": "categorical", "unit": None, "lower_bound": None, "upper_bound": None,
            "lower_inclusive": None, "upper_inclusive": None}


def _classify_full_range(m, or_none_suffix: bool) -> Dict[str, Any]:
    lower, lu, upper, uu = m.group(1), m.group(2), m.group(3), m.group(4)
    anomaly = None
    if lu and uu and lu != uu:
        anomaly = f"SUSPECTED_UNIT_TYPO({lu}_vs_{uu})"
    elif or_none_suffix:
        anomaly = "OR_NONE_QUALIFIER_IGNORED"
    return {"value_type": "numeric_range", "unit": _normalize_unit(uu or lu),
            "lower_bound": _parse_num(lower), "upper_bound": _parse_num(upper),
            "lower_inclusive": True, "upper_inclusive": False, "anomaly_flag": anomaly}


def _unify_value_types(bands: List["ExtractedGradeBand"]) -> None:
    """RuleEngine.grade() reads value_type/unit from ONLY the first matched
    candidate (candidates[0]["value_type"]), and every existing rule file
    already stores the SAME value_type/unit across all grade rows of one
    factor -- so a factor's bands must agree, never a per-row mix. Mutates
    bands in place; never invents new information, only reconciles what
    per-band classification already found:
      - all bands boolean -> keep boolean
      - all bands numeric_range -> keep numeric_range
      - one sentinel (distance_positive, unbounded) + the rest numeric_range
        -> the whole factor is distance_positive (matches the existing
        「區段內有」+ increasing-distance convention already used by
        data/rules/regional_rules.json for proximity factors)
      - anything else inconsistent -> the whole factor becomes categorical
        and every band's numeric bounds are cleared (categorical rows never
        carry bounds in the existing schema -- keeping them would silently
        imply a numeric comparison that value_type=="categorical" callers
        never perform, which is worse than having no bounds at all)."""
    types = {b.value_type for b in bands}
    if types <= {"boolean"} or types <= {"numeric_range"}:
        return
    if types == {"distance_positive", "numeric_range"}:
        for b in bands:
            b.value_type = "distance_positive"
        return
    for b in bands:
        b.value_type = "categorical"
        b.lower_bound = b.upper_bound = None
        b.lower_inclusive = b.upper_inclusive = None


def _infer_direction(bands: List["ExtractedGradeBand"]) -> Optional[str]:
    """'positive' = larger raw value is better (grade1/優 has the larger
    representative value than gradeN/劣); 'negative' = the reverse. This is
    the SAME purely-descriptive, non-authoritative label the existing
    rule_schema.json documents ("非Rule Selection邏輯本身依據") -- derived
    here only from the bands' own parsed numbers, never guessed when either
    end is a non-numeric sentinel/categorical band (returns None)."""
    if len(bands) < 2:
        return None
    first, last = bands[0], bands[-1]

    def _rep(b):
        return b.lower_bound if b.lower_bound is not None else b.upper_bound

    fv, lv = _rep(first), _rep(last)
    if fv is None or lv is None or fv == lv:
        return None
    return "positive" if fv > lv else "negative"


def _resolve_canonical_factor(note_text: Optional[str], candidate_names: List[str]) -> Optional[str]:
    """1. exact match only (against the union of already-known factor
    names from data/rules/regional_rules.json / individual_rules.json --
    the same principle engine/rule_table_ingest.py already relies on for
    factor identity). No fuzzy matching, no AI. Longest-substring-wins is
    the only deterministic normalization applied, and only when it breaks
    a tie unambiguously; a genuine tie (two equally-long candidate names
    both present) resolves to None rather than guessing."""
    if not note_text:
        return None
    matches = [name for name in candidate_names if name and name in note_text]
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    if len(matches) > 1 and len(matches[0]) == len(matches[1]):
        return None
    return matches[0]


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def extract_from_pdf(pdf_path: str, known_regional_factors: Optional[List[str]] = None,
                      known_individual_factors: Optional[List[str]] = None) -> ExtractionReport:
    """Deterministic-first extraction entry point. known_regional_factors/
    known_individual_factors are the canonical factor-name whitelists to
    match against (callers normally pass the `factor` values already
    present in data/rules/regional_rules.json / individual_rules.json --
    see build_case_rule_package_from_pdf() below for the convenience
    wrapper that loads these automatically)."""
    known_regional_factors = known_regional_factors or []
    known_individual_factors = known_individual_factors or []

    with open(pdf_path, "rb") as f:
        raw = f.read()
    source_sha256 = hashlib.sha256(raw).hexdigest()

    doc = pymupdf.open(pdf_path)
    report = ExtractionReport(source_document=pdf_path, source_sha256=source_sha256, page_count=doc.page_count)

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_text = page.get_text()
        title = _extract_title(page_text)
        if title is None:
            continue
        if title["scope"] == "regional":
            report.regional_table_detected = True
            known_names = known_regional_factors
        else:
            report.individual_table_detected = True
            known_names = known_individual_factors

        page_lines = _extract_page_lines(page)
        lines = [pl.text for pl in page_lines]
        block_starts = _find_block_starts(lines)

        for block_idx, start in enumerate(block_starts):
            rows, max_adjustment, end = _parse_matrix_block(lines, start)
            candidate = RuleCandidate(
                scope=title["scope"], city=title["city"], district=title["district"],
                land_use_type=title["land_use_type"], source_document=pdf_path,
                source_page=str(page_index + 1), max_adjustment_declared=max_adjustment,
                candidate_id=f"CAND-P{page_index + 1}-{block_idx:02d}",
            )

            header_start = start - 4  # include the "比凖地/(比較標的)/宗地/(比準地)" header lines
            end_idx = min(end, len(page_lines)) - 1
            y_range = (page_lines[header_start].y0, page_lines[end_idx].y0) if 0 <= end_idx < len(page_lines) else None
            note_text = _find_note_for_range(page_lines, y_range) if y_range else None
            candidate.note_text_candidate = note_text
            candidate.canonical_factor_id = _resolve_canonical_factor(note_text, known_names)

            expected_n = len(rows[0][2]) if rows else 0
            for row_idx, (grade_word, desc, matrix_row) in enumerate(rows):
                classification = _classify_condition(desc)
                band = ExtractedGradeBand(
                    grade=grade_word, grade_code=row_idx + 1, condition_text=desc,
                    matrix_row=matrix_row, anomaly_flag=classification.pop("anomaly_flag", None),
                    **classification,
                )
                candidate.bands.append(band)

            _unify_value_types(candidate.bands)
            _validate_candidate(candidate, expected_n, len(rows))
            report.candidates.append(candidate)

    return report


def _check_range_continuity(bands: List[ExtractedGradeBand]) -> List[str]:
    """Direction-aware adjacent-band boundary check (never assumes
    ascending order -- most factors in this PDF are DESCENDING: grade1/優
    has the largest numbers, gradeN/劣 the smallest, e.g. 主要道路寬度's
    30m以上 -> 未滿10m). Bands with no numeric bound at all (boolean /
    categorical / the distance sentinel) are excluded, never compared."""
    ranged = [b for b in bands if b.lower_bound is not None or b.upper_bound is not None]
    ordered = sorted(ranged, key=lambda b: b.grade_code)
    if len(ordered) < 2:
        return []

    def _rep(b):
        return b.lower_bound if b.lower_bound is not None else b.upper_bound

    fv, lv = _rep(ordered[0]), _rep(ordered[-1])
    if fv is None or lv is None or fv == lv:
        return []
    descending = fv > lv

    issues: List[str] = []
    for a, b in zip(ordered, ordered[1:]):
        if descending:
            edge_a, edge_b = a.lower_bound, b.upper_bound
        else:
            edge_a, edge_b = a.upper_bound, b.lower_bound
        if edge_a is None or edge_b is None:
            continue
        if descending:
            if edge_a > edge_b:
                issues.append("GRADE_RANGE_GAP")
            elif edge_a < edge_b:
                issues.append("GRADE_RANGE_OVERLAP")
        else:
            if edge_a < edge_b:
                issues.append("GRADE_RANGE_GAP")
            elif edge_a > edge_b:
                issues.append("GRADE_RANGE_OVERLAP")
    return issues


def _validate_candidate(candidate: RuleCandidate, expected_grade_count: int, actual_row_count: int) -> None:
    """Populates candidate.status/issues. Never raises -- every failure
    mode becomes an explicit issue code, per Phase 3A's Parsing Safety
    requirement (§7): RULE_EXTRACTION_INCOMPLETE / RULE_EXTRACTION_AMBIGUOUS
    / MATRIX_INCOMPLETE / GRADE_RANGE_OVERLAP / GRADE_RANGE_GAP /
    UNKNOWN_FACTOR."""
    issues: List[str] = []

    if actual_row_count == 0 or actual_row_count != expected_grade_count:
        issues.append("RULE_EXTRACTION_INCOMPLETE")

    for band in candidate.bands:
        if len(band.matrix_row) != len(candidate.bands):
            issues.append("MATRIX_INCOMPLETE")
            break

    if candidate.max_adjustment_declared is None:
        issues.append("MATRIX_INCOMPLETE")

    if candidate.bands and candidate.max_adjustment_declared is not None:
        actual_max = max((abs(v) for band in candidate.bands for v in band.matrix_row), default=0.0)
        if abs(candidate.max_adjustment_declared - actual_max) > 1e-9:
            issues.append("RULE_EXTRACTION_AMBIGUOUS")

    issues.extend(_check_range_continuity(candidate.bands))

    if candidate.canonical_factor_id is None:
        issues.append("UNKNOWN_FACTOR")

    candidate.issues = issues
    if "RULE_EXTRACTION_INCOMPLETE" in issues or "MATRIX_INCOMPLETE" in issues:
        candidate.status = "PARTIAL"
    elif "UNKNOWN_FACTOR" in issues or "RULE_EXTRACTION_AMBIGUOUS" in issues or "GRADE_RANGE_OVERLAP" in issues:
        candidate.status = "AMBIGUOUS"
    else:
        candidate.status = "EXTRACTED"


def reevaluate_candidate_with_canonical_factor(candidate: RuleCandidate, canonical_factor_id: str) -> RuleCandidate:
    """Human-supplied factor-mapping resolution (docs/audit/
    EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md §3, "factor
    mapping" edit): sets canonical_factor_id and re-runs the SAME
    deterministic validation extract_from_pdf() used, so UNKNOWN_FACTOR
    disappears but any OTHER genuine issue (matrix incomplete, range
    overlap, ...) is still surfaced rather than silently cleared. Mutates
    and returns the same candidate -- callers persist the result (see
    backend/handlers/case_rule_repository.py::resolve_candidate_factor_
    mapping())."""
    candidate.canonical_factor_id = canonical_factor_id
    expected_n = len(candidate.bands[0].matrix_row) if candidate.bands else 0
    _validate_candidate(candidate, expected_n, len(candidate.bands))
    return candidate


def candidate_to_rule_records(candidate: RuleCandidate, package_id: str) -> List[Dict[str, Any]]:
    """Converts one CLEAN candidate (status == EXTRACTED, canonical_factor_id
    resolved) into rule_schema.json-shaped dicts -- one per grade, exactly
    the shape data/rules/regional_rules.json['rules'] / engine/
    rule_table_ingest.py::build_rules_from_csv() already produce. Returns []
    for a non-EXTRACTED candidate -- callers must not call this on a
    PARTIAL/AMBIGUOUS candidate (see build_case_rule_package_from_pdf(),
    the only caller -- also called by backend/handlers/case_rule_
    repository.py::resolve_candidate_factor_mapping() after a human
    factor-mapping edit re-validates a candidate back to EXTRACTED).
    rule_id embeds candidate.candidate_id (when set) so a later re-
    resolution of the SAME candidate can be matched and replaced exactly,
    never guessed via factor-name/page heuristics."""
    if candidate.status != "EXTRACTED" or not candidate.canonical_factor_id:
        return []
    prefix = "REG" if candidate.scope == "regional" else "IND"
    factor_key = candidate.canonical_factor_id.upper().replace(" ", "_")
    matrix = candidate._matrix()
    direction = _infer_direction(candidate.bands)
    id_parts = [prefix, factor_key, package_id]
    if candidate.candidate_id:
        id_parts.append(candidate.candidate_id)
    records = []
    for band in candidate.bands:
        records.append({
            "rule_id": "-".join(id_parts) + f"-{band.grade_code:02d}",
            "version": "candidate-1.0",
            "city": candidate.city, "district": candidate.district, "land_use_type": candidate.land_use_type,
            "category": None,
            "factor": candidate.canonical_factor_id,
            "value_type": band.value_type, "unit": band.unit, "direction": direction,
            "lower_bound": band.lower_bound, "upper_bound": band.upper_bound,
            "lower_inclusive": band.lower_inclusive, "upper_inclusive": band.upper_inclusive,
            "grade": band.grade, "grade_code": band.grade_code, "grade_label": band.condition_text,
            "adjustment_matrix": matrix, "max_adjustment": candidate.max_adjustment_declared,
            "effective_date": None,
            "source_document": candidate.source_document, "source_page": candidate.source_page,
            "source_note": f"deterministic PDF extraction, note_text={candidate.note_text_candidate!r}",
            "anomaly_flag": band.anomaly_flag,
        })
    return records


def build_case_rule_package_from_pdf(
    pdf_path: str, case_id: str, package_id: str, rule_version: str,
    known_regional_factors: List[str], known_individual_factors: List[str],
    created_at,
) -> Tuple[CaseRulePackage, ExtractionReport]:
    """Convenience wrapper: extract, then assemble a CaseRulePackage whose
    status is DERIVED from extraction quality -- NEVER CONFIRMED (Phase 3A
    §4/§9): EXTRACTED only if every candidate on the page cleanly resolved
    (no issues at all); PARTIAL if at least one candidate resolved cleanly
    but others didn't; AMBIGUOUS if none did. Only EXTRACTED-status
    candidates are converted into regional_rules/individual_rules entries --
    every candidate (clean or not) is preserved verbatim in
    package.metadata['extraction_candidates'] for human review, so nothing
    is silently dropped."""
    report = extract_from_pdf(pdf_path, known_regional_factors, known_individual_factors)

    regional_rules: List[Dict[str, Any]] = []
    individual_rules: List[Dict[str, Any]] = []
    for candidate in report.candidates:
        records = candidate_to_rule_records(candidate, package_id)
        if candidate.scope == "regional":
            regional_rules.extend(records)
        else:
            individual_rules.extend(records)

    clean_count = sum(1 for c in report.candidates if c.status == "EXTRACTED")
    if not report.candidates or clean_count == 0:
        package_status = CaseRulePackageStatus.AMBIGUOUS
    elif clean_count == len(report.candidates):
        package_status = CaseRulePackageStatus.EXTRACTED
    else:
        package_status = CaseRulePackageStatus.PARTIAL

    package = CaseRulePackage(
        case_id=case_id, package_id=package_id, rule_version=rule_version,
        source_document=pdf_path, source_sha256=report.source_sha256,
        source_type="PDF_DETERMINISTIC_EXTRACTION",
        status=package_status,
        regional_rules=regional_rules, individual_rules=individual_rules,
        created_at=created_at,
        metadata={
            # Full per-candidate data (bands/matrix included, not just a
            # summary) -- see RuleCandidate.to_dict(): lets a later human
            # factor-mapping edit regenerate real rule records without
            # re-running PDF extraction (backend/handlers/case_rule_
            # repository.py::resolve_candidate_factor_mapping()).
            "extraction_candidates": [c.to_dict() for c in report.candidates],
        },
        warnings=[f"page={c.source_page} candidate_status={c.status} issues={c.issues}"
                  for c in report.candidates if c.status != "EXTRACTED"],
    )
    return package, report
