# Tucson Daily Brief Site

Static blog for GitHub Pages — no JavaScript, no frameworks, no build tools. Live at `https://tucsondailybrief.com`.

The project is a one-person local news operation for the Tucson metro: a daily aggregated brief, plus an original-journalism layer (meeting previews, AI-drafted post-meeting news reports, surfaced public filings, development watch), plus distribution surfaces (podcast, newsletter, crossword, social, RAG-powered Ask).

## Reference docs — read these before working in an area

CLAUDE.md is the index. Detail lives in these; **when you learn something durable, add it to the relevant doc, not here.**

| Doc | Covers |
|---|---|
| `BRAND-BIBLE.md` | **Consult first for any "write copy representing TDB" ask** — the three registers, canonical strings, schoolmarm rule, banned phrases, boilerplate intros, source map to the per-surface docs |
| `PIPELINE.md` | Daily brief renderer internals, the 6:00/6:10 AM cron chain, Anthropic billing posture, recurring failure modes (mis-save, no-network, weather-alert briefs, NWS fire-zone fix, editor's desk, self-citation gap) |
| `MEETING-WATCH-PIPELINE.md` | The four agenda miners, publishing flow, slug routing, canceled-meeting guard, renderer bugs |
| `AI-REPORTER.md` | Live + VOD transcription → news report: architecture, usage, schema, scheduler, stream URLs, STT research |
| `NAMES-BIBLE.md` | `pipeline/local_names.json` — canonical names, Deepgram misreads, the pronouns rule, the queued audit |
| `ORIGINAL-JOURNALISM.md` | Planned content types (Old Pueblo Speaks, Tracking, FOIA Lead Spotter, Deep Read…), post-meeting data-source matrix, story ideas |
| `RAG.md` | Ask / `tdb-ask` — architecture, gotchas, Fly.io hosting, Phase 3 backlog |
| `NEWSLETTER.md` | TDB Weekly — voice, format, pipeline, Buttondown, sender architecture |
| `CROSSWORD.md` | The Tucson Mini — editorial posture, generation, wordbank, Saturday ritual |
| `SOCIAL-CARDS.md` | `social/` renderers, themes, aspect ratios, render-sharpness lessons |
| `MASCOT.md` | The friendly-saguaro brand character (parked 2026-07-28): decision trail, local-only assets, the patch-compositing animation pipeline, viseme lip-sync plan, prompt kit, resume checklist, light-content-only rule |
| `CHARACTER-ANIMATION-STACK.md` | How to animate the mascot — why generative video is wrong for cel art (HeyGen bake-off data), the three build tiers, RTX 5080 / LoRA + synthetic-data bootstrap, Character Animator vs Hedra, and the "could this be a product" note |
| `SHORT-FORM-VIDEO.md` | Platform automation map + DIY publish-adapter plan |
| `SOCIAL-AUTOPOST.md` | Auto-posting feasibility per platform; Facebook strategy |
| `X-PLAYBOOK.md` | X brand account (manual posting): the Pirate Wires thread pattern, link-penalty mechanics, profile kit, thread template |
| `MARKETING.md` | Two-brand split, the distribution loop, content mix, instrumentation TODOs |
| `IG-BTS-STRATEGY.md` | IG behind-the-scenes content: the "outcome + accountable human, never the tooling" principle, format buckets, founder-post signal, variable-isolation test |
| `ROADMAP.md` | **Everything queued and gated** — Responsiveness, coverage expansion, structured-data layers, Data Center Watch, Tucson en Breve, off-the-laptop migration, Spotify |
| `REDESIGN-V2.md` / `REDESIGN.md` | Visual language / information architecture (both shipped 2026-05-11) |
| `*-DATA-FEASIBILITY.md`, `COVERAGE-EXPANSION.md` | Per-municipality and school-district data scans (OV, Marana, Tucson, schools, courts/probation) |
| `crime.md`, `crime-tpd-data.md` | The FBI-reporting-gap story research |
| `powerbi.md` | The PCAO PowerBI forensics — frozen dashboard swap, both reports' contents, ME live dashboard, reusable probing techniques |
| `k3-data-plan.md` | The civic-transparency pipeline plan — pollers→snapshots→surfaces, build order (PCAO Watch → crime poller → ME rollup → AOC → §1-605), guardrails |

Strategy/impact notes live **outside this repo** in `~/claude-code-projects/tucson-daily-brief-notes/` (deliberately private).

**History:** CLAUDE.md was a single 159k file until 2026-07-17, when it hit the 150k context limit and was split into the docs above. The prose moved verbatim — nothing was dropped — but if something seems missing, the pre-split original is `git show 30d8853:CLAUDE.md`.

## Standing rules

These are the ones that cost something when forgotten:

- **No fabrication in reader-facing output, ever.** Verify every factual claim before it ships. Highest-scrutiny category is pop-culture/celebrity clues and any card or short. See the `feedback_ai_content_quality_bar` memory.
- **Derive, don't ask a model.** Every data-integrity bug found so far was the pipeline asking a model for something it should have computed — a list length, a lookup-table value, an editorial judgment about a meeting with no agenda. The model answers plausibly each time. See `feedback_verify_dont_delegate`.
- **A name that entered via transcript is a hypothesis, not a fact,** until an external source confirms it. See `NAMES-BIBLE.md`.
- **Cite our own reporting first.** On any morning after we publish a news report, the brief must credit us — not an outside outlet — for a story we broke. Automated in the brief since 2026-08-12 (`build_own_reports_block()` injects recent `news-reports/` into the synthesis prompt); still a manual rule on every other surface (newsletter, social, cards). History in `PIPELINE.md`.
- **Don't start new section-scale features mid-build.** Capture in `ROADMAP.md` with a gate, then drop it. See `feedback_resist_feature_creep`.
- **Two-brand split:** reader-facing surfaces lead with the outcome ("The Tucson news you'd otherwise miss"); the AI/tooling story belongs on About, LinkedIn, and industry conversations.

## Editorial model

- **Agenda previews** (forward-looking "What to Watch") publish automatically, no human review — they summarize what's on the agenda. Low risk, high value in timeliness.
- **Post-meeting reporting and all other original journalism** is human-reviewed before publishing — no exceptions. AI drafts and flags; a human reviews, edits, approves.
- **Daily Shorts + the weekly "Buried in the Agenda" Short** are the deliberate exception: full-auto to YouTube, user's call, while they stay YouTube-only.
- Each piece carries a clear disclosure about AI involvement.

**Coverage area:** the Tucson metro broadly — City of Tucson, Pima County, Marana, Oro Valley, and their governing bodies, commissions, and public records. Not limited to city limits. TDB's north star is depth in an under-covered metro, not statewide expansion (see the `project_editorial_thesis` memory).

## Project Structure

```
├── style.css                    # Warm-organic Southwest editorial CSS (REDESIGN-V2.md)
├── generate_post.py             # Daily brief renderer + ALL shared chrome; rebuilds homepage, archive, sitemap, RSS
├── generate_brief.py            # Deterministic 6 AM brief generator (replaced the OpenClaw agent)
├── index.html                   # Auto-generated zoned homepage (NOT the daily archive)
├── briefings.html               # Auto-generated full daily-brief archive
├── posts/                       # Daily brief HTML (YYYY-MM-DD.html)
├── meeting-watch.html + meeting-watch/     # Agenda previews (auto-published)
├── news-reports.html + news-reports/       # AI-drafted, human-reviewed reports
├── public-record.html + public-record/     # "Spotted" — display name only; URL kept for back-comat
├── around-town/                 # Development-watch feed items
├── in-depth/                    # Deep dives
├── ask.html                     # Live RAG Q&A; POSTs to Fly app tdb-ask
├── responsiveness.html          # Coming-soon stub (dashboard not built)
├── about.html                   # Hand-authored — NOT generated by any pipeline
├── ai_reporter.py               # transcript JSON → Claude report → Telegram → publish
├── ai_reporter_live.py          # streamlink/HLS → Deepgram WebSocket → transcript JSON
├── ai_reporter_vod.py           # ffmpeg → Deepgram batch → transcript JSON (VOD fallback)
├── run_live_reporter.sh         # Live reporter wrapper (env, dep validation)
├── schedule_recording.py        # Auto-schedules live-capture `at` jobs from previews
├── pipeline/                    # Version-controlled brief config
│   ├── sources.json             # News source config (status: broken|disabled = skip)
│   ├── local_names.json         # Names bible — see NAMES-BIBLE.md
│   ├── records_custodians.json  # Verified public-records channels per government — never guess an entry
│   ├── EDITOR-TIPS.md           # Hand-submitted leads, read each run
│   └── TUCSON-BRIEF.md          # Original agent rules — REFERENCE ONLY (rules now in generate_brief.py)
├── school_news.py               # 9 metro K-12 districts' own news channels → the brief's 🎓 Schools section
├── agenda_mining.py             # Pima County BOS (Legistar API) + shared render/publish helpers
├── agenda_mining_marana.py      # Marana (Destiny Hosted scrape)
├── agenda_mining_orovalley.py   # Oro Valley (Destiny Hosted scrape)
├── agenda_mining_tucson.py      # City of Tucson (Hyland OnBase PDF + pdftotext)
├── agenda_mining_sahuarita.py   # Sahuarita Unified governing board — FIRST school district; schedules captures from the board's published annual calendar, not the agenda
├── check_agendas.sh             # 8 AM cron: 4 miners + Sahuarita board + Spotted + dev watch + auto-publish + push
├── public_record_liquor.py      # Spotted pipeline: liquor filings out of agenda references
├── public_record_liquor_dllc.py # Spotted: Marana liquor licenses via AZ DLLC database diff (no AI calls)
├── foia_lead_spotter.py         # Weekly: trawl news-reports/ for records-request leads → web-search verify facts/prior-disclosure → draft §39-121 emails → Telegram
├── promise_tracker.py           # "What They Promised" evidence ledger: transcript → validated-verbatim promise rows → human review (ORIGINAL-JOURNALISM.md)
├── praise_tracker.py            # Praise × pending business: officials praising named private entities, joined vs agendas/filings (ORIGINAL-JOURNALISM.md)
├── promise-ledger/              # Working dir (gitignored): promises.sqlite + review sheets
├── run_foia_spotter.sh          # Cron wrapper (Mondays 9:30 AM)
├── records-requests/            # Working dir (gitignored — repo is public, unsent requests stay local)
├── dev_watch_marana.py          # Marana development watch (ArcGIS poll/diff)
├── dev_watch_orovalley.py       # Oro Valley development watch (ArcGIS poll/diff)
├── rag/                         # Ask — build_index.py, ask.py, server.py, index.sqlite (gitignored)
├── refresh_ask_index.sh         # 8:45 AM cron: rebuild index + fly deploy
├── Dockerfile + fly.toml        # Ask service image (app: tdb-ask)
├── crossword/                   # The Tucson Mini — play.html, tools/, puzzles/
├── generate_newsletter.py       # TDB Weekly draft generator
├── upload_to_buttondown.py      # Push draft to Buttondown as editable draft
├── run_newsletter.sh            # Manual Saturday wrapper (NOT cron'd)
├── social/                      # Card + short-form renderers; cards/ output gitignored
├── responsiveness/PLANNING.md   # Responsiveness Index — planning only
├── people-photos/               # Official portraits + manifest — research only, not wired in
├── transcripts/                 # Working dir (gitignored)
├── agenda-watch/                # Working dir: previews + full references (not published)
├── newsletter/drafts/           # Working dir (gitignored)
├── CNAME, .nojekyll, robots.txt, 404.html, sitemap.xml, rss.xml
└── CLAUDE.md
```

## How It Works

`generate_post.py` takes a briefing markdown file, extracts the date from the filename, converts markdown → HTML, writes `posts/YYYY-MM-DD.html`, then calls `rebuild_homepage()` — which scans all posts plus the newest entry in each section directory and rebuilds `index.html`, `briefings.html`, `sitemap.xml`, and `rss.xml`. Idempotent; running twice overwrites cleanly.

```bash
python generate_post.py ~/.openclaw/workspace/briefings/tucson-brief-2026-02-18.md  # normal
python generate_post.py --rebuild-homepage                                           # refresh only
python generate_post.py --rebuild-all ~/.openclaw/workspace/briefings/               # bulk re-render
```

**`generate_post.py` is the single source of truth for shared chrome.** Every section renderer (`agenda_mining.py`, `ai_reporter.py`, `public_record_liquor.py`, `dev_watch_*.py`, `render_indepth.py`) imports from it: `ANALYTICS_HTML`, `SUBSCRIBE_PANEL_HTML`, `SCROLL_TRIGGER_JS`, the SVG primitives (`HAND_RULE_SVG`, `SUNRAY_SVG`, `ARROW_SVG`, `FEATURED_SUN_SVG`…), `site_header_html()`, `section_nav_html()`, `footer_html()`, `rebuild_homepage()`, plus the SEO helpers (`seo_head_html()`, `news_article_jsonld()`, `derive_description()`, `extract_headline()`) and the topic-flag layer (`TOPIC_DEFS`, `detect_topics()`, `topic_badge_html()`).

**Footer is path-aware:** `footer_html(path_prefix="")` for root pages, `"../"` for nested ones. Renderers producing nested pages must pass the prefix. Nested renderers also take a `page_slug` kwarg so canonical/OG URLs match the real file.

**Feature flag `SHOW_TOOLS`** (currently `False`) gates only the secondary Tools nav row (just Responsiveness) and the homepage Tools card row. Ask is **not** gated — it's linked site-wide as ChatTDB. Flip when the Responsiveness dashboard ships.

**Live nav (top level): Daily Briefs · Local Government · Around Town · In Depth · ChatTDB.** The Local Government hub has two children, surfaced as a second nav row on hub-child pages: **What to Watch** (meeting previews) and **What They Decided** (post-meeting reports).

**Display-name renames are display-only** (same trick as Spotted): the reader-facing labels changed but URLs, directories, `_NAV` active-keys, module names, and CSS classes kept the original terminology. Two rename waves:
- **2026-06-24 IA reorg** → the topic-hub structure above (see `IA-REORG.md`).
- **2026-07-18 reader-facing labels** (from `~/claude-code-projects/tucson-daily-brief-notes/sol-new-names.md`): Meeting Watch/Local Meeting Previews → **What to Watch**; News Reports/Local Meeting Reports → **What They Decided**; Deep Dives → **In Depth**. URLs stayed `meeting-watch.html`, `news-reports.html`, `in-depth.html`; nav keys stayed `meetings`/`reports`/`indepth`. Renderers plus a one-time HTML sweep over published pages carried the change; "Spotted" and "Daily Briefs" were already good and kept.

**"Spotted" is a display rename only.** URL stayed `public-record.html`, directory stayed `public-record/`, and all code (module names, CSS class `public-record-filing`) keeps the original terminology. Only user-facing text changed.

Detail on renderer internals and the recurring failure modes → `PIPELINE.md`.

## Input Format

Briefing files land in `~/.openclaw/workspace/briefings/tucson-brief-YYYY-MM-DD.md` (a pipeline contract with `run_podcast.sh` — don't relocate). They have a title line, emoji section headers (🏛️ Government, 🚨 Public Safety…), bold story headlines with descriptions, source citations prefixed 📰/📄, ─── separators, a weather section, and trailing metadata (stripped during conversion).

**Source citations** come in two forms, both handled by `linkify_sources()` in `generate_post.py`: markdown links `📰 [Source Name](https://direct-url)` (preferred — links to the article) or plain text `📰 Source Name` (falls back to the outlet homepage via the `SOURCE_URLS` dict).

## Design

Visual language is **warm-organic Southwest editorial**, shipped 2026-05-11. The original Daring Fireball restraint is gone and should never be reverted to (see `feedback_tdb_visual_direction`). Full plan in `REDESIGN-V2.md`; `redesign-preview.html` is a self-contained reference.

**Tokens (`style.css :root`):**
- Locked palette: sand `#f5f0e6`, tan `#e8dfd1`, terracotta `#c75b39` / dark `#a84a2e`, sage `#7a8b6f`, brown `#3d3029` / light `#5c4a3f`
- Warm-only extensions: bone `#faf4e8`, adobe `#d97048`, clay `#8c3a1f`, dusk `#4a382c`, shadow `#251c17`, dust (hairlines) `#c7b9a4`
- Type: Fraunces (display, `WONK` axis on) + Newsreader (body) — both variable, both Google Fonts
- Containers: reading 640px, editorial 1040px, full 1280px. Mobile breakpoint `max-width: 880px`. 8px vertical rhythm, sections in multiples of 24px.

**Signature moves:** fixed drifting sun-cast gradient (`body::before`), SVG paper-grain overlay (`body::after`), grain bleed under the featured headline, the desert-sun motif on the homepage feature (desktop only), hand-drawn animated SVG underlines under section heads on daily briefs only, drop caps on brief ledes.

**Masthead tagline: "The Tucson news you'd otherwise miss, by Nicholas De Leon."** Baked into every published page — changing it means editing `generate_post.py` **and** string-replacing across all published HTML (use Python, not sed — `&` entities break sed). Kicker reads "From the Old Pueblo".

**Footer links:** About, Apple Podcasts, YouTube, Facebook, Instagram, Bluesky, X, LinkedIn, Email. Facebook (`facebook.com/tucsondailybrief`) + Instagram (`instagram.com/tucsondailybrief`) added 2026-07-19, Bluesky (`bsky.app/profile/tucsondailybrief.com`, the custom-domain brand handle) added 2026-08-04 — these are **brand accounts**, so linking them is consistent with the standing preference that *personal* social not be connected to the site. X (and, until the brand account existed, Bluesky) was removed 2026-05-15 for that personal-social reason; the X **brand** account (`x.com/tusdailybrief` — 15-char handle limit, see X-PLAYBOOK.md) went into the footer 2026-08-08 at the user's request. Adding/removing a footer link means editing `footer_html()` in `generate_post.py` **and** sweeping it into already-published HTML (Python, not sed — match the adjacent-anchor pair so only the footer is touched; `redesign-preview.html` has no live footer and is skipped).

**Analytics:** GA4 via `gtag.js`, ID `G-MEYSB9GYF2`, site-wide via `ANALYTICS_HTML`.

## SEO layer

Every page carries a meta description, canonical URL, OG + Twitter Card tags, and RSS discovery. Article pages add `article:published_time`; briefs/reports/deep dives add schema.org `NewsArticle` JSON-LD; the homepage carries `WebSite` + `NewsMediaOrganization`. One source of truth: the SEO helpers in `generate_post.py`.

- **Share image:** `assets/og-default.png` (1200×630, terracotta wordmark card) — regenerate with the same chromium-screenshot approach as `social/render_card.py` if the brand changes.
- **`sitemap.xml` + `rss.xml`** are regenerated by `rebuild_homepage()` (`build_sitemap()` / `build_rss()`), so every publishing pipeline refreshes them. RSS = last 30 briefs. Crossword excluded (noindex, subscriber-only).
- **`robots.txt`** (allow all + sitemap pointer) and **`404.html`** (noindex, absolute paths so it works at any URL depth) are static, hand-maintained files.
- **Daily-brief `<title>`/description lead with the day's first real headline** via `extract_headline()` — which skips weather day-labels the same way `collect_existing_posts` does — instead of a bare date.
- **One `<h1>` per page:** the wordmark is an `<h1>` only on the homepage (`site_header_html(h1=True)`); elsewhere the page's own title is the h1.
- Already-published pages were retrofitted in place 2026-07-11; anything new gets meta from its renderer.
- Google Search Console is registered and the sitemap is fetching successfully. Google only — user isn't interested in other engines.

## Site structure

The homepage is a **zoned entry hall** (featured brief + cross-stream cards + Tools row [gated] + subscribe panel + last 7 briefs). The full daily archive is `/briefings.html`.

- **Daily Brief** (`/`, `/briefings.html`, `posts/`) — live
- **What to Watch** (`meeting-watch.html`) — agenda previews, 4 municipalities, auto-published (under the Local Government hub)
- **What They Decided** (`news-reports.html`) — post-meeting reports, AI-drafted, human-reviewed (under the Local Government hub)
- **Spotted** (`public-record.html`) — liquor licenses via two routes: agenda mining (Pima, Tucson, Oro Valley) + a state-DLLC database diff for Marana (added 2026-07-28 — Marana handles licenses administratively, so they never hit an agenda; see `public_record_liquor_dllc.py`)
- **Around Town** — development-watch items (Marana + Oro Valley pollers)
- **Ask** (`ask.html`) — RAG Q&A, live, linked site-wide
- **Responsiveness** (`responsiveness.html`) — stub only
- **The Tucson Mini** (`crossword/`) — unlisted subscriber perk
- **About** (`about.html`) — hand-edit only; leads with the reader promise, "How this is made" essay below it
- Planned: The Old Pueblo Speaks, Deep Read, Tracking → `ORIGINAL-JOURNALISM.md`

## Automation

**All AI calls use the Anthropic API via API key**, not a Pro/Max subscription — a deliberate decision from day one, and why the April 2026 subscription-OAuth crackdown didn't affect this pipeline.

**Running cost (re-baselined 2026-07-30 — the long-standing "~$3–4/month total" was stale):**

| line item | $/month | basis |
|---|---:|---|
| **ElevenLabs subscription** | **$24.00** | flat — the largest single cost by far, bigger than every API call combined. Possibly over-provisioned; not yet reviewed |
| Daily brief — Opus 5 synthesis | ~$10.57 | **measured**: `in=30043 out=8091 thinking=4879` (`/tmp/brief-gen.log`) |
| Model A/B challenger — Sol, daily via cron | ~$6.68 | measured, `brief-bake-off/ab.jsonl` |
| Everything else (~17 Sonnet 4.6 call sites + Haiku short) | **not measured** | agenda miners, Spotted, dev watch, FOIA spotter, newsletter, RAG, crossword. Prompts are far smaller than the brief's, but no instrumentation exists |

Two things drove the brief's cost up and are easy to miss:
- **Opus 5 thinks by default.** `call_claude()` passes no `thinking` param, and on Opus 5 omitting it runs *adaptive* thinking (unlike Opus 4.8/4.7, where omitting meant none). ~4,900 of 8,091 output tokens were thinking, billed at the $25/MTok output rate. `output_config.effort` is the dial if that needs reducing.
- **Tokenizers differ by ~61%.** The same brief prompt is 34,908 tokens to Claude and 21,647 to GPT-5.6 — a real cost multiplier independent of sticker price. Never compare vendors on $/MTok alone; compare measured cost per run.

Cost detail, the model bake-off, and the per-model comparison live in `PIPELINE.md`.

| Time (MST) | Job | Log |
|---|---|---|
| 6:00 AM daily | `generate_brief.py` via `run_brief.sh` — fetch sources → one synthesis call (`CLAUDE_MODEL = "claude-opus-5"`, `generate_brief.py:99`) → save to canonical path (retry loop, 5 attempts/60s) | `/tmp/brief-gen.log` |
| 6:10 AM daily | `run_podcast.sh` (6:10 → 7:30 → 6:30 on 2026-07-28, back to **6:10** on 2026-07-30 — see `PIPELINE.md`) — Telegram → blog post + push → daily YouTube Short → podcast script (Haiku) → ElevenLabs TTS → RSS/R2 → YouTube | `/tmp/podcast-gen.log` |
| 8:00 AM daily | `check_agendas.sh` — 4 agenda miners + **Sahuarita governing board** + Spotted + dev watch + auto-schedule live recordings (`ENABLE_AUTO_SCHEDULE=1`); **Mondays** also publish the weekly "Buried in the Agenda" Short. The "no new previews" branch is an `if/else`, **not** an early exit — it was `exit 0` until 2026-08-04, which skipped all eight independent stages below it (Spotted, DLLC, both dev watches, Monday short, geo editions, staleness check) on any day no council posted a new agenda, i.e. most days. When adding a stage, put it outside that branch | `/tmp/agenda-check.log` |
| 6:45 AM + 4:45 PM daily | `run_bluesky_post.sh` — Bluesky **+ Threads** ledger-diff posters, catch-all (also run at the end of `check_agendas.sh`); fully derived from `sitemap.xml` + each page's og: meta, no AI calls — see `SOCIAL-AUTOPOST.md` Part 3 (Threads: `social/threads_poster.py`, self-refreshing 60-day token) | `/tmp/bluesky-post.log` |
| 8:45 AM daily | `refresh_ask_index.sh` — rebuild RAG index + `fly deploy` (baked index would otherwise freeze answers) | `/tmp/ask-index-refresh.log` |
| 9:30 AM Mon | `run_foia_spotter.sh` — FOIA Lead Spotter: scan new published news reports for records-request leads, **web-search-verify each lead** (facts accurate? already public? — catches AI-paraphrased program names from our own reports), draft A.R.S. § 39-121 request emails to `records-requests/drafts/`, Telegram alert. **Nothing sends automatically** — human reviews and sends from `nicholas@daylayown.org` | `/tmp/foia-spotter.log` |
| Manual, Sat | `run_newsletter.sh` — **not cron'd.** Run by hand after the Tucson Mini is locked; the generator hard-stops if no puzzle exists for the send date. **Check `newsletter/PENDING-CORRECTIONS.md` before every send** — the generator has no manual-insert hook, so any queued correction has to be pasted into the draft by hand, after generating and before `upload_to_buttondown.py`. Delete the entry once it has actually sent. (None queued as of 2026-08-03 — the Oro Valley mayor correction ran in the 2026-08-02 send.) | `/tmp/newsletter-gen.log` |
| Scheduled | `at` jobs — live reporter for YouTube + Swagit meetings | `/tmp/live-reporter*.log` |

The blog post runs **before and independently of** the podcast, so a podcast failure never blocks the blog. Each distribution step is non-fatal. `atd` must be enabled (`systemctl enable --now atd`) for live recordings.

**Key dependencies:** `pdftotext` (poppler-utils) for Tucson PDFs; `at` + `atd`; `ANTHROPIC_API_KEY` in `~/.config/environment.d/anthropic.conf`; Telegram credentials. **Delivery of the brief itself** happens only through `run_podcast.sh` → `send_telegram.py` (OpenClaw's own cron delivery was disabled to stop duplicate sends). Other pipelines — `ai_reporter.py`, `foia_lead_spotter.py`, `check_agendas.sh`, `schedule_recording.py`, `poll_tucson_water.py`, and the brief's provenance gate — send their own *alerts* through the same `send_telegram.py` script. That's fine; the rule is about not sending the brief twice, not about restricting the channel.

Failure modes, billing detail, and the editor's-desk hook → `PIPELINE.md`.

## Deployment

- **Live:** `https://tucsondailybrief.com` (CNAME), GitHub Pages from `master` root, repo `daylayown/tucson-daily-brief-site`, `.nojekyll` for static serving.
- **Deploy runs via a custom GitHub Actions workflow** (`.github/workflows/deploy.yml`), **not** GitHub's managed "Deploy from a branch" flow. Switched 2026-07-04 because the managed flow has no retry and the Pages backend intermittently failed the hand-off (twice in three days — the build succeeded both times). The workflow uploads the repo root and deploys with **3 attempts + backoff** (60s, 120s), so transient hiccups self-heal. Triggers on push to `master` + manual `workflow_dispatch`.
- Revert if ever needed: `gh api repos/daylayown/tucson-daily-brief-site/pages -X PUT -f build_type=legacy`

## What's next

**⏰ Dated task — review the provenance-gate shadow log, first week of August 2026.** The gate went live 2026-07-28 in shadow mode and starts logging 2026-07-29, so by Aug 1–7 there are ~3–9 days of data. Read it and decide whether to enforce:

```bash
cat brief-inputs/shadow.jsonl | python3 -m json.tool --json-lines   # or: jq . brief-inputs/shadow.jsonl
```

For each entry, check the flagged name against `brief-inputs/<date>.sources.txt` (the exact text the model was given) and judge: **real miss** (the name genuinely wasn't in the sources) or **false positive** (a place/org the person-shaped filter should have excluded). That splits three ways:

- **Mostly real misses** → flip `Mode.SHADOW` → `Mode.ENFORCE` in `generate_brief.py`. PARTIAL findings then auto-trim ("David Barrett" → "Barrett"); UNGROUNDED still only alerts.
- **Mostly false positives** → tune `_GENERIC_TAIL` / `_PLACE_PREFIX` / `person_shaped()` in `provenance_gate.py` first, then re-review. Do not enforce on a noisy filter.
- **Empty log** → good news, and worth saying out loud: it's evidence the 7/23 fabrication was genuinely rare rather than merely unnoticed. Leave it in shadow as cheap insurance; revisit only if another one surfaces.

Also queued off the same work: inject `pipeline/local_names.json` into `SYNTHESIS_PROMPT` (lowers the error rate; is **not** a control — see `PIPELINE.md`), and decide whether the newsletter needs its own name check. Note the gate cannot catch an error the newsletter *inherited* from a brief — it reads as grounded there — so the brief gate is the real protection for both.

While reviewing, note the publication time went 6:10 → 7:30 → 6:30 on 2026-07-28 and then **back to 6:10 on 2026-07-30**, at the user's call: the shadow gate had produced only false positives ("Arizona Constitution", "Tucson Electric Power" — place/org names the person-shaped filter should exclude), and the split 6:05 A/B send vs 6:30 publication was confusing in Telegram. The provenance-review window is now ~8 minutes, i.e. effectively none — the gate is an after-the-fact alert.

**The next big project is short-form video** (`SHORT-FORM-VIDEO.md` + `MARKETING.md`) — specifically the distribution loop: two social packages per week for eight consecutive weeks, one moat package + one reach package, each converting toward the newsletter. The moat package is now specced: the **geographic editions** ("What to Watch: Marana / Oro Valley"), a personalization-by-portfolio experiment timed to Meta's video-first feed pivot — framework locked 2026-07-28 in `SHORT-FORM-VIDEO.md`. Everything else in `ROADMAP.md` is captured and gated behind it. Don't start those mid-stream without a user go-ahead.
