"""Fetch REAL MODIS Terra products per ward via NASA GIBS (no API key needed).

Layers (verified live in GIBS WMTS capabilities):
  MODIS_Terra_Land_Surface_Temp_Day  -- daily daytime land-surface temperature (MOD11A1, 1 km)
  MODIS_Terra_NDVI_8Day              -- rolling 8-day vegetation index (MOD13Q1)

Method: one WMS GetMap (EPSG:4326 plate carree) per city per layer; PNG pixels
are decoded back to physical values with NASA's official colour maps
(gibs.earthdata.nasa.gov/colormaps) -- exact palette match, nearest-key fallback.
Per-ward values = mean of decoded pixels inside the real ward polygon
(exterior rings, PIL polygon mask; same approach as sat_attrs.py).

Outputs data/cities/<City>/modis.json:
  {"lst": {"date":..,"wards":{id: degC}}, "ndvi": {"date":..,"wards":{id: value}},
   "provenance": ...}
Provenance is stamped so the UI can say REAL MODIS with the exact date.
"""
import json, math, os, re, sys, datetime as dt
import numpy as np
import requests
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
CITYROOT = os.path.join(ROOT, "data", "cities")
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
CM = {
    "lst":  ("MODIS_Terra_Land_Surface_Temp_Day", "https://gibs.earthdata.nasa.gov/colormaps/v1.3/MODIS_Land_Surface_Temp.xml", 0.02),    # DN -> K
    "ndvi": ("MODIS_Terra_NDVI_8Day",             "https://gibs.earthdata.nasa.gov/colormaps/v1.0/MODIS_NDVI.xml",              1.0),     # v1.0 cmap values already physical
}
UA = {"User-Agent": "TAPAS-HeatEWS/1.0 (urban heat early-warning research prototype)"}

def parse_val(s):
    s = s.strip()
    m = re.match(r"^[\[\(]([-\d.eE+]+)\s*,\s*([-\d.eE+]+)[\]\)]$", s)
    if m: return (float(m.group(1)) + float(m.group(2))) / 2.0
    try: return float(s)
    except Exception: return None

def build_lut(url):
    cm = requests.get(url, timeout=60, headers=UA).text
    lut = {}
    for e in re.findall(r"<ColorMapEntry([^>]*)/>", cm):
        if "nodata" in e or 'transparent="true"' in e: continue
        rgb = re.search(r'rgb="\(?\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)?"', e)
        val = re.search(r'(?:sourceValue|value)="([^"]+)"', e)
        if rgb and val:
            v = parse_val(val.group(1))
            if v is not None:
                lut[int(rgb.group(1)) * 65536 + int(rgb.group(2)) * 256 + int(rgb.group(3))] = v
    if not lut: raise RuntimeError("empty colormap " + url)
    return lut

def decode(img_rgba, lut):
    """Return (values float32 HxW, valid mask) decoding palette PNG via lut."""
    a = np.asarray(img_rgba)
    keys = a[:, :, 0].astype(np.int64) * 65536 + a[:, :, 1].astype(np.int64) * 256 + a[:, :, 2].astype(np.int64)
    valid = a[:, :, 3] > 200
    lk = np.array(sorted(lut)); lv = np.array([lut[k] for k in lk])
    uniq, inv = np.unique(keys, return_inverse=True)
    pos = np.clip(np.searchsorted(lk, uniq), 1, len(lk) - 1)
    near = np.where(np.abs(uniq - lk[pos - 1]) <= np.abs(lk[pos] - uniq), pos - 1, pos)
    vals = lv[near][inv].reshape(keys.shape).astype(np.float32)
    exact = np.isin(uniq, lk)[inv].reshape(keys.shape)
    return vals, valid & exact

def getmap(layer, bbox, size, day):
    r = requests.get(WMS, timeout=120, headers=UA, params={
        "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": layer,
        "STYLES": "", "CRS": "CRS:84", "BBOX": ",".join(str(x) for x in bbox),
        "WIDTH": size, "HEIGHT": size, "FORMAT": "image/png", "TRANSPARENT": "TRUE",
        "TIME": day})
    r.raise_for_status()
    from io import BytesIO
    return Image.open(BytesIO(r.content)).convert("RGBA")

