# Civic and neighborhood increment

The first daily brief is in `output/drafts/archive/2026-10-09/`. The following commands operate only on private local records. They do not publish, install an OS job, send messages, or purchase services.

## Three independent pipelines

News uses its own collector, checkpoint database, draft database and lock. Meeting discovery and filings have separate run locks; their civic records share SQLite with WAL and a busy timeout. A failure stays in that pipeline's run log. Input capture has one lock per meeting/source job and supports simultaneous worker processes. Scheduling claims jobs durably and runs independent pipelines with bounded retries. A failed pipeline does not cancel the others.

```sh
cd /Users/andys/portlandDailyBrief
source .venv/bin/activate
portland-brief collect-news
portland-brief run
portland-civic discover --max-pages 4
portland-civic filings --max-notices 5 --boundaries
portland-civic status
```

`config/sources.json` covers news. `config/civic-sources.json` covers official meetings, notices and separate geometry layers. Each source has jurisdiction, type, URL, access method, cadence and health notes. Source timestamps, retrieval outcomes, source hashes, parser versions and per-stage success live in SQLite. A successful retrieval alone does not establish processed coverage. `config/procedures.json` contains effective-dated current rules and explicit unverified exceptions. `config/tasks.json` selects task providers and budgets. News inherits its pinned provider/model from `config/settings.json` unless overridden with a corresponding revision. Meeting extraction/article generation currently support only their deterministic provider; transcription supports timestamped import. Alternate API providers are not implemented and no API credentials are required.

## Discover, reconcile and inspect

```sh
portland-civic discover --source portland-calendar --max-pages 4
portland-civic discover --source portland-documents-active --max-pages 4
portland-civic discover --source portland-documents-completed --max-pages 4
portland-civic discover --source portland-documents-completed --start-url 'https://www.portland.gov/council/documents/past?page=4' --max-pages 4
portland-civic discover --source portland-votes --max-pages 4
portland-civic reconcile-votes
portland-civic inspect-event meeting:0d4e70235666cf87535d095a
```

The continuation URL appears in the scan result. A continuation completes that slice; it does not prove the entire index was ingested or remained unchanged during a multi-page scan. Active/completed indexes share official document numbers. Calendar records are discovered even without published agendas. Their calendar IDs and agenda-session IDs remain separate until an explicit official relationship is available; there is no title/date-based automatic identity merge. This reconciliation gap is visible in coverage status.

County discovery uses its own official Granicus archive, session types, meeting IDs and procedure snapshot. An agenda is a proposal, including its consent items. County attachments currently provide URL identity fallbacks; cross-meeting matter identity needs verified county document numbers. County outcomes/minutes extraction remains a later increment.

## Working official meeting samples

```sh
portland-civic ingest-agenda --url https://www.portland.gov/council/agenda/2026/10/7
portland-civic ingest-document --url https://www.portland.gov/council/documents/ordinance/title-17-bulk-fuel-terminal-capacity-reduction-regulations --attachments
portland-civic extract-attachment document:5557b9eff3b21797ac172064
portland-civic report meeting:0a73fcd7b4c2d084ebe0fc0d --max-words 500
portland-civic ingest-county-agenda --url 'https://multnomah.granicus.com/AgendaViewer.php?view_id=3&event_id=3999'
portland-civic report meeting:6abb3e04f7ba60fafe0e5299
```

Portland produces linked before/after/follow-through Markdown views, escaped evidence review HTML and a full structured JSON record. The before view of a past meeting is reconstructed from currently retrieved records, not a preserved historical preview. These drafts use official fields/excerpts and deterministic wording; they do not summarize recording discussion or infer motives, consensus or implementation. Long material stays in the structured record. The county sample produces a before view and explicit missing after/follow-through statuses.

