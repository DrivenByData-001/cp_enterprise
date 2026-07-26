import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from ..db import db_cursor, get_or_create_person
from ..models import EpisodeCreate, EpisodeUpdate

router = APIRouter(prefix="/api/episodes", tags=["episodes"])


# --- Derived date-span helpers -----------------------------------------------
#
# docs/11-capability-model-design.md §5.4 derives "years of experience" and
# "recency" per-concept, from episodes holding an accepted claim for that
# concept. Claims don't exist until Phase 2, so Phase 0 applies the same
# union-of-spans primitive one level up: across all episodes, with no concept
# filter. Nothing here is stored — it's recomputed on every read.

def _parse_partial_date(raw: str | None) -> date | None:
    """Parse an ISO date that may be YYYY, YYYY-MM, or YYYY-MM-DD."""
    if not raw:
        return None
    parts = raw.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _episode_span(ep: dict, today: date) -> tuple[date, date] | None:
    start = _parse_partial_date(ep.get("start_date"))
    if start is None:
        return None
    end = _parse_partial_date(ep.get("end_date")) or today
    if end < start:
        end = start
    return (start, end)


def _years_between(start: date, end: date) -> float:
    return round((end - start).days / 365.25, 2)


def _union_span_years(spans: list[tuple[date, date]]) -> float:
    """Total years covered by the union of date ranges — overlapping episodes
    (e.g. a project nested inside a job) must not double-count."""
    if not spans:
        return 0.0
    ordered = sorted(spans, key=lambda s: s[0])
    merged: list[tuple[date, date]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return round(sum((end - start).days for start, end in merged) / 365.25, 2)


def _with_derived(episodes: list[dict]) -> list[dict]:
    today = date.today()
    out = []
    for ep in episodes:
        span = _episode_span(ep, today)
        ep = {**ep, "duration_years": _years_between(*span) if span else None}
        out.append(ep)
    return out


# --- CRUD ---------------------------------------------------------------------

@router.get("")
def list_episodes():
    with db_cursor() as cur:
        person_id = get_or_create_person(cur)
        cur.execute(
            "SELECT * FROM episode WHERE person_id = ? ORDER BY start_date IS NULL, start_date",
            (person_id,),
        )
        episodes = [dict(r) for r in cur.fetchall()]
    return _with_derived(episodes)


@router.get("/timeline")
def get_timeline():
    """Career history ordered by date, plus derived spans (§5.4) — computed on
    every read, never stored."""
    with db_cursor() as cur:
        person_id = get_or_create_person(cur)
        cur.execute(
            "SELECT * FROM episode WHERE person_id = ? ORDER BY start_date IS NULL, start_date",
            (person_id,),
        )
        episodes = [dict(r) for r in cur.fetchall()]

    today = date.today()
    enriched = _with_derived(episodes)
    spans = [s for ep in episodes if (s := _episode_span(ep, today)) is not None]

    return {
        "episodes": enriched,
        "total_span_years": _union_span_years(spans),
        "earliest_start": min((s[0].isoformat() for s in spans), default=None),
        "latest_end": max((s[1].isoformat() for s in spans), default=None),
    }


@router.get("/{episode_id}")
def get_episode(episode_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM episode WHERE id = ?", (episode_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "episode not found")
    return _with_derived([dict(row)])[0]


def _validate_parent(cur, parent_id: int | None, person_id: int, self_id: int | None):
    if parent_id is None:
        return
    if parent_id == self_id:
        raise HTTPException(400, "an episode cannot be its own parent")
    cur.execute("SELECT person_id FROM episode WHERE id = ?", (parent_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(400, "parent_episode_id does not exist")
    if row["person_id"] != person_id:
        raise HTTPException(400, "parent_episode_id belongs to a different person")


@router.post("")
def create_episode(payload: EpisodeCreate):
    with db_cursor() as cur:
        person_id = get_or_create_person(cur)
        _validate_parent(cur, payload.parent_episode_id, person_id, None)
        cur.execute(
            """
            INSERT INTO episode
                (person_id, kind, title, organisation, start_date, end_date,
                 date_precision, parent_episode_id, domain_hint, context_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person_id,
                payload.kind,
                payload.title,
                payload.organisation,
                payload.start_date,
                payload.end_date,
                payload.date_precision,
                payload.parent_episode_id,
                payload.domain_hint,
                payload.context_note,
                datetime.utcnow().isoformat(),
            ),
        )
        episode_id = cur.lastrowid
    return {"id": episode_id, "status": "created"}


@router.put("/{episode_id}")
def update_episode(episode_id: int, payload: EpisodeUpdate):
    with db_cursor() as cur:
        cur.execute("SELECT person_id FROM episode WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "episode not found")
        person_id = row["person_id"]
        _validate_parent(cur, payload.parent_episode_id, person_id, episode_id)
        cur.execute(
            """
            UPDATE episode SET
                kind = ?, title = ?, organisation = ?, start_date = ?, end_date = ?,
                date_precision = ?, parent_episode_id = ?, domain_hint = ?, context_note = ?
            WHERE id = ?
            """,
            (
                payload.kind,
                payload.title,
                payload.organisation,
                payload.start_date,
                payload.end_date,
                payload.date_precision,
                payload.parent_episode_id,
                payload.domain_hint,
                payload.context_note,
                episode_id,
            ),
        )
    return {"id": episode_id, "status": "updated"}


@router.delete("/{episode_id}")
def delete_episode(episode_id: int):
    with db_cursor() as cur:
        try:
            cur.execute("DELETE FROM episode WHERE id = ?", (episode_id,))
        except sqlite3.IntegrityError:
            raise HTTPException(
                400, "cannot delete an episode that has other episodes nested under it — reassign or delete those first"
            )
        if cur.rowcount == 0:
            raise HTTPException(404, "episode not found")
    return {"status": "deleted"}
