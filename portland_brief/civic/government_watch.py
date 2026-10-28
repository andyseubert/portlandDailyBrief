"""Permanent private council coverage; public notices and reviewed reports only."""
from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urljoin, urlsplit

from ..records import safe_url, parse_time
from ..rendering import atomic_text, page, local_time
from . import parsers
from .ingestion import ingest_agenda, ingest_rosters, ingest_document, extract_attachment
from .reports import generate, validate_claims
from .store import stable_id, stamp


AGENDA_PATH = re.compile(r'^/council/agenda/(?:[a-z-]+/)?(\d{4})/(\d{1,2})/(\d{1,2})$')


def agenda_links(raw, url):
    result = []
    for a in parsers.soup_main(raw).select('a[href]'):
        target = urljoin(url, a['href']).split('#')[0].rstrip('/')
        match = AGENDA_PATH.fullmatch(urlsplit(target).path)
        if match and urlsplit(target).hostname == 'www.portland.gov':
            safe_url(target, ['www.portland.gov'])
            entry = {'url': target, 'title': parsers.text(a), 'day': '-'.join([match[1], match[2].zfill(2), match[3].zfill(2)])}
            if not any(x['url'] == target for x in result):
                result.append(entry)
    return result


def election_candidates(raw):
    main = parsers.soup_main(raw)
    candidates = []
    for heading in main.select('h3'):
        label = parsers.text(heading)
        district = re.search(r'City of Portland Councilor, District (\d+)', label)
        auditor = label.startswith('City of Portland Auditor (')
        if not district and not auditor:
            continue
        # Stop at the next heading, never collect candidates from another race.
        listing = heading.find_next(['ul', 'h2', 'h3'])
        if listing and listing.name == 'ul':
            for li in listing.select('li'):
                name = parsers.text(li)
                if name:
                    candidates.append({'name': name, 'office': 'City Council' if district else 'City Auditor', 'district': district[1] if district else None})
    if not candidates or not any(c['office'] == 'City Council' for c in candidates):
        raise ValueError('Official election candidate lists not found')
    return candidates


def current_appearances(store, meeting):
    version = json.loads(meeting['data']).get('source_version')
    return [dict(r) for r in store.db.execute('SELECT * FROM appearances WHERE meeting_id=? ORDER BY CAST(agenda_number AS INTEGER)', (meeting['id'],))
            if not version or json.loads(r['data']).get('source_version') == version]


