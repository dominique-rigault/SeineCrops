"""Exécution manuelle de la chaîne d'acquisition (RPG + catalogue CDSE).

Enveloppe CLI fine autour de `src.acquisition.orchestration` (analyse des
arguments, configuration du logging), la logique elle-même vit dans
`src/`, réutilisable telle quelle par le DAG Airflow. Ce script reste
utile pour tester manuellement en dehors d'Airflow, mais n'a pas de
reprise sur échec tâche par tâche, juste un `try/except` global qui
arrête tout à la première erreur.

Usage :
    python -m scripts.run_ingestion
    python -m scripts.run_ingestion --skip-cdse   # RPG seul
    python -m scripts.run_ingestion --skip-rpg    # CDSE seul (nécessite un run RPG préalable)
"""

from __future__ import annotations

import argparse
import logging

from src.acquisition.orchestration import run_cdse, run_rpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-rpg", action="store_true", help="Sauter l'ingestion RPG"
    )
    parser.add_argument(
        "--skip-cdse", action="store_true", help="Sauter le catalogue CDSE"
    )
    args = parser.parse_args()

    if not args.skip_rpg:
        run_rpg()
    if not args.skip_cdse:
        run_cdse()


if __name__ == "__main__":
    main()
