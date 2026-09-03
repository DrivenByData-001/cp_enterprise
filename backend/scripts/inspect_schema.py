"""Dump the actual `jobber` and `profile360` schemas from a live Postgres
database, for reconciling against the reconstructions in
`backend/migrations/` and `docs/14-phase2-postgres-architecture.md` §3/§5.

This build has never run this script against the real `open-brain` Supabase
project (no credentials in this environment) — every migration is written to
be additive/idempotent specifically so it is safe to apply either way, but
column-level mismatches (a differently-named or differently-typed column
already present on an already-migrated table) can only be found by actually
looking, which is what this script is for.

Usage:
    cd backend && python3 scripts/inspect_schema.py

Reads DATABASE_URL from the environment (same variable the app uses — see
.env.example). Prints, per schema, every table with its columns
(name/type/nullable), primary key, and foreign keys. Read-only: issues only
`information_schema`/`pg_catalog` SELECTs, never touches application data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.config import database_url  # noqa: E402

SCHEMAS = ("jobber", "profile360")


def _tables(cur, schema: str) -> list[str]:
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
        (schema,),
    )
    return [r["table_name"] for r in cur.fetchall()]


def _columns(cur, schema: str, table: str) -> list[dict]:
    cur.execute(
        """
        SELECT column_name, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return cur.fetchall()


def _primary_key(cur, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (schema, table),
    )
    return [r["column_name"] for r in cur.fetchall()]


def _foreign_keys(cur, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        SELECT
            kcu.column_name,
            ccu.table_schema AS foreign_schema,
            ccu.table_name AS foreign_table,
            ccu.column_name AS foreign_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'FOREIGN KEY'
        ORDER BY kcu.column_name
        """,
        (schema, table),
    )
    return [f"{r['column_name']} -> {r['foreign_schema']}.{r['foreign_table']}({r['foreign_column']})" for r in cur.fetchall()]


def _row_count(cur, schema: str, table: str) -> int | None:
    try:
        cur.execute(f'SELECT COUNT(*) AS n FROM "{schema}"."{table}"')
        return cur.fetchone()["n"]
    except psycopg.Error:
        return None


def main() -> None:
    conn = psycopg.connect(database_url(), row_factory=dict_row, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s)", (list(SCHEMAS),))
        present = {r["schema_name"] for r in cur.fetchall()}

        for schema in SCHEMAS:
            print(f"\n{'=' * 70}\nschema: {schema}{'  (NOT FOUND on this connection)' if schema not in present else ''}\n{'=' * 70}")
            if schema not in present:
                continue
            for table in _tables(cur, schema):
                n = _row_count(cur, schema, table)
                print(f"\n-- {schema}.{table}" + (f"  ({n} rows)" if n is not None else ""))
                pk = _primary_key(cur, schema, table)
                if pk:
                    print(f"   PRIMARY KEY ({', '.join(pk)})")
                for col in _columns(cur, schema, table):
                    nullable = "" if col["is_nullable"] == "YES" else " NOT NULL"
                    default = f" DEFAULT {col['column_default']}" if col["column_default"] else ""
                    print(f"   {col['column_name']:<32} {col['udt_name']}{nullable}{default}")
                for fk in _foreign_keys(cur, schema, table):
                    print(f"   FOREIGN KEY {fk}")
    conn.close()


if __name__ == "__main__":
    main()
