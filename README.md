# TAPAS — Thermal Assessment & Protection Analytics System

**Ward-level heat-health early warning for Indian cities.**

Smart India Hackathon · Problem Statement ID 26083 · Team **Ecoloytes**

🔗 **Live prototype:** https://tapas-1-8mey.onrender.com/
*(Hosted on Render's free tier — the first load can take 30–60 seconds while the server wakes up.)*

---

## Contents

1. [The problem](#the-problem)
2. [What TAPAS does](#what-tapas-does)
3. [How the risk model works](#how-the-risk-model-works)
4. [Data sources](#data-sources)
5. [Architecture](#architecture)
6. [Quick start](#quick-start)
7. [Configuration](#configuration)
8. [API reference](#api-reference)
9. [Project structure](#project-structure)
10. [Rebuilding the data layers](#rebuilding-the-data-layers)
11. [Testing](#testing)
12. [Deployment](#deployment)
13. [Honesty & limitations](#honesty--limitations)
14. [Roadmap](#roadmap)
15. [Acknowledgements & attribution](#acknowledgements--attribution)

---

## The problem

Heatwaves are a growing public-health threat in India, but warnings are usually issued at city or district level. Local authorities can't easily tell **which wards** are most vulnerable, what the **health impact** might be, or **which interventions** to trigger — and residents get generic advice.

## What TAPAS does

TAPAS turns live weather, satellite-derived urban environment and Census vulnerability into a **ward-level risk picture** and **suggested protective actions** for both administrators and residents.

Pilot cities (417 wards in total): **Ahmedabad (48), Mumbai (24), Chennai (201), Hyderabad (144).**

### Dashboard

| Level | What you see |
|---|---|
| **India** | Real GIS map with state boundaries and the four pilot-city markers; a national heat-watch summary (wards per risk band, per city). |
| **City** | Real municipal ward polygons coloured by the selected layer; city overview (risk distribution, 30-day heat trend, administrative actions, resource allocation); ward search and a severity filter. |
| **Ward** | Headline risk band, mortality risk and hospitalization-spike outputs, 5-day forecast, preventive-measures simulator, action lists for administrators and residents, and the underlying data with provenance tags. |

### Map layers

HTSI (heat-stress index) · Mortality risk · Hospitalization Spike · UTCI heat stress (°C) · Satellite vegetation (%)

### Key features

- **Anomaly-driven risk.** A ward escalates when heat exceeds *its own city's* seasonal norm, not on raw temperature — so humid Chennai isn't permanently "red".
- **Two health outputs.** Mortality risk and Hospitalization Spike are computed from the same weighted factors via separate exposure–response logistics.
- **5-day forecast per ward** with confidence that degrades with lead time.
- **30-day heat trend per city** (archive backfill + live scans, visibly distinguished).
- **Preventive-measures simulator.** Tick interventions (cooling centres, water audits, outdoor-work rescheduling, welfare checks, grid/energy notice) and see the modelled step-down in mortality and hospitalization risk. Clearly labelled as an illustrative scenario.
- **Action library modelled on the Ahmedabad Heat Action Plan**, split into *administration* and *resident* guidance, escalating with risk level.
- **Alerts** via SMS/WhatsApp (Twilio) in **English, Hindi and the state's language**, with a heat-stroke emergency message at High/Severe. Without Twilio credentials, sends are honestly marked **SIMULATED**.
- **Heatwave simulator (preview mode).** Adds +N °C to preview escalation of risk, measures and alerts. Off by default and always labelled as a preview, never as live.
- **Provenance everywhere.** Layers are tagged `live`, `satellite`, `climatology` or `fallback`.

---

## How the risk model works

### HTSI — Heat-Threat Stress Index

```
HTSI = (W_H · H) × (W_V · V) × (W_E · E) × (1 − W_AC · AC) × scale
```

| Factor | Meaning | Built from |
|---|---|---|
| **H** Hazard | How unusual today's heat is | Air-temp anomaly vs the city's ERA5 monthly 90th-percentile Tmax, plus UTCI "surge" (roughly `0.62·anomaly + 0.38·surge` in the forecast path) |
| **E** Exposure | Urban thermal environment | Satellite built/vegetation fractions, MODIS land-surface temperature, WUDAPT Local Climate Zone |
| **V** Vulnerability | Who is at risk | 7 Census-2011 indicators: children 0–6, literacy, slum share, elderly 60+, disability, density, kutcha housing (weights renormalise over available indicators) |
| **AC** Adaptive capacity | Ability to cool/cope | Per-ward proxy (green cover, hospitals, electricity/water reference data) |

HTSI is bucketed into **Low / Moderate / High / Severe** using configurable cut-points (default `0.055 / 0.115 / 0.185`).

### Mortality risk & Hospitalization Spike

Two separate logistic models on the same weighted factors plus the temperature anomaly and UTCI surge. Adaptive capacity enters with a **negative** sign (more cooling access → lower risk). Risk bands default to `0.06 / 0.22 / 0.45`.

### Preventive-measure effects

Each measure moves one model term (adaptive capacity, UTCI surge, or vulnerability uplift) by a fixed effect size, applied cumulatively. Effect sizes live in `data/weights.json`.

> ⚠️ **All coefficients, band cut-points and measure effect sizes are defensible defaults, not validated against outcome data.** India publishes no ward-level heat-mortality data. Treat outputs as *relative early-warning signals*, not clinical probabilities. See [Honesty & limitations](#honesty--limitations).

---

## Data sources

| Layer | Source | Cadence |
|---|---|---|
| Live & forecast weather | [OpenWeatherMap](https://openweathermap.org/forecast5) 5-day/3-hour forecast API, per ~3 km grid cell | Refreshed every 6 h by default |
| Historical baseline | ECMWF **ERA5** reanalysis via Open-Meteo archive, ~11 years; monthly 90th-percentile Tmax per city | Static (calibration only) |
| 30-day trend backfill | Open-Meteo archive API, model re-run for one representative ward per city | Static file + live scans going forward |
| Land-surface temperature & NDVI | **NASA GIBS** — MODIS Terra LST (day) and NDVI 8-day | Per fetch run |
| Local Climate Zone | **WUDAPT** global LCZ map (Demuzere et al. 2022), 100 m | Static |
| Vegetation / built / water fractions | Derived from Esri World Imagery pixels inside each ward polygon | Static |
| Vulnerability | **Census of India 2011** (C-14 city tables, HL-14 housing tables, age-standardised disability) | Static |
| Ward boundaries | Municipal / OSM administrative boundaries (GeoJSON) | Static |
| Basemap | Esri World Light Gray Canvas (keyless tiles) | Live tiles |

> Shortwave radiation is **estimated** from a clear-sky solar-position model scaled by cloud cover (the free OpenWeatherMap endpoint doesn't provide it); those records carry `_sw_estimated: true`.

---

## Architecture

```
 OpenWeatherMap ──►  weather.py  ──►  Live (6-hourly refresh) ─┐
 ERA5 baseline ───►  datastore.py ──► STORE (wards, env, V)    ├─► compute_snapshot() ─► FastAPI ─► Leaflet UI
 MODIS / LCZ / Census / satellite ──►  (JSON, or PostGIS)      ┘        │
                                                                        ├─► scheduler: national scan, history,
                                                                        │   alerts + digests (Twilio / outbox)
                                                                        └─► scenario / simulator endpoints
```

- **Backend:** Python 3.12, FastAPI + Uvicorn. NumPy vectorises UTCI over a whole ward's forecast window; `pythermalcomfort` computes UTCI. A background scheduler thread refreshes weather, rescans all wards and emits alerts.
- **Frontend:** vanilla JavaScript, HTML and CSS (no build step). Map is **Leaflet** on Esri keyless tiles; India states and ward polygons are real GeoJSON rendered as vector layers.
- **Storage:** JSON files in `data/` by default. Optional **PostGIS** read path (`DATABASE_URL`); the app falls back to JSON automatically if the database is absent.
- **Live vs fallback:** each city reports `weather_prov` = `live`, `mixed` or `fallback`; it is surfaced in the header status and per ward.

---

## Quick start

### Requirements

- Python 3.12+
- An [OpenWeatherMap API key](https://openweathermap.org/api) (free tier is enough) for live weather

### Run locally

```bash
git clone <your-repo-url> && cd TAPAS-main
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export OPENWEATHER_API_KEY=your_key_here      # Windows PowerShell: $env:OPENWEATHER_API_KEY="..."
cd backend
python main.py
```

Open **http://localhost:8000**. The first national snapshot takes a little while, because the free tier is rate-limited (~55 calls/min) and the scheduler paces requests accordingly.

Without a key the app still starts: wards show **Insufficient data** or `fallback` weather, clearly labelled — never presented as live.

### Docker

```bash
docker build -t tapas .
docker run -p 8000:8000 -e OPENWEATHER_API_KEY=your_key tapas
```

### Docker Compose (with PostGIS)

```bash
OPENWEATHER_API_KEY=your_key docker compose up --build
```

This starts PostGIS, loads ward polygons via `backend/load_postgis.py`, then serves the app on port 8000.

---

## Configuration

All configuration is via environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `OPENWEATHER_API_KEY` | *(none)* | Live weather. Unset → no live data. |
| `PORT` | `8000` | HTTP port. |
| `HW_LIVE_REFRESH_S` | `21600` (6 h) | How often live weather is re-fetched. |
| `HW_DIGEST_S` | `21600` (6 h) | How often digest alerts are generated. |
| `TAPAS_NOSCHED` | *(unset)* | Set to `1` to disable the background scheduler (tests, offline use). |
| `DATABASE_URL` | *(none)* | Optional PostGIS connection string. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `TWILIO_TO` | *(none)* | Enable **real** SMS/WhatsApp sends. Otherwise sends are logged as SIMULATED. |

Model coefficients, weights, band cut-points and measure effects are stored in `data/weights.json` and can be inspected at `GET /api/weights`.

---

## API reference

Interactive docs are served by FastAPI at `/docs`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/india` | State boundaries and city markers |
| GET | `/api/india/watch` | National heat-watch (per-city band counts) |
| GET | `/api/cities` | Cities with `weather_prov` (live / mixed / fallback), refresh interval, simulator state |
| GET | `/api/city/{city}/wards` | Ward features + band, HTSI, mortality, hospitalization, aggregate |
| GET | `/api/city/{city}/geometry` | Ward polygons (static) |
| GET | `/api/city/{city}/ward/{id}` | Full ward snapshot: factors, forecast, measures, layers |
| GET | `/api/city/{city}/ward/{id}/projection` | Preventive-impact projection |
| GET | `/api/city/{city}/allocation` | Ranked at-risk wards and resource share |
| GET | `/api/city/{city}/export.csv` | City ward data as CSV |
| GET | `/api/trend?days=30` | Heat trend series (archive backfill + live scans) |
| GET / POST | `/api/scenario/preventive` | Preventive-measures scenario (POST computes a ward scenario) |
| GET / POST | `/api/weights` | Read / update model weights and coefficients |
| POST | `/api/sim` | Set the labelled heatwave-preview offset (°C) |
| GET | `/api/outbox` | Alert log |
| POST | `/api/alerts/test` | Send a test alert (real if Twilio is configured) |
| GET | `/api/twilio/status` | Whether Twilio is configured |
| GET | `/api/db/status` | PostGIS status |
| GET | `/api/cadence` | Data-layer cadence and provenance |

> ⚠️ `POST /api/weights`, `/api/sim` and `/api/alerts/test` are **unauthenticated** and `/api/sim` is **global server state**. Don't expose these publicly without adding access control. See [Roadmap](#roadmap).

---

## Project structure

```
.
├── backend/
│   ├── app.py               # FastAPI app: model, snapshots, scheduler, alerts, all endpoints
│   ├── main.py              # Uvicorn entry point
│   ├── weather.py           # OpenWeatherMap ingestion + labelled offline fallback
│   ├── datastore.py         # Loads wards, satellite attrs, baselines, Census references
│   ├── measures.py          # Admin / resident action library (Heat Action Plan based)
│   ├── measures_i18n.py     # Hindi + state-language message generation
│   ├── pg_store.py          # Optional PostGIS read path
│   ├── load_postgis.py      # Loads polygons/attributes into PostGIS
│   ├── sql/                 # PostGIS schema
│   └── (data builders)      # build_maps, build_national, sat_attrs, fetch_modis,
│                            #   fetch_lcz, prepare_climate, backfill_trend
├── static/
│   ├── index.html
│   ├── app.js               # India → City → Ward navigation, layers, panels, simulator UI
│   └── styles.css
├── data/
│   ├── cities/<City>/       # wards.geojson, baseline, MODIS, LCZ, health reference
│   ├── geo/                 # source ward boundaries
│   ├── hl14/                # parsed Census C-14 / HL-14 tables
│   ├── processed/           # national GeoJSON, ward profiles
│   ├── weights.json         # model coefficients & measure effects
│   └── trend_backfill.json  # 30-day archive backfill
├── tests/test_tapas.py      # 24 offline tests
├── scripts/check_city_trend_ui.py  # optional browser regression check (Playwright)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Rebuilding the data layers

The repository ships with all processed data, so you only need these to refresh or extend it. Run from `backend/`.

| Script | What it does |
|---|---|
| `build_maps.py` | Fetches Esri imagery tiles and normalises ward geometry per city |
| `sat_attrs.py` | Derives vegetation / built / water fractions per ward from imagery |
| `fetch_modis.py` | Per-ward MODIS Terra LST and NDVI via NASA GIBS (no key needed) |
| `fetch_lcz.py` | Per-ward WUDAPT Local Climate Zone (reads the COG by HTTP range requests) |
| `prepare_climate.py` | ERA5-based monthly 90th-percentile Tmax baseline per city (Open-Meteo archive) |
| `backfill_trend.py [days]` | Re-runs the model over archived weather to produce `trend_backfill.json` |
| `load_postgis.py` | Loads polygons/attributes into PostGIS (`DATABASE_URL`, or emits SQL) |

Some builders need extra packages (e.g. Pillow); install them as prompted.

---

## Testing

```bash
pip install pytest httpx
cd backend
TAPAS_NOSCHED=1 python3 -m pytest ../tests -q
```

The suite is offline-safe (no weather network, scheduler disabled) and covers the API surface, honest Twilio behaviour, model factors, adaptive capacity, vulnerability, trend labelling and multilingual messages.

Optional UI regression check (needs a running server and Playwright + Chromium):

```bash
python3 scripts/check_city_trend_ui.py http://localhost:8000
```

---

## Deployment

The app is a single container serving both API and static UI on `$PORT`. It runs on Render (Docker or native Python: `python backend/main.py`).

Checklist:
- Set `OPENWEATHER_API_KEY` in the host's environment.
- Verify live data after deploy: `GET /api/cities` should show `weather_prov: "live"` for each city (allow the first scan to finish).
- Free tiers sleep when idle, so expect a cold start; consider a keep-warm ping for demos.
- Set Twilio variables only if you want **real** sends.

---

## Honesty & limitations

TAPAS is deliberately transparent about what it is not.

- **Not clinically validated.** Mortality and hospitalization outputs come from hand-set logistic coefficients. They rank wards and flag escalation; they are not calibrated probabilities of death or admission.
- **Census 2011 is the latest published ward-level data.** Vulnerability is therefore ~15 years old. Census 2027 ward-level results are not yet available. Hyderabad's elderly share is borrowed from a district row, and slum share is pending where unpublished (both disclosed in the code).
- **Satellite fractions are colour-threshold proxies** from basemap imagery, not classified land-cover products.
- **Fallback weather.** If a live call fails for a grid cell, the backend substitutes a clearly tagged synthetic series (`weather_prov: fallback`) so the demo still renders. Treat any ward in `fallback` or `mixed` cities as **not live**.
- **The trend is partly a re-run.** Historical trend bars are model re-runs on archived weather for one representative ward per city; only scans recorded going forward are full-city live scans. The UI distinguishes them.
- **Preventive-measure effects are illustrative**, not measured intervention outcomes.
- **Forecast resolution.** The free OpenWeatherMap endpoint provides 3-hour steps for 5 days; confidence is lowered after day 3.

---

## Roadmap

**Trust & safety**
- Show fallback-weather wards as *Insufficient* (or visibly hatched) rather than scoring them normally.
- Make the heatwave simulator per-session instead of global.
- Add authentication for write endpoints (`/api/weights`, `/api/sim`, `/api/alerts/test`).
- Show "data updated N minutes ago" in the UI.

**Data**
- Layer recent data over Census 2011 (modelled population/age grids, building footprints, night lights), with versioned vulnerability indices and provenance.
- Ingest Census 2027 tables as they are published.
- Deep-pilot **Ahmedabad**: obtain daily mortality, ambulance-call and heat-illness admission data; fit and validate the exposure–response curves on held-out years.

**Product**
- Report risk as bands/ranks until calibration exists.
- Frontend regression tests (navigation, filters, selection).
- Move dev-only dependencies (Playwright) out of `requirements.txt`.

---

## Acknowledgements & attribution

- Weather: [OpenWeatherMap](https://openweathermap.org/) · Reanalysis: ECMWF **ERA5** via [Open-Meteo](https://open-meteo.com/)
- Satellite: NASA **GIBS** / MODIS · Imagery and tiles © **Esri**
- Local Climate Zones: Demuzere et al. (2022), WUDAPT global LCZ map
- Thermal comfort: [`pythermalcomfort`](https://github.com/CenterForTheBuiltEnvironment/pythermalcomfort) (UTCI)
- Demographics: Census of India 2011
- Response design: modelled on the **Ahmedabad Heat Action Plan**

Built by **Team Ecoloytes** for Smart India Hackathon (PS 26083).
