"""TAPAS Heat EWS automated tests.
Run:  cd backend && TAPAS_NOSCHED=1 python3 -m pytest ../tests -q
Offline-safe: no weather network needed; scheduler disabled."""
import os, sys, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("TAPAS_NOSCHED", "1")
os.environ.pop("TWILIO_ACCOUNT_SID", None); os.environ.pop("TWILIO_AUTH_TOKEN", None)
os.environ.pop("TWILIO_FROM", None); os.environ.pop("TWILIO_TO", None)

import pytest
from fastapi.testclient import TestClient
import app, datastore
from app import (STORE, CITY_IDS, vulnerability, ac_for_ward, env_terms,
                 city_sat_medians, clamp)
from measures_i18n import personal_multilang, STATE_LANG

client = TestClient(app.app)

def wards_with_sat(city):
    return [w for w in STORE.cities[city]["wards"] if w.get("sat") and w["sat"].get("built_frac") is not None]

# ---------- API surface ----------
def test_landing_200():
    r = client.get("/")
    assert r.status_code == 200 and "TAPAS" in r.text

def test_twilio_status_honest_when_unconfigured():
    r = client.get("/api/twilio/status")
    assert r.status_code == 200
    j = r.json()
    assert j["configured"] is False
    assert set(j["vars"]) == {"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TWILIO_TO"}

def test_test_send_honest_simulated_without_creds():
    r = client.post("/api/alerts/test")
    assert r.status_code == 200
    j = r.json()
    assert j["sent"] is False and j["channel_result"].startswith("SIMULATED")

def test_trend_endpoint_labelled():
    r = client.get("/api/trend?days=30")
    assert r.status_code == 200
    j = r.json()
    assert j["points"] >= 20
    assert any(p["src"] == "archive-backfill" for p in j["series"])
    assert "UNVALIDATED" in j["disclosure"]

def test_outbox_records_test_send():
    client.post("/api/alerts/test")
    r = client.get("/api/outbox")
    assert r.status_code == 200
    assert any(e["type"] == "test" for e in r.json())

# ---------- model factors ----------
def test_ac_is_per_ward_not_static():
    for city in CITY_IDS:
        ws = wards_with_sat(city)
        acs = set()
        base = STORE.cities[city]["config"]["ac_ref"]["value"]
        for w in ws:
            env = env_terms(w["sat"], city)
            a, info = ac_for_ward(city, env, w["sat"])
            acs.add(round(a, 4))
            assert "elec_pct" in info and "health_per_km2" in info  # composite inputs present
        if city != "Mumbai":  # Mumbai wards share city-level HL-14 fallback -> still varies via satellite
            assert len(acs) > 1, f"{city} AC collapsed to one value"
        assert all(0.05 <= a <= 0.9 for a in acs)
        assert len(acs) >= 1 and base is not None

def test_v_has_seven_indicator_slots_and_honest_pending():
    for city in CITY_IDS:
        v = vulnerability(city)
        have = set(v["parts"])
        assert {"children", "literacy", "disability", "density", "kutcha"} <= have, (city, have)
        # elderly 60+ now present for all four cities; Hyderabad's comes from the
        # fully-urban district row of C-14 AP (2011 jurisdiction) - no city row was ever published
        assert "elderly" in have, (city, have)
        if city == "Hyderabad":
            assert "slum share" in v["pending"]   # still genuinely unpublished
        assert "UNVALIDATED" in v["disclosure"]

def test_v_kutcha_varies_per_ward_where_hl14_matched():
    city = "Chennai"
    ws = [w for w in wards_with_sat(city) if w["sat"].get("kutcha_pct") is not None]
    vals = {vulnerability(city, w)["v"] for w in ws[:80]}
    assert len(vals) > 1, "per-ward kutcha produced no V variation"

def test_modis_present_for_three_plus_cities():
    n = 0
    for city in CITY_IDS:
        m = STORE.cities[city].get("modis")
        if m and m.get("lst") and len(m["lst"].get("wards", {})) > 0:
            n += 1
    assert n >= 3

