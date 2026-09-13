"use strict";
/* TAPAS 4-city front-end.
   Views: INDIA (national landing map + city markers) -> CITY (satellite GIS
   ward map) -> WARD detail. Mortality & Hospitalization Spike are separate logistics
   on the same weighted H/V/E/AC factors as HTSI.
   Self-contained; satellite ward polygons drawn over the real Esri basemap. */
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const BCOL={"Low":"#2e9e5b","Moderate":"#e8a51d","High":"#f0722c","Severe":"#dd3a3a","Insufficient":"#c7d0db"};
const LAYERLAB={"htsi":"Hazard risk (HTSI)","mort":"Mortality risk","hosp":"Hospitalization Spike","utci":"UTCI hazard (°C)","veg":"Satellite vegetation (%)"};
const st={cities:[],india:null,view:"india",city:null,layer:"htsi",geo:null,mapmeta:null,sim:null};
async function api(p,opts){const r=await fetch(p,Object.assign({headers:{"Content-Type":"application/json"}},opts));if(!r.ok)throw new Error((await r.text()).slice(0,140));return r.json();}

/* mercator (matches backend) */
function mX(lon,z){return (lon+180)/360*Math.pow(2,z)*256;}
function mY(lat,z){const r=lat*Math.PI/180;return (1-Math.log(Math.tan(r)+1/Math.cos(r))/Math.PI)/2*Math.pow(2,z)*256;}
function ringP(ring,z,tx,ty){let d="";for(let i=0;i<ring.length;i++){const p=ring[i],x=mX(p[0],z)-tx*256,y=mY(p[1],z)-ty*256;d+=(i?"L":"M")+x.toFixed(1)+" "+y.toFixed(1)+" ";}return d+"Z";}
function polyPaths(geom,z,tx,ty){const o=[];if(geom.type==="Polygon"){for(const r of geom.coordinates)o.push(ringP(r,z,tx,ty));}else{for(const poly of geom.coordinates)o.push(ringP(poly[0],z,tx,ty));}return o;}
function heat(v,min,max){const t=Math.max(0,Math.min(1,(v-min)/(max-min||1)));return `rgb(${Math.round(30+215*t)},${Math.round(60+120*(1-t))},${Math.round(210-150*t)})`;}

/* ---------------- boot & header ---------------- */
async function boot(){
  try{
    const [a,b]=await Promise.all([api("/api/india"),api("/api/cities")]);
    st.india=a; st.cities=b.cities; st.sim=b.sim; paintLive(); paintStatus(b);
    $("#outbox").onclick=async()=>modal(await outboxHTML());
    $("#mclose").onclick=()=>$("#modal").classList.add("hidden");
    $("#modal").addEventListener("click",e=>{if(e.target.id==="modal")$("#modal").classList.add("hidden");});
    document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("#modal").classList.contains("hidden"))$("#modal").classList.add("hidden");});
    $("#crumbRoot").onclick=()=>renderIndia();
    $("#simApply").onclick=applySim; $("#simReset").onclick=()=>{$("#simRange").value=0;$("#simVal").textContent="+0 °C";applySim();};
    $("#simRange").oninput=()=>$("#simVal").textContent="+"+$("#simRange").value+" °C";
    document.querySelectorAll("#layers button").forEach(b=>b.onclick=()=>setLayer(b.dataset.l));
    renderIndia();
  }catch(e){$("#sidepanel").innerHTML="<div class='placeholder'>Load error: "+esc(e.message)+"</div>";}
}
function paintLive(){const b=$("#live");const live=st.cities&&st.cities.some(c=>c.weather_prov==="live");b.className="pill"+(live?" liveok":"");b.textContent=live?"● Live · 4 cities":"● meteo fallback";}
function paintStatus(b){
  const cs=b&&b.cities||st.cities||[];
  const live=cs.filter(c=>c.weather_prov==="live").length;
  const mix=cs.filter(c=>c.weather_prov==="mixed").length;
  const fb=cs.length-live-mix;
  const d=$("#sbLive");
  d.className="sb-dot"+(live? (mix||fb? " mixed":" live") : (mix?" mixed":" fallback"));
  const extra=(fb||mix)?` &middot; ${mix} mixed, ${fb} fallback`:"";
  $("#sbText").innerHTML=`National heat-watch &middot; <b>${live}/${cs.length}</b> cities on live weather${extra}. Mortality, Hospitalization Spike &amp; V are <b>early-warning signals on defensible-default (not clinically validated)</b> coefficients.`;
}
function cityInfo(id){return st.cities.find(c=>c.id===id);}

