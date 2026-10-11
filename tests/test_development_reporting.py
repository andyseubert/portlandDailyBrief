import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from portland_brief.civic.store import CivicStore
from portland_brief.civic.developments import scan
from portland_brief.civic.development_reports import generate, review, approved_watch, validate_snapshot, validate_claims
from portland_brief.civic.development_evidence import report_rows, research
from portland_brief.civic.geography import notice_links, parse_notice
from portland_brief.civic.notices import collect

FIX = Path(__file__).parent/'fixtures'
NOW = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)
SOURCE = {'id':'permits','url':'https://www.portlandmaps.com/arcgis/rest/services/Public/BDS_Permit/MapServer/2'}


class PermitFetcher:
    def __init__(self):
        self.pages = [json.loads((FIX/f'portland-commercial-construction-page-{i}.json').read_text()) for i in [1,2]]

    def get(self, url, kind='json'):
        q = parse_qs(urlsplit(url).query)
        if 'returnCountOnly' in q: obj={'count':141}
        else: obj=self.pages[int(q['resultOffset'][0])//100]
        return json.dumps(obj).encode(), {'fetched_at':NOW.isoformat(),'status':'ok'}


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.store=CivicStore(self.root/'civic.db'); self.fetcher=PermitFetcher()
        scan(self.store,self.fetcher,SOURCE,{},self.root/'watch',NOW)
        self.reports=generate(self.store,self.root/'review')
        self.gibbs=next(r for r in self.reports if r['folder_id']==5273121)['report_id']
        self.marshall=next(r for r in self.reports if r['folder_id']==5228969)['report_id']

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def test_pending_drafts_never_enter_daily_watch_and_height_hold_blocks_approval(self):
        self.assertEqual(approved_watch(self.store,NOW),[])
        with self.assertRaisesRegex(ValueError,'contradictory'):
            review(self.store,self.marshall,'approve','Fixture editor','checked',90)
        self.assertEqual(self.store.rows('development_review_audit'),[])

    def test_approved_snapshot_is_cited_and_tampering_is_rejected(self):
        review(self.store,self.gibbs,'approve','Fixture editor','Compared official fields',120)
        snapshots=approved_watch(self.store,NOW)
        self.assertEqual(len(snapshots),1)
        stamp=datetime.now(timezone.utc).isoformat()
        validate_snapshot(self.store,snapshots[0],stamp)
        wrong=copy.deepcopy(snapshots[0]);wrong['claims'][0]['value']='Construction completed'
        with self.assertRaisesRegex(ValueError,'reviewed version'):
            validate_snapshot(self.store,wrong,stamp)
        self.assertEqual(approved_watch(self.store,NOW+timedelta(hours=25)),[])
        self.assertEqual(approved_watch(self.store,NOW-timedelta(seconds=1)),[])

    def test_material_change_reopens_approval_and_unchanged_rerun_is_idempotent(self):
        review(self.store,self.gibbs,'approve','Fixture editor','checked',60)
        before=len(self.store.rows('development_report_versions'))
        generate(self.store,self.root/'review')
        self.assertEqual(len(self.store.rows('development_report_versions')),before)
        self.assertEqual(len(approved_watch(self.store,NOW)),1)
        for page in self.fetcher.pages:
            for feature in page['features']:
                if feature['attributes']['FOLDERKEY']==5273121:feature['attributes']['STATUS']='Issued'
        scan(self.store,self.fetcher,SOURCE,{},self.root/'watch',NOW+timedelta(minutes=1))
        self.assertEqual(approved_watch(self.store,NOW+timedelta(minutes=1)),[])
        self.assertTrue(any(r['action']=='approval_invalidated' for r in self.store.rows('development_review_audit')))
        generate(self.store,self.root/'review')
        self.assertEqual(len(self.store.rows('development_reports')),3)

    def test_editor_can_select_supported_claims_but_cannot_add_invented_facts(self):
        report=self.store.db.execute('SELECT * FROM development_reports WHERE id=?',(self.gibbs,)).fetchone()
        v=self.store.db.execute('SELECT * FROM development_report_versions WHERE id=?',(report['current_version'],)).fetchone()
        claims=json.loads(v['claims']); selected={'claims':claims[:1]}
        review(self.store,self.gibbs,'edit','Fixture editor','Shorter draft',35,selected)
        generate(self.store,self.root/'review')
        review(self.store,self.gibbs,'approve','Fixture editor','checked selected evidence',45)
        self.assertEqual(approved_watch(self.store,NOW)[0]['claims'],claims[:1])
        bad=copy.deepcopy(claims);bad[0]['value']='A six-story building opened today'
        with self.assertRaisesRegex(ValueError,'official field'):
            validate_claims(self.store,bad,json.loads(v['evidence_bundle']))
        bad=copy.deepcopy(claims);bad[0]['passage_id']='invented'
        with self.assertRaisesRegex(ValueError,'missing'):
            validate_claims(self.store,bad,json.loads(v['evidence_bundle']))
        with self.assertRaisesRegex(ValueError,'finite'):
            review(self.store,self.gibbs,'hold','Fixture editor','checked',float('nan'))

    def test_explicit_related_permits_are_separate_and_repeat_without_duplicates(self):
        links=self.store.rows('development_relationships')
        self.assertEqual(sorted(r['related_identifier'] for r in links),['26-007681-MT','26-021266-MT'])
        self.assertTrue(all(json.loads(r['data'])['parent_relationship']=='not established' for r in links))
        generate(self.store,self.root/'review')
        self.assertEqual(self.store.rows('development_relationships'),links)
        self.assertEqual(len(self.store.rows('development_records')),141)

    def test_saved_official_address_change_is_pending_not_completed(self):
        rows=report_rows((FIX/'portland-division-address-change-register.html').read_bytes(),['CHANGE STATUS','TAXLOT R NUMBER'])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['CHANGE STATUS'],'Pending')
        self.assertEqual(rows[0]['TAXLOT R NUMBER'],'R184111')
        self.assertEqual(rows[0]['NEW SITUS ADDRESS'],'3921 SE DIVISION ST')
        unit=report_rows((FIX/'portland-division-building-report.html').read_bytes(),['IVR NUMBER','PROPERTY ID','STATE ID'])
        self.assertEqual(unit[0]['IVR NUMBER'],'5243125')
        self.assertEqual(unit[0]['PROPERTY ID'],rows[0]['TAXLOT R NUMBER'])
        with self.assertRaisesRegex(ValueError,'headers'):
            report_rows(b'<html>Service unavailable</html>',['CHANGE STATUS'])

    def test_daily_render_and_repository_export_require_approved_evidence(self):
        from portland_brief.rendering import markdown, html
        from portland_brief.repository_archive import validate
        review(self.store,self.gibbs,'approve','Fixture editor','Checked',50)
        watch=approved_watch(self.store,NOW)
        doc={'title':'Brief','date':'2026-10-10','generated_at':datetime.now(timezone.utc).isoformat(),'timezone':'America/Los_Angeles','window_hours':24,'status':'draft','stories':[],'evidence':{},'gaps':[],'weather':{'coverage':'test','periods':[]},'usage':{'model':'test','input_tokens':0,'output_tokens':0,'attempts':0},'development_watch':watch}
        self.assertIn('Development Watch',markdown(doc))
        self.assertIn('5273121_did',html(doc))
        with self.assertRaisesRegex(ValueError,'editorial audit'):validate(doc,[])
        validate(doc,[],self.store)
        # A later hold does not falsify the historical approval at generation.
        review(self.store,self.gibbs,'hold','Fixture editor','Revisit',10)
        validate(doc,[],self.store)
        self.assertEqual(approved_watch(self.store,NOW),[])


