"""Sauvegarde des prédictions — portage de `04_classification.ipynb` §4.4.

Persistance des prédictions du modèle final dans une table PostGIS, en
prévision de l'API FastAPI (S5, qui expose déjà classe/proba par parcelle)
et des scores de divergence/phénologie (S4, à raccorder dans une itération
ultérieure).

Le modèle est réappliqué à **toutes** les parcelles (train + test), pas
seulement au jeu de test : l'objectif n'est plus l'évaluation mais la
couverture complète nécessaire à S4/S5. La colonne `split` est conservée
pour distinguer une prédiction in-sample (train) d'une prédiction
out-of-sample (test).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values
from sklearn.ensemble import RandomForestClassifier

from src.db.connection import connexion

logger = logging.getLogger(__name__)

DDL_CLASSIFICATION = """
CREATE TABLE IF NOT EXISTS derived.parcelles_classification (
    id_parcel       TEXT PRIMARY KEY,
    classe_predite  TEXT NOT NULL,
    classe_declaree TEXT NOT NULL,
    proba_max       REAL NOT NULL,
    split           TEXT NOT NULL CHECK (split IN ('train', 'test')),
    model_version   TEXT NOT NULL,
    date_prediction TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
# ⚠ Suppose id_parcel de type TEXT, cohérent avec derived.rpg_parcelles_aoi.id_parcel


def creer_table_classification() -> None:
    """Crée `derived.parcelles_classification` si absente (portage cellule 30, idempotent)."""
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL_CLASSIFICATION)
        conn.commit()
    logger.info("Table derived.parcelles_classification prête (créée si absente).")


def predire_toutes_parcelles(
    modele: RandomForestClassifier,
    df_wide: pd.DataFrame,
    feature_cols: list[str],
    version_prefix: str = "rf_tuned",
) -> pd.DataFrame:
    """Applique le modèle à toutes les parcelles, train et test confondus
    (portage cellule 31) — voir note de module sur la couverture complète.

    Retourne un DataFrame `[id_parcel, classe_predite, classe_declaree,
    proba_max, split, model_version]`.
    """
    X_full = df_wide.loc[:, feature_cols].values
    proba_full = modele.predict_proba(X_full)
    y_pred_full = modele.classes_[np.argmax(proba_full, axis=1)]
    proba_max_full = proba_full.max(axis=1)

    model_version = f"{version_prefix}_{pd.Timestamp.today().strftime('%Y%m%d')}"

    df_predictions = pd.DataFrame(
        {
            "id_parcel": df_wide.index,
            "classe_predite": y_pred_full,
            "classe_declaree": df_wide["classe"].to_numpy(),
            "proba_max": proba_max_full,
            "split": df_wide["split"].to_numpy(),
            "model_version": model_version,
        }
    )

    logger.info(
        "Prédictions générées : %s parcelles\n%s",
        f"{len(df_predictions):,}",
        df_predictions["split"].value_counts().to_string(),
    )
    return df_predictions


def upsert_predictions(df_predictions: pd.DataFrame) -> int:
    """Upsert dans `derived.parcelles_classification` (portage cellule 32).

    `ON CONFLICT ... DO UPDATE` — cohérent avec la décision déjà actée dans
    `methode.md` (les prédictions changent à chaque run du modèle,
    contrairement aux composites S2 stables entre deux runs qui utilisent
    `DO NOTHING`). Retourne le nombre de lignes upsertées.
    """
    records = list(
        df_predictions[
            [
                "id_parcel",
                "classe_predite",
                "classe_declaree",
                "proba_max",
                "split",
                "model_version",
            ]
        ].itertuples(index=False, name=None)
    )

    sql_upsert = """
        INSERT INTO derived.parcelles_classification
            (id_parcel, classe_predite, classe_declaree, proba_max, split, model_version)
        VALUES %s
        ON CONFLICT (id_parcel) DO UPDATE SET
            classe_predite  = EXCLUDED.classe_predite,
            classe_declaree = EXCLUDED.classe_declaree,
            proba_max       = EXCLUDED.proba_max,
            split           = EXCLUDED.split,
            model_version   = EXCLUDED.model_version,
            date_prediction = now()
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql_upsert, records, page_size=5000)
        conn.commit()

    logger.info(
        "%s prédictions upsertées dans derived.parcelles_classification",
        f"{len(records):,}",
    )
    return len(records)


def verifier_predictions() -> pd.DataFrame:
    """Vérification post-upsert : nombre de lignes et proba moyenne par
    `model_version` × `split` (portage cellule 33).
    """
    with connexion() as conn:
        df_check = pd.read_sql(
            "SELECT model_version, split, COUNT(*) AS n, ROUND(AVG(proba_max)::numeric, 3) AS proba_moy "
            "FROM derived.parcelles_classification GROUP BY model_version, split ORDER BY model_version, split",
            conn,
        )
    logger.info("Vérification post-upsert :\n%s", df_check.to_string(index=False))
    return df_check
