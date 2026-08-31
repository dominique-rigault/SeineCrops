"""Ingestion du RPG dans PostGIS — portage de `01_ingestion_rpg.ipynb`.

Chaque fonction référence la section du notebook dont elle est portée, pour
garder la traçabilité (cf. `methode.md` §S6, règle de non-réouverture). Les
fonctions retournent des données structurées et logguent leur résultat via
`logging` — elles n'impriment rien elles-mêmes (cf. `methode.md` §Logging) ;
l'affichage éventuel est la responsabilité de l'appelant (notebook, tâche
Airflow, test).

Non porté depuis le notebook (reste notebook-only, diagnostic exploratoire
ponctuel, pas nécessaire à un run récurrent du pipeline) :
- §1.1 — vérification de la disponibilité du millésime via le flux WFS
  GetCapabilities (tolérante aux pannes, ne décide jamais du millésime) ;
- §1 — construction de `SOURCE.json` (fiche de traçabilité initiale, mêle
  saisie manuelle du dépôt d'archive et cette vérification WFS) ;
- §3.1/§3.2 — vérification de connexion PostGIS et écriture de `DB.json`
  (déjà couvertes par `src/db/connection.py`, pas de logique propre à
  l'ingestion RPG à porter ici).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import subprocess
import urllib.request
from pathlib import Path

import fiona
import geopandas as gpd
import pandas as pd
import pyogrio

from src.db.connection import connexion, get_pg_params
from src.db.qa import qa_validite, reparer_si_necessaire
from src.reporting.diagnostics import (
    ajouter_tableau,
    nouveau_run_diagnostic,
    rendre_rapport_html,
)

logger = logging.getLogger(__name__)


# ── §1.2 — Empreinte et détection de l'archive ────────────────────────────


def sha256sum(path: Path, chunk: int = 1 << 20) -> str:
    """Empreinte SHA-256 d'un fichier (pinning du millésime exact)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def detecter_archive(
    data_raw: Path, patterns: tuple[str, ...] = ("*.7z", "*.zip")
) -> Path | None:
    """Détecte l'archive régionale RPG déposée dans `data_raw`.

    Retourne `None` si aucune archive, le chemin si une seule. Lève
    `ValueError` en cas d'ambiguïté (plusieurs candidates) — décision
    laissée à l'appelant plutôt que résolue arbitrairement.
    """
    candidats = sorted({p for pat in patterns for p in data_raw.glob(pat)})
    if not candidats:
        logger.info("Aucune archive détectée dans %s", data_raw)
        return None
    if len(candidats) > 1:
        raise ValueError(
            f"Plusieurs archives candidates dans {data_raw} : "
            f"{[c.name for c in candidats]} — ambiguïté à résoudre manuellement."
        )
    logger.info(
        "Archive détectée : %s (%.1f Mo)",
        candidats[0].name,
        candidats[0].stat().st_size / 1e6,
    )
    return candidats[0]


# ── §2.1 — Localisation et décompression ──────────────────────────────────


def decompresser_archive(
    archive: Path, data_raw: Path, nom_fichier_gpkg: str = "RPG_Parcelles.gpkg"
) -> Path:
    """Décompresse une archive `.7z` dans `data_raw`, retourne le chemin du GeoPackage.

    `nom_fichier_gpkg` est le nom de FICHIER (avec extension) recherché sur
    le disque — distinct du nom de COUCHE à l'intérieur du GeoPackage (cf.
    `couche_cible` dans `reconnaitre_gpkg`/`charger_rpg_vers_raw`), qui n'a
    pas d'extension. Les deux valent "RPG_Parcelles"(.gpkg) mais ne doivent
    jamais être confondus dans les appels — bug corrigé après un premier
    run qui cherchait le fichier sans son extension.

    Idempotent : si `nom_fichier_gpkg` est déjà présent, la décompression
    est sautée plutôt que refaite.
    """
    existants = list(data_raw.rglob(nom_fichier_gpkg))
    if existants:
        logger.info("%s déjà présent, décompression ignorée.", nom_fichier_gpkg)
        return existants[0]

    import py7zr

    logger.info(
        "Décompression de %s (%.2f Go)…", archive.name, archive.stat().st_size / 1e9
    )
    with py7zr.SevenZipFile(archive, mode="r") as z:
        z.extractall(path=data_raw)

    trouves = list(data_raw.rglob(nom_fichier_gpkg))
    if not trouves:
        raise FileNotFoundError(
            f"{nom_fichier_gpkg} introuvable après décompression de {archive.name}"
        )
    return trouves[0]


