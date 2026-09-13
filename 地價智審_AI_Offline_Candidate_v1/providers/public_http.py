"""Bounded, cached public-data requests. No credentials or case contents sent."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request
import urllib.parse


class PublicDataError(RuntimeError):
    pass


class PublicHttpClient:
    def __init__(self, cache_dir=None, offline=False, timeout=10):
        self.cache_dir = Path(cache_dir or os.environ.get("PUBLIC_DATA_CACHE_DIR") or
                              Path(__file__).resolve().parents[1] / "data/public_data_cache")
        self.offline, self.timeout = offline, timeout

    def get(self, url, ttl=86400, data=None, timeout=None):
        if urllib.parse.urlsplit(url).scheme != "https":
            raise PublicDataError("公開資料來源必須使用 HTTPS")
        key = hashlib.sha256(url.encode() + (data or b"")).hexdigest()
        path = self.cache_dir / (key + ".json")
        cached = None
        try:
            cached = json.loads(path.read_text("utf-8"))
            if cached["url"] == url and (self.offline or time.time() - cached["timestamp"] < ttl):
                return cached["body"].encode("utf-8"), {"url": url, "retrieved_at": cached["timestamp"],
                    "cached": True, "stale": time.time() - cached["timestamp"] >= ttl}
        except (OSError, ValueError, KeyError):
            pass
        if self.offline:
            raise PublicDataError("離線模式沒有這次查詢的快取")
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": "NTPC-Land-Appraisal-PublicData/1.0 (2026 hackathon; user-initiated)",
                "Accept": "application/json, application/xml, text/xml",
            })
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as res:
                raw = res.read(8_000_001)
            if len(raw) > 8_000_000:
                raise PublicDataError("回應超過 8 MB 上限，請縮小查詢範圍")
            body = raw.decode("utf-8-sig")
            # Do not cache HTML rate-limit or error pages as successful data.
            if not body.lstrip().startswith(("{", "[", "<?xml", "<")) or "<html" in body[:500].lower():
                raise PublicDataError("來源未回傳可辨識的 JSON/XML")
        except (OSError, UnicodeError) as exc:
            raise PublicDataError(f"公開服務連線失敗：{type(exc).__name__}") from exc
        stamp = time.time()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        record = {"url": url, "timestamp": stamp, "body": body}
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir,
                                             delete=False) as handle:
                temp = handle.name
                json.dump(record, handle, ensure_ascii=False)
            os.replace(temp, path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        return body.encode("utf-8"), {"url": url, "retrieved_at": stamp, "cached": False, "stale": False}

    def json(self, url, **kwargs):
        raw, meta = self.get(url, **kwargs)
        try:
            return json.loads(raw), meta
        except ValueError as exc:
            raise PublicDataError("公開服務 JSON 格式錯誤") from exc
