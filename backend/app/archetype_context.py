"""Persisted, on-demand Day-in-the-Life / context enrichment for a role
**archetype** (build §10).

Deliberately the same module shape, lifecycle and invariants as
`role_context.py` — this is the archetype-level sibling of that feature, not
a new pattern:

- `get_archetype_context` is a pure read. It never calls the AI provider and
  never writes.
- `generate_archetype_context` is explicit and on-demand. `force=False` is a
  no-op (and makes no model call) when an active enrichment already exists;
  `force=True` is the deliberate Regenerate.
- The model is always called **outside** any open transaction: evidence is
  read and the `running` extraction_run row is committed first, then the
  network call, then persistence in a fresh transaction.
- A failed generation leaves the prior active enrichment completely
  untouched — `_persist` is only reachable after a validated response.
- Exactly one active row per archetype, enforced by migration 0023's partial
  unique index, with supersede-then-insert one statement apart in a single
  transaction.
- Nothing is ever bulk-generated. There is no "generate for all archetypes"
  entry point anywhere in this module or its routes.

## Grounding (build §10)

Only archetype-level, market-side evidence: the archetype's own metadata,
the postings a curator has assigned to it, the accepted/current requirements
on those postings (via the canonical `role_requirements` loader), the derived
`d_archetype_demand` rates, and — where they exist — the assigned postings'
own Role Context enrichments.

`profile360` is never read here, directly or indirectly. This describes what
the archetype is like to do; it is not, and must never become, a statement
about the user's fit for it. Compensation is also deliberately withheld from
the prompt: pay is resolved elsewhere from evidence with its own basis, and
narrative text is the wrong place for a number that would arrive with no
basis attached.
"""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import db_cursor, to_json_param
from .models import RoleContextGeneration
from .role_requirements import load_role_requirements_bulk

TASK = "archetype_context_generate"
PROMPT_NAME = "archetype_context_enrichment.md"
GENERATOR_VERSION = "1"

_MAX_POSTINGS_IN_PROMPT = 12
_MAX_POSTING_CHARS = 2500


class ArchetypeContextSubjectError(ValueError):
    """No such active role_archetype concept — a 404 at the route layer."""


class ArchetypeContextGroundingError(ValueError):
    """The archetype has no assigned postings, so there is no evidence to
    synthesise from. Generating anyway would produce a description grounded
    in nothing but the archetype's name — exactly the kind of confident
    fabrication this build refuses. A 4xx, not a failed AI run."""


class ArchetypeContextGenerationError(RuntimeError):
    """The AI call failed (config/provider/format/schema). The prior active
    enrichment, if any, is untouched — no write happens before this point.
    Mirrors role_context.RoleContextGenerationError exactly."""

    def __init__(self, message: str, *, error_type: str):
        super().__init__(message)
        self.error_type = error_type


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


def _load_archetype(cur, archetype_concept_id: str) -> dict:
    cur.execute(
        "SELECT c.id, c.canonical_name, c.status, rad.seniority_band, rad.typical_market, rad.notes, "
        "       f.canonical_name AS primary_function "
        "FROM jobber.concept c "
        "LEFT JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id "
        "LEFT JOIN jobber.concept f ON f.id = rad.primary_function_concept_id "
        "WHERE c.id = %s AND c.type_code = 'role_archetype'",
        (archetype_concept_id,),
    )
    row = cur.fetchone()
    if not row:
        raise ArchetypeContextSubjectError(f"role_archetype {archetype_concept_id!r} not found")
    return dict(row)


