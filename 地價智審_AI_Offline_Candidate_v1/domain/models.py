# -*- coding: utf-8 -*-
"""
Domain models for the AI-Assisted Real Estate Valuation Case Review pipeline.

Design principles (per project constitution & Phase 1-3 findings):
- These models are pure data containers. No LLM calls, no business logic here.
- Every FieldCompletion carries a full traceability chain (raw_value ->
  normalized_value -> source -> rule_id -> grade -> adjustment -> formula ->
  calculation -> final_value) so a human reviewer or Smart Review engine can
  always answer "why is this value what it is".
- Numeric calculation fields use Decimal (not float) internally, per the
  precision-preservation finding independently re-verified in Phase 2/3
  (184,763 -> 188,458.26 -> 212,957.83 -> round -> 212,958 only reproduces
  the official Golden Case value when full precision is retained end-to-end).
- Config is data-driven (city/district/land_use_type are fields, not
  hardcoded), per Phase 1 REQ-006 ("system must not be built for a single
  segment/case").
"""
from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, ConfigDict, computed_field, model_validator


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    """Mirrors schemas/field_dictionary.json's source_type taxonomy (Phase 2),
    kept as a closed enum here so engines cannot invent new provenance labels."""
    OFFICIAL_FORM_FIELD = "官方表單既定欄位"
    AI_ASSISTED_FILL = "AI輔助填寫"
    RULE_ENGINE = "Rule Engine判定"
    ADJUSTMENT_ENGINE = "Adjustment Engine計算"
    CALCULATION_ENGINE = "Calculation Engine計算"
    AI_SUGGESTED_HUMAN_CONFIRMED = "AI輔助建議＋人工核定"
    CARRIED_OVER = "承接"
    MANUAL_SIGNOFF = "人工簽核"
    GIS_MEASUREMENT = "GIS量測"
    # COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 4: a value the competition's
    # own 題目.pdf already fixed for a segment (e.g. P001-00's 建蔽率=50%,
    # 主要道路寬度=28M) -- immutable once collected. No provider/AI/OSM/NLSC
    # value may ever overwrite a FactorInput carrying this source_type; a
    # differing external value is stored as separate REFERENCE_EVIDENCE
    # instead (see backend/handlers/collect_data.py's segment-scoped path).
    COMPETITION_PROVIDED_FIXED = "競賽題目提供固定值"


class FieldStatus(str, Enum):
    """Terminal status of a single field's completion attempt. Mirrors the
    project constitution's Data Acquisition Layer contract: never guess when
    data or a rule is missing."""
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    ERROR = "ERROR"


