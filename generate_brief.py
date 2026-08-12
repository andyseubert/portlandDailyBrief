#!/usr/bin/env python3
"""generate_brief.py — deterministic Tucson Daily Brief generator.

Replaces the OpenClaw 6 AM briefing agent. The agent was an agent runtime whose
agency we had already designed away (sources.json is the sole source of truth,
TUCSON-BRIEF.md pins the format and save path), and the agent loop introduced
three recurring failure modes, all from using an agent for deterministic work:

  1. mid-run context compaction silently dropped heavy feeds before synthesis,
     so the brief collapsed onto whichever 2 sources happened to survive;
  2. the agent occasionally saved the brief to the wrong path, stalling the
     whole 6:10 podcast/blog pipeline;
  3. holding every raw feed in one long context bloated the run.

Here the model does exactly ONE thing — read the day's already-fetched items and
write the brief (rank / select / dedupe / prose). Everything else is Python:

  * fetch every source in sources.json (RSS / Bluesky / NWS) — nothing can be
    silently dropped, because Python guarantees every feed reaches the prompt;
  * filter to a recent time window, dedupe;
  * read EDITOR-TIPS.md and include in-window tips;
  * one bounded Sonnet call for synthesis (no agent loop -> no compaction);
  * write to the canonical path with open(path, "w") — the mis-save bug is
    structurally impossible;
  * append a DETERMINISTIC provenance footer (Python knows exactly which feeds
    returned data and which failed — more honest than the model claiming).

Usage:
    python generate_brief.py                  # full run, write canonical brief
    python generate_brief.py --dry-run        # fetch + filter, print item digest, NO API call
    python generate_brief.py --print          # full run, print to stdout instead of writing
    python generate_brief.py --date 2026-06-25
    python generate_brief.py --hours 48       # widen the recency window (default 36h)
    python generate_brief.py --out /tmp/x.md  # write somewhere else (side-by-side testing)
"""

import argparse
import calendar
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests

# A handful of gov feeds use "insecure_tls" (broken cert chain); silence the
# per-request InsecureRequestWarning those verify=False fetches would emit.
requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HOME = Path.home()
# sources.json is version-controlled in this repo (pipeline/sources.json). The
# legacy ~/.openclaw/.../references/sources.json path is now a symlink to it, so
# any remaining OpenClaw reference still resolves, but this script reads the repo
# copy directly (script-relative) and no longer depends on ~/.openclaw existing.
SOURCES_JSON = Path(__file__).resolve().parent / "pipeline" / "sources.json"
# EDITOR-TIPS.md is likewise version-controlled here (pipeline/EDITOR-TIPS.md); the
# legacy ~/.openclaw/workspace/EDITOR-TIPS.md path is now a symlink to it. The original
# TUCSON-BRIEF.md editorial rules (retired with the OpenClaw agent) are preserved for
# reference at pipeline/TUCSON-BRIEF.md — the live rules are in SYNTHESIS_PROMPT below.
EDITOR_TIPS = Path(__file__).resolve().parent / "pipeline" / "EDITOR-TIPS.md"
# Output dir consumed downstream by run_podcast.sh (resolve_brief). Left under
# ~/.openclaw/workspace by design: a pipeline contract, not version-controlled config.
BRIEFINGS_DIR = HOME / ".openclaw/workspace/briefings"
# Provenance-gate working dir (gitignored): the source text each brief was built
# from, plus the shadow log. Archiving the input is what makes a flag auditable
# after the fact — RSS feeds roll over within a day or two, so it cannot be
# reconstructed later. Not under BRIEFINGS_DIR: run_podcast.sh globs that path.
GATE_ARCHIVE_DIR = Path(__file__).resolve().parent / "brief-inputs"
# Provenance alerts go out at 6:00, ten minutes ahead of run_podcast.sh, so there
# is a window to catch a bad name before it publishes. This does NOT conflict with
# the "Telegram only via run_podcast.sh" rule — that rule exists to stop the
# *brief itself* being sent twice (PIPELINE.md). An alert is a distinct message
# type, same as the FOIA and agenda alerts.
SEND_TELEGRAM = HOME / ".openclaw/skills/tucson-daily-brief/scripts/send_telegram.py"

# EXPERIMENT (started 2026-07-29, review ~2026-08-05): running the brief
# synthesis on Opus 5 to see whether adaptive thinking reduces the comprehension
# errors that produced three corrections on 2026-07-29 alone ("unanimously" for
# a 4-0-with-abstention, "in effect" for a warning that had not started, and
# "remain to be decided" for a source that said "remain intact").
# To revert: set this back to "claude-sonnet-4-6". Nothing else needs changing —
# the response parsing and token budget below are correct for both models.
CLAUDE_MODEL = "claude-opus-5"

