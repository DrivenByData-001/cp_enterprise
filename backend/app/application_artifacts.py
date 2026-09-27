"""Phase 4: Application artifact generation + lifecycle (docs/35).

Same active/draft/superseded discipline as concept_dossier.py/role_context.py:
a plain read (`list_artifacts`/`list_artifact_history`) never calls the AI
provider; `generate_artifact` is the only thing that does, and the model is
always called outside any open DB transaction — evidence is read and that
transaction closed first, the network call happens with no transaction open,
and persistence happens in a fresh, short transaction only after a
successful, validated response comes back. A provider/format/schema failure,
or a post-validation rejection (an invented source_ref/episode_id/
concept_id), leaves the current active AND draft rows completely untouched —
nothing is written before that point.

Four artifact types (positioning | cv | cover_letter | supporting_statement)
share one lifecycle and one persistence shape (jobber.application_artifact,
migration 0028). Unlike concept_dossier's split generate/regenerate (generate
is a no-op once active exists; regenerate is the only thing that calls the
model again), Phase 4 has a single `generate_artifact` action that always
calls the model and always produces/replaces only the draft (build §2/§16) —
the frontend simply relabels the same action "Regenerate" once a draft or
active version already exists; the backend action is identical either way.
"""

import json
from datetime import date as _date
from datetime import datetime, timezone

from fastapi import HTTPException

from . import profile360_reader as p360
from .ai import AITaskError, run_json_task
from .application_generation import (
    ApplicationGenerationSubjectError,
    GenerationContext,
    build_application_generation_context,
    compute_fingerprint_for,
    gather_application_evidence,
    generation_readiness_summary,
    get_active_positioning,
)
from .db import db_cursor, to_json_param
from .models import (
    ApplicationCoverLetterGeneration,
    ApplicationCVGeneration,
    ApplicationPositioningGeneration,
    ApplicationSupportingStatementGeneration,
)

GENERATOR_VERSION = "1"
ARTIFACT_TYPES = ("positioning", "cv", "cover_letter", "supporting_statement")

_PROMPT_NAMES = {
    "positioning": "application_positioning.md",
    "cv": "application_cv.md",
    "cover_letter": "application_cover_letter.md",
    "supporting_statement": "application_supporting_statement.md",
}
_OUTPUT_MODELS = {
    "positioning": ApplicationPositioningGeneration,
    "cv": ApplicationCVGeneration,
    "cover_letter": ApplicationCoverLetterGeneration,
    "supporting_statement": ApplicationSupportingStatementGeneration,
}
_TASKS = {t: f"application_{t}_generate" for t in ARTIFACT_TYPES}


class ApplicationArtifactSubjectError(ValueError):
    """No such application, artifact, or artifact_type — 404 at the route layer."""


class ApplicationArtifactStateError(RuntimeError):
    """The requested action doesn't apply to the artifact's current state
    (adopt/discard with no draft, edit on a superseded row) — 409 at the
    route layer."""


class ApplicationArtifactValidationError(ValueError):
    """The model's response cited a source_ref/episode_id/concept_id it was
    never actually given, or a manual edit's content doesn't match its
    artifact_type's shape — 422 at the route layer. Raised only after a
    schema-valid response; nothing is ever persisted once this fires."""


class ApplicationArtifactGenerationError(RuntimeError):
    """The AI call itself failed (config/provider/format/schema) — mapped to
    a specific HTTP status at the route layer, same convention as
    concept_dossier.ConceptDossierGenerationError."""

    def __init__(self, message: str, *, error_type: str):
        super().__init__(message)
        self.error_type = error_type


GENERATION_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


def raise_for_generation_error(e: ApplicationArtifactGenerationError) -> None:
    raise HTTPException(status_code=GENERATION_ERROR_STATUS.get(e.error_type, 502), detail=str(e)) from e


def _require_artifact_type(artifact_type: str) -> None:
    if artifact_type not in ARTIFACT_TYPES:
        raise ApplicationArtifactSubjectError(f"unknown artifact_type {artifact_type!r}")


