# Implementation status — October 9, 2026 (America/Los_Angeles)

Plan 1 is working locally and tested. Plan 2 has an evidence-first civic and neighborhood increment, not comprehensive Portland-area reporting. No brief or meeting report has been publicly published; no site or persistent schedule has been installed.

## Inspection and changes

Before this increment, the project had configured news feeds, bounded/cached requests, source-linked local-model summaries, NWS weather, private Markdown/HTML archives and editorial versioning/backup. It lacked independent collectors/checkpoints, structured civic records, effective-dated procedures, meeting discovery/reconciliation, neighborhood events and input capture.

Implemented now: independent news collection/checkpoints and late-arrival retention; separate meeting/filing job locks; durable local-time scheduler code; official calendar/committee/document/register and county archive parsers; jurisdictions, bodies, people, membership snapshots, matters, topics, appearances, documents, motions, amendments, vote events/individual votes and cross-checks; effective-dated council/county rules; source versions/passages/facts; document-based meeting views/review queue; isolated PDF page extraction; structured filings and separate official geometry layers; recoverable direct-media/local input capture and timestamped transcript import. No model has tools or publication authority. No automated speech recognizer or transcript-to-article generator is enabled.

## Plan 1 validation and first brief

The first source-checked brief contains seven stories, with limited-context labels and original summaries. It is at `output/drafts/archive/2026-10-09/brief.md` and `.html`; its evidence/review page is `output/private-review/2026-10-09.html`. Manual source checking corrected alarm terminology, an unsupported characterization, and an unrelated Eugene comparison. Automatic citation/number checks alone do not establish accuracy. It remains a local draft awaiting Andy's editorial approval.

OPB, Willamette Week, Portland Mercury, KOIN, Portland RSS and the county listing are technically validated. KGW/KPTV feeds respond but are disabled after terms review; OregonLive is unvalidated and disabled. County cards currently lack enough descriptive evidence for prose. Private noncommercial source restrictions and unresolved public reuse are documented in `config/sources.json`; public release is not enabled.

Measured sample generation: 5,623 input tokens, 1,731 output tokens, 12 attempted generations including retries, about 61 seconds. The pinned local MLX/Qwen model requires no API credits; API spend is $0. Thirty comparable daily runs would use about 168,690 input and 51,930 output tokens, including the measured retries. The configured 30,000-token/run ceiling bounds 30 runs at 900,000 tokens. Download/storage, electricity and human review are separate unpriced costs. These are measurements/estimates for this local workload, not a cloud service quote or a promise of unattended factual accuracy.

## Actual official coverage

| Source | Observed and processed scope | Remaining gap |
| --- | --- | --- |
| Portland clerk calendar | All 47 currently listed future events, three pages | Calendar-to-agenda identity reconciliation; most agendas unavailable/unprocessed |
| Current policy committees | Five institutions and current observed memberships; whole committee has 12 members | Earlier effective dates and historical membership evidence |
| Active council documents | 49/49 index records, two pages | Detailed document/attachment ingestion for most matters |
| Completed council documents | First 100/4,108 index records, four pages | Explicit continuation/backfill; complete reconciliation not established |
| Council voting register | First 96/15,930 rows, four pages | Historical backfill and completeness; register events remain distinct from agenda motions |
| October 7 council agenda | One session, 14 appearances, four amendment motions, six agenda vote events | Recording discussion and historical membership verification; amendment timing review |
| Document 2026-312 | Official purpose/cost/impact fields and 16 attachments retrieved; one PDF page extracted | Remaining PDF extraction and reviewed interpretations; Kanal attachment label/header mismatch |
| Multnomah archive | 1,548 discovered session notices on the returned archive page | This is discovery only; historical archive exhaustiveness not independently established |
| October 15 county agenda | Four proposed items, including three consent candidates; participation instructions | No votes/outcomes exist yet; no county minutes/outcome parser |
| Land-use notices | Five live samples: three proposals, revised staff report and generic decision; addresses/deadline/official neighborhood fields | Full notice pagination, linked decision review, neighborhood-contact endpoint, geocoding and association sources |
| Official geography | Neighborhood layer: 125 returned features, count reconciled; separate district layer: four features | Two district geometries invalid and held; no silent repairs or location invention |

