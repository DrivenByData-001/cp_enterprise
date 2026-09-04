"""Phase 2 AI extraction service: role requirement extraction, concept
adjudication, and profile360 claim/capability mapping — the four closed-
vocabulary tasks the brief (§8) asks for. Builds on `app.ai.run_json_task`
(never calls a provider client directly) and `app.concept_linking`'s
canonicalisation cascade (docs/11 §7.3): exact match, then embedding kNN
retrieval, then model adjudication among the retrieved candidates only — the
model is never shown, and can never choose outside, the candidate set.

Every call records a `jobber.extraction_run` row, success or failure (brief
§8/§9) — this module is the only thing that writes to that table besides the
review endpoints' mapping-run bookkeeping.
"""

import json
from datetime import datetime, timezone

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .concept_linking import (
    exact_match_concept_id,
    get_or_create_current_vocabulary_version,
    nearest_concept,
    nearest_concepts,
    normalize_name,
)
from .models import (
    ClaimMappingResult,
    ConceptAdjudicationResult,
    RequirementExtractionResult,
)
from .profile360_reader import Profile360UnavailableError, display_text, get_capability, get_claim, list_claims
from .span_validation import validate_span


class ExtractionSubjectError(ValueError):
    """The requested subject (role_instance / profile360 row) doesn't exist or
    isn't in a state this extraction can run against — a 4xx at the route
    layer, not something to record as a failed AI run (no AI call was made)."""


def _safe_task_metadata(prompt_name: str) -> tuple[str, str]:
    """Best-effort model/prompt_version for an extraction_run row even when
    the real run_json_task call never got far enough to return one (e.g. a
    missing OPENAI_API_KEY) — the point of recording a failed run is to know
    a failure happened, so this must not itself raise."""
    try:
        model = ai_model_name()
    except AIConfigError:
        model = "unconfigured"
    try:
        version = prompt_version(load_prompt(prompt_name))
    except AIConfigError:
        version = "unknown"
    return model, version


