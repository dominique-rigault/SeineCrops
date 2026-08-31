"""Fixtures pour les tests de schéma (`tests/db/`).

Distinct de `tests/api/conftest.py` (fixture asyncpg, pour les tests de la
couche API) : les tests de schéma vérifient l'état de la base au niveau DDL
(colonnes, contraintes, index, plans d'exécution), un usage synchrone et
ponctuel plutôt qu'un service continu de requêtes — `psycopg2` via
`src.db.connection.connexion`, la même connexion que celle utilisée par
`rpg.py` et les scripts de migration, pas `asyncpg`.
"""

from __future__ import annotations

import pytest

from src.db.connection import connexion


@pytest.fixture
def db_connection():
    """Connexion PostgreSQL synchrone, une par test (pas de partage d'état
    entre tests — chaque test qui prépare une requête (`PREPARE`) la
    désalloue lui-même)."""
    with connexion() as conn:
        yield conn
