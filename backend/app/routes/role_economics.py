"""Per-role compensation and archetype review endpoints (build §2/§3/§4/§5/§14).

Mounted under /api/role-instances alongside routes/role_instances.py, as its
own router — the same convention role_context.py uses for roles. Split out
rather than appended to role_instances.py because that module is already the
requirement-review surface and these are a separate review workflow with
separate rules.

Nothing here runs during raw capture. Every AI-backed endpoint below
(`compensation/propose`, `archetype/propose`) is an explicit POST the user
triggers; every GET is a pure read that makes no model call.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..archetype_classification import (
    ArchetypeAssignmentError,
    ArchetypeClassificationSubjectError,
    NoArchetypeCatalogueError,
    active_archetype_catalogue,
    assign_archetype,
    propose_archetype,
    role_archetype_summary,
)
from ..compensation_resolver import compare_to_personal_earnings, resolve_role_compensation
from ..db import db_cursor
from ..personal_earnings import safe_personal_earnings_state
from ..posting_compensation import (
    PostingCompensationSubjectError,
    PostingCompensationValidationError,
    accept_posting_compensation,
    list_role_compensation_observations,
    propose_posting_compensation,
)

router = APIRouter(prefix="/api/role-instances", tags=["role-economics"])

# Same mapping every other AI-backed route in this app uses.
_AI_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


class CompensationAcceptInput(BaseModel):
    """What the reviewer confirmed — the AI proposal unchanged (Accept), or
    with corrected figures/quote (Edit & accept). Both take exactly the same
    server-side validation path in app/posting_compensation.py; an edited
    item gets no easier ride than a proposed one."""

    amount_min: float | None = None
    amount_max: float | None = None
    currency: str = Field(min_length=3, max_length=3)
    component: str
    pay_period: str
    employment_basis: str | None = None
    evidence_span: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


class ArchetypeAssignInput(BaseModel):
    """`archetype_concept_id = None` is "leave unclassified" — an explicit,
    valid review outcome, not a missing value."""

    archetype_concept_id: str | None = None


# --- Compensation (build §2/§3/§4) -----------------------------------------

@router.get("/{role_id}/compensation")
def read_role_compensation(role_id: str, market_id: str | None = None, currency: str | None = None):
    """Role/Target Detail's Compensation and Your-comparison sections in one
    read: the resolved figure with its basis, every compensation observation
    attached to this role (at any review status, for the review surface), and
    the comparison against the user's own earnings — which is honestly
    `comparable: false` with a stated reason whenever the two are not
    like-for-like."""
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "role_instance not found")
        resolved = resolve_role_compensation(cur, role_id, market_id=market_id, currency=currency)
        earnings = safe_personal_earnings_state(cur)
        return {
            "role_instance_id": role_id,
            "compensation": resolved,
            "personal_comparison": compare_to_personal_earnings(resolved, earnings),
            "personal_earnings": earnings,
            "observations": list_role_compensation_observations(cur, role_id),
        }


@router.post("/{role_id}/compensation/propose")
def propose_compensation(role_id: str):
    """Explicit, on-demand extraction of *stated* compensation from this
    role's own source document. Records an extraction_run for provenance and
    writes no compensation observation — the result is a proposal, and each
    item arrives with the server's own validation verdict already attached."""
    with db_cursor() as cur:
        try:
            result = propose_posting_compensation(cur, role_id)
        except PostingCompensationSubjectError as e:
            raise HTTPException(404, str(e)) from e
    if result["status"] == "failed":
        raise HTTPException(_AI_ERROR_STATUS.get(result["error_type"], 502), result["error"])
    return result


@router.post("/{role_id}/compensation/accept")
def accept_compensation(role_id: str, payload: CompensationAcceptInput):
    """The only writer of a reviewed `posting_stated` observation. Rejecting
    a proposal needs no endpoint: the proposal was never persisted, so
    declining it simply leaves nothing behind."""
    with db_cursor() as cur:
        try:
            return accept_posting_compensation(cur, role_id, payload.model_dump())
        except PostingCompensationSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except PostingCompensationValidationError as e:
            raise HTTPException(400, str(e)) from e


# --- Archetype review (build §5) --------------------------------------------

@router.get("/{role_id}/archetype")
def read_role_archetype(role_id: str):
    with db_cursor() as cur:
        try:
            return role_archetype_summary(cur, role_id)
        except ArchetypeClassificationSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.get("/archetype-catalogue")
def read_archetype_catalogue():
    """The active archetypes a reviewer may choose from — the manual
    search/select affordance's source. Declared before /{role_id}/... routes
    cannot shadow it because its first path segment is a literal, but it is
    kept adjacent to its consumers deliberately."""
    with db_cursor() as cur:
        return active_archetype_catalogue(cur)


@router.post("/{role_id}/archetype/propose")
def propose_role_archetype(role_id: str):
    """Ask the model to pick from the existing active catalogue. Writes
    nothing to the role; a suggestion outside the catalogue is reported as
    unmatched rather than acted on, and no archetype is ever created."""
    with db_cursor() as cur:
        try:
            result = propose_archetype(cur, role_id)
        except ArchetypeClassificationSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except NoArchetypeCatalogueError as e:
            raise HTTPException(400, str(e)) from e
    if result["status"] == "failed":
        raise HTTPException(_AI_ERROR_STATUS.get(result["error_type"], 502), result["error"])
    return result


@router.put("/{role_id}/archetype")
def put_role_archetype(role_id: str, payload: ArchetypeAssignInput):
    """Accept the proposal, choose another, or leave unclassified — all three
    are the same explicit human action through this one endpoint."""
    with db_cursor() as cur:
        try:
            return assign_archetype(cur, role_id, payload.archetype_concept_id)
        except ArchetypeClassificationSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except ArchetypeAssignmentError as e:
            raise HTTPException(400, str(e)) from e
