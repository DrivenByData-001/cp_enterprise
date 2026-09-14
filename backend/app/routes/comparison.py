"""Evidence-backed role comparison (brief §11/§16/§17/§28), upgraded to
Phase 3's capability engine. Status assignment lives in `app.capability_engine`
only — this route is presentation: it enriches the engine's trace with the
role-side document detail (unchanged Phase 2 shape) and returns the
structural picture (per-requirement status + blocking/unverified gaps)
before any score. `fit_score`/`embedding_similarity` are included but
deliberately secondary (brief §18/§19) — the frontend must not present them
as the headline result.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from datetime import date
from typing import Literal
from uuid import UUID

from .. import capability_engine
from ..db import db_cursor, instance_type_to_app_kind
from ..profile360_promotion import Profile360PromotionError, promote_assertion_to_profile360
from ..role_requirements import load_role_requirements
from ..target_mapping import target_mapping_summary

router = APIRouter(prefix="/api/comparison", tags=["comparison"])


class DevelopmentActionInput(BaseModel):
    concept_id: UUID
    title: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=10000)
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("An action title is required")
        return value.strip()


class DevelopmentActionUpdate(BaseModel):
    status: Literal["open", "done"]


@router.get("/role/{role_id}/actions")
def list_actions(role_id: UUID):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.development_action WHERE role_instance_id = %s ORDER BY created_at, id", (role_id,))
        return cur.fetchall()


@router.post("/role/{role_id}/actions")
def create_action(role_id: UUID, payload: DevelopmentActionInput):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Role not found")
        cur.execute("SELECT id FROM jobber.concept WHERE id = %s AND status = 'active'", (payload.concept_id,))
        if not cur.fetchone():
            raise HTTPException(400, "Choose an active concept")
        cur.execute("""INSERT INTO jobber.development_action (role_instance_id, concept_id, title, note, due_date)
                       VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                    (role_id, payload.concept_id, payload.title, payload.note, payload.due_date))
        return cur.fetchone()


