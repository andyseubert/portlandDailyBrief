"""Readable private project pages and a map built from verified permit points."""
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

from shapely.geometry import shape, mapping

from ..records import safe_url

ASSETS = Path(__file__).with_name('assets')
STATE_LABELS = {'needs_review': 'Source-linked draft', 'hold': 'Open questions', 'approved': 'Reviewed'}


def pretty(value):
    return re.sub(r'\b(nw|ne|sw|se|n|s)\b', lambda m: m[0].upper(), str(value).title(), flags=re.I)


def date_text(value):
    if not value: return 'Date unavailable'
    dt = datetime.fromtimestamp(value/1000, timezone.utc) if isinstance(value, (int, float)) else datetime.fromisoformat(value)
    return dt.astimezone(ZoneInfo('America/Los_Angeles')).strftime('%B %-d, %Y')


def script_data(value):
    # Source text cannot terminate a script element or introduce executable HTML.
    return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def link(url, label, css=''):
    return '<a class="'+escape(css, quote=True)+'" href="'+escape(safe_url(url), quote=True)+'">'+escape(label)+'</a>'


def shell(title, body, prefix='', map_data=None):
    scripts = ''
    if map_data is not None:
        scripts = '<script id="map-data" type="application/json">'+script_data(map_data)+'</script><script src="'+prefix+'assets/vendor/leaflet/leaflet.js" defer></script><script src="'+prefix+'assets/development-map.js" defer></script>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>'+escape(title)+' · Portland Daily Brief</title><link rel="stylesheet" href="'+prefix+'assets/vendor/leaflet/leaflet.css"><link rel="stylesheet" href="'+prefix+'assets/developments.css"></head><body><a class="skip-link" href="#main">Skip to content</a><header class="site-header"><a class="brand" href="'+prefix+'index.html"><span class="brand-mark" aria-hidden="true">P<span>↗</span></span><span>Portland <strong>Daily Brief</strong></span></a><span class="private-label"><span class="live-dot"></span>Private workspace</span></header>'+body+'<footer class="site-footer"><span>Portland Daily Brief <span aria-hidden="true">/</span> Development Watch</span><span>Official records. Original summaries. Dated draft coverage.</span></footer>'+scripts+'</body></html>'


def view_model(record, report, version, tags, research):
    data = json.loads(record['data']); a = data['attributes']; assessment = data['assessment']
    address = pretty(' '.join(str(a[k]).strip() for k in ['HOUSE', 'DIRECTION', 'PROPSTREET', 'STREETTYPE'] if a[k]))
    name = next((row['PROJECT NAME'] for e in research if e['kind'] == 'building-report' for row in e['data'].get('matching_rows', []) if row.get('PROJECT NAME')), None)
    if not name:
        prefix = a['DESCRIPTION'].split(' - ')[0]
        name = prefix if ' - ' in a['DESCRIPTION'] and len(prefix) < 70 and not prefix.upper().startswith(('PDOX', 'PS ')) else address
    name = pretty(name)
    neighborhoods = [pretty(t['area_id']) for t in tags if t['area_kind'] == 'neighborhood' and t['area_id']]
    ambiguous = any((json.loads(t['data']) if isinstance(t['data'], str) else t['data']).get('ambiguous') for t in tags if t['area_kind'] == 'neighborhood')
    neighborhood = ' / '.join(neighborhoods) if neighborhoods else 'Neighborhood unconfirmed'
    if ambiguous: neighborhood += ' · boundary location'
    heights = sorted(set(assessment['height_in_description'] + ([assessment['height_numeric_field']] if assessment['height_numeric_field'] else [])))
    height = ' / '.join(f'{x:g}' for x in heights) if heights else 'Unknown'
    height_note = 'Story counts disagree' if len(heights) > 1 else 'Proposed stories'
    units = assessment['housing_units_reported']
    homes = f'{units:g}' if type(units) in {int, float} and math.isfinite(units) and units > 0 else 'Unknown'
    description = a['DESCRIPTION'].casefold()
    commercial = 'Retail shell' if 'shell' in description and ('retail' in description or 'commercial' in description) else 'Retail' if 'retail' in description else 'Commercial space'
    commercial_note = 'Planned at ground level' if 'ground' in description else 'In the proposal'
    sentences = json.loads(version['data'])['rendered_claims']
    summary = next((s for c,s in zip(json.loads(version['claims']), sentences) if c['kind']=='description'), 'Open the official record to review the proposal.')
    if len(heights) > 1:
        description_heights = ' and '.join(f'{x:g}' for x in assessment['height_in_description'])
        numeric_note = f"{assessment['height_numeric_field']:g}" if assessment['height_numeric_field'] else 'no usable count'
        summary = f"A mixed-use building with {homes} proposed homes and commercial space appears in the city’s permit records. Its description lists {description_heights} stories, while the separate story-count field reports {numeric_note}. The height needs confirmation."
    geom = data.get('geometry') or {}; lat, lng = geom.get('y'), geom.get('x')
    located = type(lat) in {int,float} and type(lng) in {int,float} and math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180
    return {'id':record['folder_id'],'name':name,'address':address,'neighborhood':neighborhood,'height':height,'height_note':height_note,'homes':homes,'commercial':commercial,'commercial_note':commercial_note,'summary':summary,'status':a['STATUS'],'state':report['state'],'state_label':STATE_LABELS.get(report['state'],'Awaiting review'),'lat':lat if located else None,'lng':lng if located else None,'url':str(record['folder_id'])+'/review.html','created':date_text(a['CREATEDATE']),'observed':date_text(data['last_material_change_at']),'source_url':record['source_url'],'application':a['APPLICATION']}