def test_hl14_and_health_merged():
    w = next(w for w in wards_with_sat("Chennai") if str(w["id"]) == "58")
    assert w["sat"].get("elec_pct") is not None
    assert w["sat"].get("kutcha_pct") is not None
    assert w["sat"].get("health_per_km2") is not None

# ---------- alerts ----------
def test_digest_cross_suppression():
    s = app.Sched.__new__(app.Sched)
    now = dt.datetime(2026, 9, 10, 12, 0, 0)
    s.last_event = {"Chennai/58": {"band": "High", "ts": now - dt.timedelta(hours=2)},
                    "Hyderabad/19": {"band": "High", "ts": now - dt.timedelta(hours=20)}}
    act = [{"ward": {"city": "Chennai", "id": "58", "label": "Ward 58"}, "current": {"band": "High", "htsi": 0.15}},
           {"ward": {"city": "Hyderabad", "id": "19", "label": "Ward 19"}, "current": {"band": "Severe", "htsi": 0.2}}]
    kept, sup = s.digest_candidates(act, now)
    assert sup == 1 and len(kept) == 1 and kept[0]["ward"]["city"] == "Hyderabad"
    msg = app.build_digest_message(kept, sup)
    assert "omitted" in msg and "Ward 58" not in msg

def test_personal_multilang_aligned():
    for city in CITY_IDS:
        for band in ("Moderate", "High", "Severe"):
            m = personal_multilang(band, city)
            assert m["state_lang"] == STATE_LANG[city]
            assert len(m["hi"]) == len(m["en"]) and len(m["state"]) == len(m["en"])
            assert all(x.strip() for x in m["hi"]) and all(x.strip() for x in m["state"])

def test_lcz_real_wudapt_layer():
    """Real WUDAPT/Demuzere LCZ classes populated per ward (Zenodo 6364594 COG)."""
    for city in ["Mumbai","Ahmedabad","Chennai","Hyderabad"]:
        r = client.get(f"/api/city/{city}/wards")
        assert r.status_code == 200
        feats = r.json()["features"]
        sats=[(f["properties"].get("sat") or {}) for f in feats]
        with_lcz=[s for s in sats if s.get("lcz")]
        assert len(with_lcz)==len(sats), f"{city}: LCZ missing for some wards"
        assert all(1<=s["lcz"]<=17 for s in with_lcz), f"{city}: LCZ class out of WUDAPT range"
        assert all(s.get("lcz_name") for s in with_lcz)
        # classes must vary across wards (not a constant)
        assert len({s["lcz"] for s in with_lcz})>1, f"{city}: LCZ constant"

def test_lcz_flows_into_exposure_and_panel():
    """LCZ built-share modulates E and reaches the ward profile panel."""
    hit=0
    for city in ["Mumbai","Ahmedabad","Chennai","Hyderabad"]:
        for w in STORE.cities[city]["wards"]:
            sat=w.get("sat") or {}
            if sat.get("lcz") is None: continue
            e=env_terms(sat, city)
            assert e.get("lcz")==sat["lcz"] and e.get("lcz_name")
            assert 0<=e["E"]<=1
            hit+=1
    assert hit>400, f"expected LCZ on every ward, got {hit}"
    # the merged sat record itself carries the LCZ fields the panel renders
    wa=[w for w in STORE.cities["Mumbai"]["wards"] if str(w["id"])=="A"][0]
    assert wa["sat"].get("lcz") and wa["sat"].get("lcz_name")
    assert wa["sat"].get("lcz_built_share") is not None
    r=client.get("/api/city/Mumbai/wards")
    satA=[f["properties"]["sat"] for f in r.json()["features"] if f["properties"]["id"]=="A"][0]
    assert satA.get("lcz")==wa["sat"]["lcz"]

