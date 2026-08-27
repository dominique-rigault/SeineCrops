-- db/migrations/0003_derived_rpg_parcelles_aoi_pk.sql
-- ATTENTION : non rejouable après application de 0004 (DROP TABLE bloqué par les FK).
-- Un replay nécessiterait de defaire 0004 au préalable — cas volontairement non géré.
BEGIN;

CREATE TABLE IF NOT EXISTS derived.rpg_parcelles_aoi_dissolved AS
WITH geom_union AS (
    SELECT id_parcel, ST_Union(geom) AS geom
    FROM derived.rpg_parcelles_aoi
    GROUP BY id_parcel
),
attribut_dominant AS (
    SELECT DISTINCT ON (id_parcel)
        id_parcel, surf_parc, code_cultu, code_group, culture_d1, culture_d2, cat_cult_p
    FROM derived.rpg_parcelles_aoi
    ORDER BY id_parcel, ST_Area(geom) DESC
)
SELECT a.id_parcel, a.surf_parc, a.code_cultu, a.code_group,
       a.culture_d1, a.culture_d2, a.cat_cult_p, g.geom
FROM attribut_dominant a
JOIN geom_union g USING (id_parcel);

DROP TABLE derived.rpg_parcelles_aoi;
ALTER TABLE derived.rpg_parcelles_aoi_dissolved RENAME TO rpg_parcelles_aoi;

ALTER TABLE derived.rpg_parcelles_aoi
    ALTER COLUMN id_parcel SET NOT NULL,
    ADD CONSTRAINT pk_rpg_parcelles_aoi PRIMARY KEY (id_parcel);

CREATE INDEX IF NOT EXISTS idx_rpg_parcelles_aoi_geom
    ON derived.rpg_parcelles_aoi USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_rpg_parcelles_aoi_code_cultu
    ON derived.rpg_parcelles_aoi (code_cultu);

INSERT INTO public.schema_migrations (version, description)
VALUES ('0003', 'dissolve doublons id_parcel + PK sur derived.rpg_parcelles_aoi')
ON CONFLICT (version) DO NOTHING;

COMMIT;