def collect_grounding(cur, archetype_concept_id: str) -> dict:
    """Every piece of evidence this generation is allowed to use, gathered in
    a bounded number of queries and returned as data — so the same structure
    can be stored as the enrichment's provenance record and asserted against
    in tests, rather than only existing inside a prompt string."""
    archetype = _load_archetype(cur, archetype_concept_id)

    cur.execute(
        "SELECT ri.id, ri.title, ri.organisation, ri.country, ri.seniority_level, ri.employment_type, "
        "       ri.posting_date, ri.description, ri.summary, ri.responsibilities, ri.requirements, "
        "       d.content_text AS source_document_text "
        "FROM jobber.role_instance ri LEFT JOIN jobber.document d ON d.id = ri.document_id "
        "WHERE ri.archetype_concept_id = %s "
        "ORDER BY ri.posting_date DESC NULLS LAST, ri.id",
        (archetype_concept_id,),
    )
    postings = [dict(r) for r in cur.fetchall()]
    posting_ids = [str(p["id"]) for p in postings]

    requirements = load_role_requirements_bulk(cur, posting_ids) if posting_ids else {}

    cur.execute(
        "SELECT d.capability_concept_id, c.canonical_name, d.roles_in_archetype, d.roles_demanding_capability, "
        "       d.demand_rate, d.required_count, d.preferred_count "
        "FROM jobber.d_archetype_demand d JOIN jobber.concept c ON c.id = d.capability_concept_id "
        "WHERE d.archetype_concept_id = %s "
        "ORDER BY d.demand_rate DESC NULLS LAST, c.canonical_name",
        (archetype_concept_id,),
    )
    demand = [
        {
            "capability_concept_id": str(r["capability_concept_id"]),
            "canonical_name": r["canonical_name"],
            "roles_in_archetype": r["roles_in_archetype"],
            "roles_demanding_capability": r["roles_demanding_capability"],
            "demand_rate": float(r["demand_rate"]) if r["demand_rate"] is not None else None,
            "required_count": r["required_count"],
            "preferred_count": r["preferred_count"],
        }
        for r in cur.fetchall()
    ]

    role_contexts = []
    if posting_ids:
        cur.execute(
            "SELECT role_instance_id, grounding_summary, caveats FROM jobber.role_context_enrichment "
            "WHERE status = 'active' AND role_instance_id = ANY(%s::uuid[])",
            (posting_ids,),
        )
        role_contexts = [
            {
                "role_instance_id": str(r["role_instance_id"]),
                "grounding_summary": r["grounding_summary"],
                "caveats": r["caveats"],
            }
            for r in cur.fetchall()
        ]

    return {
        "archetype": archetype,
        "postings": postings,
        "posting_ids": posting_ids,
        "requirements": requirements,
        "demand": demand,
        "role_contexts": role_contexts,
    }


def _build_input_text(grounding: dict) -> str:
    archetype = grounding["archetype"]
    lines = [f"ARCHETYPE: {archetype['canonical_name']}"]
    for label, key in (
        ("Seniority band", "seniority_band"), ("Primary function", "primary_function"),
        ("Typical market", "typical_market"), ("Curator notes", "notes"),
    ):
        if archetype.get(key):
            lines.append(f"{label}: {archetype[key]}")

    postings = grounding["postings"]
    lines.append("")
    lines.append(f"ASSIGNED POSTINGS: {len(postings)} in total.")
    if len(postings) > _MAX_POSTINGS_IN_PROMPT:
        lines.append(
            f"(The {_MAX_POSTINGS_IN_PROMPT} most recent are reproduced below; the counts above cover all of them.)"
        )

    for posting in postings[:_MAX_POSTINGS_IN_PROMPT]:
        posting_id = str(posting["id"])
        lines.append("")
        lines.append(f"--- Posting: {posting['title'] or 'Untitled'}")
        for label, key in (
            ("Seniority", "seniority_level"), ("Country", "country"),
            ("Employment type", "employment_type"), ("Posted", "posting_date"),
        ):
            if posting.get(key):
                lines.append(f"{label}: {posting[key]}")
        items = grounding["requirements"].get(posting_id, [])
        if items:
            required = sorted({i["canonical_name"] for i in items if i["requirement_type"] == "required"})
            other = sorted({i["canonical_name"] for i in items if i["requirement_type"] != "required"})
            if required:
                lines.append("Required: " + ", ".join(required))
            if other:
                lines.append("Other requirements: " + ", ".join(other))
        body = next(
            (posting[k] for k in ("description", "responsibilities", "requirements", "summary") if posting.get(k)),
            None,
        ) or posting.get("source_document_text")
        if body:
            lines.append("Text: " + body[:_MAX_POSTING_CHARS])

    if grounding["demand"]:
        lines.append("")
        lines.append("DERIVED CAPABILITY DEMAND ACROSS THIS ARCHETYPE'S POSTINGS:")
        for item in grounding["demand"][:25]:
            rate = f"{item['demand_rate']:.0%}" if item["demand_rate"] is not None else "unknown"
            lines.append(
                f"- {item['canonical_name']}: demanded by {item['roles_demanding_capability']}"
                f"/{item['roles_in_archetype']} postings ({rate}); required on {item['required_count']}"
            )

    if grounding["role_contexts"]:
        lines.append("")
        lines.append("EXISTING PER-POSTING CONTEXT NOTES (grounded points only, for corroboration):")
        for context in grounding["role_contexts"][:_MAX_POSTINGS_IN_PROMPT]:
            summary = context["grounding_summary"] or {}
            for point in (summary.get("advert_grounded_points") or [])[:6]:
                lines.append(f"- {point}")

    return "\n".join(lines)


