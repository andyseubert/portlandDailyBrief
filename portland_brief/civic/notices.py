"""Bounded, reconciled city notice collection and isolated attachment extraction."""
import hashlib
import json
from pathlib import Path

from ..records import safe_url
from .geography import notice_links, parse_notice, import_filing
from .ingestion import extract_attachment
from .parsers import soup_main, text
from .store import stable_id, stamp


def collect(store, fetcher, source, output, max_pages=4, max_notices=100, attachments=True):
    if not 1 <= max_pages <= 20 or not 1 <= max_notices <= 100:
        raise ValueError('Use bounded notice page and document budgets')
    started = stamp(); url = source['url']; visited = set(); links = {}; errors = []
    pages = 0; expected = None; terminal = False
    while url and pages < max_pages:
        try:
            safe_url(url, ['www.portland.gov'])
            if url in visited:
                raise ValueError('Notice pagination cycle')
            raw, meta = fetcher.get(url, 'html')
            if meta.get('status') == 'stale_cache':
                raise RuntimeError('Live notice page unavailable')
            entries, pagination = notice_links(raw, url)
            if expected is None:
                expected = pagination['expected_count']
            elif pagination['expected_count'] != expected:
                raise ValueError('Notice inventory changed during pagination')
            if any(link['url'] in links for link in entries):
                raise ValueError('Repeated notice across pages; inventory requires reconciliation')
            store.source_version(url, raw, 'land-use-index-v2', {'links': entries, 'pagination': pagination}, 'filings', meta['fetched_at'])
            links.update((link['url'], link) for link in entries)
            visited.add(url); pages += 1; url = pagination['next_url']
            terminal = url is None
        except (ValueError, RuntimeError, OSError) as exc:
            store.failed_retrieval('filings', url, exc); errors.append({'url': url, 'error': str(exc)}); break
    processed = []; attachment_results = []; failed = []
    for link in list(links.values())[:max_notices]:
        try:
            raw, meta = fetcher.get(link['url'], 'html')
            if meta.get('status') == 'stale_cache':
                raise RuntimeError('Live notice unavailable')
            try:
                row = parse_notice(raw, link['url'])
            except ValueError as exc:
                if 'Stable land-use case identifier not found' not in str(exc):
                    raise
                # The official index also lists general rules and guidance. Keep
                # these as documents, without inventing case or project IDs.
                main = soup_main(raw); title = text(main.select_one('h1'))
                content = text(main.select_one('.field--name-field-body-content'))
                if not title or not content:
                    raise ValueError('Non-case notice content is incomplete')
                vid, changed = store.source_version(link['url'], raw, 'land-use-information/1', {'title': title, 'content': content}, 'filings', meta['fetched_at'])
                pid = store.passage(vid, 'official notice body', content)
                did = stable_id('land-use-information', link['url'])
                store.upsert('documents', id=did, matter_id=None, source_url=link['url'], kind='land_use_information', data={'title': title, 'source_version': vid, 'passage_id': pid, 'case_identifier': None, 'classification': 'no_explicit_case_identifier', 'review_required': True})
                store.commit(); processed.append({'document_id': did, 'case_identifier': None, 'classification': 'information_document_requires_review'})
                continue
            item = import_filing(store, row, raw, link['url']); processed.append(item)
            if attachments:
                for attachment in row['attachments'][:4]:
                    target = attachment['url']
                    try:
                        body, status = fetcher.get(target, 'binary')
                        if status.get('status') == 'stale_cache' or not body.startswith(b'%PDF-'):
                            raise ValueError('Current PDF attachment unavailable or format unrecognized')
                        vid, changed = store.source_version(target, body, 'filing-attachment-bytes/1', {'content_hash': hashlib.sha256(body).hexdigest()}, 'filings', status['fetched_at'])
                        did = stable_id('document', target)
                        store.upsert('documents', id=did, matter_id=None, source_url=target, kind='attachment', data={'label': attachment['label'], 'source_version': vid, 'filing_id': item['filing_id'], 'case_identifier': row['case_identifier'], 'pipeline': 'filings', 'content_status': 'retrieved_not_text_extracted'})
                        store.commit()
                        extracted = extract_attachment(store, did)
                        attachment_results.append({'url': target, 'changed': changed, **extracted})
                    except Exception as exc:
                        store.failed_retrieval('filings', target, exc)
                        attachment_results.append({'url': target, 'error': str(exc), 'status': 'requires_review'})
                if len(row['attachments']) > 4:
                    attachment_results.append({'url': link['url'], 'error': 'Attachment budget exceeded', 'status': 'partial'})
        except Exception as exc:
            store.failed_retrieval('filings', link['url'], exc); failed.append({'url': link['url'], 'error': str(exc)})
    # Recheck the first page; disappearance on an incomplete scan is never removal.
    if terminal:
        try:
            raw, meta = fetcher.get(source['url'], 'html')
            again, pagination = notice_links(raw, source['url'])
            if meta.get('status') == 'stale_cache' or pagination['expected_count'] != expected or again != list(links.values())[:len(again)]:
                raise ValueError('Notice index changed or failed during final reconciliation')
        except Exception as exc:
            errors.append({'url': source['url'], 'error': str(exc)})
    inventory_complete = terminal and not errors and expected is not None and len(links) == expected
    complete = inventory_complete and len(processed) == len(links) and not failed
    result = {'source_id': source['id'], 'checked_at': started, 'pages': pages, 'expected_notices': expected,
              'discovered_notices': len(links), 'processed_notices': len(processed), 'inventory_complete': inventory_complete,
              'complete': complete, 'next_url': url, 'unprocessed_notices': max(0, len(links)-len(processed)),
              'case_notices': sum('filing_id' in p for p in processed),
              'information_documents': sum('document_id' in p for p in processed),
              'errors': errors + failed, 'attachments': attachment_results, 'attachment_processing_complete': attachments and complete and all('error' not in a and a.get('passages') for a in attachment_results),
              'scope': 'Currently listed city land-use notices; not historical land-use cases or neighborhood-contact notices',
              'published': False}
    rid = stable_id('notice-run', source['id'], started)
    store.upsert('pipeline_runs', id=rid, pipeline='filings', started_at=started, ingestion_status='success' if complete else 'partial' if processed else 'failed', parsing_status='success' if complete else 'partial', reconciliation_status='inventory_reconciled' if complete else 'partial', generation_status='structured_evidence', editorial_status='needs_review', data=result)
    store.upsert('inventory_scans', id=stable_id('notice-inventory', rid), pipeline_run_id=rid, source_url=source['url'], complete=int(inventory_complete), pages=pages, expected_count=expected, parsed_count=len(links), data=result)
    if complete:
        store.db.execute('INSERT OR REPLACE INTO collection_checkpoints VALUES(?,?,?)', ('notices:'+source['id'], started, json.dumps(result, sort_keys=True)))
    store.commit()
    target = Path(output); target.mkdir(parents=True, exist_ok=True)
    (target/'notice-status.json').write_text(json.dumps(result, indent=2))
    return result
