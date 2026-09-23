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

import bcrypt
import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_module  # noqa: E402
from app import embeddings  # noqa: E402
from app.config import test_database_url  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOCAL_BASELINE_SQL = (BACKEND_DIR / "scripts" / "local_baseline.sql").read_text(encoding="utf-8")

# Test-only credentials for the app's single-user auth (app/auth.py,
# docs/20). A low bcrypt cost factor keeps `verify_password` fast across
# hundreds of tests — nothing about this hash is used outside this file, so
# there's no reason to pay production's cost factor here.
TEST_AUTH_PASSWORD = "test-password"
TEST_AUTH_PASSWORD_HASH = bcrypt.hashpw(TEST_AUTH_PASSWORD.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")


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
    "d_pathways",
    "d_target_path",
    "d_target_evidence",
    "development_action",
    "capability_gold_judgment",
    "eval_run",
    "gold_claim",
    "gold_document",
    "d_gap_value",
    "archetype_context_enrichment",
    "d_archetype_comp",
    "d_archetype_demand",
    "compensation_observation",
    "planning_assumption",
    "economics_rebuild_state",
    "market",
    "d_role_fit",
    "d_capability_coverage",
    "person_capability_assertion",
    "preference_observation",
    "profile360_capability_mapping",
    "profile360_claim_mapping",
    "requirement_evidence",
    "requirement_claim",
    "extraction_run",
    "vocabulary_version",
    "d_embedding",
    "concept_proposal",
    "concept_edge",
    "concept_xref",
    "concept_dossier",
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
# profile360.compensation_observation / compensation_evidence are created by
# migration 0015 against the same stub schema and reset alongside the rest —
# personal earnings tests seed and clear them per test like any other table.
_RESETTABLE_PROFILE360_TABLES = [
    "compensation_evidence", "compensation_observation",
    "claims", "capabilities", "episodes", "snapshots", "manual_import_queue",
]


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
    # app/main.py reads both at import time (see its module-level
    # config.session_secret()/config.auth_password_hash() calls) and fails
    # closed if either is missing — tests need real values for the same
    # reason a real deployment does. APP_ENV is deliberately left unset
    # (defaults to "development") so the session cookie isn't marked
    # `Secure`, which TestClient's http (not https) requests wouldn't carry.
    monkeypatch_session.setenv("APP_SESSION_SECRET", "test-only-session-secret-not-for-production")
    monkeypatch_session.setenv("APP_AUTH_PASSWORD_HASH", TEST_AUTH_PASSWORD_HASH)
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
        # jobber.planning_assumption is a migration-seeded singleton, so
        # truncating it (which is what resets a user-set billable-days
        # assumption between tests) must put the seeded row back — a real
        # deployment always has it. app/personal_earnings.py tolerates the
        # row being absent too, but tests should exercise the normal shape.
        cur.execute(
            "INSERT INTO jobber.planning_assumption (singleton) VALUES (true) ON CONFLICT (singleton) DO NOTHING"
        )
        # Reseeded with NULL revisions, i.e. "never rebuilt" — the correct
        # starting state for a fresh deployment, and the one that makes a
        # test's own `record_rebuild` meaningful instead of inheriting a
        # previous test's.
        cur.execute(
            "INSERT INTO jobber.economics_rebuild_state (singleton) VALUES (true) ON CONFLICT (singleton) DO NOTHING"
        )
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
        "app.document_processing.embed_text",
    ):
        monkeypatch.setattr(target, _fake_embed_text)


@pytest.fixture
def client():
    """A pre-authenticated TestClient — every one of this suite's ~300
    existing tests predates app-level auth (app/auth.py) and exercises
    business logic, not login itself, so this fixture logs in once up
    front and hands back a client that behaves exactly as it did before
    auth existed. Tests that specifically exercise unauthenticated/expired
    session behaviour use `anon_client` instead (see tests/test_auth.py)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        login_resp = c.post("/api/auth/login", json={"password": TEST_AUTH_PASSWORD})
        assert login_resp.status_code == 200, f"test login fixture failed: {login_resp.status_code} {login_resp.text}"
        yield c


@pytest.fixture
def anon_client():
    """A TestClient with no session cookie — for asserting what happens
    *without* logging in first (tests/test_auth.py)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_password() -> str:
    return TEST_AUTH_PASSWORD
