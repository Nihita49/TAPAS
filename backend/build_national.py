"""Build a real national India GIS basemap (Esri World Street Map) + projection
metadata so the landing view is a proper GIS map (not a hand-drawn flat SVG)."""
import json, math, os, urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import io

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
OUT=os.path.join(ROOT,"data","processed","india_basemap")
os.makedirs(OUT,exist_ok=True)

def merc(lon,lat,z):
    n=256<<z
    x=(lon+180.0)/360.0*n
    lat=max(-85.05,min(85.05,lat))
    y=(1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n
    return x,y

def fetch(z,x,y,style):
    base=("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile"
          if style=="street" else
          "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile")
    req=urllib.request.Request(f"{base}/{z}/{y}/{x}",headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=20) as r: return r.read()

# India bounds lon 68..98 lat 6..37 (subset; continental focus)
bbox=[68.0,6.0,98.0,37.0]
z=5
x0,y0=merc(bbox[0],bbox[3],z); x1,y1=merc(bbox[2],bbox[1],z)
tx0,tx1=int(x0//256),int(x1//256); ty0,ty1=int(y0//256),int(y1//256)
nx=tx1-tx0+1; ny=ty1-ty0+1
img=Image.new("RGB",(nx*256,ny*256))
tiles={}
def dl(t):
    try: tiles[t]=fetch(z,t[0],t[1],"street")
    except Exception: tiles[t]=None
with ThreadPoolExecutor(max_workers=10) as ex:
    list(ex.map(dl,[(x,y) for y in range(ty0,ty1+1) for x in range(tx0,tx1+1)]))
for (x,y),b in tiles.items():
    if not b: continue
    try: t=Image.open(io.BytesIO(b)).convert("RGB")
    except Exception: continue
    img.paste(t,((x-tx0)*256,(y-ty0)*256))
img.save(os.path.join(OUT,"india.jpg"),"JPEG",quality=80)
meta={"zoom":z,"tx":tx0,"ty":ty0,"nx":nx,"ny":ny,
      "W":nx*256,"H":ny*256,"bbox":bbox,
      "source":"Esri World Street Map (real GIS basemap)"}
json.dump(meta,open(os.path.join(OUT,"map.json"),"w"))
print("india national basemap:",meta,"sizeMB",os.path.getsize(os.path.join(OUT,"india.jpg"))/1e6)