/* ================= INDIA (landing) = proper national GIS ================= */
function renderIndia(){
  st.view="india";
  $("#gis").classList.remove("india");
  $("#base").classList.remove("hidden"); $("#overlay").classList.remove("hidden"); $("#hov").classList.remove("hidden");
  $("#indiaWrap").classList.add("hidden");
  $("#layers").classList.add("hidden"); $("#simbar").classList.add("hidden");
  $("#crumbRoot").classList.add("cur"); $("#crumbRoot").textContent="India";
  $("#sepCrumb").classList.add("hidden"); $("#crumbCity").classList.add("hidden");
  renderNationalMap();
  $("#legend").innerHTML=`<span style="font-size:11px;font-weight:700">National coverage</span>
    <span class="cell"><span class="sw" style="background:#1467f0"></span>Live pilot city — click to open</span>
    <span class="cell"><span class="sw" style="background:#0a1e40;border:0"></span>State boundaries</span>`;
  $("#srcnote").textContent="Real GIS basemap (Esri World Street Map). Click a pilot-city marker (Ahmedabad · Chennai · Hyderabad · Mumbai) to open its satellite ward map.";
  renderSideIndia();
}
function renderNationalMap(){
  const mm=st.india.mapmeta||{zoom:5,tx:22,ty:12,nx:3,ny:4,W:768,H:1024};
  const z=Number(mm.zoom), tx=Number(mm.tx), ty=Number(mm.ty), W=Number(mm.W), H=Number(mm.H);
  $("#base").src="/api/india/basemap";
  $("#gis").style.aspectRatio=W+"/"+H;
  const svg=$("#overlay"); svg.innerHTML="";
  svg.setAttribute("viewBox",`0 0 ${W} ${H}`); svg.setAttribute("preserveAspectRatio","none");
  const g=document.createElementNS("http://www.w3.org/2000/svg","g");
  const px=lon=>mX(lon,z)-tx*256, py=lat=>mY(lat,z)-ty*256;
  const pilotStates=["Maharashtra","Gujarat","Tamil Nadu","Telangana"];
  const ringD=ring=>{let d="",n=0;ring.forEach(pt=>{if(!pt||pt.length<2||!isFinite(pt[0])||!isFinite(pt[1]))return;const x=px(pt[0]),y=py(pt[1]);if(!isFinite(x)||!isFinite(y))return;d+=(n++?"L":"M")+x.toFixed(1)+" "+y.toFixed(1)+" ";});return n>=3?d+"Z":"";};
  (st.india.states.features||[]).forEach(f=>{
    const nm=f.properties.name,geo=f.geometry; if(!geo)return;
    let rings=geo.type==="Polygon"?[geo.coordinates[0]]:geo.coordinates.map(p=>p[0]);
    const d=rings.map(ringD).join(" "); if(!d)return;
    const isPilot=pilotStates.includes(nm);
    const p=document.createElementNS("http://www.w3.org/2000/svg","path");
    p.setAttribute("d",d);
    p.setAttribute("fill",isPilot?"rgba(20,103,240,0.16)":"rgba(255,255,255,0)");
    p.setAttribute("stroke",isPilot?"#1467f0":"rgba(15,40,80,0.35)");
    p.setAttribute("stroke-width",isPilot?"1.6":"0.7");
    p.addEventListener("click",()=>{$("#hov").innerHTML=nm+(isPilot?" — live pilot state (click a city marker).":" — roll-out roadmap state.");});
    g.appendChild(p);
  });
  // markers on top
  (st.india.cities||[]).forEach(c=>{
    const cx=px(c.centre[0]),cy=py(c.centre[1]);
    if(!isFinite(cx)||!isFinite(cy))return;
    const el=document.createElementNS("http://www.w3.org/2000/svg","g");
    const o=document.createElementNS("http://www.w3.org/2000/svg","circle");o.setAttribute("cx",cx);o.setAttribute("cy",cy);o.setAttribute("r",9);o.setAttribute("fill","#fff");o.setAttribute("stroke","#1467f0");o.setAttribute("stroke-width","2.5");o.style.cursor="pointer";
    const i=document.createElementNS("http://www.w3.org/2000/svg","circle");i.setAttribute("cx",cx);i.setAttribute("cy",cy);i.setAttribute("r",4.5);i.setAttribute("fill","#1467f0");
    const t=document.createElementNS("http://www.w3.org/2000/svg","text");t.setAttribute("x",cx+13);t.setAttribute("y",cy+4);t.setAttribute("font-size","13");t.setAttribute("font-weight","700");t.setAttribute("fill","#0a1e40");t.setAttribute("paint-order","stroke");t.setAttribute("stroke","#fff");t.setAttribute("stroke-width","3");t.style.cursor="pointer";t.textContent=c.name;
    [o,i,t].forEach(n=>{n.addEventListener("click",(e)=>{e.stopPropagation();openCity(c.id);});n.addEventListener("mouseenter",()=>{$("#hov").innerHTML=`<b>${c.name}</b> (${c.state}) · ${c.n_wards} wards · <i>click to open</i>`;});});
    el.appendChild(o);el.appendChild(i);el.appendChild(t);g.appendChild(el);
  });
  svg.appendChild(g);
  $("#hov").textContent="India — four live pilot cities on a real GIS basemap. Click a marker.";
}

