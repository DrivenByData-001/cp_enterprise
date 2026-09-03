from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..db import db_cursor, get_or_create_document, row_to_dict, upsert_role_instance
from ..embeddings import cosine_similarity, embed_text, get_embedding, get_embeddings, set_embedding
from ..models import TargetImport

router = APIRouter(prefix="/api/targets", tags=["targets"])

_TARGET_JSON_COLUMNS = ("typical_tasks", "skill_decomposition", "technical_subjects", "raw_json")


def _compose_target_text(target) -> str:
    skill_names = [s.skill for s in target.skill_decomposition]
    subject_names = [s.subject for s in target.technical_subjects]
    parts = [
        target.title,
        target.summary,
        target.description,
        "\n".join(target.typical_tasks),
        "Skills: " + ", ".join(skill_names) if skill_names else None,
        "Technical subjects: " + ", ".join(subject_names) if subject_names else None,
    ]
    return "\n\n".join(p for p in parts if p)


def target_columns(cur, payload: TargetImport) -> dict:
    target, meta = payload.target, payload.metadata
    text = _compose_target_text(target)

    document_id = None
    if text.strip():
        # A target's text is a user/AI-composed narrative, never an original
        # employer advert — 'user_paste' provenance, never 'original_capture'.
        document_id, _ = get_or_create_document(
            cur,
            kind="narrative",
            body=text,
            provenance="user_paste",
            title=target.title,
            source=meta.source,
            url=meta.url,
        )

    return {
        "kind": "target_imagined" if target.is_imagined else "target_real",
        "document_id": document_id,
        "title": target.title,
        "organisation": target.organisation,
        "seniority_level": target.seniority_level,
        "captured_at": meta.captured_at or datetime.now(timezone.utc).isoformat(),
        "url": meta.url,
        "description": target.description,
        "summary": target.summary,
        "career_track": target.career_track,
        "extraction_status": meta.extraction_status,
        "extraction_notes": meta.notes_for_user,
        "typical_tasks": target.typical_tasks or None,
        "skill_decomposition": [s.model_dump() for s in target.skill_decomposition] or None,
        "technical_subjects": [s.model_dump() for s in target.technical_subjects] or None,
        "grounding_note": target.grounding_note,
        "feasibility_note": target.feasibility_note,
        "is_plausible": target.is_plausible,
        "raw_json": payload.model_dump(mode="json"),
        "_embedding_text": text,
    }


@router.get("")
def list_targets():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM jobber.profile_snapshots WHERE is_current = TRUE ORDER BY created_at DESC LIMIT 1"
        )
        prow = cur.fetchone()
        profile_vec = get_embedding(cur, "profile_snapshot", prow["id"]) if prow else []

        cur.execute(
            "SELECT ri.*, lra.* FROM jobber.role_instance ri "
            "LEFT JOIN jobber.legacy_role_analysis lra ON lra.role_instance_id = ri.id "
            "WHERE ri.kind != 'posting' ORDER BY ri.created_at DESC"
        )
        rows = [row_to_dict(r, _TARGET_JSON_COLUMNS) for r in cur.fetchall()]
        for r in rows:
            r["node_type"] = r["kind"]

        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in rows])

    for r in rows:
        r["similarity"] = cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None
    return rows


@router.post("")
def import_target(payload: TargetImport):
    skills = [s.model_dump() for s in payload.skills]
    with db_cursor() as cur:
        columns = target_columns(cur, payload)
        embedding_text = columns.pop("_embedding_text")
        role_id = upsert_role_instance(cur, None, columns, skills)
        vector = embed_text(embedding_text) if embedding_text else []
        if vector:
            set_embedding(cur, "role_instance", role_id, vector)
    return {"id": role_id, "status": "imported"}


@router.put("/{target_id}")
def update_target(target_id: int, payload: TargetImport):
    with db_cursor() as cur:
        cur.execute("SELECT kind FROM jobber.role_instance WHERE id = %s", (target_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "target not found")
    if row["kind"] == "posting":
        raise HTTPException(400, "this is a posting — edit it via PUT /api/roles/{id}")

    skills = [s.model_dump() for s in payload.skills]
    with db_cursor() as cur:
        columns = target_columns(cur, payload)
        embedding_text = columns.pop("_embedding_text")
        upsert_role_instance(cur, target_id, columns, skills)
        vector = embed_text(embedding_text) if embedding_text else []
        if vector:
            set_embedding(cur, "role_instance", target_id, vector)
    return {"id": target_id, "status": "updated"}