def localiser_gpkg(
    data_raw: Path,
    nom_fichier_gpkg: str = "RPG_Parcelles.gpkg",
    archive_patterns: tuple[str, ...] = ("*.7z",),
) -> Path:
    """Localise le fichier GeoPackage dans `data_raw`, décompresse si besoin.

    Compose `detecter_archive` + `decompresser_archive` — reproduit la
    logique de §2.1 (recherche directe, sinon fallback archive).
    `nom_fichier_gpkg` : nom de FICHIER (avec `.gpkg`) — ne pas y passer un
    nom de couche (cf. note dans `decompresser_archive`).
    """
    trouves = list(data_raw.rglob(nom_fichier_gpkg))
    if trouves:
        return trouves[0]

    archive = detecter_archive(data_raw, archive_patterns)
    if archive is None:
        raise FileNotFoundError(
            f"Ni {nom_fichier_gpkg} ni archive trouvé(e) sous {data_raw}"
        )
    return decompresser_archive(archive, data_raw, nom_fichier_gpkg)


# ── §2.2-2.6 — Reconnaissance ──────────────────────────────────────────────


def reconnaitre_gpkg(gpkg: Path, couche_cible: str) -> dict:
    """Inventorie le GeoPackage : couches, schéma, distributions, emprise.

    Purement exploratoire, aucune écriture PostGIS. Retourne un dict
    structuré, réutilisé à la fois pour `RECON.json` et pour le rapport de
    diagnostics (`generer_diagnostics_reconnaissance`) — le calcul n'est
    fait qu'une fois pour les deux sorties.
    """
    couches_dispo = fiona.listlayers(gpkg)
    inventaire = []
    for nom in couches_dispo:
        with fiona.open(gpkg, layer=nom) as src:
            inventaire.append(
                {
                    "couche": nom,
                    "geom": src.schema["geometry"]
                    if src.schema["geometry"] != "None"
                    else "—",
                    "epsg": src.crs.to_epsg() if src.crs else None,
                    "n_objets": len(src),
                    "attributs": list(src.schema["properties"].keys()),
                }
            )

    with fiona.open(gpkg, layer=couche_cible) as src:
        schema = src.schema
        crs = src.crs
        n_objets = len(src)

    df = pyogrio.read_dataframe(gpkg, layer=couche_cible)

    top_cultures = (
        df["code_cultu"]
        .value_counts()
        .head(20)
        .rename_axis("code_cultu")
        .reset_index(name="n_parcelles")
    )
    top_cultures["pct"] = (top_cultures["n_parcelles"] / len(df) * 100).round(1)

    stats_surf = df["surf_parc"].describe().round(3)

    info = pyogrio.read_info(gpkg, layer=couche_cible)
    bbox = info["total_bounds"]

    recon = {
        "gpkg": gpkg.name,
        "date_reconnaissance": datetime.date.today().isoformat(),
        "couches": inventaire,
        "RPG_Parcelles": {
            "n_objets": n_objets,
            "geometrie": schema["geometry"],
            "epsg": crs.to_epsg(),
            "attributs_livres": list(schema["properties"].keys()),
            "bbox_lamb93": {
                "xmin": round(bbox[0]),
                "ymin": round(bbox[1]),
                "xmax": round(bbox[2]),
                "ymax": round(bbox[3]),
            },
        },
        "surf_parc_stats": stats_surf.to_dict(),
        "top20_cultures": top_cultures.to_dict(orient="records"),
    }

    logger.info(
        "Reconnaissance %s : %s objets, %d couche(s).",
        gpkg.name,
        f"{n_objets:,}",
        len(couches_dispo),
    )
    return recon


