"""Personal current / latest-known earnings state, derived read-only from
`profile360.compensation_observation` (build §1).

Boundary, restated because it is the load-bearing rule of this module:
person-side compensation lives in `profile360` and is *never* copied into
`jobber`. Nothing here issues an INSERT/UPDATE/DELETE against `profile360`
— the only thing this module writes is `jobber.planning_assumption`, which
holds no compensation amount at all, only the user's own explicit
"assume N billable days per year" parameter used to derive a comparison at
read time (see `save_planning_assumption`).

What this module deliberately does NOT do:

- It never annualises a day rate on its own. A contract day rate stays a day
  rate unless the user has stated a billable-days assumption, and even then
  the derived figure is returned as a separate, explicitly labelled
  `planning_equivalent` alongside the source observation — never as, or in
  place of, the observation's own amount.
- It never combines currencies. Two accepted baselines in different
  currencies come back as two baselines; no FX rate exists anywhere in this
  codebase and none is invented here.
- It never merges PAYE and contracting. `employment_basis` is part of the
  baseline grouping key, so a PAYE salary and a contract day rate are two
  separate baselines that are separately comparable (or separately not
  comparable) against an opportunity.
- It never lets a bonus, allowance or employer pension contribution stand in
  for base pay. Those components are preserved, individually identifiable, in
  `other_components` — real earnings facts, but not the base-pay baseline an
  advertised base salary is compared against.
- It never treats an unreviewed or rejected observation as evidence. Only
  `review_status = 'accepted'` rows participate, exactly as the jobber-side
  compensation layer requires accepted rows.

"Current" vs "latest known" is a decision about what the *sources* actually
establish, never a guess about the present — and, critically, never an
assumption that an open-ended row is still in force. Two sources are
consulted together:

1. the observation's own stated period; and
2. the **employment episode it belongs to** (`profile360.episodes`, joined
   through `compensation_observation.episode_id`).

An open-ended salary observation attached to an episode that ended in 2022 is
*not* your current salary in 2026, however open-ended the observation row
looks on its own — that was a real mislabelling this module previously
produced, and it matters most for exactly the historical data this app is
built to hold. An episode that has ended, or whose status is not one this
module recognises as ongoing, makes its observations historical.

An observation with no dates of its own at all is likewise not called current
unless an ongoing episode genuinely establishes it. "No dates recorded" is
absence of evidence, not evidence of currency.

When no current evidence exists, the most recent historical evidence is
returned with `status='historical'` and a note saying so — never silently
presented as today's pay.
"""

import hashlib
from datetime import date

import psycopg

# profile360.compensation_observation.component values that describe base
# pay — the thing an advertised base salary is comparable to. Ordered by
# which makes the better baseline when a group has several: an explicit
# annual base beats a day rate, which beats a periodic base, which beats a
# raw gross-pay figure for one period.
_BASE_COMPONENT_PRIORITY = ("annual_base", "day_rate", "periodic_base", "gross_pay")

# Everything else is a real, accepted earnings fact that must stay visible
# and separately identifiable, but is never the base-pay baseline.
_OTHER_COMPONENTS = ("bonus", "allowance", "employer_pension", "other")

# profile360.episodes.status values this module accepts as "still ongoing".
# Deliberately a small allow-list rather than a deny-list: an unrecognised
# status is treated as NOT ongoing, so an unfamiliar value can only ever make
# this module more cautious about calling something current, never less.
_ONGOING_EPISODE_STATUSES = {"active", "current", "ongoing"}

# profile360 employment_basis -> the jobber-side vocabulary
# (compensation_observation.employment_basis: permanent | contract | unknown).
# 'other' deliberately has no jobber equivalent and stays unmapped, so a
# comparison against it is reported as not comparable rather than guessed.
EMPLOYMENT_BASIS_EQUIVALENT = {"paye": "permanent", "contract": "contract", "unknown": "unknown"}


class PersonalEarningsUnavailableError(RuntimeError):
    """`profile360.compensation_observation` is not reachable on this
    connection (schema absent, or the connected role lacks access) — the
    same "never silently return empty data for an unreachable schema"
    posture as profile360_reader.Profile360UnavailableError. A route maps
    this to a clear 503, never to an "no earnings recorded" answer."""


