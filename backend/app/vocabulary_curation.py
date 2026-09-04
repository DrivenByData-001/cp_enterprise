"""Vocabulary proposal prioritisation and curation (Vocabulary Proposal
Prioritisation and Curation UX brief). This module turns the flat
`jobber.concept_proposal` queue (1,525 rows in production, §bootstrap) into a
prioritised, evidence-rich, cluster-level review workflow.

Architecture, deliberately minimal (brief §1: "Do not duplicate corpus
evidence into new persistence tables merely for display if it can be
calculated efficiently"):

- **No new tables.** Every piece of cluster evidence (role coverage, year
  span, countries, seniority, sample titles) is computed live, on each
  request, by joining the existing `concept_proposal` and
  `role_skill_observation`/`role_instance` tables in Python — the same
  pattern `routes/concepts.py::_group_proposals` already used for live
  occurrence counts, just extended to carry the fuller evidence this brief
  asks for. At this corpus's scale (~1,525 proposals, ~4,700 unresolved
  observations) this is comfortably sub-100ms; no caching, no materialised
  view, no new index — see docs/19 for why none of those are justified yet.
- **The browser never sees the whole queue.** `list_clusters` computes the
  full ranked/filtered set server-side and returns one page; the frontend
  only ever holds `limit` rows at a time (brief §3).
- **Cluster resolution reuses the existing proposal-resolution core**
  (`resolve_surface_form_group`, moved here from `routes/concepts.py` without
  behavioural change) rather than inventing a second way to create concepts/
  aliases/mappings — "Accept" = `accept_new`, "Merge into existing concept" =
  `accept_alias` (already exactly that semantically), "Reject" = `reject`.
  This module adds an idempotency pre-check in front of it (brief §5:
  "remain idempotent... do not create duplicate canonical concepts when the
  action is retried") and batch/preview wrappers the legacy single-surface-
  form endpoint never needed.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from fastapi import HTTPException
import psycopg

from .concept_linking import normalize_name
from .vocabulary_priority import (
    BAND_HIGH,
    ClusterSignals,
    assign_priority_band,
    compute_priority_score,
    evidence_flags,
    noise_flags,
)

# How many example role titles a cluster carries in the list view vs. the
# expanded detail view (brief §4: "Allow expansion for more evidence/
# examples"). Small caps so the queue endpoint stays cheap to serialise even
# though the underlying evidence computation itself is whole-corpus.
LIST_EXAMPLE_ROLE_LIMIT = 5
DETAIL_EXAMPLE_ROLE_LIMIT = 25

_RESOLVED_STATUSES_ACCEPTED = ("accepted_new", "accepted_alias")
_ACTIONS = ("accept_new", "accept_alias", "reject", "defer")


# --- shared proposal-resolution core (moved from routes/concepts.py, no ---
# --- behavioural change: same SQL, same status vocabulary, same order)  ---

def resolve_surface_form_group(
    cur,
    surface_forms: list[str],
    *,
    action: str,
    now: datetime,
    type_code: str | None = None,
    canonical_name: str | None = None,
    definition: str | None = None,
    concept_id: str | None = None,
) -> tuple[str, str | None]:
    """Shared core of the legacy single-surface-form endpoint
    (`POST /api/concepts/proposals/resolve`), the legacy cluster endpoint
    (`.../resolve-cluster`), and this module's `accept_cluster`/
    `reject_cluster`/`merge_cluster`. For a cluster with more than one
    member, exactly one concept is ever created/chosen — every *other*
    member surface form becomes an alias of it.

    Returns (status, resolved_concept_id). Raises HTTPException on the same
    conditions the original endpoint did (404 no pending proposals, 400 bad
    concept_id/duplicate name) — untouched behaviour for the n=1 case that
    predates this module.
    """
    if action not in _ACTIONS:
        raise HTTPException(400, f"action must be one of {_ACTIONS}")
    if action == "accept_new" and not (type_code and canonical_name):
        raise HTTPException(400, "accept_new requires type_code and canonical_name")
    if action == "accept_alias" and not concept_id:
        raise HTTPException(400, "accept_alias requires concept_id")

    all_proposal_ids: list[str] = []
    ids_by_surface_form: dict[str, list[str]] = {}
    for surface_form in surface_forms:
        cur.execute(
            "SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'",
            (surface_form,),
        )
        ids = [r["id"] for r in cur.fetchall()]
        ids_by_surface_form[surface_form] = ids
        all_proposal_ids.extend(ids)

    if not all_proposal_ids:
        raise HTTPException(404, "no pending proposals for this surface form")

    resolved_concept_id = None

    if action == "accept_new":
        try:
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at, reviewed_at) "
                "VALUES (%s, %s, %s, 'active', 'extraction_proposal', %s, %s) RETURNING id",
                (type_code, canonical_name, definition, now, now),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "a concept with this type and canonical name already exists")
        resolved_concept_id = cur.fetchone()["id"]
        new_status = "accepted_new"
        canonical_normalized = normalize_name(canonical_name)
        alias_targets = [sf for sf in surface_forms if sf != canonical_normalized]

    elif action == "accept_alias":
        cur.execute("SELECT id FROM jobber.concept WHERE id = %s AND status = 'active'", (concept_id,))
        if not cur.fetchone():
            raise HTTPException(400, "concept_id does not exist or is not active")
        resolved_concept_id = concept_id
        new_status = "accepted_alias"
        alias_targets = list(surface_forms)

    elif action == "reject":
        new_status = "rejected"
        alias_targets = []
    else:
        new_status = "deferred"
        alias_targets = []

    for surface_form in alias_targets:
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) "
            "VALUES (%s, %s, 'extraction_proposal', %s) ON CONFLICT (alias, concept_id) DO NOTHING",
            (resolved_concept_id, surface_form, now),
        )

    for surface_form, ids in ids_by_surface_form.items():
        if not ids:
            continue
        cur.execute(
            "UPDATE jobber.concept_proposal SET status = %s, resolved_concept_id = %s, resolved_at = %s "
            "WHERE id = ANY(%s::uuid[])",
            (new_status, resolved_concept_id, now, ids),
        )
        if resolved_concept_id is not None:
            cur.execute(
                "SELECT id, surface_form AS name FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL"
            )
            matching_ids = [r["id"] for r in cur.fetchall() if normalize_name(r["name"]) == surface_form]
            if matching_ids:
                cur.execute(
                    "UPDATE jobber.role_skill_observation SET canonical_concept_id = %s WHERE id = ANY(%s::uuid[])",
                    (resolved_concept_id, matching_ids),
                )

    return new_status, (str(resolved_concept_id) if resolved_concept_id is not None else None)


# --- cluster evidence aggregation (brief §1) ------------------------------

@dataclass
class ClusterEvidence:
    cluster_key: str
    surface_forms: list[str] = field(default_factory=list)
    proposal_ids: list[str] = field(default_factory=list)
    suggested_type: str | None = None
    nearest_concept_id: str | None = None
    nearest_similarity: float | None = None
    role_ids: set[str] = field(default_factory=set)
    observation_count: int = 0
    years: set[int] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    seniority_levels: set[str] = field(default_factory=set)
    career_tracks: set[str] = field(default_factory=set)
    first_observed: date | None = None
    last_observed: date | None = None
    example_roles: dict[str, str] = field(default_factory=dict)


def suggested_canonical_label(surface_forms: list[str]) -> str:
    """The default proposed canonical label: the longest surface form in the
    cluster (ties broken alphabetically) — e.g. "Solvency II" over "SII".
    Mirrors the heuristic the pre-existing ProposalCard UI already used;
    centralised here so the backend and frontend never disagree."""
    if not surface_forms:
        return ""
    return sorted(surface_forms, key=lambda s: (-len(s), s))[0]


def _signals_for(ev: ClusterEvidence, current_year: int) -> ClusterSignals:
    return ClusterSignals(
        role_count=len(ev.role_ids),
        observation_count=ev.observation_count,
        year_count=len(ev.years),
        seniority_count=len(ev.seniority_levels),
        country_count=len(ev.countries),
        career_track_count=len(ev.career_tracks),
        most_recent_year=max(ev.years) if ev.years else None,
        current_year=current_year,
    )


def build_pending_cluster_index(cur, *, example_limit: int = LIST_EXAMPLE_ROLE_LIMIT) -> dict[str, ClusterEvidence]:
    """The full pending-cluster evidence map, keyed by cluster_key. Two
    queries, both over already-indexed/small tables (~1,525 pending
    proposals, ~4,700 unresolved observations in production) — see module
    docstring for why this is computed live rather than persisted."""
    cur.execute(
        """
        SELECT id, surface_form, cluster_key, suggested_type, nearest_concept_id, nearest_similarity
        FROM jobber.concept_proposal
        WHERE status = 'pending'
        ORDER BY surface_form
        """
    )
    evidence: dict[str, ClusterEvidence] = {}
    surface_form_to_cluster: dict[str, str] = {}
    for row in cur.fetchall():
        key = row["cluster_key"] or row["surface_form"]
        ev = evidence.setdefault(key, ClusterEvidence(cluster_key=key))
        if row["surface_form"] not in ev.surface_forms:
            ev.surface_forms.append(row["surface_form"])
        ev.proposal_ids.append(str(row["id"]))
        ev.suggested_type = ev.suggested_type or row["suggested_type"]
        if row["nearest_concept_id"] is not None and ev.nearest_concept_id is None:
            ev.nearest_concept_id = str(row["nearest_concept_id"])
            ev.nearest_similarity = row["nearest_similarity"]
        surface_form_to_cluster[row["surface_form"]] = key

    # Most-recent-first so the capped example_roles list is "recent postings
    # that used this term" rather than an arbitrary/incidental row order.
    cur.execute(
        """
        SELECT rso.surface_form, rso.role_instance_id,
               ri.posting_date, ri.country, ri.seniority_level, ri.career_track, ri.title
        FROM jobber.role_skill_observation rso
        JOIN jobber.role_instance ri ON ri.id = rso.role_instance_id
        WHERE rso.canonical_concept_id IS NULL
        ORDER BY ri.posting_date DESC NULLS LAST, rso.role_instance_id
        """
    )
    for row in cur.fetchall():
        key = surface_form_to_cluster.get(normalize_name(row["surface_form"]))
        ev = evidence.get(key) if key else None
        if ev is None:
            # A captured skill Pass B/the clustering pass hasn't keyed yet
            # (e.g. imported after the last bootstrap run) — not this
            # module's job to create its proposal; it simply carries no
            # cluster evidence yet, same tolerance `_group_proposals` (the
            # legacy queue) already has for live-count mismatches.
            continue
        role_id = str(row["role_instance_id"])
        ev.role_ids.add(role_id)
        ev.observation_count += 1
        posting_date = row["posting_date"]
        if posting_date is not None:
            ev.years.add(posting_date.year)
            ev.first_observed = posting_date if ev.first_observed is None else min(ev.first_observed, posting_date)
            ev.last_observed = posting_date if ev.last_observed is None else max(ev.last_observed, posting_date)
        if row["country"]:
            ev.countries.add(row["country"])
        if row["seniority_level"]:
            ev.seniority_levels.add(row["seniority_level"])
        if row["career_track"]:
            ev.career_tracks.add(row["career_track"])
        if role_id not in ev.example_roles and len(ev.example_roles) < example_limit:
            ev.example_roles[role_id] = row["title"]

    return evidence


def cluster_summary(ev: ClusterEvidence, *, current_year: int) -> dict:
    """The full "review representation" the brief's §1/§2/§4/§7/§8 ask for:
    grouped surface forms, coverage/temporal/geographic/seniority evidence,
    example roles, the deterministic priority score/band, and advisory
    noise/sparse flags — everything a curator needs without opening the
    database by hand."""
    signals = _signals_for(ev, current_year)
    score = compute_priority_score(signals)
    band = assign_priority_band(signals)
    flags = evidence_flags(signals) + noise_flags(ev.surface_forms)
    return {
        "cluster_key": ev.cluster_key,
        "status": "pending",
        "suggested_canonical_label": suggested_canonical_label(ev.surface_forms),
        "surface_forms": sorted(ev.surface_forms),
        "proposal_ids": ev.proposal_ids,
        "suggested_type": ev.suggested_type,
        "nearest_concept_id": ev.nearest_concept_id,
        "nearest_similarity": ev.nearest_similarity,
        "role_count": signals.role_count,
        "observation_count": signals.observation_count,
        "distinct_years": sorted(ev.years),
        "first_observed": ev.first_observed.isoformat() if ev.first_observed else None,
        "last_observed": ev.last_observed.isoformat() if ev.last_observed else None,
        "countries": sorted(ev.countries),
        "seniority_levels": sorted(ev.seniority_levels),
        "career_tracks": sorted(ev.career_tracks),
        "example_roles": [{"id": rid, "title": title} for rid, title in ev.example_roles.items()],
        "priority_score": round(score, 4),
        "priority_band": band,
        "flags": flags,
    }


def _resolved_cluster_rows(cur, statuses: list[str]) -> list[dict]:
    """Lighter-weight rows for clusters with no pending member left. Once
    resolved, a cluster's observations carry a non-NULL canonical_concept_id
    and drop out of the "unresolved" evidence query above, so the rich
    role/year/country breakdown isn't recomputed here — this is audit/
    history information (what happened, when, onto what concept), not an
    active review queue. Documented boundary, see module docstring."""
    cur.execute(
        """
        SELECT cp.surface_form, cp.cluster_key, cp.status, cp.resolved_concept_id, cp.resolved_at,
               cp.occurrence_count, c.canonical_name AS resolved_canonical_name
        FROM jobber.concept_proposal cp
        LEFT JOIN jobber.concept c ON c.id = cp.resolved_concept_id
        WHERE cp.status = ANY(%s)
        ORDER BY cp.surface_form
        """,
        (statuses,),
    )
    groups: dict[str, dict] = {}
    for row in cur.fetchall():
        key = row["cluster_key"] or row["surface_form"]
        g = groups.setdefault(
            key,
            {
                "cluster_key": key,
                "status": row["status"],
                "surface_forms": [],
                "resolved_concept_id": str(row["resolved_concept_id"]) if row["resolved_concept_id"] else None,
                "resolved_canonical_name": row["resolved_canonical_name"],
                "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
                "observation_count": 0,
                "role_count": None,
                "priority_score": None,
                "priority_band": None,
                "flags": [],
            },
        )
        if row["surface_form"] not in g["surface_forms"]:
            g["surface_forms"].append(row["surface_form"])
        g["observation_count"] += row["occurrence_count"] or 0
    for g in groups.values():
        g["suggested_canonical_label"] = suggested_canonical_label(g["surface_forms"])
        g["surface_forms"] = sorted(g["surface_forms"])
    return list(groups.values())


# --- queue: list / filter / sort / paginate (brief §3) ---------------------

_SORTS = ("priority", "occurrence", "role_count", "recent", "alphabetical")
_STATUSES = ("pending", "accepted", "rejected", "all")


def list_clusters(
    cur,
    *,
    status: str = "pending",
    q: str | None = None,
    min_role_count: int | None = None,
    min_observation_count: int | None = None,
    observed_from: str | None = None,
    observed_to: str | None = None,
    country: str | None = None,
    seniority: str | None = None,
    type_code: str | None = None,
    band: str | None = None,
    sort: str = "priority",
    limit: int = 20,
    offset: int = 0,
    current_year: int | None = None,
) -> dict:
    """Default view is `pending + highest priority first` (brief §3). Every
    filter/sort/pagination step happens here, server-side, over the full
    matching set — the caller (routes/vocabulary.py) only ever receives and
    forwards on one page. Evidence-based filters (min_role_count, band,
    country, seniority, type_code) only apply meaningfully to pending
    clusters (see `_resolved_cluster_rows`'s docstring for why accepted/
    rejected rows don't carry that evidence) and are ignored for other
    statuses."""
    if status not in _STATUSES:
        raise HTTPException(400, f"status must be one of {_STATUSES}")
    if sort not in _SORTS:
        raise HTTPException(400, f"sort must be one of {_SORTS}")
    current_year = current_year or datetime.now(timezone.utc).year

    rows: list[dict] = []
    if status in ("pending", "all"):
        evidence = build_pending_cluster_index(cur)
        rows.extend(cluster_summary(ev, current_year=current_year) for ev in evidence.values())
    if status == "accepted":
        rows.extend(_resolved_cluster_rows(cur, list(_RESOLVED_STATUSES_ACCEPTED)))
    elif status == "rejected":
        rows.extend(_resolved_cluster_rows(cur, ["rejected"]))
    elif status == "all":
        rows.extend(_resolved_cluster_rows(cur, [*_RESOLVED_STATUSES_ACCEPTED, "rejected", "deferred"]))

    if q:
        needle = q.strip().casefold()
        rows = [
            r for r in rows
            if needle in r["suggested_canonical_label"].casefold() or any(needle in sf.casefold() for sf in r["surface_forms"])
        ]
    if min_role_count is not None:
        rows = [r for r in rows if r["role_count"] is not None and r["role_count"] >= min_role_count]
    if min_observation_count is not None:
        rows = [r for r in rows if r["observation_count"] is not None and r["observation_count"] >= min_observation_count]
    if country:
        rows = [r for r in rows if "countries" in r and country in r["countries"]]
    if seniority:
        rows = [r for r in rows if "seniority_levels" in r and seniority in r["seniority_levels"]]
    if type_code:
        rows = [r for r in rows if r.get("suggested_type") == type_code]
    if band:
        rows = [r for r in rows if r["priority_band"] == band]
    if observed_from:
        rows = [r for r in rows if r.get("last_observed") and r["last_observed"] >= observed_from]
    if observed_to:
        rows = [r for r in rows if r.get("first_observed") and r["first_observed"] <= observed_to]

    total = len(rows)

    if sort == "priority":
        rows.sort(key=lambda r: (r["priority_score"] is None, -(r["priority_score"] or 0), -(r["role_count"] or 0), -(r["observation_count"] or 0), r["cluster_key"]))
    elif sort == "occurrence":
        rows.sort(key=lambda r: (-(r["observation_count"] or 0), r["cluster_key"]))
    elif sort == "role_count":
        rows.sort(key=lambda r: (-(r["role_count"] or 0), r["cluster_key"]))
    elif sort == "recent":
        rows.sort(key=lambda r: (r.get("last_observed") or "", r["cluster_key"]), reverse=True)
    else:
        rows.sort(key=lambda r: r["suggested_canonical_label"].casefold())

    page = rows[offset : offset + limit]
    return {"items": page, "total": total, "limit": limit, "offset": offset, "status": status, "sort": sort}


def get_cluster_detail(cur, cluster_key: str) -> dict | None:
    """The expanded review card (brief §4: "Allow expansion for more
    evidence/examples") — same computation as the list view but with a
    larger example-role cap. Falls back to the lighter resolved-cluster shape
    if the cluster has already been fully resolved; None if cluster_key is
    unknown entirely."""
    current_year = datetime.now(timezone.utc).year
    evidence = build_pending_cluster_index(cur, example_limit=DETAIL_EXAMPLE_ROLE_LIMIT)
    if cluster_key in evidence:
        return cluster_summary(evidence[cluster_key], current_year=current_year)
    for row in _resolved_cluster_rows(cur, [*_RESOLVED_STATUSES_ACCEPTED, "rejected", "deferred"]):
        if row["cluster_key"] == cluster_key:
            return row
    return None


# --- progress (brief §11) ---------------------------------------------------

def get_progress(cur) -> dict:
    cur.execute("SELECT COALESCE(cluster_key, surface_form) AS ck, status FROM jobber.concept_proposal")
    cluster_statuses: dict[str, set[str]] = {}
    for row in cur.fetchall():
        cluster_statuses.setdefault(row["ck"], set()).add(row["status"])

    pending = accepted = rejected = other = 0
    for statuses in cluster_statuses.values():
        if "pending" in statuses:
            pending += 1
        elif statuses <= set(_RESOLVED_STATUSES_ACCEPTED):
            accepted += 1
        elif statuses == {"rejected"}:
            rejected += 1
        else:
            other += 1

    current_year = datetime.now(timezone.utc).year
    evidence = build_pending_cluster_index(cur)
    high_priority_pending = sum(
        1 for ev in evidence.values() if assign_priority_band(_signals_for(ev, current_year)) == BAND_HIGH
    )

    cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation WHERE canonical_concept_id IS NOT NULL")
    observations_mapped = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL")
    observations_unresolved = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE status = 'active'")
    accepted_concepts = cur.fetchone()["n"]

    return {
        "total_clusters": len(cluster_statuses),
        "pending_clusters": pending,
        "accepted_clusters": accepted,
        "rejected_clusters": rejected,
        "other_status_clusters": other,
        "high_priority_pending_clusters": high_priority_pending,
        "accepted_concepts": accepted_concepts,
        "observations_mapped": observations_mapped,
        "observations_unresolved": observations_unresolved,
        # brief §10: elsewhere in the app, "0 confident matches" must never
        # be confused with "0 candidates because nothing has been curated
        # yet" — this flag is the one signal both this page and any other
        # canonical-vocabulary consumer should read to tell the two apart.
        "canonical_vocabulary_curated": accepted_concepts > 0,
    }


# --- cluster actions: accept / reject / merge (brief §5) --------------------

def _pending_surface_forms(cur, cluster_key: str) -> list[str]:
    cur.execute(
        "SELECT DISTINCT surface_form FROM jobber.concept_proposal "
        "WHERE status = 'pending' AND COALESCE(cluster_key, surface_form) = %s",
        (cluster_key,),
    )
    return [r["surface_form"] for r in cur.fetchall()]


def _cluster_resolution_state(cur, cluster_key: str) -> dict | None:
    """None while the cluster still has >=1 pending member (i.e. it is a
    live queue item). Otherwise a rollup of every member's resolution: the
    common (status, resolved_concept_id) when every member agrees — the
    normal case, since every action in this module resolves a cluster's
    members together in one transaction — else status='mixed' (only
    reachable if a member was separately resolved through the legacy
    single-surface-form endpoint outside this cluster-aware workflow)."""
    cur.execute(
        "SELECT DISTINCT status, resolved_concept_id FROM jobber.concept_proposal "
        "WHERE COALESCE(cluster_key, surface_form) = %s",
        (cluster_key,),
    )
    rows = cur.fetchall()
    if not rows or any(r["status"] == "pending" for r in rows):
        return None
    statuses = {r["status"] for r in rows}
    concept_ids = {str(r["resolved_concept_id"]) for r in rows if r["resolved_concept_id"] is not None}
    if len(statuses) == 1 and len(concept_ids) <= 1:
        return {"status": next(iter(statuses)), "resolved_concept_id": next(iter(concept_ids), None)}
    return {"status": "mixed", "resolved_concept_id": None}


def accept_cluster(
    cur, *, cluster_key: str, type_code: str, canonical_name: str, definition: str | None = None, now: datetime | None = None
) -> dict:
    """Accept the cluster as one new canonical concept (brief §5's "Accept").
    Idempotent: retrying after a successful accept returns the same result
    (`idempotent_replay: true`) instead of erroring or creating a second
    concept; retrying with a cluster that resolved to something *else*
    (e.g. it was rejected) is a 409, since silently overwriting a prior
    human decision would be worse than asking again explicitly."""
    now = now or datetime.now(timezone.utc)
    surface_forms = _pending_surface_forms(cur, cluster_key)
    if not surface_forms:
        prior = _cluster_resolution_state(cur, cluster_key)
        if prior and prior["status"] == "accepted_new":
            return {
                "cluster_key": cluster_key,
                "status": "accepted_new",
                "resolved_concept_id": prior["resolved_concept_id"],
                "surface_forms": [],
                "aliases_created": 0,
                "idempotent_replay": True,
            }
        if prior:
            raise HTTPException(409, f"cluster already resolved with status {prior['status']!r}, not accepted_new")
        raise HTTPException(404, "no pending proposals for this cluster")

    status, resolved_concept_id = resolve_surface_form_group(
        cur, surface_forms, action="accept_new", type_code=type_code, canonical_name=canonical_name,
        definition=definition, now=now,
    )
    return {
        "cluster_key": cluster_key,
        "status": status,
        "resolved_concept_id": resolved_concept_id,
        "surface_forms": surface_forms,
        "aliases_created": max(0, len(surface_forms) - 1),
        "idempotent_replay": False,
    }


def reject_cluster(cur, *, cluster_key: str, now: datetime | None = None) -> dict:
    """Reject the cluster coherently (brief §5's "Reject"): no concept/alias
    is ever created, and proposal/audit history is preserved (rows move to
    status='rejected', never deleted). Idempotent on retry."""
    now = now or datetime.now(timezone.utc)
    surface_forms = _pending_surface_forms(cur, cluster_key)
    if not surface_forms:
        prior = _cluster_resolution_state(cur, cluster_key)
        if prior and prior["status"] == "rejected":
            return {"cluster_key": cluster_key, "status": "rejected", "resolved_concept_id": None, "surface_forms": [], "idempotent_replay": True}
        if prior:
            raise HTTPException(409, f"cluster already resolved with status {prior['status']!r}, not rejected")
        raise HTTPException(404, "no pending proposals for this cluster")

    status, resolved_concept_id = resolve_surface_form_group(cur, surface_forms, action="reject", now=now)
    return {"cluster_key": cluster_key, "status": status, "resolved_concept_id": resolved_concept_id, "surface_forms": surface_forms, "idempotent_replay": False}


def merge_cluster(cur, *, cluster_key: str, concept_id: str, now: datetime | None = None) -> dict:
    """Merge the cluster into an already-accepted canonical concept (brief
    §5's "Merge into existing concept"): every surface form becomes an alias
    of `concept_id`, every matching observation is mapped onto it, and no
    second concept is ever created. Idempotent when retried with the *same*
    target concept; a 409 if it was already resolved onto a *different*
    concept or a different action."""
    now = now or datetime.now(timezone.utc)
    surface_forms = _pending_surface_forms(cur, cluster_key)
    if not surface_forms:
        prior = _cluster_resolution_state(cur, cluster_key)
        if prior and prior["status"] == "accepted_alias" and prior["resolved_concept_id"] == str(concept_id):
            return {
                "cluster_key": cluster_key, "status": "accepted_alias", "resolved_concept_id": concept_id,
                "surface_forms": [], "aliases_created": 0, "idempotent_replay": True,
            }
        if prior:
            raise HTTPException(409, f"cluster already resolved with status {prior['status']!r}, not merged into {concept_id!r}")
        raise HTTPException(404, "no pending proposals for this cluster")

    status, resolved_concept_id = resolve_surface_form_group(cur, surface_forms, action="accept_alias", concept_id=concept_id, now=now)
    return {
        "cluster_key": cluster_key, "status": status, "resolved_concept_id": resolved_concept_id,
        "surface_forms": surface_forms, "aliases_created": len(surface_forms), "idempotent_replay": False,
    }


# --- batch review (brief §6) ------------------------------------------------
#
# Batch accept/reject always takes an explicit list of cluster_keys (+ the
# accept parameters per cluster) chosen by the user — there is no "select
# all pending" or "accept every High-band cluster" helper anywhere in this
# module or its routes, by design (brief §6: "Do NOT add... automatic
# acceptance based solely on priority/frequency").

def preview_batch(cur, *, action: str, items: list[dict]) -> dict:
    """Read-only: the confirmation counts the brief §6 requires before a
    batch runs for real (clusters, resulting concepts, alias estimate,
    affected observations) — never writes anything."""
    if action not in ("accept", "reject"):
        raise HTTPException(400, "action must be accept or reject")
    if not items:
        raise HTTPException(400, "items must not be empty")

    evidence = build_pending_cluster_index(cur)
    found = 0
    resulting_concepts = 0
    aliases_estimate = 0
    observations_affected = 0
    missing: list[str] = []
    for item in items:
        ev = evidence.get(item["cluster_key"])
        if ev is None:
            missing.append(item["cluster_key"])
            continue
        found += 1
        observations_affected += ev.observation_count
        if action == "accept":
            resulting_concepts += 1
            aliases_estimate += max(0, len(ev.surface_forms) - 1)

    return {
        "action": action,
        "clusters_selected": len(items),
        "clusters_ready": found,
        "clusters_not_pending": missing,
        "resulting_concepts": resulting_concepts if action == "accept" else 0,
        "aliases_estimate": aliases_estimate if action == "accept" else 0,
        "observations_affected": observations_affected,
    }


def execute_batch(cur, *, action: str, items: list[dict], now: datetime | None = None) -> dict:
    """Executes every item in `items` using the caller's already-open
    `db_cursor()` transaction. **Whole-batch atomicity**: this function
    issues plain writes with no per-item SAVEPOINT, so the first exception
    (unknown/non-pending cluster_key, missing accept fields, a genuine DB
    error) propagates straight out of the `with db_cursor() as cur:` block
    the route handler holds — `db_cursor` rolls back the *entire* connection
    on any exception (app/db.py), so nothing from earlier items in the same
    batch is left committed either.

    This is the "clearly documented" per-cluster transactional semantics the
    brief (§14) requires before allowing anything less than all-or-nothing:
    a batch either fully applies or leaves the database exactly as it was —
    never a partially-accepted batch. This is deliberately simpler than the
    per-candidate SAVEPOINT pattern `vocabulary_bootstrap.persist_candidate_
    capabilities` uses for its own, differently-scoped batch (that one
    *wants* one bad candidate to not block the rest); a user-selected batch
    review action is a single deliberate unit of work, not an unattended
    bulk job, so failing it as one unit is the safer and simpler default."""
    now = now or datetime.now(timezone.utc)
    if action not in ("accept", "reject"):
        raise HTTPException(400, "action must be accept or reject")
    if not items:
        raise HTTPException(400, "items must not be empty")

    results = []
    for item in items:
        cluster_key = item["cluster_key"]
        if action == "accept":
            if not item.get("canonical_name") or not item.get("type_code"):
                raise HTTPException(400, f"cluster {cluster_key!r}: accept requires canonical_name and type_code")
            result = accept_cluster(
                cur, cluster_key=cluster_key, type_code=item["type_code"], canonical_name=item["canonical_name"],
                definition=item.get("definition"), now=now,
            )
        else:
            result = reject_cluster(cur, cluster_key=cluster_key, now=now)
        results.append(result)

    return {"action": action, "clusters_processed": len(results), "results": results}
