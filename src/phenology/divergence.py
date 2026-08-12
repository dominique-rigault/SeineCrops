"""Profils médians et scores de divergence — portage de
`05_divergence_pheno.ipynb` §5.2.

Les 704 features (dans la campagne actuelle) mélangent 7 bandes de
réflectance brute (échelle DN, ~0-10 000) et 4 indices bornés (~-1 à 1) —
cf. `methode.md`. Sans standardisation, une distance serait dominée par
les bandes brutes ; la **standardisation (z-score par feature) est donc
appliquée avant tout calcul de distance**, pas en option a posteriori.

Le chargement/pivot du feature set (§5.1 du notebook) n'est pas reporté
ici : quasi identique à `src.ml.features` (même `GROUP_MAP`, même
dédoublonnage RPG, même pivot long→wide) — réutilisé directement plutôt
que dupliqué (cf. `charger_feature_set_long`, `pivoter_features`,
`charger_et_regrouper_classes`, `joindre_classes`).

**Diagnostic raccord orbital (30UYV, orbites 51/94)** : constantes gardées
locales à ce module (pas dans `src/config.py`) — ce sont des
caractéristiques géométriques empiriques de cette campagne précise
(cf. `methode.md`, Limites documentées), pas des paramètres de campagne
génériques ; une future campagne (cycle orbital différent) pourrait ne
pas avoir ce même raccord au même endroit.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import wkt as shapely_wkt

from src.ml.split import charger_centroides
from src.reporting.diagnostics import (
    ajouter_figure,
    ajouter_tableau,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)

NON_FEATURE_COLS = {
    "id_parcel",
    "classe",
    "dist_classe",
    "pct_features_valides",
    "seuil_div",
    "divergent",
    "dist_raccord",
    "zone_raccord_orbital",
}

SEUIL_RACCORD = 2000  # m — largeur de la zone de transition observée (cf. methode.md)
ORBITES_RACCORD = (51, 94)
TUILE_RACCORD = "30UYV"


def standardiser_features(
    df: pd.DataFrame, feature_cols: list[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standardisation z-score manuelle (nanmean/nanstd — `StandardScaler`
    de sklearn refuse les NaN). Portage cellule 6 (première partie).

    Retourne `(X_scaled, mu, sigma)` — `mu`/`sigma` retournés pour
    traçabilité/réutilisation (ex. standardiser de nouvelles parcelles
    avec les mêmes paramètres).
    """
    X_raw = df[feature_cols].to_numpy(dtype=np.float64)
    mu = np.nanmean(X_raw, axis=0)
    sigma = np.nanstd(X_raw, axis=0)
    sigma[sigma == 0] = 1.0  # features constantes → éviter division par zéro
    X_scaled = (X_raw - mu) / sigma
    return X_scaled, mu, sigma


def calculer_profils_medians(
    X_scaled: np.ndarray, classes: np.ndarray, feature_cols: list[str]
) -> pd.DataFrame:
    """Profil médian par classe déclarée, sur features standardisées (portage cellule 6, suite)."""
    df_scaled = pd.DataFrame(X_scaled, columns=feature_cols)
    df_scaled["classe"] = classes
    medians = df_scaled.groupby("classe")[feature_cols].median()

    logger.info(
        "Profils médians : %d classes × %d features, %d NaN",
        medians.shape[0],
        medians.shape[1],
        int(medians.isna().sum().sum()),
    )
    return medians


def _rms_distance(X: np.ndarray, X_ref: np.ndarray) -> np.ndarray:
    """Distance RMS par feature valide (`nanmean`, NaN ignorés — invariante
    au nombre de mois observés)."""
    return np.sqrt(np.nanmean((X - X_ref) ** 2, axis=1))


def calculer_distance_rms(
    df: pd.DataFrame,
    X_scaled: np.ndarray,
    medians: pd.DataFrame,
    feature_cols: list[str],
) -> pd.DataFrame:
    """Distance RMS de chaque parcelle à son profil médian de classe (portage
    fin cellule 6). Ajoute `dist_classe`/`pct_features_valides` à `df` (copie).
    """
    df = df.copy()
    classes = df["classe"].values
    X_med = medians.loc[classes].to_numpy()
    df["dist_classe"] = _rms_distance(X_scaled, X_med)

    X_raw = df[feature_cols].to_numpy(dtype=np.float64)
    n_valid = (~np.isnan(X_raw)).sum(axis=1)
    df["pct_features_valides"] = 100 * n_valid / len(feature_cols)

    logger.info(
        "Couverture features — min %.1f%% / médiane %.1f%%",
        df["pct_features_valides"].min(),
        df["pct_features_valides"].median(),
    )
    return df


