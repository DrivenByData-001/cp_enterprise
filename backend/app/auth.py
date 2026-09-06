"""Session-cookie authentication for the single-operator deployment.

See docs/20-render-deployment.md for the full design rationale. In short:
this app has exactly one legitimate user and one shared password, so a
session is nothing more than a Starlette `SessionMiddleware` cookie —
signed and self-expiring via `itsdangerous.TimestampSigner` (wired up in
app/main.py), never a database-backed session table. There is nothing to
look up server-side beyond "does this request carry a validly-signed,
non-expired `{'authenticated': True}` cookie" — a sessions table would add
persistence and migration surface for no benefit at this scale.

`require_auth` is the single enforcement point. It is wired in app/main.py
as a router-level dependency on every /api router except health and auth
itself — see that file's own comment for the exact allowlist and rationale.
There is deliberately no per-route `Depends(require_auth)` sprinkled through
the individual route modules in `app/routes/`.
"""

import time
from collections import defaultdict

import bcrypt
from fastapi import HTTPException, Request, status

from . import config

SESSION_COOKIE_NAME = "cp_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 days — see docs/20 "Session lifetime".

_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_ATTEMPTS = 5
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(request: Request) -> None:
    """In-process fixed-window limiter: at most `_LOGIN_MAX_ATTEMPTS` failed
    logins per client per `_LOGIN_WINDOW_SECONDS`. Deliberately simple (no
    Redis or other shared store) — see docs/20 "CSRF / brute-force login
    protection" for why that's an accepted tradeoff at this scale: a single
    Render instance, a single legitimate user, and a reset on every
    deploy/restart that costs nothing because there is no legitimate reason
    for genuine login traffic to be bursty."""
    key = _client_key(request)
    now = time.monotonic()
    recent = [t for t in _failed_attempts[key] if now - t < _LOGIN_WINDOW_SECONDS]
    _failed_attempts[key] = recent
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a minute.",
        )


def record_login_failure(request: Request) -> None:
    _failed_attempts[_client_key(request)].append(time.monotonic())


def record_login_success(request: Request) -> None:
    _failed_attempts.pop(_client_key(request), None)


def verify_password(password: str) -> bool:
    stored_hash = config.auth_password_hash()
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed APP_AUTH_PASSWORD_HASH (not a bcrypt hash) must never
        # crash the request or, worse, be treated as "no password required" —
        # it just means nobody can log in until the operator fixes it.
        return False


def require_auth(request: Request) -> None:
    """FastAPI dependency: raises 401 unless the request's session cookie
    says `authenticated`. Wired at router-level in app/main.py — see the
    module docstring above."""
    if not request.session.get("authenticated"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
