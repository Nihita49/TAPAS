"""Calibration layer: ties TAPAS mortality / hospitalization risk to historical
health data instead of hand-set coefficients alone.

Three bases, in priority order (the app labels every output with the one used):

  fitted           Poisson count model fitted to observed daily deaths and/or
                   heat-illness admissions in data/observed/observed.csv
  published-anchor slope anchored to a published relative risk (see
                   data/observed/published_anchors.json); a prior, not a fit
  default          the original hand-set logistic (unvalidated)

The calibrated output is a RELATIVE RISK versus a normal day for the city's
typical ward, RR = exp(beta_a * a + beta_s * surge [+ gamma * z_adm]), where
`a` is the IMD-scaled anomaly driver and `surge` the UTCI surge term the model
already uses. Ward differences scale the excess (RR - 1) by the ward's
V x E x (1 - AC) multiplier relative to the city median. City data can
calibrate the level; it cannot validate the ward-to-ward differences.

Files:
  data/observed/observed.csv            city,date,deaths,admissions,source
  data/observed/published_anchors.json  published relative risks used as priors
  data/calibration.json                 output of calibrate_risk.py, read at runtime

Pure numpy + stdlib. Nothing here imports the web app (no circular imports).
"""
import os, json, csv, math, datetime as dt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OBS_DIR = os.path.join(ROOT, "data", "observed")
OBS_CSV = os.path.join(OBS_DIR, "observed.csv")
ANCHORS = os.path.join(OBS_DIR, "published_anchors.json")
CAL_FILE = os.path.join(ROOT, "data", "calibration.json")

MIN_ROWS = 365            # at least a year of daily rows to attempt a fit
MIN_HEAT_DAYS = 15        # and enough hot days to identify a heat slope
HEAT_A = 0.25             # a >= this counts as a hot day for the check above
RR_CAP = 4.0              # runtime cap on RR: never extrapolate past this
ADM_MAX_AGE_DAYS = 2      # admissions feed older than this is not used

_CAL = {"cities": {}}


# ------------------------------------------------------------------ loading
def load_calibration():
    """(Re)load data/calibration.json into memory. Missing file => no calibration."""
    global _CAL
    try:
        d = json.load(open(CAL_FILE))
        _CAL = d if isinstance(d.get("cities"), dict) else {"cities": {}}
    except Exception:
        _CAL = {"cities": {}}
    return _CAL


def status():
    return _CAL


def city_cal(city, outcome):
    """Calibration block for (city, 'mort'|'hosp') or None."""
    return ((_CAL.get("cities") or {}).get(city) or {}).get(outcome)


def read_observed(path=OBS_CSV):
    """-> {city: {date(YYYY-MM-DD): {'deaths': float|None, 'admissions': float|None}}}"""
    out = {}
    try:
        with open(path, newline="") as f:
            for r in csv.DictReader(l for l in f if not l.lstrip().startswith("#")):
                c = (r.get("city") or "").strip(); d = (r.get("date") or "").strip()
                if not c or not d: continue
                try: dt.date.fromisoformat(d)
                except Exception: continue
                def num(k):
                    v = (r.get(k) or "").strip()
                    try: return float(v) if v != "" else None
                    except Exception: return None
                out.setdefault(c, {})[d] = {"deaths": num("deaths"), "admissions": num("admissions")}
    except FileNotFoundError:
        pass
    return out


_OBS_CACHE = {"mtime": None, "data": {}}
def read_observed_cached():
    """read_observed() re-parsed only when observed.csv changes on disk."""
    try: m = os.path.getmtime(OBS_CSV)
    except OSError: m = None
    if _OBS_CACHE["mtime"] != m:
        _OBS_CACHE["data"] = read_observed(); _OBS_CACHE["mtime"] = m
    return _OBS_CACHE["data"]


# ------------------------------------------------------------------ Poisson GLM
def _design(dates, a, s, extra=None):
    """Design matrix: intercept, a, surge, [extra], day-of-week dummies,
    two annual harmonics, linear trend. Returns (X, names)."""
    n = len(dates)
    ds = [dt.date.fromisoformat(d) for d in dates]
    doy = np.array([d.timetuple().tm_yday for d in ds], float)
    dow = np.array([d.weekday() for d in ds])
    t = (np.array([d.toordinal() for d in ds], float) - ds[0].toordinal()) / 3652.5
    cols = [np.ones(n), np.asarray(a, float), np.asarray(s, float)]
    names = ["intercept", "a", "surge"]
    if extra is not None:
        cols.append(np.asarray(extra, float)); names.append("adm_z")
    for k in range(1, 7):
        cols.append((dow == k).astype(float)); names.append("dow%d" % k)
    for h in (1, 2):
        cols.append(np.sin(2 * math.pi * h * doy / 365.25)); names.append("sin%d" % h)
        cols.append(np.cos(2 * math.pi * h * doy / 365.25)); names.append("cos%d" % h)
    cols.append(t); names.append("trend")
    return np.column_stack(cols), names


