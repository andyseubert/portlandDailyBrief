#!/usr/bin/env python3
"""
Generate an HTML blog post from a Tucson Daily Brief markdown briefing file,
and rebuild both the homepage (index.html) and the full daily-brief archive
(briefings.html).

Usage:
    python generate_post.py path/to/tucson-brief-2026-02-18.md
    python generate_post.py --rebuild-homepage     # refresh only (no new post)
"""

import sys
import os
import re
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
POSTS_DIR = SITE_DIR / "posts"
MEETINGS_DIR = SITE_DIR / "meeting-watch"
REPORTS_DIR = SITE_DIR / "news-reports"
PUBLIC_RECORD_DIR = SITE_DIR / "public-record"
AROUND_TOWN_DIR = SITE_DIR / "around-town"   # development cases (rezonings/GPAs/variances)
INDEPTH_DIR = SITE_DIR / "in-depth"

# Feature flag: surface the Tools (Ask, Responsiveness Index) on the site.
# Flip to True once at least one tool ships. When False:
#   - The homepage Tools card row is hidden
#   - The Tools nav row (under streams nav) is hidden on every page
# Stub pages at /ask.html and /responsiveness.html still exist and work; they
# just aren't linked from the main nav until this flag flips.
SHOW_TOOLS = False

# Map source outlet names to their homepages (longest names first to match greedily)
SOURCE_URLS = {
    "Arizona Luminaria": "https://azluminaria.org",
    "AZ Luminaria": "https://azluminaria.org",
    "Arizona Daily Star": "https://tucson.com",
    "Arizona Mirror": "https://azmirror.com",
    "AZPM": "https://www.azpm.org",
    "Cronkite News": "https://cronkitenews.azpbs.org",
    "Inside Tucson Business": "https://www.insidetucsonbusiness.com",
    "KGUN 9": "https://www.kgun9.com",
    "NWS Tucson": "https://forecast.weather.gov/MapClick.php?CityName=Tucson&state=AZ",
    "SaddleBrooke Progress": "https://www.saddlebrookeprogress.com",
    "This Is Tucson": "https://thisistucson.com",
    "Tucson Agenda": "https://tucsonagenda.substack.com",
    "Tucson Foodie": "https://www.tucsonfoodie.com",
    "Tucson Local Media": "https://www.tucsonlocalmedia.com",
    "Explorer News": "https://www.tucsonlocalmedia.com",
    "CALÓ News": "https://calonews.org",
}


# ---------------------------------------------------------------------------
# Date and slug helpers
# ---------------------------------------------------------------------------

def parse_date_from_filename(filepath: str) -> datetime:
    """Extract date from a filename like tucson-brief-2026-02-18.md."""
    basename = Path(filepath).stem
    match = re.search(r"(\d{4}-\d{2}-\d{2})", basename)
    if not match:
        print(f"Error: could not extract date from filename '{basename}'", file=sys.stderr)
        sys.exit(1)
    return datetime.strptime(match.group(1), "%Y-%m-%d")


def format_date_long(dt: datetime) -> str:
    """February 18, 2026"""
    return dt.strftime("%B %-d, %Y")


def format_date_short(dt: datetime) -> str:
    """Feb 18, 2026"""
    return dt.strftime("%b %-d, %Y")


def format_date_eyebrow(dt: datetime) -> str:
    """TUESDAY, MAY 11 — used for the featured Today's Brief card."""
    return dt.strftime("%A, %B %-d").upper()


def post_slug(dt: datetime) -> str:
    """2026-02-18"""
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def escape(text: str) -> str:
    """Escape HTML special characters."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def inline_format(text: str) -> str:
    """Handle bold (**text**) within already-safe-ish content."""
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text


# ---------------------------------------------------------------------------
# Topic detection — high-interest civic topics
# ---------------------------------------------------------------------------
# Some auto-published items deserve more than a generic card. Data centers, in
# particular, are a live Southern AZ flashpoint (power, water, growth): Marana
# approved a 600-acre hyperscale rezoning in Jan 2026 over packed-chamber
# opposition, and more applications keep arriving. This block lets the
# dev-watch pollers (Marana, Oro Valley, future Tucson) flag a case by topic so
# it gets an on-card badge AND a distinct Telegram alert (wired in
# check_agendas.sh) instead of being lost in the routine development feed.
#
# Precision over recall: a match elevates a card and pings a human, so a false
# positive costs reader trust. Keywords stay tight and specific. "technology /
# computing campus" are included because they're the standard euphemism these
# projects are branded with (the Marana "Ranch House" case literally reads
# "Technology Campus, Data Center and Medium Density Residential").
TOPIC_DEFS = {
    "data-center": {
        "label": "Data Center Watch",
        "keywords": [
            "data center", "data centre", "datacenter", "data-center",
            "hyperscale", "server farm",
            "technology campus", "computing campus", "compute campus",
        ],
    },
}


def detect_topics(*texts: str) -> list[str]:
    """Return topic keys whose keywords appear in the given text(s).

    Case-insensitive substring match over the concatenated, lowercased text.
    Returns a list (stable TOPIC_DEFS order) so callers can flag a card with
    one or more topics; empty when nothing matches."""
    blob = " ".join(t for t in texts if t).lower()
    return [key for key, spec in TOPIC_DEFS.items()
            if any(kw in blob for kw in spec["keywords"])]


def topic_label(key: str) -> str:
    """Reader-facing label for a topic key (falls back to the key itself)."""
    return TOPIC_DEFS.get(key, {}).get("label", key)


def topic_badge_html(keys: list[str]) -> str:
    """Badge row for an elevated-topic card; empty string when no topics."""
    if not keys:
        return ""
    badges = "".join(
        f'<span class="topic-flag topic-flag--{escape(k)}">'
        f'{escape(topic_label(k))}</span>'
        for k in keys
    )
    return f'<p class="topic-flags">{badges}</p>'


# ---------------------------------------------------------------------------
# Briefing markdown → HTML
# ---------------------------------------------------------------------------

def md_to_html(text: str) -> str:
    """Convert briefing markdown to HTML."""
    lines = text.strip().split("\n")
    html_parts = []
    i = 0

    # Skip first line (title — we use the date from filename instead)
    if lines and lines[0].startswith("Tucson Daily Brief"):
        i = 1

    # Strip the trailing operational footer block. Every brief — both the
    # deterministic generate_brief.py output and the older OpenClaw-era variants —
    # ends with a block whose first line begins with "Briefing saved:", followed
    # by free-form source counts / skip notes / "Generated deterministically…" /
    # manual "Edited …" notes. The wording of those follow-on lines varies, so we
    # anchor on the stable "Briefing saved:" marker and drop everything from there
    # to the end (it's metadata for the Telegram message, not for publication).
    # Any blank line or separator immediately preceding it is dropped too, so no
    # dangling <hr> is left behind.
    end = len(lines)
    for j in range(len(lines) - 1, -1, -1):
        # tolerate bold-wrapped markers like "**Briefing saved:**"
        if lines[j].strip().lstrip("*").startswith("Briefing saved:"):
            end = j
            break
    while end > 0:
        prev = lines[end - 1].strip()
        if (not prev) or re.match(r"^[─\-]{3,}$", prev):
            end -= 1
        else:
            break

    while i < end:
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        if re.match(r"^[─\-]{3,}$", line):
            html_parts.append("<hr>")
            i += 1
            continue

        if line.startswith("\U0001f4f0") or line.startswith("\U0001f4c4"):
            source_text = line.replace("\U0001f4f0", "").replace("\U0001f4c4", "").strip()
            html_parts.append(f'<p class="source">{linkify_sources(source_text)}</p>')
            i += 1
            continue

        # Section headers are short, emoji-prefixed labels (e.g. "🏛️ Government").
        # An emoji-prefixed line that contains bold markdown is an inline callout
        # (e.g. "⚠️ **Extreme Heat Watch…**"), NOT a section header — let it fall
        # through to the paragraph branch so the **bold** is converted properly.
        if (re.match(r"^[\U0001f300-\U0001faff☀-➿️]", line)
                and "**" not in line):
            html_parts.append(f"<h2>{escape(line)}</h2>")
            i += 1
            continue

        para_lines = [line]
        i += 1
        while i < end:
            next_line = lines[i].strip()
            if (not next_line or
                    next_line.startswith("\U0001f4f0") or
                    next_line.startswith("\U0001f4c4") or
                    re.match(r"^[─\-]{3,}$", next_line) or
                    re.match(r"^[\U0001f300-\U0001faff☀-➿️]", next_line)):
                break
            para_lines.append(next_line)
            i += 1

        para_text = " ".join(para_lines)
        para_html = inline_format(para_text)
        html_parts.append(f"<p>{para_html}</p>")

    return "\n".join(html_parts)


def linkify_sources(text: str) -> str:
    """Convert source citation text to HTML with hyperlinks."""
    def md_link_to_html(m):
        name = m.group(1)
        url = m.group(2)
        return f'<a href="{escape(url)}">{escape(name)}</a>'

    result = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', md_link_to_html, text)

    for name in sorted(SOURCE_URLS, key=len, reverse=True):
        url = SOURCE_URLS[name]
        result = re.sub(
            r'(?<![">])' + re.escape(name) + r'(?![^<]*</a>)',
            f'<a href="{url}">{escape(name)}</a>',
            result,
        )
    return result


def extract_lede(text: str) -> str:
    """Pull the first story headline for the index listing."""
    for line in text.strip().split("\n"):
        match = re.search(r"\*\*(.+?)\*\*", line)
        if match:
            headline = match.group(1)
            if headline.endswith("."):
                headline = headline[:-1]
            if len(headline) > 120:
                headline = headline[:117] + "..."
            return headline
    return ""


# ---------------------------------------------------------------------------
# Shared page chrome — used by every index page on the site
# ---------------------------------------------------------------------------

ANALYTICS_HTML = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT,WONK@9..144,300..900,0..100,0..1&family=Newsreader:ital,opsz,wght@0,6..72,300..700;1,6..72,300..700&display=swap">
<noscript><style>.section-head__rule path { stroke-dashoffset: 0 !important; }</style></noscript>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-MEYSB9GYF2"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-MEYSB9GYF2');
</script>"""