def ring_px(geom, bbox, size):
    lon0, lat0, lon1, lat1 = bbox
    rings = geom["coordinates"] if geom["type"] == "Polygon" else [c[0] for c in geom["coordinates"]]
    out = []
    for ring in rings:
        px = [(((float(c[0]) - lon0) / (lon1 - lon0)) * size, ((lat1 - float(c[1])) / (lat1 - lat0)) * size)
              for c in ring if len(c) >= 2]
        if len(px) >= 3: out.append(px)
    return out

def fetch_city(city, days_back_max=10):
    wj = json.load(open(os.path.join(CITYROOT, city, "wards.geojson")))
    feats = wj["features"]
    lons = []; lats = []
    for f in feats:
        coords = json.dumps(f["geometry"]["coordinates"])
        for num in re.findall(r"-\d+\.\d+", coords):
            pass
    # cheap bbox from geojson bbox if present else scan
    if "bbox" in wj:
        lon0, lat0, lon1, lat1 = wj["bbox"]
    else:
        def walk(c):
            if isinstance(c, (list, tuple)):
                if len(c) >= 2 and isinstance(c[0], (int, float)):
                    lons.append(float(c[0])); lats.append(float(c[1]))
                else:
                    for x in c: walk(x)
        for f in feats: walk(f["geometry"]["coordinates"])
        lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    pad = 0.05
    bbox = (lon0 - pad, lat0 - pad, lon1 + pad, lat1 + pad)
    out = {"provenance": ("REAL MODIS Terra per-ward values via NASA GIBS WMS (no key); palette PNG decoded "
                          "with official NASA colour maps; mean of pixels inside each real ward polygon"),
           "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")}
    size = 768
    today = dt.date.today()
    for kind, (layer, cm_url, scale) in CM.items():
        lut = build_lut(cm_url)
        # scan the last N days and keep the one with the MOST valid (cloud-free) coverage
        best = None  # (opaque_frac, day, img)
        for back in range(1, days_back_max + 1):
            day = (today - dt.timedelta(days=back)).isoformat()
            try:
                cand = getmap(layer, bbox, size, day)
            except Exception as e:
                print(f"  {city} {kind} {day}: {e}"); continue
            frac = float((np.asarray(cand)[:, :, 3] > 200).mean())
            if frac > 0.3 and (best is None or frac > best[0]):
                best = (frac, day, cand)
            if best and best[0] > 0.9: break   # essentially cloud-free; stop early
        if best is None:
            print(f"  {city} {kind}: NO DATA in last {days_back_max} days (skipped)")
            continue
        _, used, img = best
        vals, valid = decode(img, lut)
        if kind == "lst": vals = vals * scale - 273.15   # -> deg C
        else:             vals = vals * scale            # -> NDVI index
        ward_vals = {}
        for f in feats:
            wid = str(f["properties"].get("id") or f["properties"].get("WARD_NO") or f["properties"].get("name"))
            rings = ring_px(f["geometry"], bbox, size)
            if not rings: continue
            m = Image.new("L", (size, size), 0); d = ImageDraw.Draw(m)
            for px in rings: d.polygon(px, fill=255)
            mask = (np.asarray(m) > 0) & valid
            n = int(mask.sum())
            if n >= 9: ward_vals[wid] = round(float(vals[mask].mean()), 3)
        out[kind] = {"date": used, "wards": ward_vals, "layer": layer}
        vv = list(ward_vals.values())
        print(f"  {city} {kind} {used}: {len(ward_vals)} wards, "
              f"min {min(vv):.2f} med {sorted(vv)[len(vv)//2]:.2f} max {max(vv):.2f}")
    json.dump(out, open(os.path.join(CITYROOT, city, "modis.json"), "w"))

if __name__ == "__main__":
    cities = sys.argv[1:] or ["Mumbai", "Ahmedabad", "Chennai", "Hyderabad"]
    for c in cities:
        print(c); fetch_city(c)
    print("done")
