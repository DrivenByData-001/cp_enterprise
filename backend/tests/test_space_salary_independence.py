"""The 3D Career Space stays semantically driven and salary-independent
(build §6).

These are invariants, not features: Space positions roles by what they are
about, and financial navigation belongs in Pathways. A future change that
quietly folds compensation into the embedding text, or filters Space by
whether a role has a price on it, fails here.
"""

import uuid

from app import embeddings
from app.db import create_document, db_cursor, flatten_role_instance, upsert_role_instance


def _market(cur):
    cur.execute(
        "INSERT INTO jobber.market (code, label, country) VALUES ('country-united-kingdom', 'United Kingdom', "
        "'United Kingdom') ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label RETURNING id"
    )
    return str(cur.fetchone()["id"])


def _role(cur, *, title, text, salary_min=None, salary_max=None, currency=None):
    document_id, _dup = create_document(
        cur, kind="job_posting", content_text=text, provenance_quality="original",
        title=title, source="user_paste",
    )
    role_id = str(
        upsert_role_instance(
            cur, None,
            {"instance_type": "observed_posting", "title": title, "document_id": document_id,
             "description": text, "salary_min": salary_min, "salary_max": salary_max,
             "currency": currency, "country": "United Kingdom"},
            skills=[],
        )
    )
    # Embed through the same canonical composition every real write path
    # uses, so these tests exercise the actual text, not a stand-in.
    role = flatten_role_instance(
        {"id": role_id, "instance_type": "observed_posting", "target_basis": None, "title": title,
         "description": text, "salary_min": salary_min, "salary_max": salary_max, "currency": currency}
    )
    vector = embeddings.embed_text(embeddings.role_embedding_text(role))
    if vector:
        embeddings.set_embedding(cur, "role_instance", role_id, vector)
    return role_id


def test_embedding_text_contains_no_compensation_field():
    """`role_embedding_text` is the single canonical text every write path
    and the rebuild path agree on — so asserting here covers all of them."""
    role = {
        "node_type": "posting",
        "title": "Head of Capital",
        "description": "Lead the capital function.",
        "requirements": "Qualified actuary.",
        "responsibilities": "Own the ORSA.",
        "salary_min": 120000,
        "salary_max": 145000,
        "salary_estimate_min": 118000,
        "salary_estimate_max": 150000,
        "currency": "GBP",
    }
    text = embeddings.role_embedding_text(role)

    assert "Lead the capital function." in text
    for forbidden in ("120000", "145000", "118000", "150000", "GBP"):
        assert forbidden not in text


def test_two_identical_roles_with_different_salaries_embed_identically():
    with db_cursor() as cur:
        body = "Lead the capital function under Solvency II for a life insurer."
        cheap = _role(cur, title="Head of Capital", text=body, salary_min=80000, salary_max=90000, currency="GBP")
        rich = _role(cur, title="Head of Capital", text=body, salary_min=200000, salary_max=260000, currency="GBP")
        vectors = embeddings.get_embeddings(cur, "role_instance", [cheap, rich])

    assert vectors[cheap] == vectors[rich]


def test_accepting_compensation_evidence_does_not_move_a_role_in_space(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        role_id = _role(cur, title="Head of Capital", text="Lead the capital function.")
        before = embeddings.get_embedding(cur, "role_instance", role_id)

        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, role_instance_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, 'base', 'annual', 'GBP', 120000, 145000, 'posting_stated', 'accepted')
            """,
            (f"test:{uuid.uuid4()}", role_id, market_id),
        )
        after = embeddings.get_embedding(cur, "role_instance", role_id)

    assert before == after


def test_a_role_with_no_compensation_still_appears_in_space(client):
    with db_cursor() as cur:
        priced = _role(cur, title="Priced role", text="A role with a stated salary.",
                       salary_min=100000, salary_max=120000, currency="GBP")
        unpriced = _role(cur, title="Unpriced role", text="A role with no salary information at all.")

    body = client.get("/api/space?").json()
    ids = {point["id"] for point in body["points"]}
    assert priced in ids
    assert unpriced in ids


def test_space_response_carries_no_compensation_fields(client):
    with db_cursor() as cur:
        _role(cur, title="Priced role", text="A role with a stated salary.",
              salary_min=100000, salary_max=120000, currency="GBP")
        _role(cur, title="Another role", text="A second, quite different kind of role.")

    body = client.get("/api/space").json()
    assert body["points"], "expected at least one point"
    for point in body["points"]:
        assert not any(key.startswith("salary") for key in point)
        assert "compensation" not in point
        assert "reference_comp" not in point


def test_rebuild_embeds_source_text_only_never_the_salary_columns(client):
    """The rebuild path prefers the linked document's own verbatim capture
    over the composed fallback (see `embeddings.role_embedding_text`), so
    the two texts legitimately differ. What must hold either way is that
    neither carries a salary — a rebuilt role is positioned by the same
    source material, priced or not."""
    body = "Lead the capital function under Solvency II."
    with db_cursor() as cur:
        priced = _role(cur, title="Head of Capital", text=body,
                       salary_min=120000, salary_max=145000, currency="GBP")
        unpriced = _role(cur, title="Head of Capital", text=body)

    client.post("/api/space/rebuild-role-embeddings?force=true")

    with db_cursor() as cur:
        cur.execute(
            "SELECT d.content_text FROM jobber.role_instance ri "
            "JOIN jobber.document d ON d.id = ri.document_id WHERE ri.id = %s",
            (priced,),
        )
        source_text = cur.fetchone()["content_text"]
        # The two roles share identical source material and differ only in
        # their salary columns, so a rebuild must land them in the same place.
        assert embeddings.get_embedding(cur, "role_instance", priced) == embeddings.get_embedding(
            cur, "role_instance", unpriced
        )

    for forbidden in ("120000", "145000", "GBP"):
        assert forbidden not in source_text
