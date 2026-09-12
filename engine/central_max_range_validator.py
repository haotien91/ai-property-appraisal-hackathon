# -*- coding: utf-8 -*-
"""
CentralMaxRangeDatasetValidator — structural integrity checks for the
digitized central max-adjustment-range dataset (data/rules/central_max_
adjustment_range.json), which is the 內政部104年1月30日台內地字第
10413006723號令附表「影響地價個別因素評價基準表」／「影響地價區域因素評價
基準表」transcribed by hand from a rendered image (see the dataset's own
source_document.digitization_method for why -- the source PDF's embedded
font has no usable ToUnicode mapping for the Chinese label text).

Scope, per this round's explicit instruction: validate that the DIGITIZED
DATASET ITSELF is well-formed and internally consistent (schema, no
duplicate cells, symmetric subgrade coverage). This module does NOT compare
the central table against this codebase's local regional_rules.json/
individual_rules.json -- that cross-validation (and the local-to-central
subgrade mapping it would require) is explicitly out of scope until a human
confirms the mapping in data/rules/central_max_range_local_mapping.json
(currently all UNMAPPED -- see LocalToCentralMappingStatus).
"""
from __future__ import annotations

import json
import sys
import os
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain.models import (  # noqa: E402
    CentralMaxRangeDataset, CentralMaxRangeEntry, CentralMaxRangeCellState,
    LocalToCentralMappingStatus,
)

VALID_LAND_USE_TYPES = {"住宅用地", "商業用地", "工業用地", "農業用地", "其他用地"}
VALID_TABLE_TYPES = {"individual", "regional"}

DEFAULT_DATASET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "rules", "central_max_adjustment_range.json",
)
DEFAULT_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "rules", "central_max_range_local_mapping.json",
)


class CentralMaxRangeDatasetError(Exception):
    """Raised when the dataset file cannot even be parsed/schema-validated
    -- distinct from CentralMaxRangeValidationIssue, which reports
    structural problems in an otherwise schema-valid dataset."""


class CentralMaxRangeValidationIssue:
    def __init__(self, severity: str, message: str):
        self.severity = severity  # "ERROR" | "WARNING"
        self.message = message

    def __repr__(self) -> str:
        return f"[{self.severity}] {self.message}"

    def __eq__(self, other) -> bool:
        return isinstance(other, CentralMaxRangeValidationIssue) and \
            (self.severity, self.message) == (other.severity, other.message)


def load_central_max_range_dataset(path: str = DEFAULT_DATASET_PATH) -> CentralMaxRangeDataset:
    """Reads and pydantic-validates the dataset file. Raises
    CentralMaxRangeDatasetError (not a raw pydantic/JSON exception) with a
    human-readable message on any parse/schema failure -- this function
    never returns a partially-valid dataset."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise CentralMaxRangeDatasetError(f"無法讀取或解析{path}：{e}") from e

    try:
        return CentralMaxRangeDataset.model_validate(raw)
    except Exception as e:  # pydantic ValidationError
        raise CentralMaxRangeDatasetError(f"{path}未通過schema驗證：{e}") from e


def load_local_mapping_statuses(path: str = DEFAULT_MAPPING_PATH) -> List[LocalToCentralMappingStatus]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise CentralMaxRangeDatasetError(f"無法讀取或解析{path}：{e}") from e
    try:
        return [LocalToCentralMappingStatus.model_validate(item) for item in raw]
    except Exception as e:
        raise CentralMaxRangeDatasetError(f"{path}未通過schema驗證：{e}") from e


class CentralMaxRangeDatasetValidator:
    """Stateless; every method is a pure function of its inputs."""

    def validate(self, dataset: CentralMaxRangeDataset) -> List[CentralMaxRangeValidationIssue]:
        issues: List[CentralMaxRangeValidationIssue] = []
        issues.extend(self._check_known_categories(dataset.entries))
        issues.extend(self._check_no_duplicate_cells(dataset.entries))
        issues.extend(self._check_subgrade_symmetry(dataset.entries))
        return issues

    @staticmethod
    def has_errors(issues: List[CentralMaxRangeValidationIssue]) -> bool:
        return any(i.severity == "ERROR" for i in issues)

    def _check_known_categories(self, entries: List[CentralMaxRangeEntry]) -> List[CentralMaxRangeValidationIssue]:
        issues = []
        for e in entries:
            if e.table_type not in VALID_TABLE_TYPES:
                issues.append(CentralMaxRangeValidationIssue(
                    "ERROR", f"未知table_type={e.table_type!r}（{e.land_use_type}/{e.item_name}）"))
            if e.land_use_type not in VALID_LAND_USE_TYPES:
                issues.append(CentralMaxRangeValidationIssue(
                    "ERROR", f"未知land_use_type={e.land_use_type!r}（{e.item_name}）"))
        return issues

    def _check_no_duplicate_cells(self, entries: List[CentralMaxRangeEntry]) -> List[CentralMaxRangeValidationIssue]:
        """Two entries covering the exact same (table, land_use_type,
        subgrade, category, item) cell would mean one is either a
        transcription duplicate or a silently-conflicting overwrite --
        both are integrity bugs the dataset must never contain."""
        issues = []
        seen: Dict[Tuple, CentralMaxRangeEntry] = {}
        for e in entries:
            key = (e.table_type, e.land_use_type, e.land_use_subgrade,
                   e.major_category_name, e.item_code, e.item_name)
            if key in seen:
                issues.append(CentralMaxRangeValidationIssue(
                    "ERROR", f"重複的儲存格：{key}（值分別為{seen[key].max_range_pct}與{e.max_range_pct}）"))
            seen[key] = e
        return issues

    def _check_subgrade_symmetry(self, entries: List[CentralMaxRangeEntry]) -> List[CentralMaxRangeValidationIssue]:
        """Within one (table_type, land_use_type) table, every subgrade
        column must cover the exact same set of (major_category, item)
        rows as every other subgrade of that same table -- the source
        prints one row per item across all of that table's subgrade
        columns simultaneously, so a mismatch here means a row was missed
        (or extra) for one specific subgrade during transcription."""
        issues = []
        groups: Dict[Tuple[str, str], Dict[object, set]] = defaultdict(lambda: defaultdict(set))
        for e in entries:
            key = (e.table_type, e.land_use_type)
            item_key = (e.major_category_name, e.item_code, e.item_name)
            groups[key][e.land_use_subgrade].add(item_key)

        for (table_type, land_use_type), by_subgrade in groups.items():
            subgrades = list(by_subgrade.keys())
            if len(subgrades) <= 1:
                continue
            reference_sg = subgrades[0]
            reference_items = by_subgrade[reference_sg]
            for sg in subgrades[1:]:
                if by_subgrade[sg] != reference_items:
                    missing = reference_items - by_subgrade[sg]
                    extra = by_subgrade[sg] - reference_items
                    issues.append(CentralMaxRangeValidationIssue(
                        "ERROR",
                        f"{table_type}/{land_use_type}：子級別「{sg}」與「{reference_sg}」項目不對稱"
                        f"（缺少{missing or '無'}；多出{extra or '無'}）",
                    ))
        return issues