const _WBAND=["Low","Moderate","High","Severe"];
const _WCOL={"Low":"#2e9e5b","Moderate":"#e8a51d","High":"#f0722c","Severe":"#dd3a3a","Insufficient":"#c7d0db"};
async function renderSideIndia(){
  $("#sidepanel").innerHTML=`<div class="ov-head"><h2>Thermal Assessment &amp; Protection Analytics (TAPAS)</h2>
    <p><b>A weighted multi-factor model</b> combining heat hazard, vulnerability, exposure and adaptive capacity. <b>Mortality Risk</b> and <b>Hospitalization Spike</b> are computed from the same underlying factors via a separate model. Select a pilot city.</p></div>
    <div class="cav" style="margin:6px 0"><b>Calibration caveat:</b> V index magnitude, mortality coefficients and any projection/allocation are <b>defensible defaults, not validated</b> &mdash; India publishes no ward-level outcome data. They are early-warning signals.</div>
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
let _trendCache=null,_trendCacheTs=0;
async function cityTrendCard(city){
  const sp=$("#sidepanel"); if(!sp)return;
  const h=document.createElement("div"); h.id="cityTrendBox";
  const back=[...sp.querySelectorAll("button")].find(x=>x&&x.textContent.includes("Back to India"));
  if(back)back.parentNode.insertBefore(h,back); else sp.appendChild(h);
  const name=cityInfo(city).name;
  h.innerHTML=`<div class="card"><h4>30-day heat trend <span class="hint">${esc(name)} · archive backfill + live scans</span></h4><div class="prov">Loading…</div></div>`;
  try{
    const now=Date.now();
    if(!_trendCache||now-_trendCacheTs>120000){_trendCache=await api("/api/trend?days=30");_trendCacheTs=now;}
    const rows=(_trendCache.series||[]).map(p=>({p,c:(p.cities||[]).find(x=>x.city===city)})).filter(r=>r.c);
    if(!rows.length){h.innerHTML="";return;}
    const HO={"Low":30,"Moderate":52,"High":74,"Severe":96};
    const bars=rows.map((r,i)=>{const p=r.p,b=r.c.worst||"Low",live=p.src==="live-scan";
      const dlabel=(p.ts||"").slice(8,10);
      const tip=(p.ts||"").slice(0,10)+" · "+name+": "+b+(live?" (live full scan)":" (archive backfill)");
      return `<div style="display:flex;flex-direction:column;align-items:center;flex:0 0 auto" title="${esc(tip)}">
        <i style="display:block;width:14px;height:${HO[b]||40}px;background:${_WCOL[b]||"#c7d0db"};border-radius:3px;${live?"":"opacity:.6"}"></i>
        <span style="font-size:9px;color:#6b7c94;margin-top:3px;height:11px">${(i%5===0)?dlabel:""}</span></div>`;}).join("");
    h.innerHTML=`<div class="card"><h4>30-day heat trend <span class="hint">${esc(name)} · archive backfill + live scans</span></h4>
      <div style="display:flex;align-items:flex-end;gap:5px;height:118px;overflow-x:auto;padding:4px 2px">${bars}</div>
      <div style="display:flex;gap:14px;align-items:center;margin-top:6px;font-size:10.5px;color:#23344a;flex-wrap:wrap">
        ${["Low","Moderate","High","Severe"].map(b=>`<span style="display:inline-flex;align-items:center;gap:5px"><i style="width:11px;height:11px;border-radius:2px;background:${_WCOL[b]};display:inline-block"></i>${b}</span>`).join("")}
        <span style="color:#6b7c94">· opaque = live 417-ward scan · faded = archive backfill · date label every 5th day (hover for detail)</span>
      </div>
      <div class="prov">Each bar = that day's worst ward band in <b>${esc(name)}</b> only. Same UNVALIDATED default coefficients throughout.</div></div>`;
  }catch(e){h.innerHTML="";}
}

/* ================= CITY ================= */
async function openCity(id){st.city=id;st.view="city";
  const gis=$("#gis"); gis.classList.remove("india");
  $("#indiaWrap").classList.add("hidden");
  $("#base").classList.remove("hidden"); $("#overlay").classList.remove("hidden");
  $("#layers").classList.remove("hidden"); $("#simbar").classList.remove("hidden");
  $("#crumbRoot").classList.remove("cur"); $("#crumbRoot").textContent="‹ India";
  $("#sepCrumb").classList.remove("hidden"); $("#crumbCity").classList.remove("hidden");
  $("#crumbCity").textContent=cityInfo(id).name;
  await loadWards();}
async function loadWards(){
  const d=await api(`/api/city/${st.city}/wards`);
  st.geo=d.features;st.mapmeta=d.mapmeta;st.sim=d.sim;st.agg=d.aggregate||null;
  const {tx,ty,W,H}=st.mapmeta;
  $("#base").src=`/api/city/${st.city}/basemap`;
  $("#gis").style.aspectRatio=W+"/"+H;
  const svg=$("#overlay");svg.innerHTML="";svg.setAttribute("viewBox",`0 0 ${W} ${H}`);svg.setAttribute("preserveAspectRatio","none");
  const g=document.createElementNS("http://www.w3.org/2000/svg","g");
  st.geo.forEach(f=>{const p=f.properties;polyPaths(f.geometry,st.mapmeta.zoom,st.mapmeta.tx,st.mapmeta.ty).forEach(pth=>{
    const el=document.createElementNS("http://www.w3.org/2000/svg","path");el.setAttribute("d",pth);el.setAttribute("class","ward");
    colorPath(el,p);
    el.addEventListener("click",()=>openWard(p.id));
    el.addEventListener("mouseenter",()=>{$("#hov").innerHTML=hovText(p);});
    g.appendChild(el);});});
  svg.appendChild(g);
  setLayer(st.layer,true);
  renderSideCity();
  allocationCard(cityInfo(st.city).id);
  cityTrendCard(cityInfo(st.city).id);
  $("#hov").textContent=cityInfo(st.city).name+" · municipal wards · switch view above (HTSI / Mortality / Hospitalization Spike / UTCI / Vegetation) · click a ward";
  renderSrcNote();
}
async function allocationCard(city){
  try{
    const a=await api(`/api/city/${city}/allocation`);
    const rows=(a.rows||[]).filter(r=>r.priority_score>0).slice(0,6);
    const bcol=r=>r.mort_band==="Severe"?"#dd3a3a":r.mort_band==="High"?"#f0722c":r.mort_band==="Moderate"?"#e8a51d":"#2e9e5b";
    const h=document.createElement("div");h.id="allocCard";
    h.innerHTML=`<div class="card"><h4>Resource allocation <span class="hint">top at-risk wards</span></h4>
      <table class="t"><thead><tr><th>Ward</th><th>Mort</th><th>Score</th><th>Share</th></tr></thead><tbody>
      ${rows.map(r=>`<tr><td>${esc(r.ward)}</td><td><span class="badge bg${r.mort_band}" style="font-size:10px">${r.mort_band}</span></td>
        <td>${r.priority_score}</td><td>${r.share_pct}%</td></tr>`).join("")||`<tr><td colspan="4">No wards currently elevated (all Low risk).</td></tr>`}
      </tbody></table>
      <div class="prov">Ranked by modelled HTSI &amp; mortality priority score. Deployment illustrative — not an audited plan. ${esc(a.disclosure||"")}</div></div>`;
    const sp=$("#sidepanel");
    // put allocation after city forecast/outlook cards, before back button
    const back=[...sp.querySelectorAll("button")].find(x=>x&&x.textContent.includes("Back to India"));
    if(back)back.parentNode.insertBefore(h,back);
    else sp.appendChild(h);
  }catch(e){}
}

