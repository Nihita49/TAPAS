# TAPAS: Thermal Assessment & Protection Analytics System

**Smart India Hackathon — Problem Statement ID: 26083**
**Team: Ecoloytes**

TAPAS is a live national heat-watch prototype for India. It turns heat data into ward-level risk insights and suggested protective actions, so authorities can see where heat is most dangerous and act before it escalates.

**🔗 Live Prototype:** [https://tapas-1-8mey.onrender.com/](https://tapas-1-8mey.onrender.com/)

> The prototype is hosted on Render and kept awake by an uptime monitor that pings it regularly, so it normally loads without a cold start.

---

## Problem

Heatwaves are a growing public health threat in India. Warnings are usually issued at a coarse level, which makes it hard for local authorities to know which wards are most vulnerable, what the health impact could be, and which interventions to trigger.

## What TAPAS Does

- **Live national heat-watch:** A satellite-basemap map of India with drill-down from country to ward level.
- **Risk assessment:** Shows heat risk per region using multiple indicators.
- **Health impact indicators:** Estimates mortality risk and hospitalization spikes linked to heat exposure.
- **Thermal comfort and environment:** Displays UTCI (Universal Thermal Climate Index) and vegetation cover as context for heat stress.
- **Alert log:** A running log of heat alerts as conditions change, delivered via SMS/WhatsApp in English, Hindi, and the local state language.
- **Heatwave simulator (preview mode):** Lets users raise ward temperature by a chosen number of °C to preview how risk levels, protective measures, and alerts would escalate. It is clearly separated from live data, is off by default, and is never shown as live.

## Key Features

| Feature | Description |
|---|---|
| Interactive map | Satellite basemap with national → ward drill-down |
| Layer switching | Risk, Mortality, Hospitalization Spike, UTCI, Vegetation |
| Alert log | Live feed of heat alerts, with trilingual guidance and emergency escalation messaging |
| Simulator | "What-if" heatwave preview (+°C), with a reset to return to live data |
| Protection measures | Suggested actions that escalate with risk level |

## How to Use the Prototype

1. Open the [live prototype](https://tapas-1-8mey.onrender.com/).
2. Wait for the live national heat-watch to finish loading (this can take a moment right after a restart).
3. Use the layer options to switch between **Risk**, **Mortality**, **Hospitalization Spike**, **UTCI**, and **Vegetation**.
4. Click through the map to drill down from India to a specific ward, and open the detail panel for that ward.
5. Check the **Alert log** for current alerts.
6. To test a scenario, turn on the **Simulator** and raise the temperature with the **+°C** control to preview escalation of measures and alerts. Select **Live (reset)** to return to real data.

## Who It's For

- Municipal and district disaster management authorities
- Public health departments
- Urban planners working on heat action plans

## Tech Stack

- **Frontend:** Vanilla JavaScript, HTML5, CSS — no framework. Ward boundaries are rendered with a custom-built SVG map layer for fast, dependency-free loading.
- **Backend:** Python, FastAPI (ASGI) served via Uvicorn. NumPy is used to vectorize the thermal-comfort calculations across all wards in a city in a single batched pass, and `pythermalcomfort` computes UTCI (Universal Thermal Climate Index).
- **Data sources:**
  - **Live weather:** OpenWeatherMap Forecast API (per-ward-cluster live temperature, humidity, wind, cloud cover)
  - **Satellite basemap:** Esri World Imagery
  - **Thermal environment:** MODIS Land Surface Temperature + Local Climate Zone (LCZ) classification, used as an urban-heat-island proxy per ward
  - **Historical baseline:** ERA5 reanalysis-derived (2014–2024) monthly climatology per city. The hazard anomaly is the departure of the day's maximum temperature from the city's **normal** (mean monthly max temperature), scaled to India Meteorological Department (IMD) heat-wave criteria: 0 at normal, 0.5 at a 4.5 °C departure (IMD Heat Wave), and 1.0 at 6.4 °C (IMD Severe Heat Wave). The 90th-percentile monthly max is shown as the seasonal reference threshold. Risk therefore escalates only when heat exceeds a city's *own* seasonal norm rather than from raw temperature alone
  - **Vulnerability:** Census 2011 ward-level demographic data (elderly population, literacy, slum households, disability, population density)
  - **Ward geometry:** Real municipal ward boundaries (GeoJSON) for each pilot city
- **Alerting:** Twilio (SMS/WhatsApp; a paid, subscription-based service, so real sends require an active Twilio account and credentials), with trilingual message generation (English, Hindi, and the state's regional language) and a dedicated heatstroke emergency-escalation message at High/Severe risk
- **Hosting:** Render, with an uptime monitor pinging the app to keep it awake

---

## Limitations

TAPAS is a prototype. These are the current limits, stated plainly.

**Model and validation**
- **Mortality and hospitalization estimates are not validated.** The coefficients are documented, editable defaults. Ward-level heat-health outcome data is not publicly available, so ward-level calibration is not currently possible. Outputs are indicative, not clinical predictions.
- **Adaptive capacity (AC) is a modelled proxy.** It combines city-level cooling access with per-ward built-up, vegetation, electricity/tap-water and healthcare-access indicators. There is no ward-level dataset of actual cooling access.
- **HTSI is a multiplicative index** (H × V × E × (1 − AC)), so it is 0 whenever hazard is 0. The hazard is deliberately zero when the day's maximum is at or below the city's normal and UTCI is at or below 36 °C, so ordinary days read as no heat risk by design.
- **Risk-band cut-points** were tuned during development and may need re-tuning as the hazard definition is refined.

**Data**
- **Census 2011** is the latest ward-level demographic source available. Wards that cannot be matched to Census records fall back to city totals, and this is disclosed in the app. Some indicators (for example elderly population for Hyderabad) are pending.
- **Satellite layers are dated snapshots**, not live: MODIS land surface temperature and NDVI depend on satellite passes and cloud cover, and Local Climate Zones are a static classification.
- **Coverage is four pilot cities** (Mumbai, Ahmedabad, Chennai, Hyderabad). Adding a city needs its ward boundaries, Census matching and climate baseline.

**Operations**
- **Live weather requires an OpenWeatherMap API key.** Without it, the app falls back to offline data, which is tagged as such.
- **SMS/WhatsApp delivery requires a paid Twilio account and credentials.** Without them, alerts are logged as simulated in the alert log rather than sent.
- **The simulator is illustrative.** It raises temperature uniformly across wards and cannot represent real spatial variation.

## Future Work

- Load pending Census indicators (for example Hyderabad elderly population) and update to Census 2021 ward data when published.
- Cross-check the temperature–risk relationship at city level against published studies (for example Ahmedabad excess-mortality work) and disclose the result.
- Add a keyless weather fallback so live data does not depend on a single provider key.
- Re-tune risk-band cut-points against the IMD-based hazard driver.
- Extend to further cities.