def collect(store, fetcher, config, output, now=None):
    now = now or datetime.now(timezone.utc)
    checked = now.isoformat()
    start = (now - timedelta(days=config.get('lookback_days', 14))).date().isoformat()
    end = (now + timedelta(days=config.get('lookahead_days', 7))).date().isoformat()
    errors, agenda_urls, fetched, documents, fetched_documents = [], {}, [], [], 0

    def get(url):
        raw, meta = fetcher.get(url, 'html')
        if meta.get('status') == 'stale_cache':
            raise RuntimeError('Live fetch failed: ' + meta.get('error', 'stale cache'))
        return raw, meta

    def failed(url, exc):
        store.failed_retrieval('government_watch', url, exc)
        errors.append({'url': url, 'error': str(exc)[:500]})

    roster_url = 'https://www.portland.gov/council/policy-committees'
    try:
        raw, _ = get(roster_url)
        ingest_rosters(store, raw, roster_url, now.astimezone().date().isoformat())
    except (ValueError, RuntimeError, OSError) as exc:
        failed(roster_url, exc)

    bodies = {'https://www.portland.gov/council/agenda': 'portland:council'}
    for body in store.db.execute("SELECT * FROM bodies WHERE jurisdiction_id='portland' AND kind IN ('committee','committee_of_whole')"):
        url = json.loads(body['data']).get('url')
        if url:
            bodies[safe_url(url, ['www.portland.gov'])] = body['id']
    # Explicit source links establish the body; dates/titles never merge meetings.
    for index_url, body_id in bodies.items():
        try:
            raw, _ = get(index_url)
            links = agenda_links(raw, index_url)
            if not links:
                raise ValueError('No dated agenda links found on official body index')
            store.source_version(index_url, raw, 'agenda-links-v1', links, pipeline='government_watch')
            for link in links:
                if start <= link['day'] <= end:
                    agenda_urls[link['url']] = body_id
        except (ValueError, RuntimeError, OSError) as exc:
            failed(index_url, exc)
    # Revisit already discovered agendas for late-posted outcomes, even after an
    # index switches to next week's agenda. Never infer outcomes from elapsed time.
    for meeting in store.db.execute("SELECT * FROM meetings WHERE starts_at>=? AND starts_at<=?", (start, end + 'T23:59:59')):
        if json.loads(meeting['data']).get('document_based') and meeting['body_id'] in bodies.values():
            agenda_urls.setdefault(meeting['source_url'], meeting['body_id'])

    budget = config.get('max_agendas', 12)
    def proximity(url):
        match = AGENDA_PATH.fullmatch(urlsplit(url).path)
        day = datetime(int(match[1]),int(match[2]),int(match[3]),tzinfo=timezone.utc)
        return abs((day-now).total_seconds()), url
    last_checks = {r['source_url']:r['checked_at'] for r in store.db.execute('SELECT m.source_url,MIN(w.checked_at) AS checked_at FROM meeting_watch_entries w JOIN meetings m ON m.id=w.id GROUP BY m.source_url')}
    selected = sorted(agenda_urls, key=lambda url:(last_checks.get(url,''),proximity(url)))
    for url in selected[:budget]:
        try:
            raw, _ = get(url)
            result = ingest_agenda(store, raw, url, agenda_urls[url])
            fetched.append(url)
            for meeting_id in result['meeting_ids']:
                meeting = store.db.execute('SELECT * FROM meetings WHERE id=?', (meeting_id,)).fetchone()
                rows = current_appearances(store, meeting)
                holds = [w for row in rows for w in json.loads(row['data']).get('identity_warnings', [])]
                reports = generate(store, meeting_id, Path(output) / 'reports')
                store.upsert('meeting_watch_entries', id=meeting_id, checked_at=checked, source_version=json.loads(meeting['data'])['source_version'], data={'reports': reports, 'identity_holds': holds, 'outcomes': sum(bool(r['official_disposition']) for r in rows), 'items': len(rows)})
                store.commit()
                for row in rows:
                    item = json.loads(row['data'])
                    if item.get('document_url') and not item.get('identity_warnings') and item['document_url'] not in documents:
                        documents.append(item['document_url'])
        except (ValueError, RuntimeError, OSError) as exc:
            failed(url, exc)

    # Full linked documents are bounded separately; the queue reports deferred
    # work. PDF bytes/text are not silently represented as interpreted content.
    document_budget = config.get('max_documents', 24)
    def last_document_check(url):
        row = store.db.execute("SELECT MAX(at) FROM retrievals WHERE url=? AND status='fetched_and_parsed'",(url,)).fetchone()
        return row[0] or '', url
    documents.sort(key=last_document_check)
    for url in documents[:document_budget]:
        try:
            ingest_document(store, fetcher, url, attachments=False)
            fetched_documents += 1
        except (ValueError, RuntimeError, OSError) as exc:
            failed(url, exc)
    attachment_rows = []
    for document in store.db.execute("SELECT * FROM documents WHERE kind='attachment'"):
        metadata = json.loads(document['data'])
        parent = store.db.execute('SELECT url FROM source_versions WHERE id=?',(metadata.get('parent_version'),)).fetchone()
        if parent and parent['url'] in documents:
            attachment_rows.append(dict(document))
    attachment_rows.sort(key=lambda r:last_document_check(r['source_url']))
    attachment_budget = config.get('max_attachments',6)
    attachments_fetched, attachment_pages, empty_pages = 0, 0, 0
    for attachment in attachment_rows[:attachment_budget]:
        try:
            raw, meta = fetcher.get(attachment['source_url'],'binary')
            if meta.get('status') == 'stale_cache':
                raise RuntimeError('Live attachment fetch failed: '+meta.get('error','stale cache'))
            version, _ = store.source_version(attachment['source_url'],raw,'attachment-bytes-v1',{'content_hash':hashlib.sha256(raw).hexdigest()})
            metadata = json.loads(attachment['data'])
            if metadata.get('source_version') != version:
                metadata = {k:v for k,v in metadata.items() if k not in {'page_passages','pages','empty_or_scan_pages','review_warnings','extraction_version'}}
                metadata['content_status'] = 'retrieved_not_text_extracted'
            metadata['source_version'] = version
            store.upsert('documents',**{**attachment,'data':metadata})
            store.commit()
            extracted = extract_attachment(store,attachment['id'])
            attachments_fetched += 1
            attachment_pages += extracted['pages']
            empty_pages += len(extracted['empty_or_scan_pages'])
        except (ValueError, RuntimeError, OSError) as exc:
            failed(attachment['source_url'],exc)
    # Regenerate previews with this run's document evidence.
    for entry in store.db.execute('SELECT * FROM meeting_watch_entries WHERE checked_at=?', (checked,)).fetchall():
        metadata = json.loads(entry['data'])
        metadata['reports'] = generate(store, entry['id'], Path(output) / 'reports')
        store.upsert('meeting_watch_entries', **{**dict(entry), 'data': metadata})
    election = config.get('election', {})
    if election.get('url') and now.date().isoformat() <= election['monitor_through']:
        try:
            raw, _ = get(election['url'])
            candidates = election_candidates(raw)
            version, _ = store.source_version(election['url'], raw, 'election-candidates-v1', candidates, pipeline='government_watch')
            store.upsert('election_watch_versions', id=version, checked_at=checked, data={'candidates': candidates, 'election_day': election['election_day'], 'source_url': election['url']})
        except (ValueError, RuntimeError, OSError) as exc:
            failed(election['url'], exc)
    agenda_versions={r['source_url']:r['source_version'] for r in store.db.execute('SELECT m.source_url,w.source_version FROM meeting_watch_entries w JOIN meetings m ON m.id=w.id WHERE w.checked_at=?',(checked,))}
    unread_attachments=sum(json.loads(store.db.execute('SELECT data FROM documents WHERE id=?',(r['id'],)).fetchone()[0]).get('content_status')!='text_extracted' for r in attachment_rows)
    result = {'checked_at': checked, 'agenda_sources': len(bodies), 'agendas_discovered': len(agenda_urls), 'agendas_fetched': len(fetched), 'agenda_versions':agenda_versions, 'agendas_deferred': max(0, len(selected)-budget), 'documents_fetched': fetched_documents, 'documents_deferred': max(0,len(documents)-document_budget), 'attachments_discovered':len(attachment_rows), 'attachments_fetched':attachments_fetched, 'attachments_deferred':max(0,len(attachment_rows)-attachment_budget),'attachment_pages':attachment_pages,'attachment_empty_pages':empty_pages, 'errors': errors, 'permanent': True, 'scope': 'Current official Portland council/committee index links and retained agendas in the configured date window; county news is prioritized but county outcome ingestion remains separate.'}
    result['attachments_without_complete_text']=unread_attachments
    store.upsert('government_watch_runs', id=stable_id('government-watch-run', checked), checked_at=checked, data=result)
    store.commit()
    write_queue(store, Path(output), now, config)
    return result


