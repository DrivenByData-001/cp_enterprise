"""Profile360 mapping as a human-curation workflow (claims AND capabilities):
candidate display, curator mapping, full-vocabulary search, vocabulary
proposals with provenance, and mapping-state semantics."""

import pytest

from app import ai, db, extraction
from app.ai import AITaskError
from app.models import ClaimMappingResult

_KINDS = {
    # kind: (url segment, mapping table, source col, concept col, concept type used for candidates)
    "claim": ("claims", "profile360_claim_mapping", "profile360_claim_id", "jobber_concept_id"),
    "capability": ("capabilities", "profile360_capability_mapping", "profile360_capability_id", "jobber_capability_concept_id"),
}
BOTH = pytest.mark.parametrize("kind", ["claim", "capability"])


def _concept(cur, name, type_code="capability", definition=None):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name, definition),
    )
    return str(cur.fetchone()["id"])


def _subject(cur, kind, text):
    if kind == "claim":
        cur.execute("INSERT INTO profile360.claims (claim_text, evidence_class) VALUES (%s, 'stated') RETURNING id", (text,))
    else:
        cur.execute("INSERT INTO profile360.capabilities (name) VALUES (%s) RETURNING id", (text,))
    return str(cur.fetchone()["id"])


def _fake_run(output, task="t", prompt_name="p"):
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="v",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=1, output_chars=1,
    )
    return ai.AITaskResult(output=output, run=run)


def _ai_returns(monkeypatch, **fields):
    monkeypatch.setattr(extraction, "run_json_task", lambda *, task, prompt_name, user_input, output_model:
                        _fake_run(ClaimMappingResult(**fields), task, prompt_name))


def _url(kind, sid, tail=""):
    return f"/api/profile360/{_KINDS[kind][0]}/{sid}{tail}"


def _rows(cur, kind, sid):
    _, table, scol, ccol = _KINDS[kind]
    cur.execute(f"SELECT {ccol} AS cid, mapping_basis, review_status FROM jobber.{table} WHERE {scol} = %s", (sid,))
    return {str(r["cid"]): r for r in cur.fetchall()}


def _workbench(client, kind, sid):
    r = client.get(_url(kind, sid, "/mapping"))
    assert r.status_code == 200, r.text
    return r.json()


def _seed(kind, n=3):
    with db.db_cursor() as cur:
        ids = [_concept(cur, f"Concept {i}", definition=f"Definition of concept {i}") for i in range(n)]
        sid = _subject(cur, kind, "Concept 0 work")
    return sid, ids


# --- candidates -------------------------------------------------------------

@BOTH
def test_candidates_returned_with_definitions_similarity_and_ai_recommendation(client, monkeypatch, kind):
    sid, ids = _seed(kind, n=3)
    _ai_returns(monkeypatch, chosen_canonical_name="Concept 1", reasoning="best fit")
    res = client.post(_url(kind, sid, "/map")).json()
    assert res["mapped"] is True and len(res["candidates"]) == 3

    wb = _workbench(client, kind, sid)
    assert wb["ai_outcome"] == "recommended"
    cands = wb["candidates"]
    assert [c["rank"] for c in cands] == [1, 2, 3]
    assert {c["concept_id"] for c in cands} == set(ids)
    assert all(c["definition"].startswith("Definition of concept") for c in cands)
    assert all(c["type_code"] == "capability" and isinstance(c["similarity"], float) for c in cands)
    selected = [c for c in cands if c["ai_selected"]]
    assert [c["canonical_name"] for c in selected] == ["Concept 1"]
    # the AI recommendation is an unreviewed mapping, linked to its candidate
    assert selected[0]["mapping_status"] == "unreviewed" and selected[0]["mapping_id"]
    assert wb["latest_run"]["reasoning"] == "best fit"


@BOTH
def test_at_most_ten_candidates(client, monkeypatch, kind):
    with db.db_cursor() as cur:
        for i in range(14):
            _concept(cur, f"Many {i}")
        sid = _subject(cur, kind, "Many")
    _ai_returns(monkeypatch, chosen_canonical_name="Many 3")
    assert len(client.post(_url(kind, sid, "/map")).json()["candidates"]) == 10
    assert len(_workbench(client, kind, sid)["candidates"]) == 10


@BOTH
def test_failed_ai_run_still_exposes_candidates(client, monkeypatch, kind):
    sid, _ = _seed(kind)

    def boom(**_):
        raise AITaskError("provider down")

    monkeypatch.setattr(extraction, "run_json_task", boom)
    assert client.post(_url(kind, sid, "/map")).json()["status"] == "failed"
    wb = _workbench(client, kind, sid)
    assert wb["ai_outcome"] == "failed" and len(wb["candidates"]) == 3
    assert not any(c["ai_selected"] for c in wb["candidates"])


