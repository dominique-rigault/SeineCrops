-- db/migrations/0006_derived_ddl_classification_phenologie.sql
-- Formalise en migration versionnée le DDL de derived.parcelles_classification
-- (predict.py::DDL_CLASSIFICATION) et de derived.divergence / derived.phenologie
-- (persist.py::DDL_DIVERGENCE_PHENOLOGIE), jusqu'ici des CREATE TABLE en dur
-- dans le code applicatif (anti-pattern signalé section 9 du gabarit de
-- dossier projet). Les trois tables existent déjà en base (créées par
-- creer_table_classification() / creer_tables_phenologie() lors des premiers
-- runs) : CREATE TABLE IF NOT EXISTS rend cette migration un no-op sur une
-- base existante, et reproductible depuis une base vide.
--
-- DDL repris à l'identique des chaînes Python actuelles (post-migration 0005,
-- sans colonne classe_declaree). Les FOREIGN KEY vers derived.rpg_parcelles_aoi
-- sont déjà posées par 0004 et ne sont pas répétées ici.
BEGIN;

CREATE TABLE IF NOT EXISTS derived.parcelles_classification (
    id_parcel       TEXT PRIMARY KEY,
    classe_predite  TEXT NOT NULL,
    proba_max       REAL NOT NULL,
    split           TEXT NOT NULL CHECK (split IN ('train', 'test')),
    model_version   TEXT NOT NULL,
    date_prediction TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS derived.divergence (
    id_parcel             text PRIMARY KEY,
    dist_classe           double precision,
    seuil_div             double precision,
    divergent             boolean,
    dist_raccord          double precision,
    zone_raccord_orbital  boolean,
    version_pipeline      text NOT NULL,
    date_calcul           timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS derived.phenologie (
    id_parcel         text PRIMARY KEY,
    sos_date          date,
    pos_date          date,
    eos_date          date,
    los_jours         integer,
    sos_en_bord       boolean,
    eos_en_bord       boolean,
    pos_en_bord       boolean,
    fiable            boolean,
    lambda_whittaker  double precision,
    version_pipeline  text NOT NULL,
    date_calcul       timestamp NOT NULL DEFAULT now()
);

INSERT INTO public.schema_migrations (version, description)
VALUES ('0006', 'DDL versionne derived.parcelles_classification, derived.divergence, derived.phenologie')
ON CONFLICT (version) DO NOTHING;

COMMIT;