def names(store, now):
    result = {r[0] for r in store.db.execute("SELECT DISTINCT p.name FROM people p JOIN memberships m ON m.person_id=p.id WHERE m.effective_to IS NULL")}
    row = store.db.execute('SELECT * FROM election_watch_versions ORDER BY checked_at DESC LIMIT 1').fetchone()
    if row and parse_time(row['checked_at']) <= now and now - parse_time(row['checked_at']) < timedelta(days=3):
        result.update(c['name'] for c in json.loads(row['data'])['candidates'])
    return sorted(result)


def snapshot(store, now, config):
    """Only notice metadata and actually approved claims may enter the brief."""
    result = {'enabled': True, 'notices': [], 'reports': [], 'gaps': [], 'pending_reports': 0}
    latest = store.db.execute('SELECT * FROM government_watch_runs WHERE checked_at<=? ORDER BY checked_at DESC LIMIT 1', (now.isoformat(),)).fetchone()
    if not latest or now - parse_time(latest['checked_at']) > timedelta(hours=3):
        result['gaps'].append('Council agenda coverage has not completed a recent check; private review queue may be out of date.')
    elif json.loads(latest['data'])['errors']:
        result['gaps'].append('Council agenda collection has source or parsing gaps; see the private review queue.')
    if latest and json.loads(latest['data']).get('attachments_without_complete_text'):
        result['gaps'].append(f"Council watch has {json.loads(latest['data'])['attachments_without_complete_text']} linked attachments without complete extracted text. Agenda collection is separate from document interpretation.")
    # Notices only, with their observed source version: no unreviewed agenda
    # narratives, decisions, votes or purported meeting reports enter the archive.
    for row in store.db.execute('SELECT m.*,b.name AS body_name,w.checked_at,w.source_version FROM meeting_watch_entries w JOIN meetings m ON m.id=w.id JOIN bodies b ON b.id=m.body_id WHERE julianday(m.starts_at)>=julianday(?) AND julianday(m.starts_at)<=julianday(?) ORDER BY julianday(m.starts_at)', (now.isoformat(), (now + timedelta(days=config.get('lookahead_days',7))).isoformat())):
        if parse_time(row['checked_at']) > now or now - parse_time(row['checked_at']) > timedelta(hours=36):
            continue
        evidence = store.db.execute('SELECT artifact_path FROM source_versions WHERE id=?',(row['source_version'],)).fetchone()
        title = parsers.text(parsers.soup_main(Path(evidence[0]).read_bytes()).select_one('h1'))
        result['notices'].append({'meeting_id':row['id'], 'title':title, 'starts_at':row['starts_at'], 'source_url':row['source_url'], 'source_version':row['source_version'], 'checked_at':row['checked_at']})
    for r in store.db.execute('SELECT r.*,v.created_at,v.claims,v.evidence_bundle FROM meeting_reports r JOIN report_versions v ON v.id=r.current_version JOIN meetings m ON m.id=r.meeting_id JOIN bodies b ON b.id=m.body_id WHERE b.jurisdiction_id=\'portland\' AND m.starts_at>=?', ((now-timedelta(days=config.get('lookback_days',14))).isoformat(),)):
        if r['state'] != 'approved':
            result['pending_reports'] += 1
            continue
        audit = store.db.execute("SELECT at FROM report_audit WHERE report_id=? AND version_id=? AND action='approve' AND at<=? ORDER BY id DESC LIMIT 1", (r['id'],r['current_version'],now.isoformat())).fetchone()
        if not audit or now-parse_time(audit['at']) > timedelta(hours=24):
            continue
        claims = json.loads(r['claims'])
        # Expose only approved structured claims; never synthesize new prose.
        brief_claims = [c for c in claims if c.get('predicate') in {'title','official_disposition'}][:6]
        if not brief_claims:
            continue
        item = {'report_id':r['id'],'version_id':r['current_version'],'phase':r['phase'],'approved_at':audit['at'],'claims':brief_claims}
        validate_report_snapshot(store,item,now)
        result['reports'].append(item)
    if result['pending_reports']:
        result['gaps'].append(f"{result['pending_reports']} council meeting report drafts await editorial review; unreviewed agenda/outcome narratives are omitted.")
    return result


