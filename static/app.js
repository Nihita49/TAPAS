"use strict";
/* TAPAS 4-city front-end.
   Views: INDIA (national landing map + city markers) -> CITY (real Leaflet
   ward map) -> WARD detail. Mortality & Hospitalization Spike are separate
   logistics on the same weighted H/V/E/AC factors as HTSI.
   Map: real Leaflet map on Esri's keyless World Light Gray Canvas tiles
   (CARTO's equivalent free tiles started requiring an API key in Sept 2026,
   so this uses a still-genuinely-keyless provider instead). Ward/state
   polygons are real GeoJSON from the backend (data/cities/{City}/wards.geojson,
   data/processed/india_simp.geojson) rendered as genuine Leaflet vector
   layers -- no static basemap image, no hand-rolled projection. */
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const BCOL={"Low":"#2e9e5b","Moderate":"#e8a51d","High":"#f0722c","Severe":"#dd3a3a","Insufficient":"#c7d0db"};
const LAYERLAB={"htsi":"Hazard risk (HTSI)","mort":"Mortality risk","hosp":"Hospitalization Spike","utci":"UTCI hazard (°C)","veg":"Satellite vegetation (%)"};
const INDIA_CENTER=[22.9,79.0], INDIA_ZOOM=5;
const ESRI_GRAY_BASE="https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const ESRI_GRAY_REF="https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTR='Tiles &copy; Esri — Esri, DeLorme, NAVTEQ';
const st={cities:[],india:null,view:"india",city:null,layer:"htsi",geo:null,mapmeta:null,sim:null,
  map:null,wardLayers:{},filterBands:new Set(["Low","Moderate","High","Severe"]),searchIndex:[],
  cityFullBounds:null,_selectedWardId:null};
async function api(p,opts){const r=await fetch(p,Object.assign({headers:{"Content-Type":"application/json"}},opts));if(!r.ok)throw new Error((await r.text()).slice(0,140));return r.json();}
const sleep=ms=>new Promise(res=>setTimeout(res,ms));
function heat(v,min,max){const t=Math.max(0,Math.min(1,(v-min)/(max-min||1)));return `rgb(${Math.round(30+215*t)},${Math.round(60+120*(1-t))},${Math.round(210-150*t)})`;}

/* ---- real Leaflet map, created once. India view and City/Ward views all
   share this ONE map instance -- switching levels is a genuine geographic
   flyTo()/fitBounds() on real coordinates, not a content swap. ---- */
function initMap(){
  st.map=L.map("map",{zoomControl:false,minZoom:4,maxZoom:18,scrollWheelZoom:true}).setView(INDIA_CENTER,INDIA_ZOOM);
  L.tileLayer(ESRI_GRAY_BASE,{attribution:ESRI_ATTR,maxZoom:19,maxNativeZoom:16}).addTo(st.map);
  L.tileLayer(ESRI_GRAY_REF,{maxZoom:19,maxNativeZoom:16}).addTo(st.map);   // place-name labels layer
  st.indiaLayer=L.layerGroup().addTo(st.map);
  st.markerLayer=L.layerGroup().addTo(st.map);
  st.wardLayerGroup=L.layerGroup().addTo(st.map);
  st.map.on("click",()=>{const d=$("#searchDrop"); if(d)d.classList.add("hidden");});
  // ---- India<->City hierarchy stays aligned with the CAMERA, not just with
  // button clicks: if the person zooms/pinches/scrolls back out to national
  // level themselves (rather than clicking the "India" breadcrumb), restore
  // the national view + city markers automatically instead of leaving them
  // stuck on the city they'd zoomed into with nothing else visible. ----
  st.map.on("zoomend",()=>{
    if(st.view==="city"&&st.map.getZoom()<=INDIA_ZOOM+1)goIndia();
  });
}
/* ---- fade a layer group's vector paths out, then clear it -- avoids the
   abrupt "everything vanishes to blank basemap" flash that used to happen
   when india/city layers were cleared synchronously mid-flyTo. ---- */
function fadeClearLayers(groups,ms=380){
  groups.forEach(g=>g.eachLayer(l=>{const el=l.getElement&&l.getElement();
    if(el){el.style.transition=`opacity ${ms}ms ease`; el.style.opacity=0;}}));
  setTimeout(()=>groups.forEach(g=>g.clearLayers()),ms);
}


/* ---------------- boot & header ---------------- */
async function boot(){
  try{
    initMap();
    const [a,b]=await Promise.all([api("/api/india"),api("/api/cities")]);
    st.india=a; st.cities=b.cities; st.sim=b.sim; paintLive();
    $("#outbox").onclick=async()=>modal(await outboxHTML());
    $("#mclose").onclick=()=>$("#modal").classList.add("hidden");
    $("#modal").addEventListener("click",e=>{if(e.target.id==="modal")$("#modal").classList.add("hidden");});
    document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("#modal").classList.contains("hidden"))$("#modal").classList.add("hidden");});
    $("#crumbRoot").onclick=()=>goIndia();
    $("#crumbCity").onclick=()=>{if(st._selectedWardId)backToCityMap();};
    $("#simToggle").onclick=()=>$("#simPop").classList.toggle("hidden");
    $("#simApply").onclick=async()=>{await applySim();$("#simPop").classList.add("hidden");};
    $("#simReset").onclick=async()=>{$("#simRange").value=0;$("#simVal").textContent="+0 °C";await applySim();await goIndia();};
    $("#simRange").oninput=()=>$("#simVal").textContent="+"+$("#simRange").value+" °C";
    document.addEventListener("click",e=>{if(!e.target.closest("#simbar"))$("#simPop").classList.add("hidden");});
    document.querySelectorAll("#layers button").forEach(b=>b.onclick=()=>setLayer(b.dataset.l));
    $("#ovBtn").onclick=()=>toggleOverview();
    $("#filterToggle").onclick=()=>{$("#filterbar").classList.toggle("hidden");};
    wireFilterDrag();
    $("#sideClose").onclick=()=>closeSide();
    $("#zIn").onclick=()=>st.map.zoomIn(); $("#zOut").onclick=()=>st.map.zoomOut();
    $("#zHome").onclick=()=>goIndia();
    wireSearch();
    buildSearchIndex();   // background; doesn't block first paint
    warmTrendCache();     // pre-fetch the 30-day trend so it's ready before anyone opens a city
    renderIndia();
  }catch(e){$("#sidepanel").innerHTML="<div class='placeholder'>Load error: "+esc(e.message)+"</div>";}
}
function paintLive(){const b=$("#live");const live=st.cities&&st.cities.some(c=>c.weather_prov==="live");b.className="pill"+(live?" liveok":"");b.textContent=live?"● Live · 4 cities":"● meteo fallback";}
function cityInfo(id){return st.cities.find(c=>c.id===id);}
async function goIndia(){renderIndia(); st.map.flyTo(INDIA_CENTER,INDIA_ZOOM,{duration:0.9});}

/* ---- ward search: cross-city index built once in the background; the
   dropdown scopes to the current city once one is open. Selecting a
   result reuses the same zoom + drawer + pulse path a direct map click
   would take, so the two ways of reaching a ward feel identical. ---- */
async function buildSearchIndex(){
  for(const c of st.cities){
    try{
      const d=await api(`/api/city/${c.id}/wards`);
      d.features.forEach(f=>{const p=f.properties;
        st.searchIndex.push({city:c.id,cityName:c.name,id:p.id,label:p.label,band:p.band,available:p.available});});
    }catch(e){/* one city failing to index shouldn't block the others */}
  }
}
function updateSearchPlaceholder(){
  const inp=$("#wardSearch"); if(!inp)return;
  inp.placeholder=st.view==="city"?`Search a ward in ${cityInfo(st.city).name}…`:"Search any ward across India…";
}
function searchMatches(q){
  q=q.trim().toLowerCase(); if(!q)return [];
  const scoped=st.view==="city"?st.searchIndex.filter(w=>w.city===st.city):st.searchIndex;
  return scoped.filter(w=>w.available!==false&&(w.label||"").toLowerCase().includes(q)).slice(0,8);
}
let _searchMatches=[];
function renderSearchDrop(matches){
  _searchMatches=matches; const el=$("#searchDrop");
  if(!matches.length){el.innerHTML=`<div class="sres-empty">No matching wards</div>`;el.classList.remove("hidden");return;}
  el.innerHTML=matches.map((m,i)=>{
    const ci=cityInfo(m.city); const fb=(ci&&ci.weather_prov&&ci.weather_prov!=="live")?`<span class="fb">fallback data</span>`:"";
    return `<div class="sres" data-i="${i}"><span class="dot" style="background:${_WCOL[m.band]||"#c7d0db"}"></span><span class="lab">${esc(m.label)}</span>${st.view!=="city"?`<span class="city">${esc(m.cityName)}</span>`:""}${fb}</div>`;
  }).join("");
  el.classList.remove("hidden");
  el.querySelectorAll(".sres").forEach((row,i)=>row.onclick=()=>selectSearchResult(matches[i]));
}
async function selectSearchResult(m){
  $("#searchDrop").classList.add("hidden"); $("#wardSearch").value="";
  if(st.view!=="city"||st.city!==m.city)await openCity(m.city);
  await openWard(m.id);
}
function wireSearch(){
  const inp=$("#wardSearch");
  const run=()=>{const q=inp.value;if(q.trim())renderSearchDrop(searchMatches(q));else $("#searchDrop").classList.add("hidden");};
  inp.addEventListener("input",run); inp.addEventListener("focus",run);
  document.addEventListener("click",e=>{if(!e.target.closest(".searchWrap"))$("#searchDrop").classList.add("hidden");});
  document.addEventListener("keydown",e=>{if(e.key==="Escape")$("#searchDrop").classList.add("hidden");});
}

