"""Persistance PostGIS — portage de `05_divergence_pheno.ipynb` §5.4.

Deux tables dédiées (`derived.divergence`, `derived.phenologie`), avec
`ON CONFLICT DO UPDATE` — les valeurs peuvent changer d'un run à l'autre
(recalibrage de `λ`, ajustement de `FENETRES_PHENOLOGIE`), cohérent avec
la décision déjà actée pour les prédictions de `src/ml/predict.py`.
Upsert vectorisé via `psycopg2.extras.execute_values`.

Depuis la migration 0005 (sprint S3) : `classe_declaree` n'est plus
écrite ici, elle vit uniquement sur `derived.rpg_parcelles_aoi` (cf.
`db/migrations/0005_rpg_parcelles_aoi_classe_declaree.sql`).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

from src.db.connection import connexion

logger = logging.getLogger(__name__)

DDL_DIVERGENCE_PHENOLOGIE = """
CREATE TABLE IF NOT EXISTS derived.divergence (
    id_parcel             text PRIMARY KEY,
    dist_classe           double precision,
    seuil_div             double precision,
    divergent             boolean,
    dist_raccord          double precision,
    zone_raccord_orbital  boolean,
    version_pipeline      text NOT NULL,
    date_calcul           timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS derived.phenologie (
    id_parcel         text PRIMARY KEY,
    sos_date          date,
    pos_date          date,
    eos_date          date,
    los_jours         integer,
    sos_en_bord       boolean,
    eos_en_bord       boolean,
    pos_en_bord       boolean,
    fiable            boolean,
    lambda_whittaker  double precision,
    version_pipeline  text NOT NULL,
    date_calcul       timestamp NOT NULL DEFAULT now()
);
"""


def creer_tables_phenologie() -> None:
    """Crée `derived.divergence` et `derived.phenologie` si absentes (portage cellule 18)."""
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL_DIVERGENCE_PHENOLOGIE)
        conn.commit()
    logger.info("Tables derived.divergence et derived.phenologie prêtes.")


def to_native(v):
    """Convertit un scalaire numpy/pandas en type Python natif pour psycopg2
    (portage cellule 19).

    `numpy.int64`/`numpy.bool_` ne sont pas adaptables tels quels — échec
    sur `los_jours`/les colonnes booléennes après `.where(..., None)` (qui
    force le dtype `object`, où les valeurs restent "boxées" en numpy).
    `numpy.float64` hérite de `float` et passe déjà nativement.
    """
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def upsert_divergence(df: pd.DataFrame, version_pipeline: str) -> int:
    """Upsert `derived.divergence` (portage cellule 19). Retourne le nombre
    de lignes upsertées.
    """
    cols_div = [
        "id_parcel",
        "dist_classe",
        "seuil_div",
        "divergent",
        "dist_raccord",
        "zone_raccord_orbital",
    ]
    df_divergence = df[cols_div].copy()
    df_divergence["version_pipeline"] = version_pipeline
    df_divergence = df_divergence.where(pd.notna(df_divergence), None)

    rows = list(df_divergence.itertuples(index=False, name=None))
    rows = [tuple(to_native(v) for v in row) for row in rows]

    # Garde-fou : rows doit correspondre au schéma de derived.divergence.
    n_cols_attendu = len(cols_div) + 1  # + version_pipeline
    if rows and len(rows[0]) != n_cols_attendu:
        raise ValueError(
            f"rows_div ne correspond pas à derived.divergence : "
            f"{len(rows[0])} valeurs pour {n_cols_attendu} colonnes attendues."
        )

    sql = """
        INSERT INTO derived.divergence
            (id_parcel, dist_classe, seuil_div, divergent,
             dist_raccord, zone_raccord_orbital, version_pipeline)
        VALUES %s
        ON CONFLICT (id_parcel) DO UPDATE SET
            dist_classe = EXCLUDED.dist_classe,
            seuil_div = EXCLUDED.seuil_div,
            divergent = EXCLUDED.divergent,
            dist_raccord = EXCLUDED.dist_raccord,
            zone_raccord_orbital = EXCLUDED.zone_raccord_orbital,
            version_pipeline = EXCLUDED.version_pipeline,
            date_calcul = now()
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=5000)
        conn.commit()

    logger.info("derived.divergence : %s lignes upsertées.", f"{len(rows):,}")
    return len(rows)