def generer_diagnostics_reconnaissance(
    recon: dict, nom_module: str = "acquisition_rpg"
) -> Path:
    """Rapport de diagnostics HTML pour la reconnaissance GeoPackage.

    Tableaux uniquement (pas de figure) : la reconnaissance produit des
    distributions et inventaires, pas de série temporelle à tracer — cf.
    `methode.md` §S6, distinction QC visuelle / tests automatisés.
    """
    run_dir = nouveau_run_diagnostic(nom_module)

    df_couches = pd.DataFrame(recon["couches"])
    df_top_cultures = pd.DataFrame(recon["top20_cultures"])
    df_stats_surf = pd.DataFrame.from_dict(
        recon["surf_parc_stats"], orient="index", columns=["valeur"]
    )

    blocs = [
        ajouter_tableau(df_couches, "Couches du GeoPackage"),
        ajouter_tableau(df_top_cultures, "Top 20 cultures (code_cultu)"),
        ajouter_tableau(df_stats_surf, "Statistiques de surface (surf_parc, ha)"),
    ]
    metriques = {
        "GeoPackage": recon["gpkg"],
        "Date reconnaissance": recon["date_reconnaissance"],
        "Objets (RPG_Parcelles)": f"{recon['RPG_Parcelles']['n_objets']:,}",
        "EPSG": recon["RPG_Parcelles"]["epsg"],
    }
    return rendre_rapport_html(run_dir, "Reconnaissance RPG", blocs, metriques)


def recuperer_referentiel_cultures(millesime: int, ref_dir: Path) -> pd.DataFrame:
    """Récupère la table `codes_cultures` via WFS Géoplateforme (portage §2.6).

    Absente de l'archive régionale v3.0 — seul cas où une lecture réseau est
    faite pendant l'ingestion elle-même (table légère, stable, sans géométrie).
    """
    ref_dir.mkdir(parents=True, exist_ok=True)
    url = (
        "https://data.geopf.fr/wfs/ows"
        "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
        f"&TYPENAMES=RPG.{millesime}:codes_cultures"
        "&OUTPUTFORMAT=application%2Fjson"
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read())

    df_codes = pd.DataFrame([f["properties"] for f in data["features"]])
    dest = ref_dir / f"codes_cultures_{millesime}.csv"
    df_codes.to_csv(dest, index=False, encoding="utf-8")
    logger.info(
        "Référentiel codes_cultures récupéré : %d codes -> %s", len(df_codes), dest
    )
    return df_codes


# ── §4 — Chargement PostGIS et filtre AOI ──────────────────────────────────


def _retirer_create_schema(dump_path: Path) -> None:
    """Retire les lignes `CREATE SCHEMA` générées par GDAL dans un dump PGDUMP.

    Le schéma cible existe déjà (créé en amont) ; le conserver ferait
    échouer le chargement `psql` avec `ON_ERROR_STOP=1`.
    """
    contenu = dump_path.read_text(encoding="utf-8")
    nettoye = "\n".join(
        line
        for line in contenu.splitlines()
        if not line.strip().upper().startswith("CREATE SCHEMA")
    )
    dump_path.write_text(nettoye, encoding="utf-8")


def _executer_psql(dump_path: Path) -> None:
    """Exécute un fichier `.sql` via `psql` en subprocess.

    `PSQL_BIN` lu depuis l'environnement (`.env`), défaut `"psql"` (suppose
    le binaire sur le PATH) — plus portable que le chemin Windows en dur du
    notebook, à surcharger explicitement si `psql` n'est pas sur le PATH.
    """
    pg_params = get_pg_params()
    psql_bin = os.getenv("PSQL_BIN", "psql")
    cmd = [
        psql_bin,
        "-U",
        pg_params["user"],
        "-h",
        pg_params["host"],
        "-p",
        str(pg_params["port"]),
        "-d",
        pg_params["dbname"],
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        str(dump_path),
    ]
    env = {**os.environ, "PGPASSWORD": pg_params["password"]}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Échec psql sur {dump_path.name} :\n{result.stderr}")


