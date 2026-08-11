"""Règle de décision sur la complétude temporelle — portage de
`04_classification.ipynb` §4.1bis.

⚠️ **Ordonnancement des fonctions corrigé** par rapport à l'ordre
d'affichage des cellules du notebook (8 → 9 → 10 → 11) : les cellules 9 et
10 utilisaient `tier_wide`/`MOIS_ORDER`, définis seulement en cellule 11 —
artefact d'édition non linéaire en Jupyter (le notebook a dû être exécuté
dans un ordre différent de son ordre d'affichage), pas une erreur de
logique métier, confirmé avant migration. Ordre logique réel, reproduit
ici par l'ordre des fonctions :
1. `calculer_qc_action` (cellule 8)
2. `construire_tier_wide` (début cellule 11 : pivot + `MOIS_ORDER`)
3. `diagnostiquer_distance_ancrage` (cellule 9)
4. `corriger_tier_ancrage_eloigne` (cellule 10)
5. `appliquer_interpolation` (fin cellule 11, utilise `tier_wide` corrigé
   par l'étape 4)

La table `derived.s2_parcelles_completude` (§3.5) porte, par
`id_parcel × mois`, le taux de couverture spatiale (`pct_pixels_couverts`)
du composite. Elle permet de trancher, pour chaque cellule de la matrice
de features, entre trois actions : **exclure** (0 % — NaN structurel,
laissé tel quel), **imputer** (< seuil — valeur peu fiable, remplacée par
interpolation temporelle linéaire), **conserver** (≥ seuil — inchangée).
"""

from __future__ import annotations

import logging

import pandas as pd

from src.db.connection import connexion
from src.ml.features import STATS

logger = logging.getLogger(__name__)

SEUIL_COUVERTURE = 0.50


def charger_completude() -> pd.DataFrame:
    """Charge `derived.s2_parcelles_completude` (portage début cellule 8)."""
    with connexion() as conn:
        df_completude = pd.read_sql(
            "SELECT id_parcel, mois, n_dates_valides_moy, pct_pixels_couverts "
            "FROM derived.s2_parcelles_completude",
            conn,
        )
    logger.info("Complétude : %s lignes (id_parcel × mois)", f"{len(df_completude):,}")
    return df_completude


def calculer_qc_action(
    df_completude: pd.DataFrame, seuil: float = SEUIL_COUVERTURE
) -> pd.DataFrame:
    """Ajoute la colonne `qc_action` (`exclure`/`imputer`/`conserver`) selon
    `pct_pixels_couverts` (portage fin cellule 8)."""

    def _action(pct: float) -> str:
        if pct == 0:
            return "exclure"
        if pct < seuil:
            return "imputer"
        return "conserver"

    df_completude = df_completude.copy()
    df_completude["qc_action"] = df_completude["pct_pixels_couverts"].apply(_action)

    logger.info(
        "Répartition qc_action :\n%s",
        df_completude["qc_action"].value_counts().to_string(),
    )
    return df_completude


def construire_tier_wide(
    df_completude: pd.DataFrame, index_reference: pd.Index
) -> tuple[pd.DataFrame, list[str]]:
    """Pivote `qc_action` en matrice (parcelle × mois), réindexée sur l'index
    de `df_wide` (portage début cellule 11). Retourne `(tier_wide, mois_order)`.
    """
    tier_wide = df_completude.pivot(
        index="id_parcel", columns="mois", values="qc_action"
    )
    tier_wide = tier_wide.reindex(index_reference)
    mois_order = sorted(tier_wide.columns)
    return tier_wide, mois_order


def diagnostiquer_distance_ancrage(
    tier_wide: pd.DataFrame, mois_order: list[str]
) -> pd.DataFrame:
    """Pour chaque cellule `imputer`, distance (en mois) au voisin non-exclu
    le plus proche, de part et d'autre (portage cellule 9).

    Retourne un DataFrame `[id_parcel, mois, dist_gauche, dist_droite, dist_min]`
    — `dist_min` vaut `None` si aucun ancrage valide des deux côtés.
    """
    mois_idx = {m: i for i, m in enumerate(mois_order)}
    has_impute = (tier_wide == "imputer").any(axis=1)
    sous_tier = tier_wide[has_impute]

    rows_diag = []
    for id_parcel, row in sous_tier.iterrows():
        for mois in mois_order:
            if row[mois] != "imputer":
                continue
            i = mois_idx[mois]

            dist_gauche = next(
                (
                    i - j
                    for j in range(i - 1, -1, -1)
                    if row[mois_order[j]] != "exclure"
                ),
                None,
            )
            dist_droite = next(
                (
                    j - i
                    for j in range(i + 1, len(mois_order))
                    if row[mois_order[j]] != "exclure"
                ),
                None,
            )

            candidats = [d for d in (dist_gauche, dist_droite) if d is not None]
            dist_min = min(candidats) if candidats else None

            rows_diag.append(
                {
                    "id_parcel": id_parcel,
                    "mois": mois,
                    "dist_gauche": dist_gauche,
                    "dist_droite": dist_droite,
                    "dist_min": dist_min,
                }
            )

    df_diag = pd.DataFrame(rows_diag)
    if len(df_diag):
        logger.info(
            "Cellules 'imputer' analysées : %s, sans ancrage : %s, ancrées à > 1 mois : %s",
            f"{len(df_diag):,}",
            f"{df_diag['dist_min'].isna().sum():,}",
            f"{(df_diag['dist_min'] > 1).sum():,}",
        )
    else:
        logger.info("Aucune cellule 'imputer' à analyser.")
    return df_diag