function renderSideCity(){const c=cityInfo(st.city);
  $("#sidepanel").innerHTML=`<div class="ov-head"><h2>${esc(c.name)} — ward map</h2>
    <p>${c.n_wards} municipal wards · Census 2011 population ${(c.census2011.population/1e6).toFixed(1)} M.</p>
    <p style="font-size:12px">Use the view buttons above the map to colour wards by <b>HTSI</b>, <b>Mortality Risk</b>, Hospitalization Spike, UTCI heat-stress, or satellite vegetation. Click a ward for its full profile &amp; the two risk outputs (separate logistics on the shared weighted factors).</p></div>
    ${cityForecastCards(st.agg,c)}
    <div class="ov-grid"><button class="btn ghost" id="openCityWardHint" style="width:100%">Back to India</button></div>`;
  $("#sidepanel").querySelector("#openCityWardHint").onclick=()=>renderIndia();
}
function cityForecastCards(agg,c){
  if(!agg||!agg.distribution)return "";
  const dist=agg.distribution.mortality||{};
  const total=Object.values(dist).reduce((a,b)=>a+b,0)||1;
  const LAY=["Low","Moderate","High","Severe"];
  const barH=90;
  const distSvg=`<svg viewBox="0 0 300 ${barH}" style="width:100%">`+
    LAY.map((k,i)=>{const v=dist[k]||0;const f=v/total;const x=14+i*70+8;return `<rect x="${x}" y="${barH-18-(f*barH-22)}" width="46" height="${Math.max(3,f*barH-22)}" rx="3" fill="${BCOL[k]}" opacity="0.85"><title>${k}: ${v}</title></rect><text x="${x+23}" y="${barH-18-(f*barH-22)-5}" text-anchor="middle" font-size="10" font-weight="700" fill="${BCOL[k]}">${v}</text><text x="${x+23}" y="${barH-5}" text-anchor="middle" font-size="8.5" fill="#6b7c94">${k}</text>`;}).join("")+
    `</svg>`;
  const outlook=(agg.outlook||[]).map(p=>({lab:shortDay(p.day),mort:p.mortality_prob,hosp:p.hosp_prob}));
  const fwd=outlook.length?outlookLine(outlook):"";
  const mcol=p=>p<0.06?"#2e9e5b":p<0.22?"#e8a51d":p<0.45?"#f0722c":"#dd3a3a";
  return `<div class="card"><h4>City Mortality outlook <span class="hint">${esc(c.name)} · from the shared risk engine</span></h4>
    <div class="prov">Wards by Mortality-risk band right now:</div>
    ${distSvg}
    <div style="font-size:11px;color:var(--muted);margin:2px 0 8px">${total} wards · based on live weather + real satellite environment.</div>
    <div class="prov" style="margin-top:6px">5-day forward outlook (city-wide mean risk):</div>
    ${fwd||`<div class="prov">Forecast data warming up — check back shortly.</div>`}
  </div>
  <div class="card" style="border-left:4px solid #1467f0"><h4>Administrative preventive measures <span class="hint">${esc(c.name)} · level ${esc(agg.alert_level)}</span></h4>
    <ul style="margin:6px 0;padding-left:18px">${(agg.admin_measures||[]).map(m=>`<li style="font-size:13px;line-height:1.55;margin:5px 0">${esc(m)}</li>`).join("")}</ul>
    <div class="prov">Two-tier response modelled on the Ahmedabad Heat Action Plan. Administration measures shown here; resident SMS guidance is set per ward (click a ward).</div></div>`;
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
  return `<b>${esc(p.label)}</b> · HTSI ${p.htsi} (<span class="c${p.band}">${p.band}</span>)<br>Mortality ${p.mort} · Hospitalization Spike ${p.hosp}`;}
function colorPath(el,p){
  if(!p.available){el.setAttribute("fill","#c7d0db");el.setAttribute("stroke-dasharray","2 2");return;}
  let f;
  if(st.layer==="utci")f=heat(p.utci||20,22,44);
  else if(st.layer==="veg")f=heat(p.veg?p.veg*100:0,0,40);
  else{const key=st.layer==="mort"?p.mort:st.layer==="hosp"?p.hosp:p.band;f=BCOL[key]||"#999";}
  el.setAttribute("fill",f);
}
function setLayer(l,silent){st.layer=l;
  document.querySelectorAll("#layers button").forEach(b=>b.classList.toggle("on",b.dataset.l===l));
  const lh=$("#layerHint"); if(lh)lh.textContent=LAYERLAB[l]||"";
  if(st.geo&&!silent){const els=$("#overlay").querySelectorAll("path.ward");st.geo.forEach((f,i)=>{if(els[i])colorPath(els[i],f.properties);});}
  renderLegend();}
