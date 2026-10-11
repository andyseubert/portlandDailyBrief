import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from portland_brief.civic.developments import classify, scan, intake_row, crosscheck_intake
from portland_brief.civic.store import CivicStore

FIX = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
SOURCE = {'id': 'portland-commercial-construction', 'url': 'https://www.portlandmaps.com/arcgis/rest/services/Public/BDS_Permit/MapServer/2'}


class Fetcher:
    def __init__(self):
        self.pages = [json.loads((FIX / f'portland-commercial-construction-page-{n}.json').read_text()) for n in [1, 2]]
        self.count = 141
        self.failed = False

    def get(self, url):
        if self.failed:
            raise RuntimeError('source unavailable')
        params = parse_qs(urlsplit(url).query)
        response = {'count': self.count} if 'returnCountOnly' in params else self.pages[int(params['resultOffset'][0]) // 100]
        return json.dumps(response).encode(), {'fetched_at': NOW.isoformat(), 'status': 'ok'}


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CivicStore(Path(self.temp.name) / 'civic.sqlite3')
        self.output = Path(self.temp.name) / 'review'
        self.fetcher = Fetcher()
        self.attrs = {f['attributes']['FOLDERKEY']: f['attributes']
                      for page in self.fetcher.pages for f in page['features']}

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def run_scan(self, **cfg):
        return scan(self.store, self.fetcher, SOURCE, cfg, self.output, NOW)

    def test_real_paged_inventory_and_candidates(self):
        result = self.run_scan()
        self.assertTrue(result['complete']); self.assertEqual(result['pages'], 2)
        self.assertEqual(result['processed_records'], 141)
        rows = self.store.rows('development_records')
        matches = [r['folder_id'] for r in rows if json.loads(r['data'])['assessment']['status'] == 'matches_filter']
        self.assertEqual(sorted(matches), [5243125, 5273121])
        self.assertEqual(result['screening_counts']['hold_conflict'], 1)
        self.assertEqual(result['screening_counts']['needs_evidence'], 62)
        self.assertEqual(self.store.rows('meeting_reports'), [])

    def test_numeric_zero_does_not_overrule_explicit_project_height(self):
        result = classify(self.attrs[5273121])
        self.assertEqual(result['stories'], 4)
        self.assertEqual(result['status'], 'matches_filter')

    def test_threshold_can_change_without_changing_official_source_extraction(self):
        self.run_scan()
        sources = len(self.store.rows('source_versions'))
        result = self.run_scan(minimum_stories=5)
        self.assertTrue(result['complete'])
        self.assertEqual(result['screening_counts']['matches_filter'], 1)
        self.assertEqual(len(self.store.rows('source_versions')), sources)

    def test_conflicting_height_is_held(self):
        result = classify(self.attrs[5228969])
        self.assertEqual(result['height_in_description'], [4])
        self.assertEqual(result['height_numeric_field'], 3)
        self.assertEqual(result['status'], 'hold_conflict')

    def test_support_offices_are_not_a_business_component(self):
        self.assertEqual(classify(self.attrs[5235024])['status'], 'needs_evidence')

    def test_below_threshold_and_unverified_zoning_height(self):
        self.assertEqual(classify(self.attrs[5275632])['status'], 'excluded')
        attrs = {**self.attrs[5273121], 'DESCRIPTION': 'New apartment building with commercial space. Zoning allows a four-story building.', 'NUMBSTORIES': None}
        self.assertEqual(classify(attrs)['status'], 'needs_evidence')
        attrs['DESCRIPTION'] = 'New 4-story apartments. Nearby retail shops.'
        self.assertEqual(classify(attrs)['status'], 'needs_evidence')
        attrs['DESCRIPTION'] = 'New 4-story apartments with no retail space.'
        self.assertEqual(classify(attrs)['status'], 'excluded')

    def test_component_permits_are_not_whole_developments(self):
        attrs = {**self.attrs[5273121], 'FOLDERTYPE': 'FA', 'WORK_DESCRIPTION': 'Alteration'}
        self.assertEqual(classify(attrs)['status'], 'excluded')

    def test_repeat_and_cosmetic_updates_preserve_record_versions(self):
        self.run_scan()
        before = [r for r in self.store.rows('development_versions')]
        changes = len(self.store.rows('changes'))
        self.run_scan()
        self.assertEqual(self.store.rows('development_versions'), before)
        self.assertEqual(len(self.store.rows('changes')), changes)
        self.assertEqual(len(self.store.rows('changes')), changes)
        for page in self.fetcher.pages:
            for f in page['features']:
                f['attributes']['DESCRIPTION'] = '  ' + f['attributes']['DESCRIPTION'] + '  '
                f['attributes']['OBJECTID'] += 100000
        self.run_scan()
        self.assertEqual(self.store.rows('development_versions'), before)
        self.assertEqual(len(self.store.rows('changes')), changes)

    def test_material_change_creates_evidence_version_and_reopens_review(self):
        self.run_scan()
        self.store.db.execute("UPDATE development_records SET state='approved' WHERE folder_id=5243125")
        self.attrs[5243125]['STATUS'] = 'Issued'
        self.run_scan()
        row = self.store.db.execute('SELECT * FROM development_records WHERE folder_id=5243125').fetchone()
        self.assertEqual(row['state'], 'needs_review')
        self.assertEqual(len(self.store.rows('development_versions')), 142)
        self.assertEqual(json.loads(row['data'])['implementation_status'], 'unknown')

    def test_outage_preserves_records_draft_and_checkpoint(self):
        self.run_scan(); before = (self.output / 'watch.md').read_bytes()
        checkpoint = self.store.rows('collection_checkpoints')
        self.fetcher.failed = True
        result = self.run_scan()
        self.assertFalse(result['complete']); self.assertTrue(result['errors'])
        self.assertEqual(len(self.store.rows('development_records')), 141)
        self.assertEqual((self.output / 'watch.md').read_bytes(), before)
        self.assertEqual(self.store.rows('collection_checkpoints'), checkpoint)

    def test_page_cap_and_missing_records_do_not_imply_withdrawal(self):
        self.run_scan()
        result = self.run_scan(max_pages=1)
        self.assertFalse(result['complete']); self.assertEqual(result['processed_records'], 100)
        self.assertEqual(len(self.store.rows('development_records')), 141)
        self.assertIn('PARTIAL', (self.output / 'watch.md').read_text())

    def test_duplicate_id_and_count_mismatch_remain_partial(self):
        self.fetcher.pages[1]['features'][0] = copy.deepcopy(self.fetcher.pages[0]['features'][0])
        result = self.run_scan()
        self.assertFalse(result['complete']); self.assertTrue(result['errors'])
        self.assertEqual(self.store.rows('collection_checkpoints'), [])
        self.fetcher = Fetcher(); self.fetcher.count = 142
        self.assertFalse(self.run_scan()['complete'])

    def test_draft_claims_link_to_current_passages_and_escape_html(self):
        self.attrs[5273121]['STATUS'] = '<script>untrusted()</script>'
        self.run_scan()
        self.assertNotIn('<script>', (self.output / 'watch.html').read_text())
        self.assertIn('&lt;script&gt;', (self.output / 'watch.html').read_text())
        for row in self.store.rows('development_records'):
            passage = self.store.db.execute('SELECT * FROM passages WHERE id=?', (row['passage_id'],)).fetchone()
            self.assertEqual(passage['version_id'], row['source_version'])
            self.assertEqual(json.loads(passage['text']), json.loads(row['data'])['attributes'])

    def test_intake_address_disagreement_is_held_and_versioned(self):
        self.run_scan()
        raw = (FIX / 'portland-commercial-intake-5243125.html').read_bytes()
        class Intake:
            def get(self, url, kind):
                return raw, {'status': 'ok', 'fetched_at': NOW.isoformat()}
        record = dict(self.store.db.execute('SELECT * FROM development_records WHERE folder_id=5243125').fetchone())
        result = crosscheck_intake(self.store, Intake(), record, '2025-10-10', NOW)
        self.assertEqual(result, 'conflict')
        row = dict(self.store.db.execute('SELECT * FROM development_records WHERE id=?', (record['id'],)).fetchone())
        self.assertEqual(row['state'], 'hold')
        self.assertIn('3921 SE DIVISION', json.loads(row['data'])['intake_crosscheck']['facts']['row']['address'])
        count = len(self.store.rows('development_versions'))
        crosscheck_intake(self.store, Intake(), row, '2025-10-10', NOW)
        self.assertEqual(len(self.store.rows('development_versions')), count)
        class FailedIntake:
            def get(self, url, kind):
                raise RuntimeError('report unavailable')
        self.assertEqual(crosscheck_intake(self.store, FailedIntake(), row, '2025-10-10', NOW), 'unavailable')
        preserved = self.store.db.execute('SELECT state,data FROM development_records WHERE id=?', (row['id'],)).fetchone()
        self.assertEqual(preserved['state'], 'hold')
        self.assertEqual(preserved['data'], row['data'])

    def test_intake_missing_row_is_not_a_withdrawal(self):
        raw = (FIX / 'portland-commercial-intake-5243125.html').read_bytes()
        self.assertIsNone(intake_row(raw, 5273121))
        row = intake_row(raw, 5243125)
        self.assertEqual(row['folder_id'], 5243125)
        self.assertEqual(row['status'], 'Under Review')


if __name__ == '__main__':
    unittest.main()
