"""Evidence-first local meeting articles and an explicit editorial queue."""
from datetime import datetime
import html
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .store import stable_id, stamp


def validate_claims(store, claims, bundle=None, allow_transcript=False):
    if not claims:
        raise ValueError('A report needs supported claims')
    for claim in claims:
        fact = store.db.execute('SELECT f.*,p.version_id,p.evidence_kind,s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.id=?', (claim.get('fact_id'),)).fetchone()
        if not fact or claim.get('value') != json.loads(fact['value']):
            raise ValueError('Claim lacks a valid supporting fact/value')
        if bundle is not None and bundle.get(fact['url']) != fact['version_id']:
            raise ValueError('Claim evidence is outside the reviewed bundle')
        for key, actual in [('source_url', fact['url']), ('source_version', fact['version_id']), ('passage_id', fact['passage_id'])]:
            if key in claim and claim[key] != actual:
                raise ValueError('Claim citation metadata does not match stored evidence')
        if fact['status'] not in {'official', 'recording_verified'}:
            raise ValueError('Provisional, superseded or conflicting facts require review')
        if not allow_transcript and fact['evidence_kind'] in {'automatic_transcript', 'transcript_unverified'}:
            raise ValueError('Transcript claims are provisional and require recording verification')
    return True


def generate(store, meeting_id, output, max_words=500):
    meeting = store.db.execute('SELECT * FROM meetings WHERE id=?', (meeting_id,)).fetchone()
    if not meeting:
        raise ValueError('Meeting not found')
    metadata = json.loads(meeting['data'])
    appearances = [dict(r) for r in store.db.execute("SELECT * FROM appearances WHERE meeting_id=? ORDER BY CASE WHEN action_type IN ('adoption','reading') THEN 0 WHEN matter_id IS NULL THEN 2 ELSE 1 END, CAST(agenda_number AS INTEGER)", (meeting_id,))]
    if not appearances and meeting['kind'] != 'executive_session':
        raise ValueError('Only a notice is available; no meeting report can be inferred')
    phases = {}
    for phase, predicates in [('before', ['title', 'agenda_note', 'responsible_department', 'neighborhood_fields', 'registration_urls']), ('after', ['title', 'official_disposition']), ('follow-through', ['title', 'notes', 'responsible_department'])]:
        if phase == 'after' and not any(a['official_disposition'] for a in appearances):
            phases[phase] = {'state': 'missing', 'reason': 'Official outcomes have not been ingested. Agenda proposals are not decisions.'}
            continue
        if phase == 'follow-through' and not any(json.loads(a['data']).get('notes') for a in appearances):
            phases[phase] = {'state': 'missing', 'reason': 'No explicit subsequent action or implementation evidence has been ingested.'}
            continue
        claims, bundle, blocks = [], {}, []
        for appearance in appearances:
            current_source = json.loads(appearance['data'])['source_version']
            facts = store.db.execute('SELECT f.*,p.version_id,s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.entity_id=? AND p.version_id=?', (appearance['id'], current_source)).fetchall()
            selected = [f for f in facts if f['predicate'] in predicates]
            if phase == 'before' and appearance['matter_id']:
                matter = store.db.execute('SELECT data FROM matters WHERE id=?', (appearance['matter_id'],)).fetchone()
                document_version = json.loads(matter[0]).get('document_source_version')
                if document_version:
                    selected += list(store.db.execute("SELECT f.*,p.version_id,s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.entity_id=? AND p.version_id=? AND f.predicate IN ('purpose','stated_costs','community_impacts')", (appearance['matter_id'], document_version)))
                    for attachment in store.db.execute("SELECT data,source_url FROM documents WHERE matter_id=? AND kind='attachment'", (appearance['matter_id'],)):
                        attachment_version = json.loads(attachment['data']).get('source_version')
                        if attachment_version:
                            bundle[attachment['source_url']] = attachment_version
            if not selected:
                continue
            pieces = []
            for fact in selected:
                value = json.loads(fact['value'])
                claim = {'fact_id': fact['id'], 'predicate': fact['predicate'], 'value': value, 'appearance_id': appearance['id'], 'matter_id': appearance['matter_id'], 'passage_id': fact['passage_id'], 'source_url': fact['url'], 'source_version': fact['version_id']}
                claims.append(claim)
                bundle[fact['url']] = fact['version_id']
                # Factual source labels/excerpts are transparent; do not fabricate prose entailment.
                label = {'title': 'Proposal/item', 'official_disposition': 'Official action', 'responsible_department': 'Responsible organization', 'neighborhood_fields': 'Official affected-area field', 'agenda_note': 'Agenda/participation note', 'registration_urls': 'Item registration links', 'notes': 'Official follow-up/action notes', 'purpose': 'Stated purpose of proposal', 'stated_costs': 'Stated financial impacts of proposal', 'community_impacts': 'Stated impacts of proposal'}[fact['predicate']]
                pieces.append(f"{label}: {value if isinstance(value, str) else ', '.join(value)}")
            prose = '\n'.join(pieces)
            if phase == 'after':
                summaries = {
                    'reading': 'The council advanced this proposal to another reading. This appearance does not record final adoption.',
                    'adoption': 'The official agenda records passage of this measure. Its effective date and implementation require separate evidence.',
                    'rescheduling': 'The council rescheduled consideration of this proposal.',
                    'committee_recommendation': 'The committee made a recommendation; that action is distinct from final council adoption.',
                    'report_receipt': 'The official agenda records that this material was placed on file.',
                    'report_acceptance': 'The official agenda records acceptance of this report.',
                    'withdrawal': 'The official agenda records withdrawal of this item.',
                    'rejection': 'The official agenda records rejection of this item.'
                }
                prose = json.loads(appearance['data'])['title'] + '\n\n' + summaries.get(appearance['action_type'], 'The official disposition requires further procedural review.') + '\nRecorded disposition: ' + appearance['official_disposition']
            blocks.append({'appearance_id': appearance['id'], 'text': prose, 'citation': selected[0]['url'], 'passage_id': selected[0]['passage_id']})
        if meeting['kind'] == 'executive_session':
            facts = store.db.execute('SELECT f.*,p.version_id,s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.entity_id=? AND f.predicate IN (\'notice\',\'starts_at\')', (meeting_id,)).fetchall()
            for fact in facts:
                value = json.loads(fact['value'])
                claims.append({'fact_id': fact['id'], 'value': value, 'source_url': fact['url'], 'source_version': fact['version_id'], 'passage_id': fact['passage_id']})
                bundle[fact['url']] = fact['version_id']
                blocks.append({'text': f"Public notice: {value}", 'citation': fact['url'], 'passage_id': fact['passage_id']})
        if not claims:
            continue
        if meeting['starts_at']:
            timefact = store.db.execute("SELECT f.*,p.version_id,s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.entity_id=? AND f.predicate='starts_at' ORDER BY s.retrieved_at DESC LIMIT 1", (meeting_id,)).fetchone()
            if timefact:
                claims.append({'fact_id': timefact['id'], 'value': json.loads(timefact['value']), 'source_url': timefact['url'], 'source_version': timefact['version_id'], 'passage_id': timefact['passage_id']})
                bundle[timefact['url']] = timefact['version_id']
        if phase == 'before':
            for fact in store.db.execute("SELECT f.*,p.version_id,s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.entity_id=? AND f.predicate='participation_instructions' ORDER BY s.retrieved_at DESC", (meeting_id,)):
                value = json.loads(fact['value'])
                claims.append({'fact_id': fact['id'], 'value': value, 'source_url': fact['url'], 'source_version': fact['version_id'], 'passage_id': fact['passage_id']})
                bundle[fact['url']] = fact['version_id']
                blocks.insert(0, {'text': 'Official participation instructions: ' + ' '.join(value), 'citation': fact['url'], 'passage_id': fact['passage_id']})
                break
        validate_claims(store, claims, bundle)
        issues = [json.loads(r['data'])['validation'] for r in store.db.execute('SELECT data FROM vote_events WHERE meeting_id=? AND review_status=\'hold\'', (meeting_id,))]
        for appearance in appearances:
            if appearance['matter_id']:
                for doc in store.db.execute("SELECT data FROM documents WHERE matter_id=?", (appearance['matter_id'],)):
                    warnings = json.loads(doc[0]).get('review_warnings')
                    if warnings:
                        issues.append({'issues': warnings, 'review_status': 'hold'})
        capture = [json.loads(r['data']) for r in store.db.execute('SELECT data FROM capture_jobs WHERE meeting_id=?', (meeting_id,))]
        transcripts = [dict(r) for r in store.db.execute('SELECT * FROM transcript_segments WHERE meeting_id=? ORDER BY start_seconds', (meeting_id,))]
        rid = stable_id('report', meeting_id, phase)
        body = store.db.execute('SELECT name FROM bodies WHERE id=?', (meeting['body_id'],)).fetchone()
        article = f"# {body[0] if body else 'Official meeting'} — {phase}\n\nLOCAL DRAFT — document-based view; not published or editorially approved. The before view is reconstructed from the retrieved records, not a historical pre-meeting snapshot.\n\n"
        if meeting['starts_at']:
            local = datetime.fromisoformat(meeting['starts_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Los_Angeles'))
            article += f"Meeting: {local.isoformat()} (America/Los_Angeles).\n\n"
        article += 'These are official-record excerpts. Recording discussion, motives, consensus and implementation are not inferred. AI-assisted workflow; this report uses deterministic extraction.\n\n'
        shown = []
        payload_limit = max(20, max_words - len(article.split()) - 40)
        for block in blocks:
            words = len(block['text'].split())
            if words > payload_limit:
                block = {**block, 'text': ' '.join(block['text'].split()[:payload_limit]) + ' [excerpt; see complete structured evidence]'}
                words = payload_limit
            if sum(len(b['text'].split()) + 5 for b in shown) + words > payload_limit and shown:
                continue
            shown.append(block)
            article += block['text'] + f"\n\n[Official evidence]({block['citation']}{block['passage_id'] and ''}). Passage `{block['passage_id']}`.\n\n"
        article += '[Complete structured record](record.json) retains every extracted item, motion and vote.\n\n'
        article += f"Review: {len(issues)} procedural or source issue(s) held; recording discussion has not been verified. Participation rules must be checked for this specific item.\n"
        version = stable_id('report-version', rid, claims, bundle, max_words, issues, capture, transcripts, article, 'official-evidence-template-v3')
        old = store.db.execute('SELECT * FROM meeting_reports WHERE id=?', (rid,)).fetchone()
        if old and old['current_version']:
            prior = store.db.execute('SELECT * FROM report_versions WHERE id=?', (old['current_version'],)).fetchone()
            if prior and json.loads(prior['data']).get('base_generated_version') == version:
                # Unchanged ingestion must retain reviewed edits/corrections.
                version, article = prior['id'], prior['article']
                claims, bundle = json.loads(prior['claims']), json.loads(prior['evidence_bundle'])
        state = old['state'] if old and old['current_version'] == version else 'needs_review'
        store.upsert('meeting_reports', id=rid, meeting_id=meeting_id, phase=phase, current_version=version, state=state, data={'document_based': True})
        store.db.execute('INSERT OR IGNORE INTO report_versions VALUES(?,?,?,?,?,?,?)', (version, rid, stamp(), json.dumps(bundle, sort_keys=True), json.dumps(claims, ensure_ascii=False), article, json.dumps({'generator': 'official-evidence-template-v3', 'max_words': max_words, 'issues': issues, 'capture_completeness': capture, 'transcript_review': transcripts, 'document_based': True})))
        target = Path(output) / meeting_id.replace(':', '-') / phase
        target.mkdir(parents=True, exist_ok=True)
        (target / 'article.md').write_text(article)
        # HTML is escaped, including all fetched text and URLs.
        review = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Private meeting review</title><body><h1>Private meeting review</h1><pre style="white-space:pre-wrap">' + html.escape(article) + '</pre><h2>Evidence and review holds</h2>'
        for claim in claims:
            evidence = store.db.execute('SELECT text,locator FROM passages WHERE id=?', (claim['passage_id'],)).fetchone()
            review += '<details><summary>' + html.escape(str(claim['value'])) + '</summary><p>' + html.escape(evidence['locator']) + '</p><pre style="white-space:pre-wrap">' + html.escape(evidence['text']) + '</pre></details>'
        review += '<h2>Review holds</h2><pre>' + html.escape(json.dumps(issues, indent=2)) + '</pre><h2>Capture completeness and missing intervals</h2><pre>' + html.escape(json.dumps(capture, indent=2)) + '</pre><h2>Unverified transcripts and attribution</h2><pre>' + html.escape(json.dumps(transcripts, indent=2)) + '</pre></body></html>'
        (target / 'review.html').write_text(review)
        (target / 'record.json').write_text(json.dumps({'meeting': dict(meeting), 'appearances': appearances, 'motions': [dict(r) for r in store.db.execute('SELECT m.* FROM motions m JOIN appearances a ON a.id=m.appearance_id WHERE a.meeting_id=?', (meeting_id,))], 'votes': [dict(r) for r in store.db.execute('SELECT * FROM vote_events WHERE meeting_id=?', (meeting_id,))], 'claims': claims, 'evidence_bundle': bundle}, indent=2))
        phases[phase] = {'report_id': rid, 'version': version, 'state': state, 'article': str(target / 'article.md'), 'review': str(target / 'review.html')}
    store.commit()
    return phases


