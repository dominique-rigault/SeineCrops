-- db/migrations/0005_rpg_parcelles_aoi_classe_declaree.sql
BEGIN;

ALTER TABLE derived.rpg_parcelles_aoi
    ADD COLUMN classe_declaree text;

UPDATE derived.rpg_parcelles_aoi
SET classe_declaree = CASE
    WHEN code_cultu = 'BTN' THEN 'betterave'
    WHEN code_group::int IN (1, 3)   THEN 'cereales_hiver'
    WHEN code_group::int = 2         THEN 'mais'
    WHEN code_group::int = 5         THEN 'colza'
    WHEN code_group::int = 9         THEN 'lin'
    WHEN code_group::int IN (18, 19) THEN 'prairie'
    WHEN code_group::int = 25        THEN 'legumes_fleurs'
    ELSE 'autres'
END;

ALTER TABLE derived.rpg_parcelles_aoi
    ALTER COLUMN classe_declaree SET NOT NULL;

ALTER TABLE derived.parcelles_classification DROP COLUMN classe_declaree;
ALTER TABLE derived.divergence               DROP COLUMN classe_declaree;
ALTER TABLE derived.phenologie               DROP COLUMN classe_declaree;

INSERT INTO public.schema_migrations (version, description)
VALUES ('0005', 'centralisation classe_declaree sur rpg_parcelles_aoi')
ON CONFLICT (version) DO NOTHING;

COMMIT;
