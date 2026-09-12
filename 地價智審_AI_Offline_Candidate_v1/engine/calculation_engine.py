# -*- coding: utf-8 -*-
"""
Calculation Engine — deterministic arithmetic for the Sales Comparison
Approach (比較法). Implements the exact chain independently re-derived and
verified in docs/phase2/calculation_dependency.md and
docs/phase3/rule_engine_spec.md:

    land_normal_price
      -> (x (1 + price_date_adjustment_rate))          [full precision, NEVER rounded here]
      -> adjusted_price (raw, unrounded)
      -> (x (1 + region_adjustment_rate + individual_adjustment_total))  [full precision]
      -> trial_price (raw, unrounded)
      -> (x weight, summed across comparables if >1)
      -> base_parcel_comparison_price
      -> ROUND HERE ONLY (REQ-022: conventional rounding to the unit's digit)

Two independently-verified findings drive this design:
1. Using the DISPLAYED (already-rounded) intermediate 188,459 instead of the
   raw 188,458.26 produces 212,959, which does NOT match the official
   Golden Case answer of 212,958. Only full-precision propagation matches.
2. The manual specifies TWO DIFFERENT rounding rules for two different
   fields (REQ-021 tiered-ceiling for base land price itself; REQ-022
   conventional rounding specifically for 比準地比較價格) -- this engine
   implements ONLY REQ-022 (the one actually exercised by this pipeline);
   REQ-021 is out of scope here (it applies to a different form/field, see
   docs/phase2/business_process.md Step 2, which is outside the three core
   forms this competition targets).

No LLM, no network calls, no randomness anywhere in this module.
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    CalculationStep, CalculationResult, RoundingRule, AdjustmentResult,
)


class CalculationEngineError(Exception):
    pass


def _d(value) -> Decimal:
    """Converts any numeric-ish input to Decimal via its string
    representation, never via float(), to avoid introducing binary
    floating-point error before we've even started (e.g. float(1.13) itself
    is not exactly 1.13 in IEEE-754; str-based Decimal construction avoids
    this class of error entirely)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class CalculationEngine:
    """Stateless; every method is a pure function of its inputs. All
    quantities are Decimal throughout; conversion to float is the caller's
    responsibility (e.g. for JSON serialization at the API boundary)."""

    def price_date_adjustment(self, land_normal_price, price_date_adjustment_rate_pct) -> CalculationStep:
        base = _d(land_normal_price)
        rate = _d(price_date_adjustment_rate_pct) / Decimal("100")
        raw = base * (Decimal("1") + rate)
        return CalculationStep(
            step="price_date_adjustment",
            formula="land_normal_price * (1 + price_date_adjustment_rate / 100)",
            inputs={
                "land_normal_price": str(base),
                "price_date_adjustment_rate_pct": str(_d(price_date_adjustment_rate_pct)),
            },
            raw_result=raw,
            rounding_rule=RoundingRule.NONE_FULL_PRECISION,
            rounded_result=None,
            source_document="土地徵收補償市價查估作業手冊.pdf",
            source_page="p.52-53",
            notes=(
                "此步驟結果（調整至估價基準日單價）在官方表單上會顯示一個四捨五入後的"
                "數字，但該顯示值之進位規則官方未明文規定（見 docs/phase3/source_anomalies.md "
                "ANOMALY-09相關的 open_questions B-4）。本Engine刻意不在此處四捨五入，"
                "全程使用raw_result（完整精度）帶入下一步計算，僅在最終比準地比較價格"
                "才依REQ-022進位，這是重現官方Golden Case答案212,958的必要條件（見"
                "docs/phase2/calculation_dependency.md 段落9之獨立驗算）。"
            ),
        )

    def trial_price(self, adjusted_price_raw: Decimal, region_adjustment_rate_pct,
                     individual_adjustment_total_pct) -> CalculationStep:
        adjusted = _d(adjusted_price_raw)
        region = _d(region_adjustment_rate_pct) / Decimal("100")
        individual = _d(individual_adjustment_total_pct) / Decimal("100")
        net = Decimal("1") + region + individual
        raw = adjusted * net
        return CalculationStep(
            step="trial_price",
            formula="adjusted_price * (1 + region_adjustment_rate/100 + individual_adjustment_total/100)",
            inputs={
                "adjusted_price_raw": str(adjusted),
                "region_adjustment_rate_pct": str(_d(region_adjustment_rate_pct)),
                "individual_adjustment_total_pct": str(_d(individual_adjustment_total_pct)),
            },
            raw_result=raw,
            rounding_rule=RoundingRule.NONE_FULL_PRECISION,
            rounded_result=None,
            source_document="土地徵收補償市價查估作業手冊.pdf",
            source_page="p.52-53",
        )

    def individual_adjustment_total(self, adjustments: List[AdjustmentResult]) -> CalculationStep:
        total = sum((a.adjustment_pct for a in adjustments), Decimal("0"))
        return CalculationStep(
            step="individual_adjustment_total",
            formula="sum(differential_rate for each of 19 individual factors)",
            inputs={a.factor: str(a.adjustment_pct) for a in adjustments},
            raw_result=total,
            rounding_rule=RoundingRule.NONE_FULL_PRECISION,
            rounded_result=None,
            source_document="查估書表範本.pdf",
            source_page="表4",
        )

    def regional_total_adjustment(self, adjustments: List[AdjustmentResult]) -> CalculationStep:
        total = sum((a.adjustment_pct for a in adjustments), Decimal("0"))
        return CalculationStep(
            step="regional_total_adjustment",
            formula="sum(regional_subtotal for 主要項目 1-8)",
            inputs={a.factor: str(a.adjustment_pct) for a in adjustments},
            raw_result=total,
            rounding_rule=RoundingRule.NONE_FULL_PRECISION,
            rounded_result=None,
            source_document="查估書表範本.pdf",
            source_page="表5-2",
            notes="須逐字等於表4之區域因素調整百分率（跨表一致性核心檢查點，見docs/phase2/form_dependency.md）",
        )

    def adjustment_abs_sum(self, price_date_rate_pct, region_rate_pct, individual_total_pct) -> CalculationStep:
        total = abs(_d(price_date_rate_pct)) + abs(_d(region_rate_pct)) + abs(_d(individual_total_pct))
        return CalculationStep(
            step="adjustment_abs_sum",
            formula="abs(price_date_rate) + abs(region_rate) + abs(individual_total)",
            inputs={
                "price_date_rate_pct": str(_d(price_date_rate_pct)),
                "region_rate_pct": str(_d(region_rate_pct)),
                "individual_total_pct": str(_d(individual_total_pct)),
            },
            raw_result=total,
            rounding_rule=RoundingRule.NONE_FULL_PRECISION,
            source_document="土地徵收補償市價查估作業手冊.pdf",
            source_page="p.52-53",
        )

    def base_parcel_comparison_price(
        self, trial_prices_raw: Dict[str, Decimal], weights_pct: Dict[str, Decimal]
    ) -> CalculationStep:
        """Weighted average of one or more comparables' trial prices, with
        REQ-022 conventional rounding applied ONLY at this final step."""
        if set(trial_prices_raw.keys()) != set(weights_pct.keys()):
            raise CalculationEngineError(
                "trial_prices_raw and weights_pct must cover the exact same comparable_ids; "
                f"got {sorted(trial_prices_raw)} vs {sorted(weights_pct)}"
            )
        weight_sum = sum(weights_pct.values(), Decimal("0"))
        if weight_sum != Decimal("100"):
            raise CalculationEngineError(
                f"Comparable weights must sum to exactly 100%, got {weight_sum}%. "
                f"This engine never silently normalizes weights -- 不動產估價技術規則"
                f"§27 has NO official weight formula (confirmed directly against "
                f"law.moj.gov.tw, see docs/phase1/evidence_matrix.md REQ-029 and "
                f"docs/phase1/open_questions.md B-2), so the caller must supply valid "
                f"weights explicitly. engine/comparable_selection_engine.py's "
                f"suggest_weights() can produce a disclosed, human-confirmable "
                f"suggestion that already sums to exactly 100%."
            )
        weighted_sum = sum(
            (trial_prices_raw[cid] * (weights_pct[cid] / Decimal("100")) for cid in trial_prices_raw),
            Decimal("0"),
        )
        rounded = weighted_sum.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return CalculationStep(
            step="base_parcel_comparison_price",
            formula="sum(trial_price[c] * weight[c]/100 for each comparable c)",
            inputs={
                **{f"trial_price[{c}]": str(trial_prices_raw[c]) for c in trial_prices_raw},
                **{f"weight[{c}]": str(weights_pct[c]) for c in weights_pct},
            },
            raw_result=weighted_sum,
            rounding_rule=RoundingRule.CONVENTIONAL_ROUND,
            rounded_result=rounded,
            source_document="土地徵收補償市價查估作業手冊.pdf",
            source_page="p.52-53",
            notes="REQ-022:「比準地比較價格之尾數以四捨五入計算至個位數」，四捨五入僅在此步驟套用",
        )