def review(store, report_id, action, actor, note, review_seconds, edits=None):
    if action not in {'approve', 'hold', 'edit', 'correct'} or not actor.strip() or not note.strip() or review_seconds < 0:
        raise ValueError('Explicit editor, note, nonnegative measured review seconds and valid action required')
    report = store.db.execute('SELECT * FROM meeting_reports WHERE id=?', (report_id,)).fetchone()
    if not report:
        raise ValueError('Report not found')
    version = store.db.execute('SELECT * FROM report_versions WHERE id=?', (report['current_version'],)).fetchone()
    claims, bundle = json.loads(version['claims']), json.loads(version['evidence_bundle'])
    if edits is not None:
        # Editors change structured claims, never add uncited free-form factual sentences.
        claims = edits['claims']
        validate_claims(store, claims, bundle)
        newid = stable_id('report-version', report_id, claims, bundle)
        article = 'LOCAL DRAFT — edited, not publicly published.\n\n' + '\n\n'.join(str(c['value']) + f" [Evidence]({store.db.execute('SELECT s.url FROM facts f JOIN passages p ON p.id=f.passage_id JOIN source_versions s ON s.id=p.version_id WHERE f.id=?', (c['fact_id'],)).fetchone()[0]})" for c in claims)
        base_generated = json.loads(version['data']).get('base_generated_version', version['id'])
        store.db.execute('INSERT OR IGNORE INTO report_versions VALUES(?,?,?,?,?,?,?)', (newid, report_id, stamp(), json.dumps(bundle), json.dumps(claims), article, json.dumps({'editor': actor, 'correction': action == 'correct', 'base_generated_version': base_generated})))
        store.db.execute('UPDATE meeting_reports SET current_version=? WHERE id=?', (newid, report_id))
        version = store.db.execute('SELECT * FROM report_versions WHERE id=?', (newid,)).fetchone()
    if action == 'approve':
        validate_claims(store, claims, bundle)
        for url, vid in bundle.items():
            source = store.db.execute('SELECT meaningful_hash FROM source_versions WHERE id=?', (vid,)).fetchone()
            current = store.db.execute('SELECT meaningful_hash FROM source_current WHERE url=?', (url,)).fetchone()
            if not source or not current or source[0] != current[0]:
                raise ValueError('Material evidence changed; regenerate and review')
        conflicts = store.db.execute('SELECT data FROM vote_events WHERE meeting_id=?', (report['meeting_id'],)).fetchall()
        if any(any('mismatch' in i or 'conflict' in i or 'duplicate' in i for i in json.loads(r[0])['validation']['issues']) for r in conflicts):
            raise ValueError('Contradictory vote records must be reconciled before approval')
    state = 'approved' if action == 'approve' else 'hold' if action == 'hold' else 'needs_review'
    store.db.execute('UPDATE meeting_reports SET state=? WHERE id=?', (state, report_id))
    store.db.execute('INSERT INTO report_audit(report_id,version_id,action,actor,at,review_seconds,note) VALUES(?,?,?,?,?,?,?)', (report_id, version['id'], action, actor, stamp(), review_seconds, note))
    store.commit()
    return {'state': state, 'published': False, 'review_seconds': review_seconds}