class NoticeTests(unittest.TestCase):
    def test_saved_real_index_reconciles_all_three_pages_and_download_links(self):
        total=[];url='https://www.portland.gov/ppd/zoning-land-use/news/notices'
        for i in range(3):
            links,p=notice_links((FIX/f'portland-notice-index-{i}.html').read_bytes(),url)
            self.assertEqual(p['expected_count'],41);total+=links;url=p['next_url']
        self.assertIsNone(url);self.assertEqual(len({l['url'] for l in total}),41)
        parsed=parse_notice((FIX/'portland-land-use-2026-10-09.html').read_bytes(),total[0]['url'])
        self.assertTrue(parsed['attachments'][0]['url'].endswith('/download'))
        demo=parse_notice((FIX/'portland-demolition-delay.html').read_bytes(),'https://www.portland.gov/demo')
        self.assertEqual(demo['case_identifier'],'PR26-053039HRI')
        self.assertEqual(demo['stage'],'unknown')

    def test_budget_and_outage_cannot_claim_complete_or_remove_records(self):
        class IndexFetcher:
            failed=False
            def get(self,url,kind='html'):
                if self.failed:raise RuntimeError('offline')
                if '/news/notices' in url:
                    i=int(parse_qs(urlsplit(url).query).get('page',['0'])[0]);raw=(FIX/f'portland-notice-index-{i}.html').read_bytes()
                else:raw=(FIX/'portland-land-use-2026-10-09.html').read_bytes()
                return raw,{'fetched_at':NOW.isoformat(),'status':'ok'}
        with tempfile.TemporaryDirectory() as temp:
            store=CivicStore(Path(temp)/'civic.db');fetcher=IndexFetcher();source={'id':'notices','url':'https://www.portland.gov/ppd/zoning-land-use/news/notices'}
            result=collect(store,fetcher,source,Path(temp)/'out',max_pages=1,max_notices=1,attachments=False)
            self.assertFalse(result['inventory_complete']);self.assertFalse(result['complete'])
            self.assertEqual(result['discovered_notices'],20)
            rows=store.rows('filing_events');fetcher.failed=True
            failed=collect(store,fetcher,source,Path(temp)/'out',attachments=False)
            self.assertFalse(failed['complete']);self.assertEqual(store.rows('filing_events'),rows)
            self.assertEqual(store.rows('collection_checkpoints'),[])
            store.close()

    def test_changed_attachment_same_url_versions_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            store=CivicStore(Path(temp)/'civic.db');url='https://www.portland.gov/plan/download'
            a,_=store.source_version(url,b'%PDF-original','attachment-test/1',{'hash':'original'},'filings')
            b,changed=store.source_version(url,b'%PDF-revised','attachment-test/1',{'hash':'revised'},'filings')
            self.assertTrue(changed);self.assertNotEqual(a,b)
            self.assertEqual(len(store.rows('source_versions')),2);store.close()
