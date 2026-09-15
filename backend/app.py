"""TAPAS -- Thermal Assessment & Protection Analytics System (ward-level heat-health EWS, 4 Indian cities).

Risk is anomaly-driven (per Ahmedabad Heat-Action-Plan logic): a ward escalates
when today's heat exceeds ITS OWN city's observed ERA5 seasonal norm, not from raw
afternoon temperature (which is routinely high in humid Chennai). Real inputs:
  live OpenWeatherMap weather (per ~3km grid), real satellite ward thermal
  environment, and an observed ERA5 reanalysis (2014-2024) seasonal baseline. A clearly-labelled, default-off
  simulator raises temperature to preview escalation for demonstrations; it is
  never presented as live. Missing data => available:false, never a substituted
  average.
"""
import datetime as dt, json, os, math, time, threading, csv, io
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
class _NoCache(BaseHTTPMiddleware):
    async def dispatch(self, req: Request, call_next):
        resp=await call_next(req)
        if req.url.path in ("/","/index.html") or req.url.path.startswith("/static/"):
            resp.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"]="no-cache"
        return resp
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pythermalcomfort.models import utci

from datastore import STORE, CITY_IDS
from weather import fetch_many, fetch_many_paced, synth_weather
from measures import admin_actions, user_sms
from measures_i18n import personal_multilang, personal_oneline, emergency_multilang
import pg_store


ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Representative locality anchor per MCGM administrative ward (public geography;
# a letter ward spans several neighbourhoods - shown as its main area anchor).
_MUMBAI_AREAS={
 "A":"Colaba / Fort","B":"Mandvi / Dongri","C":"Marine Lines","D":"Mahalaxmi / Opera House",
 "E":"Byculla / Mazgaon","F/N":"Parel","F/S":"Lalbaug / Matunga","G/N":"Mahim / Dharavi",
 "G/S":"Lower Parel / Mahalaxmi","H/E":"Bandra E / Santacruz","H/W":"Bandra W / Khar",
 "K/E":"Andheri E / Jogeshwari","K/W":"Andheri W / Versova","L":"Kurla",
 "M/E":"Chembur / Govandi","M/W":"Chembur / Mankhurd","N":"Ghatkopar / Vikhroli",
 "P/N":"Malad / Marve","P/S":"Goregaon / Aarey","R/C":"Borivali","R/N":"Borivali / Dahisar",
 "R/S":"Kandivali","S":"Bhandup","T":"Mulund"}
def ward_label(city,w):
    lab=(w.get("label") or "").strip()
    if city!="Mumbai" or not lab: return lab or ("Ward "+str(w["id"]))
    code=lab.replace("Ward ","").strip()
    area=_MUMBAI_AREAS.get(code)
    return f"{lab} · {area}" if area else lab

app=FastAPI(title="TAPAS - Thermal Assessment & Protection Analytics System")
app.add_middleware(_NoCache)
REFRESH_S=int(os.environ.get("HW_LIVE_REFRESH_S",6*3600))
DIGEST_S=int(os.environ.get("HW_DIGEST_S",6*3600))
SIM={"offset":0.0,"label":""}  # labelled simulator, default off

def clamp(x,a=0.0,b=1.0): return max(a,min(b,x))

# ------------------------------------------------------------ geometry util
def walk(c,a):
    if isinstance(c[0],(int,float)): a.append((float(c[0]),float(c[1]))); return
    for x in c: walk(x,a)
def geo_area_km2(geom):
    pts=[]; walk(geom["coordinates"],pts)
    if not pts: return None
    lat0=sum(p[1] for p in pts)/len(pts)
    kx=111.32*math.cos(math.radians(lat0)); ky=110.57
    n=len(pts); a=0.0
    for i in range(n):
        x1,y1=pts[i]; x2,y2=pts[(i+1)%n]
        a+=(x1*kx)*(y2*ky)-(x2*kx)*(y1*ky)
    return abs(a)/2.0

def _utci(tair,rh,wind,sw,hour):
    sw=sw if sw is not None else 0.0
    solar=clamp(sw/700.0)
    tr=tair+(8.0*solar if 6<=hour<=18 else 2.0)
    v=clamp(wind if wind is not None else 2.0,0.5,17.0)
    try:
        res=utci(tdb=tair,tr=tr,v=v,rh=rh if rh is not None else 50.0)
        u=float(res.utci) if hasattr(res,"utci") else float(res)
    except Exception: return None
    return u if u==u else None

_BAND_COLS={"Low":"#2f9e44","Moderate":"#f59f00","High":"#f76707","Severe":"#e03131"}
def band(p):
    """HTSI band from the configurable cut-points BAND_T (see /api/weights)."""
    if p is None: return "Insufficient","#b9c4d0"
    for n,h in zip(("Low","Moderate","High"),BAND_T):
        if p<h: return n,_BAND_COLS[n]
    return "Severe",_BAND_COLS["Severe"]

RB=[("Low",0.06,"#2f9e44"),("Moderate",0.22,"#f59f00"),
    ("High",0.45,"#f76707"),("Severe",9.0,"#e03131")]
RISK_BAND_T=[0.06,0.22,0.45]   # mortality/hosp probability cut-points Low|Moderate|High|Severe
RISK_BAND_T_DEFAULTS=list(RISK_BAND_T)
_RBCOL={"Low":"#2f9e44","Moderate":"#f59f00","High":"#f76707","Severe":"#e03131"}
def rband(p):
    b=("Low" if p<RISK_BAND_T[0] else "Moderate" if p<RISK_BAND_T[1]
       else "High" if p<RISK_BAND_T[2] else "Severe")
    return b,_RBCOL[b]

# ------------------------------------------------------------ satellite env
_MED_CACHE={}
def city_sat_medians(city):
    """City medians of per-ward satellite attributes (real values, computed once)."""
    if city in _MED_CACHE: return _MED_CACHE[city]
    import statistics
    built=[];veg=[];lst=[];ndvi=[];health=[];elec=[];tap=[]
    for w in STORE.cities[city]["wards"]:
        s=w.get("sat")
        if not s: continue
        if s.get("built_frac") is not None: built.append(s["built_frac"])
        if s.get("veg_frac") is not None: veg.append(s["veg_frac"])
        if s.get("lst_day_c") is not None: lst.append(s["lst_day_c"])
        if s.get("ndvi_modis") is not None: ndvi.append(s["ndvi_modis"])
        if s.get("health_per_km2") is not None: health.append(s["health_per_km2"])
        if s.get("elec_pct") is not None: elec.append(s["elec_pct"])
        if s.get("tap_pct") is not None: tap.append(s["tap_pct"])
    med={"built":statistics.median(built) if built else 0.5,
         "veg":statistics.median(veg) if veg else 0.1,
         "lst":statistics.median(lst) if lst else None,
         "ndvi":statistics.median(ndvi) if ndvi else None,
         "health":statistics.median(health) if health else None,
         "elec":statistics.median(elec) if elec else None,
         "tap":statistics.median(tap) if tap else None}
    _MED_CACHE[city]=med; return med

def env_terms(sat, city=None):
    if not sat or sat.get("veg_frac") is None: return None
    veg=sat["veg_frac"]; built=sat["built_frac"]; wat=sat["wat_frac"]
    E=clamp(0.55*built+0.45*max(0.0,1.0-veg-0.6*wat))
    cool=clamp(veg*1.4+wat*2.0)
    out={"E":round(E,3),"veg":round(veg,3),"built":round(built,3),
         "water":round(wat,3),"cool":round(cool,3)}
    # ---- REAL MODIS Terra enhancements (per-ward, dated; NASA GIBS) ----
    ndvi=sat.get("ndvi_modis"); lst=sat.get("lst_day_c")
    if ndvi is not None:
        out["ndvi_modis"]=round(ndvi,3)
        out["cool"]=round(clamp(0.5*cool+0.5*clamp(ndvi*1.4+wat*2.0)),3)
    if lst is not None and city:
        out["lst_day_c"]=round(lst,1)
        med=city_sat_medians(city).get("lst")
        if med is not None:
            # real daytime LST anomaly vs city median adjusts exposure
            out["lst_anom"]=round(lst-med,1)
            out["E"]=round(clamp(0.75*E+0.25*clamp((lst-med)/6.0+0.5)),3)
    # ---- REAL WUDAPT LCZ (Demuzere et al. 2022 global map) per-ward ----
    lcz=sat.get("lcz")
    if lcz is not None:
        out["lcz"]=int(lcz); out["lcz_name"]=sat.get("lcz_name")
        bs=sat.get("lcz_built_share")
        if bs is not None:
            out["lcz_built_share"]=round(bs,3)
            out["E"]=round(clamp(0.85*out["E"]+0.15*bs),3)
    return out

# ------------------------------------------------------------ cooling access
# Per-ward cooling-access PROXY: city AC baseline (documented) modulated by the
# ward's real satellite attributes -- dense built-up / sparse-green wards proxy
# informal-settlement patterns (less AC); greener wards proxy more AC.
# The modulation coefficients are MODELLED DEFAULTS (unvalidated), disclosed in
# the UI, factor panel and Methodology. Not survey data.
AC_MOD={"built":0.18,"green":0.16,"elec":0.16,"health":0.10,"water":0.08}
def ac_for_ward(city, env, sat=None):
    """Per-ward cooling-access composite (spec factor list):
    city AC baseline x modifiers from REAL per-ward inputs -
      electricity access (Census HL-14, ward-level), green cover (MODIS NDVI,
      real), healthcare proximity (OSM hospitals/km2, real), water access
      (HL-14 treated-tap % + surface-water frac), built-up density (satellite).
    Modifier coefficients are MODELLED DEFAULTS (unvalidated) - disclosed."""
    base=STORE.cities[city]["config"]["ac_ref"]["value"]
    sat=sat or {}
    if not env: return base,{"base":base,"modifier":1.0,"inputs":"(no satellite attrs)"}
    med=city_sat_medians(city)
    use_ndvi=env.get("ndvi_modis") is not None and med.get("ndvi") is not None
    green=env.get("ndvi_modis") if use_ndvi else env.get("veg")
    gmed=med["ndvi"] if use_ndvi else med["veg"]
    dbn=clamp((env["built"]-med["built"])/0.30,-1,1)
    dgn=clamp((green-gmed)/0.15,-1,1) if (green is not None and gmed is not None) else 0.0
    elec=sat.get("elec_pct"); emed=med.get("elec")
    eln=clamp((elec-emed)/4.0,-1,1) if (elec is not None and emed is not None) else 0.0
    hl=sat.get("health_per_km2"); hmed=med.get("health")
    hln=clamp((hl-hmed)/2.0,-1,1) if (hl is not None and hmed is not None) else 0.0
    tap=sat.get("tap_pct"); tmed=med.get("tap")
    wsrc = tap if tap is not None else env.get("water")*100
    wmed = tmed if tmed is not None else (med.get("tap") or 90)
    wn=clamp((wsrc-wmed)/12.0,-1,1) if wsrc is not None else 0.0
    mod=1.0 - AC_MOD["built"]*dbn + AC_MOD["green"]*dgn + AC_MOD["elec"]*eln \
        + AC_MOD["health"]*hln + AC_MOD["water"]*wn
    val=clamp(base*mod,0.05,0.90)
    return val,{"base":base,"modifier":round(mod,3),
                "built_dev":round(env["built"]-med["built"],3),
                "green_dev":round((green-gmed) if green is not None else 0.0,3),
                "green_source":"MODIS NDVI (real)" if use_ndvi else "true-colour veg frac",
                "elec_pct":elec,"health_per_km2":hl,"tap_pct":tap,
                "inputs":"electricity HL-14 + NDVI MODIS + hospitals OSM + tapwater HL-14 + built satellite"}


# ------------------------------------------------------------ configurable model weights
# HTSI = (WH*H) x (VV*V) x (WE*E) x (1 - WAC*AC). Defaults weight every factor
# equally at 1.0. V is a city-based Census vulnerability index with a REAL
# per-ward kutcha refinement (see Methodology); sub-weights W_V allocate its
# seven indicators.
WEIGHTS={"H":1.0,"V":1.0,"E":1.0,"AC":1.0,"scale":1.0}
W_V={"children":0.22,"literacy":0.13,"slum":0.18,"elderly":0.13,"disability":0.09,"density":0.13,"kutcha":0.12}
V_SCALE={"lo":0.92,"hi":0.30}                       # V = lo + hi*v_index (mapped [lo, lo+hi])

# ---- configurable exposure-response coefficients & band cut-points ----------
# Defensible, UNVALIDATED defaults (see Methodology 4); editable at runtime via
# POST /api/weights and persisted to data/weights.json.
# k_m / k_h: adaptive-capacity (AC) coefficients - AC enters both logistics with a
# NEGATIVE sign (higher cooling access -> lower risk). Defensible default,
# UNVALIDATED, pending real outcome data - same status as the other coefficients.
K_M=1.8   # k_m: mortality logistic AC coefficient
K_H=1.4   # k_h: hospitalisation logistic AC coefficient
RISK_COEF={"mort":{"intercept":-4.3,"anom":3.0,"surge":1.6,"H":1.5,"E":1.4,"V":4.0,"AC":K_M},
           "hosp":{"intercept":-4.0,"anom":3.2,"surge":1.8,"H":1.2,"E":1.3,"V":3.2,"AC":K_H}}
RISK_COEF_DEFAULTS=json.loads(json.dumps(RISK_COEF))
BAND_T=[0.055,0.115,0.185]     # HTSI cut-points Low|Moderate|High|Severe
BAND_T_DEFAULTS=list(BAND_T)
FLAGS={"sym_anom":False}       # True => anomaly term may also lower risk (a in [-1,1])
_WFILE=os.path.join(ROOT,"data","weights.json")

# ---- preventive-measure effect sizes (preventive impact simulator) ----------
# Illustrative UNVALIDATED defaults, pending real outcome data - same status as
# every other coefficient in this file. Each Section-5 measure maps to the risk
# term it is assumed to move: AC (adaptive capacity), s (UTCI surge) or vt
# (vulnerability uplift). Applied cumulatively in this canonical order; AC is
# re-clamped to [0.05,0.90] and s floored at 0 after every step.
MEASURE_EFFECTS={
 "cooling_centres":        {"term":"AC","op":"add","value":0.10,
    "rationale":"cooling-centre access raises effective cooling coverage for those ward residents"},
 "water_audits":           {"term":"AC","op":"add","value":0.03,
    "rationale":"water-point/hydration audits modestly raise adaptive capacity"},
 "outdoor_work_reschedule":{"term":"s","op":"add","value":-0.15,
    "rationale":"shifting outdoor work out of peak heat cuts the UTCI surge actually absorbed"},
 "welfare_checks":         {"term":"vt","op":"mul","value":0.85,
    "rationale":"welfare checks on elderly/vulnerable dampen the vulnerability uplift"},
 "grid_energy_notice":     {"term":"AC","op":"add","value":0.05,
    "rationale":"grid/energy notices sustain cooling use through peak hours"},
}
MEASURE_EFFECTS_DEFAULTS=json.loads(json.dumps(MEASURE_EFFECTS))
SCENARIO_DISCLOSURE=("Illustrative scenario — effect sizes are unvalidated defaults, "
                     "consistent with Section 6 transparency disclosures. "
                     "Not a measured or validated outcome.")
def _load_weights_file():
    try:
        d=json.load(open(_WFILE))
        WEIGHTS.update({k:float(v) for k,v in (d.get("weights") or {}).items() if k in WEIGHTS})
        W_V.update({k:float(v) for k,v in (d.get("v_weights") or {}).items() if k in W_V})
        rc=d.get("risk_coef") or {}
        for out,k in ((RISK_COEF["mort"],"mort"),(RISK_COEF["hosp"],"hosp")):
            out.update({kk:float(vv) for kk,vv in (rc.get(k) or {}).items() if kk in out})
        bt=d.get("band_t"); 
        if isinstance(bt,list) and len(bt)==3 and 0<bt[0]<bt[1]<bt[2]<1: BAND_T[:]=[float(x) for x in bt]
        rbt=d.get("risk_band_t")
        if isinstance(rbt,list) and len(rbt)==3 and 0<rbt[0]<rbt[1]<rbt[2]<1: RISK_BAND_T[:]=[float(x) for x in rbt]
        if isinstance(d.get("flags"),dict): FLAGS["sym_anom"]=bool(d["flags"].get("sym_anom",False))
        me=d.get("measure_effects") or {}
        for k,v in me.items():
            if k in MEASURE_EFFECTS and isinstance(v,dict):
                try: MEASURE_EFFECTS[k]["value"]=float(v["value"])
                except Exception: pass
    except Exception:
        pass
def _save_weights_file():
    try:
        json.dump({"weights":WEIGHTS,"v_weights":W_V,"risk_coef":RISK_COEF,
                   "band_t":BAND_T,"risk_band_t":RISK_BAND_T,"flags":FLAGS,
                   "measure_effects":{k:{kk:vv for kk,vv in v.items()} for k,v in MEASURE_EFFECTS.items()}},
                  open(_WFILE,"w"))
    except Exception:
        pass
_load_weights_file()
# Honest disclosure for V: inputs are real Census-2011-derived city figures for
# the indicators available; the index's magnitude mapping and mortality-uplift
# coefficient are defensible DEFAULTS, not formally calibrated.
_CAL_NOTE=("Vulnerability (V): 7 Census-derived indicators - children 0-6, literacy, slum share, "
           "elderly 60+, disability, density, kutcha housing. Fetched from official tables: children, literacy, "
           "disability (age-standardised, Sagar et al. 2016 PMC4973875), density (Census population / municipal "
           "area), elderly 60+ from C-14 City tables (Mumbai .086, Ahmedabad .080, Chennai .098). Hyderabad has no "
           "official C-14 City row (verified absent in the national C-14 City file and the AP/Telangana state "
           "files), so its elderly share (.068) comes from the fully-urban Hyderabad district row (2011 "
           "jurisdiction) of C-14 Andhra Pradesh - labelled as such. Kutcha housing REAL per ward from Census "
           "HL-14 (unmatched wards fall back to the city total row, disclosed); slum share pending where not "
           "published. Weights renormalise over available indicators. City base + per-ward kutcha refinement. "
           "Index magnitude and mortality-uplift coefficient are UNVALIDATED defaults.")
_V_CACHE={}
_NAT={"ts":"","total":None,"cities":None}  # cached national watch (updated by scheduler)

def _vuln_city(city):
    """City-level vulnerability base from real Census-2011 city figures
    (children, literacy, slum, elderly C-14 City, disability, density + city kutcha).
    Kutcha is refined per ward in vulnerability() via real HL-14 ward values."""
    if city in _V_CACHE: return _V_CACHE[city]
    cfg=STORE.cities[city]["config"]; v=cfg.get("vuln") or {}
    if not v:
        _V_CACHE[city]={"v":0.5,"parts":{},"source":"(none)","disclosure":_CAL_NOTE}; return _V_CACHE[city]
    parts={}
    if v.get("children_0_6") is not None: parts["children"]=clamp(v["children_0_6"]/0.13)
    if v.get("literacy") is not None: parts["literacy"]=clamp((1.0-v["literacy"])/0.20)
    if v.get("slum") is not None: parts["slum"]=clamp(v["slum"]/0.60)
    if v.get("elderly_60plus") is not None: parts["elderly"]=clamp(v["elderly_60plus"]/0.15)
    if v.get("disability") is not None: parts["disability"]=clamp(v["disability"]/0.03)
    dens=v.get("density")
    if dens and dens.get("value") is not None: parts["density"]=clamp(dens["value"]/25000.0)
    if v.get("kutcha") is not None: parts["kutcha"]=clamp(v["kutcha"]/0.20)
    pending=[nm for nm,val_ in (("elderly 60+ (C-14 City)",v.get("elderly_60plus")),
                                ("slum share",v.get("slum"))) if val_ is None]
    if not parts:
        _V_CACHE[city]={"v":0.5,"parts":parts,"source":v.get("source",""),"disclosure":_CAL_NOTE}; return _V_CACHE[city]
    # renormalise available weights
    sub=sum(W_V.get(k,0) for k in parts) or 1.0
    vi=sum((W_V.get(k,0)/sub)*parts[k] for k in parts)
    lo=V_SCALE["lo"]; hi=V_SCALE["hi"]
    val=max(0.8,min(lo+hi+0.1, lo+hi*vi))
    _V_CACHE[city]={"v":round(val,4),"index":round(vi,3),"parts":{k:round(x,3) for k,x in parts.items()},
                    "source":v.get("source",""),"lit":v.get("literacy"),"child":v.get("children_0_6"),"slum":v.get("slum"),
                    "elderly":v.get("elderly_60plus"),"disability":v.get("disability"),
                    "density":(v.get("density") or {}).get("value"),"pending":pending,
                    "disclosure":_CAL_NOTE}
    return _V_CACHE[city]

def vulnerability(city, ward=None):
    """V with per-ward refinement: kutcha housing is REAL per-ward Census HL-14
    where the ward matched; unmatched wards fall back to the city total row."""
    base=_vuln_city(city)
    if ward is None: return base
    s=ward.get("sat") or {}
    k=s.get("kutcha_pct")
    if k is None: return base
    parts=dict(base["parts"]); parts["kutcha"]=clamp((k/100.0)/0.20)
    sub=sum(W_V.get(kk,0) for kk in parts) or 1.0
    vi=sum((W_V.get(kk,0)/sub)*parts[kk] for kk in parts)
    lo=V_SCALE["lo"]; hi=V_SCALE["hi"]
    val=max(0.8,min(lo+hi+0.1, lo+hi*vi))
    out=dict(base); out.update({"v":round(val,4),"index":round(vi,3),
        "parts":{kk:round(x,3) for kk,x in parts.items()},"ward_kutcha_pct":k})
    return out

def model_confidence(prov,env_conf=0.70):
    """Simple transparent confidence propagation through the factor product.
    H(weather) + E(satellite) + V(census) + AC(cooling) factor confidences,
    blended by the same relative weights the risk uses."""
    hc=0.85 if prov=="live" else (0.60 if prov=="mixed" else 0.50)
    vc=0.35; acc=0.50  # V is unvalidated default -> lowest confidence
    return round(0.35*hc+0.25*env_conf+0.25*vc+0.15*acc,3)

# ------------------------------------------------------------ weather
def unique_grid(city):
    pts={}
    for w in STORE.cities[city]["wards"]:
        c=w["centroid"]; key=(round(c[0]/0.028)*0.028,round(c[1]/0.028)*0.028)
        pts.setdefault(key,[]).append(w)
    return pts

class Live:
    def __init__(self):
        self.records={}; self.grid={}; self.last=None
    def refresh(self,force=False):
        now=dt.datetime.now(dt.timezone.utc)
        if not force and self.last and (now-self.last).total_seconds()<REFRESH_S: return
        import time as _t
        for city in CITY_IDS:
            g=unique_grid(city); self.grid[city]=g
            locs={k:(g[k][0]["centroid"][1],g[k][0]["centroid"][0]) for k in g}
            r=fetch_many_paced(locs,max_workers=5,per_minute_limit=55)
            # retry failures once
            fails=[k for k,res in r.items() if res[0]!="ok"]
            if fails: print(f"[weather] {city}: {len(fails)}/{len(locs)} failed — sample: {r[fails[0]][1]}", flush=True)
            if fails:
                fl={k:locs[k] for k in fails}
                _t.sleep(0.5)
                r2=fetch_many(fl,max_workers=6)
                for k,res in r2.items():
                    if res[0]=="ok": r[k]=res
            recs={}
            for k,res in r.items():
                if res[0]=="ok":
                    res[1]["_prov"]="live"; recs[k]={"rec":res[1],"prov":"live"}
                else:
                    w=g[k][0]; s=synth_weather(w["centroid"][1],w["centroid"][0],now)
                    s["_prov"]="fallback"; recs[k]={"rec":s,"prov":"fallback"}
            self.records[city]=recs
        self.last=now
    def prov(self,city):
        recs=self.records.get(city)
        if not recs: return "fallback"
        live=sum(1 for v in recs.values() if v["prov"]=="live")
        # live if the clear majority of grid points succeeded (one transient miss is normal)
        return "live" if live/max(1,len(recs))>=0.9 else ("fallback" if live==0 else "mixed")
    def ward(self,city,ward):
        g=self.grid.get(city,{})
        if not g: return None
        c=ward["centroid"]; key=(round(c[0]/0.028)*0.028,round(c[1]/0.028)*0.028)
        e=self.records.get(city,{}).get(key)
        return e["rec"] if e else None
LIVE=Live()

def _series(rec):
    """hourly [(t_dt, tair, rh, wind, sw)] applying sim offset to temps."""
    H=rec.get("hourly",{})
    times=H.get("time",[]); ta=H.get("temperature_2m",[]); rh=H.get("relative_humidity_2m",[])
    ws=H.get("wind_speed_10m",[]); sw=H.get("shortwave_radiation",[])
    n=min(len(times),len(ta)); out=[]
    for i in range(n):
        try: t=dt.datetime.fromisoformat(times[i])
        except Exception: continue
        temp=float(ta[i])+SIM["offset"] if ta[i] is not None else None
        if temp is None: continue
        out.append({"t":t,"tair":temp,
                    "rh":float(rh[i]) if i<len(rh) and rh[i] is not None else None,
                    "wind":float(ws[i]) if i<len(ws) and ws[i] is not None else None,
                    "sw":float(sw[i]) if i<len(sw) and sw[i] is not None else 0.0})
    return out

# ------------------------------------------------------------ risk core
def severity(city, rec, series, now):
    """Return (htsi_sev 0..1 anomaly-ish, utci_current, tair_current,
                anom_deg, baseline_deg, info)."""
    base=STORE.cities[city]["baseline"]["p90_monthly_tmax"].get(str(now.month))
    # current obs = nearest hour
    cur=None; best=None
    for o in series:
        diff=abs((o["t"].replace(tzinfo=None)-now).total_seconds())
        if best is None or diff<best: best=diff; cur=o
    if cur is None: return None
    utci_v=_utci(cur["tair"],cur["rh"],cur["wind"],cur["sw"],cur["t"].hour)
    tair=cur["tair"]
    # daily max today (with sim offset)
    day=str(now.date()); daymax=max((o["tair"] for o in series if o["t"].date()==now.date()),default=tair)
    anom=(daymax-base) if base is not None else None
    anom_driver=_a_of(anom)
    # absolute surge term (only extreme UTCI counts; avoids year-round humid flags)
    surge=clamp((utci_v-36.0)/10.0) if utci_v else 0.0
    hsev=clamp(0.62*anom_driver+0.38*surge)
    return {"hsev":hsev,"utci":utci_v,"tair":tair,"anom":anom,
            "base":base,"daymax":daymax}

def _a_of(anom):
    """Anomaly driver: defaults floor cool days at 0; FLAGS['sym_anom'] allows
    a symmetric [-1,1] driver so below-normal heat can lower risk."""
    if anom is None: return 0.0
    return clamp(anom/3.0,-1,1) if FLAGS.get("sym_anom") else clamp(anom/3.0)

def _risk_terms(a, surge, Hw, Ew, Vw, ACw):
    """Per-term additive contributions to each logistic's z (transparency).
    Inputs are the SAME WEIGHTED factors HTSI uses - Hw=wH*H, Ew=wE*E, Vw=wV*V,
    ACw=wAC*AC - so editing WEIGHTS via /api/weights moves HTSI and both risk
    outputs in the same call. AC enters with a NEGATIVE sign."""
    cm=RISK_COEF["mort"]; ch=RISK_COEF["hosp"]
    mt={"intercept":cm["intercept"],"anom":round(cm["anom"]*a,3),
        "surge":round(cm["surge"]*surge,3),"H":round(cm["H"]*Hw,3),
        "E":round(cm["E"]*Ew,3),"V":round(cm["V"]*(Vw-1.0),3),"AC":round(-cm["AC"]*ACw,3)}
    ht={"intercept":ch["intercept"],"anom":round(ch["anom"]*a,3),
        "surge":round(ch["surge"]*surge,3),"H":round(ch["H"]*Hw,3),
        "E":round(ch["E"]*Ew,3),"V":round(ch["V"]*(Vw-1.0),3),"AC":round(-ch["AC"]*ACw,3)}
    mt["intercept"]=round(mt["intercept"],3); ht["intercept"]=round(ht["intercept"],3)
    zm=sum(mt.values()); zh=sum(ht.values())
    return 1/(1+math.exp(-zm)), 1/(1+math.exp(-zh)), mt, ht, round(zm,3), round(zh,3)

def _risk_probs(a, surge, Hw, Ew, Vw, ACw):
    """Transparent exposure-response logistic for mortality & hospitalisation.
    Standard sigmoid on a configurable coefficient set (defensible UNVALIDATED
    defaults, editable via /api/weights) evaluated on the same WEIGHTED factors
    as HTSI; AC lowers risk with a negative coefficient."""
    m,h,_,_,_,_=_risk_terms(a,surge,Hw,Ew,Vw,ACw)
    return m,h
  
_SNAP_CACHE={}
def cached_snapshot(city, ward, rec, now):
    """Wraps compute_snapshot() with a small cache so repeated requests
    don't re-run the per-ward forecast's pythermalcomfort UTCI loop from
    scratch every time. Cache key changes whenever: the weather record
    refreshes, the simulator offset changes, or a new 15-minute window
    begins."""
    bucket=now.replace(minute=(now.minute//15)*15, second=0, microsecond=0)
    key=(city, ward["id"], rec.get("generationtime_utc"), SIM["offset"], bucket.isoformat())
    hit=_SNAP_CACHE.get(key)
    if hit is not None: return hit
    snap=compute_snapshot(city, ward, rec, now)
    _SNAP_CACHE.clear() if len(_SNAP_CACHE)>5000 else None
    _SNAP_CACHE[key]=snap
    return snap
  
def compute_snapshot(city, ward, rec, now):
    env=env_terms(ward.get("sat"), city)
    series=_series(rec)
    ac, ac_info=ac_for_ward(city, env, ward.get("sat"))
    res=severity(city,rec,series,now) if env else None
    if not env or not res:
        return {"ward":{"id":ward["id"],"label":ward.get("label"),"city":city},
                "available":False,"reason":"Insufficient data (environment or weather)"}
    # ---- HTSI = H x V x E x (1 - AC), configurable weights ----
    prov=rec.get("_prov","live")
    V=vulnerability(city, ward); VV=V["v"]
    H=res["hsev"]; E=env["E"]
    wH=WEIGHTS["H"]; wV=WEIGHTS["V"]; wE=WEIGHTS["E"]; wAC=WEIGHTS["AC"]
    htsi=clamp((wH*H)*(wV*VV)*(wE*E)*(1.0-wAC*ac))*WEIGHTS["scale"]
    curband,_=band(htsi)
    conf=model_confidence(prov)
    anom=res["anom"]
    # mortality/hospitalisation risk: respond to hazard anomaly + exposure
    a=_a_of(anom)
    surge=clamp((res["utci"]-36.0)/10.0) if res["utci"] else 0.0
    mort,hosp,mt,ht,zm,zh=_risk_terms(a,surge,WEIGHTS["H"]*H,WEIGHTS["E"]*E,WEIGHTS["V"]*VV,WEIGHTS["AC"]*ac)
    mb=rband(mort)[0]; hb=rband(hosp)[0]
    fc=forecast(city,ward,rec,now,env,ac)
    src=rec.get("generationtime_utc") or now.isoformat()
    ac_ref=STORE.cities[city]["config"]["ac_ref"]
    factors={"H":round(H,4),"V":round(VV,4),"E":round(E,4),"AC":round(ac,3),
             "weights":{k:WEIGHTS[k] for k in WEIGHTS},
             "v_index":V["index"],"v_parts":V["parts"],"v_source":V["source"],
             "v_pending":V.get("pending",[]),
             "v_disclosure":V.get("disclosure",""),
             "ac_basis":ac_ref.get("basis",""),
             "ac_type":"per-ward proxy = city baseline x built/NDVI modifiers (modelled, unvalidated)",
             "ac_detail":ac_info,
             "confidence":conf,
             "risk_terms":{"mort":mt,"hosp":ht,"z_mort":zm,"z_hosp":zh},
             "formula":"HTSI = (wH.H) x (wV.V) x (wE.E) x (1 - wAC.AC)"}
    return {
      "ward":{"id":ward["id"],"label":ward.get("label"),"city":city},
      "available":True,
      "current":{"tair":round(res["tair"],1),"utci":round(res["utci"],1) if res["utci"] else None,
                 "htsi":round(htsi,4),"band":curband,"confidence":conf,
                 "anom":round(anom,1) if anom is not None else None,
                 "baseline_90":round(res["base"],1) if res["base"] else None,
                 "daymax":round(res["daymax"],1),
                 "time":now.isoformat(),
                 "weather_prov":prov,
                 "sim_active":bool(SIM["offset"])},
      "environment":{"satellite":env,"ac_city":STORE.cities[city]["config"]["ac_ref"]["value"],
                     "ac_ward":round(ac,3),"ac_info":ac_info,
                     "modis":(STORE.cities[city].get("modis") or {}).get("summary"),
                     "modis_provenance":(STORE.cities[city].get("modis") or {}).get("provenance")},
      "factors":factors,
      "mortality":{"probability":round(mort,3),"band":mb},
      "hospitalisation":{"probability":round(hosp,3),"band":hb},
      "forecast":fc,
      "measures":{"admin":admin_actions(curband),"user":user_sms(curband),
                 "user_i18n":personal_multilang(curband,city),"level":curband,
                 "emergency":emergency_multilang(city) if curband in ("High","Severe") else None},
      "layers":{
        "weather":{"value":round(res["tair"],1),
                   "source":"OpenWeatherMap (live)" if prov=="live" else "OpenWeatherMap (offline fallback, tagged)",
                   "source_date":src,"provenance":prov,
                   "cadence":"Refreshed up to every 6h (within source capability)."},
        "satellite":{"value":env,
                   "source":"Esri World Imagery satellite -- per-ward true-colour thermal-environment analysis",
                   "source_date":"static basemap","provenance":"satellite",
                   "cadence":"Static per ward until basemap refresh; NOT a fake 6h product."},
        "baseline":{"value":res["base"],
                   "source":"ECMWF ERA5 reanalysis (2014-2024 observed), monthly 90th-pct Tmax",
                   "source_date":"observed 2014-2024","provenance":"observed-climatology",
                   "cadence":"Threshold calibration only; never a live value."},
      },
    }

def forecast(city,ward,rec,now,env,ac):
    if not env: return []
    series=_series(rec); days={}; vfor=vulnerability(city)
    for o in series:
        t=o["t"]
        if (t.replace(tzinfo=None)-now).total_seconds()< -3600 or (t.replace(tzinfo=None)-now).total_seconds()>120*3600: continue
        u=_utci(o["tair"],o["rh"],o["wind"],o["sw"],t.hour)
        if u is None: continue
        base=STORE.cities[city]["baseline"]["p90_monthly_tmax"].get(str(t.month))
        surge=clamp((u-36.0)/10.0)
        anom_d=clamp((o["tair"]-base)/3.0) if base else 0.0
        hsev=clamp(0.62*anom_d+0.38*surge)
        htsi=(WEIGHTS["H"]*hsev)*(WEIGHTS["V"]*vfor["v"])*(WEIGHTS["E"]*env["E"])*(1.0-WEIGHTS["AC"]*ac)*WEIGHTS["scale"]
        ha=(t.replace(tzinfo=None)-now).total_seconds()/3600.0
        conf=1.0 if ha<=24 else (0.85 if ha<=72 else (0.62 if ha<=96 else 0.45))
        days.setdefault(t.date().isoformat(),[]).append({"h":htsi,"u":u,"tair":o["tair"],
                                                         "surge":surge,"base":base,"conf":conf})
    out=[]
    for d,arr in sorted(days.items()):
        dd=dt.date.fromisoformat(d)
        baseD=STORE.cities[city]["baseline"]["p90_monthly_tmax"].get(str(dd.month))
        daymax=max(x["tair"] for x in arr)
        anom=(daymax-baseD) if baseD is not None else None
        peak=max(arr,key=lambda x:x["h"])
        a=_a_of(anom)
        srg=clamp((peak["u"]-36.0)/10.0) if peak["u"] else 0.0
        hsev_day=clamp(0.62*a+0.38*srg)
        mort,hosp=_risk_probs(a,srg,WEIGHTS["H"]*hsev_day,WEIGHTS["E"]*env["E"],WEIGHTS["V"]*vfor["v"],WEIGHTS["AC"]*ac)
        out.append({"date":d,"day":dd.strftime("%a %d %b"),"peak_htsi":round(peak["h"],3),
                    "peak_utci":round(peak["u"],1),"band":band(peak["h"])[0],
                    "peak_mortality_band":rband(mort)[0],"mortality_prob":round(mort,3),
                    "peak_hosp_band":rband(hosp)[0],"hosp_prob":round(hosp,3),
                    "confidence":round(sum(x["conf"] for x in arr)/len(arr),2)})
    return out

# ------------------------------------------------------------ scheduler + alert
outbox=[]
if os.path.exists(os.path.join(ROOT,"data","outbox.jsonl")):
    try: outbox=[json.loads(l) for l in open(os.path.join(ROOT,"data","outbox.jsonl")) if l.strip()][-80:]
    except Exception: outbox=[]
def _twilio_send(msg, to):
    """Real Twilio SMS send when credentials are present in the environment.
    Returns an honest channel_result string either way (never fakes a send)."""
    import requests
    sid=os.environ.get("TWILIO_ACCOUNT_SID"); tok=os.environ.get("TWILIO_AUTH_TOKEN")
    frm=os.environ.get("TWILIO_FROM"); to=to or os.environ.get("TWILIO_TO")
    if not (sid and tok and frm and to):
        return "SIMULATED (set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM/TWILIO_TO env vars to send real)"
    try:
        r=requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                        auth=(sid,tok), data={"To":to,"From":frm,"Body":msg[:1500]}, timeout=15)
        if r.status_code in (200,201):
            return "SENT via Twilio (sid "+str(r.json().get("sid",""))[:24]+")"
        return "TWILIO REJECTED "+str(r.status_code)+": "+r.text[:140]
    except Exception as ex:
        return "TWILIO ERROR "+str(ex)[:140]

def build_sms(msg,band,city):
    """Assemble the user SMS body in THREE languages - English, Hindi and the
    recipient city's state language - and, at High/Severe, append the trilingual
    heat-stroke emergency block (signs + national numbers 112/108).
    Returns (body, personal, emergency)."""
    pers=None; emg=None
    if city and band in _BAND_NAMES:
        pers=personal_multilang(band, city)
        if band in ("High","Severe"): emg=emergency_multilang(city)
    def _body(n):
        b=msg
        if pers:
            b+="\n-- Personal guidance --\nEN: "+" | ".join(pers["en"][:n])
            b+="\nHI: "+" | ".join(pers["hi"][:n])
            b+="\n"+pers["state_lang_name"]+": "+" | ".join(pers["state"][:n])
        if emg:  # emergency block is never trimmed
            b+="\n-- EMERGENCY (heatstroke) --\n"+emg["en"]["signs"]+" "+emg["en"]["call"]
            b+="\nHI: "+emg["hi"]["signs"]+" "+emg["hi"]["call"]
            b+="\n"+emg["state_lang_name"]+": "+emg["state"]["signs"]+" "+emg["state"]["call"]
        return b
    n=len(pers["en"]) if pers else 0
    body=_body(n)
    while len(body)>1450 and n>1:   # keep under the Twilio send cap; emergency always fits
        n-=1; body=_body(n)
    return body,pers,emg

def log_alert(type_,ward,band,msg,extra=None):
    """Every user alert SMS goes out trilingual (EN+HI+state); at High/Severe the
    heat-stroke emergency block (signs + 112/108) is appended in all three."""
    city=next((c for c in CITY_IDS if str(ward).startswith(c)), None)
    body,pers,emg=build_sms(msg,band,city)
    e={"ts":dt.datetime.now(dt.timezone.utc).isoformat(),"type":type_,"ward":ward,"band":band,
       "message":body,"channel":"sms_whatsapp","sim_active":bool(SIM["offset"])}
    e["channel_result"]=_twilio_send(body, os.environ.get("TWILIO_TO",""))
    if pers: e["personal"]=pers
    if emg: e["emergency"]=emg
    if extra: e.update(extra)
    outbox.append(e)
    with open(os.path.join(ROOT,"data","outbox.jsonl"),"a") as f: f.write(json.dumps(e)+"\n")
    return e
def build_digest_message(active, suppressed=0):
    m="[TAPAS CITY DIGEST] "+dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")+"\n"
    m+="Wards at High/Severe: "+str(len(active))+"\n"
    for s in active:
        m+=f"- {s['ward']['city']} {s['ward']['label']}: {s['current']['band']} (HTSI {s['current']['htsi']})\n"
    if suppressed:
        m+=f"(+{suppressed} at-risk ward(s) omitted - already covered by individual event alerts within the digest window)\n"
    return m

_ORDER_BAND={"Low":0,"Moderate":1,"High":2,"Severe":3}
_BAND_NAMES=["Low","Moderate","High","Severe"]
HIST=os.path.join(ROOT,"data","history.jsonl")
_last_hist=[None]
def log_history(now,tally,gtot):
    """Append an hourly national snapshot to data/history.jsonl (real full-scan
    history; merges with the archive backfill in /api/trend)."""
    if _last_hist[0] is not None and (now-_last_hist[0]).total_seconds()<3300: return
    _last_hist[0]=now
    e={"ts":now.isoformat(),"src":"live-scan",
       "total":{"available":gtot["available"],"bands":gtot["bands"]},
       "cities":[{"city":t["city"],"bands":t["bands"],
                  "worst":_BAND_NAMES[min(3,t["worst_htsi"])]} for t in tally]}
    try:
        with open(HIST,"a") as f: f.write(json.dumps(e)+"\n")
    except Exception as ex: print("[hist]",ex)
# Dedup windows (seconds): re-alert the same ward only after the cooldown and on
# an escalation; +3h for High, +1h escalation repeat suppressed until cooldown.
EVENT_COOLDOWN={"High":6*3600,"Severe":3*3600}

_BAND_LIST=["Low","Moderate","High","Severe"]

def compute_national(now):
    """Single pass over every ward in every pilot city.
    Returns (at_risk_snapshots, per-city tally, grand total).
    Reuses the exact same compute_snapshot the live model runs, so the
    national watch always mirrors what each city map would show."""
    now=now.replace(tzinfo=None)
    atrisk=[]
    tally=[]
    gtot={"available":0,"insufficient":0,"wards":0,"bands":{b:0 for b in _BAND_LIST}}
    for city in CITY_IDS:
        cfg=STORE.cities[city]["config"]
        t={"city":city,"name":cfg["name"],"state":cfg["state"],"n_wards":0,
           "available":0,"insufficient":0,
           "bands":{b:0 for b in _BAND_LIST},
           "worst_htsi":_ORDER_BAND["Low"],"worst_mort":_ORDER_BAND["Low"]}
        for w in STORE.cities[city]["wards"]:
            t["n_wards"]+=1; gtot["wards"]+=1
            rec=LIVE.ward(city,w)
            if not rec:
                t["insufficient"]+=1; t["bands"]["Insufficient"]=t["bands"].get("Insufficient",0)+1; continue
            snap=compute_snapshot(city,w,rec,now)
            if not snap.get("available"):
                t["insufficient"]+=1; t["bands"]["Insufficient"]=t["bands"].get("Insufficient",0)+1; continue
            t["available"]+=1; gtot["available"]+=1
            cb=snap["current"]["band"]; mb=snap["mortality"]["band"]
            if cb in t["bands"]: t["bands"][cb]+=1
            t["worst_htsi"]=max(t["worst_htsi"],_ORDER_BAND.get(cb,0))
            t["worst_mort"]=max(t["worst_mort"],_ORDER_BAND.get(mb,0))
            if cb in ("High","Severe"): atrisk.append(snap)
        for b in t["bands"]: gtot["bands"][b]=gtot["bands"].get(b,0)+t["bands"][b]
        gtot["insufficient"]+=t["insufficient"]
        tally.append(t)
    return atrisk,tally,gtot

class Sched(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.last_digest=None
        self.last_event={}   # ward key -> {"band":..,"ts":..}
        self._last_computed_live=None   # tracks LIVE.last we last computed against
        self._cached_atrisk=[]
    def at_risk(self):
        """Recompute the full 417-ward risk model ONLY when the underlying live
        weather actually refreshed (every REFRESH_S, default 6h). Between
        refreshes, reuse the cached result instead of re-running compute_national
        (which re-runs UTCI/forecast for every ward) on every 20s scheduler tick."""
        global _NAT
        now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if _NAT.get("cities") and self._last_computed_live==LIVE.last:
            return self._cached_atrisk
        out,tally,gtot=compute_national(now)
        _NAT={"ts":now.isoformat(),"total":gtot,"cities":tally}
        log_history(now,tally,gtot)
        self._last_computed_live=LIVE.last
        self._cached_atrisk=out
        return out
    def check_events(self, now):
        """Fire immediate, deduped threshold-crossing alerts when a ward escalates
        into High/Severe (event-driven mode, in addition to the periodic digest)."""
        now_n=now.replace(tzinfo=None)
        for s in self.at_risk():
            w=s["ward"]; band=s["current"]["band"]
            key=f"{w['city']}/{w['id']}"
            prev=self.last_event.get(key)
            # Only alert if this is an escalation (prev lower or None) and cooldown passed
            if prev and _ORDER_BAND.get(prev["band"],0)>=_ORDER_BAND[band]                and (now_n-prev["ts"]).total_seconds()<EVENT_COOLDOWN.get(band,3600):
                continue
            msgs={"High":"Heat escalation: High hazard. ",
                  "Severe":"Heat escalation: SEVERE hazard. "}
            msg=f"[TAPAS ALERT] {w['city']} {w['label']} - {msgs[band]}HTSI {s['current']['htsi']}, UTCI {s['current'].get('utci')}C. Preventive actions recommended now."
            # attach admin measures line; log_alert appends the full trilingual
            # personal guidance (+ emergency block at High/Severe)
            adm=s.get("measures",{}).get("admin",[])
            if adm: msg+=" Action: "+adm[0]
            log_alert("event",f"{w['city']} {w['label']}",band,msg,
                      extra={"ward_city":w["city"],"ward_id":w["id"]})
            self.last_event[key]={"band":band,"ts":now_n}
    def digest_candidates(self, act, now_n):
        """SPEC cross-suppression: the 6h digest must NOT re-list wards that
        already received an individual event alert within the digest window."""
        kept=[]; suppressed=0
        for s in act:
            w=s["ward"]; key=f"{w['city']}/{w['id']}"
            prev=self.last_event.get(key)
            if prev and (now_n-prev["ts"]).total_seconds()<DIGEST_S:
                suppressed+=1; continue
            kept.append(s)
        return kept, suppressed
    def run(self):
        LIVE.refresh(force=True)
        # warm the national-watch cache so the landing overview is instant
        try:
            self.at_risk()
        except Exception as e:
            print("[sched] warm watch", e)
        while True:
            try: LIVE.refresh()
            except Exception as e: print("[sched] refresh",e)
            now=dt.datetime.now(dt.timezone.utc)
            try: self.check_events(now)
            except Exception as e: print("[sched] event",e)
            if self.last_digest is None or (now-self.last_digest).total_seconds()>=DIGEST_S:
                try: act=self.at_risk()
                except Exception: act=[]
                now_n=now.replace(tzinfo=None)
                kept, suppressed = self.digest_candidates(act, now_n)
                if kept:
                    log_alert("digest","ALL-CITY","ACTIVE",build_digest_message(kept,suppressed))
                    self.last_digest=now
                elif act:
                    log_alert("digest","ALL-CITY","SUPPRESSED",
                              f"[TAPAS DIGEST] all {len(act)} at-risk ward(s) already covered by individual "
                              f"event alerts within {DIGEST_S//3600}h - digest cross-suppressed (spec rule).")
                    self.last_digest=now
            time.sleep(20)

# ================================================================ API
@app.get("/") 
def index(): return FileResponse(os.path.join(ROOT,"static","index.html"))

@app.get("/api/india")
def india():
    """Real national GIS basemap + state outlines + pilot city points."""
    nmap=os.path.join(ROOT,"data","processed","india_basemap","map.json")
    try:
        mm=json.load(open(nmap))
    except Exception:
        mm={}
    try:
        states=json.load(open(os.path.join(ROOT,"data","processed","india_simp.geojson")))
    except Exception:
        states={"type":"FeatureCollection","features":[]}
    pts=[]
    for city in CITY_IDS:
        cfg=STORE.cities[city]["config"]
        pts.append({"id":city,"name":cfg["name"],"state":cfg["state"],
                    "centre":cfg["centre"],"n_wards":len(STORE.cities[city]["wards"])})
    return {"states":states,"cities":pts,"mapmeta":mm}

@app.get("/api/india/basemap")
def india_basemap():
    return FileResponse(os.path.join(ROOT,"data","processed","india_basemap","india.jpg"))

@app.get("/api/india/watch")
def india_watch():
    """Live national heat-watch from the scheduler's last full ward scan.
    Returns quickly (cached); while the first weather fetch is still running it
    reports warming:true so the UI can poll instead of caching a partial tally."""
    ready = _NAT.get("cities") and _NAT.get("total") and _NAT.get("total",{}).get("available",0) >= max(1, (_NAT.get("total",{}).get("wards",1)//2))
    if not ready:
        return {"warming": True, "ts": _NAT.get("ts", ""),
                "total": _NAT.get("total"), "cities": _NAT.get("cities") or [],
                "prov": {c: LIVE.prov(c) for c in CITY_IDS}, "disclosure": _CAL_NOTE}
    return {"warming": False, "ts": _NAT["ts"], "total": _NAT["total"],
            "cities": _NAT["cities"],
            "prov": {c: LIVE.prov(c) for c in CITY_IDS}, "disclosure": _CAL_NOTE}

@app.get("/api/trend")
def trend(days: int = 14):
    """Historical trend/replay: daily national High/Severe ward counts.
    Past days come from data/trend_backfill.json (archive weather re-run through
    the same model, per-city representative ward); today onwards from the live
    full-scan history (data/history.jsonl). Both are labelled."""
    days=max(1,min(90,days))
    series=[]
    try:
        with open(HIST) as f:
            for l in f:
                if not l.strip(): continue
                e=json.loads(l)
                series.append({"ts":e["ts"],"src":e.get("src","live-scan"),
                               "high_severe":(e.get("total",{}).get("bands",{}).get("High",0)
                                              +e.get("total",{}).get("bands",{}).get("Severe",0)),
                               "bands":e.get("total",{}).get("bands",{}),
                               "cities":e.get("cities",[])})
    except Exception: pass
    try:
        with open(os.path.join(ROOT,"data","trend_backfill.json")) as f:
            for e in json.load(f):
                series.append(e)
    except Exception: pass
    by_day={}
    for e in series:
        d=(e.get("ts") or "")[:10]
        if not d: continue
        # live full-scan rows supersede backfilled rows for the same day
        if d not in by_day or e.get("src")=="live-scan": by_day[d]=e
    ordered=[by_day[d] for d in sorted(by_day)][-days:]
    return {"days":days,"points":len(ordered),"series":ordered,
            "disclosure":("Past days are model re-runs on archived weather (per-city representative ward), "
                          "not archived live scans; live rows are real full 417-ward scans. Bands use the same "
                          "UNVALIDATED default coefficients as the live model.")}


@app.get("/api/cities")
def cities():
    out=[]
    for city in CITY_IDS:
        cfg=STORE.cities[city]["config"]
        out.append({"id":city,"name":cfg["name"],"state":cfg["state"],"primary":cfg["primary"],
                    "centre":cfg["centre"],"n_wards":len(STORE.cities[city]["wards"]),
                    "census2011":cfg["census2011"],"weather_prov":LIVE.prov(city),
                    "map":STORE.cities[city]["map"].get("image"),
                    "boundary_source":STORE.cities[city]["boundary_source"],
                    "vuln":vulnerability(city)})
    return {"cities":out,"sim":SIM,"digest_s":DIGEST_S,"refresh_s":REFRESH_S,
            "weights":{k:WEIGHTS[k] for k in WEIGHTS},"v_weights":{k:W_V[k] for k in W_V}}

# ================================================================ Phase 2
@app.get("/api/city/{city}/ward/{wid:path}/projection")
def projection(city,wid):
    """Preventive-impact projection: 'if adaptive capacity (AC) rises / a measure
    is applied, how much does the modelled mortality risk fall?' It re-runs the
    SAME unvalidated defensible-default model with an assumed AC gain, so it
    inherits (and shows) the same V/mortality caveat. Clearly a scenario, not a
    measured outcome."""
    city=_norm(city); now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    w=STORE.ward(city,wid)
    if not w: raise HTTPException(404,"ward not found")
    rec=LIVE.ward(city,w)
    snap=cached_snapshot(city,w,rec,now) if rec else None
    if not snap or not snap.get("available"):
        return {"available":False,"reason":"Insufficient data","city":city,"ward_label":ward_label(city,w)}
    env=snap["environment"]["satellite"]; ac=snap["environment"].get("ac_ward",snap["environment"]["ac_city"])
    f=snap["factors"]; VV=f["V"]
    utci=snap["current"].get("utci"); anom=snap["current"].get("anom")
    a=_a_of(anom)
    surge=clamp((utci-36.0)/10.0) if utci else 0.0
    E=env["E"]; H=f["H"]
    base_mort,base_hosp=_risk_probs(a,surge,WEIGHTS["H"]*H,WEIGHTS["E"]*E,WEIGHTS["V"]*VV,WEIGHTS["AC"]*ac)
    rows=[]
    # Scenarios raise AC by an assumed gain (cooling access / refuge / shade programme)
    for label,gain,desc in [
        ("Cooling access uplift +10pp",0.10,"Adaptive-capacity programme (AC +0.10)"),
        ("Cooling access uplift +20pp",0.20,"Heat-Action-Plan shelters & AC reach (AC +0.20)"),
        ("Full cooling coverage +40pp",0.40,"Universal cooling/refuge coverage (AC +0.40, capped)")]:
        ac2=min(0.95,ac+gain); rel=(1-ac2)/(1-ac)   # cooling-deficit ratio
        # a higher AC lowers the exposure each individual faces -> E_eff down by rel
        E2=E*rel
        m,h=_risk_probs(a,surge,WEIGHTS["H"]*H,WEIGHTS["E"]*E2,WEIGHTS["V"]*VV,WEIGHTS["AC"]*ac)
        d_mort=(base_mort-m)/max(1e-6,base_mort)*100
        rows.append({"label":label,"desc":desc,"ac_from":round(ac,3),"ac_to":round(ac2,3),
                     "mort_before":round(base_mort,3),"mort_after":round(m,3),
                     "mort_band_after":rband(m)[0],"mortality_reduction_pct":round(d_mort,1),
                     "hosp_before":round(base_hosp,3),"hosp_after":round(h,3)})
    return {"city":city,"ward_label":ward_label(city,w),"available":True,
            "baseline":{"ac":round(ac,3),"mortality_prob":round(base_mort,3),
                        "mortality_band":snap["mortality"]["band"],"V":VV,"E":E},
            "scenarios":rows,
            "disclosure":"Scenario projection. Re-runs the same defensible-default (unvalidated) model with an assumed adaptive-capacity gain; not a measured outcome."}

def apply_measures(a, s, H, E, Vv, AC, measures):
    """Cumulatively apply MEASURE_EFFECTS in canonical order (pure function).
    Adjustments happen in RAW input space (AC re-clamped to [0.05,0.90], s
    floored at 0), then the same WEIGHTS as HTSI are applied before the
    logistic. Returns (a,s,H,E,Vv,AC,steps) with per-step P values (waterfall)."""
    Hw=WEIGHTS["H"]*H; Ew=WEIGHTS["E"]*E; Vw=WEIGHTS["V"]*Vv; ACw=WEIGHTS["AC"]*AC
    bm,bh=_risk_probs(a,s,Hw,Ew,Vw,ACw)
    steps=[]
    for m in MEASURE_EFFECTS:
        if m not in measures: continue
        eff=MEASURE_EFFECTS[m]
        if eff["term"]=="AC": AC=clamp(AC+eff["value"],0.05,0.90); ACw=WEIGHTS["AC"]*AC
        elif eff["term"]=="s": s=max(0.0,s+eff["value"])
        elif eff["term"]=="vt": Vv=1.0+eff["value"]*(Vv-1.0); Vw=WEIGHTS["V"]*Vv
        mm,hh=_risk_probs(a,s,Hw,Ew,Vw,ACw)
        steps.append({"measure":m,"term":eff["term"],"op":eff["op"],"value":eff["value"],
                      "rationale":eff["rationale"],
                      "AC":round(AC,3),"s":round(s,3),"Vv":round(Vv,4),
                      "mort_after":round(mm,4),"hosp_after":round(hh,4),
                      "mort_delta":round(mm-bm,4),"hosp_delta":round(hh-bh,4)})
    return a,s,H,E,Vv,AC,steps

def simulate_preventive_impact(city, ward_id, date, selected_measures):
    """Illustrative scenario: given this ward's baseline risk on `date`
    (default today), how far would the selected Section-5 measures lower the
    modelled mortality/hospitalisation P if activated? NOT a validated
    prediction - see disclosure in the response."""
    city=_norm(city)
    w=STORE.ward(city,ward_id)
    if not w: raise HTTPException(404,"ward not found")
    rec=LIVE.ward(city,w)
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if not rec:
        return {"available":False,"reason":"no live weather for ward","city":city,
                "ward_label":ward_label(city,w),"disclosure":SCENARIO_DISCLOSURE}
    env=env_terms(w.get("sat"), city)
    if not env:
        return {"available":False,"reason":"Insufficient data (environment)","city":city,
                "ward_label":ward_label(city,w),"disclosure":SCENARIO_DISCLOSURE}
    ac,_=ac_for_ward(city, env, w.get("sat"))
    VV=vulnerability(city,w)["v"]; E=env["E"]
    series=_series(rec)
    a=None; s=None
    if date:
        day=[o for o in series if (o["t"].replace(tzinfo=None)).date().isoformat()==date]
        us=[(o,_utci(o["tair"],o["rh"],o["wind"],o["sw"],o["t"].hour)) for o in day]
        us=[(o,u) for o,u in us if u is not None]
        if us:
            peak=max(us,key=lambda x:x[1])
            base=STORE.cities[city]["baseline"]["p90_monthly_tmax"].get(str(peak[0]["t"].month))
            a=_a_of(clamp((peak[0]["tair"]-base)/3.0) if base else 0.0)
            s=clamp((peak[1]-36.0)/10.0)
    Hh=None
    if a is None:
        res=severity(city,rec,series,now)
        a=_a_of(res["anom"]); s=clamp((res["utci"]-36.0)/10.0) if res["utci"] else 0.0
        Hh=res["hsev"]
    if Hh is None: Hh=clamp(0.62*a+0.38*s)
    bm,bh=_risk_probs(a,s,WEIGHTS["H"]*Hh,WEIGHTS["E"]*E,WEIGHTS["V"]*VV,WEIGHTS["AC"]*ac)
    a2,s2,H2,E2,V2,AC2,steps=apply_measures(a,s,Hh,E,VV,ac,list(selected_measures or []))
    am,ah=_risk_probs(a2,s2,WEIGHTS["H"]*H2,WEIGHTS["E"]*E2,WEIGHTS["V"]*V2,WEIGHTS["AC"]*AC2)
    return {"available":True,"city":city,"ward_label":ward_label(city,w),
            "date":date or now.date().isoformat(),
            "inputs":{"a":round(a,3),"s":round(s,3),"E":round(E,3),"V":round(VV,3),"AC":round(ac,3)},
            "baseline":{"mortality":round(bm,4),"hospitalisation":round(bh,4),
                        "mort_band":rband(bm)[0],"hosp_band":rband(bh)[0]},
            "adjusted":{"mortality":round(am,4),"hospitalisation":round(ah,4),
                        "mort_band":rband(am)[0],"hosp_band":rband(ah)[0]},
            "waterfall":steps,
            "selected":[m for m in MEASURE_EFFECTS if m in (selected_measures or [])],
            "disclosure":SCENARIO_DISCLOSURE}

class PrevReq(BaseModel):
    city:str=None; ward_id:str=None; date:str=None; measures:list=None
@app.get("/api/scenario/preventive")
def scenario_preventive_get(city:str=None, ward_id:str=None, date:str=None, measures:str=""):
    """Preventive-impact simulator (illustrative, unvalidated - see disclosure)."""
    if not city or not ward_id: raise HTTPException(400,"city and ward_id are required")
    return simulate_preventive_impact(city,ward_id,date,[m for m in measures.split(",") if m])
@app.post("/api/scenario/preventive")
def scenario_preventive_post(req:PrevReq):
    if not req.city or not req.ward_id: raise HTTPException(400,"city and ward_id are required")
    return simulate_preventive_impact(req.city,req.ward_id,req.date,req.measures or [])

@app.get("/api/city/{city}/allocation")
def allocation(city):
    """Resource-allocation output: rank wards by modelled risk (HTSI & mortality)
    and recommend proportional deployment of response resources."""
    city=_norm(city); now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows=[]
    for w in STORE.cities[city]["wards"]:
        rec=LIVE.ward(city,w)
        s=cached_snapshot(city,w,rec,now) if rec else None
        if not s or not s["available"]: continue
        htsi=s["current"]["htsi"]; mp=s["mortality"]["probability"]
        # simple priority score
        score=htsi*0.6+mp*0.4
        rows.append({"ward_id":w["id"],"ward":ward_label(city,w),"zone":w.get("zone"),
                     "htsi":htsi,"band":s["current"]["band"],"mort_band":s["mortality"]["band"],
                     "mort_prob":mp,"veg":s["environment"]["satellite"]["veg"],
                     "priority_score":round(score,4)})
    rows.sort(key=lambda r:r["priority_score"],reverse=True)
    total=sum(r["priority_score"] for r in rows) or 1.0
    for r in rows: r["share_pct"]=round(r["priority_score"]/total*100,1)
    return {"city":city,"mode":"ranked by modelled HTSI & mortality priority score",
            "rows":rows,
            "disclosure":"Resource allocation is driven by the modelled risk score (same defensible-default model). Deployment suggestion is illustrative, not an audited plan."}

class WgtReq(BaseModel):
    H:float=None; V:float=None; E:float=None; AC:float=None; scale:float=None
    c:float=None; l:float=None; s:float=None
    elderly:float=None; disability:float=None; density:float=None
    mort_intercept:float=None; mort_anom:float=None; mort_surge:float=None; mort_E:float=None; mort_V:float=None; mort_AC:float=None
    hosp_intercept:float=None; hosp_anom:float=None; hosp_surge:float=None; hosp_E:float=None; hosp_V:float=None; hosp_AC:float=None
    band1:float=None; band2:float=None; band3:float=None
    rband1:float=None; rband2:float=None; rband3:float=None
    sym_anom:bool=None
    measure_effects:dict=None
    reset:bool=None
@app.get("/api/weights")
def get_weights():
    return {"weights":{k:WEIGHTS[k] for k in WEIGHTS},
            "v_weights":{k:W_V[k] for k in W_V},
            "v_scale":V_SCALE,
            "risk_coef":RISK_COEF,"risk_coef_defaults":RISK_COEF_DEFAULTS,
            "measure_effects":MEASURE_EFFECTS,"measure_effects_defaults":MEASURE_EFFECTS_DEFAULTS,
            "band_t":list(BAND_T),"band_t_defaults":BAND_T_DEFAULTS,
            "risk_band_t":list(RISK_BAND_T),"risk_band_t_defaults":RISK_BAND_T_DEFAULTS,
            "flags":dict(FLAGS),
            "note":"Configurable: HTSI factor weights; V sub-weights; exposure-response logistic "
                   "coefficients (mort/hosp); HTSI band cut-points (band_t); mortality/hosp probability "
                   "cut-points (risk_band_t) - the two band scales are independently calibrated BY DESIGN "
                   "(HTSI is a 0..~0.5 composite, the logistics are probabilities); sym_anom flag; "
                   "measure_effects. All defensible UNVALIDATED defaults - see Methodology 4. "
                   "POST reset:true restores defaults."}
@app.post("/api/weights")
def set_weights(req:WgtReq):
    if req.reset:
        WEIGHTS.update({"H":1.0,"V":1.0,"E":1.0,"AC":1.0,"scale":1.0})
        W_V.update({"children":0.22,"literacy":0.13,"slum":0.18,"elderly":0.13,
                    "disability":0.09,"density":0.13,"kutcha":0.12})
        for k in ("mort","hosp"): RISK_COEF[k].update(RISK_COEF_DEFAULTS[k])
        for k in MEASURE_EFFECTS: MEASURE_EFFECTS[k].update(MEASURE_EFFECTS_DEFAULTS[k])
        BAND_T[:]=list(BAND_T_DEFAULTS); RISK_BAND_T[:]=list(RISK_BAND_T_DEFAULTS); FLAGS["sym_anom"]=False
    for k,v in [("H",req.H),("V",req.V),("E",req.E),("AC",req.AC),("scale",req.scale)]:
        if v is not None: WEIGHTS[k]=float(v)
    for k,v in [("children",req.c),("literacy",req.l),("slum",req.s)]:
        if v is not None: W_V[k]=float(v)
    for k in ("elderly","disability","density"):
        v=getattr(req,k,None)
        if v is not None: W_V[k]=float(v)
    cc=lambda x: max(-12.0,min(12.0,float(x)))
    for out,pref in ((RISK_COEF["mort"],"mort"),(RISK_COEF["hosp"],"hosp")):
        for kk in ("intercept","anom","surge","E","V","AC"):
            v=getattr(req,pref+"_"+kk,None)
            if v is not None: out[kk]=cc(v)
    bt=[req.band1,req.band2,req.band3]
    if all(x is not None for x in bt):
        b=[float(x) for x in bt]
        if 0.001<b[0]<b[1]<b[2]<0.999: BAND_T[:]=b   # keep strictly ordered cut-points
    rbt=[req.rband1,req.rband2,req.rband3]
    if all(x is not None for x in rbt):
        b=[float(x) for x in rbt]
        if 0.001<b[0]<b[1]<b[2]<0.999: RISK_BAND_T[:]=b
    if req.sym_anom is not None: FLAGS["sym_anom"]=bool(req.sym_anom)
    for k,v in (req.measure_effects or {}).items():
        if k not in MEASURE_EFFECTS: continue
        val=v["value"] if isinstance(v,dict) else v
        try: val=float(val)
        except Exception: continue
        # plausibility guards: additive deltas in [-0.5,0.5], multipliers in [0,1]
        MEASURE_EFFECTS[k]["value"]=max(-0.5,min(0.5,val)) if MEASURE_EFFECTS[k]["op"]=="add" else max(0.0,min(1.0,val))
    _V_CACHE.clear(); _MED_CACHE.clear(); _SNAP_CACHE.clear()
    _save_weights_file()
    return get_weights()

class SimReq(BaseModel): offset:float=0; label:str=""
@app.post("/api/sim")
def sim(req:SimReq):
    SIM["offset"]=float(req.offset); SIM["label"]=req.label or ""
    return {"sim":SIM,"note":"Labelled heatwave simulator (not live). Raises temperature across wards to preview escalation, measures and alerts."}

@app.get("/api/city/{city}/geometry")
def city_geometry(city, response: Response):
    """Static ward boundary geometry only. Fetched once per city and
    cached client-side; /wards now returns properties only."""
    city=_norm(city)
    pgg=pg_store.wards_geometry(city)
    feats=[{"type":"Feature","id":w["id"],
            "geometry":(pgg.get(str(w["id"])) if pgg else None) or w["geom"]}
           for w in STORE.cities[city]["wards"]]
    response.headers["Cache-Control"]="public, max-age=86400"
    return {"city":city,"features":feats}

@app.get("/api/city/{city}/wards")
def city_wards(city):
    city=_norm(city); now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    feats=[]
    agg={"htsi":{"Low":0,"Moderate":0,"High":0,"Severe":0},
         "mortality":{"Low":0,"Moderate":0,"High":0,"Severe":0}}
    outlook={}
    highest=0
    order=["Low","Moderate","High","Severe"]
    for w in STORE.cities[city]["wards"]:
        rec=LIVE.ward(city,w)
        snap=cached_snapshot(city,w,rec,now) if rec else None
        p={"id":w["id"],"label":ward_label(city,w),"zone":w.get("zone"),"area_km2":w.get("area_km2"),"sat":w.get("sat")}
        if snap and snap["available"]:
            c=snap["current"]; mb=snap["mortality"]["band"]; hb=snap["hospitalisation"]["band"]
            agg["htsi"][c["band"]]=agg["htsi"].get(c["band"],0)+1
            agg["mortality"][mb]=agg["mortality"].get(mb,0)+1
            hi=order.index(mb); highest=max(highest,hi)
            for f in (snap.get("forecast") or []):
                d=outlook.setdefault(f["date"],{"mort":0.0,"hosp":0.0,"n":0})
                if f.get("mortality_prob") is not None: d["mort"]+=f["mortality_prob"]; d["n"]+=1
                if f.get("hosp_prob") is not None: d["hosp"]+=f["hosp_prob"]
            p.update({"available":True,"htsi":c["htsi"],"band":c["band"],"utci":c["utci"],
                      "tair":c["tair"],"mort":mb,"hosp":hb,
                      "veg":(snap["environment"]["satellite"] or {}).get("veg")})
        else: p.update({"available":False,"band":"Insufficient"})
        feats.append({"type":"Feature","properties":p,"geometry":None})
    rows=[]
    for date in sorted(outlook):
        d=outlook[date]
        rows.append({"date":date,"day":dt.date.fromisoformat(date).strftime("%a %d %b"),
                     "mortality_prob":round(d["mort"]/max(1,d["n"]),3),
                     "hosp_prob":round(d["hosp"]/max(1,d["n"]),3)})
    city_measures=admin_actions(order[highest])
    if highest==0:
        city_measures=["Heat within seasonal normal. Routine heat-advisory monitoring continues; no escalated response triggered."]
    return {"city":city,"features":feats,"sim":SIM,"geo_source":"omitted (see /geometry)",
            "aggregate":{"distribution":agg,"outlook":rows,
                         "alert_level":order[highest],
                         "admin_measures":city_measures},
            "mapmeta":{"zoom":STORE.cities[city]["map"]["zoom"],
                       "tx":STORE.cities[city]["map"]["tile_min"][0],
                       "ty":STORE.cities[city]["map"]["tile_min"][1],
                       "W":STORE.cities[city]["map"]["nx"]*256,
                       "H":STORE.cities[city]["map"]["ny"]*256}}
  
@app.get("/api/city/{city}/basemap")
def basemap(city):
    city=_norm(city); return FileResponse(os.path.join(ROOT,"data","cities",city,STORE.cities[city]["map"]["image"]))

@app.get("/api/city/{city}/ward/{wid:path}")
def ward_detail(city,wid):
    city=_norm(city); now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    w=STORE.ward(city,wid)
    if not w: raise HTTPException(404,"ward not found")
    rec=LIVE.ward(city,w)
    snap=cached_snapshot(city,w,rec,now) if rec else None
    if snap and snap.get("ward") is not None:
        snap["ward"]["label"]=ward_label(city,snap["ward"])
    wout=dict(w); wout["label"]=ward_label(city,w)
    cfg=STORE.cities[city]["config"]
    return {"city":city,"city_name":cfg["name"],"ward":wout,"snapshot":snap,
            "census2011":cfg["census2011"],"ac_ref":cfg["ac_ref"],
            "cadence":cadence_table(),"non_goals":non_goals(),"sim":SIM,
            "tile_source":STORE.cities[city]["tile_source"]}

@app.get("/api/city/{city}/export.csv")
def export_city_csv(city: str):
    """Full per-ward audit export: HTSI, both risk outputs and every logistic term,
    computed by the same compute_snapshot() path that serves the ward panel."""
    city=_norm(city)
    if city not in STORE.cities: raise HTTPException(404,"unknown city")
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cols=["city","ward","label","available","htsi","htsi_band","mort_band","mort_prob","hosp_band","hosp_prob",
          "z_mort","z_hosp","mort_intercept","mort_anom","mort_surge","mort_H","mort_E","mort_V","mort_AC",
          "hosp_intercept","hosp_anom","hosp_surge","hosp_H","hosp_E","hosp_V","hosp_AC","H","V","E","AC"]
    buf=io.StringIO(); wtr=csv.writer(buf)
    wtr.writerow(cols)
    for w in STORE.cities[city]["wards"]:
        rec=LIVE.ward(city,w)
        snap=cached_snapshot(city,w,rec,now) if rec else None
        row=[city,w["id"],ward_label(city,w)]
        if snap and snap.get("available"):
            c=snap["current"]; f=snap["factors"]; rt=f["risk_terms"]
            row+=[1,c["htsi"],c["band"],snap["mortality"]["band"],snap["mortality"]["probability"],
                  snap["hospitalisation"]["band"],snap["hospitalisation"]["probability"],
                  rt["z_mort"],rt["z_hosp"],
                  rt["mort"]["intercept"],rt["mort"]["anom"],rt["mort"]["surge"],rt["mort"]["H"],rt["mort"]["E"],rt["mort"]["V"],rt["mort"]["AC"],
                  rt["hosp"]["intercept"],rt["hosp"]["anom"],rt["hosp"]["surge"],rt["hosp"]["H"],rt["hosp"]["E"],rt["hosp"]["V"],rt["hosp"]["AC"],
                  f["H"],f["V"],f["E"],f["AC"]]
        else:
            row+=[0]+[""]*(len(cols)-3)
        wtr.writerow(row)
    body=("# TAPAS ward export - %s - %s\n" % (city, now.isoformat())
          +"# Coefficients are defensible UNVALIDATED defaults (GET /api/weights shows live values); early-warning signals, not clinical predictions.\n"
          +buf.getvalue())
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition":'attachment; filename="tapas_%s_wards.csv"'%city})

@app.get("/api/outbox")
def get_outbox(): return list(reversed(outbox[-60:]))

@app.get("/api/db/status")
def db_status():
    """Live storage status: PostGIS when DATABASE_URL is reachable, else JSON."""
    return pg_store.pg_status()

@app.get("/api/twilio/status")
def twilio_status():
    vars_needed=["TWILIO_ACCOUNT_SID","TWILIO_AUTH_TOKEN","TWILIO_FROM","TWILIO_TO"]
    have={k:bool(os.environ.get(k)) for k in vars_needed}
    return {"configured":all(have.values()),"vars":have,
            "note":"When all four env vars are set, every alert and the test trigger perform REAL Twilio sends "
                   "(channel_result reports SENT <sid>); otherwise everything is honestly SIMULATED. "
                   "docker-compose.yml passes them through from your environment."}

@app.post("/api/alerts/test")
def alerts_test():
    """Demoable/verifiable real-send trigger: builds a FULL sample alert using
    the exact same message-construction path real event alerts use (trilingual
    personal guidance + emergency block via log_alert/build_sms), so the real
    alert *content* can be shown live even when Twilio itself isn't configured
    (the send result is still honestly SIMULATED/SENT either way). Uses the
    current worst-band ward if one is available so the numbers are real; falls
    back to a representative sample ward (forced to "High" for the message)
    if nothing is currently at High/Severe, so the demo content is never empty."""
    atrisk=[]
    try:
        atrisk,_,_=compute_national(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    except Exception:
        pass
    if atrisk:
        s=max(atrisk,key=lambda x:_ORDER_BAND.get(x["current"]["band"],0))
        city=s["ward"]["city"]; label=s["ward"]["label"]; band=s["current"]["band"]
        htsi=s["current"]["htsi"]; utci=s["current"].get("utci")
        adm=s.get("measures",{}).get("admin",[])
    else:
        city=CITY_IDS[0]; w=STORE.cities[city]["wards"][0]
        label=ward_label(city,w); band="High"; htsi=0.12; utci=33.8
        adm=admin_actions(band)
    msgs={"High":"Heat escalation: High hazard. ","Severe":"Heat escalation: SEVERE hazard. "}
    msg=f"[TAPAS TEST] {city} {label} - {msgs.get(band,msgs['High'])}HTSI {htsi}, UTCI {utci}C. Preventive actions recommended now."
    if adm: msg+=" Action: "+adm[0]
    e=log_alert("test",f"{city} {label}",band,msg,
                extra={"ward_city":city,"note":"manually triggered sample — not an automatic detection"})
    return {"sent":e["channel_result"].startswith("SENT"),"channel_result":e["channel_result"]}

@app.get("/api/cadence")
def cadence_api(): return {"cadence":cadence_table(),"non_goals":non_goals()}

def _norm(city):
    for c in CITY_IDS:
        if c.lower()==city.lower(): return c
    raise HTTPException(404,"unknown city")

def cadence_table():
    return [
      {"layer":"Live / forecast weather","source":"OpenWeatherMap","revisit":"Hourly","system":"Refreshed up to every 6h (within source capability)."},
      {"layer":"Thermal environment","source":"Esri World Imagery true-colour per-ward analysis + REAL MODIS Terra per-ward values (NASA GIBS, no key): daytime LST (MOD11A1) and NDVI (MOD13Q1 rolling 8-day)","revisit":"Daily LST / 8-day NDVI (fetch_modis.py)","system":"LST/NDVI decoded from NASA official colour maps, mean of pixels inside each real ward polygon; LST anomaly vs city median adjusts exposure E; NDVI feeds greenness + cooling-access proxy. Ahmedabad LST currently absent (monsoon cloud, honestly skipped). NOT a fake sub-6h product."},
      {"layer":"Baseline / thresholds","source":"ECMWF ERA5 reanalysis (2014-2024 observed)","revisit":"Climatology","system":"Observed seasonal threshold only - never a live value."},
      {"layer":"Demographics / vulnerability","source":"Census of India 2011 (C-14 City; C-14 AP for Hyderabad) + ward-level HL-14 + Sagar et al. 2016 (PMC4973875) disability","revisit":"Static","system":"V = 7 indicators (children, literacy, slum, elderly 60+, disability, density, kutcha); elderly from C-14 City tables, Hyderabad via the fully-urban Hyderabad district row of C-14 AP (2011 jurisdiction; no official city row exists); kutcha REAL per ward from HL-14 (city-total fallback disclosed); slum pending where unpublished; weights renormalise over available indicators."},
      {"layer":"Cooling access (AC)","source":"City baseline (documented) x per-ward modifiers: satellite built density, MODIS NDVI, HL-14 electricity + tap water, OSM hospitals/km2","revisit":"Computed per ward","system":"PER-WARD composite, modelled default coefficients - UNVALIDATED, not survey data. Disclosed in factor panel."},
      {"layer":"Local Climate Zones","source":"Demuzere et al. 2022 global LCZ map v1 (WUDAPT lineage), 100 m, Zenodo 6364594, LCZ_Filter band","revisit":"Static (2018 nominal)","system":"Per-ward mode class inside the real ward polygon via COG windowed reads (fetch_lcz.py); built-class share blends 15% into exposure E."},
      {"layer":"Alert triggers","source":"System","revisit":"Computed","system":"Event-driven (band crossing, deduped by cooldown/escalation) + 6h city digest with CROSS-SUPPRESSION (wards already event-alerted in-window are omitted). Real Twilio send when TWILIO_* env creds present; otherwise honestly SIMULATED."},
      {"layer":"Historical trend","source":"Open-Meteo archive re-run (backfill) + live full-scan history.jsonl","revisit":"Hourly (live) / daily (backfill)","system":"Backfill days = same model on archived weather with a per-city representative ward; live days = real 417-ward scans."},
      {"layer":"Storage","source":"JSON files (runtime) + PostGIS loader provided (sql/schema.sql, load_postgis.py)","revisit":"Static","system":"Runtime is file-based for portability; PostGIS path shipped for production deploy."},
    ]
def non_goals():
    return [
      "Not a clinical mortality/hospitalisation predictor.",
      "Satellite thermal layer is static per ward, not a live sub-6h product.",
      "India does not publish ward-level social demography at municipal-ward granularity; city Census figures are shown and ward risk uses real satellite + live weather.",
      "Preventive guidance is protocol-driven (Heat-Action-Plan style), not a per-person guarantee.",
      "Simulator overlays are clearly labelled not-live and default off.",
    ]

class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        return resp

app.mount("/static",_NoCacheStatic(directory=os.path.join(ROOT,"static")),name="static")
if os.environ.get("TAPAS_NOSCHED")!="1":
    SCHED=Sched(); SCHED.start()