# --- curator mapping --------------------------------------------------------

@BOTH
def test_user_can_pick_a_different_candidate_than_the_ai(client, monkeypatch, kind):
    sid, ids = _seed(kind)
    _ai_returns(monkeypatch, chosen_canonical_name="Concept 1")
    client.post(_url(kind, sid, "/map"))
    other = next(c for c in _workbench(client, kind, sid)["candidates"] if not c["ai_selected"])

    r = client.post(_url(kind, sid, "/mappings"), json={"concept_id": other["concept_id"]})
    assert r.status_code == 200
    with db.db_cursor() as cur:
        rows = _rows(cur, kind, sid)
    assert rows[other["concept_id"]]["mapping_basis"] == "curator_asserted"
    assert rows[other["concept_id"]]["review_status"] == "accepted"
    # the AI's own recommendation is untouched — human review is the gate
    ai_row = next(v for k, v in rows.items() if k != other["concept_id"])
    assert (ai_row["mapping_basis"], ai_row["review_status"]) == ("ai_suggested", "unreviewed")


@BOTH
def test_manual_mapping_works_without_any_ai_run(client, kind):
    sid, ids = _seed(kind)
    assert client.post(_url(kind, sid, "/mappings"), json={"concept_id": ids[0]}).status_code == 200
    with db.db_cursor() as cur:
        assert _rows(cur, kind, sid)[ids[0]]["review_status"] == "accepted"


@BOTH
def test_full_vocabulary_search_finds_concept_outside_top_candidates(client, monkeypatch, kind):
    with db.db_cursor() as cur:
        for i in range(12):
            _concept(cur, f"Filler {i}")
        far = _concept(cur, "Zebra stewardship", definition="Looking after striped animals")
        cur.execute("SELECT id FROM jobber.concept WHERE id <> %s ORDER BY canonical_name", (far,))
        near = [str(r["id"]) for r in cur.fetchall()]
        sid = _subject(cur, kind, "Filler work")
    # Pin retrieval (the test embeddings are hash-seeded, so the real ranking
    # is arbitrary): the 12 fillers rank first, 'far' is below the cut-off.
    from app import profile360_mapping as pm
    monkeypatch.setattr(pm, "nearest_concepts", lambda cur, text, limit=10, type_codes=None: [
        (cid, 0.9 - i / 100) for i, cid in enumerate(near + [far])][:limit])
    _ai_returns(monkeypatch, chosen_canonical_name=None, reasoning="none fit")
    client.post(_url(kind, sid, "/map"))
    top = {c["concept_id"] for c in _workbench(client, kind, sid)["candidates"]}
    assert len(top) == 10 and far not in top  # the 10 suggestions are not the whole vocabulary

    hits = client.get("/api/profile360/vocabulary/search", params={"q": "zebra", "kind": kind}).json()
    assert [h["id"] for h in hits] == [far]
    assert hits[0]["definition"] == "Looking after striped animals"
    # definition-only matches are searchable too
    assert client.get("/api/profile360/vocabulary/search", params={"q": "striped", "kind": kind}).json()[0]["id"] == far

    assert client.post(_url(kind, sid, "/mappings"), json={"concept_id": far}).status_code == 200
    with db.db_cursor() as cur:
        assert _rows(cur, kind, sid)[far]["review_status"] == "accepted"


def test_capability_search_and_mapping_restricted_to_capability_concepts(client):
    with db.db_cursor() as cur:
        tool = _concept(cur, "Python tooling", type_code="tool")
        _concept(cur, "Python stewardship", type_code="capability")
        cap_id = _subject(cur, "capability", "Python things")
        claim_id = _subject(cur, "claim", "Python things")
    names = [h["canonical_name"] for h in client.get("/api/profile360/vocabulary/search", params={"q": "python", "kind": "capability"}).json()]
    assert names == ["Python stewardship"]
    both = client.get("/api/profile360/vocabulary/search", params={"q": "python", "kind": "claim"}).json()
    assert len(both) == 2
    assert client.post(_url("capability", cap_id, "/mappings"), json={"concept_id": tool}).status_code == 400
    assert client.post(_url("claim", claim_id, "/mappings"), json={"concept_id": tool}).status_code == 200


# --- many-to-many, rejection, visibility, states ----------------------------