def _record_extraction_run(
    cur,
    *,
    task: str,
    subject_type: str,
    model: str,
    prompt_name: str,
    prompt_version: str,
    vocabulary_version_id: str,
    started_at: datetime,
    status: str,
    document_id: str | None = None,
    role_instance_id: str | None = None,
    profile360_claim_id: str | None = None,
    profile360_capability_id: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    input_chars: int | None = None,
    output_chars: int | None = None,
    notes: str | None = None,
) -> str:
    cur.execute(
        """
        INSERT INTO jobber.extraction_run
            (task, subject_type, document_id, role_instance_id, profile360_claim_id, profile360_capability_id,
             model, prompt_name, prompt_version, vocabulary_version_id, started_at, finished_at, status,
             error_type, error_message, input_chars, output_chars, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            task, subject_type, document_id, role_instance_id, profile360_claim_id, profile360_capability_id,
            model, prompt_name, prompt_version, vocabulary_version_id, started_at, status,
            error_type, error_message, input_chars, output_chars, notes,
        ),
    )
    return str(cur.fetchone()["id"])


def _concepts_by_ids(cur, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    cur.execute(
        "SELECT id, type_code, canonical_name, definition FROM jobber.concept WHERE id = ANY(%s::uuid[])",
        (ids,),
    )
    return [{**row, "id": str(row["id"])} for row in cur.fetchall()]


_VALID_IMPORTANCE = {1, 2, 3, 4, 5}


def extract_role_requirements(cur, role_instance_id: str) -> dict:
    """Phase A (open extraction of requirement surface forms + verbatim
    spans) + Phase B (doc 11 §7.3 canonicalisation cascade) against one
    role_instance's source document. Returns a summary dict; never raises for
    an AI failure (that is recorded and returned as status='failed') — only
    for a bad `role_instance_id` (ExtractionSubjectError, a 4xx upstream)."""
    cur.execute("SELECT id, document_id FROM jobber.role_instance WHERE id = %s", (role_instance_id,))
    role = cur.fetchone()
    if not role:
        raise ExtractionSubjectError("role_instance not found")
    if not role["document_id"]:
        raise ExtractionSubjectError(
            "this role_instance has no source document — nothing to extract requirements from"
        )

    cur.execute("SELECT id, content_text, provenance_quality FROM jobber.document WHERE id = %s", (role["document_id"],))
    document = cur.fetchone()
    document["id"] = str(document["id"])

    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    started_at = datetime.now(timezone.utc)
    model, pversion = _safe_task_metadata("extract_role_requirements.md")

    user_input = f"Source document text:\n\n{document['content_text']}"
    try:
        result = run_json_task(
            task="requirement_extract",
            prompt_name="extract_role_requirements.md",
            user_input=user_input,
            output_model=RequirementExtractionResult,
        )
    except AITaskError as e:
        run_id = _record_extraction_run(
            cur, task="requirement_extract", subject_type="role_instance",
            document_id=document["id"], role_instance_id=role_instance_id,
            model=model, prompt_name="extract_role_requirements.md", prompt_version=pversion,
            vocabulary_version_id=vocabulary_version_id, started_at=started_at, status="failed",
            error_type=type(e).__name__, error_message=str(e), input_chars=len(user_input),
        )
        return {"status": "failed", "extraction_run_id": run_id, "error": str(e)}

    # §5.2 invariant 1 / §8.3: never trust the model's claim to have quoted
    # verbatim — check it. §4/§14: a legacy-reconstructed (or otherwise
    # non-original) document can never back a stated/implied claim, however
    # good the span looks, because the text itself isn't guaranteed original.
    # provenance_quality is production's column (docs/14 §3) — 'original'
    # means a genuine verbatim capture; 'legacy_extracted'/'reconstructed'/
    # 'unknown' all downgrade to inferred with no stored span.
    can_trust_spans = document["provenance_quality"] == "original"

    # (surface_form, requirement_type, basis_to_store, span_to_store, importance, context_text)
    validated: list[tuple] = []
    rejected_span_count = 0
    for item in result.output.requirements:
        if not validate_span(document["content_text"], item.evidence_span):
            rejected_span_count += 1
            continue
        importance = item.importance if item.importance in _VALID_IMPORTANCE else None
        basis = item.basis if item.basis in ("stated", "implied") else "implied"
        if can_trust_spans:
            validated.append((item.surface_form, item.requirement_type, basis, item.evidence_span, importance, item.evidence_span))
        else:
            validated.append((item.surface_form, item.requirement_type, "inferred", None, importance, item.evidence_span))

    # Phase B cascade: exact match first (free), else collect for batched adjudication.
    item_concept: dict[int, str] = {}  # keyed by list index (int), valued by concept id (uuid str)
    to_adjudicate: list[dict] = []
    for idx, (surface_form, *_rest) in enumerate(validated):
        concept_id = exact_match_concept_id(cur, normalize_name(surface_form))
        if concept_id is not None:
            item_concept[idx] = concept_id
            continue
        candidates = _concepts_by_ids(cur, [c[0] for c in nearest_concepts(cur, surface_form, limit=10)])
        if candidates:
            to_adjudicate.append(
                {
                    "item_index": idx,
                    "surface_form": surface_form,
                    "sentence_context": validated[idx][5],
                    "candidates": candidates,
                }
            )

    adjudication_run_id = None
    if to_adjudicate:
        adj_started = datetime.now(timezone.utc)
        adj_model, adj_pversion = _safe_task_metadata("adjudicate_concept_candidates.md")
        adj_input = "Items:\n\n" + json.dumps(to_adjudicate, indent=2, default=str)
        try:
            adj_result = run_json_task(
                task="concept_link_adjudicate",
                prompt_name="adjudicate_concept_candidates.md",
                user_input=adj_input,
                output_model=ConceptAdjudicationResult,
            )
            adjudication_run_id = _record_extraction_run(
                cur, task="concept_link_adjudicate", subject_type="role_instance",
                document_id=document["id"], role_instance_id=role_instance_id,
                model=adj_model, prompt_name="adjudicate_concept_candidates.md", prompt_version=adj_pversion,
                vocabulary_version_id=vocabulary_version_id, started_at=adj_started, status="ok",
                input_chars=len(adj_input),
            )
            by_index = {u["item_index"]: u for u in to_adjudicate}
            for decision in adj_result.output.decisions:
                entry = by_index.get(decision.item_index)
                if not entry or not decision.chosen_canonical_name:
                    continue
                match = next(
                    (c for c in entry["candidates"] if c["canonical_name"] == decision.chosen_canonical_name), None
                )
                if match:
                    item_concept[decision.item_index] = match["id"]
        except AITaskError as e:
            adjudication_run_id = _record_extraction_run(
                cur, task="concept_link_adjudicate", subject_type="role_instance",
                document_id=document["id"], role_instance_id=role_instance_id,
                model=adj_model, prompt_name="adjudicate_concept_candidates.md", prompt_version=adj_pversion,
                vocabulary_version_id=vocabulary_version_id, started_at=adj_started, status="failed",
                error_type=type(e).__name__, error_message=str(e), input_chars=len(adj_input),
            )
            # Adjudication failing doesn't fail the whole extraction — items
            # simply fall through to concept_proposal below, same as a
            # declined match would.

    main_run_id = _record_extraction_run(
        cur, task="requirement_extract", subject_type="role_instance",
        document_id=document["id"], role_instance_id=role_instance_id,
        model=model, prompt_name="extract_role_requirements.md", prompt_version=pversion,
        vocabulary_version_id=vocabulary_version_id, started_at=started_at,
        status="partial" if rejected_span_count else "ok",
        input_chars=len(user_input),
        notes=f"{rejected_span_count} item(s) rejected: proposed evidence_span did not occur verbatim in the document"
        if rejected_span_count else None,
    )

    claims_created = proposals_created = proposals_updated = 0
    for idx, (surface_form, requirement_type, basis, span, importance, _context) in enumerate(validated):
        concept_id = item_concept.get(idx)
        if concept_id is not None:
            cur.execute(
                """
                INSERT INTO jobber.requirement_claim
                    (role_instance_id, concept_id, requirement_type, importance, basis,
                     document_id, evidence_span, extraction_run_id, review_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'unreviewed')
                """,
                (role_instance_id, concept_id, requirement_type, importance, basis, document["id"], span, main_run_id),
            )
            claims_created += 1
            continue

        # Unresolved vocabulary -> concept_proposal (never silently invented —
        # brief §6/§18), same convention as concept_linking.run_pass_b.
        normalized = normalize_name(surface_form)
        cur.execute(
            "SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'",
            (normalized,),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                UPDATE jobber.concept_proposal SET
                    occurrence_count = occurrence_count + 1,
                    document_id = COALESCE(document_id, %s),
                    evidence_span = COALESCE(evidence_span, %s),
                    extraction_run_id = COALESCE(extraction_run_id, %s)
                WHERE id = %s
                """,
                (document["id"], span, main_run_id, existing["id"]),
            )
            proposals_updated += 1
        else:
            nearest = nearest_concept(cur, surface_form)
            cur.execute(
                """
                INSERT INTO jobber.concept_proposal
                    (surface_form, occurrence_count, nearest_concept_id, nearest_similarity,
                     document_id, evidence_span, extraction_run_id, status)
                VALUES (%s, 1, %s, %s, %s, %s, %s, 'pending')
                """,
                (normalized, nearest[0] if nearest else None, nearest[1] if nearest else None,
                 document["id"], span, main_run_id),
            )
            proposals_created += 1

    return {
        "status": "ok",
        "extraction_run_id": main_run_id,
        "adjudication_run_id": adjudication_run_id,
        "claims_created": claims_created,
        "proposals_created": proposals_created,
        "proposals_updated": proposals_updated,
        "rejected_span_count": rejected_span_count,
    }


