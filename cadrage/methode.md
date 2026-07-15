# SeineCrops — Note de méthode

**Suivi et classification des cultures par séries temporelles Sentinel-2**
Plateaux de la Basse-Seine (Caux & Neubourg), Normandie

*Licence documentation : CC-BY 4.0*
*Licence code : MIT*

---

## Synthèse

### Objectif et positionnement

SeineCrops reproduit, à échelle réduite et en open source, la logique du **3STR** (Système de Suivi des Surfaces en Temps Réel), le dispositif opérationnel imposé par la PAC 2023-2027 et mis en œuvre en France par l'ASP. L'ambition n'est pas de produire un résultat de recherche original, mais de démontrer une **chaîne d'ingénierie de bout en bout** : de la donnée satellite brute à la carte interactive, en passant par une base PostGIS spatio-temporelle, un modèle de classification et une API de restitution.

### Données

Deux sources open data constituent l'ossature du projet. **Sentinel-2 L2A** (Copernicus Data Space Ecosystem) fournit les séries temporelles d'images satellite à 10-20 m de résolution, librement accessibles via l'API OData CDSE. Le **RPG 2024** (Registre Parcellaire Graphique, IGN) fournit les contours de 80 689 parcelles agricoles et leur culture déclarée pour la zone d'étude — c'est la vérité terrain pour l'apprentissage supervisé.

### Zone d'étude et période

L'AOI (3 349 km²) couvre le Pays de Caux et le plateau du Neubourg, de part et d'autre de la Seine, en Normandie. Ce territoire d'openfield grandes cultures est traversé par quatre tuiles Sentinel-2 (30UYA, 31UCR, 30UYV, 31UCQ), organisées en deux paires nord/sud. La période d'observation couvre **septembre N à décembre N+1** (~16 mois), alignée sur la campagne RPG N+1 : cette fenêtre étendue capture l'implantation du colza en tête et l'arrachage betterave/récolte maïs en queue.

### Pipeline

La chaîne se déroule en six sprints séquentiels. S1 ingère le RPG dans PostGIS et diagnostique la disponibilité Sentinel-2. S2 télécharge les bandes spectrales, calcule les indices (NDVI, EVI, NDWI, NDRE), produit les composites mensuels et agrège les statistiques zonales par parcelle. S3 entraîne et évalue un modèle de classification (Random Forest en baseline, option Deep Learning). S4 détecte les divergences entre couvert observé et culture déclarée, et extrait les métriques phénologiques (SOS/POS/EOS). S5 expose les résultats via une API FastAPI et une carte web interactive. S6 industrialise la chaîne (Airflow, tests, CI/CD).

### Feature set

Le feature set d'entrée du modèle compte **704 features par parcelle** : 11 variables (7 bandes spectrales + 4 indices) × 4 statistiques zonales (mean, std, p10, p90) × 16 mois. Les bandes retenues sont B02, B04, B05, B06, B07, B08 et B11 — couvrant le visible, le red-edge et le SWIR, qui sont les régions spectrales les plus discriminantes pour les cultures tempérées.

### Critères de succès

Le projet est piloté par six questions d'ingénierie mesurables : robustesse au manque d'observations nuageuses (profil reconstruit pour ≥ 90 % des parcelles), qualité de classification (F1 macro ≥ 0,85 sur les grandes cultures), gain du deep learning sur la baseline, fiabilité des alertes de divergence, absence de fuite spatiale dans l'évaluation, et reproductibilité de la chaîne.

---

## Note technique

### S0 — Cadrage

**AOI** : dessinée sous QGIS pour couvrir l'openfield normand (Caux + Neubourg) en excluant le Pays de Bray (bocage, petites parcelles). Stockée en GeoJSON (EPSG:4326) dans `data/vector/aoi/aoi_seinecrops.geojson`.

**Année de référence** : RPG 2024 (millésime le plus récent disponible). La fenêtre Sentinel-2 est alignée sur ce millésime : septembre 2023 → décembre 2024.

