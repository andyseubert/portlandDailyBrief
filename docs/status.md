# Implementation status — October 10, 2026 (America/Los_Angeles)

Plan 1 is working locally and tested. Andy authorized daily local execution and repository archiving on October 10. A user LaunchAgent targets 7 a.m. America/Los_Angeles and catches up on wake/login. [Daily review drafts](../briefings.md) are shared in GitHub using dated `posts/` files, complete archive indexes and Git revision history. No website is deployed and no civic report is published. Plan 2 remains an evidence-first civic/neighborhood increment, not comprehensive reporting.

## Inspection and changes

Before this increment, the project had configured news feeds, bounded/cached requests, source-linked local-model summaries, NWS weather, private Markdown/HTML archives and editorial versioning/backup. It lacked independent collectors/checkpoints, structured civic records, effective-dated procedures, meeting discovery/reconciliation, neighborhood events and input capture.

Implemented now: independent news collection/checkpoints and late-arrival retention; separate meeting/filing job locks; durable local-time scheduler code; official calendar/committee/document/register and county archive parsers; jurisdictions, bodies, people, membership snapshots, matters, topics, appearances, documents, motions, amendments, vote events/individual votes and cross-checks; effective-dated council/county rules; source versions/passages/facts; document-based meeting views/review queue; isolated PDF page extraction; structured filings and separate official geometry layers; recoverable direct-media/local input capture and timestamped transcript import. No model has tools or publication authority. No automated speech recognizer or transcript-to-article generator is enabled.

## Plan 1 validation and first brief

The [first source-checked brief](../posts/2026-10-09.md) contains seven stories, with limited-context labels and original summaries. Its evidence/review page remains private at `output/private-review/2026-10-09.html`. Manual source checking corrected alarm terminology, an unsupported characterization, and an unrelated Eugene comparison. Automatic citation/number checks alone do not establish accuracy. Repository sharing does not supply Andy's editorial approval.

OPB, Willamette Week, Portland Mercury, KOIN, KGW, KPTV, Portland RSS and the county listing are technically validated. Andy subsequently reported separate KGW/KPTV permissions; these feeds are now enabled. OregonLive RSS is verified and now enabled under Andy's subsequently reported Advance permission; the prior terms assessment remains in access history. County cards currently lack enough descriptive evidence for prose. Source constraints remain documented in `config/sources.json`; fetched source text stays private, while the user-authorized archive shares original attributed summaries and links.

Measured sample generation: 5,623 input tokens, 1,731 output tokens, 12 attempted generations including retries, about 61 seconds. The pinned local MLX/Qwen model requires no API credits; API spend is $0. Thirty comparable daily runs would use about 168,690 input and 51,930 output tokens, including the measured retries. The configured 30,000-token/run ceiling bounds 30 runs at 900,000 tokens. Download/storage, electricity and human review are separate unpriced costs. These are measurements/estimates for this local workload, not a cloud service quote or a promise of unattended factual accuracy.

The October 10 run produced eight supported review-draft stories using 5,563 input tokens and 1,756 output tokens across 12 attempts, with $0 API spend and 196.55 seconds of model time. A source comparison corrected unsupported roundtable detail, school-closure characterization, and a singular firefighter reference before archiving. Both dated editions are retained. The installed runner and independent collector execution have been checked on this Mac.

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
| Independent collection/schedule | Installed Mac runner, separate locks/checkpoints, durable due jobs, LA/DST tests, bounded failures and repository retry | Full source inventory reconciliation; missed historical dates require explicit backfill |
| Meeting capture/attribution | Job/chunk/gap persistence, input capture, archived fallback jobs, raw transcript/vocabulary, uncertain speaker labels | Real public recording test, council YouTube adapter, automatic transcription, reviewed attribution/quotation and live completion verification |
| Outcomes/article/review | Official structured outcomes, bounded views, evidence review HTML, approve/edit/hold/correct, versioned evidence and recorded review seconds | Long-transcript outcome reconciliation and measured human editorial workload |
| Archive/costs | Private revisions, jurisdiction/matter/evidence metadata, token/retry/storage/capture/review metrics | Approved civic export/search/chat, production operating-cost measurements |