def _map_profile360_row(
    cur,
    *,
    row_kind: str,  # 'claim' | 'capability'
    row_id: str,
    row: dict,
    prompt_name: str,
    task: str,
    mapping_table: str,
    mapping_id_column: str,
    concept_id_column: str,
    type_codes: list[str] | None,
) -> dict:
    text = display_text(row)
    candidates = _concepts_by_ids(cur, [c[0] for c in nearest_concepts(cur, text, limit=10, type_codes=type_codes)])
    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    started_at = datetime.now(timezone.utc)
    model, pversion = _safe_task_metadata(prompt_name)

    subject_kwargs = {"profile360_claim_id": row_id} if row_kind == "claim" else {"profile360_capability_id": row_id}

    if not candidates:
        run_id = _record_extraction_run(
            cur, task=task, subject_type=f"profile360_{row_kind}",
            model=model, prompt_name=prompt_name, prompt_version=pversion,
            vocabulary_version_id=vocabulary_version_id, started_at=started_at, status="ok",
            notes="no embedding candidates retrieved — nothing to adjudicate",
            **subject_kwargs,
        )
        # Brief (docs/18 §11): until a real canonical vocabulary is curated,
        # `mapped: False` alone is ambiguous — it means the same thing
        # whether zero candidates existed to even consider, or several
        # existed and the model declined all of them. Those are different
        # claims about the evidence: the first says nothing about this
        # person's evidence at all (the *vocabulary* is the gap), the second
        # is a real "not confident" judgment against real candidates.
        # `candidates_considered`/`reason` make the distinction explicit and
        # machine-readable rather than leaving it to be inferred (or,
        # worse, conflated) from a free-text note.
        return {
            "status": "ok", "extraction_run_id": run_id, "mapped": False,
            "candidates_considered": 0, "reason": "no_candidates_available",
        }

    record_json = json.dumps(dict(row), indent=2, default=str)
    candidates_json = json.dumps(candidates, indent=2, default=str)
    user_input = f"Record:\n\n{record_json}\n\nCandidates:\n\n{candidates_json}"

    try:
        result = run_json_task(
            task=task, prompt_name=prompt_name, user_input=user_input, output_model=ClaimMappingResult,
        )
    except AITaskError as e:
        run_id = _record_extraction_run(
            cur, task=task, subject_type=f"profile360_{row_kind}",
            model=model, prompt_name=prompt_name, prompt_version=pversion,
            vocabulary_version_id=vocabulary_version_id, started_at=started_at, status="failed",
            error_type=type(e).__name__, error_message=str(e), input_chars=len(user_input),
            **subject_kwargs,
        )
        return {"status": "failed", "extraction_run_id": run_id, "error": str(e)}

    match = None
    if result.output.chosen_canonical_name:
        match = next((c for c in candidates if c["canonical_name"] == result.output.chosen_canonical_name), None)

    run_id = _record_extraction_run(
        cur, task=task, subject_type=f"profile360_{row_kind}",
        model=result.run.model, prompt_name=prompt_name, prompt_version=result.run.prompt_version,
        vocabulary_version_id=vocabulary_version_id, started_at=started_at, status="ok",
        input_chars=result.run.input_chars, output_chars=result.run.output_chars,
        notes=result.output.reasoning,
        **subject_kwargs,
    )

    if not match:
        return {
            "status": "ok", "extraction_run_id": run_id, "mapped": False,
            "candidates_considered": len(candidates), "reason": "declined_all_candidates",
        }

    cur.execute(
        f"""
        INSERT INTO jobber.{mapping_table} ({mapping_id_column}, {concept_id_column}, mapping_basis, review_status, extraction_run_id)
        VALUES (%s, %s, 'ai_suggested', 'unreviewed', %s)
        ON CONFLICT ({mapping_id_column}, {concept_id_column}) DO UPDATE SET
            extraction_run_id = EXCLUDED.extraction_run_id
        RETURNING id
        """,
        (row_id, match["id"], run_id),
    )
    mapping_id = cur.fetchone()["id"]
    return {
        "status": "ok", "extraction_run_id": run_id, "mapped": True, "mapping_id": mapping_id, "concept_id": match["id"],
        "candidates_considered": len(candidates),
    }


