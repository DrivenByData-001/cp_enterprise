import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..db import db_cursor, row_to_dict, upsert_job_role
from ..embeddings import cosine_similarity, dumps_vec, embed_text, embedding_model_name, loads_vec
from ..models import TargetImport

router = APIRouter(prefix="/api/targets", tags=["targets"])


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


def target_columns(payload: TargetImport) -> dict:
    target, meta = payload.target, payload.metadata
    text = _compose_target_text(target)
    vector = embed_text(text) if text else []

    return {
        "node_type": "target_imagined" if target.is_imagined else "target_real",
        "title": target.title,
        "organisation": target.organisation,
        "seniority_level": target.seniority_level,
        "captured_at": meta.captured_at or datetime.now(timezone.utc).isoformat(),
        "source": meta.source,
        "url": meta.url,
        "description": target.description,
        "summary": target.summary,
        "career_track": target.career_track,
        "extraction_status": meta.extraction_status,
        "extraction_notes": meta.notes_for_user,
        "typical_tasks": json.dumps(target.typical_tasks) if target.typical_tasks else None,
        "skill_decomposition": (
            json.dumps([s.model_dump() for s in target.skill_decomposition])
            if target.skill_decomposition
            else None
        ),
        "technical_subjects": (
            json.dumps([s.model_dump() for s in target.technical_subjects])
            if target.technical_subjects
            else None
        ),
        "grounding_note": target.grounding_note,
        "feasibility_note": target.feasibility_note,
        "is_plausible": None if target.is_plausible is None else int(target.is_plausible),
        "raw_json": payload.model_dump_json(),
        "embedding": dumps_vec(vector) if vector else None,
        "embedding_model": embedding_model_name() if vector else None,
        "embedded_at": datetime.now(timezone.utc).isoformat() if vector else None,
    }


@router.get("")
def list_targets():
    with db_cursor() as cur:
        cur.execute(
            "SELECT embedding FROM profile_snapshots WHERE is_current = 1 ORDER BY created_at DESC LIMIT 1"
        )
        prow = cur.fetchone()
        profile_vec = loads_vec(prow["embedding"]) if prow else []

        cur.execute("SELECT * FROM job_roles WHERE node_type != 'posting' ORDER BY created_at DESC")
        rows = [row_to_dict(r) for r in cur.fetchall()]

        cur.execute("SELECT id, embedding FROM job_roles WHERE node_type != 'posting'")
        vec_by_id = {r["id"]: loads_vec(r["embedding"]) for r in cur.fetchall()}

    for r in rows:
        r["similarity"] = cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None
    return rows


@router.post("")
def import_target(payload: TargetImport):
    skills = [s.model_dump() for s in payload.skills]
    role_id = upsert_job_role(None, target_columns(payload), skills)
    return {"id": role_id, "status": "imported"}


@router.put("/{target_id}")
def update_target(target_id: int, payload: TargetImport):
    with db_cursor() as cur:
        cur.execute("SELECT node_type FROM job_roles WHERE id = ?", (target_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "target not found")
    if row["node_type"] == "posting":
        raise HTTPException(400, "this is a posting — edit it via PUT /api/roles/{id}")

    skills = [s.model_dump() for s in payload.skills]
    upsert_job_role(target_id, target_columns(payload), skills)
    return {"id": target_id, "status": "updated"}
