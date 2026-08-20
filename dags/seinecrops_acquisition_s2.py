"""DAG A : acquisition et traitement Sentinel-2 (`@monthly`).

Ne dépend jamais du RPG : uniquement de l'imagerie Copernicus, disponible
en continu. Complète `seinecrops_zonal_ml_phenologie` (DAG B), qui lui
dépend du RPG publié annuellement (cf. `methode.md`, Conception du DAG
Airflow).

Chaîne complète :
`disponibilite_s2` puis `scl_f_valid_aoi` puis `traitement_bandes_indices`
puis `qc_fichiers` puis `nettoyage_intermediaires` (conditionnel) puis
`qc_couverture_temporelle` puis `composites_mensuels`. Les sept tâches
sont maintenant en place.
"""

from __future__ import annotations

import logging

import pendulum
from airflow.decorators import dag, task

from src import config


@dag(
    dag_id="seinecrops_acquisition_s2",
    schedule="@monthly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,  # ne pas rejouer tous les mois passés depuis start_date au premier déploiement
    max_active_runs=1,
    tags=["seinecrops", "s2", "acquisition"],
)
def seinecrops_acquisition_s2():
    @task
    def disponibilite_s2() -> str:
        """§2 : catalogue CDSE (4 tuiles), dédupliqué, sauvegardé en parquet.

        Retourne le CHEMIN du parquet, pas le DataFrame lui-même, cf.
        note ci-dessous sur XCom.
        """
        from src.acquisition import cdse

        token = cdse.get_cdse_token()
        raw_results = cdse.interroger_catalogue_complet(
            config.DATE_START, config.DATE_END, token
        )
        df = cdse.structurer_catalogue(raw_results)
        df_dedup = cdse.dedupliquer_catalogue(df)
        chemin = cdse.sauvegarder_catalogue(df_dedup, config.DATA_RAW_S2)
        return str(chemin)

    @task
    def scl_f_valid_aoi(chemin_catalogue: str) -> str:
        """§3.1 : masque SCL, calcul de `f_valid_aoi` par scène.

        Réutilise `run_scl()` de `scripts/run_processing.py`, déjà testé
        en conditions réelles cette session, plutôt que de réimplémenter
        sa logique ici. Seule adaptation nécessaire : reconstruire le
        `ctx` (DataFrame, AOI) à partir du chemin reçu par XCom, puisque
        `run_scl()` attend un dict avec ces objets, pas des chemins.
        """
        import geopandas as gpd
        import pandas as pd

        from src.processing.orchestration import run_scl

        ctx = {
            "df_dedup": pd.read_parquet(chemin_catalogue),
            "aoi": gpd.read_file(config.AOI_GEOJSON),
        }
        run_scl(ctx)  # écrit le parquet mis à jour sur place, génère les diagnostics
        return chemin_catalogue

    @task
    def traitement_bandes_indices(chemin_catalogue: str) -> str:
        """§3.2 : téléchargement des bandes, calcul des indices.

        Réutilise `run_bands()`, comme pour `scl_f_valid_aoi`. Reconstruit
        `df_retenues` (filtre `f_valid_aoi`) depuis le catalogue rechargé,
        `preparer_scenes_retenues()` fait ce filtrage, elle aussi
        réutilisée plutôt que réimplémentée.
        """
        import geopandas as gpd
        import pandas as pd

        from src.processing.orchestration import preparer_scenes_retenues, run_bands

        df_dedup = pd.read_parquet(chemin_catalogue)
        df_retenues = preparer_scenes_retenues(df_dedup)
        ctx = {"aoi": gpd.read_file(config.AOI_GEOJSON)}

        run_bands(ctx, df_retenues)
        return chemin_catalogue  # inchangé, seuls les fichiers bandes/indices sur disque ont bougé

    @task
    def qc_fichiers(chemin_catalogue: str) -> bool:
        """§3.2 bis : QC de complétude des fichiers bandes/indices.

        Retourne un booléen (pas la liste de problèmes elle-même,
        potentiellement volumineuse pour XCom) : `True` si aucun problème
        détecté. `nettoyage_intermediaires` s'appuie sur cette valeur pour
        décider s'il doit s'exécuter, cf. sa docstring.
        """
        import pandas as pd

        from src.processing.orchestration import (
            preparer_scenes_retenues,
            run_qc_fichiers,
        )

        df_dedup = pd.read_parquet(chemin_catalogue)
        df_retenues = preparer_scenes_retenues(df_dedup)
        problemes = run_qc_fichiers(df_retenues)
        return len(problemes) == 0

    @task
    def nettoyage_intermediaires(qc_ok: bool) -> None:
        """Supprime les `.jp2` bruts, uniquement si `qc_fichiers` n'a détecté
        aucun problème.

        ⚠️ `qc_fichiers` ne lève jamais d'exception, même si elle trouve des
        problèmes, elle retourne juste une valeur. Le `trigger_rule` par
        défaut d'Airflow (`all_success`) ne bloquerait donc PAS cette tâche
        en cas de problème détecté : `qc_fichiers` aurait "réussi" du point
        de vue Airflow (pas planté), qu'elle ait trouvé 0 ou 50 problèmes.
        La vérification explicite du booléen ici, dans le corps de la
        tâche, est indispensable, pas seulement une précaution.
        """
        from src.processing import qc

        logger = logging.getLogger(__name__)
        if not qc_ok:
            logger.warning(
                "QC fichiers a détecté des problèmes, suppression des .jp2 sautée."
            )
            return

        n = qc.supprimer_jp2(config.DATA_RAW_S2_BANDS)
        logger.info("%d fichier(s) .jp2 supprimé(s).", n)

    @task
    def qc_couverture_temporelle(chemin_catalogue: str) -> dict:
        """§3.2 quater : QC de couverture temporelle mensuelle.

        Retourne un petit dictionnaire (chemin + liste de mois complets),
        pas seulement le chemin : `composites_mensuels` a besoin des deux.
        Reste léger (une liste d'une quinzaine de chaînes), donc adapté à
        XCom, contrairement à un DataFrame ou une grille complète.
        """
        import geopandas as gpd
        import pandas as pd

        from src.processing import grid
        from src.processing.orchestration import (
            determiner_mois_complets,
            preparer_scenes_retenues,
            run_qc_couverture,
        )

        df_dedup = pd.read_parquet(chemin_catalogue)
        df_retenues = preparer_scenes_retenues(df_dedup)
        aoi = gpd.read_file(config.AOI_GEOJSON)
        grille = grid.calculer_grille_aoi(aoi)

        mois_complets = determiner_mois_complets(df_retenues)
        run_qc_couverture({"grille": grille}, df_retenues, mois_complets)

        return {"chemin_catalogue": chemin_catalogue, "mois_complets": mois_complets}

    @task
    def composites_mensuels(contexte: dict) -> None:
        """§3.3 : composites mensuels, dernière tâche de ce DAG."""
        import geopandas as gpd
        import pandas as pd

        from src.processing import grid
        from src.processing.orchestration import (
            preparer_scenes_retenues,
            run_composites,
        )

        df_dedup = pd.read_parquet(contexte["chemin_catalogue"])
        df_retenues = preparer_scenes_retenues(df_dedup)
        aoi = gpd.read_file(config.AOI_GEOJSON)
        grille = grid.calculer_grille_aoi(aoi)

        run_composites({"grille": grille}, df_retenues, contexte["mois_complets"])

    chemin_catalogue = disponibilite_s2()
    chemin_catalogue = scl_f_valid_aoi(chemin_catalogue)
    chemin_catalogue = traitement_bandes_indices(chemin_catalogue)
    qc_ok = qc_fichiers(chemin_catalogue)
    nettoyage_intermediaires(qc_ok)
    contexte_composites = qc_couverture_temporelle(chemin_catalogue)
    composites_mensuels(contexte_composites)


seinecrops_acquisition_s2()