Each stored factual claim references an immutable fact, passage and source version. Matter IDs survive multiple appearances through verified official numbers/URLs; agenda ordinals are local. Introducers remain introducers. Amendment motions/votes, document voting-register observations and agenda final votes remain distinct. Register cross-checks compare positions without declaring the sources to be the same event. Ordinary, emergency, consent, mayoral and county validation use separate rules; unsupported exceptional procedures remain held. Published dispositions are retained even when a calculation conflicts.

PDF extraction runs in a subprocess with no credentials, a timeout, CPU/file/page/text limits and a Linux memory ceiling. macOS does not enforce that same address-space ceiling. Scanned/empty/encrypted/oversized PDFs remain unprocessed; no OCR is enabled. Page locators refer to physical PDF pages. One live fuel-terminal attachment has a label/header mismatch; the queue flags it rather than treating the attachment number as settled.

## Editorial review

The report command prints the report IDs and private review HTML paths. Inspect evidence, procedural holds, uncertain transcript names and capture gaps beside the draft. Report JSON supplies the exact current claims for structured edits.

```sh
portland-civic hold REPORT_ID --editor 'Andy Seubert' --note 'Explain unresolved evidence' --review-seconds 120
portland-civic approve REPORT_ID --editor 'Andy Seubert' --note 'Explain checks performed' --review-seconds 900
portland-civic edit REPORT_ID --file edits.json --editor 'Andy Seubert' --note 'Explain edits' --review-seconds 300
portland-civic correct REPORT_ID --file edits.json --editor 'Andy Seubert' --note 'Explain correction' --review-seconds 300
```

Replace placeholders. Durations must be actual measured review time. Edits use `{"claims": [...]}` with existing valid evidence-backed claim records; arbitrary uncited prose is not accepted. Edits/corrections require new review. Material changes revoke approval against the reviewed evidence bundle and preserve an audit entry. Contradictory votes block approval. Unverified transcripts cannot become approved factual claims. Source approval never deploys anything. There is no meeting-publication export or public search/chat yet.

No human has approved these meeting drafts. Review labor is unmeasured so far; budget an initial hour per article as an interview-derived planning allowance, then replace it with Portland review measurements. Do not describe that allowance as measured Portland effort or include it in a service quote.

## Recoverable recording input and transcripts

`ffmpeg` and `ffprobe` are already installed on this Mac. On a new Mac, install them with `brew install ffmpeg` if input capture is needed. Recording is input to written coverage, never a podcast or speech output.

```sh
portland-civic capture MEETING_ID --url 'https://archive-video.granicus.com/multnomah/ACTUAL_ARCHIVE_FILENAME.mp4' --mode archived --max-seconds 3600
portland-civic capture MEETING_ID --url '/absolute/path/to/permitted-recording.wav' --mode local --max-seconds 3600
portland-civic import-transcript MEETING_ID --file transcript.json --source-url 'https://official-source.example/transcript'
portland-civic vocabulary
```

Use an actual discovered permitted media URL, not the example filename. Remote capture accepts only the configured official media hosts, requires a direct playable stream/file, limits protocols and persists closed 30-second audio chunks during capture. Retry counters, chunks, hashes, missing intervals and completeness survive process exit. Re-running a completed job reuses it. Different meetings have separate locks. A duration limit or live EOF/recess/disconnection does not prove completion. Live reconnection records uncertain missing intervals; it never silently joins them into complete evidence. An archived fallback is a separate job for the same meeting, and reports retain the same meeting/phase identity. Archived completeness means the asset's end was reached, not that the agency published the entire meeting.

Council YouTube URL resolution, live-stream watchdog deployment, live completion verification, recording/transcript alignment and automatic speech recognition are **not validated or enabled**. Actual capture testing used a two-second local silent fixture, not a public council recording. Do not claim a real recorded-meeting report works yet.

Transcript import format:

```json
{"automatic": true, "segments": [{"start_seconds": 0, "end_seconds": 30, "speaker": "speaker 1", "text": "Raw automatic transcript"}]}
```

