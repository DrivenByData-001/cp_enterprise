"""Phase 6: Career Direction CRUD, selection, discovery orchestration and
candidate adoption (docs/37).

Three different things (build §1), never collapsed into one table or one
score:

- Preferences (jobber.preference_observation) — untouched by this module.
- Career Direction (jobber.career_direction/career_direction_dimension) —
  the user-owned statement of a desired future state, configured here.
- Target (jobber.role_instance) — one concrete role hypothesis, linked to
  but never duplicated by a Career Direction.

Lifecycle discipline, shared with concept_dossier.py/application_artifacts.py:
a plain read (`list_directions`/`get_direction`/`get_discovery_run`) never
calls the AI provider and never writes; `discover_candidates` is the only
thing that does, and the model is always called with no DB transaction
open — the discovery context is gathered and that transaction closed first,
the network call happens next, and persistence happens in a fresh, short
transaction only after a successful, validated response. A provider/format/
schema/grounding failure creates no Career Direction, selects nothing,
changes no Target, and leaves existing directions completely untouched
(build §11) — `_persist_run` only ever records the *attempt*, in its own
audit table, never jobber.career_direction itself.

Selection is the one state change AI can never make: `select_direction` is
the only writer of state='selected', always as an explicit, transactional
supersede-then-promote (existing selected -> exploring, chosen -> selected)
so the database's own partial unique index (migration 0030) is never
violated mid-transaction. `discover_candidates`/`adopt_candidate` never call
it.
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from . import career_direction_discovery as cdd
from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import db_cursor, to_json_param
from .models import (
    CareerDirectionAdoptRequest,
    CareerDirectionConstraints,
    CareerDirectionCreate,
    CareerDirectionDimensionInput,
    CareerDirectionDiscoverRequest,
    CareerDirectionDiscoveryResult,
    CareerDirectionUpdate,
)

TASK = "career_direction_discover"
PROMPT_NAME = "career_direction_discovery.md"


class CareerDirectionSubjectError(ValueError):
    """No such Career Direction, discovery run, or candidate — a 404 at the
    route layer."""


class CareerDirectionLinkError(ValueError):
    """An invalid dimension_code / target_role_instance_id / target_archetype_
    concept_id was supplied to a manual create/update — a 400 at the route
    layer, before anything is written."""


class CareerDirectionStateError(RuntimeError):
    """The requested action doesn't apply to the direction's current state
    (select an archived direction, reopen a non-archived one) — 409 at the
    route layer."""


class CareerDirectionValidationError(ValueError):
    """The model's response was schema-valid JSON but failed a grounding
    check (an invented source_ref/archetype id, or a factual block missing
    the source category it requires) — 422 at the route layer. Nothing is
    ever persisted as a Career Direction once this fires; the discovery run
    itself is still recorded, as `failed`, for audit."""


class CareerDirectionGenerationError(RuntimeError):
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


def raise_for_generation_error(e: CareerDirectionGenerationError) -> None:
    raise HTTPException(status_code=GENERATION_ERROR_STATUS.get(e.error_type, 502), detail=str(e)) from e


def _safe_task_metadata() -> tuple[str, str]:
    """Best-effort model/prompt_version even when run_json_task never got far
    enough to return one (e.g. no OPENAI_API_KEY) — same pattern as
    concept_dossier._safe_task_metadata; must never itself raise."""
    try:
        model = ai_model_name()
    except AIConfigError:
        model = "unconfigured"
    try:
        version = prompt_version(load_prompt(PROMPT_NAME))
    except AIConfigError:
        version = "unknown"
    return model, version


# --- validation helpers (manual create/update; build §3/§4) ----------------


def _validate_dimension_codes(cur, dimensions: list[CareerDirectionDimensionInput]) -> None:
    if not dimensions:
        return
    codes = [d.dimension_code for d in dimensions]
    cur.execute("SELECT code FROM jobber.preference_dimension WHERE code = ANY(%s)", (codes,))
    known = {r["code"] for r in cur.fetchall()}
    unknown = set(codes) - known
    if unknown:
        raise CareerDirectionLinkError(f"unknown dimension_code(s): {sorted(unknown)}")


def _validate_target_link(cur, target_role_instance_id: str | None) -> None:
    """A Career Direction's Target may only be a non-posting role_instance
    (build §3) — an observed posting is market data, never a user's own
    hypothesis."""
    if target_role_instance_id is None:
        return
    cur.execute("SELECT instance_type FROM jobber.role_instance WHERE id = %s", (target_role_instance_id,))
    row = cur.fetchone()
    if not row:
        raise CareerDirectionLinkError(f"target_role_instance_id {target_role_instance_id!r} not found")
    if row["instance_type"] == "observed_posting":
        raise CareerDirectionLinkError(
            "an observed posting cannot be linked as a Career Direction's Target — create or link a Target instead"
        )


def _validate_archetype_link(cur, archetype_concept_id: str | None) -> None:
    if archetype_concept_id is None:
        return
    cur.execute(
        "SELECT 1 FROM jobber.concept WHERE id = %s AND type_code = 'role_archetype' AND status = 'active'",
        (archetype_concept_id,),
    )
    if not cur.fetchone():
        raise CareerDirectionLinkError(
            f"target_archetype_concept_id {archetype_concept_id!r} is not an active role_archetype concept"
        )


def _write_dimensions(cur, direction_id: str, dimensions: list[CareerDirectionDimensionInput]) -> None:
    cur.execute("DELETE FROM jobber.career_direction_dimension WHERE career_direction_id = %s", (direction_id,))
    for d in dimensions:
        cur.execute(
            "INSERT INTO jobber.career_direction_dimension "
            "(career_direction_id, dimension_code, desired_direction, importance, note) VALUES (%s, %s, %s, %s, %s)",
            (direction_id, d.dimension_code, d.desired_direction, d.importance, d.note),
        )


# --- serialization -----------------------------------------------------------


def _direction_or_404(cur, direction_id: str) -> dict:
    cur.execute(
        "SELECT cd.*, ri.title AS target_title, ri.organisation AS target_organisation, "
        "       ac.canonical_name AS archetype_name, ac.status AS archetype_status "
        "FROM jobber.career_direction cd "
        "LEFT JOIN jobber.role_instance ri ON ri.id = cd.target_role_instance_id "
        "LEFT JOIN jobber.concept ac ON ac.id = cd.target_archetype_concept_id "
        "WHERE cd.id = %s",
        (direction_id,),
    )
    row = cur.fetchone()
    if not row:
        raise CareerDirectionSubjectError(f"career direction {direction_id!r} not found")
    return dict(row)


def _dimension_rows(cur, direction_id: str) -> list[dict]:
    cur.execute(
        "SELECT dimension_code, desired_direction, importance, note FROM jobber.career_direction_dimension "
        "WHERE career_direction_id = %s ORDER BY dimension_code",
        (direction_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def _dimension_rows_bulk(cur, direction_ids: list[str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {did: [] for did in direction_ids}
    if not direction_ids:
        return grouped
    cur.execute(
        "SELECT career_direction_id, dimension_code, desired_direction, importance, note "
        "FROM jobber.career_direction_dimension WHERE career_direction_id = ANY(%s::uuid[]) ORDER BY dimension_code",
        (direction_ids,),
    )
    for r in cur.fetchall():
        grouped.setdefault(str(r["career_direction_id"]), []).append(
            {"dimension_code": r["dimension_code"], "desired_direction": r["desired_direction"],
             "importance": r["importance"], "note": r["note"]}
        )
    return grouped


def _serialize(row: dict, dimensions: list[dict]) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "summary": row["summary"],
        "state": row["state"],
        "origin": row["origin"],
        "dimensions": dimensions,
        "constraints": row["constraints"],
        "target": (
            {"id": str(row["target_role_instance_id"]), "title": row["target_title"], "organisation": row["target_organisation"]}
            if row["target_role_instance_id"] else None
        ),
        "archetype": (
            {"id": str(row["target_archetype_concept_id"]), "canonical_name": row["archetype_name"], "status": row["archetype_status"]}
            if row["target_archetype_concept_id"] else None
        ),
        "source_discovery_run_id": str(row["source_discovery_run_id"]) if row["source_discovery_run_id"] else None,
        "source_candidate_id": row["source_candidate_id"],
        "selected_at": row["selected_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# --- reads (never call AI) --------------------------------------------------


def list_directions(cur, state: str | None = None) -> list[dict]:
    """Bounded list (build §7/§27) — one query for the rows plus target/
    archetype summary joins, one bulk query for every direction's dimensions.
    Never once per direction. Defaults to non-archived (build §22 "Directions
    I'm exploring" is exactly this default, plus the selected one)."""
    query = (
        "SELECT cd.*, ri.title AS target_title, ri.organisation AS target_organisation, "
        "       ac.canonical_name AS archetype_name, ac.status AS archetype_status "
        "FROM jobber.career_direction cd "
        "LEFT JOIN jobber.role_instance ri ON ri.id = cd.target_role_instance_id "
        "LEFT JOIN jobber.concept ac ON ac.id = cd.target_archetype_concept_id "
    )
    params: list = []
    if state:
        query += "WHERE cd.state = %s "
        params.append(state)
    else:
        query += "WHERE cd.state != 'archived' "
    query += "ORDER BY (cd.state = 'selected') DESC, cd.updated_at DESC LIMIT 100"
    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    ids = [str(r["id"]) for r in rows]
    dims_by_id = _dimension_rows_bulk(cur, ids)
    return [_serialize(r, dims_by_id.get(str(r["id"]), [])) for r in rows]


def preference_summary(cur) -> dict:
    """Read-only pass-through to the deterministic preference summary (build
    §9) — used by the property-first builder's "seed from my preferences"
    action. Never persists anything; a convenience `suggested_direction` is
    never written back as a new preference_observation row."""
    return cdd.summarize_preferences(cur)


def get_selected_direction_summary(cur) -> dict | None:
    """The one cheap read Home/`/future`/the Opportunity workspace each make
    to show the current direction (build §23/§24) — a single indexed lookup,
    never a full list fetch filtered client-side in three different places."""
    cur.execute(
        "SELECT cd.*, ri.title AS target_title, ri.organisation AS target_organisation, "
        "       ac.canonical_name AS archetype_name, ac.status AS archetype_status "
        "FROM jobber.career_direction cd "
        "LEFT JOIN jobber.role_instance ri ON ri.id = cd.target_role_instance_id "
        "LEFT JOIN jobber.concept ac ON ac.id = cd.target_archetype_concept_id "
        "WHERE cd.state = 'selected'"
    )
    row = cur.fetchone()
    if not row:
        return None
    row = dict(row)
    return _serialize(row, _dimension_rows(cur, str(row["id"])))


def get_direction(cur, direction_id: str) -> dict:
    row = _direction_or_404(cur, direction_id)
    result = _serialize(row, _dimension_rows(cur, direction_id))

    if row["target_archetype_concept_id"]:
        aid = str(row["target_archetype_concept_id"])
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance WHERE archetype_concept_id = %s", (aid,))
        assigned = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_archetype_demand WHERE archetype_concept_id = %s", (aid,))
        demand_count = cur.fetchone()["n"]
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.d_archetype_comp WHERE archetype_concept_id = %s AND reference_comp IS NOT NULL",
            (aid,),
        )
        comp_count = cur.fetchone()["n"]
        result["archetype_evidence"] = {
            "assigned_postings": assigned, "demand_capabilities": demand_count, "compensation_buckets": comp_count,
        }
    else:
        result["archetype_evidence"] = None
    return result