def stats(project):
    return '<div class="project-stats"><div><strong>'+escape(project['height'])+'</strong><span>'+escape(project['height_note'])+'</span></div><div><strong>'+escape(project['homes'])+'</strong><span>Proposed homes</span></div><div><strong class="use-stat">'+escape(project['commercial'])+'</strong><span>'+escape(project['commercial_note'])+'</span></div></div>'


def card(project, number):
    return '<article class="project-card" data-id="'+str(project['id'])+'" data-state="'+escape(project['state'])+'" data-search="'+escape(' '.join([project['name'],project['address'],project['neighborhood']]).casefold(),quote=True)+'"><div class="card-top"><span class="eyebrow">'+escape(project['neighborhood'])+'</span><span class="pin-key '+('held' if project['state']=='hold' else '')+'">'+str(number)+'</span></div><h3><a href="'+project['url']+'">'+escape(project['name'])+'</a></h3><p class="card-address">'+escape(project['address'])+'</p><p class="card-summary">'+escape(project['summary'])+'</p><div class="card-facts"><span>'+escape(project['height'])+' stories</span><span>'+escape(project['homes'])+' homes proposed</span></div><div class="card-bottom"><span class="status-chip '+escape(project['state'])+'">'+escape(project['state_label'])+'</span><a class="text-link" href="'+project['url']+'">Explore project <span aria-hidden="true">↗</span></a></div></article>'


def evidence_section(store, record, payload):
    sections = []
    for entry in payload['evidence']:
        source = store.db.execute('SELECT retrieved_at FROM source_versions WHERE id=?',(entry['version'],)).fetchone()
        label = 'Building application' if any(p['id']==record['passage_id'] for p in entry['passages']) else 'Official intake cross-check'
        if not entry['passages']: continue
        passages = []
        for passage in entry['passages']:
            try: facts=json.loads(passage['text'])
            except ValueError: facts={}
            fields = [('DESCRIPTION','Project description'),('description','Project description'),('STATUS','Official status'),('status','Official status'),('APPLICATION','Application'),('case_number','Application'),('address','Address in the intake report'),('NUMBSTORIES','Separate story-count field'),('NUMNEWUNITS','New housing units')]
            for key,title in fields:
                if key in facts and facts[key] is not None:
                    passages.append('<div class="source-fact"><strong>'+escape(title)+'</strong><p>'+escape(str(facts[key]))+'</p></div>')
            if facts.get('matching_rows') == 0:
                passages.append('<p>No matching intake row appeared in the scoped query. This does not establish withdrawal or denial.</p>')
        source_url=record['source_url'] if label=='Building application' else entry['url']
        sections.append('<details class="source-detail"><summary><span>'+escape(label)+'</span><span class="source-date">Retrieved '+escape(date_text(source[0]) if source else 'date unavailable')+'</span></summary><div class="source-content">'+''.join(passages)+link(source_url,'Open official source ↗','text-link')+'</div></details>')
    return '<section id="sources" class="reading-section"><div class="section-heading"><span class="section-number">04</span><h2>Go to the source</h2></div><p>City records support the account above. Expand a source to read the relevant description or field.</p>'+''.join(sections)+'</section>'


