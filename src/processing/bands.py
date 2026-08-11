"""Téléchargement des bandes spectrales et calcul des indices — portage de
`03_series_s2.ipynb` §3.2.

Fusion téléchargement + masquage + indices dans une seule fonction par
scène (`process_scene_bands`), comme dans le notebook — pas de séparation
en deux tâches Airflow distinctes malgré ce que suggérait le DAG indicatif
initial de `methode.md` (cf. décision actée : évite une relecture disque
des rasters déjà chargés en mémoire pour 528 950 parcelles × plusieurs
centaines de scènes).

⚠️ **`resample_to_20m` porte deux correctifs historiques critiques** —
diagnostiqués à la main sur ce projet (cf. `methode.md`), à ne jamais
modifier sans relire leur documentation en commentaire dans la fonction :
1. `src_nodata=0` — les JP2 L2A codent en 0 les pixels hors fauchée
   satellite ; sans ce paramètre, GDAL les traite comme réflectance valide
   et les mélange par interpolation bilinéaire aux pixels réels voisins,
   produisant un artefact de frontière nette décorrélé du footprint de
   tuile (diagnostiqué via un pattern bimodal dans les distances de
   divergence en S4).
2. Fallback CRS via `ref_crs_wkt` (résolu en amont par `get_tile_crs`,
   passé en paramètre) plutôt qu'un codage en dur `EPSG:32630` — l'ancien
   fallback décalait d'un fuseau UTM entier les tuiles 31UCQ/31UCR
   (zone 31N réelle) quand le JP2OpenJPEG n'exposait pas son CRS
   (systématique sur cet environnement Windows pour ces deux tuiles),
   produisant 100 % de nodata sur 149/149 et 145/145 scènes affectées.

`get_granule_id`/`get_tile_crs`/`ODATA_BASE_DL` réutilisés depuis
`src.processing.scl` (mêmes fonctions, utilisées à l'identique par §3.1 et
§3.2 dans le notebook) plutôt que dupliqués.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
import rasterio.warp
import requests
from pyproj import CRS
from rasterio.enums import Resampling

from src.acquisition.cdse import get_cdse_token, refresh_cdse_token
from src.processing.scl import (
    ODATA_BASE_DL,
    SCL_INVALIDES,
    get_granule_id,
    get_tile_crs,
)

logger = logging.getLogger(__name__)

BANDES = [
    ("B02", "R10m", "10m"),
    ("B04", "R10m", "10m"),
    ("B05", "R20m", "20m"),
    ("B06", "R20m", "20m"),
    ("B07", "R20m", "20m"),
    ("B08", "R10m", "10m"),
    ("B11", "R20m", "20m"),
]
INDICES_NOMS = ["NDVI", "EVI", "NDWI", "NDRE"]


def download_band(
    product_id: str,
    scene_id: str,
    granule_id: str,
    band: str,
    safe_dir: str,
    res_suffix: str,
    tile_id: str,
    date_str: str,
    token: dict,
    bands_dir: Path,
) -> Path | None:
    """Télécharge une bande jp2 dans `bands_dir/<tile_id>/`. Idempotent (skip si déjà présente)."""
    out_dir = bands_dir / tile_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"T{tile_id}_{date_str}_{band}_{res_suffix}.jp2"
    out_path = out_dir / fname

    if out_path.exists():
        return out_path

    url = (
        f"{ODATA_BASE_DL}/Products({product_id})/Nodes({scene_id}.SAFE)"
        f"/Nodes(GRANULE)/Nodes({granule_id})/Nodes(IMG_DATA)"
        f"/Nodes({safe_dir})/Nodes({fname})/$value"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=180,
            stream=True,
        )
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
        return out_path
    except Exception as e:
        logger.warning("download_band(%s, %s) : %s", scene_id, band, e)
        return None


def resample_to_20m(
    src_path: Path,
    ref_transform,
    ref_crs_wkt: str,
    ref_shape: tuple,
) -> np.ndarray:
    """Relit une bande jp2 et la resample à la grille 20 m de référence (array-to-array).

    NE PAS MODIFIER sans relire la documentation de module — deux correctifs
    historiques critiques (`src_nodata=0`, fallback CRS via `ref_crs_wkt`).
    """
    with rasterio.open(src_path) as src:
        raw = src.read(1).astype(np.float32)
        src_crs_wkt = (src.crs or CRS.from_wkt(ref_crs_wkt)).to_wkt()
        src_transform = src.transform
    data = np.full(ref_shape, np.nan, dtype=np.float32)
    rasterio.warp.reproject(
        source=raw,
        destination=data,
        src_transform=src_transform,
        src_crs=src_crs_wkt,
        dst_transform=ref_transform,
        dst_crs=ref_crs_wkt,
        resampling=Resampling.bilinear,
        src_nodata=0,  # ← correctif 1 : 0 = hors fauchée dans les JP2 L2A
        dst_nodata=np.nan,  # ← propage le nodata (pas de blending 0/valeur réelle)
    )
    return data


def compute_indices(bands: dict) -> dict:
    """Calcule NDVI, EVI, NDWI, NDRE à partir du dict de tableaux numpy (bandes 20 m)."""
    eps = 1e-10  # évite la division par zéro
    b02 = bands["B02"].astype(np.float32) / 10000
    b04 = bands["B04"].astype(np.float32) / 10000
    b05 = bands["B05"].astype(np.float32) / 10000
    b08 = bands["B08"].astype(np.float32) / 10000
    b11 = bands["B11"].astype(np.float32) / 10000

    ndvi = (b08 - b04) / (b08 + b04 + eps)
    ndwi = (b08 - b11) / (b08 + b11 + eps)
    ndre = (b08 - b05) / (b08 + b05 + eps)

    evi_denom = b08 + 6 * b04 - 7.5 * b02 + 1
    evi_denom = np.where(np.abs(evi_denom) < 0.001, 0.001, evi_denom)
    evi = 2.5 * (b08 - b04) / evi_denom

    return {
        "NDVI": np.clip(ndvi, -1, 1),
        "EVI": np.clip(evi, -2, 2),
        "NDWI": np.clip(ndwi, -1, 1),
        "NDRE": np.clip(ndre, -1, 1),
    }


def save_geotiff(
    data: np.ndarray, out_path: Path, transform, crs, nodata: float = -9999.0
) -> None:
    """Sauvegarde un tableau numpy en GeoTIFF Float32 compressé (tuilé, deflate)."""
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as dst:
        data_out = np.where(np.isnan(data), nodata, data).astype(np.float32)
        dst.write(data_out, 1)


def process_scene_bands(
    row: pd.Series,
    token: dict,
    aoi: gpd.GeoDataFrame,
    scl_dir: Path,
    bands_dir: Path,
    indices_dir: Path,
) -> tuple[str, str]:
    """Pour une scène retenue : téléchargement des 7 bandes, resampling 20 m,
    masquage SCL, calcul des 4 indices, sauvegarde GeoTIFF.

    `token` doit déjà être valide/rafraîchi par l'appelant (cf.
    `traiter_bandes_indices`) — cette fonction ne l'obtient plus elle-même
    (le notebook rappelait `get_cdse_token()` à chaque scène, ce qui
    fonctionnait mais réauthentifiait inutilement sur un run de plusieurs
    centaines de scènes ; corrigé lors de la migration).

    Portage de §3.2 (cellule 11, `process_scene_s32`). Retourne
    `(scene_id, "OK"|"SKIP"|"ERREUR <msg>")`.
    """
    scene_id, product_id, tile_id = row["scene_id"], row["product_id"], row["tile_id"]
    date_str = scene_id.split("_")[2]

    idx_dir = indices_dir / tile_id
    ndvi_path = idx_dir / f"{scene_id}_NDVI.tif"
    if ndvi_path.exists():
        return (scene_id, "SKIP")

    granule_id = get_granule_id(product_id, scene_id, token)
    if granule_id is None:
        return (scene_id, "ERREUR granule_id introuvable")

    band_paths = {}
    for band, safe_dir, res_suffix in BANDES:
        p = download_band(
            product_id,
            scene_id,
            granule_id,
            band,
            safe_dir,
            res_suffix,
            tile_id,
            date_str,
            token,
            bands_dir,
        )
        if p is None:
            return (scene_id, f"ERREUR téléchargement {band}")
        band_paths[band] = p

    try:
        # B05 (20m natif) définit la grille de référence après crop AOI
        with rasterio.open(band_paths["B05"]) as ref:
            ref_crs = ref.crs or CRS.from_epsg(32600 + int(tile_id[:2]))
            aoi_reproj = aoi.to_crs(ref_crs.to_epsg())
            shapes = [g.__geo_interface__ for g in aoi_reproj.geometry]
            b05_raw, transform = rasterio.mask.mask(ref, shapes, crop=True, nodata=0)
            ref_shape = b05_raw.shape[1:]
            del b05_raw

        # Masque SCL sur l'emprise AOI
        scl_path = scl_dir / tile_id / f"{scene_id}_SCL_60m.jp2"
        scl_reproj = np.zeros(ref_shape, dtype=np.uint8)
        if scl_path.exists():
            with rasterio.open(scl_path) as scl_src:
                scl_raw = scl_src.read(1)
                scl_crs_wkt = (scl_src.crs or get_tile_crs(tile_id)).to_wkt()
                scl_transform = scl_src.transform
            rasterio.warp.reproject(
                source=scl_raw,
                destination=scl_reproj,
                src_transform=scl_transform,
                src_crs=scl_crs_wkt,
                dst_transform=transform,
                dst_crs=ref_crs.to_wkt(),
                resampling=Resampling.nearest,
            )
            del scl_raw
        else:
            logger.warning("SCL absente pour %s — masque non appliqué", scene_id)
        masque_invalide = np.isin(scl_reproj, list(SCL_INVALIDES))
        del scl_reproj

        # Lecture + resampling 20m uniforme pour toutes les bandes
        bands = {
            band: resample_to_20m(
                band_paths[band], transform, ref_crs.to_wkt(), ref_shape
            )
            for band, _, _ in BANDES
        }

        # Masque SCL appliqué à toutes les bandes
        for band in bands:
            bands[band] = bands[band].astype(np.float32)
            bands[band][masque_invalide] = np.nan
        del masque_invalide

        # Sauvegarde bandes
        band_out_dir = bands_dir / tile_id
        band_out_dir.mkdir(parents=True, exist_ok=True)
        for band, arr in bands.items():
            save_geotiff(
                arr, band_out_dir / f"{scene_id}_{band}.tif", transform, ref_crs
            )

        # Calcul et sauvegarde indices
        idx_dir.mkdir(parents=True, exist_ok=True)
        indices = compute_indices(bands)
        for name, arr in indices.items():
            save_geotiff(arr, idx_dir / f"{scene_id}_{name}.tif", transform, ref_crs)

    except Exception as e:
        return (scene_id, f"ERREUR {e}")
    finally:
        gc.collect()

    return (scene_id, "OK")


def traiter_bandes_indices(
    df_retenues: pd.DataFrame,
    aoi: gpd.GeoDataFrame,
    scl_dir: Path,
    bands_dir: Path,
    indices_dir: Path,
) -> dict:
    """Boucle séquentielle sur toutes les scènes retenues (portage §3.2, boucle finale).

    Le token CDSE est obtenu une fois puis rafraîchi ici, dans la boucle —
    pas dans `process_scene_bands` — et **réassigné** à chaque itération
    (`refresh_cdse_token` retourne un nouveau dict, ne mute pas en place ;
    une réassignation manquante annulerait le bénéfice du partage, cf.
    docstring de module).

    Retourne `{"n_ok": int, "n_skip": int, "erreurs": [(scene_id, message), ...]}`.
    """
    erreurs: list[tuple[str, str]] = []
    n_ok = n_skip = 0
    token = get_cdse_token()

    for i, (_, row) in enumerate(df_retenues.iterrows(), 1):
        token = refresh_cdse_token(token)
        scene_id, status = process_scene_bands(
            row, token, aoi, scl_dir, bands_dir, indices_dir
        )
        if status == "OK":
            n_ok += 1
        elif status == "SKIP":
            n_skip += 1
        else:
            erreurs.append((scene_id, status))

        if i % 20 == 0 or i == len(df_retenues):
            logger.info(
                "[%d/%d] OK:%d SKIP:%d ERR:%d",
                i,
                len(df_retenues),
                n_ok,
                n_skip,
                len(erreurs),
            )

    logger.info("Terminé. OK:%d SKIP:%d Erreurs:%d", n_ok, n_skip, len(erreurs))
    for scene, msg in erreurs:
        logger.warning("%s — %s", scene, msg)

    return {"n_ok": n_ok, "n_skip": n_skip, "erreurs": erreurs}
