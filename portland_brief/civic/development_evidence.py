"""Bounded official cross-record research. Unresolved differences remain visible."""
import json
import re
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from .developments import fetch_json
from .projects import LAYER, FIELDS
from .store import stable_id, stamp


def report_rows(raw, required_headers):
    soup = BeautifulSoup(raw, 'html.parser')
    headers = [' '.join(c.get_text(' ', strip=True).split()) for c in soup.select('thead th')]
    if not headers or not set(required_headers).issubset(headers) or len(headers) != len(set(headers)):
        raise ValueError('Official report headers changed; require parser review')
    rows = []
    for tr in soup.select('tbody tr'):
        cells = tr.select('td')
        if not cells: continue
        if len(cells) != len(headers):
            raise ValueError('Official report row is incomplete')
        rows.append(dict(zip(headers, [' '.join(c.get_text(' ', strip=True).split()).replace('\u200b', '') for c in cells])))
    return rows


def save(store, record, url, raw, facts, kind, fetched_at):
    parser = '2' if kind == 'related-permit' else '1'
    version, _ = store.source_version(url, raw, 'development-research/'+kind+'/'+parser, facts, 'filings', fetched_at)
    passage = store.passage(version, kind+':FOLDERKEY='+str(record['folder_id']), json.dumps(facts, sort_keys=True))
    store.upsert('development_evidence', id=stable_id('development-evidence-link', record['id'], url), record_id=record['id'], source_url=url, source_version=version, passage_id=passage, kind=kind, data=facts)
    store.commit()
    return {'url': url, 'kind': kind, 'source_version': version, 'passage_id': passage}


def research(store, fetcher, end_date):
    results = []
    for record in store.rows('development_records'):
        data = json.loads(record['data']); a = data['attributes']
        if data['assessment']['status'] not in {'matches_filter', 'hold_conflict'}: continue
        entries = []; details = None; unit = None
        url = 'https://www.portlandmaps.com/arcgis/rest/services/Public/BDS_Permit/MapServer/2/query?'+urlencode({'f':'json','where':f"FOLDERKEY = {record['folder_id']}",'outFields':'FOLDERKEY,PROPERTYKEY,STATEIDKEY,NEIGHBORHOOD,GIS_LOCATION_TYPE,TRIM_URL','outSR':4326})
        try:
            obj, raw, meta = fetch_json(fetcher, url)
            rows = obj.get('features', [])
            if len(rows) != 1 or obj.get('exceededTransferLimit') or rows[0]['attributes']['FOLDERKEY'] != record['folder_id']:
                raise ValueError('Official folder-detail query is incomplete or ambiguous')
            details = rows[0]
            entries.append(save(store, record, url, raw, details, 'folder-details', meta['fetched_at']))
        except Exception as exc:
            store.failed_retrieval('filings', url, exc); entries.append({'url':url,'error':str(exc)})
        url = 'https://www.portlandmaps.com/reports/index.cfm?'+urlencode({'action':'building-unit','start_date':'01/01/2025','end_date':end_date,'search_field':'IVR_NUMBER','search_value':record['folder_id']})
        try:
            raw, meta = fetcher.get(url, 'html')
            if meta.get('status') == 'stale_cache': raise RuntimeError('Current building report unavailable')
            rows = report_rows(raw, ['IVR NUMBER','PROPERTY ID','STATE ID','ADDRESS'])
            matches = [r for r in rows if r['IVR NUMBER'] == str(record['folder_id'])]
            if len(matches) > 1: raise ValueError('Duplicate building-report folder identity')
            unit = matches[0] if matches else None
            entries.append(save(store, record, url, raw, {'matching_rows':matches,'note':'No matching row is not withdrawal, denial or proof of absence'}, 'building-report', meta['fetched_at']))
        except Exception as exc:
            store.failed_retrieval('filings', url, exc); entries.append({'url':url,'error':str(exc)})
        if details and unit and re.fullmatch(r'R\d+',unit['PROPERTY ID']) and re.sub(r'\s+','',unit['STATE ID']) == re.sub(r'\s+','',details['attributes']['STATEIDKEY']):
            url = 'https://www.portlandmaps.com/reports/index.cfm?'+urlencode({'action':'assessor-multnomah','start_date':'01/01/2025','end_date':end_date,'search_field':'TAXLOT_R_NUMBER','search_value':unit['PROPERTY ID']})
            try:
                raw, meta = fetcher.get(url, 'html')
                if meta.get('status') == 'stale_cache': raise RuntimeError('Current address-change report unavailable')
                rows = report_rows(raw, ['TAXLOT R NUMBER','ORIGINAL SITUS ADDRESS','NEW SITUS ADDRESS','CHANGE STATUS'])
                matches = [r for r in rows if r['TAXLOT R NUMBER'] == unit['PROPERTY ID']]
                entries.append(save(store, record, url, raw, {'matching_rows':matches,'property_id':unit['PROPERTY ID'],'state_id':unit['STATE ID'],'note':'Preserve change status verbatim. Pending is not completed; differing addresses are not silently resolved.'}, 'address-change-register', meta['fetched_at']))
            except Exception as exc:
                store.failed_retrieval('filings', url, exc); entries.append({'url':url,'error':str(exc)})
        for identifier in sorted(set(re.findall(r'\b(\d{2})-(\d{6})-(MT|CO|RS)\b',a['DESCRIPTION'],re.I))):
            year,number,kind=identifier; application=f'20{year}-{number}-000-00-{kind.upper()}'
            url=LAYER+'/query?'+urlencode({'f':'json','where':f"APPLICATION = '{application}'",'outFields':FIELDS,'outSR':4326,'resultRecordCount':100})
            try:
                obj,raw,meta=fetch_json(fetcher,url)
                rows=obj.get('features')
                if not isinstance(rows,list) or obj.get('exceededTransferLimit'):raise ValueError('Related permit query incomplete')
                if any(r['attributes'].get('APPLICATION') != application for r in rows):raise ValueError('Related permit identifier mismatch')
                # A refreshed GIS-processing timestamp is not project activity.
                rows = [{**r, 'attributes': {k: ' '.join(v.split()) if isinstance(v, str) else v for k,v in r['attributes'].items() if k != 'GIS_PROCESS_DATE'}} for r in rows]
                facts={'explicit_reference':'-'.join(identifier).upper(),'records':sorted(rows,key=lambda r:r['attributes']['FOLDERKEY']),'parent_relationship':'not established; permits remain distinct'}
                entries.append(save(store,record,url,raw,facts,'related-permit',meta['fetched_at']))
            except Exception as exc:
                store.failed_retrieval('filings',url,exc);entries.append({'url':url,'error':str(exc)})
        results.append({'folder_id':record['folder_id'],'evidence':entries,'review_required':True})
    return {'checked_at':stamp(),'projects':results,'published':False,'model_tokens':0}
