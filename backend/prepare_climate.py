"""Per-city baseline from ECMWF ERA5 reanalysis (observed) daily Tmax, ~11 yrs.
Monthly 90th percentile = 'unusually hot for this city this month'. Replaces the
earlier CMIP Climate-Normals (which ran cool in places) and the pre-spec ERA5
monthly tables. Real data via Open-Meteo archive API, no key.
"""
import json, os, requests, collections
import numpy as np
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
CITIES={"Mumbai":(19.076,72.877),"Ahmedabad":(23.0225,72.5714),
        "Chennai":(13.0827,80.2707),"Hyderabad":(17.385,78.4867)}
def pull(lat,lon):
    mo=collections.defaultdict(list)
    for year in range(2014,2025):
        s=f"{year}-01-01"; e=f"{year}-12-31"
        r=requests.get("https://archive-api.open-meteo.com/v1/era5",
          params={"latitude":lat,"longitude":lon,"start_date":s,"end_date":e,
                  "daily":"temperature_2m_max","timezone":"Asia/Kolkata"},timeout=90)
        r.raise_for_status(); d=r.json()
        times=d["daily"]["time"]; vals=d["daily"]["temperature_2m_max"]
        for t,v in zip(times,vals):
            if v is not None:
                mo[int(t[5:7])].append(float(v))
    return mo
def main():
    for city,(lat,lon) in CITIES.items():
        mo=pull(lat,lon)
        p90={}; mean={}
        for mon in range(1,13):
            a=np.array(mo.get(mon,[]))
            p90[mon]=round(float(np.percentile(a,90)),1) if a.size else None
            mean[mon]=round(float(a.mean()),1) if a.size else None
        out={"city":city,"lat":lat,"lon":lon,
             "source":"ECMWF ERA5 reanalysis (Open-Meteo archive), daily Tmax 2014-2024",
             "unit":"degC","p90_monthly_tmax":{str(m):p90[m] for m in range(1,13)},
             "mean_monthly_tmax":{str(m):mean[m] for m in range(1,13)},
             "note":"Observed 90th-percentile of daily max temp per month. Baseline/threshold only - never a live value."}
        cdir=os.path.join(ROOT,"data","cities",city); os.makedirs(cdir,exist_ok=True)
        json.dump(out,open(os.path.join(cdir,"baseline.json"),"w"),indent=1)
        print(city,"p90 Sept:",p90[9],"Aug:",p90[8],"Apr:",p90[4],"n_years samples:",len(mo[9])//30)
main()
