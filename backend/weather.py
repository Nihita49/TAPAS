"""Live meteorological ingestion from OpenWeatherMap's classic free Forecast
API (/data/2.5/forecast) -- the no-card-required tier (1,000,000 calls/month,
60/min), as opposed to the "One Call 3.0" product which requires billing
details on file. Trade-off: 3-hour-step data for 5 days ahead, rather than
true hourly for 6 days -- adequate for this app's nearest-hour lookups and
daily-max aggregation; just a coarser refresh cadence, not a functional loss.

Zero-placeholder rule: if a live call fails, the value is returned as
available=False (UI renders "Insufficient data") and the caller decides whether
a clearly-tagged demo-fallback series may be used for the demonstration run.

Provider note: this endpoint does not publish a shortwave/solar-radiation
field. That field is instead ESTIMATED here from a standard clear-sky
solar-position model (latitude + day-of-year + hour-of-day), scaled down by
OWM's reported cloud-cover %. This is a well-established meteorological
approximation technique, not a live measurement -- every record built this
way carries "_sw_estimated": True so callers/UI can be transparent about it,
consistent with this app's existing "defensible default, not validated"
labelling elsewhere.
"""
import datetime as dt
import math
import os
import requests

BASE = "https://api.openweathermap.org/data/2.5/forecast"
API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


