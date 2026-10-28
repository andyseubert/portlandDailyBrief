import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from portland_brief.fetching import Fetcher
from portland_brief.records import digest


URL = 'https://www.portland.gov/cache-regression'
SETTINGS = {'cache_path': 'cache', 'cache_ttl_seconds': 3600,
            'cache_max_age_seconds': 86400, 'fetch_attempts': 1,
            'fetch_timeout_seconds': 1}


class Response:
    url = URL

    def __init__(self, data):
        self.data = data
        self.headers = {'ETag': hashlib.sha256(data).hexdigest()}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.data


def concurrent_fetch(root, start, results, worker):
    """Separate processes model the news/meeting/filings scheduler children."""
    original_replace = Path.replace

    def delayed_replace(path, target):
        # Widen the old shared-temporary-file race and the gap between the
        # body and metadata replacements; all readers must still be coherent.
        time.sleep(.01)
        return original_replace(path, target)

    try:
        start.wait(10)
        with patch('portland_brief.fetching.urlopen', side_effect=lambda *a, **k: Response(f'worker {worker}'.encode())), patch.object(Path, 'replace', delayed_replace):
            for _ in range(6):
                fetcher = Fetcher(Path(root), SETTINGS, True)
                data, meta = fetcher.get(URL)
                assert meta['status'] == 'ok', meta
                assert data == f'worker {worker}'.encode(), data
                data, meta = Fetcher(Path(root), SETTINGS).get(URL)
                assert hashlib.sha256(data).hexdigest() == meta['content_hash']
                assert meta['etag'] == meta['content_hash']
        results.put(None)
    except Exception as exc:
        results.put(repr(exc))


class FetchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        with patch('portland_brief.fetching.urlopen', return_value=Response(b'original')):
            return Fetcher(self.root, SETTINGS, True).get(URL)

    def test_concurrent_processes_preserve_cache_bytes_and_validators(self):
        ctx = multiprocessing.get_context('spawn')
        start, results = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=concurrent_fetch, args=(str(self.root), start, results, i)) for i in range(4)]
        try:
            for process in workers:
                process.start()
            start.set()
            for process in workers:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual([results.get(timeout=2) for _ in workers], [None] * len(workers))
            cache = self.root / 'cache'
            body = (cache / (digest(URL) + '.bin')).read_bytes()
            meta = json.loads((cache / (digest(URL) + '.json')).read_text())
            self.assertEqual(hashlib.sha256(body).hexdigest(), meta['content_hash'])
            self.assertEqual(list(cache.glob('*.tmp')), [])
        finally:
            for process in workers:
                if process.is_alive():
                    process.terminate()
                process.join()
            results.close()

    def test_interrupted_pair_is_refetched_without_wrong_conditional_headers(self):
        self.seed()
        (self.root / 'cache' / (digest(URL) + '.bin')).write_bytes(b'interrupted replacement')
        seen = []

        def opening(request, **kwargs):
            seen.append(dict(request.header_items()))
            return Response(b'recovered')

        with patch('portland_brief.fetching.urlopen', side_effect=opening):
            data, meta = Fetcher(self.root, SETTINGS).get(URL)
        self.assertEqual(data, b'recovered')
        self.assertEqual(meta['status'], 'ok')
        self.assertNotIn('If-none-match', seen[0])

    def test_corrupt_pair_is_never_returned_as_stale_on_outage(self):
        self.seed()
        (self.root / 'cache' / (digest(URL) + '.bin')).write_bytes(b'wrong body')
        with patch('portland_brief.fetching.urlopen', side_effect=URLError('fixture offline')):
            with self.assertRaisesRegex(RuntimeError, 'fixture offline'):
                Fetcher(self.root, SETTINGS, True).get(URL)

    def test_outage_retains_verified_bytes_without_advancing_freshness(self):
        _, original = self.seed()
        with patch('portland_brief.fetching.urlopen', side_effect=URLError('fixture offline')):
            data, meta = Fetcher(self.root, SETTINGS, True).get(URL)
        self.assertEqual(data, b'original')
        self.assertEqual(meta['status'], 'stale_cache')
        self.assertEqual(meta['checked_epoch'], original['checked_epoch'])
        self.assertIn('fixture offline', meta['error'])

    def test_not_modified_preserves_original_bytes_and_fetch_time(self):
        _, original = self.seed()
        with patch('portland_brief.fetching.urlopen', side_effect=HTTPError(URL, 304, 'Not Modified', {}, None)):
            data, meta = Fetcher(self.root, SETTINGS, True).get(URL)
        self.assertEqual(data, b'original')
        self.assertEqual(meta['status'], 'not_modified')
        self.assertEqual(meta['fetched_at'], original['fetched_at'])

    def test_failed_replacement_cleans_unique_temporary_file(self):
        _, original = self.seed()
        with patch.object(Path, 'replace', side_effect=OSError('fixture disk error')), patch('portland_brief.fetching.urlopen', return_value=Response(b'new')):
            data, meta = Fetcher(self.root, SETTINGS, True).get(URL)
        self.assertEqual(data, b'original')
        self.assertEqual(meta['status'], 'stale_cache')
        self.assertEqual(meta['content_hash'], original['content_hash'])
        self.assertEqual(meta['checked_epoch'], original['checked_epoch'])
        self.assertEqual(list((self.root / 'cache').glob('*.tmp')), [])

    def test_interrupted_metadata_replacement_keeps_old_fallback_and_recovers(self):
        _, original = self.seed()
        replace = Path.replace

        def fail_metadata(path, target):
            if target.suffix == '.json':
                raise OSError('fixture metadata disk error')
            return replace(path, target)

        with patch.object(Path, 'replace', fail_metadata), patch('portland_brief.fetching.urlopen', return_value=Response(b'new')):
            data, meta = Fetcher(self.root, SETTINGS, True).get(URL)
        self.assertEqual(data, b'original')
        self.assertEqual(meta['status'], 'stale_cache')
        self.assertEqual(meta['content_hash'], original['content_hash'])
        self.assertEqual(meta['checked_epoch'], original['checked_epoch'])
        with patch('portland_brief.fetching.urlopen', return_value=Response(b'recovered')):
            data, meta = Fetcher(self.root, SETTINGS).get(URL)
        self.assertEqual(data, b'recovered')
        self.assertEqual(meta['status'], 'ok')

    def test_not_modified_cache_write_failure_does_not_advance_freshness(self):
        _, original = self.seed()
        with patch.object(Path, 'replace', side_effect=OSError('fixture disk error')), patch('portland_brief.fetching.urlopen', side_effect=HTTPError(URL, 304, 'Not Modified', {}, None)):
            data, meta = Fetcher(self.root, SETTINGS, True).get(URL)
        self.assertEqual(data, b'original')
        self.assertEqual(meta['status'], 'stale_cache')
        self.assertEqual(meta['checked_epoch'], original['checked_epoch'])
