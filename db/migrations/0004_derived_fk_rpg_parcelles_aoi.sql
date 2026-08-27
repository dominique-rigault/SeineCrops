-- db/migrations/0004_derived_fk_rpg_parcelles_aoi.sql
BEGIN;

ALTER TABLE derived.parcelles_classification
    ADD CONSTRAINT fk_classification_parcelle
    FOREIGN KEY (id_parcel) REFERENCES derived.rpg_parcelles_aoi(id_parcel);

ALTER TABLE derived.divergence
    ADD CONSTRAINT fk_divergence_parcelle
    FOREIGN KEY (id_parcel) REFERENCES derived.rpg_parcelles_aoi(id_parcel);

ALTER TABLE derived.phenologie
    ADD CONSTRAINT fk_phenologie_parcelle
    FOREIGN KEY (id_parcel) REFERENCES derived.rpg_parcelles_aoi(id_parcel);

ALTER TABLE derived.s2_parcelles_monthly
    ADD CONSTRAINT fk_s2_mensuel_parcelle
    FOREIGN KEY (id_parcel) REFERENCES derived.rpg_parcelles_aoi(id_parcel);

ALTER TABLE derived.s2_parcelles_completude
    ADD CONSTRAINT fk_s2_completude_parcelle
    FOREIGN KEY (id_parcel) REFERENCES derived.rpg_parcelles_aoi(id_parcel);

ALTER TABLE derived.s2_parcelles_ndvi_dates
    ADD CONSTRAINT fk_s2_ndvi_parcelle
    FOREIGN KEY (id_parcel) REFERENCES derived.rpg_parcelles_aoi(id_parcel);

INSERT INTO public.schema_migrations (version, description)
VALUES ('0004', 'FK des 6 tables filles vers derived.rpg_parcelles_aoi')
ON CONFLICT (version) DO NOTHING;

COMMIT;
