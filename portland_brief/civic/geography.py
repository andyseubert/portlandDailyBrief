"""Official independent boundary layers and evidence-backed development stages."""
import json
import re
from urllib.parse import urlencode, urljoin
from shapely.geometry import shape, Point

from ..records import safe_url
from .parsers import soup_main, text, page_info, field
from .store import stable_id

STAGES = {'pre_application', 'application', 'proposal', 'approval', 'denial', 'permit', 'construction', 'opening', 'unknown'}


def import_boundaries(store, data, kind, url, expected_count):
    if kind not in {'neighborhood', 'council_district'}:
        raise ValueError('Boundary kinds must remain distinct')
    payload = json.loads(data)
    features = payload.get('features', [])
    complete = not payload.get('exceededTransferLimit') and len(features) == expected_count and expected_count > 0
    if not complete:
        raise ValueError('Boundary response is incomplete; preserve previous dataset')
    geometry_issues = []
    for index, feature in enumerate(features):
        if not shape(feature['geometry']).is_valid:
            geometry_issues.append({'feature_index': index, 'issue': 'invalid_official_geometry_requires_review'})
        props = feature['properties']
        if kind == 'neighborhood' and not props.get('NAME'):
            raise ValueError('Missing official neighborhood name')
        if kind == 'council_district' and props.get('DISTRICT') is None:
            raise ValueError('Missing official district field')
    version, changed = store.source_version(url, data, 'arcgis-geojson-v1', payload, 'filings')
    store.upsert('boundary_datasets', id=stable_id('boundary', kind, version), kind=kind, source_url=url, version_id=version, crs='EPSG:4326', complete=1, data={'feature_collection': payload, 'expected_count': expected_count, 'geometry_issues': geometry_issues})
    store.commit()
    return {'kind': kind, 'features': len(features), 'source_version': version, 'changed': changed, 'geometry_issues': geometry_issues}


def fetch_boundaries(store, fetcher, source):
    base = source['url'] + '/query?'
    count_url = base + urlencode({'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'})
    count_data, meta = fetcher.get(count_url)
    if meta.get('status') == 'stale_cache':
        raise RuntimeError('Boundary count request failed')
    expected = json.loads(count_data)['count']
    query_url = base + urlencode({'where': '1=1', 'outFields': '*', 'returnGeometry': 'true', 'outSR': '4326', 'f': 'geojson'})
    data, meta = fetcher.get(query_url)
    if meta.get('status') == 'stale_cache':
        raise RuntimeError('Boundary geometry request failed')
    return import_boundaries(store, data, 'neighborhood' if source['id'] == 'neighborhood-boundaries' else 'council_district', query_url, expected)


def tag_location(store, entity_type, entity_id, geometry=None, source_evidence=None, relation='physically_located', explicit_areas=None):
    if relation not in {'physically_located', 'explicitly_affected', 'citywide', 'countywide', 'unknown'}:
        raise ValueError('Unknown impact relation')
    if relation == 'explicitly_affected' and (not explicit_areas or not source_evidence):
        raise ValueError('Wider impact needs explicit evidence and named areas')
    if geometry is not None and not source_evidence:
        raise ValueError('Coordinates need location evidence, not an incidental mention')
    tags = []
    if geometry is not None:
        location = shape(geometry)
        if not location.is_valid:
            raise ValueError('Invalid source location')
        datasets = {}
        for row in store.rows('boundary_datasets'):
            if row['complete']:
                datasets[row['kind']] = row
        for kind, dataset in datasets.items():
            matches = []
            for feature in json.loads(dataset['data'])['feature_collection']['features']:
                boundary = shape(feature['geometry'])
                if not boundary.is_valid:
                    continue
                if boundary.intersects(location):
                    props = feature['properties']
                    name = props['NAME'] if kind == 'neighborhood' else str(props['DISTRICT'])
                    matches.append({'area_id': name, 'boundary_touch': boundary.boundary.intersects(location)})
            if json.loads(dataset['data']).get('geometry_issues'):
                tags.append({'area_kind': kind, 'area_id': None, 'relation': 'unknown', 'method': 'invalid_official_geometry_held_for_review', 'evidence': dataset['version_id'], 'data': {'partial': True}})
            for match in matches:
                tags.append({'area_kind': kind, 'area_id': match['area_id'], 'relation': relation, 'method': 'official_geometry_intersection', 'evidence': json.dumps({'location_evidence': source_evidence, 'boundary_version': dataset['version_id']}), 'data': {'source_geometry': geometry, 'ambiguous': match['boundary_touch'] or (location.geom_type == 'Point' and len(matches) > 1), 'multiple_affected': len(matches) > 1}})
    elif explicit_areas:
        tags = [{'area_kind': 'neighborhood', 'area_id': area, 'relation': relation, 'method': 'explicit_official_neighborhood_field' if relation == 'physically_located' else 'explicit_official_impact_statement', 'evidence': source_evidence, 'data': {'geometry_unverified': True}} for area in explicit_areas]
    if not tags:
        tags = [{'area_kind': 'coverage', 'area_id': None, 'relation': relation if relation in {'citywide', 'countywide'} else 'unknown', 'method': 'no_verified_location', 'evidence': source_evidence or '', 'data': {}}]
    # Revised locations replace current tags; source versions preserve prior evidence.
    store.db.execute('DELETE FROM location_tags WHERE entity_type=? AND entity_id=?', (entity_type, entity_id))
    for tag in tags:
        store.upsert('location_tags', id=stable_id('location-tag', entity_type, entity_id, tag['area_kind'], tag['area_id']), entity_type=entity_type, entity_id=entity_id, **tag)
    store.commit()
    return tags


