# Session handoff — October 28, 2026

## Current resume point

Andy resumed on October 28 with “pick up where we left off.” Read this section and `AGENTS.md` before the historical October 20 handoff below. The completed increment fixes shared-cache races and meeting failure reporting; development histories, scanned attachment interpretation and county ingestion remain later gates. No daily edition was regenerated, model invoked, human approval recorded, publication permission changed or website deployed.

At inspection, `main` was clean and matched `origin/main`; daily archive commit `2b13b47` contains October 28. Twenty dated editions exist, October 9–28 inclusive. The LaunchAgent remains loaded at 60-second intervals, and persisted daily synthesis next targets October 29 at 7 a.m. LA. No scheduler settings were changed.

### Completed reliability work and validation

- HTTP fetching serializes each URL across processes, uses unique sibling temporary files and verifies the cached body's hash against metadata before reuse. A mismatched pair is fetched unconditionally; a failed write or outage preserves the prior verified fallback's metadata/freshness. A 304 retains original source bytes/fetch time. New fetch diagnostics include the underlying error rather than only “request or parse failed.” The historical October 20 shared temporary path is a real race hazard, but its specific failure cause and the old generic fetch failures cannot be proven retroactively.
- Meeting `discover` returns nonzero for actual fetch/parse/identity errors or a terminal inventory count mismatch. Intentional page budgets retain explicit continuation/partial coverage and exit zero when otherwise healthy. Roster failures propagate too. Scheduler retries now receive these failure codes; an executing scheduler tick also exits nonzero when a child fails, without cancelling independent jobs.
- Identifier checks run before matter writes. A conflicted document row is held with exact document number, URL, source version and passage while other rows/pages continue. Index-row counts and normalized/held counts are separate. Inventory traversal may reconcile while normalization remains partial and the command exits nonzero. No conflicting identities were merged or guessed.
- Full validation: `.venv/bin/python -m unittest discover -s tests -q` passed **139 tests**. Sixteen added cases cover four simultaneous cache-writer processes, interrupted body/metadata replacements, stale/304 behavior and unchanged freshness, actual meeting CLI-to-scheduler retry propagation, roster/parse/count errors, deliberate page limits and held identities without writes. No UI changes or new browser checks; the October 10 browser validation remains historical.

### Latest live verification

Private structured result: `output/meeting-health-2026-10-28-live.json`. The initial sandboxed attempt failed DNS resolution for official hosts and remains honestly recorded in civic retrievals; the approved outside-sandbox rerun fetched all configured sources. Do not mistake that test-environment DNS failure for a continuing LaunchAgent outage.

| Source | Live returned scope | Remaining limit |
| --- | --- | --- |
| Clerk calendar | 36/36 listed events, two pages | Discovery, not processed meetings |
| Current institutions | Five; source unchanged | Current observed membership, not historical terms |
| Active documents | 42/42 index rows, two pages; 41 normalized, one held | Identity reconciliation below; no claim all detailed documents processed |
| Completed documents | 100/4,136, four pages | Continuation starts at `?page=4` |
| Voting register | 96/16,074, four pages | Continuation starts at `?page=4` |
| County archive | 1,551 entries, listing reached end | No independent expected total or reviewed outcomes |

