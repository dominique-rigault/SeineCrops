"""Exécution manuelle de la chaîne d'acquisition (RPG + catalogue CDSE).

Appelle `src.acquisition.rpg` et `src.acquisition.cdse` dans l'ordre, avec
les paramètres de `src.config` — même séquence que celle qu'un DAG Airflow
appellera plus tard tâche par tâche (cf. `methode.md` §S6, granularité par
fonction). Ce script est un point d'entrée temporaire : il permet de tester
la migration `src/` en conditions réelles avant que le DAG lui-même existe.
Il ne remplace pas le DAG — il n'a pas de reprise sur échec tâche par tâche,
juste un `try/except` global qui arrête tout à la première erreur.

Usage :
    python -m scripts.run_ingestion
    python -m scripts.run_ingestion --skip-cdse   # RPG seul
    python -m scripts.run_ingestion --skip-rpg    # CDSE seul (nécessite un run RPG préalable)
"""

from __future__ import annotations

import argparse
import json
import logging

from src import config
from src.acquisition import cdse, rpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("run_ingestion")


def run_rpg() -> None:
    logger.info(
        "=== Ingestion RPG — millésime %s, région %s ===",
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

    rpg.ecrire_rapport_cloture(
        data_raw=config.DATA_RAW_RPG,
        project_root=config.PROJECT_ROOT,
        millesime=config.MILLESIME,
        region_code=config.REGION_CODE,
        aoi_geojson=config.AOI_GEOJSON,
        qa_raw=qa_raw,
        resultats_validation=resultats,
        surf_totale_ha=surf_km2 * 100,  # 1 km² = 100 ha
        n_codes=len(df_codes),
    )
    logger.info("=== Ingestion RPG terminée ===")


def run_cdse() -> None:
    logger.info(
        "=== Catalogue CDSE — fenêtre %s -> %s ===",
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-rpg", action="store_true", help="Sauter l'ingestion RPG"
    )
    parser.add_argument(
        "--skip-cdse", action="store_true", help="Sauter le catalogue CDSE"
    )
    args = parser.parse_args()

    if not args.skip_rpg:
        run_rpg()
    if not args.skip_cdse:
        run_cdse()


if __name__ == "__main__":
    main()
