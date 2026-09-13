"""Derive REAL per-ward satellite attributes by sampling each city's satellite
basemap (Esri World Imagery) pixels inside each real ward polygon.
  veg_frac : vegetation (green) pixel fraction  -> informs greenspace / cool surface
  wat_frac : water pixel fraction
  built_frac: low-veg built/barren fraction
Method: true-colour vegetation threshold g>r,g>b; documented, reproducible.
Provenance = 'satellite-derived (real basemap)', not a hand-typed estimate.
"""
import json, os, math
import numpy as np
from PIL import Image, ImageDraw
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
CITYROOT=os.path.join(ROOT,"data","cities")

def merc(lon,lat,z):
    n=256<<z
    x=(lon+180.0)/360.0*n
    lat=min(85.0511,max(-85.0511,lat))
    y=(1.0-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2.0*n
    return x,y

def ring_pts(geom,z,tx0,ty0):
    """Return list of pixel polygons (each a list of (px,py)) for a Polygon or
    MultiPolygon using only the exterior ring of each part."""
    coords=geom["coordinates"]
    rings = coords if geom["type"]=="Polygon" else [c[0] for c in coords]
    out=[]
    for ring in rings:
        px=[]
        for c in ring:
            if len(c)>=2:
                X,Y=merc(float(c[0]),float(c[1]),z); px.append((X-tx0*256,Y-ty0*256))
        if len(px)>=3: out.append(px)
    return out

def analyze(city):
    mm=json.load(open(os.path.join(CITYROOT,city,"map.json")))
    z=mm["zoom"]; tx0,ty0=mm["tile_min"]
    img=np.array(Image.open(os.path.join(CITYROOT,city,"basemap.jpg")).convert("RGB")).astype(np.int16)
    H,W,_=img.shape
    wj=json.load(open(os.path.join(CITYROOT,city,"wards.geojson")))
    # vectorized pixel class map
    r=img[:,:,0]; g=img[:,:,1]; b=img[:,:,2]
    veg=(g>70)&(g>r+8)&(g>b+4)
    water=(b>r)&(b>=g)&((b+r)>180)&(g<220)
    results=[]
    for f in wj["features"]:
        p=f["properties"]; ge=f["geometry"]
        polys=ring_pts(ge,z,tx0,ty0)
        if not polys: results.append((p,None)); continue
        # mask in local bbox
        xs=[pt[0] for pol in polys for pt in pol]; ys=[pt[1] for pol in polys for pt in pol]
        x0,x1=int(max(0,min(xs))),int(min(W-1,max(xs)))
        y0,y1=int(max(0,min(ys))),int(min(H-1,max(ys)))
        if x1<=x0 or y1<=y0: results.append((p,None)); continue
        m=Image.new("L",(x1-x0+1,y1-y0+1),0)
        d=ImageDraw.Draw(m)
        for pol in polys:
            d.polygon([(px-x0,py-y0) for px,py in pol],fill=255)
        mask=np.array(m)>0
        rr=r[y0:y1+1,x0:x1+1][mask]; gg=g[y0:y1+1,x0:x1+1][mask]; bb=b[y0:y1+1,x0:x1+1][mask]
        if rr.size==0: results.append((p,None)); continue
        vg=((gg>70)&(gg>rr+8)&(gg>bb+4)).mean()
        wa=((bb>rr)&(bb>=gg)&((bb+rr)>180)&(gg<220)).mean()
        # bright built surfaces (low veg, high luminance, warm-neutral)
        lum=(rr+gg+bb)/3.0
        dry=(~((gg>70)&(gg>rr+8)&(gg>bb+4)))&(~((bb>rr)&(bb>=gg)))&(lum>90)
        results.append((p,{"veg_frac":round(float(vg),3),"wat_frac":round(float(wa),3),
                            "built_frac":round(float(dry.mean()),3),
                            "provenance":"satellite-derived (real basemap, true-colour veg threshold)"}))
    # merge into wards.geojson
    for (p,sat),f in zip(results,wj["features"]):
        f["properties"]["satellite"]=sat
    json.dump(wj,open(os.path.join(CITYROOT,city,"wards.geojson"),"w"))
    ok=sum(1 for (p,s) in results if s)
    print(f"{city}: wards with sat attrs {ok}/{len(results)}")
    return results

for city in ["Mumbai","Ahmedabad","Chennai","Hyderabad"]:
    try: analyze(city)
    except Exception as e: import traceback; traceback.print_exc(); print(city,"ERR",e)
