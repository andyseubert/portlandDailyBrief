"""Evidence-first development drafts and explicit, versioned editorial review."""
import html
import json
import math
from pathlib import Path
import re

from .geography import tag_location
from .store import stable_id, stamp

FIELDS = {'description': ('DESCRIPTION', 'Proposal in the permit description'),
          'status': ('STATUS', 'Official permit status'),
          'application': ('APPLICATION', 'Application number')}
EXPLANATION_VERSION = 'development-summary/1'


def fingerprint(record):
    data = json.loads(record['data'])
    return stable_id('development-evidence', record['content_hash'], data.get('intake_crosscheck', {}).get('facts'))


def issues(record):
    data = json.loads(record['data']); result = []
    if data['assessment']['status'] != 'matches_filter':
        result += data['assessment']['reasons'] or ['Mixed-use screening requires more evidence']
    result += data.get('intake_crosscheck', {}).get('facts', {}).get('conflicts', [])
    return result


def validate_claims(store, claims, bundle, current=True):
    if not claims:
        raise ValueError('At least one supported claim is required')
    for claim in claims:
        if set(claim) != {'kind', 'value', 'passage_id'} or claim['kind'] not in FIELDS:
            raise ValueError('Claims must use supported structured permit fields; arbitrary prose is not accepted')
        row = store.db.execute('SELECT p.*,s.url FROM passages p JOIN source_versions s ON s.id=p.version_id WHERE p.id=?', (claim['passage_id'],)).fetchone()
        if not row or bundle.get(row['url']) != row['version_id']:
            raise ValueError('Claim evidence is missing from the reviewed bundle')
        attrs = json.loads(row['text'])
        if not isinstance(claim['value'], str) or not claim['value'].strip() or claim['value'] != attrs.get(FIELDS[claim['kind']][0]):
            raise ValueError('Claim conflicts with its supporting official field')
    if current:
        for url, version in bundle.items():
            row = store.db.execute('SELECT v.meaningful_hash,c.meaningful_hash AS current_hash FROM source_versions v JOIN source_current c ON c.url=v.url WHERE v.id=? AND v.url=?', (version, url)).fetchone()
            if not row or row['meaningful_hash'] != row['current_hash']:
                raise ValueError('Material source change requires renewed review')


def text(claim):
    if claim['kind'] == 'description':
        from .developments import classify
        value = claim['value']
        assessment = classify({'DESCRIPTION': value, 'TYPE': '', 'FOLDERTYPE': 'CO', 'WORK_DESCRIPTION': 'New Construction'})
        if assessment['status'] == 'matches_filter':
            line = f"The permit application describes a proposed {assessment['stories']:g}-story building combining residences and commercial space."
            units = re.findall(r'\b(\d+)[ -]+(?:residential (?:rental )?)?units?\b', value, re.I)
            if len(set(units)) == 1:
                line += f" Its description lists {int(units[0])} housing units."
            return line
    return FIELDS[claim['kind']][1] + ': ' + claim['value']


