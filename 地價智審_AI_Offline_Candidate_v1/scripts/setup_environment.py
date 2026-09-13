"""Create a complete local .env without overwriting existing settings or printing secrets."""
import argparse
from pathlib import Path
import re
import secrets


def assignments(text):
    return dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE))


def setup(root):
    template = (root / ".env.example").read_text(encoding="utf-8-sig")
    defaults = assignments(template)
    target = root / ".env"
    existing = target.read_text(encoding="utf-8-sig") if target.exists() else None
    if existing is None:
        text = template
    else:
        current = assignments(existing)
        missing = [key for key in defaults if key not in current]
        text = existing
        if missing:
            text = text.rstrip() + "\n\n# Additional settings; see .env.example for descriptions.\n"
            text += "".join(f"{key}={defaults[key]}\n" for key in missing)
    current = assignments(text)
    if not current.get("RAG_API_TOKEN", "").strip():
        text = re.sub(r"^RAG_API_TOKEN=[^\r\n]*", "RAG_API_TOKEN=" + secrets.token_urlsafe(32), text, flags=re.MULTILINE)
    if text != existing:
        target.write_text(text, encoding="utf-8")
    missing = set(defaults) - set(assignments(text))
    if missing:
        raise RuntimeError("Missing settings: " + ", ".join(sorted(missing)))
    print(f".env ready: {len(defaults)} settings; existing values preserved; secrets not displayed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    setup(parser.parse_args().root.resolve())
