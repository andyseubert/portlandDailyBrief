from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safe_url(url: str, allowed_hosts=None) -> str:
    p = urlsplit(url.strip())
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Only public HTTPS source URLs are accepted")
    if p.port not in (None, 443) or (allowed_hosts and p.hostname not in allowed_hosts):
        raise ValueError("Source URL host is outside the registry")
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def canonical_url(url: str) -> str:
    p = urlsplit(safe_url(url))
    params = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")
              and k.lower() not in {"fbclid", "gclid", "outputtype"}]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(sorted(params)), ""))


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


@dataclass
class Evidence:
    id: str
    source_id: str
    source_name: str
    title: str
    url: str
    published_at: str | None
    fetched_at: str
    text: str
    context: str = "Limited context: headline and RSS description only; full article not reviewed."
    content_hash: str = ""
    extraction_version: str = "1"

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = digest(self.title + "\n" + self.text)

    def to_dict(self):
        return asdict(self)


@dataclass
class Story:
    headline: str
    sentences: list[str]
    citation_ids: list[str]
    evidence_spans: list[str]
    flags: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def words(text: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "at", "for", "is", "are", "with", "after", "from", "as", "by", "says", "said", "portland", "oregon", "new", "news", "its", "it"}
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in stop}
