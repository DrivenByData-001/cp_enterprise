"""Persisted, on-demand Concept Dossier generation (cp_round_of_changes.md
§C/§D/§E/§F). Turns a terse accepted canonical concept (e.g. "ORSA",
"Capital Management", "Prophet") into a curator-maintained explanatory
dossier — grounded in the concept's own metadata, its aliases, representative
role evidence, and neighbouring canonical concepts. Never reads profile360
(§E: "Do NOT read profile360" — this describes the shared vocabulary, not
the user's own evidence).

Generated on demand only, same discipline as app/role_context.py:
`get_concept_dossier` is a pure read and never calls the AI provider;
`generate_concept_dossier`/`regenerate_concept_dossier` are the only things
that do, and the model is always called outside any open DB transaction —
evidence is read and closed first, the network call happens with no
transaction open, and persistence happens in a fresh transaction afterward.
A provider/validation failure raises ConceptDossierGenerationError and never
touches the current active dossier (§D: "AI/provider failures must leave
the current active dossier untouched").

Lifecycle differs from role_context.py's on purpose (§C/§D, not a role_context
copy-paste):

- No dossier yet -> `generate_concept_dossier` creates the first ACTIVE
  version.
- An active dossier exists -> `generate_concept_dossier` is a no-op (returns
  the existing active dossier, no AI call — same "repeated call is free"
  guarantee role_context's plain generate has). `regenerate_concept_dossier`
  is the only thing that calls the model again, and it creates a DRAFT,
  never touching/superseding the active row automatically.
- `adopt_draft` supersedes the active row and promotes the draft to active,
  atomically, in one transaction.
- `discard_draft` marks the draft superseded without touching active.
- `save_manual_edit` supersedes the active row and inserts a new
  curator-authored active version — version history is retained exactly
  like an AI regeneration, just with `origin='curator'` and no model/
  guidance/raw_output.

Audit metadata (model, prompt_version, guidance, raw AI output) lives
directly on `jobber.concept_dossier` rather than `jobber.extraction_run` —
see migrations/0012_concept_dossier.sql's header for why."""

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import db_cursor, to_json_param
from .models import ConceptDossierGeneration

TASK = "concept_dossier_generate"
PROMPT_NAME = "concept_dossier_generate.md"
GENERATOR_VERSION = "1"

_CONTENT_COLUMNS = (
    "plain_definition", "classification_rationale", "practical_meaning", "underlying_elements",
    "stronger_expressions", "weaker_expressions", "boundaries_and_overlaps", "related_concepts", "caveats",
)

# How many representative role-evidence examples / candidate related
# concepts to ground generation in (§E). Small, fixed caps — this is a
# curator-facing explanation, not an exhaustive corpus dump.
ROLE_EVIDENCE_LIMIT = 8
CANDIDATE_CONCEPT_LIMIT = 12


class ConceptDossierSubjectError(ValueError):
    """No such concept — a 404 at the route layer, same convention as
    role_context.RoleContextSubjectError."""


class ConceptDossierStateError(RuntimeError):
    """The requested action doesn't apply to the dossier's current state
    (regenerate/adopt/discard with no active or no draft) — mapped to 409 at
    the route layer."""


class ConceptDossierGenerationError(RuntimeError):
    """The AI call failed (config/provider/format/schema) — mapped to a
    specific HTTP status at the route layer, same convention as
    role_context.RoleContextGenerationError. The active dossier (if any) is
    always left untouched when this is raised — no write happens before
    this point."""

    def __init__(self, message: str, *, error_type: str):
        super().__init__(message)
        self.error_type = error_type


def _safe_task_metadata() -> tuple[str, str]:
    """Best-effort model/prompt_version even when run_json_task never got
    far enough to return one (e.g. no OPENAI_API_KEY) — same pattern as
    role_context._safe_task_metadata; must never itself raise."""
    try:
        model = ai_model_name()
    except AIConfigError:
        model = "unconfigured"
    try:
        version = prompt_version(load_prompt(PROMPT_NAME))
    except AIConfigError:
        version = "unknown"
    return model, version