def _active_row(cur, archetype_concept_id: str) -> dict | None:
    cur.execute(
        "SELECT * FROM jobber.archetype_context_enrichment "
        "WHERE archetype_concept_id = %s AND status = 'active'",
        (archetype_concept_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _serialize(row: dict) -> dict:
    """Every persisted field except `raw_output` (audit/debug only, same
    posture as role_context_enrichment)."""
    return {
        "id": str(row["id"]),
        "archetype_concept_id": str(row["archetype_concept_id"]),
        "status": row["status"],
        "generated_at": row["generated_at"],
        "generator_version": row["generator_version"],
        "model": row["model"],
        "source_fingerprint": row["source_fingerprint"],
        "grounding_provenance": row["grounding_provenance"],
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


def _evidence_caveats(grounding: dict) -> list[str]:
    """Deterministic, evidence-derived caveats computed by this module, not
    asked of the model — thin evidence must produce a visible warning even
    if the model's own `caveats` field comes back null."""
    caveats = []
    n = len(grounding["postings"])
    if n == 0:
        caveats.append("No postings are assigned to this archetype.")
    elif n == 1:
        caveats.append(
            "Only one posting is assigned to this archetype, so this is a description of a single advert's "
            "pattern, not a synthesis across the market."
        )
    elif n < 4:
        caveats.append(
            f"Only {n} postings are assigned to this archetype; the pattern below is thin evidence."
        )
    roles_with_requirements = sum(1 for items in grounding["requirements"].values() if items)
    if n and roles_with_requirements == 0:
        caveats.append(
            "None of the assigned postings has reviewed requirement evidence yet, so this synthesis rests on "
            "posting text alone."
        )
    elif n and roles_with_requirements < n:
        caveats.append(
            f"{roles_with_requirements} of {n} assigned postings have reviewed requirement evidence."
        )
    if not grounding["demand"]:
        caveats.append(
            "No derived capability demand exists for this archetype yet — rebuild economics to populate it."
        )
    return caveats


def get_archetype_context(cur, archetype_concept_id: str) -> dict:
    """Pure read: never generates, never calls the model, never writes.
    404 only if the archetype itself does not exist; an archetype with no
    enrichment returns 200 with `enrichment: null` so the UI can offer a
    Generate affordance instead of treating absence as an error."""
    archetype = _load_archetype(cur, archetype_concept_id)
    active = _active_row(cur, archetype_concept_id)
    return {
        "archetype_concept_id": archetype_concept_id,
        "archetype_name": archetype["canonical_name"],
        "enrichment": _serialize(active) if active else None,
        "synthesis_note": (
            "Archetype context is a synthesis across the evidence available for this archetype. "
            "It is not a statement of fact about every job that carries this title."
        ),
    }


def _persist(
    *, archetype_concept_id: str, run_id: str, model: str, fingerprint: str,
    provenance: dict, payload: RoleContextGeneration, extra_caveats: list[str],
    now: datetime | None = None,
) -> dict:
    """Atomic supersede-then-insert in one transaction, with no AI call
    inside it — the same mechanism (and the same reason) as
    role_context.py's `_persist`: there is never a moment where two 'active'
    rows for one archetype could both commit."""
    now = now or datetime.now(timezone.utc)
    stakeholder_context = {"stakeholders": [s.model_dump() for s in payload.stakeholders]}
    caveats = " ".join([*extra_caveats, payload.caveats or ""]).strip() or None

    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.archetype_context_enrichment SET status = 'superseded', updated_at = %s "
            "WHERE archetype_concept_id = %s AND status = 'active'",
            (now, archetype_concept_id),
        )
        cur.execute(
            """
            INSERT INTO jobber.archetype_context_enrichment
                (archetype_concept_id, status, generated_at, generator_version, model, source_fingerprint,
                 grounding_provenance, day_in_life, typical_week, team_context, manager_context,
                 stakeholder_context, career_progression, grounding_summary, caveats, raw_output,
                 extraction_run_id, created_at, updated_at)
            VALUES (%s, 'active', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                archetype_concept_id, now, GENERATOR_VERSION, model, fingerprint, to_json_param(provenance),
                to_json_param([i.model_dump() for i in payload.day_in_life]),
                to_json_param([i.model_dump() for i in payload.typical_week]),
                to_json_param(payload.team_context.model_dump()),
                to_json_param(payload.manager_context.model_dump()),
                to_json_param(stakeholder_context),
                to_json_param([i.model_dump() for i in payload.career_progression]),
                to_json_param(payload.grounding_summary.model_dump()),
                caveats, to_json_param(json.loads(payload.model_dump_json())), run_id, now, now,
            ),
        )
        row = cur.fetchone()
        cur.execute(
            "UPDATE jobber.extraction_run SET status = 'ok', finished_at = now() WHERE id = %s", (run_id,)
        )
    return _serialize(row)


def _mark_run_failed(run_id: str, *, error_type: str, error_message: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.extraction_run SET status = 'failed', finished_at = now(), "
            "error_type = %s, error_message = %s WHERE id = %s",
            (error_type, error_message, run_id),
        )


def generate_archetype_context(archetype_concept_id: str, *, force: bool = False) -> dict:
    """Generate (or, with force=True, regenerate) one archetype's context.

    The `extraction_run` row records `subject_type='role_instance'` against
    the archetype's most-recent assigned posting. That is a provenance
    compromise, made deliberately and documented in migration 0023:
    `extraction_run`'s subject CHECK constraints (0003) are unnamed and cover
    no 'role_archetype' subject kind, and migration 0012 already established
    that widening them is riskier than a single feature warrants. The
    authoritative record of *what this generation was actually grounded in*
    therefore lives on the enrichment row's own `grounding_provenance`, which
    names every posting id, requirement count and demand row used."""
    with db_cursor() as cur:
        grounding = collect_grounding(cur, archetype_concept_id)
        existing = _active_row(cur, archetype_concept_id)

    if existing is not None and not force:
        return {"created": False, "enrichment": _serialize(existing)}

    if not grounding["posting_ids"]:
        raise ArchetypeContextGroundingError(
            "this archetype has no assigned postings, so there is no evidence to describe it from — "
            "assign roles to it first"
        )

    input_text = _build_input_text(grounding)
    fingerprint = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    provenance = {
        "posting_ids": grounding["posting_ids"],
        "postings_used_in_prompt": min(len(grounding["postings"]), _MAX_POSTINGS_IN_PROMPT),
        "postings_total": len(grounding["postings"]),
        "requirement_counts": {k: len(v) for k, v in grounding["requirements"].items()},
        "demand_capability_ids": [d["capability_concept_id"] for d in grounding["demand"]],
        "role_context_ids": [c["role_instance_id"] for c in grounding["role_contexts"]],
        "reads_profile360": False,
    }
    model, pversion = _safe_task_metadata()
    started_at = datetime.now(timezone.utc)
    subject_role_id = grounding["posting_ids"][0]

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.extraction_run "
            "(task, subject_type, role_instance_id, model, prompt_name, prompt_version, "
            " vocabulary_version_id, started_at, status) "
            "VALUES (%s, 'role_instance', %s, %s, %s, %s, NULL, %s, 'running') RETURNING id",
            (TASK, subject_role_id, model, PROMPT_NAME, pversion, started_at),
        )
        run_id = str(cur.fetchone()["id"])
    # committed here — visible before the network call.

    try:
        ai_result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=input_text, output_model=RoleContextGeneration,
        )
    except AITaskError as e:
        _mark_run_failed(run_id, error_type=type(e).__name__, error_message=str(e))
        raise ArchetypeContextGenerationError(str(e), error_type=type(e).__name__) from e

    enrichment = _persist(
        archetype_concept_id=archetype_concept_id, run_id=run_id, model=ai_result.run.model,
        fingerprint=fingerprint, provenance=provenance, payload=ai_result.output,
        extra_caveats=_evidence_caveats(grounding),
    )
    return {"created": True, "enrichment": enrichment}


GENERATION_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


def raise_for_generation_error(e: ArchetypeContextGenerationError) -> None:
    raise HTTPException(status_code=GENERATION_ERROR_STATUS.get(e.error_type, 502), detail=str(e)) from e