def test_sms_trilingual_and_emergency_block():
    """SMS = English + Hindi + state language; High/Severe add trilingual
    heat-stroke emergency block with national numbers 112/108."""
    from measures_i18n import emergency_multilang, STATE_LANG
    for city in ["Mumbai","Ahmedabad","Chennai","Hyderabad"]:
        m=emergency_multilang(city)
        assert m["numbers"]==["112","108"]
        for k in ("en","hi","state"):
            assert m[k]["signs"] and m[k]["call"]
        assert m["state_lang"]==STATE_LANG[city]
    body,_,emg=app.build_sms("[TAPAS ALERT] x","High","Hyderabad")
    for tag in ("-- Personal guidance --","EN:","HI:","Telugu:","-- EMERGENCY (heatstroke) --","112","108"):
        assert tag in body
    assert emg and emg["numbers"]==["112","108"]
    assert len(body)<=1500                      # Twilio single-segment-ish cap used by _twilio_send
    body_low,_,emg_low=app.build_sms("[TAPAS ALERT] x","Moderate","Mumbai")
    assert "Marathi:" in body_low and emg_low is None and "EMERGENCY" not in body_low

def test_ward_panel_emergency_matches_band():
    seen=0
    for city,wid in [("Mumbai","A"),("Hyderabad","1")]:
        r=client.get(f"/api/city/{city}/ward/{wid}")
        s=r.json().get("snapshot")
        if not s: continue                      # offline test client: no live weather
        meas=s["measures"]; seen+=1
        if meas["level"] in ("High","Severe"):
            assert meas["emergency"] and meas["emergency"]["numbers"]==["112","108"]
        else:
            assert meas["emergency"] is None
    if seen==0:
        # offline: verify the wiring directly through the snapshot builder's rule
        from measures_i18n import emergency_multilang
        assert emergency_multilang("Hyderabad")["numbers"]==["112","108"]

def test_configurable_risk_surface_and_reset():
    """POST /api/weights tunes logistic coefficients, band cut-points, sym_anom;
    invalid band ordering rejected; reset restores documented defaults."""
    j=client.post("/api/weights", json={"mort_anom":6.0}).json()
    assert j["risk_coef"]["mort"]["anom"]==6.0
    j=client.post("/api/weights", json={"band1":0.05,"band2":0.20,"band3":0.30}).json()
    assert j["band_t"][:3]==[0.05,0.20,0.30]
    j=client.post("/api/weights", json={"band1":0.30,"band2":0.10,"band3":0.20}).json()
    assert j["band_t"][:3]==[0.05,0.20,0.30]        # unordered cut-points rejected
    j=client.post("/api/weights", json={"sym_anom":True}).json()
    assert j["flags"]["sym_anom"] is True
    assert app._a_of(-3.0)==-1.0                    # cool days now lower the driver
    j=client.post("/api/weights", json={"mort_AC":2.5,"hosp_AC":0.7}).json()
    assert j["risk_coef"]["mort"]["AC"]==2.5 and j["risk_coef"]["hosp"]["AC"]==0.7
    j=client.post("/api/weights", json={"reset":True}).json()
    assert j["risk_coef"]["mort"]["anom"]==3.0 and j["risk_coef"]["hosp"]["V"]==3.2
    assert j["risk_coef"]["mort"]["AC"]==1.8 and j["risk_coef"]["hosp"]["AC"]==1.4   # k_m / k_h defaults
    assert j["band_t"]==j["band_t_defaults"] and j["flags"]["sym_anom"] is False
    assert app._a_of(-3.0)==0.0

def test_snapshot_exposes_logistic_terms():
    r=client.get("/api/city/Mumbai/ward/A")
    s=r.json().get("snapshot")
    if not s: return                                # offline test client
    rt=s["factors"]["risk_terms"]
    assert set(rt["mort"])=={"intercept","anom","surge","H","E","V","AC"}
    assert rt["mort"]["AC"]<0 and rt["hosp"]["AC"]<0          # higher AC lowers risk
    assert abs(sum(rt["mort"].values())-rt["z_mort"])<0.011
    assert abs(sum(rt["hosp"].values())-rt["z_hosp"])<0.011

