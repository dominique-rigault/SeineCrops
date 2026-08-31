"""Jeu de tests de schéma (gabarit §9), cumulatif.

Chaque migration ajoute sa propre classe `TestMigrationXXXX` à ce fichier ;
les classes existantes ne sont jamais retirées ni réécrites pour un
changement ultérieur — seulement pour refléter un changement de la
migration qu'elles couvrent elles-mêmes. Une nouvelle migration qui altère
une contrainte posée par une migration antérieure (colonne retirée,
contrainte remplacée) doit mettre à jour la classe de la migration
d'origine en conséquence, pas ajouter une exception dans sa propre classe.

Portée de ce premier fichier : rattrapage des migrations `0002` à `0007`
(aucune n'avait de test avant ce sprint), plus le test d'index de S8
(`TestMigration0003IndexSpatiaux`, seule partie explicitement demandée
pour ce sprint — le rattrapage `0002`/`0004`/`0005`/`0006`/`0007` a été
ajouté à la demande explicite de Dominique après le cadrage initial).

Ces tests vérifient des faits structurels (contraintes, index, plans
d'exécution) — pas des valeurs de contenu (une classe déclarée est-elle
correcte, par exemple), qui relèvent d'une autre suite.
"""

from __future__ import annotations

import pytest

from src.api.queries import SQL_BBOX, SQL_COUNT_BBOX

# --- Helpers, réutilisés par plusieurs classes -------------------------------


def colonnes(cur, schema: str, table: str) -> dict[str, dict]:
    """Colonnes d'une table : {nom: {type, nullable}}."""
    cur.execute(
        """
        SELECT column_name, data_type, udt_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema, table),
    )
    return {
        row[0]: {"data_type": row[1], "udt_name": row[2], "is_nullable": row[3]}
        for row in cur.fetchall()
    }


def colonnes_pk(cur, schema: str, table: str) -> list[str]:
    """Colonnes de la clé primaire, dans l'ordre — [] si aucune PK."""
    cur.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a
            ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum)
        """,
        (f"{schema}.{table}",),
    )
    return [row[0] for row in cur.fetchall()]


def index_existants(cur, schema: str, table: str) -> dict[str, str]:
    """Index d'une table : {nom_index: définition}."""
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
        (schema, table),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def fk_existante(cur, schema: str, table: str, constraint_name: str) -> dict | None:
    """Détails d'une FOREIGN KEY nommée sur une table — None si absente."""
    cur.execute(
        """
        SELECT kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
            ON tc.constraint_name = ccu.constraint_name
            AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = %s AND tc.table_name = %s
            AND tc.constraint_name = %s
        """,
        (schema, table, constraint_name),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "colonne_locale": row[0],
        "schema_ref": row[1],
        "table_ref": row[2],
        "colonne_ref": row[3],
    }