# --- Planning assumption (jobber-side, holds no compensation amount) -------

def load_planning_assumption(cur) -> dict:
    """The user's explicit contract-annualisation assumption, or an empty
    one. Never defaults to a number: with no assumption stated, a day rate
    simply has no annual planning equivalent."""
    cur.execute(
        "SELECT contract_billable_days_per_year, note, updated_at FROM jobber.planning_assumption WHERE singleton"
    )
    row = cur.fetchone()
    if not row or row["contract_billable_days_per_year"] is None:
        return {
            "contract_billable_days_per_year": None,
            "note": row["note"] if row else None,
            "updated_at": row["updated_at"] if row else None,
        }
    return {
        "contract_billable_days_per_year": float(row["contract_billable_days_per_year"]),
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


def save_planning_assumption(cur, *, contract_billable_days_per_year: float | None, note: str | None) -> dict:
    """Upsert the singleton. Passing `None` for the day count clears the
    assumption, which immediately removes every derived planning equivalent
    — the assumption is never sticky beyond what the user currently says."""
    cur.execute(
        """
        INSERT INTO jobber.planning_assumption (singleton, contract_billable_days_per_year, note, updated_at)
        VALUES (true, %s, %s, now())
        ON CONFLICT (singleton) DO UPDATE SET
            contract_billable_days_per_year = EXCLUDED.contract_billable_days_per_year,
            note = EXCLUDED.note, updated_at = now()
        """,
        (contract_billable_days_per_year, note),
    )
    return load_planning_assumption(cur)


# --- Reading the accepted person-side observations -------------------------

def _accepted_observations(cur) -> list[dict]:
    """Accepted person-side observations, each carrying its employment
    episode's end date, status and label. One LEFT JOIN rather than a second
    query per observation — and the episode context is not optional colour:
    `_is_current` cannot answer correctly without it."""
    try:
        cur.execute(
            """
            SELECT co.id, co.episode_id, co.source_kind, co.employment_basis, co.component,
                   co.period_start, co.period_end, co.pay_date, co.amount, co.currency, co.unit, co.quantity,
                   co.notes, co.uncertainty, co.created_at,
                   e.end_date AS episode_end_date, e.start_date AS episode_start_date,
                   e.status AS episode_status, e.title AS episode_title, e.organisation AS episode_organisation
            FROM profile360.compensation_observation co
            LEFT JOIN profile360.episodes e ON e.id = co.episode_id
            WHERE co.review_status = 'accepted'
            ORDER BY co.component, co.currency
            """
        )
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege) as e:
        raise PersonalEarningsUnavailableError(
            "profile360.compensation_observation is not reachable on this connection — "
            "personal earnings cannot be resolved (see docs/14 §5)."
        ) from e
    return [dict(r) for r in cur.fetchall()]


def personal_compensation_fingerprint(cur) -> str:
    """A digest of everything that determines the earnings state: every
    person-side compensation row (any review status), the employment episodes
    those rows are linked to, and the current planning assumption.

    `profile360` is externally owned and read-only from here, so this app
    cannot install an invalidation trigger on it the way migration 0019 does
    for its own tables — an edit made in profile360's own tool would
    otherwise never invalidate a cached result. Hashing the rows is how that
    external edit is detected.

    **The episode rows are part of this, not optional colour.** Since
    `_is_current` began consulting the employment episode, an episode moving
    from ongoing to ended changes a salary from current to historical without
    touching the salary row at all. A fingerprint over the observations alone
    would not notice. (The Pathways cache happens to be protected anyway,
    because `target_cache.revisions` hashes `profile360.episodes` for
    capability-coverage reasons — but relying on an unrelated hash to keep
    this correct is exactly the kind of accident that breaks silently when
    that hash is narrowed. This fingerprint now covers its own contract.)

    Only the episodes actually linked from a compensation observation are
    hashed: an unrelated episode edit changes no earnings answer, and
    including it would invalidate caches for no reason.

    Every review status is included on purpose: accepting or rejecting an
    observation changes the answer even though the row's compensation fields
    did not.

    Returns a fixed marker instead of raising when profile360 is unreachable,
    so a cache key can always be computed; the reader itself still raises."""
    try:
        cur.execute(
            "SELECT md5(COALESCE(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) AS digest "
            "FROM profile360.compensation_observation t"
        )
        rows_digest = cur.fetchone()["digest"] or ""
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        rows_digest = "profile360-compensation-unavailable"

    try:
        cur.execute(
            """
            SELECT md5(COALESCE(string_agg(
                md5(concat_ws('|', e.id::text, e.start_date::text, e.end_date::text, e.status)),
                '' ORDER BY e.id), '')) AS digest
            FROM profile360.episodes e
            WHERE EXISTS (
                SELECT 1 FROM profile360.compensation_observation co WHERE co.episode_id = e.id
            )
            """
        )
        episodes_digest = cur.fetchone()["digest"] or ""
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        episodes_digest = "profile360-episodes-unavailable"

    assumption = load_planning_assumption(cur)
    return hashlib.sha256(
        f"{rows_digest}|{episodes_digest}|{assumption['contract_billable_days_per_year']}".encode("utf-8")
    ).hexdigest()


