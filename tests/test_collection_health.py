from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from portland_brief.civic.cli import main as civic_main
from portland_brief.civic.ingestion import bootstrap, scan
from portland_brief.civic.store import CivicStore, IdentifierConflict
from portland_brief.scheduling import main as scheduler_main, tick


ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'id': 'active', 'enabled': True, 'pipeline': 'meetings', 'type': 'document_index',
          'url': 'https://www.portland.gov/council/documents'}


def cards(*items, total=None):
    prefix = f'Displaying 1 - {len(items)} of {total}' if total is not None else ''
    return ('<main>' + prefix + ''.join(
        f'<div class="views-row"><h2><a href="/council/documents/{slug}">{number}</a></h2>'
        f'<div class="field--name-field-document-number"><div class="field__item">{number}</div></div></div>'
        for number, slug in items) + '</main>').encode()


class CollectionHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        (self.root / 'config/civic-sources.json').write_text(json.dumps([SOURCE]))

    def tearDown(self):
        self.temp.cleanup()

    def discover(self, data, meta, *extra):
        output = io.StringIO()
        with patch('portland_brief.civic.cli.Fetcher.get', return_value=(data, meta)), redirect_stdout(output):
            code = civic_main(['--root', str(self.root), 'discover', *extra])
        return code, json.loads(output.getvalue())

    def test_stale_failure_reaches_cli_with_underlying_reason(self):
        code, result = self.discover(b'not used', {'status': 'stale_cache', 'error': 'HTTP 503'})
        self.assertEqual(code, 1)
        self.assertEqual(result[0]['records'], 0)
        self.assertIn('HTTP 503', result[0]['errors'][0]['error'])

    def test_parse_failure_reaches_cli(self):
        code, result = self.discover(b'<main>Unexpected upstream layout</main>', {'status': 'ok'})
        self.assertEqual(code, 1)
        self.assertIn('No document cards', result[0]['errors'][0]['error'])

    def test_bounded_pagination_is_success_with_explicit_partial_coverage(self):
        data = (ROOT / 'tests/fixtures/portland-current-documents.html').read_bytes()
        code, result = self.discover(data, {'status': 'ok'}, '--max-pages', '1')
        self.assertEqual(code, 0)
        self.assertEqual(result[0]['records'], 25)
        self.assertFalse(result[0]['pagination_complete'])
        self.assertEqual(result[0]['errors'], [])
        self.assertTrue(result[0]['next_url'])

    def test_terminal_inventory_mismatch_fails(self):
        code, result = self.discover(cards(('2026-1', 'one'), total=2), {'status': 'ok'})
        self.assertEqual(code, 1)
        self.assertIn('Inventory count mismatch', result[0]['errors'][0]['error'])

    def test_roster_failure_reaches_cli(self):
        (self.root / 'config/civic-sources.json').write_text(json.dumps([{**SOURCE, 'type': 'institutions'}]))
        code, result = self.discover(b'', {'status': 'stale_cache', 'error': 'HTTP 502'})
        self.assertEqual(code, 1)
        self.assertIn('HTTP 502', result[0]['error'])

    def test_reused_url_holds_conflict_and_continues_other_documents(self):
        store = CivicStore(self.root / 'civic.db')
        self.addCleanup(store.close)
        bootstrap(store, ROOT)
        reused = 'https://www.portland.gov/council/documents/reused'
        first = store.matter('portland', '2026-373', reused, 'Old presentation', {'original': True})
        second = store.matter('portland', '2026-388', 'https://www.portland.gov/council/documents/new-stable', 'New presentation', {})
        store.commit()
        before = store.rows('matters')
        with self.assertRaises(IdentifierConflict):
            store.matter('portland', '2026-388', reused, 'Changed title', {'wrong': True})
        store.failed_retrieval('meetings', reused, 'Fixture identity conflict')
        self.assertEqual(store.rows('matters'), before)
        with self.assertRaises(IdentifierConflict):
            store.matter('portland', '2026-999', reused, 'Must not create orphan', {})
        store.commit()
        self.assertEqual(store.rows('matters'), before)

        class Fetch:
            def get(self, *args):
                return cards(('2026-388', 'reused'), ('2026-400', 'valid'), total=2), {'status': 'ok'}

        result = scan(store, Fetch(), SOURCE)
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['normalized_records'], 1)
        self.assertEqual(result['held_records'], 1)
        self.assertTrue(result['pagination_complete'])
        self.assertEqual(result['errors'][0]['document_number'], '2026-388')
        self.assertEqual(store.rows('inventory_scans')[0]['complete'], 1)
        self.assertEqual(store.rows('pipeline_runs')[0]['parsing_status'], 'partial')
        passage = result['errors'][0]['passage_id']
        self.assertIsNotNone(store.db.execute('SELECT 1 FROM passages WHERE id=?', (passage,)).fetchone())
        self.assertEqual(store.rows('matters')[:2], before)
        self.assertEqual(len(store.rows('documents')), 1)
        self.assertNotEqual(first, second)

    def test_real_discover_failure_enters_scheduler_retry_without_stopping_other_jobs(self):
        config_path = self.root / 'config/tasks.json'
        config = json.loads(config_path.read_text())
        config['repository_archive']['enabled'] = False
        config_path.write_text(json.dumps(config))

        def runner(command, **kwargs):
            if 'discover' in command:
                code = civic_main(['--root', str(self.root), 'discover'])
            else:
                code = 0
            return subprocess.CompletedProcess(command, code)

        with patch('portland_brief.civic.cli.Fetcher.get', return_value=(b'', {'status': 'stale_cache', 'error': 'HTTP 503'})), redirect_stdout(io.StringIO()):
            result = tick(self.root, True, datetime(2026, 10, 28, 18, tzinfo=timezone.utc), runner)
        jobs = {j['job']: j for j in result['results']}
        self.assertEqual(jobs['meetings-discovery']['state'], 'retry')
        self.assertEqual(jobs['meetings-discovery']['exit_code'], 1)
        self.assertTrue(all(j['state'] == 'pending' for key, j in jobs.items() if key != 'meetings-discovery'))
        with sqlite3.connect(self.root / 'data/scheduler/jobs.sqlite3') as db:
            self.assertEqual(db.execute("SELECT exit_code FROM attempts WHERE job_id='meetings-discovery'").fetchone()[0], 1)

    def test_scheduler_process_exit_reflects_failed_job(self):
        with patch('portland_brief.scheduling.tick', return_value={'results': [{'exit_code': 1}]}), redirect_stdout(io.StringIO()):
            self.assertEqual(scheduler_main(['--root', str(self.root), 'tick', '--run']), 1)
