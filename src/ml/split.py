"""Split spatial par blocs — portage de `04_classification.ipynb` §4.2.

Découpage train/test réduisant la fuite spatiale : un split aléatoire
placerait des parcelles voisines — partageant le même contexte
pédo-climatique et des profils temporels corrélés — de part et d'autre de
la frontière train/test, gonflant artificiellement les métriques
d'évaluation. L'AOI est découpée en blocs géographiques réguliers, chaque
bloc affecté intégralement au train ou au test.

**Limitation déjà documentée dans `methode.md`, non corrigée ici** (hors
périmètre de cette migration) : `RandomizedSearchCV` (`train.py`) est
aveugle à ce découpage par blocs — sa validation croisée interne (`KFold`
classique) remélange des parcelles voisines. Une vraie prise en compte
nécessiterait `GroupKFold` avec les identifiants de bloc.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.db.connection import connexion

logger = logging.getLogger(__name__)

BLOCK_SIZE = 10_000  # 10 km — compromis : trop petit efface l'effet de bloc, trop grand augmente la variance du split
TEST_RATIO = 0.20
SEED = 42


def charger_centroides(index_reference: pd.Index) -> pd.DataFrame:
    """Charge les centroïdes des parcelles depuis `derived.rpg_parcelles_aoi`,
    restreints aux parcelles présentes dans le feature set (portage cellule 13).
    """
    with connexion() as conn:
        df_centr = pd.read_sql(
            "SELECT id_parcel, ST_X(ST_Centroid(geom)) AS cx, ST_Y(ST_Centroid(geom)) AS cy "
            "FROM derived.rpg_parcelles_aoi",
            conn,
        )
    df_centr = df_centr.drop_duplicates(subset="id_parcel", keep="first")
    df_centr = df_centr[df_centr["id_parcel"].isin(index_reference)]

    logger.info(
        "Centroïdes : %s parcelles, emprise X %.0f→%.0f m, Y %.0f→%.0f m",
        f"{len(df_centr):,}",
        df_centr["cx"].min(),
        df_centr["cx"].max(),
        df_centr["cy"].min(),
        df_centr["cy"].max(),
    )
    return df_centr


def split_spatial_par_blocs(
    df_centr: pd.DataFrame,
    block_size: float = BLOCK_SIZE,
    test_ratio: float = TEST_RATIO,
    seed: int = SEED,
) -> pd.DataFrame:
    """Affecte chaque parcelle à un bloc géographique (grille carrée alignée
    sur l'emprise), tire aléatoirement les blocs test (portage cellule 14).

    Retourne `df_centr` avec les colonnes `block_id`, `split` ajoutées.
    """
    df_centr = df_centr.copy()
    x_min, y_min = df_centr["cx"].min(), df_centr["cy"].min()
    df_centr["block_x"] = ((df_centr["cx"] - x_min) // block_size).astype(int)
    df_centr["block_y"] = ((df_centr["cy"] - y_min) // block_size).astype(int)
    df_centr["block_id"] = (
        df_centr["block_x"].astype(str) + "_" + df_centr["block_y"].astype(str)
    )

    blocks = df_centr["block_id"].unique()
    rng = np.random.default_rng(seed)
    n_test_blocks = max(1, int(len(blocks) * test_ratio))
    test_blocks = set(rng.choice(blocks, size=n_test_blocks, replace=False))

    df_centr["split"] = df_centr["block_id"].apply(
        lambda b: "test" if b in test_blocks else "train"
    )

    n_train = int((df_centr["split"] == "train").sum())
    n_test = int((df_centr["split"] == "test").sum())
    logger.info(
        "Blocs : %d total, %d test — Train %s (%.1f%%), Test %s (%.1f%%)",
        len(blocks),
        n_test_blocks,
        f"{n_train:,}",
        100 * n_train / len(df_centr),
        f"{n_test:,}",
        100 * n_test / len(df_centr),
    )
    return df_centr


def joindre_split(df_wide: pd.DataFrame, df_centr: pd.DataFrame) -> pd.DataFrame:
    """Joint la colonne `split` à `df_wide` (portage fin cellule 14, idempotent)."""
    df_wide = df_wide.drop(columns=["split"], errors="ignore")
    df_wide = df_wide.join(df_centr.set_index("id_parcel")[["split"]], on="id_parcel")
    return df_wide


def verifier_representation_classes(df_wide: pd.DataFrame) -> pd.DataFrame:
    """Table croisée classe × split, avec `pct_test` par classe (portage cellule 15).

    À inspecter avant l'entraînement : une classe sous-représentée dans le
    test rend son F1 peu fiable, quelle que soit la qualité du modèle.
    """
    ct = pd.crosstab(df_wide["classe"], df_wide["split"])
    ct["total"] = ct.sum(axis=1)
    ct["pct_test"] = (100 * ct["test"] / ct["total"]).round(1)
    ct = ct.sort_values("total", ascending=False)

    logger.info("Représentation par classe :\n%s", ct.to_string())
    return ct
