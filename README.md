# SeineCrops

**Suivi et classification des cultures par séries temporelles Sentinel-2**
Plateaux de la Basse-Seine (Caux & Neubourg), Normandie · données open data

[![CI](https://github.com/VOTRE-USERNAME/SeineCrops/actions/workflows/ci.yml/badge.svg)](https://github.com/dominique-rigault/SeineCrops/actions)
[![License: MIT](https://img.shields.io/badge/Code-MIT-green.svg)](./LICENSE)
[![License: CC BY 4.0](https://img.shields.io/badge/Docs-CC--BY%204.0-blue.svg)](./LICENSE-docs)

---

## Vue d'ensemble

SeineCrops est une chaîne de traitement **reproductible et open source** qui classifie
les cultures agricoles et détecte les parcelles dont le couvert observé diverge de
leur déclaration PAC, à partir de séries temporelles Sentinel-2 croisées au RPG.

Le projet reproduit à échelle réduite la logique du dispositif opérationnel
**3STR** (Système de Suivi des Surfaces en Temps Réel, ASP / PAC 2023-2027).

```
Copernicus CDSE ──► [Acquisition · Rasterio]
                              │
                              ▼
   RPG (IGN/WFS) ──────► [Agrégation zonale] ───────────────┐
                              │                             │
              ┌───────────────┴───────────────┐             │
              ▼                               ▼             │
      [ML : RF / DL]                [Phéno SOS/POS/EOS]     │
              │                               │             │
              └───────────────┬───────────────┘             │
                              ▼                             │
                           PostGIS ◄────────────────────────┘
                              │
                              ▼
                          [FastAPI]
                              │
                              ▼
                       Webmap (MapLibre)

Orchestration : Airflow · CI/CD : GitHub Actions
```

---

## Zone d'étude & période

- **AOI** : Pays de Caux + plateau du Neubourg (Eure), de part et d'autre
  de la Seine, de la pointe du Havre au sud du Neubourg - openfield grandes
  cultures. Surface mesurée : **3 349 km²** (80 689 parcelles), 4 tuiles
  Sentinel-2 (30UYA · 31UCR · 30UYV · 31UCQ).
- **Période** : septembre N → décembre N+1 (~16 mois, campagne RPG N+1).
- **Cultures cibles** : blé tendre, orge, colza, maïs, betterave, lin, prairies, autres.

---

## Données (toutes open data)

| Donnée | Source | Licence |
|---|---|---|
| Sentinel-2 L2A | Copernicus Data Space Ecosystem (CDSE) | Politique Copernicus (libre) |
| RPG parcelles + culture | IGN - archive régionale GeoPackage v3.0 (R28, millésime 2024) | Licence Ouverte Etalab v2 |
| RPG codes cultures | Géoplateforme WFS `RPG.2024:codes_cultures` | Licence Ouverte Etalab v2 |
| Masque nuages | Bande SCL du L2A | idem S2 |
| Météo (optionnel) | meteo.data.gouv.fr / ERA5 CDS | Libre |
| BD TOPO / Ortho (optionnel) | geoservices.ign.fr | Licence Ouverte Etalab v2 |

> **RPG v3.0 (millésime 2024).** L'offre RPG est restructurée en 8 bases thématiques
> (RPG\_Parcelles, RPG\_Ilots, RPG\_PAC, RPG\_PP, RPG\_BIO, RPG\_IAE, RPG\_SNA, RPG\_ZDH).
> SeineCrops utilise **RPG\_Parcelles** comme vérité terrain (528 950 parcelles pour
> la Normandie, EPSG:2154).

> Les données ne sont pas redistribuées dans ce dépôt. La traçabilité de chaque
> millésime est assurée par `SOURCE.json` (empreinte SHA-256) et `RECON.json`
> (inventaire des couches, statistiques, emprise).

---

## Stack technique

| Couche | Outils |
|---|---|
| Base de données | PostgreSQL + PostGIS |
| Traitement raster | Python · Rasterio · GDAL |
| Analyse spatiale | GeoPandas · NumPy |
| Machine learning | scikit-learn (RF) · PyTorch ou Keras (DL, optionnel) |
| Orchestration | Airflow (ou Prefect) |
| API | FastAPI |
| Carte web | MapLibre GL JS ou Leaflet |
| Qualité logicielle | pytest · pré-commit · GitHub Actions |

---

## Structure du dépôt

```
SeineCrops/
├── .github/workflows/        # CI/CD GitHub Actions
├── cadrage/                  # Documents de cadrage et de méthode
│   └── SeineCrops_cadrage.pdf
├── data/
│   ├── raw/
│   │   ├── rpg/
│   │   │   └── 2024/
│   │   │       ├── R28/
│   │   │       │   ├── SOURCE.json               # traçabilité : source, licence, SHA-256
│   │   │       │   ├── RECON.json                # inventaire : couches, stats, emprise
│   │   │       │   ├── DB.json                   # versions PostgreSQL / PostGIS, schémas
│   │   │       │   ├── INGESTION_REPORT.json     # rapport de clôture consolidé
│   │   │       │   └── RPG_3-0__GPKG_…/          # archive décompressée (non versionnée)
│   │   │       └── _referentiels/
│   │   │           └── codes_cultures_2024.csv
│   │   └── s2/
│   │       ├── AVAILABILITY_REPORT.json          # rapport de clôture disponibilité S2
│   │       ├── availability_s2.png               # histogramme mensuel (non versionné)
│   │       ├── catalogue_dedup.parquet           # catalogue dédupliqué + f_valid_aoi (non versionné)
│   │       └── composites/                       # composites mensuels AOI (non versionnés)
│   │           └── <YYYY-MM>/<variable>.tif      # 176 GeoTIFF (16 mois × 11 variables)
│   └── vector/
│       ├── aoi/
│       │   └── aoi_seinecrops.geojson            # AOI Caux + Neubourg (dessinée QGIS)
│       └── s2_tiles/
│           └── sentinel2_4tuiles_2154.gpkg       # emprise des 4 tuiles Sentinel-2 (EPSG:2154)
├── divergence/
├── docs/                     # Dictionnaire de données, schéma PostGIS
├── notebooks/                 # Prototypage historique - non nécessaires pour reproduire
│   │                          # le pipeline, portés vers src/*/orchestration.py + scripts/
│   ├── 01_ingestion_rpg.ipynb    # Acquisition RPG, PostGIS, filtre AOI, QA (sections 1–5)
│   ├── 02_disponibilite_s2.ipynb # Diagnostic catalogue CDSE, disponibilité mensuelle (sections 1–5)
│   ├── 03_series_s2.ipynb        # SCL, bandes, indices, composite mensuel, agrégation zonale (sections 3.1–3.4)
│   ├── 04_classification.ipynb   # Baseline RF, split spatial par blocs, évaluation (F1 macro 0,893)
│   └── 05_divergence_pheno.ipynb # Distance RMS standardisée, phénologie Whittaker SOS/POS/EOS (sections 5.1–5.4)
├── db/
│   ├── migrations/            # DDL versionné, 0001 à 0007 - ordre de rejeu imposé par les dépendances, pas numérique (cf. Démarrage rapide)
│   └── benchmarks/            # Scripts avant/après par sprint d'index (gabarit §9)
│       └── s8_benchmark_index_geom.py  # idx_rpg_parcelles_aoi_geom, requêtes bbox réelles (S8)
├── scripts/                   # Enveloppes CLI (§ Démarrage rapide), utilisées aussi par le DAG Airflow
│   ├── run_ingestion.py          # Ingestion RPG + catalogue CDSE (portage 01+02)
│   ├── run_processing.py         # Séries temporelles S2 (portage 03)
│   ├── run_ml.py                 # Classification (portage 04)
│   └── run_phenology.py          # Divergence/phénologie (portage 05)
├── src/
│   ├── acquisition/          # Téléchargement S2, ingestion RPG
│   ├── processing/           # Masque nuages, indices, composite
│   ├── db/
│   │   └── init.sql          # extension PostGIS, schémas raw / derived
│   ├── ml/                   # Classification et détection de divergence
│   ├── phenology/            # Métriques SOS/POS/EOS
│   └── api/                  # FastAPI
│       ├── __init__.py
│       ├── db.py             # pool asyncpg, chargement .env, détection .projectroot
│       ├── schemas.py        # modèles Pydantic (ParcelleDetail, ParcelleProfil)
│       ├── queries.py        # requêtes SQL + assemblage ligne DB → modèle Pydantic
│       └── main.py           # application FastAPI, routes /parcelles/{id}, /parcelles/{id}/profil, /parcelles?bbox=
├── web/
│   └── index.html            # carte web MapLibre - fond OSM, couche parcelles, panneau de détail + graphique NDVI
├── tests/
│   ├── conftest.py
│   ├── api/
│   │   ├── conftest.py       # fixture de connexion asyncpg
│   │   ├── test_queries.py             # tests unitaires (logique pure)
│   │   └── test_queries_integration.py # tests d'intégration PostGIS
│   └── db/                   # tests de schéma, cumulatifs (gabarit §9) - PK, FK, index, usage réel
│       ├── conftest.py       # fixture de connexion psycopg2 (distincte de tests/api/, cf. methode.md §S8)
│       └── test_schema.py    # 0002 à 0007 + index spatiaux (S8)
├── .env                      # identifiants PostGIS (non versionné)
├── .gitignore                # Exclusions du versionning
├── .pre-commit-config.yaml
├── .projectroot
├── LICENSE                   # MIT (code)
├── LICENSE-docs              # CC-BY 4.0 (documentation)
├── LICENSING.md              # Tableau de partage des licences
├── README.md
└── requirements.txt          # Dépendances Python
```

---

## Démarrage rapide

```bash
# Cloner le dépôt
git clone https://github.com/dominique-rigault/SeineCrops.git
cd SeineCrops

# Créer et activer l'environnement virtuel
python -m venv .venv-geo
source .venv-geo/Scripts/activate   # Windows Git Bash
# source .venv-geo/bin/activate     # Linux / macOS

# Installer les dépendances
pip install -r requirements.txt

# Activer les hooks pre-commit
pre-commit install
```

> **Notebooks non nécessaires pour reproduire le pipeline.** `notebooks/`
> ne contient que les carnets de prototypage historiques - toute la chaîne
> reproductible passe désormais par `scripts/run_*.py` (voir plus bas).
> Aucun notebook n'a besoin d'être ouvert ni exécuté. Pour ne même pas les
> récupérer au clonage :
>
> ```bash
> git clone --filter=blob:none --no-checkout \
>     https://github.com/dominique-rigault/SeineCrops.git
> cd SeineCrops
> git sparse-checkout init --no-cone
> printf '/*\n!/notebooks/\n' > .git/info/sparse-checkout
> git checkout main
> ```
>
> Nécessite Git ≥ 2.25 (sparse-checkout non-cone) - à vérifier avec
> `git --version` si la commande échoue.

**Compte Copernicus Data Space Ecosystem (CDSE)**

Un compte gratuit est nécessaire pour interroger le catalogue Sentinel-2 et
télécharger les produits L2A (nécessaire pour les séries temporelles et la suite du pipeline).

1. S'inscrire sur [dataspace.copernicus.eu](https://dataspace.copernicus.eu)
2. Confirmer l'adresse e-mail (lien envoyé par CDSE)
3. Se connecter une première fois sur le portail et accepter les conditions d'utilisation
4. Renseigner les credentials dans `.env` (voir ci-dessous)

> Sans l'étape 3, l'API retourne `invalid_grant / Account is not fully set up`.

**Variables d'environnement (`.env`)**

Copier `.env.example` et renseigner toutes les variables avant d'exécuter les scripts :

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `PG_HOST` | Hôte PostgreSQL (ex. `localhost`) |
| `PG_PORT` | Port PostgreSQL (ex. `5432`) |
| `PG_DB` | Nom de la base (ex. `seinecrops`) |
| `PG_USER` | Utilisateur PostgreSQL |
| `PG_PASSWORD` | Mot de passe PostgreSQL |
| `CDSE_USER` | Adresse e-mail du compte CDSE |
| `CDSE_PASSWORD` | Mot de passe du compte CDSE |

> Le fichier `.env` n'est pas versionné (listé dans `.gitignore`).
> Ne jamais committer de credentials en clair.

**Base PostGIS**

```bash
# Créer la base et activer PostGIS + schémas (raw, derived)
psql -U postgres -c "CREATE DATABASE seinecrops;"
psql -U postgres -d seinecrops -f src/db/init.sql

# Table de suivi des migrations - seule migration sans dépendance sur des
# données, s'applique dès la création de la base
psql -U postgres -d seinecrops -f db/migrations/0001_create_schema_migrations.sql -v ON_ERROR_STOP=1
```

**Ingestion RPG + catalogue Sentinel-2**

```bash
# Télécharger l'archive régionale RPG depuis la page produit IGN :
# https://geoservices.ign.fr/rpg
# → Normandie (R28) · RPG Parcelles · millésime 2024
# Déposer l'archive dans : data/raw/rpg/2024/R28/

# Compte CDSE requis (voir ci-dessus) pour le volet catalogue Sentinel-2.
# Credentials renseignés dans .env.
python -m scripts.run_ingestion
# --skip-rpg   : catalogue CDSE seul (nécessite un run RPG préalable)
# --skip-cdse  : ingestion RPG seule
```

> Effets : décompression automatique de l'archive `.7z` (via `py7zr`) en
> `.gpkg` local, non versionné ; 4 fichiers de traçabilité
> (`SOURCE.json`, `RECON.json`, `DB.json`, `INGESTION_REPORT.json`) ;
> chargement PostGIS via le driver PGDUMP de GDAL + `psql` (`ogr2ogr` et
> le driver PostgreSQL natif sont absents de cet environnement Windows) ;
> `raw.rpg_parcelles`/`raw.aoi_seinecrops` peuplées, `derived.rpg_parcelles_aoi`
> créée par filtre AOI ; côté catalogue, `AVAILABILITY_REPORT.json` généré,
> aucune image téléchargée à ce stade (diagnostic pur).

> **Millésime non paramétrable en ligne de commande.** Aucun des scripts
> `scripts/run_*.py` n'expose de `--millesime`/`--annee` : l'année RPG
> (`2024`), la fenêtre temporelle Sentinel-2 et les chemins de campagne
> sont des constantes de `src/config.py`. Pour traiter un autre millésime,
> modifier ce fichier avant d'exécuter les scripts, plutôt que de
> s'appuyer sur un argument d'exécution qui n'existe pas.

**Migrations de contrainte**

```bash
# derived.rpg_parcelles_aoi (étape précédente) requise.
# Ordre imposé par les dépendances, PAS alphabétique - ne pas remplacer
# par une boucle sur db/migrations/*.sql : 0004 porte sur des tables que
# seules 0006 et 0007 créent, et échouerait s'il passait avant elles.
for f in \
    0002_raw_rpg_parcelles_aoi_ddl.sql \
    0003_derived_rpg_parcelles_aoi_pk.sql \
    0006_derived_ddl_classification_phenologie.sql \
    0007_derived_ddl_zonal.sql \
    0004_derived_fk_rpg_parcelles_aoi.sql \
    0005_rpg_parcelles_aoi_classe_declaree.sql; do
    psql -U postgres -d seinecrops -f "db/migrations/$f" -v ON_ERROR_STOP=1
done
```

> Effets : `0002` déclare explicitement le schéma natif de `raw.rpg_parcelles`/
> `raw.aoi_seinecrops` (posé implicitement par GDAL à l'ingestion) ;
> `0003` dissout les doublons `id_parcel` et pose la `PRIMARY KEY` sur
> `derived.rpg_parcelles_aoi` ; `0006` et `0007` créent respectivement
> `derived.parcelles_classification`/`divergence`/`phenologie` et
> `derived.s2_parcelles_monthly`/`_completude`/`_ndvi_dates` (tables que
> les étapes suivantes peuplent, mais ne créent plus depuis que leur DDL
> a été sorti du code applicatif) ; `0004` pose les 6 `FOREIGN KEY` vers
> `rpg_parcelles_aoi` ; `0005` centralise `classe_declaree` sur
> `rpg_parcelles_aoi`.
>
> Migrations forward-only (pas de rollback), pour la plupart idempotentes
> (`IF NOT EXISTS`) - sûres à rejouer sur une base déjà à jour, sauf
> `0004` (ajout de contraintes `FOREIGN KEY`, sans équivalent
> `IF NOT EXISTS` en PostgreSQL) : une exécution en double y échouera
> proprement plutôt que silencieusement.

**Séries temporelles Sentinel-2**

```bash
# Compte CDSE requis. Credentials renseignés dans .env.
# Base PostGIS avec derived.rpg_parcelles_aoi (étape précédente) requise.
python -m scripts.run_processing
# --skip-scl / --skip-bands / --skip-composites / --skip-zonal : reprise partielle
```

> Effets : téléchargement SCL puis bandes/indices (B02, B04, B05, B06,
> B07, B08, B11, NDVI, EVI, NDWI, NDRE), 176 GeoTIFF de composites
> mensuels dans `data/raw/s2/composites/<YYYY-MM>/` (non versionnés,
> suppression automatique des scènes source après compositage pour
> libérer l'espace disque, composites déjà produits skippés à la relance),
> `derived.s2_parcelles_monthly`/`_completude`/`_ndvi_dates` peuplées par
> agrégation zonale.

**Classification**

```bash
# Base PostGIS avec derived.s2_parcelles_monthly (étape précédente) requise.
python -m scripts.run_ml
# --skip-search : modèle baseline uniquement, sans RandomizedSearchCV (plus rapide)
```

> Effets : aucun fichier. `derived.parcelles_classification` peuplée
> (upsert `ON CONFLICT DO UPDATE` - une ligne par parcelle, un nouveau
> run écrase la prédiction précédente avec la nouvelle version de modèle).

**Divergence & phénologie**

```bash
# Base PostGIS avec derived.parcelles_classification (étape précédente) requise.
python -m scripts.run_phenology
```

> Effets : aucun fichier, hors 2 appels à l'API CDSE (empreintes de
> scènes pour `zone_raccord_orbital`) - appel obligatoire mais
> skippable sans faire planter le run (dégrade seulement
> `dist_raccord`/`zone_raccord_orbital`). `derived.divergence` et
> `derived.phenologie` peuplées (upsert `ON CONFLICT DO UPDATE`).

**Service (API + carte web)**

```bash
# Base PostGIS avec derived.divergence/derived.phenologie (étape précédente) requise.
uvicorn src.api.main:app --reload
```

> Effets : aucun fichier, aucune écriture en base (lecture seule).
> Endpoints `GET /parcelles/{id}`, `GET /parcelles/{id}/profil`,
> `GET /parcelles?bbox=`, `GET /health`. Ouvrir ensuite `web/index.html`
> dans un navigateur pour la carte interactive (MapLibre, fond OSM, sans
> étape de build).

**Orchestration Airflow (optionnelle, via Docker)**

Alternative à l'enchaînement manuel des scripts ci-dessus : les 4 étapes
d'acquisition/traitement (ingestion RPG + catalogue Sentinel-2, séries
temporelles, classification, divergence & phénologie) sont orchestrées
par deux DAG Airflow (`seinecrops_acquisition_s2`,
`seinecrops_zonal_ml_phenologie`).

> **Les migrations de contrainte ne font pas partie du DAG.** Elles
> doivent être jouées manuellement (cf. « Migrations de contrainte »
> ci-dessus) **avant** tout premier déclenchement de DAG sur une base
> neuve - le DAG suppose que `derived.parcelles_classification`,
> `derived.divergence`, `derived.phenologie` et `derived.s2_parcelles_*`
> existent déjà (créées par les migrations `0006`/`0007`, plus par
> l'application), sans quoi la tâche de séries temporelles échoue dès sa
> première écriture. Limite actuelle, pas prévue pour être comblée par le
> DAG lui-même à ce stade.

```bash
docker compose up airflow-init
docker compose up -d
```

Interface Airflow sur [http://localhost:8080](http://localhost:8080),
identifiants créés automatiquement par `airflow-init` : `admin` / `admin`.
Déclenchement des DAG depuis l'interface, ou en CLI :

```bash
docker compose exec airflow-webserver airflow dags trigger seinecrops_acquisition_s2
docker compose exec airflow-webserver airflow dags trigger seinecrops_zonal_ml_phenologie
```

> **Seul Airflow est conteneurisé, pas PostGIS.** La base `seinecrops`
> reste sur l'hôte Windows (celle créée plus haut avec `psql`) - seule la
> base de métadonnées Airflow (`postgres-airflow`) tourne en conteneur.
> `.env` est réutilisé tel quel (monté dans les conteneurs) : `PG_HOST`
> y est probablement `localhost`, valable sur l'hôte mais pas depuis un
> conteneur - `docker-compose.yml` le surcharge automatiquement en
> `host.docker.internal` (le nom DNS fourni par Docker Desktop pour
> atteindre l'hôte), aucune modification manuelle du `.env` n'est
> nécessaire pour ça.
>
> Dépendances Airflow dans `requirements-airflow.txt` (distinct de
> `requirements.txt`, utilisé en local) ; `docker compose build` ne
> recharge pas les conteneurs déjà démarrés - refaire `docker compose up -d`
> après un build pour que la nouvelle image soit utilisée. Le pool Airflow
> `ml_intensif` (1 slot, sérialise `entrainement_ml` et
> `divergence_phenologie` pour éviter une contention mémoire) et
> l'utilisateur `admin` sont créés automatiquement par `airflow-init`.
>
> **Hors périmètre, non implémenté** (`methode.md`, clôture du sprint
> d'industrialisation) : CI GitHub Actions et conteneurisation de l'API
> (`src/api/`). Seule l'orchestration Airflow des étapes
> d'acquisition/traitement est industrialisée à ce stade - l'API se
> lance encore manuellement (`uvicorn`, ci-dessus) et PostGIS reste sur
> l'hôte, pas en conteneur.
>
> Les tests, eux, sont implémentés et exécutés manuellement (aucun des
> deux n'est branché sur une CI, puisqu'il n'y en a pas à ce stade) :
> `tests/api/` (couche API, 2 tests unitaires + 7 tests d'intégration
> PostGIS, `pytest tests/api/`) et `tests/db/` (schéma - PK/FK/index/
> usage réel des index, `methode.md` §S8, `pytest tests/db/`).

---

## Résultats intermédiaires

> *Cette section sera alimentée jalon par jalon.*

**Ingestion RPG**

RPG millésime 2024, Normandie (R28), base RPG\_Parcelles v3.0 :

| Indicateur | Normandie entière | AOI (Caux + Neubourg) |
|---|---|---|
| Parcelles | 528 950 | 80 689 |
| Surface totale | - | 334 943 ha (3 349 km²) |
| Surface moyenne | 3,6 ha (médiane 2,1 ha) | - |
| Surface max | 800,9 ha | - |
| Emprise (Lambert-93) | x : 343 139 – 613 528 · y : 6 788 983 – 6 998 373 | x : 487 964 – 582 799 · y : 6 875 633 – 6 981 896 |
| Top cultures (échantillon) | SNE, JAC, PPH, BTA, BOR, PTR | - |
| Codes cultures (référentiel national) | 147 codes | - |
| Géométries invalides (avant filtre AOI) | 0 | - |
| Index spatial (GIST) + attributaire (`code_cultu`) | - | ✅ |

> Parcelles intersectant l'AOI conservées **entières** (pas de découpe à la frontière) :
> une parcelle tronquée perdrait sa cohérence phénologique pour la classification.
> La QA géométrique (`ST_IsValid` / `ST_MakeValid`) est appliquée à `raw` **avant** le
> filtre AOI, pour qu'aucune parcelle invalide ne soit silencieusement exclue sans trace.

**Disponibilité Sentinel-2**

Catalogue CDSE, 4 tuiles (30UYA · 31UCR · 30UYV · 31UCQ), fenêtre sept. 2023 → déc. 2024 :

| Indicateur | Valeur |
|---|---|
| Scènes catalogue brutes (4 tuiles) | 1 071 (après déduplication baseline) |
| Jours couverts - couverture partielle (≥ 1 tuile) | 292 / 488 jours |
| Mois le plus creux | *voir* `AVAILABILITY_REPORT.json` |

> Aucun filtre de couverture nuageuse appliqué au catalogue - toutes les scènes L2A
> disponibles sont recensées. La disponibilité effective sur l'AOI (`f_valid_aoi`)
> est calculée à l'étape suivante (séries temporelles) à partir de la bande SCL.
> Voir `data/raw/s2/AVAILABILITY_REPORT.json` pour le détail mensuel.

**Séries temporelles**

Table spatio-temporelle `derived.s2_parcelles_monthly` :

| Indicateur | Valeur |
|---|---|
| Scènes retenues (`f_valid_aoi ≥ 0.01`) | 559 / 1 071 (52 %) |
| Variables (7 bandes + 4 indices) | B02, B04, B05, B06, B07, B08, B11, NDVI, EVI, NDWI, NDRE |
| Composites mensuels | 176 GeoTIFF (16 mois × 11 variables, EPSG:2154, 20 m) |
| Parcelles rasterisées | 77 932 / 80 683 (96,6 %) |
| Parcelles sans pixel (< 20 m) | 2 751 (0,023 % de la surface agricole) |
| Statistiques zonales par parcelle | mean, std, p10, p90 |
| Lignes PostGIS | 13 716 032 |
| Feature set résultant | 704 features / parcelle (11 var × 4 stats × 16 mois) |

> Composite mensuel par médiane deux étapes : médiane journalière (toutes tuiles
> couvrant un pixel ce jour-là) puis médiane mensuelle (toutes images journalières
> du mois). 6 doublons `id_parcel` dans le RPG corrigés par `dissolve` avant
> rasterisation. Correction EVI août 2024 : dénominateur instable en pleine
> végétation, recalculé depuis les composites de bandes.

**Classification**

Baseline Random Forest (`n_estimators=300`, `max_depth=30`, `min_samples_leaf=5`,
`class_weight="balanced"`), split spatial par blocs (75 blocs) :

| Indicateur | Valeur |
|---|---|
| Split train / test | 61 043 / 16 889 parcelles (78,3 % / 21,7 %) |
| F1 macro (8 classes) | **0,893** |
| F1 macro hors `autres` (7 classes agronomiques) | **0,922** (cible ≥ 0,85 atteinte) |
| Accuracy test / train | 0,881 / 0,941 (écart raisonnable) |
| Classes les mieux discriminées | colza (F1 0,978), céréales d'hiver (0,962), betterave (0,960) |
| Classe la plus faible | `autres` (F1 ≈ 0,68), confusion bidirectionnelle avec `prairie` |
| Modèle retenu | baseline par défaut (`rf_base`), sans tuning ni features temporelles |

> `RandomizedSearchCV` (20 itér., `cv=3`) testé et écarté : gain F1 macro nul (+0,001)
> pour un surapprentissage aggravé (écart train/test 0,060 → 0,107), la CV interne
> étant aveugle au split spatial par blocs. Trois features temporelles dérivées
> (amplitude NDVI, jour du maximum, pente mai→août) testées et écartées également :
> gain nul, la confusion `autres`/`prairie` se redistribue sans se réduire - indice
> d'un problème de qualité de label RPG plutôt que de feature manquante.

**Divergence & phénologie**

Distance RMS standardisée (z-score) au profil médian de classe; lissage Whittaker
pondéré (λ=800) pour SOS/POS/EOS/LOS, fenêtres calendaires par classe :

| Indicateur | Valeur |
|---|---|
| Parcelles divergentes | 2 420 / 77 932 (3,1 %) |
| Persistance | `derived.divergence`, `derived.phenologie` (upsert `ON CONFLICT DO UPDATE`) |

| Classe | % LOS réaliste / conforme |
|---|---:|
| lin | 75 % |
| maïs | 74 % |
| betterave | 72 % |
| colza | 66 % |
| légumes/fleurs | 62 % |
| céréales d'hiver | 60 % |
| autres | 33 % |
| prairie | 24 % |

> `autres` et `prairie` n'ont pas de fenêtre calendaire calibrée (couvert pérenne /
> classe hétérogène) : la notion de "LOS typique" y est mal posée par construction,
> pas un échec de méthode. Flag `zone_raccord_orbital` ajouté pour isoler la bande
> de divergence structurelle liée au raccord orbital 51/94 sur la tuile 30UYV.

**Service**

FastAPI (`src/api/`) + carte web MapLibre (`web/index.html`), sans étape de build :

| Indicateur | Valeur |
|---|---|
| Endpoints | `GET /parcelles/{id}`, `GET /parcelles/{id}/profil`, `GET /parcelles?bbox=`, `GET /health` |
| Tests | 9 (`pytest tests/api/`) : 2 unitaires (`test_queries.py`) + 7 d'intégration PostGIS (`test_queries_integration.py`) |
| `BBOX_MAX_AREA_KM2` / `limit` | 50 km² / 2000 - mesurés sur la densité réelle de parcelles (24,1/km²) |
| Tolérance de simplification géométrique | 5 m (médiane 18 → 7 sommets/parcelle) |
| Carte web | fond OSM (raster, sans clé API), couleurs liées aux cultures réelles (colza jaune, lin bleu…), panneau de détail + graphique NDVI au clic |

> Pagination par `offset` volontairement non implémentée (cf. `methode.md` §S5) - le
> geste naturel d'une carte interactive est de réduire le `bbox` (zoom/déplacement),
> pas de paginer une même zone.

<!-- Industrialisation : ajouter ici les métriques (CI, temps de traitement) -->

---

## Documentation

- [Cadrage du projet](./cadrage/SeineCrops_cadrage.pdf)
- [Note de méthode](./cadrage/methode.md)
- [Dictionnaire de données PostGIS](./docs/dictionnaire.md) *(à venir)*
- [Référence API](./docs/api.md) *(à venir - générée par FastAPI/OpenAPI)*

---

## Contexte opérationnel

Ce projet s'inscrit dans le contexte du **3STR** (Système de Suivi des Surfaces
en Temps Réel), dispositif rendu obligatoire par la PAC 2023-2027 et mis en œuvre
par l'ASP en France. La faisabilité scientifique de la classification par séries
temporelles S2 est établie (BreizhCrops, PASTIS, iota2/CESBIO); SeineCrops
vise une **démonstration d'ingénierie opérationnelle de bout en bout** en open source.

**Référence** : ASP - [Système de suivi des surfaces agricoles en temps réel (3STR)](https://www.asp.gouv.fr/missions-et-expertise/missions/pac-2023-2027/systeme-de-suivi-des-surfaces-agricoles-en-temps-reel)

---

## Licence

- **Code** : [MIT](./LICENSE)
- **Documentation** : [CC-BY 4.0](./LICENSE-docs)
- Voir [LICENSING.md](./LICENSING.md) pour le détail du partage et les licences des données tierces.

---

## Auteur

Dominique Rigault - projet de formation en géomatique, secteur Agriculture.
