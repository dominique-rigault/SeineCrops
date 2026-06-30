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
   RPG (IGN/WFS) ──► [Agrégation zonale] ──► PostGIS (parcelles + séries)
                                                      │
                          ┌───────────────────────────┼──────────────────┐
                          ▼                           ▼                  ▼
                  [ML : RF / DL]          [Phéno SOS/POS/EOS]      [FastAPI]
                          │                           │                  │
                          └───────────────────────────┴──────────────────┘
                                                      │
                                              Webmap (MapLibre)
                                                      ▲
                              Orchestration : Airflow · CI/CD : GitHub Actions
```

---

## Statut

> 🚧 **En cours de construction** — sprint S1 (données)

| Sprint | Objectif | Statut |
|---|---|---|
| S0 — Cadrage | Cadrage, dépôt Git, AOI, choix année RPG | ✅ |
| S1 — Données | Disponibilité S2 + ingestion RPG dans PostGIS | 🔄 RPG ingéré, validé, documenté · dispo S2 à venir |
| S2 — Séries | Indices, composite mensuel, table spatio-temporelle | ⬜ |
| S3 — Classification | Baseline RF, évaluation, option DL | ⬜ |
| S4 — Divergence & phéno | Détection divergence + métriques SOS/POS/EOS | ⬜ |
| S5 — Service | FastAPI + carte web | ⬜ |
| S6 — Industrialisation | Airflow, tests, CI/CD, documentation | ⬜ |

---

## Zone d'étude & période

- **AOI** : Pays de Caux + plateau du Neubourg (Eure), de part et d'autre
  de la Seine, de la pointe du Havre au sud du Neubourg — openfield grandes
  cultures, hors bocage et fonds de vallées (exclusion par type de culture
  déclaré, pas par découpe géométrique). Surface mesurée : **3 349 km²**
  (80 689 parcelles), 1–2 tuiles Sentinel-2.
- **Période** : septembre N → décembre N+1 (~16 mois, campagne RPG N+1).
- **Cultures cibles** : blé tendre, orge, colza, maïs, betterave, lin, prairies, autres.

---

## Données (toutes open data)

| Donnée | Source | Licence |
|---|---|---|
| Sentinel-2 L2A | Copernicus Data Space Ecosystem (CDSE) | Politique Copernicus (libre) |
| RPG parcelles + culture | IGN — archive régionale GeoPackage v3.0 (R28, millésime 2024) | Licence Ouverte Etalab v2 |
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
│   │   └── rpg/
│   │       └── 2024/
│   │           ├── R28/
│   │           │   ├── SOURCE.json          # traçabilité : source, licence, SHA-256
│   │           │   ├── RECON.json           # inventaire : couches, stats, emprise
│   │           │   ├── DB.json              # versions PostgreSQL / PostGIS, schémas
│   │           │   ├── INGESTION_REPORT.json # rapport de clôture consolidé
│   │           │   └── RPG_3-0__GPKG_…/    # archive décompressée (non versionnée)
│   │           └── _referentiels/
│   │               └── codes_cultures_2024.csv
│   └── vector/
│       └── aoi/
│           └── aoi_seinecrops.geojson       # AOI Caux + Neubourg (dessinée QGIS)
├── divergence/
├── docs/                     # Dictionnaire de données, schéma PostGIS
├── notebooks/
│   └── 01_ingestion_rpg.ipynb   # acquisition, reconnaissance, PostGIS, filtre AOI, QA (sections 1–5)
├── src/
│   ├── acquisition/          # Téléchargement S2, ingestion RPG
│   ├── processing/           # Masque nuages, indices, composite
│   ├── db/
│   │   └── init.sql          # extension PostGIS, schémas raw / derived
│   ├── ml/                   # Classification et détection de divergence
│   ├── phenology/            # Métriques SOS/POS/EOS
│   └── api/                  # FastAPI
├── tests/                    # Tests unitaires et d'intégration
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

**Base PostGIS**

```bash
# Créer la base et activer PostGIS + schémas (raw, derived)
psql -U postgres -c "CREATE DATABASE seinecrops;"
psql -U postgres -d seinecrops -f src/db/init.sql