# Opus 5 thinks by default and max_tokens caps thinking + response text
# TOGETHER. The old 4000 was sized for Sonnet 4.6 running thinking-off; on a
# thinking model that truncates the brief mid-story. Kept non-streaming, so stay
# near ~16k to avoid HTTP timeouts on the 180s socket.
MAX_SYNTHESIS_TOKENS = 16000
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

TZ = ZoneInfo("America/Phoenix")  # Arizona: no DST
DEFAULT_WINDOW_HOURS = 36         # catch late prior-day items at a 6 AM run

# Browser-ish UA so Lee Enterprises / WAF feeds don't 403 a bare urllib client.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}
FETCH_TIMEOUT = 25
MAX_ITEMS_PER_FEED = 25  # mirror the l=25 the feeds already cap at


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def load_sources():
    """Flatten sources.json tiers into an ordered list, preserving tier order."""
    data = json.loads(SOURCES_JSON.read_text())
    tier_order = [
        "tier_1_primary",
        "tier_2_broadcast",
        "tier_2_officials",
        "tier_2_institutional",
        "tier_3_supplemental",
        "tier_4_weather_safety",
    ]
    out = []
    for tier in tier_order:
        for src in data["sources"].get(tier, []):
            src = dict(src)
            src["_tier"] = tier
            out.append(src)

    # A tier present in sources.json but missing from tier_order was silently
    # dropped by the .get() above — no error, no log line, just absent from every
    # brief. That is exactly the failure this module was written to make
    # impossible ("nothing can be silently dropped, because Python guarantees
    # every feed reaches the prompt"), and it happened anyway: tier_2_institutional
    # was added 2026-06-25 and never reached tier_order, so seven official feeds —
    # City of Tucson, TEP, Sun Tran, RTA, UA News x2, PCAO, six of them
    # priority=high — were absent from every brief for 34 days.
    # Fail loudly instead of guessing an order for an unknown tier.
    unknown = [t for t in data["sources"] if t not in tier_order]
    if unknown:
        raise SystemExit(
            f"ERROR: sources.json has tier(s) not in tier_order: {unknown}. "
            f"Add them to tier_order in load_sources() — an unlisted tier is "
            f"silently skipped and its feeds never reach the brief."
        )
    return out


def is_skipped(src):
    """sources.json: status 'broken' or 'disabled' -> never fetch."""
    return src.get("status") in ("broken", "disabled")


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------

def struct_to_dt(parsed):
    """feedparser's *_parsed time.struct_time is UTC -> aware datetime."""
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    except Exception:
        return None


def within_window(dt, cutoff):
    return dt is not None and dt >= cutoff


def clean_text(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)          # strip HTML tags from summaries
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Fetchers — one per source type. Each returns (items, error_or_None).
# An item is a dict: {title, summary, link, published (aware dt or None)}.
# ---------------------------------------------------------------------------

def fetch_rss(src, cutoff):
    # A few government feeds (e.g. Pima County Attorney) serve a valid feed over
    # an incomplete TLS chain; "insecure_tls": true skips verification for them.
    verify = not src.get("insecure_tls", False)
    try:
        resp = requests.get(src["url"], headers=HTTP_HEADERS,
                            timeout=FETCH_TIMEOUT, verify=verify)
        resp.raise_for_status()
    except Exception as e:
        return [], f"fetch error: {e}"

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        return [], f"parse error: {getattr(feed, 'bozo_exception', 'malformed feed')}"

    items, undated = [], []
    for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
        pub = struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed"))
        item = {
            "title": clean_text(entry.get("title", "")),
            "summary": clean_text(entry.get("summary", "") or entry.get("description", "")),
            "link": entry.get("link", ""),
            "published": pub,
        }
        if not item["title"]:
            continue
        if pub is None:
            undated.append(item)        # keep a few; feed order ~ newest first
        elif within_window(pub, cutoff):
            items.append(item)
    # Include up to a few undated items only if the feed gave us almost nothing
    # dated (some feeds omit dates entirely) — flagged so the model can hedge.
    if undated and len(items) < 3:
        for it in undated[:3]:
            it["undated"] = True
            items.append(it)
    return items, None


