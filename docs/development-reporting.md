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

## Next milestone gates

1. Complete city land-use notice pagination and extract linked plans/decision attachments. Establish height, housing count, commercial space, location, official case identifiers and participation deadlines from passages/pages. Resolve the flagged addresses and heights; preserve disagreement until resolved.
2. Link related land-use, whole-building and component permits using explicit official identifiers and documents. Connect verified project geometry to neighborhood boundaries, preserving corner-address and boundary ambiguity. Add the county adapter separately.
3. Connect development records to editorial approve/edit/hold/correct actions, with evidence-version approval invalidation and measured human review time. Publish a **Development Watch** section in the brief from reviewed new records or material changes, with source links and explicit stage labels. The existing civic approval boundary remains in place; the current private screening draft does not bypass it.

The intended story explains what is proposed or changed, the verified location, height, homes and business space, exact procedural status, and any stated next action or public-comment deadline. An issued permit is not proof of construction, completion or a business opening. Quiet days do not require development filler. The current commercial scan does not finish the land-use, county, document-extraction or editorial/publication gates.
