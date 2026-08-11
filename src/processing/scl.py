"""Téléchargement SCL et calcul de `f_valid_aoi` — portage de
`03_series_s2.ipynb` §3.1.

`f_valid_aoi` est la métrique de disponibilité effective : contrairement au
`cloud_cover_catalogue` (couverture nuageuse déclarée sur la tuile entière),
elle mesure la fraction de pixels exploitables **sur l'AOI**.

Écarts par rapport au notebook :
- `get_cdse_token`/`refresh_cdse_token` ne sont pas redéfinies ici — le
  notebook les dupliquait localement (même authentification OAuth CDSE que
  `02_disponibilite_s2.ipynb`), réutilisées depuis `src.acquisition.cdse`.
- La boucle de téléchargement utilisait `ThreadPoolExecutor(max_workers=1)`
  — un pool à un seul worker n'apporte aucun parallélisme réel, simplifié
  en boucle séquentielle directe (cf. `methode.md`, `rasterio` non
  thread-safe sur les lectures JP2 — la contrainte à 1 worker n'était pas
  une marge de sécurité arbitraire, mais une nécessité).
- `get_granule_id` et `get_tile_crs` sont définies ici (premières utilisées
  chronologiquement, §3.1) et réutilisées par `bands.py` (§3.2) plutôt que
  dupliquées — les deux sections en ont besoin à l'identique.

**`SCL_INVALIDES`** : le jeu de classes exclues du calcul est `{1, 3, 7, 8, 9, 10, 11}`
(pixels saturés/défectueux, ombres nuageuses, nuages basse/moyenne/haute
probabilité, cirrus, neige/glace). Le tableau markdown du notebook source
n'en documentait que 5 (3, 8, 9, 10, 11 — omettant 1 et 7) alors que le
code en excluait bien 7 : une erreur de documentation du notebook, pas un
comportement à corriger — confirmé, le jeu de classes du code fait
autorité. Documentation corrigée ici en conséquence.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
import requests
from pyproj import CRS

from src.acquisition.cdse import refresh_cdse_token
from src.reporting.diagnostics import (
    ajouter_tableau,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)

ODATA_BASE_DL = "https://download.dataspace.copernicus.eu/odata/v1"

F_VALID_SEUIL = 0.01  # fraction minimale de pixels valides sur l'AOI
SCL_INVALIDES = {1, 3, 7, 8, 9, 10, 11}  # classes SCL exclues (cf. note ci-dessus)


def get_tile_crs(tile_id: str) -> CRS:
    """CRS UTM natif d'une tuile Sentinel-2 à partir de son identifiant (ex. 30UYA -> zone 30N)."""
    zone = int(tile_id[:2])
    return CRS.from_epsg(32600 + zone)


def get_granule_id(product_id: str, scene_id: str, token: dict) -> str | None:
    """Nom du premier répertoire sous `GRANULE/` dans l'arborescence SAFE du produit.

    Non disponible dans la réponse catalogue — récupéré via un appel `Nodes`
    léger (métadonnées seules, pas de téléchargement). Retourne `None` en
    cas d'erreur réseau plutôt que de lever, pour ne pas interrompre une
    boucle sur des centaines de scènes pour un échec isolé.
    """
    url = f"{ODATA_BASE_DL}/Products({product_id})/Nodes({scene_id}.SAFE)/Nodes(GRANULE)/Nodes"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=30,
        )
        resp.raise_for_status()
        nodes = resp.json().get("result", [])
        if nodes:
            return nodes[0]["Name"]
    except Exception as e:
        logger.warning("get_granule_id(%s) : %s", scene_id, e)
    return None


def compute_f_valid_aoi(
    scl_path: Path, aoi_geom: gpd.GeoDataFrame, tile_id: str
) -> float:
    """Fraction de pixels valides (hors `SCL_INVALIDES`) dans l'emprise AOI.

    Retourne `NaN` si le masque est vide (aucun pixel non-nodata).
    """
    with rasterio.open(scl_path) as src:
        scl_crs = src.crs or get_tile_crs(tile_id)
        aoi_reproj = aoi_geom.to_crs(scl_crs.to_epsg())
        shapes = [geom.__geo_interface__ for geom in aoi_reproj.geometry]
        try:
            data, _ = rasterio.mask.mask(src, shapes, crop=True, nodata=0)
        except Exception:
            return float("nan")
        scl = data[0]
        total = np.sum(scl > 0)
        if total == 0:
            return float("nan")
        invalides = np.sum(np.isin(scl, list(SCL_INVALIDES)))
        return float((total - invalides) / total)


