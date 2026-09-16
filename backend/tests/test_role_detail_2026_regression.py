"""2026 Role Detail rendering regression (next-build brief §5).

Root cause: `GET /api/roles/{role_id}` only ever read skills from
`jobber.role_skill_observation` and the advert text from
`role_instance.description/requirements/responsibilities`. Those are the
shape `posting_role_columns`/`JobPostingImport` produces (the historical
corpus, and every `/api/import*` path). But the source-aware capture path
(`POST /api/role-instances/ingest` + `POST /api/role-instances/{id}/
extract-requirements`, app/routes/role_instances.py) creates a role with
*none* of those columns populated — its evidence lives in
`jobber.requirement_claim` (concept-linked) and the linked document's own
`content_text` instead. A role captured this way — the shape a freshly
captured 2026 posting like the MetLife Dublin Senior Actuary role actually
has — listed fine on the Dashboard (which never touches those fields) but
opened to an almost-empty Role Detail: no advert text, no skills, despite
real captured evidence existing in the database all along.

This fixture reproduces that shape directly (not by re-extracting the real
production row — no production data is touched) and proves the endpoint
now surfaces that evidence instead of silently omitting it."""

from datetime import date

from app import db, extraction
from app.models import RequirementExtractionResult, RequirementItem

METLIFE_BODY = (
    "Senior Actuary — MetLife Europe\n"
    "Dublin, 20 Lower Hatch Street\n\n"
    "This role works closely with the Head of Actuarial Function on Solvency II "
    "reporting, ORSA and IFRS 17. You will prepare Board reporting materials and "
    "represent the function in CBI regulatory interactions. This role is intended "
    "as a pathway toward Head of Actuarial Function.\n\n"
    "Strong stakeholder management skills are essential."
)

_REQUIREMENTS = [
    ("Solvency II", "Solvency II reporting", "knowledge"),
    ("ORSA", "ORSA", "knowledge"),
    ("IFRS 17", "IFRS 17", "knowledge"),
    ("Stakeholder management", "stakeholder management", "method"),
]


def _make_concepts(cur) -> None:
    for name, _span, type_code in _REQUIREMENTS:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES (%s, %s, 'active', 'curator', now())",
            (type_code, name),
        )


def _seed_source_aware_role(cur) -> str:
    """The exact shape app/routes/role_instances.py::_ingest_raw produces:
    a document holding the verbatim advert, and a bare role_instance with no
    description/requirements/responsibilities/legacy_analysis/legacy_scores
    at all — deliberately not going through posting_role_columns, since that
    is precisely what distinguishes this pipeline from the one Role Detail
    was originally built against."""
    document_id, _ = db.create_document(
        cur, kind="job_posting", content_text=METLIFE_BODY, provenance_quality="original",
        title="Senior Actuary", source="user_paste",
    )
    role_id = db.upsert_role_instance(
        cur, None,
        {
            "instance_type": "observed_posting",
            "target_basis": None,
            "document_id": document_id,
            "title": "Senior Actuary",
            "organisation": "MetLife Europe",
            "location": "Dublin, 20 Lower Hatch Street",
            "posting_date": date(2026, 5, 27),
        },
        skills=[],
    )
    return role_id


def _run_requirement_extraction(cur, role_id: str, monkeypatch) -> None:
    def _dispatch(*, task, prompt_name, user_input, output_model):
        from app import ai

        requirements = [
            RequirementItem(surface_form=name, requirement_type="required", basis="stated", evidence_span=span)
            for name, span, _type in _REQUIREMENTS
        ]
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=RequirementExtractionResult(requirements=requirements), run=run)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)
    result = extraction.extract_role_requirements(cur, role_id)
    assert result["status"] == "ok"
    assert result["claims_created"] == len(_REQUIREMENTS)


def test_2026_style_role_appears_in_list_endpoint(client, monkeypatch):
    with db.db_cursor() as cur:
        _make_concepts(cur)
        role_id = _seed_source_aware_role(cur)
        _run_requirement_extraction(cur, role_id, monkeypatch)

    page = client.get("/api/roles", params={"period": "all", "limit": 50}).json()
    ids = {r["id"] for r in page["items"]}
    assert role_id in ids


