"""Coverage for app/auth.py, app/routes/auth.py, and the central auth policy
wired in app/main.py — see docs/20-render-deployment.md.

`client` (tests/conftest.py) is pre-authenticated; `anon_client` is a fresh,
logged-out TestClient. Most of this file uses `anon_client` deliberately,
since it's exactly the unauthenticated caller these protections exist for.
"""

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import itsdangerous
import pytest

from app.auth import SESSION_COOKIE_NAME

# Deliberately not `from app.main import app` at module level: app.main
# calls config.session_secret()/auth_password_hash() at import time (see
# that file), which fail until the session-scoped conftest fixture has set
# APP_SESSION_SECRET/APP_AUTH_PASSWORD_HASH — and fixtures don't run until
# test *execution*, after this module is already collected. Every test
# below that needs the app object imports it lazily inside the test body
# (same pattern as tests/test_pool_lifecycle.py), or gets it indirectly via
# the `client`/`anon_client` fixtures.

BACKEND_DIR = Path(__file__).resolve().parent.parent

# The complete public-endpoint allowlist per app/main.py's own policy
# comment — every other /api/* route must require a session.
PUBLIC_API_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    """Starlette's TestClient always reports the same fake client host, so
    without this every test in this file would share one rate-limit bucket
    (app/auth.py's `_failed_attempts` is process-wide, keyed by client IP —
    correct for real distinct clients, but an artifact to reset between
    tests here)."""
    from app import auth as auth_module

    auth_module._failed_attempts.clear()
    yield
    auth_module._failed_attempts.clear()


# --- public endpoint -------------------------------------------------------


def test_health_is_public(anon_client):
    resp = anon_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- protected endpoints require a session ----------------------------------


def test_protected_get_endpoint_rejects_anonymous_request(anon_client):
    resp = anon_client.get("/api/roles")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/api/vocabulary/clusters/accept"),
        ("POST", "/api/vocabulary/clusters/reject"),
        ("POST", "/api/vocabulary/clusters/merge"),
        ("POST", "/api/vocabulary/clusters/batch"),
        ("POST", "/api/capabilities/rebuild"),
        ("POST", "/api/import/bulk"),
    ],
)
def test_state_changing_endpoints_reject_anonymous_request(anon_client, method, path):
    """Vocabulary curation writes and other mutating endpoints must 401
    without a session — never 200/422/500, which would mean the auth
    dependency wasn't reached at all."""
    resp = anon_client.request(method, path, json={})
    assert resp.status_code == 401


def test_vocabulary_read_endpoints_are_protected(anon_client):
    for path in ("/api/vocabulary/progress", "/api/vocabulary/clusters", "/api/vocabulary/methodology"):
        resp = anon_client.get(path)
        assert resp.status_code == 401, path


def test_api_auth_cannot_be_bypassed_by_calling_endpoints_directly(anon_client):
    """Black-box version of the auth policy check: walks the app's own
    OpenAPI schema (the stable, public description of every route actually
    served — robust to FastAPI's internal route-storage representation,
    unlike reaching into `app.routes`/`route.dependant`) and, for every
    /api/* operation not in the public allowlist, makes the real call with
    no session and asserts it 401s. A future router wired into app/main.py
    without the `require_auth` dependency fails this test rather than
    shipping an unprotected endpoint — regardless of whether it fails on a
    GET, a POST, or a path with `{param}` segments in it."""
    from app.main import app

    schema = app.openapi()
    checked = 0
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/") or path in PUBLIC_API_PATHS:
            continue
        # Placeholder path params are enough: the auth dependency must
        # reject the request before any handler logic (or path-param
        # validation) ever runs — the state-changing-endpoint tests above
        # already confirm this holds even for a malformed/empty body.
        concrete_path = re.sub(r"\{[^}]+\}", "placeholder", path)
        for method in operations:
            method = method.upper()
            if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                continue
            resp = anon_client.request(method, concrete_path, json={})
            assert resp.status_code == 401, (
                f"{method} {path} is reachable without auth (got {resp.status_code}: {resp.text[:200]})"
            )
            checked += 1
    assert checked > 20, "expected to check every protected router's routes, found suspiciously few"


# --- login ------------------------------------------------------------------


def test_login_failure_wrong_password(anon_client):
    resp = anon_client.post("/api/auth/login", json={"password": "wrong-password"})
    assert resp.status_code == 401
    assert SESSION_COOKIE_NAME not in anon_client.cookies


def test_login_success_sets_session_cookie(anon_client, test_password):
    resp = anon_client.post("/api/auth/login", json={"password": test_password})
    assert resp.status_code == 200
    assert SESSION_COOKIE_NAME in anon_client.cookies