def _telecharger_scl(
    product_id: str, scene_id: str, tile_id: str, scl_path: Path, token: dict
) -> bool:
    """Télécharge la bande SCL (60 m) d'une scène. Retourne `True` en cas de succès."""
    granule_id = get_granule_id(product_id, scene_id, token)
    if granule_id is None:
        return False

    date_str = scene_id.split("_")[2]  # YYYYMMDDTHHMMSS
    scl_name = f"T{tile_id}_{date_str}_SCL_60m.jp2"
    url = (
        f"{ODATA_BASE_DL}/Products({product_id})/Nodes({scene_id}.SAFE)"
        f"/Nodes(GRANULE)/Nodes({granule_id})/Nodes(IMG_DATA)/Nodes(R60m)/Nodes({scl_name})/$value"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=120,
            stream=True,
        )
        resp.raise_for_status()
        with open(scl_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
        return True
    except Exception as e:
        logger.warning("Téléchargement SCL (%s) : %s", scene_id, e)
        return False


def process_scene_scl(
    row: pd.Series, token: dict, aoi: gpd.GeoDataFrame, scl_dir: Path
) -> tuple[str, float]:
    """Pour une scène : téléchargement SCL (skip si déjà présente) + calcul `f_valid_aoi`.

    `token` doit déjà être valide/rafraîchi par l'appelant (cf.
    `calculer_f_valid_aoi`) — le rafraîchissement se fait dans la boucle,
    pas ici (cf. note de module : `refresh_cdse_token` retourne un nouveau
    dict, un rafraîchissement local à cette fonction ne se propagerait pas
    à l'itération suivante et annulerait le bénéfice du partage de token).

    Retourne `(scene_id, f_valid_aoi)` — `f_valid_aoi` vaut `NaN` en cas d'échec.
    """
    scene_id, product_id, tile_id = row["scene_id"], row["product_id"], row["tile_id"]

    out_dir = scl_dir / tile_id
    out_dir.mkdir(parents=True, exist_ok=True)
    scl_path = out_dir / f"{scene_id}_SCL_60m.jp2"

    if not scl_path.exists():
        ok = _telecharger_scl(product_id, scene_id, tile_id, scl_path, token)
        if not ok:
            return (scene_id, float("nan"))

    return (scene_id, compute_f_valid_aoi(scl_path, aoi, tile_id))


def calculer_f_valid_aoi(
    df_dedup: pd.DataFrame,
    aoi: gpd.GeoDataFrame,
    token: dict,
    scl_dir: Path,
    seuil: float = F_VALID_SEUIL,
) -> pd.DataFrame:
    """Calcule `f_valid_aoi` pour toutes les scènes de `df_dedup` (portage §3.1, cellule 6).

    Boucle séquentielle (cf. note de module sur `ThreadPoolExecutor`). Le
    token est rafraîchi et **réassigné** ici à chaque itération — pas dans
    `process_scene_scl` (cf. sa docstring) — pour que le bénéfice du
    partage de token persiste réellement sur la durée du run.
    Retourne `df_dedup` avec la colonne `f_valid_aoi` mise à jour.
    """
    df_dedup = df_dedup.copy()
    results: dict[str, float] = {}

    for i, (_, row) in enumerate(df_dedup.iterrows(), 1):
        token = refresh_cdse_token(token)
        scene_id, f = process_scene_scl(row, token, aoi, scl_dir)
        results[scene_id] = f
        if i % 50 == 0 or i == len(df_dedup):
            valides = sum(1 for v in results.values() if not np.isnan(v) and v >= seuil)
            logger.info(
                "[%d/%d] scènes traitées, %d retenues (f≥%s)",
                i,
                len(df_dedup),
                valides,
                seuil,
            )

    df_dedup["f_valid_aoi"] = df_dedup["scene_id"].map(results)
    logger.info(
        "f_valid_aoi calculé pour %d/%d scènes",
        df_dedup["f_valid_aoi"].notna().sum(),
        len(df_dedup),
    )
    return df_dedup


def generer_diagnostics_f_valid_aoi(
    df_dedup: pd.DataFrame,
    seuil: float = F_VALID_SEUIL,
    nom_module: str = "processing_scl",
) -> Path:
    """Rapport de diagnostics HTML pour `f_valid_aoi` (portage §3.1, cellule 7).

    Tableau de distribution uniquement (pas de figure — cohérent avec
    `generer_diagnostics_reconnaissance`, même nature de contenu tabulaire).
    """
    run_dir = nouveau_run_diagnostic(nom_module)

    n_total = len(df_dedup)
    n_nan = int(df_dedup["f_valid_aoi"].isna().sum())
    n_retenues = int((df_dedup["f_valid_aoi"] >= seuil).sum())
    n_exclues = int((df_dedup["f_valid_aoi"] < seuil).sum())

    df_distribution = (
        df_dedup["f_valid_aoi"].describe().round(3).to_frame(name="valeur")
    )

    blocs = [ajouter_tableau(df_distribution, "Distribution de f_valid_aoi")]
    metriques = {
        "Scènes totales": n_total,
        "f_valid_aoi NaN (échecs)": n_nan,
        f"Retenues (≥ {seuil})": n_retenues,
        f"Exclues (< {seuil})": n_exclues,
    }
    return rendre_rapport_html(
        run_dir, "Disponibilité effective (f_valid_aoi)", blocs, metriques
    )
