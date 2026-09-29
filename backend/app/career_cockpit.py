"""Phase 9: Career Cockpit composition (docs/40) — the Home page's one
server-side read.

`build_cockpit` composes systems that already exist (Career Direction,
Target progress, Opportunity alignment, Applications, the Profile360
learning-promotion queue, Market Coverage) into the bounded sections
`routes/cockpit.py::get_cockpit` returns. It computes nothing new about
evidence, opportunities or applications — every section is either a direct
read or a bounded, batched composition of an existing service.

No AI, no writes anywhere in this module (build §16) — the only mutations
in the whole Phase 9 surface are `career_progress.record_checkpoint` and
`application_learning.promote_note`/`promote_event`, both explicit POSTs
routed elsewhere.

## Section failure isolation (build §37)

Each of the six sections below opens its *own* `db_cursor()` (its own
pooled connection/transaction) rather than sharing one cursor across the
whole composition. This is deliberate, not just a style choice: Postgres
aborts a transaction on the first error within it, so a shared cursor would
let one section's failure silently poison every section built after it.
Giving each section its own connection means a Market Coverage failure
genuinely cannot blank Applications or Opportunities, and vice versa —
`_safe` catches the exception and reports `{"state": "unavailable"}` for
that section alone. The one read that is *not* isolated this way is the
selected-Direction lookup itself (`_selected_direction`) — a single cheap
indexed SELECT that most other sections need context from; a genuine
failure there (build §37's "critical DB/identity failures may still error")
is allowed to propagate as a 500 rather than silently faking "no Direction
selected", which would misrepresent real state.
"""

import logging
from datetime import datetime, timezone

from . import application_learning, career_directions, career_progress, market_coverage, opportunity_alignment
from .db import db_cursor
from .economics_freshness import economics_freshness

logger = logging.getLogger(__name__)

OPPORTUNITIES_LIMIT = 5
ACTIVE_APPLICATIONS_LIMIT = 5
RECENT_OUTCOMES_LIMIT = 5
LEARNING_ITEMS_LIMIT = 8
NEXT_ACTIONS_LIMIT = 4
INTERVIEW_URGENCY_DAYS = 7

ACTIVE_STATUSES = ("preparing", "ready", "submitted", "interviewing")
OUTCOME_EVENT_TYPES = ("offer_received", "offer_accepted", "offer_declined", "rejected", "role_closed", "withdrawn", "closed")


def _safe(build_fn) -> dict:
    try:
        return build_fn()
    except Exception as e:  # section failure isolation is the point (build §37)
        logger.exception("career_cockpit section failed")
        return {"state": "unavailable", "reason": str(e) or "temporarily unavailable"}


def _safe_list(build_fn) -> list:
    try:
        return build_fn()
    except Exception:
        logger.exception("career_cockpit auxiliary lookup failed")
        return []


def _selected_direction() -> dict | None:
    with db_cursor() as cur:
        return career_directions.get_selected_direction_summary(cur)


# --- Direction (build §17) ---------------------------------------------------


def _build_direction_section(direction: dict | None) -> dict:
    if direction is None:
        return {"state": "no_direction", "direction": None, "target": None}
    return {
        "state": "with_target" if direction.get("target") else "no_target",
        "direction": {
            "id": direction["id"],
            "name": direction["name"],
            "summary": direction["summary"],
            "selected_at": direction["selected_at"],
            "constraints": direction.get("constraints"),
            "top_dimensions": sorted(direction.get("dimensions") or [], key=lambda d: -d["importance"])[:5],
            "archetype": direction.get("archetype"),
        },
        "target": direction.get("target"),
    }


# --- Target progress (build §18) — reuses career_progress.read_progress ----


def _build_target_progress_section(direction: dict | None) -> dict:
    with db_cursor() as cur:
        return career_progress.read_progress(cur, direction)


def _development_actions_due(target_id: str) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, due_date FROM jobber.development_action "
            "WHERE role_instance_id = %s AND status = 'open' AND due_date IS NOT NULL AND due_date <= CURRENT_DATE "
            "ORDER BY due_date ASC",
            (target_id,),
        )
        return [{"id": str(r["id"]), "title": r["title"], "due_date": r["due_date"]} for r in cur.fetchall()]


# --- Opportunities (build §19/§20) ------------------------------------------