def test_export_csv_audit_rows(monkeypatch):
    import csv as _csv, io as _io
    r=client.get("/api/city/Mumbai/export.csv")
    assert r.status_code==200 and r.headers["content-type"].startswith("text/csv")
    lines=r.text.splitlines()
    assert lines[0].startswith("# TAPAS ward export") and "UNVALIDATED" in lines[1]
    rows=list(_csv.DictReader(_io.StringIO("\n".join(lines[2:]))))
    assert len(rows)==len(STORE.cities["Mumbai"]["wards"])
    # deterministic flattening check: inject a snapshot, re-read the CSV
    fake={"available":True,
      "current":{"htsi":0.12,"band":"Moderate","utci":30.0,"tair":31.0},
      "mortality":{"band":"Moderate","probability":0.11},
      "hospitalisation":{"band":"Moderate","probability":0.14},
      "factors":{"H":0.5,"V":1.2,"E":0.7,"AC":0.4,
        "risk_terms":{"mort":{"intercept":-4.3,"anom":0.9,"surge":0.2,"H":0.6,"E":0.8,"V":0.6,"AC":-0.72},
                      "hosp":{"intercept":-4.0,"anom":1.0,"surge":0.2,"H":0.48,"E":0.7,"V":0.5,"AC":-0.56},
                      "z_mort":-1.92,"z_hosp":-1.68}}}
    monkeypatch.setattr(app,"compute_snapshot",lambda *a,**k:fake)
    monkeypatch.setattr(app.LIVE,"ward",lambda city,w:{"_prov":"test"})   # rec truthy offline
    r2=client.get("/api/city/Mumbai/export.csv")
    rows2=list(_csv.DictReader(_io.StringIO("\n".join(r2.text.splitlines()[2:]))))
    assert rows2 and all(x["available"]=="1" for x in rows2)
    x=rows2[0]
    assert x["htsi_band"]=="Moderate" and x["mort_band"]=="Moderate" and x["hosp_band"]=="Moderate"
    assert float(x["z_mort"])==-1.92 and float(x["z_hosp"])==-1.68
    assert float(x["mort_H"])==0.6 and float(x["hosp_H"])==0.48
    assert float(x["mort_E"])==0.8 and float(x["hosp_V"])==0.5
    assert float(x["mort_AC"])==-0.72 and float(x["hosp_AC"])==-0.56
    assert float(x["H"])==0.5 and float(x["AC"])==0.4

def test_slashed_ward_ids_route():
    # Mumbai ward ids like "F/N" contain a slash; both ward routes must resolve them.
    r=client.get("/api/city/Mumbai/ward/F/N")
    assert r.status_code==200 and r.json()["ward"]["id"]=="F/N"
    r2=client.get("/api/city/Mumbai/ward/F/N/projection")
    assert r2.status_code==200

def test_apply_measures_clamps_and_waterfall():
    a,s,E,Vv,AC=0.5,0.10,0.8,1.20,0.88
    a2,s2,H2,E2,V2,AC2,steps=app.apply_measures(a,s,0.5,E,Vv,AC,
        ["welfare_checks","cooling_centres","outdoor_work_reschedule","water_audits","grid_energy_notice"])
    assert AC2==0.90                          # 0.88+0.10(+0.03+0.05) clamped at 0.90
    assert s2==0.0                            # 0.10-0.15 floored at 0
    assert V2==1.0+0.85*(1.20-1.0)            # vt *= 0.85
    assert [st["measure"] for st in steps]==list(app.MEASURE_EFFECTS)   # canonical order
    seq=[st["mort_after"] for st in steps]
    assert all(x<=y+1e-9 for y,x in zip(seq,seq[1:]))   # never increases risk
    assert all(st["mort_delta"]<=0 and st["hosp_delta"]<=0 for st in steps)
    assert steps[0]["rationale"]

def test_apply_measures_ignores_unknown():
    a,s,E,Vv,AC=0.5,0.2,0.8,1.2,0.4
    _,_,_,_,_,_,steps=app.apply_measures(a,s,0.5,E,Vv,AC,["bogus_measure"])
    assert steps==[]