def detail_page(store, record, report, version, payload, project):
    data=json.loads(record['data']); assessment=data['assessment']; intake=data.get('intake_crosscheck',{}); facts=intake.get('facts',{})
    reference=link(record['source_url'],'Building application ↗','inline-source')
    neighborhoods=project['neighborhood']
    body='<main id="main" class="detail-main"><nav class="breadcrumb" aria-label="Breadcrumb"><a href="../index.html">← Back to the development map</a><span>'+escape(neighborhoods)+'</span></nav><div class="detail-intro"><div class="eyebrow">Development Watch <span aria-hidden="true">/</span> '+escape(neighborhoods)+'</div><h1>'+escape(project['name'])+'</h1><p class="detail-address">'+escape(project['address'])+'</p><div class="intro-meta"><span class="status-chip '+escape(project['state'])+'">'+escape(project['state_label'])+'</span><span>City status: <strong>'+escape(project['status'])+'</strong></span><span>Observed '+escape(project['observed'])+'</span></div></div>'+stats(project)+'<div class="detail-layout"><div class="reading-column"><section class="reading-section opening"><div class="section-heading"><span class="section-number">01</span><h2>What’s proposed</h2></div><p class="lead">'+escape(project['summary'])+'</p><p>'+reference+'</p><p class="muted">This is a proposal described in a permit record. Construction, completion and business openings have not been verified.</p></section>'
    questions=[]
    if assessment['status']=='hold_conflict':
        counts=' and '.join(f'{n:g}' for n in assessment['height_in_description']) or 'no explicit story count'
        numeric=f"{assessment['height_numeric_field']:g}" if assessment['height_numeric_field'] else 'no usable count'
        questions.append(('How tall is the building?',f"The description lists {counts} stories; the separate city field reports {numeric}. Supporting plans are needed before we can give one confirmed height."))
    if facts.get('status')=='conflict' and facts.get('row'):
        questions.append(('Why are there two addresses?', 'The city GIS lists '+project['address']+', while the intake report lists '+pretty(facts['row']['address'])+'. Both are retained until the discrepancy is reconciled.'))
    if facts.get('status')=='not_listed':
        questions.append(('Is there an intake report?', 'No matching row was found in the report query used for this review. That is a coverage gap, not evidence that the application was denied or withdrawn.'))
    if assessment['height_numeric_field'] is None:
        questions.append(('Where does the height come from?', 'The story count comes from the explicit project description. The separate numeric field does not supply a usable height.'))
    for entry in payload['research_evidence']:
        if entry['kind']=='address-change-register':
            d=entry['data']; rows=d.get('matching_rows',[])
            if rows:
                changes=' '.join('The register labels '+pretty(r['ORIGINAL SITUS ADDRESS'])+' → '+pretty(r['NEW SITUS ADDRESS'])+' as '+r['CHANGE STATUS']+'.' for r in rows)
                questions.append(('What does the property register show?', 'The linked official property is '+d['property_id']+'. '+changes+' Address changes do not establish construction or a completed building.'))
    if not questions: questions.append(('What remains to be checked?', 'Supporting plans, current participation opportunities and any evidence of work on site still need review.'))
    body+='<section class="reading-section"><div class="section-heading"><span class="section-number">02</span><h2>What needs a closer look</h2></div><div class="questions">'+''.join('<article class="question"><h3>'+escape(title)+'</h3><p>'+escape(answer)+'</p></article>' for title,answer in questions)+'</div>'
    for entry in payload['research_evidence']:
        if entry['kind']=='address-change-register' and entry['data'].get('matching_rows'):body+='<p>'+link(entry['source_url'],'Official address-change register ↗','inline-source')+'</p>'
    body+='</section><section class="reading-section"><div class="section-heading"><span class="section-number">03</span><h2>Where it stands</h2></div><p>The building record is marked <strong>'+escape(project['status'])+'</strong>. '+('An issued permit does not establish completed work.' if project['status'].casefold()=='issued' else 'This record does not establish a final city decision or completed construction.')+'</p><ol class="timeline"><li><span class="timeline-date">'+escape(project['created'])+'</span><h3>Permit record created</h3><p>Date from the city’s record-creation field.</p></li><li><span class="timeline-date">'+escape(project['observed'])+'</span><h3>Added to our watch</h3><p>Initial collection establishes a baseline. It does not mean the proposal first appeared on this date.</p></li><li><span class="timeline-date">Next</span><h3>'+('Resolve the record differences' if project['state']=='hold' else 'Follow the official record')+'</h3><p>'+('Confirm the flagged details against sufficient official evidence before this becomes a reviewed report.' if project['state']=='hold' else 'Watch for published changes, supporting plans and verified participation opportunities.')+'</p></li></ol><div class="participation"><h3>Want to follow or participate?</h3><p>Current hearings, comment deadlines and participation instructions have not been verified for this project. Check its official records for published notices.</p>'+link(record['source_url'],'Check the city’s project record ↗','text-link')+'</div></section>'+evidence_section(store,record,payload)
    related=[]
    for entry in payload['research_evidence']:
        if entry['kind']!='related-permit':continue
        for row in entry['data'].get('records',[]):
            a=row['attributes']
            related.append('<article class="related-record"><h3>'+escape(a.get('PERMIT') or 'Related permit')+'</h3><p>'+escape(a['DESCRIPTION'])+'</p><p class="muted">Official status: <strong>'+escape(a['STATUS'])+'</strong> · '+escape(a['APPLICATION'])+'</p>'+link(a['PORTLAND_MAPS_URL'],'View this separate permit ↗','text-link')+'</article>')
    if related: body+='<section class="reading-section"><h2>Related records</h2><p>These permits are explicitly referenced by the building description. Their statuses belong to those records, not to the whole project.</p>'+''.join(related)+'</section>'
    body+='</div><aside class="project-aside"><div class="aside-card"><div class="eyebrow">The location</div><h2>'+escape(neighborhoods)+'</h2><div class="mini-map" id="map" aria-label="Map showing the official permit point"></div><p>'+escape(project['address'])+'</p><p class="muted small">Pin follows the city’s permit point. It is not a verified building footprint. Different recorded addresses remain disclosed above.</p><a class="text-link" href="../index.html#project-'+str(project['id'])+'">Find it on the development map ↗</a></div><div class="aside-card quiet"><div class="eyebrow">Record notes</div><dl class="record-notes"><dt>Source agency</dt><dd>City of Portland</dd><dt>Building application</dt><dd>'+escape(project['application'])+'</dd><dt>Reporting status</dt><dd>'+escape(project['state_label'])+'</dd></dl><p class="small muted">AI-assisted workflow, with deterministic summaries from official evidence. This private draft has not been publicly released.</p></div></aside></div></main>'
    return shell(project['name'],body,'../',{'projects':[project],'detail':True,'selected':project['id']})


