"""Fetch REAL WUDAPT/Demuzere et al. (2022) global LCZ (100 m) per ward.
Source: Zenodo record 6364594, lcz_filter_v1.tif (Cloud-Optimized GeoTIFF,
EPSG:4326) - read via HTTP range requests, no full download.
Per ward: mode LCZ_Filter class inside the real ward polygon + class share of
built classes. Output data/cities/<City>/lcz.json.

Run: python3 fetch_lcz.py
"""
import json, os, collections
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from PIL import Image, ImageDraw

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
CITYROOT=os.path.join(ROOT,"data","cities")
URL="/vsicurl/https://zenodo.org/api/records/6364594/files/lcz_filter_v1.tif/content"
LCZ_NAME={1:"Compact highrise",2:"Compact midrise",3:"Compact lowrise",4:"Open highrise",
 5:"Open midrise",6:"Open lowrise",7:"Lightweight low-rise",8:"Large lowrise",
 9:"Sparsely built",10:"Heavy industry",11:"Dense trees",12:"Scattered trees",
 13:"Bush/scrub",14:"Low plants",15:"Bare rock/paved",16:"Bare soil/sand",17:"Water"}
BUILT={1,2,3,4,5,6,7,8,9,10,15}

def ring_px(geom,bbox,size_x,size_y):
    lon0,lat0,lon1,lat1=bbox
    rings=geom["coordinates"] if geom["type"]=="Polygon" else [c[0] for c in geom["coordinates"]]
    out=[]
    for ring in rings:
        px=[(((float(c[0])-lon0)/(lon1-lon0))*size_x, ((lat1-float(c[1]))/(lat1-lat0))*size_y)
            for c in ring if len(c)>=2]
        if len(px)>=3: out.append(px)
    return out

def main():
    with rasterio.open(URL) as ds:
        for city in ["Mumbai","Ahmedabad","Chennai","Hyderabad"]:
            wj=json.load(open(os.path.join(CITYROOT,city,"wards.geojson")))
            feats=wj["features"]
            lons=[];lats=[]
            def walk(c):
                if isinstance(c,(list,tuple)):
                    if c and isinstance(c[0],(int,float)): lons.append(c[0]);lats.append(c[1])
                    else:
                        for x in c: walk(x)
            for f in feats: walk(f["geometry"]["coordinates"])
            pad=0.02
            bbox=(min(lons)-pad,min(lats)-pad,max(lons)+pad,max(lats)+pad)
            win=from_bounds(*bbox, ds.transform)
            arr=ds.read(1, window=win)
            H,W=arr.shape
            out={}
            for f in feats:
                wid=str(f["properties"]["id"])
                polys=ring_px(f["geometry"],bbox,W,H)
                if not polys: continue
                m=Image.new("L",(W,H),0); d=ImageDraw.Draw(m)
                for px in polys: d.polygon(px,fill=255)
                mask=np.asarray(m)>0
                vals=arr[mask]
                vals=vals[vals>0]
                if len(vals)<9: continue
                cnt=collections.Counter(vals.tolist())
                mode=cnt.most_common(1)[0][0]
                built_share=round(sum(v for k,v in cnt.items() if k in BUILT)/len(vals),3)
                out[wid]={"lcz":int(mode),"lcz_name":LCZ_NAME.get(int(mode),"?"),
                          "built_share":built_share,"n_px":int(len(vals))}
            json.dump({"source":"Demuzere et al. 2022 global LCZ map v1 (WUDAPT), 100 m, Zenodo 6364594, "
                                "LCZ_Filter band, per-ward mode class inside real ward polygon",
                       "fetched":"2026-09-10","wards":out},
                      open(os.path.join(CITYROOT,city,"lcz.json"),"w"))
            dist=collections.Counter(v["lcz"] for v in out.values())
            print(city, len(out), "wards w/ LCZ; top classes:", dist.most_common(4))

if __name__=="__main__":
    main()
