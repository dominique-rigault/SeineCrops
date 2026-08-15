"""Composite mensuel — portage de `03_series_s2.ipynb` §3.3.

Construction en deux étapes, pour chaque bande/indice (11 variables), sur
la grille AOI et la liste des mois complets (`§3.2 ter` / `qc.py`) :

1. **Image journalière** — médiane pixel à pixel des tuiles disponibles ce jour.
2. **Composite mensuel** — médiane pixel à pixel des images journalières valides.

La médiane est robuste aux nuages résiduels non détectés par la SCL et aux
outliers radiométriques ponctuels. Un pixel sans aucune acquisition valide
dans le mois reçoit la valeur nodata (`-9999`).

**Marqueur de version + écriture atomique** (cf. `methode.md`, conception
du DAG) : chaque composite embarque `COMPOSITES_VERSION` en tag GeoTIFF,
et sa fraîcheur est vérifiée par rapport à ses fichiers sources (pas
seulement son existence) — nécessaire pour qu'une scène republiée par
Copernicus (nouvelle baseline) invalide correctement le composite mensuel
qui en dépend, pas seulement la bande/l'indice individuel. `COMPOSITES_VERSION`
est une constante distincte de `SCL_VERSION`/`BANDS_VERSION` (versions par
étage, pas une seule version globale — une bump ici n'invalide jamais les
sorties d'un autre module). L'écriture passe par un fichier temporaire
puis un renommage atomique : `out_path` n'existe jamais sous son nom final
tant que l'écriture n'est pas intégralement terminée, un crash en cours
d'écriture laisse un `.tmp.tif` orphelin plutôt qu'un fichier corrompu
mais avec un tag de version qui semblerait à jour.
"""

from __future__ import annotations

import gc
import logging
import os
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from src.processing.grid import reproject_to_aoi

logger = logging.getLogger(__name__)

VARIABLES = [
    "B02",
    "B04",
    "B05",
    "B06",
    "B07",
    "B08",
    "B11",
    "NDVI",
    "EVI",
    "NDWI",
    "NDRE",
]
COMPOSITES_VERSION = (
    "1.0"  # bump uniquement si compute_monthly_composite change (logique de calcul)
)


def _composite_a_jour(
    out_path: Path,
    scene_ids_par_date: dict,
    src_dir: Path,
    variable: str,
    composites_version: str,
) -> bool:
    """Un composite est à jour s'il existe, porte le tag de version courant,
    ET est plus récent que tous ses fichiers sources (bandes/indices).

    Les deux conditions sont nécessaires et complémentaires : le tag de
    version détecte un changement de *code* (`compute_monthly_composite`
    modifié) ; la comparaison de date détecte un changement de *données*
    à code inchangé (ex. une scène republiée par Copernicus avec une
    nouvelle baseline, dont l'indice source a été retraité).
    """
    if not out_path.exists():
        return False
    try:
        with rasterio.open(out_path) as dst:
            tag_version = dst.tags().get("composites_version")
    except Exception:
        return False  # fichier illisible/corrompu — pas à jour, sera régénéré
    if tag_version != composites_version:
        return False

    mtime_out = out_path.stat().st_mtime
    for scene_ids in scene_ids_par_date.values():
        for scene_id in scene_ids:
            tile_id = scene_id.split("_")[5][1:]
            src_path = src_dir / tile_id / f"{scene_id}_{variable}.tif"
            if src_path.exists() and src_path.stat().st_mtime > mtime_out:
                return False
    return True


