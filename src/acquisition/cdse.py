"""Disponibilité Sentinel-2 (catalogue CDSE) — portage de `02_disponibilite_s2.ipynb`.

Même principe que `rpg.py` : chaque fonction référence sa section d'origine,
retourne des données structurées, logue son résultat via `logging` plutôt
que d'imprimer (cf. `methode.md` §Logging et §Migration notebooks → src/).

Écart délibéré par rapport au notebook : `ANNEE_REFERENCE`, `DATE_START`,
`DATE_END` n'y sont pas des constantes de module mais des paramètres passés
explicitement aux fonctions — mêmes raisons que `millesime`/`region_code`
dans `rpg.py` (une valeur de campagne figée en dur dans un module `src/`
serait un couplage caché entre le code et une exécution donnée).

Non porté depuis le notebook (reste notebook-only, même logique que les
exclusions de `rpg.py`, documentée dans `methode.md`) :
- l'annotation de rupture d'année (`axvline` "→ 2024") sur l'histogramme de
  §2.5 — cosmétique, dépendante d'une position codée en dur (`x=3.5`), pas
  reconduite dans `generer_diagnostics_disponibilite`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src.db.connection import PROJECT_ROOT  # noqa: F401 — importé pour son effet de bord (load_dotenv)
from src.reporting.diagnostics import (
    ajouter_figure,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

TUILES = {
    "30UYA": "nord",
    "31UCR": "nord",
    "30UYV": "sud",
    "31UCQ": "sud",
}


# ── §2.1 — Authentification CDSE ───────────────────────────────────────────


def _get_credentials() -> tuple[str, str]:
    user = os.getenv("CDSE_USER")
    password = os.getenv("CDSE_PASSWORD")
    if not user or not password:
        raise EnvironmentError("CDSE_USER / CDSE_PASSWORD manquants dans .env.")
    return user, password


def get_cdse_token() -> dict:
    """Obtient un token OAuth CDSE. Retourne {access_token, refresh_token, expires_at}."""
    user, password = _get_credentials()
    resp = requests.post(
        CDSE_TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "cdse-public",
            "username": user,
            "password": password,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + payload["expires_in"] - 30,  # marge 30 s
    }


def refresh_cdse_token(token_dict: dict) -> dict:
    """Rafraîchit le token si expiré (marge 30 s), sinon le retourne tel quel.

    Si le *refresh token* lui-même a expiré (cas observé après plusieurs
    heures d'exécution sur `traitement_bandes_indices` — cf. `methode.md`
    §S6, bug trouvé au premier run réel du DAG A), le rafraîchissement
    échoue avec un `400`/`401` : on retombe alors sur une ré-authentification
    complète (`get_cdse_token`) plutôt que de laisser l'exception remonter
    et faire échouer toute la tâche après plusieurs heures de travail utile.
    """
    if time.time() < token_dict["expires_at"]:
        return token_dict
    resp = requests.post(
        CDSE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": "cdse-public",
            "refresh_token": token_dict["refresh_token"],
        },
        timeout=30,
    )
    if resp.status_code in (400, 401):
        logger.warning(
            "refresh_cdse_token : rafraîchissement refusé (%d), "
            "ré-authentification complète.",
            resp.status_code,
        )
        return get_cdse_token()
    resp.raise_for_status()
    payload = resp.json()
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + payload["expires_in"] - 30,
    }


# ── §2.2 — Requête catalogue multi-tuiles ──────────────────────────────────


def query_s2_catalogue(
    tile_id: str,
    date_start: str,
    date_end: str,
    token_dict: dict,
    page_size: int = 1000,
) -> list[dict]:
    """Interroge le catalogue OData pour une tuile S2 L2A, pagine automatiquement.

    Aucun filtre `cloudCover` : toutes les scènes L2A disponibles sont
    retournées (le filtrage réel se fait sur la bande SCL en S2, pas ici).
    Le token est rafraîchi avant chaque page.
    """
    filter_str = (
        f"Collection/Name eq 'SENTINEL-2' and "
        f"Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'productType' and "
        f"att/OData.CSC.StringAttribute/Value eq 'S2MSI2A') and "
        f"Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'tileId' and "
        f"att/OData.CSC.StringAttribute/Value eq '{tile_id}') and "
        f"ContentDate/Start ge {date_start} and "
        f"ContentDate/Start le {date_end}"
    )

    results: list[dict] = []
    skip = 0
    while True:
        token_dict = refresh_cdse_token(token_dict)
        resp = requests.get(
            ODATA_BASE,
            params={
                "$filter": filter_str,
                "$orderby": "ContentDate/Start asc",
                "$top": page_size,
                "$skip": skip,
                "$expand": "Attributes",
            },
            headers={"Authorization": f"Bearer {token_dict['access_token']}"},
            timeout=60,
        )
        resp.raise_for_status()
        batch = resp.json().get("value", [])
        results.extend(batch)
        if len(batch) < page_size:
            break
        skip += page_size

    return results


def interroger_catalogue_complet(
    date_start: str,
    date_end: str,
    token_dict: dict,
    tuiles: dict[str, str] = TUILES,
) -> dict[str, list[dict]]:
    """Boucle `query_s2_catalogue` sur toutes les tuiles. Retourne {tile_id: [scènes...]}."""
    raw_results: dict[str, list[dict]] = {}
    for tile_id, pair in tuiles.items():
        t0 = time.time()
        scenes = query_s2_catalogue(tile_id, date_start, date_end, token_dict)
        raw_results[tile_id] = scenes
        logger.info(
            "%s (%s) -> %d scènes [%.1fs]", tile_id, pair, len(scenes), time.time() - t0
        )

    total = sum(len(v) for v in raw_results.values())
    logger.info("Total brut (toutes tuiles, doublons inclus) : %d scènes.", total)
    return raw_results


# ── §2.3 — Structuration en DataFrame ──────────────────────────────────────


def _extract_attribute(attributes: list[dict], name: str):
    """Extrait la valeur d'un attribut OData par son nom."""
    for attr in attributes:
        if attr.get("Name") == name:
            return attr.get("Value")
    return None