Four October 7 final document roll calls agree with the fetched register positions. That cross-check does not merge them into an agenda amendment event or supply missing historical membership. Fuel-terminal matter 2026-312 was passed to another reading as amended, not finally adopted. Adopted ordinance numbers, document numbers, URLs and local agenda ordinals are distinct. Effective dates and implementation remain unknown unless explicitly evidenced.

Development-time parser corrections remain in private history. Three initially misclassified notice proposals are marked superseded; five current events remain. A fixture test now prevents selection of the page's feedback field as notice evidence. Parser changes require a new version when unchanged bytes produce different structured facts.

## Requirement gates

| Requirement | Implemented increment | Not yet complete |
| --- | --- | --- |
| Durable public-record model | Separate normalized entities and identifiers; official-number identity across appearances; no title merging | Explicit calendar/session mapping and durable county matter identifiers |
| Official factual foundation | Source-specific adapters, pagination status, immutable passages and register cross-checks | Comprehensive past records and all attachment/recording formats |
| Procedural distinctions | Exact dispositions plus normalized actions; amendment/final events separate; no universal effective-date calculation | Historical and special procedure evidence |
| Effective-dated voting/institutions | Council/county snapshots, current observed rosters, whole committee explicit | Historical council/committee memberships and committee-specific procedure snapshots |
| Deterministic votes | Duplicate identity/tally/threshold conflicts held; absence distinct; consent helper creates one linked event | Automatic recognition/ingestion of a real completed consent calendar; verified mayoral exception example |
| Versioning/recovery | Raw/source/parser hashes, meaningful-change detection, no outage removal alerts, review revocation | Changed participation/media parsing for every format; full disaster restore drill |
| Before/after/follow-through | Linked evidence excerpts, structured records, document-based drafts and missing outcomes | Editorially refined consequence articles; verified quotes/recording discussion and later implementation tracking |
| Neighborhood coverage | Stable filing events/stages, official field tags, geometry intersection and ambiguity | Neighborhood-contact API, automatic verified geocoding, association registry population |
| Independent collection/schedule | Separate locks/checkpoints, durable due jobs, LA/DST tests, bounded failures | Persistent OS runner remains uninstalled; full source inventory reconciliation |
| Meeting capture/attribution | Job/chunk/gap persistence, input capture, archived fallback jobs, raw transcript/vocabulary, uncertain speaker labels | Real public recording test, council YouTube adapter, automatic transcription, reviewed attribution/quotation and live completion verification |
| Outcomes/article/review | Official structured outcomes, bounded views, evidence review HTML, approve/edit/hold/correct, versioned evidence and recorded review seconds | Long-transcript outcome reconciliation and measured human editorial workload |
| Archive/costs | Private revisions, jurisdiction/matter/evidence metadata, token/retry/storage/capture/review metrics | Approved civic export/search/chat, production operating-cost measurements |

Current procedural sources: [Portland Charter](https://www.portland.gov/charter/2/1), [Council Actions Code](https://www.portland.gov/code/3/02/030), and [Multnomah Board Rules](https://multco.us/info/board-rules). Configured council rules begin October 3, 2025; county rules begin their stated March 19, 2026 adoption. Historical rules are not fabricated. Unverified exceptions include property disposal/street vacations, franchise timing, special procedural motions, specific mayoral eligibility, county special thresholds and item-specific participation exceptions. Attendance never establishes every vote's participants.

## Validation and next gate

66 tests pass, including saved real October 7 actions/roll calls, the October 15 county agenda, an actual land-use notice and a real official amendment PDF. Synthetic cases explicitly test continuation identity, consent linkage without invented roll calls, contradictory tallies, outages, changed attachments, historical membership limits, geometry ambiguity, missing recordings, citation integrity, idempotent reprocessing, backups, late arrivals and independent scheduled failures. An actual two-second local `ffmpeg` capture verifies persisted input and safe reruns; it is not a real meeting report. No human meeting-review duration has been measured and no meeting report is approved.

The next release gate is reviewed meeting coverage: complete explicit calendar/session links, historical roster/rule evidence, PDF/official-action reconciliation, then one permitted archived recording and a timestamp-supported report. Systematic coverage cannot be claimed until discovered meetings reconcile with processed records. Hosting, public publishing, public search and persistent scheduling remain later stages.

No podcast, voice cloning, social distribution, newsletter, crossword, automatic outreach, paid service or public deployment is enabled. GitHub is a user-authorized fork with independently written Portland code on its own branch; Actions are disabled and Pages is off. See `docs/civic-operations.md` and `README.md` for complete commands and source/recovery limits.