Current procedural sources: [Portland Charter](https://www.portland.gov/charter/2/1), [Council Actions Code](https://www.portland.gov/code/3/02/030), and [Multnomah Board Rules](https://multco.us/info/board-rules). Configured council rules begin October 3, 2025; county rules begin their stated March 19, 2026 adoption. Historical rules are not fabricated. Unverified exceptions include property disposal/street vacations, franchise timing, special procedural motions, specific mayoral eligibility, county special thresholds and item-specific participation exceptions. Attendance never establishes every vote's participants.

## Validation and next gate

84 tests pass, including saved real October 7 actions/roll calls, the October 15 county agenda, an actual land-use notice and a real official amendment PDF. Synthetic cases explicitly test continuation identity, consent linkage without invented roll calls, contradictory tallies, outages, changed attachments, historical membership limits, geometry ambiguity, missing recordings, citation integrity, idempotent reprocessing, backups, late arrivals and independent scheduled failures. Added archive checks verify excluded private evidence, preserved correction history, no duplicate commits, retry after a failed push, rejection of unrelated staged files and exclusion of private files placed in posts/, launchd configuration, wake catch-up, reuse of an already generated daily edition, and the pause switch. An actual two-second local `ffmpeg` capture verifies persisted input and safe reruns; it is not a real meeting report. No human meeting-review duration has been measured and no meeting report is approved.

The next release gate is reviewed meeting coverage: complete explicit calendar/session links, historical roster/rule evidence, PDF/official-action reconciliation, then one permitted archived recording and a timestamp-supported report. Systematic coverage cannot be claimed until discovered meetings reconcile with processed records. Website hosting, civic publishing and public search remain later stages.

No podcast, voice cloning, social distribution, newsletter, crossword, automatic outreach, paid service or public deployment is enabled. GitHub is a user-authorized fork with independently written Portland code on its own branch; Actions are disabled and Pages is off. See `docs/civic-operations.md` and `README.md` for complete commands and source/recovery limits.

## Development and zoning priority — October 10

Configured development/zoning ranking ahead of routine general-interest stories while retaining freshness, geographic relevance, source limits and evidence gates. Selection records the matched priorities. Saved tests verify ranking, exclusion of stale/outside/thin items, distinct city/county case IDs, separate proposal/decision events and disabling the preference. No historical brief was rewritten.

Verified official entry points added to the civic registry: [Portland Planning Commission](https://www.portland.gov/bps/planning/planning-commission), [county public notices](https://multco.us/info/public-notices), [county hearings officer](https://multco.us/info/hearings-officer), and [county Planning Commission](https://multco.us/info/planning-commission). Their source-specific adapters remain pending and disabled. County land-use notices cover unincorporated areas; deciding jurisdiction and urban-pocket agreements require case-specific evidence. Commission recommendations remain distinct from city/county adoption.

## Source/access validation — October 10

Eight enabled source endpoints parsed successfully in the live refresh: OPB 42 records, Willamette Week 9, Mercury 10, KOIN 10, Portland news 50, county news listing 24, Portland zoning/land-use 25, and Portland planning 25. HTTP 304 is recorded separately from new body retrieval. These returned counts are not comprehensive discovery or story counts. The county news listing still supplies only headlines; planning/zoning RSS does not replace the civic case/attachment adapters. Of the zoning feed’s sampled 25 descriptions, only two had at least 12 words; planning had 24 of 25. Freshness and evidence selection further limit generation.

| Source | Access assessment and action |
| --- | --- |
| KOIN / Nexstar | Andy reports automated-collection and browser User-Agent permission. Recorded user attestation, enabled KOIN, verified RSS with its configured normal User-Agent. No independent permission-document verification was required or claimed. [Parent terms](https://www.nexstar.tv/terms-of-use/) remain documented alongside this exception. |
| KGW | Initially disabled after [station terms](https://www.kgw.com/terms) review. Andy subsequently reported permission; enabled under that attestation and live-validated with the configured browser User-Agent. Prior assessment retained. |
| KPTV | Initially disabled after [terms](https://www.kptv.com/terms-of-service/) review. Andy subsequently reported permission; enabled under that attestation and live-validated with the configured browser User-Agent. Prior assessment retained. |
| OregonLive | Publisher RSS now retrieved and parsed: 50 records. The [Advance Local agreement](https://www.advancelocal.com/advancelocalUserAgreement/user-agreement.html), sections 5.1.4, 5.1.10 and 5.2.1, restricts automated collection and AI use and expressly applies restrictions to RSS. Initially disabled pending permission; Andy subsequently reported receiving Advance permission for automated OregonLive collection. Enabled under that attestation with the configured normal browser User-Agent; previous access assessments retained. |
| OPB / Mercury / Willamette Week | Feeds validated. Source constraints remain recorded; original summaries and citations are distinct from a public/commercial content license. |
| Portland planning and zoning/land-use | Published official RSS links discovered and tested, enabled with shared City of Portland source balancing. |

Added checks verify permission-required sources never fetch even if their enabled flag is toggled, user-authorized sources collect with the configured User-Agent, department feeds do not multiply a publisher’s quota, and disabled buffered material cannot silently return to model input. Historical editions are preserved. Nexstar permission is not transferred to unrelated publishers. No outreach, paid service or public website deployment was performed.

## Address-based development lookup — October 10

Added a bounded on-demand `portland-civic lookup-project` command against the official PortlandMaps All Permits GIS layer. It retains official folder IDs, separate application identifiers/category labels, exact status and description, source geometry, official neighborhood fields, direct detail links, passages and private source versions. No inferred construction/opening, title-based project merging, prose generation or publication. Query failures preserve the last successful evidence. Transfer-limit responses remain partial; no result does not establish no project exists.

Live lookup at 6866 SE 87th Avenue returned one issued fire-sprinkler deferred submittal, folder 5289977/application 2025-020413-DFS-01-RS. Address, description, status and folder ID were compared with the [official residential issued-permits report](https://www.portlandmaps.com/reports/index.cfm?action=rs-issued). This is a component permit, not verified completion of a whole development. One saved real response supplies acceptance fixtures for duplicate identities, exact address matching, changed source versions, outages, incomplete queries and repeat processing. Full property history, parent-permit linkage, county permit ingestion, broad automated permit monitoring and address-based project watchlists remain incomplete; the initial focused monitor below is now implemented. The city's interactive map, advanced search and exportable permit reports are cataloged in `config/civic-sources.json`; their catalog flags do not imply scheduled monitoring.

The subsequent live refresh with KGW/KPTV enabled returned ten sources: OPB 42, Willamette Week 9, Mercury 10, KGW 40, KOIN 10, KPTV 20, Portland news 50, county listing 24, zoning/land-use 25 and planning 25. All ten parsed; collection buffered 255 records without model use. At that stage, OregonLive was excluded from scheduled collection pending permission; the later Advance authorization below resolves that gap. The full suite passed 92 tests, including eight new permit-lookup cases; a live repeat reused the same source version, permit identity and passage. No historical edition was regenerated.

## Advance/OregonLive authorization — October 10

Andy reports receiving Advance permission to automate collection from OregonLive. Recorded the permission as user attestation, retained both previous access assessments, enabled the verified publisher RSS endpoint and configured the same normal browser User-Agent used for the other authorized publishers. A live refresh parsed 50 OregonLive records with no rejected feed entries; all eleven enabled source endpoints succeeded. News collection buffered 305 records in total, including OregonLive, using zero model tokens. These are returned candidates, not 305 eligible local stories. Portland relevance, rolling publication windows, evidence checks and source balancing still apply during synthesis.

The 37 existing brief tests passed, including permission gating, source-specific User-Agent handling, disabled-buffer filtering and evidence validation. OregonLive joins the existing scheduled collection and next daily synthesis without an additional job. Historical briefs were preserved; source descriptions remain private.

## Four-plus-story mixed-use development watch — October 10

Added a configurable official Commercial Construction GIS connector to the existing daily filings pipeline. A live one-year scan reconciled 141/141 records across two pages: two height/use matches, one height conflict, 62 needing more evidence and 76 exclusions. All records retain exact official identifiers/status, geometry, passages and private source versions. Drafts appear under `output/development-review/`; no civic approval or public release has occurred.

Relevant records are cross-checked against the official commercial intake report by folder ID. The sample returned two address disagreements and one unlisted report row. Disagreements are held; an unlisted intake does not imply withdrawal. Support-office housing is not treated as a business component. Material changes reopen review, while cosmetic source changes do not generate record versions or alerts. Source outages and incomplete scans preserve previous records and distinguish inventory completeness from unresolved reconciliation/editorial review.

The full suite passed 107 tests, including 15 new development checks for saved real pagination, conflicting heights/addresses, absent intake rows, support-office false positives, component permits, zoning/incidental-location mentions, repeat processing, cosmetic changes, outages, evidence passages and HTML escaping. One live rerun and the scheduled filings command were checked for stable record versions. Model tokens/API cost are zero for this deterministic connector. Full city notice pagination, linked plan/decision extraction, county development ingestion, parent-record linkage, spatial neighborhood assignment for these candidates and reviewed daily-brief integration remain pending. See [operating commands and release gates](development-reporting.md).