# --- manual create / update (never call AI; build §19) ---------------------


def create_direction(cur, payload: CareerDirectionCreate) -> dict:
    _validate_dimension_codes(cur, payload.dimensions)
    _validate_target_link(cur, payload.target_role_instance_id)
    _validate_archetype_link(cur, payload.target_archetype_concept_id)

    cur.execute(
        "INSERT INTO jobber.career_direction "
        "(name, summary, origin, target_role_instance_id, target_archetype_concept_id, constraints) "
        "VALUES (%s, %s, 'user', %s, %s, %s) RETURNING id",
        (
            payload.name, payload.summary, payload.target_role_instance_id, payload.target_archetype_concept_id,
            to_json_param(payload.constraints.model_dump()),
        ),
    )
    direction_id = str(cur.fetchone()["id"])
    _write_dimensions(cur, direction_id, payload.dimensions)
    return get_direction(cur, direction_id)


def update_direction(cur, direction_id: str, payload: CareerDirectionUpdate) -> dict:
    """Only supplied fields change (build §7). Never touches state/selected_at
    — select_direction/archive_direction/reopen_direction are the only paths
    that may."""
    _direction_or_404(cur, direction_id)
    patch = payload.model_dump(exclude_unset=True)

    if "dimensions" in patch:
        dims = [CareerDirectionDimensionInput(**d) for d in patch["dimensions"]]
        _validate_dimension_codes(cur, dims)
    if "target_role_instance_id" in patch:
        _validate_target_link(cur, patch["target_role_instance_id"])
    if "target_archetype_concept_id" in patch:
        _validate_archetype_link(cur, patch["target_archetype_concept_id"])

    columns: dict = {}
    for field in ("name", "summary", "target_role_instance_id", "target_archetype_concept_id"):
        if field in patch:
            columns[field] = patch[field]
    if "constraints" in patch:
        columns["constraints"] = to_json_param(patch["constraints"])

    if columns:
        set_clause = ", ".join(f"{c} = %s" for c in columns)
        cur.execute(
            f"UPDATE jobber.career_direction SET {set_clause}, updated_at = now() WHERE id = %s",
            [*columns.values(), direction_id],
        )
    if "dimensions" in patch:
        _write_dimensions(cur, direction_id, dims)

    return get_direction(cur, direction_id)


