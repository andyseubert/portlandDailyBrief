# Daily Brief Pipeline — renderer, automation, failure modes

The `generate_post.py` renderer, the 6:00/6:10 AM cron chain, Anthropic billing posture, and the recurring failure modes (brief mis-save, no-network, weather-alert-led briefs, the NWS fire-zone fix, editor's desk, the self-citation gap).

Reference doc split out of CLAUDE.md on 2026-07-17 to keep the always-loaded context lean. Prose is preserved verbatim from CLAUDE.md; CLAUDE.md now carries a short pointer to this file.

---

## How It Works

`generate_post.py` takes a briefing markdown file as input and:
1. Extracts the date from the filename (e.g., `tucson-brief-2026-02-18.md` → `2026-02-18`)
2. Converts the markdown to HTML (handles bold, emoji section headers, source citations with links, separators)
3. Writes an editorial-style HTML post (Fraunces display date, drop cap, magazine-style section heads) to `posts/YYYY-MM-DD.html`
4. Calls `rebuild_homepage()` which scans all posts in `posts/` AND the newest entry in `meeting-watch/`, `news-reports/`, `public-record/`, then rebuilds **both** `index.html` (zoned homepage) and `briefings.html` (full daily archive). The homepage's cross-stream cards surface the latest items from every section so a new daily brief, new meeting preview, new news report, or new Spotted filing all refresh the homepage.
5. Is idempotent — running it twice with the same input overwrites cleanly, no duplicates

**Weather-alert-led briefs (fixed 2026-06-23, commit `2c6827d`):** On days with an active NWS alert, the 6 AM agent leads the brief with the Weather section + a `⚠️ **Alert headline.**` callout. This surfaced two bugs, both fixed in `generate_post.py`: (1) `md_to_html` treated *any* emoji-prefixed line as a section header (`<h2>`), so the alert line became a heading with literal `**` asterisks — now an emoji line containing `**` falls through to the paragraph branch and renders the bold properly (real section headers like `🏛️ Government` never contain bold markdown, so they're unaffected); (2) `collect_existing_posts` picked the first `<strong>` as the homepage featured headline, which on weather-led days was a forecast day-label ("Today (Monday, June 22):") — it now skips weather labels (via `_is_weather_label()`: text ending in `:` or containing `°`) and uses the first real headline (e.g. the heat-warning text). Sanity-check the homepage featured card on any weather-alert day.

**Weather-alert-led briefs, round two (fixed 2026-08-03, commit `29fa1f2`):** The 6/23 fix assumed the alert callout was a *headline*. On a multi-hazard day it isn't — 8/3 had heat + flood + dust, and `generate_brief.py` emitted one bolded `⚠️ ALERTS:` block of ~470 characters. `_is_weather_label()` doesn't match it (no trailing `:`, no `°`), so the whole paragraph became the homepage featured headline — it ran most of a screen at display size — plus the post `<title>`, meta description, OG/Twitter cards and `NewsArticle` headline. Fixes, all in `generate_post.py`:

- **`_clamp_weather_alert()`** trims a callout matching `^\W*ALERTS?\b` to its lead sentence; anything else passes through untouched. Applied at all three headline sources — `extract_headline()` (markdown → post meta), `collect_existing_posts()` (post HTML → homepage lead + `post-lede` + RSS), and `collect_brief_rundown()`.
- **`_first_sentence()` is abbreviation-aware.** A naïve first-sentence split cuts "runs through 8 p.m. Monday" at `p.m.`. It requires `.!?` + whitespace + a capital, then rejects the break if the head ends in a known abbreviation (`a.m`, `p.m`, `Sgt`, `Ave`, `St`, …), with a 180-char word-boundary fallback for text that never breaks.
- **Status strip names the hazards** instead of quoting a clause. `extract_weather_status()` used `[^.·]*`, which stopped at the period inside `p.m.` and rendered the dangling *"Heat Warning is in effect now and runs through 8 p"*. It now collects hazard names via `_ALERT_NAME_RE` (longer alternatives first, so "Extreme Heat Warning" doesn't degrade to "Heat Warning") and joins up to three: "Extreme Heat Warning, Flood Watch, Blowing Dust Advisory".
- **Temps chip regex** accepted only "high/low **near** N". The brief writes "low **around** 80", so `lo` was `None` and the `108° / 80°` chip had been silently missing from the strip on every such day. Now accepts `near|around|of`.

**The editorial rule (user's call, 2026-08-03):** on alert days the alert *still leads* the homepage — but as its first sentence only, never the full NWS dump. The hazard list in the status strip carries the rest. Rejected alternative: demoting weather so a real story always leads (in monsoon season Tucson has alerts most days, which would have inverted the problem).

**NWS alert query — fire-weather-zone fix (2026-06-26, commit `c92e407`):** `generate_brief.py` fetches active NWS alerts from a URL in `pipeline/sources.json`. It was querying a single **public forecast zone** (`?zone=AZZ504`), which is **structurally blind to Red Flag Warnings** — those (and all fire-weather products) are issued for a *separate* set of **fire-weather zones** (Tucson = `AZZ150–AZZ154`). On 2026-06-26 a real Red Flag Warning returned 0 results under `zone=AZZ504`, so the brief honestly reported "no active NWS alerts" and leaned on broadcaster reports. **Fix:** query by **point** instead — `?point=32.2217,-110.9694` (the same downtown point as the forecast source). A point query resolves *all* zones over the spot (public + county + fire), so it catches heat warnings, flood watches, AND red flags in one call. Don't revert to a single-zone query. (History: the zone was also wrong before 2026-06-25 — `AZZ540` = Phoenix/Buckeye.) This matters most in monsoon/fire season when Red Flag Warnings are frequent.

**Provenance gate — the fabricated-name control (added 2026-07-28, `provenance_gate.py`):** On 2026-07-23 the brief named the Oro Valley mayor-elect "**David Barrett**." The winner is **Melanie Barrett**; the cited KVOA source only ever said "Barrett," and the given name bled in from *David* Schweikert and *David* Ruiz in the same paragraph. It shipped to the site, the RAG index, and the 7/26 newsletter (which had already sent and could not be recalled). Corrected 2026-07-28, commit `02c60de`.

The synthesis prompt **already** said *"NEVER fabricate facts, names, dates, quotes, or sources. If unsure, leave it out"* (`generate_brief.py`, SELECTION RULES) — and the model did it anyway. That is the whole lesson: **a prompt prohibition is not a control**, because a confabulated fact and a recalled one are indistinguishable from the inside. The model was never *aware* of being unsure. So the control has to be mechanical and has to compare against what the model was actually handed.

`run_provenance_gate()` extracts every capitalized multi-word run from the draft and checks it against the exact `items_block + weather_block + tips_block` that produced it. Three outcomes: **GROUNDED** (full string in sources), **PARTIAL** (only a shorter suffix is — "Barrett" but not "David Barrett", i.e. a modifier was invented), **UNGROUNDED** (no trace). Notes:

- **Currently SHADOW mode** — it classifies, logs, and prints warnings; it never alters the brief and never fails the run (all exceptions swallowed; a bug in the checker must not take down the 6 AM cron). Review `brief-inputs/shadow.jsonl` before enabling `Mode.ENFORCE`.
- **Telegram alert at ~6:01, about 30 minutes before the brief publishes.** `send_provenance_alert()` fires only when there are person-shaped alerts — silent on a clean brief, because a channel that pings every morning gets muted and then protects nothing. Timing was the point, and it is why `run_podcast.sh` moved off 6:10 on 2026-07-28 (first to 7:30 in the morning, pared back to 6:30 that evening). **Reverted to 6:10 on 2026-07-30 at the user's call** — after two days the gate had produced only false positives ("Arizona Constitution", "Tucson Electric Power"), and the split between the 6:05 A/B send and the 6:30 publication was confusing to read in Telegram. The review window is back to ~8 minutes, i.e. effectively none: treat the alert as after-the-fact notification, not a gate. If the gate is ever flipped to ENFORCE, revisit this — enforcement plus no review window means the trim ships unreviewed. The whole publication chain takes ~48s (measured), so the start still clears `check_agendas.sh` at 8:00 and `refresh_ask_index.sh` at 8:45 — the brief is live well before the RAG index rebuilds. Nothing waits on the review: an unread alert publishes anyway, so this is a soft gate, not a block. This does **not** violate the "Telegram only via `run_podcast.sh`" rule — that rule prevents the *brief itself* being sent twice; alerts are a separate message type, exactly like the FOIA and agenda alerts, and go through the same `send_telegram.py`. Non-fatal throughout: a missing sender or a Telegram outage warns and continues.
- **Log everything, alert narrowly.** Every finding is logged, but only *person-shaped* runs raise a warning — misnaming a human is the harm worth interrupting for; an unmatched street or program name is usually noise. Measured on real briefs: ~44 runs/brief, ~14 unverified, **~0–3 person-shaped alerts/day**.
- **Auto-repair is deliberately narrow.** Trimming works for people ("David Barrett" → "Barrett") but produces gibberish for orgs and places ("Sahuarita Unified School District" → "District"). `_repairable()` therefore requires a single invented given name leaving a single bare surname, no punctuation, no acronyms. A botched repair is worse than the unverified name it fixes.
- **`brief-inputs/` archives the source text per brief** (gitignored). Mandatory, not optional: RSS feeds roll over within a day or two, so a flag is unauditable after the fact without it. It is *not* under `BRIEFINGS_DIR` because `run_podcast.sh` globs that path.
- **No fetch-window drift in production.** The gate runs in-process against the exact prompt. A test harness that re-fetches sources later will show phantom flags for stories that have aged out — that is the harness, not the gate.
- **The names bible is not a substitute.** Cross-checking `pipeline/local_names.json` was tried and measured: it matched **0–1 of ~17 names per brief** (it holds 71 local officials; briefs are full of state politicians, suspects, and business owners). Useful as *prompt context* to lower the error rate, useless as an allowlist. Injecting it into `SYNTHESIS_PROMPT` is still queued.

See the `feedback_verify_dont_delegate` memory — this is the same failure shape as every prior data-integrity bug: the pipeline asked a model for something it should have computed.

Usage:
```
# Normal mode — process a single new briefing and refresh derived pages
python generate_post.py ~/.openclaw/workspace/briefings/tucson-brief-2026-02-18.md

# Refresh-only mode — rebuild homepage + briefings.html with no new post
python generate_post.py --rebuild-homepage

# Bulk regen — re-render every individual post HTML from .md sources, then refresh.
# Useful after template changes; not used by cron.
python generate_post.py --rebuild-all ~/.openclaw/workspace/briefings/
```

`generate_post.py` is also the home of the shared chrome — every section index renderer (`agenda_mining.py`, `ai_reporter.py`, `public_record_liquor.py`) imports `ANALYTICS_HTML`, `SUBSCRIBE_PANEL_HTML`, `SCROLL_TRIGGER_JS`, `HAND_RULE_SVG`, `SUNRAY_SVG`, `ARROW_SVG`, `ARROW_LEFT_SVG`, `FEATURED_SUN_SVG`, `site_header_html()`, `section_nav_html()`, `footer_html()`, and `rebuild_homepage()`. One source of truth for masthead, footer, nav, subscribe panel, and SVG primitives.

**Footer is path-aware.** `footer_html(path_prefix="")` takes `""` for root pages and `"../"` for nested pages (so the About link resolves correctly from both `/index.html` and `/posts/YYYY-MM-DD.html`). Every renderer that produces a nested page (`render_meeting_post`, `render_report_post`, the Spotted filing renderer, `render_post`) passes the prefix; root-level renderers call `footer_html()` bare.

## Automation

### OpenClaw and Anthropic API billing

**All AI calls in this pipeline use the Anthropic API via API key** (`"mode": "api_key"` in `openclaw.json`), not a Claude Pro/Max subscription. This was a deliberate architectural decision from day one (February 2026). The Claude Max subscription is used only for interactive sessions (Claude Code, claude.ai).

**Why this matters:** On April 4, 2026, Anthropic officially cut off Claude subscribers from using Pro/Max subscription OAuth tokens with third-party tools like OpenClaw, citing unsustainable infrastructure strain. Users running agents through flat-rate subscriptions were burning $1,000–5,000/day in equivalent API costs. This crackdown does not affect API key users — only subscription-based auth.

**This pipeline is unaffected.** OpenClaw's role here was as a cron scheduler and skills platform, authenticated via API key (it has since been retired — see `ROADMAP.md`). All downstream scripts (`agenda_mining*.py`, `ai_reporter.py`, `generate_podcast.py`) also make direct API calls with the API key from `~/.config/environment.d/anthropic.conf`.

**Monthly API cost:** ~$3–4/month total. Daily briefing (Sonnet) ~$0.09/day, podcast condensation (Haiku) ~$0.01/day, agenda mining (Sonnet) ~$0.50–0.80/month across all four municipalities.

This site is part of a daily pipeline with two stages:

1. **6:00 AM MST** — OpenClaw cron job (`~/.openclaw/cron/jobs.json`) runs the briefing agent (Sonnet 4.6) in an isolated session. The agent reads `TUCSON-BRIEF.md`, fetches sources from `sources.json`, and saves the briefing to `~/.openclaw/workspace/briefings/tucson-brief-YYYY-MM-DD.md`. OpenClaw delivery is set to `"none"` — the agent does not send to Telegram directly.

   **Source-skip mechanism + context-compaction note (2026-06-14):** A source in `sources.json` with `"status": "broken"` OR `"status": "disabled"` is skipped entirely by the agent (rule lives in `TUCSON-BRIEF.md`). `broken` = the feed can't be reached (e.g. Tucson Sentinel, Cloudflare-walled); `disabled` = intentionally paused, still fetchable. Several June briefs ended with a footer note like *"Arizona Daily Star feeds fetched but content unavailable due to context compaction"* — this is the agent honestly reporting that OpenClaw's mid-run context compaction summarized away a heavy feed's raw content before stories could be extracted, so that source contributed ~zero stories those days. The **Arizona Daily Star main feed** (`tucson.com/search/?f=rss&t=article&l=25`) was the main offender: two heavy 25-item Daily Star feeds, and the main one is mostly national/wire/sports with only ~2 of 25 items overlapping the local feed. **Trial fix (2026-06-14):** main feed set `"status": "disabled"`, keeping only **Arizona Daily Star - Local News** (`&c=news/local`) — nearly all hyper-local Tucson coverage. Revert by removing the `status` field. If compaction still drops feeds, the durable fix is an extract-as-you-go instruction in `TUCSON-BRIEF.md` (distill each feed to notes right after fetching, before synthesis).

   **Brief mis-save failure mode (recurring; self-healing fix 2026-06-24):** ~weekly the 6 AM agent generates a perfectly good brief but **saves it to the wrong path** despite an explicit instruction (`TUCSON-BRIEF.md` line 180 says save to `~/.openclaw/workspace/briefings/tucson-brief-YYYY-MM-DD.md`). Observed mis-saves: wrong dir `~/.openclaw/workspace/briefs/` and/or dropping the `tucson-brief-` prefix (e.g. `briefs/2026-06-24.md`, `briefs/2026-05-08.md`, `briefs/2026-05-16.md`, `briefs/tucson-brief-2026-05-26.md` — all missing from the published archive). On a mis-save the whole downstream chain silently stalls (run_podcast.sh waits 10 min for the canonical path, then exits → no blog, no Short, no podcast). **Durable fix:** `run_podcast.sh` now self-heals — `resolve_brief()` checks both `briefings/` and `briefs/` (with/without prefix) for a `*${TODAY}*` file and copies whatever it finds to the canonical path before proceeding. So a future mis-save no longer kills the run. This is an LLM-non-determinism failure (the instruction is unambiguous; the agent ignores it ~5% of days) and is the motivating example for the "Eliminate the OpenClaw dependency" roadmap below — deterministic plumbing (fetch fixed URLs → call Claude → save to a fixed path) should be Python, not an agent.

   **Brief no-network failure mode (self-healing fix 2026-07-07):** the 6 AM `run_brief.sh` shares the laptop's network, so a transient DNS/network blip at 6:00 makes *every* source fetch fail; `generate_brief.py` then sees 0 items and aborts with exit 1 — correctly refusing to publish an empty brief, but stalling the whole downstream chain the same way a mis-save does (no Telegram, blog, Short, or podcast). Happened 2026-07-07 (every host `Temporary failure in name resolution`; network was fine again ~20 min later). **Durable fix:** `run_brief.sh` now wraps the generator in a **retry loop** — default 5 attempts, 60s apart (`BRIEF_MAX_ATTEMPTS` / `BRIEF_RETRY_DELAY` env-tunable), so a short outage self-heals before the 6:30 podcast start (margin was 6:20 when the chain fired at 6:10). Safe to retry because the brief is only written *after* a successful synthesis call (a failed attempt writes nothing). If all attempts fail it exits non-zero, so a real sustained outage still surfaces. Manual recovery when it does miss the window: run `./run_brief.sh` then `run_podcast.sh` once the network is back.

   **Editor's desk — manual story injection (2026-06-19):** A hook for hand-feeding the daily brief stories that aren't in any `sources.json` feed yet (tips, Chamber/PR emails, scoops you catch early). The file `~/.openclaw/workspace/EDITOR-TIPS.md` holds editor-submitted tips; the "Editor Tips" section of `TUCSON-BRIEF.md` instructs the 6 AM agent to read it every run, include any tip whose `[include-through: YYYY-MM-DD]` date is today or later (ranked by the normal Editorial Priorities), and — as the **one explicit exception to the sources.json-only rule** — fetch the vetted link(s) in a tip to confirm details and build attribution. Tips auto-expire (skipped once their include-through date passes), so they never linger. Each tip carries an editor note steering attribution + tone (e.g. soft-hedge single-source items, report political events neutrally) and may instruct withholding specifics (e.g. report a venue as "Oro Valley" without naming the building). To queue one: add an entry to `EDITOR-TIPS.md` in the documented format (what / when / where / details / sources / editor note + include-through date). First use: the June 22 2026 VP JD Vance Oro Valley event, queued 2026-06-19.

   **Self-citation gap — the brief cites outside outlets for stories we reported ourselves (found 2026-07-16, NOT fixed).** `generate_brief.py` synthesizes from `pipeline/sources.json` feeds and has **no knowledge of TDB's own published reporting**, so when a local outlet covers a meeting we recorded, the next morning's brief dutifully credits *them* for *our* story. Concrete case: we published `news-reports/pima-county-2026-07-14.html` — the Pima County BOS 4-1 vote backing the Tohono O'odham Nation's border-wall lawsuit — **on 7/14, the day of the meeting, from our own live-captured transcript, including Chairman Verlon Jose's testimony**. The 7/15 brief then carried the same story sourced to **KVOA** (whose own piece was bylined a news producer, not someone in the room). We were first, we had more, and we handed them the credit line. **This is the aggregation layer cannibalizing the original layer** — precisely the dynamic [[project_aggregation_layer_sunset]] is about, showing up as a concrete bug rather than a strategy question.

   **The rule (user's framing, 2026-07-16): on any morning after we publish a news report, check our own reporting first before citing an outside outlet. "No reason to pull our legs out from under our own reporting."** Until it's automated, this is a manual check on days following a live capture: if the brief's story matches a report in `news-reports/`, the brief should link *us* and, where an outlet genuinely added something we didn't have, credit them for that increment only.

   **Sketch of the durable fix (not built, discuss first):** before synthesis, `generate_brief.py` scans `news-reports/` (and plausibly `meeting-watch/` + `public-record/`) for pieces published in the last ~48h, and passes them to the model as a **"TDB already reported this"** block with an instruction to lead with our own link and treat feed items on the same story as corroboration. The matching is the hard part (headline/entity overlap, not exact strings) and it's the same shape as the agenda-vs-transcript cross-reference described under the AI Reporter's "deaf to items that pass without discussion" gap — both are "diff what we know against what we said." Worth considering whether one utility serves both.

2. **6:10 AM MST** — System cron triggers `~/.openclaw/skills/tucson-daily-brief/scripts/run_podcast.sh`, which waits for the `.md` file, then runs in this order: sends to Telegram (via `send_telegram.py`) → generates blog post + git push → **generates + auto-publishes the daily short-form video to YouTube Shorts** (`social/generate_short.py --publish`) → generates condensed podcast script (via Claude Haiku) → generates podcast audio (ElevenLabs TTS) → uploads RSS/R2 → generates YouTube video → uploads to YouTube. The blog post runs **before** and **independently of** the podcast, so a podcast failure (e.g. ElevenLabs quota exceeded) never blocks the blog. Each distribution step is non-fatal.

   **Daily Short (auto, added 2026-06-23):** after the blog post, `social/generate_short.py --publish` has Haiku pick an *evergreen feel-good* story from the last 14 days of `posts/`, write a facts-only beat script (anti-hype + dedup against already-used stories), render a 1080×1920 "Only in Tucson" clip with its own AI music, and **publish it public to YouTube Shorts unattended** (no review gate — user's call, full auto while it's YouTube-only). Exits non-zero (non-fatal) on days with no fresh feel-good story. See the "Social Media Cards" / `SHORT-FORM-VIDEO.md` for the full design. **"Buried in the Agenda" (the moat series) went live 2026-07-11 as a weekly Monday auto-short** — `social/generate_agenda_short.py`, run by `check_agendas.sh` on Mondays (see the "Marketing & Distribution Strategy" section for the full design).

Telegram delivery happens **only** through `run_podcast.sh` → `send_telegram.py`, which reads the saved `.md` file. OpenClaw's cron delivery was disabled to prevent duplicate sends of raw agent output.

### Podcast script condensing

The podcast script is condensed from a full ~7,500-char read (~8 minutes) to a tight ~1,400-char read (~90 seconds) using Claude Haiku. This was implemented to stay within the ElevenLabs Creator tier (100K chars/month, $22/mo). The `condense_script()` function in `generate_podcast.py` sends the full script to Haiku with instructions to pick the top 5 most newsworthy stories, drop weather/source attributions/section transitions, and write in broadcast style. Cost: ~$0.01/day. Falls back to the full script if the API call fails.

**ElevenLabs budget:** Creator tier, 100K chars/month. Condensed podcast uses ~45K chars/month (with Turbo v2.5 at 0.5 credits/char, that's ~22.5K credits/month). Usage-based billing enabled at 25,000 credit threshold as safety net.

## The 🎓 Schools section (`school_news.py`, added 2026-08-04)

Nine Tucson-metro K-12 districts read on their own channels, feeding a dedicated
brief section between Development & Business and Officials. Full per-district
route map, platform notes and the blocked-district detail live in
`SCHOOL-DATA-FEASIBILITY.md` § E; the module's own docstring carries the rest.

**Why it is a module, not entries in `sources.json`.** A district's news page is
an official channel. In the news-items block the model can only read it as
reporting, so "the district said X" becomes "X" — the exact failure that moved
Kelly's and Gallego's Bluesky out of `tier_2_officials` on 2026-07-30. Adding a
district feed to `sources.json` would re-create it. Don't.

**What it does differently from `officials_watch.py`:**

- **Repeat suppression by brief date, not fetch time.** Districts post a few
  times a week, so a 36h window misses anything posted over a weekend; the
  window is 96h and `pipeline/.school_news_seen.json` (gitignored) records the
  brief date an item was first offered. An item is dropped only if it went to an
  *earlier* brief — so a same-day re-run (run_brief.sh retries five times) still
  sees its own items and a failed brief never loses a story. `--dry-run` passes
  `mark_seen=False` so a dry run cannot consume tomorrow's items.
- **Blocked districts are named in the prompt.** Vail, Flowing Wells and Tanque
  Verde sit behind Finalsite's Client Challenge and cannot be read at all. A
  section built from six that reads as "the schools news" would assert coverage
  we don't have, so `build_block()` emits a NOT CHECKED TODAY line and the
  prompt forbids implying full coverage or reporting those districts as quiet.
  Same distinction as officials_watch's silent-vs-unchecked rule.
- **Hard selection rules in the prompt.** Most district posts are parent
  logistics (hiring, supply lists, spirit weeks, photo-use policies). The
  SCHOOLS rules in `SYNTHESIS_PROMPT` enumerate the exclusions and require
  district-wide or metro consequence, cap the section at 1-3 items, and say
  outright that omitting the section beats padding it. There is also a FERPA
  rule: never identify an individual student, whatever the district published.
- **Convergence is the story.** When several districts announce the same thing
  the prompt merges them into one item naming the districts. First run
  (2026-08-04) surfaced exactly that: Marana, Sunnyside and Sahuarita all posted
  Arizona Healthy Schools Act guidance within three days.

Both guards from `officials_watch.fetch_source` are carried over and matter more
here, because five of the six working adapters are HTML scrapes: zero extractor
rows is treated as breakage (a live district page always has posts), and rows
found with no parseable date is treated as a date-format change rather than as a
quiet district.

`schools_block` is also joined into the provenance gate's `sources` string. A
name that reached the brief only through a district announcement *is* grounded,
and leaving the block out would flag it as fabricated.

## Sources that look healthy while delivering nothing (2026-07-29)

One day produced three variants of the same failure, and none of them raised an
error. Check for all three when auditing sources — a green log line is not
evidence that a source is working.

**1. Silently skipped.** `load_sources()` read each tier with
`data["sources"].get(tier, [])` against a hardcoded `tier_order`.
`tier_2_institutional` was never added to that list, so seven feeds — City of
Tucson, TEP, Sun Tran, RTA, UA News x2, PCAO, six of them priority=high — were
absent from every brief for 34 days. No error, no log line. Now raises on any
tier not in `tier_order`, and an unrecognised source `type` fails loudly instead
of `continue`-ing.

**2. Live URL, dead content.** Congressional and Senate `rss.xml` files return
HTTP 200 with content months old — Ciscomani stopped at 2026-05-28 while his
press page was current to 07-24, Grijalva at 01-29 against a 07-22 page, Kelly
at 06-02 against 07-28. A feed reader sees success. Reading them would have
injected stale content into a daily brief. This is why `officials_watch.py`
scrapes pages and never feeds.

**3. Dormant channel.** AZPM was read through their Bluesky account, which went
quiet on 2026-06-27. A dormant account returns success with zero items forever.
AZPM went from 12 of 27 June briefs to **0 of 29 in July** and nothing surfaced
it — the failure was only visible by counting citations per month. They publish
no RSS at all, so `scrape_azpm.py` now reads their story index.

**The audit that finds these** is not "did the fetch succeed" but "when did this
source last actually appear in a brief":

```bash
for m in 2026-05 2026-06 2026-07; do
  printf "%s: %s of %s\n" "$m" \
    "$(grep -l -i "SOURCE NAME" posts/$m-*.html | wc -l)" \
    "$(ls posts/$m-*.html | wc -l)"
done
```

A high-priority source at zero for a month is the signal. Nothing else caught any
of these three.


## Model cost + the bake-off (re-baselined 2026-07-30)

`CLAUDE.md` carries the summary table; this is the detail and the method.

**Measure, don't estimate.** The synthesis call already logs its own usage —
`Synthesis: model=… in=… out=… thinking=… stop=…` in `/tmp/brief-gen.log`. Read
that line before quoting any cost figure. Two estimation traps, both hit on
2026-07-30 and both worth ~2x:

1. **Opus 5 thinks by default.** `call_claude()` sends no `thinking` param.
   On Opus 4.8/4.7 that meant no thinking; on **Opus 5 it runs adaptive
   thinking**. 4,879 of 8,091 output tokens on the 7/30 run were thinking,
   billed as output. Reduce with `output_config.effort` if wanted — untested.
2. **Tokenizers are not comparable.** The identical 79,643-char prompt is
   **34,908 tokens to Claude and 21,647 to GPT-5.6** (~61% more for Claude).
   Comparing vendors on $/MTok alone is meaningless; compare measured $/run.

### Per-model cost for one brief (measured 2026-07-30)

| model | in | out | $/run | $/month |
|---|---:|---:|---:|---:|
| Opus 5 (production) | 30,043 | 8,091 | 0.3525 | $10.57 |
| GPT-5.6 Sol | 21,647 | 3,810 | 0.2225 | $6.68 |
| GPT-5.6 Terra | 21,647 | 3,350 | 0.0835 | **$2.50** |
| Sonnet 4.6 (est., no thinking) | 30,043 | ~3,200 | 0.1381 | $4.14 |

OpenAI cut Terra 20% and Luna 80% on 2026-07-30 (Sol unchanged). Prices in
`brief_model_ab.py` are **short-context** rates; OpenAI charges roughly double
for long context without publishing the boundary. Re-check if the prompt grows.

### The bake-off harness

`brief_model_ab.py` runs challengers on the **byte-identical** prompt the
published brief was built from, captured by monkeypatching `generate_brief.
call_claude` (the `ERROR: synthesis failed` line in the log is that working as
designed). Publishes nothing; writes only to `brief-bake-off/`.

- `--models sol,terra` / `--cheap` / `--no-telegram`; **cron names its arms
  explicitly** (`--models sol,terra,flash --no-telegram` as of 2026-07-31, in
  `run_brief_ab.sh`) — leaving it bare would run every challenger and silently
  multiply the daily cost. `run_brief_ab.sh` is the source of truth for which
  arms actually run; check it before quoting the set here.
- The `opus` arm exists for when the prompt has changed since publication (a
  prompt or source-layer edit). Otherwise the copied champion brief stands in.
- The provenance gate runs on every arm and is the one automated quality signal.
  **Caveat: as of 7/30 every gate finding across four models was a false
  positive** — `Tucson Daily Brief` (our own masthead), `Two A-10s` (an
  aircraft), `Pascua Yaqui Tribe's`, `National Weather Service`. Tune
  `person_shaped()` before trusting it or enforcing on it.

### State of the experiment (2026-07-30, n=1 — decide nothing yet)

Luna is out: it buried a four-day Extreme Heat Warning last in the brief on the
morning it took effect, consistently across runs. Judgment failure, not polish.

Sol vs Terra is the live race. After the officials-block fix Terra produced 823
words at **97% grounded** — the best rate of the three — for $0.084/run against
Opus 5's $0.419. Opus 5 remains the most complete (1,147 words) but is ~4.2x
Terra's cost and spends ~4x the reasoning tokens, so the arms are not
like-for-like on effort. Needs several more days before any decision.

### DeepSeek V4 arms (added 2026-07-31)

DeepSeek shipped **V4-Flash-0731** the morning of 2026-07-31 (`deepseek-v4-flash`,
public beta; V4 itself landed 2026-04-24). Two arms are wired: `flash` and
`dspro`. Both are 1M context / 384K max output and **think by default**, left on
because the champion does too.

| arm | model | in $/MTok | out $/MTok | est. $/mo at the brief's workload |
|---|---|---:|---:|---:|
| `flash` | deepseek-v4-flash | 0.14 | 0.28 | **~$0.20** |
| `dspro` | deepseek-v4-pro | 0.435 | 0.87 | ~$0.61 |

That is ~1/50th of Opus 5 and cheaper than Luna, the previous floor. Notes that
cost something when forgotten:

- **The endpoint is OpenAI-compatible**, so the arm reuses `run_chat()`.
  `PROVIDERS` now carries per-provider url/env/`token_param` — DeepSeek wants
  `max_tokens`, GPT-5.x rejects it and needs `max_completion_tokens`. There is
  also an Anthropic-shaped endpoint at `https://api.deepseek.com/anthropic`,
  unused here.
- **Cache-hit input is priced 50x lower** ($0.0028 vs $0.14). `run_one` applies
  the split only for specs carrying `cache_price`, so the OpenAI/Anthropic arms
  keep pricing all input at full rate and stay comparable to earlier
  `ab.jsonl` rows.
- **⚠ Peak-hour surcharge: 2x on everything** during Beijing 09:00–12:00 and
  14:00–18:00 = **18:00–21:00 and 23:00–03:00 MST**. The 6:05 AM cron lands at
  21:05 Beijing, off-peak; ad-hoc evening runs may not be.
- **The "Opus-level" framing is a coding claim, and it is about Pro, not Flash.**
  V4-Pro beats Opus 4.8 on competitive programming and long-context retrieval
  but trails it on SWE-bench Pro (52.1 vs 69.2); Flash sits well below both.
  None of that predicts news synthesis from a 30K-token source pile — which is
  the whole reason this harness reads briefs instead of benchmarks.
- **PRC hosting is a settled question — don't re-raise it.** The user weighed it
  2026-07-31 and is fine with it: the inputs are already-public news text. Judge
  these arms on output quality like every other challenger.

#### First Flash run (2026-07-31, n=1)

`in=19,367 out=36,625 thinking=34,316 finish=stop 328s $0.0123`, 880 words.

- **It thinks ~7x harder than Opus 5** — 34,316 thinking tokens against Opus's
  4,879, and **93% of its output tokens were thinking**. The first attempt
  returned **empty text** at the shared 16K budget: `finish=length`, all 16,000
  tokens spent reasoning. The V4 arms therefore carry `max_tokens=48000`. If
  another thinking model is ever added, check this before trusting a null result.
- **Latency is the real cost, not dollars.** 328s vs the ~30-60s the other arms
  take. Still fine for a 6:05 AM batch job that publishes nothing; would matter
  if it were ever promoted to the 6:00 AM production path, which has a retry
  loop and a podcast chained behind it.
- **Two harness bugs this surfaced,** both fixed: `max_tokens` was a single
  shared constant, and `run_one` counted a billed-but-empty response as a
  success ("1/1 arms succeeded" for a run that wrote no brief).
- **Do not read this run as a head-to-head.** It was a manual re-run ~45 min
  after the 6:00 AM fetch, so Flash saw a *larger* news window than the champion
  did — it correctly reported the Pueblo High interim-principal story (Alma
  Mejia-Garcia / Frank Rosthenhausler, verified against the cited Star URL) that
  simply did not exist in production's source pull. Only the cron run, on the
  byte-identical prompt, is a fair comparison.

**Editorial read, Flash vs the 7/31 champion** (16 story items each — Flash is
not covering less, it is covering *differently* and writing ~30% tighter):

| | Flash | Opus 5 champion |
|---|---|---|
| Missed by the other | AZ Supreme Court clergy ruling, Hobbs-declines-to-debate as its own item, Gehrke family interview, hunger-relief merger, KXCI blues show | bat-exposure record, Tucson Fire call volume, $174M city bonds, back-to-school, Wyyerd fiber deal, Sun Tran Aug. 16 changes, Community on Wheels closing |
| Lean | courts + human interest | institutional + service journalism |

- **Flash caught the clergy ruling and Opus dropped it** — churches, not judges,
  decide when a confession of child abuse must be reported (Cochise County
  case). Four outlets carried it and **all four were in the 6:00 AM sources**,
  so this is a real champion miss, not a timing artifact. Verified by grepping
  `brief-inputs/<date>.sources.txt` — do that before crediting either arm with
  a "find."
- **Opus is better where depth shows:** the weather item (issue time, hard
  expiry, named zones, cooling-center/pet gap vs Flash's bare "through Sunday" —
  the most actionable item on a 111° day), explanatory asides ("a notice of
  claim is a required precursor to a lawsuit, not a filed suit"), naming the ICE
  arrestee as the Star did, and crediting **Votebeat** as originating the ballot
  story that Luminaria and Mirror republished. Flash credited the republishers.
- **Latency (328s) is not a real objection** — the user's call 7/31. This is a
  batch job with an 8-hour runway that publishes nothing. Judge these arms on
  brief quality.

#### The bracket bug was ours, not Flash's

Flash emitted `**[Headline.]**` on every story, which `md_to_html()` renders as
literal `<strong>[Headline.]</strong>`. Root cause was **`SYNTHESIS_PROMPT`
overloading square brackets** — placeholder notation (`**[Headline.]**`) on the
same line as literal markdown link syntax (`[Source Name](url)`). Opus resolved
the ambiguity; Flash took it literally, which is the correct reading of what the
prompt actually said. Fixed 2026-07-31 by showing real output instead of
placeholders, plus an explicit "no brackets" line; verified live on both flash
and sol. **General lesson: a challenger rendering the prompt literally is a
prompt bug, not a model defect — check the instruction before blaming the arm.**

### Grok 4.6 arm (added 2026-08-12, launch day)

SpaceXAI shipped **Grok 4.6** the morning of 2026-08-12 (`grok-4.6`, verified
against `api.x.ai/v1/models` the same day; AA Intelligence Index 61 — ties
GPT-5.6 Sol, one behind Fable 5, two behind Opus 5). Wired as the `grok` arm in
**both** `brief_model_ab.py` and `newsletter_model_ab.py` (the user wants it
read on both surfaces); key in `~/.config/environment.d/xai.conf`
(`XAI_API_KEY`). In the daily cron set as of 2026-08-12
(`--models sol,terra,flash,grok`).

| arm | model | in $/MTok | out $/MTok | measured $/run | est. $/mo |
|---|---|---:|---:|---:|---:|
| `grok` | grok-4.6 | 2.00 | 6.00 | **$0.106** (n=1) | ~$3.20 |

Notes that cost something when forgotten:

- **xAI reports reasoning tokens DISJOINT from `completion_tokens`** — unlike
  OpenAI and DeepSeek, where `reasoning_tokens` is a subset. First run measured
  11,331 reasoning vs 2,221 completion; pricing `completion_tokens` alone
  undercounts ~2x ($0.054 vs the true $0.106). `PROVIDERS["xai"]` carries
  `reasoning_extra=True` and `run_chat()` adds reasoning into `out` for such
  providers. **If another vendor is ever added, measure which convention its
  usage block follows before trusting the first cost figure.**
- It's a thinking model with no off switch observed, so it carries the
  DeepSeek-style `max_tokens=48000` headroom rather than the shared 16K.
- Cache-hit input is $0.50/MTok — only a 4x discount, so per the DeepSeek-era
  rule it is NOT priced separately (the daily prompt never repeats anyway).
- **First-run editorial signal (n=1, ad-hoc ~10:20 AM prompt, not the 6:05
  one):** 844-919 words across two runs, ~15 headlines, ~140-170s. Both gate
  UNGROUNDED findings were **gate false positives**, not fabrications: "Officials
  Are Saying" (a section header) and "Tucson Unified School District's" — the
  possessive with a curly apostrophe failed the exact-string lookup even though
  "Tucson Unified School District classrooms" sits verbatim in the sources.
  **Gate-tuning queue: normalize possessives ('s / ’s) before lookup.**
- The newsletter arm runs whatever `CHALLENGERS` holds (no cron — manual
  Saturday via `run_newsletter.sh` step 3), so grok joins the next send's
  bake-off automatically. `newsletter_model_ab.py`'s `run_chat()` now honors
  per-spec `max_tokens` (it silently ignored it before — same class of bug as
  the shared-16K one Flash surfaced).
