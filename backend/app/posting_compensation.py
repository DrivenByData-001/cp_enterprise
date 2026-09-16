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
- Rejecting a *proposal* is simply not accepting it. Because a proposal is
  never persisted, declining one leaves nothing behind.
- Rejecting, correcting or re-accepting an *accepted* posting-stated
  observation goes through this module's own role-aware functions
  (`reject_role_observation`, `correct_role_observation`,
  `reaccept_role_observation`), not the generic market-data review/PATCH
  endpoints. That distinction matters: the generic endpoints simply flip
  `review_status` or mutate columns in place, which would let a re-accept
  recreate two accepted base salaries, and let a PATCH alter a source-backed
  fact without re-checking the span or the document's provenance. Everything
  that can make a posting-stated observation accepted enforces the same two
  invariants — full validation against the immutable source, and
  supersession of any other accepted figure for the same
  (component, pay_period) — so those invariants cannot be reached around.

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
4. the value shape matches the component: an amount range in a stated
   currency for base / day_rate / total_package, and a percentage in
   `bonus_pct` with no cash amount for a bonus (the schema's `currency`
   column is NOT NULL, so a bonus borrows the currency of the pay it applies
   to, read from the role's own evidence and never guessed);
5. **every submitted figure is corroborated by the numbers in that span**
   (`_corroboration_problems`). Rules 2 and 4 together prove the quote is
   real and the shape is sane, and still say nothing about whether the
   amounts belong to the quote: £999,999 carrying a genuine
   "£120,000 - £145,000 per annum" span satisfies both, and would be stored
   as a stated fact. A `posting_stated` figure has to be a figure the advert
   states, so it is matched against the numbers the span actually contains;
6. component / pay_period / employment_basis are in the controlled sets the
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
from decimal import Decimal, InvalidOperation
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
    # A bonus expressed as a proportion of pay belongs here, in the column
    # the schema already has for it — never squeezed into an amount field
    # with an invented currency.
    bonus_pct: Optional[float] = None
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


def _role_currency(cur, role_instance_id: str, role: dict) -> str | None:
    """The currency of this role's pay, for a bonus percentage that has none
    of its own. Taken from what the role already records — an accepted
    compensation observation first, then the legacy `role_instance.currency`
    column — never guessed from the country. Returns None when the role has
    no currency anywhere, which makes a bonus-only item unacceptable rather
    than inventing one."""
    cur.execute(
        "SELECT currency FROM jobber.compensation_observation "
        "WHERE role_instance_id = %s AND review_status = 'accepted' AND component != 'bonus_pct' "
        "ORDER BY (evidence_span IS NULL), reviewed_at DESC NULLS LAST, id LIMIT 1",
        (role_instance_id,),
    )
    row = cur.fetchone()
    if row and row["currency"]:
        return row["currency"].strip().upper()
    return (role.get("currency") or "").strip().upper() or None


def _value_problems(item: dict, *, fallback_currency: str | None) -> list[str]:
    """The component-specific shape rules, shared by the proposal annotator
    and by acceptance so the two can never disagree.

    `bonus_pct` is a percentage of pay, not a sum of money: it carries a
    percentage and no amount, and needs a currency only because the schema's
    `currency` column is NOT NULL — that currency is the currency of the pay
    the bonus applies to, taken from the role's own evidence rather than
    invented here."""
    problems: list[str] = []
    component = item.get("component")
    if component not in VALID_COMPONENTS:
        return [f"component must be one of {list(VALID_COMPONENTS)}"]
    if item.get("pay_period") not in VALID_PAY_PERIODS:
        problems.append(f"pay_period must be one of {list(VALID_PAY_PERIODS)}")

    if component == "bonus_pct":
        percent = item.get("bonus_pct")
        if percent is None:
            problems.append("a bonus must state its percentage in bonus_pct")
        elif not (0 < percent <= 100):
            problems.append("bonus_pct must be a percentage between 0 and 100")
        if item.get("amount_min") is not None or item.get("amount_max") is not None:
            problems.append("a bonus percentage must not also carry a cash amount")
        currency = (item.get("currency") or fallback_currency or "").strip().upper()
        if not currency:
            problems.append(
                "a bonus percentage can only be recorded alongside the currency of the pay it applies to, "
                "and this role has none"
            )
        elif not _CURRENCY_RE.match(currency):
            problems.append(f"{currency!r} is not a three-letter currency code")
        return problems

    currency = (item.get("currency") or "").strip().upper()
    if not currency:
        problems.append("no currency was stated")
    elif not _CURRENCY_RE.match(currency):
        problems.append(f"{item['currency']!r} is not a three-letter currency code")
    if item.get("bonus_pct") is not None:
        problems.append("bonus_pct only applies to a bonus_pct component")

    amount_min, amount_max = item.get("amount_min"), item.get("amount_max")
    if amount_min is None and amount_max is None:
        problems.append("no amount was stated")
    for label, value in (("amount_min", amount_min), ("amount_max", amount_max)):
        if value is not None and value < 0:
            problems.append(f"{label} cannot be negative")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        problems.append("amount_min cannot be greater than amount_max")
    return problems


# --- Corroborating a figure against the quote that backs it ------------------
#
# `validate_span` proves the *quote* is real. It says nothing about whether the
# numbers submitted alongside it are the numbers that quote states, and neither
# does `_value_problems`, which only checks shape. Between them an accept of
# £999,999 carrying a perfectly genuine "£120,000 - £145,000 per annum" span
# passed every rule and was stored with `basis='posting_stated'` — the tier the
# compensation resolver treats as fact, that feeds the archetype benchmark and
# the user's personal comparison.
#
# So the figures are matched against the numbers the span actually contains.
# The parsing below is deliberately narrow: it reads what adverts write
# (£120,000, 120000, £120k, £1.2m, "£120-145k", "up to 15%") and nothing more.
# When a reviewer's figure is not in the quote the answer is to refuse, not to
# guess which number was meant — and the refusal names the curator-asserted
# pathway, because a figure that is the reviewer's own judgement rather than
# the advert's is a legitimate thing to record, just not as `posting_stated`.

_SCALE_SUFFIXES = {"k": Decimal(1_000), "m": Decimal(1_000_000)}

# A comma-grouped or plain decimal number. The comma form leads the
# alternation so "120,000" can never be read as "120".
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# A scale suffix counts only when attached to its number ("120k", "1.2 m") and
# not followed by more letters, so the "m" of "monthly" is not a million.
_NUMBER_RE = re.compile(
    rf"(?<![\d.,])(?P<number>{_NUMBER})(?:\s?(?P<suffix>[kKmM])(?![a-zA-Z]))?(?![\d,])"
)

# "£120-145k" states £120,000-£145,000, not £120. The lower bound of a range
# inherits the suffix its upper bound carries, and that reading *replaces* the
# bare one rather than joining it — 120 is not a figure that span states.
_SHARED_SCALE_RE = re.compile(
    rf"(?<![\d.,])(?P<lead>{_NUMBER})\s*(?:-|–|—|to)\s*[^\d\s]{{0,3}}\s*"
    rf"(?P<second>{_NUMBER})\s?(?P<suffix>[kKmM])(?![a-zA-Z])"
)

_PERCENT_RE = re.compile(r"\s*(?:%|percent|per cent|pct)", re.IGNORECASE)


def _scaled(number: str, suffix: str | None) -> Decimal:
    value = Decimal(number.replace(",", ""))
    return value * _SCALE_SUFFIXES[suffix.lower()] if suffix else value


def _as_decimal(value) -> Decimal | None:
    """The submitted figure as an exact Decimal, or None if it will not
    parse. A value that cannot be read is *not* corroborated — the callers
    below refuse it rather than skipping the check, because "we could not
    tell" must never resolve to "accepted" in a validation layer."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _is_percentage(text: str, at: int) -> bool:
    """True when the number ending at `at` is written as a percentage — so
    "15" in "a bonus of up to 15%" is a percentage and never an amount."""
    return _PERCENT_RE.match(text, at) is not None


def _amounts_in_span(span: str) -> set[Decimal]:
    inherited = {
        match.start("lead"): _scaled(match.group("lead"), match.group("suffix"))
        for match in _SHARED_SCALE_RE.finditer(span)
    }
    amounts: set[Decimal] = set()
    for match in _NUMBER_RE.finditer(span):
        if _is_percentage(span, match.end()):
            continue
        start = match.start("number")
        amounts.add(
            inherited[start] if start in inherited
            else _scaled(match.group("number"), match.group("suffix"))
        )
    return amounts


def _percentages_in_span(span: str) -> set[Decimal]:
    return {
        _scaled(match.group("number"), None)
        for match in _NUMBER_RE.finditer(span)
        if _is_percentage(span, match.end())
    }


def _figure(value) -> str:
    try:
        return _canonical_amount(value)
    except (TypeError, ValueError):
        return repr(value)


def _unsupported(label: str, value, stated: set[Decimal], *, unit: str = "") -> str:
    found = ", ".join(f"{_canonical_amount(v)}{unit}" for v in sorted(stated)) or "no such figure"
    return (
        f"{label} {_figure(value)}{unit} is not stated by the quoted evidence span, which "
        f"states {found} — a posting-stated figure has to be a figure the advert gives. Quote the "
        "passage this number comes from, or, if it is your own judgement rather than the advert's, "
        "record it as a curator-asserted observation on the Economics page instead"
    )


def _corroboration_problems(item: dict, span: str) -> list[str]:
    """Each supplied figure must be represented by the span, after the
    normalisation above. Runs only once the span is known verbatim and the
    value shape is known good, so its message is never one of several."""
    if item.get("component") == "bonus_pct":
        raw = item.get("bonus_pct")
        if raw is None:
            return []
        percent = _as_decimal(raw)
        stated = _percentages_in_span(span)
        if percent is None or percent not in stated:
            return [_unsupported("bonus_pct", raw, stated, unit="%")]
        return []

    stated = _amounts_in_span(span)
    problems = []
    for label in ("amount_min", "amount_max"):
        raw = item.get(label)
        if raw is None:
            continue
        value = _as_decimal(raw)
        if value is None or value not in stated:
            problems.append(_unsupported(label, raw, stated))
    return problems


def _annotate_proposal_item(item: dict, document_text: str, *, fallback_currency: str | None) -> dict:
    """Every proposal item is returned with the server's own verdict on it
    already attached, so the review UI can show "this quote was not found in
    the source" before the user clicks Accept rather than only after."""
    span = (item.get("evidence_span") or "").strip()
    quoted = False
    problems = []
    if not span:
        problems.append("no evidence span was quoted")
    elif not validate_span(document_text, span):
        problems.append("the quoted evidence span does not appear verbatim in the source document")
    else:
        quoted = True
    value_problems = _value_problems(item, fallback_currency=fallback_currency)
    problems += value_problems
    if quoted and not value_problems:
        # Same rule, same order as acceptance — a proposal whose figures are
        # not in its own quote is shown as unacceptable on the review screen
        # rather than discovered at the moment the user clicks Accept.
        problems += _corroboration_problems(item, span)
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
    fallback_currency = _role_currency(cur, role_instance_id, role)
    payload["items"] = [
        _annotate_proposal_item(i, content_text, fallback_currency=fallback_currency)
        for i in payload["items"]
    ]
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


def _validate_accept(item: dict, document: dict, *, fallback_currency: str | None) -> dict:
    """Every rule from this module's docstring, applied server-side to the
    (possibly user-edited) payload. Raises with the first failing rule
    named. Shares `_value_problems` and `_corroboration_problems` with the
    proposal annotator, so the review screen's verdict and acceptance can
    never disagree.

    Order matters: the span is proved verbatim, then the value shape, then
    the figures against the span. Corroborating an amount against a quote
    that is not in the document, or against a bonus that wrongly carries a
    cash amount, would only produce a confusing second complaint about a
    payload already known to be wrong."""
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

    employment_basis = item.get("employment_basis")
    if employment_basis is not None and employment_basis not in VALID_EMPLOYMENT_BASES:
        raise PostingCompensationValidationError(
            f"employment_basis must be one of {list(VALID_EMPLOYMENT_BASES)} or omitted"
        )

    problems = _value_problems(item, fallback_currency=fallback_currency)
    if problems:
        raise PostingCompensationValidationError(problems[0])

    problems = _corroboration_problems(item, span)
    if problems:
        raise PostingCompensationValidationError(problems[0])

    component = item["component"]
    is_bonus = component == "bonus_pct"
    currency = (item.get("currency") or (fallback_currency if is_bonus else "") or "").strip().upper()
    return {
        "evidence_span": span,
        "currency": currency,
        "component": component,
        "pay_period": item["pay_period"],
        "employment_basis": employment_basis,
        # A bonus carries its percentage and no amount; everything else
        # carries amounts and no percentage. Never both.
        "amount_min": None if is_bonus else item.get("amount_min"),
        "amount_max": None if is_bonus else item.get("amount_max"),
        "bonus_pct": item.get("bonus_pct") if is_bonus else None,
        "source_note": item.get("note"),
    }


def _canonical_amount(value) -> str:
    """One spelling per number, so the content-addressed key below is stable.

    The same figure legitimately arrives as an int from a JSON payload, a
    float from a Pydantic model, and a Decimal read back off the database —
    and `str()` spells those three differently (`120000`, `120000.0`,
    `Decimal('120000')`). Without normalising, re-submitting an unchanged
    figure would hash to a new key and create a duplicate accepted row
    instead of being the no-op it is."""
    if value is None:
        return ""
    normalised = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return normalised or "0"


def _source_key(role_instance_id: str, validated: dict) -> str:
    """Deterministic and content-addressed, so accepting the same reviewed
    figure twice (a double-click, a retried request, a correction that
    changes nothing) is idempotent rather than creating a duplicate economic
    fact — and so a genuinely *different* reviewed figure on the same role
    still gets its own row. Deliberately distinct from the 0013 backfill's
    `posting_stated:{role_id}` key, so a reviewed acceptance and a mechanical
    projection never collide."""
    parts = [
        str(validated["component"]),
        str(validated["pay_period"]),
        str(validated["currency"]),
        _canonical_amount(validated["amount_min"]),
        _canonical_amount(validated["amount_max"]),
        _canonical_amount(validated["bonus_pct"]),
        str(validated["evidence_span"]),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
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
    fallback_currency = _role_currency(cur, role_instance_id, role)
    validated = _validate_accept(item, document, fallback_currency=fallback_currency)
    market_id = get_or_create_market_by_country(cur, role["country"])
    source_key = _source_key(role_instance_id, validated)

    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, role_instance_id, document_id, market_id, component, pay_period, employment_basis,
             currency, amount_min, amount_max, bonus_pct, basis, review_status, observed_at, evidence_span,
             source_note, reviewed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'posting_stated', 'accepted', %s, %s, %s, now())
        ON CONFLICT (source_key) DO UPDATE SET
            review_status = 'accepted', reviewed_at = now()
        RETURNING id, (xmax = 0) AS inserted
        """,
        (
            source_key, role_instance_id, str(document["id"]), market_id, validated["component"],
            validated["pay_period"], validated["employment_basis"], validated["currency"],
            validated["amount_min"], validated["amount_max"], validated["bonus_pct"], document["source_date"],
            validated["evidence_span"], validated["source_note"],
        ),
    )
    row = cur.fetchone()
    observation_id = str(row["id"])
    superseded = _supersede_prior_accepted(cur, role_instance_id, observation_id, validated)
    return {
        "id": observation_id,
        "created": bool(row["inserted"]),
        "status": "accepted",
        "market_id": market_id,
        "market_unassigned_reason": (
            None if market_id else
            "this role's country could not be resolved to a single market, so the observation is unassigned"
        ),
        "basis": "posting_stated",
        "review_status": "accepted",
        "superseded_observation_ids": superseded,
    }


def _supersede_prior_accepted(cur, role_instance_id: str, observation_id: str, validated: dict) -> list[str]:
    """Accepting a corrected figure retires the one it corrects.

    Without this, correcting a mistake left *two* accepted `posting_stated`
    observations for the same (component, pay_period) on one role — both
    feeding the archetype benchmark aggregation, so the wrong figure kept
    influencing the market even after the user had fixed it, and which one
    surfaced as the headline depended on ordering.

    The retired row is marked `rejected` (the existing review vocabulary —
    no new state, and the same statuses the Market Data review lifecycle
    already uses) and annotated with what replaced it. It is never deleted:
    the audit trail keeps showing that the figure was accepted before a human
    corrected it, exactly as a superseded requirement claim does."""
    cur.execute(
        """
        UPDATE jobber.compensation_observation
        SET review_status = 'rejected', reviewed_at = now(),
            source_note = COALESCE(source_note || ' | ', '') || %s
        WHERE role_instance_id = %s AND id != %s
          AND basis = 'posting_stated' AND review_status = 'accepted'
          AND component = %s AND pay_period = %s
        RETURNING id
        """,
        (
            f"superseded by reviewed observation {observation_id}",
            role_instance_id, observation_id, validated["component"], validated["pay_period"],
        ),
    )
    return [str(r["id"]) for r in cur.fetchall()]


def list_role_compensation_observations(cur, role_instance_id: str) -> list[dict]:
    """Every compensation observation attached to this role, at any review
    status — the review surface's own list, distinct from what the resolver
    consumes (accepted only)."""
    cur.execute(
        """
        SELECT co.id, co.basis, co.review_status, co.component, co.pay_period, co.employment_basis,
               co.currency, co.amount_min, co.amount_mid, co.amount_max, co.bonus_pct, co.evidence_span,
               co.observed_at, co.source_note, co.market_id, m.label AS market_label,
               co.created_at, co.reviewed_at
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


# --- Correcting an already-accepted observation ----------------------------
#
# Everything below shares `_validate_accept` and `_supersede_prior_accepted`
# with initial acceptance, deliberately. An accepted `posting_stated` row is a
# stated economic fact backed by a verbatim quote; any path that can change
# its figures, or make it accepted again, has to re-establish the same
# guarantees. The generic market-data review/PATCH endpoints cannot: they flip
# a status or set columns without re-reading the source document, so using
# them here would let a re-accept produce two accepted base salaries and let a
# correction quietly detach a figure from the quote that justified it.


class PostingCompensationObservationError(ValueError):
    """The observation does not exist on this role, or is not a
    posting-stated row this module owns — a 404/400 at the route layer."""


def _load_role_observation(cur, role_instance_id: str, observation_id: str) -> dict:
    cur.execute(
        "SELECT id, role_instance_id, basis, review_status, component, pay_period, employment_basis, "
        "       currency, amount_min, amount_max, bonus_pct, evidence_span, source_note "
        "FROM jobber.compensation_observation WHERE id = %s AND role_instance_id = %s",
        (observation_id, role_instance_id),
    )
    row = cur.fetchone()
    if not row:
        raise PostingCompensationObservationError("compensation observation not found on this role")
    if row["basis"] != "posting_stated":
        raise PostingCompensationObservationError(
            "only a posting-stated observation is corrected here — a survey or curator-asserted row is "
            "reviewed on the Economics page"
        )
    return dict(row)


def _observation_as_item(row: dict, overrides: dict | None = None) -> dict:
    """The stored row in the same shape `_validate_accept` takes, so a
    correction and an initial acceptance are validated by identical code."""
    item = {
        "amount_min": float(row["amount_min"]) if row["amount_min"] is not None else None,
        "amount_max": float(row["amount_max"]) if row["amount_max"] is not None else None,
        "bonus_pct": float(row["bonus_pct"]) if row["bonus_pct"] is not None else None,
        "currency": row["currency"],
        "component": row["component"],
        "pay_period": row["pay_period"],
        "employment_basis": row["employment_basis"],
        "evidence_span": row["evidence_span"],
        "note": row["source_note"],
    }
    for key, value in (overrides or {}).items():
        item[key] = value
    return item


def correct_role_observation(cur, role_instance_id: str, observation_id: str, patch: dict) -> dict:
    """Correct an accepted (or previously rejected) posting-stated figure,
    **without destroying the value it replaces**.

    A correction is a new accepted observation plus the retirement of the old
    one — never an in-place rewrite. Overwriting the row would give the right
    current answer while erasing the fact that a different figure was once
    accepted as source-backed financial evidence, which is exactly what this
    codebase's curation model refuses to do everywhere else (see
    `routes/role_instances.py::_supersede_with_new_claim` for the same
    non-destructive supersession on requirement claims). After a correction
    the history reads: "£120,000–£145,000 was accepted, then corrected to
    £125,000–£150,000", with both rows still present.

    `patch` carries only the fields the reviewer changed; everything else is
    read back off the stored row. The result is re-validated in full — the
    span must still occur verbatim in the role's immutable source document,
    the document's provenance must still be original, the value shape must
    still match the component, and the corrected figures must be stated by
    the span — so a correction can never detach a stated fact from the quote
    that justifies it, or turn a bonus percentage into a cash amount.

    That last rule is what makes a correction re-anchor rather than drift:
    changing the amounts without changing the quote is refused unless the
    quote already states the new numbers, so correcting a figure to one from
    a different passage means quoting that passage. A figure the advert does
    not state anywhere cannot be reached from here at all — it is not a
    posting-stated fact, whatever the reviewer believes about the role."""
    role, document = _load_role_and_document(cur, role_instance_id)
    stored = _load_role_observation(cur, role_instance_id, observation_id)
    item = _observation_as_item(stored, patch)

    # Reuses acceptance wholesale: same validation, same content-addressed
    # key, same supersession of anything else accepted for the component.
    result = accept_posting_compensation(cur, role_instance_id, item)
    corrected_id = result["id"]

    if corrected_id != observation_id:
        # `_supersede_prior_accepted` keys on the *new* component, so a
        # correction that also changes the component (base -> total_package,
        # say) would leave the original accepted. Retire it explicitly: a
        # correction always retires what it corrects, whatever changed.
        cur.execute(
            """
            UPDATE jobber.compensation_observation
            SET review_status = 'rejected', reviewed_at = now(),
                source_note = COALESCE(source_note || ' | ', '') || %s
            WHERE id = %s AND role_instance_id = %s AND review_status != 'rejected'
            RETURNING id
            """,
            (f"corrected; superseded by reviewed observation {corrected_id}", observation_id, role_instance_id),
        )
        if cur.fetchone() is not None and observation_id not in result["superseded_observation_ids"]:
            result["superseded_observation_ids"] = [*result["superseded_observation_ids"], observation_id]

    result["corrected_from_observation_id"] = observation_id
    result["status"] = "corrected" if corrected_id != observation_id else "unchanged"
    return result


def reaccept_role_observation(cur, role_instance_id: str, observation_id: str) -> dict:
    """Bring a rejected posting-stated observation back.

    Re-validated and superseding, exactly like an acceptance: a row that was
    rejected while a replacement was accepted must retire that replacement on
    the way back in, or the role ends up with two accepted base salaries both
    feeding the archetype benchmark — the very state `_supersede_prior_accepted`
    exists to prevent.

    Updated in place rather than copied, and that is not the destructive case
    the correction path avoids: re-accepting changes no value, only this
    row's own review decision, so there is no prior figure to preserve."""
    role, document = _load_role_and_document(cur, role_instance_id)
    stored = _load_role_observation(cur, role_instance_id, observation_id)
    fallback_currency = _role_currency(cur, role_instance_id, role)
    validated = _validate_accept(_observation_as_item(stored), document, fallback_currency=fallback_currency)

    cur.execute(
        "UPDATE jobber.compensation_observation SET review_status = 'accepted', reviewed_at = now() "
        "WHERE id = %s AND role_instance_id = %s RETURNING id",
        (observation_id, role_instance_id),
    )
    if cur.fetchone() is None:
        raise PostingCompensationObservationError("compensation observation not found on this role")

    return {
        "id": observation_id,
        "status": "accepted",
        "review_status": "accepted",
        "superseded_observation_ids": _supersede_prior_accepted(
            cur, role_instance_id, observation_id, validated
        ),
    }


def reject_role_observation(cur, role_instance_id: str, observation_id: str) -> dict:
    """Retire an observation. No validation needed — rejecting can only ever
    remove a fact from consideration, never assert one — and the row is kept,
    not deleted, so the audit trail still shows it was once accepted."""
    _load_role_observation(cur, role_instance_id, observation_id)
    cur.execute(
        "UPDATE jobber.compensation_observation SET review_status = 'rejected', reviewed_at = now() "
        "WHERE id = %s AND role_instance_id = %s",
        (observation_id, role_instance_id),
    )
    return {"id": observation_id, "status": "rejected", "review_status": "rejected",
            "superseded_observation_ids": []}
