"""Independent official-record ingestion. Parse failures never mean deletions."""
from collections import defaultdict
from datetime import datetime
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import parsers
from .procedures import validate_vote
from .store import IdentifierConflict, stable_id, stamp


def bootstrap(store, root):
    for ident, name, kind in [('portland', 'Portland', 'city'), ('multnomah', 'Multnomah County', 'county')]:
        store.upsert('jurisdictions', id=ident, name=name, kind=kind, data={})
    for ident, jurisdiction, name in [('portland:council', 'portland', 'Portland City Council'), ('multnomah:board', 'multnomah', 'Multnomah County Board')]:
        store.upsert('bodies', id=ident, jurisdiction_id=jurisdiction, name=name, kind='council' if jurisdiction == 'portland' else 'board', data={})
    for rule in json.loads((root / 'config/procedures.json').read_text()):
        store.upsert('procedure_versions', id=rule['id'], body_id=rule['body_id'], effective_from=rule['effective_from'], effective_to=rule['effective_to'], data=rule)
    store.commit()


def person(store, name, url=None):
    # Official full names only. Never resolve ambiguous surname matches automatically.
    raw_name = name
    name = re.sub(r'^(?:Council Vice President|Council President|Councilor|Mayor)\s+', '', name).strip()
    pid = stable_id('person', name.casefold().strip())
    old = store.db.execute('SELECT official_url FROM people WHERE id=?', (pid,)).fetchone()
    store.upsert('people', id=pid, name=name.strip(), official_url=url or (old[0] if old else None), data={})
    if raw_name != name:
        store.db.execute('INSERT OR REPLACE INTO person_aliases VALUES(?,?,?)', (raw_name, pid, 'exact_official_role_prefix_removed'))
    return pid


def resolve_votes(store, votes, roster):
    names = {name: person(store, name) for name in roster}
    result = []
    for vote in votes:
        raw = vote.get('raw_name', vote.get('person_name', ''))
        matches = [pid for name, pid in names.items() if name.casefold() == raw.casefold() or name.casefold().endswith(' ' + raw.casefold())]
        result.append({**vote, 'person_id': matches[0] if len(matches) == 1 else None, 'identity_method': 'unique_official_session_name' if len(matches) == 1 else 'unresolved'})
    return result


def record_vote(store, meeting_id, appearance_id, motion_id, matter_ids, origin, identity, event, passage_id, when, body):
    vid = stable_id('vote', origin, identity)
    rules = store.procedure(body, when) if body and when else None
    membership = store.membership(body, when) if body and when else []
    result = validate_vote(event, rules, membership or None)
    version = stable_id('vote-version', vid, event, passage_id)
    store.upsert('vote_events', id=vid, meeting_id=meeting_id, appearance_id=appearance_id, motion_id=motion_id, event_kind=event['event_kind'], origin=origin, review_status=result['review_status'], data={**event, 'validation': result, 'passage_id': passage_id, 'current_version': version})
    store.upsert('vote_event_versions', id=version, vote_event_id=vid, source_passage_id=passage_id, data={**event, 'validation': result})
    # Current individual positions replace current positions, but prior complete event versions persist.
    store.db.execute('DELETE FROM individual_votes WHERE vote_event_id=?', (vid,))
    for index, vote in enumerate(event.get('individual_votes', [])):
        store.upsert('individual_votes', id=stable_id('individual-vote', vid, index), vote_event_id=vid, person_id=vote.get('person_id'), position=vote['position'], data=vote)
    for mid in matter_ids:
        store.db.execute('INSERT OR REPLACE INTO vote_event_matters VALUES(?,?,1,NULL)', (vid, mid))
    return vid