# ---------------------------------------------------------------------------
# SEO / social meta — shared by every renderer (added 2026-07-11)
# ---------------------------------------------------------------------------

SITE_URL = "https://tucsondailybrief.com"
OG_IMAGE_URL = f"{SITE_URL}/assets/og-default.png"
RSS_URL = f"{SITE_URL}/rss.xml"
SITE_NAME = "Tucson Daily Brief"


def seo_head_html(*, title: str, description: str, path: str,
                  og_type: str = "website",
                  published: datetime | None = None,
                  jsonld: dict | None = None) -> str:
    """Meta description + canonical + Open Graph/Twitter cards + optional
    JSON-LD, for one page. `title`/`description` are plain text (unescaped);
    `path` is the site-relative path ("" for the homepage)."""
    url = f"{SITE_URL}/{path}" if path else f"{SITE_URL}/"
    t, d = escape(title), escape(description)
    lines = [
        f'<meta name="description" content="{d}">',
        f'<link rel="canonical" href="{url}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{OG_IMAGE_URL}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
        f'<meta name="twitter:image" content="{OG_IMAGE_URL}">',
        f'<link rel="alternate" type="application/rss+xml" title="{SITE_NAME}" href="{RSS_URL}">',
    ]
    if published is not None:
        lines.append(f'<meta property="article:published_time" content="{published.strftime("%Y-%m-%d")}">')
    if jsonld:
        lines.append('<script type="application/ld+json">'
                     + json.dumps(jsonld, ensure_ascii=False) + "</script>")
    return "\n".join(lines)


def news_article_jsonld(*, headline: str, path: str, published: datetime,
                        description: str = "") -> dict:
    """schema.org NewsArticle for an article page."""
    url = f"{SITE_URL}/{path}"
    d = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": headline[:110],
        "url": url,
        "mainEntityOfPage": url,
        "datePublished": published.strftime("%Y-%m-%d"),
        "author": [{"@type": "Person", "name": "Nicholas De Leon"}],
        "publisher": {
            "@type": "NewsMediaOrganization",
            "name": SITE_NAME,
            "url": f"{SITE_URL}/",
            "logo": {"@type": "ImageObject", "url": OG_IMAGE_URL},
        },
        "image": [OG_IMAGE_URL],
    }
    if description:
        d["description"] = description
    return d


def derive_description(body_html: str, max_len: int = 160) -> str:
    """Plain-text meta description from the first substantive paragraph of an
    article body fragment. Skips chrome-y paragraphs (post-meta, back links,
    kickers) and very short ones."""
    for m in re.finditer(r"<p(\s[^>]*)?>(.*?)</p>", body_html, re.DOTALL):
        attrs = m.group(1) or ""
        if re.search(r"post-meta|back-link|brief-kicker|brief-weekday|masthead|post-lede|section-intro", attrs):
            continue
        text = _unescape_and_truncate(m.group(2), max_len=0)
        if len(text) < 40:
            continue
        if max_len and len(text) > max_len:
            text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
        return text
    return ""


def extract_headline(md_text: str) -> str:
    """First real story headline from a briefing markdown — skips weather
    day-labels/temps (which lead the brief on weather-alert days), mirroring
    collect_existing_posts()."""
    for line in md_text.strip().split("\n"):
        for match in re.finditer(r"\*\*(.+?)\*\*", line):
            headline = match.group(1).strip()
            if _is_weather_label(headline):
                continue
            return _clamp_weather_alert(headline).rstrip(".")
    return ""


# Inline SVGs reused across the site
SUNRAY_SVG = """<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
<circle cx="7" cy="7" r="2.2" fill="currentColor"/>
<g stroke="currentColor" stroke-width="1.2" stroke-linecap="round">
<line x1="7" y1="0.5" x2="7" y2="2.5"/><line x1="7" y1="11.5" x2="7" y2="13.5"/>
<line x1="0.5" y1="7" x2="2.5" y2="7"/><line x1="11.5" y1="7" x2="13.5" y2="7"/>
<line x1="2.4" y1="2.4" x2="3.6" y2="3.6"/><line x1="10.4" y1="10.4" x2="11.6" y2="11.6"/>
<line x1="2.4" y1="11.6" x2="3.6" y2="10.4"/><line x1="10.4" y1="3.6" x2="11.6" y2="2.4"/>
</g></svg>"""

# Larger sun motif for the homepage featured area — same geometry as SUNRAY_SVG
# but scaled up with 12 rays of varied length so it reads as an editorial mark
# rather than a tiny dingbat. Uses currentColor so the CSS sets the tint.
FEATURED_SUN_SVG = """<svg class="featured__sun" viewBox="0 0 160 160" aria-hidden="true">
<circle cx="80" cy="80" r="30" fill="currentColor" fill-opacity="0.85"/>
<g stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-opacity="0.75">
<line x1="80" y1="8" x2="80" y2="30"/>
<line x1="80" y1="130" x2="80" y2="152"/>
<line x1="8" y1="80" x2="30" y2="80"/>
<line x1="130" y1="80" x2="152" y2="80"/>
<line x1="29" y1="29" x2="44" y2="44"/>
<line x1="116" y1="116" x2="131" y2="131"/>
<line x1="29" y1="131" x2="44" y2="116"/>
<line x1="116" y1="44" x2="131" y2="29"/>
<line x1="50" y1="14" x2="56" y2="32"/>
<line x1="110" y1="14" x2="104" y2="32"/>
<line x1="50" y1="146" x2="56" y2="128"/>
<line x1="110" y1="146" x2="104" y2="128"/>
</g></svg>"""

ARROW_SVG = """<svg width="18" height="14" viewBox="0 0 18 14" aria-hidden="true"><path d="M1 7h15M11 2l5 5-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>"""

ARROW_LEFT_SVG = """<svg width="18" height="14" viewBox="0 0 18 14" aria-hidden="true"><path d="M17 7H2M7 2L2 7l5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>"""

HAND_RULE_SVG = """<svg class="section-head__rule" width="280" height="14" viewBox="0 0 280 14" fill="none" aria-hidden="true">
<path d="M2 8 C 30 4, 80 12, 130 7 S 230 4, 278 9" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" fill="none"/>
</svg>"""

# Vanilla JS for scroll-triggered hand-drawn underlines
SCROLL_TRIGGER_JS = """<script>
(function(){if(!('IntersectionObserver' in window)){document.querySelectorAll('.section-head__rule').forEach(el=>el.classList.add('in-view'));return;}
const io=new IntersectionObserver((entries)=>{entries.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in-view');io.unobserve(e.target);}});},{rootMargin:'0px 0px -8% 0px',threshold:0.1});
document.querySelectorAll('.section-head__rule').forEach(el=>io.observe(el));})();
</script>"""

def footer_html(path_prefix: str = "") -> str:
    """Site-wide footer. `path_prefix` is "" for root pages, "../" for nested pages."""
    return f"""<div class="footer-row">
<p class="footer-row__byline">By Nicholas De Leon, in Tucson.</p>
<p class="footer-row__links">
<a href="{path_prefix}about.html">About</a>
<a href="https://podcasts.apple.com/us/podcast/tucson-daily-brief/id1878173070">Apple Podcasts</a>
<a href="https://www.youtube.com/@tucsondailybrief">YouTube</a>
<a href="https://www.facebook.com/tucsondailybrief">Facebook</a>
<a href="https://www.instagram.com/tucsondailybrief">Instagram</a>
<a href="https://bsky.app/profile/tucsondailybrief.com">Bluesky</a>
<a href="https://x.com/tusdailybrief">X</a>
<a href="https://www.linkedin.com/in/nicholas-de-leon-3b5b6a9">LinkedIn</a>
<a href="mailto:nicholas@tucsondailybrief.com">Email</a>
</p>
</div>"""

