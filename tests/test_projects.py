import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from portland_brief.civic.projects import address_parts, lookup
from portland_brief.civic.store import CivicStore

FIXTURE = Path(__file__).parent / 'fixtures/portland-permits-6866-se-87th.json'


class Fetcher:
    def __init__(self, response):
        self.response = response
        self.status = 'ok'

    def get(self, url):
        self.url = url
        return json.dumps(self.response).encode(), {
            'status': self.status, 'fetched_at': '2026-10-11T02:57:00+00:00'}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CivicStore(Path(self.temp.name) / 'civic.sqlite3')
        self.fetcher = Fetcher(json.loads(FIXTURE.read_text()))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_lookup(self):
        return lookup(self.store, self.fetcher, '6866 SE 87TH AVE')

    def test_real_deferred_submittal_retains_identity_and_status(self):
        result = self.run_lookup()
        record, = result['records']
        self.assertEqual(record['official_application_number'], '2025-020413-DFS-01-RS')
        self.assertEqual(record['official_status'], 'Issued')
        self.assertEqual(record['implementation_status'], 'unknown')
        self.assertIn('FIRE SPRINKLERS', record['official_fields']['DESCRIPTION'])
        self.assertEqual(record['source_url'], 'https://www.portlandmaps.com/detail/permit/5289977_did/')
        self.assertEqual(len(self.store.rows('passages')), 1)
        self.assertFalse(result['published'])

    def test_unchanged_inputs_have_one_evidence_version(self):
        first = self.run_lookup()
        second = self.run_lookup()
        self.assertEqual(first['source_version'], second['source_version'])
        self.assertEqual(first['records'], second['records'])
        self.assertFalse(second['material_change'])
        self.assertEqual(len(self.store.rows('source_versions')), 1)
        self.assertEqual(len(self.store.rows('passages')), 1)
        self.assertEqual(self.store.rows('matters'), [])
        self.assertEqual(self.store.rows('meeting_reports'), [])

    def test_house_substring_does_not_match_another_address(self):
        wrong = copy.deepcopy(self.fetcher.response['features'][0])
        wrong['attributes']['HOUSE'] = ' 16866'
        self.fetcher.response['features'].append(wrong)
        self.assertEqual(len(self.run_lookup()['records']), 1)

    def test_duplicate_official_identity_is_held(self):
        first = self.run_lookup()
        self.fetcher.response['features'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.run_lookup()
        self.assertEqual(self.store.rows('source_current')[0]['version_id'], first['source_version'])
        self.assertEqual(self.store.rows('retrievals')[-1]['status'], 'failed')

    def test_changed_same_endpoint_preserves_new_evidence_version(self):
        first = self.run_lookup()
        self.fetcher.response['features'][0]['attributes']['STATUS'] = 'Under Review'
        second = self.run_lookup()
        self.assertTrue(second['material_change'])
        self.assertNotEqual(first['source_version'], second['source_version'])
        self.assertEqual(len(self.store.rows('source_versions')), 2)
        self.assertEqual(first['records'][0]['id'], second['records'][0]['id'])
        self.assertEqual(second['records'][0]['implementation_status'], 'unknown')

    def test_record_cap_is_visibly_partial(self):
        self.fetcher.response['exceededTransferLimit'] = True
        self.assertEqual(self.run_lookup()['status'], 'partial')

    def test_service_error_and_stale_cache_preserve_previous_evidence(self):
        first = self.run_lookup()
        missing = json.loads(FIXTURE.read_text())
        del missing['features'][0]['attributes']['APPLICATION']
        for response, status in [({'error': {'code': 400}}, 'ok'), (missing, 'ok'), ([], 'ok'),
                                 (json.loads(FIXTURE.read_text()), 'stale_cache')]:
            self.fetcher.response, self.fetcher.status = response, status
            with self.assertRaises((ValueError, RuntimeError)):
                self.run_lookup()
            self.assertEqual(self.store.rows('source_current')[0]['version_id'], first['source_version'])

    def test_query_validation_and_sql_quoting(self):
        self.assertEqual(address_parts(' 6866 se 87th avenue '), ('6866', 'SE', '87TH'))
        for address in ['6866 SE 87TH%; DELETE', 'SW 4TH & HARRISON', '6866 SE 87TH\n--', '']:
            with self.assertRaises(ValueError):
                address_parts(address)
        lookup(self.store, self.fetcher, "6866 SE O'CONNOR RD")
        where = parse_qs(urlsplit(self.fetcher.url).query)['where'][0]
        self.assertIn("PROPSTREET = 'O''CONNOR'", where)


if __name__ == '__main__':
    unittest.main()
