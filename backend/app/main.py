from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routes import concepts, episodes, import_routes, profile, roles, space, targets

app = FastAPI(title="Career Navigator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


app.include_router(import_routes.router)
app.include_router(roles.router)
app.include_router(profile.router)
app.include_router(space.router)
app.include_router(targets.router)
app.include_router(episodes.router)
app.include_router(concepts.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