**Dépôt** : GitHub public, MIT pour le code, CC-BY 4.0 pour la documentation. Séparation stricte code/données : les données lourdes ne sont pas versionnées ; leur traçabilité est assurée par des fichiers JSON (SHA-256, provenance, versions).

**Grille des tuiles Sentinel-2** : index shapefile téléchargé depuis [justinelliotmeyers/Sentinel-2-Shapefile-Index](https://github.com/justinelliotmeyers/Sentinel-2-Shapefile-Index), converti en GeoPackage (EPSG:2154) et stocké dans `data/vector/s2_tiles/s2_tiles_2154.gpkg`. Utilisé pour la visualisation du recouvrement des 4 tuiles sur l'AOI.

---

### S1 — Données

#### S1.1 — Ingestion RPG

**Source** : archive GeoPackage RPG v3.0, base RPG_Parcelles, région Normandie (R28, millésime 2024), téléchargée depuis geoservices.ign.fr. 528 950 parcelles pour la Normandie entière.

**Chargement PostGIS** : via le driver PGDUMP de GDAL + `psql` (les drivers `ogr2ogr` PostgreSQL natif et pyogrio PostgreSQL sont indisponibles dans l'environnement Windows de développement). Les lignes `CREATE SCHEMA` sont retirées avant ingestion quand le schéma existe déjà.

**Schéma** : deux schémas PostGIS distincts. `raw` reçoit les données brutes sans modification. `derived` reçoit les données filtrées et transformées — la table `rpg_parcelles_aoi` contient les 80 689 parcelles intersectant l'AOI, conservées entières (pas de découpe à la frontière).

**Principe AOI-first** : la QA géométrique (`ST_IsValid` / `ST_MakeValid`) est appliquée à `raw` *avant* le filtre AOI — une parcelle invalide dans l'AOI doit être réparée ou tracée explicitement, pas silencieusement exclue par le filtre spatial. 10 géométries invalides ont été détectées et réparées avant filtrage.

**Filtre AOI** : `ST_Intersects` plutôt que `ST_Intersection` — on conserve les parcelles entières pour la cohérence phénologique. Une parcelle tronquée à la frontière de l'AOI perdrait une partie de ses pixels et biaiserait les statistiques zonales.

**Provenance** : quatre fichiers JSON consolident la traçabilité (`SOURCE.json`, `RECON.json`, `DB.json`, `INGESTION_REPORT.json`).

#### S1.2 — Disponibilité Sentinel-2

**API** : OData CDSE (`catalogue.dataspace.copernicus.eu`), collection SENTINEL-2, type S2MSI2A (L2A), filtre par `tileId`. Pas de filtre `cloudCover` à la requête catalogue — toutes les scènes disponibles sont recensées, y compris les plus nuageuses. La couverture nuageuse déclarée (`cloud_cover_catalogue`) est conservée à titre informatif ; la disponibilité effective sur l'AOI (`f_valid_aoi`), calculée à partir de la bande SCL, est l'objet du sprint S2.

**Déduplication** : CDSE met à disposition plusieurs baselines de traitement Sen2Cor pour les mêmes acquisitions (ex. N0509 et N0510). On conserve la baseline la plus récente par scène (même date et tuile), ce qui élimine les doublons sans perdre d'acquisitions.

**Métriques de disponibilité** : deux indicateurs complémentaires sont calculés pour chaque mois. La *couverture partielle* compte les jours avec au moins une scène sur l'une quelconque des 4 tuiles. La *couverture quasi complète* compte les jours où les paires nord (30UYA + 31UCR) ET sud (30UYV + 31UCQ) sont simultanément couvertes — condition nécessaire pour disposer d'une image complète de l'AOI ce jour-là. Ces deux indicateurs sont calculés sans filtre de couverture nuageuse — ils reflètent la disponibilité catalogue brute.

**Livrable** : `data/raw/s2/AVAILABILITY_REPORT.json` (rapport mensuel) et `data/raw/s2/catalogue_dedup.parquet` (liste complète des scènes avec identifiants CDSE, utilisée par S2 pour le téléchargement).

---

### S2 — Séries temporelles

#### S2.1 — Masque nuages et sélection des scènes (`f_valid_aoi`)

**SCL** : la bande Scene Classification Layer (60 m) du produit L2A Sen2Cor classe chaque pixel en 12 catégories. Les classes invalides retenues sont 1 (pixels saturés/défectueux), 3 (ombres nuageuses), 7 (nuages bas, probabilité faible), 8 (nuages moyennement probables), 9 (nuages hautement probables), 10 (cirrus) et 11 (neige/glace) — conformément aux recommandations HR-VPP/Sen4CAP. La classe 7 est particulièrement utile en contexte normand où les nuages bas d'automne-hiver sont fréquemment sous-détectés par l'algorithme SCL.

**`f_valid_aoi`** : pour chaque scène, fraction de pixels valides (hors classes invalides) dans l'emprise de l'AOI. Calculée en reprojetant l'AOI dans le CRS de la SCL (UTM dérivé du `tile_id` : EPSG 32600 + numéro de zone, car le driver JP2OpenJPEG ne renseigne pas toujours le CRS dans les métadonnées). Seuil de rétention : `f_valid_aoi ≥ 0.01` (au moins 1 % de pixels valides sur l'AOI). Ce seuil très permissif permet de conserver le maximum de scènes tout en éliminant celles entièrement couvertes de nuages — le composite mensuel par médiane gère la qualité résiduelle.

**Résultats S2.1** : 559 scènes retenues sur 1 071 cataloguées (52 %), 9 NaN (timeouts CDSE). Distribution bimodale : médiane à 0,031, 75e percentile à 0,411 — beaucoup de scènes quasi-nuageuses et des scènes claires franchement exploitables.

**Téléchargement SCL** : via l'API OData `/Nodes/` (`download.dataspace.copernicus.eu`), qui diffère de l'API catalogue (`catalogue.dataspace.copernicus.eu`). La réponse Nodes utilise la clé `"result"` (et non `"value"` comme le catalogue). Le `granule_id` (identifiant interne du répertoire GRANULE/ dans l'arborescence SAFE) est récupéré dynamiquement par un appel Nodes préalable, car il n'est pas disponible dans la réponse catalogue.

#### S2.2 — Téléchargement des bandes spectrales et calcul des indices

**Bandes retenues** : B02 (bleu, 10 m), B04 (rouge, 10 m), B05 (red-edge 1, 20 m), B06 (red-edge 2, 20 m), B07 (red-edge 3, 20 m), B08 (PIR large, 10 m), B11 (SWIR 1, 20 m). Toutes les bandes sont resamplées à **20 m** par interpolation bilinéaire (`rasterio.warp.reproject`, mode array-to-array pour contourner le bug JP2OpenJPEG/Windows), sur la grille de référence définie par B05 (natif 20 m) après découpe AOI.

**Découpe AOI** : appliquée dès la lecture (`rasterio.mask.mask` avec `crop=True`) pour ne charger en mémoire que les pixels dans l'emprise de l'AOI — indispensable pour maîtriser l'empreinte mémoire sur des tuiles de 110 × 110 km.

**Masque SCL pixel à pixel** : pour chaque scène retenue, la SCL (60 m) est reprojetée sur la grille AOI 20 m (`Resampling.nearest`, obligatoire pour une couche catégorielle) et les pixels des classes invalides (1, 3, 7, 8, 9, 10, 11) sont mis à NaN sur toutes les bandes avant le calcul des indices. Ce masquage per-pixel est distinct et complémentaire du filtre de sélection `f_valid_aoi` qui opère à l'échelle de la scène entière.

**Indices spectraux** :

| Indice | Formule | Intérêt |
|--------|---------|---------|
| NDVI | (B08 − B04) / (B08 + B04) | Vigueur végétale, phénologie |
| EVI | 2.5 × (B08 − B04) / (B08 + 6×B04 − 7.5×B02 + 1) | Vigueur végétale, résiste à la saturation du NDVI en été |
| NDWI | (B08 − B11) / (B08 + B11) | Teneur en eau foliaire et du sol |
| NDRE | (B08 − B05) / (B08 + B05) | Prend le relais du NDVI en pleine saison végétative, quand celui-ci plafonne et perd son pouvoir discriminant |

Les valeurs sont normalisées (division par 10 000 pour passer en réflectance) et clampées dans [-1, 1] ([-2, 2] pour l'EVI). Le dénominateur EVI est stabilisé par un garde-fou `np.where(abs(denom) < 0.001, 0.001, denom)` pour éviter les instabilités numériques en période de forte végétation estivale. Les GeoTIFF sont sauvegardés en Float32, compressés Deflate, tuiles 256 × 256.

**Contraintes Windows** : `ThreadPoolExecutor` provoque des blocages de sockets (`WinError 10013`) au-delà de 2-4 workers simultanés sur Windows en raison des limites du pare-feu et du pool de connexions. La boucle de téléchargement est séquentielle pour garantir la stabilité.

#### S2.3 — Composite mensuel

**Stratégie** : construction en deux étapes successives, à l'échelle de l'AOI entière.

Étape 1 — **Image journalière** : pour chaque date d'acquisition, toutes les scènes disponibles (1 à 4 selon les recouvrements entre tuiles) sont mosaïquées pour couvrir l'AOI. Chaque pixel reçoit la médiane des valeurs valides issues de toutes les tuiles qui le couvrent ce jour-là.

Étape 2 — **Composite mensuel** : pour chaque mois civil, la médiane pixel à pixel de toutes les images journalières valides (`f_valid_aoi ≥ 0.01`) du mois est calculée. La médiane est robuste aux nuages résiduels non détectés par la SCL et aux outliers radiométriques ponctuels. Un pixel sans aucune acquisition valide dans le mois reçoit la valeur nodata (-9999).

**Implémentation** : traitement par chunks de scènes (12 scènes par chunk) pour maîtriser l'empreinte mémoire, suivi d'une médiane des médianes de chunks. Lecture rasterio séquentielle (pas de parallélisme : le driver JP2OpenJPEG n'est pas thread-safe sur Windows).

**Structure de sortie** : `data/raw/s2/composites/<YYYY-MM>/<variable>.tif` — un GeoTIFF AOI par mois × variable (176 fichiers pour 16 mois × 11 variables).

#### S2.4 — Agrégation zonale et chargement PostGIS

**Statistiques** : pour chaque parcelle RPG × mois × variable, quatre statistiques zonales sont calculées sur les pixels valides du composite : mean, std, p10, p90. La combinaison mean + std capture la tendance centrale et l'hétérogénéité intra-parcelle. p10/p90 enrichissent le feature set pour la classification sans surcoût significatif.

**Méthode** : les 80 683 parcelles (après dissolve des 6 doublons `id_parcel`) sont rasterisées en un raster de labels sur la grille AOI (20 m, EPSG:2154), construit une seule fois. Pour chaque composite, les statistiques sont calculées par tri vectorisé numpy (argsort + split par label) — O(n log n) sur les pixels valides, sans appel rasterio.mask par parcelle. 2 751 parcelles (0,023 % de la surface) ne capturent aucun centre de pixel à 20 m et sont absentes de la table.

**Table PostGIS** : `derived.s2_parcelles_monthly` (clé primaire composite `id_parcel × mois × variable`). Volumétrie : 13 716 032 lignes (77 932 parcelles × 11 variables × 16 mois). Les insertions utilisent `INSERT ... ON CONFLICT DO NOTHING` pour permettre les relances partielles — si un composite est régénéré, supprimer les lignes du mois × variable concerné avant relance.

**Correction EVI août 2024** : le dénominateur EVI (B08 + 6×B04 − 7,5×B02 + 1) devenait instable en pleine végétation estivale. Corrigé par un garde-fou `np.where(abs(denom) < 0.001, 0.001, denom)`. Le composite d'août a été recalculé directement depuis les composites de bandes (ratio des médianes et non médiane des ratios — écart négligeable sur un indice normalisé).

#### S2.5 — Agrégation zonale NDVI aux dates d'acquisition

**Motivation** : les composites mensuels (S2.4) écrasent la dynamique fine du couvert, ce qui limite la précision d'extraction des métriques phénologiques (SOS/POS/EOS, S4). En complément, un profil NDVI est agrégé par parcelle **à chaque date d'acquisition**, sans compositage temporel — un échantillonnage irrégulier (trous nuageux, densité orbitale variable) mais fidèle à la trajectoire réelle de la végétation.

**Source** : les GeoTIFF NDVI par scène produits en S2.2 (déjà masqués SCL), reprojetés sur la grille AOI 20 m. Pour une date couverte par plusieurs tuiles, les scènes sont mosaïquées par médiane pixel à pixel (même logique que l'étape journalière du compositage S2.3). L'agrégation zonale réutilise le raster de labels et le tri vectorisé numpy de S2.4.

**Table PostGIS** : `derived.s2_parcelles_ndvi_dates` (clé primaire composite `id_parcel × date`), colonnes `mean`, `std`, `n_pixels`. Le champ `n_pixels` compte les pixels valides ayant contribué à la statistique, et permet de filtrer les parcelles à faible couverture lors du lissage phénologique. Insertions en `ON CONFLICT DO NOTHING` pour les relances partielles.

---

### S3 — Classification *(prévu)*

**Baseline** : Random Forest scikit-learn, split spatial par blocs (pas de split aléatoire qui créerait une fuite spatiale entre parcelles voisines). Features : les 704 colonnes de `derived.s2_parcelles_monthly` pivotées en wide format par parcelle. Cibles : les codes cultures RPG regroupés en 7-8 classes (blé tendre, orge, colza, maïs, betterave, lin, prairies, autres).

**Évaluation** : matrice de confusion, F1 macro et par classe, avec attention portée aux classes minoritaires. Cible indicative F1 macro ≥ 0,85 sur les grandes cultures.

**Option DL** : 1D-CNN ou LSTM sur la dimension temporelle (16 pas de temps), à envisager si la baseline ne satisfait pas les critères de validation. Le gain de performance sera mis en regard de la complexité supplémentaire.

---

### S4 — Divergence et phénologie *(prévu)*

**Détection de divergence** : pour chaque parcelle, calcul d'un score de distance au profil médian de sa classe déclarée (distance euclidienne ou DTW dans l'espace des 704 features). Les parcelles au-delà d'un seuil sont signalées comme divergentes — elles peuvent correspondre à une erreur de déclaration RPG, une culture intermédiaire non déclarée, ou un stress exceptionnel.

**Métriques phénologiques** : ajustement d'une courbe double-logistique (ou filtre Savitzky-Golay) sur le profil NDVI temporal de chaque parcelle, et extraction de SOS (Start of Season), POS (Peak of Season), EOS (End of Season) et longueur de saison. Ces métriques sont agrégées par zone et par culture pour produire des cartes phénologiques comparables aux produits HR-VPP Copernicus.

---

### S5 — Service *(prévu)*

**API** : FastAPI exposant pour chaque parcelle son identifiant RPG, sa classe prédite, son score de confiance, son score de divergence et ses métriques phénologiques. Documentation auto-générée via OpenAPI.

**Carte web** : MapLibre GL JS ou Leaflet, fond de carte Copernicus/IGN, clic parcelle → profil NDVI temporel + classe + statut de divergence.

---

### S6 — Industrialisation *(prévu)*

**Orchestration** : DAG Airflow (ou Prefect) reproduisant la chaîne complète depuis l'acquisition CDSE jusqu'au chargement PostGIS, avec gestion des dépendances inter-tâches et relance sur échec.

**Tests** : tests unitaires pytest sur les fonctions de calcul (indices spectraux, stats zonales, métriques phénologiques), tests d'intégration sur un sous-ensemble de parcelles. CI GitHub Actions.

**Documentation** : dictionnaire de données PostGIS, schéma de la base, README mis à jour jalon par jalon, note de méthode (ce document).

---

## Décisions clés et justifications

| Décision | Alternative écartée | Justification |
|----------|--------------------|--------------------------------------------|
| RPG comme vérité terrain | Enquêtes terrain | Open data national, couvre 100 % des parcelles, mise à jour annuelle |
| Seuil `f_valid_aoi ≥ 0.01` | Seuil ≥ 0.20 (HR-VPP) | Normandie nuageuse : un seuil strict éliminerait trop de scènes automnales ; le composite médiane absorbe la qualité résiduelle |
| Composite mensuel médiane | Meilleur pixel (best-pixel) | Plus simple, plus robuste, standard HR-VPP/Sen4CAP |
| Résolution 20 m (résolution native des bandes 20 m, resample 10 m → 20 m) | Tout à 10 m | Cohérence avec la résolution native de la majorité des bandes (B05, B06, B07, B11) ; évite une sur-résolution artificielle des bandes 10 m qui n'apporterait pas d'information supplémentaire pour les statistiques zonales à l'échelle de la parcelle |
| `ST_Intersects` pour filtre AOI | `ST_Intersection` (découpe) | Cohérence phénologique : une parcelle tronquée perd une partie de ses pixels et biaise les stats zonales |
| QA géométrique avant filtre AOI | QA après filtre | Une parcelle invalide dans l'AOI doit être réparée ou tracée, pas silencieusement exclue |
| Boucle séquentielle (téléchargement) | `ThreadPoolExecutor` | Instabilité réseau Windows (`WinError 10013`) avec plusieurs workers simultanés |
| Split spatial par blocs (classification) | Split aléatoire | Le split aléatoire crée une fuite spatiale : des parcelles voisines se retrouvent en train et en test |
| Format par nature de donnée (parquet métadonnées / GeoTIFF composites / PostGIS parcelles-séries) | Format unique (tout PostGIS ou tout fichiers plats) | Parquet pour le catalogue de scènes (lecture séquentielle, pas de requête spatiale) ; GeoTIFF pour les composites raster (accès fenêtré rasterio, interopérabilité QGIS) ; PostGIS pour les données vecteur/relationnelles nécessitant jointures, requêtes spatiales et relances partielles par clé composite |
---

## Limites documentées

**Optique seule** : pas de fusion radar Sentinel-1. La couverture nuageuse normande est gérée par masquage SCL, composite temporel et indicateur `f_valid_aoi`, mais les mois d'hiver restent sous-représentés. L'ajout de Sentinel-1 (cohérence, rétrodiffusion) est une perspective naturelle.

**Résolution 20 m** : comme le 3STR, la chaîne ne distingue ni les petites parcelles (< 0,5 ha) ni les cultures en mélange. C'est une limite intrinsèque de Sentinel-2 à cette résolution.

**Vérité terrain RPG** : le RPG enregistre la culture déclarée, pas la culture réellement implantée. Les erreurs de déclaration sont traitées comme du bruit dans la classification et comme du signal dans la détection de divergence.

**Évaluation géographiquement contrainte** : l'évaluation est limitée à la Normandie. La généralisation à d'autres régions (autre RPG, autre phénologie) nécessiterait un réétalonnage.