def ingest_rosters(store, data, url, observed_on):
    rows = parsers.committees(data, url)
    version, changed = store.source_version(url, data, parsers.PARSER_VERSION, [{k: v for k, v in r.items() if k != 'passage'} for r in rows])
    for row in rows:
        bid = stable_id('body', 'portland', row['url'])
        store.upsert('bodies', id=bid, jurisdiction_id='portland', name=row['name'], kind=row['kind'], data={'url': row['url'], 'functions': row['functions']})
        body_version_id = stable_id('body-version', bid, row['name'], row['functions'], row['members'])
        if not store.db.execute('SELECT 1 FROM body_versions WHERE id=?', (body_version_id,)).fetchone():
            store.db.execute('UPDATE body_versions SET effective_to=? WHERE body_id=? AND effective_to IS NULL', (observed_on, bid))
            store.upsert('body_versions', id=body_version_id, body_id=bid, effective_from=observed_on, effective_to=None, source_version_id=version, data={'name': row['name'], 'functions': row['functions'], 'effective_date_method': 'observed_from_only'})
        passage = store.passage(version, row['locator'], row['passage'])
        current = {person(store, member['name']): member for member in row['members']}
        for old in store.membership(bid, observed_on):
            if old['person_id'] not in current or json.loads(old['data']).get('role') != current[old['person_id']]['role']:
                store.db.execute('UPDATE memberships SET effective_to=? WHERE id=?', (observed_on, old['id']))
        for pid, member in current.items():
            existing = [m for m in store.membership(bid, observed_on) if m['person_id'] == pid]
            if not existing:
                store.upsert('memberships', id=stable_id('membership', bid, pid, observed_on, member['role']), body_id=bid, person_id=pid, effective_from=observed_on, effective_to=None, data={'role': member['role'], 'passage_id': passage, 'effective_date_method': 'observed_from_only'})
        # The whole-committee roster verifies the current council membership, not historical terms.
        if row['kind'] == 'committee_of_whole':
            for pid in current:
                if not any(m['person_id'] == pid for m in store.membership('portland:council', observed_on)):
                    store.upsert('memberships', id=stable_id('membership', 'portland:council', pid, observed_on), body_id='portland:council', person_id=pid, effective_from=observed_on, effective_to=None, data={'passage_id': passage, 'effective_date_method': 'observed_from_only'})
    store.commit()
    return {'institutions': len(rows), 'source_version': version, 'changed': changed}