def corriger_tier_ancrage_eloigne(
    tier_wide: pd.DataFrame, df_diag: pd.DataFrame
) -> pd.DataFrame:
    """Repasse en `exclure` les cellules `imputer` dont l'ancrage valide le
    plus proche est à plus d'un mois — **ou totalement absent** (portage
    cellule 10, étendu).

    Écart volontaire par rapport au notebook source : `dist_min > 1` seul
    ne capture pas le cas `dist_min` absent (`NaN > 1` vaut `False` en
    pandas), laissant ces cellules en `imputer` sans aucune base
    d'interpolation valide — un cas plus défavorable que "ancré à plus
    d'un mois", pourtant non couvert par le filtre d'origine. Étendu ici
    pour couvrir explicitement `dist_min.isna()` en plus de `dist_min > 1`,
    cohérent avec l'esprit de la règle documentée dans `methode.md`
    (la complétude spatiale inspire davantage confiance que l'interpolation
    temporelle) — à vérifier que le nombre de cas concernés reste marginal
    (cf. `n_sans_ancrage` retourné par `diagnostiquer_distance_ancrage`,
    déjà loggé).

    Retourne une **copie** modifiée de `tier_wide` — ne modifie jamais
    l'original en place, contrairement au notebook (`tier_wide.loc[...] = ...`),
    pour que l'appelant garde la main sur l'état avant/après correction.
    """
    tier_wide = tier_wide.copy()
    if len(df_diag) == 0:
        return tier_wide

    a_corriger = df_diag["dist_min"].isna() | (df_diag["dist_min"] > 1)
    cellules_a_corriger = df_diag.loc[a_corriger, ["id_parcel", "mois"]]
    for _, r in cellules_a_corriger.iterrows():
        tier_wide.loc[r["id_parcel"], r["mois"]] = "exclure"

    n_sans_ancrage = int(df_diag["dist_min"].isna().sum())
    n_eloigne = int((df_diag["dist_min"] > 1).sum())
    logger.info(
        "Cellules repassées en 'exclure' : %s (dont %s sans aucun ancrage, %s ancrées à > 1 mois)",
        f"{len(cellules_a_corriger):,}",
        f"{n_sans_ancrage:,}",
        f"{n_eloigne:,}",
    )
    return tier_wide


def appliquer_interpolation(
    df_wide: pd.DataFrame,
    tier_wide: pd.DataFrame,
    mois_order: list[str],
    stats: list[str] = STATS,
) -> pd.DataFrame:
    """Interpolation temporelle linéaire des cellules `imputer`, par variable ×
    stat (portage fin cellule 11).

    `tier_wide` doit être la version déjà corrigée par
    `corriger_tier_ancrage_eloigne` — appliquer l'interpolation avant
    correction imputerait des cellules qui devraient in fine rester exclues.
    Retourne une copie modifiée de `df_wide`.
    """
    df_wide = df_wide.copy()
    variables = sorted({c.split("_")[0] for c in df_wide.columns if c != "classe"})
    n_impute_total = 0

    for var in variables:
        for stat in stats:
            cols_vs = [
                f"{var}_{stat}_{m}"
                for m in mois_order
                if f"{var}_{stat}_{m}" in df_wide.columns
            ]
            if not cols_vs:
                continue

            sub = df_wide[cols_vs]
            interpolated = sub.interpolate(axis=1, limit_direction="both")

            mois_present = [
                m for m in mois_order if f"{var}_{stat}_{m}" in df_wide.columns
            ]
            mask_impute = tier_wide[mois_present] == "imputer"
            mask_impute.columns = cols_vs

            n_impute_total += int(mask_impute.sum().sum())
            df_wide.loc[:, cols_vs] = sub.where(~mask_impute, interpolated)

    n_nan_after = int(
        df_wide.drop(columns=["classe"], errors="ignore").isna().sum().sum()
    )
    logger.info(
        "Valeurs imputées (interpolation temporelle) : %s, NaN résiduels (tier 'exclure') : %s",
        f"{n_impute_total:,}",
        f"{n_nan_after:,}",
    )
    return df_wide