function renderLegend(){const lg=$("#legend");
  if(st.layer==="utci"){lg.innerHTML=`<span style="font-size:11px;font-weight:700">UTCI heat stress (°C)</span>`+gradCells([[22,"22"],[30,"30"],[36,"36"],[44,"44+"]],v=>heat(v,22,44));return;}
  if(st.layer==="veg"){lg.innerHTML=`<span style="font-size:11px;font-weight:700">Satellite vegetation (%)</span>`+gradCells([[0,"0"],[20,"20"],[40,"40+"]]);return;}
  lg.innerHTML=`<span style="font-size:11px;font-weight:700">${LAYERLAB[st.layer]||"Risk"}</span>`+["Low","Moderate","High","Severe"].map(k=>`<span class="cell"><span class="sw" style="background:${BCOL[k]}"></span>${k}</span>`).join("")+
   `<span class="cell"><span class="sw" style="background:#c7d0db;border-style:dashed"></span>Insufficient</span>`;
  const hint=(st.layer==="mort"||st.layer==="hosp")?'<span style="color:#7a4b00;font-size:11px">Mortality &amp; Hospitalization Spike are decision outputs computed from the same weighted H/V/E/AC factors as HTSI via a separate exposure–response logistic.</span>':"";
  if(hint)lg.insertAdjacentHTML("beforeend",hint);}
function gradCells(arr,fn){return `<span style="display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden">`+arr.map(([v,lab])=>`<span style="width:32px;height:16px;background:${fn?fn(v):"#fff"};display:grid;place-items:center;font-size:9px;color:#fff">${lab}</span>`).join("")+`</span>`;}
function renderSrcNote(){const c=cityInfo(st.city);$("#srcnote").textContent=`Basemap & thermal analysis: Esri World Imagery (real satellite, per-ward). Boundaries: ${c.boundary_source||"municipal wards"}. Mortality & Hospitalization Spike come from a separate model on the same weighted factors as HTSI.`;}

/* ================= WARD ================= */
async function openWard(id){$("#hov").textContent="Loading ward…";
  const d=await api(`/api/city/${st.city}/ward/${id}`);st.current=d;
  $("#sidepanel").innerHTML=wardHTML(d);
  // preventive-impact projection (scenario)
  try{const pr=await api(`/api/city/${st.city}/ward/${id}/projection`);
    if(pr&&pr.available)projectionCard(pr);}catch(e){}
  preventiveSimCard();
  const bb=$("#backIndia"); if(bb)bb.onclick=()=>renderSideCity();}
const PS_LABELS={cooling_centres:"Cooling centres",water_audits:"Water audits",outdoor_work_reschedule:"Outdoor-work rescheduling",welfare_checks:"Welfare checks",grid_energy_notice:"Grid / energy notice"};
async function preventiveSimCard(){
  const d=st.current; if(!d||!d.ward||!$("#sidepanel"))return;
  let w; try{w=await api("/api/weights");}catch(e){return;}
  const keys=Object.keys(w.measure_effects||{}); if(!keys.length)return;
  const holder=document.createElement("div"); holder.id="prevSimCard";
  holder.innerHTML=`<div class="card" style="border-left:4px solid #2e9e5b"><h4>Preventive-measures simulator <span class="hint">interactive scenario</span></h4>
    <p style="font-size:12px;color:#23344a">Tick the measures you would activate for this ward and press Apply — the model shows the step-by-step step-down in Mortality risk and Hospitalization Spike.</p>
    ${keys.map(k=>`<label style="display:flex;gap:8px;align-items:center;font-size:13px;margin:4px 0;cursor:pointer"><input type="checkbox" data-m="${k}"> ${PS_LABELS[k]||k}</label>`).join("")}
    <button class="btn" id="psApply" style="margin-top:8px">Apply selected measures</button>
    <div id="psRes" style="margin-top:8px"></div></div>`;
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
        +`<div class="prov" style="margin-top:6px">After all selected measures: Mortality <b>${Math.round(j.adjusted.mortality*1000)/10}%</b> (${esc(j.adjusted.mort_band)}) · Hospitalization Spike <b>${Math.round(j.adjusted.hospitalisation*1000)/10}%</b> (${esc(j.adjusted.hosp_band)}).</div>
        <div class="cav" style="border-left:4px solid #b7791f;font-size:11px"><b>Illustrative scenario — not measured or validated.</b> ${esc(j.disclosure||"")}</div>`;
    }catch(e){res.innerHTML=`<div class="cav">Scenario failed: ${esc(e.message)}</div>`;}
  };
}
function projectionCard(pr){
  const holder=document.createElement("div");
  holder.id="projCard";
  const rows=pr.scenarios||[];
  const mkbar=(row)=>{const red=Math.max(0,row.mortality_reduction_pct||0);
    return `<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>${esc(row.label)}</span><b>mortality ${red>0?red.toFixed(0)+"% ↓":"no change"}</b></div>
    <div class="bar"><i style="width:${Math.min(100,red)}%;background:${red>=35?"#2e9e5b":red>=15?"#7fb069":"#c7d0db"}"></i></div>
    <div style="font-size:10.5px;color:var(--muted)">${esc(row.desc)} · risk ${Math.round(row.mort_before*1000)/10}% → ${Math.round(row.mort_after*1000)/10}% (${esc(row.mort_band_after)})</div></div>`;};
  holder.innerHTML=`<div class="card" style="border-left:4px solid #2e9e5b"><h4>Preventive-impact projection <span class="hint">scenario</span></h4>
    <p style="font-size:12px;color:#23344a">If adaptive capacity (cooling access / shelters / shade) rises, modelled mortality risk falls. Scenario on the same defensible-default model.</p>
    ${rows.map(mkbar).join("")}
    <div class="cav" style="border-left:4px solid #b7791f;font-size:11px"><b>Scenario, not measured.</b> ${esc(pr.disclosure||"")}</div></div>`;
  const meas=document.querySelector("#sidepanel .card:has(h4)");
  // insert after the Hospitalization Spike output card (first .risk block) -> simplest: append before Action measures if present
  const cards=document.querySelectorAll("#sidepanel .card");
  let anchorEl=null; cards.forEach(c=>{if(c.querySelector("h4")&&c.querySelector("h4").textContent.includes("Action measures"))anchorEl=c;});
  if(anchorEl){anchorEl.parentNode.insertBefore(holder,anchorEl);}
  else{$("#sidepanel").appendChild(holder);}
}

