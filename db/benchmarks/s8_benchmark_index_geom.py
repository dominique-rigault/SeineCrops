"""Benchmark avant/après : idx_rpg_parcelles_aoi_geom (S8).

Un seul changement isolé (gabarit §9) : l'index GIST sur
derived.rpg_parcelles_aoi.geom. Même requête bbox réelle (SQL_BBOX,
SQL_COUNT_BBOX — src/api/queries.py, celles utilisées par
GET /parcelles?bbox=), mesurée avant et après, sur la même connexion et
la même bbox.

Méthodologie :
    - "Après" : état actuel de la base (index en place).
    - "Avant" : DROP INDEX dans une transaction jamais commitée
      (conn.rollback() en finally, quoi qu'il arrive) — l'index n'est
      jamais retiré pour de vrai, même en cas de plantage du script.
    - Temps mesuré : "Execution Time" d'EXPLAIN (ANALYZE, FORMAT JSON),
      pas le round-trip Python (évite le bruit réseau/driver). 1 répétition
      de chauffe non comptée + N répétitions mesurées, médiane retenue
      (moins sensible aux outliers qu'une moyenne sur un petit échantillon).
    - PREPARE/EXECUTE plutôt que substitution %s de psycopg2, incompatible
      avec la syntaxe $n d'asyncpg utilisée dans src/api/queries.py — la
      requête testée est celle du fichier source, sans copie.

Usage (depuis la racine du dépôt) :
    python -m db.benchmarks.s8_benchmark_index_geom
"""

from __future__ import annotations

import json
import statistics

from src.api.queries import SQL_BBOX, SQL_COUNT_BBOX
from src.db.connection import connexion

N_REPETITIONS = 10
BUFFER_DEG = 0.01  # ~5 km² autour d'une parcelle réelle - fenêtre "village"


def bbox_reel(cur) -> tuple[float, float, float, float]:
    """Bbox EPSG:4326 centrée sur une parcelle réelle de l'AOI - même
    logique que tests/db/test_schema.py::bbox_reel, dupliquée ici plutôt
    que factorisée pour l'instant (à revoir si elle dérive un jour entre
    les deux fichiers)."""
    cur.execute(
        """
        SELECT ST_X(c), ST_Y(c) FROM (
            SELECT ST_Centroid(ST_Transform(geom, 4326)) AS c
            FROM derived.rpg_parcelles_aoi
            ORDER BY id_parcel
            LIMIT 1
        ) t
        """
    )
    lon, lat = cur.fetchone()
    return (lon - BUFFER_DEG, lat - BUFFER_DEG, lon + BUFFER_DEG, lat + BUFFER_DEG)


def index_geom_present(cur) -> bool:
    cur.execute(
        """
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'derived' AND tablename = 'rpg_parcelles_aoi'
            AND indexname = 'idx_rpg_parcelles_aoi_geom'
        """
    )
    return cur.fetchone() is not None


def temps_execution_ms(
    cur, prepare_name: str, types_params: list[str], sql: str, valeurs: tuple
) -> float:
    cur.execute(f"PREPARE {prepare_name} ({', '.join(types_params)}) AS {sql}")
    try:
        placeholders = ", ".join(str(v) for v in valeurs)
        cur.execute(
            f"EXPLAIN (ANALYZE, FORMAT JSON) EXECUTE {prepare_name}({placeholders})"
        )
        plan_json = cur.fetchone()[0]
        return plan_json[0]["Execution Time"]
    finally:
        cur.execute(f"DEALLOCATE {prepare_name}")


def mesurer_serie(
    cur, sql: str, types_params: list[str], valeurs: tuple, label: str
) -> list[float]:
    temps = []
    for i in range(N_REPETITIONS + 1):  # i == 0 : chauffe, non comptée
        t = temps_execution_ms(cur, f"bench_{label}_{i}", types_params, sql, valeurs)
        if i > 0:
            temps.append(t)
    return temps


def resumer(temps: list[float]) -> dict:
    return {
        "n": len(temps),
        "mediane_ms": round(statistics.median(temps), 3),
        "moyenne_ms": round(statistics.mean(temps), 3),
        "min_ms": round(min(temps), 3),
        "max_ms": round(max(temps), 3),
    }


def main() -> None:
    types_bbox = ["double precision"] * 4 + ["integer"]
    types_count = ["double precision"] * 4

    with connexion() as conn:
        with conn.cursor() as cur:
            assert index_geom_present(cur), (
                "idx_rpg_parcelles_aoi_geom absent avant même de commencer - "
                "le benchmark 'avant/après' n'a pas de sens sans état de "
                "départ connu. Vérifier les migrations 0001-0007."
            )
            bbox = bbox_reel(cur)
            print(f"Bbox de test (EPSG:4326) : {bbox}")

            # --- APRÈS : état actuel, index en place ---
            apres_bbox = mesurer_serie(
                cur, SQL_BBOX, types_bbox, (*bbox, 2000), "apres_bbox"
            )
            apres_count = mesurer_serie(
                cur, SQL_COUNT_BBOX, types_count, bbox, "apres_count"
            )
        conn.commit()  # clôt proprement la phase "après" (lecture seule, rien à valider)

        # --- AVANT : index retiré, transaction jamais commitée ---
        try:
            with conn.cursor() as cur:
                cur.execute("DROP INDEX derived.idx_rpg_parcelles_aoi_geom")
                avant_bbox = mesurer_serie(
                    cur, SQL_BBOX, types_bbox, (*bbox, 2000), "avant_bbox"
                )
                avant_count = mesurer_serie(
                    cur, SQL_COUNT_BBOX, types_count, bbox, "avant_count"
                )
        finally:
            conn.rollback()  # restaure l'index, y compris si une mesure a levé une exception

        with conn.cursor() as cur:
            assert index_geom_present(cur), (
                "ÉCHEC CRITIQUE : idx_rpg_parcelles_aoi_geom absent après rollback. "
                "Recréer manuellement immédiatement : "
                "CREATE INDEX idx_rpg_parcelles_aoi_geom ON derived.rpg_parcelles_aoi USING GIST (geom);"
            )
        print("Index restauré après le rollback - vérifié.")

    resultats = {
        "bbox_testee": bbox,
        "n_repetitions": N_REPETITIONS,
        "SQL_BBOX": {
            "avant_sans_index": resumer(avant_bbox),
            "apres_avec_index": resumer(apres_bbox),
        },
        "SQL_COUNT_BBOX": {
            "avant_sans_index": resumer(avant_count),
            "apres_avec_index": resumer(apres_count),
        },
    }
    print(json.dumps(resultats, indent=2, ensure_ascii=False))

    for nom, r in [
        ("SQL_BBOX", resultats["SQL_BBOX"]),
        ("SQL_COUNT_BBOX", resultats["SQL_COUNT_BBOX"]),
    ]:
        avant_med = r["avant_sans_index"]["mediane_ms"]
        apres_med = r["apres_avec_index"]["mediane_ms"]
        gain = avant_med / apres_med if apres_med else float("inf")
        print(f"{nom} : x{gain:.1f} (médiane {avant_med} ms -> {apres_med} ms)")


if __name__ == "__main__":
    main()