# Decorative SVG for the subscribe panel: sun, mountains, saguaro silhouette
SUBSCRIBE_ART_SVG = """<svg viewBox="0 0 240 240" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#d97048" stop-opacity="0"/>
<stop offset="100%" stop-color="#8c3a1f" stop-opacity="0.5"/>
</linearGradient></defs>
<circle cx="190" cy="60" r="36" fill="#f5f0e6" opacity="0.92"/>
<g stroke="#faf4e8" stroke-width="1.5" stroke-linecap="round" opacity="0.55">
<line x1="190" y1="6" x2="190" y2="20"/><line x1="190" y1="100" x2="190" y2="114"/>
<line x1="136" y1="60" x2="150" y2="60"/><line x1="230" y1="60" x2="244" y2="60"/>
<line x1="152" y1="22" x2="162" y2="32"/><line x1="218" y1="98" x2="228" y2="108"/>
<line x1="152" y1="98" x2="162" y2="88"/><line x1="218" y1="22" x2="228" y2="12"/>
</g>
<path d="M -10 200 L 30 150 L 60 175 L 90 130 L 130 165 L 170 110 L 210 145 L 250 175 L 250 240 L -10 240 Z" fill="#3d3029" opacity="0.6"/>
<path d="M -10 230 L 50 195 L 100 215 L 150 185 L 200 205 L 250 190 L 250 240 L -10 240 Z" fill="#251c17" opacity="0.55"/>
<g transform="translate(60 130)" fill="#3d3029" opacity="0.85">
<rect x="-4" y="0" width="8" height="80" rx="3"/>
<rect x="-4" y="20" width="3" height="22" rx="1.5"/>
<rect x="1" y="36" width="3" height="16" rx="1.5"/>
<rect x="-12" y="22" width="8" height="3" rx="1.5"/>
<rect x="-9" y="20" width="3" height="14" rx="1.5"/>
<rect x="4" y="32" width="8" height="3" rx="1.5"/>
<rect x="9" y="22" width="3" height="14" rx="1.5"/>
</g>
<rect x="0" y="0" width="240" height="240" fill="url(#sky)"/>
</svg>"""

SUBSCRIBE_PANEL_HTML = f"""<div class="subscribe__panel" id="subscribe">
<div class="subscribe__art">{SUBSCRIBE_ART_SVG}</div>
<div class="subscribe__body">
<p class="subscribe__eyebrow">TDB Weekly</p>
<h2 class="subscribe__title">A warm Sunday-morning roundup of Tucson, in your inbox.</h2>
<p>The week&rsquo;s most important Tucson developments, what local government decided, and what&rsquo;s coming next &mdash; plus The Tucson Mini, a 5&times;5 crossword built just for subscribers.</p>
<form action="https://buttondown.email/api/emails/embed-subscribe/tucsondailybrief" method="post" target="_blank" class="subscribe__form">
<input class="subscribe__input" type="email" name="email" placeholder="you@somewhere.com" aria-label="Email address" required>
<button class="subscribe__button" type="submit">Subscribe</button>
</form>
<p class="subscribe__fine">Free. Sunday mornings. Unsubscribe anytime.</p>
</div>
</div>"""

# Bluesky-powered comments on article pages (SOCIAL-AUTOPOST.md Part 3). The
# section ships hidden; /assets/bsky-comments.js reveals it only when the
# page's canonical URL appears in /assets/bluesky-posts.json (exported by
# social/bluesky_poster.py from its posting ledger) — so pages that never got
# a Bluesky post render nothing, and there is no backend. Every article
# renderer interpolates this immediately before its closing </article>.
BLUESKY_COMMENTS_HTML = """<section class="bsky-comments" id="bsky-comments" hidden>
<h2 class="bsky-comments__head">Comments</h2>
<p class="bsky-comments__meta" id="bsky-comments-meta"></p>
<div class="bsky-comments__list" id="bsky-comments-list"></div>
<p class="bsky-comments__cta"><a id="bsky-comments-link" href="https://bsky.app/profile/tucsondailybrief.com" target="_blank" rel="noopener">Join the conversation on Bluesky</a></p>
</section>
<script defer src="/assets/bsky-comments.js"></script>"""

# Header used by every page on the site
def site_header_html(h1: bool = False) -> str:
    # The wordmark is an <h1> only on the homepage; every other page reserves
    # its <h1> for the page's own title (one h1 per page). The homepage (the
    # only h1=True caller) uses the 1120px home container so the masthead lines
    # up with the edition/lead grid below it.
    tag = "h1" if h1 else "p"
    container = "container container--home" if h1 else "container"
    return f"""<header class="masthead">
<div class="{container}">
<div class="masthead__row">
<p class="masthead__kicker">From the Old Pueblo</p>
<{tag} class="masthead__wordmark"><a href="./">Tucson Daily Brief</a></{tag}>
<p class="masthead__tagline">The Tucson news you&rsquo;d otherwise miss, by Nicholas De Leon.</p>
</div>
</div>
</header>"""

# Subpage header (for individual posts) — relative paths back to root
def post_header_html() -> str:
    return """<header class="masthead">
<div class="container">
<div class="masthead__row">
<p class="masthead__kicker">From the Old Pueblo</p>
<p class="masthead__wordmark"><a href="../">Tucson Daily Brief</a></p>
<p class="masthead__tagline">The Tucson news you&rsquo;d otherwise miss, by Nicholas De Leon.</p>
</div>
</div>
</header>"""


# ---------------------------------------------------------------------------
# Section nav — five top-level hubs, some with a contextual second row.
#
# The site ships NO JavaScript, so hubs are landing pages + a contextual
# sub-row (not dropdowns). `_NAV` is the single source of truth:
#   (key, label, href, children)  — children is None for a plain section/tool,
#   or a list of (key, label, href) for a hub.
#
# `active` (passed by every renderer) marks the current page. It may be a
# top-level key OR a child key OR an alias (see `_NAV_ALIASES`). The resolver
# lights up the matching top-level item and, if the active page is a hub child,
# renders the hub's children as a second row.
#
# Display names changed in the 2026-06-24 IA reorg; URLs/dirs/active-keys were
# preserved (same trick as the Public Record → Spotted rename) so nothing
# breaks. See IA-REORG.md.
# ---------------------------------------------------------------------------
_NAV = [
    ("briefings", "Daily Briefs", "briefings.html", None),
    ("local-government", "Local Government", "local-government.html", [
        ("meetings", "What to Watch", "meeting-watch.html"),
        ("reports", "What They Decided", "news-reports.html"),
    ]),
    ("around-town", "Around Town", "around-town.html", None),
    ("indepth", "In Depth", "in-depth.html", None),
    ("ask", "ChatTDB", "ask.html", None),
]

# Pages that aren't first-class nav items but belong under a hub. Maps an
# `active` key → (top_level_key, sub_key_or_None) so e.g. the still-live
# public-record.html and individual filing pages light up "Around Town".
_NAV_ALIASES = {
    "record": ("around-town", None),
}

_TOOLS = [
    ("responsiveness", "Responsiveness", "responsiveness.html"),
]


def _resolve_active(active: str) -> tuple[str | None, str | None]:
    """Return (top_level_key, sub_key) for the active page, or (None, None)."""
    for key, _label, _href, children in _NAV:
        if active == key:
            return key, None
        if children:
            for ck, _cl, _ch in children:
                if active == ck:
                    return key, ck
    if active in _NAV_ALIASES:
        return _NAV_ALIASES[active]
    return None, None


def section_nav_html(active: str = "", path_prefix: str = "") -> str:
    """Render the section nav. `active` marks the current page (rendered as
    plain text, no link). `path_prefix` is "" for site root or "../" for nested
    pages. A contextual second row appears only when the active page lives under
    a hub. The Tools row is gated by the SHOW_TOOLS flag."""
    top_active, sub_active = _resolve_active(active)

    def link_or_text(key, label, href, is_active):
        if is_active:
            return f'<span class="active">{label}</span>'
        return f'<a href="{path_prefix}{href}">{label}</a>'

    streams = "".join(
        link_or_text(k, l, h, k == top_active) for k, l, h, _c in _NAV
    )

    # Contextual sub-row: the active hub's children (if it has any).
    sub_row = ""
    for k, _l, _h, children in _NAV:
        if k == top_active and children:
            subs = " &middot; ".join(
                link_or_text(ck, cl, ch, ck == sub_active) for ck, cl, ch in children
            )
            sub_row = f'\n<div class="subsection-nav">{subs}</div>'
            break

    if SHOW_TOOLS:
        tools = " &middot; ".join(link_or_text(*t, t[0] == active) for t in _TOOLS)
        tools_row = f'\n<div class="tools-nav">{tools}</div>'
    else:
        tools_row = ""

    return f"""<nav class="section-nav">
<div class="nav-main">
<div class="streams-nav">{streams}</div>
<a class="btn-sub" href="{path_prefix}#subscribe">Subscribe free</a>
</div>{sub_row}{tools_row}
</nav>"""


# ---------------------------------------------------------------------------
# Post rendering (individual daily-brief page)
# ---------------------------------------------------------------------------

def render_post(date: datetime, body_html: str, headline: str = "") -> str:
    """Render a daily-brief individual post page in the new editorial language.
    `headline` (the day's first real story headline, plain text) feeds the
    <title>, meta description, and NewsArticle structured data."""
    date_long = format_date_long(date)
    slug = post_slug(date)
    path = f"posts/{slug}.html"
    weekday = date.strftime("%A")
    css_path = "../style.css"
    home_href = "../"
    back_href = "../briefings.html"

    if headline:
        short = headline if len(headline) <= 80 else headline[:77].rsplit(" ", 1)[0] + "..."
        title_text = f"{short} — Tucson Daily Brief, {date_long}"
        description = f"{headline}, plus the rest of the day's Tucson news — local government, public safety, business, and events."
    else:
        title_text = f"{date_long} — Tucson Daily Brief"
        description = f"The Tucson news for {date_long} — local government, public safety, business, and events."
    if len(description) > 300:
        description = description[:297].rsplit(" ", 1)[0] + "…"
    seo = seo_head_html(
        title=title_text, description=description, path=path,
        og_type="article", published=date,
        jsonld=news_article_jsonld(
            headline=headline or f"Tucson Daily Brief — {date_long}",
            path=path, published=date, description=description),
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title_text)}</title>
{seo}
<link rel="stylesheet" href="{css_path}">
{ANALYTICS_HTML}
</head>
<body>

