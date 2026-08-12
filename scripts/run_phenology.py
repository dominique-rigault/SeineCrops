"""Exécution manuelle de la chaîne divergence/phénologie (§5.1 → §5.4).

Même principe que `run_ingestion.py`/`run_processing.py`/`run_ml.py` :
appelle `src.phenology.*` (et `src.ml.features` pour §5.1, réutilisé sans
duplication) dans l'ordre, en attendant le DAG Airflow.

Pas de téléchargement réseau lourd — seul `calculer_flag_raccord_orbital`
fait 2 appels à l'API CDSE (empreintes de scènes). Cet appel est
**obligatoire mais skippable** : son échec ne fait pas planter tout le
run, mais dégrade `derived.divergence` (`dist_raccord`/
`zone_raccord_orbital` resteront `NULL`/`False`) — cf. `divergence.py` et
`methode.md` pour les conséquences (risque de confondre du bruit
d'échantillonnage géométrique avec une vraie divergence agronomique dans
la bande orbitale).

Usage :
    python -m scripts.run_phenology
"""

from __future__ import annotations

import logging

from src import config
from src.ml.features import (
    charger_et_regrouper_classes,
    charger_feature_set_long,
    joindre_classes,
    pivoter_features,
)
from src.phenology import divergence, persist, phenology, whittaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("run_phenology")

VERSION_PIPELINE = "S4-v1"


def preparer_feature_set():
    """§5.1 — réutilise `src.ml.features`, pas de logique propre à ce script.

    `id_parcel` est ramené en colonne explicite (`reset_index`) — `src.ml.features`
    le garde en index, mais tout `divergence.py`/`phenology.py` en aval suppose
    une colonne (comme le notebook d'origine). Normalisé ici, une seule fois,
    plutôt que de gérer l'ambiguïté index/colonne dans chaque fonction appelée.
    """
    logger.info("=== §5.1 — Chargement et pivot (réutilise src.ml.features) ===")
    df_long = charger_feature_set_long()
    df_wide = pivoter_features(df_long)
    df_classes = charger_et_regrouper_classes()
    df = joindre_classes(df_wide, df_classes)
    df = df.reset_index()  # id_parcel : index -> colonne
    return df


def calculer_divergence(df):
    """§5.2 — standardisation, profils médians, distance RMS, seuils, flag raccord orbital."""
    logger.info("=== §5.2 — Profils médians et scores de divergence ===")
    feature_cols = [c for c in df.columns if c not in divergence.NON_FEATURE_COLS]

    X_scaled, _mu, _sigma = divergence.standardiser_features(df, feature_cols)
    medians = divergence.calculer_profils_medians(
        X_scaled, df["classe"].values, feature_cols
    )
    df = divergence.calculer_distance_rms(df, X_scaled, medians, feature_cols)
    df, stats_div = divergence.calculer_seuils_divergence(df)

    rapport_dist = divergence.generer_diagnostics_divergence_distribution(df, stats_div)
    logger.info("Diagnostics distribution divergence : %s", rapport_dist)
    rapport_spatial = divergence.generer_diagnostics_divergence_spatiale(df)
    logger.info("Diagnostics répartition spatiale : %s", rapport_spatial)

    try:
        df = divergence.calculer_flag_raccord_orbital(
            df, config.DATA_RAW_S2 / "catalogue_dedup.parquet"
        )
    except Exception as e:
        logger.warning(
            "Flag raccord orbital non calculé (%s) — dist_raccord/zone_raccord_orbital resteront "
            "NULL/False. Risque de confondre du bruit d'échantillonnage géométrique avec une vraie "
            "divergence agronomique dans la bande orbitale (cf. methode.md). À relancer dès que possible.",
            e,
        )
        df["dist_raccord"] = float("nan")
        df["zone_raccord_orbital"] = False

    return df


def extraire_phenologie_pipeline(df):
    """§5.3 — chargement NDVI, binning, lissage Whittaker, extraction SOS/POS/EOS/LOS."""
    logger.info("=== §5.3 — Lissage NDVI et extraction phénologique ===")
    df_ndvi_long = whittaker.charger_ndvi_profils(df["id_parcel"])
    grille = whittaker.construire_grille_et_binning(df_ndvi_long, df["id_parcel"])
    X_smooth = whittaker.lisser_whittaker(grille["X_valeurs"], grille["X_poids"])

    df_pheno = phenology.extraire_phenologie_toutes_parcelles(
        X_smooth,
        grille["jours_grille"],
        df["id_parcel"].to_numpy(),
        df["classe"].to_numpy(),
        grille["date_min"],
    )

    rapport_pheno = phenology.generer_diagnostics_phenologie(
        df["id_parcel"].to_numpy(),
        df["classe"].to_numpy(),
        X_smooth,
        grille["jours_grille"],
        df_pheno,
        df_ndvi_long,
        grille["date_min"],
    )
    logger.info("Diagnostics phénologie : %s", rapport_pheno)

    return df_pheno


def persister_resultats(df, df_pheno) -> None:
    """§5.4 — DDL, upserts, vérification."""
    logger.info("=== §5.4 — Chargement PostGIS ===")
    persist.creer_tables_phenologie()
    persist.upsert_divergence(df, VERSION_PIPELINE)
    persist.upsert_phenologie(df_pheno, whittaker.LAMBDA_WHITTAKER, VERSION_PIPELINE)
    persist.verifier_chargement()

    rapport_synthese = divergence.generer_diagnostics_synthese(
        df, df_pheno, phenology.FENETRES_PHENOLOGIE
    )
    logger.info("Diagnostics synthèse divergence/phénologie : %s", rapport_synthese)


def main() -> None:
    df = preparer_feature_set()
    df = calculer_divergence(df)
    df_pheno = extraire_phenologie_pipeline(df)
    persister_resultats(df, df_pheno)

    logger.info("=== Divergence/phénologie terminé ===")


if __name__ == "__main__":
    main()