# --- Deriving the earnings state -------------------------------------------

def _effective_from(observation: dict) -> date | None:
    """The date the source itself says this figure took effect. Never
    invented: an observation with no period and no pay date has none, and
    sorts last rather than being assigned today's date."""
    return observation["period_start"] or observation["pay_date"] or observation["period_end"]


def _episode_is_ongoing(observation: dict, today: date) -> bool | None:
    """Whether the employment episode this observation belongs to is still
    running. `None` means there is no linked episode to consult — not "yes"."""
    if observation.get("episode_id") is None:
        return None
    end = observation.get("episode_end_date")
    if end is not None and end < today:
        return False
    status = observation.get("episode_status")
    if status is not None and status not in _ONGOING_EPISODE_STATUSES:
        return False
    return True


def _is_current(observation: dict, today: date) -> bool:
    """Whether this observation describes what the person is paid *now*.

    Four rules, each of which can only ever demote to historical — nothing
    here promotes an observation to current on weak grounds:

    1. The observation's own stated period has ended -> historical.
    2. It starts in the future -> not current yet.
    3. The employment episode it belongs to has ended, or is not in a status
       this module recognises as ongoing -> historical, however open-ended
       the observation row itself looks. This is the rule that stops a 2019
       salary from an employment that ended in 2022 being reported as your
       current pay in 2026.
    4. An observation with no dates of its own is current only if a linked
       episode is genuinely ongoing. With no episode and no dates there is
       nothing establishing currency, so it is historical — absence of
       evidence is not evidence of currency.
    """
    if observation["period_end"] is not None and observation["period_end"] < today:
        return False
    start = observation["period_start"] or observation["pay_date"]
    if start is not None and start > today:
        return False

    episode_ongoing = _episode_is_ongoing(observation, today)
    if episode_ongoing is False:
        return False

    if start is None and observation["period_end"] is None:
        return episode_ongoing is True

    return True


def _evidence_period(observation: dict) -> str | None:
    start, end = observation["period_start"], observation["period_end"]
    if start and end:
        return f"{start.isoformat()} to {end.isoformat()}"
    if start:
        return f"from {start.isoformat()}"
    if end:
        return f"to {end.isoformat()}"
    if observation["pay_date"]:
        return observation["pay_date"].isoformat()
    return None


def _evidence_status_reason(observation: dict, today: date, evidence_status: str) -> str | None:
    """Why this observation is not current, in the user's terms. Only ever
    populated for a historical item, so a UI can explain the label instead of
    leaving the user to wonder why a salary they still hold reads as past."""
    if evidence_status == "current":
        return None
    if observation["period_end"] is not None and observation["period_end"] < today:
        return f"Its stated period ended on {observation['period_end'].isoformat()}."
    start = observation["period_start"] or observation["pay_date"]
    if start is not None and start > today:
        return f"It does not take effect until {start.isoformat()}."
    if _episode_is_ongoing(observation, today) is False:
        end = observation.get("episode_end_date")
        where = observation.get("episode_title") or observation.get("episode_organisation") or "the linked episode"
        if end is not None:
            return f"{where} ended on {end.isoformat()}, so this pay is no longer in force."
        return f"{where} is no longer recorded as ongoing, so this pay is no longer in force."
    return (
        "No period, pay date or ongoing employment episode establishes that this is still in force, "
        "so it is reported as latest known rather than current."
    )


