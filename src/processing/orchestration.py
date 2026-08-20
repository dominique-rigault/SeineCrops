"""Orchestration de la chaîne de traitement Sentinel-2 (§3.1 à §3.6).

Déplacé depuis `scripts/run_processing.py` pour être réutilisable à la
fois par le script CLI (désormais une simple enveloppe autour de ce
module) et par le DAG Airflow, sans dupliquer la logique entre les deux.

⚠️ **Contraintes de durée et de mémoire, à anticiper pour les futurs
tests** : `run_bands`/`run_composites` téléchargent potentiellement
plusieurs centaines de scènes (plusieurs Go), traitent des rasters sur
l'emprise complète de l'AOI, et peuvent tourner plusieurs heures. Les
futurs tests unitaires/d'intégration de cette chaîne (cf. `methode.md`
§Tests) devront s'appuyer sur des fixtures réduites, pas sur ces
fonctions directement :
- une grille AOI minuscule (quelques dizaines de pixels, pas la grille
  réelle) pour `grid.py`/`composites.py`/`zonal.py` ;
- 1-2 scènes synthétiques (petits GeoTIFF fabriqués à la main) plutôt que
  des téléchargements CDSE réels, pour `bands.py`/`scl.py` ;
- les appels réseau (CDSE) mockés, jamais exécutés en CI.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from src import config
from src.acquisition.cdse import get_cdse_token
from src.db.connection import get_connection
from src.processing import bands, composites, grid, qc, scl, zonal

logger = logging.getLogger(__name__)


def charger_contexte() -> dict:
    """Charge le catalogue dédupliqué, l'AOI, et calcule la grille de référence.

    État partagé entre toutes les phases, chargé une seule fois plutôt que
    recalculé à chaque appel de fonction.

    Normalise `scene_id` (retire un éventuel suffixe `.SAFE`), le champ
    `Name` du catalogue OData peut ou non l'inclure selon la version, et
    `get_granule_id`/`download_band`/`_telecharger_scl` ajoutent déjà
    `.SAFE` eux-mêmes lors de la construction des URLs. Omis lors du
    portage initial de `03_series_s2.ipynb` (§3.1, juste après le
    chargement du parquet), provoquait un `.SAFE.SAFE` et un 404
    systématique sur *toutes* les scènes, détecté au premier run réel.
    """
    df_dedup = pd.read_parquet(config.DATA_RAW_S2 / "catalogue_dedup.parquet")
    df_dedup["scene_id"] = df_dedup["scene_id"].str.removesuffix(".SAFE")
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
    """§3.1 : masque SCL, calcul de `f_valid_aoi` par scène."""
    logger.info("=== §3.1, SCL et f_valid_aoi ===")
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
    """§3.2 : téléchargement des bandes, calcul des indices."""
    logger.info(
        "=== §3.2, bandes et indices (%d scènes retenues) ===", len(df_retenues)
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
    """§3.2 bis : QC de complétude des fichiers bandes/indices."""
    logger.info("=== §3.2 bis, QC complétude fichiers ===")
    problemes = qc.verifier_completude_fichiers(
        df_retenues, config.DATA_RAW_S2_BANDS, config.DATA_RAW_S2_INDICES
    )
    rapport = qc.generer_diagnostics_completude_fichiers(problemes, len(df_retenues))
    logger.info("Diagnostics QC fichiers : %s", rapport)
    if problemes:
        logger.warning(
            "%d problème(s) détecté(s), NE PAS lancer qc.supprimer_jp2() avant correction",
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
        "Mois complets : %d/%d, %s", len(mois_complets), len(mois_list), mois_complets
    )
    return mois_complets


def run_qc_couverture(
    ctx: dict, df_retenues: pd.DataFrame, mois_complets: list[str]
) -> pd.DataFrame:
    """§3.2 quater : QC de couverture temporelle mensuelle."""
    logger.info("=== §3.2 quater, QC couverture temporelle ===")
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
    """§3.3 : composites mensuels."""
    logger.info("=== §3.3, composites mensuels ===")
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
    """§3.4/§3.5/§3.6 : agrégation zonale, connexion unique.

    Connexion ouverte/fermée explicitement (pas `with get_connection() as
    conn:`, qui ne ferme pas la connexion en sortie de bloc chez
    `psycopg2`, ne gère que la transaction). Justifié ici par la durée de
    vie de la connexion, partagée entre plusieurs opérations lourdes
    séquentielles, contrairement aux appels courts du reste de `src/`.
    """
    logger.info("=== §3.4/§3.5/§3.6, agrégation zonale ===")
    run_zonal_composites(ctx)
    run_zonal_completude(ctx, mois_complets)
    run_zonal_ndvi_dates(ctx)


def _preparer_grille_labels(ctx: dict):
    """Crée les tables si absentes, rasterise la grille de labels.

    Recalculée indépendamment par chacune des 3 fonctions `run_zonal_*`
    (redondant, ~15-20 s à chaque fois d'après les runs déjà observés),
    plutôt que partagée via un état commun, nécessaire pour que les 3
    étapes puissent devenir des tâches Airflow indépendantes, exécutables
    en parallèle : `label_grid`/`label_to_id` sont trop volumineux pour un
    passage léger entre tâches (XCom), donc chaque tâche doit pouvoir se
    les procurer elle-même plutôt que de dépendre d'un état partagé.
    """
    conn = get_connection()
    conn.autocommit = True
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
    return conn, label_grid, label_to_id


def run_zonal_composites(ctx: dict) -> None:
    """§3.4 seul : stats zonales des composites mensuels."""
    logger.info("=== §3.4, stats zonales mensuelles ===")
    conn, label_grid, label_to_id = _preparer_grille_labels(ctx)
    try:
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
    finally:
        conn.close()


def run_zonal_completude(ctx: dict, mois_complets: list[str]) -> None:
    """§3.5 seul : stats zonales de complétude."""
    logger.info("=== §3.5, complétude zonale ===")
    conn, label_grid, label_to_id = _preparer_grille_labels(ctx)
    try:
        zonal.charger_completude_vers_postgis(
            conn, config.DATA_COMPLETUDE_DIR, mois_complets, label_grid, label_to_id
        )
    finally:
        conn.close()


def run_zonal_ndvi_dates(ctx: dict) -> None:
    """§3.6 seul : NDVI aux dates d'acquisition."""
    logger.info("=== §3.6, NDVI zonal aux dates d'acquisition ===")
    conn, label_grid, label_to_id = _preparer_grille_labels(ctx)
    try:
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
