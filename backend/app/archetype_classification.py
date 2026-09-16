"""Reviewed role-archetype classification (build §5).

Three rules define this module, and every function below exists to enforce
one of them:

1. **The AI may only propose an archetype that already exists and is
   active.** The model is handed the catalogue and told to copy a name
   verbatim; the server then resolves that name against
   `concept(type_code='role_archetype', status='active')` and discards
   anything that does not match exactly. There is no code path from a model
   response to `INSERT INTO jobber.concept` — auto-creation is structurally
   impossible here, not merely discouraged.

2. **Nothing is ever assigned silently.** `propose_archetype` writes no
   `role_instance.archetype_concept_id`. Assignment happens only through
   `assign_archetype`, called from an explicit endpoint a human triggers,
   and it accepts `None` — "leave unclassified" is a first-class, valid
   outcome, not a failure to decide.

3. **Manual choice always beats the proposal.** `assign_archetype` neither
   knows nor cares whether a proposal was ever made; the reviewer can search
   the catalogue and pick anything active.

Legacy `role_instance.career_track` is deliberately left completely alone —
not read, not written, not deprecated. Pathways and the economics layer
prefer the reviewed archetype; the old free-text track remains exactly where
it was for everything that already uses it.
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import to_json_param
from .role_requirements import load_role_requirements

TASK = "role_archetype_classify"
PROMPT_NAME = "classify_role_archetype.md"

_MAX_SOURCE_CHARS = 12000


class ArchetypeClassificationSubjectError(ValueError):
    """No such role — a 404 at the route layer."""


class NoArchetypeCatalogueError(ValueError):
    """There are no active archetypes to choose from. Proposing is
    meaningless in that state, and the honest answer is to say so rather
    than to let the model invent the catalogue it was supposed to pick
    from."""


class ArchetypeAssignmentError(ValueError):
    """The requested archetype is not an active role_archetype concept."""


class ArchetypeProposal(BaseModel):
    archetype_name: Optional[str] = None
    confidence: Optional[str] = None
    rationale: Optional[str] = None
    alternatives: list[str] = []


def _safe_task_metadata() -> tuple[str, str]:
    try:
        model = ai_model_name()
    except AIConfigError:
        model = "unconfigured"
    try:
        version = prompt_version(load_prompt(PROMPT_NAME))
    except AIConfigError:
        version = "unknown"
    return model, version


def active_archetype_catalogue(cur) -> list[dict]:
    cur.execute(
        "SELECT c.id, c.canonical_name, rad.seniority_band, rad.typical_market, rad.notes "
        "FROM jobber.concept c "
        "LEFT JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id "
        "WHERE c.type_code = 'role_archetype' AND c.status = 'active' "
        "ORDER BY c.canonical_name"
    )
    return [
        {
            "id": str(r["id"]),
            "canonical_name": r["canonical_name"],
            "seniority_band": r["seniority_band"],
            "typical_market": r["typical_market"],
            "notes": r["notes"],
        }
        for r in cur.fetchall()
    ]


def _build_input_text(role: dict, requirements: list[dict], catalogue: list[dict]) -> str:
    lines = ["AVAILABLE ARCHETYPES (choose one of these names exactly, or null):"]
    for archetype in catalogue:
        band = f" [{archetype['seniority_band']}]" if archetype["seniority_band"] else ""
        note = f" — {archetype['notes']}" if archetype["notes"] else ""
        lines.append(f"- {archetype['canonical_name']}{band}{note}")

    lines.append("")
    lines.append("ROLE TO CLASSIFY:")
    for label, key in (
        ("Title", "title"), ("Organisation", "organisation"), ("Seniority", "seniority_level"),
        ("Country", "country"), ("Employment type", "employment_type"),
    ):
        if role.get(key):
            lines.append(f"{label}: {role[key]}")

    if requirements:
        names = sorted({r["canonical_name"] for r in requirements})
        lines.append("Reviewed requirements: " + ", ".join(names))

    body = next(
        (role[k] for k in ("description", "summary", "requirements", "responsibilities") if role.get(k)),
        None,
    ) or role.get("source_document_text")
    if body:
        lines.append("")
        lines.append("Role text:")
        lines.append(body[:_MAX_SOURCE_CHARS])
    return "\n".join(lines)


def _load_role(cur, role_instance_id: str) -> dict:
    cur.execute(
        "SELECT ri.id, ri.title, ri.organisation, ri.country, ri.seniority_level, ri.employment_type, "
        "       ri.description, ri.summary, ri.requirements, ri.responsibilities, ri.archetype_concept_id, "
        "       ri.document_id, d.content_text AS source_document_text "
        "FROM jobber.role_instance ri LEFT JOIN jobber.document d ON d.id = ri.document_id "
        "WHERE ri.id = %s",
        (role_instance_id,),
    )
    row = cur.fetchone()
    if not row:
        raise ArchetypeClassificationSubjectError("role_instance not found")
    return dict(row)


def propose_archetype(cur, role_instance_id: str) -> dict:
    """Run the classification task and return a *proposal*. Writes an
    `extraction_run` for provenance and nothing else — in particular, never
    `role_instance.archetype_concept_id`.

    A model-returned name that is not an exact active-catalogue entry is
    reported as `matched: false` with the raw suggestion preserved, so a
    reviewer can see what the model said and why it was not offered as a
    one-click accept. `alternatives` is filtered the same way."""
    role = _load_role(cur, role_instance_id)
    catalogue = active_archetype_catalogue(cur)
    if not catalogue:
        raise NoArchetypeCatalogueError(
            "there are no active role archetypes to choose from — create one on the Economics page first"
        )

    requirements = load_role_requirements(cur, role_instance_id)
    input_text = _build_input_text(role, requirements, catalogue)
    by_name = {a["canonical_name"]: a for a in catalogue}
    started_at = datetime.now(timezone.utc)
    model, pversion = _safe_task_metadata()

    try:
        result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=input_text, output_model=ArchetypeProposal,
        )
    except AITaskError as e:
        run_id = _record_run(
            cur, role_instance_id=role_instance_id, model=model, pversion=pversion, started_at=started_at,
            status="failed", input_chars=len(input_text), output_payload=None,
            error_type=type(e).__name__, error_message=str(e),
        )
        return {
            "status": "failed", "extraction_run_id": run_id, "error": str(e),
            "error_type": type(e).__name__, "proposal": None,
        }

    output = result.output
    suggested = by_name.get((output.archetype_name or "").strip()) if output.archetype_name else None
    alternatives = [
        {"id": by_name[name]["id"], "canonical_name": name}
        for name in (output.alternatives or [])
        if name in by_name
    ][:3]

    proposal = {
        "matched": suggested is not None,
        "archetype_concept_id": suggested["id"] if suggested else None,
        "archetype_name": suggested["canonical_name"] if suggested else None,
        "raw_suggestion": output.archetype_name,
        "confidence": output.confidence,
        "rationale": output.rationale,
        "alternatives": alternatives,
        "catalogue_size": len(catalogue),
        "note": (
            None if suggested is not None
            else "No active archetype matched. Choose one manually, or leave this role unclassified — "
                 "archetypes are never created automatically."
        ),
    }
    run_id = _record_run(
        cur, role_instance_id=role_instance_id, model=result.run.model, pversion=result.run.prompt_version,
        started_at=started_at, status="ok", input_chars=result.run.input_chars,
        output_chars=result.run.output_chars, output_payload={"raw": output.model_dump(), "resolved": proposal},
    )
    return {"status": "ok", "extraction_run_id": run_id, "error": None, "error_type": None, "proposal": proposal}


def _record_run(
    cur, *, role_instance_id: str, model: str, pversion: str, started_at: datetime, status: str,
    input_chars: int, output_chars: int | None = None, output_payload: dict | None = None,
    error_type: str | None = None, error_message: str | None = None,
) -> str:
    cur.execute(
        """
        INSERT INTO jobber.extraction_run
            (task, subject_type, role_instance_id, model, prompt_name, prompt_version,
             started_at, finished_at, status, input_chars, output_chars, output_payload,
             error_type, error_message)
        VALUES (%s, 'role_instance', %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            TASK, role_instance_id, model, PROMPT_NAME, pversion, started_at, status,
            input_chars, output_chars, to_json_param(output_payload), error_type, error_message,
        ),
    )
    return str(cur.fetchone()["id"])