def upsert_phenologie(
    df_pheno: pd.DataFrame, lambda_whittaker: float, version_pipeline: str
) -> int:
    """Upsert `derived.phenologie` (portage cellule 20). Retourne le nombre
    de lignes upsertées.
    """
    cols_pheno = [
        "id_parcel",
        "sos_date",
        "pos_date",
        "eos_date",
        "los_jours",
        "sos_en_bord",
        "eos_en_bord",
        "pos_en_bord",
        "fiable",
    ]
    df_phenologie = df_pheno[cols_pheno].copy()
    df_phenologie["lambda_whittaker"] = lambda_whittaker
    df_phenologie["version_pipeline"] = version_pipeline

    # Types adaptés à la DDL : dates -> date (pas Timestamp), los_jours -> Int64
    for c in ["sos_date", "pos_date", "eos_date"]:
        df_phenologie[c] = df_phenologie[c].dt.date
    df_phenologie["los_jours"] = df_phenologie["los_jours"].astype("Int64")
    df_phenologie = df_phenologie.where(pd.notna(df_phenologie), None)

    rows = list(df_phenologie.itertuples(index=False, name=None))
    rows = [tuple(to_native(v) for v in row) for row in rows]

    n_cols_attendu = len(cols_pheno) + 2  # + lambda_whittaker + version_pipeline
    if rows and len(rows[0]) != n_cols_attendu:
        raise ValueError(
            f"rows_pheno ne correspond pas à derived.phenologie : "
            f"{len(rows[0])} valeurs pour {n_cols_attendu} colonnes attendues."
        )

    sql = """
        INSERT INTO derived.phenologie
            (id_parcel, sos_date, pos_date, eos_date, los_jours,
             sos_en_bord, eos_en_bord, pos_en_bord, fiable, lambda_whittaker, version_pipeline)
        VALUES %s
        ON CONFLICT (id_parcel) DO UPDATE SET
            sos_date = EXCLUDED.sos_date,
            pos_date = EXCLUDED.pos_date,
            eos_date = EXCLUDED.eos_date,
            los_jours = EXCLUDED.los_jours,
            sos_en_bord = EXCLUDED.sos_en_bord,
            eos_en_bord = EXCLUDED.eos_en_bord,
            pos_en_bord = EXCLUDED.pos_en_bord,
            fiable = EXCLUDED.fiable,
            lambda_whittaker = EXCLUDED.lambda_whittaker,
            version_pipeline = EXCLUDED.version_pipeline,
            date_calcul = now()
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=5000)
        conn.commit()

    logger.info("derived.phenologie : %s lignes upsertées.", f"{len(rows):,}")
    return len(rows)


def verifier_chargement(n_attendu: int | None = None) -> dict:
    """Vérification post-chargement (portage cellule 21).

    `n_attendu` : vérification optionnelle (le notebook figeait `77932` en
    dur — spécifique à la campagne actuelle, cf. `src/ml/features.py` pour
    le même principe sur `n_features_attendu`). Lève `ValueError` si fourni
    et ne correspond pas.

    Retourne `{"n_divergence", "n_phenologie", "n_divergentes", "n_fiables"}`.
    """
    with connexion() as conn:
        n_div = int(
            pd.read_sql("SELECT count(*) AS n FROM derived.divergence", conn)["n"].iloc[
                0
            ]
        )
        n_pheno = int(
            pd.read_sql("SELECT count(*) AS n FROM derived.phenologie", conn)["n"].iloc[
                0
            ]
        )
        n_div_flag = int(
            pd.read_sql(
                "SELECT count(*) AS n FROM derived.divergence WHERE divergent", conn
            )["n"].iloc[0]
        )
        n_pheno_fiable = int(
            pd.read_sql(
                "SELECT count(*) AS n FROM derived.phenologie WHERE fiable", conn
            )["n"].iloc[0]
        )

    logger.info(
        "derived.divergence : %s lignes, dont %s divergentes. derived.phenologie : %s lignes, dont %s fiables.",
        f"{n_div:,}",
        f"{n_div_flag:,}",
        f"{n_pheno:,}",
        f"{n_pheno_fiable:,}",
    )

    if n_attendu is not None:
        if n_div != n_attendu:
            raise ValueError(
                f"Écart sur derived.divergence : {n_div} lignes, {n_attendu} attendues."
            )
        if n_pheno != n_attendu:
            raise ValueError(
                f"Écart sur derived.phenologie : {n_pheno} lignes, {n_attendu} attendues."
            )

    return {
        "n_divergence": n_div,
        "n_phenologie": n_pheno,
        "n_divergentes": n_div_flag,
        "n_fiables": n_pheno_fiable,
    }
