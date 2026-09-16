"""Source-backed posting compensation extraction and review (build §2).

A narrow, explicitly-invoked flow — deliberately NOT a revival of the old
monolithic JSON posting analysis:

- Raw capture (`routes/role_instances.py::_ingest_raw`) is untouched and
  still makes no AI call. Nothing in this module runs during capture.
- `propose_posting_compensation` is an explicit action. It records an
  `extraction_run` (success or failure, same provenance convention as
  metadata enrichment and requirement extraction) and returns a **proposal**.
  It writes no `compensation_observation` row at all.
- `accept_posting_compensation` is the only writer. A human has reviewed —
  and may have edited — the proposed figures, so the row it creates is born
  `review_status='accepted'` with `basis='posting_stated'`, which is what
  makes it authoritative for the compensation resolver's tier 1.
- Rejecting is simply not accepting. Because the proposal is never persisted,
  there is nothing to clean up and no half-reviewed state to interpret later.

Server-side validation on accept is not a formality; it is the only thing
standing between an AI proposal and a stated economic fact:

1. the role exists and has a linked source document;
2. `evidence_span` occurs **verbatim** in that document's immutable
   `content_text` (`span_validation.validate_span`, the same check
   requirement claims use);
3. the document's `provenance_quality` is `'original'` — a legacy,
   reconstructed or unknown-provenance document can never back a stated
   fact, however plausible the quote looks. Weak provenance is never
   silently upgraded;
4. the currency is a three-letter code and the amounts form a sane, ordered,
   non-negative range;
5. component / pay_period / employment_basis are in the controlled sets the
   `compensation_observation` CHECK constraints already enforce — rejected
   here with a clear message rather than as a raw integrity error.

The legacy `role_instance.salary_min/salary_max/salary_estimate_*` columns
are never written or cleared by this module, so the existing 0013 backfill,
Role Detail's legacy display, and every historical role keep working exactly
as before. Where both a backfilled projection and a reviewed source-quoted
observation exist for the same role, the resolver prefers the quoted one —
see `compensation_resolver.py`.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import to_json_param
from .market import get_or_create_market_by_country
from .span_validation import validate_span

TASK = "posting_compensation_extract"
PROMPT_NAME = "extract_posting_compensation.md"

VALID_COMPONENTS = ("base", "bonus_pct", "total_package", "day_rate")
VALID_PAY_PERIODS = ("annual", "daily")
VALID_EMPLOYMENT_BASES = ("permanent", "contract", "unknown")

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class PostingCompensationSubjectError(ValueError):
    """No such role, or it has no usable source document — a 4xx at the
    route layer, never a failed AI run."""


class PostingCompensationValidationError(ValueError):
    """An accept payload that server-side validation refuses. Always carries
    a message naming the specific rule that failed."""


class PostingCompensationProposalItem(BaseModel):
    """Loose on purpose: "do not invent a missing field" means the model
    must be able to return nulls, and every field is re-validated against
    the controlled vocabulary at accept time rather than being coerced or
    dropped silently here."""

    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: Optional[str] = None
    component: Optional[str] = None
    pay_period: Optional[str] = None
    employment_basis: Optional[str] = None
    evidence_span: Optional[str] = None
    note: Optional[str] = None


class PostingCompensationProposal(BaseModel):
    items: list[PostingCompensationProposalItem] = []
    no_compensation_stated: bool = False
    notes: Optional[str] = None


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


def _load_role_and_document(cur, role_instance_id: str) -> tuple[dict, dict]:
    cur.execute(
        "SELECT id, document_id, country, currency FROM jobber.role_instance WHERE id = %s",
        (role_instance_id,),
    )
    role = cur.fetchone()
    if not role:
        raise PostingCompensationSubjectError("role_instance not found")
    if not role["document_id"]:
        raise PostingCompensationSubjectError(
            "this role has no linked source document — stated compensation can only be reviewed against a source"
        )
    cur.execute(
        "SELECT id, content_text, provenance_quality, source_date FROM jobber.document WHERE id = %s",
        (role["document_id"],),
    )
    document = cur.fetchone()
    if not document or not (document["content_text"] or "").strip():
        raise PostingCompensationSubjectError("the linked source document has no usable content_text")
    return dict(role), dict(document)


def _annotate_proposal_item(item: dict, document_text: str) -> dict:
    """Every proposal item is returned with the server's own verdict on it
    already attached, so the review UI can show "this quote was not found in
    the source" before the user clicks Accept rather than only after."""
    span = (item.get("evidence_span") or "").strip()
    problems = []
    if not span:
        problems.append("no evidence span was quoted")
    elif not validate_span(document_text, span):
        problems.append("the quoted evidence span does not appear verbatim in the source document")
    if item.get("currency") and not _CURRENCY_RE.match(item["currency"].strip().upper()):
        problems.append(f"{item['currency']!r} is not a three-letter currency code")
    if not item.get("currency"):
        problems.append("no currency was stated")
    if item.get("component") not in VALID_COMPONENTS:
        problems.append(f"component must be one of {list(VALID_COMPONENTS)}")
    if item.get("pay_period") not in VALID_PAY_PERIODS:
        problems.append(f"pay_period must be one of {list(VALID_PAY_PERIODS)}")
    if item.get("amount_min") is None and item.get("amount_max") is None:
        problems.append("no amount was stated")
    return {**item, "acceptable": not problems, "problems": problems}