def poisson_glm(X, y, iters=60, ridge=1e-8):
    """Poisson regression (log link) by IRLS. Returns (beta, se, phi) with
    quasi-Poisson standard errors (SE scaled by sqrt of Pearson dispersion)."""
    y = np.asarray(y, float)
    n, p = X.shape
    beta = np.zeros(p); beta[0] = math.log(max(y.mean(), 1e-6))
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 30); mu = np.exp(eta)
        z = eta + (y - mu) / mu
        W = mu
        XtW = X.T * W
        A = XtW @ X + ridge * np.eye(p)
        new = np.linalg.solve(A, XtW @ z)
        if np.max(np.abs(new - beta)) < 1e-9:
            beta = new; break
        beta = new
    mu = np.exp(np.clip(X @ beta, -30, 30))
    phi = max(1.0, float(np.sum((y - mu) ** 2 / mu) / max(1, n - p)))
    cov = np.linalg.inv((X.T * mu) @ X + ridge * np.eye(p)) * phi
    return beta, np.sqrt(np.diag(cov)), phi


def _loglik(X, y, beta):
    mu = np.exp(np.clip(X @ beta, -30, 30))
    return float(np.sum(y * np.log(mu) - mu))


def fit_outcome(dates, y, a, s, adm_lag=None):
    """Fit one outcome for one city. `dates`, `y`, `a`, `s` are aligned daily
    arrays (rows with missing y or features must already be dropped).
    `adm_lag` (optional): previous-day admissions aligned to the same rows.
    Returns a calibration block, or {'rejected': reason} if the fit is not
    supportable. A fit is only accepted when the heat slope is positive and
    significant (one-sided 95%)."""
    n = len(dates)
    a = np.asarray(a, float); s = np.asarray(s, float); y = np.asarray(y, float)
    if n < MIN_ROWS:
        return {"rejected": "only %d daily rows (need >= %d)" % (n, MIN_ROWS)}
    heat = int(np.sum(a >= HEAT_A))
    if heat < MIN_HEAT_DAYS:
        return {"rejected": "only %d hot days with a >= %.2f (need >= %d)" % (heat, HEAT_A, MIN_HEAT_DAYS)}
    extra = None; adm_mean = None
    if adm_lag is not None:
        la = np.log1p(np.asarray(adm_lag, float)); adm_mean = float(la.mean()); extra = la - adm_mean
    X, names = _design(dates, a, s, extra)
    beta, se, phi = poisson_glm(X, y)
    ia, is_ = names.index("a"), names.index("surge")
    z_a = beta[ia] / se[ia] if se[ia] > 0 else 0.0
    if not (beta[ia] > 0 and z_a > 1.645):
        return {"rejected": "no significant positive heat effect (beta_a=%.3f, z=%.2f)" % (beta[ia], z_a)}
    blk = {
        "basis": "fitted",
        "beta_a": round(float(beta[ia]), 4), "se_a": round(float(se[ia]), 4),
        "beta_s": round(float(max(beta[is_], 0.0)), 4), "se_s": round(float(se[is_]), 4),
        "surge_kept": bool(beta[is_] > 0 and beta[is_] / se[is_] > 1.645) if se[is_] > 0 else False,
        "dispersion": round(phi, 3), "n_days": int(n), "n_hot_days": heat,
        "period": [dates[0], dates[-1]],
    }
    if not blk["surge_kept"]: blk["beta_s"] = 0.0
    if extra is not None:
        ig = names.index("adm_z")
        zg = beta[ig] / se[ig] if se[ig] > 0 else 0.0
        if beta[ig] > 0 and zg > 1.645:
            blk["gamma_adm"] = round(float(beta[ig]), 4); blk["se_gamma"] = round(float(se[ig]), 4)
            blk["adm_log_mean"] = round(adm_mean, 4)
    # baseline (a = surge = 0) mean daily count over the fitted period
    X0 = X.copy(); X0[:, ia] = 0.0; X0[:, is_] = 0.0
    if extra is not None: X0[:, names.index("adm_z")] = 0.0
    blk["baseline_per_day"] = round(float(np.mean(np.exp(np.clip(X0 @ beta, -30, 30)))), 2)
    # fit diagnostics for the validation report
    mu = np.exp(np.clip(X @ beta, -30, 30))
    blk["diagnostics"] = {
        "pearson_r_obs_vs_pred": round(float(np.corrcoef(y, mu)[0, 1]), 3),
        "mean_obs": round(float(y.mean()), 2), "mean_pred": round(float(mu.mean()), 2),
        "rr_at_a0.5": round(math.exp(blk["beta_a"] * 0.5), 3),
        "rr_at_a1.0": round(math.exp(blk["beta_a"] + blk["beta_s"]), 3),
    }
    return blk


