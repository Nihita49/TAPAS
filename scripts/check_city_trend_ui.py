"""UI regression check for the per-city 30-day trend card (requires a running server).

Verifies, in a real browser:
  1. the national (India) dashboard renders NO 30-day trend strip;
  2. opening a city renders a 30-bar strip whose colours/opacities exactly match
     that city's own series from GET /api/trend?days=30 (client-side filter);
  3. two different cities produce different strips.

Usage:  python3 scripts/check_city_trend_ui.py [base_url]   (default http://localhost:8000)
Exits 0 on pass, 1 on failure. Skips (exit 0 with notice) when playwright/chromium
are unavailable so CI without a browser stays green.
"""
import json, sys, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed"); return 0
    COL = {"Low": "rgb(46, 158, 91)", "Moderate": "rgb(232, 165, 29)",
           "High": "rgb(240, 114, 44)", "Severe": "rgb(221, 58, 58)"}
    trend = json.load(urllib.request.urlopen(BASE + "/api/trend?days=30"))["series"]

    def expected(city):
        return [(next(x for x in p["cities"] if x["city"] == city)["worst"], p.get("src"))
                for p in trend if any(x["city"] == city for x in p["cities"])]

    SEL = ("()=>[...document.querySelectorAll('#cityTrendBox div[style*=\"flex-end\"] > div')]"
           ".map(d=>{const i=d.querySelector('i');const cs=getComputedStyle(i);"
           "return {bg:cs.backgroundColor,op:cs.opacity}})")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
    except Exception as e:
        print("SKIP: chromium unavailable:", str(e)[:120]); return 0
    fails = []
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="networkidle"); pg.wait_for_timeout(3000)
        if pg.evaluate("()=>document.body.innerText.includes('30-day heat trend')"):
            fails.append("national view shows a trend strip")
        seen = {}
        for city in ["Mumbai", "Chennai"]:
            pg.evaluate(f"async()=>{{await openCity('{city}')}}")
            pg.wait_for_function("()=>{const el=document.querySelector('#cityTrendBox');"
                                 "return el&&el.innerHTML.includes('Each bar')}", timeout=20000)
            got = pg.evaluate(SEL); seen[city] = got
            exp = [{"bg": COL[w], "op": "1" if s == "live-scan" else "0.6"} for w, s in expected(city)]
            if got != exp:
                fails.append(f"{city} strip does not match its API series")
        if seen.get("Mumbai") == seen.get("Chennai"):
            fails.append("Mumbai and Chennai strips identical")
        if errs:
            fails.append("page errors: " + "; ".join(errs[:3]))
        b.close()
    if fails:
        print("FAIL:"); [print(" -", f) for f in fails]; return 1
    print("PASS: national view has no trend strip; Mumbai and Chennai strips are "
          "city-scoped, colour/opacity-exact, and distinct; no page errors.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