function factorRow(s){
  if(!s||!s.factors)return "";
  const f=s.factors;
  const cell=(k,v,lab,src)=>`<div style="flex:1;min-width:70px;text-align:center;padding:6px;border:1px solid var(--line);border-radius:8px;background:#fbfdff">
    <div style="font-size:9.5px;color:var(--muted);font-weight:700;text-transform:uppercase">${k} · ${lab}</div>
    <div style="font-size:17px;font-weight:800;color:#0a1e40;margin:2px 0">${v}</div>
    <div style="font-size:9px;color:var(--muted)">${src}</div></div>`;
  return `<div class="card"><h4>HTSI model factors</h4>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0">
      ${cell("H","Hazard",f.H.toFixed(3),"ERA5 anomaly + UTCI")}
      ${cell("V","Vulnerability",f.V.toFixed(3),"Census 2011 · kutcha per ward")}
      ${cell("E","Exposure",f.E.toFixed(3),"satellite + MODIS LST")}
      ${cell("AC","Cooling",f.AC.toFixed(3),"per-ward proxy")}
    </div>
    <div style="display:flex;gap:14px;font-size:11px;flex-wrap:wrap;color:#23344a">
      <span>V index <b>${(f.v_index*100).toFixed(0)}</b> (${Object.keys(f.v_parts||{}).map(k=>k+" "+f.v_parts[k]).join(" · ")})${(f.v_pending&&f.v_pending.length)?` · <span style="color:#b7791f">pending: ${esc(f.v_pending.join(", "))}</span>`:""}</span>
      <span>Model confidence <b>${Math.round(f.confidence*100)}%</b></span>
    </div>
    </div>`;}
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
  return `<div class="w-head"><div><h2>${esc(w.label)} <span style="color:var(--muted);font-weight:400">· ${d.city_name}</span></h2>
    <div class="w-sub">${w.zone?esc("Zone "+w.zone)+" · ":""}${w.area_km2?w.area_km2+" km²":""} · real municipal boundary</div></div>
    <div class="box2">${gauge}<div style="text-align:center"><div class="badge bg${c.band}">${esc(c.band)}</div><div style="font-size:11px;color:var(--muted)">${c.sim_active?"simulator":"live"}</div></div></div></div>
  ${factorRow(s)}

  <div class="card" style="border-left:4px solid #dd3a3a"><h4>Output 1 · Mortality Risk</h4>
    ${s.mortality?mk(s.mortality,"Mortality risk"):""}</div>
  <div class="card"><h4>Output 2 · Hospitalization Spike</h4>
    ${s.hospitalisation?mk(s.hospitalisation,"Hospitalization Spike"):""}</div>
  <div id="simAnchor"></div>
  ${mortChart(s)}
  <div class="kpis">
    ${kpi("UTCI","",c.utci!=null?c.utci+" °C":"Insufficient")}
    ${kpi("Air temp",c.tair!=null?c.tair+" °C":"—")}
    ${kpi("Day max",c.daymax!=null?c.daymax+" °C":"—")}
    ${kpi("vs city normal",c.anom!=null?(c.anom>=0?"+":"")+c.anom+" °C":"—")}
    ${kpi("Pop. 2011 (city)",(d.census2011.population/1e6).toFixed(1)+" M")}
  </div>
  ${c.sim_active?`<div class="cav">Under the labelled heatwave simulator (not live). Reset to see today's real conditions.</div>`:""}
  ${forecastCard(s.forecast)}
  <div class="card"><h4>Ward environment <span class="hint">real satellite analysis</span></h4>${satBars(env)}
    <div class="prov">${esc(s.layers.satellite.source)} · <span class="tag sat">satellite</span></div></div>
  <div class="card"><h4>City context</h4><table class="t"><tbody>
    <tr><td>Population (Census 2011)</td><td>${(d.census2011.population).toLocaleString()}</td></tr>
    <tr><td>Cooling access (this ward)</td><td>${Math.round((s.environment.ac_ward||0)*100)}% <span class="hint">per-ward proxy · city base ${Math.round((s.environment.ac_city||0)*100)}% · ${esc((s.environment.ac_info||{}).green_source||"")} · ${esc(d.ac_ref.basis)}</span></td></tr>
    <tr><td>Seasonal threshold (this month)</td><td>${c.baseline_90} °C <span class="tag sat">ERA5</span></td></tr></tbody></table></div>
  <div class="card"><h4>Action measures · ${esc(meas.level)}</h4>
    <div class="meas"><h5>🏛 Administration / city response</h5><ul>${meas.admin.map(m=>`<li>${esc(m)}</li>`).join("")}</ul></div>
    <div class="meas"><h5>📲 Personal guidance (SMS / WhatsApp) — English · Hindi · ${esc((meas.user_i18n||{}).state_lang_name||"state")}</h5>
      <ul>${meas.user.map((m,i)=>{const u=meas.user_i18n||{};return `<li><div>${esc(m)}</div>${u.hi?`<div style="color:#44566e;font-size:11.5px">हिं: ${esc(u.hi[i]||"")}</div><div style="color:#44566e;font-size:11.5px">${esc(u.state_lang_name||"")}: ${esc((u.state||[])[i]||"")}</div>`:""}</li>`;}).join("")}</ul>
      ${meas.emergency?`<div style="margin-top:8px;border:1px solid #d64545;background:#fdecec;border-radius:8px;padding:8px 10px;font-size:11.5px">
        <div style="font-weight:700;color:#a12626;margin-bottom:4px">🚨 Heat-stroke emergency — call ${esc((meas.emergency.numbers||["112","108"]).join(" / "))}</div>
        <div><b>EN:</b> ${esc(meas.emergency.en.signs)} ${esc(meas.emergency.en.call)}</div>
        <div style="color:#44566e;margin-top:2px"><b>हिं:</b> ${esc(meas.emergency.hi.signs)} ${esc(meas.emergency.hi.call)}</div>
        <div style="color:#44566e;margin-top:2px"><b>${esc(meas.emergency.state_lang_name||"")}:</b> ${esc(meas.emergency.state.signs)} ${esc(meas.emergency.state.call)}</div></div>`:""}</div></div>
  <div class="sec">Data layers &amp; last update</div>
  <table class="t"><tbody>
    <tr><td>Weather</td><td>${c.tair} °C</td><td><span class="tag ${s.layers.weather.provenance==="live"?"live":"ref"}">${s.layers.weather.provenance}</span></td><td class="prov">${esc(s.layers.weather.cadence)}</td></tr>
    <tr><td>Satellite env</td><td>${(env.veg*100)|0}% veg</td><td><span class="tag sat">satellite</span></td><td class="prov">${esc(s.layers.satellite.cadence)}</td></tr>
    <tr><td>Baseline</td><td>${c.baseline_90} °C</td><td><span class="tag ref">climatology</span></td><td class="prov">${esc(s.layers.baseline.cadence)}</td></tr></tbody></table>
  <div class="card" style="margin-top:10px"><button class="btn ghost" id="backIndia" style="width:100%">‹ Back to city map</button></div>`;
}
function kpi(k,v){return `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`;}
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
  return `<div class="card"><h4>Mortality &amp; Hospitalization Spike outlook <span class="hint">predictive, from the risk-engine forecast</span></h4>
    <div style="display:flex;gap:10px;align-items:center">
    <svg viewBox="0 0 ${W} ${H}" style="flex:1;min-width:0" role="img" aria-label="Mortality risk forecast chart">
      ${grid}${line("hosp","#e07bb0")}${line("mort","#1467f0")}
      ${pts.map((p,i)=>p.mort!=null?`<text x="${xp(i)}" y="${yp(p.mort)-6}" text-anchor="middle" font-size="8" font-weight="700" fill="${col(p.mort)}">${lbl(p)}</text>`:"").join("")}
      ${rowdots}
    </svg>
    <div style="font-size:11px;line-height:1.7">
      <div><span class="sw" style="background:#1467f0"></span> Mortality risk (prob.)</div>
      <div><span class="sw" style="background:#e07bb0"></span> Hospitalization Spike (prob.)</div>
      <div style="margin-top:6px;color:#7a4b00">Today: <b>${Math.round(cur.probability*100)}%</b> · ${esc(cur.band)}</div>
      <div style="color:var(--muted);margin-top:4px">Modelled forecast — not clinical; confidence falls after day 3.</div>
    </div></div></div>`;}
function shortDay(day){const m=(day||"").match(/[A-Za-z]{3} \d{1,2} \w{3}/);return m?day.split(" ")[0]:day;}

function forecastCard(fc){if(!fc||!fc.length)return "";return `<div class="card"><h4>5-day heat forecast <span class="hint">confidence degrades after day 3</span></h4>
  <table class="t"><thead><tr><th>Day</th><th>Peak HTSI</th><th>Peak UTCI</th><th>HTSI band</th><th>Mortality</th><th>Hosp. Spike</th><th>Confidence</th></tr></thead><tbody>
  ${fc.map(f=>`<tr><td>${esc(f.day)}</td><td>${f.peak_htsi}</td><td>${f.peak_utci} °C</td>
    <td><span class="badge bg${f.band}" style="font-size:10px">${f.band}</span></td>
    <td>${f.mortality_prob!=null?`<span class="badge bg${f.peak_mortality_band}" style="font-size:10px">${f.peak_mortality_band}</span><span class="hint">${Math.round(f.mortality_prob*100)}%</span>`:"—"}</td>
    <td>${f.hosp_prob!=null?`<span class="badge bg${f.peak_hosp_band}" style="font-size:10px">${f.peak_hosp_band}</span><span class="hint">${Math.round(f.hosp_prob*100)}%</span>`:"—"}</td>
    <td><span class="conf"><span class="bar"><i style="width:${(f.confidence*100)|0}%;background:${f.confidence<0.62?"#f0722c":"#1467f0"}"></i></span></span>${Math.round(f.confidence*100)}%</td></tr>`).join("")}
  </tbody></table>
  <div class="prov">OpenWeatherMap hourly · horizon 120 h. Mortality / Hospitalization Spike are risk bands for that day's peak heat, from the same exposure–response model as today (not clinical forecasts).</div></div>`;}
