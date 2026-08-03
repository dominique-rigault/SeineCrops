"""Connexion PostGIS pour l'API SeineCrops.

Contrairement aux notebooks du pipeline (une connexion `asyncpg.connect`
ouverte/fermée par requête, cf. `06_api.ipynb` §6.1), l'API utilise un
pool de connexions (`asyncpg.create_pool`), créé une fois au démarrage
et réutilisé par toutes les requêtes HTTP — évite le coût d'ouverture
d'une connexion TCP/PostgreSQL à chaque appel.
"""

import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


def find_project_root(marker: str = ".projectroot") -> Path:
    # Path(__file__) plutôt que Path().resolve() (utilisé dans les
    # notebooks) : ce module est importé depuis un process uvicorn,
    # dont le répertoire de travail n'est pas garanti être la racine
    # du projet — __file__ est stable quel que soit le point de lancement.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / marker).exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError("Racine du projet introuvable")


PROJECT_ROOT = find_project_root()
load_dotenv(PROJECT_ROOT / ".env")

PG_DSN = (
    f"postgresql://{os.getenv('PG_USER', 'postgres')}:{os.getenv('PG_PASSWORD')}"
    f"@{os.getenv('PG_HOST', 'localhost')}:{os.getenv('PG_PORT', 5432)}"
    f"/{os.getenv('PG_DBNAME', 'seinecrops')}"
)

pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global pool
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=10)


async def close_pool() -> None:
    global pool
    if pool is not None:
        await pool.close()
        pool = None
