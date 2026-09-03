"""Evidence-backed role comparison (brief §11). Deliberately does not build the
full doc 11 Phase 3/4 derivation engine (component_of edges, compositional
coverage) — that machinery doesn't exist yet even for the local vocabulary.
What this does implement, honestly: for each requirement_claim on a role, walk
the mapping tables to the same concept and report exactly one of the four
epistemic statuses, with a trace in both directions (brief §11) rather than a
score. "Not found" always means absence of evidence, never asserted absence of
ability (brief definition of done #8).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import profile360_reader as p360
from ..db import db_cursor, instance_type_to_app_kind
from ..profile360_promotion import Profile360PromotionError, promote_assertion_to_profile360

router = APIRouter(prefix="/api/comparison", tags=["comparison"])


def _person_side(cur, concept_id: str) -> dict:
    cur.execute(
        """
        SELECT id, profile360_claim_id AS profile360_id, review_status, mapping_basis, 'claim' AS mapping_kind
        FROM jobber.profile360_claim_mapping WHERE jobber_concept_id = %s
        UNION ALL
        SELECT id, profile360_capability_id AS profile360_id, review_status, mapping_basis, 'capability' AS mapping_kind
        FROM jobber.profile360_capability_mapping WHERE jobber_capability_concept_id = %s
        """,
        (concept_id, concept_id),
    )
    mappings = cur.fetchall()

    for m in mappings:
        try:
            source = (
                p360.get_claim(cur, m["profile360_id"])
                if m["mapping_kind"] == "claim"
                else p360.get_capability(cur, m["profile360_id"])
            )
            m["display"] = p360.display_text(source) if source else None
        except p360.Profile360UnavailableError:
            m["display"] = None

    cur.execute(
        "SELECT id, note, created_at, promoted_to_profile360_at FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s",
        (concept_id,),
    )
    assertion = cur.fetchone()

    accepted = [m for m in mappings if m["review_status"] == "accepted"]
    if accepted:
        return {"status": "evidenced", "mappings": accepted, "assertion": None}
    pending = [m for m in mappings if m["review_status"] == "unreviewed"]
    if pending:
        return {"status": "partial", "mappings": pending, "assertion": None}
    if assertion:
        return {"status": "user_asserted", "mappings": [], "assertion": assertion}
    return {"status": "not_found", "mappings": [], "assertion": None}


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

        cur.execute(
            """
            SELECT rc.id, rc.requirement_type, rc.basis, rc.review_status, rc.evidence_span,
                   c.id AS concept_id, c.canonical_name, c.type_code,
                   d.id AS document_id, d.title AS document_title, d.provenance_quality AS document_provenance, d.url AS document_url
            FROM jobber.requirement_claim rc
            JOIN jobber.concept c ON c.id = rc.concept_id
            LEFT JOIN jobber.document d ON d.id = rc.document_id
            WHERE rc.role_instance_id = %s
            ORDER BY rc.requirement_type, c.canonical_name
            """,
            (role_instance_id,),
        )
        requirement_rows = cur.fetchall()

        items = []
        for rc in requirement_rows:
            concept_id = str(rc["concept_id"])
            person = _person_side(cur, concept_id)
            items.append(
                {
                    "concept": {"id": concept_id, "canonical_name": rc["canonical_name"], "type_code": rc["type_code"]},
                    "status": person["status"],
                    "role_side": {
                        "requirement_claim_id": str(rc["id"]),
                        "requirement_type": rc["requirement_type"],
                        "basis": rc["basis"],
                        "review_status": rc["review_status"],
                        "evidence_span": rc["evidence_span"],
                        "document": (
                            {"id": str(rc["document_id"]), "title": rc["document_title"], "provenance": rc["document_provenance"], "url": rc["document_url"]}
                            if rc["document_id"]
                            else None
                        ),
                    },
                    "person_side": {"mappings": person["mappings"], "assertion": person["assertion"]},
                }
            )

    counts = {"evidenced": 0, "partial": 0, "user_asserted": 0, "not_found": 0}
    for item in items:
        counts[item["status"]] += 1

    return {"role": role, "items": items, "counts": counts}


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
    review pipeline."""
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
