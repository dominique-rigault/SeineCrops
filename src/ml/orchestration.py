"""Orchestration de la chaîne de classification (§4.1 à §4.4).

Déplacé depuis `scripts/run_ml.py` pour être réutilisable à la fois par
le script CLI (désormais une simple enveloppe autour de ce module) et par
le DAG Airflow, sans dupliquer la logique entre les deux.

Contrairement à `src.processing.orchestration`, cette chaîne est rapide
(minutes, pas heures) et sans téléchargement réseau, repose entièrement
sur PostGIS et du calcul en mémoire. Moins de contraintes pour de futurs
tests automatisés, mais toujours pas d'exécution CI directe :
`RandomizedSearchCV` (`n_iter=20 × cv=3`, forêts de 200-600 arbres) reste
coûteux en CPU.
"""

from __future__ import annotations

import logging

from src.ml import features, imputation, predict, split, train

logger = logging.getLogger(__name__)


def preparer_feature_set() -> tuple:
    """§4.1 + §4.1bis : feature set, classes, imputation ciblée.

    Retourne `(df_wide, mois_order)`, `df_wide` prêt pour le split spatial.
    """
    logger.info("=== §4.1, préparation du feature set ===")
    df_long = features.charger_feature_set_long()
    df_wide = features.pivoter_features(df_long)
    df_classes = features.charger_et_regrouper_classes()
    df_wide = features.joindre_classes(df_wide, df_classes)
    diag_nan = features.diagnostiquer_nan(df_wide)
    logger.info("Diagnostic NaN : %.2f%% avant imputation", diag_nan["pct_nan"])

    logger.info("=== §4.1bis, règle de décision complétude temporelle ===")
    df_completude = imputation.charger_completude()
    df_completude = imputation.calculer_qc_action(df_completude)
    tier_wide, mois_order = imputation.construire_tier_wide(
        df_completude, df_wide.index
    )
    df_diag = imputation.diagnostiquer_distance_ancrage(tier_wide, mois_order)
    tier_wide = imputation.corriger_tier_ancrage_eloigne(tier_wide, df_diag)
    df_wide = imputation.appliquer_interpolation(df_wide, tier_wide, mois_order)

    return df_wide, mois_order


def appliquer_split_spatial(df_wide):
    """§4.2 : split spatial par blocs."""
    logger.info("=== §4.2, split spatial par blocs ===")
    df_centr = split.charger_centroides(df_wide.index)
    df_centr = split.split_spatial_par_blocs(df_centr)
    df_wide = split.joindre_split(df_wide, df_centr)
    split.verifier_representation_classes(df_wide)
    return df_wide


def entrainer_et_evaluer(df_wide, skip_search: bool) -> dict:
    """§4.3 : matrices, baseline, recherche d'hyperparamètres (optionnelle), évaluation."""
    logger.info("=== §4.3, entraînement Random Forest ===")
    matrices = train.construire_matrices(df_wide)

    rf_base = train.entrainer_rf_baseline(matrices)
    resultats_base = train.evaluer_modele(rf_base, matrices)
    rapport_base = train.generer_diagnostics_modele(
        resultats_base,
        modele=rf_base,
        feature_cols=matrices["feature_cols"],
        nom_module="ml_baseline",
    )
    logger.info("Diagnostics baseline : %s", rapport_base)

    if skip_search:
        logger.info("skip_search : modèle final = baseline (pas de RandomizedSearchCV)")
        return {
            "matrices": matrices,
            "modele_final": rf_base,
            "resultats": resultats_base,
        }

    search = train.rechercher_hyperparametres(matrices)
    rf_tuned = search.best_estimator_
    resultats_tuned = train.evaluer_modele(rf_tuned, matrices)
    rapport_tuned = train.generer_diagnostics_modele(
        resultats_tuned,
        modele=rf_tuned,
        feature_cols=matrices["feature_cols"],
        nom_module="ml_tuned",
    )
    logger.info("Diagnostics modèle tuné : %s", rapport_tuned)

    return {
        "matrices": matrices,
        "modele_final": rf_tuned,
        "resultats": resultats_tuned,
    }


def sauvegarder_predictions(df_wide, modele, feature_cols, version_prefix: str) -> None:
    """§4.4 : persistance des prédictions PostGIS."""
    logger.info("=== §4.4, sauvegarde des prédictions ===")
    predict.creer_table_classification()
    df_predictions = predict.predire_toutes_parcelles(
        modele, df_wide, feature_cols, version_prefix=version_prefix
    )
    predict.upsert_predictions(df_predictions)
    predict.verifier_predictions()
