"""Contrôles qualité — portage de `03_series_s2.ipynb` §3.2 bis, §3.2 quater,
§3.4 bis, §3.6 bis.

Chaque fonction retourne des résultats structurés (jamais de `print`) —
utilisables à la fois comme porte QC pour une future tâche Airflow
(`qc_stats_zonales` dans le DAG indicatif de `methode.md`) et comme
fixtures de test d'intégration.

Pour chaque contrôle qui s'y prête, une fonction `generer_diagnostics_*`
correspondante produit un rapport HTML via `src.reporting.diagnostics` —
cf. décision actée sur la pertinence des diagnostics pour les sections QC,
pas seulement pour les étapes d'acquisition.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from src.db.connection import connexion
from src.processing.bands import BANDES, INDICES_NOMS
from src.processing.grid import reproject_to_aoi
from src.reporting.diagnostics import (
    ajouter_figure,
    ajouter_tableau,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)


# ── §3.2 bis — Complétude des fichiers bandes/indices ──────────────────────


def verifier_completude_fichiers(
    df_retenues: pd.DataFrame,
    bands_dir: Path,
    indices_dir: Path,
) -> list[dict]:
    """Vérifie, pour chaque scène retenue, la présence des 7 bandes + 4 indices,
    l'absence de fichiers entièrement nodata, et l'absence de zéros résiduels
    sur les bandes (portage §3.2 bis, cellule 13).

    Retourne une liste de problèmes structurés — chaque élément :
    `{"scene_id", "tile_id", "type", "detail"}`, `type` ∈
    `{"MANQUANT", "VIDE", "ZEROS_RESIDUELS"}`. Liste vide si tout est conforme —
    condition nécessaire avant d'autoriser `supprimer_jp2`.
    """
    bandes_attendues = [b for b, _, _ in BANDES]
    problemes: list[dict] = []

    for _, row in df_retenues.iterrows():
        scene_id, tile_id = row["scene_id"], row["tile_id"]
        band_dir = bands_dir / tile_id
        idx_dir = indices_dir / tile_id

        fichiers_attendus = [
            (band_dir / f"{scene_id}_{b}.tif", f"bande {b}") for b in bandes_attendues
        ] + [(idx_dir / f"{scene_id}_{i}.tif", f"indice {i}") for i in INDICES_NOMS]

        manquants = [label for path, label in fichiers_attendus if not path.exists()]
        if manquants:
            problemes.append(
                {
                    "scene_id": scene_id,
                    "tile_id": tile_id,
                    "type": "MANQUANT",
                    "detail": manquants,
                }
            )
            continue  # pas la peine de vérifier la validité si déjà incomplet

        vides: list[str] = []
        zeros_residuels: list[str] = []
        for path, label in fichiers_attendus:
            with rasterio.open(path) as src:
                arr = src.read(1)
                if np.all(arr == src.nodata):
                    vides.append(label)
                elif "indice" not in label:  # zéros résiduels : bandes uniquement
                    n_zero = int(np.sum(arr == 0))
                    if n_zero > 0:
                        zeros_residuels.append(f"{label} ({n_zero:,} px)")

        if vides:
            problemes.append(
                {
                    "scene_id": scene_id,
                    "tile_id": tile_id,
                    "type": "VIDE",
                    "detail": vides,
                }
            )
        if zeros_residuels:
            problemes.append(
                {
                    "scene_id": scene_id,
                    "tile_id": tile_id,
                    "type": "ZEROS_RESIDUELS",
                    "detail": zeros_residuels,
                }
            )

    if problemes:
        logger.warning(
            "%d problème(s) détecté(s) sur %d scènes vérifiées",
            len(problemes),
            len(df_retenues),
        )
    else:
        logger.info("%d scènes vérifiées, aucun problème détecté", len(df_retenues))

    return problemes


def generer_diagnostics_completude_fichiers(
    problemes: list[dict], n_scenes: int, nom_module: str = "processing_qc_fichiers"
) -> Path:
    """Rapport de diagnostics HTML pour `verifier_completude_fichiers`."""
    run_dir = nouveau_run_diagnostic(nom_module)

    if problemes:
        blocs = [ajouter_tableau(pd.DataFrame(problemes), "Problèmes détectés")]
    else:
        blocs = [
            ajouter_tableau(
                pd.DataFrame({"résultat": ["Aucun problème détecté"]}), "Résultat"
            )
        ]

    metriques = {"Scènes vérifiées": n_scenes, "Problèmes détectés": len(problemes)}
    return rendre_rapport_html(
        run_dir, "QC — Complétude bandes/indices", blocs, metriques
    )


def supprimer_jp2(bands_dir: Path) -> int:
    """Supprime tous les fichiers `.jp2` sous `bands_dir` — **destructif**.

    À n'appeler QUE depuis la future tâche Airflow dédiée
    (`nettoyage_intermediaires`), après que `verifier_completude_fichiers` a
    confirmé l'absence de problème — porte QC explicite, cf. `methode.md`
    (leçon S2 : suppression prématurée des intermédiaires avant détection
    du bug nodata, correction rétroactive rendue impossible). Portage de
    §3.2 bis, cellule 14 (désactivée par défaut dans le notebook).

    Retourne le nombre de fichiers supprimés.
    """
    jp2_files = list(bands_dir.rglob("*.jp2"))
    for f in jp2_files:
        f.unlink()
    logger.info("%d fichier(s) .jp2 supprimé(s) sous %s", len(jp2_files), bands_dir)
    return len(jp2_files)


# ── §3.2 quater — Couverture temporelle mensuelle ──────────────────────────


def _completude_a_jour(
    df_m: pd.DataFrame, out_path: Path, indices_dir: Path, variable_qc: str
) -> bool:
    """Vrai si le raster de complétude est plus récent que tous les indices sources du mois."""
    if not out_path.exists():
        return False
    mtime_out = out_path.stat().st_mtime
    for _, row in df_m.iterrows():
        src_path = indices_dir / row["tile_id"] / f"{row['scene_id']}_{variable_qc}.tif"
        if src_path.exists() and src_path.stat().st_mtime > mtime_out:
            return False
    return True


def calculer_couverture_temporelle(
    mois_complets: list[str],
    df_retenues: pd.DataFrame,
    indices_dir: Path,
    grille: dict,
    completude_dir: Path,
    variable_qc: str = "NDVI",
) -> pd.DataFrame:
    """Pour chaque mois, calcule le nombre de dates valides par pixel AOI et le
    pourcentage de pixels sans aucune date valide (portage §3.2 quater, cellule 20).

    Sauvegarde le raster de complétude par mois dans `completude_dir`
    (réutilisé en §3.5/`zonal.py`) — idempotent, un mois n'est recalculé
    que si un indice source est plus récent que le raster déjà présent.

    `variable_qc` : une seule variable suffit, le masque de validité
    (SCL + hors-fauchée) est identique pour toutes les variables d'une même
    scène (même raisonnement que dans le notebook).

    Retourne un DataFrame `[mois, n_dates_retenues, pct_pixels_0_date]`.
    """
    completude_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "dtype": "int16",
        "count": 1,
        "height": grille["height"],
        "width": grille["width"],
        "crs": grille["crs_wkt"],
        "transform": grille["transform"],
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        # pas de nodata : 0 est une valeur valide ("0 date"), pas une absence
    }

    resultats = []
    for mois in mois_complets:
        out_path = completude_dir / f"{mois}_n_valid.tif"
        df_m = df_retenues[df_retenues["mois"] == mois]
        scene_ids_par_date = df_m.groupby("date8")["scene_id"].apply(list).to_dict()
        n_dates_mois = len(scene_ids_par_date)

        if _completude_a_jour(df_m, out_path, indices_dir, variable_qc):
            with rasterio.open(out_path) as src:
                n_valid_mois = src.read(1)
            pct_zero = float(np.mean(n_valid_mois == 0) * 100)
            logger.info(
                "%s — SKIP (à jour, %d dates) — %.1f%% à 0 date",
                mois,
                n_dates_mois,
                pct_zero,
            )
            resultats.append(
                {
                    "mois": mois,
                    "n_dates_retenues": n_dates_mois,
                    "pct_pixels_0_date": round(pct_zero, 1),
                }
            )
            continue

        n_valid_mois = np.zeros((grille["height"], grille["width"]), dtype=np.int16)
        for date_str, scene_ids in scene_ids_par_date.items():
            tuile_arrays = []
            for scene_id in scene_ids:
                tile_id = scene_id.split("_")[5][1:]
                src_path = indices_dir / tile_id / f"{scene_id}_{variable_qc}.tif"
                if src_path.exists():
                    tuile_arrays.append(reproject_to_aoi(src_path, grille))
            if tuile_arrays:
                jour = (
                    tuile_arrays[0]
                    if len(tuile_arrays) == 1
                    else np.nanmedian(np.stack(tuile_arrays, axis=0), axis=0)
                )
                n_valid_mois += (~np.isnan(jour)).astype(np.int16)

        pct_zero = float(np.mean(n_valid_mois == 0) * 100)
        logger.info(
            "%s — %d dates traitées — %.1f%% pixels AOI sans date valide",
            mois,
            n_dates_mois,
            pct_zero,
        )
        resultats.append(
            {
                "mois": mois,
                "n_dates_retenues": n_dates_mois,
                "pct_pixels_0_date": round(pct_zero, 1),
            }
        )

        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(n_valid_mois.astype("int16"), 1)

    return pd.DataFrame(resultats)


def generer_diagnostics_couverture_temporelle(
    df_qc: pd.DataFrame,
    seuil_risque: float = 20,
    nom_module: str = "processing_qc_couverture",
) -> Path:
    """Histogramme + tableau de couverture temporelle mensuelle (portage §3.2 quater, cellule 21)."""
    import matplotlib.pyplot as plt

    run_dir = nouveau_run_diagnostic(nom_module)

    fig, ax = plt.subplots(figsize=(12, 5))
    couleurs = [
        "crimson" if p > seuil_risque else "steelblue"
        for p in df_qc["pct_pixels_0_date"]
    ]
    bars = ax.bar(df_qc["mois"], df_qc["pct_pixels_0_date"], color=couleurs)
    ax.axhline(
        seuil_risque,
        color="crimson",
        linestyle="--",
        linewidth=1,
        label=f"Seuil de risque ({seuil_risque} %)",
    )
    ax.set_ylabel("% pixels AOI sans date valide")
    ax.set_title("Couverture temporelle par mois — S2 composites")
    ax.set_xticklabels(df_qc["mois"], rotation=45, ha="right")
    for bar, n_dates in zip(bars, df_qc["n_dates_retenues"]):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1,
            f"{n_dates}d",
            ha="center",
            fontsize=8,
        )
    ax.legend()
    fig.tight_layout()

    bloc_fig = ajouter_figure(
        fig, "couverture_temporelle", "Couverture temporelle par mois", run_dir
    )
    plt.close(fig)
    bloc_table = ajouter_tableau(df_qc.set_index("mois"), "Détail par mois")

    n_a_risque = int((df_qc["pct_pixels_0_date"] > seuil_risque).sum())
    metriques = {
        "Mois traités": len(df_qc),
        f"Mois à risque (> {seuil_risque}%)": n_a_risque,
    }
    return rendre_rapport_html(
        run_dir,
        "QC — Couverture temporelle mensuelle",
        [bloc_fig, bloc_table],
        metriques,
    )


# ── §3.4 bis — Cohérence des stats zonales mensuelles ──────────────────────


def verifier_coherence_stats_mensuelles() -> dict:
    """Vérifie `derived.s2_parcelles_monthly` : nombre de variables/parcelles
    par mois, nombre de parcelles RPG absentes de la table (portage §3.4 bis,
    cellules 34-35).

    Retourne `{"par_mois": [{"mois", "n_variables", "n_parcelles"}, ...],
    "n_parcelles_absentes": int}`.
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mois, COUNT(DISTINCT variable) AS n_var, COUNT(DISTINCT id_parcel) AS n_parcelles
                FROM derived.s2_parcelles_monthly
                GROUP BY mois ORDER BY mois;
                """
            )
            par_mois = [
                {"mois": r[0], "n_variables": r[1], "n_parcelles": r[2]}
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT COUNT(DISTINCT id_parcel)
                FROM derived.rpg_parcelles_aoi
                WHERE id_parcel NOT IN (SELECT DISTINCT id_parcel FROM derived.s2_parcelles_monthly);
                """
            )
            n_absentes = cur.fetchone()[0]

    logger.info(
        "%d mois vérifiés, %d parcelle(s) RPG absente(s) de s2_parcelles_monthly",
        len(par_mois),
        n_absentes,
    )
    return {"par_mois": par_mois, "n_parcelles_absentes": n_absentes}