def validate_report_snapshot(store, item, as_of):
    row = store.db.execute('SELECT * FROM report_versions WHERE id=? AND report_id=?', (item['version_id'],item['report_id'])).fetchone()
    approval = store.db.execute("SELECT 1 FROM report_audit WHERE report_id=? AND version_id=? AND action='approve' AND at=?", (item['report_id'],item['version_id'],item['approved_at'])).fetchone()
    if not row or not approval or parse_time(item['approved_at']) > as_of:
        raise ValueError('Meeting snapshot requires a real prior editorial approval')
    report = store.db.execute('SELECT phase FROM meeting_reports WHERE id=?',(item['report_id'],)).fetchone()
    audit = store.db.execute('SELECT action,version_id FROM report_audit WHERE report_id=? AND at<=? ORDER BY id DESC LIMIT 1', (item['report_id'],as_of.isoformat())).fetchone()
    if not audit or audit['action'] != 'approve' or audit['version_id'] != item['version_id'] or report['phase'] != item['phase']:
        raise ValueError('Meeting snapshot approval was held, edited or invalidated')
    claims = json.loads(row['claims'])
    if not item['claims'] or any(c not in claims for c in item['claims']):
        raise ValueError('Meeting snapshot contains unreviewed claims')
    validate_claims(store,item['claims'],json.loads(row['evidence_bundle']))


