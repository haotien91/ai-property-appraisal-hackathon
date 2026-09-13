# -*- coding: utf-8 -*-
"""Manual-review items derived from Table51Analysis/Table4Analysis dicts.

Kept free of case_store/boto3 imports so the local app can build the same
CaseExportBundle as the Lambda export path (export/bundle_builder.py)."""
from __future__ import annotations

from typing import List

from export.models import ManualReviewItem


def _far_manual_review_items(table4_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table4_analysis_dict.get("comparisons", []):
        for fr in comp.get("individual_factor_results", []):
            if fr.get("is_far_special_policy"):
                items.append(ManualReviewItem(
                    source="table4_far", segment_code=comp.get("comparable_segment_code"),
                    field_id=fr.get("field_id"), factor_name=fr.get("factor_name"),
                    reason=fr.get("reason") or "LAND_DEVELOPMENT_ANALYSIS_REQUIRED",
                ))
    return items


def _weight_manual_review_items(table4_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table4_analysis_dict.get("comparisons", []):
        if comp.get("weight_status") != "HUMAN_CONFIRMED":
            items.append(ManualReviewItem(
                source="table4_weight", segment_code=comp.get("comparable_segment_code"),
                reason=f"WEIGHT_NOT_HUMAN_CONFIRMED (status={comp.get('weight_status')})",
            ))
    return items


def _table51_manual_review_items(table51_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table51_analysis_dict.get("comparisons", []):
        seg = comp.get("comparable_segment_code")
        for fr in comp.get("factor_results", []):
            if fr.get("requires_manual_review"):
                items.append(ManualReviewItem(
                    source="table51_factor", segment_code=seg, field_id=fr.get("field_id"),
                    factor_name=fr.get("factor_name"), reason=fr.get("reason") or "MANUAL_REVIEW_REQUIRED",
                ))
    return items


def _table4_manual_review_items(table4_analysis_dict: dict) -> List[ManualReviewItem]:
    items = []
    for comp in table4_analysis_dict.get("comparisons", []):
        seg = comp.get("comparable_segment_code")
        for fr in comp.get("individual_factor_results", []):
            if fr.get("requires_manual_review") and not fr.get("is_far_special_policy"):
                items.append(ManualReviewItem(
                    source="table4_factor", segment_code=seg, field_id=fr.get("field_id"),
                    factor_name=fr.get("factor_name"), reason=fr.get("reason") or "MANUAL_REVIEW_REQUIRED",
                ))
    return items


def all_manual_review_items(table51_analysis_dict: dict, table4_analysis_dict: dict) -> List[ManualReviewItem]:
    return (_table51_manual_review_items(table51_analysis_dict) + _table4_manual_review_items(table4_analysis_dict)
            + _far_manual_review_items(table4_analysis_dict) + _weight_manual_review_items(table4_analysis_dict))
