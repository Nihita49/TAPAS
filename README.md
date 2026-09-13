# TAPAS — Ward-Level Heat-Health Early Warning System (4 Indian cities)

A professional, GIS-first demo of a ward-scale heat early-warning platform for
**Mumbai, Ahmedabad, Chennai and Hyderabad** — architected to show it scales from
one city to four, and beyond.

The UI is deliberately **white/light and clean**, with a real **satellite GIS
basemap occupying the working half** of the screen and ward polygons drawn over it.

> The formula, coefficients, and methodology are intentionally **not** shown
> in the app itself — the frontend states only that risk comes from a
> weighted multi-factor model. This README is the source of truth for how
> the model actually works.

---

## Run it locally

**Requirements:** Python 3.10+ (3.11 recommended), pip, internet access, and
an **OpenWeatherMap One Call API 3.0 key** (the app calls it on every
refresh cycle — see step 0 below).

0. **Get an OpenWeatherMap API key:**
   - Sign up at https://openweathermap.org/ — the free account key works
     directly against the **classic Forecast API** (`/data/2.5/forecast`,
     which is what `backend/weather.py` calls): **no card required**,
     1,000,000 calls/month, 60/min. (This is a different, no-billing tier
     from OpenWeatherMap's separate "One Call 3.0" product, which does
     require a card on file — this app deliberately uses the classic
     endpoint to avoid that.)
   - New keys can take up to ~2 hours to activate after signup.
   - Set it as an environment variable before running the server:
     ```bash
     export OPENWEATHER_API_KEY="your_key_here"     # Windows: $env:OPENWEATHER_API_KEY="your_key_here"
     ```
   - If this variable is unset, every live weather call fails immediately
     and the app falls back to the clearly-labelled `demo-fallback` series
     (see "Zero-placeholder rule" in `backend/weather.py`) — the app still
     runs, but shows no live weather.
1. **Get the code** — unzip this project anywhere, e.g. `~/tapas/`.
2. **Create a virtual environment (recommended, keeps deps isolated):**
   ```bash
   cd tapas/heatwatch
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   ```
3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   (`psycopg2-binary` and `playwright` are optional — only needed for the
   live PostGIS path and the Playwright screenshot/UI test respectively.
   If either fails to install and you don't need that feature, comment
   the line out in `requirements.txt` and re-run.)
4. **Run the server:**
   ```bash
   cd backend
   python main.py
   ```
5. **Open it:** go to **http://localhost:8000** in a browser. The
   national dashboard loads first — click any city card to open its
   ward map.

That's the whole setup beyond the API key — no database, no build step.
Ward geometry, satellite imagery, Census data, MODIS/LCZ layers etc. all
ship as static files under `data/` and are read straight off disk; live
weather comes from OpenWeatherMap's classic Forecast API (3-hour steps,
5 days ahead — no card, generous free quota).

> **Solar radiation note:** this endpoint does not publish a
> shortwave/solar-radiation field. `backend/weather.py` estimates it from a
> clear-sky solar-position model (latitude, day-of-year, hour-of-day) scaled
> by OWM's reported cloud cover — a standard meteorological approximation,
> not a live sensor measurement. Every record is tagged
> `"_sw_estimated": true` so this is auditable, consistent with the app's
> existing "defensible default, not validated" labelling for other factors.

**Stopping it:** `Ctrl+C` in the terminal running `python main.py`.

**To run it again later:** just repeat steps 2 (`source .venv/bin/activate`)
and 4 — no need to reinstall dependencies unless `requirements.txt` changed.

### Optional: live PostGIS storage
By default ward geometry is served from JSON files (no setup needed). To
serve it from a real PostGIS database instead:
```bash
createdb tapas && python load_postgis.py     # applies sql/schema.sql + 417 wards
DATABASE_URL="postgresql://tapas@127.0.0.1:5432/tapas" python main.py
# verify: GET /api/db/status -> {"live":true,"postgis_version":...,"cities":4,"wards":417}
```

### Optional: Docker
```bash
docker compose up --build
# OPENWEATHER_API_KEY and TWILIO_* vars are passed through from your environment if set
```

