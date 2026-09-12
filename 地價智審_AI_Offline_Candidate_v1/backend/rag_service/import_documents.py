"""Explicit offline ingestion: PDF pages / Markdown / text to searchable records.

Never infers table cells or changes the source documents.
"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--project-id", action="append", default=[])
    parser.add_argument("--shared", action="store_true", help="Explicitly make these documents visible to all cases")
    parser.add_argument("--skip-empty-pages", action="store_true", help="Skip and report pages without text; images remain unsearchable")
    args = parser.parse_args()
    if args.shared == bool(args.project_id):
        parser.error("Choose --shared OR one or more --project-id")
    target = Path(args.out)
    records = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
    for filename in args.files:
        path = Path(filename)
        raw = path.read_bytes()
        scope = sorted(args.project_id)
        digest = hashlib.sha256(raw + json.dumps(scope).encode()).hexdigest()[:20]
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            pages = [page.extract_text() or "" for page in PdfReader(path).pages]
            kind = "pdf_text_unverified_layout"
        elif path.suffix.lower() in (".md", ".txt"):
            pages, kind = [raw.decode("utf-8-sig")], "text"
        else:
            raise SystemExit("Supported: .pdf .md .txt; structured tables belong in project JSON")
        for page, content in enumerate(pages, 1):
            if not content.strip():
                if args.skip_empty_pages:
                    print(f"UNINDEXED: {path.name} page {page} has no extractable text")
                    continue
                raise SystemExit(f"No text: {path.name} page {page}; OCR/visual review required; output not written")
            record = {"id": f"{digest}-p{page}", "title": path.name, "text": content,
                      "source": path.name, "page": page, "project_ids": scope,
                      "extraction": kind, "is_mock": False}
            records = [r for r in records if r["id"] != record["id"]]
            records.append(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    print(f"Wrote {len(records)} pages/records to {target}")


if __name__ == "__main__":
    main()
