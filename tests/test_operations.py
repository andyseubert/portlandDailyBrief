from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave

from portland_brief.collection import NewsBuffer
from portland_brief.records import Evidence
from portland_brief.selection import select
from portland_brief.scheduling import next_local_daily, tick
from portland_brief.civic.store import CivicStore
from portland_brief.civic.ingestion import bootstrap, ingest_agenda, extract_attachment, ingest_index_page, ingest_county_agenda
from portland_brief.civic.capture import create_job, run_capture
from portland_brief.civic.geography import parse_notice

ROOT = Path(__file__).resolve().parents[1]


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_scheduler_preserves_seven_am_across_dst(self):
        spring = next_local_daily(datetime(2026,3,7,16,tzinfo=timezone.utc))
        fall = next_local_daily(datetime(2026,10,31,16,tzinfo=timezone.utc))
        self.assertEqual(spring.isoformat(), '2026-03-08T14:00:00+00:00')
        self.assertEqual(fall.isoformat(), '2026-11-01T15:00:00+00:00')

    def test_nonexistent_local_schedule_time_skipped(self):
        nextdate = next_local_daily(datetime(2026,3,8,8,tzinfo=timezone.utc), '02:30')
        self.assertEqual(nextdate.date().isoformat(), '2026-03-09')

    def test_scheduled_pipeline_failure_does_not_stop_others(self):
        (self.root / 'config').mkdir()
        shutil.copy(ROOT / 'config/tasks.json', self.root / 'config/tasks.json')
        config_path = self.root / 'config/tasks.json'
        config = json.loads(config_path.read_text())
        config['repository_archive']['enabled'] = False
        config_path.write_text(json.dumps(config))
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if 'collect-news' in command else 0)
        result = tick(self.root, True, datetime(2026,10,9,21,tzinfo=timezone.utc), runner)
        self.assertEqual(len(calls), 6)
        states = {j['job']: j['state'] for j in result['results']}
        self.assertEqual(states['news-collect'], 'retry')
        self.assertEqual(states['meetings-discovery'], 'pending')
        self.assertEqual(states['filings-collect'], 'pending')
        result2 = tick(self.root, True, datetime(2026,10,9,21,1,tzinfo=timezone.utc), runner)
        self.assertEqual(result2['results'], [])

    def test_news_late_arrival_checkpoint_and_no_cross_date_repeat(self):
        buffer = NewsBuffer(self.root / 'news.db')
        now = datetime(2026,10,9,14,tzinfo=timezone.utc)
        item = Evidence('late','city','City','Portland closure','https://www.portland.gov/late',(now-timedelta(hours=30)).isoformat(),now.isoformat(),'A Portland road closure will affect residents using this route during scheduled maintenance work.')
        buffer.db.execute('INSERT INTO syntheses VALUES(?,?)', ('2026-10-08', (now-timedelta(hours=24)).isoformat())); buffer.db.commit()
        buffer.record([item], [{'source_id':'city','status':'ok'}], now)
        candidates, late = buffer.candidates(now, '2026-10-09')
        self.assertEqual(late, ['late'])
        self.assertIn('late-arriving', candidates[0].context)
        selected, _ = select(candidates,now,{'window_hours':24,'story_limit':10,'per_source_limit':2,'late_arrival_ids':late})
        self.assertEqual(len(selected),1)
        buffer.mark_generated([{'citation_ids':['late']}],{'late':item.to_dict()},'2026-10-09',now)
        self.assertEqual(buffer.candidates(now+timedelta(hours=24),'2026-10-10')[0], [])
        self.assertEqual(len(buffer.candidates(now,'2026-10-09')[0]), 1)
        buffer.close()

    def test_failed_collection_does_not_advance_success_checkpoint(self):
        buffer = NewsBuffer(self.root / 'news.db')
        now = datetime.now(timezone.utc)
        buffer.record([], [{'source_id':'city','status':'ok'}], now)
        buffer.record([], [{'source_id':'city','status':'failed'}], now+timedelta(hours=1))
        self.assertEqual(buffer.db.execute('SELECT at FROM checkpoints').fetchone()[0], now.isoformat())
        buffer.close()

    def test_real_neighborhood_notice_preserves_address_deadline_and_stage(self):
        data = (ROOT / 'tests/fixtures/portland-land-use-2026-10-09.html').read_bytes()
        parsed = parse_notice(data, 'https://www.portland.gov/ppd/zoning-land-use/news/2026/10/9/notice-land-use-proposal-lu-26-071183-ad')
        self.assertEqual(parsed['stage'], 'proposal')
        self.assertEqual(parsed['address'], '7640 N Delaware Ave')
        self.assertEqual(parsed['explicit_affected_neighborhoods'], ['Kenton'])
        self.assertEqual(parsed['comment_deadline_raw'], 'October 30, 2026 5:00 PM')
        self.assertIn('Comment Deadline', parsed['evidence_text'])
        self.assertNotIn('Give website feedback', parsed['evidence_text'])

    def test_real_attachment_page_and_conflicting_label(self):
        store = CivicStore(self.root / 'civic.db'); bootstrap(store,ROOT)
        data = (ROOT / 'tests/fixtures/portland-2026-312-kanal-amendment-2.pdf').read_bytes()
        version, _ = store.source_version('https://www.portland.gov/kanal.pdf',data,'attachment-bytes-v1',{'hash':'fixture'})
        store.upsert('documents',id='attachment',matter_id=None,source_url='https://www.portland.gov/kanal.pdf',kind='attachment',data={'source_version':version,'label':'Approved amendment - Kanal 2 (October 7, 2026)'})
        result = extract_attachment(store,'attachment')
        self.assertEqual(result['pages'],1)
        self.assertIn('attachment_label_text_mismatch_requires_review',result['warnings'])
        passage = store.rows('passages')[0]
        self.assertEqual(passage['locator'],'PDF page 1')
        self.assertIn('March 31, 2027',passage['text'])
        store.close()

    def test_real_county_agenda_proposals_do_not_create_votes(self):
        store = CivicStore(self.root / 'civic.db'); bootstrap(store,ROOT)
        ingest_index_page(store,(ROOT / 'tests/fixtures/multnomah-archive.html').read_bytes(),'https://multnomah.granicus.com/ViewPublisher.php?view_id=3','granicus')
        raw = (ROOT / 'tests/fixtures/multnomah-2026-10-15-agenda.html').read_bytes()
        result = ingest_county_agenda(store,raw,'https://multnomah.granicus.com/AgendaViewer.php?view_id=3&event_id=3999')
        self.assertEqual(result['agenda_items'],4)
        self.assertEqual(result['consent_candidates'],3)
        self.assertEqual(store.rows('vote_events'),[])
        appearances = store.rows('appearances')
        self.assertTrue(all(a['official_disposition']=='' for a in appearances))
        self.assertTrue(all(a['action_type']=='unknown' for a in appearances))
        self.assertTrue(all(m['jurisdiction_id']=='multnomah' for m in store.rows('matters')))
        store.close()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Optional capture dependency unavailable')
    def test_real_ffmpeg_capture_persists_audio_and_is_idempotent(self):
        store = CivicStore(self.root / 'civic.db'); bootstrap(store,ROOT)
        mid = ingest_agenda(store,(ROOT / 'tests/fixtures/portland-council-2026-10-07.html').read_bytes(),'https://www.portland.gov/council/agenda/2026/10/7')['meeting_ids'][0]
        audio = self.root / 'input-fixture.wav'
        with wave.open(str(audio),'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 32000)
        job = create_job(store,mid,str(audio),'local')
        result = run_capture(store,job,maximum_seconds=10)
        self.assertTrue(result['complete'])
        self.assertGreater(result['storage_bytes'],0)
        count = len(store.rows('capture_segments'))
        self.assertEqual(run_capture(store,job,10)['complete'],True)
        self.assertEqual(len(store.rows('capture_segments')),count)
        store.close()


if __name__ == '__main__':
    unittest.main()