{post_header_html()}

<div class="container">
{section_nav_html(active="briefings", path_prefix=home_href)}
</div>

<main>
<div class="container container--reading">
<a class="back-link" href="{back_href}">{ARROW_LEFT_SVG} All Daily Briefs</a>

<article id="{slug}" class="brief">
<header class="brief-header">
<p class="brief-kicker">{SUNRAY_SVG} Daily Brief</p>
<h1 class="brief-date">{format_date_long(date)}</h1>
<p class="brief-weekday">{weekday}</p>
</header>
<div class="brief-body">
{body_html}
</div>
{BLUESKY_COMMENTS_HTML}
</article>

<!--PREVNEXT-START--><!--PREVNEXT-END-->
</div>
</main>

<div class="container">
<div style="margin-bottom:var(--gap-xl)">{SUBSCRIBE_PANEL_HTML}</div>
{footer_html(path_prefix=home_href)}
</div>

{SCROLL_TRIGGER_JS}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Prev/next edition navigation (habit loop between daily briefs)
# ---------------------------------------------------------------------------

# render_post() emits an empty marker pair; restamp_edition_nav() fills it on
# every rebuild, so the previous newest brief gains its "next" link the moment a
# newer brief publishes. Self-healing — no per-post source needed to refresh it.
PREVNEXT_RE = re.compile(r"<!--PREVNEXT-START-->.*?<!--PREVNEXT-END-->", re.DOTALL)


def _edition_nav_html(older: dict | None, newer: dict | None) -> str:
    """Prev/next block for a daily brief. `older`/`newer` are post dicts (or
    None at the ends). Posts sit side-by-side in posts/, so hrefs are bare slugs."""
    if not older and not newer:
        return ""
    prev_link = ""
    if older:
        prev_link = (
            f'<a class="edition-nav__link edition-nav__prev" href="{older["slug"]}.html" rel="prev">'
            f'<span class="edition-nav__dir">{ARROW_LEFT_SVG} Previous brief</span>'
            f'<span class="edition-nav__date">{format_date_long(older["date"])}</span></a>'
        )
    next_link = ""
    if newer:
        next_link = (
            f'<a class="edition-nav__link edition-nav__next" href="{newer["slug"]}.html" rel="next">'
            f'<span class="edition-nav__dir">Next brief {ARROW_SVG}</span>'
            f'<span class="edition-nav__date">{format_date_long(newer["date"])}</span></a>'
        )
    return f'<nav class="edition-nav" aria-label="More daily briefs">{prev_link}{next_link}</nav>'


def restamp_edition_nav(posts: list[dict]) -> None:
    """Refresh the prev/next block inside every daily-brief page. `posts` is the
    newest-first list from collect_existing_posts(). Idempotent; only rewrites a
    file whose nav actually changed."""
    n_posts = len(posts)
    for i, p in enumerate(posts):
        newer = posts[i - 1] if i > 0 else None          # chronologically later
        older = posts[i + 1] if i + 1 < n_posts else None  # chronologically earlier
        f = POSTS_DIR / f"{p['slug']}.html"
        if not f.exists():
            continue
        html = f.read_text()
        replacement = f"<!--PREVNEXT-START-->{_edition_nav_html(older, newer)}<!--PREVNEXT-END-->"
        new_html, count = PREVNEXT_RE.subn(replacement, html)
        if count and new_html != html:
            f.write_text(new_html)


# ---------------------------------------------------------------------------
# Cross-section data collection (for the homepage cross-stream cards)
# ---------------------------------------------------------------------------

def _is_weather_label(s: str) -> bool:
    """True for weather forecast day-labels/temps (e.g. 'Today (Mon...):', '108°F'),
    which must never be chosen as a brief's featured headline."""
    s = s.strip()
    return (not s) or s.endswith(":") or ("°" in s)


# The weather section's bolded alert callout, e.g. "⚠️ ALERTS: An Extreme Heat
# Warning is in effect …". On multi-hazard days this runs to several hundred
# characters, so it can lead the page but must not run down it.
_ALERT_BLOCK_RE = re.compile(r"^\W*ALERTS?\b", re.I)
# A sentence ends at .!? followed by whitespace + a capital — but "8 p.m. Monday"
# and "Sgt. Ruiz" are not sentence ends.
_ABBREVS = ("a.m", "p.m", "Sgt", "Lt", "Ave", "Blvd", "Rd", "St", "Dr", "Mt")
_SENT_END_RE = re.compile(r"[.!?](?=\s+[A-Z“\"(])")


def _first_sentence(s: str, hard_cap: int = 180) -> str:
    """First sentence of s, abbreviation-aware. Falls back to a word-boundary
    truncation if the text has no usable sentence break."""
    s = s.strip()
    for m in _SENT_END_RE.finditer(s):
        head = s[: m.end()]
        if any(head[:-1].endswith(a) for a in _ABBREVS):
            continue
        if len(head) <= hard_cap:
            return head
        break
    if len(s) <= hard_cap:
        return s
    return s[:hard_cap].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _clamp_weather_alert(s: str) -> str:
    """Trim the weather ALERTS callout down to its lead sentence. Any other
    headline passes through untouched."""
    s = s.strip()
    return _first_sentence(s) if _ALERT_BLOCK_RE.match(s) else s


def collect_existing_posts() -> list[dict]:
    """Scan posts/ directory for existing HTML files and extract metadata."""
    posts = []
    if not POSTS_DIR.exists():
        return posts
    for f in POSTS_DIR.glob("*.html"):
        match = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
        if not match:
            continue
        dt = datetime.strptime(match.group(1), "%Y-%m-%d")
        content = f.read_text()
        lede_match = re.search(r'<p class="post-lede"[^>]*>(.+?)</p>', content)
        if lede_match:
            lede = lede_match.group(1)
        else:
            # First real headline — skip weather day-labels/temps, which lead the
            # brief on days with an active weather alert.
            lede = ""
            for sm in re.finditer(r"<strong>(.+?)</strong>", content):
                cand = sm.group(1).strip()
                if _is_weather_label(cand):
                    continue
                lede = _clamp_weather_alert(cand).rstrip(".")
                break
        posts.append({"date": dt, "slug": post_slug(dt), "lede": lede})
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def collect_brief_rundown(slug: str, n: int = 4) -> list[str]:
    """The top N story headlines from a brief's HTML, for the homepage
    'This morning in Tucson' rundown. Skips weather day-labels the same way
    collect_existing_posts() does. Derived from the published brief, never
    asked of a model."""
    path = POSTS_DIR / f"{slug}.html"
    if not path.exists():
        return []
    content = path.read_text()
    items = []
    for sm in re.finditer(r"<strong>(.+?)</strong>", content):
        cand = _unescape_and_truncate(sm.group(1), max_len=0).strip()
        if not cand or _is_weather_label(cand):
            continue
        items.append(_clamp_weather_alert(cand).rstrip("."))
        if len(items) >= n:
            break
    return items