def check_constraint_existe(cur, schema: str, table: str, constraint_name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_type = 'CHECK'
            AND table_schema = %s AND table_name = %s AND constraint_name = %s
        """,
        (schema, table, constraint_name),
    )
    return cur.fetchone() is not None


def plan_execution(
    cur, prepare_name: str, types_params: list[str], sql: str, valeurs: tuple
) -> str:
    """Plan d'exécution réel (`EXPLAIN ANALYZE`) d'une requête portant des
    placeholders `$1, $2, ...` (convention `asyncpg` de `src/api/queries.py`).

    Passe par `PREPARE`/`EXECUTE` côté serveur plutôt que par la
    substitution `%s` de `psycopg2` (incompatible avec la syntaxe `$n`) :
    la requête testée est ainsi celle du fichier source, caractère pour
    caractère, sans copie qui pourrait diverger.
    """
    cur.execute(f"PREPARE {prepare_name} ({', '.join(types_params)}) AS {sql}")
    try:
        placeholders = ", ".join(str(v) for v in valeurs)
        cur.execute(
            f"EXPLAIN (ANALYZE, FORMAT TEXT) EXECUTE {prepare_name}({placeholders})"
        )
        return "\n".join(row[0] for row in cur.fetchall())
    finally:
        cur.execute(f"DEALLOCATE {prepare_name}")


@pytest.fixture
def bbox_reel(db_connection):
    """Bbox EPSG:4326 centrée sur une parcelle réelle de l'AOI (buffer
    ~0,01°, ≈ 5 km²) — garantit une intersection réelle sans coordonnées
    normandes codées en dur, dont l'exactitude serait invérifiable ici."""
    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT ST_X(c), ST_Y(c) FROM (
                SELECT ST_Centroid(ST_Transform(geom, 4326)) AS c
                FROM derived.rpg_parcelles_aoi
                ORDER BY id_parcel
                LIMIT 1
            ) t
            """
        )
        lon, lat = cur.fetchone()
    return (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)


# --- 0002 : DDL explicite raw.rpg_parcelles / raw.aoi_seinecrops ------------


class TestMigration0002RawDDL:
    def test_rpg_parcelles_colonnes(self, db_connection):
        with db_connection.cursor() as cur:
            cols = colonnes(cur, "raw", "rpg_parcelles")
        attendu = {
            "ogc_fid",
            "wkb_geometry",
            "id_parcel",
            "surf_parc",
            "code_cultu",
            "code_group",
            "culture_d1",
            "culture_d2",
            "cat_cult_p",
        }
        assert attendu <= cols.keys(), f"Colonnes manquantes : {attendu - cols.keys()}"
        assert cols["wkb_geometry"]["udt_name"] == "geometry"

    def test_rpg_parcelles_pk(self, db_connection):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "raw", "rpg_parcelles") == ["ogc_fid"]

    def test_rpg_parcelles_index_gist(self, db_connection):
        with db_connection.cursor() as cur:
            idx = index_existants(cur, "raw", "rpg_parcelles")
        assert "rpg_parcelles_wkb_geometry_geom_idx" in idx
        assert "gist" in idx["rpg_parcelles_wkb_geometry_geom_idx"].lower()

    def test_aoi_seinecrops_pk(self, db_connection):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "raw", "aoi_seinecrops") == ["ogc_fid"]

    def test_aoi_seinecrops_index_gist(self, db_connection):
        with db_connection.cursor() as cur:
            idx = index_existants(cur, "raw", "aoi_seinecrops")
        assert "aoi_seinecrops_wkb_geometry_geom_idx" in idx


# --- 0003 : dissolve + PK derived.rpg_parcelles_aoi -------------------------


class TestMigration0003PkDissolve:
    def test_pk_id_parcel(self, db_connection):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "derived", "rpg_parcelles_aoi") == ["id_parcel"]

    def test_id_parcel_not_null(self, db_connection):
        with db_connection.cursor() as cur:
            cols = colonnes(cur, "derived", "rpg_parcelles_aoi")
        assert cols["id_parcel"]["is_nullable"] == "NO"


class TestMigration0003IndexSpatiaux:
    """Ajout S8 : le seul point explicitement demandé pour ce sprint."""

    def test_index_geom_existe(self, db_connection):
        with db_connection.cursor() as cur:
            idx = index_existants(cur, "derived", "rpg_parcelles_aoi")
        assert "idx_rpg_parcelles_aoi_geom" in idx
        assert "gist" in idx["idx_rpg_parcelles_aoi_geom"].lower()

    def test_index_code_cultu_existe(self, db_connection):
        """Présence vérifiée (l'objet existe) — pas d'usage réel identifié
        (dette technique documentée, `dictionnaire_donnees_postgis.md`),
        donc pas de test d'utilisation associé, contrairement à l'index
        géométrique ci-dessous."""
        with db_connection.cursor() as cur:
            idx = index_existants(cur, "derived", "rpg_parcelles_aoi")
        assert "idx_rpg_parcelles_aoi_code_cultu" in idx

    def test_index_geom_utilise_par_sql_bbox(self, db_connection, bbox_reel):
        with db_connection.cursor() as cur:
            plan = plan_execution(
                cur,
                "test_bbox",
                [
                    "double precision",
                    "double precision",
                    "double precision",
                    "double precision",
                    "integer",
                ],
                SQL_BBOX,
                (*bbox_reel, 2000),
            )
        assert "idx_rpg_parcelles_aoi_geom" in plan, (
            "Index absent du plan d'exécution de SQL_BBOX (requête réelle "
            f"de GET /parcelles?bbox=) :\n{plan}"
        )

    def test_index_geom_utilise_par_sql_count_bbox(self, db_connection, bbox_reel):
        with db_connection.cursor() as cur:
            plan = plan_execution(
                cur,
                "test_count_bbox",
                [
                    "double precision",
                    "double precision",
                    "double precision",
                    "double precision",
                ],
                SQL_COUNT_BBOX,
                bbox_reel,
            )
        assert "idx_rpg_parcelles_aoi_geom" in plan, (
            "Index absent du plan d'exécution de SQL_COUNT_BBOX (utilisée "
            f"quand la pagination tronque le résultat) :\n{plan}"
        )


# --- 0004 : FK des 6 tables filles vers derived.rpg_parcelles_aoi ----------


class TestMigration0004ForeignKeys:
    FK_ATTENDUES = [
        ("parcelles_classification", "fk_classification_parcelle"),
        ("divergence", "fk_divergence_parcelle"),
        ("phenologie", "fk_phenologie_parcelle"),
        ("s2_parcelles_monthly", "fk_s2_mensuel_parcelle"),
        ("s2_parcelles_completude", "fk_s2_completude_parcelle"),
        ("s2_parcelles_ndvi_dates", "fk_s2_ndvi_parcelle"),
    ]

    @pytest.mark.parametrize("table, contrainte", FK_ATTENDUES)
    def test_fk_vers_rpg_parcelles_aoi(self, db_connection, table, contrainte):
        with db_connection.cursor() as cur:
            fk = fk_existante(cur, "derived", table, contrainte)
        assert fk is not None, f"FK '{contrainte}' absente sur derived.{table}"
        assert fk["colonne_locale"] == "id_parcel"
        assert fk["schema_ref"] == "derived"
        assert fk["table_ref"] == "rpg_parcelles_aoi"
        assert fk["colonne_ref"] == "id_parcel"


# --- 0005 : centralisation classe_declaree ----------------------------------


class TestMigration0005ClasseDeclaree:
    def test_colonne_presente_sur_rpg_parcelles_aoi(self, db_connection):
        with db_connection.cursor() as cur:
            cols = colonnes(cur, "derived", "rpg_parcelles_aoi")
        assert "classe_declaree" in cols
        assert cols["classe_declaree"]["is_nullable"] == "NO"

    @pytest.mark.parametrize(
        "table", ["parcelles_classification", "divergence", "phenologie"]
    )
    def test_colonne_absente_des_tables_filles(self, db_connection, table):
        with db_connection.cursor() as cur:
            cols = colonnes(cur, "derived", table)
        assert "classe_declaree" not in cols, (
            f"classe_declaree encore présente sur derived.{table} — "
            "régression sur la centralisation 0005"
        )


# --- 0006 : DDL classification / divergence / phenologie -------------------


class TestMigration0006DdlApplicatif:
    @pytest.mark.parametrize(
        "table", ["parcelles_classification", "divergence", "phenologie"]
    )
    def test_pk_id_parcel(self, db_connection, table):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "derived", table) == ["id_parcel"]

    def test_check_split_train_test(self, db_connection):
        with db_connection.cursor() as cur:
            cur.execute(
                """
                SELECT cc.check_clause
                FROM information_schema.check_constraints cc
                JOIN information_schema.table_constraints tc
                    ON cc.constraint_name = tc.constraint_name
                    AND cc.constraint_schema = tc.table_schema
                WHERE tc.table_schema = 'derived'
                    AND tc.table_name = 'parcelles_classification'
                """
            )
            clauses = [row[0] for row in cur.fetchall()]
        assert any("split" in c for c in clauses), (
            "Contrainte CHECK sur 'split' introuvable sur "
            "derived.parcelles_classification"
        )


# --- 0007 : DDL tables zonales (s2_parcelles_*) -----------------------------


class TestMigration0007DdlZonal:
    def test_pk_s2_parcelles_monthly(self, db_connection):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "derived", "s2_parcelles_monthly") == [
                "id_parcel",
                "mois",
                "variable",
            ]

    def test_pk_s2_parcelles_completude(self, db_connection):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "derived", "s2_parcelles_completude") == [
                "id_parcel",
                "mois",
            ]

    def test_pk_s2_parcelles_ndvi_dates(self, db_connection):
        with db_connection.cursor() as cur:
            assert colonnes_pk(cur, "derived", "s2_parcelles_ndvi_dates") == [
                "id_parcel",
                "date",
            ]
