from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..db import db_cursor
from ..embeddings import dumps_vec, embed_text, embedding_model_name
from ..models import ProfileUpdate

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
def get_current_profile():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, narrative_text, embedding_model, created_at FROM profile_snapshots "
            "WHERE is_current = 1 ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(row)


@router.get("/history")
def get_profile_history():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, narrative_text, embedding_model, is_current, created_at "
            "FROM profile_snapshots ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.post("")
def update_profile(payload: ProfileUpdate):
    text = payload.narrative_text.strip()
    if not text:
        raise HTTPException(400, "narrative_text cannot be empty")

    vector = embed_text(text)

    with db_cursor() as cur:
        cur.execute("UPDATE profile_snapshots SET is_current = 0")
        cur.execute(
            """
            INSERT INTO profile_snapshots
                (narrative_text, embedding, embedding_model, is_current, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                text,
                dumps_vec(vector) if vector else None,
                embedding_model_name() if vector else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        new_id = cur.lastrowid

    return {"id": new_id, "status": "saved"}