def propose_posting_compensation(cur, role_instance_id: str) -> dict:
    """Explicit, on-demand extraction of *stated* compensation from one
    role's own source document. Records the run; never writes an
    observation. The returned `proposal.items` each carry the server's
    validation verdict (see `_annotate_proposal_item`) — the same rules
    `accept_posting_compensation` re-applies, so the review screen can never
    offer an Accept that the server will then refuse."""
    role, document = _load_role_and_document(cur, role_instance_id)
    document_id = str(document["id"])
    content_text = document["content_text"]
    started_at = datetime.now(timezone.utc)
    model, pversion = _safe_task_metadata()

    try:
        result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=content_text,
            output_model=PostingCompensationProposal,
        )
    except AITaskError as e:
        run_id = _record_run(
            cur, role_instance_id=role_instance_id, document_id=document_id, model=model, pversion=pversion,
            started_at=started_at, status="failed", input_chars=len(content_text), output_payload=None,
            error_type=type(e).__name__, error_message=str(e),
        )
        return {
            "status": "failed", "extraction_run_id": run_id, "error": str(e),
            "error_type": type(e).__name__, "proposal": None,
        }

    payload = result.output.model_dump()
    payload["items"] = [_annotate_proposal_item(i, content_text) for i in payload["items"]]
    run_id = _record_run(
        cur, role_instance_id=role_instance_id, document_id=document_id, model=result.run.model,
        pversion=result.run.prompt_version, started_at=started_at, status="ok",
        input_chars=result.run.input_chars, output_chars=result.run.output_chars, output_payload=payload,
    )
    return {
        "status": "ok", "extraction_run_id": run_id, "error": None, "error_type": None,
        "proposal": payload, "document_id": document_id,
        "provenance_quality": document["provenance_quality"],
    }


