"""Day-in-the-Life / Role Context enrichment API (next-build brief §7).
Mounted under the same /api/roles prefix as routes/roles.py, as its own
router (same "one small dedicated router per sub-feature" precedent as
routes/role_instances.py's requirements sub-resource). Protected by the
existing central auth policy like every other router (app/main.py)."""

from fastapi import APIRouter, HTTPException

from ..db import db_cursor
from ..role_context import (
    RoleContextGenerationError,
    RoleContextSubjectError,
    generate_role_context,
    get_role_context,
    raise_for_generation_error,
)

router = APIRouter(prefix="/api/roles", tags=["role-context"])


@router.get("/{role_id}/context")
def read_role_context(role_id: str):
    """Never triggers generation (brief §7) — a pure, repeatable read. 404
    only if the role itself doesn't exist; a role with no enrichment yet
    returns 200 with `enrichment: null` so the frontend can show a
    [Generate] affordance rather than treating "nothing generated yet" as an
    error."""
    with db_cursor() as cur:
        try:
            return get_role_context(cur, role_id)
        except RoleContextSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.post("/{role_id}/context/generate")
def create_role_context(role_id: str):
    """First-time generation. A no-op (returns the existing enrichment,
    no AI call) if one is already active — regeneration is a deliberate,
    separate action (brief §6.6), never implicit here."""
    try:
        return generate_role_context(role_id, force=False)
    except RoleContextSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except RoleContextGenerationError as e:
        raise_for_generation_error(e)


@router.post("/{role_id}/context/regenerate")
def refresh_role_context(role_id: str):
    """Always calls the model and creates a new active version, superseding
    the prior one — the one deliberate action that can replace an existing
    enrichment (brief §6.6). A failure here leaves the prior active
    enrichment fully intact (role_context.py's _persist is only ever reached
    after a successful, validated model response)."""
    try:
        return generate_role_context(role_id, force=True)
    except RoleContextSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except RoleContextGenerationError as e:
        raise_for_generation_error(e)
