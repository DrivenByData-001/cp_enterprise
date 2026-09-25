from fastapi import APIRouter, HTTPException, Query

from ..db import build_role_view, db_cursor, delete_role_instance, flatten_role_instance, upsert_role_instance
from ..document_processing import role_extraction_quality, role_extraction_quality_bulk
from ..embeddings import cosine_similarity, ensure_profile_embedding, get_embedding, get_embeddings
from ..models import JobPostingImport
from ..role_requirements import REQUIREMENT_EVIDENCE_SQL
from ..stepping_stones import path_to_target as _path_to_target
from .import_routes import posting_columns

router = APIRouter(prefix="/api/roles", tags=["roles"])


# docs/18 §3 (Dashboard temporal filter): the historical corpus (~2008-2025)
# must not drown out recent/current roles in the view checked day to day, but
# must remain explicitly, fully browsable — never hidden at the persistence
# layer (README's own pre-existing note on this, now implemented). "Recent"
# is a named, documented window, not a guess: the last DEFAULT_RECENT_YEARS
# calendar years, plus every role with no known posting_date at all (a
# freshly captured role with unknown/unset posting date is exactly the kind
# of "current" role this default must not hide).
DEFAULT_RECENT_YEARS = 3


@router.get("")
def list_roles(
    career_track: str | None = None,
    concept_id: str | None = None,
    min_similarity: float | None = None,
    sort: str | None = Query(None, pattern="^(similarity|posting_date|captured_at|title)$"),
    period: str = Query("current", pattern="^(current|recent|all|unknown_date)$"),
    year: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Server-side filtered, sorted, and paginated (brief: "every role loaded
    into the browser" must not be required). Temporal precedence: an
    explicit `year` or `date_from`/`date_to` always wins; otherwise `period`
    decides ('current' — the default, the everyday operational view — or
    'recent'/'all'/'unknown_date'). Pagination is applied last, after
    similarity is computed and the full matching set is sorted — see the
    comment above the slice below for why that's still "server-side"
    pagination in the sense that matters (the browser never receives
    unpaginated rows), even though the DB query itself isn't LIMIT/OFFSET'd:
    at this corpus's scale (~300 roles) an in-memory sort after a SQL-side
    similarity-independent filter is simpler and no less correct than
    pushing cosine ranking into SQL, and every filter that *can* run in SQL
    (track, concept, temporal) already does.

    `sort` defaults to `None` rather than a fixed literal specifically so an
    explicit query parameter can be told apart from "the caller didn't ask" —
    the Save-checkpoint/Current-roles brief's own default-sort behaviour
    (newest/recently-captured first under 'current', §6.3) only applies when
    the caller truly omitted it; passing `sort=similarity` explicitly always
    wins, same as any other explicit temporal/sort parameter.
    """
    with db_cursor() as cur:
        _, profile_vec = ensure_profile_embedding(cur)

        filters = ""
        params: list = []
        if career_track:
            filters += " AND ri.career_track = %s"
            params.append(career_track)
        if concept_id is not None:
            filters += " AND ri.id IN (SELECT role_instance_id FROM (" + REQUIREMENT_EVIDENCE_SQL + ") evidence WHERE concept_id = %s AND concept_status = 'active')"
            params.append(concept_id)

        applied_period = "all"
        if year is not None:
            filters += " AND EXTRACT(YEAR FROM ri.posting_date) = %s"
            params.append(year)
            applied_period = "year"
        elif date_from or date_to:
            if date_from:
                filters += " AND ri.posting_date >= %s"
                params.append(date_from)
            if date_to:
                filters += " AND ri.posting_date <= %s"
                params.append(date_to)
            applied_period = "range"
        elif period == "current":
            # The default, everyday operational view (Save-checkpoint /
            # Current-roles brief §6.1): a role counts as "current" when its
            # own posting year is this calendar year, or — only when no
            # posting date was ever captured for it — its linked source
            # document was itself captured this calendar year. This decides
            # *inclusion* only; it never substitutes captured_at into
            # posting_date, and a dated-but-old role is never pulled in just
            # because it happens to have been recaptured recently.
            filters += (
                " AND (EXTRACT(YEAR FROM ri.posting_date) = EXTRACT(YEAR FROM CURRENT_DATE)"
                " OR (ri.posting_date IS NULL AND EXTRACT(YEAR FROM d.captured_at) = EXTRACT(YEAR FROM CURRENT_DATE)))"
            )
            applied_period = "current"
        elif period == "unknown_date":
            # A named, explicit filter for "posting date was never captured"
            # (docs: source-aware ingest cleanup, problem #8) — distinct from
            # 'recent', which also includes unknown-date roles alongside
            # genuinely recent ones. This shows *only* the unknown-date rows,
            # so they're findable on their own rather than merely not hidden.
            filters += " AND ri.posting_date IS NULL"
            applied_period = "unknown_date"
        elif period == "recent":
            filters += " AND (ri.posting_date IS NULL OR ri.posting_date >= (CURRENT_DATE - (%s || ' years')::interval))"
            params.append(DEFAULT_RECENT_YEARS)
            applied_period = "recent"

        cur.execute(
            "SELECT ri.*, d.url AS url, d.captured_at AS captured_at FROM jobber.role_instance ri "
            "LEFT JOIN jobber.document d ON d.id = ri.document_id "
            "WHERE ri.instance_type = 'observed_posting'" + filters,
            params,
        )
        rows = [flatten_role_instance(r) for r in cur.fetchall()]

        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in rows])
        quality_by_id = role_extraction_quality_bulk(cur, [r["id"] for r in rows])

        cur.execute(
            "SELECT MIN(EXTRACT(YEAR FROM posting_date)) AS min_year, MAX(EXTRACT(YEAR FROM posting_date)) AS max_year "
            "FROM jobber.role_instance WHERE instance_type = 'observed_posting' AND posting_date IS NOT NULL"
        )
        year_bounds = cur.fetchone()

    for r in rows:
        r["similarity"] = cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None
        r["extraction_quality"] = quality_by_id.get(r["id"])

    if min_similarity is not None:
        rows = [r for r in rows if r["similarity"] is not None and r["similarity"] >= min_similarity]

    # An explicit `sort` always wins (brief §6.3/§13). Only when the caller
    # truly omitted it does Current's own default apply: newest/recently-
    # captured first, not similarity — a role the user just saved must show
    # up immediately near the top rather than wherever it happens to rank
    # against the profile.
    use_current_recency_default = sort is None and applied_period == "current"
    effective_sort = sort or ("captured_at" if applied_period == "current" else "similarity")

    if use_current_recency_default:
        # captured_at is the primary key; a row with no linked document (so
        # no captured_at at all — legacy/bulk-imported roles) falls back to
        # its own posting_date rather than being stranded at a meaningless
        # position, and a row with neither sorts last. Plain descending
        # string comparison already gives all three: two real ISO-shaped
        # date/timestamp strings compare chronologically, and "" (neither
        # value present) sorts after every real value once reversed.
        rows.sort(key=lambda r: str(r.get("captured_at") or r.get("posting_date") or ""), reverse=True)
    elif effective_sort == "similarity":
        rows.sort(key=lambda r: (r["similarity"] is None, -(r["similarity"] or 0)))
    elif effective_sort in ("posting_date", "captured_at", "title"):
        rows.sort(key=lambda r: (r.get(effective_sort) is None, str(r.get(effective_sort) or "")), reverse=(effective_sort != "title"))

    total = len(rows)
    page = rows[offset : offset + limit]

    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "period": applied_period,
        "year_range": (
            {"min": int(year_bounds["min_year"]), "max": int(year_bounds["max_year"])}
            if year_bounds and year_bounds["min_year"] is not None
            else None
        ),
    }


@router.get("/{role_id}")
def get_role(role_id: str):
    """2026 Role Detail regression (fixed here): a role captured via the
    source-aware ingest + requirement-extraction pipeline
    (routes/role_instances.py::_ingest_raw, extraction.extract_role_requirements)
    never gets role_skill_observation rows or flat description/requirements/
    responsibilities columns populated — that pipeline's evidence lives in
    jobber.requirement_claim (concept-linked) and the linked document's own
    verbatim content_text instead. Such a role still listed fine on the
    Dashboard (which never touches those fields) but rendered an almost-empty
    Role Detail, despite real captured evidence existing all along. Both
    fallbacks below are additive — they only ever fill in evidence that is
    otherwise completely absent, never override/hide data the older
    JobPostingImport-shaped pipeline already populates."""
    with db_cursor() as cur:
        role = build_role_view(cur, role_id)
        if role is None:
            raise HTTPException(404, "role not found")
        role.pop("_source_document_id", None)

        _, profile_vec = ensure_profile_embedding(cur)
        role_vec = get_embedding(cur, "role_instance", role_id)

        if role["node_type"] != "posting":
            role["path"] = _path_to_target(cur, role_id, role_vec, profile_vec)

        # The authoritative ok/partial signal (docs/18 §5) — None for a role
        # never processed through this pipeline (legacy/bulk import, hand
        # edit); the frontend falls back to role['extraction_status'] then.
        role["extraction_quality"] = role_extraction_quality(cur, role_id)

    role["similarity"] = cosine_similarity(profile_vec, role_vec) if profile_vec else None
    return role


@router.put("/{role_id}")
def update_role(role_id: str, payload: JobPostingImport):
    with db_cursor() as cur:
        cur.execute("SELECT instance_type FROM jobber.role_instance WHERE id = %s", (role_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "role not found")
    if row["instance_type"] != "observed_posting":
        raise HTTPException(400, "this is a target role — edit it via PUT /api/targets/{id}")

    skills = [s.model_dump() for s in payload.skills]
    with db_cursor() as cur:
        columns = posting_columns(cur, payload)
        embedding_text = columns.pop("_embedding_text")
        upsert_role_instance(cur, role_id, columns, skills)
        if embedding_text:
            from ..embeddings import embed_text, set_embedding

            vector = embed_text(embedding_text)
            if vector:
                set_embedding(cur, "role_instance", role_id, vector)
    return {"id": role_id, "status": "updated"}


@router.delete("/{role_id}")
def delete_role(role_id: str):
    with db_cursor() as cur:
        if not delete_role_instance(cur, role_id):
            raise HTTPException(404, "role not found")
    return {"status": "deleted"}
