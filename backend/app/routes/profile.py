from fastapi import APIRouter, HTTPException

from ..db import db_cursor
from ..embeddings import embed_text, embedding_model_name, set_embedding
from ..models import ProfileUpdate

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
def get_current_profile():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, narrative_text, embedding_model, created_at FROM jobber.profile_snapshots "
            "WHERE is_current = TRUE ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row


@router.get("/history")
def get_profile_history():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, narrative_text, embedding_model, is_current, created_at "
            "FROM jobber.profile_snapshots ORDER BY created_at DESC"
        )
        return cur.fetchall()


@router.post("")
def update_profile(payload: ProfileUpdate):
    text = payload.narrative_text.strip()
    if not text:
        raise HTTPException(400, "narrative_text cannot be empty")

    vector = embed_text(text)

    with db_cursor() as cur:
        cur.execute("UPDATE jobber.profile_snapshots SET is_current = FALSE")
        cur.execute(
            """
            INSERT INTO jobber.profile_snapshots (narrative_text, embedding_model, is_current, created_at)
            VALUES (%s, %s, TRUE, now())
            RETURNING id
            """,
            (text, embedding_model_name() if vector else None),
        )
        new_id = cur.fetchone()["id"]
        if vector:
            set_embedding(cur, "profile_snapshot", new_id, vector)

    return {"id": new_id, "status": "saved"}