function satBars(env){const b=(lab,v,col)=>`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>${lab}</span><b>${Math.round(v*100)}%</b></div><div class="bar"><i style="width:${Math.min(100,v*100)|0}%;background:${col}"></i></div></div>`;
  let s=b("Vegetation / cooling",env.veg,"#2e9e5b")+b("Built / impervious (heat gain)",env.built,"#c26a3a")+b("Water",env.water,"#3a7fd6");
  // REAL MODIS per-ward values (NASA GIBS), dated and labelled
  if(env.ndvi_modis!=null) s+=b("NDVI — MODIS Terra 8-day (real)",Math.max(0,env.ndvi_modis),"#237a4b");
  if(env.lst_day_c!=null){
    const anom=env.lst_anom!=null?(env.lst_anom>=0?"+":"")+env.lst_anom+" °C vs city median":"";
    s+=`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>Daytime LST — MODIS Terra (real)</span><b>${env.lst_day_c} °C</b></div><div class="bar"><i style="width:${Math.min(100,Math.max(4,(env.lst_day_c-20)*6))|0}%;background:#b5462f"></i></div><div class="hint">${anom}</div></div>`;}
  if(env.lcz!=null)
    s+=`<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>Local Climate Zone — WUDAPT (real)</span><b>LCZ ${env.lcz} · ${esc(env.lcz_name||"")}</b></div><div class="bar"><i style="width:${Math.max(4,Math.min(100,Math.round((env.lcz_built_share||0)*100)))}%;background:#8c6bb1"></i></div><div class="hint">built-class share ${(Math.round((env.lcz_built_share||0)*100))}% · Demuzere et al. 2022 global LCZ map, 100 m</div></div>`;
  return s;}

