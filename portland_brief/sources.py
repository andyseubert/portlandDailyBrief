"""Sources supply evidence, never instructions or model URLs."""
import json
import re
from urllib.parse import urljoin
import feedparser
from bs4 import BeautifulSoup

from .fetching import plain
from .records import Evidence, canonical_url, digest, parse_time, safe_url


def normalize_rss(data: bytes, source: dict, fetched_at: str, limit: int):
    # Reject unsafe XML before handing it to the tolerant RSS normalizer.
    from defusedxml.ElementTree import fromstring
    fromstring(data)
    feed = feedparser.parse(data)
    if not feed.entries:
        raise ValueError("Feed has no entries")
    items, rejected = [], 0
    for entry in feed.entries:
        try:
            url = canonical_url(safe_url(entry.get("link", ""), source["allowed_hosts"]))
            title = plain(entry.get("title", ""), 300)
            if not title:
                raise ValueError("Missing title")
            # Never use content:encoded, video/audio, or paid bodies.
            text = plain(entry.get("summary", ""), limit)
            text = re.sub(r"\s+The post .*$", "", text)
            dt = parse_time(entry.get("published"))  # modified is not publication
            items.append(Evidence("e_" + digest(url)[:16], source["id"], source["name"], title, url, dt.isoformat() if dt else None, fetched_at, text))
        except (ValueError, TypeError):
            rejected += 1
    return items, rejected


def normalize_county(data: bytes, source: dict, fetched_at: str):
    soup = BeautifulSoup(data, "html.parser")
    items = []
    for card in soup.select(".card"):
        link, timestamp = card.select_one("h2 a[href]"), card.select_one("time[datetime]")
        if not link or not timestamp:
            continue
        url = canonical_url(safe_url(urljoin(source["url"], link["href"]), source["allowed_hosts"]))
        dt = parse_time(timestamp["datetime"])
        title = plain(link.get_text(" ", strip=True), 300)
        items.append(Evidence("e_" + digest(url)[:16], source["id"], source["name"], title, url, dt.isoformat() if dt else None, fetched_at, "", "Limited context: official listing headline only; insufficient evidence for a two-sentence summary."))
    if not items:
        raise ValueError("No dated county cards found; connector may need updating")
    return items, 0


def collect(fetcher, registry, settings):
    items, health = [], []
    for source in registry:
        if not source["enabled"]:
            health.append({"source_id": source["id"], "name": source["name"], "status": "disabled", "reason": source["access_notes"]})
            continue
        try:
            data, meta = fetcher.get(source["url"], "rss" if source["type"] == "rss" else "html")
            if source["type"] == "rss":
                records, rejected = normalize_rss(data, source, meta["fetched_at"], settings["evidence_char_limit"])
            elif source["type"] == "county_listing":
                records, rejected = normalize_county(data, source, meta["fetched_at"])
            else:
                raise ValueError("Unknown source adapter")
            # A stale fallback is visible but cannot silently enter a new brief.
            if meta["status"] != "stale_cache":
                items.extend(records)
            health.append({"source_id": source["id"], "name": source["name"], **meta, "item_count": len(records), "rejected_count": rejected, "last_validation_date": meta["fetched_at"][:10]})
        except Exception as exc:
            health.append({"source_id": source["id"], "name": source["name"], "status": "failed", "error": str(exc)[:350], "item_count": 0})
    return items, health
