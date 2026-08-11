"""Grille AOI de référence et reprojection commune — portage de
`03_series_s2.ipynb` §3.2 ter.

Transverse à tout `src/processing/` : la même grille (résolution, emprise,
transform) doit être utilisée pour le masquage SCL, les bandes/indices, les
composites et les stats zonales — sinon les pixels ne s'alignent pas d'une
étape à l'autre.

Écart par rapport au notebook : la grille est retournée comme un dict
explicite (`calculer_grille_aoi`) plutôt que stockée dans des globales de
module (`AOI_WIDTH`, `AOI_HEIGHT`, `AOI_TRANSFORM`) — cohérent avec le
principe déjà appliqué à `rpg.py`/`cdse.py` (paramètres explicites plutôt
que constantes globales), et nécessaire ici : plusieurs futures tâches
Airflow indépendantes doivent recalculer/recevoir la même grille sans
dépendre d'un état de module partagé.
"""

from __future__ import annotations

import numpy as np
import rasterio
import rasterio.warp
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from pyproj import CRS as ProjCRS
from pathlib import Path
import geopandas as gpd

RES = 20  # résolution cible en mètres
CRS_CIBLE = "EPSG:2154"
CRS_CIBLE_WKT = ProjCRS.from_epsg(2154).to_wkt()


def calculer_grille_aoi(aoi: gpd.GeoDataFrame, res: int = RES) -> dict:
    """Calcule la grille AOI de référence (emprise arrondie à `res` m, EPSG:2154).

    Portage de §3.2 ter (cellule 16). Retourne un dict `{width, height,
    transform, crs_wkt, res, bounds}` — à passer explicitement aux fonctions
    qui en ont besoin (`reproject_to_aoi`, composites, stats zonales),
    plutôt que recalculé à chaque appel.
    """
    aoi_2154 = aoi.to_crs(CRS_CIBLE)
    bounds = aoi_2154.total_bounds  # (xmin, ymin, xmax, ymax)

    xmin = np.floor(bounds[0] / res) * res
    ymin = np.floor(bounds[1] / res) * res
    xmax = np.ceil(bounds[2] / res) * res
    ymax = np.ceil(bounds[3] / res) * res

    width = int((xmax - xmin) / res)
    height = int((ymax - ymin) / res)
    transform = from_bounds(xmin, ymin, xmax, ymax, width, height)

    return {
        "width": width,
        "height": height,
        "transform": transform,
        "crs_wkt": CRS_CIBLE_WKT,
        "res": res,
        "bounds": (xmin, ymin, xmax, ymax),
    }


def reproject_to_aoi(src_path: Path, grille: dict) -> np.ndarray:
    """Reprojette un GeoTIFF source sur la grille AOI (portage §3.2 ter, cellule 17).

    Retourne un tableau float32 `(height, width)` avec `NaN` sur nodata —
    interpolation bilinéaire, cohérente avec `resample_to_20m` (`bands.py`).
    """
    with rasterio.open(src_path) as src:
        nodata = src.nodata if src.nodata is not None else -9999.0
        dst = np.full((grille["height"], grille["width"]), np.nan, dtype=np.float32)
        rasterio.warp.reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=grille["transform"],
            dst_crs=grille["crs_wkt"],
            resampling=Resampling.bilinear,
            src_nodata=nodata,
            dst_nodata=np.nan,
        )
    return dst