def lag_scan(dates, y, a, s, max_lag=3):
    """Informational: AIC for exposure lagged 0..max_lag days (moving the
    exposure series back). The runtime model uses lag 0; this shows whether a
    lagged version would fit better. Returns {lag: aic}."""
    out = {}
    ds = list(dates); y = np.asarray(y, float); a = np.asarray(a, float); s = np.asarray(s, float)
    for L in range(0, max_lag + 1):
        if len(ds) - L < MIN_ROWS: break
        d2 = ds[L:]; y2 = y[L:]
        a2 = a[:len(a) - L] if L else a; s2 = s[:len(s) - L] if L else s
        X, _ = _design(d2, a2, s2)
        try:
            b, _, _ = poisson_glm(X, y2)
            out[L] = round(2 * X.shape[1] - 2 * _loglik(X, y2, b), 1)
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ anchors
def load_anchors(path=ANCHORS):
    try:
        return json.load(open(path))
    except Exception:
        return {"anchors": []}


def anchor_block(anchor, city, mean_tmax_fn, a_of_fn):
    """Turn one published anchor into a calibration block for `city`.
    beta_a = ln(RR) / a_ref, where a_ref is either given explicitly or derived
    from a stated absolute Tmax and month via the city's own ERA5 normal."""
    rr = float(anchor["rr"]); ex = anchor.get("exposure") or {}
    if ex.get("type") == "tmax_month":
        norm = mean_tmax_fn(city, int(ex["month"]))
        if norm is None: return None
        a_ref = a_of_fn(float(ex["tmax_c"]) - norm)
    else:
        a_ref = float(ex.get("a_ref", 0.5))
    if a_ref is None or a_ref <= 0.05 or rr <= 1.0:
        return None
    blk = {
        "basis": "published-anchor", "anchor_id": anchor.get("id"),
        "beta_a": round(math.log(rr) / a_ref, 4), "beta_s": 0.0,
        "a_ref": round(a_ref, 3), "rr_ref": rr, "rr_ci": anchor.get("rr_ci"),
        "source": anchor.get("source"), "caveat": anchor.get("caveat"),
        "rr_at_a0.5": round(math.exp(math.log(rr) / a_ref * 0.5), 3),
        "rr_at_a1.0": round(math.exp(math.log(rr) / a_ref), 3),
    }
    if anchor.get("baseline_deaths_per_day") is not None:
        blk["baseline_per_day"] = anchor["baseline_deaths_per_day"]
        blk["baseline_note"] = anchor.get("baseline_note")
    return blk


# ------------------------------------------------------------------ runtime
def latest_admissions_z(city, now, obs=None):
    """z_adm for the mortality nowcast: log1p(latest admissions) minus the
    fitted centring constant, only when a fitted gamma exists and the newest
    admissions row is at most ADM_MAX_AGE_DAYS old. -> (z, date) or (None, None)."""
    blk = city_cal(city, "mort")
    if not blk or "gamma_adm" not in blk: return None, None
    obs = obs if obs is not None else read_observed_cached()
    rows = obs.get(city) or {}
    days = sorted(d for d, r in rows.items() if r.get("admissions") is not None)
    if not days: return None, None
    last = days[-1]
    if (now.date() - dt.date.fromisoformat(last)).days > ADM_MAX_AGE_DAYS: return None, None
    return math.log1p(rows[last]["admissions"]) - blk["adm_log_mean"], last


def calibrated_rr(city, outcome, a, surge, ward_mult=1.0, adm_z=None):
    """Calibrated relative risk for a ward-day, or None when the city/outcome
    has no calibration (caller then shows the default output only).
    RR = 1 + (RR_city - 1) * ward_mult, capped at RR_CAP."""
    blk = city_cal(city, outcome)
    if not blk: return None
    z = blk["beta_a"] * a + blk.get("beta_s", 0.0) * surge
    used_adm = False
    if adm_z is not None and "gamma_adm" in blk:
        z += blk["gamma_adm"] * adm_z; used_adm = True
    rr_city = min(RR_CAP, math.exp(z))
    rr = 1.0 + (rr_city - 1.0) * ward_mult
    return {"rr": round(min(RR_CAP, rr), 3), "excess_pct": round((min(RR_CAP, rr) - 1.0) * 100.0, 1),
            "rr_city": round(rr_city, 3), "basis": blk["basis"], "admissions_used": used_adm}


def public_status():
    """Compact, API-safe summary per city / outcome."""
    out = {}
    obs = read_observed_cached()
    for c, d in (_CAL.get("cities") or {}).items():
        out[c] = {}
        for o in ("mort", "hosp"):
            b = d.get(o)
            if b: out[c][o] = dict(b)
            else: out[c][o] = {"basis": "default",
                               "note": d.get(o + "_note") or "no calibration data or anchor for this city"}
        rows = obs.get(c) or {}
        out[c]["observed_rows"] = {
            "deaths": sum(1 for r in rows.values() if r.get("deaths") is not None),
            "admissions": sum(1 for r in rows.values() if r.get("admissions") is not None)}
    return {"generated": _CAL.get("generated"), "cities": out, "notes": _CAL.get("notes", [])}


load_calibration()