def compute_monthly_composite(
    mois: str,
    scene_ids_par_date: dict,
    variable: str,
    grille: dict,
    bands_dir: Path,
    indices_dir: Path,
    composites_dir: Path,
    composites_version: str = COMPOSITES_VERSION,
) -> tuple[str, str, str]:
    """Calcule le composite mensuel AOI pour un mois × variable (portage §3.3, cellule 24).

    `scene_ids_par_date` : `{date_str: [scene_id, ...]}` pour ce mois.
    Retourne `(mois, variable, "OK"|"SKIP"|"VIDE"|"ERR <msg>")` — idempotent,
    mais `SKIP` seulement si le composite est réellement à jour (`_composite_a_jour`),
    pas seulement présent (cf. docstring de module).
    """
    out_dir = composites_dir / mois
    out_path = out_dir / f"{variable}.tif"
    src_dir = indices_dir if variable in ("NDVI", "EVI", "NDWI", "NDRE") else bands_dir

    if _composite_a_jour(
        out_path, scene_ids_par_date, src_dir, variable, composites_version
    ):
        return (mois, variable, "SKIP")

    # Chronométrage par étape — pour distinguer I/O disque (reprojection, lecture des
    # fichiers sources) de calcul CPU pur (médianes), diagnostic ajouté suite à un
    # ralentissement observé (CPU ~22%, disque/réseau ~0% pendant le run).
    t_reproject = 0.0
    t_daily_median = 0.0
    n_fichiers_lus = 0

    try:
        n_dates = len(scene_ids_par_date)
        # Pré-alloué plutôt qu'accumulé en liste + np.stack : évite une copie
        # complète supplémentaire en mémoire à l'étape 2 (jusqu'à ~3 Go de pic
        # transitoire sur les mois à 14 dates, grille 4824×5448 en float32) —
        # détecté via pression mémoire/swap observée sur un run réel.
        daily_stack = np.full(
            (n_dates, grille["height"], grille["width"]), np.nan, dtype=np.float32
        )
        n_dates_valides = 0

        # Étape 1 — image journalière par date, écrite directement dans daily_stack
        for date_str, scene_ids in scene_ids_par_date.items():
            tuile_arrays = []
            for scene_id in scene_ids:
                tile_id = scene_id.split("_")[5][1:]
                src_path = src_dir / tile_id / f"{scene_id}_{variable}.tif"
                if src_path.exists():
                    t0 = time.perf_counter()
                    tuile_arrays.append(reproject_to_aoi(src_path, grille))
                    t_reproject += time.perf_counter() - t0
                    n_fichiers_lus += 1

            if tuile_arrays:
                if len(tuile_arrays) == 1:
                    daily_stack[n_dates_valides] = tuile_arrays[0]
                else:
                    # all-NaN attendu en bord d'AOI / entre tuiles sans recouvrement —
                    # résultat NaN correct, cf. qc.py pour le détail du correctif (np.errstate
                    # ne suffit pas, il faut warnings.simplefilter).
                    t0 = time.perf_counter()
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", category=RuntimeWarning)
                        daily_stack[n_dates_valides] = np.nanmedian(
                            np.stack(tuile_arrays, axis=0), axis=0
                        )
                    t_daily_median += time.perf_counter() - t0
                n_dates_valides += 1
            del tuile_arrays

        if n_dates_valides == 0:
            return (mois, variable, "VIDE")

        # Étape 2 — composite mensuel, sur les seules lignes effectivement remplies
        # all-NaN attendu pour un pixel jamais valide sur tout le mois — devient -9999
        # (nodata) juste après, résultat correct.
        t0 = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            composite = np.nanmedian(daily_stack[:n_dates_valides], axis=0).astype(
                np.float32
            )
        t_monthly_median = time.perf_counter() - t0
        composite[np.isnan(composite)] = -9999.0
        del daily_stack
        gc.collect()

        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".tmp.tif")
        t0 = time.perf_counter()
        with rasterio.open(
            tmp_path,
            "w",
            driver="GTiff",
            height=grille["height"],
            width=grille["width"],
            count=1,
            dtype="float32",
            crs=grille["crs_wkt"],
            transform=grille["transform"],
            nodata=-9999.0,
            compress="deflate",
            tiled=True,
            blockxsize=256,
            blockysize=256,
        ) as dst:
            dst.write(composite, 1)
            dst.update_tags(composites_version=composites_version)
        os.replace(
            tmp_path, out_path
        )  # atomique — out_path n'existe jamais à moitié écrit
        t_write = time.perf_counter() - t0

        logger.info(
            "%s/%s — reprojection %.1fs (%d fichiers, %.3fs/fichier), médiane journ. %.1fs, "
            "médiane mens. %.1fs, écriture %.1fs",
            mois,
            variable,
            t_reproject,
            n_fichiers_lus,
            t_reproject / n_fichiers_lus if n_fichiers_lus else 0.0,
            t_daily_median,
            t_monthly_median,
            t_write,
        )

        del composite
        gc.collect()
        return (mois, variable, "OK")

    except Exception as e:
        return (mois, variable, f"ERR {e}")


def construire_composites_mensuels(
    mois_complets: list[str],
    df_retenues: pd.DataFrame,
    grille: dict,
    bands_dir: Path,
    indices_dir: Path,
    composites_dir: Path,
    variables: list[str] = VARIABLES,
    composites_version: str = COMPOSITES_VERSION,
) -> dict:
    """Boucle principale mois × variable (portage §3.3, cellule 25).

    Retourne `{"n_ok", "n_skip", "n_vide", "n_err", "erreurs": [(mois, variable, msg)]}`.
    """
    total = len(mois_complets) * len(variables)
    n_ok = n_skip = n_vide = n_err = 0
    erreurs: list[tuple[str, str, str]] = []
    done = 0

    logger.info(
        "Composites à produire : %d (%d mois × %d variables)",
        total,
        len(mois_complets),
        len(variables),
    )

    for i_mois, mois in enumerate(mois_complets, 1):
        df_mois = df_retenues[df_retenues["mois"] == mois]
        scene_ids_par_date = df_mois.groupby("date8")["scene_id"].apply(list).to_dict()

        for variable in variables:
            _, _, status = compute_monthly_composite(
                mois,
                scene_ids_par_date,
                variable,
                grille,
                bands_dir,
                indices_dir,
                composites_dir,
                composites_version=composites_version,
            )
            if status == "OK":
                n_ok += 1
            elif status == "SKIP":
                n_skip += 1
            elif status == "VIDE":
                n_vide += 1
            else:
                n_err += 1
                erreurs.append((mois, variable, status))
            done += 1

        logger.info(
            "[%d/%d] mois %s traité (%d/%d composites cumulés)",
            i_mois,
            len(mois_complets),
            mois,
            done,
            total,
        )
        gc.collect()

    logger.info("Terminé. OK:%d SKIP:%d VIDE:%d ERR:%d", n_ok, n_skip, n_vide, n_err)
    if erreurs:
        for mois, variable, msg in erreurs:
            logger.warning("%s/%s — %s", mois, variable, msg)

    return {
        "n_ok": n_ok,
        "n_skip": n_skip,
        "n_vide": n_vide,
        "n_err": n_err,
        "erreurs": erreurs,
    }
