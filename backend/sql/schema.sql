-- TAPAS Heat EWS -- PostGIS production schema
-- The runtime is file-based (JSON) for portability; this schema is the
-- production-deploy path for real PostGIS ward polygons (spec nav section).
-- Load with:  python3 backend/load_postgis.py  (writes SQL, pipe into psql)
--   psql -d tapas -f backend/sql/schema.sql
--   python3 backend/load_postgis.py | psql -d tapas

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS cities (
  id          text PRIMARY KEY,
  name        text NOT NULL,
  state       text NOT NULL,
  centre      geometry(Point, 4326),
  config      jsonb NOT NULL DEFAULT '{}'::jsonb   -- census2011, vuln, ac_ref
);

CREATE TABLE IF NOT EXISTS wards (
  city_id     text NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
  ward_id     text NOT NULL,
  label       text,
  zone        text,
  area_km2    double precision,
  centroid    geometry(Point, 4326),
  geom        geometry(MultiPolygon, 4326) NOT NULL,  -- real ward polygons
  sat         jsonb NOT NULL DEFAULT '{}'::jsonb,     -- built/veg/wat + MODIS lst/ndvi
  PRIMARY KEY (city_id, ward_id)
);
CREATE INDEX IF NOT EXISTS wards_geom_idx ON wards USING GIST (geom);

-- Hourly scored snapshots (the live scan writes here in production)
CREATE TABLE IF NOT EXISTS ward_scores (
  ts          timestamptz NOT NULL,
  city_id     text NOT NULL,
  ward_id     text NOT NULL,
  htsi        double precision,
  band        text,
  h           double precision,
  v           double precision,
  e           double precision,
  ac          double precision,
  utci        double precision,
  mort        double precision,
  hosp        double precision,
  confidence  double precision,
  PRIMARY KEY (ts, city_id, ward_id)
);

-- Alert outbox (event + digest, with cross-suppression metadata)
CREATE TABLE IF NOT EXISTS alerts (
  id             bigserial PRIMARY KEY,
  ts             timestamptz NOT NULL DEFAULT now(),
  type           text NOT NULL,             -- 'event' | 'digest'
  city_id        text,
  ward_id        text,
  band           text,
  message        text,
  channel_result text,                      -- 'SENT via Twilio ...' | 'SIMULATED ...'
  suppressed     integer DEFAULT 0          -- digest cross-suppression count
);
