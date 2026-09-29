# TAPAS — Thermal Assessment & Protection Analytics System

**Smart India Hackathon · Problem Statement ID 26083 · Team Ecoloytes**

TAPAS is a ward-level heat-risk assessment and decision-support system for India. Heatwave warnings usually say *where it is hot*; TAPAS adds a layer that says *where it is most dangerous*, by combining live weather with satellite data, Census vulnerability and cooling access into one index, then turning that into suggested actions and multilingual alerts for the people who have to respond.

**Live prototype:** https://tapas-1-8mey.onrender.com/

> The prototype runs on Render behind an uptime monitor, so it normally loads without a cold start. Right after a restart, give the national heat-watch a moment to finish loading.

---

## Table of contents

1. [The problem](#the-problem)
2. [What TAPAS does](#what-tapas-does)
3. [How the model works](#how-the-model-works)
4. [Architecture and project structure](#architecture-and-project-structure)
5. [Data sources](#data-sources)
6. [Getting started](#getting-started)
7. [Configuration](#configuration)
8. [API reference](#api-reference)
9. [Rebuilding the data](#rebuilding-the-data)
10. [Testing](#testing)
11. [Limitations](#limitations)
12. [Future work](#future-work)

---

## The problem

Heatwaves are a growing public-health threat in India, but warnings are issued at a coarse level. Local authorities can't easily tell which wards are most vulnerable, what the health impact could be, or which interventions to trigger and when.

## What TAPAS does

- **National heat-watch.** A satellite-basemap map of India with drill-down from country → city → ward, plus a national summary of how many wards sit in each risk band.
- **Ward-level risk (HTSI).** Combines Hazard, Vulnerability, Exposure and Adaptive Capacity into the Heat Thermal Severity Index, banded Low / Moderate / High / Severe.
- **Health-impact indicators.** Modelled mortality and hospitalization-spike probabilities for every ward.
- **Thermal comfort and environment.** UTCI (Universal Thermal Climate Index), MODIS land-surface temperature, vegetation (NDVI) and Local Climate Zones as context.
- **5-day forecast per ward**, with confidence that decays with lead time.
- **Protection measures.** Administrator actions and personal guidance that escalate with the risk band, modelled on public Heat Action Plans (chiefly Ahmedabad's).
- **Alerting.** Event alerts when a ward escalates into High/Severe, plus a 6-hourly digest. Messages go out by SMS/WhatsApp in English, Hindi and the city's state language, with a dedicated heatstroke emergency block at High/Severe.
- **Decision-support tools.** Resource-allocation ranking of wards, a preventive-measures impact simulator, a 30-day trend view, and a per-city CSV audit export.
- **Heatwave simulator (preview mode).** Raises ward temperature by 0–10 °C to preview how risk, measures and alerts escalate. It is off by default, clearly badged, and never presented as live data.

### Using the prototype

1. Open the live prototype and wait for the national watch to load.
2. Click a city, then a ward, to open its detail panel. You can also search any ward across India.
3. Switch map layers: **Risk**, **Mortality**, **Hospitalization Spike**, **UTCI**, **Vegetation**.
4. Open **Alert log** to see the SMS/WhatsApp outbox.
5. To test a scenario, open the **Simulator**, pick a temperature rise, and apply the preview. Use **Reset** to return to live data.

### Who it's for

Municipal and district disaster-management authorities, public-health departments, and urban planners working on heat action plans.

### Pilot coverage

| City | State | Wards | Alert language |
|---|---|---|---|
| Mumbai | Maharashtra | 24 | Marathi |
| Ahmedabad | Gujarat | 48 | Gujarati |
| Chennai | Tamil Nadu | 201 | Tamil |
| Hyderabad | Telangana | 144 | Telugu |

417 wards in total. All alerts also include English and Hindi.

---

## How the model works

Risk is **anomaly-driven**: a ward escalates when today's heat exceeds *its own city's* seasonal norm, not because raw afternoon temperature is high (which is routine in humid Chennai). If inputs are missing, TAPAS reports "insufficient data" for that ward instead of substituting an average.

### The index

```
HTSI = (wH · H) × (wV · V) × (wE · E) × (1 − wAC · AC)
```

| Factor | What it captures | How it's computed |
|---|---|---|
| **H** Hazard | Heat above the city's norm, plus extreme thermal stress | `H = clamp(0.62 · anomaly + 0.38 · surge)`. The anomaly driver is the day's max temperature minus the city's mean monthly max (ERA5 2014–2024), scaled to IMD criteria: 0 at normal, 0.5 at +4.5 °C (Heat Wave), 1.0 at +6.4 °C (Severe Heat Wave). The surge term is `clamp((UTCI − 36) / 10)`, so only extreme UTCI counts. |
| **V** Vulnerability | Who is most at risk | Seven Census 2011 indicators, weighted: children 0–6 (0.22), slum share (0.18), literacy (0.13), elderly 60+ (0.13), density (0.13), kutcha housing (0.12), disability (0.09). Kutcha housing is per-ward where Census HL-14 data matched; the rest are city-level. |
| **E** Exposure | How much the built environment traps heat | Built-up vs. vegetation/water fractions from satellite imagery, adjusted by the ward's MODIS daytime LST anomaly against the city median and by the LCZ built share. |
| **AC** Adaptive capacity | Ability to cope (cooling, water, healthcare) | A documented city baseline modulated per ward by built-up density, MODIS NDVI, electricity access, treated-tap water, and hospitals per km². Clamped to 0.05–0.90. |

Because HTSI is multiplicative, it is 0 whenever hazard is 0. Hazard is deliberately zero when the day's maximum is at or below the city's normal and UTCI is at or below 36 °C, so ordinary days read as no heat risk by design.

### Bands and health outputs

- **HTSI bands:** Low < 0.055 ≤ Moderate < 0.115 ≤ High < 0.185 ≤ Severe.
- **Mortality and hospitalization** use transparent logistic models over the anomaly, UTCI surge, H, E, V and AC (AC has a negative sign, since more cooling access lowers risk). Probability bands cut at 0.06 / 0.22 / 0.45. Every per-term contribution is exposed in the ward panel and the CSV export.
- **Confidence** is propagated from the provenance of each input (live vs. fallback weather, satellite, Census, cooling proxy).

All weights, coefficients, band cut-points and measure effect-sizes are editable at runtime through `POST /api/weights` and persisted to `data/weights.json`, with a reset to documented defaults.

### Alert logic

A background scheduler refreshes live weather every 6 hours (`HW_LIVE_REFRESH_S`) and re-scores all 417 wards only when the weather has refreshed. It then:

- fires an **event alert** when a ward escalates into High or Severe (cooldown: 6 h for High, 3 h for Severe);
- sends a **6-hourly digest** of active at-risk wards, which skips any ward that already got an event alert inside the digest window (cross-suppression).

---

## Architecture and project structure

Single-service app: a FastAPI backend serves the JSON API and the static frontend. The runtime reads file-based JSON for portability; PostGIS is an optional read path for ward geometry.

```
TAPAS/
├── backend/
│   ├── main.py            # Uvicorn entry point (honours $PORT)
│   ├── app.py             # FastAPI app: risk model, scheduler, alerts, all API routes
│   ├── datastore.py       # Loads wards, satellite/Census/MODIS/LCZ layers, city config
│   ├── weather.py         # OpenWeatherMap ingestion + clearly-tagged demo fallback
│   ├── measures.py        # Admin actions and personal guidance by risk band
│   ├── measures_i18n.py   # Hindi / Marathi / Gujarati / Tamil / Telugu guidance + emergency text
│   ├── pg_store.py        # Optional PostGIS read path (falls back to JSON)
│   ├── load_postgis.py    # Loads ward polygons into PostGIS
│   ├── sql/schema.sql     # PostGIS schema (cities, wards, ward_scores, alerts)
│   └── build_maps.py, build_national.py, sat_attrs.py, fetch_modis.py,
│       fetch_lcz.py, prepare_climate.py, backfill_trend.py    # data-build pipeline
├── static/                # Frontend: index.html, app.js, styles.css (no framework)
├── data/
│   ├── cities/<City>/     # wards.geojson, map.json, basemap.jpg, baseline.json,
│   │                      #   modis.json, lcz.json, health.json
│   ├── hl14/              # Census 2011 HL-14 / C-14 source tables + parsed JSON
│   ├── geo/               # Raw municipal ward/zone boundaries
│   ├── processed/         # India basemap and national geometry
│   ├── weights.json       # Persisted model configuration
│   ├── trend_backfill.json
│   └── outbox.jsonl       # Persisted alert log
├── tests/test_tapas.py    # Pytest suite
├── scripts/check_city_trend_ui.py   # Browser regression check (Playwright)
├── Dockerfile
├── docker-compose.yml     # App + PostGIS
└── requirements.txt
```

**Tech stack**

- **Backend:** Python 3.12, FastAPI on Uvicorn. NumPy vectorizes the UTCI calculation across each city's forecast window, and `pythermalcomfort` computes UTCI. Shapely handles geometry.
- **Frontend:** Vanilla JavaScript, HTML and CSS, with Leaflet 1.9.4 (loaded from unpkg) for the map and hand-written inline SVG for charts and gauges.
- **Storage:** JSON files by default; optional PostGIS 16 (`docker-compose.yml`).
- **Alerting:** Twilio (SMS/WhatsApp), optional.
- **Hosting:** Render.

---

## Data sources

| Layer | Source |
|---|---|
| Live weather | OpenWeatherMap 5-day / 3-hour Forecast API (free tier), fetched per ~3 km grid cell shared by nearby wards. Shortwave radiation is estimated from a clear-sky solar model scaled by cloud cover, and tagged as estimated. |
| Historical baseline | ERA5 reanalysis daily Tmax, 2014–2024, pulled via the Open-Meteo archive API. Gives each city's monthly mean (the "normal") and 90th percentile. |
| Land-surface temperature and NDVI | NASA GIBS MODIS Terra (MOD11A1 daytime LST, MOD13Q1 8-day NDVI), sampled per ward. |
| Local Climate Zones | WUDAPT / Demuzere et al. (2022) global LCZ map (100 m). |
| Satellite basemap and land cover | Esri World Imagery; vegetation, water and built-up fractions derived per ward from the imagery. |
| Vulnerability and amenities | Census of India 2011: city figures, C-14 (elderly), HL-14 ward-level electricity, kutcha housing and treated tap water. Disability from Sagar et al. 2016. |
| Healthcare access | OpenStreetMap hospitals per km² per ward. |
| Ward geometry | Municipal ward boundaries (GeoJSON) for each pilot city. |
| Heat thresholds | IMD Heat Wave / Severe Heat Wave departure criteria (4.5 °C / 6.4 °C). |

---

## Getting started

### Prerequisites

- Python 3.12 (or Docker)
- Optional: an [OpenWeatherMap](https://openweathermap.org/api) API key for live weather, and a Twilio account for real SMS/WhatsApp sends

### Run locally

```bash
git clone <your-repo-url> tapas && cd tapas
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export OPENWEATHER_API_KEY=your_key_here             # optional; see below
python backend/main.py
```

Open http://localhost:8000. The app starts a background scheduler that loads weather for all four cities, so the national view takes a short while on first start.

Without an OpenWeatherMap key the app still runs, using a synthetic demo weather series that is tagged `fallback` everywhere it appears. It is never presented as live.

### Run with Docker

```bash
docker build -t tapas .
docker run -p 8000:8000 -e OPENWEATHER_API_KEY=your_key_here tapas
```

### Run with Docker Compose (app + PostGIS)

```bash
export OPENWEATHER_API_KEY=your_key_here             # optional
docker compose up --build
```

Compose starts a PostGIS 16 database, loads the ward polygons into it (`load_postgis.py`), then starts the app with `DATABASE_URL` set. Ward geometry is then served from PostGIS; check `GET /api/db/status`. If the database is absent, the app falls back to the JSON files automatically.

---

## Configuration

All configuration is through environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `OPENWEATHER_API_KEY` | *(unset)* | Enables live weather. Unset → tagged demo fallback. |
| `PORT` | `8000` | HTTP port. |
| `HW_LIVE_REFRESH_S` | `21600` (6 h) | How often live weather is refreshed and wards are re-scored. |
| `HW_DIGEST_S` | `21600` (6 h) | Interval of the all-city alert digest. |
| `DATABASE_URL` | *(unset)* | PostGIS connection string. Unset → JSON datastore. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `TWILIO_TO` | *(unset)* | Real SMS/WhatsApp sends. Without them, alerts are logged as **SIMULATED**. Also needs `pip install twilio` (commented out in `requirements.txt`). |
| `TAPAS_NOSCHED` | *(unset)* | Set to `1` to disable the background scheduler (used by tests and scripts). |

---

## API reference

Interactive docs are available at `/docs` (FastAPI's Swagger UI) when the server is running.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/india` | National geometry and summary |
| GET | `/api/india/basemap` | National basemap metadata |
| GET | `/api/india/watch` | National heat-watch: ward counts per band, per city |
| GET | `/api/trend?days=N` | Daily High/Severe ward counts (backfilled past + live scans), max 90 days |
| GET | `/api/cities` | List of pilot cities |
| GET | `/api/city/{city}/geometry` | Ward polygons (from PostGIS if available) |
| GET | `/api/city/{city}/wards` | All wards with current scores for a city |
| GET | `/api/city/{city}/basemap` | City basemap metadata |
| GET | `/api/city/{city}/ward/{ward_id}` | Full ward snapshot: HTSI, factors, risks, forecast, measures |
| GET | `/api/city/{city}/ward/{ward_id}/projection` | Ward projection |
| GET | `/api/city/{city}/allocation` | Wards ranked by priority score, with suggested share of resources |
| GET | `/api/city/{city}/export.csv` | Per-ward audit export including every logistic term |
| GET / POST | `/api/scenario/preventive` | Preventive-measures impact simulation |
| GET / POST | `/api/weights` | Read or edit model weights, coefficients, band cut-points; supports reset |
| POST | `/api/sim` | Set the heatwave simulator offset (`{"offset": 3}`; 0 = off) |
| GET | `/api/outbox` | Recent alert log |
| POST | `/api/alerts/test` | Send a test alert (simulated without Twilio credentials) |
| GET | `/api/twilio/status` | Which Twilio variables are configured |
| GET | `/api/db/status` | PostGIS connection status |
| GET | `/api/cadence` | Data refresh cadence per layer and stated non-goals |

Ward IDs can contain slashes (for example Mumbai's `F/N`); the routes accept them.

---

## Rebuilding the data

The repository ships with all processed data, so none of this is needed to run the app. To regenerate layers or add a city, run the scripts in `backend/` (they need extra libraries beyond `requirements.txt`, such as Pillow and rasterio):

| Script | Produces |
|---|---|
| `build_maps.py` | Esri satellite basemap and normalized ward geometry per city |
| `build_national.py` | India basemap and projection metadata |
| `sat_attrs.py` | Per-ward vegetation, water and built-up fractions from the basemap |
| `fetch_modis.py` | Per-ward MODIS LST and NDVI (`modis.json`) |
| `fetch_lcz.py` | Per-ward Local Climate Zone class (`lcz.json`) |
| `prepare_climate.py` | ERA5 monthly baseline per city (`baseline.json`) |
| `backfill_trend.py` | 30-day model re-run on archived weather (`trend_backfill.json`) |
| `load_postgis.py` | Loads wards into PostGIS (or prints SQL to pipe into `psql`) |

Adding a city needs its ward boundaries, Census matching, and a climate baseline, plus an entry in `backend/datastore.py`.

---

## Testing

```bash
pip install pytest httpx
cd backend && TAPAS_NOSCHED=1 python -m pytest ../tests -q
```

The suite runs offline (no weather network, scheduler disabled) and covers the API surface, honest Twilio behaviour, per-ward AC and vulnerability, MODIS/LCZ/HL-14 merging, trilingual alerts and emergency blocks, configurable weights and reset, the CSV audit export, digest cross-suppression, and the preventive-measures simulator.

For the trend card UI, start the server and run the Playwright check:

```bash
pip install playwright && playwright install chromium
python scripts/check_city_trend_ui.py http://localhost:8000
```

---

## Limitations

TAPAS is a prototype. These limits are stated plainly, and most are also disclosed in the app.

**Model and validation**
- **Mortality and hospitalization estimates are not validated.** Coefficients are documented, editable defaults. Ward-level heat-health outcome data isn't publicly available, so ward-level calibration isn't currently possible. Outputs are indicative, not clinical predictions.
- **Adaptive capacity is a modelled proxy.** There is no ward-level dataset of actual cooling access.
- **HTSI is multiplicative**, so it is 0 whenever hazard is 0 (by design; see above).
- **Risk-band cut-points** were tuned during development and may need re-tuning as the hazard definition is refined.
- **Preventive-measure effect sizes** are illustrative, unvalidated defaults.

**Data**
- **Census 2011** is the latest ward-level demographic source available. Wards that can't be matched fall back to city totals (disclosed in the app). Slum share for Chennai and Hyderabad is pending.
- **Satellite layers are dated snapshots**, not live. MODIS depends on satellite passes and cloud cover; LCZ is a static classification.
- **Coverage is four pilot cities.**
- **Trend history:** past days are model re-runs on archived weather for one representative ward per city; only days recorded by the live scheduler are full 417-ward scans.

**Operations**
- **Live weather needs an OpenWeatherMap key**; the free tier gives 3-hour steps, not hourly.
- **SMS/WhatsApp delivery needs a paid Twilio account.** Without one, alerts are logged as simulated.
- **The simulator is illustrative.** It raises temperature uniformly across wards and can't represent real spatial variation.
- **Runtime state is file-based** (`outbox.jsonl`, `history.jsonl`, `weights.json`); on hosts with ephemeral disks these reset on redeploy.

## Future work

- Load pending Census indicators and update to Census 2021 ward data when published.
- Cross-check the temperature–risk relationship against published studies (for example Ahmedabad excess-mortality work) and disclose the result.
- Add a keyless weather fallback so live data doesn't depend on a single provider key.
- Re-tune risk-band cut-points against the IMD-based hazard driver.
- Extend to further cities.