/* ---------------- sim ---------------- */
async function applySim(){const off=parseInt($("#simRange").value,10)||0;const btn=$("#simApply");btn.disabled=true;
  try{await api("/api/sim",{method:"POST",body:JSON.stringify({offset:off})});if(st.city)await loadWards();}catch(e){$("#hov").textContent="sim error "+e.message;}finally{btn.disabled=false;}}

/* ---------------- modals ---------------- */
function modal(html){$("#mbody").innerHTML=html;$("#modal").classList.remove("hidden");}
async function outboxHTML(){const [r,tw]=await Promise.all([api("/api/outbox"),api("/api/twilio/status").catch(()=>({configured:false}))]);
  const btn=`<div style="margin:8px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button class="btn" id="twTest">📨 Send test alert (real Twilio if configured)</button>
    <span class="hint">${tw.configured?"<b style='color:#157a35'>Twilio configured — sends are REAL</b>":"<b style='color:#b7791f'>Twilio not configured — sends are honestly SIMULATED</b> · set TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM/TO"}</span></div>
    <div id="twRes" class="prov"></div>`;
  const html=`<h2>Alert outbox — SMS / WhatsApp</h2>`+btn+(r.length?`<div class="alert">`+r.map(e=>`<div class="row ${e.type}"><div class="meta">${esc(e.ts)} · <b>${esc(e.type)}</b> · ${esc(e.ward)} · ${esc(e.band)} · ${e.sim_active?"<b>simulator</b>":""}</div><pre>${esc(e.message)}</pre>${e.personal?`<div style="font-size:11.5px;color:#44566e;margin:4px 0">हिं: ${esc((e.personal.hi||[])[0]||"")}<br>${esc(e.personal.state_lang_name||"")}: ${esc((e.personal.state||[])[0]||"")}</div>`:""}${e.emergency?`<div style="font-size:11.5px;color:#a12626;margin:4px 0"><b>🚨 Emergency ${esc((e.emergency.numbers||["112","108"]).join("/"))}:</b> ${esc(e.emergency.en.signs)} ${esc(e.emergency.en.call)}<br>हिं: ${esc(e.emergency.hi.signs)} ${esc(e.emergency.hi.call)}<br>${esc(e.emergency.state_lang_name||"")}: ${esc(e.emergency.state.signs)} ${esc(e.emergency.state.call)}</div>`:""}<div style="font-size:11px;color:#157a35">${esc(e.channel_result)}</div></div>`).join("")+`</div>`:`<p>No alerts yet. Open a city, enable the heatwave preview (+4/6 °C), and event + digest alerts will appear here.</p>`);
  setTimeout(()=>{const b=document.getElementById("twTest"); if(b)b.onclick=async()=>{b.disabled=true;try{const r2=await api("/api/alerts/test",{method:"POST"});document.getElementById("twRes").textContent="Result: "+r2.channel_result;}catch(e){document.getElementById("twRes").textContent="Error: "+e.message;}finally{b.disabled=false;}};},0);
  return html;}

boot().catch(e=>console.error(e));
