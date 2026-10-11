import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

from ..config import load_config
from ..fetching import Fetcher
from ..pipeline import run_lock
from .store import CivicStore, stamp
from .ingestion import bootstrap, ingest_rosters, ingest_agenda, scan, reconcile_register, ingest_document, inspect_event, extract_attachment, ingest_county_agenda
from .geography import fetch_boundaries, import_filing, notice_links, parse_notice
from .reports import generate, review
from .capture import create_job, run_capture, import_transcript, vocabulary
from .projects import lookup as lookup_project
from .developments import scan as scan_developments
from .notices import collect as collect_notices


def coverage(store):
    counts = {}
    for table in ['meetings', 'matters', 'appearances', 'motions', 'amendments', 'vote_events', 'filing_events', 'source_versions', 'meeting_reports', 'capture_jobs']:
        counts[table] = len(store.rows(table))
    counts['current_filing_events'] = sum(not json.loads(r['data']).get('superseded_by') for r in store.rows('filing_events'))
    statuses = [dict(r) for r in store.db.execute('SELECT kind,coverage_status,COUNT(*) AS count FROM meetings GROUP BY kind,coverage_status')]
    return {'counts': counts, 'meeting_statuses': statuses, 'inventory_scans': [{**r, 'data': json.loads(r['data'])} for r in store.rows('inventory_scans')], 'held_votes': store.db.execute("SELECT COUNT(*) FROM vote_events WHERE review_status='hold'").fetchone()[0], 'review_minutes': store.db.execute('SELECT COALESCE(SUM(review_seconds),0)/60 FROM report_audit').fetchone()[0], 'source_storage_bytes': sum(Path(r['artifact_path']).stat().st_size for r in {v['content_hash']: v for v in store.rows('source_versions')}.values()), 'capture_metrics': [json.loads(r['data']) for r in store.rows('capture_jobs')], 'comprehensive_coverage': False, 'publishing': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Private official-record and neighborhood pipelines')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    discover = sub.add_parser('discover')
    discover.add_argument('--max-pages', type=int, default=4)
    discover.add_argument('--source', help='Source ID; absent means all enabled meeting sources')
    discover.add_argument('--start-url', help='Explicit pagination continuation; use with --source')
    discover.add_argument('--refresh', action='store_true')
    agenda = sub.add_parser('ingest-agenda')
    agenda.add_argument('--url', required=True)
    agenda.add_argument('--body', default='portland:council')
    agenda.add_argument('--fixture', type=Path)
    report = sub.add_parser('report')
    report.add_argument('meeting_id')
    report.add_argument('--max-words', type=int, default=500)
    detail = sub.add_parser('ingest-document')
    detail.add_argument('--url', required=True)
    detail.add_argument('--attachments', action='store_true')
    county = sub.add_parser('ingest-county-agenda'); county.add_argument('--url', required=True); county.add_argument('--fixture', type=Path)
    pdf = sub.add_parser('extract-attachment'); pdf.add_argument('document_id')
    event = sub.add_parser('inspect-event'); event.add_argument('meeting_id')
    sub.add_parser('status')
    sub.add_parser('vocabulary')
    sub.add_parser('reconcile-votes')
    filing = sub.add_parser('import-filing')
    filing.add_argument('--file', type=Path, required=True)
    filing.add_argument('--url', required=True)
    collect = sub.add_parser('filings')
    collect.add_argument('--max-notices', type=int)
    collect.add_argument('--boundaries', action='store_true')
    collect.add_argument('--refresh', action='store_true')
    collect.add_argument('--max-pages', type=int)
    collect.add_argument('--attachments', action='store_true', default=None)
    collect.add_argument('--no-attachments', dest='attachments', action='store_false')
    notices = sub.add_parser('notices', help='Reconcile the current city notice inventory and extract official PDFs')
    notices.add_argument('--max-pages', type=int, default=4)
    notices.add_argument('--max-notices', type=int, default=100)
    notices.add_argument('--refresh', action='store_true')
    notices.add_argument('--no-attachments', action='store_true')
    sub.add_parser('development-reports', help='Build private, evidence-linked development review drafts')
    dev_evidence = sub.add_parser('development-evidence', help='Cross-check focused projects with official property and related permit records')
    dev_evidence.add_argument('--refresh', action='store_true')
    dev_review = sub.add_parser('review-development')
    dev_review.add_argument('report_id')
    dev_review.add_argument('action', choices=['approve', 'hold', 'edit', 'correct'])
    dev_review.add_argument('--editor', required=True)
    dev_review.add_argument('--note', required=True)
    dev_review.add_argument('--review-seconds', type=float, required=True)
    dev_review.add_argument('--file', type=Path, help='JSON claims selected from the evidence-backed draft')
    project = sub.add_parser('lookup-project', help='Inspect official permits at a street address; private evidence only')
    project.add_argument('--address', required=True)
    project.add_argument('--limit', type=int, default=100)
    project.add_argument('--refresh', action='store_true')
    developments = sub.add_parser('developments', help='Screen official new-construction permits and write a private mixed-use review draft')
    developments.add_argument('--refresh', action='store_true')
    for action in ['approve', 'hold', 'edit', 'correct']:
        p = sub.add_parser(action)
        p.add_argument('report_id')
        p.add_argument('--editor', required=True)
        p.add_argument('--note', required=True)
        p.add_argument('--review-seconds', type=float, required=True, help='Actual measured human review duration, not estimated time')
        if action in {'edit', 'correct'}:
            p.add_argument('--file', type=Path, required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('meeting_id')
    capture.add_argument('--url', required=True)
    capture.add_argument('--mode', choices=['live', 'archived', 'local'], default='archived')
    capture.add_argument('--max-seconds', type=int, default=3600)
    transcript = sub.add_parser('import-transcript')
    transcript.add_argument('meeting_id')
    transcript.add_argument('--file', type=Path, required=True)
    transcript.add_argument('--source-url', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    settings, _ = load_config(root)
    registry = json.loads((root / 'config/civic-sources.json').read_text())
    fetcher = Fetcher(root, {**settings, 'response_byte_limit': 16_000_000}, getattr(args, 'refresh', False))
    database = root / settings['data_path'] / 'civic.sqlite3'
    store = CivicStore(database)
    try:
        bootstrap(store, root)
        if args.command == 'capture':
            # Job-specific lock permits simultaneous meetings. No civic-wide lock around media capture.
            jid = create_job(store, args.meeting_id, args.url, args.mode)
            result = {'job_id': jid, **run_capture(store, jid, args.max_seconds)}
        else:
            with run_lock(database.parent / ('filings.lock' if args.command in {'filings', 'import-filing', 'lookup-project', 'developments', 'notices', 'development-reports', 'development-evidence', 'review-development'} else 'meetings.lock')):
                if args.command == 'lookup-project':
                    result = lookup_project(store, fetcher, args.address, args.limit)
                elif args.command == 'developments':
                    source = next(s for s in registry if s['type'] == 'development_permits' and s['enabled'])
                    cfg = json.loads((root / 'config/tasks.json').read_text())['filings']['development_focus']
                    result = scan_developments(store, fetcher, source, cfg, root / settings['output_path'] / 'development-review')
                elif args.command == 'notices':
                    source = next(s for s in registry if s['type'] == 'land_use_notices' and s['enabled'])
                    result = collect_notices(store, fetcher, source, root / settings['output_path'] / 'development-review', args.max_pages, args.max_notices, not args.no_attachments)
                elif args.command == 'development-reports':
                    from .development_reports import generate
                    result = generate(store, root / settings['output_path'] / 'development-review')
                elif args.command == 'development-evidence':
                    from .development_evidence import research
                    result = research(store, fetcher, datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%m/%d/%Y'))
                elif args.command == 'review-development':
                    from .development_reports import review
                    result = review(store, args.report_id, args.action, args.editor, args.note, args.review_seconds, json.loads(args.file.read_text()) if args.file else None)
                elif args.command == 'discover':
                    if not 1 <= args.max_pages <= 1000 or (args.start_url and not args.source):
                        raise ValueError('Use a bounded page budget and source for continuations')
                    result = []
                    for source in registry:
                        if not source['enabled'] or source['pipeline'] != 'meetings' or (args.source and source['id'] != args.source):
                            continue
                        if source['type'] == 'institutions':
                            try:
                                data, meta = fetcher.get(source['url'], 'html')
                                if meta.get('status') == 'stale_cache':
                                    raise RuntimeError('Live roster fetch failed')
                                result.append({'source_id': source['id'], **ingest_rosters(store, data, source['url'], datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat())})
                            except Exception as exc:
                                store.failed_retrieval('meetings', source['url'], exc)
                                result.append({'source_id': source['id'], 'error': str(exc)})
                        else:
                            result.append(scan(store, fetcher, source, args.max_pages, args.start_url))
                    if not result:
                        raise ValueError('No matching enabled source')
                elif args.command == 'ingest-agenda':
                    from ..records import safe_url
                    safe_url(args.url, ['www.portland.gov'])
                    if args.fixture:
                        data = args.fixture.read_bytes()
                    else:
                        data, meta = fetcher.get(args.url, 'html')
                        if meta.get('status') == 'stale_cache':
                            raise RuntimeError('Live agenda fetch failed')
                    result = ingest_agenda(store, data, args.url, args.body)
                elif args.command == 'ingest-document':
                    from ..records import safe_url
                    safe_url(args.url, ['www.portland.gov'])
                    result = ingest_document(store, fetcher, args.url, args.attachments)
                elif args.command == 'ingest-county-agenda':
                    from ..records import safe_url
                    safe_url(args.url, ['multnomah.granicus.com'])
                    if args.fixture:
                        body = args.fixture.read_bytes()
                    else:
                        body, meta = fetcher.get(args.url, 'html')
                        if meta.get('status') == 'stale_cache':
                            raise RuntimeError('County agenda fetch failed')
                    result = ingest_county_agenda(store, body, args.url)
                elif args.command == 'extract-attachment':
                    result = extract_attachment(store, args.document_id)
                elif args.command == 'inspect-event':
                    result = inspect_event(store, fetcher, args.meeting_id)
                elif args.command == 'report':
                    if not 50 <= args.max_words <= 5000:
                        raise ValueError('Article length must be bounded')
                    result = generate(store, args.meeting_id, root / settings['output_path'] / 'meetings', args.max_words)
                elif args.command == 'status':
                    result = coverage(store)
                elif args.command == 'vocabulary':
                    result = vocabulary(store)
                elif args.command == 'reconcile-votes':
                    result = reconcile_register(store)
                elif args.command == 'import-filing':
                    data = args.file.read_bytes()
                    result = import_filing(store, json.loads(data), data, args.url)
                elif args.command == 'filings':
                    filing_cfg = json.loads((root / 'config/tasks.json').read_text())['filings']
                    notice_cfg = filing_cfg.get('notice_collection', {})
                    max_notices = args.max_notices if args.max_notices is not None else notice_cfg.get('max_notices', 100)
                    max_pages = args.max_pages if args.max_pages is not None else notice_cfg.get('max_pages', 4)
                    attachments = args.attachments if args.attachments is not None else notice_cfg.get('attachments', True)
                    if not 1 <= max_notices <= 100:
                        raise ValueError('Bound notice requests')
                    result = []
                    for source in registry:
                        if not source['enabled'] or source['pipeline'] != 'filings':
                            continue
                        try:
                            if source['type'] == 'arcgis_geometry' and args.boundaries:
                                result.append(fetch_boundaries(store, fetcher, source))
                            elif source['type'] == 'development_permits':
                                cfg = json.loads((root / 'config/tasks.json').read_text())['filings']['development_focus']
                                result.append(scan_developments(store, fetcher, source, cfg, root / settings['output_path'] / 'development-review'))
                            elif source['type'] == 'land_use_notices':
                                result.append(collect_notices(store, fetcher, source, root / settings['output_path'] / 'development-review', max_pages, max_notices, attachments))
                        except Exception as exc:
                            store.failed_retrieval('filings', source['url'], exc)
                            result.append({'url': source['url'], 'error': str(exc)})
                    from .development_reports import generate
                    if filing_cfg.get('research_focused_projects', True):
                        from .development_evidence import research
                        result.append(research(store, fetcher, datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%m/%d/%Y')))
                    result.append({'development_review_queue': generate(store, root / settings['output_path'] / 'development-review')})
                elif args.command in {'approve', 'hold', 'edit', 'correct'}:
                    result = review(store, args.report_id, args.command, args.editor, args.note, args.review_seconds, json.loads(args.file.read_text()) if hasattr(args, 'file') else None)
                elif args.command == 'import-transcript':
                    result = import_transcript(store, args.meeting_id, json.loads(args.file.read_text()), args.source_url)
                elif args.command == 'backup':
                    if args.out.exists():
                        raise ValueError('Backup destination exists')
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    target = sqlite3.connect(args.out)
                    store.db.backup(target)
                    target.close()
                    result = {'backup': str(args.out), 'source_artifacts': str(database.parent / 'source-versions'), 'note': 'Back up source-versions and capture directories with the database.'}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.command == 'developments' and result['errors']:
            return 1
        if args.command == 'notices' and (not result['complete'] or not args.no_attachments and not result['attachment_processing_complete']):
            return 1
        return 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        store.db.rollback()
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
