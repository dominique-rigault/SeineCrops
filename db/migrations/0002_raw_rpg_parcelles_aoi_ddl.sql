-- db/migrations/0002_raw_rpg_parcelles_aoi_ddl.sql
BEGIN;

CREATE TABLE IF NOT EXISTS raw.rpg_parcelles (
    ogc_fid      serial,
    wkb_geometry geometry(MultiPolygon, 2154),
    id_parcel    character varying,
    surf_parc    double precision,
    code_cultu   character varying,
    code_group   character varying,
    culture_d1   character varying,
    culture_d2   character varying,
    cat_cult_p   character varying,
    CONSTRAINT rpg_parcelles_pk PRIMARY KEY (ogc_fid)
);

CREATE INDEX IF NOT EXISTS rpg_parcelles_wkb_geometry_geom_idx
    ON raw.rpg_parcelles USING gist (wkb_geometry);

CREATE TABLE IF NOT EXISTS raw.aoi_seinecrops (
    ogc_fid      serial,
    wkb_geometry geometry(Polygon, 2154),
    CONSTRAINT aoi_seinecrops_pk PRIMARY KEY (ogc_fid)
);

CREATE INDEX IF NOT EXISTS aoi_seinecrops_wkb_geometry_geom_idx
    ON raw.aoi_seinecrops USING gist (wkb_geometry);

INSERT INTO public.schema_migrations (version, description)
VALUES ('0002', 'DDL explicite raw.rpg_parcelles et raw.aoi_seinecrops')
ON CONFLICT (version) DO NOTHING;

COMMIT;