### Optional: accelerated demo clock
Speeds up the alert-digest cycle so a scheduled 6h-style digest is
observable live during a demo, instead of waiting 6 real hours:
```bash
HW_ACCELERATED=1 HW_DIGEST_S=70 HW_LIVE_REFRESH_S=1800 python main.py
```

### Run the tests
```bash
cd backend && TAPAS_NOSCHED=1 python3 -m pytest ../tests -q    # 24 tests
```

### Build / data-prep scripts (one-off, kept for transparency)
These produced the committed `data/cities/*` assets:
- `backend/build_maps.py` — downloads real Esri satellite tiles, normalises real ward geometry → 4 cities.
- `backend/sat_attrs.py` — derives real per-ward vegetation/built/water from the satellite imagery.
- `backend/prepare_climate.py` — computes observed monthly thresholds from ECMWF ERA5 reanalysis.
- `backend/fetch_modis.py` — real per-ward MODIS Terra day-LST & NDVI via NASA GIBS.
- `backend/fetch_lcz.py` — real per-ward WUDAPT LCZ class (Demuzere et al. 2022 global
  100 m map, Zenodo 6364594) read from the Cloud-Optimized GeoTIFF via HTTP range
  requests; per-ward mode class inside the real ward polygon.
- `backend/load_postgis.py` — applies `sql/schema.sql` and upserts ward geometry into PostGIS.

---

## How the risk works (real inputs only)

Each ward's risk is **anomaly-driven** — following the Ahmedabad Heat-Action-Plan
logic, a ward escalates when *today's heat exceeds its own city's seasonal
normal*, not from raw afternoon temperature (which is routinely high in humid
Chennai). Live weather is fetched per ~3 km grid point per city, then each ward
uses its nearest point.

```
heat stress H  = UTCI from live OpenWeatherMap temp/RH/wind + estimated radiation + anomaly vs ERA5 p90
environment E  = real satellite built/veg/water + MODIS Terra LST/NDVI + WUDAPT LCZ (per ward)
vulnerability V= 7-indicator Census-2011 index; kutcha housing real per ward (HL-14)
cooling AC     = per-ward composite: city baseline x real modifiers (HL-14 electricity/tap,
                 OSM hospitals/km2, MODIS NDVI, satellite built density) - modelled defaults
Risk / HTSI    = H x V x E x (1 - AC)          (weights configurable: GET/POST /api/weights)

All exposure-response logistic coefficients (mortality & hospitalisation),
the HTSI band cut-points, the sym_anom flag and the preventive-measure effect
sizes (measure_effects: cooling_centres, water_audits, outdoor_work_reschedule,
welfare_checks, grid_energy_notice) are ALSO configurable via
POST /api/weights (persisted to data/weights.json; reset:true restores the
documented UNVALIDATED defaults). Each ward snapshot also exposes the logistic
term-by-term contributions (factors.risk_terms) via the API for auditability.
Mortality     = logistic(-4.3 + 3.0a + 1.6s + 1.5*(wH*H) + 1.4*(wE*E) + 4.0*(wV*V-1) - 1.8*(wAC*AC))  [deaths]
Hospitalis.   = logistic(-4.0 + 3.2a + 1.8s + 1.2*(wH*H) + 1.3*(wE*E) + 3.2*(wV*V-1) - 1.4*(wAC*AC))  [ER spike]
              (same WEIGHTED factors as HTSI - editing WEIGHTS moves HTSI and
               both risk outputs together; probability bands use risk_band_t,
               independently calibrated from HTSI's band_t by design)
              (AC = per-ward adaptive capacity in [0.05,0.90]: electricity, green
               cover, hospitals/km2, tap water, built density; higher AC lowers
               risk. k_m=1.8 / k_h=1.4 are defensible defaults, UNVALIDATED,
               pending real outcome data)
              (two INDEPENDENT outputs: same live inputs, separate coefficients;
               neither is derived from or merged with the other)
```

## Honest data sources

