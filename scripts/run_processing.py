"""Exécution manuelle de la chaîne de traitement Sentinel-2 (§3.1 → §3.6).

Même principe que `scripts/run_ingestion.py` : appelle `src.processing.*`
dans l'ordre, avec les paramètres de `src.config`, en attendant le DAG
Airflow. Point d'entrée temporaire, pas un remplacement.

⚠️ **Contraintes de durée et de mémoire, à anticiper dès maintenant pour
les futurs tests** : ce script télécharge potentiellement plusieurs
centaines de scènes (plusieurs Go), traite des rasters sur l'emprise
complète de l'AOI, et peut tourner plusieurs heures. Il n'est **pas**
adapté à une exécution en CI telle quelle. Les futurs tests unitaires /
d'intégration de cette chaîne (cf. `methode.md` §Tests) devront s'appuyer
sur des fixtures réduites plutôt que sur ce script directement :
- une grille AOI minuscule (quelques dizaines de pixels, pas la grille
  réelle) pour les fonctions de `grid.py`/`composites.py`/`zonal.py` ;
- 1-2 scènes synthétiques (petits GeoTIFF fabriqués à la main) plutôt que
  des téléchargements CDSE réels, pour `bands.py`/`scl.py` ;
- les appels réseau (CDSE) mockés, jamais exécutés en CI.

Usage :
    python -m scripts.run_processing
    python -m scripts.run_processing --skip-scl --skip-bands --skip-composites --skip-zonal
"""

from __future__ import annotations

import argparse
import logging

import geopandas as gpd
import pandas as pd

from src import config
from src.acquisition.cdse import get_cdse_token
from src.db.connection import get_connection
from src.processing import bands, composites, grid, qc, scl, zonal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("run_processing")


def charger_contexte() -> dict:
    """Charge le catalogue dédupliqué, l'AOI, et calcule la grille de référence.

    État partagé entre toutes les phases — chargé une seule fois plutôt que
    recalculé à chaque appel de fonction.
    """
    df_dedup = pd.read_parquet(config.DATA_RAW_S2 / "catalogue_dedup.parquet")
    aoi = gpd.read_file(config.AOI_GEOJSON)
    grille = grid.calculer_grille_aoi(aoi)
    logger.info(
        "Contexte chargé : %d scènes, grille %d×%d px",
        len(df_dedup),
        grille["width"],
        grille["height"],
    )
    return {"df_dedup": df_dedup, "aoi": aoi, "grille": grille}


def run_scl(ctx: dict) -> pd.DataFrame:
    logger.info("=== §3.1 — SCL et f_valid_aoi ===")
    token = get_cdse_token()
    df_dedup = scl.calculer_f_valid_aoi(
        ctx["df_dedup"], ctx["aoi"], token, config.DATA_RAW_S2_SCL
    )
    rapport = scl.generer_diagnostics_f_valid_aoi(df_dedup)
    logger.info("Diagnostics f_valid_aoi : %s", rapport)
    df_dedup.to_parquet(config.DATA_RAW_S2 / "catalogue_dedup.parquet", index=False)
    return df_dedup


def preparer_scenes_retenues(df_dedup: pd.DataFrame) -> pd.DataFrame:
    """Filtre `f_valid_aoi ≥ seuil`, ajoute les colonnes `mois`/`date8` (portage §3.2 quater, cellule 18)."""
    df_retenues = df_dedup[df_dedup["f_valid_aoi"] >= scl.F_VALID_SEUIL].copy()
    df_retenues["mois"] = (
        pd.to_datetime(df_retenues["date"]).dt.to_period("M").astype(str)
    )
    df_retenues["date8"] = pd.to_datetime(df_retenues["date"]).dt.strftime("%Y%m%d")
    return df_retenues


def run_bands(ctx: dict, df_retenues: pd.DataFrame) -> None:
    logger.info(
        "=== §3.2 — Bandes et indices (%d scènes retenues) ===", len(df_retenues)
    )
    resultat = bands.traiter_bandes_indices(
        df_retenues,
        ctx["aoi"],
        config.DATA_RAW_S2_SCL,
        config.DATA_RAW_S2_BANDS,
        config.DATA_RAW_S2_INDICES,
    )
    logger.info("Bandes/indices : %s", resultat)


def run_qc_fichiers(df_retenues: pd.DataFrame) -> list[dict]:
    logger.info("=== §3.2 bis — QC complétude fichiers ===")
    problemes = qc.verifier_completude_fichiers(
        df_retenues, config.DATA_RAW_S2_BANDS, config.DATA_RAW_S2_INDICES
    )
    rapport = qc.generer_diagnostics_completude_fichiers(problemes, len(df_retenues))
    logger.info("Diagnostics QC fichiers : %s", rapport)
    if problemes:
        logger.warning(
            "%d problème(s) détecté(s) — NE PAS lancer qc.supprimer_jp2() avant correction",
            len(problemes),
        )
    return problemes