def _concept_or_404(cur, concept_id: str) -> dict:
    cur.execute("SELECT * FROM jobber.concept WHERE id = %s", (concept_id,))
    row = cur.fetchone()
    if not row:
        raise ConceptDossierSubjectError(f"concept {concept_id!r} not found")
    concept = dict(row)
    concept["id"] = str(concept["id"])
    return concept


def _active_row(cur, concept_id: str) -> dict | None:
    cur.execute("SELECT * FROM jobber.concept_dossier WHERE concept_id = %s AND status = 'active'", (concept_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _draft_row(cur, concept_id: str) -> dict | None:
    cur.execute("SELECT * FROM jobber.concept_dossier WHERE concept_id = %s AND status = 'draft'", (concept_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _serialize(row: dict) -> dict:
    """Wire shape: every persisted field except `raw_output` (audit/debug
    only, same posture as role_context_enrichment.raw_output — never
    returned by the normal read API)."""
    return {
        "id": str(row["id"]),
        "concept_id": str(row["concept_id"]),
        "status": row["status"],
        "origin": row["origin"],
        "generated_at": row["generated_at"],
        "generator_version": row["generator_version"],
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "guidance": row["guidance"],
        "plain_definition": row["plain_definition"],
        "classification_rationale": row["classification_rationale"],
        "practical_meaning": row["practical_meaning"],
        "underlying_elements": row["underlying_elements"],
        "stronger_expressions": row["stronger_expressions"],
        "weaker_expressions": row["weaker_expressions"],
        "boundaries_and_overlaps": row["boundaries_and_overlaps"],
        "related_concepts": row["related_concepts"],
        "caveats": row["caveats"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_concept_dossier(cur, concept_id: str) -> dict:
    """Read-only (§D: "Viewing a concept must NEVER trigger an AI call") —
    never calls the AI provider, never writes anything."""
    _concept_or_404(cur, concept_id)
    active = _active_row(cur, concept_id)
    draft = _draft_row(cur, concept_id)
    return {
        "concept_id": concept_id,
        "active": _serialize(active) if active else None,
        "draft": _serialize(draft) if draft else None,
    }


def list_concept_dossier_history(cur, concept_id: str) -> list[dict]:
    """Every version ever recorded for this concept (active, the current
    draft if any, and every superseded row) — proves §D's "manual save...
    retains version history" is actually retrievable, not just retained
    silently in the database."""
    _concept_or_404(cur, concept_id)
    cur.execute(
        "SELECT * FROM jobber.concept_dossier WHERE concept_id = %s ORDER BY generated_at DESC, created_at DESC",
        (concept_id,),
    )
    return [_serialize(dict(row)) for row in cur.fetchall()]


# --- grounding input (§E) ----------------------------------------------------

def _representative_role_evidence(cur, concept_id: str, limit: int = ROLE_EVIDENCE_LIMIT) -> list[dict]:
    """Up to `limit` distinct roles observed using this concept, preferring a
    verbatim requirement_claim evidence_span when one exists for that same
    role+concept, falling back to the plain surface_form otherwise. Drawn
    from role_skill_observation (the corpus-wide evidence source) rather
    than requirement_claim alone, since the latter only covers roles
    captured through the newer source-aware pipeline."""
    cur.execute(
        """
        SELECT DISTINCT ON (ri.id) ri.id AS role_id, ri.title, ri.organisation, ri.posting_date,
               rc.evidence_span, rso.surface_form
        FROM jobber.role_skill_observation rso
        JOIN jobber.role_instance ri ON ri.id = rso.role_instance_id
        LEFT JOIN jobber.requirement_claim rc
            ON rc.role_instance_id = ri.id AND rc.concept_id = rso.canonical_concept_id
        WHERE rso.canonical_concept_id = %s
        ORDER BY ri.id, ri.posting_date DESC NULLS LAST
        LIMIT %s
        """,
        (concept_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def _candidate_related_concepts(cur, concept_id: str, limit: int = CANDIDATE_CONCEPT_LIMIT) -> list[dict]:
    """Grounded candidate pool for §F's related-concepts suggestion,
    ranked/limited *before* the AI ever sees them: concepts co-occurring
    with this one across roles (ranked by co-occurrence count), plus any
    concept already joined to it by an accepted concept_edge. The AI may
    only choose relationships among these — see
    concept_dossier.py::_filter_related_concepts."""
    cur.execute(
        """
        SELECT c.id, c.canonical_name, c.type_code, c.definition, COUNT(*) AS co_count
        FROM jobber.role_skill_observation rso1
        JOIN jobber.role_skill_observation rso2
            ON rso2.role_instance_id = rso1.role_instance_id AND rso2.canonical_concept_id != rso1.canonical_concept_id
        JOIN jobber.concept c ON c.id = rso2.canonical_concept_id
        WHERE rso1.canonical_concept_id = %s AND c.status = 'active'
        GROUP BY c.id
        ORDER BY co_count DESC
        LIMIT %s
        """,
        (concept_id, limit),
    )
    candidates: dict[str, dict] = {}
    for row in cur.fetchall():
        row = dict(row)
        row["id"] = str(row["id"])
        candidates[row["id"]] = row

    cur.execute(
        """
        SELECT c.id, c.canonical_name, c.type_code, c.definition
        FROM jobber.concept_edge ce
        JOIN jobber.concept c ON c.id = (CASE WHEN ce.from_concept_id = %s THEN ce.to_concept_id ELSE ce.from_concept_id END)
        WHERE (ce.from_concept_id = %s OR ce.to_concept_id = %s) AND ce.status = 'accepted' AND c.status = 'active'
        """,
        (concept_id, concept_id, concept_id),
    )
    for row in cur.fetchall():
        row = dict(row)
        row["id"] = str(row["id"])
        candidates.setdefault(row["id"], row)

    ranked = sorted(candidates.values(), key=lambda r: -(r.get("co_count") or 0))
    return ranked[:limit]


def _gather_grounding(cur, concept_id: str) -> tuple[dict, str, list[str], list[dict], list[dict]]:
    concept = _concept_or_404(cur, concept_id)
    cur.execute("SELECT definition FROM jobber.concept_type WHERE code = %s", (concept["type_code"],))
    type_row = cur.fetchone()
    type_definition = type_row["definition"] if type_row else ""
    cur.execute("SELECT alias FROM jobber.concept_alias WHERE concept_id = %s ORDER BY alias", (concept_id,))
    aliases = [r["alias"] for r in cur.fetchall()]
    role_evidence = _representative_role_evidence(cur, concept_id)
    candidates = _candidate_related_concepts(cur, concept_id)
    return concept, type_definition, aliases, role_evidence, candidates


def _build_input_text(
    concept: dict, type_definition: str, aliases: list[str],
    role_evidence: list[dict], candidates: list[dict], guidance: str | None,
) -> str:
    lines = [
        f"Canonical name: {concept['canonical_name']}",
        f"Type: {concept['type_code']} — {type_definition}",
        f"Definition on file: {concept.get('definition') or '(none yet)'}",
    ]
    if aliases:
        lines.append("Aliases: " + ", ".join(aliases))

    lines.append("")
    lines.append("Representative role evidence (from captured postings):")
    for ev in role_evidence:
        quote = ev.get("evidence_span") or ev.get("surface_form") or ""
        role_label = ev.get("title") or "Unknown role"
        org = ev.get("organisation") or "unknown organisation"
        lines.append(f'- {role_label} ({org}): "{quote}"')
    if not role_evidence:
        lines.append("- (no captured role evidence for this concept yet)")

    lines.append("")
    lines.append("Candidate related canonical concepts (choose relationships ONLY among these, by id; do not invent others):")
    for c in candidates:
        lines.append(f"- id={c['id']} | {c['canonical_name']} ({c['type_code']}): {c.get('definition') or ''}")
    if not candidates:
        lines.append("- (no candidates available)")

    if guidance:
        lines.append("")
        lines.append(f"Curator guidance for this generation — follow it: {guidance}")

    return "\n".join(lines)


def _filter_related_concepts(related: list, candidates: list[dict]) -> list[dict]:
    """§F: "The AI may choose and explain relationships ONLY among real
    accepted canonical concepts supplied as candidates." Any concept_id the
    model returns that isn't one of the candidates it was actually given is
    silently dropped — never trusted, never stored."""
    by_id = {c["id"]: c for c in candidates}
    out = []
    for item in related:
        candidate = by_id.get(item.concept_id)
        if candidate is None:
            continue
        out.append(
            {
                "concept_id": candidate["id"],
                "canonical_name": candidate["canonical_name"],
                "type_code": candidate["type_code"],
                "relationship": item.relationship,
                "explanation": item.explanation,
            }
        )
    return out


def _run_ai(concept, type_definition, aliases, role_evidence, candidates, guidance) -> ConceptDossierGeneration:
    input_text = _build_input_text(concept, type_definition, aliases, role_evidence, candidates, guidance)
    result = run_json_task(task=TASK, prompt_name=PROMPT_NAME, user_input=input_text, output_model=ConceptDossierGeneration)
    return result


# --- persistence --------------------------------------------------------------

def _insert_dossier(cur, *, concept_id: str, status: str, origin: str, model: str | None,
                     prompt_version_: str | None, guidance: str | None, content: dict,
                     raw_output: dict | None, now: datetime) -> dict:
    cur.execute(
        """
        INSERT INTO jobber.concept_dossier
            (concept_id, status, origin, generated_at, generator_version, model, prompt_version, guidance,
             plain_definition, classification_rationale, practical_meaning, underlying_elements,
             stronger_expressions, weaker_expressions, boundaries_and_overlaps, related_concepts, caveats,
             raw_output, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            concept_id, status, origin, now, GENERATOR_VERSION, model, prompt_version_, guidance,
            content["plain_definition"], content["classification_rationale"], content["practical_meaning"],
            to_json_param(content["underlying_elements"]), to_json_param(content["stronger_expressions"]),
            to_json_param(content["weaker_expressions"]), content["boundaries_and_overlaps"],
            to_json_param(content["related_concepts"]), content.get("caveats"),
            to_json_param(raw_output), now, now,
        ),
    )
    return dict(cur.fetchone())


def _content_from_ai(output: ConceptDossierGeneration, filtered_related: list[dict]) -> dict:
    return {
        "plain_definition": output.plain_definition,
        "classification_rationale": output.classification_rationale,
        "practical_meaning": output.practical_meaning,
        "underlying_elements": output.underlying_elements,
        "stronger_expressions": output.stronger_expressions,
        "weaker_expressions": output.weaker_expressions,
        "boundaries_and_overlaps": output.boundaries_and_overlaps,
        "related_concepts": filtered_related,
        "caveats": output.caveats,
    }


def generate_concept_dossier(concept_id: str, *, guidance: str | None = None) -> dict:
    """First-time generation (§D point 1). A no-op (returns the existing
    active dossier, no AI call) if one is already active — mirrors
    role_context's plain `generate`, so a repeated/accidental call never
    wastes a model call or creates a spurious version."""
    with db_cursor() as cur:
        _concept_or_404(cur, concept_id)
        existing = _active_row(cur, concept_id)
    if existing is not None:
        return {"created": False, "dossier": _serialize(existing)}

    with db_cursor() as cur:
        concept, type_definition, aliases, role_evidence, candidates = _gather_grounding(cur, concept_id)

    try:
        ai_result = _run_ai(concept, type_definition, aliases, role_evidence, candidates, guidance)
    except AITaskError as e:
        raise ConceptDossierGenerationError(str(e), error_type=type(e).__name__) from e

    filtered_related = _filter_related_concepts(ai_result.output.related_concepts, candidates)
    content = _content_from_ai(ai_result.output, filtered_related)
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        row = _insert_dossier(
            cur, concept_id=concept_id, status="active", origin="ai", model=ai_result.run.model,
            prompt_version_=ai_result.run.prompt_version, guidance=guidance, content=content,
            raw_output=json.loads(ai_result.output.model_dump_json()), now=now,
        )
    return {"created": True, "dossier": _serialize(row)}


def regenerate_concept_dossier(concept_id: str, *, guidance: str | None = None) -> dict:
    """§D point 2: only meaningful once an active dossier exists — always
    calls the model and creates/replaces a DRAFT, never the active row
    (that only ever changes via adopt_draft or save_manual_edit)."""
    with db_cursor() as cur:
        _concept_or_404(cur, concept_id)
        active = _active_row(cur, concept_id)
    if active is None:
        raise ConceptDossierStateError("no active dossier to regenerate from — generate one first")

    with db_cursor() as cur:
        concept, type_definition, aliases, role_evidence, candidates = _gather_grounding(cur, concept_id)

    try:
        ai_result = _run_ai(concept, type_definition, aliases, role_evidence, candidates, guidance)
    except AITaskError as e:
        raise ConceptDossierGenerationError(str(e), error_type=type(e).__name__) from e

    filtered_related = _filter_related_concepts(ai_result.output.related_concepts, candidates)
    content = _content_from_ai(ai_result.output, filtered_related)
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        # Supersede any existing draft first, in the same transaction as the
        # insert — same "unconditional supersede-then-insert" pattern as
        # role_context._persist, which is what keeps the partial unique
        # index (idx_concept_dossier_one_draft) from ever being violated.
        cur.execute(
            "UPDATE jobber.concept_dossier SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE concept_id = %s AND status = 'draft'",
            (now, now, concept_id),
        )
        row = _insert_dossier(
            cur, concept_id=concept_id, status="draft", origin="ai", model=ai_result.run.model,
            prompt_version_=ai_result.run.prompt_version, guidance=guidance, content=content,
            raw_output=json.loads(ai_result.output.model_dump_json()), now=now,
        )
    return {"created": True, "dossier": _serialize(row)}


def adopt_draft(concept_id: str) -> dict:
    """§D point 4: supersedes the old active row, promotes the draft to
    active, atomically — one transaction, supersede-then-promote so the
    one-active partial unique index is never violated mid-transaction."""
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        _concept_or_404(cur, concept_id)
        draft = _draft_row(cur, concept_id)
        if draft is None:
            raise ConceptDossierStateError("no draft dossier to adopt")

        cur.execute(
            "UPDATE jobber.concept_dossier SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE concept_id = %s AND status = 'active'",
            (now, now, concept_id),
        )
        cur.execute(
            "UPDATE jobber.concept_dossier SET status = 'active', updated_at = %s WHERE id = %s AND status = 'draft' RETURNING *",
            (now, draft["id"]),
        )
        row = cur.fetchone()
    return {"dossier": _serialize(dict(row))}


def discard_draft(concept_id: str) -> dict:
    """§D point 3: the curator declines the AI draft. The active dossier
    (curated or previously adopted) is left exactly as it was."""
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        _concept_or_404(cur, concept_id)
        cur.execute(
            "UPDATE jobber.concept_dossier SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE concept_id = %s AND status = 'draft' RETURNING id",
            (now, now, concept_id),
        )
        row = cur.fetchone()
    if not row:
        raise ConceptDossierStateError("no draft dossier to discard")
    return {"status": "discarded"}


def save_manual_edit(concept_id: str, patch: dict) -> dict:
    """§D point 5: a curator's direct edit. Retains version history exactly
    like an AI regeneration — supersedes the current active row and inserts
    a new one — but origin='curator', model/prompt_version/guidance/
    raw_output all null. Unspecified fields (not in `patch`, already
    exclude_unset-filtered by the route layer) carry over unchanged from the
    version being superseded; `related_concepts` is always carried over
    unchanged (this endpoint never edits it — see ConceptDossierManualEdit)."""
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        _concept_or_404(cur, concept_id)
        active = _active_row(cur, concept_id)
        if active is None:
            raise ConceptDossierStateError("no active dossier to edit — generate one first")

        content = {col: patch.get(col, active[col]) for col in _CONTENT_COLUMNS if col != "related_concepts"}
        content["related_concepts"] = active["related_concepts"]

        cur.execute(
            "UPDATE jobber.concept_dossier SET status = 'superseded', superseded_at = %s, updated_at = %s WHERE id = %s",
            (now, now, active["id"]),
        )
        row = _insert_dossier(
            cur, concept_id=concept_id, status="active", origin="curator", model=None,
            prompt_version_=None, guidance=None, content=content, raw_output=None, now=now,
        )
    return {"dossier": _serialize(row)}


# HTTP status for each AITaskError subclass, same mapping role_context.py
# already uses for the equivalent generation failure modes.
GENERATION_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


def raise_for_generation_error(e: ConceptDossierGenerationError) -> None:
    raise HTTPException(status_code=GENERATION_ERROR_STATUS.get(e.error_type, 502), detail=str(e)) from e