def ingest_agenda(store, data, url, body='portland:council'):
    bodyrow = store.db.execute('SELECT * FROM bodies WHERE id=?', (body,)).fetchone()
    if not bodyrow:
        raise ValueError('Unknown governing body')
    sessions = parsers.agenda(data, url, bodyrow['kind'])
    meaningful = [{k: v for k, v in s.items() if k != 'passage'} for s in sessions]
    version, changed = store.source_version(url, data, parsers.PARSER_VERSION, meaningful)
    mids = []
    for session in sessions:
        meeting_id = stable_id('meeting', body, url, session['official_relation_id'])
        existing = store.db.execute('SELECT data FROM meetings WHERE id=?', (meeting_id,)).fetchone()
        metadata = {**(json.loads(existing[0]) if existing else {}), **session, 'source_version': version, 'document_based': True}
        metadata.pop('items', None)
        store.upsert('meetings', id=meeting_id, body_id=body, source_url=url, starts_at=session['starts_at'], kind='regular' if bodyrow['kind'] == 'council' else bodyrow['kind'], coverage_status='processed', data=metadata)
        mids.append(meeting_id)
        sp = store.passage(version, session['locator'] + ':session', session['passage'])
        store.fact('meeting', meeting_id, 'starts_at', session['starts_at'], sp)
        for item in session['items']:
            mid = None
            if item['document_url']:
                mid = store.matter(bodyrow['jurisdiction_id'], item['document_number'], item['document_url'], item['title'], {'introduced_by': item['introduced_by'], 'responsible_department': item['responsible_department']})
                if item['action_type'] == 'adoption':
                    adopted_day = datetime.fromisoformat(session['starts_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
                    store.matter(bodyrow['jurisdiction_id'], item['document_number'], item['document_url'], item['title'], {'adoption_date': adopted_day, 'adoption_evidence': {'agenda_url': url, 'source_version': version, 'official_disposition': item['official_disposition']}})
                for kind in ['ordinance', 'resolution']:
                    if item[kind + '_number']:
                        store.adopted_identifier(bodyrow['jurisdiction_id'], mid, kind, item[kind + '_number'])
                store.upsert('documents', id=stable_id('document', item['document_url']), matter_id=mid, source_url=item['document_url'], kind=item['event_kind'], data={'official_number': item['document_number']})
            aid = stable_id('appearance', meeting_id, item['official_relation_id'] or item['agenda_number'])
            passage = store.passage(version, item['locator'], item['passage'])
            structured = {**item, 'source_version': version, 'passage_id': passage}
            store.upsert('appearances', id=aid, meeting_id=meeting_id, matter_id=mid, agenda_number=item['agenda_number'], official_disposition=item['official_disposition'], action_type=item['action_type'], data=structured)
            for predicate in ['title', 'official_disposition', 'notes', 'responsible_department', 'introduced_by', 'neighborhood_fields', 'agenda_note', 'registration_urls']:
                if item[predicate]:
                    store.fact('appearance', aid, predicate, item[predicate], passage)
            for index, motion in enumerate(item['motions']):
                moid = stable_id('motion', aid, index)
                store.upsert('motions', id=moid, appearance_id=aid, kind=motion['kind'], official_text=motion['official_text'], data={**motion, 'passage_id': passage})
                if motion['kind'] == 'amendment':
                    store.upsert('amendments', id=stable_id('amendment', moid), motion_id=moid, matter_id=mid, data={'passage_id': passage})
                if motion['declared_tallies']:
                    event = {**motion, 'event_kind': 'ordinance_amendment' if motion['kind'] == 'amendment' else 'procedural_motion', 'individual_votes': resolve_votes(store, motion['individual_votes'], session['attendance']), 'named_votes_complete': True, 'published_outcome': 'passed' if 'Motion passed' in motion['official_text'] else 'failed' if 'Motion failed' in motion['official_text'] else None}
                    record_vote(store, meeting_id, aid, moid, [mid] if mid else [], 'agenda_motion', moid, event, passage, session['starts_at'], body)
            if item['declared_tallies']:
                event = {**item, 'individual_votes': resolve_votes(store, item['individual_votes'], session['attendance']), 'named_votes_complete': True, 'published_outcome': 'passed' if item['action_type'] in {'adoption', 'report_acceptance'} else 'failed' if item['action_type'] == 'rejection' else None}
                if item['action_type'] not in {'adoption', 'rejection', 'report_acceptance'}:
                    event['event_kind'] = 'procedural_motion'
                record_vote(store, meeting_id, aid, None, [mid] if mid else [], 'agenda_action', aid, event, passage, session['starts_at'], body)
    store.commit()
    return {'meeting_ids': mids, 'appearances': sum(len(s['items']) for s in sessions), 'source_version': version, 'changed': changed}


PARSERS = {'calendar': parsers.calendar, 'document_index': parsers.document_index, 'vote_register': parsers.vote_register, 'granicus': parsers.county_archive}


def ingest_index_page(store, data, url, kind, row_errors=None):
    rows = PARSERS[kind](data, url)
    page = parsers.page_info(data, url)
    version, changed = store.source_version(url, data, parsers.PARSER_VERSION, {'rows': [{k: v for k, v in r.items() if k != 'passage'} for r in rows], 'pagination': page})
    register = defaultdict(list)
    for row in rows:
        pid = store.passage(version, row['locator'], row['passage'])
        if kind in {'calendar', 'granicus'}:
            county = kind == 'granicus'
            mid = stable_id('meeting', 'multnomah' if county else 'portland', row.get('official_identifier') or row['url'])
            old = store.db.execute('SELECT data FROM meetings WHERE id=?', (mid,)).fetchone()
            metadata = {**(json.loads(old[0]) if old else {}), **row, 'source_version': version, 'passage_id': pid}
            store.upsert('meetings', id=mid, body_id='multnomah:board' if county else None, source_url=row['agenda_url'] if county else row['url'], starts_at=row['starts_at'], kind=row['kind'], coverage_status=(store.db.execute('SELECT coverage_status FROM meetings WHERE id=?', (mid,)).fetchone() or ['discovered'])[0], data=metadata)
            store.fact('meeting', mid, 'notice', row['title'], pid)
            if row['starts_at']:
                store.fact('meeting', mid, 'starts_at', row['starts_at'], pid)
        elif kind == 'document_index':
            try:
                mid = store.matter('portland', row['document_number'], row['url'], row['title'], {'index_disposition': row['official_disposition'], 'index_final_disposition_date': row['final_disposition_date'], 'index_version': version, 'introduced_by': row['introduced_by']})
            except IdentifierConflict as exc:
                if row_errors is None:
                    raise
                row_errors.append({'url': row['url'], 'document_number': row['document_number'], 'source_version': version, 'passage_id': pid, 'error': str(exc)})
                continue
            store.fact('matter', mid, 'index_disposition', row['official_disposition'], pid)
            store.upsert('documents', id=stable_id('document', row['url']), matter_id=mid, source_url=row['url'], kind=row['document_type'], data={'source_version': version, 'official_number': row['document_number'], 'index_only': True})
        else:
            mid = store.matter('portland', row['document_number'], row['document_url'], row['title'], {})
            voter = person(store, row['person_name'], row['person_url'])
            store.upsert('register_observations', id=stable_id('register-observation', mid, row['date'], voter), matter_id=mid, publication_date=row['date'] or 'unknown', person_id=voter, position=row['position'], passage_id=pid, data={**row, 'person_id': voter, 'passage_id': pid})
            register[(mid, row['date'])].append({**row, 'person_id': voter, 'passage_id': pid})
    for (mid, day), votes in register.items():
        votes = [json.loads(r[0]) for r in store.db.execute('SELECT data FROM register_observations WHERE matter_id=? AND publication_date=?', (mid, day or 'unknown'))]
        # A page often contains only part of a roll call. Declare it partial, never infer absent voters.
        record_vote(store, None, None, None, [mid], 'official_document_register', (mid, day), {'event_kind': 'document_register', 'individual_votes': votes, 'declared_tallies': {}, 'named_votes_complete': False, 'publication_date': day, 'register_partial': True}, votes[0]['passage_id'], day, 'portland:council')
    store.commit()
    return rows, page, version


def scan(store, fetcher, source, max_pages=4, start_url=None):
    url, pages, count, expected, errors = start_url or source['url'], 0, 0, None, []
    row_errors = []
    run_id = stable_id('scan', source['id'], stamp())
    next_url = url
    while next_url and pages < max_pages:
        current = next_url
        try:
            data, meta = fetcher.get(current, 'html')
            if meta.get('status') == 'stale_cache':
                raise RuntimeError('Live request failed; stale cache is not a successful scan: ' + meta.get('error', 'unknown fetch error'))
            rows, page, _ = ingest_index_page(store, data, current, source['type'], row_errors)
            pages += 1
            count += len(rows)
            if expected is None:
                expected = page['expected_count']
            next_url = page['next_url']
        except Exception as exc:
            store.failed_retrieval(source['pipeline'], current, exc)
            errors.append({'url': current, 'error': str(exc)[:500]})
            break
    if next_url is None and expected is not None and count != expected and start_url is None:
        errors.append({'url': url, 'error': f'Inventory count mismatch: parsed {count}, expected {expected}'})
    inventory_complete = not errors and next_url is None and (expected is None or count == expected or start_url is not None)
    errors.extend(row_errors)
    complete = inventory_complete and not row_errors
    result = {'source_id': source['id'], 'pages': pages, 'records': count, 'normalized_records': count - len(row_errors), 'held_records': len(row_errors), 'expected_count': expected, 'pagination_complete': inventory_complete, 'next_url': next_url, 'errors': errors, 'scope': 'continuation' if start_url else 'from_first_page'}
    store.upsert('pipeline_runs', id=run_id, pipeline=source['pipeline'], started_at=stamp(), ingestion_status='partial' if errors else 'succeeded', parsing_status='partial' if errors or not complete else 'succeeded', reconciliation_status='pending', generation_status='not_requested', editorial_status='not_reviewed', data=result)
    store.upsert('inventory_scans', id=run_id, pipeline_run_id=run_id, source_url=source['url'], complete=int(inventory_complete), pages=pages, expected_count=expected, parsed_count=count, data=result)
    store.commit()
    return result


def record_consent(store, meeting_id, included_matters, removed_matters, event, passage_id, when, body):
    if set(included_matters) & set(removed_matters):
        raise ValueError('Removed consent items cannot also be included')
    if removed_matters and not event.get('removal_evidence'):
        raise ValueError('Removal from consent requires explicit evidence')
    vid = record_vote(store, meeting_id, None, None, included_matters, 'agenda_consent', (meeting_id, 'consent', event.get('official_event_identifier')), {**event, 'event_kind': 'consent'}, passage_id, when, body)
    for mid in removed_matters:
        store.db.execute('INSERT OR REPLACE INTO vote_event_matters VALUES(?,?,0,?)', (vid, mid, event['removal_evidence']))
    store.commit()
    return vid


def reconcile_register(store):
    checks = []
    for event in store.rows('vote_events'):
        if event['origin'] != 'agenda_action':
            continue
        metadata = json.loads(event['data'])
        if metadata.get('published_outcome') not in {'passed', 'failed'}:
            continue
        meeting = store.db.execute('SELECT starts_at FROM meetings WHERE id=?', (event['meeting_id'],)).fetchone()
        day = datetime.fromisoformat(meeting[0].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
        mids = [r[0] for r in store.db.execute('SELECT matter_id FROM vote_event_matters WHERE vote_event_id=? AND included=1', (event['id'],))]
        for mid in mids:
            for register in store.db.execute('SELECT v.* FROM vote_events v JOIN vote_event_matters m ON m.vote_event_id=v.id WHERE m.matter_id=? AND v.origin=\'official_document_register\'', (mid,)):
                data = json.loads(register['data'])
                if data.get('publication_date') != day:
                    continue
                a = {v['person_id']: v['position'] for v in metadata['individual_votes'] if v.get('person_id')}
                b = {v['person_id']: v['position'] for v in data['individual_votes'] if v.get('person_id')}
                conflicts = {pid: [a[pid], b[pid]] for pid in a.keys() & b.keys() if a[pid] != b[pid]}
                status = 'conflict_hold' if conflicts else 'partial_agreement' if a.keys() != b.keys() else 'consistent_document_crosscheck'
                check = {'conflicts': conflicts, 'agenda_voters': len(a), 'register_voters': len(b), 'same_event_assumed': False, 'date': day}
                store.upsert('vote_crosschecks', id=stable_id('crosscheck', event['id'], register['id']), agenda_event_id=event['id'], register_event_id=register['id'], status=status, data=check)
                if conflicts:
                    for eid in [event['id'], register['id']]:
                        row = store.db.execute('SELECT data FROM vote_events WHERE id=?', (eid,)).fetchone()
                        value = json.loads(row[0]); value['validation']['issues'] = sorted(set(value['validation']['issues'] + ['register_crosscheck_conflict']))
                        store.db.execute('UPDATE vote_events SET review_status=\'hold\',data=? WHERE id=?', (json.dumps(value), eid))
                checks.append({'status': status, **check})
    store.commit()
    return checks


def ingest_document(store, fetcher, url, attachments=False):
    data, meta = fetcher.get(url, 'html')
    if meta.get('status') == 'stale_cache':
        raise RuntimeError('Live document fetch failed')
    parsed = parsers.document_detail(data, url)
    version, changed = store.source_version(url, data, parsers.PARSER_VERSION, parsed)
    mid = store.matter('portland', parsed['document_number'], url, parsed['title'], {'document_source_version': version})
    for predicate, value in parsed['facts'].items():
        if value:
            passage = store.passage(version, 'document-field:' + predicate, value)
            store.fact('matter', mid, predicate, value, passage)
    results = []
    for attachment in parsed['attachments']:
        store.upsert('documents', id=stable_id('document', attachment['url']), matter_id=mid, source_url=attachment['url'], kind='attachment', data={'label': attachment['label'], 'parent_version': version, 'content_status': 'not_fetched'})
        if attachments:
            try:
                body, status = fetcher.get(attachment['url'], 'binary')
                if status.get('status') == 'stale_cache':
                    raise RuntimeError('Attachment fetch failed')
                import hashlib
                avid, achanged = store.source_version(attachment['url'], body, 'attachment-bytes-v1', {'content_hash': hashlib.sha256(body).hexdigest()})
                store.upsert('documents', id=stable_id('document', attachment['url']), matter_id=mid, source_url=attachment['url'], kind='attachment', data={'label': attachment['label'], 'source_version': avid, 'content_status': 'retrieved_not_text_extracted'})
                results.append({'url': attachment['url'], 'changed': achanged, 'version': avid})
            except Exception as exc:
                store.failed_retrieval('meetings', attachment['url'], exc)
                results.append({'url': attachment['url'], 'error': str(exc)})
    store.commit()
    return {'matter_id': mid, 'source_version': version, 'changed': changed, 'attachments': results, 'attachment_text_extraction': 'not_implemented'}


def inspect_event(store, fetcher, meeting_id):
    meeting = store.db.execute('SELECT * FROM meetings WHERE id=?', (meeting_id,)).fetchone()
    if not meeting or not meeting['source_url'].startswith('https://www.portland.gov/'):
        raise ValueError('Use a discovered official Portland calendar event')
    data, meta = fetcher.get(meeting['source_url'], 'html')
    if meta.get('status') == 'stale_cache':
        raise RuntimeError('Event fetch failed')
    parsed = parsers.event_detail(data, meeting['source_url'])
    meaningful = {k: v for k, v in parsed.items() if k != 'passage'}
    version, changed = store.source_version(meeting['source_url'], data, parsers.PARSER_VERSION, meaningful)
    metadata = {**json.loads(meeting['data']), **meaningful, 'detail_source_version': version}
    store.upsert('meetings', **{**dict(meeting), 'data': metadata})
    passage = store.passage(version, 'event-participation', parsed['passage'])
    for predicate in ['participation_instructions', 'official_links']:
        if parsed[predicate]:
            store.fact('meeting', meeting_id, predicate, parsed[predicate], passage)
    store.commit()
    return {'meeting_id': meeting_id, **meaningful, 'changed': changed, 'processed': False}


def extract_attachment(store, document_id):
    import os
    import subprocess
    import sys
    row = store.db.execute('SELECT * FROM documents WHERE id=? AND kind=\'attachment\'', (document_id,)).fetchone()
    if not row:
        raise ValueError('Unknown attachment')
    metadata = json.loads(row['data'])
    source = store.db.execute('SELECT * FROM source_versions WHERE id=?', (metadata.get('source_version'),)).fetchone()
    if not source:
        raise ValueError('Fetch this attachment first')
    environment = {key: os.environ[key] for key in ['PATH', 'TMPDIR', 'LANG'] if key in os.environ}
    result = subprocess.run([sys.executable, '-m', 'portland_brief.civic.pdf_worker', source['artifact_path']], capture_output=True, text=True, timeout=60, env=environment)
    if result.returncode:
        raise ValueError('PDF extraction failed in isolated worker; retain source for review')
    extracted = json.loads(result.stdout)
    warnings = []
    label_number = re.search(r'Kanal\s+(\d+)', metadata.get('label', ''), re.I)
    first_line = extracted['pages'][0]['text'].splitlines()[0] if extracted['pages'] and extracted['pages'][0]['text'] else ''
    text_number = re.search(r'Kanal Amendment\s+(\d+)', first_line, re.I)
    if label_number and text_number and label_number[1] != text_number[1]:
        warnings.append('attachment_label_text_mismatch_requires_review')
    passage_ids = []
    for page in extracted['pages']:
        if page['text'].strip():
            passage_ids.append(store.passage(source['id'], 'PDF page ' + str(page['page']), page['text'], 'official_attachment'))
    empty_pages = [page['page'] for page in extracted['pages'] if not page['text'].strip()]
    store.upsert('documents', **{**dict(row), 'data': {**metadata, 'content_status': 'text_extracted' if passage_ids else 'scan_requires_review', 'extraction_version': extracted['parser_version'], 'page_passages': passage_ids, 'pages': len(extracted['pages']), 'empty_or_scan_pages': empty_pages, 'review_warnings': warnings}})
    store.commit()
    return {'document_id': document_id, 'pages': len(extracted['pages']), 'passages': passage_ids, 'empty_or_scan_pages': empty_pages, 'ocr': False, 'warnings': warnings}


def ingest_county_agenda(store, data, url):
    parsed = parsers.county_agenda(data, url)
    meeting = store.db.execute('SELECT * FROM meetings WHERE body_id=\'multnomah:board\' AND source_url=?', (url,)).fetchone()
    if not meeting:
        raise ValueError('Discover the county meeting first; do not infer session identity from an agenda title')
    if meeting['kind'] == 'executive_session':
        raise ValueError('Executive sessions receive public notice coverage only')
    version, changed = store.source_version(url, data, parsers.PARSER_VERSION, parsed)
    metadata = {**json.loads(meeting['data']), 'agenda_version': version, 'agenda_processed': True, 'outcomes_processed': False, 'participation_instructions': parsed['participation_instructions'], 'participation_links': parsed['participation_links']}
    store.upsert('meetings', **{**dict(meeting), 'coverage_status': 'partial', 'data': metadata})
    notice = store.passage(version, 'county-participation', ' '.join(parsed['participation_instructions']) + ' ' + json.dumps(parsed['participation_links']))
    store.fact('meeting', meeting['id'], 'participation_instructions', parsed['participation_instructions'], notice)
    for item in parsed['items']:
        mid = None
        if item['attachments']:
            # County document identifiers are not assumed equivalent to Portland numbers.
            # URL fallback remains explicit until a durable official matter number is found.
            mid = store.matter('multnomah', None, item['attachments'][0]['url'], item['title'], {'identity_status': 'official_attachment_url_fallback; cross_meeting_identity_unverified'})
        aid = stable_id('appearance', meeting['id'], item['agenda_number'])
        passage = store.passage(version, item['locator'], item['passage'])
        store.upsert('appearances', id=aid, meeting_id=meeting['id'], matter_id=mid, agenda_number=item['agenda_number'], official_disposition='', action_type='unknown', data={**item, 'source_version': version, 'passage_id': passage, 'outcome_status': 'not_available'})
        store.fact('appearance', aid, 'title', item['title'], passage)
        for attachment in item['attachments']:
            store.upsert('documents', id=stable_id('document', attachment['url']), matter_id=mid, source_url=attachment['url'], kind='attachment', data={'label': attachment['label'], 'content_status': 'not_fetched', 'parent_version': version})
    store.commit()
    return {'meeting_id': meeting['id'], 'agenda_items': len(parsed['items']), 'consent_candidates': sum(x['consent_candidate'] for x in parsed['items']), 'vote_events_created': 0, 'outcomes': 'not_available', 'changed': changed}
