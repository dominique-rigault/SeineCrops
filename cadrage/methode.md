# SeineCrops - Note de méthode

**Suivi et classification des cultures par séries temporelles Sentinel-2**
Plateaux de la Basse-Seine (Caux & Neubourg), Normandie

*Licence documentation : CC-BY 4.0*
*Licence code : MIT*

---

## Synthèse

### Objectif et positionnement

SeineCrops reproduit, à échelle réduite et en open source, la logique du **3STR** (Système de Suivi des Surfaces en Temps Réel), le dispositif opérationnel imposé par la PAC 2023-2027 et mis en œuvre en France par l'ASP. L'ambition n'est pas de produire un résultat de recherche original, mais de démontrer une **chaîne d'ingénierie de bout en bout** : de la donnée satellite brute à la carte interactive, en passant par une base PostGIS spatio-temporelle, un modèle de classification et une API de restitution.

### Données

Deux sources open data constituent l'ossature du projet. **Sentinel-2 L2A** (Copernicus Data Space Ecosystem) fournit les séries temporelles d'images satellite à 10-20 m de résolution, librement accessibles via l'API OData CDSE. Le **RPG 2024** (Registre Parcellaire Graphique, IGN) fournit les contours de 80 689 parcelles agricoles et leur culture déclarée pour la zone d'étude - c'est la vérité terrain pour l'apprentissage supervisé.

### Zone d'étude et période

L'AOI (3 349 km²) couvre le Pays de Caux et le plateau du Neubourg, de part et d'autre de la Seine, en Normandie. Ce territoire d'openfield grandes cultures est traversé par quatre tuiles Sentinel-2 (30UYA, 31UCR, 30UYV, 31UCQ), organisées en deux paires nord/sud. La période d'observation couvre **septembre N à décembre N+1** (~16 mois), alignée sur la campagne RPG N+1 : cette fenêtre étendue capture l'implantation du colza en tête et l'arrachage betterave/récolte maïs en queue.

### Pipeline

La chaîne se déroule en six sprints séquentiels. S1 ingère le RPG dans PostGIS et diagnostique la disponibilité Sentinel-2. S2 télécharge les bandes spectrales, calcule les indices (NDVI, EVI, NDWI, NDRE), produit les composites mensuels et agrège les statistiques zonales par parcelle. S3 entraîne et évalue un modèle de classification (Random Forest en baseline, option Deep Learning). S4 détecte les divergences entre couvert observé et culture déclarée, et extrait les métriques phénologiques (SOS/POS/EOS). S5 expose les résultats via une API FastAPI et une carte web interactive. S6 industrialise la chaîne (Airflow, tests, CI/CD).

### Feature set

Le feature set d'entrée du modèle compte **704 features par parcelle** : 11 variables (7 bandes spectrales + 4 indices) × 4 statistiques zonales (mean, std, p10, p90) × 16 mois. Les bandes retenues sont B02, B04, B05, B06, B07, B08 et B11 - couvrant le visible, le red-edge et le SWIR, qui sont les régions spectrales les plus discriminantes pour les cultures tempérées.

### Critères de succès

Le projet est piloté par six questions d'ingénierie mesurables : robustesse au manque d'observations nuageuses (profil reconstruit pour ≥ 90 % des parcelles), qualité de classification (F1 macro ≥ 0,85 sur les grandes cultures), gain du deep learning sur la baseline, fiabilité des alertes de divergence, absence de fuite spatiale dans l'évaluation, et reproductibilité de la chaîne.

---

## Note technique

### S0 - Cadrage

**AOI** : dessinée sous QGIS pour couvrir l'openfield normand (Caux + Neubourg) en excluant le Pays de Bray (bocage, petites parcelles). Stockée en GeoJSON (EPSG:4326) dans `data/vector/aoi/aoi_seinecrops.geojson`.

**Année de référence** : RPG 2024 (millésime le plus récent disponible). La fenêtre Sentinel-2 est alignée sur ce millésime : septembre 2023 → décembre 2024.

**Dépôt** : GitHub public, MIT pour le code, CC-BY 4.0 pour la documentation. Séparation stricte code/données : les données lourdes ne sont pas versionnées ; leur traçabilité est assurée par des fichiers JSON (SHA-256, provenance, versions).

