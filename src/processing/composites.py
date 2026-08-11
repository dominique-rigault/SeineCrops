"""Composite mensuel — portage de `03_series_s2.ipynb` §3.3.

Construction en deux étapes, pour chaque bande/indice (11 variables), sur
la grille AOI et la liste des mois complets (`§3.2 ter` / `qc.py`) :

1. **Image journalière** — médiane pixel à pixel des tuiles disponibles ce jour.
2. **Composite mensuel** — médiane pixel à pixel des images journalières valides.

La médiane est robuste aux nuages résiduels non détectés par la SCL et aux
outliers radiométriques ponctuels. Un pixel sans aucune acquisition valide
dans le mois reçoit la valeur nodata (`-9999`).
"""

from __future__ import annotations

import gc
import logging
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


def compute_monthly_composite(
    mois: str,
    scene_ids_par_date: dict,
    variable: str,
    grille: dict,
    bands_dir: Path,
    indices_dir: Path,
    composites_dir: Path,
) -> tuple[str, str, str]:
    """Calcule le composite mensuel AOI pour un mois × variable (portage §3.3, cellule 24).

    `scene_ids_par_date` : `{date_str: [scene_id, ...]}` pour ce mois.
    Retourne `(mois, variable, "OK"|"SKIP"|"VIDE"|"ERR <msg>")` — idempotent
    (`SKIP` si le composite existe déjà).
    """
    out_dir = composites_dir / mois
    out_path = out_dir / f"{variable}.tif"
    if out_path.exists():
        return (mois, variable, "SKIP")

    src_dir = indices_dir if variable in ("NDVI", "EVI", "NDWI", "NDRE") else bands_dir

    try:
        daily_images = []

        # Étape 1 — image journalière par date
        for date_str, scene_ids in scene_ids_par_date.items():
            tuile_arrays = []
            for scene_id in scene_ids:
                tile_id = scene_id.split("_")[5][1:]
                src_path = src_dir / tile_id / f"{scene_id}_{variable}.tif"
                if src_path.exists():
                    tuile_arrays.append(reproject_to_aoi(src_path, grille))

            if tuile_arrays:
                if len(tuile_arrays) == 1:
                    daily_images.append(tuile_arrays[0])
                else:
                    daily_images.append(
                        np.nanmedian(np.stack(tuile_arrays, axis=0), axis=0)
                    )
            del tuile_arrays

        if not daily_images:
            return (mois, variable, "VIDE")

        # Étape 2 — composite mensuel
        stack = np.stack(daily_images, axis=0)
        composite = np.nanmedian(stack, axis=0).astype(np.float32)
        composite[np.isnan(composite)] = -9999.0
        del daily_images, stack
        gc.collect()

        out_dir.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            out_path,
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
