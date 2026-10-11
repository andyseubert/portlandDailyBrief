"""Official permit screening and private drafts; no inferred project completion."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

from ..records import safe_url
from .store import stable_id

PARSER = 'mixed-use-permits/3'
STORY_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
               'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
               'eleven': 11, 'twelve': 12}
HEIGHT = re.compile(r'(?<![\d.])\b(\d+|' + '|'.join(STORY_WORDS) + r')[ -]+stor(?:y|ies|ey|eys)\b', re.I)
RESIDENTIAL = re.compile(r'\b(apartments?|residential (?:rental )?units?|dwelling units?|housing units?|condos?|condominiums?)\b', re.I)
BUSINESS = re.compile(r'\b(retail|commercial(?:/retail)?(?:\s+(?:office\s+)?(?:use|space|uses|spaces))|restaurant|storefront)\b', re.I)
NEGATIVE_BUSINESS = re.compile(r'\b(no|without) (?:commercial|retail|business)(?:\s+(?:space|uses?))?\b', re.I)
FIELDS = ('OBJECTID,FOLDERKEY,APPLICATION,PERMIT,STATUS,HOUSE,DIRECTION,PROPSTREET,'
          'STREETTYPE,CITY,COUNTY,FOLDERTYPE,TYPE,WORK_DESCRIPTION,DESCRIPTION,NUMBSTORIES,'
          'NUMNEWUNITS,PORTLAND_MAPS_URL,NEIGHBORHOOD,GIS_LOCATION_TYPE,CREATEDATE,ISSUED,FINALED')


def profile(config):
    result = {'minimum_stories': 4, 'lookback_days': 365, 'page_size': 100, 'max_pages': 4, **config}
    for key, low, high in [('minimum_stories', 1, 100), ('lookback_days', 1, 3650),
                           ('page_size', 1, 200), ('max_pages', 1, 20)]:
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError('Invalid bounded development setting: ' + key)
    return result


def classify(attrs, minimum_stories=4):
    description = ' '.join((attrs.get('DESCRIPTION') or '').split())
    residential = bool(RESIDENTIAL.search(description + ' ' + (attrs.get('TYPE') or '')))
    # A nearby business or a zoning designation is not a proposed commercial use.
    project_clauses = [s for s in re.split(r'[.;\n]', description)
                       if not re.search(r'\b(nearby|adjacent|zoning|zoned|zone allows|maximum allowed)\b', s, re.I)]
    scope_text = ' '.join(project_clauses)
    business = bool(BUSINESS.search(scope_text)) and not NEGATIVE_BUSINESS.search(description)
    heights = sorted({int(m[1]) if m[1].isdigit() else STORY_WORDS[m[1].lower()] for m in HEIGHT.finditer(scope_text)})
    number = attrs.get('NUMBSTORIES')
    numeric = number if type(number) in {int, float} and number > 0 else None
    conflict = len(heights) > 1 or bool(heights and numeric is not None and heights[0] != numeric)
    stories = heights[0] if len(heights) == 1 else numeric if not heights else None
    reasons = []
    if attrs.get('FOLDERTYPE') != 'CO' or attrs.get('WORK_DESCRIPTION') != 'New Construction':
        status = 'excluded'; reasons.append('Not a whole commercial new-construction record')
    elif conflict:
        status = 'hold_conflict'; reasons.append('Height fields disagree or describe multiple heights; check supporting plans')
    elif stories is not None and stories < minimum_stories:
        status = 'excluded'; reasons.append('Below configured story threshold')
    elif NEGATIVE_BUSINESS.search(description):
        status = 'excluded'; reasons.append('Description expressly excludes a commercial component')
    elif stories is not None and residential and business:
        status = 'matches_filter'
    else:
        status = 'needs_evidence'
        if stories is None: reasons.append('Building story count not established')
        if not residential: reasons.append('Residential component not established')
        if not business: reasons.append('Commercial component not established in project description')
    return {'status': status, 'stories': stories, 'height_in_description': heights,
            'height_numeric_field': numeric, 'residential': residential, 'business': bool(business),
            'reasons': reasons, 'minimum_stories': minimum_stories,
            'housing_units_reported': attrs.get('NUMNEWUNITS'),
            'note': 'Screening candidate only; zoning allowance, permit status, construction and opening remain distinct.'}


def fetch_json(fetcher, url):
    raw, meta = fetcher.get(url)
    if meta.get('status') == 'stale_cache':
        raise RuntimeError('Current development collection failed; preserve prior records')
    obj = json.loads(raw)
    if not isinstance(obj, dict) or obj.get('error'):
        raise ValueError('Official development query returned an error')
    return obj, raw, meta


def semantic_record(row):
    return {**row, 'attributes': {k: ' '.join(v.split()) if isinstance(v, str) else v
                                  for k, v in row['attributes'].items() if k != 'OBJECTID'}}


def intake_row(raw, folder_id):
    soup = BeautifulSoup(raw, 'html.parser')
    page_text = soup.get_text(' ', strip=True)
    if not all(label in page_text for label in ['Commercial Building Permit Intakes Report', 'CASE NUMBER', 'IVR NUMBER']):
        raise ValueError('Official intake format unrecognized; missing material is not established')
    link = next((a for a in soup.select('a[href]')
                 if a['href'].rstrip('/') == f'https://www.portlandmaps.com/detail/permit/{folder_id}_did'), None)
    if not link:
        return None
    tr = link.find_parent('tr')
    values = [' '.join(td.get_text(' ', strip=True).split()) for td in tr.select('td')] if tr else []
    if len(values) < 11:
        raise ValueError('Official intake report columns incomplete; cross-check requires review')
    # Verified official column order: detail, case, address, work, use,
    # description, valuation, received, issued, status, IVR/folder ID.
    if values[10] != str(folder_id):
        raise ValueError('Intake row link and declared folder ID conflict')
    return {'case_number': values[1], 'address': values[2], 'work': values[3],
            'description': values[5], 'status': values[9], 'folder_id': folder_id}


def crosscheck_intake(store, fetcher, record, cutoff, now):
    data = json.loads(record['data']); attrs = data['attributes']
    end = now.astimezone(ZoneInfo('America/Los_Angeles')).date()
    start = datetime.fromisoformat(cutoff).date()
    url = 'https://www.portlandmaps.com/reports/index.cfm?' + urlencode({
        'action': 'co-intakes', 'start_date': start.strftime('%m/%d/%Y'),
        'end_date': end.strftime('%m/%d/%Y'), 'search_field': 'IVR_NUMBER',
        'search_value': record['folder_id']})
    try:
        raw, meta = fetcher.get(url, 'html')
        if meta.get('status') == 'stale_cache':
            raise RuntimeError('Current intake cross-check unavailable; preserve previous evidence')
        row = intake_row(raw, record['folder_id'])
        conflicts = []
        gis_address = ' '.join(str(attrs[k]).strip() for k in ['HOUSE', 'DIRECTION', 'PROPSTREET', 'STREETTYPE'] if attrs[k])
        if row:
            report_address = row['address'].split(',')[0].strip()
            if report_address.upper() != gis_address.upper():
                conflicts.append('Official GIS address and intake-report address differ; location requires reconciliation')
            if row['status'].casefold() != attrs['STATUS'].casefold():
                conflicts.append('Official status differs between GIS and intake report')
        facts = {'status': 'conflict' if conflicts else 'matched_row' if row else 'not_listed',
                 'row': row, 'conflicts': conflicts}
        version, _ = store.source_version(url, raw, 'commercial-intake-crosscheck/1', facts, 'filings', meta['fetched_at'])
        passage = store.passage(version, f'intake-row:FOLDERKEY={record["folder_id"]}',
                                json.dumps(row if row else {'query_folder_id': record['folder_id'], 'matching_rows': 0}, sort_keys=True))
        old = data.get('intake_crosscheck', {}).get('facts')
        if old != facts or not data.get('intake_crosscheck', {}).get('passage_id'):
            data['intake_crosscheck'] = {'facts': facts, 'source_url': url,
                                        'source_version': version, 'passage_id': passage}
            state = 'hold' if conflicts or data['assessment']['status'] == 'hold_conflict' else 'needs_review'
            store.db.execute('UPDATE development_records SET state=?,data=? WHERE id=?', (state, json.dumps(data, sort_keys=True), record['id']))
            store.db.execute('INSERT OR IGNORE INTO development_versions VALUES(?,?,?,?,?,?)',
                (stable_id('development-crosscheck', record['id'], record['content_hash'], facts),
                 record['id'], version, passage, record['content_hash'], json.dumps(data, sort_keys=True)))
            # Repair the missing-row passage reference from the initial local
            # baseline; the source bytes and extracted facts remain unchanged.
            store.db.execute('UPDATE development_versions SET passage_id=? WHERE id=? AND source_version=?',
                (passage, stable_id('development-crosscheck', record['id'], record['content_hash'], facts), version))
        store.commit()
        return facts['status']
    except (ValueError, RuntimeError, OSError) as exc:
        store.failed_retrieval('filings', url, exc)
        return 'unavailable'


def scan(store, fetcher, source, config, output, now=None):
    cfg = profile(config)
    now = now or datetime.now(timezone.utc)
    cutoff = (now.astimezone(ZoneInfo('America/Los_Angeles')).date() - timedelta(days=cfg['lookback_days'])).isoformat()
    base = safe_url(source['url'], ['www.portlandmaps.com']) + '/query?'
    where = f"CREATEDATE >= TIMESTAMP '{cutoff} 00:00:00'"
    count_url = base + urlencode({'f': 'json', 'where': where, 'returnCountOnly': 'true'})
    entries, seen, errors, pages, expected = [], set(), [], 0, None
    terminal = False
    try:
        obj, raw, meta = fetch_json(fetcher, count_url)
        expected = obj.get('count')
        if type(expected) is not int or expected < 0:
            raise ValueError('Official inventory count missing')
        for page in range(cfg['max_pages']):
            url = base + urlencode({'f': 'json', 'where': where, 'outFields': FIELDS,
                  'outSR': 4326, 'resultRecordCount': cfg['page_size'],
                  'resultOffset': page * cfg['page_size'], 'orderByFields': 'OBJECTID ASC'})
            obj, raw, meta = fetch_json(fetcher, url)
            features = obj.get('features')
            if not isinstance(features, list) or obj.get('spatialReference', {}).get('wkid') != 4326:
                raise ValueError('Incomplete permit fields or unverified geometry projection')
            parsed = []
            for feature in features:
                attrs = feature.get('attributes') if isinstance(feature, dict) else None
                if not isinstance(attrs, dict) or not set(FIELDS.split(',')).issubset(attrs):
                    raise ValueError('Expected official permit fields missing')
                key = attrs['FOLDERKEY']
                if type(key) is not int or key <= 0 or key in seen:
                    raise ValueError('Repeated or missing official permit identity; inventory needs review')
                link = safe_url(attrs['PORTLAND_MAPS_URL'], ['www.portlandmaps.com'])
                if link != f'https://www.portlandmaps.com/detail/permit/{key}_did/':
                    raise ValueError('Permit detail identifier conflicts with official folder ID')
                seen.add(key)
                parsed.append({'attributes': attrs, 'geometry': feature.get('geometry'),
                               'assessment': classify(attrs, cfg['minimum_stories'])})
            version, _ = store.source_version(url, raw, PARSER,
                sorted([{k: v for k, v in semantic_record(r).items() if k != 'assessment'} for r in parsed],
                       key=lambda r: r['attributes']['FOLDERKEY']),
                'filings', meta['fetched_at'])
            for row in parsed:
                attrs = row['attributes']; key = attrs['FOLDERKEY']
                ident = stable_id('development-permit', source['id'], key)
                meaningful = {'attributes': attrs, 'geometry': row['geometry'], 'assessment': row['assessment']}
                content_hash = hashlib.sha256(json.dumps(semantic_record(meaningful), sort_keys=True).encode()).hexdigest()
                old = store.db.execute('SELECT * FROM development_records WHERE id=?', (ident,)).fetchone()
                if not old or old['content_hash'] != content_hash:
                    passage = store.passage(version, f'FOLDERKEY={key}', json.dumps(attrs, sort_keys=True))
                    state = 'hold' if row['assessment']['status'] == 'hold_conflict' else 'needs_review'
                    data = {**meaningful, 'first_observed_at': json.loads(old['data'])['first_observed_at'] if old else now.isoformat(),
                            'last_material_change_at': now.isoformat(), 'change_kind': 'changed_record' if old else 'newly_discovered_record',
                            'implementation_status': 'unknown', 'source_organization': 'City of Portland',
                            'deciding_jurisdiction': 'not inferred from postal CITY/COUNTY fields'}
                    store.upsert('development_records', id=ident, source_id=source['id'], folder_id=key,
                                 source_url=attrs['PORTLAND_MAPS_URL'], source_version=version,
                                 passage_id=passage, content_hash=content_hash, state=state, data=data)
                    store.db.execute('INSERT OR IGNORE INTO development_versions VALUES(?,?,?,?,?,?)',
                        (stable_id('development-version', ident, content_hash, version), ident, version,
                         passage, content_hash, json.dumps(data, sort_keys=True)))
                entries.append(ident)
            store.commit(); pages += 1
            if not obj.get('exceededTransferLimit'):
                terminal = True; break
            if not features:
                raise ValueError('Pagination reports more records but returned an empty page')
        if terminal:
            end, _, _ = fetch_json(fetcher, count_url)
            if end.get('count') != expected:
                errors.append('Inventory count changed during collection; reconcile on next scan')
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        errors.append(str(exc)); store.failed_retrieval('filings', source['url'], exc)
    complete = terminal and not errors and len(entries) == expected
    coverage = {'source_id': source['id'], 'expected_records': expected, 'processed_records': len(entries),
                'pages': pages, 'complete': complete, 'errors': errors, 'created_since': cutoff,
                'scope': 'Commercial Construction GIS layer within creation-date lookback; not all city development',
                'status': 'processed' if complete else 'partial', 'checked_at': now.isoformat()}
    if terminal and not complete and not errors:
        coverage['errors'].append('Returned records do not reconcile with official count')
    coverage['screening_counts'] = dict(Counter(json.loads(r['data'])['assessment']['status']
        for r in store.rows('development_records') if r['id'] in entries))
    crosschecks = Counter()
    if cfg.get('verify_intake_reports'):
        candidates = [r for r in store.rows('development_records') if r['id'] in entries
                      and json.loads(r['data'])['assessment']['status'] in {'matches_filter', 'hold_conflict'}]
        for record in candidates[:10]:
            crosschecks[crosscheck_intake(store, fetcher, record, cutoff, now)] += 1
        crosschecks['pending_budget'] = max(0, len(candidates) - 10)
    coverage['intake_crosschecks'] = dict(crosschecks)
    coverage['reconciliation_status'] = 'needs_review' if any(crosschecks[k] for k in ['conflict', 'not_listed', 'unavailable', 'pending_budget']) or coverage['screening_counts'].get('hold_conflict') else 'inventory_reconciled'
    # A fetch outage must not replace a successful draft with an empty result.
    if entries or (complete and expected == 0):
        render(store, source['id'], coverage, output, set(entries))
    run_id = stable_id('development-run', source['id'], now.isoformat())
    store.upsert('pipeline_runs', id=run_id, pipeline='filings', started_at=now.isoformat(),
                 ingestion_status='success' if complete else 'partial' if entries else 'failed',
                 parsing_status='success' if entries or complete else 'failed',
                 reconciliation_status=coverage['reconciliation_status'] if complete else 'partial',
                 generation_status='private_draft' if entries or complete else 'preserved_previous',
                 editorial_status='needs_review', data=coverage)
    store.upsert('inventory_scans', id=stable_id('development-inventory', run_id), pipeline_run_id=run_id,
                 source_url=source['url'], complete=int(complete), pages=pages, expected_count=expected,
                 parsed_count=len(entries), data=coverage)
    if complete:
        store.db.execute('INSERT OR REPLACE INTO collection_checkpoints VALUES(?,?,?)',
            ('developments:' + source['id'], now.isoformat(), json.dumps(coverage, sort_keys=True)))
    store.commit()
    target = Path(output); target.mkdir(parents=True, exist_ok=True)
    (target / 'collection-status.json').write_text(json.dumps(coverage, indent=2))
    return coverage


def render(store, source_id, coverage, output, observed_ids):
    records = [r for r in store.rows('development_records') if r['source_id'] == source_id]
    lines = ['# Portland mixed-use development watch', '',
             'PRIVATE REVIEW DRAFT — evidence screening; no editorial approval or public release.', '',
             'Collection snapshot: ' + datetime.fromisoformat(coverage['checked_at']).astimezone(ZoneInfo('America/Los_Angeles')).isoformat() + ' (America/Los_Angeles).', '',
             f"Collection: {coverage['processed_records']} of {coverage['expected_records']} records; "
             f"{'complete within stated scope' if coverage['complete'] else 'PARTIAL'}. {coverage['scope']}.", '',
             'Initial discoveries are a baseline, not proof of new activity today. Missing records are not withdrawals.', '']
    for record in sorted(records, key=lambda r: r['folder_id'], reverse=True):
        data = json.loads(record['data']); a = data['attributes']; assessment = data['assessment']
        if assessment['status'] not in {'matches_filter', 'hold_conflict'}:
            continue
        address = ' '.join(str(a[k]).strip() for k in ['HOUSE', 'DIRECTION', 'PROPSTREET', 'STREETTYPE'] if a[k])
        lines += [f'## {address}', '']
        if record['id'] not in observed_ids:
            lines += ['Not returned in this bounded scan; prior evidence retained. No withdrawal is inferred.', '']
        if assessment['status'] == 'matches_filter':
            lines += [f"The official new-construction permit record describes a {assessment['stories']:g}-story building "
                      'combining residences and commercial space.']
            units = assessment['housing_units_reported']
            if type(units) in {int, float} and units > 0:
                lines += [f'The permit field reports {units:g} new housing units.']
        else:
            lines += ['HELD FOR REVIEW: ' + '; '.join(assessment['reasons']) + '.',
                      f"Story counts: description {assessment['height_in_description']}; numeric field {assessment['height_numeric_field']}."]
        crosscheck = data.get('intake_crosscheck')
        if crosscheck:
            facts = crosscheck['facts']
            if facts['conflicts']:
                lines += ['HELD FOR REVIEW: ' + '; '.join(facts['conflicts']) + '.',
                          'GIS address above; intake-report address: ' + facts['row']['address'] + '.',
                          '[Official intake cross-check](' + crosscheck['source_url'] + ').']
            elif facts['status'] == 'not_listed':
                lines += ['No matching intake-report row found in this query; no withdrawal or denial is inferred.']
        lines += [f"Official status: **{a['STATUS']}**. Application: `{a['APPLICATION']}`.",
                  f"[Official permit]({record['source_url']}). Supporting passage: `{record['passage_id']}`.",
                  'Last material observation: ' + datetime.fromisoformat(data['last_material_change_at']).astimezone(ZoneInfo('America/Los_Angeles')).isoformat() + '.',
                  'Construction, completion and business opening are unverified.', '']
    lines += ['Other missing-height/use candidates remain in the complete structured review file.',
              'The latest collection attempt, including outages, is recorded in collection-status.json.', '']
    target = Path(output); target.mkdir(parents=True, exist_ok=True)
    article = '\n'.join(lines)
    (target / 'watch.md').write_text(article)
    links = ''.join('<li><a href="' + html.escape(r['source_url'], quote=True) + '">Official permit ' + str(r['folder_id']) + '</a></li>'
                    for r in records if json.loads(r['data'])['assessment']['status'] in {'matches_filter', 'hold_conflict'})
    (target / 'watch.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Private development watch</title><body><pre style="white-space:pre-wrap">' + html.escape(article) + '</pre><h2>Source links</h2><ul>' + links + '</ul></body></html>')
    (target / 'records.json').write_text(json.dumps({'coverage': coverage, 'records': records}, indent=2))
