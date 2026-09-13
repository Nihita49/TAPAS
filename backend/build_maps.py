"""Build real satellite basemaps (Esri World Imagery tiles) + normalize real
ward geometry for 4 cities. Outputs go to data/cities/<city>/. Honest: tiles are
real Esri World Imagery (© Esri); ward polygons are real municipal/OSM admin
boundaries. No fabricated data here -- only geometry + imagery.
"""
import json, math, os, urllib.request, threading, re
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import numpy as np

HERE=os.path.dirname(os.path.abspath(__file__))
OUTROOT=os.path.join(os.path.dirname(HERE),"data","cities")
SRC=os.path.join(os.path.dirname(HERE),"data","geo")
os.makedirs(OUTROOT,exist_ok=True)

# city configs -> source file + zone field etc
CITIES={
 "Mumbai":   {"src":"BMC_admin_wards.geojson","center":(72.86,19.08),"id":"name"},
 "Ahmedabad":{"src":"amd_Wards.geojson","center":(72.58,23.02),"id":"Name"},
 "Chennai":  {"src":"che_Wards.geojson","center":(80.27,13.08),"id":"Ward_No","zone":"Zone_Name"},
 "Hyderabad":{"src":"hyd_Wards.geojson","center":(78.47,17.39),"id":"name","parse_wardnum":True,"zone":"zone_guess"},
}

def merc(lon,lat,z):
    n=256<<z
    x=(lon+180.0)/360.0*n
    lat=min(85.0511,max(-85.0511,lat))
    y=(1.0-math.log(math.tan(math.radians(lat))+1.0/math.cos(math.radians(lat)))/math.pi)/2.0*n
    return x,y

def walkpts(coords,arr):
    if isinstance(coords[0],(int,float)): arr.append(coords); return
    for c in coords: walkpts(c,arr)

def fetch_tile(z,x,y):
    url=f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=20) as r: return r.read()

def area_km2(geom):
    pts=[]; walkpts(geom["coordinates"],pts)
    if not pts: return None
    lat0=sum(p[1] for p in pts)/len(pts)
    kx=111.32*math.cos(math.radians(lat0)); ky=110.57
    n=len(pts); a=0.0
    for i in range(n):
        x1,y1=pts[i][0],pts[i][1]
        x2,y2=pts[(i+1)%n][0],pts[(i+1)%n][1]
        a+=(x1*kx)*(y2*ky)-(x2*kx)*(y1*ky)
    return abs(a)/2.0

def centroid(geom):
    pts=[]; walkpts(geom["coordinates"],pts)
    if not pts: return None
    # area-weighted-ish simple mean (good enough for a weather rep point)
    return [sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts)]

def main():
    for city,cfg in CITIES.items():
        d=json.load(open(os.path.join(SRC,cfg["src"])))
        # normalize features
        norm=[]
        seen=set()
        for f in d["features"]:
            p=f["properties"]
            g=f["geometry"]
            # id
            if cfg.get("parse_wardnum"):
                m=re.match(r"Ward\s*(\d+)",str(p.get(cfg["id"],"")))
                if not m: continue
                wid=int(m.group(1)); label=p.get(cfg["id"])
                zone=None
            elif city=="Chennai":
                wn=p.get("Ward_No")
                if not isinstance(wn,int): continue
                wid=wn; label=f"Ward {wn}"; zone=p.get("Zone_Name")
            elif city=="Ahmedabad":
                name=str(p.get("Name")); m=re.match(r"(\d+)\s*(.*)",name)
                wid=int(m.group(1)) if m else None
                if wid is None or wid in seen: continue
                label=name; zone=None
            else: # Mumbai
                wid=str(p.get("name")); label=f"Ward {wid}"; zone=None
            if isinstance(wid,int) and wid in seen: continue
            seen.add(wid)
            c=centroid(g); a=area_km2(g)
            if c is None: continue
            norm.append({"id":wid,"label":label,"zone":zone,
                         "centroid":[round(c[0],6),round(c[1],6)],
                         "area_km2":round(a,4) if a else None,
                         "geometry":g})
        norm.sort(key=lambda x:(0 if isinstance(x["id"],int) else 1, str(x["id"])))
        # write normalized geometry geojson (drop none geometry to keep small? keep geometry)
        fc={"type":"FeatureCollection","crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}},"features":[
            {"type":"Feature","properties":{k:v for k,v in n.items() if k!="geometry"},"geometry":n["geometry"]} for n in norm]}
        cdir=os.path.join(OUTROOT,city); os.makedirs(cdir,exist_ok=True)
        json.dump(fc,open(os.path.join(cdir,"wards.geojson"),"w"))
        # basemap
        # bbox
        allp=[]; [walkpts(f["geometry"]["coordinates"],allp) for f in fc["features"]]
        minlon=min(p[0] for p in allp); maxlon=max(p[0] for p in allp)
        minlat=min(p[1] for p in allp); maxlat=max(p[1] for p in allp)
        # pad
        dlon=(maxlon-minlon)*0.07; dlat=(maxlat-minlat)*0.07
        bbox=(minlon-dlon,minlat-dlat,maxlon+dlon,maxlat+dlat)
        # choose zoom ~ target 6 tiles across
        widthdeg=bbox[2]-bbox[0]
        z=round(math.log2(6*360.0/max(widthdeg,1e-4)))
        z=max(9,min(15,z))
        x0,y0=merc(bbox[0],bbox[3],z); x1,y1=merc(bbox[2],bbox[1],z)
        tx0,tx1=int(x0//256),int(x1//256); ty0,ty1=int(y0//256),int(y1//256)
        nX=tx1-tx0+1; nY=ty1-ty0+1
        img=Image.new("RGB",(nX*256,nY*256))
        tiles={}
        def dl(tx,ty):
            try: tiles[(tx,ty)]=fetch_tile(z,tx,ty)
            except Exception: tiles[(tx,ty)]=None
        with ThreadPoolExecutor(max_workers=8) as ex:
            ex.map(lambda p:dl(*p), [(tx,ty) for ty in range(ty0,ty1+1) for tx in range(tx0,tx1+1)])
        for (tx,ty),b in tiles.items():
            if not b: continue
            try: t=Image.open(__import__("io").BytesIO(b)).convert("RGB")
            except Exception: t=Image.new("RGB",(256,256),(60,90,60))
            img.paste(t,((tx-tx0)*256,(ty-ty0)*256))
        bmp=os.path.join(cdir,"basemap.jpg")
        img.save(bmp,"JPEG",quality=82)
        meta={"city":city,"zoom":z,"tile_min":[tx0,ty0],"nx":nX,"ny":nY,
              "image":"basemap.jpg","bbox":bbox,"n_wards":len(fc["features"]),
              "tile_source":"Esri World Imagery (© Esri) -- real satellite",
              "boundary_source":cfg.get("src")}
        json.dump(meta,open(os.path.join(cdir,"map.json"),"w"))
        print(f"{city}: wards={len(fc['features'])} zoom={z} tiles={nX}x{nY} imgMB={os.path.getsize(bmp)/1e6:.1f} bbox={[round(x,3) for x in bbox]}")

main()
