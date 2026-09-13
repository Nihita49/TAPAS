"""Live PostGIS read path. When DATABASE_URL is set and reachable, the app
serves ward geometry FROM PostGIS (ST_AsGeoJSON) and reports /api/db/status;
otherwise everything transparently falls back to the JSON datastore.
"""
import json, os

def _conn():
    url = os.environ.get("DATABASE_URL")
    if not url: return None
    try:
        import psycopg2
        return psycopg2.connect(url, connect_timeout=3)
    except Exception:
        return None

def pg_status():
    url_set = bool(os.environ.get("DATABASE_URL"))
    conn = _conn()
    if not conn:
        return {"live": False, "database_url_set": url_set,
                "reason": "DATABASE_URL unset or connection failed - runtime uses JSON datastore"}
    try:
        cur = conn.cursor()
        cur.execute("SELECT postgis_version()")
        ver = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM wards")
        nwards = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM cities")
        ncities = cur.fetchone()[0]
        return {"live": True, "database_url_set": True, "postgis_version": ver,
                "cities": ncities, "wards": nwards}
    except Exception as e:
        return {"live": False, "database_url_set": True, "reason": str(e)[:160]}
    finally:
        conn.close()

def wards_geometry(city):
    """{ward_id: GeoJSON geometry dict} from PostGIS, or None if DB absent."""
    conn = _conn()
    if not conn: return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT ward_id, ST_AsGeoJSON(geom)::text FROM wards WHERE city_id=%s", (city,))
        rows = cur.fetchall()
        return {str(r[0]): json.loads(r[1]) for r in rows} if rows else None
    except Exception:
        return None
    finally:
        conn.close()