def test_login_does_not_leak_backend_error_detail(anon_client):
    """A wrong password must get a generic 'invalid credentials' message,
    never a raw exception/traceback string."""
    resp = anon_client.post("/api/auth/login", json={"password": "wrong-password"})
    body = resp.json()
    assert body["detail"] == "Invalid credentials"


def test_login_rate_limited_after_repeated_failures(anon_client):
    for _ in range(5):
        resp = anon_client.post("/api/auth/login", json={"password": "wrong-password"})
        assert resp.status_code == 401
    resp = anon_client.post("/api/auth/login", json={"password": "wrong-password"})
    assert resp.status_code == 429


def test_login_rate_limit_does_not_block_correct_password_before_threshold(anon_client, test_password):
    for _ in range(3):
        anon_client.post("/api/auth/login", json={"password": "wrong-password"})
    resp = anon_client.post("/api/auth/login", json={"password": test_password})
    assert resp.status_code == 200


# --- session persistence / logout -------------------------------------------


def test_session_persists_across_requests(anon_client, test_password):
    anon_client.post("/api/auth/login", json={"password": test_password})
    first = anon_client.get("/api/roles")
    second = anon_client.get("/api/vocabulary/progress")
    assert first.status_code == 200
    assert second.status_code == 200


def test_logout_clears_session(anon_client, test_password):
    anon_client.post("/api/auth/login", json={"password": test_password})
    assert anon_client.get("/api/roles").status_code == 200

    logout_resp = anon_client.post("/api/auth/logout")
    assert logout_resp.status_code == 200

    assert anon_client.get("/api/roles").status_code == 401


def test_logout_is_safe_when_not_logged_in(anon_client):
    resp = anon_client.post("/api/auth/logout")
    assert resp.status_code == 200


def test_auth_status_reflects_session_state(anon_client, test_password):
    assert anon_client.get("/api/auth/status").json() == {"authenticated": False}
    anon_client.post("/api/auth/login", json={"password": test_password})
    assert anon_client.get("/api/auth/status").json() == {"authenticated": True}
    anon_client.post("/api/auth/logout")
    assert anon_client.get("/api/auth/status").json() == {"authenticated": False}


# --- invalid / expired session ------------------------------------------


def test_garbage_session_cookie_is_rejected(anon_client):
    anon_client.cookies.set(SESSION_COOKIE_NAME, "not-a-valid-signed-cookie")
    resp = anon_client.get("/api/roles")
    assert resp.status_code == 401


def test_expired_session_cookie_is_rejected(anon_client, monkeypatch):
    """Crafts a session cookie signed with the real (test) secret but with a
    timestamp far in the past, proving itsdangerous's max_age check — not
    just cookie presence — gates access."""
    secret = os.environ["APP_SESSION_SECRET"]
    signer = itsdangerous.TimestampSigner(secret)
    payload = base64.b64encode(json.dumps({"authenticated": True}).encode("utf-8"))

    real_get_timestamp = itsdangerous.TimestampSigner.get_timestamp
    monkeypatch.setattr(
        itsdangerous.TimestampSigner,
        "get_timestamp",
        lambda self: real_get_timestamp(self) - 365 * 24 * 3600,
    )
    stale_cookie = signer.sign(payload).decode("utf-8")
    monkeypatch.undo()  # restore real "now" before the app verifies it

    anon_client.cookies.set(SESSION_COOKIE_NAME, stale_cookie)
    resp = anon_client.get("/api/roles")
    assert resp.status_code == 401


# --- fail-closed configuration -----------------------------------------


def test_session_secret_required_fails_closed(monkeypatch):
    from app import config

    monkeypatch.delenv("APP_SESSION_SECRET", raising=False)
    with pytest.raises(config.ConfigError):
        config.session_secret()


def test_auth_password_hash_required_fails_closed(monkeypatch):
    from app import config

    monkeypatch.delenv("APP_AUTH_PASSWORD_HASH", raising=False)
    with pytest.raises(config.ConfigError):
        config.auth_password_hash()


def test_app_process_refuses_to_start_without_session_secret():
    """End-to-end version of the above: a real process importing app.main
    with APP_SESSION_SECRET missing must fail (non-zero exit), not silently
    come up with authentication disabled."""
    env = os.environ.copy()
    env.pop("APP_SESSION_SECRET", None)
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "APP_SESSION_SECRET" in result.stderr


def test_app_process_refuses_to_start_without_password_hash():
    env = os.environ.copy()
    env.pop("APP_AUTH_PASSWORD_HASH", None)
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "APP_AUTH_PASSWORD_HASH" in result.stderr


# --- CORS --------------------------------------------------------------


def test_cors_defaults_never_wildcard_with_credentials():
    from app import config

    assert "*" not in config.cors_allowed_origins()