/* ================= INDIA (landing) = proper national GIS ================= */
function renderIndia(){
  st.view="india";
  $("#hov").classList.remove("hidden");
  $("#layers").classList.add("hidden");
  $("#filterbar").classList.add("hidden"); $("#filterbar").innerHTML="";
  $("#filterToggle").classList.add("hidden"); $("#simToggle").classList.add("hidden");
  $("#ovBtn").classList.remove("hidden"); $("#ovBtn").textContent="Overview";
  $("#searchWrap").classList.add("hidden"); $("#searchDrop").classList.add("hidden");
  $("#side").classList.add("hidden"); st._selectedWardId=null;
  $("#crumbRoot").classList.add("cur"); $("#crumbRoot").textContent="India";
  $("#sepCrumb").classList.add("hidden"); $("#crumbCity").classList.add("hidden");
  $("#sepCrumb2").classList.add("hidden"); $("#crumbWard").classList.add("hidden");
  updateSearchPlaceholder();
  renderNationalMap();
  $("#legend").innerHTML=`<span style="font-size:11px;font-weight:700">National coverage</span>
    <span class="cell"><span class="sw" style="background:#1467f0"></span>Live pilot city — click to open</span>
    <span class="cell"><span class="sw" style="background:#0a1e40;border:0"></span>State boundaries</span>`;
  renderSideIndia();
}
/* ---- real geographic India layer: actual state-boundary GeoJSON +
   circleMarkers at each city's real [lon,lat] centre. Replaces the old
   hand-projected SVG-over-a-static-PNG approach entirely. ---- */
function renderNationalMap(){
  st.indiaLayer.clearLayers(); st.markerLayer.clearLayers();
  if(st.wardLayerGroup.getLayers().length)fadeClearLayers([st.wardLayerGroup]);   // fade any ward polygons still on screen instead of yanking them out
  const pilotStates=["Maharashtra","Gujarat","Tamil Nadu","Telangana"];
  L.geoJSON(st.india.states,{
    style:f=>{const pilot=pilotStates.includes(f.properties.name);
      return {fillColor:pilot?"#cfe3f7":"transparent",fillOpacity:pilot?0.35:0,
        color:pilot?"#1467f0":"rgba(15,40,80,0.28)",weight:pilot?1.4:0.7};},
    onEachFeature:(f,layer)=>{
      const pilot=pilotStates.includes(f.properties.name);
      layer.on("mouseover",()=>{$("#hov").innerHTML=esc(f.properties.name)+(pilot?" — live pilot state (click a city marker).":" — roll-out roadmap state.");});
    }
  }).addTo(st.indiaLayer);
  (st.india.cities||[]).forEach(c=>{
    const [lon,lat]=c.centre;
    const m=L.circleMarker([lat,lon],{radius:9,color:"#1467f0",weight:2.5,fillColor:"#fff",fillOpacity:1}).addTo(st.markerLayer);
    m.bindTooltip(`<b>${esc(c.name)}</b> (${esc(c.state)}) · ${c.n_wards} wards`,{direction:"top",offset:[0,-10],className:"wtip"});
    m.on("click",()=>openCity(c.id));
  });
  $("#hov").textContent="India — four live pilot cities on a real GIS map. Click a marker.";
}

const _WBAND=["Low","Moderate","High","Severe"];
const _WCOL={"Low":"#2e9e5b","Moderate":"#e8a51d","High":"#f0722c","Severe":"#dd3a3a","Insufficient":"#c7d0db"};
async function renderSideIndia(){
  $("#sidepanel").innerHTML=`<div class="ov-head"><h2>National heat-watch</h2>
    <p style="font-size:12px">Pick a pilot city below or on the map to see its ward-level risk.</p></div>
    <details class="dcard" style="margin:8px 0"><summary><h4>About this model</h4><span class="dcard-teaser">early-warning, not validated</span></summary>
      <div class="dcard-body"><p style="font-size:12px;color:#23344a;margin:6px 0">A weighted model combining heat hazard, vulnerability, exposure and adaptive capacity.</p>
      <div class="cav"><b>Calibration caveat:</b> V index magnitude, mortality coefficients and any projection/allocation are <b>defensible defaults, not validated</b> &mdash; India publishes no ward-level outcome data. Treat them as early-warning signals.</div></div></details>
    <div id="nwBox"><div class="placeholder">Loading live national heat-watch&hellip;</div></div>`;
  loadWatch(0);
}
async function loadWatch(attempt){
  const m=$("#nwBox"); if(!m) return;
  try{
    const w=await api("/api/india/watch");
    if(w && w.warming){
      m.innerHTML="<div class='placeholder'>Building the live national snapshot from live weather&hellip; (this takes a few seconds on first load)</div>";
      if(attempt<40){ setTimeout(()=>loadWatch(attempt+1), 3000); }
      else m.innerHTML="<div class='cav'>National watch still warming up &mdash; refresh to retry.</div>";
      return;
    }
    renderWatch(w,m);
  }catch(e){ if(attempt<10){ setTimeout(()=>loadWatch(attempt+1),3000);} else m.innerHTML="<div class='cav'>National watch temporarily unavailable ("+esc(e.message)+").</div>"; }
}
function renderWatch(w,mount){
  if(!mount) return;
  const cities=w.cities||[]; const total=w.total||{}; const bands=total.bands||{};
  const escA=bands.High||0, escB=bands.Severe||0, active=escA+escB;
  const cards=cities.map(c=>{
    const bb=_WBAND[Math.max(c.worst_htsi||0,c.worst_mort||0)]||"Low";
    const col=_WCOL[bb]||"#c7d0db"; const n=c.n_wards||1;
    const seg=_WBAND.map(b=>{const k=c.bands[b]||0;const p=Math.round(k/n*100);
      return p?`<i style="width:${p}%;background:${_WCOL[b]}" title="${b}: ${k}"></i>`:"";}).join("");
    const ins=c.insufficient>0?`<span class="nw-ins"> · ${c.insufficient} no data</span>`:"";
    return `<div class="nw-card" data-id="${esc(c.city)}" title="Open ${esc(c.name)}">
      <div class="nw-top"><div><div class="nw-city">${esc(c.name)}</div>
      <div class="nw-state">${esc(c.state)} &middot; ${c.n_wards} wards</div></div>
      <span class="nw-pill" style="background:${col}">${bb}</span></div>
      <div class="nw-dist">${seg}</div>
      <div class="nw-meta"><span>${c.available} wards resolved${ins}</span><span>peak ${bb}</span></div></div>`;
  }).join("");
  const hot = active>0 ? `<span style="color:#c2410c;font-weight:700">${active} ward${active>1?"s":""} now High/Severe</span>` : `<span>no ward currently escalated to High/Severe</span>`;
  mount.innerHTML=`<div class="nw-head"><h3>Live national heat-watch</h3>
     <span class="nw-ts" title="Ward snapshots recomputed on the live model">updated ${(w.ts||"").slice(0,16).replace("T"," ")} UTC</span></div>
     <div class="nw-sum"><span><b>${total.available||0}</b> wards resolved</span>
     <span><b style="color:#c2410c">${active}</b> High/Severe</span>
     <span>${hot}</span></div>
     <div class="nw-grid">${cards}</div>
     <div class="nw-note">Click a city card to open its live ward map &mdash; or a city marker on the map. Hover bars for per-band counts. Mortality / Hospitalization Spike are model outputs, not clinical forecasts.</div>`;
  mount.querySelectorAll(".nw-card").forEach(el=>el.onclick=()=>openCity(el.dataset.id));
}
let _trendCache=null,_trendCacheTs=0,_trendWarming=false;
async function warmTrendCache(){
  // pre-fetch the 30-day series at boot, in parallel with everything else,
  // so by the time someone opens a city and clicks the trend tab the data
  // is already sitting in cache instead of racing a fresh fetch.
  if(_trendWarming)return; _trendWarming=true;
  try{_trendCache=await api("/api/trend?days=30");_trendCacheTs=Date.now();}catch(e){/* cityTrendCard will retry itself */}
}
async function cityTrendCard(city,attempt){
  attempt=attempt||0;
  const sp=$("#sidepanel"); if(!sp)return;
  const slot=sp.querySelector("#cityTrendSlot");
  if(!slot)return;   // user navigated away before this resolved -- nothing to fill in
  let h=slot.querySelector("#cityTrendBox");
  if(!h){h=document.createElement("div"); h.id="cityTrendBox"; slot.appendChild(h);}
  const name=cityInfo(city).name;
  if(attempt===0)h.innerHTML=dcard("30-day heat trend",esc(name),"Loading…","<div class='prov'>Loading…</div>",true);
  try{
    const now=Date.now();
    if(!_trendCache||now-_trendCacheTs>120000){_trendCache=await api("/api/trend?days=30");_trendCacheTs=now;}
    const cityKey=String(city||"").trim().toLowerCase();
    const rows=(_trendCache.series||[]).map(p=>({p,c:(p.cities||[]).find(x=>String(x.city||"").trim().toLowerCase()===cityKey)})).filter(r=>r.c);
    if(!rows.length){
      // don't just give up on a blank/slow response -- retry patiently
      // (same pattern as the national watch card) so the chart reliably
      // shows up on its own once the backend has warmed up, rather than
      // only appearing after some unrelated action (like the simulator)
      // happens to trigger a second load.
      if(attempt<40){_trendCache=null;setTimeout(()=>cityTrendCard(city,attempt+1),3000);return;}
      h.innerHTML=dcard("30-day heat trend",esc(name),"","<div class='prov'>Trend data still warming up — check back shortly.</div>",true);
      return;
    }
    const HO={"Low":30,"Moderate":52,"High":74,"Severe":96};
    const bars=rows.map((r,i)=>{const p=r.p,b=r.c.worst||"Low",live=p.src==="live-scan";
      const dlabel=(p.ts||"").slice(8,10);
      const tip=(p.ts||"").slice(0,10)+" · "+name+": "+b+(live?" (live full scan)":" (archive backfill)");
      return `<div style="display:flex;flex-direction:column;align-items:center;flex:0 0 auto" title="${esc(tip)}">
        <i style="display:block;width:14px;height:${HO[b]||40}px;background:${_WCOL[b]||"#c7d0db"};border-radius:3px;${live?"":"opacity:.6"}"></i>
        <span style="font-size:9px;color:#6b7c94;margin-top:3px;height:11px">${(i%5===0)?dlabel:""}</span></div>`;}).join("");
    const last=rows[rows.length-1].c.worst||"Low";
    h.innerHTML=dcard("30-day heat trend",esc(name),`now: ${last}`,`
      <div style="display:flex;align-items:flex-end;gap:5px;height:150px;overflow-x:auto;padding:6px 2px">${bars}</div>
      <div style="display:flex;gap:14px;align-items:center;margin-top:6px;font-size:10.5px;color:#23344a;flex-wrap:wrap">
        ${["Low","Moderate","High","Severe"].map(b=>`<span style="display:inline-flex;align-items:center;gap:5px"><i style="width:11px;height:11px;border-radius:2px;background:${_WCOL[b]};display:inline-block"></i>${b}</span>`).join("")}
      </div>`,true);
  }catch(e){
    if(attempt<40){setTimeout(()=>cityTrendCard(city,attempt+1),3000);return;}
    h.innerHTML=dcard("30-day heat trend",esc(name),"","<div class='prov'>Trend temporarily unavailable.</div>",true);
  }
}

