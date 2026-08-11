"""Contrôle qualité géométrique, générique à toute table PostGIS du projet.

Portage de `01_ingestion_rpg.ipynb` §4.1bis. Les deux fonctions étaient déjà
génériques dans le notebook (`schema_table`, `col_geom` en paramètres) —
seul leur usage était local à l'ingestion RPG. Élevées ici en `src/db/`
plutôt que laissées dans `src/acquisition/rpg.py`, pour un futur usage par
d'autres modules (ex. `src/processing/`) sans dépendre d'`acquisition`.

Logging : chaque fonction logue son résultat via le module `logging`
standard, à la source du calcul — pas laissé à la charge de l'appelant
(qui pourrait l'omettre, comme c'était implicitement le cas dans le
notebook où seul un `print` explicite après l'appel affichait le résultat).
Capturé nativement par Airflow (log par tâche) sans infrastructure dédiée ;
distinct des rapports JSON (`INGESTION_REPORT.json`...), qui persistent le
résultat final plutôt que de tracer l'exécution.
"""

from __future__ import annotations

import logging

from .connection import connexion

logger = logging.getLogger(__name__)


def qa_validite(schema_table: str, col_geom: str) -> int:
    """Compte les géométries invalides dans une table. Retourne le nombre trouvé."""
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) FROM {schema_table}
                WHERE NOT ST_IsValid({col_geom});
                """
            )
            n_invalides = cur.fetchone()[0]

    if n_invalides:
        logger.warning(
            "%s : %d géométrie(s) invalide(s) détectée(s)", schema_table, n_invalides
        )
    else:
        logger.info("%s : aucune géométrie invalide", schema_table)
    return n_invalides


def reparer_si_necessaire(schema_table: str, col_geom: str, n_invalides: int) -> int:
    """Répare les géométries invalides via `ST_MakeValid` si nécessaire.

    Ne touche pas à la table si aucune invalidité n'est détectée — la
    réparation n'est appliquée qu'aux lignes concernées, pas en bloc sur
    toute la table. Retourne le nombre de géométries effectivement réparées.
    """
    if n_invalides == 0:
        logger.info("%s : aucune réparation nécessaire", schema_table)
        return 0

    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {schema_table}
                SET {col_geom} = ST_MakeValid({col_geom})
                WHERE NOT ST_IsValid({col_geom});
                """
            )
            n_reparees = cur.rowcount
            conn.commit()

    logger.warning(
        "%s : %d géométrie(s) réparée(s) via ST_MakeValid", schema_table, n_reparees
    )
    return n_reparees