def assign_archetype(cur, role_instance_id: str, archetype_concept_id: str | None) -> dict:
    """The only writer of `role_instance.archetype_concept_id` in this
    module. `None` clears the assignment — leaving a role unclassified is a
    deliberate, recordable choice, not an error state."""
    cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_instance_id,))
    if not cur.fetchone():
        raise ArchetypeClassificationSubjectError("role_instance not found")

    if archetype_concept_id is not None:
        cur.execute(
            "SELECT id, canonical_name FROM jobber.concept "
            "WHERE id = %s AND type_code = 'role_archetype' AND status = 'active'",
            (archetype_concept_id,),
        )
        archetype = cur.fetchone()
        if not archetype:
            raise ArchetypeAssignmentError(
                "archetype_concept_id is not an active role_archetype concept"
            )
    else:
        archetype = None

    cur.execute(
        "UPDATE jobber.role_instance SET archetype_concept_id = %s, updated_at = now() WHERE id = %s",
        (archetype_concept_id, role_instance_id),
    )
    return {
        "role_instance_id": role_instance_id,
        "archetype_concept_id": str(archetype["id"]) if archetype else None,
        "archetype_name": archetype["canonical_name"] if archetype else None,
        "status": "assigned" if archetype else "unclassified",
    }


def role_archetype_summary(cur, role_instance_id: str) -> dict:
    """What Role/Target Detail shows in its Archetype section: the current
    reviewed assignment (or an explicit unclassified state) plus whether a
    catalogue exists to choose from at all."""
    cur.execute(
        "SELECT ri.archetype_concept_id, c.canonical_name, c.status, rad.seniority_band, rad.typical_market "
        "FROM jobber.role_instance ri "
        "LEFT JOIN jobber.concept c ON c.id = ri.archetype_concept_id "
        "LEFT JOIN jobber.role_archetype_detail rad ON rad.concept_id = ri.archetype_concept_id "
        "WHERE ri.id = %s",
        (role_instance_id,),
    )
    row = cur.fetchone()
    if not row:
        raise ArchetypeClassificationSubjectError("role_instance not found")
    cur.execute(
        "SELECT COUNT(*) AS n FROM jobber.concept WHERE type_code = 'role_archetype' AND status = 'active'"
    )
    catalogue_size = cur.fetchone()["n"]
    if not row["archetype_concept_id"]:
        return {
            "assigned": False,
            "archetype_concept_id": None,
            "archetype_name": None,
            "seniority_band": None,
            "typical_market": None,
            "catalogue_size": catalogue_size,
            "state": "unclassified",
        }
    return {
        "assigned": True,
        "archetype_concept_id": str(row["archetype_concept_id"]),
        "archetype_name": row["canonical_name"],
        "archetype_status": row["status"],
        "seniority_band": row["seniority_band"],
        "typical_market": row["typical_market"],
        "catalogue_size": catalogue_size,
        "state": "assigned" if row["status"] == "active" else "assigned_to_deprecated_archetype",
    }
