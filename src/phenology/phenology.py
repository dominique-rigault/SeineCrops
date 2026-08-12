"""Extraction phénologique (SOS/POS/EOS/LOS) — portage de
`05_divergence_pheno.ipynb` §5.3 (partie extraction).

Convention HR-VPP/TIMESAT : seuils à 20 % de l'amplitude saisonnière ; SOS
sur la branche montante, EOS sur la branche descendante, POS = date du
maximum. Minimum local de chaque côté du pic (pas un minimum global
partagé) : le creux pré-saison (interculture) et le creux post-récolte
(sol nu) sont deux phénomènes différents, à des niveaux différents — un
minimum global biaiserait le seuil de l'un via le creux de l'autre.

`FENETRES_PHENOLOGIE` restreint la RECHERCHE du maximum (pas le lissage,
calculé sur tout le signal disponible) à une fenêtre calendaire par
classe — la fenêtre de 16 mois déborde volontairement avant la campagne
pour les besoins de la classification, mais pour les cultures de
printemps/été, le début de fenêtre correspond encore à la culture
précédente ou à un couvert intermédiaire (CIPAN), pas à la culture
déclarée. Calendrier Normandie approximatif, à affiner — constante locale
au module, pas dans `src/config.py` (décision de modélisation liée à
cette logique d'extraction précise, comme `GROUP_MAP`/`LAMBDA_WHITTAKER`).

`id_parcels`/`classes` sont passés comme tableaux explicites alignés avec
les lignes de `X_smooth`, plutôt qu'un DataFrame indexé — `X_smooth`
(construit par `whittaker.construire_grille_et_binning`) n'a pas de
notion de nom de colonne/index, seulement un ordre de lignes ; imposer un
DataFrame ambigu (id_parcel en index ou en colonne selon la source
appelante) aurait été une source d'erreur silencieuse.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.reporting.diagnostics import (
    ajouter_figure,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)

FENETRES_PHENOLOGIE = {
    "cereales_hiver": (40, 400),
    "colza": (5, 380),  # semis fin août N → récolte mi-sept N+1
    "mais": (230, 470),  # semis avril-mai N+1 → récolte sept-oct N+1
    "betterave": (
        170,
        475,
    ),  # semis mars-avril N+1 → récolte oct-déc N+1 (~fin de grille)
    "lin": (180, 380),
    "legumes_fleurs": (200, 450),
    # prairie / autres : pas de fenêtre — couvert pérenne ou classe hétérogène ;
    # pos_en_bord/sos_en_bord/eos_en_bord restent le signal.
}
FRAC_SOS = 0.20
FRAC_EOS = 0.20


def extraire_phenologie(
    y: np.ndarray, jours: np.ndarray, fenetre: tuple[int, int] | None = None
) -> tuple[float, float, float, float, bool, bool, bool]:
    """SOS/POS/EOS/LOS pour une parcelle, par seuils d'amplitude (portage
    cellule 15, fonction `extraire_phenologie`).

    Si le vrai creux pré-saison (ou post-récolte) tombe hors fenêtre, le
    minimum local observable devient le bord de fenêtre lui-même —
    `sos_en_bord`/`eos_en_bord`/`pos_en_bord` détectent explicitement ce
    cas (test d'indice, pas de seuil, pour éviter la tautologie avec un
    minimum local) plutôt que de chasser une fenêtre parfaite.

    Retourne `(sos, pos, eos, los, pos_en_bord, sos_en_bord, eos_en_bord)`
    — tout `NaN`/`False` si aucun signal exploitable dans la fenêtre.
    """
    y_recherche = y.copy()
    if fenetre is not None:
        jmin, jmax = fenetre
        hors_fenetre = (jours < jmin) | (jours > jmax)
        y_recherche = np.where(hors_fenetre, np.nan, y)

    if np.isnan(y_recherche).all():
        return np.nan, np.nan, np.nan, np.nan, False, False, False

    i_max = np.nanargmax(y_recherche)
    y_max = y_recherche[i_max]

    y_min_avant = np.nanmin(y_recherche[: i_max + 1])
    y_min_apres = np.nanmin(y_recherche[i_max:])
    amplitude_avant = y_max - y_min_avant
    amplitude_apres = y_max - y_min_apres

    if amplitude_avant <= 0 and amplitude_apres <= 0:
        return np.nan, np.nan, np.nan, np.nan, False, False, False

    seuil_sos = (
        y_min_avant + FRAC_SOS * amplitude_avant if amplitude_avant > 0 else y_max
    )
    seuil_eos = (
        y_min_apres + FRAC_EOS * amplitude_apres if amplitude_apres > 0 else y_max
    )

    fenetre_valide = ~np.isnan(y_recherche)
    idx_valides = np.where(fenetre_valide)[0]

    avant_pic = np.where(
        fenetre_valide[: i_max + 1] & (y_recherche[: i_max + 1] <= seuil_sos)
    )[0]
    idx_sos = avant_pic[-1] if len(avant_pic) else idx_valides[0]
    sos_en_bord = idx_sos <= idx_valides[0] + 1  # SOS retombé sur le bord gauche

    apres_pic = np.where(fenetre_valide[i_max:] & (y_recherche[i_max:] <= seuil_eos))[0]
    idx_eos = i_max + apres_pic[0] if len(apres_pic) else idx_valides[-1]
    eos_en_bord = idx_eos >= idx_valides[-1] - 1  # EOS retombé sur le bord droit

    pos_en_bord = i_max <= idx_valides[0] + 1 or i_max >= idx_valides[-1] - 1

    sos, eos, pos = jours[idx_sos], jours[idx_eos], jours[i_max]
    return sos, pos, eos, eos - sos, pos_en_bord, sos_en_bord, eos_en_bord


def extraire_phenologie_toutes_parcelles(
    X_smooth: np.ndarray,
    jours_grille: np.ndarray,
    id_parcels: np.ndarray,
    classes: np.ndarray,
    date_min: pd.Timestamp,
    fenetres: dict = FENETRES_PHENOLOGIE,
) -> pd.DataFrame:
    """Boucle `extraire_phenologie` sur toutes les parcelles (portage fin
    cellule 15). `id_parcels`/`classes` doivent être alignés avec les
    lignes de `X_smooth` (même ordre que celui utilisé pour le construire).

    Retourne un DataFrame `[id_parcel, classe, sos_jour, pos_jour,
    eos_jour, los_jours, sos_date, pos_date, eos_date, *_en_bord, fiable]`.
    """
    resultats = []
    for i in range(X_smooth.shape[0]):
        fenetre = fenetres.get(classes[i])
        resultats.append(extraire_phenologie(X_smooth[i], jours_grille, fenetre))

    df_pheno = pd.DataFrame(
        resultats,
        columns=[
            "sos_jour",
            "pos_jour",
            "eos_jour",
            "los_jours",
            "pos_en_bord",
            "sos_en_bord",
            "eos_en_bord",
        ],
    )
    df_pheno.insert(0, "id_parcel", np.asarray(id_parcels))
    df_pheno.insert(1, "classe", np.asarray(classes))

    for col, out in [
        ("sos_jour", "sos_date"),
        ("pos_jour", "pos_date"),
        ("eos_jour", "eos_date"),
    ]:
        df_pheno[out] = df_pheno[col].apply(
            lambda j: date_min + pd.Timedelta(days=j) if pd.notna(j) else pd.NaT
        )

    df_pheno["fiable"] = ~(
        df_pheno["pos_en_bord"] | df_pheno["sos_en_bord"] | df_pheno["eos_en_bord"]
    )

    n_nan = int(df_pheno["sos_jour"].isna().sum())
    logger.info(
        "Phénologie extraite : %s / %s parcelles\n%s",
        f"{len(df_pheno) - n_nan:,}",
        f"{len(df_pheno):,}",
        df_pheno.groupby("classe")[["sos_date", "pos_date", "eos_date", "los_jours"]]
        .agg(
            {
                "sos_date": "median",
                "pos_date": "median",
                "eos_date": "median",
                "los_jours": "median",
            }
        )
        .to_string(),
    )
    logger.info(
        "Fiabilité par classe :\n%s",
        df_pheno.groupby("classe")[
            ["sos_en_bord", "eos_en_bord", "pos_en_bord", "fiable"]
        ]
        .mean()
        .to_string(
            formatters={
                c: "{:.1%}".format
                for c in ["sos_en_bord", "eos_en_bord", "pos_en_bord", "fiable"]
            }
        ),
    )
    return df_pheno


def generer_diagnostics_phenologie(
    id_parcels: np.ndarray,
    classes: np.ndarray,
    X_smooth: np.ndarray,
    jours_grille: np.ndarray,
    df_pheno: pd.DataFrame,
    df_ndvi_long: pd.DataFrame,
    date_min: pd.Timestamp,
    seed: int = 42,
    nom_module: str = "phenology_extraction",
) -> Path:
    """Validation visuelle : profil NDVI brut, lissé, repères phénologiques,
    un exemple par classe (portage cellule 16).

    `date_min` : nécessaire pour convertir `df_ndvi_long["date"]` en jours
    depuis l'origine de la grille (même échelle que `jours_grille`) — cette
    conversion se fait ici, sur une copie, plutôt que de dépendre d'une
    colonne `jour` calculée ailleurs (`whittaker.construire_grille_et_binning`
    la calcule sur sa propre copie interne, jamais propagée à l'appelant).
    """
    import matplotlib.pyplot as plt

    run_dir = nouveau_run_diagnostic(nom_module)
    rng = np.random.default_rng(seed)
    index_parcels = pd.Index(id_parcels)
    classes_triees = sorted(pd.unique(classes))

    df_ndvi_long = df_ndvi_long.copy()
    df_ndvi_long["jour"] = (df_ndvi_long["date"] - date_min).dt.days

    fig, axes = plt.subplots(2, 4, figsize=(18, 8), sharex=True)
    axes = axes.ravel()
    for i, cls in enumerate(classes_triees):
        if i >= len(axes):
            break
        ax = axes[i]
        candidats = df_pheno.loc[
            (df_pheno["classe"] == cls) & df_pheno["sos_jour"].notna(), "id_parcel"
        ]
        if candidats.empty:
            ax.set_visible(False)
            continue

        id_exemple = rng.choice(candidats.values)
        idx = index_parcels.get_loc(id_exemple)

        obs = df_ndvi_long[df_ndvi_long["id_parcel"] == id_exemple]
        ax.scatter(
            obs["jour"], obs["ndvi"], s=10, alpha=0.4, color="gray", label="NDVI brut"
        )
        ax.plot(
            jours_grille,
            X_smooth[idx],
            color="darkgreen",
            lw=1.5,
            label="lissé (Whittaker)",
        )

        row = df_pheno[df_pheno["id_parcel"] == id_exemple].iloc[0]
        for jour, label, color in [
            (row["sos_jour"], "SOS", "steelblue"),
            (row["pos_jour"], "POS", "firebrick"),
            (row["eos_jour"], "EOS", "darkorange"),
        ]:
            ax.axvline(jour, color=color, ls="--", lw=1, label=label)

        ax.set_title(f"{cls}", fontsize=9)
        ax.legend(fontsize=6, loc="lower center")

    for j in range(len(classes_triees), len(axes)):
        axes[j].set_visible(False)

    fig.suptitle(
        "Profils NDVI, lissage et repères phénologiques — un exemple par classe", y=1.02
    )
    fig.tight_layout()

    bloc_fig = ajouter_figure(
        fig, "profils_phenologiques", "Profils phénologiques par classe", run_dir
    )
    plt.close(fig)

    n_fiable = int(df_pheno["fiable"].sum())
    metriques = {
        "Parcelles fiables": f"{n_fiable:,} / {len(df_pheno):,} ({100 * n_fiable / len(df_pheno):.1f}%)"
    }
    return rendre_rapport_html(
        run_dir, "Phénologie — profils par classe", [bloc_fig], metriques
    )