def fetch_bluesky(src, cutoff):
    try:
        resp = requests.get(src["url"], headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
                            timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [], f"fetch error: {e}"

    items = []
    for fi in data.get("feed", []):
        post = fi.get("post", {})
        record = post.get("record", {})
        # Skip reposts (a 'reason' on the feed item marks a repost).
        if fi.get("reason"):
            continue
        created = record.get("createdAt")
        pub = None
        if created:
            try:
                pub = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except Exception:
                pub = None
        if not within_window(pub, cutoff):
            continue
        text = clean_text(record.get("text", ""))
        external = (post.get("embed") or {}).get("external") or {}
        # record.embed is the canonical place for the external link too.
        if not external:
            external = (record.get("embed") or {}).get("external") or {}
        link = external.get("uri", "")
        title = clean_text(external.get("title", ""))
        desc = clean_text(external.get("description", ""))
        summary_parts = [p for p in (text, title, desc) if p]
        items.append({
            "title": title or (text[:120] if text else "(post)"),
            "summary": " — ".join(summary_parts),
            "link": link,                 # may be empty for plain posts
            "published": pub,
        })
    return items, None


def fetch_weather(sources):
    """Fetch NWS active alerts + the multi-day point forecast.

    Returns (weather_context_str, errors_list). The forecast is passed to the
    model as structured text; the model writes the ⛈️ section per the format.
    """
    errors = []
    alerts_src = next((s for s in sources if "Active Alerts" in s["name"]), None)
    point_src = next((s for s in sources if "Point Forecast" in s["name"]), None)

    alert_lines = []
    if alerts_src:
        try:
            r = requests.get(alerts_src["url"], headers={"User-Agent": HTTP_HEADERS["User-Agent"],
                                                         "Accept": "application/geo+json"},
                            timeout=FETCH_TIMEOUT)
            r.raise_for_status()
            for feat in r.json().get("features", []):
                p = feat.get("properties", {})
                # `onset` is load-bearing: NWS headlines read "issued <now> until
                # <end>" with no start, so an alert that has not begun yet looks
                # active. On 2026-07-29 that produced "Extreme Heat Warning in
                # effect through Sunday" for a warning that started the next
                # morning. The start time is in `description` under WHEN, but 600
                # characters in — derive it here rather than hoping the model
                # reads that far.
                onset = p.get("onset") or p.get("effective") or "?"
                ends = p.get("ends") or p.get("expires") or "?"
                alert_lines.append(
                    f"- {p.get('event','Alert')}: {p.get('headline','')} "
                    f"(severity {p.get('severity','?')}, "
                    f"STARTS {onset}, ENDS {ends})\n"
                    f"  NOTE: if STARTS is in the future, this alert is NOT yet in "
                    f"effect — say when it begins, not that it is in effect now.\n"
                    f"  {clean_text(p.get('description',''))[:600]}"
                )
        except Exception as e:
            errors.append(("NWS Active Alerts", str(e)))

    forecast_lines = []
    if point_src:
        try:
            r = requests.get(point_src["url"], headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
                            timeout=FETCH_TIMEOUT)
            r.raise_for_status()
            forecast_url = r.json().get("properties", {}).get("forecast")
            if forecast_url:
                fr = requests.get(forecast_url, headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
                                 timeout=FETCH_TIMEOUT)
                fr.raise_for_status()
                for period in fr.json().get("properties", {}).get("periods", [])[:8]:
                    forecast_lines.append(
                        f"- {period.get('name','')}: {period.get('detailedForecast','')}"
                    )
        except Exception as e:
            errors.append(("NWS Point Forecast", str(e)))

    parts = []
    if alert_lines:
        parts.append("ACTIVE NWS ALERTS:\n" + "\n".join(alert_lines))
    else:
        parts.append("ACTIVE NWS ALERTS: none.")
    if forecast_lines:
        parts.append("FORECAST PERIODS (NWS Tucson, downtown point):\n" + "\n".join(forecast_lines))
    else:
        parts.append("FORECAST: unavailable.")
    return "\n\n".join(parts), errors


# ---------------------------------------------------------------------------
# Editor tips
# ---------------------------------------------------------------------------

def load_editor_tips(today):
    """Return the raw markdown of tips whose include-through date >= today."""
    if not EDITOR_TIPS.exists():
        return []
    text = EDITOR_TIPS.read_text()
    # Split on '## ' headings; keep blocks whose include-through date is current.
    blocks = re.split(r"\n(?=## )", text)
    live = []
    for block in blocks:
        if not block.lstrip().startswith("## "):
            continue
        m = re.search(r"\[include-through:\s*(\d{4}-\d{2}-\d{2})\]", block)
        if not m:
            continue
        try:
            through = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if through >= today:
            live.append(block.strip())
    return live


# ---------------------------------------------------------------------------
# Dedupe + prompt assembly
# ---------------------------------------------------------------------------

def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = (it.get("link") or "").strip().lower() or it["title"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def build_items_block(by_source):
    """Render the fetched items, grouped by source, for the synthesis prompt."""
    parts = []
    for name, items in by_source:
        if not items:
            continue
        parts.append(f"### SOURCE: {name}  ({len(items)} item(s) in window)")
        for it in items:
            when = it["published"].astimezone(TZ).strftime("%Y-%m-%d %H:%M MST") if it.get("published") else "undated"
            flag = " [UNDATED — hedge or skip]" if it.get("undated") else ""
            parts.append(f"- TITLE: {it['title']}")
            if it["summary"]:
                parts.append(f"  SUMMARY: {it['summary'][:700]}")
            parts.append(f"  LINK: {it['link'] or '(none)'}")
            parts.append(f"  PUBLISHED: {when}{flag}")
        parts.append("")
    return "\n".join(parts)


OWN_REPORTS_DIR = Path(__file__).resolve().parent / "news-reports"


def build_own_reports_block(brief_date):
    """Recent Tucson Daily Brief originals (news-reports/), so the synthesis
    can cite our own coverage instead of crediting an outside outlet for a
    story we broke — closes the self-citation gap (PIPELINE.md, added
    2026-08-12 after both bake-off arms credited KVOA with the data-center
    moratorium story our live reporter published the night before).

    Dated by the article:published_time meta the SEO layer stamps into each
    report (file mtime lies: site-wide HTML sweeps rewrite published pages).
    Window = published on the brief date or the day before, so a report lands
    in exactly one morning's prompt: an evening report enters the next
    morning's brief and is excluded the morning after.
    """
    if not OWN_REPORTS_DIR.is_dir():
        return ""
    out = []
    for p in sorted(OWN_REPORTS_DIR.glob("*.html")):
        head = p.read_text(errors="replace")[:8000]
        m = re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})"', head)
        if not m:
            continue
        pub = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        if not 0 <= (brief_date - pub).days <= 1:
            continue
        title = re.search(r'<meta property="og:title" content="([^"]*)"', head)
        desc = re.search(r'<meta property="og:description" content="([^"]*)"', head)
        url = re.search(r'<link rel="canonical" href="([^"]*)"', head)
        if not (title and url):
            continue
        t = html.unescape(title.group(1)).removesuffix(" — Tucson Daily Brief")
        out.append(f"- TITLE: {t}")
        if desc:
            out.append(f"  SUMMARY: {html.unescape(desc.group(1))}")
        out.append(f"  LINK: {url.group(1)}")
        out.append(f"  PUBLISHED: {pub.isoformat()}")
    return "\n".join(out)


