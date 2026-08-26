# SeineCrops - Affinement MCD/MPD (sprint S2, mission P1)

Point de départ : `dictionnaire_donnees_postgis.md`, qui documente un
schéma déjà écrit (base `seinecrops`, deux schémas `raw`/`derived`). Ce
document ne repart pas de zéro : il construit le MCD manquant à partir de
ce dictionnaire, puis affine le MPD (typage, contraintes, FK,
normalisation), en reprenant le point non tranché signalé dans
`cadrage_bascule_postgis.md` : la duplication de `classe_declaree` sur
trois tables.

---

## 1. Ce que le dictionnaire actuel donne, et ce qui manque

Le dictionnaire existant est un bon inventaire de colonnes, mais ce n'est
pas un MCD : les relations entre tables (`§ Relations entre tables`) sont
explicitement déclarées **conventionnelles**, pas contraintes - « rien
n'empêche en théorie une incohérence entre `derived.parcelles_classification`
et `derived.rpg_parcelles_aoi` ». La démarche « MCD avant MPD » (canevas
`gabarit_dossier_projet.md`, section 9) demande d'inverser cet ordre :
poser d'abord les entités et leurs cardinalités réelles, puis vérifier que
le DDL actuel les respecte - ici il ne les respecte pas encore
(absence de PK sur `rpg_parcelles_aoi`, donc absence de FK possible en
l'état).

---

## 2. Modèle logique affiné (relations contraintes, pas un MCD)

Ce diagramme reprend celui de `dictionnaire_donnees_postgis.md` en
remplaçant les relations « convention, non FK » par des relations
contraintes. C'est un modèle logique/relationnel affiné, pas un MCD - un
véritable MCD, construit depuis l'usage métier indépendamment du schéma
déjà écrit, fait l'objet d'un document séparé :
`MCD_seinecrops_conceptuel.md`.

```mermaid
erDiagram
    RPG_PARCELLES_AOI ||--o| CLASSIFICATION : "id_parcel"
    RPG_PARCELLES_AOI ||--o| DIVERGENCE : "id_parcel"
    RPG_PARCELLES_AOI ||--o| PHENOLOGIE : "id_parcel"
    RPG_PARCELLES_AOI ||--o{ S2_MENSUEL : "id_parcel"
    RPG_PARCELLES_AOI ||--o{ S2_COMPLETUDE : "id_parcel"
    RPG_PARCELLES_AOI ||--o{ S2_NDVI_DATES : "id_parcel"

    RPG_PARCELLES_AOI {
        text id_parcel PK
        double surf_parc
        text code_cultu
        text code_group
        text classe_declaree "déplacée ici, cf. §3"
        geometry geom
    }
    CLASSIFICATION {
        text id_parcel PK_FK
        text classe_predite
        real proba_max
        text split
    }
    DIVERGENCE {
        text id_parcel PK_FK
        double dist_classe
        boolean divergent
    }
    PHENOLOGIE {
        text id_parcel PK_FK
        date sos_date
        date eos_date
        integer los_jours
    }
    S2_MENSUEL {
        text id_parcel FK
        text mois PK
        text variable PK
        double mean
    }
    S2_COMPLETUDE {
        text id_parcel FK
        text mois PK
        numeric pct_pixels_couverts
    }
    S2_NDVI_DATES {
        text id_parcel FK
        date date PK
        double mean
    }
```

Différence avec le diagramme du dictionnaire existant : les relations
`"id_parcel (convention, non FK)"` deviennent des relations contraintes
(`FK`), et `classe_declaree` disparaît de `CLASSIFICATION`, `DIVERGENCE`
et `PHENOLOGIE` pour ne plus vivre que sur `RPG_PARCELLES_AOI`.

---

## 3. Décision : normalisation de `classe_declaree`

Point non tranché du cadrage, tranché ici.

| Décision | Alternative écartée | Justification |
|---|---|---|
| `classe_declaree` stockée une seule fois, sur `derived.rpg_parcelles_aoi` (colonne calculée depuis `code_cultu` via `GROUP_MAP`) ; `parcelles_classification`, `divergence` et `phenologie` la récupèrent par jointure sur `id_parcel` | Garder la redondance actuelle (colonne répétée sur 3 tables, écrite indépendamment à chaque pipeline) | `classe_declaree` est une propriété de la parcelle (issue du RPG, statique sur la campagne), pas un résultat produit par la classification, la divergence ou la phénologie. La répéter sur trois tables écrites par trois pipelines distincts (`predict.py`, `persist.py` × 2) crée un risque de désaccord si `GROUP_MAP` change et qu'une seule des trois tables est rejouée - risque déjà réel puisque les upserts sont indépendants (`ON CONFLICT DO UPDATE` par table) |

Effet de bord utile : les requêtes de consultation
(ex. « distance à la classe par classe déclarée ») passent par une
jointure explicite sur `rpg_parcelles_aoi`, ce qui rend visible la
dépendance au lieu de la masquer derrière une colonne dupliquée à jour par
convention.

---

## 4. Affinement du MPD

### 4.1. Clé primaire manquante sur `derived.rpg_parcelles_aoi`

Le dictionnaire signale que cette table est un `CTAS` sans PK ni
contrainte. C'est le blocage principal empêchant toute FK réelle depuis
les tables `derived.*`.

**Vérifié dans `src/acquisition/rpg.py`** : `filtrer_aoi()` est un `CTAS`
brut (`JOIN ST_Intersects`), sans `dissolve` ni `GROUP BY`. Le `dissolve`
documenté dans `methode.md` (6 doublons `id_parcel` corrigés) intervient
plus tard, côté `zonal.py`, juste avant la rasterisation - **pas** avant
la création de `rpg_parcelles_aoi`. La table peut donc porter des
`id_parcel` dupliqués au moment de la migration ; la contrainte PK ne peut
pas être posée telle quelle, un dédoublonnage doit être intégré à la
migration elle-même plutôt que supposé déjà fait :

```sql
-- 1. Mesurer l'ampleur réelle (6 doublons attendus, cf. methode.md S2)
SELECT id_parcel, count(*) FROM derived.rpg_parcelles_aoi
GROUP BY id_parcel HAVING count(*) > 1;

-- 2. Dédoublonner par dissolve géométrique (même logique que zonal.py,
--    remontée ici plutôt que laissée en aval) avant de contraindre
CREATE TABLE derived.rpg_parcelles_aoi_dissolved AS
SELECT
    id_parcel,
    max(surf_parc) AS surf_parc,      -- à confirmer : agrégat pertinent
    max(code_cultu) AS code_cultu,    -- pour chaque colonne non géométrique,
    max(code_group) AS code_group,    -- cf. logique retenue par zonal.py
    ST_Union(geom) AS geom
FROM derived.rpg_parcelles_aoi
GROUP BY id_parcel;

DROP TABLE derived.rpg_parcelles_aoi;
ALTER TABLE derived.rpg_parcelles_aoi_dissolved
    RENAME TO rpg_parcelles_aoi;

-- 3. Contrainte, une fois l'unicité garantie
ALTER TABLE derived.rpg_parcelles_aoi
    ALTER COLUMN id_parcel SET NOT NULL,
    ADD CONSTRAINT pk_rpg_parcelles_aoi PRIMARY KEY (id_parcel);
```

L'étape 2 suppose de savoir *comment* `zonal.py` agrège les colonnes non
géométriques des doublons (moyenne, première valeur, valeur dominante) -
à confirmer dans `src/processing/zonal.py` avant d'écrire cette migration
pour de vrai, plutôt que de deviner un agrégat ici.

### 4.2. Typage resserré

Le dictionnaire type plusieurs colonnes en `text` par défaut GDAL/CTAS
alors que le contenu est structurellement plus contraint :

| Colonne | Type actuel (dictionnaire) | Type affiné | Justification |
|---|---|---|---|
| `code_cultu`, `code_group` | text | `varchar(3)` | Codes RPG à longueur fixe (référentiel Etalab), jamais réellement variables |
| `parcelles_classification.split` | text + `CHECK IN ('train','test')` | conserver `text` + `CHECK`, ou `enum split_role` | Le `CHECK` existant suffit ; un type `enum` n'apporte rien de plus tant qu'aucune troisième valeur n'est anticipée (cf. gabarit §9, index/contraintes justifiés par un usage réel, pas une anticipation) |
| `s2_parcelles_monthly.mois` | text (`'YYYY-MM'`) | conserver `text` | Un vrai type `date` imposerait un jour arbitraire (01) pour une grandeur qui est un mois, pas une date - le texte contraint (`CHECK (mois ~ '^\d{4}-\d{2}$')`) est plus honnête que de forcer un type inadapté |

### 4.3. Contraintes `FOREIGN KEY` à ajouter

Une fois 4.1 posé :

```sql
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
```

Conséquence assumée sur l'ordre d'exécution du pipeline : ces FK
imposent que `ingestion_rpg` (et donc `rpg_parcelles_aoi`) ait
effectivement terminé avant tout upsert dans les six tables filles - déjà
vrai dans le graphe Airflow documenté dans `methode.md` (DAG A et DAG B),
donc aucun changement d'orchestration requis, seulement une garantie
supplémentaire au niveau base.

### 4.4. `classe_declaree`, migration

```sql
ALTER TABLE derived.rpg_parcelles_aoi
    ADD COLUMN classe_declaree text;

UPDATE derived.rpg_parcelles_aoi
    SET classe_declaree = <application de GROUP_MAP à code_cultu>;

ALTER TABLE derived.rpg_parcelles_aoi
    ALTER COLUMN classe_declaree SET NOT NULL;

ALTER TABLE derived.parcelles_classification DROP COLUMN classe_declaree;
ALTER TABLE derived.divergence DROP COLUMN classe_declaree;
ALTER TABLE derived.phenologie DROP COLUMN classe_declaree;
```

Les trois pipelines qui écrivent aujourd'hui `classe_declaree`
(`predict.py`, `persist.py` × 2) doivent cesser de la porter en colonne
propre et la lire par jointure - changement de code corrélé, à traiter
dans le même commit que la migration de schéma.

### 4.5. Index

Le dictionnaire documente déjà les index existants sur
`rpg_parcelles_aoi` (`GIST` sur `geom`, B-tree sur `code_cultu`). Avec les
FK de 4.3, ajouter un index B-tree sur `id_parcel` dans chaque table fille
n'est *pas* automatique en PostgreSQL pour la colonne référencée côté
enfant sur une clé simple (le PK de la table parente est déjà indexé) mais
utile côté tables 1-n (`s2_parcelles_monthly`,
`s2_parcelles_completude`, `s2_parcelles_ndvi_dates`) où `id_parcel` n'est
qu'une partie de la clé composite - ces index existent déjà de fait via la
PK composite (`(id_parcel, mois, variable)` etc., dont `id_parcel` est le
préfixe), donc **aucun index supplémentaire n'est nécessaire** : à
documenter explicitement pour éviter qu'un index redondant soit ajouté
par réflexe lors de la mise en place des FK.

---

## 5. Mise à jour du tableau de décisions clés du projet

Ligne à ajouter à la section 7 (`gabarit_dossier_projet.md`) du dossier
SeineCrops :

| Décision | Alternative écartée | Justification |
|---|---|---|
| `classe_declaree` centralisée sur `rpg_parcelles_aoi`, lue par jointure depuis les 3 tables filles | Répéter la colonne sur chaque table fille (état actuel) | Propriété de la parcelle, pas un résultat de pipeline ; la redondance actuelle expose à un désaccord entre tables si `GROUP_MAP` évolue et qu'une seule table est rejouée |
| FK explicites `id_parcel` entre `rpg_parcelles_aoi` et les 6 tables filles | Conserver le lien par convention de nommage (état actuel, déjà signalé comme risque dans le dictionnaire) | Le gabarit (section 9) exclut explicitement le lien par convention au-delà du prototypage ; aucun coût d'orchestration, l'ordre Airflow existant respecte déjà la dépendance |
