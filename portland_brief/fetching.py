"""Bounded requests, conditional caching, and explicit outage records."""
from datetime import datetime, timezone
import json
import hashlib
import fcntl
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET
from xml.etree import ElementTree as ET

from .records import digest, safe_url


def plain(text: str, limit: int = 1600) -> str:
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "iframe", "form"]):
        tag.decompose()
    result = " ".join(soup.get_text(" ", strip=True).split())
    if len(result) > limit:
        result = result[:limit].rsplit(" ", 1)[0] + "…"
    return result


def minimize_feed(data: bytes, limit: int) -> bytes:
    root = SafeET.fromstring(data)
    for parent in root.iter():
        for child in list(parent):
            name = child.tag.rsplit("}", 1)[-1].lower()
            if name in {"encoded", "content", "enclosure", "thumbnail", "image"}:
                parent.remove(child)
            elif name in {"description", "summary"}:
                child.text = plain(child.text or "", limit)
                for nested in list(child):
                    child.remove(nested)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class Fetcher:
    def __init__(self, root: Path, settings: dict, force: bool = False):
        self.settings, self.force = settings, force
        self.cache = root / settings["cache_path"]
        self.cache.mkdir(parents=True, exist_ok=True)
        self.user_agent = os.getenv("PORTLAND_BRIEF_USER_AGENT", "PortlandDailyBrief/0.1 (local personal news research)")

    def get(self, url: str, kind: str = "json", user_agent: str | None = None):
        url = safe_url(url)
        # News and civic jobs share this cache across processes. Hold one URL's
        # lock through reading, conditional requests and both file replacements.
        # Other URLs remain independent; never unlink a shared lock file.
        with (self.cache / (digest(url) + ".lock")).open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._get(url, kind, user_agent)

    def _get(self, url, kind, user_agent):
        key = digest(url)
        bodyfile, metafile = self.cache / (key + ".bin"), self.cache / (key + ".json")
        meta, cached = {}, None
        if bodyfile.exists() and metafile.exists():
            try:
                meta = json.loads(metafile.read_text())
                cached = bodyfile.read_bytes()
                # A crash between replacements (or a legacy concurrent write)
                # must not attach another body's validators or freshness to it.
                if not isinstance(meta, dict) or meta.get("content_hash") != hashlib.sha256(cached).hexdigest():
                    meta, cached = {}, None
            except (ValueError, OSError):
                meta, cached = {}, None
        now = datetime.now(timezone.utc)
        age = now.timestamp() - meta.get("checked_epoch", 0)
        if meta and not self.force and age < self.settings["cache_ttl_seconds"]:
            return cached, {**meta, "status": "cached", "url": url}
        agent = user_agent or self.user_agent
        if '\r' in agent or '\n' in agent:
            raise ValueError('User-Agent must be a single header value')
        headers = {"User-Agent": agent, "Accept": "application/geo+json, application/json" if kind == "json" else "application/rss+xml, application/xml, text/html"}
        for keyname, headername in [("etag", "If-None-Match"), ("last_modified", "If-Modified-Since")]:
            if meta.get(keyname):
                headers[headername] = meta[keyname]
        error = "request failed"
        for attempt in range(self.settings["fetch_attempts"]):
            try:
                with urlopen(Request(url, headers=headers), timeout=self.settings["fetch_timeout_seconds"]) as response:
                    host = urlsplit(url).hostname
                    allowed = [host, host[4:]] if host.startswith("www.") else [host, "www." + host]
                    safe_url(response.url, allowed)
                    limit = self.settings.get("response_byte_limit", 2_000_000)
                    raw = response.read(limit + 1)
                    if len(raw) > limit:
                        raise ValueError("Source exceeds configured response size limit")
                    data = minimize_feed(raw, self.settings["evidence_char_limit"]) if kind == "rss" else raw
                    fresh_meta = {"fetched_at": now.isoformat(), "checked_epoch": now.timestamp(), "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"), "status": "ok", "url": url, "content_hash": hashlib.sha256(data).hexdigest()}
                    self._save(bodyfile, metafile, data, fresh_meta)
                    return data, fresh_meta
            except HTTPError as exc:
                if exc.code == 304 and meta and cached is not None:
                    fresh_meta = {**meta, "checked_epoch": now.timestamp(), "status": "not_modified"}
                    try:
                        self._save(bodyfile, metafile, cached, fresh_meta)
                        return cached, fresh_meta
                    except OSError as cache_error:
                        error = f"{type(cache_error).__name__}: {str(cache_error)[:300]}"
                else:
                    error = f"HTTP {exc.code}"
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if attempt + 1 < self.settings["fetch_attempts"]:
                time.sleep(min(2 ** attempt, 4))
        if meta and cached is not None and now.timestamp() - datetime.fromisoformat(meta["fetched_at"]).timestamp() < self.settings["cache_max_age_seconds"]:
            return cached, {**meta, "status": "stale_cache", "error": error, "url": url}
        raise RuntimeError(f"{error} ({url})")

    @staticmethod
    def _save(bodyfile, metafile, data, meta):
        # Called under get's per-URL lock. Unique sibling files also prevent
        # collisions with older runners still using the predictable .tmp name.
        for path, payload in [(bodyfile, data), (metafile, json.dumps(meta, indent=2).encode())]:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(payload)
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