def _serialize(observation: dict, *, evidence_status: str, today: date) -> dict:
    return {
        "observation_id": str(observation["id"]),
        "episode_id": str(observation["episode_id"]) if observation["episode_id"] else None,
        "episode_title": observation.get("episode_title"),
        "episode_organisation": observation.get("episode_organisation"),
        "episode_end_date": observation.get("episode_end_date"),
        "episode_status": observation.get("episode_status"),
        "evidence_status_reason": _evidence_status_reason(observation, today, evidence_status),
        "source_kind": observation["source_kind"],
        "employment_basis": observation["employment_basis"],
        "employment_basis_equivalent": EMPLOYMENT_BASIS_EQUIVALENT.get(observation["employment_basis"] or ""),
        "component": observation["component"],
        "amount": float(observation["amount"]),
        "currency": observation["currency"],
        "unit": observation["unit"],
        "quantity": float(observation["quantity"]) if observation["quantity"] is not None else None,
        "effective_from": _effective_from(observation),
        "period_start": observation["period_start"],
        "period_end": observation["period_end"],
        "pay_date": observation["pay_date"],
        "evidence_period": _evidence_period(observation),
        "evidence_status": evidence_status,
        "notes": observation["notes"],
        "uncertainty": observation["uncertainty"],
    }


def _planning_equivalent(baseline: dict, assumption: dict) -> dict | None:
    """Only ever derived for a genuine day rate, and only when the user has
    stated the assumption. Always labelled as a planning equivalent with the
    arithmetic shown, and always returned *alongside* the source amount, so
    nothing downstream can mistake it for a stated salary."""
    days = assumption.get("contract_billable_days_per_year")
    if days is None:
        return None
    if baseline["component"] != "day_rate" and baseline["unit"] != "daily":
        return None
    amount = baseline["amount"] * days
    return {
        "amount": amount,
        "currency": baseline["currency"],
        "unit": "annual",
        "basis": "planning_equivalent",
        "assumption": {
            "billable_days_per_year": days,
            "day_rate": baseline["amount"],
            "note": assumption.get("note"),
        },
        "label": (
            f"{baseline['amount']:,.0f} x {days:g} billable days"
        ),
        "caveat": "This is a planning equivalent, not salary.",
    }


def _baseline_sort_key(item: dict, today: date):
    """Within one (currency, employment_basis) group: current evidence
    first, then the stronger base component, then the most recent effective
    date, then a stable id so the choice never flips between identical
    candidates."""
    effective = item["effective_from"]
    return (
        0 if item["evidence_status"] == "current" else 1,
        _BASE_COMPONENT_PRIORITY.index(item["component"]),
        -(effective.toordinal() if effective else 0),
        item["observation_id"],
    )


