"""Load shared defaults and local credentials without executing shell syntax."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_environment(root=ROOT):
    values = {}
    for name in (".env.shared.example", ".env"):
        path = root / name
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid environment assignment: {name}:{number}")
            if value.startswith(("\"", "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Unclosed environment value: {name}:{number}")
                value = value[1:-1]
            values[key] = value
    for key, value in values.items():
        if value:
            os.environ.setdefault(key, value)


def frontend_config():
    mode = os.environ.get("APP_MODE", "local")
    if mode not in ("local", "mock", "production"):
        raise ValueError("APP_MODE must be local, mock or production")
    url = os.environ.get("API_BASE_URL", "").rstrip("/")
    if mode == "production" and (not url.startswith("https://") or "REPLACE" in url):
        raise ValueError("Production requires a real HTTPS API_BASE_URL")
    return {"MODE": "production" if mode == "production" else "mock",
            "API_BASE_URL": url, "LOCAL_BACKEND": mode == "local"}
