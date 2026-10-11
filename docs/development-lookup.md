# Identify a development by location

Verified October 10, 2026. Start with official records and preserve their exact procedural stage.

| Task | Official tool |
| --- | --- |
| Browse new construction and demolition permits on a map | [Construction and demolition map](https://www.portlandmaps.com/bds/construction_and_demolition_map) |
| Investigate an address or intersection | [PortlandMaps](https://www.portlandmaps.com/) |
| Filter permits by status, location, type and date | [Advanced permit search](https://www.portlandmaps.com/advanced/index.cfm?action=permits) |
| Browse commercial applications | [Commercial permit intakes](https://www.portlandmaps.com/reports/index.cfm?action=co-intakes) |
| Browse issued commercial permits | [Commercial permits issued](https://www.portlandmaps.com/reports/index.cfm?action=co-issued) |
| Browse residential applications / issued permits | [Residential intakes](https://www.portlandmaps.com/reports/index.cfm?action=rs-intakes), [issued permits](https://www.portlandmaps.com/reports/index.cfm?action=rs-issued) |
| Find land-use proposals, decisions and comment deadlines | [City land-use notices](https://www.portland.gov/ppd/land-use-notices) |
| Find neighborhood development notices | [Neighborhood contact notices](https://www.portland.gov/ppd/neighborhood-contact/search-neighborhood-notices) |

In PortlandMaps, search the address in the upper-right search box. An intersection such as `SW Harrison & SW 4th` can narrow the area when you do not know the number. Select the actual parcel, open **Permits & Zoning**, then select a permit/case. Read the work description, status, activity and associated documents. Record the official permit/application identifier and URL. Condo accounts may require selecting Related Accounts. See the city's [search guide](https://www.portland.gov/ppd/portlandmaps-help-guides/portlandmaps-general-search).

The reports provide date/text filters and CSV/JSON/XML exports. Intake and issuance reports are separate. Different permits at an address can represent separate projects, trade work, revisions or deferred submittals; do not combine them just because their titles or location match.

## Local evidence lookup

From the project directory:

```sh
.venv/bin/portland-civic lookup-project --address '6866 SE 87TH AVE' --refresh > data/project-lookup-6866.json
```

Replace the quoted address with a street number, direction and street name; the final street suffix is optional. The current command supports one street address, without unit numbers or intersections. Use the interactive map for those searches. `--limit 100` is the default bounded candidate limit; allowed values are 1–200. Omit `--refresh` to use conditional caching. The lookup uses the filings lock independently of news collection and meeting discovery.

Output includes exact official status, description and dates in `official_fields`, an application identifier, folder ID, detail link, source geometry, source version and supporting passage. `APPLICATION` contains the stable permit/application number; the GIS field named `PERMIT` is a category label, not that number. Date fields retain official epoch-millisecond values; no effective dates or construction dates are calculated. A source-provided neighborhood is labeled as such and has not been independently spatially matched by this command. `CITY` and `COUNTY` are location fields, not proof of deciding authority.

Evidence bytes, hashes, retrieval records and parser versions stay under ignored `data/` and civic SQLite. Unchanged inputs reuse source versions and passages. Changed descriptions/statuses create new evidence versions. Failed or stale fetches do not replace successful evidence. An API transfer limit yields `partial`; an empty response means no matches in this query, not that no development exists. No model, generated article or new public post is involved.

The live example returned application **2025-020413-DFS-01-RS**, folder **5289977**, status **Issued**, with a fire-sprinkler deferred-submittal description. Those fields agree with the [official residential issued-permits report](https://www.portlandmaps.com/reports/index.cfm?action=rs-issued). It is not evidence that the entire development is finished. Parent permits and associated land-use cases still need inspection.

## Coverage limits and next increment

The city [public-records guide](https://www.portland.gov/ppd/public-records) says most documents for permits issued after December 2023 are not yet available in PortlandMaps. Current status metadata can still appear while plans or attachments are absent. The guide identifies eFiles for some completed land-use cases. Missing documents are a gap, not a denial, withdrawal or missing project. No records request is sent by this application.

The permit map and GIS layer provide a useful starting point, not comprehensive verified development coverage. Current on-demand lookup does not reconcile every historical permit, parent permit, land-use case, or county record. Automatic permit ingestion, source-change checkpoints, a location-based project watchlist and a local project-map interface are the next implementation increments. County notices remain a separate jurisdiction/source adapter; Portland postal addresses alone do not establish city permitting authority.
