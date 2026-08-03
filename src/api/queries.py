"""Requêtes PostGIS et assemblage des réponses API.

Portage des requêtes validées dans `06_api.ipynb` (§6.2 — fiche
parcelle, §6.4 — profil temporel). Voir `cadrage/methode.md` §S5 pour
le détail des tables sources et du mapping colonne DB → champ API.
"""

from collections import defaultdict
from datetime import date

import pandas as pd
from asyncpg import Connection

from .schemas import ParcelleDetail, ParcelleProfil

# --- Fiche parcelle (§6.2) -------------------------------------------------

SQL_FICHE_PARCELLE = """
    SELECT
        c.id_parcel,
        r.code_cultu,
        c.classe_declaree AS classe_declaree_classif,
        c.classe_predite,
        c.proba_max,
        d.dist_classe,
        d.divergent,
        d.zone_raccord_orbital,
        p.sos_date,
        p.pos_date,
        p.eos_date,
        p.los_jours,
        p.fiable AS phenologie_fiable
    FROM derived.parcelles_classification c
    LEFT JOIN derived.divergence d USING (id_parcel)
    LEFT JOIN derived.phenologie p USING (id_parcel)
    LEFT JOIN derived.rpg_parcelles_aoi r USING (id_parcel)
    WHERE c.id_parcel = $1
"""
# Ne récupère qu'une seule classe_declaree (celle de parcelles_classification,
# retenue comme référence en 6.2) : le garde-fou de cohérence entre les 3
# classe_declaree a déjà été exécuté une fois sur les 77 932 parcelles
# (§6.2, 0 incohérence) — pas besoin de le revérifier à chaque appel API.


async def fetch_parcelle_detail(
    conn: Connection, id_parcel: str
) -> ParcelleDetail | None:
    row = await conn.fetchrow(SQL_FICHE_PARCELLE, id_parcel)
    if row is None:
        return None
    return ParcelleDetail(
        id_parcel=row["id_parcel"],
        code_cultu_declare=row["code_cultu"],
        classe_declaree=row["classe_declaree_classif"],
        classe_predite=row["classe_predite"],
        proba_classe=row["proba_max"],
        score_divergence=row["dist_classe"],
        divergente=row["divergent"],
        zone_raccord_orbital=row["zone_raccord_orbital"],
        sos=row["sos_date"],
        pos=row["pos_date"],
        eos=row["eos_date"],
        los_jours=row["los_jours"],
        phenologie_fiable=row["phenologie_fiable"],
    )


# --- Profil temporel (§6.4) ------------------------------------------------

SQL_PROFIL = """
    SELECT mois, variable, mean
    FROM derived.s2_parcelles_monthly
    WHERE id_parcel = $1 AND variable IN ('NDVI', 'EVI', 'NDWI', 'NDRE')
    ORDER BY mois, variable
"""

# Calendrier de référence : sept N → déc N+1 (16 mois), aligné sur la
# fenêtre d'observation du projet (cf. methode.md §Zone d'étude).
# Les mois sous le seuil de complétude sont des lignes absentes en
# base (pas des NULL, vérifié en §6.4bis) — ce calendrier permet de
# les reconstituer et de garder les listes alignées avec `dates`.
MOIS_REFERENCE = pd.period_range("2023-09", "2024-12", freq="M").astype(str).tolist()


async def fetch_parcelle_profil(
    conn: Connection, id_parcel: str
) -> ParcelleProfil | None:
    existe = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM derived.parcelles_classification WHERE id_parcel = $1)",
        id_parcel,
    )
    if not existe:
        return None

    rows = await conn.fetch(SQL_PROFIL, id_parcel)
    valeurs: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        valeurs[r["variable"]][r["mois"]] = r["mean"]

    return ParcelleProfil(
        id_parcel=id_parcel,
        dates=[date.fromisoformat(f"{m}-01") for m in MOIS_REFERENCE],
        ndvi=[valeurs["NDVI"].get(m) for m in MOIS_REFERENCE],
        evi=[valeurs["EVI"].get(m) for m in MOIS_REFERENCE],
        ndwi=[valeurs["NDWI"].get(m) for m in MOIS_REFERENCE],
        ndre=[valeurs["NDRE"].get(m) for m in MOIS_REFERENCE],
    )