def charger_rpg_vers_raw(gpkg: Path, couche_cible: str, data_raw: Path) -> int:
    """Charge la couche RPG dans `raw.rpg_parcelles` (export PGDUMP + `psql`).

    Portage de §4.1. Retourne le nombre de parcelles chargées ; lève si le
    volume chargé diverge du volume lu ou si le SRID est inattendu.
    """
    df_full = pyogrio.read_dataframe(gpkg, layer=couche_cible)
    n_objets = len(df_full)

    dump_path = data_raw / "rpg_parcelles_raw.sql"
    pyogrio.write_dataframe(
        df_full,
        str(dump_path),
        driver="PGDUMP",
        layer="rpg_parcelles",
        layer_options={"SCHEMA": "raw", "GEOM_TYPE": "geometry"},
    )
    _retirer_create_schema(dump_path)
    _executer_psql(dump_path)

    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM raw.rpg_parcelles;")
            n_charge = cur.fetchone()[0]
            cur.execute("SELECT ST_SRID(wkb_geometry) FROM raw.rpg_parcelles LIMIT 1;")
            srid = cur.fetchone()[0]

    if n_charge != n_objets:
        raise ValueError(
            f"Chargement incomplet : attendu {n_objets:,}, trouvé {n_charge:,}"
        )
    if srid != 2154:
        raise ValueError(f"SRID inattendu pour raw.rpg_parcelles : {srid}")

    logger.info(
        "raw.rpg_parcelles chargée : %s parcelles, SRID %d.", f"{n_charge:,}", srid
    )
    return n_charge


def qa_raw_avant_filtre() -> tuple[int, int]:
    """QA géométrique de `raw.rpg_parcelles` avant le filtre AOI (portage §4.1bis).

    Faite ici plutôt qu'après le filtre : une parcelle invalide dans l'AOI
    doit être réparée ou explicitement tracée, pas silencieusement exclue
    par un filtre en aval. Retourne `(n_invalides, n_reparees)`.
    """
    n_invalides = qa_validite("raw.rpg_parcelles", "wkb_geometry")
    n_reparees = reparer_si_necessaire("raw.rpg_parcelles", "wkb_geometry", n_invalides)
    return n_invalides, n_reparees


def charger_aoi_vers_raw(aoi_geojson: Path, data_raw: Path) -> tuple[int, float]:
    """Charge l'AOI dans `raw.aoi_seinecrops`, reprojetée en EPSG:2154 (portage §4.2).

    Retourne `(srid, surface_km2)`.
    """
    aoi = gpd.read_file(aoi_geojson)
    aoi_2154 = aoi.to_crs(epsg=2154)

    dump_path = data_raw / "aoi_seinecrops_raw.sql"
    pyogrio.write_dataframe(
        aoi_2154,
        str(dump_path),
        driver="PGDUMP",
        layer="aoi_seinecrops",
        layer_options={"SCHEMA": "raw", "GEOM_TYPE": "geometry"},
    )
    _retirer_create_schema(dump_path)
    _executer_psql(dump_path)

    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ST_SRID(wkb_geometry), ROUND((ST_Area(wkb_geometry) / 1e6)::numeric, 1)
                FROM raw.aoi_seinecrops;
                """
            )
            srid, surf_km2 = cur.fetchone()

    if srid != 2154:
        raise ValueError(f"SRID inattendu pour raw.aoi_seinecrops : {srid}")

    logger.info(
        "raw.aoi_seinecrops chargée : SRID %d, surface %.1f km².", srid, surf_km2
    )
    return srid, float(surf_km2)


def filtrer_aoi() -> int:
    """Crée `derived.rpg_parcelles_aoi` via `ST_Intersects` (portage §4.3).

    Parcelles conservées entières (pas de découpe à la frontière, cf.
    `methode.md`) — aucun `WHERE ST_IsValid` : `raw.rpg_parcelles` a déjà
    été validée/réparée par `qa_raw_avant_filtre`, un filtre ici serait
    redondant et masquerait silencieusement une régression.
    """
    sql = """
        DROP TABLE IF EXISTS derived.rpg_parcelles_aoi;

        CREATE TABLE derived.rpg_parcelles_aoi AS
        SELECT
            p.id_parcel,
            p.surf_parc,
            p.code_cultu,
            p.code_group,
            p.culture_d1,
            p.culture_d2,
            p.cat_cult_p,
            p.wkb_geometry AS geom
        FROM raw.rpg_parcelles AS p
        JOIN raw.aoi_seinecrops AS a
            ON ST_Intersects(p.wkb_geometry, a.wkb_geometry);
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM derived.rpg_parcelles_aoi;")
            n_aoi = cur.fetchone()[0]

    logger.info("derived.rpg_parcelles_aoi créée : %s parcelles.", f"{n_aoi:,}")
    return n_aoi


# ── §5 — Validation et clôture ──────────────────────────────────────────────


