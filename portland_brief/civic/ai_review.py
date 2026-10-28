"""Automatic private AI checks are separate from editorial approval and export."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from ..pipeline import run_lock
from ..validation import suspicious
from .reports import validate_claims
from .store import stable_id, stamp

RUBRIC='private-claim-review-v1'


def context(store,report,cfg):
    version=store.db.execute('SELECT * FROM report_versions WHERE id=?',(report['current_version'],)).fetchone()
    claims=json.loads(version['claims']);bundle=json.loads(version['evidence_bundle'])
    fingerprint=stable_id('ai-evidence',version['id'],bundle,RUBRIC,cfg['model'],cfg['model_revision'])
    holds=[]
    try:
        validate_claims(store,claims,bundle)
    except ValueError as exc:
        holds.append(str(exc))
    if report['state']=='hold':
        holds.append('An explicit editor hold remains in force')
    for url,vid in bundle.items():
        row=store.db.execute('SELECT meaningful_hash FROM source_versions WHERE id=?',(vid,)).fetchone()
        current=store.db.execute('SELECT meaningful_hash FROM source_current WHERE url=?',(url,)).fetchone()
        if not row or not current or row[0]!=current[0]:
            holds.append('Material source evidence changed; regenerate the draft before review')
    for row in store.db.execute('SELECT data FROM appearances WHERE meeting_id=?',(report['meeting_id'],)):
        holds.extend(json.loads(row[0]).get('identity_warnings',[]))
    for issue in json.loads(version['data']).get('issues',[]):
        holds.extend(x for x in issue.get('issues',[]) if any(word in x for word in ['mismatch','conflict','duplicate']))
    tasks=[]
    for claim in claims:
        passage=store.db.execute('SELECT text,locator FROM passages WHERE id=?',(claim['passage_id'],)).fetchone()
        value={'claim_id':claim['fact_id'],'predicate':claim.get('predicate','meeting_metadata'),'value':claim['value'],'source_passage':passage['text'] if passage else '', 'locator':passage['locator'] if passage else ''}
        if suspicious(json.dumps(value,ensure_ascii=False)):
            holds.append('Source instructions quarantined from the reviewer')
        if len(json.dumps(value,ensure_ascii=False))>cfg.get('max_chunk_chars',12000):
            holds.append('A claim and its complete supporting passage exceed the private review input bound')
        tasks.append(value)
    return version,claims,fingerprint,sorted(set(holds)),tasks


def status(store,report,cfg):
    version,claims,fingerprint,holds,tasks=context(store,report,cfg)
    rows=store.db.execute('SELECT * FROM ai_review_chunks WHERE report_id=? AND report_version=? AND fingerprint=?',(report['id'],version['id'],fingerprint)).fetchall()
    checked=set();issues=[];last=None
    for row in rows:
        data=json.loads(row['data']);checked.update(data['checked_claim_ids']);issues.extend(data['issues']);last=max(last or '',row['checked_at'])
    required={c['fact_id'] for c in claims}
    state='needs_attention' if holds or issues else 'ai_checked' if required and required<=checked else 'not_checked'
    return {'status':state,'checked_claims':len(required&checked),'total_claims':len(required),'issues':issues,'holds':holds,'checked_at':last,'fingerprint':fingerprint,'human_approved':report['state']=='approved','scope':'AI checks claim support in supplied official records; it is not human approval or independent reporting.'}


def validate_response(raw,task):
    if not isinstance(raw,dict) or set(raw)!={'verdict','checked_claim_ids','issues'}:
        raise ValueError('Reviewer schema is invalid')
    expected=[c['claim_id'] for c in task['claims']]
    ids=raw['checked_claim_ids']
    if not isinstance(ids,list) or len(ids)!=len(set(ids)) or set(ids)!=set(expected):
        raise ValueError('Reviewer did not check every assigned claim exactly once')
    if raw['verdict'] not in {'pass','needs_attention'} or not isinstance(raw['issues'],list):
        raise ValueError('Reviewer verdict is invalid')
    for issue in raw['issues']:
        if (not isinstance(issue,dict) or set(issue)!={'claim_id','reason'} or issue['claim_id'] not in expected or
            not isinstance(issue['reason'],str) or not 5<=len(issue['reason'])<=600 or suspicious(issue['reason']) or 'http' in issue['reason'].lower()):
            raise ValueError('Reviewer issue contains unsupported fields or text')
    if (raw['verdict']=='pass') != (not raw['issues']):
        raise ValueError('Reviewer verdict contradicts its issues')
    return raw


def local_review(root,cfg,tasks,run_dir):
    if cfg.get('provider')!='mlx':
        raise ValueError('Only the existing local MLX reviewer is authorized')
    run_dir.mkdir(parents=True,exist_ok=True)
    request=run_dir/'input.json';response=run_dir/'output.json'
    request.write_text(json.dumps({'settings':cfg,'tasks':tasks},indent=2))
    permitted={'PATH','HOME','LANG','LC_ALL','TMPDIR','SYSTEMROOT'}
    env={k:v for k,v in os.environ.items() if k in permitted}
    env.update(HF_HOME=str(root/'.models'),HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    with run_lock(root/'data/model.lock'):
        result=subprocess.run([sys.executable,'-m','portland_brief.civic.ai_review_worker',str(request),str(response)],cwd=root,env=env,capture_output=True,text=True,timeout=cfg.get('timeout_seconds',600))
    if result.returncode or not response.exists():
        raise RuntimeError('Local reviewer failed; no AI pass or human approval recorded')
    return json.loads(response.read_text())


def run(store,root,cfg,reviewer=local_review,now=None):
    now=now or datetime.now(timezone.utc)
    started=now.isoformat();run_id=stable_id('ai-review-run',started)
    tasks=[];reports=[];skipped=[]
    # Prefer newly posted actions and recent sessions; unfinished chunks resume
    # on later runs, while matching completed checks are reused.
    rows=store.db.execute("SELECT r.* FROM meeting_reports r JOIN meetings m ON m.id=r.meeting_id WHERE m.starts_at>=? ORDER BY CASE r.phase WHEN 'after' THEN 0 WHEN 'before' THEN 1 ELSE 2 END,m.starts_at DESC",(cfg.get('since','2026-10-01'),)).fetchall()
    for row in rows:
        report=dict(row);state=status(store,report,cfg)
        if state['status']!='not_checked':
            continue
        if len(reports)>=cfg.get('max_reports',4):
            break
        version,claims,fingerprint,holds,items=context(store,report,cfg)
        checked=set()
        for chunk in store.db.execute('SELECT data FROM ai_review_chunks WHERE fingerprint=?',(fingerprint,)):
            checked.update(json.loads(chunk[0])['checked_claim_ids'])
        pending=[x for x in items if x['claim_id'] not in checked]
        chunk=[];size=0
        meeting=store.db.execute('SELECT m.starts_at,b.name,b.kind FROM meetings m LEFT JOIN bodies b ON b.id=m.body_id WHERE m.id=?',(report['meeting_id'],)).fetchone()
        context_data={'body':meeting['name'],'body_kind':meeting['kind'],'meeting_time':meeting['starts_at'],'phase':report['phase']}
        for item in pending:
            length=len(json.dumps(item,ensure_ascii=False))
            if chunk and size+length>cfg.get('max_chunk_chars',12000):
                tid=stable_id('ai-review-chunk',fingerprint,[x['claim_id'] for x in chunk])
                tasks.append({'task_id':tid,'report_id':report['id'],'report_version':version['id'],'fingerprint':fingerprint,'context':context_data,'claims':chunk})
                chunk=[];size=0
            chunk.append(item);size+=length
        if chunk:
            tid=stable_id('ai-review-chunk',fingerprint,[x['claim_id'] for x in chunk])
            tasks.append({'task_id':tid,'report_id':report['id'],'report_version':version['id'],'fingerprint':fingerprint,'context':context_data,'claims':chunk})
        reports.append(report['id'])
    tasks=tasks[:cfg.get('max_chunks',8)]
    result={'run_id':run_id,'started_at':started,'tasks':len(tasks),'reviewed_chunks':0,'errors':[],'usage':{'input_tokens':0,'output_tokens':0,'attempts':0,'api_cost_usd':0.0},'human_approvals_recorded':0}
    if tasks:
        try:
            output=reviewer(root,cfg,tasks,root/'data/ai-review'/run_id.replace(':','-'))
            result['usage']=output['usage'];by_id={t['task_id']:t for t in tasks};seen=set()
            for record in output['results']:
                tid=record.get('task_id')
                if tid not in by_id or tid in seen:
                    result['errors'].append('Reviewer returned an unknown or duplicate task ID');continue
                seen.add(tid);task=by_id[tid]
                if record.get('error'):
                    result['errors'].append(record['error']);continue
                try:
                    reviewed=validate_response(record.get('review'),task)
                    current=store.db.execute('SELECT * FROM meeting_reports WHERE id=?',(task['report_id'],)).fetchone()
                    if not current or current['current_version']!=task['report_version'] or status(store,dict(current),cfg)['fingerprint']!=task['fingerprint'] or context(store,dict(current),cfg)[3]:
                        raise ValueError('Report changed during review; stale verdict discarded')
                    store.upsert('ai_review_chunks',id=tid,report_id=task['report_id'],report_version=task['report_version'],fingerprint=task['fingerprint'],checked_at=stamp(),status=reviewed['verdict'],data={**reviewed,'model':cfg['model'],'model_revision':cfg['model_revision'],'rubric':RUBRIC})
                    result['reviewed_chunks']+=1
                except (ValueError,TypeError,KeyError) as exc:
                    result['errors'].append(str(exc))
            if set(by_id)-seen:
                result['errors'].append('Reviewer omitted assigned tasks; they remain unchecked')
        except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as exc:
            result['errors'].append(str(exc)[:300])
    store.upsert('ai_review_runs',id=run_id,started_at=started,status='partial' if result['errors'] else 'completed',data=result)
    store.commit()
    return result
