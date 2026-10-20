# Portland Daily Brief

An independently written Python application for Portland and Multnomah County written news. [Read the daily brief archive](briefings.md). The reference repository remains in Git history because Andy requested a fork; no Tucson implementation, prompts, branding, articles or assets are used by the Portland application.

Returning to the project? Read [the session handoff](docs/handoff.md) and [AGENTS.md](AGENTS.md) for current state, unresolved issues and restart commands. The handoff was checked October 20, 2026; older dated coverage figures are historical snapshots.

## Current operation

Daily brief: live feeds → recent local candidates → event deduplication → local text model → evidence validation → private editorial versions → dated Markdown and escaped HTML. Andy authorized daily generation and sharing review drafts in this repository on October 10, 2026. The Mac runner targets **7:00 a.m. America/Los_Angeles**, catches up after wake/login, and commits/pushes summary-only snapshots. Review drafts are not editor-approved reporting. Read [implementation status](docs/status.md) for actual coverage and limits.

The default model runs locally on Apple silicon using MLX and Qwen3-4B-Instruct-2507, pinned to a specific model revision. No text-model API, account, API key or paid service is needed. The first model download uses about 2.3 GB of storage. The Python environment adds roughly 350 MB. Allow at least 8 GB available memory; the default model is specific to Apple silicon macOS. Private data, source evidence, model inputs, downloaded models and caches are ignored by Git; only original summary Markdown/HTML enter the repository archive.

## Install on this Mac

```sh
cd /Users/andys/portlandDailyBrief
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python requirements.lock.txt
uv pip install --python .venv/bin/python --no-deps -e .
```