def _fold_target_words(guidance: str | None, target_words: int | None) -> str | None:
    """target_words has no column of its own — it is folded into the same
    `guidance` text column a plain style instruction would use, so it is
    covered by the same fingerprint/history/provenance machinery guidance
    already gets, without a schema change for one extra optional number."""
    if target_words is None:
        return guidance
    line = f"Target length: approximately {target_words} words."
    return f"{guidance}\n\n{line}" if guidance else line


# --- row helpers ---------------------------------------------------------


def _active_row(cur, application_id: str, artifact_type: str) -> dict | None:
    cur.execute(
        "SELECT * FROM jobber.application_artifact WHERE application_id = %s AND artifact_type = %s AND status = 'active'",
        (application_id, artifact_type),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _draft_row(cur, application_id: str, artifact_type: str) -> dict | None:
    cur.execute(
        "SELECT * FROM jobber.application_artifact WHERE application_id = %s AND artifact_type = %s AND status = 'draft'",
        (application_id, artifact_type),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _insert(cur, *, application_id, artifact_type, status, origin, model, prompt_name, prompt_version,
            guidance, input_fingerprint, source_manifest, content, raw_output, now) -> dict:
    cur.execute(
        """
        INSERT INTO jobber.application_artifact
            (application_id, artifact_type, status, origin, generator_version, model, prompt_name,
             prompt_version, guidance, input_fingerprint, source_manifest, content, raw_output,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            application_id, artifact_type, status, origin, GENERATOR_VERSION, model, prompt_name, prompt_version,
            guidance, input_fingerprint, to_json_param(source_manifest), to_json_param(content),
            to_json_param(raw_output), now, now,
        ),
    )
    return dict(cur.fetchone())


# --- CV chronology safeguard (docs/35 §10) ----------------------------------
#
# The model returns only `episode_id` (app/models.py::CVExperienceEntry) —
# never employer/title/dates. Resolution happens here, at read time, always
# against the live Profile360 episode row — never a copy stored in `content`
# — so a CV always shows the current authoritative chronology even if it was
# generated before a later episode edit.


def _coerce_date(value) -> _date | None:
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        try:
            return _date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _episode_sort_key(episode: dict | None) -> _date:
    if not episode:
        return _date.min
    end = _coerce_date(episode.get("end_date"))
    if end:
        return end
    if _coerce_date(episode.get("start_date")):
        return _date.max  # no end_date recorded -> current/ongoing, sorts first
    return _date.min


def _resolve_cv_content(cur, content: dict) -> dict:
    experience = content.get("experience") or []
    episode_ids = {e["episode_id"] for e in experience}
    episodes: dict[str, dict | None] = {}
    for episode_id in episode_ids:
        row = p360.get_episode(cur, episode_id)
        episodes[episode_id] = dict(row) if row else None

    resolved = []
    for entry in experience:
        episode = episodes.get(entry["episode_id"])
        resolved.append({
            **entry,
            "title": (episode or {}).get("title"),
            "organisation": (episode or {}).get("organisation"),
            "start_date": (episode or {}).get("start_date"),
            "end_date": (episode or {}).get("end_date"),
            "episode_found": episode is not None,
        })
    resolved.sort(key=lambda e: _episode_sort_key(episodes.get(e["episode_id"])), reverse=True)
    return {**content, "experience": resolved}


# --- serialization -----------------------------------------------------------


def _serialize(cur, row: dict) -> dict:
    """Wire shape: every persisted field except `raw_output` (audit/debug
    only — never returned by a normal read endpoint, docs/35 §7/§21).
    `grounding_status` is derived from `origin`, never stored separately, so
    it can never drift from it."""
    content = row["content"]
    if row["artifact_type"] == "cv":
        content = _resolve_cv_content(cur, content)
    return {
        "id": str(row["id"]),
        "application_id": str(row["application_id"]),
        "artifact_type": row["artifact_type"],
        "status": row["status"],
        "origin": row["origin"],
        "generator_version": row["generator_version"],
        "model": row["model"],
        "prompt_name": row["prompt_name"],
        "prompt_version": row["prompt_version"],
        "guidance": row["guidance"],
        "source_manifest": row["source_manifest"],
        "content": content,
        "grounding_status": "grounded_generation" if row["origin"] == "ai" else "user_edited_not_revalidated",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "superseded_at": row["superseded_at"],
    }


def compute_staleness(cur, application_id: str, row: dict, *, bundle=None, active_positioning=None) -> bool:
    bundle = bundle or gather_application_evidence(cur, application_id)
    if row["artifact_type"] != "positioning" and active_positioning is None:
        active_positioning = get_active_positioning(cur, application_id)
    current_fingerprint = compute_fingerprint_for(
        bundle, artifact_type=row["artifact_type"], guidance=row["guidance"],
        active_positioning=active_positioning if row["artifact_type"] != "positioning" else None,
    )
    return current_fingerprint != row["input_fingerprint"]


def _summary(cur, row: dict | None, bundle, active_positioning) -> dict | None:
    if row is None:
        return None
    serialized = _serialize(cur, row)
    serialized["stale"] = compute_staleness(cur, row["application_id"], row, bundle=bundle, active_positioning=active_positioning)
    return serialized


# --- reads (never call AI) --------------------------------------------------


def list_artifacts(cur, application_id: str) -> dict:
    """The one bounded request the workspace needs on load (build §13/§20):
    active/draft summaries (with `stale`) and a superseded-count per artifact
    type, plus the deterministic, AI-free generation-context summary
    (build §19). One evidence gather total, never once per artifact type."""
    bundle = gather_application_evidence(cur, application_id)
    active_positioning = get_active_positioning(cur, application_id)

    artifacts = {}
    for artifact_type in ARTIFACT_TYPES:
        active = _active_row(cur, application_id, artifact_type)
        draft = _draft_row(cur, application_id, artifact_type)
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.application_artifact "
            "WHERE application_id = %s AND artifact_type = %s AND status = 'superseded'",
            (application_id, artifact_type),
        )
        # Fetched immediately — _summary (for a CV artifact) issues its own
        # queries on this same cursor to resolve episode metadata, which
        # would otherwise clobber this pending result set before it's read.
        history_count = cur.fetchone()["n"]
        artifacts[artifact_type] = {
            "active": _summary(cur, active, bundle, active_positioning),
            "draft": _summary(cur, draft, bundle, active_positioning),
            "history_count": history_count,
        }

    return {
        "application_id": application_id,
        "artifacts": artifacts,
        "generation_context": generation_readiness_summary(bundle),
    }


def list_artifact_history(cur, application_id: str, artifact_type: str) -> list[dict]:
    _require_artifact_type(artifact_type)
    cur.execute("SELECT 1 FROM jobber.application WHERE id = %s", (application_id,))
    if not cur.fetchone():
        raise ApplicationGenerationSubjectError(f"application {application_id!r} not found")
    cur.execute(
        "SELECT * FROM jobber.application_artifact WHERE application_id = %s AND artifact_type = %s "
        "ORDER BY created_at DESC LIMIT 50",
        (application_id, artifact_type),
    )
    return [_serialize(cur, dict(r)) for r in cur.fetchall()]


# --- post-generation validation (docs/35 §7) --------------------------------


def _collect_by_key(node, key: str) -> set[str]:
    """Walks a plain dict/list structure (a Pydantic `.model_dump()`)
    collecting every string value found under `key` — used both for
    `source_refs` (list-valued) and `episode_id`/`concept_id` (scalar-valued)
    since the walk logic is identical either way."""
    found: set[str] = set()

    def _walk(node):
        if isinstance(node, dict):
            value = node.get(key)
            if isinstance(value, list):
                found.update(v for v in value if isinstance(v, str))
            elif isinstance(value, str):
                found.add(value)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(node)
    return found


def _validate_and_shape(artifact_type: str, output, ctx: GenerationContext) -> dict:
    """Rejects (raises, never silently filters) any source_ref, episode_id,
    or concept_id the model returned that wasn't actually offered to it —
    the model cannot establish grounding merely by inventing a
    plausible-looking id (docs/35 §7)."""
    data = output.model_dump()

    used_refs = _collect_by_key(data, "source_refs")
    unknown_refs = used_refs - ctx.known_source_refs()
    if unknown_refs:
        raise ApplicationArtifactValidationError(f"response cited unknown source_ref(s): {sorted(unknown_refs)}")

    if artifact_type == "cv":
        used_episodes = _collect_by_key(data, "episode_id")
        unknown_episodes = used_episodes - ctx.known_episode_ids()
        if unknown_episodes:
            raise ApplicationArtifactValidationError(f"response cited unknown episode_id(s): {sorted(unknown_episodes)}")

    if artifact_type == "positioning":
        lead_ids = {r["concept_id"] for r in data.get("requirements_to_lead_with", [])}
        unknown_lead = lead_ids - ctx.accepted_concept_ids()
        if unknown_lead:
            raise ApplicationArtifactValidationError(
                f"requirements_to_lead_with cited non-accepted concept_id(s): {sorted(unknown_lead)}"
            )
        gap_ids = {g["concept_id"] for g in data.get("gaps_and_cautions", []) if g.get("concept_id")}
        unknown_gap = gap_ids - ctx.known_concept_ids()
        if unknown_gap:
            raise ApplicationArtifactValidationError(f"gaps_and_cautions cited unknown concept_id(s): {sorted(unknown_gap)}")

    if artifact_type == "supporting_statement":
        section_ids = {s["concept_id"] for s in data.get("sections", []) if s.get("concept_id")}
        unknown_sections = section_ids - ctx.known_concept_ids()
        if unknown_sections:
            raise ApplicationArtifactValidationError(f"sections cited unknown concept_id(s): {sorted(unknown_sections)}")

    return data


# --- generate (the only thing that calls AI) --------------------------------


def generate_artifact(application_id: str, artifact_type: str, *, guidance: str | None = None,
                       target_words: int | None = None) -> dict:
    """Always creates a new draft (build §2/§16): supersedes any existing
    draft for this artifact_type first, never touches the current active
    version. The AI call happens with no DB transaction open — see module
    docstring. A validated response is required before anything is written;
    an AITaskError or a post-validation rejection leaves active/draft
    completely untouched."""
    _require_artifact_type(artifact_type)
    effective_guidance = _fold_target_words(guidance, target_words)

    with db_cursor() as cur:
        ctx = build_application_generation_context(
            cur, application_id, artifact_type=artifact_type, guidance=effective_guidance,
        )
    # committed/closed here — the network call below happens with no
    # transaction open (docs/35 §3).

    prompt_name = _PROMPT_NAMES[artifact_type]
    output_model = _OUTPUT_MODELS[artifact_type]
    try:
        ai_result = run_json_task(
            task=_TASKS[artifact_type], prompt_name=prompt_name, user_input=ctx.prompt_text, output_model=output_model,
        )
    except AITaskError as e:
        raise ApplicationArtifactGenerationError(str(e), error_type=type(e).__name__) from e

    content = _validate_and_shape(artifact_type, ai_result.output, ctx)  # raises before any write on failure

    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.application_artifact SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE application_id = %s AND artifact_type = %s AND status = 'draft'",
            (now, now, application_id, artifact_type),
        )
        row = _insert(
            cur, application_id=application_id, artifact_type=artifact_type, status="draft", origin="ai",
            model=ai_result.run.model, prompt_name=prompt_name, prompt_version=ai_result.run.prompt_version,
            guidance=effective_guidance, input_fingerprint=ctx.input_fingerprint,
            source_manifest=ctx.source_manifest(), content=content,
            raw_output=json.loads(ai_result.output.model_dump_json()), now=now,
        )
        serialized = _serialize(cur, row)
    serialized["stale"] = False  # freshly generated against current evidence, by definition
    return {"created": True, "artifact": serialized}


# --- edit / adopt / discard (never call AI) ---------------------------------


def edit_artifact(application_id: str, artifact_id: str, content: dict) -> dict:
    """A user's direct edit (build §6/§17): always creates a NEW draft
    (origin='user_edit'), superseding the prior draft if one existed —
    editing the active version never mutates it in place; the user must
    still explicitly adopt the resulting draft. `content` is re-validated for
    *shape* against the artifact_type's own output model, but never against
    the source registry — a manual edit's source_refs are retained as
    contextual/inherited provenance and `grounding_status` becomes
    `user_edited_not_revalidated` (see `_serialize`) rather than pretending
    the edit was machine-verified."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM jobber.application_artifact WHERE id = %s AND application_id = %s",
            (artifact_id, application_id),
        )
        source_row = cur.fetchone()
        if not source_row:
            raise ApplicationArtifactSubjectError("artifact not found on this application")
        source_row = dict(source_row)
        if source_row["status"] == "superseded":
            raise ApplicationArtifactStateError("cannot edit a superseded (historical) version")

        artifact_type = source_row["artifact_type"]
        output_model = _OUTPUT_MODELS[artifact_type]
        try:
            validated = output_model.model_validate(content)
        except Exception as e:
            raise ApplicationArtifactValidationError(f"edited content does not match {artifact_type} shape: {e}") from e

        now = datetime.now(timezone.utc)
        cur.execute(
            "UPDATE jobber.application_artifact SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE application_id = %s AND artifact_type = %s AND status = 'draft'",
            (now, now, application_id, artifact_type),
        )
        row = _insert(
            cur, application_id=application_id, artifact_type=artifact_type, status="draft", origin="user_edit",
            model=None, prompt_name=None, prompt_version=None, guidance=source_row["guidance"],
            input_fingerprint=source_row["input_fingerprint"], source_manifest=source_row["source_manifest"],
            content=validated.model_dump(), raw_output=None, now=now,
        )
        serialized = _serialize(cur, row)
        serialized["stale"] = compute_staleness(cur, application_id, row)
    return {"artifact": serialized}


def adopt_artifact(application_id: str, artifact_id: str) -> dict:
    """Promotes the current draft to active, atomically superseding the old
    active row in the same transaction (build §2 lifecycle rules)."""
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        cur.execute(
            "SELECT artifact_type FROM jobber.application_artifact "
            "WHERE id = %s AND application_id = %s AND status = 'draft'",
            (artifact_id, application_id),
        )
        draft = cur.fetchone()
        if not draft:
            raise ApplicationArtifactStateError("no such draft to adopt on this application")
        artifact_type = draft["artifact_type"]

        cur.execute(
            "UPDATE jobber.application_artifact SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE application_id = %s AND artifact_type = %s AND status = 'active'",
            (now, now, application_id, artifact_type),
        )
        cur.execute(
            "UPDATE jobber.application_artifact SET status = 'active', updated_at = %s WHERE id = %s RETURNING *",
            (now, artifact_id),
        )
        row = cur.fetchone()
        serialized = _serialize(cur, dict(row))
        serialized["stale"] = compute_staleness(cur, application_id, dict(row))
    return {"artifact": serialized}


def discard_artifact(application_id: str, artifact_id: str) -> dict:
    """Declines the current draft. The active version (if any) is untouched."""
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.application_artifact SET status = 'superseded', superseded_at = %s, updated_at = %s "
            "WHERE id = %s AND application_id = %s AND status = 'draft' RETURNING id",
            (now, now, artifact_id, application_id),
        )
        row = cur.fetchone()
    if not row:
        raise ApplicationArtifactStateError("no such draft to discard on this application")
    return {"status": "discarded"}
