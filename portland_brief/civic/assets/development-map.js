/* Local assets and embedded points work under file:// as well as localhost. */
(() => {
  'use strict';
  const dataNode = document.getElementById('map-data');
  if (!dataNode || !window.L) return;
  const data = JSON.parse(dataNode.textContent);
  const projects = data.projects;
  const map = L.map('map', {zoomControl:false, scrollWheelZoom:false, attributionControl:true});
  L.control.zoom({position:'bottomright'}).addTo(map);
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution('<a href="https://www.portlandmaps.com/">Map &amp; permit points: City of Portland</a>');
  const layers = L.layerGroup().addTo(map);
  const markers = new Map();
  const boundaryLayer = data.boundaries ? L.geoJSON(data.boundaries, {interactive:false, style:{color:'#bccab4',weight:1,fillColor:'#e3eadc',fillOpacity:.55}}).addTo(map) : null;
  const tiles = L.tileLayer('https://www.portlandmaps.com/arcgis/rest/services/Public/Basemap_Gray_Complete/MapServer/tile/{z}/{y}/{x}', {maxZoom:19, maxNativeZoom:19, minZoom:10, detectRetina:false});
  let tileErrors = 0;
  tiles.on('tileerror', () => { tileErrors++; if (tileErrors>2) { const note=document.getElementById('map-fallback'); if (note) note.hidden=false; } });
  tiles.on('tileload', () => { const note=document.getElementById('map-fallback'); if (note) note.hidden=true; });
  tiles.addTo(map);
  const node = (tag, text, css) => {const el=document.createElement(tag); if (text) el.textContent=text; if (css) el.className=css; return el;};
  const validPoint = p => Number.isFinite(p.lat) && Number.isFinite(p.lng);
  function fit(items) {
    const points = items.filter(validPoint).map(p=>[p.lat,p.lng]);
    if (points.length>1) map.fitBounds(points,{padding:[65,70],maxZoom:14});
    else if (points.length===1) map.setView(points[0],data.detail?16:14);
    else map.setView([45.518,-122.673],12);
  }
  function popup(project) {
    const box=node('div');
    box.append(node('div',project.neighborhood,'popup-neighborhood'),node('h3',project.name),node('p',project.address));
    box.append(node('p',`${project.height} stories · ${project.homes} homes proposed`,'popup-facts'));
    box.append(node('span',project.state_label,'status-chip '+project.state));
    const a=node('a','Explore this project ↗','popup-link'); a.href=project.url; box.append(a); return box;
  }
  function select(project, move=true) {
    document.querySelectorAll('.project-card').forEach(card=>card.classList.toggle('selected',card.dataset.id===String(project.id)));
    const marker=markers.get(project.id);
    if (marker) {if (move) map.panTo(marker.getLatLng()); marker.openPopup();}
    const card=document.querySelector(`.project-card[data-id="${project.id}"]`);
    if (card && window.innerWidth>720) {
      const list=document.getElementById('project-list');
      const top=card.getBoundingClientRect().top-list.getBoundingClientRect().top+list.scrollTop;
      list.scrollTo({top:Math.max(0,top-8),behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    }
  }
  projects.forEach((p,i)=>{
    if (!validPoint(p)) return;
    const icon=L.divIcon({className:'development-pin',html:'<div class="pin-body'+(p.state==='hold'?' held':'')+'"><span>'+(i+1)+'</span></div>',iconSize:[34,42],iconAnchor:[17,39],popupAnchor:[0,-35]});
    const marker=L.marker([p.lat,p.lng],{icon,title:p.name+' — '+p.address,alt:p.name+'; open project preview',keyboard:true}).addTo(layers);
    if (!data.detail) {marker.bindPopup(popup(p),{maxWidth:290});marker.on('click',()=>select(p,false));}
    else marker.bindTooltip(p.name,{direction:'top',offset:[0,-38]});
    markers.set(p.id,marker);
  });
  fit(projects);
  const search=document.getElementById('project-search');
  let filter='all';
  function update() {
    const term=search?search.value.trim().toLocaleLowerCase():'';
    const visible=projects.filter(p=>(filter==='all'||(filter==='clear'?p.state!=='hold':p.state===filter))&&[p.name,p.address,p.neighborhood].join(' ').toLocaleLowerCase().includes(term));
    const ids=new Set(visible.map(p=>p.id));
    document.querySelectorAll('.project-card').forEach(c=>{c.hidden=!ids.has(Number(c.dataset.id));});
    markers.forEach((m,id)=>{if(ids.has(id)) layers.addLayer(m); else layers.removeLayer(m);});
    const count=document.getElementById('project-count');if(count) count.textContent=visible.length+' '+(visible.length===1?'project':'projects');
    const empty=document.getElementById('empty-projects');if(empty) empty.hidden=visible.length>0;
    fit(visible);
  }
  if(search) search.addEventListener('input',update);
  document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
    filter=button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});update();
  }));
  const reset=document.getElementById('map-reset');if(reset) reset.addEventListener('click',()=>{
    filter='all';if(search) search.value='';document.querySelectorAll('[data-filter]').forEach(b=>{const active=b.dataset.filter==='all';b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});map.closePopup();update();
  });
  const selected=Number(location.hash.replace('#project-',''));
  if(selected && !data.detail) {const p=projects.find(p=>p.id===selected);if(p) select(p);}
  // Browser checks can inspect rendered state without exposing private source payloads.
  window.developmentMap={map,markers};
})();