def structurer_catalogue(
    raw_results: dict[str, list[dict]], tuiles: dict[str, str] = TUILES
) -> pd.DataFrame:
    """Structure les résultats OData bruts en DataFrame (une ligne par scène).

    `f_valid_aoi` est provisionnée à `NaN` — calculée en S2 à partir de la
    bande SCL, hors périmètre de ce module.
    """
    records = []
    for tile_id, pair in tuiles.items():
        for scene in raw_results[tile_id]:
            attrs = scene.get("Attributes", [])
            records.append(
                {
                    "product_id": scene["Id"],
                    "scene_id": scene["Name"],
                    "tile_id": tile_id,
                    "pair": pair,
                    "datetime_utc": pd.to_datetime(scene["ContentDate"]["Start"]),
                    "cloud_cover_catalogue": _extract_attribute(attrs, "cloudCover"),
                    "orbit_relative": _extract_attribute(attrs, "relativeOrbitNumber"),
                    "f_valid_aoi": np.nan,
                }
            )

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["datetime_utc"].dt.date)
    df = df.sort_values(["datetime_utc", "tile_id"]).reset_index(drop=True)

    logger.info(
        "Catalogue structuré : %d scènes (%s -> %s).",
        len(df),
        df["date"].min(),
        df["date"].max(),
    )
    return df


# ── §2.4 — Déduplication et disponibilité ──────────────────────────────────


def dedupliquer_catalogue(df: pd.DataFrame) -> pd.DataFrame:
    """Déduplique tuile×date, conserve le baseline le plus récent.

    Tri par `scene_id` décroissant : le baseline le plus récent (ex. N0511 >
    N0510) et la date de génération la plus récente arrivent en premier.
    """
    df_dedup = (
        df.sort_values("scene_id", ascending=False)
        .drop_duplicates(subset=["tile_id", "date"], keep="first")
        .sort_values(["datetime_utc", "tile_id"])
        .reset_index(drop=True)
    )

    n_doublons = len(df) - len(df_dedup)
    logger.info(
        "Déduplication : %d scènes -> %d (%d doublon(s) supprimé(s)).",
        len(df),
        len(df_dedup),
        n_doublons,
    )
    return df_dedup


