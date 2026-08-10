"""Artefacts de diagnostics optionnels, versionnés par run.

Nouveau en S6 (pas un portage direct d'un notebook). Objectif : conserver
la valeur des contrôles visuels pratiqués dans les notebooks (histogramme
de disponibilité CDSE en `02_disponibilite_s2.ipynb` §2.5, futures
enveloppes phénologiques D10-D90...) sans les laisser hors de toute
automatisation — chaque run produit un dossier daté, jamais écrasé, et un
rapport HTML autonome qui les rassemble.

Non bloquant pour le pipeline : ces artefacts sont un complément
d'observabilité, pas une porte de validation. Une tâche Airflow qui les
génère peut échouer sans empêcher les tâches suivantes de s'exécuter (cf.
`methode.md` §S6, distinction QC visuelle / tests automatisés).

Emplacement : `data/diagnostics/{nom_module}/{run_id}/` — séparé de
`data/raw`/`data/derived` (données sources et dérivées) car ce sont des
artefacts reproductibles à volonté, pas des données à conserver.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.db.connection import PROJECT_ROOT

logger = logging.getLogger(__name__)

DIAGNOSTICS_ROOT = PROJECT_ROOT / "data" / "diagnostics"


@dataclass
class BlocDiag:
    """Un bloc de contenu du rapport HTML : soit une figure, soit un tableau.

    `contenu` porte le nom de fichier relatif au run_dir (figure) ou le
    HTML déjà rendu du tableau — jamais un chemin absolu, pour que le
    rapport reste portable si le dossier est déplacé/archivé.
    """

    titre: str
    type: str  # "figure" ou "tableau"
    contenu: str


def nouveau_run_diagnostic(nom_module: str, run_id: str | None = None) -> Path:
    """Crée `data/diagnostics/{nom_module}/{run_id}/` et la retourne.

    `run_id` par défaut : horodatage UTC `YYYYMMDD_HHMMSS`, pour ne jamais
    écraser un run précédent et rester utilisable hors Airflow (notebook,
    exécution manuelle). Un `run_id` explicite peut être passé pour aligner
    sur l'identifiant de run Airflow si besoin.
    """
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    run_dir = DIAGNOSTICS_ROOT / nom_module / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Run de diagnostics créé : %s", run_dir)
    return run_dir


def sauvegarder_figure(fig, nom: str, run_dir: Path) -> Path:
    """Sauvegarde une figure matplotlib en PNG dans `run_dir`.

    `nom` sans extension (ex. "disponibilite_mensuelle") — `.png` est ajouté.
    """
    dest = run_dir / f"{nom}.png"
    fig.savefig(dest, dpi=150, bbox_inches="tight")
    logger.info("Figure sauvegardée : %s", dest)
    return dest


def ajouter_figure(fig, nom: str, titre: str, run_dir: Path) -> BlocDiag:
    """Sauvegarde une figure et retourne le bloc correspondant pour le rapport."""
    dest = sauvegarder_figure(fig, nom, run_dir)
    return BlocDiag(titre=titre, type="figure", contenu=dest.name)


def ajouter_tableau(df: pd.DataFrame, titre: str) -> BlocDiag:
    """Retourne le bloc rapport pour un DataFrame, rendu en table HTML."""
    html = df.to_html(index=True, border=0, classes="tableau-diag")
    return BlocDiag(titre=titre, type="tableau", contenu=html)


def rendre_rapport_html(
    run_dir: Path,
    titre: str,
    blocs: list[BlocDiag],
    metriques: dict,
) -> Path:
    """Assemble `diagnostics.html` dans `run_dir` : un fichier autonome
    (CSS inline, aucune dépendance JS/CDN) listant les métriques clés puis
    chaque bloc (figure ou tableau) dans l'ordre fourni.
    """
    lignes_metriques = "\n".join(
        f"<tr><td>{cle}</td><td>{valeur}</td></tr>" for cle, valeur in metriques.items()
    )

    blocs_html = []
    for bloc in blocs:
        if bloc.type == "figure":
            blocs_html.append(
                f"<section><h2>{bloc.titre}</h2>"
                f'<img src="{bloc.contenu}" alt="{bloc.titre}"></section>'
            )
        elif bloc.type == "tableau":
            blocs_html.append(f"<section><h2>{bloc.titre}</h2>{bloc.contenu}</section>")
        else:
            raise ValueError(f"Type de bloc inconnu : {bloc.type!r}")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{titre}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  table {{ border-collapse: collapse; margin-top: 0.5rem; }}
  td, th {{ padding: 0.3rem 0.8rem; border-bottom: 1px solid #ddd; text-align: left; }}
  img {{ max-width: 100%; margin-top: 0.5rem; }}
  .meta {{ color: #666; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>{titre}</h1>
<p class="meta">Généré le {datetime.now(timezone.utc).isoformat()}</p>
<table>
{lignes_metriques}
</table>
{''.join(blocs_html)}
</body>
</html>
"""

    dest = run_dir / "diagnostics.html"
    dest.write_text(html, encoding="utf-8")
    logger.info("Rapport de diagnostics écrit : %s", dest)
    return dest