# ------------------------------------------------------------ solar estimate
def _clear_sky_ghi(lat, lon, when_utc):
    """Rough clear-sky global horizontal irradiance (W/m^2) from solar
    geometry alone (no atmosphere/aerosol correction) -- adequate as a
    scaling reference; actual sky conditions are applied via cloud cover."""
    day_of_year = when_utc.timetuple().tm_yday
    # solar declination (degrees -> radians)
    decl = math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81))))
    lat_r = math.radians(lat)
    # approximate solar time using longitude (no equation-of-time correction;
    # small error, acceptable for a scaling estimate)
    solar_hour = (when_utc.hour + when_utc.minute / 60.0) + lon / 15.0
    hour_angle = math.radians(15.0 * (solar_hour - 12.0))
    cos_zenith = (math.sin(lat_r) * math.sin(decl) +
                  math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
    if cos_zenith <= 0:
        return 0.0
    solar_constant = 1361.0
    return max(0.0, solar_constant * cos_zenith * 0.75)  # 0.75 ~ typical clear-sky atmospheric transmittance


def _estimate_shortwave(lat, lon, when_utc, cloud_pct):
    """Clear-sky GHI attenuated by cloud cover (simple linear-ish model:
    heavy overcast cuts irradiance by ~75%, matching common empirical fits)."""
    ghi = _clear_sky_ghi(lat, lon, when_utc)
    cloud_frac = max(0.0, min(1.0, (cloud_pct or 0) / 100.0))
    return round(ghi * (1.0 - 0.75 * cloud_frac))


# ------------------------------------------------------------ fetch + adapt
def fetch_ward(lat, lon, forecast_days=6):
    """Return hourly+daily record for one location, or raise on failure.
    Shape matches the app's internal contract (see _series() in app.py):
      hourly: time[], temperature_2m[], relative_humidity_2m[],
              wind_speed_10m[] (km/h, to match prior Open-Meteo default unit),
              shortwave_radiation[] (ESTIMATED, see module docstring)
      daily:  temperature_2m_max[] (aggregated from the 3-hour steps below,
              one max per calendar date present in the forecast window --
              this endpoint has no separate daily-max field)

    Note: the classic /data/2.5/forecast endpoint returns 3-hour-step data
    for 5 days ahead (up to 40 entries), not true hourly for 6 days.
    `forecast_days` beyond what the API returns is simply capped by however
    much data comes back; callers should not assume exactly N days.
    """
    if not API_KEY:
        raise RuntimeError("OPENWEATHER_API_KEY not set")
    r = requests.get(BASE, params={
        "lat": round(lat, 4), "lon": round(lon, 4),
        "appid": API_KEY, "units": "metric",
    }, timeout=20)
    r.raise_for_status()
    j = r.json()

    times, t2, rh, ws, sw = [], [], [], [], []
    by_date = {}
    for item in j.get("list", []):
        when = dt.datetime.fromtimestamp(item["dt"], tz=dt.timezone.utc)
        main = item.get("main", {})
        wind = item.get("wind", {})
        clouds = (item.get("clouds") or {}).get("all")
        temp = main.get("temp")
        times.append(when.strftime("%Y-%m-%dT%H:%M"))
        t2.append(temp)
        rh.append(main.get("humidity"))
        ws.append(round(wind.get("speed", 0.0) * 3.6, 1))  # m/s -> km/h
        sw.append(_estimate_shortwave(lat, lon, when, clouds))
        if temp is not None:
            d = when.date()
            by_date[d] = max(by_date.get(d, temp), temp)

    daymax = [by_date[d] for d in sorted(by_date)[:forecast_days]]

    return {
        "_provenance": "live",
        "_sw_estimated": True,
        "generationtime_utc": _utcnow().isoformat(),
        "hourly": {"time": times, "temperature_2m": t2,
                   "relative_humidity_2m": rh, "wind_speed_10m": ws,
                   "shortwave_radiation": sw},
        "daily": {"temperature_2m_max": daymax},
    }


def synth_weather(lat, lon, now=None):
    """Clearly-tagged OFFLINE FALLBACK series (used only if the live API is
    unreachable, so the demo still renders). Every consuming layer is tagged
    provenance='demo-fallback'; it is never mistaken for live ingestion."""
    now = now or _utcnow()
    start = now - dt.timedelta(hours=48)
    times, t2, rh, ws, sw = [], [], [], [], []
    for i in range(48 + 168):  # 2d past + 7d ahead
        t = start + dt.timedelta(hours=i)
        hour = t.hour
        amp = 4.0
        temp = 28.0 - amp * math.cos(2 * math.pi * (hour - 2) / 24)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        t2.append(round(temp, 1))
        rh.append(round(72 + 8 * math.sin(2 * math.pi * (hour - 6) / 24), 1))
        ws.append(round(6 + 2 * math.sin(2 * math.pi * hour / 24), 1))
        sw.append(int(max(0, 700 * math.cos(2 * math.pi * (hour - 13) / 24))))
    return {
        "_provenance": "demo-fallback",
        "generationtime_utc": _utcnow().isoformat(),
        "hourly": {"time": times, "temperature_2m": t2,
                   "relative_humidity_2m": rh, "wind_speed_10m": ws,
                   "shortwave_radiation": sw},
        "daily": {"temperature_2m_max": [max(t2[i:i+24]) for i in range(48, len(t2), 24)][:6]},
    }


def fetch_many(centroids, max_workers=8, forecast_days=6):
    """Fetch weather for many ward centroids with a small thread pool.
    Returns {ward_id: record} for successes and {ward_id: error} for failures.
    """
    from concurrent.futures import ThreadPoolExecutor
    out = {}

    def one(item):
        wid, (lat, lon) = item
        try:
            return wid, ("ok", fetch_ward(lat, lon, forecast_days))
        except Exception as e:  # noqa
            return wid, ("err", f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for wid, res in ex.map(one, centroids.items()):
            out[wid] = res
    return out
    
def fetch_many_paced(centroids, max_workers=5, per_minute_limit=55):
    """Fetch weather for many locations while respecting OpenWeatherMap's
    free-tier 60 requests/minute cap. Processes in chunks sized to the limit,
    pausing between chunks so each chunk's wall-clock time is at least 60s
    (unless it's the last chunk) — avoids OWM-side throttling that was
    causing slow/laggy refreshes."""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    items = list(centroids.items())
    out = {}

    def one(item):
        wid, (lat, lon) = item
        try:
            return wid, ("ok", fetch_ward(lat, lon))
        except Exception as e:  # noqa
            return wid, ("err", f"{type(e).__name__}: {e}")

    for i in range(0, len(items), per_minute_limit):
        chunk = items[i:i + per_minute_limit]
        t0 = _t.time()
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for wid, res in ex.map(one, chunk):
                out[wid] = res
        elapsed = _t.time() - t0
        remaining = len(items) - (i + len(chunk))
        if remaining > 0 and elapsed < 60:
            _t.sleep(60 - elapsed)
    return out