def calculer_disponibilite_mensuelle(
    df_dedup: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Disponibilité journalière puis agrégation mensuelle. Retourne `(daily, daily_full_aoi, monthly)`.

    - `daily` : nombre de scènes par jour, toutes tuiles confondues (couverture partielle).
    - `daily_full_aoi` : bool par jour — `True` si les paires nord ET sud sont couvertes
      (condition nécessaire pour une couverture quasi complète de l'AOI).
    - `monthly` : agrégation mensuelle (`n_scenes`, `days_covered`, `days_full_aoi`, `pct_*`).
    """
    daily = df_dedup.groupby("date")["scene_id"].count().rename("n_scenes")

    pairs_per_day = df_dedup.groupby("date")["pair"].apply(lambda x: set(x.unique()))
    daily_full_aoi = pairs_per_day.apply(
        lambda pairs: {"nord", "sud"}.issubset(pairs)
    ).rename("full_aoi")

    daily_combined = pd.concat([daily, daily_full_aoi], axis=1).fillna(
        {"full_aoi": False}
    )

    monthly = (
        daily_combined.resample("MS")
        .agg(
            n_scenes=("n_scenes", "sum"),
            days_covered=("n_scenes", lambda x: (x > 0).sum()),
            days_full_aoi=("full_aoi", "sum"),
        )
        .assign(
            days_in_month=lambda x: x.index.days_in_month,
            pct_covered=lambda x: (x["days_covered"] / x["days_in_month"] * 100).round(
                1
            ),
            pct_full_aoi=lambda x: (
                x["days_full_aoi"] / x["days_in_month"] * 100
            ).round(1),
        )
    )

    logger.info(
        "Disponibilité : %d/%d jours couverts (partiel), %d/%d jours AOI quasi complète.",
        int((daily > 0).sum()),
        len(daily),
        int(daily_full_aoi.sum()),
        len(daily),
    )
    return daily, daily_full_aoi, monthly


# ── §2.5 — Diagnostics, rapport, persistance ───────────────────────────────


def generer_diagnostics_disponibilite(
    monthly: pd.DataFrame,
    date_start: str,
    date_end: str,
    nom_module: str = "acquisition_cdse",
) -> Path:
    """Histogramme de disponibilité mensuelle + rapport de diagnostics HTML.

    Portage de §2.5 (histogramme), via `src.reporting.diagnostics` plutôt
    qu'un `plt.show()` notebook — cf. `methode.md`, QC visuelle non
    bloquante pour le DAG.
    """
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    run_dir = nouveau_run_diagnostic(nom_module)

    fig, ax1 = plt.subplots(figsize=(14, 5))
    x = range(len(monthly))
    width = 0.28
    months_labels = [d.strftime("%b\n%Y") for d in monthly.index]

    ax1.bar(
        [i - width for i in x],
        monthly["n_scenes"],
        width=width,
        color="#4C8BB5",
        label="Scènes catalogue (4 tuiles)",
    )
    ax1.bar(
        list(x),
        monthly["days_covered"],
        width=width,
        color="#E07B39",
        label="Jours couverts (partiel)",
    )
    ax1.bar(
        [i + width for i in x],
        monthly["days_full_aoi"],
        width=width,
        color="#4CAF50",
        label="Jours AOI quasi complète (nord+sud)",
    )

    ax1.set_xticks(list(x))
    ax1.set_xticklabels(months_labels, fontsize=8)
    ax1.set_ylabel("Nombre")
    ax1.yaxis.set_major_locator(ticker.MultipleLocator(10))
    ax1.set_ylim(0, monthly["n_scenes"].max() * 1.20)
    ax1.legend(loc="upper left", fontsize=8)
    ax1.set_title(
        f"Disponibilité Sentinel-2 L2A — AOI SeineCrops\n"
        f"Tuiles : 30UYA · 31UCR · 30UYV · 31UCQ  ·  "
        f"Fenêtre : {date_start[:10]} -> {date_end[:10]}  ·  Sans filtre nuage",
        fontsize=10,
    )

    for i, (_, row) in enumerate(monthly.iterrows()):
        if row["days_full_aoi"] > 0:
            ax1.text(
                i + width,
                row["days_full_aoi"] + 0.4,
                f"{row['pct_full_aoi']:.0f}%",
                ha="center",
                va="bottom",
                fontsize=7,
                color="#2E7D32",
            )

    fig.tight_layout()

    bloc = ajouter_figure(
        fig, "disponibilite_mensuelle", "Disponibilité Sentinel-2 mensuelle", run_dir
    )
    plt.close(fig)

    metriques = {
        "Fenêtre": f"{date_start[:10]} -> {date_end[:10]}",
        "Mois couverts": len(monthly),
    }
    return rendre_rapport_html(
        run_dir, "Disponibilité Sentinel-2 — CDSE", [bloc], metriques
    )


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def ecrire_rapport_disponibilite(
    df: pd.DataFrame,
    df_dedup: pd.DataFrame,
    daily: pd.Series,
    daily_full_aoi: pd.Series,
    monthly: pd.DataFrame,
    annee_reference: int,
    date_start: str,
    date_end: str,
    data_raw: Path,
    tuiles: dict[str, str] = TUILES,
) -> Path:
    """Consolide `AVAILABILITY_REPORT.json` (portage §2.5, cellule 19)."""
    date_min = pd.Timestamp(date_start[:10])
    date_max = pd.Timestamp(date_end[:10])
    jours_fenetre = (date_max - date_min).days + 1

    daily_full = daily.reindex(
        pd.date_range(date_min, date_max, freq="D"), fill_value=0
    )
    daily_full_aoi_reindexed = daily_full_aoi.reindex(
        pd.date_range(date_min, date_max, freq="D"), fill_value=False
    )

    jours_couverts = int((daily_full > 0).sum())
    jours_full_aoi = int(daily_full_aoi_reindexed.sum())

    rapport = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "annee_reference": annee_reference,
        "fenetre": {"start": date_start, "end": date_end},
        "tuiles": {tile_id: {"pair": pair} for tile_id, pair in tuiles.items()},
        "orbites_relatives": (
            df_dedup.groupby("tile_id")["orbit_relative"]
            .apply(lambda x: sorted(x.unique().tolist()))
            .to_dict()
        ),
        "catalogue": {
            "scenes_brutes": len(df),
            "scenes_dedup": len(df_dedup),
            "doublons_supprimes": len(df) - len(df_dedup),
            "filtre_nuage": None,
        },
        "disponibilite": {
            "jours_fenetre": jours_fenetre,
            "jours_couverts": jours_couverts,
            "pct_couverts": round(jours_couverts / jours_fenetre * 100, 1),
            "jours_full_aoi": jours_full_aoi,
            "pct_full_aoi": round(jours_full_aoi / jours_fenetre * 100, 1),
            "note_full_aoi": (
                "Jours où les paires nord (30UYA|31UCR) ET sud (30UYV|31UCQ) "
                "sont simultanément couvertes"
            ),
            "mensuel": [
                {
                    "mois": row.Index.strftime("%Y-%m"),
                    "n_scenes": int(row.n_scenes),
                    "days_covered": int(row.days_covered),
                    "days_full_aoi": int(row.days_full_aoi),
                    "days_in_month": int(row.days_in_month),
                    "pct_covered": float(row.pct_covered),
                    "pct_full_aoi": float(row.pct_full_aoi),
                }
                for row in monthly.itertuples()
            ],
        },
        "niveau_2": {
            "statut": "non calculé",
            "description": (
                "f_valid_aoi sera calculé en sprint S2 par téléchargement de la bande SCL "
                "(60 m) et calcul de la fraction de pixels valides sur l'AOI "
                "(classes SCL invalides : 3, 8, 9, 10, 11)."
            ),
        },
    }

    dest = data_raw / "AVAILABILITY_REPORT.json"
    dest.write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2, cls=_DecimalEncoder),
        encoding="utf-8",
    )
    logger.info("Rapport de disponibilité écrit : %s", dest)
    return dest


def sauvegarder_catalogue(df_dedup: pd.DataFrame, data_raw: Path) -> Path:
    """Persiste le catalogue dédupliqué en Parquet — dépendance directe de
    `03_series_s2.ipynb` (identifiants CDSE nécessaires au téléchargement des bandes).
    """
    dest = data_raw / "catalogue_dedup.parquet"
    df_dedup.to_parquet(dest, index=False)
    logger.info(
        "Catalogue dédupliqué sauvegardé : %s (%d scènes).", dest, len(df_dedup)
    )
    return dest