def _newest_html_in(directory: Path) -> Path | None:
    """Return the newest dated HTML file in a section directory, or None."""
    if not directory.exists():
        return None
    candidates = []
    for f in directory.glob("*.html"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
        if not m:
            continue
        dt = datetime.strptime(m.group(1), "%Y-%m-%d")
        candidates.append((dt, f))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _unescape_and_truncate(html_snippet: str, max_len: int = 110) -> str:
    """Strip tags, decode HTML entities, optionally truncate. Returns plain text."""
    text = re.sub(r"<[^>]+>", "", html_snippet).strip()
    text = (text.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&mdash;", "—")
                .replace("&middot;", "·")
                .replace("&rsquo;", "’")
                .replace("&lsquo;", "‘")
                .replace("&times;", "×"))
    text = re.sub(r"\s+", " ", text)
    if max_len and len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def _article_h1(content: str) -> str | None:
    """Pull the first <h1> inside the <article> tag."""
    m = re.search(r'<article[^>]*>.*?<h1>(.+?)</h1>', content, re.DOTALL)
    return _unescape_and_truncate(m.group(1), max_len=0) if m else None


def collect_latest_meeting() -> dict | None:
    """Scan meeting-watch/ for the newest preview. Returns a card dict or None."""
    f = _newest_html_in(MEETINGS_DIR)
    if not f:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
    dt = datetime.strptime(m.group(1), "%Y-%m-%d")
    content = f.read_text()
    title = _article_h1(content) or f.stem
    # Lede: paragraph right after the "Meeting Preview" h2 (editorial overview)
    lede = ""
    lede_match = re.search(
        r'<h2>\s*Meeting Preview\s*</h2>\s*<p>(.+?)</p>',
        content, re.DOTALL)
    if lede_match:
        lede = _unescape_and_truncate(lede_match.group(1))
    return {
        "date": dt,
        "title": title,
        "lede": lede,
        "href": f"meeting-watch/{f.stem}.html",
    }


def collect_latest_report() -> dict | None:
    """Scan news-reports/ for the newest report."""
    f = _newest_html_in(REPORTS_DIR)
    if not f:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
    dt = datetime.strptime(m.group(1), "%Y-%m-%d")
    content = f.read_text()
    title = _article_h1(content) or f.stem
    # Lede: first <p><strong>...</strong></p> after the h1 (the bold lede paragraph)
    lede = ""
    lede_match = re.search(
        r'<article[^>]*>.*?<h1>.+?</h1>\s*<p><strong>(.+?)</strong></p>',
        content, re.DOTALL)
    if lede_match:
        lede = _unescape_and_truncate(lede_match.group(1))
    return {
        "date": dt,
        "title": title,
        "lede": lede,
        "href": f"news-reports/{f.stem}.html",
    }


def _collect_at_dir(directory: Path, href_prefix: str, kind: str, kind_label: str) -> list[dict]:
    """Collect Around Town items from one source dir (filings or development).
    Both render a `.filing-subtitle` lede + an <article> <h1>, so extraction is
    uniform. Date comes from the YYYY-MM-DD in the filename."""
    items = []
    if not directory.exists():
        return items
    pattern = "liquor-*.html" if kind == "new-business" else "*.html"
    for f in directory.glob(pattern):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
        if not m:
            continue
        dt = datetime.strptime(m.group(1), "%Y-%m-%d")
        content = f.read_text()
        title = _article_h1(content) or f.stem
        lede = ""
        lede_match = re.search(r'<p class="filing-subtitle">(.+?)</p>', content)
        if lede_match:
            lede = _unescape_and_truncate(lede_match.group(1))
        # Carry any high-interest topic flags (e.g. data-center) onto the card.
        topics = sorted(set(re.findall(r'topic-flag--([a-z0-9-]+)', content)))
        items.append({
            "date": dt,
            "title": title,
            "lede": lede,
            "href": f"{href_prefix}/{f.stem}.html",
            "kind": kind,
            "kind_label": kind_label,
            "topics": topics,
        })
    return items


def collect_around_town_items() -> list[dict]:
    """Merged Around Town feed: new-business/liquor filings (public-record/) +
    development cases (around-town/), newest first."""
    items = (_collect_at_dir(PUBLIC_RECORD_DIR, "public-record", "new-business", "New business")
             + _collect_at_dir(AROUND_TOWN_DIR, "around-town", "development", "Development"))
    items.sort(key=lambda x: x["date"], reverse=True)
    return items


def collect_latest_filing() -> dict | None:
    """Newest item across all of Around Town (filings + development)."""
    items = collect_around_town_items()
    return items[0] if items else None


def collect_latest_indepth() -> dict | None:
    """Scan in-depth/ for the newest published feature (date from <meta published>)."""
    if not INDEPTH_DIR.exists():
        return None
    candidates = []
    for f in INDEPTH_DIR.glob("*.html"):
        content = f.read_text()
        m = re.search(r'<meta name="published" content="(\d{4}-\d{2}-\d{2})">', content)
        if not m:
            continue
        candidates.append((datetime.strptime(m.group(1), "%Y-%m-%d"), f, content))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    dt, f, content = candidates[0]
    title = _article_h1(content) or f.stem
    lede = ""
    lede_match = re.search(
        r'<article[^>]*>.*?<h1>.+?</h1>\s*<p><strong>(.+?)</strong></p>',
        content, re.DOTALL)
    if lede_match:
        lede = _unescape_and_truncate(lede_match.group(1))
    return {
        "date": dt,
        "title": title,
        "lede": lede,
        "href": f"in-depth/{f.stem}.html",
    }


# ---------------------------------------------------------------------------
# Homepage and full-archive (briefings.html) rendering
# ---------------------------------------------------------------------------

def _render_featured(featured: dict) -> str:
    """The Today's Brief feature: kicker, headline, aside w/ drop cap.
    A terracotta sun motif sits in the upper-right negative space of the
    featured area (see .featured__sun in style.css)."""
    return f"""<section class="featured">
<div class="container">
{FEATURED_SUN_SVG}
<div class="featured__grid">
<div>
<div class="featured__kicker">
{SUNRAY_SVG}
<span class="featured__kicker-text">{format_date_eyebrow(featured["date"])}</span>
</div>
<h2 class="featured__headline">{escape(featured["lede"])}</h2>
</div>
<aside class="featured__aside">
<p class="featured__aside-kicker">Today&rsquo;s Brief</p>
<p class="featured__aside-lede">Plus the rest of the day&rsquo;s news from Tucson, Pima County, and beyond.</p>
<a href="posts/{featured["slug"]}.html" class="featured__cta">
Read today&rsquo;s brief {ARROW_SVG}
</a>
</aside>
</div>
</div>
</section>"""


def _render_stream_card(label: str, when: str, item: dict) -> str:
    """A cross-stream card: just the section label + the headline.
    (`when`/date and the story lede are intentionally omitted — both read as
    clutter on these cards; the section label + headline carry the card.)"""
    return f"""<article class="card">
<p class="card__eyebrow">
{SUNRAY_SVG}
<span>{label}</span>
</p>
<h3 class="card__title"><a href="{item["href"]}">{escape(item["title"])}</a></h3>
</article>"""


def _render_tool_card(label: str, title: str, body: str, cta: str, href: str) -> str:
    """A clay tool card on the homepage (Ask, Responsiveness)."""
    return f"""<a href="{href}" class="tool-card">
<p class="tool-card__eyebrow">{label}</p>
<h3 class="tool-card__title">{title}</h3>
<p class="tool-card__body">{body}</p>
<span class="tool-card__cta">{cta} {ARROW_SVG}</span>
</a>"""


def _render_recent_item(p: dict) -> str:
    weekday_abbr = p["date"].strftime("%a")
    date_str = p["date"].strftime("%b %-d") + f" &middot; {weekday_abbr}"
    return f"""<li class="recent__item">
<span class="recent__date">{date_str}</span>
<h3 class="recent__title"><a href="posts/{p["slug"]}.html">{escape(p["lede"])}</a></h3>
</li>"""


# A plain-language map of the site for first-time visitors. Mirrors the fuller
# guide on about.html.
_SECTION_GUIDE = [
    ("Daily Briefs", "briefings.html",
     "Every morning&rsquo;s synthesis of Tucson news &mdash; city, county, courts, business &mdash; in one place."),
    ("Local Government", "local-government.html",
     "What your council is deciding: previewed before each meeting, reported after."),
    ("Around Town", "around-town.html",
     "New businesses, filings, rezonings and development &mdash; what&rsquo;s opening and changing near you."),
    ("In Depth", "in-depth.html",
     "Standalone feature stories on the issues that matter most across Southern Arizona."),
    ("ChatTDB", "ask.html",
     "Ask anything about Tucson &mdash; answers drawn from, and citing, TDB&rsquo;s own reporting."),
]


def _render_section_guide() -> str:
    items = "\n".join(
        f'<a class="guide-item" href="{href}">'
        f'<h3 class="guide-item__title">{name}</h3>'
        f'<p class="guide-item__desc">{desc}</p></a>'
        for name, href, desc in _SECTION_GUIDE
    )
    return f"""<section class="guide">
<div class="container container--editorial">
<div class="guide__head"><h2 class="section-head">What you&rsquo;ll find here</h2></div>
<div class="guide-grid">
{items}
</div>
</div>
</section>"""


# ---------------------------------------------------------------------------
# Homepage instrument layer (status strip + week at a glance) — all derived
# at render time from published content; NO model calls. See DESIGN-DIRECTIONS.
# ---------------------------------------------------------------------------

def collect_next_meeting() -> dict | None:
    """Soonest upcoming meeting (date >= today) from meeting-watch/, with the
    meeting time parsed from the page when present."""
    if not MEETINGS_DIR.exists():
        return None
    today = datetime.now().date()
    cands = []
    for f in MEETINGS_DIR.glob("*.html"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
        if not m:
            continue
        dt = datetime.strptime(m.group(1), "%Y-%m-%d")
        if dt.date() >= today:
            cands.append((dt, f))
    cands.sort()
    for dt, f in cands:
        content = f.read_text()
        title = _article_h1(content) or f.stem
        # A canceled meeting isn't a "next meeting to watch" — skip it.
        if re.search(r"cancel", title, re.I):
            continue
        name = re.split(r"\s+[—-]\s+What to Watch", title)[0].strip()
        tm = re.search(r"(\d{1,2}:\d{2}\s*[ap]\.?m\.?)", content, re.I)
        time_str = tm.group(1).replace(".", "").upper().replace("  ", " ") if tm else ""
        return {"date": dt, "name": name, "time": time_str,
                "href": f"meeting-watch/{f.stem}.html"}
    return None


# Longer hazard names first so "Extreme Heat Warning" doesn't match as "Heat Warning".
_ALERT_NAME_RE = re.compile(
    r"\b(Flash Flood|Excessive Heat|Extreme Heat|Severe Thunderstorm|Blowing Dust|"
    r"Red Flag|High Wind|Winter Storm|Dust|Flood|Heat|Wind)\s+"
    r"(Watch|Warning|Advisory)\b", re.I)


def extract_weather_status(brief_html: str) -> dict:
    """From a brief's weather section: {alert, hi, lo}. If the brief says there
    are no active alerts, alert is None. hi/lo are the nearest forecast values."""
    t = re.sub(r"<[^>]+>", " ", brief_html).replace("&deg;", "°").replace("&amp;", "&")
    t = re.sub(r"\s+", " ", t)
    wi = t.find("Weather")
    weather = t[wi:] if wi >= 0 else t
    alert = None
    if not re.search(r"no active\b[^.]*alert", weather, re.I):
        # Name the hazards rather than quoting a clause — the strip is a ticker,
        # and the old clause match cut mid-abbreviation ("…runs through 8 p").
        names = []
        for m in _ALERT_NAME_RE.finditer(weather):
            name = f"{m.group(1).title()} {m.group(2).title()}"
            if name not in names:
                names.append(name)
        alert = ", ".join(names[:3]) or None
    # "near"/"around"/"of" all occur in NWS phrasing — matching only "near"
    # silently dropped the temps chip from the status strip.
    hi = re.search(r"high (?:near|around|of) (\d+)", weather, re.I)
    lo = re.search(r"low (?:near|around|of) (\d+)", weather, re.I)
    return {"alert": alert,
            "hi": hi.group(1) if hi else None,
            "lo": lo.group(1) if lo else None}


def render_status_strip(brief_html: str, next_meeting: dict | None) -> str:
    """The full-width live status strip. All content derived, never asked."""
    w = extract_weather_status(brief_html) if brief_html else {"alert": None, "hi": None, "lo": None}
    parts = []
    if w["alert"]:
        parts.append(f'<span class="warn"><span class="dot"></span><b>{escape(w["alert"])}</b></span>')
    else:
        parts.append('<span><span class="dot"></span>No active weather alerts</span>')
    if next_meeting:
        when = next_meeting["date"].strftime("%a").upper()
        if next_meeting["time"]:
            when += f' {next_meeting["time"]}'
        parts.append(f'<span><span class="dot"></span>Next meeting: '
                     f'<b>{escape(next_meeting["name"])}</b> &middot; {when}</span>')
    parts.append('<span class="spacer"></span>')
    if w["hi"] and w["lo"]:
        parts.append(f'<span><span class="dot"></span>{w["hi"]}° / {w["lo"]}° &middot; Tucson</span>')
    return f"""<div class="status"><div class="container container--home">
{"".join(parts)}
</div></div>"""


def collect_week_items(start, end) -> list[dict]:
    """All published items dated within [start, end] (inclusive dates), across
    streams, each tagged with a short mono stream marker + label."""
    items = []

    def _scan(directory, kind, label):
        if not directory.exists():
            return
        for f in directory.glob("*.html"):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
            if not m:
                continue
            dt = datetime.strptime(m.group(1), "%Y-%m-%d")
            if not (start <= dt.date() <= end):
                continue
            title = _article_h1(f.read_text()) or f.stem
            items.append({"date": dt, "mk": kind, "label": label,
                          "title": title, "href": f"{directory.name}/{f.stem}.html"})

    for p in collect_existing_posts():
        if start <= p["date"].date() <= end:
            items.append({"date": p["date"], "mk": "brief", "label": "Brief",
                          "title": p["lede"], "href": f'posts/{p["slug"]}.html'})
    _scan(MEETINGS_DIR, "preview", "Preview")
    _scan(REPORTS_DIR, "report", "Report")
    for it in collect_around_town_items():
        if start <= it["date"].date() <= end:
            mk = "filing" if it["kind"] == "new-business" else "dev"
            label = "Filing" if it["kind"] == "new-business" else "Development"
            items.append({"date": it["date"], "mk": mk, "label": label,
                          "title": it["title"], "href": it["href"]})
    return items


def render_week_glance() -> str:
    """'The week at a glance' — the current Mon–Fri, each day's items tagged by
    stream. Returns '' if the week has no items."""
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).date()
    days = [monday + timedelta(days=i) for i in range(5)]
    items = collect_week_items(days[0], days[-1])
    if not items:
        return ""
    by_day = {d: [] for d in days}
    for it in items:
        d = it["date"].date()
        if d in by_day:
            by_day[d].append(it)
    # Keep the grid tidy: lead with the highest-signal items (brief/report/
    # preview) over filings/dev, and cap each day so a busy filing day doesn't
    # tower over the rest. The full record lives in each section.
    _rank = {"brief": 0, "report": 1, "preview": 2, "dev": 3, "filing": 4}
    cells = []
    for d in days:
        is_today = d == today.date()
        day_items = sorted(by_day[d], key=lambda it: _rank.get(it["mk"], 9))[:5]
        lis = "".join(
            f'<li><span class="mk {it["mk"]}">{it["label"]}</span>'
            f'<a href="{it["href"]}">{escape(it["title"])}</a></li>'
            for it in day_items
        )
        dh = f'{d.strftime("%a")} {d.day}' + (" &middot; Today" if is_today else "")
        cells.append(f'<div class="day{" today" if is_today else ""}">'
                     f'<p class="dh">{dh}</p><ul>{lis}</ul></div>')
    note = f'{days[0].strftime("%b %-d").upper()} &ndash; {days[-1].strftime("%b %-d").upper()}'
    return (f'<div class="wk-h"><h2>The week at a glance</h2>'
            f'<span class="note">{note}</span></div>\n'
            f'<section class="week">{"".join(cells)}</section>')


# One-day homepage hero override: feature a specific page as the lead story
# instead of the daily brief, ONLY on the given date. THE RULE (user,
# 2026-08-09): a manual override lasts exactly one day — the next morning's
# rebuild (the 6 AM brief run) takes the hero back automatically. No
# multi-day windows; re-set the override each day if it's genuinely wanted
# again. Set to None to disable.
# Sources: "report" / "indepth" (validated against that section's latest item
# by slug) or "custom" (explicit href + title, any page on the site).
FEATURED_REPORT_OVERRIDE = None


def render_homepage(posts: list[dict],
                    latest_meeting: dict | None,
                    latest_report: dict | None,
                    latest_filing: dict | None,
                    latest_indepth: dict | None = None) -> str:
    """Render the homepage — the "Morning Edition + instrument" hybrid: an
    edition dateline, a lead + "This morning in Tucson" rundown, a ruled
    cross-stream table, the week at a glance, and recent briefs. All content
    is derived at render time (no model calls). See DESIGN-DIRECTIONS-2026-07."""
    today = datetime.now()

    # ── Live status strip (weather/alert + next meeting) ──
    brief_html = ""
    if posts:
        _bp = POSTS_DIR / f'{posts[0]["slug"]}.html'
        if _bp.exists():
            brief_html = _bp.read_text()
    status_block = render_status_strip(brief_html, collect_next_meeting())

    # ── Edition dateline (weather now lives in the status strip above) ──
    edition_block = f"""<div class="edition">
<span>{format_date_long(today)}</span><span class="rule"></span>
<span class="loc">Tucson, Arizona</span>
</div>"""

    # ── Lead + "This morning in Tucson" rundown ──
    # One-day override: feature a specific news report as the hero (see
    # FEATURED_REPORT_OVERRIDE). Active only on its date and only if it's the
    # current latest_report; otherwise the daily brief leads as usual.
    hero_report = None
    if FEATURED_REPORT_OVERRIDE:
        if FEATURED_REPORT_OVERRIDE.get("date") == today.strftime("%Y-%m-%d"):
            _src = FEATURED_REPORT_OVERRIDE.get("source", "report")
            if _src == "custom":
                hero_report = {"href": FEATURED_REPORT_OVERRIDE["href"],
                               "title": FEATURED_REPORT_OVERRIDE["title"],
                               "lede": FEATURED_REPORT_OVERRIDE.get("dek", "")}
            else:
                _cand = latest_indepth if _src == "indepth" else latest_report
                if _cand and _cand["href"].endswith(f'{FEATURED_REPORT_OVERRIDE["slug"]}.html'):
                    hero_report = _cand

    if hero_report and posts:
        # Report leads; the brief moves into the "This morning in Tucson" aside
        # (keep all headlines here — none of them is the lead anymore).
        rundown = collect_brief_rundown(posts[0]["slug"], 5)
        rundown_lis = "\n".join(
            f'<li><a href="posts/{posts[0]["slug"]}.html">{escape(it)}</a></li>'
            for it in rundown
        ) or f'<li><a href="posts/{posts[0]["slug"]}.html">Read today&rsquo;s brief</a></li>'
        lead_block = f"""<section class="lead-grid">
<div class="lead">
{FEATURED_SUN_SVG}
<p class="lead__kicker">{escape(FEATURED_REPORT_OVERRIDE.get("kicker", "What They Decided"))}</p>
<h2 class="lead__head">{escape(hero_report["title"])}</h2>
<p class="lead__dek">{escape(FEATURED_REPORT_OVERRIDE.get("dek") or hero_report["lede"])}</p>
<a class="link-arrow" href="{hero_report["href"]}">{escape(FEATURED_REPORT_OVERRIDE.get("cta", "Read the full report"))} {ARROW_SVG}</a>
</div>
<aside class="rundown">
<h2>This morning in Tucson</h2>
<ol>
{rundown_lis}
</ol>
<p class="see-all"><a class="link-arrow" href="posts/{posts[0]["slug"]}.html">Read today&rsquo;s brief {ARROW_SVG}</a></p>
</aside>
</section>"""
    elif posts:
        featured = posts[0]
        # Skip the first headline — it's the lead, already shown to the left.
        rundown = collect_brief_rundown(featured["slug"], 5)[1:]
        rundown_lis = "\n".join(
            f'<li><a href="posts/{featured["slug"]}.html">{escape(it)}</a></li>'
            for it in rundown
        ) or f'<li><a href="posts/{featured["slug"]}.html">Read today&rsquo;s brief</a></li>'
        lead_block = f"""<section class="lead-grid">
<div class="lead">
{FEATURED_SUN_SVG}
<p class="lead__kicker">Today&rsquo;s Brief</p>
<h2 class="lead__head">{escape(featured["lede"])}</h2>
<p class="lead__dek">Plus the rest of the day&rsquo;s news from Tucson, Pima County, and beyond.</p>
<a class="link-arrow" href="posts/{featured["slug"]}.html">Read today&rsquo;s brief {ARROW_SVG}</a>
</div>
<aside class="rundown">
<h2>This morning in Tucson</h2>
<ol>
{rundown_lis}
</ol>
</aside>
</section>"""
    else:
        lead_block = '<section class="lead-grid"><div class="lead"><p class="lead__dek">No briefings yet.</p></div></section>'

    # ── "Latest across Tucson" — ruled 4-cell cross-stream table ──
    def _across_cell(eyebrow, cls, item, meta):
        return (f'<div class="cell"><p class="eyebrow{cls}">{eyebrow}</p>'
                f'<h3><a href="{item["href"]}">{escape(item["title"])}</a></h3>'
                f'<p class="meta">{meta}</p></div>')
    across_cells = []
    if latest_meeting:
        when = "Tomorrow" if latest_meeting["date"].date() == (today.date() + timedelta(days=1)) else format_date_short(latest_meeting["date"])
        across_cells.append(_across_cell("What to Watch", "", latest_meeting, when))
    if latest_report and latest_report is not hero_report:
        # When an item is already the hero, don't repeat it in the across strip.
        across_cells.append(_across_cell("What They Decided", " sage", latest_report, format_date_short(latest_report["date"])))
    if latest_filing:
        across_cells.append(_across_cell("Around Town", " clay", latest_filing, "New filing"))
    if latest_indepth and latest_indepth is not hero_report:
        across_cells.append(_across_cell("In Depth", " sage", latest_indepth, format_date_short(latest_indepth["date"])))
    across_block = (f'<h2 class="section-head">Latest across Tucson</h2>\n'
                    f'<section class="across">{"".join(across_cells)}</section>') if across_cells else ""

    # ── The week at a glance ──
    week_block = render_week_glance()

    # ── Recent daily briefs — two-column list ──
    recent = posts[1:7]
    if recent:
        recent_items = "\n".join(
            f'<li><span class="d">{p["date"].strftime("%b %-d")} &middot; {p["date"].strftime("%a")}</span>'
            f'<a href="posts/{p["slug"]}.html">{escape(p["lede"])}</a></li>'
            for p in recent
        )
        recent_block = f"""<h2 class="section-head">Recent daily briefs</h2>
<section><ul class="recent-list">
{recent_items}
</ul>
<p class="see-all"><a class="link-arrow" href="briefings.html">See all Daily Briefs {ARROW_SVG}</a></p></section>"""
    else:
        recent_block = ""

    subscribe_block = f"""<section class="subscribe">
<div class="container container--editorial">
{SUBSCRIBE_PANEL_HTML}
</div>
</section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tucson Daily Brief &mdash; The Tucson news you&rsquo;d otherwise miss</title>
{seo_head_html(
    title="Tucson Daily Brief — The Tucson news you'd otherwise miss",
    description="Daily Tucson news briefings, local government meeting coverage, and new business and development filings across Tucson, Pima County, Marana, and Oro Valley — by Nicholas De Leon.",
    path="",
    jsonld={
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": SITE_NAME, "url": f"{SITE_URL}/"},
            {"@type": "NewsMediaOrganization", "name": SITE_NAME,
             "url": f"{SITE_URL}/",
             "logo": {"@type": "ImageObject", "url": OG_IMAGE_URL},
             "founder": {"@type": "Person", "name": "Nicholas De Leon"},
             "areaServed": "Tucson metropolitan area, Arizona"},
        ],
    })}
<link rel="stylesheet" href="style.css">
{ANALYTICS_HTML}
</head>
<body class="home">

{status_block}

{site_header_html(h1=True)}

<div class="container container--home">
{section_nav_html(active="")}
</div>

<main>
<div class="container container--home">
{edition_block}
{lead_block}
{across_block}
{week_block}
{recent_block}
</div>
{subscribe_block}
</main>

<div class="container">
{footer_html()}
</div>

{SCROLL_TRIGGER_JS}
</body>
</html>
"""


def render_briefings_index(posts: list[dict]) -> str:
    """Render the full daily-brief archive page."""
    items = []
    for p in posts:
        items.append(f"""<li>
<span class="post-date">{format_date_short(p["date"])}</span>
<a href="posts/{p["slug"]}.html">{format_date_long(p["date"])}</a>
<p class="post-lede">{escape(p["lede"])}</p>
</li>""")

    post_list = "\n".join(items) if items else '<li class="empty">No briefings yet.</li>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Briefs &mdash; Tucson Daily Brief</title>
{seo_head_html(
    title="Daily Briefs — Tucson Daily Brief",
    description="The full archive of Tucson Daily Brief's morning news briefings — every day's Tucson and Southern Arizona news in one place.",
    path="briefings.html")}
<link rel="stylesheet" href="style.css">
{ANALYTICS_HTML}
</head>
<body>

{site_header_html()}

<div class="container">
{section_nav_html(active="briefings")}
</div>

<main>
<div class="container container--editorial">
<div style="padding-top:var(--gap-xl);margin-bottom:var(--gap-l)">
<h1 class="section-head">Daily Briefs</h1>
<p class="section-intro">Every day&rsquo;s synthesis of Tucson, Pima County, and Arizona news, newest first.</p>
</div>

<div style="margin-bottom:var(--gap-xl)">{SUBSCRIBE_PANEL_HTML}</div>

<ul class="post-list">
{post_list}
</ul>
</div>
</main>

<div class="container">
{footer_html()}
</div>

{SCROLL_TRIGGER_JS}
</body>
</html>
"""


def rebuild_all_briefs(source_dir: str | Path) -> None:
    """Regenerate every individual post HTML by re-running each markdown source
    through render_post(). Called once after a render_post() template change."""
    source = Path(source_dir).expanduser()
    if not source.exists():
        print(f"  Source dir not found: {source}")
        return
    count = 0
    POSTS_DIR.mkdir(exist_ok=True)
    for md_path in sorted(source.glob("tucson-brief-*.md")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.stem)
        if not m:
            continue
        date = datetime.strptime(m.group(1), "%Y-%m-%d")
        slug = post_slug(date)
        md_text = md_path.read_text()
        body_html = md_to_html(md_text)
        (POSTS_DIR / f"{slug}.html").write_text(render_post(date, body_html, extract_headline(md_text)))
        count += 1
    print(f"  Regenerated {count} daily-brief HTML page(s)")


# ---------------------------------------------------------------------------
# Hub landing pages — Local Government, Around Town (no-JS: pages, not dropdowns)
# ---------------------------------------------------------------------------

def render_local_government(latest_meeting: dict | None,
                            latest_report: dict | None) -> str:
    """Local Government hub: previews (before) + reports (after), with the
    latest of each surfaced as a card and links to the full archives."""
    cards = []
    if latest_meeting:
        cards.append(_render_stream_card("What to Watch",
                                         format_date_short(latest_meeting["date"]),
                                         latest_meeting))
    if latest_report:
        cards.append(_render_stream_card("What They Decided",
                                         format_date_short(latest_report["date"]),
                                         latest_report))
    cards_block = f'<div class="cross-grid">{"".join(cards)}</div>' if cards else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local Government &mdash; Tucson Daily Brief</title>
{seo_head_html(
    title="Local Government — Tucson Daily Brief",
    description="Meeting previews and post-meeting news reports for Tucson Mayor & Council, the Pima County Board of Supervisors, and the Marana and Oro Valley town councils.",
    path="local-government.html")}
<link rel="stylesheet" href="style.css">
{ANALYTICS_HTML}
</head>
<body>

{site_header_html()}

<div class="container">
{section_nav_html(active="local-government")}
</div>

<main>
<div class="container container--editorial">
<div style="padding-top:var(--gap-xl);margin-bottom:var(--gap-l)">
<h1 class="section-head">Local Government</h1>
<p class="section-intro">What your local government is deciding &mdash; before and after. Ahead of each meeting we preview what&rsquo;s on the agenda; afterward we report what was decided. Coverage spans Tucson, Pima County, Marana, and Oro Valley.</p>
</div>

{cards_block}

<p class="hub-links">
<a href="meeting-watch.html">What to Watch {ARROW_SVG}</a>
<a href="news-reports.html">What They Decided {ARROW_SVG}</a>
</p>

<div style="margin-top:var(--gap-xl)">{SUBSCRIBE_PANEL_HTML}</div>
</div>
</main>

<div class="container">
{footer_html()}
</div>

{SCROLL_TRIGGER_JS}
</body>
</html>
"""


def _render_at_item(it: dict) -> str:
    """One row in the Around Town combined feed, tagged by kind."""
    topic_html = "".join(
        f'<span class="topic-flag topic-flag--{escape(t)}">{escape(topic_label(t))}</span>'
        for t in it.get("topics", []))
    return f"""<li class="at-item">
<div class="at-meta">
<span class="post-date">{format_date_short(it["date"])}</span>
<span class="at-tag at-tag--{it["kind"]}">{it["kind_label"]}</span>
{topic_html}
</div>
<a href="{it["href"]}">{escape(it["title"])}</a>
<p class="post-lede">{escape(it["lede"])}</p>
</li>"""


def render_around_town(items: list[dict]) -> str:
    """Around Town combined feed: new-business filings + development cases."""
    lis = ("\n".join(_render_at_item(it) for it in items) if items
           else '<li class="empty">Nothing yet &mdash; check back soon.</li>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Around Town &mdash; Tucson Daily Brief</title>
{seo_head_html(
    title="Around Town — Tucson Daily Brief",
    description="What's opening, building, and changing in the Tucson area — new businesses, liquor license filings, rezonings, and development cases pulled from public records.",
    path="around-town.html")}
<link rel="stylesheet" href="style.css">
{ANALYTICS_HTML}
</head>
<body>

{site_header_html()}

<div class="container">
{section_nav_html(active="around-town")}
</div>

<main>
<div class="container container--editorial">
<div style="padding-top:var(--gap-xl);margin-bottom:var(--gap-l)">
<h1 class="section-head">Around Town</h1>
<p class="section-intro">What&rsquo;s opening, building, and changing near you &mdash; new businesses and liquor filings, plus rezonings and development cases &mdash; pulled automatically from public records, most of which never get reported on. Each item is tagged <strong>New business</strong> or <strong>Development</strong>.</p>
</div>

<div style="margin-bottom:var(--gap-xl)">{SUBSCRIBE_PANEL_HTML}</div>

<ul class="post-list at-list">
{lis}
</ul>
</div>
</main>

<div class="container">
{footer_html()}
</div>

{SCROLL_TRIGGER_JS}
</body>
</html>
"""


def render_newsletter() -> str:
    """Standalone TDB Weekly signup landing page — the conversion target for
    social link stickers and hand-pasted links.

    Why this exists: the site's subscribe form is an inline embed (it POSTs
    straight to Buttondown), so there's no page a social link can point at that
    both shows the form AND runs our GA4. This page does. Inbound utm_* params
    therefore register in GA4 → Acquisition → Traffic acquisition, closing the
    social→newsletter attribution gap. Link it as:
      /newsletter.html?utm_source=instagram&utm_medium=story&utm_campaign=tdb-weekly
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TDB Weekly &mdash; the free Tucson newsletter &mdash; Tucson Daily Brief</title>
{seo_head_html(
    title="TDB Weekly — the free Tucson newsletter",
    description="A free Sunday-morning email: the week's most important Tucson developments, what local government decided, and what's coming next — plus The Tucson Mini crossword, built just for subscribers.",
    path="newsletter.html")}
<link rel="stylesheet" href="style.css">
{ANALYTICS_HTML}
</head>
<body>

{site_header_html()}

<div class="container">
{section_nav_html()}
</div>

<main>
<div class="container container--editorial">
<div style="padding-top:var(--gap-xl);margin-bottom:var(--gap-l)">
<h1 class="section-head">TDB Weekly</h1>
<p class="section-intro">One free email, Sunday mornings. The week&rsquo;s most important Tucson developments &mdash; what local government decided, what&rsquo;s coming next, and the stories you&rsquo;d otherwise miss &mdash; gathered into a warm, five-minute read. Plus <strong>The Tucson Mini</strong>, a crossword built just for subscribers.</p>
</div>

{SUBSCRIBE_PANEL_HTML}

<div class="post-page" style="max-width:680px;margin-top:var(--gap-xl)">
<h2>What lands in your inbox</h2>

<h3>What was decided</h3>
<p>The week&rsquo;s consequential votes and decisions from the City of Tucson, Pima County, Marana, and Oro Valley &mdash; drawn from meetings and filings most Tucsonans never hear about.</p>

<h3>What&rsquo;s coming next</h3>
<p>A look ahead at what&rsquo;s on the agenda in the week to come, so you know what&rsquo;s worth watching before it happens &mdash; not after.</p>

<h3>The Tucson Mini</h3>
<p>A 5&times;5 crossword built around Tucson vocabulary &mdash; our streets, saguaros, and shared references &mdash; included with every issue, for subscribers only.</p>

<h3>Free, and easy to leave</h3>
<p>No cost, no spam, one email a week. Unsubscribe anytime with one click.</p>
</div>
</div>
</main>

<div class="container">
{footer_html()}
</div>

{SCROLL_TRIGGER_JS}
</body>
</html>
"""


def build_sitemap() -> None:
    """Write sitemap.xml covering every indexable page on the site. The
    crossword is deliberately excluded (noindex, subscriber-only)."""
    root_pages = ["", "briefings.html", "local-government.html", "around-town.html",
                  "meeting-watch.html", "news-reports.html", "public-record.html",
                  "in-depth.html", "ask.html", "about.html", "newsletter.html",
                  "responsiveness.html", "talk-to-us.html"]
    entries = []

    def add(path: str, lastmod: str | None = None) -> None:
        loc = f"{SITE_URL}/{path}" if path else f"{SITE_URL}/"
        lm = f"\n<lastmod>{lastmod}</lastmod>" if lastmod else ""
        entries.append(f"<url>\n<loc>{loc}</loc>{lm}\n</url>")

    for p in root_pages:
        add(p)
    for directory, prefix in [(POSTS_DIR, "posts"), (MEETINGS_DIR, "meeting-watch"),
                              (REPORTS_DIR, "news-reports"), (PUBLIC_RECORD_DIR, "public-record"),
                              (AROUND_TOWN_DIR, "around-town"), (INDEPTH_DIR, "in-depth")]:
        if not directory.exists():
            continue
        for f in sorted(directory.glob("*.html")):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
            add(f"{prefix}/{f.name}", m.group(1) if m else None)

    (SITE_DIR / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) + "\n</urlset>\n")


def build_rss(posts: list[dict], limit: int = 30) -> None:
    """Write rss.xml — the most recent daily briefs (newest first)."""
    def xml_escape(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    items = []
    for p in posts[:limit]:
        title = f"Tucson Daily Brief — {format_date_long(p['date'])}"
        url = f"{SITE_URL}/posts/{p['slug']}.html"
        desc = _unescape_and_truncate(p.get("lede", ""), max_len=0)
        pub = p["date"].strftime("%a, %d %b %Y 06:00:00 -0700")
        items.append(f"""<item>
<title>{xml_escape(title)}</title>
<link>{url}</link>
<guid isPermaLink="true">{url}</guid>
<pubDate>{pub}</pubDate>
<description>{xml_escape(desc)}</description>
</item>""")

    (SITE_DIR / "rss.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>{SITE_NAME}</title>
<link>{SITE_URL}/</link>
<atom:link href="{RSS_URL}" rel="self" type="application/rss+xml"/>
<description>The Tucson news you'd otherwise miss — daily briefings on Tucson and Southern Arizona.</description>
<language>en-us</language>
{chr(10).join(items)}
</channel>
</rss>
""")


