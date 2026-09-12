from __future__ import annotations

import re

_PUNCT = "，。；：、,.;:()（）[]【】『』「」"


def normalize_name(text: str) -> str:
    value = re.sub(r"\s+", "", str(text))
    return value.strip(_PUNCT)


def first_explicit_road(text: str) -> tuple[str, list[str]]:
    cleaned = normalize_name(text)
    parts = [p for p in re.split(r"(?:及|與|、|和)", cleaned) if p]
    if not parts:
        raise ValueError("road description is empty")
    warnings: list[str] = []
    if len(parts) > 1:
        warnings.append(
            f"Composite boundary deferred; using first explicit road '{parts[0]}' and ignoring: {', '.join(parts[1:])}"
        )
    return parts[0], warnings