/* ================= CITY ================= */
async function openCity(id){
  st.city=id;st.view="city";
  st.filterBands=new Set(["Low","Moderate","High","Severe"]);   // fresh filter state per city
  st._selectedWardId=null;
  $("#layers").classList.remove("hidden");
  $("#filterToggle").classList.remove("hidden"); $("#simToggle").classList.remove("hidden");
  $("#ovBtn").textContent="City overview";
  $("#searchWrap").classList.remove("hidden");
  $("#side").classList.add("hidden");
  $("#crumbRoot").classList.remove("cur"); $("#crumbRoot").textContent="‹ India";
  $("#sepCrumb").classList.remove("hidden"); $("#crumbCity").classList.remove("hidden");
  $("#crumbCity").textContent=cityInfo(id).name;
  $("#sepCrumb2").classList.add("hidden"); $("#crumbWard").classList.add("hidden");
  updateSearchPlaceholder();
  const [lon,lat]=cityInfo(id).centre;
  // ---- one continuous zoom into the city, not two competing animations ----
  // Start heading toward the city immediately for instant feedback, fetch the
  // ward data in parallel, then let a single final flyToBounds smoothly
  // redirect that same flight onto the exact ward extent once it's known.
  // Leaflet blends a new flyTo target into an animation already in progress,
  // so this reads as one continuous zoom-in on the place clicked, rather
  // than a jump followed by an unrelated re-zoom.
  st.map.flyTo([lat,lon],12,{duration:0.9});
  // markers must be gone BEFORE the zoom animation starts, not fading during
  // it -- Leaflet's flyTo animates zoom via a CSS transform on the whole
  // vector pane, and a circleMarker's radius is fixed in *pixels* (unlike
  // real-world-sized polygons), so scaling that pane visibly balloons it
  // mid-flight. Clearing it synchronously, right here, means there's
  // nothing left in the pane to distort once the animation begins. The
  // state-boundary polygons still fade out gracefully in loadWards() below
  // -- only the small circle markers get this instant-clear treatment.
  st.markerLayer.clearLayers();
  await loadWards(false);
  if(st.cityFullBounds)st.map.flyToBounds(st.cityFullBounds,{padding:[24,24],duration:0.9});
}
const _geomCache={}; // city -> {id: geometry}, fetched once per city (static, never changes)
async function loadWards(fly=true){
  if(!_geomCache[st.city]){
    const g=await api(`/api/city/${st.city}/geometry`);
    const m={}; (g.features||[]).forEach(f=>{m[f.id]=f.geometry;});
    _geomCache[st.city]=m;
  }
  const gc=_geomCache[st.city];
  const d=await api(`/api/city/${st.city}/wards`);
  st.geo=d.features.map(f=>({...f,geometry:gc[f.properties.id]||f.geometry}));
  st.mapmeta=d.mapmeta;st.sim=d.sim;st.agg=d.aggregate||null;

  // fade the national state-boundary/city-marker layers out instead of
  // clearing them synchronously -- clearing them the instant a city is
  // clicked (while the flyTo animation is still mid-flight) was what made
  // the marker "turn white": the layers vanished onto a bare basemap before
  // the zoom had actually arrived anywhere.
  // markerLayer is already cleared synchronously in openCity() (see the
  // comment there) -- only the state-boundary polygons need the graceful fade.
  if(st.indiaLayer.getLayers().length)fadeClearLayers([st.indiaLayer]);
  st.wardLayerGroup.clearLayers();
  st.wardLayers={};
  const layer=L.geoJSON({type:"FeatureCollection",features:st.geo},{
    style:f=>wardStyle(f.properties),
    onEachFeature:(f,lyr)=>{
      const p=f.properties;
      st.wardLayers[p.id]=lyr;
      lyr.bindTooltip(hovText(p),{sticky:true,direction:"top",className:"wtip"});  // lightweight hover only
      lyr.on("click",()=>openWard(p.id));
      lyr.on("mouseover",()=>{if(st._selectedWardId!==p.id)lyr.setStyle({weight:2.2,color:"#0d1f38"});});
      lyr.on("mouseout",()=>{if(st._selectedWardId!==p.id)lyr.setStyle(wardStyle(p));});
    }
  }).addTo(st.wardLayerGroup);
  st.cityFullBounds=layer.getBounds();
  if(fly)st.map.flyToBounds(st.cityFullBounds,{padding:[24,24],duration:0.6});   // snap to the real ward extent — skipped on data-only refreshes

  setLayer(st.layer,true);
  renderFilterBar(); applyFilterDim();
  renderSideCity();
  allocationCard(cityInfo(st.city).id);
  cityTrendCard(cityInfo(st.city).id);
  $("#hov").textContent=cityInfo(st.city).name+" · municipal wards · switch view above (HTSI / Mortality / Hospitalization Spike / UTCI / Vegetation) · click a ward";
}
/* ---- city-level severity filter (Low/Moderate/High/Severe pills). Dims
   (not hides) non-matching wards so spatial context is preserved. Counts
   are recomputed from the live `st.geo` on every render/refresh. ---- */
/* ---- vertical filter sidebar (Low/Moderate/High/Severe). Semantics: start
   with everything visible; clicking a band ISOLATES the map to just that
   band (not a toggle-hide -- clicking Moderate used to remove it from the
   visible set, which read as "the band you clicked disappears"). Clicking
   the already-isolated band, or "Show all", restores every band. ---- */