def calculer_seuils_divergence(
    df: pd.DataFrame, k: float = 2.0
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Seuil de divergence par classe : médiane + `k` × IQR intra-classe
    (portage cellule 7). Ajoute `seuil_div`/`divergent` à `df` (copie).

    Retourne `(df, stats_div)` — `stats_div` : DataFrame `[median, IQR,
    seuil, n_div, taux]` indexé par classe.
    """
    df = df.copy()

    def _stats(g: pd.Series) -> pd.Series:
        q1, q2, q3 = g.quantile([0.25, 0.50, 0.75])
        seuil = q2 + k * (q3 - q1)
        n_div = (g > seuil).sum()
        return pd.Series(
            {
                "median": q2,
                "IQR": q3 - q1,
                "seuil": seuil,
                "n_div": n_div,
                "taux": n_div / len(g),
            }
        )

    stats_div = df.groupby("classe")["dist_classe"].apply(_stats).unstack()

    df["seuil_div"] = df["classe"].map(stats_div["seuil"])
    df["divergent"] = df["dist_classe"] > df["seuil_div"]

    n_div = int(df["divergent"].sum())
    logger.info(
        "Parcelles divergentes : %s / %s (%.1f%%)\n%s",
        f"{n_div:,}",
        f"{len(df):,}",
        100 * n_div / len(df),
        stats_div[["median", "IQR", "seuil", "n_div", "taux"]]
        .sort_values("taux", ascending=False)
        .to_string(),
    )
    return df, stats_div


def generer_diagnostics_divergence_distribution(
    df: pd.DataFrame, stats_div: pd.DataFrame, nom_module: str = "phenology_divergence"
) -> Path:
    """Histogrammes de distribution des distances RMS par classe, avec seuil
    (portage cellule 7, visualisation). Une figure par classe, grille 2×4.
    """
    import matplotlib.pyplot as plt

    run_dir = nouveau_run_diagnostic(nom_module)
    classes = stats_div.sort_values("taux", ascending=False).index
    n_classes = len(classes)

    fig, axes = plt.subplots(2, 4, figsize=(16, 8), sharey=False)
    axes = axes.ravel()
    for i, cls in enumerate(classes):
        ax = axes[i]
        vals = df.loc[df["classe"] == cls, "dist_classe"]
        seuil = stats_div.loc[cls, "seuil"]
        taux = stats_div.loc[cls, "taux"]
        ax.hist(vals, bins=60, color="steelblue", edgecolor="none", alpha=0.8)
        ax.axvline(
            seuil,
            color="firebrick",
            ls="--",
            lw=1.5,
            label=f"med + k·IQR = {seuil:,.1f}",
        )
        ax.set_title(f"{cls}\n{taux:.1%} divergentes", fontsize=10)
        ax.legend(fontsize=7, loc="upper right")
        ax.set_xlabel("distance RMS (par feature)")
    for j in range(n_classes, len(axes)):
        axes[j].set_visible(False)
    fig.suptitle(
        "Distribution des distances RMS au profil médian de classe (RPG déclaré)",
        fontsize=12,
        y=1.01,
    )
    fig.tight_layout()

    bloc_fig = ajouter_figure(
        fig,
        "distribution_divergence",
        "Distribution des distances RMS par classe",
        run_dir,
    )
    plt.close(fig)
    bloc_table = ajouter_tableau(
        stats_div[["median", "IQR", "seuil", "n_div", "taux"]].sort_values(
            "taux", ascending=False
        ),
        "Détail par classe",
    )

    n_div = int(df["divergent"].sum())
    metriques = {
        "Parcelles divergentes": f"{n_div:,} / {len(df):,} ({100 * n_div / len(df):.1f}%)"
    }
    return rendre_rapport_html(
        run_dir,
        "Divergence — distribution par classe",
        [bloc_fig, bloc_table],
        metriques,
    )


def generer_diagnostics_divergence_spatiale(
    df: pd.DataFrame, nom_module: str = "phenology_divergence"
) -> Path:
    """Carte de répartition spatiale des parcelles divergentes (portage cellule 8).

    `df` doit avoir `id_parcel` en **colonne** (pas en index) — cf.
    `scripts/run_phenology.py::preparer_feature_set`, qui normalise ce point
    une seule fois en amont plutôt que chaque fonction ne le vérifie.

    Charge les centroïdes via `src.ml.split.charger_centroides` (réutilisé,
    pas dupliqué).
    """
    import matplotlib.pyplot as plt

    run_dir = nouveau_run_diagnostic(nom_module)
    df_geo = charger_centroides(df["id_parcel"])
    df_diag = df[["id_parcel", "classe", "dist_classe", "divergent"]].merge(
        df_geo, on="id_parcel"
    )

    fig, ax = plt.subplots(figsize=(10, 9))
    for is_div, color, label, z in [
        (False, "lightgray", "conforme", 1),
        (True, "firebrick", "divergente", 2),
    ]:
        sub = df_diag[df_diag["divergent"] == is_div]
        ax.scatter(
            sub["cx"],
            sub["cy"],
            s=1.5,
            alpha=0.5,
            color=color,
            label=f"{label} ({len(sub):,})",
            zorder=z,
        )
    ax.set_xlabel("X (Lambert-93, m)")
    ax.set_ylabel("Y (Lambert-93, m)")
    ax.set_aspect("equal")
    ax.legend(markerscale=12, loc="upper left")
    ax.set_title("Répartition spatiale des parcelles divergentes")
    fig.tight_layout()

    bloc_fig = ajouter_figure(
        fig, "repartition_spatiale_divergence", "Répartition spatiale", run_dir
    )
    plt.close(fig)

    n_div = int(df_diag["divergent"].sum())
    metriques = {
        "Divergentes": f"{n_div:,} / {len(df_diag):,} ({100 * n_div / len(df_diag):.1f}%)"
    }
    return rendre_rapport_html(
        run_dir, "Divergence — répartition spatiale", [bloc_fig], metriques
    )


def _get_footprint_wkt(product_id: str) -> str:
    """Empreinte (WKT) d'une scène via l'API CDSE OData (endpoint public,
    sans authentification). Portage cellule 10.
    """
    url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})?$select=Footprint"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    raw = r.json()["Footprint"]
    return raw.split(";", 1)[1].rstrip("'")


def calculer_flag_raccord_orbital(
    df: pd.DataFrame,
    catalogue_path: Path,
    seuil_raccord: float = SEUIL_RACCORD,
    orbites: tuple[int, int] = ORBITES_RACCORD,
    tile_id: str = TUILE_RACCORD,
) -> pd.DataFrame:
    """Flag `zone_raccord_orbital` : parcelles proches du raccord entre deux
    orbites relatives sur une tuile donnée (portage cellule 10).

    `df` doit avoir `id_parcel` en **colonne** (pas en index) — même
    précondition que `generer_diagnostics_divergence_spatiale`.

    Nécessite un accès réseau (empreintes des scènes via l'API CDSE) — 2
    appels seulement (une scène par orbite), léger comparé à
    `bands.py`/`scl.py`, mais reste une dépendance réseau à chaque run.
    **Obligatoire mais skippable** : cette fonction ne gère pas elle-même
    l'échec réseau — c'est à l'appelant (`scripts/run_phenology.py`) de
    décider de continuer sans le flag (avec avertissement explicite) plutôt
    que de faire échouer tout le run pour un appel non critique au sens
    strict (dégrade l'interprétation en aval, ne bloque pas le calcul).

    Ajoute `dist_raccord`/`zone_raccord_orbital` à `df` (copie).
    """
    catalogue = pd.read_parquet(catalogue_path)
    scenes_raccord = (
        catalogue[
            (catalogue["tile_id"] == tile_id)
            & (catalogue["orbit_relative"].isin(orbites))
        ]
        .sort_values("f_valid_aoi", ascending=False)
        .groupby("orbit_relative")
        .first()
        .reset_index()
    )
    scenes_raccord["footprint_wkt"] = scenes_raccord["product_id"].apply(
        _get_footprint_wkt
    )
    gdf_raccord = gpd.GeoDataFrame(
        scenes_raccord,
        geometry=[shapely_wkt.loads(w) for w in scenes_raccord["footprint_wkt"]],
        crs="EPSG:4326",
    ).to_crs("EPSG:2154")

    df_geo = charger_centroides(df["id_parcel"])
    points = gpd.GeoSeries(
        gpd.points_from_xy(df_geo["cx"], df_geo["cy"]), crs="EPSG:2154"
    )
    boundaries = [
        gdf_raccord.loc[gdf_raccord["orbit_relative"] == o, "geometry"].iloc[0].boundary
        for o in orbites
    ]
    df_geo["dist_raccord"] = np.column_stack(
        [points.distance(b) for b in boundaries]
    ).min(axis=1)

    df = df.copy()
    df = df.drop(columns=["dist_raccord", "zone_raccord_orbital"], errors="ignore")
    df = df.merge(df_geo[["id_parcel", "dist_raccord"]], on="id_parcel", how="left")
    df["zone_raccord_orbital"] = df["dist_raccord"] < seuil_raccord

    n_flag = int(df["zone_raccord_orbital"].sum())
    n_flag_div = (
        int((df["zone_raccord_orbital"] & df["divergent"]).sum())
        if "divergent" in df.columns
        else None
    )
    logger.info(
        "Parcelles proches d'un raccord orbital (< %d m) : %s (%.1f%%)%s",
        seuil_raccord,
        f"{n_flag:,}",
        100 * n_flag / len(df),
        f", dont {n_flag_div:,} divergentes" if n_flag_div is not None else "",
    )
    return df


def generer_diagnostics_synthese(
    df: pd.DataFrame,
    df_pheno: pd.DataFrame,
    fenetres: dict,
    nom_module: str = "phenology_synthese",
) -> Path:
    """Croisement divergence (§5.2) × phénologie (§5.3) : une parcelle
    divergente a-t-elle aussi une durée de saison (LOS) atypique par
    rapport à sa classe ? Portage cellule 23.

    `df` doit avoir `id_parcel` en **colonne** (pas en index) — même
    précondition que `generer_diagnostics_divergence_spatiale`.

    Pas garanti — la divergence porte sur la forme globale du profil
    (704 features), pas spécifiquement sur SOS/POS/EOS — mais une
    corrélation, si elle existe, renforcerait la crédibilité des deux
    signaux indépendamment obtenus. Restreint aux classes avec fenêtre
    calendaire (`fenetres`) et aux parcelles phénologiquement fiables.
    """
    import matplotlib.pyplot as plt

    run_dir = nouveau_run_diagnostic(nom_module)
    df_synth = df[["id_parcel", "classe", "dist_classe", "divergent"]].merge(
        df_pheno[["id_parcel", "los_jours", "fiable"]], on="id_parcel"
    )
    df_synth = df_synth[df_synth["fiable"]]

    classes_avec_fenetre = [c for c in df["classe"].unique() if c in fenetres]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharey=False)
    axes = axes.ravel()
    for i, cls in enumerate(classes_avec_fenetre):
        if i >= len(axes):
            break
        ax = axes[i]
        sub = df_synth[df_synth["classe"] == cls]
        for is_div, color, label in [
            (False, "lightgray", "conforme"),
            (True, "firebrick", "divergente"),
        ]:
            pts = sub[sub["divergent"] == is_div]
            ax.scatter(
                pts["dist_classe"],
                pts["los_jours"],
                s=6,
                alpha=0.4,
                color=color,
                label=f"{label} ({len(pts):,})",
            )
        ax.set_title(cls, fontsize=10)
        ax.set_xlabel("distance RMS (standardisée)")
        ax.set_ylabel("LOS (jours)")
        ax.legend(fontsize=7)
    for j in range(len(classes_avec_fenetre), len(axes)):
        axes[j].set_visible(False)
    fig.suptitle(
        "Divergence vs longueur de saison — parcelles fiables uniquement", y=1.01
    )
    fig.tight_layout()

    bloc_fig = ajouter_figure(
        fig, "divergence_vs_los", "Divergence vs longueur de saison", run_dir
    )
    plt.close(fig)

    stats = df_synth.groupby("divergent")["los_jours"].describe()[
        ["mean", "std", "50%"]
    ]
    bloc_table = ajouter_tableau(stats, "LOS par statut de divergence")

    metriques = {"Parcelles (fiables)": f"{len(df_synth):,}"}
    return rendre_rapport_html(
        run_dir,
        "Synthèse — divergence vs phénologie",
        [bloc_fig, bloc_table],
        metriques,
    )
