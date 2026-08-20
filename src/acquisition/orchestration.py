"""Orchestration de la chaîne d'acquisition (RPG + catalogue CDSE).

Déplacé depuis `scripts/run_ingestion.py` pour être réutilisable à la fois
par le script CLI (`scripts/run_ingestion.py`, désormais une simple
enveloppe autour de ce module) et par le DAG Airflow, évite de dupliquer
la logique d'orchestration entre les deux, qui divergeraient sinon
silencieusement au premier correctif appliqué d'un seul côté.
"""

from __future__ import annotations

import json
import logging

from src import config
from src.acquisition import cdse, rpg

logger = logging.getLogger(__name__)


def run_rpg() -> None:
    """Ingestion RPG complète : reconnaissance, chargement PostGIS, filtre AOI, validation."""
    logger.info(
        "=== Ingestion RPG, millésime %s, région %s ===",
        config.MILLESIME,
        config.REGION_CODE,
    )

    gpkg = rpg.localiser_gpkg(
        config.DATA_RAW_RPG, config.NOM_FICHIER_GPKG, config.ARCHIVE_PATTERNS
    )

    recon = rpg.reconnaitre_gpkg(gpkg, config.COUCHE_CIBLE_RPG)
    (config.DATA_RAW_RPG / "RECON.json").write_text(
        json.dumps(recon, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rapport_diag = rpg.generer_diagnostics_reconnaissance(recon)
    logger.info("Diagnostics reconnaissance : %s", rapport_diag)

    df_codes = rpg.recuperer_referentiel_cultures(config.MILLESIME, config.REF_DIR)

    rpg.charger_rpg_vers_raw(gpkg, config.COUCHE_CIBLE_RPG, config.DATA_RAW_RPG)
    qa_raw = rpg.qa_raw_avant_filtre()

    _srid_aoi, surf_km2 = rpg.charger_aoi_vers_raw(
        config.AOI_GEOJSON, config.DATA_RAW_RPG
    )
    rpg.filtrer_aoi()
    rpg.indexer_rpg_aoi()

    resultats = rpg.valider_ingestion()
    surf_totale_ha = rpg.calculer_surface_totale_aoi()

    rpg.ecrire_rapport_cloture(
        data_raw=config.DATA_RAW_RPG,
        project_root=config.PROJECT_ROOT,
        millesime=config.MILLESIME,
        region_code=config.REGION_CODE,
        aoi_geojson=config.AOI_GEOJSON,
        qa_raw=qa_raw,
        resultats_validation=resultats,
        surf_totale_ha=surf_totale_ha,
        n_codes=len(df_codes),
        surf_aoi_polygone_km2=surf_km2,
    )
    logger.info("=== Ingestion RPG terminée ===")


def run_cdse() -> None:
    """Catalogue CDSE complet : interrogation, structuration, déduplication, rapports."""
    logger.info(
        "=== Catalogue CDSE, fenêtre %s -> %s ===",
        config.DATE_START[:10],
        config.DATE_END[:10],
    )

    config.DATA_RAW_S2.mkdir(parents=True, exist_ok=True)

    token = cdse.get_cdse_token()
    raw_results = cdse.interroger_catalogue_complet(
        config.DATE_START, config.DATE_END, token
    )

    df = cdse.structurer_catalogue(raw_results)
    df_dedup = cdse.dedupliquer_catalogue(df)
    daily, daily_full_aoi, monthly = cdse.calculer_disponibilite_mensuelle(df_dedup)

    rapport_diag = cdse.generer_diagnostics_disponibilite(
        monthly, config.DATE_START, config.DATE_END
    )
    logger.info("Diagnostics disponibilité : %s", rapport_diag)

    cdse.ecrire_rapport_disponibilite(
        df=df,
        df_dedup=df_dedup,
        daily=daily,
        daily_full_aoi=daily_full_aoi,
        monthly=monthly,
        annee_reference=config.ANNEE_REFERENCE,
        date_start=config.DATE_START,
        date_end=config.DATE_END,
        data_raw=config.DATA_RAW_S2,
    )
    cdse.sauvegarder_catalogue(df_dedup, config.DATA_RAW_S2)
    logger.info("=== Catalogue CDSE terminé ===")