function renderFilterBar(){
  const fb=$("#filterbar");
  if(st.view!=="city"){fb.classList.add("hidden");fb.innerHTML="";return;}
  const bands=["Low","Moderate","High","Severe"];
  const counts={Low:0,Moderate:0,High:0,Severe:0};
  (st.geo||[]).forEach(f=>{const b=f.properties.band;if(counts[b]!=null)counts[b]++;});
  const allOn=st.filterBands.size===bands.length;
  fb.innerHTML=`<div class="filterbar-head"><span class="filterbar-grip" title="Drag to move">⠿⠿</span>Risk filter
      <button class="filterbar-x" id="filterClose" title="Close">×</button></div>
    <div class="filterbar-list">
    <div class="fchip fchip-all${allOn?" on":""}" data-all="1">Show all</div>
    ${bands.map(b=>{
      const on=st.filterBands.has(b);
      return `<div class="fchip${on?" on":""}" data-b="${b}" style="${on?`background:${_WCOL[b]};border-color:${_WCOL[b]}`:""}">
        <span class="dot" style="background:${on?"#fff":_WCOL[b]}"></span>${b} <span class="cnt">${counts[b]}</span></div>`;
    }).join("")}
    </div>`;
  fb.querySelector("#filterClose").onclick=()=>fb.classList.add("hidden");
  fb.querySelector(".fchip-all").onclick=()=>{st.filterBands=new Set(bands);renderFilterBar();applyFilterDim();};
  fb.querySelectorAll(".fchip[data-b]").forEach(ch=>ch.onclick=()=>{
    const b=ch.dataset.b;
    if(st.filterBands.size===1&&st.filterBands.has(b))st.filterBands=new Set(bands);   // click the isolated band again -> show all
    else st.filterBands=new Set([b]);                                                  // isolate to just this band
    renderFilterBar(); applyFilterDim();
  });
}
/* ---- makes the filter sidebar a draggable floating panel (spec point 2).
   Bound once, via delegation on the never-replaced #filterbar element (its
   innerHTML gets rebuilt on every toggle, but the element itself doesn't),
   so this never needs re-wiring after a re-render. ---- */
function wireFilterDrag(){
  const fb=$("#filterbar"); if(!fb)return;
  let dragging=false,ox=0,oy=0;
  fb.addEventListener("mousedown",e=>{
    if(!e.target.closest(".filterbar-grip"))return;
    const r=fb.getBoundingClientRect();
    fb.style.position="fixed"; fb.style.left=r.left+"px"; fb.style.top=r.top+"px";
    fb.style.right="auto"; fb.style.bottom="auto";
    ox=e.clientX-r.left; oy=e.clientY-r.top; dragging=true;
    e.preventDefault();
  });
  document.addEventListener("mousemove",e=>{
    if(!dragging)return;
    fb.style.left=Math.max(4,Math.min(window.innerWidth-40,e.clientX-ox))+"px";
    fb.style.top=Math.max(4,Math.min(window.innerHeight-40,e.clientY-oy))+"px";
  });
  document.addEventListener("mouseup",()=>dragging=false);
}
function applyFilterDim(){
  (st.geo||[]).forEach(f=>{
    const p=f.properties,lyr=st.wardLayers[p.id]; if(!lyr)return;
    const dim=p.available&&!st.filterBands.has(p.band);
    const el=lyr.getElement&&lyr.getElement(); if(el)el.classList.toggle("dim",dim);
  });
}
async function allocationCard(city){
  try{
    const a=await api(`/api/city/${city}/allocation`);
    const rows=(a.rows||[]).filter(r=>r.priority_score>0).slice(0,6);
    const bcol=r=>r.mort_band==="Severe"?"#dd3a3a":r.mort_band==="High"?"#f0722c":r.mort_band==="Moderate"?"#e8a51d":"#2e9e5b";
    const h=document.createElement("div");h.id="allocCard";
    h.innerHTML=dcard("Resource allocation","top at-risk wards",`${rows.length} flagged`,`
      <table class="t"><thead><tr><th>Ward</th><th>Mort</th><th>Score</th><th>Share</th></tr></thead><tbody>
      ${rows.map(r=>`<tr><td>${esc(r.ward)}</td><td><span class="badge bg${r.mort_band}" style="font-size:10px">${r.mort_band}</span></td>
        <td>${r.priority_score}</td><td>${r.share_pct}%</td></tr>`).join("")||`<tr><td colspan="4">No wards currently elevated (all Low risk).</td></tr>`}
      </tbody></table>
      <div class="prov">Ranked by modelled HTSI &amp; mortality priority score. Deployment illustrative — not an audited plan. ${esc(a.disclosure||"")}</div>`);
    const sp=$("#sidepanel");
    const slot=sp.querySelector("#allocSlot");
    if(slot)slot.appendChild(h);
    else sp.appendChild(h);
  }catch(e){}
}

function cityWorstBand(agg){
  if(!agg||!agg.distribution)return null;
  const dist=agg.distribution.mortality||{};
  for(const b of ["Severe","High","Moderate","Low"])if(dist[b])return b;
  return null;
}
function renderSideCity(){const c=cityInfo(st.city);
  const worst=cityWorstBand(st.agg);
  const glance=worst?`<div class="glance"><span class="badge bg${worst}">${worst}</span>
      <div class="gtxt">Worst ward band right now is <b>${worst}</b>. <b>${c.n_wards}</b> wards · pop. ${(c.census2011.population/1e6).toFixed(1)} M.</div></div>`
    :`<div class="glance"><div class="gtxt"><b>${c.n_wards}</b> wards · pop. ${(c.census2011.population/1e6).toFixed(1)} M. Loading current risk…</div></div>`;
  $("#sidepanel").innerHTML=`<div class="ov-head"><h2>${esc(c.name)}</h2></div>
    ${glance}
    <div class="wtabs">
      <button class="wtab on" data-t="ov">Overview</button>
      <button class="wtab" data-t="tr">30-day trend</button>
      <button class="wtab" data-t="fc">5-day forecast</button>
      <button class="wtab" data-t="ac">Actions</button>
    </div>
    <div class="wtabpanel" data-p="ov">${cityOverviewTab(st.agg)}
      <p style="font-size:11.5px;color:var(--muted);margin-top:10px">Use the buttons above the map to colour wards by HTSI, Mortality, Hospitalization Spike, UTCI or vegetation. Click any ward for its full profile.</p></div>
    <div class="wtabpanel hidden" data-p="tr"><div id="cityTrendSlot"></div></div>
    <div class="wtabpanel hidden" data-p="fc">${cityForecastTab(st.agg)}</div>
    <div class="wtabpanel hidden" data-p="ac">${cityActionsTab(st.agg,c)}<div id="allocSlot"></div></div>
    <div class="ov-grid"><button class="btn ghost" id="openCityWardHint" style="width:100%">Back to India</button></div>`;
  $("#sidepanel").querySelector("#openCityWardHint").onclick=()=>goIndia();
  wireTabs($("#sidepanel"));
}
function cityOverviewTab(agg){
  if(!agg||!agg.distribution)return `<div class="prov">Loading…</div>`;
  const dist=agg.distribution.mortality||{};
  const total=Object.values(dist).reduce((a,b)=>a+b,0)||1;
  const LAY=["Low","Moderate","High","Severe"];
  const barH=90;
  const distSvg=`<svg viewBox="0 0 300 ${barH}" style="width:100%">`+
    LAY.map((k,i)=>{const v=dist[k]||0;const f=v/total;const x=14+i*70+8;return `<rect x="${x}" y="${barH-18-(f*barH-22)}" width="46" height="${Math.max(3,f*barH-22)}" rx="3" fill="${BCOL[k]}" opacity="0.85"><title>${k}: ${v}</title></rect><text x="${x+23}" y="${barH-18-(f*barH-22)-5}" text-anchor="middle" font-size="10" font-weight="700" fill="${BCOL[k]}">${v}</text><text x="${x+23}" y="${barH-5}" text-anchor="middle" font-size="8.5" fill="#6b7c94">${k}</text>`;}).join("")+
    `</svg>`;
  return `<div class="prov">Wards by Mortality-risk band right now:</div>${distSvg}
    <div style="font-size:11px;color:var(--muted);margin:2px 0 8px">${total} wards · live weather + real satellite environment.</div>`;
}
function cityForecastTab(agg){
  if(!agg||!agg.distribution)return `<div class="prov">Loading…</div>`;
  const outlook=(agg.outlook||[]).map(p=>({lab:shortDay(p.day),mort:p.mortality_prob,hosp:p.hosp_prob}));
  const fwd=outlook.length?outlookLine(outlook):"";
  return `<div class="prov">City-wide mean risk, next 5 days:</div>
    ${fwd||`<div class="prov">Forecast data warming up — check back shortly.</div>`}`;
}
function cityActionsTab(agg,c){
  if(!agg)return `<div class="prov">Loading…</div>`;
  return `<div style="font-size:11px;color:var(--muted);margin-bottom:6px">Alert level: <b>${esc(agg.alert_level||"—")}</b></div>
  <ul style="margin:6px 0;padding-left:18px">${(agg.admin_measures||[]).map(m=>`<li style="font-size:13px;line-height:1.55;margin:5px 0">${esc(m)}</li>`).join("")}</ul>
  <div class="prov">Two-tier response modelled on the Ahmedabad Heat Action Plan. Resident SMS guidance is set per ward (click a ward).</div>`;
}
function outlookLine(pts){
  const W=300,H=120,padL=8,padB=20,padT=14,padR=8;
  const iw=W-padL-padR,ih=H-padT-padB;
  const xp=i=>padL+(pts.length<=1?iw/2:iw*i/(pts.length-1));
  const max=Math.max(...pts.map(p=>Math.max(p.mort,p.hosp,0.01)),0.1)*1.15;
  const yp=v=>padT+ih-(v/max)*ih;
  const mcol=v=>v<0.06?"#2e9e5b":v<0.22?"#e8a51d":v<0.45?"#f0722c":"#dd3a3a";
  const line=(k,st)=>{let d="";pts.forEach((p,i)=>{d+=(d?"L":"M")+xp(i).toFixed(1)+" "+yp(p[k]).toFixed(1)+" ";});return `<path d="${d}" fill="none" stroke="${st}" stroke-width="2.3"/>`+pts.map((p,i)=>`<circle cx="${xp(i).toFixed(1)}" cy="${yp(p[k]).toFixed(1)}" r="3.2" fill="${mcol(p[k])}" stroke="#fff" stroke-width="1"/>`).join("");};
  const grid=Array.from({length:4},(_,k)=>`<line x1="${padL}" x2="${W-padR}" y1="${yp(max/4*(k+1))}" y2="${yp(max/4*(k+1))}" stroke="#eef2f7" stroke-width="1"/>`).join("");
  const dots=pts.map((p,i)=>`<text x="${xp(i)}" y="${H-6}" text-anchor="middle" font-size="8" fill="#6b7c94">${esc(p.lab)}</text>`).join("");
  const vals=pts.map((p,i)=>`<text x="${xp(i)}" y="${yp(p.mort)-5}" text-anchor="middle" font-size="8" font-weight="700" fill="${mcol(p.mort)}">${Math.round(p.mort*100)}%</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%">${grid}${line("hosp","#e07bb0")}${line("mort","#1467f0")}${vals}${dots}</svg>
    <div style="display:flex;gap:14px;font-size:11px"><span><span class="sw" style="background:#1467f0"></span>Mortality</span><span><span class="sw" style="background:#e07bb0"></span>Hospitalization Spike</span></div>`;
}
function hovText(p){if(!p.available)return "Ward "+esc(p.label)+" — Insufficient data";
  if(st.layer==="mort")return `<b>${esc(p.label)}</b> · Mortality risk: <b class="c${p.mort}">${p.mort}</b><br>HTSI ${p.htsi}`;
  if(st.layer==="hosp")return `<b>${esc(p.label)}</b> · Hospitalization Spike: <b class="c${p.hosp}">${p.hosp}</b><br>HTSI ${p.htsi}`;
  if(st.layer==="utci")return `<b>${esc(p.label)}</b> · UTCI ${p.utci} °C · air ${p.tair} °C`;
  if(st.layer==="veg")return `<b>${esc(p.label)}</b> · vegetation ${(p.veg*100).toFixed(0)}%`;
  return `<b>${esc(p.label)}</b> · HTSI ${p.htsi} (<span class="c${p.band}">${p.band}</span>)`;}
function wardStyle(p){
  if(!p.available)return {fillColor:"#c7d0db",fillOpacity:0.35,color:"#fff",weight:1,dashArray:"3 3"};
  let f;
  if(st.layer==="utci")f=heat(p.utci||20,22,44);
  else if(st.layer==="veg")f=heat(p.veg?p.veg*100:0,0,40);
  else{const key=st.layer==="mort"?p.mort:st.layer==="hosp"?p.hosp:p.band;f=BCOL[key]||"#999";}
  return {fillColor:f,fillOpacity:0.55,color:"#fff",weight:1};
}
function setLayer(l,silent){st.layer=l;
  document.querySelectorAll("#layers button").forEach(b=>b.classList.toggle("on",b.dataset.l===l));
  if(st.geo){st.geo.forEach(f=>{const p=f.properties,lyr=st.wardLayers[p.id];
    if(lyr&&!silent)lyr.setStyle(wardStyle(p));
  });}
  renderLegend();}
function renderLegend(){const lg=$("#legend");
  if(st.layer==="utci"){lg.innerHTML=`<span style="font-size:11px;font-weight:700">UTCI heat stress (°C)</span>`+gradCells([[22,"22"],[30,"30"],[36,"36"],[44,"44+"]],v=>heat(v,22,44));return;}
  if(st.layer==="veg"){lg.innerHTML=`<span style="font-size:11px;font-weight:700">Satellite vegetation (%)</span>`+gradCells([[0,"0"],[20,"20"],[40,"40+"]]);return;}
  lg.innerHTML=`<span style="font-size:11px;font-weight:700">${LAYERLAB[st.layer]||"Risk"}</span>`+["Low","Moderate","High","Severe"].map(k=>`<span class="cell"><span class="sw" style="background:${BCOL[k]}"></span>${k}</span>`).join("")+
   `<span class="cell"><span class="sw" style="background:#c7d0db;border-style:dashed"></span>Insufficient</span>`;
  const hint=(st.layer==="mort"||st.layer==="hosp")?'<span style="color:#7a4b00;font-size:11px">Mortality &amp; Hospitalization Spike are decision outputs computed from the same weighted H/V/E/AC factors as HTSI via a separate exposure–response logistic.</span>':"";
  if(hint)lg.insertAdjacentHTML("beforeend",hint);}
function gradCells(arr,fn){return `<span style="display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden">`+arr.map(([v,lab])=>`<span style="width:32px;height:16px;background:${fn?fn(v):"#fff"};display:grid;place-items:center;font-size:9px;color:#fff">${lab}</span>`).join("")+`</span>`;}