SYNTHESIS_PROMPT = """\
You are the editor of the Tucson Daily Brief, a daily hyper-local news briefing \
for the Tucson / Pima County area. Today is {today_human} (America/Phoenix).

You are given the day's news items, ALREADY FETCHED from the brief's vetted \
sources (you do not fetch anything — work only from what is below). Your job is \
editorial judgment: select, rank, deduplicate, and write the brief.

EDITORIAL PRIORITIES (rank stories in this order; lead with the highest available):
1. Government actions — city council votes, county decisions, AZ legislation affecting Tucson, ballot measures
2. Public safety — major incidents, emergency alerts, law enforcement news (not routine blotter)
3. Schools — the K-12 districts: TUSD, Amphitheater, Marana, Sunnyside, Vail,
   Sahuarita, Catalina Foothills, Flowing Wells, Tanque Verde. (U of A is NOT a
   school district — university news belongs in its topical section.)
4. Development & business — construction, business openings/closings, economic news
5. Community & events — major events, cultural happenings (only if genuinely significant)
6. What your officials are saying — press releases from the officials who represent
   Tucson and, in competitive races, the candidates. Never leads the brief.
7. Weather — always include; lead with it ONLY if an active alert exists

SELECTION RULES:
- Target 7-12 stories. Quality over quantity. Omit empty sections rather than padding.
- DEDUPLICATE: if the same story appears in multiple sources, merge and cite all of them.
- SOURCE DIVERSITY: draw across the available sources; do not lean on one or two \
outlets if others carry relevant local stories. (Some sources may simply have nothing \
local today — that is fine; never invent stories to balance them.)
- Exclude national/state news UNLESS it directly impacts Tucson/Pima County.
- Exclude sports unless it's a major U of A item and significant (not routine results).
- Exclude sponsored/affiliate/advertorial content.
- Items flagged [UNDATED] may be stale — only use one if clearly still relevant, and hedge.
- Soft-hedge single-source or unconfirmed items ("according to [outlet]," "is scheduled to").
- NEVER fabricate facts, names, dates, quotes, or sources. If unsure, leave it out.
- Write neutral, factual AP-style prose. No opinion, no framing — even on political items.

EDITOR TIPS: The blocks under "=== EDITOR TIPS ===" are hand-submitted leads. Treat \
each as a candidate story ranked normally. Follow each tip's own editor note (attribution, \
hedging, what to withhold). Do not invent details beyond the tip and its links.

OFFICIALS: Write the 📢 section from "=== OFFICIALS ===" if that block is present; \
omit the whole section if it is absent or empty. Rules, all of them load-bearing:
- Everything there is a CLAIM, not a fact. Write "said in a release" / "announced" — \
never restate a press release as established fact, and never adopt its framing.
- Keep the two groups distinct. Officials speak as officeholders; candidates speak as \
campaigns. Say which. An incumbent may legitimately appear in both.
- Contested races travel in pairs. If the block carries a NOTE that one side posted \
nothing, SAY SO in the brief — "X's campaign did not post this week." Reporting one \
side silently, with no mention of the other, is the failure this rule exists to stop.
- 1-2 sentences per item, 4-6 items total. Prefer items with Tucson-area consequence \
over national message discipline.
- Draw this section ONLY from the "=== OFFICIALS ===" block. Do not promote a story \
or a social post out of the news items into it — those belong in their own sections, \
and an item pulled across arrives without the URL this section requires.
- Every item needs its markdown link, same as everywhere else. If an item has no URL, \
leave it out.

SCHOOLS: The 🎓 section covers the K-12 districts. It draws from TWO kinds of \
material and the difference decides how you write each item:
- The "=== SCHOOLS ===" block is what districts said on their OWN channels. \
Every line there is an announcement, not a finding: write "the district \
announced" / "said in a post" — never restate it as established fact, and never \
adopt its framing.
- A school story reported by a NEWS OUTLET in the news items belongs in this \
section too, written normally and cited to the outlet. Put it here, not under \
Government or Community — one school story, one place, never both.
- SELECT HARD. Most district posts are routine parent logistics and do NOT \
belong in a news brief: hiring posts, open-enrollment reminders, supply lists, \
lunch menus, app downloads, spirit weeks, photo-use policies, meet-the-teacher \
nights, individual student or staff recognition. Include an item only if it has \
district-wide or metro consequence — budgets, bonds and overrides, board \
decisions, school closures or boundary changes, superintendent changes, policy \
or state-law changes that affect families, safety incidents, ballot measures, \
construction, labor actions.
- If several districts announced the SAME thing, merge them into one item and \
name the districts. That convergence is the story.
- FERPA: never name, photograph-describe, or identify an individual student, \
whatever the district published.
- 1-3 items, 1-2 sentences each. If nothing clears the bar, OMIT THE WHOLE \
SECTION. A padded schools section is worse than no schools section.
- If the block carries a "NOT CHECKED TODAY" note: never write that those \
districts announced nothing, and never write a sentence that claims the section \
covers all of them. That is a constraint on what you claim, NOT an instruction \
to add a disclaimer — do not append "other districts were not checked" or any \
standing caveat to the section. Naming the districts an item actually came from \
is already accurate.
- Every item needs its markdown link, same as everywhere else.

WEATHER: Write the ⛈️ section from the NWS data under "=== WEATHER ===". If an active \
alert exists, lead that section with a ⚠️ callout in bold. Include today's \
conditions/high/low/wind, tonight, tomorrow, and a 2-3 sentence outlook.

OUTPUT FORMAT — output ONLY the briefing, starting with the title line. No preamble, \
no "Good morning," no sign-off, no footer (the system appends provenance). Use this \
structure, with ─── dividers between sections, omitting any section with no stories:

Tucson Daily Brief — {today_human}

In the skeleton below, square brackets appear ONLY as markdown link syntax. The \
headline is bold text with no brackets around it — write `**Headline.**`, never \
`**[Headline.]**`.

🚨 Public Safety
**Headline.** Two to three sentences of neutral summary.
📰 [Source Name](https://direct-article-url)

───

🏛️ Government
(same per-story format)

───

🎓 Schools
**Headline.** Two to three sentences. District announcements are attributed as \
announcements; outlet-reported items are cited to the outlet.
📰 [Source Name](https://direct-url)

───

🏗️ Development & Business
(same per-story format)

───

🎉 Community & Events
(same per-story format)

───

📢 What Your Officials Are Saying
**Name (role).** One or two sentences on what they said, attributed as a release.
📰 [Source Name](https://direct-article-url)

───

⛈️ Weather — Tucson
[alert callout if any, then forecast as described]

📄 NWS Tucson Forecast API

ATTRIBUTION: every story ends with 📰 and a markdown link to the DIRECT article URL \
from its LINK field: `📰 [Source Name](url)`. Multi-source: `📰 [A](url1), [B](url2)`. \
If an item's LINK is "(none)", cite the source name without a link. Never link to a homepage.

SELF-CITATION: the OUR OWN REPORTING block lists stories Tucson Daily Brief itself \
published in the last day — original reporting, not aggregation. When a news item \
covers the same story, our report outranks the outside coverage: prefer our report's \
framing and facts, and cite it FIRST as `📰 [Tucson Daily Brief](link)`, keeping an \
outside link after it only if it adds facts our report lacks. Use only what the \
TITLE/SUMMARY actually say — do not invent detail beyond them.

=== OUR OWN REPORTING (Tucson Daily Brief originals) ===

{own_reports_block}

=== WEATHER ===
{weather_block}

=== EDITOR TIPS ===
{tips_block}

=== SCHOOLS ===

{schools_block}

=== OFFICIALS ===

{officials_block}

=== NEWS ITEMS (grouped by source) ===
{items_block}
"""