# Configurer les identifiants de connexion (non versionné)
cp .env.example .env   # puis renseigner PG_PASSWORD
```

**Ingestion RPG (sprint S1 — terminée)**

```bash
# Télécharger l'archive régionale RPG depuis la page produit IGN :
# https://geoservices.ign.fr/rpg
# → Normandie (R28) · RPG Parcelles · millésime 2024
# Déposer l'archive dans : data/raw/rpg/2024/R28/

# Ouvrir et exécuter le notebook d'ingestion (Run All)
jupyter notebook notebooks/01_ingestion_rpg.ipynb
# Section 1 : récupération et traçabilité du millésime (SOURCE.json)
# Section 2 : reconnaissance du GeoPackage (RECON.json)
# Section 3 : connexion PostGIS (DB.json)
# Section 4 : chargement raw + QA géométrique + filtre AOI (derived)
# Section 5 : assertions de cohérence + rapport de clôture (INGESTION_REPORT.json)
```

> La décompression de l'archive `.7z` est automatique (via `py7zr`).
> Le chargement PostGIS passe par le driver PGDUMP de GDAL + `psql`
> (`ogr2ogr` et le driver PostgreSQL natif sont absents de cet environnement Windows).

---

## Résultats intermédiaires

> *Cette section sera alimentée jalon par jalon.*

**S1 — Ingestion RPG (terminée)**

RPG millésime 2024, Normandie (R28), base RPG\_Parcelles v3.0 :

| Indicateur | Normandie entière | AOI (Caux + Neubourg) |
|---|---|---|
| Parcelles | 528 950 | 80 689 |
| Surface totale | — | 334 943 ha (3 349 km²) |
| Surface moyenne | 3,6 ha (médiane 2,1 ha) | — |
| Surface max | 800,9 ha | — |
| Emprise (Lambert-93) | x : 343 139 – 613 528 · y : 6 788 983 – 6 998 373 | x : 487 964 – 582 799 · y : 6 875 633 – 6 981 896 |
| Top cultures (échantillon) | SNE, JAC, PPH, BTA, BOR, PTR | — |
| Codes cultures (référentiel national) | 147 codes | — |
| Géométries invalides (avant filtre AOI) | 0 | — |
| Index spatial (GIST) + attributaire (`code_cultu`) | — | ✅ |

> Parcelles intersectant l'AOI conservées **entières** (pas de découpe à la frontière) :
> une parcelle tronquée perdrait sa cohérence phénologique pour la classification.
> La QA géométrique (`ST_IsValid` / `ST_MakeValid`) est appliquée à `raw` **avant** le
> filtre AOI, pour qu'aucune parcelle invalide ne soit silencieusement exclue sans trace.

<!-- S1 suite : ajouter ici l'histogramme de disponibilité S2 -->
<!-- S2 : ajouter ici un exemple de profil NDVI par parcelle -->
<!-- S3 : ajouter ici la carte des cultures prédites et la matrice de confusion -->
<!-- S4 : ajouter ici la carte de divergence et les métriques phéno -->
<!-- S5 : ajouter ici le lien vers la démo interactive -->

---

## Documentation

- [Cadrage du projet](./cadrage/SeineCrops_cadrage.pdf)
- [Note de méthode](./cadrage/methode.md) *(à venir)*
- [Dictionnaire de données PostGIS](./docs/dictionnaire.md) *(à venir)*
- [Référence API](./docs/api.md) *(à venir — générée par FastAPI/OpenAPI)*

---

## Contexte opérationnel

Ce projet s'inscrit dans le contexte du **3STR** (Système de Suivi des Surfaces
en Temps Réel), dispositif rendu obligatoire par la PAC 2023-2027 et mis en œuvre
par l'ASP en France. La faisabilité scientifique de la classification par séries
temporelles S2 est établie (BreizhCrops, PASTIS, iota2/CESBIO) ; SeineCrops
vise une **démonstration d'ingénierie opérationnelle de bout en bout** en open source.

**Référence** : ASP — [Système de suivi des surfaces agricoles en temps réel (3STR)](https://www.asp.gouv.fr/missions-et-expertise/missions/pac-2023-2027/systeme-de-suivi-des-surfaces-agricoles-en-temps-reel)

---

## Licence

- **Code** : [MIT](./LICENSE)
- **Documentation** : [CC-BY 4.0](./LICENSE-docs)
- Voir [LICENSING.md](./LICENSING.md) pour le détail du partage et les licences des données tierces.

---

## Auteur

Dominique Rigault - projet de formation en géomatique, secteur Agriculture.