def rebuild_homepage() -> None:
    """Rebuild index.html (zoned homepage), briefings.html (full archive), and
    the two hub pages (local-government.html, around-town.html). Callable from
    any pipeline that publishes new content."""
    posts = collect_existing_posts()
    latest_meeting = collect_latest_meeting()
    latest_report = collect_latest_report()
    latest_filing = collect_latest_filing()
    latest_indepth = collect_latest_indepth()

    (SITE_DIR / "index.html").write_text(
        render_homepage(posts, latest_meeting, latest_report, latest_filing, latest_indepth)
    )
    (SITE_DIR / "briefings.html").write_text(render_briefings_index(posts))
    (SITE_DIR / "local-government.html").write_text(
        render_local_government(latest_meeting, latest_report)
    )
    (SITE_DIR / "around-town.html").write_text(
        render_around_town(collect_around_town_items())
    )
    (SITE_DIR / "newsletter.html").write_text(render_newsletter())
    restamp_edition_nav(posts)
    build_sitemap()
    build_rss(posts)
    print(f"  Rebuilt: index.html + briefings.html + local-government.html + around-town.html + newsletter.html + sitemap.xml + rss.xml ({len(posts)} briefing(s))")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate a TDB daily-brief post and rebuild the homepage.")
    parser.add_argument("briefing", nargs="?", help="Path to tucson-brief-YYYY-MM-DD.md")
    parser.add_argument("--rebuild-homepage", action="store_true",
                        help="Refresh index.html and briefings.html only; do not process a briefing.")
    parser.add_argument("--rebuild-all", metavar="DIR",
                        help="Regenerate all individual post HTML from .md files in DIR, then rebuild homepage.")
    args = parser.parse_args()

    if args.rebuild_all:
        rebuild_all_briefs(args.rebuild_all)
        rebuild_homepage()
        return

    if args.rebuild_homepage:
        rebuild_homepage()
        return

    if not args.briefing:
        parser.error("provide a briefing path, or use --rebuild-homepage")

    md_path = args.briefing
    if not os.path.isfile(md_path):
        print(f"Error: file not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    date = parse_date_from_filename(md_path)
    slug = post_slug(date)
    md_text = Path(md_path).read_text()
    body_html = md_to_html(md_text)

    POSTS_DIR.mkdir(exist_ok=True)
    post_file = POSTS_DIR / f"{slug}.html"
    post_file.write_text(render_post(date, body_html, extract_headline(md_text)))
    print(f"Wrote {post_file}")

    rebuild_homepage()


if __name__ == "__main__":
    main()
