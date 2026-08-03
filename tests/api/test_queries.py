"""Tests de src/api/queries.py.

Premier lot : logique pure, sans connexion PostGIS (cf. tests
d'intégration à venir, qui nécessiteront une fixture de connexion
asyncpg — cf. methode.md §S6 sur la stratégie de tests).
"""

from src.api.queries import MOIS_REFERENCE, BboxTropLargeError


def test_bbox_trop_large_error_message():
    erreur = BboxTropLargeError(surface_km2=120.5, max_km2=50)
    message = str(erreur)

    assert "120.5" in message
    assert "50" in message
    assert erreur.surface_km2 == 120.5
    assert erreur.max_km2 == 50


def test_mois_reference_calendrier():
    # Calendrier de référence : sept 2023 -> déc 2024 (16 mois),
    # cf. methode.md §Zone d'étude
    assert len(MOIS_REFERENCE) == 16
    assert MOIS_REFERENCE[0] == "2023-09"
    assert MOIS_REFERENCE[-1] == "2024-12"