The all-source command correctly exits **1** because of the held identity, even though all HTTP requests succeeded. [The current City listing](https://www.portland.gov/council/documents) and [Committee Chair updates detail](https://www.portland.gov/council/documents/presentation/committee-chair-updates) identify that URL as **2026-388**. Stored prior official evidence links the same URL to **2026-373**, whose placed-file URL is also retained. Distinct matter IDs are `matter:4e7568bbc8091037d0844674` and `matter:1da26ed23148d8c3206b67d7`. The live held passage is `passage:03959fe4fb1630e4b57974cb`, source version `source:c73b19202e882d6dd96bcd7a`. Reconcile URL identity/version history from explicit official evidence; do not treat a recurring presentation title as one continuing legislative matter.

Current civic storage: 1,615 meeting identities (1,613 discovered, one partial, one processed); no capture jobs, transcript segments or human approval audits. Latest existing filings run is October 27: commercial layer 143/143 over two pages within the creation-date window starting October 27, 2025; 149 retained identities across runs. Current notices reconciled 43/43 over three pages (37 case notices, six information documents), 38 PDF attachments, 477 pages, 16 pages without text. Attachment processing remains incomplete. Three development reports remain on the map. These figures were inspected from scheduled records, not a new filings refresh in this session.

### Next work and exact commands

1. Reconcile the reused meeting URL from explicit official document histories while preserving both matter identities. The scanner now continues valid rows but keeps the conflict visible and retryable.
2. Audit aggregate filings exit status: its prior scheduler success code can coexist with incomplete notice attachment processing. This increment fixes meeting/scheduler propagation, not every civic subcommand.
3. Investigate the October 20 apparently overlapping news items from retained evidence before changing selection or a dated archive. No retrospective correction was made here.
4. Resume scanned-page interpretation and durable development histories, then address watchlists and the separate county adapter as in `AGENTS.md`.

```sh
git status --short --branch
git log -5 --oneline
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m portland_brief.launch_agent status
.venv/bin/portland-civic discover --source portland-documents-active --max-pages 4 --refresh
```

The active-document command can legitimately return 1 while the held conflict remains. Inspect its per-source `errors`, `normalized_records` and `held_records`, not just the exit code. Keep private databases, cache, source bytes and the live-check JSON ignored. Commit/push tested implementation/docs normally; no force pushes. Existing map, development drafts, source permissions and editorial/publication boundaries remain in force.

## Historical October 20 handoff

Checked around 9:23 a.m. America/Los_Angeles. This is a dated snapshot; inspect current records when returning. The session ended after Andy asked us to document continuity. This handoff changes documentation only; it does not start a new implementation stage, publish a website or change scheduling.

## Read first

Start with [AGENTS.md](../AGENTS.md) for the goal, standing authorization, review policy and boundaries. Then use this handoff for the immediate restart. The project is already installed at `/Users/andys/portlandDailyBrief`, on `main`, with its `.venv`. Do not reinstall, refork, recreate the repository or ask again for existing source permissions.

Repository: https://github.com/andyseubert/portlandDailyBrief. At the start of this handoff, the worktree was clean and `main` matched `origin/main`. Latest daily archive commit: `9114e14`, October 20. Last implementation milestone: `be07232`, development map and automatic source-checked drafts. The handoff itself is committed separately afterward.

## What Andy wants

A written Portland/Multnomah County news service with daily briefs, systematic meeting coverage and neighborhood development reporting. Development/zoning is a priority, especially buildings with **at least four stories and both residences and commercial space**. The index should be a map with clickable development pins and readable project presentations. Andy should not need to read JSON or approve every draft to browse it.

Development map/draft coverage now runs automatic source checks and displays specific uncertainties. An automatic `hold`/`needs_review` in stored records is not an assignment to an external reviewer and does not by itself prevent draft coverage. An actual editor's explicit hold still excludes a project from daily drafts. The city's **Under Review** is its permit process. No human approvals have been recorded. Meeting reports and the separate approved export retain editorial gates; never approve reports as Andy.

Local scheduling and summary-only GitHub draft sharing are authorized. Keep public website deployment, automatic public civic publication, paid services, outreach, podcasts, voice cloning, social publishing, newsletters and crosswords disabled. Meeting media may be evidence for written reporting, but live capture/automatic transcription are not installed or validated. Source permission from Nexstar/KOIN, KGW, KPTV and Advance/OregonLive is already recorded; normal browser User-Agents are authorized by Andy's attestation. No paywall bypasses or copied full articles.

## What is working and where to read it

| Item | Location and actual state |
| --- | --- |
| Latest brief | `posts/2026-10-20.md` and `.html`; twelve editions from October 9 through October 20 |
| Dated archive | `briefings.md`, `briefings.html`, `posts/`; prior revisions stay in Git history |
| Latest-page export | Repository-root `index.html` is the daily brief, not the private development map |
| Development map | `output/development-review/index.html`; open directly in a browser; three candidates |
| Project presentations | `output/development-review/5273121/review.html` (Gibbs), `5243125/review.html` (Division), `5228969/review.html` (Marshall); readable `article.md` beside each |
| Development-to-daily integration | `posts/2026-10-11.md` includes all three automatically checked drafts with uncertainty disclosure; this release gate has been exercised |
| Private evidence | `data/civic.sqlite3`, `data/source-versions/`, development evidence tables, private output; ignored by Git |
| News history/evidence | `data/editorial.sqlite3`, `data/news-collection.sqlite3`, `data/runs/`, `output/drafts/`, `output/private-review/` |
| Operations | `data/scheduler/jobs.sqlite3`, per-job logs in `data/scheduler/`, LaunchAgent plist in `~/Library/LaunchAgents/` |

The map uses verified City permit points, official street tiles and embedded neighborhood boundaries, with locally vendored Leaflet. It works under file URLs; pins and boundaries remain usable without street tiles. No map API key or public host is required. Project points are not verified building footprints. County coverage is not implied.

## Current runtime and source coverage

The loaded user LaunchAgent is `com.andyseubert.portland-daily-brief`, running a scheduler tick every 60 seconds. It was idle between ticks with last exit code zero at inspection. Daily synthesis targets **7:00 a.m. America/Los_Angeles**; the persisted next occurrence was October 21 at 7 a.m. News and meeting collection are hourly, filings daily, and archive sync every five minutes. `config/tasks.json` is the effective schedule configuration; its settings override the legacy scheduling field in `config/settings.json`.

Closing the chat does not uninstall this independent runner. The Mac must remain available in the user's session; wake/login catch-up exists, but it is not a cloud service or a guarantee of collection while asleep/offline. No scheduler settings were changed for this handoff. Jobs were pending at inspection with no retry counters active, but this does **not** establish source health.

Latest successful filings scan, October 19 around 4:15–4:19 p.m. PDT:

- Commercial Construction: **143/143 records**, two pages, creation-date window starting October 19, 2025. Two matches, one height conflict, 62 needing evidence, 78 excluded. SQLite retains 146 unique records across runs; that is different from the latest scan count.
- Current City land-use notices: **35/35 entries**, two pages; 29 case notices and six information documents. Thirty PDFs, 369 pages, 21 pages without extracted text. `attachment_processing_complete` is false even though the current index reconciled.
- Three mapped project reports remain; no production review audits or approvals exist. A changing listing/window count does not establish deletion or withdrawal.

Latest meeting inventories that actually returned records, October 20 around 7:22–7:23 a.m. PDT:

| Source | Returned scope | Limit |
| --- | --- | --- |
| Clerk calendar | 43/43 listed events, three pages | Discovery, not processed meeting coverage |
| Active documents | 47/47, two pages | Index entries, not complete document interpretation |
| Completed documents | 100/4,119, four pages | Partial; continuation starts at `?page=4` |
| Voting register | 96/15,990, four pages | Partial; continuation starts at `?page=4` |
| County archive listing | 1,550 entries, one page; parser reached end | No independent expected total; listing discovery, not reviewed outcomes |

Stored meeting records total 1,605: 1,603 discovered, one partial, one processed. Calendar and agenda identities remain separate until explicitly linked, so these counts are not a deduplicated complete inventory. Four phase reports need review; no capture jobs or transcript segments exist. Historical memberships, exceptional procedures, county outcomes/rules, full meeting reconciliation and county development ingestion remain incomplete.

## First checks on return: reliability issues

1. **Recent meeting fetch failures are not reflected by the scheduler's zero exit code.** After the successful morning scans, the latest stored attempts around 8:27–8:44 a.m. PDT returned zero pages and `Live request failed; stale cache is not a successful scan` for all five meeting inventories. The scheduler log still records exit code zero. Inspect underlying fetch errors and CLI exit-status propagation before claiming recovery. Retained successful evidence must stay intact. These are failures, not cancellations or withdrawals.
2. **Today's brief discloses a county-source cache write failure.** The `.cache/http/…tmp` to `.bin` rename raised `No such file or directory`. `Fetcher._save` in `portland_brief/fetching.py` uses a predictable temporary filename, shared across processes and between body/metadata for a URL. Concurrent writers are a plausible cause, not a reproduced diagnosis. Investigate unique temporary files/coordination and add a meaningful concurrent-writer regression test when fixing it. Do not delete source history or label cached material current to silence the error.
3. **Check event deduplication.** Today's brief contains two apparently overlapping reports about the same downtown stabbing/police-shooting incident. This is a triage observation from the generated brief, not a fresh source-verified correction. Inspect supporting evidence before merging or changing the dated edition; preserve corrections/version history.

Useful evidence: `posts/2026-10-20.md`, `data/scheduler/meetings-discovery-20261020T152755.log`, `data/scheduler/news-synthesis-20261020T140000.log`, latest `retrievals`/`inventory_scans`/`pipeline_runs` in civic SQLite, and scheduler `jobs`/`attempts`. Old errors in the same database are historical; sort by time and distinguish latest failure from latest successful collection. Full `portland-civic status` prints the entire growing inventory history, so redirect it to a private file and summarize rather than dumping it into the conversation.

No root-cause fix, new collection run, model generation, approval or test run was performed during this documentation handoff.

## Development questions to keep visible

- **Gibbs**, folder `5273121`, application `2026-050115-000-00-CO`: description says four stories, 35 units and commercial ground floor. Numeric zero is missing height data. The scoped intake/building-report queries had no matching row; this is not denial or withdrawal. An earlier searched proposal download returned 404; its contents were not retrieved.
- **Division**, folder `5243125`, application `2026-021265-000-00-CO`: six stories, 127 units and retail in the description. GIS uses `2440 SE Cesar E Chavez Blvd`; intake uses `3921 SE Division St`. Official property evidence connects tax lot R184111, but address change remains **Pending**. Preserve both addresses and unresolved discrepancy.
- **Marshall**, folder `5228969`, application `2026-007680-000-00-CO`: description says four stories, 50 units and commercial shell; separate numeric height says three. Do not settle this from an occupied roof. Address changes are not evidence of construction. Related mechanical permit `26-007681-MT` being Expired does not establish cancellation of the building proposal.
- Explicit related permit numbers do not prove parent relationships. Homestead, Richmond and Northwest District tags come from verified permit points; district boundaries are separate, and two invalid geometries remain flagged.

After reliability checks: interpret plans/decisions and scanned pages with page evidence; expand durable histories using explicit land-use/permit/property identifiers; add address-based watchlists and verified broader map coverage; then build a separate county development/zoning adapter. Continue meeting coverage in tested stages. No full reference parity or comprehensive city/county coverage has been achieved.

## Commands for the next session

Inspect first; these do not request new source collection or inference:

```sh
cd /Users/andys/portlandDailyBrief
git status --short --branch
git log -5 --oneline
cat AGENTS.md
cat docs/handoff.md
.venv/bin/python -m portland_brief.launch_agent status
open output/development-review/index.html
open posts/2026-10-20.html
```

Use current dates on return. Refresh/verification commands below perform work; run the relevant ones after inspecting the active jobs and evidence:

```sh
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/portland-civic discover --source portland-calendar --max-pages 4 --refresh
.venv/bin/portland-civic developments --refresh
.venv/bin/portland-civic notices --refresh
.venv/bin/portland-civic development-evidence --refresh
.venv/bin/portland-civic development-reports
.venv/bin/portland-civic lookup-project --address 'ACTUAL STREET ADDRESS' --refresh
```

`.venv/bin/portland-brief run` generates/replaces today's private edition and can invalidate its approval. Do not rerun it just to inspect state. Existing dated editions are reused by scheduled synthesis; unchanged development records remain on the map without becoming daily filler. The full filings job is `.venv/bin/portland-civic filings`; no additional scheduler is needed. Repository draft export/sync is `.venv/bin/python -m portland_brief.repository_archive --push`, after committing unrelated implementation/docs changes and confirming `main` is clean. Commit and push completed, tested increments; never force-push.

## Files to preserve and validation baseline

Git stores implementation/configuration/documentation and summary-only archive pages. It does **not** back up ignored databases, source evidence, models, private reports or scheduler state. Keep `.venv/`, `.models/`, `.cache/`, `data/` and `output/` on this Mac. Do not stage private evidence, model inputs, credentials or raw articles. See [civic operations](civic-operations.md) for SQLite backup/restore and media limits; don't copy live SQLite databases without a consistent snapshot. No new backup or restore drill was performed for this handoff.

The last full implementation validation on October 10 passed **123 tests**. Browser checks covered pin drill-in, search/filter/reset, source expansion, desktop/mobile layouts, keyboard activation, file URLs and unavailable street tiles. Re-run relevant tests after code changes; this documentation-only handoff did not rerun them. The local MLX/Qwen model remains pinned; no API credits are needed. Today's archived generation records 5,600 input tokens, 1,719 output tokens and 12 attempts, with $0 API spend. No new budget or measured human-review cost is claimed.

Suggested restart instruction: **“Read AGENTS.md and docs/handoff.md, check current runtime state, investigate the recorded fetch/cache and health-reporting failures, then continue evidence-backed development reporting and project histories.”**
