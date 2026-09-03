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
