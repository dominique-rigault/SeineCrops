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

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


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
    """
    return psycopg2.connect(**PG_PARAMS)
