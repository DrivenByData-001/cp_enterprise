from fastapi import APIRouter, HTTPException

from ..db import create_document, db_cursor, flatten_role_instance, upsert_role_instance
from ..embeddings import cosine_similarity, embed_text, ensure_profile_embedding, get_embeddings, set_embedding
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


def target_columns(cur, payload: TargetImport) -> dict:
    target, meta = payload.target, payload.metadata
    text = _compose_target_text(target)

    document_id = None
    if text.strip():
        # Neither 'original' (not a verbatim capture of pre-existing source
        # material — it's a user/AI-synthesized narrative) nor
        # 'legacy_extracted'/'reconstructed' (not from the SQLite migration,
        # not explicitly flagged as a reconstruction) genuinely fits a
        # target's narrative. 'unknown' is the least wrong of the four
        # production values: it correctly keeps this out of "trustworthy
        # original evidence" without asserting something false. See docs/14 §3.
        document_id, _duplicate_of = create_document(
            cur,
            kind="narrative",
            content_text=text,
            provenance_quality="unknown",
            title=target.title,
            source=meta.source,
            url=meta.url,
        )

    return {
        "instance_type": "user_defined_target",
        "target_basis": "imagined" if target.is_imagined else "real_role",
        "document_id": document_id,
        "title": target.title,
        "organisation": target.organisation,
        "seniority_level": target.seniority_level,
        "description": target.description,
        "summary": target.summary,
        "career_track": target.career_track,
        "extraction_status": meta.extraction_status,
        "extraction_notes": meta.notes_for_user,
        "legacy_analysis": {
            "typical_tasks": target.typical_tasks or None,
            "skill_decomposition": [s.model_dump() for s in target.skill_decomposition] or None,
            "technical_subjects": [s.model_dump() for s in target.technical_subjects] or None,
            "grounding_note": target.grounding_note,
            "feasibility_note": target.feasibility_note,
            "is_plausible": target.is_plausible,
            "raw_json": payload.model_dump(mode="json"),
        },
        "_embedding_text": text,
    }


@router.get("")
def list_targets():
    with db_cursor() as cur:
        _, profile_vec = ensure_profile_embedding(cur)

        cur.execute(
            "SELECT ri.*, d.url AS url FROM jobber.role_instance ri "
            "LEFT JOIN jobber.document d ON d.id = ri.document_id "
            "WHERE ri.instance_type != 'observed_posting' ORDER BY ri.created_at DESC"
        )
        rows = [flatten_role_instance(r) for r in cur.fetchall()]

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
def update_target(target_id: str, payload: TargetImport):
    with db_cursor() as cur:
        cur.execute("SELECT instance_type FROM jobber.role_instance WHERE id = %s", (target_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "target not found")
    if row["instance_type"] == "observed_posting":
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
