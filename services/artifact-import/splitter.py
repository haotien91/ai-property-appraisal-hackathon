"""Lossless structural split of the producer's 1.0 bundle; no appraisal logic."""
import hashlib
import simplejson as json


class InvalidBundle(ValueError):
    pass


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidBundle(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(raw, use_decimal=True, allow_nan=False,
                          object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidBundle(f"Invalid JSON: {str(exc)[:160]}") from exc


def encode(value):
    return json.dumps(value, ensure_ascii=False, use_decimal=True,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def request_fingerprints(value):
    canonical = json.dumps(value, ensure_ascii=False, use_decimal=True, allow_nan=False,
                           separators=(",", ":"), sort_keys=True).encode("utf-8")
    # Accept previously issued keys for identically ordered legacy requests.
    return hashlib.sha256(canonical).hexdigest(), hashlib.sha256(encode(value)).hexdigest()


def require(condition, message):
    if not condition:
        raise InvalidBundle(message)


def validate_factors(factors, label, base=None, comparable=None):
    require(isinstance(factors, list), f"{label}: factors must be an array")
    seen = set()
    for factor in factors:
        require(isinstance(factor, dict), f"{label}: factor must be an object")
        ident = factor.get('field_id')
        require(isinstance(ident, str) and bool(ident), f"{label}: missing field_id")
        require(ident not in seen, f"{label}: duplicate field_id {ident}")
        seen.add(ident)
        for key, expected in [('base_segment_code', base), ('comparable_segment_code', comparable)]:
            if key in factor and expected is not None:
                require(factor[key] == expected, f"{label}.{ident}: {key} mismatch")


def split_bundle(bundle):
    require(isinstance(bundle, dict), "Bundle must be an object")
    require(bundle.get("schema_version") == "1.0", "Only producer schema_version 1.0 is supported")
    surveys = bundle.get("table3")
    segments = bundle.get("segments")
    require(isinstance(surveys, dict) and 1 <= len(surveys) <= 200,
            "table3 must contain 1–200 segment objects")
    require(isinstance(segments, dict) and set(segments) == set(surveys),
            "segments and table3 must identify the same segments")
    base = bundle.get("base_segment_code")
    comps = bundle.get("comparable_segment_codes")
    require(isinstance(base, str) and base in surveys, "Unknown base_segment_code")
    require(isinstance(comps, list) and all(isinstance(c, str) for c in comps),
            "comparable_segment_codes must be a string array")
    require(len(comps) == len(set(comps)) and base not in comps
            and set(comps) | {base} == set(surveys), "Invalid comparison segment membership")
    for code, segment in segments.items():
        require(isinstance(segment, dict) and segment.get("segment_code") == code,
                f"segments.{code}: inconsistent segment_code")
    parts = []
    for code, survey in surveys.items():
        require(isinstance(survey, dict) and survey.get("segment_code") == code,
                f"table3.{code}: inconsistent segment_code")
        require(isinstance(survey.get("factors"), list), f"table3.{code}.factors must be an array")
        validate_factors(survey["factors"], f"table3.{code}")
        parts.append({"kind": "survey", "segment_code": code, "payload": survey})
    for key, kind, field_key in [("table4", "comparison", "individual_factor_results"),
                                  ("table5_1", "regional_factors", "factor_results")]:
        table = bundle.get(key)
        require(isinstance(table, dict) and isinstance(table.get("comparisons"), list),
                f"{key}.comparisons must be an array")
        require(table.get("base_segment_code") == base, f"{key}: base segment mismatch")
        codes, indices = [], []
        for comparison in table["comparisons"]:
            require(isinstance(comparison, dict), f"{key}: comparison must be an object")
            code = comparison.get("comparable_segment_code")
            idx = comparison.get("comparison_index")
            require(isinstance(code, str) and code in comps, f"{key}: unknown comparable segment")
            require(type(idx) is int and idx > 0, f"{key}: invalid comparison_index")
            require(isinstance(comparison.get(field_key), list), f"{key}: missing factor array")
            validate_factors(comparison[field_key], f"{key}.{code}", base, code)
            codes.append(code)
            indices.append(idx)
            require(segments[code].get("comparison_index") == idx, f"{key}: comparison_index mismatch")
        require(len(codes) == len(set(codes)) and set(codes) == set(comps),
                f"{key}: comparison membership mismatch")
        require(len(indices) == len(set(indices)), f"{key}: duplicate comparison_index")
        parts.append({"kind": kind, "payload": table})
    special = {"table3", "table4", "table5_1", "review", "review_status", "manual_review_items"}
    # Unknown producer fields are preserved, never silently dropped.
    parts.append({"kind": "context", "payload": {k: v for k, v in bundle.items() if k not in special}})
    parts.append({"kind": "review", "payload": {k: bundle[k] for k in special
                                                 if k in bundle and k not in {"table3", "table4", "table5_1"}}})
    return parts


def select_fields(payload, segment_code=None, field_id=None):
    """Return whole matching factor records, retaining evidence and nulls."""
    value = payload
    if segment_code and isinstance(value, dict) and "comparisons" in value:
        selected = [c for c in value["comparisons"] if c.get("comparable_segment_code") == segment_code]
        value = {**value, "comparisons": selected}
    elif segment_code and isinstance(value, dict) and value.get("segment_code") != segment_code:
        return {"matches": []}
    if not field_id:
        return value
    hits = []
    def visit(node):
        if isinstance(node, dict):
            if node.get("field_id") == field_id:
                hits.append(node)
            else:
                for item in node.values():
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(value)
    return {"field_id": field_id, "matches": hits}
