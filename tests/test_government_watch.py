from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from portland_brief.civic import parsers
from portland_brief.civic.government_watch import collect, snapshot, validate_snapshot, election_candidates, names, write_news_queue
from portland_brief.civic.ingestion import bootstrap, ingest_agenda, ingest_document
from portland_brief.civic.reports import generate, review
from portland_brief.civic.store import CivicStore
from portland_brief.government import order_stories
from portland_brief.records import Evidence
from portland_brief.selection import select
from portland_brief.rendering import markdown, html
from portland_brief.repository_archive import validate as validate_archive
from portland_brief.scheduling import definitions
from tests.test_brief import evidence, doc, raw_story
from portland_brief.validation import validate_story
from portland_brief.config import load_config
from portland_brief.pipeline import run


ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tests/fixtures'
NOW = datetime(2026,10,28,15,tzinfo=timezone.utc)
COUNCIL = 'https://www.portland.gov/council/agenda/2026/10/28'
COMMITTEE = 'https://www.portland.gov/council/agenda/committee-whole/2026/10/29'
BALLOT = 'https://www.portland.gov/auditor/elections/2026-november-ballot'
CFG = {'lookback_days':14,'max_agendas':12,'max_documents':0,'max_attachments':0,'election':{'url':BALLOT,'election_day':'2026-11-03','monitor_through':'2026-11-30'}}


class FixtureFetch:
    def __init__(self):
        self.calls = []

    def get(self,url,kind='html'):
        self.calls.append(url)
        exact = {COUNCIL:'portland-council-2026-10-28.html',COMMITTEE:'portland-committee-whole-2026-10-29.html',BALLOT:'portland-ballot-2026.html','https://www.portland.gov/council/policy-committees':'portland-committees-2026-10-09.html'}
        if url in exact:
            return (FIX/exact[url]).read_bytes(), {'status':'ok'}
        if url == 'https://www.portland.gov/council/agenda':
            target = COUNCIL
        elif url == 'https://www.portland.gov/council/agenda/committee-whole':
            target = COMMITTEE
        elif url.startswith('https://www.portland.gov/council/agenda/'):
            target = url+'/2020/1/1'  # Explicit old listing, outside this scan's window.
        else:
            raise AssertionError('Unexpected request '+url)
        return f'<main><h2><a href="{target}">Official agenda</a></h2></main>'.encode(),{'status':'ok'}


class GovernmentWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CivicStore(self.root/'civic.sqlite3')
        bootstrap(self.store,ROOT)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def collect(self,config=None,now=NOW,fetcher=None):
        return collect(self.store,fetcher or FixtureFetch(),config or CFG,self.root/'watch',now)

    def test_real_this_week_agendas_create_previews_not_outcomes_or_approvals(self):
        result = self.collect()
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['agendas_fetched'],2)
        self.assertEqual(len(self.store.rows('meeting_watch_entries')),3)
        self.assertEqual(len(self.store.rows('appearances')),22)
        self.assertEqual(len(self.store.rows('meeting_reports')),3)
        self.assertEqual({r['phase'] for r in self.store.rows('meeting_reports')},{'before'})
        self.assertEqual({r['state'] for r in self.store.rows('meeting_reports')},{'needs_review'})
        self.assertEqual(self.store.rows('report_audit'),[])
        self.assertGreater(result['documents_deferred'],0)
        text=(self.root/'watch/index.html').read_text()
        self.assertIn('Amend Council Organization',text)
        self.assertIn('Awaiting official outcome',text)
        self.assertIn('Chemical weapons usage',text)
        self.assertIn('after elections',text)

    def test_repeat_preserves_source_report_and_candidate_versions(self):
        self.collect()
        tables=['meetings','appearances','source_versions','meeting_reports','report_versions','election_watch_versions']
        before={t:len(self.store.rows(t)) for t in tables}
        self.collect(now=NOW+timedelta(hours=1))
        self.assertEqual(before,{t:len(self.store.rows(t)) for t in tables})

    def test_bounded_agenda_budget_rotates_without_starving_deferred_agendas(self):
        config={**CFG,'max_agendas':1}
        first=self.collect(config=config)
        second=self.collect(config=config,now=NOW+timedelta(hours=1))
        self.assertEqual(first['agendas_deferred'],1)
        self.assertEqual(second['agendas_deferred'],1)
        self.assertEqual(set(first['agenda_versions'])|set(second['agenda_versions']),{COUNCIL,COMMITTEE})

    def test_private_news_queue_excludes_disabled_and_outside_sources(self):
        from portland_brief.collection import NewsBuffer
        shutil.copytree(ROOT/'config',self.root/'config')
        settings,registry=load_config(self.root)
        buffer=NewsBuffer(self.root/'data/news-collection.sqlite3')
        gov=evidence(id='government',title='Portland City Council considers budget changes',published_at=(NOW-timedelta(hours=1)).isoformat())
        disabled=evidence(id='disabled',source_id='closed',title='Portland City Council private fixture',published_at=(NOW-timedelta(hours=1)).isoformat())
        outside=evidence(id='outside',source_id='kgw',title='Salem City Council considers unrelated proposal',text='Salem City Council considered a local proposal during its meeting this morning in Salem.',published_at=(NOW-timedelta(hours=1)).isoformat())
        buffer.record([gov,disabled,outside],[{'source_id':'portland','status':'ok'}],NOW);buffer.close()
        self.assertEqual(write_news_queue(self.store,self.root,settings,NOW),1)
        rendered=(self.root/'output/council-watch/news.html').read_text()
        self.assertIn(gov.title,rendered)
        self.assertNotIn(disabled.title,rendered)
        self.assertNotIn(outside.title,rendered)

    def test_live_failure_keeps_existing_agendas_and_reports_with_visible_gap(self):
        self.collect()
        before=self.store.rows('meeting_reports')
        class Down:
            def get(self,*args):
                return b'',{'status':'stale_cache','error':'fixture HTTP 503'}
        result=self.collect(now=NOW+timedelta(hours=1),fetcher=Down())
        self.assertTrue(result['errors'])
        self.assertEqual(before,self.store.rows('meeting_reports'))
        snap=snapshot(self.store,NOW+timedelta(hours=1),CFG)
        self.assertTrue(any('source or parsing gaps' in g for g in snap['gaps']))
        self.assertIn('fixture HTTP 503',(self.root/'watch/index.html').read_text())

    def test_notice_metadata_exports_but_unreviewed_narratives_do_not(self):
        self.collect()
        snap=snapshot(self.store,NOW+timedelta(seconds=1),CFG)
        self.assertEqual(len(snap['notices']),3)
        self.assertEqual(snap['reports'],[])
        self.assertEqual(snap['pending_reports'],3)
        validate_snapshot(self.store,snap,NOW+timedelta(seconds=1))
        d=doc();d['government_watch']=snap;d['generated_at']=(NOW+timedelta(seconds=1)).isoformat()
        validate_archive(d,[{'id':'portland','allowed_hosts':['www.portland.gov']}],self.store)
        forged=deepcopy(d)
        report=self.store.rows('meeting_reports')[0]
        forged['government_watch']['reports']=[{'report_id':report['id'],'version_id':report['current_version'],'phase':'before','approved_at':NOW.isoformat(),'claims':[]}]
        with self.assertRaisesRegex(ValueError,'approval'):
            validate_archive(forged,[{'id':'portland','allowed_hosts':['www.portland.gov']}],self.store)
        for key in ['title','starts_at','source_url','meeting_id']:
            tampered=deepcopy(snap);tampered['notices'][0][key]='fabricated'
            with self.assertRaises(ValueError):
                validate_snapshot(self.store,tampered,NOW+timedelta(seconds=1))

    def test_expired_notice_evidence_is_omitted_and_stale_health_disclosed(self):
        self.collect()
        snap=snapshot(self.store,NOW+timedelta(hours=40),CFG)
        self.assertEqual(snap['notices'],[])
        self.assertTrue(any('recent check' in g for g in snap['gaps']))

    def test_real_ballot_names_include_all_races_and_challengers(self):
        rows=election_candidates((FIX/'portland-ballot-2026.html').read_bytes())
        self.assertEqual(len(rows),34)
        self.assertEqual({c['district'] for c in rows},{None,'3','4'})
        self.assertIn('Eli Arnold',[c['name'] for c in rows])
        self.assertNotIn('One City Auditor (four-year term)',[c['name'] for c in rows])
        self.collect()
        self.assertIn('Eli Arnold',names(self.store,NOW+timedelta(hours=1)))
        self.assertNotIn('Eli Arnold',names(self.store,NOW+timedelta(days=4)))

    def test_post_election_job_remains_enabled_after_candidate_monitor_ends(self):
        shutil.copytree(ROOT/'config',self.root/'config')
        config,jobs=definitions(self.root)
        self.assertIn('council-watch',jobs)
        self.assertTrue(config['government_watch']['enabled'])
        fetcher=FixtureFetch()
        self.collect(now=datetime(2027,1,5,tzinfo=timezone.utc),fetcher=fetcher)
        self.assertNotIn(BALLOT,fetcher.calls)
        self.assertIn('https://www.portland.gov/council/agenda',fetcher.calls)

    def test_identity_conflict_keeps_appearance_without_merging_and_blocks_approval(self):
        url='https://www.portland.gov/council/documents/presentation/committee-chair-updates'
        old=self.store.matter('portland','2026-373',url,'Earlier presentation',{})
        self.store.commit()
        self.collect()
        row=next(r for r in self.store.rows('appearances') if json.loads(r['data']).get('document_number')=='2026-388')
        self.assertIsNone(row['matter_id'])
        self.assertTrue(json.loads(row['data'])['identity_warnings'])
        report=next(r for r in self.store.rows('meeting_reports') if r['meeting_id']==row['meeting_id'])
        with self.assertRaisesRegex(ValueError,'identities'):
            review(self.store,report['id'],'approve','Fixture editor','Fixture only',10)
        self.assertEqual(self.store.db.execute("SELECT matter_id FROM matter_identifiers WHERE namespace='source_url' AND value=?",(url,)).fetchone()[0],old)

    def test_report_snapshot_requires_actual_approval_and_rejects_tampering_or_hold(self):
        raw=(FIX/'portland-council-2026-10-07.html').read_bytes()
        mid=ingest_agenda(self.store,raw,'https://www.portland.gov/council/agenda/2026/10/7')['meeting_ids'][0]
        report=generate(self.store,mid,self.root/'reports')['before']['report_id']
        review(self.store,report,'approve','Fixture editor','Fixture evidence checked',30)
        now=datetime.now(timezone.utc)+timedelta(seconds=1)
        snap=snapshot(self.store,now,{'lookback_days':30})
        self.assertEqual(len(snap['reports']),1)
        validate_snapshot(self.store,snap,now)
        bad=deepcopy(snap);bad['reports'][0]['claims'][0]['value']='Invented adoption'
        with self.assertRaises(ValueError):
            validate_snapshot(self.store,bad,now)
        original=deepcopy(snap)
        review(self.store,report,'hold','Fixture editor','Hold for fixture check',1)
        with self.assertRaises(ValueError):
            validate_snapshot(self.store,original,datetime.now(timezone.utc)+timedelta(seconds=2))

    def test_real_proposed_amendments_are_separate_attachment_links(self):
        raw=(FIX/'portland-council-rules-2026-372.html').read_bytes()
        parsed=parsers.document_detail(raw,'https://www.portland.gov/council/documents/ordinance/council-rules-ordinance-2026')
        amendments=[a for a in parsed['attachments'] if a['role']=='amendment']
        self.assertGreater(len(amendments),20)
        self.assertTrue(any('Kanal 16' in a['label'] for a in amendments))
        self.assertTrue(any(a['role']=='exhibit' for a in parsed['attachments']))
        self.assertFalse(any('adopted' in a['label'].lower() for a in amendments))

    def test_document_refresh_preserves_existing_attachment_page_evidence(self):
        raw=(FIX/'portland-council-rules-2026-372.html').read_bytes()
        url='https://www.portland.gov/council/documents/ordinance/council-rules-ordinance-2026'
        class Fetch:
            def get(self,*args):
                return raw,{'status':'ok'}
        ingest_document(self.store,Fetch(),url)
        row=next(r for r in self.store.rows('documents') if r['kind']=='attachment')
        data={**json.loads(row['data']),'content_status':'text_extracted','page_passages':['fixture-page'],'pages':3}
        self.store.upsert('documents',**{**row,'data':data});self.store.commit()
        ingest_document(self.store,Fetch(),url)
        saved=json.loads(self.store.db.execute('SELECT data FROM documents WHERE id=?',(row['id'],)).fetchone()[0])
        self.assertEqual(saved['page_passages'],['fixture-page'])
        self.assertEqual(saved['content_status'],'text_extracted')

    def test_removed_agenda_item_is_not_retained_in_current_report_claims(self):
        raw=(FIX/'portland-council-2026-10-28.html').read_bytes()
        from bs4 import BeautifulSoup
        mid=ingest_agenda(self.store,raw,COUNCIL)['meeting_ids'][0]
        dom=BeautifulSoup(raw,'html.parser');item=dom.select('.relation--type-agenda-item')[1]
        title=parsers.text(item.select_one('.council-document__title a'));item.decompose()
        ingest_agenda(self.store,str(dom).encode(),COUNCIL)
        result=generate(self.store,mid,self.root/'reports')
        version=self.store.db.execute('SELECT claims FROM report_versions WHERE id=?',(result['before']['version'],)).fetchone()
        self.assertNotIn(title,[c['value'] for c in json.loads(version[0])])