def test_cors_includes_local_vite_dev_origin_outside_production(monkeypatch):
    from app import config

    monkeypatch.delenv("APP_ENV", raising=False)
    assert "http://localhost:5173" in config.cors_allowed_origins()


def test_cors_respects_explicit_allowed_origins(monkeypatch):
    from app import config

    monkeypatch.setenv("APP_CORS_ORIGINS", "https://cp-enterprise.example.com")
    monkeypatch.setenv("APP_ENV", "production")
    origins = config.cors_allowed_origins()
    assert origins == ["https://cp-enterprise.example.com"]


# --- SPA fallback / static serving --------------------------------------


@pytest.fixture
def fake_frontend_dist(tmp_path, monkeypatch):
    """Points app.main.FRONTEND_DIST at a throwaway build directory —
    app.main.spa_fallback reads that module global dynamically per request
    (see app/main.py), so this fixture doesn't need to re-import the app."""
    import app.main as main_module

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>spa shell</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")

    monkeypatch.setattr(main_module, "FRONTEND_DIST", dist)
    return dist


def test_spa_fallback_serves_index_for_root(anon_client, fake_frontend_dist):
    resp = anon_client.get("/")
    assert resp.status_code == 200
    assert "spa shell" in resp.text


@pytest.mark.parametrize(
    "deep_route",
    ["/space", "/trends", "/vocabulary", "/roles/11111111-1111-1111-1111-111111111111", "/comparison/abc"],
)
def test_deep_frontend_route_refresh_serves_index_not_404(anon_client, fake_frontend_dist, deep_route):
    """Simulates a browser refresh on a deep client-side route — must serve
    the SPA shell (which then does its own client-side routing), not 404."""
    resp = anon_client.get(deep_route)
    assert resp.status_code == 200
    assert "spa shell" in resp.text


def test_spa_fallback_serves_real_static_files(anon_client, fake_frontend_dist):
    resp = anon_client.get("/favicon.svg")
    assert resp.status_code == 200
    assert resp.text == "<svg></svg>"


def test_spa_fallback_never_swallows_api_requests(anon_client, fake_frontend_dist):
    """A nonexistent /api path must 404, never fall back to index.html —
    otherwise a typo'd or removed API route would look like a 200 HTML
    page instead of a clear 404."""
    resp = anon_client.get("/api/this-route-does-not-exist")
    assert resp.status_code == 404
    assert "spa shell" not in resp.text


def test_spa_fallback_does_not_intercept_real_protected_api_routes(anon_client, fake_frontend_dist):
    """Even with a frontend build present, a real protected API path must
    still reach the API router (and its auth check) rather than the SPA
    catch-all — asserted via the 401 an unauthenticated caller gets, which
    only the API router (not the SPA fallback) would ever return."""
    resp = anon_client.get("/api/roles")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "traversal_path",
    [
        "/../../../../../../../../etc/passwd",
        "/assets/../../../../../../etc/passwd",
        "/..%2f..%2f..%2f..%2fetc%2fpasswd",
    ],
)
def test_spa_fallback_blocks_path_traversal_outside_frontend_dist(anon_client, fake_frontend_dist, traversal_path):
    """A request trying to escape FRONTEND_DIST via `..` segments must never
    return a file from outside it — it should fall back to the SPA shell
    (public, but just the app's own UI code) exactly like any other unknown
    route, never leak `/etc/passwd` or similar."""
    resp = anon_client.get(traversal_path)
    assert resp.status_code == 200
    assert "root:" not in resp.text  # /etc/passwd's tell-tale first line
    assert "spa shell" in resp.text


def test_no_frontend_build_returns_404_not_error(anon_client, tmp_path, monkeypatch):
    """Without a build (the normal state for local backend-only dev, and
    for this test — deliberately pointed at an empty temp dir rather than
    relying on whether a real `frontend/dist` happens to exist on disk in
    whatever environment runs this suite) an unmatched frontend-shaped path
    must 404 cleanly rather than error."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "no-such-build")
    resp = anon_client.get("/some/deep/route")
    assert resp.status_code == 404


# --- Render-style startup ------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_render_style_port_startup_and_health_check(postgres_test_db):
    """Runs the literal production start command (docs/20 / render.yaml)
    against $PORT in a real subprocess and confirms it binds and answers
    the Render health-check path. Also exercises run_migrations() a second
    time against the same already-migrated test database, from a cold
    process — proving startup is safe to repeat (docs/14 §... / brief §14)."""
    port = _free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
        shell=True,
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                pytest.fail(f"server process exited early (code {proc.returncode}):\n{output}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                    assert resp.status == 200
                    assert json.loads(resp.read()) == {"status": "ok"}
                    return
            except (urllib.error.URLError, ConnectionError) as e:
                last_error = e
                time.sleep(0.5)
        pytest.fail(f"server did not become healthy at $PORT in time: {last_error}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
