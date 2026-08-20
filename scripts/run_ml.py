"""Exécution manuelle de la chaîne de classification (§4.1 à §4.4).

Enveloppe CLI fine autour de `src.ml.orchestration` (analyse des
arguments, configuration du logging), la logique elle-même vit dans
`src/`, réutilisable telle quelle par le DAG Airflow.

Usage :
    python -m scripts.run_ml
    python -m scripts.run_ml --skip-search   # baseline uniquement, pas de RandomizedSearchCV
"""

from __future__ import annotations

import argparse
import logging

from src.ml import orchestration as orch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Sauter RandomizedSearchCV, modèle final = baseline (plus rapide)",
    )
    args = parser.parse_args()

    df_wide, _ = orch.preparer_feature_set()
    df_wide = orch.appliquer_split_spatial(df_wide)
    resultat_entrainement = orch.entrainer_et_evaluer(df_wide, args.skip_search)

    version_prefix = "rf_base" if args.skip_search else "rf_tuned"
    orch.sauvegarder_predictions(
        df_wide,
        resultat_entrainement["modele_final"],
        resultat_entrainement["matrices"]["feature_cols"],
        version_prefix,
    )

    logging.getLogger("run_ml").info("=== Classification terminée ===")


if __name__ == "__main__":
    main()