class PartyRole(str, Enum):
    BASE_PARCEL = "比準地"          # 比準地 (reference parcel)
    COMPARABLE = "比較標的"          # 比較標的 (comparable)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """Provenance record attached to any value flowing through the pipeline.
    Mirrors the Data Acquisition Layer contract in the master project
    instructions (value/unit/source/source_url/timestamp/confidence/notes)."""
    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="人類可讀之來源說明，例如 '查估書表範本.pdf 表1' 或 '使用者輸入'")
    source_type: SourceType
    source_document: Optional[str] = None
    source_page: Optional[str] = None
    confidence: Optional[str] = Field(None, description="高/中/低，或UNKNOWN；非數值強行量化之信心分數")
    retrieved_at: Optional[datetime] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Case-level input
# ---------------------------------------------------------------------------

class FactorInput(BaseModel):
    """A single raw factor value for either the base parcel or a comparable,
    before any Rule Engine processing. This is the atomic unit the Grade
    Engine consumes."""
    model_config = ConfigDict(extra="forbid")

    field_id: str = Field(..., description="穩定英文field_id，對應 schemas/field_dictionary.json")
    factor: str = Field(..., description="官方中文因素名稱，對應 data/rules/*.json 之 'factor' 欄位")
    raw_value: Union[float, int, str, bool, None] = None
    unit: Optional[str] = None
    evidence: Evidence


class CompetitionCase(BaseModel):
    """Top-level structured input for one appraisal case. Deliberately
    config-driven (city/district/land_use_type are data, not hardcoded) so
    the same pipeline works for any district/land-use combination the
    finals may present (Phase 1 REQ-006/REQ-007)."""
    model_config = ConfigDict(extra="forbid")

    case_no: str
    appraisal_period: str = Field(..., description="年期，ROC格式，例如 1140901")
    appraisal_base_date: str
    segment_code: str
    segment_scope: str
    city: str
    district: str
    land_use_type: str

    base_parcel_id: str = Field(..., description="比準地識別碼，例如宗地流水號或地號")
    base_parcel_factors: List[FactorInput] = Field(default_factory=list)

    comparable_ids: List[str] = Field(default_factory=list)
    comparable_factors: Dict[str, List[FactorInput]] = Field(
        default_factory=dict, description="key=comparable_id, value=該比較標的之因素清單"
    )
    comparable_land_normal_price: Dict[str, Decimal] = Field(default_factory=dict)
    comparable_transaction_date: Dict[str, str] = Field(default_factory=dict)
    comparable_price_date_adjustment_rate: Dict[str, Decimal] = Field(
        default_factory=dict,
        description="AI輔助建議＋人工核定之價格日期調整率(%)；此值本身不由Grade/Adjustment Engine產生，見rule_engine_spec.md已知限制",
    )
    comparable_weight: Dict[str, Decimal] = Field(
        default_factory=dict,
        description="AI輔助建議＋人工核定之比較標的權重(%)；僅1筆比較標的時官方明文=100（作業手冊p.52-53）",
    )


# ---------------------------------------------------------------------------
# Rule / Grade / Adjustment results
# ---------------------------------------------------------------------------

class RuleResult(BaseModel):
    """Direct mirror of a matched record from data/rules/*.json (schemas/rule_schema.json)."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    factor: str
    matched: bool
    grade: Optional[str] = None
    grade_code: Optional[int] = None
    grade_label: Optional[str] = None
    source_document: str
    source_page: str
    anomaly_flag: Optional[str] = Field(
        None, description="若非null，指向 docs/phase3/source_anomalies.md 對應編號"
    )


class GradeResult(BaseModel):
    """Grade determination for one factor, for one party (base parcel or a
    named comparable). Wraps engine.rule_engine.GradeResult with full
    traceability metadata."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    factor: str
    party_role: PartyRole
    party_id: str
    raw_value: Union[float, int, str, bool, None]
    normalized_value: Union[float, int, str, bool, None]
    rule_result: RuleResult
    evidence: Evidence


class AdjustmentResult(BaseModel):
    """Result of an Adjustment Matrix lookup between a base-parcel grade and
    a comparable grade for the same factor."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    factor: str
    comparable_id: str
    base_grade: GradeResult
    comparable_grade: GradeResult
    adjustment_pct: Decimal
    rule_id: str = Field(..., description="rule_id 之矩陣來源，取自 base_grade 所在的規則記錄")
    matrix_row_grade_code: int
    matrix_col_grade_code: int


# ---------------------------------------------------------------------------
# Calculation results
# ---------------------------------------------------------------------------

class RoundingRule(str, Enum):
    NONE_FULL_PRECISION = "NONE_FULL_PRECISION"   # intermediate value, never rounded
    TIERED_CEILING = "TIERED_CEILING"              # REQ-021 (land price fadeouts, per amount tier)
    CONVENTIONAL_ROUND = "CONVENTIONAL_ROUND"      # REQ-022 (final comparison price, round-half-up)


class CalculationStep(BaseModel):
    """A single, independently-inspectable step in the Calculation Engine's
    chain. Every step retains full (unrounded) Decimal precision in
    raw_result; rounded_result is populated only for steps where an official
    rounding rule applies (REQ-021/REQ-022), and is null otherwise -- this is
    the direct implementation of the Phase 2/3 finding that the official
    Golden Case answer (212,958) is only reproducible when intermediate
    values are NOT rounded before use."""
    model_config = ConfigDict(extra="forbid")

    step: str
    formula: str
    inputs: Dict[str, str] = Field(default_factory=dict, description="輸入變數名稱->字串化數值，供人工覆核")
    raw_result: Decimal
    rounding_rule: RoundingRule = RoundingRule.NONE_FULL_PRECISION
    rounded_result: Optional[Decimal] = None
    source_document: Optional[str] = None
    source_page: Optional[str] = None
    notes: Optional[str] = None


class CalculationResult(BaseModel):
    """Full calculation chain for one comparable's trial price, plus the
    final weighted base-parcel comparison price. Mirrors
    docs/phase2/calculation_dependency.md's 10-segment derivation."""
    model_config = ConfigDict(extra="forbid")

    comparable_id: str
    steps: List[CalculationStep]
    trial_price_raw: Decimal
    trial_price_rounded: Optional[Decimal] = None
    adjustment_abs_sum_pct: Decimal
    price_formation_similarity: Optional[str] = None
    weight_pct: Decimal


# ---------------------------------------------------------------------------
# Comparable selection (不動產估價技術規則 §21/§23/§25/§26/§27 — verified
# verbatim against 全國法規資料庫 law.moj.gov.tw/LawClass/LawAll.aspx?pcode=
# D0060077, 修正日期 民國102年12月20日, the parent regulation the competition's
# 土地徵收補償市價查估作業手冊 itself implements). See
# engine/comparable_selection_engine.py for the deterministic checks that
# populate these models.
# ---------------------------------------------------------------------------

class ExclusionDeterminationStatus(str, Enum):
    """Three-state §25 verdict, added because the boolean `excluded` alone
    lets a downstream reader conflate two very different situations that
    both produce excluded=False: "all applicable checks genuinely passed"
    vs. "the only concern is the 30% total-adjustment check, whose formula
    is LEGAL_BASIS_UNCONFIRMED -- this comparable's status per that clause
    cannot actually be determined". excluded=False must never be presented
    as "確認依法不需排除" when the real state is UNDETERMINED."""
    EXCLUDED = "EXCLUDED"            # over_15pct_items non-empty (§25's unambiguous 15% clause)
    NOT_EXCLUDED = "NOT_EXCLUDED"    # 15% clause clean AND both 30%-total candidate readings clean
    UNDETERMINED = "UNDETERMINED"    # 15% clause clean, but >=1 candidate 30%-total reading is over 30%


class ComparableExclusionCheck(BaseModel):
    """不動產估價技術規則 §25 full text: "...任一單獨項目之價格調整率大於百分
    之十五，或情況、價格日期、區域因素及個別因素調整總調整率大於百分之三十
    時...應排除該比較標的之適用。但..."

    Two very different confidence levels live in this one article:

    1. The 15% single-item threshold ("任一單獨項目...大於百分之十五") is
       unambiguous: one factor, its own |adjustment_pct|, compared to 15%.
       `excluded` is driven ONLY by this check.
    2. The 30% "總調整率" (total adjustment rate) of 情況+價格日期+區域因素+
       個別因素 combined has NO defined formula anywhere this codebase could
       find. §25's own text just names the four inputs, never how to combine
       them. The one place the 土地徵收補償市價查估作業手冊 DOES give an
       explicit "絕對值加總" formula (p.52-53 item「十」：「各項調整百分率
       先取絕對值後加總計算」) is for a DIFFERENT, separately-named field
       ("調整百分率絕對值加總") that item「十一」ties explicitly to §27's
       WEIGHT decision -- not to §25's exclusion threshold. Treating that
       formula as if it also answered §25's "總調整率" would be inventing a
       legal basis that was never confirmed to exist (verified against
       law.moj.gov.tw/LawClass/LawAll.aspx?pcode=D0060077 §25 full text and
       the manual's 表4/比較法調查估價表 section, p.50-53, during this
       session). So this model reports BOTH defensible candidate readings
       side by side, purely for human reference, and `excluded` never
       depends on either of them -- see `total_adjustment_legal_basis`.

    This model is a finding, not a silent auto-exclusion: nothing in this
    codebase removes a comparable from the pipeline on its own -- every
    verdict here surfaces to the estimator via Smart Review."""
    model_config = ConfigDict(extra="forbid")

    comparable_id: str
    exclusion_determination_status: ExclusionDeterminationStatus = Field(
        ..., description="優先讀取此欄位，而非excluded：EXCLUDED=15%單項門檻觸發（§25明文，"
        "唯一無歧義依據）；NOT_EXCLUDED=15%單項門檻未觸發，且30%總門檻兩種候選讀法皆未觸發"
        "（真正『已確認不需排除』）；UNDETERMINED=15%單項門檻未觸發，但30%總門檻至少一種"
        "候選讀法觸發——因該公式LEGAL_BASIS_UNCONFIRMED，無法斷定是否『依法不需排除』，"
        "需估價師人工判斷，不等同NOT_EXCLUDED"
    )
    excluded: bool = Field(
        ..., description="向後相容欄位，恆等於(exclusion_determination_status==EXCLUDED)。"
        "excluded=False涵蓋NOT_EXCLUDED與UNDETERMINED兩種狀態，不代表『已確認不需排除』——"
        "下游（前端/API/PDF）不應僅憑excluded=False即顯示或暗示『依法不需排除』，"
        "應改讀exclusion_determination_status"
    )
    over_15pct_items: List[str] = Field(
        default_factory=list, description="factor名稱，其單獨調整率絕對值 > 15%"
    )

    total_adjustment_signed_sum_pct: Decimal = Field(
        ..., description="候選讀法A：情況+價格日期+區域+個別因素「先加總、再取絕對值」"
        "（呼應作業手冊p.53「總調整率為各項...差異之加總」一詞於其他表格之慣用法，"
        "但該處未必即為§25本條之定義）"
    )
    total_adjustment_abs_component_sum_pct: Decimal = Field(
        ..., description="候選讀法B：情況+價格日期+區域+個別因素「各自先取絕對值、再加總」"
        "（沿用作業手冊p.52-53item「十」對『調整百分率絕對值加總』之定義，"
        "但該定義原文明確是給§27權重判斷用，非§25總調整率）"
    )
    total_over_30pct_signed_sum: bool
    total_over_30pct_abs_component_sum: bool
    total_adjustment_legal_basis: str = Field(
        default="LEGAL_BASIS_UNCONFIRMED",
        description="§25「總調整率」之精確計算公式，於不動產估价技術規則本文與"
        "土地徵收補償市價查估作業手冊中均查無明文定義，僅為系統列出之候選讀法，"
        "不構成官方確認公式，不得作為自動排除之依據",
    )

    exception_claimed: bool = False
    exception_note: Optional[str] = Field(
        None, description="§25但書：性質特殊或區位特殊缺乏市場交易資料之敘明理由，"
        "僅於exception_claimed=True時有意義；本欄位本身不構成系統自動核准exception，"
        "永遠需要估價師在報告書中真正敘明"
    )
    source_document: str = "不動產估價技術規則"
    source_article: str = "第25條"


class TrialPriceGapCheck(BaseModel):
    """不動產估價技術規則 §26: if the gap between the highest and lowest
    trial prices (after §25 exclusions) reaches 20% or more of their
    average, that determination must be reconsidered -- comparables whose
    trial price sits on the excluded side should be dropped. gap_pct
    formula per §26 second paragraph: (高-低) / ((高+低)/2)。"""
    model_config = ConfigDict(extra="forbid")

    max_price: Decimal
    min_price: Decimal
    max_comparable_id: str
    min_comparable_id: str
    gap_pct: Decimal
    triggered: bool  # gap_pct >= 20
    source_document: str = "不動產估價技術規則"
    source_article: str = "第26條"


class SuggestedWeight(BaseModel):
    """不動產估價技術規則 §27 requires weighing comparables by 'each
    comparable's data reliability' and 'similarity of price-formation
    factors to the appraisal target', but gives NO mathematical formula for
    converting those into a percentage -- verified directly against the
    official text (§27 full text has no equation). This is why every field
    here is explicitly labeled a SUGGESTION: `method` names the (non-legal)
    convention used, and `requires_human_confirmation` is always True --
    this value must never be silently treated as an official/legal
    computation the way Grade/Adjustment Matrix lookups are."""
    model_config = ConfigDict(extra="forbid")

    comparable_id: str
    suggested_weight_pct: Decimal
    method: str = "反比於調整率絕對值加總（系統建議，非法定公式——不動產估價技術規則§27未規定計算公式）"
    requires_human_confirmation: bool = True
    source_document: str = "系統建議（非不動產估價技術規則明文規定）"


# ---------------------------------------------------------------------------
# Field completion (the traceable unit requested by Phase 4 TRACEABILITY spec)
# ---------------------------------------------------------------------------

class FieldCompletion(BaseModel):
    """One completed (or explicitly not-completed) field on one official
    form. This is the atomic traceability unit: every value that ends up on
    a filled-in form must be representable as one of these, with a full
    chain back to its source."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    chinese_label: str
    form: str = Field(..., description="表1 / 表5-2 / 表4")

    raw_value: Union[float, int, str, bool, None] = None
    normalized_value: Union[float, int, str, bool, None] = None
    source: str
    rule_id: Optional[str] = None
    grade: Optional[str] = None
    adjustment: Optional[Decimal] = None
    formula: Optional[str] = None
    calculation: Optional[str] = Field(
        None, description="人類可讀之計算摘要字串，例如 '188458.26 * 1.13 = 212957.8338 -> round -> 212958'"
    )
    final_value: Union[float, int, str, Decimal, None] = None

    status: FieldStatus
    evidence: Optional[Evidence] = None
    anomaly_flag: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class FormCompletionResult(BaseModel):
    """Top-level output of the Form Completion Engine for one form, for one
    case."""
    model_config = ConfigDict(extra="forbid")

    case_no: str
    form: str
    fields: List[FieldCompletion]
    generated_at: datetime
    engine_versions: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completed_count(self) -> int:
        return sum(1 for f in self.fields if f.status == FieldStatus.COMPLETED)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def manual_review_count(self) -> int:
        return sum(1 for f in self.fields if f.status == FieldStatus.MANUAL_REVIEW_REQUIRED)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unknown_count(self) -> int:
        return sum(1 for f in self.fields if f.status == FieldStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# TABLE51-THREE-COMPARABLE-C1: 表5-1 影響地價區域因素分析明細表.
#
# Generic, comparable-count-aware domain model for the base<->comparable
# regional-factor comparison table -- explicitly preserves ONE independent
# lineage per comparable (P001->P002, P001->P003, P001->P004 as three
# SEPARATE Table51Comparison entries), never an averaged/merged comparable.
# Built by engine/table51_analysis_engine.py, which reuses GradeEngine/
# AdjustmentEngine/CalculationEngine unmodified -- no grading logic is
# duplicated here or in that engine module; this is purely the traceable
# output shape.
# ---------------------------------------------------------------------------

class Table51FactorResult(BaseModel):
    """One row of 表5-1's detail table (one of the 29 regional factors) for
    ONE base<->comparable lineage. `status`/`requires_manual_review`/
    `reason` together express EITHER a genuine grade+adjustment (status=
    COMPLETED) OR an honest failure to determine one (status=MANUAL_REVIEW_
    REQUIRED) -- never a fabricated grade, never a value silently defaulted
    to 0 for a missing factor.

    TABLE51-THREE-COMPARABLE-C1-FINAL-GATE-1 Task 1/2: `*_raw_value` is
    ALWAYS the verbatim value as originally submitted/collected (e.g. 題目
    .pdf's own "第一種住宅區", never silently rewritten into a rule-pack
    band label) -- `*_evaluation_value` is the SEPARATE, possibly-mapped
    value actually fed into GradeEngine.grade_factor() when normalization
    was needed (see engine/regional_factor_value_normalization.py), None
    when raw_value already IS the evaluation value. `normalization_reason`/
    `mapping_source` explain any such mapping; never applied to raw_value
    itself. Data provenance (`*_source_type`/`*_source`) and rule
    provenance (`rule_id`/`rule_source_document`/`rule_source_page`) are
    kept as clearly DISTINCT fields -- never collapsed into one ambiguous
    "source" string, so a reader can always tell "where did this NUMBER
    come from" apart from "which regulation/rule matched it"."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    factor_name: str
    category: str = Field(description="e.g. '土地使用管制(1)' -- taken verbatim from the rule pack's own category field")
    base_segment_code: str
    comparable_segment_code: str

    base_raw_value: Union[float, int, str, bool, None] = None
    base_evaluation_value: Union[float, int, str, bool, None] = Field(
        None, description="Only set when normalization mapped base_raw_value to a different value for grading")
    base_source_type: Optional[str] = None
    base_source: Optional[str] = None

    comparable_raw_value: Union[float, int, str, bool, None] = None
    comparable_evaluation_value: Union[float, int, str, bool, None] = Field(
        None, description="Only set when normalization mapped comparable_raw_value to a different value for grading")
    comparable_source_type: Optional[str] = None
    comparable_source: Optional[str] = None

    normalization_reason: Optional[str] = None
    mapping_source: Optional[str] = Field(
        None, description="Traceable to 評價基準明細表.pdf / the A1 rule-source-truth gate -- never invented here")

    base_grade: Optional[str] = None
    comparable_grade: Optional[str] = None
    adjustment_pct: Optional[Decimal] = None

    rule_id: Optional[str] = None
    rule_source_document: Optional[str] = None
    rule_source_page: Optional[str] = None

    status: FieldStatus
    requires_manual_review: bool = False
    reason: Optional[str] = Field(None, description="Populated only when requires_manual_review=True")


class Table51CategorySubtotal(BaseModel):
    """百分比小計 for one of the 8 official categories. `subtotal_pct` is
    None (never a partial/fabricated sum) whenever ANY member factor in
    this category requires manual review -- OFFICIAL_BLANK_FORM_FORMULA:
    每類別小計 = 該類別所有因素修正百分比之和（見表5-1區域因素明細表(住)
    工作表的欄位結構與 B42 儲存格本身之文字註記，非本專案自行發明）。"""
    model_config = ConfigDict(extra="forbid")

    category: str
    category_index: int = Field(description="1-8, matches the official form's 主要項目(N) numbering")
    subtotal_pct: Optional[Decimal] = None
    requires_manual_review: bool = False


class Table51Comparison(BaseModel):
    """One COMPLETE, INDEPENDENT base<->comparable lineage (e.g. P001-00 ->
    P002-00) -- 29 factor_results + 8 category_subtotals + one grand
    total_adjustment_pct. `total_adjustment_pct` is None whenever any
    category_subtotal is None (fail-closed: a comparison with any
    unresolved factor never reports a numeric grand total that silently
    treats the gap as zero)."""
    model_config = ConfigDict(extra="forbid")

    comparable_segment_code: str
    comparison_index: int = Field(description="1/2/3, matches CompetitionSegment.comparison_index")
    factor_results: List[Table51FactorResult] = Field(default_factory=list)
    category_subtotals: List[Table51CategorySubtotal] = Field(default_factory=list)
    total_adjustment_pct: Optional[Decimal] = None
    status: FieldStatus
    requires_manual_review: bool = False
    calculation_mode: Optional[str] = Field(
        None, description="None = fail-closed totals; PARTIAL_DRAFT = totals over resolved factors only")
    excluded_factor_ids: List[str] = Field(
        default_factory=list, description="PARTIAL_DRAFT only: factors left out of subtotals/total")


class Table51Analysis(BaseModel):
    """Top-level 表5-1 result for one Competition case -- base_segment_code
    + an ORDERED list of comparisons, one per comparable segment, each a
    fully independent lineage (never averaged together)."""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    base_segment_code: str
    rule_profile_id: Optional[str] = None
    comparisons: List[Table51Comparison] = Field(default_factory=list)
    remarks: Dict[str, str] = Field(
        default_factory=dict, description="備註欄 text keyed base/comp1/comp2/comp3/whole_case")


# ---------------------------------------------------------------------------
# TABLE4-THREE-COMPARABLE-D1: 表4 比較法調查估價表.
#
# Same design discipline as Table51Analysis: one INDEPENDENT
# Table4Comparison per comparable segment (P001->P002, P001->P003,
# P001->P004), never averaged/merged. Built by engine/
# table4_analysis_engine.py, which reuses GradeEngine/AdjustmentEngine/
# CalculationEngine/ComparableSelectionEngine unmodified -- no grading or
# calculation logic is duplicated here or in that engine module.
# ---------------------------------------------------------------------------

class Table4FactorResult(BaseModel):
    """One row of 表4's 20-slot 個別因素調整 section (19 standard-matrix
    factors + 1 FAR special-policy factor) for ONE base<->comparable
    lineage. Same provenance discipline as Table51FactorResult: raw_value
    is NEVER mutated by normalization; data provenance (*_source_type/
    *_source) is kept separate from rule provenance (rule_id/
    rule_source_document/rule_source_page)."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    factor_name: str
    is_far_special_policy: bool = Field(
        False, description="True only for individual_floor_area_ratio -- Shulin A2's deliberate no-rule-record special policy")
    base_segment_code: str
    comparable_segment_code: str

    base_raw_value: Union[float, int, str, bool, None] = None
    base_evaluation_value: Union[float, int, str, bool, None] = None
    base_source_type: Optional[str] = None
    base_source: Optional[str] = None

    comparable_raw_value: Union[float, int, str, bool, None] = None
    comparable_evaluation_value: Union[float, int, str, bool, None] = None
    comparable_source_type: Optional[str] = None
    comparable_source: Optional[str] = None

    normalization_reason: Optional[str] = None
    mapping_source: Optional[str] = None

    base_grade: Optional[str] = None
    comparable_grade: Optional[str] = None
    adjustment_pct: Optional[Decimal] = None

    rule_id: Optional[str] = None
    rule_source_document: Optional[str] = None
    rule_source_page: Optional[str] = None

    status: FieldStatus
    requires_manual_review: bool = False
    reason: Optional[str] = None


class Table4WeightStatus(str, Enum):
    """不動產估價技術規則§27 has NO formula for combining multiple
    comparables' trial prices into weights (see engine/comparable_
    selection_engine.py's own module docstring, re-verified directly
    against law.moj.gov.tw). A suggested weight is ALWAYS a disclosed,
    named convention -- never claimed as the legally-determined value."""
    SYSTEM_AUXILIARY_SUGGESTION = "SYSTEM_AUXILIARY_SUGGESTION"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class Table4Comparison(BaseModel):
    """One COMPLETE, INDEPENDENT base<->comparable lineage (e.g. P001-00 ->
    P002-00). `regional_adjustment_pct` is BRIDGED from the matching
    Table51Comparison for this SAME comparable_segment_code (never re-
    derived independently, never averaged across comparables, never taken
    from a different comparable's Table5-1 result) -- see
    `regional_adjustment_source_comparison_index` for the lineage proof."""
    model_config = ConfigDict(extra="forbid")

    comparable_segment_code: str
    comparison_index: int

    # 0基本資料 / price-date-related (COMPETITION_PROVIDED_FIXED, from 題目.pdf p.6)
    transaction_date_raw: Optional[str] = None
    land_normal_price_raw: Optional[Decimal] = None
    price_date_adjustment_pct_raw: Optional[Decimal] = None
    adjusted_price_raw: Optional[Decimal] = None
    transaction_source_type: Optional[str] = None
    transaction_source: Optional[str] = None

    # Table5-1 bridge (Task 4) -- NEVER independently recomputed here.
    regional_adjustment_pct: Optional[Decimal] = None
    regional_adjustment_requires_manual_review: bool = False
    regional_adjustment_source_comparison_index: Optional[int] = None

    # 個別因素調整 (Task 5/6) -- 20 slots, reusing GradeEngine/AdjustmentEngine.
    individual_factor_results: List[Table4FactorResult] = Field(default_factory=list)
    individual_adjustment_total_pct: Optional[Decimal] = None

    # 試算價格 chain (reusing engine/calculation_engine.py unmodified).
    trial_price: Optional[Decimal] = None
    adjustment_abs_sum: Optional[Decimal] = None

    # 權重 (Task 9) -- always a disclosed, non-statutory suggestion unless a
    # human has explicitly confirmed one.
    weight_pct: Optional[Decimal] = None
    weight_status: Table4WeightStatus = Table4WeightStatus.MANUAL_REVIEW_REQUIRED
    weight_requires_human_confirmation: bool = True
    weight_basis: Optional[str] = Field(
        None, description="e.g. 'SYSTEM_AUXILIARY: inverse-proportional to adjustment_abs_sum, NON_STATUTORY'")

    status: FieldStatus
    requires_manual_review: bool = False
    reason: Optional[str] = None
    calculation_mode: Optional[str] = Field(
        None, description="None = fail-closed; PARTIAL_DRAFT = trial price over resolved factors only")
    excluded_factor_ids: List[str] = Field(default_factory=list)


class Table4Analysis(BaseModel):
    """Top-level 表4 result for one Competition case -- base_segment_code
    + an ORDERED list of comparisons, one per comparable segment."""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    base_segment_code: str
    rule_profile_id: Optional[str] = None
    comparisons: List[Table4Comparison] = Field(default_factory=list)
    calculation_mode: Optional[str] = None
    base_comparison_price: Optional[Decimal] = Field(
        None, description="PARTIAL_DRAFT only: weighted by SYSTEM_AUXILIARY weights, still needs human review")
    base_comparison_price_basis: Optional[str] = None
    remarks: Dict[str, str] = Field(
        default_factory=dict, description="備註欄 text keyed base/comp1/comp2/comp3/whole_case")
    condition_labels: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="segment_code -> field_id -> 條件名稱 (road/facility name printed beside the distance)")


# ---------------------------------------------------------------------------
# Phase 5: Data Acquisition Layer
# ---------------------------------------------------------------------------

class Coordinate(BaseModel):
    """WGS84 lat/lon. Optional fields left None (not 0.0) when genuinely
    unknown, so downstream code can distinguish 'coordinate at 0,0' from
    'coordinate not available' -- never fabricate a coordinate to fill this in."""
    model_config = ConfigDict(extra="forbid")

    latitude: Optional[float] = None
    longitude: Optional[float] = None


class NormalizedDataPoint(BaseModel):
    """The atomic unit returned by every Data Provider. Mirrors the Data
    Acquisition Layer contract in the master project instructions: value/
    unit/source/source_url/timestamp/confidence/coordinate/notes. Providers
    NEVER guess -- if a value cannot be determined, `value` is None and
    `confidence` is 'UNKNOWN', not a fabricated number."""
    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., description="欄位名稱，對應schemas/field_dictionary.json之field_id")
    value: Union[float, int, str, bool, None] = None
    unit: Optional[str] = None
    source: str = Field(..., description="人類可讀來源說明")
    source_type: str = Field(..., description="'Mock' | '官方提供資料' | 'GovernmentOpenData' | 'API' | '使用者輸入' 等")
    coordinate: Optional[Coordinate] = None
    confidence: str = Field(..., description="'高' | '中' | '低' | 'UNKNOWN'")
    retrieved_at: datetime
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 6: Smart Review / Cross-Form Audit
# ---------------------------------------------------------------------------

class IssueType(str, Enum):
    """The six categories required by Phase 6 / the master project instructions
    section 17."""
    PASSED = "Passed"
    ERROR = "Error"
    WARNING = "Warning"
    MISSING = "Missing"
    INCONSISTENT = "Inconsistent"
    LOW_CONFIDENCE = "Low Confidence"


class Severity(str, Enum):
    """Independent from issue_type: severity grades HOW BAD an Error/
    Inconsistent/Missing finding is, since e.g. a Grade Error on a
    high-max_adjustment factor is more severe than one on a low-max
    factor. PASSED issues always carry severity=INFO."""
    CRITICAL = "CRITICAL"   # propagates to final price (base_parcel_comparison_price)
    HIGH = "HIGH"           # propagates to a subtotal/total but not yet confirmed to reach final price
    MEDIUM = "MEDIUM"       # local to one field, does not by itself change totals
    LOW = "LOW"             # cosmetic / advisory
    INFO = "INFO"           # Passed, or informational only


class CheckType(str, Enum):
    """Which deterministic check produced this issue -- maps directly to
    the 'REQUIRED CHECKS' list in the Phase 6 instructions."""
    GRADE_ERROR = "GRADE_ERROR"
    ADJUSTMENT_ERROR = "ADJUSTMENT_ERROR"
    SUBTOTAL_ERROR = "SUBTOTAL_ERROR"
    TOTAL_ERROR = "TOTAL_ERROR"
    MISSING = "MISSING"
    WRONG_UNIT = "WRONG_UNIT"
    RULE_NOT_FOUND = "RULE_NOT_FOUND"
    CROSS_FORM_INCONSISTENT = "CROSS_FORM_INCONSISTENT"
    PASSED_CHECK = "PASSED_CHECK"
    # 建蔽率／容積率 two-layer lookup cross-validation (LandUseRatioValidator)
    BUILDING_COVERAGE_RATE_INCONSISTENT = "BUILDING_COVERAGE_RATE_INCONSISTENT"
    FLOOR_AREA_RATIO_INCONSISTENT = "FLOOR_AREA_RATIO_INCONSISTENT"
    ZONING_PLAN_UNRESOLVED = "ZONING_PLAN_UNRESOLVED"
    ZONE_CATEGORY_NORMALIZATION_UNCERTAIN = "ZONE_CATEGORY_NORMALIZATION_UNCERTAIN"
    LAND_USE_RATIO_RULE_UNAVAILABLE = "LAND_USE_RATIO_RULE_UNAVAILABLE"
    # 主要道路寬度 Multi-Evidence Model (RoadWidthResolver/RoadWidthValidator)
    ROAD_WIDTH_INCONSISTENT = "ROAD_WIDTH_INCONSISTENT"
    ROAD_WIDTH_UNAVAILABLE = "ROAD_WIDTH_UNAVAILABLE"
    ROAD_WIDTH_EVIDENCE_CONFLICT = "ROAD_WIDTH_EVIDENCE_CONFLICT"
    # 表5-2 Grade Representation Contract (RuleValidator.validate_grade_
    # representation) -- Check B, orthogonal to GRADE_ERROR (Check A):
    # submitted_grade_text does not match any canonical representation
    # (expected.grade, or expected.grade_label ONLY for the evidence-
    # confirmed factors in RuleValidator._GRADE_LABEL_ALSO_VALID_TEXT_FOR)
    # for the SUBMITTED grade_code -- a WARNING-level internal-consistency
    # flag, never conflated with GRADE_ERROR's valuation-critical severity.
    GRADE_REPRESENTATION_INCONSISTENT = "GRADE_REPRESENTATION_INCONSISTENT"
    # submitted_grade_code does not correspond to ANY grade band this
    # factor's rules define -- distinct from MISSING (code absent) and
    # from GRADE_ERROR (code present, valid, but wrong) per Contract v3
    # Phase D guardrail "invalid code != missing code".
    GRADE_CODE_INVALID = "GRADE_CODE_INVALID"


class ExplanationData(BaseModel):
    """Structured (not free-text) explanation, per Phase 6's explicit
    'explanation_data' naming (vs. the master instructions' plain
    'explanation' string) -- machine-readable enough for a UI to render
    each part separately."""
    model_config = ConfigDict(extra="forbid")

    summary: str
    check_type: CheckType
    rule_citation: Optional[str] = None       # e.g. "評價基準明細表範例.pdf p.2"
    computed_steps: List[str] = Field(default_factory=list)  # human-readable calc trail


class RecommendationData(BaseModel):
    """Structured recommendation. `requires_human_review` is always True
    for anything the deterministic engines cannot resolve on their own
    (e.g. which of two conflicting cross-form values is correct)."""
    model_config = ConfigDict(extra="forbid")

    action: str
    suggested_value: Union[str, float, int, None] = None
    requires_human_review: bool = False


class AuditIssue(BaseModel):
    """One finding from Smart Review. Field list matches the Phase 6 ISSUE
    SCHEMA instruction exactly: issue_id, severity, issue_type, source_form,
    field, label, submitted_value, expected_value, rule_id, source,
    explanation_data, recommendation_data, upstream_dependency,
    downstream_impact."""
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    severity: Severity
    issue_type: IssueType
    source_form: str                                   # "表1" | "表5-2" | "表4"
    field: str
    label: str
    submitted_value: Union[str, float, int, None] = None
    expected_value: Union[str, float, int, None] = None
    rule_id: Optional[str] = None
    source: str
    explanation_data: ExplanationData
    recommendation_data: RecommendationData
    upstream_dependency: List[str] = Field(default_factory=list)
    downstream_impact: List[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    """Top-level Smart Review output for one case."""
    model_config = ConfigDict(extra="forbid")

    case_no: str
    issues: List[AuditIssue]
    generated_at: datetime
    engine_versions: dict = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == IssueType.PASSED)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == IssueType.ERROR)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == IssueType.WARNING)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def missing_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == IssueType.MISSING)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def inconsistent_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == IssueType.INCONSISTENT)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def low_confidence_count(self) -> int:
        return sum(1 for i in self.issues if i.issue_type == IssueType.LOW_CONFIDENCE)


# ---------------------------------------------------------------------------
# Dataset Registry (offline/hybrid provider modes) -- tracks locally-cached
# snapshots of government open-data downloads (e.g. NTPC zoning shapefiles)
# so a Provider can answer "is my local copy current, stale, or missing"
# without re-downloading on every query. See providers/dataset_registry.py.
# ---------------------------------------------------------------------------

class DatasetSnapshotStatus(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"  # never synced, or last sync attempt failed


class DatasetSnapshotInfo(BaseModel):
    """One row of the DatasetRegistry: everything needed to know whether a
    locally-cached copy of a government dataset can be trusted, and to
    stamp every NormalizedDataPoint derived from it with an honest
    dataset_version rather than implying it is live/real-time data."""
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    source_name: str
    source_agency: str
    source_url: str
    local_snapshot_version: str = Field(..., description="通常為來源Last-Modified日期或下載時間戳")
    local_path: str
    checksum: str = Field(..., description="本地檔案SHA-256，供完整性驗證")
    license: Optional[str] = None
    refresh_policy: str = Field(..., description="'daily'|'weekly'|'monthly'|'quarterly'，或ISO8601 duration如'P90D'")
    last_synced_at: datetime
    source_last_modified: Optional[datetime] = Field(
        None, description="來源伺服器回報之Last-Modified，非本地下載時間"
    )
    record_count: Optional[int] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# NTPC Zoning Provider (Phase 1 REQ-005 follow-up: 官方都市計畫使用分區圖資)
# ---------------------------------------------------------------------------

class ZoningQueryResult(BaseModel):
    """Result of a point-in-polygon query against the locally-cached NTPC
    zoning snapshot. legal_status is ALWAYS 'reference_only' -- the source
    dataset's own publisher states the map is for reference only and the
    published 都市計畫書圖 governs (see providers/ntpc_zoning_provider.py)."""
    model_config = ConfigDict(extra="forbid")

    zone_name: Optional[str] = None
    plan_name: Optional[str] = None
    matched_polygon_count: int = 0
    source_name: str = "新北市都市計畫土地使用分區及範圍圖"
    source_agency: str = "新北市政府城鄉發展局"
    source_url: str = "https://data.ntpc.gov.tw/datasets/fe26e0a5-54c2-4876-bbc7-150243c048f5"
    dataset_id: Optional[str] = Field(
        None, description="providers/dataset_registry.py DatasetRegistry之主鍵"
        "（例如'ntpc_zoning'）；連同dataset_version才能唯一定位registry中的快照列"
        "並回溯checksum/local_path/source_last_modified，dataset_version單獨不足"
        "（DatasetRegistry以dataset_id為primary key，非dataset_version）"
    )
    dataset_version: Optional[str] = None
    legal_status: str = "reference_only"
    derivation_method: str = "point_in_polygon"
    requires_manual_review: bool = False
    notes: Optional[str] = None


class UrbanPlanStatus(str, Enum):
    """Every value `UrbanPlanBoundaryResult.urban_plan_status` may honestly
    take. This is a DIFFERENT judgment from ZoningQueryResult's zone_name
    lookup above -- see providers/urban_plan_boundary_provider.py's module
    docstring for why the two must never be collapsed into one result."""
    INSIDE = "INSIDE"
    OUTSIDE = "OUTSIDE"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"
    PLAN_MAPPING_UNAVAILABLE = "PLAN_MAPPING_UNAVAILABLE"


class UrbanPlanBoundaryResult(BaseModel):
    """Result of resolving which 新北市都市計畫 (if any) a coordinate falls
    in, via providers/urban_plan_boundary_provider.py's point-in-polygon
    query against the locally-cached 新北市都市計畫範圍 snapshot. Always a
    SEPARATE evidence from ZoningQueryResult (使用分區/zone_name) -- both
    are independently derived from the same submitted coordinate and only
    converge at the LandUseRatioEngine.resolve_floor_area_ratio(zone_name=,
    plan_id=) call site, never earlier.

    plan_id is the canonical, stable, version-controlled identifier from
    data/rules/urban_plan_id_registry.json (see scripts/build_urban_plan_
    id_registry.py) -- NEVER a hash of plan_name, and NEVER plan_name
    itself. Only meaningful (non-None) when urban_plan_status == INSIDE.
    """
    model_config = ConfigDict(extra="forbid")

    urban_plan_status: UrbanPlanStatus
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    source_code: Optional[str] = Field(
        None, description="來源shapefile之key/SDF_ID屬性，如'64/28'，供追溯至原始圖徵記錄"
    )
    source_dataset: str = "新北市都市計畫範圍"
    source_version: Optional[str] = Field(
        None, description="DatasetRegistry快照版本（dataset_id='ntpc_plan_boundary'）"
    )
    source_type: str = "OFFICIAL_OPEN_DATA_REFERENCE"
    requires_manual_review: bool = False
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Road Width Multi-Evidence Model (Phase 6/8 follow-up: 主要道路寬度)
#
# There is no single, precise, authoritative New Taipei City road-width API
# -- pretending otherwise would misrepresent what this system can actually
# verify. Instead, every candidate road-width figure is kept as its own
# RoadWidthEvidence (never collapsed into a bare number), and
# RoadWidthResolver decides whether the available evidence is trustworthy
# enough to cite as a single reference value, genuinely conflicting (in
# which case ALL evidence is preserved and a human is asked to look), or
# simply unavailable.
# ---------------------------------------------------------------------------

class RoadWidthEvidenceType(str, Enum):
    """Ordered roughly by how directly each type answers "what is this
    road's width right now" -- OFFICIAL_ATTRIBUTE (a government road-
    registry attribute) is the most direct; URBAN_PLAN_DESIGN_WIDTH is a
    DIFFERENT semantic quantity (the planned/regulatory width, which need
    not equal the as-built current width -- see RoadWidthResolver's
    docstring); EXTERNAL_MAP_ATTRIBUTE (e.g. an OSM way's width tag) and
    GEOMETRIC_ESTIMATE (derived from road-edge geometry) are both lower-
    confidence, non-official measurements; SUBMITTED_VALUE is what an
    appraiser wrote on the report -- it is NEVER a candidate for
    RoadWidthResolver's official/reference resolution (see
    RoadWidthResolver docstring), only ever the "submitted" side of a
    RoadWidthValidator comparison."""
    OFFICIAL_ATTRIBUTE = "OFFICIAL_ATTRIBUTE"
    URBAN_PLAN_DESIGN_WIDTH = "URBAN_PLAN_DESIGN_WIDTH"
    EXTERNAL_MAP_ATTRIBUTE = "EXTERNAL_MAP_ATTRIBUTE"
    GEOMETRIC_ESTIMATE = "GEOMETRIC_ESTIMATE"
    SUBMITTED_VALUE = "SUBMITTED_VALUE"


class RoadWidthEvidence(BaseModel):
    """One candidate road-width figure with full provenance -- mirrors the
    ZoningQueryResult/LandUseRatioResolutionResult convention (a dedicated,
    richly-typed result model) rather than being force-fit into
    NormalizedDataPoint/Evidence, which have no first-class evidence_type/
    dataset_id/legal_status/derivation_method slots. width_m/road_name are
    Optional because a source that genuinely has no data for this road
    must still be representable as an (empty) RoadWidthEvidence rather than
    simply omitted -- omission and "queried, found nothing" are different
    facts."""
    model_config = ConfigDict(extra="forbid")

    road_name: Optional[str] = None
    width_m: Optional[Decimal] = None
    evidence_type: RoadWidthEvidenceType
    source_name: Optional[str] = None
    source_agency: Optional[str] = None
    source_url: Optional[str] = None
    dataset_id: Optional[str] = None
    dataset_version: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    confidence: str = Field("UNKNOWN", description="'高'/'中'/'低'/'UNKNOWN'")
    legal_status: Optional[str] = None
    derivation_method: Optional[str] = None
    requires_manual_review: bool = False
    notes: Optional[str] = None


class RoadWidthResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"                # single trustworthy value, all available evidence agrees
    CONFLICT = "CONFLICT"                # 2+ evidence entries disagree -- never averaged/silently picked
    UNAVAILABLE = "UNAVAILABLE"          # no non-submitted evidence at all


class RoadWidthResolutionResult(BaseModel):
    """RoadWidthResolver's output. `evidence` always holds every
    non-SUBMITTED_VALUE candidate considered (even on CONFLICT/UNAVAILABLE)
    so a human reviewer never has to re-run the query to see what was
    weighed. `resolved_width_m`/`resolved_evidence` are populated ONLY when
    status=RESOLVED."""
    model_config = ConfigDict(extra="forbid")

    status: RoadWidthResolutionStatus
    resolved_width_m: Optional[Decimal] = None
    resolved_evidence: Optional[RoadWidthEvidence] = None
    evidence: List[RoadWidthEvidence] = Field(default_factory=list)
    requires_manual_review: bool = False
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase API-1: Official External Data Evidence (新北市已公告徵收案件地籍資料
# / 新北市公告土地現值 — data.ntpc.gov.tw OpenAPI). Mirrors RoadWidthEvidence's
# convention (a dedicated, richly-typed evidence model, not a NormalizedData
# Point force-fit) because both need first-class dataset_id/source_authority/
# query_parameters/authoritative_status slots NormalizedDataPoint has no room
# for. Per this round's explicit scope: this evidence is EXTERNAL
# CORROBORATING EVIDENCE only -- it is never written into SubmittedFormData,
# never consumed by RuleEngine/AdjustmentEngine/CalculationEngine/AuditEngine,
# and a query miss must never be read as a legal conclusion (see each
# model's own field docstrings for the exact honest-uncertainty semantics).
# ---------------------------------------------------------------------------

class ExpropriationCaseStatus(str, Enum):
    """Three-state outcome of one query against 新北市已公告徵收案件地籍資料
    (dataset_id DD9C0006-8FAD-450D-8C7B-E59240B0ED13), never collapsed to a
    boolean. This dataset's own description covers only 92年以後 一般徵收案件
    （不含更正及撤銷徵收），so a miss can NEVER be reported as "this parcel was
    not expropriated" -- that would be inventing a legal conclusion the
    dataset's own coverage cannot support (it might be a pre-92年 case, a
    更正/撤銷 case the dataset excludes by design, or a 區段徵收 case, none of
    which this dataset would ever show)."""
    FOUND_IN_DATASET = "FOUND_IN_DATASET"
    NOT_FOUND_IN_DATASET = "NOT_FOUND_IN_DATASET"
    UNKNOWN = "UNKNOWN"


class ExpropriationCaseMatch(BaseModel):
    """One DISTINCT expropriation event for a parcel (Phase API-1.9).

    "Distinct" means distinct by full content
    (announcement_year, project_name, district, segment, land_no) --
    Phase API-1.8's live full-dataset audit found (district,segment,
    land_no) is NOT a unique key in 新北市已公告徵收案件地籍資料: 441
    real parcels have multiple source rows, and Phase API-1.9's follow-up
    audit further split those into 322 same-year-different-project + 57
    both-differ genuine PARCEL_MULTI_EVENT cases (a parcel legitimately
    expropriated under separate government projects) versus 62
    EXACT_SOURCE_DUPLICATE parcels (every row for that parcel is byte-
    identical -- a raw source redundancy, not a second event). A
    `ExpropriationCaseMatch` is emitted once per DISTINCT event signature,
    never once per raw row -- so an EXACT_SOURCE_DUPLICATE parcel (e.g. the
    same project row listed 3 times) still produces exactly one match here,
    while `ExpropriationCaseEvidence.raw_match_count` separately preserves
    the raw row count for provenance/audit (see that field's docstring)."""
    model_config = ConfigDict(extra="forbid")

    announcement_year: Optional[str] = None
    project_name: Optional[str] = None
    district: str
    segment: str
    land_no: str


class ExpropriationCaseEvidence(BaseModel):
    """One query result against the NTPC expropriation-case dataset.
    `requires_manual_review` is HARDCODED True at construction (see field
    default) for every status, including FOUND_IN_DATASET -- this evidence
    is corroborating only, per this round's explicit "API Evidence只能作
    external corroborating evidence" instruction; nothing here may itself
    become a final determination without human confirmation. This is also
    why Phase API-1.9 introduces no new status for "multiple matches" (see
    ExpropriationCaseStatus): every result already mandates manual review
    regardless of match_count, so a downstream reviewer resolving which of
    several real matches applies to the current case IS this field's
    existing job, not a gap needing a new enum value.

    `segment` is the real 地籍段名 (cadastral section name, e.g. "金美段"),
    NEVER this project's own `price_segment_code` (e.g. "P002-00") -- see
    providers/cadastral_identifier.py's module docstring for the Phase
    API-1.5 fix this field naming now enforces (Phase API-1 conflated the
    two).

    `local_snapshot_version`/`checksum`/`downloaded_at`/`record_count`/
    `schema_version` are populated ONLY when this evidence came from
    providers/cadastral_dataset_cache.py's local synced snapshot (Phase
    API-1.5's primary query path) -- they stay None for a PARSE_UNCERTAIN
    or snapshot-not-yet-synced result, never fabricated.

    Phase API-1.9 multi-record contract (see providers/expropriation_case_
    provider.py's module docstring for the full audit this codifies):
    `match_count` is the number of DISTINCT events for this parcel (never
    the raw row count -- see `raw_match_count` for that), and governs the
    convenience-field semantics below. `announcement_year`/`project_name`
    are the two PRE-Phase-API-1.9 single-value convenience fields, kept for
    backward compatibility with any existing single-match consumer:
      - match_count == 0: status=NOT_FOUND_IN_DATASET, both None (unchanged).
      - match_count == 1: both populated from that one match (unchanged
        behavior for every parcel that was already unambiguous before this
        round).
      - match_count > 1: both explicitly None -- NEVER silently set to the
        first/latest/highest-year match. A caller reading only these two
        legacy fields for a multi-match parcel sees an honest "not a single
        value", not a silently-arbitrary one; `matches` carries every real
        distinct event, in a fixed deterministic order (see
        expropriation_case_provider.py's `_sort_key_for_match`)."""
    model_config = ConfigDict(extra="forbid")

    status: ExpropriationCaseStatus
    district: Optional[str] = None
    segment: Optional[str] = None
    land_no: Optional[str] = None
    announcement_year: Optional[str] = None
    project_name: Optional[str] = None
    match_count: int = Field(0, description="DISTINCT事件數（非raw row數）。0=NOT_FOUND_IN_DATASET。")
    matches: List[ExpropriationCaseMatch] = Field(
        default_factory=list,
        description="該parcel全部DISTINCT徵收事件，依固定deterministic順序排列，絕不因fetchone/first-match而遺漏。",
    )
    raw_match_count: int = Field(
        0, description="該parcel在原始snapshot中的raw row總數（含EXACT_SOURCE_DUPLICATE重複列，未去重）。"
    )
    distinct_event_count: int = Field(0, description="等同match_count，明確命名以利與raw_match_count對照閱讀。")
    dataset_id: Optional[str] = None
    dataset_name: Optional[str] = None
    source_authority: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    query_parameters: Optional[Dict[str, str]] = None
    authoritative_status: Optional[str] = Field(
        None, description="'OFFICIAL_OPEN_DATA_PARTIAL_COVERAGE' 等，說明此為官方資料但涵蓋範圍有限"
    )
    confidence: str = Field("UNKNOWN", description="'高'/'中'/'低'/'UNKNOWN'")
    requires_manual_review: bool = True
    local_snapshot_version: Optional[str] = None
    checksum: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    record_count: Optional[int] = None
    schema_version: Optional[str] = None
    notes: Optional[str] = None


class LandPriceFieldStatus(str, Enum):
    """Per-field, per-dataset-year availability -- the concrete mechanism
    behind this round's mandatory DatasetVersion->SchemaAdapter requirement.
    AVAILABLE means the field genuinely exists in that year's dataset schema
    AND a value was found for the queried parcel; NOT_FOUND_FOR_PARCEL means
    the field exists in that year's schema but this parcel had no matching
    row; FIELD_NOT_AVAILABLE_FOR_YEAR means the queried year's dataset
    schema simply does not carry this field at all (e.g. 114年 dataset has
    no 公告地價 column) -- this is a schema fact, not a missing-value fact,
    and MUST NOT be reported the same way as NOT_FOUND_FOR_PARCEL; UNKNOWN
    covers "not queried yet" / API failure / unverified-year schema."""
    AVAILABLE = "AVAILABLE"
    NOT_FOUND_FOR_PARCEL = "NOT_FOUND_FOR_PARCEL"
    FIELD_NOT_AVAILABLE_FOR_YEAR = "FIELD_NOT_AVAILABLE_FOR_YEAR"
    UNKNOWN = "UNKNOWN"


class LandPriceEvidence(BaseModel):
    """One query result against 新北市公告土地現值 (year-specific dataset_id,
    e.g. 114年=826870ef-4ea5-48bf-915b-e0a33158cf06). `announced_land_
    current_value`/`announced_land_price` are each paired with their own
    _status field precisely so a caller can never do `if value is None`
    and wrongly conclude "not found" when the real reason is "this year's
    dataset schema never had this column" -- collapsing those two facts is
    exactly what this round's instructions forbid.

    `segment` is the real 地籍段名 (cadastral section name), NEVER this
    project's own `price_segment_code` -- see providers/cadastral_
    identifier.py's module docstring (Phase API-1.5 fix).

    `local_snapshot_version`/`checksum`/`downloaded_at`/`record_count`/
    `schema_version` are populated ONLY when this evidence came from a
    completed per-district sync (providers/cadastral_dataset_cache.py,
    Phase API-1.5's primary query path) -- Phase API-1's live-scan-only
    implementation always left these None; that is no longer true once a
    snapshot exists for the queried district."""
    model_config = ConfigDict(extra="forbid")

    district: Optional[str] = None
    segment: Optional[str] = None
    land_no: Optional[str] = None
    dataset_year: Optional[str] = None
    announced_land_current_value: Optional[Decimal] = None
    announced_land_current_value_status: LandPriceFieldStatus = LandPriceFieldStatus.UNKNOWN
    announced_land_price: Optional[Decimal] = None
    announced_land_price_status: LandPriceFieldStatus = LandPriceFieldStatus.UNKNOWN
    dataset_id: Optional[str] = None
    dataset_name: Optional[str] = None
    source_authority: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    local_snapshot_version: Optional[str] = None
    checksum: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    record_count: Optional[int] = None
    schema_version: Optional[str] = None
    query_parameters: Optional[Dict[str, str]] = None
    authoritative_status: Optional[str] = None
    confidence: str = Field("UNKNOWN", description="'高'/'中'/'低'/'UNKNOWN'")
    requires_manual_review: bool = True
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Central max-adjustment-range table (內政部104年1月30日台內地字第10413006723
# 號令附表：「影響地價個別因素評價基準表」／「影響地價區域因素評價基準表」)
# ---------------------------------------------------------------------------

class CentralMaxRangeCellState(str, Enum):
    """How one (land_use_type[+subgrade], major_category, item) cell in the
    source table reads. The source's own note 七 is explicit that a "-" is
    NOT zero: "最大影響範圍(%)為「-」者，該項不予考慮調整" (this item is not
    to be considered/adjusted for this land type at all -- a fundamentally
    different statement than "considered, with a 0% allowance"). Separately,
    a handful of category rows (「其他影響因素」on every regional table) are
    printed with no numeric value and no "-" either -- genuinely blank cells
    reserved for local jurisdictions to define, not an omission in this
    digitization. Conflating any of these three states would misrepresent
    the source."""
    VALUE = "VALUE"
    DASH_NOT_APPLICABLE = "DASH_NOT_APPLICABLE"
    BLANK_NO_DATA = "BLANK_NO_DATA"


class CentralMaxRangeEntry(BaseModel):
    """One cell of the digitized central table. `land_use_subgrade` is None
    for the individual-factor table and for the regional tables that have no
    subgrade split (農業用地/其他用地); it is one of the source's own labels
    (e.g. "高度商業用地") for 住宅用地/商業用地/工業用地, which the source
    prints as separate columns -- never invented or normalized by this
    codebase."""
    model_config = ConfigDict(extra="forbid")

    table_type: str = Field(..., description="'individual'（影響地價個別因素評價基準表）或"
                             "'regional'（影響地價區域因素評價基準表）")
    land_use_type: str = Field(..., description="住宅用地／商業用地／工業用地／農業用地／其他用地")
    land_use_subgrade: Optional[str] = Field(
        None, description="來源表格自身之子級別欄位標籤（如「高度商業用地」），"
        "無子級別分欄之表格（個別因素表、農業用地/其他用地區域因素表）固定為None"
    )
    major_category_code: str = Field(..., description="主要項目編號，例如'1'、'6'")
    major_category_name: str = Field(..., description="主要項目名稱，例如'宗地條件'（不含編號）")
    item_code: Optional[int] = Field(
        None, description="細項編號（表格原文之阿拉伯數字），無編號之類別層級數值"
        "（如個別因素表「6.其他」、區域因素表「其他影響因素」）為None"
    )
    item_name: Optional[str] = Field(None, description="細項名稱；類別層級數值（item_code為None時）亦為None")
    cell_state: CentralMaxRangeCellState
    max_range_pct: Optional[Decimal] = Field(
        None, description="最大影響範圍(%)，僅於cell_state=VALUE時有值"
    )
    source_page: int = Field(..., description="來源PDF之0-index頁碼")
    source_table_title: str = Field(..., description="來源表格標題，例如'2.影響商業用地區域因素評價基準表'")

    @model_validator(mode="after")
    def _value_nullability_matches_cell_state(self) -> "CentralMaxRangeEntry":
        if self.cell_state == CentralMaxRangeCellState.VALUE and self.max_range_pct is None:
            raise ValueError("cell_state=VALUE 但 max_range_pct 為 None（數值與狀態不一致）")
        if self.cell_state != CentralMaxRangeCellState.VALUE and self.max_range_pct is not None:
            raise ValueError(f"cell_state={self.cell_state} 但 max_range_pct 非None（"
                              "'-'與空白皆不得帶有數值，避免被誤讀為0%或任何具體數字）")
        return self


class CentralMaxRangeSourceDocument(BaseModel):
    """Citation block for the entire digitized dataset -- one shared record,
    not duplicated per entry. source_pdf_sha256/byte_size were computed from
    the actual file used for this transcription and re-verified by
    re-downloading from source_url during this session (byte-for-byte
    identical); digitization_method documents WHY this is a manual visual
    transcription rather than programmatic text extraction (the source PDF's
    embedded font has no usable ToUnicode mapping for the Chinese label text
    -- pypdf and pymupdf both extract the same corrupted characters; only the
    ASCII digits/numbers survive extraction intact)."""
    model_config = ConfigDict(extra="forbid")

    document_name: str = "行政院公報 第021卷 第020期 內政篇"
    issuing_agency: str = "內政部"
    document_number: str = "台內地字第10413006723號"
    gazette_publish_date: str = Field("20150130", description="西元年月日，公報發布日")
    effective_date: str = Field("1040301", description="民國年月日，令文所載生效日："
                                 "「自中華民國一百零四年三月一日生效」")
    source_url: str = (
        "https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg021020/"
        "ch02/type2/gov10/num7/Eg.pdf"
    )
    source_pdf_sha256: str
    source_pdf_byte_size: int
    digitization_method: str = Field(
        "MANUAL_VISUAL_TRANSCRIPTION",
        description="來源PDF內嵌字型無可用ToUnicode對照表，中文標籤文字層擷取後為亂碼"
        "（pypdf與pymupdf結果相同，僅ASCII數字未受影響）；改以PyMuPDF將各頁面渲染為"
        "高解析度圖像，逐列人工核對後轉錄，非OCR、非文字層自動擷取",
    )
    manual_verification_status: str = Field(
        "VISUALLY_VERIFIED_SINGLE_PASS",
        description="已由單一人工核對流程逐儲存格對照渲染圖像轉錄完成；尚未經第二人獨立複核"
        "（雙人覆核為更高信賴度標準，本輪未執行，如需更高信賴度應另行安排複核）",
    )
    digitized_at: datetime
    source_local_path: Optional[str] = Field(
        None, description="專案內正式歸檔之來源檔案路徑（相對於repo根目錄），"
        "例如'data/sources/urban_planning/內政部104年公報_...pdf'；None表示尚未歸檔，"
        "僅有source_url可供追溯（依賴外部網站長期可用）"
    )


class CentralMaxRangeDataset(BaseModel):
    """Top-level container for the fully digitized central table set (both
    the individual-factor table and all five regional-factor tables, every
    land-use type and every source-defined subgrade)."""
    model_config = ConfigDict(extra="forbid")

    source_document: CentralMaxRangeSourceDocument
    entries: List[CentralMaxRangeEntry]


class LocalToCentralMappingStatus(BaseModel):
    """Whether one of THIS codebase's local rule files (data/rules/
    regional_rules.json, data/rules/individual_rules.json) has a confirmed
    official mapping to a specific central-table land_use_type (+subgrade,
    for tables that have one). Defaults to UNMAPPED: no official document
    states which of the central table's subgrades (e.g. 高度/中度/普通/
    村里鄰商業用地) a given district's local 基準明細表 corresponds to --
    that association is a jurisdiction-specific administrative decision the
    central table itself does not make, and this codebase's existing rule
    files do not record it either (see providers/... land_use_type is
    recorded simply as e.g. "商業用地", with no subgrade). Never
    auto-assigned; a human must confirm and update mapping_status to MAPPED
    with a cited official source before any cross-validation against the
    central table's subgrade-specific figures can be considered reliable."""
    model_config = ConfigDict(extra="forbid")

    local_rule_file: str = Field(..., description="例如 'data/rules/regional_rules.json'")
    local_land_use_type: str = Field(..., description="該規則檔內記錄之land_use_type值，例如'商業用地'")
    table_type: str = Field(..., description="'individual' 或 'regional'")
    mapping_status: str = Field(
        "UNMAPPED", description="'UNMAPPED'（預設，無官方依據）或'MAPPED'（已有官方依據並填妥"
        "mapped_central_land_use_type/mapped_central_subgrade）"
    )
    mapped_central_land_use_type: Optional[str] = None
    mapped_central_subgrade: Optional[str] = Field(
        None, description="僅當該central table之land_use_type有子級別分欄時才有意義"
    )
    notes: str = Field(
        "尚無官方依據可確認地方基準明細表對應中央表哪一子級別（如商業用地之高度/中度/"
        "普通/村里鄰），需人工確認，本系統不自行猜測映射關係",
    )


# ---------------------------------------------------------------------------
# 建蔽率／容積率 -- two-layer design (Phase X follow-up):
#   Layer 1: 都市計畫法新北市施行細則附表一 -- a genuine CITYWIDE common
#            ceiling table (verified: 第三十六條 states 附表一 values are a
#            legal ceiling -- "各土地使用分區之建蔽率不得超過附表一之規定"
#            -- but a specific 都市計畫書 may set a STRICTER local limit,
#            which then governs instead: "其都市計畫書另有較嚴格之規定者，
#            從其規定"). This table covers BOTH 建蔽率 and 容積率, but for
#            住宅區/商業區/其他使用分區 the 容積率 COLUMN ITSELF says
#            "依實際發展，循都市計畫程序，於都市計畫書中訂定" -- i.e. there
#            is deliberately NO citywide 容積率 for these zone types; each
#            都市計畫 area sets its own.
#   Layer 2: per-(都市計畫, zone) 容積率 registry, populated only from an
#            actual official document for that specific plan area -- never
#            a generalization from one plan to another.
# ---------------------------------------------------------------------------

class LandUseRatioMetric(str, Enum):
    BUILDING_COVERAGE_RATE = "building_coverage_rate"  # 建蔽率
    FLOOR_AREA_RATIO = "floor_area_ratio"  # 容積率


class LandUseRatioCellState(str, Enum):
    """How one (zone_name, metric) cell in 附表一 reads."""
    VALUE = "VALUE"
    DEFERRED_TO_PLAN_DOCUMENT = "DEFERRED_TO_PLAN_DOCUMENT"  # 「依實際發展/需要，循都市計畫程序，於都市計畫書中訂定」
    DEFERRED_TO_SUBREGULATION = "DEFERRED_TO_SUBREGULATION"  # 「依本施行細則相關規定辦理」（保護區/農業區之容積率）


class NtpcCommonZoneRatioEntry(BaseModel):
    """One cell of 都市計畫法新北市施行細則附表一（土地使用分區建蔽率及容積率
    規定表）。`ceiling_note` restates 第三十六條第二項's override rule so a
    caller reading one entry in isolation still sees the caveat -- this
    table is a legal MAXIMUM, not an unconditionally-applicable value."""
    model_config = ConfigDict(extra="forbid")

    zone_name: str = Field(..., description="附表一原文之土地使用分區名稱，例如'商業區'")
    metric: LandUseRatioMetric
    cell_state: LandUseRatioCellState
    value_pct: Optional[Decimal] = Field(None, description="僅於cell_state=VALUE時有值")
    terrain_qualifier: Optional[str] = Field(
        None, description="僅旅館區之容積率有此區分：'山坡地'或'平地'，其餘皆為None"
    )
    caveat_note: Optional[str] = Field(
        None, description="附表一原文中緊隨數值之但書文字（如保存區「但區內原有建築物已"
        "超過者，不在此限」），逐字保留，不改寫"
    )
    legal_source: str = "都市計畫法新北市施行細則"
    article_or_section: str = "第三十六條、附表一"
    ceiling_override_note: str = (
        "本表所列為全市法定上限（第三十六條第一項：「各土地使用分區之建蔽率不得超過"
        "附表一之規定」）；該都市計畫書另有較嚴格之規定者，從其規定（第三十六條第二項），"
        "本系統未逐一查核個別都市計畫書是否有更嚴格限制"
    )

    @model_validator(mode="after")
    def _value_nullability_matches_cell_state(self) -> "NtpcCommonZoneRatioEntry":
        if self.cell_state == LandUseRatioCellState.VALUE and self.value_pct is None:
            raise ValueError("cell_state=VALUE 但 value_pct 為 None")
        if self.cell_state != LandUseRatioCellState.VALUE and self.value_pct is not None:
            raise ValueError(f"cell_state={self.cell_state} 但 value_pct 非None")
        return self


class NtpcCommonZoneRatioSourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_name: str = "都市計畫法新北市施行細則"
    issuing_agency: str = "新北市政府"
    version_note: str = Field("民國111年3月16日修正", description="法規本身之修正日期標示")
    legal_basis_article: str = "本細則依都市計畫法第八十五條規定訂定"
    source_url: str = (
        "https://www.planning.ntpc.gov.tw/uploaddowndoc?dis=planningcitylaw&"
        "file=planningcitylaw/202303271001520.pdf&filedisplay=1110316-"
        "%E9%83%BD%E5%B8%82%E8%A8%88%E7%95%AB%E6%B3%95%E6%96%B0%E5%8C%97%E5%B8%82%E6"
        "%96%BD%E8%A1%8C%E7%B4%B0%E5%89%87(%E5%85%A8%E6%A2%9D%E6%96%8758%E6%A2%9D)"
        "(111%E5%B9%B43%E6%9C%8818%E6%97%A5%E7%94%9F%E6%95%88).pdf&flag=doc"
    )
    source_pdf_sha256: str
    source_pdf_byte_size: int
    digitization_method: str = Field(
        "TEXT_LAYER_EXTRACTION_MANUALLY_VERIFIED",
        description="本文件PDF文字層可直接擷取（與央表gazette_eg.pdf不同，無字型編碼異常），"
        "已逐條與附表一原文人工比對確認",
    )
    digitized_at: datetime
    source_local_path: Optional[str] = Field(
        None, description="專案內正式歸檔之來源檔案路徑（相對於repo根目錄）"
    )


class NtpcCommonZoneRatioDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_document: NtpcCommonZoneRatioSourceDocument
    entries: List[NtpcCommonZoneRatioEntry]


class FloorAreaRatioRuleStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    SOURCE_UNCONFIRMED = "SOURCE_UNCONFIRMED"
    UNAVAILABLE_PER_PLAN = "UNAVAILABLE_PER_PLAN"


class PlanZoneFloorAreaRatioEntry(BaseModel):
    """One (都市計畫, zone) 容積率 fact, sourced from that specific plan's
    own 土地使用分區管制要點 -- NEVER generalized from one plan to another.
    `rule_status=CONFIRMED` requires every citation field to be populated;
    the model_validator below enforces this so a CONFIRMED entry can never
    silently be missing its source."""
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(..., description="系統內部代碼，例如'jinshan'（非官方代碼，僅供本系統查詢用）")
    plan_name: str = Field(..., description="官方都市計畫名稱，例如'金山都市計畫'")
    zone_name: str = Field(..., description="該都市計畫土地使用分區管制要點原文之分區名稱，"
                            "例如'第二種商業區'")
    floor_area_ratio_pct: Optional[Decimal] = None
    rule_status: FloorAreaRatioRuleStatus
    document_title: Optional[str] = None
    document_agency: Optional[str] = None
    document_date: Optional[str] = Field(None, description="文件標示之日期，原樣保留（如'中華民國109年11月'）")
    article_or_section: Optional[str] = None
    source_url: Optional[str] = None
    source_pdf_sha256: Optional[str] = None
    source_pdf_byte_size: Optional[int] = None
    source_local_path: Optional[str] = Field(
        None, description="專案內正式歸檔之來源檔案路徑（相對於repo根目錄）"
    )
    requires_manual_review: bool = True
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _confirmed_requires_full_citation(self) -> "PlanZoneFloorAreaRatioEntry":
        if self.rule_status == FloorAreaRatioRuleStatus.CONFIRMED:
            missing = [
                f for f in ("floor_area_ratio_pct", "document_title", "document_date",
                            "article_or_section", "source_url", "source_pdf_sha256")
                if getattr(self, f) is None
            ]
            if missing:
                raise ValueError(f"rule_status=CONFIRMED但缺少引註欄位：{missing}")
        if self.rule_status != FloorAreaRatioRuleStatus.CONFIRMED and self.floor_area_ratio_pct is not None:
            raise ValueError(f"rule_status={self.rule_status} 但 floor_area_ratio_pct 非None"
                              "（未確認之規則不得帶有具體數值）")
        return self


class LandUseRatioResolutionResult(BaseModel):
    """Output of LandUseRatioEngine's lookup -- the honest answer to "what
    ratio applies here", including which layer of the priority chain
    (個別都市計畫 -> 新北市共通規定 -> 查無資料) actually supplied it."""
    model_config = ConfigDict(extra="forbid")

    zone_name: str
    plan_id: Optional[str] = Field(None, description="internal_plan_id -- 系統內部識別碼，非官方代碼")
    official_plan_name: Optional[str] = Field(
        None, description="僅resolution_layer=PLAN_SPECIFIC時有值，來自該筆登記之官方都市計畫名稱"
    )
    metric: LandUseRatioMetric
    resolved_value_pct: Optional[Decimal] = None
    resolution_layer: str = Field(
        ..., description="'PLAN_SPECIFIC'（個別都市計畫土地使用分區管制要點）／"
        "'NTPC_COMMON'（都市計畫法新北市施行細則附表一）／'UNAVAILABLE'（查無資料）"
    )
    rule_status: str
    legal_source: Optional[str] = Field(None, description="法規/文件名稱，僅於resolved_value_pct有值時填入")
    article_or_section: Optional[str] = None
    source_url: Optional[str] = None
    source_document_checksum: Optional[str] = Field(None, description="來源文件SHA-256")
    dataset_version: Optional[str] = Field(None, description="法規版本標示或文件公告日期")
    requires_manual_review: bool
    notes: Optional[str] = None


class ZoneNameNormalizationResult(BaseModel):
    """official_raw_zone_name（NtpcZoningProvider查得的官方原始分區名稱，
    如「第二種商業區」）must NEVER be silently overwritten -- this model
    keeps it and the normalized category (used only for Layer 1's 19-row
    common table lookup) as two separate fields, always both present.
    normalized_zone_category is None when this codebase's normalizer
    cannot reliably classify the raw name (e.g. 道路用地／綠地／學校用地
    等公共設施用地類，屬附表三而非附表一，本輪未digitize附表三) -- that
    is reported honestly, never guessed."""
    model_config = ConfigDict(extra="forbid")

    official_raw_zone_name: str
    normalized_zone_category: Optional[str] = Field(
        None, description="都市計畫法新北市施行細則附表一之19種標準分區之一；"
        "None表示本系統無法可靠歸類（見normalization_confidence=UNKNOWN）"
    )
    normalization_method: str = Field(
        ..., description="'EXACT_MATCH'（原始名稱本身已完全等於附表一分區名稱）／"
        "'SUBGRADE_PREFIX_STRIP'（去除「第X種」前綴與括號附註後比對成功）／"
        "'UNCLASSIFIABLE'（兩者皆未成功，可能屬附表三公共設施用地或本系統未涵蓋之分區）"
    )
    normalization_confidence: str = Field(
        ..., description="'高'（EXACT_MATCH或SUBGRADE_PREFIX_STRIP成功比對）／"
        "'UNKNOWN'（UNCLASSIFIABLE，需人工確認，非系統信心不足而是根本無法比對）"
    )
    requires_manual_review: bool


# ---------------------------------------------------------------------------
# Independent Uploaded Document Extraction (Phase 6 follow-up: the
# PDF/PNG/JPG -> 表單辨識 -> 欄位擷取 -> confidence -> 人工確認 ->
# Structured Input seam docs/phase6/document_extraction_spec.md's
# TextractAdapter placeholder described but never built. This module's
# strict boundary: extraction code ONLY reads text/fields and their
# provenance -- it NEVER grades, computes an adjustment rate, or judges
# correctness. That stays exclusively RuleEngine/AdjustmentEngine/
# CalculationEngine/AuditEngine's job (see engine/human_confirmation.py's
# docstring for the full division of responsibility).
# ---------------------------------------------------------------------------

class FormType(str, Enum):
    """This round's MVP scope: 3 of the ~14 official 查估書表 forms (see
    the master project instructions for the full set). An unmatched page
    (a map/diagram page, an unsupported form, or a page FormClassifier
    cannot confidently identify) is UNCERTAIN -- never guessed as one of
    the 3 supported types."""
    TABLE1_LAND_SEGMENT_SURVEY = "表1_地價區段勘查表"
    TABLE5_2_REGIONAL_FACTOR_ANALYSIS = "表5-2_影響地價區域因素分析明細表"
    TABLE4_COMPARISON_METHOD = "表4_比較法調查估價表"
    UNCERTAIN = "UNCERTAIN"


class FormClassificationResult(BaseModel):
    """FormClassifier's output for one page. `evidence` lists WHICH
    deterministic markers matched (title text, table-number token, a
    distinctive field label) so a human reviewer can see why the
    classifier decided what it decided -- never a bare label with no
    justification. confidence is a fraction 0.0-1.0 here (unlike the
    Provider layer's '高'/'中'/'低'/'UNKNOWN' convention) because
    FormClassifier's own gating threshold (engine/form_classifier.py) is
    numeric; this is a fully separate typed model from LandUseRatio's/
    RoadWidth's Evidence conventions, since it describes a different kind
    of provenance (document layout matching, not external-dataset
    lookup)."""
    model_config = ConfigDict(extra="forbid")

    page_number: int
    form_type: FormType
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)
    requires_manual_review: bool


class ExtractionMethod(str, Enum):
    """How one ExtractedField's raw_text was obtained. TEXT_LAYER (this
    round's real, working method -- see providers/document_extraction_
    provider.py's LocalExtractionProvider) reads a PDF's embedded text
    layer + word-level bounding boxes via PyMuPDF; it is NOT OCR (no image
    pixel analysis) -- reliable only for PDFs whose text layer is intact
    (查估書表範本.pdf's is, confirmed during this round's source survey;
    a scanned/rasterized PDF or a photographed form would need
    AWS_TEXTRACT or another true OCR method instead, neither of which is
    AWS_RUNTIME_VERIFIED in this codebase yet)."""
    TEXT_LAYER = "TEXT_LAYER"
    AWS_TEXTRACT = "AWS_TEXTRACT"
    MOCK_FIXTURE = "MOCK_FIXTURE"


class BoundingBox(BaseModel):
    """Pixel/point coordinates of one extracted token on its source page,
    in the source document's own coordinate space (PyMuPDF's page-point
    units for TEXT_LAYER; Textract's normalized 0-1 ratios would need
    their own adapter-side conversion, not assumed here)."""
    model_config = ConfigDict(extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float


class ExtractionStatus(str, Enum):
    """Row/cell-level structural outcome of one extraction attempt --
    orthogonal to confidence/requires_manual_review (a field can be
    EXTRACTED with low confidence, but a field that is not EXTRACTED
    always requires manual review). Added for 表5-2's table-shaped layout,
    where "this factor's row wasn't found at all" and "this factor's row
    exists but one specific cell is blank" are genuinely different facts
    that must never collapse into one representation (see 表5-2 Extraction
    Implementation-Ready Contract v3 §1/§5)."""
    EXTRACTED = "EXTRACTED"            # value cell located, raw_text non-empty, normalized
    CELL_BLANK = "CELL_BLANK"          # row located; this specific column's cell has no text
    ROW_NOT_FOUND = "ROW_NOT_FOUND"    # this canonical factor's row never located on the page
    UNKNOWN_FACTOR = "UNKNOWN_FACTOR"  # label text found but does not match canonical lookup
    PARSE_FAILED = "PARSE_FAILED"      # text present at expected position, did not fit pattern


class ExtractionRole(str, Enum):
    """Which quantity, among several sharing one canonical field_id, this
    ExtractedField instance holds. Exists so 表5-2's grade CODE (e.g. "3")
    and grade TEXT (e.g. "普通") for the same factor can both be recorded
    without inventing a pseudo field_id suffix that would pollute the
    canonical, schemas/field_dictionary.json-aligned field_id namespace."""
    VALUE = "VALUE"                     # single-value field (表1/表4 existing convention)
    GRADE_TEXT = "GRADE_TEXT"           # 表5-2 優劣等級 text (優/稍優/普通/稍劣/劣)
    GRADE_CODE = "GRADE_CODE"           # 表5-2 優劣等級 numeric code -- evidence only
    ADJUSTMENT_PCT = "ADJUSTMENT_PCT"   # 表5-2 per-row 修正百分比 -- evidence only this round
    TOTAL = "TOTAL"                     # 影響地價區域因素總修正數


class ExtractionSubjectRole(str, Enum):
    """WHICH column a value came from, independent of extraction_role and
    of the canonical field_id (which never encodes this). 表1/表4's
    existing single-value fields have no base/comparable distinction, so
    they are NONE, not a guessed BASE."""
    BASE = "BASE"              # 比準地 column
    COMPARABLE = "COMPARABLE"  # 比較標的N column (see comparable_slot for which N)
    NONE = "NONE"              # no base/comparable distinction applies (表1/表4 VALUE fields)


class ExtractedField(BaseModel):
    """One field pulled from an uploaded document -- the atomic output
    unit of DocumentExtractionProvider.extract_fields(), deliberately
    mirroring the existing NormalizedDataPoint/Evidence provenance
    convention (source_document/confidence/requires_manual_review) rather
    than inventing a parallel schema. normalized_value is Optional[str]
    (not Decimal/float) -- normalization here means "strip 元/M2, %, full-
    width spaces" only; converting to a typed numeric value for
    calculation is CalculationEngine's job downstream, never this layer's.

    extraction_status/extraction_role/extraction_subject_role/
    comparable_slot are intentionally REQUIRED (not defaulted) -- this is
    a deliberate schema migration, not a silent additive convenience; see
    表5-2 Extraction Implementation-Ready Contract v3 for why."""
    model_config = ConfigDict(extra="forbid")

    field_id: str = Field(..., description="穩定英文field_id，對應schemas/field_dictionary.json")
    raw_text: str = Field(..., description="頁面上實際擷取到的原始文字，未經任何正規化")
    normalized_value: Optional[str] = Field(
        None, description="去除單位/全形空白等純文字正規化結果；None表示raw_text無法可靠正規化"
    )
    unit: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    page: int
    bounding_box: Optional[BoundingBox] = None
    extraction_method: ExtractionMethod
    source_document: str = Field(..., description="來源檔名或document_id")
    requires_manual_review: bool
    extraction_status: ExtractionStatus = Field(
        ..., description="row/cell層級的結構性擷取結果，與confidence/requires_manual_review正交"
    )
    extraction_role: ExtractionRole = Field(
        ..., description="同一canonical field_id下，此欄位代表哪一種數值（文字等級/代碼等級/修正百分比/總計/單一值）"
    )
    extraction_subject_role: ExtractionSubjectRole = Field(
        ..., description="此數值來自哪一欄（比準地/比較標的/無此區分），與field_id/extraction_role正交"
    )
    comparable_slot: Optional[int] = Field(
        ..., description="COMPARABLE時為第幾個比較標的欄位(1/2/3...)；BASE/NONE時必為None"
    )


class HumanConfirmationRecord(BaseModel):
    """Persisted human-in-the-loop decision for one ExtractedField.
    confirmed_value is None until a human actually confirms/corrects it --
    engine/human_confirmation.py enforces that a field whose extraction
    confidence is below threshold may not feed into deterministic rule
    judgment (RuleEngine/AdjustmentEngine/CalculationEngine) until this
    record exists with a non-None confirmed_value.

    extraction_role/extraction_subject_role/comparable_slot are REQUIRED
    alongside field_id -- once 表5-2 legitimately reuses one canonical
    field_id for BASE and every COMPARABLE slot, AND for both its
    GRADE_CODE and GRADE_TEXT roles simultaneously (see ExtractedField's
    docstring), field_id+subject_role+slot alone is STILL not a unique
    identity -- a BASE/GRADE_CODE confirmation and a BASE/GRADE_TEXT
    confirmation for the same factor would silently collide without
    extraction_role also in the key (found and fixed while wiring the
    Grade Representation Contract's Check A/B, which are the first
    consumers to need GRADE_CODE and GRADE_TEXT reconstructed
    simultaneously for the same field_id/BASE/None)."""
    model_config = ConfigDict(extra="forbid")

    field_id: str
    extraction_role: ExtractionRole
    extraction_subject_role: ExtractionSubjectRole
    comparable_slot: Optional[int]
    extracted_value: Optional[str] = None
    confirmed_value: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Phase API-2 — Official Facility Evidence (schools/stations/markets/parks).
#
# Same "external corroborating evidence, never a final determination"
# posture as ExpropriationCaseEvidence/LandPriceEvidence (Phase API-1):
# OfficialFacilityProvider answers "what/where/how far", never "what grade".
# Grade assignment stays with the existing deterministic Rule/Grade Engine
# (see data/rules/individual_rules.json's "接近學校之程度" / data/rules/
# regional_rules.json's "接近市場/公園/大型車站之程度" factors, which this
# round does NOT modify) -- this evidence supplies distance FACTS a human
# reviewer (or, unchanged, the existing engines fed by manually-confirmed
# form values) can use, nothing more.
#
# docs/phase2/distance_rules.md p.24 also documents an official rule this
# model must not violate: "同一細項有多個設施存在...則以對當地地價影響
# 最大者填寫...由查估人員判斷" -- i.e. when multiple facilities of the same
# type are found, picking which one matters is an EXPLICIT HUMAN JUDGMENT
# call per the manual itself, not simply "nearest wins". `matches` therefore
# always holds every candidate found (never silently narrowed to one), and
# `nearest` is offered only as a convenience pointer into `matches` -- never
# as a stand-in for the manual's required human "most impactful" judgment.
# ---------------------------------------------------------------------------

class FacilityType(str, Enum):
    """First batch only (Phase API-2 §0): the four facility categories with
    a direct, well-established relevance to 區域因素/個別因素接近條件 per
    the appraisal manual. MRT/rail STATION is folded into `STATION` (both
    come from the same verified source dataset, see providers/official_
    facility_provider.py's module docstring); bus stops are deliberately
    NOT a FacilityType here -- the manual's "大型車站"/接近條件"車站" is
    never satisfied by a bus stop (see that provider's module docstring for
    the live NTPC dataset audit this reflects)."""
    SCHOOL = "SCHOOL"
    MARKET = "MARKET"
    PARK = "PARK"
    STATION = "STATION"


class FacilityDistanceMethod(str, Enum):
    """Phase API-2.1 renamed from `GEODESIC_WGS84` (Phase API-2's original,
    now-recognized-as-imprecise name) to `HAVERSINE_WGS84`: the actual
    implementation (`engine.geo_distance_engine.GeoDistanceEngine.
    straight_line_distance`, FROZEN, unmodified) is specifically the
    Haversine great-circle formula on a WGS84-coordinate sphere -- "geodesic"
    is a broader term that can also refer to ellipsoidal (Vincenty-style)
    formulas this codebase does NOT implement, so the more precise name
    avoids overclaiming. Route/walking distance (OSRM) remains out of scope
    this round even though docs/phase2/distance_rules.md's manual excerpt
    suggests route distance for 通達-type facilities like schools/markets --
    see `FacilityDistanceSemantics` for the explicit "this is a reference
    distance, not a route distance" label this round adds."""
    HAVERSINE_WGS84 = "HAVERSINE_WGS84"
    UNKNOWN = "UNKNOWN"


class FacilityDistanceSemantics(str, Enum):
    """Phase API-2.1: explicit, unmistakable label for WHAT KIND of distance
    `distance_m` represents, independent of the computation method name
    above -- so a downstream reader can never mistake a straight-line
    number for a route/walking distance just because the manual (docs/
    phase2/distance_rules.md) suggests route distance for some facility
    types. This codebase does not compute route/walking distance for
    facilities at all this round (would require OSRM, out of scope) --
    `STRAIGHT_LINE_REFERENCE` is therefore the ONLY non-trivial value used
    today; `NOT_APPLICABLE` covers "no distance was computed at all"."""
    STRAIGHT_LINE_REFERENCE = "STRAIGHT_LINE_REFERENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CoordinateSourceType(str, Enum):
    """Phase API-2.1 Target Coordinate Provenance Audit (§2): every actual
    runtime path that can produce `ProviderContext.center_coordinate`,
    traced end-to-end from backend/handlers/collect_data.py's
    `_resolve_center_coordinate()` (not merely "does OfficialFacilityProvider
    itself import Nominatim" -- it does not, and never did, but that alone
    does not prove the COORDINATE flowing into it is official).

    Traced call chain: Input body/meta -> `_resolve_center_coordinate()` ->
    `ProviderContext.center_coordinate` (+ new `center_coordinate_evidence`,
    this round) -> `OfficialFacilityProvider.query_facility()`. Only THREE
    of the five values below are reachable at actual runtime today
    (SUBMITTED_BY_CALLER, NOMINATIM_EXTERNAL, UNKNOWN/None) -- OFFICIAL_GIS
    and DEMO_ONLY are enumerated because they are REAL possibilities this
    contract must be able to represent (a future official coordinate
    registry; providers/transportation_provider.py's `JINSHAN_DISTRICT_
    CENTROID`, which today is NEVER wired into `ctx.center_coordinate` at
    all -- it is used only by that module's own unused `demo_geo_engine_
    usage()` helper), not because they are live paths today. See
    docs/phase9/facility_evidence_pipeline.md §CENTER_COORDINATE_SOURCE_
    AT_RUNTIME for the full trace."""
    SUBMITTED_BY_CALLER = "SUBMITTED_BY_CALLER"
    NOMINATIM_EXTERNAL = "NOMINATIM_EXTERNAL"
    OFFICIAL_GIS = "OFFICIAL_GIS"
    DEMO_ONLY = "DEMO_ONLY"
    UNKNOWN = "UNKNOWN"


class CoordinateAuthoritativeStatus(str, Enum):
    """What `TargetCoordinateEvidence.authoritative_status` may honestly
    claim. Only `OFFICIAL` may ever combine with an official facility
    dataset to produce `FacilityEvidence.distance_authoritative_status =
    OFFICIAL` -- every other value forces `MIXED_SOURCE` (facility side is
    official, target side is not) or worse (see FacilityEvidence's
    docstring)."""
    OFFICIAL = "OFFICIAL"
    EXTERNAL_UNVERIFIED = "EXTERNAL_UNVERIFIED"
    DEMO_ONLY = "DEMO_ONLY"
    UNKNOWN = "UNKNOWN"


class TargetCoordinateEvidence(BaseModel):
    """Phase API-2.1 Coordinate Provenance Contract (§4): describes WHERE a
    query coordinate (e.g. `ProviderContext.center_coordinate`) actually
    came from, so `FacilityEvidence` can honestly distinguish "this
    distance combines an official facility coordinate with an official
    target coordinate" from "...with an unverified/external/demo one" --
    see `FacilityEvidence.distance_authoritative_status`.

    Deliberately NOT a large refactor of `ProviderContext`: added as one
    new optional field (`center_coordinate_evidence`) alongside the
    existing `center_coordinate`, populated by backend/handlers/
    collect_data.py at the exact point `_resolve_center_coordinate()`
    decides which branch fired -- every branch now tags its own
    provenance, rather than the coordinate arriving anonymously."""
    model_config = ConfigDict(extra="forbid")

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    crs: str = Field("EPSG:4326", description="本專案Coordinate model一律為WGS84；此欄位仍明列以求contract自我完整")
    source_type: CoordinateSourceType
    source_authority: Optional[str] = None
    source_url: Optional[str] = None
    authoritative_status: CoordinateAuthoritativeStatus
    precision_level: Optional[str] = Field(
        None, description="'PARCEL'/'SEGMENT'/'DISTRICT'/'CITY'/'UNKNOWN' 等，描述座標精細度，例如Nominatim對地名之geocode通常只達區/段層級，非地號層級"
    )
    retrieved_at: Optional[datetime] = None
    notes: Optional[str] = None


class CoordinateComparisonStatus(str, Enum):
    """Phase API-2.3F §5: when BOTH a submitted coordinate AND an official
    NLSC coordinate exist for the same case, this describes the purely
    geometric/numeric relationship between them -- it NEVER adjudicates
    which one is "correct" for valuation purposes (this round's explicit
    "不得讓LLM決定哪一個正確" instruction), and this round deliberately
    does NOT invent a legal/valuation materiality threshold (a domain/
    policy decision outside this round's authority).

    - `MATCH`: coordinate_delta_m is within COORDINATE_IDENTITY_EPSILON_M
      -- a tiny (1 meter) numerical-identity tolerance for GPS/CRS
      rounding noise, NOT a claim that "this much difference doesn't
      matter for the appraisal".
    - `DIFFERENT`: the two coordinates are numerically distinct points
      beyond that tolerance -- a plain geometric fact, not a judgment
      about which source is right or whether the gap is significant.
    - `MANUAL_REVIEW_REQUIRED`: the comparison itself could not be
      reliably computed (e.g. one side's evidence claims to exist but
      carries an invalid/missing latitude or longitude) -- distinct from
      DIFFERENT, which requires both values to have been successfully
      compared."""
    MATCH = "MATCH"
    DIFFERENT = "DIFFERENT"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class CoordinateComparisonEvidence(BaseModel):
    """Phase API-2.3F §3/§5: SUBMITTED_COORDINATE_EVIDENCE and
    OFFICIAL_PARCEL_COORDINATE_EVIDENCE are two INDEPENDENT evidences that
    must never overwrite each other (this round's explicit correction of
    Phase API-2.3H's "submitted coordinate存在→完全不查NLSC" design,
    which is inappropriate for a review system that should surface a
    discrepancy, not hide it by skipping the official lookup). This model
    is the third, DERIVED evidence produced only when both exist --
    coordinate_delta_m is computed via the FROZEN engine/geo_distance_
    engine.py's GeoDistanceEngine.straight_line_distance() (Haversine),
    reused unmodified, never a bespoke distance formula."""
    model_config = ConfigDict(extra="forbid")

    submitted_latitude: Optional[float] = None
    submitted_longitude: Optional[float] = None
    official_latitude: Optional[float] = None
    official_longitude: Optional[float] = None
    coordinate_delta_m: Optional[float] = None
    status: CoordinateComparisonStatus = CoordinateComparisonStatus.MANUAL_REVIEW_REQUIRED
    requires_manual_review: bool = True
    notes: Optional[str] = None
    computed_at: Optional[datetime] = None


class FacilityCoordinateStatus(str, Enum):
    """Per-facility coordinate provenance -- see facility_dataset_cache.py's
    module docstring for which source dataset lands in which state.
    Deliberately three-state, mirroring this codebase's other never-
    collapse-to-a-boolean status enums: a caller must be able to tell
    "usable WGS84 coordinate" apart from "source only ever had an address"
    (ADDRESS_ONLY) apart from "source had SOME coordinate field but its CRS
    could not be confirmed" (CRS_UNKNOWN, never guessed/assumed as WGS84)."""
    WGS84 = "WGS84"
    ADDRESS_ONLY = "ADDRESS_ONLY"
    CRS_UNKNOWN = "COORDINATE_CRS_UNKNOWN"


class FacilityMatch(BaseModel):
    """One candidate facility found for a query -- see FacilityEvidence's
    docstring for why this is always a LIST, never a single "the" answer.

    `facility_subtype`/`source_category` (Phase API-2.1 §10, additive):
    preserve the SOURCE dataset's own original category string (e.g.
    "國民小學"/"國民中學"/"完全中學"/"高中職"/"大專院校" under FacilityType.
    SCHOOL; "捷運站"/"火車站" under FacilityType.STATION) -- normalizing
    into a coarse FacilityType must never silently discard which specific
    kind of school/station this is; a reviewer or future rule needing that
    distinction can read it here instead of it being lost at normalization
    time. `facility_subtype` is this project's own coarser bucket (e.g.
    "MRT"/"TRA" for STATION) when one is defined; `source_category` is
    ALWAYS the verbatim original value, even if `facility_subtype` is None
    for a source category with no defined bucket yet."""
    model_config = ConfigDict(extra="forbid")

    facility_id: Optional[str] = None
    facility_type: FacilityType
    facility_subtype: Optional[str] = Field(None, description="例如STATION的'MRT'/'TRA'/'HSR'/'BUS_TERMINAL'；未定義bucket時為None")
    source_category: Optional[str] = Field(None, description="來源官方資料集之原始分類字串，逐字保留，例如'國民小學'/'捷運站'")
    name: str
    address: Optional[str] = None
    district: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    coordinate_status: FacilityCoordinateStatus
    distance_m: Optional[float] = Field(
        None, description="僅當coordinate_status=WGS84且query_coordinate存在時才會有值；ADDRESS_ONLY/CRS_UNKNOWN恆為None，絕不臆測"
    )
    source_dataset_id: Optional[str] = None
    source_authority: Optional[str] = None


class FacilityEvidence(BaseModel):
    """One query result against an official NTPC facility dataset.
    `requires_manual_review` is HARDCODED True (see field default) for the
    same reason as ExpropriationCaseEvidence -- this is corroborating
    distance/location EVIDENCE, never a grade or adjustment-rate decision
    (Phase API-2 §8's explicit instruction: Distance Evidence ≠ Grade).

    `matches` holds EVERY facility found for `facility_type` (optionally
    scoped by `district`) -- never narrowed to "the nearest one" at this
    layer, because the appraisal manual (docs/phase2/distance_rules.md
    p.24) requires a HUMAN to pick "the one most impactful to local land
    value" when multiple exist, not an automatic nearest-wins rule.
    `nearest` is a convenience pointer (the entry in `matches` with the
    smallest non-None `distance_m`, or None if no match has a computable
    distance) -- it is NOT the manual's required "most impactful" choice,
    and callers must not treat it as such without human confirmation
    (`requires_manual_review` stays True regardless).

    `query_coordinate` being None means distance could not be computed for
    ANY match regardless of the dataset's own coordinate availability (see
    TARGET_COORDINATE_UNAVAILABLE in docs/phase9/facility_evidence_pipeline.
    md) -- this is distinct from a match's own `coordinate_status` being
    ADDRESS_ONLY (the FACILITY has no coordinate) or CRS_UNKNOWN.

    `nearest` is a DISTANCE CONVENIENCE POINTER ONLY (Phase API-2.1 §8,
    reaffirming Phase API-2's original design after re-checking the field
    naming does not overclaim): it is never
    `selected_appraisal_facility` and must never be read as one. The
    appraisal manual's "同一細項多設施時，對當地地價影響最大者填寫...由
    查估人員判斷" requirement is satisfied by `matches` retaining EVERY
    candidate, not by `nearest` making that judgment call automatically.

    `target_coordinate_evidence`/`distance_authoritative_status`/
    `official_distance_ready` (Phase API-2.1 §3-5, additive): a FACILITY's
    own coordinate being official (`coordinate_status=WGS84`, sourced from
    NTPC OpenData) does NOT make the resulting DISTANCE official -- the
    QUERY coordinate matters just as much. `distance_authoritative_status`
    is computed as:
      - no distance computed at all (no match has `distance_m`) -> "UNKNOWN"
      - a distance WAS computed AND `target_coordinate_evidence.
        authoritative_status == OFFICIAL` -> "OFFICIAL"
      - a distance WAS computed but the target coordinate's provenance is
        anything else (EXTERNAL_UNVERIFIED/DEMO_ONLY/UNKNOWN) -> "MIXED_
        SOURCE" (facility side official, target side not) -- NEVER silently
        reported as "OFFICIAL" just because `query_coordinate` is non-None.
    `official_distance_ready` is `True` only when `distance_authoritative_
    status == "OFFICIAL"` -- this is the field Golden Case must answer NO
    on (no official parcel-level coordinate exists for it -- see
    docs/phase9/facility_evidence_pipeline.md §5), even in the (currently
    unreachable at runtime) case where some other coordinate happened to be
    supplied.

    `search_coverage` (Phase API-2.2 §3, additive): the synced NTPC facility
    dataset covers 新北市 (New Taipei City) ONLY -- it has no rows at all
    for 台北/基隆/桃園 or any other jurisdiction. This means `nearest` can
    NEVER be truthfully described as the "absolute nearest" facility in
    reality -- only the nearest one WITHIN this dataset's coverage. A parcel
    near a city boundary could have a genuinely closer school or station
    just across that boundary that this Provider structurally cannot see.
    `nearest`/`matches` therefore mean **nearest_within_dataset_coverage**,
    never **nearest_global** -- `search_coverage` states this explicitly
    (currently always `"NEW_TAIPEI_CITY"`, since only NTPC OpenData is
    synced this round) so a caller reading `nearest` alone still has the
    coverage caveat attached, without requiring a schema-breaking rename of
    the existing `nearest`/`matches` field names (backward compatible)."""
    model_config = ConfigDict(extra="forbid")

    facility_type: FacilityType
    query_coordinate: Optional[Coordinate] = None
    query_district: Optional[str] = None
    search_coverage: str = Field(
        "NEW_TAIPEI_CITY",
        description="此次查詢實際涵蓋之地理範圍——nearest/matches僅代表此範圍內之最近設施（nearest_within_dataset_coverage），絕非全域最近（nearest_global）",
    )
    target_coordinate_evidence: Optional[TargetCoordinateEvidence] = None
    matches: List[FacilityMatch] = Field(default_factory=list)
    nearest: Optional[FacilityMatch] = None
    match_count: int = 0
    distance_method: FacilityDistanceMethod = FacilityDistanceMethod.UNKNOWN
    distance_semantics: FacilityDistanceSemantics = FacilityDistanceSemantics.NOT_APPLICABLE
    distance_authoritative_status: str = Field(
        "UNKNOWN", description="'OFFICIAL' | 'MIXED_SOURCE' | 'UNKNOWN' -- 見本model docstring之判定規則"
    )
    official_distance_ready: bool = False
    dataset_id: Optional[str] = None
    dataset_name: Optional[str] = None
    dataset_version: Optional[str] = Field(None, description="snapshot_meta.downloaded_at，作為此份資料之版本標記")
    source_authority: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    checksum: Optional[str] = None
    source_record_count: Optional[int] = Field(None, description="來源官方資料集原始總筆數（未篩選）")
    stored_record_count: Optional[int] = Field(None, description="本地snapshot中屬於此facility_type之筆數（normalize後）")
    record_count: Optional[int] = Field(
        None, description="DEPRECATED別名，等同stored_record_count，保留供既有讀者相容；新讀者請改用stored_record_count"
    )
    authoritative_status: Optional[str] = Field(
        None, description="'OFFICIAL_OPEN_DATA' 等；ADDRESS_ONLY類設施會標註涵蓋限制"
    )
    confidence: str = Field("UNKNOWN", description="'高'/'中'/'低'/'UNKNOWN'")
    requires_manual_review: bool = True
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase API-2.3 — Official Parcel Coordinate (NLSC CAD_001, auth-gated).
#
# Two-stage evidence, mirroring the two-stage design confirmed feasible by
# Phase API-2.2R2's live audit:
#   1. IDENTIFIER RESOLUTION (district/section name -> official NLSC codes)
#      via `NlscSectionCodeEvidence` -- uses ONLY the three CONFIRMED-OPEN,
#      no-auth code services (ListCounty/ListTown/ListLandSection, see
#      providers/nlsc_code_cache.py). This stage can genuinely succeed
#      today, with zero credentials, because those three services really
#      are open.
#   2. PARCEL POSITION LOOKUP (resolved codes -> a coordinate) via
#      `OfficialParcelCoordinateEvidence` -- uses CAD_001 (CadasMapPosition),
#      which per Phase API-2.2R2's audit DOES require an NLSC application/
#      credential. This stage is Auth-Gated: without a real, non-guessed
#      credential (see providers/official_parcel_coordinate_provider.py's
#      module docstring for the feature-flag + config contract), it MUST
#      resolve to AUTH_REQUIRED, never to a fabricated coordinate and never
#      to a silent Nominatim/demo substitute reported as if it were this
#      evidence's own success.
# ---------------------------------------------------------------------------

class SectionCodeMatchStatus(str, Enum):
    """Outcome of matching a (city_name, district_name, section_name) triple
    against NLSC's official ListCounty/ListTown/ListLandSection code
    services. Deliberately three-state (never collapsed to found/not-found):
    `AMBIGUOUS` exists because ListLandSection can legitimately return
    multiple rows whose `sectstr` matches the same normalized section name
    (e.g. two land offices administering sections with an identical name) --
    per this round's explicit "禁止fuzzy first-match" instruction, more than
    one exact-normalized-match candidate is NEVER auto-resolved to "the
    first one"; it is reported as AMBIGUOUS with every candidate row
    preserved, same principle as Phase API-1.9's multi-match expropriation
    contract.

    `OUT_OF_COVERAGE` (Phase API-2.3H §7): ListLandSection has only ever
    been synced for 新北市's 29 towns (see providers/nlsc_code_cache.py's
    SECTION_SNAPSHOT_COVERAGE) -- a section lookup for any OTHER county
    returning 0 rows does NOT mean "NLSC has no such section" (that would
    require actually having synced that county's sections and finding
    nothing), it means this codebase never synced that county's section
    list at all. Collapsing that into NOT_FOUND would misrepresent a
    deliberate sync-scope gap as an official negative result -- exactly
    the "API response異常 ≠ 官方查無宗地" risk this round audits."""
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_COVERAGE = "OUT_OF_COVERAGE"


class NlscSectionCodeEvidence(BaseModel):
    """Result of resolving district/section names to NLSC's official
    county/town/section codes, via the three services CONFIRMED live and
    open with zero authentication (Phase API-2.2R2): ListCounty (COM_003),
    ListTown (COM_004), ListLandSection (COM_006). `office`/`section_code`
    are the exact field names NLSC's own ListLandSection response uses
    (`office`/`sectcode`) -- preserved verbatim, not renamed, so a reader
    cross-checking against the raw API response is never confused about
    which field is which.

    Golden Case (this round, live-verified, not fabricated):
    新北市 -> county_code="F", 金山區 -> town_code="F25",
    金美段 -> section_code="1027" (office="FD", i.e. 汐止地政事務所 --
    a real, non-obvious fact: the land office administering 金山區's
    sections is 汐止's, not a "金山" office, per the live ListLandSection
    response itself)."""
    model_config = ConfigDict(extra="forbid")

    city_name: Optional[str] = None
    city_code: Optional[str] = None
    district_name: Optional[str] = None
    town_code: Optional[str] = None
    section_name: Optional[str] = None
    section_code: Optional[str] = None
    office: Optional[str] = Field(None, description="NLSC地政事務所代碼，來自ListLandSection之office欄位，逐字保留")
    match_status: SectionCodeMatchStatus = SectionCodeMatchStatus.NOT_FOUND
    candidates: List[Dict[str, Optional[str]]] = Field(
        default_factory=list,
        description="match_status=AMBIGUOUS時，保留全部原始候選row（office/sectcode/sectstr），供人工判斷，絕不自動擇一",
    )
    source_service: Optional[str] = Field(None, description="'ListCounty'/'ListTown'/'ListLandSection' 等")
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    authoritative_status: Optional[str] = Field(None, description="'OFFICIAL_OPEN_DATA' -- 三個開放API皆為NLSC官方系統本體")
    requires_manual_review: bool = True
    notes: Optional[str] = None


class ParcelCoordinateStatus(str, Enum):
    """CAD_001 (CadasMapPosition) query outcome -- deliberately distinguishes
    WHY a coordinate is unavailable, never collapsing "we don't have
    permission to ask" (AUTH_REQUIRED) with "we asked and there is no such
    parcel" (NOT_FOUND) or "the request itself was malformed" (INVALID_
    REQUEST). Phase API-2.3's Auth Gate (see providers/official_parcel_
    coordinate_provider.py) means AUTH_REQUIRED/AUTH_CONTRACT_UNVERIFIED
    are the only reachable non-UNKNOWN/non-OUT_OF_COVERAGE statuses in this
    round's actual runtime environment (no real NLSC credential exists) --
    SUCCESS/SERVICE_UNAVAILABLE/INVALID_REQUEST/UNVERIFIED_RESPONSE/
    EMPTY_RESPONSE/PARSE_FAILED are exercised only via response-parser unit
    tests against documented-example fixtures, never against the live API.

    Phase API-2.3H additions (§3, §5, §6, §7) -- NLSC's own technical
    documentation (docs/phase9/official_parcel_coordinate_audit.md) never
    documents what a "parcel not found" response actually looks like for
    CAD_001 (CAD001_DOCUMENTED_NOT_FOUND_RESPONSE=UNCONFIRMED), so this
    round removes the earlier, overclaiming inference that HTTP 404 / an
    empty HTTP 200 body / a parsed-but-fields-missing XML response means
    "官方查無宗地". `NOT_FOUND` is kept in this enum for forward
    compatibility (once NLSC's actual no-result response IS documented)
    but is UNUSED by this codebase's parser today -- see
    parse_cadas_map_position_response()'s module-level docstring.

    - `AUTH_CONTRACT_UNVERIFIED`: NLSC_CAD_API_ENABLED=true and credentials
      are present, but CAD001_AUTH_CONTRACT_STATUS (a source-code constant,
      not an env var) is not "VERIFIED" -- i.e. this codebase still has no
      documented basis for what CAD_001's actual runtime auth transport is
      (see official_parcel_coordinate_provider.py's module docstring). This
      status is distinct from AUTH_REQUIRED (which means "feature flag off"
      or "credentials missing") precisely so a reviewer can tell "we chose
      not to call" apart from "we don't even know how a real call would
      authenticate".
    - `UNVERIFIED_RESPONSE`: got an HTTP response NLSC's own documentation
      does not define a confident interpretation for (e.g. HTTP 404, or a
      well-formed XML response missing the documented repX/repY fields).
    - `EMPTY_RESPONSE`: HTTP 200 with a blank/empty body.
    - `PARSE_FAILED`: the response body could not be parsed as XML at all.
    - `OUT_OF_COVERAGE`: identifier resolution stopped at
      SectionCodeMatchStatus.OUT_OF_COVERAGE (county outside the synced
      NlscCodeCache section snapshot) -- never a coordinate query outcome
      per se, but surfaced here so callers see one unified status field."""
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_CONTRACT_UNVERIFIED = "AUTH_CONTRACT_UNVERIFIED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNVERIFIED_RESPONSE = "UNVERIFIED_RESPONSE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    PARSE_FAILED = "PARSE_FAILED"
    OUT_OF_COVERAGE = "OUT_OF_COVERAGE"
    UNKNOWN = "UNKNOWN"


class ParcelCoordinateSemantics(str, Enum):
    """What CAD_001's repX/repY actually represent, per Phase API-2.2R2's
    live audit of NLSC's own technical documentation (CadasMapPosition.jsp):
    the official text says only "代表點X"/"代表點Y" ("representative point
    X/Y") -- NEVER "centroid"/"質心"/"形心". This enum exists specifically
    so no code path can name or imply CENTROID without NLSC itself using
    that word; `OFFICIAL_PARCEL_REPRESENTATIVE_POINT` is the only non-
    UNKNOWN value defined, deliberately with no CENTROID member to rename
    into by mistake."""
    OFFICIAL_PARCEL_REPRESENTATIVE_POINT = "OFFICIAL_PARCEL_REPRESENTATIVE_POINT"
    UNKNOWN = "UNKNOWN"


class OfficialParcelCoordinateEvidence(BaseModel):
    """One CAD_001 (CadasMapPosition) query result. `latitude`/`longitude`
    are ALWAYS WGS84 (EPSG:4326) once populated -- normalized at query time
    if the source response used EPSG:3826 (see `source_crs`/`source_
    coordinate_x`/`source_coordinate_y`, which preserve the RAW, pre-
    normalization values verbatim and are never overwritten, per this
    round's explicit "不得覆蓋原值" instruction).

    `status=AUTH_REQUIRED` is the expected, correct outcome in this round's
    actual environment (no real NLSC CAD_001 credential exists -- see
    providers/official_parcel_coordinate_provider.py's Auth Gate). Even in
    that case, `county_code`/`town_code`/`section_code`/`nlsc_land_no` are
    still populated whenever identifier resolution succeeded -- proving a
    failure happened at the AUTHORIZATION layer, not the IDENTIFIER
    RESOLUTION layer (this round's explicit distinction)."""
    model_config = ConfigDict(extra="forbid")

    status: ParcelCoordinateStatus = ParcelCoordinateStatus.UNKNOWN

    city: Optional[str] = None
    district: Optional[str] = None
    section_name: Optional[str] = None
    land_no_raw: Optional[str] = None

    county_code: Optional[str] = None
    town_code: Optional[str] = None
    section_code: Optional[str] = None
    nlsc_land_no: Optional[str] = Field(None, description="NlscLandNumberEncoder輸出之8碼格式，例如'04890000'")

    latitude: Optional[float] = Field(None, description="WGS84緯度，恆為正規化後之值")
    longitude: Optional[float] = Field(None, description="WGS84經度，恆為正規化後之值")
    source_coordinate_x: Optional[float] = Field(None, description="CAD_001原始repX，未經任何轉換，絕不覆寫")
    source_coordinate_y: Optional[float] = Field(None, description="CAD_001原始repY，未經任何轉換，絕不覆寫")
    source_crs: Optional[str] = Field(None, description="'EPSG:4326' 或 'EPSG:3826'，CAD_001請求時實際指定/預設之CRS")
    coordinate_semantics: ParcelCoordinateSemantics = ParcelCoordinateSemantics.UNKNOWN

    source_service: str = Field("CadasMapPosition", description="固定為CAD_001之官方服務名稱")
    api_code: str = Field("CAD_001", description="固定為NLSC官方功能編號")
    source_authority: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None

    authoritative_status: Optional[str] = Field(
        None, description="status=SUCCESS時為'OFFICIAL'；否則為None或說明性字串，絕不在非SUCCESS時標OFFICIAL"
    )
    confidence: str = Field("UNKNOWN", description="'高'/'中'/'低'/'UNKNOWN'")
    requires_manual_review: bool = True
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Case-Scoped Rule Architecture (docs/audit/CASE_SCOPED_RULE_ARCHITECTURE_
# REPORT.md). Lets one case use a confirmed, competition-day evaluation
# standard WITHOUT mutating the static data/rules/regional_rules.json /
# individual_rules.json baseline that every other case (including the
# Golden Case) keeps relying on. RuleEngine/GradeEngine/AdjustmentEngine
# themselves are untouched -- a CaseRulePackage's regional_rules/
# individual_rules are plain rule_schema.json-shaped dicts, identical in
# shape to what those two static files already contain, merged in by
# engine/case_rule_resolution.py::build_rule_engine_for_case() at request
# time rather than by inventing a second rule format or a second engine.
# ---------------------------------------------------------------------------

class RuleSourceType(str, Enum):
    """Attached per-rule_id by build_rule_engine_for_case() (never inside
    RuleEngine/GradeEngine themselves) so every Grade/Adjustment result can
    be traced back to whether it came from the static baseline or a
    human-CONFIRMED case-scoped package -- see CaseRuleResolution.trace_by_
    rule_id below."""
    STATIC_LOCAL = "STATIC_LOCAL"
    CASE_IMPORTED_CONFIRMED = "CASE_IMPORTED_CONFIRMED"


class CaseRulePackageStatus(str, Enum):
    """DRAFT/EXTRACTED/PARTIAL/AMBIGUOUS/REJECTED can NEVER reach
    RuleEngine -- only a package that has reached CONFIRMED via
    CaseRuleRepository.confirm() (which itself re-runs engine.
    rule_table_validator.RuleTableValidator and refuses to confirm if any
    ERROR-severity issue exists) may be resolved into a live RuleEngine.
    See CONFIRMED Gate in the case-scoped rule architecture report.
    AMBIGUOUS (added for the Evaluation Standard Importer, see
    docs/audit/EVALUATION_STANDARD_IMPORTER_PHASE3A_REPORT.md) marks a
    package whose extraction could not confidently resolve a factor name
    or matrix -- distinct from PARTIAL (some rows/scopes cleanly resolved,
    others didn't) and EXTRACTED (fully clean, still not human-confirmed)."""
    DRAFT = "DRAFT"
    EXTRACTED = "EXTRACTED"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class RuleFieldEdit(BaseModel):
    """One append-only audit-log entry for a human edit to a single field
    of a single rule record within a CaseRulePackage (docs/audit/
    EVALUATION_STANDARD_HUMAN_CONFIRMATION_PHASE3B_REPORT.md §3). Never
    overwritten or deleted -- CaseRulePackage.edit_history is append-only,
    so a rule record's confirmed value can always be traced back to
    exactly what the Importer originally extracted and who changed it."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(..., description="編輯對象規則列（或'candidate:<candidate_id>'代表factor mapping類編輯）")
    field: str = Field(..., description="被編輯的欄位名稱，例如grade_label/lower_bound/adjustment_matrix/applicability")
    original_extracted_value: Any = None
    confirmed_value: Any = None
    edited: bool = True
    confirmed_by: Optional[str] = None
    confirmed_at: datetime


class CaseRulePackage(BaseModel):
    """One candidate (or confirmed) set of case-scoped evaluation-standard
    rules, scoped to a single case_id so Case A's rules can never leak into
    Case B's grading (see Isolation Strategy in the architecture report).

    `regional_rules`/`individual_rules` are plain lists of rule_schema.json
    -shaped dicts -- the SAME shape already used by data/rules/regional_
    rules.json['rules'] / individual_rules.json['rules'] and already
    produced by engine/rule_table_ingest.py::build_rules_from_csv(). This
    is a DELIBERATE reuse, not a new rule format: RuleEngine.__init__()
    takes exactly this shape today.

    Only one CONFIRMED package may exist per case_id at a time (enforced by
    CaseRuleRepository.confirm(), not by this model) -- confirming a
    replacement requires first rejecting the previously-confirmed package,
    so there is never an ambiguous "which CONFIRMED package applies" state."""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    package_id: str
    rule_version: str = Field(..., description="人工指定之版本標籤，例如'2026-competition-day-v1'")

    source_document: str = Field(..., description="來源檔名或document_id，供traceability使用")
    source_sha256: Optional[str] = Field(None, description="來源文件SHA-256，若可取得")
    source_type: str = Field(..., description="自由文字，例如'MANUAL_CSV_INGEST'/'HUMAN_TRANSCRIPTION'")

    status: CaseRulePackageStatus = CaseRulePackageStatus.DRAFT

    regional_rules: List[Dict[str, Any]] = Field(default_factory=list)
    individual_rules: List[Dict[str, Any]] = Field(default_factory=list)

    created_at: datetime
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejection_reason: Optional[str] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    edit_history: List[RuleFieldEdit] = Field(default_factory=list)


class CaseRuleTraceInfo(BaseModel):
    """Per-rule_id provenance, produced by build_rule_engine_for_case() and
    kept OUTSIDE RuleEngine/GradeEngine/domain.models.RuleResult (neither is
    modified by this architecture) -- callers look this up by the rule_id
    that RuleResult/AdjustmentResult already carry."""
    model_config = ConfigDict(extra="forbid")

    rule_source_type: RuleSourceType
    case_id: Optional[str] = None
    package_id: Optional[str] = None
    source_document: Optional[str] = None
    source_sha256: Optional[str] = None
    rule_version: Optional[str] = None


class CaseRuleResolution(BaseModel):
    """Returned by build_rule_engine_for_case() alongside the RuleEngine
    instance itself (the RuleEngine is NOT a pydantic model and is not part
    of this object). `resolution_status` distinguishes the three cases
    Failure Semantics requires callers to tell apart:
      STATIC_LOCAL            -- no case package at all, or none CONFIRMED
                                  yet (normal baseline; NOT an error)
      CASE_IMPORTED_CONFIRMED -- a CONFIRMED package supplied at least one
                                  scope's rules and is in effect
      CASE_RULE_NOT_CONFIRMED -- a package exists for this case but no
                                  version of it has ever been CONFIRMED
                                  (static baseline still used; warning only)
    CASE_RULE_INVALID is deliberately NOT a resolution_status value -- an
    invalid CONFIRMED package makes build_rule_engine_for_case() raise
    CaseRulePackageInvalidError instead of returning, so no caller can
    mistake it for a successful resolution."""
    model_config = ConfigDict(extra="forbid")

    case_id: Optional[str] = None
    resolution_status: str = Field(..., description="'STATIC_LOCAL' | 'CASE_IMPORTED_CONFIRMED' | 'CASE_RULE_NOT_CONFIRMED'")
    package_id: Optional[str] = None
    trace_by_rule_id: Dict[str, CaseRuleTraceInfo] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AI Semantic Fallback (docs/audit/AI_SEMANTIC_FALLBACK_PHASE3C_REPORT.md).
# Only ever ADVISORY: a SemanticRuleMappingCandidate is a proposal a human
# reviewer sees in the Review DTO and may accept (via the SAME Phase 3B
# CaseRuleRepository.resolve_candidate_factor_mapping()/submit_human_edits()
# a human-typed correction would use) or ignore -- nothing in this module
# ever writes directly into CaseRulePackage.regional_rules/individual_rules.
# ---------------------------------------------------------------------------

class SemanticRuleMappingCandidate(BaseModel):
    """The ONLY fields a SemanticRuleMappingProvider may populate (Phase 3C
    §2's explicit output contract). No grade/rank/adjustment_rate/price/
    legal_conclusion field exists on this model AT ALL -- not merely
    validated-against, structurally absent, so no code path can accidentally
    forward one even if a provider's raw response smuggled it in before
    validation (validate_ai_response_schema() in providers/semantic_rule_
    mapping_provider.py runs on the RAW dict first and rejects any such
    response outright, but this model is the second, structural line of
    defense: there is no field here to assign it to)."""
    model_config = ConfigDict(extra="forbid")

    candidate_factor_id: Optional[str] = None
    candidate_condition_id: Optional[str] = None
    normalized_condition_candidate: Optional[str] = None
    candidate_unit: Optional[str] = None
    candidate_value_type: Optional[str] = None
    confidence: str = Field(..., description="'HIGH' | 'MEDIUM' | 'LOW' -- affects ONLY human review priority, never auto-confirmation")
    reason: str


class SemanticRuleMappingResult(BaseModel):
    """Full provenance envelope around one AI proposal attempt (§5) --
    persisted verbatim in CaseRulePackage.metadata['ai_semantic_candidates']
    (keyed by candidate_id), independent of and never overwritten by
    whatever a human later decides via edit_history (Phase 3B)."""
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    original_text: str
    deterministic_failure_reason: List[str] = Field(default_factory=list, description="The Importer issue codes that triggered AI (UNKNOWN_FACTOR/RULE_EXTRACTION_AMBIGUOUS/...)")
    provider: str = Field(..., description="'MOCK' | 'BEDROCK'")
    model_id: Optional[str] = None
    prompt_version: str
    status: str = Field(..., description="'OK' | 'SCHEMA_INVALID' | 'SCOPE_VIOLATION' | 'PROVIDER_UNAVAILABLE'")
    candidate: Optional[SemanticRuleMappingCandidate] = None
    error_message: Optional[str] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Competition Case State (STEP 5 §2). Tracked SEPARATELY from every stage's
# own record (META/FACTORS/case_rule_package/FORM_COMPLETION/REVIEW_RESULT)
# via case_store's generic put_record/get_record under SK="COMPETITION_STATE"
# -- this module adds NO new persistence mechanism and touches no Core
# Freeze engine; it is a pure tracking/reporting layer a
# CompetitionOrchestrator (or any handler) updates as each real stage
# completes, never a second source of truth for what those stages computed.
# ---------------------------------------------------------------------------

class CompetitionLifecycleStage(str, Enum):
    """The suggested STEP5 §2 lifecycle. Strictly forward-moving under
    normal progress (CompetitionCaseState.advance() enforces this -- see
    backend/handlers/competition_state.py), but ANY stage may instead
    transition straight to MANUAL_REVIEW_REQUIRED or FAILED on a blocking
    condition -- overall_status must never claim COMPLETE/PDF_READY when a
    blocking issue exists, per §2's explicit "不得假裝COMPLETE"."""
    CREATED = "CREATED"
    DOCUMENTS_UPLOADED = "DOCUMENTS_UPLOADED"
    APPRAISAL_EXTRACTED = "APPRAISAL_EXTRACTED"
    EVALUATION_STANDARD_EXTRACTED = "EVALUATION_STANDARD_EXTRACTED"
    RULE_REVIEW_REQUIRED = "RULE_REVIEW_REQUIRED"
    RULE_CONFIRMED = "RULE_CONFIRMED"
    DATA_COLLECTED = "DATA_COLLECTED"
    ANALYZED = "ANALYZED"
    FORMS_COMPLETED = "FORMS_COMPLETED"
    REVIEWED = "REVIEWED"
    PDF_READY = "PDF_READY"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    FAILED = "FAILED"


class CompetitionCaseState(BaseModel):
    """Everything STEP5 §2 requires a Competition Case to be able to report
    about its own progress. Every *_status field is a free-form string
    (not itself a new enum per field) so each stage can report whatever
    status vocabulary that stage's OWN handler already uses today
    (rule_resolution_status's 'STATIC_LOCAL'/'CASE_IMPORTED_CONFIRMED'/
    'CASE_RULE_NOT_CONFIRMED', CaseRulePackageStatus's own values, etc.) --
    this model does not invent a second status taxonomy to keep in sync
    with those."""
    model_config = ConfigDict(extra="forbid")

    case_id: str

    appraisal_document_id: Optional[str] = None
    appraisal_extraction_status: Optional[str] = None

    evaluation_standard_document_id: Optional[str] = None
    evaluation_standard_package_id: Optional[str] = None
    evaluation_standard_status: Optional[str] = None

    case_rule_status: Optional[str] = None

    collect_data_status: Optional[str] = None
    analyze_status: Optional[str] = None
    complete_form_status: Optional[str] = None
    review_status: Optional[str] = None
    pdf_status: Optional[str] = None

    overall_status: CompetitionLifecycleStage = CompetitionLifecycleStage.CREATED
    manual_review_required: bool = False
    blocking_issues: List[str] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class FacilityConfirmationStatus(str, Enum):
    """FACILITY-CONFIRMATION-GATE-1. Mirrors CaseRulePackageStatus's own
    "only confirm()/reject() ever mutate status" discipline (see
    backend/handlers/facility_confirmation_repository.py's CONFIRMED
    Gate) -- a raw provider/system-recommended candidate is ALWAYS
    PENDING until a human reviewer acts on it; no provider, collect_data,
    or the PDF renderer may ever create a record already CONFIRMED."""
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class FacilityCandidate(BaseModel):
    """One system-recommended facility candidate for an official Table1
    facility row (utility/funeral/major_station subtypes) -- NEVER itself
    an official selection. Derived mechanically from FACTORS' own
    already-collected provider evidence (FACTORS.points for utility/
    funeral, FACTORS.official_facility_evidence for major_station), never
    re-queried or re-derived from a name string or a new guess. See
    backend/handlers/facility_confirmation_repository.py::derive_candidates().

    `selection_basis` is an honesty label, not a safety claim (TABLE1-
    MOST-IMPACTFUL-CROSS-CUTTING-AUDIT's finding that "nearest" is not
    verified to equal the official manual's "most impactful" facility
    for any of these subtypes) -- NEAREST_PROVIDER_RESULT (utility/
    funeral: the provider already collapsed to a single nearest result
    internally, before this candidate was even derived) or
    NEAREST_VALID_DISTANCE (major_station: selected among government
    facility_subtype=="MRT"/"TRA" matches by smallest verified positive
    distance_m). Never "MOST_IMPACTFUL" or "OFFICIAL_CONFIRMED" -- no
    code path in this system is authorized to claim either."""
    model_config = ConfigDict(extra="forbid")

    facility_type: str = Field(description="'utility' / 'funeral' / 'major_station'")
    facility_subtype: str = Field(description="substation/gas_tank/cemetery/funeral_home/crematorium/columbarium/MRT/TRA")
    name: Optional[str] = None
    distance_m: Optional[float] = None
    source: Optional[str] = None
    source_type: Optional[str] = Field(None, description="'Mock'/'API'(OSM)/'GovernmentOpenData' -- never invented, always copied verbatim from the underlying NormalizedDataPoint/FacilityMatch")
    source_url: Optional[str] = None
    dataset_id: Optional[str] = None
    confidence: Optional[str] = None
    retrieved_at: Optional[str] = None
    provenance_notes: Optional[str] = None
    selection_basis: str = Field(description="NEAREST_PROVIDER_RESULT | NEAREST_VALID_DISTANCE -- never MOST_IMPACTFUL or OFFICIAL_CONFIRMED")


class FacilityConfirmationRecord(BaseModel):
    """FACILITY-CONFIRMATION-GATE-1's CONFIRMED Gate record -- one per
    (case_id, subtype), SK=f"FACILITY_CONFIRMATION#{subtype}" in
    case_store.py's shared table (mirrors case_rule_repository.py's own
    SK-family convention, a NEW/separate record family, not sharing rows
    with CASE_RULE_PACKAGE#/CASE_RULE_CONFIRMED_POINTER).

    `candidate` is the LATEST system-derived recommendation (refreshed on
    every get_or_refresh_candidates() call); `confirmed_selection` is a
    SEPARATE snapshot taken only at the moment confirm() was called -- a
    later candidate refresh can update `candidate` (and set `stale=True`
    if it now differs) without ever silently altering what was actually
    confirmed. official_pdf_renderer.py reads ONLY confirmed_selection
    (via a CONFIRMED-status record), never `candidate`.

    `CONFIRMED` means "a reviewer accepted this candidate as usable" --
    it is explicitly NOT a claim that the underlying source is government-
    official data (see FacilityCandidate.source_type, which stays
    Mock/API/GovernmentOpenData as appropriate regardless of confirmation
    status)."""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    subtype: str
    # COMPETITION-DOMAIN-MULTI-SEGMENT-B1 Task 6/7: EXPLICIT, OPTIONAL --
    # None means this is a LEGACY (pre-B1) single-segment record, keyed by
    # SK=f"FACILITY_CONFIRMATION#{subtype}" (unchanged). A non-None value
    # means this record belongs to ONE specific segment of a multi-segment
    # Competition case, keyed by SK=f"FACILITY_CONFIRMATION#{segment_code}#
    # {subtype}" -- see backend/handlers/facility_confirmation_repository.py.
    # Never inferred from case_id/subtype; always explicitly passed in.
    segment_code: Optional[str] = None
    status: FacilityConfirmationStatus = FacilityConfirmationStatus.PENDING
    candidate: Optional[FacilityCandidate] = None
    confirmed_selection: Optional[FacilityCandidate] = None
    reviewer_note: Optional[str] = None
    confirmed_by: Optional[str] = None
    rejected_by: Optional[str] = None
    stale: bool = Field(False, description="True when a freshly-derived candidate differs from confirmed_selection while status==CONFIRMED -- surfaced for human awareness, never auto-resolved")
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# COMPETITION-DOMAIN-MULTI-SEGMENT-B1: explicit Segment domain model.
#
# A single Competition Case (e.g. shulin_residential_2026's official 4-
# parcel contract) is NOT one price segment (地價區段) shared by every
# party -- the 比準地 (base parcel) and each of its N 比較標的 (comparables)
# each sit in their OWN 地價區段, each independently surveyed on its own
# 表3 地價區段勘查表. `segment_role` is ALWAYS explicit (supplied by
# whoever defines the case's segment map, e.g. a fixture-builder script or
# a future frontend form) -- NEVER inferred by parsing the segment_code
# string itself (Task 1's explicit prohibition: no "P001" prefix guessing).
# ---------------------------------------------------------------------------

class SegmentRole(str, Enum):
    BASE_SEGMENT = "BASE_SEGMENT"
    COMPARABLE_SEGMENT_1 = "COMPARABLE_SEGMENT_1"
    COMPARABLE_SEGMENT_2 = "COMPARABLE_SEGMENT_2"
    COMPARABLE_SEGMENT_3 = "COMPARABLE_SEGMENT_3"


_COMPARABLE_ROLE_BY_INDEX = {
    1: SegmentRole.COMPARABLE_SEGMENT_1,
    2: SegmentRole.COMPARABLE_SEGMENT_2,
    3: SegmentRole.COMPARABLE_SEGMENT_3,
}


class CompetitionSegment(BaseModel):
    """One 地價區段 (price zone) -- either the base parcel's own segment or
    one comparable's. `parcel_ids` holds whatever 地號/宗地流水號 identifies
    the parcel(s) surveyed within this segment (may be more than one, e.g.
    P001-00's "新北市樹林區文林段317地號"-style single parcel, or a segment
    covering multiple 地號) -- never conflated with segment_code itself."""
    model_config = ConfigDict(extra="forbid")

    segment_code: str
    segment_role: SegmentRole
    comparison_index: Optional[int] = Field(
        None, description="1/2/3 for a COMPARABLE_SEGMENT_N; must be None for BASE_SEGMENT"
    )
    parcel_ids: List[str] = Field(default_factory=list)
    district: str
    land_use_type: str

    @model_validator(mode="after")
    def _role_matches_comparison_index(self) -> "CompetitionSegment":
        if self.segment_role == SegmentRole.BASE_SEGMENT:
            if self.comparison_index is not None:
                raise ValueError("BASE_SEGMENT must not carry a comparison_index")
        else:
            expected = _COMPARABLE_ROLE_BY_INDEX.get(self.comparison_index)
            if expected is None or expected != self.segment_role:
                raise ValueError(
                    f"segment_role={self.segment_role.value!r} does not match "
                    f"comparison_index={self.comparison_index!r} (expected "
                    f"{_COMPARABLE_ROLE_BY_INDEX.get(self.comparison_index)!r})"
                )
        return self


class CompetitionSegmentMap(BaseModel):
    """The explicit, whole-case segment map: one base_segment + an ordered
    list of comparable segments. This is the ONLY source of truth for
    "which segment_codes belong to this case and what role each plays" --
    resolve_segment() (backend/handlers/competition_segments.py) looks up
    strictly within ONE case's own map, so a segment_code that happens to
    also exist in a DIFFERENT case's map can never leak across (Task 15's
    cross-case safety falls out of case_store.py's existing PK=CASE#<id>
    partitioning, not any extra check here)."""
    model_config = ConfigDict(extra="forbid")

    case_id: str
    base_segment: CompetitionSegment
    comparables: List[CompetitionSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> "CompetitionSegmentMap":
        if self.base_segment.segment_role != SegmentRole.BASE_SEGMENT:
            raise ValueError("base_segment.segment_role must be BASE_SEGMENT")
        seen_codes = {self.base_segment.segment_code}
        seen_indices = set()
        for comp in self.comparables:
            if comp.segment_role == SegmentRole.BASE_SEGMENT:
                raise ValueError("a comparable segment must not have segment_role=BASE_SEGMENT")
            if comp.segment_code in seen_codes:
                raise ValueError(f"duplicate segment_code={comp.segment_code!r} within one case")
            seen_codes.add(comp.segment_code)
            if comp.comparison_index in seen_indices:
                raise ValueError(f"duplicate comparison_index={comp.comparison_index!r} within one case")
            seen_indices.add(comp.comparison_index)
        return self

    def all_segments(self) -> List[CompetitionSegment]:
        return [self.base_segment] + list(self.comparables)
