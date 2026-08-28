-- db/migrations/0007_derived_ddl_zonal.sql
BEGIN;

CREATE TABLE IF NOT EXISTS derived.s2_parcelles_monthly (
    id_parcel  TEXT  NOT NULL,
    mois       TEXT  NOT NULL,
    variable   TEXT  NOT NULL,
    mean       REAL,
    std        REAL,
    p10        REAL,
    p90        REAL,
    PRIMARY KEY (id_parcel, mois, variable)
);

CREATE TABLE IF NOT EXISTS derived.s2_parcelles_completude (
    id_parcel             TEXT  NOT NULL,
    mois                  TEXT  NOT NULL,
    n_dates_valides_moy   REAL,
    pct_pixels_couverts   REAL,
    PRIMARY KEY (id_parcel, mois)
);

CREATE TABLE IF NOT EXISTS derived.s2_parcelles_ndvi_dates (
    id_parcel  TEXT  NOT NULL,
    date       DATE  NOT NULL,
    mean       REAL,
    std        REAL,
    n_pixels   INTEGER,
    PRIMARY KEY (id_parcel, date)
);

INSERT INTO public.schema_migrations (version, description)
VALUES ('0007', 'DDL de derived.s2_parcelles_monthly, _completude, _ndvi_dates - extrait de zonal.py')
ON CONFLICT (version) DO NOTHING;

COMMIT;
