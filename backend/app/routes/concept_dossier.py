"""Concept Dossier API (cp_round_of_changes.md §C/§D/§E/§F/§G). Mounted
under the same /api/concepts prefix as routes/concepts.py, as its own router
— same "one small dedicated router per sub-feature" precedent as
routes/role_context.py relative to routes/roles.py. Protected by the
existing central auth policy like every other router (app/main.py)."""

from fastapi import APIRouter, HTTPException

from .. import concept_dossier as dossier
from ..db import db_cursor
from ..models import ConceptDossierGenerateRequest, ConceptDossierManualEdit

router = APIRouter(prefix="/api/concepts", tags=["concept-dossier"])


@router.get("/{concept_id}/dossier")
def read_dossier(concept_id: str):
    """Never triggers generation (§D: "Viewing a concept must NEVER trigger
    an AI call") — a pure, repeatable read. 404 only if the concept itself
    doesn't exist; a concept with no dossier yet returns 200 with
    `active: null, draft: null` so the frontend can show a [Generate]
    affordance rather than treating "nothing generated yet" as an error."""
    with db_cursor() as cur:
        try:
            return dossier.get_concept_dossier(cur, concept_id)
        except dossier.ConceptDossierSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.get("/{concept_id}/dossier/history")
def read_dossier_history(concept_id: str):
    with db_cursor() as cur:
        try:
            return dossier.list_concept_dossier_history(cur, concept_id)
        except dossier.ConceptDossierSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.post("/{concept_id}/dossier/generate")
def create_dossier(concept_id: str, payload: ConceptDossierGenerateRequest = ConceptDossierGenerateRequest()):
    """First-time generation. A no-op (returns the existing active dossier,
    no AI call) if one is already active — regeneration is a deliberate,
    separate action, never implicit here."""
    try:
        return dossier.generate_concept_dossier(concept_id, guidance=payload.guidance)
    except dossier.ConceptDossierSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except dossier.ConceptDossierGenerationError as e:
        dossier.raise_for_generation_error(e)


@router.post("/{concept_id}/dossier/regenerate")
def refresh_dossier(concept_id: str, payload: ConceptDossierGenerateRequest = ConceptDossierGenerateRequest()):
    """Always calls the model and creates/replaces a DRAFT — the active
    (curated) dossier is never touched by this call. A failure here leaves
    both the active dossier and any prior draft fully intact (the supersede+
    insert in concept_dossier.py is only ever reached after a successful,
    validated model response)."""
    try:
        return dossier.regenerate_concept_dossier(concept_id, guidance=payload.guidance)
    except dossier.ConceptDossierSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except dossier.ConceptDossierStateError as e:
        raise HTTPException(409, str(e)) from e
    except dossier.ConceptDossierGenerationError as e:
        dossier.raise_for_generation_error(e)


@router.post("/{concept_id}/dossier/adopt")
def adopt_dossier_draft(concept_id: str):
    """Promotes the current draft to active, atomically superseding the old
    active row. 409 if there is no draft to adopt."""
    try:
        return dossier.adopt_draft(concept_id)
    except dossier.ConceptDossierSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except dossier.ConceptDossierStateError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/{concept_id}/dossier/discard")
def discard_dossier_draft(concept_id: str):
    """Declines the current draft. The active dossier is untouched. 409 if
    there is no draft to discard."""
    try:
        return dossier.discard_draft(concept_id)
    except dossier.ConceptDossierSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except dossier.ConceptDossierStateError as e:
        raise HTTPException(409, str(e)) from e


@router.put("/{concept_id}/dossier")
def edit_dossier(concept_id: str, payload: ConceptDossierManualEdit):
    """A curator's direct edit of the active dossier — creates a new active
    version and supersedes the previous one rather than overwriting it in
    place, so version history is retained. 409 if there is no active dossier
    yet to edit (generate one first)."""
    try:
        return dossier.save_manual_edit(concept_id, payload.model_dump(exclude_unset=True))
    except dossier.ConceptDossierSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except dossier.ConceptDossierStateError as e:
        raise HTTPException(409, str(e)) from e