def _record_run(
    cur, *, role_instance_id: str, document_id: str, model: str, pversion: str, started_at: datetime,
    status: str, input_chars: int, output_chars: int | None = None, output_payload: dict | None = None,
    error_type: str | None = None, error_message: str | None = None,
) -> str:
    cur.execute(
        """
        INSERT INTO jobber.extraction_run
            (task, subject_type, document_id, role_instance_id, model, prompt_name, prompt_version,
             started_at, finished_at, status, input_chars, output_chars, output_payload,
             error_type, error_message)
        VALUES (%s, 'role_instance', %s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            TASK, document_id, role_instance_id, model, PROMPT_NAME, pversion, started_at, status,
            input_chars, output_chars, to_json_param(output_payload), error_type, error_message,
        ),
    )
    return str(cur.fetchone()["id"])


def _validate_accept(item: dict, document: dict) -> dict:
    """Every rule from this module's docstring, applied server-side to the
    (possibly user-edited) payload. Raises with the first failing rule
    named."""
    if document["provenance_quality"] != "original":
        raise PostingCompensationValidationError(
            "this role's source document does not have original provenance, so it cannot back a stated "
            "compensation fact — record a curator-asserted observation on the Economics page instead"
        )

    span = (item.get("evidence_span") or "").strip()
    if not span:
        raise PostingCompensationValidationError(
            "an exact evidence span quoted from the source document is required"
        )
    if not validate_span(document["content_text"], span):
        raise PostingCompensationValidationError(
            "evidence_span is not an exact match in the source document"
        )

    currency = (item.get("currency") or "").strip().upper()
    if not _CURRENCY_RE.match(currency):
        raise PostingCompensationValidationError("currency must be a three-letter ISO code, e.g. GBP")

    component = item.get("component")
    if component not in VALID_COMPONENTS:
        raise PostingCompensationValidationError(f"component must be one of {list(VALID_COMPONENTS)}")
    pay_period = item.get("pay_period")
    if pay_period not in VALID_PAY_PERIODS:
        raise PostingCompensationValidationError(f"pay_period must be one of {list(VALID_PAY_PERIODS)}")
    employment_basis = item.get("employment_basis")
    if employment_basis is not None and employment_basis not in VALID_EMPLOYMENT_BASES:
        raise PostingCompensationValidationError(
            f"employment_basis must be one of {list(VALID_EMPLOYMENT_BASES)} or omitted"
        )

    amount_min, amount_max = item.get("amount_min"), item.get("amount_max")
    if amount_min is None and amount_max is None:
        raise PostingCompensationValidationError("at least one of amount_min / amount_max is required")
    for label, value in (("amount_min", amount_min), ("amount_max", amount_max)):
        if value is not None and value < 0:
            raise PostingCompensationValidationError(f"{label} cannot be negative")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise PostingCompensationValidationError("amount_min cannot be greater than amount_max")

    return {
        "evidence_span": span,
        "currency": currency,
        "component": component,
        "pay_period": pay_period,
        "employment_basis": employment_basis,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "source_note": item.get("note"),
    }


def _source_key(role_instance_id: str, validated: dict) -> str:
    """Deterministic and content-addressed, so accepting the same reviewed
    figure twice (a double-click, a retried request) is idempotent rather
    than creating a duplicate economic fact — and so a genuinely *different*
    reviewed figure on the same role still gets its own row. Deliberately
    distinct from the 0013 backfill's `posting_stated:{role_id}` key, so a
    reviewed acceptance and a mechanical projection never collide."""
    digest = hashlib.sha256(
        "|".join(
            str(validated[k])
            for k in ("component", "pay_period", "currency", "amount_min", "amount_max", "evidence_span")
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"posting_stated_reviewed:{role_instance_id}:{digest}"


def accept_posting_compensation(cur, role_instance_id: str, item: dict) -> dict:
    """Create the accepted, source-backed `posting_stated` observation.

    `item` is whatever the reviewer confirmed — the AI proposal unchanged
    (Accept) or with corrected figures/span (Edit & accept). Both take
    exactly the same validation path; an edited item gets no easier ride
    than a proposed one, and the span it claims must still be in the source.

    The market is derived from the role's own country through `market.py`'s
    documented alias map, and left NULL when the country cannot be safely
    resolved — never guessed, exactly as the backfill does."""
    role, document = _load_role_and_document(cur, role_instance_id)
    validated = _validate_accept(item, document)
    market_id = get_or_create_market_by_country(cur, role["country"])
    source_key = _source_key(role_instance_id, validated)

    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, role_instance_id, document_id, market_id, component, pay_period, employment_basis,
             currency, amount_min, amount_max, basis, review_status, observed_at, evidence_span,
             source_note, reviewed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'posting_stated', 'accepted', %s, %s, %s, now())
        ON CONFLICT (source_key) DO UPDATE SET
            review_status = 'accepted', reviewed_at = now()
        RETURNING id, (xmax = 0) AS inserted
        """,
        (
            source_key, role_instance_id, str(document["id"]), market_id, validated["component"],
            validated["pay_period"], validated["employment_basis"], validated["currency"],
            validated["amount_min"], validated["amount_max"], document["source_date"],
            validated["evidence_span"], validated["source_note"],
        ),
    )
    row = cur.fetchone()
    return {
        "id": str(row["id"]),
        "created": bool(row["inserted"]),
        "status": "accepted",
        "market_id": market_id,
        "market_unassigned_reason": (
            None if market_id else
            "this role's country could not be resolved to a single market, so the observation is unassigned"
        ),
        "basis": "posting_stated",
        "review_status": "accepted",
    }


def list_role_compensation_observations(cur, role_instance_id: str) -> list[dict]:
    """Every compensation observation attached to this role, at any review
    status — the review surface's own list, distinct from what the resolver
    consumes (accepted only)."""
    cur.execute(
        """
        SELECT co.id, co.basis, co.review_status, co.component, co.pay_period, co.employment_basis,
               co.currency, co.amount_min, co.amount_mid, co.amount_max, co.evidence_span, co.observed_at,
               co.source_note, co.market_id, m.label AS market_label, co.created_at, co.reviewed_at
        FROM jobber.compensation_observation co
        LEFT JOIN jobber.market m ON m.id = co.market_id
        WHERE co.role_instance_id = %s
        ORDER BY co.created_at DESC
        """,
        (role_instance_id,),
    )
    rows = []
    for row in cur.fetchall():
        row = dict(row)
        row["id"] = str(row["id"])
        row["market_id"] = str(row["market_id"]) if row["market_id"] else None
        rows.append(row)
    return rows
