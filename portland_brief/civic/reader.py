"""Private reading snapshots: summaries and official histories, never raw news evidence."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from ..rendering import atomic_text
from ..records import safe_url
from ..storage import Store
from .ai_review import status as ai_status


def catalog(store,root,settings,now=None):
    now=now or datetime.now(timezone.utc)
    briefs=[];editorial_path=root/settings['data_path']/'editorial.sqlite3'
    if editorial_path.exists():
        editorial=Store(editorial_path)
        try:
            for edition in editorial.list_editions()[:30]:
                if edition['state'] not in {'approved','needs_review'}:
                    continue
                _,doc=editorial.get(edition['date'])
                stories=[]
                for story in doc['stories']:
                    sources=[]
                    for eid in story['citation_ids']:
                        source=doc['evidence'][eid]
                        sources.append({'name':source['source_name'],'url':safe_url(source['url']),'published_at':source.get('published_at')})
                    stories.append({'headline':story['headline'],'sentences':story['sentences'],'sources':sources,'government':'local_government' in story.get('flags',[])})
                briefs.append({'date':doc['date'],'generated_at':doc['generated_at'],'stories':stories,'gaps':doc['gaps'],'state':edition['state']})
        finally:
            editorial.close()
    reports=[];by_meeting={}
    cfg=settings.get('ai_review')
    for row in store.db.execute('SELECT r.*,m.starts_at,m.source_url,b.name AS body_name FROM meeting_reports r JOIN meetings m ON m.id=r.meeting_id LEFT JOIN bodies b ON b.id=m.body_id ORDER BY m.starts_at DESC,r.phase'):
        report=dict(row)
        version=store.db.execute('SELECT * FROM report_versions WHERE id=?',(row['current_version'],)).fetchone()
        claims=json.loads(version['claims'])
        checks=ai_status(store,report,cfg) if cfg else {'status':'not_checked','checked_claims':0,'total_claims':len(claims),'issues':[],'holds':[],'human_approved':row['state']=='approved'}
        item={'id':row['id'],'version':row['current_version'],'meeting_id':row['meeting_id'],'body':row['body_name'] or 'Official meeting','starts_at':row['starts_at'],'phase':row['phase'],'source_url':row['source_url'],'human_state':row['state'],'ai':checks,
              'claims':[{'id':c['fact_id'],'predicate':c.get('predicate','meeting_metadata'),'value':c['value'],'source_url':c['source_url'],'appearance_id':c.get('appearance_id')} for c in claims]}
        reports.append(item);by_meeting.setdefault(row['meeting_id'],[]).append(item['id'])
    histories=[]
    for matter in store.db.execute('SELECT * FROM matters ORDER BY title'):
        events=[]
        for row in store.db.execute('SELECT a.*,m.starts_at,m.source_url,m.body_id,m.data AS meeting_data,b.name AS body_name FROM appearances a JOIN meetings m ON m.id=a.meeting_id LEFT JOIN bodies b ON b.id=m.body_id WHERE a.matter_id=? ORDER BY julianday(m.starts_at),CAST(a.agenda_number AS INTEGER)',(matter['id'],)):
            metadata=json.loads(row['data']);current=json.loads(row['meeting_data']).get('source_version')
            if current and metadata.get('source_version')!=current:
                continue
            votes=[]
            for vote in store.db.execute('SELECT * FROM vote_events WHERE appearance_id=?',(row['id'],)):
                data=json.loads(vote['data'])
                # Preserve separate amendment/procedural and final events. A
                # published tally never manufactures individual positions.
                votes.append({'id':vote['id'],'kind':vote['event_kind'],'origin':vote['origin'],'motion_id':vote['motion_id'],'review_status':vote['review_status'],'tallies':data.get('declared_tallies',{}),'positions':[{'name':v.get('raw_name') or v.get('person_name') or 'Unresolved name','position':v['position']} for v in data.get('individual_votes',[])],'issues':data.get('validation',{}).get('issues',[])})
            motions=[{'id':m['id'],'kind':m['kind'],'text':m['official_text']} for m in store.db.execute('SELECT * FROM motions WHERE appearance_id=?',(row['id'],))]
            events.append({'id':row['id'],'meeting_id':row['meeting_id'],'date':row['starts_at'],'body':row['body_name'] or 'Official body','agenda_number':row['agenda_number'],'stage':row['action_type'],'disposition':row['official_disposition'],'notes':metadata.get('notes',''),'source_url':row['source_url'],'votes':votes,'motions':motions,'reports':by_meeting.get(row['meeting_id'],[])})
        identifiers=[{'namespace':r['namespace'],'value':r['value']} for r in store.db.execute('SELECT namespace,value FROM matter_identifiers WHERE matter_id=?',(matter['id'],))]
        if not events:
            # Index-only entries are explicitly catalogued as such, never as a
            # reconstructed timeline or evidence that an item was adopted.
            meta=json.loads(matter['data'])
            if not meta.get('index_version'):
                continue
        histories.append({'id':matter['id'],'title':matter['title'],'jurisdiction':matter['jurisdiction_id'],'identifiers':identifiers,'events':events,'index_only':not events})
    # Keep conflicted appearances visible without merging them into a matter.
    for row in store.db.execute('SELECT a.*,m.starts_at,m.source_url,b.name AS body_name FROM appearances a JOIN meetings m ON m.id=a.meeting_id LEFT JOIN bodies b ON b.id=m.body_id WHERE a.matter_id IS NULL'):
        data=json.loads(row['data'])
        if not data.get('identity_warnings'):
            continue
        histories.append({'id':row['id'],'title':data['title'],'jurisdiction':'portland','identifiers':[{'namespace':'unreconciled_document_number','value':data.get('document_number','')}],'events':[{'id':row['id'],'meeting_id':row['meeting_id'],'date':row['starts_at'],'body':row['body_name'],'stage':row['action_type'],'disposition':row['official_disposition'],'notes':'Identity unresolved: '+ '; '.join(data['identity_warnings']),'source_url':row['source_url'],'votes':[],'motions':[],'reports':by_meeting.get(row['meeting_id'],[])}],'identity_hold':True,'index_only':False})
    latest=store.db.execute('SELECT data FROM government_watch_runs ORDER BY checked_at DESC LIMIT 1').fetchone()
    latest_review=store.db.execute('SELECT data FROM ai_review_runs ORDER BY started_at DESC LIMIT 1').fetchone()
    return {'schema_version':1,'generated_at':now.isoformat(),'private':True,'briefs':briefs,'reports':reports,'histories':histories,
            'coverage':json.loads(latest[0]) if latest else None,'ai_run':json.loads(latest_review[0]) if latest_review else None,
            'notice':'Private AI-assisted reading workspace. AI checks are not human approval. Official proposals, amendments, readings and final decisions remain separate. Historical inventory and county outcome coverage are incomplete.'}


def build(store,root,settings,now=None):
    output=root/settings['output_path']/'civic-reader';output.mkdir(parents=True,exist_ok=True)
    data=catalog(store,root,settings,now)
    text=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    atomic_text(output/'catalog.json',text)
    assets=Path(__file__).with_name('reader_assets')
    for name in ['app.js','style.css']:
        shutil.copyfile(assets/name,output/name)
    # Embedding a bounded JSON text node makes file URLs work without fetch.
    embedded=text.replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    shell=(assets/'index.html').read_text().replace('<!--CATALOG-->','<script id="reader-data" type="application/json">'+embedded+'</script>')
    atomic_text(output/'index.html',shell)
    return {'path':str(output/'index.html'),'reports':len(data['reports']),'histories':len(data['histories']),'ai_checked':sum(r['ai']['status']=='ai_checked' for r in data['reports']),'needs_attention':sum(r['ai']['status']=='needs_attention' for r in data['reports'])}