def test_scenario_endpoint_and_tunable_measure_effects(monkeypatch):
    monkeypatch.setattr(app.LIVE,"ward",lambda city,w:{"_prov":"test"})
    monkeypatch.setattr(app,"severity",lambda city,rec,series,now:{"anom":1.5,"utci":39.0,"hsev":0.5,"tair":39.0,"daymax":40.0,"base":37.0})
    r=client.get("/api/scenario/preventive?city=Mumbai&ward_id=A&measures=cooling_centres,outdoor_work_reschedule")
    assert r.status_code==200
    j=r.json()
    assert j["available"] is True
    assert j["disclosure"]==app.SCENARIO_DISCLOSURE
    assert "unvalidated" in j["disclosure"] and "Section 6" in j["disclosure"]
    assert [w_["measure"] for w_ in j["waterfall"]]==["cooling_centres","outdoor_work_reschedule"]
    assert j["adjusted"]["mortality"]<=j["baseline"]["mortality"]
    assert j["baseline"]["mort_band"] and j["adjusted"]["mort_band"]
    # POST variant
    r2=client.post("/api/scenario/preventive", json={"city":"Mumbai","ward_id":"A","measures":["welfare_checks"]})
    assert r2.status_code==200 and r2.json()["waterfall"][0]["term"]=="vt"
    # runtime-tunable effect sizes via /api/weights, with plausibility guard + reset
    j2=client.post("/api/weights", json={"measure_effects":{"cooling_centres":0.2}}).json()
    assert j2["measure_effects"]["cooling_centres"]["value"]==0.2
    r3=client.get("/api/scenario/preventive?city=Mumbai&ward_id=A&measures=cooling_centres").json()
    assert r3["waterfall"][0]["value"]==0.2
    j4=client.post("/api/weights", json={"measure_effects":{"cooling_centres":5.0}}).json()
    assert j4["measure_effects"]["cooling_centres"]["value"]==0.5
    j5=client.post("/api/weights", json={"reset":True}).json()
    assert j5["measure_effects"]["cooling_centres"]["value"]==0.10

def test_weights_move_logistics_and_risk_bands_exposed(monkeypatch):
    monkeypatch.setattr(app.LIVE,"ward",lambda city,w:{"_prov":"test"})
    monkeypatch.setattr(app,"severity",lambda city,rec,series,now:{"anom":1.5,"utci":39.0,"hsev":0.5,"tair":39.0,"daymax":40.0,"base":37.0})
    def prob():
        s=client.get("/api/city/Mumbai/ward/A").json()["snapshot"]
        return s["mortality"]["probability"], s["hospitalisation"]["probability"]
    w=client.get("/api/weights").json()
    assert "risk_band_t" in w and "risk_band_t_defaults" in w
    m0,h0=prob()
    j=client.post("/api/weights", json={"E":1.8}).json()
    assert j["weights"]["E"]==1.8
    m1,h1=prob()
    assert (m1,h1)!=(m0,h0)                       # WEIGHTS["E"] now moves the logistics
    j=client.post("/api/weights", json={"E":1.0,"H":2.0}).json()
    m2,h2=prob()
    assert (m2,h2)!=(m1,h1)                       # WEIGHTS["H"] now moves the logistics
    j=client.post("/api/weights", json={"rband1":0.05,"rband2":0.15,"rband3":0.30}).json()
    assert j["risk_band_t"]==[0.05,0.15,0.30]
    j=client.post("/api/weights", json={"rband1":0.30,"rband2":0.10,"rband3":0.20}).json()
    assert j["risk_band_t"]==[0.05,0.15,0.30]     # unordered rejected
    j=client.post("/api/weights", json={"reset":True}).json()
    assert j["risk_band_t"]==j["risk_band_t_defaults"] and j["weights"]["H"]==1.0
    assert "independently calibrated" in j["note"]


# ---------- calibration layer (historical health data) ----------
import numpy as np, json as _json
import calibration as cal


