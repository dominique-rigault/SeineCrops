"""Agrégation zonale et chargement PostGIS — portage de `03_series_s2.ipynb`
§3.4, §3.5, §3.6.

Trois tables cibles, un chargement commun de la grille de labels
rasterisée, trois boucles d'insertion distinctes :
- `derived.s2_parcelles_monthly` (§3.4) — stats spectrales par parcelle × mois × variable
- `derived.s2_parcelles_completude` (§3.5) — indicateur de qualité par parcelle × mois
- `derived.s2_parcelles_ndvi_dates` (§3.6) — profil NDVI par parcelle × date d'acquisition

**Connexion PostGIS passée en paramètre**, pas ouverte via `get_connection()`
par appel comme le reste de `src/` — ces boucles tournent sur des milliers
d'itérations (jusqu'à ~14,2 M de lignes pour `s2_parcelles_monthly`, cf.
`methode.md`), ouvrir/fermer une connexion à chaque insertion serait
coûteux. Le notebook gardait déjà une connexion unique (`autocommit=True`)
sur toute la section — le cycle de vie de la connexion reste la
responsabilité de l'appelant (orchestration/DAG), pas de ces fonctions.

`INSERT ... ON CONFLICT DO NOTHING` pour les trois tables — cohérent avec
la décision déjà actée dans `methode.md` (composites stables entre deux
runs, contrairement aux prédictions de classification qui appellent
`DO UPDATE`).
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from psycopg2.extras import execute_values
from rasterio.features import rasterize as rio_rasterize

logger = logging.getLogger(__name__)


def creer_tables_zonales(conn) -> None:
    """Crée les 3 tables cibles si absentes (portage §3.4/§3.5/§3.6, regroupées
    ici plutôt que dispersées — exécution idempotente).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS derived.s2_parcelles_monthly (
                id_parcel  TEXT  NOT NULL,
                mois       TEXT  NOT NULL,
                variable   TEXT  NOT NULL,
                mean       REAL,
                std        REAL,
                p10        REAL,
                p90        REAL,
                PRIMARY KEY (id_parcel, mois, variable)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS derived.s2_parcelles_completude (
                id_parcel             TEXT  NOT NULL,
                mois                  TEXT  NOT NULL,
                n_dates_valides_moy   REAL,
                pct_pixels_couverts   REAL,
                PRIMARY KEY (id_parcel, mois)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS derived.s2_parcelles_ndvi_dates (
                id_parcel  TEXT  NOT NULL,
                date       DATE  NOT NULL,
                mean       REAL,
                std        REAL,
                n_pixels   INTEGER,
                PRIMARY KEY (id_parcel, date)
            );
            """
        )
    conn.commit()
    logger.info(
        "Tables zonales prêtes (s2_parcelles_monthly, _completude, _ndvi_dates)."
    )


def charger_grille_labels(
    conn, grille: dict
) -> tuple[gpd.GeoDataFrame, np.ndarray, dict]:
    """Charge les parcelles depuis `derived.rpg_parcelles_aoi`, rasterise un
    label entier par parcelle sur la grille AOI (portage §3.4, cellule 28).

    Retourne `(gdf_parcelles, label_grid, label_to_id)` — `gdf_parcelles`
    conservé (pas juste `label_grid`/`label_to_id`) pour
    `diagnostiquer_parcelles_non_rasterisees`, qui a besoin des géométries.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT f_geometry_column FROM geometry_columns
            WHERE f_table_schema = 'derived' AND f_table_name = 'rpg_parcelles_aoi';
            """
        )
        geom_col = cur.fetchone()[0]

    gdf_parcelles = gpd.read_postgis(
        f"SELECT id_parcel, {geom_col} FROM derived.rpg_parcelles_aoi",
        conn,
        geom_col=geom_col,
    )
    gdf_parcelles = gdf_parcelles.dissolve(by="id_parcel", as_index=False).reset_index(
        drop=True
    )
    gdf_parcelles["label"] = gdf_parcelles.index + 1
    label_to_id = dict(zip(gdf_parcelles["label"], gdf_parcelles["id_parcel"]))

    shapes = list(zip(gdf_parcelles.geometry, gdf_parcelles["label"]))
    label_grid = rio_rasterize(
        shapes,
        out_shape=(grille["height"], grille["width"]),
        transform=grille["transform"],
        fill=0,
        dtype="int32",
    )

    n_lab = np.count_nonzero(label_grid)
    logger.info(
        "Grille de labels : %d parcelles, %s pixels étiquetés (%.1f%%)",
        len(gdf_parcelles),
        f"{n_lab:,}",
        n_lab / label_grid.size * 100,
    )
    return gdf_parcelles, label_grid, label_to_id


def diagnostiquer_parcelles_non_rasterisees(
    gdf_parcelles: gpd.GeoDataFrame, label_grid: np.ndarray
) -> dict:
    """Identifie les parcelles n'ayant capturé aucun centre de pixel dans le
    raster de labels (portage §3.4, cellule 29).

    Contexte projet (cf. `methode.md`) : à distinguer des « parcelles
    orphelines » (correctement rasterisées mais sans valeur S2 valide sur
    toute la fenêtre — angle mort structurel à la jonction UTM 30N/31N),
    diagnostiquées séparément via `derived.s2_parcelles_monthly`, pas ici.

    Retourne `{"n_absentes", "pct_absentes", "surface_absentes_ha",
    "pct_surface", "grandes_absentes"}` — `grandes_absentes` : DataFrame
    des parcelles > 0,5 ha parmi les non rasterisées, triées par compacité
    décroissante (indice d'élongation, périmètre²/(4π·surface) — 1 pour un
    cercle parfait, croît pour les formes allongées).
    """
    labels_attendus = set(gdf_parcelles["label"])
    labels_presents = set(np.unique(label_grid)) - {0}
    labels_absents = labels_attendus - labels_presents

    gdf_absentes = gdf_parcelles[gdf_parcelles["label"].isin(labels_absents)].copy()

    n_absentes = len(gdf_absentes)
    surface_absentes_ha = gdf_absentes.geometry.area.sum() / 1e4
    surface_totale_ha = gdf_parcelles.geometry.area.sum() / 1e4

    gdf_absentes["surface_ha"] = gdf_absentes.geometry.area / 1e4
    gdf_absentes["perimetre_m"] = gdf_absentes.geometry.length
    gdf_absentes["compacite"] = gdf_absentes["perimetre_m"] ** 2 / (
        4 * np.pi * gdf_absentes.geometry.area
    )

    grandes_absentes = gdf_absentes[gdf_absentes["surface_ha"] > 0.5].sort_values(
        "compacite", ascending=False
    )[["id_parcel", "surface_ha", "compacite"]]

    resultats = {
        "n_absentes": n_absentes,
        "pct_absentes": n_absentes / len(gdf_parcelles) * 100,
        "surface_absentes_ha": float(surface_absentes_ha),
        "pct_surface": surface_absentes_ha / surface_totale_ha * 100,
        "grandes_absentes": grandes_absentes,
    }
    logger.info(
        "%d parcelle(s) non rasterisée(s) (%.2f%%), %.1f ha (%.3f%% de la surface totale)",
        n_absentes,
        resultats["pct_absentes"],
        surface_absentes_ha,
        resultats["pct_surface"],
    )
    return resultats


# ── §3.4 — Stats spectrales mensuelles ──────────────────────────────────────


def zonal_stats_from_labels(
    data: np.ndarray, labels: np.ndarray, label_to_id: dict
) -> list[tuple]:
    """Calcule mean, std, p10, p90 par parcelle étiquetée (portage §3.4, cellule 31).

    Approche vectorisée : tri des pixels par label puis découpe — O(n log n)
    sur les pixels valides, plus rapide que 80 689 appels `rasterio.mask`.
    Retourne une liste de tuples `(id_parcel, mean, std, p10, p90)`.
    """
    flat_data, flat_labels = data.ravel(), labels.ravel()
    valid = np.isfinite(flat_data) & (flat_labels > 0)
    d, lab = flat_data[valid], flat_labels[valid]
    if len(d) == 0:
        return []

    order = np.argsort(lab, kind="mergesort")
    d, lab = d[order], lab[order]
    unique_labels, start_idx = np.unique(lab, return_index=True)
    groups = np.split(d, start_idx[1:])

    rows = []
    for lbl, pixels in zip(unique_labels, groups):
        n = len(pixels)
        if n == 0:
            continue
        rows.append(
            (
                label_to_id[int(lbl)],
                float(np.mean(pixels)),
                float(np.std(pixels)) if n > 1 else 0.0,
                float(np.percentile(pixels, 10)),
                float(np.percentile(pixels, 90)),
            )
        )
    return rows


def charger_composites_vers_postgis(
    conn,
    composites_dir: Path,
    label_grid: np.ndarray,
    label_to_id: dict,
    variables: list[str],
) -> int:
    """Boucle sur les composites disponibles : stats zonales + insertion
    `derived.s2_parcelles_monthly` (portage §3.4, cellule 32).

    Retourne le nombre total de lignes insérées.
    """
    mois_disponibles = sorted(
        d.name for d in composites_dir.iterdir() if d.is_dir() and len(d.name) == 7
    )
    logger.info("Mois disponibles : %d %s", len(mois_disponibles), mois_disponibles)

    insert_sql = """
        INSERT INTO derived.s2_parcelles_monthly (id_parcel, mois, variable, mean, std, p10, p90)
        VALUES %s
        ON CONFLICT (id_parcel, mois, variable) DO NOTHING
    """

    total_ins = 0
    for mois in mois_disponibles:
        n_mois = 0
        for variable in variables:
            tif_path = composites_dir / mois / f"{variable}.tif"
            if not tif_path.exists():
                logger.warning("%s/%s — fichier absent", mois, variable)
                continue

            with rasterio.open(tif_path) as src:
                data = src.read(1).astype(np.float32)
            data[data == -9999.0] = np.nan

            rows = zonal_stats_from_labels(data, label_grid, label_to_id)
            if not rows:
                continue

            insert_rows = [(r[0], mois, variable, r[1], r[2], r[3], r[4]) for r in rows]
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, insert_rows, page_size=5000)

            n_mois += len(insert_rows)
            total_ins += len(insert_rows)

        logger.info("%s — %s lignes (cumul %s)", mois, f"{n_mois:,}", f"{total_ins:,}")

    logger.info(
        "Terminé. %s lignes insérées dans s2_parcelles_monthly.", f"{total_ins:,}"
    )
    return total_ins


# ── §3.5 — Statistiques de complétude ───────────────────────────────────────


def zonal_completude_from_labels(
    data: np.ndarray, labels: np.ndarray, label_to_id: dict
) -> list[tuple]:
    """Nombre moyen de dates valides et % de pixels couverts par parcelle
    (portage §3.5, cellule 37) — à partir du raster `n_valid_mois` (int16,
    `0` = pixel jamais valide ce mois-ci, valeur légitime, pas une absence).

    Retourne une liste de tuples `(id_parcel, n_dates_valides_moy, pct_pixels_couverts)`.
    """
    flat_data = data.ravel().astype(np.float32)
    flat_labels = labels.ravel()
    valid = flat_labels > 0  # tous les pixels de parcelle, y compris n_valid == 0
    d, lab = flat_data[valid], flat_labels[valid]
    if len(d) == 0:
        return []

    order = np.argsort(lab, kind="mergesort")
    d, lab = d[order], lab[order]
    unique_labels, start_idx = np.unique(lab, return_index=True)
    groups = np.split(d, start_idx[1:])

    rows = []
    for lbl, pixels in zip(unique_labels, groups):
        n = len(pixels)
        if n == 0:
            continue
        rows.append(
            (
                label_to_id[int(lbl)],
                float(np.mean(pixels)),
                float(np.mean(pixels > 0) * 100),
            )
        )
    return rows


def charger_completude_vers_postgis(
    conn,
    completude_dir: Path,
    mois_complets: list[str],
    label_grid: np.ndarray,
    label_to_id: dict,
) -> int:
    """Boucle sur les rasters de complétude disponibles : stats zonales +
    insertion `derived.s2_parcelles_completude` (portage §3.5, cellule 38).

    Skip les mois déjà en base (idempotence par mois entier, pas par ligne —
    contrairement à `charger_composites_vers_postgis` qui s'appuie sur
    `ON CONFLICT DO NOTHING` ligne à ligne).
    """
    mois_completude_dispo = sorted(
        p.name.replace("_n_valid.tif", "") for p in completude_dir.glob("*_n_valid.tif")
    )
    mois_manquants = sorted(set(mois_complets) - set(mois_completude_dispo))
    if mois_manquants:
        logger.warning(
            "Mois manquants (raster de complétude pas encore produit) : %s",
            mois_manquants,
        )

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT mois FROM derived.s2_parcelles_completude;")
        deja_en_base = {row[0] for row in cur.fetchall()}
    logger.info("%d mois déjà en base — seront skippés", len(deja_en_base))

    insert_sql = """
        INSERT INTO derived.s2_parcelles_completude (id_parcel, mois, n_dates_valides_moy, pct_pixels_couverts)
        VALUES %s
        ON CONFLICT (id_parcel, mois) DO NOTHING
    """

    total_ins = 0
    for mois in mois_completude_dispo:
        if mois in deja_en_base:
            continue

        with rasterio.open(completude_dir / f"{mois}_n_valid.tif") as src:
            n_valid_mois = src.read(1)

        rows = zonal_completude_from_labels(n_valid_mois, label_grid, label_to_id)
        if not rows:
            logger.warning("%s — aucune parcelle", mois)
            continue

        insert_rows = [(r[0], mois, r[1], r[2]) for r in rows]
        with conn.cursor() as cur:
            execute_values(cur, insert_sql, insert_rows, page_size=5000)
        conn.commit()

        total_ins += len(insert_rows)
        logger.info(
            "%s — %s lignes (cumul %s)", mois, f"{len(insert_rows):,}", f"{total_ins:,}"
        )

    logger.info(
        "Terminé. %s lignes insérées dans s2_parcelles_completude.", f"{total_ins:,}"
    )
    return total_ins


# ── §3.6 — NDVI aux dates d'acquisition ─────────────────────────────────────


def zonal_ndvi_from_labels(
    data: np.ndarray, labels: np.ndarray, label_to_id: dict
) -> list[tuple]:
    """mean, std, n_pixels par parcelle (portage §3.6, cellule 42).
    Retourne une liste de tuples `(id_parcel, mean, std, n_pixels)`.
    """
    flat_d, flat_l = data.ravel(), labels.ravel()
    valid = np.isfinite(flat_d) & (flat_l > 0)
    d, lab = flat_d[valid], flat_l[valid]
    if len(d) == 0:
        return []

    order = np.argsort(lab, kind="mergesort")
    d, lab = d[order], lab[order]
    unique_labels, start_idx = np.unique(lab, return_index=True)
    groups = np.split(d, start_idx[1:])

    rows = []
    for lbl, px in zip(unique_labels, groups):
        n = len(px)
        rows.append(
            (
                label_to_id[int(lbl)],
                float(np.mean(px)),
                float(np.std(px)) if n > 1 else 0.0,
                int(n),
            )
        )
    return rows


def _date_from_ndvi_path(p: Path) -> str:
    """Date (YYYYMMDD) extraite du nom de fichier `{scene_id}_NDVI.tif`."""
    scene_id = p.name.replace("_NDVI.tif", "")
    return scene_id.split("_")[2][:8]


def charger_ndvi_dates_vers_postgis(
    conn,
    indices_dir: Path,
    grille: dict,
    label_grid: np.ndarray,
    label_to_id: dict,
) -> int:
    """Mosaïque NDVI par date (médiane inter-tuiles) → stats zonales →
    insertion `derived.s2_parcelles_ndvi_dates` (portage §3.6, cellule 42).

    Contrairement aux composites mensuels, pas de compositage temporel :
    chaque date d'acquisition reste distincte, nécessaire à l'extraction des
    métriques phénologiques (SOS/POS/EOS, S4) que le pas mensuel écraserait.
    """
    from collections import defaultdict

    from src.processing.grid import reproject_to_aoi

    ndvi_files = sorted(indices_dir.rglob("*_NDVI.tif"))
    scenes_par_date: dict[str, list[Path]] = defaultdict(list)
    for p in ndvi_files:
        scenes_par_date[_date_from_ndvi_path(p)].append(p)
    logger.info(
        "Fichiers NDVI par scène : %d, %d dates distinctes",
        len(ndvi_files),
        len(scenes_par_date),
    )

    insert_sql = """
        INSERT INTO derived.s2_parcelles_ndvi_dates (id_parcel, date, mean, std, n_pixels)
        VALUES %s
        ON CONFLICT (id_parcel, date) DO NOTHING
    """

    total_ins = 0
    for date_str, paths in sorted(scenes_par_date.items()):
        date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        stack = np.full(
            (len(paths), grille["height"], grille["width"]), np.nan, dtype=np.float32
        )
        for k, p in enumerate(paths):
            stack[k] = reproject_to_aoi(p, grille)
        # all-NaN attendu en bord d'AOI / entre tuiles sans recouvrement ce jour précis
        # — résultat NaN correct, seul l'avertissement est du bruit. np.errstate ne
        # suffit pas ici (avertissement émis via le module `warnings` standard, pas les
        # indicateurs flottants bas niveau que np.errstate contrôle) — corrigé après
        # vérification empirique, cf. qc.py pour le détail.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            daily = np.nanmedian(stack, axis=0)
        del stack

        rows = zonal_ndvi_from_labels(daily, label_grid, label_to_id)
        del daily
        if not rows:
            logger.warning("%s — aucune parcelle", date_iso)
            continue

        insert_rows = [(r[0], date_iso, r[1], r[2], r[3]) for r in rows]
        with conn.cursor() as cur:
            execute_values(cur, insert_sql, insert_rows, page_size=5000)
        conn.commit()

        total_ins += len(insert_rows)
        logger.info(
            "%s (%d scène·s) — %s lignes (cumul %s)",
            date_iso,
            len(paths),
            f"{len(insert_rows):,}",
            f"{total_ins:,}",
        )

    logger.info(
        "Terminé. %s lignes insérées dans s2_parcelles_ndvi_dates.", f"{total_ins:,}"
    )
    return total_ins
