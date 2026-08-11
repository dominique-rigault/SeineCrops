"""Préparation du feature set — portage de `04_classification.ipynb` §4.1.

Construction de la matrice d'entrée du modèle à partir de
`derived.s2_parcelles_monthly` (format long : une ligne par
parcelle × mois × variable) et de `derived.rpg_parcelles_aoi`
(culture déclarée, regroupée en classes cibles).

`GROUP_MAP` reste une constante de module (pas dans `src/config.py`) :
c'est une décision de modélisation (le regroupement des `code_group` RPG
en classes cibles), pas un paramètre de campagne comme `millesime`/
`region_code` — sa place naturelle est avec le code qui l'utilise.
"""

from __future__ import annotations

import logging

import pandas as pd

from src.db.connection import connexion

logger = logging.getLogger(__name__)

STATS = ["mean", "std", "p10", "p90"]

# Mapping code_group RPG → classe cible (v3, 8 classes) — cf. §4.1 pour le détail
GROUP_MAP = {
    1: "cereales_hiver",  # Blé tendre
    3: "cereales_hiver",  # Orge
    2: "mais",  # Maïs grain et ensilage
    5: "colza",  # Colza
    9: "lin",  # Plantes à fibres (≈ lin fibre en Normandie)
    18: "prairie",  # Prairies permanentes
    19: "prairie",  # Prairies temporaires
    25: "legumes_fleurs",  # Légumes ou fleurs
}


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
    df_melt = df_long.melt(
        id_vars=["id_parcel", "mois", "variable"],
        value_vars=stats,
        var_name="stat",
        value_name="value",
    )
    df_melt["feature"] = (
        df_melt["variable"] + "_" + df_melt["stat"] + "_" + df_melt["mois"]
    )

    df_wide = df_melt.pivot(index="id_parcel", columns="feature", values="value")
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


def charger_et_regrouper_classes(group_map: dict = GROUP_MAP) -> pd.DataFrame:
    """Charge `code_group`/`code_cultu`, regroupe en classes cibles (portage cellule 5).

    Retourne un DataFrame indexé `id_parcel`, colonne `classe` — les 6
    `id_parcel` en doublon du RPG source sont dédupliqués (premier
    conservé), déjà identifiés en S1.
    """
    with connexion() as conn:
        df_labels = pd.read_sql(
            "SELECT id_parcel, code_group::int AS code_group, code_cultu FROM derived.rpg_parcelles_aoi",
            conn,
        )
    df_labels = df_labels.drop_duplicates(subset="id_parcel", keep="first")

    df_labels["classe"] = df_labels["code_group"].map(group_map).fillna("autres")
    df_labels.loc[df_labels["code_cultu"] == "BTN", "classe"] = (
        "betterave"  # exception : betterave hors GROUP_MAP
    )

    logger.info(
        "Classes assignées : %s parcelles\n%s",
        f"{len(df_labels):,}",
        df_labels["classe"].value_counts().to_string(),
    )
    return df_labels.set_index("id_parcel")[["classe"]]


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