# --- explicit selection / archive / reopen (human-only; build §3/§7) -------


def select_direction(cur, direction_id: str) -> dict:
    """The only writer of state='selected' anywhere in this build. Runs as
    one transaction on the caller's cursor: existing selected -> exploring,
    chosen -> selected, so migration 0030's partial unique index is never
    violated mid-transaction. Idempotent if already selected."""
    row = _direction_or_404(cur, direction_id)
    if row["state"] == "archived":
        raise CareerDirectionStateError("an archived direction cannot be selected — reopen it first")
    if row["state"] == "selected":
        return get_direction(cur, direction_id)

    now = datetime.now(timezone.utc)
    cur.execute("UPDATE jobber.career_direction SET state = 'exploring', updated_at = %s WHERE state = 'selected'", (now,))
    cur.execute(
        "UPDATE jobber.career_direction SET state = 'selected', selected_at = %s, updated_at = %s WHERE id = %s",
        (now, now, direction_id),
    )
    return get_direction(cur, direction_id)


def archive_direction(cur, direction_id: str) -> dict:
    """Archiving the selected direction leaves no selected direction at all —
    never auto-picks a replacement (build §7/§20)."""
    _direction_or_404(cur, direction_id)
    cur.execute(
        "UPDATE jobber.career_direction SET state = 'archived', updated_at = now() WHERE id = %s", (direction_id,)
    )
    return get_direction(cur, direction_id)


