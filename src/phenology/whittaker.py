"""Lissage Whittaker des profils NDVI — portage de
`05_divergence_pheno.ipynb` §5.3 (partie lissage).

Les acquisitions dans `derived.s2_parcelles_ndvi_dates` sont irrégulières
(166 dates sur 16 mois dans la campagne actuelle). Plutôt qu'interpoler
puis lisser (biais d'interpolation linéaire + double lissage implicite),
un **lisseur de Whittaker** (moindres carrés pénalisés, pondérés par
`n_pixels`) est utilisé — la référence du domaine pour ce type de données
(TIMESAT, HR-VPP Copernicus), nativement adapté aux séries irrégulières :
chaque observation pèse selon sa fiabilité, sans valeur fabriquée entre
deux dates observées.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.linalg import solveh_banded
from scipy.sparse import diags

from src.db.connection import connexion

logger = logging.getLogger(__name__)

LAMBDA_WHITTAKER = (
    800.0  # calibré visuellement (4 niveaux × 4 parcelles-types) — cf. methode.md
)
EPS_RIDGE = 1e-6  # sécurité numérique (parcelles à très peu d'observations)
N_MIN_OBS = 2  # sous ce seuil, système mal posé (noyau de D2'D2) → NaN
PAS_GRILLE_JOURS = 5


def charger_ndvi_profils(id_parcels) -> pd.DataFrame:
    """Charge `derived.s2_parcelles_ndvi_dates` (`n_pixels >= 1`), restreint
    aux `id_parcels` donnés (portage cellule 12).
    """
    with connexion() as conn:
        df_ndvi_long = pd.read_sql(
            "SELECT id_parcel, date, mean AS ndvi, n_pixels "
            "FROM derived.s2_parcelles_ndvi_dates WHERE n_pixels >= 1",
            conn,
        )
    # psycopg2 renvoie un DATE SQL en datetime.date (dtype object) —
    # conversion explicite pour les opérations .dt en aval.
    df_ndvi_long["date"] = pd.to_datetime(df_ndvi_long["date"])
    df_ndvi_long = df_ndvi_long[df_ndvi_long["id_parcel"].isin(id_parcels)]

    logger.info(
        "NDVI : %s lignes, %s parcelles, %s dates distinctes",
        f"{len(df_ndvi_long):,}",
        f"{df_ndvi_long['id_parcel'].nunique():,}",
        f"{df_ndvi_long['date'].nunique():,}",
    )
    return df_ndvi_long


def construire_grille_et_binning(
    df_ndvi_long: pd.DataFrame, id_parcels_ref, pas_jours: int = PAS_GRILLE_JOURS
) -> dict:
    """Grille régulière (pas de `pas_jours` jours) + binning des observations
    (portage cellule 13).

    Chaque observation reste sur sa vraie date, "aimantée" au point de
    grille le plus proche — pas de valeur fabriquée entre deux observations,
    contrairement à une interpolation. Collisions (plusieurs dates dans le
    même bin) résolues par moyenne pondérée par `n_pixels`.

    Retourne `{"grille_dates", "jours_grille", "X_valeurs", "X_poids", "date_min"}`.
    """
    date_min = df_ndvi_long["date"].min()
    date_max = df_ndvi_long["date"].max()
    grille_dates = pd.date_range(date_min, date_max, freq=f"{pas_jours}D")
    jours_grille = (grille_dates - date_min).days.values

    df_ndvi_long = df_ndvi_long.copy()
    df_ndvi_long["jour"] = (df_ndvi_long["date"] - date_min).dt.days
    df_ndvi_long["idx_grille"] = np.searchsorted(jours_grille, df_ndvi_long["jour"])
    df_ndvi_long["idx_grille"] = df_ndvi_long["idx_grille"].clip(
        0, len(jours_grille) - 1
    )

    df_ndvi_long["poids_ndvi"] = df_ndvi_long["ndvi"] * df_ndvi_long["n_pixels"]
    agg = df_ndvi_long.groupby(
        ["id_parcel", "idx_grille"], sort=False, as_index=False
    ).agg(
        poids_ndvi_sum=("poids_ndvi", "sum"),
        n_pixels=("n_pixels", "sum"),
    )
    agg["ndvi"] = agg["poids_ndvi_sum"] / agg["n_pixels"]
    df_binned = agg[["id_parcel", "idx_grille", "ndvi", "n_pixels"]]

    df_valeurs = df_binned.pivot(index="id_parcel", columns="idx_grille", values="ndvi")
    df_poids = df_binned.pivot(
        index="id_parcel", columns="idx_grille", values="n_pixels"
    )

    df_valeurs = df_valeurs.reindex(
        index=id_parcels_ref, columns=range(len(jours_grille))
    )
    df_poids = df_poids.reindex(index=id_parcels_ref, columns=range(len(jours_grille)))

    X_valeurs = df_valeurs.to_numpy(dtype=np.float64)
    X_poids = np.nan_to_num(df_poids.to_numpy(dtype=np.float64), nan=0.0)
    X_valeurs = np.nan_to_num(X_valeurs, nan=0.0)  # sans effet là où poids = 0

    logger.info(
        "Grille : %d points, pas %d j, %s → %s. Collisions résolues par agrégation : %s",
        len(grille_dates),
        pas_jours,
        grille_dates.min().date(),
        grille_dates.max().date(),
        f"{len(df_ndvi_long) - len(df_binned):,}",
    )
    return {
        "grille_dates": grille_dates,
        "jours_grille": jours_grille,
        "X_valeurs": X_valeurs,
        "X_poids": X_poids,
        "date_min": date_min,
    }


def _build_d2_penalty_bands(n: int, lam: float) -> np.ndarray:
    """Bandes de la matrice de pénalité `D2'D2` (dérivée seconde), format
    bandé pour `solveh_banded` (portage début cellule 14).

    Structure de pénalité partagée par toutes les parcelles (dépend
    seulement de `n`/`lam`, pas des données) — calculée une seule fois par
    appel de `lisser_whittaker`, pas par parcelle.
    """
    D2 = diags([1, -2, 1], [0, 1, 2], shape=(n - 2, n), dtype=np.float64).toarray()
    P = lam * (D2.T @ D2)
    u = 2
    ab = np.zeros((u + 1, n))
    for i in range(n):
        for j in range(max(0, i - u), i + 1):
            ab[u + j - i, i] = P[j, i]
    ab[-1, :] += EPS_RIDGE
    return ab


def lisser_whittaker(
    X_valeurs: np.ndarray,
    X_poids: np.ndarray,
    lam: float = LAMBDA_WHITTAKER,
    n_min_obs: int = N_MIN_OBS,
) -> np.ndarray:
    """Lisseur de Whittaker, résolu parcelle par parcelle (système bandé
    pentadiagonal — portage cellule 14).

    `min_z  sum_i w_i (y_i - z_i)^2 + lam * sum (D2 z)^2` — moindres carrés
    pénalisés, pondérés par `n_pixels` (via `X_poids`).

    NaN pour les parcelles avec moins de `n_min_obs` observations pondérées
    (système mal posé — noyau de `D2'D2`). Retourne `X_smooth`, même forme
    que `X_valeurs`.
    """
    n = X_valeurs.shape[1]
    ab_D2 = _build_d2_penalty_bands(n, lam)

    X_smooth = np.full_like(X_valeurs, np.nan)
    for i in range(X_valeurs.shape[0]):
        w = X_poids[i]
        if (w > 0).sum() < n_min_obs:
            continue
        ab = ab_D2.copy()
        ab[-1, :] += w
        X_smooth[i] = solveh_banded(ab, w * X_valeurs[i], lower=False)

    n_non_calculable = int(np.isnan(X_smooth).all(axis=1).sum())
    logger.info(
        "Parcelles non calculables (< %d observations pondérées) : %s. NaN dans X_smooth : %s / %s",
        n_min_obs,
        f"{n_non_calculable:,}",
        f"{int(np.isnan(X_smooth).sum()):,}",
        f"{X_smooth.size:,}",
    )
    return X_smooth
