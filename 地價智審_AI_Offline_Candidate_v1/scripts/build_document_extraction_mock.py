# -*- coding: utf-8 -*-
"""
Regenerates frontend/*/mock/document_extraction.json from the REAL
LocalExtractionProvider run against the Golden PDF (data/sources/
competition/查估書表範本.pdf) -- the same code path tests/
test_document_extraction_e2e.py already proves connects to AuditEngine.

Why this exists: Final Frontend Integration needs a Document Upload/
Extraction/Confirmation demo screen that works with no AWS access (see
provider-contract skill's CODE_READY vs RUNTIME_VERIFIED distinction --
this repo has no AWS_RUNTIME_VERIFIED provider). Rather than hand-author a
JSON file that merely LOOKS like ExtractedField output, this script runs
the actual FROZEN LocalExtractionProvider (LOCAL_RUNTIME_VERIFIED) and
serializes its real output, then applies the actual flag_low_confidence()
gate -- identical principle to scripts/build_demo_review_result_mock.py.

This script only READS engine/providers/domain code; it does not modify
any CORE FREEZE file.

Usage: py scripts/build_document_extraction_mock.py
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "engine", "providers"):
    sys.path.insert(0, os.path.join(REPO_ROOT, p) if p else REPO_ROOT)

from providers.document_extraction_provider import LocalExtractionProvider  # noqa: E402
from engine.human_confirmation import flag_low_confidence  # noqa: E402

GOLDEN_PDF = os.path.join(REPO_ROOT, "data", "sources", "competition", "查估書表範本.pdf")
MOCK_DOCUMENT_ID = "LOCAL-MOCK-GOLDEN-DOCUMENT"
MOCK_CASE_NO = "1140901-99-001"

OUTPUT_PATHS = [
    os.path.join(REPO_ROOT, "frontend", "app", "frontend", "mock", "document_extraction.json"),
    os.path.join(REPO_ROOT, "frontend", "mock", "document_extraction.json"),
]


def main() -> int:
    provider = LocalExtractionProvider()
    classifications = provider.classify(GOLDEN_PDF)
    fields = provider.extract_fields(GOLDEN_PDF, classifications)
    fields = flag_low_confidence(fields)

    requires_confirmation_count = sum(1 for f in fields if f.requires_manual_review)

    field_dicts = []
    for f in fields:
        fd = json.loads(f.model_dump_json())
        # Overwrite the local absolute filesystem path LocalExtractionProvider
        # stamped into source_document (this machine's real disk path) with a
        # stable, machine-independent label -- this is fixture post-processing
        # in this standalone script only, not a change to ExtractedField's
        # actual production behavior (document_extract.py stamps its own
        # /tmp/{document_id}.pdf path there in the real Lambda path, which is
        # equally machine-local by design; a checked-in demo fixture must not
        # leak a contributor's local directory name instead).
        fd["source_document"] = "查估書表範本.pdf（Golden Case, LOCAL/MOCK）"
        field_dicts.append(fd)

    payload = {
        "case_no": MOCK_CASE_NO,
        "document_id": MOCK_DOCUMENT_ID,
        "status": "COMPLETED",
        "field_count": len(fields),
        "requires_confirmation_count": requires_confirmation_count,
        "extractor": "LocalExtractionProvider",
        "extractor_version": "1.0",
        "created_at": "2026-09-04T00:00:00+00:00",
        "classifications": [json.loads(c.model_dump_json()) for c in classifications],
        "fields": field_dicts,
        "local_mock": True,
        "local_mock_notice": (
            "此為 LOCAL/MOCK 展示資料：由 LocalExtractionProvider 對 Golden PDF "
            "實際執行擷取後序列化產生，並非透過 AWS S3/Lambda 即時處理。"
        ),
    }

    for path in OUTPUT_PATHS:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"寫入 {path}")

    print(
        f"\ndocument_id={MOCK_DOCUMENT_ID}  field_count={len(fields)}  "
        f"requires_confirmation_count={requires_confirmation_count}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