`uv` is already installed on this machine. An alternative with an installed Python 3.12 is:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
```

The optional local-model dependencies require Apple silicon. The core collection, records, rendering and tests can run without MLX, but text generation then needs a separately implemented provider. There is no automatic paid fallback.

## Generate and read a daily draft

```sh
.venv/bin/portland-brief validate-sources --refresh
.venv/bin/portland-brief run
.venv/bin/portland-brief status
.venv/bin/python -m unittest discover -v
```

`run` is the one-command workflow after installation. Its first use downloads the configured model if it is absent. Model downloads stay under `.models/` unless `HF_HOME` is explicitly set. Sources have timeouts, two bounded attempts, conditional requests and a 15-minute cache. `run --refresh` revalidates the feeds. A partial run visibly identifies missing coverage. A total source/model failure exits nonzero, records the failure, and preserves the last successful draft.

Open `output/drafts/index.html` as a file in your browser. Dated drafts are under `output/drafts/archive/YYYY-MM-DD/brief.md` and `brief.html`. Dates and displayed times use **America/Los_Angeles**, including DST. Selection uses an elapsed rolling 24-hour publication window. Undated, future, old, insufficient and suspicious items are skipped; eight stories is the default maximum, never a quota.

Each archive date is an atomic pointer to an immutable version. Rerunning a date replaces that pointer, creates one new version, and keeps a single archive entry. It never duplicates the edition. A rerun of an older date cannot replace the newest index. Source URLs come from validated source metadata; model-supplied URLs are rejected.

## Sources and settings

Edit `config/sources.json` to enable/disable or add a source with a unique ID, URL, type, jurisdiction, allowed article hosts, cadence and access notes. Run `validate-sources --refresh` afterwards; it records validation dates and health. A response with HTTP 200 is not itself reuse permission. Andy reports separate permissions for Nexstar/KOIN, KGW, KPTV and Advance/OregonLive; all four are enabled and live-tested with source-specific normal browser User-Agents. The registry retains the earlier terms assessments alongside these permission exceptions. OregonLive's publisher-hosted RSS is verified and enabled under Andy's reported Advance permission. The prior [Advance Local User Agreement](https://www.advancelocal.com/advancelocalUserAgreement/user-agreement.html) assessment remains in access history alongside this permission exception. Source reuse constraints remain recorded, including OPB and Mercury restrictions. The user-authorized repository archive shares original attributed summaries and direct links; fetched descriptions and article material stay private.

Only short RSS descriptions are used. Full-content fields and media enclosures are removed before caching. The county adapter discovers dated listing headlines but does not pretend a headline alone is enough to support a two-sentence summary. Fetched data is not allowed to change prompts, credentials, tools, configuration or editorial state. Source evidence, timestamps, content hashes, model revision and token usage are saved privately under `data/runs/` and SQLite.

Edit `config/settings.json` for the timezone, jurisdiction, output/data/cache paths, story limit, window and model. The provider currently implemented is `mlx`; changing provider names does not create an API integration. Change `model` and `model_revision` together, verify the model license/hardware needs, and rerun the tests and an evidence review. Maximum model input/output tokens per attempt and total run tokens are enforced. Model errors have bounded retries; invalid outputs are omitted. `.env.example` documents optional environment variables; the application does not load `.env` implicitly.

The latest October 10 validation pass parsed eleven enabled sources, including the authorized KGW, KPTV and OregonLive feeds. Counts describe returned records, not eligible stories or complete coverage; see [validation results](docs/status.md). Department feeds were discovered from their official published RSS links; sparse land-use notices are still omitted from prose. City department feeds share a publisher balance group. Validation dates use Portland local time and distinguish the most recent successful check from the original body retrieval timestamp.

## Development and zoning priority

Supported Portland and Multnomah County zoning, land-use, redevelopment, housing projects and planning-commission stories receive a configurable ranking boost in `daily_brief.editorial_priorities` in `config/tasks.json`. Relevance, publication windows, source balance, evidence validation and the maximum story count still apply. Explicitly different land-use case IDs and different procedural stages are preserved when checking similar story titles. Priority does not make an application an approval or a permit proof of construction.

City land-use notices now follow pagination: the October 10 run reconciled all 41 listed entries, including 35 case notices and six information documents, and retrieved 36 linked PDFs. One appeal PDF has no extractable text and remains flagged for visual review. This is the current published index, not historical case completeness. County development/notice adapters remain pending. See [AGENTS.md](AGENTS.md) for the current goal, state and next steps.

For a development you see while driving, use the city's [construction/demolition permit map](https://www.portlandmaps.com/bds/construction_and_demolition_map), or search an address/intersection in [PortlandMaps](https://www.portlandmaps.com/) and open **Permits & Zoning**. [Advanced permit search](https://www.portlandmaps.com/advanced/index.cfm?action=permits) supports status/location/date filters. [Project lookup instructions](docs/development-lookup.md) explain the official listings, missing-document limits and the new local command:

```sh
.venv/bin/portland-civic lookup-project --address '6866 SE 87TH AVE' --refresh
```

This returns bounded official permit records and evidence links, saves private source versions, and uses no model. It preserves each permit's identity and exact status. The initial commercial new-construction monitor described below now runs in the existing daily filings job; broader permit coverage and address-based watchlists remain pending.

The first development reporting focus is **four or more stories with residences and commercial space in the same project**. `filings.development_focus` in `config/tasks.json` controls the height threshold, creation-date lookback and bounded pagination. Official permit descriptions supply the use/height evidence; zoning capacity and housing support offices do not establish a qualifying business component. Conflicting heights or official addresses remain visible as specific open questions. Run the same collection manually:

```sh
.venv/bin/portland-civic developments --refresh
```

Open `output/development-review/index.html` directly in a browser for the **development map**. Click a numbered pin to explore a readable project presentation: what's proposed, homes and business space, the city's exact permit status, location, timeline, linked sources and any open questions. Search by project, neighborhood or address. Street tiles come from the official City map; locally embedded neighborhood boundaries and pins remain usable when tiles are unavailable. No map key or web deployment is needed.

The first scan reconciled 141 records across two pages within the chosen one-year Commercial Construction GIS scope. Three candidates are presented, rather than a complete city/county development map. Explicit related trade permits retain their own identities/statuses. The city status **Under Review** refers to its permit process, not our approval queue.

Development map browsing and daily **draft** coverage no longer require human approval. `filings.draft_review_policy` is `automatic_source_checked`: validate source evidence, disclose disagreements, respect explicit editor holds and include only fresh material updates in the daily brief. Unchanged projects remain on the map without becoming daily filler. Optional human approval is still distinct; the separate approved export excludes unreviewed development blocks. [Development reporting commands and limits](docs/development-reporting.md) explain how this works.

## Review, edit and approve locally

```sh
.venv/bin/portland-brief review 2026-10-09
.venv/bin/portland-brief export-edits 2026-10-09 --out data/edits-oct9.json
```

Open the private review HTML beside the source links. Edit headlines and the `sentences` array in the exported JSON. Keep `base_run_id`, `citation_ids` and exact supporting `evidence_spans` intact or replace spans with exact evidence text. Remove a story row to reject that story. The validator checks evidence references and obvious errors; it cannot prove that a sentence's meaning is supported. Verify consequential facts, names, proposals, dates and attribution yourself.

```sh
.venv/bin/portland-brief apply-edits 2026-10-09 --file data/edits-oct9.json --editor 'Andy Seubert' --note 'Compared claims against supporting sources'
.venv/bin/portland-brief approve 2026-10-09 --editor 'Andy Seubert' --note 'Reviewed current evidence and final text'
.venv/bin/portland-brief build-approved --out output/approved-local-export-1
```

These are instructions for an explicit future editor action. The delivered first brief has **not** been approved on Andy's behalf. A local export includes only currently approved summaries/citations and excludes private snippets/model inputs. It retains not-published labels. It is not a deployment. Edit or regenerate an approved edition and approval is invalidated. `reject DATE --editor ... --note ...` rejects a needs-review edition; `reopen DATE ...` reopens an approved edition.

The daily repository draft archive is configured; source-checked development summaries can join those drafts without human approval. Private project/evidence pages stay local. Meeting reports and the separate approved export retain editorial gates. No public website deployment, domain, newsletter or social account is configured. The fork's inherited GitHub Actions remain disabled. Local review files must never be used as a public hosting directory.

## Failures, backups and recovery

If generation fails, read the newest `data/runs/*/run.json`. Source outages are listed in `data/source-health.json` after validation. For MLX failures check the environment, free memory, internet availability during initial model download and the configured revision. A broken source is disabled by changing its `enabled` field; no secret is needed. Old items are excluded even when a feed endpoint is healthy. A stale fallback is recorded but excluded from a new brief.

```sh
.venv/bin/portland-brief backup --out data/backups/editorial-2026-10-09.sqlite3
```

This makes a transaction-consistent SQLite snapshot without overwriting an existing backup. Also back up `config/`, private `data/runs/`, any civic/media data and `output/drafts/versions/`; the database alone does not include media or all filesystem artifacts. To test a recovery without replacing original data:

```sh
mkdir -p /tmp/portland-brief-recovery
cp data/backups/editorial-2026-10-09.sqlite3 /tmp/portland-brief-recovery/editorial.sqlite3
.venv/bin/python -c "import sqlite3; db=sqlite3.connect('/tmp/portland-brief-recovery/editorial.sqlite3'); print(db.execute('PRAGMA integrity_check').fetchone()); print(db.execute('SELECT date,state FROM editions').fetchall())"
```

Restore to a new private workspace, then copy configuration and immutable filesystem versions there. Inspect the restored records before replacing a working installation. To restore a prior local draft, use its retained version as evidence for a new editorial version; do not silently replace an already reviewed edition. No API secrets exist in the default setup. If an optional future provider adds a secret, rotate it at that provider and replace the environment value, never a committed file.

## Scheduling and production boundary

The installed user LaunchAgent checks durable due jobs every minute. Daily synthesis targets **07:00 America/Los_Angeles**, with DST handling. News collection and meeting discovery run hourly; filing collection runs daily. Each pipeline retries independently. Repository sync checks every five minutes and retries an interrupted push without generating a duplicate commit. The Mac must be logged in, online and awake to run; after sleep it catches up once for the current date. It does not manufacture missing historical editions or force the Mac to wake/power on.

The archive follows the reference's storage method: `posts/YYYY-MM-DD.html`, `briefings.html` for all editions, and `index.html` for the latest. `posts/YYYY-MM-DD.md` and [briefings.md](briefings.md) add GitHub-readable copies. Reruns replace the date's current files; Git commits retain earlier versions and corrections. Private immutable versions and evidence stay under ignored `output/` and `data/`.

```sh
.venv/bin/python -m portland_brief.launch_agent status
.venv/bin/portland-schedule tick
.venv/bin/python -m portland_brief.repository_archive --push
```

To pause all background collection, set `schedule.enabled` to `false` in `config/tasks.json`; the runner checks it before every tick. Set `repository_archive.enabled` to `false` to stop repository sharing while retaining local collection. Removing the job preserves all records:

```sh
.venv/bin/python -m portland_brief.launch_agent remove
# Reinstall and resume:
.venv/bin/python -m portland_brief.launch_agent install
```

Logs and retry state are private in `data/scheduler/`. Git authentication uses this Mac's existing credential helper; no token is put into the plist, files or logs. The archive job stages only dated summary files and archive indexes, holds when unrelated tracked/staged changes exist, and never force-pushes. Commit/push other code edits manually before resuming archive sync. A failed generation leaves earlier files intact; a failed push retains the local commit for retry. The runner remains independent of GitHub Actions and Pages.

## Measured usage and sample

The source-checked [first brief](posts/2026-10-09.md) is committed as a review draft. Its private review page is `output/private-review/2026-10-09.html`. A generation before editorial correction used 5,623 input tokens and 1,731 output tokens across 12 bounded model attempts, taking 61.29 seconds on this Mac. Editing used no additional model calls. Seven source-checked stories remain; no story was added to fill a quota.

The local model has **$0 per-token API charges**. Thirty runs at that sample workload are about 168,690 input and 51,930 output tokens; retries in the sample are already included. The configured 30,000-token/run ceiling is at most 900,000 tokens for 30 runs. Electricity, storage and human review are additional unpriced local resources. No hosted-model, runner or hosting price is quoted or purchased. Model documentation: [MLX LM](https://github.com/ml-explore/mlx-lm) and [the configured model](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit). Interview costs are not Portland quotes.

## Civic and neighborhood increment

The original proof of concept now has independent collection/checkpoints and an evidence-first civic increment. Read [civic operating commands](docs/civic-operations.md), [actual coverage and release gates](docs/status.md), and [reference inventory](docs/reference-inventory.md).

```sh
portland-brief collect-news
portland-civic discover --max-pages 4
portland-civic ingest-agenda --url https://www.portland.gov/council/agenda/2026/10/7
portland-civic reconcile-votes
portland-civic report meeting:0a73fcd7b4c2d084ebe0fc0d
portland-civic filings --max-notices 5 --boundaries
portland-civic status
portland-schedule tick
python -m unittest -v
```

The persistent Mac job runs independent collectors and shares daily review drafts; civic publication remains gated. Meeting input capture/transcript import is separate from the text-only daily brief and has no audio output/distribution feature. Real recording coverage, automatic transcription, historical procedure reconciliation and civic publication/search are unfinished. Private evidence, source snapshots, model files and civic drafts remain excluded from Git.