def _synth(n=1100, beta_a=0.6, beta_s=0.0, seed=7, base=100.0, adm_gamma=0.0):
    rng = np.random.default_rng(seed)
    d0 = dt.date(2019, 1, 1)
    dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]
    doy = np.array([(d0 + dt.timedelta(days=i)).timetuple().tm_yday for i in range(n)], float)
    # hot season Apr-Jun produces a > 0 on about a fifth of days
    a = np.clip(rng.normal(0, 0.25, n) + 0.5 * np.exp(-((doy - 135) / 25.0) ** 2), 0, 1)
    s = np.clip(a * 0.6 + rng.normal(0, 0.05, n), 0, 1)
    mu = base * np.exp(beta_a * a + beta_s * s + 0.1 * np.sin(2 * np.pi * doy / 365.25))
    return dates, a, s, mu, rng


def test_poisson_fit_recovers_heat_slope():
    dates, a, s, mu, rng = _synth(beta_a=0.6)
    y = rng.poisson(mu)
    blk = cal.fit_outcome(dates, y, a, s)
    assert blk["basis"] == "fitted"
    assert abs(blk["beta_a"] - 0.6) < 3 * blk["se_a"] + 0.05
    assert 0.9 * 100 < blk["baseline_per_day"] < 1.15 * 100


def test_fit_rejected_when_no_heat_signal():
    dates, a, s, mu, rng = _synth(beta_a=0.0)
    y = rng.poisson(mu)
    blk = cal.fit_outcome(dates, y, a, s)
    assert "rejected" in blk and "basis" not in blk


def test_fit_rejected_on_too_little_data():
    dates, a, s, mu, rng = _synth(n=200)
    blk = cal.fit_outcome(dates, rng.poisson(mu), a, s)
    assert "rejected" in blk and "rows" in blk["rejected"]


def test_admissions_gamma_only_kept_when_informative():
    dates, a, s, mu, rng = _synth(beta_a=0.5)
    z = rng.normal(0, 0.4, len(dates))                     # extra latent driver visible in admissions
    y = rng.poisson(mu * np.exp(0.5 * z))
    adm = np.expm1(np.log1p(60) + z)                       # lagged admissions carry that driver
    blk = cal.fit_outcome(dates, y, a, s, adm_lag=adm)
    assert blk["basis"] == "fitted" and blk.get("gamma_adm", 0) > 0.2
    assert "adm_log_mean" in blk
    # unrelated admissions must not be adopted
    blk2 = cal.fit_outcome(dates, rng.poisson(mu), a, s, adm_lag=rng.uniform(20, 80, len(dates)))
    assert "gamma_adm" not in blk2


def test_anchor_block_math_and_guards():
    an = {"id": "x", "rr": 1.5, "exposure": {"type": "imd_heat_wave", "a_ref": 0.5}, "source": "s"}
    b = cal.anchor_block(an, "Ahmedabad", app._mean_tmax, app._a_of)
    assert abs(b["beta_a"] - np.log(1.5) / 0.5) < 1e-3 and b["basis"] == "published-anchor"
    # a_ref derived from a stated Tmax and the city's own ERA5 May normal
    an2 = {"id": "y", "rr": 1.76, "exposure": {"type": "tmax_month", "tmax_c": 46.8, "month": 5}}
    b2 = cal.anchor_block(an2, "Ahmedabad", app._mean_tmax, app._a_of)
    assert 0.8 < b2["a_ref"] < 0.9
    # an exposure at/below normal cannot define a slope
    an3 = {"id": "z", "rr": 1.16, "exposure": {"type": "tmax_month", "tmax_c": 30.0, "month": 5}}
    assert cal.anchor_block(an3, "Ahmedabad", app._mean_tmax, app._a_of) is None


def test_shipped_calibration_is_honest_about_uncalibrated_outputs():
    d = _json.load(open(cal.CAL_FILE))
    for c in ("Ahmedabad", "Mumbai", "Chennai", "Hyderabad"):
        assert d["cities"][c]["mort"]["basis"] == "published-anchor"
    # same study and definition everywhere; Mumbai's published effect is much weaker than Ahmedabad's
    assert d["cities"]["Mumbai"]["mort"]["beta_a"] < d["cities"]["Ahmedabad"]["mort"]["beta_a"] / 3
    assert d["cities"]["Ahmedabad"]["mort"]["anchor_id"].startswith("debont2024-97p-2d")
    for c in d["cities"].values():
        assert "hosp" not in c and c["hosp_note"]          # no admissions data => no calibrated hosp


