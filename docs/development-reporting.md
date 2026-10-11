# Mixed-use development reporting

The initial focus is buildings with **four or more stories, residences, and commercial space in the same proposed building**. It is a reporting priority, not a quota. Smaller developments and other public-interest stories retain their existing workflows.

## Working increment

The official PortlandMaps Commercial Construction GIS layer is collected in the existing daily filings job, independently of news collection and meetings. A bounded creation-date lookback defaults to one year. Page offsets are followed and unique folder IDs are reconciled against an official count before completeness is recorded for that specific scope. This is not a complete city/county development inventory, historical backfill, or coverage of conversions and additions.

```sh
.venv/bin/portland-civic developments --refresh
```

Read `output/development-review/watch.md` or `watch.html`. `records.json` includes structured records and source passages; `collection-status.json` records the latest attempt, including failures. The HTML contains direct official permit links. Output stays local and private. No model, paid service, website publication or additional scheduler has been enabled.

Adjust `filings.development_focus` in `config/tasks.json`:

| Setting | Default |
| --- | --- |
| Minimum stories | 4 |
| Creation-date lookback | 365 days |
| Page size / maximum pages | 100 / 4 |
| Official intake-report cross-checks | Enabled; up to ten relevant records per scan |

Height comes from explicit project descriptions or the positive official `NUMBSTORIES` field. Zero is treated as missing, rather than proof of a zero-story building. Contradictory or multiple heights are held. Missing height/use evidence stays in the structured queue. Permitted zoning height, nearby shops, support offices, residential amenities and a mixed-use zoning label do not establish the requested project combination. Whole new-construction records are screened separately from alterations, trade permits and deferred submittals.

Official status is retained verbatim. The GIS layer and the intake report remain separate evidence sources. A conflicting address/status creates a hold, without choosing an authority by convenience. A missing intake row does not mean withdrawal or denial. Distinct permits are not merged by address or title.

Source bytes, parser versions, passages, inventory runs, checkpoints, record versions and review states are kept in civic SQLite and ignored source-artifact storage. Reprocessing unchanged records creates no duplicate record versions. Whitespace and GIS object-number changes do not trigger material updates. Source failures preserve successful evidence and the previous draft. Old records absent from a bounded scan remain retained with an explicit gap, without removal alerts. Material record changes reopen review. Retrieved text is data, not model/tool instructions.

## First live baseline

On October 10 the monitor processed **141/141 records across two pages** for the configured Commercial Construction source and creation-date window. The deterministic screen returned two height/use matches, one height conflict, 62 records needing more evidence and 76 exclusions. Three relevant records were cross-checked against the official intake report: two address disagreements were held, and one record was not listed in that report query. Missing detail is not evidence of a negative outcome.

The saved source fixtures include a four-story record whose numeric story field is zero, a six-story apartment/retail record, a description/numeric height disagreement, support-office housing that must not become commercial mixed use, and an intake/GIS address disagreement. These are baseline discoveries rather than proof of newly proposed projects today. No report is editorially approved.

## Evidence and review increment — October 10

City notice pagination now reconciles **41/41 listed entries**, including 35 case notices and six general information documents without invented case IDs. The collector retrieved 36 PDFs and processed 622 pages in an isolated worker. Twenty-six pages have no extracted text; some may be blank or image-based. One twelve-page appeal has no extracted text at all. Visual/OCR review remains pending; no OCR or paid service is enabled. Complete index collection does not establish complete interpretation of every attachment or historical case coverage.

```sh
.venv/bin/portland-civic notices --refresh
.venv/bin/portland-civic development-evidence --refresh
.venv/bin/portland-civic development-reports
```

The notices command returns nonzero for incomplete collection or extraction and preserves successful evidence. Open `output/development-review/index.html` for the private queue. Individual folders contain `article.md`, `review.html` and `review.json`. Gibbs needs review; Division and Marshall are held. Each substantive summary claim points to a checked official field and source passage. Immutable explanation versions, source bundles and editorial audits preserve historical snapshots. Unchanged regeneration preserves edits; material source changes invalidate approval.

Official tax-lot/state-ID evidence connects Division's addresses to R184111, but the address-change register says **Pending**. Both addresses and the hold remain. Marshall's property register contains distinct pending and completed situs changes; these do not establish building construction. Its numeric three-story field still conflicts with the four-story description. Gibbs's missing report row remains a gap. A searched older Gibbs proposal download returned 404; its contents were not ingested.

Explicit `26-021266-MT` and `26-007681-MT` references resolve to separate official trade permits, with parent relationships unestablished. Primary permit points spatially match Richmond, Northwest District and Homestead; these are point-location tags, not full project footprints. Invalid district geometry stays flagged separately.

### Editorial commands

Use the stable `report_id` from `queue.json`, the actual editor's name and measured review duration. Numbers below illustrate command syntax, not measured Portland review effort:

```sh
.venv/bin/portland-civic review-development REPORT_ID approve --editor 'EDITOR NAME' --note 'Compared claims with official records' --review-seconds 120
.venv/bin/portland-civic review-development REPORT_ID hold --editor 'EDITOR NAME' --note 'Height conflict unresolved' --review-seconds 45
# Copy the claims array from review.json into {"claims": [...]}; select/reorder supported claims.
.venv/bin/portland-civic review-development REPORT_ID edit --file data/development-edits.json --editor 'EDITOR NAME' --note 'Shortened draft' --review-seconds 60
.venv/bin/portland-civic review-development REPORT_ID correct --file data/development-edits.json --editor 'EDITOR NAME' --note 'Correction rationale' --review-seconds 60
.venv/bin/portland-civic development-reports
```

Invented values/citation IDs are rejected; edits/corrections reopen review. Contradictory or materially changed evidence blocks approval. No production human review duration is recorded and no development report is approved. These commands do not publish a website.

The existing daily filings job now follows configured notice budgets (four pages, 100 entries, attachments enabled), researches focused candidates and regenerates the queue independently of news and meeting jobs. The daily brief may include up to three approved development snapshots within its rolling window, counting toward its existing story maximum. Baseline discoveries are labeled; quiet days require no filler. Repository export checks the reviewed explanations/evidence and approval at generation time, preserving dated history when later evidence changes.

The full suite passes **117 tests**, including real three-page inventory/download links, PR identifiers, incomplete/outage coverage, pending address changes, distinct related permits, review holds, invented claims, approval invalidation, historical snapshots and archive gates. The filings command ran end to end locally. This deterministic increment uses zero model tokens and $0 in API charges. Civic source-artifact storage is approximately 138 MiB across the project. No human review cost is claimed.

## Remaining milestone gates

1. Interpret the retrieved plans/decisions, including scanned pages. Establish explicit height, use, costs, case identifiers and participation deadlines from passages/pages. Resolve held addresses/heights only with sufficient official evidence.
2. Link related land-use, whole-building and component permits using explicit official identifiers and documents. Connect verified project geometry to neighborhood boundaries, preserving corner-address and boundary ambiguity. Add the county adapter separately.
3. Use the implemented review controls on one real draft and verify its approved snapshot through a daily brief and Git archive. No live report is approved yet. Measure actual review effort; expand editing beyond supported claim selection as structured extraction broadens.

The intended story explains what is proposed or changed, the verified location, height, homes and business space, exact procedural status, and any stated next action or public-comment deadline. An issued permit is not proof of construction, completion or a business opening. Quiet days do not require development filler. The current commercial scan does not finish the land-use, county, document-extraction or editorial/publication gates.