| Layer | Source | Provenance | Refresh |
|---|---|---|---|
| Live / forecast weather | OpenWeatherMap classic Forecast API, 3-hour steps (temp/RH/wind live; shortwave radiation is a clear-sky × cloud-cover estimate, see `backend/weather.py`) | **live** (radiation: estimated) | up to every 6 h |
| Ward thermal environment | Esri World Imagery satellite (per-ward true-colour analysis) | **satellite** (real) | static per ward |
| Ward LST / NDVI | MODIS Terra (NASA GIBS) | **satellite** (real) | 8-day product, sampled per ward |
| Ward LCZ | Demuzere et al. 2022 global LCZ map v1 (WUDAPT lineage), 100 m, Zenodo 6364594 | **real** per-ward mode class | static (2018 nominal) |
| Baseline / thresholds | ECMWF ERA5 reanalysis (Open-Meteo archive) 2014–2024, monthly 90th-pct Tmax | observed climatology | threshold only, never a live value |
| Ward polygons | Mumbai: MCGM; Ahmedabad/Chennai/Hyderabad: municipal/OSM admin boundaries (DataMeet/OpenCity lineage); served live from PostGIS when `DATABASE_URL` set | real boundary | static |
| Demographics | Census of India 2011 — city tables (C-14) + **ward-level HL-14** (electricity, kutcha, tap water); elderly 60+ from C-14 City (Mum/Ahd/Che) and, for Hyderabad, the fully-urban Hyderabad district row of C-14 Andhra Pradesh (2011 jurisdiction) — no official C-14 City row was ever published for Hyderabad (verified against the national C-14 City file and the AP/Telangana state files) | real | static |
| Healthcare proximity | OSM Overpass hospitals / ward km² | real | static |

**No placeholders.** Missing data renders **Insufficient data** (or an explicit
*pending* tag) — never a silently-substituted average.

## Preventive / response measures (Ahmedabad Heat-Action-Plan style)

Two tiers, made **dynamic by current risk level** and surfaced per ward and in
SMS/WhatsApp content:
- **Administration / city response** (`measures.admin`) — cooling centres, water
  audits, outdoor-work rescheduling, welfare checks, escalation to a city heat
  cell, grid/energy notice.
- **Personal guidance** (`measures.user`) — "drink water regularly", avoid
  12:00–16:00 exertion, check elderly neighbours, seek help for heat illness.
  Every user SMS/WhatsApp alert is sent in **three languages — English, Hindi
  (universal), and the state language of the recipient's city** (Marathi,
  Gujarati, Tamil, Telugu; `backend/measures_i18n.py`), and the guidance is
  **band-specific** (Moderate/High/Severe lists differ, driven by the ward's
  live-computed risk level). The same trilingual block renders in the ward
  panel and the alert-log modal.
- **Heat-stroke emergency block** — when a ward's live band is High or Severe,
  the SMS and the UI add, in all three languages: heat-stroke warning signs
  (very high fever, confusion, no sweating — do not wait) plus India's national
  numbers **112** (unified emergency) and **108** (ambulance).

## The simulator (clearly labelled, default off)

The main view always shows **today's real conditions**. A small **"Simulator
(not live)"** control raises temperature across wards so you can preview
escalation, the dynamic measures, and the alert log during a pitch. It is never
shown as live and can be reset in one click.

## Alerting & Twilio setup

Event + 6-hour city-digest alerts route through a logged outbox (SMS/WhatsApp
channels), viewable in-app via the **"Alert log"** button. Without any Twilio
credentials configured, every send is honestly logged as **SIMULATED** — the
app is fully usable and demoable with zero setup. Registering Twilio turns
these into **real SMS sends**.

### How to register for Twilio
1. Go to **[twilio.com/try-twilio](https://www.twilio.com/try-twilio)** and
   sign up (free trial, no card required to start).
2. Verify your email and phone number when prompted.
3. On the **Console Dashboard** (after login, this is the default landing
   page), you'll see your **Account SID** and **Auth Token** at the top —
   click "show" to reveal the token. Copy both.
4. Get a phone number to send *from*: in the Console, go to
   **Phone Numbers → Manage → Buy a number** (a trial account gives you one
   free number) — this is your `TWILIO_FROM` value, in E.164 format
   (e.g. `+15551234567`).
5. **Trial-account limitation:** a Twilio trial account can only send to
   phone numbers you've *verified* in the Console (**Phone Numbers →
   Manage → Verified Caller IDs**). Add the number you want alerts sent to
   there first — that becomes your `TWILIO_TO` value. (Upgrading to a paid
   account removes this restriction.)

