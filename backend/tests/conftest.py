"""Postgres test fixtures (docs/14-phase2-postgres-architecture.md §7).

Tests run against a real, disposable Postgres database — never SQLite, never
a mock, and never the production database. `TEST_DATABASE_URL` (defaulting to
a local Postgres, see app/config.py) is used only to create/drop a
session-scoped throwaway database; the app itself is pointed at that
throwaway database's own URL for the whole test session. If no Postgres is
reachable there, the Postgres-backed tests skip with an explicit reason
rather than failing or silently passing — see `_pg_available` below.

Isolation between tests is by truncation, not transaction rollback: this
app's `db_cursor()` commits per call (by design — see app/db.py), so a
per-test wrapping transaction would not actually contain multi-call
operations. `_reset_data` (autouse, function-scoped) truncates every jobber
table except the seeded vocabulary/reference tables before each test, which
is simpler and no less correct against a real Postgres.

Since the Phase 2 production-schema reconciliation pass, the throwaway
database is bootstrapped with `backend/scripts/local_baseline.sql` before
migrations run — the same production-shaped stand-in for the live pre-Phase-2
`jobber` baseline (and `profile360` stub) that a from-scratch local dev setup
uses (see that file's header and README). This means every test run is itself a
live proof that all Phase 2 migrations apply cleanly on top of the confirmed
production baseline shape — `test_migration_compatibility.py` asserts this
explicitly, plus that migrations refuse to run at all against a database
that never got that baseline (0001's preflight guard).
"""

import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_module  # noqa: E402
from app import embeddings  # noqa: E402
from app.config import test_database_url  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOCAL_BASELINE_SQL = (BACKEND_DIR / "scripts" / "local_baseline.sql").read_text(encoding="utf-8")


def _with_dbname(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def _pg_available(admin_url: str) -> bool:
    try:
        with psycopg.connect(admin_url, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


# Tables truncated between tests. Seed/reference tables (concept_type,
# concept_edge_rule, preference_dimension) and migration_history are
# deliberately excluded — they should persist exactly as a real deployment's
# would. This list is the full set of non-seed jobber tables that exist after
# local_baseline.sql + all of migrations/*.sql have applied (docs/14 §3/§4) —
# there is no more jobber.person/episode/episode_document/profile_snapshots or
# legacy_role_analysis; that data now lives directly on role_instance or in
# profile360, per the reconciliation pass.
_RESETTABLE_JOBBER_TABLES = [
    "capability_gold_judgment",
    "eval_run",
    "gold_claim",
    "gold_document",
    "d_role_fit",
    "d_capability_coverage",
    "person_capability_assertion",
    "preference_observation",
    "profile360_capability_mapping",
    "profile360_claim_mapping",
    "requirement_claim",
    "extraction_run",
    "vocabulary_version",
    "d_embedding",
    "concept_proposal",
    "concept_edge",
    "concept_xref",
    "capability_detail",
    "role_archetype_detail",
    "concept_alias",
    "role_skill_observation",
    "role_instance",
    "concept",
    "document",
]

# The profile360 stub tables local_baseline.sql provides, all matching the
# confirmed live shape (docs/14 §5/§6) — reset between tests same as
# jobber's own tables.
_RESETTABLE_PROFILE360_TABLES = ["claims", "capabilities", "episodes", "snapshots", "manual_import_queue"]


@pytest.fixture(scope="session")
def postgres_test_db():
    admin_url = test_database_url()
    if not _pg_available(admin_url):
        pytest.skip(f"no Postgres reachable at TEST_DATABASE_URL ({admin_url}) — see docs/14 §7")

    db_name = f"cp_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')

    db_url = _with_dbname(admin_url, db_name)
    try:
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(LOCAL_BASELINE_SQL)
        yield db_url
    finally:
        db_module.reset_pool()
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')


@pytest.fixture(scope="session", autouse=True)
def _configure_app_database(postgres_test_db, monkeypatch_session):
    monkeypatch_session.setenv("DATABASE_URL", postgres_test_db)
    db_module.reset_pool()
    db_module.run_migrations()
    yield


@pytest.fixture(scope="session")
def monkeypatch_session():
    """pytest's built-in `monkeypatch` is function-scoped; this session-scoped
    equivalent is only used for the one env var the whole test session needs
    fixed for its lifetime (DATABASE_URL)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(autouse=True)
def _reset_data(_configure_app_database):
    with db_module.db_cursor() as cur:
        cur.execute("TRUNCATE TABLE jobber." + ", jobber.".join(_RESETTABLE_JOBBER_TABLES) + " RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE TABLE profile360." + ", profile360.".join(_RESETTABLE_PROFILE360_TABLES) + " RESTART IDENTITY CASCADE")
    yield


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """This sandbox's network policy blocks huggingface.co (same constraint
    noted in docs/11 §11 Phase 1 build notes) — every test uses a
    deterministic pseudo-embedding instead of the real fastembed model, same
    precedent as that phase's own tests. Deterministic-by-content (a hash
    seed, not random) so semantically-unrelated test strings don't
    accidentally collide or cluster."""

    def _fake_embed_text(text: str) -> list[float]:
        text = (text or "").strip()
        if not text:
            return []
        seed = abs(hash(text)) % (2**32)
        vec = []
        x = seed or 1
        for _ in range(embeddings.EMBEDDING_DIM):
            x = (1103515245 * x + 12345) % (2**31)
            vec.append((x / (2**31)) * 2 - 1)
        return vec

    # `embed_text` is imported by name (`from ..embeddings import embed_text`)
    # at module load time in several route modules, so patching the
    # `embeddings` module alone leaves those modules' own bindings pointing
    # at the real (network-calling) function — every such binding needs
    # patching individually. (`routes.roles.update_role` re-imports it fresh
    # inside the function body, so it alone would already see this.)
    monkeypatch.setattr(embeddings, "embed_text", _fake_embed_text)
    for target in (
        "app.concept_linking.embed_text",
        "app.routes.import_routes.embed_text",
        "app.routes.targets.embed_text",
        "app.routes.role_instances.embed_text",
    ):
        monkeypatch.setattr(target, _fake_embed_text)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
