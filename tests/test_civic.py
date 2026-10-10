from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from portland_brief.civic.store import CivicStore, stable_id
from portland_brief.civic.ingestion import bootstrap, ingest_agenda, ingest_rosters, ingest_index_page, scan, record_consent, reconcile_register
from portland_brief.civic import parsers
from portland_brief.civic.procedures import action_type, validate_vote, validate_readings, effective_date
from portland_brief.civic.reports import generate, validate_claims, review
from portland_brief.civic.geography import import_boundaries, tag_location, import_filing
from portland_brief.civic.capture import create_job, save_segment, finish, import_transcript, segment_by_agenda

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures'
URL = 'https://www.portland.gov/council/agenda/2026/10/7'


class CivicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CivicStore(self.root / 'civic.db')
        bootstrap(self.store, ROOT)
        self.raw = (FIX / 'portland-council-2026-10-07.html').read_bytes()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ingest(self):
        return ingest_agenda(self.store, self.raw, URL)['meeting_ids'][0]

    def test_real_meeting_readings_are_not_adoption(self):
        self.ingest()
        items = self.store.rows('appearances')
        fuel = next(r for r in items if r['agenda_number'] == '5')
        self.assertEqual(fuel['action_type'], 'reading')
        self.assertIn('second reading', fuel['official_disposition'].lower())
        final = next(r for r in items if r['agenda_number'] == '10')
        self.assertEqual(final['action_type'], 'adoption')
        self.assertEqual(len(items), 14)
        self.assertEqual(action_type('Recommend adoption', 'committee'), 'committee_recommendation')
        self.assertEqual(action_type('Passed', 'committee_of_whole'), 'committee_action')

    def test_real_amendment_votes_are_separate(self):
        self.ingest()
        fuel = next(r for r in self.store.rows('appearances') if r['agenda_number'] == '5')
        votes = [r for r in self.store.rows('vote_events') if r['appearance_id'] == fuel['id']]
        self.assertEqual(len(votes), 2)
        self.assertTrue(all(r['motion_id'] and r['origin'] == 'agenda_motion' for r in votes))
        self.assertEqual([json.loads(v['data'])['declared_tallies'] for v in votes], [{'yea': 8, 'nay': 4}, {'yea': 10, 'nay': 2}])
        self.assertEqual(len(self.store.rows('amendments')), 4)

    def test_real_final_vote_preserves_absence(self):
        self.ingest()
        fire = next(r for r in self.store.rows('appearances') if r['agenda_number'] == '13')
        event = next(r for r in self.store.rows('vote_events') if r['appearance_id'] == fire['id'])
        positions = json.loads(event['data'])['individual_votes']
        self.assertEqual(sum(v['position'] == 'absent' for v in positions), 1)
        self.assertEqual(sum(v['position'] == 'nay' for v in positions), 2)

    def test_continued_matter_connects_across_meetings(self):
        first = self.ingest()
        changed = self.raw.replace(b'relation-25679', b'relation-new-session').replace(b'2026-10-07T16:30:00Z', b'2026-11-05T22:00:00Z')
        second = ingest_agenda(self.store, changed, 'https://www.portland.gov/council/agenda/2026/11/5')['meeting_ids'][0]
        fuel = [r for r in self.store.rows('appearances') if r['agenda_number'] == '5']
        self.assertNotEqual(first, second)
        self.assertEqual(fuel[0]['matter_id'], fuel[1]['matter_id'])
        self.assertNotEqual(fuel[0]['id'], fuel[1]['id'])

    def test_title_similarity_does_not_merge_matters(self):
        a = self.store.matter('portland', '2026-1', 'https://www.portland.gov/a', 'Identical title', {})
        b = self.store.matter('portland', '2026-2', 'https://www.portland.gov/b', 'Identical title', {})
        self.assertNotEqual(a, b)

    def test_identifiers_are_distinct(self):
        self.ingest()
        row = self.store.db.execute("SELECT matter_id FROM matter_identifiers WHERE value='2026-334'").fetchone()
        identifiers = [dict(r) for r in self.store.db.execute('SELECT * FROM matter_identifiers WHERE matter_id=?', (row[0],))]
        self.assertEqual({r['namespace'] for r in identifiers}, {'official_document_number', 'source_url', 'ordinance_number'})
        self.assertIsNone(json.loads(self.store.db.execute('SELECT data FROM matters WHERE id=?', (row[0],)).fetchone()[0])['effective_date'])

    def test_consent_one_event_multiple_matters_no_invented_voters(self):
        mid = self.ingest()
        matters = [r['id'] for r in self.store.rows('matters')][:3]
        passage = self.store.rows('passages')[0]['id']
        vid = record_consent(self.store, mid, matters[:2], matters[2:], {'declared_tallies': {'yea': 12}, 'individual_votes': [], 'published_outcome': 'passed', 'removal_evidence': passage}, passage, '2026-10-07', 'portland:council')
        self.assertEqual(len([r for r in self.store.rows('vote_events') if r['id'] == vid]), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM vote_event_matters WHERE vote_event_id=? AND included=1', (vid,)).fetchone()[0], 2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM individual_votes WHERE vote_event_id=?', (vid,)).fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT review_status FROM vote_events WHERE id=?', (vid,)).fetchone()[0], 'hold')

    def test_tally_conflicts_and_duplicate_voters_hold(self):
        event = {'event_kind': 'ordinary_ordinance', 'individual_votes': [{'person_id': 'a', 'position': 'yea'}, {'person_id': 'a', 'position': 'nay'}], 'declared_tallies': {'yea': 7}, 'named_votes_complete': True, 'published_outcome': 'passed'}
        check = validate_vote(event, self.store.procedure('portland:council', '2026-10-07'), [{'person_id': 'a'}])
        self.assertEqual(check['review_status'], 'hold')
        self.assertIn('duplicate_voter_identity_or_positions', check['issues'])
        self.assertIn('named_count_tally_mismatch:yea', check['issues'])

    def test_body_specific_thresholds_and_mayor_hold(self):
        council = self.store.procedure('portland:council', '2026-10-07')
        event = {'event_kind': 'emergency_ordinance', 'declared_tallies': {'yea': 8}, 'published_outcome': 'passed'}
        check = validate_vote(event, council, [])
        self.assertFalse(check['calculated_passage_check'])
        self.assertIn('calculated_outcome_conflicts_with_official_action', check['issues'])
        event.update(event_kind='ordinary_ordinance', declared_tallies={'yea': 6, 'nay': 6})
        self.assertIn('mayoral_tiebreak_requires_explicit_evidence', validate_vote(event, council, [])['issues'])
        county = self.store.procedure('multnomah:board', '2026-10-07')
        event.update(event_kind='ordinary_motion', declared_tallies={'yea': 3})
        self.assertTrue(validate_vote(event, county, [])['calculated_passage_check'])

    def test_historical_rules_and_rosters_not_applied_backward(self):
        ingest_rosters(self.store, (FIX / 'portland-committees-2026-10-09.html').read_bytes(), 'https://www.portland.gov/council/policy-committees', '2026-10-09')
        self.assertEqual(self.store.membership('portland:council', '2026-10-07'), [])
        self.assertEqual(len(self.store.membership('portland:council', '2026-10-09')), 12)
        self.assertIsNone(self.store.procedure('portland:council', '2025-01-01'))
        self.assertEqual(len(self.store.rows('bodies')), 7)

    def test_timing_does_not_guess_effective_date(self):
        rule = self.store.procedure('portland:council', '2026-10-07')
        self.assertIn('readings_too_close', validate_readings('2026-10-07', '2026-10-09', rule))
        self.assertIn('amendment_timing_requires_review', validate_readings('2026-10-01', '2026-10-07', rule, '2026-10-07'))
        self.assertIsNone(effective_date(adoption_date='2026-10-07')['date'])

    def test_outage_creates_no_removal_or_source_change(self):
        self.ingest()
        before = len(self.store.rows('changes'))
        self.store.failed_retrieval('meetings', URL, 'timeout')
        self.assertEqual(len(self.store.rows('changes')), before)
        self.assertEqual(len(self.store.rows('appearances')), 14)

    def test_attachment_at_same_url_is_versioned(self):
        url = 'https://www.portland.gov/files/attachment.pdf'
        a, _ = self.store.source_version(url, b'PDF version 1', 'attachment-v1', {'sha': hashlib.sha256(b'PDF version 1').hexdigest()})
        b, changed = self.store.source_version(url, b'PDF version 2', 'attachment-v1', {'sha': hashlib.sha256(b'PDF version 2').hexdigest()})
        self.assertNotEqual(a, b)
        self.assertTrue(changed)
        self.assertEqual(len(self.store.rows('source_versions')), 2)

    def test_cosmetic_change_no_alert(self):
        self.store.source_version(URL, b'<p>A</p>', 'test', {'text': 'A'})
        _, changed = self.store.source_version(URL, b'<p class="new">A</p>', 'test', {'text': 'A'})
        self.assertFalse(changed)
        self.assertEqual(len(self.store.rows('changes')), 0)

    def test_missing_recording_document_summary_has_valid_evidence(self):
        mid = self.ingest()
        result = generate(self.store, mid, self.root / 'output')
        self.assertEqual(set(result), {'before', 'after', 'follow-through'})
        for report in self.store.rows('report_versions'):
            validate_claims(self.store, json.loads(report['claims']), json.loads(report['evidence_bundle']))
            self.assertIn('document-based', report['article'])
        with self.assertRaises(ValueError):
            validate_claims(self.store, [{'fact_id': 'fabricated', 'value': 'city approved'}])

    def test_changed_evidence_invalidates_approval(self):
        mid = self.ingest()
        report = generate(self.store, mid, self.root / 'output')['after']['report_id']
        review(self.store, report, 'approve', 'Fixture editor', 'Checked official excerpts; historical roster gap noted', 120)
        self.assertEqual(self.store.db.execute('SELECT state FROM meeting_reports WHERE id=?', (report,)).fetchone()[0], 'approved')
        new = self.raw.replace(b'Passed to second reading as amended', b'Rescheduled after amendment')
        ingest_agenda(self.store, new, URL)
        self.assertEqual(self.store.db.execute('SELECT state FROM meeting_reports WHERE id=?', (report,)).fetchone()[0], 'needs_review')
        with self.assertRaises(ValueError):
            review(self.store, report, 'approve', 'Editor', 'Old evidence', 30)

    def test_unchanged_reprocessing_preserves_editorial_corrections(self):
        mid = self.ingest()
        report = generate(self.store, mid, self.root / 'output')['after']['report_id']
        row = self.store.db.execute('SELECT * FROM report_versions WHERE report_id=?', (report,)).fetchone()
        claims = json.loads(row['claims'])[:2]
        review(self.store, report, 'correct', 'Editor', 'Keep only checked items', 90, {'claims': claims})
        edited = self.store.db.execute('SELECT current_version FROM meeting_reports WHERE id=?', (report,)).fetchone()[0]
        self.ingest()
        result = generate(self.store, mid, self.root / 'output')
        self.assertEqual(result['after']['version'], edited)
        self.assertEqual(json.loads(self.store.db.execute('SELECT claims FROM report_versions WHERE id=?', (edited,)).fetchone()[0]), claims)

    def test_reprocessing_unchanged_no_duplicate_records_or_posts(self):
        mid = self.ingest(); generate(self.store, mid, self.root / 'output')
        before = {t: len(self.store.rows(t)) for t in ['matters', 'appearances', 'motions', 'vote_events', 'individual_votes', 'meeting_reports', 'report_versions']}
        self.ingest(); generate(self.store, mid, self.root / 'output')
        self.assertEqual(before, {t: len(self.store.rows(t)) for t in before})

    def test_register_is_independent_of_amendment_event(self):
        self.ingest()
        ingest_index_page(self.store, (FIX / 'portland-votes-first-page.html').read_bytes(), 'https://www.portland.gov/council/votes', 'vote_register')
        origins = {r['origin'] for r in self.store.rows('vote_events')}
        self.assertIn('official_document_register', origins)
        self.assertIn('agenda_motion', origins)
        self.assertTrue(all(json.loads(r['data'])['register_partial'] for r in self.store.rows('vote_events') if r['origin'] == 'official_document_register'))
        checks = reconcile_register(self.store)
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(c['status'] == 'consistent_document_crosscheck' for c in checks))

    def test_parser_changes_require_explicit_version_and_citations_cannot_change(self):
        self.store.source_version(URL, b'unchanged', 'parser-v1', {'value': 'one'})
        with self.assertRaises(ValueError):
            self.store.source_version(URL, b'unchanged', 'parser-v1', {'value': 'two'})
        self.store.source_version(URL, b'unchanged', 'parser-v2', {'value': 'two'})
        mid = self.ingest()
        report = generate(self.store, mid, self.root / 'output')
        version = self.store.rows('report_versions')[0]
        claim = json.loads(version['claims'])[0]
        with self.assertRaises(ValueError):
            validate_claims(self.store, [{**claim, 'source_url': 'https://invented.invalid/claim'}])

    def test_incomplete_pagination_explicit(self):
        class Fetch:
            def get(_, url, kind):
                return (FIX / 'portland-current-documents.html').read_bytes(), {'status': 'ok'}
        result = scan(self.store, Fetch(), {'id': 'active', 'pipeline': 'meetings', 'type': 'document_index', 'url': 'https://www.portland.gov/council/documents'}, 1)
        self.assertFalse(result['pagination_complete'])
        self.assertEqual(result['expected_count'], 49)
        self.assertEqual(result['records'], 25)
        self.assertTrue(result['next_url'])

    def test_county_sessions_not_city_and_calendar_discovery_without_agenda(self):
        rows, _, _ = ingest_index_page(self.store, (FIX / 'portland-calendar.html').read_bytes(), 'https://www.portland.gov/auditor/council-clerk/events', 'calendar')
        self.assertEqual(len(rows), 20)
        county = parsers.county_archive((FIX / 'multnomah-archive.html').read_bytes(), 'https://multnomah.granicus.com/ViewPublisher.php?view_id=3')
        self.assertIn('executive_session', {r['kind'] for r in county})
        self.assertIn('briefing', {r['kind'] for r in county})
        self.assertIn('regular', {r['kind'] for r in county})
        self.assertTrue(all(r['coverage_status'] == 'discovered' for r in self.store.rows('meetings')))

    def boundaries(self):
        features = []
        for name, x in [('West', 0), ('East', 1)]:
            features.append({'type': 'Feature', 'properties': {'NAME': name}, 'geometry': {'type': 'Polygon', 'coordinates': [[[x,0],[x+1,0],[x+1,1],[x,1],[x,0]]]}})
        data = json.dumps({'type': 'FeatureCollection', 'features': features}).encode()
        import_boundaries(self.store, data, 'neighborhood', 'https://www.portlandmaps.com/boundary-fixture', 2)

    def test_neighborhood_multiple_areas_and_boundary_ambiguity(self):
        self.boundaries()
        tags = tag_location(self.store, 'project', 'x', {'type': 'Point', 'coordinates': [1,.5]}, 'official-address-geometry')
        self.assertEqual({t['area_id'] for t in tags}, {'West', 'East'})
        self.assertTrue(all(t['data']['ambiguous'] for t in tags))
        tags = tag_location(self.store, 'project', 'y', {'type': 'LineString', 'coordinates': [[.5,.5],[1.5,.5]]}, 'official-route')
        self.assertEqual(len(tags), 2)
        unknown = tag_location(self.store, 'project', 'z')
        self.assertEqual(unknown[0]['relation'], 'unknown')
        with self.assertRaises(ValueError):
            tag_location(self.store, 'project', 'bad', relation='explicitly_affected', explicit_areas=['West'])

    def test_filing_stages_separate_and_revisions_idempotent(self):
        row = {'case_identifier': 'LU-26-12345', 'stage': 'application', 'evidence_text': 'Application received'}
        a = import_filing(self.store, row, b'v1', 'https://www.portland.gov/notice')
        b = import_filing(self.store, row, b'v1', 'https://www.portland.gov/notice')
        self.assertEqual(a['filing_id'], b['filing_id'])
        c = import_filing(self.store, {**row, 'stage': 'approval', 'evidence_text': 'Approved application'}, b'v2', 'https://www.portland.gov/decision')
        self.assertNotEqual(a['filing_id'], c['filing_id'])
        self.assertEqual({r['stage'] for r in self.store.rows('filing_events')}, {'application', 'approval'})

    def test_capture_disconnect_not_meeting_completion(self):
        mid = self.ingest()
        job = create_job(self.store, mid, 'https://multnomah.granicus.com/fixture-stream', 'live')
        audio = self.root / 'segment.wav'; audio.write_bytes(b'fixture')
        save_segment(self.store, job, audio, 0, 30)
        data = finish(self.store, job)
        self.assertFalse(data['complete'])
        self.assertEqual(data['audio_minutes'], .5)
        self.assertEqual(self.store.rows('capture_jobs')[0]['state'], 'interrupted')

    def test_capture_missing_intervals_and_simultaneous_job_ids(self):
        mid = self.ingest()
        a = create_job(self.store, mid, 'https://multnomah.granicus.com/a')
        b = create_job(self.store, mid, 'https://multnomah.granicus.com/b')
        self.assertNotEqual(a, b)
        audio = self.root / 'segment.wav'; audio.write_bytes(b'fixture')
        save_segment(self.store, a, audio, 0, 30); save_segment(self.store, a, audio, 60, 90)
        data = finish(self.store, a, {'verified_end': True})
        self.assertFalse(data['complete'])
        self.assertEqual(data['missing_intervals'][0], {'start_seconds': 30.0, 'end_seconds': 60.0})

    def test_raw_transcript_speaker_identity_never_guessed(self):
        mid = self.ingest()
        payload = {'automatic': True, 'segments': [{'start_seconds': 0, 'end_seconds': 30, 'text': 'Speaker said council approved it', 'speaker': 'speaker 1'}]}
        import_transcript(self.store, mid, payload, 'https://www.portland.gov/transcript-fixture')
        row = self.store.rows('transcript_segments')[0]
        self.assertEqual(row['raw_speaker'], 'speaker 1')
        self.assertIsNone(row['person_id'])
        self.assertIsNone(row['corrected_text'])
        self.assertEqual(row['attribution_status'], 'uncertain')
        fact = next(f for f in self.store.rows('facts') if f['predicate'] == 'raw_transcript')
        with self.assertRaises(ValueError):
            validate_claims(self.store, [{'fact_id': fact['id'], 'value': json.loads(fact['value'])}])

    def test_context_segmentation_preserves_agenda_boundaries(self):
        segments = [{'id': str(i), 'start_seconds': i*10, 'end_seconds': (i+1)*10, 'text': 'x'*60, 'agenda_id': 'a' if i<2 else 'b'} for i in range(4)]
        batches = segment_by_agenda(segments, 120)
        self.assertEqual(len(batches), 2)
        self.assertEqual([s['id'] for b in batches for s in b], ['0','1','2','3'])

    def test_backup_restore_integrity(self):
        self.ingest()
        target = sqlite3.connect(self.root / 'backup.db'); self.store.db.backup(target)
        self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(target.execute('SELECT COUNT(*) FROM appearances').fetchone()[0], 14)
        target.close()


if __name__ == '__main__':
    unittest.main()