def _build_opportunities_section(direction: dict | None) -> dict:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT ri.id, ri.title, ri.organisation, ri.location, ri.posting_date, d.captured_at
            FROM jobber.role_instance ri
            LEFT JOIN jobber.document d ON d.id = ri.document_id
            WHERE ri.instance_type = 'observed_posting'
            ORDER BY COALESCE(d.captured_at, ri.created_at) DESC
            LIMIT %s
            """,
            (OPPORTUNITIES_LIMIT,),
        )
        rows = cur.fetchall()
        ids = [str(r["id"]) for r in rows]

        applied_ids: set[str] = set()
        if ids:
            cur.execute(
                "SELECT DISTINCT role_instance_id FROM jobber.application WHERE role_instance_id = ANY(%s::uuid[])", (ids,)
            )
            applied_ids = {str(r["role_instance_id"]) for r in cur.fetchall()}

        # No fabricated route relationship without a Target (build §20's
        # last line) — `relationships` stays empty and every item below
        # reports `relationship: null`.
        relationships: dict[str, dict] = {}
        target = (direction or {}).get("target")
        if target and ids:
            cur.execute("SELECT archetype_concept_id FROM jobber.role_instance WHERE id = %s", (target["id"],))
            target_row = cur.fetchone()
            target_archetype_id = str(target_row["archetype_concept_id"]) if target_row and target_row["archetype_concept_id"] else None
            direction_archetype = (direction or {}).get("archetype")
            relationships = opportunity_alignment.bulk_target_relationship(
                cur,
                target["id"],
                ids,
                target_archetype_id=target_archetype_id,
                direction_archetype_id=direction_archetype["id"] if direction_archetype else None,
            )

    items = []
    for r in rows:
        role_id = str(r["id"])
        rel = relationships.get(role_id)
        items.append(
            {
                "role_instance_id": role_id,
                "title": r["title"],
                "organisation": r["organisation"],
                "location": r["location"],
                "posting_date": r["posting_date"],
                "relationship": rel["relationship"] if rel else None,
                "target_gaps_involved": rel["target_gaps_addressed"] if rel else [],
                "review_caveat": rel["review_caveat"] if rel else None,
                "has_application": role_id in applied_ids,
            }
        )
    return {"state": "available", "items": items}


# --- Applications (build §21/§22) -------------------------------------------


def _build_applications_section() -> dict:
    with db_cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS n FROM jobber.application GROUP BY status")
        by_status = {row["status"]: row["n"] for row in cur.fetchall()}
        active_count = sum(by_status.get(s, 0) for s in ACTIVE_STATUSES)

        # Same bounded, LATERAL-joined shape as routes/applications.py::
        # list_applications (build §21's "no query per Application") —
        # narrowed to active statuses and this section's own preview bound.
        cur.execute(
            """
            SELECT a.id, a.role_instance_id, a.status, a.updated_at,
                   ri.title, ri.organisation,
                   latest.event_type AS latest_event_type, latest.event_at AS latest_event_at,
                   nxt.event_at AS next_interview_at, nxt.label AS next_interview_label
            FROM jobber.application a
            JOIN jobber.role_instance ri ON ri.id = a.role_instance_id
            LEFT JOIN LATERAL (
                SELECT event_type, event_at FROM jobber.application_event e
                WHERE e.application_id = a.id ORDER BY e.event_at DESC, e.created_at DESC LIMIT 1
            ) latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT event_at, label FROM jobber.application_event e
                WHERE e.application_id = a.id AND e.event_type = 'interview_scheduled' AND e.event_at >= now()
                ORDER BY e.event_at ASC LIMIT 1
            ) nxt ON TRUE
            WHERE a.status = ANY(%s)
            ORDER BY a.updated_at DESC LIMIT %s
            """,
            (list(ACTIVE_STATUSES), ACTIVE_APPLICATIONS_LIMIT),
        )
        active_items = [
            {
                "id": str(r["id"]),
                "role_instance_id": str(r["role_instance_id"]),
                "status": r["status"],
                "role": {"title": r["title"], "organisation": r["organisation"]},
                "latest_event": (
                    {"event_type": r["latest_event_type"], "event_at": r["latest_event_at"]}
                    if r["latest_event_type"] is not None else None
                ),
                "next_interview": (
                    {"event_at": r["next_interview_at"], "label": r["next_interview_label"]}
                    if r["next_interview_at"] is not None else None
                ),
            }
            for r in cur.fetchall()
        ]

        # The soonest upcoming interview *across every* Application, not
        # just active ones — a closed/withdrawn Application can't carry a
        # still-upcoming interview_scheduled event in practice, but this
        # never assumes that; it asks directly.
        cur.execute(
            """
            SELECT e.event_at, e.label, a.id AS application_id, ri.title, ri.organisation
            FROM jobber.application_event e
            JOIN jobber.application a ON a.id = e.application_id
            JOIN jobber.role_instance ri ON ri.id = a.role_instance_id
            WHERE e.event_type = 'interview_scheduled' AND e.event_at >= now()
            ORDER BY e.event_at ASC LIMIT 1
            """
        )
        next_row = cur.fetchone()
        next_interview = (
            {
                "application_id": str(next_row["application_id"]),
                "role_title": next_row["title"],
                "organisation": next_row["organisation"],
                "event_at": next_row["event_at"],
                "label": next_row["label"],
            }
            if next_row else None
        )

        cur.execute(
            """
            SELECT e.id, e.event_type, e.event_at, e.notes, a.id AS application_id, ri.title, ri.organisation
            FROM jobber.application_event e
            JOIN jobber.application a ON a.id = e.application_id
            JOIN jobber.role_instance ri ON ri.id = a.role_instance_id
            WHERE e.event_type = ANY(%s)
            ORDER BY e.event_at DESC LIMIT %s
            """,
            (list(OUTCOME_EVENT_TYPES), RECENT_OUTCOMES_LIMIT),
        )
        recent_outcomes = [
            {
                "id": str(r["id"]),
                "event_type": r["event_type"],
                "event_at": r["event_at"],
                "application_id": str(r["application_id"]),
                "role": {"title": r["title"], "organisation": r["organisation"]},
                "has_notes": bool((r["notes"] or "").strip()),
            }
            for r in cur.fetchall()
        ]

    return {
        "state": "available",
        "active_count": active_count,
        "by_status": by_status,
        "active_items": active_items,
        "next_interview": next_interview,
        "recent_outcomes": recent_outcomes,
    }


# --- Learning (build §23) ---------------------------------------------------


def _build_learning_section(target_requirements_by_concept: dict[str, dict] | None) -> dict:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT n.id, n.application_id, n.note_type, n.note_text, n.concept_id, n.created_at,
                   a.role_instance_id, ri.title AS role_title, ri.organisation,
                   c.canonical_name AS concept_canonical_name
            FROM jobber.application_note n
            JOIN jobber.application a ON a.id = n.application_id
            JOIN jobber.role_instance ri ON ri.id = a.role_instance_id
            LEFT JOIN jobber.concept c ON c.id = n.concept_id
            ORDER BY n.created_at DESC LIMIT %s
            """,
            (LEARNING_ITEMS_LIMIT,),
        )
        note_rows = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT e.id, e.application_id, e.event_type, e.event_at, e.notes,
                   a.role_instance_id, ri.title AS role_title, ri.organisation
            FROM jobber.application_event e
            JOIN jobber.application a ON a.id = e.application_id
            JOIN jobber.role_instance ri ON ri.id = a.role_instance_id
            WHERE e.notes IS NOT NULL AND length(trim(e.notes)) > 0
            ORDER BY e.event_at DESC LIMIT %s
            """,
            (LEARNING_ITEMS_LIMIT,),
        )
        event_rows = [dict(r) for r in cur.fetchall()]

        merged = [{"kind": "note", "row": r, "date": r["created_at"]} for r in note_rows]
        merged += [{"kind": "event", "row": r, "date": r["event_at"]} for r in event_rows]
        merged.sort(key=lambda x: x["date"], reverse=True)
        merged = merged[:LEARNING_ITEMS_LIMIT]

        hashes: dict[str, str] = {}
        for m in merged:
            row = m["row"]
            role = {"id": row["role_instance_id"], "title": row["role_title"], "organisation": row["organisation"]}
            try:
                if m["kind"] == "note":
                    concept = {"canonical_name": row["concept_canonical_name"]} if row["concept_id"] else None
                    _, content_hash = application_learning.note_payload(row, role=role, concept=concept)
                    key = application_learning.note_source_key(str(row["id"]))
                else:
                    _, content_hash = application_learning.event_payload(row, role=role)
                    key = application_learning.event_source_key(str(row["id"]))
            except application_learning.LearningSourceError:
                continue
            hashes[key] = content_hash
            m["source_key"] = key
        merged = [m for m in merged if "source_key" in m]

        queue_rows = application_learning.queue_rows_bulk(cur, list(hashes))

    items = []
    for m in merged:
        row, kind = m["row"], m["kind"]
        concept_id = str(row["concept_id"]) if kind == "note" and row.get("concept_id") else None
        target_requirement = (target_requirements_by_concept or {}).get(concept_id) if concept_id else None
        text = row["note_text"] if kind == "note" else row["notes"]
        items.append(
            {
                "source_type": kind,
                "source_id": str(row["id"]),
                "application_id": str(row["application_id"]),
                "role": {"title": row["role_title"], "organisation": row["organisation"]},
                "date": m["date"],
                "text_preview": (text or "")[:280],
                "concept_id": concept_id,
                "concept_canonical_name": row.get("concept_canonical_name") if kind == "note" else None,
                "note_type": row["note_type"] if kind == "note" else None,
                "event_type": row["event_type"] if kind == "event" else None,
                "queue_status": application_learning.status_for(queue_rows.get(m["source_key"]), hashes[m["source_key"]]),
                "is_current_target_requirement": target_requirement is not None,
                "current_target_evidence_status": target_requirement["status"] if target_requirement else None,
            }
        )
    return {"state": "available", "items": items}


# --- Market context (build §25) ---------------------------------------------


def _resolve_market_archetype(cur, direction: dict) -> str | None:
    target = direction.get("target")
    if target:
        cur.execute("SELECT archetype_concept_id FROM jobber.role_instance WHERE id = %s", (target["id"],))
        row = cur.fetchone()
        if row and row["archetype_concept_id"]:
            return str(row["archetype_concept_id"])
    archetype = direction.get("archetype")
    return archetype["id"] if archetype else None


def _build_market_context_section(direction: dict | None) -> dict:
    if direction is None:
        return {"state": "no_direction"}
    with db_cursor() as cur:
        archetype_id = _resolve_market_archetype(cur, direction)
        if archetype_id is None:
            return {"state": "no_archetype", "reason": "No archetype is assigned to this Direction or Target yet."}
        freshness = economics_freshness(cur)
        detail = market_coverage.archetype_coverage_detail(cur, archetype_id, freshness=freshness)
        if not detail["found"]:
            return {"state": "no_archetype", "reason": "The linked archetype could not be found."}
        # Same field names `ArchetypeEvidence` already uses everywhere else
        # this shape appears (Pathways, Opportunity alignment, Career
        # Direction detail) — never a renamed alias of the same facts, so
        # the frontend can share one type (build §25's "reuse Phase 8's
        # shared coverage service... do not create another market
        # confidence definition").
        return {"state": "available", "representativeness": market_coverage.REPRESENTATIVENESS_UNKNOWN, **detail}


# --- Next actions (build §26/§27) -------------------------------------------


def _diff_has_changes(diff: dict | None) -> bool:
    if not diff:
        return False
    return bool(diff["evidence_strengthened"] or diff["evidence_weakened"] or diff["assertion_added"])


def build_next_actions(
    *,
    direction: dict | None,
    target_progress: dict,
    applications: dict,
    learning: dict,
    opportunities: dict,
    development_actions_due: list[dict],
    now: datetime,
) -> list[dict]:
    """Deterministic, rule-based next actions (build §26/§27) — no model
    call, no score. Rules are evaluated in a fixed precedence order below;
    every rule whose condition holds contributes one action, and the result
    is capped at NEXT_ACTIONS_LIMIT. Order is workflow/urgency precedence,
    not a career-quality ranking (build §26).

    Precedence:
      1. an interview is scheduled within INTERVIEW_URGENCY_DAYS
      2. no Career Direction is selected
      3. the selected Direction has no linked Target
      4. Target requirement review/mapping is incomplete
      5. no progress baseline has been recorded yet
      6. current evidence changed since the last checkpoint
      7. unqueued application learning exists
      8. open development actions are due/overdue
      9. recent opportunities have been captured
      10. otherwise: review the Direction or Market Coverage

    Rules 2-6 are mutually exclusive stages of the same Direction/Target
    setup funnel (`elif`, not independent checks) — only the earliest
    unmet one of them ever fires for a given state, since each presupposes
    the previous one is already satisfied.
    """
    actions: list[dict] = []

    next_interview = applications.get("next_interview") if applications.get("state") == "available" else None
    if next_interview and next_interview.get("event_at"):
        days_until = (next_interview["event_at"].date() - now.date()).days
        if 0 <= days_until <= INTERVIEW_URGENCY_DAYS:
            actions.append(
                {
                    "code": "upcoming_interview",
                    "title": f"Prepare for your interview — {next_interview.get('role_title') or 'upcoming role'}",
                    "reason": f"An interview is scheduled in {days_until} day(s).",
                    "href": f"/applications/{next_interview['application_id']}",
                    "urgency_days": days_until,
                }
            )

    if direction is None:
        actions.append(
            {
                "code": "select_direction",
                "title": "Define or select a Career Direction",
                "reason": "No Career Direction is currently selected.",
                "href": "/future",
            }
        )
    elif direction.get("target") is None:
        actions.append(
            {
                "code": "link_target",
                "title": "Create or link a concrete Target",
                "reason": f"“{direction['name']}” has no linked Target yet.",
                "href": f"/future/directions/{direction['id']}",
            }
        )
    elif target_progress.get("current") and not (
        target_progress["current"]["review"]["target_review_complete"]
        and target_progress["current"]["review"]["target_mapping_complete"]
    ):
        actions.append(
            {
                "code": "complete_target_review",
                "title": "Finish reviewing your Target's requirements",
                "reason": "Requirement review or mapping is incomplete, so progress cannot be read reliably.",
                "href": f"/targets/{target_progress['current']['target']['id']}",
            }
        )
    elif target_progress.get("state") == "no_checkpoint":
        actions.append(
            {
                "code": "record_baseline",
                "title": "Record a progress baseline",
                "reason": "No progress checkpoint has been recorded yet for this Target.",
                "href": "/",
            }
        )
    elif target_progress.get("state") == "available" and _diff_has_changes(target_progress.get("diff")):
        diff = target_progress["diff"]
        changed = len(diff["evidence_strengthened"]) + len(diff["evidence_weakened"]) + len(diff["assertion_added"])
        actions.append(
            {
                "code": "review_evidence_change",
                "title": "Review what changed in your Target evidence",
                "reason": f"{changed} requirement(s) changed since your last checkpoint.",
                "href": "/",
            }
        )

    if learning.get("state") == "available":
        unqueued = [i for i in learning["items"] if i["queue_status"] == application_learning.NOT_QUEUED]
        if unqueued:
            actions.append(
                {
                    "code": "review_learning",
                    "title": "Review recent application learning",
                    "reason": f"{len(unqueued)} recent note(s)/reflection(s) have not been sent to Profile360 for review.",
                    "href": "/applications",
                }
            )

    if development_actions_due:
        target_id = ((target_progress.get("current") or {}).get("target") or {}).get("id")
        actions.append(
            {
                "code": "development_actions_due",
                "title": "Check your development actions",
                "reason": f"{len(development_actions_due)} open development action(s) are due or overdue.",
                "href": f"/targets/{target_id}" if target_id else "/",
            }
        )

    if opportunities.get("state") == "available" and opportunities["items"]:
        actions.append(
            {
                "code": "review_opportunities",
                "title": "Review recent opportunities",
                "reason": f"{len(opportunities['items'])} opportunity/opportunities captured recently.",
                "href": "/opportunities?period=current",
            }
        )

    if not actions:
        actions.append(
            {
                "code": "review_market",
                "title": "Review your Direction or Market Coverage",
                "reason": "Nothing more specific needs attention right now.",
                "href": "/market/coverage",
            }
        )

    return actions[:NEXT_ACTIONS_LIMIT]


# --- Composition -------------------------------------------------------------


def build_cockpit() -> dict:
    """GET /api/cockpit (build §16/§40). Bounded, read-only, no AI, no side
    effects. See the module docstring for why each section manages its own
    connection rather than sharing one cursor."""
    now = datetime.now(timezone.utc)
    direction = _selected_direction()

    direction_section = _safe(lambda: _build_direction_section(direction))
    target_progress = _safe(lambda: _build_target_progress_section(direction))
    opportunities = _safe(lambda: _build_opportunities_section(direction))
    applications = _safe(_build_applications_section)

    target_requirements_by_concept = None
    if target_progress.get("current"):
        target_requirements_by_concept = {r["concept_id"]: r for r in target_progress["current"]["requirements"]}
    learning = _safe(lambda: _build_learning_section(target_requirements_by_concept))

    market_context = _safe(lambda: _build_market_context_section(direction))

    development_actions_due: list[dict] = []
    if target_progress.get("current"):
        target_id = target_progress["current"]["target"]["id"]
        development_actions_due = _safe_list(lambda: _development_actions_due(target_id))

    next_actions = build_next_actions(
        direction=direction,
        target_progress=target_progress,
        applications=applications,
        learning=learning,
        opportunities=opportunities,
        development_actions_due=development_actions_due,
        now=now,
    )

    return {
        "direction": direction_section,
        "target_progress": target_progress,
        "opportunities": opportunities,
        "applications": applications,
        "learning": learning,
        "market_context": market_context,
        "next_actions": next_actions,
    }