def map_profile360_claim(cur, claim_id: str) -> dict:
    row = get_claim(cur, claim_id)
    if row is None:
        raise ExtractionSubjectError("profile360 claim not found")
    return _map_profile360_row(
        cur, row_kind="claim", row_id=claim_id, row=row,
        prompt_name="map_profile360_claim.md", task="profile360_claim_map",
        mapping_table="profile360_claim_mapping", mapping_id_column="profile360_claim_id",
        concept_id_column="jobber_concept_id", type_codes=None,
    )


def map_profile360_claim_to_capability(cur, claim_id: str) -> dict:
    """Phase 3 Pass C (brief §23): the same closed-vocabulary mapping as
    map_profile360_claim, but the candidate list is restricted to
    capability-typed concepts only — for the case where a claim's text
    plausibly states the *whole* integrated capability, not merely an
    atomic component of one (doc 11 §3.1's "direct" evidence route: "the
    source text plainly states the whole thing"). Writes into the same
    profile360_claim_mapping table/review queue as map_profile360_claim, so
    the existing Profile360.tsx "Claims" review queue surfaces these with no
    frontend change — nothing here can become an accepted mapping without
    that human review step (brief: "Pass C output must go through review
    before it affects evidenced coverage")."""
    row = get_claim(cur, claim_id)
    if row is None:
        raise ExtractionSubjectError("profile360 claim not found")
    return _map_profile360_row(
        cur, row_kind="claim", row_id=claim_id, row=row,
        prompt_name="map_profile360_claim.md", task="capability_attribute",
        mapping_table="profile360_claim_mapping", mapping_id_column="profile360_claim_id",
        concept_id_column="jobber_concept_id", type_codes=["capability"],
    )