# ---------------------------------------------------------------------------
# Claude call (raw HTTP + retry/backoff — matches generate_newsletter.py)
# ---------------------------------------------------------------------------

MAX_RETRIES = 4


def call_claude(prompt, api_key, max_tokens=None):
    max_tokens = max_tokens or MAX_SYNTHESIS_TOKENS
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        CLAUDE_API_URL, data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read())

                # A policy decline is an HTTP 200, not an error: stop_reason is
                # "refusal" and content is empty or partial. Check it before
                # reading content.
                if result.get("stop_reason") == "refusal":
                    cat = (result.get("stop_details") or {}).get("category")
                    print(f"  ERROR: Claude declined the request (category {cat!r})",
                          file=sys.stderr)
                    return None

                # Scan for the first text block rather than assuming content[0].
                # Thinking-capable models put a thinking block first — with
                # display "omitted" its text is empty but the block is still
                # there, so content[0]["type"] == "text" is False and the old
                # code returned None, silently killing the 6 AM brief.
                # Instrument the thinking experiment: how much did it think,
                # and did it help? thinking_tokens is the whole question.
                u = result.get("usage", {}) or {}
                think = (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
                print(f"  Synthesis: model={CLAUDE_MODEL} in={u.get('input_tokens')} "
                      f"out={u.get('output_tokens')} thinking={think} "
                      f"stop={result.get('stop_reason')}", file=sys.stderr)

                for block in result.get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        return block["text"]

                if result.get("stop_reason") == "max_tokens":
                    print("  ERROR: response hit max_tokens with no text block — "
                          "thinking consumed the budget; raise MAX_SYNTHESIS_TOKENS",
                          file=sys.stderr)
                return None
        except urllib.error.HTTPError as e:
            if e.code != 429 and 400 <= e.code < 500:
                detail = e.read().decode("utf-8", "replace")[:400]
                print(f"  ERROR: Claude API HTTP {e.code} (not retrying): {detail}", file=sys.stderr)
                return None
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)
        if attempt < MAX_RETRIES:
            backoff = 2 ** (attempt - 1) + 0.1 * attempt
            print(f"  WARN: Claude call failed ({last_err}); retry {attempt}/{MAX_RETRIES-1} in {backoff:.1f}s",
                  file=sys.stderr)
            time.sleep(backoff)
        else:
            print(f"  ERROR: Claude call failed after {MAX_RETRIES} attempts: {last_err}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Provenance footer — deterministic, Python owns the ground truth
# ---------------------------------------------------------------------------

def run_provenance_gate(body, sources, date_str):
    """Check the draft's proper nouns against the source text, and archive both.

    Never raises and never modifies the brief — a bug in the checker must not be
    able to take down the 6 AM run. Archiving the source text is what makes the
    shadow log auditable later: without it there is no way to re-derive what the
    model was actually given.
    """
    try:
        import provenance_gate as pg
    except Exception as e:
        print(f"  WARN: provenance gate unavailable: {e}", file=sys.stderr)
        return

    try:
        GATE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        (GATE_ARCHIVE_DIR / f"{date_str}.sources.txt").write_text(sources)

        result = pg.check(body, sources, mode=pg.Mode.SHADOW)
        pg.log_jsonl(result, GATE_ARCHIVE_DIR / "shadow.jsonl", date_str, model=CLAUDE_MODEL)

        print(f"  Provenance gate ({result.mode.value}): {result.summary()}", file=sys.stderr)
        for f in result.alerts:
            detail = f" — unverified {f.dropped!r}, sources support {f.supported!r}" \
                if f.status is pg.Status.PARTIAL else " — no trace in sources"
            print(f"    ⚠ {f.run}{detail}", file=sys.stderr)

        if result.alerts:
            send_provenance_alert(result, date_str, pg.Status)
    except Exception as e:
        print(f"  WARN: provenance gate failed (brief unaffected): {e}", file=sys.stderr)


def send_provenance_alert(result, date_str, Status):
    """Telegram the person-shaped alerts, ~10 minutes before the brief publishes.

    Non-fatal by construction: a Telegram outage must never stop a brief that is
    otherwise fine. Quiet when there is nothing to report — an alert channel that
    fires every morning gets muted, and then it protects nothing.
    """
    if not SEND_TELEGRAM.exists():
        print("  WARN: send_telegram.py not found, skipping alert", file=sys.stderr)
        return

    n = len(result.alerts)
    lines = [
        f"🔍 BRIEF NAME CHECK — {n} unverified name{'s' if n != 1 else ''} "
        f"in the {date_str} brief\n",
    ]
    for f in result.alerts:
        if f.status is Status.PARTIAL:
            lines.append(f"⚠️ “{f.run}”")
            lines.append(f"   sources support “{f.supported}” — “{f.dropped}” appears nowhere")
        else:
            lines.append(f"🔴 “{f.run}”")
            lines.append("   no trace in any source fetched for this brief")
    lines.append(
        "\nThese names are in the draft but not in the source text it was written "
        "from, so they may have been invented. The brief publishes at 6:10 — check "
        "before then if you can.\n"
        "Shadow mode: nothing was changed automatically."
    )

    with tempfile.NamedTemporaryFile("w", suffix=".md", prefix="name-check-",
                                     delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        tmp = fh.name
    try:
        sys.stdout.flush()  # keep parent/child output ordered in cron logs
        subprocess.run([sys.executable, str(SEND_TELEGRAM), tmp], check=False)
    except Exception as e:
        print(f"  WARN: Telegram alert failed (non-fatal): {e}", file=sys.stderr)
    finally:
        os.unlink(tmp)


def build_footer(date_str, fetched, failed, skipped):
    succeeded = [n for n, c in fetched if c > 0]
    empty = [n for n, c in fetched if c == 0]
    lines = ["───", "", f"Briefing saved: tucson-brief-{date_str}.md"]
    lines.append(f"Sources with content: {len(succeeded)} of {len(fetched)} fetched "
                 f"({', '.join(succeeded) if succeeded else 'none'})")
    if empty:
        lines.append(f"Fetched, no items in window: {', '.join(empty)}")
    if failed:
        lines.append("Fetch failures: " + "; ".join(f"{n} ({e})" for n, e in failed))
    if skipped:
        lines.append(f"Skipped (broken/disabled in sources.json): {', '.join(skipped)}")
    lines.append("Generated deterministically by generate_brief.py — Next update: 6:00 AM MST")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate the Tucson Daily Brief deterministically.")
    ap.add_argument("--date", help="Brief date YYYY-MM-DD (default: today, America/Phoenix)")
    ap.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS,
                    help=f"Recency window in hours (default {DEFAULT_WINDOW_HOURS})")
    ap.add_argument("--out", help="Write to this path instead of the canonical briefings dir")
    ap.add_argument("--print", dest="to_stdout", action="store_true",
                    help="Print the brief to stdout instead of writing a file")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + filter only; print the item digest and exit (no API call)")
    args = ap.parse_args()

    now = datetime.now(TZ)
    brief_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now.date()
    now_utc = datetime.now(timezone.utc)
    today_human = brief_date.strftime("%B %-d, %Y")

    print(f"Generating Tucson Daily Brief for {brief_date} "
          f"(default window: last {args.hours}h; per-source 'window_hours' honored)", file=sys.stderr)
    sources = load_sources()

    by_source = []          # [(name, [items])] in tier order
    fetched = []            # [(name, count)] for the footer
    failed = []             # [(name, error)]
    skipped = []            # [name]

    for src in sources:
        name, stype = src["name"], src["type"]
        if is_skipped(src):
            skipped.append(name)
            continue
        if stype == "api_json":
            continue        # weather handled separately
        # Per-source recency window: low-cadence, high-value sources (e.g. AZPM)
        # can set "window_hours" in sources.json to a longer lookback than the
        # default so their bursty posts still get caught.
        src_hours = src.get("window_hours", args.hours)
        src_cutoff = now_utc - timedelta(hours=src_hours)
        if stype == "rss":
            items, err = fetch_rss(src, src_cutoff)
        elif stype == "bluesky":
            items, err = fetch_bluesky(src, src_cutoff)
        elif stype == "scrape":
            # Outlets with no feed at all. `scraper` names a module exposing
            # fetch(src, cutoff) -> (items, err) in fetch_rss's shape.
            try:
                mod = __import__(src["scraper"])
                items, err = mod.fetch(src, src_cutoff)
            except Exception as e:
                items, err = [], f"scraper {src.get('scraper')!r} failed: {e}"
        else:
            # An unrecognised type used to `continue` — no error, no log line,
            # the source simply absent from every brief. That is the same silent
            # drop that hid tier_2_institutional for 34 days. Make it loud.
            failed.append((name, f"unknown source type {stype!r}"))
            print(f"  FAIL  {name}: unknown source type {stype!r}", file=sys.stderr)
            continue
        if err:
            failed.append((name, err))
            print(f"  FAIL  {name}: {err}", file=sys.stderr)
            continue
        items = dedupe(items)
        by_source.append((name, items))
        fetched.append((name, len(items)))
        win = f" [window {src_hours}h]" if src_hours != args.hours else ""
        print(f"  ok    {name}: {len(items)} item(s) in window{win}", file=sys.stderr)

    weather_block, weather_errs = fetch_weather(sources)
    failed.extend(weather_errs)
    for n, e in weather_errs:
        print(f"  FAIL  {n}: {e}", file=sys.stderr)

    tips = load_editor_tips(brief_date)
    if tips:
        print(f"  ok    Editor tips: {len(tips)} live", file=sys.stderr)

    items_block = build_items_block(by_source)
    tips_block = "\n\n".join(tips) if tips else "(none)"

    # Officials + candidates. Non-fatal by design: a site redesign should cost us
    # one section, never the brief.
    try:
        import officials_watch as ow
        ow_items, ow_errs = ow.fetch_all()
        # Pass errors through: a candidate whose scraper failed must not be
        # reported as having posted nothing. See build_block()'s docstring.
        officials_block = ow.build_block(ow_items, errors=ow_errs)
        print(f"  ok    Officials watch: {len(ow_items)} release(s), "
              f"{len(ow_errs)} error(s)", file=sys.stderr)
        for name, err in ow_errs:
            print(f"  WARN  officials source FAILED — {name}: {err}", file=sys.stderr)
    except Exception as e:
        officials_block = ""
        print(f"  WARN  officials watch unavailable: {e}", file=sys.stderr)
    # School districts' own channels. Same posture as officials watch: non-fatal,
    # and it feeds its own labeled section rather than the news-items block, so a
    # district announcement can never be read as reporting. See school_news.py.
    # mark_seen is off for --dry-run so a dry run cannot consume tomorrow's items.
    try:
        import school_news as sn
        sn_items, sn_errs, sn_blocked = sn.fetch_all(brief_date=brief_date,
                                                     mark_seen=not args.dry_run)
        schools_block = sn.build_block(sn_items, errors=sn_errs, blocked=sn_blocked)
        print(f"  ok    School news: {len(sn_items)} post(s), "
              f"{len(sn_errs)} error(s), {len(sn_blocked)} blocked", file=sys.stderr)
        for name, err in sn_errs:
            print(f"  WARN  school source FAILED — {name}: {err}", file=sys.stderr)
    except Exception as e:
        schools_block = ""
        print(f"  WARN  school news unavailable: {e}", file=sys.stderr)

    # Our own published reporting — same non-fatal posture as the blocks above.
    try:
        own_reports_block = build_own_reports_block(brief_date)
        n_own = own_reports_block.count("- TITLE:")
        print(f"  ok    Own reporting: {n_own} report(s) in window", file=sys.stderr)
    except Exception as e:
        own_reports_block = ""
        print(f"  WARN  own-reports block unavailable: {e}", file=sys.stderr)

    total_items = sum(c for _, c in fetched)
    print(f"Total items in window: {total_items} across "
          f"{sum(1 for _, c in fetched if c)} source(s) with content", file=sys.stderr)

    if args.dry_run:
        print("\n=== WEATHER ===\n" + weather_block)
        print("\n=== EDITOR TIPS ===\n" + tips_block)
        print("\n=== OWN REPORTING ===\n" + (own_reports_block or "(none)"))
        print("\n=== SCHOOLS ===\n" + (schools_block or "(none)"))
        print("\n=== OFFICIALS ===\n" + (officials_block or "(none)"))
        print("\n=== ITEMS ===\n" + items_block)
        return

    if total_items == 0:
        print("ERROR: no items in window from any source — aborting (would produce an empty brief).",
              file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    prompt = SYNTHESIS_PROMPT.format(
        today_human=today_human,
        weather_block=weather_block,
        tips_block=tips_block,
        items_block=items_block,
        schools_block=schools_block or "(none in window — omit the 🎓 section)",
        officials_block=officials_block or "(none in window — omit the 📢 section)",
        own_reports_block=own_reports_block or "(none in window)",
    )
    print(f"Prompt: ~{len(prompt):,} chars (~{len(prompt)//4:,} tokens). Calling Claude...", file=sys.stderr)

    body = call_claude(prompt, api_key)
    if not body:
        print("ERROR: synthesis failed", file=sys.stderr)
        sys.exit(1)

    # Provenance gate. Runs against the exact source text the model was given, so
    # there is no fetch-window drift to produce phantom flags. Shadow mode for
    # now: classify and log, never alter the brief. See provenance_gate.py.
    run_provenance_gate(
        body,
        # schools_block belongs here for the same reason officials_block does: a
        # name that reached the brief only via a district announcement IS
        # grounded, and leaving the block out would flag it as fabricated.
        # own_reports_block likewise: a name grounded only in OUR report (e.g.
        # a supervisor quoted in last night's meeting story) must not flag.
        sources="\n".join([items_block, weather_block, tips_block,
                           schools_block, officials_block, own_reports_block]),
        date_str=brief_date.isoformat(),
    )

    footer = build_footer(brief_date.isoformat(), fetched, failed, skipped)
    brief = body.strip() + "\n\n" + footer + "\n"

    if args.to_stdout:
        print(brief)
        return

    out_path = Path(args.out) if args.out else (BRIEFINGS_DIR / f"tucson-brief-{brief_date.isoformat()}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(brief)        # <-- deterministic path: the mis-save bug cannot happen
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
