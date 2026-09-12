# -*- coding: utf-8 -*-
"""
ComparableSelectionEngine — deterministic checks from 不動產估價技術規則
（內政部，全國法規資料庫 pcode=D0060077，修正日期民國102年12月20日）§25/§26/§27,
the parent regulation the competition's 土地徵收補償市價查估作業手冊 itself
implements for 比較法 (Sales Comparison Approach). Every article cited below
was fetched and read verbatim from law.moj.gov.tw during this session --
not paraphrased from a secondary summary.

Why this exists: Phase 1 REQ-026 asked whether there is an official formula
for converting multiple comparables' adjustment magnitudes into weights.
There is not -- §27's full text is:

    "不動產估價師應採用三件以上比較標的，就其經前條推估檢討後之勘估標的
    試算價格，考量各比較標的蒐集資料可信度、各比較標的與勘估標的價格形成
    因素之相近程度，決定勘估標的之比較價格，並將比較修正內容敘明之。"

-- two QUALITATIVE considerations (資料可信度／相近程度), no equation. This
engine therefore does NOT claim to compute "the" legally correct weight; it
computes a SUGGESTED weight (SuggestedWeight.requires_human_confirmation is
always True) using a named, defensible convention (inverse-proportional to
each comparable's total adjustment magnitude -- the more a comparable had
to be adjusted, the less similar it presumably is to the appraisal target,
per §27's "相近程度" language).

§25/§26 are a mix of confirmed and unconfirmed precision, not uniformly
"exact percentage thresholds": §25's 15% single-item threshold and §26's
20% price-gap threshold both have unambiguous official formulas and are
implemented as real deterministic checks. §25's OTHER clause -- a 30%
threshold on "情況、價格日期、區域因素及個別因素調整總調整率" -- names its
four inputs but never defines how to combine them into that "總調整率";
re-verified during this session that neither the regulation's own text nor
the 土地徵收補償市價查估作業手冊 (whose only relevant "絕對值加總" formula,
p.52-53 item「十」, is explicitly for §27's weight decision, not §25's
exclusion) supplies one. check_exclusion() therefore reports two candidate
readings of that total for human reference and never lets either one drive
`excluded` -- see ComparableExclusionCheck's docstring for the full trail.
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    AdjustmentResult, ComparableExclusionCheck, ExclusionDeterminationStatus,
    TrialPriceGapCheck, SuggestedWeight,
)

SINGLE_ITEM_THRESHOLD_PCT = Decimal("15")   # §25: 任一單獨項目 > 15%
TOTAL_ADJUSTMENT_THRESHOLD_PCT = Decimal("30")  # §25: 總調整率 > 30%
PRICE_GAP_THRESHOLD_PCT = Decimal("20")     # §26: 試算價格差距 >= 20%


class ComparableSelectionEngine:
    """Stateless; every method is a pure function of its inputs."""

    def check_exclusion(
        self,
        comparable_id: str,
        regional_adjustments: List[AdjustmentResult],
        individual_adjustments: List[AdjustmentResult],
        situation_adjustment_pct: Decimal = Decimal("0"),
        price_date_adjustment_pct: Decimal = Decimal("0"),
        exception_note: Optional[str] = None,
    ) -> ComparableExclusionCheck:
        """§25: flags (does not silently drop) a comparable whose any single
        regional/individual adjustment item exceeds 15% in magnitude --
        `excluded` is driven ONLY by this check, per §25's unambiguous text
        ("任一單獨項目之價格調整率大於百分之十五").

        The article's OTHER clause -- 情況+價格日期+區域因素+個別因素"總調整
        率"大於百分之三十 -- has no defined computation formula anywhere this
        codebase could find (re-verified directly against law.moj.gov.tw's
        §25 full text and the 土地徵收補償市價查估作業手冊's 表4 section,
        p.52-53, during this session; see ComparableExclusionCheck's
        docstring for the full citation trail). This method therefore
        computes BOTH defensible candidate readings and reports them purely
        as reference information (`total_adjustment_legal_basis` is always
        "LEGAL_BASIS_UNCONFIRMED") -- it never lets either one flip
        `excluded`, since doing so would be inventing a legal threshold the
        regulation itself does not define a formula for.

        `exception_note`: pass a non-empty string ONLY when the estimator
        has actually documented the §25 proviso ("性質特殊或區位特殊缺乏
        市場交易資料，並於估價報告書中敘明") -- this function does not
        invent or infer that justification on its own; it only records
        whether the caller supplied one alongside the rule's own verdict."""
        all_items = list(regional_adjustments) + list(individual_adjustments)
        over_15 = [a.factor for a in all_items if abs(a.adjustment_pct) > SINGLE_ITEM_THRESHOLD_PCT]

        region_total = sum((a.adjustment_pct for a in regional_adjustments), Decimal("0"))
        individual_total = sum((a.adjustment_pct for a in individual_adjustments), Decimal("0"))

        signed_sum = situation_adjustment_pct + price_date_adjustment_pct + region_total + individual_total
        abs_component_sum = (
            abs(situation_adjustment_pct) + abs(price_date_adjustment_pct)
            + sum((abs(a.adjustment_pct) for a in regional_adjustments), Decimal("0"))
            + sum((abs(a.adjustment_pct) for a in individual_adjustments), Decimal("0"))
        )

        total_over_30_signed = abs(signed_sum) > TOTAL_ADJUSTMENT_THRESHOLD_PCT
        total_over_30_abs = abs_component_sum > TOTAL_ADJUSTMENT_THRESHOLD_PCT

        if over_15:
            status = ExclusionDeterminationStatus.EXCLUDED
        elif total_over_30_signed or total_over_30_abs:
            # Neither candidate reading of "總調整率" is a confirmed legal
            # formula (see docstring) -- this comparable's §25 total-clause
            # status genuinely cannot be determined, which is NOT the same
            # thing as "confirmed not excluded".
            status = ExclusionDeterminationStatus.UNDETERMINED
        else:
            status = ExclusionDeterminationStatus.NOT_EXCLUDED

        return ComparableExclusionCheck(
            comparable_id=comparable_id,
            exclusion_determination_status=status,
            excluded=(status == ExclusionDeterminationStatus.EXCLUDED),
            over_15pct_items=over_15,
            total_adjustment_signed_sum_pct=signed_sum,
            total_adjustment_abs_component_sum_pct=abs_component_sum,
            total_over_30pct_signed_sum=total_over_30_signed,
            total_over_30pct_abs_component_sum=total_over_30_abs,
            exception_claimed=bool(exception_note),
            exception_note=exception_note,
        )

    def check_trial_price_gap(self, trial_prices_raw: Dict[str, Decimal]) -> TrialPriceGapCheck:
        """§26 second paragraph's exact formula: gap_pct = (高-低) /
        ((高+低)/2). Requires at least 2 trial prices to compare; raises
        for fewer, since "gap between highest and lowest" is undefined for
        a single value (never silently returns triggered=False for that
        case, which would misrepresent 'not applicable' as 'passed')."""
        if len(trial_prices_raw) < 2:
            raise ValueError(
                f"§26試算價格差距檢核需要至少2個比較標的之試算價格，收到{len(trial_prices_raw)}個。"
                "單一比較標的無「最高最低價格差距」可比較，此檢核不適用，請勿呼叫。"
            )
        max_id = max(trial_prices_raw, key=lambda cid: trial_prices_raw[cid])
        min_id = min(trial_prices_raw, key=lambda cid: trial_prices_raw[cid])
        high, low = trial_prices_raw[max_id], trial_prices_raw[min_id]
        gap_pct = (high - low) / ((high + low) / Decimal("2")) * Decimal("100")

        return TrialPriceGapCheck(
            max_price=high, min_price=low,
            max_comparable_id=max_id, min_comparable_id=min_id,
            gap_pct=gap_pct, triggered=gap_pct >= PRICE_GAP_THRESHOLD_PCT,
        )

    def suggest_weights(self, adjustment_abs_sums: Dict[str, Decimal]) -> Dict[str, SuggestedWeight]:
        """§27 gives no formula (see module docstring) -- this is a named,
        disclosed CONVENTION, not a legal computation: weight is inverse-
        proportional to each comparable's total adjustment magnitude
        (smaller magnitude = presumed closer 相近程度 per §27's own
        language = suggested higher weight), normalized so the suggestions
        sum to 100%. If every comparable has an identical (including zero)
        adjustment magnitude, splits evenly rather than dividing by zero.
        Every returned SuggestedWeight carries requires_human_confirmation
        =True; nothing in this codebase auto-applies these into
        CalculationEngine.base_parcel_comparison_price() without that
        confirmation happening first. That said, the returned percentages
        are adjusted to sum to EXACTLY 100.00 (see below) so that accepting
        every suggestion as-is is valid input to that function without
        further arithmetic -- confirmation is still required, but a human
        who does confirm as-is should not then hit a spurious rounding
        error the suggestion itself introduced."""
        if not adjustment_abs_sums:
            raise ValueError("suggest_weights需要至少1個比較標的之adjustment_abs_sum")

        inverses = {cid: Decimal("1") / (v + Decimal("1")) for cid, v in adjustment_abs_sums.items()}
        # +1 denominator avoids division-by-zero for a comparable with a
        # genuinely zero total adjustment (a perfect match), while still
        # preserving strict ordering (smaller magnitude -> larger inverse).
        inverse_sum = sum(inverses.values(), Decimal("0"))

        pcts: Dict[str, Decimal] = {
            cid: (inv / inverse_sum * Decimal("100")).quantize(Decimal("0.01"))
            for cid, inv in inverses.items()
        }
        # Independently-rounded percentages can miss 100.00 by a cent or two
        # (e.g. three equal comparables -> 33.33+33.33+33.33=99.99). Push
        # the residual onto whichever comparable has the LARGEST suggested
        # weight (proportionally the least-affected by a one-cent nudge),
        # breaking ties by comparable_id for determinism.
        residual = Decimal("100") - sum(pcts.values(), Decimal("0"))
        if residual != Decimal("0"):
            adjust_cid = max(pcts, key=lambda cid: (pcts[cid], cid))
            pcts[adjust_cid] = pcts[adjust_cid] + residual

        return {
            cid: SuggestedWeight(comparable_id=cid, suggested_weight_pct=pct)
            for cid, pct in pcts.items()
        }
