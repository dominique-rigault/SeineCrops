"""Tests d'intégration de src/api/queries.py, connectés à PostGIS.

Nécessitent une base `derived` à jour (cf. tests/api/conftest.py pour
la fixture de connexion). Utilisent la parcelle 100032, déjà validée
manuellement en 06_api.ipynb (§6.3), comme cas de non-régression.
"""

import pytest

from src.api.queries import (
    BboxTropLargeError,
    fetch_parcelle_detail,
    fetch_parcelle_profil,
    fetch_parcelles_bbox,
)


@pytest.mark.asyncio
async def test_fetch_parcelle_detail_parcelle_connue(db_conn):
    fiche = await fetch_parcelle_detail(db_conn, "100032")

    assert fiche is not None
    assert fiche.id_parcel == "100032"
    assert fiche.code_cultu_declare == "SNE"
    assert fiche.classe_declaree == "autres"
    assert fiche.classe_predite == "cereales_hiver"


@pytest.mark.asyncio
async def test_fetch_parcelle_detail_parcelle_inconnue(db_conn):
    fiche = await fetch_parcelle_detail(db_conn, "id-inexistant")

    assert fiche is None


# --- ParcelleProfil (§6.4) --------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_parcelle_profil_mois_manquants(db_conn):
    profil = await fetch_parcelle_profil(db_conn, "100032")

    assert profil is not None
    assert len(profil.dates) == 16
    assert profil.dates[0].isoformat() == "2023-09-01"
    assert profil.dates[-1].isoformat() == "2024-12-01"

    # Déc. 2023, janv. et fév. 2024 : sous le seuil de complétude (validé §6.4)
    assert profil.ndvi[3] is None
    assert profil.ndvi[4] is None
    assert profil.ndvi[5] is None
    assert profil.ndvi[0] == pytest.approx(0.47928470373153687)


@pytest.mark.asyncio
async def test_fetch_parcelle_profil_parcelle_inconnue(db_conn):
    profil = await fetch_parcelle_profil(db_conn, "id-inexistant")

    assert profil is None


# --- bbox (§6.5-6.8) ---------------------------------------------------------
# Note : tests "instantané" liés à l'état actuel de la base — à ajuster si
# le nombre de parcelles évolue significativement (ex. après un reprocess nb03).

PETIT_BBOX = (0.72, 49.39, 0.79, 49.42)  # validé manuellement : 277 parcelles
GRAND_BBOX = (
    0.05041953058845202,
    48.94567022581,
    1.3990876398924237,
    49.924824772591904,
)  # emprise AOI


@pytest.mark.asyncio
async def test_fetch_parcelles_bbox_normal(db_conn):
    resultat = await fetch_parcelles_bbox(db_conn, PETIT_BBOX)

    assert resultat["retourne"] == 277
    assert resultat["total_disponible"] == 277
    assert resultat["tronque"] is False
    assert resultat["features"][0]["type"] == "Feature"


@pytest.mark.asyncio
async def test_fetch_parcelles_bbox_tronque(db_conn):
    resultat = await fetch_parcelles_bbox(db_conn, PETIT_BBOX, limit=10)

    assert resultat["retourne"] == 10
    assert resultat["total_disponible"] == 277
    assert resultat["tronque"] is True


@pytest.mark.asyncio
async def test_fetch_parcelles_bbox_trop_large(db_conn):
    with pytest.raises(BboxTropLargeError):
        await fetch_parcelles_bbox(db_conn, GRAND_BBOX)
