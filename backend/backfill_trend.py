"""Trend backfill: re-run the SAME model over the last N days of ARCHIVED weather
(Open-Meteo archive API, real reanalysis-quality data) for a per-city
representative ward, producing data/trend_backfill.json consumed by /api/trend.

Honest labelling: these rows are model re-runs on archived weather with one
representative ward per city (not full 417-ward scans, which are recorded live
going forward in data/history.jsonl). The API and UI disclose this.

Run:  TAPAS_NOSCHED=1 python3 backfill_trend.py [days]
"""
import os, sys, json, datetime as dt
os.environ.setdefault("TAPAS_NOSCHED", "1")
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app  # reuses the real model: compute_snapshot/env_terms/vulnerability
from app import STORE, CITY_IDS, compute_snapshot, city_sat_medians

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
OUT = os.path.join(os.path.dirname(HERE), "data", "trend_backfill.json")
ARCH = "https://archive-api.open-meteo.com/v1/archive"

def representative_ward(city):
    med = city_sat_medians(city)
    best = None; bestd = 1e9
    for w in STORE.cities[city]["wards"]:
        s = w.get("sat")
        if not s or s.get("built_frac") is None: continue
        d = abs(s["built_frac"] - med["built"]) + abs(s.get("veg_frac", 0) - med["veg"])
        if d < bestd: bestd = d; best = w
    return best

def fetch_archive(lat, lon, start, end):
    r = requests.get(ARCH, timeout=120, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation",
        "timezone": "Asia/Kolkata"})
    r.raise_for_status()
    j = r.json()
    j["_prov"] = "archive"
    return j

def main():
    end = dt.date.today() - dt.timedelta(days=1)      # archive latency ~ a few days
    start = end - dt.timedelta(days=DAYS - 1)
    rows = {}
    for city in CITY_IDS:
        cfg = STORE.cities[city]["config"]
        w = representative_ward(city)
        if not w:
            print(city, "no representative ward"); continue
        lat, lon = cfg["centre"][1], cfg["centre"][0]
        try:
            rec = fetch_archive(lat, lon, start.isoformat(), end.isoformat())
        except Exception as e:
            print(city, "archive fetch failed:", e); continue
        days = sorted({t[:10] for t in rec.get("hourly", {}).get("time", [])})
        n = 0
        for d in days:
            now = dt.datetime.fromisoformat(d + "T14:00:00")   # daily heat peak (local)
            snap = compute_snapshot(city, w, rec, now)
            if not snap or not snap.get("available"): continue
            c = snap["current"]
            row = rows.setdefault(d, {"ts": d + "T14:00:00", "src": "archive-backfill",
                                      "high_severe": None, "bands": None, "cities": []})
            row["cities"].append({"city": city, "worst": c["band"], "htsi": c["htsi"],
                                  "utci": c.get("utci"), "rep_ward": w.get("label") or w["id"]})
            n += 1
        print(f"{city}: {n} backfilled days (rep ward {w.get('label') or w['id']})")
    out = [rows[d] for d in sorted(rows)]
    json.dump(out, open(OUT, "w"))
    print("wrote", OUT, len(out), "days")

if __name__ == "__main__":
    main()