def import_filing(store, row, raw, url):
    if row.get('stage') not in STAGES or not row.get('case_identifier') or not row.get('evidence_text'):
        raise ValueError('Filing requires stable official case identifier, explicit stage and supporting passage')
    safe_url(url)
    jurisdiction = row.get('jurisdiction_id', 'portland')
    # Stages are events, not an overwritable current outcome. A proposal cannot become construction.
    version, changed = store.source_version(url, raw, 'structured-filing-v3', row, 'filings')
    ident = stable_id('filing-event', jurisdiction, row['case_identifier'], row['stage'], row.get('official_event_identifier') or url)
    passage = store.passage(version, row.get('locator', 'notice'), row['evidence_text'])
    store.upsert('filing_events', id=ident, jurisdiction_id=jurisdiction, case_identifier=row['case_identifier'], stage=row['stage'], source_url=url, version_id=version, data={**row, 'passage_id': passage})
    store.fact('filing_event', ident, 'notice', row['evidence_text'], passage)
    tags = tag_location(store, 'filing_event', ident, row.get('geometry'), passage, row.get('impact_relation', 'physically_located'), row.get('explicit_affected_neighborhoods'))
    store.commit()
    return {'filing_id': ident, 'stage': row['stage'], 'changed': changed, 'tags': tags}


def notice_links(data, url):
    result = []
    for link in soup_main(data).select('h2 a[href]'):
        target = urljoin(url, link['href'])
        if '/news/' in target and target != url:
            result.append({'url': safe_url(target, ['www.portland.gov']), 'title': text(link)})
    if not result:
        raise ValueError('No official notice cards parsed')
    return result, page_info(data, url)


def parse_notice(data, url):
    main = soup_main(data)
    title = text(main.select_one('h1'))
    body = main.select_one('.field--name-field-body-content')
    summary = field(main, name='field-summary')
    neighborhood = field(main, name='field-neighborhood')
    published = main.select_one('.field--name-field-published-on time[datetime]')
    passage = ' '.join(x for x in [title, summary, text(body), 'Official neighborhood: ' + neighborhood if neighborhood else ''] if x)
    match = re.search(r'\b(?:LU|LUR|PR)\s*\d{2,4}\s*[-–]\s*\d{5,8}(?:\s*[-–]?\s*[A-Z0-9-]+)?', title + ' ' + passage, re.I)
    if not match:
        raise ValueError('Stable land-use case identifier not found; hold notice for manual extraction')
    identifier = re.sub(r'\s+', '', match[0]).upper()
    lower = title.casefold()
    stage = 'approval' if re.search(r'approval|approved', lower) else 'denial' if re.search(r'denial|denied', lower) else 'proposal' if 'proposal' in lower else 'application' if 'application' in lower else 'unknown'
    # Decisions are not always approvals. Preserve unknown pending explicit disposition review.
    address = re.search(r'\b\d{1,6}\s+[NESW]{1,2}\s+.+', summary, re.I)
    # Extract the actual labeled instruction, without swallowing unrelated links.
    deadlines = {}
    for node in body.select('p, li') if body else []:
        value = text(node)
        for label in ['Comment Deadline', 'Appeal Deadline']:
            match = re.search(re.escape(label) + r'\s*:\s*(.+)', value, re.I)
            if match:
                deadlines[label] = match[1].strip()
    links = [{'url': safe_url(urljoin(url, a['href']), ['www.portland.gov']), 'label': text(a)} for a in body.select('a[href]') if '.pdf' in a['href'].lower() or ('/documents/' in a['href'] and a['href'].rstrip('/').endswith('/download'))] if body else []
    return {'case_identifier': identifier, 'stage': stage, 'title': title, 'evidence_text': passage, 'locator': '.field--name-field-body-content + official summary/neighborhood fields', 'geometry': None, 'address': address[0] if address else None, 'published_at_raw': published['datetime'] if published else None, 'comment_deadline_raw': deadlines.get('Comment Deadline'), 'appeal_deadline_raw': deadlines.get('Appeal Deadline'), 'attachments': links, 'explicit_affected_neighborhoods': neighborhood.split('; ') if neighborhood else None, 'impact_relation': 'physically_located' if neighborhood and address else 'unknown', 'extraction_notes': 'Neighborhood is the explicit official field; spatial match not performed without verified geometry. Generic decision headings do not establish approval.'}