@router.patch("/role/{role_id}/actions/{action_id}")
def update_action(role_id: UUID, action_id: UUID, payload: DevelopmentActionUpdate):
    with db_cursor() as cur:
        cur.execute("UPDATE jobber.development_action SET status = %s, updated_at = now() "
                    "WHERE id = %s AND role_instance_id = %s RETURNING *", (payload.status, action_id, role_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Action not found on this role")
        return row


@router.delete("/role/{role_id}/actions/{action_id}")
def delete_action(role_id: UUID, action_id: UUID):
    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.development_action WHERE id = %s AND role_instance_id = %s", (action_id, role_id))
        if not cur.rowcount:
            raise HTTPException(404, "Action not found on this role")
    return {"status": "deleted"}


def _requirement_documents(cur, requirement_claim_ids: list[str]) -> dict[str, dict | None]:
    """Role-side document detail per requirement_claim — kept as a small,
    separate query rather than folded into capability_engine.derive_role_fit,
    since that engine is deliberately jobber.document-agnostic (role-fit
    status never depends on which document a requirement came from)."""
    if not requirement_claim_ids:
        return {}
    cur.execute(
        """
        SELECT rc.id AS requirement_claim_id, d.id AS document_id, d.title, d.provenance_quality, d.url
        FROM jobber.requirement_claim rc
        LEFT JOIN jobber.document d ON d.id = rc.document_id
        WHERE rc.id = ANY(%s::uuid[])
        """,
        (requirement_claim_ids,),
    )
    out: dict[str, dict | None] = {}
    for r in cur.fetchall():
        out[str(r["requirement_claim_id"])] = (
            {"id": str(r["document_id"]), "title": r["title"], "provenance": r["provenance_quality"], "url": r["url"]}
            if r["document_id"]
            else None
        )
    return out


@router.get("/role/{role_instance_id}")
def compare_role(role_instance_id: str):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, instance_type, target_basis FROM jobber.role_instance WHERE id = %s",
            (role_instance_id,),
        )
        role = cur.fetchone()
        if not role:
            raise HTTPException(404, "role_instance not found")
        role = {
            "id": str(role["id"]),
            "title": role["title"],
            "kind": instance_type_to_app_kind(role["instance_type"], role["target_basis"]),
        }
        mapping = (target_mapping_summary(cur, role_instance_id, load_role_requirements(cur, role_instance_id))
                   if role["kind"] != "posting" else None)

        try:
            fit = capability_engine.derive_role_fit(cur, role_instance_id)
        except capability_engine.RoleInstanceNotFoundError:
            raise HTTPException(404, "role_instance not found")

        trace_items = fit["trace"]["items"]
        claim_ids = [item["requirement_claim_id"] for item in trace_items if item["requirement_claim_id"]]
        docs_by_claim = _requirement_documents(cur, claim_ids)

        # Keep personal notes retractable even when stronger mapped evidence wins
        # the status. Reading this separately never changes the engine's judgment.
        concept_ids = list({item["concept"]["id"] for item in trace_items})
        cur.execute("SELECT id, jobber_concept_id, note, created_at, promoted_to_profile360_at "
                    "FROM jobber.person_capability_assertion WHERE jobber_concept_id = ANY(%s::uuid[])", (concept_ids,))
        assertions = {}
        for row in cur.fetchall():
            assertion = dict(row)
            key = str(assertion.pop("jobber_concept_id"))
            assertion["id"] = str(assertion["id"])
            assertions[key] = assertion

        items = []
        for item in trace_items:
            role_side = {
                "requirement_claim_id": item["requirement_claim_id"],
                "role_skill_observation_id": item.get("role_skill_observation_id"),
                "requirement_source": item.get("requirement_source"),
                "requirement_type": item["requirement_type"],
                "basis": item["role_side"]["basis"],
                "review_status": item["role_side"]["review_status"],
                "evidence_span": item["role_side"]["evidence_span"],
                "document": docs_by_claim.get(item["requirement_claim_id"]) if item["requirement_claim_id"] else None,
            }
            detail = item["detail"]
            if detail["kind"] == "concept":
                person_side = {
                    "mappings": detail["mappings"],
                    "assertion": detail["assertion"],
                    "component_of": detail.get("component_of", []),
                    "coverage": None,
                }
            else:
                person_side = {"mappings": [], "assertion": None, "component_of": [], "coverage": detail["coverage"]}
            person_side["assertion"] = assertions.get(item["concept"]["id"])
            items.append({"concept": item["concept"], "status": item["status"], "role_side": role_side, "person_side": person_side})

    counts = {
        "evidenced": fit["n_evidenced"],
        "partial": fit["n_partial"],
        "user_asserted": fit["n_asserted"],
        "not_found": fit["n_not_found"],
    }

    return {
        "role": role,
        "items": items,
        "counts": counts,
        "target_mapping": mapping,
        # Structural summary (brief §17/§18) — shown before, and separate
        # from, fit_score in the UI.
        "blocking_gaps": fit["blocking_gaps"],
        "unverified_required": fit["unverified_required"],
        "fit_score": None if mapping is not None and not mapping["complete"] else fit["fit_score"],
        "embedding_similarity": fit["embedding_similarity"],
        "engine_version": capability_engine.ENGINE_VERSION,
    }


class AssertCapability(BaseModel):
    concept_id: str
    note: str | None = None


@router.post("/assert")
def assert_capability(payload: AssertCapability):
    """The one-click "I have done this" action (doc 11 §5.3) — records that
    the user asserts this concept with no document behind it. Not a claim:
    see jobber.person_capability_assertion / docs/14 §6 for why this is
    deliberately not profile360's concern, and a TEMPORARY navigation
    override only — see `promote` below for the path into profile360's own
    review pipeline. Unchanged from Phase 2."""
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.concept WHERE id = %s AND status = 'active'", (payload.concept_id,))
        if not cur.fetchone():
            raise HTTPException(400, "concept does not exist or is not active")
        cur.execute(
            """
            INSERT INTO jobber.person_capability_assertion (jobber_concept_id, asserted, note)
            VALUES (%s, TRUE, %s)
            ON CONFLICT (jobber_concept_id) DO UPDATE SET asserted = TRUE, note = EXCLUDED.note
            RETURNING id
            """,
            (payload.concept_id, payload.note),
        )
        new_id = str(cur.fetchone()["id"])
    return {"id": new_id, "status": "asserted"}


@router.delete("/assert/{concept_id}")
def retract_assertion(concept_id: str):
    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s", (concept_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "no assertion for this concept")
    return {"status": "retracted"}


@router.post("/assert/{concept_id}/promote")
def promote_assertion(concept_id: str):
    """Promotes a jobber-local assertion into profile360's own review
    pipeline (profile360.manual_import_queue, confirmed schema — see
    app/profile360_promotion.py) so profile360 can review and confirm it on
    its own terms."""
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s", (concept_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "no assertion for this concept")
        try:
            return promote_assertion_to_profile360(cur, str(row["id"]))
        except Profile360PromotionError as e:
            raise HTTPException(503, str(e)) from e
