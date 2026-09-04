from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import reset_pool, run_migrations
from .routes import (
    capabilities,
    comparison,
    concepts,
    documents,
    episodes,
    evaluation,
    import_routes,
    preferences,
    profile,
    profile360,
    role_instances,
    roles,
    space,
    targets,
    trends,
    vocabulary,
)

app = FastAPI(title="Career Navigator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


app.include_router(import_routes.router)
app.include_router(roles.router)
app.include_router(profile.router)
app.include_router(space.router)
app.include_router(targets.router)
app.include_router(episodes.router)
app.include_router(concepts.router)
app.include_router(role_instances.router)
app.include_router(profile360.router)
app.include_router(preferences.router)
app.include_router(comparison.router)
app.include_router(capabilities.router)
app.include_router(evaluation.router)
app.include_router(documents.router)
app.include_router(trends.router)
app.include_router(vocabulary.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
