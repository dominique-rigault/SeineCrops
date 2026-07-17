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

#### 1 — Ingestion RPG (`01_ingestion_rpg.ipynb`)

**Source** : archive GeoPackage RPG v3.0, base RPG_Parcelles, région Normandie (R28, millésime 2024), téléchargée depuis geoservices.ign.fr. 528 950 parcelles pour la Normandie entière.

**Chargement PostGIS (4.1)** : via le driver PGDUMP de GDAL + `psql` (les drivers `ogr2ogr` PostgreSQL natif et pyogrio PostgreSQL sont indisponibles dans l'environnement Windows de développement). Les lignes `CREATE SCHEMA` sont retirées avant ingestion quand le schéma existe déjà.

**Schéma** : deux schémas PostGIS distincts. `raw` reçoit les données brutes sans modification. `derived` reçoit les données filtrées et transformées — la table `rpg_parcelles_aoi` contient les 80 689 parcelles intersectant l'AOI, conservées entières (pas de découpe à la frontière).

**Principe AOI-first (4.1bis)** : la QA géométrique (`ST_IsValid` / `ST_MakeValid`) est appliquée à `raw` *avant* le filtre AOI — une parcelle invalide dans l'AOI doit être réparée ou tracée explicitement, pas silencieusement exclue par le filtre spatial. 10 géométries invalides ont été détectées et réparées avant filtrage.

**Filtre AOI (4.3)** : `ST_Intersects` plutôt que `ST_Intersection` — on conserve les parcelles entières pour la cohérence phénologique. Une parcelle tronquée à la frontière de l'AOI perdrait une partie de ses pixels et biaiserait les statistiques zonales.

**Provenance (5.3)** : quatre fichiers JSON consolident la traçabilité (`SOURCE.json`, `RECON.json`, `DB.json`, `INGESTION_REPORT.json`).

#### 2 — Disponibilité Sentinel-2 (`02_disponibilite_s2.ipynb`)

**API (2.1, 2.2)** : OData CDSE (`catalogue.dataspace.copernicus.eu`), collection SENTINEL-2, type S2MSI2A (L2A), filtre par `tileId`. Pas de filtre `cloudCover` à la requête catalogue — toutes les scènes disponibles sont recensées, y compris les plus nuageuses. La couverture nuageuse déclarée (`cloud_cover_catalogue`) est conservée à titre informatif ; la disponibilité effective sur l'AOI (`f_valid_aoi`), calculée à partir de la bande SCL, est l'objet de 3.1.

**Déduplication (2.4)** : CDSE met à disposition plusieurs baselines de traitement Sen2Cor pour les mêmes acquisitions (ex. N0509 et N0510). On conserve la baseline la plus récente par scène (même date et tuile), ce qui élimine les doublons sans perdre d'acquisitions.

**Métriques de disponibilité (2.4)** : deux indicateurs complémentaires sont calculés pour chaque mois. La *couverture partielle* compte les jours avec au moins une scène sur l'une quelconque des 4 tuiles. La *couverture quasi complète* compte les jours où les paires nord (30UYA + 31UCR) ET sud (30UYV + 31UCQ) sont simultanément couvertes — condition nécessaire pour disposer d'une image complète de l'AOI ce jour-là. Ces deux indicateurs sont calculés sans filtre de couverture nuageuse — ils reflètent la disponibilité catalogue brute.

**Livrable (2.5)** : `data/raw/s2/AVAILABILITY_REPORT.json` (rapport mensuel) et `data/raw/s2/catalogue_dedup.parquet` (liste complète des scènes avec identifiants CDSE, utilisée par S2 pour le téléchargement).

---

### S2 — Séries temporelles (`03_series_s2.ipynb`)

#### 3.1 — Masque nuages et sélection des scènes (`f_valid_aoi`)

**SCL** : la bande Scene Classification Layer (60 m) du produit L2A Sen2Cor classe chaque pixel en 12 catégories. Les classes invalides retenues sont 1 (pixels saturés/défectueux), 3 (ombres nuageuses), 7 (nuages bas, probabilité faible), 8 (nuages moyennement probables), 9 (nuages hautement probables), 10 (cirrus) et 11 (neige/glace) — conformément aux recommandations HR-VPP/Sen4CAP. La classe 7 est particulièrement utile en contexte normand où les nuages bas d'automne-hiver sont fréquemment sous-détectés par l'algorithme SCL.

**`f_valid_aoi`** : pour chaque scène, fraction de pixels valides (hors classes invalides) dans l'emprise de l'AOI. Calculée en reprojetant l'AOI dans le CRS de la SCL (UTM dérivé du `tile_id` : EPSG 32600 + numéro de zone, car le driver JP2OpenJPEG ne renseigne pas toujours le CRS dans les métadonnées). Seuil de rétention : `f_valid_aoi ≥ 0.01` (au moins 1 % de pixels valides sur l'AOI). Ce seuil très permissif permet de conserver le maximum de scènes tout en éliminant celles entièrement couvertes de nuages — le composite mensuel par médiane gère la qualité résiduelle.