/* ================= WARD ================= */
function deselectWard(id){
  // resetting the Leaflet path style alone isn't enough -- the black
  // border/highlight is actually driven by the CSS ".selected" class added
  // in selectWard(), and setStyle() never removes CSS classes, so the
  // previous ward kept its highlight until the class itself was stripped.
  const lyr=st.wardLayers[id]; if(!lyr)return;
  lyr.setStyle(wardStyle(lyr.feature.properties));
  const el=lyr.getElement&&lyr.getElement();
  if(el)el.classList.remove("selected","pulse");
}
function selectWard(id){
  if(st._selectedWardId&&st._selectedWardId!==id)deselectWard(st._selectedWardId);
  st._selectedWardId=id;
  const lyr=st.wardLayers[id]; if(!lyr)return;
  lyr.setStyle({weight:3,color:"#0a1e40"});
  lyr.bringToFront();
  // Leaflet's default renderer draws vector layers as real SVG <path>
  // elements, so the same CSS keyframe pulse used before still works --
  // we just grab the live DOM node via getElement() instead of our own array.
  const el=lyr.getElement&&lyr.getElement();
  if(el){el.classList.add("selected","pulse"); setTimeout(()=>el.classList.remove("pulse"),1150);}
}
function backToCityMap(){
  if(st._selectedWardId)deselectWard(st._selectedWardId);
  st._selectedWardId=null;
  if(st.cityFullBounds)st.map.flyToBounds(st.cityFullBounds,{padding:[24,24],duration:0.5});
  $("#side").classList.add("hidden");
  $("#sepCrumb2").classList.add("hidden"); $("#crumbWard").classList.add("hidden");
}
async function openWard(id){
  $("#hov").textContent="Loading ward…";
  const lyr=st.wardLayers[id];
  if(lyr)st.map.flyToBounds(lyr.getBounds(),{padding:[70,70],maxZoom:16,duration:0.6});   // real geographic fitBounds
  selectWard(id);
  const d=await api(`/api/city/${st.city}/ward/${id}`);st.current=d;
  $("#hov").textContent=(d.snapshot&&d.snapshot.available)
    ? `${d.ward.label} · HTSI ${d.snapshot.current.htsi} (${d.snapshot.current.band})`
    : `${d.ward.label} · Insufficient data`;
  $("#sepCrumb2").classList.remove("hidden"); $("#crumbWard").classList.remove("hidden");
  $("#crumbWard").textContent=d.ward.label;
  const sp=$("#sidepanel"),side=$("#side");
  side.classList.remove("hidden");
  sp.classList.remove("drawer-in"); sp.classList.add("drawer-enter");
  sp.innerHTML=wardHTML(d);
  wireTabs(sp);
  void sp.offsetWidth;                          // force reflow so the enter->in transition actually plays
  sp.classList.remove("drawer-enter"); sp.classList.add("drawer-in");
  preventiveSimCard();}