**Grille des tuiles Sentinel-2** : index shapefile téléchargé depuis [justinelliotmeyers/Sentinel-2-Shapefile-Index](https://github.com/justinelliotmeyers/Sentinel-2-Shapefile-Index), converti en GeoPackage (EPSG:2154) et stocké dans `data/vector/s2_tiles/s2_tiles_2154.gpkg`. Utilisé pour la visualisation du recouvrement des 4 tuiles sur l'AOI.

---

### S1 - Données

#### 1 - Ingestion RPG (`01_ingestion_rpg.ipynb`)

**Source** : archive GeoPackage RPG v3.0, base RPG_Parcelles, région Normandie (R28, millésime 2024), téléchargée depuis geoservices.ign.fr. 528 950 parcelles pour la Normandie entière.

**Chargement PostGIS (4.1)** : via le driver PGDUMP de GDAL + `psql` (les drivers `ogr2ogr` PostgreSQL natif et pyogrio PostgreSQL sont indisponibles dans l'environnement Windows de développement). Les lignes `CREATE SCHEMA` sont retirées avant ingestion quand le schéma existe déjà.

**Schéma** : deux schémas PostGIS distincts. `raw` reçoit les données brutes sans modification. `derived` reçoit les données filtrées et transformées - la table `rpg_parcelles_aoi` contient les 80 689 parcelles intersectant l'AOI, conservées entières (pas de découpe à la frontière).

**Principe AOI-first (4.1bis)** : la QA géométrique (`ST_IsValid` / `ST_MakeValid`) est appliquée à `raw` *avant* le filtre AOI - une parcelle invalide dans l'AOI doit être réparée ou tracée explicitement, pas silencieusement exclue par le filtre spatial. 10 géométries invalides ont été détectées et réparées avant filtrage.

**Filtre AOI (4.3)** : `ST_Intersects` plutôt que `ST_Intersection` - on conserve les parcelles entières pour la cohérence phénologique. Une parcelle tronquée à la frontière de l'AOI perdrait une partie de ses pixels et biaiserait les statistiques zonales.

**Provenance (5.3)** : quatre fichiers JSON consolident la traçabilité (`SOURCE.json`, `RECON.json`, `DB.json`, `INGESTION_REPORT.json`).

#### 2 - Disponibilité Sentinel-2 (`02_disponibilite_s2.ipynb`)

**API (2.1, 2.2)** : OData CDSE (`catalogue.dataspace.copernicus.eu`), collection SENTINEL-2, type S2MSI2A (L2A), filtre par `tileId`. Pas de filtre `cloudCover` à la requête catalogue - toutes les scènes disponibles sont recensées, y compris les plus nuageuses. La couverture nuageuse déclarée (`cloud_cover_catalogue`) est conservée à titre informatif ; la disponibilité effective sur l'AOI (`f_valid_aoi`), calculée à partir de la bande SCL, est l'objet de 3.1.

**Déduplication (2.4)** : CDSE met à disposition plusieurs baselines de traitement Sen2Cor pour les mêmes acquisitions (ex. N0509 et N0510). On conserve la baseline la plus récente par scène (même date et tuile), ce qui élimine les doublons sans perdre d'acquisitions.

**Métriques de disponibilité (2.4)** : deux indicateurs complémentaires sont calculés pour chaque mois. La *couverture partielle* compte les jours avec au moins une scène sur l'une quelconque des 4 tuiles. La *couverture quasi complète* compte les jours où les paires nord (30UYA + 31UCR) ET sud (30UYV + 31UCQ) sont simultanément couvertes - condition nécessaire pour disposer d'une image complète de l'AOI ce jour-là. Ces deux indicateurs sont calculés sans filtre de couverture nuageuse - ils reflètent la disponibilité catalogue brute.

**Livrable (2.5)** : `data/raw/s2/AVAILABILITY_REPORT.json` (rapport mensuel) et `data/raw/s2/catalogue_dedup.parquet` (liste complète des scènes avec identifiants CDSE, utilisée par S2 pour le téléchargement).

---

### S2 - Séries temporelles (`03_series_s2.ipynb`)

#### 3.1 - Masque nuages et sélection des scènes (`f_valid_aoi`)

**SCL** : la bande Scene Classification Layer (60 m) du produit L2A Sen2Cor classe chaque pixel en 12 catégories. Les classes invalides retenues sont 1 (pixels saturés/défectueux), 3 (ombres nuageuses), 7 (nuages bas, probabilité faible), 8 (nuages moyennement probables), 9 (nuages hautement probables), 10 (cirrus) et 11 (neige/glace) - conformément aux recommandations HR-VPP/Sen4CAP. La classe 7 est particulièrement utile en contexte normand où les nuages bas d'automne-hiver sont fréquemment sous-détectés par l'algorithme SCL.

**`f_valid_aoi`** : pour chaque scène, fraction de pixels valides (hors classes invalides) dans l'emprise de l'AOI. Calculée en reprojetant l'AOI dans le CRS de la SCL (UTM dérivé du `tile_id` : EPSG 32600 + numéro de zone, car le driver JP2OpenJPEG ne renseigne pas toujours le CRS dans les métadonnées). Seuil de rétention : `f_valid_aoi ≥ 0.01` (au moins 1 % de pixels valides sur l'AOI). Ce seuil très permissif permet de conserver le maximum de scènes tout en éliminant celles entièrement couvertes de nuages - le composite mensuel par médiane gère la qualité résiduelle.

**Résultats 3.1** : 552 scènes retenues sur 1 071 cataloguées (52 %), 9 NaN (timeouts CDSE). Distribution bimodale : médiane à 0,031, 75e percentile à 0,411 - beaucoup de scènes quasi-nuageuses et des scènes claires franchement exploitables.

**Saisonnalité de `f_valid_aoi`** : la distribution bimodale ci-dessus masque une forte structure saisonnière, quantifiée mois par mois sur la fenêtre complète (`scenes_totales` reste stable à 62-70, cohérent avec une revisite orbitale constante - la variation vient entièrement de la météo, pas du catalogue) :

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

L'été (juillet-septembre, `f_valid_aoi` moyen 0,33-0,44) est nettement plus favorable que l'hiver (décembre-février, 0,05-0,26), avec un creux marqué en février et décembre 2024 - cohérent avec le climat océanique normand (couverture nuageuse persistante en fin d'automne et en hiver). Cette hétérogénéité saisonnière se répercute mécaniquement sur la densité d'observations des composites mensuels (3.3) : un composite hivernal peut être bâti sur 5-6 dates d'acquisition contre 11-14 en été, avec un bruit résiduel potentiellement plus élevé - point à garder en tête lors de l'interprétation des métriques phénologiques (S4).

**Téléchargement SCL** : via l'API OData `/Nodes/` (`download.dataspace.copernicus.eu`), qui diffère de l'API catalogue (`catalogue.dataspace.copernicus.eu`). La réponse Nodes utilise la clé `"result"` (et non `"value"` comme le catalogue). Le `granule_id` (identifiant interne du répertoire GRANULE/ dans l'arborescence SAFE) est récupéré dynamiquement par un appel Nodes préalable, car il n'est pas disponible dans la réponse catalogue.

#### 3.2 - Téléchargement des bandes spectrales et calcul des indices

**Bandes retenues** : B02 (bleu, 10 m), B04 (rouge, 10 m), B05 (red-edge 1, 20 m), B06 (red-edge 2, 20 m), B07 (red-edge 3, 20 m), B08 (PIR large, 10 m), B11 (SWIR 1, 20 m). Toutes les bandes sont resamplées à **20 m** par interpolation bilinéaire (`rasterio.warp.reproject`, mode array-to-array pour contourner le bug JP2OpenJPEG/Windows), sur la grille de référence définie par B05 (natif 20 m) après découpe AOI.

**Découpe AOI** : appliquée dès la lecture (`rasterio.mask.mask` avec `crop=True`) pour ne charger en mémoire que les pixels dans l'emprise de l'AOI - indispensable pour maîtriser l'empreinte mémoire sur des tuiles de 110 × 110 km.

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

#### 3.2 bis - Contrôle qualité des correctifs `resample_to_20m`

**Correctif 1 - nodata JP2** : les fichiers JP2 L2A codent les pixels hors fauchée (bord de tuile) en valeur 0, et non en NaN. Le premier appel `rasterio.warp.reproject` de `resample_to_20m` ne précisait pas `src_nodata=0` / `dst_nodata=np.nan` - ces zéros étaient donc traités comme de la réflectance valide et mélangés aux pixels voisins par l'interpolation bilinéaire, produisant un artefact rectiligne fixe aligné sur la géométrie de fauchée, invisible sans inspection visuelle du composite. Corrigé en spécifiant explicitement `src_nodata`/`dst_nodata`.

**Correctif 2 - fallback CRS codé en dur (31UCQ/31UCR)** : quand un JP2 source n'expose pas son CRS dans ses métadonnées (driver JP2OpenJPEG sur Windows, déjà documenté), `resample_to_20m` retombait sur un fallback codé en dur (`EPSG:32630`, zone UTM 30N) - correct par coïncidence pour 30UYA/30UYV, mais faux pour 31UCQ/31UCR (zone 31N), décalant les données d'un fuseau entier lors de la reprojection et produisant un résultat intégralement nodata. Découvert lors de l'investigation de 3.4 (2 278 parcelles « orphelines », correctement rasterisées mais sans la moindre valeur S2 sur toute la fenêtre d'observation). Diagnostic mené par élimination successive : hypothèse fauchée écartée (aucune corrélation avec le contour réel des tuiles ni avec l'orbite - 3 orbites relatives distinctes desservent les deux tuiles, toutes également affectées), géométrie AOI reprojetée validée (pas de déformation, test aller-retour par zone UTM concordant), avant d'isoler la cause au niveau du fichier bande natif lui-même (100 % nodata sur les 7 bandes, 149/149 scènes 31UCQ et 145/145 scènes 31UCR - systématique, pas occasionnel). Mécanisme confirmé par reproduction sur données réelles : un décalage d'un fuseau UTM appliqué à une bande valide (0 % nodata) produit exactement le symptôme observé (100 % nodata). Corrigé en réutilisant `ref_crs_wkt` (déjà résolu correctement par tuile via `get_tile_crs()`) comme fallback, plutôt que de redeviner le CRS bande par bande.

Cette cellule de contrôle qualité rescanne systématiquement les GeoTIFF de bandes *et* d'indices produits en 3.2 pour détecter à la fois les zéros résiduels (correctif 1) et les fichiers entièrement nodata (correctif 2) avant de poursuivre vers 3.3 - le premier correctif ne couvrait que le premier cas, ce qui explique pourquoi le second bug est resté invisible jusqu'à l'investigation manuelle de 3.4.

**Reprise après correctif 2** : contrairement au correctif 1 (qui produisait des valeurs *fausses* mélangées par interpolation), celui-ci produit du NaN pur - aucune valeur déjà en base n'était erronée, seule la densité d'observation était réduite dans les zones où 31UCQ/31UCR auraient dû contribuer. Reprise ciblée effectuée : retéléchargement complet de 31UCQ (149 scènes) et 31UCR (145 scènes), les JP2 sources ayant été supprimés par ce même contrôle qualité après validation initiale (correctif 1 uniquement) ; suppression et rejeu intégral des composites (3.3) ; `TRUNCATE` et rejeu de 3.4, 3.5, 3.6. Correctif développé sur la branche `fix/nb03-crs-fallback-31n`. **Validée** : les 2 278 parcelles orphelines ont disparu (`NOT IN s2_parcelles_monthly` retombe à 2 751, le seul déficit structurel restant) et le plafond mensuel atteint désormais 77 932 lignes (11 variables × 77 932 parcelles) sur les 4 meilleurs mois - le maximum théorique absolu, sans aucun manque.

#### 3.2 quater - Contrôle qualité de la couverture temporelle

**Motivation** : au-delà du contrôle pixel-par-pixel de 3.2 bis (zéros résiduels dans les bandes), un second contrôle qualité, indépendant des composites de 3.3, quantifie la couverture temporelle réelle disponible par mois - combien de dates valides couvrent chaque pixel de l'AOI. Sans cette mesure, un mois structurellement peu couvert (nébulosité hivernale) est indiscernable, à l'œil, d'un artefact de traitement.

**Méthode** : pour chaque mois, un raster `n_valid` (int16, résolution 20 m) compte, pixel par pixel, le nombre de dates d'acquisition ayant fourni une observation valide (masque SCL + hors-fauchée déjà appliqué en 3.2). Une seule variable (NDVI) suffit à ce calcul - le masque de validité est partagé par les 11 variables issues d'une même scène. Les rasters sont sauvegardés (`data/completude/<YYYY-MM>_n_valid.tif`, Deflate, tuilé 256×256) pour réutilisation en 3.5, avec un mécanisme idempotent basé sur la comparaison des dates de modification (raster de complétude vs fichiers indices sources) plutôt qu'un simple test d'existence - même principe de prudence que la leçon retenue en 3.2 bis sur les fichiers intermédiaires obsolètes silencieusement réutilisés.

**Résultats** - pourcentage de pixels AOI n'ayant reçu aucune date valide dans le mois :

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

La dégradation hivernale est progressive et monotone (10,5 % en septembre → 70,4 % en décembre 2023), cohérente avec le climat océanique normand déjà documenté en 3.1, et se répète à l'identique sur le second hiver (66,7 % en décembre 2024) - signe d'un pattern saisonnier structurel plutôt que d'un accident météorologique isolé.

**Enquête sur un artefact apparent (composite EVI décembre 2024)** : une bande sombre parfaitement rectiligne, orientée comme une fauchée satellite, a été repérée à l'aperçu Windows sur le composite EVI de décembre 2024 - signature visuelle a priori compatible avec une résurgence du bug nodata de 3.2 bis. Le diagnostic (distinction entre pixels exactement à 0, signature du bug, et pixels proches de 0 mais non nuls, signal réel possible - l'AOI inclut la façade littorale de la Manche et l'estuaire de Seine) a écarté le bug : 13 pixels exactement à 0 contre 1 352 319 pixels proches de 0 non nuls sur le fichier testé, confirmé également nul sur une tuile sans littoral (30UCQ, 0 suspect détecté). La comparaison avec le raster `n_valid` a ensuite montré une concordance de 85,7 % entre la bande sombre et la zone à 0 date valide : décembre 2024 n'a eu qu'une seule date exploitable sur l'ensemble de l'AOI (06/12, orbite R137, `f_valid_aoi` 0,86–0,91 sur les 4 tuiles), et la zone hors fauchée propre à cette unique scène n'a été compensée par aucune autre date claire du mois - le composite hérite donc, à raison, du bord de fauchée de cette seule scène. Conclusion : le correctif nodata de 3.2 bis fonctionne correctement ; la bande observée est un déficit de couverture réel, pas un artefact numérique.

**Implication pour S3/S4** : le taux de couverture par mois ne suffit pas à décider, à l'échelle de la parcelle, si une feature mensuelle doit être conservée, imputée ou exclue - voir 3.5 pour l'indicateur à la granularité parcelle × mois.

#### 3.3 - Composite mensuel

**Stratégie** : construction en deux étapes successives, à l'échelle de l'AOI entière.

Étape 1 - **Image journalière** : pour chaque date d'acquisition, toutes les scènes disponibles (1 à 4 selon les recouvrements entre tuiles) sont mosaïquées pour couvrir l'AOI. Chaque pixel reçoit la médiane des valeurs valides issues de toutes les tuiles qui le couvrent ce jour-là.

Étape 2 - **Composite mensuel** : pour chaque mois civil, la médiane pixel à pixel de toutes les images journalières valides (`f_valid_aoi ≥ 0.01`) du mois est calculée. La médiane est robuste aux nuages résiduels non détectés par la SCL et aux outliers radiométriques ponctuels. Un pixel sans aucune acquisition valide dans le mois reçoit la valeur nodata (-9999).

**Implémentation** : traitement par chunks de scènes (12 scènes par chunk) pour maîtriser l'empreinte mémoire, suivi d'une médiane des médianes de chunks.

**Parallélisme évalué et écarté (threads)** : `ThreadPoolExecutor` sur la boucle des 11 variables a été testé pour accélérer S2.3 (source distincte du GeoTIFF, pas de JP2 en jeu à ce stade). Diagnostic par observation CPU : le taux plafonnait à 25-50 % au lieu des ~75 % attendus pour 3 threads actifs - signe de contention sur le GIL plutôt que d'un vrai parallélisme, probablement entretenue par `gc.collect()` appelé à chaque date (un `gc.collect()` est un stop-the-world qui bloque tous les threads Python, pas seulement l'appelant). Le `gc.collect()` par date a été retiré (inutile : `del` suffit à libérer des tableaux numpy sans cycle de références), et le threading a finalement été abandonné au profit d'une boucle séquentielle simple - le gain réel restait marginal et n'en justifiait pas la complexité.

**Parallélisme opérationnel (multi-processus)** : pour accélérer la reprise complète après le correctif nodata (176 composites à produire, ~7-10 min/variable en séquentiel), deux notebooks identiques ont été exécutés en parallèle sur deux kernels Jupyter distincts (deux processus OS, donc deux GIL indépendants - contrairement au threading, un vrai gain), l'un traitant les mois en ordre croissant, l'autre en ordre décroissant. Aucun conflit possible : chaque mois lit/écrit dans des sous-dossiers disjoints (`composites/<YYYY-MM>/`), et `out_path.exists()` en tête de fonction protège toute écriture concurrente si les deux runs venaient à converger vers le même mois.

**Structure de sortie** : `data/raw/s2/composites/<YYYY-MM>/<variable>.tif` - un GeoTIFF AOI par mois × variable (176 fichiers pour 16 mois × 11 variables).

#### 3.4 - Agrégation zonale et chargement PostGIS

**Statistiques** : pour chaque parcelle RPG × mois × variable, quatre statistiques zonales sont calculées sur les pixels valides du composite : mean, std, p10, p90. La combinaison mean + std capture la tendance centrale et l'hétérogénéité intra-parcelle. p10/p90 enrichissent le feature set pour la classification sans surcoût significatif.

**Méthode** : les 80 683 parcelles (après dissolve des 6 doublons `id_parcel`) sont rasterisées en un raster de labels sur la grille AOI (20 m, EPSG:2154), construit une seule fois. Pour chaque composite, les statistiques sont calculées par tri vectorisé numpy (argsort + split par label) - O(n log n) sur les pixels valides, sans appel rasterio.mask par parcelle. 2 751 parcelles (0,023 % de la surface) ne capturent aucun centre de pixel à 20 m et sont absentes de la table.

**Parcelles orphelines (couverture S2 nulle) - résolu** : au-delà des 2 751 non rasterisées, 2 278 parcelles supplémentaires *étaient* correctement rasterisées mais n'avaient jamais reçu la moindre valeur S2 valide sur l'ensemble de la fenêtre d'observation - vérifié exhaustivement sur 106 à 152 dates selon la tuile, 100 % de pixels NaN à chaque fois, indépendamment de la couverture nuageuse. Cause identifiée : le fallback CRS codé en dur de `resample_to_20m` (voir 3.2 bis, correctif 2), pas une limite physique de fauchée comme initialement suspecté. Les parcelles orphelines correspondaient exactement à la portion de l'AOI couverte uniquement par 31UCQ/31UCR, sans recouvrement de secours par 30UYA/30UYV. **Après reprise (retéléchargement + rejeu complet 3.2 → 3.6)** : les 2 278 parcelles orphelines ont disparu (`NOT IN s2_parcelles_monthly` retombe à 2 751), confirmé par un plafond mensuel atteignant 857 252 lignes (77 932 parcelles × 11 variables) sur les 4 meilleurs mois. **Total exclu du feature set S3** : 2 751 parcelles sur 80 683 (3,4 %) - uniquement le déficit structurel de rasterisation, seule exclusion réelle restante.

**Table PostGIS** : `derived.s2_parcelles_monthly` (clé primaire composite `id_parcel × mois × variable`). Les insertions utilisent `INSERT ... ON CONFLICT DO NOTHING` pour permettre les relances partielles - le calcul zonal (coûteux) est court-circuité en amont de l'insertion pour les paires mois × variable déjà présentes en base, pas seulement l'insertion elle-même.

**Volumétrie (run post-correctif 2, validé)** : 11 458 381 lignes - 506 388 de plus que le run précédent (10 952 293), soit exactement l'apport de 31UCQ/31UCR désormais fonctionnelles. Le plafond mensuel atteint 857 252 lignes (77 932 parcelles × 11 variables, le maximum théorique absolu) sur les 4 meilleurs mois (septembre 2023, août-octobre 2024) - aucun manque résiduel sur ces mois. L'écart restant sur les autres mois suit fidèlement le profil de couverture hivernale de 3.2 quater (ex. 21 252 lignes en décembre 2023, cohérent avec les 62,1 % de pixels sans date valide ce mois-là) - un déficit météorologique réel, plus un artefact du bug CRS.

**Correction EVI août 2024** : le dénominateur EVI (B08 + 6×B04 − 7,5×B02 + 1) devenait instable en pleine végétation estivale. Corrigé par un garde-fou `np.where(abs(denom) < 0.001, 0.001, denom)`. Le composite d'août a été recalculé directement depuis les composites de bandes (ratio des médianes et non médiane des ratios - écart négligeable sur un indice normalisé).

#### 3.5 - Agrégation zonale de la complétude temporelle

**Motivation** : le pourcentage de pixels sans date valide calculé en 3.2 quater est une mesure globale à l'échelle de l'AOI - insuffisante pour décider, parcelle par parcelle, si une feature mensuelle doit être conservée, imputée ou exclue en S3. Une table dédiée porte cet indicateur à la granularité `id_parcel × mois`.

**Méthode** : les rasters `n_valid` produits en 3.2 quater sont agrégés par parcelle avec le même raster de labels et la même approche vectorisée (tri + split par label) que 3.4, en calculant le nombre moyen de dates valides et le pourcentage de la surface parcellaire couverte par au moins une date. Contrairement aux statistiques spectrales de 3.4, les pixels à 0 date valide sont explicitement inclus dans la moyenne - 0 est ici une donnée informative (absence de couverture), pas une valeur à exclure.

**Table PostGIS** : `derived.s2_parcelles_completude` (clé primaire composite `id_parcel × mois`), colonnes `n_dates_valides_moy` et `pct_pixels_couverts`. Table séparée de `s2_parcelles_monthly` plutôt qu'une colonne supplémentaire de cette dernière : le masque de validité (SCL + hors-fauchée) est identique pour les 11 variables issues d'une même scène, donc une colonne dénormalisée aurait dupliqué 11 fois la même valeur par parcelle × mois. Insertions en `ON CONFLICT DO NOTHING`, avec un skip au niveau du mois entier tant que le raster de complétude correspondant existe déjà sur disque - traitement incrémental, compatible avec une exécution de 3.2 quater encore en cours sur les mois les plus récents.

#### 3.6 - Agrégation zonale NDVI aux dates d'acquisition

**Motivation** : les composites mensuels (3.4) écrasent la dynamique fine du couvert, ce qui limite la précision d'extraction des métriques phénologiques (SOS/POS/EOS, S4). En complément, un profil NDVI est agrégé par parcelle **à chaque date d'acquisition**, sans compositage temporel - un échantillonnage irrégulier (trous nuageux, densité orbitale variable) mais fidèle à la trajectoire réelle de la végétation.

**Source** : les GeoTIFF NDVI par scène produits en 3.2 (déjà masqués SCL), reprojetés sur la grille AOI 20 m. Pour une date couverte par plusieurs tuiles, les scènes sont mosaïquées par médiane pixel à pixel (même logique que l'étape journalière du compositage 3.3). L'agrégation zonale réutilise le raster de labels et le tri vectorisé numpy de 3.4.

**Table PostGIS** : `derived.s2_parcelles_ndvi_dates` (clé primaire composite `id_parcel × date`), colonnes `mean`, `std`, `n_pixels`. Le champ `n_pixels` compte les pixels valides ayant contribué à la statistique, et permet de filtrer les parcelles à faible couverture lors du lissage phénologique. Insertions en `ON CONFLICT DO NOTHING` pour les relances partielles.

---

### S3 - Classification *(baseline retenue comme modèle final - persistance en 4.4)* (`04_classification.ipynb`)

**Baseline** : Random Forest scikit-learn, split spatial par blocs (pas de split aléatoire qui créerait une fuite spatiale entre parcelles voisines). Features : les 704 colonnes de `derived.s2_parcelles_monthly` pivotées en wide format par parcelle. Cibles : les codes cultures RPG regroupés en 8 classes (blé tendre, orge, colza, maïs, betterave, lin, prairies, autres).

**Règle de décision sur la complétude (4.1bis)** : `derived.s2_parcelles_completude` (3.5) permet de trancher, par `id_parcel × mois`, entre trois actions - exclure (`pct_pixels_couverts == 0`, NaN structurel, laissé tel quel), imputer (0 % < couverture < 50 %, valeur remplacée par interpolation linéaire sur le mois adjacent), conserver (couverture ≥ 50 %, valeur inchangée). Les cellules "imputer" dont le voisin valide le plus proche est à plus d'un mois sont repassées en "exclure", pour éviter d'interpoler sur une distance temporelle où la dynamique végétative n'est plus linéaire. **Impact mesuré négligeable** : sur les 1 246 912 couples parcelle × mois, seuls 464 (0,037 %) remplissent la condition d'imputation à distance 1 mois, soit 20 416 valeurs sur 54 864 128 au niveau feature (0,037 %) - la démarche confirme la cohérence du feature set (le résidu NaN post-règle, 9 030 604, correspond exactement au diagnostic indépendant de 3.4) plus qu'elle n'en modifie le contenu.

**Résultats baseline (post-correctif nodata 3.2 bis)** : split spatial 75 blocs (61 043 train / 16 889 test, 78,3 % / 21,7 %), hyperparamètres par défaut (`n_estimators=300`, `max_depth=30`, `min_samples_leaf=5`, `class_weight="balanced"`). **F1 macro : 0,893** (accuracy test 0,881, accuracy train 0,941 - écart raisonnable, pas de surapprentissage marqué). Gain de +0,158 par rapport au run précédent sur données buguées (F1 macro 0,735), très majoritairement attribuable au correctif nodata de nb03 - la règle de complétude n'a affecté que 0,037 % des valeurs du feature set.

En excluant `autres` (fourre-tout hétérogène, pas une "grande culture" au sens du critère de succès), la moyenne des 7 classes agronomiques atteint **0,922**, au-delà du seuil cible. Classes les mieux discriminées : colza (F1 0,978), céréales d'hiver (0,962), betterave (0,960). Classe la plus faible : `autres` (F1 0,678–0,691 selon runs, precision ~0,66), avec une confusion bidirectionnelle dominante vers/depuis `prairie` - cohérent avec un usage du sol hétérogène (jachères, bandes enherbées, friches) partageant une signature spectrale proche de la prairie, plutôt qu'un problème de feature engineering ciblé (voir tests ci-dessous).

**Tuning `RandomizedSearchCV` - testé, écarté.** 20 itérations, `cv=3`, `scoring="f1_macro"` sur `n_estimators`, `max_depth`, `min_samples_leaf`, `max_features`. Meilleurs paramètres retenus par la recherche : `{n_estimators: 200, min_samples_leaf: 2, max_features: 0.2, max_depth: 30}`, F1 macro CV 0,884. Appliqués au test spatial : F1 macro 0,894 (quasi identique au baseline, +0,001), mais écart train/test passé de 0,060 à **0,107** - le surapprentissage s'est aggravé pour un gain nul. **Cause identifiée** : `RandomizedSearchCV(cv=3)` effectue un k-fold classique sur `X_train`, aveugle à l'information de bloc spatial (perdue au `.values` de la cellule de préparation des matrices). La CV interne autorise donc des parcelles spatialement voisines entre apprentissage et validation, ce qui favorise artificiellement les hyperparamètres les plus permissifs à la mémorisation locale (ici `min_samples_leaf=2`) - un score CV optimiste qui ne se vérifie pas sur le vrai test, spatialement disjoint. **Enseignement pour toute reprise future du tuning** : remplacer `cv=3` par un `GroupKFold` (ou équivalent) utilisant l'identifiant de bloc spatial comme groupe, pour que la CV interne respecte la même contrainte que le split externe. Non implémenté à ce stade - le gain visé (marginal, +0,001 sur la CV standard) ne justifiait pas l'effort de correction avant de trancher.

**Features temporelles dérivées - testées, écartées.** Test ciblé (3 features, à partir de `s2_parcelles_ndvi_dates`, 3.6, filtrée sur `n_pixels >= 5`) : `amplitude_ndvi` (max − min saison), `jour_max_ndvi` (position temporelle du maximum), `pente_ndvi_mai_aout` (dérivée des composites mensuels déjà en base, sans dépendance à 3.6). Couverture 69 534 / 77 932 parcelles (89,2 % - écart expliqué par les 2 751 parcelles sans pixel 20 m connues de S2.4 et le filtre `n_pixels`, NaN laissé tel quel pour le reste plutôt qu'imputé). Résultat : **F1 macro inchangé (0,893)**. La confusion `autres`/`prairie` s'est redistribuée sans se réduire (total quasi stable, ~1090 parcelles dans les deux runs) : recall `autres` en hausse mais precision stable, signe que les nouvelles features changent le sens des erreurs sans réduire le chevauchement de signal entre les deux classes. `pente_ndvi_mai_aout` s'est classée 5ᵉ/707 en importance malgré un gain nul - cohérent avec une feature qui condense une information déjà accessible au modèle en deux splits (`NDVI_mean_2024-08` − `NDVI_mean_2024-05`), donc un gain de commodité structurelle pour l'arbre plutôt qu'un signal réellement nouveau. **Hypothèse retenue** : la confusion `autres`/`prairie` est plus probablement un problème de label RPG (la classe `autres` mélange des usages du sol à dynamiques hétérogènes) qu'un manque de feature - piste distincte, non investiguée à ce stade, différée.

**Décision finale** : modèle retenu pour la persistance (4.4) = **baseline par défaut** (`rf_base`, sans tuning, sans features temporelles dérivées), par souci de simplicité et de robustesse - le gain potentiel des deux pistes testées (tuning, features temporelles) s'est révélé nul à marginal, l'une au prix d'un surapprentissage aggravé.

**Évaluation** : matrice de confusion, F1 macro et par classe, avec attention portée aux classes minoritaires. Cible indicative F1 macro ≥ 0,85 sur les grandes cultures - **atteinte** sur le baseline (0,922 hors classe `autres`).

**Option DL** : 1D-CNN ou LSTM sur la dimension temporelle (16 pas de temps), non prioritaire au vu des enseignements ci-dessus - la limite actuelle semble tenir davantage à la définition du label `autres` qu'à la capacité du modèle à exploiter la dynamique temporelle, ce qu'un DL ne résoudrait pas mécaniquement. À reconsidérer si l'investigation du label RPG écarte cette hypothèse.

---

### S4 - Divergence et phénologie (`05_divergence_pheno.ipynb`)

**Détection de divergence** : pour chaque parcelle, distance RMS (nanmean) au profil médian de sa classe déclarée, sur features standardisées (z-score, obligatoire - 704 features mélangeant bandes brutes en réflectance et indices bornés). Seuil médiane + 2·IQR par classe. Parcelles au-delà du seuil signalées comme divergentes - erreur de déclaration RPG, culture intermédiaire non déclarée, ou stress exceptionnel.

**Métriques phénologiques** : lissage par filtre de Whittaker pondéré par `n_pixels` (natif sur acquisitions irrégulières, sans biais d'interpolation - préféré à interpolation + Savitzky-Golay), sur grille régulière à pas de 5 jours, `lambda=800` calibré visuellement. Extraction de SOS, POS, EOS et longueur de saison (LOS) par seuils d'amplitude (20 %), avec fenêtre calendaire de recherche par classe (`FENETRES_PHENOLOGIE`) pour exclure le signal de la culture précédente / interculture en début de fenêtre. Flags de fiabilité `sos_en_bord` / `eos_en_bord` / `pos_en_bord`.

**Résultats S4** : 2 420 parcelles divergentes sur 77 932 (3,1 %). Couverture de la phénologie exploitable, mesurée en chaînant fiabilité et réalisme du LOS (±20 % de la médiane des parcelles conformes fiables) sur le **même dénominateur** (total des parcelles conformes de la classe - piège d'un calcul en deux étapes indépendantes, cf. note méthodologique ci-dessus) :

| classe | n conforme | phénologie fiable | LOS réaliste | % LOS réaliste / conforme |
|---|---:|---:|---:|---:|
| lin | 6 648 | 5 997 (83,6 %) | 5 013 | **75 %** |
| mais | 6 176 | 5 647 (81,4 %) | 4 597 | **74 %** |
| betterave | 3 321 | 2 921 (81,3 %) | 2 375 | **72 %** |
| colza | 3 341 | 2 416 (90,6 %) | 2 189 | **66 %** |
| legumes_fleurs | 2 995 | 2 540 (72,7 %) | 1 847 | **62 %** |
| cereales_hiver | 17 003 | 16 178 (62,7 %) | 10 144 | **60 %** |
| autres | 12 364 | 8 184 (49,5 %) | 4 051 | **33 %** |
| prairie | 23 662 | 16 251 (35,1 %) | 5 704 | **24 %** |

Calculé avec les fenêtres avant le dernier ajustement de `jmin`/`jmax` sur `cereales_hiver`/`lin`/`legumes_fleurs` (cf. limites ci-dessous) - l'ordre de grandeur ne devrait pas changer significativement, à revalider si besoin. Les 6 classes de grande culture avec fenêtre calibrée tombent entre 60 % et 75 % : le produit de deux filtres corrects pris isolément (fiabilité 81-95 %, LOS proche de la médiane 63-91 % parmi les fiables) donne mécaniquement un taux bout-en-bout plus bas - pas un signe de défaut caché, un rappel que les deux métriques ne s'additionnent pas. `autres` et `prairie` s'effondrent (33 %, 24 %) car aucune fenêtre calendaire ne leur est appliquée (couvert pérenne / classe hétérogène) : la notion de "LOS typique" y est mal posée par construction, pas un échec de méthode - ces deux tables restent peu exploitables pour un usage phénologique fin, à documenter clairement pour tout consommateur en aval de `derived.phenologie`.

---

### S5 - Service *(terminé)*

**Objectif** : exposer les résultats de S1-S4 (RPG, classification, divergence, phénologie), déjà persistés en PostGIS, via une API FastAPI et une carte web interactive. Aucun nouveau calcul scientifique dans ce sprint - uniquement de la restitution.

#### Contrat de données

Cinq tables `derived` alimentent la réponse :

| Table | Contenu | Grain |
|---|---|---|
| `derived.rpg_parcelles_aoi` | géométrie, `id_parcel`, `code_cultu` déclaré | parcelle |
| `derived.parcelles_classification` (nb04, 4.4) | `classe_predite`, `classe_declaree`, `proba_max` (confiance du RF), `split`, `model_version` | parcelle |
| `derived.divergence` (nb05, 5.4) | `classe_declaree`, `dist_classe`, `seuil_div`, `divergent` (bool), `dist_raccord`, `zone_raccord_orbital`, `version_pipeline` | parcelle |
| `derived.phenologie` (nb05, 5.4) | `classe_declaree`, `sos_date`, `pos_date`, `eos_date`, `los_jours`, flags `sos_en_bord`/`pos_en_bord`/`eos_en_bord`, `fiable`, `lambda_whittaker`, `version_pipeline` | parcelle |
| `derived.s2_parcelles_monthly` (nb03, 3.4) | `mois` (texte `'YYYY-MM'`), `variable`, `mean`, `std`, `p10`, `p90` | parcelle × mois × variable |

> Tables confirmées : `derived.parcelles_classification` (nb04, 4.4),
> `derived.divergence` et `derived.phenologie` (nb05, 5.4), clé primaire `id_parcel`,
> upsert `ON CONFLICT DO UPDATE`. Les trois tables portent chacune leur propre colonne
> `classe_declaree` (redondante mais issue de la même source RPG) - l'API peut s'appuyer
> sur celle de `derived.parcelles_classification` comme référence, et vérifier la
> cohérence entre tables comme garde-fou (cf. section 2 du notebook API).
>
> **Casse de `variable` (`derived.s2_parcelles_monthly`), vérifiée en base (nb API,
> §6.4bis)** : valeurs stockées en **majuscules** - `NDVI`, `EVI`, `NDWI`, `NDRE` pour
> les indices, `B02`/`B04`/`B05`/`B06`/`B07`/`B08`/`B11` pour les bandes. Point à ne
> pas confondre avec les noms de champs `ParcelleProfil` (`ndvi`, `evi`, `ndwi`, `ndre`),
> choisis en minuscules côté API sans lien avec la casse DB.
>
> **Mapping colonne DB → champ API** (noms API choisis plus explicites pour un
> consommateur externe qui ne connaît pas le schéma interne) :
>
> | Colonne DB | Champ API |
> |---|---|
> | `proba_max` (classification) | `proba_classe` |
> | `dist_classe` (divergence) | `score_divergence` |
> | `divergent` (divergence) | `divergente` |
> | `sos_date` / `pos_date` / `eos_date` / `los_jours` (phenologie) | `sos` / `pos` / `eos` / `los_jours` |
> | `fiable` (phenologie) | `phenologie_fiable` |
> | `variable = 'NDVI'/'EVI'/'NDWI'/'NDRE'`, `mean` (s2_parcelles_monthly) | `ndvi`/`evi`/`ndwi`/`ndre` (listes) |

**Schémas Pydantic proposés** :

```python
class ParcelleDetail(BaseModel):
    id_parcel: str
    code_cultu_declare: str
    classe_declaree: str
    classe_predite: str
    proba_classe: float
    score_divergence: float
    divergente: bool
    zone_raccord_orbital: bool
    sos: date | None
    pos: date | None
    eos: date | None
    los_jours: int | None
    phenologie_fiable: bool

class ParcelleProfil(BaseModel):
    id_parcel: str
    dates: list[date]        # 16 pas mensuels, sept N → déc N+1
    ndvi: list[float | None]
    evi: list[float | None]
    ndwi: list[float | None]
    ndre: list[float | None]
```

**Mois manquants - absence de ligne, pas `NULL` (vérifié, nb API §6.4)** : pour un mois sous
le seuil de complétude, `derived.s2_parcelles_monthly` ne contient **aucune ligne** pour
la combinaison `id_parcel × mois × variable` - la valeur n'est pas stockée à `NULL`, elle
est structurellement absente (test réel : 52 lignes sur 64 attendues pour une parcelle,
13 mois distincts sur 16). L'API doit donc reconstruire le calendrier de référence des
16 mois (`sept N` → `déc N+1`) côté requête (`generate_series` + `LEFT JOIN`) ou côté code
Python, et combler les mois absents par `None` dans les listes `ParcelleProfil` - un simple
`SELECT` sans ce calendrier de référence produirait des listes plus courtes que 16 éléments,
désynchronisées avec `dates`.

#### Endpoints envisagés

| Endpoint | Rôle |
|---|---|
| `GET /parcelles/{id_parcel}` | fiche complète → `ParcelleDetail` |
| `GET /parcelles/{id_parcel}/profil` | série temporelle → `ParcelleProfil`, pour le graphique au clic |
| `GET /parcelles?bbox=xmin,ymin,xmax,ymax&limit=` | liste filtrée spatialement, propriétés allégées (`id_parcel`, `classe_predite`, `divergente`) pour colorer la carte - sans `offset` (cf. décision pagination) |
| `GET /health` | vérification liveness (utile si déploiement conteneurisé en S6) |

#### Choix techniques à trancher

| Décision | Options | Recommandation |
|---|---|---|
| Driver PostGIS côté API | `psycopg2` (sync) vs `asyncpg` (async) | `asyncpg` + endpoints `async def` - cohérent avec FastAPI, évite de bloquer le event loop sur des requêtes spatiales ; contourne aussi l'incompatibilité déjà notée `psycopg2`/SQLAlchemy/`pd.read_sql` (non pertinente ici, l'API ne passe pas par pandas) |
| Service géométries pour la carte | GeoJSON simplifié par bbox (`ST_Intersects` + `ST_SimplifyPreserveTopology`) vs tuiles vectorielles (`ST_AsMVT`) | GeoJSON simplifié en première itération, `tolerance = 5 m` (mesuré : médiane 18 → 7 sommets/parcelle, cf. diagnostic ci-dessous) ; `ST_AsMVT` en perspective si la carte devient publique à fort trafic - 80 700 parcelles en GeoJSON non simplifié serait trop lourd pour un rendu fluide |
| CRS de sortie API | EPSG:2154 (natif PostGIS) vs EPSG:4326 (attendu par MapLibre/GeoJSON) | EPSG:4326 en sortie (`ST_Transform` + `ST_AsGeoJSON` côté SQL), cohérent avec la convention GeoJSON/RFC 7946 |
| Carte web | Leaflet vs MapLibre GL JS | MapLibre - rendu vectoriel plus performant si migration ultérieure vers `ST_AsMVT`, pas de rupture d'outil entre première itération et perspective tuiles |
| Pagination `/parcelles?bbox=` | offset/limit vs curseur vs `limit` seul | **`limit` seul, sans `offset`** (décidé après implémentation, §6.8) - l'usage identifié est une carte web interactive, où réduire le bbox (zoom/déplacement) est le geste naturel pour voir plus de parcelles, pas un défilement "page suivante" sur la même zone. `offset` reporté : à ajouter seulement si un usage d'export/script en masse apparaît (parcourir systématiquement un bbox large page par page), non identifié à ce jour |

**Précision `ST_Intersects` / `ST_SimplifyPreserveTopology` (endpoint `GET /parcelles?bbox=`)** :

- `ST_Intersects(geom_parcelle, bbox_geom)` - filtre entre la géométrie de chaque parcelle et le rectangle `bbox` reçu en paramètre, converti côté SQL en géométrie via `ST_MakeEnvelope(xmin, ymin, xmax, ymax, 4326)`. C'est l'étape de sélection spatiale, indépendante de la simplification.
- `ST_SimplifyPreserveTopology(geom_parcelle, tolerance)` - s'applique ensuite à la géométrie de *chaque parcelle sélectionnée*, indépendamment des autres : réduit son nombre de sommets tout en garantissant l'absence d'auto-intersection introduite par la simplification (contrairement à `ST_Simplify` seul, qui peut créer des géométries invalides). Ce n'est **pas** intrinsèquement lié au zoom : la `tolerance` est un paramètre fixe qu'on choisit ; sans indication de zoom envoyée par le client, une tolérance unique doit être choisie a priori (compromis fidélité/poids). Une variante plus fine consisterait à faire varier `tolerance` selon la taille du `bbox` reçu (bbox large → zoom faible → tolérance plus grossière), mais cela reste une approximation du zoom réel côté carte, pas une correspondance garantie comme dans `ST_AsMVT` (où le zoom est un paramètre explicite de la requête de tuile).

**Diagnostic mesuré sur `derived.rpg_parcelles_aoi`** (nombre de sommets par géométrie, `ST_NPoints`, médiane sur les 80 683 parcelles, EPSG:2154) :

| Géométrie | Médiane de sommets |
|---|---:|
| Brute | 18 |
| `ST_SimplifyPreserveTopology(geom, 5)` | 7 (−61 %) |
| `ST_SimplifyPreserveTopology(geom, 10)` | 6 (−67 %) |

**Décision** : `tolerance = 5` (mètres, EPSG:2154) - le gain marginal entre 5 m et 10 m est faible (1 sommet médian de moins), alors que doubler la tolérance de déformation risque de décoller visiblement les limites des petites parcelles. 5 m capte l'essentiel du gain de poids sans sacrifice de fidélité supplémentaire.

#### Portage vers `src/api/` (endpoints `/parcelles/{id}` et `/parcelles/{id}/profil`)

Code validé en notebook (`06_api.ipynb` §6.1-6.4) porté vers 4 modules, séparés par responsabilité :

| Module | Rôle |
|---|---|
| `src/api/__init__.py` | fichier vide, marque `src/api` comme package Python |
| `src/api/db.py` | pool de connexions `asyncpg`, chargement `.env`, détection `.projectroot` |
| `src/api/schemas.py` | modèles Pydantic `ParcelleDetail`, `ParcelleProfil` |
| `src/api/queries.py` | requêtes SQL + assemblage ligne DB → modèle Pydantic |
| `src/api/main.py` | application FastAPI, routes, cycle de vie (`lifespan`) |

Écarts assumés par rapport au notebook, documentés dans le code (docstrings/commentaires) plutôt que silencieux :

- **Pool de connexions plutôt que connexion par requête** : le notebook ouvre/ferme une connexion `asyncpg` à chaque cellule (adapté à l'exploration) ; l'API crée un pool une fois au démarrage (`lifespan`), réutilisé par toutes les requêtes HTTP.
- **`Path(__file__)` plutôt que `Path().resolve()`** pour `find_project_root` : un notebook a pour répertoire de travail celui d'où Jupyter est lancé (souvent déjà proche de la racine projet) ; un module importé par `uvicorn` n'a pas cette garantie - `__file__` est stable quel que soit le point de lancement.
- **Requête fiche parcelle simplifiée** : ne récupère plus qu'une seule `classe_declaree` (celle de `parcelles_classification`, retenue comme référence), sans revérifier la cohérence entre les 3 tables à chaque appel - ce garde-fou a déjà tourné une fois sur les 77 932 parcelles (§6.2, 0 incohérence), le revérifier par requête HTTP ajouterait un coût sans bénéfice supplémentaire.

**Endpoint `GET /parcelles?bbox=`** (le plus complexe du contrat) porté à son tour, après prototypage complet en notebook (§6.5-6.8) : `ST_Intersects` + `ST_SimplifyPreserveTopology(5)` + `ST_Transform` vers EPSG:4326, garde-fous `BBOX_MAX_AREA_KM2=50`/`limit≤2000` (§6.5, mesurés), détection de troncature sans `COUNT` systématique (`LIMIT limit+1`). Paramètre `bbox` reçu en chaîne `"xmin,ymin,xmax,ymax"` (convention REST courante, ex. `?bbox=0.72,49.39,0.79,49.42`), EPSG:4326, plutôt que 4 paramètres numériques séparés - plus proche de ce qu'une carte web (MapLibre `map.getBounds()`) transmet nativement.

#### Risques spécifiques à ce sprint

- **Volume** : `bbox` (le rectangle `xmin,ymin,xmax,ymax` demandé par le client, typiquement la fenêtre courante de la carte) sans limite pourrait renvoyer les 80 700 parcelles en un seul appel - les deux précautions sont cumulatives, pas alternatives :
  - **Taille de bbox maximale : `BBOX_MAX_AREA_KM2 = 50`** - requête refusée en `HTTP 400` si la surface du rectangle dépasse ce seuil, avec un corps de réponse explicite : `{"detail": "bbox trop large (surface X km², maximum 50 km²) - réduisez l'emprise géographique demandée"}`. FastAPI permet ça nativement via une `HTTPException(status_code=400, detail=...)`, le message étant repris tel quel dans la réponse JSON exposée par Swagger/OpenAPI. **Valeur mesurée** (nb API §6.5) : densité moyenne de 24,1 parcelles/km² sur l'AOI (80 689 parcelles / 3 349 km²) - 50 km² correspond à une fenêtre de carte de type "village/petite commune" (~5-7 km de côté), au-delà de laquelle un rendu agrégé serait de toute façon plus pertinent qu'une liste de parcelles individuelles.
  - **`limit` par défaut strict = 2000** : si le nombre de parcelles dans le bbox (valide) dépasse `limit`, la réponse reste `HTTP 200` mais signale explicitement la troncature plutôt que de la laisser silencieuse - champs additionnels dans la réponse : `"total_disponible": N, "retourne": limit, "tronque": true, "message": "Résultat tronqué à {limit} parcelles sur {N} - réduisez l'emprise géographique demandée."`. Le client (carte web) peut alors afficher un bandeau "zoomez pour voir toutes les parcelles" plutôt que de faire croire que la carte est complète. **Marge retenue** : à densité moyenne, 50 km² donne ~1 200 parcelles attendues ; `limit = 2000` absorbe une densité locale ~1,7× supérieure à la moyenne (zones de petites parcelles, maraîchage/lin) sans revenir à un "tout ou rien".
  - **`ORDER BY c.id_parcel` ajouté à `SQL_BBOX`** (correction, après revue) : sans tri explicite, PostgreSQL ne garantit aucun ordre stable des lignes retournées - en cas de troncature, le sous-ensemble de parcelles affiché pouvait varier d'un appel à l'autre sur la même vue exacte de la carte, sans lien avec un critère de pertinence. Le tri par `id_parcel` ne rend pas la sélection "meilleure" (ce n'est pas un tri par pertinence), seulement **reproductible** - la même requête renvoie toujours le même sous-ensemble.
- **PROJ** : si des opérations spatiales au-delà d'un simple `ST_AsGeoJSON` sont nécessaires côté API (reprojection à la volée, calculs de distance), reprendre la même précaution que dans le pipeline (WKT plutôt qu'EPSG codes) pour éviter le conflit PostgreSQL/pyproj déjà documenté.
- **Cohérence des NULL** : les parcelles `autres`/`prairie` sans fenêtre phénologique calibrée (cf. limites S4) renverront `sos`/`pos`/`eos` à `null` - à documenter explicitement dans l'OpenAPI (`description` du champ), pas seulement dans `methode.md`, pour qu'un consommateur externe de l'API ne l'interprète pas comme une donnée manquante par erreur.

#### Carte web (`web/index.html`)

MapLibre GL JS, fond de carte OpenStreetMap en raster (pas de clé API - cohérent avec l'exigence de reproductibilité, QI6 du cadrage). Fichier HTML/CSS/JS unique, sans étape de build.

- **Chargement dynamique** : `GET /parcelles?bbox=` rappelé à chaque `moveend`, avec la vue courante (`map.getBounds()`) comme bbox. La vue initiale (AOI complète, ~10 660 km²) déclenche volontairement le refus `HTTP 400` - géré comme un état normal ("zoomez pour voir les parcelles"), pas comme une erreur, puisque `BBOX_MAX_AREA_KM2 = 50` rend ce cas systématique au premier chargement.
- **Palette des classes** : couleurs liées aux cultures réelles plutôt qu'une palette catégorielle arbitraire - jaune colza (couleur de floraison), bleu-lilas lin (fleurs bleues), bordeaux betterave, etc. Contour rouge distinct (`--couleur-alerte`) pour les parcelles `divergente`, indépendant de la couleur de classe.
- **Panneau de détail** : au clic sur une parcelle, appel parallèle (`Promise.all`) à `GET /parcelles/{id}` et `GET /parcelles/{id}/profil`, affichage de la fiche complète et d'un graphique NDVI (Chart.js) avec les mois manquants représentés comme des trous (`spanGaps: false`), cohérent avec la sémantique "absence de ligne, pas `NULL`" documentée plus haut.
- **CORS** : `CORSMiddleware` ajouté à `main.py`, permissif (`allow_origins=["*"]`) pour le développement local uniquement - à restreindre avant tout déploiement public (cf. Hors périmètre ci-dessous).

#### Hors périmètre S5 *(statut : cf. S6 et perspectives)*

Déploiement (Docker) repris en S6 (cf. section Déploiement ci-dessous). **Hors périmètre S6 également, reportés en perspectives post-projet** faute d'objectif de mise en production publique à ce stade : authentification/rate-limiting, cache de requêtes, tuiles vectorielles `ST_AsMVT`, restriction CORS à l'origine réelle de déploiement - ces quatre points ne prennent sens que si la carte devient un service public à fort trafic, ce qui n'est pas l'objectif affiché du projet (démonstration reproductible en local/portfolio).

---

### S6 - Industrialisation *(prévu)*

#### Orchestration

**Orchestrateur retenu : Apache Airflow** (Prefect écarté pour ce projet, conservé en option pour un projet ultérieur plus réactif - cf. justification dans le tableau des décisions clés). Deux critères ont pesé plus que les autres : SeineCrops est un batch long et gourmand (le cas d'usage pour lequel Airflow est conçu, quand Prefect vise plutôt des pipelines courts/réactifs) ; et Airflow reste l'outil le plus probable dans une offre agritech/EO, secteur actuellement ciblé même si non figé à l'issue de la formation.

**Granularité du DAG : par fonction**, pas par notebook. Choix motivé par la valeur pédagogique (comprendre le fonctionnement d'Airflow au niveau fin plutôt que d'orchestrer 5 boîtes noires) et par l'adéquation aux étapes très gourmandes en temps : une tâche par fonction clé permet une relance ciblée sur échec (ex. rejouer les stats zonales sans retélécharger les bandes S2), alors qu'un découpage par notebook oblige à tout rejouer en cas d'échec en fin de chaîne. Tâches prévisionnelles (ordre indicatif) : `ingestion_rpg` → `disponibilite_s2` → `telechargement_bandes` → `calcul_indices` → `composites_mensuels` → `stats_zonales` → `qc_stats_zonales` → `nettoyage_intermediaires` → `classification` → `divergence_pheno`.

**Suppression des intermédiaires (`data/raw/s2/bands`, `data/raw/s2/indices`)** : tâche de nettoyage **séparée**, placée après une tâche de QC explicite validant les sorties finales (`stats_zonales` correctement écrites en base) - jamais fusionnée avec la tâche de traitement elle-même. Cette séparation répond directement à la leçon déjà actée plus bas dans ce document : la suppression prématurée des intermédiaires en S2 (avant détection du bug nodata) avait rendu la correction rétroactive impossible et imposé une reprise complète. Le nettoyage est conditionné à la réussite du QC, pas seulement à la réussite de la tâche précédente.

**Mémoire pendant les étapes gourmandes** : deux mesures cumulatives, à affiner en cours de sprint plutôt qu'à figer a priori —
- monitoring passif (`psutil`, log du pic mémoire en début/fin de tâche) pour observer et documenter la consommation réelle plutôt que la deviner ;
- isolation par processus séparé pour les tâches les plus lourdes (`telechargement_bandes`, `composites_mensuels`), sur le même principe que le parallélisme multi-processus déjà retenu en S2 pour contourner la contention GIL : un processus qui se termine libère toute sa mémoire, contrairement à un run mono-processus qui accumule sur toute la durée du DAG.

**Déclenchement : planifié** (cron Airflow), choisi comme objectif d'apprentissage plutôt que le déclenchement manuel. Fréquence à trancher en cours de sprint, alignée sur la réalité du pipeline plutôt que fixée arbitrairement - probablement mensuel, cohérent avec le composite mensuel médiane déjà en place (un cron plus fréquent n'apporterait rien tant que l'agrégation reste mensuelle).

#### Migration notebooks → `src/`

La logique de S1-S4, actuellement dans les notebooks (`01_ingestion_rpg.ipynb` à `05_divergence_pheno.ipynb`), est extraite vers les modules `src/acquisition/`, `src/processing/`, `src/db/`, `src/ml/`, `src/phenology/`, `src/reporting/` pour devenir déclenchable par Airflow. Décision : **extraction complète, notebooks conservés en parallèle plutôt que supprimés** - même principe que `src/api/` (S5), déjà construit selon cette logique (chaque module documente sa provenance en commentaire, ex. `# Portage des requêtes validées dans 06_api.ipynb §6.2`).

| Module | Statut | Portage depuis | Contenu |
|---|---|---|---|
| `src/db/connection.py` | ✅ fait | transverse (S1-S2), généralisation de `src/api/db.py` | `find_project_root`, `get_pg_params` (échoue explicitement si `.env` ou `PG_PASSWORD` absent), `get_connection` (psycopg2, une connexion par tâche - pendant synchrone du pool asyncpg de l'API) |
| `src/db/qa.py` | ✅ fait | `01_ingestion_rpg.ipynb` §4.1bis | `qa_validite`, `reparer_si_necessaire` - génériques à toute table PostGIS, logguées via `logging` (cf. section Logging ci-dessous) |
| `src/reporting/diagnostics.py` | ✅ fait | transverse, nouveau en S6 | `nouveau_run_diagnostic`, `sauvegarder_figure`, `ajouter_figure`, `ajouter_tableau`, `rendre_rapport_html` - artefacts de diagnostics optionnels versionnés par run (`data/diagnostics/{module}/{run_id}/`), non bloquants pour le DAG. Rapport HTML autonome (CSS inline, images en référence relative - le dossier de run doit être déplacé/zippé en bloc pour être partagé, pas le HTML seul) |
| `src/acquisition/rpg.py` | ✅ fait | `01_ingestion_rpg.ipynb` §2-§5 | `sha256sum`, `detecter_archive`, `decompresser_archive`, `localiser_gpkg`, `reconnaitre_gpkg` + `generer_diagnostics_reconnaissance` (alimentent `RECON.json` et le rapport de diagnostics depuis le même calcul), `recuperer_referentiel_cultures`, `charger_rpg_vers_raw`, `qa_raw_avant_filtre`, `charger_aoi_vers_raw`, `filtrer_aoi`, `indexer_rpg_aoi`, `valider_ingestion` (porte QC, lève `AssertionError`), `ecrire_rapport_cloture`. **Non porté** (reste notebook-only, cf. note ci-dessous) : §1.1 (vérification WFS du millésime), §1 (`SOURCE.json`), §3.1/3.2 (connexion/`DB.json`, déjà couverts par `src/db/`) |
| `src/acquisition/cdse.py` | ✅ fait | `02_disponibilite_s2.ipynb` | `get_cdse_token`, `refresh_cdse_token`, `query_s2_catalogue`, `interroger_catalogue_complet`, `structurer_catalogue`, `dedupliquer_catalogue`, `calculer_disponibilite_mensuelle`, `generer_diagnostics_disponibilite` (via `src/reporting/diagnostics.py`), `ecrire_rapport_disponibilite`, `sauvegarder_catalogue` |
| `src/config.py` | ✅ fait | transverse, nouveau en S6 | Source de vérité unique pour les paramètres de campagne (`MILLESIME`, `REGION_CODE`, fenêtre CDSE dérivée, chemins) - retirés des constantes globales des notebooks en faveur de paramètres explicites dans `rpg.py`/`cdse.py`. Migrable vers des Variables Airflow sans réécriture une fois le DAG monté |
| `scripts/run_ingestion.py` | ✅ fait | nouveau en S6 (pas un portage) | Orchestration manuelle de `rpg.py`/`cdse.py` dans l'ordre (`run_rpg()`, `run_cdse()`), point d'entrée temporaire en attendant le DAG Airflow - brouillon de la future séquence de tâches |

**Non porté depuis `01_ingestion_rpg.ipynb`** : la vérification de disponibilité du millésime par flux WFS (§1.1), la construction de `SOURCE.json` (§1), et la vérification de connexion PostGIS/`DB.json` (§3.1-3.2) restent notebook-only - diagnostics exploratoires ponctuels (exécutés une fois par millésime, pas à chaque run du pipeline), tranché explicitement plutôt que laissé en flou implicite. À reconsidérer seulement si le pipeline devait un jour re-vérifier le millésime à chaque exécution Airflow.

**`PSQL_BIN`** : `_executer_psql` (dans `rpg.py`) lit `PSQL_BIN` depuis `.env`, avec défaut `"psql"` (suppose le binaire sur le PATH) - plus portable que le chemin Windows en dur du notebook. Sur le poste de développement, `psql` n'est pas sur le PATH (confirmé via `which psql`/`command -v psql`) : `PSQL_BIN=C:\Program Files\PostgreSQL\18\bin\psql.exe` doit être ajouté explicitement à `.env` (pas besoin de guillemets, `python-dotenv` lit la valeur littéralement, l'espace dans `Program Files` ne pose pas de problème puisque le chemin est passé comme un seul élément de liste à `subprocess.run`, sans `shell=True`).

**`RECON.json` simplifié** : `reconnaitre_gpkg` ne reprend pas tous les champs du notebook (`chemin_relatif`, `taille_go`, `attributs_attendus_v3`, `note_cat_cult_p`, le bloc `codes_cultures`, `autres_bases_livraison`) - certains de ces éléments existent déjà ailleurs (`INGESTION_REPORT.json` a son propre bloc `referentiel_cultures`), les autres sont jugés non essentiels au diagnostic. Décision assumée, actée explicitement plutôt que découverte incidemment dans un diff.

#### Deux bugs trouvés à la première exécution réelle

`scripts/run_ingestion.py` a permis de tester `rpg.py`/`cdse.py` en conditions réelles pour la première fois (base PostGIS et identifiants CDSE réels) - deux bugs logiques ont été détectés, aucun n'était visible par `py_compile` (erreurs de logique, pas de syntaxe) :

1. **Confusion nom de fichier / nom de couche** (`localiser_gpkg`, `decompresser_archive`) : un seul paramètre (`couche_cible`) était réutilisé pour deux chaînes différentes - le nom de fichier sur disque (`RPG_Parcelles.gpkg`) et le nom de couche interne au GeoPackage (`RPG_Parcelles`, sans extension). La décompression réussissait, mais la vérification post-décompression cherchait le mauvais nom et levait `FileNotFoundError`. Corrigé en renommant le paramètre en `nom_fichier_gpkg`, distinct de `couche_cible` (qui reste utilisé pour la lecture de couche dans `reconnaitre_gpkg`/`charger_rpg_vers_raw`).
2. **`surface_totale_ha` confondait deux grandeurs** : le rapport de clôture recevait l'aire du polygone AOI lui-même (`charger_aoi_vers_raw`, ~5924.6 km²) au lieu de la somme de `surf_parc` sur les parcelles filtrées (~3349.4 km², la valeur de référence du projet) - deux grandeurs légitimement différentes (le polygone AOI inclut les terres non agricoles à l'intérieur du périmètre). Détecté par comparaison avec la valeur de référence connue du projet, pas par un test automatisé. Corrigé par l'ajout de `calculer_surface_totale_aoi()` (portage de §5 cellule 53, qui manquait à l'inventaire initial) ; les deux grandeurs sont maintenant distinctes dans `INGESTION_REPORT.json` (`surface_totale_ha` et `surface_aoi_polygone_km2`).

**Leçon pour la section Tests** : les deux bugs partagent un point commun - aucun n'était détectable sans exécution réelle contre des données/identifiants réels, et le second n'a été repéré qu'en comparant à une valeur de référence connue par ailleurs (mémoire du projet), pas par une assertion automatisée. Argument concret en faveur des tests de non-régression avec valeurs de référence fixées en fixture (cf. §Tests), pas seulement des tests structurels (schéma, types).
| `src/processing/grid.py` | ✅ fait | `03_series_s2.ipynb` §3.2 ter | `calculer_grille_aoi` (dict explicite : width, height, transform, crs_wkt - pas de globales de module), `reproject_to_aoi` |
| `src/processing/scl.py` | ✅ fait | §3.1 | `get_tile_crs`, `get_granule_id` (réutilisée par `bands.py`), `compute_f_valid_aoi`, `process_scene_scl`, `calculer_f_valid_aoi` (boucle, gère le rafraîchissement de token), `generer_diagnostics_f_valid_aoi`. `get_cdse_token`/`refresh_cdse_token` réutilisées depuis `src.acquisition.cdse`, pas redéfinies |
| `src/processing/bands.py` | ✅ fait | §3.2 | ⚠️ `resample_to_20m` - 2 correctifs historiques critiques (voir note dédiée ci-dessous), à ne jamais modifier sans relire sa documentation. `download_band`, `compute_indices`, `save_geotiff`, `process_scene_bands` (fusion téléchargement+indices, cf. décision DAG), `traiter_bandes_indices` (boucle, gère le rafraîchissement de token) |
| `src/processing/qc.py` | ✅ fait | §3.2 bis, §3.2 quater, §3.4 bis, §3.6 bis | `verifier_completude_fichiers` + diagnostics, `supprimer_jp2` (destructif, appelable uniquement par la future tâche `nettoyage_intermediaires`), `calculer_couverture_temporelle` + diagnostics (histogramme), `verifier_coherence_stats_mensuelles` + diagnostics, `verifier_coherence_ndvi_dates` (pas de diagnostics HTML - résultat déjà scalaire) |
| `src/processing/composites.py` | ✅ fait | §3.3 | `compute_monthly_composite`, `construire_composites_mensuels` (boucle mois × variable) |
| `src/processing/zonal.py` | ✅ fait | §3.4, §3.5, §3.6 | `creer_tables_zonales`, `charger_grille_labels` (retourne aussi `gdf_parcelles`, écart du portage strict - nécessaire à `diagnostiquer_parcelles_non_rasterisees`), `diagnostiquer_parcelles_non_rasterisees`, 3× (`zonal_*_from_labels` + `charger_*_vers_postgis`) pour `s2_parcelles_monthly`/`_completude`/`_ndvi_dates`. Connexion PostGIS passée en paramètre (pas `get_connection()` par appel), fermée explicitement (`conn.close()`, cf. note dédiée) |
| `scripts/run_processing.py` | ✅ fait | nouveau en S6 (pas un portage) | Orchestration manuelle des 6 modules `src/processing/` dans l'ordre, flags `--skip-*` par phase - mêmes réserves que `run_ingestion.py` (pas de reprise fine sur échec). Docstring documente explicitement les contraintes durée/mémoire pour les futurs tests (cf. §Tests) |
| `src/ml/features.py` | ✅ fait | `04_classification.ipynb` §4.1 | `charger_feature_set_long`, `pivoter_features` (704 features par défaut, plus figé en dur - vérification optionnelle via `n_features_attendu`), `charger_et_regrouper_classes` (`GROUP_MAP`, constante locale au module - décision de modélisation, pas un paramètre de campagne), `joindre_classes`, `diagnostiquer_nan` |
| `src/ml/imputation.py` | ✅ fait | §4.1bis | ⚠️ ordonnancement des fonctions **corrigé** par rapport à l'ordre d'affichage des cellules du notebook (voir note dédiée ci-dessous). `charger_completude`, `calculer_qc_action`, `construire_tier_wide`, `diagnostiquer_distance_ancrage`, `corriger_tier_ancrage_eloigne` (étendue - voir note), `appliquer_interpolation` |
| `src/ml/split.py` | ✅ fait | §4.2 | `charger_centroides`, `split_spatial_par_blocs`, `joindre_split`, `verifier_representation_classes`. `BLOCK_SIZE`/`TEST_RATIO`/`SEED` constantes locales (hyperparamètres de modélisation, pas de campagne) |
| `src/ml/train.py` | ✅ fait | §4.3 | `construire_matrices`, `entrainer_rf_baseline`, `evaluer_modele` (généralisée - fusionne les cellules 19/21 du notebook, dupliquées à l'identique pour baseline et modèle tuné), `rechercher_hyperparametres` (`RandomizedSearchCV`, `cv=3` - limitation déjà documentée plus bas dans ce document, non corrigée), `top_features_importance`, `generer_diagnostics_modele` (nouveau, voir note) |
| `src/ml/predict.py` | ✅ fait | §4.4 | `creer_table_classification`, `predire_toutes_parcelles` (toutes les parcelles, train+test), `upsert_predictions` (`ON CONFLICT DO UPDATE`, cohérent avec la décision déjà actée), `verifier_predictions` |
| `scripts/run_ml.py` | ✅ fait | nouveau en S6 (pas un portage) | Orchestration manuelle des 5 modules `src/ml/`, flag `--skip-search` (baseline seule, sans `RandomizedSearchCV`, pour un run rapide). Pas de téléchargement réseau contrairement à `run_processing.py` - plus rapide, mais toujours pas d'exécution CI directe (`RandomizedSearchCV` reste coûteux en CPU) |
| `src/phenology/divergence.py` | ✅ fait | `05_divergence_pheno.ipynb` §5.2 | `standardiser_features`, `calculer_profils_medians`, `calculer_distance_rms`, `calculer_seuils_divergence` (`k` généralisé, défaut 2.0), `generer_diagnostics_divergence_distribution`, `generer_diagnostics_divergence_spatiale` (réutilise `src.ml.split.charger_centroides`), `calculer_flag_raccord_orbital` (obligatoire mais skippable, cf. note dédiée), `generer_diagnostics_synthese`. Constantes `SEUIL_RACCORD`/`ORBITES_RACCORD`/`TUILE_RACCORD` locales (caractéristiques géométriques empiriques de cette campagne) |
| `src/phenology/whittaker.py` | ✅ fait | §5.3 (lissage) | `charger_ndvi_profils`, `construire_grille_et_binning`, `lisser_whittaker` (système bandé pentadiagonal). `LAMBDA_WHITTAKER`/`N_MIN_OBS` locaux |
| `src/phenology/phenology.py` | ✅ fait | §5.3 (extraction) | `extraire_phenologie` (par parcelle), `extraire_phenologie_toutes_parcelles` (prend `id_parcels`/`classes` en tableaux explicites, pas un DataFrame - cf. note dédiée), `generer_diagnostics_phenologie`. `FENETRES_PHENOLOGIE` locale (calendrier Normandie approximatif, à affiner) |
| `src/phenology/persist.py` | ✅ fait | §5.4 | `creer_tables_phenologie`, `to_native`, `upsert_divergence`, `upsert_phenologie` (`ON CONFLICT DO UPDATE`), `verifier_chargement` (`n_attendu` généralisé, comme `n_features_attendu` en `src/ml/`) |
| `scripts/run_phenology.py` | ✅ fait | nouveau en S6 (pas un portage) | Orchestration §5.1 (réutilise `src.ml.features`, aucune logique propre) → §5.2 → §5.3 → §5.4. Implémente le pattern "obligatoire skippable" pour `calculer_flag_raccord_orbital` (`try/except`, avertissement explicite, continue sans le flag plutôt que d'échouer) |

| `src/phenology/` | à faire | `05_divergence_pheno.ipynb` | distance RMS standardisée, lissage Whittaker, marqueurs SOS/POS/EOS |

**Écart assumé notebook → module (`qa.py`)** : dans le notebook, `qa_validite`/`reparer_si_necessaire` imprimaient leur résultat directement ; en module, elles ne font qu'exécuter, logger (niveau `INFO`/`WARNING`) et retourner une valeur - l'affichage devient la responsabilité de l'appelant le cas échéant. Principe appliqué à toutes les fonctions portées : une fonction `src/` retourne des données structurées, elle n'impose pas de format d'affichage.

**Reconnaissance (section 2 de `01_ingestion_rpg.ipynb`)** : contrairement au choix initial d'exclusion (diagnostic jugé trop ponctuel pour justifier un portage), elle est finalement portée en fonction (`reconnaitre_gpkg`) plutôt que laissée notebook-only - elle alimente à la fois `RECON.json` (déjà prévu) et le rapport HTML de diagnostics (nouveau), sans dupliquer le calcul entre les deux sorties.

**Règle de non-réouverture** : un notebook de sprint mergé n'est plus modifié pour corriger sa logique - toute évolution se fait dans `src/`. Le notebook devient un instantané figé de la démarche au moment du sprint (valeur pédagogique/portfolio, traçabilité des décisions) ; `src/` devient l'unique source de vérité exécutable. Cette règle élimine le risque usuel de la duplication (deux copies d'une même logique qui divergent au fil des corrections) : les notebooks ne bougeant plus, il n'y a rien à resynchroniser.

#### Logging

Le module `logging` standard est utilisé dans `src/`, pas d'infrastructure de log dédiée : Airflow capture nativement stdout et tout appel `logging` par tâche (horodaté, consultable dans l'UI/CLI), ce qui couvre le besoin d'observabilité d'exécution sans réinventer un mécanisme de capture. Chaque fonction logue son propre résultat à la source du calcul (ex. `qa_validite` logue le nombre de géométries invalides trouvées), plutôt que de laisser cette responsabilité à l'appelant - qui pourrait l'omettre.

Deux niveaux, complémentaires et non redondants :
- **`logging`** : traçabilité d'exécution, éphémère, consultable pendant/juste après un run (Airflow, ou console en exécution notebook/manuelle).
- **Rapports JSON** (`SOURCE.json`, `RECON.json`, `INGESTION_REPORT.json`, `AVAILABILITY_REPORT.json`...) : documentent le résultat final, persistants, versionnés à côté des données - déjà en place depuis S1/S2, non remis en cause par l'ajout du logging.

#### Détails critiques `src/processing/`

**Fusion téléchargement + indices (§3.2)** : `process_scene_bands` fusionne
en une seule fonction ce que le DAG indicatif initial (plus haut dans ce
document) décrivait comme deux tâches séparées (`telechargement_bandes`,
`calcul_indices`) - décision validée explicitement : la fusion évite une
relecture disque des rasters déjà chargés en mémoire, sur plusieurs
centaines de scènes. Le DAG indicatif doit être corrigé en conséquence
quand il sera effectivement construit : une seule tâche
`traitement_bandes_indices` à la place des deux.

**Les deux correctifs historiques de `resample_to_20m`** sont préservés à
l'identique lors du portage (`src_nodata=0` pour les pixels hors fauchée ;
fallback CRS via `ref_crs_wkt` plutôt qu'un codage en dur `EPSG:32630`) —
seuls les noms de paramètres et le passage de `print` à `logger` ont changé,
jamais la logique de reprojection. Code à considérer comme critique pour
tout le pipeline : deux bugs majeurs déjà diagnostiqués y vivaient (cf.
tableau des décisions clés, entrées S2 historiques).

**`SCL_INVALIDES` - documentation corrigée** : le notebook source ne
documentait que 5 classes SCL exclues (3, 8, 9, 10, 11) dans son tableau
markdown, alors que le code en excluait bien 7 (`{1, 3, 7, 8, 9, 10, 11}`).
Confirmé : le jeu de classes du code fait autorité, c'est la documentation
du notebook qui était incomplète - corrigée dans `src/processing/scl.py`.

**Rafraîchissement de token CDSE - bug de propagation trouvé et corrigé**
dans `scl.py` et `bands.py` : `refresh_cdse_token()` retourne un nouveau
dict, il ne mute pas en place. Un rafraîchissement fait à l'intérieur de la
fonction par-scène (`process_scene_scl`/`process_scene_bands`, comme dans
le notebook - `get_cdse_token()` rappelé à chaque scène) ne se propage pas
à l'itération suivante : chaque scène retombe sur une authentification
complète après expiration du token initial, annulant le bénéfice du
partage. Corrigé en déplaçant le rafraîchissement (et sa réassignation)
dans la boucle appelante (`calculer_f_valid_aoi`, `traiter_bandes_indices`)
plutôt que dans la fonction par-scène - gain attendu significatif sur la
durée totale du run (une authentification complète en moins par scène,
sur plusieurs centaines de scènes).

**Suppression des `.jp2` (`qc.supprimer_jp2`)** : jamais appelée
automatiquement par `run_processing.py` - utilisable uniquement depuis la
future tâche Airflow dédiée `nettoyage_intermediaires`, après confirmation
par `verifier_completude_fichiers`. Réplique la leçon S2 déjà actée
(suppression prématurée avant QC → correction rétroactive impossible).

**Gestion de connexion PostGIS dans `zonal.py`** : connexion ouverte et
fermée explicitement (`conn = get_connection(); ... finally: conn.close()`)
plutôt que `with get_connection() as conn:` utilisé ailleurs dans `src/`
(`rpg.py`, `qa.py`) - chez `psycopg2`, le context manager d'une connexion
ne gère que la transaction (commit/rollback), **pas la fermeture**. Sans
conséquence pratique pour des appels courts (`rpg.py`/`qa.py`), mais rendu
explicite ici car la connexion vit plusieurs heures à travers plusieurs
opérations lourdes séquentielles. **Question ouverte, non tranchée** :
faut-il harmoniser `rpg.py`/`qa.py` avec une fermeture explicite par
cohérence, ou est-ce un non-enjeu pour des scripts courts qui se
terminent de toute façon peu après ?

#### Détails critiques `src/ml/`

**Anomalie d'ordre d'exécution corrigée (§4.1bis)** : dans le notebook,
les cellules 9 et 10 utilisent `tier_wide`/`MOIS_ORDER`, définis
seulement en cellule 11 - artefact d'édition non linéaire en Jupyter
(le notebook a nécessairement été exécuté dans un ordre différent de son
ordre d'affichage pour produire les résultats montrés), confirmé avant
migration, pas une erreur de logique métier. `src/ml/imputation.py`
réordonne les fonctions selon l'ordre logique réel : `calculer_qc_action`
→ `construire_tier_wide` → `diagnostiquer_distance_ancrage` →
`corriger_tier_ancrage_eloigne` → `appliquer_interpolation`.

**`corriger_tier_ancrage_eloigne` étendue** : la règle documentée plus bas
dans ce document (tableau des décisions clés) précise un repli en
`exclure` si l'ancrage d'interpolation est à plus d'un mois. Le notebook
implémentait cette condition par `dist_min > 1`, qui ne capture pas le cas
`dist_min` absent (`NaN > 1` vaut `False` en pandas) - des cellules sans
aucun ancrage valide restaient donc en `imputer`, un cas pourtant plus
défavorable que "ancré à plus d'un mois". Décision prise d'étendre la
condition à `dist_min.isna() | (dist_min > 1)`, cohérent avec l'esprit de
la règle documentée. Le log distingue les deux sous-comptes
(`n_sans_ancrage`, `n_eloigne`) pour vérifier que le volume concerné reste
marginal - à contrôler au premier run réel via `scripts/run_ml.py`.

**§4.3bis non porté** : section testant l'ajout de 3 features temporelles
(amplitude NDVI, date du maximum, pente mai→août) pour réduire la
confusion `autres`/`prairie`. Conclusion déjà documentée dans ce document
(tableau des décisions clés, apprentissage sur les features temporelles
dérivées) : ces features n'apportent pas de signal nouveau, seulement une
combinaison linéaire de colonnes déjà présentes. Expérience ponctuelle
déjà conclue négativement, pas une étape du pipeline opérationnel - le
modèle retenu reste celui de §4.3 (`rf_base`/`rf` tuné), pas `rf_aug`.
Laissée notebook-only, même logique que les autres diagnostics
exploratoires déjà exclus (`SOURCE.json`, vérification WFS du millésime).

**`generer_diagnostics_modele` (nouveau, `train.py`)** : rapport de
diagnostics HTML pour un modèle évalué (heatmap de la matrice de
confusion, tableau détaillé, rapport de classification, top features) —
pas un portage direct, ajouté pour permettre de consulter les performances
au fil des runs (comparaison baseline/tuné, suivi dans le temps), sur le
même principe que les diagnostics déjà en place pour l'acquisition et le
traitement.

**Constantes de modélisation restées locales aux modules** (`GROUP_MAP`
dans `features.py`, `BLOCK_SIZE`/`TEST_RATIO`/`SEED` dans `split.py`,
`RF_PARAMS_BASELINE`/`PARAM_DIST_SEARCH`/`SEED` dans `train.py`) : pas
ajoutées à `src/config.py`, qui reste réservé aux paramètres de campagne
(millésime, fenêtre temporelle, chemins) - ce sont des décisions de
modélisation, dont la place naturelle est avec le code qui les utilise.

#### Détails critiques `src/phenology/`

**§5.1 non porté en module propre** : le chargement/pivot du feature set
était quasi identique à `src.ml.features` (même `GROUP_MAP`, même
dédoublonnage RPG, même pivot long→wide) - réutilisé directement dans
`scripts/run_phenology.py` plutôt que dupliqué. Aucun code écrit pour
cette section dans `src/phenology/`.

**`id_parcels`/`classes` en tableaux explicites** (`phenology.py`) plutôt
qu'un DataFrame indexé : `X_smooth` (sortie de `whittaker.py`) n'a aucune
notion de nom de colonne/index, seulement un ordre de lignes - imposer un
DataFrame ambigu (`id_parcel` en index dans `src.ml.features`, en colonne
dans le notebook d'origine) aurait été une source d'erreur silencieuse
d'alignement entre les résultats et les parcelles.

**`calculer_flag_raccord_orbital` - obligatoire mais skippable** : cette
fonction fait 2 appels réseau à l'API CDSE (empreintes de scènes,
endpoint public sans authentification) pour calculer `dist_raccord`/
`zone_raccord_orbital`, qui distinguent le bruit d'échantillonnage
géométrique documenté (raccord orbital 51/94 sur la tuile 30UYV, cf.
Limites documentées) d'une vraie divergence agronomique. La fonction
elle-même ne gère pas l'échec réseau ; c'est `scripts/run_phenology.py`
qui l'entoure d'un `try/except` - en cas d'échec, le run continue avec
`dist_raccord`/`zone_raccord_orbital` à `NaN`/`False` et un avertissement
explicite, plutôt que de faire échouer tout le run pour un appel léger
(2 scènes) mais non strictement bloquant. Conséquence documentée si ce
repli est activé : risque de confondre ce bruit d'échantillonnage avec
un signal agronomique réel pour les parcelles de cette bande, tant que
le flag n'est pas recalculé.

**Diagnostic de synthèse initialement oublié** : `generer_diagnostics_synthese`
(portage de la dernière cellule du notebook, croisement divergence ×
phénologie) manquait à l'inventaire initial des modules - repéré et
ajouté à `divergence.py` avant de finaliser `scripts/run_phenology.py`,
pas après coup.

**Migration notebooks → `src/` complète** : les 5 notebooks (S1-S4) sont
maintenant intégralement portés (`src/db/`, `src/reporting/`,
`src/acquisition/`, `src/processing/`, `src/ml/`, `src/phenology/`).
Restent : le DAG Airflow lui-même, et les tests automatisés (aucun test
écrit à ce stade pour l'ensemble de `src/`).

#### Bugs trouvés lors des tests manuels (`run_ml.py`, `run_phenology.py`, `run_processing.py`)

Suite de la stratégie déjà appliquée à `run_ingestion.py` : tester chaque
script manuellement avant de construire le DAG, plutôt que de découvrir
ces bugs une fois encapsulés dans des tâches Airflow. Statut des 5
scripts à date : `run_ingestion.py` ✅, `run_ml.py` (baseline seule,
`--skip-search`) ✅, `run_ml.py` (avec `RandomizedSearchCV`) ⏳ crash à
revalider après correctif, `run_phenology.py` ✅, `run_processing.py` §3.1
✅ et §3.2 ✅ (16/16 mois complets), §3.3/§3.4-3.6 ⏳ pas encore testées.

**Backend matplotlib forcé en `Agg`** (`src/reporting/diagnostics.py`,
transverse) : le backend interactif par défaut (`TkAgg` sur Windows)
provoque des `RuntimeError: main thread is not in main loop` /
`Tcl_AsyncDelete` dès qu'un autre thread tourne en parallèle
(`RandomForestClassifier(n_jobs=-1)` notamment) - Tkinter n'est pas
thread-safe. Détecté après un crash en toute fin d'un run
`RandomizedSearchCV` de ~5h (60 fits, certaines combinaisons
`max_features=0.2` avec beaucoup d'arbres coûtant jusqu'à 16 min chacune —
durée largement sous-estimée au départ), juste avant l'écriture des
diagnostics finaux : calcul perdu, rien n'étant persisté avant cette
étape. Le pipeline ne fait jamais d'affichage interactif (uniquement
`savefig()`+`close()`), le backend `Agg` est donc strictement suffisant.

**Split spatial non reproductible malgré `SEED` fixée** (`src/ml/split.py`) :
`charger_centroides` chargeait les centroïdes sans `ORDER BY` - PostgreSQL
ne garantit l'ordre des lignes que s'il est demandé explicitement. Sans
lui, l'ordre peut varier d'une exécution à l'autre (observé concrètement
entre deux runs `run_ml.py` lancés en parallèle), ce qui casse la
reproductibilité de `split_spatial_par_blocs` : `rng.choice` pioche des
*positions* dans `df_centr["block_id"].unique()`, dont l'ordre dépend de
celui des lignes source. Corrigé par `ORDER BY id_parcel` explicite.

**Diagnostic OOB ajouté au baseline** (`src/ml/train.py`) :
`oob_score=True` sur `RF_PARAMS_BASELINE` - estimateur de généralisation
quasi gratuit (chaque arbre évalué sur les échantillons qu'il n'a pas vus
dans son tirage bootstrap), plus direct que le seul écart train/test pour
juger d'un éventuel surapprentissage. Résultat du premier run avec split
reproductible : train `0.9411`, OOB `0.8722`, test `0.8800`. L'écart
train→OOB (~7 points) signale un surapprentissage réel (mémorisation
d'exemples par les arbres profonds) ; l'écart OOB→test quasi nul (le test
fait même très légèrement mieux) suggère que la généralisation
géographique (split par blocs, zones jamais vues) n'ajoute pas de
pénalité mesurable au-delà d'un holdout non-spatial classique - plutôt
rassurant sur la robustesse géographique du modèle, l'essentiel de l'écart
train/test observé n'étant pas dû à la fuite spatiale mais à de la
capacité RF ordinaire.

**Étiquetage baseline/tuné corrigé** (`scripts/run_ml.py`) : le modèle
baseline était upserté dans `derived.parcelles_classification` sous
l'étiquette `rf_tuned` par erreur (préfixe non paramétré selon
`--skip-search`) - corrigé (`rf_base_*` vs `rf_tuned_*` selon le cas),
sans quoi une comparaison de versions dans la table serait trompeuse.

**Normalisation `id_parcel` (`src/phenology/`)** : `src.ml.features` garde
`id_parcel` en index de DataFrame, mais `divergence.py`/`phenology.py`
supposaient une colonne partout (comme le notebook d'origine). Une
première gestion défensive (`df["id_parcel"] if "id_parcel" in df.columns
else df.index`) avait été appliquée de façon incomplète - un seul appel
sur deux dans `generer_diagnostics_divergence_spatiale` - provoquant un
premier `KeyError`. Corrigé à la source plutôt que rustiné localement :
`scripts/run_phenology.py::preparer_feature_set` fait un `reset_index()`
unique juste après `joindre_classes`, garantissant `id_parcel` en colonne
pour tout le reste du pipeline ; les conditions défensives devenues
inutiles ont été retirées de `divergence.py`, remplacées par une
précondition documentée dans chaque docstring concernée.

**Colonne `jour` non propagée** (`src/phenology/whittaker.py` /
`phenology.py`) : `construire_grille_et_binning` calculait `jour` sur une
copie locale de `df_ndvi_long`, jamais répercutée sur la variable de
l'appelant - `generer_diagnostics_phenologie` recevait donc un
`df_ndvi_long` sans cette colonne, `KeyError: 'jour'`. Corrigé en
recalculant `jour` directement dans `generer_diagnostics_phenologie` à
partir de `date_min` (déjà disponible), plutôt que de dépendre d'un état
calculé ailleurs et non propagé.

**Carte de répartition spatiale des divergences - observation notée, pas
un bug** : deux lignes visibles au tracé similaire aux chevauchements de
fauchées satellites (cohérent avec le flag raccord orbital déjà en place —
13,6 % des parcelles concernées), un effet de littoral plausible, et 3
amas moins attendus, non expliqués - investigation reportée à plus tard.

**Suffixe `.SAFE` non normalisé** (`scripts/run_processing.py`) : le
champ `Name` du catalogue OData peut ou non inclure `.SAFE` selon la
version - `get_granule_id`/`download_band`/`_telecharger_scl` l'ajoutent
déjà eux-mêmes lors de la construction des URLs. Cette normalisation
existait dans le notebook source (§3.1, juste après le chargement du
parquet) mais avait été omise lors du portage initial vers
`src/processing/` - provoquait un `.SAFE.SAFE` et un 404 systématique sur
**toutes** les scènes. Invisible dans `run_ingestion.py`, qui ne descend
jamais dans l'arborescence `Nodes(...)` d'un produit (seulement les
métadonnées catalogue). Corrigé par `str.removesuffix(".SAFE")` dans
`charger_contexte()`, au même endroit logique que le notebook.

**Conflits PROJ/CRS Windows - trois sources distinctes, découvertes
successivement** : chaque correctif a révélé la couche suivante, jusqu'à
résolution complète.
1. `scl.py`/`bands.py` : `.to_crs(crs.to_epsg())` repassait par une
   résolution EPSG (déclenchant une consultation de `proj.db`) plutôt que
   d'utiliser l'objet CRS déjà disponible - remplacé par `.to_crs(crs)` direct.
2. `src/db/connection.py` : GDAL découvrait par défaut la `proj.db` de
   PostgreSQL/PostGIS (schéma trop ancien - `DATABASE.LAYOUT.VERSION.MINOR = 2`
   quand `≥ 5` est attendu) au lieu de celle du venv.
3. Une fois `PROJ_DATA` pointé vers la `proj.db` de `rasterio` (schéma
   correct cette fois), l'erreur persistait quand même - **PROJ met en
   cache son chemin de recherche à l'initialisation de la bibliothèque C**,
   déclenchée par `import rasterio` lui-même ; fixer la variable
   d'environnement *après* cet import arrivait trop tard, la valeur était
   déjà figée en interne. Corrigé en localisant `rasterio` via
   `importlib.util.find_spec` (qui ne déclenche pas l'exécution de son
   `__init__.py`, donc pas l'initialisation de GDAL/PROJ) pour fixer
   `PROJ_DATA` *avant* le véritable `import rasterio`, où qu'il ait lieu
   dans le process.

Non fatal en pratique (le run continuait malgré le bruit, GDAL retombant
sur un comportement dégradé) mais très bruyant, et une résolution
silencieusement dégradée restait un risque à écarter plutôt qu'à ignorer.
Validé : `CRS.from_epsg(32630)` se résout proprement sans erreur après
les trois correctifs. Argument concret de plus en faveur de la
conteneurisation Docker déjà prévue (`methode.md` §S6) - un environnement
figé éliminerait cette classe de fragilité par construction.

**Nettoyage des bandes/indices d'exécutions antérieures - décision
actée** : aucun mécanisme ne détecte qu'un fichier bande/indice déjà
présent sur disque a été produit par une version du code désormais
corrigée (notebook, ou `src/` avant un correctif comme le fallback CRS
UTM déjà documenté) - seule sa présence est vérifiée, jamais sa
provenance. Concrètement rencontré : des fichiers `31UCR` laissés par une
exécution notebook antérieure au correctif CRS auraient été silencieusement
réutilisés (skippés) sans le nettoyage manuel effectué avant ce run.
Deux options actées :
- **Option 1 (retenue immédiatement)** : procédure manuelle documentée —
  toute correction touchant `resample_to_20m`/`compute_indices`/le calcul
  géométrique impose de vider `data/raw/s2/bands/`/`data/raw/s2/indices/`
  avant le prochain run.
- **Option 2 (différée à la conception du DAG)** : marqueur de version
  embarqué (dans l'esprit de `VERSION_PIPELINE` déjà utilisé par
  `persist.py`), comparé avant de considérer un fichier existant comme
  valide - nécessaire une fois le déclenchement automatisé (planifié),
  qui ne pourra plus compter sur une vérification humaine après chaque
  changement de code touchant le calcul raster.

**Granularité du rafraîchissement de token CDSE - amélioration identifiée,
non corrigée** : `traiter_bandes_indices` rafraîchit le token une fois par
scène, pas avant chaque bande individuelle (7 appels réseau séquentiels
par scène) - a provoqué 3 échecs par expiration en cours de scène sur 552
(`401 Unauthorized`, toutes sur la tuile `31UCQ`, cause de cette
concentration non déterminée). Sans conséquence pratique : `qc.
verifier_completude_fichiers` les détecte (`MANQUANT` complet, aucun
fichier partiel créé grâce à l'ordre des opérations dans
`process_scene_bands`), et l'idempotence les rattrape automatiquement au
passage suivant. Amélioration à envisager avant le DAG plutôt qu'urgente
maintenant.

#### Tests - deux échelles, à ne pas confondre

**Tests automatisés (pytest, CI GitHub Actions)**, à deux échelles :
- *unitaires* : une fonction isolée, entrées synthétiques contrôlées (ex. `calcul_ndvi` sur un tableau numpy fabriqué à la main, résultat connu à l'avance, cas limites division par zéro/nodata) - rapide, sans base de données ni disque.
- *intégration* : la chaîne réelle sur un sous-ensemble de parcelles (échantillon fixe couvrant les 8 classes, à définir), base PostGIS de test - vérifie l'articulation entre modules (sortie de `processing` lisible par `ml`, schéma de table conforme à ce qu'attend `queries.py`), pas seulement la justesse de chaque fonction isolée. Périmètre volontairement réduit par rapport à l'AOI complète pour un temps de CI raisonnable. Inclut un cas de **non-régression phénologique** : une fois les fenêtres calendaires par classe calibrées (cf. QC visuelle ci-dessous), vérifie que les SOS/POS/EOS recalculés sur ce même échantillon retombent dans les enveloppes D10-D90 déjà établies (stockées en fixture), avec alerte si dérive au-delà d'une tolérance à définir - détecte qu'un changement de code a silencieusement décalé la phénologie calculée, sans constituer une échelle de test distincte de l'intégration.

**QC visuelle (notebooks, non automatisée, complémentaire)** : la génération de cartes/plots de l'AOI pour détection visuelle d'anomalie par l'utilisateur reste une pratique légitime et déjà utile (le bug fallback CRS en 31UCQ/31UCR aurait pu être repéré visuellement avant diagnostic technique) - mais ce n'est pas un test au sens CI, un pipeline automatisé ne "regarde" pas une image. De même, la confirmation des fenêtres phénologiques par classe sur présentation des enveloppes D10-D90 est un exercice de calibration visuelle déjà pratiqué en S4, à conserver comme étape exploratoire notebook - distincte du test de non-régression automatisé qui s'appuie sur cette calibration une fois figée. Pour capter formellement ce qu'un contrôle visuel détecterait, certaines observations sont converties en assertions automatisées (`ST_IsValid` par géométrie, bbox de l'AOI dans une plage attendue, nombre de parcelles cohérent avec le total connu), sans prétendre à une équivalence stricte avec l'œil humain, qui détecte de l'inattendu et pas seulement des règles prédéfinies.

**Contrainte durée/mémoire pour les futurs tests de `src/processing/`** :
`scripts/run_processing.py` télécharge potentiellement plusieurs centaines
de scènes (plusieurs Go) et traite des rasters sur l'emprise complète de
l'AOI - inadapté tel quel à une exécution en CI. Les futurs tests
automatisés de cette chaîne devront s'appuyer sur des fixtures réduites,
pas sur le script directement : une grille AOI miniature (quelques
dizaines de pixels) pour `grid.py`/`composites.py`/`zonal.py` ; 1-2 scènes
synthétiques (petits GeoTIFF fabriqués à la main, pas de téléchargement
réel) pour `bands.py`/`scl.py` ; les appels réseau CDSE systématiquement
mockés. Point soulevé explicitement pendant la migration, avant même
l'écriture des tests eux-mêmes - à ne pas perdre de vue quand la section
tests sera implémentée.

#### Déploiement

**Docker** : conteneurisation de l'API (`src/api/`) et du DAG Airflow, cohérente avec l'orchestration déjà retenue pour S6 - un `docker-compose` local (API + PostGIS + Airflow) plutôt qu'un hébergement distant, l'objectif restant la démonstration reproductible plutôt qu'un service public. `GET /health` (déjà prévu en S5) sert de liveness probe.

#### Documentation

Dictionnaire de données PostGIS (par table `raw.*`/`derived.*` : colonnes, types, contraintes, origine), schéma de la base, README mis à jour jalon par jalon plutôt qu'en bloc final, note de méthode (ce document).

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
| Modèle final = baseline `rf_base` (défaut, sans tuning ni features temporelles) | Modèle tuné (`RandomizedSearchCV`), ou feature set augmenté (amplitude/date max NDVI, pente saisonnière) | Tuning : gain F1 macro nul (+0,001) au prix d'un surapprentissage doublé (écart train/test 0,060 → 0,107), causé par une CV interne (`cv=3`) aveugle au split spatial par blocs. Features temporelles : gain F1 macro nul (0,893 → 0,893), la confusion `autres`/`prairie` se redistribue sans se réduire - indice d'un problème de label RPG plutôt que de feature manquante. Simplicité et robustesse privilégiées sur un gain marginal à nul |
| Règle exclure/imputer/conserver sur `pct_pixels_couverts` (seuil 50 %), avec repli en "exclure" si ancrage d'interpolation > 1 mois | Imputation systématique par interpolation, ou laisser Random Forest gérer les NaN nativement | La complétude spatiale (50 % de la parcelle) inspire davantage confiance que l'interpolation temporelle inter-mensuelle, la dynamique végétative n'étant pas linéaire sur plusieurs mois ; impact final négligeable (0,037 % des valeurs) |
| Extension de la règle d'ancrage : repli en "exclure" aussi si aucun ancrage valide des deux côtés (`dist_min` absent), pas seulement si ancrage > 1 mois | Garder `dist_min > 1` seul, comme dans le notebook | `NaN > 1` vaut `False` en pandas - le filtre d'origine ne capturait pas le cas "aucun ancrage", pourtant plus défavorable que "ancré à plus d'un mois". Cohérent avec l'esprit de la règle ci-dessus. Volume à vérifier au premier run réel (`n_sans_ancrage` loggé séparément) |
| Format par nature de donnée (parquet métadonnées / GeoTIFF composites / PostGIS parcelles-séries) | Format unique (tout PostGIS ou tout fichiers plats) | Parquet pour le catalogue de scènes (lecture séquentielle, pas de requête spatiale) ; GeoTIFF pour les composites raster (accès fenêtré rasterio, interopérabilité QGIS) ; PostGIS pour les données vecteur/relationnelles nécessitant jointures, requêtes spatiales et relances partielles par clé composite |
| Table `s2_parcelles_completude` séparée de `s2_parcelles_monthly` | Colonne de complétude ajoutée à `s2_parcelles_monthly` | Le masque de validité est partagé par les 11 variables d'une même scène ; une colonne dénormalisée aurait dupliqué la même valeur 11 fois par parcelle × mois |
| Idempotence par comparaison de dates de modification (raster de complétude vs sources) | Simple test d'existence du fichier de sortie | Un test d'existence seul aurait reproduit le piège déjà rencontré en 3.2 bis (fichier intermédiaire présent mais généré avant un correctif, silencieusement jamais régénéré) |
| Fallback CRS par `ref_crs_wkt` (déjà résolu via `get_tile_crs`) | Fallback codé en dur sur une zone UTM fixe | Un EPSG fixe supposait à tort que toutes les tuiles étaient en zone 30N ; correct par coïncidence pour 30UYA/30UYV, faux pour 31UCQ/31UCR (décalage d'un fuseau, 100 % nodata sur 294 scènes) |
| Migration S6 notebooks → `src/` : extraction complète, notebooks conservés en parallèle (pas supprimés), règle de non-réouverture des notebooks mergés | Notebooks seuls exécutés via `papermill` (sans extraction), ou suppression des notebooks après extraction | Papermill aurait préservé la démarche pédagogique mais mal adapté à Airflow/tests pytest, et rapproché moins de la pratique professionnelle visée ; supprimer les notebooks aurait perdu leur valeur de portfolio/traçabilité. La règle de non-réouverture élimine le risque usuel de duplication (deux copies divergentes) : les notebooks figés n'ont plus besoin d'être resynchronisés avec `src/` |
| Orchestrateur S6 : Apache Airflow | Prefect | SeineCrops est un batch long et gourmand en ressources, le cas d'usage pour lequel Airflow est conçu (Prefect vise plutôt des pipelines courts/réactifs, données passées en mémoire entre tâches) ; Airflow reste aussi l'outil le plus probable dans une offre agritech/EO, secteur actuellement ciblé. Prefect reste une option pertinente pour un projet ultérieur plus réactif (ex. réseaux d'énergie), non retenue ici faute de secteur de sortie déterminé à ce stade |
| Granularité du DAG S6 : par fonction (une tâche par étape clé) | Une tâche par notebook (5 tâches) | Permet une relance ciblée sur échec (ex. rejouer les stats zonales sans retélécharger les bandes S2) sans tout rejouer ; plus pédagogique pour la maîtrise fine d'Airflow qu'un DAG à 5 boîtes noires |
| Suppression des intermédiaires S2 (`data/raw/s2/bands`, `data/raw/s2/indices`) : tâche de nettoyage séparée, après QC explicite | Suppression intégrée à la tâche de traitement | Réplique la leçon déjà tirée en S2 : supprimer avant QC avait rendu la correction du bug nodata rétroactivement impossible et imposé une reprise complète |
| Logging S6 : module `logging` standard, capturé par Airflow, pas d'infrastructure dédiée | Log structuré maison (JSON lines, fichier propre au pipeline) | Airflow capture déjà `logging`/stdout par tâche nativement (UI/CLI, horodaté) - une infra maison ferait doublon pour un projet portfolio solo. Les métriques destinées à être requêtables/agrégées à travers les runs restent dans les rapports JSON déjà en place (`INGESTION_REPORT.json` etc.), pas dans les logs |
| `src/db/connection.py` : échec explicite si `.env` ou `PG_PASSWORD` absent, plutôt que des valeurs par défaut silencieuses | Défauts silencieux (`localhost`/`postgres`/`password=None`) laissant échouer la connexion plus tard | Un échec tardif côté psycopg2 (à la connexion) est moins explicite qu'un échec immédiat au chargement du module - même logique que la vérification `CDSE_USER`/`CDSE_PASSWORD` déjà pratiquée en S2. `PG_HOST`/`PG_USER`/`PG_DBNAME` gardent des défauts (localhost/postgres/seinecrops), jugés sans risque pour un usage local |
| `src/acquisition/rpg.py` : `PSQL_BIN` configurable via `.env`, défaut `"psql"` (PATH) | Chemin Windows en dur (`C:\Program Files\PostgreSQL\18\bin\psql.exe`), comme dans le notebook | Portabilité - le notebook est intrinsèquement lié au poste de développement, un module `src/` destiné à tourner sous Airflow ne devrait pas l'être. `psql` n'étant pas sur le PATH du poste actuel, `PSQL_BIN` doit être renseigné explicitement dans `.env` en attendant |
| Fusion `telechargement_bandes` + `calcul_indices` en une seule tâche DAG (`process_scene_bands`) | Deux tâches séparées, comme suggéré par le DAG indicatif initial | Évite une relecture disque des rasters déjà en mémoire, sur plusieurs centaines de scènes ; le DAG indicatif était une estimation avant d'avoir vu le code réel du notebook |
| Rafraîchissement du token CDSE déplacé dans la boucle appelante (`calculer_f_valid_aoi`/`traiter_bandes_indices`), pas dans la fonction par-scène | Rappeler `get_cdse_token()` à chaque scène (comportement du notebook) | `refresh_cdse_token()` retourne un nouveau dict sans muter l'ancien - un rafraîchissement local à la fonction par-scène ne se propage pas à l'itération suivante, annulant le bénéfice du partage de token sur un run de plusieurs heures |
| `zonal.py` : connexion PostGIS ouverte/fermée explicitement (`get_connection()` + `conn.close()` en `finally`) | `with get_connection() as conn:` comme le reste de `src/` | Chez `psycopg2`, le context manager d'une connexion ne gère que la transaction, pas la fermeture - sans conséquence pour des appels courts, mais rendu explicite ici car la connexion vit plusieurs heures à travers des opérations lourdes séquentielles. Question ouverte : harmoniser `rpg.py`/`qa.py` ou laisser tel quel ? |
| Backend matplotlib forcé en `Agg` dans `src/reporting/diagnostics.py` | Laisser le backend par défaut (`TkAgg`) | Tkinter n'est pas thread-safe - provoque des crashs dès qu'un autre thread tourne en parallèle (`RandomForestClassifier n_jobs=-1`). Le pipeline ne fait jamais d'affichage interactif, `Agg` est strictement suffisant |
| `id_parcel` normalisé en colonne une seule fois (`reset_index` dans `run_phenology.py`) plutôt que géré au cas par cas dans chaque fonction | Conditions défensives (`if "id_parcel" in df.columns else df.index`) dans chaque fonction de `divergence.py` | La gestion défensive locale s'est montrée incomplète en pratique (un seul appel sur deux corrigé, l'autre oublié) - normaliser une fois à la source élimine la classe d'erreur entière plutôt que de compter sur une vigilance répétée |
| Nettoyage des bandes/indices obsolètes : procédure manuelle maintenant, marqueur de version embarqué avec le DAG | Marqueur de version dès maintenant | La complexité (instrumenter chaque fonction d'écriture) n'est justifiée que par un déclenchement automatisé sans supervision humaine - pas encore le cas tant que les scripts sont lancés manuellement |
| `PROJ_DATA` fixé via `importlib.util.find_spec("rasterio")` (sans import) plutôt qu'après `import rasterio` | Fixer la variable après import, ou pointer vers la `proj.db` de `pyproj` | PROJ met en cache son chemin de recherche à l'initialisation de la bibliothèque C - une variable fixée après `import rasterio` arrive trop tard. `rasterio` et `pyproj` embarquent chacun leur propre PROJ avec des versions de schéma différentes ; c'est celle de `rasterio` qui doit faire foi (schéma le plus récent réclamé) |
| `rpg.py`/`cdse.py` : paramètres de campagne (`millesime`, `region_code`, `date_start`/`date_end`) explicites en argument, centralisés dans `src/config.py` | Constantes globales de module (comme dans les notebooks) | Une valeur de campagne figée en dur dans `src/` serait un couplage caché entre le code et une exécution donnée ; `src/config.py` reste une source de vérité unique, migrable vers des Variables Airflow sans réécriture des fonctions |
---

## Limites documentées

**Optique seule** : pas de fusion radar Sentinel-1. La couverture nuageuse normande est gérée par masquage SCL, composite temporel et indicateur `f_valid_aoi`, mais les mois d'hiver restent sous-représentés. L'ajout de Sentinel-1 (cohérence, rétrodiffusion) est une perspective naturelle.

**Résolution 20 m** : comme le 3STR, la chaîne ne distingue ni les petites parcelles (< 0,5 ha) ni les cultures en mélange. C'est une limite intrinsèque de Sentinel-2 à cette résolution.

**Vérité terrain RPG** : le RPG enregistre la culture déclarée, pas la culture réellement implantée. Les erreurs de déclaration sont traitées comme du bruit dans la classification et comme du signal dans la détection de divergence.

**Évaluation géographiquement contrainte** : l'évaluation est limitée à la Normandie. La généralisation à d'autres régions (autre RPG, autre phénologie) nécessiterait un réétalonnage.

**Bande de faible complétude et de divergence accrue au raccord orbital 51/94 (30UYV)** : deux lignes quasi rectilignes traversent le centre de l'AOI - complétude réduite (`pct_pixels_couverts` moyen ~54-70 % contre ~78 % en moyenne AOI) et taux de divergence plus élevé (nb05, 5.2), concentré sur les *bords* de la bande plutôt que diffus sur toute sa largeur (pic à 5,7-7,5 % dans les 100 premiers mètres, retour à la moyenne AOI au-delà de ~2 km). Confirmé par superposition des empreintes de scènes CDSE : la bande coïncide avec le raccord entre les orbites relatives 51 et 94 sur la tuile 30UYV (zone couverte par une seule orbite plutôt que deux). Hypothèses écartées par diagnostic : effet radiométrique (view angle/BRDF, qui varierait graduellement sur toute la fauchée, pas en pic resserré) et pixels mixtes en bord de footprint (qui se limiteraient à quelques dizaines de mètres). **Cause retenue** : la trace orbitale d'une même orbite relative dérive d'une acquisition à l'autre (étendue mesurée 1 341 m, écart-type 391 m sur 10 dates échantillonnées, orbite 51) - la position du bord "vrai" varie donc sur ~1-2 km au fil des 16 mois, ce qui élargit artificiellement la zone de transition observée dans les diagnostics agrégés. **Pas un bug de traitement** : limite physique d'acquisition, stable en position moyenne. 10 593 parcelles (13,6 % de l'AOI) sont à moins de 2 km d'un raccord orbital connu ; leur taux de divergence y est 1,4× supérieur à la moyenne AOI (4,3 % contre 3,1 %). Flag `zone_raccord_orbital` ajouté en nb05 (5.2) pour ne pas confondre ce bruit d'échantillonnage avec un signal agronomique lors de l'interprétation des parcelles divergentes.

**Discontinuités de traitement Sen2Cor entre tuiles adjacentes - phénomène
distinct de `zone_raccord_orbital`, au sein d'un même passage satellite** :
vérifié empiriquement (script ad hoc, comparaison pixel à pixel du NDVI
entre les 3 tuiles disponibles pour une même date, `20230902`, une seule
orbite ce jour-là) - `0,0 %` de valeurs strictement identiques dans les
zones de recouvrement entre tuiles (2,1 M px comparés sur la plus grande
paire), écart moyen `0,006` à `0,012`, écart max jusqu'à `0,215` (échelle
NDVI `-1` à `1`). Contre-intuitif : un point au sol donné, à un instant
donné, n'a qu'un seul angle de visée physique - la géométrie
d'acquisition ne peut donc pas expliquer, à elle seule, des valeurs
différentes selon la tuile où le pixel est classé.

**Cause confirmée par la documentation Sen2Cor** : Sen2Cor traite chaque
tuile L1C **indépendamment** ("Sen2Cor is designed to process single tile
Level-1C products") - la correction atmosphérique (estimation de
l'épaisseur optique des aérosols, de la vapeur d'eau...) est calculée
séparément pour chaque tuile, à partir du contenu de cette seule tuile.
Deux tuiles adjacentes issues du même passage satellite, partageant une
frontière physique où l'atmosphère réelle est continue, peuvent donc
recevoir des paramètres de correction atmosphérique légèrement différents
de chaque côté - produisant une réflectance de surface (L2A) différente
pour un même point au sol, non pas parce que le capteur a mesuré deux
fois, mais parce que l'inversion atmosphérique a été calculée deux fois,
indépendamment. Phénomène documenté dans la communauté Copernicus (forum
Copernicus Data Space Ecosystem, discussion sur les paramètres de
correction atmosphérique par granule), pas un artefact du pipeline
SeineCrops.

Effet secondaire mineur, contribuant aux écarts ponctuellement plus
élevés (jusqu'à `0,215`) : chaque tuile est nativement dans sa propre
projection UTM (30N vs 31N pour l'AOI), donc le rééchantillonnage
indépendant de chaque tuile vers la grille commune (Lambert-93) introduit
une erreur de rééchantillonnage supplémentaire, plus visible aux
frontières nettes de parcelles.

**Implication** : la médiane inter-tuiles (§3.3, `compute_monthly_composite`)
n'est donc pas seulement utile pour gérer les recouvrements multi-orbites
— elle corrige aussi ces discontinuités de traitement Sen2Cor au sein d'un
même passage, un problème plus large que ce qui avait été envisagé
initialement. Confirme qu'aucun raccourci de calcul (sauter la médiane
quand une seule orbite couvre l'AOI un jour donné) n'est valide.

**Performance/mémoire de `§3.3` (composites mensuels) - diagnostic et
pistes d'amélioration** : ralentissement significatif observé sur un run
réel (jusqu'à ~1h10 pour 11 composites sur un mois chargé, contre un
ordre de grandeur nettement inférieur attendu). Chronométrage détaillé
ajouté (`compute_monthly_composite`, temps par étape : reprojection,
médiane journalière, médiane mensuelle, écriture) - a montré que la
médiane journalière domine très largement le temps total (ex. `577s`
contre `40s` de reprojection pour un composite), alors qu'un simple appel
`np.nanmedian` sur 2-4 tableaux de cette taille ne devrait prendre qu'une
fraction de seconde en mémoire non contrainte.

**Cause retenue** : pression mémoire déjà documentée (`Validée` `18+ Go`
committés contre `~15 Go` de RAM physique disponible, confirmée stable —
pas une fuite progressive, vérifié par un suivi de plusieurs mesures
espacées et du nombre de handles ouverts, constant). `np.nanmedian`
implique un tri interne, un accès mémoire non séquentiel bien plus
sensible aux défauts de page qu'un parcours linéaire - sous swap, chaque
tri peut déclencher des allers-retours disque, expliquant un facteur ×50
à ×100 par rapport à un calcul en RAM pure.

**Correctif déjà appliqué** : pré-allocation du tableau `daily_stack`
plutôt qu'accumulation en liste Python + copie via `np.stack` (qui
doublait transitoirement la mémoire occupée, jusqu'à ~3 Go de pic par
variable sur les mois à 14 dates) - réduit le pic, mais ne résout pas le
plateau de consommation sur toute la durée du traitement d'une variable,
qui reste la cause principale de la lenteur.

**Actions identifiées, non implémentées** (classées par rapport effort/impact) :
- *Immédiat, sans code* : libérer de la mémoire système pendant le run
  (fermer navigateur/IDE), ne jamais faire tourner deux scripts lourds en
  parallèle (leçon déjà tirée de `run_ml.py`).
- *Structurel, à considérer avant le DAG* : traitement par blocs spatiaux
  (`rasterio.windows.Window`) plutôt que la grille AOI entière en mémoire
  simultanément - réduirait l'empreinte mémoire d'un facteur proportionnel
  au nombre de blocs, éliminant le swap plutôt que de le contourner. Vrai
  chantier d'architecture (`grid.py`, `composites.py`, potentiellement
  `zonal.py` par cohérence), pas un correctif ponctuel - pertinence à
  réévaluer selon l'environnement cible du DAG (machine dédiée, limites
  mémoire explicites en conteneur), pas seulement la machine de
  développement actuelle.
- *Délibérément écartée* : paralléliser le traitement des 11 variables.
  Intuitivement tentant (8 cœurs logiques disponibles), mais
  contre-productif ici - le goulot est la mémoire, pas le CPU ; paralléliser
  ferait coexister plusieurs `daily_stack` simultanément et aggraverait le
  problème plutôt que de le résoudre. À garder en tête pour la conception
  du DAG : la parallélisation naturelle par tâche ne s'applique pas sans
  repenser la mémoire d'abord.

**Censure de gauche du SOS pour le colza (limite structurelle de la fenêtre d'observation)** : la fenêtre d'observation (Sept N → Déc N+1) démarre après le semis du colza (fin août N) - le creux NDVI pré-semis n'est donc jamais observable, quelle que soit la borne basse choisie pour la fenêtre de recherche phénologique (nb05, 5.3). 25,9 % des parcelles colza ont un SOS détecté collé au bord gauche de la fenêtre (`sos_en_bord`), contre 0,6-5,5 % pour les autres cultures de printemps/été après calibrage de `FENETRES_PHENOLOGIE`. **Non corrigible par ajustement de fenêtre** : le bord des données précède déjà le bord de la fenêtre de recherche. Ces parcelles sont exclues des statistiques agrégées via le flag `fiable`, plutôt que de biaiser le SOS médian de la classe vers une valeur artificiellement précoce.

**Multimodalité intra-fenêtre non filtrée par pos_en_bord (limite méthodologique de 5.3)** : une minorité de parcelles (~7 % des `cereales_hiver` divergentes contre 1 % des conformes, test χ² p<0,0001) présentent un profil NDVI lissé franchement multimodal à l'intérieur même de la fenêtre calendaire autorisée (`FENETRES_PHENOLOGIE`) - l'extraction SOS/POS/EOS par seuil d'amplitude autour du maximum global détecte alors un pic secondaire plutôt que la vraie saison, produisant un LOS anormalement court. Investigué : ni un seuil physique universel (0,23 % de points NDVI < 0,05 dans tout le jeu, insuffisant pour expliquer tous les cas), ni un défaut de scène à une date donnée (NDVI médian et couverture en pixels cohérents avec les dates voisines sur les cas testés - écarté par comparaison au jour 248/2024-05-07, date de forte couverture avec 68 879 parcelles observées), ni entièrement capturé par un lissage robuste itératif (filtrage par résidu, corrige 3 cas sur 5 testés visuellement). Cause probable : creux réels mais brefs (destruction d'interculture, accident cultural) sur des parcelles où le pic secondaire résultant dépasse le pic saisonnier principal. **Non corrigé** - impact limité et concentré sur `cereales_hiver` (le taux de LOS extrême reste sous 2 % pour `mais`/`betterave`) ; solution complète (détection de multimodalité + choix du "bon" pic, ou lissage robuste généralisé) laissée pour une itération future si le besoin se confirme.

**Bimodalité du SOS pour les céréales d'hiver, liée au tallage automnal (limite biologique, non corrigée)** : la distribution du LOS des parcelles `cereales_hiver` fiables et conformes est franchement bimodale (~150-165 j et ~215-235 j), la médiane globale (180 j) tombant dans le creux entre les deux modes. Investigué et écarté : mélange blé/orge dans `GROUP_MAP` (le blé tendre seul, n=12 832, reproduit la même bimodalité que l'ensemble) ; clivage géographique Pays de Caux / Neubourg (les deux zones se superposent, même creux dans les deux). Le clivage vient entièrement du SOS (mode bas : SOS médian jour 175, n=5 371 ; mode haut : SOS médian jour 100, n=4 518), l'EOS étant quasi identique entre les deux modes (330 vs 325 j). **Cause retenue** : une céréale d'hiver a une croissance en deux temps (tallage d'automne, pause hivernale, montée de printemps) - pour une partie des parcelles, la pousse d'automne suffit à franchir le seuil d'amplitude de 20 % (SOS précoce, jour ~100) ; pour la majorité, seule la montée de printemps le franchit (SOS tardif, jour ~175). Un seuil d'amplitude unique est structurellement ambigu face à ce type de profil à deux temps. **Non corrigé** : la piste testée (seuil basé sur un percentile 10 plutôt qu'un minimum ponctuel, pour écarter l'hypothèse d'un point aberrant isolé) n'améliore le taux de "LOS proche de la médiane" que de 62,7 % à 63,8 % - négligeable, confirmant que la cause est bien la biologie de la culture, pas un artefact de méthode corrigible localement.

**"LOS du profil médian" ≠ "LOS médian des parcelles" - piège d'agrégation (règle méthodologique retenue)** : extraire SOS/POS/EOS sur une courbe composite (médiane des profils individuels d'une classe) donne un résultat non représentatif des parcelles individuelles, en particulier pour les classes bimodales ou hétérogènes - l'écart peut atteindre +120 j (`cereales_hiver` : 180 j en médiane des parcelles fiables contre 300 j sur le profil médian composite, qui mélange les deux modes de SOS en une forme lissée démarrant tôt et s'étalant tard). Écarts observés : betterave +50 j, colza +30 j, maïs +65 j, lin +15 j ; seul `legumes_fleurs` s'inverse (−30 j), cohérent avec son hétérogénéité déjà connue (cycles très différents qui se neutralisent partiellement en médiane). **Règle retenue pour toute statistique de classe en aval** : toujours agréger les métriques extraites parcelle par parcelle ("LOS médian des parcelles fiables"), jamais extraire sur un profil déjà agrégé.
