"""Préparation du feature set — portage de `04_classification.ipynb` §4.1.

Construction de la matrice d'entrée du modèle à partir de
`derived.s2_parcelles_monthly` (format long : une ligne par
parcelle × mois × variable) et de `derived.rpg_parcelles_aoi`
(culture déclarée, regroupée en classes cibles).

Depuis la migration 0005 (sprint S3) : `classe_declaree` est calculée et
centralisée en base (`CASE` SQL équivalent à l'ancien `GROUP_MAP`, cf.
`db/migrations/0005_rpg_parcelles_aoi_classe_declaree.sql`) —
`rpg_parcelles_aoi` est désormais la source de vérité, `GROUP_MAP` a
disparu de ce module. Toute évolution du regroupement de classes doit
être faite dans une nouvelle migration SQL, pas ici.
"""

from __future__ import annotations

import logging

import pandas as pd

from src.db.connection import connexion

logger = logging.getLogger(__name__)

STATS = ["mean", "std", "p10", "p90"]


def charger_feature_set_long() -> pd.DataFrame:
    """Charge `derived.s2_parcelles_monthly` (format long, ~14 M lignes). Portage cellule 3."""
    with connexion() as conn:
        df_long = pd.read_sql(
            "SELECT id_parcel, mois, variable, mean, std, p10, p90 "
            "FROM derived.s2_parcelles_monthly ORDER BY id_parcel, mois, variable",
            conn,
        )
    logger.info(
        "Feature set long : %s lignes, %s parcelles",
        f"{len(df_long):,}",
        f"{df_long['id_parcel'].nunique():,}",
    )
    return df_long


def pivoter_features(
    df_long: pd.DataFrame,
    stats: list[str] = STATS,
    n_features_attendu: int | None = None,
) -> pd.DataFrame:
    """Pivote long → wide : une ligne par parcelle, une colonne par
    `{variable}_{stat}_{mois}` (portage cellule 4).

    `n_features_attendu` : vérification optionnelle (le notebook figeait
    `704` en dur — spécifique à la campagne actuelle, 11 variables × 4
    stats × 16 mois ; passé en paramètre plutôt qu'en constante pour ne
    pas coupler ce module à une campagne donnée, cf. `src/config.py`).
    """
    df_wide = df_long.set_index(["id_parcel", "mois", "variable"])[stats].unstack(
        ["variable", "mois"]
    )
    df_wide.columns = [
        f"{variable}_{stat}_{mois}" for stat, variable, mois in df_wide.columns
    ]
    df_wide.columns.name = None

    logger.info(
        "Matrice wide : %s parcelles × %d features",
        f"{df_wide.shape[0]:,}",
        df_wide.shape[1],
    )
    if n_features_attendu is not None and df_wide.shape[1] != n_features_attendu:
        raise ValueError(
            f"Attendu {n_features_attendu} features, obtenu {df_wide.shape[1]}"
        )
    return df_wide


def charger_et_regrouper_classes() -> pd.DataFrame:
    """Charge la classe déclarée depuis `derived.rpg_parcelles_aoi` (portage cellule 5).

    Depuis la migration 0005 (sprint S3), `classe_declaree` est calculée et
    centralisée en base : cette fonction se contente de la lire, elle ne
    la recalcule plus (`GROUP_MAP` supprimé de ce module).

    Retourne un DataFrame indexé `id_parcel`, colonne `classe` — une ligne
    par parcelle garantie par la PK posée en 0003, plus besoin de
    `drop_duplicates` ici.
    """
    with connexion() as conn:
        df_classes = pd.read_sql(
            "SELECT id_parcel, classe_declaree AS classe FROM derived.rpg_parcelles_aoi",
            conn,
        )
    logger.info(
        "Classes chargées : %s parcelles\n%s",
        f"{len(df_classes):,}",
        df_classes["classe"].value_counts().to_string(),
    )
    return df_classes.set_index("id_parcel")[["classe"]]


def joindre_classes(df_wide: pd.DataFrame, df_classes: pd.DataFrame) -> pd.DataFrame:
    """Joint la colonne `classe` à `df_wide` (portage fin cellule 5, idempotent)."""
    df_wide = df_wide.drop(columns=["classe"], errors="ignore")
    df_wide = df_wide.join(df_classes)

    logger.info(
        "Parcelles avec classe assignée : %s / %s",
        f"{df_wide['classe'].notna().sum():,}",
        f"{len(df_wide):,}",
    )
    return df_wide


def diagnostiquer_nan(df_wide: pd.DataFrame) -> dict:
    """Diagnostic des valeurs manquantes, global et par mois (portage cellule 6).

    Retourne `{"n_nan", "n_total", "pct_nan", "par_mois": {mois: {"n_nan", "pct"}}}`.
    """
    feature_cols = [c for c in df_wide.columns if c != "classe"]
    n_total = len(df_wide) * len(feature_cols)
    n_nan = int(df_wide[feature_cols].isna().sum().sum())

    nan_by_month: dict[str, list[int]] = {}
    for col in feature_cols:
        mois = col.rsplit("_", 1)[-1]  # ndvi_mean_2024-06 → 2024-06
        nan_by_month.setdefault(mois, []).append(int(df_wide[col].isna().sum()))

    par_mois = {}
    for mois, valeurs in nan_by_month.items():
        total = sum(valeurs)
        pct = 100 * total / (len(valeurs) * len(df_wide))
        par_mois[mois] = {"n_nan": total, "pct": round(pct, 2)}

    resultats = {
        "n_nan": n_nan,
        "n_total": n_total,
        "pct_nan": round(100 * n_nan / n_total, 2),
        "par_mois": par_mois,
    }
    logger.info(
        "NaN : %s / %s (%.2f%%)", f"{n_nan:,}", f"{n_total:,}", resultats["pct_nan"]
    )
    return resultats
