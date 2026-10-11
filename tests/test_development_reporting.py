import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from portland_brief.civic.store import CivicStore
from portland_brief.civic.developments import scan
from portland_brief.civic.development_reports import generate, review, approved_watch, source_checked_watch, validate_snapshot, validate_claims
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

    def test_automatic_drafts_need_no_human_approval_but_disclose_height_conflicts(self):
        snapshots=source_checked_watch(self.store,NOW)
        self.assertEqual(len(snapshots),3)
        self.assertEqual(self.store.rows('development_review_audit'),[])
        marshall=next(p for p in snapshots if p['folder_id']==5228969)
        self.assertIn('description lists 4 stories',marshall['sentences'][0])
        self.assertIn('field reports 3',marshall['sentences'][0])
        self.assertIn('unconfirmed',marshall['sentences'][0])
        for p in snapshots:validate_snapshot(self.store,p,datetime.now(timezone.utc).isoformat())
        from portland_brief.repository_archive import validate
        validate({'date':'2026-10-10','status':'draft','stories':[],'weather':{},'development_watch':snapshots,'generated_at':datetime.now(timezone.utc).isoformat()},[],self.store)
        wrong=copy.deepcopy(marshall);wrong['sentences'][0]='A four-story building was approved.'
        with self.assertRaisesRegex(ValueError,'explanation differs'):
            validate_snapshot(self.store,wrong,datetime.now(timezone.utc).isoformat())
        wrong=copy.deepcopy(marshall);wrong['editorial_state']='approved'
        with self.assertRaisesRegex(ValueError,'editorial approval'):
            validate_snapshot(self.store,wrong,datetime.now(timezone.utc).isoformat())

    def test_automatic_drafts_respect_an_explicit_editor_hold_and_stale_evidence(self):
        review(self.store,self.gibbs,'hold','Fixture editor','Do not include this draft',30)
        self.assertNotIn(5273121,[p['folder_id'] for p in source_checked_watch(self.store,NOW)])
        self.assertEqual(source_checked_watch(self.store,NOW+timedelta(hours=25)),[])
        original=next(r for r in self.store.rows('development_records') if r['folder_id']==5243125)
        source=self.store.db.execute('SELECT * FROM source_versions WHERE id=?',(original['source_version'],)).fetchone()
        self.store.source_version(source['url'],b'changed evidence','changed-parser/1',{'changed':True},'filings')
        self.assertNotIn(5243125,[p['folder_id'] for p in source_checked_watch(self.store,NOW)])

    def test_unchanged_permit_refreshes_provenance_after_a_page_or_parser_change(self):
        record=next(r for r in self.store.rows('development_records') if r['folder_id']==5243125)
        source=self.store.db.execute('SELECT * FROM source_versions WHERE id=?',(record['source_version'],)).fetchone()
        # Simulate a record retained from an older parser's page-level semantics.
        legacy,_=self.store.source_version(source['url'],b'legacy response','legacy-parser/1',{'legacy':True},'filings')
        attrs=json.loads(record['data'])['attributes']
        passage=self.store.passage(legacy,'FOLDERKEY=5243125',json.dumps(attrs,sort_keys=True))
        self.store.db.execute('UPDATE development_records SET source_version=?,passage_id=? WHERE id=?',(legacy,passage,record['id']))
        before=len(self.store.rows('development_versions'))
        scan(self.store,self.fetcher,SOURCE,{},self.root/'watch',NOW+timedelta(minutes=1))
        generate(self.store,self.root/'review')
        fresh=next(r for r in self.store.rows('development_records') if r['id']==record['id'])
        self.assertEqual(fresh['source_version'],source['id'])
        self.assertEqual(fresh['content_hash'],record['content_hash'])
        self.assertEqual(json.loads(fresh['data'])['last_material_change_at'],json.loads(record['data'])['last_material_change_at'])
        self.assertEqual(len(self.store.rows('development_versions')),before)
        snapshots=source_checked_watch(self.store,NOW+timedelta(minutes=1))
        self.assertEqual(len(snapshots),3)
        for p in snapshots:validate_snapshot(self.store,p,datetime.now(timezone.utc).isoformat())
        review(self.store,self.gibbs,'approve','Fixture editor','checked',60)
        explanations=len(self.store.rows('development_report_versions'))
        for page in self.fetcher.pages:
            for feature in page['features']:
                feature['attributes']['OBJECTID']+=10000
                feature['attributes']['DESCRIPTION']=feature['attributes']['DESCRIPTION'].replace(' ','  ')
        scan(self.store,self.fetcher,SOURCE,{},self.root/'watch',NOW+timedelta(minutes=2))
        generate(self.store,self.root/'review')
        self.assertEqual(len(self.store.rows('development_report_versions')),explanations)
        self.assertEqual(len(approved_watch(self.store,NOW+timedelta(minutes=2))),1)

    def test_map_uses_verified_coordinates_and_reader_pages_do_not_dump_json(self):
        from bs4 import BeautifulSoup
        raw=(self.root/'review/index.html').read_text();soup=BeautifulSoup(raw,'html.parser')
        map_data=json.loads(soup.select_one('#map-data').string)
        gibbs=next(p for p in map_data['projects'] if p['id']==5273121)
        record=next(r for r in self.store.rows('development_records') if r['folder_id']==5273121)
        geometry=json.loads(record['data'])['geometry']
        self.assertEqual([gibbs['lng'],gibbs['lat']],[geometry['x'],geometry['y']])
        self.assertEqual(gibbs['name'],'1010 SW Gibbs St')
        detail=BeautifulSoup((self.root/'review/5228969/review.html').read_text(),'html.parser')
        self.assertEqual(detail.select('pre'),[])
        self.assertIn('How tall is the building?',detail.get_text())
        self.assertNotIn('HELD FOR REVIEW',detail.get_text())
        self.assertNotIn('"passage_id"',detail.get_text())

    def test_source_text_cannot_break_the_map_data_script_or_invent_a_location(self):
        from portland_brief.civic.development_pages import script_data, view_model
        value=script_data({'name':'</script><script>alert(1)</script>'})
        self.assertNotIn('<',value)
        self.assertEqual(json.loads(value)['name'],'</script><script>alert(1)</script>')
        record=next(r for r in self.store.rows('development_records') if r['folder_id']==5273121)
        data=json.loads(record['data']);data['geometry']={'x':float('nan'),'y':45};record['data']=json.dumps(data)
        report=self.store.db.execute('SELECT * FROM development_reports WHERE id=?',(self.gibbs,)).fetchone()
        version=self.store.db.execute('SELECT * FROM development_report_versions WHERE id=?',(report['current_version'],)).fetchone()
        project=view_model(record,dict(report),dict(version),[],[])
        self.assertIsNone(project['lat']);self.assertIsNone(project['lng'])


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