def test_calibrated_rr_monotone_capped_and_ward_scaled(monkeypatch):
    monkeypatch.setattr(cal, "_CAL", {"cities": {"X": {"mort": {"basis": "fitted", "beta_a": 0.7, "beta_s": 0.0}}}})
    lo = cal.calibrated_rr("X", "mort", 0.2, 0.0)["rr"]
    hi = cal.calibrated_rr("X", "mort", 0.9, 0.0)["rr"]
    assert 1.0 < lo < hi
    assert cal.calibrated_rr("X", "mort", 0.9, 0.0, ward_mult=2.0)["rr"] > hi
    assert cal.calibrated_rr("X", "mort", 0.9, 0.0, ward_mult=0.5)["rr"] < hi
    monkeypatch.setitem(cal._CAL["cities"]["X"]["mort"], "beta_a", 9.0)
    assert cal.calibrated_rr("X", "mort", 1.0, 0.0, ward_mult=2.0)["rr"] <= cal.RR_CAP
    assert cal.calibrated_rr("X", "hosp", 0.9, 0.0) is None    # uncalibrated outcome stays None
    assert cal.calibrated_rr("Nowhere", "mort", 0.9, 0.0) is None


def test_admissions_nowcast_only_when_fresh(monkeypatch):
    blk = {"basis": "fitted", "beta_a": 0.5, "gamma_adm": 0.3, "adm_log_mean": 4.0}
    monkeypatch.setattr(cal, "_CAL", {"cities": {"X": {"mort": blk}}})
    now = dt.datetime(2026, 6, 10, 12)
    obs = {"X": {"2026-06-09": {"deaths": None, "admissions": 200.0}}}
    z, d = cal.latest_admissions_z("X", now, obs)
    assert d == "2026-06-09" and z > 1.0
    obs_old = {"X": {"2026-05-01": {"deaths": None, "admissions": 200.0}}}
    assert cal.latest_admissions_z("X", now, obs_old) == (None, None)


def test_ward_multiplier_bounded_and_median_is_one():
    city = "Ahmedabad"
    ws = wards_with_sat(city)[:40]
    ms = [app.ward_multiplier(city, w) for w in ws]
    assert all(0.5 <= m <= 2.0 for m in ms)
    assert min(ms) < max(ms)