const PS_LABELS={cooling_centres:"Cooling centres",water_audits:"Water audits",outdoor_work_reschedule:"Outdoor-work rescheduling",welfare_checks:"Welfare checks",grid_energy_notice:"Grid / energy notice"};
async function preventiveSimCard(){
  const d=st.current; if(!d||!d.ward||!$("#sidepanel"))return;
  let w; try{w=await api("/api/weights");}catch(e){return;}
  const keys=Object.keys(w.measure_effects||{}); if(!keys.length)return;
  const holder=document.createElement("div"); holder.id="prevSimCard";
  holder.innerHTML=dcard("🧪 Preventive-measures simulator","try it",`${keys.length} measures`,`
    <p style="font-size:12px;color:#23344a">Tick the measures you'd put in place for this ward and press Apply — see how much lower Mortality risk and Hospitalization risk could go.</p>
    ${keys.map(k=>`<label style="display:flex;gap:8px;align-items:center;font-size:13px;margin:4px 0;cursor:pointer"><input type="checkbox" data-m="${k}"> ${PS_LABELS[k]||k}</label>`).join("")}
    <button class="btn" id="psApply" style="margin-top:8px">Apply selected measures</button>
    <div id="psRes" style="margin-top:8px"></div>`,true);
  holder.querySelector("details").style.borderLeft="4px solid #2e9e5b";
  const sp=$("#sidepanel"),an=sp.querySelector("#simAnchor");
  if(an)an.before(holder); else sp.appendChild(holder);
  holder.querySelector("#psApply").onclick=async()=>{
    const res=holder.querySelector("#psRes");
    const sel=[...holder.querySelectorAll("input:checked")].map(i=>i.dataset.m);
    if(!sel.length){res.innerHTML=`<div class="prov">Select at least one measure first.</div>`;return;}
    res.innerHTML=`<div class="prov">Computing scenario…</div>`;
    try{
      const j=await api("/api/scenario/preventive",{method:"POST",body:JSON.stringify({city:st.city,ward_id:String(d.ward.id),measures:sel})});
      if(!j.available){res.innerHTML=`<div class="cav">${esc(j.reason||"Insufficient data.")}</div>`;return;}
      const bar=(p,col)=>`<span class="bar" style="flex:1"><i style="width:${Math.min(100,p*100)}%;background:${col}"></i></span>`;
      const row=(lab,m,h)=>`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>${esc(lab)}</span><b>${Math.round(m*1000)/10}% mort · ${Math.round(h*1000)/10}% spike</b></div>
        <div style="display:flex;gap:4px;align-items:center">${bar(m,"#1467f0")}${bar(h,"#e07bb0")}</div></div>`;
      res.innerHTML=row("Baseline (no measures)",j.baseline.mortality,j.baseline.hospitalisation)
        +(j.waterfall||[]).map(s=>row("+ "+(PS_LABELS[s.measure]||s.measure),s.mort_after,s.hosp_after)).join("")
        +`<div class="prov" style="margin-top:6px">After all selected measures: Mortality <b>${Math.round(j.adjusted.mortality*1000)/10}%</b> (${esc(j.adjusted.mort_band)}) · Hospitalization Spike <b>${Math.round(j.adjusted.hospitalisation*1000)/10}%</b> (${esc(j.adjusted.hosp_band)}).</div>`;
    }catch(e){res.innerHTML=`<div class="cav">Scenario failed: ${esc(e.message)}</div>`;}
  };
}
function factorBars(s){
  if(!s||!s.factors)return "";
  const f=s.factors;
  const row=(lab,v,col,src)=>`<div class="fbarrow"><span class="fblab" title="${esc(src)}">${lab}</span>
    <div class="bar"><i style="width:${Math.round(Math.min(1,Math.max(0,v))*100)}%;background:${col}"></i></div>
    <span class="fbval">${v.toFixed(2)}</span></div>`;
  const body=`<div class="factorbars">
    ${row("Hazard",f.H,"#dd3a3a","ERA5 anomaly + UTCI")}
    ${row("Exposure",f.E,"#f0722c","satellite + MODIS LST")}
    ${row("Vulnerability",f.V,"#7a4b00","Census 2011 · kutcha per ward")}
    ${row("Adaptive Cap.",f.AC,"#2e9e5b","per-ward proxy")}
  </div>
  <div class="fmeta">V index <b>${(f.v_index*100).toFixed(0)}</b> · confidence <b>${Math.round(f.confidence*100)}%</b>${(f.v_pending&&f.v_pending.length)?` · <span style="color:#b7791f">pending: ${esc(f.v_pending.join(", "))}</span>`:""}</div>`;
  return `<details class="dcard" style="margin:8px 0 4px"><summary><h4 style="font-size:12.5px">How this risk was calculated</h4><span class="dcard-teaser">4 factors</span></summary><div class="dcard-body">${body}</div></details>`;
}
function wardHTML(d){const s=d.snapshot,w=d.ward;
  if(!s||!s.available)return `<div class="w-head"><h2>Ward ${esc(w.label)}</h2></div><p>Insufficient data.</p>`;
  const c=s.current,env=s.environment.satellite,meas=s.measures;
  const mk=(m,lab)=>`<div class="risk" style="border-left-color:${BCOL[m.band]}"><b style="min-width:150px">${lab}</b>
    <span class="badge bg${m.band}">${esc(m.band)}</span><span class="hint">~${Math.round(m.probability*100)}% above seasonal baseline</span></div>`;
  const gauge=`<svg class="gauge" viewBox="0 0 120 120"><circle cx="60" cy="60" r="48" fill="none" stroke="#eef2f7" stroke-width="11"/>
    <circle cx="60" cy="60" r="48" fill="none" stroke="${BCOL[c.band]}" stroke-width="11" stroke-linecap="round"
      stroke-dasharray="${(2*Math.PI*48).toFixed(1)}" stroke-dashoffset="${(2*Math.PI*48*(1-Math.min(1,c.htsi/0.5))).toFixed(1)}" transform="rotate(-90 60 60)"/>
    <text x="60" y="54" text-anchor="middle" font-size="13" font-weight="700" fill="${BCOL[c.band]}">${c.htsi}</text>
    <text x="60" y="69" text-anchor="middle" font-size="9.5" fill="#6b7c94">HTSI</text></svg>`;
  const topAction=(meas.admin&&meas.admin[0])||(meas.user&&meas.user[0])||"No escalated action required at this level.";
  return `<div class="w-head"><div><h2>${esc(w.label)} <span style="color:var(--muted);font-weight:400">· ${d.city_name}</span></h2>
    <div class="w-sub">${w.zone?esc("Zone "+w.zone)+" · ":""}${w.area_km2?w.area_km2+" km²":""} · real municipal boundary</div></div>
    <div class="box2">${gauge}<div style="text-align:center"><div class="badge bg${c.band}">${esc(c.band)}</div><div style="font-size:11px;color:var(--muted)">${c.sim_active?"preview":"live"}</div></div></div></div>

  ${factorBars(s)}
  <div class="wact"><div class="wact-lab">Recommended action</div><div class="wact-txt">${esc(topAction)}</div></div>

  <div class="wtabs">
    <button class="wtab on" data-t="ov">Overview</button>
    <button class="wtab" data-t="fc">Forecast</button>
    <button class="wtab" data-t="gd">Guidance</button>
    <button class="wtab" data-t="dt">Data</button>
  </div>

  <div class="wtabpanel" data-p="ov">
    <div class="card" style="border-left:4px solid #dd3a3a"><h4>Output 1 · Mortality Risk</h4>
      ${s.mortality?mk(s.mortality,"Mortality risk"):""}</div>
    <div class="card"><h4>Output 2 · Hospitalization Spike</h4>
      ${s.hospitalisation?mk(s.hospitalisation,"Hospitalization Spike"):""}</div>
    ${mortChart(s)}
    <details class="dcard" style="margin:8px 0"><summary><h4 style="font-size:12.5px">Additional readings</h4><span class="dcard-teaser">temp, population</span></summary>
      <div class="dcard-body"><div class="kpis">
      ${kpi("UTCI",c.utci!=null?c.utci+" °C":"Insufficient")}
      ${kpi("Air temp",c.tair!=null?c.tair+" °C":"—")}
      ${kpi("Day max",c.daymax!=null?c.daymax+" °C":"—")}
      ${kpi("Above city average",c.anom!=null?(c.anom>=0?"+":"")+c.anom+" °C":"—")}
      ${kpi("Population (2011 census)",(d.census2011.population/1e6).toFixed(1)+" M")}
    </div></div></details>
    ${c.sim_active?`<div class="cav">Under the labelled heatwave preview (not live). Reset to see today's real conditions.</div>`:""}
  </div>

  <div class="wtabpanel hidden" data-p="fc">
    ${wardTrendChart(s)}
    ${forecastCard(s.forecast)}
  </div>

  <div class="wtabpanel hidden" data-p="gd">
    <div class="card"><h4>Action measures · ${esc(meas.level)}</h4>
      <div class="meas"><h5>🏛 Administration / city response</h5><ul>${meas.admin.map(m=>`<li>${esc(m)}</li>`).join("")}</ul></div>
      <div class="meas"><h5>📲 Personal guidance (SMS / WhatsApp) — English · Hindi · ${esc((meas.user_i18n||{}).state_lang_name||"state")}</h5>
        <ul>${meas.user.map((m,i)=>{const u=meas.user_i18n||{};return `<li><div>${esc(m)}</div>${u.hi?`<div style="color:#44566e;font-size:11.5px">हिं: ${esc(u.hi[i]||"")}</div><div style="color:#44566e;font-size:11.5px">${esc(u.state_lang_name||"")}: ${esc((u.state||[])[i]||"")}</div>`:""}</li>`;}).join("")}</ul>
        ${meas.emergency?`<div style="margin-top:8px;border:1px solid #d64545;background:#fdecec;border-radius:8px;padding:8px 10px;font-size:11.5px">
          <div style="font-weight:700;color:#a12626;margin-bottom:4px">🚨 Heat-stroke emergency — call ${esc((meas.emergency.numbers||["112","108"]).join(" / "))}</div>
          <div><b>EN:</b> ${esc(meas.emergency.en.signs)} ${esc(meas.emergency.en.call)}</div>
          <div style="color:#44566e;margin-top:2px"><b>हिं:</b> ${esc(meas.emergency.hi.signs)} ${esc(meas.emergency.hi.call)}</div>
          <div style="color:#44566e;margin-top:2px"><b>${esc(meas.emergency.state_lang_name||"")}:</b> ${esc(meas.emergency.state.signs)} ${esc(meas.emergency.state.call)}</div></div>`:""}</div></div>
    <div id="simAnchor"></div>
  </div>

  <div class="wtabpanel hidden" data-p="dt">
    <div class="card"><h4>Ward environment</h4>${satBars(env)}</div>
    <div class="card"><h4>City context</h4><table class="t"><tbody>
      <tr><td>Population (Census 2011)</td><td>${(d.census2011.population).toLocaleString()}</td></tr>
      <tr><td>Cooling access (this ward)</td><td>${Math.round((s.environment.ac_ward||0)*100)}%</td></tr>
      <tr><td>Seasonal threshold (this month)</td><td>${c.baseline_90} °C</td></tr></tbody></table></div>
    <div class="sec">Data layers</div>
    <table class="t"><tbody>
      <tr><td>Weather</td><td>${c.tair} °C</td><td><span class="tag ${s.layers.weather.provenance==="live"?"live":"ref"}">${s.layers.weather.provenance}</span></td></tr>
      <tr><td>Satellite env</td><td>${(env.veg*100)|0}% veg</td><td><span class="tag sat">satellite</span></td></tr>
      <tr><td>Baseline</td><td>${c.baseline_90} °C</td><td><span class="tag ref">climatology</span></td></tr></tbody></table>
  </div>`;
}
function wireTabs(scopeEl){
  scopeEl.querySelectorAll(".wtab").forEach(btn=>btn.onclick=()=>{
    scopeEl.querySelectorAll(".wtab").forEach(b=>b.classList.toggle("on",b===btn));
    const t=btn.dataset.t;
    scopeEl.querySelectorAll(".wtabpanel").forEach(p=>p.classList.toggle("hidden",p.dataset.p!==t));
  });
}
function kpi(k,v){return `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`;}
/* ---- collapsed-by-default section: a one-line summary the person can tap
   to reveal the fuller detail underneath, instead of every card dumping
   all its numbers on screen at once. `open` defaults to false. ---- */
function dcard(title,hint,teaser,bodyHtml,open){
  return `<details class="card dcard"${open?" open":""}><summary><h4>${title}${hint?` <span class="hint">${hint}</span>`:""}</h4>${teaser?`<span class="dcard-teaser">${teaser}</span>`:""}</summary><div class="dcard-body">${bodyHtml}</div></details>`;
}
function mortChart(s){
  const cur=s.mortality,fc=s.forecast||[];
  if(!cur)return "";
  // build series: today + forecast days
  const pts=[{lab:"Today",mort:cur.probability,hosp:(s.hospitalisation? s.hospitalisation.probability:0)}];
  fc.forEach(f=>{pts.push({lab:shortDay(f.day),mort:f.mortality_prob!=null?f.mortality_prob:null,hosp:f.hosp_prob!=null?f.hosp_prob:null});});
  if(pts.filter(p=>p.mort!=null).length<2)return "";
  const W=300,H=150,padL=36,padB=26,padT=14,padR=12;
  const iw=W-padL-padR,ih=H-padT-padB;
  const xp=i=>padL+ (pts.length<=1?iw/2: iw*i/(pts.length-1));
  const all=pts.map(p=>Math.max(p.mort||0,p.hosp||0,0.01));
  const max=Math.max(...all,0.2)*1.1;
  const yp=v=>padT+ih-(v/max)*ih;
  const col=v=>v<0.06?"#2e9e5b":v<0.22?"#e8a51d":v<0.45?"#f0722c":"#dd3a3a";
  const line=(key,stroke)=>{let d="";pts.forEach((p,i)=>{const v=p[key];if(v==null)return;d+=(d?"L":"M")+xp(i).toFixed(1)+" "+yp(v).toFixed(1)+" ";});return d?`<path d="${d}" fill="none" stroke="${stroke}" stroke-width="2.4"/>`+pts.map((p,i)=>{if(p[key]==null)return "";return `<circle cx="${xp(i).toFixed(1)}" cy="${yp(p[key]).toFixed(1)}" r="3.4" fill="${col(p[key])}" stroke="#fff" stroke-width="1"/>`;}).join(""):"";};
  const lbl=p=>p.mort!=null?Math.round(p.mort*100)+"%":"—";
  const rowdots=pts.map((p,i)=>`<text x="${xp(i)}" y="${H-8}" text-anchor="middle" font-size="8.5" fill="#6b7c94">${esc(p.lab)}</text>`).join("");
  const grid=Array.from({length:5},(_,k)=>`<line x1="${padL}" x2="${W-padR}" y1="${yp((max/5)*(k+1))}" y2="${yp((max/5)*(k+1))}" stroke="#eef2f7" stroke-width="1"/>`).join("");
  const mb=cur.probability;
  return `<div class="card"><h4>Mortality &amp; Hospitalization Spike outlook</h4>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:150px" role="img" aria-label="Mortality risk forecast chart">
      ${grid}${line("hosp","#e07bb0")}${line("mort","#1467f0")}
      ${pts.map((p,i)=>p.mort!=null?`<text x="${xp(i)}" y="${yp(p.mort)-6}" text-anchor="middle" font-size="8" font-weight="700" fill="${col(p.mort)}">${lbl(p)}</text>`:"").join("")}
      ${rowdots}
    </svg>
    <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:11px;margin-top:8px">
      <span><span class="sw" style="background:#1467f0"></span> Mortality risk (prob.)</span>
      <span><span class="sw" style="background:#e07bb0"></span> Hospitalization Spike (prob.)</span>
    </div>
    <div style="font-size:11px;margin-top:6px;color:#7a4b00">Today: <b>${Math.round(cur.probability*100)}%</b> · ${esc(cur.band)}</div>
    </div>`;}
function shortDay(day){const m=(day||"").match(/[A-Za-z]{3} \d{1,2} \w{3}/);return m?day.split(" ")[0]:day;}

/* ---- ward-level 5-day heat trend: this section didn't exist before (the
   Forecast tab only had the numeric table below) -- built from the same
   `forecast` array the table uses, so no extra API call. Mirrors the
   city-level 30-day trend visually (coloured band bars) but at ward
   granularity, and stacked with its own full-width row rather than
   squeezed next to a legend. ---- */
function wardTrendChart(s){
  const fc=s.forecast||[];
  if(!fc.length)return `<div class="card"><h4>5-day Heat Trend</h4><div class="prov">Forecast data warming up — check back shortly.</div></div>`;
  const HO={"Low":34,"Moderate":58,"High":82,"Severe":108};
  const bars=fc.map(f=>{
    const b=f.band||"Low", h=HO[b]||40;
    const conf=f.confidence!=null?Math.round(f.confidence*100):null;
    const tip=`${f.day}: ${b} · HTSI ${f.peak_htsi} · UTCI ${f.peak_utci}°C`+(conf!=null?` · ${conf}% confidence`:"");
    return `<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:0" title="${esc(tip)}">
      <span style="font-size:9.5px;color:${BCOL[b]};font-weight:700;margin-bottom:3px">${f.peak_htsi}</span>
      <i style="display:block;width:26px;max-width:60%;height:${h}px;background:${BCOL[b]};border-radius:4px;${conf!=null?`opacity:${Math.max(.45,conf/100)}`:""}"></i>
      <span style="font-size:10px;color:#6b7c94;margin-top:5px;text-align:center">${esc((f.day||"").split(" ").slice(0,2).join(" "))}</span>
      <span class="badge bg${b}" style="font-size:9px;margin-top:3px">${b}</span></div>`;}).join("");
  return `<div class="card"><h4>5-day Heat Trend</h4>
    <div style="display:flex;align-items:flex-end;gap:8px;height:150px;padding:10px 4px 0">${bars}</div>
    <div style="display:flex;gap:14px;align-items:center;margin-top:10px;font-size:10.5px;color:#23344a;flex-wrap:wrap">
      ${["Low","Moderate","High","Severe"].map(b=>`<span style="display:inline-flex;align-items:center;gap:5px"><i style="width:11px;height:11px;border-radius:2px;background:${BCOL[b]};display:inline-block"></i>${b}</span>`).join("")}
    </div></div>`;
}
function forecastCard(fc){if(!fc||!fc.length)return "";
  const worst=fc.reduce((a,f)=>(_WBAND.indexOf(f.band)>_WBAND.indexOf(a.band)?f:a),fc[0]);
  return dcard("5-day forecast","confidence degrades after day 3",`worst: ${worst.day} · ${worst.band}`,`
  <table class="t"><thead><tr><th>Day</th><th>Peak HTSI</th><th>Peak UTCI</th><th>HTSI band</th><th>Mortality</th><th>Hosp. Spike</th><th>Confidence</th></tr></thead><tbody>
  ${fc.map(f=>`<tr><td>${esc(f.day)}</td><td>${f.peak_htsi}</td><td>${f.peak_utci} °C</td>
    <td><span class="badge bg${f.band}" style="font-size:10px">${f.band}</span></td>
    <td>${f.mortality_prob!=null?`<span class="badge bg${f.peak_mortality_band}" style="font-size:10px">${f.peak_mortality_band}</span><span class="hint">${Math.round(f.mortality_prob*100)}%</span>`:"—"}</td>
    <td>${f.hosp_prob!=null?`<span class="badge bg${f.peak_hosp_band}" style="font-size:10px">${f.peak_hosp_band}</span><span class="hint">${Math.round(f.hosp_prob*100)}%</span>`:"—"}</td>
    <td><span class="conf"><span class="bar"><i style="width:${(f.confidence*100)|0}%;background:${f.confidence<0.62?"#f0722c":"#1467f0"}"></i></span></span>${Math.round(f.confidence*100)}%</td></tr>`).join("")}
  </tbody></table>`,true);}
function satBars(env){const b=(lab,v,col)=>`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>${lab}</span><b>${Math.round(v*100)}%</b></div><div class="bar"><i style="width:${Math.min(100,v*100)|0}%;background:${col}"></i></div></div>`;
  let s=b("Vegetation",env.veg,"#2e9e5b")+b("Built-up",env.built,"#c26a3a")+b("Water",env.water,"#3a7fd6");
  if(env.ndvi_modis!=null) s+=b("NDVI",Math.max(0,env.ndvi_modis),"#237a4b");
  if(env.lst_day_c!=null){
    s+=`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>Daytime land surface temp</span><b>${env.lst_day_c} °C</b></div><div class="bar"><i style="width:${Math.min(100,Math.max(4,(env.lst_day_c-20)*6))|0}%;background:#b5462f"></i></div></div>`;}
  if(env.lcz!=null)
    s+=`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>Local Climate Zone</span><b>LCZ ${env.lcz} · ${esc(env.lcz_name||"")}</b></div><div class="bar"><i style="width:${Math.max(4,Math.min(100,Math.round((env.lcz_built_share||0)*100)))}%;background:#8c6bb1"></i></div></div>`;
  return s;}

/* ---------------- sim ---------------- */
async function applySim(){const off=parseInt($("#simRange").value,10)||0;const btn=$("#simApply");btn.disabled=true;
  try{await api("/api/sim",{method:"POST",body:JSON.stringify({offset:off})});updateSimBadge(off);if(st.city)await loadWards(false);}catch(e){$("#hov").textContent="sim error "+e.message;}finally{btn.disabled=false;}}
function updateSimBadge(off){
  const b=$("#simBadge");
  if(off>0){b.textContent="PREVIEW +"+off+"°C";b.className="sim-badge preview";}
  else{b.textContent="LIVE";b.className="sim-badge live";}
}

/* ---- on-demand "Overview" panel (spec point 1/5): the rich India/city
   summary content (national watch cards, city forecast/trend/allocation)
   is unchanged internally -- it now just lives inside the floating side
   panel, shown only when asked for, instead of a permanent column. ---- */
function toggleOverview(){
  const side=$("#side");
  if(!side.classList.contains("hidden")&&!st._selectedWardId){closeSide();return;}
  st._selectedWardId=null;
  side.classList.remove("hidden");
  if(st.view==="city")renderSideCity(); else renderSideIndia();
}
function closeSide(){
  if(st._selectedWardId){backToCityMap();return;}
  $("#side").classList.add("hidden");
}

/* ---------------- modals ---------------- */
function modal(html){$("#mbody").innerHTML=html;$("#modal").classList.remove("hidden");}
async function outboxHTML(){const [r,tw]=await Promise.all([api("/api/outbox"),api("/api/twilio/status").catch(()=>({configured:false}))]);
  const note=`<div style="margin:8px 0;font-size:12.5px;color:#23344a">
    Alerts are sent as SMS/WhatsApp using Twilio. ${tw.configured?"Twilio is configured on this server — sends go out for real.":"Twilio isn't configured on this server, so sends are logged here but not actually delivered."}
    <div class="prov" style="margin-top:6px">Example of a High/Severe ward alert:</div>
    <pre style="white-space:pre-wrap;font-size:11.5px;background:#f4f6f9;border-radius:8px;padding:8px 10px;margin-top:4px">[TAPAS ALERT] Ahmedabad 16 Shahibag - Heat escalation: High hazard. HTSI 0.42, UTCI 41.2C. Preventive actions recommended now. Action: Open ward cooling centres
-- Personal guidance --
EN: Stay indoors 12-4pm | Drink water every hour | Check on elderly neighbours
HI: दोपहर 12-4 बजे घर के अंदर रहें | हर घंटे पानी पिएं | बुज़ुर्ग पड़ोसियों का हाल पूछें
Gujarati: બપોરે 12-4 ઘરની અંદર રહો | દર કલાકે પાણી પીવો | વૃદ્ધ પડોશીઓની ખબર રાખો
-- EMERGENCY (heatstroke) --
Signs: high body temp, confusion, no sweating. Call 112/108 now.</pre></div>`;
  const html=`<h2>Alert outbox — SMS / WhatsApp</h2>`+note+(r.length?`<div class="alert">`+r.map(e=>`<div class="row ${e.type}"><div class="meta">${esc(e.ts)} · <b>${esc(e.type)}</b> · ${esc(e.ward)} · ${esc(e.band)} · ${e.sim_active?"<b>simulator</b>":""}</div><pre>${esc(e.message)}</pre>${e.personal?`<div style="font-size:11.5px;color:#44566e;margin:4px 0">हिं: ${esc((e.personal.hi||[])[0]||"")}<br>${esc(e.personal.state_lang_name||"")}: ${esc((e.personal.state||[])[0]||"")}</div>`:""}${e.emergency?`<div style="font-size:11.5px;color:#a12626;margin:4px 0"><b>🚨 Emergency ${esc((e.emergency.numbers||["112","108"]).join("/"))}:</b> ${esc(e.emergency.en.signs)} ${esc(e.emergency.en.call)}<br>हिं: ${esc(e.emergency.hi.signs)} ${esc(e.emergency.hi.call)}<br>${esc(e.emergency.state_lang_name||"")}: ${esc(e.emergency.state.signs)} ${esc(e.emergency.state.call)}</div>`:""}<div style="font-size:11px;color:#157a35">${esc(e.channel_result)}</div></div>`).join("")+`</div>`:`<p>No alerts yet. Open a city, enable the heatwave preview (+4/6 °C), and event + digest alerts will appear here.</p>`);
  return html;}

boot().catch(e=>console.error(e));
