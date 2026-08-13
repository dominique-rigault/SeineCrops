"""Connexion PostGIS pour les modules d'acquisition/traitement (pipeline Airflow).

Contrairement à `src/api/db.py` (pool `asyncpg`, réutilisé par tous les
appels HTTP d'un process uvicorn long-running), les tâches du pipeline
sont des scripts courts, lancés indépendamment par Airflow — une connexion
`psycopg2` ouverte/fermée par tâche est le bon niveau, pas un pool partagé.
C'est le même choix que dans les notebooks S1/S2 (`psycopg2.connect(**PG_PARAMS)`),
porté ici pour être réutilisable sans le redéfinir dans chaque module.

Portage de `01_ingestion_rpg.ipynb` §Imports et paramètres globaux et
`02_disponibilite_s2.ipynb` §Imports (les deux notebooks définissaient
`find_project_root` séparément — fusionné ici).
"""

from __future__ import annotations

import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Windows : PostgreSQL/PostGIS installe sa propre libproj + proj.db, souvent
# découverte par erreur par GDAL/PROJ à la place d'une copie embarquée dans le
# venv — provoque des erreurs "PROJ: proj_create_from_database: ... whereas a
# number >= N is expected. It comes from another PROJ installation."
#
# Point clé, découvert après un premier essai infructueux : PROJ met en cache
# son chemin de recherche à l'initialisation de la bibliothèque C (déclenchée
# par `import rasterio` lui-même) — fixer `PROJ_DATA` APRÈS cet import arrive
# trop tard, la valeur est déjà figée en interne. Il faut localiser le dossier
# de `rasterio` SANS l'importer (`importlib.util.find_spec`, qui ne déclenche
# pas l'exécution de son `__init__.py`) pour fixer la variable d'environnement
# avant le véritable `import rasterio`, où qu'il ait lieu dans le process.
#
# On pointe vers la copie de `rasterio` (pas `pyproj`) : les deux paquets
# embarquent chacun leur propre PROJ, avec des versions de schéma différentes
# — le GDAL interne à `rasterio` réclame le schéma le plus récent, donc c'est
# sa copie qui doit faire foi pour tout le process.
_rasterio_spec = importlib.util.find_spec("rasterio")
if _rasterio_spec is not None and _rasterio_spec.origin is not None:
    _rasterio_proj_data = Path(_rasterio_spec.origin).parent / "proj_data"
    if (_rasterio_proj_data / "proj.db").exists():
        os.environ["PROJ_DATA"] = str(_rasterio_proj_data)
        os.environ["PROJ_LIB"] = str(
            _rasterio_proj_data
        )  # nom historique, gardé pour compat GDAL plus anciens


def find_project_root(marker: str = ".projectroot") -> Path:
    """Racine du projet, indépendante du répertoire d'exécution.

    `Path(__file__)` plutôt que `Path().resolve()` (utilisé dans les
    notebooks) : ce module est importé depuis des tâches Airflow dont le
    répertoire de travail n'est pas garanti être la racine du projet —
    même raisonnement que `src/api/db.py`.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / marker).exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError("Racine du projet introuvable")


PROJECT_ROOT = find_project_root()
ENV_PATH = PROJECT_ROOT / ".env"

if not ENV_PATH.exists():
    raise FileNotFoundError(
        f".env introuvable : {ENV_PATH}\n"
        "Créer ce fichier à la racine du projet (variables PG_* et CDSE_* — "
        "voir README / methode.md)."
    )

load_dotenv(ENV_PATH)


def get_pg_params() -> dict:
    """Paramètres de connexion psycopg2, lus depuis `.env`.

    Échoue explicitement si PG_PASSWORD est absent plutôt que de laisser
    passer `None` jusqu'à un échec de connexion psycopg2 peu explicite
    (même logique que la vérification CDSE_USER/CDSE_PASSWORD de
    `02_disponibilite_s2.ipynb` §2.1).
    """
    password = os.getenv("PG_PASSWORD")
    if password is None:
        raise EnvironmentError(f"PG_PASSWORD manquant dans {ENV_PATH}")

    return {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", 5432)),
        "dbname": os.getenv("PG_DBNAME", "seinecrops"),
        "user": os.getenv("PG_USER", "postgres"),
        "password": password,
    }


PG_PARAMS = get_pg_params()


def get_connection() -> psycopg2.extensions.connection:
    """Nouvelle connexion psycopg2, à utiliser en context manager :

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)

    ⚠️ Chez `psycopg2`, `with conn:` ne gère QUE la transaction
    (commit/rollback) — la connexion elle-même n'est jamais fermée par ce
    bloc. Pour un appel isolé et court, sans conséquence pratique (le
    garbage collector CPython ferme la socket via `__del__`, quasi
    immédiat par comptage de références). Préférer `connexion()`
    ci-dessous pour une fermeture garantie, notamment dans des boucles
    ou avant qu'un nombre indéterminé d'appels s'accumulent (ex. futurs
    workers Airflow exécutant plusieurs tâches dans le même process).
    """
    return psycopg2.connect(**PG_PARAMS)


@contextmanager
def connexion():
    """Connexion PostGIS avec fermeture garantie (transaction ET socket) :

        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(...)

    Contrairement à `with get_connection() as conn:`, ce context manager
    ferme explicitement la connexion en sortie de bloc (`finally`), qu'il
    y ait eu exception ou non — corrige une ambiguïté que `psycopg2` laisse
    ouverte (cf. docstring de `get_connection`). Préféré pour tout nouveau
    code ; `get_connection()` reste disponible pour un usage direct
    (ex. connexion longue-vie explicitement fermée par l'appelant, comme
    `scripts/run_processing.py::run_zonal`).
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
