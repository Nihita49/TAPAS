"""Preventive & response measures modelled on public Heat Action Plans
(primarily the Ahmedabad Heat Action Plan). Two tiers:
  admin : city-administration / emergency actions (triggered & escalated)
  user  : personal guidance pushed in SMS/WhatsApp to residents & health staff
Dynamic by current risk level (Moderate/High/Severe). Content is an
action-library; the engine selects relevant measures per ward risk.
"""
TIERS={
 "admin":{
  "Moderate":[
    "Issue heat advisory; activate cooling-centre information line.",
    "Direct municipal water tankers + kiosks to high-exposure wards.",
    "Alert healthcare facilities to prepare for heat-illness cases.",
    "Brief ward officers + volunteers on heat watch and referral paths.",
  ],
  "High":[
    "Open municipal cooling/refuge centres and extend operating hours.",
    "Prioritise water + shade audits at bus stops, markets, worksites.",
    "Pre-position ORS + ice-packs at health posts in the ward.",
    "Shift outdoor municipal work to cooler hours (avoid 12:00-16:00).",
    "Notify grid/energy desk to prep for cooling-load spikes.",
  ],
  "Severe":[
    "Activate ward-level heat response cell; 6-hourly status to city control.",
    "Run 'no-show' welfare checks on at-risk (elderly, alone) residents.",
    "Suspend/limit outdoor school + construction during peak heat.",
    "Stand up temporary hydration + first-aid points at transit hubs.",
    "Escalate to city crisis committee for resource mobilisation.",
  ],
 },
 "user":{
  "Moderate":[
    "Drink water regularly, even if not thirsty.",
    "Avoid strenuous outdoor activity between 12:00 and 16:00.",
    "Wear light, loose, light-coloured clothing.",
  ],
  "High":[
    "Drink water/ORS at regular intervals; avoid caffeine & alcohol.",
    "Stay in shade or a cooled room; use wet cloth/water on skin.",
    "Check on elderly neighbours & those who live alone.",
    "Never leave children or pets in parked vehicles.",
  ],
  "Severe":[
    "Move to the nearest cooling/refuge centre or cool indoor space now.",
    "Seek medical help if dizzy, nauseated, confused or with high fever.",
    "Keep children indoors; supervise the elderly and chronically ill.",
    "Report neighbours in distress to the ward help line.",
  ],
 },
}

def measures_for(band, tier):
    lib=TIERS.get(tier,{})
    if band not in lib:
        # escalate to next available higher level
        order=["Low","Moderate","High","Severe"]
        for b in order[order.index(band) if band in order else 0:]:
            if b in lib: return lib[b]
        return []
    return lib[band]

def user_sms(band):
    return measures_for(band,"user")

def admin_actions(band):
    return measures_for(band,"admin")

TIER_LABEL={"admin":"Administration / City response","user":"Personal (resident) guidance"}
