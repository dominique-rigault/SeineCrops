"""Exécution manuelle de la chaîne de traitement Sentinel-2 (§3.1 à §3.6).

Enveloppe CLI fine autour de `src.processing.orchestration` (analyse des
arguments, configuration du logging), la logique elle-même vit dans
`src/`, réutilisable telle quelle par le DAG Airflow.

Usage :
    python -m scripts.run_processing
    python -m scripts.run_processing --skip-scl --skip-bands --skip-composites --skip-zonal
"""

from __future__ import annotations

import argparse
import logging

from src.processing import orchestration as orch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-scl", action="store_true", help="Sauter §3.1 (SCL/f_valid_aoi)"
    )
    parser.add_argument(
        "--skip-bands",
        action="store_true",
        help="Sauter §3.2 (bandes/indices) et son QC",
    )
    parser.add_argument(
        "--skip-composites",
        action="store_true",
        help="Sauter §3.2quater (QC couverture) et §3.3 (composites)",
    )
    parser.add_argument(
        "--skip-zonal",
        action="store_true",
        help="Sauter §3.4/§3.5/§3.6 (agrégation zonale)",
    )
    args = parser.parse_args()

    ctx = orch.charger_contexte()

    if not args.skip_scl:
        ctx["df_dedup"] = orch.run_scl(ctx)

    df_retenues = orch.preparer_scenes_retenues(ctx["df_dedup"])

    if not args.skip_bands:
        orch.run_bands(ctx, df_retenues)
        orch.run_qc_fichiers(df_retenues)

    mois_complets = orch.determiner_mois_complets(df_retenues)

    if not args.skip_composites:
        orch.run_qc_couverture(ctx, df_retenues, mois_complets)
        orch.run_composites(ctx, df_retenues, mois_complets)

    if not args.skip_zonal:
        orch.run_zonal(ctx, mois_complets)

    logging.getLogger("run_processing").info("=== Traitement terminé ===")


if __name__ == "__main__":
    main()
