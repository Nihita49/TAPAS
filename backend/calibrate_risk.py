"""Calibrate TAPAS mortality / hospitalization risk against historical health data.

  python backend/calibrate_risk.py                 fit observed data + anchors (needs network)
  python backend/calibrate_risk.py --anchors-only  published anchors only (offline)

Reads   data/observed/observed.csv           daily deaths / admissions per city
        data/observed/published_anchors.json published relative risks (priors)
Writes  data/calibration.json                read by the app at runtime

For every city it re-runs the model's own daily exposure (daily_heat_features
in app.py, the same code used live) on ARCHIVED ERA5 weather for the dates in
observed.csv, then fits a Poisson count model:
  deaths(t)     ~ exp(b_a*a + b_s*surge [+ g*log(1+admissions(t-1))] + dow + season + trend)
  admissions(t) ~ exp(b_a*a + b_s*surge + dow + season + trend)
A fit is accepted only with >= 365 rows, >= 15 hot days and a positive,
significant heat slope; otherwise the published anchor (if any) is kept and the
reason is recorded. After running, POST /api/calibration/reload (or restart).
"""
import os, sys, json, datetime as dt
os.environ.setdefault("TAPAS_NOSCHED", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import calibration as cal
import app
from app import CITY_IDS, STORE, _series, daily_heat_features, _mean_tmax, _a_of

ARCH = "https://archive-api.open-meteo.com/v1/archive"


def fetch_archive_range(lat, lon, start, end):
    """Archived hourly weather for [start, end] (date objects), fetched a year at a time."""
    import requests
    hourly = {"time": [], "temperature_2m": [], "relative_humidity_2m": [],
              "wind_speed_10m": [], "shortwave_radiation": []}
    y = start
    while y <= end:
        stop = min(end, dt.date(y.year, 12, 31))
        r = requests.get(ARCH, timeout=180, params={
            "latitude": lat, "longitude": lon, "start_date": y.isoformat(), "end_date": stop.isoformat(),
            "hourly": ",".join(k for k in hourly if k != "time"), "timezone": "Asia/Kolkata"})
        r.raise_for_status()
        h = r.json().get("hourly", {})
        for k in hourly: hourly[k] += h.get(k, [])
        y = stop + dt.timedelta(days=1)
    return {"hourly": hourly}


def city_features(city, dates, fetch=fetch_archive_range):
    """{date: (a, surge)} for the given ISO dates, from archived weather."""
    ds = sorted(dt.date.fromisoformat(d) for d in dates)
    cfg = STORE.cities[city]["config"]
    rec = fetch(cfg["centre"][1], cfg["centre"][0], ds[0], ds[-1])
    series = _series(rec)
    out = {}
    for d in ds:
        f = daily_heat_features(city, series, d)
        if f: out[d.isoformat()] = (f[0], f[1])
    return out


def fit_city(city, rows, feats):
    """-> ({'mort': block|None, 'hosp': block|None}, {'mort': note, 'hosp': note})."""
    blocks, notes = {"mort": None, "hosp": None}, {}
    days = sorted(d for d in rows if d in feats)
    def take(key, lag_adm=False):
        D, Y, A, S, L = [], [], [], [], []
        for d in days:
            y = rows[d].get(key)
            if y is None: continue
            if lag_adm:
                prev = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
                la = (rows.get(prev) or {}).get("admissions")
                if la is None: continue
                L.append(la)
            D.append(d); Y.append(y); A.append(feats[d][0]); S.append(feats[d][1])
        return D, Y, A, S, L
    for outcome, key in (("mort", "deaths"), ("hosp", "admissions")):
        D, Y, A, S, _ = take(key)
        if not D:
            continue
        blk = cal.fit_outcome(D, Y, A, S)
        if outcome == "mort" and "rejected" not in blk:
            D2, Y2, A2, S2, L2 = take("deaths", lag_adm=True)
            if len(D2) >= cal.MIN_ROWS:
                b2 = cal.fit_outcome(D2, Y2, A2, S2, adm_lag=L2)
                if "rejected" not in b2 and "gamma_adm" in b2: blk = b2
        if "rejected" in blk:
            notes[outcome] = "fit rejected: " + blk["rejected"]
        else:
            contiguous = all((dt.date.fromisoformat(D[i + 1]) - dt.date.fromisoformat(D[i])).days == 1
                             for i in range(len(D) - 1))
            if contiguous: blk["lag_aic"] = cal.lag_scan(D, Y, A, S)
            blocks[outcome] = blk
    return blocks, notes


def build(anchors_only=False, fetch=fetch_archive_range, observed=None):
    anchors = cal.load_anchors()
    observed = observed if observed is not None else cal.read_observed()
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "cities": {},
           "notes": ["Calibrated output is a relative risk vs a normal day for the city's typical ward; "
                     "ward differences scale the excess by V x E x (1-AC) relative to the city median and are NOT validated by city data.",
                     "Basis per output: fitted (observed data) > published-anchor (prior) > default (unvalidated)."]}
    nc = anchors.get("not_calibrated") or {}
    for city in CITY_IDS:
        blocks = {"mort": None, "hosp": None}; notes = {}
        for an in anchors.get("anchors", []):
            if not an.get("use") or city not in (an.get("cities") or []): continue
            o = an.get("outcome", "mort")
            b = cal.anchor_block(an, city, _mean_tmax, _a_of)
            if b:
                base = (anchors.get("baselines") or {}).get(city)
                if base and o == "mort":
                    b["baseline_per_day"] = base["deaths_per_day"]; b["baseline_note"] = base.get("note")
                blocks[o] = b
        rows = observed.get(city) or {}
        if rows and not anchors_only:
            try:
                feats = city_features(city, list(rows), fetch)
                fb, fn = fit_city(city, rows, feats)
                for o in ("mort", "hosp"):
                    if fb[o]: blocks[o] = fb[o]
                notes.update(fn)
            except Exception as e:
                notes["mort"] = notes["hosp"] = "observed-data fit failed: %s" % e
        entry = {}
        for o in ("mort", "hosp"):
            if blocks[o]: entry[o] = blocks[o]
            else:
                entry[o + "_note"] = notes.get(o) or (nc.get(city) if o == "mort" and nc.get(city) else
                                                       nc.get("hosp") if o == "hosp" else
                                                       "no observed data or published anchor for this city")
        out["cities"][city] = entry
    return out


def main():
    anchors_only = "--anchors-only" in sys.argv
    res = build(anchors_only=anchors_only)
    json.dump(res, open(cal.CAL_FILE, "w"), indent=1)
    print("wrote", cal.CAL_FILE)
    for c, e in res["cities"].items():
        for o in ("mort", "hosp"):
            b = e.get(o)
            print("%-10s %-4s %s" % (c, o, ("%s beta_a=%s rr@a=1: %s" % (b["basis"], b["beta_a"], b.get("rr_at_a1.0"))) if b
                                       else "default (%s)" % e.get(o + "_note")))

if __name__ == "__main__":
    main()