class GovernmentNewsTests(unittest.TestCase):
    def settings(self):
        return {'window_hours':24,'story_limit':2,'per_source_limit':2,'government_watch':{'enabled':True},'government_names':['Eli Arnold','Mitch Green']}

    def test_government_priority_persists_after_election_and_keeps_eligibility(self):
        now=datetime(2027,2,1,tzinfo=timezone.utc)
        gov=evidence(id='gov',source_id='kgw',title='Portland City Council considers budget',published_at=(now-timedelta(hours=2)).isoformat(),text='Portland City Council will consider a proposed budget amendment during its next public meeting.')
        entertainment=evidence(id='fun',title='Portland concert tonight',published_at=(now-timedelta(minutes=1)).isoformat())
        stale=evidence(id='stale',title='Portland City Council adopts budget',published_at=(now-timedelta(days=4)).isoformat())
        chosen,_=select([entertainment,gov,stale],now,self.settings())
        self.assertEqual(chosen[0].id,'gov')
        self.assertNotIn('stale',[x.id for x in chosen])

    def test_different_candidates_are_not_merged_by_similar_headlines(self):
        a=evidence(id='a',source_id='kgw',title='Eli Arnold answers questions in Portland City Council election',published_at=(NOW-timedelta(hours=1)).isoformat())
        b=evidence(id='b',source_id='kgw',title='Mitch Green answers questions in Portland City Council election',url='https://www.kgw.com/other',published_at=(NOW-timedelta(hours=1)).isoformat())
        chosen,_=select([a,b],NOW,self.settings())
        self.assertEqual(len(chosen),2)

    def test_known_full_name_makes_local_candidate_news_eligible(self):
        e=evidence(source_id='kgw',title='Eli Arnold responds to criticism',text='Eli Arnold responded to criticism over his campaign proposals during a public interview this morning.',published_at=(NOW-timedelta(hours=1)).isoformat())
        chosen,_=select([e],NOW,self.settings())
        self.assertEqual(len(chosen),1)

    def test_rendered_section_is_prominent_and_escaped_in_both_formats(self):
        d=doc();d['government_watch']={'enabled':True,'notices':[],'reports':[],'gaps':[]}
        d['stories'][0]['flags']=['local_government'];d['stories'][0]['headline']='<script>bad</script>'
        for rendered in [markdown(d),html(d)]:
            self.assertIn('Local Government Watch',rendered)
            self.assertNotIn('<script>',rendered)
            self.assertIn('after elections',rendered)
        self.assertLess(markdown(d).index('Council &'),markdown(d).index('&lt;script&gt;'))

    def test_daily_pipeline_reserves_government_space_after_summary_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);shutil.copytree(ROOT/'config',root/'config')
            settings,registry=load_config(root);settings['story_limit']=1
            gov=evidence(id='gov',url='https://www.portland.gov/news/gov',title='Portland City Council reviews drainage work',published_at=(NOW-timedelta(hours=1)).isoformat())
            other=evidence(id='other',url='https://www.portland.gov/news/other',title='Portland summer concert lineup',published_at=(NOW-timedelta(hours=1)).isoformat())
            summaries=[validate_story(raw_story(e),e) for e in [other,gov]]
            health=[{'source_id':'portland','name':'City of Portland','status':'ok'}]
            with patch('portland_brief.pipeline.collect',return_value=([other,gov],health)),patch('portland_brief.pipeline.get_weather',return_value=doc()['weather']):
                result,path=run(root,settings,registry,now=NOW,summarizer=lambda *args:(summaries,[],doc()['usage']))
            self.assertEqual(len(result['stories']),1)
            self.assertEqual(result['stories'][0]['citation_ids'],['gov'])
            self.assertIn('local_government',result['stories'][0]['flags'])
            self.assertIn('Council & Local Government Watch',markdown(result))
