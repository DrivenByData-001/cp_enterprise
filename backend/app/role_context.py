"""Persisted, on-demand Day-in-the-Life / Role Context enrichment
(next-build brief §6/§7). A role-side market/context enrichment — what doing
this job would actually feel like day to day — stored entirely in `jobber`,
never in `profile360`: this describes the *role*, not fit to the user, and
must never read the user's own profile360 evidence (brief §6.1/§6.7).

Generated on demand only (never for all roles at once, never merely by
viewing a role — brief §6.6): `get_role_context` is a pure read and never
calls the AI provider; `generate_role_context`/its `force=True` regenerate
form are the only things that do. Built on `app.ai.run_json_task`, same
provider-agnostic task layer every other AI feature in this app uses — no
new provider, no new failure-handling pattern.
"""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import build_role_view, db_cursor, to_json_param
from .models import RoleContextGeneration

TASK = "role_context_generate"
PROMPT_NAME = "role_context_enrichment.md"
GENERATOR_VERSION = "1"

_JSONB_SECTIONS = (
    "day_in_life", "typical_week", "team_context", "manager_context",
    "stakeholder_context", "career_progression", "grounding_summary",
)


class RoleContextSubjectError(ValueError):
    """No such role_instance — a 404 at the route layer, same convention as
    extraction.ExtractionSubjectError / document_processing.DocumentNotFoundError."""


class RoleContextGenerationError(RuntimeError):
    """The AI call failed (config/provider/format/schema) — mapped to a
    specific HTTP status at the route layer, same convention as
    import_routes.py's _NATIVE_ERROR_STATUS. The prior active enrichment (if
    any) is always left untouched when this is raised — no write happens
    before this point."""

    def __init__(self, message: str, *, error_type: str):
        super().__init__(message)
        self.error_type = error_type


def _safe_task_metadata() -> tuple[str, str]:
    """Best-effort model/prompt_version for the extraction_run row even when
    run_json_task never got far enough to return one (e.g. no
    OPENAI_API_KEY) — same pattern as document_processing.py/extraction.py's
    own _safe_task_metadata; must never itself raise."""
    try:
        model = ai_model_name()
    except AIConfigError:
        model = "unconfigured"
    try:
        version = prompt_version(load_prompt(PROMPT_NAME))
    except AIConfigError:
        version = "unknown"
    return model, version


