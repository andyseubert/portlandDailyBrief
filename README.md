# Portland Daily Brief

An independently written Python application for private Portland and Multnomah County news drafts. The reference repository remains in Git history because Andy requested a fork; no Tucson implementation, prompts, branding, articles or assets are used by the Portland application.

## Current operation

Daily brief: live feeds → recent local candidates → event deduplication → local text model → evidence validation → private editorial versions → dated Markdown and escaped HTML. Every output remains **LOCAL DRAFT — NOT PUBLISHED**. Read [implementation status](docs/status.md) for actual coverage and limits.

The default model runs locally on Apple silicon using MLX and Qwen3-4B-Instruct-2507, pinned to a specific model revision. No text-model API, account, API key or paid service is needed. The first model download uses about 2.3 GB of storage. The Python environment adds roughly 350 MB. Allow at least 8 GB available memory; the default model is specific to Apple silicon macOS. The data, drafts, downloaded model and caches are ignored by Git.

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

Edit `config/sources.json` to enable/disable or add a source with a unique ID, URL, type, jurisdiction, allowed article hosts, cadence and access notes. Run `validate-sources --refresh` afterwards; it records validation dates and health. A response with HTTP 200 is not itself reuse permission. KGW and KPTV are disabled due to terms restrictions; OregonLive is unvalidated. OPB and Mercury terms limit reuse: all content here stays personal/local. KOIN's feed is accessible but its terms/full-text endpoints were inaccessible; no bypass is attempted. Review these constraints before any public release.

Only short RSS descriptions are used. Full-content fields and media enclosures are removed before caching. The county adapter discovers dated listing headlines but does not pretend a headline alone is enough to support a two-sentence summary. Fetched data is not allowed to change prompts, credentials, tools, configuration or editorial state. Source evidence, timestamps, content hashes, model revision and token usage are saved privately under `data/runs/` and SQLite.

Edit `config/settings.json` for the timezone, jurisdiction, output/data/cache paths, story limit, window and model. The provider currently implemented is `mlx`; changing provider names does not create an API integration. Change `model` and `model_revision` together, verify the model license/hardware needs, and rerun the tests and an evidence review. Maximum model input/output tokens per attempt and total run tokens are enforced. Model errors have bounded retries; invalid outputs are omitted. `.env.example` documents optional environment variables; the application does not load `.env` implicitly.

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

No public site, domain, newsletter, social account or publishing workflow is configured. The fork's inherited GitHub Actions were disabled. Local review files must never be used as a public hosting directory. Public publication requires a later implementation and decision about destination and source constraints.

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

The initial proof of concept is manual; no launchd/cron job was installed during Plan 1. The later requested independent scheduling/civic work is tracked in `docs/status.md`. Future daily timing is 07:00 **America/Los_Angeles**, not a fixed UTC offset. Turning a JSON boolean on does not install a job. To disable manual operation, stop invoking `run`; no background process or public publishing runs in this initial increment.

## Measured usage and sample

The source-checked first brief is locally saved at `output/drafts/archive/2026-10-09/brief.md` and `brief.html`. Its private review page is `output/private-review/2026-10-09.html`. It is not committed to the public fork. A generation before editorial correction used 5,623 input tokens and 1,731 output tokens across 12 bounded model attempts, taking 61.29 seconds on this Mac. Editing used no additional model calls. Seven source-checked stories remain; no story was added to fill a quota.

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

Scheduling code is tested, but no persistent job is installed or running. `portland-schedule serve` explicitly starts a foreground scheduler; Ctrl-C pauses it. It makes private drafts only. Meeting input capture/transcript import is separate from the text-only daily brief and has no audio output/distribution feature. Real recording coverage, automatic transcription, historical procedure reconciliation and civic publication/search are unfinished. Current live samples and the full measured coverage report are local; private data, source snapshots, model files and drafts are excluded from Git.
