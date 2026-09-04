"""Regression coverage for the connection-pool shutdown fix.

CLI scripts (backend/scripts/rebuild_embeddings.py, process_job_documents.py,
seed_phase3_eval_sample.py) and the FastAPI app's own `shutdown` event
(app/main.py) all now call `db.reset_pool()` explicitly before exiting,
instead of leaving the pool to be garbage-collected at interpreter shutdown.
Left to psycopg_pool's own `ConnectionPool.__del__`, that path tries to join
the pool's worker/scheduler threads within a 5s timeout and, when that join
doesn't land in time (routinely, right at interpreter teardown), logs
`couldn't stop thread 'pool-N-worker-*'/'pool-N-scheduler' within 5.0
seconds` — harmless, but noisy on every CLI run. These tests prove
`reset_pool()` actually closes the underlying pool (so the explicit-close
path really does what the fix relies on) and that a subsequent call still
leaves the app in a working state (get_pool()/db_cursor() transparently
open a fresh pool), matching how the same process can run several CLI
operations, or the FastAPI app can restart, without a stale closed pool
being reused.
"""

from app import db as db_module


def test_reset_pool_closes_the_underlying_pool():
    pool = db_module.get_pool()
    assert not pool.closed

    db_module.reset_pool()

    assert pool.closed


def test_reset_pool_is_safe_to_call_when_no_pool_is_open():
    db_module.reset_pool()
    db_module.reset_pool()  # must not raise on an already-closed/never-opened pool


def test_get_pool_after_reset_pool_returns_a_fresh_usable_pool():
    first_pool = db_module.get_pool()
    db_module.reset_pool()

    second_pool = db_module.get_pool()

    assert second_pool is not first_pool
    assert not second_pool.closed
    with db_module.db_cursor() as cur:
        cur.execute("SELECT 1 AS one")
        assert cur.fetchone()["one"] == 1


def test_shutdown_event_closes_the_pool():
    """app/main.py's `shutdown` event is the FastAPI-owned equivalent of a
    CLI script's explicit reset_pool() call — proven end to end via a real
    TestClient context exit, which fires ASGI startup/shutdown events."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        c.get("/api/health")
        pool = db_module.get_pool()
        assert not pool.closed

    assert pool.closed
