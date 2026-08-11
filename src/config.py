"""Paramètres de campagne SeineCrops — source de vérité unique.

Regroupe ce qui était en constantes globales dans `01_ingestion_rpg.ipynb`
et `02_disponibilite_s2.ipynb` (`MILLESIME`, `REGION_CODE`, `DATE_START`...),
retiré des modules `src/acquisition/` en faveur de paramètres explicites
(cf. `rpg.py`/`cdse.py`). Ce module est le point unique où la valeur de
campagne est fixée pour une exécution donnée — `scripts/run_ingestion.py`
et, plus tard, le DAG Airflow, lisent d'ici plutôt que de la redéfinir.

Pensé pour une migration ultérieure vers des Variables Airflow sans
réécriture : le DAG pourra lire `Variable.get("annee_reference", default_var=ANNEE_REFERENCE)`,
ce module restant le filet de valeurs par défaut.
"""

from __future__ import annotations

from datetime import date

from src.db.connection import PROJECT_ROOT

# ── Choix primaire : la campagne / le millésime de référence ──────────────
# C'est la PÉRIODE d'observation qui est choisie en premier (cf. cadrage) ;
# le millésime RPG et la sélection Sentinel-2 en DÉCOULENT.
ANNEE_REFERENCE = 2024  # campagne agricole = millésime RPG (vérité terrain)
MILLESIME = ANNEE_REFERENCE
REGION_CODE = "R28"  # Normandie (code région INSEE 28)
CRS_SOURCE = "EPSG:2154"  # Lambert-93 (métropole)

# ── Fenêtre d'observation dérivée (sept N-1 → déc N, avec RPG = N) ────────
FENETRE_DEBUT = date(ANNEE_REFERENCE - 1, 9, 1)
FENETRE_FIN = date(ANNEE_REFERENCE, 12, 31)

# Format attendu par le filtre OData CDSE (ContentDate/Start) — cf. cdse.py.
DATE_START = f"{ANNEE_REFERENCE - 1}-09-01T00:00:00.000Z"
DATE_END = f"{ANNEE_REFERENCE}-12-31T23:59:59.999Z"

# ── Arborescence locale, ancrée sur la racine du dépôt ─────────────────────
DATA_DIR = PROJECT_ROOT / "data"
DATA_RAW_RPG = DATA_DIR / "raw" / "rpg" / str(MILLESIME) / REGION_CODE
DATA_RAW_S2 = DATA_DIR / "raw" / "s2"
REF_DIR = DATA_RAW_RPG.parent / "_referentiels"

ARCHIVE_PATTERNS = ("*.7z", "*.zip")  # motif(s) de l'archive régionale RPG
NOM_FICHIER_GPKG = "RPG_Parcelles.gpkg"  # nom de FICHIER sur le disque (avec extension)
COUCHE_CIBLE_RPG = "RPG_Parcelles"  # nom de COUCHE dans le GeoPackage (sans extension) — ne pas confondre
AOI_GEOJSON = DATA_DIR / "vector" / "aoi" / "aoi_seinecrops.geojson"

# ── Sorties intermédiaires de src/processing/ (§3.1-3.6) ───────────────────
DATA_RAW_S2_SCL = DATA_RAW_S2 / "scl"
DATA_RAW_S2_BANDS = DATA_RAW_S2 / "bands"
DATA_RAW_S2_INDICES = DATA_RAW_S2 / "indices"
DATA_RAW_S2_COMPOSITES = DATA_RAW_S2 / "composites"
DATA_COMPLETUDE_DIR = DATA_DIR / "completude"
