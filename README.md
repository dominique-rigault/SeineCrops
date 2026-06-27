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

> 🚧 **En cours de construction** — sprint S0 (cadrage)

| Sprint | Objectif | Statut |
|---|---|---|
| S0 — Cadrage | Cadrage, dépôt Git, AOI, choix année RPG | ✅ |
| S1 — Données | Disponibilité S2 + ingestion RPG dans PostGIS | 🔜 |
| S2 — Séries | Indices, composite mensuel, table spatio-temporelle | ⬜ |
| S3 — Classification | Baseline RF, évaluation, option DL | ⬜ |
| S4 — Divergence & phéno | Détection divergence + métriques SOS/POS/EOS | ⬜ |
| S5 — Service | FastAPI + carte web | ⬜ |
| S6 — Industrialisation | Airflow, tests, CI/CD, documentation | ⬜ |

---

## Zone d'étude & période

- **AOI** : est du Pays de Caux + plateau du Neubourg (Eure), de part et d'autre
  de la Seine — openfield grandes cultures, hors bocage et fonds de vallées.
  Surface cible : 500–1 500 km², 1–2 tuiles Sentinel-2.
- **Période** : septembre N → décembre N+1 (~16 mois, campagne RPG N+1).
- **Cultures cibles** : blé tendre, orge, colza, maïs, betterave, lin, prairies, autres.

---

## Données (toutes open data)

| Donnée | Source | Licence |
|---|---|---|
| Sentinel-2 L2A | Copernicus Data Space Ecosystem (CDSE) | Politique Copernicus (libre) |
| RPG parcelles + culture | geoservices.ign.fr / WFS `RPG.LATEST` | Licence Ouverte Etalab v2 |
| Masque nuages | Bande SCL du L2A | idem S2 |
| Météo (optionnel) | meteo.data.gouv.fr / ERA5 CDS | Libre |
| BD TOPO / Ortho (optionnel) | geoservices.ign.fr | Licence Ouverte Etalab v2 |

> Les données ne sont pas redistribuées dans ce dépôt. Les scripts d'acquisition
> sont dans `src/acquisition/`.

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
├── cadrage/                  # Documents de cadrage et de méthode
│   └── SeineCrops_cadrage.pdf
├── src/
│   ├── acquisition/          # Téléchargement S2, ingestion RPG
│   ├── processing/           # Masque nuages, indices, composite
│   ├── db/                   # Schéma PostGIS, migrations
│   ├── ml/                   # Classification et détection de divergence
│   ├── phenology/            # Métriques SOS/POS/EOS
│   └── api/                  # FastAPI
├── notebooks/                # Exploration et visualisation
├── tests/                    # Tests unitaires et d'intégration
├── docs/                     # Dictionnaire de données, schéma PostGIS
├── .gitignore                # Exclusions du versionning
├── .github/workflows/        # CI/CD GitHub Actions
├── LICENSE                   # MIT (code)
├── LICENSE-docs              # CC-BY 4.0 (documentation)
├── LICENSING.md              # Tableau de partage des licences
├── .pre-commit-config.yaml
├── requirements.txt          # Dépendances Python
└── README.md
```

---

## Démarrage rapide

> ⚠️ *Section à compléter au sprint S1.*

```bash
# Cloner le dépôt
git clone https://github.com/dominique-rigault/SeineCrops.git
cd SeineCrops

# Installer les dépendances
pip install -r requirements.txt

# Initialiser la base PostGIS
psql -U postgres -f src/db/init.sql

# Lancer les tests
pytest
```

---

## Résultats intermédiaires

> *Cette section sera alimentée jalon par jalon.*

<!-- S1 : ajouter ici l'histogramme de disponibilité S2 -->
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
