"""Fixtures pytest pour les tests de src/api/ nécessitant PostGIS."""

import sys
from pathlib import Path

# Autonome : ne dépend pas de l'ordre de chargement de tests/conftest.py
# (racine du projet = 2 niveaux au-dessus de ce fichier, tests/api/ -> tests/ -> racine).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg
import pytest_asyncio

from src.api.db import PG_DSN


@pytest_asyncio.fixture
async def db_conn():
    conn = await asyncpg.connect(PG_DSN)
    yield conn
    await conn.close()
