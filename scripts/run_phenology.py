"""Exécution manuelle de la chaîne divergence/phénologie (§5.1 à §5.4).

Enveloppe CLI fine autour de `src.phenology.orchestration` (configuration
du logging), la logique elle-même vit dans `src/`, réutilisable telle
quelle par le DAG Airflow.

Usage :
    python -m scripts.run_phenology
"""

from __future__ import annotations

import logging

from src.phenology import orchestration as orch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


def main() -> None:
    df = orch.preparer_feature_set()
    df = orch.calculer_divergence(df)
    df_pheno = orch.extraire_phenologie_pipeline(df)
    orch.persister_resultats(df, df_pheno)

    logging.getLogger("run_phenology").info("=== Divergence/phénologie terminé ===")


if __name__ == "__main__":
    main()