Raw text/diarization labels are immutable source versions. Imported names stay uncertain; the vocabulary never silently rewrites them. Corrections have separate fields, but a recording-verified attribution/quotation workflow and transcript outcome reconciliation remain unimplemented. Context batching retains segment IDs, timestamps and supplied agenda boundaries; it is not a working long-transcript article generator. Transcript facts are provisional and cannot bypass official vote checks.

## Neighborhood events

The first live notice sample covers five notices: three proposals, one revised staff report and one generic decision. The latter two retain unknown procedural stages until their documents establish the actual outcome. Official neighborhood fields and addresses support current tags; geometry is not invented from mentions. All 125 features returned by the official neighborhood layer are retained with a verified count. Council districts are a different four-feature layer; invalid geometries are retained and held, never silently repaired.

A manual filing import supplies `case_identifier`, explicit `stage`, `evidence_text`, `jurisdiction_id`, optional address/project geometry, and any explicit impact areas. Supported stages are pre-application, proposal, application, approval, denial, permit, construction, opening and unknown. Revised records keep their stable event identity and source versions. A new stage creates another event; it does not overwrite the earlier proposal. Point/line/polygon intersection records boundary version, method, multiple areas and ambiguity. No automated geocoder is configured.

```sh
portland-civic import-filing --file filing.json --url 'https://www.portland.gov/ACTUAL-OFFICIAL-NOTICE'
```

The neighborhood-contact application's API is not verified. Its adapter stays disabled and supports validated manual import. The association source schema distinguishes missing material from absent meetings, but no association source is validated or ingested yet. Association positions, outreach, permits, construction and openings are not inferred from these five notices.

## Scheduler, pause and recovery

```sh
portland-schedule tick
portland-schedule tick --run
portland-schedule serve --poll-seconds 30
```

The first command only inspects/initializes durable due records. `tick --run` explicitly executes due collectors; daily synthesis targets 7 a.m. LA, with current-date catch-up when today's edition is missing after wake/login. `serve` runs a foreground local process; Ctrl-C stops it. Independent failures retry up to three times per occurrence, then move to the next cadence while retaining logs. Jobs do not replay daily synthesis twice after a fall-back clock change. A nonexistent configured spring-forward time is skipped. Collection checkpoints retain late arrivals up to the configured 72-hour lookback, label older publications and suppress unchanged stories already synthesized on a previous date. Revisions remain eligible. This is bounded retention, not a guarantee for arbitrarily late stories.

Andy authorized the persistent Mac runner on October 10. The user LaunchAgent checks `tick --run --scheduled` every minute. Pause it with `schedule.enabled=false` or remove it using `python -m portland_brief.launch_agent remove`. Daily summary-only review drafts are committed/pushed using the reference's dated `posts/`, archive and latest-page arrangement; civic reports and their evidence remain private pending review. The archive sync job retries independently. GitHub Actions remain disabled and Pages is off. See the README for installation/status/recovery commands.

```sh
portland-brief backup --out /absolute/new/path/news.sqlite3
portland-civic backup --out /absolute/new/path/civic.sqlite3
```

Preserve `data/source-versions/`, `data/capture/`, `data/news-collection.sqlite3`, scheduler state, `output/` and configuration with the SQLite backups. Stop running jobs before filesystem copies of live databases; use SQLite's backup command for consistent snapshots. To test restoration, copy config into a separate recovery directory, place the backup at its configured data path, copy source artifacts and keep their stored paths available, then run `portland-civic --root /absolute/recovery/path status`. Run `PRAGMA integrity_check` and compare record counts. Do not overwrite the active database or leave an old WAL beside a restored file. Automated tests restore both database snapshots; artifact portability and a whole-machine disaster recovery drill remain unverified.

Metrics include news input/output tokens, retries and measured elapsed generation; source retrieval/job states; source/artifact storage; capture minutes/chunks/gaps; and explicitly recorded review seconds. No audio/model API service is configured. Storage/electricity and human labor are separate costs. Failed-source timestamps never imply withdrawal, deleted documents or canceled meetings.