def validate_snapshot(store, watch, as_of):
    for notice in watch.get('notices', []):
        safe_url(notice['source_url'], ['www.portland.gov'])
        row = store.db.execute('SELECT * FROM source_versions WHERE id=? AND url=?', (notice['source_version'],notice['source_url'])).fetchone()
        check = store.db.execute('SELECT data FROM government_watch_runs WHERE checked_at=?',(notice['checked_at'],)).fetchone()
        observed = json.loads(check[0]).get('agenda_versions',{}) if check else {}
        if not row or parse_time(notice['checked_at']) > as_of or observed.get(notice['source_url']) != notice['source_version']:
            raise ValueError('Meeting notice lacks dated official evidence')
        raw = Path(row['artifact_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['content_hash']:
            raise ValueError('Meeting notice source bytes do not match stored hash')
        sessions = parsers.agenda(raw,row['url'])
        meeting = store.db.execute('SELECT body_id FROM meetings WHERE id=?', (notice['meeting_id'],)).fetchone()
        title = parsers.text(parsers.soup_main(raw).select_one('h1'))
        matches = [s for s in sessions if meeting and stable_id('meeting',meeting['body_id'],row['url'],s['official_relation_id']) == notice['meeting_id'] and s['starts_at'] == notice['starts_at']]
        if not matches or title != notice['title']:
            raise ValueError('Meeting notice does not match official evidence')
    for item in watch.get('reports', []):
        validate_report_snapshot(store,item,as_of)


def write_queue(store, output, now, config):
    output.mkdir(parents=True, exist_ok=True)
    e = escape
    blocks = ['<h1>Council &amp; Local Government Watch</h1><p>Permanent coverage, including after elections. Private drafts; meeting reports require editorial review.</p><p><a href="news.html">Recent government and election news</a></p>', f'<p>Checked {e(local_time(now.isoformat(),"America/Los_Angeles"))}. Proposals, recommendations, amendments and final decisions remain distinct. Recordings have not been reviewed.</p>']
    run = store.db.execute('SELECT data FROM government_watch_runs ORDER BY checked_at DESC LIMIT 1').fetchone()
    if run:
        data = json.loads(run[0])
        blocks.append(f"<p>This run checked {data['agendas_fetched']} agendas and {data['documents_fetched']} linked documents. {data['agendas_deferred']} agendas, {data['documents_deferred']} documents and {data.get('attachments_deferred',0)} attachments were outside this run's processing budget and rotate into later checks. {data.get('attachments_fetched',0)} attachments processed this run; {data.get('attachment_empty_pages',0)} pages without extracted text. {len(data['errors'])} source/document errors.</p>")
        if data.get('attachments_without_complete_text'):
            blocks.append(f"<p class=\"notice\">{data['attachments_without_complete_text']} linked attachments still lack complete extracted text. Extracted pages also require interpretation; they are not reviewed reporting.</p>")
        blocks.extend('<p class="notice">'+e(err['url']+': '+err['error'])+'</p>' for err in data['errors'])
    start = (now-timedelta(days=config.get('lookback_days',14))).date().isoformat()
    for meeting in store.db.execute('SELECT m.*,b.name AS body_name,w.checked_at,w.data AS watch_data FROM meeting_watch_entries w JOIN meetings m ON m.id=w.id JOIN bodies b ON b.id=m.body_id WHERE m.starts_at>=? ORDER BY m.starts_at DESC', (start,)):
        info = json.loads(meeting['watch_data'])
        blocks.append('<article><h2>'+e(meeting['body_name'])+'</h2><p>'+e(local_time(meeting['starts_at'],'America/Los_Angeles'))+' · Last checked '+e(local_time(meeting['checked_at'],'America/Los_Angeles'))+'</p><p><a href="'+e(meeting['source_url'],quote=True)+'">Official agenda and participation links</a></p>')
        blocks.append(f"<p>{info['items']} agenda items; {info['outcomes']} recorded dispositions. A disposition can be a referral or another reading. Missing outcomes remain pending.</p>")
        for phase, report in info['reports'].items():
            label={'before':'Agenda preview','after':'Recorded actions draft','follow-through':'Follow-up draft'}[phase]
            if report.get('review'):
                target = Path(report['review']).resolve().relative_to(output.resolve())
                current = store.db.execute('SELECT state FROM meeting_reports WHERE id=?',(report['report_id'],)).fetchone()
                state={'needs_review':'Awaiting editorial review','approved':'Editor approved','hold':'Editor hold'}.get(current[0],current[0])
                blocks.append('<p><a href="'+e(target.as_posix(),quote=True)+'">'+e(label)+'</a> — '+e(state)+'</p>')
            else:
                blocks.append('<p>'+e(label+': '+report.get('reason','Not available'))+'</p>')
        for hold in info.get('identity_holds',[]):
            blocks.append('<p class="notice">Identity hold: '+e(hold)+'</p>')
        for row in current_appearances(store,meeting):
            item = json.loads(row['data'])
            blocks.append('<details><summary>'+e(str(row['agenda_number'])+' · '+item['title'])+'</summary><p>Stage: '+e(row['action_type'])+'. Recorded disposition: '+e(row['official_disposition'] or 'Awaiting official outcome')+'</p><p>'+e(item.get('notes',''))+'</p>')
            if row['matter_id']:
                for attachment in store.db.execute("SELECT * FROM documents WHERE matter_id=? AND kind='attachment'",(row['matter_id'],)):
                    details = json.loads(attachment['data'])
                    blocks.append('<p><a href="'+e(attachment['source_url'],quote=True)+'">'+e(details.get('label','Official attachment'))+'</a> · '+e(details.get('attachment_role','exhibit'))+' · '+e(details.get('content_status','not_fetched'))+'</p>')
            blocks.append('</details>')
        blocks.append('</article>')
    blocks.append('<h2>Calendar notices without reconciled agenda identities</h2><p>Calendar and agenda IDs are separate until explicit source links are verified. These notices are not counted as processed reports.</p>')
    for row in store.db.execute("SELECT * FROM meetings WHERE starts_at>=? AND starts_at<=? AND source_url LIKE 'https://www.portland.gov/%/events/%' ORDER BY starts_at", (start,(now+timedelta(days=7)).date().isoformat())):
        metadata = json.loads(row['data'])
        blocks.append('<p><a href="'+e(row['source_url'],quote=True)+'">'+e(metadata.get('title','Official calendar notice'))+'</a> · '+e(local_time(row['starts_at'],'America/Los_Angeles'))+'</p>')
    candidate = store.db.execute('SELECT * FROM election_watch_versions ORDER BY checked_at DESC LIMIT 1').fetchone()
    if candidate:
        info = json.loads(candidate['data'])
        blocks.append('<h2>Election Watch</h2><p>Candidate names from the <a href="'+e(info['source_url'],quote=True)+'">official ballot list</a>; checked '+e(candidate['checked_at'])+'. Campaign claims require attribution. Results are not inferred from news coverage or preliminary counts.</p>')
        blocks.append('<p>'+e('; '.join(c['name']+' — '+c['office']+(' District '+c['district'] if c['district'] else '') for c in info['candidates']))+'</p>')
    blocks.append('<p>Scope: bounded current council/committee agendas and linked documents. Deferred documents, unread attachments, calendar reconciliation and county outcomes remain coverage gaps. No inference of complete city/county coverage.</p>')
    atomic_text(output/'index.html',page('Council & Local Government Watch',''.join(blocks)))


def write_news_queue(store, root, settings, now=None):
    from ..government import is_government
    from ..records import Evidence
    from ..sources import source_enabled
    from ..selection import relevant
    now=now or datetime.now(timezone.utc)
    output=root/settings['output_path']/'council-watch';output.mkdir(parents=True,exist_ok=True)
    e=escape
    blocks=['<h1>Government &amp; election news</h1><p><a href="index.html">Council coverage</a></p><p>Recent headlines from enabled feeds, including items outside the daily selection. Headline discovery is not an independently verified report. The daily edition retains relevance, freshness, source-balance and evidence checks.</p>']
    path=root/settings['data_path']/'news-collection.sqlite3'
    count=0
    if path.exists():
        registry=json.loads((root/'config/sources.json').read_text())
        allowed={s['id']:s for s in registry if source_enabled(s)}
        scoped={**settings,'government_names':names(store,now)}
        cutoff=now-timedelta(hours=settings.get('late_arrival_lookback_hours',72))
        seen=set()
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            for row in db.execute('SELECT * FROM collected WHERE last_seen>=? ORDER BY last_seen DESC',(cutoff.isoformat(),)):
                item=Evidence(**json.loads(row['record']))
                if item.id in seen or item.source_id not in allowed:
                    continue
                seen.add(item.id)
                published=parse_time(item.published_at)
                if not published or not cutoff<=published<=now or not relevant(item,scoped) or not is_government(item,scoped):
                    continue
                try:
                    safe_url(item.url,allowed[item.source_id]['allowed_hosts'])
                except ValueError:
                    continue
                used=db.execute('SELECT MAX(date) FROM consumed WHERE id=? AND content_hash=?',(item.id,item.content_hash)).fetchone()[0]
                status='Included in '+used if used else 'Not yet included in a daily edition'
                blocks.append('<article><h2><a href="'+e(item.url,quote=True)+'">'+e(item.title)+'</a></h2><p>'+e(item.source_name)+' · Published '+e(item.published_at)+' · '+e(status)+'</p></article>')
                count+=1
            blocks.append('<h2>Last successful feed checks</h2>')
            for source in db.execute('SELECT source_id,at FROM checkpoints ORDER BY source_id'):
                if source['source_id'] in allowed:
                    blocks.append('<p>'+e(allowed[source['source_id']]['name'])+' · '+e(source['at'])+'</p>')
    if not count:
        blocks.append('<p>No matching recent buffered headlines are available. This does not establish that no local-government news exists.</p>')
    atomic_text(output/'news.html',page('Government & election news',''.join(blocks)))
    return count