def run_pass_c(cur, limit: int = 25) -> dict:
    """Batch entrypoint over the profile360 claim corpus (brief §23): attempt
    capability attribution for claims that have no existing *mapping row* to
    a capability concept yet. Bounded by `limit`, safe to re-run repeatedly —
    a claim that was successfully mapped (or already has a human-reviewed
    mapping) is skipped on the next run. A claim the model *declined* writes
    no row at all (same convention as map_profile360_claim/
    map_profile360_capability — a decline is not persisted anywhere) and so
    is legitimately retried on the next run, e.g. after the catalogue grows;
    this costs one AI call per still-unmapped claim per run, not a
    correctness issue."""
    try:
        claims = list_claims(cur, limit=limit, offset=0)
    except Profile360UnavailableError as e:
        return {"status": "unavailable", "error": str(e), "attempted": 0, "mapped": 0, "failed": 0, "results": []}

    cur.execute(
        """
        SELECT DISTINCT profile360_claim_id FROM jobber.profile360_claim_mapping
        WHERE jobber_concept_id IN (SELECT id FROM jobber.concept WHERE type_code = 'capability')
        """
    )
    already_attempted = {str(r["profile360_claim_id"]) for r in cur.fetchall()}

    attempted = mapped = failed = 0
    results = []
    for claim in claims:
        claim_id = str(claim["id"])
        if claim_id in already_attempted:
            continue
        attempted += 1
        try:
            result = map_profile360_claim_to_capability(cur, claim_id)
        except ExtractionSubjectError:
            continue
        if result["status"] == "failed":
            failed += 1
        elif result.get("mapped"):
            mapped += 1
        results.append({"profile360_claim_id": claim_id, **result})

    return {"status": "ok", "attempted": attempted, "mapped": mapped, "failed": failed, "results": results}


def map_profile360_capability(cur, capability_id: str) -> dict:
    row = get_capability(cur, capability_id)
    if row is None:
        raise ExtractionSubjectError("profile360 capability not found")
    return _map_profile360_row(
        cur, row_kind="capability", row_id=capability_id, row=row,
        prompt_name="map_profile360_capability.md", task="profile360_capability_map",
        mapping_table="profile360_capability_mapping", mapping_id_column="profile360_capability_id",
        concept_id_column="jobber_capability_concept_id", type_codes=["capability"],
    )
