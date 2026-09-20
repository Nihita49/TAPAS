# TAPAS: Thermal Assessment & Protection Analytics System

**Smart India Hackathon — Problem Statement ID: 26083**
**Team: Ecoloytes**

TAPAS is a live national heat-watch prototype for India. It turns heat data into ward-level risk insights and suggested protective actions, so authorities can see where heat is most dangerous and act before it escalates.

**🔗 Live Prototype:** [https://tapas-1-8mey.onrender.com/](https://tapas-1-8mey.onrender.com/)

> The prototype is hosted on Render's free tier, so the first load may take 30–60 seconds while the server wakes up.

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
2. Wait for the live national heat-watch to finish loading.
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
  - **Historical baseline:** ERA5 reanalysis-derived seasonal norms (90th-percentile monthly max temperature per city), so risk escalates only when today's heat exceeds a city's *own* seasonal norm rather than from raw temperature alone
  - **Vulnerability:** Census 2011 ward-level demographic data (elderly population, literacy, slum households, disability, population density)
  - **Ward geometry:** Real municipal ward boundaries (GeoJSON) for each pilot city
- **Alerting:** Twilio (SMS/WhatsApp), with trilingual message generation (English, Hindi, and the state's regional language) and a dedicated heatstroke emergency-escalation message at High/Severe risk
- **Hosting:** Render

## Status

Working prototype for demonstration and evaluation. The simulator uses hypothetical temperature increases and does not represent live conditions.
