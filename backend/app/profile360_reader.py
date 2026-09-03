"""Read-only accessor for the `profile360` schema — the authoritative
person-side career evidence store, owned by a separate tool, never duplicated
into `jobber` (docs/11 §1, docs/14 §5). Nothing in this module issues an
INSERT/UPDATE/DELETE against `profile360`.

Column names beyond `id` are not known from this build environment (no live
credentials — docs/14 §2) and are never hard-coded: every table access
introspects `information_schema` first (cached per process) and degrades
gracefully — a missing schema/table raises `Profile360UnavailableError` rather
than crashing the caller with a raw Postgres error, and a row is always
returned as a plain dict of whatever columns actually exist, with a
best-effort human-readable label rather than an assumed field name.
"""

import psycopg

# Exactly the eight tables the Phase 2 brief names. No code path in this
# module can query a table outside this set, including from a request
# parameter — this is the entirety of the "allowlisted" guarantee referenced
# in docs/15 §4.
ALLOWED_TABLES = {
    "documents", "episodes", "claims", "evidence", "capabilities",
    "contradictions", "open_questions", "snapshots",
}

# Common field names to try, in order, when picking one string to show a human
# reviewer for a row whose real shape is unknown. First match wins; if none
# match, the caller falls back to showing the raw row.
_DISPLAY_FIELD_CANDIDATES = (
    "claim_text", "text", "statement", "description", "summary", "title", "name", "label",
)

_column_cache: dict[str, list[str]] = {}
_pk_cache: dict[str, str] = {}


class Profile360UnavailableError(RuntimeError):
    """The profile360 schema/table/row this call needs is not reachable —
    schema not present (e.g. a local/test database), or the connected role
    lacks access. Never silently returns empty data for this case; the
    caller (a route) should map it to a clear 5xx rather than a false
    "not found"."""


def _require_allowed(table: str) -> None:
    if table not in ALLOWED_TABLES:
        raise ValueError(f"{table!r} is not one of the allowlisted profile360 tables: {sorted(ALLOWED_TABLES)}")


def list_columns(cur, table: str) -> list[str]:
    _require_allowed(table)
    if table in _column_cache:
        return _column_cache[table]
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'profile360' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    cols = [r["column_name"] for r in cur.fetchall()]
    if not cols:
        raise Profile360UnavailableError(
            f"profile360.{table} was not found (or has no visible columns) on this connection. "
            "See docs/14-phase2-postgres-architecture.md §5 — this build has never verified the "
            "live profile360 schema."
        )
    _column_cache[table] = cols
    return cols


def _primary_key_column(cur, table: str) -> str:
    _require_allowed(table)
    if table in _pk_cache:
        return _pk_cache[table]
    cur.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'profile360' AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        LIMIT 1
        """,
        (table,),
    )
    row = cur.fetchone()
    pk = row["column_name"] if row else "id"  # 'id' is the reasonable fallback if introspection is inconclusive
    _pk_cache[table] = pk
    return pk


def display_text(row: dict, max_len: int = 240) -> str:
    """Best-effort single string to show a human reviewer, without assuming
    any specific column exists."""
    for field in _DISPLAY_FIELD_CANDIDATES:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text if len(text) <= max_len else text[: max_len - 1] + "…"
    # Nothing recognisable — show a compact key:value fallback rather than
    # hiding the row entirely.
    parts = [f"{k}={v!r}" for k, v in row.items() if k != "id" and v is not None][:4]
    return ", ".join(parts) or "(no displayable fields)"


def fetch_rows(cur, table: str, limit: int = 50, offset: int = 0) -> list[dict]:
    _require_allowed(table)
    list_columns(cur, table)  # raises Profile360UnavailableError early if the table isn't reachable
    try:
        cur.execute(f"SELECT * FROM profile360.{table} ORDER BY 1 LIMIT %s OFFSET %s", (limit, offset))
    except psycopg.errors.UndefinedTable as e:
        raise Profile360UnavailableError(f"profile360.{table} does not exist on this connection.") from e
    return cur.fetchall()


def get_row(cur, table: str, row_id) -> dict | None:
    _require_allowed(table)
    pk = _primary_key_column(cur, table)
    try:
        cur.execute(f"SELECT * FROM profile360.{table} WHERE {pk} = %s", (row_id,))
    except psycopg.errors.UndefinedTable as e:
        raise Profile360UnavailableError(f"profile360.{table} does not exist on this connection.") from e
    return cur.fetchone()


def row_exists(cur, table: str, row_id) -> bool:
    return get_row(cur, table, row_id) is not None


# --- Convenience wrappers for the two tables Phase 2 mapping cares about ----

def list_claims(cur, limit: int = 50, offset: int = 0) -> list[dict]:
    return fetch_rows(cur, "claims", limit=limit, offset=offset)


def get_claim(cur, claim_id) -> dict | None:
    return get_row(cur, "claims", claim_id)


def list_capabilities(cur, limit: int = 50, offset: int = 0) -> list[dict]:
    return fetch_rows(cur, "capabilities", limit=limit, offset=offset)


def get_capability(cur, capability_id) -> dict | None:
    return get_row(cur, "capabilities", capability_id)
