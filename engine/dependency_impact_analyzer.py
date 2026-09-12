# -*- coding: utf-8 -*-
"""
DependencyImpactAnalyzer — given a field_id that has an error, returns the
list of downstream field_ids that are potentially affected, per
data/dependency_graph.json. Pure, deterministic, no LLM.
"""
from __future__ import annotations
import re
import json
from typing import List, Dict, Any


class DependencyImpactAnalyzer:
    def __init__(self, graph_path: str):
        with open(graph_path, encoding="utf-8") as f:
            self._graph = json.load(f)
        self._rules = self._graph["rules"]

    def analyze(self, field_id: str) -> List[str]:
        """Returns downstream field_ids impacted if `field_id` is wrong.
        Multiple rules can match (e.g. a regional adjustment_pct field also
        matches nothing else, but a field could theoretically match more
        than one pattern in richer graphs) -- results are de-duplicated
        while preserving first-seen order."""
        impacted: List[str] = []
        for rule in self._rules:
            m = re.match(rule["upstream_pattern"], field_id)
            if not m:
                continue
            groups = m.groupdict()
            for template in rule["downstream_template"]:
                resolved = template.format(**groups) if groups else template
                if resolved not in impacted:
                    impacted.append(resolved)
        return impacted

    def upstream_of(self, field_id: str) -> List[str]:
        """Reverse lookup: which upstream field_id patterns (described, not
        enumerated) feed into this field, for populating
        AuditIssue.upstream_dependency. Since the graph is pattern-based
        and does not enumerate concrete upstream field_ids (there can be up
        to 28 for a single downstream node), this returns human-readable
        rule descriptions rather than concrete field_ids when the upstream
        side is a many-to-one convergence."""
        upstream_descriptions: List[str] = []
        for rule in self._rules:
            for template in rule["downstream_template"]:
                # A field_id "matches" a downstream template if it equals
                # the template with any {cid} substituted -- reverse this by
                # checking if the template's non-parametrized parts appear
                # as a prefix/suffix of field_id.
                template_regex = "^" + re.escape(template).replace(r"\{cid\}", r"(?P<cid>.+)") + "$"
                if re.match(template_regex, field_id):
                    upstream_descriptions.append(rule["description"])
        return upstream_descriptions

    def matched_rule_ids(self, field_id: str) -> List[str]:
        return [r["rule_id"] for r in self._rules if re.match(r["upstream_pattern"], field_id)]