def boundaries(store):
    rows=[r for r in store.rows('boundary_datasets') if r['kind']=='neighborhood' and r['complete']]
    if not rows:return {'type':'FeatureCollection','features':[]}
    features=[]
    for f in json.loads(rows[-1]['data'])['feature_collection']['features']:
        geometry=shape(f['geometry'])
        if not geometry.is_valid:continue
        geometry=geometry.simplify(.00008,preserve_topology=True)
        features.append({'type':'Feature','geometry':mapping(geometry),'properties':{'name':pretty(f['properties']['NAME'])}})
    return {'type':'FeatureCollection','features':features}


def write_pages(store, output, contexts):
    output=Path(output); shutil.copytree(ASSETS,output/'assets',dirs_exist_ok=True)
    projects=[]
    for record,report,version,payload in contexts:
        research=payload['research_evidence']; tags=payload['spatial_tags']
        project=view_model(record,report,version,tags,research); projects.append(project)
        (output/str(project['id'])/'review.html').write_text(detail_page(store,record,report,version,payload,project))
        from ..rendering import markdown_text
        esc=markdown_text
        lines=['# '+esc(project['name']), '', esc(project['state_label'])+' · '+esc(project['neighborhood']), '', esc(project['summary']), '',
               '## At a glance', '', '| Detail | Official record |', '| --- | --- |',
               '| Stories | '+esc(project['height'])+' · '+esc(project['height_note'])+' |',
               '| Proposed homes | '+esc(project['homes'])+' |',
               '| Business space | '+esc(project['commercial'])+' · '+esc(project['commercial_note'])+' |',
               '| City permit status | '+esc(project['status'])+' |', '',
               'GIS address: '+esc(project['address'])+'.', '', '[Building application]('+record['source_url']+').', '',
               'Construction, completion and business opening are unverified.', '', '## Open questions', '']
        for question in payload['holds']:lines += [esc(question)+'.', '']
        crosscheck=json.loads(record['data']).get('intake_crosscheck',{})
        row=crosscheck.get('facts',{}).get('row')
        if row:lines += ['Intake-report address: '+esc(pretty(row['address']))+'.', '', '[Official intake report]('+crosscheck['source_url']+').', '']
        elif crosscheck:lines += ['No matching intake row appeared in the scoped query; this is not a denial or withdrawal.', '']
        lines += ['## Timeline', '', 'Permit record created: '+project['created']+'.', '', 'Added to our watch: '+project['observed']+'. Initial discoveries establish a baseline, not new activity today.', '',
                  'Current hearings and participation deadlines have not been verified for this project.', '']
        (output/str(project['id'])/'article.md').write_text('\n'.join(lines))
    projects.sort(key=lambda p:p['name'])
    body='<main id="main" class="map-main"><div class="map-intro"><div><div class="eyebrow">Neighborhoods <span aria-hidden="true">/</span> Buildings <span aria-hidden="true">/</span> What’s next</div><h1>A closer look at<br>Portland’s next places.</h1><p>Explore proposed buildings with homes and business space.<br class="desktop-break"> Start with a pin. Get the story behind the address.</p></div><div class="map-focus"><span class="focus-number">04<span>+</span></span><span>stories, with residences<br>and commercial space</span></div></div><div class="explorer"><section class="map-panel" aria-label="Development map"><div id="map" class="main-map" aria-label="Interactive map of development proposals"></div><div class="map-top-note"><span class="live-dot"></span>Portland · '+str(len(projects))+' projects to explore</div><button class="map-reset" id="map-reset" type="button">Show all pins <span aria-hidden="true">↗</span></button><div class="map-legend"><span><i class="legend-dot"></i>Source-linked draft</span><span><i class="legend-dot held"></i>Open questions</span></div><p class="map-fallback" id="map-fallback" hidden>Street map unavailable. Official neighborhood boundaries and project pins remain available.</p></section><aside class="explorer-side" aria-label="Projects on the map"><div class="explorer-tools"><div class="eyebrow">Your development watch</div><div class="side-heading"><h2>Find a project</h2><span id="project-count" aria-live="polite">'+str(len(projects))+' projects</span></div><label class="search-label" for="project-search">Address, project or neighborhood</label><div class="search-wrap"><span aria-hidden="true">⌕</span><input id="project-search" type="search" placeholder="Try Division or Homestead…" autocomplete="off"></div><div class="filter-row" role="group" aria-label="Filter by reporting status"><button class="filter active" type="button" data-filter="all" aria-pressed="true">All</button><button class="filter" type="button" data-filter="clear" aria-pressed="false">No conflicts</button><button class="filter" type="button" data-filter="hold" aria-pressed="false">Open questions</button></div></div><div class="project-list" id="project-list">'+''.join(card(p,i+1) for i,p in enumerate(projects))+'<p id="empty-projects" class="empty-projects" hidden>No projects match that search. Try another address or neighborhood.</p></div></aside></div><div class="coverage-footer"><div><span class="eyebrow">A focused starting point</span><p>These are selected candidates from the city’s commercial new-construction records. This map does not show every Portland or county development.</p></div><div><span class="eyebrow">Read with the right context</span><p>Pins use official permit points, not building footprints. Initial discoveries are a baseline; applications are not completed projects. Coverage is generated automatically. Specific uncertainties stay visible.</p></div></div><noscript><p>Interactive pins need JavaScript. The project links above remain available.</p></noscript></main>'
    (output/'index.html').write_text(shell('Development Watch',body,map_data={'projects':projects,'detail':False,'boundaries':boundaries(store)}))