def reopen_direction(cur, direction_id: str) -> dict:
    row = _direction_or_404(cur, direction_id)
    if row["state"] != "archived":
        raise CareerDirectionStateError("only an archived direction can be reopened")
    cur.execute(
        "UPDATE jobber.career_direction SET state = 'exploring', updated_at = now() WHERE id = %s", (direction_id,)
    )
    return get_direction(cur, direction_id)


# --- discovery run reads (never call AI; build §17) -------------------------


def _serialize_run(row: dict) -> dict:
    """Every persisted field except `raw_output` (audit/debug only, same
    posture as concept_dossier/application_artifact — never returned by a
    normal read endpoint, build §6)."""
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "model": row["model"],
        "prompt_name": row["prompt_name"],
        "prompt_version": row["prompt_version"],
        "guidance": row["guidance"],
        "criteria": row["criteria"],
        "source_manifest": row["source_manifest"],
        "output": row["output"],
        "error_type": row["error_type"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def get_discovery_run(cur, run_id: str) -> dict:
    cur.execute("SELECT * FROM jobber.career_direction_discovery_run WHERE id = %s", (run_id,))
    row = cur.fetchone()
    if not row:
        raise CareerDirectionSubjectError(f"discovery run {run_id!r} not found")
    return _serialize_run(dict(row))


def list_discovery_runs(cur, limit: int = 20) -> list[dict]:
    cur.execute(
        "SELECT * FROM jobber.career_direction_discovery_run ORDER BY created_at DESC LIMIT %s", (min(limit, 50),)
    )
    return [_serialize_run(dict(r)) for r in cur.fetchall()]


# --- grounding validation (build §14/§15) -----------------------------------


def _collect_source_refs(node) -> set[str]:
    found: set[str] = set()

    def _walk(n):
        if isinstance(n, dict):
            value = n.get("source_refs")
            if isinstance(value, list):
                found.update(v for v in value if isinstance(v, str))
            for v in n.values():
                _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return found


def _require_grounded(label: str, source_refs: list) -> None:
    if not source_refs:
        raise CareerDirectionValidationError(f"{label} has no source_refs — every candidate block must cite at least one source")


def _require_category(label: str, source_refs: list, ctx: cdd.DiscoveryContext, categories: set[str]) -> None:
    _require_grounded(label, source_refs)
    cited_categories = {ctx.category_for_ref(r) for r in source_refs}
    if not (cited_categories & categories):
        raise CareerDirectionValidationError(
            f"{label} does not cite any source in the required category/categories {sorted(categories)}"
        )


def _validate_and_shape(output: CareerDirectionDiscoveryResult, ctx: cdd.DiscoveryContext) -> dict:
    """Rejects (raises, never silently filters) any candidate citing a
    source_ref or archetype id that was not actually offered — a model
    cannot establish grounding merely by inventing a plausible-looking id
    (build §15). Every factual block must additionally cite at least one
    source from the epistemic category that claim actually needs: a
    preference-alignment claim needs direction_input/preference evidence, a
    market claim needs market evidence, and a claim that the user already
    has/demonstrates something needs person-side (Profile360) evidence — a
    role posting or archetype can never itself prove the user has a
    capability."""
    data = output.model_dump()
    known_refs = ctx.known_source_refs()
    known_archetypes = ctx.known_archetype_ids()

    if not data["insufficient_evidence"] and not data["candidates"]:
        data["insufficient_evidence"] = True
        data["insufficient_evidence_reason"] = (
            data.get("insufficient_evidence_reason")
            or "No grounded hypothesis could be produced from the available evidence."
        )

    for candidate in data["candidates"]:
        label_name = candidate["name"]
        used_refs = _collect_source_refs(candidate)
        unknown_refs = used_refs - known_refs
        if unknown_refs:
            raise CareerDirectionValidationError(f"candidate {label_name!r} cited unknown source_ref(s): {sorted(unknown_refs)}")

        cited_archetypes = [a for a in (candidate.get("primary_archetype_id"), *candidate.get("related_archetype_ids", [])) if a]
        unknown_archetypes = set(cited_archetypes) - known_archetypes
        if unknown_archetypes:
            raise CareerDirectionValidationError(f"candidate {label_name!r} cited unknown archetype id(s): {sorted(unknown_archetypes)}")

        _require_grounded(f"candidate {label_name!r} summary", candidate["summary"]["source_refs"])
        for item in candidate["priority_alignment"]:
            _require_category(
                f"candidate {label_name!r} priority_alignment[{item['dimension_code']}]", item["source_refs"], ctx,
                {cdd.CATEGORY_DIRECTION_INPUT, cdd.CATEGORY_PREFERENCE},
            )
        for item in candidate["market_basis"]:
            _require_category(f"candidate {label_name!r} market_basis", item["source_refs"], ctx, {cdd.CATEGORY_MARKET_EVIDENCE})
        for item in candidate["person_basis"]:
            _require_category(f"candidate {label_name!r} person_basis", item["source_refs"], ctx, {cdd.CATEGORY_PERSON_EVIDENCE})
        if candidate.get("compensation_context"):
            _require_category(
                f"candidate {label_name!r} compensation_context", candidate["compensation_context"]["source_refs"], ctx,
                {cdd.CATEGORY_MARKET_EVIDENCE},
            )
        for item in candidate["tradeoffs"]:
            _require_grounded(f"candidate {label_name!r} tradeoffs entry", item["source_refs"])
        for item in candidate["unknowns"]:
            _require_grounded(f"candidate {label_name!r} unknowns entry", item["source_refs"])

    # A stable per-candidate id for adoption lookups (build §18) — assigned
    # here, never by the model, and never part of its output schema.
    for candidate in data["candidates"]:
        candidate["id"] = str(uuid.uuid4())

    return data


# --- discovery orchestration (the only thing that calls AI; build §11) -----


def _persist_run(*, status: str, model: str, prompt_version_: str, guidance: str | None, input_fingerprint: str,
                  criteria: dict, source_manifest: list, output: dict | None, raw_output: dict | None,
                  error_type: str | None, error_message: str | None, started_at: datetime) -> str:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobber.career_direction_discovery_run
                (status, model, prompt_name, prompt_version, guidance, input_fingerprint, criteria,
                 source_manifest, output, raw_output, error_type, error_message, created_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            RETURNING id
            """,
            (
                status, model, PROMPT_NAME, prompt_version_, guidance, input_fingerprint, to_json_param(criteria),
                to_json_param(source_manifest), to_json_param(output), to_json_param(raw_output),
                error_type, error_message, started_at,
            ),
        )
        return str(cur.fetchone()["id"])


def discover_candidates(payload: CareerDirectionDiscoverRequest) -> dict:
    """POST /api/career-directions/discover (build §11). Sequence: validate
    criteria; assemble the bounded discovery context in a short transaction;
    close it; call the model with no transaction open; validate the response;
    persist the run in a fresh short transaction; return reviewable
    candidates. A failure at any stage after context assembly still records a
    `failed` run for audit, but creates/selects/changes no Career Direction,
    Target, or existing direction (build §11/§28)."""
    with db_cursor() as cur:
        _validate_dimension_codes(cur, payload.dimensions)
        ctx = cdd.build_discovery_context(
            cur, dimensions=payload.dimensions, constraints=payload.constraints, guidance=payload.guidance,
        )
    # Transaction closed here — the model call below happens with no DB
    # transaction open (build §11/§27).

    model, pversion = _safe_task_metadata()
    started_at = datetime.now(timezone.utc)
    criteria = cdd.criteria_payload(
        name=payload.name, dimensions=payload.dimensions, constraints=payload.constraints, guidance=payload.guidance,
    )

    try:
        ai_result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=ctx.prompt_text, output_model=CareerDirectionDiscoveryResult,
        )
    except AITaskError as e:
        _persist_run(
            status="failed", model=model, prompt_version_=pversion, guidance=payload.guidance,
            input_fingerprint=ctx.input_fingerprint, criteria=criteria, source_manifest=ctx.source_manifest(),
            output=None, raw_output=None, error_type=type(e).__name__, error_message=str(e), started_at=started_at,
        )
        raise CareerDirectionGenerationError(str(e), error_type=type(e).__name__) from e

    try:
        validated_output = _validate_and_shape(ai_result.output, ctx)
    except CareerDirectionValidationError as e:
        _persist_run(
            status="failed", model=ai_result.run.model, prompt_version_=ai_result.run.prompt_version,
            guidance=payload.guidance, input_fingerprint=ctx.input_fingerprint, criteria=criteria,
            source_manifest=ctx.source_manifest(), output=None,
            raw_output=json.loads(ai_result.output.model_dump_json()),
            error_type="CareerDirectionValidationError", error_message=str(e), started_at=started_at,
        )
        raise

    run_id = _persist_run(
        status="ok", model=ai_result.run.model, prompt_version_=ai_result.run.prompt_version,
        guidance=payload.guidance, input_fingerprint=ctx.input_fingerprint, criteria=criteria,
        source_manifest=ctx.source_manifest(), output=validated_output,
        raw_output=json.loads(ai_result.output.model_dump_json()), error_type=None, error_message=None,
        started_at=started_at,
    )
    return {
        "status": "ok", "discovery_run_id": run_id, "result": validated_output, "caveats": ctx.caveats,
        "corpus_disclosure": ctx.corpus_disclosure,
    }


def adopt_candidate(run_id: str, candidate_id: str, payload: CareerDirectionAdoptRequest) -> dict:
    """POST .../discovery-runs/{run_id}/candidates/{candidate_id}/adopt (build
    §18). Verifies the run exists, the candidate exists in that run, and (when
    still present) the cited archetype still exists and is active — never
    selects, never touches any other Career Direction."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.career_direction_discovery_run WHERE id = %s", (run_id,))
        run = cur.fetchone()
        if not run:
            raise CareerDirectionSubjectError(f"discovery run {run_id!r} not found")
        run = dict(run)
        if run["status"] != "ok" or not run["output"]:
            raise CareerDirectionSubjectError(f"discovery run {run_id!r} has no adoptable candidates")

        candidate = next((c for c in run["output"].get("candidates", []) if c.get("id") == candidate_id), None)
        if candidate is None:
            raise CareerDirectionSubjectError(f"candidate {candidate_id!r} not found in discovery run {run_id!r}")

        primary_archetype_id = candidate.get("primary_archetype_id")
        if primary_archetype_id:
            cur.execute(
                "SELECT 1 FROM jobber.concept WHERE id = %s AND type_code = 'role_archetype' AND status = 'active'",
                (primary_archetype_id,),
            )
            if not cur.fetchone():
                # Catalogue drift since the run was generated (the archetype
                # was deprecated/removed) — adopt the direction anyway, just
                # without an archetype anchor, rather than blocking a human
                # decision on stale catalogue state.
                primary_archetype_id = None

        criteria = run["criteria"] or {}
        dimensions = [CareerDirectionDimensionInput(**d) for d in criteria.get("dimensions", [])]
        constraints = CareerDirectionConstraints(**(criteria.get("constraints") or {}))

        name = (payload.name or candidate["name"] or "").strip() or candidate["name"]
        summary = payload.summary if payload.summary is not None else candidate["summary"]["text"]

        cur.execute(
            "INSERT INTO jobber.career_direction "
            "(name, summary, origin, target_archetype_concept_id, constraints, source_discovery_run_id, source_candidate_id) "
            "VALUES (%s, %s, 'ai_adopted', %s, %s, %s, %s) RETURNING id",
            (name, summary, primary_archetype_id, to_json_param(constraints.model_dump()), run_id, candidate_id),
        )
        direction_id = str(cur.fetchone()["id"])
        _write_dimensions(cur, direction_id, dimensions)
        result = get_direction(cur, direction_id)
    return {"created": True, "direction": result}
