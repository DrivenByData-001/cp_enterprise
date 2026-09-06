"""Environment-based configuration. The one place in the app that reads
`os.environ` for connection/credential values — everything else takes them as
function arguments or imports from here, so `.env`/deployment config is the
single source of truth for where the app runs, per docs/14 and docs/15.
"""

import os


class ConfigError(RuntimeError):
    """A required environment variable is missing."""


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ConfigError(
            "DATABASE_URL is not set. See .env.example — it must be a Postgres "
            "connection string (e.g. the Supabase project's connection string)."
        )
    return url


def test_database_url() -> str:
    """Only used by the test suite (backend/tests/conftest.py), never by the
    running app. Defaults to a local Postgres so `pytest` works out of the box
    on a machine with Postgres installed, without requiring any cloud
    credential — see docs/14 §7."""
    return os.getenv("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")


# --- Authentication / deployment (docs/20-render-deployment.md) ------------
#
# Auth is always on — there is no "development bypass" flag. Locally and in
# production alike, APP_SESSION_SECRET and APP_AUTH_PASSWORD_HASH must be
# set or the app refuses to start (see app/main.py, which calls both at
# import time). This is deliberate: inferring "safe to skip auth" from a
# hostname or an APP_ENV value would be exactly the kind of implicit,
# easy-to-misconfigure gate the brief asks to avoid. APP_ENV itself is only
# consulted for one thing — whether the session cookie is marked `Secure`.


def session_secret() -> str:
    value = os.getenv("APP_SESSION_SECRET")
    if not value:
        raise ConfigError(
            "APP_SESSION_SECRET is not set. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))" '
            "and set it as an environment variable (see docs/20-render-deployment.md)."
        )
    return value


def auth_password_hash() -> str:
    value = os.getenv("APP_AUTH_PASSWORD_HASH")
    if not value:
        raise ConfigError(
            "APP_AUTH_PASSWORD_HASH is not set. Generate one with "
            "`python backend/scripts/hash_password.py` and set it as an "
            "environment variable (see docs/20-render-deployment.md)."
        )
    return value


def is_production() -> bool:
    """Explicit opt-in only (APP_ENV=production) — never inferred from
    hostname/port. Controls exactly one thing: whether the session cookie is
    marked `Secure` (requires HTTPS, which Render terminates for us but a
    local `uvicorn --reload` over plain http does not have)."""
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def cors_allowed_origins() -> list[str]:
    """Cross-origin allowlist for the (non-default) split frontend/backend
    deployment — see docs/20 "Alternative: two-service deployment". The
    default same-origin deployment never issues a cross-origin request, so
    this normally stays empty; set APP_CORS_ORIGINS to a comma-separated list
    of exact origins (e.g. "https://cp-enterprise-frontend.onrender.com") if
    the frontend is ever split into its own Static Site. Never "*" — requests
    carry the session cookie (allow_credentials=True in app/main.py), and
    CORS forbids combining a wildcard origin with credentialed requests.
    The local Vite dev server origin is added automatically outside
    production so `npm run dev` keeps working against a backend run
    separately on a different port."""
    raw = os.getenv("APP_CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not is_production():
        for dev_origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
            if dev_origin not in origins:
                origins.append(dev_origin)
    return origins