**Résultats 3.1** : 552 scènes retenues sur 1 071 cataloguées (52 %), 9 NaN (timeouts CDSE). Distribution bimodale : médiane à 0,031, 75e percentile à 0,411 — beaucoup de scènes quasi-nuageuses et des scènes claires franchement exploitables.

**Saisonnalité de `f_valid_aoi`** : la distribution bimodale ci-dessus masque une forte structure saisonnière, quantifiée mois par mois sur la fenêtre complète (`scenes_totales` reste stable à 62-70, cohérent avec une revisite orbitale constante — la variation vient entièrement de la météo, pas du catalogue) :

| Mois | Scènes cataloguées | `f_valid_aoi` moyen | Scènes retenues |
|------|--------------------:|---------------------:|------------------:|
| 2023-09 | 66 | 0,438 | 50 |
| 2023-10 | 66 | 0,253 | 35 |
| 2023-11 | 66 | 0,116 | 31 |
| 2023-12 | 69 | 0,258 | 26 |
| 2024-01 | 67 | 0,237 | 36 |
| 2024-02 | 62 | 0,047 | 15 |
| 2024-03 | 70 | 0,080 | 34 |
| 2024-04 | 66 | 0,183 | 49 |
| 2024-05 | 66 | 0,156 | 36 |
| 2024-06 | 66 | 0,326 | 43 |
| 2024-07 | 70 | 0,330 | 53 |
| 2024-08 | 66 | 0,431 | 46 |
| 2024-09 | 66 | 0,260 | 34 |
| 2024-10 | 69 | 0,336 | 32 |
| 2024-11 | 66 | 0,122 | 18 |
| 2024-12 | 70 | 0,068 | 14 |