def calculer_surface_totale_aoi() -> float:
    """Surface agricole totale (ha) des parcelles filtrées — somme de `surf_parc`
    sur `derived.rpg_parcelles_aoi`. Portage de §5 (cellule 53).

    Distincte de l'aire du polygone AOI lui-même (`raw.aoi_seinecrops`,
    calculée dans `charger_aoi_vers_raw`) : celle-ci inclut aussi les terres
    non agricoles (forêts, zones urbaines, réseau hydrographique) à
    l'intérieur du périmètre — les deux grandeurs ne doivent pas être
    interverties dans le rapport de clôture.
    """
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ROUND((SUM(surf_parc))::numeric, 1) FROM derived.rpg_parcelles_aoi;"
            )
            surf_totale_ha = cur.fetchone()[0]
    return float(surf_totale_ha)


def valider_ingestion() -> dict:
    """Assertions formalisées sur l'ingestion (portage §5.1-5.2).

    Répare si nécessaire (`derived.rpg_parcelles_aoi`), puis vérifie CRS,
    volumes, absence de fuite/duplication (`derived` ⊂ `raw`). Lève
    `AssertionError` au premier échec — sert de porte QC pour la tâche
    Airflow correspondante (bloque `nettoyage_intermediaires` en cas
    d'échec). Retourne un dict de résultats en cas de succès, réutilisé
    par `ecrire_rapport_cloture` et exploitable par un futur test pytest
    d'intégration.

    Ne vérifie plus la présence des index (`idx_rpg_parcelles_aoi_geom`,
    `idx_rpg_parcelles_aoi_code_cultu`) : à ce stade de la chaîne
    d'ingestion (S1), ces index n'existent pas encore — ils sont posés
    par la migration `0003` (P1 S7), exécutée après coup, pas par cette
    fonction. Leur présence et leur usage effectif sont désormais
    vérifiés par le jeu de tests de schéma (`tests/db/test_schema.py`),
    au bon niveau : contre l'état de la base après migrations, pas contre
    l'état intermédiaire post-ingestion.
    """
    n_invalides_derived = qa_validite("derived.rpg_parcelles_aoi", "geom")
    n_reparees_derived = reparer_si_necessaire(
        "derived.rpg_parcelles_aoi", "geom", n_invalides_derived
    )
    if n_reparees_derived:
        n_invalides_derived = qa_validite("derived.rpg_parcelles_aoi", "geom")

    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ST_SRID(wkb_geometry) FROM raw.rpg_parcelles LIMIT 1;")
            srid_raw = cur.fetchone()[0]
            cur.execute("SELECT ST_SRID(wkb_geometry) FROM raw.aoi_seinecrops LIMIT 1;")
            srid_aoi_brut = cur.fetchone()[0]
            cur.execute("SELECT ST_SRID(geom) FROM derived.rpg_parcelles_aoi LIMIT 1;")
            srid_derived = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM raw.rpg_parcelles;")
            n_raw = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM derived.rpg_parcelles_aoi;")
            n_derived = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*) FROM derived.rpg_parcelles_aoi d
                WHERE NOT EXISTS (
                    SELECT 1 FROM raw.rpg_parcelles r WHERE r.id_parcel = d.id_parcel
                );
                """
            )
            n_orphelines = cur.fetchone()[0]

    assert srid_raw == 2154, f"CRS raw.rpg_parcelles inattendu : {srid_raw}"
    assert srid_aoi_brut == 2154, f"CRS raw.aoi_seinecrops inattendu : {srid_aoi_brut}"
    assert (
        srid_derived == 2154
    ), f"CRS derived.rpg_parcelles_aoi inattendu : {srid_derived}"
    assert (
        0 < n_derived < n_raw
    ), f"Volume incohérent : derived={n_derived:,}, raw={n_raw:,}"
    assert (
        n_orphelines == 0
    ), f"{n_orphelines} parcelle(s) orpheline(s) dans derived (sans correspondance raw)"
    assert (
        n_invalides_derived == 0
    ), f"{n_invalides_derived} géométrie(s) invalide(s) restante(s) dans derived"

    logger.info(
        "Validation réussie : %s parcelles (derived) / %s (raw).",
        f"{n_derived:,}",
        f"{n_raw:,}",
    )

    return {
        "srid_raw": srid_raw,
        "srid_aoi_brut": srid_aoi_brut,
        "srid_derived": srid_derived,
        "n_raw": n_raw,
        "n_derived": n_derived,
        "n_orphelines": n_orphelines,
        "n_invalides_derived": n_invalides_derived,
        "n_reparees_derived": n_reparees_derived,
    }


def ecrire_rapport_cloture(
    data_raw: Path,
    project_root: Path,
    millesime: int,
    region_code: str,
    aoi_geojson: Path,
    qa_raw: tuple[int, int],
    resultats_validation: dict,
    surf_totale_ha: float,
    n_codes: int | None = None,
    surf_aoi_polygone_km2: float | None = None,
) -> Path:
    """Consolide `SOURCE.json`/`RECON.json`/`DB.json` (s'ils existent) et les
    résultats de `valider_ingestion` en `INGESTION_REPORT.json` (portage §5.3).

    `surf_totale_ha` : surface agricole (somme de `surf_parc` sur les
    parcelles filtrées, cf. `calculer_surface_totale_aoi`) — champ historique,
    même sémantique que dans le notebook. `surf_aoi_polygone_km2` (optionnel) :
    emprise du polygone AOI lui-même (`charger_aoi_vers_raw`), ajoutée à
    titre informatif, à ne pas confondre avec `surf_totale_ha`.
    """

    def charger_si_existe(path: Path) -> dict | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    source_doc = charger_si_existe(data_raw / "SOURCE.json")
    recon_doc = charger_si_existe(data_raw / "RECON.json")
    db_doc = charger_si_existe(data_raw / "DB.json")
    n_invalides_raw, n_reparees_raw = qa_raw

    rapport = {
        "date_cloture": datetime.date.today().isoformat(),
        "millesime": millesime,
        "region": region_code,
        "source": {
            "archive": source_doc.get("archive") if source_doc else None,
            "sha256": source_doc.get("sha256") if source_doc else None,
            "licence": source_doc.get("licence") if source_doc else None,
            "date_recuperation": source_doc.get("date_recuperation")
            if source_doc
            else None,
        },
        "reconnaissance": {
            "n_objets_normandie": recon_doc.get("RPG_Parcelles", {}).get("n_objets")
            if recon_doc
            else None,
            "schema_attributaire": recon_doc.get("RPG_Parcelles", {}).get(
                "attributs_livres"
            )
            if recon_doc
            else None,
        },
        "base_postgis": {
            "host": db_doc.get("host") if db_doc else None,
            "dbname": db_doc.get("dbname") if db_doc else None,
            "postgresql": db_doc.get("postgresql") if db_doc else None,
            "postgis": db_doc.get("postgis") if db_doc else None,
        },
        "filtre_aoi": {
            "aoi_fichier": str(aoi_geojson.relative_to(project_root)),
            "table_resultat": "derived.rpg_parcelles_aoi",
            "methode": "ST_Intersects (parcelles entières, pas de découpe à la frontière)",
            "n_parcelles_normandie": resultats_validation["n_raw"],
            "n_parcelles_aoi": resultats_validation["n_derived"],
            "surface_totale_ha": float(surf_totale_ha),
            "surface_aoi_polygone_km2": (
                float(surf_aoi_polygone_km2)
                if surf_aoi_polygone_km2 is not None
                else None
            ),
        },
        "qa": {
            "geometries_invalides_raw_avant_filtre": n_invalides_raw,
            "geometries_reparees_raw_avant_filtre": n_reparees_raw,
            "geometries_invalides_derived_apres_filtre": resultats_validation[
                "n_invalides_derived"
            ],
            "geometries_reparees_derived_apres_filtre": resultats_validation[
                "n_reparees_derived"
            ],
            "parcelles_orphelines": resultats_validation["n_orphelines"],
            "note_ordre_qa": (
                "QA géométrique appliquée à raw AVANT le filtre AOI (4.1bis), pas après : "
                "une parcelle invalide dans l'AOI doit être réparée ou explicitement tracée, "
                "pas silencieusement exclue par un WHERE ST_IsValid dans la requête de filtre."
            ),
        },
        "referentiel_cultures": {
            "fichier": str(
                (
                    data_raw.parent
                    / "_referentiels"
                    / f"codes_cultures_{millesime}.csv"
                ).relative_to(project_root)
            ),
            "n_codes": n_codes,
            "source": f"WFS Géoplateforme — RPG.{millesime}:codes_cultures",
        },
    }

    dest = data_raw / "INGESTION_REPORT.json"
    dest.write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Rapport de clôture écrit : %s", dest)
    return dest