@BOTH
def test_multiple_mappings_rejection_does_not_block_remap_and_mappings_stay_visible(client, kind):
    sid, ids = _seed(kind)
    assert client.post(_url(kind, sid, "/mappings"), json={"concept_id": ids[0]}).status_code == 200
    assert client.post(_url(kind, sid, "/mappings"), json={"concept_id": ids[1]}).status_code == 200  # additional mapping

    wb = _workbench(client, kind, sid)
    assert wb["mapping_state"] == "mapped"
    assert {m["concept_id"] for m in wb["mappings"]} == {ids[0], ids[1]}
    assert all(m["definition"] and m["review_status"] == "accepted" for m in wb["mappings"])

    first = next(m for m in wb["mappings"] if m["concept_id"] == ids[0])
    client.post(f"/api/profile360/mappings/{first['id']}/review", json={"kind": kind, "action": "reject"})
    wb = _workbench(client, kind, sid)
    assert {m["concept_id"]: m["review_status"] for m in wb["mappings"]} == {ids[0]: "rejected", ids[1]: "accepted"}

    # remapping the rejected pair re-opens it as curator_asserted/accepted
    assert client.post(_url(kind, sid, "/mappings"), json={"concept_id": ids[0]}).status_code == 200
    with db.db_cursor() as cur:
        row = _rows(cur, kind, sid)[ids[0]]
    assert (row["mapping_basis"], row["review_status"]) == ("curator_asserted", "accepted")


@BOTH
def test_one_concept_can_map_many_items_and_mapping_is_idempotent(client, kind):
    with db.db_cursor() as cur:
        concept = _concept(cur, "Shared")
        a, b = _subject(cur, kind, "a"), _subject(cur, kind, "b")
    for sid in (a, b, a):
        assert client.post(_url(kind, sid, "/mappings"), json={"concept_id": concept}).status_code == 200
    with db.db_cursor() as cur:
        assert len(_rows(cur, kind, a)) == len(_rows(cur, kind, b)) == 1


@BOTH
def test_mapped_items_remain_listable_and_state_filters_work(client, kind):
    seg, _, scol, _ = _KINDS[kind][0], *_KINDS[kind][1:]
    with db.db_cursor() as cur:
        c = _concept(cur, "C")
        plain, pending, mapped, rejected = (_subject(cur, kind, t) for t in ("plain", "pending", "mapped", "rejected"))
    # pending via AI-style unreviewed row; mapped via curator; rejected-only
    from app import profile360_mapping as pm
    with db.db_cursor() as cur:
        pm.upsert_mapping(cur, kind, pending, c, basis="ai_suggested", review_status="unreviewed")
        pm.upsert_mapping(cur, kind, mapped, c, basis="curator_asserted", review_status="accepted")
        pm.upsert_mapping(cur, kind, rejected, c, basis="ai_suggested", review_status="rejected")

    def ids(state):
        r = client.get(f"/api/profile360/{seg}", params={"mapping_state": state, "limit": 100})
        assert r.status_code == 200
        return {x["id"]: x["_mapping_state"] for x in r.json()}

    assert set(ids("unmapped")) == {plain, rejected}  # rejected-only stays eligible
    assert set(ids("pending")) == {pending}
    assert set(ids("mapped")) == {mapped}  # inspectable independently of the Unmapped queue
    assert set(ids("all")) == {plain, pending, mapped, rejected}
    assert ids("all")[mapped] == "mapped"
    assert client.get(f"/api/profile360/{seg}", params={"mapping_state": "bogus"}).status_code == 400


# --- AI declines / missing vocabulary ---------------------------------------

@BOTH
def test_ai_can_decline_all_candidates_and_offer_a_proposal_without_creating_vocabulary(client, monkeypatch, kind):
    sid, _ = _seed(kind)
    _ai_returns(
        monkeypatch, chosen_canonical_name=None, reasoning="nothing fits", no_adequate_concept=True,
        proposed_canonical_name="Model Risk Appetite Setting", proposed_type_code="capability",
        proposed_definition="Defining the tolerance for model risk.",
    )
    res = client.post(_url(kind, sid, "/map")).json()
    assert res["mapped"] is False and res["reason"] == "declined_all_candidates" and res["no_adequate_concept"] is True
    assert res["proposal"]["created_proposal"] is True

    with db.db_cursor() as cur:
        assert _rows(cur, kind, sid) == {}  # no mapping forced
        cur.execute("SELECT 1 FROM jobber.concept WHERE canonical_name ILIKE 'model risk appetite setting'")
        assert cur.fetchone() is None  # NOT silently canonical
        cur.execute("SELECT * FROM jobber.concept_proposal WHERE surface_form = 'model risk appetite setting'")
        prop = cur.fetchone()
        assert (prop["status"], prop["suggested_type"], prop["suggested_definition"]) == (
            "pending", "capability", "Defining the tolerance for model risk.")
        assert prop["nearest_concept_id"] is not None and prop["nearest_similarity"] is not None
        assert prop["extraction_run_id"] is not None

    wb = _workbench(client, kind, sid)
    assert wb["ai_outcome"] == "declined_all_candidates"
    p = wb["proposals"][0]
    assert (p["origin"], p["status"], p["suggested_definition"]) == ("ai", "pending", "Defining the tolerance for model risk.")
    assert p["source_text"] and p["nearest_canonical_name"]


