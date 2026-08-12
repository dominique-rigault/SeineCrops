"""Entraînement Random Forest — portage de `04_classification.ipynb` §4.3.

Baseline de classification : Random Forest scikit-learn sur le feature set
préparé (`features.py`/`imputation.py`/`split.py`). Aucune normalisation
n'est nécessaire — Random Forest est invariant aux échelles des features.

**Limitation déjà documentée dans `methode.md`, non corrigée ici** (hors
périmètre de cette migration) : `rechercher_hyperparametres` utilise une
validation croisée `KFold` classique (`cv=3`), aveugle au split spatial
par blocs de `split.py` — une vraie prise en compte nécessiterait
`GroupKFold` avec les identifiants de bloc plutôt qu'un `cv` entier.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import RandomizedSearchCV

from src.reporting.diagnostics import (
    ajouter_figure,
    ajouter_tableau,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)

SEED = 42

RF_PARAMS_BASELINE = dict(
    n_estimators=300,
    max_depth=30,
    min_samples_leaf=5,
    max_features="sqrt",
    class_weight="balanced",
    random_state=SEED,
    n_jobs=-1,
    oob_score=True,  # estimateur de généralisation quasi gratuit (chaque arbre évalué
    # sur les échantillons qu'il n'a pas vus) — comparer à l'accuracy
    # test donne un signal plus direct que le seul écart train/test
    # pour juger d'un éventuel surapprentissage.
)

PARAM_DIST_SEARCH = {
    "n_estimators": [200, 400, 600],
    "max_depth": [20, 30, 40],
    "min_samples_leaf": [2, 5, 10],
    "max_features": ["sqrt", 0.1, 0.2],
}


def construire_matrices(df_wide: pd.DataFrame) -> dict:
    """Construit `X_train`/`X_test`/`y_train`/`y_test` à partir de `df_wide`
    (colonnes `classe`/`split` déjà présentes — cf. `features.py`/`split.py`).
    Portage cellule 17.

    Retourne `{"X_train", "X_test", "y_train", "y_test", "feature_cols"}`.
    """
    feature_cols = [c for c in df_wide.columns if c not in ("classe", "split")]
    train_mask = df_wide["split"] == "train"
    test_mask = df_wide["split"] == "test"

    X_train = df_wide.loc[train_mask, feature_cols].values
    X_test = df_wide.loc[test_mask, feature_cols].values
    y_train = df_wide.loc[train_mask, "classe"].to_numpy()
    y_test = df_wide.loc[test_mask, "classe"].to_numpy()

    logger.info(
        "X_train %s, X_test %s, classes %s",
        X_train.shape,
        X_test.shape,
        sorted(np.unique(y_train).tolist()),
    )
    return {
        "X_train": X_train,
        "X_test": X_test,
        "y_train": y_train,
        "y_test": y_test,
        "feature_cols": feature_cols,
    }


def entrainer_rf_baseline(
    matrices: dict, params: dict = RF_PARAMS_BASELINE
) -> RandomForestClassifier:
    """Entraîne un Random Forest baseline, hyperparamètres par défaut (portage cellule 18)."""
    rf = RandomForestClassifier(**params)
    rf.fit(matrices["X_train"], matrices["y_train"])

    score_train = rf.score(matrices["X_train"], matrices["y_train"])
    score_test = rf.score(matrices["X_test"], matrices["y_test"])
    if params.get("oob_score"):
        logger.info(
            "Accuracy train : %.4f, OOB : %.4f, test : %.4f — écart OOB→test signale la "
            "généralisation géographique (blocs disjoints), écart train→OOB un éventuel surapprentissage",
            score_train,
            rf.oob_score_,
            score_test,
        )
    else:
        logger.info("Accuracy train : %.4f, test : %.4f", score_train, score_test)
    return rf


def evaluer_modele(modele: RandomForestClassifier, matrices: dict) -> dict:
    """Matrice de confusion + rapport de classification.

    Portage des cellules 19/21, **généralisée** en une seule fonction
    réutilisable pour le modèle baseline et le modèle tuné (le notebook
    dupliquait la même logique d'évaluation dans les deux cellules).

    Retourne `{"accuracy_train", "accuracy_test", "confusion_matrix"
    (DataFrame), "classification_report" (dict), "y_pred"}`.
    """
    y_test = matrices["y_test"]
    y_pred = modele.predict(matrices["X_test"])
    classes = sorted(np.unique(matrices["y_train"]).tolist())

    cm = confusion_matrix(y_test, y_pred, labels=classes)
    cm_df = pd.DataFrame(cm, index=classes, columns=classes)
    report = classification_report(y_test, y_pred, digits=3, output_dict=True)

    accuracy_train = modele.score(matrices["X_train"], matrices["y_train"])
    accuracy_test = modele.score(matrices["X_test"], matrices["y_test"])

    logger.info(
        "Accuracy train : %.4f, test : %.4f\n%s\n%s",
        accuracy_train,
        accuracy_test,
        cm_df.to_string(),
        classification_report(y_test, y_pred, digits=3),
    )
    return {
        "accuracy_train": accuracy_train,
        "accuracy_test": accuracy_test,
        "confusion_matrix": cm_df,
        "classification_report": report,
        "y_pred": y_pred,
    }


def rechercher_hyperparametres(
    matrices: dict,
    param_dist: dict = PARAM_DIST_SEARCH,
    n_iter: int = 20,
    cv: int = 3,
    seed: int = SEED,
) -> RandomizedSearchCV:
    """`RandomizedSearchCV` sur le train uniquement (portage cellule 20).

    ⚠️ `cv` (validation croisée `KFold` classique) est aveugle au split
    spatial par blocs — cf. docstring de module. Le jeu test n'est jamais
    utilisé ici, seulement à l'évaluation finale via `evaluer_modele`.
    """
    search = RandomizedSearchCV(
        RandomForestClassifier(random_state=seed, n_jobs=-1),
        param_distributions=param_dist,
        n_iter=n_iter,
        scoring="f1_macro",
        cv=cv,
        random_state=seed,
        n_jobs=1,  # parallélisme au niveau du RF, pas du CV
        verbose=2,
    )
    search.fit(matrices["X_train"], matrices["y_train"])

    logger.info(
        "Meilleur F1 macro (CV) : %.4f, meilleurs paramètres : %s",
        search.best_score_,
        search.best_params_,
    )
    return search


def top_features_importance(
    modele: RandomForestClassifier, feature_cols: list[str], n: int = 20
) -> pd.Series:
    """Top `n` features par importance décroissante (portage fin cellule 21)."""
    importances = pd.Series(modele.feature_importances_, index=feature_cols)
    top = importances.nlargest(n)
    logger.info("Top %d features :\n%s", n, top.to_string())
    return top


def generer_diagnostics_modele(
    resultats_eval: dict,
    modele: RandomForestClassifier | None = None,
    feature_cols: list[str] | None = None,
    top_n_features: int = 20,
    nom_module: str = "ml_evaluation",
) -> Path:
    """Rapport de diagnostics HTML pour un modèle évalué : matrice de
    confusion (heatmap + tableau), rapport de classification, top features
    si le modèle est fourni.

    Nouveau en S6, pas un portage direct — répond au besoin de consulter
    les performances au fil des runs (comparaison baseline/tuné, suivi
    dans le temps), sur le même principe que les autres diagnostics déjà
    en place (`reconnaissance`, `disponibilité`, QC `processing`).
    """
    import matplotlib.pyplot as plt

    run_dir = nouveau_run_diagnostic(nom_module)
    cm_df = resultats_eval["confusion_matrix"]
    report = resultats_eval["classification_report"]

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(cm_df.values, cmap="Blues")
    ax.set_xticks(range(len(cm_df.columns)))
    ax.set_yticks(range(len(cm_df.index)))
    ax.set_xticklabels(cm_df.columns, rotation=45, ha="right")
    ax.set_yticklabels(cm_df.index)
    ax.set_xlabel("Prédit")
    ax.set_ylabel("Réel")
    ax.set_title("Matrice de confusion")
    vmax = cm_df.values.max()
    for i in range(cm_df.shape[0]):
        for j in range(cm_df.shape[1]):
            val = cm_df.values[i, j]
            couleur = "white" if val > vmax / 2 else "black"
            ax.text(j, i, str(val), ha="center", va="center", color=couleur, fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()

    bloc_heatmap = ajouter_figure(
        fig, "matrice_confusion", "Matrice de confusion", run_dir
    )
    plt.close(fig)

    bloc_cm_table = ajouter_tableau(cm_df, "Matrice de confusion (détail)")
    df_report = pd.DataFrame(report).T.round(3)
    bloc_report = ajouter_tableau(df_report, "Rapport de classification")

    blocs = [bloc_heatmap, bloc_cm_table, bloc_report]
    if modele is not None and feature_cols is not None:
        top = top_features_importance(modele, feature_cols, n=top_n_features)
        bloc_features = ajouter_tableau(
            top.to_frame(name="importance"), f"Top {top_n_features} features"
        )
        blocs.append(bloc_features)

    metriques = {
        "Accuracy train": round(resultats_eval["accuracy_train"], 4),
        "Accuracy test": round(resultats_eval["accuracy_test"], 4),
        "F1 macro": round(report["macro avg"]["f1-score"], 4),
    }
    return rendre_rapport_html(run_dir, "Évaluation du modèle", blocs, metriques)