def personal_earnings_state(cur, *, today: date | None = None) -> dict:
    """The user's current (or, failing that, latest-known) earnings state.

    One baseline per (currency, employment_basis) group — never one merged
    figure. `status` summarises the whole state: 'current' if any baseline
    rests on current evidence, 'historical' if only ended periods exist,
    'unavailable' if there is no accepted person-side compensation evidence
    at all. `unavailable` is a real answer, not an error: this app cannot
    know the user's pay until profile360 holds accepted evidence of it."""
    today = today or date.today()
    observations = _accepted_observations(cur)
    assumption = load_planning_assumption(cur)
    fingerprint = personal_compensation_fingerprint(cur)

    if not observations:
        return {
            "status": "unavailable",
            "as_of": today,
            "baselines": [],
            "other_components": [],
            "currencies": [],
            "planning_assumption": assumption,
            "notes": [
                "No accepted personal compensation evidence exists in profile360, "
                "so no earnings baseline can be stated."
            ],
            "fingerprint": fingerprint,
        }

    base_items: list[dict] = []
    other_items: list[dict] = []
    for observation in observations:
        evidence_status = "current" if _is_current(observation, today) else "historical"
        item = _serialize(observation, evidence_status=evidence_status, today=today)
        if observation["component"] in _BASE_COMPONENT_PRIORITY:
            base_items.append(item)
        elif observation["component"] in _OTHER_COMPONENTS:
            other_items.append(item)

    groups: dict[tuple[str | None, str | None], list[dict]] = {}
    for item in base_items:
        groups.setdefault((item["currency"], item["employment_basis"]), []).append(item)

    baselines = []
    for (currency, employment_basis), items in groups.items():
        items.sort(key=lambda i: _baseline_sort_key(i, today))
        chosen = items[0]
        superseded = items[1:]
        baselines.append(
            {
                **chosen,
                "planning_equivalent": _planning_equivalent(chosen, assumption),
                "label": "Current earnings" if chosen["evidence_status"] == "current" else "Latest known earnings",
                "other_evidence_in_group": [
                    {
                        "observation_id": i["observation_id"],
                        "component": i["component"],
                        "amount": i["amount"],
                        "unit": i["unit"],
                        "evidence_status": i["evidence_status"],
                        "evidence_period": i["evidence_period"],
                    }
                    for i in superseded
                ],
                "group": {"currency": currency, "employment_basis": employment_basis},
            }
        )

    # Deterministic presentation order: current before historical, then by
    # currency/basis so the same evidence always renders in the same order.
    baselines.sort(key=lambda b: (0 if b["evidence_status"] == "current" else 1, b["currency"] or "", b["employment_basis"] or ""))

    currencies = sorted({b["currency"] for b in baselines if b["currency"]})
    any_current = any(b["evidence_status"] == "current" for b in baselines)
    status = "current" if any_current else ("historical" if baselines else "unavailable")

    notes: list[str] = []
    if status == "historical":
        notes.append("No current compensation evidence — showing the latest known earnings instead.")
        ended = [b for b in baselines if b.get("episode_end_date") is not None]
        if ended:
            where = ", ".join(
                sorted({b.get("episode_title") or b.get("episode_organisation") or "an earlier role" for b in ended})
            )
            notes.append(
                f"The most recent accepted pay evidence belongs to employment that has ended ({where}), "
                "so it is not reported as your current earnings."
            )
        undated = [
            b for b in baselines
            if b["effective_from"] is None and b["period_end"] is None and b.get("episode_id") is None
        ]
        if undated:
            notes.append(
                "Some accepted compensation evidence carries no period, pay date or employment episode. "
                "Without one of those, nothing establishes that it is still in force."
            )
    if len(currencies) > 1:
        notes.append(
            "Accepted compensation evidence exists in more than one currency. "
            "These are reported separately and are never converted or combined."
        )
    if len({b["employment_basis"] for b in baselines}) > 1:
        notes.append(
            "Accepted compensation evidence spans more than one employment basis "
            "(for example PAYE and contract). These are kept distinct."
        )
    if any(b["component"] == "day_rate" and b["planning_equivalent"] is None for b in baselines):
        notes.append(
            "A day rate has no annual planning equivalent until you state a billable-days-per-year assumption."
        )
    if not baselines and other_items:
        notes.append(
            "Only non-base compensation evidence (bonus, allowance or pension) is accepted; "
            "no base-pay baseline can be stated from it."
        )

    return {
        "status": status,
        "as_of": today,
        "baselines": baselines,
        "other_components": other_items,
        "currencies": currencies,
        "planning_assumption": assumption,
        "notes": notes,
        "fingerprint": fingerprint,
    }


def safe_personal_earnings_state(cur, *, today: date | None = None) -> dict:
    """`personal_earnings_state`, but with an unreachable profile360 turned
    into an explicit state rather than an exception — for composite readers
    (Pathways, Role Detail) where the personal overlay is one panel among
    several and its absence must not fail the whole response."""
    try:
        return personal_earnings_state(cur, today=today)
    except PersonalEarningsUnavailableError as e:
        return {
            "status": "profile360_unavailable",
            "as_of": today or date.today(),
            "baselines": [],
            "other_components": [],
            "currencies": [],
            "planning_assumption": {"contract_billable_days_per_year": None, "note": None, "updated_at": None},
            "notes": [str(e)],
            "fingerprint": "profile360-unavailable",
        }