@BOTH
def test_ai_proposal_ignored_when_it_also_chose_a_candidate(client, monkeypatch, kind):
    sid, _ = _seed(kind)
    _ai_returns(monkeypatch, chosen_canonical_name="Concept 1", proposed_canonical_name="Something New")
    assert client.post(_url(kind, sid, "/map")).json()["mapped"] is True
    assert _workbench(client, kind, sid)["proposals"] == []


@BOTH
def test_curator_can_propose_new_vocabulary_as_pending_with_provenance(client, kind):
    sid, _ = _seed(kind)
    type_code = "capability"
    r = client.post(_url(kind, sid, "/propose-vocabulary"), json={
        "canonical_name": "Brand New Thing", "type_code": type_code, "definition": "A genuinely new idea."})
    assert r.status_code == 200, r.text
    with db.db_cursor() as cur:
        cur.execute("SELECT id, status, suggested_type FROM jobber.concept_proposal WHERE surface_form = 'brand new thing'")
        prop = cur.fetchone()
        assert prop["status"] == "pending" and prop["suggested_type"] == type_code
        cur.execute("SELECT * FROM jobber.concept WHERE canonical_name ILIKE 'brand new thing'")
        assert cur.fetchone() is None
        cur.execute("SELECT * FROM jobber.concept_proposal_profile360_source WHERE concept_proposal_id = %s", (prop["id"],))
        src = cur.fetchone()
        assert (src["source_kind"], str(src["source_id"]), src["origin"]) == (kind, sid, "curator")
        assert src["source_text"] == "Concept 0 work"
        assert _rows(cur, kind, sid) == {}  # no mapping until the vocabulary is accepted


def test_proposal_validation(client):
    sid, ids = _seed("capability")
    cap_url = _url("capability", sid, "/propose-vocabulary")
    assert client.post(cap_url, json={"canonical_name": "Concept 0"}).status_code == 409  # exists: map to it instead
    assert client.post(cap_url, json={"canonical_name": "X", "type_code": "tool"}).status_code == 400
    assert client.post(cap_url, json={"canonical_name": "  "}).status_code == 400
    # a capability proposal defaults to the capability type
    assert client.post(cap_url, json={"canonical_name": "Defaulted"}).status_code == 200
    with db.db_cursor() as cur:
        claim_sid = _subject(cur, "claim", "a claim")
    assert client.post(_url("claim", claim_sid, "/propose-vocabulary"), json={"canonical_name": "No type"}).status_code == 400
    assert client.post(_url("claim", claim_sid, "/propose-vocabulary"), json={"canonical_name": "T", "type_code": "nope"}).status_code == 400


# --- accepted proposal -> same canonical vocabulary, mapped back ------------

@BOTH
def test_accepted_profile360_proposal_creates_canonical_concept_and_maps_back_unreviewed(client, kind):
    sid, _ = _seed(kind)
    client.post(_url(kind, sid, "/propose-vocabulary"), json={
        "canonical_name": "Brand New Thing", "type_code": "capability", "definition": "A genuinely new idea."})

    r = client.post("/api/concepts/proposals/resolve", json={
        "surface_form": "brand new thing", "action": "accept_new", "type_code": "capability",
        "canonical_name": "Brand New Thing", "definition": "A genuinely new idea."})
    assert r.status_code == 200, r.text
    new_id = r.json()["resolved_concept_id"]

    with db.db_cursor() as cur:
        cur.execute("SELECT origin, status, definition FROM jobber.concept WHERE id = %s", (new_id,))
        concept = cur.fetchone()
        rows = _rows(cur, kind, sid)
    assert (concept["origin"], concept["status"]) == ("extraction_proposal", "active")  # same path as posting vocab
    assert (rows[new_id]["mapping_basis"], rows[new_id]["review_status"]) == ("curator_asserted", "unreviewed")

    wb = _workbench(client, kind, sid)
    assert wb["proposals"][0]["status"] == "accepted_new" and wb["proposals"][0]["resolved_concept_id"] == new_id
    # the new concept is now part of the searchable shared vocabulary
    assert client.get("/api/profile360/vocabulary/search", params={"q": "brand new", "kind": kind}).json()[0]["id"] == new_id

    # human review of the mapping is still required
    mid = wb["mappings"][0]["id"]
    client.post(f"/api/profile360/mappings/{mid}/review", json={"kind": kind, "action": "accept"})
    assert _workbench(client, kind, sid)["mapping_state"] == "mapped"