L'été (juillet-septembre, `f_valid_aoi` moyen 0,33-0,44) est nettement plus favorable que l'hiver (décembre-février, 0,05-0,26), avec un creux marqué en février et décembre 2024 — cohérent avec le climat océanique normand (couverture nuageuse persistante en fin d'automne et en hiver). Cette hétérogénéité saisonnière se répercute mécaniquement sur la densité d'observations des composites mensuels (3.3) : un composite hivernal peut être bâti sur 5-6 dates d'acquisition contre 11-14 en été, avec un bruit résiduel potentiellement plus élevé — point à garder en tête lors de l'interprétation des métriques phénologiques (S4).

**Téléchargement SCL** : via l'API OData `/Nodes/` (`download.dataspace.copernicus.eu`), qui diffère de l'API catalogue (`catalogue.dataspace.copernicus.eu`). La réponse Nodes utilise la clé `"result"` (et non `"value"` comme le catalogue). Le `granule_id` (identifiant interne du répertoire GRANULE/ dans l'arborescence SAFE) est récupéré dynamiquement par un appel Nodes préalable, car il n'est pas disponible dans la réponse catalogue.

#### 3.2 — Téléchargement des bandes spectrales et calcul des indices

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

#### 3.2 bis — Contrôle qualité des correctifs `resample_to_20m`

**Correctif 1 — nodata JP2** : les fichiers JP2 L2A codent les pixels hors fauchée (bord de tuile) en valeur 0, et non en NaN. Le premier appel `rasterio.warp.reproject` de `resample_to_20m` ne précisait pas `src_nodata=0` / `dst_nodata=np.nan` — ces zéros étaient donc traités comme de la réflectance valide et mélangés aux pixels voisins par l'interpolation bilinéaire, produisant un artefact rectiligne fixe aligné sur la géométrie de fauchée, invisible sans inspection visuelle du composite. Corrigé en spécifiant explicitement `src_nodata`/`dst_nodata`.

**Correctif 2 — fallback CRS codé en dur (31UCQ/31UCR)** : quand un JP2 source n'expose pas son CRS dans ses métadonnées (driver JP2OpenJPEG sur Windows, déjà documenté), `resample_to_20m` retombait sur un fallback codé en dur (`EPSG:32630`, zone UTM 30N) — correct par coïncidence pour 30UYA/30UYV, mais faux pour 31UCQ/31UCR (zone 31N), décalant les données d'un fuseau entier lors de la reprojection et produisant un résultat intégralement nodata. Découvert lors de l'investigation de 3.4 (2 278 parcelles « orphelines », correctement rasterisées mais sans la moindre valeur S2 sur toute la fenêtre d'observation). Diagnostic mené par élimination successive : hypothèse fauchée écartée (aucune corrélation avec le contour réel des tuiles ni avec l'orbite — 3 orbites relatives distinctes desservent les deux tuiles, toutes également affectées), géométrie AOI reprojetée validée (pas de déformation, test aller-retour par zone UTM concordant), avant d'isoler la cause au niveau du fichier bande natif lui-même (100 % nodata sur les 7 bandes, 149/149 scènes 31UCQ et 145/145 scènes 31UCR — systématique, pas occasionnel). Mécanisme confirmé par reproduction sur données réelles : un décalage d'un fuseau UTM appliqué à une bande valide (0 % nodata) produit exactement le symptôme observé (100 % nodata). Corrigé en réutilisant `ref_crs_wkt` (déjà résolu correctement par tuile via `get_tile_crs()`) comme fallback, plutôt que de redeviner le CRS bande par bande.

Cette cellule de contrôle qualité rescanne systématiquement les GeoTIFF de bandes *et* d'indices produits en 3.2 pour détecter à la fois les zéros résiduels (correctif 1) et les fichiers entièrement nodata (correctif 2) avant de poursuivre vers 3.3 — le premier correctif ne couvrait que le premier cas, ce qui explique pourquoi le second bug est resté invisible jusqu'à l'investigation manuelle de 3.4.

**Reprise après correctif 2** : contrairement au correctif 1 (qui produisait des valeurs *fausses* mélangées par interpolation), celui-ci produit du NaN pur — aucune valeur déjà en base n'est erronée, seule la densité d'observation est réduite dans les zones où 31UCQ/31UCR auraient dû contribuer. Reprise ciblée nécessaire : retéléchargement complet de 31UCQ (149 scènes) et 31UCR (145 scènes), les JP2 sources ayant été supprimés par ce même contrôle qualité après validation initiale (correctif 1 uniquement) ; suppression et rejeu intégral des composites (3.3) puisque `compute_monthly_composite` skip les fichiers déjà présents ; `TRUNCATE` et rejeu de 3.4, 3.5, 3.6. Correctif développé sur la branche `fix/nb03-crs-fallback-31n`. *Reprise en cours au moment de la rédaction — volumétrie ci-dessous à recalculer une fois terminée.*

#### 3.2 quater — Contrôle qualité de la couverture temporelle

**Motivation** : au-delà du contrôle pixel-par-pixel de 3.2 bis (zéros résiduels dans les bandes), un second contrôle qualité, indépendant des composites de 3.3, quantifie la couverture temporelle réelle disponible par mois — combien de dates valides couvrent chaque pixel de l'AOI. Sans cette mesure, un mois structurellement peu couvert (nébulosité hivernale) est indiscernable, à l'œil, d'un artefact de traitement.

**Méthode** : pour chaque mois, un raster `n_valid` (int16, résolution 20 m) compte, pixel par pixel, le nombre de dates d'acquisition ayant fourni une observation valide (masque SCL + hors-fauchée déjà appliqué en 3.2). Une seule variable (NDVI) suffit à ce calcul — le masque de validité est partagé par les 11 variables issues d'une même scène. Les rasters sont sauvegardés (`data/completude/<YYYY-MM>_n_valid.tif`, Deflate, tuilé 256×256) pour réutilisation en 3.5, avec un mécanisme idempotent basé sur la comparaison des dates de modification (raster de complétude vs fichiers indices sources) plutôt qu'un simple test d'existence — même principe de prudence que la leçon retenue en 3.2 bis sur les fichiers intermédiaires obsolètes silencieusement réutilisés.

**Résultats** — pourcentage de pixels AOI n'ayant reçu aucune date valide dans le mois :

| Mois | Dates retenues | % pixels à 0 date valide |
|------|----------------:|---------------------------:|
| 2023-09 | 14 | 10,5 |
| 2023-10 | 11 | 13,8 |
| 2023-11 | 10 | 43,6 |
| 2023-12 | 8 | 70,4 |
| 2024-01 | 10 | 55,1 |
| 2024-02 | 4 | 41,2 |
| 2024-03 | 12 | 44,3 |
| 2024-04 | 14 | 38,7 |
| 2024-05 | 11 | 21,6 |
| 2024-06 | 13 | 13,4 |
| 2024-07 | 14 | 14,4 |
| 2024-08 | 14 | 10,5 |
| 2024-09 | 10 | 10,5 |
| 2024-10 | 10 | 10,5 |
| 2024-11 | 5 | 22,7 |
| 2024-12 | 6 | 66,7 |

La dégradation hivernale est progressive et monotone (10,5 % en septembre → 70,4 % en décembre 2023), cohérente avec le climat océanique normand déjà documenté en 3.1, et se répète à l'identique sur le second hiver (66,7 % en décembre 2024) — signe d'un pattern saisonnier structurel plutôt que d'un accident météorologique isolé.

**Enquête sur un artefact apparent (composite EVI décembre 2024)** : une bande sombre parfaitement rectiligne, orientée comme une fauchée satellite, a été repérée à l'aperçu Windows sur le composite EVI de décembre 2024 — signature visuelle a priori compatible avec une résurgence du bug nodata de 3.2 bis. Le diagnostic (distinction entre pixels exactement à 0, signature du bug, et pixels proches de 0 mais non nuls, signal réel possible — l'AOI inclut la façade littorale de la Manche et l'estuaire de Seine) a écarté le bug : 13 pixels exactement à 0 contre 1 352 319 pixels proches de 0 non nuls sur le fichier testé, confirmé également nul sur une tuile sans littoral (30UCQ, 0 suspect détecté). La comparaison avec le raster `n_valid` a ensuite montré une concordance de 85,7 % entre la bande sombre et la zone à 0 date valide : décembre 2024 n'a eu qu'une seule date exploitable sur l'ensemble de l'AOI (06/12, orbite R137, `f_valid_aoi` 0,86–0,91 sur les 4 tuiles), et la zone hors fauchée propre à cette unique scène n'a été compensée par aucune autre date claire du mois — le composite hérite donc, à raison, du bord de fauchée de cette seule scène. Conclusion : le correctif nodata de 3.2 bis fonctionne correctement ; la bande observée est un déficit de couverture réel, pas un artefact numérique.

**Implication pour S3/S4** : le taux de couverture par mois ne suffit pas à décider, à l'échelle de la parcelle, si une feature mensuelle doit être conservée, imputée ou exclue — voir 3.5 pour l'indicateur à la granularité parcelle × mois.

#### 3.3 — Composite mensuel

**Stratégie** : construction en deux étapes successives, à l'échelle de l'AOI entière.

Étape 1 — **Image journalière** : pour chaque date d'acquisition, toutes les scènes disponibles (1 à 4 selon les recouvrements entre tuiles) sont mosaïquées pour couvrir l'AOI. Chaque pixel reçoit la médiane des valeurs valides issues de toutes les tuiles qui le couvrent ce jour-là.

Étape 2 — **Composite mensuel** : pour chaque mois civil, la médiane pixel à pixel de toutes les images journalières valides (`f_valid_aoi ≥ 0.01`) du mois est calculée. La médiane est robuste aux nuages résiduels non détectés par la SCL et aux outliers radiométriques ponctuels. Un pixel sans aucune acquisition valide dans le mois reçoit la valeur nodata (-9999).

**Implémentation** : traitement par chunks de scènes (12 scènes par chunk) pour maîtriser l'empreinte mémoire, suivi d'une médiane des médianes de chunks.

**Parallélisme évalué et écarté (threads)** : `ThreadPoolExecutor` sur la boucle des 11 variables a été testé pour accélérer S2.3 (source distincte du GeoTIFF, pas de JP2 en jeu à ce stade). Diagnostic par observation CPU : le taux plafonnait à 25-50 % au lieu des ~75 % attendus pour 3 threads actifs — signe de contention sur le GIL plutôt que d'un vrai parallélisme, probablement entretenue par `gc.collect()` appelé à chaque date (un `gc.collect()` est un stop-the-world qui bloque tous les threads Python, pas seulement l'appelant). Le `gc.collect()` par date a été retiré (inutile : `del` suffit à libérer des tableaux numpy sans cycle de références), et le threading a finalement été abandonné au profit d'une boucle séquentielle simple — le gain réel restait marginal et n'en justifiait pas la complexité.

**Parallélisme opérationnel (multi-processus)** : pour accélérer la reprise complète après le correctif nodata (176 composites à produire, ~7-10 min/variable en séquentiel), deux notebooks identiques ont été exécutés en parallèle sur deux kernels Jupyter distincts (deux processus OS, donc deux GIL indépendants — contrairement au threading, un vrai gain), l'un traitant les mois en ordre croissant, l'autre en ordre décroissant. Aucun conflit possible : chaque mois lit/écrit dans des sous-dossiers disjoints (`composites/<YYYY-MM>/`), et `out_path.exists()` en tête de fonction protège toute écriture concurrente si les deux runs venaient à converger vers le même mois.

**Structure de sortie** : `data/raw/s2/composites/<YYYY-MM>/<variable>.tif` — un GeoTIFF AOI par mois × variable (176 fichiers pour 16 mois × 11 variables).

#### 3.4 — Agrégation zonale et chargement PostGIS

**Statistiques** : pour chaque parcelle RPG × mois × variable, quatre statistiques zonales sont calculées sur les pixels valides du composite : mean, std, p10, p90. La combinaison mean + std capture la tendance centrale et l'hétérogénéité intra-parcelle. p10/p90 enrichissent le feature set pour la classification sans surcoût significatif.

**Méthode** : les 80 683 parcelles (après dissolve des 6 doublons `id_parcel`) sont rasterisées en un raster de labels sur la grille AOI (20 m, EPSG:2154), construit une seule fois. Pour chaque composite, les statistiques sont calculées par tri vectorisé numpy (argsort + split par label) — O(n log n) sur les pixels valides, sans appel rasterio.mask par parcelle. 2 751 parcelles (0,023 % de la surface) ne capturent aucun centre de pixel à 20 m et sont absentes de la table.

**Parcelles orphelines (couverture S2 nulle)** : au-delà des 2 751 non rasterisées, 2 278 parcelles supplémentaires *sont* correctement rasterisées mais n'ont jamais reçu la moindre valeur S2 valide sur l'ensemble de la fenêtre d'observation — vérifié exhaustivement sur 106 à 152 dates selon la tuile, 100 % de pixels NaN à chaque fois, indépendamment de la couverture nuageuse. Cause identifiée : le fallback CRS codé en dur de `resample_to_20m` (voir 3.2 bis, correctif 2), pas une limite physique de fauchée comme initialement suspecté. Les parcelles orphelines correspondent exactement à la portion de l'AOI couverte uniquement par 31UCQ/31UCR (les deux tuiles affectées), sans recouvrement de secours par 30UYA/30UYV — le compositing journalier (médiane, insensible au NaN) a silencieusement absorbé l'absence totale de contribution de ces deux tuiles partout où une autre tuile fonctionnelle prenait le relais, ne laissant apparaître le problème qu'aux endroits sans recouvrement. **Total exclu du feature set S3 avant reprise** : 2 751 + 2 278 = 5 029 parcelles sur 80 683 (6,2 %) — chiffre voué à diminuer significativement une fois la reprise de 3.2/3.3/3.4 sur 31UCQ/31UCR terminée (seules les 2 751 non rasterisées resteront structurellement exclues).

**Table PostGIS** : `derived.s2_parcelles_monthly` (clé primaire composite `id_parcel × mois × variable`). Les insertions utilisent `INSERT ... ON CONFLICT DO NOTHING` pour permettre les relances partielles — le calcul zonal (coûteux) est court-circuité en amont de l'insertion pour les paires mois × variable déjà présentes en base, pas seulement l'insertion elle-même.

**Volumétrie (run antérieur au correctif 2, invalidé)** : 10 952 293 lignes — *à recalculer une fois 31UCQ/31UCR retéléchargées et 3.2 → 3.4 rejouées*. Ce chiffre sous-estime la couverture réelle disponible, puisque 31UCQ/31UCR n'ont contribué aucune valeur à aucun composite sur toute la période (voir 3.2 bis, correctif 2). Pour mémoire, la logique de comparaison utilisée sur ce run (écart de −20 % vs maximum théorique, expliqué par les déficits hivernaux de 3.2 quater) reste valable en principe, mais devra être revérifiée avec les chiffres post-reprise, puisqu'une partie de cet écart provenait en réalité du bug plutôt que de la seule météo.

**Correction EVI août 2024** : le dénominateur EVI (B08 + 6×B04 − 7,5×B02 + 1) devenait instable en pleine végétation estivale. Corrigé par un garde-fou `np.where(abs(denom) < 0.001, 0.001, denom)`. Le composite d'août a été recalculé directement depuis les composites de bandes (ratio des médianes et non médiane des ratios — écart négligeable sur un indice normalisé).

#### 3.5 — Agrégation zonale de la complétude temporelle

**Motivation** : le pourcentage de pixels sans date valide calculé en 3.2 quater est une mesure globale à l'échelle de l'AOI — insuffisante pour décider, parcelle par parcelle, si une feature mensuelle doit être conservée, imputée ou exclue en S3. Une table dédiée porte cet indicateur à la granularité `id_parcel × mois`.

**Méthode** : les rasters `n_valid` produits en 3.2 quater sont agrégés par parcelle avec le même raster de labels et la même approche vectorisée (tri + split par label) que 3.4, en calculant le nombre moyen de dates valides et le pourcentage de la surface parcellaire couverte par au moins une date. Contrairement aux statistiques spectrales de 3.4, les pixels à 0 date valide sont explicitement inclus dans la moyenne — 0 est ici une donnée informative (absence de couverture), pas une valeur à exclure.

**Table PostGIS** : `derived.s2_parcelles_completude` (clé primaire composite `id_parcel × mois`), colonnes `n_dates_valides_moy` et `pct_pixels_couverts`. Table séparée de `s2_parcelles_monthly` plutôt qu'une colonne supplémentaire de cette dernière : le masque de validité (SCL + hors-fauchée) est identique pour les 11 variables issues d'une même scène, donc une colonne dénormalisée aurait dupliqué 11 fois la même valeur par parcelle × mois. Insertions en `ON CONFLICT DO NOTHING`, avec un skip au niveau du mois entier tant que le raster de complétude correspondant existe déjà sur disque — traitement incrémental, compatible avec une exécution de 3.2 quater encore en cours sur les mois les plus récents.

#### 3.6 — Agrégation zonale NDVI aux dates d'acquisition

**Motivation** : les composites mensuels (3.4) écrasent la dynamique fine du couvert, ce qui limite la précision d'extraction des métriques phénologiques (SOS/POS/EOS, S4). En complément, un profil NDVI est agrégé par parcelle **à chaque date d'acquisition**, sans compositage temporel — un échantillonnage irrégulier (trous nuageux, densité orbitale variable) mais fidèle à la trajectoire réelle de la végétation.

**Source** : les GeoTIFF NDVI par scène produits en 3.2 (déjà masqués SCL), reprojetés sur la grille AOI 20 m. Pour une date couverte par plusieurs tuiles, les scènes sont mosaïquées par médiane pixel à pixel (même logique que l'étape journalière du compositage 3.3). L'agrégation zonale réutilise le raster de labels et le tri vectorisé numpy de 3.4.

**Table PostGIS** : `derived.s2_parcelles_ndvi_dates` (clé primaire composite `id_parcel × date`), colonnes `mean`, `std`, `n_pixels`. Le champ `n_pixels` compte les pixels valides ayant contribué à la statistique, et permet de filtrer les parcelles à faible couverture lors du lissage phénologique. Insertions en `ON CONFLICT DO NOTHING` pour les relances partielles.

---

### S3 — Classification *(baseline exécutée — contenu ci-dessous à mettre à jour)* (`04_classification.ipynb`)

**Baseline** : Random Forest scikit-learn, split spatial par blocs (pas de split aléatoire qui créerait une fuite spatiale entre parcelles voisines). Features : les 704 colonnes de `derived.s2_parcelles_monthly` pivotées en wide format par parcelle. Cibles : les codes cultures RPG regroupés en 7-8 classes (blé tendre, orge, colza, maïs, betterave, lin, prairies, autres).

**Évaluation** : matrice de confusion, F1 macro et par classe, avec attention portée aux classes minoritaires. Cible indicative F1 macro ≥ 0,85 sur les grandes cultures.

**Option DL** : 1D-CNN ou LSTM sur la dimension temporelle (16 pas de temps), à envisager si la baseline ne satisfait pas les critères de validation. Le gain de performance sera mis en regard de la complexité supplémentaire.

---

### S4 — Divergence et phénologie *(prévu)* (`05_divergence_pheno.ipynb`)

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
| QC systématique avant suppression des intermédiaires | Suppression dès l'écriture des GeoTIFF finaux | Le bug nodata (fauchée codée 0 au lieu de NaN, détecté en 3.2 bis) n'a été détecté qu'après suppression des JP2/GeoTIFF intermédiaires, rendant la correction rétroactive impossible et imposant une reprise complète |
| Parallélisme multi-processus (kernels séparés) pour la reprise 3.3 | `ThreadPoolExecutor` sur la boucle des 11 variables | Contention GIL observée (25-50 % CPU au lieu de ~75 % attendu) ; deux processus OS indépendants contournent le GIL, contrairement aux threads |
| Split spatial par blocs (classification) | Split aléatoire | Le split aléatoire crée une fuite spatiale : des parcelles voisines se retrouvent en train et en test |
| Format par nature de donnée (parquet métadonnées / GeoTIFF composites / PostGIS parcelles-séries) | Format unique (tout PostGIS ou tout fichiers plats) | Parquet pour le catalogue de scènes (lecture séquentielle, pas de requête spatiale) ; GeoTIFF pour les composites raster (accès fenêtré rasterio, interopérabilité QGIS) ; PostGIS pour les données vecteur/relationnelles nécessitant jointures, requêtes spatiales et relances partielles par clé composite |
| Table `s2_parcelles_completude` séparée de `s2_parcelles_monthly` | Colonne de complétude ajoutée à `s2_parcelles_monthly` | Le masque de validité est partagé par les 11 variables d'une même scène ; une colonne dénormalisée aurait dupliqué la même valeur 11 fois par parcelle × mois |
| Idempotence par comparaison de dates de modification (raster de complétude vs sources) | Simple test d'existence du fichier de sortie | Un test d'existence seul aurait reproduit le piège déjà rencontré en 3.2 bis (fichier intermédiaire présent mais généré avant un correctif, silencieusement jamais régénéré) |
| Fallback CRS par `ref_crs_wkt` (déjà résolu via `get_tile_crs`) | Fallback codé en dur sur une zone UTM fixe | Un EPSG fixe supposait à tort que toutes les tuiles étaient en zone 30N ; correct par coïncidence pour 30UYA/30UYV, faux pour 31UCQ/31UCR (décalage d'un fuseau, 100 % nodata sur 294 scènes) |
---

## Limites documentées

**Optique seule** : pas de fusion radar Sentinel-1. La couverture nuageuse normande est gérée par masquage SCL, composite temporel et indicateur `f_valid_aoi`, mais les mois d'hiver restent sous-représentés. L'ajout de Sentinel-1 (cohérence, rétrodiffusion) est une perspective naturelle.

**Résolution 20 m** : comme le 3STR, la chaîne ne distingue ni les petites parcelles (< 0,5 ha) ni les cultures en mélange. C'est une limite intrinsèque de Sentinel-2 à cette résolution.

**Vérité terrain RPG** : le RPG enregistre la culture déclarée, pas la culture réellement implantée. Les erreurs de déclaration sont traitées comme du bruit dans la classification et comme du signal dans la détection de divergence.

**Évaluation géographiquement contrainte** : l'évaluation est limitée à la Normandie. La généralisation à d'autres régions (autre RPG, autre phénologie) nécessiterait un réétalonnage.

**Fallback CRS corrigé (31UCQ/31UCR, en cours de reprise)** : un bug de reprojection (fallback CRS codé en dur sur la zone 30N, voir 3.2 bis correctif 2) a empêché 31UCQ/31UCR de contribuer la moindre valeur S2 sur toute la fenêtre d'observation, jusqu'à sa découverte lors de l'investigation des 2 278 parcelles « orphelines » de 3.4. Corrigé et en cours de reprise (retéléchargement + rejeu 3.2 → 3.6) au moment de la rédaction — cette entrée sera retirée une fois la reprise validée, ne laissant que les 2 751 parcelles non rasterisées (3.4) comme exclusion structurelle réelle.
