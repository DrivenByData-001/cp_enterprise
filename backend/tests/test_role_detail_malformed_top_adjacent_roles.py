"""Durable Role Detail fix (cp_round_of_changes.md §A).

Production diagnosis found `legacy_analysis.top_adjacent_roles` stored as a
JSON *string* (e.g. `"[\\"Deputy Head of Actuarial Function\\", \\"Head of
Actuarial Function\\"]"`) rather than a genuine JSON array on 23 historical/
imported rows, including all three 2026 roles. RoleDetail.tsx assumes
`string[]` and calls `role.top_adjacent_roles.join(', ')`, which crashes when
the backend hands it a raw string instead. Production data has already been
corrected manually — these tests do NOT depend on that fix and never touch
production; they reproduce the malformed shape directly via
`db.upsert_role_instance` (same as test_persistence.py's own
`legacy_analysis` pattern) to prove the *application* can never be crashed
by a row shaped like this again, historical or freshly imported.

Covers both the unit-level normaliser (`db._normalize_top_adjacent_roles` /
`db.flatten_role_instance`) and the full `GET /api/roles/{id}` response, so
this is a guarantee about the actual wire contract, not just an internal
helper."""

from app import db


def _make_role(cur, *, legacy_analysis: dict, **extra_cols) -> str:
    columns = {
        "instance_type": "observed_posting",
        "title": "Senior Actuary",
        "description": "Works on Solvency II reporting.",
        "requirements": "Actuarial exams.",
        "responsibilities": "Board reporting.",
        "legacy_analysis": legacy_analysis,
        **extra_cols,
    }
    return db.upsert_role_instance(
        cur, None, columns,
        skills=[{"name": "Solvency II", "category": "knowledge", "requirement_type": "required"}],
    )


# --- unit level: db._normalize_top_adjacent_roles ---------------------------

def test_normalize_none_stays_none():
    assert db._normalize_top_adjacent_roles(None) is None


def test_normalize_proper_array_is_preserved():
    value = ["Deputy Head of Actuarial Function", "Head of Actuarial Function"]
    assert db._normalize_top_adjacent_roles(value) == value


def test_normalize_json_string_array_is_parsed():
    raw = '["Deputy Head of Actuarial Function", "Head of Actuarial Function"]'
    assert db._normalize_top_adjacent_roles(raw) == ["Deputy Head of Actuarial Function", "Head of Actuarial Function"]


def test_normalize_non_json_string_becomes_null():
    assert db._normalize_top_adjacent_roles("not valid json at all") is None


def test_normalize_json_object_string_becomes_null():
    assert db._normalize_top_adjacent_roles('{"a": 1}') is None


def test_normalize_json_array_of_non_strings_becomes_null():
    assert db._normalize_top_adjacent_roles("[1, 2, 3]") is None


def test_normalize_list_of_non_strings_becomes_null():
    assert db._normalize_top_adjacent_roles([1, 2, 3]) is None


def test_normalize_empty_string_becomes_null():
    assert db._normalize_top_adjacent_roles("") is None


def test_normalize_mixed_type_list_becomes_null():
    assert db._normalize_top_adjacent_roles(["Actuary", 5]) is None


# --- flatten_role_instance ----------------------------------------------------

def test_flatten_preserves_proper_array(client):
    with db.db_cursor() as cur:
        role_id = _make_role(cur, legacy_analysis={"top_adjacent_roles": ["Analyst", "Senior Analyst"]})
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (role_id,))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["top_adjacent_roles"] == ["Analyst", "Senior Analyst"]


def test_flatten_parses_double_encoded_json_string(client):
    with db.db_cursor() as cur:
        role_id = _make_role(
            cur,
            legacy_analysis={"top_adjacent_roles": '["Deputy Head of Actuarial Function", "Head of Actuarial Function"]'},
        )
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (role_id,))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["top_adjacent_roles"] == ["Deputy Head of Actuarial Function", "Head of Actuarial Function"]


def test_flatten_never_exposes_malformed_value(client):
    with db.db_cursor() as cur:
        role_id = _make_role(cur, legacy_analysis={"top_adjacent_roles": "not an array"})
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (role_id,))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["top_adjacent_roles"] is None


def test_flatten_leaves_other_legacy_analysis_fields_untouched(client):
    """The fix must be surgical — every other legacy_analysis-derived field
    (and everything else flatten_role_instance does) is unaffected by a
    malformed top_adjacent_roles sitting alongside it in the same JSONB blob."""
    with db.db_cursor() as cur:
        role_id = _make_role(
            cur,
            legacy_analysis={
                "top_adjacent_roles": "garbage, not json",
                "key_skills_summary": "Solvency II, ORSA",
                "summary": "A senior actuarial role.",
            },
        )
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (role_id,))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["top_adjacent_roles"] is None
    assert flat["key_skills_summary"] == "Solvency II, ORSA"
    assert flat["description"] == "Works on Solvency II reporting."
    assert flat["requirements"] == "Actuarial exams."
    assert flat["responsibilities"] == "Board reporting."


# --- full endpoint: GET /api/roles/{id} must never crash --------------------

def test_role_detail_endpoint_survives_malformed_top_adjacent_roles(client):
    with db.db_cursor() as cur:
        role_id = _make_role(
            cur,
            legacy_analysis={
                "top_adjacent_roles": '["Deputy Head of Actuarial Function", "Head of Actuarial Function"]',
            },
        )

    resp = client.get(f"/api/roles/{role_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_adjacent_roles"] == ["Deputy Head of Actuarial Function", "Head of Actuarial Function"]


def test_role_detail_endpoint_nulls_out_unparseable_string_instead_of_crashing(client):
    with db.db_cursor() as cur:
        role_id = _make_role(cur, legacy_analysis={"top_adjacent_roles": "Deputy Head of Actuarial Function"})

    resp = client.get(f"/api/roles/{role_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_adjacent_roles"] is None
    # The rest of the role's evidence is unaffected by the malformed field.
    assert body["description"] == "Works on Solvency II reporting."
    assert body["requirements"] == "Actuarial exams."
    assert body["responsibilities"] == "Board reporting."
    assert [s["name"] for s in body["skills"]] == ["Solvency II"]


def test_role_list_endpoint_also_survives_malformed_top_adjacent_roles(client):
    """GET /api/roles (used by Dashboard) also runs every row through
    flatten_role_instance — must not crash either."""
    with db.db_cursor() as cur:
        _make_role(cur, legacy_analysis={"top_adjacent_roles": "[not, valid, json]"})

    resp = client.get("/api/roles", params={"period": "all", "limit": 50})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_role_detail_endpoint_still_works_for_well_formed_legacy_row(client):
    """The pre-existing, correctly-shaped case (a genuine JSON array) must
    keep working unchanged — this fix must not regress the normal path."""
    with db.db_cursor() as cur:
        role_id = _make_role(cur, legacy_analysis={"top_adjacent_roles": ["Pricing Analyst", "Reserving Actuary"]})

    resp = client.get(f"/api/roles/{role_id}")
    assert resp.status_code == 200
    assert resp.json()["top_adjacent_roles"] == ["Pricing Analyst", "Reserving Actuary"]
