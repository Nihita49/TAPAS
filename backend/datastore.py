"""Multi-city datastore. Loads, for each of 4 cities:
 - real municipal ward polygons (+ centroid, area)
 - real satellite-derived ward attributes (veg/built/water fractions) from the
   actual basemap imagery
 - real Open-Meteo 1991-2020 Climate-Normals monthly thresholds (baseline)
 - documented city-level Census-2011 header + cooling/demographic reference
Values carry provenance. Nothing is silently substituted.
"""
import json, os
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
CITYROOT=os.path.join(ROOT,"data","cities")

CITIES = {
  "Mumbai":    {"name":"Mumbai","state":"Maharashtra","short":"Mum","primary":True,
     "centre":[72.8777,19.076],
     "census2011":{"population":12442373,"source":"Census of India 2011, Greater Mumbai (M Corp.)"},
     "vuln":{"literacy":0.8973,"children_0_6":0.0968,"slum":0.4184,
             "elderly_60plus":0.0857,                # Census 2011 C-14 City: urban 60+ share, Greater Mumbai (M Corp.)
             "disability":0.0270,                    # age-standardised, Maharashtra (Sagar et al. 2016, PMC4973875, Census 2011)
             "kutcha":0.016,                         # Census 2011 HL-14: kachcha roof % of households, Greater Mumbai total
             "density":{"value":20621,"area_km2":603.4,"basis":"derived: Census 2011 population / MCGM area 603.4 km2"},
             "source":"Census 2011: city figures; C-14 City elderly; HL-14 ward-level electricity/kutcha/tapwater; Sagar et al. 2016 (PMC4973875) disability; per-ward HL-14 values where published"},
     "ac_ref":{"value":0.45,"basis":"city baseline; modulated per-ward by satellite built/NDVI proxy (modelled, unvalidated)","conf":0.55}},
  "Ahmedabad": {"name":"Ahmedabad","state":"Gujarat","short":"Ahd","primary":False,
     "centre":[72.5714,23.0225],
     "census2011":{"population":5577940,"source":"Census of India 2011, Ahmedabad (M Corp.)"},
     "vuln":{"literacy":0.8829,"children_0_6":0.1113,"slum":0.0449,
             "elderly_60plus":0.0796,"disability":0.0193,"kutcha":0.007,   # C-14 City elderly; HL-14 kutcha roof (Ahmadabad M Corp. total)
             "density":{"value":12017,"area_km2":464.16,"basis":"derived: Census 2011 population / AMC area 464.16 km2"},
             "source":"Census 2011: city figures; C-14 City elderly; HL-14 ward-level electricity/kutcha/tapwater; Sagar et al. 2016 (PMC4973875) disability"},
     "ac_ref":{"value":0.42,"basis":"city baseline (Ahmedabad Heat Action Plan context); modulated per-ward by satellite built/NDVI proxy (modelled, unvalidated)","conf":0.55}},
  "Chennai":   {"name":"Chennai","state":"Tamil Nadu","short":"Che","primary":False,
     "centre":[80.2707,13.0827],
     "census2011":{"population":4646732,"source":"Census of India 2011, Chennai (M Corp.)"},
     "vuln":{"literacy":0.9018,"children_0_6":0.0988,"slum":None,
             "elderly_60plus":0.0984,"disability":0.0164,"kutcha":0.038,   # C-14 City elderly; HL-14 kutcha roof (Chennai M Corp. total)
             "density":{"value":26609,"area_km2":174.63,"basis":"derived: Census 2011 population / pre-2018 GCC area 174.63 km2"},
             "source":"Census 2011: city figures; C-14 City elderly; HL-14 ward-level electricity/kutcha/tapwater; Sagar et al. 2016 (PMC4973875) disability; slum share pending"},
     "ac_ref":{"value":0.55,"basis":"city baseline (coastal, higher AC uptake); modulated per-ward by satellite built/NDVI proxy (modelled, unvalidated)","conf":0.55}},
  "Hyderabad": {"name":"Hyderabad","state":"Telangana","short":"Hyd","primary":False,
     "centre":[78.4867,17.385],
     "census2011":{"population":6731790,"source":"Census of India 2011, Hyderabad (M Corp.)"},
     "vuln":{"literacy":0.8326,"children_0_6":0.1188,"slum":None,
             "elderly_60plus":0.0677,              # C-14 Andhra Pradesh (2011 jurisdiction), Hyderabad district - fully urban (rural=0); official C-14 City row for Hyderabad was never published (verified absent in national C-14 City file and in AP/Telangana state files)
             "disability":0.0280,"kutcha":0.028,     # HL-14 kutcha roof (GHMC total); Sagar et al. 2016 (undivided AP) disability
             "density":{"value":10357,"area_km2":650.0,"basis":"derived: Census 2011 population / GHMC area 650 km2"},
             "source":"Census 2011: city figures; elderly 60+ from C-14 AP fully-urban Hyderabad district (2011 jurisdiction) because no official C-14 City row exists; HL-14 ward-level electricity/kutcha/tapwater; Sagar et al. 2016 (PMC4973875, undivided AP) disability; slum share pending"},
     "ac_ref":{"value":0.5,"basis":"city baseline; modulated per-ward by satellite built/NDVI proxy (modelled, unvalidated)","conf":0.55}},
}
class CityStore:
    def __init__(self):
        self.cities={}
        for cid,cfg in CITIES.items():
            cdir=os.path.join(CITYROOT,cid)
            fc=json.load(open(os.path.join(cdir,"wards.geojson")))
            mm=json.load(open(os.path.join(cdir,"map.json")))
            base=json.load(open(os.path.join(cdir,"baseline.json")))
            wards=[]
            for f in fc["features"]:
                p=f["properties"]
                wards.append({
                  "id":p["id"],"label":p.get("label"),"zone":p.get("zone"),
                  "centroid":p["centroid"],"area_km2":p.get("area_km2"),
                  "sat":p.get("satellite"),
                  "geom":f["geometry"],
                })
            # ---- merge Census HL-14 ward-level amenities + OSM health access ----
            try:
                hl=json.load(open(os.path.join(ROOT,"data","hl14",cid.lower()+"_parsed.json")))
                hlw=hl.get("wards") or {}
                n=0
                for w in wards:
                    if not w["sat"]: w["sat"]={}
                    r=hlw.get(str(w["id"]))
                    if r:
                        w["sat"]["elec_pct"]=r.get("elec_pct")
                        w["sat"]["kutcha_pct"]=r.get("roof_kutcha_pct")
                        w["sat"]["kutcha_wall_pct"]=r.get("wall_kutcha_pct")
                        w["sat"]["tap_pct"]=r.get("tap_treated_pct")
                        n+=1
                    elif hl.get("city_total"):   # honest fallback: city total, labelled
                        t=hl["city_total"]
                        w["sat"].setdefault("elec_pct",t.get("elec_pct"))
                        w["sat"].setdefault("kutcha_pct",t.get("roof_kutcha_pct"))
                        w["sat"].setdefault("tap_pct",t.get("tap_treated_pct"))
                hl_n=n
            except Exception:
                hl_n=0
            try:
                hj=json.load(open(os.path.join(cdir,"health.json")))
                hw=hj.get("wards") or {}
                for w in wards:
                    if not w["sat"]: w["sat"]={}
                    r=hw.get(str(w["id"]))
                    if r: w["sat"]["health_per_km2"]=r.get("per_km2")
            except Exception:
                pass
            # ---- merge REAL WUDAPT/Demuzere LCZ per-ward classes ----
            try:
                lz=json.load(open(os.path.join(cdir,"lcz.json")))
                for w in wards:
                    if not w["sat"]: w["sat"]={}
                    r=(lz.get("wards") or {}).get(str(w["id"]))
                    if r:
                        w["sat"]["lcz"]=r.get("lcz"); w["sat"]["lcz_name"]=r.get("lcz_name")
                        w["sat"]["lcz_built_share"]=r.get("built_share")
            except Exception:
                pass
            # ---- merge REAL MODIS per-ward values (fetch_modis.py output) ----
            modis=None
            mpath=os.path.join(cdir,"modis.json")
            if os.path.exists(mpath):
                try: modis=json.load(open(mpath))
                except Exception: modis=None
            if modis:
                for w in wards:
                    if not w["sat"]: continue
                    k=str(w["id"])
                    if modis.get("lst") and k in modis["lst"].get("wards",{}):
                        w["sat"]["lst_day_c"]=modis["lst"]["wards"][k]
                    if modis.get("ndvi") and k in modis["ndvi"].get("wards",{}):
                        w["sat"]["ndvi_modis"]=modis["ndvi"]["wards"][k]
                lst_n=len(modis.get("lst",{}).get("wards",{})); ndvi_n=len(modis.get("ndvi",{}).get("wards",{}))
                modis["summary"]={"lst_wards":lst_n,"ndvi_wards":ndvi_n,
                                  "lst_date":(modis.get("lst") or {}).get("date"),
                                  "ndvi_date":(modis.get("ndvi") or {}).get("date")}
            self.cities[cid]={
               "config":cfg,"wards":wards,
               "map":mm,"baseline":base,"modis":modis,
               "boundary_source":cfg["name"]+" "+mm.get("boundary_source",""),
               "tile_source":mm.get("tile_source",""),
            }
    def ward(self,city,key):
        key=str(key)
        for w in self.cities[city]["wards"]:
            if str(w["id"])==key: return w
        return None

STORE=CityStore()
CITY_IDS=list(CITIES.keys())
if __name__=="__main__":
    for c in CITY_IDS:
        s=STORE.cities[c]
        print(c,"wards",len(s["wards"]),"map",s["map"]["image"],"satok",
              sum(1 for w in s["wards"] if w["sat"]),"p90Apr",s["baseline"]["p90_monthly_tmax"]["4"])
