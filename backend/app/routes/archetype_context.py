"""Archetype Day-in-the-Life / context API (build §10), plus the archetype's
own compensation benchmark read.

Mounted under the existing /api/archetypes prefix as its own router — the
same "one small dedicated router per sub-feature" convention
routes/role_context.py already follows for roles. GET never generates;
generation and regeneration are separate explicit POSTs; there is no
bulk-generate endpoint anywhere.
"""

from fastapi import APIRouter, HTTPException

from ..archetype_context import (
    ArchetypeContextGenerationError,
    ArchetypeContextGroundingError,
    ArchetypeContextSubjectError,
    generate_archetype_context,
    get_archetype_context,
    raise_for_generation_error,
)
from ..compensation_resolver import resolve_archetype_compensation
from ..db import db_cursor

router = APIRouter(prefix="/api/archetypes", tags=["archetype-context"])


@router.get("/{archetype_id}/context")
def read_archetype_context(archetype_id: str):
    """Pure read — never triggers generation. An archetype with no
    enrichment yet returns 200 with `enrichment: null` so the UI can offer
    Generate rather than treating absence as an error."""
    with db_cursor() as cur:
        try:
            return get_archetype_context(cur, archetype_id)
        except ArchetypeContextSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.post("/{archetype_id}/context/generate")
def create_archetype_context(archetype_id: str):
    """First-time generation. A no-op returning the existing enrichment (and
    making no model call) if one is already active — regeneration is a
    separate, deliberate action."""
    try:
        return generate_archetype_context(archetype_id, force=False)
    except ArchetypeContextSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except ArchetypeContextGroundingError as e:
        raise HTTPException(400, str(e)) from e
    except ArchetypeContextGenerationError as e:
        raise_for_generation_error(e)


@router.post("/{archetype_id}/context/regenerate")
def refresh_archetype_context(archetype_id: str):
    """Always calls the model and supersedes the prior active version. A
    failure leaves that prior version fully intact."""
    try:
        return generate_archetype_context(archetype_id, force=True)
    except ArchetypeContextSubjectError as e:
        raise HTTPException(404, str(e)) from e
    except ArchetypeContextGroundingError as e:
        raise HTTPException(400, str(e)) from e
    except ArchetypeContextGenerationError as e:
        raise_for_generation_error(e)


@router.get("/{archetype_id}/compensation")
def read_archetype_compensation(archetype_id: str, market_id: str | None = None, currency: str | None = None):
    """The archetype's market benchmark through the shared resolver — never
    a second, parallel aggregation of the same evidence."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM jobber.concept WHERE id = %s AND type_code = 'role_archetype'", (archetype_id,)
        )
        if not cur.fetchone():
            raise HTTPException(404, "role_archetype not found")
        return resolve_archetype_compensation(cur, archetype_id, market_id=market_id, currency=currency)
