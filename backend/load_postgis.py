"""Load the real ward polygons + attributes into PostGIS (schema: sql/schema.sql).

Two modes:
  1) DATABASE_URL set  -> applies sql/schema.sql and upserts all wards/cities
     directly (idempotent), used by docker-compose before the server starts.
  2) otherwise         -> emits SQL on stdout:  python3 load_postgis.py | psql -d tapas

Runtime read path lives in pg_store.py (app falls back to JSON when DB absent).
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault("TAPAS_NOSCHED", "1")
import datastore

def q(s):
    if s is None: return "NULL"
    return "'" + str(s).replace("'", "''") + "'"

def sql_rows():
    rows=[]
    rows.append("BEGIN;")
    for cid, c in datastore.STORE.cities.items():
        cfg = c["config"]
        lon, lat = cfg["centre"]
        keep = {"census2011": cfg.get("census2011"), "vuln": cfg.get("vuln"),
                "ac_ref": cfg.get("ac_ref")}
        rows.append(f"INSERT INTO cities (id,name,state,centre,config) VALUES "
              f"({q(cid)},{q(cfg['name'])},{q(cfg['state'])},"
              f"ST_SetSRID(ST_MakePoint({lon},{lat}),4326),"
              f"{q(json.dumps(keep))}::jsonb) ON CONFLICT (id) DO UPDATE SET "
              f"config=EXCLUDED.config;")
        for w in c["wards"]:
            geom = json.dumps(w["geom"])
            sat = json.dumps({k: v for k, v in (w.get("sat") or {}).items()})
            cx, cy = (w.get("centroid") or [lon, lat])
            rows.append(f"INSERT INTO wards (city_id,ward_id,label,zone,area_km2,centroid,geom,sat) VALUES "
                  f"({q(cid)},{q(w['id'])},{q(w.get('label'))},{q(w.get('zone'))},"
                  f"{w.get('area_km2') if w.get('area_km2') is not None else 'NULL'},"
                  f"ST_SetSRID(ST_MakePoint({cx},{cy}),4326),"
                  f"ST_SetSRID(ST_Force2D(ST_GeomFromGeoJSON({q(geom)})),4326),"
                  f"{q(sat)}::jsonb) ON CONFLICT (city_id,ward_id) DO UPDATE SET "
                  f"sat=EXCLUDED.sat, geom=EXCLUDED.geom;")
    rows.append("COMMIT;")
    return rows

def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        for r in sql_rows(): print(r)
        return
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=10)
    cur = conn.cursor()
    with open(os.path.join(HERE, "sql", "schema.sql")) as f:
        cur.execute(f.read())
    conn.commit()
    rows = sql_rows()
    cur.execute("\n".join(rows))
    conn.commit()
    cur.execute("SELECT count(*) FROM wards")
    print("loaded wards into PostGIS:", cur.fetchone()[0])
    conn.close()

if __name__ == "__main__":
    main()