def generate(store, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=True); reports = []
    for record in store.rows('development_records'):
        data = json.loads(record['data'])
        if data['assessment']['status'] not in {'matches_filter', 'hold_conflict'}:
            continue
        attrs = data['attributes']; rid = stable_id('development-report', record['id'])
        claims = [{'kind': kind, 'value': attrs[field], 'passage_id': record['passage_id']} for kind, (field, _) in FIELDS.items() if attrs.get(field)]
        bundle = {}
        for pid in [record['passage_id'], data.get('intake_crosscheck', {}).get('passage_id')]:
            if pid:
                row = store.db.execute('SELECT s.url,s.id FROM passages p JOIN source_versions s ON s.id=p.version_id WHERE p.id=?', (pid,)).fetchone()
                if row: bundle[row['url']] = row['id']
        research = [r for r in store.rows('development_evidence') if r['record_id'] == record['id']]
        for evidence in research:
            bundle[evidence['source_url']] = evidence['source_version']
        # Related numbers are explicit references, not merged projects or inferred parents.
        related = sorted(set(re.findall(r'\b\d{2}-\d{6}-(?:MT|CO|RS|LU)\b', attrs['DESCRIPTION'], re.I)))
        store.db.execute('DELETE FROM development_relationships WHERE record_id=?', (record['id'],))
        for identifier in related:
            store.upsert('development_relationships', id=stable_id('development-relation', record['id'], identifier), record_id=record['id'], related_identifier=identifier.upper(), kind='explicit_permit_description_reference', passage_id=record['passage_id'], data={'parent_relationship': 'not established', 'target_resolution': 'pending'})
        geom = data.get('geometry'); tags = []
        if geom and type(geom.get('x')) in {int, float} and type(geom.get('y')) in {int, float}:
            pid = store.passage(record['source_version'], f'FOLDERKEY={record["folder_id"]}; geometry EPSG:4326', json.dumps(geom, sort_keys=True))
            tags = tag_location(store, 'development', record['id'], {'type': 'Point', 'coordinates': [geom['x'], geom['y']]}, pid)
        held = issues(record); digest = fingerprint(record)
        vid = stable_id('development-report-version', rid, digest, claims, bundle, EXPLANATION_VERSION)
        old = store.db.execute('SELECT * FROM development_reports WHERE id=?', (rid,)).fetchone()
        # Keep editorial edits across unchanged regeneration.
        old_version = store.db.execute('SELECT data FROM development_report_versions WHERE id=?', (old['current_version'],)).fetchone() if old else None
        unchanged = old_version and json.loads(old_version['data']).get('base_generated_version') == vid
        state = old['state'] if unchanged else 'hold' if held else 'needs_review'
        if not unchanged:
            store.upsert('development_reports', id=rid, record_id=record['id'], current_version=vid, state=state)
            store.db.execute('INSERT OR IGNORE INTO development_report_versions VALUES(?,?,?,?,?,?,?)', (vid, rid, stamp(), digest, json.dumps(bundle, sort_keys=True), json.dumps(claims), json.dumps({'base_generated_version': vid, 'explanation_version': EXPLANATION_VERSION, 'rendered_claims': [text(c) for c in claims], 'issues': held, 'observed_at': data['last_material_change_at'], 'change_kind': data['change_kind']}, sort_keys=True)))
            if old and old['state'] == 'approved':
                store.db.execute('INSERT INTO development_review_audit(report_id,version_id,action,actor,at,review_seconds,note) VALUES(?,?,?,?,?,?,?)', (rid, vid, 'approval_invalidated', 'evidence_monitor', stamp(), 0, 'Material development evidence changed'))
        report = store.db.execute('SELECT * FROM development_reports WHERE id=?', (rid,)).fetchone()
        version = store.db.execute('SELECT * FROM development_report_versions WHERE id=?', (report['current_version'],)).fetchone()
        claims = json.loads(version['claims']); validate_claims(store, claims, json.loads(version['evidence_bundle']), current=False)
        heading = ' '.join(str(attrs[k]).strip() for k in ['HOUSE', 'DIRECTION', 'PROPSTREET', 'STREETTYPE'] if attrs[k])
        target = output / str(record['folder_id']); target.mkdir(exist_ok=True)
        lines = ['# '+heading, '', 'PRIVATE EDITORIAL DRAFT — '+report['state']+'.', '', 'Baseline observation; not evidence of newly proposed activity today.', '']
        sentences = json.loads(version['data'])['rendered_claims']
        for claim, sentence in zip(claims, sentences):
            lines += [sentence, '', f'[Official permit]({record["source_url"]}) · Passage `{claim["passage_id"]}`', '']
        lines += ['Construction, completion and opening are unverified.', '', '## Review holds', '', *held, '', '## Source reconciliation', '', json.dumps(data.get('intake_crosscheck', {}), indent=2), '', '## Explicit related permit references', '', *related, '', '## Spatial tags for the official permit point', '', json.dumps(tags, indent=2)]
        (target/'article.md').write_text('\n'.join(lines))
        evidence = []
        for url, source_version in bundle.items():
            evidence.append({'url': url, 'version': source_version, 'passages': [dict(p) for p in store.db.execute('SELECT * FROM passages WHERE version_id=? AND (id=? OR id=?)', (source_version, record['passage_id'], data.get('intake_crosscheck', {}).get('passage_id')))]})
        payload = {'report_id': rid, 'version': report['current_version'], 'state': report['state'], 'claims': claims, 'holds': held, 'evidence': evidence, 'research_evidence': [{**r, 'data': json.loads(r['data'])} for r in research], 'spatial_tags': tags, 'related_identifiers': related}
        (target/'review.json').write_text(json.dumps(payload, indent=2))
        body = '<h1>'+html.escape(heading)+'</h1><p>Private development review — '+html.escape(report['state'])+'</p><h2>Draft and evidence</h2>'
        for claim, sentence in zip(claims, sentences):
            passage = store.db.execute('SELECT * FROM passages WHERE id=?', (claim['passage_id'],)).fetchone()
            body += '<p>'+html.escape(sentence)+'</p><details><summary>'+html.escape(passage['locator'])+'</summary><pre>'+html.escape(passage['text'])+'</pre></details>'
        body += '<h2>Holds, official cross-check and spatial evidence</h2><pre>'+html.escape(json.dumps(payload, indent=2))+'</pre>'
        (target/'review.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Private development review</title><style>body{max-width:900px;margin:2em auto;padding:1em;font:18px system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+body+'</html>')
        reports.append({'report_id': rid, 'folder_id': record['folder_id'], 'state': report['state'], 'review': str(target/'review.html')})
    store.commit()
    (output/'queue.json').write_text(json.dumps(reports, indent=2))
    links = ''.join('<li><a href="'+str(r['folder_id'])+'/review.html">Permit '+str(r['folder_id'])+'</a> — '+html.escape(r['state'])+'</li>' for r in reports)
    (output/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Development review queue</title><h1>Private development review queue</h1><p>Editorial approval required. No automatic publication.</p><ul>'+links+'</ul></html>')
    return reports


def review(store, report_id, action, actor, note, review_seconds, edits=None):
    if action not in {'approve', 'hold', 'edit', 'correct'} or not actor.strip() or not note.strip() or not math.isfinite(review_seconds) or review_seconds < 0:
        raise ValueError('Editor, note and finite measured review duration required')
    report = store.db.execute('SELECT * FROM development_reports WHERE id=?', (report_id,)).fetchone()
    if not report: raise ValueError('Development report not found')
    version = store.db.execute('SELECT * FROM development_report_versions WHERE id=?', (report['current_version'],)).fetchone()
    record = store.db.execute('SELECT * FROM development_records WHERE id=?', (report['record_id'],)).fetchone()
    bundle = json.loads(version['evidence_bundle']); claims = json.loads(version['claims'])
    if edits is not None:
        claims = edits['claims']; validate_claims(store, claims, bundle)
        vid = stable_id('development-edited-version', report_id, claims, bundle, action, EXPLANATION_VERSION)
        store.db.execute('INSERT OR IGNORE INTO development_report_versions VALUES(?,?,?,?,?,?,?)', (vid, report_id, stamp(), version['record_hash'], json.dumps(bundle), json.dumps(claims), json.dumps({**json.loads(version['data']), 'explanation_version': EXPLANATION_VERSION, 'rendered_claims': [text(c) for c in claims], 'actor': actor, 'correction': action == 'correct'})))
        store.db.execute('UPDATE development_reports SET current_version=? WHERE id=?', (vid, report_id))
    elif action in {'edit', 'correct'}:
        raise ValueError('Edit/correct requires a structured claims JSON file')
    else:
        vid = version['id']
    if action == 'approve':
        validate_claims(store, claims, bundle)
        if fingerprint(record) != version['record_hash'] or issues(record):
            raise ValueError('Changed or contradictory development evidence must be reconciled before approval')
    state = 'approved' if action == 'approve' else 'hold' if action == 'hold' else 'needs_review'
    store.db.execute('UPDATE development_reports SET state=? WHERE id=?', (state, report_id))
    store.db.execute('INSERT INTO development_review_audit(report_id,version_id,action,actor,at,review_seconds,note) VALUES(?,?,?,?,?,?,?)', (report_id, vid, action, actor, stamp(), review_seconds, note))
    store.commit()
    return {'report_id': report_id, 'state': state, 'published': False, 'review_seconds': review_seconds}


def approved_watch(store, now, window_hours=24, limit=3):
    """Immutable reviewed snapshots; failed/missing collection does not invent activity."""
    from datetime import datetime
    result = []
    for report in store.rows('development_reports'):
        if report['state'] != 'approved': continue
        record = store.db.execute('SELECT * FROM development_records WHERE id=?', (report['record_id'],)).fetchone()
        version = store.db.execute('SELECT * FROM development_report_versions WHERE id=?', (report['current_version'],)).fetchone()
        data = json.loads(version['data']); delta = (now-datetime.fromisoformat(data['observed_at'])).total_seconds()
        if not 0 <= delta <= window_hours*3600 or fingerprint(record) != version['record_hash'] or issues(record): continue
        claims = json.loads(version['claims']); bundle = json.loads(version['evidence_bundle'])
        try: validate_claims(store, claims, bundle)
        except ValueError: continue
        result.append({'report_id': report['id'], 'version_id': version['id'], 'folder_id': record['folder_id'], 'source_url': record['source_url'], 'observed_at': data['observed_at'], 'change_kind': data['change_kind'], 'claims': claims, 'sentences': data['rendered_claims'], 'evidence_bundle': bundle, 'editorial_state': 'approved'})
    return sorted(result, key=lambda r: (r['observed_at'], r['folder_id']), reverse=True)[:limit]


def validate_snapshot(store, project, generated_at):
    """Historical approval at generation time; current proposals cannot rewrite history."""
    from ..records import safe_url
    version = store.db.execute('SELECT * FROM development_report_versions WHERE id=? AND report_id=?', (project['version_id'], project['report_id'])).fetchone()
    report = store.db.execute('SELECT * FROM development_reports WHERE id=?', (project['report_id'],)).fetchone()
    record = store.db.execute('SELECT * FROM development_records WHERE id=?', (report['record_id'],)).fetchone() if report else None
    audit = store.db.execute('SELECT * FROM development_review_audit WHERE report_id=? AND at<=? ORDER BY id DESC LIMIT 1', (project['report_id'], generated_at)).fetchone()
    if not version or not record or not audit or audit['action'] != 'approve' or audit['version_id'] != version['id']:
        raise ValueError('Development snapshot lacks editorial approval at generation time')
    if project['editorial_state'] != 'approved' or project['folder_id'] != record['folder_id'] or project['source_url'] != record['source_url']:
        raise ValueError('Development snapshot identity conflicts with the stored official record')
    if project['claims'] != json.loads(version['claims']) or project['evidence_bundle'] != json.loads(version['evidence_bundle']):
        raise ValueError('Development snapshot differs from its reviewed version')
    if project['sentences'] != json.loads(version['data'])['rendered_claims']:
        raise ValueError('Development explanation differs from its reviewed version')
    safe_url(project['source_url'], ['www.portlandmaps.com'])
    validate_claims(store, project['claims'], project['evidence_bundle'], current=False)
