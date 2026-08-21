"""DAG B : agrégation zonale, classification ML, divergence et phénologie.

Déclenchement manuel (`schedule=None`) : dépend du millésime RPG de la
campagne, publié une fois par an avec un délai d'environ un an après la
fin de la campagne observée (vérifié, cf. `methode.md`, Conception du DAG
Airflow). Pas de cron automatique tant que la date de publication n'est
pas prévisible avec certitude, à déclencher manuellement (UI Airflow ou
API) une fois la publication constatée.

Suppose que `seinecrops_acquisition_s2` (DAG A) a déjà tourné et produit
les composites mensuels nécessaires.

Chaîne : `ingestion_rpg` puis, en parallèle, `stats_zonales_mensuelles`,
`completude_zonale`, `ndvi_zonale_dates`, puis, en parallèle aussi,
`entrainement_ml` et `divergence_phenologie`.

⚠️ Correction par rapport au graphe initialement dessiné en discussion
(`methode.md`) : `entrainement_ml` et `divergence_phenologie` sont en
réalité des tâches parallèles, pas séquentielles, `divergence_phenologie`
ne dépend pas du modèle ML entraîné (§5.1 lit directement
`derived.s2_parcelles_monthly`/`derived.rpg_parcelles_aoi` via
`src.ml.features`, jamais les prédictions), seulement des mêmes tables
sources que le ML. Corrigé en écrivant ce fichier, à répercuter dans
`methode.md`.
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task

from src import config


@dag(
    dag_id="seinecrops_zonal_ml_phenologie",
    schedule=None,  # déclenchement manuel uniquement (UI Airflow ou API)
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["seinecrops", "zonal", "ml", "phenologie"],
)
def seinecrops_zonal_ml_phenologie():
    @task
    def ingestion_rpg() -> None:
        """§1 : ingestion RPG complète (nouveau millésime publié)."""
        from src.acquisition.orchestration import run_rpg

        run_rpg()

    @task
    def stats_zonales_mensuelles(_rpg_done: None) -> None:
        """§3.4 : stats zonales des composites mensuels.

        `_rpg_done` n'est pas utilisée dans le corps de la tâche, elle sert
        uniquement à créer la dépendance sur `ingestion_rpg` : Airflow
        accepte de passer la valeur de retour d'une tâche (même `None`) en
        paramètre d'une autre, juste pour encoder l'ordre d'exécution, pas
        seulement pour transmettre une donnée utile.
        """
        import geopandas as gpd

        from src.processing import grid
        from src.processing.orchestration import run_zonal_composites

        aoi = gpd.read_file(config.AOI_GEOJSON)
        grille = grid.calculer_grille_aoi(aoi)
        run_zonal_composites({"grille": grille})

    @task
    def completude_zonale(_rpg_done: None) -> None:
        """§3.5 : stats zonales de complétude.

        Recharge le catalogue déjà produit par le DAG A pour retrouver
        `mois_complets`, seule dépendance de données réelle en plus de
        `ingestion_rpg`.
        """
        import geopandas as gpd
        import pandas as pd

        from src.processing import grid
        from src.processing.orchestration import (
            determiner_mois_complets,
            preparer_scenes_retenues,
            run_zonal_completude,
        )

        df_dedup = pd.read_parquet(config.DATA_RAW_S2 / "catalogue_dedup.parquet")
        df_retenues = preparer_scenes_retenues(df_dedup)
        mois_complets = determiner_mois_complets(df_retenues)

        aoi = gpd.read_file(config.AOI_GEOJSON)
        grille = grid.calculer_grille_aoi(aoi)
        run_zonal_completude({"grille": grille}, mois_complets)

    @task
    def ndvi_zonale_dates(_rpg_done: None) -> None:
        """§3.6 : NDVI zonal aux dates d'acquisition."""
        import geopandas as gpd

        from src.processing import grid
        from src.processing.orchestration import run_zonal_ndvi_dates

        aoi = gpd.read_file(config.AOI_GEOJSON)
        grille = grid.calculer_grille_aoi(aoi)
        run_zonal_ndvi_dates({"grille": grille})

    @task(pool="ml_intensif")
    def entrainement_ml(_stats_done: None, _completude_done: None) -> None:
        """§4.1-4.4 : classification ML.

        `skip_search=True` par défaut dans le DAG (modèle baseline, pas de
        `RandomizedSearchCV`) : le tuning surapprend sans gain mesurable
        (déjà vérifié empiriquement, cf. `methode.md`) et coûte plusieurs
        heures. Le tuning corrigé (`StratifiedGroupKFold`, décrit mais pas
        implémenté) pourra être ajouté comme tâche manuelle séparée plus
        tard, pas dans le flux automatique par défaut.
        """
        from src.ml import orchestration as orch

        df_wide, _ = orch.preparer_feature_set()
        df_wide = orch.appliquer_split_spatial(df_wide)
        resultat = orch.entrainer_et_evaluer(df_wide, skip_search=True)
        orch.sauvegarder_predictions(
            df_wide,
            resultat["modele_final"],
            resultat["matrices"]["feature_cols"],
            "rf_base",
        )

    @task(pool="ml_intensif")
    def divergence_phenologie(
        _stats_done: None, _completude_done: None, _ndvi_done: None
    ) -> None:
        """§5.1-5.4 : divergence et phénologie.

        Dépend des 3 tâches zonales, mais PAS de `entrainement_ml` (cf.
        note de module) : les deux tâches tournent en parallèle.
        """
        from src.phenology import orchestration as orch

        df = orch.preparer_feature_set()
        df = orch.calculer_divergence(df)
        df_pheno = orch.extraire_phenologie_pipeline(df)
        orch.persister_resultats(df, df_pheno)

    rpg_done = ingestion_rpg()
    stats_done = stats_zonales_mensuelles(rpg_done)
    completude_done = completude_zonale(rpg_done)
    ndvi_done = ndvi_zonale_dates(rpg_done)

    entrainement_ml(stats_done, completude_done)
    divergence_phenologie(stats_done, completude_done, ndvi_done)


seinecrops_zonal_ml_phenologie()
