# -*- coding: utf-8 -*-
"""
RuleTableValidator — deterministic sanity checks for a candidate rule table
(a list of records shaped like rule_schema.json / data/rules/*.json), BEFORE
it is ever handed to RuleEngine.

Purpose: on competition day the team will receive a brand-new 評價基準明細表
for a segment/land-use category never seen before (Phase 1 REQ-006/007 —
Golden Case cannot be hardcoded). Whoever transcribes that table — a human
under time pressure, or an AI draft that a human then reviews — needs fast,
specific, all-at-once feedback ("factor X's matrix isn't square", "factor Y's
max_adjustment doesn't match its own matrix") rather than RuleEngine failing
opaquely three steps downstream during form completion. This module never
raises for a bad rule table -- it always returns the full list of problems
found, so a person can fix everything in one pass instead of a slow
fix-one-rerun loop.

This intentionally does NOT depend on the `jsonschema` package (not an
existing project dependency; schemas/rule_schema.json is used elsewhere in
this codebase only as documentation, mirrored by hand into Pydantic models
-- see domain/models.py). The checks below hand-implement the subset of
schemas/rule_schema.json's constraints that matter for correctness, plus
business-rule checks the JSON Schema itself cannot express (matrix squareness,
anti-symmetry, max_adjustment consistency, bound contiguity across grades).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


REQUIRED_FIELDS = [
    "rule_id", "version", "city", "district", "land_use_type", "category",
    "factor", "value_type", "unit", "direction", "lower_bound", "upper_bound",
    "lower_inclusive", "upper_inclusive", "grade", "grade_code",
    "adjustment_matrix", "max_adjustment", "effective_date",
    "source_document", "source_page", "source_note",
]

VALID_VALUE_TYPES = {
    "numeric_range", "boolean", "categorical", "distance_positive", "distance_negative",
}

# 作業手冊 p.48-49／p.52-53 (「（七）／（八）...注意事項」第2點／1點)
# officially documents FIVE grading systems (2/3/5/7/9-level), and for the
# 5-level system specifically THREE separate label vocabularies depending
# on the factor's nature (general quality / presence-severity / strictness).
# A rule table using ANY of these is equally valid -- competition day is
# confirmed to use a different land-use category/segment than Golden Case
# (Phase 1 REQ-006/007), which may well use a different grading system than
# Golden Case's 5-level 優/稍優/普通/稍劣/劣. Treating only that one
# vocabulary as "the" valid set would make this validator reject correct
# official data -- exactly the kind of false ERROR this module exists to
# avoid causing under competition-day time pressure.
KNOWN_GRADE_VOCABULARIES = [
    {"優", "劣"},                                            # 2級
    {"優", "普通", "劣"},                                      # 3級
    {"優", "稍優", "普通", "稍劣", "劣"},                         # 5級（一般優劣）
    {"無", "輕微", "中度", "嚴重", "極嚴重"},                      # 5級（有無/嚴重程度）
    {"極輕微", "輕微", "普通", "嚴格", "極嚴格"},                   # 5級（嚴格程度）
    {"極優", "優", "稍優", "普通", "稍劣", "劣", "極劣"},           # 7級
    {"超極優", "極優", "優", "稍優", "普通", "稍劣", "劣", "極劣", "超極劣"},  # 9級
]
VALID_GRADES = set().union(*KNOWN_GRADE_VOCABULARIES)
# 9級以上由直轄市或縣(市)地政機關自行決定等級細項 (同一頁條文) -- the label
# text for a 10+-level system is explicitly NOT nationally fixed, so a
# grade outside every known vocabulary above is a WARNING (please double-
# check against this table's own source PDF), never an ERROR that blocks
# an otherwise-valid, officially-permitted local naming scheme.

# Bound-contiguity checking only makes sense for value types that carry a
# numeric band; boolean/categorical factors legitimately have null bounds.
BOUND_CHECKED_VALUE_TYPES = {"numeric_range", "distance_positive", "distance_negative"}


@dataclass
class ValidationIssue:
    severity: str  # "ERROR" | "WARNING"
    factor_key: str  # (city, district, land_use_type, factor) joined for readability
    rule_id: Optional[str]
    message: str

    def __str__(self) -> str:
        rid = f"[{self.rule_id}] " if self.rule_id else ""
        return f"{self.severity} {rid}({self.factor_key}) {self.message}"


class RuleTableValidator:
    """Stateless; `validate()` is a pure function of its input list."""

    def validate(self, rules: List[Dict[str, Any]]) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        issues.extend(self._check_required_fields(rules))
        issues.extend(self._check_duplicate_rule_ids(rules))
        issues.extend(self._check_enums(rules))

        groups = self._group_by_factor(rules)
        for factor_key, group in groups.items():
            issues.extend(self._check_grade_code_uniqueness(factor_key, group))
            issues.extend(self._check_matrix_consistency_within_factor(factor_key, group))
            issues.extend(self._check_matrix_square(factor_key, group))
            issues.extend(self._check_max_adjustment(factor_key, group))
            issues.extend(self._check_diagonal_zero(factor_key, group))
            issues.extend(self._check_antisymmetry(factor_key, group))
            issues.extend(self._check_bound_contiguity(factor_key, group))
            issues.extend(self._check_range_segments(factor_key, group))
            issues.extend(self._check_grade_vocabulary_consistency(factor_key, group))

        return issues

    @staticmethod
    def has_errors(issues: List[ValidationIssue]) -> bool:
        return any(i.severity == "ERROR" for i in issues)

    # -- individual-record checks -------------------------------------------------

    def _check_required_fields(self, rules: List[Dict[str, Any]]) -> List[ValidationIssue]:
        issues = []
        for r in rules:
            missing = [f for f in REQUIRED_FIELDS if f not in r]
            if missing:
                issues.append(ValidationIssue(
                    "ERROR", r.get("factor", "?"), r.get("rule_id"),
                    f"缺少必要欄位：{missing}",
                ))
        return issues

    def _check_duplicate_rule_ids(self, rules: List[Dict[str, Any]]) -> List[ValidationIssue]:
        seen: Dict[str, int] = defaultdict(int)
        for r in rules:
            rid = r.get("rule_id")
            if rid:
                seen[rid] += 1
        return [
            ValidationIssue("ERROR", "?", rid, f"rule_id 重複出現 {count} 次")
            for rid, count in seen.items() if count > 1
        ]

    def _check_enums(self, rules: List[Dict[str, Any]]) -> List[ValidationIssue]:
        issues = []
        for r in rules:
            vt = r.get("value_type")
            if vt not in VALID_VALUE_TYPES:
                issues.append(ValidationIssue(
                    "ERROR", r.get("factor", "?"), r.get("rule_id"),
                    f"value_type={vt!r} 不在允許值 {sorted(VALID_VALUE_TYPES)} 內",
                ))
            grade = r.get("grade")
            if grade not in VALID_GRADES:
                # WARNING, not ERROR: 作業手冊明文「9級以上由直轄市或縣(市)
                # 地政機關自行決定等級細項」，該分級文字本來就不是全國統一
                # 的固定清單，本系統不應把合法的地方自訂分級當成錯誤擋下來。
                issues.append(ValidationIssue(
                    "WARNING", r.get("factor", "?"), r.get("rule_id"),
                    f"grade={grade!r} 不在7組已知官方分級用語內，"
                    "可能是9級以上由地方政府自行決定之等級細項（合法），"
                    "也可能是筆誤，請對照該表原始PDF確認",
                ))
        return issues

    # -- per-factor-group checks ---------------------------------------------------

    def _group_by_factor(
        self, rules: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in rules:
            key = f"{r.get('city')}/{r.get('district')}/{r.get('land_use_type')}/{r.get('factor')}"
            groups[key].append(r)
        return groups

    def _check_grade_code_uniqueness(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        codes = [r.get("grade_code") for r in group]
        if len(codes) != len(set(codes)):
            return [ValidationIssue(
                "ERROR", factor_key, None,
                f"同一因素內 grade_code 重複：{codes}",
            )]
        return []

    def _check_matrix_consistency_within_factor(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        """Every grade-row of the same factor is expected to carry an
        IDENTICAL copy of that factor's full adjustment_matrix (this is the
        convention used throughout data/rules/*.json). If two rows of the
        same factor disagree, that is very likely a transcription error."""
        matrices = [r.get("adjustment_matrix") for r in group]
        first = matrices[0]
        for r, m in zip(group, matrices):
            if m != first:
                return [ValidationIssue(
                    "ERROR", factor_key, r.get("rule_id"),
                    "此因素底下各等級列所攜帶的 adjustment_matrix 不一致"
                    "（同一因素的每一列應攜帶完全相同的完整矩陣）",
                )]
        return []

    def _check_matrix_square(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        band_codes = {str(r.get("grade_code")) for r in group}
        matrix = group[0].get("adjustment_matrix") or {}
        row_keys = set(matrix.keys())
        col_keys = {k for row in matrix.values() for k in row.keys()}
        issues = []
        if row_keys != band_codes:
            issues.append(ValidationIssue(
                "ERROR", factor_key, None,
                f"矩陣的列（row）鍵值 {sorted(row_keys)} 與此因素實際擁有的等級 "
                f"{sorted(band_codes)} 不一致",
            ))
        if col_keys != band_codes:
            issues.append(ValidationIssue(
                "ERROR", factor_key, None,
                f"矩陣的欄（col）鍵值 {sorted(col_keys)} 與此因素實際擁有的等級 "
                f"{sorted(band_codes)} 不一致",
            ))
        return issues

    def _check_max_adjustment(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        matrix = group[0].get("adjustment_matrix") or {}
        actual_max = max(
            (abs(v) for row in matrix.values() for v in row.values()), default=0.0
        )
        declared = group[0].get("max_adjustment")
        if declared is None or abs(float(declared) - actual_max) > 1e-9:
            return [ValidationIssue(
                "ERROR", factor_key, None,
                f"max_adjustment 宣告值 {declared} 與矩陣實際最大絕對值 {actual_max} 不符",
            )]
        return []

    def _check_diagonal_zero(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        matrix = group[0].get("adjustment_matrix") or {}
        issues = []
        for code, row in matrix.items():
            v = row.get(code)
            if v is not None and v != 0:
                issues.append(ValidationIssue(
                    "WARNING", factor_key, None,
                    f"同等級比對 matrix[{code}][{code}]={v}，一般預期同等級間修正率應為0，請確認是否為刻意設計",
                ))
        return issues

    def _check_antisymmetry(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        matrix = group[0].get("adjustment_matrix") or {}
        issues = []
        checked = set()
        for a, row in matrix.items():
            for b, v_ab in row.items():
                if a == b or (b, a) in checked:
                    continue
                checked.add((a, b))
                v_ba = matrix.get(b, {}).get(a)
                if v_ba is not None and v_ab != -v_ba:
                    issues.append(ValidationIssue(
                        "WARNING", factor_key, None,
                        f"matrix[{a}][{b}]={v_ab} 與 matrix[{b}][{a}]={v_ba} 非互為正負號"
                        "（比較法慣例上，比準地優於比較標的與反向比較時的修正率通常正負相反，請確認是否為刻意設計）",
                    ))
        return issues

    @staticmethod
    def _record_segments(record: Dict[str, Any]):
        """One record's own list of (lower_bound, upper_bound) numeric
        bands -- either its `range_segments` (SHULIN-COMPETITION-RULE-
        PACK-A2 Task 5/6/7: a grade genuinely covering 2+ DISJOINT ranges,
        e.g. land_depth's 普通) when present and non-empty, or its own
        single top-level (lower_bound, upper_bound) otherwise -- so every
        other check in this class keeps working against "the record's
        bounds" without needing to know which shape it used."""
        segments = record.get("range_segments")
        if segments:
            return [(s.get("lower_bound"), s.get("upper_bound")) for s in segments]
        return [(record.get("lower_bound"), record.get("upper_bound"))]

    def _check_bound_contiguity(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        """Generalized (Task 5/6) to sort by actual VALUE order rather than
        grade_code order: for every EXISTING monotonic factor grade_code
        order already coincides with value order, so this is byte-for-byte
        the same check for them. For a non-monotonic factor (land_depth),
        grade_code order does NOT match value order -- sorting by value
        instead is what lets a genuinely non-monotonic grade sequence
        (劣→普通→優→稍優→普通→稍劣→劣, by depth) still validate as
        contiguous with no gaps."""
        checkable = [
            r for r in group
            if r.get("value_type") in BOUND_CHECKED_VALUE_TYPES and not r.get("anomaly_flag")
        ]
        if len(checkable) < 2:
            return []
        flat = []  # (lower, upper, grade_code)
        for r in checkable:
            for lb, ub in self._record_segments(r):
                if lb is None and ub is None:
                    continue  # unbounded sentinel band (e.g. "區段內有") -- not part of a numeric sequence
                flat.append((lb, ub, r.get("grade_code")))
        if len(flat) < 2:
            return []
        neg_inf = float("-inf")
        ordered = sorted(flat, key=lambda t: (t[0] if t[0] is not None else neg_inf))
        issues = []
        for prev, cur in zip(ordered, ordered[1:]):
            prev_upper = prev[1]
            cur_lower = cur[0]
            if prev_upper is None or cur_lower is None:
                continue
            if prev_upper != cur_lower:
                issues.append(ValidationIssue(
                    "WARNING", factor_key, None,
                    f"數值級距銜接處不連續：等級{prev[2]}的區段上界={prev_upper}，"
                    f"數值上緊接的下一區段（等級{cur[2]}）下界={cur_lower}"
                    "（可能有遺漏或重疊的數值區間，亦可能為刻意設計，請確認）",
                ))
        return issues

    @staticmethod
    def _segments_overlap(a, b) -> bool:
        """True only for a GENUINE overlap (shared interior), never for two
        segments merely touching at one point (e.g. "...未滿14m" followed
        by "14m以上...") -- that touching case is the normal, expected
        contiguous boundary _check_bound_contiguity already validates on
        its own terms; this check exists to catch a DIFFERENT mistake
        (two segments whose bodies genuinely overlap, so one value could
        match either)."""
        a_lb, a_ub = a[0], a[1]
        b_lb, b_ub = b[0], b[1]
        lo = max(a_lb if a_lb is not None else float("-inf"), b_lb if b_lb is not None else float("-inf"))
        hi = min(a_ub if a_ub is not None else float("inf"), b_ub if b_ub is not None else float("inf"))
        return lo < hi

    def _check_range_segments(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        """Task 8: validates range_segments (and, for consistency, every
        ordinary single-range record in the same factor group) for:
        unreachable ranges (lower_bound >= upper_bound), exact-duplicate
        segments, and genuine pairwise overlap. Grade-code-set consistency
        is unaffected -- _check_matrix_square already handles that purely
        from grade_code, independent of how many segments a grade uses."""
        issues: List[ValidationIssue] = []
        all_segments = []  # (lower, upper, rule_id)

        for r in group:
            segments = r.get("range_segments")
            if not segments:
                continue
            for seg in segments:
                lb, ub = seg.get("lower_bound"), seg.get("upper_bound")
                if lb is not None and ub is not None and lb >= ub:
                    issues.append(ValidationIssue(
                        "ERROR", factor_key, r.get("rule_id"),
                        f"range_segments 內一個區段的 lower_bound={lb} >= upper_bound={ub}，"
                        "此區段永遠無法被任何數值命中（unreachable range）",
                    ))
                all_segments.append((lb, ub, r.get("rule_id")))

        for r in group:
            if r.get("range_segments"):
                continue  # already folded in above
            lb, ub = r.get("lower_bound"), r.get("upper_bound")
            if lb is None and ub is None:
                continue
            if lb is not None and ub is not None and lb >= ub:
                issues.append(ValidationIssue(
                    "ERROR", factor_key, r.get("rule_id"),
                    f"lower_bound={lb} >= upper_bound={ub}，此區段永遠無法被任何數值命中",
                ))
            all_segments.append((lb, ub, r.get("rule_id")))

        seen: Dict[Any, List[Optional[str]]] = {}
        for lb, ub, rule_id in all_segments:
            seen.setdefault((lb, ub), []).append(rule_id)
        for (lb, ub), rule_ids in seen.items():
            if len(rule_ids) > 1:
                issues.append(ValidationIssue(
                    "ERROR", factor_key, None,
                    f"完全相同的數值區段 (lower_bound={lb}, upper_bound={ub}) 重複出現於多筆規則：{rule_ids}",
                ))

        for i in range(len(all_segments)):
            for j in range(i + 1, len(all_segments)):
                if self._segments_overlap(all_segments[i], all_segments[j]):
                    a, b = all_segments[i], all_segments[j]
                    issues.append(ValidationIssue(
                        "ERROR", factor_key, None,
                        f"數值區段重疊：{a[2]}(lower={a[0]},upper={a[1]}) 與 "
                        f"{b[2]}(lower={b[0]},upper={b[1]}) 有重疊區間，"
                        "同一數值不應可能同時符合兩個不同規則",
                    ))
        return issues

    def _check_grade_vocabulary_consistency(
        self, factor_key: str, group: List[Dict[str, Any]]
    ) -> List[ValidationIssue]:
        """作業手冊同一個5級制底下其實有3種不同的用語版本（優劣／有無嚴重
        程度／嚴格程度），加上2/3/7/9級。同一個因素底下的各等級列，理論上
        應該全部來自同一組用語（例如不該一列用「優」、另一列用「嚴重」）。
        僅在兩列的用語各自都命中『不同』的已知官方詞彙組時才提出警告——
        若其中一列用語不在任何已知組內（可能是9級以上地方自訂），不在此
        重複告警（已由_check_enums處理過一次）。"""
        vocab_hits = set()
        for r in group:
            grade = r.get("grade")
            for i, vocab in enumerate(KNOWN_GRADE_VOCABULARIES):
                if grade in vocab:
                    vocab_hits.add(i)
        if len(vocab_hits) > 1:
            grades_seen = sorted({r.get("grade") for r in group})
            return [ValidationIssue(
                "WARNING", factor_key, None,
                f"此因素底下的等級文字 {grades_seen} 橫跨了一組以上的官方分級用語"
                "（例如混用「優/稍優/普通/稍劣/劣」與「無/輕微/中度/嚴重/極嚴重」），"
                "請確認是否為刻意設計或抄錄時混用了不同用語組",
            )]
        return []
