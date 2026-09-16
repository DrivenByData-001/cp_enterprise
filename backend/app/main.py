from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, require_auth
from .db import reset_pool, run_migrations
from .routes import (
    archetype_context,
    archetypes,
    auth as auth_routes,
    capabilities,
    comparison,
    concept_dossier,
    concepts,
    documents,
    economics,
    episodes,
    evaluation,
    import_routes,
    market_data,
    pathways,
    preferences,
    profile,
    profile360,
    role_context,
    role_economics,
    role_instances,
    roles,
    space,
    targets,
    trends,
    vocabulary,
)

# The built frontend (`npm run build` inside frontend/), served same-origin
# by this backend in production — see docs/20-render-deployment.md for why
# this was chosen over a separate Static Site. Absent in local development
# (Vite's own dev server serves the frontend and proxies /api to us
# instead, per vite.config.ts / docs/19), so everything below the "Frontend
# static serving" section is conditional on this directory existing.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="Career Navigator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Calling config.session_secret() here means a deployment with no
# APP_SESSION_SECRET set fails at import time — i.e. the process never
# comes up and Render reports the deploy as failed, rather than silently
# serving traffic with authentication effectively disabled. See docs/20
# "Fail-closed configuration".
app.add_middleware(
    SessionMiddleware,
    secret_key=config.session_secret(),
    session_cookie=SESSION_COOKIE_NAME,
    max_age=SESSION_MAX_AGE_SECONDS,
    same_site="strict",
    https_only=config.is_production(),
)

# Validated eagerly (not just lazily on the first login attempt) for the
# same fail-closed reason as the session secret above.
config.auth_password_hash()


@app.on_event("startup")
def startup():
    run_migrations()


@app.on_event("shutdown")
def shutdown():
    # Close the connection pool explicitly rather than leaving it to
    # psycopg_pool's __del__ at interpreter shutdown — see reset_pool's
    # docstring (app/db.py) for why that path logs a spurious thread-join
    # warning.
    reset_pool()


# --- Route registration / central auth policy ---------------------------
#
# Every /api router is protected by `require_auth` (session cookie
# required) EXCEPT:
#   - auth_routes itself (`/api/auth/login|logout|status`) — login can't
#     require a session, and logout/status are harmless when logged out;
#   - `GET /api/health` below — Render's health checker calls it with no
#     session and must keep working.
# This is the complete public-endpoint allowlist; everything else exposes
# either personal career data or a mutating operation and must not be
# reachable without authenticating first. See docs/20 "Protected endpoint
# policy" and tests/test_auth.py, which asserts this holds for every route
# actually registered on `app` (so a future router added without the
# `dependencies=` below fails that test rather than shipping unprotected).
app.include_router(auth_routes.router)

_protected = [Depends(require_auth)]
app.include_router(import_routes.router, dependencies=_protected)
app.include_router(roles.router, dependencies=_protected)
app.include_router(role_context.router, dependencies=_protected)
app.include_router(profile.router, dependencies=_protected)
app.include_router(space.router, dependencies=_protected)
app.include_router(targets.router, dependencies=_protected)
app.include_router(episodes.router, dependencies=_protected)
app.include_router(concepts.router, dependencies=_protected)
app.include_router(concept_dossier.router, dependencies=_protected)
app.include_router(role_instances.router, dependencies=_protected)
app.include_router(profile360.router, dependencies=_protected)
app.include_router(preferences.router, dependencies=_protected)
app.include_router(comparison.router, dependencies=_protected)
app.include_router(capabilities.router, dependencies=_protected)
app.include_router(evaluation.router, dependencies=_protected)
app.include_router(documents.router, dependencies=_protected)
app.include_router(trends.router, dependencies=_protected)
app.include_router(vocabulary.router, dependencies=_protected)
app.include_router(archetypes.router, dependencies=_protected)
app.include_router(archetype_context.router, dependencies=_protected)
app.include_router(economics.router, dependencies=_protected)
app.include_router(market_data.router, dependencies=_protected)
app.include_router(role_economics.router, dependencies=_protected)
app.include_router(pathways.router, dependencies=_protected)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Frontend static serving / SPA fallback ------------------------------
#
# The /assets mount is only registered once frontend/dist exists (a
# production build has been run) — StaticFiles requires its directory to
# exist at mount time. The catch-all route is always registered (its own
# `index.is_file()` check below 404s cleanly when there's no build, e.g.
# local backend-only dev or the test suite — see tests/test_auth.py) and
# must be the LAST route registered so every /api/* router above it wins
# the match first; the explicit "api/" guard inside it is defence in depth
# against that ever changing, per the brief's "Ensure /api/... requests are
# never swallowed by the frontend fallback." Serving the SPA shell itself
# requires no auth — it contains no data, only UI code, and every actual
# data request it makes is one of the protected /api/* calls above.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if full_path:
        # `full_path` is attacker-controlled and can contain literal ".."
        # segments (FastAPI's `path` converter does not strip them) — joining
        # it onto FRONTEND_DIST unchecked would let a request like
        # `/../../etc/passwd` resolve outside the frontend build directory.
        # `.resolve()` + `relative_to()` is the same containment check
        # Starlette's own StaticFiles uses (see its `lookup_path`).
        candidate = (FRONTEND_DIST / full_path).resolve()
        served_root = FRONTEND_DIST.resolve()
        if candidate.is_relative_to(served_root) and candidate.is_file():
            return FileResponse(candidate)
    return FileResponse(index)