### How to configure it here
Set these four environment variables before running `python main.py` (or
pass them through to `docker compose up`):
```bash
export TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TWILIO_AUTH_TOKEN="your_auth_token_here"
export TWILIO_FROM="+15551234567"        # the number you bought
export TWILIO_TO="+919XXXXXXXXX"         # the number you verified
```
On Windows (PowerShell): `$env:TWILIO_ACCOUNT_SID="AC..."` (repeat per variable).

### How to access / verify it
- **`GET /api/twilio/status`** — tells you whether all four variables are
  detected (`{"configured": true/false}`) without exposing the secret values.
- **Alert log → "📨 Send test alert"** button — fires `POST
  /api/alerts/test` immediately; the result line shows either `SENT <sid>`
  (real Twilio send, check your phone) or `SIMULATED` (no/incomplete
  credentials). This is the fastest way to confirm your setup worked.
- Every send — real or simulated — is appended to `data/outbox.jsonl` and
  shown in the in-app Alert log regardless.

**Production note:** commercial SMS in India additionally requires carrier
**DLT** (Distributed Ledger Technology) header/template registration with
your telecom operator — this app discloses that requirement and never
claims real delivery without proper Twilio credentials configured.

## Layout

```
heatwatch/
├─ backend/
│  ├─ app.py            # FastAPI app + scheduler (multi-city), V/AC/HTSI engine
│  ├─ datastore.py      # loads 4 cities (geometry/satellite/MODIS/LCZ/health/baseline)
│  ├─ pg_store.py       # live PostGIS reads (wards geometry, status)
│  ├─ measures.py       # two-tier preventive measures
│  ├─ measures_i18n.py  # Hindi + state-language personal guidance
│  ├─ weather.py        # OpenWeatherMap live + tagged offline fallback
│  ├─ load_postgis.py, fetch_modis.py, fetch_lcz.py, build_* / prepare_* / sat_attrs.py
│  ├─ sql/schema.sql
│  └─ main.py
├─ data/cities/<City>/  # wards.geojson, basemap.jpg, map.json, baseline.json,
│                       # health.json (HL-14), modis.json, lcz.json
├─ static/              # white/light GIS front-end (self-contained, no CDN)
├─ tests/test_tapas.py  # 14 offline-safe API/model tests
├─ Dockerfile, docker-compose.yml
└─ requirements.txt
```

## API (short)
`/api/cities` · `/api/india` · `/api/city/{id}/wards` · `/api/city/{id}/basemap` ·
`/api/city/{id}/ward/{wid}` · `/api/city/{id}/ward/{wid}/projection` ·
`POST /api/sim` · `/api/outbox` · `/api/cadence` · `/api/weights` (GET/POST) ·
`/api/trend` · `/api/db/status` · `/api/twilio/status` · `POST /api/alerts/test` ·
`GET/POST /api/scenario/preventive` (preventive-impact simulator: baseline vs
adjusted risk + per-measure waterfall; illustrative, unvalidated effect sizes,
tunable via /api/weights measure_effects) ·
`/api/city/{id}/export.csv` (per-ward audit export: HTSI, both risk outputs and every
logistic term; same compute_snapshot path as the ward panel; UNVALIDATED-defaults note in header)

## Explicit non-goals
- `/api/weights` is intentionally OPEN (no auth) in this demo instance - any visitor can retune the shared model; reset:true restores defaults. Production deployments must add admin auth.
- Not a clinical mortality/hospitalisation predictor.
- Satellite thermal layer is static per ward — not a live sub-6 h product.
- V/AC/mortality coefficients are defensible, disclosed, unvalidated defaults.
- Simulator overlays are clearly labelled not-live and default off.