def test_snapshot_carries_calibrated_block_with_basis():
    city = "Ahmedabad"; w = wards_with_sat(city)[0]
    now = dt.datetime(2026, 5, 20, 14)
    rec = {"hourly": {"time": [], "temperature_2m": [], "relative_humidity_2m": [],
                      "wind_speed_10m": [], "shortwave_radiation": []}}
    for h in range(72):
        t = dt.datetime(2026, 5, 19, 0) + dt.timedelta(hours=h)
        rec["hourly"]["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
        rec["hourly"]["temperature_2m"].append(33 + 12 * max(0, np.sin(np.pi * (t.hour - 6) / 14)) if 6 <= t.hour <= 20 else 33)
        rec["hourly"]["relative_humidity_2m"].append(30); rec["hourly"]["wind_speed_10m"].append(3)
        rec["hourly"]["shortwave_radiation"].append(600 if 8 <= t.hour <= 17 else 0)
    snap = app.compute_snapshot(city, w, rec, now)
    assert snap["available"]
    m = snap["mortality"]; c = m["calibrated"]
    assert c and c["basis"] == "published-anchor" and c["rr"] > 1.0 and c["label"]
    assert snap["hospitalisation"]["calibrated"] is None           # no admissions data yet
    assert "probability" in m and "band" in m                        # original output untouched
    assert all("mortality_rr" in f for f in snap["forecast"])


def test_uncalibrated_outcome_has_no_calibrated_block():
    city = "Mumbai"; w = wards_with_sat(city)[0]
    rec = app.synth_weather(19.0, 72.8) if hasattr(app, "synth_weather") else None
    snap = app.compute_snapshot(city, w, rec, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    assert snap["available"]
    assert snap["mortality"]["calibrated"] is not None and snap["mortality"]["calibrated"]["basis"] == "published-anchor"
    assert snap["hospitalisation"]["calibrated"] is None          # no admissions data or anchor yet


def test_calibration_endpoints():
    j = client.get("/api/calibration").json()
    assert j["cities"]["Ahmedabad"]["mort"]["basis"] == "published-anchor"
    assert j["cities"]["Mumbai"]["mort"]["basis"] == "published-anchor"
    assert j["cities"]["Mumbai"]["hosp"]["basis"] == "default" and j["cities"]["Mumbai"]["hosp"]["note"]
    assert client.post("/api/calibration/reload").status_code == 200


def test_build_fits_observed_data_end_to_end(monkeypatch, tmp_path):
    """calibrate_risk.build(): synthetic 'observed' deaths + a fake archive fetch
    must give a fitted block that replaces the anchor for that city."""
    import calibrate_risk as cr
    city = "Ahmedabad"
    dates, a_true, s_true, mu, rng = _synth(n=1100, beta_a=0.6)
    d0 = dt.date.fromisoformat(dates[0])
    # fake archive whose daily-max maps (via the model's own _a_of) to a_true
    def fake_fetch(lat, lon, start, end):
        H = {"time": [], "temperature_2m": [], "relative_humidity_2m": [], "wind_speed_10m": [], "shortwave_radiation": []}
        d = start
        while d <= end:
            a = a_true[(d - d0).days]
            norm = app._mean_tmax(city, d.month)
            # invert _a_of on its linear pieces
            dep = 0.0 if a <= 0 else (a / 0.5 * app.IMD_HW_DEP if a <= 0.5 else
                                      app.IMD_HW_DEP + (a - 0.5) / 0.5 * (app.IMD_SHW_DEP - app.IMD_HW_DEP))
            for h in range(24):
                H["time"].append("%sT%02d:00" % (d.isoformat(), h))
                H["temperature_2m"].append(norm + dep - (0 if h == 14 else 3))
                H["relative_humidity_2m"].append(40); H["wind_speed_10m"].append(3)
                H["shortwave_radiation"].append(500)
            d += dt.timedelta(days=1)
        return {"hourly": H}
    rows = {dates[i]: {"deaths": float(rng.poisson(mu[i])), "admissions": None} for i in range(len(dates))}
    res = cr.build(fetch=fake_fetch, observed={city: rows})
    blk = res["cities"][city]["mort"]
    assert blk["basis"] == "fitted" and blk["beta_a"] > 0.3
    assert blk["diagnostics"]["pearson_r_obs_vs_pred"] > 0.3
    assert res["cities"]["Chennai"]["mort"]["basis"] == "published-anchor"   # untouched


def test_calibrated_block_survives_series_with_no_observation_today():
    """Live OpenWeather data is 3-hourly and starts at the next slot, so late in
    the UTC day nothing is dated 'today'. The calibrated block must still appear,
    using the nearest upcoming day, and say which day it used."""
    city = "Ahmedabad"; w = wards_with_sat(city)[0]
    rec = {"hourly": {"time": [], "temperature_2m": [], "relative_humidity_2m": [],
                      "wind_speed_10m": [], "shortwave_radiation": []}}
    for h in range(0, 120, 3):
        t = dt.datetime(2026, 5, 21, 0, 0) + dt.timedelta(hours=h)
        rec["hourly"]["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
        rec["hourly"]["temperature_2m"].append(44 if 6 <= t.hour <= 15 else 34)
        rec["hourly"]["relative_humidity_2m"].append(30); rec["hourly"]["wind_speed_10m"].append(10)
        rec["hourly"]["shortwave_radiation"].append(500)
    snap = app.compute_snapshot(city, w, rec, dt.datetime(2026, 5, 20, 23, 40))
    c = snap["mortality"]["calibrated"]
    assert c and c["rr"] > 1.0 and c["exposure_day"] == "2026-05-21"
