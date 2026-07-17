-- reset_seinecrops.sql

-- nb03 — composites mensuels et NDVI par date
DROP TABLE IF EXISTS derived.s2_parcelles_ndvi_dates;
DROP TABLE IF EXISTS derived.s2_parcelles_monthly;

-- nb01 — nb01 recrée déjà cette table elle-même (DROP + CREATE AS SELECT
-- en 4.3), ce DROP est redondant mais explicite un état propre avant rejeu
DROP TABLE IF EXISTS derived.rpg_parcelles_aoi;

-- raw.* : pas de DROP automatique dans nb01 (chargement via dump PGDUMP
-- sans clause de remplacement) — indispensable avant rejeu complet, sinon
-- échec "relation already exists" en 4.1/4.2
DROP TABLE IF EXISTS raw.rpg_parcelles;
DROP TABLE IF EXISTS raw.aoi_seinecrops;
