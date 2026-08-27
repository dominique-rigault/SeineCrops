"""Orchestration de la chaîne divergence/phénologie (§5.1 à §5.4).

Déplacé depuis `scripts/run_phenology.py` pour être réutilisable à la
fois par le script CLI (désormais une simple enveloppe autour de ce
module) et par le DAG Airflow, sans dupliquer la logique entre les deux.

Pas de téléchargement réseau lourd, seul `calculer_flag_raccord_orbital`
fait 2 appels à l'API CDSE (empreintes de scènes). Cet appel est
obligatoire mais skippable : son échec ne fait pas planter tout le run,
mais dégrade `derived.divergence` (`dist_raccord`/`zone_raccord_orbital`
resteront `NULL`/`False`), cf. `divergence.py` et `methode.md` pour les
conséquences (risque de confondre du bruit d'échantillonnage géométrique
avec une vraie divergence agronomique dans la bande orbitale).

⚠️ `preparer_feature_set()` porte le même nom que la fonction homonyme de
`src.ml.orchestration`, mais fait autre chose (remet `id_parcel` en
colonne, pas seulement charge/pivote) : les deux ne se confondent pas en
pratique puisqu'elles vivent dans des modules différents, appelées avec
un préfixe de module (`ml_orch.preparer_feature_set()` vs
`pheno_orch.preparer_feature_set()`), mais à garder en tête si les deux
sont importées ensemble dans le DAG.
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

logger = logging.getLogger(__name__)

VERSION_PIPELINE = "S4-v1"


def preparer_feature_set():
    """§5.1 : réutilise `src.ml.features`, pas de logique propre à ce module.

    `id_parcel` est ramené en colonne explicite (`reset_index`), `src.ml.features`
    le garde en index, mais tout `divergence.py`/`phenology.py` en aval suppose
    une colonne (comme le notebook d'origine). Normalisé ici, une seule fois,
    plutôt que de gérer l'ambiguïté index/colonne dans chaque fonction appelée.
    """
    logger.info("=== §5.1, chargement et pivot (réutilise src.ml.features) ===")
    df_long = charger_feature_set_long()
    df_wide = pivoter_features(df_long)
    df_classes = charger_et_regrouper_classes()
    df = joindre_classes(df_wide, df_classes)
    df = df.reset_index()  # id_parcel : index -> colonne
    return df


def calculer_divergence(df):
    """§5.2 : standardisation, profils médians, distance RMS, seuils, flag raccord orbital."""
    logger.info("=== §5.2, profils médians et scores de divergence ===")
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
            "Flag raccord orbital non calculé (%s), dist_raccord/zone_raccord_orbital resteront "
            "NULL/False. Risque de confondre du bruit d'échantillonnage géométrique avec une vraie "
            "divergence agronomique dans la bande orbitale (cf. methode.md). À relancer dès que possible.",
            e,
        )
        df["dist_raccord"] = float("nan")
        df["zone_raccord_orbital"] = False

    return df


def extraire_phenologie_pipeline(df):
    """§5.3 : chargement NDVI, binning, lissage Whittaker, extraction SOS/POS/EOS/LOS."""
    logger.info("=== §5.3, lissage NDVI et extraction phénologique ===")
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
    """§5.4 : upserts, vérification.

    Suppose `derived.divergence` et `derived.phenologie` déjà créées par la
    migration `0006` (DDL versionné depuis le sprint S3, plus de
    `CREATE TABLE` en dur ici, cf. `src/phenology/persist.py`).
    """
    logger.info("=== §5.4, chargement PostGIS ===")
    persist.upsert_divergence(df, VERSION_PIPELINE)
    persist.upsert_phenologie(df_pheno, whittaker.LAMBDA_WHITTAKER, VERSION_PIPELINE)
    persist.verifier_chargement()

    rapport_synthese = divergence.generer_diagnostics_synthese(
        df, df_pheno, phenology.FENETRES_PHENOLOGIE
    )
    logger.info("Diagnostics synthèse divergence/phénologie : %s", rapport_synthese)