def generer_diagnostics_stats_mensuelles(
    resultats: dict, nom_module: str = "processing_qc_stats_mensuelles"
) -> Path:
    """Rapport de diagnostics HTML pour `verifier_coherence_stats_mensuelles`."""
    run_dir = nouveau_run_diagnostic(nom_module)

    df_par_mois = pd.DataFrame(resultats["par_mois"]).set_index("mois")
    blocs = [ajouter_tableau(df_par_mois, "Variables/parcelles par mois")]
    metriques = {
        "Mois en base": len(resultats["par_mois"]),
        "Parcelles RPG absentes": resultats["n_parcelles_absentes"],
    }
    return rendre_rapport_html(
        run_dir, "QC — Cohérence stats mensuelles", blocs, metriques
    )


# ── §3.6 bis — Cohérence NDVI aux dates d'acquisition ──────────────────────


def verifier_coherence_ndvi_dates() -> dict:
    """Vérifie `derived.s2_parcelles_ndvi_dates` : volumétrie, doublons sur la
    clé primaire `(id_parcel, date)` (portage §3.6 bis, cellule 44).

    Retourne un dict de métriques + `"doublons"` (liste, vide si aucun —
    condition attendue : la clé primaire de la table devrait déjà l'empêcher,
    ce contrôle vérifie qu'aucune insertion n'a contourné la contrainte).
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT id_parcel), COUNT(DISTINCT date),
                       MIN(n_pixels), MAX(n_pixels), AVG(n_pixels)
                FROM derived.s2_parcelles_ndvi_dates;
                """
            )
            n_lignes, n_parcelles, n_dates, min_px, max_px, moy_px = cur.fetchone()

            cur.execute(
                """
                SELECT id_parcel, date, COUNT(*)
                FROM derived.s2_parcelles_ndvi_dates
                GROUP BY id_parcel, date
                HAVING COUNT(*) > 1
                LIMIT 5;
                """
            )
            doublons = cur.fetchall()

    resultats = {
        "n_lignes": n_lignes,
        "n_parcelles": n_parcelles,
        "n_dates": n_dates,
        "n_pixels_min": min_px,
        "n_pixels_max": max_px,
        "n_pixels_moyenne": float(moy_px) if moy_px is not None else None,
        "doublons": doublons,
    }

    if doublons:
        logger.warning("%d doublon(s) détecté(s) sur (id_parcel, date)", len(doublons))
    else:
        logger.info(
            "Aucun doublon détecté sur (id_parcel, date) — %d lignes, %d parcelles",
            n_lignes,
            n_parcelles,
        )

    return resultats