def determiner_mois_complets(df_retenues: pd.DataFrame) -> list[str]:
    """Mois pour lesquels toutes les scènes retenues ont leurs indices produits (portage §3.2 ter, cellule 18)."""
    mois_list = sorted(df_retenues["mois"].unique())
    mois_complets = []
    for mois in mois_list:
        df_mois = df_retenues[df_retenues["mois"] == mois]
        tous_presents = all(
            (
                config.DATA_RAW_S2_INDICES / sid.split("_")[5][1:] / f"{sid}_NDVI.tif"
            ).exists()
            for sid in df_mois["scene_id"]
        )
        if tous_presents:
            mois_complets.append(mois)
    logger.info(
        "Mois complets : %d/%d — %s", len(mois_complets), len(mois_list), mois_complets
    )
    return mois_complets


def run_qc_couverture(
    ctx: dict, df_retenues: pd.DataFrame, mois_complets: list[str]
) -> pd.DataFrame:
    logger.info("=== §3.2 quater — QC couverture temporelle ===")
    df_qc = qc.calculer_couverture_temporelle(
        mois_complets,
        df_retenues,
        config.DATA_RAW_S2_INDICES,
        ctx["grille"],
        config.DATA_COMPLETUDE_DIR,
    )
    rapport = qc.generer_diagnostics_couverture_temporelle(df_qc)
    logger.info("Diagnostics couverture temporelle : %s", rapport)
    return df_qc


def run_composites(
    ctx: dict, df_retenues: pd.DataFrame, mois_complets: list[str]
) -> None:
    logger.info("=== §3.3 — Composites mensuels ===")
    resultat = composites.construire_composites_mensuels(
        mois_complets,
        df_retenues,
        ctx["grille"],
        config.DATA_RAW_S2_BANDS,
        config.DATA_RAW_S2_INDICES,
        config.DATA_RAW_S2_COMPOSITES,
    )
    logger.info("Composites : %s", resultat)


def run_zonal(ctx: dict, mois_complets: list[str]) -> None:
    """§3.4/§3.5/§3.6 — connexion unique, ouverte/fermée explicitement (pas
    `with get_connection() as conn:`, qui ne ferme pas la connexion en sortie
    de bloc chez psycopg2 — ne gère que la transaction). Justifié ici par la
    durée de vie de la connexion, partagée entre plusieurs opérations lourdes
    séquentielles, contrairement aux appels courts du reste de `src/`.
    """
    logger.info("=== §3.4/§3.5/§3.6 — Agrégation zonale ===")
    conn = get_connection()
    conn.autocommit = True
    try:
        zonal.creer_tables_zonales(conn)
        gdf_parcelles, label_grid, label_to_id = zonal.charger_grille_labels(
            conn, ctx["grille"]
        )

        diag = zonal.diagnostiquer_parcelles_non_rasterisees(gdf_parcelles, label_grid)
        logger.info(
            "Parcelles non rasterisées : %d (%.2f%%), %.1f ha",
            diag["n_absentes"],
            diag["pct_absentes"],
            diag["surface_absentes_ha"],
        )

        zonal.charger_composites_vers_postgis(
            conn,
            config.DATA_RAW_S2_COMPOSITES,
            label_grid,
            label_to_id,
            composites.VARIABLES,
        )
        resultats_stats = qc.verifier_coherence_stats_mensuelles()
        rapport_stats = qc.generer_diagnostics_stats_mensuelles(resultats_stats)
        logger.info("Diagnostics stats mensuelles : %s", rapport_stats)

        zonal.charger_completude_vers_postgis(
            conn, config.DATA_COMPLETUDE_DIR, mois_complets, label_grid, label_to_id
        )

        zonal.charger_ndvi_dates_vers_postgis(
            conn, config.DATA_RAW_S2_INDICES, ctx["grille"], label_grid, label_to_id
        )
        resultats_ndvi = qc.verifier_coherence_ndvi_dates()
        logger.info(
            "QC NDVI dates : %d lignes, %d parcelles, %d doublon(s)",
            resultats_ndvi["n_lignes"],
            resultats_ndvi["n_parcelles"],
            len(resultats_ndvi["doublons"]),
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-scl", action="store_true", help="Sauter §3.1 (SCL/f_valid_aoi)"
    )
    parser.add_argument(
        "--skip-bands",
        action="store_true",
        help="Sauter §3.2 (bandes/indices) et son QC",
    )
    parser.add_argument(
        "--skip-composites",
        action="store_true",
        help="Sauter §3.2quater (QC couverture) et §3.3 (composites)",
    )
    parser.add_argument(
        "--skip-zonal",
        action="store_true",
        help="Sauter §3.4/§3.5/§3.6 (agrégation zonale)",
    )
    args = parser.parse_args()

    ctx = charger_contexte()

    if not args.skip_scl:
        ctx["df_dedup"] = run_scl(ctx)

    df_retenues = preparer_scenes_retenues(ctx["df_dedup"])

    if not args.skip_bands:
        run_bands(ctx, df_retenues)
        run_qc_fichiers(df_retenues)

    mois_complets = determiner_mois_complets(df_retenues)

    if not args.skip_composites:
        run_qc_couverture(ctx, df_retenues, mois_complets)
        run_composites(ctx, df_retenues, mois_complets)

    if not args.skip_zonal:
        run_zonal(ctx, mois_complets)

    logger.info("=== Traitement terminé ===")


if __name__ == "__main__":
    main()