def test_profile360_proposal_shares_the_job_posting_proposal_queue_and_concept(client, monkeypatch):
    """A term already pending from job-posting extraction and the same term
    proposed from Profile360 are ONE concept_proposal; accepting it yields one
    jobber.concept and maps every Profile360 source."""
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, status) VALUES ('shared term', 3, 'pending') RETURNING id"
        )
        posting_proposal = cur.fetchone()["id"]
        claim_a, claim_b = _subject(cur, "claim", "first claim"), _subject(cur, "claim", "second claim")
    for sid in (claim_a, claim_b):
        r = client.post(_url("claim", sid, "/propose-vocabulary"), json={"canonical_name": "Shared Term", "type_code": "tool"})
        assert r.status_code == 200 and r.json()["proposal_id"] == str(posting_proposal)

    with db.db_cursor() as cur:
        cur.execute("SELECT occurrence_count FROM jobber.concept_proposal WHERE id = %s", (posting_proposal,))
        assert cur.fetchone()["occurrence_count"] == 5
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_proposal_profile360_source WHERE concept_proposal_id = %s", (posting_proposal,))
        assert cur.fetchone()["n"] == 2

    res = client.post("/api/concepts/proposals/resolve", json={
        "surface_form": "shared term", "action": "accept_new", "type_code": "tool", "canonical_name": "Shared Term"}).json()
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE LOWER(canonical_name) = 'shared term'")
        assert cur.fetchone()["n"] == 1
        assert res["resolved_concept_id"] in _rows(cur, "claim", claim_a) and res["resolved_concept_id"] in _rows(cur, "claim", claim_b)


def test_rejected_profile360_proposal_creates_no_vocabulary_or_mapping(client):
    sid, _ = _seed("claim")
    client.post(_url("claim", sid, "/propose-vocabulary"), json={"canonical_name": "Nope", "type_code": "tool"})
    client.post("/api/concepts/proposals/resolve", json={"surface_form": "nope", "action": "reject"})
    with db.db_cursor() as cur:
        assert _rows(cur, "claim", sid) == {}
        cur.execute("SELECT 1 FROM jobber.concept WHERE canonical_name = 'Nope'")
        assert cur.fetchone() is None
    assert _workbench(client, "claim", sid)["proposals"][0]["status"] == "rejected"


def test_capability_proposal_merged_into_non_capability_concept_does_not_map(client):
    with db.db_cursor() as cur:
        tool = _concept(cur, "Some Tool", type_code="tool")
        sid = _subject(cur, "capability", "cap")
    client.post(_url("capability", sid, "/propose-vocabulary"), json={"canonical_name": "Cap Thing"})
    client.post("/api/concepts/proposals/resolve", json={"surface_form": "cap thing", "action": "accept_alias", "concept_id": tool})
    with db.db_cursor() as cur:
        assert _rows(cur, "capability", sid) == {}


# --- AI re-suggestion semantics ---------------------------------------------

@BOTH
def test_repeat_ai_suggestion_does_not_reopen_a_rejected_mapping(client, monkeypatch, kind):
    sid, _ = _seed(kind)
    _ai_returns(monkeypatch, chosen_canonical_name="Concept 1")
    mid = client.post(_url(kind, sid, "/map")).json()["mapping_id"]
    client.post(f"/api/profile360/mappings/{mid}/review", json={"kind": kind, "action": "reject"})
    again = client.post(_url(kind, sid, "/map")).json()
    assert again["mapped"] is False and again["reason"] == "recommended_previously_rejected"
    assert _workbench(client, kind, sid)["mapping_state"] == "unmapped"


def test_workbench_404_for_unknown_item_and_bad_kind(client):
    import uuid
    assert client.get(_url("claim", str(uuid.uuid4()), "/mapping")).status_code == 404
    assert client.get("/api/profile360/widgets/x/mapping").status_code == 404