def _archetype_context_grounding(cur, role: dict) -> dict | None:
    """Secondary grounding for a role that has a *reviewed* archetype with an
    active context enrichment (build §11).

    Read-only and entirely optional: a role with no archetype, or an
    archetype with no generated context, gets `None` and generation proceeds
    exactly as it did before — this can never turn a working generation into
    a failing one. Only the archetype's own generic characteristics are
    passed through (the grounded/inferred summaries and its caveats), never
    another posting's specifics."""
    archetype_id = role.get("archetype_concept_id")
    if not archetype_id:
        return None
    cur.execute(
        "SELECT ace.grounding_summary, ace.caveats, ace.day_in_life, ace.typical_week, "
        "       ace.team_context, ace.manager_context, ace.stakeholder_context, ace.career_progression, "
        "       c.canonical_name AS archetype_name "
        "FROM jobber.archetype_context_enrichment ace "
        "JOIN jobber.concept c ON c.id = ace.archetype_concept_id "
        "WHERE ace.archetype_concept_id = %s AND ace.status = 'active'",
        (str(archetype_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _render_archetype_grounding(grounding: dict) -> str:
    """The archetype block appended to the prompt, explicitly framed as
    *generic* and explicitly subordinate to the role's own detail (build
    §11: "explicit target details override generic archetype context")."""
    lines = [
        "",
        "---",
        "",
        f"GENERIC ARCHETYPE CONTEXT — SECONDARY GROUNDING ONLY ({grounding['archetype_name']}).",
        "",
        "This describes what this KIND of role is typically like across several postings. It is background,",
        "not evidence about the specific role above. Where the role's own material says something different,",
        "the role's own material wins — always. Never restate an archetype characteristic as if this specific",
        "role stated it: anything you take from this block is `inferred`, never `advert_grounded`. Use it to",
        "fill gaps the role's own material leaves open, and say nothing from it that the role contradicts.",
    ]
    summary = grounding.get("grounding_summary") or {}
    for label, key in (("Typical, evidence-grounded across the archetype", "advert_grounded_points"),
                       ("Typical, inferred across the archetype", "inferred_points")):
        points = summary.get(key) or []
        if points:
            lines.append("")
            lines.append(f"{label}:")
            lines.extend(f"- {p}" for p in points[:10])

    for label, items, text_key in (
        ("Typical day shape", grounding.get("day_in_life") or [], "activity"),
        ("Typical week shape", grounding.get("typical_week") or [], "activity"),
        ("Typical career progression", grounding.get("career_progression") or [], "step"),
    ):
        if items:
            lines.append("")
            lines.append(f"{label} for this archetype:")
            lines.extend(f"- {i.get(text_key)}" for i in items[:8] if i.get(text_key))

    stakeholders = (grounding.get("stakeholder_context") or {}).get("stakeholders") or []
    if stakeholders:
        lines.append("")
        lines.append("Typical stakeholders for this archetype:")
        lines.extend(f"- {s.get('text')}" for s in stakeholders[:8] if s.get("text"))

    if grounding.get("caveats"):
        lines.append("")
        lines.append(f"Archetype context caveats: {grounding['caveats']}")
    return "\n".join(lines)


def _build_input_text(role: dict, archetype_grounding: dict | None = None) -> str:
    """Role evidence only (brief §6.7) — title, organisation, location,
    seniority, description/requirements/responsibilities or the captured
    source text fallback, known skills, and (for a target) existing
    typical_tasks. Never touches profile360.

    Since build §11, a role with a reviewed archetype may additionally carry
    that archetype's context as clearly-labelled *secondary* grounding,
    appended after the role's own material by
    `_render_archetype_grounding` — still never profile360, and still never
    allowed to outrank what the role itself says."""
    lines = [f"Title: {role.get('title') or 'Unknown'}"]
    for label, key in (
        ("Organisation", "organisation"), ("Location", "location"), ("Country", "country"),
        ("Seniority", "seniority_level"), ("Career track", "career_track"), ("Employment type", "employment_type"),
    ):
        if role.get(key):
            lines.append(f"{label}: {role[key]}")

    body: list[str] = []
    has_flat_text = any(role.get(k) for k in ("description", "requirements", "responsibilities"))
    if role.get("description"):
        body.append(f"Description:\n{role['description']}")
    if role.get("requirements"):
        body.append(f"Requirements:\n{role['requirements']}")
    if role.get("responsibilities"):
        body.append(f"Responsibilities:\n{role['responsibilities']}")
    if role.get("summary"):
        body.append(f"Summary:\n{role['summary']}")
    if not has_flat_text and role.get("source_document_text"):
        body.append(f"Captured source text (verbatim):\n{role['source_document_text']}")

    # Both reviewed requirements and legacy extracted skills are real
    # evidence about the role and belong in this prompt's context — the
    # reviewed/legacy split (db.role_skills_display) exists to keep Role
    # Detail's *display* honest about what a human has actually reviewed,
    # not to narrow what this generation step is allowed to read.
    skills = (role.get("skills") or []) + (role.get("legacy_skills") or [])
    if skills:
        body.append("Known skills/requirements: " + ", ".join(s["name"] for s in skills if s.get("name")))

    if role.get("node_type") != "posting" and role.get("typical_tasks"):
        body.append("Typical tasks (existing target data):\n" + "\n".join(f"- {t}" for t in role["typical_tasks"]))

    text = "\n".join(lines) + "\n\n" + "\n\n".join(body)
    if archetype_grounding is not None:
        text += "\n" + _render_archetype_grounding(archetype_grounding)
    return text


def _active_row(cur, role_instance_id: str) -> dict | None:
    cur.execute(
        "SELECT * FROM jobber.role_context_enrichment WHERE role_instance_id = %s AND status = 'active'",
        (role_instance_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _serialize(row: dict) -> dict:
    """Wire shape: every persisted field except `raw_output` (audit/debug
    only — same posture as extraction_run.output_payload, never returned by
    GET /api/roles/{id} either)."""
    return {
        "id": str(row["id"]),
        "role_instance_id": str(row["role_instance_id"]),
        "status": row["status"],
        "generated_at": row["generated_at"],
        "generator_version": row["generator_version"],
        "model": row["model"],
        "source_document_id": str(row["source_document_id"]) if row["source_document_id"] else None,
        "source_content_sha256": row["source_content_sha256"],
        "day_in_life": row["day_in_life"],
        "typical_week": row["typical_week"],
        "team_context": row["team_context"],
        "manager_context": row["manager_context"],
        "stakeholder_context": row["stakeholder_context"],
        "career_progression": row["career_progression"],
        "grounding_summary": row["grounding_summary"],
        "caveats": row["caveats"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_role_context(cur, role_instance_id: str) -> dict:
    """Read-only (brief §6.6/§7: "The GET endpoint must not trigger
    generation") — never calls the AI provider, never writes anything."""
    cur.execute(
        "SELECT ri.id, ri.archetype_concept_id, c.canonical_name AS archetype_name "
        "FROM jobber.role_instance ri LEFT JOIN jobber.concept c ON c.id = ri.archetype_concept_id "
        "WHERE ri.id = %s",
        (role_instance_id,),
    )
    role = cur.fetchone()
    if not role:
        raise RoleContextSubjectError(f"role_instance {role_instance_id!r} not found")
    active = _active_row(cur, role_instance_id)
    grounding = _archetype_context_grounding(cur, dict(role))
    return {
        "role_instance_id": role_instance_id,
        "enrichment": _serialize(active) if active else None,
        # Build §11: lets the UI say "this will also draw on the <X>
        # archetype's context" before the user clicks Generate, and explain
        # afterwards what the generation was allowed to see.
        "archetype_grounding": (
            {
                "archetype_concept_id": str(role["archetype_concept_id"]),
                "archetype_name": role["archetype_name"],
                "available": grounding is not None,
            }
            if role["archetype_concept_id"] else None
        ),
    }


def _persist(
    *, role_instance_id: str, run_id: str, model: str, fingerprint: str,
    source_document_id: str | None, payload: RoleContextGeneration, now: datetime | None = None,
) -> dict:
    """Atomic supersede-then-insert (brief §7 point 5), in the caller's own
    short transaction (no AI call happens inside it). Unconditionally
    supersedes any existing active row before inserting the new one, in the
    same transaction — this is what keeps the partial unique index
    (idx_role_context_enrichment_one_active) from ever being violated by
    this code path, with no separate locking needed: there is never a moment
    where two 'active' rows for the same role could both commit, since the
    supersede and the insert are one statement apart in one transaction."""
    now = now or datetime.now(timezone.utc)
    stakeholder_context = {"stakeholders": [s.model_dump() for s in payload.stakeholders]}
    sections = {
        "day_in_life": [i.model_dump() for i in payload.day_in_life],
        "typical_week": [i.model_dump() for i in payload.typical_week],
        "team_context": payload.team_context.model_dump(),
        "manager_context": payload.manager_context.model_dump(),
        "stakeholder_context": stakeholder_context,
        "career_progression": [i.model_dump() for i in payload.career_progression],
        "grounding_summary": payload.grounding_summary.model_dump(),
    }

    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.role_context_enrichment SET status = 'superseded', updated_at = %s "
            "WHERE role_instance_id = %s AND status = 'active'",
            (now, role_instance_id),
        )
        cur.execute(
            """
            INSERT INTO jobber.role_context_enrichment
                (role_instance_id, status, generated_at, generator_version, model, source_document_id,
                 source_content_sha256, day_in_life, typical_week, team_context, manager_context,
                 stakeholder_context, career_progression, grounding_summary, caveats, raw_output,
                 extraction_run_id, created_at, updated_at)
            VALUES (%s, 'active', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                role_instance_id, now, GENERATOR_VERSION, model, source_document_id, fingerprint,
                to_json_param(sections["day_in_life"]), to_json_param(sections["typical_week"]),
                to_json_param(sections["team_context"]), to_json_param(sections["manager_context"]),
                to_json_param(sections["stakeholder_context"]), to_json_param(sections["career_progression"]),
                to_json_param(sections["grounding_summary"]), payload.caveats,
                to_json_param(json.loads(payload.model_dump_json())), run_id, now, now,
            ),
        )
        row = cur.fetchone()
        cur.execute(
            "UPDATE jobber.extraction_run SET status = 'ok', finished_at = now() WHERE id = %s",
            (run_id,),
        )
    return _serialize(row)


def _mark_run_failed(run_id: str, *, error_type: str, error_message: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.extraction_run SET status = 'failed', finished_at = now(), "
            "error_type = %s, error_message = %s WHERE id = %s",
            (error_type, error_message, run_id),
        )


def generate_role_context(role_instance_id: str, *, force: bool = False) -> dict:
    """Generate (or, with force=True, regenerate) the Day-in-the-Life
    enrichment for one role. `force=False` (the plain "Generate" action,
    brief §6.6): a no-op returning the existing enrichment unchanged — and no
    AI call — if one is already active, so a repeated/accidental call never
    wastes a model call or creates a spurious new version. `force=True` (the
    deliberate "Regenerate" action) always calls the model and creates a new
    active version, superseding the prior one.

    The model is always called outside any open DB transaction (brief §7
    point 3): evidence is read and the 'running' extraction_run row is
    committed *before* the network call; persistence happens in a fresh,
    separate transaction afterward. A provider/validation failure marks that
    run 'failed' and raises RoleContextGenerationError — the active
    enrichment (if any) is never touched in that path, since _persist is
    never reached."""
    with db_cursor() as cur:
        role = build_role_view(cur, role_instance_id)
        if role is None:
            raise RoleContextSubjectError(f"role_instance {role_instance_id!r} not found")
        existing = _active_row(cur, role_instance_id)
        # Build §11: a reviewed archetype's context becomes secondary
        # grounding when one exists. Read here, alongside the role's own
        # evidence and before any AI call, so generation stays a single
        # read-then-call-then-write pass.
        archetype_grounding = _archetype_context_grounding(cur, role)

    if existing is not None and not force:
        return {"created": False, "enrichment": _serialize(existing)}

    source_document_id = role.pop("_source_document_id", None)
    input_text = _build_input_text(role, archetype_grounding)
    fingerprint = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    model, pversion = _safe_task_metadata()
    started_at = datetime.now(timezone.utc)

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.extraction_run "
            "(task, subject_type, role_instance_id, model, prompt_name, prompt_version, "
            " vocabulary_version_id, started_at, status) "
            "VALUES (%s, 'role_instance', %s, %s, %s, %s, NULL, %s, 'running') RETURNING id",
            (TASK, role_instance_id, model, PROMPT_NAME, pversion, started_at),
        )
        run_id = str(cur.fetchone()["id"])
    # committed here — visible before the network call, same lifecycle
    # discipline as document_processing.process_job_posting_document.

    try:
        ai_result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=input_text, output_model=RoleContextGeneration,
        )
    except AITaskError as e:
        _mark_run_failed(run_id, error_type=type(e).__name__, error_message=str(e))
        raise RoleContextGenerationError(str(e), error_type=type(e).__name__) from e

    enrichment = _persist(
        role_instance_id=role_instance_id, run_id=run_id, model=ai_result.run.model,
        fingerprint=fingerprint, source_document_id=source_document_id, payload=ai_result.output,
    )
    return {"created": True, "enrichment": enrichment}


# HTTP status for each AITaskError subclass, same mapping import_routes.py
# already uses for the equivalent native-import failure modes (brief §8:
# "context generation should return a clear operational error").
GENERATION_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


def raise_for_generation_error(e: RoleContextGenerationError) -> None:
    raise HTTPException(status_code=GENERATION_ERROR_STATUS.get(e.error_type, 502), detail=str(e)) from e