def test_2026_style_role_renders_with_its_real_evidence_via_detail_endpoint(client, monkeypatch):
    """The actual regression: before the fix, `skills` was `[]` and no advert
    text was present at all, even though this role has real captured
    evidence — a requirement_claim per concept, and the document's own
    verbatim text. Role Detail must surface both — but (requirement-review
    curation gate) only once that evidence has actually been reviewed and
    accepted; freshly extracted, unreviewed claims must not appear as if
    they were established "skills" on the very page that also carries the
    "Requirements review pending" indicator saying otherwise."""
    with db.db_cursor() as cur:
        _make_concepts(cur)
        role_id = _seed_source_aware_role(cur)
        _run_requirement_extraction(cur, role_id, monkeypatch)

    # Before review: no role_skill_observation rows for this role and every
    # requirement_claim is still unreviewed, so skills must be empty rather
    # than silently presenting unreviewed AI proposals as established facts
    # — and the review-pending indicator must say so.
    before = client.get(f"/api/roles/{role_id}").json()
    assert before["skills"] == []
    assert before["requirement_review"] == {
        "accepted": 0, "unreviewed": len(_REQUIREMENTS), "rejected": 0,
        "unresolved_proposals": 0, "extraction_attempted": True, "needs_reextraction": 0, "complete": False,
    }

    claims = client.get(f"/api/role-instances/{role_id}/requirements").json()["items"]
    assert len(claims) == len(_REQUIREMENTS)
    for claim in claims:
        accept_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim['id']}/accept")
        assert accept_resp.status_code == 200

    resp = client.get(f"/api/roles/{role_id}")
    assert resp.status_code == 200
    role = resp.json()

    assert role["title"] == "Senior Actuary"
    assert role["node_type"] == "posting"
    assert role["requirement_review"] == {
        "accepted": len(_REQUIREMENTS), "unreviewed": 0, "rejected": 0,
        "unresolved_proposals": 0, "extraction_attempted": True, "needs_reextraction": 0, "complete": True,
    }

    # Skills: none in role_skill_observation for this role — must fall back
    # to accepted requirement_claim evidence rather than silently rendering
    # empty (or, before the curation gate, an unreviewed proposal).
    skill_names = {s["name"] for s in role["skills"]}
    assert skill_names == {name for name, _s, _t in _REQUIREMENTS}
    assert all(s["resolved_concept_id"] for s in role["skills"])

    # Advert text: role_instance.description/requirements/responsibilities
    # are all NULL for this pipeline — the endpoint must fall back to the
    # linked document's own verbatim content rather than showing nothing.
    assert not role.get("description")
    assert not role.get("requirements")
    assert not role.get("responsibilities")
    assert role.get("source_document_text") == METLIFE_BODY


def test_older_role_skill_observation_shaped_posting_still_renders(client):
    """The pre-existing shape (JobPostingImport-derived: role_skill_observation
    + flat description/requirements columns) must keep rendering, evidence
    intact — this is the "older historical role still works" guard (brief
    §5.4). Its skill now surfaces as `legacy_skills` rather than `skills`
    (round-3 code-review follow-up, docs/29 §13): this role has no
    requirement_claim review history at all, so labelling its evidence
    "reviewed" would overstate it — but the evidence itself is exactly as
    present as before."""
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur, None,
            {
                "instance_type": "observed_posting",
                "title": "Pricing Actuary",
                "description": "A historical posting with its own description.",
                "requirements": "Actuarial exams.",
                "posting_date": date(2019, 3, 1),
            },
            skills=[{"name": "Pricing", "category": "method", "importance": 4, "requirement_type": "required"}],
        )

    resp = client.get(f"/api/roles/{role_id}")
    assert resp.status_code == 200
    role = resp.json()
    assert role["description"] == "A historical posting with its own description."
    assert role["skills"] == []
    assert [s["name"] for s in role["legacy_skills"]] == ["Pricing"]
    # The fallback must never kick in (and never overwrite/duplicate) when
    # the role already has real role_skill_observation evidence.
    assert role.get("source_document_text") is None


def test_target_role_still_renders(client):
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur, None,
            {"instance_type": "user_defined_target", "target_basis": "real_role", "title": "Head of Actuarial Function"},
            skills=[],
        )

    resp = client.get(f"/api/roles/{role_id}")
    assert resp.status_code == 200
    assert resp.json()["node_type"] == "target_real"
